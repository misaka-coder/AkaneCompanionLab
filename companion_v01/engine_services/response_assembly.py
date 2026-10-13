"""Final response prompt rules and generation-context assembly."""

from __future__ import annotations

import hashlib
from typing import Any

import config as mod_config
from ..client_protocol import ClientMode
from ..tool_invocation import TOOL_CAPABILITY_SELECTION_FIELD, TOOL_EXECUTION_RECEIPTS_FIELD
from .response_contracts import ResponseEnvironment, ResponseMaterials, ResponseRequest
from .response_exposure import ResponseExposure
from .response_materials import prepare_request_materials
from .response_projection import ResponseProjection, _compare_memcore_projection_shadow

COMPACT_READBACK_STABLE_HINT = (
    "部分已完成轮次的历史工具结果会显示为紧凑回执，而不是完整正文。\n"
    "这不代表结果丢失。工具调用参数仍在对应历史 action 中可见；回执保留结果状态与 source_id。\n"
    "若当前问题依赖其中的具体内容，可以按 source_id 单个或批量打开完整结果；\n"
    "若旧结果不足，可以继续调用相应工具。长任务中已配对的旧段也可能附阶段总结后收起；\n"
    "不要在已有信息足够时机械回读。"
)
COMPACT_ARGUMENT_READBACK_STABLE_HINT = (
    "已结束轮次的长工具参数或结果可能只保留预览和 source_id，原文仍可回读。"
    "需要具体内容、复制或复用被收起的参数时，用 open_memory 打开原文；已有信息足够时无需回读。"
    "长任务中已配对旧段可能附阶段总结后收起，尚未配对的调用保持完整。"
)


def build_compact_readback_hint(
    *, policy: str, has_compact_history: bool, has_compact_arguments_history: bool = False
) -> str:
    """当前启用紧凑策略或可见历史仍含 compact turn 时返回稳定回读说明, 否则空串。

    默认 full 策略且无 compact 历史时返回空串, 保证旧请求体逐字节不变。
    """
    policy = str(policy or "").strip().lower()
    if policy == "compact_arguments_after_terminal" or has_compact_arguments_history:
        return COMPACT_ARGUMENT_READBACK_STABLE_HINT
    if policy == "compact_after_terminal" or bool(has_compact_history):
        return COMPACT_READBACK_STABLE_HINT
    return ""


def with_compact_readback_hint(
    stable_system_context: str,
    *,
    policy: str,
    has_compact_history: bool,
    has_compact_arguments_history: bool = False,
) -> str:
    """Compose the compact readback rule once even if prompt budgeting rebuilds context."""

    base = str(stable_system_context or "")
    hint = build_compact_readback_hint(
        policy=policy,
        has_compact_history=has_compact_history,
        has_compact_arguments_history=has_compact_arguments_history,
    )
    if not hint or hint in base:
        return base
    return "\n\n".join(part for part in (base, hint) if part.strip())


def with_current_actor_relation(current_message_text: str, *, relation: str) -> str:
    """Add a verified relation only to the request-local current user tail."""

    text = str(current_message_text or "").strip()
    normalized_relation = str(relation or "").strip().lower()
    if normalized_relation not in {"owner", "participant"} or not text:
        return text
    lines = text.splitlines()
    if any(line.strip().startswith("actor_relation:") for line in lines):
        return text
    for index, line in enumerate(lines):
        if line.strip().startswith("actor:"):
            indent = line[: len(line) - len(line.lstrip())]
            lines.insert(index + 1, f"{indent}actor_relation: {normalized_relation}")
            return "\n".join(lines)
    return f"{text}\nactor_relation: {normalized_relation}"


