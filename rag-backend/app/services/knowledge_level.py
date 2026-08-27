"""
KnowledgeLevelStore — per-user Kompetenzniveau (financial-literacy level)
-------------------------------------------------------------------------
Every learner is assigned a competence level 0–3 (see level_tool.LEVEL_LABELS).
The level is written by the model itself: after a Kompetenzdiagnose it emits a
[[KOMPETENZNIVEAU:n|Begründung]] marker, which routers/rag.py intercepts in the
token stream and turns into a set() call here. It is read back on every chat turn
and injected into the system prompt, so the model adapts its explanation depth.

  {base_dir}/knowledge_level.db    SQLite: tables `user_level`, `level_history`

`base_dir` is the ./data/knowledge_level bind mount (see docker-compose.yml).
Identity comes from OWUI (see owui_auth.py) — this store just trusts the user_id
it is handed and scopes every query by it.

`level_history` is append-only: MINTIFY is a research project, and how a learner's
diagnosed level moves over time is the interesting datum, not just its final value.
"""

import os
import sqlite3
import threading
from datetime import datetime, timezone

# Inclusive bounds of the scale. The model is an untrusted writer, so anything
# outside this range is rejected rather than stored.
MIN_LEVEL = 0
MAX_LEVEL = 3


class KnowledgeLevelStore:
    def __init__(self, base_dir: str):
        os.makedirs(base_dir, exist_ok=True)
        self._db = sqlite3.connect(
            os.path.join(base_dir, "knowledge_level.db"), check_same_thread=False
        )
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_level (
                    user_id    TEXT PRIMARY KEY,
                    level      INTEGER NOT NULL,
                    reason     TEXT,
                    source     TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS level_history (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    TEXT NOT NULL,
                    level      INTEGER NOT NULL,
                    reason     TEXT,
                    source     TEXT,
                    chat_id    TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_history_user ON level_history(user_id)"
            )
            self._db.commit()

    def get(self, user_id: str) -> dict | None:
        """Current level of one user, or None if never diagnosed."""
        if not user_id:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT user_id, level, reason, source, updated_at"
                " FROM user_level WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def set(
        self,
        user_id: str,
        level: int,
        reason: str = "",
        source: str = "diagnose",
        chat_id: str | None = None,
    ) -> dict:
        """Upsert the user's current level and append a history entry.

        Raises ValueError for an out-of-range level — the model writes here, so
        the value is validated at the boundary rather than trusted.
        """
        if not user_id:
            raise ValueError("user_id is required")
        if not isinstance(level, int) or not (MIN_LEVEL <= level <= MAX_LEVEL):
            raise ValueError(f"level must be an int in {MIN_LEVEL}..{MAX_LEVEL}, got {level!r}")

        now = datetime.now(timezone.utc).isoformat()
        reason = (reason or "").strip()
        with self._lock:
            self._db.execute(
                "INSERT INTO user_level (user_id, level, reason, source, updated_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET"
                "   level = excluded.level, reason = excluded.reason,"
                "   source = excluded.source, updated_at = excluded.updated_at",
                (user_id, level, reason, source, now),
            )
            self._db.execute(
                "INSERT INTO level_history (user_id, level, reason, source, chat_id, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, level, reason, source, chat_id, now),
            )
            self._db.commit()
        return {
            "user_id": user_id,
            "level": level,
            "reason": reason,
            "source": source,
            "updated_at": now,
        }

    def history(self, user_id: str, limit: int = 50) -> list[dict]:
        """Most recent level changes for one user, newest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT level, reason, source, chat_id, created_at"
                " FROM level_history WHERE user_id = ?"
                " ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def clear(self, user_id: str) -> bool:
        """Reset a user to 'not yet diagnosed'. History is deliberately kept."""
        with self._lock:
            cursor = self._db.execute(
                "DELETE FROM user_level WHERE user_id = ?", (user_id,)
            )
            self._db.commit()
        return cursor.rowcount > 0

    def list_all(self) -> list[dict]:
        """Every user's current level + how often it changed (admin export)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT u.user_id, u.level, u.reason, u.source, u.updated_at,"
                "       (SELECT COUNT(*) FROM level_history h WHERE h.user_id = u.user_id)"
                "         AS history_count"
                " FROM user_level u ORDER BY u.updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]
