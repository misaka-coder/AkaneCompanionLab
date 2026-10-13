"""Prompt token estimation and bounded history trimming."""

from __future__ import annotations

import json
from typing import Any

import config as mod_config
from ..text_utils import estimate_prompt_tokens, render_chat_timeline
from .response_assembly import build_generation_context
from .response_contracts import ResponseEnvironment, ResponseMaterials, ResponseRequest
from .response_exposure import ResponseExposure
from .response_projection import ResponseProjection, _projection_failure_context


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
    return estimate_prompt_tokens(text)


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


def _trim_oldest_provider_history_group(history_turns: list[dict[str, Any]]) -> int:
    """Drop one oldest complete provider-history group.

    Provider projections no longer carry host-only turn ids once converted to
    request messages. A new user message is therefore the safest portable
    group boundary: remove the oldest user/assistant/tool prefix up to the next
    user message. If only one historical group remains, drop it whole rather
    than splitting a tool exchange. The current user turn is stored separately
    and is never passed to this helper.
    """

    if not history_turns:
        return 0
    remove_count = len(history_turns)
    for index in range(1, len(history_turns)):
        if str(history_turns[index].get("role") or "").strip().lower() == "user":
            remove_count = index
            break
    del history_turns[:remove_count]
    return remove_count


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


def fit_context_budget(
    engine: Any,
    request: ResponseRequest,
    environment: ResponseEnvironment,
    projection: ResponseProjection,
    materials: ResponseMaterials,
    exposure: ResponseExposure,
    generation_context: dict[str, Any],
) -> dict[str, Any]:
    prompt_token_limit = max(0, int(getattr(mod_config, "LLM_AUTO_COMPACT_TOKEN_LIMIT", 0) or 0))
    window = max(0, int(getattr(mod_config, "LLM_CONTEXT_WINDOW", 0) or 0))
    if window:
        output = max(0, int(getattr(mod_config, "LLM_CHAT_MAX_OUTPUT_TOKENS", 0) or 0))
        capacity = max(1, window - output)
        prompt_token_limit = min(prompt_token_limit, capacity) if prompt_token_limit else capacity
    initial_prompt_tokens = _estimate_generation_context_tokens(generation_context, exposure.native_tools)
    prompt_tokens = initial_prompt_tokens
    compact_attempted = False
    if prompt_token_limit and initial_prompt_tokens > prompt_token_limit and projection.projection_read_active:
        compact_attempted, generation_context = _compact_context(
            engine,
            request,
            environment,
            projection,
            materials,
            exposure,
            generation_context,
            prompt_token_limit=prompt_token_limit,
        )
        if "memcore_projection_failure" in generation_context:
            return generation_context
        prompt_tokens = _estimate_generation_context_tokens(generation_context, exposure.native_tools)
    # Emergency second boundary: compaction normally keeps these layers small,
    # but a blocked turn or one oversized imported/tool trace must never make
    # context grow without a bound. Trim complete authoritative history groups,
    # or legacy raw history; confirmed memory and the current user message
    # remain intact. Legacy summary inputs are no longer read by this pipeline.
    trimmed_layers: list[str] = []
    trimmed_history_messages = 0
    if prompt_token_limit and projection.projection_authoritative:
        while (
            prompt_tokens > prompt_token_limit
            and projection.authoritative_history_turns
        ):
            removed = _trim_oldest_provider_history_group(projection.authoritative_history_turns)
            if removed <= 0:
                break
            trimmed_history_messages += removed
            if "memcore_history" not in trimmed_layers:
                trimmed_layers.append("memcore_history")
            generation_context = build_generation_context(engine, request, environment, projection, materials, exposure)
            prompt_tokens = _estimate_generation_context_tokens(generation_context, exposure.native_tools)
    if prompt_token_limit and not projection.projection_authoritative:
        while prompt_tokens > prompt_token_limit and _trim_oldest_prompt_raw_record(
            projection.raw_records,
            current_source_id=projection.current_source_id,
            current_content=str(projection.current_record.get("content") or ""),
        ):
            projection.raw_text = render_chat_timeline(projection.raw_records)
            if "raw" not in trimmed_layers:
                trimmed_layers.append("raw")
            generation_context = build_generation_context(engine, request, environment, projection, materials, exposure)
            prompt_tokens = _estimate_generation_context_tokens(generation_context, exposure.native_tools)
    generation_context["prompt_budget"] = {
        "limit_tokens": prompt_token_limit,
        "initial_estimated_tokens": initial_prompt_tokens,
        "final_estimated_tokens": prompt_tokens,
        "compact_attempted": compact_attempted,
        "trimmed_layers": trimmed_layers,
        "trimmed_history_messages": trimmed_history_messages,
    }
    return generation_context


