"""
RAG endpoints

POST /api/pipe         — main RAG inference (retrieval + system-prompt injection + vLLM)
POST /api/passthrough  — direct vLLM streaming, no retrieval, no prompt manipulation
                         (used by the pipeline for OWUI's title/tag meta-tasks)
"""

import re

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import Settings
from app.dependencies import (
    get_embedder,
    get_knowledge_level,
    get_llm_client,
    get_query_rewriter,
    get_retriever,
    get_settings,
    get_sparse_embedder,
    get_web_searcher,
)
from app.services import context_cache, level_tool
from app.services.dates import today_german
from app.services.embedder import Embedder
from app.services.knowledge_level import KnowledgeLevelStore
from app.services.llm_client import LLMClient
from app.services.query_rewriter import QueryRewriter
from app.services.retriever import Retriever
from app.services.sparse_embedder import SparseEmbedder
from app.services.web_search import WebResult, WebSearcher

router = APIRouter()


DEFAULT_CONTEXT_BLOCK = "\n\n[KONTEXT]\n{context}\n[/KONTEXT]\n"

# Fresh web-search results (SearXNG + trafilatura, done in the rag-backend),
# injected when the user enabled 🌐. Appended AFTER {context} formatting so web
# text containing literal { } can never break str.format().
WEB_CONTEXT_BLOCK = (
    "\n\n[WEB-RECHERCHE]\n"
    "Die folgenden Auszüge stammen aus einer aktuellen Internet-Suche "
    "(Stand: {date}). Sie sind aktueller als dein Trainingswissen — stütze deine "
    "Antwort darauf und zitiere die genutzten Quellen als [Quelle N].\n\n"
    "{web}\n[/WEB-RECHERCHE]\n"
)


def _format_web_context(results: list[WebResult]) -> str:
    """Numbered source blocks the model can cite as [Quelle N]."""
    return "\n\n".join(
        f"[Quelle {i}] {r.title}\nURL: {r.url}\n{r.text}"
        for i, r in enumerate(results, 1)
    )


def _quellen_footer(results: list[WebResult]) -> str:
    """Rendered after the answer so the user always sees the sources used."""
    lines = "\n".join(f"{i}. {r.url}" for i, r in enumerate(results, 1))
    return f"\n\n---\n**Quellen ({len(results)} Webseiten durchsucht):**\n{lines}"


class PipeRequest(BaseModel):
    """Validated request body for POST /api/pipe. Sent by mintify_pipeline.pipe()."""
    user_message: str
    model_id: str
    messages: list[dict]
    temperature: float = 0.7
    # Per-model system prompt configured in OWUI. Wins over base_system_prompt when set.
    owui_system_prompt: str = ""
    # Pipeline fallback persona (the MINTIFY German assistant prompt).
    base_system_prompt: str = ""
    top_k: int = 5
    # OWUI file IDs the user has attached as Knowledge to this chat.
    # None = no filter (legacy behaviour). Empty list = explicit "no knowledge attached".
    file_ids: list[str] | None = None
    # OWUI user id of the person chatting (from body.metadata in the pipeline).
    # Lets retrieval include the caller's OWN personal files (and only theirs).
    user_id: str | None = None
    # OWUI chat id — used to cache the context this answer was built from so the
    # Bewertung judges (POST /api/judge, a separate browser call) can grade the
    # answer against the same sources instead of their own knowledge.
    chat_id: str | None = None
    # OWUI id of the assistant message this turn is producing. Keys the cached
    # retrieval hits so the browser can ask for THIS answer's chunks
    # (GET /api/retrieved-sources/{message_id}) and store them as its citations.
    message_id: str | None = None
    # True when the user enabled 🌐 for this turn → the backend runs SearXNG +
    # trafilatura over user_message and injects the results.
    web_search: bool = False
    # True when the user enabled 💡 "Nachdenken" → the model reflects first
    # (Qwen3.5 chat_template_kwargs.enable_thinking); the <think> block is streamed
    # back as a collapsible "Gedankengang" section.
    thinking: bool = False
    # Per-model "Kompetenzniveau" checkbox (OWUI Admin Panel → model →
    # Capabilities). True (default) = inject the profile/tool contract for this
    # model; False = admin explicitly excluded this model. Back-compat: a model
    # saved before this checkbox existed has no such key in its capabilities, and
    # the pipeline extractor defaults that case to True too.
    kompetenz_capability: bool = True


