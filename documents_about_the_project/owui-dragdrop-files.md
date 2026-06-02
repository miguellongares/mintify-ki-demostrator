# OWUI drag-and-drop chat files — storage, lifecycle & RAG

This document explains what happens when a user **drags a file into the chat
input** (e.g. a `.docx` or `.pdf`): where it is stored, how long it stays
available, and **who retrieves its content**.

> **Architecture note (updated June 2026):** MINTIFY was consolidated onto a
> **single RAG**. OWUI is now only the *upload + universal text extraction*
> layer; the **rag-backend + Qdrant** does all retrieval. OWUI no longer embeds
> files into its own Chroma DB and no longer runs its own retrieval. Earlier
> versions of this doc described two parallel RAG systems (OWUI Chroma + MINTIFY
> Qdrant) and a shared uploads folder — that is **superseded**. See `CLAUDE.md`
> → "Document ingestion".

---

## TL;DR

| Question | Answer |
|---|---|
| Where is the file stored? | 3 places keyed by UUID: disk, OWUI SQL `file` table (extracted text), and Qdrant (`mintify_rag`) |
| Who extracts the text? | **OWUI** — every file type (pdf, docx, xlsx, …) via its loaders |
| Who embeds + retrieves? | **rag-backend + Qdrant** — the only RAG. OWUI's Chroma embedding is disabled (`BYPASS_EMBEDDING_AND_RETRIEVAL=true`) |
| When is it indexed? | **Eagerly on upload** — OWUI fires a webhook to the rag-backend, which pulls the text and embeds it |
| Available for the whole chat? | **Yes** — re-attached on every message and saved with the chat |
| Re-embedded every message? | No — embedded once on upload (dedup by file hash) |
| Auto-cleanup? | Qdrant points ARE removed on file delete (webhook). Disk + SQL row removed by OWUI. Orphaned Chroma collections from before the change may linger |

---

## The flow at a glance

```
Drop file → OWUI POST /api/v1/files/
  ├─ disk:  data/open-webui/uploads/{uuid}_name.ext      (raw file)
  ├─ SQL:   file table, data.content                     (extracted text, ANY type)
  └─ webhook → rag-backend POST /api/index {file_id}
                 └─ GET OWUI /api/v1/files/{id} → text → chunk → embed
                       └─ Qdrant mintify_rag  (payload owui_file_id={uuid})

Chat message → pipeline forwards file_ids → rag-backend retrieves from Qdrant
              (filter owui_file_id ∈ file_ids) → injects {context} → vLLM
```

OWUI's Chroma embedding and `<source>` retrieval are **off**; the pipeline also
strips any `<source>`/`<context>` OWUI might inject, so document text reaches the
model **once**, via the rag-backend's `{context}`.

---

## Where it gets stored — three places, all keyed by UUID

`backend/open_webui/routers/files.py:upload_file` assigns a UUID and writes:

| Store | Host path | Container path (`mintify-webui`) | Contents |
|---|---|---|---|
| Disk | `data/open-webui/uploads/{uuid}_name.ext` | `/app/backend/data/uploads/…` | raw file |
| SQL `file` table | `data/open-webui/webui.db` | `/app/backend/data/webui.db` | metadata + **full extracted text** in `data.content` |
| Qdrant | `data/qdrant/collections/mintify_rag/` | (separate `qdrant` container) | chunks + embeddings, payload `owui_file_id` |

Text extraction (`routers/retrieval.py:process_file` → `loaders/main.py`) still
runs for every type (`.docx → Docx2txtLoader`, `.pdf → PyPDFLoader`, …) and
saves `file.data["content"]` **regardless of embedding** — that's what lets
`BYPASS_EMBEDDING_AND_RETRIEVAL=true` skip Chroma while keeping the text the
rag-backend needs.

> Note: with bypass on, OWUI no longer creates `file-{uuid}` Chroma collections.
> Any such directories under `data/open-webui/vector_db/` are leftovers from
> before the change.

---

## How long it stays available

Unchanged from OWUI's normal behaviour — a dropped file stays in scope for the
**entire chat** and **survives reloads**. In
`open-webui-src/src/lib/components/chat/Chat.svelte`:

```js
let chatFiles = [];                                          // :141 chat-scoped
chatFiles.push(..._files.filter(i => ['doc','file','collection'].includes(i.type)));  // :1321
let files = JSON.parse(JSON.stringify(chatFiles));           // :1499 re-sent every turn
chat = await updateChatById(..., { files: chatFiles });      // :907 persisted
chatFiles = chatContent?.files ?? [];                        // :823 restored on reopen
```

The `file` table row is scoped to `user_id` (no `chat_id`), so files persist
per-user globally but are only auto-attached to the chat they were dropped in.

---

## Indexing & retrieval (the single RAG)

**Eager ingestion.** `routers/files.py` fires a best-effort webhook
(`RAG_INGEST_WEBHOOK_URL`, daemon thread) on upload, content-update, and delete:
- `POST /api/index {file_id}` → `indexer.index_file_id()` →
  `owui_client.fetch_file()` (`GET /api/v1/files/{id}`, **admin** Bearer key) →
  chunk → embed → upsert into Qdrant with `{text, source, owui_file_id,
  file_hash, chunk_index}`.
- `DELETE /api/index/{id}` → removes that file's points (closes OWUI's old
  cleanup gap).
- `POST /api/sync` → backfill/repair: index every OWUI file.

**Retrieval.** On each message the pipeline (`mintify_pipeline.py`) forwards the
attached `file_ids`; `retriever.py` filters Qdrant by
`owui_file_id ∈ file_ids` and the rag-backend injects the hits into the
`{context}` placeholder. Works identically for **any** file type now, because
the text came from OWUI's universal extractor — a dropped `.docx` is retrieved
the same way a Knowledge `.pdf` is.

**Requirement.** Ingestion depends on a valid `OWUI_API_KEY` (an OWUI admin API
key). If it's missing/invalid, indexing silently fails with `401`s in
`docker logs mintify-rag-service`; `POST /api/sync` is the repair button.

---

## Cleanup

- **File delete** (OWUI UI/API) → removes disk file + SQL row, AND fires the
  `DELETE /api/index/{id}` webhook so the Qdrant points go too.
- **Chat delete** → still leaves files/vectors (files are user-scoped, not
  chat-scoped).
- **Legacy Chroma** `file-{uuid}` collections from before the bypass change are
  orphaned; delete them via OWUI rather than `rm`-ing dirs (manual removal can
  corrupt Chroma's `chroma.sqlite3` index).
