"""
MINTIFY RAG Pipeline
--------------------
Manifold pipeline: all models currently loaded in vLLM appear as separate entries
in Open Web UI's chat dropdown (e.g. "MINTIFY – Qwen/Qwen2.5-3B-Instruct").

on_startup() — loads embedding model, connects to Qdrant & vLLM, indexes existing PDFs
pipelines()   — method; queries vLLM /v1/models live so the list stays current
pipe()        — called per message: retrieves context, streams enriched response
"""

import os
import glob
import hashlib
from typing import Generator, Union

from pydantic import BaseModel


class Pipeline:
    class Valves(BaseModel):
        VLLM_BASE_URL: str = os.getenv("VLLM_BASE_URL", "http://host.docker.internal:8000")
        EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
        QDRANT_URL: str = os.getenv("QDRANT_URL", "http://qdrant:6333")
        COLLECTION_NAME: str = "mintify_rag"
        TOP_K: int = 5
        CHUNK_SIZE: int = 800
        CHUNK_OVERLAP: int = 100
        UPLOAD_DIR: str = "/app/uploads"
        SYSTEM_PROMPT: str = (
            "Du bist ein hilfreicher KI-Assistent für finanzielle Bildung, entwickelt im Rahmen "
            "des MINTIFY-Forschungsprojekts der Hochschule Bonn-Rhein-Sieg.\n\n"
            "Beantworte alle Fragen ausschließlich auf Deutsch, klar und verständlich.\n\n"
            "Nutze den folgenden Kontext aus den hinterlegten Dokumenten, um die Frage des Nutzers "
            "zu beantworten:\n\n"
            "[KONTEXT]\n{context}\n[/KONTEXT]\n\n"
            "Falls die Antwort nicht im Kontext zu finden ist, weise ehrlich darauf hin und "
            "beantworte die Frage trotzdem so gut wie möglich auf Basis deines Wissens. "
            "Gib bei Verwendung von Kontextinformationen stets die Quelldokumente an."
        )

    def __init__(self):
        self.id = "mintify_pipeline"
        self.name = "MINTIFY"
        self.type = "manifold"
        self.valves = self.Valves(**self._load_valves())
        self.embedder = None
        self.qdrant = None
        self.llm_client = None
        self._indexed_hashes: set = set()

    def pipelines(self) -> list:
        """Returns one entry per model currently loaded in vLLM."""
        try:
            import httpx
            resp = httpx.get(f"{self.valves.VLLM_BASE_URL}/v1/models", timeout=5)
            resp.raise_for_status()
            models = resp.json().get("data", [])
            return [{"id": m["id"].replace("/", "__"), "name": f" – {m['id']}"} for m in models]
        except Exception as e:
            print(f"[MINTIFY] Could not fetch models from vLLM: {e}")
            return [{"id": "unavailable", "name": "vLLM unavailable"}]

    def _load_valves(self) -> dict:
        import json
        path = os.path.join(os.path.dirname(__file__), "mintify_pipeline", "valves.json")
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return {}

    async def on_startup(self):
        from sentence_transformers import SentenceTransformer
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams
        from openai import OpenAI

        print("[MINTIFY] Loading embedding model...")
        self.embedder = SentenceTransformer(self.valves.EMBEDDING_MODEL)

        print("[MINTIFY] Connecting to Qdrant...")
        self.qdrant = QdrantClient(url=self.valves.QDRANT_URL)

        existing = [c.name for c in self.qdrant.get_collections().collections]
        if self.valves.COLLECTION_NAME not in existing:
            self.qdrant.create_collection(
                collection_name=self.valves.COLLECTION_NAME,
                vectors_config=VectorParams(size=384, distance=Distance.COSINE),
            )
            print(f"[MINTIFY] Created Qdrant collection '{self.valves.COLLECTION_NAME}'")

        print("[MINTIFY] Connecting to LLM (vLLM on RunPod via SSH tunnel)...")
        self.llm_client = OpenAI(
            base_url=f"{self.valves.VLLM_BASE_URL}/v1",
            api_key="vllm",
        )

        os.makedirs(self.valves.UPLOAD_DIR, exist_ok=True)
        self._index_all_files()
        print("[MINTIFY] Ready.")

    async def on_shutdown(self):
        print("[MINTIFY] Stopped.")

    def pipe(
        self, user_message: str, model_id: str, messages: list, body: dict
    ) -> Union[str, Generator]:

        print(f"\n{'='*60}")
        print(f"[MINTIFY] pipe() called")
        print(f"[MINTIFY] user_message: {repr(user_message[:200])}")
        print(f"[MINTIFY] model_id: {model_id}")
        print(f"[MINTIFY] stream: {body.get('stream', False)}")
        print(f"[MINTIFY] messages count: {len(messages)}")

        # Pick up any PDFs added since startup
        self._index_new_files()

        # Embed the user query
        print(f"[MINTIFY] Embedding query...")
        query_vec = self.embedder.encode(user_message).tolist()

        # Retrieve context from Qdrant
        context, hits_info = self._retrieve_context(query_vec)

        # Log retrieved chunks
        print(f"[MINTIFY] Retrieved {len(hits_info)} chunks:")
        for i, h in enumerate(hits_info):
            print(f"  [{i+1}] source={h['source']} page={h['page']} score={h['score']}")
            print(f"       text_preview_all: {repr(h['text'])}")# [:120]

        print(f"[MINTIFY] Context length: {len(context)} chars")

        # Strip manifold prefix and reverse slash sanitization: "mintify_pipeline.Qwen__..." → "Qwen/..."
        actual_model = model_id.removeprefix(f"{self.id}.").replace("__", "/")

        # Build enriched message list
        enriched_system = self.valves.SYSTEM_PROMPT.format(context=context)
        enriched = [{"role": "system", "content": enriched_system}]
        enriched += [m for m in messages if m.get("role") != "system"]

        print(f"[MINTIFY] Sending {len(enriched)} messages to vLLM (model: {actual_model})")
        print(f"[MINTIFY] System prompt length: {len(enriched_system)} chars")
        print(f"{'='*60}\n")

        return self._stream_vllm(enriched, body, actual_model)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _retrieve_context(self, query_vec: list) -> tuple:
        """Returns (context_string, list_of_hit_dicts_for_logging)."""
        try:
            info = self.qdrant.get_collection(self.valves.COLLECTION_NAME)
            n = info.points_count or 0
            print(f"[MINTIFY] Qdrant collection '{self.valves.COLLECTION_NAME}': {n} points")
        except Exception as e:
            print(f"[MINTIFY] ERROR reading Qdrant collection: {e}")
            return "Fehler beim Zugriff auf den Wissensspeicher.", []

        if n == 0:
            print("[MINTIFY] No documents in Qdrant — returning fallback.")
            return "Noch keine Dokumente im Wissensspeicher.", []

        try:
            result = self.qdrant.query_points(
                collection_name=self.valves.COLLECTION_NAME,
                query=query_vec,
                limit=min(self.valves.TOP_K, n),
                with_payload=True,
            )
            hits = result.points
        except Exception as e:
            print(f"[MINTIFY] ERROR during Qdrant search: {e}")
            return f"Fehler bei der Vektorsuche: {e}", []

        if not hits:
            print("[MINTIFY] Qdrant search returned 0 hits.")
            return "Keine relevanten Dokumente gefunden.", []

        hits_info = []
        parts = []
        for hit in hits:
            source = hit.payload.get("source", "unbekannt")
            page = hit.payload.get("page", "?")
            score = round(hit.score, 3)
            text = hit.payload.get("text", "")
            hits_info.append({"source": source, "page": page, "score": score, "text": text})
            parts.append(f"[Quelle: {source}, Seite {page}, Relevanz {score}]\n{text}")

        return "\n\n---\n\n".join(parts), hits_info

    def _stream_vllm(self, messages: list, body: dict, model: str) -> Generator:
        try:
            stream = self.llm_client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                temperature=body.get("temperature", 0.7),
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as e:
            print(f"[MINTIFY] ERROR in _stream_vllm: {e}")
            yield f"Fehler bei der Modellanfrage: {e}"

    def _index_all_files(self):
        pattern = os.path.join(self.valves.UPLOAD_DIR, "**", "*.pdf")
        files = glob.glob(pattern, recursive=True)
        print(f"[MINTIFY] Found {len(files)} PDF(s) in {self.valves.UPLOAD_DIR}")
        for path in files:
            self._index_file(path)

    def _index_new_files(self):
        pattern = os.path.join(self.valves.UPLOAD_DIR, "**", "*.pdf")
        for path in glob.glob(pattern, recursive=True):
            if self._file_hash(path) not in self._indexed_hashes:
                print(f"[MINTIFY] New file detected: {os.path.basename(path)}")
                self._index_file(path)

    def _index_file(self, path: str):
        import fitz  # pymupdf
        from qdrant_client.models import PointStruct

        h = self._file_hash(path)
        if h in self._indexed_hashes:
            return

        filename = os.path.basename(path)
        print(f"[MINTIFY] Indexing: {filename}")

        doc = fitz.open(path)
        points = []
        id_base = int(h, 16) % (2**53)  # deterministic: same file → same IDs across restarts

        for page_num, page in enumerate(doc):
            text = page.get_text()
            if not text.strip():
                continue
            for i, chunk in enumerate(self._chunk_text(text)):
                vec = self.embedder.encode(chunk).tolist()
                points.append(
                    PointStruct(
                        id=id_base + page_num * 1000 + i,
                        vector=vec,
                        payload={
                            "text": chunk,
                            "source": filename,
                            "page": page_num + 1,
                            "file_hash": h,
                        },
                    )
                )

        if points:
            self.qdrant.upsert(
                collection_name=self.valves.COLLECTION_NAME,
                points=points,
            )

        doc.close()
        self._indexed_hashes.add(h)
        print(f"[MINTIFY] Indexed {len(points)} chunks from '{filename}'")

    def _chunk_text(self, text: str) -> list:
        size, overlap = self.valves.CHUNK_SIZE, self.valves.CHUNK_OVERLAP
        chunks, start = [], 0
        while start < len(text):
            end = min(start + size, len(text))
            chunks.append(text[start:end])
            if end == len(text):
                break
            start += size - overlap
        return chunks

    def _file_hash(self, path: str) -> str:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
