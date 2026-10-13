"""Execution result projection, continuation metadata, and artifact delivery feedback."""

from __future__ import annotations

from dataclasses import (
    replace,
)

from typing import (
    Any,
)

from ...execution_specs import (
    ARTIFACT_STATUS_NOT_REQUESTED,
    ARTIFACT_STATUS_REGISTRATION_FAILED,
    EXEC_STATUS_CANCELLED,
    EXEC_STATUS_COMPLETED,
    EXEC_STATUS_FAILED,
    EXEC_STATUS_RUNNING,
    EXEC_MAX_STATUS_WAIT_SECONDS,
    EXEC_STATUS_TIMED_OUT,
    EXEC_STATUS_UNAVAILABLE,
)

from ..core import (
    ToolExecutionContext,
    ToolExecutionResult,
    ToolFollowupEnvelope,
)


class ExecutionReceiptMixin:
    """Bounded tool receipts, real command coordinates, and artifact feedback."""

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
                **({"wait_seconds": EXEC_MAX_STATUS_WAIT_SECONDS} if str(mapped.event_status or "") == EXEC_STATUS_RUNNING else {}),
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

    def _settle_resources(
        self,
        mapped: Any,
        *,
        run_id: str,
        context: ToolExecutionContext,
        initial_result: bool = False,
    ) -> Any:
        """Finalize declared files only after known completion or a rejected start.

        A query error or unavailable query cannot confirm that a process ended.
        Preserve its staged files and binding for a later authoritative result.
        """

        bridge = self.resource_bridge
        if bridge is None or not run_id:
            return mapped
        owner = self._owner(context)
        if not bridge.has_binding(run_id=run_id, owner=owner):
            return mapped
        status = str(mapped.event_status or "")
        if status == "already_ended":
            status = str(mapped.reason or "")
        if status == EXEC_STATUS_COMPLETED:
            registration = bridge.register_outputs(run_id=run_id, owner=owner)
        elif status in {EXEC_STATUS_FAILED, EXEC_STATUS_TIMED_OUT, EXEC_STATUS_CANCELLED}:
            registration = bridge.finalize_without_outputs(
                run_id=run_id, owner=owner, reason=f"command_{status}",
            )
        elif initial_result and status == EXEC_STATUS_UNAVAILABLE:
            registration = bridge.finalize_without_outputs(
                run_id=run_id, owner=owner, reason="command_not_started",
            )
        else:
            return mapped
        return self._enrich_resources(mapped, registration, context=context)

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
