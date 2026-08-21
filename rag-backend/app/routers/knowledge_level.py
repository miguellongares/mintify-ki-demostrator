"""
Kompetenzniveau — per-user financial-literacy level (0–3).

Written primarily by the model itself (see services/level_tool.py: the
[[KOMPETENZNIVEAU:n]] marker intercepted in routers/rag.py). These endpoints are
the manual/administrative surface on top of the same store.

Called directly from the browser like /api/judge and /api/personal-docs: dev →
localhost:8088, production → /rag-api/* via Caddy. Every request must carry the
caller's OWUI token as a Bearer header; the user id resolved from it scopes all
queries.

Note: only GET/POST/DELETE — main.py's CORS allow_methods does not include
PUT/PATCH, so those would fail preflight from the browser.
"""

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.dependencies import get_knowledge_level, get_owui_auth
from app.services import level_tool
from app.services.knowledge_level import KnowledgeLevelStore
from app.services.owui_auth import OwuiAuth

router = APIRouter()


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


class LevelRequest(BaseModel):
    level: int
    reason: str = ""


def _as_response(record: dict | None) -> dict:
    if not record:
        return {"level": None, "label": None, "reason": None, "updated_at": None}
    return {
        "level": record["level"],
        "label": level_tool.label(record["level"]),
        "reason": record.get("reason") or "",
        "source": record.get("source"),
        "updated_at": record.get("updated_at"),
    }


@router.get("/knowledge-level")
async def get_level(
    user: dict = Depends(require_user),
    store: KnowledgeLevelStore = Depends(get_knowledge_level),
):
    return _as_response(store.get(user["id"]))


@router.post("/knowledge-level")
async def set_level(
    req: LevelRequest,
    user: dict = Depends(require_user),
    store: KnowledgeLevelStore = Depends(get_knowledge_level),
):
    try:
        record = store.set(user["id"], req.level, req.reason, source="manual")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    print(f"[Kompetenz] user={user['id']} manuell → Niveau {req.level}", flush=True)
    return _as_response(record)


@router.delete("/knowledge-level")
async def reset_level(
    user: dict = Depends(require_user),
    store: KnowledgeLevelStore = Depends(get_knowledge_level),
):
    """Reset to 'not yet diagnosed' — the next chat can run a fresh diagnosis.
    The history is kept on purpose (research audit trail)."""
    cleared = store.clear(user["id"])
    print(f"[Kompetenz] user={user['id']} zurückgesetzt (vorher gesetzt: {cleared})", flush=True)
    return {"reset": cleared}


@router.get("/knowledge-level/history")
async def get_history(
    user: dict = Depends(require_user),
    store: KnowledgeLevelStore = Depends(get_knowledge_level),
):
    return {"history": store.history(user["id"])}


@router.get("/knowledge-level/all")
async def list_all_levels(
    user: dict = Depends(require_user),
    store: KnowledgeLevelStore = Depends(get_knowledge_level),
):
    """Research export: every user's current level + how often it changed."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    users = store.list_all()
    return {
        "count": len(users),
        "users": [{**u, "label": level_tool.label(u["level"])} for u in users],
    }
