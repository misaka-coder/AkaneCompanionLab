"""Memcore support operations."""
from __future__ import annotations

import hashlib
import re
from typing import Any


class MemcoreSupportMixin:
    @staticmethod
    def _status(
        operation: str,
        ok: bool,
        status: str,
        *,
        source_id: str = "",
        reason: str = "",
        index_status: str = "",
    ) -> dict[str, Any]:
        return {
            "operation": operation,
            "ok": bool(ok),
            "status": status,
            "source_id": str(source_id or ""),
            "index_status": str(index_status or ""),
            "reason": str(reason or ""),
        }

    @staticmethod
    def _coerce_positive_int_or_none(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    @staticmethod
    def _record_belongs_to_namespace(record: dict[str, Any], namespace: Any) -> bool:
        return (
            str(record.get("tenant_id") or "") == str(getattr(namespace, "tenant_id", "") or "")
            and str(record.get("user_id") or "") == str(getattr(namespace, "user_id", "") or "")
            and str(record.get("domain_id") or "") == str(getattr(namespace, "domain_id", "") or "")
            and str(record.get("conversation_id") or "") == str(getattr(namespace, "conversation_id", "") or "")
        )

    @staticmethod
    def _project_native_memory_dispatch(
        *,
        operation: str,
        dispatched: Any,
    ) -> dict[str, Any]:
        envelope = dict(dispatched) if isinstance(dispatched, dict) else {}
        result = envelope.get("result")
        payload = dict(result) if isinstance(result, dict) else {}
        status = str(payload.get("status") or envelope.get("status") or "failed")
        reason = str(payload.get("reason") or envelope.get("reason") or "")
        return {
            **payload,
            "operation": operation,
            "ok": bool(envelope.get("ok")),
            "status": status,
            "reason": reason,
            "receipt": dict(envelope.get("receipt") or {}),
            "backend": "memcore",
        }

    @staticmethod
    def _kind_segment(value: Any, *, fallback: str) -> str:
        normalized = re.sub(r"[^a-z0-9_-]+", "_", str(value or "").strip().lower()).strip("_-")
        return normalized[:80] or fallback

    @classmethod
    def _kind_suffix(cls, value: Any, *, fallback: str) -> str:
        parts = [cls._kind_segment(part, fallback="") for part in str(value or "").strip().lower().split(".")]
        normalized = ".".join(part for part in parts if part)
        return normalized[:120].strip(".") or fallback

    @classmethod
    def _external_event_metadata(cls, event: dict[str, Any]) -> dict[str, Any]:
        _ = event
        return {}

    @staticmethod
    def _has_memory_annotation(metadata: dict[str, Any]) -> bool:
        from memcore import memory_metadata_has_signal

        return memory_metadata_has_signal(metadata)

    @staticmethod
    def _safe_task_event_text(value: Any, *, limit: int) -> str:
        """Keep background-task text model-reusable without projecting secrets.

        Operation evidence (goal text, messages, failure targets, discovered
        paths) stays intact so a later continuation round can reuse it.
        Resource backing paths belong to structured fields and must not enter
        these text fields in the first place.
        """
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"(?i)\bbearer\s+[^\s]+", "Bearer [redacted]", text)
        text = re.sub(
            r"(?i)\b(api[_-]?key|password|secret|token|authorization)\s*[:=]\s*[^\s,;]+",
            r"\1=[redacted]",
            text,
        )
        text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()[: max(1, int(limit or 1))]

    @staticmethod
    def _stable_turn_id(source_id: str) -> str:
        digest = hashlib.sha256(str(source_id or "").encode("utf-8", errors="ignore")).hexdigest()[:32]
        return f"turn:{digest}"
