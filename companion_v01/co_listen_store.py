"""Cross-source co-listen history persistence.

This module owns the `co_listen_history` table. It is intentionally a thin,
self-contained module rather than methods on `MemoryStore` so that the
listening-together demo path can land without touching the main store
surface area. Callers pass in an `sqlite3` connection (typically obtained
via `MemoryStore._connect()`); this module handles schema creation,
incremental upserts, and read-side summaries.

Semantics (Listening Together v1 §7.5):

- A play of a given `TrackIdentity` only counts as one co-listen when the
  user has listened past a minimum threshold (default 30s).
- A subsequent count for the same identity is suppressed inside a cooldown
  window (default 5 minutes) so that loops do not inflate the counter.
- `last_listened_at` is always refreshed on touch, even when the count
  itself is not incremented — this preserves the "we just listened to
  this" timestamp without bloating the count.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS co_listen_history (
    profile_user_id TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    title_normalized TEXT NOT NULL DEFAULT '',
    artist_normalized TEXT NOT NULL DEFAULT '',
    album_hint TEXT NOT NULL DEFAULT '',
    display_title TEXT NOT NULL DEFAULT '',
    display_artist TEXT NOT NULL DEFAULT '',
    last_source TEXT NOT NULL DEFAULT '',
    co_listen_count INTEGER NOT NULL DEFAULT 0,
    first_listened_at INTEGER NOT NULL DEFAULT 0,
    last_listened_at INTEGER NOT NULL DEFAULT 0,
    last_commit_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (profile_user_id, identity_key)
);

CREATE INDEX IF NOT EXISTS idx_co_listen_recent
ON co_listen_history(profile_user_id, last_listened_at DESC);
"""


@dataclass(frozen=True)
class CoListenSummary:
    """Read-side projection of a single identity's co-listen state."""

    co_listen_count: int
    first_listened_at: int
    last_listened_at: int
    last_source: str
    display_title: str
    display_artist: str


@dataclass(frozen=True)
class CoListenRecentEntry:
    identity_key: str
    display_title: str
    display_artist: str
    last_source: str
    last_listened_at: int
    co_listen_count: int


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(_SCHEMA_SQL)


