"""Session CRUD operations extracted from store/core.py."""

from __future__ import annotations

import time
import uuid
from typing import Any

from .row_mappers import row_to_session


def ensure_session(
    store: Any,
    *,
    profile_user_id: str,
    session_id: str,
    character_pack_id: str = "",
    display_title: str = "",
) -> dict[str, Any]:
    if not str(profile_user_id or "").strip():
        return {"profile_user_id": "", "session_id": ""}
    normalized = str(session_id or str(uuid.uuid4().hex)).strip()
    if not normalized:
        normalized = uuid.uuid4().hex
    with store._connect() as conn:
        row = conn.execute(
            """SELECT * FROM chat_sessions
               WHERE profile_user_id = ? AND session_id = ? AND character_pack_id = ?""",
            (profile_user_id, normalized, character_pack_id or ""),
        ).fetchone()
        if row:
            return row_to_session(dict(row))
        now_ts = int(time.time())
        title = str(display_title or _build_default_session_title(profile_user_id)).strip() or "新的对话"
        conn.execute(
            """INSERT INTO chat_sessions (profile_user_id, session_id, character_pack_id, display_title, latest_message_ts, message_count)
               VALUES (?, ?, ?, ?, ?, 0)""",
            (profile_user_id, normalized, character_pack_id or "", title, now_ts),
        )
        return {
            "profile_user_id": profile_user_id,
            "session_id": normalized,
            "character_pack_id": character_pack_id or "",
            "display_title": title,
            "gift_focus_asset_id": "",
            "gift_focus_updated_at": 0,
            "latest_final_json": {},
            "latest_message_ts": now_ts,
            "message_count": 0,
        }


def rename_session(
    store: Any,
    *,
    profile_user_id: str,
    session_id: str,
    display_title: str,
    character_pack_id: str = "",
) -> dict[str, Any] | None:
    title = str(display_title or "").strip()
    if not title:
        return None
    with store._connect() as conn:
        conn.execute(
            "UPDATE chat_sessions SET display_title = ? WHERE profile_user_id = ? AND session_id = ? AND character_pack_id = ?",
            (title, profile_user_id, session_id, character_pack_id or ""),
        )
        row = conn.execute(
            "SELECT * FROM chat_sessions WHERE profile_user_id = ? AND session_id = ? AND character_pack_id = ?",
            (profile_user_id, session_id, character_pack_id or ""),
        ).fetchone()
        return row_to_session(dict(row)) if row else None


def get_session(store: Any, profile_user_id: str, session_id: str) -> dict[str, Any] | None:
    with store._connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM chat_sessions
            WHERE profile_user_id = ? AND session_id = ?
            LIMIT 1
            """,
            (str(profile_user_id), str(session_id)),
        ).fetchone()
    return row_to_session(dict(row)) if row else None


def get_character_session(
    store: Any,
    profile_user_id: str,
    session_id: str,
    character_pack_id: str,
) -> dict[str, Any] | None:
    with store._connect() as conn:
        row = conn.execute(
            "SELECT * FROM chat_sessions WHERE profile_user_id = ? AND session_id = ? AND character_pack_id = ?",
            (profile_user_id, session_id, character_pack_id or ""),
        ).fetchone()
        return row_to_session(dict(row)) if row else None


def set_session_gift_focus(
    store: Any,
    *,
    profile_user_id: str,
    session_id: str,
    asset_id: str,
) -> None:
    now_ts = int(time.time())
    with store._connect() as conn:
        conn.execute(
            "UPDATE chat_sessions SET gift_focus_asset_id = ?, gift_focus_updated_at = ? WHERE profile_user_id = ? AND session_id = ?",
            (asset_id, now_ts, profile_user_id, session_id),
        )


def clear_session_gift_focus(store: Any, *, profile_user_id: str, session_id: str) -> None:
    with store._connect() as conn:
        conn.execute(
            "UPDATE chat_sessions SET gift_focus_asset_id = '', gift_focus_updated_at = 0 WHERE profile_user_id = ? AND session_id = ?",
            (profile_user_id, session_id),
        )


def list_sessions(
    store: Any,
    profile_user_id: str,
    *,
    character_pack_id: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    limit = max(1, int(limit))
    with store._connect() as conn:
        if character_pack_id:
            rows = conn.execute(
                """SELECT * FROM chat_sessions WHERE profile_user_id = ? AND character_pack_id = ? ORDER BY latest_message_ts DESC LIMIT ?""",
                (profile_user_id, character_pack_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM chat_sessions WHERE profile_user_id = ? ORDER BY latest_message_ts DESC LIMIT ?""",
                (profile_user_id, limit),
            ).fetchall()
        return [row_to_session(dict(r)) for r in rows]


def _build_default_session_title(profile_user_id: str) -> str:
    return f"{profile_user_id} 的对话"


def backfill_profile_sessions(store: Any, *, profile_user_id: str) -> int:
    """Ensure at least one session exists for this profile user."""
    count = 0
    with store._connect() as conn:
        existing = conn.execute(
            "SELECT COUNT(*) FROM chat_sessions WHERE profile_user_id = ?", (profile_user_id,)
        ).fetchone()[0]
        if existing == 0:
            session_id = uuid.uuid4().hex
            now_ts = int(time.time())
            title = _build_default_session_title(profile_user_id)
            conn.execute(
                "INSERT INTO chat_sessions (profile_user_id, session_id, character_pack_id, display_title, latest_message_ts, message_count) VALUES (?, ?, '', ?, ?, 0)",
                (profile_user_id, session_id, title, now_ts),
            )
            count = 1
    return count
