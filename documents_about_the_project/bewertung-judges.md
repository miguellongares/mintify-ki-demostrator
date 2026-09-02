# Bewertung — die drei Richter (LLM judges)

This document explains the **Bewertung** feature: the right-hand sidebar in the
chat where three independent LLM judges rate the answer the user just received.
It covers what the judges are for, the three prompt variants (A/B/C) the client
supplied in **August 2026**, how a verdict travels from vLLM to a coloured card
in the browser, and every file involved.

The judges are **not** part of the chat pipeline. They run on a **separate
browser → rag-backend call** that fires *after* the answer has finished
streaming. That separation is the source of most of the design decisions below —
above all the [context cache](#the-context-cache--why-it-exists).

---

## TL;DR

| Question | Answer |
|---|---|
| What is it? | Three LLM judges rate every chatbot answer from the *user's* point of view |
| Where does the user see it? | Right sidebar, toggled by the **"Richter Modus"** button in the chat Navbar |
| How many judges? | Exactly 3, run in parallel — one per prompt variant A / B / C |
| Are the three prompts identical? | **No** (since August 2026). Shared preamble + three different role framings |
| What is judged? | The **whole conversation**, not just the last turn (since September 2026) |
| What do they rate against? | The five ethical rules: Faktentreue, kein Verkaufsdruck, Autonomie, Transparenz, Fürsorge |
| What is the verdict scale? | `gut` \| `mittel` \| `eher_schlecht` |
| Which model? | The same vLLM chat model, auto-discovered from `GET {JUDGE_VLLM_URL}/v1/models` |
| Which backend? | The **rag-backend** (`mintify-rag-service`) — no extra container |
| Endpoint | `POST /api/judge` with `{query, answer, chat_id}` |
| Output format from the model | **Plain text** — `Urteil: Gut` + 1–2 sentences. Not JSON (changed August 2026) |
| Do judges see the retrieved documents? | Yes — via an in-process cache keyed by `chat_id`. See [the context cache](#the-context-cache--why-it-exists) |
| Rebuild needed for prompt changes? | **No** — `rag-backend/app` is bind-mounted; `docker compose restart rag-backend` is enough |
| Rebuild needed for panel changes? | Yes — `docker compose build open-webui && docker compose up -d open-webui` |
| Caddy change needed? | No — `/api/judge` sits on the rag-backend, already covered by the `/rag-api/*` route |

---

# Part 1 — How it works (high level)

## What the feature is for

MINTIFY answers questions about **Altersvorsorge** (retirement provision) — a
domain where a plausible-sounding but wrong or subtly *sales-flavoured* answer
does real damage. The judges are the safety net. Quoting the client's brief:

> Sie sind ein Coach/ Richter/ Dritter, der die Chatunterhaltung für den Nutzer
> bewertet. Sie schützen ihn vor falschen, im schlechten Sinne überzeugenden,
> unreflektierten oder betrügerischen Antworten.

Note the addressee: the verdict text is written **to the learner as coaching**,
not to a developer as a QA report. "Der Bot nennt keine Nachteile — frag ruhig
nach Alternativen" is the intended tone, not "Rule 2 violated".

## The five ethical rules

Every judge grades against the same five rules. They are the shared foundation
of all three variants:

| # | Rule | What it checks |
|---|---|---|
| 1 | **Faktentreue** | Nothing false or invented; claims are covered by sources or context |
| 2 | **Kein Verkaufsdruck** | No pushing toward products, no one-sided praise; drawbacks and alternatives are named |
| 3 | **Autonomie** | The decision stays with the user; the answer encourages reflection instead of persuading |
| 4 | **Transparenz** | Limits and uncertainties are disclosed; no financial advice is implied |
| 5 | **Fürsorge** | Understandable, fits the person's situation, no scaremongering, no exploiting ignorance |

## The three variants

All three judges share one preamble (purpose + the five rules + the output
format) and then get a **different role framing**. This is deliberate: three
samples of one prompt produce three near-identical cards, whereas three
different framings produce three genuinely different readings of the same
answer.

| Judge | Variant | Framing |
|---|---|---|
| Richter 1 | **A** | *"Du bist ein unabhängiger Richter."* — formal, walks the five numbered rules explicitly |
| Richter 2 | **B** | *"Du arbeitest als kritischer Verbraucherschützer…"* — consumer-advocate voice, prose rather than a checklist, most adversarial about sales pressure |
| Richter 3 | **C** | *"Rolle: Unabhängiger Prüfer."* — terse, turns the rules into five yes/no questions the model answers for itself before committing |

The variants are the client's own text, kept **verbatim**. They are expected to
be re-tuned after the first round of live testing, so they live as three clearly
separated constants at the top of `judge_client.py` and nowhere else.

## The verdict scale

| Value on the wire | Label in the panel | Colour |
|---|---|---|
| `gut` | Gut | green |
| `mittel` | Mittel | yellow |
| `eher_schlecht` | Eher schlecht | red |

`eher_schlecht` ("rather poor") replaced a blunt `schlecht` in August 2026 — the
client's wording. The scale has no harsher point, so if the model writes a bare
`Schlecht` it is normalised onto `eher_schlecht`. The panel still renders the
legacy `schlecht` value so older chats do not break.

## What the user sees

The **"Richter Modus"** button in the chat Navbar (scales icon) toggles
`showJudgement`. While it is open, the cards always describe the answer
currently on screen. Three things trigger a round:

1. **An answer finishes streaming.**
2. **The panel is opened** on a chat that already has an answer — it judges that
   answer immediately instead of waiting for the next question.
3. **A stored chat is opened** from the sidebar while the panel is open — the
   newly loaded chat's last answer is judged. Starting a new chat clears the
   cards instead, since there is nothing to judge.

All three go through one helper, `syncJudgement()`, guarded by
`lastJudgedMessageId`: a call that would re-judge the answer already on the cards
does nothing, so closing and re-opening the panel is free.

> **Old chats judge without context.** The context cache is in-process and
> bounded (see [the context cache](#the-context-cache--why-it-exists)), so a chat
> from yesterday is virtually always a miss. Those judges run on the lenient
> no-context prompt and grade wording, balance and transparency rather than
> Faktentreue against sources. That is by design — the alternative would be
> grading a grounded answer against the judge's own training knowledge, which is
> the exact regression the cache was built to prevent.

```
┌ Bewertung ───────────────────────────┐
│  ● Richter 1 — Gut                   │  green, left border
│    Die Antwort ist durch die Quellen │
│    gedeckt und lässt dir die         │
│    Entscheidung.                     │
│                                      │
│  ● Richter 2 — Eher schlecht         │  red
│    Der Bot drängt Richtung Riester   │
│    und nennt keine Nachteile.        │
│                                      │
│  ● Richter 3 — Mittel                │  yellow
│    Fachlich richtig, aber die        │
│    Unsicherheiten bleiben ungesagt.  │
└──────────────────────────────────────┘
```

Before the results arrive, all three cards show a spinner and
*"Richter N — denkt nach…"*. The three judges run **in parallel**, so the wall
time is one judge call, not three.

## The flow through the stack

```
Browser
  │  Chat.svelte — three entry points, one helper:
  │    (a) stream finished     → judgeMessage(message)
  │    (b) panel opened        → syncJudgement()   ($: on $showJudgement)
  │    (c) stored chat loaded  → syncJudgement()   (end of loadChat success)
  │        new chat started    → syncJudgement()   → resetJudge(), cards cleared
  │
  │  judgeMessage → judgementPaneComponent.requestJudge(
  │                    userMsg,
  │                    removeDetails(answer, ['reasoning']),   ← Gedankengang stripped
  │                    $chatId)
  ▼
JudgementPanel.svelte
  │  POST {ragApiBase()}/api/judge   {query, answer, chat_id}
  │      dev      → /rag-api (Vite proxy)
  │      docker   → http://localhost:8088
  │      prod     → {origin}/rag-api  → Caddy strips → :8088
  ▼
rag-backend  routers/judge.py
  │  context_cache.get(chat_id)  →  [DOKUMENTE] … [WEB-RECHERCHE] …
  ▼
services/judge_client.py  judge_all()
  │  ThreadPoolExecutor(3)
  ├── judge(1) → Variante A ┐
  ├── judge(2) → Variante B ├─→ vLLM  {JUDGE_VLLM_URL}/v1/chat/completions
  └── judge(3) → Variante C ┘        (enable_thinking: false)
  │
  │  each returns plain text:  "Urteil: Gut\nDie Antwort ist …"
  │  _parse_verdict()  →  {rating, reasoning, variant}
  ▼
{"judges": [{rating, reasoning, variant} × 3]}  →  three coloured cards
```

## The context cache — why it exists

This is the single most important thing to understand about the judges.

`/api/judge` is a **fresh browser request**. It knows the question and the
answer, but it has never seen the document chunks or web-search results the
answer was built from. Left like that, a judge asked *"is this factually
accurate?"* falls back on its own training knowledge — which is older than the
web results and contains none of the user's documents. The observed result
(before July 2026) was judges rating **correct, well-grounded answers as
`schlecht`**, purely because they personally could not verify a fact.

The fix: `/api/pipe` caches the exact context of each answer just before it
starts streaming, and `/api/judge` reads it back.

```
POST /api/pipe          (chat turn)
  retrieval + web search
  context_cache.store(chat_id, context=<chunks>, web=<web results>, question=…)
  → stream the answer

POST /api/judge         (afterwards, separate call, same chat_id)
  context_cache.get(chat_id)
  → injected into every judge prompt as [KONTEXT]
```

The judge prompt then states plainly, tied to rule 1:

> Der KONTEXT ist deine Wahrheitsquelle. […] Stufe eine Aussage NICHT herab, nur
> weil du sie selbst nicht kennst oder nicht verifizieren kannst — prüfe sie
> ausschließlich gegen den Kontext.

On a **cache miss** (a model-only answer, no documents, no web, or a server
restart) a shorter no-context note replaces the block: judge plausibility, and
explicitly do *not* downgrade merely for being unverifiable. The other four
rules are unaffected by a cache miss — they are about *how* the answer is
written, which needs no sources at all.

---

# Part 2 — How it is built (technical)

## File map

| File | Role |
|---|---|
| `rag-backend/app/services/judge_client.py` | **The heart.** The prompts (preamble, context blocks, variants A/B/C), the vLLM calls, the verdict parser |
| `rag-backend/app/routers/judge.py` | `POST /api/judge` — assembles the cached context, calls the client, shapes the response |
| `rag-backend/app/services/context_cache.py` | Bounded in-process LRU: `chat_id → {context, web, question}` |
| `rag-backend/app/routers/rag.py` | Writes into the cache (`context_cache.store(...)`) on every chat turn |
| `rag-backend/app/dependencies.py` | Builds the single `JudgeClient` at startup; injected via `Depends(get_judge_client)` |
| `rag-backend/app/config.py` | `judge_vllm_url` (env `JUDGE_VLLM_URL`) |
| `open-webui-src/src/lib/components/chat/JudgementPanel.svelte` | The sidebar: fetch, colours, labels, loading state |
| `open-webui-src/src/lib/components/chat/Chat.svelte` | Triggers `requestJudge()` when an answer finishes |
| `open-webui-src/src/lib/components/chat/Navbar.svelte` | The "Richter Modus" toggle button |
| `open-webui-src/src/lib/stores/index.ts` | `showJudgement` store |
| `open-webui-src/src/lib/utils/ragApi.ts` | `ragApiBase()` — the one place the backend URL is decided |

## Prompt assembly — `judge_client.py`

A prompt is built per call from four blocks:

```python
prompt = "\n\n".join([
    _DATE_BLOCK,      # Aktuelles Datum: … + "ein heutiges Datum ist keine Erfindung"
    _PREAMBLE,        # Zweck + die fünf ethischen Regeln + Ausgabeformat
    _SCOPE_NOTE,      # "nicht jede Nachricht handelt von Altersvorsorge"
    source_block,     # _CONTEXT_BLOCK (cache hit) or _NO_CONTEXT_BLOCK (miss)
    conversation,     # [GESPRÄCH] the full transcript (or the last turn alone)
    _VARIANTS[i],     # A, B or C
])
```

**`_DATE_BLOCK` and `_SCOPE_NOTE` are corrections, not decoration.** Both were
added on 2026-09-01 after the judges failed a perfectly good greeting:

- The chat model **is** grounded in the real date (`Aktuelles Datum: …`, added by
  `routers/rag.py`), so it correctly opened with *"Da heute Dienstag, der
  1. September 2026 ist…"*. The judges were **not** grounded, compared that date
  against their own training cutoff, and reported *"ein fiktives Datum
  (1. September 2026)"* as a Faktentreue violation. Any prompt given to a model
  in this stack needs the same date grounding as the chat prompt — this is the
  third place that lesson has been learned, after the chat prompt itself and the
  web query rewriter.
- The client's three variants all name **Altersvorsorge** as the topic, but
  MINTIFY also runs general personas (Lehrer, Komiker, Suchmodell) and ordinary
  greetings. Judge 1 downgraded the greeting for *"ignoriert den Kontext der
  Altersvorsorge"*. `_SCOPE_NOTE` states that the topic is context, not a
  requirement, and that the five rules apply regardless. Remove this block if the
  system is ever narrowed to Altersvorsorge only.

Two further details that are load-bearing:

**The variant goes last.** Each variant restates the output format in its own
words, and recency is what keeps the model emitting `Urteil: …` instead of
drifting into a paragraph of prose. Putting the shared preamble last was tried
mentally and rejected for exactly this reason.

**`.format()` only ever touches the templates.** `context`, `query` and `answer`
are substituted *in*, never re-scanned — a `{` inside a retrieved PDF chunk or
inside the model's own answer cannot raise `KeyError` in `str.format()`. This is
the same rule `routers/rag.py` follows when it appends blocks *after*
`.format(context=…)`.

Context is truncated to `_MAX_CONTEXT_CHARS = 12000` so three parallel calls fit
comfortably in the 16k window.

## Judging the whole conversation

Until September 2026 the judges saw exactly one question and one answer. That
contradicted the prompts themselves — variants A and B say *"in diesem Gespräch"*
and *"die Chatunterhaltung"* — and it made a whole class of violation invisible:
a sales nudge in message 2 that the last answer never walks back reads as clean
when the last answer is all you see.

`Chat.svelte` now sends the full transcript alongside the last turn:

```js
const transcript = msgList
    .filter((m) => (m.role === 'user' || m.role === 'assistant') && !m.error)
    .map((m) => ({ role: m.role, content: removeDetails(m.content ?? '', ['reasoning']) }))
    .filter((m) => m.content.trim());
```

`_format_transcript()` renders it as a German dialogue and marks the final
chatbot turn, because several variants ask about it by name:

```
Nutzer: Lohnt sich Riester für mich?

Chatbot (letzte Antwort): Riester kann sich lohnen. Nachteile: …
```

Three details that keep this cheap and safe:

- **Formatted once per round, not once per judge.** `judge_all()` builds the
  string before the `ThreadPoolExecutor` fans out; all three variants share it.
- **Capped at `_MAX_TRANSCRIPT_CHARS` (8000), trimmed from the front.** Whole
  messages are dropped, never half a turn, and a single
  `[… frühere Nachrichten aus Platzgründen gekürzt …]` line is added afterwards —
  adding it inside the trim loop would make the note itself the next thing
  dropped. Recent turns are what the verdict is really about; the oldest are the
  cheapest to lose.
- **The client's variant text is untouched.** `_TRANSCRIPT_BLOCK` carries the
  redefinition instead: the whole conversation is the object of evaluation, a
  violation in an earlier message counts as much as one in the last, and where a
  variant says *"die letzte Chatbot-Antwort"* it means the chatbot's behaviour
  across the chat. Only the chatbot's turns are graded — the user's messages are
  context. This matters because the variants are expected to be re-tuned by the
  client; they stay a verbatim block that can be swapped wholesale.

Budget check: 12000 chars of context + 8000 of transcript + ~2000 of prompt is
roughly 6–7k tokens of German, comfortably inside the 16k window with room for
the 512-token answer.

## The vLLM call

```python
completion = client.chat.completions.create(
    model=model,                      # auto-discovered, cached on the instance
    messages=[{"role": "user", "content": prompt}],
    temperature=0.3,
    max_tokens=512,
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
)
```

- **Model discovery**: `_resolve_model()` does `GET {url}/v1/models` once and
  caches the first id. Falls back to the literal `"default"` if that fails, so a
  judge call still reaches a single-model vLLM.
- **`enable_thinking: False`** is mandatory. The Qwen3.5 instance is launched
  with `--reasoning-parser qwen3`; without this flag a judge could emit a
  `<think>` block on the `reasoning` delta field and return an empty `content`.
  See `llm_client.py` and the Thinking-mode doc for the full story.
- **Parallelism**: `judge_all()` runs a `ThreadPoolExecutor(max_workers=3)`.
  Results are written back **by index**, not by completion order, so Richter 1
  is always variant A regardless of which call finishes first.
- **Failure is contained per judge.** An exception inside one judge returns
  `{"rating": "mittel", "reasoning": "Bewertung fehlgeschlagen: …"}` — the other
  two cards still show real verdicts.

## The output format and the parser

Until August 2026 the judges were asked for JSON and parsed with `json.loads()`.
The client's new prompts ask for **prose**:

```
Urteil: Eher schlecht
Der Bot drängt Richtung Riester und nennt keine Nachteile — frag nach Alternativen.
```

`_parse_verdict()` replaces `json.loads()`. It is deliberately forgiving,
because a small model asked for a fixed first line will still occasionally
decorate it. Handled shapes, all covered by a local test run:

| Model output | Parsed as |
|---|---|
| `Urteil: Gut\nDie Antwort …` | `gut` + sentence |
| `Urteil: Eher schlecht — Der Bot …` | `eher_schlecht` + sentence |
| `**Urteil: Mittel**\n\nDie Angaben …` | `mittel` + sentence |
| `Urteil: eher-schlecht. Keine Quellen …` | `eher_schlecht` + sentence |
| `Urteil: Schlecht\nFalsche Zahlen.` | `eher_schlecht` (normalised) |
| A `Urteil: Gut` verdict wrapped in a markdown code fence | `gut` + sentence (fence stripped) |
| `Gut. Die Antwort bleibt neutral.` | `gut` + sentence (no `Urteil:` keyword) |
| `Urteil: Mittel` | `mittel` + *"Keine Begründung erhalten."* |
| `Die Antwort war okay.` | `mittel` + the raw text |
| `""` | `mittel` + *"Keine Begründung erhalten."* |

Two subtleties worth keeping in mind when editing the regex:

- **`eher schlecht` must be tried before `schlecht`** in the alternation, or the
  shorter branch matches first and every "eher schlecht" is read as "schlecht".
- **Unparseable output degrades, it does not throw.** A missing verdict yields
  `mittel` and keeps the raw text as the reasoning — a usable coaching sentence
  with a hedged rating beats an empty card.

## The endpoint — `routers/judge.py`

```
POST /api/judge
  {
    "query":    "…",                       ← last user message (logging + fallback)
    "answer":   "…",                       ← last assistant message (idem)
    "chat_id":  "…",
    "messages": [ {"role": "user", "content": "…"}, … ]   ← the whole chat
  }
→ { "judges": [ { "rating": "gut", "reasoning": "…", "variant": "A" }, … ] }
```

`messages` is optional. Without it the prompt falls back to `_CONVERSATION_BLOCK`
(the old single-turn shape), which keeps a stale frontend build working against a
new backend.

`_build_judge_context()` turns the cached dict into one labelled block:

```
[DOKUMENTE]
<retrieved chunks>

[WEB-RECHERCHE]
<web search results>
```

Either half may be absent. Both absent → empty string → the no-context prompt.

`variant` is an optional field on the `JudgeResult` model. It has to be declared
there explicitly — pydantic strips anything the response model does not know,
so returning it from the service alone would silently drop it.

## The cache — `context_cache.py`

An `OrderedDict` behind a `Lock`, capped at 256 chats, LRU-evicted. Not Redis,
not SQLite, on purpose:

- The rag-backend runs a **single uvicorn worker**, so one process-local dict is
  genuinely shared by `/api/pipe` and `/api/judge`.
- The data is worthless after the judge call it serves — there is nothing to
  persist.
- A restart simply produces cache misses, and a cache miss is a supported,
  handled state (the lenient prompt), not an error.

The consequence to remember: **restart the rag-backend mid-conversation and the
next judge round for an already-answered turn grades without documents.** Ask
the question again and the cache is repopulated.

## Frontend — `JudgementPanel.svelte`

```ts
export const requestJudge = async (query, answer, chatId) => { … }
```

Exported as a `const` so `Chat.svelte` can call it on the bound component
instance. Notes:

- **`removeDetails(message.content, ['reasoning'])`** in `Chat.svelte` strips
  the collapsed *Gedankengang* block before the answer reaches the judges — they
  grade the answer, not the model's internal reflection.
- **The URL is never hardcoded.** `ragApiBase()` decides between the Vite proxy
  path, `localhost:8088`, and `{origin}/rag-api`. A hardcoded `:8088` is exactly
  the bug that broke production in May 2026 (see [History](#history)).
- **`resetJudge()`** is a second exported function, called when the panel is
  opened on a chat with no answer yet — otherwise the component (which is never
  destroyed, only shrunk to `w-0`) would keep showing the previous chat's cards.
- **The failure path renders three cards**, all `mittel` /
  *"Bewertung nicht verfügbar."* — the panel never shows an empty state after a
  network error.
- `isNegative(rating)` maps both `eher_schlecht` and legacy `schlecht` to red,
  so chats judged before August 2026 still render correctly.

## Configuration

| Setting | Default | Purpose |
|---|---|---|
| `JUDGE_VLLM_URL` | `http://host.docker.internal:8002` | vLLM base URL the judges call. Not set in `docker-compose.yml` — the default applies |
| `_MAX_CONTEXT_CHARS` | `12000` | Context truncation, code constant in `judge_client.py` |
| `_MAX_ENTRIES` | `256` | Cache size, code constant in `context_cache.py` |
| `max_tokens` | `512` | Per judge call, code constant |
| `temperature` | `0.3` | Per judge call, code constant |

There is **no kill-switch env var** — the feature is off unless the user opens
the panel, which is switch enough.

## Deployment

```bash
# Prompt / parser / endpoint changes — rag-backend/app is bind-mounted
docker compose restart rag-backend

# Panel changes (labels, colours) — needs an image rebuild
docker compose build open-webui && docker compose up -d open-webui
```

No Caddy change is ever needed: `/api/judge` lives on the rag-backend, which is
already reachable through the existing `/rag-api/*` route.

## Verification

```bash
# Watch a judging round, variant by variant
docker logs -f mintify-rag-service 2>&1 | grep "\[Judge\]"
```

A healthy round looks like:

```
[Judge] POST /api/judge | query='Was ist die Riester-Rente?' | … | context=hit
[Judge] Starting 3 parallel judge calls (query='Was ist die Riester…', context_chars=4812)
[Judge] Judge-1 (Variante A) starting (model=cyankiwi/Qwen3.5-27B-AWQ-4bit, context=yes) ...
[Judge] Judge-2 (Variante B) starting (…)
[Judge] Judge-3 (Variante C) starting (…)
[Judge] Judge-2 (Variante B) done in 2.8s → rating=mittel
[Judge] Judge-1 (Variante A) done in 3.1s → rating=gut
[Judge] Judge-3 (Variante C) done in 3.4s → rating=gut
[Judge] All 3 judges completed in 3.4s → ratings=['gut', 'mittel', 'gut']
```

Things to check in that output:

- **`context=hit`** — a `miss` on a turn that used documents means `chat_id` did
  not travel (pipeline → `/api/pipe`) or the backend was restarted since.
- **Three distinct variants** — A, B and C must each appear exactly once.
- **Ratings that differ from each other** — three identical verdicts every
  single round is the signal that the variants have collapsed into one voice
  and need re-tuning.

Direct call without the UI (context will be a miss unless `chat_id` matches a
real recent chat):

```bash
curl -s localhost:8088/api/judge \
  -H 'Content-Type: application/json' \
  -d '{"query":"Lohnt sich Riester?","answer":"Riester lohnt sich immer, schließen Sie sofort ab!"}' \
  | python3 -m json.tool
```

That answer should draw at least one `eher_schlecht` — it violates rules 2, 3
and 4 at once, which makes it a decent smoke test of the prompts.

---

## History

| When | Change |
|---|---|
| May 2026 | Panel hardcoded `:8088` on every request → on production the browser tried `https://mintify.verbraucherinformatik.de:8088/…`, which is not exposed and is blocked as mixed content. Fixed by the `/rag-api/*` Caddy route plus the hostname check that later became `ragApiBase()` |
| July 2026 | Judges rated grounded answers `schlecht` because they only saw `{query, answer}` and fell back on their own knowledge. Fixed by `context_cache.py` and the context-aware prompt |
| July 2026 | `enable_thinking: false` forced on judge calls after `--reasoning-parser qwen3` was enabled on the vLLM instance |
| Aug 2026 | Client-supplied prompts: one shared preamble + the five ethical rules + three role variants A/B/C, one per judge. Output moved from JSON to plain `Urteil: …` text, `json.loads()` replaced by `_parse_verdict()`, `schlecht` → `eher_schlecht`, `variant` added to the response |
| Sep 2026 | Opening the panel now judges the answer already on screen (`$: ` block in `Chat.svelte` + `resetJudge()`); previously the cards stayed empty until the next question |
| Sep 2026 | Judges moved from one turn to the **whole conversation**: `messages` added to `POST /api/judge`, `_format_transcript()` + `_TRANSCRIPT_BLOCK` in `judge_client.py` |
| Sep 2026 | Opening a stored chat from the sidebar with the panel open judges that chat's last answer too. All triggers unified in `syncJudgement()`, called from the `$: ` block, the end of `loadChat()`, and `initNewChat()` |
| Sep 2026 | Judges failed a greeting on two counts, both prompt gaps rather than model faults: they read the (correct, grounded) current date as invented, and they penalised the answer for not being about Altersvorsorge. Fixed by `_DATE_BLOCK` and `_SCOPE_NOTE` |

---

## Known limitations

- **Long chats are judged on their tail.** The transcript is capped at 8000
  chars, so in a very long conversation the opening turns fall out of the
  judges' view. The truncation is announced in the prompt, but a violation in
  message 2 of a 40-message chat will eventually stop being visible.
- **`max_tokens = 512` for a 1–2 sentence verdict.** Harmless, but if a model
  rambles past the format the parser keeps only the first `Urteil:` match and
  the rest is discarded rather than shown.
- **The cache is per process and per restart.** See
  [The cache](#the-cache--context_cachepy).
- **Verdicts are not persisted.** Nothing is written to disk, so there is no
  research export of how the judges rated over time — unlike the Kompetenzniveau
  feature, which keeps an append-only `level_history` table. If the judges are
  meant to produce evaluation data for the project, this is the gap to close.
- **Three judges, one model.** All three variants hit the same vLLM instance and
  the same weights. The diversity is prompt-level only, not model-level.
