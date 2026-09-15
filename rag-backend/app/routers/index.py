"""
Index endpoints — keep Qdrant in sync with OWUI's files

POST   /api/index         — webhook: OWUI calls this after a file is uploaded or
                            its content is updated. Body {file_id}. The indexer
                            then pulls the extracted text from OWUI and embeds it.
POST   /api/sync          — backfill: (re)index every file OWUI knows about.
                            Use once after migration or to repair drift.
DELETE /api/index/{id}    — remove all Qdrant points for a file (OWUI calls this
                            on delete; fills the gap OWUI's own cleanup leaves).
POST   /api/reconcile     — drop Qdrant points whose owui_file_id no longer exists
                            in OWUI (safety net against drift).
POST   /api/reset-collection — wipe + recreate the collection and clear the indexer
                            cache. Used by scripts/reset_rag.sh for a clean state.
POST   /api/reindex-all   — admin one-shot: sync + personal-docs resync + reconcile.
                            Backs the "Reindex" button in OWUI Admin Panel →
                            Settings → Documents, so re-indexing after a chunker
                            or embedding change is a button, not three curls.
"""

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.dependencies import (
    get_extractor,
    get_indexer,
    get_owui_auth,
    get_personal_docs,
)
from app.services.extractor import Extractor
from app.services.indexer import Indexer
from app.services.owui_auth import OwuiAuth
from app.services.personal_docs import PersonalDocsStore, resync_all

router = APIRouter()


def require_admin(
    authorization: str | None = Header(None),
    auth: OwuiAuth = Depends(get_owui_auth),
) -> dict:
    """Same OWUI-token check as the personal-docs endpoints, restricted to admins.
    Re-indexing touches every user's documents, so identity stays in OWUI."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    user = auth.resolve_user(authorization.split(" ", 1)[1])
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid OWUI token")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


class IndexRequest(BaseModel):
    file_id: str


@router.post("/index")
def index_file(req: IndexRequest, indexer: Indexer = Depends(get_indexer)):
    print(f"[RAG] /index webhook for file_id={req.file_id}", flush=True)
    chunks = indexer.index_file_id(req.file_id)
    return {"file_id": req.file_id, "chunks": chunks}


@router.post("/sync")
def sync_all(indexer: Indexer = Depends(get_indexer)):
    print("[RAG] /sync — backfilling all OWUI files", flush=True)
    return indexer.sync_all()


@router.delete("/index/{file_id}")
def delete_file(file_id: str, indexer: Indexer = Depends(get_indexer)):
    indexer.delete_file_id(file_id)
    return {"file_id": file_id, "deleted": True}


@router.post("/reconcile")
def reconcile(indexer: Indexer = Depends(get_indexer)):
    print("[RAG] /reconcile — purging Qdrant points for files gone from OWUI", flush=True)
    return indexer.reconcile()


@router.post("/reset-collection")
def reset_collection(indexer: Indexer = Depends(get_indexer)):
    print("[RAG] /reset-collection — wiping the collection", flush=True)
    return indexer.reset_collection()


@router.post("/reindex-all")
def reindex_all(
    user: dict = Depends(require_admin),
    indexer: Indexer = Depends(get_indexer),
    store: PersonalDocsStore = Depends(get_personal_docs),
    extractor: Extractor = Depends(get_extractor),
):
    """Re-index everything: OWUI files, personal files, then drop orphans.

    Order matters — reconcile() runs last so it only ever purges points whose
    file really is gone from OWUI, never ones sync_all() was about to refresh.

    Neither step re-extracts text that already exists (OWUI serves its stored
    content, personal docs reuse text_content), so this works with docling
    offline — which is exactly the situation after a chunker change.
    """
    print(f"[RAG] /reindex-all requested by {user.get('email')}", flush=True)
    owui = indexer.sync_all()
    personal = resync_all(store, indexer, extractor)
    orphans = indexer.reconcile()
    result = {
        "owui": owui,
        "personal": personal,
        "orphans_deleted": len(orphans.get("orphans_deleted", [])),
        "chunks": owui.get("chunks", 0) + personal.get("chunks", 0),
    }
    print(f"[RAG] /reindex-all done: {result}", flush=True)
    return result