def _compact_context(
    engine: Any,
    request: ResponseRequest,
    environment: ResponseEnvironment,
    projection: ResponseProjection,
    materials: ResponseMaterials,
    exposure: ResponseExposure,
    generation_context: dict[str, Any],
    *,
    prompt_token_limit: int = 0,
) -> tuple[bool, dict[str, Any]]:
    manager = getattr(engine, "memcore_manager", None)
    compact_sync = getattr(manager, "compact_due_sync", None)
    if not callable(compact_sync):
        return False, generation_context
    capture = getattr(manager, "capture_compaction_request", None)
    capture_request = getattr(getattr(engine, "llm", None), "capture_memcore_generation_request", None)
    if callable(capture) and callable(capture_request) and str(getattr(mod_config, "MEMCORE_COMPACTION_REQUEST_MODE", "independent")) == "chat_prefix":
        from .response_assembly import finalize_context
        from .final_response_constants import FINAL_RESPONSE_TEMPERATURE
        effective = finalize_context(engine, request, environment, projection, exposure, dict(generation_context))
        fixed = _estimate_generation_context_tokens({**effective, "post_user_turns": []}, exposure.native_tools)
        working_budget = max(1, (prompt_token_limit or
                                getattr(mod_config, "LLM_AUTO_COMPACT_TOKEN_LIMIT", 0) or
                                getattr(mod_config, "LLM_CONTEXT_WINDOW", 0) or 200000) - fixed)
        observer = engine._build_memcore_request_observer(
            generation_context=effective, profile_user_id=request.profile_user_id,
            session_id=request.session_id, character_pack_id=request.character_pack_id,
            request_projection_state=request.request_projection_state,
        )
        captured = capture_request(
            system_prompt=effective["system_prompt"],
            user_prompt=engine._request_projection_user_prompt(request.request_projection_state, effective["user_prompt"]),
            temperature=FINAL_RESPONSE_TEMPERATURE, prompt_cache_key=engine._final_prompt_cache_key(effective),
            user_images=request.native_user_images,
            system_extra_blocks=effective.get("system_extra_blocks"), history_turns=effective.get("history_turns"),
            ephemeral_turns=effective.get("ephemeral_turns"), post_user_turns=effective.get("post_user_turns"),
            native_tools=exposure.native_tools, native_tool_choice=effective.get("native_tool_choice"),
            execution_target=request.execution_target, chat_model_override=request.chat_model_override,
            request_observer=observer, working_budget_tokens=working_budget,
        )
        if observer is None:
            capture(request={"_compaction_request": captured, "_working_budget_tokens": working_budget}, generation_context=effective,
                    profile_user_id=request.profile_user_id, session_id=request.session_id, character_pack_id=request.character_pack_id)
    compact_sync(
        profile_user_id=request.profile_user_id,
        session_id=request.session_id,
        character_pack_id=request.character_pack_id,
        provider_profile=str(projection.provider_projection.get("provider_profile") or ""),
        chat_model_override=request.chat_model_override,
    )
    projection.adopt(projection.read(engine, request), update_authority=True)
    if not projection.projection_read_active and not projection.projection_migration_window:
        return True, _projection_failure_context(projection.provider_projection, prompt_scope=request.prompt_scope)
    if projection.projection_read_active and projection.projection_authoritative:
        exposure.synchronize_catalog()
        failure = exposure.failure_context()
        if failure is not None:
            return True, failure
        if not exposure.capability_catalog_lifecycle_enabled:
            exposure.shape_projection()
        projection.prepare_turns(request, always_overlay=True)
    return True, build_generation_context(engine, request, environment, projection, materials, exposure)
