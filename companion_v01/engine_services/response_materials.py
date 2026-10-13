"""Resolve client policies, persona, visual resources and context contributions."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import logging
from typing import Any

import config as mod_config
from ..client_protocol import ClientCapability, ClientMode
from ..domain_profiles import DomainProfileRegistry, build_domain_profile_prompt
from ..prompt_blocks import strip_care_prompt_contract
from ..prompt_context_lifecycle import PromptContextContribution, PromptContextLifecycle, materialize_prompt_contexts
from ..prompt_profiles import PromptModule, configure_qq_conversation_prompt
from .response_contracts import ExtraContextSections, ResponseEnvironment, ResponseMaterials, ResponseRequest
from .response_projection import ResponseProjection

logger = logging.getLogger("akane.response_builder")


@dataclass(frozen=True)
class _VisualInputs:
    attachment_service: Any
    current_visual_context_payload: Any
    persona_context: dict[str, Any]
    automatic_character_context: str
    visual_observation_sections: list[str]


@dataclass(frozen=True)
class _RequestPromptSnapshot:
    key: str
    materials: ResponseMaterials
    execution_context: str


def prepare_request_materials(
    engine: Any, request: ResponseRequest, environment: ResponseEnvironment,
    projection: ResponseProjection, exposure: Any, *, fallback: ResponseMaterials | None = None,
) -> ResponseMaterials:
    """Sample model-facing state once per task; keep authority and tool history live."""
    state = request.request_projection_state
    if not isinstance(state, dict):
        return fallback if fallback is not None else prepare_materials(engine, request, environment, projection)
    # This private, in-memory value belongs to PreparedTurn, never to the engine
    # or a durable projection. Only explicit context/contract boundaries refresh
    # it; time, care/playback state and cwd changes during ordinary tools do not.
    key = hashlib.sha256(json.dumps({
        "identity": (request.profile_user_id, request.session_id, request.character_pack_id,
                     request.current_user_source_id, request.user_message, request.now_ts,
                     request.current_input_transient, request.current_actor_relation,
                     request.authorization_profile_user_id, request.actor_stable_id, request.actor_profile_user_id),
        "client": environment.client_context.to_public_dict(),
        "domain": environment.domain_profile.id,
        "mode": (getattr(environment.prompt_profile, "id", ""), environment.care_enabled,
                 environment.debug_enabled, environment.prompt_profile.system_prompt_override,
                 environment.prompt_profile.mode_prompt_override(debug_enabled=environment.debug_enabled)),
        "generation": projection.provider_projection.get("compaction_generation"),
        "projection": (projection.projection_authoritative, bool(projection.projection_recovery_failure),
                       projection.provider_projection.get("provider_profile")),
        "model": request.chat_model_override,
        "control": (request.allow_tool_call, request.extra_user_context, request.stable_system_context),
        "contracts": (exposure.native_tools, exposure.published_plugin_blocks,
                      getattr(exposure, "execution_stable_context", ""), exposure.tool_prompt_context),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    snapshot = state.get("_response_prompt_snapshot")
    if isinstance(snapshot, _RequestPromptSnapshot) and snapshot.key == key:
        exposure.execution_context = snapshot.execution_context
        return deepcopy(snapshot.materials)
    materials = prepare_materials(engine, request, environment, projection)
    state["_response_prompt_snapshot"] = _RequestPromptSnapshot(
        key=key, materials=deepcopy(materials), execution_context=exposure.execution_context,
    )
    return materials


def resolve_environment(engine: Any, request: ResponseRequest) -> ResponseEnvironment:
    resource_manifest = request.resource_manifest
    client_context = request.client_context or engine._resolve_client_protocol_context({})
    care_status_getter = getattr(engine, "care_feature_status", None)
    care_status = care_status_getter() if callable(care_status_getter) else {"enabled": True}
    care_enabled = bool(care_status.get("enabled", True))
    care_context_resolver = getattr(engine, "care_enabled_for_context", None)
    if care_enabled and callable(care_context_resolver):
        care_enabled = bool(
            care_context_resolver(
                character_pack_id=request.character_pack_id,
                client_context=client_context,
            )
        )
    prompt_builder = engine._get_prompt_builder()
    prompt_profile = engine._get_prompt_profile_registry().resolve(client_context, care_enabled=care_enabled)
    prompt_profile = configure_qq_conversation_prompt(
        prompt_profile, session_id=request.session_id,
        current_actor_relation=request.current_actor_relation,
    )
    from ..speech_policy import configure_speech_prompt

    prompt_profile = configure_speech_prompt(engine, request.character_pack_id, client_context, prompt_profile)
    domain_profile = DomainProfileRegistry().get(
        request.domain_profile_id if prompt_profile.includes(PromptModule.DOMAIN_PROFILE) else ""
    )
    domain_profile_context = build_domain_profile_prompt(domain_profile)
    tool_capability_available = bool(
        prompt_profile.includes(PromptModule.TOOLS) and client_context.has_capability(ClientCapability.TOOL_ACTIONS)
    )
    effective_allow_tool_call = bool(request.allow_tool_call and tool_capability_available)
    requested_debug_enabled = bool(
        getattr(mod_config, "FINAL_DEBUG", False)
        if request.final_debug_enabled is None
        else request.final_debug_enabled
    )
    debug_enabled = bool(requested_debug_enabled and prompt_profile.supports_thought_debug)
    if client_context.effective_mode != ClientMode.QQ_TEXT:
        resource_manifest = resource_manifest or engine.resource_manifest
    manifest = resource_manifest.refresh() if resource_manifest else None
    runtime_projection = engine._get_user_runtime_projection(request.profile_user_id)
    user_bgm_tracks = list(runtime_projection.get("extra_bgm_tracks") or [])
    user_scene_groups = list(runtime_projection.get("extra_scene_groups") or [])
    user_character_outfits = list(runtime_projection.get("extra_character_outfits") or [])
    desktop_pet_character_only = client_context.effective_mode == ClientMode.DESKTOP_PET
    character_pack_persona_enabled = client_context.effective_mode in {ClientMode.DESKTOP_PET, ClientMode.QQ_TEXT}
    return ResponseEnvironment(
        client_context=client_context,
        care_enabled=care_enabled,
        prompt_builder=prompt_builder,
        prompt_profile=prompt_profile,
        domain_profile=domain_profile,
        domain_profile_context=domain_profile_context,
        tool_capability_available=tool_capability_available,
        effective_allow_tool_call=effective_allow_tool_call,
        debug_enabled=debug_enabled,
        resource_manifest=resource_manifest,
        manifest=manifest,
        runtime_projection=runtime_projection,
        user_bgm_tracks=user_bgm_tracks,
        user_scene_groups=user_scene_groups,
        user_character_outfits=user_character_outfits,
        desktop_pet_character_only=desktop_pet_character_only,
        character_pack_persona_enabled=character_pack_persona_enabled,
    )


def prepare_materials(
    engine: Any,
    request: ResponseRequest,
    environment: ResponseEnvironment,
    projection: ResponseProjection,
) -> ResponseMaterials:
    memory_text = "\n\n".join(request.confirmed_snippets) if request.confirmed_snippets else ""
    visual = _prepare_visual_inputs(engine, request, environment)
    extra = _prepare_extra_context(engine, request, environment, projection, visual)
    resource_context, current_visual_context, visual_defaults = _prepare_visual_resources(
        engine,
        request,
        environment,
        visual,
    )
    mode_prompt_override = environment.prompt_profile.mode_prompt_override(debug_enabled=environment.debug_enabled)
    if not environment.care_enabled and not mode_prompt_override:
        mode_prompt_override = strip_care_prompt_contract(
            environment.prompt_builder.persona.final_debug_mode_prompt
            if environment.debug_enabled
            else environment.prompt_builder.persona.final_fast_mode_prompt
        )
    if not environment.care_enabled:
        mode_prompt_override = strip_care_prompt_contract(mode_prompt_override)
    system_prompt_override = environment.prompt_profile.system_prompt_override
    if not environment.care_enabled:
        system_prompt_override = strip_care_prompt_contract(system_prompt_override)
    return ResponseMaterials(
        memory_text=memory_text,
        persona_context=visual.persona_context,
        current_visual_context=current_visual_context,
        resource_context=resource_context,
        visual_defaults=visual_defaults,
        extra=extra,
        mode_prompt_override=mode_prompt_override,
        system_prompt_override=system_prompt_override,
    )


def _prepare_visual_inputs(engine: Any, request: ResponseRequest, environment: ResponseEnvironment) -> _VisualInputs:
    attachment_service = engine._get_attachment_inbox_service()
    current_visual_context_payload = engine._resolve_current_visual_payload(
        session_id=request.session_id,
        current_visual_payload=request.current_visual_payload,
    )
    current_character = (
        current_visual_context_payload.get("character")
        if isinstance(current_visual_context_payload, dict)
        and isinstance(current_visual_context_payload.get("character"), dict)
        else {}
    )
    current_character_outfit = str(current_character.get("outfit") or "").strip()
    scene_observation_context = (
        engine.vision_service.build_scene_prompt_context(
            visual_payload=current_visual_context_payload,
            extra_bgm_tracks=environment.user_bgm_tracks,
            extra_scene_groups=environment.user_scene_groups,
            extra_character_outfits=environment.user_character_outfits,
        )
        if engine.vision_service is not None
        and environment.prompt_profile.includes(PromptModule.SCENE_OBSERVATION)
        and not environment.desktop_pet_character_only
        else ""
    )
    outfit_observation_context = (
        engine.vision_service.build_outfit_prompt_context(
            visual_payload=current_visual_context_payload,
            extra_bgm_tracks=environment.user_bgm_tracks,
            extra_scene_groups=environment.user_scene_groups,
            extra_character_outfits=environment.user_character_outfits,
        )
        if engine.vision_service is not None
        and environment.prompt_profile.includes(PromptModule.OUTFIT_OBSERVATION)
        and not environment.desktop_pet_character_only
        else ""
    )
    character_pack_persona_context = (
        engine._build_desktop_pet_character_pack_prompt_context(
            character_pack_id=request.character_pack_id,
            resource_manifest=environment.resource_manifest,
            client_mode=environment.client_context.effective_mode.value,
            preferred_outfit=current_character_outfit,
            extra_character_outfits=environment.user_character_outfits,
        )
        if environment.character_pack_persona_enabled and environment.prompt_profile.includes(PromptModule.PERSONA)
        else {"system_context": "", "reference_context": "", "active_id": ""}
    )
    automatic_character_context = ""
    if environment.character_pack_persona_enabled and request.character_pack_id:
        context_library_service = getattr(
            getattr(engine, "desktop_pet_character_resources", None),
            "context_libraries",
            None,
        )
        automatic_context_builder = getattr(
            context_library_service,
            "build_automatic_context",
            None,
        )
        if automatic_context_builder is not None:
            try:
                automatic_character_context = str(
                    automatic_context_builder(request.character_pack_id, request.user_message) or ""
                ).strip()
            except Exception as exc:
                logger.warning("automatic character context loading failed: %s", exc)
                automatic_character_context = ""
    persona_context = character_pack_persona_context
    visual_observation_sections = [
        text
        for text in [
            scene_observation_context,
            outfit_observation_context,
        ]
        if text
    ]
    return _VisualInputs(
        attachment_service=attachment_service,
        current_visual_context_payload=current_visual_context_payload,
        persona_context=persona_context,
        automatic_character_context=automatic_character_context,
        visual_observation_sections=visual_observation_sections,
    )


def _prepare_extra_context(
    engine: Any,
    request: ResponseRequest,
    environment: ResponseEnvironment,
    projection: ResponseProjection,
    visual: _VisualInputs,
) -> ExtraContextSections:
    extra_context = str(request.extra_user_context or "").strip()
    # The last flag is a placement contract, not a Bot-specific exception:
    # per-turn transport/event material changes on every request and must stay
    # after append-only history, while durable runtime context can precede it.
    extra_context_contributions = [
        PromptContextContribution(
            name="relationship",
            content=engine._build_memory_relationship_context(
                profile_user_id=request.profile_user_id,
                character_pack_id=request.character_pack_id,
                now_ts=request.now_ts,
            ),
            lifecycle=PromptContextLifecycle.STABLE,
        ),
        PromptContextContribution(
            name="attachment_focus",
            content=lambda: visual.attachment_service.build_activity_prompt_context(
                profile_user_id=request.profile_user_id,
                session_id=request.session_id,
            ),
            lifecycle=_declared_activity_context_lifecycle(visual.attachment_service),
            enabled=bool(
                visual.attachment_service is not None
                and environment.prompt_profile.includes(PromptModule.EXTRA_CONTEXT)
                and environment.client_context.effective_mode in {ClientMode.QQ_TEXT, ClientMode.DESKTOP_PET}
            ),
        ),
        PromptContextContribution(
            name="plugin_observations",
            content=lambda: engine.plugin_observations_provider(
                profile_user_id=request.profile_user_id,
                session_id=request.session_id,
                character_pack_id=request.character_pack_id,
            ),
            enabled=callable(getattr(engine, "plugin_observations_provider", None))
            and environment.prompt_profile.includes(PromptModule.EXTRA_CONTEXT),
        ),
        PromptContextContribution(name="character_automatic_context", content=visual.automatic_character_context),
        # Active outfit/emotion ids change far less often than conversation
        # history. Keep them in their own stable pre-history block: ordinary
        # turns reuse them, while a real outfit/resource change invalidates the
        # prefix once without hiding any current resource from the model.
        PromptContextContribution(
            name="character_resources",
            content=str(visual.persona_context.get("resource_context") or "").strip(),
            lifecycle=PromptContextLifecycle.STABLE,
        ),
        PromptContextContribution(
            name="turn_extra_context",
            content=extra_context if environment.prompt_profile.includes(PromptModule.EXTRA_CONTEXT) else "",
        ),
    ]
    materialized_contexts = materialize_prompt_contexts(
        extra_context_contributions,
        event_timeline_authoritative=projection.event_timeline_authoritative,
    )
    extra_context_audit_sections = engine._build_extra_context_audit_sections(
        [(name, text) for name, text, _volatile in materialized_contexts.sections]
    )
    volatile_names = {name for name, _text, volatile in materialized_contexts.sections if volatile}
    stable_extra_context_sections = [
        section["text"]
        for section in extra_context_audit_sections
        if str(section.get("name") or "") not in volatile_names
    ]
    volatile_extra_context_sections = [
        section["text"] for section in extra_context_audit_sections if str(section.get("name") or "") in volatile_names
    ]
    merged_extra_context = (
        "\n\n".join(stable_extra_context_sections) if stable_extra_context_sections else "(无额外上下文)"
    )
    merged_volatile_extra_context = "\n\n".join(volatile_extra_context_sections)
    return ExtraContextSections(
        materialized=materialized_contexts,
        audit_sections=extra_context_audit_sections,
        stable=merged_extra_context,
        volatile=merged_volatile_extra_context,
    )


def _prepare_visual_resources(
    engine: Any,
    request: ResponseRequest,
    environment: ResponseEnvironment,
    visual: _VisualInputs,
) -> tuple[str, str, dict[str, Any]]:
    visual_defaults = (
        environment.resource_manifest.build_runtime_manifest(
            extra_bgm_tracks=environment.user_bgm_tracks,
            extra_scene_groups=environment.user_scene_groups,
            extra_character_outfits=environment.user_character_outfits,
        )["defaults"]
        if environment.manifest
        else {
            "major": "default",
            "minor": "default",
            "background": "evening_classroom",
            "bgm": "",
            "outfit": "default",
            "emotion": "normal",
        }
    )
    if environment.resource_manifest and visual.current_visual_context_payload:
        try:
            current_visual_defaults = environment.resource_manifest.normalize_visual_output(
                json.loads(json.dumps(visual.current_visual_context_payload)),
                extra_bgm_tracks=environment.user_bgm_tracks,
                extra_scene_groups=environment.user_scene_groups,
                extra_character_outfits=environment.user_character_outfits,
            )
            visual_defaults = dict(visual_defaults)
            if environment.desktop_pet_character_only:
                visual_defaults["outfit"] = str(
                    current_visual_defaults.get("character", {}).get("outfit") or visual_defaults["outfit"]
                )
                visual_defaults["emotion"] = str(current_visual_defaults.get("emotion") or visual_defaults["emotion"])
            else:
                current_scene = (
                    current_visual_defaults.get("scene") if isinstance(current_visual_defaults, dict) else {}
                )
                current_character = (
                    current_visual_defaults.get("character") if isinstance(current_visual_defaults, dict) else {}
                )
                if isinstance(current_scene, dict):
                    visual_defaults["major"] = str(current_scene.get("major") or visual_defaults["major"])
                    visual_defaults["minor"] = str(current_scene.get("minor") or visual_defaults["minor"])
                    visual_defaults["background"] = str(
                        current_scene.get("background") or visual_defaults["background"]
                    )
                    visual_defaults["bgm"] = str(current_scene.get("bgm") or visual_defaults["bgm"])
                if isinstance(current_character, dict):
                    visual_defaults["outfit"] = str(current_character.get("outfit") or visual_defaults["outfit"])
                visual_defaults["emotion"] = str(current_visual_defaults.get("emotion") or visual_defaults["emotion"])
        except Exception as exc:
            logger.warning("current visual defaults failed: %s", exc)
    if environment.client_context.effective_mode == ClientMode.QQ_TEXT:
        resource_context = "" if environment.resource_manifest else "当前角色包没有可用的表情图片清单。"
    else:
        resource_context = (
            (
                environment.resource_manifest.build_character_catalog_prompt_context(
                    extra_character_outfits=environment.user_character_outfits,
                )
                if environment.desktop_pet_character_only
                else environment.resource_manifest.build_prompt_context(
                    extra_bgm_tracks=environment.user_bgm_tracks,
                    extra_scene_groups=environment.user_scene_groups,
                    extra_character_outfits=environment.user_character_outfits,
                )
            )
            if environment.resource_manifest and environment.prompt_profile.includes(PromptModule.RESOURCE_MANIFEST)
            else "当前没有额外的视觉资源。"
        )
    current_visual_context = (
        engine._build_current_visual_context(
            profile_user_id=request.profile_user_id,
            session_id=request.session_id,
            current_visual_payload=request.current_visual_payload,
            visual_payload=visual.current_visual_context_payload,
            runtime_projection=environment.runtime_projection,
            character_only=environment.desktop_pet_character_only,
            resource_manifest=environment.resource_manifest,
        )
        if environment.prompt_profile.includes(PromptModule.CURRENT_VISUAL_STATE)
        else ""
    )
    if visual.visual_observation_sections:
        current_visual_context = "\n\n".join(
            part for part in [current_visual_context, *visual.visual_observation_sections] if part
        )
    return resource_context, current_visual_context, visual_defaults


def _declared_activity_context_lifecycle(service: Any) -> PromptContextLifecycle:
    """Read a producer lifecycle declaration without feature-specific rules."""

    resolver = getattr(service, "activity_prompt_context_lifecycle", None)
    if not callable(resolver):
        return PromptContextLifecycle.TURN
    try:
        return PromptContextLifecycle.coerce(resolver())
    except Exception as exc:
        logger.warning("prompt context lifecycle declaration failed: %s", exc)
        return PromptContextLifecycle.TURN
