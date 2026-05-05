import json
import os
from typing import Generator

import httpx
from pydantic import BaseModel


class Pipeline:

    class Valves(BaseModel):
        BACKEND_URL: str = os.getenv("RAG_BACKEND_URL", "http://mintify-rag-service:8080")
        TOP_K: int = 5
        # Must contain {context} placeholder — filled by the RAG backend before sending to vLLM.
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
        self.type = "manifold" #By defining manifold as type OWUI automatically calls pipelines
        self.valves = self.Valves(**self._load_valves())

    def pipelines(self) -> list:
        """Fetch available models from the RAG backend."""
        try:
            resp = httpx.get(f"{self.valves.BACKEND_URL}/api/models", timeout=5)
            resp.raise_for_status()
            # Replace "/" with "__" — OWUI embeds model IDs in URL paths; "/" breaks routing.
            result = [
                {"id": m["id"].replace("/", "__"), "name": m["name"]}
                for m in resp.json()
            ]
            print(f"[MINTIFY] Registered models: {[m['id'] for m in result]}", flush=True)
            return result
        except Exception as e:
            print(f"[MINTIFY] Backend unreachable: {e}", flush=True)
            return [{"id": "unavailable", "name": "vLLM unavailable"}]

    async def on_startup(self):
        print(f"[MINTIFY] Pipeline started. Backend: {self.valves.BACKEND_URL}", flush=True)
        try:
            resp = httpx.get(f"{self.valves.BACKEND_URL}/api/health", timeout=10)
            resp.raise_for_status()
            print("[MINTIFY] RAG backend is healthy.", flush=True)
        except Exception as e:
            print(f"[MINTIFY] WARNING: RAG backend health check failed: {e}", flush=True)

    async def on_shutdown(self):
        print("[MINTIFY] Pipeline stopped.", flush=True)

    def pipe(self, user_message: str, model_id: str, messages: list, body: dict) -> Generator:
        # Reverse "__" → "/" sanitization and strip the pipeline namespace prefix.
        actual_model = model_id.removeprefix(f"{self.id}.").replace("__", "/")

        payload = {
            "user_message": user_message,
            "model_id": actual_model,
            "messages": messages,
            "temperature": body.get("temperature", 0.7),
            "system_prompt": self.valves.SYSTEM_PROMPT,
            "top_k": self.valves.TOP_K,
        }

        print(f"[MINTIFY] pipe() called | model={actual_model!r} | msg={user_message!r}", flush=True)

        try:
            with httpx.Client(timeout=120.0) as client:  # 120s: vLLM on RunPod can be slow
                with client.stream("POST", f"{self.valves.BACKEND_URL}/api/pipe", json=payload) as r:
                    r.raise_for_status()
                    for chunk in r.iter_text():
                        if chunk:
                            yield chunk
        except Exception as exc:
            print(f"[MINTIFY] ERROR calling RAG backend: {exc}", flush=True)
            yield f"[MINTIFY Fehler] RAG-Backend nicht erreichbar: {exc}"

    def _load_valves(self) -> dict:
        path = os.path.join(os.path.dirname(__file__), "mintify_pipeline", "valves.json")
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return {}
