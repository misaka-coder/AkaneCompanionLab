"""Restore terminal-result references from actual provider input, not job scans."""
from __future__ import annotations

import json
import re

from services.tool_history import isolate_tool_history

from ..native_tool_schema import native_tool_model_name_map
from ..tool_orchestration.feedback import append_structured_artifact_receipts, shape_tool_followup
from .results import completion_result
from .contracts import _registered_failure_result


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(block.get("text") or "") for block in content
                         if isinstance(block, dict) and block.get("type") == "text")
    return ""


def _exchanges(messages):
    """Read intact native pairs, retaining call identity in both protocols."""
    calls, results = {}, {}
    for message in isolate_tool_history(messages).messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                function = call["function"]
                try:
                    arguments = json.loads(function["arguments"])
                except (ValueError, TypeError):
                    continue
                calls[call["id"]] = (function["name"], arguments)
            blocks = message.get("content") if isinstance(message.get("content"), list) else []
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    calls[block["id"]] = (block["name"], block["input"])
        elif message.get("role") == "tool":
            results[message["tool_call_id"]] = _text(message.get("content"))
        elif message.get("role") == "user" and isinstance(message.get("content"), list):
            for block in message["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    results[block["tool_use_id"]] = _text(block.get("content"))
    return [(call_id, *call, results[call_id]) for call_id, call in calls.items() if call_id in results]


def _direct_result_text(engine, job):
    result = completion_result(job, identity=False)
    # A live managed result can be empty; completion_result's no-output label
    # is for the idle presenter, not the original tool-history projection.
    if not job.result_summary and not job.last_error:
        result.followup_context = ""
    text = append_structured_artifact_receipts(result.followup_context, stream_events=result.stream_events)
    shaped = shape_tool_followup(result.followup_envelope.with_content(text)
        if result.followup_envelope is not None else text, tool_type=job.capability_id)
    return engine._sanitize_tool_trace_text(shaped)


def _matches_direct_result(engine, job, text):
    if text == _direct_result_text(engine, job):
        return True
    # Admission can fail after the durable job was created. Its original
    # public failure receipt differs from the later idle result presenter.
    if job.status == "failed" and job.last_error:
        result = _registered_failure_result(job.capability_id, job_id=job.job_id, job=job, reason=job.last_error)
        return text == engine._sanitize_tool_trace_text(result.followup_context)
    return False


def _shell_terminal_text(job, run_id, text):
    """Recognize producer-owned terminal markers, never a running output page."""
    lines = text.splitlines()
    if any(line.startswith("[running:") for line in lines):
        return False
    if job.status == "succeeded":
        marker = f"[exit code: 0; run_id={run_id}]"
        return marker in lines
    if job.status == "failed":
        return any(re.fullmatch(r"\[(?:exit code: -?\d+; reason=[^\r\n]+|timed out; reason=[^\r\n]+; process_group=terminated)\]", line)
                   for line in lines)
    if job.status == "cancelled":
        return any(re.fullmatch(r"\[cancelled; reason=[^\r\n]+\]", line) for line in lines)
    return False


def request_completion_jobs(engine, work, context, request):
    """Return scope-verified jobs whose terminal evidence is actually present.

    Direct managed results resolve through the existing invocation index and
    must equal the durable terminal presentation (an accepted/running result
    must not consume a job that happened to finish later). Explicit status
    queries resolve their authorized public job ID and terminal header. Host
    boundary events resolve their trusted source IDs and exact visible text.
    No result-body search or pending-completion-library scan occurs.
    """
    owner, pack, channel, _turn_id = work.key
    store = work.registry.store
    jobs = {}
    aliases = native_tool_model_name_map(context.get("native_tools"))
    projection = context.get("memcore_projection_read") or {}
    descriptors = [item for item in projection.get("current_turn_messages") or [] if isinstance(item, dict)]
    # Closed earlier turns can also be replayed as history in a later request.
    history = list(context.get("history_turns") or [])
    start = int(context.get("memcore_history_start_index") or 0)
    projected_history = history[start:]
    source_groups = list(projection.get("history_message_source_ids") or [])
    # The high-water protection trims oldest complete history groups while
    # retaining the original surface metadata. Align the surviving suffix,
    # not the old first source IDs, before identifying boundary events.
    source_groups = source_groups[-len(projected_history):] if projected_history else []
    for payload, sources in zip(projected_history, source_groups):
        descriptors.append({"payload": payload, "source_ids": sources})
    actual = list(request.get("history_messages") or [])
    authoritative = [item.get("payload") or {} for item in descriptors if item.get("source_ids")]
    owned_pairs = set((call_id, name, json.dumps(args, sort_keys=True), text)
                      for call_id, name, args, text in _exchanges(authoritative))
    # A transient host turn or narrow current-turn recovery may deliberately
    # have no durable freeze observer. Its already-authorized, in-memory tool
    # history is still a real input, but it can only confirm live references;
    # it cannot recover arbitrary jobs without source-owned descriptors.
    live_pairs = set((call_id, name, json.dumps(args, sort_keys=True), text)
                     for call_id, name, args, text in _exchanges(context.get("post_user_turns") or []))
    live_ids = set(work.queried) | set(work.offered)

    def eligible(job):
        return (job is not None and job.owner == owner and job.character_pack_id == pack and job.channel == channel
                and job.status in {"succeeded", "failed", "cancelled"} and not job.model_consumed_by
                and not job.scope_revoked_reason and not job.payload.get("task_scope"))

    for call_id, name, arguments, text in _exchanges(actual):
        identity = (call_id, name, json.dumps(arguments, sort_keys=True), text)
        source_owned = identity in owned_pairs
        if not source_owned and identity not in live_pairs:
            continue
        capability_id = aliases.get(name, name)
        if capability_id == "capability_invoke":
            # Execution routes the carrier to this target, while native
            # history retains the original name and outer arguments. Match
            # that intact pair first, then resolve the same job identity.
            target = arguments.get("capability_id") if isinstance(arguments, dict) else None
            values = arguments.get("arguments") if isinstance(arguments, dict) else None
            if not isinstance(target, str) or not target or not isinstance(values, dict):
                continue
            capability_id, arguments = target, values
        if capability_id == "exec_status" and isinstance(arguments, dict):
            public_id = str(arguments.get("run_id") or "")
            job = (store.get(public_id, owner=owner) if public_id.startswith("job_") else
                store.get_by_key(owner=owner, capability_source="execution", capability_id="exec_run", idempotency_key=public_id))
            if not eligible(job) or (not source_owned and job.job_id not in live_ids):
                continue
            if job.capability_source == "execution":
                if job.payload.get("run_id") == public_id and _shell_terminal_text(job, public_id, text):
                    jobs[job.job_id] = job
                continue
            header = f"job_id={job.job_id}; tool={job.capability_id}; status={job.status}"
            page_header = f"job_id={job.job_id}; status={job.status}; next_cursor="
            if text.startswith(header + "\n") or text.startswith(header + "; reason=") or text.startswith(page_header):
                jobs[job.job_id] = job
        else:
            job = store.get_by_key(owner=owner, capability_source="tool", capability_id=capability_id,
                idempotency_key=call_id)
            if (eligible(job) and (source_owned or job.job_id in live_ids)
                and job.tool_call_id == call_id and _matches_direct_result(engine, job, text)):
                jobs[job.job_id] = job

    actual_texts = {_text(message.get("content")) for message in actual
                    if isinstance(message, dict) and message.get("role") == "user"}
    for descriptor in descriptors:
        payload = descriptor.get("payload") or {}
        if payload.get("role") != "user" or _text(payload.get("content")) not in actual_texts:
            continue
        for source_id in descriptor.get("source_ids") or []:
            source_id = str(source_id)
            if not source_id.startswith("job-completed:job_") or not source_id.endswith(":boundary"):
                continue
            job = store.get(source_id[len("job-completed:"):-len(":boundary")], owner=owner)
            if eligible(job) and source_id == job.completion_event_id + ":boundary":
                jobs[job.job_id] = job
    return list(jobs.values())
