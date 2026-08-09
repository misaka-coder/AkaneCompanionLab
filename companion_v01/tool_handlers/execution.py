"""Built-in execution tool handlers (Phase 3 wiring).

These handlers bind an ``ExecutionProvider`` (Phase 2 ``TrustedLocalExecutor``
or a future cloud/satellite provider) onto the existing tool chain:

* CapabilitySelection / schema selects the three fixed tools;
* the executor broker dedupes each invocation (invocation-id ledger);
* the capcore permission chain gates ``exec_run`` (high risk, ``confirm=always``)
  with allow / ask / deny;
* results map onto the existing ``ToolExecutionResult`` / ``ToolResultEnvelope``
  shapes so the current tool trace and MemCore settlement keep working.

The three tools are only registered when the host supplies a provider; without
one they are absent from the schema (execution disabled -> the model never sees
them). Runtime readiness (workspace missing, provider offline) is a structured
``unavailable`` result, never a schema change.

Permission scope in this slice: ``exec_run`` is gated by the per-profile capcore
policy (user + risk), and one-shot approval grants bind its exact arguments,
session and provider. ``exec_status`` / ``exec_cancel`` are owner-scoped to the
profile/session/provider that started the run and do not ask.
"""

from __future__ import annotations

from typing import Any

import config

from capcore import PermissionDecision

from ..capcore_runtime import (
    approval_required_event,
    manual_permission_request,
    resolve_permission_for_profile,
)
from ..capability_approval import build_approval_request_fingerprint
from ..execution_run import (
    ExecutionRunOwner,
    execute_exec_cancel,
    execute_exec_run,
    execute_exec_status,
)
from ..execution_specs import (
    EXEC_CANCEL_TOOL_SPEC,
    EXEC_RUN_TOOL_SPEC,
    EXEC_STATUS_TOOL_SPEC,
)
from .core import BaseToolHandler, ToolExecutionContext, ToolExecutionResult, ToolFollowupEnvelope


class _ExecToolHandlerBase(BaseToolHandler):
    """Shared provider / owner / result plumbing for the three exec tools."""

    def __init__(self, *, execution_provider: Any, config_base_dir: Any = None, approval_store: Any = None) -> None:
        self.execution_provider = execution_provider
        self.config_base_dir = config_base_dir
        self.approval_store = approval_store

    def _owner(self, context: ToolExecutionContext) -> ExecutionRunOwner:
        provider = self.execution_provider
        return ExecutionRunOwner(
            profile_user_id=str(context.profile_user_id or ""),
            session_id=str(context.session_id or ""),
            provider_id=str(getattr(provider, "provider_id", "local") or "local"),
        )

    def capability_status(self, **_kwargs: Any) -> dict[str, Any]:
        """Existing ServerLocalOfferIndex readiness probe (mirrors satellite)."""
        provider = self.execution_provider
        if provider is None:
            return {"enabled": False, "status": "unavailable", "reason": "execution_provider_unconfigured"}
        try:
            availability = provider.availability()
        except Exception:
            return {"enabled": False, "status": "unavailable", "reason": "availability_check_failed"}
        if not availability.enabled:
            return {"enabled": False, "status": "unavailable", "reason": str(availability.reason or "execution_unavailable")}
        return {"enabled": True, "status": str(availability.status or "ready") or "ready", "reason": ""}

    def _unavailable_result(self, reason: str) -> ToolExecutionResult:
        clean_reason = str(reason or "execution_provider_unconfigured")
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "capability_execution_result",
                    "tool_type": self.tool_type,
                    "status": "unavailable",
                    "reason": clean_reason,
                }
            ],
            followup_context=(
                f"<capability_unavailable>当前没有配置可用的执行器：{clean_reason}。"
                "请直接说明这次不能执行，不要假装已经执行。</capability_unavailable>"
            ),
            state_updates={
                "capability_execution": {
                    "tool_type": self.tool_type,
                    "status": "unavailable",
                    "reason": clean_reason,
                }
            },
        )

    def _mapped_result(self, mapped: Any) -> ToolExecutionResult:
        data = dict(mapped.data or {})
        next_cursor = str(data.get("next_cursor") or "").strip() or None
        continuation = (
            {
                "type": "exec_status",
                "run_id": str(data.get("run_id") or ""),
                "cursor": next_cursor,
            }
            if next_cursor
            else None
        )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[dict(mapped.event)],
            followup_context=str(mapped.model_feedback or ""),
            followup_envelope=ToolFollowupEnvelope(
                content=str(mapped.model_feedback or ""),
                producer_bounded=True,
                complete=next_cursor is None,
                continuation=continuation,
            ),
            state_updates={
                "capability_execution": {
                    "tool_type": self.tool_type,
                    "status": str(mapped.event_status or ""),
                    "reason": str(mapped.reason or ""),
                    "run_id": str(data.get("run_id") or ""),
                    "exit_code": data.get("exit_code"),
                    "next_cursor": next_cursor,
                    "output_ref": str(data.get("output_ref") or "") or None,
                }
            },
        )

    def _approval_required(
        self,
        context: ToolExecutionContext,
        decision: PermissionDecision,
        *,
        request_id: str = "",
        fingerprint: str = "",
    ) -> ToolExecutionResult:
        event = approval_required_event(
            capability_id=self.tool_type,
            action_id=self.tool_type,
            title="执行命令需要确认",
            summary="Akane 想以宿主用户权限运行一条命令。",
            client_mode=str(context.client_mode or ""),
            decision=decision,
        )
        if request_id:
            event["requestId"] = request_id
        if fingerprint:
            event["requestFingerprint"] = fingerprint
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[event],
            followup_context=(
                "这个命令需要用户确认后才能执行；请自然说明需要用户在能力审批中允许后再执行，不要声称已经完成。"
            ),
            state_updates={
                "capability_execution": {
                    "tool_type": self.tool_type,
                    "status": "approval_required",
                    "reason": "requires_user_decision",
                }
            },
        )

    def _blocked(self, reason: str) -> ToolExecutionResult:
        clean_reason = str(reason or "capability_disabled_by_policy")
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "capability_execution_result",
                    "tool_type": self.tool_type,
                    "status": "blocked",
                    "reason": clean_reason,
                }
            ],
            followup_context=(
                f"命令执行已被当前能力策略阻止（{clean_reason}）。请自然说明无法执行，不要假装已经完成。"
            ),
            state_updates={
                "capability_execution": {
                    "tool_type": self.tool_type,
                    "status": "blocked",
                    "reason": clean_reason,
                }
            },
        )


