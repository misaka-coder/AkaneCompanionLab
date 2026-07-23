from __future__ import annotations

from typing import Any

import config

from .retrieval_types import RetrievalPipelineResult
from .text_utils import detect_time_of_day_from_text, normalize_text
from .tool_runtime import ToolExecutionResult
from .memcore_integration.diagnostics import build_shadow_payload


def collect_visible_context_source_ids(
    *,
    recent_raw: list[dict[str, Any]],
    recent_episodic_summaries: list[dict[str, Any]],
    recent_semantic_summaries: list[dict[str, Any]],
    extra_source_ids: list[str] | None = None,
) -> list[str]:
    visible_ids: list[str] = []
    seen: set[str] = set()

    def add_source_id(value: Any) -> None:
        source_id = str(value or "").strip()
        if not source_id or source_id in seen:
            return
        seen.add(source_id)
        visible_ids.append(source_id)

    for source_id in extra_source_ids or []:
        add_source_id(source_id)
    for row in recent_raw or []:
        add_source_id(row.get("source_id"))
    for summary in recent_episodic_summaries or []:
        add_source_id(summary.get("source_id") or summary.get("summary_id"))
    for semantic_summary in recent_semantic_summaries or []:
        add_source_id(semantic_summary.get("source_id") or semantic_summary.get("semantic_id"))
    return visible_ids


def resolve_pre_retrieval_enabled(engine: Any, *, payload: dict[str, Any]) -> bool:
    override = engine._coerce_bool(payload.get("pre_retrieval_enabled"))
    if override is not None:
        return bool(override)
    return bool(getattr(config, "PRE_RETRIEVAL_DEFAULT_ENABLED", True))


def build_skipped_pre_retrieval_pipeline(
    engine: Any,
    *,
    user_message: str,
    now_ts: int,
    reason: str,
    use_legacy_service: bool = True,
) -> RetrievalPipelineResult:
    retrieval_service = engine._get_retrieval_service() if use_legacy_service else None
    try:
        time_hint = (
            retrieval_service._extract_time_hint(user_message=user_message, now_ts=now_ts)
            if retrieval_service is not None
            else {
                "date_label": None,
                "time_of_day": detect_time_of_day_from_text(user_message),
                "relative_time": None,
            }
        )
    except Exception:
        time_hint = {
            "date_label": None,
            "time_of_day": detect_time_of_day_from_text(user_message),
            "relative_time": None,
        }
    if retrieval_service is not None:
        router_timing = retrieval_service._build_shortcut_timing(
            stage="router",
            branch="pre_retrieval_disabled",
            ready_event_type="decision",
        )
    else:
        router_timing = {
            "stage": "router",
            "branch": "pre_retrieval_disabled",
            "mode": "shortcut",
            "ready_event_type": "decision",
        }
    return RetrievalPipelineResult(
        used_retrieval=False,
        confirmed_snippets=[],
        router_output={
            "need_retrieval": False,
            "route": "pre_retrieval_disabled",
            "rewritten_query": "",
            "keywords": [],
            "time_hint": dict(time_hint or {}),
            "index_current_message": True,
            "reason": str(reason or "").strip(),
            "confidence": 1.0,
        },
        router_timing=router_timing,
        retrieval_result={
            "filtered_candidate_count": 0,
            "time_filter": {
                "date_label": None,
                "time_of_day": None,
                "relative_time": None,
                "matched": False,
            },
            "fused_hits": [],
            "memory_snippets": [],
        },
        verifier_output={
            "match_result": "skip",
            "match_score": 0.0,
            "need_retry": False,
            "selected_indexes": [],
            "retry_query": "",
            "retry_keywords": [],
            "retry_time_hint": None,
            "reason": str(reason or "").strip(),
        },
        verifier_timing={
            "mode": "skip",
            "attempts": [],
            "selected_attempt": None,
        },
    )