def build_generation_context(
    engine: Any,
    request: ResponseRequest,
    environment: ResponseEnvironment,
    projection: ResponseProjection,
    materials: ResponseMaterials,
    exposure: ResponseExposure,
) -> dict[str, Any]:
    # Budget compaction can publish a new generation inside the same task.
    # Refresh at that boundary, otherwise reuse the initial dynamic snapshot.
    materials = prepare_request_materials(engine, request, environment, projection, exposure, fallback=materials)
    current_message_visible_in_raw = (
        bool(projection.provider_projection.get("current_source_visible"))
        if (projection.projection_read_active)
        else bool(
            projection.current_source_id
            and any(
                str(record.get("source_id") or "").strip() == projection.current_source_id
                for record in projection.raw_records
            )
        )
    )
    if projection.projection_authoritative:
        history_turns = [dict(turn) for turn in projection.authoritative_history_turns]
    elif projection.current_source_id:
        history_records = [
            record
            for record in projection.raw_records
            if str(record.get("source_id") or "").strip() != projection.current_source_id
        ]
    else:
        history_records, _current = engine._split_history_records(
            recent_raw=projection.raw_records,
            user_message=request.user_message,
            now_ts=request.now_ts,
        )
    if not projection.projection_authoritative:
        history_builder = getattr(engine, "_build_history_turns", None)
        history_turns = history_builder(history_records) if callable(history_builder) else []
        if not history_turns and projection.raw_text and not projection.raw_records:
            history_turns = [
                {
                    "role": "user",
                    "content": f"当前会话中所有未总结的原始消息：\n{projection.raw_text}",
                }
            ]
    effective_stable_system_context = with_compact_readback_hint(
        request.stable_system_context,
        policy=str(getattr(mod_config, "MEMCORE_OPERATION_PROJECTION_POLICY", "full_until_raw_compaction") or ""),
        has_compact_history=bool(projection.provider_projection.get("has_compact_history")),
        has_compact_arguments_history=bool(projection.provider_projection.get("has_compact_arguments_history")),
    )
    from ..tool_scheduling import TOOL_EXECUTION_HINT
    if environment.tool_capability_available and callable(getattr(getattr(engine, "host_tool_jobs", None), "invoke", None)):
        if TOOL_EXECUTION_HINT not in effective_stable_system_context:
            effective_stable_system_context = "\n\n".join(filter(None, (effective_stable_system_context, TOOL_EXECUTION_HINT)))
    generation_context = environment.prompt_builder.build_final_generation_context(
        now_ts=request.now_ts,
        raw_text="" if projection.projection_authoritative else projection.raw_text,
        history_turns=history_turns,
        current_message_text=with_current_actor_relation(
            projection.current_message_text,
            relation=request.current_actor_relation,
        ),
        episodic_summary_text="",
        semantic_summary_text="",
        memory_text=materials.memory_text,
        current_visual_context=materials.current_visual_context,
        resource_context=materials.resource_context,
        extra_context=materials.extra.stable,
        volatile_extra_context=materials.extra.volatile,
        extra_context_audit_sections=materials.extra.audit_sections,
        stable_system_context=effective_stable_system_context,
        persona_system_context=str(materials.persona_context.get("system_context") or ""),
        persona_reference_context=str(materials.persona_context.get("reference_context") or ""),
        persona_active_id=str(materials.persona_context.get("active_id") or ""),
        domain_profile_context=environment.domain_profile_context,
        visual_defaults=materials.visual_defaults,
        allow_tool_call=environment.effective_allow_tool_call,
        capability_catalog_context=exposure.capability_catalog_context,
        execution_context=exposure.execution_context,
        execution_stable_context=getattr(exposure, "execution_stable_context", ""),
        tool_prompt_context=exposure.tool_prompt_context,
        debug_enabled=environment.debug_enabled,
        system_prompt_override=materials.system_prompt_override,
        mode_prompt_override=materials.mode_prompt_override,
        prompt_scope=request.prompt_scope,
        current_message_in_raw=current_message_visible_in_raw,
        **(
            {"registered_stable_blocks_override": exposure.published_plugin_blocks}
            if callable(exposure.block_reader)
            else {}
        ),
    )
    cache_scope_material = "\x00".join(
        (
            str(request.profile_user_id or ""),
            str(request.session_id or ""),
            str(request.character_pack_id or ""),
        )
    )
    generation_context["prompt_cache_scope_hash"] = hashlib.sha256(
        cache_scope_material.encode("utf-8", errors="ignore")
    ).hexdigest()
    generation_context["tool_exposure_lifecycle"] = dict(exposure.exposure_lifecycle)
    generation_context["memcore_history_start_index"] = max(
        0,
        len(list(generation_context.get("history_turns") or [])) - len(history_turns),
    )
    generation_context["post_user_turns"] = [dict(turn) for turn in projection.effective_post_user_turns]
    generation_context["memcore_projection_read"] = {
        key: value for key, value in projection.provider_projection.items() if key not in {"history_turns"}
    }
    if projection.projection_recovery_failure is not None:
        generation_context["memcore_projection_recovery"] = dict(projection.projection_recovery_failure)
    prompt_context_lifecycle = {
        "event_timeline_authoritative": projection.event_timeline_authoritative,
        "skipped_event_backed": list(materials.extra.materialized.skipped_event_backed),
    }
    if exposure.capability_catalog_lifecycle_enabled:
        prompt_context_lifecycle["capability_catalog"] = {
            "status": str(exposure.capability_catalog_lifecycle.get("status") or "skipped"),
            "role": str(exposure.capability_catalog_lifecycle.get("catalog_role") or ""),
            "changed": bool(exposure.capability_catalog_lifecycle.get("changed")),
            "compaction_generation": int(exposure.capability_catalog_lifecycle.get("compaction_generation") or 0),
        }
    generation_context["prompt_context_lifecycle"] = prompt_context_lifecycle
    return generation_context


