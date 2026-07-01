# Online Search (Web Search) via SearXNG

> Added in commit `e3fe583` ("Implemented the OnlineSearch"). This document explains how
> MINTIFY's 🌐 web-search feature works end to end and how it was wired together.

## TL;DR

| Question | Answer |
|---|---|
| What does it do? | When the user enables the 🌐 toggle in a chat, the answer is grounded on a **live internet search** instead of (only) the LLM's training knowledge. |
| Who runs the search? | The **rag-backend**, not Open Web UI. It queries a self-hosted **SearXNG** container, fetches the top pages, and extracts clean article text with **trafilatura**. |
| Where do results go? | Injected into the system prompt as a `[WEB-RECHERCHE]` block with numbered `[Quelle N]` sources, then a **Quellen** footer with the URLs is appended to the streamed answer. |
| Why not OWUI's native web search? | OWUI's built-in search returns boilerplate-heavy raw pages and only snippets. MINTIFY bypasses it for clean, budgeted, multi-engine results. |
| New infra | A `searxng` Docker service (`searxng/searxng:latest`) + `searxng/settings.yml`. |
| New dependency | `trafilatura>=1.8.0` in `rag-backend/requirements.txt`. |
| Key files | `rag-backend/app/services/web_search.py`, `rag-backend/app/routers/rag.py`, `pipelines/mintify_pipeline.py`, `docker-compose.yml`, `searxng/settings.yml` |

---

## Why backend-owned (and not OWUI-native)

Open Web UI ships its own web-search support (`open-webui-src/backend/open_webui/retrieval/web/searxng.py`),
but it only returns **raw SearXNG snippets**, and its page loader (`WebBaseLoader`) dumps whole
pages including nav menus, cookie banners, ads and footers — noise that crowds out the answer in
the LLM context window.

MINTIFY instead does the search **inside the rag-backend**, where it already builds the prompt for
the LLM. This lets it:

- fetch the actual result pages and extract only the **main article text** (trafilatura),
- apply per-document and total **character budgets** so the context stays focused,
- fall back to the SearXNG snippet when a page can't be read (JS-only, anti-bot, timeout),
- emit numbered citations the model is asked to reference.

OWUI's own fetch step is therefore **disabled** so pages aren't fetched twice. This is done with the
`MINTIFY_BACKEND_WEB_SEARCH=true` env var and a guard in OWUI's middleware (see below). Crucially,
`ENABLE_WEB_SEARCH=true` stays on so the 🌐 toggle is still visible and the `web_search` flag still
flows through to the pipeline — OWUI just stops doing the actual fetching.

---

## End-to-end data flow

```
User toggles 🌐  (MessageInput.svelte)
  │
  └─ OWUI sets  body.metadata.features.web_search = true
       │
       ├─ middleware.py guard SKIPS OWUI-native web search   (MINTIFY_BACKEND_WEB_SEARCH=true)
       │
       └─ Pipelines server → mintify_pipeline.pipe()
            │  _web_search_enabled(body)  reads body.metadata.features.web_search
            │  → forwards PipeRequest.web_search = true to the rag-backend
            │
            └─ rag-backend  POST /api/pipe   (routers/rag.py)
                 │
                 ├─ if web_search:  WebSearcher.search(user_message)   (services/web_search.py)
                 │     │
                 │     ├─ SearXNG JSON API:  http://searxng:8080/search?q=…&format=json&language=de
                 │     │       → top-N {url, title, snippet}
                 │     │
                 │     ├─ fetch each page concurrently (httpx + ThreadPoolExecutor)
                 │     │       → trafilatura.extract()  → clean article text
                 │     │       → snippet fallback if extracted text is too short
                 │     │
                 │     └─ return list[WebResult]  (ranked as SearXNG ranked, budget-capped)
                 │
                 ├─ inject results as a [WEB-RECHERCHE] block with [Quelle N] sources
                 │   into the system prompt (after {context} formatting)
                 │
                 └─ stream answer from vLLM, then append a **Quellen** footer with the URLs
```

Every layer streams; the web search runs **before** the LLM call (it has to, since its output goes
into the prompt), but the page fetches are concurrent so total latency ≈ the slowest single page.

---

## The SearXNG service

