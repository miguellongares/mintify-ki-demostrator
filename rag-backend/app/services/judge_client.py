import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from openai import OpenAI

# Used when /api/pipe cached the sources this answer was built from (documents
# and/or a live web search). The context is the ground truth — the judge must
# NOT rely on its own training knowledge, which is older than the web results
# and does not contain the user's private documents. This is the fix for judges
# rating correct, grounded answers as "schlecht" because they couldn't verify
# facts they had never seen.
_JUDGE_PROMPT_WITH_CONTEXT = """\
Du bist ein unabhängiger Bewertungs-Assistent für ein Finanzbildungssystem.
Dir liegen die Nutzerfrage, die Antwort des Systems und der KONTEXT vor, auf dem
die Antwort beruht (Auszüge aus hinterlegten Dokumenten und/oder einer aktuellen
Internet-Recherche).

WICHTIG: Der KONTEXT ist deine EINZIGE Wahrheitsquelle. Er kann aktueller sein
als dein Trainingswissen und enthält Informationen, die du nicht kennst.
Bewerte NIEMALS als "schlecht", nur weil du eine Aussage selbst nicht kennst
oder nicht verifizieren kannst — prüfe ausschließlich gegen den Kontext.

[KONTEXT]
{context}
[/KONTEXT]

Nutzerfrage: {query}

Antwort: {answer}

Bewerte nach: (1) Belegtheit — wird die Antwort vom Kontext gestützt und
widerspricht ihm nicht? (2) Relevanz — beantwortet sie die Frage? (3) Klarheit.

Antworte NUR mit einem JSON-Objekt (kein Markdown, kein Text davor/danach):
{{"rating": "gut" | "mittel" | "schlecht", "reasoning": "<1-2 konkrete Sätze, nenne den ausschlaggebenden Punkt>"}}

Kriterien:
- gut: durch den Kontext gestützt, beantwortet die Frage, klar formuliert
- mittel: überwiegend gestützt, aber unvollständig oder in Teilen unbelegt
- schlecht: widerspricht dem Kontext, geht an der Frage vorbei oder ist irreführend\
"""

# Fallback when no context was cached (pure model answer — no documents, no web,
# or a cache miss). Judge general quality and stay lenient: an answer the judge
# simply cannot fact-check is not automatically "schlecht".
_JUDGE_PROMPT_NO_CONTEXT = """\
Du bist ein unabhängiger Bewertungs-Assistent für ein Finanzbildungssystem.
Für diese Antwort liegen keine Quelldokumente vor. Bewerte die allgemeine
Qualität der Antwort auf die Nutzerfrage.

Nutzerfrage: {query}

Antwort: {answer}

Antworte NUR mit einem JSON-Objekt (kein Markdown, kein Text davor/danach):
{{"rating": "gut" | "mittel" | "schlecht", "reasoning": "<1-2 Sätze>"}}

Kriterien:
- gut: hilfreich, plausibel, klar und beantwortet die Frage
- mittel: teilweise hilfreich oder unvollständig
- schlecht: nur wenn die Antwort klar falsch, irreführend oder am Thema vorbei ist
Markiere NICHT als "schlecht", nur weil du einzelne Aussagen nicht selbst
verifizieren kannst.\
"""

# Judges run within the 16k-token model window alongside the answer; cap the
# injected context so 3 parallel calls stay fast and never overflow.
_MAX_CONTEXT_CHARS = 12000


class JudgeClient:
    def __init__(self, judge_url: str):
        self._url = judge_url
        self._model: str | None = None
        print(f"[Judge] Initialized with URL: {judge_url}", flush=True)

    def _resolve_model(self) -> str:
        if self._model:
            return self._model
        print(f"[Judge] Resolving model from {self._url}/v1/models ...", flush=True)
        try:
            resp = httpx.get(f"{self._url}/v1/models", timeout=5)
            resp.raise_for_status()
            models = resp.json().get("data", [])
            if models:
                self._model = models[0]["id"]
                print(f"[Judge] Using model: {self._model}", flush=True)
                return self._model # type: ignore
            print(f"[Judge] No models returned from {self._url}", flush=True)
        except Exception as e:
            print(f"[Judge] Could not fetch model from {self._url}: {e}", flush=True)
        return "default"

    def judge(self, judge_index: int, query: str, answer: str, context: str = "") -> dict:
        model = self._resolve_model()
        client = OpenAI(base_url=f"{self._url}/v1", api_key="vllm")
        t0 = time.time()
        if context:
            prompt = _JUDGE_PROMPT_WITH_CONTEXT.format(
                context=context[:_MAX_CONTEXT_CHARS], query=query, answer=answer
            )
        else:
            prompt = _JUDGE_PROMPT_NO_CONTEXT.format(query=query, answer=answer)
        print(
            f"[Judge] Judge-{judge_index} starting (model={model}, "
            f"context={'yes' if context else 'no'}) ...",
            flush=True,
        )
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=512,
                # Judges must return JSON, not reflect — force thinking off
                # regardless of the vLLM default (see llm_client.py).
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            raw = completion.choices[0].message.content or ""
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            result = json.loads(raw)
            if result.get("rating") not in ("gut", "mittel", "schlecht"):
                print(f"[Judge] Judge-{judge_index} returned unknown rating '{result.get('rating')}', defaulting to 'mittel'", flush=True)
                result["rating"] = "mittel"
            elapsed = time.time() - t0
            print(f"[Judge] Judge-{judge_index} done in {elapsed:.1f}s → rating={result['rating']}", flush=True)
            return result
        except Exception as e:
            elapsed = time.time() - t0
            print(f"[Judge] Judge-{judge_index} FAILED after {elapsed:.1f}s: {e}", flush=True)
            return {"rating": "mittel", "reasoning": f"Bewertung fehlgeschlagen: {e}"}

    def judge_all(self, query: str, answer: str, context: str = "") -> list[dict]:
        print(
            f"[Judge] Starting 3 parallel judge calls "
            f"(query={query[:60]!r}, context_chars={len(context)}) ...",
            flush=True,
        )
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(self.judge, i + 1, query, answer, context): i
                for i in range(3)
            }
            results = [None, None, None]
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    print(f"[Judge] Future {idx} raised: {e}", flush=True)
                    results[idx] = {"rating": "mittel", "reasoning": f"Fehler: {e}"}
        elapsed = time.time() - t0
        print(f"[Judge] All 3 judges completed in {elapsed:.1f}s → ratings={[r['rating'] for r in results]}", flush=True)
        return results