def run_pre_retrieval_pipeline(
    engine: Any,
    *,
    payload: dict[str, Any],
    profile_user_id: str,
    character_pack_id: str | None = None,
    user_message: str,
    now_ts: int,
    recent_raw: list[dict[str, Any]],
    recent_episodic_summaries: list[dict[str, Any]],
    recent_semantic_summaries: list[dict[str, Any]],
    current_user_source_id: str,
    verifier_debug_enabled: bool | None,
) -> RetrievalPipelineResult:
    if _memory_backend() == "memcore":
        return build_skipped_pre_retrieval_pipeline(
            engine,
            user_message=user_message,
            now_ts=now_ts,
            reason="memcore 模式下不再运行旧前置检索；由模型按需调用 memcore 记忆工具。",
            use_legacy_service=False,
        )
    if not resolve_pre_retrieval_enabled(engine, payload=payload):
        return build_skipped_pre_retrieval_pipeline(
            engine,
            user_message=user_message,
            now_ts=now_ts,
            reason="本轮已关闭前置检索，直接基于当前可见上下文回复。",
        )
    return engine._get_retrieval_service().run_explicit(
        profile_user_id=profile_user_id,
        character_pack_id=character_pack_id,
        original_query=user_message,
        now_ts=now_ts,
        exclude_source_ids=collect_visible_context_source_ids(
            recent_raw=recent_raw,
            recent_episodic_summaries=recent_episodic_summaries,
            recent_semantic_summaries=recent_semantic_summaries,
            extra_source_ids=[current_user_source_id],
        ),
        verifier_debug_enabled=verifier_debug_enabled,
        route="pre_retrieval",
    )


def should_index_user_record_in_vector(engine: Any, *, router_output: dict[str, Any]) -> bool:
    if not bool(router_output.get("need_retrieval")):
        return True
    normalized = engine._coerce_bool(router_output.get("index_current_message"))
    return True if normalized is None else bool(normalized)


def apply_user_vector_index_policy(
    engine: Any,
    *,
    user_record: dict[str, Any],
    router_output: dict[str, Any],
) -> dict[str, Any]:
    should_index = should_index_user_record_in_vector(engine, router_output=router_output)
    if bool(user_record.get("index_in_vector", True)) != should_index:
        user_record["index_in_vector"] = should_index
        engine.store.update_message_index_in_vector(user_record["source_id"], should_index)
    else:
        user_record["index_in_vector"] = should_index
    return user_record


