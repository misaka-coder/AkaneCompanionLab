"""Real host + MemCore private dialogue fan-out, without QQ network delivery."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import unittest
import uuid
from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from memcore import Actor, EntryOrigin, TimelineEntryInput, TurnRole

from companion_v01.channels.qq.commands.parser import resolve_qq_command
from companion_v01.channels.qq.context import QQMessageContext
from companion_v01.channels.qq.gateway import NapCatQQGateway
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.owner_private_group_memory import OwnerPrivateGroupMemory
from companion_v01.owner_private_session import OwnerPrivateSessionPolicy
from companion_v01.plugin_conversation_refs import PluginConversationReferenceAuthority
from companion_v01.store import MemoryStore
from companion_v01.turn_coordination import TurnCoordinator
from tests.test_memcore_integration import _FakeLLM
from tests.test_memory_conversation_selection import _Embedding


class OwnerPrivateGroupMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.manager = MemcoreManager(backend="memcore", storage_path=root / "core.sqlite3",
                                     visible_scope="conversation", enable_flavor=False, shadow_compare=False,
                                     llm=_FakeLLM(), embedding_provider=_Embedding())
        self.addCleanup(self.manager.close)
        self.store = MemoryStore(root / "host")
        self.gateway = NapCatQQGateway(state_path=root / "qq.json", default_character_pack_id="cecilia")
        self.coordinator = TurnCoordinator()
        refs = PluginConversationReferenceAuthority(root / "refs.key", instance_id="bot")
        self.policy = OwnerPrivateSessionPolicy(enabled=True, master_qq="12345", references=refs,
                                               manager=self.manager, store=self.store)
        self.engine = SimpleNamespace(store=self.store, memcore_manager=self.manager, owner_private_policy=self.policy)
        self.master_patch = patch.object(type(self.gateway), "master_qq", new_callable=lambda: property(lambda self: "12345"))
        self.master_patch.start()
        self.addCleanup(self.master_patch.stop)
        self.mirror = OwnerPrivateGroupMemory(engine=self.engine, gateway=self.gateway,
                                             coordinator=self.coordinator, bot_id="bot", enabled=True)
        self.engine.owner_private_group_memory = self.mirror
        self.manager.owner_private_group_memory = self.mirror
        self.qq = {"user_id": "master", "real_user_id": "master", "character_pack_id": "cecilia", "client_mode": "qq_text",
                   "_memory_qq_ref": refs.issue_qq(profile_user_id="master", session_id="master", character_pack_id="cecilia", user_id=12345)}
        self.desktop = {"user_id": "desktop", "real_user_id": "master", "character_pack_id": "cecilia", "client_mode": "desktop_pet"}
        self.policy.stamp_desktop(self.desktop, owner_authenticated=True, character="cecilia")
        self.session = self.group(87)

    def group(self, group_id, *, character="cecilia", enable=True, trusted=True):
        session = f"qq_group_shared_{group_id}"
        self.store.add_message(profile_user_id=session, session_id=session, character_pack_id=character,
                               role="user", content="群内真实消息", memory_metadata={"client_mode": "qq_text"} if trusted else {})
        if enable:
            self.assertTrue(self.gateway._preferences.set_private_memory_enabled(group_id, True))
        return session

    def system(self, session="master", character="cecilia"):
        return self.manager._get_system(profile_user_id=session, session_id=session, character_pack_id=character, warm_index=False)

    def complete(self, *, desktop=False, tools=False, character="cecilia", payload=None, notify=True):
        payload = dict(payload or (self.desktop if desktop else self.qq))
        source_id, final_id = uuid.uuid4().hex, uuid.uuid4().hex
        args = dict(profile_user_id="master", session_id="desktop" if desktop else "master", character_pack_id=character)
        channel = "desktop" if desktop else "qq_private"
        with self.policy.scope(payload, character=character):
            begin = self.manager.begin_input_turn({"source_id": source_id, "content": "周末一起做草莓蛋糕。", "timestamp": int(time.time()) - 20, "channel": channel},
                                                  actor_stable_id="desktop:master" if desktop else "", **args)
            self.assertTrue(begin["ok"], begin)
            system = self.system(character=character)
            if tools:
                system.record_tool_exchange(turn_id=begin["turn_id"], tool_name="web_search", tool_call_id="native.call",
                                            tool_input={"query": "食谱"}, result="工具专有轨迹，不得复制")
            with patch.object(self.mirror, "notify_completion", wraps=self.mirror.notify_completion if notify else lambda: None):
                final = self.manager.complete_input_turn(
                    turn_id=begin["turn_id"], assistant_record={"source_id": final_id, "content": "好呀，一起做吧。", "timestamp": int(time.time())},
                    memory_metadata={"topic_terms": ["草莓蛋糕"]},
                    provider_output_raw=json.dumps({"utterances": [{"text": "好呀，一起做吧。", "say": "一緒に作りましょう。", "style": "calm"}],
                                                   "memory_metadata": {"topic_terms": ["草莓蛋糕"]}}, ensure_ascii=False), **args,
                )
                self.assertTrue(final["ok"], final)
        return begin["turn_id"]

    def deliveries(self):
        with closing(sqlite3.connect(self.store.db_path)) as conn:
            return conn.execute("SELECT session_id, status FROM private_memory_mirror_deliveries ORDER BY session_id").fetchall()

    def group_entries(self, session=None):
        return self.system(session or self.session).store.list_prompt_visible_entries(namespace=self.system(session or self.session).namespace)

    def test_only_user_and_final_are_copied_and_default_groups_stay_off(self):
        off = self.group(42, enable=False)
        self.complete(tools=True)
        self.mirror._drain(self.session)
        entries = self.group_entries()
        self.assertEqual([entry.kind for entry in entries], ["message.user", "message.assistant"])
        self.assertEqual(entries[0].namespace.actor.stable_id, "qq:12345")
        self.assertEqual(entries[1].target_actor.stable_id, "qq:12345")
        self.assertEqual(entries[1].reply_to_source_id, entries[0].source_id)
        self.assertNotIn("工具专有轨迹", str([entry.to_record() for entry in entries]))
        self.assertEqual(self.group_entries(off), [])
        self.assertEqual(self.deliveries(), [(self.session, "applied")])
        self.assertEqual(len(self.system().get_turn_entries(entries[0].trace_metadata["source_turn_id"])), 4)

    def test_desktop_source_and_original_assistant_output_are_retained(self):
        self.complete(desktop=True)
        self.mirror._drain(self.session)
        projection = self.system(self.session).build_context_projection(provider_profile="openai_chat").payloads
        self.assertIn("channel: desktop", projection[0]["content"])
        assistant = projection[1]["content"]
        self.assertIn("过往发言", assistant)
        self.assertIn("好呀，一起做吧。", assistant)
        self.assertNotIn("一緒に", assistant)
        self.assertNotIn("[hidden]", assistant)
        self.assertNotIn("memory_metadata", assistant)
        self.assertIn("一緒に", self.group_entries()[1].payload["provider_output_raw"])

    def test_background_import_does_not_consume_owner_messages_as_steering(self):
        async def exercise():
            entered, release = asyncio.Event(), asyncio.Event()

            async def drain(_session):
                entered.set()
                await release.wait()

            with patch.object(self.mirror, "_drain_async", drain):
                task = asyncio.create_task(self.mirror._run_group(self.session))
                try:
                    await asyncio.wait_for(entered.wait(), 1)
                    result = self.coordinator.offer_steer(
                        profile_user_id=self.session, session_id=self.session, actor_id="qq:12345",
                        content="现在回答我", allow_other_actor=True,
                    )
                    self.assertEqual(result["status"], "busy_maintenance")
                    self.assertFalse(result["ok"])
                finally:
                    release.set()
                    await task
        asyncio.run(exercise())

    def test_restart_restores_unacknowledged_private_completion(self):
        self.complete(notify=False)
        recovered = OwnerPrivateGroupMemory(engine=self.engine, gateway=self.gateway, coordinator=TurnCoordinator(), bot_id="bot", enabled=True)
        recovered._drain(self.session)
        self.assertEqual(len(self.group_entries()), 2)
        recovered._drain(self.session)
        self.assertEqual(len(self.group_entries()), 2)

    def test_multiple_completed_pairs_keep_order_and_deduplicate(self):
        turns = [self.complete(desktop=index % 2 == 1) for index in range(3)]
        self.mirror._drain(self.session)
        entries = self.group_entries()
        self.assertEqual(len(entries), 6)
        self.assertEqual([entry.trace_metadata["source_turn_id"] for entry in entries],
                         [turn for turn in turns for _ in range(2)])
        self.assertEqual(len({entry.turn_id for entry in entries}), 3)
        self.assertEqual([entry.payload["channel"] for entry in entries],
                         ["qq_private", "qq_private", "desktop", "desktop", "qq_private", "qq_private"])
        self.mirror._drain(self.session)
        self.assertEqual(len(self.group_entries()), 6)

    def test_crash_after_group_commit_retries_without_duplicates(self):
        self.complete()
        original = self.mirror.receipts.finish
        crashed = False
        def fail_once(key, session, status, **kwargs):
            nonlocal crashed
            if status == "applied" and not crashed:
                crashed = True
                raise OSError("after_commit")
            return original(key, session, status, **kwargs)
        with patch.object(self.mirror.receipts, "finish", side_effect=fail_once):
            self.mirror._drain(self.session)
        self.assertEqual(len(self.group_entries()), 2)
        with self.mirror.receipts._connect() as conn:
            conn.execute("UPDATE private_memory_mirror_deliveries SET available_at=0")
        self.mirror._drain(self.session)
        self.assertEqual(len(self.group_entries()), 2)
        self.assertEqual(self.deliveries()[0][1], "applied")

    def test_disabled_then_reenabled_does_not_replay_pending_or_old_history(self):
        self.complete()
        preferences = self.gateway._preferences
        self.assertTrue(preferences.set_private_memory_enabled(87, False))
        self.assertTrue(preferences.set_private_memory_enabled(87, True))
        self.mirror._drain(self.session)
        self.assertEqual(self.group_entries(), [])
        self.assertEqual(self.deliveries()[0][1], "skipped_disabled")
        self.complete()
        self.mirror._drain(self.session)
        self.assertEqual(len(self.group_entries()), 2)

    def test_no_cross_bot_character_or_untrusted_group(self):
        other_role = self.group(99, character="reimu")
        untrusted = self.group(88, trusted=False)
        self.complete()
        other_bot = OwnerPrivateGroupMemory(engine=self.engine, gateway=self.gateway, coordinator=TurnCoordinator(), bot_id="other", enabled=True)
        other_bot._drain(self.session)
        self.assertEqual(self.group_entries(), [])
        self.assertEqual(self.group_entries(other_role), [])
        self.assertEqual(self.group_entries(untrusted), [])
        self.mirror._drain(self.session)
        self.assertEqual(len(self.group_entries()), 2)

    def test_forged_origin_and_unshared_desktop_are_not_registered(self):
        self.complete(payload={**self.qq, "_memory_qq_ref": "forged"})
        self.policy.enabled = False
        self.complete(desktop=True)
        self.assertEqual(self.deliveries(), [])

    def test_enabled_after_completion_has_no_historical_backfill(self):
        self.gateway._preferences.set_private_memory_enabled(87, False)
        self.complete()
        self.gateway._preferences.set_private_memory_enabled(87, True)
        self.mirror._drain(self.session)
        self.assertEqual(self.group_entries(), [])
        self.complete()
        self.mirror._drain(self.session)
        self.assertEqual(len(self.group_entries()), 2)

    def test_group_failure_does_not_stop_other_group(self):
        second = self.group(42)
        self.complete()
        original = self.manager.import_private_exchange
        def fail_one(job, snapshot, *, session):
            if session == self.session:
                raise RuntimeError("one_group_failure")
            return original(job, snapshot, session=session)
        with patch.object(self.manager, "import_private_exchange", side_effect=fail_one):
            self.mirror._drain(self.session)
            self.mirror._drain(second)
        self.assertEqual(len(self.group_entries(second)), 2)
        self.assertEqual(self.group_entries(), [])

    def test_busy_group_defers_pair_then_next_context_keeps_original_prefix(self):
        async def run():
            await self.mirror.start()
            async with self.coordinator.hold(self.session, self.session, turn_kind="group_chat"):
                group = self.system(self.session)
                handle = group.begin_turn(stimuli=[TimelineEntryInput(kind="message.user", origin=EntryOrigin.USER,
                    turn_role=TurnRole.STIMULUS, semantic_text="群任务进行中", payload={"channel": "qq_group"})])
                before = group.build_context_projection(provider_profile="openai_chat").payloads
                await asyncio.to_thread(self.complete, tools=True)
                await asyncio.sleep(.03)
                self.assertEqual(len(self.group_entries()), 1)
                group.complete_turn(turn_id=handle.turn_id, semantic_text="群任务完成", provider_output_raw="群任务完成")
                previous = group.build_context_projection(provider_profile="openai_chat").payloads
            async with self.coordinator.hold(self.session, self.session, turn_kind="group_chat"):
                after = group.build_context_projection(provider_profile="openai_chat").payloads
                self.assertEqual(after[:len(previous)], previous)
                self.assertEqual(previous[:len(before)], before)
                self.assertEqual(len(after), 4)
            self.assertTrue(await self.mirror.close())
        asyncio.run(run())

    def test_recovery_before_private_final_never_writes_half_pair(self):
        system = self.system()
        with self.policy.scope(self.qq):
            handle = system.begin_turn(stimuli=[TimelineEntryInput(source_id="input", kind="message.user", origin=EntryOrigin.USER,
                turn_role=TurnRole.STIMULUS, semantic_text="尚未回复", payload={"channel": "qq_private"})])
            self.mirror.prepare_completion(system=system, turn_id=handle.turn_id, final_id="final", character_id="cecilia")
        self.mirror._drain(self.session)
        self.assertEqual(self.group_entries(), [])
        system.abort_turn(handle.turn_id, reason="failed")
        with self.mirror.receipts._connect() as conn:
            conn.execute("UPDATE private_memory_mirror_deliveries SET available_at=0")
        self.mirror._drain(self.session)
        self.assertEqual(self.deliveries()[0][1], "skipped_no_final")

    def test_command_status_and_owner_only_write_permission(self):
        for text in ("/私人记忆", "/私人记忆 状态", "/私人记忆 开", "/私人记忆 关"):
            self.assertFalse(resolve_qq_command(text).error)
        self.assertTrue(resolve_qq_command("/私人记忆 同步工具").error)
        context = QQMessageContext(should_respond=True, reason="command", is_group=True, group_id=87,
                                   user_id=22222, clean_message="/私人记忆 关")
        result = self.gateway.handle_private_memory_command(context, available=True)
        self.assertEqual(result["status"], "forbidden")
        context = replace(context, user_id=12345)
        self.assertTrue(self.gateway.handle_private_memory_command(context, available=True)["ok"])
        self.assertFalse(self.gateway._preferences.private_memory_policy(87)[0])

    def test_preference_save_failure_rolls_back_and_restart_preserves_generation(self):
        preferences = self.gateway._preferences
        initial = preferences.private_memory_policy(87)
        with patch.object(preferences, "_persist_gateway_state", return_value=False):
            self.assertFalse(preferences.set_private_memory_enabled(87, False))
        self.assertEqual(preferences.private_memory_policy(87), initial)
        preferences._load_persisted_state()
        self.assertEqual(preferences.private_memory_policy(87), initial)

    def test_steering_inputs_are_copied_with_their_original_completed_turn(self):
        args = dict(profile_user_id="master", session_id="master", character_pack_id="cecilia")
        with self.policy.scope(self.qq):
            begin = self.manager.begin_input_turn({"source_id": "first", "content": "想做草莓蛋糕。", "channel": "qq_private"}, **args)
            appended = self.manager.append_turn_user_input({"source_id": "steer", "content": "记得少放糖。", "channel": "qq_private"},
                                                          turn_id=begin["turn_id"], **args)
            self.assertTrue(appended["ok"], appended)
            result = self.manager.complete_input_turn(turn_id=begin["turn_id"], assistant_record={"source_id": "final", "content": "好，少糖的草莓蛋糕。"},
                                                       memory_metadata={}, **args)
            self.assertTrue(result["ok"], result)
        self.mirror._drain(self.session)
        entries = self.group_entries()
        self.assertEqual([entry.semantic_text for entry in entries], ["想做草莓蛋糕。", "记得少放糖。", "好，少糖的草莓蛋糕。"])
        self.assertEqual(len({entry.turn_id for entry in entries}), 1)

    def test_formal_voice_completion_is_copied_once_without_provisional_events(self):
        args = dict(profile_user_id="master", session_id="desktop", character_pack_id="cecilia")
        def record(projection_id, kind, payload):
            return {"projection_id": projection_id, "kind": kind, "target": "memcore",
                    "source_event_id": projection_id + ".event", "payload": {"voice_turn_id": "voice", **payload}}
        with self.policy.scope(self.desktop):
            provisional = self.manager.record_voice_projection(record("provisional", "event.voice.asr.provisional", {"text": "草莓"}), **args)
            self.assertTrue(provisional["ok"], provisional)
            user = self.manager.record_voice_projection(record("user", "message.user.voice", {"text": "一起做草莓蛋糕吧。", "channel": "desktop"}),
                                                         actor_stable_id="desktop:master", **args)
            self.assertTrue(user["ok"], user)
            self.mirror._drain(self.session)
            self.assertEqual(self.group_entries(), [])
            final = record("assistant", "message.assistant.voice", {"full_text": "好呀，一起做吧。", "full_text_status": "complete",
                           "delivered_units": [0], "interrupted_units": [], "memory_metadata": {"topic_terms": ["蛋糕"]}})
            for _ in range(2):
                completed = self.manager.record_voice_projection(final, **args)
                self.assertTrue(completed["ok"], completed)
        self.mirror._drain(self.session)
        entries = self.group_entries()
        self.assertEqual([entry.kind for entry in entries], ["message.user.voice", "message.assistant.voice"])
        self.assertEqual([entry.payload.get("channel") for entry in entries], ["desktop", "desktop"])
        self.assertEqual(self.deliveries(), [(self.session, "applied")])

    def test_reserved_final_write_can_be_retried_with_a_new_final_id(self):
        system = self.system()
        args = dict(profile_user_id="master", session_id="master", character_pack_id="cecilia")
        with self.policy.scope(self.qq):
            begin = self.manager.begin_input_turn({"source_id": "retry.user", "content": "周末见。", "channel": "qq_private"}, **args)
            self.mirror.prepare_completion(system=system, turn_id=begin["turn_id"], final_id="uncommitted.final", character_id="cecilia")
            completed = self.manager.complete_input_turn(turn_id=begin["turn_id"], assistant_record={"source_id": "actual.final", "content": "周末见。"},
                                                         memory_metadata={}, **args)
            self.assertTrue(completed["ok"], completed)
        self.mirror._drain(self.session)
        self.assertEqual(self.group_entries()[-1].trace_metadata["source_id"], "actual.final")
        self.assertEqual(self.deliveries(), [(self.session, "applied")])

    def test_silent_completion_and_host_events_are_not_registered(self):
        args = dict(profile_user_id="master", session_id="master", character_pack_id="cecilia")
        with self.policy.scope(self.qq):
            begin = self.manager.begin_input_turn({"source_id": "silent.user", "content": "不用回复。"}, **args)
            completed = self.manager.complete_input_turn(turn_id=begin["turn_id"], assistant_record={}, memory_metadata={}, append_final=False, **args)
            self.assertTrue(completed["ok"], completed)
            system = self.system()
            event = system.begin_turn(stimuli=[TimelineEntryInput(kind="event.proactive", origin=EntryOrigin.ENVIRONMENT,
                turn_role=TurnRole.STIMULUS, semantic_text="观察事件")])
            self.mirror.prepare_completion(system=system, turn_id=event.turn_id, final_id="event.final", character_id="cecilia")
        self.assertEqual(self.deliveries(), [])

    def test_deleted_target_is_not_recreated_from_pending_delivery(self):
        self.complete()
        with closing(sqlite3.connect(self.store.db_path)) as conn:
            conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (self.session,))
            conn.commit()
        self.mirror._drain(self.session)
        self.assertEqual(self.group_entries(), [])
        self.assertEqual(self.deliveries()[0][1], "skipped_invalid_target")

    def test_global_pause_preserves_pending_until_resume(self):
        self.complete()
        self.mirror.enabled = False
        self.mirror._drain(self.session)
        self.assertEqual(self.group_entries(), [])
        self.assertEqual(self.deliveries()[0][1], "pending")
        self.mirror.enabled = True
        self.mirror._drain(self.session)
        self.assertEqual(len(self.group_entries()), 2)

    def test_unavailable_configuration_does_not_advertise_or_poll_the_feature(self):
        for target, field, value in ((self.policy, "enabled", False), (self.policy, "master_qq", ""),
                                     (self.manager, "backend", "dual")):
            with patch.object(target, field, value):
                disabled = OwnerPrivateGroupMemory(engine=self.engine, gateway=self.gateway,
                                                  coordinator=TurnCoordinator(), bot_id="bot", enabled=True)
                self.assertFalse(disabled.enabled)
                async def check():
                    with patch.object(disabled.receipts, "due_sessions", side_effect=AssertionError("disabled must not poll")):
                        await disabled.start()
                        self.assertIsNone(disabled._pump_task)
                        self.assertTrue(await disabled.close())
                asyncio.run(check())

    def test_owner_status_reports_only_local_pending_and_safe_failure_reason(self):
        self.complete()
        self.assertEqual(self.mirror.status(self.session)["pending"], 1)
        with patch.object(self.manager, "import_private_exchange", side_effect=RuntimeError("private text must not appear")):
            self.mirror._drain(self.session)
        state = self.mirror.status(self.session)
        self.assertEqual(state, {"pending": 0, "retry_pending": 1, "reason": "RuntimeError"})
        context = QQMessageContext(should_respond=True, reason="command", is_group=True, group_id=87,
                                   user_id=12345, clean_message="/私人记忆 状态")
        response = self.gateway.handle_private_memory_command(context, available=True, queue_status=state)
        self.assertIn("重试 1 轮", response["reply"])
        self.assertNotIn("private text", response["reply"])
        self.assertEqual(self.mirror.status("qq_group_shared_42")["retry_pending"], 0)

    def test_hot_reads_use_group_and_pending_indexes_instead_of_scanning_history(self):
        def check_queries(owner, callback, first_table, index_fragment):
            queries = []
            original = owner._connect
            @contextmanager
            def traced():
                with original() as conn:
                    conn.set_trace_callback(queries.append)
                    yield conn
            with patch.object(owner, "_connect", traced):
                callback()
            with original() as conn:
                for query in (query for query in queries if query.lstrip().upper().startswith("SELECT")):
                    plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + query)]
                    self.assertTrue(plan[0].startswith(f"SEARCH {first_table} "), plan)
                    fragments = (index_fragment,) if isinstance(index_fragment, str) else index_fragment
                    self.assertTrue(any(fragment in plan[0] for fragment in fragments), plan)
        check_queries(self.store, lambda: self.store.has_trusted_qq_group_session(self.session, character_pack_id="cecilia"), "m",
                      ("session_id=?", "profile_user_id=?"))
        check_queries(self.mirror.receipts, lambda: self.mirror.receipts.pending("bot", self.session), "d", "private_memory_mirror_pending")
        check_queries(self.mirror.receipts, lambda: self.mirror.status(self.session), "d", "private_memory_mirror_pending")


if __name__ == "__main__":
    unittest.main()
