"""
PersonalDocsStore — per-user personal file storage
--------------------------------------------------
Users drag & drop private files onto the sidebar drop zone; each user only
ever sees their own documents. Metadata lives in a SQLite table, the raw
bytes on disk:

  {base_dir}/personal_docs.db            SQLite: table `documents`
  {base_dir}/files/{user_id}/{doc_id}    raw file bytes

`base_dir` is the `./data/personal_docs` bind mount (see docker-compose.yml).
Identity comes from OWUI (see owui_auth.py) — this store just trusts the
user_id it is handed and scopes every query by it.
"""

import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone


def _safe_id(value: str) -> str:
    """Reject ids that could escape the storage directory."""
    if not value or value in (".", "..") or "/" in value or "\\" in value or "\x00" in value:
        raise ValueError(f"unsafe id: {value!r}")
    return value


class PersonalDocsStore:
    def __init__(self, base_dir: str):
        self._files_dir = os.path.join(base_dir, "files")
        os.makedirs(self._files_dir, exist_ok=True)

        self._db = sqlite3.connect(
            os.path.join(base_dir, "personal_docs.db"), check_same_thread=False
        )
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    document_id  TEXT PRIMARY KEY,
                    user_id      TEXT NOT NULL,
                    filename     TEXT NOT NULL,
                    content_type TEXT,
                    size_bytes   INTEGER NOT NULL,
                    created_at   TEXT NOT NULL,
                    text_content TEXT
                )
                """
            )
            # Migration for DBs created before text extraction existed.
            columns = {row[1] for row in self._db.execute("PRAGMA table_info(documents)")}
            if "text_content" not in columns:
                self._db.execute("ALTER TABLE documents ADD COLUMN text_content TEXT")
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id)"
            )
            self._db.commit()

    def _path(self, user_id: str, document_id: str) -> str:
        return os.path.join(self._files_dir, _safe_id(user_id), _safe_id(document_id))

    def add(
        self,
        user_id: str,
        filename: str,
        content_type: str | None,
        data: bytes,
        text_content: str = "",
    ) -> dict:
        document_id = uuid.uuid4().hex
        path = self._path(user_id, document_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

        record = {
            "document_id": document_id,
            "filename": filename,
            "content_type": content_type,
            "size_bytes": len(data),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._db.execute(
                "INSERT INTO documents (document_id, user_id, filename, content_type,"
                " size_bytes, created_at, text_content) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    document_id,
                    user_id,
                    filename,
                    content_type,
                    record["size_bytes"],
                    record["created_at"],
                    text_content or None,
                ),
            )
            self._db.commit()
        return record

    def update_text(self, document_id: str, text_content: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE documents SET text_content = ? WHERE document_id = ?",
                (text_content or None, document_id),
            )
            self._db.commit()

    def read_bytes(self, user_id: str, document_id: str) -> bytes | None:
        """Raw file bytes from disk (for re-extraction during /sync)."""
        path = self._path(user_id, document_id)
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as f:
            return f.read()

    def list_all(self) -> list[dict]:
        """Every document of every user, incl. text_content — admin /sync only."""
        with self._lock:
            rows = self._db.execute(
                "SELECT document_id, user_id, filename, content_type, size_bytes,"
                " created_at, text_content FROM documents ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def list(self, user_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT document_id, filename, content_type, size_bytes, created_at"
                " FROM documents WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, user_id: str, document_id: str) -> tuple[dict, str] | None:
        """Return (metadata, absolute file path) or None if not owned/found."""
        with self._lock:
            row = self._db.execute(
                "SELECT document_id, filename, content_type, size_bytes, created_at"
                " FROM documents WHERE user_id = ? AND document_id = ?",
                (user_id, document_id),
            ).fetchone()
        if row is None:
            return None
        path = self._path(user_id, row["document_id"])
        if not os.path.isfile(path):
            return None
        return dict(row), path

    def delete(self, user_id: str, document_id: str) -> bool:
        with self._lock:
            cursor = self._db.execute(
                "DELETE FROM documents WHERE user_id = ? AND document_id = ?",
                (user_id, document_id),
            )
            self._db.commit()
        if cursor.rowcount == 0:
            return False
        try:
            os.remove(self._path(user_id, document_id))
        except FileNotFoundError:
            pass
        return True


def resync_all(store: "PersonalDocsStore", indexer, extractor) -> dict:
    """Re-extract missing text and (re)index every personal document.

    Shared by POST /api/personal-docs/sync and the admin-wide
    POST /api/reindex-all, so both do exactly the same thing. Extraction is
    only attempted for documents that have no text yet — a re-index after a
    chunker change must not depend on docling being reachable.
    """
    docs = store.list_all()
    extracted = 0
    chunks = 0
    for doc in docs:
        text = doc.get("text_content") or ""
        if not text:
            data = store.read_bytes(doc["user_id"], doc["document_id"])
            if data is None:
                continue
            text = extractor.extract(doc["filename"], doc.get("content_type"), data)
            if text:
                store.update_text(doc["document_id"], text)
                extracted += 1
        chunks += indexer.index_personal_doc(
            document_id=doc["document_id"],
            user_id=doc["user_id"],
            filename=doc["filename"],
            text=text,
        )
    print(
        f"[PersonalDocs] resync: {len(docs)} docs, {extracted} newly extracted,"
        f" {chunks} chunks indexed",
        flush=True,
    )
    return {"documents": len(docs), "newly_extracted": extracted, "chunks": chunks}
