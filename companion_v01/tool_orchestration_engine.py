from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import config
from capcore import validate_tool_spec_args

from .client_protocol import ClientProtocolContext
from .client_protocol import ClientMode
from .tool_invocation import LEGACY_JSON
from .tool_invocation import NATIVE_ANTHROPIC
from .tool_invocation import NATIVE_OPENAI
from .tool_invocation import TOOL_INVOCATION_ID_FIELD
from .tool_invocation import TOOL_CAPABILITY_SELECTION_FIELD
from .tool_invocation import TOOL_EXECUTION_RECEIPT_FIELD
from .tool_invocation import TOOL_MODEL_ARGUMENTS_FIELD
from .tool_invocation import TOOL_MODEL_NAME_FIELD
from .tool_invocation import TOOL_SOURCE_FIELD
from .tool_invocation import ToolInvocation
from .tool_invocation import ToolResultEnvelope
from .tool_invocation import ValidationResult
from .tool_invocation import invocation_to_legacy_tool_call
from .tool_invocation import legacy_tool_call_to_invocation
from .native_tool_schema import NATIVE_TOOL_CAPABILITY_ID_FIELD, build_openai_native_tool_specs
from .tool_runtime import ToolExecutionContext, ToolExecutionResult, ToolFollowupEnvelope
from .tool_handlers.core import TaskExecutionScope
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




def _bounded_int(raw_value: Any, *, default: int, lower: int = 1, upper: int = 16) -> int:
    try:
        value = int(raw_value)
    except Exception:
        value = default
    return max(lower, min(upper, value))


def max_tool_rounds() -> int:
    try:
        return max(0, int(getattr(config, "TOOL_ROUND_HARD_LIMIT", 0) or 0))
    except Exception:
        return 0


