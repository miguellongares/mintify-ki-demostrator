"""
Indexer — PDF ingestion pipeline
---------------------------------
Converts PDF files in /app/uploads into searchable vector chunks stored in Qdrant.

Full indexing pipeline per file:
  1. Compute MD5 hash of the file → skip if already in _indexed_hashes (dedup)
  2. Open the PDF with PyMuPDF (fitz) and extract text page by page
  3. Split each page's text into overlapping chunks (chunk_size chars, chunk_overlap overlap)
  4. Embed each chunk with Embedder.encode() → 384-dim float vector
  5. Upsert all PointStructs into Qdrant in a single batch call

Deduplication strategy:
  - _indexed_hashes is an in-memory set of MD5 hex strings.
  - On startup, _prefetch_indexed_hashes() pre-populates it from Qdrant's stored payloads
    so that container restarts don't trigger redundant re-embedding of known files.
  - index_new_files() (called on every pipe() request) adds newly uploaded files
    without requiring a container restart.

Deterministic Qdrant point IDs:
  Each chunk's ID is computed as:
    id = int(md5_hex, 16) % (2**53) + page_num * 1000 + chunk_index
  This means re-indexing the SAME file produces the SAME IDs → upsert() overwrites
  existing points instead of creating duplicates. Do NOT change this formula without
  also wiping the Qdrant collection (otherwise old and new IDs would coexist).

Qdrant collection schema:
  - Vector size: 384 (must match all-MiniLM-L6-v2 output dimension)
  - Distance: COSINE (normalised dot product — standard for semantic similarity)
  - Payload fields per point: text, source (filename), page, file_hash
"""

import glob
import hashlib
import os

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from app.config import Settings
from app.services.embedder import Embedder


class Indexer:
    def __init__(self, embedder: Embedder, qdrant: QdrantClient, settings: Settings):
        self._embedder = embedder
        self._qdrant = qdrant
        self._settings = settings
        # In-memory cache of already-indexed file hashes. Avoids Qdrant reads on every request.
        self._indexed_hashes: set[str] = set()

    def startup(self):
        """Called once from init_singletons() after all services are initialised."""
        self._ensure_collection()        # create Qdrant collection if it doesn't exist
        self._prefetch_indexed_hashes()  # load known hashes from existing Qdrant points
        self._index_all_files()          # index any PDFs not yet in Qdrant

    def index_new_files(self):
        """
        Called on every pipe() request to pick up PDFs uploaded after startup.
        Hashing ensures only truly new files are processed — no extra Qdrant reads.
        """
        pattern = os.path.join(self._settings.upload_dir, "**", "*.pdf")
        for path in glob.glob(pattern, recursive=True):
            if self._file_hash(path) not in self._indexed_hashes:
                print(f"[RAG] New file detected: {os.path.basename(path)}")
                self._index_file(path)

    def _ensure_collection(self):
        """Create the Qdrant collection if it doesn't already exist."""
        existing = [c.name for c in self._qdrant.get_collections().collections]
        if self._settings.collection_name not in existing:
            self._qdrant.create_collection(
                collection_name=self._settings.collection_name,
                # size=384 must match the embedding model's output dimension.
                # distance=COSINE is standard for semantic similarity search.
                vectors_config=VectorParams(size=384, distance=Distance.COSINE),
            )
            print(f"[RAG] Created Qdrant collection '{self._settings.collection_name}'")

    def _prefetch_indexed_hashes(self):
        """
        Read file_hash payloads from existing Qdrant points to rebuild the in-memory
        set without re-reading files from disk. This prevents re-indexing on container restart.
        limit=10000 is a safe upper bound; scroll() returns all points up to that limit.
        """
        try:
            scroll_result = self._qdrant.scroll(
                collection_name=self._settings.collection_name,
                with_payload=["file_hash"],
                limit=10000,
            )
            for point in scroll_result[0]:
                h = point.payload.get("file_hash")
                if h:
                    self._indexed_hashes.add(h)
            print(f"[RAG] Pre-loaded {len(self._indexed_hashes)} known file hashes from Qdrant")
        except Exception as e:
            print(f"[RAG] Could not pre-fetch hashes: {e}")

    def _index_all_files(self):
        """Index every PDF found in the upload directory (skipping already-indexed ones)."""
        pattern = os.path.join(self._settings.upload_dir, "**", "*.pdf")
        files = glob.glob(pattern, recursive=True)
        print(f"[RAG] Found {len(files)} PDF(s) in {self._settings.upload_dir}")
        for path in files:
            self._index_file(path)

    def _index_file(self, path: str):
        """
        Full indexing pipeline for a single PDF file.
        fitz (PyMuPDF) is imported lazily here to avoid a top-level import error
        if the package isn't installed (though it always is in the Docker image).
        """
        import fitz  # pymupdf

        h = self._file_hash(path)
        if h in self._indexed_hashes:
            # Already indexed (checked again in case of race between startup scan and new upload).
            return

        filename = os.path.basename(path)
        print(f"[RAG] Indexing: {filename}")

        doc = fitz.open(path)
        points = []

        # Deterministic base ID derived from the file hash.
        # int(hex_string, 16) converts the MD5 hex digest to an integer.
        # % (2**53) keeps it within JavaScript's safe integer range (Qdrant IDs are 64-bit
        # but some clients use JS numbers, so this avoids precision loss).
        id_base = int(h, 16) % (2**53)

        for page_num, page in enumerate(doc):
            text = page.get_text()
            if not text.strip():
                # Skip blank pages (scanned images, cover pages, etc.)
                continue
            for i, chunk in enumerate(self._chunk_text(text)):
                vec = self._embedder.encode(chunk)
                points.append(
                    PointStruct(
                        # page_num * 1000 + i ensures unique IDs within the same file.
                        # Assumes no page has more than 1000 chunks (chunk_size=800 chars
                        # per typical page of ~2000 chars → max ~3 chunks per page).
                        id=id_base + page_num * 1000 + i,
                        vector=vec,
                        payload={
                            "text": chunk,          # stored for context injection
                            "source": filename,     # shown to user as citation
                            "page": page_num + 1,   # 1-indexed for human readability
                            "file_hash": h,         # enables hash-based dedup on restart
                        },
                    )
                )

        if points:
            # upsert() inserts new points or overwrites existing ones with the same ID.
            # Because IDs are deterministic, re-indexing the same file is idempotent.
            self._qdrant.upsert(
                collection_name=self._settings.collection_name,
                points=points,
            )

        doc.close()
        self._indexed_hashes.add(h)
        print(f"[RAG] Indexed {len(points)} chunks from '{filename}'")

    def _chunk_text(self, text: str) -> list[str]:
        """
        Split text into overlapping fixed-size chunks.
        Overlap prevents context loss at chunk boundaries — a sentence that spans
        two chunks appears (partially) in both, so at least one chunk will be retrieved
        if the user asks about that sentence.
        """
        size = self._settings.chunk_size
        overlap = self._settings.chunk_overlap
        chunks, start = [], 0
        while start < len(text):
            end = min(start + size, len(text))
            chunks.append(text[start:end])
            if end == len(text):
                break
            # Advance by (size - overlap) so the next chunk starts overlap chars before
            # the end of the current chunk.
            start += size - overlap
        return chunks

    def _file_hash(self, path: str) -> str:
        """MD5 hash of the entire file content — used as a dedup key."""
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
