"""Compatible final-response entry; coordinate explicit preparation stages."""

from __future__ import annotations

from typing import Any, Mapping
import logging

from ..local_timings import LocalTimings, timing_scope

logger = logging.getLogger("akane.local_timing")

from ..client_protocol import ClientProtocolContext
from ..resource_manifest import ResourceManifest
from .response_assembly import build_generation_context, finalize_context
from .response_budget import fit_context_budget
from .response_contracts import ResponseRequest
from .response_exposure import ResponseExposure
from .response_materials import prepare_request_materials, resolve_environment
from .response_materials import _declared_activity_context_lifecycle as _declared_activity_context_lifecycle
from .response_projection import prepare_projection

from .response_catalog import (
    _capability_catalog_source as _capability_catalog_source,
    _catalog_message_text as _catalog_message_text,
    _shape_capability_catalog_projection as _shape_capability_catalog_projection,
    _split_active_projection_turns as _split_active_projection_turns,
    _visible_projection_source_ids as _visible_projection_source_ids,
    _overlay_ephemeral_provider_evidence as _overlay_ephemeral_provider_evidence,
)
from .response_projection import (
    PROJECTION_READ_MIGRATION_REASONS as PROJECTION_READ_MIGRATION_REASONS,
    _safe_projection_failure_code as _safe_projection_failure_code,
    _compare_memcore_projection_shadow as _compare_memcore_projection_shadow,
    _projected_current_message_text as _projected_current_message_text,
    _build_memcore_provider_history as _build_memcore_provider_history,
    _projection_failure_context as _projection_failure_context,
    _memory_backend as _memory_backend,
)
from .response_budget import (
    _estimate_generation_context_tokens as _estimate_generation_context_tokens,
    _drop_oldest_prompt_lines as _drop_oldest_prompt_lines,
    _trim_oldest_provider_history_group as _trim_oldest_provider_history_group,
    _trim_oldest_prompt_raw_record as _trim_oldest_prompt_raw_record,
)
from .response_assembly import (
    COMPACT_READBACK_STABLE_HINT as COMPACT_READBACK_STABLE_HINT,
    build_compact_readback_hint as build_compact_readback_hint,
    with_compact_readback_hint as with_compact_readback_hint,
    with_current_actor_relation as with_current_actor_relation,
)


def prepare_context(
    engine: Any,
    *,
    session_id: str,
    user_message: str,
    recent_raw: list[dict[str, Any]],
    recent_episodic_summaries: list[dict[str, Any]],
    recent_semantic_summaries: list[dict[str, Any]],
    confirmed_snippets: list[str],
    now_ts: int,
    profile_user_id: str,
    current_visual_payload: Any = None,
    extra_user_context: str = "",
    stable_system_context: str = "",
    client_context: ClientProtocolContext | None = None,
    resource_manifest: ResourceManifest | None = None,
    character_pack_id: str = "",
    allow_tool_call: bool = True,
    final_debug_enabled: bool | None = None,
    enable_native_tools: bool = False,
    chat_model_override: str = "",
    execution_target: Any = None,
    post_user_turns: list[dict[str, Any]] | None = None,
    prompt_exclude_source_ids: list[str] | None = None,
    domain_profile_id: str = "",
    prompt_scope: str = "",
    current_user_source_id: str = "",
    current_input_transient: bool = False,
    current_actor_relation: str = "",
    authorization_profile_user_id: str = "",
    actor_stable_id: str = "",
    actor_profile_user_id: str = "",
    request_projection_state: Mapping[str, Any] | None = None,
    native_user_images: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # Legacy summary/projection arguments remain accepted for callers. The
    # current model surface comes from MemCore and the frozen tool exposure.
    request = ResponseRequest(
        session_id=session_id,
        user_message=user_message,
        recent_raw=recent_raw,
        confirmed_snippets=confirmed_snippets,
        now_ts=now_ts,
        profile_user_id=profile_user_id,
        current_visual_payload=current_visual_payload,
        extra_user_context=extra_user_context,
        stable_system_context=stable_system_context,
        client_context=client_context,
        resource_manifest=resource_manifest,
        character_pack_id=character_pack_id,
        allow_tool_call=allow_tool_call,
        final_debug_enabled=final_debug_enabled,
        enable_native_tools=enable_native_tools,
        chat_model_override=chat_model_override,
        execution_target=execution_target,
        post_user_turns=post_user_turns,
        prompt_exclude_source_ids=prompt_exclude_source_ids,
        domain_profile_id=domain_profile_id,
        prompt_scope=str(prompt_scope or "").strip().lower(),
        current_user_source_id=current_user_source_id,
        current_input_transient=current_input_transient,
        current_actor_relation=current_actor_relation,
        authorization_profile_user_id=authorization_profile_user_id,
        actor_stable_id=actor_stable_id,
        actor_profile_user_id=actor_profile_user_id,
        request_projection_state=(request_projection_state if isinstance(request_projection_state, dict) else None),
        native_user_images=native_user_images,
    )
    stages = LocalTimings()
    environment = resolve_environment(engine, request)
    stages.mark("environment_ms")
    projection = prepare_projection(engine, request)
    stages.mark("projection_read_ms")
    if isinstance(projection, dict):
        return projection
    exposure = ResponseExposure(engine, request, environment, projection)
    failure = exposure.prepare()
    stages.mark("tool_exposure_ms")
    if failure is not None:
        return failure
    materials = prepare_request_materials(engine, request, environment, projection, exposure)
    stages.mark("request_materials_ms")
    projection.prepare_turns(request)
    generation_context = build_generation_context(engine, request, environment, projection, materials, exposure)
    stages.mark("history_assembly_ms")
    generation_context = fit_context_budget(
        engine,
        request,
        environment,
        projection,
        materials,
        exposure,
        generation_context,
    )
    stages.mark("context_budget_ms")
    if "memcore_projection_failure" in generation_context:
        return generation_context
    result = finalize_context(engine, request, environment, projection, exposure, generation_context)
    stages.mark("finalize_ms")
    logger.info("request_context_prepared scope=%s stages=%s",
        timing_scope(profile_user_id, session_id, character_pack_id), stages.snapshot())
    return result
