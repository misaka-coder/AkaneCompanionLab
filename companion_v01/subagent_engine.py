"""Isolated task turns using the host's LLM, tool pipeline and MemCore projection."""

from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
import time
from typing import Any, Callable

from .engine_services.tool_rounds import restrict_capability_selection
from .llm_runtime import ModelExecutionTarget
from .host_tool_jobs import _artifact_references
from .native_tool_schema import build_openai_native_tool_specs, native_tool_model_name_map
from .subagent_runtime import SubagentRunResult, SubagentStartRequest
from .subagent_policy import task_capability_selection
from .tool_handlers.core import TaskExecutionScope
from .tool_batch import execute_tool_batch
from .tool_invocation import TOOL_CAPABILITY_SELECTION_FIELD, NATIVE_REASONING_CONTENT_FIELD, TOOL_INVOCATION_ID_FIELD
from . import tool_orchestration_engine as orchestration


TASK_SYSTEM_PROMPT = (
    "You are an isolated task worker. Complete the assigned task using the supplied tools. "
    "Read relevant source before editing; verify changes with real checks. "
    "Your report goes to the parent agent, who handles user delivery. "
    "Use native tool calls for actions. Finish with JSON: "
    '{"status":"succeeded","summary":"findings, changes and verification"}. '
    'If unfinished, return {"status":"failed","summary":"what remains","reason":"brief cause"}. '
    "Report only observed results. Keep the final summary under 4000 characters; "
    "put a longer report in the shared workspace and mention its path."
)


def model_route_fingerprint(target: ModelExecutionTarget) -> str:
    client = target.bundle.client
    route = (target.role, target.model, target.protocol,
             str(getattr(client, "base_url", "") or getattr(client, "_akane_base_url", "")))
    return hashlib.sha256(json.dumps(route, ensure_ascii=False).encode()).hexdigest()


