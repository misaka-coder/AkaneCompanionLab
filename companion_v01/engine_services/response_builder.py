"""Final response context builder extracted from engine.py."""

from __future__ import annotations
import json
import logging
import re
from typing import Any

from ..client_protocol import ClientCapability, ClientMode, ClientProtocolContext
import config as mod_config
from ..domain_profiles import DomainProfileRegistry, build_domain_profile_prompt
from ..memory_rendering import render_semantic_summary_timeline, render_summary_timeline
from ..prompt_blocks import strip_care_prompt_contract
from ..prompt_profiles import PromptModule
from ..resource_manifest import ResourceManifest
from ..text_utils import render_chat_timeline

logger = logging.getLogger("akane.response_builder")


QQ_GENERATED_FILE_CONTEXT_ACTION_MARKERS = (
    "结果",
    "成果",
    "产物",
    "生成",
    "导出",
    "保存",
    "打包",
    "压缩",
    "下载",
    "发我",
    "发给我",
    "给我发",
    "传给我",
    "交付",
    "转换",
    "转成",
    "分离",
    "提取",
    "修改",
    "处理",
)
QQ_GENERATED_FILE_CONTEXT_HANDLE_RE = re.compile(
    r"\bgen_\d+\b|\bfile_\d+\b|\bimg_\d+\b|\baudio_\d+\b|\bvideo_\d+\b|"
    r"\.(?:mp3|wav|flac|m4a|aac|ogg|opus|mp4|mov|mkv|pdf|docx|xlsx|pptx|zip|rar|7z)\b",
    re.IGNORECASE,
)


