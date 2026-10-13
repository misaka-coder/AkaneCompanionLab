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


class VisionStoreMixin:
    def get_vision_observation(
        self,
        *,
        observation_type: str,
        resource_fingerprint: str,
        prompt_version: str = "v1",
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM vision_observations
                WHERE observation_type = ? AND resource_fingerprint = ? AND prompt_version = ?
                LIMIT 1
                """,
                (
                    str(observation_type or "").strip(),
                    str(resource_fingerprint or "").strip(),
                    str(prompt_version or "v1").strip() or "v1",
                ),
            ).fetchone()
        return self._row_to_vision_observation(dict(row)) if row else None



    def upsert_vision_observation(
        self,
        *,
        observation_type: str,
        resource_fingerprint: str,
        target_id: str = "",
        source_path: str = "",
        public_path: str = "",
        prompt_version: str = "v1",
        provider: str = "",
        model_name: str = "",
        status: str = "ready",
        summary: str = "",
        observation: dict[str, Any] | None = None,
        error_message: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        normalized_type = str(observation_type or "").strip()
        normalized_fingerprint = str(resource_fingerprint or "").strip()
        normalized_prompt_version = str(prompt_version or "v1").strip() or "v1"
        if not normalized_type or not normalized_fingerprint:
            raise ValueError("observation_type and resource_fingerprint are required")

        normalized_observation = observation if isinstance(observation, dict) else {}
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT * FROM vision_observations
                WHERE observation_type = ? AND resource_fingerprint = ? AND prompt_version = ?
                LIMIT 1
                """,
                (
                    normalized_type,
                    normalized_fingerprint,
                    normalized_prompt_version,
                ),
            ).fetchone()

            if existing is None:
                payload = {
                    "observation_id": f"vision::{uuid.uuid4()}",
                    "observation_type": normalized_type,
                    "resource_fingerprint": normalized_fingerprint,
                    "target_id": str(target_id or "").strip(),
                    "source_path": str(source_path or "").strip(),
                    "public_path": str(public_path or "").strip(),
                    "prompt_version": normalized_prompt_version,
                    "provider": str(provider or "").strip(),
                    "model_name": str(model_name or "").strip(),
                    "status": str(status or "ready").strip() or "ready",
                    "summary": str(summary or "").strip(),
                    "observation_json": json.dumps(normalized_observation, ensure_ascii=False),
                    "error_message": str(error_message or "").strip(),
                    "created_at": effective_ts,
                    "updated_at": effective_ts,
                }
                conn.execute(
                    """
                    INSERT INTO vision_observations (
                        observation_id, observation_type, resource_fingerprint, target_id,
                        source_path, public_path, prompt_version, provider, model_name,
                        status, summary, observation_json, error_message, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["observation_id"],
                        payload["observation_type"],
                        payload["resource_fingerprint"],
                        payload["target_id"],
                        payload["source_path"],
                        payload["public_path"],
                        payload["prompt_version"],
                        payload["provider"],
                        payload["model_name"],
                        payload["status"],
                        payload["summary"],
                        payload["observation_json"],
                        payload["error_message"],
                        payload["created_at"],
                        payload["updated_at"],
                    ),
                )
                return self._row_to_vision_observation(payload)

            payload = {
                **dict(existing),
                "target_id": str(target_id or existing["target_id"] or "").strip(),
                "source_path": str(source_path or existing["source_path"] or "").strip(),
                "public_path": str(public_path or existing["public_path"] or "").strip(),
                "provider": str(provider or existing["provider"] or "").strip(),
                "model_name": str(model_name or existing["model_name"] or "").strip(),
                "status": str(status or existing["status"] or "ready").strip() or "ready",
                "summary": str(summary or existing["summary"] or "").strip(),
                "observation_json": json.dumps(
                    normalized_observation or self._safe_json_loads(existing["observation_json"], fallback={}),
                    ensure_ascii=False,
                ),
                "error_message": str(error_message or "").strip(),
                "updated_at": effective_ts,
            }
            conn.execute(
                """
                UPDATE vision_observations
                SET target_id = ?, source_path = ?, public_path = ?, provider = ?, model_name = ?,
                    status = ?, summary = ?, observation_json = ?, error_message = ?, updated_at = ?
                WHERE observation_id = ?
                """,
                (
                    payload["target_id"],
                    payload["source_path"],
                    payload["public_path"],
                    payload["provider"],
                    payload["model_name"],
                    payload["status"],
                    payload["summary"],
                    payload["observation_json"],
                    payload["error_message"],
                    payload["updated_at"],
                    payload["observation_id"],
                ),
            )
        return self._row_to_vision_observation(payload)