def record_co_listen_event(
    connection: sqlite3.Connection,
    *,
    profile_user_id: str,
    identity_key: str,
    title_normalized: str,
    artist_normalized: str,
    album_hint: str,
    display_title: str,
    display_artist: str,
    source: str,
    progress_seconds: float,
    now_ts: int,
    min_listen_seconds: float = 30.0,
    repeat_cooldown_seconds: float = 300.0,
) -> CoListenSummary:
    """Record a touch on `identity_key` and return the updated summary.

    The progress / cooldown rules above gate whether the count is bumped
    on this call; `last_listened_at` is always refreshed.
    """

    profile_user_id = str(profile_user_id or "").strip()
    identity_key = str(identity_key or "").strip()
    if not profile_user_id or not identity_key:
        return CoListenSummary(0, 0, 0, "", "", "")

    row = connection.execute(
        """
        SELECT co_listen_count, first_listened_at, last_listened_at, last_commit_at,
               display_title, display_artist, last_source
        FROM co_listen_history
        WHERE profile_user_id = ? AND identity_key = ?
        """,
        (profile_user_id, identity_key),
    ).fetchone()

    progress = max(0.0, float(progress_seconds or 0))
    eligible_for_count = progress >= float(min_listen_seconds)

    if row is None:
        commit_now = eligible_for_count
        new_count = 1 if commit_now else 0
        first_at = now_ts if commit_now else 0
        commit_at = now_ts if commit_now else 0
        connection.execute(
            """
            INSERT INTO co_listen_history (
                profile_user_id, identity_key,
                title_normalized, artist_normalized, album_hint,
                display_title, display_artist, last_source,
                co_listen_count, first_listened_at, last_listened_at, last_commit_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_user_id,
                identity_key,
                title_normalized,
                artist_normalized,
                album_hint,
                display_title,
                display_artist,
                source,
                new_count,
                first_at,
                now_ts,
                commit_at,
            ),
        )
        return CoListenSummary(
            co_listen_count=new_count,
            first_listened_at=first_at,
            last_listened_at=now_ts,
            last_source=source,
            display_title=display_title,
            display_artist=display_artist,
        )

    (
        prev_count,
        prev_first_at,
        _prev_last_at,
        prev_commit_at,
        prev_display_title,
        prev_display_artist,
        _prev_source,
    ) = row

    prev_count = int(prev_count or 0)
    prev_first_at = int(prev_first_at or 0)
    prev_commit_at = int(prev_commit_at or 0)

    cooldown_passed = (now_ts - prev_commit_at) >= float(repeat_cooldown_seconds)
    should_commit = eligible_for_count and (prev_commit_at == 0 or cooldown_passed)

    new_count = prev_count + 1 if should_commit else prev_count
    new_first_at = prev_first_at or (now_ts if should_commit else 0)
    new_commit_at = now_ts if should_commit else prev_commit_at

    next_display_title = display_title or prev_display_title
    next_display_artist = display_artist or prev_display_artist

    connection.execute(
        """
        UPDATE co_listen_history
        SET title_normalized = ?,
            artist_normalized = ?,
            album_hint = ?,
            display_title = ?,
            display_artist = ?,
            last_source = ?,
            co_listen_count = ?,
            first_listened_at = ?,
            last_listened_at = ?,
            last_commit_at = ?
        WHERE profile_user_id = ? AND identity_key = ?
        """,
        (
            title_normalized,
            artist_normalized,
            album_hint,
            next_display_title,
            next_display_artist,
            source,
            new_count,
            new_first_at,
            now_ts,
            new_commit_at,
            profile_user_id,
            identity_key,
        ),
    )
    return CoListenSummary(
        co_listen_count=new_count,
        first_listened_at=new_first_at,
        last_listened_at=now_ts,
        last_source=source,
        display_title=next_display_title,
        display_artist=next_display_artist,
    )


def get_co_listen_summary(
    connection: sqlite3.Connection,
    *,
    profile_user_id: str,
    identity_key: str,
) -> CoListenSummary | None:
    profile_user_id = str(profile_user_id or "").strip()
    identity_key = str(identity_key or "").strip()
    if not profile_user_id or not identity_key:
        return None
    row = connection.execute(
        """
        SELECT co_listen_count, first_listened_at, last_listened_at,
               last_source, display_title, display_artist
        FROM co_listen_history
        WHERE profile_user_id = ? AND identity_key = ?
        """,
        (profile_user_id, identity_key),
    ).fetchone()
    if row is None:
        return None
    return CoListenSummary(
        co_listen_count=int(row[0] or 0),
        first_listened_at=int(row[1] or 0),
        last_listened_at=int(row[2] or 0),
        last_source=str(row[3] or ""),
        display_title=str(row[4] or ""),
        display_artist=str(row[5] or ""),
    )


def list_recent_co_listened(
    connection: sqlite3.Connection,
    *,
    profile_user_id: str,
    limit: int = 5,
    exclude_identity_key: str = "",
) -> tuple[CoListenRecentEntry, ...]:
    profile_user_id = str(profile_user_id or "").strip()
    if not profile_user_id:
        return ()
    rows: list[Any] = list(
        connection.execute(
            """
            SELECT identity_key, display_title, display_artist, last_source,
                   last_listened_at, co_listen_count
            FROM co_listen_history
            WHERE profile_user_id = ?
              AND co_listen_count > 0
              AND last_listened_at > 0
              AND identity_key != ?
            ORDER BY last_listened_at DESC
            LIMIT ?
            """,
            (profile_user_id, str(exclude_identity_key or ""), max(1, int(limit))),
        ).fetchall()
    )
    return tuple(
        CoListenRecentEntry(
            identity_key=str(row[0] or ""),
            display_title=str(row[1] or ""),
            display_artist=str(row[2] or ""),
            last_source=str(row[3] or ""),
            last_listened_at=int(row[4] or 0),
            co_listen_count=int(row[5] or 0),
        )
        for row in rows
    )
