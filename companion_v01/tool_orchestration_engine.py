from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import config

from .client_protocol import ClientProtocolContext
from .client_protocol import ClientMode
from .tool_invocation import LEGACY_JSON
from .tool_invocation import NATIVE_ANTHROPIC
from .tool_invocation import NATIVE_OPENAI
from .tool_invocation import TOOL_INVOCATION_ID_FIELD
from .tool_invocation import TOOL_CAPABILITY_SELECTION_FIELD
from .tool_invocation import TOOL_EXECUTION_RECEIPT_FIELD
from .tool_invocation import TOOL_MODEL_NAME_FIELD
from .tool_invocation import TOOL_SOURCE_FIELD
from .tool_invocation import ToolInvocation
from .tool_invocation import ToolResultEnvelope
from .tool_invocation import ValidationResult
from .tool_invocation import invocation_to_legacy_tool_call
from .tool_invocation import legacy_tool_call_to_invocation
from .native_tool_schema import NATIVE_TOOL_CAPABILITY_ID_FIELD, build_openai_native_tool_specs
from .tool_runtime import ToolExecutionContext, ToolExecutionResult, ToolFollowupEnvelope
from .capability_registry import ExecutorBroker, OPEN_BROWSER_TOOL_SPEC
from .desktop_satellite_specs import desktop_satellite_spec
from .execution_specs import EXEC_TOOL_SPEC_BY_ID


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


_EXPLICIT_DELIVERY_TOOL_TYPES = frozenset(
    {
        "compose_file",
        "revise_generated_file",
        "apply_style_to_existing_file",
        "convert_media_file",
        "separate_audio_stems",
        "cover_song",
        "clean_voice_track",
        "transcribe_media",
        "prepare_voice_dataset",
    }
)

_DIRECT_MEDIA_TOOL_TYPES = frozenset(
    {
        "convert_media_file",
        "separate_audio_stems",
        "cover_song",
        "clean_voice_track",
        "transcribe_media",
        "prepare_voice_dataset",
    }
)

def defer_generated_artifact_delivery(call: dict[str, Any]) -> dict[str, Any]:
    """Keep artifact creation and user delivery as two observable native tool rounds."""

    normalized = dict(call)
    tool_type = str(normalized.get("type") or "").strip()
    if tool_type not in _EXPLICIT_DELIVERY_TOOL_TYPES:
        return normalized
    if "send_to_user" in normalized:
        normalized["send_to_user"] = False
    if tool_type == "cover_song":
        normalized["delivery"] = "none"
    return normalized


def _qq_media_delegation_is_blocked(
    value: Mapping[str, Any],
    *,
    client_context: ClientProtocolContext | None,
) -> bool:
    if client_context is None or client_context.effective_mode != ClientMode.QQ_TEXT:
        return False
    if str(value.get("type") or "").strip() != "delegate_task":
        return False
    agent = str(value.get("agent") or "").strip().lower()
    if agent == "media_agent":
        return True
    text = " ".join(
        str(value.get(key) or "").strip().lower()
        for key in ("brief", "goal", "raw_request")
    )
    return any(tool_name in text for tool_name in _DIRECT_MEDIA_TOOL_TYPES)


def _bounded_int(raw_value: Any, *, default: int, lower: int = 1, upper: int = 16) -> int:
    try:
        value = int(raw_value)
    except Exception:
        value = default
    return max(lower, min(upper, value))


def max_tool_rounds() -> int:
    return _bounded_int(getattr(config, "MAX_TOOL_ROUNDS", 3), default=3, lower=1, upper=5)


def max_tool_emergency_rounds(*, current_budget: int = 0) -> int:
    """Return the universal fail-safe ceiling, never a normal workflow budget."""

    soft_budget = _bounded_int(current_budget, default=max_tool_rounds(), lower=1, upper=32)
    configured = _bounded_int(
        getattr(config, "MAX_TOOL_EMERGENCY_ROUNDS", 16),
        default=16,
        lower=1,
        upper=32,
    )
    return max(soft_budget, configured)


def extend_tool_round_budget_for_progress(
    *,
    current_budget: int,
    emergency_limit: int,
    tool_round_index: int,
    tool_calls: Iterable[Mapping[str, Any]],
    seen_signatures: set[str],
) -> tuple[int, bool]:
    """Extend a soft budget only for at least one not-yet-executed call.

    Exact-repeat suppression remains authoritative elsewhere.  This helper
    only distinguishes a progressing chain from a hard emergency stop; it
    does not encode tool names, user wording, or workflow-specific steps.
    """

    budget = max(1, int(current_budget or 1))
    emergency = max(budget, int(emergency_limit or budget))
    round_index = max(0, int(tool_round_index or 0))
    calls = [dict(call) for call in tool_calls if isinstance(call, Mapping)]
    if not calls or round_index < budget:
        return budget, False
    has_new_call = any(tool_call_signature(call) not in seen_signatures for call in calls)
    if not has_new_call:
        return budget, False
    if round_index >= emergency:
        return budget, True
    return max(budget, round_index + 1), False


