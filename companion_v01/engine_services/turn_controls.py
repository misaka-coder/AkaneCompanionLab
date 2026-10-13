"""Observe stop/steering controls at the same boundaries as tool decisions."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Generator

from .. import desktop_pet_engine
from ..client_protocol import ClientMode
from ..tool_runtime import ToolExecutionResult
from ..turn_coordination import SteeringInput, cancellation_requested
from .turn_contracts import PreparedTurn
from .turn_round_state import ToolRoundState
from .turn_tools import execute_turn_tool_batch


@dataclass(frozen=True)
class PendingTurnControl:
    coordinator: Any
    snapshot: dict[str, Any]
    steers: list[SteeringInput]
    attachments: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class TurnControlDecision:
    coordinator: Any
    steering_applied: bool = False
    stopped_output: dict[str, Any] | None = None


def _drain_turn_controls(engine: Any, turn: PreparedTurn, state: ToolRoundState) -> PendingTurnControl:
    control_snapshot: dict[str, Any] = state.deferred_control_snapshot
    state.deferred_control_snapshot = {}
    turn_coordinator = getattr(engine, "turn_coordinator", None)
    if not control_snapshot and turn.request.turn_control_id and turn_coordinator is not None:
        control_snapshot = dict(turn_coordinator.drain(turn.request.turn_control_id) or {})
    if cancellation_requested():
        control_snapshot["stop_requested"] = True
    pending_steers = list(control_snapshot.get("steers") or [])
    steer_attachments = {}
    if turn.request.client_context.effective_mode == ClientMode.DESKTOP_PET:
        for steer in pending_steers:
            ids = getattr(steer, "current_attachment_ids", None)
            if ids is not None:
                steer_attachments[steer.source_id] = desktop_pet_engine.prepare_desktop_turn_attachments(
                    engine,
                    profile_user_id=turn.identity.profile_user_id,
                    session_id=turn.identity.session_id,
                    character_pack_id=turn.identity.character_pack_id,
                    attachment_ids=list(ids),
                    chat_model_override=turn.prompt.chat_model_override,
                )
    return PendingTurnControl(
        coordinator=turn_coordinator,
        snapshot=control_snapshot,
        steers=pending_steers,
        attachments=steer_attachments,
    )


def _stop_turn(
    engine: Any,
    turn: PreparedTurn,
    state: ToolRoundState,
    control: PendingTurnControl,
) -> Generator[dict[str, Any], None, dict[str, Any]]:
    control_snapshot = control.snapshot
    pending_steers = control.steers
    steer_attachments = control.attachments
    stop_reason = str(control_snapshot.get("stop_reason") or "user_stopped")
    if pending_steers:
        recorded_before_stop, failed_before_stop = engine._record_turn_steering_inputs(
            prepared_attachments=steer_attachments,
            steers=pending_steers,
            turn_id=turn.memory.turn_id,
            recent_raw_for_turn=state.recent_raw_for_turn,
            profile_user_id=turn.identity.profile_user_id,
            session_id=turn.identity.session_id,
            character_pack_id=turn.identity.character_pack_id,
        )
        engine._settle_turn_steering_receipts(
            steers=pending_steers,
            applied_source_ids=recorded_before_stop,
            failed_source_ids=failed_before_stop,
        )
        if turn.request.streaming:
            yield {
                "type": "turn_steer_recorded_before_stop",
                "count": len(recorded_before_stop),
                "failed_count": len(failed_before_stop),
            }
    latest_run_status: dict[str, str] = {}
    for completed_result in state.tool_results:
        updates = getattr(completed_result, "state_updates", None)
        execution = updates.get("capability_execution") if isinstance(updates, dict) else None
        if not isinstance(execution, dict):
            continue
        run_id = str(execution.get("run_id") or "").strip()
        if run_id:
            latest_run_status[run_id] = str(execution.get("status") or "").strip().lower()
    cancel_calls = [
        {"type": "exec_cancel", "run_id": run_id} for run_id, status in latest_run_status.items() if status == "running"
    ]
    cancellation_results: list[ToolExecutionResult] = []
    if cancel_calls:
        cancellation_results, cancellation_events = execute_turn_tool_batch(
            engine, turn, state, tool_calls=cancel_calls, now_ts=int(time.time()), include_native_context=False
        )
        if turn.request.streaming:
            for cancellation_event in cancellation_events:
                yield cancellation_event
    if turn.memory.turn_id and not turn.persistence.externally_managed_memcore_turn:
        engine._abort_memcore_input_turn(
            turn_id=turn.memory.turn_id,
            reason=stop_reason,
            chat_model_override=turn.prompt.chat_model_override,
            profile_user_id=turn.identity.profile_user_id,
            session_id=turn.identity.session_id,
            character_pack_id=turn.identity.character_pack_id,
        )
    stopped_output = {
        "status": "stopped",
        "reason": stop_reason,
        "speech": "",
        "speech_segments": [],
        "tool_events": state.tool_events,
        "npc_turns": state.tool_turns,
        "execution_cancellations": [
            dict(getattr(result, "state_updates", {}) or {}) for result in cancellation_results
        ],
        "trace_id": turn.identity.trace_id,
    }
    if turn.request.streaming:
        yield {"type": "turn_stopped", "reason": stop_reason, "payload": stopped_output}
    return stopped_output


def _apply_steering_inputs(
    engine: Any,
    turn: PreparedTurn,
    state: ToolRoundState,
    control: PendingTurnControl,
) -> Generator[dict[str, Any], None, bool]:
    pending_steers = control.steers
    steer_attachments = control.attachments
    applied_source_ids, failed_source_ids = engine._record_turn_steering_inputs(
        prepared_attachments=steer_attachments,
        steers=pending_steers,
        turn_id=turn.memory.turn_id,
        recent_raw_for_turn=state.recent_raw_for_turn,
        profile_user_id=turn.identity.profile_user_id,
        session_id=turn.identity.session_id,
        character_pack_id=turn.identity.character_pack_id,
    )
    engine._settle_turn_steering_receipts(
        steers=pending_steers,
        applied_source_ids=applied_source_ids,
        failed_source_ids=failed_source_ids,
    )
    if turn.request.streaming:
        yield {
            "type": "turn_steer_applied" if applied_source_ids else "turn_steer_failed",
            "count": len(applied_source_ids),
            "failed_count": len(failed_source_ids),
        }
    if applied_source_ids:
        steer_images = engine._steering_native_user_images(
            pending_steers,
            applied_source_ids=applied_source_ids,
        )
        for source_id in applied_source_ids:
            attachments = steer_attachments.get(source_id)
            if attachments is not None:
                turn.request.payload["current_attachment_ids"] = attachments["attachment_ids"]
                steer_images = engine._merge_model_image_inputs(attachments["images"], steer_images)
        if steer_images:
            turn.prompt.user_images = engine._merge_model_image_inputs(steer_images, turn.prompt.user_images)
            turn.prompt.execution_target = engine._upgrade_turn_execution_target_for_images(
                current_target=turn.prompt.execution_target,
                model_image_inputs=steer_images,
                chat_model_override=turn.prompt.chat_model_override,
            )
            turn.prompt.extra_user_context = engine._merge_extra_user_context(
                turn.prompt.extra_user_context,
                engine._build_native_user_image_prompt_context(steer_images),
            )
        state.budget.protocol_attempts = 0
        # Steering never closes the tool surface: the next model
        # decision keeps the same channel permissions and all
        # completed tool evidence from this open turn.
        return True
    return False


def apply_turn_controls(
    engine: Any,
    turn: PreparedTurn,
    state: ToolRoundState,
) -> Generator[dict[str, Any], None, TurnControlDecision]:
    control = _drain_turn_controls(engine, turn, state)
    if bool(control.snapshot.get("stop_requested")):
        stopped = yield from _stop_turn(engine, turn, state, control)
        return TurnControlDecision(control.coordinator, stopped_output=stopped)
    applied = False
    if control.steers:
        applied = yield from _apply_steering_inputs(engine, turn, state, control)
    return TurnControlDecision(control.coordinator, steering_applied=applied)
