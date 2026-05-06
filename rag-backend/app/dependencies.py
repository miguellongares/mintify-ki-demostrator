from app.config import Settings, settings
from app.services.embedder import Embedder
from app.services.indexer import Indexer
from app.services.judge_client import JudgeClient
from app.services.llm_client import LLMClient
from app.services.retriever import Retriever

_embedder: Embedder | None = None
_retriever: Retriever | None = None
_indexer: Indexer | None = None
_llm_client: LLMClient | None = None
_judge_client: JudgeClient | None = None

#
def init_singletons(qdrant_client):
    global _embedder, _retriever, _indexer, _llm_client, _judge_client

    _embedder = Embedder(settings.embedding_model)
    _retriever = Retriever(qdrant_client, settings.collection_name)
    _indexer = Indexer(_embedder, qdrant_client, settings)

    _llm_client = LLMClient(settings.vllm_urls)
    _llm_client.refresh_model_map()
    # Must run after _embedder is set — startup() triggers PDF indexing which needs it.
    _indexer.startup()

    _judge_client = JudgeClient(settings.judge_vllm_url)


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
