from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from companion_v01.persona_config import PERSONA
from companion_v01.prompt_builder import PromptBuilder
from companion_v01.retrieval_service import RetrievalService
from companion_v01.store import MemoryStore


class RetrievalServiceTests(unittest.TestCase):
    def _build_service(self) -> RetrievalService:
        return RetrievalService(
            store=object(),
            vector_store=object(),
            llm=object(),
            prompt_builder=PromptBuilder(PERSONA),
        )

    def _install_fake_vector_hits(
        self,
        service: RetrievalService,
        *,
        semantic_hits: list[dict[str, object]],
        keyword_hits: list[dict[str, object]] | None = None,
    ) -> None:
        class FakeVectorStore:
            def semantic_search(self, **kwargs):
                self.semantic_kwargs = kwargs
                return list(semantic_hits)

            def keyword_search(self, **kwargs):
                self.keyword_kwargs = kwargs
                return list(keyword_hits or [])

        service.vector_store = FakeVectorStore()
        service._build_memory_snippets = lambda fused_hits: [hit["source_id"] for hit in fused_hits]

    def test_run_returns_pipeline_result_without_retrieval(self) -> None:
        service = self._build_service()
        service._build_router_output = lambda **kwargs: (
            {
                "need_retrieval": False,
                "route": "direct_answer",
                "rewritten_query": "",
                "keywords": [],
                "time_hint": None,
                "reason": "",
                "confidence": 0.5,
            },
            {"mode": "skip"},
        )

        result = service.run(
            profile_user_id="user-1",
            user_message="今天天气不错",
            now_ts=1712400000,
            current_user_record={"role": "user", "content": "今天天气不错", "timestamp": 1712400000},
            recent_raw=[],
            exclude_source_ids=["current_user_msg"],
        )

        self.assertFalse(result.used_retrieval)
        self.assertEqual(result.confirmed_snippets, [])
        self.assertEqual(result.router_output["route"], "direct_answer")
        self.assertEqual(result.router_timing["mode"], "skip")
        self.assertEqual(result.verifier_output["match_result"], "skip")

    def test_retrieve_memories_category_filter_uses_or_admission(self) -> None:
        service = self._build_service()
        self._install_fake_vector_hits(
            service,
            semantic_hits=[
                {
                    "source_id": "single_category",
                    "document": "用户喜欢喝可乐",
                    "metadata": {
                        "entry_type": "raw",
                        "memory_categories_text": "preference",
                        "memory_subject_scopes_text": "user",
                        "memory_importance": 0.7,
                    },
                    "semantic_score": 0.9,
                },
                {
                    "source_id": "double_category",
                    "document": "用户和 Akane 约定晚点继续聊",
                    "metadata": {
                        "entry_type": "summary",
                        "memory_categories_text": "preference,relationship",
                        "memory_subject_scopes_text": "user,assistant",
                        "memory_importance": 0.8,
                    },
                    "semantic_score": 0.8,
                },
                {
                    "source_id": "project_memory",
                    "document": "向量检索项目要继续优化",
                    "metadata": {
                        "entry_type": "semantic_summary",
                        "memory_categories_text": "project_work",
                        "memory_subject_scopes_text": "other",
                        "memory_importance": 0.9,
                    },
                    "semantic_score": 0.7,
                },
            ],
        )

        result = service._retrieve_memories(
            profile_user_id="user-1",
            query="我喜欢什么",
            keywords=["喜欢", "可乐"],
            time_hint=None,
            categories=["preference", "relationship"],
            limit=2,
            exclude_source_ids=[],
        )

        self.assertEqual([hit["source_id"] for hit in result["fused_hits"]], ["single_category", "double_category"])
        self.assertEqual(result["precision_filters"]["relaxation_stage"], "strict")
        self.assertEqual(result["precision_filters"]["applied_filters"], ["categories"])

    def test_retrieve_memories_filters_hits_by_store_character_pack_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            akane_record = store.add_message(
                profile_user_id="user-1",
                session_id="qq-session",
                character_pack_id="",
                role="user",
                content="Akane default memory about cat ears.",
                timestamp=1712400000,
            )
            reimu_record = store.add_message(
                profile_user_id="user-1",
                session_id="qq-session",
                character_pack_id="reimu",
                role="user",
                content="Reimu shrine memory.",
                timestamp=1712400001,
            )
            service = RetrievalService(
                store=store,
                vector_store=object(),
                llm=object(),
                prompt_builder=PromptBuilder(PERSONA),
            )
            self._install_fake_vector_hits(
                service,
                semantic_hits=[
                    {
                        "source_id": akane_record["source_id"],
                        "document": "Akane default memory about cat ears.",
                        "metadata": {"entry_type": "raw", "character_pack_id": "reimu"},
                        "semantic_score": 0.95,
                    },
                    {
                        "source_id": reimu_record["source_id"],
                        "document": "Reimu shrine memory.",
                        "metadata": {"entry_type": "raw", "character_pack_id": "reimu"},
                        "semantic_score": 0.9,
                    },
                ],
            )

            result = service._retrieve_memories(
                profile_user_id="user-1",
                character_pack_id="reimu",
                query="shrine memory",
                keywords=["memory"],
                time_hint=None,
                limit=4,
                exclude_source_ids=[],
            )

            self.assertEqual([hit["source_id"] for hit in result["fused_hits"]], [reimu_record["source_id"]])
            self.assertEqual(result["memory_snippets"], [reimu_record["source_id"]])

    def test_retrieve_memories_source_layer_filter_limits_candidates(self) -> None:
        service = self._build_service()
        self._install_fake_vector_hits(
            service,
            semantic_hits=[
                {
                    "source_id": "raw_memory",
                    "document": "原始对话里提到糖水",
                    "metadata": {"entry_type": "raw", "memory_importance": 0.5},
                    "semantic_score": 0.9,
                },
                {
                    "source_id": "semantic_memory",
                    "document": "长期记忆：用户偏好甜饮",
                    "metadata": {"entry_type": "semantic_summary", "memory_importance": 0.9},
                    "semantic_score": 0.8,
                },
            ],
        )

        result = service._retrieve_memories(
            profile_user_id="user-1",
            query="用户饮料偏好",
            keywords=["饮料", "偏好"],
            time_hint=None,
            source_layers=["semantic_summary"],
            limit=1,
            exclude_source_ids=[],
        )

        self.assertEqual([hit["source_id"] for hit in result["fused_hits"]], ["semantic_memory"])
        self.assertEqual(result["precision_filters"]["applied_filters"], ["source_layers"])

    def test_retrieve_memories_relaxes_importance_when_candidate_count_is_too_low(self) -> None:
        service = self._build_service()
        self._install_fake_vector_hits(
            service,
            semantic_hits=[
                {
                    "source_id": "high_importance",
                    "document": "用户喜欢可乐",
                    "metadata": {
                        "entry_type": "raw",
                        "memory_categories_text": "preference",
                        "memory_importance": 0.9,
                    },
                    "semantic_score": 0.9,
                },
                {
                    "source_id": "lower_importance",
                    "document": "用户也提过喜欢橙汁",
                    "metadata": {
                        "entry_type": "raw",
                        "memory_categories_text": "preference",
                        "memory_importance": 0.4,
                    },
                    "semantic_score": 0.8,
                },
                {
                    "source_id": "wrong_category",
                    "document": "桌宠窗口调试完成",
                    "metadata": {
                        "entry_type": "raw",
                        "memory_categories_text": "project_work",
                        "memory_importance": 0.95,
                    },
                    "semantic_score": 0.7,
                },
            ],
        )

        result = service._retrieve_memories(
            profile_user_id="user-1",
            query="我喜欢喝什么",
            keywords=["喜欢", "饮料"],
            time_hint=None,
            categories=["preference"],
            importance_min=0.8,
            limit=2,
            exclude_source_ids=[],
        )

        self.assertEqual([hit["source_id"] for hit in result["fused_hits"]], ["high_importance", "lower_importance"])
        self.assertEqual(result["precision_filters"]["relaxation_stage"], "drop_importance_min")
        self.assertEqual(result["precision_filters"]["relaxed_filters"], ["importance_min"])

    def test_run_retrieval_chain_excludes_previous_fused_hits_on_retry(self) -> None:
        service = self._build_service()
        retrieval_calls = []

        def fake_retrieve_memories(**kwargs):
            retrieval_calls.append(list(kwargs.get("exclude_source_ids") or []))
            if len(retrieval_calls) == 1:
                return {
                    "filtered_candidate_count": 4,
                    "time_filter": {"date_label": None, "time_of_day": None, "relative_time": None, "matched": False},
                    "fused_hits": [
                        {"source_id": "memory_a"},
                        {"source_id": "memory_b"},
                        {"source_id": "memory_c"},
                        {"source_id": "memory_d"},
                    ],
                    "memory_snippets": ["first pass"],
                }
            return {
                "filtered_candidate_count": 2,
                "time_filter": {"date_label": None, "time_of_day": None, "relative_time": None, "matched": False},
                "fused_hits": [
                    {"source_id": "memory_e"},
                    {"source_id": "memory_f"},
                ],
                "memory_snippets": ["second pass"],
            }

        verify_calls = []

        def fake_verify_memories(**kwargs):
            verify_calls.append(kwargs.get("retrieval_result"))
            if len(verify_calls) == 1:
                return (
                    {
                        "match_result": "mismatch",
                        "need_retry": True,
                        "retry_query": "retry query",
                        "retry_keywords": ["retry"],
                        "retry_time_hint": None,
                        "reason": "retry",
                    },
                    {"mode": "ndjson"},
                )
            return (
                {
                    "match_result": "match",
                    "need_retry": False,
                    "selected_indexes": [1],
                    "retry_query": "",
                    "retry_keywords": [],
                    "retry_time_hint": None,
                    "reason": "",
                },
                {"mode": "ndjson"},
            )

        service._retrieve_memories = fake_retrieve_memories
        service._verify_memories = fake_verify_memories

        retrieval_result, verifier_output, snippets, verifier_timing = service._run_retrieval_chain(
            profile_user_id="user-1",
            original_query="我之前说了什么",
            now_ts=1712400000,
            router_output={
                "rewritten_query": "我之前说了什么",
                "keywords": ["之前"],
                "time_hint": None,
            },
            exclude_source_ids=["current_user_msg"],
            verifier_debug_enabled=False,
        )

        self.assertEqual(retrieval_result["memory_snippets"], ["second pass"])
        self.assertEqual(verifier_output["match_result"], "match")
        self.assertEqual(snippets, ["second pass"])
        self.assertEqual(len(retrieval_calls), 2)
        self.assertCountEqual(retrieval_calls[0], ["current_user_msg"])
        self.assertCountEqual(
            retrieval_calls[1],
            ["current_user_msg", "memory_a", "memory_b", "memory_c", "memory_d"],
        )
        self.assertEqual(verifier_timing["selected_attempt"], 2)
        self.assertEqual(
            verifier_timing["attempts"][1]["excluded_source_ids"],
            sorted(["current_user_msg", "memory_a", "memory_b", "memory_c", "memory_d"]),
        )

    def test_run_retrieval_chain_only_returns_selected_snippets_for_match(self) -> None:
        service = self._build_service()
        service._retrieve_memories = lambda **kwargs: {
            "filtered_candidate_count": 3,
            "time_filter": {"date_label": None, "time_of_day": None, "relative_time": None, "matched": False},
            "fused_hits": [
                {"source_id": "memory_a"},
                {"source_id": "memory_b"},
                {"source_id": "memory_c"},
            ],
            "memory_snippets": ["snippet A", "snippet B", "snippet C"],
        }
        service._verify_memories = lambda **kwargs: (
            {
                "match_result": "match",
                "need_retry": False,
                "selected_indexes": [2, 3],
                "retry_query": "",
                "retry_keywords": [],
                "retry_time_hint": None,
                "reason": "",
            },
            {"mode": "ndjson"},
        )

        retrieval_result, verifier_output, snippets, _ = service._run_retrieval_chain(
            profile_user_id="user-1",
            original_query="我之前说了什么",
            now_ts=1712400000,
            router_output={
                "rewritten_query": "我之前说了什么",
                "keywords": ["之前"],
                "time_hint": None,
            },
            exclude_source_ids=["current_user_msg"],
            verifier_debug_enabled=False,
        )

        self.assertEqual(retrieval_result["memory_snippets"], ["snippet A", "snippet B", "snippet C"])
        self.assertEqual(verifier_output["selected_indexes"], [2, 3])
        self.assertEqual(snippets, ["snippet B", "snippet C"])

    def test_run_retrieval_chain_drops_snippets_on_mismatch_without_retry(self) -> None:
        service = self._build_service()
        service._retrieve_memories = lambda **kwargs: {
            "filtered_candidate_count": 2,
            "time_filter": {"date_label": None, "time_of_day": None, "relative_time": None, "matched": False},
            "fused_hits": [
                {"source_id": "memory_a"},
                {"source_id": "memory_b"},
            ],
            "memory_snippets": ["snippet A", "snippet B"],
        }
        service._verify_memories = lambda **kwargs: (
            {
                "match_result": "mismatch",
                "need_retry": False,
                "selected_indexes": [],
                "retry_query": "",
                "retry_keywords": [],
                "retry_time_hint": None,
                "reason": "",
            },
            {"mode": "ndjson"},
        )

        _, verifier_output, snippets, _ = service._run_retrieval_chain(
            profile_user_id="user-1",
            original_query="我之前说了什么",
            now_ts=1712400000,
            router_output={
                "rewritten_query": "我之前说了什么",
                "keywords": ["之前"],
                "time_hint": None,
            },
            exclude_source_ids=["current_user_msg"],
            verifier_debug_enabled=False,
        )

        self.assertEqual(verifier_output["match_result"], "mismatch")
        self.assertEqual(snippets, [])

    def test_build_router_recent_context_excludes_current_user_record(self) -> None:
        service = self._build_service()

        context = service._build_router_recent_context(
            recent_raw=[
                {"source_id": "msg-1", "role": "assistant", "content": "刚才我们聊到集市了。", "timestamp": 100},
                {"source_id": "msg-2", "role": "user", "content": "是呀", "timestamp": 101},
            ],
            current_user_record={"source_id": "msg-2", "role": "user", "content": "是呀", "timestamp": 101},
        )

        self.assertEqual(len(context), 1)
        self.assertEqual(context[0]["source_id"], "msg-1")

    def test_normalize_rewritten_query_collapses_task_like_prompt_to_keywords(self) -> None:
        service = self._build_service()

        rewritten_query = service._normalize_rewritten_query(
            "用户说“是呀”是在回应Akane的哪一句具体发言？请查找紧邻‘是呀’之前的对话上下文。",
            fallback_query="是呀",
            keywords=["回应", "上一句", "对话上下文"],
        )

        self.assertEqual(rewritten_query, "回应 上一句 对话上下文")

    def test_apply_router_ndjson_event_keeps_index_current_message_from_query_event(self) -> None:
        service = self._build_service()
        state = {
            "need_retrieval": None,
            "route": "direct_answer",
            "rewritten_query": "",
            "keywords": [],
            "time_hint": None,
            "index_current_message": True,
            "reason": "",
            "confidence": 0.0,
        }

        should_stop = service._apply_router_ndjson_event(
            state,
            {
                "type": "query",
                "route": "memory_search",
                "rewritten_query": "记忆测试 上次说过什么",
                "keywords": ["记忆测试", "上次说过什么"],
                "time_hint": {},
                "index_current_message": False,
            },
            debug_enabled=False,
        )

        self.assertTrue(should_stop)
        self.assertTrue(state["need_retrieval"])
        self.assertFalse(state["index_current_message"])

    def test_apply_verifier_ndjson_event_requires_selection_after_match(self) -> None:
        service = self._build_service()
        state = {
            "match_result": "",
            "need_retry": None,
            "selected_indexes": [],
            "retry_query": "",
            "retry_keywords": [],
            "retry_time_hint": None,
            "reason": "",
            "match_score": 0.0,
        }

        should_stop = service._apply_verifier_ndjson_event(
            state,
            {"type": "decision", "match_result": "match", "need_retry": False},
            debug_enabled=False,
        )

        self.assertFalse(should_stop)
        self.assertEqual(state["match_result"], "match")
        self.assertFalse(state["need_retry"])

        should_stop = service._apply_verifier_ndjson_event(
            state,
            {"type": "selection", "selected_indexes": [1, 3]},
            debug_enabled=False,
        )

        self.assertTrue(should_stop)
        self.assertEqual(state["selected_indexes"], [1, 3])

    def test_apply_verifier_ndjson_event_allows_early_stop_for_mismatch_without_retry(self) -> None:
        service = self._build_service()
        state = {
            "match_result": "",
            "need_retry": None,
            "selected_indexes": [],
            "retry_query": "",
            "retry_keywords": [],
            "retry_time_hint": None,
            "reason": "",
            "match_score": 0.0,
        }

        should_stop = service._apply_verifier_ndjson_event(
            state,
            {"type": "decision", "match_result": "mismatch", "need_retry": False},
            debug_enabled=False,
        )

        self.assertTrue(should_stop)
        self.assertEqual(state["match_result"], "mismatch")
        self.assertFalse(state["need_retry"])

    def test_should_index_current_message_default_skips_obvious_memory_test_queries(self) -> None:
        service = self._build_service()

        self.assertFalse(service._should_index_current_message_default("你还记得吗，我之前说过什么"))
        self.assertTrue(service._should_index_current_message_default("今天晚饭想吃什么呀"))

    def test_should_hard_route_memory_search_for_past_memory_questions(self) -> None:
        service = self._build_service()

        self.assertTrue(service._should_hard_route_memory_search("我们当时定的计划是什么来着"))
        self.assertTrue(service._should_hard_route_memory_search("昨天我们都买了什么呀"))
        self.assertTrue(service._should_hard_route_memory_search("我有没有提过那个项目叫什么"))

    def test_should_hard_route_memory_search_ignores_new_past_fact_statement(self) -> None:
        service = self._build_service()

        self.assertFalse(service._should_hard_route_memory_search("我昨天没睡好"))
        self.assertFalse(service._should_hard_route_memory_search("我昨天买了菜"))
        self.assertFalse(service._should_hard_route_memory_search("以前我也学过 C 语言"))

    def test_memory_search_fallback_query_uses_explicit_date_intent(self) -> None:
        service = self._build_service()
        now_ts = int(datetime(2026, 4, 18, 12, 0).timestamp())

        time_hint = service._extract_time_hint(
            user_message="Akane，回忆一下4月12日晚上的事情",
            now_ts=now_ts,
        )
        query = service._build_memory_search_fallback_query(
            user_message="Akane，回忆一下4月12日晚上的事情",
            time_hint=time_hint,
        )
        keywords = service._build_memory_search_fallback_keywords(
            query=query,
            user_message="Akane，回忆一下4月12日晚上的事情",
            time_hint=time_hint,
            fallback_keywords=[],
        )

        self.assertEqual(time_hint["date_label"], "2026-04-12")
        self.assertEqual(time_hint["time_of_day"], "night")
        self.assertEqual(query, "2026-04-12 晚上 发生了什么")
        self.assertIn("4月12日", keywords)
        self.assertIn("发生了什么", keywords)

    def test_hard_route_fallback_uses_intent_query_when_router_stream_fails(self) -> None:
        service = self._build_service()

        class EmptyRouterLLM:
            def call_aux_ndjson(self, **kwargs):
                return SimpleNamespace(
                    events=[],
                    event_timings=[],
                    elapsed_ms=0.0,
                    completed_stream=False,
                    stop_event_type="",
                    stopped_early=False,
                    error="",
                )

        service.llm = EmptyRouterLLM()
        now_ts = int(datetime(2026, 4, 18, 12, 0).timestamp())
        router_output, router_timing = service._build_router_output(
            profile_user_id="user-1",
            user_message="Akane，回忆一下4月12日晚上的事情",
            current_user_record={
                "source_id": "current",
                "role": "user",
                "content": "Akane，回忆一下4月12日晚上的事情",
                "timestamp": now_ts,
            },
            recent_raw=[],
            now_ts=now_ts,
            router_debug_enabled=False,
        )

        self.assertTrue(router_output["need_retrieval"])
        self.assertEqual(router_output["route"], "memory_search")
        self.assertEqual(router_output["rewritten_query"], "2026-04-12 晚上 发生了什么")
        self.assertEqual(router_output["time_hint"]["date_label"], "2026-04-12")
        self.assertEqual(router_output["time_hint"]["time_of_day"], "night")
        self.assertEqual(router_timing["branch"], "hard_route_fallback")


if __name__ == "__main__":
    unittest.main()
