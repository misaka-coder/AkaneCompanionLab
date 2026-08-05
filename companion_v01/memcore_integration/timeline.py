"""Thin Akane product adapter for MemCore's native evidence tools."""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Mapping

import config


logger = logging.getLogger("akane.memcore.timeline")

TIME_PERIOD_ORDER = ("midnight", "morning", "afternoon", "night")
TIME_PERIOD_ALIASES = {
    "morning": "morning",
    "上午": "morning",
    "早上": "morning",
    "afternoon": "afternoon",
    "下午": "afternoon",
    "night": "night",
    "evening": "night",
    "晚上": "night",
    "夜晚": "night",
    "midnight": "midnight",
    "凌晨": "midnight",
    "半夜": "midnight",
}


def _memory_backend() -> str:
    backend = str(getattr(config, "MEMORY_BACKEND", "memcore") or "memcore").strip().lower()
    return backend if backend in {"legacy", "dual", "memcore"} else "memcore"


class MemcoreTimelineToolService:
    """Expose MemCore native reads through Akane product/session policy.

    MemCore owns argument validation, finite native paging, evidence rendering,
    coverage, cursors and operation receipts. Akane only chooses the authorized
    namespace and projects the result into its tool runtime.
    """

    package_native_dispatch = True

    def __init__(self, *, legacy_service: Any | None, memcore_manager: Any | None) -> None:
        self.legacy_service = legacy_service
        self.memcore_manager = memcore_manager

    def normalize_time_periods(self, values: Iterable[str] | None) -> list[str]:
        legacy_service = self.legacy_service
        if legacy_service is not None:
            return legacy_service.normalize_time_periods(values)
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values or []:
            period = TIME_PERIOD_ALIASES.get(str(value or "").strip().lower())
            if not period or period in seen:
                continue
            seen.add(period)
            normalized.append(period)
        return [period for period in TIME_PERIOD_ORDER if period in normalized]

    def read(
        self,
        *,
        profile_user_id: str,
        session_id: str = "",
        character_pack_id: str = "",
        time_range: dict[str, Any] | None = None,
        date_from: str = "",
        date_to: str = "",
        time_periods: Iterable[str] | None = None,
        anchor_source_id: str = "",
        before_turns: int = 0,
        after_turns: int = 0,
        exclude_source_ids: Iterable[str] | None = None,
        projection: str = "conversation",
        page_token_budget: int = 0,
        cursor: str = "",
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        manager = self.memcore_manager
        native_arguments = dict(arguments) if isinstance(arguments, Mapping) else None
        if native_arguments is not None:
            anchor_source_id = native_arguments.get("anchor_source_id", "")
            cursor = native_arguments.get("cursor", "")
        anchor_id = str(anchor_source_id or "").strip()
        if _memory_backend() == "memcore":
            if manager is not None and getattr(manager, "enabled", False) and getattr(manager, "available", False):
                try:
                    if native_arguments is None:
                        native_arguments = self._timeline_arguments(
                            time_range=time_range,
                            date_from=date_from,
                            date_to=date_to,
                            time_periods=time_periods,
                            anchor_source_id=anchor_id,
                            before_turns=before_turns,
                            after_turns=after_turns,
                            projection=projection,
                            page_token_budget=page_token_budget,
                            cursor=cursor,
                        )
                    if not str(native_arguments.get("cursor") or "").strip() and not anchor_id:
                        native_arguments.setdefault("cross_conversation", True)
                    result = manager.read_memory_timeline(
                        profile_user_id=profile_user_id,
                        # The cursor remains scoped to the active conversation
                        # namespace. Date/range reads cross only this user's
                        # authorized conversations via explicit host policy.
                        session_id=str(session_id or profile_user_id),
                        character_pack_id=character_pack_id,
                        arguments=native_arguments,
                    )
                except Exception as exc:
                    logger.warning("memcore timeline adapter failed: %s", type(exc).__name__)
                else:
                    if isinstance(result, dict):
                        return result
            return self._unavailable("read_memory_timeline", "memcore_timeline_unavailable")

        legacy_service = self.legacy_service
        if legacy_service is None:
            return self._unavailable("read_memory_timeline", "legacy_timeline_unavailable", backend="legacy")
        if time_range or cursor or str(projection or "conversation") != "conversation" or int(page_token_budget or 0):
            return self._unavailable(
                "read_memory_timeline",
                "precise_timeline_requires_memcore",
                backend="legacy",
            )
        if anchor_id:
            return self._unavailable(
                "read_memory_timeline",
                "raw_anchor_requires_memcore",
                backend="legacy",
            )
        return legacy_service.read(
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            date_from=date_from,
            date_to=date_to,
            time_periods=time_periods,
            exclude_source_ids=exclude_source_ids,
        )

    @staticmethod
    def _timeline_arguments(
        *,
        time_range: dict[str, Any] | None,
        date_from: str,
        date_to: str,
        time_periods: Iterable[str] | None,
        anchor_source_id: str,
        before_turns: int,
        after_turns: int,
        projection: str,
        page_token_budget: int,
        cursor: str,
    ) -> dict[str, Any]:
        resolved_cursor = str(cursor or "").strip()
        if resolved_cursor:
            return {"cursor": resolved_cursor}
        arguments: dict[str, Any] = {}
        if time_range:
            arguments["time_range"] = dict(time_range)
        if str(date_from or "").strip():
            arguments["date_from"] = str(date_from).strip()
        if str(date_to or "").strip():
            arguments["date_to"] = str(date_to).strip()
        if time_periods:
            arguments["time_periods"] = list(time_periods)
        if str(anchor_source_id or "").strip():
            arguments["anchor_source_id"] = str(anchor_source_id).strip()
        if int(before_turns or 0):
            arguments["before_turns"] = int(before_turns)
        if int(after_turns or 0):
            arguments["after_turns"] = int(after_turns)
        if str(projection or "conversation") != "conversation":
            arguments["projection"] = str(projection)
        if int(page_token_budget or 0) > 0:
            arguments["page_token_budget"] = int(page_token_budget)
        return arguments

    def open_memory(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        manager = self.memcore_manager
        if (
            _memory_backend() in {"memcore", "dual"}
            and manager is not None
            and getattr(manager, "enabled", False)
            and getattr(manager, "available", False)
        ):
            try:
                result = manager.open_memory(
                    profile_user_id=profile_user_id,
                    session_id=str(session_id or profile_user_id),
                    character_pack_id=character_pack_id,
                    arguments=dict(arguments or {}),
                )
            except Exception as exc:
                logger.warning("memcore open-memory adapter failed: %s", type(exc).__name__)
            else:
                if isinstance(result, dict):
                    return result
        return self._unavailable("open_memory", "memcore_open_memory_unavailable")

    def browse_memory(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        manager = self.memcore_manager
        if (
            _memory_backend() in {"memcore", "dual"}
            and manager is not None
            and getattr(manager, "enabled", False)
            and getattr(manager, "available", False)
        ):
            native_arguments = dict(arguments or {})
            if not str(native_arguments.get("cursor") or "").strip():
                native_arguments.setdefault("cross_conversation", True)
            try:
                result = manager.browse_memory(
                    profile_user_id=profile_user_id,
                    session_id=str(session_id or profile_user_id),
                    character_pack_id=character_pack_id,
                    arguments=native_arguments,
                )
            except Exception as exc:
                logger.warning("memcore catalog adapter failed: %s", type(exc).__name__)
            else:
                if isinstance(result, dict):
                    return result
        return self._unavailable("browse_memory", "memcore_browse_memory_unavailable")

    def render_tool_context(self, result: dict[str, Any]) -> str:
        legacy_service = self.legacy_service
        if str((result or {}).get("backend") or "") != "memcore":
            if legacy_service is not None:
                return legacy_service.render_tool_context(result)
            return "原始对话时间线读取失败：当前记忆时间线服务不可用。"
        return self._render_native_result("MemCore 原始时间线", result)

    def render_open_memory_context(self, result: dict[str, Any]) -> str:
        return self._render_native_result("MemCore 记忆证据", result)

    def render_browse_memory_context(self, result: dict[str, Any]) -> str:
        return self._render_native_result("MemCore 记忆目录", result)

    @staticmethod
    def _render_native_result(label: str, result: Mapping[str, Any] | None) -> str:
        """Serialize package-owned navigation metadata without reinterpreting it."""

        payload = dict(result or {})
        evidence_text = str(payload.pop("text", "") or "").strip()
        for internal_key in ("operation", "backend", "ok", "receipt"):
            payload.pop(internal_key, None)
        metadata = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        parts = [f"【{label}】", metadata]
        if evidence_text:
            parts.extend(("【本页证据正文】", evidence_text))
        return "\n".join(parts)

    @staticmethod
    def _unavailable(operation: str, reason: str, *, backend: str = "memcore") -> dict[str, Any]:
        return {
            "operation": operation,
            "ok": False,
            "status": "unavailable",
            "reason": reason,
            "coverage": {},
            "text": "",
            "backend": backend,
        }

    def build_acquaintance_prompt(self, **kwargs: Any) -> str:
        manager = self.memcore_manager
        if manager is not None and manager.available:
            acquaintance_note = getattr(manager, "acquaintance_note", None)
            if not callable(acquaintance_note):
                return ""
            profile_user_id = str(kwargs.get("profile_user_id") or "").strip()
            result = acquaintance_note(
                profile_user_id=profile_user_id,
                session_id=profile_user_id,
                character_pack_id=str(kwargs.get("character_pack_id") or "").strip(),
                now_ts=kwargs.get("now_ts"),
            )
            if result:
                return result
        legacy_service = self.legacy_service
        return legacy_service.build_acquaintance_prompt(**kwargs) if legacy_service is not None else ""
