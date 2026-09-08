"""
WebSearcher — backend-owned web search + content extraction.

When the user enables 🌐, mintify_pipeline forwards web_search=True and the
query (rewritten first into a standalone search query, see
QueryRewriter.rewrite_web). We:
  1. query SearXNG's JSON API → top-N {url, title, snippet} candidates
     (more candidates than needed — a blocked or junk page must not cost a
     slot in the prompt)
  2. fetch each result page with a full browser header set and pull the MAIN
     article text out with trafilatura (drops nav menus, cookie banners, ads,
     footers — the boilerplate that made OWUI's raw WebBaseLoader dump unusable)
  3. fall back to the SearXNG snippet when a page yields too little (JS-only page,
     anti-bot block, timeout) — snippets often already state the answer.
  4. rank the usable pages by embedding similarity to the query and keep only
     the relevant top-N (filters pages that matched the search engine but not
     the question).

No headless browser needed: trafilatura works on the static HTML. Pages are
fetched concurrently in a small thread pool, so total latency ≈ the slowest
single page rather than the sum.
"""

import concurrent.futures
import math
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import trafilatura

_WS_SPACES = re.compile(r"[ \t]+")
_WS_BLANKLINES = re.compile(r"\n{3,}")

# Pages that never yield useful static text (video/social, JS-only) — their
# SearXNG snippet may still be used, but we don't waste a fetch on them.
_SKIP_FETCH_DOMAINS = (
    "youtube.com",
    "youtu.be",
    "facebook.com",
    "x.com",
    "twitter.com",
    "instagram.com",
    "tiktok.com",
    "pinterest.com",
)

# How many chars of a page take part in the relevance scoring — enough to
# represent the page without embedding the full 4000-char cap.
_RELEVANCE_CHARS = 1000


def _normalize(text: str) -> str:
    """Tidy extracted text: trim, collapse spaces, cap consecutive blank lines.

    Paragraph breaks (single newlines) are preserved — they help the LLM.
    """
    text = (text or "").strip()
    text = _WS_SPACES.sub(" ", text)
    text = _WS_BLANKLINES.sub("\n\n", text)
    return text


def _skip_fetch(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in _SKIP_FETCH_DOMAINS)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


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
        candidates: int,
        fetch_timeout: float,
        per_doc_chars: int,
        total_chars: int,
        min_doc_chars: int,
        min_relevance: float = 0.0,
        embedder=None,
    ):
        self._searxng_url = searxng_url.rstrip("/")
        self._n = num_results
        self._candidates = max(candidates, num_results)
        self._timeout = fetch_timeout
        self._per_doc = per_doc_chars
        self._total = total_chars
        self._min_doc = min_doc_chars
        self._min_relevance = min_relevance
        self._embedder = embedder
        # Real browsers send far more than a User-Agent; a bare UA string is
        # what got us 403s from kicker.de / sportytrader.de. Accept-Encoding is
        # left to httpx (it only advertises codecs it can actually decode).
        self._headers = {
            "User-Agent": user_agent,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
            "Referer": "https://www.google.com/",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Ch-Ua": '"Chromium";v="125", "Google Chrome";v="125", "Not.A/Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-User": "?1",
        }

    def search(self, query: str, time_range: str | None = None) -> list[WebResult]:
        """Return clean, relevance-ranked, budgeted web results for the query.

        time_range ("day"/"week"/"month"/"year") is passed to SearXNG for
        time-sensitive queries.
        """
        hits = self._searxng(query, time_range)
        if not hits:
            return []

        # Fetch + extract candidate pages concurrently.
        slots: list[WebResult | None] = [None] * len(hits)
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(hits))) as ex:
            futs = {ex.submit(self._fetch_extract, h): i for i, h in enumerate(hits)}
            for fut in concurrent.futures.as_completed(futs):
                i = futs[fut]
                try:
                    slots[i] = fut.result()
                except Exception as e:
                    print(f"[RAG][web] fetch failed for {hits[i]['url']}: {e}", flush=True)

        usable = [r for r in slots if r is not None and r.text]
        ranked = self._rank(query, usable)

        # Keep the top-N within the overall character budget.
        out: list[WebResult] = []
        total = 0
        for r in ranked:
            if len(out) >= self._n or total >= self._total:
                break
            out.append(r)
            total += len(r.text)
        return out

    def _rank(self, query: str, results: list[WebResult]) -> list[WebResult]:
        """Order results by embedding similarity to the query and drop pages
        below the relevance floor. Without an embedder (or floor 0) the
        SearXNG order is kept."""
        if not results or self._embedder is None or self._min_relevance <= 0:
            return results

        qv = self._embedder.encode_query(query)
        scored = sorted(
            ((_cosine(qv, self._embedder.encode_passage(r.text[:_RELEVANCE_CHARS])), r) for r in results),
            key=lambda t: t[0],
            reverse=True,
        )
        kept = [(s, r) for s, r in scored if s >= self._min_relevance]
        for s, r in scored:
            if s < self._min_relevance:
                print(
                    f"[RAG][web] dropped (relevance {s:.3f} < {self._min_relevance}): {r.url}",
                    flush=True,
                )
        if not kept:
            # Never return nothing when pages WERE fetched — the model can
            # still say the sources are poor. Keep the single best page.
            kept = scored[:1]
            print(
                f"[RAG][web] no page passed the relevance floor — keeping best "
                f"({kept[0][0]:.3f}: {kept[0][1].url})",
                flush=True,
            )
        return [r for _, r in kept]

    def _searxng(self, query: str, time_range: str | None = None) -> list[dict]:
        params = {"q": query, "format": "json", "language": "de"}
        if time_range:
            params["time_range"] = time_range
        try:
            resp = httpx.get(
                f"{self._searxng_url}/search",
                params=params,
                headers={"User-Agent": self._headers["User-Agent"]},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[RAG][web] SearXNG query failed: {e}", flush=True)
            return []

        # A search where every engine is blocked comes back as HTTP 200 with an
        # empty `results` list, so without this line the whole feature fails
        # silently — the only symptom was "0 usable sources" upstream, with no
        # hint that SearXNG itself found nothing. `unresponsive_engines` names
        # the engines that refused us (CAPTCHA, rate limit, …).
        if not (data.get("results") or []):
            print(
                f"[RAG][web] SearXNG returned 0 results for {query!r} — "
                f"unresponsive engines: {data.get('unresponsive_engines') or 'none'}",
                flush=True,
            )
            return []

        hits: list[dict] = []
        seen: set[str] = set()
        for item in data.get("results") or []:
            url = item.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            hits.append(
                {
                    "url": url,
                    "title": (item.get("title") or "").strip(),
                    "snippet": (item.get("content") or "").strip(),
                }
            )
            if len(hits) >= self._candidates:
                break
        return hits

    def _fetch_extract(self, hit: dict) -> WebResult:
        url = hit["url"]
        text = ""
        if not _skip_fetch(url):
            try:
                resp = httpx.get(
                    url,
                    headers=self._headers,
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
