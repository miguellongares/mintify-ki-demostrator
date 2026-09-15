# Chunking & indexing — how document text becomes Qdrant points

This document explains the step between *"OWUI extracted the text"* and
*"the retriever found a passage"*: how a document is **split into chunks**, what
ends up **in Qdrant**, and how to **re-index** after a change.

For where files are stored and who extracts the text, see
`owui-dragdrop-files.md` and `personal-files-sidebar.md`.

---

## TL;DR

| Question | Answer |
|---|---|
| What is chunked? | The extracted text OWUI/docling produced — usually **Markdown** (`##` headings, `\| tables \|`, lists) |
| How is it split? | **By structure**, not by character count — `rag-backend/app/services/chunker.py` |
| Is a table ever cut? | Only if it alone exceeds `CHUNK_MAX_SIZE`, and then **at row boundaries with the header row repeated** |
| Does a chunk say where it came from? | Yes — every chunk is prefixed with the headings it sits under, continuations marked `(Fortsetzung)` |
| Where do chunks go? | One Qdrant collection (`mintify_rag`), named vectors `dense` (e5) + `bm25` (sparse) |
| When does indexing happen? | Event-driven — OWUI fires a webhook on upload/update/delete. Never per request |
| How do I re-index everything? | Admin Panel → Settings → Documents → **Alle Dokumente neu indexieren (Qdrant)** |

---

## The flow

```
OWUI upload / content update
  └─ webhook → rag-backend POST /api/index {file_id}
       └─ owui_client.fetch_file(id)      → {filename, content, hash}
            └─ skip if hash + CHUNK_SCHEME unchanged
            └─ delete this file's old points
            └─ chunker.chunk_markdown(content)   ← this document
                 └─ per chunk: embedder.encode_passage()  → dense  (384-dim)
                               sparse_embedder.encode_passage() → bm25
                      └─ Qdrant upsert  (collection mintify_rag)

Personal files (sidebar) take the same path via indexer.index_personal_doc().
```

---

## The chunker — three steps

`chunk_markdown(text, size, overlap, max_size)` in
`rag-backend/app/services/chunker.py`:

**1. Parse** the text into atomic structural blocks:

| Block | Recognised by | Treated as |
|---|---|---|
| `heading` | `#`…`######` | opens a section; tracked on a stack |
| `table` | consecutive lines starting with `\|` | atomic — never cut while it fits |
| `list` | `-` `*` `+` `•` or `1.` / `1)` | atomic; split at item boundaries |
| `code` | ` ``` ` / `~~~` fence | atomic |
| `para` | anything else up to a blank line | split at sentence boundaries |

`<!-- image -->` placeholders (docling's `image_export_mode=placeholder`) are
dropped — they are pure noise in a retrieved passage.

**2. Pack** whole blocks greedily up to `CHUNK_SIZE`. A block bigger than
`CHUNK_SIZE` but below `CHUNK_MAX_SIZE` gets a chunk to itself rather than being
cut. A heading also starts a new chunk — unless the current one is still under
`min_size` (`CHUNK_SIZE // 4`), so short sections merge instead of becoming
one-line chunks.

**3. Prefix** each chunk with the headings it lives under (max 3 levels), so a
passage torn out of the middle of a 40-page brochure still says what it is
about. A chunk that continues the previous section is marked
`## Abschnitt (Fortsetzung)`.

Only blocks above `CHUNK_MAX_SIZE` are split, each where it is safe:

- **table** → at row boundaries, **repeating the header + divider row** in every
  part, so half a table still has its column names
- **list** → at item boundaries
- **paragraph** → at German sentence boundaries (`z. B.`, `ca.`, initials do not
  end a sentence)
- **anything else** → at word boundaries, last resort

`CHUNK_OVERLAP` is carried over as whole trailing blocks — never a table, code
block or heading (a duplicated header row reads as broken, not as context).

### Why not LangChain / Chonkie / docling's HybridChunker?

Measured on this repo's corpus (7 OWUI documents), counting chunks that contain
table rows but no header row:

| splitter | chunks | headerless table fragments |
|---|---|---|
| `chunker.py` | 205 | **0** |
| LangChain `MarkdownHeaderTextSplitter` + `RecursiveCharacterTextSplitter` | 245 | 21 |
| Chonkie `RecursiveChunker.from_recipe("markdown")` | 206 | 19 |

