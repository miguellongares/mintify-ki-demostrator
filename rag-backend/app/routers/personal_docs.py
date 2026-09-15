"""
Personal documents — per-user drag & drop file storage (sidebar feature).

Called directly from the browser (like /api/judge): dev → localhost:8088,
production → /rag-api/* via Caddy. Every request must carry the caller's OWUI
token as a Bearer header; the user id resolved from it scopes all queries.
"""

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.dependencies import get_extractor, get_indexer, get_owui_auth, get_personal_docs
from app.services.extractor import Extractor
from app.services.indexer import Indexer
from app.services.owui_auth import OwuiAuth
from app.services.personal_docs import PersonalDocsStore, resync_all

router = APIRouter()

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB per file


def require_user(
    authorization: str | None = Header(None),
    auth: OwuiAuth = Depends(get_owui_auth),
) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    user = auth.resolve_user(authorization.split(" ", 1)[1])
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid OWUI token")
    return user


@router.post("/personal-docs")
async def upload_document(
    file: UploadFile = File(...),
    user: dict = Depends(require_user),
    store: PersonalDocsStore = Depends(get_personal_docs),
    indexer: Indexer = Depends(get_indexer),
    extractor: Extractor = Depends(get_extractor),
):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File larger than 50 MB")

    filename = file.filename or "unbenannt"
    # Extract text the same way OWUI does for knowledge-base files (docling,
    # with a plain-text bypass) and index it into Qdrant scoped to this user.
    # If extraction yields nothing (unsupported type / docling down), the file
    # is stored and downloadable but not searchable — /sync can backfill.
    text = extractor.extract(filename, file.content_type, data)

    record = store.add(
        user_id=user["id"],
        filename=filename,
        content_type=file.content_type,
        data=data,
        text_content=text,
    )
    chunks = indexer.index_personal_doc(
        document_id=record["document_id"],
        user_id=user["id"],
        filename=filename,
        text=text,
    )
    record["indexed_chunks"] = chunks
    print(
        f"[PersonalDocs] user={user['id']} uploaded {record['filename']!r}"
        f" ({record['size_bytes']} bytes, {len(text)} chars extracted,"
        f" {chunks} chunks indexed)",
        flush=True,
    )
    return record


@router.get("/personal-docs")
async def list_documents(
    user: dict = Depends(require_user),
    store: PersonalDocsStore = Depends(get_personal_docs),
):
    return {"documents": store.list(user["id"])}


@router.get("/personal-docs/{document_id}/download")
async def download_document(
    document_id: str,
    user: dict = Depends(require_user),
    store: PersonalDocsStore = Depends(get_personal_docs),
):
    result = store.get(user["id"], document_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Document not found")
    meta, path = result
    return FileResponse(
        path,
        filename=meta["filename"],
        media_type=meta["content_type"] or "application/octet-stream",
    )


@router.delete("/personal-docs/{document_id}")
async def delete_document(
    document_id: str,
    user: dict = Depends(require_user),
    store: PersonalDocsStore = Depends(get_personal_docs),
    indexer: Indexer = Depends(get_indexer),
):
    if not store.delete(user["id"], document_id):
        raise HTTPException(status_code=404, detail="Document not found")
    # Drop the RAG chunks too, so a deleted file can never resurface in answers.
    indexer.delete_personal_doc(document_id)
    print(f"[PersonalDocs] user={user['id']} deleted {document_id}", flush=True)
    return {"deleted": document_id}


@router.post("/personal-docs/sync")
async def sync_documents(
    user: dict = Depends(require_user),
    store: PersonalDocsStore = Depends(get_personal_docs),
    indexer: Indexer = Depends(get_indexer),
    extractor: Extractor = Depends(get_extractor),
):
    """Backfill/repair (admin only): re-extract text for documents uploaded
    before extraction existed (or while docling was down) and (re)index every
    personal document. Same work as the personal-docs part of /api/reindex-all."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    return resync_all(store, indexer, extractor)
