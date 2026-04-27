"""
Configuration — environment-driven settings for mintify-rag-service
--------------------------------------------------------------------
All settings are read from environment variables (set in docker-compose.yml).
pydantic-settings handles the env var → Python type conversion and provides
a single `settings` object that every module imports.

Environment variables (set in docker-compose.yml under mintify-rag-service):
  VLLM_BASE_URLS  — comma-separated list of vLLM base URLs (without /v1)
  QDRANT_URL      — Qdrant REST API URL
  UPLOAD_DIR      — directory where PDF files are stored (bind-mounted from host)

All other settings use hardcoded defaults that match the rest of the system.
They can be overridden with env vars if needed (e.g. for a different deployment).
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Comma-separated vLLM base URLs. Each must be a running vLLM instance with an
    # OpenAI-compatible API. In production, these are SSH-tunnelled from RunPod:
    #   runpod-tunnel.sh forwards RunPod port 8000 → localhost:8002 (and :8004 for a second pod)
    # Docker containers reach the host via `host.docker.internal` (Docker Desktop magic).
    vllm_base_urls: str = "http://host.docker.internal:8002,http://host.docker.internal:8004"

    # Qdrant REST API. "qdrant" resolves via Docker internal DNS to the mintify-qdrant container.
    qdrant_url: str = "http://qdrant:6333"

    # Name of the Qdrant collection that stores PDF chunk embeddings.
    # Created automatically on first startup if it doesn't exist.
    collection_name: str = "mintify_rag"

    # SentenceTransformer model used to embed both PDF chunks (at index time)
    # and user queries (at query time). Must be the same model for both so that
    # the vector spaces match. all-MiniLM-L6-v2 produces 384-dimensional vectors
    # and is a good balance of speed and quality for semantic search.
    embedding_model: str = "all-MiniLM-L6-v2"

    # Directory where PDFs are stored inside the container.
    # Bind-mounted from ./data/uploads/ on the host (see docker-compose.yml).
    upload_dir: str = "/app/uploads"

    # Characters per chunk when splitting PDF page text. Larger chunks give more
    # context per retrieved hit but reduce the number of unique results.
    chunk_size: int = 800

    # Character overlap between consecutive chunks. Prevents a sentence from being
    # split right at the boundary between two chunks, which would make both
    # chunks less meaningful. Should be ~10-15% of chunk_size.
    chunk_overlap: int = 100

    # Default number of chunks to retrieve per query. Can be overridden per-request
    # via the top_k field in the pipe() request body (controlled by the TOP_K valve
    # in the pipeline's admin panel).
    top_k: int = 5

    @property
    def vllm_urls(self) -> list[str]:
        """Parse the comma-separated VLLM_BASE_URLS string into a clean list."""
        return [u.strip() for u in self.vllm_base_urls.split(",") if u.strip()]

    class Config:
        # Optional: read from a .env file if present (useful for local development
        # outside Docker). In production (Docker), env vars come from docker-compose.yml.
        env_file = ".env"
        # Ignore any extra env vars that don't correspond to a defined field.
        extra = "ignore"


# Module-level singleton — imported directly by other modules: `from app.config import settings`
settings = Settings()
