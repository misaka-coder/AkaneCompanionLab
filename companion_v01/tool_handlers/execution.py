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

from dataclasses import replace
from typing import Any

import config

from capcore import PermissionDecision

from ..capcore_runtime import (
    approval_required_event,
    manual_permission_request,
    resolve_permission_for_profile,
)
from ..capability_approval import build_approval_request_fingerprint
from ..execution_resources import ExecutionResourceBridge
from ..execution_run import (
    ExecutionRunOwner,
    execute_exec_cancel,
    execute_exec_run,
    execute_exec_status,
    new_run_id,
)
from ..execution_specs import (
    ARTIFACT_STATUS_NOT_REQUESTED,
    ARTIFACT_STATUS_REGISTRATION_FAILED,
    EXEC_CANCEL_TOOL_SPEC,
    EXEC_RUN_TOOL_SPEC,
    EXEC_STATUS_CANCELLED,
    EXEC_STATUS_COMPLETED,
    EXEC_STATUS_FAILED,
    EXEC_STATUS_RUNNING,
    EXEC_STATUS_TOOL_SPEC,
    EXEC_STATUS_TIMED_OUT,
)
from .core import BaseToolHandler, ToolExecutionContext, ToolExecutionResult, ToolFollowupEnvelope


class _ExecToolHandlerBase(BaseToolHandler):
    """Shared provider / owner / result plumbing for the three exec tools."""

    # The execution specs are complete canonical schemas.  Advertising them
    # through the provider's native tool channel keeps argument validation at
    # the protocol boundary instead of asking the model to reproduce the
    # compatibility JSON shape from prose.
    policy_accepted_native_tool = True

    def __init__(
        self,
        *,
        execution_provider: Any,
        config_base_dir: Any = None,
        approval_store: Any = None,
        resource_bridge: ExecutionResourceBridge | None = None,
    ) -> None:
        self.execution_provider = execution_provider
        self.config_base_dir = config_base_dir
        self.approval_store = approval_store
        self.resource_bridge = resource_bridge

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
                **({"wait_seconds": 30} if str(mapped.event_status or "") == EXEC_STATUS_RUNNING else {}),
            }
            if next_cursor
            else None
        )
        state_updates: dict[str, Any] = {
            "capability_execution": {
                "tool_type": self.tool_type,
                "status": str(mapped.event_status or ""),
                "reason": str(mapped.reason or ""),
                "run_id": str(data.get("run_id") or ""),
                "exit_code": data.get("exit_code"),
                "next_cursor": next_cursor,
                "output_ref": str(data.get("output_ref") or "") or None,
                "started_at": data.get("started_at"),
                "finished_at": data.get("finished_at"),
                "observed_at": data.get("observed_at"),
            }
        }
        if data.get("generated_resources"):
            state_updates["capability_execution"]["generated_resources"] = list(data.get("generated_resources") or [])
        if data.get("artifact_status"):
            state_updates["capability_execution"]["artifact_status"] = str(data.get("artifact_status") or "")
        if data.get("next_action"):
            state_updates["capability_execution"]["next_action"] = dict(data.get("next_action") or {})
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
            state_updates=state_updates,
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

    def _resource_rejected(self, reason: str) -> ToolExecutionResult:
        clean_reason = str(reason or "execution_resources_rejected")
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
                f"这次命令没有执行：资源暂存被拒绝（{clean_reason}）。请检查资源句柄、目标相对路径与输出声明后调整，"
                "不要声称命令已经执行或文件已经生成。"
            ),
            state_updates={
                "capability_execution": {
                    "tool_type": self.tool_type,
                    "status": "blocked",
                    "reason": clean_reason,
                }
            },
        )

    def _enrich_resources(self, mapped: Any, registration: dict[str, Any]) -> Any:
        if not isinstance(registration, dict):
            return mapped
        resources = list(registration.get("generated_resources") or [])
        artifact_status = str(registration.get("artifact_status") or ARTIFACT_STATUS_NOT_REQUESTED)
        data = dict(mapped.data or {})
        data["generated_resources"] = resources
        data["artifact_status"] = artifact_status
        feedback = str(mapped.model_feedback or "")
        if resources:
            labels = "、".join(f"{item.get('handle')}({item.get('name')})" for item in resources)
            targets = [item.get("handle") for item in resources]
            data["next_action"] = {"tool": "send_file", "targets": targets}
            if artifact_status == ARTIFACT_STATUS_REGISTRATION_FAILED:
                reason = str(registration.get("reason") or "output_registration_incomplete")
                feedback = (
                    f"{feedback}\n仅部分输出登记成功：{labels}；其余输出登记失败（{reason}）。"
                    "需要交付已成功登记的文件时调用 send_file(targets=[...])；不要声称全部产物都已生成或交付。"
                )
            else:
                feedback = (
                    f"{feedback}\n已登记生成资源：{labels}。需要交付时调用 send_file(targets=[...])，"
                    "不要自动替用户发送。"
                )
        elif artifact_status == ARTIFACT_STATUS_REGISTRATION_FAILED:
            reason = str(registration.get("reason") or "unknown")
            feedback = (
                f"{feedback}\n命令已完成，但输出登记失败（{reason}）。请调整输出声明或告知用户交付失败，"
                "不要声称文件已经生成或已经交付。"
            )
        return replace(mapped, data=data, model_feedback=feedback)


