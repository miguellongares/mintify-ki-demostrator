"""
Embedder — converts text into dense vector representations
-----------------------------------------------------------
Used in two places:
  1. At indexing time (Indexer._index_file): each PDF chunk is embedded and stored in Qdrant.
  2. At query time (rag.py pipe()): the user's question is embedded for similarity search.

The SAME model must be used for both operations. Embedding with a different model
would produce vectors in a different space, making cosine similarity meaningless.

Model: all-MiniLM-L6-v2
  - Produces 384-dimensional float vectors (hence `VectorParams(size=384)` in Indexer)
  - Fast CPU inference (~5ms per sentence), good for semantic search tasks
  - Downloaded automatically from HuggingFace on first startup (~90 MB, cached at
    ~/.cache/huggingface/ inside the container; lost on rebuild unless a volume is added)
"""

from sentence_transformers import SentenceTransformer


class Embedder:
    def __init__(self, model_name: str):
        # SentenceTransformer downloads the model from HuggingFace Hub on first use.
        # On subsequent container starts, it loads from the local cache (~1-3s).
        print(f"[RAG] Loading embedding model: {model_name}")
        self._model = SentenceTransformer(model_name)
        print("[RAG] Embedding model ready.")

    def encode(self, text: str) -> list[float]:
        """
        Embed a single string into a 384-dim float vector.
        Returns a plain Python list (not a numpy array) because Qdrant's Python client
        and JSON serialization both handle lists natively.
        """
        # .tolist() converts the numpy array returned by encode() to a Python list.
        return self._model.encode(text).tolist()
