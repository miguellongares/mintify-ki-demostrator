from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from qdrant_client import QdrantClient

from app.config import settings
from app.dependencies import init_singletons
from app.proxy_guard import proxy_guard
from app.routers import (
    index,
    judge,
    knowledge_level,
    models,
    personal_docs,
    rag,
    retrieved_sources,
    tts,
)

_ready = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ready
    print("[RAG] Starting up...")

    qdrant_client = QdrantClient(url=settings.qdrant_url)
    init_singletons(qdrant_client)

    _ready = True
    print("[RAG] Ready.")

    yield

    print("[RAG] Shutting down.")


app = FastAPI(title="MINTIFY RAG Service", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Registered after CORS so it is the OUTERMOST layer: proxied requests to
# internal routes are rejected before anything else runs. See proxy_guard.py.
app.middleware("http")(proxy_guard)

app.include_router(models.router, prefix="/api")
app.include_router(rag.router, prefix="/api")
app.include_router(index.router, prefix="/api")
app.include_router(judge.router, prefix="/api")
app.include_router(tts.router, prefix="/api")
app.include_router(personal_docs.router, prefix="/api")
app.include_router(knowledge_level.router, prefix="/api")
app.include_router(retrieved_sources.router, prefix="/api")


@app.get("/api/health")
async def health():
    if not _ready:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="starting up")
    return {"status": "ok"}
