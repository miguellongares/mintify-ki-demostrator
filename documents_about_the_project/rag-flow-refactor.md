# RAG flow refactor — 2026-05-28

This document explains how the RAG (retrieval-augmented generation) flow works
**after** the cleanup of 2026-05-28. Before that day the prompt sent to vLLM
contained two retrieved contexts, three stacked system prompts, and several
LLM calls per user message even ran a second Qdrant query for the title and
the tags. This document describes the new, single-path flow and the small
patches we had to make to the OWUI fork to support it.

If you are looking for the request-flow diagram and the container layout,
read `CLAUDE.md` at the repo root first — this document only covers what was
*changed*.

---

## TL;DR

| Concern | Before | After |
|---|---|---|
| RAG retrieval per user question | 2 (OWUI's + rag-backend's) | 1 (rag-backend only) |
| System prompts stacked into one | 2 (OWUI per-model **+** pipeline persona) | 1 (per-model wins; persona is fallback) |
| Qdrant calls per chat (title, tags, query-gen) | 4 | 1 |
| Knowledge scoping | "search all 14 PDFs" | "search only the files attached to the active model" |
| OWUI `<source>` / `### Task` template noise in prompt | Yes | Stripped by the pipeline |

---

## The new request flow

```
Browser
  → Open Web UI (3000)
    → Pipelines server (9099) — pipelines/mintify_pipeline.py
        is this message a "### Task:" meta-task (title / tags / query-gen)?
        ├── YES → POST /api/passthrough on the rag-backend
        │           → straight to vLLM, no retrieval, no system prompt injection
        └── NO  → strip OWUI's <source>/<context>/<user_query> wrappers
                  read knowledge file IDs from body.metadata.model.info.meta.knowledge
                  POST /api/pipe on the rag-backend
                    → embed user_message
                    → Qdrant query, filtered by owui_file_id ∈ knowledge file IDs
                    → build ONE system prompt (OWUI prompt wins; persona = fallback)
                    → stream from vLLM
```

The rag-backend is now the **only** place where retrieval happens. OWUI's
built-in RAG still runs as a side-effect (it injects `<source>` chunks into
the user message) — the pipeline strips them out so they don't reach vLLM
twice.

---

## What changed file-by-file

### 1. `pipelines/mintify_pipeline.py` — the proxy got smarter

Three responsibilities were added:

1. **Detect meta-tasks**. `user_message.lstrip().startswith("### Task:")` →
   route to `/api/passthrough` instead of `/api/pipe`. OWUI uses these for
   title generation, tag generation, and Query Generation. None of them
   benefit from retrieving PDF chunks, so we skip Qdrant entirely.
2. **Clean OWUI's injection**. Regexes `_SOURCE_RE`, `_CONTEXT_BLOCK_RE`,
   `_USER_QUERY_RE` rip out `<source>`, `<context>`, and `<user_query>` blocks
   that OWUI's own RAG layer prepended. We forward the *real* user question
   to the rag-backend.
3. **Extract attached Knowledge file IDs** via `_extract_file_ids(body)`. It
   reads `body.metadata.model.info.meta.knowledge[*].files[*].id` (for
   collection-type attachments) and `body.metadata.files[*].id` (for direct
   per-message file uploads). Returns:
   - `None` — OWUI did not include a knowledge field at all → don't filter.
   - `[]` — knowledge field present but empty → "no Knowledge attached".
   - `[uuid, uuid, ...]` — the actual file UUIDs to filter Qdrant by.

The system prompt is no longer concatenated. Instead, the pipeline forwards
two separate fields to `/api/pipe`:

- `owui_system_prompt` — OWUI's per-model system prompt (e.g. KI Lehrer's
  "Du bist ein Lehrer experte in KI…").
- `base_system_prompt` — the pipeline's MINTIFY persona, used only as a
  fallback when `owui_system_prompt` is empty.

### 2. `rag-backend/app/routers/rag.py` — single source of truth for the prompt

`PipeRequest` was extended:

```python
owui_system_prompt: str = ""
base_system_prompt: str = ""
file_ids: list[str] | None = None
```

The prompt-selection logic is now:

```python
chosen = req.owui_system_prompt.strip() or req.base_system_prompt.strip()
if context:
    if "{context}" not in chosen:
        chosen = chosen + DEFAULT_CONTEXT_BLOCK   # appends "[KONTEXT]{context}[/KONTEXT]"
    enriched_system = chosen.format(context=context)
else:
    # No knowledge / empty hits → strip the placeholder entirely
    enriched_system = ...
```

A second endpoint was added: **`POST /api/passthrough`**. It takes
`{model_id, messages, temperature}` and streams from `LLMClient.stream(...)`
with **no retrieval, no system-prompt manipulation**. Used by the pipeline
for OWUI's meta-tasks.

### 3. `rag-backend/app/services/retriever.py` — file-scoped retrieval

`retrieve()` accepts `file_ids: list[str] | None`:

- `None` → no filter (search all indexed PDFs). Used when we have no
  information about what's attached.
- `[]` → return immediately with empty context. Treated as "no Knowledge
  attached → don't leak the whole index".
