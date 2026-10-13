"""Reply shaping, memory settlement and final event delivery for a turn."""

from __future__ import annotations

import time
import uuid
from typing import Any, Generator

from .. import final_output_engine
from ..client_protocol import ClientMode
from ..plugin_event_delivery import apply_plugin_notification_output_policy
from ..plugin_text_presentation import apply_plugin_text_presentation_policy
from ..text_utils import extract_semantic_tags
from .turn_contracts import PreparedTurn
from .turn_round_state import ToolRoundState


def _prepare_completed_output(
    engine: Any,
    turn: PreparedTurn,
    state: ToolRoundState,
) -> tuple[dict[str, Any], str]:
    """Shape the real reply and stage metadata before settling its memory owner."""
    user_record = turn.memory.user_record
    if not turn.persistence.speculative_voice_candidate:
        state.final_output = engine._apply_persona_state_to_final_output(
            profile_user_id=turn.identity.profile_user_id,
            session_id=turn.identity.session_id,
            final_output=state.final_output,
            now_ts=turn.request.now_ts,
            source_id=turn.memory.source_id,
            tool_result=state.tool_result,
        )
    if turn.request.external_event_turn:
        state.final_output["_provider_output_raw"] = state.provider_output_raw
        apply_plugin_text_presentation_policy(
            state.final_output,
            strip_leading_addresses_from=turn.request.payload.get(
                "plugin_text_strip_leading_addresses",
                (),
            ),
        )
        apply_plugin_notification_output_policy(
            state.final_output,
            {**turn.request.payload, "plugin_external_event": turn.request.plugin_external_event},
        )
    engine._attach_nonfatal_memcore_failure(state.final_output, turn.memory.failure)
    state.final_output["tool_events"] = state.tool_events
    state.final_output["npc_turns"] = state.tool_turns
    state.final_output["dialogue_turns"] = engine._build_dialogue_turns(
        preface_turn=state.preface_turns,
        npc_turns=state.tool_turns,
        final_speech=state.final_output.get("speech"),
        final_speech_segments=state.final_output.get("speech_segments"),
        speaker_name=engine._resolve_turn_speaker_identity(
            turn.request.client_context,
            turn.identity.character_pack_id,
        )["assistant_name"],
    )
    if not turn.persistence.speculative_voice_candidate:
        engine._apply_care_state_request(
            state.final_output,
            turn.request.client_context,
            profile_user_id=turn.identity.profile_user_id,
            character_pack_id=turn.identity.character_pack_id,
            payload=turn.request.payload,
            now_ts=turn.request.now_ts,
            event_id=turn.memory.source_id or turn.identity.trace_id,
        )
    state.provider_output_raw = str(state.final_output.pop("_provider_output_raw", state.provider_output_raw) or "")
    memory_annotation_status = engine._pop_memory_annotation_status(state.final_output)
    memory_tags = final_output_engine.extract_memory_search_terms(state.final_output)
    memory_metadata = state.final_output.get("memory_metadata")
    if not isinstance(memory_metadata, dict):
        memory_metadata = final_output_engine.normalize_memory_metadata(engine, None)
    else:
        memory_metadata = dict(memory_metadata)
    state.final_output["memory_metadata"] = memory_metadata
    state.final_output.pop("memory_tags", None)
    if not turn.persistence.transient_user_turn and not turn.request.external_event_turn:
        user_record = engine._apply_memory_metadata_to_user_record(
            user_record=user_record,
            memory_metadata=memory_metadata,
        )
    if turn.memory.turn_id and not turn.persistence.externally_managed_memcore_turn:
        engine._stage_memcore_turn_metadata(
            source_id=turn.memory.source_id,
            memory_metadata=memory_metadata,
            profile_user_id=turn.identity.profile_user_id,
            session_id=turn.identity.session_id,
            character_pack_id=turn.identity.character_pack_id,
            actor_stable_id=""
            if turn.persistence.current_input_transient or turn.request.external_event_turn
            else turn.identity.actor_stable_id,
            actor_display_name=""
            if turn.persistence.current_input_transient or turn.request.external_event_turn
            else turn.identity.actor_display_name,
        )
    if memory_tags and not turn.persistence.transient_user_turn and not turn.request.external_event_turn:
        user_record = engine._apply_memory_tags_to_user_record(
            user_record=user_record,
            memory_tags=memory_tags,
        )
    if turn.request.client_context.effective_mode != ClientMode.DESKTOP_PET:
        engine._schedule_visual_observations_for_payload(
            payload=state.final_output,
            profile_user_id=turn.identity.profile_user_id,
            session_id=turn.identity.session_id,
        )
    return memory_metadata, memory_annotation_status