class EngineSubagentDriver:
    """Only task orchestration differs; provider and tool protocols stay host-owned."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def __call__(self, request: SubagentStartRequest, *, cancelled: Callable[[], bool]) -> SubagentRunResult:
        engine = self.engine
        manager = getattr(engine, "memcore_manager", None)
        if manager is None or not getattr(manager, "enabled", False):
            return self._result(request, "failed", reason="subagent_trace_store_unavailable")
        metadata = dict(request.execution_context)
        mode = metadata.get("client_mode", "")
        if mode not in {"qq_text", "desktop_pet"} or not request.working_directory:
            return self._result(request, "failed", reason="subagent_execution_context_required")
        role = metadata.get("model_role", "")
        target = engine.llm.resolve_turn_execution_target(
            has_real_images=role == "vision", chat_model_override=request.model,
        )
        if not isinstance(target, ModelExecutionTarget) or model_route_fingerprint(target) != metadata.get("route_fingerprint"):
            return self._result(request, "failed", reason="subagent_model_route_changed")
        if not engine.llm.chat_supports_native_tools(execution_target=target):
            return self._result(request, "failed", reason="subagent_native_tools_unavailable")
        # A request-local facade shares clients and metrics, not mutable routing
        # settings. Reloading the parent during a child cannot change its effort.
        llm = copy.copy(engine.llm)
        llm.settings = replace(
            engine.llm._settings_view(), llm_reasoning_effort=request.reasoning_effort,
            llm_chat_reasoning_effort=request.reasoning_effort,
            llm_thinking_mode=metadata.get("thinking_mode", "default"),
        )
        client = engine._resolve_client_protocol_context({"client_mode": mode})
        selection = engine._resolve_capability_selection(
            client_context=client, profile_user_id=request.parent_profile_user_id,
            session_id=request.parent_session_id, intent_text=request.task,
            authorization_profile_user_id=metadata.get("authorization_profile_user_id", ""),
        )
        selection = restrict_capability_selection(selection, allowed_tool_names=request.allowed_tools)
        try:
            selection = task_capability_selection(selection)
        except Exception:
            return self._result(request, "failed", reason="subagent_capability_policy_unavailable")
        if request.allowed_tools and not selection.tool_names:
            return self._result(request, "failed", reason="subagent_parent_tools_unavailable")
        tools = build_openai_native_tool_specs({
            name: selection.resolved_handlers[name] for name in selection.schema_tool_names
            if name in selection.resolved_handlers
        })
        selection = replace(selection, native_tool_aliases=native_tool_model_name_map(tools))
        # The audit domain is separate even when the host uses profile-wide
        # memory. Tool permission/resource ownership remains the parent actor's.
        scope = dict(profile_user_id=request.child_session_id, session_id=request.child_session_id,
                     character_pack_id="")
        source_id = request.child_session_id + ":input"
        record = {"source_id": source_id, "role": "user", "content": request.task, "timestamp": int(time.time())}
        turn_id = ""
        history: list[dict[str, Any]] = []
        reasoning_by_call_id: dict[str, str] = {}
        model_images: list[dict[str, Any]] = []
        artifacts: dict[str, dict[str, str]] = {}
        seen: set[str] = set()
        request_context = {key: metadata[key] for key in (
            "actor_stable_id", "actor_profile_user_id", "authorization_profile_user_id",
        ) if key in metadata}
        request_context["_task_execution_scope"] = TaskExecutionScope(request.working_directory, request.child_session_id)
        try:
            opened = manager.begin_input_turn(record, **scope)
            if not opened.get("ok") or not opened.get("writable", True) or opened.get("status") not in {"open", "opened"}:
                return self._result(request, "failed", reason="subagent_trace_open_failed")
            turn_id = str(opened.get("turn_id") or "")
            if not turn_id:
                return self._result(request, "failed", reason="subagent_trace_open_failed")
            initial_projection = manager.build_open_turn_projection(
                turn_id=turn_id, provider_profile=target.protocol, **scope,
            )
            if not initial_projection.get("ok"):
                return self._result(request, "failed", reason="subagent_input_projection_failed")
            hard_limit = engine._max_tool_rounds()
            tool_rounds = 0
            while True:
                if cancelled():
                    return self._result(request, "cancelled", reason="subagent_cancelled_between_steps",
                                        artifacts=tuple(artifacts.values()))
                response = llm.call_chat_json_result(
                    system_prompt=TASK_SYSTEM_PROMPT,
                    user_prompt=f"Working directory: {request.working_directory}\n\nTask:\n{request.task}",
                    fallback={}, native_tools=tools, post_user_turns=history,
                    user_images=model_images or None,
                    execution_target=target, prompt_cache_key="akane-subagent-v1",
                )
                if response.error or response.fallback_used:
                    return self._result(request, "failed", reason="subagent_model_" + (response.error or "fallback"))
                output = dict(response.parsed)
                reasoning = str(output.get(NATIVE_REASONING_CONTENT_FIELD) or "")
                output[TOOL_CAPABILITY_SELECTION_FIELD] = selection
                output, calls, rejections = engine._prepare_tool_round_decisions(
                    final_output=output, user_message=request.task, client_context=client,
                    profile_user_id=request.parent_profile_user_id, session_id=request.parent_session_id,
                )
                if rejections:
                    return self._result(request, "failed", reason="subagent_tool_protocol_rejected",
                                        summary="\n".join(rejections), artifacts=tuple(artifacts.values()))
                for call in calls:
                    if reasoning:
                        reasoning_by_call_id[str(call.get(TOOL_INVOCATION_ID_FIELD) or "")] = reasoning
                if not calls:
                    status = str(output.get("status") or "")
                    summary = str(output.get("summary") or "").strip()
                    if status not in {"succeeded", "failed"} or not summary:
                        return self._result(request, "failed", reason="subagent_no_deliverable_result")
                    final_record = {"source_id": request.child_session_id + ":result", "role": "assistant",
                                    "content": summary, "timestamp": int(time.time())}
                    closed = manager.complete_input_turn(
                        turn_id=turn_id, assistant_record=final_record, memory_metadata=None,
                        provider_output_raw=response.raw_text, provider_profile=target.protocol,
                        provider_projection={"role": "assistant", "content": response.raw_text}, **scope,
                    )
                    if not closed.get("ok"):
                        return self._result(request, "failed", reason="subagent_trace_complete_failed")
                    turn_id = ""
                    return self._result(request, status, summary=summary,
                                        artifacts=tuple(artifacts.values()),
                                        reason=str(output.get("reason") or "task_incomplete") if status == "failed" else "")
                if hard_limit > 0 and tool_rounds >= hard_limit:
                    return self._result(request, "failed", reason="subagent_tool_round_limit")
                tool_rounds += 1
                def execute(call):
                    return engine._execute_tool_call_with_hooks(
                        call=call, final_output={}, profile_user_id=request.parent_profile_user_id,
                        session_id=request.parent_session_id, character_pack_id=metadata.get("character_pack_id", ""),
                        now_ts=int(time.time()), current_user_source_id=source_id, client_context=client,
                        memory_exclude_source_ids=[],
                        request_context={**request_context, "_model_execution_target": target},
                    )

                def handler_for(call):
                    handlers = engine._resolve_tool_handlers(
                        client_context=client, profile_user_id=request.parent_profile_user_id,
                        session_id=request.parent_session_id,
                        capability_selection=call.get(TOOL_CAPABILITY_SELECTION_FIELD),
                    )
                    return handlers.get(str(call.get("type") or ""))

                executed = execute_tool_batch(calls, execute=execute, handler_for=handler_for,
                                              cancelled=cancelled, scope_id=request.child_session_id)
                items = []
                for call, result in zip(calls, executed):
                    feedback = orchestration.shape_tool_followup(
                        result.followup_envelope or result.followup_context, tool_type=result.tool_type,
                    )
                    items.append((call, result, feedback))
                    for artifact in _artifact_references(result):
                        artifacts[artifact["handle"]] = artifact
                ids, error = engine._record_memcore_tool_batch(
                    items=items, now_ts=int(time.time()), current_user_source_id=source_id,
                    memcore_turn_id=turn_id, recorded_tool_call_ids=seen, **scope,
                )
                if error or len(ids) != 2 * len(items):
                    return self._result(request, "failed", reason="subagent_tool_trace_failed")
                batch_images = engine._merge_tool_model_image_inputs([], [item[1] for item in items])
                model_images = engine._merge_model_image_inputs(model_images, batch_images)
                media_ids = engine._record_memcore_tool_media_input(
                    model_image_inputs=batch_images, related_source_ids=ids,
                    now_ts=int(time.time()), memcore_turn_id=turn_id, **scope,
                )
                if batch_images and not media_ids:
                    return self._result(request, "failed", reason="subagent_media_trace_failed")
                projected = engine._append_tool_history_batch(
                    tool_history_turns=history, items=items, trace_source_ids=ids,
                    media_source_ids=media_ids, model_image_inputs=batch_images,
                    provider_output_raw=response.raw_text, provider_profile=target.protocol,
                    memcore_turn_id=turn_id, current_user_source_id=source_id,
                    native_reasoning_by_call_id=reasoning_by_call_id,
                    **scope,
                )
                if not projected.get("ok"):
                    return self._result(request, "failed", reason="subagent_tool_projection_failed")
        finally:
            if turn_id:
                manager.abort_input_turn(turn_id=turn_id, reason="subagent_not_completed", **scope)

    @staticmethod
    def _result(request: SubagentStartRequest, status: str, *, summary: str = "", reason: str = "",
                artifacts: tuple[dict[str, str], ...] = ()) -> SubagentRunResult:
        return SubagentRunResult(status=status, child_session_id=request.child_session_id,
                                 summary=summary[:4000], reason=reason[:500], artifacts=artifacts)