A self-hosted [SearXNG](https://docs.searxng.org/) metasearch instance runs as a Docker container.
It is **internal-only** — no host port is exposed in normal operation.

`docker-compose.yml`:

```yaml
searxng:
  image: searxng/searxng:latest
  container_name: mintify-searxng
  volumes:
    - ./searxng:/etc/searxng:rw
  environment:
    - SEARXNG_BASE_URL=http://searxng:8080/
  # Uncomment to reach the SearXNG UI from the host for debugging:
  # ports:
  #   - "8081:8080"
  restart: unless-stopped
```

Both `mintify-rag-service` and `open-webui` declare `searxng` in their `depends_on`. Inside the
Docker network it is reachable at `http://searxng:8080`.

### Required settings (`searxng/settings.yml`)

Two non-default choices are **required** for the backend's server-to-server calls to work, plus one
that makes search reliable:

```yaml
use_default_settings: true   # keep the full default engine list (Google, Bing, DuckDuckGo,
                             # Wikipedia, …); SearXNG aggregates + rotates, so a single engine
                             # rate-limiting no longer kills the search

server:
  secret_key: "…"            # generate your own with `openssl rand -hex 32`
  limiter: false             # internal-only service → no per-IP rate limiting; otherwise SearXNG
                             # blocks the backend's calls as if they were bots
  image_proxy: false

search:
  safe_search: 0
  formats:
    - html                   # so the SearXNG UI still works if you expose the debug port
    - json                   # REQUIRED — the backend calls /search?...&format=json

ui:
  static_use_hash: true
```

- **`formats` must include `json`** — default SearXNG builds only return HTML and the backend's
  JSON request would fail.
- **`limiter: false`** — otherwise SearXNG's bot detection rejects the backend's repeated
  server-side queries.
- **`use_default_settings: true`** — aggregating across many engines avoids the single-engine
  rate-limit problem (e.g. DuckDuckGo throttling) that plagues a one-engine setup.

---

## Environment variables

| Variable | Container | Value | Purpose |
|---|---|---|---|
| `SEARXNG_URL` | `mintify-rag-service` | `http://searxng:8080` | Where `WebSearcher` sends its JSON queries. |
| `ENABLE_WEB_SEARCH` | `open-webui` | `true` | Keeps the 🌐 toggle visible in the UI and the `web_search` flag flowing through `metadata.features`. |
| `MINTIFY_BACKEND_WEB_SEARCH` | `open-webui` | `true` | Custom flag — tells OWUI's middleware to **skip** its own web fetch (the rag-backend does it instead). |

---

## The WebSearcher service

`rag-backend/app/services/web_search.py` is the heart of the feature. Its three-step design:

1. **Query SearXNG's JSON API** → top-N `{url, title, snippet}` (`_searxng`, `web_search.py:95`).
   Queries are sent with `language="de"` for German-relevant results.
2. **Fetch + extract** each result page concurrently and pull the **main article text** out with
   trafilatura (`_fetch_extract`, `web_search.py:125`):
   ```python
   extracted = trafilatura.extract(
       resp.text,
       url=url,
       include_comments=False,
       include_tables=True,   # fixtures / schedules are often tables
       favor_recall=True,     # prefer a bit more content over strictness
   )
   ```
3. **Snippet fallback** — when a page yields too little usable text (JS-only page, anti-bot block,
   timeout), fall back to the SearXNG snippet, which often already states the answer
   (`web_search.py:148`).

No headless browser is needed; trafilatura works on the static HTML. Pages are fetched in a small
`ThreadPoolExecutor` (`max_workers = min(8, len(hits))`) while preserving SearXNG's ranking order
(`web_search.py:66`). Results are returned as `WebResult` dataclasses (`url`, `title`, `text`) and
capped by a total character budget.

A bot-resistant browser `User-Agent` is sent on every request, and `_normalize()` tidies the
extracted text (trim, collapse runs of spaces, cap consecutive blank lines while keeping paragraph
breaks).

### Tunables (`rag-backend/app/config.py`)

```python
searxng_url: str = "http://searxng:8080"
web_search_results: int = 4          # top-N SearXNG hits to fetch
web_fetch_timeout: float = 8.0       # seconds per SearXNG query / page fetch
web_doc_char_cap: int = 4000         # max chars kept per page
web_total_char_cap: int = 16000      # max chars across all pages
web_min_doc_chars: int = 120         # below this → fall back to the SearXNG snippet
web_user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) …"
```

The `WebSearcher` singleton is built once at startup in `rag-backend/app/dependencies.py` and
injected into the `/api/pipe` endpoint via `Depends(get_web_searcher)`.

---

## Prompt injection & citation

`rag-backend/app/routers/rag.py` consumes the results. The request model carries the flag:

```python
class PipeRequest(BaseModel):
    ...
    web_search: bool = False   # True when the user enabled 🌐 for this turn
```

When set, the endpoint runs the search and formats the results into numbered source blocks
(`_format_web_context`, `rag.py:43`):

```python
def _format_web_context(results: list[WebResult]) -> str:
    """Numbered source blocks the model can cite as [Quelle N]."""
    return "\n\n".join(
        f"[Quelle {i}] {r.title}\nURL: {r.url}\n{r.text}"
        for i, r in enumerate(results, 1)
    )
```

These are wrapped in a German preamble that tells the model the content is fresher than its
training knowledge and to cite sources as `[Quelle N]`:

```python
WEB_CONTEXT_BLOCK = (
    "\n\n[WEB-RECHERCHE]\n"
    "Die folgenden Auszüge stammen aus einer aktuellen Internet-Suche "
    "(Stand: heute). Sie sind aktueller als dein Trainingswissen — stütze deine "
    "Antwort darauf und zitiere die genutzten Quellen als [Quelle N].\n\n"
    "{web}\n[/WEB-RECHERCHE]\n"
)
```

> **Why it's appended *after* the `{context}` formatting** (`rag.py:132`): the document-retrieval
> `[KONTEXT]` block is filled first via `chosen.format(context=context)`. The web block is then
> appended and only fills its own `{web}` placeholder, so any literal `{ }` characters inside the
> fetched web text are inserted verbatim and never re-scanned by `str.format()` — which would
> otherwise raise `KeyError`.

Finally, regardless of whether the model cites them, a **Quellen** footer is always appended to the
streamed answer so the user sees which pages were used (`_quellen_footer`, `rag.py:51`):

```python
def _quellen_footer(results: list[WebResult]) -> str:
    lines = "\n".join(f"{i}. {r.url}" for i, r in enumerate(results, 1))
    return f"\n\n---\n**Quellen ({len(results)} Webseiten durchsucht):**\n{lines}"
```

---

## Pipeline & frontend touchpoints

**Pipeline** (`pipelines/mintify_pipeline.py`) — reads the toggle from the request body and forwards
it as `PipeRequest.web_search`:

```python
def _web_search_enabled(body: dict) -> bool:
    """True when the user toggled 🌐 web search for this turn.

    OWUI signals it at body.metadata.features.web_search.
    """
    meta = body.get("metadata") if isinstance(body, dict) else None
    feats = meta.get("features") if isinstance(meta, dict) else None
    return bool(feats.get("web_search")) if isinstance(feats, dict) else False
```

**OWUI middleware guard** (`open-webui-src/backend/open_webui/utils/middleware.py`) — skips OWUI's
own fetch when the MINTIFY flag is set:

```python
# MINTIFY: web search is handled by the rag-backend (SearXNG + trafilatura), not OWUI.
if (
    "web_search" in features
    and features["web_search"]
    and os.environ.get("MINTIFY_BACKEND_WEB_SEARCH", "false").lower() != "true"
):
    form_data = await chat_web_search_handler(request, form_data, extra_params, user)
```

**Frontend toggle** (`open-webui-src/src/lib/components/chat/MessageInput.svelte`) — the 🌐 button is
shown only when `$config.features.enable_web_search` is on **and** the user is an admin or has the
`web_search` permission. Clicking it flips `webSearchEnabled`, which OWUI propagates into
`metadata.features.web_search`.

**Results panel** (`open-webui-src/src/lib/components/chat/Messages/ResponseMessage/WebSearchResults.svelte`)
— renders a collapsible panel under the answer with the query and clickable source links.

---

## How to test / debug

1. In a chat, click the 🌐 **Web Search** button and ask something current (e.g. a recent
   news/finance question that the model couldn't know from training).
2. Watch the backend logs:
   ```bash
   docker logs -f mintify-rag-service
   ```
   Look for `web_search: true`, the `RAW WEB CONTENT RETRIEVED FROM THE INTERNET` banner, and
   `web_search: N usable sources` / `N chars injected as [WEB-RECHERCHE]`.
3. Confirm the streamed answer ends with the **Quellen** footer listing the source URLs.
4. To inspect SearXNG directly, temporarily uncomment the `8081:8080` port mapping in the `searxng`
   service, `docker compose up -d searxng`, and open `http://localhost:8081`. You can also hit the
   JSON API the backend uses:
   ```bash
   curl 'http://localhost:8081/search?q=test&format=json&language=de'
   ```

---

## Related docs

- `../.claude/CLAUDE.md` — overall MINTIFY architecture and request flow.
- `rag-flow-refactor.md` — the RAG pipeline this feature plugs into.