def _settle_completed_memory(
    engine: Any,
    turn: PreparedTurn,
    state: ToolRoundState,
    *,
    memory_metadata: dict[str, Any],
    memory_annotation_status: str,
) -> None:
    """Persist visible/silent output or abort without taking over external voice ownership."""
    persist_assistant_turn = turn.persistence.persist_assistant_turn
    persist_assistant_turn = engine._should_persist_completed_assistant(
        persist_assistant_turn,
        state.final_output,
    )
    deliberate_silence = bool(state.final_output.get("_deliberate_silence"))
    if persist_assistant_turn:
        assistant_record = engine.store.add_message(
            profile_user_id=turn.identity.profile_user_id,
            session_id=turn.identity.session_id,
            character_pack_id=turn.identity.character_pack_id,
            role="assistant",
            content=state.final_output.get("speech", ""),
            timestamp=int(time.time()),
            semantic_tags=extract_semantic_tags(state.final_output.get("speech", "")),
            memory_metadata={
                **engine._build_assistant_timeline_metadata(state.final_output),
                **(
                    {"owner_private_shared": True, "channel": turn.persistence.memory_channel}
                    if turn.persistence.private_scope and turn.persistence.private_scope.shared
                    else {}
                ),
            },
        )
        engine._upsert_raw_record(assistant_record)
        if turn.memory.turn_id:
            if engine._finalize_memcore_input_turn_for_delivery(
                final_output=state.final_output,
                turn_id=turn.memory.turn_id,
                assistant_record=assistant_record,
                memory_metadata=memory_metadata,
                provider_output_raw=state.provider_output_raw,
                chat_model_override=turn.prompt.chat_model_override,
                execution_target=turn.prompt.execution_target,
                annotation_status=memory_annotation_status,
                profile_user_id=turn.identity.profile_user_id,
                session_id=turn.identity.session_id,
                character_pack_id=turn.identity.character_pack_id,
            ):
                engine._schedule_memcore_compaction(
                    profile_user_id=turn.identity.profile_user_id,
                    session_id=turn.identity.session_id,
                    character_pack_id=turn.identity.character_pack_id,
                    chat_model_override=turn.prompt.chat_model_override,
                )
        elif turn.request.prompt_scope == "qq_attention":
            standalone_result = engine._append_memcore_standalone_assistant(
                assistant_record=assistant_record,
                profile_user_id=turn.identity.profile_user_id,
                session_id=turn.identity.session_id,
                character_pack_id=turn.identity.character_pack_id,
            )
            if standalone_result and bool(standalone_result.get("ok")):
                engine._schedule_memcore_compaction(
                    profile_user_id=turn.identity.profile_user_id,
                    session_id=turn.identity.session_id,
                    character_pack_id=turn.identity.character_pack_id,
                    chat_model_override=turn.prompt.chat_model_override,
                )
            elif standalone_result:
                engine._attach_nonfatal_memcore_failure(
                    state.final_output,
                    {
                        "status": str(standalone_result.get("status") or "failed"),
                        "reason": "assistant_timeline_append_failed",
                        "detail": engine._safe_memcore_failure_code(
                            standalone_result.get("reason"),
                            fallback="standalone_assistant_rejected",
                        ),
                    },
                )
    elif (
        (deliberate_silence or state.final_output.get("_notification_suppressed"))
        and turn.memory.turn_id
        and not turn.persistence.externally_managed_memcore_turn
    ):
        # Preserve an actual silent provider envelope; a host-completed
        # action closes without appending any assistant entry. Neither path
        # creates a blank legacy bubble. Tool pairs remain replayable.
        silent_assistant_record = {
            "source_id": f"assistant_silent_{uuid.uuid4().hex}",
            "role": "assistant",
            "content": "",
            "timestamp": int(time.time()),
            "semantic_tags": [],
            "memory_metadata": engine._build_assistant_timeline_metadata(state.final_output),
        }
        if engine._finalize_memcore_input_turn_for_delivery(
            final_output=state.final_output,
            turn_id=turn.memory.turn_id,
            assistant_record=silent_assistant_record,
            memory_metadata=memory_metadata,
            provider_output_raw=state.provider_output_raw,
            chat_model_override=turn.prompt.chat_model_override,
            execution_target=turn.prompt.execution_target,
            annotation_status=memory_annotation_status,
            profile_user_id=turn.identity.profile_user_id,
            session_id=turn.identity.session_id,
            character_pack_id=turn.identity.character_pack_id,
        ):
            engine._schedule_memcore_compaction(
                profile_user_id=turn.identity.profile_user_id,
                session_id=turn.identity.session_id,
                character_pack_id=turn.identity.character_pack_id,
                chat_model_override=turn.prompt.chat_model_override,
            )
    elif turn.memory.turn_id and not turn.persistence.externally_managed_memcore_turn:
        engine._abort_memcore_input_turn(
            turn_id=turn.memory.turn_id,
            reason="assistant_turn_not_persisted",
            chat_model_override=turn.prompt.chat_model_override,
            profile_user_id=turn.identity.profile_user_id,
            session_id=turn.identity.session_id,
            character_pack_id=turn.identity.character_pack_id,
        )

    if persist_assistant_turn:
        engine.store.append_eval_turn(
            trace_id=turn.identity.trace_id,
            session_id=turn.identity.session_id,
            profile_user_id=turn.identity.profile_user_id,
            character_pack_id=turn.identity.character_pack_id,
            user_message=turn.request.user_message,
            router_json=turn.diagnostics.router_output,
            verifier_json=turn.diagnostics.verifier_output,
            final_json=state.final_output,
        )


