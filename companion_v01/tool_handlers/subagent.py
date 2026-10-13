"""Model admission for one independent coding task; execution stays host-owned."""

from __future__ import annotations

from typing import Any
from dataclasses import replace
import json
import re

from ..capability_registry import CapabilityToolSpec
from ..host_jobs import HostJobOwner
from ..llm_runtime import ModelExecutionTarget
from ..subagent_models import resolve_subagent_route
from ..subagent_policy import task_capability_selection
from .core import BaseToolHandler, ToolExecutionContext, ToolExecutionResult, ToolMetadata


SPAWN_SUBAGENT_SPEC = CapabilityToolSpec(
    capability_id="spawn_subagent", display_name="Start or continue a child task",
    description=(
        "Delegate a self-contained coding or research task in the selected project. "
        "Describe the deliverable and relevant file paths; the child does not see this conversation. "
        "The child inherits available work tools with the same permissions; you handle user delivery. "
        "Returns a durable job_id immediately; the result comes back to this conversation. "
        "Continue independent work meanwhile, then verify and integrate the report. "
        "Use manage_project_workspace to select a project first."
        " Supply agent_id to continue a related task with its saved history, or to add an instruction to a running child."
    ),
    input_schema={"type": "object", "additionalProperties": False,
        "properties": {"task": {"type": "string", "minLength": 1, "maxLength": 24000},
                       "label": {"type": "string", "maxLength": 80},
                       "agent_id": {"type": "string", "description": "Existing child ID for continuation or steering."}}, "required": ["task"]},
    risk="medium", confirm="never", effects=("background_task",), visible_in=("desktop", "qq"),
    spec_version="1.1.0", schema_version=2, execution_class="sync", idempotency="effectful", max_result_bytes=4096,
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
        try:
            route = resolve_subagent_route(self.engine)
            native_ready = self.engine.llm.chat_supports_native_tools(execution_target=route.target)
        except Exception:
            native_ready = False
        ready = (getattr(getattr(self.engine, "memcore_manager", None), "enabled", False)
                 and self.runtime.providers.available(self.runtime.provider_name)
                 and callable(self.conversation_ref_issuer)
                 and native_ready)
        return {"offered": bool(ready)}

    def normalize_call(self, value):
        if not isinstance(value, dict) or value.get("type") != self.tool_type:
            return None
        task, label = value.get("task"), value.get("label", "")
        if not isinstance(task, str) or not task.strip() or len(task) > 24000 or "\x00" in task:
            return None
        if not isinstance(label, str) or len(label) > 80 or "\x00" in label:
            return None
        agent_id = value.get("agent_id", "")
        if not isinstance(agent_id, str) or (agent_id and not re.fullmatch(r"subagent_[a-f0-9]{32}", agent_id)):
            return None
        return {"type": self.tool_type, "task": task.strip(), "label": label.strip(), "agent_id": agent_id}

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
        try:
            route = resolve_subagent_route(self.engine, parent_target=target)
        except Exception as exc:
            return self._failed(str(exc) if str(exc).startswith("subagent_") else "subagent_model_route_unavailable")
        if not self.engine.llm.chat_supports_native_tools(execution_target=route.target):
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
        metadata.update(route.metadata())
        metadata.update(client_mode=context.client_mode, character_pack_id=context.character_pack_id,
                        model_role=route.target.role)
        try:
            reference = self.conversation_ref_issuer(context)
            result = self.runtime.submit(
                owner=HostJobOwner(context.profile_user_id, context.session_id),
                task=normalized["task"], label=normalized["label"], working_directory=project["working_directory"],
                allowed_tools=allowed, model=route.target.model, agent_id=normalized["agent_id"],
                reasoning_effort=route.reasoning_effort,
                execution_context=metadata, conversation_ref=reference,
                character_pack_id=context.character_pack_id, channel=context.client_mode,
                tool_call_id=context.invocation_id,
                turn_id=str(context.request_context.get("_host_origin_turn_id") or context.current_user_source_id),
            )
        except Exception as exc:
            return self._failed(f"subagent_admission_{type(exc).__name__}")
        if not result.get("ok"):
            return self._failed(str(result.get("reason") or "subagent_not_started"))
        return ToolExecutionResult(tool_type=self.tool_type,
            stream_events=[{"type": "background_job_accepted", "tool_type": self.tool_type,
                            "status": result.get("status", "accepted"), "job_id": result["job_id"],
                            "agent_id": result.get("agent_id", result.get("child_session_id", "")),
                            "job_status": result.get("job_status") or "queued",
                            "control_state": result.get("control_state") or "running",
                            "duplicate": result["duplicate"]}],
            followup_context=(f"status={result.get('status', 'accepted')}; agent_id={result.get('agent_id') or result.get('child_session_id')}; job_id={result['job_id']}\n"
                              "补充指令将在下一次请求前读入。" if result.get("status") == "steered" else
                              f"agent_id={result.get('agent_id') or result.get('child_session_id')}; job_id={result['job_id']}\n"
                              "任务已登记，结果会回到当前会话；继续独立工作，收到报告后验证并整合交付。"),
            state_updates={"capability_execution": {"tool_type": self.tool_type, "status": result.get("status", "accepted"),
                           "job_id": result["job_id"], "agent_id": result.get("agent_id", result.get("child_session_id", ""))}},
        )

    def _failed(self, reason: str, hint: str = "") -> ToolExecutionResult:
        return ToolExecutionResult(tool_type=self.tool_type,
            stream_events=[{"type": "background_job_rejected", "tool_type": self.tool_type, "status": "failed", "reason": reason}],
            followup_context=f"<tool_use_error>子任务未启动：{reason}。{hint}</tool_use_error>")


MANAGE_SUBAGENTS_SPEC = CapabilityToolSpec(
    capability_id="manage_subagents", display_name="List or delete child histories",
    description="List this conversation's children on demand. Delete selected child histories after stopping their jobs and reading their reports; project files and parent reports are retained.",
    input_schema={"type": "object", "additionalProperties": False, "properties": {
        "action": {"type": "string", "enum": ["list", "delete"]},
        "agent_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 50},
        "cursor": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "required": ["action"]},
    risk="medium", confirm="never", effects=("memory_management",), visible_in=("desktop", "qq"),
    spec_version="1.0.0", schema_version=1, execution_class="sync", idempotency="effectful", max_result_bytes=65536)


class ManageSubagentsToolHandler(BaseToolHandler):
    tool_type = "manage_subagents"
    policy_accepted_native_tool = True

    def __init__(self, *, runtime):
        self.runtime = runtime

    def tool_spec(self):
        return MANAGE_SUBAGENTS_SPEC

    def tool_metadata(self):
        return ToolMetadata(family="execution", operation="write", risk="medium")

    def normalize_call(self, value):
        if not isinstance(value, dict) or value.get("type") != self.tool_type or value.get("action") not in {"list", "delete"}:
            return None
        ids = value.get("agent_ids", [])
        if not isinstance(ids, list) or len(ids) > 50 or any(not isinstance(item, str) or not re.fullmatch(r"subagent_[a-f0-9]{32}", item) for item in ids):
            return None
        if value["action"] == "delete" and not ids:
            return None
        cursor, limit = value.get("cursor", 0), value.get("limit", 20)
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0 or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            return None
        return {"type": self.tool_type, "action": value["action"], "agent_ids": list(dict.fromkeys(ids)), "cursor": cursor, "limit": limit}

    def execute(self, *, call, context):
        value = self.normalize_call(call)
        if value is None or context.execution_scope is not None:
            result = {"ok": False, "reason": "subagent_management_request_invalid"}
        else:
            scope = dict(owner=HostJobOwner(context.profile_user_id, context.session_id),
                         character_pack_id=context.character_pack_id, channel=context.client_mode,
                         actor_id=str(context.request_context.get("actor_stable_id") or ""))
            if value["action"] == "list":
                result = self.runtime.store.list_subagents(cursor=value["cursor"], limit=value["limit"], **scope)
            else:
                rows = [{"agent_id": agent, **self.runtime.delete(agent, **scope)} for agent in value["agent_ids"]]
                result = {"ok": all(row.get("ok") for row in rows), "results": rows}
        return ToolExecutionResult(tool_type=self.tool_type, followup_context=json.dumps(result, ensure_ascii=False))
