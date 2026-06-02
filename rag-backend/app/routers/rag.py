"""
RAG endpoints

POST /api/pipe         — main RAG inference (retrieval + system-prompt injection + vLLM)
POST /api/passthrough  — direct vLLM streaming, no retrieval, no prompt manipulation
                         (used by the pipeline for OWUI's title/tag meta-tasks)
"""

import re

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.dependencies import get_embedder, get_llm_client, get_retriever
from app.services.embedder import Embedder
from app.services.llm_client import LLMClient
from app.services.retriever import Retriever

router = APIRouter()


DEFAULT_CONTEXT_BLOCK = "\n\n[KONTEXT]\n{context}\n[/KONTEXT]\n"


class PipeRequest(BaseModel):
    """Validated request body for POST /api/pipe. Sent by mintify_pipeline.pipe()."""
    user_message: str
    model_id: str
    messages: list[dict]
    temperature: float = 0.7
    # Per-model system prompt configured in OWUI. Wins over base_system_prompt when set.
    owui_system_prompt: str = ""
    # Pipeline fallback persona (the MINTIFY German assistant prompt).
    base_system_prompt: str = ""
    top_k: int = 5
    # OWUI file IDs the user has attached as Knowledge to this chat.
    # None = no filter (legacy behaviour). Empty list = explicit "no knowledge attached".
    file_ids: list[str] | None = None


class PassthroughRequest(BaseModel):
    """No retrieval, no prompt injection — straight to vLLM."""
    model_id: str
    messages: list[dict]
    temperature: float = 0.7


@router.post("/pipe")
async def pipe(
    req: PipeRequest,
    embedder: Embedder = Depends(get_embedder),
    retriever: Retriever = Depends(get_retriever),
    llm: LLMClient = Depends(get_llm_client),
) -> StreamingResponse:
    print(f"\n{'='*60}")
    print(f"[RAG] pipe() called")
    print(f"[RAG] user_message: {repr(req.user_message[:200])}")
    print(f"[RAG] model_id: {req.model_id}")
    print(f"[RAG] file_ids: {req.file_ids}")

    # Indexing is no longer triggered here — OWUI pushes new/updated files to
    # POST /api/index as they're uploaded (see routers/index.py).
    query_vec = embedder.encode(req.user_message)
    context, hits_info = retriever.retrieve(query_vec, req.top_k, file_ids=req.file_ids)

    print(f"[RAG] Retrieved {len(hits_info)} chunks:")
    for i, h in enumerate(hits_info):
        loc = f"page={h['page']}" if h.get("page") is not None else "(no page)"
        print(f"[{i+1}] {h['source']}  {loc}  score={h['score']}")

    # OWUI per-model prompt wins; pipeline persona is only a fallback.
    chosen = req.owui_system_prompt.strip() or req.base_system_prompt.strip()
    if context:
        if "{context}" not in chosen:
            chosen = chosen + DEFAULT_CONTEXT_BLOCK
        enriched_system = chosen.format(context=context)
    else:
        # No knowledge attached or zero hits — drop the {context} placeholder entirely.
        enriched_system = re.sub(
            r"\n*\[KONTEXT\]\s*\{context\}\s*\[/KONTEXT\]\n*", "\n", chosen
        ).replace("{context}", "").strip()

    enriched = [{"role": "system", "content": enriched_system}]
    enriched += [m for m in req.messages if m.get("role") != "system"]

    print(f"[RAG] Sending {len(enriched)} messages to vLLM (model: {req.model_id})")
    print(f"{'='*60}\n")

    def token_generator():
        yield from llm.stream(enriched, req.model_id, req.temperature)

    return StreamingResponse(token_generator(), media_type="text/plain; charset=utf-8")


@router.post("/passthrough")
async def passthrough(
    req: PassthroughRequest,
    llm: LLMClient = Depends(get_llm_client),
) -> StreamingResponse:
    """For OWUI meta-tasks (title, tags, query gen): stream vLLM without RAG."""
    print(f"[RAG] passthrough() | model={req.model_id} | {len(req.messages)} msgs")

    def token_generator():
        yield from llm.stream(req.messages, req.model_id, req.temperature)

    return StreamingResponse(token_generator(), media_type="text/plain; charset=utf-8")