def execute_retrieve_memory_tool(
    engine: Any,
    *,
    call: dict[str, Any],
    context: Any,
) -> ToolExecutionResult:
    query = normalize_text(str(call.get("query") or "")).strip()
    entity_anchors = [str(item).strip() for item in list(call.get("entity_anchors") or []) if str(item).strip()]
    topic_terms = [str(item).strip() for item in list(call.get("topic_terms") or []) if str(item).strip()]
    time_hint = call.get("time_hint") if isinstance(call.get("time_hint"), dict) else None
    source_layers = [str(item).strip() for item in list(call.get("source_layers") or []) if str(item).strip()]
    memory_facets = [str(item).strip() for item in list(call.get("memory_facets") or []) if str(item).strip()]
    about_roles = [str(item).strip() for item in list(call.get("about_roles") or []) if str(item).strip()]
    include_explicit = call.get("include_explicit") is True
    kind_patterns = [str(item).strip() for item in list(call.get("kind_patterns") or []) if str(item).strip()]
    current_user_record = (
        engine.store.get_message_by_source_id(context.current_user_source_id)
        if str(context.current_user_source_id or "").strip()
        else None
    )
    original_query = str((current_user_record or {}).get("content") or query)
    character_pack_id = str(getattr(context, "character_pack_id", "") or "").strip()
    extra_excludes = []
    visual_payload = context.visual_payload if isinstance(context.visual_payload, dict) else {}
    raw_extra_excludes = visual_payload.get("_memory_retrieval_exclude_source_ids")
    if isinstance(raw_extra_excludes, list):
        extra_excludes = [str(item).strip() for item in raw_extra_excludes if str(item).strip()]
    memcore_read_payload: dict[str, Any] | None = None
    if _memory_backend() == "memcore":
        exclude_source_ids = collect_visible_context_source_ids(
            recent_raw=[],
            recent_episodic_summaries=[],
            recent_semantic_summaries=[],
            extra_source_ids=[context.current_user_source_id, *extra_excludes],
        )
        memcore_read_payload = execute_memcore_retrieve_memory(
            engine,
            context=context,
            current_user_record=current_user_record,
            query=query,
            entity_anchors=entity_anchors,
            topic_terms=topic_terms,
            time_hint=time_hint,
            source_layers=source_layers,
            memory_facets=memory_facets,
            about_roles=about_roles,
            exclude_source_ids=exclude_source_ids,
            include_explicit=include_explicit,
            kind_patterns=kind_patterns,
        )
        if memcore_read_payload.get("ok"):
            snippets = [str(item).strip() for item in memcore_read_payload.get("snippets", []) if str(item).strip()]
            return _build_retrieve_memory_tool_result(
                query=query,
                entity_anchors=entity_anchors,
                topic_terms=topic_terms,
                time_hint=time_hint,
                source_layers=source_layers,
                memory_facets=memory_facets,
                about_roles=about_roles,
                snippets=snippets,
                retrieval_result=_build_memcore_retrieval_result(
                    snippets=snippets,
                    time_hint=time_hint,
                    source_layers=source_layers,
                    memory_facets=memory_facets,
                    about_roles=about_roles,
                    memcore_payload=memcore_read_payload,
                ),
                verifier_output=_build_memcore_verifier_output(snippets),
                verifier_timing={"mode": "memcore", "attempts": [], "selected_attempt": None},
                retrieval_backend="memcore",
                memcore_read=_sanitize_memcore_read_state(memcore_read_payload),
            )
        return _build_retrieve_memory_tool_result(
            query=query,
            entity_anchors=entity_anchors,
            topic_terms=topic_terms,
            time_hint=time_hint,
            source_layers=source_layers,
            memory_facets=memory_facets,
            about_roles=about_roles,
            snippets=[],
            retrieval_result=_build_memcore_retrieval_result(
                snippets=[],
                time_hint=time_hint,
                source_layers=source_layers,
                memory_facets=memory_facets,
                about_roles=about_roles,
                memcore_payload=memcore_read_payload,
            ),
            verifier_output=_build_memcore_verifier_output([]),
            verifier_timing={"mode": "memcore", "attempts": [], "selected_attempt": None},
            retrieval_backend="memcore",
            memcore_read=_sanitize_memcore_read_state(memcore_read_payload),
        )
    # Legacy/dual migration adapter. The model-facing contract remains the
    # MemCore schema; old retrieval receives only a conservative projection.
    keywords = list(dict.fromkeys([*entity_anchors, *topic_terms]))
    subject_scopes = list(
        dict.fromkeys(
            "other" if role in {"third_party", "external"} else role
            for role in about_roles
            if role in {"user", "assistant", "third_party", "external"}
        )
    )
    facet_to_legacy_category = {
        "profile": "personal_profile",
        "preference": "preference",
        "relationship": "relationship",
        "event": "life_event",
        "state": "emotion_state",
        "plan": "plan_goal",
    }
    categories = list(
        dict.fromkeys(facet_to_legacy_category[facet] for facet in memory_facets if facet in facet_to_legacy_category)
    )
    importance_min = None
    limit = None
    episodic_limit = max(1, int(getattr(config, "EPISODIC_VISIBLE_MAX", getattr(config, "RECENT_SUMMARY_LIMIT", 5))))
    semantic_limit = max(1, int(getattr(config, "SEMANTIC_VISIBLE_LIMIT", 3)))
    recent_raw = engine.store.get_unsummarized_messages(
        context.session_id,
        character_pack_id=character_pack_id,
    )
    recent_episodic_summaries = engine.store.get_visible_episodic_summaries(
        context.profile_user_id,
        limit=episodic_limit,
        character_pack_id=character_pack_id,
    )
    recent_semantic_summaries = (
        engine.store.get_recent_semantic_summaries(
            context.profile_user_id,
            limit=semantic_limit,
            character_pack_id=character_pack_id,
        )
        if bool(getattr(config, "ENABLE_SEMANTIC_MEMORY", True))
        else []
    )
    exclude_source_ids = collect_visible_context_source_ids(
        recent_raw=recent_raw,
        recent_episodic_summaries=recent_episodic_summaries,
        recent_semantic_summaries=recent_semantic_summaries,
        extra_source_ids=[context.current_user_source_id, *extra_excludes],
    )
    pipeline = engine._get_retrieval_service().run_explicit(
        profile_user_id=context.profile_user_id,
        character_pack_id=character_pack_id,
        original_query=original_query,
        now_ts=int(context.now_ts),
        query=query,
        keywords=keywords,
        time_hint=time_hint,
        source_layers=source_layers,
        subject_scopes=subject_scopes,
        categories=categories,
        importance_min=importance_min,
        limit=limit,
        exclude_source_ids=exclude_source_ids,
        verifier_debug_enabled=False,
        route="post_retrieval",
    )
    snippets = [str(item).strip() for item in pipeline.confirmed_snippets if str(item).strip()]
    result = _build_retrieve_memory_tool_result(
        query=query,
        entity_anchors=entity_anchors,
        topic_terms=topic_terms,
        time_hint=time_hint,
        source_layers=source_layers,
        memory_facets=memory_facets,
        about_roles=about_roles,
        snippets=snippets,
        retrieval_result=pipeline.retrieval_result,
        verifier_output=pipeline.verifier_output,
        verifier_timing=pipeline.verifier_timing,
        retrieval_backend="legacy",
        memcore_read=_sanitize_memcore_read_state(memcore_read_payload) if memcore_read_payload is not None else None,
    )
    memory_retrieval_state = result.state_updates["memory_retrieval"]
    shadow_payload = execute_memcore_shadow_retrieve(
        engine,
        call=call,
        context=context,
        current_user_record=current_user_record,
        query=query,
        entity_anchors=entity_anchors,
        topic_terms=topic_terms,
        time_hint=time_hint,
        source_layers=source_layers,
        memory_facets=memory_facets,
        about_roles=about_roles,
        exclude_source_ids=exclude_source_ids,
        legacy_snippets=snippets,
    )
    if shadow_payload is not None:
        memory_retrieval_state["memcore_shadow"] = shadow_payload
    return result


