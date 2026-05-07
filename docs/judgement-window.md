# Judgement Window

The Judgement Window is a right-sidebar feature that automatically evaluates the quality of each AI response. After every completed chat message, three independent LLM judges rate the answer as **gut** (good), **mittel** (medium), or **schlecht** (bad) and explain their reasoning. Results appear as color-coded cards in the sidebar.

---

## Architecture Overview

```
User clicks toggle → showJudgement = true → sidebar slides in

Chat streams response → done = true
  → Chat.svelte extracts (userMsg, aiAnswer)
  → calls JudgementPanel.requestJudge()
    → POST http://localhost:8080/api/judge
      → judge_client.judge_all()
        → 3× parallel vLLM calls at :8004
        ← 3× { rating, reasoning }
      ← { judges: [...] }
    ← judgeResults set, loading cleared
  → 3 colored cards rendered in sidebar
```

The browser calls the RAG backend directly on port `8080` — the request does **not** go through the OWUI pipeline.

---

## Frontend

### 1. Toggle button — `Navbar.svelte`

A ChartBar icon in the top navigation bar toggles the panel. It turns blue when the mode is active.

```svelte
<!-- open-webui-src/src/lib/components/chat/Navbar.svelte:140 -->
<Tooltip content="Judgement-Modus">
    <button
        class="flex cursor-pointer px-2 py-2 rounded-xl hover:bg-gray-50 dark:hover:bg-gray-850
               transition {$showJudgement ? 'bg-gray-100 dark:bg-gray-800' : ''}"
        on:click={async () => {
            await showJudgement.set(!$showJudgement);
        }}
        aria-label="Judgement Mode"
    >
        <div class="m-auto self-center
                    {$showJudgement ? 'text-blue-600 dark:text-blue-400' : 'text-gray-600 dark:text-gray-400'}">
            <ChartBar className="size-5" />
        </div>
    </button>
</Tooltip>
```

### 2. Trigger — `Chat.svelte`

When a chat response finishes streaming (`done = true`), `Chat.svelte` extracts the last user message and the completed AI answer, then calls the panel's exported function.

```svelte
<!-- open-webui-src/src/lib/components/chat/Chat.svelte:1241 -->
if ($showJudgement && judgementPaneComponent) {
    const msgList = createMessagesList(history, message.id);
    const userMsg = [...msgList].reverse().find((m) => m.role === 'user')?.content ?? '';
    judgementPaneComponent.requestJudge(userMsg, message.content);
}
```

The panel component is mounted at the bottom of `Chat.svelte` with a ref binding:

```svelte
<!-- Chat.svelte:2164 -->
<JudgementPanel bind:this={judgementPaneComponent} />
```

### 3. Panel component — `JudgementPanel.svelte`

The panel owns all judge state locally. The exported `requestJudge` function fetches the backend and updates local reactive variables — this keeps Svelte reactivity reliable across async boundaries.

```typescript
// open-webui-src/src/lib/components/chat/JudgementPanel.svelte:9
export const requestJudge = async (query: string, answer: string) => {
    judgeLoading = true;
    judgeResults = null;
    try {
        const resp = await fetch('http://localhost:8080/api/judge', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query, answer })
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        judgeResults = data.judges;
    } catch (e) {
        console.error('[JudgementPanel] judge fetch failed:', e);
        judgeResults = [
            { rating: 'mittel', reasoning: 'Bewertung nicht verfügbar.' },
            { rating: 'mittel', reasoning: 'Bewertung nicht verfügbar.' },
            { rating: 'mittel', reasoning: 'Bewertung nicht verfügbar.' }
        ];
    } finally {
        judgeLoading = false;
    }
};
```

**Rating color mapping:**

```typescript
// JudgementPanel.svelte:33
function ratingColor(rating: string) {
    if (rating === 'gut')      return 'border-green-500 bg-green-50 dark:bg-green-900/20';
    if (rating === 'schlecht') return 'border-red-500   bg-red-50   dark:bg-red-900/20';
    return                            'border-yellow-400 bg-yellow-50 dark:bg-yellow-900/20';
}
```

The sidebar is a fixed 350px-wide `<aside>` that slides in/out via a CSS width transition driven by the `$showJudgement` store. While loading, three animated skeleton boxes are shown. Once results arrive, each box becomes a bordered card with a dot, a rating label ("Richter 1 — Gut"), and the reasoning text.

