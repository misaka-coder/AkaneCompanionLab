"""Thin Akane product adapter for MemCore's native evidence tools."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable, Mapping

import config


logger = logging.getLogger("akane.memcore.timeline")


def _project_catalog_participants(value: Any) -> Any:
    """Present each card's flat list once; keep package identities in storage."""
    if isinstance(value, list):
        return [_project_catalog_participants(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    projected = {key: _project_catalog_participants(item) for key, item in value.items()}
    refs = value.get("participant_refs")
    if isinstance(refs, list) and value.get("memory_id") and value.get("node_type") in {"raw", "episodic", "semantic"}:
        projected.pop("participant_refs", None)
        participants = []
        for ref in refs:
            if not isinstance(ref, Mapping):
                continue
            actor_id = str(ref.get("actor_id") or "").strip()
            if not actor_id:
                continue
            name = str(ref.get("display_name") or "").strip()
            qq = re.fullmatch(r"qq:([0-9]+)", actor_id)
            label = f"QQ {qq.group(1)}" if qq else f"ID {actor_id}"
            duplicate_name = name in {actor_id, label, qq.group(1) if qq else actor_id}
            participants.append(f"{name}（{label}）" if name and not duplicate_name else label)
        projected["participants"] = participants
    return projected


def _compact_card(card: Mapping[str, Any]) -> dict[str, Any]:
    """Display a navigation card, retaining full diagnostics in the trusted result."""
    allowed = (
        "memory_id", "node_type", "memory_title", "period_start_at", "period_end_at",
        "participants", "matched_terms", "keyword_hits", "matched_source_count", "match_ref",
    )
    result = {key: card[key] for key in allowed if card.get(key) or key == "matched_source_count" and key in card}
    # This bounds tag presentation, never the query pool or evidence membership.
    remaining = 8
    for key in ("entity_anchors", "topic_terms"):
        words = list(card.get(key) or [])
        if words and remaining:
            result[key] = words[:remaining]
            remaining -= len(result[key])
    if card.get("keyword_evidence_complete") is False:
        result["keyword_evidence_complete"] = False
    if card.get("has_sources") is False:
        result["has_sources"] = False
    if not card.get("memory_title") and card.get("catalog_hint"):
        result["catalog_hint"] = card["catalog_hint"]
    preview = card.get("match_preview")
    if isinstance(preview, Mapping):
        identity = _project_catalog_participants({
            "memory_id": "preview", "node_type": "raw", "participant_refs": [{
                "actor_id": preview.get("actor_id"), "display_name": preview.get("actor_display_name"),
            }],
        }).get("participants", [])
        result["match_preview"] = {
            key: preview[key] for key in ("source_id", "timestamp", "text") if preview.get(key)
        }
        if identity:
            result["match_preview"]["speaker"] = identity[0]
    return result


def _compact_native_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Remove duplicate scope/time/diagnostics before the provider sees a result."""
    output = _project_catalog_participants(payload)
    if isinstance(output.get("cards"), list):
        output = {key: value for key, value in output.items() if key in {
            "status", "reason", "conversation", "time_range", "date_from", "date_to", "time_periods",
            "keywords", "keyword_match", "participants", "participant_match", "node_types",
            "matched_card_count", "returned_card_count", "remaining_card_count", "page_complete", "next_cursor",
            "cards", "coverage",
        } and (value or key in {"page_complete", "returned_card_count", "remaining_card_count"})}
        output["cards"] = [_compact_card(card) for card in output.get("cards", []) if isinstance(card, Mapping)]
        coverage = payload.get("coverage") or {}
        output["coverage"] = {key: coverage[key] for key in (
            "total_source_count", "live_source_count", "gap_source_count", "history_start_at", "history_end_at",
        ) if key in coverage}
    elif output.get("view") == "card" and isinstance(output.get("result"), Mapping):
        output["result"] = _compact_card(output["result"])
        for key in ("memory_id", "node_type"):
            if output["result"].get(key) == output.get(key):
                output["result"].pop(key, None)
    elif output.get("view") == "sources" and isinstance(output.get("result"), Mapping):
        page = dict(output["result"])
        # A single tree's signed cursor is also promoted to the root by policy.
        # Batch children retain their independent cursors and paging state.
        if not isinstance(page.get("items"), list):
            for key in ("next_cursor", "page_complete"):
                if key in output and page.get(key) == output[key]:
                    page.pop(key, None)
        output["result"] = page
    elif "selected_entry_count" in output and isinstance(output.get("coverage"), Mapping):
        coverage = dict(output["coverage"])
        # Per-entry IDs already accompany the evidence. Counts and continuation
        # belong in one place; retain actual returned spans and missing coverage.
        coverage.pop("returned_logical_unit_ids", None)
        for key, root_key in {
            "complete": "coverage_complete", "entry_count": "returned_entry_count",
            "logical_unit_count": "returned_logical_unit_count",
            "projected_token_count": "returned_projected_token_count",
            "total_entry_count": "selected_entry_count",
            "total_logical_unit_count": "selected_logical_unit_count",
        }.items():
            if root_key in output and coverage.get(key) == output[root_key]:
                coverage.pop(key, None)
        for key in list(coverage):
            if key in output and coverage[key] == output[key]:
                coverage.pop(key)
        time_range = output.get("time_range") or {}
        for key, range_key in (("requested_start", "start_at"), ("requested_end", "end_at")):
            if time_range.get(range_key) == coverage.get(key):
                coverage.pop(key, None)
        if output.get("message_count") == output.get("returned_entry_count"):
            output.pop("message_count", None)
        output["coverage"] = coverage
    output.pop("result_projection", None)
    if output.get("conversation"):
        output.pop("source_label", None)
    return output


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

    def __init__(self, *, legacy_service: Any | None = None, memcore_manager: Any | None = None) -> None:
        self.legacy_service = legacy_service
        self.memcore_manager = memcore_manager

    def normalize_time_periods(self, values: Iterable[str] | None) -> list[str]:
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
                    if "cross_conversation" in native_arguments:
                        return self._unavailable("read_memory_timeline", "cross_conversation_requires_conversation")
                    if not str(native_arguments.get("cursor") or "").strip():
                        native_arguments["cross_conversation"] = False
                    result = manager.read_memory_timeline(
                        profile_user_id=profile_user_id,
                        # The host selected one authorized conversation before
                        # this adapter sees the request.
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
            native_arguments = dict(arguments or {})
            speakers = native_arguments.get("speaker_ids")
            if isinstance(speakers, list):
                native_arguments["speaker_ids"] = [
                    f"qq:{item}" if isinstance(item, str) and re.fullmatch(r"[0-9]+", item) else item
                    for item in speakers
                ]
            if "cross_conversation" in native_arguments:
                return self._unavailable("open_memory", "cross_conversation_requires_conversation")
            if not str(native_arguments.get("cursor") or "").strip() and not native_arguments.get("match_ref"):
                native_arguments["cross_conversation"] = False
            try:
                result = manager.open_memory(
                    profile_user_id=profile_user_id,
                    session_id=str(session_id or profile_user_id),
                    character_pack_id=character_pack_id,
                    arguments=native_arguments,
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
            participants = native_arguments.get("participants")
            if isinstance(participants, list):
                native_arguments["participants"] = [
                    f"qq:{item.strip()}" if isinstance(item, str) and re.fullmatch(r"[0-9]+", item.strip()) else item
                    for item in participants
                ]
            if "cross_conversation" in native_arguments:
                return self._unavailable("browse_memory", "cross_conversation_requires_conversation")
            if not str(native_arguments.get("cursor") or "").strip():
                native_arguments["cross_conversation"] = False
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
        """Preserve evidence and cursors while presenting paging once."""

        payload = dict(result or {})
        evidence_text = str(payload.pop("text", "") or "").strip()
        for internal_key in ("operation", "backend", "ok", "receipt"):
            payload.pop(internal_key, None)
        metadata = json.dumps(_compact_native_metadata(payload), ensure_ascii=False, sort_keys=True, default=str)
        parts = [f"【{label}】", metadata]
        if label in {"MemCore 原始时间线", "MemCore 记忆证据"} and (
            payload.get("status") == "partial" or payload.get("coverage_complete") is False
            or payload.get("page_complete") is False
            or (isinstance(payload.get("result"), Mapping) and payload["result"].get("page_complete") is False)
        ):
            parts.append("当前证据不完整；需要完整总结时应沿续读凭据读取剩余记录，直接回答时须注明已读范围与缺失。")
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