def _build_retrieve_memory_tool_result(
    *,
    query: str,
    entity_anchors: list[str],
    topic_terms: list[str],
    time_hint: dict[str, Any] | None,
    source_layers: list[str],
    memory_facets: list[str],
    about_roles: list[str],
    snippets: list[str],
    retrieval_result: dict[str, Any],
    verifier_output: dict[str, Any],
    verifier_timing: dict[str, Any],
    retrieval_backend: str,
    memcore_read: dict[str, Any] | None = None,
) -> ToolExecutionResult:
    raw_anchor_ids = [
        str(match.get("source_id") or "").strip()
        for match in list((memcore_read or {}).get("matches") or [])
        if isinstance(match, dict)
        and str(match.get("layer") or "") == "raw"
        and str(match.get("source_id") or "").strip()
    ]
    if snippets:
        followup_context = (
            "你刚刚主动检索了长期记忆。下面是可能回答主人问题的参考记忆：\n"
            + "\n\n".join(snippets)
            + (
                "\n\n可用于 read_memory_timeline 附近完整 turn 扩窗的 raw source_id：\n"
                + "\n".join(f"- {source_id}" for source_id in raw_anchor_ids)
                if raw_anchor_ids
                else ""
            )
            + "\n\n内容够用就直接自然回答；只有 raw 结果缺少前后语境时才扩窗，不要声称系统绝对证明了这些记忆。"
        )
    else:
        followup_context = (
            "你刚刚主动检索了长期记忆，但这次没有找到足以回答主人问题的相关记忆。"
            "请自然说明自己没有想起可靠线索，不要编造。"
        )
    memory_retrieval_state = {
        "tool_call": {
            "query": query,
            "entity_anchors": entity_anchors,
            "topic_terms": topic_terms,
            "time_hint": time_hint or {},
            "source_layers": source_layers,
            "memory_facets": memory_facets,
            "about_roles": about_roles,
        },
        "retrieval_result": retrieval_result,
        "retrieval_backend": retrieval_backend,
        "verifier_output": verifier_output,
        "verifier_timing": verifier_timing,
        "confirmed_snippets": snippets,
    }
    if memcore_read is not None:
        memory_retrieval_state["memcore_read"] = memcore_read
    return ToolExecutionResult(
        tool_type="retrieve_memory",
        raw_turns=[],
        stream_events=[],
        followup_context=followup_context,
        state_updates={"memory_retrieval": memory_retrieval_state},
    )


