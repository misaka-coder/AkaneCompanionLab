"""Native tool admission and provider-visible schemas for the selected round."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import config

from ..native_tool_schema import NATIVE_TOOL_CAPABILITY_ID_FIELD, build_openai_native_tool_specs


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


def native_tool_decision_allowlist() -> set[str]:
    return set(_native_tool_decision_allowlist_items())


def _native_tool_decision_allowlist_items() -> list[str]:
    raw = str(getattr(config, "NATIVE_TOOL_DECISION_ALLOWLIST", "*") or "").strip()
    allowed: list[str] = []
    seen: set[str] = set()
    for raw_item in raw.split(","):
        item = raw_item.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        allowed.append(item)
    return allowed or ["*"]


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
    candidate_names = _native_tool_candidate_names(handlers=handlers, allowed_tool_names=allowed_tool_names)
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
    if not isinstance(handlers, Mapping):
        return NativeToolDecisionPlan(
            status="disabled",
            reason="native_tool_handlers_missing",
            tools=[],
            legacy_prompt_exclusions=set(),
        )
    candidate_names = _native_tool_candidate_names(handlers=handlers, allowed_tool_names=allowed_tool_names)
    if allowed_tool_names is not None and not candidate_names:
        return NativeToolDecisionPlan(
            status="disabled",
            reason="native_tool_not_in_capability_selection",
            tools=[],
            legacy_prompt_exclusions=set(),
        )
    if not any(name in handlers for name in candidate_names):
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


def _native_tool_candidate_names(
    *,
    handlers: Mapping[str, Any],
    allowed_tool_names: Iterable[str] | None = None,
) -> list[str]:
    allowlist = _native_tool_decision_allowlist_items()
    allowed_sequence = [str(item or "").strip() for item in (allowed_tool_names or ()) if str(item or "").strip()]
    allowed = set(allowed_sequence)
    if "*" in allowlist:
        # Capability selection remains authoritative: wildcard means all tools
        # already selected for this client/scenario, never every dormant handler.
        candidates = (
            allowed_sequence
            if allowed_tool_names is not None
            else [str(name or "").strip() for name in handlers if str(name or "").strip()]
        )
    else:
        candidates = allowlist if allowed_tool_names is None else [name for name in allowlist if name in allowed]
    for raw_name, handler in handlers.items():
        name = str(raw_name or "").strip()
        if not name or name in candidates:
            continue
        if allowed_tool_names is not None and name not in allowed:
            continue
        if bool(getattr(handler, "policy_accepted_native_tool", False)):
            candidates.append(name)
    return candidates


def _native_tool_schema_for_handler(tool_name: str, handler: Any) -> dict[str, Any] | None:
    # M66-B: web_search uses its canonical ToolSpec via handler.tool_spec() like every other tool.
    normalized_name = str(tool_name or "").strip()
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
        model_name = str(function.get("name") or "").strip()
        capability_id = str(raw.get(NATIVE_TOOL_CAPABILITY_ID_FIELD) or "").strip() or model_name
        if capability_id:
            exclusions.add(capability_id)
    return exclusions