class ExecRunToolHandler(_ExecToolHandlerBase):
    """``exec_run``: run a command in the trusted execution workspace."""

    tool_type = "exec_run"

    def tool_spec(self):
        return EXEC_RUN_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- exec_run：以宿主用户权限在受信任执行工作区运行命令或脚本；命令参数字段名是 command（不是 cmd）。"
            "cwd 只能用工作区相对路径或挂载别名，"
            "不接受绝对路径；环境变量由宿主按白名单注入，不接受环境变量参数。短命令直接返回结果；"
            "命令仍在执行时返回 run_id 与 running 状态，用 exec_status 查询进度、exec_cancel 停止；"
            "输出超过限额时通过 next_cursor 增量读取。需要命令读取已有材料时，用 input_resources 声明句柄"
            "（使用材料索引实际显示的 doc_* / img_* / aud_* / vid_* / gen_*）与命令工作区内相对路径 as，"
            "输入会复制进本次运行的独立工作区；"
            "需要命令产出文件时，用 output_globs 声明输出相对路径，命令完成后会自动登记为 gen_*，"
            "然后用 send_file 交付。高风险命令会按当前用户策略请求确认。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        # ``command`` is the canonical persisted/wire field.  Accept the
        # widespread ``cmd`` spelling only at the compatibility JSON ingress,
        # then immediately canonicalize it so approval fingerprints, traces
        # and every downstream component still have one representation.
        canonical_command = str(value.get("command") or "").strip()
        compatibility_command = str(value.get("cmd") or "").strip()
        if canonical_command and compatibility_command and canonical_command != compatibility_command:
            return None
        command = canonical_command or compatibility_command
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
        input_resources = self._normalize_input_resources(value.get("input_resources"))
        output_globs = self._normalize_output_globs(value.get("output_globs"))
        if value.get("input_resources") is not None and input_resources is None:
            return None
        if value.get("output_globs") is not None and output_globs is None:
            return None
        if str(value.get("cwd") or "").strip() and (input_resources or output_globs):
            # Resource mode owns an isolated per-run cwd. Silently ignoring a
            # caller-supplied cwd would make the approved request differ from
            # the command actually executed.
            return None
        if input_resources:
            normalized["input_resources"] = input_resources
        if output_globs:
            normalized["output_globs"] = output_globs
        return normalized

    @staticmethod
    def _normalize_input_resources(value: Any) -> list[dict[str, Any]] | None:
        if value is None:
            return []
        if not isinstance(value, list) or len(value) > 8:
            return None
        normalized: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                return None
            handle = str(item.get("handle") or "").strip()
            target = str(item.get("as") or "").strip()
            if not handle or not target or len(handle) > 120 or len(target) > 512:
                return None
            normalized.append({"handle": handle, "as": target})
        return normalized

    @staticmethod
    def _normalize_output_globs(value: Any) -> list[str] | None:
        if value is None:
            return []
        if not isinstance(value, list) or len(value) > 32:
            return None
        normalized: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if not text or len(text) > 1024:
                return None
            normalized.append(text)
        return normalized

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        provider = self.execution_provider
        if provider is None:
            return self._unavailable_result("execution_provider_unconfigured")
        command = str(call.get("command") or "").strip()
        cwd = str(call.get("cwd") or "").strip()
        input_resources = call.get("input_resources")
        output_globs = call.get("output_globs")
        args_preview = {"command": command, "cwd": cwd}
        if input_resources:
            args_preview["input_resources"] = input_resources
        if output_globs:
            args_preview["output_globs"] = output_globs
        decision = self._permission_decision(context, args_preview)
        if not decision.allowed:
            if decision.requires_user_decision:
                return self._ask_or_redeem(context, decision, call)
            return self._blocked(str(decision.reason or "capability_disabled_by_policy"))
        return self._execute_command(provider, call, context)

    def _execute_command(self, provider: Any, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        command = str(call.get("command") or "").strip()
        cwd = str(call.get("cwd") or "").strip()
        input_resources = call.get("input_resources") or []
        output_globs = call.get("output_globs") or []
        bridge = self.resource_bridge
        staged = None
        run_id = ""
        if input_resources or output_globs:
            if bridge is None:
                return self._resource_rejected("execution_resources_unconfigured")
            run_id = new_run_id()
            staged = bridge.stage_inputs(
                run_id=run_id,
                owner=self._owner(context),
                input_resources=input_resources,
                output_globs=output_globs,
            )
            if not bool(staged.get("ok")):
                return self._resource_rejected(str(staged.get("reason") or "execution_resources_staging_failed"))
            cwd = str(staged.get("cwd_relpath") or cwd)
        mapped = execute_exec_run(
            provider,
            owner=self._owner(context),
            command=command,
            cwd=cwd,
            timeout_seconds=call.get("timeout_seconds"),
            initial_wait_seconds=call.get("initial_wait_seconds"),
            run_id=run_id,
        )
        if staged is not None and run_id:
            owner = self._owner(context)
            if mapped.event_status == EXEC_STATUS_COMPLETED:
                registration = bridge.register_outputs(run_id=run_id, owner=owner)
                mapped = self._enrich_resources(mapped, registration)
            elif mapped.event_status in {EXEC_STATUS_FAILED, EXEC_STATUS_TIMED_OUT, EXEC_STATUS_CANCELLED}:
                registration = bridge.finalize_without_outputs(
                    run_id=run_id,
                    owner=owner,
                    reason=f"command_{mapped.event_status}",
                )
                mapped = self._enrich_resources(mapped, registration)
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
        arguments: dict[str, Any] = {}
        for key in ("command", "cwd", "timeout_seconds", "initial_wait_seconds"):
            if call.get(key) is not None:
                arguments[key] = call.get(key)
        if call.get("input_resources") is not None:
            arguments["input_resources"] = call.get("input_resources")
        if call.get("output_globs") is not None:
            arguments["output_globs"] = call.get("output_globs")
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
            "每次返回自 cursor 之后的新输出和 next_cursor，任务终止且输出读完后 next_cursor 为 null；"
            "运行中暂无新输出时可用 wait_seconds 最多等待 30 秒，有新输出或终态会提前返回。"
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
        if value.get("wait_seconds") is not None:
            try:
                normalized["wait_seconds"] = int(value.get("wait_seconds"))
            except (TypeError, ValueError):
                return None
        return normalized

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        provider = self.execution_provider
        if provider is None:
            return self._unavailable_result("execution_provider_unconfigured")
        run_id = str(call.get("run_id") or "").strip()
        mapped = execute_exec_status(
            provider,
            owner=self._owner(context),
            run_id=run_id,
            cursor=str(call.get("cursor") or "").strip() or None,
            wait_seconds=call.get("wait_seconds"),
        )
        bridge = self.resource_bridge
        owner = self._owner(context)
        if bridge is not None and run_id and bridge.has_binding(run_id=run_id, owner=owner):
            if mapped.event_status == EXEC_STATUS_COMPLETED:
                registration = bridge.register_outputs(run_id=run_id, owner=owner)
                mapped = self._enrich_resources(mapped, registration)
            elif mapped.event_status in {EXEC_STATUS_FAILED, EXEC_STATUS_TIMED_OUT, EXEC_STATUS_CANCELLED}:
                registration = bridge.finalize_without_outputs(
                    run_id=run_id,
                    owner=owner,
                    reason=f"command_{mapped.event_status}",
                )
                mapped = self._enrich_resources(mapped, registration)
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
        run_id = str(call.get("run_id") or "").strip()
        mapped = execute_exec_cancel(
            provider,
            owner=self._owner(context),
            run_id=run_id,
        )
        bridge = self.resource_bridge
        owner = self._owner(context)
        if bridge is not None and run_id and bridge.has_binding(run_id=run_id, owner=owner):
            if mapped.event_status == EXEC_STATUS_CANCELLED:
                registration = bridge.finalize_without_outputs(
                    run_id=run_id,
                    owner=owner,
                    reason="command_cancelled",
                )
                mapped = self._enrich_resources(mapped, registration)
            elif mapped.event_status == "already_ended" and mapped.reason == EXEC_STATUS_COMPLETED:
                registration = bridge.register_outputs(run_id=run_id, owner=owner)
                mapped = self._enrich_resources(mapped, registration)
            elif mapped.event_status == "already_ended" and mapped.reason in {
                EXEC_STATUS_FAILED,
                EXEC_STATUS_TIMED_OUT,
                EXEC_STATUS_CANCELLED,
            }:
                registration = bridge.finalize_without_outputs(
                    run_id=run_id,
                    owner=owner,
                    reason=f"command_{mapped.reason}",
                )
                mapped = self._enrich_resources(mapped, registration)
        return self._mapped_result(mapped)