def _memory_backend() -> str:
    backend = str(getattr(config, "MEMORY_BACKEND", "memcore") or "memcore").strip().lower()
    return backend if backend in {"legacy", "dual", "memcore"} else "memcore"


def execute_memcore_retrieve_memory(
    engine: Any,
    *,
    context: Any,
    current_user_record: dict[str, Any] | None,
    query: str,
    entity_anchors: list[str],
    topic_terms: list[str],
    time_hint: dict[str, Any] | None,
    source_layers: list[str],
    memory_facets: list[str],
    about_roles: list[str],
    exclude_source_ids: list[str],
    include_explicit: bool = False,
    kind_patterns: list[str] | None = None,
) -> dict[str, Any]:
    manager = getattr(engine, "memcore_manager", None)
    if manager is None or not getattr(manager, "enabled", False):
        return {
            "operation": "retrieve_memory",
            "ok": False,
            "status": "unavailable",
            "reason": "memcore_manager_not_enabled",
            "snippet_count": 0,
            "snippet_hashes": [],
            "snippets": [],
        }
    if not getattr(manager, "available", False):
        status = manager.status() if hasattr(manager, "status") else {}
        return {
            "operation": "retrieve_memory",
            "ok": False,
            "status": "unavailable",
            "reason": str((status or {}).get("reason") or "memcore_unavailable"),
            "snippet_count": 0,
            "snippet_hashes": [],
            "snippets": [],
        }
    try:
        return manager.retrieve_memory(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            character_pack_id=str(getattr(context, "character_pack_id", "") or "").strip(),
            current_user_record=current_user_record,
            query=query,
            entity_anchors=entity_anchors,
            topic_terms=topic_terms,
            time_hint=time_hint,
            source_layers=source_layers,
            memory_facets=memory_facets,
            about_roles=about_roles,
            exclude_source_ids=exclude_source_ids,
            include_explicit=include_explicit,
            kind_patterns=list(kind_patterns or []),
        )
    except Exception as exc:
        return {
            "operation": "retrieve_memory",
            "ok": False,
            "status": "failed",
            "reason": str(exc) or exc.__class__.__name__,
            "snippet_count": 0,
            "snippet_hashes": [],
            "snippets": [],
        }


