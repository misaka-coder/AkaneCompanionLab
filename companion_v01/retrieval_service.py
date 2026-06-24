from __future__ import annotations

import json
import random
import re
from datetime import datetime, timedelta
from typing import Any

import config

from .llm_runtime import LLMRuntime
from .memory_rendering import render_memory_mood_line, render_relative_time_anchor_line
from .prompt_builder import PromptBuilder
from .retrieval_types import RetrievalPipelineResult
from .store import MemoryStore, normalize_character_pack_id
from .text_utils import (
    detect_time_of_day_from_text,
    extract_semantic_tags,
    normalize_text,
    parse_joined_tags,
    render_chat_line,
    render_chat_timeline,
    timestamp_to_datetime_label,
)
from .vector_store import VectorStore, fuse_with_rrf

HARD_ROUTE_WORDS = [
    "记得",
    "之前",
    "上次",
    "上回",
    "回忆",
    "不是说过",
    "说过什么",
    "聊过什么",
    "提过什么",
    "讲过什么",
    "叫什么来着",
    "叫啥来着",
]
PAST_MEMORY_REFERENCE_MARKERS = [
    "以前",
    "曾经",
    "过去",
    "当时",
    "那时候",
    "那会儿",
    "那天",
    "那次",
    "前几天",
    "前阵子",
    "前段时间",
    "昨天",
    "前天",
]
PAST_MEMORY_FACT_MARKERS = [
    "说过",
    "聊过",
    "谈过",
    "提过",
    "讲过",
    "约定过",
    "答应过",
    "约好的",
    "定好的",
    "定的计划",
    "之前的计划",
    "旧计划",
    "叫什么",
    "叫啥",
    "来着",
]
PAST_MEMORY_RECALL_MARKERS = [
    "说过",
    "聊过",
    "谈过",
    "提过",
    "讲过",
    "约定过",
    "答应过",
]
MEMORY_RECALL_INTENT_MARKERS = [
    "记得",
    "记不清",
    "想不起来",
    "想想",
    "再想想",
    "回忆",
    "回想",
    "想起来",
    "发生了什么",
    "发生过什么",
    "聊了什么",
    "聊过什么",
    "说了什么",
    "说过什么",
    "那件事",
    "事情",
]
TIME_OF_DAY_QUERY_LABELS = {
    "morning": "上午",
    "afternoon": "下午",
    "night": "晚上",
    "midnight": "凌晨",
}
RAW_INDEX_SKIP_MARKERS = [
    "还记得",
    "记不记得",
    "之前说过什么",
    "上次说过什么",
    "我说过什么",
    "我刚刚说过什么",
    "测试一下",
    "测试你",
    "考考你",
]
TASK_LIKE_ROUTER_QUERY_MARKERS = [
    "请查找",
    "需要确认",
    "请确认",
    "请判断",
    "判断一下",
    "用于检索",
    "对话上下文",
    "紧邻",
    "上一句",
    "上一条",
    "哪一句",
    "是在回应",
    "用户说",
    "用户提到",
    "模型",
    "路由",
]
PRECISION_SOURCE_LAYERS = {"raw", "summary", "semantic_summary"}
PRECISION_SUBJECT_SCOPES = {"user", "assistant", "other"}
PRECISION_CATEGORIES = {
    "casual",
    "preference",
    "personal_profile",
    "plan_goal",
    "project_work",
    "relationship",
    "emotion_state",
    "life_event",
    "memory_query",
    "system_meta",
}
PRECISION_SOURCE_LAYER_ALIASES = {
    "semantic": "semantic_summary",
    "semantic_memory": "semantic_summary",
    "long_term": "semantic_summary",
    "longterm": "semantic_summary",
    "长期记忆": "semantic_summary",
    "摘要": "summary",
    "原始": "raw",
    "原始对话": "raw",
}
PRECISION_SUBJECT_SCOPE_ALIASES = {
    "用户": "user",
    "玩家": "user",
    "主人": "user",
    "我": "user",
    "assistant": "assistant",
    "akane": "assistant",
    "角色": "assistant",
    "助手": "assistant",
    "其它": "other",
    "其他": "other",
    "别人": "other",
    "他人": "other",
    "项目": "other",
    "话题": "other",
}
PRECISION_CATEGORY_ALIASES = {
    "闲聊": "casual",
    "偏好": "preference",
    "喜好": "preference",
    "个人资料": "personal_profile",
    "计划": "plan_goal",
    "目标": "plan_goal",
    "项目": "project_work",
    "工作": "project_work",
    "关系": "relationship",
    "情绪": "emotion_state",
    "状态": "emotion_state",
    "生活事件": "life_event",
    "回忆": "memory_query",
    "记忆查询": "memory_query",
    "系统": "system_meta",
}


