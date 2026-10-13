"""Create a user memory record and the MemCore-compatible diagnostic envelope."""

from __future__ import annotations

from typing import Any

import config

from ..client_protocol import ClientMode
from ..text_utils import detect_time_of_day_from_text, extract_semantic_tags
from .turn_contracts import TurnDiagnostics, TurnIdentity, TurnMemoryState, TurnPersistence, TurnRequest


def prepare_user_record(
    engine: Any,
    identity: TurnIdentity,
    request: TurnRequest,
    persistence: TurnPersistence,
    *,
    memory_user_message: str,
    user_memory_source_id: str,
    message_addressing: dict[str, Any],
    forward_references: list[dict[str, Any]],
    precommitted_source_id: str,
    precommitted_voice_turn_id: str,
) -> dict[str, Any]:
    """Build transient voice/event input or persist the verified user input."""
    self = engine
    profile_user_id = identity.profile_user_id
    session_id = identity.session_id
    turn_character_pack_id = identity.character_pack_id
    payload = request.payload
    client_context = request.client_context
    user_message = request.user_message
    now_ts = request.now_ts
    date_label = request.date_label
    time_of_day = request.time_of_day
    plugin_external_event = request.plugin_external_event
    transient_user_turn = persistence.transient_user_turn
    externally_managed_memcore_turn = persistence.externally_managed_memcore_turn
    memory_channel = persistence.memory_channel
    private_scope = persistence.private_scope
    if transient_user_turn:
        user_record = self._build_transient_user_record(
            user_message=user_message,
            now_ts=now_ts,
            date_label=date_label,
            time_of_day=time_of_day,
        )
        if externally_managed_memcore_turn:
            user_record.update(
                {
                    "source_id": precommitted_source_id,
                    "role": "message.user.voice",
                    "kind": "message.user.voice",
                    "semantic_text": user_message,
                    "payload": {
                        "text": user_message,
                        "modality": "voice",
                        "voice_turn_id": precommitted_voice_turn_id,
                    },
                }
            )
    else:
        user_memory_metadata = (
            self._external_event_memory_metadata(plugin_external_event) if plugin_external_event is not None else {}
        )
        if memory_channel:
            user_memory_metadata["channel"] = memory_channel
        if private_scope and private_scope.shared:
            user_memory_metadata["owner_private_shared"] = True
        read_policy = getattr(self, "memory_read_policy", None)
        if (
            client_context.effective_mode == ClientMode.QQ_TEXT
            and read_policy is not None
            and read_policy.is_verified_qq_turn(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=turn_character_pack_id,
                reference=payload.get("_memory_qq_ref"),
            )
        ):
            user_memory_metadata["client_mode"] = "qq_text"
        if message_addressing:
            user_memory_metadata["message_addressing"] = message_addressing
        if forward_references:
            user_memory_metadata["forward_references"] = forward_references
        if payload.get("current_attachment_ids"):
            user_memory_metadata["current_attachment_ids"] = list(payload["current_attachment_ids"])
        user_record = self.store.add_message(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=turn_character_pack_id,
            role=(f"event.{plugin_external_event['event_type']}" if plugin_external_event is not None else "user"),
            content=memory_user_message,
            timestamp=now_ts,
            date_label=date_label,
            time_of_day=time_of_day,
            semantic_tags=extract_semantic_tags(memory_user_message),
            memory_metadata=user_memory_metadata or None,
            source_id=user_memory_source_id,
        )
        source_message_id = str(payload.get("source_message_id") or "").strip()
        if source_message_id:
            user_record["source_message_id"] = source_message_id

    if memory_channel:
        user_record["channel"] = memory_channel
    return user_record


def build_turn_diagnostics(user_message: str) -> TurnDiagnostics:
    """Keep the compatibility fields for a turn driven by MemCore tools."""
    router_output = {
        "need_retrieval": False,
        "route": "memcore",
        "rewritten_query": "",
        "keywords": [],
        "time_hint": {
            "date_label": None,
            "time_of_day": detect_time_of_day_from_text(user_message),
            "relative_time": None,
        },
        "index_current_message": False,
        "reason": "memcore 模式下不运行旧前置检索；由模型按需调用 memcore 记忆工具。",
        "confidence": 1.0,
    }
    router_timing = {"stage": "router", "branch": "memcore", "mode": "shortcut", "ready_event_type": "decision"}
    retrieval_result = {
        "filtered_candidate_count": 0,
        "time_filter": {"date_label": None, "time_of_day": None, "relative_time": None, "matched": False},
        "fused_hits": [],
        "memory_snippets": [],
    }
    verifier_output = {
        "match_result": "skip",
        "match_score": 0.0,
        "need_retry": False,
        "selected_indexes": [],
        "retry_query": "",
        "retry_keywords": [],
        "retry_time_hint": None,
        "reason": "memcore",
    }
    verifier_timing = {"mode": "skip", "attempts": [], "selected_attempt": None}
    return TurnDiagnostics(
        router_output=router_output,
        router_timing=router_timing,
        retrieval_result=retrieval_result,
        verifier_output=verifier_output,
        verifier_timing=verifier_timing,
    )


