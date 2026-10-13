from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import uuid
from typing import Any, Callable

from ..text_utils import timestamp_to_date_label, infer_time_of_day
from .base import (
    CHARACTER_PACK_ID_PATTERN,
    GENERATED_FILE_FORMAT_PATTERN,
    MessageSourceIdCollisionError,
    normalize_character_pack_id,
    _loads_json_object,
    logger,
)


class SessionStoreMixin:
    def _build_default_session_title(
        self,
        conn: sqlite3.Connection,
        profile_user_id: str,
        character_pack_id: str = "",
    ) -> str:
        normalized_character_pack_id = normalize_character_pack_id(character_pack_id)
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM chat_sessions
            WHERE profile_user_id = ? AND character_pack_id = ?
            """,
            (str(profile_user_id), normalized_character_pack_id),
        ).fetchone()
        count = int(row["cnt"] or 0) if row else 0
        if count <= 0:
            return "新的对话"
        return f"新的对话 {count + 1}"



    def ensure_session(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        display_title: str | None = None,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        normalized_profile_user_id = str(profile_user_id or "").strip()
        normalized_session_id = str(session_id or "").strip()
        normalized_character_pack_id = normalize_character_pack_id(character_pack_id)
        if not normalized_profile_user_id or not normalized_session_id:
            raise ValueError("profile_user_id and session_id are required")

        effective_ts = int(timestamp or time.time())
        requested_title = str(display_title or "").strip()

        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT * FROM chat_sessions
                WHERE session_id = ? AND profile_user_id = ?
                LIMIT 1
                """,
                (normalized_session_id, normalized_profile_user_id),
            ).fetchone()
            if existing is not None:
                existing_character_pack_id = normalize_character_pack_id(existing["character_pack_id"])
                effective_character_pack_id = (
                    existing_character_pack_id
                    if normalized_character_pack_id
                    and existing_character_pack_id
                    and existing_character_pack_id != normalized_character_pack_id
                    else (normalized_character_pack_id or existing_character_pack_id)
                )
                if (
                    existing["character_pack_id"] == effective_character_pack_id
                    and existing["updated_at"] == effective_ts
                    and (not requested_title or existing["display_title"] == requested_title)
                ):
                    # Repeated touches in the same second have nothing to commit.
                    return self._row_to_session(dict(existing))
                if requested_title:
                    conn.execute(
                        """
                        UPDATE chat_sessions
                        SET display_title = ?, character_pack_id = ?, updated_at = ?
                        WHERE session_id = ? AND profile_user_id = ?
                        """,
                        (
                            requested_title,
                            effective_character_pack_id,
                            effective_ts,
                            normalized_session_id,
                            normalized_profile_user_id,
                        ),
                    )
                    return self._row_to_session(
                        {
                            **dict(existing),
                            "display_title": requested_title,
                            "character_pack_id": effective_character_pack_id,
                            "updated_at": effective_ts,
                        }
                    )

                conn.execute(
                    """
                    UPDATE chat_sessions
                    SET character_pack_id = ?, updated_at = ?
                    WHERE session_id = ? AND profile_user_id = ?
                    """,
                    (
                        effective_character_pack_id,
                        effective_ts,
                        normalized_session_id,
                        normalized_profile_user_id,
                    ),
                )
                return self._row_to_session(
                    {
                        **dict(existing),
                        "character_pack_id": effective_character_pack_id,
                        "updated_at": effective_ts,
                    }
                )

            resolved_title = requested_title or self._build_default_session_title(
                conn,
                normalized_profile_user_id,
                normalized_character_pack_id,
            )
            payload = {
                "session_id": normalized_session_id,
                "profile_user_id": normalized_profile_user_id,
                "character_pack_id": normalized_character_pack_id,
                "display_title": resolved_title,
                "created_at": effective_ts,
                "updated_at": effective_ts,
            }
            conn.execute(
                """
                INSERT INTO chat_sessions (
                    session_id, profile_user_id, character_pack_id, display_title, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["session_id"],
                    payload["profile_user_id"],
                    payload["character_pack_id"],
                    payload["display_title"],
                    payload["created_at"],
                    payload["updated_at"],
                ),
            )
        return self._row_to_session(payload)



    def rename_session(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        display_title: str,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        normalized_profile_user_id = str(profile_user_id or "").strip()
        normalized_session_id = str(session_id or "").strip()
        normalized_title = str(display_title or "").strip()
        if not normalized_profile_user_id or not normalized_session_id or not normalized_title:
            return None

        effective_ts = int(timestamp or time.time())
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM chat_sessions
                WHERE session_id = ? AND profile_user_id = ?
                LIMIT 1
                """,
                (normalized_session_id, normalized_profile_user_id),
            ).fetchone()
            if row is None:
                return None

            conn.execute(
                """
                UPDATE chat_sessions
                SET display_title = ?, updated_at = ?
                WHERE session_id = ? AND profile_user_id = ?
                """,
                (
                    normalized_title,
                    effective_ts,
                    normalized_session_id,
                    normalized_profile_user_id,
                ),
            )
        return self._row_to_session({**dict(row), "display_title": normalized_title, "updated_at": effective_ts})



    def get_session(self, profile_user_id: str, session_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM chat_sessions
                WHERE profile_user_id = ? AND session_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id)),
            ).fetchone()
        return self._row_to_session(dict(row)) if row else None



    def get_character_session(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> dict[str, Any] | None:
        normalized_character_pack_id = normalize_character_pack_id(character_pack_id)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM chat_sessions
                WHERE profile_user_id = ? AND session_id = ? AND character_pack_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), str(session_id), normalized_character_pack_id),
            ).fetchone()
        return self._row_to_session(dict(row)) if row else None



    def set_session_gift_focus(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        asset_id: str,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        session = self.ensure_session(
            profile_user_id=profile_user_id,
            session_id=session_id,
            timestamp=timestamp,
        )
        effective_ts = int(timestamp or time.time())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE chat_sessions
                SET current_gift_focus_asset_id = ?, current_gift_focus_updated_at = ?, updated_at = ?
                WHERE session_id = ? AND profile_user_id = ?
                """,
                (
                    str(asset_id or "").strip(),
                    effective_ts,
                    effective_ts,
                    str(session_id),
                    str(profile_user_id),
                ),
            )
        return {
            **session,
            "current_gift_focus_asset_id": str(asset_id or "").strip(),
            "current_gift_focus_updated_at": effective_ts,
            "updated_at": effective_ts,
        }



    def clear_session_gift_focus(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        session = self.get_session(profile_user_id, session_id)
        if session is None:
            return None
        effective_ts = int(timestamp or time.time())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE chat_sessions
                SET current_gift_focus_asset_id = '', current_gift_focus_updated_at = ?, updated_at = ?
                WHERE session_id = ? AND profile_user_id = ?
                """,
                (
                    effective_ts,
                    effective_ts,
                    str(session_id),
                    str(profile_user_id),
                ),
            )
        return {
            **session,
            "current_gift_focus_asset_id": "",
            "current_gift_focus_updated_at": effective_ts,
            "updated_at": effective_ts,
        }



    def _backfill_profile_sessions(
        self,
        *,
        conn: sqlite3.Connection,
        profile_user_id: str,
        character_pack_id: str | None = None,
    ) -> None:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id) if character_pack_id is not None else None
        )
        existing_ids = {
            str(row["session_id"])
            for row in conn.execute(
                """
                SELECT session_id FROM chat_sessions
                WHERE profile_user_id = ?
                  AND (? IS NULL OR character_pack_id = ?)
                """,
                (str(profile_user_id), normalized_character_pack_id, normalized_character_pack_id),
            ).fetchall()
        }
        claimed_ids = {
            str(row["session_id"]) for row in conn.execute("SELECT session_id FROM chat_sessions").fetchall()
        }
        legacy_rows = conn.execute(
            """
            SELECT session_id, character_pack_id, MIN(timestamp) AS created_at, MAX(timestamp) AS updated_at
            FROM chat_messages
            WHERE profile_user_id = ?
              AND (? IS NULL OR character_pack_id = ?)
            GROUP BY session_id, character_pack_id
            ORDER BY MIN(timestamp) ASC, session_id ASC
            """,
            (str(profile_user_id), normalized_character_pack_id, normalized_character_pack_id),
        ).fetchall()

        for row in legacy_rows:
            session_id = str(row["session_id"] or "").strip()
            if not session_id or session_id in existing_ids or session_id in claimed_ids:
                continue
            row_character_pack_id = normalize_character_pack_id(row["character_pack_id"])
            created_at = int(row["created_at"] or time.time())
            updated_at = int(row["updated_at"] or created_at)
            title = self._build_default_session_title(conn, str(profile_user_id), row_character_pack_id)
            conn.execute(
                """
                INSERT INTO chat_sessions (
                    session_id, profile_user_id, character_pack_id, display_title, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    str(profile_user_id),
                    row_character_pack_id,
                    title,
                    created_at,
                    updated_at,
                ),
            )
            existing_ids.add(session_id)
            claimed_ids.add(session_id)



    def list_sessions(
        self,
        profile_user_id: str,
        limit: int = 50,
        character_pack_id: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id) if character_pack_id is not None else None
        )
        with self._connect() as conn:
            self._backfill_profile_sessions(
                conn=conn,
                profile_user_id=str(profile_user_id),
                character_pack_id=normalized_character_pack_id,
            )
            rows = conn.execute(
                """
                SELECT * FROM chat_sessions
                WHERE profile_user_id = ?
                  AND (? IS NULL OR character_pack_id = ?)
                ORDER BY updated_at DESC, created_at DESC, session_id DESC
                LIMIT ?
                """,
                (
                    str(profile_user_id),
                    normalized_character_pack_id,
                    normalized_character_pack_id,
                    max(1, int(limit)),
                ),
            ).fetchall()
        return [self._row_to_session(dict(row)) for row in rows]

    def list_care_conversation_origins(self, *, master_qq: str = "") -> list[dict[str, Any]]:
        """Enumerate existing QQ conversations once when splitting care state."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT character_pack_id, session_id
                FROM chat_messages
                WHERE CASE WHEN json_valid(memory_metadata_json)
                    THEN json_extract(memory_metadata_json, '$.client_mode')
                    ELSE '' END IN ('qq', 'qq_text')
                ORDER BY character_pack_id, session_id
                """
            ).fetchall()
            known = {(row["character_pack_id"], row["session_id"]): set() for row in rows}
            # The durable ingress retains the verified sender even when older
            # chat_messages did not carry actor metadata. Read identity only.
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='session_inbox_items'").fetchone():
                identities = conn.execute("""
                    SELECT DISTINCT json_extract(payload_json, '$.turn_payload.character_pack_id') AS character,
                        session_id, json_extract(payload_json, '$.event.user_id') AS actor
                    FROM session_inbox_items WHERE source = 'qq' AND json_valid(payload_json)
                    AND json_extract(payload_json, '$.event.post_type') = 'message'
                    """).fetchall()
                for row in identities:
                    char, session, actor = str(row["character"] or ""), str(row["session_id"] or ""), str(row["actor"] or "")
                    if char and session and actor.isdecimal():
                        known.setdefault((char, session), set()).add(f"qq:{actor}")
            for (char, session), actors in known.items():
                if session == "master" and str(master_qq).isdecimal():
                    actors.add(f"qq:{master_qq}")
                elif re.fullmatch(r"qq_pri_[1-9][0-9]*", session):
                    actors.add(f"qq:{session[7:]}")
        return [{"character_pack_id": char, "session_id": session, "relation_user_ids": sorted(actors)}
                for (char, session), actors in sorted(known.items())]

    def owner_private_activity(self, *, character_pack_id: str) -> dict[str, int]:
        """Read proven shared interaction times; no model text or material leaves the store."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT role, MAX(timestamp) AS latest FROM chat_messages
                WHERE profile_user_id = 'master' AND character_pack_id = ?
                  AND role IN ('user', 'assistant')
                  AND (CASE WHEN json_valid(memory_metadata_json)
                       THEN json_extract(memory_metadata_json, '$.owner_private_shared') ELSE 0 END = 1
                    OR (session_id = 'master' AND CASE WHEN json_valid(memory_metadata_json)
                       THEN json_extract(memory_metadata_json, '$.client_mode') ELSE '' END IN ('qq', 'qq_text')))
                GROUP BY role""", (normalize_character_pack_id(character_pack_id),)
            ).fetchall()
            voice = conn.execute("SELECT last_user_at, last_reply_at FROM owner_private_voice_activity WHERE character_pack_id = ?",
                                 (normalize_character_pack_id(character_pack_id),)).fetchone()
        result = {"last_user_at": 0, "last_reply_at": 0}
        for row in rows:
            result["last_user_at" if row["role"] == "user" else "last_reply_at"] = int(row["latest"] or 0)
        if voice:
            for key in result:
                result[key] = max(result[key], int(voice[key]))
        return result

    def record_owner_private_voice_activity(self, *, character_pack_id: str, role: str, timestamp: int) -> None:
        """Persist only the shared cooldown clock, not a second voice history."""
        if role not in {"user", "assistant"}:
            raise ValueError("private_activity_role_invalid")
        with self._connect() as conn:
            conn.execute("""INSERT INTO owner_private_voice_activity(character_pack_id, last_user_at, last_reply_at)
                VALUES (?, ?, ?) ON CONFLICT(character_pack_id) DO UPDATE SET
                last_user_at = MAX(last_user_at, excluded.last_user_at),
                last_reply_at = MAX(last_reply_at, excluded.last_reply_at)""",
                (normalize_character_pack_id(character_pack_id), timestamp if role == "user" else 0,
                 timestamp if role == "assistant" else 0))



    def has_trusted_qq_group_session(self, session_id: str, *, character_pack_id: str = "") -> bool:
        """Accept an old group only when this Bot stored a QQ-origin message."""

        normalized = str(session_id or "").strip()
        if re.fullmatch(r"qq_group_shared_[1-9][0-9]{0,19}", normalized) is None:
            return False
        return bool(self.list_trusted_qq_group_sessions(
            character_pack_id=character_pack_id, after_session_id="", limit=1,
            exact_session_id=normalized,
        ))



    def qq_group_directory_has_unverified_sessions(self, *, character_pack_id: str = "") -> bool:
        """Flag group messages in this character domain without QQ provenance."""

        pack = normalize_character_pack_id(character_pack_id)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM chat_messages AS m
                WHERE m.character_pack_id = ?
                  AND m.profile_user_id = m.session_id
                  AND m.session_id GLOB 'qq_group_shared_[1-9]*'
                  AND length(m.session_id) BETWEEN 17 AND 36
                  AND substr(m.session_id, 17) NOT GLOB '*[^0-9]*'
                GROUP BY m.session_id
                HAVING SUM(CASE WHEN json_valid(m.memory_metadata_json)
                           THEN CASE WHEN json_extract(m.memory_metadata_json, '$.client_mode')
                                IN ('qq', 'qq_text') THEN 1 ELSE 0 END
                           ELSE 0 END) = 0
                LIMIT 1
                """,
                (pack,),
            ).fetchone()
        return row is not None



    def list_trusted_qq_group_sessions(
        self,
        *,
        character_pack_id: str = "",
        after_session_id: str = "",
        limit: int = 20,
        exact_session_id: str = "",
    ) -> list[dict[str, Any]]:
        """Read QQ-origin messages in the selected character domain.

        chat_sessions has one row per session, so its character_pack_id may
        belong to an older character even when this one has verified messages.
        """

        pack = normalize_character_pack_id(character_pack_id)
        after = str(after_session_id or "").strip()
        exact = str(exact_session_id or "").strip()
        bounded_limit = max(1, min(100, int(limit)))
        exact_clause = "AND m.session_id = ?" if exact else ""
        params = (pack, after, *((exact,) if exact else ()), bounded_limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT m.session_id, m.profile_user_id, m.character_pack_id,
                       '' AS display_title
                FROM chat_messages AS m
                WHERE m.character_pack_id = ?
                  AND m.profile_user_id = m.session_id
                  AND m.session_id GLOB 'qq_group_shared_[1-9]*'
                  AND length(m.session_id) BETWEEN 17 AND 36
                  AND substr(m.session_id, 17) NOT GLOB '*[^0-9]*'
                  AND m.session_id > ?
                  {exact_clause}
                  AND (CASE WHEN json_valid(m.memory_metadata_json)
                       THEN json_extract(m.memory_metadata_json, '$.client_mode')
                       ELSE '' END) IN ('qq', 'qq_text')
                ORDER BY m.session_id ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows
                if re.fullmatch(r"qq_group_shared_[1-9][0-9]{0,19}", str(row["session_id"]))]