class RetrievalService:
    def __init__(
        self,
        *,
        store: MemoryStore,
        vector_store: VectorStore,
        llm: LLMRuntime,
        prompt_builder: PromptBuilder,
    ):
        self.store = store
        self.vector_store = vector_store
        self.llm = llm
        self.prompt_builder = prompt_builder

    def run(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str | None = None,
        user_message: str,
        now_ts: int,
        current_user_record: dict[str, Any],
        recent_raw: list[dict[str, Any]],
        exclude_source_ids: list[str],
        router_debug_enabled: bool | None = None,
        verifier_debug_enabled: bool | None = None,
    ) -> RetrievalPipelineResult:
        router_output, router_timing = self._build_router_output(
            profile_user_id=profile_user_id,
            user_message=user_message,
            current_user_record=current_user_record,
            recent_raw=recent_raw,
            now_ts=now_ts,
            router_debug_enabled=router_debug_enabled,
        )

        retrieval_result = {
            "filtered_candidate_count": 0,
            "time_filter": {
                "date_label": None,
                "time_of_day": None,
                "relative_time": None,
                "matched": False,
            },
            "fused_hits": [],
            "memory_snippets": [],
        }
        verifier_output = {
            "match_result": "skip",
            "match_score": 0.0,
            "need_retry": False,
            "selected_indexes": [],
            "retry_query": "",
            "retry_keywords": [],
            "retry_time_hint": None,
            "reason": "本轮未触发记忆检索。",
        }
        confirmed_snippets: list[str] = []
        verifier_timing: dict[str, Any] = {
            "mode": "skip",
            "attempts": [],
            "selected_attempt": None,
        }

        if router_output.get("need_retrieval"):
            retrieval_result, verifier_output, confirmed_snippets, verifier_timing = self._run_retrieval_chain(
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                original_query=user_message,
                now_ts=now_ts,
                router_output=router_output,
                exclude_source_ids=exclude_source_ids,
                verifier_debug_enabled=verifier_debug_enabled,
            )

        return RetrievalPipelineResult(
            used_retrieval=bool(router_output.get("need_retrieval")),
            confirmed_snippets=confirmed_snippets,
            router_output=router_output,
            router_timing=router_timing,
            retrieval_result=retrieval_result,
            verifier_output=verifier_output,
            verifier_timing=verifier_timing,
        )

    def run_explicit(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str | None = None,
        original_query: str,
        now_ts: int,
        query: str | None = None,
        keywords: list[str] | None = None,
        time_hint: dict[str, Any] | None = None,
        source_layers: list[str] | None = None,
        subject_scopes: list[str] | None = None,
        categories: list[str] | None = None,
        importance_min: float | int | str | None = None,
        limit: int | None = None,
        exclude_source_ids: list[str] | None = None,
        verifier_debug_enabled: bool | None = None,
        route: str = "memory_search",
    ) -> RetrievalPipelineResult:
        fallback_keywords = extract_semantic_tags(original_query, limit=6)
        normalized_keywords = self._normalize_keywords(
            list(keywords or []),
            fallback=fallback_keywords,
        )
        base_time_hint = self._extract_time_hint(user_message=original_query, now_ts=now_ts)
        normalized_time_hint = self._normalize_time_hint(time_hint, default=base_time_hint)
        normalized_query = self._normalize_rewritten_query(
            query or original_query,
            fallback_query=original_query,
            keywords=normalized_keywords,
        )
        precision_filters = self._normalize_precision_filters(
            source_layers=source_layers,
            subject_scopes=subject_scopes,
            categories=categories,
            importance_min=importance_min,
            limit=limit,
        )
        router_output = self._finalize_router_output(
            need_retrieval=True,
            route=route or "memory_search",
            rewritten_query=normalized_query,
            keywords=normalized_keywords,
            time_hint=normalized_time_hint,
            index_current_message=self._should_index_current_message_default(original_query),
            debug_enabled=False,
        )
        router_output["precision_filters"] = precision_filters
        retrieval_result, verifier_output, confirmed_snippets, verifier_timing = self._run_retrieval_chain(
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            original_query=original_query,
            now_ts=now_ts,
            router_output=router_output,
            exclude_source_ids=list(exclude_source_ids or []),
            verifier_debug_enabled=verifier_debug_enabled,
        )
        return RetrievalPipelineResult(
            used_retrieval=True,
            confirmed_snippets=confirmed_snippets,
            router_output=router_output,
            router_timing=self._build_shortcut_timing(
                stage="router",
                branch="explicit_query",
                ready_event_type="query",
            ),
            retrieval_result=retrieval_result,
            verifier_output=verifier_output,
            verifier_timing=verifier_timing,
        )

    def _build_router_output(
        self,
        *,
        profile_user_id: str,
        user_message: str,
        current_user_record: dict[str, Any],
        recent_raw: list[dict[str, Any]],
        now_ts: int,
        router_debug_enabled: bool | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        del profile_user_id
        time_hint = self._extract_time_hint(user_message=user_message, now_ts=now_ts)
        fallback_keywords = extract_semantic_tags(user_message, limit=6)
        default_index_current_message = self._should_index_current_message_default(user_message)
        debug_enabled = bool(
            getattr(config, "ROUTER_DEBUG", False)
            if router_debug_enabled is None
            else router_debug_enabled
        )
        hard_route = self._should_hard_route_memory_search(user_message)
        hard_route_fallback_query = self._build_memory_search_fallback_query(
            user_message=user_message,
            time_hint=time_hint,
        )
        hard_route_fallback_keywords = self._build_memory_search_fallback_keywords(
            query=hard_route_fallback_query,
            user_message=user_message,
            time_hint=time_hint,
            fallback_keywords=fallback_keywords,
        )

        if not hard_route and random.random() < float(getattr(config, "DRIFT_PROBABILITY", 0.20)):
            return (
                self._finalize_router_output(
                    need_retrieval=True,
                    route="surprise_memory",
                    rewritten_query=user_message,
                    keywords=fallback_keywords,
                    time_hint=time_hint,
                    index_current_message=True,
                    debug_enabled=debug_enabled,
                    reason=self.prompt_builder.persona.surprise_memory_reason,
                    confidence=0.62,
                ),
                self._build_shortcut_timing(
                    stage="router",
                    branch="drift_shortcut",
                    ready_event_type="decision",
                ),
            )

        recent_context = self._build_router_recent_context(
            recent_raw=recent_raw,
            current_user_record=current_user_record,
        )[-6:]
        context_text = render_chat_timeline(recent_context)
        current_message_text = self._render_current_message_line(
            current_user_record=current_user_record,
        )
        fallback = self._finalize_router_output(
            need_retrieval=hard_route,
            route="memory_search" if hard_route else "direct_answer",
            rewritten_query=hard_route_fallback_query if hard_route else "",
            keywords=hard_route_fallback_keywords if hard_route else [],
            time_hint=time_hint,
            index_current_message=default_index_current_message if hard_route else True,
            debug_enabled=debug_enabled,
            reason=(
                "用户明确在询问过去记忆或共同经历，外层规则强制进入记忆检索。"
                if hard_route
                else "这是即时闲聊，不依赖历史事实。"
            ),
            confidence=1.0 if hard_route else 0.55,
        )
        system_prompt, user_prompt = self.prompt_builder.build_router_prompts(
            now_ts=now_ts,
            recent_context_text=context_text,
            current_message_text=current_message_text,
            debug_enabled=debug_enabled,
            forced_retrieval_hint=(
                "当前消息包含明显的回忆/过去事实请求；必须输出 need_retrieval=true。"
                if hard_route
                else ""
            ),
        )

        router_state = {
            "need_retrieval": None,
            "route": "direct_answer",
            "rewritten_query": "",
            "keywords": [],
            "time_hint": time_hint,
            "index_current_message": True,
            "reason": "",
            "confidence": 0.0,
        }

        ndjson_result = self.llm.call_aux_ndjson(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            on_event=lambda event: self._apply_router_ndjson_event(
                router_state,
                event,
                debug_enabled=debug_enabled,
            ),
            temperature=0.1,
            prompt_cache_key="aux:router",
        )
        if not ndjson_result.events or router_state.get("need_retrieval") is None:
            return dict(fallback), self._summarize_ndjson_timing(
                stage="router",
                ndjson_result=ndjson_result,
                ready_event_type=None,
                branch="hard_route_fallback" if hard_route else "fallback",
            )

        need_retrieval = bool(router_state.get("need_retrieval")) or hard_route
        route = str(router_state.get("route") or ("memory_search" if need_retrieval else "direct_answer"))
        rewritten_query = str(router_state.get("rewritten_query") or "")
        keywords = list(router_state.get("keywords") or [])
        normalized_hint = self._normalize_time_hint(router_state.get("time_hint"), default=time_hint)

        if need_retrieval and not keywords:
            keywords = hard_route_fallback_keywords if hard_route else fallback_keywords
        if need_retrieval and not rewritten_query:
            rewritten_query = hard_route_fallback_query if hard_route else user_message
        rewritten_query = self._normalize_rewritten_query(
            rewritten_query,
            fallback_query=hard_route_fallback_query if hard_route else user_message,
            keywords=keywords,
        )

        return (
            self._finalize_router_output(
                need_retrieval=need_retrieval,
                route=route,
                rewritten_query=rewritten_query,
                keywords=keywords,
                time_hint=normalized_hint,
                index_current_message=self._coerce_bool(router_state.get("index_current_message")),
                debug_enabled=debug_enabled,
                reason=str(router_state.get("reason") or ""),
                confidence=router_state.get("confidence", 0.0),
            ),
            self._summarize_ndjson_timing(
                stage="router",
                ndjson_result=ndjson_result,
                ready_event_type="query" if need_retrieval else "decision",
                branch="hard_route_ndjson" if hard_route else "ndjson",
            ),
        )

    def _run_retrieval_chain(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str | None = None,
        original_query: str,
        now_ts: int,
        router_output: dict[str, Any],
        exclude_source_ids: list[str],
        verifier_debug_enabled: bool | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], list[str], dict[str, Any]]:
        current_router = dict(router_output)
        last_result: dict[str, Any] | None = None
        last_verifier: dict[str, Any] | None = None
        accumulated_exclude_ids = {
            str(source_id).strip()
            for source_id in (exclude_source_ids or [])
            if str(source_id).strip()
        }
        debug_enabled = bool(
            getattr(config, "VERIFIER_DEBUG", False)
            if verifier_debug_enabled is None
            else verifier_debug_enabled
        )
        attempts_debug: list[dict[str, Any]] = []
        for attempt in range(2):
            precision_filters = self._normalize_precision_filters(current_router.get("precision_filters"))
            excluded_before_attempt = sorted(accumulated_exclude_ids)
            retrieval_result = self._retrieve_memories(
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                query=current_router.get("rewritten_query") or original_query,
                keywords=list(current_router.get("keywords") or []),
                time_hint=self._normalize_time_hint(current_router.get("time_hint")),
                source_layers=precision_filters.get("source_layers"),
                subject_scopes=precision_filters.get("subject_scopes"),
                categories=precision_filters.get("categories"),
                importance_min=precision_filters.get("importance_min"),
                limit=precision_filters.get("limit"),
                exclude_source_ids=list(accumulated_exclude_ids),
            )
            verifier_output, verifier_attempt_timing = self._verify_memories(
                now_ts=now_ts,
                original_query=original_query,
                router_output=current_router,
                retrieval_result=retrieval_result,
                verifier_debug_enabled=debug_enabled,
            )
            last_result = retrieval_result
            last_verifier = verifier_output
            retrieved_source_ids = [
                str(hit.get("source_id") or "").strip()
                for hit in retrieval_result.get("fused_hits", [])
                if str(hit.get("source_id") or "").strip()
            ]
            attempts_debug.append(
                {
                    "attempt": attempt + 1,
                    "query": str(current_router.get("rewritten_query") or original_query),
                    "keywords": list(current_router.get("keywords") or []),
                    "time_hint": self._normalize_time_hint(current_router.get("time_hint")),
                    "precision_filters": precision_filters,
                    "excluded_source_ids": excluded_before_attempt,
                    "retrieved_source_ids": retrieved_source_ids,
                    "selected_indexes": list(verifier_output.get("selected_indexes") or []),
                    "filtered_candidate_count": retrieval_result.get("filtered_candidate_count", 0),
                    "verifier_timing": verifier_attempt_timing,
                }
            )
            if verifier_output.get("match_result") == "match" or not verifier_output.get("need_retry"):
                confirmed_snippets = []
                if verifier_output.get("match_result") == "match":
                    confirmed_snippets = self._select_snippets_by_indexes(
                        retrieval_result.get("memory_snippets") or [],
                        verifier_output.get("selected_indexes"),
                    )
                    if not confirmed_snippets:
                        confirmed_snippets = list(retrieval_result.get("memory_snippets") or [])
                return retrieval_result, verifier_output, confirmed_snippets, {
                    "mode": "ndjson",
                    "attempts": attempts_debug,
                    "selected_attempt": attempt + 1,
                }

            accumulated_exclude_ids.update(retrieved_source_ids)
            current_router = {
                "need_retrieval": True,
                "route": "memory_search",
                "keywords": verifier_output.get("retry_keywords") or extract_semantic_tags(original_query, limit=6),
                "rewritten_query": self._normalize_rewritten_query(
                    verifier_output.get("retry_query") or original_query,
                    fallback_query=original_query,
                    keywords=list(verifier_output.get("retry_keywords") or []),
                ),
                "time_hint": self._normalize_time_hint(
                    verifier_output.get("retry_time_hint"),
                    default=current_router.get("time_hint"),
                ),
                "precision_filters": precision_filters,
                "reason": verifier_output.get("reason", ""),
                "confidence": 0.5,
            }

        return (
            last_result
            or {
                "filtered_candidate_count": 0,
                "time_filter": {"date_label": None, "time_of_day": None, "relative_time": None, "matched": False},
                "fused_hits": [],
                "memory_snippets": [],
            },
            last_verifier
            or self._finalize_verifier_output(
                match_result="mismatch",
                need_retry=False,
                retry_query="",
                retry_keywords=[],
                retry_time_hint=None,
                selected_indexes=[],
                debug_enabled=debug_enabled,
                reason="检索未能稳定命中可用记忆。",
                match_score=0.0,
            ),
            [],
            {
                "mode": "ndjson",
                "attempts": attempts_debug,
                "selected_attempt": len(attempts_debug) if attempts_debug else None,
            },
        )

    def _retrieve_memories(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str | None = None,
        query: str,
        keywords: list[str],
        time_hint: dict[str, Any] | None,
        source_layers: list[str] | None = None,
        subject_scopes: list[str] | None = None,
        categories: list[str] | None = None,
        importance_min: float | int | str | None = None,
        limit: int | None = None,
        exclude_source_ids: list[str],
    ) -> dict[str, Any]:
        normalized_time_hint = self._normalize_time_hint(time_hint)
        precision_filters = self._normalize_precision_filters(
            source_layers=source_layers,
            subject_scopes=subject_scopes,
            categories=categories,
            importance_min=importance_min,
            limit=limit,
        )
        retrieval_limit = int(precision_filters.get("limit") or 4)
        candidate_pool_size = max(10, min(80, retrieval_limit * 10))
        excluded_ids = {
            str(source_id).strip()
            for source_id in (exclude_source_ids or [])
            if str(source_id).strip()
        }
        semantic_hits = [
            hit
            for hit in self.vector_store.semantic_search(
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                query_text=query,
                time_hint=normalized_time_hint,
                n_results=candidate_pool_size,
            )
            if str(hit.get("source_id") or "").strip() not in excluded_ids
        ]
        keyword_hits = [
            hit
            for hit in self.vector_store.keyword_search(
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                query_text=query,
                keywords=keywords,
                time_hint=normalized_time_hint,
                n_results=candidate_pool_size,
            )
            if str(hit.get("source_id") or "").strip() not in excluded_ids
        ]
        semantic_hits = self._filter_hits_by_character_pack_id(
            semantic_hits,
            character_pack_id=character_pack_id,
        )
        keyword_hits = self._filter_hits_by_character_pack_id(
            keyword_hits,
            character_pack_id=character_pack_id,
        )
        semantic_hits, keyword_hits, precision_debug = self._apply_precision_filter_relaxation(
            semantic_hits=semantic_hits,
            keyword_hits=keyword_hits,
            precision_filters=precision_filters,
        )
        fused_hits = fuse_with_rrf(semantic_hits, keyword_hits)
        fused_hits = self._rerank_fused_hits(
            query=query,
            keywords=keywords,
            fused_hits=fused_hits,
        )[:retrieval_limit]
        memory_snippets = self._build_memory_snippets(fused_hits)
        return {
            "filtered_candidate_count": precision_debug["candidate_count_after"],
            "time_filter": {
                "date_label": normalized_time_hint.get("date_label"),
                "time_of_day": normalized_time_hint.get("time_of_day"),
                "relative_time": normalized_time_hint.get("relative_time"),
                "matched": bool(normalized_time_hint.get("date_label") or normalized_time_hint.get("time_of_day")),
            },
            "precision_filters": precision_debug,
            "fused_hits": [
                {
                    "source_id": hit["source_id"],
                    "dual_hit": hit["dual_hit"],
                    "entry_type": hit["entry_type"],
                    "rrf_score": round(hit["rrf_score"], 4),
                    "semantic_score": round(hit["semantic_score"], 4),
                    "tag_score": round(hit["tag_score"], 4),
                    "rank_bonus": round(hit.get("rank_bonus", 0.0), 4),
                    "final_score": round(hit.get("final_score", hit["rrf_score"]), 4),
                    "content": hit["document"][:120],
                }
                for hit in fused_hits
            ],
            "memory_snippets": memory_snippets,
        }

    def _filter_hits_by_character_pack_id(
        self,
        hits: list[dict[str, Any]],
        *,
        character_pack_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if character_pack_id is None:
            return hits

        expected_pack_id = normalize_character_pack_id(character_pack_id)
        getter = getattr(self.store, "get_record_by_source_id", None)
        filtered_hits: list[dict[str, Any]] = []
        for hit in hits:
            source_id = str(hit.get("source_id") or "").strip()
            if not source_id:
                continue
            if callable(getter):
                try:
                    record = getter(source_id)
                except Exception:
                    record = None
                if isinstance(record, dict):
                    record_pack_id = normalize_character_pack_id(record.get("character_pack_id"))
                    if record_pack_id == expected_pack_id:
                        filtered_hits.append(hit)
                    continue

            metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
            metadata_pack_id = normalize_character_pack_id(metadata.get("character_pack_id"))
            if metadata_pack_id == expected_pack_id:
                filtered_hits.append(hit)
        return filtered_hits

    def _apply_precision_filter_relaxation(
        self,
        *,
        semantic_hits: list[dict[str, Any]],
        keyword_hits: list[dict[str, Any]],
        precision_filters: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        requested_filters = self._normalize_precision_filters(precision_filters)
        candidate_hits = self._unique_hits_by_source_id([*semantic_hits, *keyword_hits])
        candidate_count_before = len(candidate_hits)
        target_count = max(1, min(int(requested_filters.get("limit") or 4), 4))
        stages = self._build_precision_filter_stages(requested_filters)

        selected_stage = stages[-1]
        selected_candidates: list[dict[str, Any]] = []
        for stage in stages:
            matching_candidates = self._filter_hits_by_precision(candidate_hits, stage["filters"])
            selected_stage = stage
            selected_candidates = matching_candidates
            if len(matching_candidates) >= target_count:
                break

        allowed_source_ids = {
            str(hit.get("source_id") or "").strip()
            for hit in selected_candidates
            if str(hit.get("source_id") or "").strip()
        }
        filtered_semantic_hits = [
            hit
            for hit in semantic_hits
            if str(hit.get("source_id") or "").strip() in allowed_source_ids
        ]
        filtered_keyword_hits = [
            hit
            for hit in keyword_hits
            if str(hit.get("source_id") or "").strip() in allowed_source_ids
        ]
        applied_filters = self._precision_filter_names(selected_stage["filters"])
        return filtered_semantic_hits, filtered_keyword_hits, {
            "requested_filters": {
                "source_layers": list(requested_filters.get("source_layers") or []),
                "subject_scopes": list(requested_filters.get("subject_scopes") or []),
                "categories": list(requested_filters.get("categories") or []),
                "importance_min": requested_filters.get("importance_min"),
                "limit": int(requested_filters.get("limit") or 4),
            },
            "applied_filters": applied_filters,
            "relaxed_filters": list(selected_stage.get("relaxed_filters") or []),
            "relaxation_stage": str(selected_stage.get("name") or "strict"),
            "candidate_count_before": candidate_count_before,
            "candidate_count_after": len(selected_candidates),
            "target_count": target_count,
        }

    def _build_precision_filter_stages(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        current = {
            "source_layers": list(filters.get("source_layers") or []),
            "subject_scopes": list(filters.get("subject_scopes") or []),
            "categories": list(filters.get("categories") or []),
            "importance_min": filters.get("importance_min"),
            "limit": int(filters.get("limit") or 4),
        }
        stages: list[dict[str, Any]] = []
        relaxed_filters: list[str] = []

        def add_stage(name: str) -> None:
            stages.append(
                {
                    "name": name,
                    "filters": {
                        "source_layers": list(current["source_layers"]),
                        "subject_scopes": list(current["subject_scopes"]),
                        "categories": list(current["categories"]),
                        "importance_min": current["importance_min"],
                        "limit": current["limit"],
                    },
                    "relaxed_filters": list(relaxed_filters),
                }
            )

        if not self._precision_filter_names(current):
            add_stage("no_precision_filters")
            return stages

        add_stage("strict")
        if current["importance_min"] is not None:
            current["importance_min"] = None
            relaxed_filters.append("importance_min")
            add_stage("drop_importance_min")
        if current["categories"]:
            current["categories"] = []
            relaxed_filters.append("categories")
            add_stage("drop_categories")
        if current["subject_scopes"]:
            current["subject_scopes"] = []
            relaxed_filters.append("subject_scopes")
            add_stage("drop_subject_scopes")
        if current["source_layers"]:
            current["source_layers"] = []
            relaxed_filters.append("source_layers")
            add_stage("no_precision_filters")
        return stages

    def _unique_hits_by_source_id(self, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique_hits: list[dict[str, Any]] = []
        seen: set[str] = set()
        for hit in hits:
            source_id = str(hit.get("source_id") or "").strip()
            if not source_id or source_id in seen:
                continue
            seen.add(source_id)
            unique_hits.append(hit)
        return unique_hits

    def _filter_hits_by_precision(
        self,
        hits: list[dict[str, Any]],
        filters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return [hit for hit in hits if self._hit_matches_precision_filters(hit, filters)]

    def _hit_matches_precision_filters(self, hit: dict[str, Any], filters: dict[str, Any]) -> bool:
        source_layers = set(filters.get("source_layers") or [])
        if source_layers:
            entry_type = str(hit.get("entry_type") or (hit.get("metadata") or {}).get("entry_type") or "").strip()
            if entry_type not in source_layers:
                return False

        subject_scopes = set(filters.get("subject_scopes") or [])
        if subject_scopes:
            hit_subjects = set(self._metadata_list(hit, "memory_subject_scopes_text"))
            if not hit_subjects.intersection(subject_scopes):
                return False

        categories = set(filters.get("categories") or [])
        if categories:
            hit_categories = set(self._metadata_list(hit, "memory_categories_text"))
            if not hit_categories.intersection(categories):
                return False

        importance_min = filters.get("importance_min")
        if importance_min is not None and self._hit_memory_importance(hit) < float(importance_min):
            return False
        return True

    def _metadata_list(self, hit: dict[str, Any], key: str) -> list[str]:
        metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
        raw_value = metadata.get(key)
        if isinstance(raw_value, list):
            raw_items = raw_value
        else:
            raw_items = parse_joined_tags(str(raw_value or ""))
        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            text = normalize_text(str(item or "")).strip().lower().replace("-", "_")
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return normalized

    def _hit_memory_importance(self, hit: dict[str, Any]) -> float:
        metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
        try:
            value = float(metadata.get("memory_importance"))
        except (TypeError, ValueError):
            value = 0.0
        return float(max(0.0, min(1.0, value)))

    def _precision_filter_names(self, filters: dict[str, Any]) -> list[str]:
        names: list[str] = []
        if filters.get("source_layers"):
            names.append("source_layers")
        if filters.get("subject_scopes"):
            names.append("subject_scopes")
        if filters.get("categories"):
            names.append("categories")
        if filters.get("importance_min") is not None:
            names.append("importance_min")
        return names

    def _build_memory_snippets(self, fused_hits: list[dict[str, Any]]) -> list[str]:
        snippets: list[str] = []
        seen: set[str] = set()
        for hit in fused_hits:
            source_id = hit["source_id"]
            record = self.store.get_record_by_source_id(source_id)
            if not record:
                continue
            if record["entry_type"] == "summary":
                snippet = self._render_summary_snippet(record)
                dedupe_key = f"summary::{record['summary_id']}"
            elif record["entry_type"] == "semantic_summary":
                snippet = self._render_semantic_summary_snippet(record)
                dedupe_key = f"semantic::{record['semantic_id']}"
            else:
                window = 2 if self._is_question_like(record.get("content", "")) else 1
                context_rows = self.store.get_context_slice(
                    record["session_id"],
                    record["seq_no"],
                    window=window,
                    profile_user_id=str(record.get("profile_user_id") or ""),
                    character_pack_id=str(record.get("character_pack_id") or ""),
                )
                snippet = self._render_raw_snippet(context_rows)
                dedupe_key = f"raw::{record['session_id']}::{record['seq_no']}"
            snippet_key = snippet.strip()
            if snippet and dedupe_key not in seen and snippet_key not in seen:
                snippets.append(snippet)
                seen.add(dedupe_key)
                seen.add(snippet_key)
        return snippets

    def _verify_memories(
        self,
        *,
        now_ts: int,
        original_query: str,
        router_output: dict[str, Any],
        retrieval_result: dict[str, Any],
        verifier_debug_enabled: bool | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        snippets = retrieval_result.get("memory_snippets") or []
        debug_enabled = bool(
            getattr(config, "VERIFIER_DEBUG", False)
            if verifier_debug_enabled is None
            else verifier_debug_enabled
        )
        if not snippets:
            return (
                self._finalize_verifier_output(
                    match_result="mismatch",
                    need_retry=False,
                    retry_query="",
                    retry_keywords=[],
                    retry_time_hint=None,
                    selected_indexes=[],
                    debug_enabled=debug_enabled,
                    reason="没有检索到可用记忆片段。",
                    match_score=0.0,
                ),
                self._build_shortcut_timing(
                    stage="verifier",
                    branch="no_snippets",
                    ready_event_type="decision",
                ),
            )

        fallback = self._build_verifier_fallback(
            original_query=original_query,
            snippets=snippets,
        )
        fallback_output = self._finalize_verifier_output(
            match_result=str(fallback.get("match_result") or "mismatch"),
            need_retry=bool(fallback.get("need_retry")),
            retry_query=str(fallback.get("retry_query") or ""),
            retry_keywords=list(fallback.get("retry_keywords") or []),
            retry_time_hint=fallback.get("retry_time_hint"),
            selected_indexes=(list(range(1, len(snippets) + 1)) if str(fallback.get("match_result") or "") == "match" else []),
            debug_enabled=debug_enabled,
            reason=str(fallback.get("reason") or ""),
            match_score=fallback.get("match_score", 0.0),
        )
        verifier_snippets_text = self._render_verifier_snippets_text(snippets)
        system_prompt, user_prompt = self.prompt_builder.build_verifier_prompts(
            now_ts=now_ts,
            original_query=original_query,
            rewritten_query=str(router_output.get("rewritten_query", "") or ""),
            keywords_json=json.dumps(router_output.get("keywords", []), ensure_ascii=False),
            time_hint_json=json.dumps(self._normalize_time_hint(router_output.get("time_hint")), ensure_ascii=False),
            snippets_text=verifier_snippets_text,
            debug_enabled=debug_enabled,
        )
        verifier_state = {
            "match_result": "",
            "need_retry": None,
            "selected_indexes": [],
            "retry_query": "",
            "retry_keywords": [],
            "retry_time_hint": None,
            "reason": "",
            "match_score": 0.0,
        }

        ndjson_result = self.llm.call_aux_ndjson(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            on_event=lambda event: self._apply_verifier_ndjson_event(
                verifier_state,
                event,
                debug_enabled=debug_enabled,
            ),
            temperature=0.1,
            prompt_cache_key="aux:verifier",
        )
        if not ndjson_result.events or verifier_state.get("need_retry") is None:
            return dict(fallback_output), self._summarize_ndjson_timing(
                stage="verifier",
                ndjson_result=ndjson_result,
                ready_event_type=None,
                branch="fallback",
            )

        match_result = str(verifier_state.get("match_result") or "")
        if match_result not in {"match", "mismatch"}:
            return dict(fallback_output), self._summarize_ndjson_timing(
                stage="verifier",
                ndjson_result=ndjson_result,
                ready_event_type=None,
                branch="fallback",
            )

        need_retry = bool(verifier_state.get("need_retry"))
        selected_indexes = self._normalize_selected_indexes(
            verifier_state.get("selected_indexes"),
            snippet_count=len(snippets),
        )
        retry_query = str(verifier_state.get("retry_query") or "")
        retry_keywords = list(verifier_state.get("retry_keywords") or [])
        retry_time_hint = self._normalize_time_hint(verifier_state.get("retry_time_hint"))
        if match_result == "match" or not need_retry:
            retry_query = ""
            retry_keywords = []
            retry_time_hint = None
        if match_result == "match" and not selected_indexes:
            selected_indexes = list(range(1, len(snippets) + 1))

        if match_result == "mismatch" and need_retry:
            if not retry_query:
                retry_query = str(fallback.get("retry_query") or original_query)
            if not retry_keywords:
                retry_keywords = list(fallback.get("retry_keywords") or extract_semantic_tags(original_query, limit=6))

        return (
            self._finalize_verifier_output(
                match_result=match_result,
                need_retry=need_retry,
                retry_query=retry_query,
                retry_keywords=retry_keywords,
                retry_time_hint=retry_time_hint,
                selected_indexes=selected_indexes,
                debug_enabled=debug_enabled,
                reason=str(verifier_state.get("reason") or ""),
                match_score=verifier_state.get("match_score", 0.0),
            ),
            self._summarize_ndjson_timing(
                stage="verifier",
                ndjson_result=ndjson_result,
                ready_event_type=(
                    "selection"
                    if match_result == "match"
                    else ("retry" if (match_result == "mismatch" and need_retry) else "decision")
                ),
                branch="ndjson",
            ),
        )

    def _apply_router_ndjson_event(
        self,
        state: dict[str, Any],
        event: dict[str, Any],
        *,
        debug_enabled: bool,
    ) -> bool:
        event_type = str(event.get("type") or "").strip().lower()
        if event_type == "decision":
            need_retrieval = self._coerce_bool(event.get("need_retrieval"))
            if need_retrieval is None:
                return False
            state["need_retrieval"] = need_retrieval
            return (need_retrieval is False) and not debug_enabled

        if event_type == "query":
            state["need_retrieval"] = True
            route = str(event.get("route") or "").strip()
            if route:
                state["route"] = route
            rewritten_query = str(event.get("rewritten_query") or "").strip()
            if rewritten_query:
                state["rewritten_query"] = rewritten_query
            if isinstance(event.get("keywords"), list):
                state["keywords"] = [str(item).strip() for item in event.get("keywords") or [] if str(item).strip()]
            state["time_hint"] = self._normalize_time_hint(
                event.get("time_hint"),
                default=state.get("time_hint"),
            )
            index_current_message = self._coerce_bool(event.get("index_current_message"))
            if index_current_message is not None:
                state["index_current_message"] = index_current_message
            return not debug_enabled

        if event_type == "debug":
            reason = str(event.get("reason") or "").strip()
            if reason:
                state["reason"] = reason
            confidence = event.get("confidence")
            if isinstance(confidence, (int, float)):
                state["confidence"] = float(confidence)
            else:
                try:
                    state["confidence"] = float(confidence)
                except Exception:
                    pass
            return True
        return False

    def _should_index_current_message_default(self, user_message: str) -> bool:
        normalized = normalize_text(user_message)
        if not normalized:
            return True
        lowered = normalized.lower()
        return not any(marker in lowered for marker in RAW_INDEX_SKIP_MARKERS)

    def _should_hard_route_memory_search(self, user_message: str) -> bool:
        normalized = normalize_text(user_message)
        if not normalized:
            return False

        if any(word in normalized for word in HARD_ROUTE_WORDS):
            return True

        has_past_reference = any(marker in normalized for marker in PAST_MEMORY_REFERENCE_MARKERS)
        if has_past_reference and (
            self._is_question_like(normalized)
            or any(marker in normalized for marker in PAST_MEMORY_FACT_MARKERS)
        ):
            return True

        has_recall_action = any(marker in normalized for marker in PAST_MEMORY_RECALL_MARKERS)
        has_shared_anchor = any(marker in normalized for marker in ("我们", "我", "你", "Akane", "赤音", "主人"))
        return has_recall_action and has_shared_anchor and self._is_question_like(normalized)

    def _build_memory_search_fallback_query(
        self,
        *,
        user_message: str,
        time_hint: dict[str, Any] | None,
    ) -> str:
        normalized = normalize_text(user_message).strip()
        hint = self._normalize_time_hint(time_hint)
        date_label = str(hint.get("date_label") or "").strip()
        time_of_day = str(hint.get("time_of_day") or "").strip()
        time_label = TIME_OF_DAY_QUERY_LABELS.get(time_of_day, "")

        if date_label and self._contains_memory_recall_intent(normalized):
            parts = [date_label]
            if time_label:
                parts.append(time_label)
            parts.append("发生了什么")
            return " ".join(parts).strip()

        if date_label:
            parts = [date_label]
            if time_label:
                parts.append(time_label)
            if normalized:
                parts.append(normalized)
            return " ".join(parts).strip()

        return normalized

    def _build_memory_search_fallback_keywords(
        self,
        *,
        query: str,
        user_message: str,
        time_hint: dict[str, Any] | None,
        fallback_keywords: list[str],
    ) -> list[str]:
        hint = self._normalize_time_hint(time_hint)
        candidates: list[str] = []
        date_label = str(hint.get("date_label") or "").strip()
        time_label = TIME_OF_DAY_QUERY_LABELS.get(str(hint.get("time_of_day") or "").strip(), "")
        if date_label:
            candidates.append(date_label)
            cn_date = self._format_cn_date_label(date_label)
            if cn_date:
                candidates.append(cn_date)
        if time_label:
            candidates.append(time_label)
        if "发生了什么" in str(query):
            candidates.extend(["发生了什么", "回忆"])
        candidates.extend(extract_semantic_tags(query, limit=6))
        candidates.extend(extract_semantic_tags(user_message, limit=6))
        candidates.extend(list(fallback_keywords or []))

        normalized_keywords: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            keyword = normalize_text(str(item or "")).strip()
            if not keyword or keyword in seen:
                continue
            normalized_keywords.append(keyword)
            seen.add(keyword)
            if len(normalized_keywords) >= 8:
                break
        return normalized_keywords

    def _apply_verifier_ndjson_event(
        self,
        state: dict[str, Any],
        event: dict[str, Any],
        *,
        debug_enabled: bool,
    ) -> bool:
        event_type = str(event.get("type") or "").strip().lower()
        if event_type == "decision":
            match_result = str(event.get("match_result") or "").strip().lower()
            need_retry = self._coerce_bool(event.get("need_retry"))
            if match_result not in {"match", "mismatch"} or need_retry is None:
                return False
            state["match_result"] = match_result
            state["need_retry"] = need_retry
            return (match_result == "mismatch" and not need_retry) and not debug_enabled

        if event_type == "selection":
            state["match_result"] = "match"
            state["need_retry"] = False
            state["selected_indexes"] = self._normalize_selected_indexes(
                event.get("selected_indexes"),
                snippet_count=64,
            )
            return not debug_enabled

        if event_type == "retry":
            state["match_result"] = "mismatch"
            state["need_retry"] = True
            retry_query = str(event.get("retry_query") or "").strip()
            if retry_query:
                state["retry_query"] = retry_query
            if isinstance(event.get("retry_keywords"), list):
                state["retry_keywords"] = [str(item).strip() for item in event.get("retry_keywords") or [] if str(item).strip()]
            state["retry_time_hint"] = self._normalize_time_hint(
                event.get("retry_time_hint"),
                default=state.get("retry_time_hint"),
            )
            return not debug_enabled

        if event_type == "debug":
            reason = str(event.get("reason") or "").strip()
            if reason:
                state["reason"] = reason
            state["match_score"] = self._normalize_match_score(event.get("match_score"))
            return True
        return False

    def _finalize_router_output(
        self,
        *,
        need_retrieval: bool,
        route: str,
        rewritten_query: str,
        keywords: list[str],
        time_hint: dict[str, Any] | None,
        index_current_message: bool | None,
        debug_enabled: bool,
        reason: str = "",
        confidence: float | int | str = 0.0,
    ) -> dict[str, Any]:
        normalized_need_retrieval = bool(need_retrieval)
        result = {
            "need_retrieval": normalized_need_retrieval,
            "route": str(route or ("memory_search" if normalized_need_retrieval else "direct_answer")),
            "rewritten_query": str(rewritten_query or "") if normalized_need_retrieval else "",
            "keywords": list(keywords or []) if normalized_need_retrieval else [],
            "time_hint": self._normalize_time_hint(time_hint),
            "index_current_message": bool(True if index_current_message is None else index_current_message),
        }
        if debug_enabled:
            try:
                normalized_confidence = float(confidence)
            except Exception:
                normalized_confidence = 0.0
            result["reason"] = str(reason or "")
            result["confidence"] = normalized_confidence
        return result

    def _finalize_verifier_output(
        self,
        *,
        match_result: str,
        need_retry: bool,
        retry_query: str,
        retry_keywords: list[str],
        retry_time_hint: dict[str, Any] | None,
        selected_indexes: list[int] | None,
        debug_enabled: bool,
        reason: str = "",
        match_score: Any = 0.0,
    ) -> dict[str, Any]:
        normalized_match_result = str(match_result or "").strip().lower()
        if normalized_match_result not in {"match", "mismatch"}:
            normalized_match_result = "mismatch"

        normalized_need_retry = bool(need_retry)
        if normalized_match_result == "match":
            normalized_need_retry = False

        normalized_retry_time_hint = (
            self._normalize_time_hint(retry_time_hint)
            if normalized_need_retry
            else None
        )
        result = {
            "match_result": normalized_match_result,
            "need_retry": normalized_need_retry,
            "selected_indexes": list(selected_indexes or []) if normalized_match_result == "match" else [],
            "retry_query": str(retry_query or "") if normalized_need_retry else "",
            "retry_keywords": list(retry_keywords or []) if normalized_need_retry else [],
            "retry_time_hint": normalized_retry_time_hint,
        }
        if debug_enabled:
            result["reason"] = str(reason or "")
            result["match_score"] = self._normalize_match_score(match_score)
        return result

    def _coerce_bool(self, value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
        return None

    def _normalize_match_score(self, value: Any) -> float:
        if isinstance(value, bool):
            return 0.0
        try:
            raw = float(value)
        except Exception:
            return 0.0
        if raw > 1.0 and raw <= 100.0:
            raw = raw / 100.0
        return float(max(0.0, min(1.0, raw)))

    def _normalize_selected_indexes(self, value: Any, *, snippet_count: int) -> list[int]:
        if snippet_count <= 0:
            return []
        if not isinstance(value, list):
            return []
        normalized: list[int] = []
        seen: set[int] = set()
        for item in value:
            try:
                index = int(item)
            except Exception:
                continue
            if index < 1 or index > snippet_count or index in seen:
                continue
            normalized.append(index)
            seen.add(index)
        return normalized

    def _select_snippets_by_indexes(self, snippets: list[str], selected_indexes: Any) -> list[str]:
        normalized_indexes = self._normalize_selected_indexes(
            selected_indexes,
            snippet_count=len(snippets),
        )
        if not normalized_indexes:
            return []
        return [
            str(snippets[index - 1])
            for index in normalized_indexes
            if 1 <= index <= len(snippets)
        ]

    def _render_verifier_snippets_text(self, snippets: list[str]) -> str:
        blocks: list[str] = []
        for index, snippet in enumerate(snippets, start=1):
            blocks.append(f"[{index}]\n{snippet}")
        return "\n\n".join(blocks)

    def _build_shortcut_timing(
        self,
        *,
        stage: str,
        branch: str,
        ready_event_type: str | None,
    ) -> dict[str, Any]:
        return {
            "stage": stage,
            "branch": branch,
            "mode": "shortcut",
            "ready_event_type": ready_event_type,
            "ready_at_ms": 0.0,
            "consumed_until_ms": 0.0,
            "stream_end_at_ms": 0.0,
            "saved_vs_full_ms": 0.0,
            "stopped_early": False,
            "completed_stream": True,
            "stop_event_type": "",
            "event_count": 0,
            "events": [],
            "error": "",
        }

    def _summarize_ndjson_timing(
        self,
        *,
        stage: str,
        ndjson_result: Any,
        ready_event_type: str | None,
        branch: str,
    ) -> dict[str, Any]:
        event_timings = list(getattr(ndjson_result, "event_timings", []) or [])
        ready_at_ms = None
        if ready_event_type:
            for item in event_timings:
                if str(item.get("type") or "").strip().lower() == str(ready_event_type).strip().lower():
                    ready_at_ms = float(item.get("elapsed_ms") or 0.0)
                    break

        consumed_until_ms = float(getattr(ndjson_result, "elapsed_ms", 0.0) or 0.0)
        completed_stream = bool(getattr(ndjson_result, "completed_stream", False))
        stop_event_type = str(getattr(ndjson_result, "stop_event_type", "") or "")
        measured_full_stream = completed_stream or stop_event_type == "debug"
        stream_end_at_ms = consumed_until_ms if measured_full_stream else None
        saved_vs_full_ms = None
        if stream_end_at_ms is not None and ready_at_ms is not None:
            saved_vs_full_ms = round(max(0.0, stream_end_at_ms - ready_at_ms), 1)

        return {
            "stage": stage,
            "branch": branch,
            "mode": "ndjson",
            "ready_event_type": ready_event_type,
            "ready_at_ms": ready_at_ms,
            "consumed_until_ms": round(consumed_until_ms, 1),
            "stream_end_at_ms": round(stream_end_at_ms, 1) if stream_end_at_ms is not None else None,
            "saved_vs_full_ms": saved_vs_full_ms,
            "stopped_early": bool(getattr(ndjson_result, "stopped_early", False)),
            "completed_stream": measured_full_stream,
            "stop_event_type": stop_event_type,
            "event_count": len(event_timings),
            "events": event_timings,
            "error": str(getattr(ndjson_result, "error", "") or ""),
        }

    def _extract_time_hint(self, *, user_message: str, now_ts: int) -> dict[str, Any]:
        time_of_day = detect_time_of_day_from_text(user_message)
        date_label = None
        relative_time = None
        lowered = user_message
        now_dt = datetime.fromtimestamp(now_ts)
        explicit_date_label = self._extract_explicit_date_label(user_message, now_dt=now_dt)
        if explicit_date_label:
            date_label = explicit_date_label
            try:
                relative_time = "past" if datetime.strptime(date_label, "%Y-%m-%d").date() <= now_dt.date() else None
            except ValueError:
                relative_time = None
        elif "今天" in lowered:
            date_label = now_dt.strftime("%Y-%m-%d")
            relative_time = "today"
        elif "昨天" in lowered:
            date_label = (now_dt - timedelta(days=1)).strftime("%Y-%m-%d")
            relative_time = "yesterday"
        elif "前天" in lowered:
            date_label = (now_dt - timedelta(days=2)).strftime("%Y-%m-%d")
            relative_time = "past"
        elif "明天" in lowered:
            date_label = (now_dt + timedelta(days=1)).strftime("%Y-%m-%d")
            relative_time = "tomorrow"
        elif any(
            word in lowered
            for word in (
                "之前",
                "上次",
                "上回",
                "以前",
                "曾经",
                "过去",
                "当时",
                "那时候",
                "那会儿",
                "那天",
                "那次",
                "前几天",
                "前阵子",
                "前段时间",
                "说过",
                "聊过",
                "提过",
            )
        ):
            relative_time = "past"
        return {
            "date_label": date_label,
            "time_of_day": time_of_day,
            "relative_time": relative_time,
        }

    def _extract_explicit_date_label(self, user_message: str, *, now_dt: datetime) -> str | None:
        raw = normalize_text(user_message)
        if not raw:
            return None

        iso_match = re.search(
            r"(?<!\d)(?P<year>20\d{2})[-/.年](?P<month>\d{1,2})[-/.月](?P<day>\d{1,2})(?:日|号)?",
            raw,
        )
        if iso_match:
            return self._coerce_explicit_date_label(
                year=int(iso_match.group("year")),
                month=int(iso_match.group("month")),
                day=int(iso_match.group("day")),
                raw=raw,
                now_dt=now_dt,
            )

        month_day_match = re.search(
            r"(?<!\d)(?P<month>\d{1,2})月(?P<day>\d{1,2})(?:日|号)?",
            raw,
        )
        if month_day_match:
            return self._coerce_explicit_date_label(
                year=now_dt.year,
                month=int(month_day_match.group("month")),
                day=int(month_day_match.group("day")),
                raw=raw,
                now_dt=now_dt,
            )
        return None

    def _coerce_explicit_date_label(
        self,
        *,
        year: int,
        month: int,
        day: int,
        raw: str,
        now_dt: datetime,
    ) -> str | None:
        try:
            candidate = datetime(year, month, day)
        except ValueError:
            return None
        if candidate.date() > now_dt.date() and self._contains_memory_recall_intent(raw):
            try:
                candidate = datetime(year - 1, month, day)
            except ValueError:
                return None
        return candidate.strftime("%Y-%m-%d")

    def _contains_memory_recall_intent(self, text: str) -> bool:
        raw = normalize_text(text)
        return any(marker in raw for marker in MEMORY_RECALL_INTENT_MARKERS)

    def _format_cn_date_label(self, date_label: str) -> str:
        try:
            dt = datetime.strptime(str(date_label or ""), "%Y-%m-%d")
        except ValueError:
            return ""
        return f"{dt.month}月{dt.day}日"

    def _normalize_time_hint(
        self,
        time_hint: Any,
        default: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base = {
            "date_label": None,
            "time_of_day": None,
            "relative_time": None,
            "start_ts": None,
            "end_ts": None,
        }
        if isinstance(default, dict):
            for key in base:
                if key in default:
                    base[key] = default.get(key)
        if isinstance(time_hint, dict):
            for key in base:
                if key in time_hint:
                    base[key] = time_hint.get(key)
            return base
        return base

    def _render_raw_snippet(self, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return ""
        parts = ["【原始对话回忆】"]
        mood_line = self._render_raw_memory_mood_line(rows)
        if mood_line:
            parts.append(mood_line)
        parts.append(render_chat_timeline(rows))
        return "\n".join(parts)

    def _render_raw_memory_mood_line(self, rows: list[dict[str, Any]]) -> str:
        mood_tags: list[str] = []
        seen: set[str] = set()
        for row in rows:
            metadata = row.get("memory_metadata") if isinstance(row.get("memory_metadata"), dict) else {}
            raw_tags = metadata.get("mood_tags") if isinstance(metadata, dict) else []
            if not isinstance(raw_tags, list):
                continue
            for item in raw_tags:
                tag = normalize_text(str(item or "")).strip()
                if not tag:
                    continue
                key = tag.lower()
                if key in seen:
                    continue
                seen.add(key)
                mood_tags.append(tag)
                if len(mood_tags) >= 3:
                    return "记忆情绪：" + " / ".join(mood_tags)
        if not mood_tags:
            return ""
        return "记忆情绪：" + " / ".join(mood_tags)

    def _render_summary_snippet(self, record: dict[str, Any]) -> str:
        labels = []
        time_range_label = self._build_summary_time_range_label(record)
        if time_range_label:
            labels.append(time_range_label)
        if record.get("period_label"):
            labels.append(f"阶段:{record['period_label']}")
        if record.get("event_type"):
            labels.append(f"类型:{record['event_type']}")
        key_events = "；".join(record.get("key_events") or [])
        core_facts = "；".join(record.get("core_facts") or [])
        prefix = f"【摘要回忆】[{ ' | '.join(labels) }] " if labels else "【摘要回忆】"
        parts = [f"{prefix}{record.get('diary_summary', '')}"]
        anchor_line = render_relative_time_anchor_line(
            text=" ".join(
                [str(record.get("diary_summary") or "")]
                + [str(event) for event in (record.get("key_events") or [])]
                + [str(fact) for fact in (record.get("core_facts") or [])]
            ),
            time_range_label=time_range_label,
        )
        if anchor_line:
            parts.append(anchor_line)
        mood_line = render_memory_mood_line(record)
        if mood_line:
            parts.append(mood_line)
        if key_events:
            parts.append(f"关键事件：{key_events}")
        if core_facts:
            parts.append(f"核心事实：{core_facts}")
        return "\n".join(parts)

    def _render_semantic_summary_snippet(self, record: dict[str, Any]) -> str:
        labels = []
        time_range_label = self._build_summary_time_range_label(record)
        if time_range_label:
            labels.append(time_range_label)
        if record.get("importance") is not None:
            labels.append(f"重要度:{float(record.get('importance') or 0.0):.2f}")
        prefix = f"【长期语义记忆】[{ ' | '.join(labels) }] " if labels else "【长期语义记忆】"
        parts = [f"{prefix}{record.get('semantic_summary', '')}"]
        anchor_line = render_relative_time_anchor_line(
            text=" ".join(
                [str(record.get("semantic_summary") or "")]
                + [str(fact) for fact in (record.get("stable_facts") or [])]
                + [str(topic) for topic in (record.get("recurring_topics") or [])]
                + [str(person) for person in (record.get("important_people") or [])]
                + [str(item_text) for item_text in (record.get("open_loops") or [])]
            ),
            time_range_label=time_range_label,
        )
        if anchor_line:
            parts.append(anchor_line)
        mood_line = render_memory_mood_line(record)
        if mood_line:
            parts.append(mood_line)
        stable_facts = "；".join(record.get("stable_facts") or [])
        recurring_topics = "；".join(record.get("recurring_topics") or [])
        important_people = "；".join(record.get("important_people") or [])
        open_loops = "；".join(record.get("open_loops") or [])
        if stable_facts:
            parts.append(f"稳定事实：{stable_facts}")
        if recurring_topics:
            parts.append(f"反复话题：{recurring_topics}")
        if important_people:
            parts.append(f"重要人物：{important_people}")
        if open_loops:
            parts.append(f"待续线索：{open_loops}")
        return "\n".join(parts)

    def _render_current_message_line(
        self,
        *,
        current_user_record: dict[str, Any],
    ) -> str:
        return render_chat_line(
            role=str(current_user_record.get("role") or "user"),
            content=str(current_user_record.get("content") or ""),
            timestamp=current_user_record.get("timestamp"),
        )

    def _build_router_recent_context(
        self,
        *,
        recent_raw: list[dict[str, Any]],
        current_user_record: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rows = list(recent_raw or [])
        if not rows:
            return []

        current_source_id = str(current_user_record.get("source_id") or "").strip()
        if current_source_id:
            filtered = [
                row
                for row in rows
                if str(row.get("source_id") or "").strip() != current_source_id
            ]
            if len(filtered) != len(rows):
                return filtered

        current_role = str(current_user_record.get("role") or "")
        current_content = str(current_user_record.get("content") or "")
        current_timestamp = current_user_record.get("timestamp")
        last_row = rows[-1] if rows else None
        if (
            isinstance(last_row, dict)
            and str(last_row.get("role") or "") == current_role
            and str(last_row.get("content") or "") == current_content
            and last_row.get("timestamp") == current_timestamp
        ):
            return rows[:-1]
        return rows

    def _normalize_rewritten_query(
        self,
        query: Any,
        *,
        fallback_query: str,
        keywords: list[str],
    ) -> str:
        text = normalize_text(query or "").strip()
        if text:
            text = text.strip(" \t\r\n?？!！。；;：:")
        if not text or self._looks_like_router_task_query(text):
            fallback = [normalize_text(item) for item in (keywords or [])]
            fallback = [item for item in fallback if item]
            if fallback:
                return " ".join(fallback[:6]).strip()
            return normalize_text(fallback_query or "").strip()
        return text

    def _normalize_keywords(self, keywords: list[str], *, fallback: list[str] | None = None, limit: int = 8) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in [*list(keywords or []), *list(fallback or [])]:
            keyword = normalize_text(str(item or "")).strip()
            if not keyword:
                continue
            dedupe_key = keyword.lower()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized.append(keyword)
            if len(normalized) >= max(1, int(limit)):
                break
        return normalized

    def _normalize_precision_filters(
        self,
        value: Any = None,
        *,
        source_layers: Any = None,
        subject_scopes: Any = None,
        categories: Any = None,
        importance_min: Any = None,
        limit: Any = None,
    ) -> dict[str, Any]:
        raw = value if isinstance(value, dict) else {}
        raw_source_layers = source_layers if source_layers is not None else raw.get("source_layers")
        raw_subject_scopes = subject_scopes if subject_scopes is not None else raw.get("subject_scopes")
        raw_categories = categories if categories is not None else raw.get("categories")
        raw_importance_min = importance_min if importance_min is not None else raw.get("importance_min")
        raw_limit = limit if limit is not None else raw.get("limit")
        return {
            "source_layers": self._normalize_precision_enum_list(
                raw_source_layers,
                allowed=PRECISION_SOURCE_LAYERS,
                aliases=PRECISION_SOURCE_LAYER_ALIASES,
                limit=3,
            ),
            "subject_scopes": self._normalize_precision_enum_list(
                raw_subject_scopes,
                allowed=PRECISION_SUBJECT_SCOPES,
                aliases=PRECISION_SUBJECT_SCOPE_ALIASES,
                limit=3,
            ),
            "categories": self._normalize_precision_enum_list(
                raw_categories,
                allowed=PRECISION_CATEGORIES,
                aliases=PRECISION_CATEGORY_ALIASES,
                limit=4,
            ),
            "importance_min": self._coerce_optional_unit_float(raw_importance_min),
            "limit": self._coerce_retrieval_limit(raw_limit),
        }

    def _normalize_precision_enum_list(
        self,
        value: Any,
        *,
        allowed: set[str],
        aliases: dict[str, str],
        limit: int,
    ) -> list[str]:
        if isinstance(value, str):
            raw_items = [part for part in re.split(r"[,，;；|、\s]+", value) if part]
        elif isinstance(value, (list, tuple, set)):
            raw_items = []
            for item in value:
                raw_items.extend(part for part in re.split(r"[,，;；|、\s]+", str(item or "")) if part)
        elif value is None:
            raw_items = []
        else:
            raw_items = [str(value or "")]

        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            key = normalize_text(str(item or "")).strip("[](){}\"' ").lower().replace("-", "_")
            mapped = aliases.get(key) or key
            if mapped not in allowed or mapped in seen:
                continue
            seen.add(mapped)
            normalized.append(mapped)
            if len(normalized) >= max(1, int(limit)):
                break
        return normalized

    def _coerce_optional_unit_float(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return float(max(0.0, min(1.0, number)))

    def _coerce_retrieval_limit(self, value: Any) -> int:
        if value in (None, ""):
            return 4
        try:
            number = int(value)
        except (TypeError, ValueError):
            return 4
        return max(1, min(12, number))

    def _looks_like_router_task_query(self, text: str) -> bool:
        raw = normalize_text(text or "")
        if not raw:
            return True
        if raw.startswith(("请", "需要", "确认", "判断")):
            return True
        return any(marker in raw for marker in TASK_LIKE_ROUTER_QUERY_MARKERS)

    def _build_summary_time_range_label(self, record: dict[str, Any]) -> str:
        start_ts, end_ts = self._resolve_record_time_range(record)
        return self._format_time_range_label(start_ts=start_ts, end_ts=end_ts)

    def _resolve_record_time_range(self, record: dict[str, Any]) -> tuple[int | None, int | None]:
        if record.get("period_start_ts") is not None or record.get("period_end_ts") is not None:
            start_ts = record.get("period_start_ts")
            end_ts = record.get("period_end_ts") or record.get("timestamp")
            return (
                int(start_ts) if start_ts is not None else None,
                int(end_ts) if end_ts is not None else None,
            )

        session_id = str(record.get("session_id") or "")
        start_seq = record.get("source_start_seq")
        end_seq = record.get("source_end_seq")
        start_record = None
        end_record = None
        if session_id and start_seq is not None:
            start_record = self.store.get_message_by_seq_no(
                session_id,
                int(start_seq),
                profile_user_id=str(record.get("profile_user_id") or ""),
                character_pack_id=str(record.get("character_pack_id") or ""),
            )
        if session_id and end_seq is not None:
            end_record = self.store.get_message_by_seq_no(
                session_id,
                int(end_seq),
                profile_user_id=str(record.get("profile_user_id") or ""),
                character_pack_id=str(record.get("character_pack_id") or ""),
            )
        start_ts = (start_record or {}).get("timestamp")
        end_ts = (end_record or {}).get("timestamp") or record.get("timestamp")
        return (
            int(start_ts) if start_ts is not None else None,
            int(end_ts) if end_ts is not None else None,
        )

    def _format_time_range_label(
        self,
        *,
        start_ts: int | float | None,
        end_ts: int | float | None,
    ) -> str:
        if start_ts is None and end_ts is None:
            return ""
        if start_ts is None:
            return timestamp_to_datetime_label(end_ts)
        if end_ts is None:
            return timestamp_to_datetime_label(start_ts)

        start_dt = datetime.fromtimestamp(float(start_ts))
        end_dt = datetime.fromtimestamp(float(end_ts))
        if start_dt.date() == end_dt.date():
            return f"{start_dt.strftime('%Y-%m-%d %H:%M')} ~ {end_dt.strftime('%H:%M')}"
        return f"{start_dt.strftime('%Y-%m-%d %H:%M')} ~ {end_dt.strftime('%Y-%m-%d %H:%M')}"

    def _build_verifier_fallback(self, *, original_query: str, snippets: list[str]) -> dict[str, Any]:
        joined = "\n".join(snippets)
        intent = self._infer_memory_intent(original_query)
        query_tags = extract_semantic_tags(original_query, limit=4)

        match = False
        reason = "使用基础回退规则完成匹配判断。"
        match_score = 0.42

        if intent == "identity" and any(phrase in joined for phrase in ("我叫", "名字叫", "名字是", "叫做")):
            match = True
            match_score = 0.82
            reason = "检索片段里出现了明确的名字表述，可以直接回答身份类问题。"
        elif intent == "preference" and any(phrase in joined for phrase in ("喜欢", "更喜欢", "偏好", "想喝", "讨厌")):
            match = True
            match_score = 0.78
            reason = "检索片段里出现了明确偏好表述，可以直接回答偏好类问题。"
        elif intent == "plan" and any(phrase in joined for phrase in ("计划", "安排", "打算", "准备", "先", "再", "继续")):
            match = True
            match_score = 0.76
            reason = "检索片段里出现了明确计划或安排线索，可以直接回答计划类问题。"
        elif any(term and term in joined for term in query_tags):
            if self._is_question_like(original_query) and all(self._is_question_like(snippet) for snippet in snippets):
                match = False
                match_score = 0.28
                reason = "检索片段主要是在重复提问，没有提供足够的新事实。"
            else:
                match = True
                match_score = 0.62
                reason = "检索片段与问题存在直接关键词重合，具备基础回答价值。"

        return {
            "match_result": "match" if match else "mismatch",
            "match_score": match_score,
            "need_retry": not match,
            "retry_query": "" if match else original_query,
            "retry_keywords": [] if match else extract_semantic_tags(original_query, limit=6),
            "retry_time_hint": None,
            "reason": reason,
        }

    def _rerank_fused_hits(
        self,
        *,
        query: str,
        keywords: list[str],
        fused_hits: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        intent = self._infer_memory_intent(query)
        query_tags = set(extract_semantic_tags(" ".join([query, *keywords]), limit=10))
        query_is_question = self._is_question_like(query)
        normalized_query = normalize_text(query)

        reranked: list[dict[str, Any]] = []
        for hit in fused_hits:
            document = str(hit.get("document") or "")
            metadata = hit.get("metadata") or {}
            entry_type = str(hit.get("entry_type") or "")
            tag_text = str(metadata.get("semantic_tags_text") or "")
            haystack = f"{document}\n{tag_text}"
            bonus = 0.0
            normalized_document = normalize_text(document)

            if normalized_document and normalized_document == normalized_query:
                bonus -= 0.020

            if query_is_question and self._is_question_like(document):
                bonus -= 0.010

            overlap = len(query_tags.intersection(set(extract_semantic_tags(haystack, limit=10))))
            bonus += min(0.006, overlap * 0.0015)

            if intent == "identity":
                if any(phrase in document for phrase in ("我叫", "名字叫", "名字是", "叫做")):
                    bonus += 0.018
                if self._is_question_like(document):
                    bonus -= 0.006
                if entry_type == "semantic_summary":
                    bonus += 0.008

            if intent == "preference":
                if any(phrase in document for phrase in ("喜欢", "更喜欢", "偏好", "想喝", "讨厌")):
                    bonus += 0.014
                if entry_type == "semantic_summary":
                    bonus += 0.008

            if intent == "plan":
                if any(phrase in document for phrase in ("打算", "计划", "安排", "准备", "先", "再", "继续")):
                    bonus += 0.010
                if entry_type == "summary":
                    bonus += 0.004

            if intent == "generic_past" and entry_type == "summary":
                bonus += 0.005
            if intent == "generic_past" and entry_type == "semantic_summary":
                bonus += 0.006

            final_score = float(hit["rrf_score"]) + bonus
            reranked.append(
                {
                    **hit,
                    "rank_bonus": float(bonus),
                    "final_score": float(final_score),
                }
            )

        reranked.sort(
            key=lambda item: (
                -item["final_score"],
                -item["rrf_score"],
                -item["semantic_score"],
                -item["tag_score"],
            )
        )
        return reranked

    def _infer_memory_intent(self, query: str) -> str:
        raw = str(query or "")
        if any(marker in raw for marker in ("叫什么名字", "我叫什么", "名字是", "名字")):
            return "identity"
        if any(marker in raw for marker in ("喜欢什么", "更喜欢", "偏好", "想喝", "讨厌")):
            return "preference"
        if any(marker in raw for marker in ("计划", "安排", "打算", "要做什么", "复习", "准备")):
            return "plan"
        if any(marker in raw for marker in ("之前", "上次", "以前", "记得", "回忆", "说过", "提过", "来着")):
            return "generic_past"
        return "generic"

    def _is_question_like(self, text: str) -> bool:
        raw = str(text or "")
        return any(
            marker in raw
            for marker in (
                "?",
                "？",
                "吗",
                "嘛",
                "什么",
                "怎么",
                "为何",
                "为什么",
                "谁",
                "哪",
                "几",
                "记得",
                "是不是",
                "要不要",
                "来着",
            )
        )
