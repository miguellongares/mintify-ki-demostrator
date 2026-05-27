from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    vllm_base_urls: str = "http://host.docker.internal:8002"
    qdrant_url: str = "http://qdrant:6333"
    collection_name: str = "mintify_rag"
    embedding_model: str = "all-MiniLM-L6-v2"
    upload_dir: str = "/app/uploads"
    chunk_size: int = 800
    chunk_overlap: int = 100
    top_k: int = 5
    judge_vllm_url: str = "http://host.docker.internal:8002"
    tts_url: str = ""

    @property
    def vllm_urls(self) -> list[str]:
        return [u.strip() for u in self.vllm_base_urls.split(",") if u.strip()]

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
