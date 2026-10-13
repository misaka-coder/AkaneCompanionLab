"""Reading-record history, independently selectable examples, and real migration."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from companion_v01.assistant_history_projection import (
    ASSISTANT_HISTORY_RECORD_HEADER,
    project_assistant_history_content,
    project_assistant_history_payload,
    restore_final_history_content,
)
from companion_v01.memcore_integration.history_notice import build_history_projection_notice
from companion_v01.memcore_integration.manager import MemcoreManager
from tests.test_memcore_integration import _FakeEmbeddingProvider, _FakeLLM


class AssistantHistoryTextTests(unittest.TestCase):
    def body(self):
        return {"emotion": "normal", "utterances": [
            {"emotion": "shy", "style": "優しく、少し照れながら", "text": "你回来啦。\n今天辛苦了。",
             "say": "おかえり。今日もお疲れさま。", "extra": {"note": "retained"}},
            {"emotion": "happy", "style": "happy", "text": "今天有件开心的事。", "say": "今日は嬉しいことがあったの。"},
        ], "memory_metadata": {"topic_terms": ["重逢"]}, "state_request": {"affinity": 1}}

    def test_reading_record_retains_order_emotions_tags_and_extra_fields(self):
        body = self.body()
        raw = json.dumps(body, ensure_ascii=False)
        before = raw
        record = project_assistant_history_content(raw)
        self.assertTrue(record.startswith(ASSISTANT_HISTORY_RECORD_HEADER))
        self.assertTrue(record.startswith("过往发言（整体表情 normal）：\n"))
        self.assertIn("“你回来啦。\n今天辛苦了。”（表情 shy）", record)
        self.assertIn("“今天有件开心的事。”（表情 happy，语气 happy）", record)
        self.assertLess(record.index("你回来啦"), record.index("今天有件开心的事"))
        self.assertIn('"state_request":{"affinity":1}', record)
        self.assertIn('"note":"retained"', record)
        for omitted in ("say:", "[hidden]", "[archived]", "優しく", "おかえり", "memory_metadata", "重逢"):
            self.assertNotIn(omitted, record)
        self.assertEqual(raw, before)
        self.assertEqual(project_assistant_history_content(record), record)

    def test_multiple_groups_keep_exact_words_and_inherit_only_the_overall_emotion(self):
        body = {"emotion": "smile", "utterances": [
            {"emotion": "smile", "text": "欢迎回来，今天辛苦了。", "say": "おかえり。"},
            {"emotion": "shy", "text": "（移开视线）今天有没有想我呀？", "say": "私のこと、考えてた？"},
            {"emotion": "happy", "text": "今天有件开心的事。", "say": "嬉しいことがあったの。"},
            {"emotion": "serious", "style": "soft", "text": "坐下来，我们慢慢聊。", "say": "ゆっくり話そう。"},
            {"style": "落ち着いて話す", "text": "你说过“晚点回来”。\n现在刚刚好。", "say": "今でちょうどいいね。"},
        ]}
        expected = (
            "过往发言（整体表情 smile）：\n"
            "“欢迎回来，今天辛苦了。”\n"
            "“（移开视线）今天有没有想我呀？”（表情 shy）\n"
            "“今天有件开心的事。”（表情 happy）\n"
            "“坐下来，我们慢慢聊。”（表情 serious，语气 soft）\n"
            "“你说过“晚点回来”。\n现在刚刚好。”"
        )
        self.assertEqual(project_assistant_history_content(json.dumps(body, ensure_ascii=False)), expected)
        no_overall = {"utterances": [{"emotion": "shy", "text": "你好。", "say": "こんにちは。"}]}
        self.assertEqual(project_assistant_history_content(json.dumps(no_overall, ensure_ascii=False)),
                         "过往发言：\n“你好。”（表情 shy）")

    def test_silence_display_only_and_bad_output_keep_distinct_meaning(self):
        silent = project_assistant_history_content('{"emotion":"normal","utterances":[]}')
        self.assertIn("（沉默）", silent)
        for say in (None, "", "[hidden]", "[archived]"):
            with self.subTest(say=say):
                raw = json.dumps({"utterances": [{"text": "只显示。", "say": say}]})
                self.assertIn("仅显示", project_assistant_history_content(raw))
        for raw in ('{"utterances":[{"say":"缺少正文"}]}', '{"utterances":[{"text":"未完成"}', "plain reply"):
            self.assertEqual(project_assistant_history_content(raw), raw)
        text = project_assistant_history_content('{"utterances":[{"text":"我们正在讨论 [hidden]。","say":null}]}')
        self.assertIn("我们正在讨论 [hidden]。", text)

    def test_four_combinations_keep_final_json_example_independent_of_history(self):
        raw = json.dumps(self.body(), ensure_ascii=False)
        for mode in ("json", "text"):
            for example in (False, True):
                with self.subTest(mode=mode, example=example):
                    history = project_assistant_history_content(raw, history_format=mode)
                    notice = build_history_projection_notice({"language": "日语", "style_mode": "description"},
                        history_format=mode, include_example=example)
                    samples = [json.loads(line) for line in notice.splitlines() if line.startswith("{")]
                    self.assertEqual(len(samples), int(example))
                    if samples:
                        for unit in samples[0]["utterances"]:
                            self.assertRegex(unit["say"], "[ぁ-ゖァ-ヺ]")
                            self.assertRegex(unit["style"], "[ぁ-ゖァ-ヺ]")
                    if mode == "text":
                        self.assertTrue(history.startswith(ASSISTANT_HISTORY_RECORD_HEADER))
                        self.assertNotIn("[hidden]", history + notice)
                        self.assertIn("助手原始 JSON 回复", notice)
                        self.assertIn("助手当时输出的是 JSON", notice)
                    else:
                        self.assertEqual(json.loads(history)["utterances"][0]["say"], "[hidden]")
                    for phrase in ("不要", "不能", "禁止", "本轮", "可省略"):
                        self.assertNotIn(phrase, notice)

    def test_rehydration_preserves_native_blocks_and_provider_envelope(self):
        raw = json.dumps(self.body(), ensure_ascii=False)
        tool = {"type": "tool_use", "id": "call-1", "name": "read", "input": {"say": "argument"}}
        payload = {"role": "assistant", "provider_marker": "kept", "content": [
            {"type": "text", "text": "old reading record"}, tool]}
        restored = restore_final_history_content(payload, raw)
        self.assertEqual(restored["content"][0]["text"], raw)
        projected = project_assistant_history_payload(restored)
        self.assertTrue(projected["content"][0]["text"].startswith(ASSISTANT_HISTORY_RECORD_HEADER))
        self.assertEqual(projected["content"][1], tool)
        self.assertEqual(projected["provider_marker"], "kept")
        self.assertEqual(payload["content"][0]["text"], "old reading record")
        for unchanged in ({**payload, "role": "user"}, {**payload, "tool_calls": [{"id": "call-1"}]}):
            self.assertEqual(restore_final_history_content(unchanged, raw), unchanged)
            self.assertEqual(project_assistant_history_payload(unchanged), unchanged)

    def test_real_upgrade_recovers_speech_presence_preserves_tools_and_freezes_reads(self):
        with TemporaryDirectory() as directory, patch("config.DATA_DIR", Path(directory)):
            path = Path(directory) / "memory.db"
            manager = MemcoreManager(backend="memcore", storage_path=path,
                visible_scope="conversation", enable_flavor=True, shadow_compare=False,
                llm=_FakeLLM(), embedding_provider=_FakeEmbeddingProvider())
            scope = dict(profile_user_id="owner", session_id="qq_group_test", character_pack_id="cecilia")
            manager._memory_config.operation_projection_policy = "compact_arguments_after_terminal"
            try:
                opened = manager.begin_input_turn({"source_id": "user", "content": "读一下资料。", "timestamp": 100}, **scope)
                tools = manager.record_tool_batch(exchanges=[{
                    "tool_name": "read_material", "tool_call_id": "call-1", "tool_input": {"handle": "material-1"},
                    "result": "完整资料正文。" * 500, "source": "test", "timestamp": 101,
                    "source_id_prefix": "tool"}], turn_id=opened["turn_id"], **scope)
                self.assertTrue(tools["ok"], tools)
                manager.build_context_projection(provider_profile="openai", **scope)
                system = manager._get_system(**scope)
                raw = json.dumps(self.body(), ensure_ascii=False)
                from memcore import ProjectionMessageInput
                old_payload = {"role": "assistant", "content": project_assistant_history_content(raw, history_format="json")}
                closed = system.complete_turn(turn_id=opened["turn_id"], source_id="final", semantic_text="你回来啦。",
                    timestamp=102, memory_annotation=self.body()["memory_metadata"], provider_output_raw=raw,
                    provider_profile="openai_chat", provider_projection=ProjectionMessageInput(
                        provider_profile="openai_chat", payload=old_payload, projection_version=17))
                self.assertTrue(closed.completed)
                original = system.store.get_turn_projections(namespace=system.namespace,
                    turn_id=opened["turn_id"], provider_profile="openai_chat")
                old_entry = system.store.get_entry(namespace=system.namespace, source_id="final")
                old_input = system.store.get_entry(namespace=system.namespace, source_id="user")
                old_tools = [dict(row.payload) for row in original if row.payload.get("tool_calls") or row.payload.get("role") == "tool"]
                result = manager.run_assistant_history_projection_migration()
                self.assertTrue(result["ok"], result)
                self.assertGreater(result["totals"]["migrated"], 0)
                self.assertEqual(result["totals"]["settled_rebuild_failed_dropped"], 0)
                after = manager.build_context_projection(provider_profile="openai", **scope)
                record = after["payloads"][-1]["content"]
                self.assertTrue(record.startswith(ASSISTANT_HISTORY_RECORD_HEADER))
                self.assertNotIn("仅显示", record)
                self.assertNotIn("[hidden]", record)
                updated = system.store.get_turn_projections(namespace=system.namespace,
                    turn_id=opened["turn_id"], provider_profile="openai_chat")
                self.assertEqual(old_tools, [dict(row.payload) for row in updated if row.payload.get("tool_calls") or row.payload.get("role") == "tool"])
                entry = system.store.get_entry(namespace=system.namespace, source_id="final")
                self.assertEqual(entry.payload["provider_output_raw"], raw)
                self.assertEqual(entry, old_entry)
                input_entry = system.store.get_entry(namespace=system.namespace, source_id="user")
                self.assertEqual(input_entry, old_input)
                self.assertEqual(input_entry.memory_metadata["topic_terms"], ["重逢"])
                repeated = manager.run_assistant_history_projection_migration()
                self.assertEqual(repeated["totals"]["migrated"], 0)
                manager.begin_input_turn({"source_id": "next", "content": "接着聊。", "timestamp": 104}, **scope)
                with patch("companion_v01.assistant_history_projection.project_assistant_history_content",
                           side_effect=AssertionError("reading frozen history must not render again")):
                    for _ in range(2):
                        surface = manager.build_context_surface(provider_profile="openai", current_source_id="next", **scope)
                        self.assertEqual(surface["history_messages"][-1]["content"], record)
                        following = manager.build_context_projection(provider_profile="openai", **scope)
                        self.assertEqual(following["payloads"][:len(after["payloads"])], after["payloads"])
                other = manager.build_context_projection(provider_profile="anthropic", **scope)
                self.assertEqual(other["payloads"][-2]["content"][0]["text"], record)
                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
