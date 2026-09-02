import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.dependencies import get_judge_client
from app.services import context_cache
from app.services.judge_client import JudgeClient

router = APIRouter()


class JudgeMessage(BaseModel):
    role: str
    content: str


class JudgeRequest(BaseModel):
    query: str
    answer: str
    # The whole chat transcript — the judges grade the conversation as a whole,
    # not just the last turn. Optional: an older frontend build sends only
    # query+answer, and the prompt falls back to the single-turn block.
    messages: list[JudgeMessage] | None = None
    # OWUI chat id — lets the judges reuse the exact context (documents + web
    # results) the answer was built from, cached by /api/pipe. Without it the
    # judges only see query+answer and rate grounded answers as "schlecht".
    chat_id: str | None = None


def _build_judge_context(cached: dict | None) -> str:
    """Assemble the cached document + web context into one block for the judges."""
    if not cached:
        return ""
    parts: list[str] = []
    if cached.get("context"):
        parts.append("[DOKUMENTE]\n" + cached["context"])
    if cached.get("web"):
        parts.append("[WEB-RECHERCHE]\n" + cached["web"])
    return "\n\n".join(parts)


class JudgeResult(BaseModel):
    rating: str
    reasoning: str
    # Which of the client's three prompt variants (A/B/C) produced this verdict.
    # Lets a card in the Bewertung panel be traced back to its prompt.
    variant: str | None = None


class JudgeResponse(BaseModel):
    judges: list[JudgeResult]


@router.post("/judge", response_model=JudgeResponse)
async def judge_answer(
    req: JudgeRequest,
    judge: JudgeClient = Depends(get_judge_client),
):
    context = _build_judge_context(context_cache.get(req.chat_id))
    messages = [m.model_dump() for m in req.messages] if req.messages else None
    print(
        f"[Judge] POST /api/judge | query={req.query[:60]!r} | "
        f"answer={req.answer[:60]!r} | chat_id={req.chat_id} | "
        f"context={'hit' if context else 'miss'} | "
        f"messages={len(messages) if messages else 0}",
        flush=True,
    )
    t0 = time.time()
    results = judge.judge_all(req.query, req.answer, context, messages)
    elapsed = time.time() - t0
    print(f"[Judge] POST /api/judge completed in {elapsed:.1f}s", flush=True)
    return JudgeResponse(judges=[JudgeResult(**r) for r in results])
