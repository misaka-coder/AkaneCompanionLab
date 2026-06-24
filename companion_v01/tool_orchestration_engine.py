from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import config

from .client_protocol import ClientProtocolContext
from .client_protocol import ClientMode
from .tool_invocation import LEGACY_JSON
from .tool_invocation import NATIVE_OPENAI
from .tool_invocation import TOOL_INVOCATION_ID_FIELD
from .tool_invocation import TOOL_SOURCE_FIELD
from .tool_invocation import ToolInvocation
from .tool_invocation import ToolResultEnvelope
from .tool_invocation import ValidationResult
from .tool_invocation import invocation_to_legacy_tool_call
from .tool_invocation import legacy_tool_call_to_invocation
from .native_tool_schema import build_openai_native_tool_specs
from .tool_runtime import ToolExecutionContext


@dataclass(frozen=True)
class NativeToolDecisionPlan:
    status: str
    reason: str
    tools: list[dict[str, Any]]
    legacy_prompt_exclusions: set[str]
    tool_choice: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.tools)


def _bounded_int(raw_value: Any, *, default: int, lower: int = 1, upper: int = 12) -> int:
    try:
        value = int(raw_value)
    except Exception:
        value = default
    return max(lower, min(upper, value))


def max_tool_rounds() -> int:
    return _bounded_int(getattr(config, "MAX_TOOL_ROUNDS", 3), default=3, lower=1, upper=5)


def _configured_family_budget(family: str, *, fallback: int) -> int:
    clean_family = str(family or "").strip()
    if clean_family == "web_research":
        return _bounded_int(getattr(config, "MAX_WEB_RESEARCH_TOOL_ROUNDS", fallback), default=fallback)
    if clean_family == "browser_control":
        return _bounded_int(getattr(config, "MAX_BROWSER_TOOL_ROUNDS", fallback), default=fallback)
    return _bounded_int(fallback, default=max_tool_rounds())


def tool_metadata_dict(handler: Any, *, tool_type: str = "") -> dict[str, Any]:
    raw_metadata: Any = None
    if handler is not None and hasattr(handler, "tool_metadata"):
        try:
            raw_metadata = handler.tool_metadata()
        except Exception:
            raw_metadata = None
    if isinstance(raw_metadata, Mapping):
        metadata = dict(raw_metadata)
    elif raw_metadata is not None:
        metadata = {
            "family": getattr(raw_metadata, "family", ""),
            "operation": getattr(raw_metadata, "operation", ""),
            "risk": getattr(raw_metadata, "risk", ""),
            "default_round_budget": getattr(raw_metadata, "default_round_budget", 3),
            "background": getattr(raw_metadata, "background", False),
        }
    else:
        metadata = {}
    metadata["tool_type"] = str(tool_type or getattr(handler, "tool_type", "") or "").strip()
    metadata["family"] = str(metadata.get("family") or "general").strip() or "general"
    metadata["operation"] = str(metadata.get("operation") or "mixed").strip() or "mixed"
    metadata["risk"] = str(metadata.get("risk") or "medium").strip() or "medium"
    metadata["default_round_budget"] = _bounded_int(
        metadata.get("default_round_budget", 3),
        default=max_tool_rounds(),
    )
    metadata["background"] = bool(metadata.get("background"))
    return metadata


def resolve_tool_round_budget(
    handlers: Mapping[str, Any],
    tool_call: Mapping[str, Any],
    *,
    current_budget: int | None = None,
) -> int:
    base_budget = max_tool_rounds() if current_budget is None else _bounded_int(current_budget, default=max_tool_rounds())
    tool_type = str((tool_call or {}).get("type") or "").strip()
    if not tool_type:
        return base_budget
    handler = handlers.get(tool_type) if isinstance(handlers, Mapping) else None
    metadata = tool_metadata_dict(handler, tool_type=tool_type)
    fallback = max(base_budget, int(metadata.get("default_round_budget") or base_budget))
    family_budget = _configured_family_budget(str(metadata.get("family") or ""), fallback=fallback)
    return max(base_budget, family_budget)


def tool_call_signature(tool_call: dict[str, Any]) -> str:
    payload = {
        str(key): value
        for key, value in dict(tool_call or {}).items()
        if not str(key).startswith("_tool_")
    }
    if str(payload.get("type") or "").strip() == "web_search":
        action = str(payload.get("action") or "search").strip() or "search"
        if action in {"search", "batch_search"}:
            payload.pop("max_results", None)
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return repr(sorted((str(key), str(value)) for key, value in payload.items()))