def _should_include_generated_file_context(client_context: ClientProtocolContext, user_message: str) -> bool:
    if client_context.effective_mode != ClientMode.QQ_TEXT:
        return True
    normalized = str(user_message or "").strip().lower()
    if not normalized:
        return False
    return bool(QQ_GENERATED_FILE_CONTEXT_HANDLE_RE.search(normalized)) or any(
        marker in normalized for marker in QQ_GENERATED_FILE_CONTEXT_ACTION_MARKERS
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
    client_context: ClientProtocolContext | None = None,
    resource_manifest: ResourceManifest | None = None,
    character_pack_id: str = "",
    allow_tool_call: bool = True,
    final_debug_enabled: bool | None = None,
    enable_native_tools: bool = False,
    chat_model_override: str = "",
    post_user_turns: list[dict[str, Any]] | None = None,
    prompt_exclude_source_ids: list[str] | None = None,
    domain_profile_id: str = "",
    prompt_scope: str = "",
) -> dict[str, Any]:
    del prompt_scope
    client_context = client_context or engine._resolve_client_protocol_context({})
    care_status_getter = getattr(engine, "care_feature_status", None)
    care_status = care_status_getter() if callable(care_status_getter) else {"enabled": True}
    care_enabled = bool(care_status.get("enabled", True))
    prompt_builder = engine._get_prompt_builder()
    prompt_profile = engine._get_prompt_profile_registry().resolve(client_context, care_enabled=care_enabled)
    domain_profile = DomainProfileRegistry().get(
        domain_profile_id if prompt_profile.includes(PromptModule.DOMAIN_PROFILE) else ""
    )
    domain_profile_context = build_domain_profile_prompt(domain_profile)
    effective_allow_tool_call = bool(
        allow_tool_call
        and prompt_profile.includes(PromptModule.TOOLS)
        and client_context.has_capability(ClientCapability.TOOL_ACTIONS)
    )
    requested_debug_enabled = bool(
        getattr(mod_config, "FINAL_DEBUG", False) if final_debug_enabled is None else final_debug_enabled
    )
    debug_enabled = bool(requested_debug_enabled and prompt_profile.supports_thought_debug)
    if client_context.effective_mode != ClientMode.QQ_TEXT:
        resource_manifest = resource_manifest or engine.resource_manifest
    manifest = resource_manifest.refresh() if resource_manifest else None
    runtime_projection = engine._get_user_runtime_projection(profile_user_id)
    user_bgm_tracks = list(runtime_projection.get("extra_bgm_tracks") or [])
    user_scene_groups = list(runtime_projection.get("extra_scene_groups") or [])
    user_character_outfits = list(runtime_projection.get("extra_character_outfits") or [])
    desktop_pet_character_only = client_context.effective_mode == ClientMode.DESKTOP_PET
    character_pack_persona_enabled = client_context.effective_mode in {ClientMode.DESKTOP_PET, ClientMode.QQ_TEXT}
    excluded_prompt_sources = {
        str(source_id or "").strip()
        for source_id in list(prompt_exclude_source_ids or [])
        if str(source_id or "").strip()
    }
    visible_recent_raw = [
        record for record in recent_raw if str(record.get("source_id") or "").strip() not in excluded_prompt_sources
    ]
    _history_records, current_record = engine._split_history_records(
        recent_raw=visible_recent_raw,
        user_message=user_message,
        now_ts=now_ts,
    )
    current_message_text = engine._render_current_message_line(
        current_user_record=current_record,
    )
    memcore_prompt_context = _build_memcore_prompt_context(
        engine,
        profile_user_id=profile_user_id,
        session_id=session_id,
        character_pack_id=character_pack_id,
        current_user_record=current_record,
        now_ts=now_ts,
        exclude_source_ids=list(excluded_prompt_sources),
    )
    if memcore_prompt_context is not None:
        raw_text = str(memcore_prompt_context.get("raw_text") or "")
        episodic_summary_text = str(memcore_prompt_context.get("episodic_text") or "")
        semantic_summary_text = str(memcore_prompt_context.get("semantic_text") or "")
    else:
        raw_text = render_chat_timeline(visible_recent_raw)
        episodic_summary_text = render_summary_timeline(
            recent_episodic_summaries,
            store=engine.store,
        )
        semantic_summary_text = render_semantic_summary_timeline(
            recent_semantic_summaries,
            store=engine.store,
        )
    memory_text = "\n\n".join(confirmed_snippets) if confirmed_snippets else ""
    extra_context = str(extra_user_context or "").strip()
    attachment_service = engine._get_attachment_inbox_service()
    attachment_focus_context = (
        attachment_service.build_prompt_context(
            profile_user_id=profile_user_id,
            session_id=session_id,
        )
        if (
            attachment_service is not None
            and prompt_profile.includes(PromptModule.EXTRA_CONTEXT)
            and client_context.effective_mode in {ClientMode.QQ_TEXT, ClientMode.DESKTOP_PET}
        )
        else ""
    )
    generated_file_service = engine._get_generated_file_service()
    include_generated_file_context = _should_include_generated_file_context(client_context, user_message)
    generated_file_context = (
        generated_file_service.build_prompt_context(
            profile_user_id=profile_user_id,
            session_id=session_id,
            limit=8,
        )
        if (
            generated_file_service is not None
            and prompt_profile.includes(PromptModule.EXTRA_CONTEXT)
            and client_context.effective_mode in {ClientMode.QQ_TEXT, ClientMode.DESKTOP_PET}
            and include_generated_file_context
        )
        else ""
    )
    workspace_file_service = engine._get_workspace_file_service()
    workspace_file_context = (
        workspace_file_service.build_prompt_context(
            profile_user_id=profile_user_id,
            session_id=session_id,
        )
        if (
            workspace_file_service is not None
            and prompt_profile.includes(PromptModule.EXTRA_CONTEXT)
            and client_context.effective_mode == ClientMode.DESKTOP_PET
        )
        else ""
    )
    task_workspace_service = engine._get_task_workspace_service()
    task_workspace_context = (
        task_workspace_service.build_prompt_context(
            profile_user_id=profile_user_id,
            session_id=session_id,
            now_ts=now_ts,
        )
        if (
            task_workspace_service is not None
            and prompt_profile.includes(PromptModule.EXTRA_CONTEXT)
        )
        else ""
    )
    pending_gift_context = (
        engine.gift_service.build_pending_prompt_context(
            profile_user_id=profile_user_id,
            session_id=session_id,
            limit=3,
        )
        if prompt_profile.includes(PromptModule.PENDING_GIFTS)
        else ""
    )
    current_visual_context_payload = engine._resolve_current_visual_payload(
        session_id=session_id,
        current_visual_payload=current_visual_payload,
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
            extra_bgm_tracks=user_bgm_tracks,
            extra_scene_groups=user_scene_groups,
            extra_character_outfits=user_character_outfits,
        )
        if engine.vision_service is not None
        and prompt_profile.includes(PromptModule.SCENE_OBSERVATION)
        and not desktop_pet_character_only
        else ""
    )
    outfit_observation_context = (
        engine.vision_service.build_outfit_prompt_context(
            visual_payload=current_visual_context_payload,
            extra_bgm_tracks=user_bgm_tracks,
            extra_scene_groups=user_scene_groups,
            extra_character_outfits=user_character_outfits,
        )
        if engine.vision_service is not None
        and prompt_profile.includes(PromptModule.OUTFIT_OBSERVATION)
        and not desktop_pet_character_only
        else ""
    )
    focused_gift = (
        engine.gift_service.resolve_focus_asset(
            profile_user_id=profile_user_id,
            session_id=session_id,
            asset_id="",
        )
        if prompt_profile.includes(PromptModule.FOCUSED_GIFT_OBSERVATION)
        else None
    )
    gift_observation_context = (
        engine.vision_service.build_gift_prompt_context(asset=focused_gift)
        if engine.vision_service is not None and focused_gift is not None
        else ""
    )
    persona_service = engine._get_persona_card_service()
    profile_persona_enabled = not (character_pack_persona_enabled and bool(character_pack_id))
    persona_context = (
        persona_service.build_prompt_context(
            profile_user_id=profile_user_id,
            session_id=session_id,
            visible_limit=5,
        )
        if (profile_persona_enabled and persona_service is not None and prompt_profile.includes(PromptModule.PERSONA))
        else {"system_context": "", "reference_context": "", "active_id": ""}
    )
    character_pack_persona_context = (
        engine._build_desktop_pet_character_pack_prompt_context(
            character_pack_id=character_pack_id,
            resource_manifest=resource_manifest,
            client_mode=client_context.effective_mode.value,
            preferred_outfit=current_character_outfit,
        )
        if character_pack_persona_enabled and prompt_profile.includes(PromptModule.PERSONA)
        else {"system_context": "", "reference_context": "", "active_id": ""}
    )
    if character_pack_persona_enabled and character_pack_id:
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
                automatic_context = str(automatic_context_builder(character_pack_id, user_message) or "").strip()
            except Exception as exc:
                logger.warning("automatic character context loading failed: %s", exc)
                automatic_context = ""
            if automatic_context:
                character_pack_persona_context = dict(character_pack_persona_context)
                existing_reference = str(character_pack_persona_context.get("reference_context") or "").strip()
                character_pack_persona_context["reference_context"] = "\n\n".join(
                    part for part in [existing_reference, automatic_context] if part
                )
    persona_context = engine._merge_prompt_persona_contexts(
        character_pack_persona_context,
        persona_context,
    )
    visual_observation_sections = [
        text
        for text in [
            scene_observation_context,
            outfit_observation_context,
        ]
        if text
    ]
    extra_context_candidates = [
        (
            "client_mode",
            engine._build_client_mode_prompt_context(client_context)
            if prompt_profile.includes(PromptModule.CLIENT_MODE)
            else "",
        ),
        (
            "relationship",
            engine._build_memory_relationship_context(
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                now_ts=now_ts,
            ),
        ),
        ("task_workspace", task_workspace_context),
        ("workspace_files", workspace_file_context),
        ("attachment_focus", attachment_focus_context),
        ("generated_files", generated_file_context),
        ("pending_gifts", pending_gift_context),
        ("gift_observation", gift_observation_context),
        (
            "turn_extra_context",
            extra_context if prompt_profile.includes(PromptModule.EXTRA_CONTEXT) else "",
        ),
    ]
    extra_context_audit_sections = engine._build_extra_context_audit_sections(extra_context_candidates)
    extra_context_sections = [section["text"] for section in extra_context_audit_sections]
    merged_extra_context = "\n\n".join(extra_context_sections) if extra_context_sections else "(无额外上下文)"
    visual_defaults = (
        resource_manifest.build_runtime_manifest(
            extra_bgm_tracks=user_bgm_tracks,
            extra_scene_groups=user_scene_groups,
            extra_character_outfits=user_character_outfits,
        )["defaults"]
        if manifest
        else {
            "major": "default",
            "minor": "default",
            "background": "evening_classroom",
            "bgm": "",
            "outfit": "default",
            "emotion": "normal",
        }
    )
    if resource_manifest and current_visual_context_payload:
        try:
            current_visual_defaults = resource_manifest.normalize_visual_output(
                json.loads(json.dumps(current_visual_context_payload)),
                extra_bgm_tracks=user_bgm_tracks,
                extra_scene_groups=user_scene_groups,
                extra_character_outfits=user_character_outfits,
            )
            visual_defaults = dict(visual_defaults)
            if desktop_pet_character_only:
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
    if client_context.effective_mode == ClientMode.QQ_TEXT:
        resource_context = (
            "QQ 端不渲染立绘；emotion 的可选值已由当前角色包表情图片清单约束。"
            if resource_manifest
            else "QQ 端不渲染立绘，当前角色包没有可用的表情图片清单。"
        )
    else:
        resource_context = (
            (
                resource_manifest.build_character_prompt_context(
                    extra_character_outfits=user_character_outfits,
                    preferred_outfit=str(visual_defaults.get("outfit") or current_character_outfit),
                )
                if desktop_pet_character_only
                else resource_manifest.build_prompt_context(
                    extra_bgm_tracks=user_bgm_tracks,
                    extra_scene_groups=user_scene_groups,
                    extra_character_outfits=user_character_outfits,
                )
            )
            if resource_manifest and prompt_profile.includes(PromptModule.RESOURCE_MANIFEST)
            else "当前没有额外的视觉资源。"
        )
    current_visual_context = (
        engine._build_current_visual_context(
            profile_user_id=profile_user_id,
            session_id=session_id,
            current_visual_payload=current_visual_payload,
            visual_payload=current_visual_context_payload,
            runtime_projection=runtime_projection,
            character_only=desktop_pet_character_only,
            resource_manifest=resource_manifest,
        )
        if prompt_profile.includes(PromptModule.CURRENT_VISUAL_STATE)
        else "(当前客户端模式不需要完整演出状态。)"
    )
    if visual_observation_sections:
        current_visual_context = "\n\n".join([current_visual_context, *visual_observation_sections])
    mode_prompt_override = prompt_profile.mode_prompt_override(debug_enabled=debug_enabled)
    if not care_enabled and not mode_prompt_override:
        mode_prompt_override = strip_care_prompt_contract(
            prompt_builder.persona.final_debug_mode_prompt
            if debug_enabled
            else prompt_builder.persona.final_fast_mode_prompt
        )
    if not care_enabled:
        mode_prompt_override = strip_care_prompt_contract(mode_prompt_override)
    if resource_manifest and client_context.effective_mode in {
        ClientMode.DESKTOP_PET,
        ClientMode.QQ_TEXT,
    }:
        default_emotion_json = json.dumps(
            str(visual_defaults.get("emotion") or "normal"),
            ensure_ascii=False,
        )
        mode_prompt_override = mode_prompt_override.replace(
            '"emotion":"normal"',
            f'"emotion":{default_emotion_json}',
        )
    native_tools: list[dict[str, Any]] = []
    native_legacy_exclusions: set[str] = set()
    if enable_native_tools:
        native_capability_selection = engine._resolve_capability_selection(
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile.id,
        )
        from .. import tool_orchestration_engine as _toe

        try:
            provider_supports_native_tools = engine.llm.chat_supports_native_tools(
                chat_model_override=chat_model_override
            )
        except TypeError:
            provider_supports_native_tools = engine.llm.chat_supports_native_tools()
        native_plan = _toe.build_native_tool_decision_plan(
            engine._resolve_tool_handlers(
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
                domain_profile_id=domain_profile.id,
            ),
            allow_tool_call=effective_allow_tool_call,
            provider_supports_native_tools=provider_supports_native_tools,
            allowed_tool_names=native_capability_selection.tool_names,
        )
        if native_plan.enabled:
            native_tools = native_plan.tools
            native_legacy_exclusions = native_plan.legacy_prompt_exclusions
        elif native_plan.status == "unsupported":
            engine.llm.record_metric("native_tool_provider_unsupported")
    tool_prompt_context = engine._build_tool_prompt_context(
        allow_tool_call=effective_allow_tool_call,
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
        exclude_tool_types=native_legacy_exclusions,
        domain_profile_id=domain_profile.id,
    )
    if native_tools:
        tool_prompt_context = "\n\n".join(
            part
            for part in [
                str(tool_prompt_context or "").strip(),
                engine._build_native_tool_round_instruction(native_tools),
            ]
            if part
        )
    system_prompt_override = prompt_profile.system_prompt_override
    if not care_enabled and not system_prompt_override:
        system_prompt_override = prompt_builder.persona.final_system_prompt
    if not care_enabled:
        system_prompt_override = strip_care_prompt_contract(system_prompt_override)
    generation_context = prompt_builder.build_final_generation_context(
        now_ts=now_ts,
        raw_text=raw_text,
        current_message_text=current_message_text,
        episodic_summary_text=episodic_summary_text,
        semantic_summary_text=semantic_summary_text,
        memory_text=memory_text,
        current_visual_context=current_visual_context,
        resource_context=resource_context,
        extra_context=merged_extra_context,
        extra_context_audit_sections=extra_context_audit_sections,
        persona_system_context=str(persona_context.get("system_context") or ""),
        persona_reference_context=str(persona_context.get("reference_context") or ""),
        persona_active_id=str(persona_context.get("active_id") or ""),
        domain_profile_context=domain_profile_context,
        visual_defaults=visual_defaults,
        allow_tool_call=effective_allow_tool_call,
        tool_prompt_context=tool_prompt_context,
        debug_enabled=debug_enabled,
        system_prompt_override=system_prompt_override,
        mode_prompt_override=mode_prompt_override,
    )
    if not care_enabled:
        fallback_payload = generation_context.get("fallback")
        if isinstance(fallback_payload, dict):
            fallback_payload.pop("state_request", None)
    if desktop_pet_character_only and client_context.has_capability(ClientCapability.AUDIO_PLAYBACK):
        fallback_payload = generation_context.get("fallback")
        if isinstance(fallback_payload, dict):
            fallback_payload["activity"] = None
    generation_context["allow_tool_call"] = effective_allow_tool_call
    generation_context["native_tools"] = native_tools
    generation_context["native_tool_choice"] = "auto" if native_tools else ""
    generation_context["post_user_turns"] = list(post_user_turns) if post_user_turns else []
    generation_context["prompt_profile"] = prompt_profile.to_public_dict()
    generation_context["domain_profile"] = domain_profile.to_public_dict()
    if client_context.effective_mode == ClientMode.QQ_TEXT:
        fallback_payload = generation_context.get("fallback")
        if isinstance(fallback_payload, dict):
            fallback_payload.pop("character", None)
            fallback_payload.pop("scene", None)
            fallback_payload.pop("live2d", None)
            fallback_payload.pop("pet", None)
            fallback_payload.pop("activity", None)
    return generation_context


