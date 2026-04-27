"""
GET /api/models — model discovery endpoint
------------------------------------------
Called by mintify_pipeline.py's pipelines() method every time Open Web UI refreshes
its model dropdown. This endpoint queries all configured vLLM instances for their
currently loaded models and returns a unified list.

Response format (list of dicts):
  [{"id": "Qwen/Qwen3-1.7B", "name": " – Qwen/Qwen3-1.7B"}, ...]

Note: model IDs here are raw HuggingFace IDs (with "/"). The pipeline layer
(mintify_pipeline.py) is responsible for sanitizing "/" → "__" for OWUI routing.
The name field has a leading space so it renders neatly in the OWUI dropdown
alongside the pipeline's own display name ("MINTIFY – Qwen/Qwen3-1.7B").

Fallback: if all vLLM endpoints are unreachable, returns a single "unavailable"
entry so OWUI always has something to show (and the user gets a clear error message).
"""

from fastapi import APIRouter, Depends

from app.dependencies import get_llm_client
from app.services.llm_client import LLMClient

router = APIRouter()


@router.get("/models")
async def get_models(llm: LLMClient = Depends(get_llm_client)) -> list[dict]:
    """
    Returns one entry per model across all configured vLLM endpoints.
    Also refreshes the internal model→URL routing map so that subsequent
    pipe() calls know which URL to use for each model ID.
    Model IDs are raw HuggingFace IDs — the pipeline layer handles sanitization.
    """
    # refresh_model_map() hits GET {vllm_url}/v1/models on every configured vLLM URL
    # and rebuilds the internal {model_id: vllm_url} dict used by LLMClient.stream().
    model_url_map = llm.refresh_model_map()

    if not model_url_map:
        # All vLLM endpoints are down or unreachable. Return a placeholder so OWUI
        # shows "vLLM unavailable" instead of an empty dropdown or a crash.
        return [{"id": "unavailable", "name": "vLLM unavailable"}]

    return [{"id": mid, "name": f" – {mid}"} for mid in model_url_map]