def describe_tool_call_for_prompt(tool_call: dict[str, Any]) -> str:
    tool_type = str(tool_call.get("type") or "unknown").strip() or "unknown"
    details = {
        str(key): value
        for key, value in dict(tool_call or {}).items()
        if key != "type" and not str(key).startswith("_tool_") and value not in (None, "", [], {})
    }
    if not details:
        return tool_type
    try:
        return f"{tool_type} {json.dumps(details, ensure_ascii=False, sort_keys=True, default=str)[:500]}"
    except Exception:
        return f"{tool_type} {details!r}"[:500]


DEFAULT_MAX_TOOL_FOLLOWUP_CHARS = 8000


def shape_tool_followup(
    followup_context: Any,
    *,
    tool_type: str,
    max_chars: int | None = None,
) -> str:
    """Discipline the tool result text fed back to the model (Claude Code-aligned).

    Two rules, applied at the single point where a tool result becomes
    model-facing feedback:
    - empty-but-successful -> stable placeholder, never an empty tool result
      (mirrors Claude Code's empty tool_result guard; an empty result tail can
      make some models end the turn with no output).
    - over-size -> truncate at a newline boundary with an honest marker that
      reports the full size and how much was omitted, so a huge result can't
      blow up the next round's context AND the model can gauge how far to narrow
      its next call (showing chars-only, without the total, left it guessing).

    Only the tool's own text is bounded here; no paths are introduced. True
    persist-to-workspace offloading (instead of truncation) is a later step and
    must use a workspace-relative handle, never an absolute path (CLAUDE.md §3).
    """
    tool_name = str(tool_type or "tool").strip() or "tool"
    text = str(followup_context or "").strip()
    if not text:
        return f"（{tool_name} 执行成功，但没有返回可展示的内容。）"
    limit = int(max_chars) if max_chars else int(
        getattr(config, "MAX_TOOL_FOLLOWUP_CHARS", DEFAULT_MAX_TOOL_FOLLOWUP_CHARS)
        or DEFAULT_MAX_TOOL_FOLLOWUP_CHARS
    )
    limit = max(500, limit)
    if len(text) <= limit:
        return text
    total = len(text)
    truncated = text[:limit]
    cut = truncated.rfind("\n")
    if cut > limit * 0.6:
        truncated = truncated[:cut]
    truncated = truncated.rstrip()
    shown = len(truncated)
    omitted = max(0, total - shown)
    return (
        f"{truncated}\n…（{tool_name} 结果共约 {total} 字，已截断，仅展示前 {shown} 字"
        f"（省略约 {omitted} 字）；如需被省略的部分，请缩小范围、加过滤条件或分页再调用。）"
    )


def build_multi_tool_followup_context(
    tool_followups: list[str],
    *,
    allow_more: bool,
    stop_reason: str = "",
) -> str:
    lines: list[str] = ["【本轮工具执行记录】"]
    if tool_followups:
        lines.extend([str(item).strip() for item in tool_followups if str(item).strip()])
    else:
        lines.append("(暂时没有可用的工具结果。)")
    if allow_more:
        lines.append(
            "如果任务还没完成，可以继续在 tool_call 字段调用下一步必要工具；"
            "如果用户已经明确交代了下一步，且下一步仍在安全边界和授权范围内，不要为了确认而停下询问；"
            "如果结果已经足够、下一步不明确、或遇到真实阻塞，请将 tool_call 设为 null，并自然回复主人。"
        )
    else:
        if str(stop_reason or "").strip() == "tool_budget_exhausted":
            lines.append("本轮工具预算已经用完；请停止继续调用工具，基于已有搜索、网页或操作结果直接总结。")
        elif str(stop_reason or "").strip() == "tool_unavailable":
            lines.append("刚才的工具返回不可用或失败状态；请停止继续调用工具，自然说明这次没有拿到可靠结果，不要编造。")
        lines.append("本轮不要再调用工具，请将 tool_call 设为 null，并基于已有结果自然回复主人。")
    return "\n\n".join(lines)


def normalize_tool_call(
    engine: Any,
    value: Any,
    *,
    client_context: ClientProtocolContext | None = None,
    profile_user_id: str = "",
    session_id: str = "",
) -> dict[str, Any] | None:
    invocation = normalize_tool_invocation(
        engine,
        value,
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
    )
    if invocation is None:
        return None
    return invocation_to_legacy_tool_call(invocation, include_metadata=True)


