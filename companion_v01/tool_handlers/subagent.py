"""Model admission for one independent coding task; execution stays host-owned."""

from __future__ import annotations

from typing import Any
from dataclasses import replace

from ..capability_registry import CapabilityToolSpec
from ..host_jobs import HostJobOwner
from ..llm_runtime import ModelExecutionTarget
from ..subagent_engine import model_route_fingerprint
from ..subagent_policy import task_capability_selection
from .core import BaseToolHandler, ToolExecutionContext, ToolExecutionResult, ToolMetadata


SPAWN_SUBAGENT_SPEC = CapabilityToolSpec(
    capability_id="spawn_subagent", display_name="Start an independent coding task",
    description=(
        "Delegate a self-contained coding or research task in the selected project. "
        "Describe the deliverable and relevant file paths; the child does not see this conversation. "
        "The child inherits available work tools with the same permissions; you handle user delivery. "
        "Returns a durable job_id immediately; the result comes back to this conversation. "
        "Continue independent work meanwhile, then verify and integrate the report. "
        "Use manage_project_workspace to select a project first."
    ),
    input_schema={"type": "object", "additionalProperties": False,
        "properties": {"task": {"type": "string", "minLength": 1, "maxLength": 24000},
                       "label": {"type": "string", "maxLength": 80}}, "required": ["task"]},
    risk="medium", confirm="never", effects=("background_task",), visible_in=("desktop", "qq"),
    spec_version="1.0.0", schema_version=1, execution_class="sync", idempotency="effectful", max_result_bytes=4096,
)

class SpawnSubagentToolHandler(BaseToolHandler):
    tool_type = "spawn_subagent"
    policy_accepted_native_tool = True

    def __init__(self, *, engine: Any, runtime: Any, conversation_ref_issuer: Any) -> None:
        self.engine = engine
        self.runtime = runtime
        self.conversation_ref_issuer = conversation_ref_issuer

    def tool_spec(self):
        return SPAWN_SUBAGENT_SPEC

    def tool_metadata(self):
        return ToolMetadata(family="execution", operation="write", risk="medium")

    def capability_status(self, **_kwargs):
        ready = (getattr(getattr(self.engine, "memcore_manager", None), "enabled", False)
                 and self.runtime.providers.available(self.runtime.provider_name)
                 and callable(self.conversation_ref_issuer)
                 and self.engine.llm.chat_supports_native_tools())
        return {"offered": bool(ready)}

    def normalize_call(self, value):
        if not isinstance(value, dict) or value.get("type") != self.tool_type:
            return None
        task, label = value.get("task"), value.get("label", "")
        if not isinstance(task, str) or not task.strip() or len(task) > 24000 or "\x00" in task:
            return None
        if not isinstance(label, str) or len(label) > 80 or "\x00" in label:
            return None
        return {"type": self.tool_type, "task": task.strip(), "label": label.strip()}

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        if context.execution_scope is not None:
            return self._failed("subagent_nested_task_unavailable")
        normalized = self.normalize_call(call)
        if normalized is None:
            return self._failed("subagent_task_invalid")
        selection = context.capability_selection
        if selection is None or not context.invocation_id:
            return self._failed("subagent_admission_context_required")
        data = context.request_context
        target = data.get("_model_execution_target")
        if not isinstance(target, ModelExecutionTarget):
            return self._failed("subagent_parent_model_target_required")
        if not self.engine.llm.chat_supports_native_tools(execution_target=target):
            return self._failed("subagent_native_tools_unavailable")
        project_handler = self.engine.tool_handlers.get("manage_project_workspace")
        if project_handler is None:
            return self._failed("subagent_project_service_unavailable")
        try:
            project = project_handler.service.current(scope=project_handler._scope(context))
        except Exception:
            return self._failed("subagent_project_context_unavailable")
        if project is None:
            return self._failed("workspace_not_selected", "先用 manage_project_workspace 选择项目。")
        try:
            # The unified parent selection already contains eligible deferred
            # targets. Snapshot its ceiling; residency never expands permission.
            candidates = set(selection.tool_names)
            if selection.execution_allowlist is not None:
                candidates.intersection_update(selection.execution_allowlist)
            child_selection = task_capability_selection(replace(selection, execution_allowlist=frozenset(candidates)))
            allowed = tuple(sorted(child_selection.execution_allowlist))
        except Exception:
            return self._failed("subagent_capability_policy_unavailable")
        if not allowed:
            return self._failed("subagent_parent_tools_unavailable")
        metadata = {key: str(data.get(key) or "") for key in (
            "actor_stable_id", "actor_profile_user_id", "authorization_profile_user_id",
        )}
        settings = self.engine.llm._settings_view()
        metadata.update(client_mode=context.client_mode, character_pack_id=context.character_pack_id,
                        model_role=target.role, route_fingerprint=model_route_fingerprint(target),
                        thinking_mode=settings.llm_thinking_mode)
        try:
            reference = self.conversation_ref_issuer(context)
            result = self.runtime.submit(
                owner=HostJobOwner(context.profile_user_id, context.session_id),
                task=normalized["task"], label=normalized["label"], working_directory=project["working_directory"],
                allowed_tools=allowed, model=target.model,
                reasoning_effort=self.engine.llm._configured_reasoning_effort(target.bundle),
                execution_context=metadata, conversation_ref=reference,
                character_pack_id=context.character_pack_id, channel=context.client_mode,
                tool_call_id=context.invocation_id, turn_id=context.current_user_source_id,
            )
        except Exception as exc:
            return self._failed(f"subagent_admission_{type(exc).__name__}")
        if not result.get("ok"):
            return self._failed(str(result.get("reason") or "subagent_not_started"))
        return ToolExecutionResult(tool_type=self.tool_type,
            stream_events=[{"type": "background_job_accepted", "tool_type": self.tool_type,
                            "status": "accepted", "job_id": result["job_id"],
                            "job_status": result.get("job_status") or "queued",
                            "control_state": result.get("control_state") or "running",
                            "duplicate": result["duplicate"]}],
            followup_context=(f"子任务已登记，job_id: {result['job_id']}。结果会回到当前会话；"
                              "可以继续独立工作，收到结果后由你验证并整合交付。"),
            state_updates={"capability_execution": {"tool_type": self.tool_type, "status": "accepted", "job_id": result["job_id"]}},
        )

    def _failed(self, reason: str, hint: str = "") -> ToolExecutionResult:
        return ToolExecutionResult(tool_type=self.tool_type,
            stream_events=[{"type": "background_job_rejected", "tool_type": self.tool_type, "status": "failed", "reason": reason}],
            followup_context=f"<tool_use_error>子任务未启动：{reason}。{hint}</tool_use_error>")
