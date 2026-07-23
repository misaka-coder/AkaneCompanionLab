"""Final response context builder extracted from engine.py."""

from __future__ import annotations
import hashlib
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
from ..tool_invocation import TOOL_CAPABILITY_SELECTION_FIELD, TOOL_EXECUTION_RECEIPTS_FIELD

logger = logging.getLogger("akane.response_builder")

PROJECTION_READ_MIGRATION_REASONS = frozenset({"legacy_memory_backend"})


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
    stable_system_context: str = "",
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
    normalized_prompt_scope = str(prompt_scope or "").strip().lower()
    client_context = client_context or engine._resolve_client_protocol_context({})
    care_status_getter = getattr(engine, "care_feature_status", None)
    care_status = care_status_getter() if callable(care_status_getter) else {"enabled": True}
    care_enabled = bool(care_status.get("enabled", True))
    care_context_resolver = getattr(engine, "care_enabled_for_context", None)
    if care_enabled and callable(care_context_resolver):
        care_enabled = bool(
            care_context_resolver(
                character_pack_id=character_pack_id,
                client_context=client_context,
            )
        )
    prompt_builder = engine._get_prompt_builder()
    prompt_profile = engine._get_prompt_profile_registry().resolve(client_context, care_enabled=care_enabled)
    domain_profile = DomainProfileRegistry().get(
        domain_profile_id if prompt_profile.includes(PromptModule.DOMAIN_PROFILE) else ""
    )
    domain_profile_context = build_domain_profile_prompt(domain_profile)
    tool_capability_available = bool(
        prompt_profile.includes(PromptModule.TOOLS)
        and client_context.has_capability(ClientCapability.TOOL_ACTIONS)
    )
    effective_allow_tool_call = bool(allow_tool_call and tool_capability_available)
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
    raw_records = list(visible_recent_raw)
    if _memory_backend() == "memcore":
        raw_text = ""
        episodic_summary_text = ""
        semantic_summary_text = ""
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
    current_source_id = str(current_record.get("source_id") or "").strip()
    provider_projection = _build_memcore_provider_history(
        engine,
        profile_user_id=profile_user_id,
        session_id=session_id,
        character_pack_id=character_pack_id,
        current_source_id=current_source_id,
        chat_model_override=chat_model_override,
        exclude_source_ids=list(excluded_prompt_sources),
    )
    projection_read_active = bool(provider_projection.get("ok"))
    projection_migration_window = (
        str(provider_projection.get("reason") or "") in PROJECTION_READ_MIGRATION_REASONS
    )
    projection_authoritative = not projection_migration_window
    if _memory_backend() == "memcore" and not projection_read_active and not projection_migration_window:
        return _projection_failure_context(provider_projection, prompt_scope=normalized_prompt_scope)
    memory_text = "\n\n".join(confirmed_snippets) if confirmed_snippets else ""
    extra_context = str(extra_user_context or "").strip()
    attachment_service = engine._get_attachment_inbox_service()
    attachment_focus_context = (
        attachment_service.build_activity_prompt_context(
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
        if (
            profile_persona_enabled
            and persona_service is not None
            and prompt_profile.includes(PromptModule.PERSONA)
        )
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
    # The last flag is a placement contract, not a Bot-specific exception:
    # per-turn transport/event material changes on every request and must stay
    # after append-only history, while durable runtime context can precede it.
    extra_context_candidates = [
        (
            "client_mode",
            engine._build_client_mode_prompt_context(client_context)
            if prompt_profile.includes(PromptModule.CLIENT_MODE)
            else "",
            False,
        ),
        (
            "relationship",
            engine._build_memory_relationship_context(
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                now_ts=now_ts,
            ),
            False,
        ),
        # Working sets can change after any attachment/tool/task transition.
        # They must stay behind the append-only MemCore projection; otherwise
        # one status update rewrites the provider prefix before the timeline.
        ("task_workspace", task_workspace_context, True),
        ("workspace_files", workspace_file_context, True),
        ("attachment_focus", attachment_focus_context, True),
        ("generated_files", generated_file_context, True),
        ("pending_gifts", pending_gift_context, True),
        ("gift_observation", gift_observation_context, True),
        (
            "turn_extra_context",
            extra_context if prompt_profile.includes(PromptModule.EXTRA_CONTEXT) else "",
            True,
        ),
    ]
    extra_context_audit_sections = engine._build_extra_context_audit_sections(
        [(name, text) for name, text, _volatile in extra_context_candidates]
    )
    volatile_names = {
        name for name, _text, volatile in extra_context_candidates if volatile
    }
    stable_extra_context_sections = [
        section["text"]
        for section in extra_context_audit_sections
        if str(section.get("name") or "") not in volatile_names
    ]
    volatile_extra_context_sections = [
        section["text"]
        for section in extra_context_audit_sections
        if str(section.get("name") or "") in volatile_names
    ]
    merged_extra_context = (
        "\n\n".join(stable_extra_context_sections)
        if stable_extra_context_sections
        else "(无额外上下文)"
    )
    merged_volatile_extra_context = "\n\n".join(volatile_extra_context_sections)
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
            "emotion 的可选值已由当前角色包表情图片清单约束。"
            if resource_manifest
            else "当前角色包没有可用的表情图片清单。"
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
        else ""
    )
    if visual_observation_sections:
        current_visual_context = "\n\n".join(
            part for part in [current_visual_context, *visual_observation_sections] if part
        )
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
    capability_selection = (
        engine._resolve_capability_selection(
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile.id,
            intent_text=user_message,
        )
        if tool_capability_available
        else None
    )
    if enable_native_tools:
        from .. import tool_orchestration_engine as _toe

        ready_handlers = engine._resolve_tool_handlers(
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile.id,
            capability_selection=capability_selection,
        )
        schema_tool_names = tuple(
            getattr(capability_selection, "schema_tool_names", ())
            or getattr(capability_selection, "tool_names", ())
            or ()
        )
        frozen_handlers = getattr(capability_selection, "resolved_handlers", {})
        schema_handlers = {
            name: frozen_handlers[name]
            for name in schema_tool_names
            if name in frozen_handlers
        }
        try:
            provider_supports_native_tools = engine.llm.chat_supports_native_tools(
                chat_model_override=chat_model_override
            )
        except TypeError:
            provider_supports_native_tools = engine.llm.chat_supports_native_tools()
        native_plan = _toe.build_native_tool_decision_plan(
            schema_handlers or ready_handlers,
            allow_tool_call=tool_capability_available,
            provider_supports_native_tools=provider_supports_native_tools,
            allowed_tool_names=schema_tool_names,
        )
        if native_plan.enabled:
            native_tools = native_plan.tools
            native_legacy_exclusions = native_plan.legacy_prompt_exclusions
        elif native_plan.status == "unsupported":
            engine.llm.record_metric("native_tool_provider_unsupported")
    tool_prompt_context = engine._build_tool_prompt_context(
        allow_tool_call=tool_capability_available,
        client_context=client_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
        exclude_tool_types=native_legacy_exclusions,
        domain_profile_id=domain_profile.id,
        capability_selection=capability_selection,
        include_capability_status=not bool(native_tools),
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
    effective_post_user_turns = [
        dict(turn) for turn in list(post_user_turns or []) if isinstance(turn, dict)
    ]
    system_prompt_override = prompt_profile.system_prompt_override
    if not care_enabled and not system_prompt_override:
        system_prompt_override = prompt_builder.persona.final_system_prompt
    if not care_enabled:
        system_prompt_override = strip_care_prompt_contract(system_prompt_override)

    def _build_generation_context() -> dict[str, Any]:
        current_message_visible_in_raw = bool(provider_projection.get("current_source_visible")) if (
            projection_read_active
        ) else bool(
            current_source_id
            and any(str(record.get("source_id") or "").strip() == current_source_id for record in raw_records)
        )
        if projection_authoritative:
            history_turns = [dict(turn) for turn in list(provider_projection.get("history_turns") or [])]
        elif current_source_id:
            history_records = [
                record
                for record in raw_records
                if str(record.get("source_id") or "").strip() != current_source_id
            ]
        else:
            history_records, _current = engine._split_history_records(
                recent_raw=raw_records,
                user_message=user_message,
                now_ts=now_ts,
            )
        if not projection_authoritative:
            history_builder = getattr(engine, "_build_history_turns", None)
            history_turns = history_builder(history_records) if callable(history_builder) else []
            if not history_turns and raw_text and not raw_records:
                history_turns = [
                    {
                        "role": "user",
                        "content": f"当前会话中所有未总结的原始消息：\n{raw_text}",
                    }
                ]
        generation_context = prompt_builder.build_final_generation_context(
            now_ts=now_ts,
            raw_text="" if projection_authoritative else raw_text,
            history_turns=history_turns,
            current_message_text=current_message_text,
            episodic_summary_text="" if projection_authoritative else episodic_summary_text,
            semantic_summary_text="" if projection_authoritative else semantic_summary_text,
            memory_text=memory_text,
            current_visual_context=current_visual_context,
            resource_context=resource_context,
            extra_context=merged_extra_context,
            volatile_extra_context=merged_volatile_extra_context,
            extra_context_audit_sections=extra_context_audit_sections,
            stable_system_context=stable_system_context,
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
            prompt_scope=normalized_prompt_scope,
            current_message_in_raw=current_message_visible_in_raw,
        )
        cache_scope_material = "\x00".join(
            (
                str(profile_user_id or ""),
                str(session_id or ""),
                str(character_pack_id or ""),
            )
        )
        generation_context["prompt_cache_scope_hash"] = hashlib.sha256(
            cache_scope_material.encode("utf-8", errors="ignore")
        ).hexdigest()
        generation_context["memcore_history_start_index"] = max(
            0,
            len(list(generation_context.get("history_turns") or [])) - len(history_turns),
        )
        generation_context["post_user_turns"] = [dict(turn) for turn in effective_post_user_turns]
        generation_context["memcore_projection_read"] = {
            key: value
            for key, value in provider_projection.items()
            if key not in {"history_turns"}
        }
        return generation_context

    generation_context = _build_generation_context()
    prompt_token_limit = max(0, int(getattr(mod_config, "LLM_AUTO_COMPACT_TOKEN_LIMIT", 0) or 0))
    initial_prompt_tokens = _estimate_generation_context_tokens(generation_context, native_tools)
    compact_attempted = False
    if prompt_token_limit and initial_prompt_tokens > prompt_token_limit and projection_read_active:
        manager = getattr(engine, "memcore_manager", None)
        compact_sync = getattr(manager, "compact_due_sync", None)
        if callable(compact_sync):
            compact_attempted = True
            compact_sync(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                provider_profile=str(provider_projection.get("provider_profile") or ""),
            )
            provider_projection = _build_memcore_provider_history(
                engine,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                current_source_id=current_source_id,
                chat_model_override=chat_model_override,
                exclude_source_ids=list(excluded_prompt_sources),
            )
            projection_read_active = bool(provider_projection.get("ok"))
            projection_migration_window = (
                str(provider_projection.get("reason") or "") in PROJECTION_READ_MIGRATION_REASONS
            )
            projection_authoritative = not projection_migration_window
            if not projection_read_active and not projection_migration_window:
                return _projection_failure_context(provider_projection, prompt_scope=normalized_prompt_scope)
            generation_context = _build_generation_context()

    # Emergency second boundary: compaction normally keeps these layers small,
    # but a single oversized imported/tool trace must never make context grow
    # without a bound. Drop oldest raw lines first, then oldest episodic lines;
    # semantic memory and the current user message remain intact.
    trimmed_layers: list[str] = []
    if prompt_token_limit and not projection_authoritative:
        while (
            _estimate_generation_context_tokens(generation_context, native_tools) > prompt_token_limit
            and _trim_oldest_prompt_raw_record(
                raw_records,
                current_source_id=current_source_id,
                current_content=str(current_record.get("content") or ""),
            )
        ):
            raw_text = render_chat_timeline(raw_records)
            if "raw" not in trimmed_layers:
                trimmed_layers.append("raw")
            generation_context = _build_generation_context()
        while (
            _estimate_generation_context_tokens(generation_context, native_tools) > prompt_token_limit
            and episodic_summary_text
        ):
            reduced = _drop_oldest_prompt_lines(episodic_summary_text)
            episodic_summary_text = "" if reduced == episodic_summary_text else reduced
            if "episodic" not in trimmed_layers:
                trimmed_layers.append("episodic")
            generation_context = _build_generation_context()
    generation_context["prompt_budget"] = {
        "limit_tokens": prompt_token_limit,
        "initial_estimated_tokens": initial_prompt_tokens,
        "final_estimated_tokens": _estimate_generation_context_tokens(generation_context, native_tools),
        "compact_attempted": compact_attempted,
        "trimmed_layers": trimmed_layers,
    }
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
    generation_context["native_tool_choice"] = (
        "auto" if native_tools and effective_allow_tool_call else "none" if native_tools else ""
    )
    generation_context["post_user_turns"] = effective_post_user_turns
    generation_context["memcore_projection_shadow"] = _compare_memcore_projection_shadow(
        engine,
        generation_context=generation_context,
        profile_user_id=profile_user_id,
        session_id=session_id,
        character_pack_id=character_pack_id,
        current_source_id=current_source_id,
        chat_model_override=chat_model_override,
    )
    generation_context["prompt_profile"] = prompt_profile.to_public_dict()
    generation_context["domain_profile"] = domain_profile.to_public_dict()
    generation_context["prompt_scope"] = normalized_prompt_scope
    execution_receipts = getattr(capability_selection, "execution_receipts", {})
    if isinstance(execution_receipts, dict) and execution_receipts:
        generation_context[TOOL_EXECUTION_RECEIPTS_FIELD] = {
            str(name): dict(receipt)
            for name, receipt in execution_receipts.items()
            if isinstance(receipt, dict)
        }
    # M66-C frozen round: carry the resolved CapabilitySelection into the
    # invocation phase so _prepare_tool_round_decisions can pass it to
    # normalize/validate without re-resolving handlers a second time.
    if capability_selection is not None:
        generation_context[TOOL_CAPABILITY_SELECTION_FIELD] = capability_selection
    if client_context.effective_mode == ClientMode.QQ_TEXT:
        fallback_payload = generation_context.get("fallback")
        if isinstance(fallback_payload, dict):
            fallback_payload.pop("character", None)
            fallback_payload.pop("scene", None)
            fallback_payload.pop("live2d", None)
            fallback_payload.pop("pet", None)
            fallback_payload.pop("activity", None)
    return generation_context


def _compare_memcore_projection_shadow(
    engine: Any,
    *,
    generation_context: dict[str, Any],
    profile_user_id: str,
    session_id: str,
    character_pack_id: str,
    current_source_id: str,
    chat_model_override: str,
) -> dict[str, Any]:
    if not bool(getattr(mod_config, "MEMCORE_SHADOW_COMPARE", False)):
        return {"ok": True, "status": "disabled", "reason": "shadow_compare_disabled"}
    manager = getattr(engine, "memcore_manager", None)
    compare = getattr(manager, "compare_context_projection", None)
    runtime = getattr(engine, "llm", None)
    protocol_getter = getattr(runtime, "chat_provider_protocol", None)
    history_normalizer = getattr(runtime, "normalize_chat_history_turns", None)
    if not callable(compare) or not callable(protocol_getter) or not callable(history_normalizer):
        return {"ok": False, "status": "unavailable", "reason": "projection_shadow_dependencies_unavailable"}
    try:
        protocol = str(protocol_getter(chat_model_override=chat_model_override) or "").strip().lower()
        history_turns = list(generation_context.get("history_turns") or [])
        history_start = max(0, int(generation_context.get("memcore_history_start_index") or 0))
        actual_history = history_normalizer(
            history_turns[history_start:],
            chat_model_override=chat_model_override,
        )
        result = compare(
            provider_profile=protocol,
            actual_history_messages=actual_history,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            exclude_source_ids=[current_source_id] if current_source_id else [],
        )
        safe_result = dict(result) if isinstance(result, dict) else {
            "ok": False,
            "status": "failed",
            "reason": "invalid_projection_shadow_result",
        }
        logger.info(
            "memcore projection shadow status=%s profile=%s strict_prefix=%s projection_hash=%s "
            "actual_history_hash=%s first_divergence_index=%s reason=%s",
            str(safe_result.get("status") or "unknown")[:40],
            str(safe_result.get("provider_profile") or "")[:40],
            bool(safe_result.get("strict_prefix")),
            str(safe_result.get("projection_hash") or "")[:64],
            str(safe_result.get("actual_history_hash") or "")[:64],
            int(safe_result.get("first_divergence_index") or 0),
            str(safe_result.get("divergence_reason") or safe_result.get("reason") or "")[:80],
        )
        return safe_result
    except Exception as exc:
        logger.warning("memcore projection shadow unavailable: %s", exc.__class__.__name__)
        return {"ok": False, "status": "failed", "reason": "shadow_compare_failed"}


def _build_memcore_provider_history(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    character_pack_id: str,
    current_source_id: str,
    chat_model_override: str,
    exclude_source_ids: list[str] | None = None,
) -> dict[str, Any]:
    if _memory_backend() != "memcore":
        return {"ok": False, "status": "migration_window", "reason": "legacy_memory_backend"}
    manager = getattr(engine, "memcore_manager", None)
    build_projection = getattr(manager, "build_context_projection", None)
    runtime = getattr(engine, "llm", None)
    protocol_getter = getattr(runtime, "chat_provider_protocol", None)
    if not callable(build_projection) or not callable(protocol_getter):
        return {"ok": False, "status": "unavailable", "reason": "projection_read_dependencies_unavailable"}
    if not str(current_source_id or "").strip():
        return {"ok": False, "status": "skipped", "reason": "current_source_id_missing"}
    try:
        protocol = str(protocol_getter(chat_model_override=chat_model_override) or "").strip().lower()
        projection: dict[str, Any] = {}
        for _attempt in range(2):
            candidate = build_projection(
                provider_profile=protocol,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
            projection = candidate if isinstance(candidate, dict) else {}
            if projection.get("ok"):
                break
            if str(projection.get("reason") or "") in PROJECTION_READ_MIGRATION_REASONS:
                break
        if not isinstance(projection, dict) or not projection.get("ok"):
            migration_reason = str((projection or {}).get("reason") or "")
            if migration_reason in PROJECTION_READ_MIGRATION_REASONS:
                return {"ok": False, "status": "migration_window", "reason": migration_reason}
            return {"ok": False, "status": "unavailable", "reason": "projection_build_failed"}
        current_sid = str(current_source_id).strip()
        current_source_visible = False
        current_turn_id = ""
        current_turn_metadata_present = False
        projection_messages = list(projection.get("messages") or [])
        for message in projection_messages:
            if not isinstance(message, dict):
                return {"ok": False, "status": "failed", "reason": "projection_message_invalid"}
            source_ids = [str(item or "").strip() for item in list(message.get("source_ids") or [])]
            if current_sid in source_ids:
                current_source_visible = True
                current_turn_metadata_present = "turn_id" in message
                current_turn_id = str(message.get("turn_id") or "").strip()
                break
        if not current_source_visible:
            return {"ok": False, "status": "skipped", "reason": "current_source_not_projected"}
        if current_turn_metadata_present and not current_turn_id:
            return {"ok": False, "status": "failed", "reason": "current_turn_id_missing"}

        history_turns: list[dict[str, Any]] = []
        history_source_ids: list[str] = []
        current_turn_messages: list[dict[str, Any]] = []
        excluded = {
            str(source_id or "").strip()
            for source_id in list(exclude_source_ids or [])
            if str(source_id or "").strip()
        }
        for message in projection_messages:
            if not isinstance(message, dict):
                return {"ok": False, "status": "failed", "reason": "projection_message_invalid"}
            source_ids = [str(item or "").strip() for item in list(message.get("source_ids") or [])]
            message_turn_id = str(message.get("turn_id") or "").strip()
            is_current_turn = bool(current_turn_id and message_turn_id == current_turn_id)
            if is_current_turn or current_sid in source_ids or (not current_turn_id and excluded.intersection(source_ids)):
                current_turn_messages.append(
                    {
                        "turn_id": message_turn_id or current_turn_id,
                        "payload": dict(message.get("payload") or {}),
                        "source_ids": source_ids,
                        "projection_index": int(message.get("projection_index", -1)),
                        "projection_status": str(message.get("projection_status") or "complete"),
                        "projection_version": int(message.get("projection_version") or 1),
                    }
                )
                continue
            payload = dict(message.get("payload") or {})
            role = str(payload.get("role") or "").strip().lower()
            if role not in {"user", "assistant", "tool"}:
                return {"ok": False, "status": "failed", "reason": "projection_message_unsupported"}
            history_turns.append(payload)
            history_source_ids.extend(source_id for source_id in source_ids if source_id)
        return {
            "ok": True,
            "status": "active",
            "reason": "",
            "provider_profile": str(projection.get("provider_profile") or ""),
            "history_turns": history_turns,
            "current_turn_id": current_turn_id,
            "current_turn_messages": current_turn_messages,
            "source_ids": list(dict.fromkeys(history_source_ids)),
            "source_count": len(set(history_source_ids)),
            "message_count": len(history_turns),
            "stable_prefix_hash": str(projection.get("stable_prefix_hash") or ""),
            "projection_version": int(projection.get("projection_version") or 0),
            "compaction_generation": int(projection.get("compaction_generation") or 0),
            "projection_generation": int(projection.get("projection_generation") or 0),
            "current_source_visible": True,
        }
    except Exception as exc:
        logger.warning("memcore projection read unavailable: %s", exc.__class__.__name__)
        return {"ok": False, "status": "failed", "reason": "projection_read_failed"}


def _projection_failure_context(projection: dict[str, Any], *, prompt_scope: str) -> dict[str, Any]:
    status = str((projection or {}).get("status") or "unavailable").strip()[:40] or "unavailable"
    reason = str((projection or {}).get("reason") or "projection_unavailable").strip()[:120]
    return {
        "memcore_projection_failure": {
            "status": status,
            "reason": reason or "projection_unavailable",
        },
        "prompt_scope": str(prompt_scope or "").strip(),
    }


def _estimate_generation_context_tokens(
    generation_context: dict[str, Any],
    native_tools: list[dict[str, Any]],
) -> int:
    parts = [
        str(generation_context.get("system_prompt") or ""),
        str(generation_context.get("user_prompt") or ""),
        "\n".join(str(item or "") for item in generation_context.get("system_extra_blocks") or []),
        json.dumps(
            generation_context.get("history_turns") or [],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
        json.dumps(
            generation_context.get("ephemeral_turns") or [],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
        json.dumps(
            generation_context.get("post_user_turns") or [],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
        json.dumps(native_tools or [], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    ]
    text = "\n".join(parts)
    cjk_chars = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    non_cjk_chars = max(0, len(text) - cjk_chars)
    return int(cjk_chars + ((non_cjk_chars + 3) // 4))


def _drop_oldest_prompt_lines(text: str) -> str:
    marker = "[更早内容已由上下文高水位保护省略]"
    lines = [line for line in str(text or "").splitlines() if line.strip() != marker]
    if not lines:
        return ""
    if len(lines) == 1:
        raw = lines[0]
        if len(raw) <= 256:
            return ""
        return marker + "\n" + raw[len(raw) // 2 :]
    remove_count = max(1, len(lines) // 4)
    remaining = lines[remove_count:]
    return marker + "\n" + "\n".join(remaining)


def _trim_oldest_prompt_raw_record(
    records: list[dict[str, Any]],
    *,
    current_source_id: str,
    current_content: str,
) -> bool:
    """Trim one oldest history record while preserving the current user turn."""

    current_sid = str(current_source_id or "").strip()
    normalized_current = str(current_content or "").strip()
    candidate_index = -1
    for index, record in enumerate(records):
        source_id = str(record.get("source_id") or "").strip()
        role = str(record.get("role") or "").strip().lower()
        content = str(record.get("content") or "").strip()
        if current_sid and source_id == current_sid:
            continue
        if not current_sid and index == len(records) - 1 and role == "user" and content == normalized_current:
            continue
        candidate_index = index
        break
    if candidate_index < 0:
        return False
    candidate = dict(records[candidate_index])
    content = str(candidate.get("content") or "")
    marker = "[更早内容已由上下文高水位保护省略]"
    if len(content) > 512:
        candidate["content"] = marker + "\n" + content[len(content) // 2 :]
        records[candidate_index] = candidate
    else:
        records.pop(candidate_index)
    return True


def _memory_backend() -> str:
    backend = str(getattr(mod_config, "MEMORY_BACKEND", "memcore") or "memcore").strip().lower()
    return backend if backend in {"legacy", "dual", "memcore"} else "memcore"
