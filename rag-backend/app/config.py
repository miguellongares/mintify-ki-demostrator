from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    vllm_base_urls: str = "http://host.docker.internal:8002"
    qdrant_url: str = "http://qdrant:6333"
    collection_name: str = "mintify_rag"
    embedding_model: str = "all-MiniLM-L6-v2"
    chunk_size: int = 800
    chunk_overlap: int = 100
    top_k: int = 5
    judge_vllm_url: str = "http://host.docker.internal:8002"
    tts_url: str = ""
    # OWUI is the single source of extracted document text. The indexer pulls a
    # file's already-extracted content from OWUI's REST API (by file id) and
    # embeds it into Qdrant — for ANY file type, not just PDFs.
    owui_base_url: str = "http://open-webui:8080"
    # Bearer token for the OWUI API. Create an admin API key in OWUI
    # (Settings → Account → API Keys) and set it here so the indexer can read
    # any user's file content. Empty → indexing is effectively disabled.
    owui_api_key: str = ""

    # --- Web search (backend-owned: SearXNG search + trafilatura extraction) ---
    # The pipeline forwards web_search=True + the query when the user enables 🌐.
    # We query SearXNG, fetch the top-N pages, and extract clean article text.
    searxng_url: str = "http://searxng:8080"
    web_search_results: int = 4          # top-N SearXNG hits to fetch
    web_fetch_timeout: float = 8.0       # seconds per SearXNG query / page fetch
    web_doc_char_cap: int = 4000         # max chars kept per page
    web_total_char_cap: int = 16000      # max chars across all pages
    web_min_doc_chars: int = 120         # below this → fall back to SearXNG snippet
    web_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )

    @property
    def vllm_urls(self) -> list[str]:
        return [u.strip() for u in self.vllm_base_urls.split(",") if u.strip()]

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