def _memory_backend() -> str:
    backend = str(getattr(mod_config, "MEMORY_BACKEND", "memcore") or "memcore").strip().lower()
    return backend if backend in {"legacy", "dual", "memcore"} else "memcore"


def _build_memcore_prompt_context(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    character_pack_id: str,
    current_user_record: dict[str, Any],
    now_ts: int,
    exclude_source_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    if _memory_backend() != "memcore":
        return None
    manager = getattr(engine, "memcore_manager", None)
    if manager is None or not getattr(manager, "enabled", False) or not getattr(manager, "available", False):
        logger.warning("memcore final prompt context unavailable: manager_not_available")
        return _empty_memcore_prompt_context("manager_not_available")
    try:
        payload = manager.build_prompt_context(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            current_user_record=current_user_record,
            now_ts=now_ts,
            exclude_source_ids=exclude_source_ids,
        )
    except Exception as exc:
        logger.warning("memcore final prompt context failed: %s", str(exc) or exc.__class__.__name__)
        return _empty_memcore_prompt_context(str(exc) or exc.__class__.__name__)
    if not isinstance(payload, dict) or not payload.get("ok"):
        reason = str((payload or {}).get("reason") or (payload or {}).get("status") or "unknown")
        logger.warning("memcore final prompt context unavailable: %s", reason)
        return _empty_memcore_prompt_context(reason)
    return payload


def _empty_memcore_prompt_context(reason: str) -> dict[str, Any]:
    return {
        "operation": "build_prompt_context",
        "ok": False,
        "status": "unavailable",
        "reason": str(reason or "unknown"),
        "raw": [],
        "episodic": [],
        "semantic": [],
        "raw_text": "",
        "episodic_text": "",
        "semantic_text": "",
        "rendered_text": "",
    }
