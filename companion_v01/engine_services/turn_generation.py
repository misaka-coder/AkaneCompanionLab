"""Fresh generation parameters shared by initial and continuation rounds."""

from __future__ import annotations

from typing import Any, Generator

from .turn_contracts import PreparedTurn
from .turn_round_state import ToolRoundState
from .turn_completions import collect_turn_completions


def _generate_turn_round(
    engine: Any,
    turn: PreparedTurn,
    *,
    recent_raw: list[dict[str, Any]],
    extra_user_context: str,
    allow_tool_call: bool,
    post_user_turns: list[dict[str, Any]] | None = None,
    state: ToolRoundState | None = None,
) -> Generator[dict[str, Any], None, dict[str, Any]]:
    # Resolve mutable media, model target and projection state for every call.
    iterator = engine._generate_round(
            mode=turn.request.mode,
            session_id=turn.identity.session_id,
            profile_user_id=turn.identity.profile_user_id,
            user_message=turn.request.user_message,
            recent_raw=recent_raw,
            recent_episodic_summaries=turn.prompt.recent_episodic_summaries,
            recent_semantic_summaries=turn.prompt.recent_semantic_summaries,
            confirmed_snippets=turn.prompt.confirmed_snippets,
            now_ts=turn.request.now_ts,
            current_visual_payload=turn.request.payload.get("current_visual"),
            extra_user_context=extra_user_context,
            client_context=turn.request.client_context,
            resource_manifest=turn.prompt.resource_manifest,
            character_pack_id=turn.identity.character_pack_id,
            user_images=turn.prompt.user_images,
            allow_tool_call=allow_tool_call,
            final_debug_enabled=turn.prompt.final_debug_enabled,
            chat_model_override=turn.prompt.chat_model_override,
            execution_target=turn.prompt.execution_target,
            prompt_exclude_source_ids=turn.prompt.exclude_source_ids,
            domain_profile_id=turn.identity.domain_profile_id,
            prompt_scope=turn.request.prompt_scope,
            stable_system_context="",
            request_projection_state=turn.prompt.request_projection_state,
            post_user_turns=post_user_turns,
        )
    work = state.completion_work if state is not None else None
    if state is not None:
        state.presentation_started = False
    try:
        while True:
            try:
                event = next(iterator)
            except StopIteration as stop:
                output = stop.value
                break
            event_type = str(event.get("type") or "") if isinstance(event, dict) else ""
            if state is not None and turn.request.streaming and (event_type.startswith(("speech_", "utterance_"))
                or event_type in {"emotion", "assistant_emotion", "ui"}):
                state.presentation_started = True
            yield event
        if work is not None and isinstance(output, dict) and not output.get("_transient_final_failure"):
            work.commit()
        return output
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            close()


def generate_initial_round(
    engine: Any,
    turn: PreparedTurn,
    *, state: ToolRoundState | None = None,
) -> Generator[dict[str, Any], None, dict[str, Any]]:
    return (
        yield from _generate_turn_round(
            engine,
            turn,
            recent_raw=turn.prompt.recent_raw,
            extra_user_context=turn.prompt.extra_user_context,
            allow_tool_call=not turn.persistence.speculative_voice_candidate,
            state=state,
        )
    )


def generate_continuation_round(
    engine: Any,
    turn: PreparedTurn,
    state: ToolRoundState,
    *,
    allow_more: bool,
    stop_reason: str | None = None,
    round_control_feedback: str | None = None,
    collect_completions: bool = True,
) -> Generator[dict[str, Any], None, dict[str, Any]]:
    if collect_completions:
        collect_turn_completions(engine, turn, state)
    context_args = {
        "turn_extra_user_context": turn.prompt.extra_user_context,
        "tool_followups": state.tool_followups,
        "allow_more": allow_more,
    }
    if stop_reason is not None:
        context_args["stop_reason"] = stop_reason
    if round_control_feedback is not None:
        context_args["round_control_feedback"] = round_control_feedback
    extra_user_context = engine._build_tool_round_extra_context(**context_args)
    return (
        yield from _generate_turn_round(
            engine,
            turn,
            recent_raw=state.recent_raw_for_turn,
            extra_user_context=extra_user_context,
            allow_tool_call=allow_more,
            post_user_turns=state.tool_history_turns,
            state=state,
        )
    )
