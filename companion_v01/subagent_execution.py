"""One child run; adapters, tools, Jobs and memory stay shared host services."""
from __future__ import annotations

from dataclasses import replace
import json
import hashlib
import time
from typing import Any

from services import provider_continuation as continuation
from . import tool_orchestration_engine as orchestration
from .engine_services.tool_rounds import restrict_capability_selection
from .host_tool_job_runtime.contracts import _artifact_references
from .host_jobs import HostJobOwner
from .model_request_control import ModelRequestCancelled
from .native_tool_schema import build_openai_native_tool_specs, native_tool_model_name_map
from .prompt_builder import internal_memory_compaction_prompt
from .subagent_memory import SubagentMemory
from .subagent_policy import task_capability_selection, CHILD_MEMORY_READ_TOOLS
from .subagent_recovery import call_with_recovery, model_failure_reason
from .subagent_runtime import SubagentRunResult
from .task_work import TaskWork
from .text_utils import estimate_prompt_tokens
from .tool_batch import execute_tool_batch
from .tool_handlers.core import TaskExecutionScope
from .tool_invocation import TOOL_CAPABILITY_SELECTION_FIELD, NATIVE_REASONING_CONTENT_FIELD, TOOL_INVOCATION_ID_FIELD
from .turn_coordination import cancellation_scope