def _sanitize_memcore_read_state(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    out = dict(payload)
    out.pop("snippets", None)
    return out


def _build_memcore_retrieval_result(
    *,
    snippets: list[str],
    time_hint: dict[str, Any] | None,
    source_layers: list[str],
    memory_facets: list[str],
    about_roles: list[str],
    memcore_payload: dict[str, Any],
) -> dict[str, Any]:
    hint = time_hint if isinstance(time_hint, dict) else {}
    return {
        "backend": "memcore",
        "filtered_candidate_count": int(memcore_payload.get("snippet_count") or len(snippets)),
        "time_filter": {
            "date_label": hint.get("date_label"),
            "time_of_day": hint.get("time_of_day"),
            "relative_time": hint.get("relative_time"),
            "matched": bool(hint),
        },
        "precision_filters": {
            "source_layers": list(source_layers),
            "memory_facets": list(memory_facets),
            "about_roles": list(about_roles),
            "effective_filters": dict(memcore_payload.get("effective_filters") or {}),
            "candidate_counts": dict(memcore_payload.get("candidate_counts") or {}),
            "entity_filter_relaxed": bool(memcore_payload.get("entity_filter_relaxed")),
        },
        "fused_hits": list(memcore_payload.get("matches") or []),
        "memory_snippets": list(snippets),
    }


def _build_memcore_verifier_output(snippets: list[str]) -> dict[str, Any]:
    return {
        "match_result": "match" if snippets else "no_match",
        "match_score": 1.0 if snippets else 0.0,
        "need_retry": False,
        "selected_indexes": list(range(1, len(snippets) + 1)),
        "retry_query": "",
        "retry_keywords": [],
        "retry_time_hint": None,
        "reason": "memcore_retrieve_for_turn",
    }


def execute_memcore_shadow_retrieve(
    engine: Any,
    *,
    call: dict[str, Any],
    context: Any,
    current_user_record: dict[str, Any] | None,
    query: str,
    entity_anchors: list[str],
    topic_terms: list[str],
    time_hint: dict[str, Any] | None,
    source_layers: list[str],
    memory_facets: list[str],
    about_roles: list[str],
    exclude_source_ids: list[str],
    legacy_snippets: list[str],
) -> dict[str, Any] | None:
    if not bool(getattr(config, "MEMCORE_SHADOW_COMPARE", False)):
        return None
    manager = getattr(engine, "memcore_manager", None)
    if manager is None or not getattr(manager, "enabled", False):
        return build_shadow_payload(
            legacy_snippets=legacy_snippets,
            memcore_result={
                "operation": "shadow_retrieve_memory",
                "ok": False,
                "status": "unavailable",
                "reason": "memcore_manager_not_enabled",
                "snippet_count": 0,
                "snippet_hashes": [],
            },
        )
    if not getattr(manager, "available", False):
        status = manager.status() if hasattr(manager, "status") else {}
        return build_shadow_payload(
            legacy_snippets=legacy_snippets,
            memcore_result={
                "operation": "shadow_retrieve_memory",
                "ok": False,
                "status": "unavailable",
                "reason": str((status or {}).get("reason") or "memcore_unavailable"),
                "snippet_count": 0,
                "snippet_hashes": [],
            },
        )
    try:
        result = manager.shadow_retrieve_memory(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            character_pack_id=str(getattr(context, "character_pack_id", "") or "").strip(),
            current_user_record=current_user_record,
            query=query or str(call.get("query") or ""),
            entity_anchors=entity_anchors,
            topic_terms=topic_terms,
            time_hint=time_hint,
            source_layers=source_layers,
            memory_facets=memory_facets,
            about_roles=about_roles,
            exclude_source_ids=exclude_source_ids,
        )
    except Exception as exc:
        result = {
            "operation": "shadow_retrieve_memory",
            "ok": False,
            "status": "failed",
            "reason": str(exc) or exc.__class__.__name__,
            "snippet_count": 0,
            "snippet_hashes": [],
        }
    return build_shadow_payload(legacy_snippets=legacy_snippets, memcore_result=result)
