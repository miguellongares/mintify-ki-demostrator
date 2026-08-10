"""
Indexer — ingest OWUI-extracted text into Qdrant
-------------------------------------------------
OWUI extracts text from EVERY uploaded file type (pdf, docx, xlsx, …) and stores
it in its SQL `file` table. This indexer pulls that text from OWUI by file id
(via OWUIClient) and embeds it into Qdrant — making rag-backend + Qdrant the
single RAG system. It no longer reads files from disk or parses PDFs itself.

Per file:
  1. Fetch {filename, content, hash} from OWUI by file id
  2. Skip if (file_id → hash) is unchanged since last index (dedup)
  3. Delete any existing points for this file id (so re-index / shrink is clean)
  4. Split content into overlapping chunks (chunk_size / chunk_overlap)
  5. Embed each chunk and upsert into Qdrant

Deterministic Qdrant point IDs:
    id = int(file_id_hex, 16) % (2**53) + chunk_index
  where file_id_hex is the OWUI file UUID with hyphens removed. Re-indexing the
  same file produces the same base id, and stale points are deleted first, so
  the collection stays consistent. (Changed from the old PDF page*1000+i scheme
  — the Qdrant collection must be cleared once when migrating.)

Qdrant payload per point: text, source (filename), owui_file_id, file_hash,
chunk_index. The retriever filters on `owui_file_id`, so that field is required.

Personal files (sidebar feature) are indexed into the SAME collection via
index_personal_doc(): their points carry {personal_doc_id, user_id,
doc_type="personal"} INSTEAD of owui_file_id. The retriever additionally
matches `user_id == <caller>`, so each user only ever retrieves their own
personal documents; reconcile()/sync_all() ignore these points because they
have no owui_file_id.
"""

import hashlib

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    Modifier,
    PointStruct,
    SparseVectorParams,
    VectorParams,
)

from app.config import Settings
from app.services.embedder import Embedder
from app.services.owui_client import OWUIClient
from app.services.sparse_embedder import SparseEmbedder


