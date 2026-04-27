"""
POST /api/pipe — main RAG inference endpoint
---------------------------------------------
This is the core of the MINTIFY system. It receives a chat request from the
pipeline proxy (mintify_pipeline.py), runs the full Retrieval-Augmented Generation
pipeline, and streams the LLM's response back as plain text.

Full RAG pipeline executed on every request:
  1. index_new_files()    — check if any new PDFs were uploaded since last call
  2. embedder.encode()    — embed the user's question into a 384-dim vector
  3. retriever.retrieve() — cosine-similarity search in Qdrant → top-K PDF chunks
  4. system_prompt.format(context=...) — inject retrieved chunks into the system prompt
  5. llm.stream()         — send enriched messages to vLLM, yield tokens as plain text

Request body (PipeRequest):
  user_message   — the user's latest question (used for embedding)
  model_id       — HuggingFace model ID (already de-sanitized by the pipeline layer)
  messages       — full conversation history in OpenAI format
  temperature    — LLM sampling temperature (0.0 = deterministic, 1.0 = creative)
  system_prompt  — the German prompt template; must contain {context} placeholder
  top_k          — how many PDF chunks to retrieve (default 5, from valve settings)

Response:
  StreamingResponse with media_type "text/plain; charset=utf-8"
  Each chunk is a raw text token from vLLM, streamed back to the pipeline proxy,
  which in turn yields it to Open Web UI, which renders it in the chat bubble.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.dependencies import get_embedder, get_indexer, get_llm_client, get_retriever
from app.services.embedder import Embedder
from app.services.indexer import Indexer
from app.services.llm_client import LLMClient
from app.services.retriever import Retriever

router = APIRouter()


class PipeRequest(BaseModel):
    """Validated request body for POST /api/pipe. Sent by mintify_pipeline.pipe()."""
    user_message: str       # raw question text — embedded and used for Qdrant search
    model_id: str           # vLLM model ID (e.g. "Qwen/Qwen3-1.7B")
    messages: list[dict]    # OpenAI-format conversation history [{"role": ..., "content": ...}]
    temperature: float = 0.7
    system_prompt: str      # must contain {context} — filled with retrieved PDF chunks
    top_k: int = 5          # number of Qdrant results to inject into context


@router.post("/pipe")
async def pipe(
    req: PipeRequest,
    # FastAPI injects these singletons via the dependency getters in dependencies.py
    embedder: Embedder = Depends(get_embedder),
    retriever: Retriever = Depends(get_retriever),
    indexer: Indexer = Depends(get_indexer),
    llm: LLMClient = Depends(get_llm_client),
) -> StreamingResponse:
    print(f"\n{'='*60}")
    print(f"[RAG] pipe() called")
    print(f"[RAG] user_message: {repr(req.user_message[:200])}")
    print(f"[RAG] model_id: {req.model_id}")

    # Step 1: Check for newly uploaded PDFs and index them on-the-fly.
    # Hashing is used to skip files already indexed — no duplicate vectors.
    indexer.index_new_files()

    # Step 2: Embed the user's query into a 384-dim vector using the same
    # all-MiniLM-L6-v2 model used at indexing time. The vector spaces must match.
    print("[RAG] Embedding query...")
    query_vec = embedder.encode(req.user_message)

    # Step 3: Search Qdrant for the top-K chunks most similar to the query vector.
    # Returns a formatted context string and a list of metadata dicts for logging.
    context, hits_info = retriever.retrieve(query_vec, req.top_k)

    print(f"[RAG] Retrieved {len(hits_info)} chunks:")
    for i, h in enumerate(hits_info):
        print(f"  [{i+1}] source={h['source']} page={h['page']} score={h['score']}")
        print(f"       text_preview: {repr(h['text'][:120])}")

    print(f"[RAG] Context length: {len(context)} chars")

    # Step 4: Build the enriched message list for vLLM.
    # Replace {context} in the system prompt with the retrieved PDF text,
    # then prepend it to the conversation history (stripping any prior system message
    # the client may have included, since we always use our own).
    enriched_system = req.system_prompt.format(context=context)
    enriched = [{"role": "system", "content": enriched_system}]
    enriched += [m for m in req.messages if m.get("role") != "system"]

    print(f"[RAG] Sending {len(enriched)} messages to vLLM (model: {req.model_id})")
    print(f"{'='*60}\n")

    # Step 5: Stream vLLM's response back.
    # token_generator is a synchronous generator — StreamingResponse runs it in a
    # thread pool so it doesn't block the async event loop.
    # media_type "text/plain" matches what mintify_pipeline.pipe() expects:
    # it iterates r.iter_text() and yields each chunk directly to OWUI.
    def token_generator():
        yield from llm.stream(enriched, req.model_id, req.temperature)

    return StreamingResponse(token_generator(), media_type="text/plain; charset=utf-8")
