# Tutorial: Build an LLM Internet-Search Feature from Scratch (SearXNG + trafilatura)

> **What this is.** A guided, hands-on curriculum for rebuilding MINTIFY's 🌐 internet-search
> feature as a small standalone app in a **new repository**, so you learn every technology and
> technique it uses. You build it yourself, milestone by milestone, with Claude helping at each
> step. This is a *simplified* version — no Open Web UI, no pipeline layer — just the core idea:
> **question → web search → fetch & clean pages → feed to an LLM → cited answer.**

---

## How to use this file

1. Create a **new empty folder**, run `git init`, and open it in Claude Code (or paste this whole
   file as your first message to Claude in that project).
2. Tell Claude: *"This is my learning roadmap. Let's start with Milestone 0. Before writing any
   code, explain the concepts to me, then we build it together."*
3. **Work one milestone at a time.** Don't skip ahead. Each milestone ends with a *Verify* step —
   don't move on until it passes.
4. For each milestone, the **"Ask Claude"** prompts are starting points — adapt them, and always
   ask *"why?"* when something is unclear. The goal is understanding, not just a working app.
5. At the end there's a **Concept → real-repo map** so you can open the real MINTIFY code and see
   the same ideas at production scale.

---

## What you'll build

A tiny Python app with one HTTP endpoint. When you send it a question with web search enabled, it:

```
  your question
      │
      ▼
  ┌─────────────────────────────────────────────────────────┐
  │  FastAPI app (your code)                                  │
  │                                                           │
  │   1. ask SearXNG for the top results      ── httpx ──►  SearXNG (Docker)
  │   2. fetch each result page concurrently  ── httpx ──►  the open web
  │   3. extract clean article text           ── trafilatura                  │
  │   4. build a prompt with the web text + [Quelle N] cites                  │
  │   5. stream the answer from the LLM       ── Anthropic SDK ──►  Claude    │
  │   6. append a "Sources" footer with the URLs                              │
  └─────────────────────────────────────────────────────────┘
      │
      ▼
  streamed answer + list of sources
```

This is the same shape as the real feature — just without the Open Web UI front-end and the
OWUI pipeline proxy in front of it.

---

## Tech & concepts you'll learn

| Area | Tool / concept | Why it's here |
|---|---|---|
| Containers | **Docker** + `docker compose` | Run SearXNG with one command; later, containerize your own app |
| Web search | **SearXNG** metasearch + its **JSON API** | Aggregates many engines (Google, Bing, DuckDuckGo, …) behind one query |
| HTTP client | **httpx** | Call SearXNG and fetch web pages from Python |
| Content extraction | **trafilatura** | Pull the *main article text* out of an HTML page (drop nav, ads, cookie banners) |
| Concurrency | `concurrent.futures.ThreadPoolExecutor` | Fetch several pages at once → latency ≈ slowest page, not the sum |
| Web framework | **FastAPI** + **uvicorn** | Serve the feature over HTTP |
| Config | **pydantic-settings** | Keep all tunables (URLs, timeouts, limits) in one typed place |
| Architecture | **Layered design** (config / services / routers / dependencies) | The same structure the real repo uses — separates HTTP from logic |
| Dependency injection | FastAPI `Depends` + build-once singletons | Create expensive objects once at startup, not per request |
| LLM | **Anthropic SDK** + token **streaming** | Get a live, cited answer; stream tokens so the UI feels fast |
| Prompting | Prompt injection + numbered **citations** | Teach the model to ground its answer on fresh web text and cite `[Quelle N]` |
| Robustness | character budgets + **snippet fallback** | Keep the context focused; degrade gracefully when a page can't be read |

---

## Prerequisites & setup

- **Python 3.11+** and a virtual environment (`python -m venv .venv && source .venv/bin/activate`).
- **Docker Desktop** running (for SearXNG).
- **An LLM to answer with.** Two options:
  - **Recommended for learning: the Claude API** (Anthropic SDK). Get an API key from
    `https://console.anthropic.com`, set `export ANTHROPIC_API_KEY=sk-ant-...`. We'll use model
    `claude-opus-4-8` with streaming. This is the simplest path — no GPU, no local server.
  - *(What the real repo uses)*: an **OpenAI-compatible** endpoint (vLLM) reached over HTTP.
    MINTIFY runs `cyankiwi/Qwen3.5-27B-AWQ-4bit` on RunPod. You don't need this for the tutorial,
    but Milestone 6 notes how the code differs.

