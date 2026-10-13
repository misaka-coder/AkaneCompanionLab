"""Admit calls against the frozen surface and report actionable contract errors."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from capcore import validate_tool_spec_args

from ..capability_registry import OPEN_BROWSER_TOOL_SPEC
from ..client_protocol import ClientProtocolContext
from ..desktop_satellite_specs import desktop_satellite_spec
from ..tool_invocation import (
    ToolInvocation,
    TOOL_CAPABILITY_SELECTION_FIELD,
    TOOL_INVOCATION_ID_FIELD,
    TOOL_MODEL_ARGUMENTS_FIELD,
    ValidationResult,
    invocation_to_legacy_tool_call,
    legacy_tool_call_to_invocation,
)
from .feedback import describe_tool_call_for_prompt


@dataclass
class PreparedToolInvocation:
    """One request-local binding and normalized business call."""

    invocation: ToolInvocation
    raw_tool_call: dict[str, Any]
    business_tool_call: dict[str, Any]
    handler: Any = None
    handlers: Mapping[str, Any] | None = None
    spec: Any = None
    normalized_call: dict[str, Any] | None = None
    normalization_failed: bool = False
    validation: ValidationResult | None = None
    terminal_result: Any = None


def _preflight_invocation(invocation: ToolInvocation) -> ValidationResult | None:
    if invocation.scheduling_error:
        return ValidationResult.fail("bad_args", invocation.scheduling_error)

    if str(invocation.parse_error or "").strip():
        if invocation.name == "computer_use":
            details = {
                "action": "unknown",
                "dispatch_phase": "validation",
                "dispatched": False,
                "action_state": "not_started",
                "error": {
                    "path": "$",
                    "reason": "invalid_json",
                    "expected": "valid JSON object matching the loaded computer_use contract",
                },
            }
            return ValidationResult.fail(
                str(invocation.parse_error),
                "computer_use 参数未形成有效 JSON 对象，本次尚未派发。" + json.dumps(details, ensure_ascii=False),
                details=details,
            )
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
                if tool_type not in {str(name or "").strip() for name in getattr(disclosure, "tool_names", ()) or ()}:
                    continue
                if str(getattr(disclosure, "state", "") or "").strip().lower() != "unavailable":
                    continue
                reason = str(getattr(disclosure, "reason", "") or "").strip()
                recovery = str(
                    getattr(disclosure, "activation", "") or getattr(disclosure, "recovery_hint", "") or ""
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
    return None


def prepare_tool_invocation(
    engine: Any,
    invocation: ToolInvocation,
    *,
    client_context: ClientProtocolContext | None = None,
    profile_user_id: str = "",
    session_id: str = "",
    raw_tool_call: Any = None,
    domain_profile_id: str = "",
    for_execution: bool = False,
) -> PreparedToolInvocation:
    raw_call = (
        dict(raw_tool_call)
        if isinstance(raw_tool_call, dict)
        else invocation_to_legacy_tool_call(invocation, include_metadata=True)
    )
    raw_call["type"] = str(invocation.name or "").strip()
    raw_call[TOOL_INVOCATION_ID_FIELD] = str(invocation.id or "")
    if invocation.capability_selection is not None:
        raw_call[TOOL_CAPABILITY_SELECTION_FIELD] = invocation.capability_selection

    prepared = PreparedToolInvocation(
        invocation=invocation,
        raw_tool_call=raw_call,
        business_tool_call={key: value for key, value in raw_call.items() if not str(key).startswith("_tool_")},
    )
    preflight = _preflight_invocation(invocation)
    if preflight is not None:
        prepared.validation = preflight
        return prepared

    selection = invocation.capability_selection
    selected_handlers = getattr(selection, "resolved_handlers", None) if selection is not None else None
    handlers: dict[str, Any]
    selected = selected_handlers.get(invocation.name) if isinstance(selected_handlers, Mapping) else None
    if selected is not None:
        handlers = dict(selected_handlers)
    else:
        resolver = getattr(engine, "_resolve_tool_handlers", None)
        handlers = (
            resolver(
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
                domain_profile_id=domain_profile_id,
                capability_selection=selection,
            )
            if callable(resolver)
            else getattr(engine, "tool_handlers", {})
        )
        if not isinstance(handlers, Mapping):
            handlers = {}
        selected = handlers.get(invocation.name)

    from ..capability_contracts import ContractBoundHandler, contract_failure
    if isinstance(selected, ContractBoundHandler):
        if not selected.native_declared:
            if for_execution:
                reason = "tool_not_declared"
                prepared.validation = ValidationResult.fail(
                    "not_available",
                    f"工具「{invocation.name}」没有当前声明的执行契约，本次没有执行。",
                )
                prepared.terminal_result = contract_failure(invocation.name, reason)
                prepared.handlers = handlers
                return prepared
        else:
            bound = selected.bind_for_execution()
            if bound is None:
                reason = "capability_not_available"
                prepared.validation = ValidationResult.fail(
                    "not_available",
                    f"工具「{invocation.name}」当前没有可用的执行绑定，本次没有执行。",
                )
                prepared.terminal_result = contract_failure(invocation.name, reason)
                prepared.handlers = handlers
                return prepared
            selected = bound
            if selection is not None:
                bound_handlers = {**dict(getattr(selection, "resolved_handlers", {}) or {}), invocation.name: bound}
                selection = replace(selection, resolved_handlers=MappingProxyType(bound_handlers))
                invocation = replace(invocation, capability_selection=selection)
                raw_call[TOOL_CAPABILITY_SELECTION_FIELD] = selection
                handlers = {**handlers, invocation.name: bound}

    prepared.invocation = invocation
    prepared.handler = selected
    prepared.handlers = handlers
    if selected is None:
        return prepared

    try:
        getter = getattr(selected, "tool_spec", None)
        prepared.spec = getter() if callable(getter) else None
    except Exception:
        prepared.spec = None

    from ..tool_scheduling import extract_scheduling, freeze_request_value
    working_call = extract_scheduling(
        freeze_request_value(raw_call),
        spec=prepared.spec,
        tool_name=invocation.name,
    )
    invocation = replace(
        invocation,
        background=working_call.get("_tool_background") is True,
        scheduling_error=str(working_call.get("_tool_scheduling_error") or ""),
    )
    prepared.invocation = invocation
    if invocation.scheduling_error:
        prepared.validation = ValidationResult.fail("bad_args", invocation.scheduling_error)
        return prepared

    # Transport metadata stays on raw_tool_call and in ToolInvocation. Strict
    # business handlers receive only the prepared call they own.
    business_call = {key: value for key, value in working_call.items() if not str(key).startswith("_tool_")}
    from ..tool_handlers.adapters import AdapterCapabilityToolHandler

    business_handler = selected._handler if isinstance(selected, ContractBoundHandler) else selected
    wire_arguments = working_call.get(TOOL_MODEL_ARGUMENTS_FIELD)
    if (isinstance(business_handler, AdapterCapabilityToolHandler)
            and isinstance(wire_arguments, Mapping)
            and invocation.model_name not in {"capability_invoke", "invoke_mcp"}):
        # Native adapter arguments may themselves contain type/arguments.
        # Put the peeled wire values in the existing business envelope instead
        # of interpreting those keys as host routing fields.
        business_call = {"type": invocation.name, "arguments": dict(wire_arguments)}
    prepared.business_tool_call = business_call

    try:
        # computer_use preserves input exactly and produces its one detailed
        # validation result at admission, including internal validator errors.
        normalized_candidate = business_call if invocation.name == "computer_use" else selected.normalize_call(business_call)
    except Exception:
        normalized_candidate = None
    if not isinstance(normalized_candidate, dict):
        prepared.normalization_failed = True
        return prepared

    normalized_invocation = legacy_tool_call_to_invocation(
        normalized_candidate,
        source=invocation.source,
        invocation_id=invocation.id,
        capability_selection=invocation.capability_selection,
    )
    if normalized_invocation is None:
        prepared.normalization_failed = True
        return prepared
    normalized_invocation = replace(
        normalized_invocation,
        execution_receipt=invocation.execution_receipt,
        parse_error=invocation.parse_error,
        raw_arguments=invocation.raw_arguments,
        model_name=invocation.model_name,
        model_arguments=invocation.model_arguments,
        capability_selection=invocation.capability_selection,
        background=invocation.background,
        scheduling_error=invocation.scheduling_error,
    )
    prepared.invocation = normalized_invocation
    prepared.normalized_call = invocation_to_legacy_tool_call(normalized_invocation)
    return prepared


def validate_tool_invocation(
    engine: Any,
    invocation: ToolInvocation | None,
    *,
    client_context: ClientProtocolContext | None = None,
    profile_user_id: str = "",
    session_id: str = "",
    raw_tool_call: Any = None,
    domain_profile_id: str = "",
    prepared: PreparedToolInvocation | None = None,
) -> ValidationResult:
    if invocation is None:
        return ValidationResult.success()
    prepared = prepared or prepare_tool_invocation(
        engine,
        invocation,
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
        raw_tool_call=raw_tool_call,
        domain_profile_id=domain_profile_id,
    )
    invocation = prepared.invocation
    if prepared.validation is not None:
        return prepared.validation

    tool_type = str(invocation.name or "").strip()
    candidate_call = prepared.business_tool_call
    satellite_spec = desktop_satellite_spec(tool_type)
    if satellite_spec is not None:
        handler = prepared.handler
        if handler is None:
            return ValidationResult.fail("unknown_tool", "当前桌面执行器没有提供这项能力。")
        if tool_type == "computer_use":
            from ..computer_use import contracts

            details = {
                "action": invocation.arguments.get("action")
                if invocation.arguments.get("action") in contracts.ACTIONS
                else "unknown",
                "dispatch_phase": "validation",
                "dispatched": False,
                "action_state": "not_started",
            }
            try:
                issue = contracts.argument_error(invocation.arguments)
            except Exception as exc:
                details.update(reason="validator_internal_error", exception_type=type(exc).__name__)
                return ValidationResult.fail(
                    "validator_internal_error",
                    "computer_use 宿主参数校验器异常，本次尚未派发；不能据此认定参数有错。"
                    + json.dumps(details, ensure_ascii=False),
                    details=details,
                )
            if issue:
                details.update(reason="bad_args", error=issue)
                return ValidationResult.fail(
                    "bad_args",
                    "computer_use 参数不符合当前动作规则，本次尚未派发。请按 error 修正："
                    + json.dumps(details, ensure_ascii=False),
                    details=details,
                )
        elif prepared.normalization_failed:
            return ValidationResult.fail("bad_args", "本地能力的参数不符合当前执行器契约。")
        if not invocation.execution_receipt:
            return ValidationResult.fail(
                "not_available",
                "绑定的桌面设备本轮没有提供可用的执行凭据（可能未启动桌宠、断线、未授权或能力不匹配），本次没有执行桌面动作。"
                "这不代表群聊禁止操作，也不代表其他执行工具不可用；请依据本轮实际提供的工具及其执行位置继续判断。",
            )
        return ValidationResult.success()

    if tool_type == OPEN_BROWSER_TOOL_SPEC.capability_id:
        if prepared.handler is None:
            return ValidationResult.fail("unknown_tool", "当前没有可用的桌面网页打开工具。")
        if prepared.normalization_failed:
            return ValidationResult.fail("bad_args", "打开网页的 URL 或参数不符合公开网页安全约束。")
        if not invocation.execution_receipt:
            return ValidationResult.fail(
                "not_available",
                "用户绑定的桌面执行器当前不在线或没有提供网页打开能力，本次没有执行任何本地动作。",
            )
        return ValidationResult.success()

    handlers = prepared.handlers or {}
    handler = prepared.handler
    if handler is None:
        return ValidationResult.fail(
            "unknown_tool",
            _unknown_tool_recovery_message(tool_type, handlers, invocation.capability_selection),
        )
    spec = prepared.spec
    if prepared.normalization_failed:
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
                errors=schema_validation.errors if schema_validation is not None and not schema_validation.ok else (),
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
        from ..capability_contracts import ContractBoundHandler

        schema_names = {
            name
            for name in allowed
            if not isinstance(handlers[name], ContractBoundHandler) or handlers[name].native_declared
        }
    direct = sorted(allowed if schema_names is None else allowed.intersection(schema_names))
    payload = {
        "reason": "unknown_tool",
        "executed": False,
        "requested_tool": tool_type,
        "declared_tool_ids": direct,
    }
    if {"capability_list", "capability_load", "capability_invoke"}.issubset(direct):
        payload["recovery"] = (
            "上列是本轮已声明的工具 ID，请按实际提供的通道调用。按需能力没有直接声明，"
            "可用 capability_list 核对目录，或用 capability_load 加载已知精确 ID；"
            "随后用 capability_invoke 传入精确 ID 和 arguments 执行。一次未知 ID 不代表其他工具不可用。"
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

    from .normalization import parse_tool_call
    invocation = parse_tool_call(value)
    return validate_tool_invocation(
        engine,
        invocation,
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
