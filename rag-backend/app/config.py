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

    @property
    def vllm_urls(self) -> list[str]:
        return [u.strip() for u in self.vllm_base_urls.split(",") if u.strip()]

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