def normalize_tool_invocation(
    engine: Any,
    value: Any,
    *,
    client_context: ClientProtocolContext | None = None,
    profile_user_id: str = "",
    session_id: str = "",
) -> ToolInvocation | None:
    if not isinstance(value, dict):
        return None

    tool_type = str(value.get("type") or "").strip()
    if not tool_type:
        return None
    source = _normalize_invocation_source(value.get(TOOL_SOURCE_FIELD))
    invocation_id = str(value.get(TOOL_INVOCATION_ID_FIELD) or "").strip()

    handlers = engine._resolve_tool_handlers(
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
    )
    delegated_media_call = _maybe_delegate_qq_media_tool(
        value,
        tool_type=tool_type,
        handlers=handlers,
        client_context=client_context,
    )
    if delegated_media_call is not None:
        return legacy_tool_call_to_invocation(
            delegated_media_call,
            source=source,
            invocation_id=invocation_id,
        )
    handler = handlers.get(tool_type)
    if handler is None:
        return None
    return legacy_tool_call_to_invocation(
        handler.normalize_call(value),
        source=source,
        invocation_id=invocation_id,
    )


def _normalize_invocation_source(value: Any) -> str:
    source = str(value or "").strip()
    if source in {NATIVE_OPENAI}:
        return source
    return LEGACY_JSON


def native_tool_decision_allowlist() -> set[str]:
    return set(_native_tool_decision_allowlist_items())


def _native_tool_decision_allowlist_items() -> list[str]:
    raw = str(getattr(config, "NATIVE_TOOL_DECISION_ALLOWLIST", "web_search") or "").strip()
    allowed: list[str] = []
    seen: set[str] = set()
    for raw_item in raw.split(","):
        item = raw_item.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        allowed.append(item)
    return allowed or ["web_search"]


def native_web_search_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search public web pages or extract public URL content when the user asks for current, "
                "online, or verifiable public information. Do not use it for localhost, intranet, file "
                "paths, login pages, paid pages, or private links."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["search", "batch_search", "extract", "get_sub_domains"],
                        "description": "Use search for one query, batch_search for multiple queries, extract for one public URL.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query for action=search, or a single query when action=batch_search is unnecessary.",
                    },
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 4,
                        "description": "Multiple search queries for action=batch_search.",
                    },
                    "url": {
                        "type": "string",
                        "description": "Public URL to extract when action=extract.",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "Maximum search results to return.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "minimum": 500,
                        "maximum": 5000,
                        "description": "Maximum extracted characters for action=extract.",
                    },
                    "domain": {
                        "type": "string",
                        "description": "Optional domain filter for search.",
                    },
                    "sub_domain": {
                        "type": "string",
                        "description": "Optional sub-domain filter for search.",
                    },
                    "domains": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 4,
                        "description": "Domains for action=get_sub_domains.",
                    },
                },
                "required": ["action"],
            },
        },
    }


