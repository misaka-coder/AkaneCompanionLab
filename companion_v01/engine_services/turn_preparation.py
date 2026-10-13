"""Resolve a real input turn before the first model generation."""

from __future__ import annotations

import time
import uuid
import logging
from typing import Any

from ..persona_config import PERSONA
from ..text_utils import (
    detect_time_of_day_from_text,
    infer_time_of_day,
    timestamp_to_date_label,
)
from .turn_contracts import (
    DeferredTurn,
    PreparedTurn,
    TurnIdentity,
    TurnPersistence,
    TurnPromptState,
    TurnRequest,
)

from .turn_input_memory import (
    build_request_projection_state,
    build_turn_diagnostics,
    open_turn_memory,
    prepare_user_record,
)
from .turn_media import prepare_turn_media
from ..local_timings import LocalTimings, timing_scope

logger = logging.getLogger("akane.local_timing")


def prepare_turn(
    engine: Any,
    payload: dict[str, Any],
    *,
    mode: str,
    _precommitted_memcore_turn: dict[str, str] | None = None,
    _voice_playback_context: str = "",
) -> PreparedTurn | DeferredTurn:
    """Validate identities, prepare media, persist input and open its memory owner."""
    self = engine
    stages = LocalTimings()
    precommitted_memcore_turn = dict(_precommitted_memcore_turn) if isinstance(_precommitted_memcore_turn, dict) else {}
    externally_managed_memcore_turn = bool(precommitted_memcore_turn)
    precommitted_source_id = str(precommitted_memcore_turn.get("source_id") or "").strip()
    precommitted_turn_id = str(precommitted_memcore_turn.get("turn_id") or "").strip()
    precommitted_voice_turn_id = str(precommitted_memcore_turn.get("voice_turn_id") or "").strip()
    if externally_managed_memcore_turn and (
        not precommitted_source_id or not precommitted_turn_id or not precommitted_voice_turn_id
    ):
        raise ValueError("voice_precommitted_turn_invalid")
    client_context = self._resolve_client_protocol_context(payload)
    turn_character_pack_id = self._resolve_payload_character_pack_id(payload)
    from ..dialogue_targets import is_service_domain, registry_for, trusted_target

    dialogue_target = trusted_target(payload)
    if is_service_domain(turn_character_pack_id) and dialogue_target is None:
        raise ValueError("dialogue_target_requires_host_context")
    if dialogue_target is not None:
        registry_for(self).validate(dialogue_target)
    actor_stable_id, actor_display_name = self._resolve_turn_actor(payload)
    turn_domain_profile_id = self._resolve_turn_domain_profile(payload)
    payload = dict(payload)
    message_addressing = self._normalize_message_addressing(
        payload,
        fallback_mode="current_request",
    )
    forward_references = self._normalize_forward_references(payload)
    speculative_voice_candidate = bool(payload.get("voice_speculative_candidate"))
    payload.pop("finance_mode", None)
    payload.pop("prompt_scope", None)
    payload["domain_profile"] = turn_domain_profile_id
    turn_kind = str(payload.get("turn_kind") or "").strip().lower()
    prompt_scope = turn_kind if turn_kind in {"qq_attention", "qq_optional_reply"} else ""
    attention_reference_source_ids = [
        str(item or "").strip()
        for item in list(payload.pop("memory_attention_reference_source_ids", []) or [])
        if str(item or "").strip()
    ]
    if prompt_scope != "qq_attention":
        attention_reference_source_ids = []
    else:
        attention_reference_source_ids = list(dict.fromkeys(attention_reference_source_ids))
    payload.pop("plugin_stable_system_context", None)
    turn_control_id = str(payload.pop("_turn_control_id", "") or "").strip()
    plugin_external_event = self._pop_plugin_external_event(
        payload,
        event_allowed=turn_kind == "plugin_event",
    )
    turn_resource_manifest = self._resolve_turn_resource_manifest(payload, client_context)
    chat_model_override = str(payload.get("chat_model_override") or "").strip()
    trace_id = str(payload.get("trace_id") or f"{PERSONA.trace_prefix}_{uuid.uuid4().hex[:12]}")
    session_id = str(payload.get("user_id") or payload.get("session_id") or "default_session")
    profile_user_id = str(payload.get("real_user_id") or session_id)
    from ..owner_private_session import current_private_scope

    private_scope = current_private_scope(self)
    memory_channel = private_scope.channel if private_scope is not None else ""
    if private_scope and private_scope.shared and turn_kind == "desktop_pet_proactive":
        activity = private_scope.policy.activity(turn_character_pack_id)
        try:
            interval = max(5, min(3600, float(payload.get("proactive_interval_seconds") or 60)))
        except (ValueError, TypeError):
            interval = 60
        if time.time() - max(activity.values(), default=0) < interval:
            quiet = {
                "status": "ok",
                "speech": "",
                "speech_segments": [],
                "_deliberate_silence": True,
                "_private_activity_deferred": True,
            }
            return DeferredTurn(quiet)
    user_memory_source_id = self._pop_user_memory_source_id(
        payload,
        profile_user_id=profile_user_id,
        session_id=session_id,
        character_pack_id=turn_character_pack_id,
        memory_role=(f"event.{plugin_external_event['event_type']}" if plugin_external_event is not None else "user"),
    )
    user_message = str(payload.get("message") or "").strip()
    memory_user_message = str(payload.get("memory_message") or user_message).strip()
    if plugin_external_event is not None:
        memory_user_message = self._render_external_event_memory_content(plugin_external_event)
    now_ts = int(payload.get("timestamp") or time.time())
    payload = self._prepare_care_context_for_turn(
        payload,
        client_context,
        profile_user_id=profile_user_id,
        character_pack_id=turn_character_pack_id,
        now_ts=now_ts,
    )
    date_label = timestamp_to_date_label(now_ts)
    time_of_day = detect_time_of_day_from_text(user_message) or infer_time_of_day(now_ts)
    turn_extra_user_context = self._build_turn_extra_user_context(payload, client_context)
    stages.mark("identity_and_care_ms")
    if _voice_playback_context:
        turn_extra_user_context = self._merge_extra_user_context(turn_extra_user_context, _voice_playback_context)
    turn_extra_user_context, turn_user_images, turn_execution_target = prepare_turn_media(
        self,
        payload=payload,
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
        character_pack_id=turn_character_pack_id,
        chat_model_override=chat_model_override,
        extra_user_context=turn_extra_user_context,
    )
    stages.mark("media_preparation_ms")
    transient_user_turn = self._is_transient_user_turn(payload) or externally_managed_memcore_turn
    current_input_transient = transient_user_turn and not externally_managed_memcore_turn
    persist_assistant_turn = self._should_persist_assistant_turn(payload) and not externally_managed_memcore_turn

    identity = TurnIdentity(
        session_id=session_id,
        profile_user_id=profile_user_id,
        character_pack_id=turn_character_pack_id,
        domain_profile_id=turn_domain_profile_id,
        actor_stable_id=actor_stable_id,
        actor_display_name=actor_display_name,
        trace_id=trace_id,
    )
    request = TurnRequest(
        mode=mode,
        payload=payload,
        client_context=client_context,
        user_message=user_message,
        now_ts=now_ts,
        date_label=date_label,
        time_of_day=time_of_day,
        prompt_scope=prompt_scope,
        turn_control_id=turn_control_id,
        plugin_external_event=plugin_external_event,
    )
    persistence = TurnPersistence(
        transient_user_turn=transient_user_turn,
        current_input_transient=current_input_transient,
        externally_managed_memcore_turn=externally_managed_memcore_turn,
        persist_assistant_turn=persist_assistant_turn,
        speculative_voice_candidate=speculative_voice_candidate,
        private_scope=private_scope,
        memory_channel=memory_channel,
    )
    user_record = prepare_user_record(
        self,
        identity,
        request,
        persistence,
        memory_user_message=memory_user_message,
        user_memory_source_id=user_memory_source_id,
        message_addressing=message_addressing,
        forward_references=forward_references,
        precommitted_source_id=precommitted_source_id,
        precommitted_voice_turn_id=precommitted_voice_turn_id,
    )
    stages.mark("input_persistence_ms")
    target_actor_id, target_actor_display_name = self._apply_message_addressing(
        user_record,
        message_addressing,
    )
    if forward_references:
        user_record["forward_references"] = forward_references
    turn_projection_source_id = str(user_record.get("source_id") or "").strip()
    recent_raw, recent_episodic_summaries, recent_semantic_summaries = self._load_turn_visible_memory(
        session_id=session_id,
        profile_user_id=profile_user_id,
        character_pack_id=turn_character_pack_id,
        user_record=user_record,
        include_transient_user_record=transient_user_turn,
    )
    stages.mark("visible_memory_ms")
    final_debug_enabled = self._coerce_bool(payload.get("final_debug"))
    diagnostics = build_turn_diagnostics(user_message)
    confirmed_snippets: list[str] = []
    memory = open_turn_memory(
        self,
        identity,
        persistence,
        user_record=user_record,
        current_source_id=turn_projection_source_id,
        plugin_external_event=plugin_external_event,
        attention_reference_source_ids=attention_reference_source_ids,
        precommitted_turn_id=precommitted_turn_id,
        target_actor_id=target_actor_id,
        target_actor_display_name=target_actor_display_name,
    )
    stages.mark("memory_turn_open_ms")
    logger.info("turn_prepared scope=%s client=%s stages=%s",
        timing_scope(profile_user_id, session_id, turn_character_pack_id), mode, stages.snapshot())
    prompt_exclude_source_ids: list[str] = []
    request_projection_state = build_request_projection_state(identity, request, persistence, memory)
    return PreparedTurn(
        identity=identity,
        request=request,
        persistence=persistence,
        prompt=TurnPromptState(
            resource_manifest=turn_resource_manifest,
            chat_model_override=chat_model_override,
            extra_user_context=turn_extra_user_context,
            user_images=turn_user_images,
            execution_target=turn_execution_target,
            final_debug_enabled=final_debug_enabled,
            recent_raw=recent_raw,
            recent_episodic_summaries=recent_episodic_summaries,
            recent_semantic_summaries=recent_semantic_summaries,
            confirmed_snippets=confirmed_snippets,
            exclude_source_ids=prompt_exclude_source_ids,
            request_projection_state=request_projection_state,
        ),
        memory=memory,
        diagnostics=diagnostics,
    )
