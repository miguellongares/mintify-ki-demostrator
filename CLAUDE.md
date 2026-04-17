# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

MINTIFY is a German-language RAG-based chat system for financial education at Hochschule Bonn-Rhein-Sieg. It runs as three Docker containers: Open Web UI (frontend), a custom Pipelines server (RAG + LLM), and Qdrant (vector DB). LLM inference runs on the host via Docker Model Runner (Apple Silicon Metal GPU — not inside a container).

## Common commands

```bash
# Start the stack
docker compose up -d

# Rebuild after frontend changes only
docker compose up --build -d open-webui

# Restart the pipeline after code changes (hot-reload usually picks it up, but restart forces it)
docker compose restart pipelines

# Upload a PDF for RAG indexing
./scripts/upload-document.sh path/to/file.pdf

# Watch pipeline logs (RAG trace, retrieved chunks, errors)
docker logs -f mintify-pipelines

# Check what chunks were retrieved for the last query
docker logs mintify-pipelines 2>&1 | grep -A 20 "pipe() called" | tail -30

# Check Qdrant vector count
curl http://localhost:6333/collections/mintify_rag

# Pull the LLM model (one-time setup)
docker model pull ai/qwen2.5
```

## Architecture

### Request flow

Every chat message goes through this path:
1. Open Web UI (`mintify-webui`, port 3000) sends the message to the Pipelines server as an OpenAI-compatible request.
2. The Pipelines server calls `pipe()` in `pipelines/mintify_pipeline.py`.
3. `pipe()` embeds the user query with `all-MiniLM-L6-v2`, searches Qdrant (`mintify_rag` collection, cosine similarity), injects the top-K chunks into the system prompt, then streams a response from Docker Model Runner via the OpenAI client.
4. Docker Model Runner (`http://model-runner.docker.internal/engines/llama.cpp/v1`) runs `ai/qwen2.5` on the host GPU using Metal.

### The pipeline (`pipelines/mintify_pipeline.py`)

This is the only custom code that runs at inference time. It is a **pipe-type** Open Web UI pipeline — it appears directly as a model in the chat dropdown ("MINTIFY – Finanzielle Bildung"), not as a filter. Key points:

- `on_startup()` is `async def` — loads the SentenceTransformer, connects Qdrant, creates the collection if missing (384-dim cosine), connects the OpenAI client, indexes all PDFs in `/app/uploads`.
- `pipe()` must be a **regular `def`** (not async) — returns a `Generator` for streaming.
- Do NOT set `self.type` on the pipeline class — doing so hides it from the model dropdown.
- The `Valves` inner class (Pydantic `BaseModel`) exposes editable settings in Admin Panel → Pipelines. Values are persisted to `pipelines/mintify_pipeline/valves.json` and loaded on init.
- PDF IDs in Qdrant are `int(md5_hex, 16) % (2**53) + page*1000 + chunk_idx` — deterministic so re-indexing the same file overwrites existing points instead of duplicating them. Do not change this scheme without also clearing the Qdrant collection.
- Use `qdrant_client.query_points()` (not `search()` — it was removed in qdrant-client ≥ 1.10).

### Hot-reload behaviour

The Pipelines container mounts `./pipelines:/app/pipelines` as a bind volume. The pipelines server watches for file changes and reloads the module automatically. If hot-reload doesn't pick up a change, delete `pipelines/__pycache__/` and run `docker compose restart pipelines`.

### Data directories

`./data/` is bind-mounted and gitignored:
- `data/uploads/` → `/app/uploads` in the pipelines container — PDFs to index
- `data/qdrant/` → Qdrant storage — **do not delete while the stack is running**
- `data/open-webui/` → OWUI SQLite database, user accounts, cache

Wiping `data/qdrant/` clears all vectors; the pipeline will re-index on the next startup.

### Open Web UI source

`open-webui-src/` is the full Open Web UI v0.6.5 SvelteKit source, built inside Docker via its own `Dockerfile`. `OLLAMA_BASE_URL` is set to empty in `docker-compose.yml` to disable OWUI's direct Ollama connection — all chat routes through the pipeline. The pipeline is registered via `OPENAI_API_BASE_URL=http://pipelines:9099` and `OPENAI_API_KEY=mintify-pipeline-key`.

## Key configuration

All pipeline tunables are in `pipelines/mintify_pipeline/valves.json` (or editable live in Admin Panel → Pipelines → MINTIFY):

| Valve | Purpose |
|---|---|
| `OLLAMA_BASE_URL` | LLM base URL (without `/v1`) — default: Docker Model Runner internal endpoint |
| `LLM_MODEL` | Model name passed to the OpenAI-compatible API |
| `EMBEDDING_MODEL` | SentenceTransformer model name (must match the 384-dim collection) |
| `TOP_K` | Number of chunks retrieved per query |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | PDF chunking parameters |
| `SYSTEM_PROMPT` | German system prompt; must contain `{context}` placeholder |

## Important constraints

- PyTorch must be installed in a **separate `RUN` layer** in `pipelines/Dockerfile` using `--index-url https://download.pytorch.org/whl/cpu`. Mixing it with other packages in the same `RUN` corrupts pip's dependency resolution.
- The Open Web UI build requires `ENV NODE_OPTIONS=--max-old-space-size=4096` before `RUN npm run build` to avoid OOM errors during the Svelte build.
- `PIPELINES_API_KEY` in the pipelines container must match `OPENAI_API_KEY` in the OWUI container.
