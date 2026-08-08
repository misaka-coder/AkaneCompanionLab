"""Memory-domain tool handlers: retrieve / timeline / browse / open."""

from __future__ import annotations

import re
from typing import Any, Callable

from ..capability_registry import (
    BROWSE_MEMORY_TOOL_SPEC,
    OPEN_MEMORY_TOOL_SPEC,
    READ_MEMORY_TIMELINE_TOOL_SPEC,
    RETRIEVE_MEMORY_TOOL_SPEC,
)
from ..text_utils import normalize_text
from .core import (
    BaseToolHandler,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolFollowupEnvelope,
)

class RetrieveMemoryToolHandler(BaseToolHandler):
    tool_type = "retrieve_memory"

    def __init__(
        self,
        *,
        retrieve_fn: Callable[..., ToolExecutionResult],
    ) -> None:
        self.retrieve_fn = retrieve_fn

    def tool_spec(self):  # M66-B: canonical ToolSpec authority
        return RETRIEVE_MEMORY_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return f"- retrieve_memory：{RETRIEVE_MEMORY_TOOL_SPEC.description} 这是内部记忆读取，不要先在 speech 里宣布。"

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None

        call_type = str(value.get("type") or "").strip()
        if call_type != self.tool_type:
            return None

        query = str(value.get("query") or "").strip()
        query = normalize_text(query)
        if not query:
            return None

        time_hint: dict[str, Any] = {}
        raw_time_hint = value.get("time_hint")
        if isinstance(raw_time_hint, dict):
            time_hint_schema = (
                RETRIEVE_MEMORY_TOOL_SPEC.input_schema.get("properties", {}).get("time_hint", {}).get("properties", {})
            )
            allowed_time_keys = set(time_hint_schema)
            time_hint = {
                str(key): item
                for key, item in raw_time_hint.items()
                if str(key) in allowed_time_keys and item is not None
            }

        entity_anchors = self._normalize_string_list(value.get("entity_anchors"))
        topic_terms = self._normalize_string_list(value.get("topic_terms"))
        source_layers = self._normalize_string_list(value.get("source_layers"), lowercase=True)
        memory_facets = self._normalize_string_list(value.get("memory_facets"), lowercase=True)
        about_roles = self._normalize_string_list(value.get("about_roles"), lowercase=True)
        raw_within_memory_id = value.get("within_memory_id")
        if raw_within_memory_id is not None and not isinstance(raw_within_memory_id, str):
            return None
        within_memory_id = normalize_text(raw_within_memory_id or "").strip()
        include_explicit = value.get("include_explicit") is True
        kind_patterns = self._normalize_string_list(value.get("kind_patterns"), lowercase=True)

        return {
            "type": self.tool_type,
            "query": query,
            "entity_anchors": entity_anchors,
            "topic_terms": topic_terms,
            "time_hint": time_hint,
            "source_layers": source_layers,
            "memory_facets": memory_facets,
            "about_roles": about_roles,
            "within_memory_id": within_memory_id,
            "include_explicit": include_explicit,
            "kind_patterns": kind_patterns,
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        return self.retrieve_fn(call=call, context=context)

    def _normalize_string_list(self, value: Any, *, lowercase: bool = False) -> list[str]:
        if isinstance(value, str):
            raw_items = [part for part in re.split(r"[,，;；|、\s]+", value) if part]
        elif isinstance(value, (list, tuple, set)):
            raw_items = [str(item or "") for item in value]
        else:
            raw_items = []
        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            text = normalize_text(item).strip("[](){}\"' ")
            if lowercase:
                text = text.lower()
            key = text.casefold()
            if not text or key in seen:
                continue
            seen.add(key)
            normalized.append(text)
        return normalized


class ReadMemoryTimelineToolHandler(BaseToolHandler):
    tool_type = "read_memory_timeline"

    def __init__(self, *, timeline_service: Any) -> None:
        self.timeline_service = timeline_service

    def tool_spec(self):  # M66-B: canonical ToolSpec authority
        return READ_MEMORY_TIMELINE_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            f"- read_memory_timeline：{READ_MEMORY_TIMELINE_TOOL_SPEC.description} "
            "整日或粗时段使用 date_from/date_to；retrieve_memory 返回 raw source_id 且需要附近完整 turn 时，"
            "使用 anchor_source_id 和 before_turns/after_turns。status=partial 时只传 next_cursor 继续，"
            "不要重复原选择器。"
            "这是内部时间线读取，不要先在 speech 里宣布。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "").strip() != self.tool_type:
            return None
        return {
            "type": self.tool_type,
            **{
                str(key): item
                for key, item in value.items()
                if key != "type" and not str(key).startswith("_tool_")
            },
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        if bool(getattr(self.timeline_service, "package_native_dispatch", False)):
            result = self.timeline_service.read(
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                character_pack_id=context.character_pack_id,
                arguments={key: item for key, item in call.items() if key != "type"},
            )
        else:
            raw_periods = call.get("time_periods")
            period_values = list(raw_periods) if isinstance(raw_periods, list) else []
            result = self.timeline_service.read(
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                character_pack_id=context.character_pack_id,
                time_range=dict(call.get("time_range") or {}) or None,
                date_from=str(call.get("date_from") or ""),
                date_to=str(call.get("date_to") or ""),
                time_periods=self.timeline_service.normalize_time_periods(period_values),
                anchor_source_id=str(call.get("anchor_source_id") or ""),
                before_turns=int(call.get("before_turns") or 0),
                after_turns=int(call.get("after_turns") or 0),
                projection=str(call.get("projection") or "conversation"),
                page_token_budget=int(call.get("page_token_budget") or 0),
                cursor=str(call.get("cursor") or ""),
                exclude_source_ids=[context.current_user_source_id] if context.current_user_source_id else [],
            )
        coverage = dict(result.get("coverage") or {})
        complete = bool(
            result.get(
                "coverage_complete",
                coverage.get("complete", str(result.get("status") or "") != "partial"),
            )
        )
        next_cursor = str(result.get("next_cursor") or coverage.get("next_cursor") or "").strip()
        continuation = {"cursor": next_cursor} if next_cursor else None
        followup_context = self.timeline_service.render_tool_context(result)
        return ToolExecutionResult(
            tool_type=self.tool_type,
            followup_context=followup_context,
            followup_envelope=ToolFollowupEnvelope(
                content=followup_context,
                producer_bounded=bool(result.get("ok")) and (complete or continuation is not None),
                complete=complete,
                continuation=continuation,
                diagnostics={
                    "projection": str(result.get("projection") or "conversation"),
                    "coverage": coverage,
                },
            ),
            state_updates={
                "memory_timeline": {
                    "backend": str(result.get("backend") or ""),
                    "status": str(result.get("status") or ""),
                    "reason": str(result.get("reason") or ""),
                    "date_from": str(result.get("date_from") or ""),
                    "date_to": str(result.get("date_to") or ""),
                    "time_periods": list(result.get("time_periods") or []),
                    "anchor_source_id": str(result.get("anchor_source_id") or ""),
                    "before_turns": int(result.get("before_turns") or 0),
                    "after_turns": int(result.get("after_turns") or 0),
                    "projection": str(result.get("projection") or "conversation"),
                    "coverage": coverage,
                    "active_dates": list(result.get("active_dates") or []),
                    "message_count": int(result.get("message_count") or 0),
                }
            },
            trace_receipt=dict(result.get("receipt") or {}) or None,
        )


class BrowseMemoryToolHandler(BaseToolHandler):
    tool_type = "browse_memory"

    def __init__(self, *, timeline_service: Any) -> None:
        self.timeline_service = timeline_service

    def tool_spec(self):
        return BROWSE_MEMORY_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            f"- browse_memory：{BROWSE_MEMORY_TOOL_SPEC.description} "
            "这是内部目录读取，不要先在 speech 里宣布。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "").strip() != self.tool_type:
            return None
        return {
            "type": self.tool_type,
            **{
                str(key): item
                for key, item in value.items()
                if key != "type" and not str(key).startswith("_tool_")
            },
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.timeline_service.browse_memory(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            character_pack_id=context.character_pack_id,
            arguments={key: item for key, item in call.items() if key != "type"},
        )
        complete = bool(result.get("page_complete", True))
        next_cursor = str(result.get("next_cursor") or "").strip()
        continuation = {"cursor": next_cursor} if next_cursor else None
        coverage = dict(result.get("coverage") or {})
        followup_context = self.timeline_service.render_browse_memory_context(result)
        return ToolExecutionResult(
            tool_type=self.tool_type,
            followup_context=followup_context,
            followup_envelope=ToolFollowupEnvelope(
                content=followup_context,
                producer_bounded=bool(result.get("ok")) and (complete or continuation is not None),
                complete=complete,
                continuation=continuation,
                diagnostics={
                    "coverage": coverage,
                    "matched_card_count": int(result.get("matched_card_count") or 0),
                    "returned_card_count": int(result.get("returned_card_count") or 0),
                },
            ),
            state_updates={
                "memory_catalog": {
                    "backend": str(result.get("backend") or ""),
                    "status": str(result.get("status") or ""),
                    "reason": str(result.get("reason") or ""),
                    "node_types": list(result.get("node_types") or []),
                    "coverage": coverage,
                    "matched_card_count": int(result.get("matched_card_count") or 0),
                    "returned_card_count": int(result.get("returned_card_count") or 0),
                    "remaining_card_count": int(result.get("remaining_card_count") or 0),
                }
            },
            trace_receipt=dict(result.get("receipt") or {}) or None,
        )


class OpenMemoryToolHandler(BaseToolHandler):
    tool_type = "open_memory"

    def __init__(self, *, timeline_service: Any) -> None:
        self.timeline_service = timeline_service

    def tool_spec(self):
        return OPEN_MEMORY_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            f"- open_memory：{OPEN_MEMORY_TOOL_SPEC.description} "
            "这是内部证据读取，不要先在 speech 里宣布。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "").strip() != self.tool_type:
            return None
        return {
            "type": self.tool_type,
            **{
                str(key): item
                for key, item in value.items()
                if key != "type" and not str(key).startswith("_tool_")
            },
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        result = self.timeline_service.open_memory(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            character_pack_id=context.character_pack_id,
            arguments={key: item for key, item in call.items() if key != "type"},
        )
        followup_context = self.timeline_service.render_open_memory_context(result)
        return ToolExecutionResult(
            tool_type=self.tool_type,
            followup_context=followup_context,
            followup_envelope=ToolFollowupEnvelope(
                content=followup_context,
                producer_bounded=bool(result.get("ok")),
                complete=bool(result.get("page_complete", True)),
                continuation=(
                    {"cursor": str(result.get("next_cursor") or "")}
                    if str(result.get("next_cursor") or "").strip()
                    else None
                ),
                diagnostics={
                    "memory_id": str(result.get("memory_id") or ""),
                    "memory_ids": list(result.get("memory_ids") or []),
                    "view": str(result.get("view") or ""),
                    "status": str(result.get("status") or ""),
                },
            ),
            state_updates={
                "open_memory": {
                    "backend": str(result.get("backend") or ""),
                    "status": str(result.get("status") or ""),
                    "reason": str(result.get("reason") or ""),
                    "memory_id": str(result.get("memory_id") or ""),
                    "memory_ids": list(result.get("memory_ids") or []),
                    "view": str(result.get("view") or ""),
                }
            },
            trace_receipt=dict(result.get("receipt") or {}) or None,
        )
