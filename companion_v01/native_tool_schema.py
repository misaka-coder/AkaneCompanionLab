from __future__ import annotations

from typing import Any, Mapping

from capcore import CapabilityToolSpec
from capcore_provider_openai import build_openai_chat_tool_set


NATIVE_TOOL_CAPABILITY_ID_FIELD = "_akane_capability_id"


def native_tool_model_name_map(native_tools: list[dict[str, Any]] | None) -> dict[str, str]:
    """Map provider-safe function names back to canonical capability ids."""

    mapping: dict[str, str] = {}
    for raw in native_tools or []:
        if not isinstance(raw, dict):
            continue
        function = raw.get("function")
        if not isinstance(function, dict):
            continue
        model_name = str(function.get("name") or "").strip()
        if not model_name:
            continue
        capability_id = str(raw.get(NATIVE_TOOL_CAPABILITY_ID_FIELD) or "").strip() or model_name
        mapping[model_name] = capability_id
    return mapping


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
        tool = _provider_tool_for_handler(handler)
        if tool is None:
            continue
        seen.add(tool_name)
        specs.append(tool)
    return specs


def build_openai_native_tool_from_spec(spec: CapabilityToolSpec) -> dict[str, Any]:
    """Project one canonical ToolSpec without introducing a handler authority."""
    if not isinstance(spec, CapabilityToolSpec):
        raise TypeError("canonical_tool_spec_required")
    # Akane's canonical memory descriptions can exceed the provider package's
    # conservative 900-character default. They are already part of the stable
    # ToolSpec contract, so do not silently truncate their semantics on the
    # native path while the legacy projection sees the complete description.
    description_limit = max(900, len(str(spec.description or "")))
    tool_set = build_openai_chat_tool_set(
        tool_specs=(spec,),
        description_max_chars=description_limit,
    )
    if not tool_set.tools:
        raise ValueError("canonical_tool_spec_not_projectable")
    tool = dict(tool_set.tools[0])
    tool[NATIVE_TOOL_CAPABILITY_ID_FIELD] = spec.capability_id
    return tool


def _provider_tool_for_handler(handler: Any) -> dict[str, Any] | None:
    canonical_spec = _handler_tool_spec(handler)
    if canonical_spec is None:
        return None
    try:
        return build_openai_native_tool_from_spec(canonical_spec)
    except Exception:
        return None


def _handler_tool_spec(handler: Any) -> CapabilityToolSpec | None:
    getter = getattr(handler, "tool_spec", None)
    if not callable(getter):
        return None
    try:
        spec = getter()
    except Exception:
        return None
    return spec if isinstance(spec, CapabilityToolSpec) else None
