"""Speaker source batches through the real Akane permission/adapter chain."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import config
from memcore import Actor
from companion_v01.capability_registry import OPEN_MEMORY_TOOL_SPEC
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.memcore_integration.timeline import MemcoreTimelineToolService
from companion_v01.tool_handlers.memory import BrowseMemoryToolHandler, OpenMemoryToolHandler
from tests import test_memory_conversation_selection as selection


class MemcoreSourcesBatchSmokeTests(unittest.TestCase):
    context = selection.MemoryConversationSelectionTests.context

    def setUp(self):
        selection.MemoryConversationSelectionTests.setUp(self)
        self.manager = MemcoreManager(
            backend="memcore",
            storage_path=Path(self.temporary.name) / "memcore.db",
            visible_scope="user",
            enable_flavor=False,
            shadow_compare=False,
            llm=object(),
            embedding_provider=selection._Embedding(),
        )
        self.addCleanup(self.manager.close)
        self.service = MemcoreTimelineToolService(legacy_service=None, memcore_manager=self.manager)
        self.handler = OpenMemoryToolHandler(timeline_service=self.service, read_policy_provider=lambda: self.policy)
        self.origin = self.context(kind="group", number=100, actor=987)
        self.scope = dict(
            profile_user_id="qq_group_shared_200", session_id="qq_group_shared_200", character_pack_id="char-a"
        )
        self.system = self.manager._get_system(**self.scope)
        backend = patch.object(config, "MEMORY_BACKEND", "memcore")
        backend.start()
        self.addCleanup(backend.stop)

    def seed(self, sid, *, repeat=2, text_size=0):
        sources = []
        for i in range(repeat):
            source_id = f"{sid}-raw-{i}"
            sources.append(source_id)
            self.system.store.add_message(
                namespace=replace(self.system.namespace, actor=Actor("qq:123456", "旧名" if i == 0 else "新名")),
                source_id=source_id,
                role="user",
                timestamp=100 + i * 10,
                content=f"{source_id} 的原话" + "完整证据" * text_size,
            )
            self.system.store.add_message(
                namespace=replace(self.system.namespace, actor=Actor("qq:654321", "另一个人")),
                source_id=f"{sid}-other-{i}",
                role="user",
                timestamp=105 + i * 10,
                content="未选择的其他人发言",
            )
            sources.append(f"{sid}-other-{i}")
        self.system.store.add_summary(
            namespace=self.system.namespace,
            record={
                "summary_id": sid,
                "timestamp": 300,
                "memory_title": "发言回溯",
                "diary_summary": "不应重复进入来源结果的摘要",
                "source_ids": sources,
            },
        )

    def execute(self, *, context=None, **args):
        call = self.handler.normalize_call({"type": "open_memory", **args})
        return self.handler.execute(call=call, context=context or self.origin)

    @staticmethod
    def metadata(result):
        return json.loads(result.followup_context.splitlines()[1])

    def test_real_handler_normalizes_qq_filters_and_wraps_each_source_cursor(self):
        self.seed("one")
        self.seed("two")
        self.assertIn("speaker_ids", OPEN_MEMORY_TOOL_SPEC.input_schema["properties"])
        first = self.execute(
            memory_ids=["one", "two"], view="sources", speaker_ids=["123456"], page_size=1, conversation="group:200"
        )
        data = self.metadata(first)
        self.assertEqual(data["conversation"], "group:200")
        self.assertEqual(data["read_mode"], "speaker_filtered")
        self.assertEqual(data["speaker_ids"], ["qq:123456"])
        self.assertFalse(first.followup_envelope.complete)
        self.assertTrue(first.followup_envelope.continuation)
        self.assertEqual(first.followup_context.count("当前证据不完整"), 1)
        self.assertNotIn("未选择的其他人发言", first.followup_context)
        self.assertNotIn("不应重复进入来源结果的摘要", first.followup_context)
        self.assertNotIn("participant_refs", first.followup_context)
        self.assertNotIn("participants", first.followup_context)
        self.assertNotIn("memory-v1:", first.followup_context)
        for item in data["result"]["items"]:
            cursor = item["result"]["next_cursor"]
            self.assertTrue(cursor.startswith("mrc1."), cursor)
            self.assertEqual(first.followup_context.count(cursor), 1)
            continued = self.execute(cursor=cursor)
            next_data = self.metadata(continued)
            self.assertEqual(next_data["conversation"], "group:200")
            self.assertEqual(next_data["memory_id"], item["memory_id"])
            self.assertIn(item["memory_id"] + "-raw-1 的原话", continued.followup_context)
            self.assertIn("新名", continued.followup_context)
            self.assertTrue(continued.followup_envelope.complete)
            self.assertNotIn("当前证据不完整", continued.followup_context)
        self.assertLessEqual(len(json.dumps(first.trace_receipt, ensure_ascii=False).encode("utf-8")), 4096)

    def test_single_source_page_shows_one_signed_cursor_and_continues_all_evidence(self):
        self.seed("one")
        args = {"memory_id": "one", "view": "sources", "page_size": 1, "conversation": "group:200"}
        pages = []
        for _ in range(4):
            page = self.execute(**args)
            data = self.metadata(page)
            pages.append(page.followup_context)
            self.assertEqual(data["conversation"], "group:200")
            self.assertEqual(data["memory_id"], "one")
            if page.followup_envelope.complete:
                self.assertTrue(data["page_complete"])
                self.assertNotIn("当前证据不完整", page.followup_context)
                break
            self.assertEqual(data["status"], "ok")
            self.assertFalse(data["page_complete"])
            cursor = data["next_cursor"]
            self.assertTrue(cursor.startswith("mrc1."))
            self.assertEqual(page.followup_context.count(cursor), 1)
            self.assertNotIn("next_cursor", data["result"])
            self.assertEqual(page.followup_context.count("当前证据不完整"), 1)
            self.assertEqual(page.followup_envelope.continuation, {"cursor": cursor})
            args = {"cursor": cursor}
        else:
            self.fail("Source continuation did not finish")
        evidence = "\n".join(pages)
        self.assertEqual(len(pages), 4)
        self.assertEqual(evidence.count("one-raw-0 的原话"), 1)
        self.assertEqual(evidence.count("one-raw-1 的原话"), 1)
        self.assertEqual(evidence.count("未选择的其他人发言"), 2)

    def test_passive_group_author_in_catalog_can_open_all_original_utterances(self):
        sources = []
        for i in range(2):
            written = self.manager.append_standalone_message(
                {
                    "source_id": f"passive-{i}",
                    "content": "相同文字也是两条真实发言",
                    "timestamp": 100 + i * 10,
                    "index_in_vector": False,
                },
                role="user",
                observed=True,
                actor_stable_id="qq:123456",
                actor_display_name="小林",
                **self.scope,
            )
            self.assertTrue(written["ok"], written)
            sources.append(written["source_id"])
        self.system.store.add_summary(
            namespace=self.system.namespace,
            record={
                "summary_id": "passive-card",
                "timestamp": 300,
                "diary_summary": "被动群聊",
                "source_ids": sources,
                "participant_refs": [{"actor_id": "qq:123456", "display_name": "小林"}],
            },
        )
        browse = BrowseMemoryToolHandler(timeline_service=self.service, read_policy_provider=lambda: self.policy)
        catalog = browse.execute(
            call=browse.normalize_call(
                {"type": "browse_memory", "participants": ["小林"], "conversation": "group:200"}
            ),
            context=self.origin,
        )
        self.assertIn("passive-card", catalog.followup_context)
        self.assertIn("小林（QQ 123456）", catalog.followup_context)
        read = self.execute(memory_id="passive-card", view="sources", speaker_ids=["123456"], conversation="group:200")
        self.assertEqual(self.metadata(read)["status"], "ok")
        self.assertEqual(read.followup_context.count("相同文字也是两条真实发言"), 2)
        for source_id in sources:
            self.assertIn(source_id, read.followup_context)

    def test_every_nested_cursor_rechecks_actor_character_bot_and_permission(self):
        self.seed("one")
        self.seed("two")
        first = self.execute(
            memory_ids=["one", "two"], view="sources", speaker_ids=["123456"], page_size=1, conversation="group:200"
        )
        cursors = [item["result"]["next_cursor"] for item in self.metadata(first)["result"]["items"]]
        for cursor in cursors:
            for origin in (
                self.context(kind="group", number=100, actor=654),
                self.context(kind="group", number=100, actor=987, character="char-b"),
            ):
                rejected = self.execute(context=origin, cursor=cursor)
                self.assertIn("cursor_invalid", rejected.followup_context)
            self.cross_enabled = False
            self.assertIn("cross_conversation_disabled", self.execute(cursor=cursor).followup_context)
            self.cross_enabled = True
            self.assertIn("cursor_invalid", self.execute(cursor=cursor, speaker_ids=["654321"]).followup_context)
        old_bot = self.policy.bot_id
        self.policy.bot_id = "other-bot"
        try:
            self.assertIn("cursor_invalid", self.execute(cursor=cursors[0]).followup_context)
        finally:
            self.policy.bot_id = old_bot
        local = self.execute(memory_id="one", view="sources", speaker_ids=["123456"])
        self.assertNotIn("one-raw-0 的原话", local.followup_context)

    def test_pending_node_actions_keep_target_conversation_and_all_cursors_are_signed(self):
        for i in range(8):
            self.seed(f"episode-{i}", text_size=90)
        self.system.config.native_timeline_page_token_budget = 2000
        amounts = []
        for cards in (1, 4, 8):
            result = self.execute(
                memory_ids=[f"episode-{i}" for i in range(cards)],
                view="sources",
                speaker_ids=["123456"],
                conversation="group:200",
            )
            amounts.append(len(result.followup_context.encode("utf-8")))
            data = self.metadata(result)
            self.assertLessEqual(amounts[-1], OPEN_MEMORY_TOOL_SPEC.max_result_bytes)
            self.assertNotIn("memory-v1:", result.followup_context)
            self.assertLessEqual(len(json.dumps(result.trace_receipt, ensure_ascii=False).encode("utf-8")), 4096)
            for item in data["result"]["items"]:
                page = item.get("result") or {}
                for action in page.get("suggested_next_actions") or []:
                    self.assertEqual(action["conversation"], "group:200")
                    executed = self.execute(**action)
                    self.assertEqual(self.metadata(executed)["conversation"], "group:200")
        # One finite shared producer budget, plus bounded host cursor framing.
        self.assertLess(amounts[-1], amounts[0] * 8)

    def test_invalid_speakers_missing_nodes_and_hard_transport_failure_are_clear(self):
        self.seed("one", text_size=20)
        for value in ([], [123456], [" 123456"], ["123456\n"]):
            rejected = self.execute(memory_id="one", view="sources", speaker_ids=value, conversation="group:200")
            self.assertIn("invalid_arguments", rejected.followup_context)
            self.assertNotIn("one-raw-0 的原话", rejected.followup_context)
        missing = self.execute(
            memory_ids=["missing", "one"], view="sources", speaker_ids=["123456"], conversation="group:200"
        )
        self.assertEqual(self.metadata(missing)["result"]["items"][0]["reason"], "memory_not_found_or_out_of_scope")
        self.assertIn("one-raw-0 的原话", missing.followup_context)
        with patch.object(
            self.handler, "tool_spec", return_value=replace(OPEN_MEMORY_TOOL_SPEC, max_result_bytes=1000)
        ):
            limited = self.execute(memory_id="one", view="sources", speaker_ids=["123456"], conversation="group:200")
        payload = self.metadata(limited)
        self.assertEqual(payload["reason"], "source_page_exceeds_host_byte_budget")
        self.assertFalse(limited.followup_envelope.complete)
        self.assertNotIn("one-raw-0 的原话", limited.followup_context)
        self.assertEqual(payload["suggested_next_actions"][0]["conversation"], "group:200")


if __name__ == "__main__":
    unittest.main()
