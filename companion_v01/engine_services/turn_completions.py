"""Collect host terminal facts at the same boundary for both model transports."""
import logging
import time

from ..host_jobs import HostJobOwner
from ..turn_coordination import cancellation_requested, current_cancellation_check

logger = logging.getLogger("akane.turn_completions")


def begin_turn_work(engine, turn):
    runtime = getattr(engine, "host_tool_jobs", None)
    registry = getattr(runtime, "completion_work", None)
    if registry is None or turn.persistence.speculative_voice_candidate:
        return None
    coordinator = getattr(engine, "turn_coordinator", None)
    def cancelled():
        return bool(cancellation_requested() or (coordinator is not None and turn.request.turn_control_id
            and coordinator.stop_requested(turn.request.turn_control_id)))
    return registry.begin(owner=HostJobOwner(turn.identity.profile_user_id, turn.identity.session_id),
        character_pack_id=turn.identity.character_pack_id,
        channel=str(turn.request.client_context.effective_mode.value), turn_id=turn.memory.turn_id,
        request_bound=bool(getattr(getattr(engine, "llm", None), "supports_request_observer", False)),
        cancelled=cancelled)


def seed_idle_completion_results(engine, turn, state):
    """Resolve trusted queue job IDs after the original scope was restored."""
    from ..host_tool_job_runtime.results import completion_result
    store = getattr(engine, "job_store", None)
    results = []
    source_ids = []
    for job_id in turn.request.payload.get("_host_completion_job_ids", []):
        job = store.get(job_id, owner=HostJobOwner(turn.identity.profile_user_id, turn.identity.session_id))
        if (job is None or job.scope_revoked_reason or job.model_consumed_by
            or job.character_pack_id != turn.identity.character_pack_id
            or job.channel != turn.request.client_context.effective_mode.value):
            continue
        results.append(completion_result(job))
        source_ids.append(job.completion_event_id)
    if not results:
        return
    state.tool_results.extend(results)
    state.tool_events.extend(event for result in results for event in result.stream_events)
    record_completion_dialogues(engine, turn, state, results)
    turn.prompt.user_images = engine._merge_tool_model_image_inputs(turn.prompt.user_images, results)
    images = engine._merge_tool_model_image_inputs([], results)
    if images and not engine._record_memcore_tool_media_input(model_image_inputs=images,
        related_source_ids=source_ids, profile_user_id=turn.identity.profile_user_id,
        session_id=turn.identity.session_id, character_pack_id=turn.identity.character_pack_id,
        now_ts=turn.request.now_ts, memcore_turn_id=turn.memory.turn_id):
        raise RuntimeError("completion_media_trace_failed")
    turn.prompt.execution_target = engine._recompute_turn_execution_target(
        current_target=turn.prompt.execution_target, tool_results=results,
        chat_model_override=turn.prompt.chat_model_override)


def collect_turn_completions(engine, turn, state):
    work = state.completion_work
    if work is None:
        return False
    started = time.monotonic()
    evidence = work.collect()
    if not evidence:
        return False
    manager = getattr(engine, "memcore_manager", None)
    source_ids = []
    results = []
    for item in evidence:
        job, result = item.job, item.result
        source_id = job.completion_event_id + ":boundary"
        recorded = manager.append_standalone_event(
            {"event_type": "task.work.completed", "source": "host.jobs",
             "fields": {"result": engine._sanitize_tool_trace_text(result.followup_context)}},
            source_id=source_id, turn_id=turn.memory.turn_id,
            profile_user_id=turn.identity.profile_user_id, session_id=turn.identity.session_id,
            character_pack_id=turn.identity.character_pack_id,
            timestamp=int(job.finished_at or time.time()),
        )
        if not recorded.get("ok"):
            raise RuntimeError("completion_trace_not_saved")
        source_ids.append(source_id)
        state.tool_results.append(result)
        state.tool_events.extend(result.stream_events)
        results.append(result)
    record_completion_dialogues(engine, turn, state, results)
    images = engine._merge_tool_model_image_inputs([], results)
    if images:
        turn.prompt.execution_target = engine._recompute_turn_execution_target(
            current_target=turn.prompt.execution_target, tool_results=results,
            chat_model_override=turn.prompt.chat_model_override)
    media_ids = engine._record_memcore_tool_media_input(model_image_inputs=images,
        related_source_ids=source_ids, profile_user_id=turn.identity.profile_user_id,
        session_id=turn.identity.session_id, character_pack_id=turn.identity.character_pack_id,
        now_ts=turn.request.now_ts, memcore_turn_id=turn.memory.turn_id) if images else []
    if images and not media_ids:
        raise RuntimeError("completion_media_trace_failed")
    projected = engine._append_tool_history_batch(
        tool_history_turns=state.tool_history_turns, items=[], trace_source_ids=source_ids,
        media_source_ids=media_ids, model_image_inputs=images,
        append_only=True, memcore_turn_id=turn.memory.turn_id,
        provider_profile=str(getattr(turn.prompt.execution_target, "protocol", "") or ""),
        current_user_source_id=turn.memory.source_id, profile_user_id=turn.identity.profile_user_id,
        session_id=turn.identity.session_id, character_pack_id=turn.identity.character_pack_id,
    )
    if not projected.get("ok"):
        raise RuntimeError("completion_projection_failed")
    turn.prompt.exclude_source_ids.extend(source_id for source_id in [*source_ids, *media_ids]
                                         if source_id not in turn.prompt.exclude_source_ids)
    logger.info("tool_completion_boundary count=%s bytes=%s duration_ms=%.3f", len(evidence),
        sum(len(item.result.followup_context.encode("utf-8")) for item in evidence),
        (time.monotonic() - started) * 1000)
    return True


def record_completion_dialogues(engine, turn, state, results):
    raw_turns = [raw for result in results for raw in result.raw_turns]
    if raw_turns:
        engine._record_tool_dialogue_turns(raw_turns=raw_turns,
            tool_turns=state.tool_turns, recent_raw_for_turn=state.recent_raw_for_turn,
            profile_user_id=turn.identity.profile_user_id, session_id=turn.identity.session_id,
            character_pack_id=turn.identity.character_pack_id, now_ts=turn.request.now_ts)


def turn_wait_cancelled(engine, turn):
    check = current_cancellation_check()
    coordinator = getattr(engine, "turn_coordinator", None)
    def cancelled():
        return bool((check is not None and check()) or (coordinator is not None
            and turn.request.turn_control_id and coordinator.has_pending_controls(turn.request.turn_control_id)))
    return cancelled
