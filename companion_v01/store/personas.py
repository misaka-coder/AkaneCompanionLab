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


class PersonaStoreMixin:
    def add_persona_event(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        card_id: str = "",
        event_type: str,
        reason: str = "",
        source_id: str = "",
        payload: dict[str, Any] | None = None,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        event = {
            "event_id": f"persona_event::{uuid.uuid4()}",
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "card_id": str(card_id or "").strip(),
            "event_type": str(event_type or "").strip(),
            "reason": str(reason or "").strip(),
            "source_id": str(source_id or "").strip(),
            "created_at": effective_ts,
            "payload_json": json.dumps(payload if isinstance(payload, dict) else {}, ensure_ascii=False),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO persona_events (
                    event_id, profile_user_id, session_id, card_id, event_type,
                    reason, source_id, created_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"],
                    event["profile_user_id"],
                    event["session_id"],
                    event["card_id"],
                    event["event_type"],
                    event["reason"],
                    event["source_id"],
                    event["created_at"],
                    event["payload_json"],
                ),
            )
        return self._row_to_persona_event(event)



    def create_persona_card(
        self,
        *,
        card_id: str,
        profile_user_id: str,
        session_id: str,
        name: str,
        status: str = "inactive",
        summary: str = "",
        speech_style: str = "",
        interaction_bias: str = "",
        resource_preference: str = "",
        switch_hint: str = "",
        unsuitable_contexts: str = "",
        created_reason: str = "",
        updated_reason: str = "",
        source_ids: list[str] | None = None,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        normalized_status = self._normalize_persona_status(status)
        normalized_sources = self._normalize_string_list(source_ids)
        with self._connect() as conn:
            if normalized_status == "active":
                conn.execute(
                    """
                    UPDATE persona_cards
                    SET status = 'inactive', updated_at = ?
                    WHERE profile_user_id = ? AND session_id = ? AND status = 'active'
                    """,
                    (effective_ts, str(profile_user_id), str(session_id)),
                )
            conn.execute(
                """
                INSERT INTO persona_cards (
                    card_id, profile_user_id, session_id, name, status,
                    summary, speech_style, interaction_bias, resource_preference,
                    switch_hint, unsuitable_contexts, created_reason, updated_reason,
                    source_ids_json, created_at, updated_at, archived_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(card_id),
                    str(profile_user_id),
                    str(session_id),
                    str(name),
                    normalized_status,
                    str(summary or ""),
                    str(speech_style or ""),
                    str(interaction_bias or ""),
                    str(resource_preference or ""),
                    str(switch_hint or ""),
                    str(unsuitable_contexts or ""),
                    str(created_reason or ""),
                    str(updated_reason or ""),
                    json.dumps(normalized_sources, ensure_ascii=False),
                    effective_ts,
                    effective_ts,
                    0,
                ),
            )
            if normalized_status == "active":
                conn.execute(
                    """
                    INSERT INTO persona_session_states (
                        profile_user_id, session_id, active_card_id, updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(profile_user_id, session_id)
                    DO UPDATE SET active_card_id = excluded.active_card_id,
                                  updated_at = excluded.updated_at
                    """,
                    (str(profile_user_id), str(session_id), str(card_id), effective_ts),
                )
        created = (
            self.get_active_persona_card(profile_user_id=profile_user_id, session_id=session_id)
            if normalized_status == "active"
            else self.get_persona_card(profile_user_id=profile_user_id, session_id=session_id, card_id=card_id)
        )
        if created is not None:
            return created
        return {
            "card_id": str(card_id),
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "name": str(name),
            "status": normalized_status,
            "summary": str(summary or ""),
            "speech_style": str(speech_style or ""),
            "interaction_bias": str(interaction_bias or ""),
            "resource_preference": str(resource_preference or ""),
            "switch_hint": str(switch_hint or ""),
            "unsuitable_contexts": str(unsuitable_contexts or ""),
            "created_reason": str(created_reason or ""),
            "updated_reason": str(updated_reason or ""),
            "source_ids": normalized_sources,
            "created_at": effective_ts,
            "updated_at": effective_ts,
            "archived_at": 0,
        }



    def get_persona_card(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        card_id: str,
    ) -> dict[str, Any] | None:
        normalized_card_id = str(card_id or "").strip()
        if not normalized_card_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM persona_cards
                WHERE profile_user_id = ? AND card_id = ?
                LIMIT 1
                """,
                (str(profile_user_id), normalized_card_id),
            ).fetchone()
        return self._row_to_persona_card(dict(row)) if row else None



    def find_persona_card(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        query: str,
    ) -> dict[str, Any] | None:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM persona_cards
                WHERE profile_user_id = ?
                  AND (card_id = ? OR name = ?)
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (str(profile_user_id), normalized_query, normalized_query),
            ).fetchone()
        return self._row_to_persona_card(dict(row)) if row else None



    def get_active_persona_card(self, *, profile_user_id: str, session_id: str) -> dict[str, Any] | None:
        normalized_profile_user_id = str(profile_user_id)
        normalized_session_id = str(session_id)
        with self._connect() as conn:
            state = conn.execute(
                """
                SELECT active_card_id FROM persona_session_states
                WHERE profile_user_id = ? AND session_id = ?
                LIMIT 1
                """,
                (normalized_profile_user_id, normalized_session_id),
            ).fetchone()
            active_card_id = str(state["active_card_id"] or "").strip() if state else ""
            if active_card_id:
                row = conn.execute(
                    """
                    SELECT * FROM persona_cards
                    WHERE profile_user_id = ? AND card_id = ?
                      AND status NOT IN ('archived', 'deleted')
                    LIMIT 1
                    """,
                    (normalized_profile_user_id, active_card_id),
                ).fetchone()
                if row is not None:
                    payload = self._row_to_persona_card(dict(row))
                    payload["status"] = "active"
                    return payload

                conn.execute(
                    """
                    UPDATE persona_session_states
                    SET active_card_id = '', updated_at = ?
                    WHERE profile_user_id = ? AND session_id = ?
                    """,
                    (int(time.time()), normalized_profile_user_id, normalized_session_id),
                )

            # Legacy fallback: older builds stored "active" directly on the
            # card row for a single session. Promote that into session state.
            row = conn.execute(
                """
                SELECT * FROM persona_cards
                WHERE profile_user_id = ? AND session_id = ? AND status = 'active'
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (normalized_profile_user_id, normalized_session_id),
            ).fetchone()
            if row is None:
                return None
            card_id = str(row["card_id"] or "")
            conn.execute(
                """
                INSERT INTO persona_session_states (
                    profile_user_id, session_id, active_card_id, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(profile_user_id, session_id)
                DO UPDATE SET active_card_id = excluded.active_card_id,
                              updated_at = excluded.updated_at
                """,
                (normalized_profile_user_id, normalized_session_id, card_id, int(time.time())),
            )
            payload = self._row_to_persona_card(dict(row))
            payload["status"] = "active"
            return payload



    def list_persona_cards(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        normalized_statuses: list[str] = []
        for item in statuses or ["active", "inactive"]:
            normalized = self._normalize_persona_status(item)
            if normalized and normalized not in normalized_statuses:
                normalized_statuses.append(normalized)
        if not normalized_statuses:
            normalized_statuses = ["active", "inactive"]
        placeholders = ",".join("?" for _ in normalized_statuses)
        normalized_limit = max(1, min(100, int(limit or 20)))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM persona_cards
                WHERE profile_user_id = ? AND status IN ({placeholders})
                ORDER BY
                    CASE status WHEN 'active' THEN 0 WHEN 'inactive' THEN 1 WHEN 'archived' THEN 2 ELSE 3 END,
                    updated_at DESC,
                    created_at DESC
                LIMIT ?
                """,
                [str(profile_user_id), *normalized_statuses, normalized_limit],
            ).fetchall()
        return [self._row_to_persona_card(dict(row)) for row in rows]



    def update_persona_card(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        card_id: str,
        fields: dict[str, Any],
        source_ids: list[str] | None = None,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        current = self.get_persona_card(profile_user_id=profile_user_id, session_id=session_id, card_id=card_id)
        if current is None:
            return None
        effective_ts = int(timestamp or time.time())
        allowed_fields = {
            "name",
            "status",
            "summary",
            "speech_style",
            "interaction_bias",
            "resource_preference",
            "switch_hint",
            "unsuitable_contexts",
            "created_reason",
            "updated_reason",
            "archived_at",
        }
        next_payload = {key: current.get(key) for key in allowed_fields}
        for key, value in (fields or {}).items():
            if key not in allowed_fields:
                continue
            if key == "status":
                next_payload[key] = self._normalize_persona_status(value)
            elif key == "archived_at":
                next_payload[key] = max(0, int(value or 0))
            else:
                next_payload[key] = str(value or "")

        next_sources = self._normalize_string_list(
            [*list(current.get("source_ids") or []), *self._normalize_string_list(source_ids)]
        )
        with self._connect() as conn:
            if str(next_payload.get("status") or "") == "active":
                conn.execute(
                    """
                    UPDATE persona_cards
                    SET status = 'inactive', updated_at = ?
                    WHERE profile_user_id = ? AND session_id = ? AND status = 'active' AND card_id != ?
                    """,
                    (effective_ts, str(profile_user_id), str(session_id), str(card_id)),
                )
            conn.execute(
                """
                UPDATE persona_cards
                SET name = ?, status = ?, summary = ?, speech_style = ?, interaction_bias = ?,
                    resource_preference = ?, switch_hint = ?, unsuitable_contexts = ?,
                    created_reason = ?, updated_reason = ?, source_ids_json = ?,
                    updated_at = ?, archived_at = ?
                WHERE profile_user_id = ? AND card_id = ?
                """,
                (
                    str(next_payload.get("name") or ""),
                    self._normalize_persona_status(next_payload.get("status")),
                    str(next_payload.get("summary") or ""),
                    str(next_payload.get("speech_style") or ""),
                    str(next_payload.get("interaction_bias") or ""),
                    str(next_payload.get("resource_preference") or ""),
                    str(next_payload.get("switch_hint") or ""),
                    str(next_payload.get("unsuitable_contexts") or ""),
                    str(next_payload.get("created_reason") or ""),
                    str(next_payload.get("updated_reason") or ""),
                    json.dumps(next_sources, ensure_ascii=False),
                    effective_ts,
                    int(next_payload.get("archived_at") or 0),
                    str(profile_user_id),
                    str(card_id),
                ),
            )
            if str(next_payload.get("status") or "") == "active":
                conn.execute(
                    """
                    INSERT INTO persona_session_states (
                        profile_user_id, session_id, active_card_id, updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(profile_user_id, session_id)
                    DO UPDATE SET active_card_id = excluded.active_card_id,
                                  updated_at = excluded.updated_at
                    """,
                    (str(profile_user_id), str(session_id), str(card_id), effective_ts),
                )
            if str(next_payload.get("status") or "") in {"archived", "deleted"}:
                conn.execute(
                    """
                    UPDATE persona_session_states
                    SET active_card_id = '', updated_at = ?
                    WHERE profile_user_id = ? AND active_card_id = ?
                    """,
                    (effective_ts, str(profile_user_id), str(card_id)),
                )
        if str(next_payload.get("status") or "") == "active":
            return self.get_active_persona_card(profile_user_id=profile_user_id, session_id=session_id)
        return self.get_persona_card(profile_user_id=profile_user_id, session_id=session_id, card_id=card_id)



    def activate_persona_card(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        card_id: str,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        current = self.get_persona_card(profile_user_id=profile_user_id, session_id=session_id, card_id=card_id)
        if current is None or str(current.get("status") or "") in {"archived", "deleted"}:
            return None
        return self.update_persona_card(
            profile_user_id=profile_user_id,
            session_id=session_id,
            card_id=card_id,
            fields={"status": "active", "archived_at": 0},
            timestamp=timestamp,
        )



    def deactivate_active_persona_card(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        active = self.get_active_persona_card(profile_user_id=profile_user_id, session_id=session_id)
        if active is None:
            return None
        effective_ts = int(timestamp or time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO persona_session_states (
                    profile_user_id, session_id, active_card_id, updated_at
                ) VALUES (?, ?, '', ?)
                ON CONFLICT(profile_user_id, session_id)
                DO UPDATE SET active_card_id = '',
                              updated_at = excluded.updated_at
                """,
                (str(profile_user_id), str(session_id), effective_ts),
            )
        return {**active, "status": "inactive"}
