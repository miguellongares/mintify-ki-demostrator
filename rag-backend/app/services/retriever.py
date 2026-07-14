"""
Retriever — semantic search in Qdrant
--------------------------------------
Takes a query vector (from Embedder.encode_query()) and returns the top-K most
semantically similar chunks from the Qdrant collection, dropping hits below
the min_score relevance floor.

"""

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue


class Retriever:
    def __init__(self, qdrant: QdrantClient, collection: str, min_score: float = 0.0):
        self._qdrant = qdrant
        self._collection = collection
        # Hits below this cosine similarity are dropped instead of injected
        # into the prompt. 0 disables the filter.
        self._min_score = min_score

    def retrieve(
        self,
        query_vec: list[float],
        top_k: int,
        file_ids: list[str] | None = None,
        user_id: str | None = None,
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

        # STRICT scoping: retrieve ONLY from
        #   (a) knowledge the user explicitly attached — the model's knowledge base
        #       AND files dropped in THIS chat (their ids arrive in file_ids), and
        #   (b) the calling user's OWN personal files (sidebar feature) — their
        #       Qdrant points carry user_id, OWUI points don't, so matching
        #       user_id == <caller> can never surface another user's documents.
        # Neither attachment nor user id → retrieve nothing, so the model answers
        # on its own. We never search the whole index (that leaked files dropped
        # in one chat into other chats).
        should = []
        if file_ids:
            should.append(FieldCondition(key="owui_file_id", match=MatchAny(any=file_ids)))
        if user_id:
            should.append(FieldCondition(key="user_id", match=MatchValue(value=user_id)))
        if not should:
            print("[RAG] No knowledge attached and no user id — skipping retrieval.")
            return "", []
        query_filter = Filter(should=should)
        print(
            f"[RAG] Filtering Qdrant by owui_file_id ∈ {file_ids or []} "
            f"OR personal docs of user_id={user_id}"
        )

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

        # Relevance floor: drop hits below min_score so truly irrelevant chunks
        # are never injected into the prompt. Log the dropped scores — they're
        # the data for tuning MIN_SCORE.
        if self._min_score > 0 and hits:
            kept = [h for h in hits if h.score >= self._min_score]
            dropped = [h for h in hits if h.score < self._min_score]
            if dropped:
                print(
                    f"[RAG] min_score={self._min_score}: kept {len(kept)}, "
                    f"dropped {len(dropped)} "
                    f"(scores: {[round(h.score, 3) for h in dropped]})"
                )
            hits = kept

        if not hits:
            print("[RAG] No hits at or above the relevance threshold.")
            if file_ids:
                return "Keine relevanten Dokumente gefunden.", []
            # Personal-docs-only search and the user simply has none indexed —
            # behave like "nothing attached" (model-only) instead of injecting
            # a misleading "no documents found" context.
            return "", []

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
