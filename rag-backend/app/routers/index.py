"""
Index endpoints — keep Qdrant in sync with OWUI's files

POST   /api/index         — webhook: OWUI calls this after a file is uploaded or
                            its content is updated. Body {file_id}. The indexer
                            then pulls the extracted text from OWUI and embeds it.
POST   /api/sync          — backfill: (re)index every file OWUI knows about.
                            Use once after migration or to repair drift.
DELETE /api/index/{id}    — remove all Qdrant points for a file (OWUI calls this
                            on delete; fills the gap OWUI's own cleanup leaves).
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.dependencies import get_indexer
from app.services.indexer import Indexer

router = APIRouter()


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
