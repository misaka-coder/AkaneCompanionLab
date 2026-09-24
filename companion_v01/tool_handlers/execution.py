"""Built-in execution tool handlers (Phase 3 wiring).

These handlers bind an ``ExecutionProvider`` (Phase 2 ``TrustedLocalExecutor``
or a future cloud/satellite provider) onto the existing tool chain:

* CapabilitySelection / schema selects the execution tools;
* the executor broker dedupes each invocation (invocation-id ledger);
* the capcore permission chain gates ``exec_run`` (high risk, ``confirm=always``)
  with allow / ask / deny;
* results map onto the existing ``ToolExecutionResult`` / ``ToolResultEnvelope``
  shapes so the current tool trace and MemCore settlement keep working.

Execution tools are only registered when the host supplies a provider; without
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
from pathlib import Path
from typing import Any

import config

from capcore import PermissionDecision

from ..capcore_runtime import (
    authorization_profile_user_id,
    approval_required_event,
    manual_permission_request,
    resolve_permission_for_profile,
)
from ..capability_approval import build_approval_request_fingerprint
from ..execution_resources import ExecutionResourceBridge, ExecutionResourceScope
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
    EXEC_CWD_MAX_CHARS,
    EXEC_CANCEL_TOOL_SPEC,
    EXEC_INPUT_TOOL_SPEC,
    EXEC_RUN_TOOL_SPEC,
    EXEC_STATUS_CANCELLED,
    EXEC_STATUS_COMPLETED,
    EXEC_STATUS_FAILED,
    EXEC_STATUS_RUNNING,
    EXEC_STATUS_TOOL_SPEC,
    EXEC_STATUS_TIMED_OUT,
)
from ..project_workspace import ProjectWorkspaceError, ProjectWorkspaceService
from .core import BaseToolHandler, ToolExecutionContext, ToolExecutionResult, ToolFollowupEnvelope


class _ExecToolHandlerBase(BaseToolHandler):
    """Shared provider / owner / result plumbing for execution tools."""

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
        project_workspace_service: ProjectWorkspaceService | None = None,
    ) -> None:
        self.execution_provider = execution_provider
        self.config_base_dir = config_base_dir
        self.approval_store = approval_store
        self.resource_bridge = resource_bridge
        self.project_workspace_service = project_workspace_service

    def _owner(self, context: ToolExecutionContext) -> ExecutionRunOwner:
        provider = self.execution_provider
        session_id = str(context.session_id or "")
        request_context = context.request_context if isinstance(context.request_context, dict) else {}
        actor_id = str(request_context.get("actor_stable_id") or "").strip()
        if session_id.startswith("qq_group_shared_") and actor_id.startswith("qq:"):
            # A shared QQ group is one conversation, but a long-running command
            # still belongs to the member who started it.  Keep that host-only
            # ownership detail out of prompts and public run receipts while
            # preventing another member's ordinary message from resuming or
            # cancelling the previous member's process.
            session_id = f"{session_id}\x1f{actor_id}"
        return ExecutionRunOwner(
            profile_user_id=str(context.profile_user_id or ""),
            session_id=session_id,
            provider_id=str(getattr(provider, "provider_id", "local") or "local"),
        )

    def _project_scope(self, context: ToolExecutionContext):
        service = self.project_workspace_service
        if service is None:
            raise ProjectWorkspaceError("project_workspace_unconfigured")
        request_context = context.request_context if isinstance(context.request_context, dict) else {}
        return service.scope_for(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            client_mode=context.client_mode,
            actor_stable_id=str(request_context.get("actor_stable_id") or ""),
            actor_profile_user_id=str(request_context.get("actor_profile_user_id") or ""),
        )

    @staticmethod
    def _resource_scope(context: ToolExecutionContext) -> ExecutionResourceScope:
        """Keep artifacts in the conversation namespace, not run-control scope."""

        return ExecutionResourceScope(
            profile_user_id=str(context.profile_user_id or ""),
            session_id=str(context.session_id or ""),
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

    def working_directory_context(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        client_mode: str,
        actor_stable_id: str = "",
        actor_profile_user_id: str = "",
    ) -> dict[str, str]:
        """Return the exact execution coordinate for the current request tail."""

        service = self.project_workspace_service
        if service is None:
            return {}
        try:
            scope = service.scope_for(
                profile_user_id=profile_user_id,
                session_id=session_id,
                client_mode=client_mode,
                actor_stable_id=actor_stable_id,
                actor_profile_user_id=actor_profile_user_id,
            )
            current = service.current(scope=scope)
        except ProjectWorkspaceError:
            current = None
        if current is None:
            try:
                root = str(self.execution_provider.resolve_workdir(""))
            except Exception:
                root = "execution_root"
            return {"working_directory": root, "project": "none", "workspace_id": ""}
        return {
            "working_directory": str(current.get("working_directory") or ""),
            "project": str(current.get("display_name") or "unnamed"),
            "workspace_id": str(current.get("workspace_id") or ""),
        }

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
        for key in ("max_chars", "actual_chars"):
            if data.get(key) is not None:
                state_updates["capability_execution"][key] = data.get(key)
        for key in ("effective_cwd", "workspace_id"):
            if data.get(key) is not None:
                state_updates["capability_execution"][key] = data.get(key)
        if data.get("generated_resources"):
            state_updates["capability_execution"]["generated_resources"] = list(data.get("generated_resources") or [])
        if data.get("artifact_status"):
            state_updates["capability_execution"]["artifact_status"] = str(data.get("artifact_status") or "")
        if data.get("artifact_reason"):
            state_updates["capability_execution"]["artifact_reason"] = str(data.get("artifact_reason") or "")
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

    @staticmethod
    def _with_execution_location(
        mapped: Any,
        *,
        provider: Any,
        cwd: str,
        workspace_id: str,
    ) -> Any:
        """Attach the one actual command coordinate without duplicating project state."""

        resolver = getattr(provider, "resolve_workdir", None)
        if not callable(resolver):
            return mapped
        try:
            effective_cwd = str(resolver(cwd))
        except Exception:
            return mapped
        data = dict(mapped.data or {})
        data["effective_cwd"] = effective_cwd
        data["workspace_id"] = str(workspace_id or "")
        event = dict(mapped.event or {})
        event["effective_cwd"] = effective_cwd
        if workspace_id:
            event["workspace_id"] = str(workspace_id)
        feedback = str(mapped.model_feedback or "")
        location = f"effective_cwd={effective_cwd}; workspace_id={workspace_id or 'none'}"
        feedback = f"{feedback}\n[{location}]" if feedback else f"[{location}]"
        return replace(mapped, data=data, event=event, model_feedback=feedback)

    def _approval_required(
        self,
        context: ToolExecutionContext,
        decision: PermissionDecision,
        *,
        request_id: str = "",
        fingerprint: str = "",
    ) -> ToolExecutionResult:
        approval_reason, title, summary, followup = self._approval_explanation(context)
        event = approval_required_event(
            capability_id=self.tool_type,
            action_id=self.tool_type,
            title=title,
            summary=summary,
            client_mode=str(context.client_mode or ""),
            decision=decision,
        )
        event["approvalReason"] = approval_reason
        if request_id:
            event["requestId"] = request_id
        if fingerprint:
            event["requestFingerprint"] = fingerprint
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[event],
            followup_context=followup,
            state_updates={
                "capability_execution": {
                    "tool_type": self.tool_type,
                    "status": "approval_required",
                    "reason": approval_reason,
                }
            },
        )

    @staticmethod
    def _approval_explanation(context: ToolExecutionContext) -> tuple[str, str, str, str]:
        profile_user_id = str(context.profile_user_id or "").strip()
        request_context = context.request_context if isinstance(context.request_context, dict) else {}
        actor_profile_user_id = str(request_context.get("actor_profile_user_id") or "").strip()
        shared_group_actor = (
            profile_user_id.startswith("qq_group_shared_")
            and actor_profile_user_id
            and actor_profile_user_id != profile_user_id
        )
        if shared_group_actor:
            return (
                "group_actor_host_execution_requires_confirmation",
                "群成员触发的宿主命令需要确认",
                "这条命令由当前群成员触发；群聊上下文不会让该成员继承设备主人的宿主权限。",
                "[approval required: 当前群成员没有自动执行宿主命令的授权；设备主人可重新发起，或批准这次请求]",
            )
        return (
            "command_execution_requires_confirmation",
            "执行命令需要确认",
            "Akane 想以宿主用户权限运行一条命令。",
            "[approval required: 请在能力审批中允许这次宿主命令]",
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
        guidance = (
            "先用 manage_project_workspace(action=open) 注册该目录，再重试；已有文件可用 manage_generated_file(action=register) 登记。"
            if clean_reason in {"output_cwd_not_registered", "output_cwd_escapes_workspace"}
            else "请检查资源句柄、目标相对路径与输出声明后调整。"
        )
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
                f"这次命令没有执行：资源暂存被拒绝（{clean_reason}）。{guidance}"
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

    def _project_rejected(self, reason: str) -> ToolExecutionResult:
        clean_reason = str(reason or "project_workspace_rejected")
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "capability_execution_result",
                    "tool_type": self.tool_type,
                    "status": "rejected",
                    "reason": clean_reason,
                }
            ],
            followup_context=(
                f"这次命令没有执行：{clean_reason}。alias:project 需要先选择持久项目；"
                "也可以先用真实命令发现目录，再直接传可访问的 cwd。不要猜测服务器目录。"
            ),
            state_updates={
                "capability_execution": {
                    "tool_type": self.tool_type,
                    "status": "rejected",
                    "reason": clean_reason,
                }
            },
        )

    def _enrich_resources(self, mapped: Any, registration: dict[str, Any], *, context=None) -> Any:
        if not isinstance(registration, dict):
            return mapped
        resources = list(registration.get("generated_resources") or [])
        artifact_status = str(registration.get("artifact_status") or ARTIFACT_STATUS_NOT_REQUESTED)
        data = dict(mapped.data or {})
        data["generated_resources"] = resources
        data["artifact_status"] = artifact_status
        artifact_reason = str(registration.get("reason") or "")
        if artifact_reason:
            data["artifact_reason"] = artifact_reason
        feedback = str(mapped.model_feedback or "")
        if resources:
            labels = "、".join(f"{item.get('handle')}({item.get('name')})" for item in resources)
            targets = [item.get("handle") for item in resources]
            task_owned = context is not None and context.execution_scope is not None
            if not task_owned:
                data["next_action"] = {"tool": "send_file", "targets": targets}
            delivery = ("文件句柄和哈希随任务结果交给父代理；由父代理处理用户交付。" if task_owned
                        else "需要交付时调用 send_file(targets=[...])；不要自动替用户发送。")
            if artifact_status == ARTIFACT_STATUS_REGISTRATION_FAILED:
                reason = artifact_reason or "output_registration_incomplete"
                feedback = (
                    f"{feedback}\n仅部分输出登记成功：{labels}；其余输出登记失败（{reason}）。"
                    f"{delivery}其余文件尚未登记。"
                )
            else:
                feedback = (
                    f"{feedback}\n已登记生成资源：{labels}。{delivery}"
                )
        elif artifact_status == ARTIFACT_STATUS_REGISTRATION_FAILED:
            reason = artifact_reason or "unknown"
            feedback = (
                f"{feedback}\n命令已完成，但输出登记失败（{reason}）。请调整输出声明或告知用户交付失败，"
                "不要声称文件已经生成或已经交付。"
            )
        return replace(mapped, data=data, model_feedback=feedback)


class ExecRunToolHandler(_ExecToolHandlerBase):
    """``exec_run``: run a command in the trusted execution workspace."""

    tool_type = "exec_run"

    def bind_job_runtime(self, runtime: Any | None) -> None:
        """Bind host lifecycle tracking without changing the public tool."""

        if runtime is not None and not callable(getattr(runtime, "begin", None)):
            raise TypeError("invalid_execution_job_runtime")
        self.execution_job_runtime = runtime

    def tool_spec(self):
        return EXEC_RUN_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- exec_run：command 直接由上方宿主事实中的 command_shell 解释，不经过另一层隐式 Shell；它不是 Shell 沙箱。"
            "Windows 默认使用 PowerShell；确需 cmd 语法时显式调用 cmd.exe /c。"
            "cwd 可为工作区相对路径、alias:project 或真实宿主绝对目录；省略时使用当前项目，没有当前项目时使用执行根。"
            "显式 cwd 只覆盖这一条命令；先从真实输出发现路径。"
            "工具结果只描述这一条命令，不代表整个任务：running 用 exec_status 续读或 exec_cancel 停止；"
            "failed/timed_out 时依据真实输出修正命令或换路，确实无法继续时再说明阻塞。"
            "本机所需程序未运行时，先查找安装位置并启动；Windows 长驻进程用 Start-Process 后验证端口或进程，"
            "不要把前台服务挂到超时。"
            "input_resources 会把材料句柄复制到 as 相对路径且不能与 cwd 同用；output_globs 登记执行根或已登记项目目录中本次新增、变更的匹配产物。"
            "按 status、exit_code、stdout、stderr、reason 与 recommended_action 判断结果；改变大量文件前先只读核对目标。"
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
        if "interactive" in value:
            if type(value["interactive"]) is not bool:
                return None
            normalized["interactive"] = value["interactive"]
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
        if str(value.get("cwd") or "").strip() and input_resources:
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
        workspace_id = ""
        input_resources = call.get("input_resources") or []
        if not cwd and not input_resources and context.execution_scope is not None:
            cwd = context.execution_scope.working_directory
            call = {**call, "cwd": cwd}
        if not cwd and not input_resources and self.project_workspace_service is not None:
            try:
                scope = self._project_scope(context)
                current = self.project_workspace_service.current(scope=scope)
                if current is not None:
                    workspace_id = str(current.get("workspace_id") or "")
                    cwd = self.project_workspace_service.execution_cwd(
                        scope=scope,
                        alias_value="alias:project",
                        execution_provider=provider,
                    )
                    call = {**call, "cwd": cwd}
            except ProjectWorkspaceError as exc:
                # A group continuation without a triggering actor has no
                # conversation-member selection to inherit.  Keep the old
                # execution-root default instead of turning an unrelated tool
                # call into group_actor_required.
                if exc.reason not in {"group_actor_required", "group_actor_profile_required"}:
                    return self._project_rejected(exc.reason)
        if cwd == "alias:project" or cwd.startswith("alias:project/"):
            if self.project_workspace_service is None:
                return self._project_rejected("project_workspace_unconfigured")
            try:
                scope = self._project_scope(context)
                current = self.project_workspace_service.current(scope=scope)
                workspace_id = str((current or {}).get("workspace_id") or "")
                cwd = self.project_workspace_service.execution_cwd(
                    scope=scope,
                    alias_value=cwd,
                    execution_provider=provider,
                )
                call = {**call, "cwd": cwd}
            except ProjectWorkspaceError as exc:
                return self._project_rejected(exc.reason)
        if not command or len(cwd) > EXEC_CWD_MAX_CHARS:
            mapped = execute_exec_run(
                    provider,
                    owner=self._owner(context),
                    command=command,
                    cwd=cwd,
                )
            return self._mapped_result(
                self._with_execution_location(
                    mapped,
                    provider=provider,
                    cwd=cwd,
                    workspace_id=workspace_id,
                )
            )
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
                return self._ask_or_redeem(context, decision, call, workspace_id=workspace_id)
            return self._blocked(str(decision.reason or "capability_disabled_by_policy"))
        return self._execute_command(provider, call, context, workspace_id=workspace_id)

    def _execute_command(
        self,
        provider: Any,
        call: dict[str, Any],
        context: ToolExecutionContext,
        *,
        workspace_id: str = "",
    ) -> ToolExecutionResult:
        command = str(call.get("command") or "").strip()
        cwd = str(call.get("cwd") or "").strip()
        input_resources = call.get("input_resources") or []
        output_globs = call.get("output_globs") or []
        bridge = self.resource_bridge
        job_runtime = getattr(self, "execution_job_runtime", None)
        track_job = bool(job_runtime is not None and job_runtime.accepts(context))
        staged = None
        run_id = ""
        if input_resources or output_globs or track_job:
            run_id = new_run_id()
        if input_resources or output_globs:
            if bridge is None:
                return self._resource_rejected("execution_resources_unconfigured")
            if output_globs and cwd and not input_resources:
                authorized_root = None
                if Path(cwd).is_absolute() and self.project_workspace_service is not None:
                    try:
                        authorized_root = self.project_workspace_service.output_directory(scope=self._project_scope(context), cwd=cwd)
                    except ProjectWorkspaceError as exc:
                        return self._resource_rejected(exc.reason)
                staged = bridge.bind_workspace_outputs(
                    run_id=run_id,
                    owner=self._owner(context),
                    resource_scope=self._resource_scope(context),
                    cwd=cwd,
                    output_globs=output_globs,
                    authorized_root=authorized_root,
                )
            else:
                staged = bridge.stage_inputs(
                    run_id=run_id,
                    owner=self._owner(context),
                    resource_scope=self._resource_scope(context),
                    input_resources=input_resources,
                    output_globs=output_globs,
                )
            if not bool(staged.get("ok")):
                return self._resource_rejected(str(staged.get("reason") or "execution_resources_staging_failed"))
            cwd = str(staged.get("effective_cwd") or staged.get("cwd_relpath") or cwd)
        if track_job:
            started = job_runtime.begin(
                run_id=run_id,
                run_owner=self._owner(context),
                context=context,
                argument_fingerprint=self._fingerprint({**call, "cwd": cwd}),
            )
            if not started.get("ok"):
                if staged is not None and bridge is not None:
                    bridge.finalize_without_outputs(
                        run_id=run_id,
                        owner=self._owner(context),
                        reason="command_not_started",
                    )
                return self._job_tracking_rejected(
                    str(started.get("reason") or "host_job_create_failed")
                )
        mapped = execute_exec_run(
            provider,
            owner=self._owner(context),
            command=command,
            cwd=cwd,
            timeout_seconds=call.get("timeout_seconds"),
            initial_wait_seconds=call.get("initial_wait_seconds"),
            run_id=run_id,
            interactive=bool(call.get("interactive", False)),
        )
        if track_job:
            tracking = job_runtime.observe_start(
                run_id,
                status=mapped.event_status,
                exit_code=mapped.data.get("exit_code"),
                reason=mapped.reason,
            )
            if mapped.event_status == EXEC_STATUS_RUNNING and not tracking.get("ok"):
                feedback = str(mapped.model_feedback or "")
                marker = "[completion notification unavailable; continue with exec_status]"
                mapped = replace(
                    mapped,
                    model_feedback=f"{feedback}\n{marker}" if feedback else marker,
                )
        if staged is not None and run_id:
            owner = self._owner(context)
            if mapped.event_status == EXEC_STATUS_COMPLETED:
                registration = bridge.register_outputs(run_id=run_id, owner=owner)
                mapped = self._enrich_resources(mapped, registration, context=context)
            elif mapped.event_status in {EXEC_STATUS_FAILED, EXEC_STATUS_TIMED_OUT, EXEC_STATUS_CANCELLED}:
                registration = bridge.finalize_without_outputs(
                    run_id=run_id,
                    owner=owner,
                    reason=f"command_{mapped.event_status}",
                )
                mapped = self._enrich_resources(mapped, registration, context=context)
        mapped = self._with_execution_location(
            mapped,
            provider=provider,
            cwd=cwd,
            workspace_id="" if input_resources else workspace_id,
        )
        task_scope = context.execution_scope
        if task_scope is not None and task_scope.pending_work is not None and mapped.event_status == EXEC_STATUS_RUNNING:
            from ..host_tool_jobs import _artifact_references
            tracked_id = str(mapped.data.get("run_id") or "")
            owner = self._owner(context)
            def inspect():
                status = execute_exec_status(provider, owner=owner, run_id=tracked_id, wait_seconds=0)
                if status.event_status == EXEC_STATUS_RUNNING:
                    return None
                if bridge is not None and bridge.has_binding(run_id=tracked_id, owner=owner):
                    registration = (bridge.register_outputs(run_id=tracked_id, owner=owner)
                                    if status.event_status == EXEC_STATUS_COMPLETED else
                                    bridge.finalize_without_outputs(run_id=tracked_id, owner=owner, reason=status.event_status))
                    status = self._enrich_resources(status, registration, context=context)
                result = self._mapped_result(status)
                return {"status": status.event_status, "summary": status.model_feedback,
                        "artifacts": _artifact_references(result)}
            task_scope.pending_work.track(tracked_id, inspect=inspect,
                                          cancel=lambda: execute_exec_cancel(provider, owner=owner, run_id=tracked_id))
        return self._mapped_result(mapped)

    def _job_tracking_rejected(self, reason: str) -> ToolExecutionResult:
        clean_reason = str(reason or "host_job_unavailable")[:160]
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[{
                "type": "capability_execution_result",
                "tool_type": self.tool_type,
                "status": "failed",
                "reason": clean_reason,
            }],
            followup_context=(
                f"<tool_use_error>命令没有开始：后台执行状态未能可靠登记（{clean_reason}）。"
                "请如实说明失败，不要声称命令正在运行。</tool_use_error>"
            ),
            state_updates={
                "capability_execution": {
                    "tool_type": self.tool_type,
                    "status": "failed",
                    "reason": clean_reason,
                }
            },
        )

    def _ask_or_redeem(
        self,
        context: ToolExecutionContext,
        decision: PermissionDecision,
        call: dict[str, Any],
        *,
        workspace_id: str = "",
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
                authorization_profile_user_id=authorization_profile_user_id(context),
            )
            if grant is not None:
                return self._execute_command(
                    self.execution_provider,
                    call,
                    context,
                    workspace_id=workspace_id,
                )
        request_id = self._create_approval_request(context, decision, fingerprint, resource, device)
        return self._approval_required(context, decision, request_id=request_id, fingerprint=fingerprint)

    def _fingerprint(self, call: dict[str, Any]) -> str:
        arguments: dict[str, Any] = {}
        for key in ("command", "cwd", "timeout_seconds", "initial_wait_seconds", "interactive"):
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
        approval_reason, title, summary, _followup = self._approval_explanation(context)
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
                "title": title,
                "summary": summary,
                "approvalReason": approval_reason,
                "payloadPreview": preview,
                "requestFingerprint": fingerprint,
                "resource": resource,
                "deviceId": device,
                "authorizationProfileUserId": authorization_profile_user_id(context),
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
            display_name=self.tool_type,
            risk="high",
            confirm="always",
            effects=("command_exec",),
            reason="command_execution_requires_confirmation",
            args_preview=args_preview,
        )
        return resolve_permission_for_profile(
            request,
            base_dir=self.config_base_dir or getattr(config, "DATA_DIR", "users_data"),
            profile_user_id=authorization_profile_user_id(context),
            family_id="ops",
        )


class ExecInputToolHandler(ExecRunToolHandler):
    """Reuse command authorization; input to an interpreter can execute code."""
    tool_type = "exec_input"

    def tool_spec(self):
        return EXEC_INPUT_TOOL_SPEC

    def capability_status(self, **kwargs: Any) -> dict[str, Any]:
        if not callable(getattr(self.execution_provider, "input", None)):
            return {"enabled": False, "status": "unavailable", "reason": "interactive_input_not_supported"}
        return super().capability_status(**kwargs)

    def build_prompt_instruction(self) -> str:
        return "- exec_input：向 interactive=true 命令写入原样 UTF-8 输入；按序号去重，pending 时查询 status，written 后用 exec_status 核验程序结果。"

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        from ..execution_run import is_valid_run_id
        if not isinstance(value, dict) or value.get("type") != self.tool_type or not is_valid_run_id(value.get("run_id")):
            return None
        action = value.get("action")
        if action not in {"write", "status"}:
            return None
        result = {"type": self.tool_type, "run_id": value["run_id"], "action": action}
        if action == "status":
            return None if any(k in value for k in ("sequence", "text", "close")) else result
        if type(value.get("sequence")) is not int or value["sequence"] < 1:
            return None
        text, close = value.get("text", ""), value.get("close", False)
        if not isinstance(text, str) or len(text) > 16384 or type(close) is not bool:
            return None
        if not text and not close:
            return None
        return {**result, "sequence": value["sequence"], "text": text, "close": close}

    def _fingerprint(self, call: dict[str, Any]) -> str:
        return build_approval_request_fingerprint({k: call[k] for k in ("run_id", "action", "sequence", "text", "close") if k in call})

    @staticmethod
    def _approval_explanation(context: ToolExecutionContext) -> tuple[str, str, str, str]:
        return ("command_input_requires_confirmation", "向运行中的命令输入需要确认",
                "Akane 想向当前会话的命令写入输入；解释器可能将输入作为代码执行。",
                "[approval required: 请在能力审批中允许这次命令输入]")

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        if not callable(getattr(self.execution_provider, "input", None)):
            return self._unavailable_result("interactive_input_not_supported")
        if call["action"] == "write":
            decision = self._permission_decision(context, {"run_id": call["run_id"], "sequence": call["sequence"],
                "input_chars": len(call["text"]), "close": call["close"]})
            if not decision.allowed:
                if decision.requires_user_decision:
                    return self._ask_or_redeem(context, decision, call)
                return self._blocked(str(decision.reason or "capability_disabled_by_policy"))
        return self._execute_command(self.execution_provider, call, context)

    def _execute_command(self, provider: Any, call: dict[str, Any], context: ToolExecutionContext, *, workspace_id: str = "") -> ToolExecutionResult:
        import json
        result = provider.input(owner=self._owner(context), **{k: v for k, v in call.items() if k in {"run_id", "action", "sequence", "text", "close"}})
        data = {**result, "run_id": call["run_id"], "tool_type": self.tool_type}
        feedback = json.dumps(data, ensure_ascii=False) + "\nwritten 只表示已写入管道；用 exec_status 查看程序实际输出。pending 时查询 exec_input(action=status)，不要换序号重发。"
        return ToolExecutionResult(tool_type=self.tool_type,
            stream_events=[{"type": "capability_execution_result", **data}],
            followup_context=feedback, state_updates={"capability_execution": data})


class ExecStatusToolHandler(_ExecToolHandlerBase):
    """``exec_status``: query a run and read incremental output by cursor."""

    tool_type = "exec_status"

    def tool_spec(self):
        return EXEC_STATUS_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- exec_status：按 run_id 查询已启动命令的状态，并用 cursor 增量读取输出；"
            "每次返回自 cursor 之后的新输出和 next_cursor，任务终止且输出读完后 next_cursor 为 null；"
            "运行中可用 wait_seconds 最多等待 30 秒；进入终态会提前返回，否则在窗口结束时一次给出期间增量输出，"
            "避免被进度行或警告唤醒后反复轮询。"
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
                mapped = self._enrich_resources(mapped, registration, context=context)
            elif mapped.event_status in {EXEC_STATUS_FAILED, EXEC_STATUS_TIMED_OUT, EXEC_STATUS_CANCELLED}:
                registration = bridge.finalize_without_outputs(
                    run_id=run_id,
                    owner=owner,
                    reason=f"command_{mapped.event_status}",
                )
                mapped = self._enrich_resources(mapped, registration, context=context)
        scope = context.execution_scope
        if scope is not None and scope.pending_work is not None and mapped.event_status in {
            EXEC_STATUS_COMPLETED, EXEC_STATUS_FAILED, EXEC_STATUS_TIMED_OUT, EXEC_STATUS_CANCELLED,
        }:
            scope.pending_work.acknowledge(run_id)
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
                mapped = self._enrich_resources(mapped, registration, context=context)
            elif mapped.event_status == "already_ended" and mapped.reason == EXEC_STATUS_COMPLETED:
                registration = bridge.register_outputs(run_id=run_id, owner=owner)
                mapped = self._enrich_resources(mapped, registration, context=context)
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
                mapped = self._enrich_resources(mapped, registration, context=context)
        scope = context.execution_scope
        if scope is not None and scope.pending_work is not None and (
            mapped.event_status == EXEC_STATUS_CANCELLED
            or (mapped.event_status == "already_ended" and mapped.reason in {
                EXEC_STATUS_COMPLETED, EXEC_STATUS_FAILED, EXEC_STATUS_TIMED_OUT, EXEC_STATUS_CANCELLED,
            })
        ):
            scope.pending_work.acknowledge(run_id)
        return self._mapped_result(mapped)
