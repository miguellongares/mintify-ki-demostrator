# Kompetenzniveau — per-user knowledge level

This document explains the **Kompetenzniveau** feature (built August 2026 on branch
`kompetenz_level`): every learner carries a financial-literacy competence level
from 0 to 3. The model **reads** that level on every chat turn to adapt how deeply
it explains things, and **writes** it itself after running a short competence
diagnosis (Kompetenzdiagnose).

It is deliberately **backend-only for the learner-facing side** — there is no
sidebar/chat UI element. The one exception, added later (August 2026), is an
**admin-facing "Kompetenzniveau" checkbox** in OWUI Admin Panel → Models, which
lets an admin exclude a specific model from the feature entirely — see
[Per-model opt-out](#per-model-opt-out). See also
[Where you can actually see it](#where-you-can-actually-see-it) and
[Deferred work](#deferred-work).

---

## TL;DR

| Question | Answer |
|---|---|
| What is stored? | One integer 0–3 per OWUI user, plus a Begründung and a timestamp |
| Where is the data? | `data/knowledge_level/knowledge_level.db` on the host (SQLite, gitignored, survives rebuilds) |
| Which backend serves it? | The **rag-backend** (`mintify-rag-service`) — no new container |
| How does the model *read* it? | It is injected into the system prompt as a `[LERNENDENPROFIL]` block on every turn |
| How does the model *write* it? | It emits a marker `[[KOMPETENZNIVEAU:2\|Begründung]]` inside its answer; the backend strips it out and stores it |
| Is that OpenAI tool-calling? | **No** — vLLM here has no tool-call parser. It is a prompt contract + a stream filter. See [Why not native tool-calling](#why-not-native-tool-calling) |
| Does the user see the marker? | **No** — it never reaches the browser |
| Is it per chat or global? | Global per user, across all chats and sessions |
| Where does the diagnosis quiz live? | In **OWUI Admin Panel → Models** as a per-model system prompt — it is *configuration, not code* |
| Kill-switch | `KOMPETENZ_ENABLED=false` |
| Rebuild needed? | No — `rag-backend/app` is bind-mounted; a container restart is enough |
| Caddy change needed? | No — the endpoints live under `/api/*` on the rag-backend, already covered by the `/rag-api/*` route |

---

# Part 1 — How it works (high level)

## The problem it solves

Before this, every learner got the same answer at the same depth. A first-semester
student and someone who already knows what compound interest is received identical
explanations. MINTIFY is a *financial education* project, so adapting to prior
knowledge is the whole point.

That requires two things the system did not have:

1. A place to **remember** what a learner knows, surviving across chats.
2. A way for the model to **write** to that place — because the model is the only
   thing in the stack that can judge an answer to a diagnostic question.

## The scale

| Level | Meaning (`LEVEL_LABELS`) |
|---|---|
| 0 | kein bzw. kaum finanzielles Vorwissen |
| 1 | Grundlagen vorhanden |
| 2 | fortgeschrittenes Verständnis |
| 3 | Expertenwissen |

The original request described a binary 0/1 outcome from a single Zinseszins
question. The scale was widened to 0–3 so the follow-up "anwendungsorientierte
Entscheidungssituation" can lift someone to 2 or 3 later without a schema change.
A single quiz question naturally maps onto 0/1 within that range.

## The lifecycle

```
┌─ FIRST TURN, level unknown ────────────────────────────────────────────┐
│                                                                        │
│  System prompt gets:  [LERNENDENPROFIL] noch nicht ermittelt           │
│                       [WERKZEUG] how to save a level                   │
│                                                                        │
│  Model runs the Kompetenzdiagnose (the Zinseszins question)            │
│  User answers "A"                                                      │
│                                                                        │
│  Model streams:  "Ihre Antwort ist richtig … Sie sind Niveau 1."       │
│                  "[[KOMPETENZNIVEAU:1|Zinseszinsfrage korrekt]]"       │
│                            │                                           │
│                            ▼                                           │
│              backend strips the marker  ──►  SQLite                    │
│              user sees only the prose                                  │
└────────────────────────────────────────────────────────────────────────┘

┌─ EVERY LATER TURN, in ANY chat ────────────────────────────────────────┐
│                                                                        │
│  System prompt gets:  [LERNENDENPROFIL] Niveau 1 von 3                 │
│                       "Passe Wortwahl und Erklärtiefe an."             │
│                       "Führe KEINE erneute Diagnose durch."            │
│                                                                        │
│  Model answers at that level. No marker emitted, nothing stored.       │
└────────────────────────────────────────────────────────────────────────┘
```

## The flow through the stack

```
Browser
  → Open Web UI (:3000)
    → Pipelines (:9099)        pipelines/mintify_pipeline.py
      │                        forwards user_id (already did, for personal files)
      → RAG backend (:8080)    rag-backend/app/routers/rag.py
        │
        ├─ READ   services/knowledge_level.py   get(user_id)
        │         services/level_tool.py        → [LERNENDENPROFIL] + [WERKZEUG]
        │                                          appended to the system prompt
        │
        ├─ vLLM on RunPod (:8002)               streams the answer back
        │
        └─ WRITE  services/level_tool.py        filter_stream() spots the marker,
                  services/knowledge_level.py   set() persists it
                     └─ SQLite  data/knowledge_level/knowledge_level.db

Side channel (browser → rag-backend, like the Bewertung panel):
  → GET/POST/DELETE /api/knowledge-level   routers/knowledge_level.py
```

The crucial detail: **`user_id` was already flowing** to `/api/pipe`. The pipeline
started forwarding it for the Persönliche-Dateien feature (so retrieval could
include a user's own files). This feature reuses that plumbing unchanged — no
change to `mintify_pipeline.py` was needed at all.

## How it gets stored

SQLite, at `data/knowledge_level/knowledge_level.db` (bind-mounted into the
container at `/data/knowledge_level`). Two tables:

**`user_level`** — the current state, one row per user, UPSERT on write:

| column | |
|---|---|
| `user_id` | TEXT PRIMARY KEY — the OWUI user id |
| `level` | INTEGER 0–3 |
| `reason` | the model's short Begründung |
| `source` | `"diagnose"` (model) or `"manual"` (API) |
| `updated_at` | ISO-8601 UTC |

**`level_history`** — append-only, one row per change, never overwritten:

| column | |
|---|---|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT |
| `user_id`, `level`, `reason`, `source` | as above |
| `chat_id` | which chat the change happened in |
| `created_at` | ISO-8601 UTC |

`level_history` exists because MINTIFY is a **research project**: how a learner's
diagnosed level moves over time is more interesting than its final value. It is
deliberately **not** deleted when a user resets their level.

Identity is never stored here. The `user_id` comes from OWUI (verified via
`services/owui_auth.py` → `GET /api/v1/auths/`), exactly as in the personal-files
feature. This store just trusts the id it is handed and scopes every query by it.

## How the model reads it

On every `/api/pipe` request, after retrieval and after the web-search block, the
backend looks up the level and appends two blocks to the system prompt:

```
[LERNENDENPROFIL]
Kompetenzniveau der lernenden Person: 1 von 3 (Grundlagen vorhanden).
Begründung der letzten Einstufung: Zinseszinsfrage korrekt beantwortet
Zuletzt aktualisiert: 2026-08-17
Passe Wortwahl, Erklärtiefe, Fachbegriffe und Beispiele an dieses Niveau an.
Führe KEINE erneute Kompetenzdiagnose durch, solange du keinen konkreten Anlass dazu hast.
[/LERNENDENPROFIL]

[WERKZEUG: Kompetenzniveau speichern]
Du kannst das Kompetenzniveau der lernenden Person dauerhaft speichern.
Schreibe dazu — und NUR dann, wenn du das Niveau tatsächlich ermittelt oder
begründet geändert hast — genau einmal am Ende deiner Antwort die Zeile:
[[KOMPETENZNIVEAU:<Zahl 0-3>|<kurze Begründung>]]
Skala: 0 = kein Vorwissen, 1 = Grundlagen, 2 = fortgeschritten, 3 = Experte.
Die Begründung ist ein kurzer Satz ohne eckige Klammern.
Diese Zeile wird automatisch verarbeitet und der Person NICHT angezeigt.
Erkläre der Person das Ergebnis deshalb zusätzlich in normalem Fließtext.
[/WERKZEUG]
```

When no level is stored yet, the first block instead says *"noch nicht ermittelt"*
and invites the model to run a diagnosis.

**This is what makes the feature "smooth":** the tool contract is appended by the
backend to *whatever* system prompt is active. Any persona configured in OWUI —
"KI-Experte", a Lehrer persona, a future one — gains the capability for free,
without its prompt knowing the marker syntax exists.

## How the model writes it

The model writes a marker line inside its normal answer:

```
[[KOMPETENZNIVEAU:1|Zinseszinsfrage korrekt beantwortet]]
```

The backend intercepts it in the token stream, removes it, and stores the level.
The user never sees it. Because the marker is invisible, the prompt also instructs
the model to state the result in prose — which the original diagnosis script
already required ("mache es der Person transparent").

## The part that is configuration, not code

The actual diagnosis — the Zinseszins question, the answer options, the teaching
persona — is **not in the repository**. It belongs in
**OWUI Admin Panel → Models** as the per-model system prompt, because
`routers/rag.py` resolves:

```python
chosen = req.owui_system_prompt.strip() or req.base_system_prompt.strip()
```

so the admin-configured prompt wins over the pipeline's fallback persona.

Two rules when writing that prompt:

- **Do not restate the storage instruction.** The original draft said *"speichere
  bitte das Kompetenzniveau '1'"*. Leave that out — the backend appends the
  authoritative 0–3 contract, and two conflicting instructions confuse the model.
  Keep the question and the "make it transparent" part.
- **No stray `{` or `}`** other than `{context}` — `routers/rag.py` runs
  `.format(context=…)` over that prompt, and a loose brace raises.

## Where you can actually see it

There is **no UI element**. What a learner sees is only the effect: explanations
pitched at their level, plus whatever the model says in prose. To inspect it:

**Browser console** (works in dev and prod):

```js
await (await fetch('http://localhost:8088/api/knowledge-level',
  {headers: {Authorization: `Bearer ${localStorage.token}`}})).json()
// production: `${location.origin}/rag-api/api/knowledge-level`
```

**Logs** — the level is printed on every turn and on every write:

```bash
docker logs mintify-rag-service 2>&1 | grep "\[Kompetenz\]"
```

**The prompt the model actually received** — `llm_client.stream()` has
`debug = True` hardcoded, so the log contains an `------Message 1-------` dump
including the `[LERNENDENPROFIL]` block.

**The database:**

```bash
sqlite3 data/knowledge_level/knowledge_level.db \
  "SELECT user_id, level, reason, updated_at FROM user_level;"
```

---

# Part 2 — How it was built (technical)

## Why not native tool-calling

The obvious implementation is an OpenAI function/tool call
(`set_kompetenzniveau(level, reason)`). It was rejected for two independent
reasons, either of which alone is blocking:

**1. vLLM is not launched with a tool-call parser.** From
`scripts/init_models.sh`:

```bash
"cyankiwi/Qwen3.5-27B-AWQ-4bit:8002:0.65:vllm-8002:16384:--reasoning-parser qwen3 --enable-prefix-caching --default-chat-template-kwargs {\\\"enable_thinking\\\":false}"
```

There is no `--enable-auto-tool-choice` and no `--tool-call-parser`. Sending
`tools=[…]` would come back as unparsed prose, not `tool_calls`. Adding those flags
means **restarting production inference on RunPod**.

**2. The transport cannot carry tool calls.** The pipeline↔backend hop is
`text/plain`, not SSE:

- `routers/rag.py` returns `StreamingResponse(..., media_type="text/plain; charset=utf-8")`
- `mintify_pipeline._stream()` reads it with `r.iter_text()`

So `delta.tool_calls` has nowhere to go. A real tool loop would mean rebuilding
that transport *and* adding a two-phase call in `token_generator()`.

The prompt-marker approach needs neither. It is **entirely contained in
`rag-backend/`** — no pipeline change, no frontend change, no inference change.

The trade-off, stated plainly: the model can emit a malformed marker or forget it.
That is mitigated by validating the level at the store boundary and by the regex
only accepting `0-3`, but it is a genuinely weaker guarantee than native
tool-calling. If `--enable-auto-tool-choice --tool-call-parser hermes` is ever
added to the vLLM launch, only `filter_stream` needs replacing — the store and the
HTTP API stay as they are.

## File map

**New files**

| File | Purpose |
|---|---|
| `rag-backend/app/services/knowledge_level.py` | `KnowledgeLevelStore` — SQLite persistence |
| `rag-backend/app/services/level_tool.py` | German prompt blocks + the stream filter |
| `rag-backend/app/routers/knowledge_level.py` | HTTP API |

**Modified files**

| File | Change |
|---|---|
| `rag-backend/app/routers/rag.py` | read path (`:196-207`) + write path (`:229-248`) |
| `rag-backend/app/config.py` | `knowledge_level_dir`, `kompetenz_enabled` |
| `rag-backend/app/dependencies.py` | `_knowledge_level` singleton + `get_knowledge_level()` |
| `rag-backend/app/main.py` | router registration |
| `docker-compose.yml` | `./data/knowledge_level` volume + `KOMPETENZ_ENABLED` |
| `.claude/CLAUDE.md` | feature documentation |

**Later addition (per-model opt-out, August 2026)** touched two files this
list originally said would stay untouched:

| File | Change |
|---|---|
| `pipelines/mintify_pipeline.py` | `_kompetenz_capability_enabled()` + forwards `kompetenz_capability` |
| `rag-backend/app/routers/rag.py` | `PipeRequest.kompetenz_capability`, ANDed into `kompetenz_active` |
| `open-webui-src/.../Models/Capabilities.svelte` | new `kompetenzniveau` checkbox entry |
| `open-webui-src/.../Models/ModelEditor.svelte` | new `kompetenzniveau` default in `capabilities` |
| `open-webui-src/.../i18n/locales/de-DE/translation.json` | help-text translation |

## The store — `services/knowledge_level.py`

Mirrors `services/personal_docs.py` exactly, because that pattern is already proven
in this codebase: one long-lived connection created at singleton init with
`check_same_thread=False`, a single `threading.Lock` around every statement
(FastAPI runs sync endpoints in a threadpool), `row_factory = sqlite3.Row`, and
idempotent `CREATE TABLE IF NOT EXISTS` in `__init__`.

The one thing worth reading is `set()` — it writes both tables in one transaction
and validates the input, because **the model is an untrusted writer**:

```python
def set(self, user_id, level, reason="", source="diagnose", chat_id=None) -> dict:
    if not user_id:
        raise ValueError("user_id is required")
    if not isinstance(level, int) or not (MIN_LEVEL <= level <= MAX_LEVEL):
        raise ValueError(f"level must be an int in {MIN_LEVEL}..{MAX_LEVEL}, got {level!r}")

    now = datetime.now(timezone.utc).isoformat()
    with self._lock:
        self._db.execute(
            "INSERT INTO user_level (user_id, level, reason, source, updated_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET"
            "   level = excluded.level, reason = excluded.reason,"
            "   source = excluded.source, updated_at = excluded.updated_at",
            (user_id, level, reason, source, now),
        )
        self._db.execute(
            "INSERT INTO level_history (user_id, level, reason, source, chat_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, level, reason, source, chat_id, now),
        )
        self._db.commit()
```

`clear()` deletes from `user_level` only — history survives a reset on purpose.

## The prompt blocks — `services/level_tool.py`

The blocks are plain module constants (`_PROFILE_KNOWN`, `_PROFILE_UNKNOWN`,
`TOOL_CONTRACT`) assembled by `profile_block(record)`.

**One subtlety governs where they are appended.** `routers/rag.py` runs
`chosen.format(context=context)` over the system prompt. Any literal `{` or `}`
in text that goes through `.format()` raises. A stored Begründung is model-written
text and could contain braces. So the blocks are appended **after** the `.format()`
call — the same discipline `WEB_CONTEXT_BLOCK` already follows for web-page text.
`TOOL_CONTRACT` contains no braces at all, and `profile_block()` fills its
placeholders with `.format()` on its own template, never on the admin's prompt.

## The stream filter — the hard part

This is the piece worth understanding, because the naive version is wrong.

**The naive version:** wait for the answer, then `answer.replace(marker, "")`.
That fails because the response is streamed — there is no "after".

**The nearly-as-naive version:** `chunk.replace(marker, "")` per chunk. That fails
because vLLM emits **token by token**. The marker actually arrives as something
like:

```
"[[KOMP"  →  "ETENZNIVEAU"  →  ":1|Zinseszins"  →  " korrekt]]"
```

No single chunk contains the marker, so nothing matches, and the marker leaks into
the browser character by character — the user watches `[[KOMPETENZNIVEAU:1…` type
itself out.

**The actual solution:** a buffering filter that holds back only the suffix that
could still become a marker, and emits everything before it immediately.

```python
_MARKER_RE = re.compile(r"\[\[KOMPETENZNIVEAU\s*:\s*([0-3])\s*(?:\|([^\]]*))?\]\]")
_PREFIX = "[[KOMPETENZNIVEAU"
_MAX_HOLD = 400


def _hold_index(buf: str) -> int:
    """Leftmost index from which `buf` could still be growing into a marker."""
    i = buf.find("[")
    while i != -1:
        tail = buf[i:]
        # Either we are already inside the marker, or the tail so far is still a
        # possible prefix of it.
        if tail.startswith(_PREFIX) or _PREFIX.startswith(tail):
            return i
        i = buf.find("[", i + 1)
    return len(buf)
```

The scan runs **left to right** and returns the *leftmost* viable candidate. That
matters: given `"a[[KOMPETENZNIVEAU:1|x [foo"`, holding from the later `[foo`
would have already emitted the marker's opening. Text before the returned index is
provably safe to send.

Per chunk, `_consume()` then:

1. Drains every **complete** `_MARKER_RE` match — emitting the text before it,
   firing `on_level(level, reason)`, continuing after it.
2. Computes `_hold_index()`, emits everything before it, keeps the rest buffered.
3. **Releases the buffer if it exceeds `_MAX_HOLD` (400 chars)** — a `[[` that
   never closes was never a marker, and without this the stream would stall
   forever on a stray bracket.

At end of stream everything remaining is flushed.

Latency cost: at most the length of a partially-formed marker, and only while one
is actually forming. Normal prose containing `[` (`"siehe [1]"`) is released on the
very next character, because `"[1]"` fails the prefix test immediately.

Three behaviours worth noting:

- **Multiple markers** each fire. `set()` is an UPSERT, so the last one wins —
  correct if the model corrects itself mid-answer — and `level_history` records
  each, which is itself useful research data.
- **`on_level` is wrapped in try/except.** A failed database write must never break
  the user's answer.
- **The filter wraps the whole `llm.stream()` output**, including the
  `<details type="reasoning">` block that `llm_client._reasoning_block()` emits as
  one chunk when 💡 Nachdenken is on. A marker written inside the model's
  reflection therefore also counts. That is a deliberate simplification: it is
  still the model's stated conclusion, and it keeps this a single pass over one
  text stream.

## Per-model opt-out

An admin can turn this feature off for one specific model — e.g. it should
only run on the "Lehrer"/diagnosis persona, not on every model in the
dropdown — via a **"Kompetenzniveau" checkbox** next to the existing
Vision/Usage/Citations checkboxes in OWUI Admin Panel → Models →
create/edit → "Fähigkeiten" ("Capabilities").

This reuses OWUI's existing generic `meta.capabilities: dict` mechanism
instead of adding new plumbing:

- **Frontend** — `Capabilities.svelte` renders one checkbox per key already
  present in the `capabilities` object it's bound to, so adding
  `kompetenzniveau` to the `helpText` map and to the default `capabilities`
  object in `ModelEditor.svelte` is enough; no new template markup needed.
  Saved as `model.meta.capabilities.kompetenzniveau` exactly like `vision`/
  `usage`/`citations`.
- **Pipeline** — OWUI already puts the full per-model `meta` dict (capabilities
  included) at `body.metadata.model.info.meta` on every chat request — the
  same object `_extract_file_ids()` already reads `knowledge` from
  (`pipelines/mintify_pipeline.py:~85`). A new sibling function,
  `_kompetenz_capability_enabled(body)`, reads `capabilities.kompetenzniveau`
  from there and forwards it as `kompetenz_capability` in the `/api/pipe`
  payload.
- **Backend** — `PipeRequest.kompetenz_capability: bool = True` in
  `routers/rag.py`, ANDed into the existing `kompetenz_active` flag alongside
  `settings.kompetenz_enabled` (global kill-switch) and `bool(req.user_id)`.
  Since both the read path (profile/tool-contract injection) and the write
  path (marker filtering) already branch on that one `kompetenz_active`
  variable, no other code needed to change.

**Back-compat default: missing key = ON.** A model saved before this checkbox
existed has no `kompetenzniveau` key in its stored `meta.capabilities` at all.
`_kompetenz_capability_enabled()` treats that case as `True`, so every
existing model (including whichever one currently runs the diagnosis) keeps
behaving exactly as it did before this change, until an admin opens it in the
editor and explicitly unchecks the box.

## Wiring into the chat flow — `routers/rag.py`

**Read path** (`rag.py:196-207`), placed after the web block and before the
`Aktuelles Datum:` prefix:

```python
kompetenz_active = settings.kompetenz_enabled and bool(req.user_id)
if kompetenz_active:
    record = levels.get(req.user_id)
    enriched_system = (
        enriched_system.rstrip()
        + level_tool.profile_block(record)
        + level_tool.TOOL_CONTRACT
    )
```

Gated on `req.user_id`: with no user there is nowhere to store a result, so
offering the tool would only invite a marker that has to be discarded.

**Write path** (`rag.py:229-248`):

```python
def on_level(level: int, reason: str) -> None:
    levels.set(req.user_id, level, reason, source="diagnose", chat_id=req.chat_id)
    print(f"[Kompetenz] user={req.user_id} → Niveau {level} …", flush=True)

def token_generator():
    tokens = llm.stream(
        enriched, req.model_id, req.temperature, enable_thinking=req.thinking
    )
    if kompetenz_active:
        tokens = level_tool.filter_stream(tokens, on_level)
    yield from tokens
    if web_results:
        yield _quellen_footer(web_results)
```

Wrapping the generator rather than post-processing keeps the stream lazy — tokens
still flow to the browser as they arrive.

## The HTTP API — `routers/knowledge_level.py`

`require_user()` is copied verbatim from `routers/personal_docs.py`: Bearer header
→ `OwuiAuth.resolve_user()` → 401 on failure. Every query is scoped by the
resolved `user["id"]`.

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/knowledge-level` | own level → `{level, label, reason, source, updated_at}`, or `level: null` |
| `POST` | `/api/knowledge-level` | `{level, reason?}` → manual set, `source="manual"`; **400** if out of range |
| `DELETE` | `/api/knowledge-level` | reset to "not yet diagnosed"; history kept |
| `GET` | `/api/knowledge-level/history` | own history, newest first |
| `GET` | `/api/knowledge-level/all` | **admin only** (`role != "admin"` → 403) — research export |

**Only GET/POST/DELETE.** `main.py` sets
`allow_methods=["GET", "POST", "DELETE", "OPTIONS"]`, so a `PUT` or `PATCH`
endpoint would fail CORS preflight from the browser.

## Configuration and deployment

`rag-backend/app/config.py`:

```python
knowledge_level_dir: str = "/data/knowledge_level"
kompetenz_enabled: bool = True
```

`docker-compose.yml` (`mintify-rag-service`):

```yaml
    environment:
      - KOMPETENZ_ENABLED=${KOMPETENZ_ENABLED:-true}
    volumes:
      - ./data/knowledge_level:/data/knowledge_level
```

Deployment is cheap because `./rag-backend/app:/app/app` is bind-mounted — Python
changes need no image build:

```bash
docker compose up -d mintify-rag-service     # only because the volume is new
docker logs mintify-rag-service | grep KnowledgeLevelStore
```

Production needs **no Caddyfile change**: the endpoints live under `/api/*` on the
rag-backend, already covered by the existing `/rag-api/*` route.

---

## Verification

**The filter, without an LLM** — this is the piece most worth testing, since a
regression silently leaks markers to users:

```bash
docker exec -it mintify-rag-service python -c "
from app.services.level_tool import filter_stream
got = []
chunks = ['Sehr gut. ', '[[KOMP', 'ETENZNIVEAU', ':2|Zins', 'eszins korrekt]]', ' Ende']
out = ''.join(filter_stream(iter(chunks), lambda l, r: got.append((l, r))))
assert 'KOMPETENZ' not in out and got == [(2, 'Zinseszins korrekt')]
t = 'siehe [1] und [[nicht]] hier'
assert ''.join(filter_stream(iter([t]), lambda *a: None)) == t
print('OK')
"
```

Cases covered during development: marker split across chunks, character-by-character
streaming, marker without a Begründung, unterminated marker (must be released, not
swallowed), non-marker brackets passing through untouched, and multiple markers in
one answer.

**End to end:**

1. `DELETE /api/knowledge-level` to reset.
2. New chat with the Lehrer model, run the diagnosis, answer **A**.
3. The reply must explain the level **and contain no `[[…]]` anywhere** — this is
   the visual proof the filter works.
4. `docker logs mintify-rag-service | grep "\[Kompetenz\]"` shows the write.
5. Open an **unrelated new chat**; the `------Message 1-------` dump must contain
   `[LERNENDENPROFIL]` with that level — proving it crosses chat boundaries.
6. Answer **B** in a fresh diagnosis → level drops; `/history` shows both entries.

---

## Known limitations

- **The model can forget or malform the marker.** Inherent to a prompt contract.
  The regex accepts only `0-3` and `set()` re-validates, so a malformed marker
  fails closed (nothing stored) rather than storing garbage — but nothing forces
  the model to emit one.
- **A Begründung containing `]` breaks its own marker.** The regex uses `[^\]]*`,
  so the marker never closes and is released as visible text after 400 chars. The
  prompt explicitly asks for "ein kurzer Satz ohne eckige Klammern".
- **Markers inside the 💡 Nachdenken reasoning block also count** — see the filter
  section above.
- **The judges don't know the level.** A deliberately simple level-0 answer could
  be rated `schlecht` by the Bewertung panel for lacking depth.

## Deferred work

- **Sidebar UI** — a "Kompetenzniveau" badge next to `PersonalFiles.svelte` with a
  "Diagnose zurücksetzen" button. Needs an `apis/kompetenz/index.ts` copying the
  `getBaseUrl()` shape from `apis/personalDocs/index.ts`, a small Svelte component,
  and an `open-webui` image rebuild. The `GET`/`DELETE` endpoints already back it.
- **Level-aware judging** — cache the level in `services/context_cache.py` next to
  the retrieval context so the Bewertung judges grade against the intended level.
- **Native tool-calling** — revisit if `--enable-auto-tool-choice
  --tool-call-parser hermes` is ever added to `scripts/init_models.sh`.
