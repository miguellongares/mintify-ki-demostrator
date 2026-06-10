"""
WebSearcher — backend-owned web search + content extraction.

When the user enables 🌐, mintify_pipeline forwards web_search=True and the
query. We:
  1. query SearXNG's JSON API → top-N {url, title, snippet}
  2. fetch each result page and pull the MAIN article text out with trafilatura
     (drops nav menus, cookie banners, ads, footers — the boilerplate that made
     OWUI's raw WebBaseLoader dump unusable)
  3. fall back to the SearXNG snippet when a page yields too little (JS-only page,
     anti-bot block, timeout) — snippets often already state the answer.

No headless browser needed: trafilatura works on the static HTML. Pages are
fetched concurrently in a small thread pool, so total latency ≈ the slowest
single page rather than the sum.
"""

import concurrent.futures
import re
from dataclasses import dataclass

import httpx
import trafilatura

_WS_SPACES = re.compile(r"[ \t]+")
_WS_BLANKLINES = re.compile(r"\n{3,}")


def _normalize(text: str) -> str:
    """Tidy extracted text: trim, collapse spaces, cap consecutive blank lines.

    Paragraph breaks (single newlines) are preserved — they help the LLM.
    """
    text = (text or "").strip()
    text = _WS_SPACES.sub(" ", text)
    text = _WS_BLANKLINES.sub("\n\n", text)
    return text


@dataclass
class WebResult:
    url: str
    title: str
    text: str


class WebSearcher:
    def __init__(
        self,
        searxng_url: str,
        user_agent: str,
        num_results: int,
        fetch_timeout: float,
        per_doc_chars: int,
        total_chars: int,
        min_doc_chars: int,
    ):
        self._searxng_url = searxng_url.rstrip("/")
        self._ua = user_agent
        self._n = num_results
        self._timeout = fetch_timeout
        self._per_doc = per_doc_chars
        self._total = total_chars
        self._min_doc = min_doc_chars

    def search(self, query: str) -> list[WebResult]:
        """Return clean, budgeted web results for the query (ranked as SearXNG ranked)."""
        hits = self._searxng(query)
        if not hits:
            return []

        # Fetch + extract pages concurrently; keep SearXNG's ranking order.
        slots: list[WebResult | None] = [None] * len(hits)
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(hits))) as ex:
            futs = {ex.submit(self._fetch_extract, h): i for i, h in enumerate(hits)}
            for fut in concurrent.futures.as_completed(futs):
                i = futs[fut]
                try:
                    slots[i] = fut.result()
                except Exception as e:
                    print(f"[RAG][web] fetch failed for {hits[i]['url']}: {e}", flush=True)

        # Drop empties and apply the overall character budget.
        out: list[WebResult] = []
        total = 0
        for r in slots:
            if r is None or not r.text:
                continue
            out.append(r)
            total += len(r.text)
            if total >= self._total:
                break
        return out

    def _searxng(self, query: str) -> list[dict]:
        try:
            resp = httpx.get(
                f"{self._searxng_url}/search",
                params={"q": query, "format": "json", "language": "de"},
                headers={"User-Agent": self._ua},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[RAG][web] SearXNG query failed: {e}", flush=True)
            return []

        hits: list[dict] = []
        for item in data.get("results") or []:
            url = item.get("url")
            if not url:
                continue
            hits.append(
                {
                    "url": url,
                    "title": (item.get("title") or "").strip(),
                    "snippet": (item.get("content") or "").strip(),
                }
            )
            if len(hits) >= self._n:
                break
        return hits

    def _fetch_extract(self, hit: dict) -> WebResult:
        url = hit["url"]
        text = ""
        try:
            resp = httpx.get(
                url,
                headers={"User-Agent": self._ua},
                timeout=self._timeout,
                follow_redirects=True,
            )
            resp.raise_for_status()
            extracted = trafilatura.extract(
                resp.text,
                url=url,
                include_comments=False,
                include_tables=True,   # fixtures / schedules are often tables
                favor_recall=True,     # prefer a bit more content over strictness
            )
            text = _normalize(extracted or "")
        except Exception as e:
            print(f"[RAG][web] fetch/extract error {url}: {e}", flush=True)

        # Too little usable text → fall back to the SearXNG snippet.
        if len(text) < self._min_doc:
            snippet = _normalize(hit.get("snippet") or "")
            if len(snippet) > len(text):
                text = snippet

        return WebResult(url=url, title=hit.get("title") or url, text=text[: self._per_doc])
