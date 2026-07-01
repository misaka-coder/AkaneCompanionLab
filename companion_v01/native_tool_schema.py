from __future__ import annotations

import re
from typing import Any, Mapping

from capcore import CapabilityToolSpec
from capcore_provider_openai import build_openai_chat_tool_set


NATIVE_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
NATIVE_TOOL_DESCRIPTION_MAX_CHARS = 900
NATIVE_TOOL_CAPABILITY_ID_FIELD = "_akane_capability_id"

# Legacy prompt instructions teach the model to hand-build the old tool_call
# JSON envelope (e.g. 格式为 {"type":"...", ...}). On the native channel the
# provider owns the call envelope, so those clauses are noise — and actively
# misleading. We strip them when a handler description falls back to the legacy
# prompt text. build_prompt_instruction itself is left untouched so the legacy
# prompt path keeps emitting the exact same instructions.
_LEGACY_ENVELOPE_MARKERS = ("格式为", "调用格式", "tool_call", '{"type"')


def _strip_legacy_envelope_clauses(text: str) -> str:
    if not text:
        return text
    # Clauses are delimited by the full-width period; keep the delimiter with
    # its clause so reassembly is lossless.
    segments = re.split(r"(?<=。)", text)
    kept = [segment for segment in segments if not any(marker in segment for marker in _LEGACY_ENVELOPE_MARKERS)]
    cleaned = "".join(kept).strip()
    # Never let sanitizing empty out a description; fall back to the original.
    return cleaned or text


def build_openai_native_tool_specs(
    handlers: Mapping[str, Any] | None,
    *,
    allowed_tool_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Build OpenAI Chat Completions tool specs from registered handlers.

    The provider envelope is delegated to capcore-provider-openai. Akane keeps
    an internal capability-id marker on each returned tool so provider-safe
    model names can be mapped back before the existing ToolHandler layer runs.
    """
    if not isinstance(handlers, Mapping):
        return []

    allowed = {str(item or "").strip() for item in allowed_tool_names or set()}
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_name, handler in sorted(handlers.items(), key=lambda item: str(item[0] or "")):
        tool_name = str(getattr(handler, "tool_type", "") or raw_name or "").strip()
        if not tool_name or tool_name in seen:
            continue
        if allowed and tool_name not in allowed:
            continue
        tool = _provider_tool_for_handler(tool_name, handler)
        if tool is None:
            continue
        seen.add(tool_name)
        specs.append(tool)
    return specs


def _provider_tool_for_handler(tool_name: str, handler: Any) -> dict[str, Any] | None:
    description = _metadata_schema_description(handler)
    parameters = _metadata_schema_parameters(handler)
    if not description:
        description = _handler_description(handler, tool_name=tool_name)
    if parameters is None:
        parameters = {
            "type": "object",
            "additionalProperties": True,
        }
    spec = CapabilityToolSpec(
        capability_id=tool_name,
        display_name=tool_name,
        description=description,
        input_schema=parameters,
        risk=_handler_risk(handler),
        confirm="never",
        effects=(),
        visible_in=(),
    )
    try:
        tool_set = build_openai_chat_tool_set(tool_specs=(spec,))
    except Exception:
        return None
    if not tool_set.tools:
        return None
    tool = dict(tool_set.tools[0])
    tool[NATIVE_TOOL_CAPABILITY_ID_FIELD] = tool_name
    return tool


def _handler_description(handler: Any, *, tool_name: str) -> str:
    description = ""
    build_prompt_instruction = getattr(handler, "build_prompt_instruction", None)
    if callable(build_prompt_instruction):
        try:
            description = str(build_prompt_instruction() or "").strip()
        except Exception:
            description = ""
    if not description:
        description = f"Call Akane tool {tool_name}."
    description = " ".join(description.split())
    description = _strip_legacy_envelope_clauses(description)
    return description[:NATIVE_TOOL_DESCRIPTION_MAX_CHARS]


def _metadata_schema_description(handler: Any) -> str:
    schema = _handler_input_schema(handler)
    if not isinstance(schema, Mapping):
        return ""
    description = str(schema.get("description") or schema.get("x_description") or "").strip()
    if not description:
        return ""
    return " ".join(description.split())[:NATIVE_TOOL_DESCRIPTION_MAX_CHARS]


def _metadata_schema_parameters(handler: Any) -> dict[str, Any] | None:
    schema = _handler_input_schema(handler)
    if not isinstance(schema, Mapping):
        return None
    parameters = {
        str(key): value for key, value in dict(schema).items() if str(key) not in {"description", "x_description"}
    }
    if str(parameters.get("type") or "").strip() != "object":
        parameters["type"] = "object"
    return parameters


def _handler_input_schema(handler: Any) -> Mapping[str, Any] | None:
    tool_metadata = getattr(handler, "tool_metadata", None)
    if not callable(tool_metadata):
        return None
    try:
        metadata = tool_metadata()
    except Exception:
        return None
    schema = getattr(metadata, "input_schema", None)
    return schema if isinstance(schema, Mapping) else None


def _handler_risk(handler: Any) -> str:
    tool_metadata = getattr(handler, "tool_metadata", None)
    if not callable(tool_metadata):
        return "low"
    try:
        metadata = tool_metadata()
    except Exception:
        return "low"
    risk = str(getattr(metadata, "risk", "") or "").strip().lower()
    return risk if risk in {"low", "medium", "high"} else "low"