class PassthroughRequest(BaseModel):
    """No retrieval, no prompt injection — straight to vLLM."""
    model_id: str
    messages: list[dict]
    temperature: float = 0.7


@router.post("/pipe")
async def pipe(
    req: PipeRequest,
    embedder: Embedder = Depends(get_embedder),
    sparse_embedder: SparseEmbedder = Depends(get_sparse_embedder),
    rewriter: QueryRewriter = Depends(get_query_rewriter),
    retriever: Retriever = Depends(get_retriever),
    llm: LLMClient = Depends(get_llm_client),
    web_searcher: WebSearcher = Depends(get_web_searcher),
    levels: KnowledgeLevelStore = Depends(get_knowledge_level),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    print(f"\n{'='*60}")
    print(f"[RAG] pipe() called")
    print(f"[RAG] user_message: {repr(req.user_message[:200])}")
    print(f"[RAG] model_id: {req.model_id}")
    print(f"[RAG] file_ids: {req.file_ids} | user_id: {req.user_id} | web_search: {req.web_search} | thinking: {req.thinking}")

    # Web search (SearXNG + trafilatura) — only when the user enabled 🌐.
    # The raw message is first turned into a standalone search query: pronouns
    # and relative dates ("das Spiel", "gestern") resolved against the chat
    # history and today's date — searching the literal chat message returned
    # only generic landing pages.
    web_results: list[WebResult] = []
    if req.web_search:
        web_query, time_sensitive = rewriter.rewrite_web(
            req.user_message, req.messages, req.model_id
        )
        web_results = web_searcher.search(
            web_query, time_range="month" if time_sensitive else None
        )
        print(f"[RAG] web_search: {len(web_results)} usable sources")
        print(f"\n{'#'*70}\n[RAG] RAW WEB CONTENT RETRIEVED FROM THE INTERNET\n{'#'*70}")
        for i, r in enumerate(web_results, 1):
            print(f"\n----- [Quelle {i}] {len(r.text)} chars -----")
            print(f"TITLE: {r.title}")
            print(f"URL  : {r.url}")
            print(f"TEXT :\n{r.text}")
        print(f"\n{'#'*70}\n", flush=True)

    # Indexing is no longer triggered here — OWUI pushes new/updated files to
    # POST /api/index as they're uploaded (see routers/index.py).
    # Let the LLM turn the message into a document-vocabulary search query:
    # resolve follow-up references ("Ich meine den HSN/TSN") against the chat
    # history and expand everyday words into the terms documents actually use
    # ("kostet" → "Kaufpreis"). Only the retrieval query changes; the messages
    # sent to vLLM stay untouched. Skipped when retrieval can't happen anyway
    # (same condition the retriever checks).
    search_query = req.user_message
    if req.file_ids or req.user_id:
        search_query = rewriter.rewrite(req.user_message, req.messages, req.model_id)
    context, hits_info = retriever.retrieve(
        embedder.encode_query(search_query),
        sparse_embedder.encode_query(search_query),
        req.top_k,
        file_ids=req.file_ids,
        user_id=req.user_id,
    )

    print(f"[RAG] Retrieved {len(hits_info)} chunks:")
    for i, h in enumerate(hits_info):
        loc = f"page={h['page']}" if h.get("page") is not None else "(no page)"
        print(f"[{i+1}] {h['source']}  {loc}  score={h['score']}")

    # OWUI per-model prompt wins; pipeline persona is only a fallback.
    chosen = req.owui_system_prompt.strip() or req.base_system_prompt.strip()
    if context:
        if "{context}" not in chosen:
            chosen = chosen + DEFAULT_CONTEXT_BLOCK
        enriched_system = chosen.format(context=context)
    else:
        # No knowledge attached or zero hits — drop the {context} placeholder entirely.
        enriched_system = re.sub(
            r"\n*\[KONTEXT\]\s*\{context\}\s*\[/KONTEXT\]\n*", "\n", chosen
        ).replace("{context}", "").strip()

    # Append fresh web-search results, if any. Done after the {context} .format()
    # above; .format() here only fills the {web} placeholder, so braces inside the
    # web text are inserted literally and never re-scanned.
    today = today_german()
    if web_results:
        web_ctx = _format_web_context(web_results)
        print(f"[RAG] web_search: {len(web_ctx)} chars injected as [WEB-RECHERCHE]")
        enriched_system = enriched_system.rstrip() + WEB_CONTEXT_BLOCK.format(
            web=web_ctx, date=today
        )

    # Kompetenzniveau: tell the model what this learner already knows, and declare
    # the tool it can use to (re)assess. Appended AFTER the {context} .format()
    # above — like the web block — so braces in the stored Begründung are safe.
    # Gated on user_id: with no user there is nowhere to store a result, so
    # offering the tool would only invite a marker we'd have to discard.
    kompetenz_active = (
        settings.kompetenz_enabled
        and req.kompetenz_capability
        and bool(req.user_id)
    )
    if kompetenz_active:
        record = levels.get(req.user_id)
        print(
            f"[Kompetenz] user={req.user_id} aktuelles Niveau:"
            f" {record['level'] if record else 'noch nicht ermittelt'}"
        )
        enriched_system = (
            enriched_system.rstrip()
            + level_tool.profile_block(record)
            + level_tool.TOOL_CONTRACT
        )

    # Remember what this answer was built from, keyed by chat, so the Bewertung
    # judges can grade it against the SAME sources (they run on a separate
    # browser call that otherwise has no context). See services/context_cache.py.
    # The raw hits ride along keyed by message_id: the browser fetches them back
    # from /api/retrieved-sources once the answer is done and stores them as the
    # chat's citations, so what's persisted is exactly what the LLM was fed.
    context_cache.store(
        req.chat_id,
        context=context,
        web=(web_ctx if web_results else ""),
        question=req.user_message,
        hits=hits_info,
        user_id=req.user_id,
        message_id=req.message_id,
    )

    # Ground the model in the actual current date — without this it guesses
    # from its training data ("heute" in prompts meant nothing).
    enriched_system = f"Aktuelles Datum: {today}.\n\n{enriched_system}"

    enriched = [{"role": "system", "content": enriched_system}]
    enriched += [m for m in req.messages if m.get("role") != "system"]

    print(f"[RAG] Sending {len(enriched)} messages to vLLM (model: {req.model_id})")
    print(f"{'='*60}\n")

    def on_level(level: int, reason: str) -> None:
        """Fired by level_tool.filter_stream when the model emits its marker."""
        levels.set(
            req.user_id, level, reason, source="diagnose", chat_id=req.chat_id
        )
        print(
            f"[Kompetenz] user={req.user_id} → Niveau {level}"
            f" ({level_tool.label(level)}) | Begründung: {reason!r}",
            flush=True,
        )

    def token_generator():
        tokens = llm.stream(
            enriched, req.model_id, req.temperature, enable_thinking=req.thinking
        )
        # Strip the model's [[KOMPETENZNIVEAU:n]] marker out of the visible answer
        # and persist it. Must wrap the stream (not post-process it): tokens arrive
        # one at a time, so the marker would otherwise leak character by character.
        if kompetenz_active:
            tokens = level_tool.filter_stream(tokens, on_level)
        yield from tokens
        # Always show the user which pages were used (the model may or may not cite).
        if web_results:
            yield _quellen_footer(web_results)

    return StreamingResponse(token_generator(), media_type="text/plain; charset=utf-8")


@router.post("/passthrough")
async def passthrough(
    req: PassthroughRequest,
    llm: LLMClient = Depends(get_llm_client),
) -> StreamingResponse:
    """For OWUI meta-tasks (title, tags, query gen): stream vLLM without RAG."""
    print(f"[RAG] passthrough() | model={req.model_id} | {len(req.messages)} msgs")

    def token_generator():
        yield from llm.stream(req.messages, req.model_id, req.temperature)

    return StreamingResponse(token_generator(), media_type="text/plain; charset=utf-8")
