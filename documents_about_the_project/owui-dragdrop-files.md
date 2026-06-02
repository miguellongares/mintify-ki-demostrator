# OWUI drag-and-drop chat files — storage, lifecycle & RAG

This document explains what happens when a user **drags a file into the chat
input** (e.g. a `.docx`) in the MINTIFY stack: where it is stored, how long it
stays available, and — importantly — **who actually retrieves its content**.

The short version: dropped files are handled **entirely by Open Web UI
itself**, *not* by the MINTIFY rag-backend. This is the opposite of how the
PDF Knowledge collections work, and it is not obvious from the code, so it is
documented here.

For the request-flow diagram and the file-scoped Knowledge retrieval, see
`CLAUDE.md` and `rag-flow-refactor.md` first.

---

## TL;DR

| Question | Answer |
|---|---|
| Where is the file stored? | 3 places, all keyed by the file's UUID: disk, OWUI's SQL `file` table, and OWUI's vector DB |
| Available for just one message? | No |
| Available for the whole chat? | **Yes** — re-attached on every message and saved with the chat |
| Available across all chats? | The stored file/vectors persist per-user globally, but are only auto-attached to the chat they were dropped in |
| Who does the RAG for it? | **OWUI**, over its own `file-{uuid}` vector collection — *not* MINTIFY's Qdrant |
| Re-embedded every message? | No — embedded once at upload (hash-deduped) |
| Auto-cleanup? | Essentially none |

---

## Where it gets stored — three places at once

When a file is dropped, OWUI persists it in three stores, all keyed by the
file's **UUID** (which is also OWUI's internal `file.id`):

| Store | Location | Contents |
|---|---|---|
| **Disk** | `data/open-webui/uploads/{uuid}_name.ext` | the raw original file |
| **SQL `file` table** | OWUI database (`backend/open_webui/models/files.py`) | metadata + **full extracted text** in `data.content` |
| **Vector DB** | collection named `file-{uuid}` | the text chunked + embedded |

### .docx text extraction

On upload, OWUI runs `process_file()`
(`backend/open_webui/routers/retrieval.py`), which selects a loader by
content-type. For `.docx`:

```python
elif file_content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document" \
     or file_ext == "docx":
    loader = Docx2txtLoader(file_path)
```

So the `.docx` → text conversion is done by LangChain's **`Docx2txtLoader`**
(wrapping the `docx2txt` library). Embedding into `file-{uuid}` happens **once**
at upload; a content-hash check (`save_docs_to_vector_db`) prevents
re-embedding. It is **not** re-embedded on every message.

---

## How long it stays available

A dropped file stays in scope for the **entire chat** and **survives page
reloads** — not just the one message.

- **Frontend** (`src/lib/components/chat/Chat.svelte`) keeps a chat-level
  `chatFiles` array (reset only when a *new* chat starts):

  ```js
  let chatFiles = [];   // chat-scoped, not per-message
  chatFiles.push(..._files.filter(item => ['doc','file','collection'].includes(item.type)));
  ```

- **Every subsequent message** re-sends the whole `chatFiles` array, merged
  with that message's own files and deduped:

  ```js
  let files = JSON.parse(JSON.stringify(chatFiles));   // re-sent every turn
  files.push(...(userMessage?.files ?? []) ...);
  ```

  Drop a file on message 1 and ask a follow-up on message 5 → message 5's
  request still carries that file.

- **Persistence:** `chatFiles` is saved into the chat record
  (`updateChatById(..., { files: chatFiles })`) and restored on reopen
  (`chatFiles = chatContent?.files ?? []`), so it survives a reload.

- **Cross-chat scope:** the `file` table row is scoped to **`user_id`** — there
  is **no `chat_id`** column. The file isn't auto-shared into other chats
  (because `chatFiles` is per-chat), but the underlying file + its `file-{uuid}`
  vector collection persist globally for that user until explicitly deleted.

---

## Who does the RAG for the dropped file

**OWUI does — not the MINTIFY rag-backend.**

On each message, OWUI's middleware
(`backend/open_webui/utils/middleware.py` → `chat_completion_files_handler` →
`get_sources_from_files`) does a semantic search over the `file-{uuid}`
collection, pulls the top-K chunks, and injects them as `<source>` blocks.

This runs in **OWUI's own vector DB**, completely separate from the **Qdrant**
(`mintify_rag` collection) that the MINTIFY rag-backend uses.

### Consequence for the MINTIFY pipeline

The pipeline's `_extract_file_ids` (`pipelines/mintify_pipeline.py`) reads
dropped-file UUIDs from `body.metadata.files[*].id` and forwards them as
`file_ids`. **But** the rag-backend only indexed *PDFs* into Qdrant with an
`owui_file_id` payload. A dropped `.docx` is **not** in Qdrant, so MINTIFY
retrieval finds **0 chunks** for it.

The docx content therefore reaches the model via **OWUI's own `<source>`
injection** — which typically lands in the **system message**. Note the
pipeline's `_clean_user_message` only strips `<source>`/`<context>` from
`role == "user"` messages, so source content placed in the system message is
**not** stripped and survives to vLLM. This is why a dropped docx is "read" by
the model even though MINTIFY's own retrieval returned nothing for it.

---

## Cleanup gap

There is effectively **no automatic cleanup**:

- Deleting a **chat** removes the chat row but leaves the file, its disk copy,
  its SQL text, and its `file-{uuid}` vector collection behind.
- The file-delete endpoint removes the disk file and DB row but contains a
  literal `# We should add Chroma cleanup here` comment — the **vector
  collection is not deleted** (collections are only cleared when a file's
  content is *updated*).

Over time, dropped files accumulate in `data/open-webui/uploads/`, in the SQL
`file` table, and as orphaned `file-{uuid}` vector collections. Worth watching
disk usage on the Hetzner host.
