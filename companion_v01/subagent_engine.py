"""Public child driver: capture the model route, then run an isolated task."""
from __future__ import annotations

from dataclasses import replace
from typing import Callable

from .plugin_invocation_scope import plugin_turn_scope
from .subagent_execution import ChildExecution
from .subagent_models import model_route_fingerprint, resolve_subagent_route, freeze_subagent_llm
from .subagent_runtime import SubagentStartRequest, SubagentRunResult
from .tool_execution_policy import tool_parallel_prompt


TASK_SYSTEM_PROMPT = (
    "You are an isolated task worker. Complete the assigned task using the supplied tools. "
    "Read relevant source before editing; verify changes with real checks. "
    "Your report goes to the parent agent, who handles user delivery. "
    "Use native tool calls for actions. Finish with JSON: "
    '{"status":"succeeded","summary":"findings, changes and verification"}. '
    'If unfinished, return {"status":"failed","summary":"what remains","reason":"brief cause"}. '
    "Report only observed results. Keep the final summary under 4000 characters; "
    "put a longer report in the shared workspace. "
    "A run_id or job_id confirms admission, not completion. Ordinary foreground calls share a wait window; "
    "use the tool's background scheduling flag for an immediate ID. Ready results appear at the next request; "
    "wait for required results with exec_status(wait_seconds=...). "
    "Finish only after required work is observed; ending a child cancels its unfinished work."
)


class EngineSubagentDriver:
    def __init__(self, engine):
        self.engine = engine

    def control(self, task_id: str, action: str) -> dict:
        work = getattr(self.engine, "_live_task_work", {}).get(str(task_id or "").strip())
        if work is None:
            return {"ok": False, "status": "not_running", "reason": "task_not_running"}
        return dict(work.control(action))

    def __call__(self, request: SubagentStartRequest, *, cancelled: Callable[[], bool]) -> SubagentRunResult:
        def failed(reason):
            return SubagentRunResult("failed", request.child_session_id, reason=reason)
        manager = getattr(self.engine, "memcore_manager", None)
        if manager is None or not manager.enabled:
            return failed("subagent_trace_store_unavailable")
        metadata = dict(request.execution_context)
        if metadata.get("client_mode") not in {"qq_text", "desktop_pet"} or not request.working_directory:
            return failed("subagent_execution_context_required")
        try:
            route = resolve_subagent_route(self.engine, inherited_model=request.model,
                                           inherited_role=metadata.get("model_role", "chat"))
            if (model_route_fingerprint(route.target) != metadata.get("route_fingerprint")
                    or route.mode != metadata.get("route_mode", "inherit")):
                return failed("subagent_model_route_changed")
            if not self.engine.llm.chat_supports_native_tools(execution_target=route.target):
                return failed("subagent_native_tools_unavailable")
            route = replace(route, reasoning_effort=request.reasoning_effort,
                thinking_mode=metadata.get("thinking_mode", route.thinking_mode),
                max_output_tokens=int(metadata.get("max_output_tokens", route.max_output_tokens)),
                timeout_seconds=float(metadata.get("request_timeout", route.timeout_seconds)))
            llm = freeze_subagent_llm(self.engine.llm, route)
        except Exception as exc:
            return failed(str(exc) if str(exc).startswith("subagent_") else "subagent_model_route_unavailable")
        with plugin_turn_scope(self.engine):
            return ChildExecution(self.engine, request, llm, route, cancelled,
                TASK_SYSTEM_PROMPT + "\n\n" + tool_parallel_prompt()).run()