---

## Backend

### 1. Endpoint — `routers/judge.py`

```python
# rag-backend/app/routers/judge.py
class JudgeRequest(BaseModel):
    query: str
    answer: str

class JudgeResult(BaseModel):
    rating: str      # "gut" | "mittel" | "schlecht"
    reasoning: str

class JudgeResponse(BaseModel):
    judges: list[JudgeResult]

@router.post("/judge", response_model=JudgeResponse)
async def judge_answer(
    req: JudgeRequest,
    judge: JudgeClient = Depends(get_judge_client),
):
    t0 = time.time()
    results = judge.judge_all(req.query, req.answer)
    elapsed = time.time() - t0
    print(f"[Judge] POST /api/judge completed in {elapsed:.1f}s", flush=True)
    return JudgeResponse(judges=[JudgeResult(**r) for r in results])
```

### 2. Judge client — `services/judge_client.py`

**The evaluation prompt** (German, instructs the LLM to return structured JSON):

```python
# judge_client.py:8
_JUDGE_PROMPT = """\
Du bist ein unabhängiger Bewertungs-Assistent für ein Finanzbildungssystem.
Bewerte die folgende Antwort auf die Nutzerfrage.

Nutzerfrage: {query}

Antwort: {answer}

Antworte NUR mit einem JSON-Objekt (kein Markdown, kein Text davor/danach):
{"rating": "gut" | "mittel" | "schlecht", "reasoning": "<1-2 Sätze>"}

Kriterien:
- gut: Antwort ist korrekt, vollständig und klar
- mittel: Antwort ist teilweise korrekt oder unvollständig
- schlecht: Antwort ist falsch, irreführend oder nicht hilfreich
"""
```

**Single judge call** (one LLM completion, parses JSON, falls back to `"mittel"` on errors):

```python
# judge_client.py:49
def judge(self, judge_index: int, query: str, answer: str) -> dict:
    model = self._resolve_model()
    client = OpenAI(base_url=f"{self._url}/v1", api_key="vllm")
    completion = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": _JUDGE_PROMPT.format(query=query, answer=answer)}],
        temperature=0.3,
        max_tokens=256,
    )
    raw = completion.choices[0].message.content.strip()
    result = json.loads(raw)
    if result.get("rating") not in ("gut", "mittel", "schlecht"):
        result["rating"] = "mittel"
    return result
```

**Three parallel judges** (preserves result order via index slots):

```python
# judge_client.py:85
def judge_all(self, query: str, answer: str) -> list[dict]:
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(self.judge, i + 1, query, answer): i for i in range(3)}
        results = [None, None, None]
        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                results[idx] = {"rating": "mittel", "reasoning": f"Fehler: {e}"}
    return results
```

### 3. Configuration

| Setting | Default | File |
|---------|---------|------|
| `judge_vllm_url` | `http://host.docker.internal:8004` | `config.py` |

The `JudgeClient` singleton is created once at startup in `dependencies.py` and injected via FastAPI's `Depends()` on every request — no re-creation per call.

---

## Key Files

| File | Role |
|------|------|
| `open-webui-src/src/lib/components/chat/Navbar.svelte` | Toggle button (ChartBar icon) |
| `open-webui-src/src/lib/components/chat/Chat.svelte` | Trigger on `done=true` |
| `open-webui-src/src/lib/components/chat/JudgementPanel.svelte` | Sidebar UI, fetch logic, state |
| `open-webui-src/src/lib/stores/index.ts` | `showJudgement` writable store |
| `rag-backend/app/routers/judge.py` | `POST /api/judge` endpoint |
| `rag-backend/app/services/judge_client.py` | Parallel judge logic, LLM calls |
| `rag-backend/app/dependencies.py` | `JudgeClient` singleton + DI |
| `rag-backend/app/config.py` | `judge_vllm_url` setting |

---

## Rebuild After Changes

| Changed area | Command |
|---|---|
| Frontend (Svelte) | `docker compose build open-webui && docker compose up -d open-webui` |
| RAG backend | `docker compose build mintify-rag-service && docker compose up -d mintify-rag-service` |

Watch judge logs:

```bash
docker logs -f mintify-rag-service 2>&1 | grep Judge
```
