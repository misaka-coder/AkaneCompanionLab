from __future__ import annotations

from typing import Any

import config

from .retrieval_types import RetrievalPipelineResult
from .text_utils import detect_time_of_day_from_text, normalize_text
from .tool_runtime import ToolExecutionResult


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
) -> RetrievalPipelineResult:
    retrieval_service = engine._get_retrieval_service()
    try:
        time_hint = retrieval_service._extract_time_hint(user_message=user_message, now_ts=now_ts)
    except Exception:
        time_hint = {
            "date_label": None,
            "time_of_day": detect_time_of_day_from_text(user_message),
            "relative_time": None,
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
        router_timing=retrieval_service._build_shortcut_timing(
            stage="router",
            branch="pre_retrieval_disabled",
            ready_event_type="decision",
        ),
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
    keywords = [str(item).strip() for item in list(call.get("keywords") or []) if str(item).strip()]
    time_hint = call.get("time_hint") if isinstance(call.get("time_hint"), dict) else None
    source_layers = [str(item).strip() for item in list(call.get("source_layers") or []) if str(item).strip()]
    subject_scopes = [str(item).strip() for item in list(call.get("subject_scopes") or []) if str(item).strip()]
    categories = [str(item).strip() for item in list(call.get("categories") or []) if str(item).strip()]
    importance_min = call.get("importance_min")
    limit = call.get("limit")
    current_user_record = (
        engine.store.get_message_by_source_id(context.current_user_source_id)
        if str(context.current_user_source_id or "").strip()
        else None
    )
    original_query = str((current_user_record or {}).get("content") or query)
    episodic_limit = max(1, int(getattr(config, "EPISODIC_VISIBLE_MAX", getattr(config, "RECENT_SUMMARY_LIMIT", 5))))
    semantic_limit = max(1, int(getattr(config, "SEMANTIC_VISIBLE_LIMIT", 3)))
    character_pack_id = str(getattr(context, "character_pack_id", "") or "").strip()
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
    extra_excludes = []
    visual_payload = context.visual_payload if isinstance(context.visual_payload, dict) else {}
    raw_extra_excludes = visual_payload.get("_memory_retrieval_exclude_source_ids")
    if isinstance(raw_extra_excludes, list):
        extra_excludes = [str(item).strip() for item in raw_extra_excludes if str(item).strip()]
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
    if snippets:
        followup_context = (
            "你刚刚主动检索了长期记忆。下面是可能回答主人问题的参考记忆：\n"
            + "\n\n".join(snippets)
            + "\n\n请基于这些参考记忆自然回应；不要声称系统绝对证明了这些记忆。"
        )
    else:
        followup_context = (
            "你刚刚主动检索了长期记忆，但这次没有找到足以回答主人问题的相关记忆。"
            "请自然说明自己没有想起可靠线索，不要编造。"
        )
    return ToolExecutionResult(
        tool_type="retrieve_memory",
        raw_turns=[],
        stream_events=[],
        followup_context=followup_context,
        state_updates={
            "memory_retrieval": {
                "tool_call": {
                    "query": query,
                    "keywords": keywords,
                    "time_hint": time_hint or {},
                    "source_layers": source_layers,
                    "subject_scopes": subject_scopes,
                    "categories": categories,
                    "importance_min": importance_min,
                    "limit": limit,
                },
                "retrieval_result": pipeline.retrieval_result,
                "verifier_output": pipeline.verifier_output,
                "verifier_timing": pipeline.verifier_timing,
                "confirmed_snippets": snippets,
            }
        },
    )
