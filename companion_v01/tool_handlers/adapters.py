"""Capability-adapter and desktop-satellite tool handlers."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import threading
from pathlib import Path
from dataclasses import replace
from typing import Any, Mapping

from capcore import (
    CapabilityResult,
    build_permission_request as capcore_build_permission_request,
    build_tool_spec as capcore_build_tool_spec,
    prepare_invocation as capcore_prepare_invocation,
)
from ..capcore_runtime import (
    authorization_profile_user_id as capcore_authorization_profile_user_id,
    approval_policy_for_capability as capcore_approval_policy_for_capability,
    approval_required_event as capcore_approval_required_event,
    invocation_context_from_execution as capcore_invocation_context_from_execution,
)
from capcore import CapabilityProtocolError, InvocationContext
from ..capability_approval import build_approval_request_fingerprint
from ..desktop_satellite_specs import desktop_satellite_spec
from ..tool_invocation import (
    TOOL_CAPABILITY_SELECTION_FIELD,
    TOOL_EXECUTION_RECEIPT_FIELD,
    TOOL_EXECUTION_RECEIPTS_FIELD,
    TOOL_INVOCATION_ID_FIELD,
    TOOL_MODEL_NAME_FIELD,
    TOOL_MODEL_ARGUMENTS_FIELD,
    TOOL_SOURCE_FIELD,
)
from .core import (
    BaseToolHandler,
    ToolExecutionAdmission,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolFollowupEnvelope,
    ToolMetadata,
)
from ..mcp_diagnostics import build_mcp_failure_diagnostic, mcp_failure_reason


_ADAPTER_TRANSPORT_FIELDS = frozenset(
    {
        TOOL_SOURCE_FIELD,
        TOOL_INVOCATION_ID_FIELD,
        TOOL_MODEL_NAME_FIELD,
        TOOL_MODEL_ARGUMENTS_FIELD,
        TOOL_EXECUTION_RECEIPT_FIELD,
        TOOL_EXECUTION_RECEIPTS_FIELD,
        TOOL_CAPABILITY_SELECTION_FIELD,
    }
)


class DesktopSatelliteToolHandler(BaseToolHandler):
    """Schema adapter for a reviewed tool executed by the bound PC satellite.

    This handler never performs the local action itself. The invocation layer
    validates the call and sends it through ExecutorBroker to the live offer.
    """

    policy_accepted_native_tool = True

    def __init__(self, *, tool_id: str, offer_source: Any = None) -> None:
        spec = desktop_satellite_spec(tool_id)
        if spec is None:
            raise ValueError(f"unknown desktop satellite tool: {tool_id}")
        self.tool_type = spec.capability_id
        self._spec = spec
        self._offer_source = offer_source

    def tool_spec(self):
        return self._spec

    def tool_metadata(self) -> ToolMetadata:
        return ToolMetadata(
            family="desktop_satellite",
            operation="external",
            risk=self._spec.risk,
            default_round_budget=3,
            input_schema=self._spec.input_schema,
            requires_confirmation=self._spec.confirm != "never",
        )

    def build_prompt_instruction(self) -> str:
        return (
            f"- {self._spec.capability_id}：{self._spec.description}"
            f"调用参数遵循：{json.dumps(self._spec.input_schema, ensure_ascii=False, sort_keys=True)}"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or str(value.get("type") or "").strip() != self.tool_type:
            return None
        args = {
            key: item
            for key, item in value.items()
            if key != "type" and not str(key).startswith("_tool_")
        }
        if self.tool_type == "computer_use":
            from ..computer_use.contracts import normalize_arguments
            normalized = normalize_arguments(args)
            return None if normalized is None else {"type": self.tool_type, **normalized}
        if self.tool_type in {"desktop_context_snapshot", "system_media_snapshot", "desktop_screenshot"}:
            return {"type": self.tool_type} if not args else None
        if self.tool_type == "system_process_snapshot":
            if set(args) - {"query", "offset", "limit"}:
                return None
            if "query" in args and (not isinstance(args["query"], str) or len(args["query"]) > 100):
                return None
            for key, minimum, maximum in (("offset", 0, 1000000), ("limit", 1, 128)):
                if key in args and (type(args[key]) is not int or not minimum <= args[key] <= maximum):
                    return None
            return {"type": self.tool_type, **args}
        if self.tool_type == "system_media_control":
            action = str(args.get("action") or "").strip().lower()
            if action not in {"play", "pause", "stop", "previous", "next"} or set(args) != {"action"}:
                return None
            return {"type": self.tool_type, "action": action}
        if self.tool_type == "system_process_terminate":
            if set(args) != {"pid"}:
                return None
            raw_pid = args.get("pid")
            if isinstance(raw_pid, bool) or not isinstance(raw_pid, int):
                return None
            pid = raw_pid
            if pid < 1 or pid > 0xFFFFFFFF:
                return None
            return {"type": self.tool_type, "pid": pid}
        if self.tool_type == "system_volume":
            action = str(args.get("action") or "").strip().lower()
            if action == "get":
                return {"type": self.tool_type, "action": "get"} if set(args) == {"action"} else None
            if action == "set":
                if set(args) != {"action", "value"}:
                    return None
                raw_value = args.get("value")
                if isinstance(raw_value, bool) or not isinstance(raw_value, int):
                    return None
                value = raw_value
                if value < 0 or value > 100:
                    return None
                return {"type": self.tool_type, "action": "set", "value": value}
            return None
        return None

    def capability_status(self, **_kwargs: Any) -> dict[str, Any]:
        source = self._offer_source
        if source is None:
            return {"enabled": False, "status": "unavailable", "reason": "satellite_not_configured"}
        try:
            ready = source.resolve_receipt(self._spec) is not None
        except Exception:
            ready = False
        return {
            "enabled": ready,
            "status": "ready" if ready else "unavailable",
            "reason": "" if ready else "satellite_offline",
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        del call, context
        raise RuntimeError("desktop_satellite_requires_executor_broker")


class AdapterCapabilityToolHandler(BaseToolHandler):
    supports_program_results = True

    def __init__(
        self,
        *,
        capability_id: str,
        adapter: Any,
        descriptor: Any,
        config_base_dir: Path | str | None = None,
        approval_store: Any = None,
    ) -> None:
        self.tool_type = str(capability_id or "").strip()
        self.adapter = adapter
        self.descriptor = descriptor
        self.config_base_dir = config_base_dir
        self.approval_store = approval_store

    def tool_spec(self):
        """Project reviewed adapter descriptors through capcore's canonical contract."""
        return capcore_build_tool_spec(self.descriptor)

    def capability_status(self) -> dict[str, Any]:
        """Expose adapter tools only after an explicit live-provider check."""
        is_live_fn = getattr(self.adapter, "is_live", None)
        if callable(is_live_fn):
            try:
                try:
                    live = bool(is_live_fn(self.tool_type))
                except TypeError:
                    live = bool(is_live_fn())
            except Exception:
                return {"enabled": False, "status": "unavailable", "reason": "adapter_liveness_check_failed"}
            return {
                "enabled": live,
                "status": "ready" if live else "unavailable",
                "reason": "" if live else "mcp_session_not_live",
            }
        return {"enabled": False, "status": "unavailable", "reason": "adapter_liveness_check_missing"}

    def tool_metadata(self) -> ToolMetadata:
        risk = str(getattr(self.descriptor, "risk", "") or "medium").strip() or "medium"
        return ToolMetadata(
            family="adapter_capability",
            operation="external",
            risk=risk,
            default_round_budget=3,
            input_schema=self._input_schema(),
            requires_confirmation=str(getattr(self.descriptor, "confirm", "never") or "never") != "never",
        )

    def build_prompt_instruction(self) -> str:
        from ..legacy_tool_prompt import render_legacy_json_tool_instruction

        return render_legacy_json_tool_instruction(self.tool_spec(), argument_envelope=True)

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        original_arguments = value.get(TOOL_MODEL_ARGUMENTS_FIELD)
        nested_arguments = value.get("arguments")
        if isinstance(original_arguments, Mapping) and value.get(TOOL_MODEL_NAME_FIELD) not in {"invoke_mcp", "capability_invoke"}:
            # Native provider arguments are separate from transport fields.
            # Business keys such as type/arguments remain ordinary JSON keys.
            args = dict(original_arguments)
        elif isinstance(nested_arguments, Mapping):
            # ``arguments`` is the internal adapter-call envelope. Its contents
            # came from the model and must reach CapCore byte-for-byte: changing
            # keys, truncating collections, collapsing whitespace, or redacting
            # secret-shaped values here would make validation and execution see
            # a different call from the one the model actually made.
            args = dict(nested_arguments)
        else:
            # Provider-native and legacy calls are flattened at the outer wire
            # boundary. Only host-owned transport metadata lives there; model
            # arguments (including unknown ones) stay untouched for CapCore's
            # canonical validator to accept or reject explicitly.
            args = {key: item for key, item in value.items() if key != "type" and key not in _ADAPTER_TRANSPORT_FIELDS}
        return {"type": self.tool_type, "arguments": args}

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        admission = self.admit_execution(call=call, context=context)
        if admission.result is not None:
            return admission.result
        return self.execute_admitted(call=dict(admission.call or {}), context=context)

    def call_arguments(self, call):
        return dict(call.get("arguments") or {})

    def admit_execution(
        self,
        *,
        call: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionAdmission:
        raw_args = call.get("arguments") if isinstance(call.get("arguments"), Mapping) else {}
        invocation_context = capcore_invocation_context_from_execution(context)
        permission_request = capcore_build_permission_request(
            self.descriptor,
            raw_args,
            invocation_context,
        )
        base_dir = self.config_base_dir
        if not base_dir:
            import config
            base_dir = getattr(config, "DATA_DIR", "users_data")
        prepared = capcore_prepare_invocation(
            self.descriptor,
            raw_args,
            invocation_context,
            capcore_approval_policy_for_capability(
                base_dir=base_dir,
                profile_user_id=capcore_authorization_profile_user_id(context),
                capability_id=self.tool_type,
                family_id="ops" if permission_request.required else "",
            ),
        )
        if not prepared.validation.ok:
            return ToolExecutionAdmission.stop(self._validation_failed(prepared.validation))
        normalized_args = dict(prepared.normalized_args)
        decision = prepared.permission_decision
        if not prepared.ok:
            if decision is None:
                return ToolExecutionAdmission.stop(self._blocked_by_policy("permission_decision_missing"))
            if decision.requires_user_decision:
                result = self._approval_result_or_none(
                    decision=decision,
                    context=context,
                    normalized_args=normalized_args,
                )
                if result is not None:
                    return ToolExecutionAdmission.stop(result)
            else:
                return ToolExecutionAdmission.stop(self._blocked_by_policy(decision.reason))
        return ToolExecutionAdmission.allow({"type": self.tool_type, "arguments": normalized_args})

    def execute_admitted(
        self,
        *,
        call: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        normalized_args = call.get("arguments") if isinstance(call.get("arguments"), Mapping) else {}
        return self._invoke(normalized_args=dict(normalized_args), context=context)

    def _invoke(
        self,
        *,
        normalized_args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            result = self._run_coro_blocking(self._invoke_adapter(normalized_args=normalized_args, context=context))
        except CapabilityProtocolError as exc:
            return self._failure_from_exception(exc, fallback="adapter_protocol_error")
        except Exception as exc:
            return self._failure_from_exception(exc, fallback="adapter_invoke_failed")
        if not isinstance(result, CapabilityResult):
            return self._failure("adapter_result_invalid")
        if not result.is_error and not result.has_value:
            result = replace(result, value=result.content)
        from ..execution_policies import transform_execution_result
        result = transform_execution_result(self.tool_type, self.tool_spec(), result)
        if context.result_consumer == "program":
            return ToolExecutionResult(tool_type=self.tool_type, capability_result=result)
        producer_followup = self._format_capability_result(result)
        from ..execution_policies import present_execution_result
        followup = present_execution_result(self.tool_type, self.tool_spec(), result, producer_followup)
        model_material = followup if followup != producer_followup else None
        is_error = bool(getattr(result, "is_error", False))
        status = self._safe_public_text(getattr(result, "status", ""), limit=80) if is_error else "ok"
        status = status or ("error" if is_error else "ok")
        reason = self._safe_public_text(getattr(result, "reason", ""), limit=120) if is_error else ""
        event = {
            "type": "adapter_capability_completed",
            "capabilityId": self.tool_type,
            "status": status,
            "is_error": is_error,
        }
        provider = self._capability_result_provider(result)
        if provider:
            event["provider"] = provider
        state_updates = {
            "adapter_capability_status": status,
            "adapter_capability_id": self.tool_type,
        }
        if reason:
            event["reason"] = reason
            state_updates["adapter_capability_reason"] = reason
        execution_result = ToolExecutionResult(
            tool_type=self.tool_type,
            capability_result=result,
            stream_events=[event],
            followup_context=followup,
            followup_envelope=ToolFollowupEnvelope(
                content=followup,
                producer_bounded=True,
                complete=True,
                diagnostics={"adapter_result_chars": len(followup)},
            ),
            state_updates=state_updates,
        )
        return self._finalize_execution_result(
            execution_result,
            capability_result=result,
            context=context,
            model_material=model_material,
        )

    def _invoke_adapter(self, *, normalized_args: dict[str, Any], context: ToolExecutionContext) -> Any:
        return self.adapter.invoke(self.tool_type, normalized_args, self._invocation_context(context))

    def _invocation_context(self, context: ToolExecutionContext) -> InvocationContext:
        return InvocationContext(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            client_mode=context.client_mode,
        )

    def _approval_result_or_none(
        self,
        *,
        decision: Any,
        context: ToolExecutionContext,
        normalized_args: dict[str, Any],
    ) -> ToolExecutionResult | None:
        fingerprint = build_approval_request_fingerprint(normalized_args)
        resource = self._safe_public_text(getattr(self.adapter, "server_id", ""), limit=160)
        device = self._safe_public_text(getattr(self.adapter, "provider_id", ""), limit=160)
        if self.approval_store is not None:
            grant = self.approval_store.resolve_grant(
                profile_user_id=str(context.profile_user_id or ""),
                session_id=str(context.session_id or ""),
                capability_id=self.tool_type,
                action_id=self.tool_type,
                resource=resource,
                device=device,
                fingerprint=fingerprint,
                authorization_profile_user_id=capcore_authorization_profile_user_id(context),
            )
            if grant is not None:
                return None
        request_id = self._create_approval_request(
            decision=decision,
            context=context,
            normalized_args=normalized_args,
            fingerprint=fingerprint,
            resource=resource,
            device=device,
        )
        return self._approval_required(
            decision=decision,
            context=context,
            request_id=request_id,
        )

    def _create_approval_request(
        self,
        *,
        decision: Any,
        context: ToolExecutionContext,
        normalized_args: dict[str, Any],
        fingerprint: str,
        resource: str,
        device: str,
    ) -> str:
        if self.approval_store is None:
            return ""
        request = getattr(decision, "request", None)
        preview = dict(getattr(request, "args_preview", None) or normalized_args)
        risk = self._safe_public_text(getattr(self.descriptor, "risk", ""), limit=40) or "medium"
        result = self.approval_store.create_request(
            profile_user_id=str(context.profile_user_id or ""),
            session_id=str(context.session_id or ""),
            payload={
                "capabilityId": self.tool_type,
                "actionId": self.tool_type,
                "risk": risk,
                "approvalMode": "ask_each_time",
                "title": f"{self._source_label()}需要确认",
                "summary": f"Akane 想执行 {self.tool_type}。",
                "approvalReason": self._safe_public_text(
                    getattr(decision, "reason", ""),
                    limit=120,
                )
                or "requires_confirmation",
                "payloadPreview": preview,
                "requestFingerprint": fingerprint,
                "resource": resource,
                "deviceId": device,
                "authorizationProfileUserId": capcore_authorization_profile_user_id(context),
            },
        )
        return str(result.get("requestId") or "") if result.get("ok") else ""

    def _capability_result_provider(self, result: Any) -> str:
        content = getattr(result, "content", None)
        if not isinstance(content, Mapping):
            return ""
        candidates = [content.get("provider")]
        nested = content.get("result")
        if isinstance(nested, Mapping):
            candidates.append(nested.get("provider"))
        for value in candidates:
            provider = self._safe_public_text(value, limit=80).strip().lower()
            if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,79}", provider):
                return provider
        return ""

    def _finalize_execution_result(
        self,
        execution_result: ToolExecutionResult,
        *,
        capability_result: Any,
        context: ToolExecutionContext,
        model_material: str | None = None,
    ) -> ToolExecutionResult:
        del capability_result, context, model_material
        return execution_result

    def _approval_required(
        self,
        *,
        decision: Any,
        context: ToolExecutionContext,
        request_id: str = "",
    ) -> ToolExecutionResult:
        event = capcore_approval_required_event(
            decision=decision,
            capability_id=self.tool_type,
            action_id=self.tool_type,
            title=f"{self._source_label()}需要确认",
            summary=f"Akane 想执行一个{self._source_label()}。",
            client_mode=context.client_mode,
        )
        if request_id:
            event["requestId"] = request_id
        if request_id:
            followup = (
                f"这个{self._source_label()}需要用户确认，审批请求已经创建。"
                "主人可在 QQ 发送 /approve 放行最新请求，随后让你继续并重试同一调用；"
                "获批前不要声称已经完成。"
            )
        else:
            followup = (
                f"这个{self._source_label()}需要用户确认，但宿主没有创建出可审批请求。"
                "请如实说明审批入口不可用，不要声称已经完成。"
            )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[event],
            followup_context=followup,
            capability_result=CapabilityResult(
                is_error=True, status="approval_required", reason="capability_approval_required",
                content={"approval_request_id": request_id},
            ),
            state_updates={
                "adapter_capability_status": "approval_required",
                "adapter_capability_id": self.tool_type,
                "adapter_capability_approval_request_id": request_id,
            },
        )

    def _validation_failed(self, validation: Any) -> ToolExecutionResult:
        first = validation.errors[0] if validation.errors else None
        reason = self._safe_public_text(getattr(first, "code", "") or "validation_error", limit=80)
        errors = [error.as_dict() for error in validation.errors]
        diagnostics = {"errors": errors, "input_schema": dict(self.tool_spec().input_schema or {}), "executed": False}
        error_text = json.dumps(errors, ensure_ascii=False, separators=(",", ":"))
        return ToolExecutionResult(
            tool_type=self.tool_type,
            capability_result=CapabilityResult(
                is_error=True, status="validation_error", reason=reason, content=diagnostics,
            ),
            stream_events=[
                {
                    "type": "adapter_capability_failed",
                    "capabilityId": self.tool_type,
                    "status": "validation_error",
                    "reason": reason,
                    "errors": errors,
                }
            ],
            followup_context=(
                f"{self._source_label()}参数没有通过校验：{error_text}。"
                f"当前输入契约：{json.dumps(diagnostics['input_schema'], ensure_ascii=False, separators=(',', ':'))}。这次调用没有执行。"
                "请根据这些结构化错误和工具 schema 修正后再调用，不要声称已经完成。"
            ),
            state_updates={
                "adapter_capability_status": "validation_error",
                "adapter_capability_id": self.tool_type,
                "adapter_capability_reason": reason,
            },
        )

    def _blocked_by_policy(self, reason: str) -> ToolExecutionResult:
        safe_reason = self._safe_public_text(reason, limit=120) or "capability_blocked_by_policy"
        return ToolExecutionResult(
            tool_type=self.tool_type,
            capability_result=CapabilityResult(is_error=True, status="blocked", reason=safe_reason),
            stream_events=[
                {
                    "type": "adapter_capability_failed",
                    "capabilityId": self.tool_type,
                    "status": "blocked",
                    "reason": safe_reason,
                }
            ],
            followup_context=(
                f"这个{self._source_label()}被当前能力策略阻止，原因：{safe_reason}；这次调用没有执行。"
                "此结果仅适用于这次调用，不表示所有工具都不可用。可根据原因和当前授权继续其他可执行步骤；"
                "需要用户处理的配置或授权应说明具体原因，不要把调整参数当作绕过权限的方法。"
            ),
            state_updates={
                "adapter_capability_status": "blocked",
                "adapter_capability_id": self.tool_type,
                "adapter_capability_reason": safe_reason,
            },
        )

    def _failure(self, reason: str) -> ToolExecutionResult:
        safe_reason = self._safe_public_text(reason, limit=120) or "adapter_invoke_failed"
        return ToolExecutionResult(
            tool_type=self.tool_type,
            capability_result=CapabilityResult(is_error=True, status="error", reason=safe_reason),
            stream_events=[
                {
                    "type": "adapter_capability_failed",
                    "capabilityId": self.tool_type,
                    "status": "error",
                    "reason": safe_reason,
                }
            ],
            followup_context=f"{self._source_label()}调用失败：{safe_reason}。不要假装已经完成。",
            state_updates={
                "adapter_capability_status": "error",
                "adapter_capability_reason": safe_reason,
            },
        )

    def _failure_from_exception(self, error: BaseException, *, fallback: str) -> ToolExecutionResult:
        reason = str(error or "").strip() or fallback
        if "mcp" not in self._source_label().lower():
            return self._failure(reason)
        safe_reason = mcp_failure_reason(error, fallback=fallback)
        diagnostic = build_mcp_failure_diagnostic(error, stage="tool_call")
        feedback = (
            f"MCP 工具调用失败：{safe_reason}。"
            f"诊断：{json.dumps(diagnostic, ensure_ascii=False, separators=(',', ':'))}。"
            "不要假装已经完成；按 recommendedAction 调整后再重试。"
        )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            capability_result=CapabilityResult(
                is_error=True, status="error", reason=safe_reason, content={"diagnostic": diagnostic},
            ),
            stream_events=[
                {
                    "type": "adapter_capability_failed",
                    "capabilityId": self.tool_type,
                    "status": "error",
                    "reason": safe_reason,
                    "diagnosticCategory": diagnostic["category"],
                }
            ],
            followup_context=feedback,
            followup_envelope=ToolFollowupEnvelope(
                content=feedback,
                producer_bounded=True,
                complete=True,
                diagnostics=diagnostic,
            ),
            state_updates={
                "adapter_capability_status": "error",
                "adapter_capability_id": self.tool_type,
                "adapter_capability_reason": safe_reason,
            },
        )

    def _format_capability_result(self, result: Any) -> str:
        content = getattr(result, "content", None)
        if isinstance(content, Mapping):
            pieces: list[str] = []
            raw_content = content.get("content")
            if isinstance(raw_content, list):
                for item in raw_content:
                    if not isinstance(item, Mapping):
                        continue
                    if str(item.get("type") or "").strip() == "text":
                        text = self._safe_model_result_text(item.get("text"))
                        if text:
                            pieces.append(text)
                    elif item.get("type"):
                        pieces.append(f"[{self._safe_public_text(item.get('type'), limit=40)} content]")
            if not pieces:
                pieces.append(
                    self._safe_model_result_text(json.dumps(content, ensure_ascii=False, default=str))
                )
            body = "\n".join(piece for piece in pieces if piece).strip()
        else:
            body = self._safe_model_result_text(content)
        if not body:
            body = f"({self._source_label()}没有返回可读内容。)"
        if bool(getattr(result, "is_error", False)):
            status = self._safe_public_text(getattr(result, "status", ""), limit=80) or "error"
            reason = self._safe_public_text(getattr(result, "reason", ""), limit=120)
            label = f"{status}/{reason}" if reason else status
            return f"{self._source_label()}返回业务错误（{label}）：\n{body}"
        return f"{self._source_label()}返回：\n{body}"

    def _source_label(self) -> str:
        raw = getattr(self.descriptor, "raw", None)
        adapter_name = ""
        if isinstance(raw, Mapping):
            adapter_name = str(raw.get("adapter") or "").strip().lower()
        if not adapter_name:
            adapter_name = str(getattr(self.adapter, "type", "") or "").strip().lower()
        if adapter_name == "python":
            return "本地 Python 能力"
        if "mcp" in adapter_name:
            return "MCP 工具"
        if adapter_name == "plugin":
            return "已安装插件能力"
        return "本地 adapter 能力"

    def _input_schema(self) -> Mapping[str, Any]:
        return self.tool_spec().input_schema

    def _safe_public_text(self, value: Any, *, limit: int) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(
            r"(?i)\b(api[_-]?key|authorization|bearer|cookie|password|secret|token)\s*[:=]\s*[^\s,;]+",
            r"\1=[redacted]",
            text,
        )
        text = re.sub(r"(?i)\bbearer\s+[^\s]+", "Bearer [redacted]", text)
        return re.sub(r"\s+", " ", text).strip()[:limit]

    def _safe_model_result_text(self, value: Any) -> str:
        """Preserve complete MCP evidence while removing credential literals."""

        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(
            r"(?i)\b(api[_-]?key|authorization|bearer|cookie|password|secret|token)\s*[:=]\s*[^\s,;]+",
            r"\1=[redacted]",
            text,
        )
        return re.sub(r"(?i)\bbearer\s+[^\s]+", "Bearer [redacted]", text).strip()

    def _run_coro_blocking(self, awaitable: Any) -> Any:
        if not inspect.isawaitable(awaitable):
            return awaitable
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            has_running_loop = False
        else:
            has_running_loop = True
        if not has_running_loop:
            # Run outside the ``get_running_loop`` exception handler so a real
            # adapter failure does not inherit "no running event loop" as a
            # misleading diagnostic context.
            return asyncio.run(awaitable)
        result_box: dict[str, Any] = {}
        error_box: dict[str, BaseException] = {}

        def runner() -> None:
            try:
                result_box["result"] = asyncio.run(awaitable)
            except BaseException as exc:
                error_box["error"] = exc

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        if error_box:
            raise error_box["error"]
        return result_box.get("result")