Neither library has a concept of an atomic block, and neither can repeat a
header row. docling's own `HybridChunker` *would* handle tables properly, but it
chunks a `DoclingDocument` — which the rag-backend never sees, since OWUI stores
only extracted text. Going that route means persisting docling's `json_content`
and changing the ingestion contract on both paths; the payoff would be real
(page numbers for citations via `prov[].page_no`), but it is its own project.

---

## What lands in Qdrant

One collection, `mintify_rag`, with **named vectors**: `dense` (384-dim cosine,
`multilingual-e5-small`) and sparse `bm25` (IDF modifier, German stemmer).

| Payload field | OWUI file | Personal file |
|---|---|---|
| `text` | the chunk, heading prefix included | same |
| `source` | filename | `<name> (persönliche Datei)` |
| `owui_file_id` | OWUI file UUID | — |
| `file_hash` | `<content-hash>:<CHUNK_SCHEME>` | — |
| `personal_doc_id` / `user_id` / `doc_type` | — | uuid4 / owner / `"personal"` |
| `chunk_index` | position in the document | same |

The retriever filters on `owui_file_id ∈ file_ids` **OR** `user_id == caller`.
OWUI points carry no `user_id` and personal points carry no `owui_file_id`, so a
user can never retrieve someone else's personal documents.

**Point IDs** are deterministic: `int(file_id_hex, 16) % 2**53 + chunk_index`
(UUID with hyphens stripped). Re-indexing produces the same base id, and stale
points are deleted first, so the collection stays consistent even when the chunk
count changes.

---

## Configuration

`rag-backend/app/config.py` (override via `docker-compose.yml` environment):

| Setting | Default | Meaning |
|---|---|---|
| `CHUNK_SIZE` | `800` | Target chunk length in characters |
| `CHUNK_OVERLAP` | `100` | Trailing context repeated in the next chunk (whole blocks only) |
| `CHUNK_MAX_SIZE` | `1600` | Hard cap; a block below this stays intact instead of being cut |
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-small` | Dense arm; asymmetric, needs the `query: ` / `passage: ` prefixes |
| `BM25_LANGUAGE` | `german` | Stemmer/stopwords of the sparse arm |
| `COLLECTION_NAME` | `mintify_rag` | Qdrant collection |

---

## Re-indexing

`indexer.CHUNK_SCHEME` (`"v2-structured"`) is part of the dedup key stored in
`file_hash`. Bump it whenever the chunking changes: the stored hash then no
longer matches, and a sync re-indexes every file instead of skipping it as
"unchanged". **Without that, a chunker change silently does nothing.**

**From the UI (preferred):** Admin Panel → Settings → Documents →
*Alle Dokumente neu indexieren (Qdrant)* → *Neu indexieren*. The toast reports
the chunk and orphan counts.

**From the shell:**

```bash
KEY=$(docker exec mintify-rag-service python -c "from app.config import settings; print(settings.owui_api_key)")
curl -s -X POST http://localhost:8088/api/reindex-all -H "Authorization: Bearer $KEY"
```

| Endpoint | Does |
|---|---|
| `POST /api/index {file_id}` | index one file (the OWUI webhook) |
| `DELETE /api/index/{id}` | drop that file's points |
| `POST /api/sync` | (re)index every OWUI file |
| `POST /api/personal-docs/sync` | (re)index every personal file (admin token) |
| `POST /api/reconcile` | drop points whose file is gone from OWUI |
| `POST /api/reindex-all` | **all of the above in one admin request** — backs the UI button |
| `POST /api/reset-collection` | wipe + recreate the collection (`scripts/reset_rag.sh`) |

Re-indexing never re-extracts text that already exists — OWUI serves its stored
content and personal docs reuse `text_content` — so it works with **docling
offline**, which is the normal situation after a chunker change.

---

## Gotchas

- **A schema change empties the collection.** `indexer._ensure_collection()`
  wipes and recreates a collection whose vector config no longer matches
  (e.g. dense-only → hybrid, or a different embedding dimension). Retrieval then
  silently returns nothing until you re-index.
- **Changing the embedding model or its prefixes requires a full re-index**, not
  just a restart — old vectors are not comparable with new ones.
- **Do not change the point-ID scheme** without clearing Qdrant first; old points
  would never be overwritten or deleted.
- **`MIN_SCORE` bounds the dense arm only.** BM25 hits bypass it by design, so
  exact keyword matches (FIN, HSN/TSN, Kaufpreis in OCR'd forms) survive a low
  cosine score.
- Chunks are now variable in length (measured: 84–1774 chars, avg 576). `MIN_SCORE` was
  tuned against the old uniform 800-char chunks — worth revisiting if recall on
  long table chunks feels thin.
