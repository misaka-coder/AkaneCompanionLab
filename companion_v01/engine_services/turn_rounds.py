"""One authoritative tool/decision loop for synchronous and streaming turns."""

from __future__ import annotations

import logging
from typing import Any, Generator

import config
from services import provider_continuation as continuation

from ..tool_invocation import NATIVE_REASONING_CONTENT_FIELD
from .turn_contracts import PreparedTurn
from .turn_controls import apply_turn_controls
from .turn_generation import generate_continuation_round, generate_initial_round
from .turn_round_state import ToolRoundBudget, ToolRoundState, TurnRoundOutcome
from .turn_tools import execute_tool_round
from .turn_completions import begin_turn_work, collect_turn_completions, seed_idle_completion_results

logger = logging.getLogger("akane.engine")


def _new_round_state(engine: Any, turn: PreparedTurn, final_output: dict[str, Any]) -> ToolRoundState:
    if turn.persistence.speculative_voice_candidate:
        hard_limit = -1
        warning_remaining = 0
    else:
        hard_limit = engine._max_tool_rounds(domain_profile_id=turn.identity.domain_profile_id)
        warning_remaining = engine._tool_round_warning_remaining(hard_limit=hard_limit)
    return ToolRoundState(
        final_output=final_output,
        recent_raw_for_turn=list(turn.prompt.recent_raw),
        budget=ToolRoundBudget(
            hard_limit=hard_limit,
            warning_remaining=warning_remaining,
            protocol_retry_limit=max(1, int(getattr(config, "CHAT_MODEL_DECISION_MAX_ATTEMPTS", 3) or 3)),
        ),
        memory_exclude_source_ids=[
            str(hit.get("source_id") or "").strip()
            for hit in turn.diagnostics.retrieval_result.get("fused_hits", [])
            if str(hit.get("source_id") or "").strip()
        ],
    )


def run_turn_rounds(engine: Any, turn: PreparedTurn) -> Generator[dict[str, Any], None, TurnRoundOutcome]:
    """Generate, drain controls and execute only real tool batches against one budget."""
    state = _new_round_state(engine, turn, {})
    state.completion_work = begin_turn_work(engine, turn)
    try:
        return (yield from _run_turn_rounds(engine, turn, state))
    finally:
        if state.completion_work is not None:
            state.completion_work.registry.close(state.completion_work)


def _run_turn_rounds(engine, turn, state):
    seed_idle_completion_results(engine, turn, state)
    state.final_output = yield from generate_initial_round(engine, turn, state=state)
    # Executed batches and rejected model decisions consume independent budgets.
    while state.budget.hard_limit == 0 or state.budget.round_index <= state.budget.hard_limit:
        control = yield from apply_turn_controls(engine, turn, state)
        if control.stopped_output is not None:
            return TurnRoundOutcome(state, stopped_output=control.stopped_output)
        turn_coordinator = control.coordinator
        if control.steering_applied:
            state.final_output = yield from generate_continuation_round(engine, turn, state, allow_more=True)
            continue
        state.provider_output_raw = str(state.final_output.pop("_provider_output_raw", "") or "")
        native_reasoning_content = str(state.final_output.pop(NATIVE_REASONING_CONTENT_FIELD, "") or "").strip()
        provider_continuation = state.final_output.pop(continuation.RESULT_FIELD, None)
        state.final_output, tool_calls, rejections = engine._prepare_tool_round_decisions(
            final_output=state.final_output,
            user_message=turn.request.user_message,
            client_context=turn.request.client_context,
            profile_user_id=turn.identity.profile_user_id,
            session_id=turn.identity.session_id,
            domain_profile_id=turn.identity.domain_profile_id,
        )
        if tool_calls and state.budget.hard_limit > 0 and state.budget.round_index >= state.budget.hard_limit:
            blocked_calls = "；".join(engine._describe_tool_call_for_prompt(tool_call) for tool_call in tool_calls)
            state.tool_followups.append(
                f"模型在本轮已经执行 {state.budget.round_index}/{state.budget.hard_limit} 轮工具后又请求：{blocked_calls}。"
                "这些额外调用没有执行；上方最后一批工具结果仍是真实结果。"
            )
            logger.warning(
                "tool_round_hard_limit session=%s rounds=%s blocked=%s",
                turn.identity.session_id,
                state.budget.round_index,
                len(tool_calls),
            )
            state.final_output = yield from generate_continuation_round(
                engine, turn, state, allow_more=False, stop_reason="tool_budget_exhausted"
            )
            break
        if turn.request.streaming:
            native_preface_text = str(state.final_output.pop("_native_preface_text", "") or "").strip()
            native_preface_streamed = bool(state.final_output.pop("_native_preface_streamed", False))
            if (
                native_preface_text
                and not native_preface_streamed
                and tool_calls
                and engine._tool_call_allows_assistant_preface(tool_calls[0])
            ):
                yield {"type": "speech_segment", "index": 0, "text": native_preface_text}
            yield {
                "type": "assistant_stage_decision",
                "has_tool_call": bool(tool_calls),
                "tool_type": str((tool_calls[0] if tool_calls else {}).get("type") or ""),
                "tool_types": [str(call.get("type") or "") for call in tool_calls],
                "tool_count": len(tool_calls),
                "rejected_tool_call": bool(rejections),
            }
        if not tool_calls:
            if not rejections:
                work = state.completion_work
                if work is not None and not state.final_output.get("_transient_final_failure") and not state.presentation_started:
                    # Collect ready facts only. Handed-back tasks cannot force
                    # an implicit join or replace a reply already streamed.
                    if collect_turn_completions(engine, turn, state):
                        state.final_output = yield from generate_continuation_round(engine, turn, state,
                            allow_more=state.budget.hard_limit <= 0 or state.budget.round_index < state.budget.hard_limit,
                            collect_completions=False)
                        continue
                if turn.request.turn_control_id and turn_coordinator is not None:
                    finalization = dict(turn_coordinator.begin_finalization(turn.request.turn_control_id) or {})
                    if finalization.get("stop_requested") or finalization.get("steers"):
                        state.deferred_control_snapshot = finalization
                        continue
                break
            engine._record_tool_call_rejection(
                final_output=state.final_output,
                rejection="\n".join(rejections),
                tool_followups=state.tool_followups,
                session_id=turn.identity.session_id,
            )
            state.budget.protocol_attempts += 1
            if state.budget.protocol_attempts >= state.budget.protocol_retry_limit:
                state.final_output = engine._tool_protocol_failure_output(rejections=rejections)
                break
            state.final_output = yield from generate_continuation_round(
                engine, turn, state, allow_more=True, stop_reason=""
            )
            continue
        if rejections:
            state.tool_followups.extend(rejections)
        yield from execute_tool_round(
            engine,
            turn,
            state,
            tool_calls=tool_calls,
            provider_continuation=provider_continuation,
            native_reasoning_content=native_reasoning_content,
        )

    return TurnRoundOutcome(state)
