"""Deployment smoke: MemCore keyword/participant catalogs through Akane's real tools."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import config
from memcore import Actor
from companion_v01.capability_registry import BROWSE_MEMORY_TOOL_SPEC
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.memcore_integration.timeline import MemcoreTimelineToolService
from companion_v01.tool_runtime import BrowseMemoryToolHandler, OpenMemoryToolHandler, ToolExecutionContext
from tests.test_memcore_integration import _FakeEmbeddingProvider, _FakeLLM


class MemcoreKeywordCatalogSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.manager = MemcoreManager(
            backend="memcore",
            storage_path=Path(temporary.name) / "memory.db",
            visible_scope="user",
            enable_flavor=False,
            shadow_compare=False,
            llm=_FakeLLM(),
            embedding_provider=_FakeEmbeddingProvider(),
        )
        self.addCleanup(self.manager.close)
        self.scope = dict(profile_user_id="keyword-smoke", session_id="catalog-smoke", character_pack_id="fixture")
        self.system = self.manager._get_system(**self.scope)
        self.service = MemcoreTimelineToolService(legacy_service=None, memcore_manager=self.manager)
        self.handler = BrowseMemoryToolHandler(timeline_service=self.service)
        self.context = ToolExecutionContext(**self.scope, now_ts=100, visual_payload={})
        self.backend = patch.object(config, "MEMORY_BACKEND", "memcore")
        self.backend.start()
        self.addCleanup(self.backend.stop)

    def _seed(self, memory_id: str, *, timestamp: int = 100) -> None:
        source_id = memory_id + "-raw"
        self.system.store.add_message(
            namespace=self.system.namespace,
            source_id=source_id,
            role="user",
            content="讨论音画同步。",
            timestamp=timestamp,
            annotation_status="accepted_host",
            memory_metadata={"topic_terms": ["音画同步"]},
        )
        self.system.store.add_summary(
            namespace=self.system.namespace,
            record={
                "summary_id": memory_id,
                "timestamp": timestamp,
                "memory_title": "配音制作",
                "diary_summary": "讨论配音和字幕。",
                "source_ids": [source_id],
                "memory_metadata": {"entity_anchors": ["GPT-SoVITS"], "topic_terms": ["字幕"]},
            },
        )

    def _execute(self, **arguments):
        call = self.handler.normalize_call({"type": "browse_memory", **arguments})
        return self.handler.execute(call=call, context=self.context)

    def _seed_participants(self, memory_id: str, actors: list[tuple[str, str]], *, timestamp: int = 100) -> None:
        source_ids = []
        for i, (actor_id, name) in enumerate(actors):
            source_id = f"{memory_id}-actor-{i}"
            source_ids.append(source_id)
            self.system.store.add_message(
                namespace=replace(self.system.namespace, actor=Actor(stable_id=actor_id, display_name=name)),
                source_id=source_id,
                role="user",
                content="讨论答辩，并提到了旁观者。",
                timestamp=timestamp + i,
            )
        self.system.store.add_summary(
            namespace=self.system.namespace,
            record={
                "summary_id": memory_id,
                "timestamp": timestamp + len(actors),
                "memory_title": "答辩方案讨论",
                "diary_summary": "讨论答辩方案。",
                "source_ids": source_ids,
                "memory_metadata": {"topic_terms": ["答辩"]},
            },
        )

    def test_package_schema_source_and_summary_keywords_reach_model_result(self) -> None:
        self._seed("episode-one")
        self.assertIn("keywords", BROWSE_MEMORY_TOOL_SPEC.input_schema["properties"])
        self.assertIn("keyword_match", BROWSE_MEMORY_TOOL_SPEC.input_schema["properties"])
        result = self._execute(keywords=["音画同步", "字幕"], keyword_match="all")
        payload = json.loads(result.followup_context.split("\n", 1)[1])
        self.assertEqual(payload["status"], "ok", payload)
        self.assertEqual(payload["cards"][0]["memory_id"], "episode-one")
        self.assertEqual(payload["cards"][0]["topic_terms"], ["字幕"])
        self.assertEqual(payload["cards"][0]["matched_terms"], ["音画同步", "字幕"])
        self.assertIn("period_start_at", payload["cards"][0])
        self.assertEqual(result.trace_receipt["selector"]["keywords"], ["音画同步", "字幕"])
        self.assertTrue(result.followup_envelope.complete)

    def test_cursor_and_invalid_filter_survive_host_adapters(self) -> None:
        self._seed("episode-one")
        self._seed("episode-two", timestamp=200)
        first = self._execute(keywords=["字幕"], page_size=1)
        self.assertFalse(first.followup_envelope.complete)
        second = self._execute(**first.followup_envelope.continuation)
        payload = json.loads(second.followup_context.split("\n", 1)[1])
        self.assertEqual(payload["cards"][0]["memory_id"], "episode-two")
        self.assertEqual(payload["keywords"], ["字幕"])
        self.assertTrue(second.followup_envelope.complete)
        invalid = self._execute(keywords=["字幕"], keyword_match="fuzzy")
        payload = json.loads(invalid.followup_context.split("\n", 1)[1])
        self.assertEqual(payload["status"], "invalid_arguments")
        self.assertEqual(payload["reason"], "invalid_keyword_match")

    def test_partial_keywords_keep_stored_witnesses_through_real_host_chain(self) -> None:
        self._seed("episode-one")
        result = self._execute(keywords=["音画", "同步", "GPT"], keyword_match="all")
        payload = json.loads(result.followup_context.split("\n", 1)[1])
        self.assertEqual(payload["status"], "ok", payload)
        self.assertEqual(
            payload["cards"][0]["keyword_hits"],
            [
                {"query": "音画", "term": "音画同步"},
                {"query": "同步", "term": "音画同步"},
                {"query": "GPT", "term": "GPT-SoVITS"},
            ],
        )
        self.assertEqual(result.trace_receipt["selector"]["keywords"], ["音画", "同步", "GPT"])
        self.assertIn("one-way", BROWSE_MEMORY_TOOL_SPEC.description)
        reverse = self._execute(keywords=["GPT-SoVITS 配音模型"])
        payload = json.loads(reverse.followup_context.split("\n", 1)[1])
        self.assertEqual(payload["status"], "empty")

    def test_participant_schema_qq_numbers_names_and_single_readable_list(self) -> None:
        self._seed_participants("episode", [("qq:123456", "旧名"), ("qq:654321", "小周"), ("qq:123456", "小林")])
        schema = BROWSE_MEMORY_TOOL_SPEC.input_schema["properties"]
        self.assertIn("participants", schema)
        self.assertIn("participant_match", schema)
        result = self._execute(participants=["123456", "小周"], participant_match="all", keywords=["答辩"])
        payload = json.loads(result.followup_context.split("\n", 1)[1])
        self.assertEqual(payload["status"], "ok", payload)
        card = payload["cards"][0]
        self.assertEqual(card["participants"], ["小林（QQ 123456）", "小周（QQ 654321）"])
        self.assertNotIn("participant_refs", card)
        self.assertEqual(card["topic_terms"], ["答辩"])
        rendered_card = json.dumps(card["participants"], ensure_ascii=False)
        self.assertEqual(rendered_card.count("123456"), 1)
        self.assertEqual(rendered_card.count("小林"), 1)
        self.assertEqual(result.trace_receipt["selector"]["participants"], ["qq:123456", "小周"])
        empty = self._execute(participants=["旁观者"])
        self.assertEqual(json.loads(empty.followup_context.split("\n", 1)[1])["status"], "empty")

    def test_same_nickname_missing_name_and_non_qq_identity_are_honest(self) -> None:
        self._seed_participants(
            "episode",
            [
                ("qq:123456", "同名"),
                ("qq:654321", "同名"),
                ("qq:765432", "765432"),
                ("qq:111222", ""),
                ("matrix:member", "其他平台"),
            ],
        )
        result = self._execute(participants=["同名"])
        card = json.loads(result.followup_context.split("\n", 1)[1])["cards"][0]
        self.assertEqual(
            card["participants"],
            [
                "同名（QQ 123456）",
                "同名（QQ 654321）",
                "QQ 765432",
                "QQ 111222",
                "其他平台（ID matrix:member）",
            ],
        )
        short = self._execute(participants=["同"])
        self.assertEqual(json.loads(short.followup_context.split("\n", 1)[1])["status"], "empty")
        stored = self.system.open_memory(memory_id="episode", view="card")["result"]
        self.assertIn("participant_refs", stored)
        self.assertNotIn("participants", stored)

    def test_participant_cursor_errors_and_batch_open_keep_one_list(self) -> None:
        self._seed_participants("first", [("qq:123456", "小林")], timestamp=100)
        self._seed_participants("second", [("qq:123456", "小林")], timestamp=200)
        first = self._execute(participants=["123456"], page_size=1)
        self.assertFalse(first.followup_envelope.complete)
        second = self._execute(**first.followup_envelope.continuation)
        payload = json.loads(second.followup_context.split("\n", 1)[1])
        self.assertEqual(payload["cards"][0]["memory_id"], "second")
        self.assertEqual(payload["participants"], ["qq:123456"])
        self.assertEqual(second.trace_receipt["selector"]["participants"], ["qq:123456"])
        for arguments in ({"participants": [123456]}, {"participants": [" "]}, {"participant_match": "all"}):
            invalid = self._execute(**arguments)
            self.assertIn(
                json.loads(invalid.followup_context.split("\n", 1)[1])["status"],
                ("invalid_arguments", "invalid_filter"),
            )
        handler = OpenMemoryToolHandler(timeline_service=self.service)
        call = handler.normalize_call({"type": "open_memory", "memory_ids": ["first", "second"], "view": "content"})
        opened = handler.execute(call=call, context=self.context)
        rendered = opened.followup_context
        payload = json.loads(rendered.split("\n", 2)[1])
        for item in payload["result"]["items"]:
            self.assertEqual(item["result"]["card"]["participants"], ["小林（QQ 123456）"])
        self.assertNotIn("participant_refs", rendered)


if __name__ == "__main__":
    unittest.main()