class ExecRunToolHandler(_ExecToolHandlerBase):
    """``exec_run``: run a command in the trusted execution workspace."""

    tool_type = "exec_run"

    def tool_spec(self):
        return EXEC_RUN_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- exec_run：以宿主用户权限在受信任执行工作区运行命令或脚本。cwd 只能用工作区相对路径或挂载别名，"
            "不接受绝对路径；环境变量由宿主按白名单注入，不接受环境变量参数。短命令直接返回结果；"
            "命令仍在执行时返回 run_id 与 running 状态，用 exec_status 查询进度、exec_cancel 停止；"
            "输出超过限额时通过 next_cursor 增量读取。高风险命令会按当前用户策略请求确认。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        command = str(value.get("command") or "").strip()
        if not command:
            return None
        normalized: dict[str, Any] = {
            "type": self.tool_type,
            "command": command,
            "cwd": str(value.get("cwd") or "").strip(),
        }
        if value.get("timeout_seconds") is not None:
            try:
                normalized["timeout_seconds"] = int(value.get("timeout_seconds"))
            except (TypeError, ValueError):
                return None
        if value.get("initial_wait_seconds") is not None:
            try:
                normalized["initial_wait_seconds"] = int(value.get("initial_wait_seconds"))
            except (TypeError, ValueError):
                return None
        return normalized

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        provider = self.execution_provider
        if provider is None:
            return self._unavailable_result("execution_provider_unconfigured")
        command = str(call.get("command") or "").strip()
        cwd = str(call.get("cwd") or "").strip()
        decision = self._permission_decision(context, {"command": command, "cwd": cwd})
        if not decision.allowed:
            if decision.requires_user_decision:
                return self._ask_or_redeem(context, decision, call)
            return self._blocked(str(decision.reason or "capability_disabled_by_policy"))
        return self._execute_command(provider, call, context)

    def _execute_command(self, provider: Any, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        mapped = execute_exec_run(
            provider,
            owner=self._owner(context),
            command=str(call.get("command") or "").strip(),
            cwd=str(call.get("cwd") or "").strip(),
            timeout_seconds=call.get("timeout_seconds"),
            initial_wait_seconds=call.get("initial_wait_seconds"),
        )
        return self._mapped_result(mapped)

    def _ask_or_redeem(
        self,
        context: ToolExecutionContext,
        decision: PermissionDecision,
        call: dict[str, Any],
    ) -> ToolExecutionResult:
        """Redeem a previously approved grant, else create an approval request.

        The grant is bound to capability/action, resource (cwd), device
        (provider) and a request fingerprint of the exact arguments, so an
        approval for one command never silently covers a different one.
        """
        fingerprint = self._fingerprint(call)
        resource = str(call.get("cwd") or "").strip()
        device = str(getattr(self.execution_provider, "provider_id", "local") or "local")
        if self.approval_store is not None:
            grant = self.approval_store.resolve_grant(
                profile_user_id=str(context.profile_user_id or ""),
                session_id=str(context.session_id or ""),
                capability_id=self.tool_type,
                action_id=self.tool_type,
                resource=resource,
                device=device,
                fingerprint=fingerprint,
            )
            if grant is not None:
                return self._execute_command(self.execution_provider, call, context)
        request_id = self._create_approval_request(context, decision, fingerprint, resource, device)
        return self._approval_required(context, decision, request_id=request_id, fingerprint=fingerprint)

    def _fingerprint(self, call: dict[str, Any]) -> str:
        arguments = {
            key: call.get(key)
            for key in ("command", "cwd", "timeout_seconds", "initial_wait_seconds")
            if call.get(key) is not None
        }
        return build_approval_request_fingerprint(arguments)

    def _create_approval_request(
        self,
        context: ToolExecutionContext,
        decision: PermissionDecision,
        fingerprint: str,
        resource: str,
        device: str,
    ) -> str:
        if self.approval_store is None:
            return ""
        request = decision.request
        preview = dict(getattr(request, "args_preview", None) or {}) if request is not None else {}
        result = self.approval_store.create_request(
            profile_user_id=str(context.profile_user_id or ""),
            session_id=str(context.session_id or ""),
            payload={
                "capabilityId": self.tool_type,
                "actionId": self.tool_type,
                "risk": "high",
                "approvalMode": "ask_each_time",
                "title": "执行命令需要确认",
                "summary": "Akane 想以宿主用户权限运行一条命令。",
                "approvalReason": "command_execution_requires_confirmation",
                "payloadPreview": preview,
                "requestFingerprint": fingerprint,
                "resource": resource,
                "deviceId": device,
            },
        )
        if not result.get("ok"):
            return ""
        return str(result.get("requestId") or "")

    def _permission_decision(self, context: ToolExecutionContext, args_preview: dict[str, Any]) -> PermissionDecision:
        request = manual_permission_request(
            context=context,
            required=True,
            capability_id=self.tool_type,
            display_name="exec_run",
            risk="high",
            confirm="always",
            effects=("command_exec",),
            reason="command_execution_requires_confirmation",
            args_preview=args_preview,
        )
        return resolve_permission_for_profile(
            request,
            base_dir=self.config_base_dir or getattr(config, "DATA_DIR", "users_data"),
            profile_user_id=str(context.profile_user_id or ""),
        )


class ExecStatusToolHandler(_ExecToolHandlerBase):
    """``exec_status``: query a run and read incremental output by cursor."""

    tool_type = "exec_status"

    def tool_spec(self):
        return EXEC_STATUS_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- exec_status：按 run_id 查询已启动命令的状态，并用 cursor 增量读取输出；"
            "每次返回自 cursor 之后的新输出和 next_cursor，任务终止且输出读完后 next_cursor 为 null。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        run_id = str(value.get("run_id") or "").strip()
        if not run_id:
            return None
        normalized: dict[str, Any] = {"type": self.tool_type, "run_id": run_id}
        cursor = value.get("cursor")
        if cursor is not None:
            normalized["cursor"] = str(cursor).strip()
        return normalized

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        provider = self.execution_provider
        if provider is None:
            return self._unavailable_result("execution_provider_unconfigured")
        mapped = execute_exec_status(
            provider,
            owner=self._owner(context),
            run_id=str(call.get("run_id") or "").strip(),
            cursor=str(call.get("cursor") or "").strip() or None,
        )
        return self._mapped_result(mapped)


class ExecCancelToolHandler(_ExecToolHandlerBase):
    """``exec_cancel``: stop a run after the provider confirms termination."""

    tool_type = "exec_cancel"

    def tool_spec(self):
        return EXEC_CANCEL_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return "- exec_cancel：按 run_id 停止正在运行的命令；只有执行器确认进程组停止后才返回成功。"

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        run_id = str(value.get("run_id") or "").strip()
        if not run_id:
            return None
        return {"type": self.tool_type, "run_id": run_id}

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        provider = self.execution_provider
        if provider is None:
            return self._unavailable_result("execution_provider_unconfigured")
        mapped = execute_exec_cancel(
            provider,
            owner=self._owner(context),
            run_id=str(call.get("run_id") or "").strip(),
        )
        return self._mapped_result(mapped)
