"""
MINTIFY RAG Pipeline — thin proxy
----------------------------------
This file is the only custom code that runs inside the Open Web UI Pipelines container
(mintify-pipelines, port 9099). It does NOT contain any RAG or ML logic itself — all of
that lives in the separate mintify-rag-service container (port 8080). This file's sole
job is to bridge Open Web UI's pipeline framework to the RAG backend over HTTP.

How it fits into the system:
  Browser → Open Web UI (port 3000)
           → Pipelines server (port 9099)  ← THIS FILE
           → mintify-rag-service (port 8080)
           → vLLM on RunPod (via SSH tunnel on host ports 8002/8004)

The Open Web UI Pipelines framework discovers this file automatically because
PIPELINES_DIR=/app/pipelines and this file is bind-mounted there from ./pipelines/.
Hot-reload: saving this file triggers automatic reload inside the running container
without needing a restart (the framework watches the directory for changes).

Pipeline type — "manifold":
  A manifold pipeline exposes MULTIPLE models in the OWUI dropdown, one per model
  returned by pipelines(). This lets users pick any model currently loaded in vLLM
  directly from the chat interface. The alternative ("pipe" type) would expose only
  a single fixed model. Manifold is the right choice here because RunPod can serve
  multiple models simultaneously.

Key methods called by the framework (in order):
  __init__()    → on startup (once)
  on_startup()  → async health check after init
  pipelines()   → called periodically to refresh the model dropdown
  pipe()        → called for every chat message sent by the user
  on_shutdown() → called when the container stops
"""

import os
from typing import Generator

import httpx
from pydantic import BaseModel


