from __future__ import annotations

import json
from typing import Any

import config

from .client_protocol import ClientProtocolContext
from .tool_runtime import ToolExecutionContext


def max_tool_rounds() -> int:
    raw_value = getattr(config, "MAX_TOOL_ROUNDS", 3)
    try:
        value = int(raw_value)
    except Exception:
        value = 3
    return max(1, min(5, value))


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


def build_multi_tool_followup_context(tool_followups: list[str], *, allow_more: bool) -> str:
    lines: list[str] = ["【本轮工具执行记录】"]
    if tool_followups:
        lines.extend([str(item).strip() for item in tool_followups if str(item).strip()])
    else:
        lines.append("(暂时没有可用的工具结果。)")
    if allow_more:
        lines.append(
            "如果任务还没完成，可以继续在 tool_call 字段调用下一步必要工具；"
            "如果结果已经足够，请将 tool_call 设为 null，并自然回复主人。"
        )
    else:
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
    handler = handlers.get(tool_type)
    if handler is None:
        return None
    return handler.normalize_call(value)


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
    tool_call: dict[str, Any],
    visual_payload: dict[str, Any],
    now_ts: int,
    current_user_source_id: str = "",
    client_context: ClientProtocolContext | None = None,
    memory_exclude_source_ids: list[str] | None = None,
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
    if memory_exclude_source_ids:
        enriched_visual_payload["_memory_retrieval_exclude_source_ids"] = list(memory_exclude_source_ids)
    return handler.execute(
        call=normalized_call,
        context=ToolExecutionContext(
            profile_user_id=profile_user_id,
            session_id=session_id,
            now_ts=now_ts,
            visual_payload=enriched_visual_payload,
            current_user_source_id=current_user_source_id,
        ),
    )
