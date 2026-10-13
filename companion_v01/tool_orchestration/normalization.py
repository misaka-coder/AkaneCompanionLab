"""Normalize wire calls while retaining invocation IDs and frozen selections."""

from __future__ import annotations

from typing import Any, Mapping

from ..capability_registry import OPEN_BROWSER_TOOL_SPEC
from ..client_protocol import ClientProtocolContext
from ..tool_invocation import (
    LEGACY_JSON,
    NATIVE_ANTHROPIC,
    NATIVE_OPENAI,
    TOOL_INVOCATION_ID_FIELD,
    TOOL_CAPABILITY_SELECTION_FIELD,
    TOOL_EXECUTION_RECEIPT_FIELD,
    TOOL_MODEL_ARGUMENTS_FIELD,
    TOOL_MODEL_NAME_FIELD,
    TOOL_SOURCE_FIELD,
    ToolInvocation,
    invocation_to_legacy_tool_call,
    legacy_tool_call_to_invocation,
)


def parse_tool_call(value: Any, *, capability_selection: Any = None) -> ToolInvocation | None:
    """Parse the host wire shape without resolving or normalizing a business handler."""
    return legacy_tool_call_to_invocation(value, capability_selection=capability_selection)


def normalize_tool_call(
    engine: Any,
    value: Any,
    *,
    client_context: ClientProtocolContext | None = None,
    profile_user_id: str = "",
    session_id: str = "",
    domain_profile_id: str = "",
    capability_selection: Any = None,
) -> dict[str, Any] | None:
    """Compatibility API for callers that explicitly need normalized dicts."""
    invocation = normalize_tool_invocation(
        engine,
        value,
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
        domain_profile_id=domain_profile_id,
        capability_selection=capability_selection,
    )
    if invocation is None:
        return None
    normalized = invocation_to_legacy_tool_call(invocation, include_metadata=True)
    # Provider-safe aliases (for example a dotted plugin capability projected
    # to an underscore-only OpenAI function name) are wire context, not tool
    # arguments. Preserve the alias across handler normalization so the native
    # follow-up can reproduce the assistant tool_call exactly.
    model_name = str(value.get(TOOL_MODEL_NAME_FIELD) or "").strip()
    if model_name:
        normalized[TOOL_MODEL_NAME_FIELD] = model_name
    model_arguments = value.get(TOOL_MODEL_ARGUMENTS_FIELD)
    if isinstance(model_arguments, Mapping):
        normalized[TOOL_MODEL_ARGUMENTS_FIELD] = dict(model_arguments)
    return normalized


def normalize_tool_invocation(
    engine: Any,
    value: Any,
    *,
    client_context: ClientProtocolContext | None = None,
    profile_user_id: str = "",
    session_id: str = "",
    domain_profile_id: str = "",
    capability_selection: Any = None,
) -> ToolInvocation | None:
    if not isinstance(value, dict):
        return None

    tool_type = str(value.get("type") or "").strip()
    if not tool_type:
        return None
    source = _normalize_invocation_source(value.get(TOOL_SOURCE_FIELD))
    invocation_id = str(value.get(TOOL_INVOCATION_ID_FIELD) or "").strip()
    frozen_selection = capability_selection or value.get(TOOL_CAPABILITY_SELECTION_FIELD)
    # Host scheduling is a wire sidecar, never a business argument. Preserve it
    # through handlers that intentionally return only their normalized fields.
    from ..tool_scheduling import extract_scheduling
    selected = _resolved_handler_for_round(engine, tool_type, frozen_selection)
    if selected is None and frozen_selection is None:
        selected = engine._resolve_tool_handlers(client_context=client_context, profile_user_id=profile_user_id,
            session_id=session_id, domain_profile_id=domain_profile_id).get(tool_type)
    getter = getattr(selected, "tool_spec", None)
    try:
        spec = getter() if callable(getter) else None
    except Exception:
        spec = None
    from ..capability_contracts import ContractBoundHandler
    if not isinstance(selected, ContractBoundHandler) or selected._execution_bound:
        value = extract_scheduling(value, spec=spec, tool_name=tool_type)
    scheduling = {key: value[key] for key in ("_tool_background", "_tool_scheduling_error") if key in value}

    if tool_type == OPEN_BROWSER_TOOL_SPEC.capability_id:
        handler = _resolved_handler_for_round(engine, tool_type, frozen_selection)
        if handler is None:
            return None
        try:
            normalized = handler.normalize_call(value)
        except Exception:
            normalized = None
        if normalized is None:
            return legacy_tool_call_to_invocation(
                value,
                source=source,
                invocation_id=invocation_id,
                capability_selection=frozen_selection,
            )
        receipt = value.get(TOOL_EXECUTION_RECEIPT_FIELD)
        if isinstance(receipt, dict):
            normalized[TOOL_EXECUTION_RECEIPT_FIELD] = dict(receipt)
        normalized.update(scheduling)
        return legacy_tool_call_to_invocation(
            normalized,
            source=source,
            invocation_id=invocation_id,
            capability_selection=frozen_selection,
        )

    # Keep input unchanged until the display wrapper resolves the current
    # implementation. An unavailable target still receives a paired error.
    handler = selected
    if handler is None:
        # Unknown or unavailable names are still genuine model decisions. Keep
        # the invocation so validation can return one ordinary tool result;
        # dropping it here would leave the model waiting for a result forever.
        return legacy_tool_call_to_invocation(
            value,
            source=source,
            invocation_id=invocation_id,
            capability_selection=frozen_selection,
        )
    if tool_type == "computer_use":
        # This contract never rewrites arguments. Defer its one diagnostic
        # validation to admission, where exceptions remain observable and stale
        # contracts are checked first. A failed call still needs a paired result.
        return legacy_tool_call_to_invocation(
            value,
            source=source,
            invocation_id=invocation_id,
            capability_selection=frozen_selection,
        )
    try:
        normalized = handler.normalize_call(value)
    except Exception:
        normalized = None
    if normalized is None:
        # This is still a genuine attempt to call a known tool. Preserve the
        # raw arguments so validation can produce an observable bad_args
        # terminal result instead of dropping the call during normalization.
        return legacy_tool_call_to_invocation(
            value,
            source=source,
            invocation_id=invocation_id,
            capability_selection=frozen_selection,
        )
    receipt = value.get(TOOL_EXECUTION_RECEIPT_FIELD)
    if isinstance(receipt, dict):
        normalized[TOOL_EXECUTION_RECEIPT_FIELD] = dict(receipt)
    normalized.update(scheduling)
    return legacy_tool_call_to_invocation(
        normalized,
        source=source,
        invocation_id=invocation_id,
        capability_selection=frozen_selection,
    )


def _resolved_handler_for_round(engine: Any, tool_type: str, capability_selection: Any) -> Any:
    if capability_selection is not None:
        frozen_handlers = getattr(capability_selection, "resolved_handlers", None)
        if isinstance(frozen_handlers, Mapping):
            return frozen_handlers.get(tool_type)
    return (getattr(engine, "tool_handlers", {}) or {}).get(tool_type)


def _normalize_invocation_source(value: Any) -> str:
    source = str(value or "").strip()
    if source in {NATIVE_OPENAI, NATIVE_ANTHROPIC}:
        return source
    return LEGACY_JSON


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
    existing = parse_tool_call(final_output.get("tool_call"))
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
