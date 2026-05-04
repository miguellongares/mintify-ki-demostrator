import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from qdrant_client import QdrantClient

from app.config import settings
from app.dependencies import init_singletons
from app.routers import models, rag

_ready = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ready
    print("[RAG] Starting up...")

    qdrant_client = QdrantClient(url=settings.qdrant_url)
    os.makedirs(settings.upload_dir, exist_ok=True)
    init_singletons(qdrant_client)

    _ready = True
    print("[RAG] Ready.")

    yield

    print("[RAG] Shutting down.")


app = FastAPI(title="MINTIFY RAG Service", lifespan=lifespan)

app.include_router(models.router, prefix="/api")
app.include_router(rag.router, prefix="/api")


@app.get("/api/health")
async def health():
    if not _ready:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="starting up")
    return {"status": "ok"}