def build_native_tool_schemas(
    handlers: Mapping[str, Any],
    *,
    allow_tool_call: bool,
    allowed_tool_names: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    if not allow_tool_call or not bool(getattr(config, "ENABLE_NATIVE_TOOL_DECISION", False)):
        return []
    if not isinstance(handlers, Mapping):
        return []
    candidate_names = _native_tool_candidate_names(allowed_tool_names=allowed_tool_names)
    schemas: list[dict[str, Any]] = []
    for tool_name in candidate_names:
        handler = handlers.get(tool_name)
        if handler is None:
            continue
        schema = _native_tool_schema_for_handler(tool_name, handler)
        if schema is not None:
            schemas.append(schema)
    return schemas


def build_native_tool_decision_plan(
    handlers: Mapping[str, Any],
    *,
    allow_tool_call: bool,
    provider_supports_native_tools: bool,
    allowed_tool_names: Iterable[str] | None = None,
) -> NativeToolDecisionPlan:
    if not allow_tool_call:
        return NativeToolDecisionPlan(
            status="disabled",
            reason="tool_call_not_allowed",
            tools=[],
            legacy_prompt_exclusions=set(),
        )
    if not bool(getattr(config, "ENABLE_NATIVE_TOOL_DECISION", False)):
        return NativeToolDecisionPlan(
            status="disabled",
            reason="native_tool_decision_disabled",
            tools=[],
            legacy_prompt_exclusions=set(),
        )
    candidate_names = _native_tool_candidate_names(allowed_tool_names=allowed_tool_names)
    if allowed_tool_names is not None and not candidate_names:
        return NativeToolDecisionPlan(
            status="disabled",
            reason="native_tool_not_in_capability_selection",
            tools=[],
            legacy_prompt_exclusions=set(),
        )
    if not isinstance(handlers, Mapping) or not any(name in handlers for name in candidate_names):
        return NativeToolDecisionPlan(
            status="disabled",
            reason="native_tool_handlers_missing",
            tools=[],
            legacy_prompt_exclusions=set(),
        )
    if not provider_supports_native_tools:
        return NativeToolDecisionPlan(
            status="unsupported",
            reason="provider_profile_not_verified_for_native_tools",
            tools=[],
            legacy_prompt_exclusions=set(),
        )
    tools = build_native_tool_schemas(
        handlers,
        allow_tool_call=allow_tool_call,
        allowed_tool_names=candidate_names,
    )
    if not tools:
        return NativeToolDecisionPlan(
            status="disabled",
            reason="native_tool_schemas_empty",
            tools=[],
            legacy_prompt_exclusions=set(),
        )
    return NativeToolDecisionPlan(
        status="enabled",
        reason="verified_native_tools",
        tools=tools,
        legacy_prompt_exclusions=native_legacy_prompt_exclusions(tools),
        tool_choice="auto",
    )


def _native_tool_candidate_names(*, allowed_tool_names: Iterable[str] | None = None) -> list[str]:
    allowlist = _native_tool_decision_allowlist_items()
    if allowed_tool_names is None:
        return allowlist
    allowed = {str(item or "").strip() for item in allowed_tool_names if str(item or "").strip()}
    return [name for name in allowlist if name in allowed]


def _native_tool_schema_for_handler(tool_name: str, handler: Any) -> dict[str, Any] | None:
    normalized_name = str(tool_name or "").strip()
    if normalized_name == "web_search":
        return native_web_search_tool_schema()
    specs = build_openai_native_tool_specs({normalized_name: handler}, allowed_tool_names={normalized_name})
    if not specs:
        return None
    return specs[0]


def native_legacy_prompt_exclusions(native_tools: list[dict[str, Any]] | None) -> set[str]:
    exclusions: set[str] = set()
    for raw in native_tools or []:
        if not isinstance(raw, dict):
            continue
        function = raw.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        if name:
            exclusions.add(name)
    return exclusions


def validate_tool_invocation(
    engine: Any,
    invocation: ToolInvocation | None,
    *,
    client_context: ClientProtocolContext | None = None,
    profile_user_id: str = "",
    session_id: str = "",
    raw_tool_call: Any = None,
) -> ValidationResult:
    if invocation is None:
        return ValidationResult.success()

    tool_type = str(invocation.name or "").strip()
    if not tool_type:
        return ValidationResult.fail("missing_tool_type", "工具调用缺少 type 字段。")

    handlers = engine._resolve_tool_handlers(
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
    )
    handler = handlers.get(tool_type)
    if handler is None:
        available = sorted(str(name) for name in handlers.keys())
        available_text = "、".join(available) if available else "（本轮没有可用工具）"
        return ValidationResult.fail(
            "unknown_tool",
            (
                f"你刚才请求的工具「{tool_type}」在本轮不可用，已被系统忽略。"
                f"本轮真正可用的工具是：{available_text}。"
                "请改用其中一个工具，或把 tool_call 设为 null 并直接回复主人，不要再调用不存在的工具。"
            ),
        )

    candidate_call = raw_tool_call if isinstance(raw_tool_call, dict) else invocation_to_legacy_tool_call(invocation)
    if handler.normalize_call(candidate_call) is None:
        return ValidationResult.fail(
            "bad_args",
            (
                f"你对工具「{tool_type}」的调用参数不完整或格式不对，系统无法执行，已被忽略"
                f"（你提交的是：{describe_tool_call_for_prompt(candidate_call)}）。"
                "请对照该工具所需字段修正后重试，或把 tool_call 设为 null 并直接回复主人。"
            ),
        )
    return ValidationResult.success()


def validate_legacy_tool_call(
    engine: Any,
    value: Any,
    *,
    client_context: ClientProtocolContext | None = None,
    profile_user_id: str = "",
    session_id: str = "",
) -> ValidationResult:
    if not isinstance(value, dict):
        return ValidationResult.success()
    tool_type = str(value.get("type") or "").strip()
    if not tool_type:
        return ValidationResult.success()

    handlers = engine._resolve_tool_handlers(
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
    )
    delegated_media_call = _maybe_delegate_qq_media_tool(
        value,
        tool_type=tool_type,
        handlers=handlers,
        client_context=client_context,
    )
    if delegated_media_call is not None:
        return validate_tool_invocation(
            engine,
            legacy_tool_call_to_invocation(delegated_media_call),
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            raw_tool_call=delegated_media_call,
        )
    return validate_tool_invocation(
        engine,
        legacy_tool_call_to_invocation(value),
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
        raw_tool_call=value,
    )


def classify_tool_call_rejection(
    engine: Any,
    value: Any,
    *,
    client_context: ClientProtocolContext | None = None,
    profile_user_id: str = "",
    session_id: str = "",
) -> str:
    """Explain why an attempted tool call could not be dispatched.

    Returns "" when there was no genuine attempt (the model emitted null / no
    type). Returns a model-readable reason when the model DID try to call a
    tool that is unknown this turn or whose arguments failed validation, so the
    caller can feed that reason back instead of dropping the attempt silently.
    """
    if not isinstance(value, dict):
        return ""
    tool_type = str(value.get("type") or "").strip()
    if not tool_type:
        return ""
    validation = validate_legacy_tool_call(
        engine,
        value,
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
    )
    if validation.ok:
        return ""
    return validation.message


def _maybe_delegate_qq_media_tool(
    value: dict[str, Any],
    *,
    tool_type: str,
    handlers: dict[str, Any],
    client_context: ClientProtocolContext | None,
) -> dict[str, Any] | None:
    if not bool(getattr(config, "QQ_DELEGATE_MEDIA_TO_BACKGROUND", True)):
        return None
    if client_context is None or client_context.effective_mode != ClientMode.QQ_TEXT:
        return None
    if tool_type not in {
        "convert_media_file",
        "separate_audio_stems",
        "clean_voice_track",
        "transcribe_media",
        "prepare_voice_dataset",
    }:
        return None
    delegate_handler = handlers.get("delegate_task")
    if delegate_handler is None:
        return None
    source_values = []
    for key in ("source_id", "source_ids", "source_target", "source_targets", "target", "targets"):
        raw = value.get(key)
        if isinstance(raw, list):
            source_values.extend(str(item or "").strip() for item in raw)
        elif str(raw or "").strip():
            source_values.append(str(raw or "").strip())
    output_bits = []
    for key in ("output_format", "output_title", "mode", "language", "profile"):
        raw = str(value.get(key) or "").strip()
        if raw:
            output_bits.append(f"{key}={raw}")
    brief = (
        "在 QQ 后台工坊执行媒体处理工具 "
        f"{tool_type}，参数为 {describe_tool_call_for_prompt(value)}。"
        "完成后把产物登记为可交付结果，由前台/系统通知用户并发送。"
    )
    delegated = {
        "type": "delegate_task",
        "agent": "media_agent",
        "brief": brief,
        "goal": f"后台完成 QQ 媒体处理：{tool_type}",
        "raw_request": brief,
        "inputs": [item for item in source_values if item][:12],
        "expected_outputs": output_bits or [f"{tool_type} 生成的结果文件"],
        "success_criteria": ["生成用户请求的媒体结果文件", "结果可由 QQ 发回用户"],
    }
    return delegate_handler.normalize_call(delegated)


def promote_narrated_tool_call(
    engine: Any,
    final_output: dict[str, Any],
    *,
    user_message: str,
    client_context: ClientProtocolContext | None = None,
    profile_user_id: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    """Recover when the model narrates a tool call in speech instead of JSON."""
    existing = normalize_tool_call(
        engine,
        final_output.get("tool_call"),
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
    )
    if existing:
        return final_output

    speech_parts = [str(final_output.get("speech") or "")]
    segments = final_output.get("speech_segments")
    if isinstance(segments, list):
        speech_parts.extend(str(item or "") for item in segments)
    speech = "\n".join(part for part in speech_parts if part).strip()
    if not speech:
        return final_output
    narrated_tool = "fetch_media_from_url" in speech or (
        "工具调用" in speech and ("链接" in speech or "url" in speech.lower())
    )
    if not narrated_tool:
        return final_output

    urls = engine._extract_prefetchable_remote_media_urls(str(user_message or ""))
    retry_requested = engine._message_requests_remote_media_retry(user_message)
    if not urls and retry_requested:
        urls = engine._recent_prefetchable_remote_media_urls(
            profile_user_id=profile_user_id,
            session_id=session_id,
        )
    if not urls:
        return final_output
    if not retry_requested and not engine._message_requests_remote_media_fetch(user_message, urls=urls):
        return final_output

    repaired = dict(final_output)
    repaired["tool_call"] = {
        "type": "fetch_media_from_url",
        "urls": urls,
    }
    return repaired


def execute_tool_call(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    character_pack_id: str = "",
    tool_call: dict[str, Any],
    visual_payload: dict[str, Any],
    now_ts: int,
    current_user_source_id: str = "",
    client_context: ClientProtocolContext | None = None,
    memory_exclude_source_ids: list[str] | None = None,
    request_context: dict[str, Any] | None = None,
) -> Any | None:
    invocation = normalize_tool_invocation(
        engine,
        tool_call,
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
    )
    if invocation is None:
        return None
    result, _envelope = execute_tool_invocation(
        engine,
        invocation=invocation,
        profile_user_id=profile_user_id,
        session_id=session_id,
        character_pack_id=character_pack_id,
        visual_payload=visual_payload,
        now_ts=now_ts,
        current_user_source_id=current_user_source_id,
        client_context=client_context,
        memory_exclude_source_ids=memory_exclude_source_ids,
        request_context=request_context,
    )
    return result


def execute_tool_invocation(
    engine: Any,
    *,
    invocation: ToolInvocation,
    profile_user_id: str,
    session_id: str,
    character_pack_id: str = "",
    visual_payload: dict[str, Any],
    now_ts: int,
    current_user_source_id: str = "",
    client_context: ClientProtocolContext | None = None,
    memory_exclude_source_ids: list[str] | None = None,
    request_context: dict[str, Any] | None = None,
) -> tuple[Any | None, ToolResultEnvelope]:
    validation = validate_tool_invocation(
        engine,
        invocation,
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
    )
    if not validation.ok:
        return None, validation_result_to_envelope(invocation=invocation, validation=validation)

    normalized_call = invocation_to_legacy_tool_call(invocation)
    handlers = engine._resolve_tool_handlers(
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
    )
    handler = handlers.get(str(normalized_call.get("type") or ""))
    if handler is None:
        validation = ValidationResult.fail(
            "unknown_tool",
            f"工具「{str(normalized_call.get('type') or '').strip() or 'unknown'}」在本轮不可用。",
        )
        return None, validation_result_to_envelope(invocation=invocation, validation=validation)

    enriched_visual_payload = dict(visual_payload or {})
    enriched_visual_payload["_profile_user_id"] = profile_user_id
    enriched_visual_payload["_character_pack_id"] = str(character_pack_id or "")
    if memory_exclude_source_ids:
        enriched_visual_payload["_memory_retrieval_exclude_source_ids"] = list(memory_exclude_source_ids)
    client_mode = ""
    if client_context is not None:
        client_mode = str(getattr(client_context.effective_mode, "value", client_context.effective_mode) or "")
    result = handler.execute(
        call=normalized_call,
        context=ToolExecutionContext(
            profile_user_id=profile_user_id,
            session_id=session_id,
            now_ts=now_ts,
            visual_payload=enriched_visual_payload,
            character_pack_id=str(character_pack_id or ""),
            current_user_source_id=current_user_source_id,
            client_mode=client_mode,
            request_context=dict(request_context or {}),
        ),
    )
    return result, tool_execution_result_to_envelope(invocation=invocation, result=result)


def validation_result_to_envelope(
    *,
    invocation: ToolInvocation,
    validation: ValidationResult,
) -> ToolResultEnvelope:
    message = str(validation.message or validation.code or "tool_validation_failed").strip()
    return ToolResultEnvelope(
        invocation_id=invocation.id,
        status="error",
        model_feedback=f"<tool_use_error>{message}</tool_use_error>",
        data={"code": str(validation.code or "validation_failed"), "tool": str(invocation.name or "")},
    )


def tool_execution_result_to_envelope(
    *,
    invocation: ToolInvocation,
    result: Any,
) -> ToolResultEnvelope:
    if result is None:
        return ToolResultEnvelope(
            invocation_id=invocation.id,
            status="error",
            model_feedback="<tool_use_error>工具执行没有返回结果。</tool_use_error>",
            data={"code": "empty_result", "tool": str(invocation.name or "")},
        )
    followup = str(getattr(result, "followup_context", "") or "").strip()
    return ToolResultEnvelope(
        invocation_id=invocation.id,
        status="ok",
        model_feedback=followup,
        data={
            "tool_type": str(getattr(result, "tool_type", "") or invocation.name),
            "state_updates": dict(getattr(result, "state_updates", {}) or {}),
        },
        events=list(getattr(result, "stream_events", []) or []),
    )