> The German prompt wording (`[WEB-RECHERCHE]`, `[Quelle N]`, "Quellen") is kept from the real
> project so the mapping is 1:1. Feel free to translate to English in your own build.

---

## Milestones

Each milestone: **Goal · Concepts · Build · Ask Claude · Verify.**

### M0 — Repo & environment

- **Goal:** a clean project skeleton you can run.
- **Concepts:** virtualenv, `requirements.txt`, `.gitignore`, `.env` for secrets.
- **Build:** `git init`; create a venv; a `requirements.txt` with:
  `httpx`, `trafilatura`, `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `anthropic`.
  Add a `.gitignore` (`.venv/`, `__pycache__/`, `.env`).
- **Ask Claude:** *"Set up a Python project skeleton with a venv and these dependencies. Explain
  what each package is for and why we pin minimum versions."*
- **Verify:** `pip install -r requirements.txt` succeeds; `python -c "import httpx, trafilatura, fastapi, anthropic"` runs with no error.

### M1 — Run SearXNG in Docker

- **Goal:** a local SearXNG you can query for JSON.
- **Concepts:** Docker images, `docker compose`, bind-mounted config, container networking.
- **Build:** a `docker-compose.yml` with a `searxng` service (`image: searxng/searxng:latest`),
  a bind-mounted `./searxng:/etc/searxng` volume, and a published port for now
  (`"8081:8080"`) so you can hit it from your laptop. Create `searxng/settings.yml` with the
  **three settings that matter**:
  ```yaml
  use_default_settings: true     # keep the full engine list (Google, Bing, DuckDuckGo, …)
  server:
    secret_key: "<run: openssl rand -hex 32>"
    limiter: false               # internal use → don't block our own server-to-server calls
  search:
    formats:
      - html
      - json                     # REQUIRED: we call the API with format=json
    safe_search: 0
  ```
- **Ask Claude:** *"Why does SearXNG need `formats: [json]` and `limiter: false` for a
  programmatic client? What does `use_default_settings: true` buy me over picking one engine?"*
- **Verify:** `docker compose up -d searxng`, then
  `curl 'http://localhost:8081/search?q=test&format=json' | head` returns JSON with a `results`
  array. *(If you get HTML or a 403, re-check the two required settings.)*

### M2 — Query SearXNG from Python

- **Goal:** a function that returns the top N hits as `{url, title, snippet}`.
- **Concepts:** `httpx.get` with query params, custom `User-Agent`, parsing JSON, timeouts.
- **Build:** a function `searxng(query)` that GETs `/search` with
  `params={"q": query, "format": "json", "language": "de"}`, raises on HTTP errors, and maps
  `data["results"]` → a list of dicts (`url`, `title`, `snippet` from the `content` field),
  capped at N (start with 4).
- **Ask Claude:** *"Walk me through httpx: what does `raise_for_status()` do, why send a
  browser-like User-Agent, and why set a timeout? Show me how to handle the request failing
  without crashing."*
- **Verify:** call it with a real query and print the URLs/titles. *(Mirrors `_searxng` in the real
  `web_search.py`.)*

### M3 — Fetch & extract clean page text

- **Goal:** turn a result URL into clean main-article text.
- **Concepts:** fetching HTML, **trafilatura** main-content extraction, text normalization,
  graceful fallback.
- **Build:** a function `fetch_extract(hit)` that GETs the page (follow redirects), then calls
  `trafilatura.extract(html, url=url, include_comments=False, include_tables=True, favor_recall=True)`.
  Normalize the result (trim, collapse runs of spaces, cap blank lines). If the extracted text is
  too short (e.g. < 120 chars — a JS-only page or anti-bot block), **fall back to the SearXNG
  snippet**. Cap each page to a max length (e.g. 4000 chars).
- **Ask Claude:** *"What does trafilatura actually do to an HTML page, and why is that better than
  dumping the raw HTML into the prompt? Explain each `extract()` flag I'm using."*
- **Verify:** feed it one of the URLs from M2 and confirm you get readable prose, not nav menus or
  cookie text. *(Mirrors `_fetch_extract` + `_normalize`.)*

### M4 — Make it concurrent

- **Goal:** fetch all N pages at once while keeping SearXNG's ranking order.
- **Concepts:** `ThreadPoolExecutor`, `as_completed`, preserving order via index slots, an overall
  character budget across all pages.
- **Build:** a `search(query)` that calls M2, then fetches all hits via a
  `ThreadPoolExecutor(max_workers=min(8, len(hits)))`, writing each result into a fixed-position
  list (so ranking is preserved even though they finish out of order). Drop empties and stop once a
  total character budget (e.g. 16000) is reached. Return a small `WebResult` dataclass list
  (`url`, `title`, `text`).
- **Ask Claude:** *"Why is threading (not asyncio) fine here, and why does total latency become
  'the slowest page' instead of 'the sum of all pages'? How do I keep results in rank order when
  they finish in random order?"*
- **Verify:** time `search()` with 4 results — it should take roughly as long as the single
  slowest page, not 4× a page. *(Mirrors `WebSearcher.search`.)*

### M5 — Restructure as a layered FastAPI service

- **Goal:** the same architecture the real repo uses, so the concepts transfer.
- **Concepts:** separation of concerns, `pydantic-settings`, dependency injection, build-once
  singletons.
- **Build:** split your code into:
  - `config.py` — a `pydantic-settings` `Settings` class holding `searxng_url`,
    `web_search_results`, `web_fetch_timeout`, `web_doc_char_cap`, `web_total_char_cap`,
    `web_min_doc_chars`, `web_user_agent` (mirror the real defaults: 4 / 8.0s / 4000 / 16000 /
    120).
  - `services/web_search.py` — a `WebSearcher` class wrapping M2–M4 (takes its config in
    `__init__`).
  - `dependencies.py` — build **one** `WebSearcher` at startup; expose `get_web_searcher()`.
  - `routers/search.py` — a `POST /search` endpoint that takes `{query, web_search}` and uses
    `Depends(get_web_searcher)`.
  - `main.py` — the FastAPI app wiring the router in.
- **Ask Claude:** *"Why separate routers (HTTP) from services (logic) from config? What problem
  does building the WebSearcher once with `Depends` solve versus creating it per request?"*
- **Verify:** `uvicorn main:app --reload`, then `curl -X POST localhost:8000/search -d '{...}'`
  returns your `WebResult`s as JSON. *(Mirrors `config.py`, `dependencies.py`, `routers/`.)*

### M6 — LLM call + prompt injection with citations

- **Goal:** turn web results into a grounded, cited answer.
- **Concepts:** building a context block, numbered citations, the `str.format` brace-safety trick,
  calling the Claude API.
- **Build:** format the results into numbered blocks the model can cite:
  ```
  [Quelle 1] <title>
  URL: <url>
  <clean text>
  ```
  Wrap them in a German preamble (`[WEB-RECHERCHE] … zitiere die genutzten Quellen als [Quelle N]
  … [/WEB-RECHERCHE]`) and put it in the system prompt. Then call Claude:
  ```python
  import anthropic
  client = anthropic.Anthropic()          # reads ANTHROPIC_API_KEY
  with client.messages.stream(
      model="claude-opus-4-8",
      max_tokens=4096,
      system=system_with_web_context,
      messages=[{"role": "user", "content": query}],
  ) as stream:
      for text in stream.text_stream:
          ...  # yield/print tokens
  ```
- **Ask Claude:** *"Explain the `str.format` brace trap: why is it dangerous to `.format()` a
  string that contains web text with literal `{` `}` in it, and how does using a separate `{web}`
  placeholder avoid it? Also explain why I stream the response."*
- **The brace-safety point (important):** in the real repo the document-context block is filled
  with `.format(context=...)` first; the web block is appended **afterward** and only fills its own
  `{web}` placeholder — so stray `{` `}` inside fetched web text are never re-scanned by
  `str.format()` (which would otherwise raise `KeyError`). Learn this — it's a real bug class.
- *(If you use vLLM instead of Claude)*: swap the Anthropic client for the OpenAI SDK pointed at
  your endpoint (`OpenAI(base_url=..., api_key=...)`) and use `chat.completions.create(stream=True)`.
  The prompt-building logic is identical. *(Mirrors `_format_web_context` + `WEB_CONTEXT_BLOCK`.)*
- **Verify:** ask a current-events question with web search on; the answer should reference the
  fetched pages and include `[Quelle N]` markers.

### M7 — Stream the answer + a sources footer

- **Goal:** stream tokens to the client and always show which pages were used.
- **Concepts:** FastAPI `StreamingResponse`, a generator that yields tokens then a footer.
- **Build:** make `POST /search` return a `StreamingResponse` over a generator that yields the LLM
  tokens, then yields a footer:
  ```
  ---
  **Quellen (N Webseiten durchsucht):**
  1. <url>
  2. <url>
  ```
  Always append the footer even if the model didn't cite — the user should see the sources.
- **Ask Claude:** *"How does `StreamingResponse` work with a Python generator, and why append the
  sources footer after the model's tokens instead of asking the model to print the URLs?"*
- **Verify:** `curl -N` the endpoint and watch tokens arrive live, followed by the Quellen footer.
  *(Mirrors `token_generator` + `_quellen_footer` in the real `rag.py`.)*

### M8 (optional) — Containerize your app

- **Goal:** run your app and SearXNG together with one command.
- **Concepts:** a Python `Dockerfile`, multi-service `docker compose`, service-to-service URLs.
- **Build:** a `Dockerfile` for your FastAPI app; add it to `docker-compose.yml` next to
  `searxng`; set `searxng_url` to the internal `http://searxng:8080` (the in-network name) instead
  of `localhost:8081`; add `depends_on: [searxng]`.
