from __future__ import annotations

from typing import Any

import config

from .retrieval_types import RetrievalPipelineResult
from .text_utils import detect_time_of_day_from_text, normalize_text
from .tool_runtime import ToolExecutionResult, ToolFollowupEnvelope
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
    within_memory_id = str(call.get("within_memory_id") or "").strip()
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
            within_memory_id=within_memory_id,
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
                within_memory_id=within_memory_id,
                snippets=snippets,
                retrieval_result=_build_memcore_retrieval_result(
                    snippets=snippets,
                    time_hint=time_hint,
                    source_layers=source_layers,
                    memory_facets=memory_facets,
                    about_roles=about_roles,
                    memcore_payload=memcore_read_payload,
                ),
                retrieval_backend="memcore",
                memcore_read=_sanitize_memcore_read_state(memcore_read_payload),
                retrieval_diagnostics=_build_memcore_retrieval_diagnostics(memcore_read_payload),
            )
        return _build_retrieve_memory_tool_result(
            query=query,
            entity_anchors=entity_anchors,
            topic_terms=topic_terms,
            time_hint=time_hint,
            source_layers=source_layers,
            memory_facets=memory_facets,
            about_roles=about_roles,
            within_memory_id=within_memory_id,
            snippets=[],
            retrieval_result=_build_memcore_retrieval_result(
                snippets=[],
                time_hint=time_hint,
                source_layers=source_layers,
                memory_facets=memory_facets,
                about_roles=about_roles,
                memcore_payload=memcore_read_payload,
            ),
            retrieval_backend="memcore",
            memcore_read=_sanitize_memcore_read_state(memcore_read_payload),
            retrieval_diagnostics=_build_memcore_retrieval_diagnostics(memcore_read_payload),
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
        within_memory_id=within_memory_id,
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
        within_memory_id=within_memory_id,
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
    within_memory_id: str,
    snippets: list[str],
    retrieval_result: dict[str, Any],
    retrieval_backend: str,
    memcore_read: dict[str, Any] | None = None,
    retrieval_diagnostics: dict[str, Any] | None = None,
    verifier_output: dict[str, Any] | None = None,
    verifier_timing: dict[str, Any] | None = None,
) -> ToolExecutionResult:
    navigation = _memcore_retrieval_navigation(memcore_read)
    navigation_by_index: dict[int, dict[str, Any]] = {}
    for item in navigation:
        try:
            match_index = int(item.get("match_index") or 0)
        except (TypeError, ValueError):
            continue
        if match_index > 0:
            navigation_by_index[match_index] = item
    read_payload = memcore_read if isinstance(memcore_read, dict) else {}
    raw_anchors = [item for item in navigation if str(item.get("layer") or "") == "raw"]
    retrieval_status = str(read_payload.get("retrieval_status") or "").strip()
    if not retrieval_status:
        retrieval_status = (
            "found" if snippets else ("empty" if read_payload.get("ok") else str(read_payload.get("status") or ""))
        )
    candidate_counts = {
        str(key): int(value or 0) for key, value in dict(read_payload.get("candidate_counts") or {}).items()
    }
    candidate_text = ", ".join(f"{key}={value}" for key, value in candidate_counts.items()) or "未提供"
    lineage_scope = dict(read_payload.get("lineage_scope") or {})
    scoped_memory_id = str(lineage_scope.get("within_memory_id") or within_memory_id or "").strip()
    if scoped_memory_id:
        scope_text = (
            f"仅限 memory_id={scoped_memory_id} 的精确来源；"
            f"scope_status={str(lineage_scope.get('status') or 'requested')}；"
            f"source_candidates={int(lineage_scope.get('candidate_source_count') or 0)}"
        )
    else:
        scope_text = "当前授权记忆范围（未限定 within_memory_id）"
    truncated = bool(read_payload.get("truncated"))
    omitted_match_count = int(read_payload.get("omitted_match_count") or 0)
    status_lines = [
        "【MemCore 语义检索状态】",
        f"- status={retrieval_status or 'unknown'}；returned={len(snippets)}",
        f"- scope={scope_text}",
        f"- pre-score candidates: {candidate_text}",
    ]
    if bool(read_payload.get("entity_filter_relaxed")):
        status_lines.append("- entity filter: 严格实体候选为零后，仅实体条件被显式放宽；其余过滤仍生效。")
    if truncated:
        status_lines.append(f"- result coverage: partial；omitted_match_count={omitted_match_count}")
    elif snippets:
        status_lines.append("- result coverage: 本次返回 raw-first 排名靠前的完整命中单元，不代表全库只有这些记录。")
    if snippets:
        rendered_hits: list[str] = []
        for index, snippet in enumerate(snippets, start=1):
            nav = navigation_by_index.get(index, {})
            layer = str(nav.get("layer") or "").strip()
            memory_id = str(nav.get("memory_id") or "").strip()
            source_id = str(nav.get("source_id") or "").strip()
            attributes = [f"命中 {index}", layer or "layer=unknown"]
            if memory_id:
                attributes.append(f"memory_id={memory_id}")
            elif source_id:
                attributes.append(f"source_id={source_id}")
            time_label = _memory_navigation_time_label(nav)
            if time_label:
                attributes.append(f"time={time_label}")
            heading = "【" + "｜".join(attributes) + "】"
            rendered_hits.append(f"{heading}\n{snippet}")
        followup_context = (
            "\n".join(status_lines)
            + "\n\n下面是可能回答当前问题的真实记忆片段：\n"
            + "\n\n".join(rendered_hits)
            + (
                "\n\n可用于 read_memory_timeline 邻近完整 turn 扩窗的 raw 锚点：\n"
                + "\n".join(
                    "- "
                    + " ".join(
                        part
                        for part in (
                            f"source_id={str(item.get('source_id') or '')}",
                            f"turn_id={str(item.get('turn_id') or '-')}",
                            f"time={_memory_navigation_time_label(item) or str(item.get('timestamp') or '-')}",
                        )
                        if part
                    )
                    for item in raw_anchors
                )
                if raw_anchors
                else ""
            )
            + "\n\n可按实际缺口任选下一步，不要机械走固定流程：\n"
            "- 用户点名了具体日期/时段，或需要逐字核对原话时：优先用 read_memory_timeline 按日期精确读取原始对话核对，"
            "不要只凭检索命中的转述或片段下结论。\n"
            "- 当前片段已足够：直接自然回答。\n"
            "- summary/semantic_summary 只缺完整摘要正文：open_memory(view=content)。\n"
            "- 摘要主题正确、只缺某个具体细节：用该 memory_id 再调用 retrieve_memory，"
            '传 within_memory_id=<memory_id> 与 source_layers=["raw"]，只在它的精确来源内搜。\n'
            "- 确实需要整棵原始来源或逐条原证据：open_memory(view=sources)。\n"
            "- raw 已命中但缺相邻话轮：用 source_id 调 read_memory_timeline；跨会话锚点不可用时，"
            "改用命中给出的可读时间调用精确 time_range。\n"
            "- 已知精确时间直接用 read_memory_timeline；宽泛多日概览才用 browse_memory。\n"
            "已有有效命中后不要只换同义词反复检索；仍无明确证据就如实说明，不要猜。"
            "注意：命中不代表全库只有这些记录——角色标注等过滤条件可能把相关原文挡在候选池外；"
            "命中与用户说法明显对不上且手上有时间线索时，用 read_memory_timeline 兜底核对。"
        )
    else:
        if read_payload and not bool(read_payload.get("ok")):
            reason = str(read_payload.get("reason") or "memory_read_failed")
            followup_context = (
                "\n".join(status_lines) + f"\n\n这次记忆读取没有成功完成，reason={reason}。"
                "这是读取失败，不等于历史中没有记录；请说明目前无法可靠核实，不要编造。"
            )
        else:
            followup_context = (
                "\n".join(status_lines)
                + "\n\n本次有效检索没有找到匹配证据。请自然说明没有想起可靠线索，不要编造。"
                "若问题带明确日期/时段线索，先改用 read_memory_timeline 按日期精确读取原始对话核对"
                "（检索的过滤条件可能因角色标注等原因漏掉原文），仍无结果再如实说明。"
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
            "within_memory_id": within_memory_id,
        },
        "retrieval_result": retrieval_result,
        "retrieval_backend": retrieval_backend,
        "confirmed_snippets": snippets,
    }
    if retrieval_diagnostics is not None:
        memory_retrieval_state["retrieval_diagnostics"] = retrieval_diagnostics
    if verifier_output is not None:
        memory_retrieval_state["verifier_output"] = verifier_output
    if verifier_timing is not None:
        memory_retrieval_state["verifier_timing"] = verifier_timing
    if memcore_read is not None:
        memory_retrieval_state["memcore_read"] = memcore_read
    followup_envelope = None
    if retrieval_backend == "memcore" and memcore_read is not None:
        followup_envelope = ToolFollowupEnvelope(
            content=followup_context,
            producer_bounded=True,
            complete=not truncated,
            continuation=(
                {
                    "action": "refine_retrieve_memory",
                    "omitted_match_count": omitted_match_count,
                }
                if truncated
                else None
            ),
            diagnostics={
                "retrieval_status": retrieval_status,
                "returned_match_count": len(snippets),
                "candidate_counts": candidate_counts,
                "lineage_scope": lineage_scope,
            },
        )
    return ToolExecutionResult(
        tool_type="retrieve_memory",
        raw_turns=[],
        stream_events=[],
        followup_context=followup_context,
        followup_envelope=followup_envelope,
        state_updates={"memory_retrieval": memory_retrieval_state},
        trace_receipt=(
            dict((memcore_read or {}).get("receipt") or {})
            if isinstance((memcore_read or {}).get("receipt"), dict)
            else None
        ),
    )


