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


def _reasoning_block(text: str) -> str:
    """Wrap the model's reflection in a closed <details type="reasoning"> block.

    The OWUI markdown renderer turns this into a collapsed "Gedankengang" section,
    and removeDetails(..., ['reasoning']) strips it before TTS reads or the judges
    rate the answer. Must be emitted fully closed in one go — the marked `details`
    tokenizer only fires once the matching </details> is present.
    """
    text = text.strip()
    return (
        '<details type="reasoning">\n'
        '<summary>Gedankengang</summary>\n'
        f'{text}\n'
        '</details>\n\n'
    )


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

    def complete(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 150,
    ) -> str:
        """
        Non-streaming chat completion — for internal meta-calls (e.g. query
        rewriting) where the full short answer is needed before continuing.
        Raises on failure; callers decide their own fallback.
        """
        url = self._resolve_url(model)
        client = OpenAI(base_url=f"{url}/v1", api_key="vllm")
        completion = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )  # type: ignore
        return completion.choices[0].message.content or ""

    def stream(
        self,
        messages: list[dict],
        model: str,
        temperature: float,
        enable_thinking: bool = False,
    ) -> Generator[str, None, None]:
        """
        Send an OpenAI-compatible chat completion request to vLLM and stream back tokens.

        When ``enable_thinking`` is True the 💡 "Nachdenken" toggle is on: we tell the
        Qwen3.5 chat template to reflect first (``chat_template_kwargs.enable_thinking``).
        vLLM is launched with ``--reasoning-parser qwen3`` (thinking off by default), so
        the model's <think> block arrives on ``delta.reasoning_content`` separate from the
        answer in ``delta.content``. We buffer the reasoning and, once the answer begins,
        emit it once as a collapsed ``<details type="reasoning">`` block (rendered as the
        "Gedankengang" section in the UI; TTS/judge strip it via removeDetails).
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
                # vLLM extension: override the server's default enable_thinking:false.
                extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
            ) # type: ignore
            reasoning_parts: list[str] = []
            reasoning_flushed = False
            for chunk in stream:
                delta = chunk.choices[0].delta
                if enable_thinking:
                    # reasoning_content is a vLLM-only field; the OpenAI SDK keeps it as
                    # an attribute or in model_extra depending on version.
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning is None:
                        extra = getattr(delta, "model_extra", None)
                        if extra:
                            reasoning = extra.get("reasoning_content")
                    if reasoning:
                        reasoning_parts.append(reasoning)
                content = delta.content
                if content:
                    # Answer started → the thinking phase is over: flush it as one
                    # complete (closed) details block so the markdown renderer picks
                    # it up as a collapsible section.
                    if reasoning_parts and not reasoning_flushed:
                        yield _reasoning_block("".join(reasoning_parts))
                        reasoning_flushed = True
                    yield content     # plain text token, streamed back to the router
            # Model reflected but produced no answer content (rare) — still show it.
            if reasoning_parts and not reasoning_flushed:
                yield _reasoning_block("".join(reasoning_parts))
        except Exception as e:
            print(f"[RAG] ERROR in LLMClient.stream (url={url}, model={model}): {e}")
            # Yield a visible German error message so the user sees feedback in the chat
            # instead of a silent empty response.
            yield f"Fehler bei der Modellanfrage: {e}"
