"""Interactive input, status pagination, and provider-confirmed cancellation."""

from __future__ import annotations

from typing import (
    Any,
)

from ...capability_approval import (
    build_approval_request_fingerprint,
)

from ...execution_run import (
    execute_exec_cancel,
    execute_exec_status,
)

from ...execution_specs import (
    EXEC_CANCEL_TOOL_SPEC,
    EXEC_INPUT_TOOL_SPEC,
    EXEC_MAX_STATUS_WAIT_SECONDS,
    EXEC_STATUS_CANCELLED,
    EXEC_STATUS_COMPLETED,
    EXEC_STATUS_FAILED,
    EXEC_STATUS_TOOL_SPEC,
    EXEC_STATUS_TIMED_OUT,
)

from ..core import (
    ToolExecutionContext,
    ToolExecutionResult,
)

from .base import (
    _ExecToolHandlerBase,
)

from .run import (
    ExecRunToolHandler,
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
        from ...execution_run import is_valid_run_id
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
            "- exec_status：run_id 可填写命令 run_id 或普通工具的 job_id，查询状态并用 cursor 续读输出；"
            "输出读完后 next_cursor 为 null，终态仍可续读；"
            f"wait_seconds 只限制本次查询等待，最多 {EXEC_MAX_STATUS_WAIT_SECONDS} 秒；终态提前返回，否则窗口结束时给出期间增量输出，"
            "进度行不结束等待，也不终止任务。"
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
        mapped = self._settle_resources(mapped, run_id=run_id, context=context)
        runtime = getattr(self, "execution_job_runtime", None)
        if runtime is not None:
            runtime.observe_query(context=context, mapped=mapped)
        scope = context.execution_scope
        if scope is not None and scope.pending_work is not None:
            scope.pending_work.observe(run_id, mapped)
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
        return "- exec_cancel：run_id 可填命令 run_id 或普通工具 job_id；申请停止任务，cancelled 表示已确认停止，cancelling 仍待确认。"

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
        mapped = self._settle_resources(mapped, run_id=run_id, context=context)
        runtime = getattr(self, "execution_job_runtime", None)
        if runtime is not None:
            runtime.observe_query(context=context, mapped=mapped)
        scope = context.execution_scope
        if scope is not None and scope.pending_work is not None and (
            mapped.event_status == EXEC_STATUS_CANCELLED
            or (mapped.event_status == "already_ended" and mapped.reason in {
                EXEC_STATUS_COMPLETED, EXEC_STATUS_FAILED, EXEC_STATUS_TIMED_OUT, EXEC_STATUS_CANCELLED,
            })
        ):
            scope.pending_work.acknowledge(run_id)
        return self._mapped_result(mapped)
