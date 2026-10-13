"""Exercise quoted history through the real MemCore compaction boundary."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import unicodedata
from unittest.mock import patch

from companion_v01.assistant_history_projection import ASSISTANT_HISTORY_RECORD_HEADER
from companion_v01.memcore_integration.history_notice import HISTORY_NOTICE_KIND, HISTORY_NOTICE_VERSION
from companion_v01.memcore_integration.manager import MemcoreManager
from tests.test_memcore_integration import _FakeEmbeddingProvider


class CapturingSummaryLLM:
    def __init__(self):
        self.calls = []
        self.fail = False

    def call_memcore_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            return kwargs["fallback"]
        return {"diary_summary": "用户喜欢少糖红茶，助手记下了这个偏好。",
                "importance": 0.6, "key_events": ["记下红茶偏好"],
                "core_facts": ["用户喜欢少糖红茶"], "memory_title": "红茶偏好",
                "catalog_hint": "饮品偏好", "topic_headings": ["红茶"], "memory_metadata": {}}


class HistoryQuotesCompactionTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        data_patch = patch("config.DATA_DIR", Path(directory.name))
        data_patch.start()
        self.addCleanup(data_patch.stop)
        format_patch = patch("companion_v01.assistant_history_projection.ASSISTANT_HISTORY_FORMAT", "text")
        format_patch.start()
        self.addCleanup(format_patch.stop)
        self.llm = CapturingSummaryLLM()
        self.manager = MemcoreManager(backend="memcore", storage_path=Path(directory.name) / "memory.db",
            visible_scope="conversation", enable_flavor=False, shadow_compare=False,
            llm=self.llm, embedding_provider=_FakeEmbeddingProvider(),
            history_notice_policy_provider=lambda _: {"language": "日语", "style_mode": "description"})
        self.addCleanup(self.manager.close)
        self.assertTrue(self.manager.available, self.manager.status())
        self.scope = dict(profile_user_id="owner", session_id="quoted-history", character_pack_id="cecilia")
        self.metadata = {"about_roles": ["user"], "memory_facets": ["preference"],
                         "topic_terms": ["少糖红茶"], "retrieval_priority": "high"}
        self.units = [{"emotion": "smile", "text": "记住啦，你喜欢少糖红茶。", "say": "覚えました。" * 100,
                       "style": "穏やかに、親しみを込めて"},
                      {"emotion": "shy", "text": "下次一起喝茶吧。", "say": "今度一緒にお茶を飲もう。",
                       "style": "soft"}]
        self.raw = json.dumps({"emotion": "smile", "utterances": self.units,
                               "memory_metadata": self.metadata}, ensure_ascii=False)
        opened = self.manager.begin_input_turn({"source_id": "user", "content": "我喜欢少糖红茶。",
                                               "timestamp": 100}, **self.scope)
        self.assertTrue(opened["ok"], opened)
        self.manager.build_context_projection(provider_profile="openai", **self.scope)
        completed = self.manager.complete_input_turn(turn_id=opened["turn_id"],
            assistant_record={"source_id": "final", "content": "\n".join(unit["text"] for unit in self.units),
                              "timestamp": 101}, memory_metadata=self.metadata, provider_output_raw=self.raw,
            provider_profile="openai", provider_projection={"role": "assistant", "content": self.raw}, **self.scope)
        self.assertTrue(completed["ok"], completed)
        opened = self.manager.begin_input_turn({"source_id": "recent-user", "content": "继续聊聊。",
                                               "timestamp": 102}, **self.scope)
        self.assertTrue(opened["ok"], opened)
        self.system = self.manager._get_system(**self.scope)
        self.system.config = replace(self.system.config, compaction_min_recent_turns=1,
            raw_token_batch_ratio=0.99, episodic_compact_trigger_count=1000)
        self.system._compaction.config = self.system.config
        context = self.manager.build_context_projection(provider_profile="openai", **self.scope)
        self.assertTrue(context["ok"], context)
        self.before = context["payloads"]
        self.original = self.system.store.get_entry(namespace=self.system.namespace, source_id="final")

    def notices(self):
        return [entry for entry in self.system.store.list_prompt_visible_entries(namespace=self.system.namespace)
                if entry.kind == HISTORY_NOTICE_KIND]

    def budgets(self):
        projections = [projection
            for bundle in self.system.store.list_compaction_bundles(namespace=self.system.namespace)
            for projection in self.system._compaction._freeze_bundle_projection(
                self.system.namespace, bundle, "openai_chat")]
        counter = self.system._compaction._count_projection_tokens
        compact = sum(counter(projection) for projection in projections)
        full = sum(counter(replace(projection, payload={"role": "assistant", "content": self.raw}))
                   if "final" in projection.source_ids else counter(projection) for projection in projections)
        return compact, full

    def test_budget_uses_frozen_quotes_and_success_keeps_semantics_and_refreshes_notice(self):
        compact, full = self.budgets()
        self.assertLess(compact, full)
        self.assertIs(self.system._compaction.projection_adapter, self.system._projection)
        final = next(payload["content"] for payload in self.before
                     if payload.get("role") == "assistant" and ASSISTANT_HISTORY_RECORD_HEADER in payload.get("content", ""))
        for unit in self.units:
            self.assertIn(unit["text"], final)
            self.assertNotIn(unit["say"], final)
        self.system.config.raw_token_trigger = (compact + full) // 2
        not_due = self.manager.compact_due_sync(provider_profile="openai", **self.scope)["stats"]
        self.assertEqual(not_due["status"], "not_due", not_due)
        self.assertEqual(not_due["before_raw_projected_tokens"], compact)
        self.assertEqual(self.llm.calls, [])

        old_note, = self.notices()
        self.system.config.raw_token_trigger = compact
        result = self.manager.compact_due_sync(provider_profile="openai", **self.scope)
        self.assertTrue(result["ok"], result)
        stats = result["stats"]
        self.assertEqual(stats["status"], "compacted", stats)
        self.assertIn("final", stats["summary_source_ids"])
        self.assertIn(old_note.source_id, stats["summary_source_ids"])
        self.assertEqual(len(self.llm.calls), 1)
        prompt = self.llm.calls[0]["user_prompt"]
        for text in ("我喜欢少糖红茶。", *(unit["text"] for unit in self.units)):
            self.assertIn(unicodedata.normalize("NFKC", text), prompt)
        self.assertNotIn("【系统说明：历史语音字段】", prompt)
        self.assertNotIn(self.units[0]["say"], prompt)
        summaries = self.system.store.get_visible_episodic_summaries(namespace=self.system.namespace, limit=10)
        summary, = [item for item in summaries if item["kind"] == "memory.episode_summary"]
        self.assertEqual(summary["memory_metadata"]["topic_terms"], ["少糖红茶"])
        self.assertEqual(summary["memory_metadata"]["retrieval_priority"], "high")
        stored = self.system.store.get_entry(namespace=self.system.namespace, source_id="final")
        self.assertEqual(stored.payload, self.original.payload)
        self.assertEqual(stored.semantic_text, self.original.semantic_text)
        self.assertEqual(stored.memory_metadata, self.original.memory_metadata)
        self.assertNotIn("final", [entry.source_id for entry in
            self.system.store.list_prompt_visible_entries(namespace=self.system.namespace)])
        note, = self.notices()
        self.assertNotEqual(note.source_id, old_note.source_id)
        self.assertEqual(note.payload["notice_version"], HISTORY_NOTICE_VERSION)
        self.assertEqual(note.payload["compaction_generation"], stats["compaction_generation"])
        self.assertIn("助手当时输出的是 JSON", note.semantic_text)
        repeated = self.manager._ensure_history_projection_notice(self.system, compaction_stats=stats)
        self.assertEqual(repeated["source_id"], note.source_id)
        self.assertEqual(len(self.notices()), 1)
        after = self.manager.build_context_projection(provider_profile="openai", **self.scope)["payloads"]
        self.assertEqual(after, self.manager.build_context_projection(provider_profile="openai", **self.scope)["payloads"])
        self.assertIn(self.before[-1], after)

    def test_failed_summary_preserves_history_generation_and_notice_until_retry(self):
        compact, _ = self.budgets()
        self.system.config.raw_token_trigger = compact
        generation = self.system.store.get_conversation_generations(namespace=self.system.namespace)
        original_note, = self.notices()
        self.llm.fail = True
        failed = self.manager.compact_due_sync(provider_profile="openai", **self.scope)["stats"]
        self.assertEqual(failed["status"], "failed", failed)
        self.assertEqual(failed["reason"], "summary_retry_pending")
        self.assertEqual(self.system.store.get_conversation_generations(namespace=self.system.namespace), generation)
        self.assertEqual(self.notices()[0].source_id, original_note.source_id)
        self.assertTrue(self.system.store.get_entry(namespace=self.system.namespace, source_id="final").prompt_visible)
        self.assertEqual(self.before, self.manager.build_context_projection(provider_profile="openai", **self.scope)["payloads"])
        self.llm.fail = False
        retried = self.manager.compact_due_sync(provider_profile="openai", **self.scope)["stats"]
        self.assertEqual(retried["status"], "compacted", retried)
        self.assertNotEqual(self.notices()[0].source_id, original_note.source_id)


if __name__ == "__main__":
    unittest.main()
