"""
LLMClient — vLLM model routing and streaming
---------------------------------------------
Manages connections to one or more vLLM instances running on RunPod.
Each vLLM instance exposes an OpenAI-compatible API on port 8000 (on RunPod),
which is SSH-tunnelled to localhost:8002 (and :8004 for a second pod) on the host,
and reached from Docker containers via host.docker.internal:8002.

Model→URL routing:
  vLLM can serve multiple models, and there can be multiple vLLM instances.
  LLMClient maintains a dict { model_id: vllm_base_url } built by querying
  GET /v1/models on each configured URL. When stream() is called with a model ID,
  it looks up the correct URL from this map.

Refresh strategy:
  - refresh_model_map() is called on startup and on every GET /api/models request.
  - If the map is stale (model not found), _resolve_url() refreshes once and retries.
  - This handles the case where vLLM starts after the RAG service or a model is loaded
    dynamically without restarting the RAG service.

Streaming:
  Uses the official openai Python client with stream=True. This gives us a standard
  SSE (Server-Sent Events) stream which we iterate chunk by chunk. Each chunk's
  delta.content is yielded as a plain text string back to the router, which wraps
  it in a FastAPI StreamingResponse. The pipeline proxy (mintify_pipeline.py) reads
  this stream via httpx and yields each chunk to Open Web UI.
"""

from typing import Generator

import httpx
from openai import OpenAI


class LLMClient:
    def __init__(self, vllm_urls: list[str]):
        # List of vLLM base URLs (without /v1), e.g. ["http://host.docker.internal:8002"]
        self._vllm_urls = vllm_urls
        # { model_id: vllm_base_url } — populated by refresh_model_map()
        self._model_url_map: dict[str, str] = {}

    def refresh_model_map(self) -> dict[str, str]:
        """
        Query all configured vLLM endpoints for their loaded models and rebuild the routing map.
        Called on startup and on every GET /api/models request (which OWUI polls periodically).
        Returns the new map so the router can build its response without a separate call.
        """
        result: dict[str, str] = {}
        for url in self._vllm_urls:
            try:
                # vLLM exposes GET /v1/models — returns {"data": [{"id": "Qwen/...", ...}]}
                resp = httpx.get(f"{url}/v1/models", timeout=5)
                resp.raise_for_status()
                for m in resp.json().get("data", []):
                    # Map each model ID to the URL where it's hosted.
                    # If two vLLM instances serve the same model ID, the last one wins.
                    result[m["id"]] = url
            except Exception as e:
                # Non-fatal: this URL may be a second pod that isn't always running.
                print(f"[RAG] Could not fetch models from {url}: {e}")
        self._model_url_map = result
        return result

    def _resolve_url(self, model: str) -> str:
        """
        Look up the vLLM base URL for a given model ID.
        If the model isn't in the current map, refresh once and retry.
        Falls back to the first configured URL as a last resort (with a warning).
        """
        if model in self._model_url_map:
            return self._model_url_map[model]

        # Map may be stale (model loaded after our last refresh) — try once more.
        self.refresh_model_map()
        if model in self._model_url_map:
            return self._model_url_map[model]

        # Still not found — use the first URL and let vLLM return an error for the wrong model.
        fallback = self._vllm_urls[0] if self._vllm_urls else ""
        print(f"[RAG] WARNING: model '{model}' not found in any vLLM endpoint, using {fallback}")
        return fallback

    def stream(self, messages: list[dict], model: str, temperature: float) -> Generator[str, None, None]:
        """
        Send an OpenAI-compatible chat completion request to vLLM and stream back tokens.

        The openai client is created fresh per call (not cached) because the base_url
        can vary per model. This is a lightweight object — no persistent connection.
        api_key="vllm" is required by the openai client interface but vLLM doesn't
        actually validate it; any non-empty string works.

        Yields raw text strings (token deltas), one per SSE chunk from vLLM.
        Empty deltas (None) are skipped — they appear in the final chunk.
        """
        url = self._resolve_url(model)
        # Point the OpenAI client at the specific vLLM instance serving this model.
        client = OpenAI(base_url=f"{url}/v1", api_key="vllm")
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,          # request SSE streaming
                temperature=temperature,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta       # plain text token, streamed back to the router
        except Exception as e:
            print(f"[RAG] ERROR in LLMClient.stream (url={url}, model={model}): {e}")
            # Yield a visible German error message so the user sees feedback in the chat
            # instead of a silent empty response.
            yield f"Fehler bei der Modellanfrage: {e}"