class Pipeline:

    # -------------------------------------------------------------------------
    # Valves — live-editable configuration
    # -------------------------------------------------------------------------
    # The Valves inner class is a Pydantic BaseModel. The Open Web UI admin panel
    # reads it and renders each field as an editable form at:
    #   Admin Panel → Pipelines → MINTIFY
    # When the admin saves changes, the framework writes them to
    # pipelines/mintify_pipeline/valves.json and reloads the pipeline.
    # On startup, _load_valves() reads that JSON and passes it here as kwargs.
    # Fields not present in the JSON fall back to their defaults below.
    class Valves(BaseModel):
        # URL of the RAG backend service. Comes from the RAG_BACKEND_URL env var
        # set in docker-compose.yml; falls back to the Docker-internal hostname
        # which is the container_name of the mintify-rag-service service.
        BACKEND_URL: str = os.getenv("RAG_BACKEND_URL", "http://mintify-rag-service:8080")

        # How many PDF chunks to inject into the system prompt per query.
        # Higher values give the LLM more context but increase prompt length and cost.
        TOP_K: int = 5

        # The German-language system prompt sent to the LLM on every request.
        # MUST contain the {context} placeholder — the RAG backend fills it with
        # the retrieved PDF chunks before sending to vLLM.
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
        # self.id becomes the namespace prefix for all sub-model IDs in OWUI.
        # A manifold model appears in the dropdown as "{self.id}.{sub_model_id}".
        self.id = "mintify_pipeline"

        # Display name shown in the OWUI admin panel under the Pipelines section.
        self.name = "MINTIFY"

        # "manifold" tells the framework this pipeline exposes multiple sub-models
        # via the pipelines() method, rather than acting as a single fixed model.
        self.type = "manifold"

        # Load saved valve settings from JSON (if they exist), then initialise
        # the Valves model. Extra keys in the JSON are silently ignored by Pydantic.
        self.valves = self.Valves(**self._load_valves())

    # -------------------------------------------------------------------------
    # pipelines() — model discovery
    # -------------------------------------------------------------------------
    # Called by the OWUI framework periodically (and on load) to refresh the
    # model dropdown. Hits the RAG backend's /api/models endpoint, which in
    # turn queries all configured vLLM instances for their loaded models.
    #
    # ID sanitization: HuggingFace model IDs contain "/" (e.g. "Qwen/Qwen3-1.7B").
    # OWUI embeds the model ID into URL paths, so "/" would break routing.
    # We replace "/" with "__" here; pipe() reverses this when forwarding to the backend.
    # The resulting OWUI model ID looks like: "mintify_pipeline.Qwen__Qwen3-1.7B"
    def pipelines(self) -> list:
        """Fetch available models from the RAG backend and sanitize IDs for OWUI routing."""
        try:
            resp = httpx.get(f"{self.valves.BACKEND_URL}/api/models", timeout=5)
            resp.raise_for_status()
            result = [
                {"id": m["id"].replace("/", "__"), "name": m["name"]}
                for m in resp.json()
            ]
            # Log registered models so it's visible in `docker logs mintify-pipelines`
            # whether real RunPod models or the fallback "unavailable" entry was returned.
            print(f"[MINTIFY] Registered models: {[m['id'] for m in result]}", flush=True)
            return result
        except Exception as e:
            # flush=True: Docker buffers stdout for non-TTY processes; belt-and-suspenders
            # alongside PYTHONUNBUFFERED=1 in docker-compose.yml.
            print(f"[MINTIFY] Backend unreachable: {e}", flush=True)
            # Return a placeholder so OWUI always has at least one entry in the dropdown.
            return [{"id": "unavailable", "name": "vLLM unavailable"}]

    # -------------------------------------------------------------------------
    # Lifecycle hooks
    # -------------------------------------------------------------------------
    # on_startup is async because the OWUI framework awaits it. The httpx call
    # inside is synchronous (not async), which is acceptable for a one-time check.
    async def on_startup(self):
        # flush=True ensures this appears immediately in `docker logs mintify-pipelines`
        # even when PYTHONUNBUFFERED=1 is not set (extra safety).
        print(f"[MINTIFY] Pipeline started. Backend: {self.valves.BACKEND_URL}", flush=True)
        try:
            resp = httpx.get(f"{self.valves.BACKEND_URL}/api/health", timeout=10)
            resp.raise_for_status()
            print("[MINTIFY] RAG backend is healthy.", flush=True)
        except Exception as e:
            # Non-fatal: the pipeline still starts. The backend may become healthy later.
            print(f"[MINTIFY] WARNING: RAG backend health check failed: {e}", flush=True)

    async def on_shutdown(self):
        print("[MINTIFY] Pipeline stopped.", flush=True)

    # -------------------------------------------------------------------------
    # pipe() — the main message handler
    # -------------------------------------------------------------------------
    # Called by the OWUI framework for every chat message. Must be a regular
    # (synchronous) def, not async — the framework wraps it in a thread executor
    # and iterates the returned generator to stream tokens back to the browser.
    #
    # Arguments provided by OWUI:
    #   user_message  — the raw text of the latest user message
    #   model_id      — full OWUI model ID, e.g. "mintify_pipeline.Qwen__Qwen3-1.7B"
    #   messages      — full conversation history as OpenAI-format dicts
    #   body          — the raw request body (contains temperature, stream flag, etc.)
    def pipe(
        self, user_message: str, model_id: str, messages: list, body: dict
    ) -> Generator:
        # Reverse the "/" → "__" sanitization applied in pipelines().
        # Also strip the pipeline namespace prefix added by OWUI.
        # Example: "mintify_pipeline.Qwen__Qwen3-1.7B" → "Qwen/Qwen3-1.7B"
        # This is the actual HuggingFace model ID that vLLM expects.
        actual_model = model_id.removeprefix(f"{self.id}.").replace("__", "/")

        # Build the request payload for the RAG backend's POST /api/pipe endpoint.
        # The backend will embed the query, search Qdrant, enrich the system prompt,
        # and stream the vLLM response back as plain text.
        payload = {
            "user_message": user_message,      # used for embedding + Qdrant search
            "model_id": actual_model,           # forwarded to vLLM as-is
            "messages": messages,               # full conversation history for the LLM
            "temperature": body.get("temperature", 0.7),
            "system_prompt": self.valves.SYSTEM_PROMPT,  # contains {context} placeholder
            "top_k": self.valves.TOP_K,         # how many PDF chunks to retrieve
        }

        # Log before the network call so we can confirm pipe() was invoked even
        # if the backend request hangs or fails immediately.
        # flush=True: Docker buffers stdout; this ensures the log line appears instantly.
        print(f"[MINTIFY] pipe() called | model_id={model_id!r} | user_message={user_message!r} | messages_count={len(messages)}", flush=True)
        print(f"[MINTIFY] Forwarding to RAG backend (model: {actual_model})", flush=True)

        # Stream the response from the RAG backend and yield each text chunk to OWUI.
        # httpx streaming keeps the connection open and yields chunks as they arrive,
        # so the user sees tokens appear progressively in the browser.
        # timeout=120s: vLLM generation on RunPod can be slow for long responses.
        try:
            with httpx.Client(timeout=120.0) as client:
                with client.stream(
                    "POST",
                    f"{self.valves.BACKEND_URL}/api/pipe",
                    json=payload,
                ) as r:
                    r.raise_for_status()
                    for chunk in r.iter_text():
                        if chunk:
                            yield chunk  # each chunk is a raw text token from vLLM
        except Exception as exc:
            # Log the error AND yield a visible error message to the chat window.
            # Without this, a backend failure would produce a silent hang in OWUI.
            print(f"[MINTIFY] ERROR calling RAG backend: {exc}", flush=True)
            yield f"[MINTIFY Fehler] RAG-Backend nicht erreichbar: {exc}"

    # -------------------------------------------------------------------------
    # _load_valves() — persistent valve settings
    # -------------------------------------------------------------------------
    # The OWUI framework saves valve changes to a JSON file next to this module.
    # We read it at __init__ time so settings survive container restarts.
    # If the file doesn't exist (first run), we return {} and all valves use defaults.
    def _load_valves(self) -> dict:
        import json
        # The JSON file lives at pipelines/mintify_pipeline/valves.json on the host,
        # which maps to /app/pipelines/mintify_pipeline/valves.json in the container.
        path = os.path.join(os.path.dirname(__file__), "mintify_pipeline", "valves.json")
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return {}
