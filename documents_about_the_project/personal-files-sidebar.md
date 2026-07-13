# Persönliche Dateien — per-user file store in the chat sidebar

This document explains the **"Persönliche Dateien"** feature (built July 2026 on
branch `model_schiberegler`): every logged-in user can drag & drop private files
onto a drop zone in the chat sidebar, browse them in a popup, and download or
delete them. Each user only ever sees their own files.

It also briefly documents the **start-screen persona/model selection rework**
(pill buttons in the navbar) that was implemented in the same session — see the
last section.

---

## TL;DR

| Question | Answer |
|---|---|
| Where is the UI? | Sidebar, between the chat list and the user footer: a dashed "Persönliche Dateien" tile |
| How do I upload? | Drag files onto the tile (instant upload) or click it → popup → "Hochladen" button |
| How do I see my files? | Click the tile → popup lists all files with size/date + download & delete buttons |
| Which backend serves it? | The **rag-backend** (`mintify-rag-service`) — NOT a new container |
| Where is the data? | `data/personal_docs/` on the host: SQLite DB + raw files (gitignored, survives rebuilds) |
| Who can see a file? | Only the uploader — every request is authenticated with the user's OWUI token |
| Is it part of RAG? | **Yes (since July 2026)** — text is extracted on upload, chunked, embedded into Qdrant, and retrieved **only for the owning user** |
| Who extracts the text? | The **same docling-serve instance OWUI uses** (RunPod, tunnel port 5001) via `services/extractor.py` — a 1:1 replica of OWUI's loader, with the same plain-text bypass |
| Size limit | 50 MB per file |

---

## Why the rag-backend (and not the vertrag-backend)?

The obvious home would have been the `vertrag-backend` (it is exactly a per-user
document store). But on `main` / `model_schiberegler` that service **does not
exist**: its sources live only on the `Verträge_interface` branch, and
`docker-compose.yml` has no vertrag service here.

The rag-backend was chosen instead because it:

- is already running (no new container, port, or Dockerfile),
- is already reachable from the browser in production via Caddy's existing
  `/rag-api/*` route (no Caddyfile change needed),
- already knows OWUI's base URL for token verification.

---

## The flow at a glance

```
Browser (drag & drop on sidebar tile, or popup buttons)
  → rag-backend /api/personal-docs            Authorization: Bearer <OWUI token>
      ├─ services/owui_auth.py                GET OWUI /api/v1/auths/  → user_id
      │                                        (identity stays in OWUI)
      ├─ services/extractor.py                docling-serve (RunPod:5001) /
      │                                        text decode → text_content
      ├─ services/personal_docs.py            PersonalDocsStore, scoped by user_id
      │     ├─ SQLite  data/personal_docs/personal_docs.db   (table `documents`,
      │     │          incl. text_content)
      │     └─ disk    data/personal_docs/files/{user_id}/{document_id}
      └─ services/indexer.py                  index_personal_doc(): chunk + embed
            └─ Qdrant  mintify_rag            payload {personal_doc_id, user_id,
                                               doc_type="personal", …}
```

And at chat time:

```
Chat message → OWUI → pipelines (mintify_pipeline.py)
  adds "user_id" (from body.metadata.user_id) to the /api/pipe payload
  → rag-backend retriever: Qdrant filter
        should( owui_file_id ∈ file_ids,   ← attached knowledge / chat files
                user_id == <caller> )      ← the caller's OWN personal files
  → matching chunks land in the [KONTEXT] block of the system prompt,
    cited as "[Quelle: <name> (persönliche Datei), Relevanz 0.xx]"
```

Browser→backend URLs are **environment-aware** (same pattern as the Bewertung
panel — never hardcode ports):

```js
window.location.hostname === 'localhost'
    ? 'http://localhost:8088/api/personal-docs'          // dev
    : `${window.location.origin}/rag-api/api/personal-docs`; // prod (Caddy → :8088)
```

---

## Backend (rag-backend)

### Endpoints — `rag-backend/app/routers/personal_docs.py`

| Method & path | Purpose |
|---|---|
| `POST /api/personal-docs` | Upload one file (multipart field `file`), max 50 MB → extract text + index into Qdrant |
| `GET /api/personal-docs` | List the caller's files (`{documents: [...]}`) |
| `GET /api/personal-docs/{id}/download` | Stream the raw file back (`FileResponse`) |
| `DELETE /api/personal-docs/{id}` | Delete DB row + file on disk + the file's Qdrant points |
| `POST /api/personal-docs/sync` | **Admin only.** Backfill: re-extract missing `text_content` from disk and (re)index all personal docs |