def _memcore_retrieval_navigation(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Preserve package navigation, with a compatibility projection for older payloads."""

    if not isinstance(payload, dict):
        return []
    provided = [dict(item) for item in list(payload.get("navigation") or []) if isinstance(item, dict)]
    if provided:
        return provided
    out: list[dict[str, Any]] = []
    for index, match in enumerate(list(payload.get("matches") or []), start=1):
        if not isinstance(match, dict):
            continue
        source_id = str(match.get("source_id") or "").strip()
        if not source_id:
            continue
        layer = str(match.get("layer") or "raw").strip() or "raw"
        item: dict[str, Any] = {"match_index": index, "layer": layer}
        if layer in {"summary", "semantic_summary"}:
            item["memory_id"] = source_id
        else:
            item["source_id"] = source_id
            turn_id = str(match.get("turn_id") or "").strip()
            if turn_id:
                item["turn_id"] = turn_id
            try:
                timestamp = int(match.get("timestamp") or 0)
            except (TypeError, ValueError):
                timestamp = 0
            if timestamp > 0:
                item["timestamp"] = timestamp
        time_metadata = match.get("time")
        if isinstance(time_metadata, dict) and time_metadata:
            item["time"] = dict(time_metadata)
        out.append(item)
    return out


def _memory_navigation_time_label(item: dict[str, Any]) -> str:
    time_metadata = item.get("time") if isinstance(item.get("time"), dict) else {}
    at = str(time_metadata.get("at") or "").strip()
    if at:
        return at
    start_at = str(time_metadata.get("start_at") or "").strip()
    end_at = str(time_metadata.get("end_at") or "").strip()
    if start_at and end_at:
        return f"{start_at} -> {end_at}"
    if start_at or end_at:
        return start_at or end_at
    timestamp = item.get("timestamp")
    try:
        return str(int(timestamp)) if int(timestamp or 0) > 0 else ""
    except (TypeError, ValueError):
        return ""


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
    within_memory_id: str,
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
            within_memory_id=within_memory_id,
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
    candidate_counts = {
        str(key): int(value or 0) for key, value in dict(memcore_payload.get("candidate_counts") or {}).items()
    }
    return {
        "backend": "memcore",
        "filtered_candidate_count": sum(value for key, value in candidate_counts.items() if key.endswith("_effective")),
        "returned_match_count": len(snippets),
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
            "candidate_counts": candidate_counts,
            "entity_filter_relaxed": bool(memcore_payload.get("entity_filter_relaxed")),
            "lineage_scope": dict(memcore_payload.get("lineage_scope") or {}),
        },
        "fused_hits": list(memcore_payload.get("matches") or []),
        "memory_snippets": list(snippets),
    }


def _build_memcore_retrieval_diagnostics(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "retrieval_status": str(payload.get("retrieval_status") or payload.get("status") or ""),
        "effective_filters": dict(payload.get("effective_filters") or {}),
        "candidate_counts": dict(payload.get("candidate_counts") or {}),
        "lineage_scope": dict(payload.get("lineage_scope") or {}),
        "returned_match_count": int(payload.get("returned_match_count") or payload.get("snippet_count") or 0),
        "entity_filter_relaxed": bool(payload.get("entity_filter_relaxed")),
        "relaxation_steps": list(payload.get("relaxation_steps") or []),
        "truncated": bool(payload.get("truncated")),
        "omitted_match_count": int(payload.get("omitted_match_count") or 0),
        "latency_ms": int(payload.get("latency_ms") or 0),
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
    within_memory_id: str,
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
            within_memory_id=within_memory_id,
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