class ChildExecution:
    def __init__(self, engine, request, llm, route, cancelled, system_prompt):
        self.engine, self.request, self.llm, self.route = engine, request, llm, route
        self.target, self.metadata = route.target, dict(request.execution_context)
        self.external_cancelled, self.system_prompt = cancelled, system_prompt
        self.work = TaskWork()
        self.memory = None
        self.history: list[dict[str, Any]] = []
        self.reasoning: dict[str, str] = {}
        self.continuations: dict[str, dict] = {}
        self.images: list[dict[str, Any]] = []
        self.artifacts: dict[str, dict[str, str]] = {}
        self.seen: set[str] = set()
        self.completed_calls: list[str] = []
        self.requests = self.recoveries = 0
        self.raw_report = ""

    def cancelled(self):
        return self.external_cancelled() or self.work.stop_requested

    def result(self, status, *, reason="", summary=""):
        if not summary and self.completed_calls:
            summary = f"Executed {len(self.completed_calls)} tool calls in the shared workspace. " + "; ".join(self.completed_calls[-6:])
        metrics = self.llm.snapshot_metrics() if callable(getattr(self.llm, "snapshot_metrics", None)) else {}
        return SubagentRunResult(status=status, child_session_id=self.request.child_session_id,
            summary=summary[:4000], artifacts=tuple(self.artifacts.values()), reason=reason[:500], model=self.target.model,
            usage={"requests": self.requests, "recoveries": self.recoveries,
                   "input_tokens": metrics.get("reported_input_tokens", 0),
                   "output_tokens": metrics.get("reported_output_tokens", 0), "cache_read_tokens": metrics.get("cache_read_tokens", 0)})

    def setup(self):
        engine, request, metadata = self.engine, self.request, self.metadata
        self.client = engine._resolve_client_protocol_context({"client_mode": metadata["client_mode"]})
        selected = engine._resolve_capability_selection(client_context=self.client,
            profile_user_id=request.parent_profile_user_id, session_id=request.parent_session_id,
            intent_text=request.task, authorization_profile_user_id=metadata.get("authorization_profile_user_id", ""))
        self.selection = task_capability_selection(restrict_capability_selection(selected, allowed_tool_names=request.allowed_tools))
        if request.allowed_tools and not self.selection.tool_names:
            raise RuntimeError("subagent_parent_tools_unavailable")
        self.tools = build_openai_native_tool_specs({name: self.selection.resolved_handlers[name]
            for name in self.selection.schema_tool_names if name in self.selection.resolved_handlers})
        self.selection = replace(self.selection, native_tool_aliases=native_tool_model_name_map(self.tools))
        catalog, errors = engine._build_loadable_capability_catalog(ready_tool_names=self.selection.tool_names,
                                                                    profile_user_id=request.parent_profile_user_id)
        if errors:
            raise RuntimeError("subagent_capability_catalog_unavailable")
        if self.selection.capability_catalog is not None:
            from .capability_exposure import directory_text
            catalog = "\n\n".join(part for part in (directory_text(self.selection), catalog) if part)
        environment = engine._build_execution_host_context(self.selection.resolved_handlers.get("exec_run"),
            execution_scope=TaskExecutionScope(request.working_directory, request.child_session_id)) or f"Working directory: {request.working_directory}"
        guidance = ("Register final files with manage_generated_file(action=register, path=...). "
                    "The parent receives the original handle and SHA-256." if "manage_generated_file" in self.selection.tool_names else "")
        self.system_prompt = "\n\n".join(part for part in (self.system_prompt, catalog, environment, guidance,
            internal_memory_compaction_prompt(),
            "Memory tools read your own task history." if self.selection.tool_names and CHILD_MEMORY_READ_TOOLS.intersection(self.selection.tool_names) else "") if part)
        self.task_prompt = request.task
        self.context = {key: metadata[key] for key in ("actor_stable_id", "actor_profile_user_id", "authorization_profile_user_id") if key in metadata}
        self.context["_task_execution_scope"] = TaskExecutionScope(request.working_directory, request.child_session_id, self.work)
        self.memory = SubagentMemory(engine, request, self.llm, self.target,
            fixed_tokens=estimate_prompt_tokens(self.system_prompt + self.task_prompt + json.dumps(self.tools, ensure_ascii=False)))
        self.memory.open(request.task)
        self.refresh_history()

    def refresh_history(self):
        self.history[:] = [continuation.overlay(self.engine._overlay_native_reasoning_content(item,
            native_reasoning_by_call_id=self.reasoning), self.continuations,
            sanitize=self.engine._sanitize_tool_trace_value) for item in self.memory.current()]

    def read_instructions(self):
        runtime = getattr(self.engine, "host_subagent_jobs", None)
        if not runtime or not self.request.run_id:
            return False
        owner = HostJobOwner(self.request.parent_profile_user_id, self.request.parent_session_id)
        instructions = runtime.store.pending_subagent_inputs(self.request.run_id, owner=owner)
        for instruction in instructions:
            source_id = self.request.child_session_id + ":steer:" + hashlib.sha256(
                (instruction["job_id"] + "\0" + instruction["source_id"]).encode()).hexdigest()[:32]
            written = self.memory.manager.append_turn_user_input(
                {"source_id": source_id, "role": "user", "content": instruction["task"], "timestamp": int(time.time())},
                turn_id=self.memory.turn_id, **self.memory.scope)
            if not written.get("ok"):
                raise RuntimeError("subagent_steer_trace_failed")
            runtime.store.acknowledge_subagent_inputs(instruction["job_id"], owner=owner, source_ids=[instruction["source_id"]])
        if instructions:
            self.refresh_history()
        return bool(instructions)

    def call_model(self, *, repair=False):
        kwargs = dict(system_prompt=self.system_prompt, user_prompt=self.memory.input_content, fallback={},
            native_tools=self.tools, history_turns=self.memory.past, post_user_turns=self.history,
            user_images=self.images or None, execution_target=self.target, prompt_cache_key="akane-subagent-v1",
            max_output_tokens=self.route.max_output_tokens, request_timeout_s=self.route.timeout_seconds,
            request_observer=self.memory.observer(pending_task_ids=list(self.work.pending_ids)))
        if repair:
            kwargs.update(native_tool_choice="none", ephemeral_turns=[{"role": "user", "_placement": "tail",
                "content": "Return the final task report as JSON with status (succeeded or failed), summary and optional reason. Do not call tools."}])
        def call():
            self.requests += 1
            return self.llm.call_chat_json_result(**kwargs)
        response, retries = call_with_recovery(call, cancelled=self.cancelled)
        self.recoveries += retries
        return response

    def record_completions(self, completions):
        ids, results = [], []
        for completion in completions:
            if completion.get("already_observed"):
                continue
            result = completion.pop("_execution_result", None)
            if result is not None:
                results.append(result)
            for artifact in completion.get("artifacts", []):
                if isinstance(artifact, dict) and artifact.get("handle"):
                    self.artifacts[artifact["handle"]] = artifact
            recorded = self.memory.manager.append_standalone_event(
                {"event_type": "task.work.completed", "source": "host", "fields": {"result": self.engine._sanitize_tool_trace_text(
                    result.followup_context if result is not None else json.dumps(completion, ensure_ascii=False))}},
                source_id=f"{self.request.child_session_id}:{self.memory.run_id}:completion:{completion['id']}",
                turn_id=self.memory.turn_id, **self.memory.scope)
            if not recorded.get("ok"):
                raise RuntimeError("subagent_completion_trace_failed")
            ids.append(str(recorded["source_id"]))
        if ids:
            images = self.engine._merge_tool_model_image_inputs([], results)
            media = self.engine._record_memcore_tool_media_input(model_image_inputs=images,
                related_source_ids=ids, now_ts=int(time.time()), memcore_turn_id=self.memory.turn_id, **self.memory.scope)
            if images and not media:
                raise RuntimeError("subagent_completion_media_trace_failed")
            self.images = self.engine._merge_model_image_inputs(self.images, images)
            self.refresh_history()

    def execute_calls(self, calls, raw_text):
        engine, request, memory = self.engine, self.request, self.memory
        def execute(call):
            own_memory = str(call.get("type") or "") in CHILD_MEMORY_READ_TOOLS
            return engine._execute_tool_call_with_hooks(call=call, final_output={},
                profile_user_id=request.child_session_id if own_memory else request.parent_profile_user_id,
                session_id=request.child_session_id if own_memory else request.parent_session_id,
                character_pack_id="" if own_memory else self.metadata.get("character_pack_id", ""), now_ts=int(time.time()),
                current_user_source_id=memory.source_id, client_context=self.client, memory_exclude_source_ids=[],
                request_context={**self.context, "_model_execution_target": self.target,
                                 "_host_visible_tool_history": self.history})
        def handler_for(call):
            return engine._resolve_tool_handlers(client_context=self.client, profile_user_id=request.parent_profile_user_id,
                session_id=request.parent_session_id, capability_selection=call.get(TOOL_CAPABILITY_SELECTION_FIELD)).get(str(call.get("type") or ""))
        executed = execute_tool_batch(calls, execute=execute, handler_for=handler_for,
            cancelled=self.cancelled, scope_id=request.child_session_id)
        items = []
        for call, result in zip(calls, executed):
            feedback = orchestration.shape_tool_followup(result.followup_envelope or result.followup_context, tool_type=result.tool_type)
            items.append((call, result, feedback))
            for artifact in _artifact_references(result):
                self.artifacts[artifact["handle"]] = artifact
            status = next((str(event.get("status")) for event in reversed(result.stream_events) if event.get("status")), "returned")
            self.completed_calls.append(f"{result.tool_type}: {status}")
        ids, error = engine._record_memcore_tool_batch(items=items, now_ts=int(time.time()),
            current_user_source_id=memory.source_id, memcore_turn_id=memory.turn_id, recorded_tool_call_ids=self.seen, **memory.scope)
        if error or len(ids) != 2 * len(items):
            raise RuntimeError("subagent_tool_trace_failed")
        images = engine._merge_tool_model_image_inputs([], executed)
        self.images = engine._merge_model_image_inputs(self.images, images)
        media = engine._record_memcore_tool_media_input(model_image_inputs=images, related_source_ids=ids,
            now_ts=int(time.time()), memcore_turn_id=memory.turn_id, **memory.scope)
        if images and not media:
            raise RuntimeError("subagent_media_trace_failed")
        projected = engine._append_tool_history_batch(tool_history_turns=self.history, items=items, trace_source_ids=ids,
            media_source_ids=media, model_image_inputs=images, provider_output_raw=raw_text, provider_profile=self.target.protocol,
            memcore_turn_id=memory.turn_id, current_user_source_id=memory.source_id,
            native_reasoning_by_call_id=self.reasoning, provider_continuations=self.continuations, **memory.scope)
        if not projected.get("ok"):
            raise RuntimeError("subagent_tool_projection_failed")

    @staticmethod
    def valid_report(output):
        return output.get("status") in {"succeeded", "failed"} and isinstance(output.get("summary"), str) and bool(output["summary"].strip())

    def loop(self):
        rounds, seen_resume = 0, self.work.resume_generation
        while True:
            if not self.work.wait_for_resume(self.cancelled) or self.cancelled():
                return self.result("cancelled", reason="subagent_cancelled_between_steps")
            self.record_completions(self.work.collect())
            self.read_instructions()
            if self.work.resume_generation != seen_resume:
                seen_resume = self.work.resume_generation
                self.images = []
                self.refresh_history()
            if self.memory.checkpoint_if_due(self.history, pending_ids=list(self.work.pending_ids)):
                if self.memory.prefix_mode:
                    self.history[:] = [continuation.overlay(self.engine._overlay_native_reasoning_content(item,
                        native_reasoning_by_call_id=self.reasoning), self.continuations,
                        sanitize=self.engine._sanitize_tool_trace_value) for item in self.history]
                else:
                    self.images = []
                    self.reasoning.clear()
                    self.continuations.clear()
            # Refresh metadata for exact provider-request projection. The schemas,
            # system prompt and ordinary tool prefix stay fixed through this run.
            if not self.memory.prefix_mode:
                self.memory.current()
            response = self.call_model()
            if self.cancelled():
                return self.result("cancelled", reason="subagent_cancelled_during_request")
            if response.error or (response.fallback_used and not response.raw_text):
                return self.result("failed", reason="subagent_model_" + model_failure_reason(response.error or "fallback"))
            output = dict(response.parsed)
            carrier = output.pop(continuation.RESULT_FIELD, None)
            if isinstance(carrier, dict):
                for call in carrier.get("calls", []):
                    if call.get("id"):
                        self.continuations[call["id"]] = carrier
            reasoning = str(output.get(NATIVE_REASONING_CONTENT_FIELD) or "")
            output[TOOL_CAPABILITY_SELECTION_FIELD] = self.selection
            output, calls, rejected = self.engine._prepare_tool_round_decisions(final_output=output,
                user_message=self.request.task, client_context=self.client,
                profile_user_id=self.request.parent_profile_user_id, session_id=self.request.parent_session_id)
            if rejected:
                return self.result("failed", reason="subagent_tool_protocol_rejected", summary="\n".join(rejected))
            if not calls:
                if not self.valid_report(output):
                    response = self.call_model(repair=True)
                    output = dict(response.parsed)
                    if response.error or response.fallback_used or not self.valid_report(output):
                        return self.result("failed", reason="subagent_no_deliverable_result")
                if self.work.pending:
                    ready = self.work.collect()
                    if ready:
                        self.record_completions(ready)
                        continue
                    return self.result("failed", reason="subagent_unfinished_work", summary=output["summary"])
                # A steer admitted while the provider returned a final report is
                # still processed before closing; it is never silently ignored.
                if self.read_instructions():
                    continue
                runtime = getattr(self.engine, "host_subagent_jobs", None)
                if runtime and self.request.run_id and not runtime.store.seal_subagent_inputs(self.request.run_id,
                        owner=HostJobOwner(self.request.parent_profile_user_id, self.request.parent_session_id)):
                    continue
                self.raw_report = response.raw_text
                return self.result(output["status"], summary=output["summary"],
                    reason=str(output.get("reason") or "task_incomplete") if output["status"] == "failed" else "")
            limit = self.engine._max_tool_rounds()
            if limit > 0 and rounds >= limit:
                return self.result("failed", reason="subagent_tool_round_limit")
            rounds += 1
            for call in calls:
                if reasoning:
                    self.reasoning[str(call.get(TOOL_INVOCATION_ID_FIELD) or "")] = reasoning
            if not self.work.wait_for_resume(self.cancelled):
                return self.result("cancelled", reason="subagent_cancelled_before_dispatch")
            self.execute_calls(calls, response.raw_text)

    def run(self):
        live = self.engine.__dict__.setdefault("_live_task_work", {})
        live[self.request.child_session_id] = self.work
        try:
            with cancellation_scope(self.cancelled):
                if self.cancelled():
                    result = self.result("cancelled", reason="subagent_cancelled")
                else:
                    self.setup()
                    result = self.loop()
        except ModelRequestCancelled:
            result = self.result("cancelled", reason="subagent_cancelled_during_request")
        except Exception as exc:
            reason = str(exc) if str(exc).startswith("subagent_") else "subagent_step_" + type(exc).__name__
            result = self.result("failed", reason=reason)
        finally:
            runtime = getattr(self.engine, "host_subagent_jobs", None)
            if runtime and self.request.run_id:
                try:
                    runtime.store.close_subagent_inputs(self.request.run_id,
                        owner=HostJobOwner(self.request.parent_profile_user_id, self.request.parent_session_id))
                except Exception:
                    result = replace(result, status="failed", reason="subagent_input_close_failed")
                    self.raw_report = ""
                if self.memory is not None and self.memory.turn_id:
                    try:
                        self.read_instructions()
                    except Exception:
                        # Unacknowledged instructions remain in Jobs and will
                        # be read when this child is resumed.
                        pass
            live.pop(self.request.child_session_id, None)
            unconfirmed = self.work.close()
            self.work.finish()
        if unconfirmed:
            result = replace(result, status="failed", reason="task_work_cancellation_unconfirmed",
                summary=(result.summary + "\nTermination unconfirmed: " + ", ".join(unconfirmed))[:4000])
            self.raw_report = ""
        if self.memory is not None:
            try:
                self.memory.close(result, self.raw_report if result.status == "succeeded" else "")
            except Exception:
                result = replace(result, status="failed", reason="subagent_trace_complete_failed")
        return result