Every route depends on `require_user`: it extracts the Bearer token, verifies it
against OWUI, and 401s otherwise. The resolved `user_id` scopes **every** query —
there is no way to list or fetch another user's documents.

### Auth — `rag-backend/app/services/owui_auth.py`

`OwuiAuth.resolve_user(token)` calls `GET {OWUI_BASE_URL}/api/v1/auths/` with the
forwarded token and returns the OWUI user object (or `None`). The rag-backend
stores no users and issues no tokens — identity stays in OWUI. This is the same
approach the vertrag-backend uses on its branch.

### Storage — `rag-backend/app/services/personal_docs.py`

`PersonalDocsStore` (singleton, created in `dependencies.py` at startup):

- **SQLite** `/data/personal_docs/personal_docs.db`, table `documents`:
  `document_id (PK), user_id, filename, content_type, size_bytes, created_at`
  (+ index on `user_id`). Connection uses `check_same_thread=False` plus a
  `threading.Lock`.
- **Raw bytes** at `/data/personal_docs/files/{user_id}/{document_id}` —
  `document_id` is a fresh `uuid4().hex`; ids are validated against path
  separators (`_safe_id`) so no request can escape the storage directory.
- Delete removes the row first, then the file (missing file is tolerated).

### Config & wiring

- `config.py` → `personal_docs_dir: str = "/data/personal_docs"`
  (env var `PERSONAL_DOCS_DIR` would override).
- `docker-compose.yml` → new bind mount for `mintify-rag-service`:
  `./data/personal_docs:/data/personal_docs`. Docker creates the host folder on
  first `up`; `data/` is gitignored, so DB + files survive image rebuilds.
- `main.py` → router registered under `/api`; CORS `allow_methods` now includes
  `DELETE`.
- `docker-compose.yml` → `DOCLING_SERVER_URL` env for `mintify-rag-service`
  (default `http://host.docker.internal:5001`, same server as OWUI's
  `DOCLING_SERVER_URL`).
