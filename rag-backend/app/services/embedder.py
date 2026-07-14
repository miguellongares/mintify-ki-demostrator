"""
Embedder — converts text into dense vector representations
-----------------------------------------------------------
Used in two places:
  1. At indexing time (Indexer._index_record): each document chunk is embedded
     via encode_passage() and stored in Qdrant.
  2. At query time (rag.py pipe()): the user's question is embedded via
     encode_query() for similarity search.

The SAME model AND the same prefix convention must be used for both operations.
Embedding with a different model (or swapping/omitting the prefixes on an
asymmetric model) would produce vectors in a different space, making cosine
similarity meaningless.

Model: intfloat/multilingual-e5-small (asymmetric — needs "query: " /
"passage: " prefixes, configured in config.py).
"""

from sentence_transformers import SentenceTransformer


class Embedder:
    def __init__(self, model_name: str, query_prefix: str = "", passage_prefix: str = ""):
        print(f"[RAG] Loading embedding model: {model_name}")
        self._model = SentenceTransformer(model_name)
        self._query_prefix = query_prefix
        self._passage_prefix = passage_prefix
        print("[RAG] Embedding model ready.")

    @property
    def dim(self) -> int:
        """Output dimension of the model — the Qdrant collection must match it."""
        return self._model.get_sentence_embedding_dimension()

    def encode_query(self, text: str) -> list[float]:
        """Embed a user question (search side of the asymmetric pair)."""
        return self._encode(self._query_prefix + text)

    def encode_passage(self, text: str) -> list[float]:
        """Embed a document chunk (index side of the asymmetric pair)."""
        return self._encode(self._passage_prefix + text)

    def _encode(self, text: str) -> list[float]:
        # .tolist() converts the numpy array returned by encode() to a Python list
        # because Qdrant's client and JSON serialization both handle lists natively.
        return self._model.encode(text).tolist()
