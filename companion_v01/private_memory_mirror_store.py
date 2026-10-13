"""Host transfer receipts; dialogue truth remains exclusively in MemCore."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class PrivateMemoryMirrorStore:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS private_memory_mirror_sources (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL UNIQUE, bot_id TEXT NOT NULL,
                    character_id TEXT NOT NULL, turn_id TEXT NOT NULL, final_id TEXT NOT NULL,
                    owner_qq TEXT NOT NULL, owner_label TEXT NOT NULL, channel TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS private_memory_mirror_deliveries (
                    source_key TEXT NOT NULL, session_id TEXT NOT NULL, generation INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    available_at REAL NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(source_key, session_id)
                );
                CREATE INDEX IF NOT EXISTS private_memory_mirror_pending
                    ON private_memory_mirror_deliveries(status, session_id, available_at);
                CREATE INDEX IF NOT EXISTS private_memory_mirror_bot
                    ON private_memory_mirror_sources(bot_id, sequence);
            """)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.database_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def reserve(self, *, source_key, bot_id, character_id, turn_id, final_id, owner_qq, owner_label, channel, targets):
        # Reserve before final commit. Recovery reads only these pending IDs,
        # including the crash gap before the host receives completion success.
        with self._connect() as conn:
            inserted = conn.execute("""
                INSERT OR IGNORE INTO private_memory_mirror_sources
                (source_key, bot_id, character_id, turn_id, final_id, owner_qq, owner_label, channel)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (source_key, bot_id, character_id, turn_id, final_id, owner_qq, owner_label, channel)).rowcount
            if inserted:
                conn.executemany("""
                    INSERT INTO private_memory_mirror_deliveries(source_key, session_id, generation)
                    VALUES (?, ?, ?)
                """, [(source_key, session, generation) for session, generation in targets.items()])

    def pending(self, bot_id, session_id, *, limit=100):
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT s.*, d.session_id, d.generation, d.attempts, d.available_at
                FROM private_memory_mirror_deliveries d CROSS JOIN private_memory_mirror_sources s
                WHERE s.source_key = d.source_key AND s.bot_id = ? AND d.session_id = ?
                  AND d.status IN ('pending', 'retry_pending')
                ORDER BY s.sequence LIMIT ?
            """, (bot_id, session_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def due_sessions(self, bot_id):
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT DISTINCT d.session_id
                FROM private_memory_mirror_deliveries d JOIN private_memory_mirror_sources s USING(source_key)
                WHERE s.bot_id = ? AND d.status IN ('pending', 'retry_pending') AND d.available_at <= ?
            """, (bot_id, time.time())).fetchall()
        return [row[0] for row in rows]

    def finish(self, source_key, session_id, status, *, reason="", delay=0):
        with self._connect() as conn:
            conn.execute("""
                UPDATE private_memory_mirror_deliveries SET status = ?, reason = ?, available_at = ?,
                    attempts = attempts + ? WHERE source_key = ? AND session_id = ?
            """, (status, reason[:160], time.time() + delay, int(status == "retry_pending"), source_key, session_id))

    def status(self, bot_id, session_id):
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT d.status, COUNT(*) AS count
                FROM private_memory_mirror_deliveries d CROSS JOIN private_memory_mirror_sources s
                WHERE s.source_key = d.source_key AND s.bot_id = ? AND d.session_id = ?
                  AND d.status IN ('pending', 'retry_pending')
                GROUP BY d.status
            """, (bot_id, session_id)).fetchall()
            latest = conn.execute("""
                SELECT d.reason FROM private_memory_mirror_deliveries d CROSS JOIN private_memory_mirror_sources s
                WHERE s.source_key = d.source_key AND s.bot_id = ? AND d.session_id = ? AND d.status = 'retry_pending'
                ORDER BY s.sequence DESC LIMIT 1
            """, (bot_id, session_id)).fetchone()
        counts = {row["status"]: row["count"] for row in rows}
        return {"pending": counts.get("pending", 0), "retry_pending": counts.get("retry_pending", 0),
                "reason": latest["reason"] if latest else ""}