def _configured_family_budget(family: str, *, fallback: int) -> int:
    clean_family = str(family or "").strip()
    if clean_family == "web_research":
        return _bounded_int(getattr(config, "MAX_WEB_RESEARCH_TOOL_ROUNDS", fallback), default=fallback)
    if clean_family == "browser_control":
        return _bounded_int(getattr(config, "MAX_BROWSER_TOOL_ROUNDS", fallback), default=fallback)
    if clean_family in {"finance_read", "finance_artifact"}:
        hard_limit = _bounded_int(
            getattr(config, "FINANCE_TOOL_ROUND_HARD_LIMIT", 16),
            default=16,
            upper=16,
        )
        return _bounded_int(
            getattr(config, "FINANCE_TOOL_ROUND_BUDGET", fallback),
            default=fallback,
            upper=hard_limit,
        )
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
    base_budget = (
        max_tool_rounds() if current_budget is None else _bounded_int(current_budget, default=max_tool_rounds())
    )
    tool_type = str((tool_call or {}).get("type") or "").strip()
    if not tool_type:
        return base_budget
    handler = handlers.get(tool_type) if isinstance(handlers, Mapping) else None
    metadata = tool_metadata_dict(handler, tool_type=tool_type)
    fallback = max(base_budget, int(metadata.get("default_round_budget") or base_budget))
    family_budget = _configured_family_budget(str(metadata.get("family") or ""), fallback=fallback)
    return max(base_budget, family_budget)


def tool_call_signature(tool_call: dict[str, Any]) -> str:
    payload = {str(key): value for key, value in dict(tool_call or {}).items() if not str(key).startswith("_tool_")}
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


def append_structured_artifact_receipts(
    followup_context: Any,
    *,
    stream_events: Iterable[Mapping[str, Any]] | None,
) -> str:
    """Ensure newly created artifacts are visible in the tool result once.

    Tool implementations may describe their result naturally, but managed
    artifact bridges are also allowed to report only a structured
    ``generated_file_ready`` event.  The model still needs the stable handle
    in the very next tool round so it can send or reuse the file directly.
    This projection is based only on the result event contract; it never
    guesses from user wording and never injects a persistent workspace list.
    """

    text = str(followup_context or "").strip()
    receipts: list[str] = []
    seen_handles: set[str] = set()
    for raw_event in stream_events or ():
        if not isinstance(raw_event, Mapping):
            continue
        if str(raw_event.get("type") or "").strip() != "generated_file_ready":
            continue
        generated = raw_event.get("generated_file")
        if not isinstance(generated, Mapping):
            continue
        handle = " ".join(str(generated.get("generated_handle") or "").split())[:120]
        if not handle or handle in seen_handles or handle in text:
            continue
        seen_handles.add(handle)
        title = " ".join(str(generated.get("output_title") or "").split())[:160]
        output_format = " ".join(
            str(generated.get("output_format") or generated.get("file_ext") or "").split()
        )[:40]
        attributes = [f"handle={handle}"]
        if title:
            attributes.append(f"title={title}")
        if output_format:
            attributes.append(f"format={output_format}")
        receipts.append("- generated_file " + " ".join(attributes))
    if not receipts:
        return text
    receipt_block = "\n".join(
        [
            "【本轮新生成文件】",
            *receipts,
            "这些 handle 已可直接交给后续工具；如果用户已明确要收到文件，可直接调用 send_file，无需先查询生成文件工作台。",
        ]
    )
    return "\n\n".join(part for part in (text, receipt_block) if part)


