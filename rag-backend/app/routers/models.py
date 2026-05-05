"""
GET /api/models — model discovery endpoint
------------------------------------------
Called by mintify_pipeline.py's pipelines() method every time Open Web UI refreshes
its model dropdown. 
Response format (list of dicts):
  [{"id": "Qwen/Qwen3-1.7B", "name": " – Qwen/Qwen3-1.7B"}, ...]

Note: model IDs here are raw HuggingFace IDs (with "/"). The pipeline layer
(mintify_pipeline.py) is responsible for sanitizing "/" → "__" for OWUI routing.
The name field has a leading space so it renders neatly in the OWUI dropdown
alongside the pipeline's own display name ("MINTIFY – Qwen/Qwen3-1.7B").

Fallback: if all vLLM endpoints are unreachable, returns a single "unavailable"
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
    model_url_map = llm.refresh_model_map()

    if not model_url_map:
        return [{"id": "unavailable", "name": "vLLM unavailable"}]

    return [{"id": mid, "name": f" – {mid}"} for mid in model_url_map]
