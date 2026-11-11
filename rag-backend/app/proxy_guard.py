"""
Public-exposure guard for requests that arrive through the reverse proxy.

The rag-backend serves two kinds of callers:
  * internal — the pipelines container (/api/pipe, /api/passthrough), OWUI's
    backend (ingest webhook /api/index, TTS /api/audio/speech, /api/reindex-all)
    and admin scripts on the host (scripts/reset_rag.sh). They talk to
    mintify-rag-service:8080 / localhost:8088 directly.
  * browser — the Bewertung panel, Persönliche Dateien, etc., which in
    production go through Caddy's /rag-api/* route.

Many internal routes have no auth of their own (/api/reset-collection wipes
Qdrant, /api/passthrough is a raw LLM). Before this guard, Caddy forwarded
/rag-api/* wholesale, so all of them were callable by anyone on the internet.

How a request from the internet is recognised: Caddy's reverse_proxy ALWAYS
sets X-Forwarded-For, and the container port is published on 127.0.0.1 only,
so Caddy is the only path in from outside. None of the internal callers set
that header. A client can add X-Forwarded-For but cannot remove the one Caddy
adds, so the check fails closed.

Proxied requests may only reach the allowlist below. It is an ALLOWlist on
purpose: a new endpoint is internal-only until someone adds it here. If you add
a browser→rag-backend endpoint, add its path here too (and keep it authenticated
where it touches user data).
"""

from starlette.requests import Request
from starlette.responses import JSONResponse

# Exact paths, or prefixes ending in "/" (matched on a segment boundary).
PUBLIC_PATHS: tuple[str, ...] = (
    "/api/health",
    "/api/judge",                  # Bewertung panel (JudgementPanel.svelte)
    "/api/personal-docs",          # sidebar Persönliche Dateien — OWUI-token auth
    "/api/personal-docs/",
    "/api/knowledge-level",        # OWUI-token auth
    "/api/knowledge-level/",
    "/api/retrieved-sources/",     # OWUI-token auth
)


def is_proxied(request: Request) -> bool:
    return "x-forwarded-for" in request.headers


def is_public_path(path: str) -> bool:
    return any(
        path.startswith(p) if p.endswith("/") else path == p
        for p in PUBLIC_PATHS
    )


async def proxy_guard(request: Request, call_next):
    if is_proxied(request) and not is_public_path(request.url.path):
        # 404, not 403: don't advertise that the route exists.
        print(
            f"[Guard] blocked proxied {request.method} {request.url.path} "
            f"from {request.headers.get('x-forwarded-for')}",
            flush=True,
        )
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return await call_next(request)