def _build_completion_debug(engine: Any, turn: PreparedTurn, state: ToolRoundState) -> dict[str, Any]:
    debug_payload = engine._build_retrieval_debug_payload(
        router_output=turn.diagnostics.router_output,
        router_timing=turn.diagnostics.router_timing,
        retrieval_result=turn.diagnostics.retrieval_result,
        verifier_output=turn.diagnostics.verifier_output,
        verifier_timing=turn.diagnostics.verifier_timing,
        confirmed_snippets=turn.prompt.confirmed_snippets,
    )
    memory_tool_updates = [
        result.state_updates.get("memory_retrieval")
        for result in state.tool_results
        if isinstance(result.state_updates, dict) and result.state_updates.get("memory_retrieval")
    ]
    if memory_tool_updates:
        debug_payload["memory_tool"] = memory_tool_updates[-1]
        debug_payload["memory_tool_rounds"] = memory_tool_updates
    character_context_debug = engine._build_character_context_debug_payload(
        character_pack_id=turn.identity.character_pack_id,
        user_message=turn.request.user_message,
        tool_results=state.tool_results,
    )
    if character_context_debug:
        debug_payload["character_context"] = character_context_debug
    return debug_payload


def complete_turn(
    engine: Any,
    turn: PreparedTurn,
    state: ToolRoundState,
) -> Generator[dict[str, Any], None, dict[str, Any]]:
    """Settle memory, emit final_ui, then add diagnostics and emit final."""
    memory_metadata, memory_annotation_status = _prepare_completed_output(engine, turn, state)
    _settle_completed_memory(
        engine,
        turn,
        state,
        memory_metadata=memory_metadata,
        memory_annotation_status=memory_annotation_status,
    )
    if turn.request.streaming:
        ui_final_payload = dict(state.final_output)
        yield {"type": "final_ui", "payload": ui_final_payload}
    state.final_output["trace_id"] = turn.identity.trace_id
    state.final_output["_debug"] = _build_completion_debug(engine, turn, state)
    if turn.request.streaming:
        yield {"type": "final", "payload": state.final_output}
    return state.final_output
