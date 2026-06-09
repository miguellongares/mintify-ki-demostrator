# File deduplication & the `user_file` grant table

This document explains how MINTIFY stores **one physical copy of each unique
uploaded file** while still letting every user (and every knowledge base / chat)
see "their" files — and how files are cleaned up. It supersedes the earlier
per-upload duplication behaviour where every upload created a new disk file, a new
`file` row, and a new set of Qdrant vectors.

> **Added June 2026.** Implemented on branch `RAG-Improvement`. Related docs:
> `owui-dragdrop-files.md`, `rag-flow-refactor.md`, and `CLAUDE.md` → "Document
> ingestion".

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
  | `meta` | reserved (JSON) |
  | `created_at` | epoch seconds |

Because the surfaced id stays the shared `file.id`, **chat message metadata
(`message.files[].id`), knowledge-base `data.file_ids`, the pipeline's
`_extract_file_ids`, Qdrant's `owui_file_id` payload, and the ingest webhook all
keep working unchanged** — they always pointed at `file.id`, and now several grants
can point at the same one.

---

## Flow on upload

```
POST /api/v1/files/  (chat drop OR knowledge-base add — same endpoint)
  │
  ├─ read bytes → content_hash = sha256(bytes)
  │
  ├─ existing = Files.get_file_by_content_hash(content_hash)   ← GLOBAL
  │
  ├─ if existing:                      (duplicate content)
  │     UserFiles.insert_grant(user, existing.id, filename)    ← only a grant
  │     return existing.id             (skip disk write, text extraction, webhook)
  │
  └─ else:                             (new content)
        store bytes → file row (meta.content_hash) → extract text
        UserFiles.insert_grant(user, file.id, name)            ← grant too
        webhook → rag-backend POST /api/index {file_id}        (embed once)
```

So content is stored + embedded exactly once; **every upload yields a grant**.

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

The file list (`GET /files/`, `/files/search`) for non-admins is now grant-based:
`Files.get_files_by_ids(UserFiles.get_file_ids_by_user_id(user.id))`. Admins still
see all files.

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
| `open-webui-src/backend/open_webui/models/files.py` | `UserFile` model + `UserFilesTable`; `get_file_by_content_hash` made global |
| `…/migrations/versions/a1b2c3d4e5f6_add_user_file_table.py` | new table (`down_revision = 3781e22d8b01`) |
| `…/routers/files.py` | global dedup + grant on upload; grant in read guards; grant-based list; ref-counted delete; clear grants on `DELETE /all` |
| `…/routers/knowledge.py` | KB remove/delete cascades require no grants before hard-delete |
| `scripts/reset_rag.sh` | also wipes the `user_file` table |
| rag-backend | **no change** (still keys by `file.id`; `reconcile` / `reset-collection` endpoints exist) |

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

---

## Quick verification

```bash
docker compose build open-webui && docker compose up -d open-webui   # migration runs at startup

# Upload the same PDF from two accounts + add it to a KB, then:
docker exec mintify-webui python -c "from open_webui.models.files import Files; print('file rows:', len(Files.get_files()))"  # → 1 per unique PDF
ls data/open-webui/uploads | wc -l            # → one disk file
curl http://localhost:6333/collections/mintify_rag   # → one point set
# → three user_file grant rows; each account can preview the file and get RAG answers.
```
