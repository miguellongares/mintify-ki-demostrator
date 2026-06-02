"""
OWUIClient — read already-extracted document text from Open Web UI
------------------------------------------------------------------
OWUI handles file uploads for EVERY file type (pdf, docx, xlsx, pptx, md, …),
extracts the text with its own loaders, and stores the full text in its SQL
`file` table under `data.content`. Rather than re-parsing files ourselves, the
indexer asks OWUI for that extracted text by file id and embeds it into Qdrant.

This makes OWUI the universal "upload + extract" layer and the rag-backend +
Qdrant the single RAG system.

Endpoints used (require a Bearer API key — create an admin key in OWUI):
  GET /api/v1/files/{id}   → full file model incl. data.content, filename, hash
  GET /api/v1/files/       → list of all files (content included by default)
"""

import httpx


class OWUIClient:
    def __init__(self, base_url: str, api_key: str):
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        if not api_key:
            print("[RAG] WARNING: OWUI_API_KEY not set — OWUI ingestion will fail.")

    @staticmethod
    def _to_record(data: dict, fallback_id: str = "") -> dict:
        """Normalise an OWUI file object into {id, filename, content, hash}."""
        meta = data.get("meta") or {}
        content = (data.get("data") or {}).get("content", "") or ""
        return {
            "id": data.get("id") or fallback_id,
            "filename": data.get("filename") or meta.get("name") or data.get("id") or fallback_id,
            "content": content,
            "hash": data.get("hash") or "",
        }

    def fetch_file(self, file_id: str) -> dict | None:
        """Fetch one file's extracted text by id. Returns None if unavailable."""
        url = f"{self._base}/api/v1/files/{file_id}"
        try:
            r = httpx.get(url, headers=self._headers, timeout=30.0)
            r.raise_for_status()
            return self._to_record(r.json(), fallback_id=file_id)
        except Exception as e:
            print(f"[RAG] OWUI fetch failed for {file_id}: {e}", flush=True)
            return None

    def list_files(self) -> list[dict]:
        """List all OWUI files (with extracted content). Used for backfill/sync."""
        url = f"{self._base}/api/v1/files/"
        try:
            r = httpx.get(url, headers=self._headers, timeout=60.0)
            r.raise_for_status()
            return [self._to_record(f) for f in r.json()]
        except Exception as e:
            print(f"[RAG] OWUI list_files failed: {e}", flush=True)
            return []
