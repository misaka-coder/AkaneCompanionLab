from __future__ import annotations

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import config
from companion_v01.memory_read_policy import MemoryReadPolicy
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.memcore_integration.timeline import MemcoreTimelineToolService
from companion_v01.plugin_conversation_refs import PluginConversationReferenceAuthority
from companion_v01.retrieval_engine import execute_retrieve_memory_tool
from companion_v01.store import MemoryStore
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.memory import (
    BrowseMemoryToolHandler, ListMemoryConversationsToolHandler,
    OpenMemoryToolHandler, ReadMemoryTimelineToolHandler,
)


class _Embedding:
    name = "hashed"
    version = "test"
    dimension = 8

    def embed_text(self, _text: str) -> list[float]:
        return [0.0] * self.dimension


class MemoryConversationSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.store = MemoryStore(root)
        self.authority = PluginConversationReferenceAuthority(root / "refs.key", instance_id="bot-a")
        self.cross_enabled = True
        self.policy = MemoryReadPolicy(
            store=self.store, bot_id="bot-a", master_qq="12345",
            enabled=lambda: self.cross_enabled, resolve_origin=self.authority.resolve,
            key=b"a" * 32,
        )
        for number in (100, 200):
            session = f"qq_group_shared_{number}"
            self.store.add_message(
                profile_user_id=session, session_id=session, character_pack_id="char-a",
                role="user", content=f"group {number}",
                memory_metadata={"client_mode": "qq_text"}, index_in_vector=False,
            )
        # A QQ-shaped local session without a verified QQ-origin message is not a target.
        self.store.add_message(
            profile_user_id="qq_group_shared_300", session_id="qq_group_shared_300",
            character_pack_id="char-a", role="user", content="local",
            memory_metadata={}, index_in_vector=False,
        )

    def context(self, *, kind: str, number: int, actor: int = 777,
                character: str = "char-a") -> ToolExecutionContext:
        if kind == "group":
            profile = session = f"qq_group_shared_{number}"
            ref = self.authority.issue_qq(
                profile_user_id=profile, session_id=session, character_pack_id=character,
                group_id=number, user_id=actor, actor_stable_id=f"qq:{actor}",
            )
        elif number == 12345:
            profile = session = "master"
            ref = self.authority.issue_qq(
                profile_user_id=profile, session_id=session, character_pack_id=character,
                user_id=number,
            )
        else:
            profile, session = f"qq_{number}", f"qq_pri_{number}"
            ref = self.authority.issue_qq(
                profile_user_id=profile, session_id=session, character_pack_id=character,
                user_id=number,
            )
        return ToolExecutionContext(
            profile, session, 0, {}, character_pack_id=character,
            client_mode="qq_text", request_context={"_memory_qq_ref": ref},
        )

    def test_authorization_matrix_and_current_default(self) -> None:
        owner = self.context(kind="direct", number=12345)
        group = self.context(kind="group", number=100, actor=987)
        stranger = self.context(kind="direct", number=555)
        for origin, selector, expected in (
            (owner, "group:100", "qq_group_shared_100"),
            (group, "master", "master"),
            (group, "group:200", "qq_group_shared_200"),
            (stranger, "current", "qq_pri_555"),
        ):
            target, args, error = self.policy.prepare(
                context=origin, call={"query": "记忆", "conversation": selector}, tool="retrieve_memory")
            self.assertIsNone(error)
            self.assertEqual(target.session_id, expected)
            self.assertEqual(args, {"query": "记忆"})
        for origin, selector, status in (
            (stranger, "master", "forbidden"),
            (stranger, "group:100", "forbidden"),
            (owner, "group:300", "conversation_unavailable"),
            (owner, "qq_pri_555", "invalid_conversation"),
            (owner, "all", "invalid_conversation"),
            (owner, "", "invalid_conversation"),
            (owner, ["group:100"], "invalid_conversation"),
        ):
            target, _, error = self.policy.prepare(
                context=origin, call={"conversation": selector}, tool="browse_memory")
            self.assertIsNone(target)
            self.assertEqual(error["status"], status)
        target, _, error = self.policy.prepare(context=group, call={}, tool="open_memory")
        self.assertIsNone(error)
        self.assertEqual(target.session_id, "qq_group_shared_100")
        self.cross_enabled = False
        target, _, error = self.policy.prepare(
            context=group, call={"conversation": "master"}, tool="open_memory")
        self.assertIsNone(target)
        self.assertEqual(error["status"], "cross_conversation_disabled")

    def test_signed_origin_and_cursor_cannot_be_reused(self) -> None:
        group = self.context(kind="group", number=100, actor=987)
        other_actor = self.context(kind="group", number=100, actor=654)
        target, _, error = self.policy.prepare(
            context=group, call={"conversation": "group:200"}, tool="browse_memory")
        self.assertIsNone(error)
        projected = self.policy.project(
            context=group, target=target, tool="browse_memory",
            result={"ok": True, "cards": [{"memory_id": "m1"}],
                    "next_cursor": "native-secret", "receipt": {"next_cursor": "native-secret"}},
        )
        cursor = projected["next_cursor"]
        self.assertNotIn("native-secret", cursor)
        self.assertEqual(projected["cards"][0]["conversation"], "group:200")
        self.assertEqual(projected["receipt"]["next_cursor"], cursor)
        continued, args, error = self.policy.prepare(
            context=group, call={"cursor": cursor}, tool="browse_memory")
        self.assertIsNone(error)
        self.assertEqual(continued.session_id, "qq_group_shared_200")
        self.assertEqual(args, {"cursor": "native-secret"})
        for origin, call, tool in (
            (other_actor, {"cursor": cursor}, "browse_memory"),
            (group, {"cursor": cursor}, "read_memory_timeline"),
            (group, {"cursor": cursor, "conversation": "current"}, "browse_memory"),
            (group, {"cursor": cursor + "x"}, "browse_memory"),
            (self.context(kind="group", number=100, actor=987, character="char-b"),
             {"cursor": cursor}, "browse_memory"),
        ):
            target, _, error = self.policy.prepare(context=origin, call=call, tool=tool)
            self.assertIsNone(target)
            self.assertEqual(error["status"], "cursor_invalid")
        another_bot = MemoryReadPolicy(
            store=self.store, bot_id="bot-b", master_qq="12345", enabled=lambda: True,
            resolve_origin=self.authority.resolve, key=b"a" * 32,
        )
        target, _, error = another_bot.prepare(
            context=group, call={"cursor": cursor}, tool="browse_memory")
        self.assertIsNone(target)
        self.assertEqual(error["status"], "cursor_invalid")
        self.cross_enabled = False
        target, _, error = self.policy.prepare(
            context=group, call={"cursor": cursor}, tool="browse_memory")
        self.assertIsNone(target)
        self.assertEqual(error["status"], "cross_conversation_disabled")
        self.cross_enabled = True
        forged = ToolExecutionContext("master", "master", 0, {}, character_pack_id="char-a",
                                      request_context=dict(group.request_context))
        self.assertTrue(self.policy.is_verified_qq_turn(
            profile_user_id=group.profile_user_id, session_id=group.session_id,
            character_pack_id="char-a", reference=group.request_context["_memory_qq_ref"]))
        self.assertFalse(self.policy.is_verified_qq_turn(
            profile_user_id="master", session_id="master", character_pack_id="char-a",
            reference=group.request_context["_memory_qq_ref"]))
        target, _, error = self.policy.prepare(
            context=forged, call={"conversation": "group:200"}, tool="browse_memory")
        self.assertIsNone(target)
        self.assertEqual(error["status"], "forbidden")

    def test_directory_pages_and_historical_authority(self) -> None:
        group = self.context(kind="group", number=100)
        cursor = ""
        names = []
        for _ in range(4):
            page = self.policy.list_conversations(context=group, cursor=cursor, limit=1)
            self.assertTrue(page["ok"])
            self.assertFalse(page["directory_complete"])
            self.assertLessEqual(len(page["items"]), 1)
            names.extend(item["conversation"] for item in page["items"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        self.assertEqual(names, ["group:100", "master", "group:200"])
        self.assertFalse(cursor)
        stranger = self.context(kind="direct", number=555)
        self.assertEqual([item["conversation"] for item in self.policy.list_conversations(
            context=stranger)["items"]], ["current"])
        self.cross_enabled = False
        self.assertEqual([item["conversation"] for item in self.policy.list_conversations(
            context=group)["items"]], ["group:100"])
        self.assertFalse(self.store.has_trusted_qq_group_session(
            "qq_group_shared_300", character_pack_id="char-a"))
        self.assertFalse(self.store.has_trusted_qq_group_session(
            "qq_group_shared_200", character_pack_id="char-b"))

    def test_group_directory_uses_message_character_when_session_keeps_older_character(self) -> None:
        group = "qq_group_shared_400"
        self.store.add_message(
            profile_user_id=group, session_id=group, character_pack_id="char-old",
            role="user", content="old character", memory_metadata={"client_mode": "qq_text"},
            index_in_vector=False,
        )
        self.store.add_message(
            profile_user_id=group, session_id=group, character_pack_id="char-a",
            role="user", content="current character", memory_metadata={"client_mode": "qq_text"},
            index_in_vector=False,
        )
        self.assertEqual(self.store.get_session(group, group)["character_pack_id"], "char-old")
        self.assertTrue(self.store.has_trusted_qq_group_session(group, character_pack_id="char-a"))
        self.assertIn(group, [row["session_id"] for row in self.store.list_trusted_qq_group_sessions(
            character_pack_id="char-a")])
        owner = self.context(kind="direct", number=12345)
        current_group = self.context(kind="group", number=100)
        for context in (owner, current_group):
            target, _, error = self.policy.prepare(
                context=context, call={"conversation": "group:400"}, tool="retrieve_memory")
            self.assertIsNone(error)
            self.assertEqual(target.session_id, group)
            self.assertIn("group:400", [item["conversation"] for item in
                self.policy.list_conversations(context=context)["items"]])

        other_group = "qq_group_shared_500"
        self.store.add_message(
            profile_user_id=other_group, session_id=other_group, character_pack_id="char-old",
            role="user", content="old character", memory_metadata={"client_mode": "qq_text"},
            index_in_vector=False,
        )
        self.store.add_message(
            profile_user_id=other_group, session_id=other_group, character_pack_id="char-a",
            role="user", content="unverified", memory_metadata={}, index_in_vector=False,
        )
        self.assertFalse(self.store.has_trusted_qq_group_session(other_group, character_pack_id="char-a"))
        self.assertFalse(self.policy.list_conversations(context=owner)["directory_complete"])

    def test_real_memcore_reads_one_namespace_and_recent_external_target(self) -> None:
        manager = MemcoreManager(
            backend="memcore", storage_path=Path(self.temporary.name) / "memcore.db",
            visible_scope="user", enable_flavor=True, shadow_compare=False,
            llm=object(), embedding_provider=_Embedding(),
        )
        try:
            manager.append_standalone_message(
                {"source_id": "secret-b", "content": "群 B 的暗号是紫色风铃。",
                 "timestamp": 1_777_700_000, "memory_metadata": {"retrieval_priority": "high"}},
                role="user", profile_user_id="qq_group_shared_200",
                session_id="qq_group_shared_200", character_pack_id="char-a",
            )
            current = self.context(kind="group", number=100)
            engine = SimpleNamespace(store=self.store, memcore_manager=manager,
                                     memory_read_policy=self.policy)
            with patch.object(config, "MEMORY_BACKEND", "memcore"):
                local = execute_retrieve_memory_tool(
                    engine, call={"query": "紫色风铃", "source_layers": ["raw"]}, context=current)
                remote = execute_retrieve_memory_tool(
                    engine, call={"query": "紫色风铃", "source_layers": ["raw"],
                                  "conversation": "group:200"}, context=current)
            self.assertNotIn("紫色风铃", local.followup_context)
            self.assertIn("紫色风铃", remote.followup_context)
            self.assertIn("conversation=group:200", remote.followup_context)
            self.assertEqual(remote.state_updates["memory_retrieval"]["retrieval_backend"], "memcore")
            self.assertEqual(manager._memory_config.visible_memory_scope, "user")
            system = manager._get_system_or_none(
                operation="test", profile_user_id="qq_group_shared_200",
                session_id="qq_group_shared_200", character_pack_id="char-a")
            self.assertEqual(system.config.visible_memory_scope, "conversation")
        finally:
            manager.close()

    def test_timeline_browse_open_and_directory_route_only_to_selected_target(self) -> None:
        calls = []

        def answer(operation):
            def run(**kwargs):
                calls.append((operation, kwargs))
                return {"ok": True, "status": "ok", "backend": "memcore", "text": "目标证据",
                        "cards": [{"memory_id": "m1"}] if operation == "browse" else [],
                        "coverage": {"complete": True}, "page_complete": True}
            return run

        manager = SimpleNamespace(
            enabled=True, available=True, read_memory_timeline=answer("timeline"),
            browse_memory=answer("browse"), open_memory=answer("open"),
        )
        service = MemcoreTimelineToolService(legacy_service=None, memcore_manager=manager)
        handlers = (
            (ReadMemoryTimelineToolHandler(timeline_service=service,
                                           read_policy_provider=lambda: self.policy),
             {"type": "read_memory_timeline", "date_from": "2026-09-27"}),
            (BrowseMemoryToolHandler(timeline_service=service,
                                     read_policy_provider=lambda: self.policy),
             {"type": "browse_memory", "keywords": ["目标"]}),
            (OpenMemoryToolHandler(timeline_service=service,
                                   read_policy_provider=lambda: self.policy),
             {"type": "open_memory", "memory_id": "m1", "view": "content"}),
        )
        group = self.context(kind="group", number=100)
        with patch.object(config, "MEMORY_BACKEND", "memcore"):
            for handler, call in handlers:
                normalized = handler.normalize_call({**call, "conversation": "group:200"})
                result = handler.execute(call=normalized, context=group)
                self.assertIn("conversation=group:200", result.followup_context)
        self.assertEqual([operation for operation, _ in calls], ["timeline", "browse", "open"])
        self.assertTrue(all(kwargs["session_id"] == "qq_group_shared_200" for _, kwargs in calls))
        self.assertTrue(all(kwargs["profile_user_id"] == "qq_group_shared_200" for _, kwargs in calls))
        self.assertTrue(all(kwargs["arguments"]["cross_conversation"] is False for _, kwargs in calls))
        directory = ListMemoryConversationsToolHandler(read_policy_provider=lambda: self.policy)
        result = directory.execute(call={"type": "list_memory_conversations", "limit": 3}, context=group)
        self.assertIn('"conversation": "group:200"', result.followup_context)
        self.assertNotIn("group:300", result.followup_context)


if __name__ == "__main__":
    unittest.main()
