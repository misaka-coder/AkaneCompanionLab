from __future__ import annotations

import json
from typing import Any, Mapping

import config

from .client_protocol import ClientProtocolContext
from .client_protocol import ClientMode
from .tool_runtime import ToolExecutionContext


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
    try:
        return json.dumps(tool_call, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return repr(sorted((str(key), str(value)) for key, value in dict(tool_call or {}).items()))


def describe_tool_call_for_prompt(tool_call: dict[str, Any]) -> str:
    tool_type = str(tool_call.get("type") or "unknown").strip() or "unknown"
    details = {
        str(key): value
        for key, value in dict(tool_call or {}).items()
        if key != "type" and value not in (None, "", [], {})
    }
    if not details:
        return tool_type
    try:
        return f"{tool_type} {json.dumps(details, ensure_ascii=False, sort_keys=True, default=str)[:500]}"
    except Exception:
        return f"{tool_type} {details!r}"[:500]


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
    if not isinstance(value, dict):
        return None

    tool_type = str(value.get("type") or "").strip()
    if not tool_type:
        return None

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
        return delegated_media_call
    handler = handlers.get(tool_type)
    if handler is None:
        return None
    return handler.normalize_call(value)


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

    handlers = engine._resolve_tool_handlers(
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
    )
    # A call that gets delegated to the QQ background worker is not a rejection.
    if _maybe_delegate_qq_media_tool(
        value, tool_type=tool_type, handlers=handlers, client_context=client_context,
    ) is not None:
        return ""

    handler = handlers.get(tool_type)
    if handler is None:
        available = sorted(str(name) for name in handlers.keys())
        available_text = "、".join(available) if available else "（本轮没有可用工具）"
        return (
            f"你刚才请求的工具「{tool_type}」在本轮不可用，已被系统忽略。"
            f"本轮真正可用的工具是：{available_text}。"
            "请改用其中一个工具，或把 tool_call 设为 null 并直接回复主人，不要再调用不存在的工具。"
        )
    if handler.normalize_call(value) is None:
        return (
            f"你对工具「{tool_type}」的调用参数不完整或格式不对，系统无法执行，已被忽略"
            f"（你提交的是：{describe_tool_call_for_prompt(value)}）。"
            "请对照该工具所需字段修正后重试，或把 tool_call 设为 null 并直接回复主人。"
        )
    return ""


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
    normalized_call = normalize_tool_call(
        engine,
        tool_call,
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
    )
    if not normalized_call:
        return None

    handlers = engine._resolve_tool_handlers(
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
    )
    handler = handlers.get(str(normalized_call.get("type") or ""))
    if handler is None:
        return None

    enriched_visual_payload = dict(visual_payload or {})
    enriched_visual_payload["_profile_user_id"] = profile_user_id
    enriched_visual_payload["_character_pack_id"] = str(character_pack_id or "")
    if memory_exclude_source_ids:
        enriched_visual_payload["_memory_retrieval_exclude_source_ids"] = list(memory_exclude_source_ids)
    client_mode = ""
    if client_context is not None:
        client_mode = str(getattr(client_context.effective_mode, "value", client_context.effective_mode) or "")
    return handler.execute(
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
