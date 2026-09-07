"""In-memory cache of the context the LLM saw for each chat's latest answer.

The Bewertung judges are triggered from the browser AFTER streaming finishes, on
a separate endpoint (/api/judge) that has no access to the retrieved document
chunks / web-search results the answer was built from. Without them the judges
fall back to their own (older) training knowledge and wrongly rate correct,
grounded answers as "schlecht" — see the judge prompt in judge_client.py.

/api/pipe stores the assembled context here keyed by chat_id right before it
starts streaming; /api/judge reads it back. The rag-backend runs a single
uvicorn worker (--reload), so this dict is shared across all requests. A bounded
LRU keeps it from growing without limit.

The same record also carries the retrieved chunks themselves (hits) keyed by
message_id, which GET /api/retrieved-sources hands back to the browser so the
citation list persisted with a chat is exactly what the LLM was fed — see
routers/retrieved_sources.py.
"""

from collections import OrderedDict
from threading import Lock

# Each turn occupies up to two keys (chat_id + message_id), so this holds
# roughly half as many turns as entries.
_MAX_ENTRIES = 512

_cache: "OrderedDict[str, dict]" = OrderedDict()
_lock = Lock()


def store(
    chat_id: str | None,
    *,
    context: str,
    web: str,
    question: str,
    hits: list[dict] | None = None,
    user_id: str | None = None,
    message_id: str | None = None,
) -> None:
    """Remember what this answer was built from.

    Stored under chat_id (what the judges look up — they only ever grade the
    chat's most recent answer) AND under message_id when the pipeline forwarded
    one, which is what GET /api/retrieved-sources reads: keying the citation
    list by the exact answer keeps two quick turns in the same chat from
    handing the browser the wrong turn's chunks.

    `hits` are the retrieved chunks themselves (retriever.hits_info) and
    `user_id` is the OWUI user the answer was produced for — the sources
    endpoint refuses to hand a turn's chunks to anybody else.
    """
    record = {
        "context": context or "",
        "web": web or "",
        "question": question or "",
        "hits": hits or [],
        "user_id": user_id or "",
    }
    keys = [k for k in (chat_id, message_id) if k]
    if not keys:
        return
    with _lock:
        for key in keys:
            _cache[key] = record
            _cache.move_to_end(key)
        while len(_cache) > _MAX_ENTRIES:
            _cache.popitem(last=False)


def get(chat_id: str | None) -> dict | None:
    """Return the cached context for chat_id, or None on a miss."""
    if not chat_id:
        return None
    with _lock:
        item = _cache.get(chat_id)
        if item is not None:
            _cache.move_to_end(chat_id)
        return item
