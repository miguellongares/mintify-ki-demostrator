"""
Dependency injection — service singletons for FastAPI
------------------------------------------------------
FastAPI's dependency injection system (Depends()) calls getter functions like
get_embedder() on every request. Because these getters return module-level
singletons (not new instances), the heavy objects (embedding model, Qdrant client,
etc.) are initialised exactly once at startup and reused across all requests.

Startup order inside init_singletons():
  1. Embedder     — downloads and loads the SentenceTransformer model (~100 MB).
                    Must be first because Indexer depends on it.
  2. Retriever    — wraps the QdrantClient for search queries. Lightweight.
  3. Indexer      — wraps Embedder + QdrantClient. Needs Embedder to be ready
                    before calling startup() which triggers PDF indexing.
  4. LLMClient    — connects to vLLM endpoints. refresh_model_map() is called
                    immediately but failures are non-fatal (vLLM may not be up yet;
                    the map is refreshed lazily on the first pipe() call).
  5. indexer.startup() — creates the Qdrant collection if needed, loads known hashes
                    from existing Qdrant points to avoid re-embedding on restart,
                    then indexes any PDFs found in /app/uploads.
"""

from app.config import Settings, settings
from app.services.embedder import Embedder
from app.services.indexer import Indexer
from app.services.llm_client import LLMClient
from app.services.retriever import Retriever

# Module-level singletons — None until init_singletons() is called during lifespan startup.
# Using module globals (rather than a container class) keeps the getter functions simple
# and avoids circular import issues between routers and services.
_embedder: Embedder | None = None
_retriever: Retriever | None = None
_indexer: Indexer | None = None
_llm_client: LLMClient | None = None


def init_singletons(qdrant_client):
    """
    Called once from main.py's lifespan context manager during application startup.
    Accepts the QdrantClient created in main.py so all services share the same instance.
    """
    global _embedder, _retriever, _indexer, _llm_client

    # Load the sentence-transformer embedding model into memory.
    # This is the slowest step (~10-30s depending on download/cache state).
    _embedder = Embedder(settings.embedding_model)

    # Wrap the shared QdrantClient with a collection-aware search interface.
    _retriever = Retriever(qdrant_client, settings.collection_name)

    # Indexer needs the embedder to vectorise PDF chunks and the Qdrant client to store them.
    _indexer = Indexer(_embedder, qdrant_client, settings)

    # Build the model→URL routing map. Best-effort — if vLLM isn't running yet,
    # this logs a warning and the map stays empty. It refreshes on the next /api/models call.
    _llm_client = LLMClient(settings.vllm_urls)
    _llm_client.refresh_model_map()

    # Run startup tasks: ensure collection exists, load known hashes, index PDFs.
    # Must be after _embedder and _llm_client are set because startup() embeds chunks.
    _indexer.startup()


# ---- FastAPI dependency getters ----
# These are passed as `Depends(get_X)` in router function signatures.
# FastAPI calls them on every request and injects the return value as a parameter.
# Because they return module globals, no new objects are created per request.

def get_settings() -> Settings:
    return settings


def get_embedder() -> Embedder:
    return _embedder


def get_retriever() -> Retriever:
    return _retriever


def get_indexer() -> Indexer:
    return _indexer


def get_llm_client() -> LLMClient:
    return _llm_client