- non-empty list → Qdrant `Filter(must=[FieldCondition(key="owui_file_id",
  match=MatchAny(any=file_ids))])`.

### 4. `rag-backend/app/services/indexer.py` — per-chunk Knowledge tag

OWUI saves uploaded PDFs to `data/open-webui/uploads/` as
`<uuid>_<original_name>.pdf`. The UUID is also OWUI's internal `file.id`.
The indexer now parses that prefix and stores it on every chunk:

```python
m = re.match(r"([0-9a-fA-F-]{36})_", filename)
owui_file_id = m.group(1) if m else None
...
payload = {
    "text": chunk,
    "source": filename,
    "page": page_num + 1,
    "file_hash": h,
    "owui_file_id": owui_file_id,   # ← new
}
```

This means the retriever can filter chunks by exactly the file IDs OWUI
passes through in `body.metadata.model.info.meta.knowledge[*].files[*].id`
— the UUIDs match 1-to-1.

> Note: if you change this scheme, you must wipe `data/qdrant/*` and let the
> indexer rebuild — old payloads won't have the `owui_file_id` field.

### 5. `docker-compose.yml` — disable OWUI's built-in RAG noise

Two env vars added to the `open-webui` service:

```yaml
- ENABLE_RAG_HYBRID_SEARCH=false
- RAG_TEMPLATE=
```

These reduce (but do not eliminate) OWUI's tendency to wrap the user message
with `### Task: Respond to the user query...` and `<source>` blocks. The
pipeline's regex stripping is the safety net.

Query Generation cannot be disabled via env var — toggle it off in
**Admin → Settings → Interface → Query Generation**.

### 6. `open-webui-src/backend/open_webui/routers/openai.py` — the OWUI patch

The hardest bug to find. OWUI sends pipeline calls through its
OpenAI-compatible route. That route does this at the top of the handler:

```python
payload = {**form_data}
metadata = payload.pop("metadata", None)   # ← removed, never restored
```

It then adds `payload["user"]` back when the target is a pipeline, but
**never restores `metadata`**. Without it, the pipeline gets a body that
looks like a vanilla OpenAI request: `{stream, model, messages, user,
max_tokens}` — no `metadata`, no `model.info`, no knowledge attachments.

The patch (around line 657):

```python
if "pipeline" in model and model.get("pipeline"):
    payload["user"] = {
        "name": user.name,
        "id": user.id,
        "email": user.email,
        "role": user.role,
    }
    if metadata:
        payload["metadata"] = metadata   # ← MINTIFY addition
```

After this change, `body.metadata` is preserved end-to-end and the pipeline
can see which Knowledge collection the user attached to the active model.

> This is a fork-local patch. If you ever update OWUI to a newer upstream
> release, re-apply it.

---

## The final shape of the prompt sent to vLLM

For a real user question on a model with a per-model system prompt and an
attached Knowledge collection, the chat-completions payload contains exactly
two messages:

```jsonc
{
  "model": "cyankiwi/Qwen3.5-27B-AWQ-4bit",
  "messages": [
    {
      "role": "system",
      "content": "<OWUI per-model system prompt>\n\n[KONTEXT]\n[Quelle: <file>, Seite <n>, Relevanz <score>]\n<chunk text>\n\n---\n\n[Quelle: ...]\n<chunk text>\n[/KONTEXT]\n"
    },
    { "role": "user", "content": "<the original user question>" }
  ],
  "temperature": 0.7,
  "stream": true
}
```

No `### Task:` wrappers, no `<source id="...">` blocks, no MINTIFY persona
mixed in unless the model has no per-model prompt of its own.

---

## How to verify

1. **End-to-end check** — pick a custom model in OWUI with a Knowledge
   collection attached, ask a question about a topic that is **not** in that
   collection. The model should now respond honestly that the context does
   not cover it.
2. **Confirm filtered retrieval** —
   `docker logs mintify-rag-service 2>&1 | grep "Filtering Qdrant"` should
   show the file UUIDs being applied as a filter.
3. **Confirm meta-tasks bypass RAG** —
   `docker logs mintify-rag-service 2>&1 | grep "passthrough"` should show
   one passthrough call per title/tag generation, with no
   `[RAG] Retrieved N chunks` line nearby.
4. **Confirm exactly one system prompt** —
   `docker logs mintify-rag-service 2>&1 | grep -A 30 "Message 1"` should
   show a single system message containing either the OWUI prompt or the
   pipeline persona — never both.

---

## Known leftover items (decide later)

- **OWUI still runs its own RAG retrieval** internally. The chunks it
  produces are stripped by the pipeline, so they don't reach vLLM, but the
  retrieval still costs CPU. To eliminate entirely, set the RAG engine to
  "None" in OWUI Admin → Settings → Documents.
- **Query Generation** still produces one extra LLM call per user message
  (now routed through `/api/passthrough`, so cheap, but still a call). Turn
  off in Admin → Settings → Interface if not needed.
- **`file_ids: None` semantics**. When OWUI does not include a `knowledge`
  field at all (e.g. base manifold model with no attachments), we currently
  fall through to "search the whole index". If you want strict scoping
  ("no knowledge attached = no retrieval"), change `_extract_file_ids` to
  return `[]` instead of `None` in that branch.
