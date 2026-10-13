"""Execute one tool batch and retain its real evidence for the next decision."""

from __future__ import annotations

import logging
from typing import Any, Generator

from ..tool_continuation import can_finish_tool_batch
from ..tool_invocation import TOOL_INVOCATION_ID_FIELD
from ..tool_runtime import ToolExecutionResult
from .turn_contracts import PreparedTurn
from .turn_generation import generate_continuation_round
from .turn_round_state import ToolRoundState

logger = logging.getLogger("akane.engine")


def execute_turn_tool_batch(
    engine: Any,
    turn: PreparedTurn,
    state: ToolRoundState,
    *,
    tool_calls: list[dict[str, Any]],
    now_ts: int,
    include_native_context: bool = True,
) -> tuple[list[ToolExecutionResult], list[dict[str, Any]]]:
    native_context = {}
    if include_native_context:
        native_context = {
            "native_reasoning_by_call_id": state.native_reasoning_by_call_id,
            "provider_continuations": state.provider_continuations,
        }
    # History and model/media target can change after a tool or a steering input.
    return engine._execute_and_record_tool_batch(
        tool_calls=tool_calls,
        final_output=state.final_output,
        provider_output_raw=state.provider_output_raw,
        tool_results=state.tool_results,
        tool_events=state.tool_events,
        tool_followups=state.tool_followups,
        tool_turns=state.tool_turns,
        recent_raw_for_turn=state.recent_raw_for_turn,
        profile_user_id=turn.identity.profile_user_id,
        session_id=turn.identity.session_id,
        character_pack_id=turn.identity.character_pack_id,
        now_ts=now_ts,
        current_user_source_id=turn.memory.source_id,
        client_context=turn.request.client_context,
        memory_exclude_source_ids=state.memory_exclude_source_ids,
        request_context={
            **turn.request.payload,
            "_host_visible_tool_history": turn.prompt.request_projection_state.get("visible_tool_history", []),
        },
        tool_history_turns=state.tool_history_turns,
        prompt_exclude_source_ids=turn.prompt.exclude_source_ids,
        recorded_tool_call_ids=state.recorded_tool_call_ids,
        domain_profile_id=turn.identity.domain_profile_id,
        memcore_turn_id=turn.memory.turn_id,
        execution_target=turn.prompt.execution_target,
        **native_context,
    )


def execute_tool_round(
    engine: Any,
    turn: PreparedTurn,
    state: ToolRoundState,
    *,
    tool_calls: list[dict[str, Any]],
    provider_continuation: Any,
    native_reasoning_content: str,
) -> Generator[dict[str, Any], None, None]:
    """Record protocol/preface evidence, execute, then continue or finish the action."""
    # A repeated signature is not an invalid decision. Polling, MCP
    # reload after a restart, a test rerun, or any external state change
    # can make byte-identical arguments produce fresh evidence. Tools
    # that mutate external state own their idempotency/receipt policy;
    # the generic model loop must not silently discard a legal call or
    # convert repetition into a terminal no-tools phase.
    executable_calls = list(tool_calls)
    turn_coordinator = getattr(engine, "turn_coordinator", None)
    if turn.request.turn_control_id and turn_coordinator is not None:
        turn_coordinator.mark_tool_activity(turn.request.turn_control_id)
    if isinstance(provider_continuation, dict):
        for call in provider_continuation.get("calls", []):
            if call.get("id"):
                state.provider_continuations[call["id"]] = provider_continuation
    if native_reasoning_content:
        for executable_call in executable_calls:
            call_id = str(executable_call.get(TOOL_INVOCATION_ID_FIELD) or "").strip()
            if call_id:
                state.native_reasoning_by_call_id[call_id] = native_reasoning_content

    preface_source_id = engine._record_assistant_preface_for_tool_call(
        tool_call=executable_calls[0],
        final_output=state.final_output,
        preface_turns=state.preface_turns,
        recent_raw_for_turn=state.recent_raw_for_turn,
        profile_user_id=turn.identity.profile_user_id,
        session_id=turn.identity.session_id,
        character_pack_id=turn.identity.character_pack_id,
        now_ts=turn.request.now_ts,
        date_label=turn.request.date_label,
        time_of_day=turn.request.time_of_day,
        memcore_turn_id=turn.memory.turn_id,
    )
    if (
        preface_source_id
        and engine._tool_call_uses_native_history(executable_calls[0])
        and preface_source_id not in turn.prompt.exclude_source_ids
    ):
        turn.prompt.exclude_source_ids.append(preface_source_id)
    if turn.request.streaming:
        working_event = engine._build_tool_working_stream_event(executable_calls[0])
        if len(executable_calls) > 1:
            working_event.update(
                {
                    "phase": "tool_batch",
                    "tool_count": len(executable_calls),
                    "tool_types": [str(call.get("type") or "") for call in executable_calls],
                    "message": "我一起查一下。",
                }
            )
        yield working_event
    batch_results, current_events = execute_turn_tool_batch(
        engine, turn, state, tool_calls=executable_calls, now_ts=turn.request.now_ts
    )
    state.tool_result = batch_results[-1] if batch_results else None
    state.budget.protocol_attempts = 0
    if turn.request.streaming:
        for stream_event in current_events:
            yield stream_event
    batch_memcore_failure = engine._tool_batch_memcore_failure(batch_results)
    if batch_memcore_failure is not None:
        turn.memory.failure = batch_memcore_failure
    if batch_memcore_failure is None and can_finish_tool_batch(batch_results):
        # Calls/results have already been paired in MemCore. Finish via
        # the existing silent-turn path, not a fabricated model reply.
        state.final_output = {
            "emotion": state.final_output.get("emotion", "normal"),
            "speech": "",
            "speech_segments": [],
            "tool_call": None,
            "_deliberate_silence": True,
            "_tool_finished_turn": True,
        }
        state.provider_output_raw = ""
        state.budget.round_index += 1
        # Re-enter the normal finalization boundary so a user steer or
        # stop arriving during the action is not lost.
        return
    turn.prompt.execution_target = engine._recompute_turn_execution_target(
        current_target=turn.prompt.execution_target,
        tool_results=batch_results,
        chat_model_override=turn.prompt.chat_model_override,
    )

    state.budget.round_index += 1
    hard_budget_reached = state.budget.hard_limit > 0 and state.budget.round_index >= state.budget.hard_limit
    round_control_feedback = ""
    remaining_rounds = max(0, state.budget.hard_limit - state.budget.round_index) if state.budget.hard_limit > 0 else 0
    if (
        state.budget.hard_limit > 0
        and not hard_budget_reached
        and not state.budget.warning_emitted
        and state.budget.warning_remaining > 0
        and remaining_rounds <= state.budget.warning_remaining
    ):
        state.budget.warning_emitted = True
        round_control_feedback = engine._build_tool_round_warning(
            used_rounds=state.budget.round_index,
            hard_limit=state.budget.hard_limit,
        )
        logger.info(
            "tool_round_warning session=%s used=%s hard=%s remaining=%s",
            turn.identity.session_id,
            state.budget.round_index,
            state.budget.hard_limit,
            remaining_rounds,
        )

    state.final_output = yield from generate_continuation_round(
        engine,
        turn,
        state,
        allow_more=not hard_budget_reached,
        stop_reason="tool_budget_exhausted" if hard_budget_reached else "",
        round_control_feedback=round_control_feedback,
    )
