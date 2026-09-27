"""Memory-domain tool handlers: retrieve / timeline / browse / open."""

from __future__ import annotations

import json
import re
from typing import Any, Callable

import config

from ..capability_registry import (
    BROWSE_MEMORY_TOOL_SPEC,
    LIST_MEMORY_CONVERSATIONS_TOOL_SPEC,
    OPEN_MEMORY_TOOL_SPEC,
    READ_MEMORY_TIMELINE_TOOL_SPEC,
    RETRIEVE_MEMORY_TOOL_SPEC,
)


def _memory_rejection(tool: str, status: str, reason: str = "") -> ToolExecutionResult:
    detail = reason or status
    feedback = f"记忆读取失败：status={status}；reason={detail}。"
    return ToolExecutionResult(
        tool_type=tool,
        followup_context=feedback,
        followup_envelope=ToolFollowupEnvelope(
            content=feedback, producer_bounded=True, complete=True,
            diagnostics={"status": status, "reason": detail},
        ),
    )


def _prepare_read(policy_provider: Callable[[], Any] | None, *, call: dict[str, Any],
                  context: ToolExecutionContext, tool: str):
    policy = policy_provider() if policy_provider is not None else None
    if policy is None:
        if "cross_conversation" in call or call.get("conversation", "current") != "current":
            return None, None, {}, _memory_rejection(tool, "forbidden")
        return None, None, {key: value for key, value in call.items() if key not in {"type", "conversation"}}, None
    target, arguments, error = policy.prepare(context=context, call=call, tool=tool)
    if error is not None:
        return policy, None, {}, _memory_rejection(tool, str(error.get("status") or "forbidden"),
                                                    str(error.get("reason") or ""))
    return policy, target, arguments, None
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

        normalized = {
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
        for name in ("conversation", "cross_conversation"):
            if name in value:
                normalized[name] = value[name]
        return normalized

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

    def __init__(self, *, timeline_service: Any, read_policy_provider: Callable[[], Any] | None = None) -> None:
        self.timeline_service = timeline_service
        self.read_policy_provider = read_policy_provider

    def tool_spec(self):  # M66-B: canonical ToolSpec authority
        return READ_MEMORY_TIMELINE_TOOL_SPEC

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
        policy, target, arguments, rejection = _prepare_read(
            self.read_policy_provider, call=call, context=context, tool=self.tool_type,
        )
        if rejection is not None:
            return rejection
        if policy is not None and str(getattr(config, "MEMORY_BACKEND", "memcore")).lower() != "memcore":
            return _memory_rejection(self.tool_type, "unavailable", "precise_conversation_read_requires_memcore")
        if target is not None and target.external and not bool(getattr(self.timeline_service, "package_native_dispatch", False)):
            return _memory_rejection(self.tool_type, "unavailable", "precise_conversation_read_requires_memcore")
        profile = target.profile_user_id if target is not None else context.profile_user_id
        session = target.session_id if target is not None else context.session_id
        if bool(getattr(self.timeline_service, "package_native_dispatch", False)):
            result = self.timeline_service.read(
                profile_user_id=profile,
                session_id=session,
                character_pack_id=context.character_pack_id,
                arguments=arguments,
            )
        else:
            raw_periods = call.get("time_periods")
            period_values = list(raw_periods) if isinstance(raw_periods, list) else []
            result = self.timeline_service.read(
                profile_user_id=profile,
                session_id=session,
                character_pack_id=context.character_pack_id,
                time_range=dict(arguments.get("time_range") or {}) or None,
                date_from=str(arguments.get("date_from") or ""),
                date_to=str(arguments.get("date_to") or ""),
                time_periods=self.timeline_service.normalize_time_periods(period_values),
                anchor_source_id=str(arguments.get("anchor_source_id") or ""),
                before_turns=int(arguments.get("before_turns") or 0),
                after_turns=int(arguments.get("after_turns") or 0),
                projection=str(arguments.get("projection") or "conversation"),
                page_token_budget=int(arguments.get("page_token_budget") or 0),
                cursor=str(arguments.get("cursor") or ""),
                exclude_source_ids=[context.current_user_source_id] if context.current_user_source_id else [],
            )
        if policy is not None and target is not None:
            result = policy.project(context=context, target=target, tool=self.tool_type, result=result)
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

    def __init__(self, *, timeline_service: Any, read_policy_provider: Callable[[], Any] | None = None) -> None:
        self.timeline_service = timeline_service
        self.read_policy_provider = read_policy_provider

    def tool_spec(self):
        return BROWSE_MEMORY_TOOL_SPEC

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
        policy, target, arguments, rejection = _prepare_read(
            self.read_policy_provider, call=call, context=context, tool=self.tool_type,
        )
        if rejection is not None:
            return rejection
        if target is not None and target.external and not bool(getattr(self.timeline_service, "package_native_dispatch", False)):
            return _memory_rejection(self.tool_type, "unavailable", "precise_cross_conversation_requires_memcore")
        result = self.timeline_service.browse_memory(
            profile_user_id=target.profile_user_id if target is not None else context.profile_user_id,
            session_id=target.session_id if target is not None else context.session_id,
            character_pack_id=context.character_pack_id,
            arguments=arguments,
        )
        if policy is not None and target is not None:
            result = policy.project(context=context, target=target, tool=self.tool_type, result=result)
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

    def __init__(self, *, timeline_service: Any, read_policy_provider: Callable[[], Any] | None = None) -> None:
        self.timeline_service = timeline_service
        self.read_policy_provider = read_policy_provider

    def tool_spec(self):
        return OPEN_MEMORY_TOOL_SPEC

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
        policy, target, arguments, rejection = _prepare_read(
            self.read_policy_provider, call=call, context=context, tool=self.tool_type,
        )
        if rejection is not None:
            return rejection
        if target is not None and target.external and not bool(getattr(self.timeline_service, "package_native_dispatch", False)):
            return _memory_rejection(self.tool_type, "unavailable", "precise_cross_conversation_requires_memcore")
        result = self.timeline_service.open_memory(
            profile_user_id=target.profile_user_id if target is not None else context.profile_user_id,
            session_id=target.session_id if target is not None else context.session_id,
            character_pack_id=context.character_pack_id,
            arguments=arguments,
        )
        if policy is not None and target is not None:
            result = policy.project(context=context, target=target, tool=self.tool_type, result=result)
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


class ListMemoryConversationsToolHandler(BaseToolHandler):
    tool_type = "list_memory_conversations"

    def __init__(self, *, read_policy_provider: Callable[[], Any]) -> None:
        self.read_policy_provider = read_policy_provider

    def tool_spec(self):
        return LIST_MEMORY_CONVERSATIONS_TOOL_SPEC

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "").strip() != self.tool_type:
            return None
        return {str(key): item for key, item in value.items() if not str(key).startswith("_tool_")}

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        policy = self.read_policy_provider()
        if policy is None:
            return _memory_rejection(self.tool_type, "unavailable", "memory_read_policy_unavailable")
        if set(call) - {"type", "cursor", "limit"}:
            return _memory_rejection(self.tool_type, "invalid_arguments")
        result = policy.list_conversations(
            context=context, cursor=call.get("cursor", ""), limit=call.get("limit", 20),
        )
        content = "【可读记忆会话】\n" + json.dumps(result, ensure_ascii=False, sort_keys=True)
        next_cursor = str(result.get("next_cursor") or "")
        return ToolExecutionResult(
            tool_type=self.tool_type,
            followup_context=content,
            followup_envelope=ToolFollowupEnvelope(
                content=content, producer_bounded=True, complete=not bool(next_cursor),
                continuation={"cursor": next_cursor} if next_cursor else None,
                diagnostics={"status": str(result.get("status") or "")},
            ),
        )
