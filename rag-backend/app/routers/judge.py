import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.dependencies import get_judge_client
from app.services.judge_client import JudgeClient

router = APIRouter()


class JudgeRequest(BaseModel):
    query: str
    answer: str


class JudgeResult(BaseModel):
    rating: str
    reasoning: str


class JudgeResponse(BaseModel):
    judges: list[JudgeResult]


@router.post("/judge", response_model=JudgeResponse)
async def judge_answer(
    req: JudgeRequest,
    judge: JudgeClient = Depends(get_judge_client),
):
    print(
        f"[Judge] POST /api/judge | query={req.query[:60]!r} | answer={req.answer[:60]!r}",
        flush=True,
    )
    t0 = time.time()
    results = judge.judge_all(req.query, req.answer)
    elapsed = time.time() - t0
    print(f"[Judge] POST /api/judge completed in {elapsed:.1f}s", flush=True)
    return JudgeResponse(judges=[JudgeResult(**r) for r in results])
