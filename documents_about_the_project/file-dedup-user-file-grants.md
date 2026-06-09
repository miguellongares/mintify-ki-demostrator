# File deduplication & the `user_file` grant table

This document explains how MINTIFY stores **one physical copy of each unique
uploaded file** while still letting every user (and every knowledge base / chat)
see "their" files — and how files are cleaned up. It supersedes the earlier
per-upload duplication behaviour where every upload created a new disk file, a new
`file` row, and a new set of Qdrant vectors.

> **Added June 2026.** Implemented on branch `RAG-Improvement`. Related docs:
> `owui-dragdrop-files.md`, `rag-flow-refactor.md`, and `CLAUDE.md` → "Document
> ingestion".
>
> **Updated June 2026** with two follow-ups: (1) chat drag-and-drop files are kept
> **private + chat-scoped** via a grant *kind*, and (2) RAG retrieval is now **strict**
> (no "search the whole index" fallback). See the two dedicated sections below.

---

## TL;DR

| Question | Answer |
|---|---|
| Is the same file stored twice? | **No.** Identical content (same raw-bytes SHA‑256) is stored once — one disk file, one `file` row, one set of Qdrant points. |
| How is "the same file" detected? | Raw bytes are hashed (`sha256`) **before** storing; the hash lives in `file.meta["content_hash"]`. |
| What if two users upload the same file? | The second upload reuses the existing `file.id` and just records a **grant** in the new `user_file` table. |
| How does a non-owner read a shared file? | Their `user_file` grant satisfies the file-read access check (previously only the owner/admin could read → 404). |
| What id do chat / KBs / rag-backend use? | The shared **`file.id`** — unchanged. Nothing downstream had to change. |
| When is content actually deleted? | Only when its **last grant AND last knowledge-base reference** are gone. |
| Does a chat drag-drop file show in my library / other chats? | **No.** Chat drops get a `kind="chat"` grant: deduped storage, but hidden from the library list and not shared with other users. |
| In a new chat with nothing attached, what is retrieved? | **Nothing** — the model answers on its own. Retrieval is strict: only the model's KB + files dropped in *this* chat. |

---

## The problem this solves

`upload_file()` used to mint a fresh UUID and write the bytes to disk + a new
`file` row on **every** upload, with no pre-check. Re-uploading the same PDF — or
uploading it from a second account, or into a knowledge base — produced a full
duplicate each time (disk + `webui.db` row + duplicate Qdrant vectors). There was
also no clean deletion path: deleting a knowledge base orphaned the underlying
files.

A first attempt deduped **per user**, but real data showed three rows of the same
PDF with one identical content hash and three different `user_id`s — different
accounts each kept a copy. The fix below makes dedup **global** and solves the
access problem that global dedup would otherwise create.

---

## Design: content store + grant table

Two tables in `webui.db`:

- **`file`** — a pure **content store**. One row per unique document (deduped by
  raw-bytes hash). Holds the path, the extracted text (`data.content`), the
  content hash (`meta.content_hash`), etc.
- **`user_file`** (new) — a **junction / grant table** recording *which user can
  see which file*, plus a per-user filename. Columns:

  | column | meaning |
  |---|---|
  | `id` | upload_id (uuid, PK) — one per upload event |
  | `file_id` | → `file.id` (the shared content) |
  | `user_id` | who holds this grant |
  | `filename` | per-user display name |
  | `meta` | JSON — holds `kind` (`"chat"` or `"library"`; absent ⇒ `"library"`) |
  | `created_at` | epoch seconds |

Because the surfaced id stays the shared `file.id`, **chat message metadata
(`message.files[].id`), knowledge-base `data.file_ids`, the pipeline's
`_extract_file_ids`, Qdrant's `owui_file_id` payload, and the ingest webhook all
keep working unchanged** — they always pointed at `file.id`, and now several grants
can point at the same one.

---

## Flow on upload

```
POST /api/v1/files/?chat=<bool>   (chat drop → chat=true; KB/workspace add → false)
  │
  ├─ kind = "chat" if chat else "library"
  ├─ read bytes → content_hash = sha256(bytes)
  │
  ├─ existing = Files.get_file_by_content_hash(content_hash)   ← GLOBAL
  │
  ├─ if existing:                      (duplicate content)
  │     UserFiles.insert_grant(user, existing.id, filename, {"kind": kind})
  │     return existing.id             (skip disk write, text extraction, webhook)
  │
  └─ else:                             (new content)
        store bytes → file row (meta.content_hash) → extract text
        UserFiles.insert_grant(user, file.id, name, {"kind": kind})
        webhook → rag-backend POST /api/index {file_id}        (embed once)
```