class Indexer:
    def __init__(
        self,
        embedder: Embedder,
        sparse_embedder: SparseEmbedder,
        qdrant: QdrantClient,
        settings: Settings,
        owui: OWUIClient,
    ):
        self._embedder = embedder
        self._sparse = sparse_embedder
        self._qdrant = qdrant
        self._settings = settings
        self._owui = owui
        # In-memory map of owui_file_id → file_hash for already-indexed files.
        # Lets us skip re-embedding unchanged files without re-reading Qdrant.
        self._indexed: dict[str, str] = {}

    def startup(self):
        """Called once at boot. Does NOT touch OWUI (it may not be up yet) —
        the collection is created and known files are loaded from Qdrant; actual
        ingestion is driven by the OWUI webhook (POST /api/index) and /api/sync."""
        self._ensure_collection()
        self._prefetch_indexed()

    # ------------------------------------------------------------------ public

    def index_file_id(self, file_id: str) -> int:
        """Fetch one file's text from OWUI and (re)index it. Returns chunk count."""
        record = self._owui.fetch_file(file_id)
        if not record:
            print(f"[RAG] Skipping {file_id}: not retrievable from OWUI.", flush=True)
            return 0
        return self._index_record(record)

    def sync_all(self) -> dict:
        """Backfill: index every OWUI file that isn't already up to date."""
        files = self._owui.list_files()
        total_chunks = 0
        indexed_files = 0
        for record in files:
            chunks = self._index_record(record)
            if chunks:
                indexed_files += 1
                total_chunks += chunks
        print(
            f"[RAG] sync_all: {indexed_files}/{len(files)} files indexed, "
            f"{total_chunks} chunks total.",
            flush=True,
        )
        return {"files_seen": len(files), "files_indexed": indexed_files, "chunks": total_chunks}

    def delete_file_id(self, file_id: str) -> None:
        """Remove all Qdrant points for a file (called when OWUI deletes it)."""
        self._delete_points(file_id)
        self._indexed.pop(file_id, None)
        print(f"[RAG] Deleted Qdrant points for owui_file_id={file_id}", flush=True)

    def index_personal_doc(
        self, document_id: str, user_id: str, filename: str, text: str
    ) -> int:
        """(Re)index one personal file's extracted text, scoped to its owner.
        Same chunking/id scheme as OWUI files; document_id is a uuid4 hex."""
        if not text.strip():
            return 0

        # Drop stale points first so re-index stays consistent.
        self.delete_personal_doc(document_id)

        id_base = int(document_id.replace("-", ""), 16) % (2**53)
        points = []
        for i, chunk in enumerate(self._chunk_text(text)):
            points.append(
                PointStruct(
                    id=id_base + i,
                    vector={
                        "dense": self._embedder.encode_passage(chunk),
                        "bm25": self._sparse.encode_passage(chunk),
                    },
                    payload={
                        "text": chunk,
                        # Marked so citations show it came from the user's own files.
                        "source": f"{filename} (persönliche Datei)",
                        "personal_doc_id": document_id,
                        "user_id": user_id,
                        "doc_type": "personal",
                        "chunk_index": i,
                    },
                )
            )

        if points:
            self._qdrant.upsert(
                collection_name=self._settings.collection_name,
                points=points,
            )
        print(
            f"[RAG] Indexed {len(points)} personal chunks from '{filename}' "
            f"(doc={document_id}, user={user_id})",
            flush=True,
        )
        return len(points)

    def delete_personal_doc(self, document_id: str) -> None:
        """Remove all Qdrant points of one personal file."""
        try:
            self._qdrant.delete(
                collection_name=self._settings.collection_name,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            FieldCondition(
                                key="personal_doc_id", match=MatchValue(value=document_id)
                            )
                        ]
                    )
                ),
            )
        except Exception as e:
            print(f"[RAG] delete personal points failed for {document_id}: {e}", flush=True)

    def reconcile(self) -> dict:
        """Drop Qdrant points whose owui_file_id no longer exists in OWUI — a safety
        net against drift (e.g. a file row removed without firing the delete webhook)."""
        live = {f["id"] for f in self._owui.list_files()}
        qdrant_ids = self._distinct_owui_ids()
        orphans = qdrant_ids - live
        for fid in orphans:
            self._delete_points(fid)
            self._indexed.pop(fid, None)
        print(
            f"[RAG] reconcile: {len(orphans)} orphan files purged "
            f"({len(qdrant_ids)} in Qdrant, {len(live)} live).",
            flush=True,
        )
        return {
            "qdrant_files": len(qdrant_ids),
            "live_files": len(live),
            "orphans_deleted": sorted(orphans),
        }

    def reset_collection(self) -> dict:
        """Wipe and recreate the collection, then clear the in-memory cache. Routing a
        full Qdrant wipe through here keeps the _indexed cache from going stale (a
        direct Qdrant delete would leave it thinking files are still indexed)."""
        try:
            self._qdrant.delete_collection(self._settings.collection_name)
        except Exception as e:
            print(f"[RAG] reset_collection delete failed: {e}", flush=True)
        self._ensure_collection()
        self._indexed.clear()
        print(f"[RAG] reset_collection: '{self._settings.collection_name}' wiped.", flush=True)
        return {"reset": True, "collection": self._settings.collection_name}

    # ------------------------------------------------------------------ internal

    def _index_record(self, record: dict) -> int:
        file_id = record["id"]
        content = record.get("content") or ""
        filename = record.get("filename") or file_id
        if not content.strip():
            print(f"[RAG] {filename} ({file_id}): no extracted text — skipped.", flush=True)
            return 0
        if len(content.strip()) < 20:
            print(
                f"[RAG] {filename} ({file_id}): suspiciously little text extracted "
                f"({len(content.strip())} chars) — possible OCR failure",
                flush=True,
            )

        h = record.get("hash") or hashlib.md5(content.encode("utf-8")).hexdigest()
        if self._indexed.get(file_id) == h:
            # Unchanged since last index.
            return 0

        # Drop any stale points first so re-index / fewer-chunks stays consistent.
        self._delete_points(file_id)

        id_base = int(file_id.replace("-", ""), 16) % (2**53)
        points = []
        for i, chunk in enumerate(self._chunk_text(content)):
            points.append(
                PointStruct(
                    id=id_base + i,
                    vector={
                        "dense": self._embedder.encode_passage(chunk),
                        "bm25": self._sparse.encode_passage(chunk),
                    },
                    payload={
                        "text": chunk,
                        "source": filename,
                        "owui_file_id": file_id,
                        "file_hash": h,
                        "chunk_index": i,
                    },
                )
            )

        if points:
            self._qdrant.upsert(
                collection_name=self._settings.collection_name,
                points=points,
            )
        self._indexed[file_id] = h
        print(f"[RAG] Indexed {len(points)} chunks from '{filename}' ({file_id})", flush=True)
        return len(points)

    def _delete_points(self, file_id: str) -> None:
        try:
            self._qdrant.delete(
                collection_name=self._settings.collection_name,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[FieldCondition(key="owui_file_id", match=MatchValue(value=file_id))]
                    )
                ),
            )
        except Exception as e:
            print(f"[RAG] delete points failed for {file_id}: {e}", flush=True)

    def _distinct_owui_ids(self) -> set[str]:
        """Scroll the whole collection collecting distinct owui_file_id payloads.
        Paginated (unlike _prefetch_indexed) so it stays correct for large collections."""
        ids: set[str] = set()
        offset = None
        while True:
            points, offset = self._qdrant.scroll(
                collection_name=self._settings.collection_name,
                with_payload=["owui_file_id"],
                with_vectors=False,
                limit=1000,
                offset=offset,
            )
            for p in points:
                fid = (p.payload or {}).get("owui_file_id")
                if fid:
                    ids.add(fid)
            if offset is None:
                break
        return ids

    def _ensure_collection(self):
        """Create the Qdrant collection if it doesn't exist — or recreate it if it
        still has the pre-hybrid schema (single unnamed dense vector, no sparse)."""
        name = self._settings.collection_name
        existing = [c.name for c in self._qdrant.get_collections().collections]

        if name in existing and not self._has_hybrid_schema(name):
            print(
                f"[RAG] WARNING: collection '{name}' has the old dense-only schema — "
                f"recreating it for hybrid (dense + BM25) search. All points were "
                f"dropped; refill with POST /api/sync and POST /api/personal-docs/sync.",
                flush=True,
            )
            self._qdrant.delete_collection(name)
            existing.remove(name)

        if name not in existing:
            self._qdrant.create_collection(
                collection_name=name,
                # distance=COSINE is standard for semantic similarity search.
                vectors_config={
                    "dense": VectorParams(size=self._embedder.dim, distance=Distance.COSINE)
                },
                # BM25 arm: term frequencies come from the client, IDF weighting is
                # applied server-side by Qdrant (Modifier.IDF).
                sparse_vectors_config={"bm25": SparseVectorParams(modifier=Modifier.IDF)},
            )
            print(f"[RAG] Created Qdrant collection '{name}' (hybrid dense+bm25)")

    def _has_hybrid_schema(self, name: str) -> bool:
        """True if the collection has the named 'dense' vector (matching the current
        embedding dim) and the 'bm25' sparse vector."""
        try:
            params = self._qdrant.get_collection(name).config.params
        except Exception as e:
            print(f"[RAG] Could not inspect collection '{name}': {e}", flush=True)
            return True  # don't wipe on a read error
        dense = params.vectors
        if not isinstance(dense, dict) or "dense" not in dense:
            return False
        if dense["dense"].size != self._embedder.dim:
            return False
        return bool(params.sparse_vectors) and "bm25" in params.sparse_vectors

    def _prefetch_indexed(self):
        """Rebuild the owui_file_id → file_hash map from existing Qdrant points so
        a container restart doesn't re-embed everything."""
        try:
            scroll_result = self._qdrant.scroll(
                collection_name=self._settings.collection_name,
                with_payload=["owui_file_id", "file_hash"],
                limit=10000,
            )
            for point in scroll_result[0]:
                fid = point.payload.get("owui_file_id")
                h = point.payload.get("file_hash")
                if fid and h:
                    self._indexed[fid] = h
            print(f"[RAG] Pre-loaded {len(self._indexed)} indexed files from Qdrant")
        except Exception as e:
            print(f"[RAG] Could not pre-fetch indexed files: {e}")

    def _chunk_text(self, text: str) -> list[str]:
        """Split text into overlapping fixed-size chunks. Overlap prevents context
        loss at chunk boundaries."""
        size = self._settings.chunk_size
        overlap = self._settings.chunk_overlap
        chunks, start = [], 0
        while start < len(text):
            end = min(start + size, len(text))
            chunks.append(text[start:end])
            if end == len(text):
                break
            start += size - overlap
        return chunks
