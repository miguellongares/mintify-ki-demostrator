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
"""

from collections import OrderedDict
from threading import Lock

_MAX_ENTRIES = 256

_cache: "OrderedDict[str, dict]" = OrderedDict()
_lock = Lock()


def store(chat_id: str | None, *, context: str, web: str, question: str) -> None:
    """Remember the context used for chat_id's most recent answer."""
    if not chat_id:
        return
    with _lock:
        _cache[chat_id] = {
            "context": context or "",
            "web": web or "",
            "question": question or "",
        }
        _cache.move_to_end(chat_id)
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