def shape_tool_followup(
    followup_context: Any,
    *,
    tool_type: str,
    max_chars: int | None = None,
) -> str:
    """Discipline the tool result text fed back to the model (Claude Code-aligned).

    Rules applied at the single point where a tool result becomes model-facing
    feedback:
    - empty-but-successful -> stable placeholder, never an empty tool result
      (mirrors Claude Code's empty tool_result guard; an empty result tail can
      make some models end the turn with no output).
    - producer-bounded envelope -> preserve the producer's complete logical
      units and continuation cursor instead of applying another character cut.
    - otherwise over-size -> truncate at a newline boundary with an honest marker that
      reports the full size and how much was omitted, so a huge result can't
      blow up the next round's context AND the model can gauge how far to narrow
      its next call (showing chars-only, without the total, left it guessing).

    Only the tool's own text is bounded here; no paths are introduced. A
    producer-bounded result is trusted only for sizing/continuation ownership;
    storage-boundary secret/path sanitization still runs independently.
    """
    tool_name = str(tool_type or "tool").strip() or "tool"
    envelope = followup_context if isinstance(followup_context, ToolFollowupEnvelope) else None
    text = str(envelope.content if envelope is not None else followup_context or "").strip()
    if not text:
        return f"（{tool_name} 执行成功，但没有返回可展示的内容。）"
    if envelope is not None and envelope.producer_bounded:
        if not envelope.complete and not dict(envelope.continuation or {}):
            text = (
                f"{text}\n"
                "（该工具声明结果尚未完整，但没有提供可执行 continuation；"
                "这次读取链路不完整，请勿假装已经读完。）"
            )
        else:
            return text
    limit = (
        int(max_chars)
        if max_chars
        else int(
            getattr(config, "MAX_TOOL_FOLLOWUP_CHARS", DEFAULT_MAX_TOOL_FOLLOWUP_CHARS)
            or DEFAULT_MAX_TOOL_FOLLOWUP_CHARS
        )
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
            "如果任务还没完成，可以继续使用本轮实际提供的工具入口：请求中直接附带的工具直接调用，"
            "只有“兼容 JSON 工具”清单里的工具才写入 JSON tool_call；"
            "如果用户已经明确交代了下一步，且下一步仍在安全边界和授权范围内，不要为了确认而停下询问；"
            "每条结果只证明其中明确给出的状态、数据和产物。结果已经足够、下一步不明确、或遇到真实阻塞时，"
            "停止调用、保持 tool_call 为 null，并自然回复主人；证据缺失的部分要明确说明，不要补猜。"
        )
    else:
        if str(stop_reason or "").strip() == "tool_budget_exhausted":
            lines.append(
                "本轮工具预算已经用完；工具阶段到此结束。请基于已有证据立即完成面向用户的答案，"
                "回答可回答的部分，并明确仍缺少的证据、数据截止时间和结论置信度。"
            )
        elif str(stop_reason or "").strip() == "tool_unavailable":
            lines.append(
                "刚才的工具返回不可用或失败状态；工具阶段到此结束。请使用此前已经取得的可靠证据回答，"
                "并明确这次未能取得的部分；没有证据的内容不要编造。"
            )
        elif str(stop_reason or "").strip() == "finance_no_progress":
            lines.append(
                "连续金融查询没有带来新证据，系统已触发无进展保险丝。请停止查询，基于现有来源、as_of 与程序计算"
                "给出当前最可靠的完整答复，并明确证据缺口和置信度。"
            )
        lines.append(
            "本轮不要再调用工具（无论原生或兼容），请将 tool_call 设为 null，并立即输出完整、可交付的最终回复。"
            "不得只回复“仍在处理”“还没完成”“需要继续查询”或类似占位语；即使证据不足，也要给出当前可支持的结论、"
            "限制与下一步建议。"
        )
    return "\n\n".join(lines)


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
    if _qq_media_delegation_is_blocked(value, client_context=client_context):
        return None
    source = _normalize_invocation_source(value.get(TOOL_SOURCE_FIELD))
    invocation_id = str(value.get(TOOL_INVOCATION_ID_FIELD) or "").strip()
    frozen_selection = capability_selection or value.get(TOOL_CAPABILITY_SELECTION_FIELD)

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
        return legacy_tool_call_to_invocation(
            normalized,
            source=source,
            invocation_id=invocation_id,
            capability_selection=frozen_selection,
        )

    # A frozen selection contains both the tools that were executable when the
    # turn started and the stable schema tools shown to the provider. Normalize
    # against that exact schema snapshot. Validation below still checks whether
    # the tool was executable, so an advertised-but-temporarily-unavailable
    # capability becomes a structured tool error instead of disappearing.
    handler = (
        _resolved_handler_for_round(engine, tool_type, frozen_selection)
        if frozen_selection is not None
        else None
    )
    if handler is None and frozen_selection is None:
        handlers = engine._resolve_tool_handlers(
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile_id,
        )
        handler = handlers.get(tool_type)
    if handler is None:
        return None
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
    normalized = defer_generated_artifact_delivery(normalized)
    receipt = value.get(TOOL_EXECUTION_RECEIPT_FIELD)
    if isinstance(receipt, dict):
        normalized[TOOL_EXECUTION_RECEIPT_FIELD] = dict(receipt)
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
    candidate_names = _native_tool_candidate_names(handlers=handlers, allowed_tool_names=allowed_tool_names)
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


def _native_tool_candidate_names(
    *,
    handlers: Mapping[str, Any],
    allowed_tool_names: Iterable[str] | None = None,
) -> list[str]:
    allowlist = _native_tool_decision_allowlist_items()
    allowed = {str(item or "").strip() for item in (allowed_tool_names or ()) if str(item or "").strip()}
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


