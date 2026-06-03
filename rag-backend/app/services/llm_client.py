"""
LLMClient — vLLM model routing and streaming

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
  Uses the official openai Python client with stream=True. The pipeline proxy (mintify_pipeline.py) reads
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
                    # Inner loop handles APIs that return multiple models per URL (e.g. LiteLLM).
                    # vLLM typically returns just one, but the /v1/models spec allows many.
                    result[m["id"]] = url
            except Exception as e:
                # Non-fatal: extra endpoints in VLLM_BASE_URLS may not always be reachable.
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
        """
        debug = True
        #For debug purpose: 
        if debug == True:
            for num, message in enumerate(messages):
                print(f'------Message {num+1}-------')
                print(message)

        url = self._resolve_url(model)
        # Point the OpenAI client at the specific vLLM instance serving this model.
        client = OpenAI(base_url=f"{url}/v1", api_key="vllm")
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,          # request SSE streaming
                temperature=temperature,
            ) # type: ignore
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta       # plain text token, streamed back to the router
        except Exception as e:
            print(f"[RAG] ERROR in LLMClient.stream (url={url}, model={model}): {e}")
            # Yield a visible German error message so the user sees feedback in the chat
            # instead of a silent empty response.
            yield f"Fehler bei der Modellanfrage: {e}"
