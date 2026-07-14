"""
SparseEmbedder — BM25 term-weight vectors for the lexical arm of hybrid search
-------------------------------------------------------------------------------
Dense embeddings (Embedder) capture meaning but fail on exact-term lookups in
OCR'd form text ("HSN/TSN", "FIN", contract numbers). This encoder produces
Qdrant sparse vectors via fastembed's Bm25 model (tokenizer + Snowball stemmer
+ stopwords for the configured language). Term IDF is NOT computed here — the
Qdrant collection is created with Modifier.IDF, so the server weights terms by
corpus statistics at query time.

Mirrors Embedder's interface: encode_passage() at indexing time (BM25 term
frequencies), encode_query() at query time (raw query terms).
"""

from fastembed import SparseTextEmbedding
from qdrant_client.models import SparseVector


class SparseEmbedder:
    def __init__(self, language: str = "german"):
        print(f"[RAG] Loading BM25 sparse encoder (Qdrant/bm25, language={language})")
        self._model = SparseTextEmbedding(model_name="Qdrant/bm25", language=language)
        print("[RAG] BM25 sparse encoder ready.")

    def encode_passage(self, text: str) -> SparseVector:
        """Encode a document chunk (BM25 term-frequency weights)."""
        emb = next(iter(self._model.embed([text])))
        return SparseVector(indices=emb.indices.tolist(), values=emb.values.tolist())

    def encode_query(self, text: str) -> SparseVector:
        """Encode a search query (query terms, weight 1 — IDF applied server-side)."""
        emb = next(iter(self._model.query_embed(text)))
        return SparseVector(indices=emb.indices.tolist(), values=emb.values.tolist())
