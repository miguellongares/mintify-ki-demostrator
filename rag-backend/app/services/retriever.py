"""
Retriever — semantic search in Qdrant
--------------------------------------
Takes a query vector (from Embedder.encode()) and returns the top-K most
semantically similar PDF chunks from the Qdrant collection.

"""

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny


class Retriever:
    def __init__(self, qdrant: QdrantClient, collection: str):
        self._qdrant = qdrant
        self._collection = collection

    def retrieve(
        self,
        query_vec: list[float],
        top_k: int,
        file_ids: list[str] | None = None,
    ) -> tuple[str, list[dict]]:
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

        # STRICT scoping: retrieve ONLY from knowledge the user explicitly attached —
        # the model's knowledge base AND files dropped in THIS chat (their ids arrive
        # in file_ids). No attachment (None OR empty) → retrieve nothing, so the model
        # answers on its own. This prevents a file dropped in one chat from surfacing
        # in another: the old `None → search the whole index` path leaked across chats
        # whenever no KB was attached.
        if not file_ids:
            print("[RAG] No knowledge attached — skipping retrieval (model-only).")
            return "", []
        query_filter = Filter(
            must=[FieldCondition(key="owui_file_id", match=MatchAny(any=file_ids))]
        )
        print(f"[RAG] Filtering Qdrant by owui_file_id ∈ {file_ids}")

        try:
            result = self._qdrant.query_points(
                collection_name=self._collection,
                query=query_vec,
                limit=min(top_k, n),
                with_payload=True,
                query_filter=query_filter,
            )
            hits = result.points
        except Exception as e:
            print(f"[RAG] ERROR during Qdrant search: {e}")
            return f"Fehler bei der Vektorsuche: {e}", []
        
        # This part of the code should be keep, then it would be wise to have a mechanism
        # where the mechanism doesn't find any relevant documents
        # TODO: replace this empty check with a score threshold (e.g. filter out hits below 0.3)                                          
        # so that truly irrelevant chunks are not injected into the prompt.
        if not hits:
            print("[RAG] Qdrant search returned 0 hits.")
            return "Keine relevanten Dokumente gefunden.", []

        hits_info = []
        parts = []
        for hit in hits:
            source = hit.payload.get("source", "unbekannt")
            # OWUI-extracted text is a single blob → no page numbers. Older PDF
            # points may still carry "page"; show it when present.
            page = hit.payload.get("page")
            score = round(hit.score, 3)   # cosine similarity, 0.0–1.0
            text = hit.payload.get("text", "")
            # Collect metadata for logging in the router (not injected into the prompt).
            hits_info.append({"source": source, "page": page, "score": score, "text": text})
            # Format for the system prompt: citation header + chunk text.
            # The LLM is instructed to reference these citations in its answer.
            if page is not None:
                parts.append(f"[Quelle: {source}, Seite {page}, Relevanz {score}]\n{text}")
            else:
                parts.append(f"[Quelle: {source}, Relevanz {score}]\n{text}")

        # Join chunks with a visible separator so the LLM can distinguish between sources.
        return "\n\n---\n\n".join(parts), hits_info