def open_turn_memory(
    engine: Any,
    identity: TurnIdentity,
    persistence: TurnPersistence,
    *,
    user_record: dict[str, Any],
    current_source_id: str,
    plugin_external_event: dict[str, Any] | None,
    attention_reference_source_ids: list[str],
    precommitted_turn_id: str,
    target_actor_id: str,
    target_actor_display_name: str,
) -> TurnMemoryState:
    """Select external voice ownership, hidden host input or a durable user turn."""
    self = engine
    profile_user_id = identity.profile_user_id
    session_id = identity.session_id
    turn_character_pack_id = identity.character_pack_id
    actor_stable_id = identity.actor_stable_id
    actor_display_name = identity.actor_display_name
    externally_managed_memcore_turn = persistence.externally_managed_memcore_turn
    current_input_transient = persistence.current_input_transient
    speculative_voice_candidate = persistence.speculative_voice_candidate
    transient_user_turn = persistence.transient_user_turn
    turn_projection_source_id = current_source_id
    memcore_turn_id = ""
    turn_memcore_failure: dict[str, Any] | None = None
    if externally_managed_memcore_turn:
        memcore_turn_id = precommitted_turn_id
    elif current_input_transient and not speculative_voice_candidate:
        memcore_open = self._begin_memcore_hidden_host_turn(
            referenced_source_ids=attention_reference_source_ids,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=turn_character_pack_id,
        )
        memcore_turn_id = str((memcore_open or {}).get("turn_id") or "").strip()
        hidden_source_id = str((memcore_open or {}).get("source_id") or "").strip()
        if hidden_source_id:
            turn_projection_source_id = hidden_source_id
        turn_memcore_failure = self._memcore_input_turn_failure(memcore_open)
    elif not transient_user_turn:
        self._upsert_raw_record(user_record)
        memcore_open = self._begin_memcore_input_turn(
            user_record=user_record,
            external_event=plugin_external_event,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=turn_character_pack_id,
            actor_stable_id=actor_stable_id,
            actor_display_name=actor_display_name,
            target_actor_id=target_actor_id,
            target_actor_display_name=target_actor_display_name,
        )
        memcore_turn_id = str((memcore_open or {}).get("turn_id") or "").strip()
        turn_memcore_failure = self._memcore_input_turn_failure(memcore_open)

    return TurnMemoryState(
        user_record=user_record,
        source_id=turn_projection_source_id,
        turn_id=memcore_turn_id,
        failure=turn_memcore_failure,
    )


def build_request_projection_state(
    identity: TurnIdentity,
    request: TurnRequest,
    persistence: TurnPersistence,
    memory: TurnMemoryState,
) -> dict[str, Any]:
    payload = request.payload
    actor_stable_id = identity.actor_stable_id
    client_context = request.client_context
    current_input_transient = persistence.current_input_transient
    turn_projection_source_id = memory.source_id
    # Request-frozen user text is turn-scoped, not generation-scoped.  A
    # tool continuation rebuilds prompt context, but it must keep the exact
    # current user message that crossed the first provider boundary while
    # appending only new action/result projections after it.
    request_projection_state: dict[str, Any] = {
        # The current stimulus is a turn invariant.  Tool prefaces and
        # observations are appended to ``recent_raw_for_turn`` later, so
        # rediscovering the stimulus from the mutable history tail can
        # lose its source id and abort an otherwise successful tool turn.
        # Transient events have no provider-visible durable stimulus: the
        # event is the request-local user tail, while the hidden
        # host record only owns tools/finalization.  Asking MemCore to
        # project that prompt-invisible record as the current message makes
        # an otherwise valid history read fail with
        # ``current_source_not_projected``.
        "current_user_source_id": "" if current_input_transient else turn_projection_source_id,
        # Keep current-turn event content out of persistent prompt history;
        # its tools and actual assistant reply still belong to a real turn.
        "record_request_projection": not current_input_transient,
        "current_input_transient": current_input_transient,
    }
    if actor_stable_id:
        request_projection_state["actor_stable_id"] = actor_stable_id
    actor_profile_user_id = str(payload.get("actor_profile_user_id") or "").strip()
    if actor_profile_user_id:
        request_projection_state["actor_profile_user_id"] = actor_profile_user_id
        request_projection_state["authorization_profile_user_id"] = actor_profile_user_id
    from .turn_context import resolve_qq_actor_relation

    current_actor_relation = resolve_qq_actor_relation(
        client_mode=client_context.effective_mode,
        actor_stable_id=actor_stable_id,
        master_qq=getattr(config, "MASTER_QQ", ""),
    )
    if current_actor_relation:
        request_projection_state["current_actor_relation"] = current_actor_relation
    return request_projection_state
