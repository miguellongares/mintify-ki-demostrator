from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    vllm_base_urls: str = "http://host.docker.internal:8002"
    qdrant_url: str = "http://qdrant:6333"
    collection_name: str = "mintify_rag"
    embedding_model: str = "intfloat/multilingual-e5-small"
    # e5 models are asymmetric: queries and passages must carry these prefixes
    # or similarity scores degrade badly. Set both to "" for symmetric models
    # (e.g. the old all-MiniLM-L6-v2).
    embedding_query_prefix: str = "query: "
    embedding_passage_prefix: str = "passage: "
    # Floor for the DENSE arm of the hybrid search (applied as score_threshold
    # on the dense prefetch). e5 scores cluster high (unrelated ≈ 0.70–0.78,
    # related ≈ 0.83+), so 0.80 is the starting point. BM25 hits bypass it by
    # design — exact keyword matches should survive even with low cosine
    # similarity. 0 disables.
    min_score: float = 0.80
    # Language for the BM25 sparse encoder (stemmer + stopwords).
    bm25_language: str = "german"
    # Rewrite follow-up questions into standalone search queries via the chat
    # LLM before retrieval. Kill-switch: set false to embed the raw message.
    query_rewrite: bool = True
    # Target chunk length. The chunker (services/chunker.py) packs whole
    # structural blocks — headings, tables, lists, sentences — up to this size
    # instead of cutting every N characters.
    chunk_size: int = 800
    # Trailing context repeated in the next chunk, carried over as whole blocks
    # (never a table), not as a blind character window.
    chunk_overlap: int = 100
    # Hard cap for a single chunk. A block bigger than chunk_size but below this
    # stays intact (a 1.200-char table is worth more whole than halved); larger
    # ones are split at row/item/sentence boundaries.
    chunk_max_size: int = 1600
    top_k: int = 5
    judge_vllm_url: str = "http://host.docker.internal:8002"
    tts_url: str = ""
    # OWUI is the single source of extracted document text. The indexer pulls a
    # file's already-extracted content from OWUI's REST API (by file id) and
    # embeds it into Qdrant — for ANY file type, not just PDFs.
    owui_base_url: str = "http://open-webui:8080"
    # Bearer token for the OWUI API. Create an admin API key in OWUI
    # (Settings → Account → API Keys) and set it here so the indexer can read
    # any user's file content. Empty → indexing is effectively disabled.
    owui_api_key: str = ""
    # Per-user personal files (sidebar drag & drop). SQLite DB + raw files live
    # under this directory — bind-mounted to ./data/personal_docs on the host.
    personal_docs_dir: str = "/data/personal_docs"
    # Per-user Kompetenzniveau (financial-literacy level 0–3). SQLite DB under
    # this directory — bind-mounted to ./data/knowledge_level on the host.
    knowledge_level_dir: str = "/data/knowledge_level"
    # Inject the [LERNENDENPROFIL] block + the [WERKZEUG] tool contract into every
    # system prompt, and intercept the model's [[KOMPETENZNIVEAU:n]] marker.
    # Kill-switch: set false to disable the whole feature without a code change.
    kompetenz_enabled: bool = True
    # docling-serve instance used to extract personal-file text — the SAME
    # server OWUI uses for knowledge-base/chat uploads (RunPod, SSH tunnel on
    # host port 5001; OWUI reaches it via DOCLING_SERVER_URL too).
    docling_server_url: str = "http://host.docker.internal:5001"
    # Explicit OCR config for docling-serve, instead of relying on its own
    # undocumented defaults. Verified live against the running instance
    # (2026-08-03): do_ocr already defaults to true there, but force_ocr+
    # ocr_lang measurably improved German OCR accuracy on a real scanned
    # document (e.g. "Loniares"→"Longares", "10S5S"→"10555"). ocr_preset
    # MUST stay "auto" — this deployment 404s on any other preset value
    # (e.g. "easyocr", "tesseract" are not enabled server-side).
    docling_do_ocr: bool = True
    docling_force_ocr: bool = True
    docling_ocr_lang: str = "de"
    docling_ocr_preset: str = "auto"

    # --- Web search (backend-owned: SearXNG search + trafilatura extraction) ---
    # The pipeline forwards web_search=True + the query when the user enables 🌐.
    # We query SearXNG, fetch the top-N pages, and extract clean article text.
    searxng_url: str = "http://searxng:8080"
    web_search_results: int = 4          # sources injected into the prompt
    web_search_candidates: int = 10      # SearXNG hits fetched (over-fetch: 403s and junk pages don't cost a slot)
    web_fetch_timeout: float = 8.0       # seconds per SearXNG query / page fetch
    web_doc_char_cap: int = 4000         # max chars kept per page
    web_total_char_cap: int = 16000      # max chars across all pages
    web_min_doc_chars: int = 120         # below this → fall back to SearXNG snippet
    # Cosine floor (e5 query↔passage) a fetched page must reach against the web
    # query to be injected — filters pages that matched the search engine but
    # not the question. 0 disables.
    web_min_relevance: float = 0.78
    web_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )

    @property
    def vllm_urls(self) -> list[str]:
        return [u.strip() for u in self.vllm_base_urls.split(",") if u.strip()]

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