- `requirements.txt` → **`python-multipart`** (multipart upload parsing) and
  **`ftfy`** (mojibake repair, mirroring OWUI's loader) added → the image must
  be **rebuilt** once, a container restart is not enough. The pipeline change
  (user_id forwarding) needs a pipelines restart:

```bash
docker compose build mintify-rag-service
docker compose up -d mintify-rag-service
docker compose restart pipelines
docker logs mintify-rag-service | grep PersonalDocs
# expect: [RAG] PersonalDocsStore → /data/personal_docs
```

---

## Frontend (open-webui-src)

### `src/lib/apis/personalDocs/index.ts`

Typed fetch helpers used by both components: `listPersonalDocs`,
`uploadPersonalDoc`, `downloadPersonalDoc` (authenticated blob fetch → temporary
object URL → programmatic `<a download>` click), `deletePersonalDoc`. All send
`Authorization: Bearer ${localStorage.token}` and use the environment-aware base
URL shown above.

### `src/lib/components/layout/Sidebar/PersonalFiles.svelte` — the drop zone

- Rendered in `Sidebar.svelte` **between the chat list and the user footer**.
- Dashed tile with folder icon, title (`Persönliche Dateien (N)` with a live
  count loaded on mount) and hint text; while dragging over it turns green
  (`mintify-green`) and the hint switches to "Loslassen zum Hochladen".
- Drop → uploads every dropped file sequentially with toast feedback, then
  refreshes the count. Click → opens the popup.
- Uses a **drag-depth counter** instead of a boolean because `dragenter`/
  `dragleave` also fire when moving over child elements (icon, text) — a plain
  boolean flickers.
- No conflict with the chat's drag & drop: OWUI's file overlay is attached to
  `#chat-container` (`Chat.svelte` / `MessageInput.svelte`), and the sidebar is
  outside that element. Handlers still call `stopPropagation` defensively.

### `src/lib/components/layout/Sidebar/PersonalFilesModal.svelte` — the popup

- Built on the shared `common/Modal.svelte`; reloads the list every time it
  opens (`$: if (show) loadDocs()`).
- Header row: hint ("Nur für dich sichtbar · max. 50 MB pro Datei") + an
  "Hochladen" button backed by a hidden `<input type="file" multiple>`.
- Each row: document icon, filename (truncated, full name as tooltip),
  `size · date` in German locale, download button, delete button (with a native
  `confirm()` in German).
- After upload/delete it dispatches `change` so the sidebar tile count updates.

---

## Text extraction + per-user RAG (added July 2026)

Originally the store was raw-bytes-only; extraction and RAG indexing were added
two days after the initial feature:

- **Extraction** — `services/extractor.py` replicates OWUI's loader
  (`open-webui-src/backend/open_webui/retrieval/loaders/main.py`) exactly,
  because OWUI runs with `CONTENT_EXTRACTION_ENGINE=docling`:
  - text-like files (extension in `KNOWN_SOURCE_EXT` — copied 1:1 from OWUI —
    or a `text/*` content type) are decoded directly, like OWUI's TextLoader;
  - everything else (pdf, docx, xlsx, scans, …) →
    `POST {DOCLING_SERVER_URL}/v1/convert/file` (multipart field `files`,
    params `image_export_mode=placeholder`, `table_mode=accurate`), taking
    `document.md_content` from the response — the **same docling-serve
    instance OWUI uses** (RunPod, SSH-tunneled to host port 5001, kept alive
    by `scripts/init_models.sh`);
  - results run through `ftfy.fix_text()`, same as OWUI's `Loader.load()`.

  So personal files get identical extraction quality to knowledge-base files:
  markdown output, reconstructed tables, OCR for scans, office formats. If
  docling is unreachable, the upload still succeeds (stored + downloadable,
  `0 chunks indexed` in the log) and the admin `/sync` backfills later.
  Result lands in the `text_content` column (the store auto-migrates older
  DBs via `PRAGMA table_info` + `ALTER TABLE`).
- **Indexing** — `indexer.index_personal_doc()` reuses the existing chunker,
  embedder, collection and deterministic id scheme
  (`int(doc_id_hex,16) % 2**53 + chunk_index`). Payload carries
  `personal_doc_id`, `user_id`, `doc_type="personal"` and **no**
  `owui_file_id` — which keeps these points invisible to `reconcile()` /
  `sync_all()` (both key on `owui_file_id`), so OWUI-drift cleanup can never
  purge personal docs.
- **Retrieval scoping** — the retriever's strict `must owui_file_id ∈ file_ids`
  filter became `should(owui_file_id ∈ file_ids, user_id == caller)`. OWUI
  points have no `user_id`, personal points have no `owui_file_id`, so the two
  conditions can't cross-match: **another user's personal files can never be
  retrieved**. If the caller has neither attachments nor personal docs,
  behaviour is unchanged (model-only, no context injected).
- **User id plumbing** — `mintify_pipeline.py` now sends `user_id`
  (`body.metadata.user_id`, fallback `body.user.id`) in the `/api/pipe`
  payload; `PipeRequest.user_id` hands it to the retriever. If it's missing,
  personal docs are simply not searched (safe default).
- **Citations** — personal chunks are stored with
  `source = "<filename> (persönliche Datei)"`, so the existing
  `[Quelle: …, Relevanz …]` formatting marks them without retriever changes.

## Deliberate non-goals

- **No admin view.** There is deliberately no endpoint that lists all users'
  files (the admin `/sync` endpoint returns only counts).

---

## Same-session UI rework: persona pills in the navbar

Also implemented in this session (files: `chat/ModelDock.svelte`,
`chat/Navbar.svelte`):

1. **Position** — the persona/model dock moved from its own second row into the
   **top navbar row itself**, horizontally centered between the sidebar
   toggle/model dropdown (left) and the action icons (right). The old
   second-row placement remains as mobile fallback (`md:hidden`).
2. **Model dropdown desktop-hidden** — the `ModelSelector` wrapper got
   `md:hidden`: on desktop the pills are the only model selector, on narrow
   viewports the dropdown returns. Safe because dropdown and dock are both just
   `bind:selectedModels` views on the same array owned by `Chat.svelte`; the
   selector stays mounted (CSS-only hide), so no state is lost. Side effect:
   the "Set as default" link is desktop-hidden too.
3. **Pill buttons** — round icon-only avatars (label = hover tooltip) became
   `rounded-full` pills showing **icon + label side by side**. Active pill:
   `border/ring mintify-green`; inactive: gray border at 70 % opacity. The
   container is `overflow-x-auto scrollbar-hidden`, pills are
   `shrink-0 whitespace-nowrap` → horizontal scroll instead of wrapping on
   small screens.
