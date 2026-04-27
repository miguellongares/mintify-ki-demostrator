"""
Retriever — semantic search in Qdrant
--------------------------------------
Takes a query vector (from Embedder.encode()) and returns the top-K most
semantically similar PDF chunks from the Qdrant collection.

How it works:
  Qdrant stores one vector per PDF chunk. When retrieve() is called, it computes
  cosine similarity between the query vector and every stored vector, then returns
  the top-K matches. Because both the query and the stored chunks were embedded with
  the same model (all-MiniLM-L6-v2), chunks semantically related to the question
  score higher than unrelated ones.

Context string format:
  Each retrieved chunk is formatted as:
    [Quelle: filename.pdf, Seite 3, Relevanz 0.842]
    <chunk text>
  Multiple chunks are joined with "\n\n---\n\n".
  This string is injected into the system prompt's {context} placeholder.
  The LLM is instructed to cite its sources using the "Quelle:" header.

Return value: tuple(context_string, hits_info_list)
  context_string — ready to inject into the system prompt
  hits_info_list — metadata dicts used for logging in rag.py (not sent to the LLM)

German fallback strings are returned when the collection is empty or on error,
because the system prompt is in German and the LLM is expected to respond in German.
"""

from qdrant_client import QdrantClient


class Retriever:
    def __init__(self, qdrant: QdrantClient, collection: str):
        self._qdrant = qdrant
        self._collection = collection

    def retrieve(self, query_vec: list[float], top_k: int) -> tuple[str, list[dict]]:
        """
        Search for the top-K most similar chunks to query_vec.

        Returns (context_string, hits_info) where:
          context_string — formatted text to inject into the system prompt
          hits_info      — list of dicts with source/page/score/text for logging
        """
        # First check the collection exists and has points — avoids a confusing
        # Qdrant error if no PDFs have been indexed yet.
        try:
            info = self._qdrant.get_collection(self._collection)
            n = info.points_count or 0
            print(f"[RAG] Qdrant collection '{self._collection}': {n} points")
        except Exception as e:
            print(f"[RAG] ERROR reading Qdrant collection: {e}")
            return "Fehler beim Zugriff auf den Wissensspeicher.", []

        if n == 0:
            # No documents indexed yet — return a German message that the LLM can
            # incorporate into its answer ("Noch keine Dokumente im Wissensspeicher.")
            print("[RAG] No documents in Qdrant — returning fallback.")
            return "Noch keine Dokumente im Wissensspeicher.", []

        try:
            # query_points() is the current Qdrant API for vector search.
            # Note: the older search() method was removed in qdrant-client >= 1.10.
            # limit=min(top_k, n) prevents requesting more results than exist.
            # with_payload=True returns the stored chunk text and metadata.
            result = self._qdrant.query_points(
                collection_name=self._collection,
                query=query_vec,
                limit=min(top_k, n),
                with_payload=True,
            )
            hits = result.points
        except Exception as e:
            print(f"[RAG] ERROR during Qdrant search: {e}")
            return f"Fehler bei der Vektorsuche: {e}", []

        if not hits:
            print("[RAG] Qdrant search returned 0 hits.")
            return "Keine relevanten Dokumente gefunden.", []

        hits_info = []
        parts = []
        for hit in hits:
            source = hit.payload.get("source", "unbekannt")
            page = hit.payload.get("page", "?")
            score = round(hit.score, 3)   # cosine similarity, 0.0–1.0
            text = hit.payload.get("text", "")
            # Collect metadata for logging in the router (not injected into the prompt).
            hits_info.append({"source": source, "page": page, "score": score, "text": text})
            # Format for the system prompt: citation header + chunk text.
            # The LLM is instructed to reference these citations in its answer.
            parts.append(f"[Quelle: {source}, Seite {page}, Relevanz {score}]\n{text}")

        # Join chunks with a visible separator so the LLM can distinguish between sources.
        return "\n\n---\n\n".join(parts), hits_info
