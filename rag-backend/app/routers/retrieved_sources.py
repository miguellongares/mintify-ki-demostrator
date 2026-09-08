"""
Retrieved sources — the chunks that actually fed one answer.

OWUI's own citation machinery is deliberately switched off for MINTIFY (see
utils/middleware.py: with BYPASS_EMBEDDING_AND_RETRIEVAL it dumps each attached
file's ENTIRE extracted text into the chat row, which is neither what the LLM
saw nor something worth persisting). This endpoint is the replacement: after the
answer finishes streaming, the browser asks for the top-K chunks /api/pipe
actually injected and stores those with the chat instead.

Nothing is retrieved or embedded here — /api/pipe already cached this turn's
hits in services/context_cache.py, so the call is a dict lookup.

Called directly from the browser (like /api/judge and /api/personal-docs):
dev → localhost:8088, production → /rag-api/* via Caddy. Chunk text is document
content, so unlike /api/judge this endpoint is authenticated: the caller must
present their own OWUI token AND be the user the answer was produced for.
"""

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.dependencies import get_owui_auth
from app.services import context_cache
from app.services.owui_auth import OwuiAuth

router = APIRouter()

# Chunks are CHUNK_SIZE (800) chars by design; this only guards against a
# pathologically long one bloating the chat row.
_MAX_CHUNK_CHARS = 2000


class RetrievedChunk(BaseModel):
    source: str
    text: str
    page: int | None = None
    file_id: str | None = None
    # "personal" for a sidebar file, absent/None for OWUI knowledge.
    doc_type: str | None = None


class RetrievedSourcesResponse(BaseModel):
    sources: list[RetrievedChunk]


def require_user(
    authorization: str | None = Header(None),
    auth: OwuiAuth = Depends(get_owui_auth),
) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    user = auth.resolve_user(authorization.split(" ", 1)[1])
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid OWUI token")
    return user


@router.get("/retrieved-sources/{message_id}", response_model=RetrievedSourcesResponse)
async def retrieved_sources(
    message_id: str,
    user: dict = Depends(require_user),
) -> RetrievedSourcesResponse:
    cached = context_cache.get(message_id)

    # Miss is normal, not an error: a model-only answer (nothing attached), a
    # turn from before a restart (the cache is in-process), or an answer that
    # aged out of the LRU. The browser just stores no citations for it.
    if not cached:
        return RetrievedSourcesResponse(sources=[])

    # The chunks belong to whoever asked the question — never hand them to
    # another account on the strength of a guessed message id. An answer
    # produced without a user id (no one to scope retrieval to) is not
    # claimable either.
    owner = cached.get("user_id") or ""
    if not owner or owner != user.get("id"):
        raise HTTPException(status_code=403, detail="Not your message")

    return RetrievedSourcesResponse(
        sources=[
            RetrievedChunk(
                source=hit.get("source") or "unbekannt",
                text=(hit.get("text") or "")[:_MAX_CHUNK_CHARS],
                page=hit.get("page"),
                file_id=hit.get("file_id"),
                doc_type=hit.get("doc_type"),
            )
            for hit in cached.get("hits", [])
            if (hit.get("text") or "").strip()
        ]
    )
