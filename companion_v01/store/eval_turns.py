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


class EvalTurnStoreMixin:
    def append_eval_turn(
        self,
        *,
        trace_id: str,
        session_id: str,
        profile_user_id: str,
        character_pack_id: str = "",
        user_message: str,
        router_json: dict[str, Any],
        verifier_json: dict[str, Any],
        final_json: dict[str, Any],
    ) -> None:
        normalized_character_pack_id = normalize_character_pack_id(character_pack_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO eval_turns (
                    trace_id, created_at, session_id, profile_user_id, character_pack_id, user_message,
                    router_json, verifier_json, final_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    int(time.time()),
                    session_id,
                    profile_user_id,
                    normalized_character_pack_id,
                    user_message,
                    json.dumps(router_json, ensure_ascii=False),
                    json.dumps(verifier_json, ensure_ascii=False),
                    json.dumps(final_json, ensure_ascii=False),
                ),
            )



    def get_latest_eval_turn(
        self,
        session_id: str,
        *,
        character_pack_id: str | None = None,
    ) -> dict[str, Any] | None:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id) if character_pack_id is not None else None
        )
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM eval_turns
                WHERE session_id = ?
                  AND (? IS NULL OR character_pack_id = ?)
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (session_id, normalized_character_pack_id, normalized_character_pack_id),
            ).fetchone()
        return self._row_to_eval_turn(dict(row)) if row else None



    def get_latest_eval_turn_for_session(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str | None = None,
    ) -> dict[str, Any] | None:
        normalized_character_pack_id = (
            normalize_character_pack_id(character_pack_id) if character_pack_id is not None else None
        )
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM eval_turns
                WHERE profile_user_id = ? AND session_id = ?
                  AND (? IS NULL OR character_pack_id = ?)
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (
                    str(profile_user_id),
                    str(session_id),
                    normalized_character_pack_id,
                    normalized_character_pack_id,
                ),
            ).fetchone()
        return self._row_to_eval_turn(dict(row)) if row else None
