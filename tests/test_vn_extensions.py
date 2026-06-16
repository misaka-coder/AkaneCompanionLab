from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import config

from companion_v01.embedding_provider import BaseEmbeddingProvider, CachedEmbeddingProvider, HashedEmbeddingProvider
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.capability_registry import CapabilityRegistry, CapabilitySnapshot
from companion_v01.client_protocol import ClientMode
from companion_v01.memory_compaction_service import MemoryCompactionService
from companion_v01.memory_rendering import render_semantic_summary_timeline, render_summary_timeline
from companion_v01.mode_profiles import ModeProfileRegistry
from companion_v01.persona_config import PERSONA
from companion_v01.prompt_builder import PromptBuilder
from companion_v01.retrieval_service import RetrievalService
from companion_v01.store import MemoryStore
from companion_v01.text_utils import render_chat_line, render_chat_timeline, resolve_speaker_name
from companion_v01.artifact_system import ArtifactContainerService
from companion_v01.gift_system import GiftSystemService
from companion_v01.tool_runtime import CancelReminderToolHandler, CheckInventoryToolHandler, ListRemindersToolHandler, ManageArtifactToolHandler, ManageGiftToolHandler, SetReminderToolHandler, ToolExecutionContext, ToolExecutionResult, ToolMetadata


class TextUtilsSpeakerTests(unittest.TestCase):
    def test_resolve_speaker_name_supports_npc_roles(self) -> None:
        self.assertEqual(resolve_speaker_name("assistant"), "Akane")
        self.assertEqual(resolve_speaker_name("npc:摊主"), "摊主")
        self.assertEqual(resolve_speaker_name("npc"), "NPC")
        self.assertEqual(resolve_speaker_name("user"), "User")

    def test_render_chat_timeline_keeps_npc_name_visible(self) -> None:
        timeline = render_chat_timeline(
            [
                {"role": "user", "content": "这个多少钱", "timestamp": 1712400000},
                {"role": "npc:摊主", "content": "三十文。", "timestamp": 1712400001},
                {"role": "assistant", "content": "要不要我帮你还价？", "timestamp": 1712400002},
            ]
        )

        self.assertIn("User: 这个多少钱", timeline)
        self.assertIn("摊主: 三十文。", timeline)
        self.assertIn("Akane: 要不要我帮你还价？", timeline)
        self.assertIn("日期", timeline)

    def test_render_chat_line_keeps_npc_name_visible(self) -> None:
        line = render_chat_line(role="npc:老板", content="刚出炉的包子。")
        self.assertEqual(line, "老板: 刚出炉的包子。")


class EngineExtensionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        self.engine.resource_manifest = None

    def test_build_retrieval_debug_payload_includes_selected_memory_snippets(self) -> None:
        payload = self.engine._build_retrieval_debug_payload(
            router_output={"need_retrieval": True},
            router_timing={"mode": "ndjson"},
            retrieval_result={
                "filtered_candidate_count": 2,
                "time_filter": {"matched": False},
                "fused_hits": [{"source_id": "a"}, {"source_id": "b"}],
                "memory_snippets": ["snippet A", "snippet B"],
            },
            verifier_output={
                "match_result": "match",
                "need_retry": False,
                "selected_indexes": [2, 1, 2],
            },
            verifier_timing={"mode": "ndjson"},
            confirmed_snippets=["snippet B", "snippet A"],
        )

        self.assertEqual(
            payload["retrieval_result"]["selected_memory_snippets"],
            [
                {"index": 2, "snippet": "snippet B"},
                {"index": 1, "snippet": "snippet A"},
            ],
        )

    def test_resolve_pre_retrieval_enabled_prefers_payload_override(self) -> None:
        with patch.object(config, "PRE_RETRIEVAL_DEFAULT_ENABLED", True):
            self.assertFalse(self.engine._resolve_pre_retrieval_enabled(payload={"pre_retrieval_enabled": False}))
            self.assertTrue(self.engine._resolve_pre_retrieval_enabled(payload={"pre_retrieval_enabled": True}))
            self.assertTrue(self.engine._resolve_pre_retrieval_enabled(payload={}))

        with patch.object(config, "PRE_RETRIEVAL_DEFAULT_ENABLED", False):
            self.assertFalse(self.engine._resolve_pre_retrieval_enabled(payload={}))
            self.assertTrue(self.engine._resolve_pre_retrieval_enabled(payload={"pre_retrieval_enabled": "true"}))

    def test_build_skipped_pre_retrieval_pipeline_returns_skip_defaults(self) -> None:
        class StubRetrievalService:
            def _extract_time_hint(self, *, user_message: str, now_ts: int) -> dict[str, object]:
                return {"date_label": None, "time_of_day": "night", "relative_time": None}

            def _build_shortcut_timing(self, *, stage: str, branch: str, ready_event_type: str | None) -> dict[str, object]:
                return {
                    "stage": stage,
                    "branch": branch,
                    "mode": "shortcut",
                    "ready_event_type": ready_event_type,
                }

        self.engine._retrieval_service = StubRetrievalService()
        self.engine._get_retrieval_service = lambda: self.engine._retrieval_service

        pipeline = self.engine._build_skipped_pre_retrieval_pipeline(
            user_message="今天随便聊聊",
            now_ts=123,
            reason="本轮已关闭前置检索，直接基于当前可见上下文回复。",
        )

        self.assertFalse(pipeline.used_retrieval)
        self.assertEqual(pipeline.router_output["route"], "pre_retrieval_disabled")
        self.assertFalse(pipeline.router_output["need_retrieval"])
        self.assertEqual(pipeline.router_timing["branch"], "pre_retrieval_disabled")
        self.assertEqual(pipeline.verifier_output["match_result"], "skip")
        self.assertEqual(pipeline.retrieval_result["fused_hits"], [])
        self.assertEqual(pipeline.confirmed_snippets, [])

    def test_build_embedding_provider_falls_back_to_hashed_when_huggingface_unavailable(self) -> None:
        with patch.object(config, "EMBEDDING_PROVIDER", "huggingface"), patch.object(
            config, "EMBEDDING_CACHE_SIZE", 0
        ), patch("companion_v01.engine.HuggingFaceEmbeddingProvider", side_effect=RuntimeError("missing deps")):
            provider = self.engine._build_embedding_provider()

        self.assertIsInstance(provider, HashedEmbeddingProvider)

    def test_build_embedding_provider_wraps_huggingface_provider_with_cache(self) -> None:
        class StubHFProvider(BaseEmbeddingProvider):
            provider_name = "stub_hf"
            version = "v-test"

            def __init__(self) -> None:
                super().__init__(dimension=4)

            def embed_text(self, text: str) -> list[float]:
                return [1.0, 0.0, 0.0, 0.0]

        stub_provider = StubHFProvider()
        with patch.object(config, "EMBEDDING_PROVIDER", "auto"), patch.object(
            config, "EMBEDDING_CACHE_SIZE", 32
        ), patch.object(config, "EMBEDDING_MODEL_NAME", "BAAI/bge-m3"), patch.object(
            config, "EMBEDDING_DEVICE", ""
        ), patch.object(config, "EMBEDDING_LOCAL_FILES_ONLY", True), patch.object(
            config, "EMBEDDING_CACHE_FOLDER", "models/cache"
        ), patch("companion_v01.engine.HuggingFaceEmbeddingProvider", return_value=stub_provider) as provider_cls:
            provider = self.engine._build_embedding_provider()

        self.assertIsInstance(provider, CachedEmbeddingProvider)
        self.assertIs(provider.inner, stub_provider)
        provider_cls.assert_called_once_with(
            model_name="BAAI/bge-m3",
            device=None,
            local_files_only=True,
            cache_folder="models/cache",
        )

    def test_run_embedding_reindex_batches_raw_summary_and_semantic_records(self) -> None:
        class StubStore:
            def count_vectorizable_records(self) -> int:
                return 4

            def iter_messages_for_vector_reindex(self, batch_size: int):
                yield [
                    {
                        "source_id": "raw-1",
                        "profile_user_id": "user-1",
                        "session_id": "session-1",
                        "seq_no": 1,
                        "role": "user",
                        "content": "欢迎回来",
                        "timestamp": 1,
                        "date_label": "2026-04-10",
                        "time_of_day": "morning",
                        "semantic_tags": ["欢迎", "回来"],
                    },
                    {
                        "source_id": "raw-2",
                        "profile_user_id": "user-1",
                        "session_id": "session-1",
                        "seq_no": 2,
                        "role": "assistant",
                        "content": "去上课",
                        "timestamp": 2,
                        "date_label": "2026-04-10",
                        "time_of_day": "morning",
                        "semantic_tags": ["上课"],
                    },
                ]

            def iter_summaries_for_vector_reindex(self, batch_size: int):
                yield [
                    {
                        "summary_id": "summary::1",
                        "profile_user_id": "user-1",
                        "session_id": "session-1",
                        "timestamp": 3,
                        "date_label": "2026-04-10",
                        "time_of_day": "afternoon",
                        "diary_summary": "今天回来了",
                        "key_events": ["回来", "上课"],
                        "core_facts": ["等你回来"],
                        "semantic_tags": ["回来"],
                        "source_end_seq": 2,
                    }
                ]

            def iter_semantic_summaries_for_vector_reindex(self, batch_size: int):
                yield [
                    {
                        "semantic_id": "semantic::1",
                        "profile_user_id": "user-1",
                        "session_id": "session-1",
                        "timestamp": 4,
                        "date_label": "2026-04-10",
                        "time_of_day": "night",
                        "semantic_summary": "用户经常提到上课",
                        "stable_facts": ["用户会上课"],
                        "recurring_topics": ["课程"],
                        "important_people": [],
                        "open_loops": ["等会回来"],
                        "semantic_tags": ["上课", "课程"],
                    }
                ]

        class StubVectorStore:
            def __init__(self) -> None:
                self.collection_name = "akane_memory_test"
                self.batches: list[list[dict[str, object]]] = []

            def count_entries(self) -> int:
                return 0

            def upsert_entries(self, entries: list[dict[str, object]]) -> None:
                self.batches.append(entries)

        self.engine.store = StubStore()
        self.engine.vector_store = StubVectorStore()
        self.engine._embedding_reindex_lock = threading.RLock()
        self.engine._embedding_reindex_status = {
            "state": "running",
            "processed": 0,
            "total": 4,
            "started_at": 0.0,
            "finished_at": 0.0,
            "error": "",
            "collection_name": "akane_memory_test",
        }

        with patch.object(config, "EMBEDDING_REINDEX_BATCH_SIZE", 2):
            self.engine._run_embedding_reindex()

        self.assertEqual(len(self.engine.vector_store.batches), 3)
        self.assertEqual(self.engine.vector_store.batches[0][0]["source_id"], "raw-1")
        self.assertEqual(self.engine.vector_store.batches[1][0]["source_id"], "summary::1")
        self.assertEqual(self.engine.vector_store.batches[2][0]["source_id"], "semantic::1")
        self.assertEqual(self.engine._embedding_reindex_status["state"], "completed")
        self.assertEqual(self.engine._embedding_reindex_status["processed"], 4)

    def test_normalize_choices_keeps_short_distinct_items(self) -> None:
        choices = self.engine._normalize_choices(
            [
                {"id": "ask_price", "text": "问问价格"},
                {"text": "问问价格"},
                "先继续往前逛",
                {"label": "故意逗她一下"},
                {"text": "这个会被截断" * 20},
            ]
        )

        self.assertEqual(len(choices), 4)
        self.assertEqual(choices[0], {"id": "ask_price", "text": "问问价格"})
        self.assertEqual(choices[1]["id"], "choice_3")
        self.assertEqual(choices[1]["text"], "先继续往前逛")
        self.assertEqual(choices[2]["text"], "故意逗她一下")
        self.assertLessEqual(len(choices[3]["text"]), 40)

    def test_normalize_final_output_keeps_code_snippet_without_markdown_fence(self) -> None:
        normalized = self.engine._normalize_final_output(
            result={
                "emotion": "normal",
                "speech": "我把示例写给你看。",
                "code_snippet": "```java\nSystem.out.println(\"hi\");\n```",
                "memory_tags": "",
                "status": "final",
                "score": 0.0,
                "tool_call": None,
                "choices": [],
                "character": {"outfit": "default"},
                "scene": {"major": "home", "minor": "room", "background": "night", "bgm": ""},
            },
            visual_defaults={
                "major": "home",
                "minor": "room",
                "background": "night",
                "bgm": "",
                "outfit": "default",
                "emotion": "normal",
            },
            allow_tool_call=True,
            debug_enabled=False,
        )

        self.assertEqual(normalized["code_snippet"], 'System.out.println("hi");')

    def test_normalize_final_output_groups_memory_metadata(self) -> None:
        normalized = self.engine._normalize_final_output(
            result={
                "emotion": "normal",
                "speech": "可乐这件事我记住啦。",
                "speech_segments": [],
                "tool_call": None,
                "code_snippet": "",
                "memory_tags": "旧词,可乐",
                "memory_metadata": {
                    "keywords": ["可乐", "饮料", "可乐"],
                    "subject_scopes": ["用户", "relationship", "topic", "unknown"],
                    "categories": ["偏好", "project_work", "unknown"],
                    "mood_tags": ["开心", "warm", "未知", "吐槽"],
                    "importance": 1.2,
                    "confidence": "0.75",
                },
                "status": "final",
                "score": 0.0,
                "choices": [],
                "character": {"outfit": "default"},
                "scene": {"major": "home", "minor": "room", "background": "night", "bgm": ""},
            },
            visual_defaults={
                "major": "home",
                "minor": "room",
                "background": "night",
                "bgm": "",
                "outfit": "default",
                "emotion": "normal",
            },
            allow_tool_call=True,
            debug_enabled=False,
        )

        self.assertEqual(normalized["memory_metadata"]["keywords"], ["可乐", "饮料", "旧词"])
        self.assertEqual(normalized["memory_metadata"]["subject_scopes"], ["user", "assistant", "other"])
        self.assertEqual(normalized["memory_metadata"]["categories"], ["preference", "project_work"])
        self.assertEqual(normalized["memory_metadata"]["mood_tags"], ["happy", "warm", "playful"])
        self.assertEqual(normalized["memory_metadata"]["importance"], 1.0)
        self.assertEqual(normalized["memory_metadata"]["confidence"], 0.75)
        self.assertNotIn("memory_tags", normalized)

    def test_normalize_final_output_migrates_legacy_memory_tags(self) -> None:
        normalized = self.engine._normalize_final_output(
            result={
                "emotion": "normal",
                "speech": "嗯嗯。",
                "speech_segments": [],
                "tool_call": None,
                "code_snippet": "",
                "memory_tags": "可乐,饮料,喜欢",
                "status": "final",
                "score": 0.0,
                "choices": [],
                "character": {"outfit": "default"},
                "scene": {"major": "home", "minor": "room", "background": "night", "bgm": ""},
            },
            visual_defaults={
                "major": "home",
                "minor": "room",
                "background": "night",
                "bgm": "",
                "outfit": "default",
                "emotion": "normal",
            },
            allow_tool_call=True,
            debug_enabled=False,
        )

        self.assertEqual(normalized["memory_metadata"]["keywords"], ["可乐", "饮料", "喜欢"])
        self.assertEqual(normalized["memory_metadata"]["subject_scopes"], [])
        self.assertEqual(normalized["memory_metadata"]["categories"], [])
        self.assertEqual(normalized["memory_metadata"]["mood_tags"], [])
        self.assertEqual(normalized["memory_metadata"]["importance"], 0.0)
        self.assertEqual(normalized["memory_metadata"]["confidence"], 0.0)
        self.assertNotIn("memory_tags", normalized)

    def test_normalize_final_output_wraps_plain_speech_as_single_segment(self) -> None:
        normalized = self.engine._normalize_final_output(
            result={
                "emotion": "normal",
                "speech": "好的主人。",
                "speech_segments": [],
                "code_snippet": "",
                "memory_tags": "",
                "status": "final",
                "score": 0.0,
                "tool_call": None,
                "choices": [],
                "character": {"outfit": "default"},
                "scene": {"major": "home", "minor": "room", "background": "night", "bgm": ""},
            },
            visual_defaults={
                "major": "home",
                "minor": "room",
                "background": "night",
                "bgm": "",
                "outfit": "default",
                "emotion": "normal",
            },
            allow_tool_call=True,
            debug_enabled=False,
        )

        self.assertEqual(normalized["speech"], "好的主人。")
        self.assertEqual(normalized["speech_segments"], ["好的主人。"])

    def test_normalize_final_output_aggregates_speech_segments_for_memory(self) -> None:
        normalized = self.engine._normalize_final_output(
            result={
                "emotion": "normal",
                "speech": "",
                "speech_segments": ["在的，主人。", "晚上好呀。", "你在做什么？"],
                "code_snippet": "",
                "memory_tags": "",
                "status": "final",
                "score": 0.0,
                "tool_call": None,
                "choices": [],
                "character": {"outfit": "default"},
                "scene": {"major": "home", "minor": "room", "background": "night", "bgm": ""},
            },
            visual_defaults={
                "major": "home",
                "minor": "room",
                "background": "night",
                "bgm": "",
                "outfit": "default",
                "emotion": "normal",
            },
            allow_tool_call=True,
            debug_enabled=False,
        )

        self.assertEqual(normalized["speech"], "在的，主人。\n晚上好呀。\n你在做什么？")
        self.assertEqual(normalized["speech_segments"], ["在的，主人。", "晚上好呀。", "你在做什么？"])

    def test_normalize_final_output_infers_segments_from_multiline_speech(self) -> None:
        normalized = self.engine._normalize_final_output(
            result={
                "emotion": "normal",
                "speech": "哼，知道了知道了。\n不就是这样分开说话嘛。\n看好了！",
                "code_snippet": "",
                "memory_tags": "",
                "status": "final",
                "score": 0.0,
                "tool_call": None,
                "choices": [],
                "character": {"outfit": "default"},
                "scene": {"major": "home", "minor": "room", "background": "night", "bgm": ""},
            },
            visual_defaults={
                "major": "home",
                "minor": "room",
                "background": "night",
                "bgm": "",
                "outfit": "default",
                "emotion": "normal",
            },
            allow_tool_call=True,
            debug_enabled=False,
        )

        self.assertEqual(normalized["speech"], "哼，知道了知道了。\n不就是这样分开说话嘛。\n看好了！")
        self.assertEqual(normalized["speech_segments"], ["哼，知道了知道了。", "不就是这样分开说话嘛。", "看好了！"])

    def test_should_index_user_record_in_vector_defaults_to_true_for_non_retrieval(self) -> None:
        self.assertTrue(
            self.engine._should_index_user_record_in_vector(
                router_output={"need_retrieval": False, "index_current_message": False}
            )
        )

    def test_should_index_user_record_in_vector_respects_false_on_retrieval_branch(self) -> None:
        self.assertFalse(
            self.engine._should_index_user_record_in_vector(
                router_output={"need_retrieval": True, "index_current_message": False}
            )
        )

    def test_normalize_npc_tool_call_accepts_common_aliases(self) -> None:
        normalized = self.engine._normalize_npc_tool_call(
            {
                "type": "call_npc",
                "name": "摊主",
                "role": "卖菜的",
                "question": "这个青菜怎么卖？",
            }
        )

        self.assertEqual(
            normalized,
            {
                "type": "call_npc",
                "npc_name": "摊主",
                "npc_role": "卖菜的",
                "query": "这个青菜怎么卖？",
            },
        )

    def test_normalize_npc_tool_call_rejects_incomplete_payload(self) -> None:
        self.assertIsNone(self.engine._normalize_npc_tool_call(None))
        self.assertIsNone(self.engine._normalize_npc_tool_call({"type": "call_npc"}))
        self.assertIsNone(self.engine._normalize_npc_tool_call({"type": "other", "query": "hi"}))

    def test_build_assistant_dialogue_turn_keeps_preface(self) -> None:
        turn = self.engine._build_assistant_dialogue_turn("我帮你问问摊主。")
        self.assertEqual(
            turn,
            {
                "speaker": PERSONA.assistant_name,
                "speech": "我帮你问问摊主。",
            },
        )

    def test_build_dialogue_turns_preserves_preface_npc_and_followup_order(self) -> None:
        turns = self.engine._build_dialogue_turns(
            preface_turn={"speaker": PERSONA.assistant_name, "speech": "我来替你问一下。"},
            npc_turns=[{"speaker": "摊主", "speech": "二十文一串。"}],
            final_speech="听起来还挺公道的。",
        )

        self.assertEqual(
            turns,
            [
                {"speaker": PERSONA.assistant_name, "speech": "我来替你问一下。"},
                {"speaker": "摊主", "speech": "二十文一串。"},
                {"speaker": PERSONA.assistant_name, "speech": "听起来还挺公道的。"},
            ],
        )

    def test_build_dialogue_turns_expands_final_speech_segments(self) -> None:
        turns = self.engine._build_dialogue_turns(
            preface_turn=None,
            npc_turns=[],
            final_speech="在的，主人。\n晚上好呀。",
            final_speech_segments=["在的，主人。", "晚上好呀。"],
        )

        self.assertEqual(
            turns,
            [
                {"speaker": PERSONA.assistant_name, "speech": "在的，主人。"},
                {"speaker": PERSONA.assistant_name, "speech": "晚上好呀。"},
            ],
        )

    def test_build_dialogue_turns_accepts_multiple_prefaces(self) -> None:
        turns = self.engine._build_dialogue_turns(
            preface_turn=[
                {"speaker": PERSONA.assistant_name, "speech": "我先查一下。"},
                {"speaker": PERSONA.assistant_name, "speech": "我再处理下一步。"},
            ],
            npc_turns=[],
            final_speech="整理好了。",
        )

        self.assertEqual(
            turns,
            [
                {"speaker": PERSONA.assistant_name, "speech": "我先查一下。"},
                {"speaker": PERSONA.assistant_name, "speech": "我再处理下一步。"},
                {"speaker": PERSONA.assistant_name, "speech": "整理好了。"},
            ],
        )

    def test_tool_call_signature_is_stable_for_duplicate_guard(self) -> None:
        left = self.engine._tool_call_signature({"type": "fake_tool", "query": "hello", "count": 1})
        right = self.engine._tool_call_signature({"count": 1, "query": "hello", "type": "fake_tool"})

        self.assertEqual(left, right)

    def test_multi_tool_followup_context_allows_or_blocks_more_tools(self) -> None:
        allow_context = self.engine._build_multi_tool_followup_context(["第 1 次工具结果：ok"], allow_more=True)
        block_context = self.engine._build_multi_tool_followup_context(
            ["第 1 次工具结果：ok"],
            allow_more=False,
            stop_reason="tool_budget_exhausted",
        )

        self.assertIn("可以继续在 tool_call 字段调用下一步必要工具", allow_context)
        self.assertIn("不要为了确认而停下询问", allow_context)
        self.assertIn("工具预算已经用完", block_context)
        self.assertIn("本轮不要再调用工具", block_context)

    def test_tool_round_budget_expands_from_tool_metadata_without_hiding_tools(self) -> None:
        class StubTool:
            def __init__(self, metadata: ToolMetadata) -> None:
                self._metadata = metadata

            def tool_metadata(self) -> ToolMetadata:
                return self._metadata

        self.engine.tool_handlers = {
            "fake_search": StubTool(ToolMetadata(family="web_research", operation="read", risk="low", default_round_budget=8)),
            "fake_browser": StubTool(ToolMetadata(family="browser_control", operation="mixed", risk="medium", default_round_budget=10)),
            "fake_plain": StubTool(ToolMetadata(family="general", operation="mixed", risk="medium", default_round_budget=3)),
        }

        with (
            patch.object(config, "MAX_TOOL_ROUNDS", 3, create=True),
            patch.object(config, "MAX_WEB_RESEARCH_TOOL_ROUNDS", 7, create=True),
            patch.object(config, "MAX_BROWSER_TOOL_ROUNDS", 11, create=True),
        ):
            self.assertEqual(
                self.engine._resolve_tool_round_budget(
                    current_budget=3,
                    tool_call={"type": "fake_search"},
                ),
                7,
            )
            self.assertEqual(
                self.engine._resolve_tool_round_budget(
                    current_budget=3,
                    tool_call={"type": "fake_browser"},
                ),
                11,
            )
            self.assertEqual(
                self.engine._resolve_tool_round_budget(
                    current_budget=3,
                    tool_call={"type": "fake_plain"},
                ),
                3,
            )

    def test_build_tool_prompt_context_includes_registered_tools(self) -> None:
        class StubTool:
            def build_prompt_instruction(self) -> str:
                return "- fake_tool：测试用工具。"

        self.engine.tool_handlers = {"fake_tool": StubTool()}
        prompt = self.engine._build_tool_prompt_context(allow_tool_call=True)

        self.assertIn("当前可调用工具", prompt)
        self.assertIn("\n- fake_tool", prompt)
        self.assertIn("fake_tool", prompt)
        self.assertIn("tool_call 输出 null", prompt)
        self.assertIn("真正调用工具只能写在 tool_call 字段", prompt)

    def test_normalize_tool_call_dispatches_to_registered_handler(self) -> None:
        class StubTool:
            def normalize_call(self, value):
                if value.get("type") != "fake_tool":
                    return None
                return {"type": "fake_tool", "query": "ok"}

        self.engine.tool_handlers = {"fake_tool": StubTool()}
        normalized = self.engine._normalize_tool_call({"type": "fake_tool", "query": "hello"})
        self.assertEqual(normalized, {"type": "fake_tool", "query": "ok"})

    def test_promote_narrated_remote_media_tool_call_from_speech(self) -> None:
        class StubFetchMediaTool:
            def normalize_call(self, value):
                if value.get("type") != "fetch_media_from_url":
                    return None
                urls = list(value.get("urls") or [])
                return {"type": "fetch_media_from_url", "urls": urls}

        self.engine.tool_handlers = {"fetch_media_from_url": StubFetchMediaTool()}
        final_output = {
            "speech": "好的主人，我直接调用工具。工具调用：fetch_media_from_url，目标链接：https://b23.tv/fwo4WAr。",
            "tool_call": None,
        }

        repaired = self.engine._promote_narrated_tool_call(
            final_output,
            user_message="再测试一下下载视频？ https://b23.tv/fwo4WAr",
        )

        self.assertEqual(
            repaired["tool_call"],
            {"type": "fetch_media_from_url", "urls": ["https://b23.tv/fwo4WAr"]},
        )

    def test_promote_narrated_remote_media_tool_call_ignores_explanations(self) -> None:
        class StubFetchMediaTool:
            def normalize_call(self, value):
                if value.get("type") != "fetch_media_from_url":
                    return None
                urls = list(value.get("urls") or [])
                return {"type": "fetch_media_from_url", "urls": urls}

        self.engine.tool_handlers = {"fetch_media_from_url": StubFetchMediaTool()}
        final_output = {
            "speech": "这个工具叫 fetch_media_from_url，参数是 url: https://b23.tv/fwo4WAr。",
            "tool_call": None,
        }

        repaired = self.engine._promote_narrated_tool_call(
            final_output,
            user_message="你这个工具需要传什么参数？",
        )

        self.assertIsNone(repaired["tool_call"])

    def test_tool_prompt_context_is_filtered_by_client_mode(self) -> None:
        class StubTool:
            def __init__(self, name: str) -> None:
                self.name = name

            def build_prompt_instruction(self) -> str:
                return f"- {self.name}：测试用工具。"

            def normalize_call(self, value):
                if value.get("type") != self.name:
                    return None
                return {"type": self.name}

        self.engine.tool_handlers = {
            name: StubTool(name)
            for name in [
                "set_reminder",
                "list_reminders",
                "cancel_reminder",
                "manage_persona",
                "manage_gift",
                "check_inventory",
                "manage_artifact",
                "call_npc",
                "sync_attachment_workspace",
                "inspect_attachment",
                "retry_attachment",
                "clear_attachment_focus",
            ]
        }
        registry = ModeProfileRegistry()
        qq_context = registry.resolve_from_payload({"client_mode": "qq_text"})
        scene_context = registry.resolve_from_payload({"client_mode": "scene_static"})

        qq_prompt = self.engine._build_tool_prompt_context(
            allow_tool_call=True,
            client_context=qq_context,
        )
        scene_prompt = self.engine._build_tool_prompt_context(
            allow_tool_call=True,
            client_context=scene_context,
        )

        self.assertIn("set_reminder", qq_prompt)
        self.assertIn("manage_persona", qq_prompt)
        self.assertIn("sync_attachment_workspace", qq_prompt)
        self.assertIn("inspect_attachment", qq_prompt)
        self.assertIn("retry_attachment", qq_prompt)
        self.assertIn("clear_attachment_focus", qq_prompt)
        self.assertNotIn("manage_gift", qq_prompt)
        self.assertNotIn("check_inventory", qq_prompt)
        self.assertIn("manage_gift", scene_prompt)
        self.assertIn("check_inventory", scene_prompt)
        self.assertNotIn("inspect_attachment", scene_prompt)
        self.assertNotIn("retry_attachment", scene_prompt)
        self.assertNotIn("sync_attachment_workspace", scene_prompt)
        self.assertIsNone(
            self.engine._normalize_tool_call(
                {"type": "manage_gift"},
                client_context=qq_context,
            )
        )
        self.assertEqual(
            self.engine._normalize_tool_call(
                {"type": "manage_gift"},
                client_context=scene_context,
            ),
            {"type": "manage_gift"},
        )

    def test_capability_registry_declares_client_tool_layers(self) -> None:
        registry = CapabilityRegistry()

        scene_tools = registry.tool_names_for_mode(ClientMode.SCENE_STATIC)
        qq_tools = registry.tool_names_for_mode(ClientMode.QQ_TEXT)
        desktop_tools = registry.tool_names_for_mode(ClientMode.DESKTOP_PET)

        self.assertIn("manage_gift", scene_tools)
        self.assertIn("call_npc", scene_tools)
        self.assertIn("web_search", scene_tools)
        self.assertNotIn("open_browser", scene_tools)
        self.assertNotIn("browser_page", scene_tools)
        self.assertNotIn("open_music_search", scene_tools)
        self.assertNotIn("transcribe_media", scene_tools)
        self.assertNotIn("send_sticker", scene_tools)

        self.assertIn("transcribe_media", qq_tools)
        self.assertIn("send_file", qq_tools)
        self.assertIn("send_sticker", qq_tools)
        self.assertIn("web_search", qq_tools)
        self.assertNotIn("open_browser", qq_tools)
        self.assertNotIn("browser_page", qq_tools)
        self.assertNotIn("open_music_search", qq_tools)
        self.assertNotIn("manage_gift", qq_tools)

        self.assertIn("transcribe_media", desktop_tools)
        self.assertIn("send_file", desktop_tools)
        self.assertIn("web_search", desktop_tools)
        self.assertIn("open_browser", desktop_tools)
        self.assertIn("browser_page", desktop_tools)
        self.assertIn("open_music_search", desktop_tools)
        self.assertNotIn("send_sticker", desktop_tools)
        self.assertNotIn("manage_gift", desktop_tools)

        desktop_selection = registry.select(
            CapabilitySnapshot(
                client_mode=ClientMode.DESKTOP_PET,
                has_any_attachment=True,
                has_media_attachment=True,
            )
        )
        self.assertIn("desktop_file_handoff", desktop_selection.module_names)
        self.assertIn("media_workbench", desktop_selection.module_names)
        self.assertIn("internet_access", desktop_selection.module_names)
        self.assertIn("desktop_browser_open", desktop_selection.module_names)
        self.assertIn("desktop_music_request", desktop_selection.module_names)
        self.assertIn("desktop_workspace", desktop_selection.layer_names)
        self.assertIn("shared_media", desktop_selection.layer_names)
        self.assertIn("web", desktop_selection.layer_names)
        self.assertIn("desktop_browser", desktop_selection.layer_names)
        self.assertIn("music_request", desktop_selection.layer_names)
        self.assertNotIn("qq_delivery", desktop_selection.layer_names)
        self.assertIn("convert_media_file", desktop_selection.tool_names)
        self.assertIn("send_file", desktop_selection.tool_names)
        self.assertIn("web_search", desktop_selection.tool_names)
        self.assertIn("open_browser", desktop_selection.tool_names)
        self.assertIn("browser_page", desktop_selection.tool_names)
        self.assertIn("open_music_search", desktop_selection.tool_names)
        self.assertNotIn("send_sticker", desktop_selection.tool_names)

        qq_selection = registry.select(CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT))
        self.assertIn("qq_delivery", qq_selection.layer_names)
        self.assertIn("send_sticker", qq_selection.tool_names)
        self.assertNotIn("media_workbench", qq_selection.module_names)

        scene_selection_with_files = registry.select(
            CapabilitySnapshot(
                client_mode=ClientMode.SCENE_STATIC,
                has_any_attachment=True,
                has_media_attachment=True,
                has_generated_file=True,
                has_media_generated_file=True,
            )
        )
        self.assertIn("web_scene", scene_selection_with_files.layer_names)
        self.assertNotIn("media_workbench", scene_selection_with_files.module_names)
        self.assertNotIn("qq_delivery", scene_selection_with_files.layer_names)
        self.assertNotIn("desktop_workspace", scene_selection_with_files.layer_names)
        self.assertNotIn("send_file", scene_selection_with_files.tool_names)
        self.assertNotIn("convert_media_file", scene_selection_with_files.tool_names)

    def test_desktop_tool_prompt_uses_desktop_layers_without_qq_or_web_tools(self) -> None:
        class StubTool:
            def __init__(self, name: str) -> None:
                self.name = name

            def build_prompt_instruction(self) -> str:
                return f"- {self.name}：测试用工具。"

            def normalize_call(self, value):
                if value.get("type") != self.name:
                    return None
                return {"type": self.name}

        with tempfile.TemporaryDirectory() as temp_dir:
            self.engine.store = MemoryStore(Path(temp_dir))
            self.engine.capability_registry = CapabilityRegistry()
            self.engine.tool_handlers = {
                name: StubTool(name)
                for name in [
                    "set_reminder",
                    "manage_persona",
                    "fetch_media_from_url",
                    "sync_attachment_workspace",
                    "inspect_attachment",
                    "retry_attachment",
                    "clear_attachment_focus",
                    "compose_file",
                    "inspect_media_info",
                    "convert_media_file",
                    "transcribe_media",
                    "send_file",
                    "send_sticker",
                    "manage_gift",
                ]
            }
            desktop_context = ModeProfileRegistry().resolve_from_payload({"client_mode": "desktop_pet"})

            prompt = self.engine._build_tool_prompt_context(
                allow_tool_call=True,
                client_context=desktop_context,
                profile_user_id="master",
                session_id="desktop_pet_test",
            )
            self.assertIn("\n- fetch_media_from_url", prompt)
            self.assertIn("\n- compose_file", prompt)
            self.assertNotIn("\n- sync_attachment_workspace", prompt)
            self.assertNotIn("\n- convert_media_file", prompt)
            self.assertNotIn("\n- send_sticker", prompt)
            self.assertNotIn("\n- manage_gift", prompt)

            media = self.engine.store.add_attachment_inbox_item(
                profile_user_id="master",
                session_id="desktop_pet_test",
                source="desktop_pet",
                kind="audio",
                status="ready",
                origin_name="voice.wav",
                file_ext=".wav",
                detail={"media_info": {"audio": {"codec": "pcm"}}},
                timestamp=100,
            )
            prompt_with_media = self.engine._build_tool_prompt_context(
                allow_tool_call=True,
                client_context=desktop_context,
                profile_user_id="master",
                session_id="desktop_pet_test",
            )

            self.assertIn("\n- sync_attachment_workspace", prompt_with_media)
            self.assertIn("\n- inspect_media_info", prompt_with_media)
            self.assertIn("\n- convert_media_file", prompt_with_media)
            self.assertIn("\n- transcribe_media", prompt_with_media)
            self.assertIn("\n- send_file", prompt_with_media)
            self.assertIn("【桌宠文件交付】", prompt_with_media)
            self.assertIn("delivery_action", prompt_with_media)
            self.assertNotIn("\n- send_sticker", prompt_with_media)
            self.assertNotIn("\n- manage_gift", prompt_with_media)

            qq_context = ModeProfileRegistry().resolve_from_payload({"client_mode": "qq_text"})
            qq_prompt_with_media = self.engine._build_tool_prompt_context(
                allow_tool_call=True,
                client_context=qq_context,
                profile_user_id="master",
                session_id="desktop_pet_test",
            )
            self.assertIn("\n- send_file", qq_prompt_with_media)
            self.assertNotIn("【桌宠文件交付】", qq_prompt_with_media)
            self.assertEqual(
                self.engine._normalize_tool_call(
                    {"type": "convert_media_file", "source_id": media["attachment_handle"]},
                    client_context=desktop_context,
                    profile_user_id="master",
                    session_id="desktop_pet_test",
                ),
                {"type": "convert_media_file"},
            )
            self.assertIsNone(
                self.engine._normalize_tool_call(
                    {"type": "send_sticker"},
                    client_context=desktop_context,
                    profile_user_id="master",
                    session_id="desktop_pet_test",
                )
            )

    def test_capability_registry_keeps_light_hints_and_hides_inactive_tools(self) -> None:
        class StubTool:
            def __init__(self, name: str) -> None:
                self.name = name

            def build_prompt_instruction(self) -> str:
                return f"- {self.name}：测试用工具。"

            def normalize_call(self, value):
                if value.get("type") != self.name:
                    return None
                return {"type": self.name}

        with tempfile.TemporaryDirectory() as temp_dir:
            self.engine.store = MemoryStore(Path(temp_dir))
            self.engine.capability_registry = CapabilityRegistry()
            self.engine.tool_handlers = {
                name: StubTool(name)
                for name in [
                    "set_reminder",
                    "list_reminders",
                    "cancel_reminder",
                    "manage_persona",
                    "fetch_media_from_url",
                    "compose_file",
                    "read_attachment_section",
                    "apply_style_to_existing_file",
                    "inspect_media_info",
                    "separate_audio_stems",
                    "clean_voice_track",
                    "transcribe_media",
                    "prepare_voice_dataset",
                    "inspect_generated_file",
                    "convert_media_file",
                    "send_file",
                    "send_generated_file",
                    "manage_generated_file",
                ]
            }
            qq_context = ModeProfileRegistry().resolve_from_payload({"client_mode": "qq_text"})

            prompt = self.engine._build_tool_prompt_context(
                allow_tool_call=True,
                client_context=qq_context,
                profile_user_id="master",
                session_id="qq_pri_1",
            )

            self.assertIn("可用能力概览", prompt)
            self.assertIn("短任务直接调用工具完成", prompt)
            self.assertIn("文档", prompt)
            self.assertIn("音频/视频", prompt)
            self.assertIn("如果用户只要原视频/原音频，下载后直接交付原文件", prompt)
            self.assertIn("\n- fetch_media_from_url", prompt)
            self.assertIn("\n- compose_file", prompt)
            self.assertNotIn("\n- convert_media_file", prompt)
            self.assertNotIn("\n- separate_audio_stems", prompt)
            self.assertNotIn("\n- clean_voice_track", prompt)
            self.assertNotIn("\n- transcribe_media", prompt)
            self.assertNotIn("\n- prepare_voice_dataset", prompt)
            self.assertNotIn("\n- read_attachment_section", prompt)
            self.assertIsNone(
                self.engine._normalize_tool_call(
                    {"type": "convert_media_file", "source_id": "audio_001"},
                    client_context=qq_context,
                    profile_user_id="master",
                    session_id="qq_pri_1",
                )
            )

    def test_capability_registry_expands_media_document_and_generated_tools(self) -> None:
        class StubTool:
            def __init__(self, name: str) -> None:
                self.name = name

            def build_prompt_instruction(self) -> str:
                return f"- {self.name}：测试用工具。"

            def normalize_call(self, value):
                if value.get("type") != self.name:
                    return None
                return {"type": self.name}

        with tempfile.TemporaryDirectory() as temp_dir:
            self.engine.store = MemoryStore(Path(temp_dir))
            self.engine.capability_registry = CapabilityRegistry()
            self.engine.tool_handlers = {
                name: StubTool(name)
                for name in [
                    "set_reminder",
                    "list_reminders",
                    "cancel_reminder",
                    "manage_persona",
                    "fetch_media_from_url",
                    "sync_attachment_workspace",
                    "inspect_attachment",
                    "retry_attachment",
                    "clear_attachment_focus",
                    "compose_file",
                    "read_attachment_section",
                    "revise_generated_file",
                    "apply_style_to_existing_file",
                    "inspect_media_info",
                    "separate_audio_stems",
                    "clean_voice_track",
                    "transcribe_media",
                    "prepare_voice_dataset",
                    "inspect_generated_file",
                    "convert_media_file",
                    "send_file",
                    "send_generated_file",
                    "manage_generated_file",
                ]
            }
            qq_context = ModeProfileRegistry().resolve_from_payload({"client_mode": "qq_text"})
            media = self.engine.store.add_attachment_inbox_item(
                profile_user_id="master",
                session_id="qq_pri_1",
                source="qq",
                kind="audio",
                status="ready",
                origin_name="song.mp3",
                file_ext=".mp3",
                detail={"media_info": {"audio": {"codec": "mp3"}}},
                timestamp=100,
            )
            document = self.engine.store.add_attachment_inbox_item(
                profile_user_id="master",
                session_id="qq_pri_1",
                source="qq",
                kind="document",
                status="ready",
                origin_name="notes.txt",
                file_ext=".txt",
                detail={"file_kind": "txt", "text_preview": "hello"},
                timestamp=110,
            )
            self.engine.store.add_generated_file(
                profile_user_id="master",
                session_id="qq_pri_1",
                output_title="输出",
                output_format="mp3",
                storage_relpath="generated/out.mp3",
                timestamp=120,
            )

            prompt = self.engine._build_tool_prompt_context(
                allow_tool_call=True,
                client_context=qq_context,
                profile_user_id="master",
                session_id="qq_pri_1",
            )

            self.assertIn("\n- sync_attachment_workspace", prompt)
            self.assertIn("\n- fetch_media_from_url", prompt)
            self.assertIn("\n- read_attachment_section", prompt)
            self.assertIn("\n- separate_audio_stems", prompt)
            self.assertIn("\n- clean_voice_track", prompt)
            self.assertIn("\n- transcribe_media", prompt)
            self.assertIn("\n- prepare_voice_dataset", prompt)
            self.assertIn("\n- convert_media_file", prompt)
            self.assertIn("\n- inspect_generated_file", prompt)
            self.assertIn("\n- send_file", prompt)
            self.assertNotIn("\n- send_generated_file", prompt)
            self.assertIn("\n- manage_generated_file", prompt)
            self.assertEqual(
                self.engine._normalize_tool_call(
                    {"type": "convert_media_file", "source_id": media["attachment_handle"]},
                    client_context=qq_context,
                    profile_user_id="master",
                    session_id="qq_pri_1",
                ),
                {"type": "convert_media_file"},
            )
            self.assertEqual(
                self.engine._normalize_tool_call(
                    {"type": "clean_voice_track", "source_id": media["attachment_handle"]},
                    client_context=qq_context,
                    profile_user_id="master",
                    session_id="qq_pri_1",
                ),
                {"type": "clean_voice_track"},
            )
            self.assertEqual(
                self.engine._normalize_tool_call(
                    {"type": "transcribe_media", "source_ids": [media["attachment_handle"]]},
                    client_context=qq_context,
                    profile_user_id="master",
                    session_id="qq_pri_1",
                ),
                {"type": "transcribe_media"},
            )
            self.assertEqual(
                self.engine._normalize_tool_call(
                    {"type": "prepare_voice_dataset", "source_ids": [media["attachment_handle"]]},
                    client_context=qq_context,
                    profile_user_id="master",
                    session_id="qq_pri_1",
                ),
                {"type": "prepare_voice_dataset"},
            )
            self.assertEqual(
                self.engine._normalize_tool_call(
                    {"type": "separate_audio_stems", "source_id": media["attachment_handle"]},
                    client_context=qq_context,
                    profile_user_id="master",
                    session_id="qq_pri_1",
                ),
                {"type": "separate_audio_stems"},
            )

            self.engine.store.clear_attachment_inbox_items(
                profile_user_id="master",
                session_id="qq_pri_1",
                target="all",
                timestamp=130,
            )
            self.engine.store.update_generated_file(
                profile_user_id="master",
                session_id="qq_pri_1",
                generated_id=self.engine.store.list_generated_files(
                    profile_user_id="master",
                    session_id="qq_pri_1",
                    statuses=["ready"],
                    limit=1,
                )[0]["generated_id"],
                status="removed",
                updated_at=140,
            )
            prompt_after_clear = self.engine._build_tool_prompt_context(
                allow_tool_call=True,
                client_context=qq_context,
                profile_user_id="master",
                session_id="qq_pri_1",
            )

            self.assertIn("音频/视频", prompt_after_clear)
            self.assertIn("\n- fetch_media_from_url", prompt_after_clear)
            self.assertNotIn("\n- sync_attachment_workspace", prompt_after_clear)
            self.assertNotIn("\n- read_attachment_section", prompt_after_clear)
            self.assertNotIn("\n- separate_audio_stems", prompt_after_clear)
            self.assertNotIn("\n- clean_voice_track", prompt_after_clear)
            self.assertNotIn("\n- transcribe_media", prompt_after_clear)
            self.assertNotIn("\n- prepare_voice_dataset", prompt_after_clear)
            self.assertNotIn("\n- convert_media_file", prompt_after_clear)
            self.assertNotIn("\n- inspect_generated_file", prompt_after_clear)
            self.assertNotIn("\n- send_file", prompt_after_clear)
            self.assertNotIn("\n- manage_generated_file", prompt_after_clear)

    def test_media_preset_routing_appears_in_prompt_for_chat_clients(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.engine.store = MemoryStore(Path(temp_dir))
            self.engine.capability_registry = CapabilityRegistry()
            self.engine.tool_handlers = {}
            self.engine.store.add_attachment_inbox_item(
                profile_user_id="master",
                session_id="session",
                source="qq",
                kind="audio",
                status="ready",
                origin_name="test.wav",
                file_ext=".wav",
                detail={"media_info": {"audio": {"codec": "pcm"}}},
                timestamp=100,
            )
            qq_context = ModeProfileRegistry().resolve_from_payload({"client_mode": "qq_text"})
            desktop_context = ModeProfileRegistry().resolve_from_payload({"client_mode": "desktop_pet"})

            no_media_prompt = self.engine._build_tool_prompt_context(
                allow_tool_call=True,
                client_context=qq_context,
                profile_user_id="master",
                session_id="empty_session",
            )
            self.assertNotIn("媒体任务预设路由", no_media_prompt)

            prompt = self.engine._build_tool_prompt_context(
                allow_tool_call=True,
                client_context=qq_context,
                profile_user_id="master",
                session_id="session",
            )

            self.assertIn("媒体任务预设路由", prompt)
            self.assertIn("生成字幕", prompt)
            self.assertIn("transcribe_media output_format=srt", prompt)
            self.assertIn("提取视频音频", prompt)
            self.assertIn("convert_media_file output_format=mp3", prompt)
            self.assertIn("压缩音频", prompt)
            self.assertIn("截取片段", prompt)
            self.assertIn("start_time/end_time", prompt)
            self.assertIn("声音忽大忽小", prompt)
            self.assertIn("normalize_volume", prompt)
            self.assertIn("声音太小", prompt)
            self.assertIn("volume_gain_db", prompt)
            self.assertIn("人声降噪", prompt)
            self.assertIn("clean_voice_track", prompt)
            self.assertIn("人声伴奏分离", prompt)
            self.assertIn("separate_audio_stems", prompt)
            self.assertIn("训练素材切片打包", prompt)
            self.assertIn("prepare_voice_dataset", prompt)
            self.assertIn("只要原文件不处理", prompt)
            self.assertIn("先 inspect_media_info 查当前规格", prompt)
            self.assertIn("人声处理组合", prompt)
            self.assertIn("send_file", prompt)

            desktop_prompt = self.engine._build_tool_prompt_context(
                allow_tool_call=True,
                client_context=desktop_context,
                profile_user_id="master",
                session_id="session",
            )
            self.assertIn("媒体任务预设路由", desktop_prompt)

    def test_web_scene_excludes_all_media_workbench_tools(self) -> None:
        registry = CapabilityRegistry()
        scene_tools = registry.tool_names_for_mode(ClientMode.SCENE_STATIC)
        for tool in {"inspect_media_info", "separate_audio_stems", "clean_voice_track",
                      "transcribe_media", "prepare_voice_dataset", "convert_media_file"}:
            self.assertNotIn(tool, scene_tools, f"{tool} should not be available in web scene mode")

    def test_web_scene_prompt_excludes_media_preset_routing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.engine.store = MemoryStore(Path(temp_dir))
            self.engine.capability_registry = CapabilityRegistry()
            self.engine.tool_handlers = {}
            self.engine.store.add_attachment_inbox_item(
                profile_user_id="master",
                session_id="session",
                source="web",
                kind="audio",
                status="ready",
                origin_name="test.wav",
                file_ext=".wav",
                detail={"media_info": {"audio": {"codec": "pcm"}}},
                timestamp=100,
            )
            scene_context = ModeProfileRegistry().resolve_from_payload({"client_mode": "scene_static"})
            prompt = self.engine._build_tool_prompt_context(
                allow_tool_call=True,
                client_context=scene_context,
                profile_user_id="master",
                session_id="session",
            )
            self.assertNotIn("媒体任务预设路由", prompt)

    def test_execute_tool_call_dispatches_to_registered_handler(self) -> None:
        class StubTool:
            def normalize_call(self, value):
                if value.get("type") != "fake_tool":
                    return None
                return {"type": "fake_tool", "query": "ok"}

            def execute(self, *, call, context):
                return ToolExecutionResult(
                    tool_type=call["type"],
                    followup_context=context.session_id,
                )

        self.engine.tool_handlers = {"fake_tool": StubTool()}
        result = self.engine._execute_tool_call(
            profile_user_id="user-1",
            session_id="session-1",
            tool_call={"type": "fake_tool", "query": "hello"},
            visual_payload={"emotion": "happy"},
            now_ts=123,
        )

        self.assertEqual(
            result,
            ToolExecutionResult(tool_type="fake_tool", followup_context="session-1"),
        )

    def test_set_reminder_tool_handler_creates_reminder_and_followup_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            handler = SetReminderToolHandler(store=store)
            result = handler.execute(
                call={
                    "type": "set_reminder",
                    "content": "复习微积分",
                    "time_text": "明天晚上八点",
                    "date_label": "",
                    "time_of_day": "night",
                    "hour": 20,
                    "minute": 0,
                },
                context=ToolExecutionContext(
                    profile_user_id="user-1",
                    session_id="session-1",
                    now_ts=int(datetime(2024, 4, 10, 0, 0).timestamp()),
                    visual_payload={},
                ),
            )

            self.assertEqual(result.tool_type, "set_reminder")
            self.assertIn("成功设置", result.followup_context)
            claimed = store.claim_due_reminders(
                profile_user_id="user-1",
                session_id="session-1",
                now_ts=int(datetime(2024, 4, 11, 20, 0).timestamp()),
            )
            self.assertEqual(len(claimed), 1)
            self.assertEqual(claimed[0]["content"], "复习微积分")

    def test_set_reminder_tool_handler_supports_relative_offset_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            handler = SetReminderToolHandler(store=store)
            now_ts = int(datetime(2024, 4, 10, 12, 0).timestamp())
            result = handler.execute(
                call={
                    "type": "set_reminder",
                    "content": "喝水",
                    "time_text": "五分钟后",
                    "offset_minutes": 5,
                    "date_label": "",
                    "time_of_day": "",
                    "hour": None,
                    "minute": None,
                },
                context=ToolExecutionContext(
                    profile_user_id="user-1",
                    session_id="session-1",
                    now_ts=now_ts,
                    visual_payload={},
                ),
            )

            claimed = store.claim_due_reminders(
                profile_user_id="user-1",
                session_id="session-1",
                now_ts=now_ts + 5 * 60,
            )
            self.assertEqual(result.tool_type, "set_reminder")
            self.assertEqual(len(claimed), 1)
            self.assertEqual(claimed[0]["content"], "喝水")

    def test_set_reminder_tool_handler_returns_clarification_context_when_time_is_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            handler = SetReminderToolHandler(store=store)
            result = handler.execute(
                call={
                    "type": "set_reminder",
                    "content": "收快递",
                    "time_text": "周末",
                    "date_label": "",
                    "time_of_day": "",
                    "hour": None,
                    "minute": None,
                    "offset_minutes": None,
                },
                context=ToolExecutionContext(
                    profile_user_id="user-1",
                    session_id="session-1",
                    now_ts=int(datetime(2024, 4, 10, 12, 0).timestamp()),
                    visual_payload={},
                ),
            )

            self.assertIn("确认更具体的提醒时间", result.followup_context)
            claimed = store.claim_due_reminders(
                profile_user_id="user-1",
                session_id="session-1",
                now_ts=int(datetime(2024, 4, 20, 12, 0).timestamp()),
            )
            self.assertEqual(claimed, [])

    def test_list_reminders_tool_handler_builds_numbered_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.add_reminder(
                profile_user_id="user-1",
                session_id="session-1",
                content="喝水",
                due_ts=int(datetime(2024, 4, 10, 12, 5).timestamp()),
                raw_time_text="五分钟后",
            )
            store.add_reminder(
                profile_user_id="user-1",
                session_id="session-1",
                content="背单词",
                due_ts=int(datetime(2024, 4, 10, 20, 0).timestamp()),
                raw_time_text="今晚八点",
            )
            handler = ListRemindersToolHandler(store=store)

            result = handler.execute(
                call={"type": "list_reminders", "status": "pending", "limit": 5},
                context=ToolExecutionContext(
                    profile_user_id="user-1",
                    session_id="session-1",
                    now_ts=int(datetime(2024, 4, 10, 12, 0).timestamp()),
                    visual_payload={},
                ),
            )

            self.assertEqual(result.tool_type, "list_reminders")
            self.assertIn("1.", result.followup_context)
            self.assertIn("2.", result.followup_context)
            self.assertIn("喝水", result.followup_context)

    def test_cancel_reminder_tool_handler_cancels_by_target_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.add_reminder(
                profile_user_id="user-1",
                session_id="session-1",
                content="喝水",
                due_ts=int(datetime(2024, 4, 10, 12, 5).timestamp()),
                raw_time_text="五分钟后",
            )
            handler = CancelReminderToolHandler(store=store)

            result = handler.execute(
                call={"type": "cancel_reminder", "target_text": "喝水"},
                context=ToolExecutionContext(
                    profile_user_id="user-1",
                    session_id="session-1",
                    now_ts=int(datetime(2024, 4, 10, 12, 1).timestamp()),
                    visual_payload={},
                ),
            )

            self.assertEqual(result.tool_type, "cancel_reminder")
            self.assertIn("成功取消", result.followup_context)
            pending = store.list_reminders(
                profile_user_id="user-1",
                session_id="session-1",
                status="pending",
            )
            self.assertEqual(pending, [])

    def test_cancel_reminder_tool_handler_asks_for_clarification_when_match_is_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.add_reminder(
                profile_user_id="user-1",
                session_id="session-1",
                content="复习英语",
                due_ts=int(datetime(2024, 4, 10, 18, 0).timestamp()),
                raw_time_text="今晚六点",
            )
            store.add_reminder(
                profile_user_id="user-1",
                session_id="session-1",
                content="复习高数",
                due_ts=int(datetime(2024, 4, 10, 20, 0).timestamp()),
                raw_time_text="今晚八点",
            )
            handler = CancelReminderToolHandler(store=store)

            result = handler.execute(
                call={"type": "cancel_reminder", "target_text": "复习"},
                context=ToolExecutionContext(
                    profile_user_id="user-1",
                    session_id="session-1",
                    now_ts=int(datetime(2024, 4, 10, 12, 1).timestamp()),
                    visual_payload={},
                ),
            )

            self.assertIn("确认具体要取消哪一条", result.followup_context)

    def test_check_inventory_tool_handler_reads_recent_pending_gifts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            service = GiftSystemService(root / "gifts", store=store)
            handler = CheckInventoryToolHandler(gift_service=service)

            service.ingest_upload(
                profile_user_id="user-1",
                session_id="session-1",
                filename="夜色.flac",
                content_type="audio/flac",
                content=b"stub-audio",
                now_ts=100,
            )
            service.ingest_upload(
                profile_user_id="user-1",
                session_id="session-1",
                filename="雨声.flac",
                content_type="audio/flac",
                content=b"stub-audio",
                now_ts=101,
            )

            result = handler.execute(
                call={"type": "check_inventory", "scope": "pending_recent", "limit": 3},
                context=ToolExecutionContext(
                    profile_user_id="user-1",
                    session_id="session-1",
                    now_ts=int(datetime(2024, 4, 10, 12, 1).timestamp()),
                    visual_payload={},
                ),
            )

            self.assertEqual(result.tool_type, "check_inventory")
            self.assertIn("scope=pending_recent", result.followup_context)
            self.assertIn("音乐: 雨声", result.followup_context)
            self.assertEqual(result.stream_events[0]["type"], "inventory_snapshot")

    def test_manage_gift_tool_handler_uses_session_focus(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            service = GiftSystemService(root / "gifts", store=store)
            handler = ManageGiftToolHandler(gift_service=service)

            asset = service.ingest_upload(
                profile_user_id="user-1",
                session_id="session-1",
                filename="夜色.flac",
                content_type="audio/flac",
                content=b"stub-audio",
                now_ts=100,
            )

            result = handler.execute(
                call={"type": "manage_gift", "action": "internalize"},
                context=ToolExecutionContext(
                    profile_user_id="user-1",
                    session_id="session-1",
                    now_ts=int(datetime(2024, 4, 10, 12, 1).timestamp()),
                    visual_payload={},
                    current_user_source_id="msg::gift_intent",
                ),
            )

            updated = store.get_gift_asset(
                profile_user_id="user-1",
                asset_id=str(asset["asset_id"]),
            )
            session = store.get_session("user-1", "session-1")

            self.assertEqual(result.tool_type, "manage_gift")
            self.assertIn("已经把礼物", result.followup_context)
            self.assertEqual(result.stream_events[0]["type"], "gift_updated")
            self.assertIsNotNone(updated)
            self.assertEqual(updated["status"], "internalized")
            self.assertIsNotNone(session)
            self.assertEqual(session["current_gift_focus_asset_id"], asset["asset_id"])

    def test_manage_artifact_tool_handler_claims_focused_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "db")
            gift_service = GiftSystemService(root / "gifts", store=store)
            artifact_service = ArtifactContainerService(store=store)
            handler = ManageArtifactToolHandler(artifact_service=artifact_service)

            asset = gift_service.ingest_upload(
                profile_user_id="user-1",
                session_id="session-1",
                filename="akane_shy.png",
                content_type="image/png",
                content=b"stub-image",
                now_ts=100,
            )

            result = handler.execute(
                call={
                    "type": "manage_artifact",
                    "action": "claim",
                    "display_name": "shy 水手服",
                    "collection_key": "daily_wardrobe",
                    "collection_name": "常服衣柜",
                    "asset_role": "outfit",
                },
                context=ToolExecutionContext(
                    profile_user_id="user-1",
                    session_id="session-1",
                    now_ts=int(datetime(2024, 4, 10, 12, 1).timestamp()),
                    visual_payload={},
                    current_user_source_id="msg::artifact_claim",
                ),
            )

            updated = store.get_gift_asset(
                profile_user_id="user-1",
                asset_id=str(asset["asset_id"]),
            )

            self.assertEqual(result.tool_type, "manage_artifact")
            self.assertEqual(result.stream_events[0]["type"], "artifact_updated")
            self.assertIn("正式认领", result.followup_context)
            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated["display_name"], "shy 水手服")
            self.assertEqual(updated["status"], "internalized")
            self.assertEqual(updated["payload"]["asset_role"], "outfit")
            self.assertEqual(updated["payload"]["projection_role"], "character")
            self.assertEqual(updated["payload"]["character_outfit_id"], "daily_wardrobe")
            self.assertEqual(updated["payload"]["character_emotion_id"], "normal")
            self.assertEqual(updated["payload"]["collection_key"], "daily_wardrobe")
            self.assertEqual(updated["source_ids"], ["msg::artifact_claim"])

    def test_consume_due_reminders_uses_llm_speech_and_persists_into_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
            engine.store = MemoryStore(Path(temp_dir))
            engine.resource_manifest = None
            engine._upsert_raw_record = lambda record: None
            engine._schedule_summary_cycle = lambda **kwargs: None

            class StubLLM:
                def call_chat_json(self, **kwargs):
                    return {"speech": "喵，我来提醒你，该喝水啦。"}

            engine.llm = StubLLM()
            engine.store.add_reminder(
                profile_user_id="user-1",
                session_id="session-1",
                content="喝水",
                due_ts=int(datetime(2024, 4, 10, 12, 5).timestamp()),
                raw_time_text="五分钟后",
            )

            notifications = engine.consume_due_reminders(
                profile_user_id="user-1",
                session_id="session-1",
                now_ts=int(datetime(2024, 4, 10, 12, 6).timestamp()),
            )

            self.assertEqual(len(notifications), 1)
            self.assertEqual(notifications[0]["speech"], "喵，我来提醒你，该喝水啦。")
            rows = engine.store.get_unsummarized_messages("session-1")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["role"], "assistant")
            self.assertEqual(rows[0]["content"], "喵，我来提醒你，该喝水啦。")

    def test_build_memory_snippets_can_render_semantic_summary_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            semantic = store.add_semantic_summary(
                profile_user_id="user-1",
                session_id="session-1",
                timestamp=int(datetime(2026, 4, 10, 20, 0).timestamp()),
                period_start_ts=int(datetime(2026, 4, 1, 8, 0).timestamp()),
                period_end_ts=int(datetime(2026, 4, 10, 20, 0).timestamp()),
                date_label="2026-04-10",
                time_of_day="night",
                importance=0.85,
                semantic_summary="主人最近反复提到课程安排，我记住他这段时间确实在忙学习。",
                stable_facts=["最近课程很多"],
                recurring_topics=["上课", "复习"],
                important_people=["老师"],
                open_loops=["还要继续复习"],
                semantic_tags=["课程", "学习"],
                source_summary_ids=["summary::1"],
                memory_metadata={
                    "keywords": ["课程", "学习"],
                    "subject_scopes": ["user"],
                    "categories": ["plan_goal"],
                    "mood_tags": ["warm", "proud"],
                    "importance": 0.85,
                    "confidence": 0.8,
                },
            )
            service = RetrievalService(
                store=store,
                vector_store=object(),
                llm=object(),
                prompt_builder=PromptBuilder(PERSONA),
            )

            snippets = service._build_memory_snippets([{"source_id": semantic["semantic_id"]}])

            self.assertEqual(len(snippets), 1)
            self.assertIn("长期语义记忆", snippets[0])
            self.assertIn("反复提到课程安排", snippets[0])
            self.assertIn("记忆情绪：warm / proud", snippets[0])
            self.assertIn("相对时间锚点", snippets[0])
            self.assertIn("2026-04-01 08:00 ~ 2026-04-10 20:00", snippets[0])

    def test_build_memory_snippets_can_render_raw_memory_mood(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            raw = store.add_message(
                profile_user_id="user-1",
                session_id="session-1",
                role="user",
                content="我今天其实有点累，但还是想继续推进项目。",
                timestamp=int(datetime(2026, 4, 10, 20, 0).timestamp()),
                memory_metadata={
                    "keywords": ["项目", "疲惫"],
                    "subject_scopes": ["user"],
                    "categories": ["emotion_state", "project_work"],
                    "mood_tags": ["worried", "determined"],
                    "importance": 0.76,
                    "confidence": 0.8,
                },
            )
            service = RetrievalService(
                store=store,
                vector_store=object(),
                llm=object(),
                prompt_builder=PromptBuilder(PERSONA),
            )

            snippets = service._build_memory_snippets([{"source_id": raw["source_id"]}])

            self.assertEqual(len(snippets), 1)
            self.assertIn("原始对话回忆", snippets[0])
            self.assertIn("记忆情绪：worried / determined", snippets[0])
            self.assertIn("继续推进项目", snippets[0])

    def test_visible_summary_timelines_render_memory_mood(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            raw = store.add_message(
                profile_user_id="user-1",
                session_id="session-1",
                role="user",
                content="我喜欢喝可乐。",
                timestamp=int(datetime(2026, 4, 10, 20, 0).timestamp()),
            )
            summary = store.add_summary(
                profile_user_id="user-1",
                session_id="session-1",
                timestamp=int(datetime(2026, 4, 10, 20, 5).timestamp()),
                date_label="2026-04-10",
                time_of_day="night",
                period_label="偏好片段",
                event_type="偏好",
                importance=0.72,
                diary_summary="今天主人说自己喜欢喝可乐，我顺手记了下来。",
                key_events=["今天主人说喜欢喝可乐"],
                core_facts=["用户喜欢喝可乐"],
                semantic_tags=["可乐", "饮料"],
                source_start_seq=raw["seq_no"],
                source_end_seq=raw["seq_no"],
                source_ids=[raw["source_id"]],
                memory_metadata={
                    "keywords": ["可乐", "饮料"],
                    "subject_scopes": ["user"],
                    "categories": ["preference"],
                    "mood_tags": ["warm", "playful"],
                    "importance": 0.72,
                    "confidence": 0.8,
                },
            )
            semantic = store.add_semantic_summary(
                profile_user_id="user-1",
                session_id="session-1",
                timestamp=int(datetime(2026, 4, 10, 20, 10).timestamp()),
                period_start_ts=int(datetime(2026, 4, 10, 20, 0).timestamp()),
                period_end_ts=int(datetime(2026, 4, 10, 20, 10).timestamp()),
                date_label="2026-04-10",
                time_of_day="night",
                importance=0.8,
                semantic_summary="主人有一条稳定的饮料偏好：喜欢可乐。",
                stable_facts=["用户喜欢可乐"],
                recurring_topics=["饮料偏好"],
                important_people=[],
                open_loops=[],
                semantic_tags=["可乐", "饮料"],
                source_summary_ids=[summary["summary_id"]],
                memory_metadata={
                    "keywords": ["可乐", "饮料"],
                    "subject_scopes": ["user"],
                    "categories": ["preference"],
                    "mood_tags": ["warm"],
                    "importance": 0.8,
                    "confidence": 0.84,
                },
            )

            summary_text = render_summary_timeline([summary], store=store)
            semantic_text = render_semantic_summary_timeline([semantic], store=store)

            self.assertIn("记忆情绪：warm / playful", summary_text)
            self.assertIn("记忆情绪：warm", semantic_text)
            self.assertIn("相对时间锚点", summary_text)
            self.assertIn("2026-04-10 20:00 ~ 20:00", summary_text)

    def test_summary_compaction_passes_persona_perspective_to_llm(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            previous_raw = store.add_message(
                profile_user_id="user-1",
                session_id="session-1",
                character_pack_id="mika",
                role="user",
                content="我最近在推进复习计划。",
                timestamp=int(datetime(2026, 4, 10, 20, 0).timestamp()),
            )
            store.add_summary(
                profile_user_id="user-1",
                session_id="session-1",
                character_pack_id="mika",
                timestamp=int(datetime(2026, 4, 10, 20, 5).timestamp()),
                date_label="2026-04-10",
                time_of_day="night",
                period_label="复习片段",
                event_type="学习",
                importance=0.72,
                diary_summary="2026-04-10 晚上，主人提到自己在推进复习计划。",
                key_events=["2026-04-10 晚上主人提到复习计划"],
                core_facts=["用户在 2026-04-10 晚上推进复习计划"],
                semantic_tags=["复习", "学习计划"],
                source_start_seq=previous_raw["seq_no"],
                source_end_seq=previous_raw["seq_no"],
                source_ids=[previous_raw["source_id"]],
            )
            captured_prompts: list[dict[str, str]] = []

            class StubLLM:
                def call_aux_json(self, **kwargs):
                    captured_prompts.append(
                        {
                            "system": str(kwargs.get("system_prompt") or ""),
                            "user": str(kwargs.get("user_prompt") or ""),
                        }
                    )
                    return {
                        "diary_summary": "主人提到自己喜欢喝可乐，我顺手记下来了。",
                        "period_label": "偏好片段",
                        "event_type": "偏好",
                        "importance": 0.72,
                        "key_events": ["主人说喜欢喝可乐"],
                        "core_facts": ["用户喜欢喝可乐"],
                    }

            service = MemoryCompactionService(
                store=store,
                vector_store=object(),
                llm=StubLLM(),
                prompt_builder=PromptBuilder(PERSONA),
                persona_context_provider=lambda **_kwargs: {
                    "system_context": "角色设定：Mika 会认真记住主人的偏好。",
                    "reference_context": "表达侧面：温柔吐槽。",
                },
            )
            try:
                service._summarize_batch(
                    [
                        {
                            "role": "user",
                            "content": "我喜欢喝可乐。",
                            "timestamp": 1712400000,
                        }
                    ],
                    profile_user_id="user-1",
                    session_id="session-1",
                    character_pack_id="mika",
                )
            finally:
                service.close()

            self.assertEqual(len(captured_prompts), 1)
            self.assertIn("[CURRENT CHARACTER MEMORY SELF]", captured_prompts[0]["system"])
            self.assertIn("Mika", captured_prompts[0]["system"])
            self.assertIn("温柔吐槽", captured_prompts[0]["system"])
            self.assertIn("不是这段对话发生过的事实", captured_prompts[0]["system"])
            self.assertIn("可参考的既有阶段摘要", captured_prompts[0]["user"])
            self.assertIn("主人提到自己在推进复习计划", captured_prompts[0]["user"])
            self.assertIn("不要把参考摘要里出现", captured_prompts[0]["user"])

    def test_run_semantic_summary_cycle_creates_semantic_memory_and_marks_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))

            class StubVectorStore:
                def __init__(self) -> None:
                    self.upserts: list[dict[str, str]] = []

                def upsert_entry(self, *, source_id, text, metadata):
                    self.upserts.append(
                        {
                            "source_id": source_id,
                            "text": text,
                            "entry_type": metadata.get("entry_type", ""),
                        }
                    )

                def upsert_entries(self, entries):
                    for entry in entries:
                        self.upsert_entry(
                            source_id=entry.get("source_id"),
                            text=entry.get("text"),
                            metadata=entry.get("metadata") or {},
                        )

            class StubLLM:
                def call_aux_json(self, **kwargs):
                    return {
                        "semantic_summary": "主人这段时间一直围绕课程和复习安排来回确认，我记住这是近期最稳定的主线。",
                        "importance": 0.82,
                        "stable_facts": ["最近在上课", "复习安排很重要"],
                        "recurring_topics": ["课程", "复习"],
                        "important_people": ["老师"],
                        "open_loops": ["还要继续准备考试"],
                    }

            service = MemoryCompactionService(
                store=store,
                vector_store=StubVectorStore(),
                llm=StubLLM(),
                prompt_builder=PromptBuilder(PERSONA),
            )

            for idx in range(5):
                store.add_summary(
                    profile_user_id="user-1",
                    session_id="session-1",
                    timestamp=1000 + idx * 100,
                    date_label="2026-04-10",
                    time_of_day="afternoon",
                    period_label=f"阶段{idx + 1}",
                    event_type="日常",
                    importance=0.6,
                    diary_summary=f"第{idx + 1}段摘要，主人提到课程安排。",
                    key_events=[f"事件{idx + 1}"],
                    core_facts=["课程", "复习"],
                    semantic_tags=["课程", "复习"],
                    source_start_seq=idx * 2 + 1,
                    source_end_seq=idx * 2 + 2,
                    source_ids=[f"msg-{idx}"],
                )

            old_enable = getattr(config, "ENABLE_SEMANTIC_MEMORY", True)
            old_trigger = getattr(config, "EPISODIC_COMPACT_TRIGGER_COUNT", 10)
            old_batch = getattr(config, "EPISODIC_COMPACT_BATCH_SIZE", 5)
            try:
                config.ENABLE_SEMANTIC_MEMORY = True
                config.EPISODIC_COMPACT_TRIGGER_COUNT = 5
                config.EPISODIC_COMPACT_BATCH_SIZE = 5

                service._run_semantic_summary_cycle_with_generation(
                    profile_user_id="user-1",
                    session_id="session-1",
                    generation=0,
                )
            finally:
                config.ENABLE_SEMANTIC_MEMORY = old_enable
                config.EPISODIC_COMPACT_TRIGGER_COUNT = old_trigger
                config.EPISODIC_COMPACT_BATCH_SIZE = old_batch
                service.close()

            semantic_records = store.get_recent_semantic_summaries("user-1", limit=3)
            visible_episodic = store.get_visible_episodic_summaries("user-1", limit=10)
            all_summaries = store.get_recent_summaries("user-1", limit=10)

            self.assertEqual(len(semantic_records), 1)
            self.assertEqual(visible_episodic, [])
            self.assertTrue(all(item["is_semanticized"] == 1 for item in all_summaries))
            self.assertEqual(len(service.vector_store.upserts), 1)
            self.assertEqual(service.vector_store.upserts[0]["entry_type"], "semantic_summary")
            self.assertTrue(service.vector_store.upserts[0]["source_id"].startswith("semantic::"))

    def test_run_semantic_summary_cycle_reinforces_existing_semantic_memory_when_overlap_is_high(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))

            class StubVectorStore:
                def __init__(self) -> None:
                    self.upserts: list[dict[str, str]] = []

                def upsert_entry(self, *, source_id, text, metadata):
                    self.upserts.append(
                        {
                            "source_id": source_id,
                            "text": text,
                            "entry_type": metadata.get("entry_type", ""),
                        }
                    )

                def upsert_entries(self, entries):
                    for entry in entries:
                        self.upsert_entry(
                            source_id=entry.get("source_id"),
                            text=entry.get("text"),
                            metadata=entry.get("metadata") or {},
                        )

            class StubLLM:
                def call_aux_json(self, **kwargs):
                    prompt = str(kwargs.get("user_prompt") or "")
                    if "已有长期语义记忆" in prompt:
                        return {
                            "semantic_summary": "主人最近持续在围绕课程和复习安排来回确认，这条学习主线比之前更明确了。",
                            "importance": 0.9,
                            "stable_facts": ["最近课程很多", "复习安排很重要"],
                            "recurring_topics": ["课程", "复习"],
                            "important_people": ["老师"],
                            "open_loops": ["还要继续准备考试"],
                        }
                    return {
                        "semantic_summary": "主人最近一直在围绕课程和复习安排打转，我记住这条学习主线还在继续。",
                        "importance": 0.82,
                        "stable_facts": ["最近课程很多", "复习安排很重要"],
                        "recurring_topics": ["课程", "复习"],
                        "important_people": [],
                        "open_loops": ["还要继续准备考试"],
                    }

            existing = store.add_semantic_summary(
                profile_user_id="user-1",
                session_id="session-1",
                timestamp=900,
                period_start_ts=700,
                period_end_ts=900,
                date_label="2026-04-01",
                time_of_day="night",
                importance=0.78,
                semantic_summary="主人最近一直在聊课程和复习安排，我记住学习是这段时间最稳定的主线。",
                stable_facts=["最近课程很多"],
                recurring_topics=["课程", "复习"],
                important_people=[],
                open_loops=["还要继续复习"],
                semantic_tags=["课程", "复习", "学习"],
                source_summary_ids=["summary::old"],
            )

            service = MemoryCompactionService(
                store=store,
                vector_store=StubVectorStore(),
                llm=StubLLM(),
                prompt_builder=PromptBuilder(PERSONA),
            )

            for idx in range(5):
                store.add_summary(
                    profile_user_id="user-1",
                    session_id="session-1",
                    timestamp=1000 + idx * 100,
                    date_label="2026-04-10",
                    time_of_day="afternoon",
                    period_label=f"阶段{idx + 1}",
                    event_type="学习",
                    importance=0.7,
                    diary_summary=f"第{idx + 1}段摘要，主人继续提到课程和复习安排。",
                    key_events=[f"复习事件{idx + 1}"],
                    core_facts=["课程", "复习"],
                    semantic_tags=["课程", "复习"],
                    source_start_seq=idx * 2 + 1,
                    source_end_seq=idx * 2 + 2,
                    source_ids=[f"msg-{idx}"],
                )

            old_enable = getattr(config, "ENABLE_SEMANTIC_MEMORY", True)
            old_reinforce = getattr(config, "ENABLE_SEMANTIC_REINFORCEMENT", True)
            old_trigger = getattr(config, "EPISODIC_COMPACT_TRIGGER_COUNT", 10)
            old_batch = getattr(config, "EPISODIC_COMPACT_BATCH_SIZE", 5)
            old_lookback = getattr(config, "SEMANTIC_REINFORCEMENT_LOOKBACK", 8)
            old_overlap = getattr(config, "SEMANTIC_REINFORCEMENT_MIN_OVERLAP", 2)
            try:
                config.ENABLE_SEMANTIC_MEMORY = True
                config.ENABLE_SEMANTIC_REINFORCEMENT = True
                config.EPISODIC_COMPACT_TRIGGER_COUNT = 5
                config.EPISODIC_COMPACT_BATCH_SIZE = 5
                config.SEMANTIC_REINFORCEMENT_LOOKBACK = 5
                config.SEMANTIC_REINFORCEMENT_MIN_OVERLAP = 2

                service._run_semantic_summary_cycle_with_generation(
                    profile_user_id="user-1",
                    session_id="session-1",
                    generation=0,
                )
            finally:
                config.ENABLE_SEMANTIC_MEMORY = old_enable
                config.ENABLE_SEMANTIC_REINFORCEMENT = old_reinforce
                config.EPISODIC_COMPACT_TRIGGER_COUNT = old_trigger
                config.EPISODIC_COMPACT_BATCH_SIZE = old_batch
                config.SEMANTIC_REINFORCEMENT_LOOKBACK = old_lookback
                config.SEMANTIC_REINFORCEMENT_MIN_OVERLAP = old_overlap
                service.close()

            semantic_records = store.get_recent_semantic_summaries("user-1", limit=5)
            visible_episodic = store.get_visible_episodic_summaries("user-1", limit=10)
            updated_existing = store.get_semantic_summary_by_id(existing["semantic_id"])

            self.assertEqual(len(semantic_records), 1)
            self.assertEqual(visible_episodic, [])
            self.assertEqual(updated_existing["semantic_id"], existing["semantic_id"])
            self.assertEqual(updated_existing["reinforcement_count"], 2)
            self.assertGreaterEqual(updated_existing["period_end_ts"], 1400)
            self.assertIn("老师", updated_existing["important_people"])
            self.assertEqual(len(updated_existing["source_summary_ids"]), 6)
            self.assertEqual(len(service.vector_store.upserts), 1)
            self.assertEqual(service.vector_store.upserts[0]["source_id"], existing["semantic_id"])


if __name__ == "__main__":
    unittest.main()
