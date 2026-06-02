from app.config import Settings, settings
from app.services.embedder import Embedder
from app.services.indexer import Indexer
from app.services.judge_client import JudgeClient
from app.services.llm_client import LLMClient
from app.services.owui_client import OWUIClient
from app.services.retriever import Retriever
from app.services.tts_client import Qwen3TTSClient, TTSClient

_embedder: Embedder | None = None
_retriever: Retriever | None = None
_indexer: Indexer | None = None
_owui_client: OWUIClient | None = None
_llm_client: LLMClient | None = None
_judge_client: JudgeClient | None = None
_tts_client: TTSClient | None = None

#
def init_singletons(qdrant_client):
    global _embedder, _retriever, _indexer, _llm_client, _judge_client, _tts_client
    global _owui_client

    _embedder = Embedder(settings.embedding_model)
    _retriever = Retriever(qdrant_client, settings.collection_name)
    _owui_client = OWUIClient(settings.owui_base_url, settings.owui_api_key)
    _indexer = Indexer(_embedder, qdrant_client, settings, _owui_client)

    _llm_client = LLMClient(settings.vllm_urls)
    _llm_client.refresh_model_map()
    # Ensures the Qdrant collection exists and loads known files from it.
    # Does NOT call OWUI (it may not be up yet) — ingestion is driven by the
    # OWUI webhook (POST /api/index) and the /api/sync backfill.
    _indexer.startup()

    _judge_client = JudgeClient(settings.judge_vllm_url)

    if settings.tts_url:
        _tts_client = Qwen3TTSClient(settings.tts_url)
        print(f"[RAG] TTS client initialised → {settings.tts_url}")
    else:
        print("[RAG] TTS disabled (TTS_URL not set)")


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


def get_judge_client() -> JudgeClient:
    return _judge_client


def get_tts_client() -> TTSClient | None:
    return _tts_client
