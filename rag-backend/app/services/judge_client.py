import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from openai import OpenAI

_JUDGE_PROMPT = """\
Du bist ein unabhängiger Bewertungs-Assistent für ein Finanzbildungssystem.
Bewerte die folgende Antwort auf die Nutzerfrage.

Nutzerfrage: {query}

Antwort: {answer}

Antworte NUR mit einem JSON-Objekt (kein Markdown, kein Text davor/danach):
{{"rating": "gut" | "mittel" | "schlecht", "reasoning": "<1-2 Sätze>"}}

Kriterien:
- gut: Antwort ist korrekt, vollständig und klar
- mittel: Antwort ist teilweise korrekt oder unvollständig
- schlecht: Antwort ist falsch, irreführend oder nicht hilfreich\
"""


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
                return self._model
            print(f"[Judge] No models returned from {self._url}", flush=True)
        except Exception as e:
            print(f"[Judge] Could not fetch model from {self._url}: {e}", flush=True)
        return "default"

    def judge(self, judge_index: int, query: str, answer: str) -> dict:
        model = self._resolve_model()
        client = OpenAI(base_url=f"{self._url}/v1", api_key="vllm")
        t0 = time.time()
        print(f"[Judge] Judge-{judge_index} starting (model={model}) ...", flush=True)
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": _JUDGE_PROMPT.format(query=query, answer=answer),
                    }
                ],
                temperature=0.3,
                max_tokens=256,
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

    def judge_all(self, query: str, answer: str) -> list[dict]:
        print(f"[Judge] Starting 3 parallel judge calls (query={query[:60]!r}) ...", flush=True)
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(self.judge, i + 1, query, answer): i for i in range(3)}
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