def validate_tool_invocation(
    engine: Any,
    invocation: ToolInvocation | None,
    *,
    client_context: ClientProtocolContext | None = None,
    profile_user_id: str = "",
    session_id: str = "",
    raw_tool_call: Any = None,
    domain_profile_id: str = "",
) -> ValidationResult:
    if invocation is None:
        return ValidationResult.success()

    tool_type = str(invocation.name or "").strip()
    if not tool_type:
        return ValidationResult.fail("missing_tool_type", "工具调用缺少 type 字段。")
    candidate_call = raw_tool_call if isinstance(raw_tool_call, dict) else invocation_to_legacy_tool_call(invocation)
    if _qq_media_delegation_is_blocked(candidate_call, client_context=client_context):
        return ValidationResult.fail(
            "media_delegation_disallowed",
            (
                "QQ 媒体处理不能委派给后台工坊。请直接调用当前可用的媒体工具"
                "（例如 separate_audio_stems、cover_song、transcribe_media 或 convert_media_file）；"
                "工具返回 gen_ 成果句柄后，再调用 send_file 交付。不要声称后台正在运行。"
            ),
        )

    satellite_spec = desktop_satellite_spec(tool_type)
    if satellite_spec is not None:
        handler = _resolved_handler_for_round(engine, tool_type, invocation.capability_selection)
        if handler is None:
            return ValidationResult.fail("unknown_tool", "当前桌面执行器没有提供这项能力。")
        try:
            normalized_candidate = handler.normalize_call(candidate_call)
        except Exception:
            normalized_candidate = None
        if normalized_candidate is None:
            return ValidationResult.fail("bad_args", "本地能力的参数不符合当前执行器契约。")
        if not invocation.execution_receipt:
            return ValidationResult.fail(
                "not_available",
                "用户绑定的桌面执行器当前不在线或没有提供这项能力，本次没有执行任何本地动作。",
            )
        return ValidationResult.success()

    if tool_type == OPEN_BROWSER_TOOL_SPEC.capability_id:
        handler = _resolved_handler_for_round(engine, tool_type, invocation.capability_selection)
        if handler is None:
            return ValidationResult.fail("unknown_tool", "当前没有可用的桌面网页打开工具。")
        try:
            normalized_candidate = handler.normalize_call(candidate_call)
        except Exception:
            normalized_candidate = None
        if normalized_candidate is None:
            return ValidationResult.fail("bad_args", "打开网页的 URL 或参数不符合公开网页安全约束。")
        if not invocation.execution_receipt:
            return ValidationResult.fail(
                "not_available",
                "用户绑定的桌面执行器当前不在线或没有提供网页打开能力，本次没有执行任何本地动作。",
            )
        return ValidationResult.success()

    frozen_selection = invocation.capability_selection
    if frozen_selection is not None:
        schema_names = {
            str(name or "").strip()
            for name in getattr(frozen_selection, "schema_tool_names", ()) or ()
            if str(name or "").strip()
        }
        executable_names = {
            str(name or "").strip()
            for name in getattr(frozen_selection, "tool_names", ()) or ()
            if str(name or "").strip()
        }
        if tool_type in schema_names and tool_type not in executable_names:
            reason = ""
            recovery = ""
            for disclosure in getattr(frozen_selection, "disclosures", ()) or ():
                if tool_type not in {
                    str(name or "").strip()
                    for name in getattr(disclosure, "tool_names", ()) or ()
                }:
                    continue
                if str(getattr(disclosure, "state", "") or "").strip().lower() != "unavailable":
                    continue
                reason = str(getattr(disclosure, "reason", "") or "").strip()
                recovery = str(
                    getattr(disclosure, "activation", "")
                    or getattr(disclosure, "recovery_hint", "")
                    or ""
                ).strip()
                break
            details = reason or "这项能力依赖的执行器或外部服务当前没有通过可用性检查。"
            recovery_text = f"恢复方式：{recovery}" if recovery else "可以稍后重试，或改用当前可用能力。"
            return ValidationResult.fail(
                "not_available",
                (
                    f"工具「{tool_type}」的调用格式有效，但本轮执行器不可用：{details}"
                    f"{recovery_text} 这次没有完成，请如实告诉用户，不要假装已经执行。"
                ),
            )

    handlers = engine._resolve_tool_handlers(
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
        domain_profile_id=domain_profile_id,
        capability_selection=invocation.capability_selection,
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
                "请按本轮为该工具实际提供的通道改用其中一个工具；如果不再需要工具，"
                "保持兼容 tool_call 为 null 并直接回复主人。不要再调用不存在的工具。"
            ),
        )

    try:
        normalized_candidate = handler.normalize_call(candidate_call)
    except Exception:
        normalized_candidate = None
    if normalized_candidate is None:
        return ValidationResult.fail(
            "bad_args",
            (
                f"你对工具「{tool_type}」的调用参数不完整或格式不对，系统无法执行，已被忽略"
                f"（你提交的是：{describe_tool_call_for_prompt(candidate_call)}）。"
                "请对照本轮 schema 修正参数，并通过该工具本轮实际提供的通道重试；"
                "如果不再需要工具，保持兼容 tool_call 为 null 并直接回复主人。"
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
    domain_profile_id: str = "",
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
        domain_profile_id=domain_profile_id,
        capability_selection=value.get(TOOL_CAPABILITY_SELECTION_FIELD),
    )
    return validate_tool_invocation(
        engine,
        legacy_tool_call_to_invocation(value),
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
        raw_tool_call=value,
        domain_profile_id=domain_profile_id,
    )


def classify_tool_call_rejection(
    engine: Any,
    value: Any,
    *,
    client_context: ClientProtocolContext | None = None,
    profile_user_id: str = "",
    session_id: str = "",
    domain_profile_id: str = "",
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
        domain_profile_id=domain_profile_id,
    )
    if validation.ok:
        return ""
    return validation.message


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
    domain_profile_id: str = "",
) -> Any | None:
    invocation = normalize_tool_invocation(
        engine,
        tool_call,
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
        domain_profile_id=domain_profile_id,
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
        domain_profile_id=domain_profile_id,
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
    domain_profile_id: str = "",
) -> tuple[Any | None, ToolResultEnvelope]:
    validation = validate_tool_invocation(
        engine,
        invocation,
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
        domain_profile_id=domain_profile_id,
    )
    if not validation.ok:
        envelope = validation_result_to_envelope(invocation=invocation, validation=validation)
        status = "unavailable" if validation.code in {"not_available", "unknown_tool"} else "rejected"
        event = {
            "type": "capability_execution_result",
            "tool_type": invocation.name,
            "status": status,
            "reason": str(validation.code or "validation_failed"),
        }
        return (
            ToolExecutionResult(
                tool_type=invocation.name,
                stream_events=[event],
                followup_context=envelope.model_feedback,
                state_updates={
                    "capability_execution": {
                        "tool_type": invocation.name,
                        "status": status,
                        "reason": str(validation.code or "validation_failed"),
                    }
                },
            ),
            ToolResultEnvelope(
                invocation_id=envelope.invocation_id,
                status=envelope.status,
                model_feedback=envelope.model_feedback,
                data=dict(envelope.data or {}),
                events=[event],
            ),
        )

    normalized_call = invocation_to_legacy_tool_call(invocation)
    if invocation.name == OPEN_BROWSER_TOOL_SPEC.capability_id:
        return _execute_open_browser_with_broker(
            engine,
            invocation=invocation,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )
    satellite_spec = desktop_satellite_spec(invocation.name)
    if satellite_spec is not None:
        return _execute_satellite_with_broker(
            engine,
            spec=satellite_spec,
            invocation=invocation,
            profile_user_id=profile_user_id,
            session_id=session_id,
            client_context=client_context,
            request_context=request_context,
        )
    handlers = engine._resolve_tool_handlers(
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
        domain_profile_id=domain_profile_id,
        capability_selection=invocation.capability_selection,
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
    execution_context = ToolExecutionContext(
            profile_user_id=profile_user_id,
            session_id=session_id,
            now_ts=now_ts,
            visual_payload=enriched_visual_payload,
            character_pack_id=str(character_pack_id or ""),
            current_user_source_id=current_user_source_id,
            client_mode=client_mode,
            request_context=dict(request_context or {}),
        )
    broker = getattr(engine, "executor_broker", None)
    if broker is None:
        broker = ExecutorBroker(None)
        try:
            setattr(engine, "executor_broker", broker)
        except Exception:
            pass
    broker_result = broker.execute_server_local(
        tool_id=invocation.name,
        invocation_id=invocation.id,
        dispatch=lambda: handler.execute(
            call=normalized_call,
            context=execution_context,
        ),
        ledger_scope=f"{profile_user_id}\x1f{session_id}",
        request_data={"arguments": normalized_call},
    )
    result = broker_result.result
    if broker_result.status != "succeeded" or result is None:
        reason = str(broker_result.reason or broker_result.status or "server_local_execution_failed")
        feedback = (
            f"<tool_use_error>工具执行没有得到可确认的结果（{reason}）。"
            "请明确说明这次没有完成，不要重试可能产生重复副作用的动作。</tool_use_error>"
        )
        event = {
            "type": "capability_execution_result",
            "tool_type": invocation.name,
            "status": broker_result.status,
            "reason": reason,
        }
        failure_result = ToolExecutionResult(
            tool_type=invocation.name,
            stream_events=[event],
            followup_context=feedback,
            state_updates={
                "capability_execution": {
                    "tool_type": invocation.name,
                    "status": broker_result.status,
                    "reason": reason,
                }
            },
        )
        return failure_result, ToolResultEnvelope(
            invocation_id=invocation.id,
            status="error",
            model_feedback=feedback,
            data={"code": reason, "tool": invocation.name, "status": broker_result.status},
            events=[event],
        )
    return result, _final_exec_envelope(invocation=invocation, result=result)


def _final_exec_envelope(
    *,
    invocation: ToolInvocation,
    result: ToolExecutionResult,
) -> ToolResultEnvelope:
    """Build the execution envelope with an honest status mapping.

    The generic builder labels every non-None result ``ok``; for the execution
    tools the command's own outcome (failed / timed_out / cancelled /
    execution_unknown) and the approval / unavailable cases must surface on the
    envelope so nothing downstream mistakes a failed command for success.
    """
    base = tool_execution_result_to_envelope(invocation=invocation, result=result)
    if str(invocation.name or "").strip() not in EXEC_TOOL_SPEC_BY_ID:
        return base
    status = "ok"
    for event in result.stream_events:
        if not isinstance(event, Mapping):
            continue
        event_type = str(event.get("type") or "").strip()
        if event_type == "capability_approval_required":
            status = "ask"
            break
        if event_type == "capability_execution_result":
            inner = str(event.get("status") or "").strip()
            if inner == "unavailable":
                status = "unavailable"
            elif inner in {"completed", "running"}:
                status = "ok"
            elif str(invocation.name or "").strip() == "exec_cancel" and inner in {"cancelled", "already_ended"}:
                status = "ok"
            else:
                status = "error"
            break
    return ToolResultEnvelope(
        invocation_id=base.invocation_id,
        status=status,
        model_feedback=base.model_feedback,
        data=dict(base.data or {}),
        events=list(base.events or []),
    )


def _execute_open_browser_with_broker(
    engine: Any,
    *,
    invocation: ToolInvocation,
    profile_user_id: str,
    session_id: str,
) -> tuple[ToolExecutionResult, ToolResultEnvelope]:
    broker = getattr(engine, "executor_broker", None)
    if broker is None:
        broker_result = None
    else:
        broker_result = broker.execute(
            spec=OPEN_BROWSER_TOOL_SPEC,
            receipt_value=invocation.execution_receipt,
            invocation_id=invocation.id,
            arguments=invocation.arguments,
            ledger_scope=f"{profile_user_id}\x1f{session_id}",
        )
    status = str(getattr(broker_result, "status", "unavailable_before_dispatch") or "").strip()
    reason = str(getattr(broker_result, "reason", "executor_broker_unavailable") or "").strip()
    model_feedback = str(getattr(broker_result, "model_feedback", "") or "").strip()
    if not model_feedback:
        model_feedback = (
            "当前没有可用的桌面执行器，请直接说明这次没有打开网页。"
            if status != "succeeded"
            else "已在用户绑定的电脑上真实打开公开网页；不要声称读取了页面内容。"
        )
    event = {
        "type": "capability_execution_result",
        "tool_type": OPEN_BROWSER_TOOL_SPEC.capability_id,
        "status": status,
    }
    if reason:
        event["reason"] = reason
    result = ToolExecutionResult(
        tool_type=OPEN_BROWSER_TOOL_SPEC.capability_id,
        stream_events=[event],
        followup_context=model_feedback,
        state_updates={
            "capability_execution": {
                "tool_type": OPEN_BROWSER_TOOL_SPEC.capability_id,
                "status": status,
                "reason": reason,
            }
        },
    )
    envelope = ToolResultEnvelope(
        invocation_id=invocation.id,
        status="ok" if status == "succeeded" else "error",
        model_feedback=model_feedback,
        data={
            "code": reason or status,
            "tool": OPEN_BROWSER_TOOL_SPEC.capability_id,
            "status": status,
        },
        events=[event],
    )
    return result, envelope


def _satellite_channel_rejection(
    spec: Any,
    invocation: ToolInvocation,
    request_context: dict[str, Any] | None,
) -> tuple[ToolExecutionResult, ToolResultEnvelope] | None:
    """Reject device access outside the configured owner's QQ private chat.

    The QQ delivery context marks group messages with ``is_group``; device
    control must only run for the owner's private chat (or the desktop pet).
    Non-QQ clients have no delivery context and pass. QQ requests fail closed
    unless they are a private message from the configured owner account.
    """
    delivery = request_context.get("qq_delivery_context") if isinstance(request_context, dict) else None
    if not isinstance(delivery, dict):
        return None
    is_group = bool(delivery.get("is_group"))
    owner_qq = str(getattr(config, "MASTER_QQ", "") or "").strip()
    sender_qq = str(delivery.get("user_id") or "").strip()
    if not is_group and owner_qq.isdigit() and sender_qq == owner_qq:
        return None
    tool_id = spec.capability_id
    reason = "device_action_requires_owner_private_chat"
    event = {
        "type": "capability_execution_result",
        "tool_type": tool_id,
        "status": "blocked",
        "reason": reason,
    }
    feedback = (
        "当前是群聊消息，不能读取或操控你的电脑；请让主人在私聊里提出后再执行，本次没有执行任何操作。"
        if is_group
        else "这条私聊消息不是来自已配置的主人账号，不能读取或操控绑定电脑；本次没有执行任何操作。"
    )
    result = ToolExecutionResult(
        tool_type=tool_id,
        stream_events=[event],
        followup_context=feedback,
        state_updates={
            "capability_execution": {
                "tool_type": tool_id,
                "status": "blocked",
                "reason": reason,
            }
        },
    )
    envelope = ToolResultEnvelope(
        invocation_id=invocation.id,
        status="error",
        model_feedback=feedback,
        data={"code": reason, "tool": tool_id, "status": "blocked"},
        events=[event],
    )
    return result, envelope


def _satellite_permission_gate(
    engine: Any,
    *,
    spec: Any,
    invocation: ToolInvocation,
    profile_user_id: str,
    session_id: str,
    client_context: ClientProtocolContext | None,
) -> tuple[ToolExecutionResult, ToolResultEnvelope] | None:
    """Return an ask/blocked result for high-risk satellite specs, else None.

    Only high-risk satellite specs (currently ``system_process_terminate``) are
    gated. The grant is bound to capability/action plus a request fingerprint of
    the exact arguments and redeemed through the shared engine approval store,
    so an approved termination never silently covers a different pid.
    """
    if str(getattr(spec, "risk", "") or "").strip().lower() != "high":
        return None
    from .capability_approval import build_approval_request_fingerprint
    from .capcore_runtime import manual_permission_request, resolve_permission_for_profile

    client_mode = ""
    if client_context is not None:
        client_mode = str(getattr(client_context.effective_mode, "value", client_context.effective_mode) or "")
    context = ToolExecutionContext(
        profile_user_id=profile_user_id,
        session_id=session_id,
        now_ts=0,
        visual_payload={},
        client_mode=client_mode,
    )
    arguments = dict(invocation.arguments or {})
    request = manual_permission_request(
        context=context,
        required=True,
        capability_id=spec.capability_id,
        display_name=str(getattr(spec, "display_name", "") or spec.capability_id),
        risk="high",
        confirm="always",
        effects=tuple(getattr(spec, "effects", ()) or ()),
        reason="high_risk_satellite_requires_confirmation",
        args_preview=arguments,
    )
    base_dir = getattr(engine, "capability_config_base_dir", None) or getattr(config, "DATA_DIR", "users_data")
    decision = resolve_permission_for_profile(request, base_dir=base_dir, profile_user_id=profile_user_id)
    if decision.allowed:
        return None
    if not decision.requires_user_decision:
        return _satellite_blocked_result(spec, invocation, str(decision.reason or "capability_disabled_by_policy"))

    fingerprint = build_approval_request_fingerprint(arguments)
    receipt = invocation.execution_receipt if isinstance(invocation.execution_receipt, Mapping) else {}
    device_id = str(receipt.get("instance_id") or "").strip()
    if not device_id:
        # No live receipt means no device action can be dispatched. Let the
        # broker return its structured unavailable result without bothering
        # the user with an approval request that cannot be executed.
        return None
    approval_store = getattr(engine, "approval_store", None)
    if approval_store is not None:
        grant = approval_store.resolve_grant(
            profile_user_id=profile_user_id,
            session_id=session_id,
            capability_id=spec.capability_id,
            action_id=spec.capability_id,
            resource="",
            device=device_id,
            fingerprint=fingerprint,
        )
        if grant is not None:
            return None
        request_id = _create_satellite_approval_request(
            approval_store,
            spec=spec,
            decision=decision,
            fingerprint=fingerprint,
            device_id=device_id,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )
        return _satellite_ask_result(spec, invocation, decision, request_id=request_id, fingerprint=fingerprint)
    return _satellite_ask_result(spec, invocation, decision, request_id="", fingerprint=fingerprint)


def _create_satellite_approval_request(
    approval_store: Any,
    *,
    spec: Any,
    decision: Any,
    fingerprint: str,
    device_id: str,
    profile_user_id: str,
    session_id: str,
) -> str:
    preview = dict(getattr(getattr(decision, "request", None), "args_preview", None) or {})
    result = approval_store.create_request(
        profile_user_id=profile_user_id,
        session_id=session_id,
        payload={
            "capabilityId": spec.capability_id,
            "actionId": spec.capability_id,
            "risk": "high",
            "approvalMode": "ask_each_time",
            "title": f"{getattr(spec, 'display_name', '') or spec.capability_id}需要确认",
            "summary": f"Akane 想在你的电脑上执行{getattr(spec, 'display_name', '') or spec.capability_id}。",
            "approvalReason": "high_risk_satellite_requires_confirmation",
            "payloadPreview": preview,
            "requestFingerprint": fingerprint,
            "deviceId": device_id,
        },
    )
    if not result.get("ok"):
        return ""
    return str(result.get("requestId") or "")


def _satellite_ask_result(
    spec: Any,
    invocation: ToolInvocation,
    decision: Any,
    *,
    request_id: str,
    fingerprint: str,
) -> tuple[ToolExecutionResult, ToolResultEnvelope]:
    from .capcore_runtime import approval_required_event

    tool_id = spec.capability_id
    event = approval_required_event(
        capability_id=tool_id,
        action_id=tool_id,
        title=f"{getattr(spec, 'display_name', '') or tool_id}需要确认",
        summary=f"Akane 想在你的电脑上执行{getattr(spec, 'display_name', '') or tool_id}。",
        client_mode="",
        decision=decision,
    )
    if request_id:
        event["requestId"] = request_id
    if fingerprint:
        event["requestFingerprint"] = fingerprint
    feedback = (
        f"这个{getattr(spec, 'display_name', '') or tool_id}需要用户确认后才能执行；"
        "请自然说明需要用户在能力审批中允许后再执行，不要声称已经完成。"
    )
    result = ToolExecutionResult(
        tool_type=tool_id,
        stream_events=[event],
        followup_context=feedback,
        state_updates={
            "capability_execution": {
                "tool_type": tool_id,
                "status": "approval_required",
                "reason": "requires_user_decision",
            }
        },
    )
    envelope = ToolResultEnvelope(
        invocation_id=invocation.id,
        status="ask",
        model_feedback=feedback,
        data={"tool": tool_id, "status": "approval_required"},
        events=[event],
    )
    return result, envelope


def _satellite_blocked_result(
    spec: Any,
    invocation: ToolInvocation,
    reason: str,
) -> tuple[ToolExecutionResult, ToolResultEnvelope]:
    tool_id = spec.capability_id
    clean_reason = str(reason or "capability_disabled_by_policy")
    event = {
        "type": "capability_execution_result",
        "tool_type": tool_id,
        "status": "blocked",
        "reason": clean_reason,
    }
    feedback = f"{getattr(spec, 'display_name', '') or tool_id}已被当前能力策略阻止（{clean_reason}）。请自然说明无法执行，不要假装已经完成。"
    result = ToolExecutionResult(
        tool_type=tool_id,
        stream_events=[event],
        followup_context=feedback,
        state_updates={
            "capability_execution": {
                "tool_type": tool_id,
                "status": "blocked",
                "reason": clean_reason,
            }
        },
    )
    envelope = ToolResultEnvelope(
        invocation_id=invocation.id,
        status="error",
        model_feedback=feedback,
        data={"code": clean_reason, "tool": tool_id, "status": "blocked"},
        events=[event],
    )
    return result, envelope


def _execute_satellite_with_broker(
    engine: Any,
    *,
    spec: Any,
    invocation: ToolInvocation,
    profile_user_id: str,
    session_id: str,
    client_context: ClientProtocolContext | None = None,
    request_context: dict[str, Any] | None = None,
) -> tuple[ToolExecutionResult, ToolResultEnvelope]:
    rejected = _satellite_channel_rejection(spec, invocation, request_context)
    if rejected is not None:
        return rejected
    gated = _satellite_permission_gate(
        engine,
        spec=spec,
        invocation=invocation,
        profile_user_id=profile_user_id,
        session_id=session_id,
        client_context=client_context,
    )
    if gated is not None:
        return gated
    broker = getattr(engine, "executor_broker", None)
    broker_result = (
        broker.execute(
            spec=spec,
            receipt_value=invocation.execution_receipt,
            invocation_id=invocation.id,
            arguments=invocation.arguments,
            ledger_scope=f"{profile_user_id}\x1f{session_id}",
        )
        if broker is not None
        else None
    )
    status = str(getattr(broker_result, "status", "unavailable_before_dispatch") or "").strip()
    reason = str(getattr(broker_result, "reason", "executor_broker_unavailable") or "").strip()
    data = dict(getattr(broker_result, "data", {}) or {}) if broker_result is not None else {}
    model_feedback = str(getattr(broker_result, "model_feedback", "") or "").strip()
    if not model_feedback:
        if status == "succeeded":
            model_feedback = f"已从用户绑定电脑读取或执行了 {spec.display_name}，以下是实际返回结果。"
        else:
            model_feedback = f"当前无法使用用户电脑上的{spec.display_name}，请如实说明没有完成。"
    if data and status in {"succeeded", "execution_unknown"}:
        model_feedback = f"{model_feedback}\n实际返回数据：{json.dumps(data, ensure_ascii=False, sort_keys=True)}"
    event = {
        "type": "capability_execution_result",
        "tool_type": spec.capability_id,
        "status": status,
    }
    if reason:
        event["reason"] = reason
    result = ToolExecutionResult(
        tool_type=spec.capability_id,
        stream_events=[event],
        followup_context=model_feedback,
        state_updates={
            "capability_execution": {
                "tool_type": spec.capability_id,
                "status": status,
                "reason": reason,
            }
        },
    )
    envelope = ToolResultEnvelope(
        invocation_id=invocation.id,
        status="ok" if status == "succeeded" else "error",
        model_feedback=model_feedback,
        data={
            "code": reason or status,
            "tool": spec.capability_id,
            "status": status,
            "result": data,
        },
        events=[event],
    )
    return result, envelope


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
    followup_envelope = getattr(result, "followup_envelope", None)
    followup = str(getattr(followup_envelope, "content", "") or getattr(result, "followup_context", "") or "").strip()
    followup_data: dict[str, Any] = {}
    if isinstance(followup_envelope, ToolFollowupEnvelope):
        followup_data = {
            "producer_bounded": bool(followup_envelope.producer_bounded),
            "complete": bool(followup_envelope.complete),
            "continuation": dict(followup_envelope.continuation or {}),
            "diagnostics": dict(followup_envelope.diagnostics or {}),
        }
    return ToolResultEnvelope(
        invocation_id=invocation.id,
        status="ok",
        model_feedback=followup,
        data={
            "tool_type": str(getattr(result, "tool_type", "") or invocation.name),
            "state_updates": dict(getattr(result, "state_updates", {}) or {}),
            **({"followup": followup_data} if followup_data else {}),
        },
        events=list(getattr(result, "stream_events", []) or []),
    )
