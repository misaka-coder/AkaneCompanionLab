from __future__ import annotations

from dataclasses import replace
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from companion_v01.assistant_history_projection import (
    ARCHIVED_SAY, ARCHIVED_STYLE, ASSISTANT_HISTORY_PROJECTION_VERSION,
    project_assistant_history_content, project_assistant_history_payload,
)
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.engine_services.response_projection import _build_memcore_provider_history
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.speech_units import voice_segments
from tests.test_memcore_integration import _FakeEmbeddingProvider, _FakeLLM


class AssistantHistoryProjectionTests(unittest.TestCase):
    def setUp(self):
        # Keep regression coverage for the independently selectable JSON arm.
        self.format_patch = patch("companion_v01.assistant_history_projection.ASSISTANT_HISTORY_FORMAT", "json")
        self.format_patch.start()
        self.addCleanup(self.format_patch.stop)

    def test_old_archive_markers_change_only_in_projected_voice_fields(self):
        body = {"emotion": "shy", "utterances": [{"emotion": "happy", "style": "[archived]",
            "text": "旧台词。", "say": "[archived]", "extra": {"say": "[archived]"}}]}
        raw = json.dumps(body, ensure_ascii=False)
        projected = json.loads(project_assistant_history_content(raw))
        self.assertEqual(ARCHIVED_SAY, "[hidden]")
        self.assertEqual(projected["utterances"][0], {**body["utterances"][0],
            "say": "[hidden]", "style": "[hidden]"})
        self.assertEqual(json.dumps(body, ensure_ascii=False), raw)

    def test_prose_styles_are_archived_without_touching_delivery_or_other_fields(self):
        for style in ("優しく", "怒りを込めて、強い口調で相手を責めるように話す。", "温柔地安慰", "speak gently and slowly"):
            with self.subTest(style=style):
                body = {"emotion": "smile", "utterances": [
                    {"style": style, "text": "慢慢休息吧。", "say": "ゆっくり休んでください。", "extra": {"style": "nested value"}}]}
                raw = json.dumps(body, ensure_ascii=False)
                delivery = voice_segments(body["utterances"])
                frozen = project_assistant_history_content(raw)
                history = json.loads(frozen)
                self.assertEqual(history["utterances"], [{**body["utterances"][0], "style": ARCHIVED_STYLE, "say": ARCHIVED_SAY}])
                self.assertEqual(json.dumps(body, ensure_ascii=False), raw)
                self.assertEqual(voice_segments(body["utterances"]), delivery)
                self.assertEqual(delivery[0]["style"], style)
                self.assertEqual(project_assistant_history_content(frozen), frozen)
        for style in ("calm", "happy", "very_soft", "profile-1", "a" * 40, "", None):
            with self.subTest(style=style):
                raw = json.dumps({"utterances": [{"text": "只显示。", "say": None, "style": style}]})
                self.assertEqual(json.loads(project_assistant_history_content(raw))["utterances"][0]["style"], style)

    def test_style_archiving_preserves_provider_blocks_and_tool_arguments(self):
        raw = json.dumps({"utterances": [{"text": "查一下。", "say": "調べます。", "style": "自然に優しく話す。"}]}, ensure_ascii=False)
        tool = {"type": "tool_use", "id": "call-1", "name": "read", "input": {"style": "cannot change", "say": "argument"}}
        payload = {"role": "assistant", "content": [{"type": "text", "text": raw}, tool], "provider_marker": "kept"}
        before = json.dumps(payload, ensure_ascii=False)
        projected = project_assistant_history_payload(payload)
        self.assertEqual(json.loads(projected["content"][0]["text"])["utterances"][0]["style"], ARCHIVED_STYLE)
        self.assertEqual(projected["content"][1], tool)
        self.assertEqual(projected["provider_marker"], "kept")
        self.assertEqual(json.dumps(payload, ensure_ascii=False), before)
        for unchanged in ({**payload, "role": "user"}, {**payload, "tool_calls": [{"id": "call-1"}]}):
            self.assertEqual(project_assistant_history_payload(unchanged), unchanged)

    def test_display_and_style_survive_without_duplicating_spoken_text(self):
        body = {
            "reply_medium": "voice",
            "emotion": "happy", "memory_metadata": {"topic_terms": ["少糖红茶"]},
            "utterances": [{"style": "calm", "text": "（递来茶杯）记住啦。", "say": "覚えました。"},
                           {"style": "happy", "text": "少糖红茶。", "say": "お砂糖控えめの紅茶ですね。"}],
            "state_request": {"affinity": 1}, "code_snippet": "print('say')",
        }
        raw = "```json\n" + json.dumps(body, ensure_ascii=False, indent=2) + "\n```"
        original_voice = voice_segments(body["utterances"])
        history = json.loads(project_assistant_history_content(raw))
        self.assertNotIn("reply_medium", history)
        self.assertEqual(body["reply_medium"], "voice")
        self.assertEqual(history["memory_metadata"], {})
        self.assertEqual([u["say"] for u in history["utterances"]], [ARCHIVED_SAY, ARCHIVED_SAY])
        self.assertEqual([u["text"] for u in history["utterances"]], [u["text"] for u in body["utterances"]])
        self.assertEqual([u["style"] for u in history["utterances"]], ["calm", "happy"])
        self.assertEqual(history["state_request"], body["state_request"])
        self.assertEqual(history["code_snippet"], body["code_snippet"])
        self.assertEqual(voice_segments(body["utterances"]), original_voice)
        self.assertNotIn(ARCHIVED_SAY, repr(original_voice))
        self.assertGreater(list(history).index("memory_metadata"), list(history).index("utterances"))

    def test_silence_and_display_only_keep_their_original_meaning(self):
        for units in [[], [{"text": "只显示。", "say": None}], [{"text": "只显示。", "say": ""}],
                      [{"text": "只显示。"}]]:
            with self.subTest(units=units):
                raw = json.dumps({"utterances": units, "memory_metadata": {}}, ensure_ascii=False)
                self.assertEqual(json.loads(project_assistant_history_content(raw))["utterances"], units)

    def test_tool_requests_and_non_reply_json_remain_exact(self):
        for field in ["tool_call", "tool_calls", "_native_tool_call", "_native_tool_calls"]:
            raw = json.dumps({"utterances": [{"text": "查一下。", "say": "調べます。"}],
                "reply_medium": "voice",
                field: {"arguments": {"text": "正文", "say": "参数不能改变", "style": "怒りを込めて話す。", "reply_medium": "tool argument"}},
                "memory_metadata": {"topic_terms": ["工具"]}}, ensure_ascii=False, indent=2)
            self.assertEqual(project_assistant_history_content(raw), raw)
        for raw in ['{"arguments":{"say":"工具参数"}}', 'not json', '[{"speech":"不是最终对象"}]',
                    '{"utterances":[{"say":"缺少正文"}]}', '{"utterances":[{"text":"未完成"}']:
            self.assertEqual(project_assistant_history_content(raw), raw)

    def test_legacy_and_scene_replies_keep_their_rendering_contract(self):
        for body in [{"emotion": "normal", "speech": "你好。", "memory_metadata": {"topic_terms": ["问候"]}},
                     {"beats": [{"speech": "你好。", "emotion_id": "normal", "advance": "click"}],
                      "memory_metadata": {"topic_terms": ["问候"]}}]:
            history = json.loads(project_assistant_history_content(json.dumps(body, ensure_ascii=False)))
            self.assertEqual(history, {**body, "memory_metadata": {}})

    def test_real_turn_retains_raw_annotations_and_frozen_prefix_with_smaller_compaction_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "memory.db"
            with patch("config.DATA_DIR", Path(directory)), patch("config.MEMCORE_RAW_TOKEN_TRIGGER", 15000):
                manager = MemcoreManager(backend="memcore", storage_path=db, visible_scope="conversation",
                    enable_flavor=True, shadow_compare=False, llm=_FakeLLM(), embedding_provider=_FakeEmbeddingProvider())
                scope = dict(profile_user_id="history-owner", session_id="history-private", character_pack_id="character")
                engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
                engine.memcore_manager = manager
                engine.llm = SimpleNamespace(chat_provider_protocol=lambda **_kwargs: "openai")
                try:
                    # An existing complete history projection is never rebuilt by this feature.
                    old = manager.begin_input_turn({"source_id": "old-user", "content": "以前的聊天", "timestamp": 100}, **scope)
                    manager.build_context_projection(provider_profile="openai", **scope)
                    old_raw = '{"utterances":[{"text":"旧回复。","say":"前の返事です。"}],"memory_metadata":{"topic_terms":["旧记忆"]}}'
                    system = manager._get_system(**scope)
                    system.complete_turn(turn_id=old["turn_id"], source_id="old-final", semantic_text="旧回复。", timestamp=101,
                        memory_annotation={"topic_terms": ["旧记忆"]}, provider_output_raw=old_raw,
                        provider_profile="openai_chat", provider_projection={"role": "assistant", "content": old_raw})
                    before = manager.build_context_projection(provider_profile="openai", **scope)
                    self.assertTrue(before["ok"], before)

                    opened = manager.begin_input_turn({"source_id": "new-user", "content": "喜欢少糖红茶", "timestamp": 102}, **scope)
                    manager.build_context_projection(provider_profile="openai", **scope)
                    metadata = {"about_roles": ["user"], "memory_facets": ["preference"],
                        "topic_terms": ["少糖红茶"], "retrieval_priority": "high"}
                    units = [{"style": "calm", "text": "记住啦。", "say": "覚えました。" * 100},
                             {"style": "うれしくて少し照れながら、自然に話す。", "text": "谢谢你告诉我。", "say": "教えてくれてありがとう。"}]
                    raw = "```json\n" + json.dumps({"reply_medium": "voice", "emotion": "normal", "memory_metadata": metadata, "utterances": units}, ensure_ascii=False, indent=2) + "\n```"
                    kwargs = dict(turn_id=opened["turn_id"], assistant_record={"source_id": "new-final", "content": "记住啦。", "timestamp": 103},
                        memory_metadata=metadata, provider_output_raw=raw, **scope)
                    completed = engine._complete_memcore_input_turn(**kwargs)
                    self.assertTrue(completed["ok"], completed)
                    frozen = manager.build_context_projection(provider_profile="openai", **scope)
                    self.assertTrue(frozen["ok"], frozen)
                    self.assertEqual(frozen["payloads"][:len(before["payloads"])], before["payloads"])
                    self.assertEqual(frozen["payloads"][len(before["payloads"])-1]["content"], old_raw)
                    history = json.loads(frozen["payloads"][-1]["content"])
                    self.assertNotIn("reply_medium", history)
                    self.assertEqual(history["utterances"], [
                        {**units[0], "say": ARCHIVED_SAY}, {**units[1], "style": ARCHIVED_STYLE, "say": ARCHIVED_SAY}])
                    self.assertEqual(history["memory_metadata"], {})
                    # Production uses ContextSurface rather than the ledger-only
                    # context projection. It must send the same frozen payloads.
                    surface = manager.build_context_surface(provider_profile="openai", current_source_id="", **scope)
                    self.assertTrue(surface["ok"], surface)
                    self.assertEqual(surface["history_messages"][-1], frozen["payloads"][-1])
                    self.assertEqual(surface["history_messages"][len(before["payloads"])-1]["content"], old_raw)
                    wire_history = _build_memcore_provider_history(engine, **scope,
                        current_source_id="", allow_history_only=True, chat_model_override="")
                    self.assertTrue(wire_history["ok"], wire_history)
                    self.assertEqual(wire_history["history_turns"][-1], frozen["payloads"][-1])

                    with closing(sqlite3.connect(db)) as connection:
                        stored_raw = json.loads(connection.execute("SELECT payload_json FROM messages WHERE source_id='new-final'").fetchone()[0])
                        annotation = json.loads(connection.execute("SELECT memory_metadata_json FROM messages WHERE source_id='new-user'").fetchone()[0])
                    self.assertEqual(stored_raw["provider_output_raw"], raw)
                    self.assertEqual(annotation["topic_terms"], ["少糖红茶"])
                    self.assertEqual(annotation["retrieval_priority"], "high")
                    self.assertEqual(json.loads(raw[8:-4])["utterances"], units)
                    self.assertEqual(json.loads(raw[8:-4])["reply_medium"], "voice")

                    system = manager._get_system(**scope)
                    bundles = system.store.list_compaction_bundles(namespace=system.namespace)
                    projections = [p for bundle in bundles for p in system._compaction._freeze_bundle_projection(system.namespace, bundle, "openai_chat")]
                    counter = system._compaction._count_projection_tokens
                    compact_tokens = sum(counter(p) for p in projections)
                    full_tokens = sum(counter(replace(p, payload={"role": "assistant", "content": raw}))
                        if p.payload.get("content") == frozen["payloads"][-1]["content"] else counter(p) for p in projections)
                    self.assertLess(compact_tokens, full_tokens)
                    system.config.raw_token_trigger = (compact_tokens + full_tokens) // 2
                    stats = system.compact_due_sync(provider_profile="openai_chat")
                    self.assertEqual(stats["status"], "not_due", stats)
                    self.assertEqual(stats["before_raw_projected_tokens"], compact_tokens)

                    # Retrying completion and appending a turn preserve every old projection byte.
                    self.assertTrue(engine._complete_memcore_input_turn(**kwargs)["ok"])
                    manager.begin_input_turn({"source_id": "next-user", "content": "接着聊天", "timestamp": 104}, **scope)
                    with patch("companion_v01.assistant_history_projection.project_assistant_history_content",
                               side_effect=AssertionError("frozen replies must not be parsed again")), \
                         patch.object(manager, "run_assistant_history_projection_migration",
                                      side_effect=AssertionError("requests must not migrate history")), \
                         patch.object(manager._store, "list_projection_namespaces",
                                      side_effect=AssertionError("requests must not enumerate database namespaces")):
                        for _ in range(3):
                            following = manager.build_context_projection(provider_profile="openai", **scope)
                            self.assertTrue(following["ok"], following)
                            self.assertEqual(following["payloads"][:len(frozen["payloads"])], frozen["payloads"])
                            surface = manager.build_context_surface(provider_profile="openai", current_source_id="next-user", **scope)
                            self.assertTrue(surface["ok"], surface)
                            self.assertEqual(surface["history_messages"][-1], frozen["payloads"][-1])
                finally:
                    manager.close()

    def test_explicit_upgrade_preserves_sources_and_tool_pairs_rebuilds_settlement_once(self):
        with tempfile.TemporaryDirectory() as directory, patch("config.DATA_DIR", Path(directory)):
            manager = MemcoreManager(backend="memcore", storage_path=Path(directory) / "memory.db",
                visible_scope="conversation", enable_flavor=True, shadow_compare=False,
                llm=_FakeLLM(), embedding_provider=_FakeEmbeddingProvider())
            scope = dict(profile_user_id="migration-owner", session_id="migration-chat", character_pack_id="character")
            manager._memory_config.operation_projection_policy = "compact_arguments_after_terminal"
            try:
                opened = manager.begin_input_turn({"source_id": "migration-user", "content": "查一下资料", "timestamp": 100}, **scope)
                trace = manager.record_tool_batch(exchanges=[{
                    "tool_name": "read_material", "tool_call_id": "migration-call", "tool_input": {"handle": "material-1"},
                    "result": "完整资料正文。" * 500, "source": "test", "timestamp": 101,
                    "source_id_prefix": "migration-tool"}], turn_id=opened["turn_id"], **scope)
                self.assertTrue(trace["ok"], trace)
                manager.build_context_projection(provider_profile="openai", **scope)
                system = manager._get_system(**scope)
                raw = json.dumps({"emotion": "normal", "utterances": [{"style": "calm", "text": "已经查好啦。", "say": "調べました。" * 40}],
                                  "reply_medium": "voice", "memory_metadata": {"topic_terms": ["资料"]}}, ensure_ascii=False, indent=2)
                closed = system.complete_turn(turn_id=opened["turn_id"], source_id="migration-final", semantic_text="已经查好啦。",
                    timestamp=102, memory_annotation={"topic_terms": ["资料"]}, provider_output_raw=raw,
                    provider_profile="openai_chat", provider_projection={"role": "assistant", "content": raw})
                self.assertTrue(closed.completed)
                original_rows = system.store.get_turn_projections(namespace=system.namespace, turn_id=opened["turn_id"], provider_profile="openai_chat")
                original_tools = [dict(p.payload) for p in original_rows if p.payload.get("tool_calls") or p.payload.get("role") == "tool"]
                original_final = system.store.get_entry(namespace=system.namespace, source_id="migration-final")

                dry = manager.run_assistant_history_projection_migration(dry_run=True)
                self.assertTrue(dry["ok"], dry)
                self.assertGreater(dry["totals"]["migrated"], 0)
                self.assertEqual(system.store.get_turn_projections(namespace=system.namespace, turn_id=opened["turn_id"], provider_profile="openai_chat"), original_rows)
                migrated = manager.run_assistant_history_projection_migration()
                self.assertTrue(migrated["ok"], migrated)
                updated_rows = system.store.get_turn_projections(namespace=system.namespace, turn_id=opened["turn_id"], provider_profile="openai_chat")
                self.assertEqual([dict(p.payload) for p in updated_rows if p.payload.get("tool_calls") or p.payload.get("role") == "tool"], original_tools)
                self.assertEqual(system.store.get_entry(namespace=system.namespace, source_id="migration-final").payload, original_final.payload)
                small = json.loads(updated_rows[-1].payload["content"])
                self.assertNotIn("reply_medium", small)
                self.assertEqual(small["utterances"][0]["say"], ARCHIVED_SAY)
                self.assertEqual(small["memory_metadata"], {})
                self.assertEqual(migrated["totals"]["settled_rebuild_failed_dropped"], 0)
                self.assertGreater(sum(migrated["totals"][key] for key in ("settled_rebuilt", "settled_rebuilt_noop", "settled_rebuilt_fallback")), 0)
                visible = manager.build_context_projection(provider_profile="openai", **scope)
                self.assertEqual(json.loads(visible["payloads"][-1]["content"])["utterances"][0]["say"], ARCHIVED_SAY)

                generations = system.store.get_conversation_generations(namespace=system.namespace)
                again = manager.run_assistant_history_projection_migration()
                self.assertTrue(again["ok"], again)
                self.assertEqual(again["totals"]["migrated"], 0)
                self.assertEqual(again["totals"]["version_advanced_only"], 0)
                self.assertEqual(system.store.get_conversation_generations(namespace=system.namespace), generations)
                # A previously unused provider profile freezes the same compact final reply.
                other = manager.build_context_projection(provider_profile="anthropic", **scope)
                self.assertTrue(other["ok"], other)
                final_text = other["payloads"][-1]["content"][0]["text"]
                self.assertEqual(json.loads(final_text)["utterances"][0]["say"], ARCHIVED_SAY)
                other_surface = manager.build_context_surface(provider_profile="anthropic", current_source_id="", **scope)
                self.assertTrue(other_surface["ok"], other_surface)
                self.assertEqual(other_surface["history_messages"][-1], other["payloads"][-1])
                self.assertEqual(system.store.get_entry(namespace=system.namespace, source_id="migration-final").payload["provider_output_raw"], raw)
            finally:
                manager.close()

    def test_version_seven_history_waits_for_explicit_upgrade_and_retains_original_reply(self):
        from memcore import ProjectionMessageInput

        with tempfile.TemporaryDirectory() as directory, patch("config.DATA_DIR", Path(directory)):
            manager = MemcoreManager(backend="memcore", storage_path=Path(directory) / "memory.db",
                visible_scope="conversation", enable_flavor=True, shadow_compare=False,
                llm=_FakeLLM(), embedding_provider=_FakeEmbeddingProvider())
            scope = dict(profile_user_id="old-projection-owner", session_id="old-projection-chat", character_pack_id="character")
            try:
                opened = manager.begin_input_turn({"source_id": "old-v7-user", "content": "喝茶吧。", "timestamp": 100}, **scope)
                manager.build_context_projection(provider_profile="openai", **scope)
                system = manager._get_system(**scope)
                body = {"emotion": "normal", "reply_medium": "voice",
                    "utterances": [{"text": "好呀。", "say": "いいですね。", "style": "calm"}],
                    "memory_metadata": {"topic_terms": ["喝茶"]}}
                raw = json.dumps(body, ensure_ascii=False)
                old_body = {**body, "utterances": [{**body["utterances"][0], "say": ARCHIVED_SAY}], "memory_metadata": {}}
                old_payload = {"role": "assistant", "content": json.dumps(old_body, ensure_ascii=False, separators=(",", ":"))}
                completed = system.complete_turn(turn_id=opened["turn_id"], source_id="old-v7-final", semantic_text="好呀。",
                    timestamp=101, memory_annotation=body["memory_metadata"], provider_output_raw=raw,
                    provider_profile="openai_chat", provider_projection=ProjectionMessageInput(
                        provider_profile="openai_chat", payload=old_payload, projection_version=7))
                self.assertTrue(completed.completed)
                before = manager.build_context_projection(provider_profile="openai", **scope)
                self.assertEqual(before["payloads"][-1], old_payload)
                self.assertEqual(before["messages"][-1]["projection_version"], 7)
                surface = manager.build_context_surface(provider_profile="openai", current_source_id="", **scope)
                self.assertEqual(surface["history_messages"][-1], old_payload)
                original = system.store.get_entry(namespace=system.namespace, source_id="old-v7-final")

                result = manager.run_assistant_history_projection_migration()
                self.assertTrue(result["ok"], result)
                after = manager.build_context_projection(provider_profile="openai", **scope)
                self.assertEqual(after["payloads"][:-1], before["payloads"][:-1])
                self.assertEqual(after["messages"][-1]["projection_version"], ASSISTANT_HISTORY_PROJECTION_VERSION)
                final = json.loads(after["payloads"][-1]["content"])
                self.assertNotIn("reply_medium", final)
                self.assertEqual(final, {key: value for key, value in old_body.items() if key != "reply_medium"})
                surface = manager.build_context_surface(provider_profile="openai", current_source_id="", **scope)
                self.assertEqual(surface["history_messages"][-1], after["payloads"][-1])
                self.assertEqual(system.store.get_entry(namespace=system.namespace, source_id="old-v7-final"), original)
                repeated = manager.run_assistant_history_projection_migration()
                self.assertTrue(repeated["ok"], repeated)
                self.assertEqual(repeated["totals"]["migrated"], 0)
                self.assertEqual(repeated["totals"]["version_advanced_only"], 0)
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