def tool_round_warning_remaining(*, hard_limit: int | None = None) -> int:
    hard = max_tool_rounds() if hard_limit is None else max(0, int(hard_limit or 0))
    if hard <= 0:
        return 0
    try:
        configured = max(0, int(getattr(config, "TOOL_ROUND_WARNING_REMAINING", 8) or 0))
    except Exception:
        configured = 8
    return min(max(0, hard - 1), configured)


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
) -> str:
    """Discipline the tool result text fed back to the model (Claude Code-aligned).

    Rules applied at the single point where a tool result becomes model-facing
    feedback:
    - empty-but-successful -> stable placeholder, never an empty tool result
      (mirrors Claude Code's empty tool_result guard; an empty result tail can
      make some models end the turn with no output).
    - producer-bounded envelope -> preserve the producer's complete logical
      units and continuation cursor instead of applying another character cut.
    - all other internal results -> preserve them unchanged. Long-result producers
      own paging; this shared boundary must not silently remove evidence.

    No paths are introduced here. Producer-owned continuation and independent
    storage-boundary secret/path sanitization remain separate contracts.
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
    return text


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
            "停止调用，并按当前客户端协议自然回复主人；证据缺失的部分要明确说明，不要补猜。"
            "凡是会对外发送或改变状态的动作，查询/准备/生成参数都不等于完成；必须实际调用对应动作工具，"
            "并看到该工具的成功回执后才能向用户宣称已完成。"
        )
    else:
        if str(stop_reason or "").strip() == "tool_budget_exhausted":
            lines.append(
                "本轮工具预算已经用完；最后一批工具已经真实执行，其结果就在上方。"
                "现在请按原有交付格式向用户说明本轮实际完成的内容、验证结果、尚未完成或无法确认的部分。"
                "如果任务没有完成，可以请用户发送“继续”；不要宣称未经验证的事项已经完成。"
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
            "本轮不要再调用工具（无论原生或兼容），请立即按当前客户端协议输出完整、可交付的最终回复。"
            "不得只回复“仍在处理”“还没完成”“需要继续查询”或类似占位语；即使证据不足，也要给出当前可支持的结论、"
            "限制与下一步建议。"
        )
    return "\n\n".join(lines)


def build_tool_round_warning(
    *,
    used_rounds: int,
    hard_limit: int,
    memcore_enabled: bool,
) -> str:
    """Explain the one-time continuation warning without changing permissions."""

    used = max(0, int(used_rounds or 0))
    hard = max(1, int(hard_limit or 1))
    remaining = max(0, hard - used)
    if memcore_enabled:
        history_rule = (
            "当前开放回合中的工具调用与结果现在都以完整形式可见。本回合结束并进入下一次用户请求后，"
            "MemCore 会继续保留普通工具调用参数；较短工具结果保留原文，只有满足压缩收益条件的较长结果"
            "才会替换为可通过 open_memory 回读的卡片。"
        )
    else:
        history_rule = (
            "当前开放回合中的工具调用与结果现在都以完整形式可见；回合结束后的历史可能因上下文维护而精简。"
        )
    return (
        f"【工具预算提醒：已使用 {used}/{hard}，还剩 {remaining} 轮】\n"
        "工具仍然可用。"
        f"{history_rule}\n"
        "单次卡片可以恢复对应结果，但不会自动把分散在多轮调用中的任务目标、关键决策、修改位置、"
        "验证状态与剩余工作整理成可靠的续作说明。如果预计不能在剩余预算内完成，请趁工具仍可使用，"
        "在真实项目中写入或更新一份可检查的续作记录，说明当前目标、已完成改动、关键文件与位置、"
        "实际测试结果、剩余事项、已知失败和下一步。不要复制大段已有输出，不要记录未经验证的结论。"
        "若能在本轮完成，则继续正常执行，无需额外创建文件。"
    )


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
            value, source=source, invocation_id=invocation_id,
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
    allowed_sequence = [str(item or "").strip() for item in (allowed_tool_names or ()) if str(item or "").strip()]
    allowed = set(allowed_sequence)
    if "*" in allowlist:
        # Capability selection remains authoritative: wildcard means all tools
        # already selected for this client/scenario, never every dormant handler.
        candidates = allowed_sequence if allowed_tool_names is not None else [
            str(name or "").strip() for name in handlers if str(name or "").strip()
        ]
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

    if str(invocation.parse_error or "").strip():
        if invocation.name == "computer_use":
            details = {"action": "unknown", "dispatch_phase": "validation", "dispatched": False,
                       "action_state": "not_started", "error": {"path": "$", "reason": "invalid_json",
                       "expected": "valid JSON object matching the loaded computer_use contract"}}
            return ValidationResult.fail(str(invocation.parse_error),
                "computer_use 参数未形成有效 JSON 对象，本次尚未派发。" + json.dumps(details, ensure_ascii=False),
                details=details)
        raw_arguments = invocation.raw_arguments
        try:
            rendered_arguments = (
                raw_arguments
                if isinstance(raw_arguments, str)
                else json.dumps(raw_arguments, ensure_ascii=False, separators=(",", ":"), default=str)
            )
        except Exception:
            rendered_arguments = repr(raw_arguments)
        return ValidationResult.fail(
            str(invocation.parse_error),
            (
                f"工具「{str(invocation.name or 'unknown')}」的参数没有形成有效 JSON，系统没有执行这次调用。"
                f"Provider 返回的原始参数是：{rendered_arguments}。"
                "请根据本轮实际提供的工具 schema 修正参数后重新调用；工具仍然可用。"
            ),
        )

    tool_type = str(invocation.name or "").strip()
    if not tool_type:
        return ValidationResult.fail("missing_tool_type", "工具调用缺少 type 字段。")
    execution_allowlist = getattr(invocation.capability_selection, "execution_allowlist", None)
    if execution_allowlist is not None and tool_type not in execution_allowlist:
        return ValidationResult.fail(
            "tool_not_allowed",
            f"工具「{tool_type}」不在本次任务的可执行范围内。这次调用没有执行，请使用本轮提供的工具。",
        )
    candidate_call = raw_tool_call if isinstance(raw_tool_call, dict) else invocation_to_legacy_tool_call(invocation)

    satellite_spec = desktop_satellite_spec(tool_type)
    if satellite_spec is not None:
        handler = _resolved_handler_for_round(engine, tool_type, invocation.capability_selection)
        if handler is None:
            return ValidationResult.fail("unknown_tool", "当前桌面执行器没有提供这项能力。")
        if tool_type == "computer_use":
            from .computer_use import contracts
            # No device dispatch has happened at this boundary. Do not reuse
            # this not_started assertion for transport or device failures.
            details = {"action": invocation.arguments.get("action") if invocation.arguments.get("action") in contracts.ACTIONS else "unknown",
                       "dispatch_phase": "validation", "dispatched": False, "action_state": "not_started"}
            try:
                issue = contracts.argument_error(invocation.arguments)
            except Exception as exc:
                details.update(reason="validator_internal_error", exception_type=type(exc).__name__)
                return ValidationResult.fail("validator_internal_error",
                    "computer_use 宿主参数校验器异常，本次尚未派发；不能据此认定参数有错。"
                    + json.dumps(details, ensure_ascii=False), details=details)
            if issue:
                details.update(reason="bad_args", error=issue)
                return ValidationResult.fail("bad_args",
                    "computer_use 参数不符合当前动作规则，本次尚未派发。请按 error 修正："
                    + json.dumps(details, ensure_ascii=False), details=details)
            normalized_candidate = candidate_call
        else:
            try:
                normalized_candidate = handler.normalize_call(candidate_call)
            except Exception:
                normalized_candidate = None
        if normalized_candidate is None:
            return ValidationResult.fail("bad_args", "本地能力的参数不符合当前执行器契约。")
        if not invocation.execution_receipt:
            return ValidationResult.fail(
                "not_available",
                "绑定的桌面设备本轮没有提供可用的执行凭据（可能未启动桌宠、断线、未授权或能力不匹配），本次没有执行桌面动作。"
                "这不代表群聊禁止操作，也不代表其他执行工具不可用；请依据本轮实际提供的工具及其执行位置继续判断。",
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
        return ValidationResult.fail(
            "unknown_tool",
            _unknown_tool_recovery_message(tool_type, handlers, invocation.capability_selection),
        )

    spec = None
    try:
        spec_getter = getattr(handler, "tool_spec", None)
        spec = spec_getter() if callable(spec_getter) else None
    except Exception:
        spec = None

    try:
        normalized_candidate = handler.normalize_call(candidate_call)
    except Exception:
        normalized_candidate = None
    if normalized_candidate is None:
        argument_error = getattr(handler, "argument_error", None)
        if callable(argument_error):
            issue = argument_error(candidate_call)
            if issue:
                return ValidationResult.fail("bad_args", issue)
        try:
            schema_validation = validate_tool_spec_args(spec, invocation.arguments) if spec is not None else None
        except Exception:
            schema_validation = None
        return ValidationResult.fail(
            "bad_args",
            _tool_schema_rejection_message(
                tool_type=tool_type,
                candidate_call=candidate_call,
                spec=spec,
                errors=schema_validation.errors
                if schema_validation is not None and not schema_validation.ok
                else (),
                conditional_rule_failed=schema_validation is None or schema_validation.ok,
            ),
        )
    return ValidationResult.success()


def _unknown_tool_recovery_message(tool_type, handlers, selection) -> str:
    """Describe the published surface, not the larger internal handler registry."""
    ceiling = getattr(selection, "execution_allowlist", None)
    allowed = {str(name) for name in handlers if ceiling is None or name in ceiling}
    schema_names = getattr(selection, "schema_tool_names", None)
    if schema_names is None:
        from .capability_contracts import ContractBoundHandler
        schema_names = {name for name in allowed if not isinstance(handlers[name], ContractBoundHandler)
                        or handlers[name].snapshot.get("contract_ref")}
    direct = sorted(allowed if schema_names is None else allowed.intersection(schema_names))
    payload = {
        "reason": "unknown_tool", "executed": False,
        "requested_tool": tool_type, "declared_tool_ids": direct,
    }
    if {"capability_list", "capability_load", "capability_invoke"}.issubset(direct):
        payload["recovery"] = (
            "上列是本轮已声明的工具 ID，请按实际提供的通道调用。按需能力没有直接声明，"
            "可用 capability_list 核对目录，或用 capability_load 加载已知精确 ID；"
            "随后用 capability_invoke 和返回的 contract_ref 执行。一次未知 ID 不代表其他工具不可用。"
        )
    else:
        payload["recovery"] = "请按本轮实际声明的工具及通道继续；这次调用未执行，不影响其他可用工具。"
    return f"工具「{tool_type}」没有找到可执行入口。" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _tool_schema_rejection_message(
    *,
    tool_type: str,
    candidate_call: Mapping[str, Any],
    spec: Any,
    errors: Iterable[Any],
    conditional_rule_failed: bool = False,
) -> str:
    schema = getattr(spec, "input_schema", None)
    error_payload = []
    for error in errors:
        error_payload.append(
            {
                "code": str(getattr(error, "code", "validation_failed") or "validation_failed"),
                "field": str(getattr(error, "argument", "") or ""),
                "detail": str(getattr(error, "detail", "") or ""),
            }
        )
    diagnostics = {
        "reason": "conditional_fields_invalid" if conditional_rule_failed else "schema_validation_failed",
        "errors": error_payload,
    }
    if isinstance(schema, Mapping):
        diagnostics["input_schema"] = schema
    else:
        diagnostics["schema_available"] = False
    return (
        f"你对工具「{tool_type}」的调用没有通过参数校验，系统没有执行。"
        f"结构化诊断：{json.dumps(diagnostics, ensure_ascii=False, separators=(',', ':'))}。"
        f"你提交的是：{describe_tool_call_for_prompt(candidate_call)}。"
        "请只使用本轮 schema 声明的字段修正调用；不要猜测别名，也不要重复未改变的失败参数。"
        "如果不再需要工具，按当前客户端的最终回复协议直接回复主人。"
    )


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
    bound = _resolved_handler_for_round(engine, invocation.name, invocation.capability_selection)
    contract_check = getattr(bound, "contract_error", None)
    if callable(contract_check):
        reason = contract_check()
        if reason:
            from .capability_contracts import contract_failure
            result = contract_failure(invocation.name, reason)
            return result, ToolResultEnvelope(invocation_id=invocation.id, status="rejected",
                model_feedback=result.followup_context,
                data={"code": reason, "tool": invocation.name, "executed": False}, events=result.stream_events)
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
            client_context=client_context,
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
    from .turn_coordination import current_cancellation_check

    execution_context = ToolExecutionContext(
            invocation_id=invocation.id,
            capability_selection=invocation.capability_selection,
            cancel_requested=current_cancellation_check(),
            profile_user_id=profile_user_id,
            session_id=session_id,
            now_ts=now_ts,
            visual_payload=enriched_visual_payload,
            character_pack_id=str(character_pack_id or ""),
            current_user_source_id=current_user_source_id,
            client_mode=client_mode,
            request_context=dict(request_context or {}),
            execution_scope=(
                request_context.get("_task_execution_scope")
                if isinstance((request_context or {}).get("_task_execution_scope"), TaskExecutionScope)
                else None
            ),
        )
    host_tool_jobs = getattr(engine, "host_tool_jobs", None)
    if host_tool_jobs is not None and host_tool_jobs.accepts(
        handler=handler,
        context=execution_context,
    ):
        result = host_tool_jobs.submit(
            capability_id=invocation.name,
            invocation_id=invocation.id,
            call=normalized_call,
            context=execution_context,
            domain_profile_id=domain_profile_id,
            handler=handler,
            turn_id=str((request_context or {}).get("_host_origin_turn_id") or ""),
        )
        return result, _final_exec_envelope(invocation=invocation, result=result)
    broker = getattr(engine, "executor_broker", None)
    if broker is None:
        broker = ExecutorBroker(None)
        try:
            setattr(engine, "executor_broker", broker)
        except Exception:
            pass
    from .tool_batch import cancelled_tool_result
    from .execution_resource_policy import tool_input_arguments
    from .execution_policies import policy_tool_spec

    # Match long-task admission: keep the selected implementation alive across
    # asynchronous policy hooks and publication, with provider revocation intact.
    retain = getattr(handler, "retain_invocation", None)
    retained = callable(retain)
    if retained:
        handler = retain()
    try:
        broker_result = broker.execute_server_local(
            tool_id=invocation.name,
            invocation_id=invocation.id,
            dispatch=lambda: cancelled_tool_result(invocation.name) if (
                execution_context.cancel_requested is not None and execution_context.cancel_requested()
            ) else handler.execute(
                call=normalized_call,
                context=execution_context,
            ),
            ledger_scope=(
                f"{profile_user_id}\x1f{session_id}\x1f{execution_context.execution_scope.task_id}"
                if execution_context.execution_scope is not None and execution_context.execution_scope.task_id
                else f"{profile_user_id}\x1f{session_id}"
            ),
            request_data={"arguments": normalized_call},
            input_arguments=tool_input_arguments(handler, normalized_call),
            policy_spec=policy_tool_spec(handler), policy_client_mode=execution_context.client_mode,
        )
    finally:
        release = getattr(handler, "release_invocation", None)
        if retained and callable(release):
            release()
    if getattr(broker_result, "policy_failure", None) is not None:
        from .execution_policies import policy_failure_tool_result
        result = policy_failure_tool_result(invocation.name, broker_result.status, broker_result.reason, broker_result.policy_failure)
        return result, _final_exec_envelope(invocation=invocation, result=result)
    if getattr(broker_result, "resource_limit", None) is not None:
        from .execution_resource_policy import resource_limit_tool_result
        result = resource_limit_tool_result(invocation.name, broker_result.resource_limit)
        return result, _final_exec_envelope(invocation=invocation, result=result)
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
    capability_result = result.capability_result
    if capability_result is not None and capability_result.is_error and capability_result.status in {"resource_exhausted", "policy_rejected", "policy_failed", "result_processing_failed"}:
        detail_key = "resource_limit" if capability_result.status == "resource_exhausted" else "policy_failure"
        detail = {detail_key: capability_result.content}
        if capability_result.status == "result_processing_failed" and isinstance(capability_result.content, Mapping):
            detail = {key: capability_result.content.get(key) for key in ("policy_failure", "execution_receipt")}
        return ToolResultEnvelope(invocation_id=base.invocation_id, status="error", model_feedback=base.model_feedback,
            data={**dict(base.data or {}), **detail}, events=list(base.events or []))
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
            elif str(invocation.name or "").strip() == "exec_input" and inner in {"idle", "pending", "written"}:
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
    client_context: ClientProtocolContext | None = None,
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
            policy_client_mode=getattr(getattr(client_context, "effective_mode", None), "value", ""),
        )
    policy_failure = (getattr(broker_result, "data", {}) or {}).get("policy_failure")
    if isinstance(policy_failure, dict):
        from .execution_policies import policy_failure_tool_result
        result = policy_failure_tool_result(invocation.name, broker_result.status, broker_result.reason, policy_failure)
        return result, _final_exec_envelope(invocation=invocation, result=result)
    resource_limit = (getattr(broker_result, "data", {}) or {}).get("resource_limit")
    if isinstance(resource_limit, dict):
        from .execution_resource_policy import resource_limit_tool_result
        result = resource_limit_tool_result(invocation.name, resource_limit)
        return result, _final_exec_envelope(invocation=invocation, result=result)
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
    """Allow QQ device access only for the authenticated owner sender.

    Group storage identity is not an authorization principal. The delivery
    context is supplied by the QQ ingress, never by model tool arguments.
    Owners may request access in either conversation kind; other members and
    actorless continuations fail closed. Existing capability gates still apply.
    """
    delivery = request_context.get("qq_delivery_context") if isinstance(request_context, dict) else None
    if not isinstance(delivery, dict):
        return None
    is_group = bool(delivery.get("is_group"))
    owner_qq = str(getattr(config, "MASTER_QQ", "") or "").strip()
    sender_qq = str(delivery.get("user_id") or "").strip()
    if owner_qq.isdigit() and sender_qq == owner_qq:
        return None
    tool_id = spec.capability_id
    reason = "device_action_requires_owner"
    event = {
        "type": "capability_execution_result",
        "tool_type": tool_id,
        "status": "blocked",
        "reason": reason,
    }
    feedback = (
        "这条群聊消息不是来自已配置的主人账号，不能读取或操控绑定电脑；其他群成员不能继承主人的权限，本次没有执行任何操作。"
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
    request_context: dict[str, Any] | None = None,
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
    from .capcore_runtime import authorization_profile_user_id, manual_permission_request, resolve_permission_for_profile

    client_mode = ""
    if client_context is not None:
        client_mode = str(getattr(client_context.effective_mode, "value", client_context.effective_mode) or "")
    context = ToolExecutionContext(
        profile_user_id=profile_user_id,
        session_id=session_id,
        now_ts=0,
        visual_payload={},
        client_mode=client_mode,
        request_context=dict(request_context or {}),
    )
    arguments = dict(invocation.arguments or {})
    authorization_profile = authorization_profile_user_id(context)
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
    decision = resolve_permission_for_profile(
        request,
        base_dir=base_dir,
        profile_user_id=authorization_profile,
        family_id="ops",
    )
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
            authorization_profile_user_id=authorization_profile,
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
            authorization_profile_user_id=authorization_profile,
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
    authorization_profile_user_id: str,
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
            "authorizationProfileUserId": authorization_profile_user_id,
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
        request_context=request_context,
    )
    if gated is not None:
        return gated
    broker = getattr(engine, "executor_broker", None)
    from .computer_use.session import dispatch_scope, scope_key
    scope_token = dispatch_scope.set(scope_key(profile_user_id, session_id))
    try:
        if spec.capability_id == "computer_use" and broker is not None:
            from .computer_use.dispatch import dispatch
            broker_result, stopped = dispatch(engine, broker=broker, spec=spec, invocation=invocation,
                profile_user_id=profile_user_id, session_id=session_id, client_context=client_context, request_context=request_context)
            if stopped is not None:
                return stopped
        else:
            broker_result = (
            broker.execute(
                spec=spec,
                receipt_value=invocation.execution_receipt,
                invocation_id=invocation.id,
                arguments=invocation.arguments,
                ledger_scope=f"{profile_user_id}\x1f{session_id}",
                policy_client_mode=getattr(getattr(client_context, "effective_mode", None), "value", ""),
            )
            if broker is not None else None
            )
    finally:
        dispatch_scope.reset(scope_token)
    policy_failure = (getattr(broker_result, "data", {}) or {}).get("policy_failure")
    if isinstance(policy_failure, dict):
        from .execution_policies import policy_failure_tool_result
        result = policy_failure_tool_result(invocation.name, broker_result.status, broker_result.reason, policy_failure)
        return result, _final_exec_envelope(invocation=invocation, result=result)
    resource_limit = (getattr(broker_result, "data", {}) or {}).get("resource_limit")
    if isinstance(resource_limit, dict):
        from .execution_resource_policy import resource_limit_tool_result
        result = resource_limit_tool_result(invocation.name, resource_limit)
        return result, _final_exec_envelope(invocation=invocation, result=result)
    status = str(getattr(broker_result, "status", "unavailable_before_dispatch") or "").strip()
    reason = str(getattr(broker_result, "reason", "executor_broker_unavailable") or "").strip()
    data = dict(getattr(broker_result, "data", {}) or {}) if broker_result is not None else {}
    model_feedback = str(getattr(broker_result, "model_feedback", "") or "").strip()
    artifact_events = []
    model_image_inputs = []
    if spec.capability_id == "computer_use":
        from .computer_use.media import extract_images
        from .computer_use.outcome import complete_outcome
        data, status, reason = complete_outcome(data, status=status, reason=reason,
                                               action=invocation.arguments.get("action"))
        try:
            model_image_inputs = extract_images(data)
        except (ValueError, TypeError, OSError):
            data["ok"] = False
            data["observation_state"] = "failed"
            data["reason"] = "invalid_screenshot_payload"
            status, reason = "failed", "invalid_screenshot_payload"
        model_feedback = "桌面工具实际结果如下；动作执行状态不代表任务完成。"
    if spec.capability_id == "desktop_screenshot":
        if status == "succeeded":
            try:
                from .desktop_screenshot import register_desktop_screenshot
                artifact_event, model_image_inputs = register_desktop_screenshot(
                    engine, data, profile_user_id=profile_user_id, session_id=session_id)
                artifact_events.append(artifact_event)
                model_feedback = "已截取绑定电脑主屏幕。图片已登记，若用户要求接收图片，请用 send_file 发送返回的 generated_handle。"
                if not model_image_inputs:
                    model_feedback += "本轮未附加可视图片，不能声称已经看清内容；可以使用 inspect_image 查看该句柄。"
            except Exception:
                status, reason = "failed", "screenshot_registration_failed"
                data = {}
                model_feedback = "桌面截图未能登记为可用图片，本次没有完成截图交付。"
        data.pop("imageBase64", None)
    if not model_feedback:
        if status == "succeeded":
            model_feedback = f"已从用户绑定电脑读取或执行了 {spec.display_name}，以下是实际返回结果。"
        else:
            model_feedback = f"当前无法使用用户电脑上的{spec.display_name}，请如实说明没有完成。"
    if data and (status in {"succeeded", "execution_unknown"} or spec.capability_id == "computer_use"):
        if spec.capability_id == "computer_use":
            from .computer_use.presentation import feedback
            model_feedback = feedback(data)
        else:
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
        stream_events=[*artifact_events, event],
        followup_context=model_feedback,
        model_image_inputs=model_image_inputs,
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
        events=[*artifact_events, event],
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
        data={"code": str(validation.code or "validation_failed"), "tool": str(invocation.name or ""),
              **({"validation": validation.details} if validation.details else {})},
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
