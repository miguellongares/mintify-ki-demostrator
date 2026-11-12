# MINTIFY KI-Demonstrator

A German-language AI tutor for **financial education**, developed at Hochschule Bonn-Rhein-Sieg.
Learners chat with an LLM in the browser; every answer is grounded in course documents (and,
optionally, in the learner's own files or a live web search), rated by three independent LLM
judges, and can be read aloud.

**Live:** https://mintify.verbraucherinformatik.de

---

## Features (state of `main`, November 2026)

| Feature | What the user sees | Where it lives |
|---|---|---|
| **RAG chat** | Answers grounded in the knowledge bases attached to the model, with citations | `rag-backend` (`routers/rag.py`) |
| **Hybrid retrieval** | Finds both meaning-based and exact-term matches (e.g. FIN, HSN, Kaufpreis in scanned forms) | Qdrant dense (e5) + BM25, fused with RRF (`services/retriever.py`) |
| **Query rewriting** | Follow-up questions ("und was kostet das?") work; everyday words are mapped to document vocabulary | `services/query_rewriter.py` |
| **Structure-aware chunking** | Tables, lists and sections stay intact in citations | `services/chunker.py` |
| **Any file type** | PDF, DOCX, XLSX, scans (OCR) … uploaded via OWUI are indexed automatically | OWUI extraction (docling) → webhook → `services/indexer.py` |
| **🌐 Web search** | Per-turn toggle; answers cite current web sources | SearXNG + trafilatura (`services/web_search.py`) |
| **💡 Nachdenken** | Per-turn toggle; model reasons first, shown as a collapsed "Gedankengang" | Qwen3.5 `enable_thinking` (`services/llm_client.py`) |
| **Bewertung panel** | Right sidebar: three judges rate each answer *gut / mittel / schlecht*, against the same context the answer used | `routers/judge.py`, `JudgementPanel.svelte` |
| **Text-to-speech** | OWUI's speaker button reads the answer in German | `routers/tts.py` → Qwen3-TTS (`tts-service/`) |
| **Persönliche Dateien** | Drag & drop zone in the sidebar; files are private per user and used in that user's RAG | `routers/personal_docs.py`, `Sidebar/PersonalFiles.svelte` |
| **Kompetenzniveau** | The model diagnoses each learner's level (0–3) and adapts its explanations; can be disabled per model | `services/level_tool.py`, `services/knowledge_level.py` |
| **Re-index button** | Admin Panel → Settings → Documents → "Alle Dokumente neu indexieren (Qdrant)" | `routers/index.py::reindex_all` |

**Not on `main`:** the *"Erkunde deine Verträge"* contract dashboard + `vertrag-backend` live on the
`Verträge_interface` branch only.

---

## Architecture

```
Browser
  ─ https ─→ Caddy (production only)
              ├─ /rag-api/*  ─→ mintify-rag-service   (Bewertung, Persönliche Dateien, sources)
              └─ everything  ─→ mintify-webui (Open WebUI)
                                   │
                                   ├─→ mintify-pipelines ─→ mintify-rag-service ─┬─→ Qdrant        vector + BM25 search
                                   │     (thin proxy)         (RAG backend)       ├─→ SearXNG       web search
                                   │                                              ├─→ vLLM  :8002  ┐
                                   ├─→ TTS button ──────────→ mintify-rag-service ─→ TTS   :8006  ├ RunPod GPU,
                                   └─→ file upload: docling extraction ────────────→ docling :5001 ┘ via SSH tunnel
                                         └─ webhook /api/index ─→ mintify-rag-service (chunk, embed, upsert)
```

Every layer streams — tokens flow back to the browser as vLLM produces them.

### Containers (`docker-compose.yml`)

| Container | Host port | Role |
|---|---|---|
| `mintify-webui` | `127.0.0.1:3000` | Custom Open WebUI **v0.6.5** fork (`open-webui-src/`) — the version is a project requirement |
| `mintify-pipelines` | `127.0.0.1:9099` | OWUI Pipelines server; `mintify_pipeline.py` forwards chats to the RAG backend |
| `mintify-rag-service` | `127.0.0.1:8088` | FastAPI: RAG, indexing, judges, TTS proxy, personal files, Kompetenzniveau |
| `mintify-qdrant` | `127.0.0.1:6333` | Vector DB, collection `mintify_rag` (named vectors `dense` + `bm25`) |
| `mintify-searxng` | — (internal) | Meta search engine for 🌐 |

All ports are bound to loopback; in production only Caddy (80/443) is public.

### GPU services on RunPod (reached through an SSH tunnel)

| Port | Service |
|---|---|
| 8002 | vLLM serving `cyankiwi/Qwen3.5-27B-AWQ-4bit` (16k context, reasoning parser, thinking off by default) |
| 8006 | Qwen3-TTS server (source in `tts-service/`) |
| 5001 | docling-serve — document → Markdown extraction incl. OCR |

`scripts/init_models.sh` starts all three on the pod and opens the tunnel.

---

## How a chat request is answered

1. **Rewrite** the user message into a search query (resolves follow-ups, expands vocabulary).
2. **Embed** it twice: dense 384-dim (`intfloat/multilingual-e5-small`) and BM25 sparse (German stemmer).
3. **Hybrid search** in Qdrant, filtered to the model's knowledge-base files **or** the caller's own personal files.
4. **Build the prompt:** system prompt + retrieved chunks (+ web results, + learner profile).
5. **Stream** the answer from vLLM back through every layer; the context is cached so the judges grade against it.

Ingestion is event-driven, not per request: OWUI extracts every uploaded file and calls
`POST /api/index` (upload) / `DELETE /api/index/{id}` (delete) on the RAG backend.

---

## Project structure

```
mintify-ki-demostrator/
├── docker-compose.yml          the whole stack
├── docker-compose.debug.yml    attach debugpy to the RAG backend (port 5678)
├── pipelines/
│   ├── mintify_pipeline.py     OWUI pipeline → POST /api/pipe
│   └── mintify_pipeline/valves.json
├── rag-backend/app/
│   ├── main.py                 app, CORS, proxy guard, routers
│   ├── proxy_guard.py          blocks internal routes for requests coming through Caddy
│   ├── config.py               all env settings
│   ├── dependencies.py         singletons created once at startup
│   ├── routers/                rag, index, judge, tts, models, personal_docs,
│   │                           knowledge_level, retrieved_sources
│   └── services/               embedder, sparse_embedder, retriever, indexer, chunker,
│                               query_rewriter, llm_client, judge_client, context_cache,
│                               web_search, tts_client, extractor, owui_client, owui_auth,
│                               personal_docs, knowledge_level, level_tool, dates
├── open-webui-src/             custom OWUI fork (Svelte frontend + Python backend)
├── tts-service/                Qwen3-TTS FastAPI server, deployed to RunPod (see RUNPOD_SETUP.md)
├── searxng/                    SearXNG settings
├── scripts/
│   ├── init_models.sh          start vLLM + TTS (+ docling) on RunPod and tunnel the ports
│   ├── server-runpod-tunnel.sh production: runs init_models.sh in a respawn loop (tmux)
│   └── reset_rag.sh            DESTRUCTIVE: wipe all files + Qdrant
├── documents_about_the_project/  design notes per feature (see below)
└── data/                       gitignored, bind-mounted runtime data
    ├── open-webui/             OWUI database, uploads, cache
    ├── qdrant/                 vector storage
    ├── personal_docs/          per-user files + SQLite
    └── knowledge_level/        Kompetenzniveau SQLite
```

---

## Running locally

**Prerequisites:** Docker + Docker Compose, SSH access to the RunPod GPU pod.

1. **Create `.env`** in the repo root:
   ```bash
   WEBUI_SECRET_KEY=<random string>
   OWUI_API_KEY=<OWUI admin API key — needed for indexing, create it after first login>
   # VLLM_BASE_URLS=http://host.docker.internal:8002   (default)
   ```
2. **Start the GPU services and tunnel** (keep it running in its own terminal):
   ```bash
   ./scripts/init_models.sh
   ```
3. **Start the stack:**
   ```bash
   docker compose up -d
   ```
4. Open http://localhost:3000. The first account becomes admin. Then:
   - Settings (user menu) → Account → API Keys: create a key as the admin → put it in `.env` as `OWUI_API_KEY` → `docker compose up -d mintify-rag-service`
   - Admin Panel → Models: enable the MINTIFY pipeline model (eye icon), attach knowledge bases, set the system prompt
5. After a fresh Qdrant (or any schema change) re-index everything via the **Reindex** button
   (Admin Panel → Settings → Documents).

Frontend hot reload (`npm run dev` on :5173) is described in
`documents_about_the_project/frontend-dev-workflow.md`.

---

## Configuration

Set in `docker-compose.yml` / `.env`; defaults live in `rag-backend/app/config.py`.

| Variable | Default | Purpose |
|---|---|---|
| `VLLM_BASE_URLS` | `http://host.docker.internal:8002` | Chat LLM (comma-separated for several) |
| `OWUI_API_KEY` | — | OWUI **admin** key; without it ingestion fails with 401 |
| `TTS_URL` | `http://host.docker.internal:8006` | Qwen3-TTS |
| `DOCLING_SERVER_URL` | `http://host.docker.internal:5001` | Text extraction (OWUI + personal files) |
| `DOCLING_FORCE_OCR` | `false` | **Keep false** — forcing OCR discards the PDF text layer (glued words, lost umlauts) |
| `DOCLING_OCR_PRESET` | `auto` | **Keep auto** — other presets fail on this docling deployment |
| `MIN_SCORE` | `0.80` | Cosine floor for the dense arm (BM25 hits bypass it) |
| `CHUNK_SIZE` / `CHUNK_MAX_SIZE` / `CHUNK_OVERLAP` | 800 / 1600 / 100 | Chunker targets (characters) |
| `QUERY_REWRITE` | `true` | Kill-switch for query rewriting |
| `KOMPETENZ_ENABLED` | `true` | Kill-switch for the Kompetenzniveau feature |

Pipeline valves (`BACKEND_URL`, `TOP_K`, `SYSTEM_PROMPT`) are editable in Admin Panel → Pipelines.

---

## Production

Hetzner server, repo at `/home/mintify/mintify-ki-demonstrator`, HTTPS via **Caddy**:

```caddyfile
mintify.verbraucherinformatik.de {
    handle /rag-api/* {
        uri strip_prefix /rag-api
        reverse_proxy localhost:8088
    }
    handle {
        reverse_proxy localhost:3000
    }
}
```

The RunPod tunnel runs permanently in tmux via `scripts/server-runpod-tunnel.sh`.
UFW must allow the Docker bridge networks (`172.17.0.0/16`, `172.22.0.0/16`) to reach
ports 8002 / 8006 / 5001 on the host.

**Deploy a change:**
```bash
git pull
docker compose restart mintify-rag-service       # rag-backend/app is bind-mounted, no rebuild
docker compose build open-webui && docker compose up -d open-webui   # after Svelte / OWUI changes
docker compose restart pipelines                 # after pipeline changes
```

### Security notes

- Browser-facing backend URLs are environment-aware: `http://localhost:8088` in dev,
  `${origin}/rag-api` in production (`open-webui-src/src/lib/utils/ragApi.ts`). Never hardcode ports.
- **`proxy_guard.py`:** requests arriving through Caddy may only reach an allowlist
  (`judge`, `personal-docs`, `knowledge-level`, `retrieved-sources`, `health`); every other route
  returns 404 from the internet. **A new browser endpoint must be added to `PUBLIC_PATHS`**, or it
  works on localhost but 404s in production.
- Personal files, Kompetenzniveau and admin actions authenticate with the caller's OWUI token.

Quick check that internal routes are not public (expect `404`):
```bash
curl -s -o /dev/null -w '%{http_code}\n' https://mintify.verbraucherinformatik.de/rag-api/api/reset-collection
```

---

## Useful commands

```bash
docker logs -f mintify-rag-service                         # RAG backend
docker logs mintify-rag-service 2>&1 | grep "\[Judge\]"     # judges
docker logs mintify-rag-service 2>&1 | grep "\[Kompetenz\]" # level diagnosis
docker logs mintify-rag-service 2>&1 | grep "\[Guard\]"     # blocked public requests
curl http://localhost:6333/collections/mintify_rag         # Qdrant point count
rm -rf pipelines/__pycache__ && docker compose restart pipelines   # force pipeline reload
```

---

## Further documentation

Per-feature design notes in `documents_about_the_project/`:

| Topic | File |
|---|---|
| Chunking & indexing | `chunking-and-indexing.md` |
| RAG flow refactor | `rag-flow-refactor.md` |
| Bewertung judges | `bewertung-judges.md` |
| Web search (SearXNG) | `online-search-searxng.md`, `tutorial-build-internet-search.md` |
| Persönliche Dateien | `personal-files-sidebar.md` |
| Kompetenzniveau | `kompetenzniveau-knowledge-level.md` |
| File uploads, dedup & grants | `owui-dragdrop-files.md`, `file-dedup-user-file-grants.md` |
| Frontend dev & branding | `frontend-dev-workflow.md`, `frontend_branding_changes.md` |
| Debugging the backend | `debugpy-setup.md` |
| Docker port exposure | `SECURITY-docker-port-exposure.md` |
| Project requirements | `requiremets.md` |
