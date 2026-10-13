"""Real MemCore timeline, compaction and provider boundaries for the notice."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, patch

import config
from companion_v01.engine_services import response_builder
from companion_v01.memcore_integration.history_notice import (
    HISTORY_NOTICE_KIND, HISTORY_NOTICE_VERSION, build_history_projection_notice,
)
from companion_v01.memcore_integration.manager import MemcoreManager
from tests.test_memcore_integration import _PromptContextEngine


class MemcoreHistoryNoticeTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "memory.db"
        self.scope = {"profile_user_id": "owner", "session_id": "conversation", "character_pack_id": "cecilia"}
        self.policy = {"language": "日语", "style_mode": "description"}
        self.provider = Mock(return_value=self.policy)
        self.manager = self.make_manager()

    def make_manager(self):
        manager = MemcoreManager(backend="memcore", storage_path=self.path,
            visible_scope="conversation", enable_flavor=False, shadow_compare=False,
            llm=object(), embedding_provider=None, retrieval_mode="navigation",
            history_notice_policy_provider=self.provider)
        self.addCleanup(manager.close)
        self.assertTrue(manager.available, manager.status())
        return manager

    def system(self, manager=None):
        return (manager or self.manager)._get_system(**self.scope)

    def notices(self, manager=None):
        manager = manager or self.manager
        return [entry for entry in manager._store.list_prompt_visible_entries(namespace=self.system(manager).namespace)
                if entry.kind == HISTORY_NOTICE_KIND]

    def open_input(self, source="input"):
        opened = self.manager.begin_input_turn({"source_id": source, "content": "欢迎回来", "timestamp": 100}, **self.scope)
        self.assertTrue(opened["ok"], opened)
        return opened

    def configure_compaction(self):
        self.manager._memory_config = replace(self.manager._memory_config,
            raw_token_trigger=1, raw_token_batch_ratio=0.9,
            compaction_min_recent_turns=1, episodic_compact_trigger_count=1000)

    def test_activation_is_host_material_with_plain_clear_provider_text(self):
        self.open_input()
        note, = self.notices()
        self.assertEqual(note.origin.value, "environment")
        self.assertIsNone(note.turn_role)
        self.assertFalse(note.semanticize)
        self.assertEqual(note.retrieval_policy.value, "never")
        self.assertEqual(note.retrieval_visibility.value, "never")
        self.assertEqual(note.trust.value, "trusted_instruction")
        self.assertIsNone(self.manager._index)
        self.assertEqual(note.memory_metadata, {})
        self.assertEqual(note.semantic_text, build_history_projection_notice(self.policy))
        for phrase in ("系统为了节省上下文", "原文另行保存", "完整、自然日语台词", "用简短日语描述", "调用工具时沿用工具协议"):
            self.assertIn(phrase, note.semantic_text)
        for phrase in ("不要", "不能", "禁止", "本轮", "可省略"):
            self.assertNotIn(phrase, note.semantic_text)
        sample = json.loads(next(line for line in note.semantic_text.splitlines() if line.startswith("{")))
        self.assertEqual(list(sample), ["emotion", "utterances", "memory_metadata"])
        self.assertEqual(len(sample["utterances"]), 2)
        for unit in sample["utterances"]:
            self.assertEqual(list(unit), ["emotion", "style", "text", "say"])
            self.assertRegex(unit["say"], "[ぁ-ゖァ-ヺ]")
            self.assertRegex(unit["style"], "[ぁ-ゖァ-ヺ]")
            self.assertNotIn("[hidden]", json.dumps(unit, ensure_ascii=False))
        self.assertNotIn("[hidden]", note.semantic_text)
        for protocol in ("openai", "deepseek", "anthropic", "canonical"):
            surface = self.manager.build_context_surface(provider_profile=protocol, current_source_id="input", **self.scope)
            self.assertTrue(surface["ok"], surface)
            expected = ([{"type": "text", "text": note.semantic_text}]
                        if protocol == "anthropic" else note.semantic_text)
            self.assertTrue(any(message.get("content") == expected for message in surface["history_messages"]), protocol)
            self.assertNotIn(note.source_id, str(surface["history_messages"]))

    def test_japanese_aliases_and_reference_styles_use_the_current_speech_contract(self):
        for language in ("日语", "日本語", "ja"):
            with self.subTest(language=language):
                notice = build_history_projection_notice({"language": language, "style_mode": "description"})
                self.assertEqual(notice, build_history_projection_notice(self.policy))
        reference = build_history_projection_notice({"language": "日语", "default_style": "soft_reference"})
        sample = json.loads(next(line for line in reference.splitlines() if line.startswith("{")))
        self.assertTrue(all(unit["style"] == "soft_reference" for unit in sample["utterances"]))
        self.assertEqual(build_history_projection_notice({}), "")
        other_language = build_history_projection_notice({"language": "英语", "style_mode": "description"})
        self.assertNotIn("おかえり", other_language)

    def test_example_upgrade_appends_once_and_preserves_the_old_frozen_prefix(self):
        old_text = build_history_projection_notice(self.policy, include_example=False)
        with patch("companion_v01.memcore_integration.history_notice.HISTORY_NOTICE_VERSION", 1), \
                patch("companion_v01.memcore_integration.history_notice.build_history_projection_notice", return_value=old_text):
            self.open_input()
        old_note, = self.notices()
        before = self.manager.build_context_projection(provider_profile="openai", **self.scope)
        self.manager.close()
        reopened = self.make_manager()
        system = self.system(reopened)
        first = reopened._ensure_history_projection_notice(system)
        second = reopened._ensure_history_projection_notice(system)
        self.assertEqual(first["source_id"], second.get("source_id", first["source_id"]))
        old, new = self.notices(reopened)
        self.assertEqual(old.source_id, old_note.source_id)
        self.assertEqual(old.semantic_text, old_text)
        self.assertEqual(new.payload["notice_version"], HISTORY_NOTICE_VERSION)
        self.assertNotEqual(new.source_id, old.source_id)
        after = reopened.build_context_projection(provider_profile="openai", **self.scope)
        self.assertEqual(before["messages"], after["messages"][:len(before["messages"])])
        self.assertEqual(after["payloads"][-1]["content"], new.semantic_text)

    def test_same_process_and_restart_preserve_frozen_prefix_without_extra_writes(self):
        self.open_input()
        first = self.manager.build_context_projection(provider_profile="openai", **self.scope)
        with patch.object(self.manager._store, "get_conversation_generations", side_effect=AssertionError("hot-path DB read")):
            self.open_input("another-input")
        self.assertEqual(self.provider.call_count, 1)
        second = self.manager.build_context_projection(provider_profile="openai", **self.scope)
        self.assertEqual(second["messages"][:len(first["messages"])], first["messages"])
        original_id = self.notices()[0].source_id
        self.manager.close()
        reopened = self.make_manager()
        result = reopened.begin_hidden_host_turn(**self.scope)
        self.assertTrue(result["ok"], result)
        self.assertEqual([entry.source_id for entry in self.notices(reopened)], [original_id])

    def test_read_only_or_passive_ingest_and_disabled_speech_do_not_insert_notice(self):
        self.system()
        self.manager.build_context_surface(provider_profile="openai", **self.scope)
        self.manager.append_standalone_message({"source_id": "passive", "content": "群聊消息", "timestamp": 100},
            role="user", observed=True, **self.scope)
        self.assertEqual(self.notices(), [])
        self.provider.return_value = {}
        self.open_input()
        self.assertEqual(self.notices(), [])
        self.provider.return_value = self.policy
        self.open_input("voice-enabled")
        self.assertEqual(len(self.notices()), 1)

    def test_hidden_and_existing_input_paths_reach_real_provider_pipeline(self):
        for index, existing in enumerate((False, True)):
            self.scope["session_id"] = f"group-{index}"
            source = f"passive-{index}"
            self.manager.append_standalone_message({"source_id": source, "content": "群里问好", "timestamp": 100},
                role="user", observed=True, **self.scope)
            if existing:
                opened = self.manager.begin_existing_input_turn(source_id=source, **self.scope)
            else:
                opened = self.manager.begin_hidden_host_turn(referenced_source_ids=[source], **self.scope)
            self.assertTrue(opened["ok"], opened)
            engine = _PromptContextEngine(memcore_manager=self.manager)
            with patch.object(config, "MEMORY_BACKEND", "memcore"), patch.object(config, "LLM_AUTO_COMPACT_TOKEN_LIMIT", 0):
                context = response_builder.prepare_context(engine, user_message="问好", current_user_source_id=source if existing else "",
                    current_input_transient=not existing, recent_raw=[], recent_episodic_summaries=[],
                    recent_semantic_summaries=[], confirmed_snippets=[], now_ts=200, **self.scope)
            self.assertNotIn("memcore_projection_failure", context)
            note, = self.notices()
            contents = [turn.get("content") for turn in
                        [*context["history_turns"], *context["post_user_turns"], *context["ephemeral_turns"]]]
            self.assertEqual(contents.count(note.semantic_text), 1)
            self.assertNotIn(note.semantic_text, str(context["ephemeral_turns"]))

    def test_notice_write_failure_is_observable_and_retried_at_next_turn(self):
        system = self.system()
        with patch.object(system, "append_standalone_entry", side_effect=OSError("private-path")):
            with self.assertLogs("akane.memcore", level="WARNING") as captured:
                self.open_input()
        self.assertIn("error_type=OSError", str(captured.output))
        self.assertNotIn("private-path", str(captured.output))
        self.assertEqual(self.notices(), [])
        self.open_input("retry-input")
        self.assertEqual(len(self.notices()), 1)

    def test_real_sync_raw_compaction_replaces_consumed_notice_and_is_idempotent(self):
        self.configure_compaction()
        system = self.system()
        initial = self.manager._ensure_history_projection_notice(system)
        result = self.manager.compact_due_sync(provider_profile="openai", **self.scope)
        self.assertTrue(result["ok"], result)
        stats = result["stats"]
        self.assertEqual(stats["status"], "compacted", stats)
        self.assertGreater(stats["summaries_created"], 0)
        self.assertIn(initial["source_id"], stats["summary_source_ids"])
        note, = self.notices()
        self.assertNotEqual(note.source_id, initial["source_id"])
        self.assertEqual(note.payload["compaction_generation"], stats["compaction_generation"])
        repeated = self.manager._ensure_history_projection_notice(system, compaction_stats=stats)
        self.assertEqual(repeated["source_id"], note.source_id)
        self.assertEqual(len(self.notices()), 1)
        episodes = self.manager._store.get_visible_episodic_summaries(namespace=system.namespace, limit=10)
        self.assertEqual(episodes, [])

    def test_real_background_raw_compaction_appends_before_completion_tracking_finishes(self):
        self.configure_compaction()
        system = self.system()
        initial = self.manager._ensure_history_projection_notice(system)
        finished = threading.Event()
        futures = []
        original = system.compact_due_background

        def capture(**kwargs):
            future = original(**kwargs)
            futures.append(future)
            return future

        with patch.object(system, "compact_due_background", side_effect=capture):
            queued = self.manager.compact_due_background(provider_profile="openai", **self.scope)
        self.assertTrue(queued["ok"], queued)
        future, = futures
        future.add_done_callback(lambda _: finished.set())
        self.assertTrue(finished.wait(5))
        self.assertEqual(future.result()["status"], "compacted")
        self.assertNotIn(future, self.manager._background_futures)
        note, = self.notices()
        self.assertNotEqual(note.source_id, initial["source_id"])

    def test_no_op_failures_and_semantic_only_generation_do_not_repeat_notice(self):
        self.open_input()
        original = self.notices()[0].source_id
        system = self.system()
        for status in ("not_due", "busy", "failed", "compacted"):
            self.manager._ensure_history_projection_notice(system,
                compaction_stats={"status": status, "summaries_created": 0, "compaction_generation": 15})
        self.manager._history_notice_ready.clear()
        with patch.object(self.manager._store, "get_conversation_generations", return_value=(15, 0)):
            result = self.manager._ensure_history_projection_notice(system)
        self.assertEqual(result["status"], "restored")
        self.assertEqual([entry.source_id for entry in self.notices()], [original])

    def test_failed_post_compaction_write_retries_even_if_an_older_notice_survived(self):
        self.open_input()
        system = self.system()
        original = self.notices()[0].source_id
        stats = {"status": "compacted", "summaries_created": 1,
                 "summary_source_ids": ["older-history"], "compaction_generation": 15}
        with patch.object(self.manager._store, "get_conversation_generations", return_value=(15, 0)):
            with patch.object(system, "append_standalone_entry", side_effect=OSError("write unavailable")):
                with self.assertLogs("akane.memcore", level="WARNING"):
                    result = self.manager._ensure_history_projection_notice(system, compaction_stats=stats)
            self.assertFalse(result["ok"])
            retried = self.manager._ensure_history_projection_notice(system)
        self.assertTrue(retried["ok"])
        self.assertNotEqual(retried["source_id"], original)
        self.assertEqual(len(self.notices()), 2)


if __name__ == "__main__":
    unittest.main()