So content is stored + embedded exactly once; **every upload yields a grant** tagged
with its `kind`. The `chat` query param is the only signal that distinguishes a chat
drag-drop from a KB/workspace upload (both hit the same endpoint) — the frontend sets
`?chat=true` from the chat/channel drop handlers.

---

## Access & listing

Every file-read endpoint guard now reads:

```python
file.user_id == user.id
or user.role == "admin"
or UserFiles.has_grant(user.id, id)        # ← new
or has_access_to_file(id, "read", user)
```

`has_access_to_file` is inert in MINTIFY (it depends on `meta.collection_name`,
which `BYPASS_EMBEDDING_AND_RETRIEVAL=true` never sets), so the **grant** is what
lets a non-owner read a shared file.

The file list (`GET /files/`, `/files/search`) for non-admins is grant-based **and
library-only**: `Files.get_files_by_ids(UserFiles.get_library_file_ids_by_user_id(user.id))`.
Chat drops (`kind="chat"`) are therefore excluded from the library. Admins still see
all files.

---

## Chat drag-and-drop isolation (grant `kind`)

A file dropped into a chat must stay scoped to that chat — it should **not** appear in
the file library and **not** be visible to other users — while still being stored once
(deduped). This is controlled by the grant `kind` (stored in `user_file.meta`):

- **`kind="chat"`** (chat / channel drops, `?chat=true`): gives the dropper read
  access (`has_grant` is checked kind-agnostically) and counts toward delete
  ref-counting, but is **excluded** from the library list. Content is still deduped.
- **`kind="library"`** (KB / workspace uploads, default): listed in `GET /files/` and
  shared as described above.

A file can hold **both** kinds at once (dropped in a chat *and* added to a KB) — each
upload ensures a grant of its own kind, so it appears in the library (library grant)
while the chat keeps working. `has_grant(user, file, kind=None)` matches any kind (used
for read access + ref-counting); `get_library_file_ids_by_user_id` filters to
`kind == "library"`.

---

## Strict retrieval scoping (no "whole-index" fallback)

Retrieval is **strict**: a chat retrieves only from knowledge it explicitly has —
the model's attached knowledge base **plus** files dropped in *that* chat (whose ids
arrive in the pipeline's `file_ids`). With **nothing** attached, it retrieves nothing
and the model answers on its own.

This closed a cross-chat leak: previously `rag-backend`'s retriever treated
`file_ids = None` as "no filter → **search the entire Qdrant index**", and the pipeline
returned `None` whenever a chat had no KB and no drop. So a brand-new chat surfaced
files dropped in *other* chats. Now:

- `pipelines/mintify_pipeline.py` `_extract_file_ids` always returns a **list** (`[]`
  when nothing is attached) — never `None`.
- `rag-backend/app/services/retriever.py` treats `None` **or** `[]` as
  "retrieve nothing"; only a non-empty list filters Qdrant by `owui_file_id`.

| Chat situation | Retrieves from |
|---|---|
| KB attached to the model | that KB's files |
| File dropped in **this** chat | that file (this chat only) |
| Nothing attached | **nothing** — model answers alone |
| File dropped in **another** chat | **not retrieved** |

> Because the default is now "no retrieval unless attached", the main answering model
> must have its knowledge base attached in OWUI (Workspace → Models → Knowledge),
> otherwise it answers without documents.

---

## Deletion & reference counting

A file's content (row + disk + Qdrant points) is the shared resource; a grant is a
per-user pointer to it. Content is purged **only when nothing references it**.

- **`DELETE /files/{id}`** — removes *this user's* grant, then purges the content
  only if `UserFiles.count_grants_for_file(id) == 0` **and**
  `Knowledges.count_knowledge_bases_referencing_file(id) == 0`.
- **Remove file from KB / delete KB** (`routers/knowledge.py`) — the cascade
  hard-deletes the underlying file only when no other KB **and** no grant still
  references it.

