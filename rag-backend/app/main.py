"""
mintify-rag-service — FastAPI application entry point
------------------------------------------------------
This container (mintify-rag-service, port 8080) holds ALL the RAG and ML logic for
the MINTIFY system. The Pipelines container (mintify-pipelines, port 9099) is a thin
proxy that delegates every real operation here.

Responsibilities of this service:
  - Load the sentence-transformer embedding model at startup
  - Connect to Qdrant and create the vector collection if it doesn't exist
  - Index all PDFs in /app/uploads on startup (and detect new ones at query time)
  - Serve GET /api/models  → query vLLM endpoints and return available model IDs
  - Serve POST /api/pipe   → full RAG pipeline: embed → retrieve → enrich → stream
  - Serve GET /api/health  → readiness probe used by docker-compose healthcheck

Startup is handled via FastAPI's lifespan context manager (replaces the deprecated
@app.on_event("startup") pattern). All heavy initialisation (model loading, PDF
indexing) happens inside the lifespan so the /api/health endpoint stays at 503 until
everything is ready. The pipelines container waits for this health check before starting.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from qdrant_client import QdrantClient

from app.config import settings
from app.dependencies import init_singletons
from app.routers import models, rag

# Global readiness flag — False during startup, True once init_singletons() completes.
# The /api/health endpoint returns 503 while this is False so docker-compose's
# healthcheck keeps the pipelines container waiting until we're fully ready.
_ready = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs once at startup (before the first request) and once at shutdown.
    Anything before `yield` is startup; anything after is teardown.

    Startup sequence:
      1. Connect to Qdrant (just a client object — no network call yet)
      2. Ensure /app/uploads directory exists (bind-mounted from ./data/uploads/)
      3. init_singletons(): load embedding model, connect retriever, scan PDFs,
         query vLLM endpoints for available models
      4. Set _ready = True so /api/health starts returning 200
    """
    global _ready
    print("[RAG] Starting up...")

    # QdrantClient connects lazily — the URL is validated here but no requests
    # are made until the first actual query. The hostname "qdrant" resolves via
    # Docker's internal DNS (container_name: mintify-qdrant in docker-compose.yml).
    qdrant_client = QdrantClient(url=settings.qdrant_url)

    # Ensure the upload directory exists. On first boot the bind-mount may be empty.
    os.makedirs(settings.upload_dir, exist_ok=True)

    # Initialise all service singletons (embedder, retriever, indexer, llm_client).
    # This is where the heavy work happens: SentenceTransformer download + load,
    # Qdrant collection creation, PDF indexing.
    init_singletons(qdrant_client)

    # Signal readiness — /api/health will now return 200, unblocking the pipelines container.
    _ready = True
    print("[RAG] Ready.")

    yield  # application runs here, serving requests

    print("[RAG] Shutting down.")


# Create the FastAPI app with the lifespan handler attached.
app = FastAPI(title="MINTIFY RAG Service", lifespan=lifespan)

# Register route groups:
#   /api/models  → app/routers/models.py  (GET — model discovery for the pipeline)
#   /api/pipe    → app/routers/rag.py     (POST — main RAG inference endpoint)
app.include_router(models.router, prefix="/api")
app.include_router(rag.router, prefix="/api")


@app.get("/api/health")
async def health():
    """
    Readiness probe consumed by docker-compose's healthcheck on mintify-rag-service.
    Returns 200 {"status": "ok"} only after startup completes.
    Returns 503 while still initialising so the pipelines container keeps waiting.
    The pipelines service has `depends_on: mintify-rag-service: condition: service_healthy`
    which means Docker won't start it until this endpoint returns 200.
    """
    if not _ready:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="starting up")
    return {"status": "ok"}
