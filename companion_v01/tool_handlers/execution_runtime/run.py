"""Command normalization and admission through projects, resources, and host jobs."""

from __future__ import annotations

from dataclasses import (
    replace,
)

from pathlib import (
    Path,
)

from typing import (
    Any,
)

from ...execution_run import (
    execute_exec_cancel,
    execute_exec_run,
    execute_exec_status,
    new_run_id,
)

from ...execution_specs import (
    EXEC_CWD_MAX_CHARS,
    EXEC_RUN_TOOL_SPEC,
    EXEC_STATUS_RUNNING,
    normalize_timeout_seconds,
)

from ...project_workspace import (
    ProjectWorkspaceError,
)

from ..core import (
    ToolExecutionContext,
    ToolExecutionResult,
)

from .approvals import (
    ExecutionApprovalMixin,
)

from .base import (
    _ExecToolHandlerBase,
)


class ExecRunToolHandler(ExecutionApprovalMixin, _ExecToolHandlerBase):
    """``exec_run``: run a command in the trusted execution workspace."""

    tool_type = "exec_run"
    # Each call owns its process and cwd. The caller separates dependent work.
    parallel_execution = True

    def tool_spec(self):
        return EXEC_RUN_TOOL_SPEC

    def build_prompt_instruction(self) -> str:
        return (
            "- exec_run：command 直接由上方宿主事实中的 command_shell 解释，不经过另一层隐式 Shell；它不是 Shell 沙箱。"
            "Windows 默认使用 PowerShell；确需 cmd 语法时显式调用 cmd.exe /c。"
            "cwd 可为工作区相对路径、alias:project 或真实宿主绝对目录；省略时使用当前项目，没有当前项目时使用执行根。"
            "显式 cwd 只覆盖这一条命令；先从真实输出发现路径。"
            "工具结果只描述这一条命令；查进度或续读用 exec_status，停止用 exec_cancel；"
            "failed/timed_out 时依据真实输出修正命令或换路，确实无法继续时再说明阻塞。"
            "本机所需程序未运行时，先查找安装位置并启动；Windows 长驻服务用 Start-Process -WindowStyle Hidden，验证端口或进程。"
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
        for flag in ("interactive", "background"):
            if flag in value:
                if type(value[flag]) is not bool:
                    return None
                normalized[flag] = value[flag]
        if value.get("timeout_seconds") is not None:
            try:
                normalized["timeout_seconds"] = normalize_timeout_seconds(value["timeout_seconds"])
            except ValueError:
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
            background=bool(call.get("background", False) or call.get("interactive", False)),
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
            mapped = self._settle_resources(
                mapped, run_id=run_id, context=context, initial_result=True,
            )
        mapped = self._with_execution_location(
            mapped,
            provider=provider,
            cwd=cwd,
            workspace_id="" if input_resources else workspace_id,
        )
        if track_job and mapped.event_status == EXEC_STATUS_RUNNING:
            from .completion_reader import ExecutionCompletionReader
            job_runtime.track_result(context=context, run_id=run_id,
                reader=ExecutionCompletionReader(self, provider, context, mapped),
                wait=False)
        task_scope = context.execution_scope
        if task_scope is not None and task_scope.pending_work is not None and mapped.event_status == EXEC_STATUS_RUNNING:
            from ...host_tool_job_runtime.contracts import _artifact_references
            tracked_id = str(mapped.data.get("run_id") or "")
            owner = self._owner(context)
            from .completion_reader import ExecutionCompletionReader
            reader = ExecutionCompletionReader(self, provider, context, mapped)
            def inspect():
                result = reader.inspect()
                if result is None:
                    return None
                return {"status": result.state_updates["capability_execution"]["status"], "summary": result.followup_context,
                        "artifacts": _artifact_references(result)}
            task_scope.pending_work.track(tracked_id, inspect=inspect,
                                          cancel=lambda: execute_exec_cancel(provider, owner=owner, run_id=tracked_id),
                                          observe=reader.observe)
        return self._mapped_result(mapped)