**Consequence:** deleting a knowledge base no longer nukes a file the uploading
user still has in their library — the grant is the primary "I own this" signal;
KB membership is secondary. To remove a file entirely, delete it from the file
list (drops the grant) once it is in no KB.

RAG retrieval is unaffected by ownership: the rag-backend reads file text via the
admin `OWUI_API_KEY` and filters Qdrant by `owui_file_id`, so any user gets answers
grounded in the shared copy.

---

## Files touched

| File | Change |
|---|---|
| `open-webui-src/backend/open_webui/models/files.py` | `UserFile` model + `UserFilesTable`; `get_file_by_content_hash` made global; kind-aware `has_grant(…, kind=None)` + `get_library_file_ids_by_user_id` |
| `…/migrations/versions/a1b2c3d4e5f6_add_user_file_table.py` | new table (`down_revision = 3781e22d8b01`). `kind` lives in `user_file.meta` — no extra migration |
| `…/routers/files.py` | global dedup + grant on upload; `chat` query param → grant `kind`; grant in read guards; **library-only** non-admin list; ref-counted delete; clear grants on `DELETE /all` |
| `…/routers/knowledge.py` | KB remove/delete cascades require no grants before hard-delete |
| `open-webui-src/src/lib/apis/files/index.ts` | `uploadFile(token, file, chat=false)` → appends `?chat=true` |
| `open-webui-src/src/lib/components/chat/MessageInput.svelte`, `chat/Chat.svelte`, `channel/MessageInput.svelte` | pass `chat=true` on drop/paste (KB upload in `Knowledge/KnowledgeBase.svelte` stays default) |
| `pipelines/mintify_pipeline.py` | `_extract_file_ids` always returns a list (`[]` = nothing attached), never `None` |
| `rag-backend/app/services/retriever.py` | strict scoping — `None`/`[]` `file_ids` ⇒ retrieve nothing (was: `None` ⇒ search whole index) |
| `scripts/reset_rag.sh` | also wipes the `user_file` table |
| rag-backend (rest) | still keys by `file.id`; `reconcile` / `reset-collection` endpoints exist |

---

## Caveats

- **Applied going forward only.** Files uploaded *before* this change have no
  `user_file` grants, so non-admin accounts won't see them in their file list
  until re-uploaded or until `scripts/reset_rag.sh` is run for a clean slate.
  Admins still see everything.
- **Cross-user KB sharing for non-owners without a grant** remains a pre-existing
  limitation (admin reads all; RAG is unaffected). A grant is created on upload,
  which covers the common flow.
- The dedup hash is the **raw bytes** (`sha256`), not the extracted text — so two
  visually-identical documents in different file formats are treated as distinct.
- **Admins still see all files** in `GET /files/` (oversight). Chat drops are hidden
  from non-admins and other users, but an admin can still see them in admin file
  management. (One-line swap to a library-only admin query if ever wanted.)
- **Chat-drop content has no per-chat "remove" UI**, so it persists as long as any
  grant exists; it's cleaned in bulk by `scripts/reset_rag.sh` / `POST /api/reconcile`.
  Since it's deduped, footprint is bounded by unique content, not number of drops.
- **The strict-retrieval default requires a KB on the answering model.** With no KB
  and no drop, a chat retrieves nothing.

---

## Quick verification

```bash
# OWUI changes (dedup, grants, chat param, library-only list) — full image rebuild,
# migration runs at startup:
docker compose build open-webui && docker compose up -d open-webui

# Retrieval scoping changes — backend + pipeline (note the service is mintify-rag-service):
docker compose build mintify-rag-service && docker compose up -d mintify-rag-service
docker compose restart pipelines
```

```bash
# Storage dedup: upload the same PDF from two accounts (+ add to a KB), then:
docker exec mintify-webui python -c "from open_webui.models.files import Files; print('file rows:', len(Files.get_files()))"  # → 1 per unique PDF
ls data/open-webui/uploads | wc -l            # → one disk file
curl http://localhost:6333/collections/mintify_rag   # → one point set

# Chat-drop isolation: drop a PDF in chat A (no KB).
#  - it does NOT appear in that user's file library (GET /api/v1/files/)
#  - another non-admin gets 404 reading it
#  - chat A answers from it; a NEW chat B (no drop, no KB) does NOT:
docker logs mintify-rag-service 2>&1 | grep -i "skipping retrieval"   # model-only path in chat B
```