- **Ask Claude:** *"Why does my app use `http://searxng:8080` inside Docker but I used
  `localhost:8081` from my laptop? Explain Docker's internal DNS and port publishing."*
- **Verify:** `docker compose up --build` brings up both; the endpoint works end-to-end in
  containers.

---

## Concept → real-repo map

Once you finish, open the real MINTIFY code and compare — the ideas are identical, just bigger:

| Milestone | Real file in this repo | What to compare |
|---|---|---|
| M1 | `docker-compose.yml`, `searxng/settings.yml` | The `searxng` service + the three required settings |
| M2 | `rag-backend/app/services/web_search.py` → `_searxng` | The JSON query |
| M3 | `rag-backend/app/services/web_search.py` → `_fetch_extract`, `_normalize` | trafilatura flags + snippet fallback |
| M4 | `rag-backend/app/services/web_search.py` → `search` | ThreadPoolExecutor + budgets |
| M5 | `rag-backend/app/config.py`, `dependencies.py`, `routers/` | Layered architecture + DI |
| M6 | `rag-backend/app/routers/rag.py` → `_format_web_context`, `WEB_CONTEXT_BLOCK` | Citation blocks + brace-safety |
| M7 | `rag-backend/app/routers/rag.py` → `token_generator`, `_quellen_footer` | Streaming + sources footer |
| — | `pipelines/mintify_pipeline.py`, `open-webui-src/...` | The OWUI/pipeline layer you *skipped* (a future project) |

For the full picture of how the real feature is wired end to end, read
`documents_about_the_project/online-search-searxng.md` in this repo.

---

## Stretch goals (once the basics work)

- **Tune language/region** — try `language=en`, or add a region param to SearXNG.
- **Cache results** — avoid re-fetching the same URL within a short window.
- **Better fallback** — when a page is JS-only, try a different result instead of just its snippet.
- **Add the OWUI layer** — the real project's biggest piece you skipped: a front-end toggle that
  sets a `web_search` flag, proxied through a pipeline to your backend. Tackle it as a second
  project once the standalone version is solid.