def finalize_context(
    engine: Any,
    request: ResponseRequest,
    environment: ResponseEnvironment,
    projection: ResponseProjection,
    exposure: ResponseExposure,
    generation_context: dict[str, Any],
) -> dict[str, Any]:
    if not environment.care_enabled:
        fallback_payload = generation_context.get("fallback")
        if isinstance(fallback_payload, dict):
            fallback_payload.pop("state_request", None)
    generation_context["allow_tool_call"] = environment.effective_allow_tool_call
    generation_context["native_tools"] = exposure.native_tools
    generation_context["native_tool_choice"] = (
        "auto"
        if exposure.native_tools and environment.effective_allow_tool_call
        else "none"
        if exposure.native_tools
        else ""
    )
    generation_context["post_user_turns"] = projection.effective_post_user_turns
    generation_context["memcore_projection_shadow"] = _compare_memcore_projection_shadow(
        engine,
        generation_context=generation_context,
        profile_user_id=request.profile_user_id,
        session_id=request.session_id,
        character_pack_id=request.character_pack_id,
        current_source_id=projection.current_source_id,
        chat_model_override=request.chat_model_override,
        execution_target=request.execution_target,
    )
    generation_context["prompt_profile"] = environment.prompt_profile.to_public_dict()
    generation_context["domain_profile"] = environment.domain_profile.to_public_dict()
    generation_context["prompt_scope"] = request.prompt_scope
    # Host-only normalization flag: never a second prompt profile/cache family.
    generation_context["allow_deliberate_silence"] = bool(
        request.current_input_transient and environment.client_context.effective_mode == ClientMode.DESKTOP_PET
    )
    execution_receipts = getattr(exposure.capability_selection, "execution_receipts", {})
    if isinstance(execution_receipts, dict) and execution_receipts:
        generation_context[TOOL_EXECUTION_RECEIPTS_FIELD] = {
            str(name): dict(receipt) for name, receipt in execution_receipts.items() if isinstance(receipt, dict)
        }
    # M66-C frozen round: carry the resolved CapabilitySelection into the
    # invocation phase so _prepare_tool_round_decisions can pass it to
    # normalize/validate without re-resolving handlers a second time.
    if exposure.capability_selection is not None:
        generation_context[TOOL_CAPABILITY_SELECTION_FIELD] = exposure.capability_selection
    if environment.client_context.effective_mode == ClientMode.QQ_TEXT:
        fallback_payload = generation_context.get("fallback")
        if isinstance(fallback_payload, dict):
            fallback_payload.pop("character", None)
            fallback_payload.pop("scene", None)
            fallback_payload.pop("live2d", None)
            fallback_payload.pop("pet", None)
            fallback_payload.pop("activity", None)
    return generation_context
