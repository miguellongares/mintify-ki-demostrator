# MINTIFY – Architecture Overview

## What this system is

MINTIFY is a German-language RAG (Retrieval-Augmented Generation) chat system for financial education. Users ask questions in a browser, and the system answers using both an LLM and relevant passages retrieved from uploaded PDF documents.

---

## The three containers + one remote service

```
Browser
  │
  ▼
┌─────────────────────────────┐
│  open-webui  (port 3000)    │  SvelteKit frontend + Python backend
│  container: mintify-webui   │
└────────────┬────────────────┘
             │  HTTP POST /chat/completions   (OpenAI-compatible)
             ▼
┌─────────────────────────────┐
│  pipelines   (port 9099)    │  Custom RAG logic — the brain
│  container: mintify-pipelines│
└──────┬──────────────────────┘
       │                  │
       │  HTTP (REST)     │  HTTP (OpenAI-compatible)
       ▼                  ▼
┌─────────────┐   ┌─────────────────────────────────────┐
│  qdrant     │   │  vLLM on RunPod  (remote GPU)        │
│  port 6333  │   │  host.docker.internal:8000           │
│  vector DB  │   │  SSH tunnel → RunPod :8000           │
└─────────────┘   │  serves Qwen/Qwen2.5-0.5B-Instruct  │
                  └─────────────────────────────────────┘
```

---

## How a chat message travels through the system

1. **User types a message** in the browser (Open Web UI frontend).

2. **Open Web UI frontend** sends the message to its own **Python backend** (also inside the `open-webui` container).

3. **Open Web UI backend** forwards it as a standard OpenAI-format `POST /chat/completions` request to the **Pipelines server**.
   - This is wired via environment variables in `docker-compose.yml`:
     ```yaml
     OPENAI_API_BASE_URL=http://pipelines:9099
     OPENAI_API_KEY=mintify-pipeline-key
     ```

4. **The Pipelines server** receives the request and calls `pipe()` in `mintify_pipeline.py`.

5. **Inside `pipe()`** (the custom RAG logic):
   - Embeds the user query using `all-MiniLM-L6-v2` (runs inside the container).
   - Searches **Qdrant** (`http://qdrant:6333`) for the top-K most relevant PDF chunks.
   - Injects those chunks into the system prompt.
   - Sends the enriched conversation to **vLLM on RunPod** (`http://host.docker.internal:8000/v1`) as a streaming OpenAI-compatible request. The local port 8000 is forwarded to RunPod via SSH tunnel (`scripts/runpod-tunnel.sh`).
   - Streams the LLM's response back to Open Web UI.

6. **Open Web UI** streams the response tokens to the browser in real time.

---

## Key configuration (docker-compose.yml)

| Container | Talks to | Via |
|---|---|---|
| `open-webui` | `pipelines` | `http://pipelines:9099` (Docker internal DNS) |
| `pipelines` | `qdrant` | `http://qdrant:6333` (Docker internal DNS) |
| `pipelines` | vLLM on RunPod | `http://host.docker.internal:8000/v1` (SSH tunnel, see `scripts/runpod-tunnel.sh`) |

The API key (`mintify-pipeline-key`) must match in both containers — set once in `docker-compose.yml`.

---

## Where Open Web UI calls the Pipeline (the exact lines)

Open Web UI has no MINTIFY-specific code. It treats the pipeline exactly like any OpenAI-compatible API. The relevant lines are in the standard OWUI source:

**`open-webui-src/backend/open_webui/routers/openai.py`**
- **Line 588** — route definition:
  ```python
  @router.post("/chat/completions")
  async def generate_chat_completion(...)
  ```
- **Line 700–703** — the actual HTTP call to the pipeline:
  ```python
  r = await session.request(
      method="POST",
      url=f"{url}/chat/completions",   # url = http://pipelines:9099
      data=payload,
  ```
- **Lines 658–664** — if the model is a pipeline, user info is injected into the payload:
  ```python
  if "pipeline" in model and model.get("pipeline"):
      payload["user"] = { "name": user.name, "id": user.id, ... }
  ```

**`open-webui-src/src/lib/apis/openai/index.ts`**
- **Line 1** — the base URL constant used by the frontend:
  ```ts
  import { OPENAI_API_BASE_URL, ... } from '$lib/constants';
  ```
  All frontend API calls use `OPENAI_API_BASE_URL`, which at runtime resolves to the backend proxy — not directly to the pipeline. The frontend never calls the pipeline directly.

---

## The Pipeline file (`pipelines/mintify_pipeline.py`)

The entire custom logic lives here. Key methods:

| Method | When called | What it does |
|---|---|---|
| `on_startup()` | Container start | Loads embedding model, connects Qdrant & LLM, indexes all PDFs in `/app/uploads` |
| `pipe()` | Every chat message | Embeds query → searches Qdrant → builds prompt → streams LLM response |
| `_index_file()` | Startup + new uploads | Chunks PDF pages, embeds each chunk, upserts into Qdrant |
| `_retrieve_context()` | Inside `pipe()` | Calls `qdrant.query_points()` and formats retrieved chunks as context string |
| `_stream_vllm()` | Inside `pipe()` | Calls `llm_client.chat.completions.create(stream=True)` and yields tokens |

### PDF indexing (deterministic IDs)

Each chunk's Qdrant ID is computed as:
```python
id = int(md5_hex, 16) % (2**53) + page_num * 1000 + chunk_index
```
This means re-indexing the same file **overwrites** existing points instead of creating duplicates.

---

## Data persistence (bind mounts)

```
./data/uploads/    →  /app/uploads   in pipelines  (PDFs to index)
./data/qdrant/     →  Qdrant storage               (vectors — do not delete while running)
./data/open-webui/ →  OWUI SQLite DB               (user accounts, chat history)
./pipelines/       →  /app/pipelines in pipelines  (hot-reload on save)
```

---

## Valves — live configuration

Pipeline settings are editable at runtime via **Admin Panel → Pipelines** in the UI.
They are saved to `pipelines/mintify_pipeline/valves.json` and loaded on startup.

Key valves:

| Valve | Default | Effect |
|---|---|---|
| `VLLM_BASE_URL` | `http://host.docker.internal:8000` | vLLM endpoint (without `/v1`) |
| `LLM_MODEL` | `Qwen/Qwen2.5-0.5B-Instruct` | Model name sent to vLLM |
| `TOP_K` | `5` | How many PDF chunks are injected per query |
| `CHUNK_SIZE` | `800` | Characters per PDF chunk |
| `SYSTEM_PROMPT` | (German prompt) | Must contain `{context}` placeholder |
