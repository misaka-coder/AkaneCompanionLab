import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from companion_v01.owner_private_session import OwnerPrivateSessionPolicy, memory_scope
from companion_v01.plugin_conversation_refs import PluginConversationReferenceAuthority
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.memory_read_policy import MemoryReadPolicy
from companion_v01.engine_services.response_builder import _shape_capability_catalog_projection
from tests.test_memory_conversation_selection import _Embedding


class OwnerPrivateSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.refs = PluginConversationReferenceAuthority(Path(self.temp.name) / "refs.key", instance_id="one")
        self.manager = SimpleNamespace()
        self.policy = OwnerPrivateSessionPolicy(enabled=True, master_qq="12345", references=self.refs, manager=self.manager)
        self.desktop = {"user_id": "desktop-session", "real_user_id": "master", "character_pack_id": "cecilia", "client_mode": "desktop_pet"}
        self.policy.stamp_desktop(self.desktop, owner_authenticated=True, character="cecilia")

    def test_binding_requires_owner_origin_and_preserves_delivery_identity(self):
        self.assertEqual(self.policy.resolve(self.desktop).memory_identity, ("master", "master"))
        self.assertEqual(self.desktop["user_id"], "desktop-session")
        for changes in (
            {"_memory_desktop_ref": "forged"}, {"real_user_id": "stranger"},
            {"character_pack_id": "reimu"}, {"client_mode": "web"},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(self.policy.resolve({**self.desktop, **changes}).shared)
        public = dict(self.desktop)
        self.policy.stamp_desktop(public, owner_authenticated=False, character="cecilia")
        self.assertFalse(self.policy.resolve(public).shared)
        self.policy.enabled = False
        self.assertEqual(self.policy.resolve(self.desktop).memory_identity, ("master", "desktop-session"))

    def test_scoped_calls_do_not_leak_to_other_manager_role_or_conversation(self):
        @memory_scope
        def call(manager, **kwargs):
            return kwargs
        physical = dict(profile_user_id="master", session_id="desktop-session", character_pack_id="cecilia")
        with self.policy.scope(self.desktop):
            self.assertEqual(call(self.manager, **physical)["session_id"], "master")
            self.assertEqual(call(object(), **physical)["session_id"], "desktop-session")
            self.assertEqual(call(self.manager, **{**physical, "character_pack_id": "reimu"})["session_id"], "desktop-session")
            self.assertEqual(call(self.manager, **{**physical, "session_id": "qq_group_shared_87"})["session_id"], "qq_group_shared_87")
        self.assertEqual(call(self.manager, **physical)["session_id"], "desktop-session")

    def test_private_material_handles_are_shared_but_unverified_access_is_not(self):
        from companion_v01.store import MemoryStore
        from companion_v01.owner_private_session import private_asset_scope
        store = MemoryStore(Path(self.temp.name)/"files")
        self.policy.store = store
        qq = store.add_attachment_inbox_item(profile_user_id="master", session_id="master", origin_name="qq.pdf")
        with self.policy.scope(self.desktop):
            self.assertIsNotNone(store.get_attachment_inbox_item(profile_user_id="master", session_id="desktop-session", attachment_id=qq["attachment_id"]))
            desktop = store.add_attachment_inbox_item(profile_user_id="master", session_id="desktop-session", origin_name="desktop.pdf")
            # Worker ownership is captured before dispatch, not when it finishes.
            @private_asset_scope
            def admit(service, **kwargs):
                return kwargs
            captured = admit(SimpleNamespace(store=store), profile_user_id="master", session_id="desktop-session")
        self.assertEqual(captured, {"profile_user_id": "master", "session_id": "master"})
        self.assertIsNotNone(store.get_attachment_inbox_item(profile_user_id="master", session_id="master", attachment_id=desktop["attachment_id"]))
        self.assertIsNone(store.get_attachment_inbox_item(profile_user_id="master", session_id="desktop-session", attachment_id=qq["attachment_id"]))
        with self.policy.scope({**self.desktop, "_memory_desktop_ref": "forged"}):
            self.assertIsNone(store.get_attachment_inbox_item(profile_user_id="stranger", session_id="master", attachment_id=qq["attachment_id"]))

    def test_voice_continues_shared_history_and_releases_turn_after_settlement(self):
        from dataclasses import replace
        from tests.test_voice_runtime_production import VoiceRuntimeProductionTests, _ThinkingEngine, _Adapter, _open_request, _commit_realtime_turn
        from companion_v01.turn_coordination import TurnCoordinator
        helper = VoiceRuntimeProductionTests()
        manager = helper._manager(Path(self.temp.name))
        self.addCleanup(manager.close)
        self.policy.manager = manager
        engine = _ThinkingEngine(manager)
        engine.owner_private_policy = self.policy
        engine.turn_coordinator = TurnCoordinator()
        service = helper._service(root=Path(self.temp.name), manager=manager, adapter=_Adapter(), engine=engine)
        self.addCleanup(service.close)
        request = replace(_open_request(), profile_user_id="master", session_id="desktop-session",
                          character_pack_id="cecilia", conversation_ref=self.desktop["_memory_desktop_ref"])

        async def run():
            result = service.create_coordinator(request)
            self.assertTrue(result.ready, result)
            async with engine.turn_coordinator.hold("master", "master", channel="qq"):
                queued = asyncio.create_task(_commit_realtime_turn(result.coordinator))
                await asyncio.sleep(.02)
                self.assertFalse(queued.done())
                self.assertFalse(result.coordinator.bridge.host.snapshot.input_turns)
            await asyncio.wait_for(queued, 3)
            self.assertTrue(await asyncio.to_thread(service.wait_idle, timeout=3))
            await asyncio.sleep(.02)
            self.assertFalse(engine.turn_coordinator.is_busy("master", "master"))
            self.assertEqual(engine.calls[0]["conversation_ref"], self.desktop["_memory_desktop_ref"])
            projection = manager.build_context_projection(profile_user_id="master", session_id="master", character_pack_id="cecilia", provider_profile="openai_chat")
            rendered = json.dumps(projection, ensure_ascii=False, default=str)
            self.assertIn("请继续讲这个方案", rendered)
            self.assertIn("channel: desktop", rendered)
        asyncio.run(run())

    def test_shared_project_selection_keeps_same_owner_and_rejects_unsigned_alias(self):
        from companion_v01.store import MemoryStore
        from companion_v01.project_workspace import ProjectWorkspaceService
        store = self.policy.store = MemoryStore(Path(self.temp.name) / "host-projects")
        service = ProjectWorkspaceService(store=store, execution_workspace_root=Path(self.temp.name) / "work")
        qq = service.scope_for(profile_user_id="master", session_id="master", client_mode="qq_text")
        project = service.create(scope=qq, display_name="Shared project")
        service.select(scope=qq, workspace_id=project["workspace_id"])
        with self.policy.scope(self.desktop):
            desktop = service.scope_for(profile_user_id="master", session_id="desktop-session", client_mode="desktop_pet")
            self.assertEqual(qq, desktop)
            self.assertEqual(service.current(scope=desktop)["workspace_id"], project["workspace_id"])
        with self.policy.scope({**self.desktop, "_memory_desktop_ref": "forged"}):
            independent = service.scope_for(profile_user_id="master", session_id="desktop-session", client_mode="desktop_pet")
            self.assertNotEqual(qq, independent)
            self.assertIsNone(service.current(scope=independent))

    def test_background_worker_restores_signed_scope_after_foreground_ends(self):
        from concurrent.futures import ThreadPoolExecutor
        from companion_v01.host_tool_jobs import HostToolJobRuntime
        from companion_v01.host_jobs import HostJobOwner
        from companion_v01.owner_private_session import current_private_scope
        engine = SimpleNamespace(owner_private_policy=self.policy,
            _resolve_payload_character_pack_id=lambda payload: payload['character_pack_id'])
        worker = HostToolJobRuntime.__new__(HostToolJobRuntime)
        worker.engine = engine
        worker._execute_in_scope = lambda job, origin: (current_private_scope(engine).memory_identity, origin)
        job = SimpleNamespace(owner=HostJobOwner("master", "desktop-session"), character_pack_id="cecilia",
            payload={"client_mode": "desktop_pet"}, channel="desktop_pet", delivery_target=self.desktop['_memory_desktop_ref'])
        with ThreadPoolExecutor(max_workers=1) as pool:
            identity, origin = pool.submit(worker._execute, job).result()
        self.assertEqual(identity, ("master", "master"))
        self.assertEqual(origin['user_id'], "desktop-session")
        self.assertEqual(origin['_memory_desktop_ref'], job.delivery_target)
        self.assertEqual(job.owner.session_id, "desktop-session")

    def test_voice_lease_waits_for_both_barge_in_and_worker_tail(self):
        from companion_v01.turn_coordination import TurnCoordinator
        from companion_v01.voice_runtime.private_session import PrivateVoiceLease
        async def run():
            coordinator = TurnCoordinator()
            lease = PrivateVoiceLease(coordinator, "cecilia")
            await lease.acquire("first")
            await lease.acquire("barge-in")
            admitted = []
            async def qq_turn():
                async with coordinator.hold("master", "master", channel="qq"):
                    admitted.append("qq")
            queued = asyncio.create_task(qq_turn())
            host = SimpleNamespace(snapshot=SimpleNamespace(input_turns={}))
            busy = {"first"}
            executor = SimpleNamespace(has_running_turn=lambda turn: turn in busy)
            lease.projection_settled("first")
            lease.projection_settled("barge-in")
            lease.settle_failed_inputs(host, executor)
            await asyncio.sleep(.01)
            self.assertEqual(admitted, [])
            busy.clear()
            lease.settle_failed_inputs(host, executor)
            await asyncio.wait_for(queued, 1)
            self.assertEqual(admitted, ["qq"])
            self.assertFalse(coordinator.is_busy("master", "master"))
        asyncio.run(run())

    def test_voice_activity_replay_does_not_restart_private_cooldown(self):
        from companion_v01.store import MemoryStore
        from companion_v01.voice_runtime.private_session import PrivateVoiceMemory
        store = self.policy.store = MemoryStore(Path(self.temp.name) / "voice-activity")
        manager = SimpleNamespace(record_voice_projection=lambda record, **kwargs: {"ok": True, "status": "ok"})
        port = PrivateVoiceMemory(manager, self.policy, self.desktop)
        port.record_voice_projection({"kind": "message.user.voice", "payload": {}}, timestamp=100)
        manager.record_voice_projection = lambda record, **kwargs: {"ok": True, "status": "duplicate"}
        port.record_voice_projection({"kind": "message.user.voice", "payload": {}}, timestamp=200)
        self.assertEqual(store.owner_private_activity(character_pack_id="cecilia")['last_user_at'], 100)
        self.assertEqual(store.owner_private_activity(character_pack_id="reimu")['last_user_at'], 0)

    def test_shared_voice_holds_qq_until_playback_receipt_and_records_once(self):
        from dataclasses import replace
        from tests.test_voice_runtime_production import (VoiceRuntimeProductionTests, _ThinkingEngine, _TTSClient,
                                                          _Adapter, _open_request, _commit_realtime_turn)
        from companion_v01.voice_runtime import VOICE_PLAYBACK_OUTPUT_MODE
        from companion_v01.turn_coordination import TurnCoordinator
        helper = VoiceRuntimeProductionTests()
        manager = helper._manager(Path(self.temp.name))
        self.addCleanup(manager.close)
        self.policy.manager = manager
        engine = _ThinkingEngine(manager)
        engine.owner_private_policy = self.policy
        engine.turn_coordinator = TurnCoordinator()
        service = helper._service(root=Path(self.temp.name), manager=manager, adapter=_Adapter(),
                                  engine=engine, tts_client=_TTSClient())
        self.addCleanup(service.close)
        request = replace(_open_request(output_mode=VOICE_PLAYBACK_OUTPUT_MODE), profile_user_id="master",
                          session_id="desktop-session", character_pack_id="cecilia",
                          conversation_ref=self.desktop["_memory_desktop_ref"])

        async def run():
            result = service.create_coordinator(request)
            self.assertTrue(result.ready, result)
            await _commit_realtime_turn(result.coordinator)
            self.assertTrue(await asyncio.to_thread(service.wait_idle, timeout=3))
            delivery = result.delivery_channel.take_outbound()
            self.assertIsNotNone(delivery)
            self.assertTrue(engine.turn_coordinator.is_busy("master", "master"))
            system = manager._get_system(profile_user_id="master", session_id="master", character_pack_id="cecilia")
            def entries():
                return system.store.get_unsummarized_messages(namespace=system.namespace)
            self.assertFalse(any(e.get("kind") == "message.assistant.voice" for e in entries()))
            result.delivery_channel.mark_sent(delivery.delivery_id)
            for kind, extra in (("enqueued", {}), ("started", {"resume_token": "test-play"}), ("completed", {"played_ms": 640})):
                ack = result.delivery_channel.acknowledge("client.playback." + kind, {"delivery_id": delivery.delivery_id, **extra})
                self.assertTrue(ack.ok, ack)
            self.assertTrue(await asyncio.to_thread(service.wait_idle, timeout=3))
            await asyncio.sleep(.02)
            self.assertFalse(engine.turn_coordinator.is_busy("master", "master"))
            duplicate = result.delivery_channel.acknowledge("client.playback.completed", {"delivery_id": delivery.delivery_id, "played_ms": 640})
            self.assertEqual(duplicate.status, "duplicate")
            assistant = [e for e in entries() if e.get("kind") == "message.assistant.voice"]
            self.assertEqual(len(assistant), 1)
            self.assertEqual(assistant[0]["payload"]["delivery_status"], "delivered")
        asyncio.run(run())

    def test_failed_shared_voice_releases_qq_without_fake_assistant(self):
        from dataclasses import replace
        from tests.test_voice_runtime_production import (VoiceRuntimeProductionTests, _ThinkingEngine, _Adapter,
                                                         _open_request, _commit_realtime_turn)
        from companion_v01.turn_coordination import TurnCoordinator
        helper = VoiceRuntimeProductionTests()
        manager = helper._manager(Path(self.temp.name))
        self.addCleanup(manager.close)
        self.policy.manager = manager
        engine = _ThinkingEngine(manager, fail=True)
        engine.owner_private_policy = self.policy
        engine.turn_coordinator = TurnCoordinator()
        service = helper._service(root=Path(self.temp.name), manager=manager, adapter=_Adapter(), engine=engine)
        self.addCleanup(service.close)
        request = replace(_open_request(), profile_user_id="master", session_id="desktop-session",
                          character_pack_id="cecilia", conversation_ref=self.desktop["_memory_desktop_ref"])

        async def run():
            result = service.create_coordinator(request)
            self.assertTrue(result.ready, result)
            await _commit_realtime_turn(result.coordinator)
            self.assertTrue(await asyncio.to_thread(service.wait_idle, timeout=3))
            await asyncio.sleep(.02)
            self.assertFalse(engine.turn_coordinator.is_busy("master", "master"))
            system = manager._get_system(profile_user_id="master", session_id="master", character_pack_id="cecilia")
            entries = system.store.get_unsummarized_messages(namespace=system.namespace)
            self.assertEqual(sum(e.get("kind") == "event.voice.failure" for e in entries), 1)
            self.assertFalse(any(e.get("kind") == "message.assistant.voice" for e in entries))
        asyncio.run(run())

    def test_background_reference_preserves_host_claim_without_promoting_unsigned_jobs(self):
        self.desktop["client_capabilities"] = ["speech_units_v1", "speech"]
        self.policy.stamp_desktop(self.desktop, owner_authenticated=True, character="cecilia")
        context = SimpleNamespace(profile_user_id="master", session_id="desktop-session", character_pack_id="cecilia",
                                  client_mode="desktop_pet", request_context=self.desktop)
        self.assertEqual(self.refs.issue(context), self.desktop["_memory_desktop_ref"])
        self.assertIn("speech_units_v1", self.refs.resolve(self.refs.issue(context))["client_capabilities"])
        context.request_context = {}
        ref = self.refs.issue(context)
        self.assertNotIn("owner_private", self.refs.resolve(ref))
        self.assertFalse(self.policy.resolve({**self.desktop, "_memory_desktop_ref": ref}).shared)

    def test_memory_tools_default_to_shared_current_without_cross_conversation_switch(self):
        policy = MemoryReadPolicy(store=SimpleNamespace(), bot_id="one", master_qq="12345",
                                  enabled=lambda: False, resolve_origin=self.refs.resolve, key=b"b"*32,
                                  private_policy=self.policy)
        context = SimpleNamespace(profile_user_id="master", session_id="desktop-session", character_pack_id="cecilia",
                                  client_mode="desktop_pet", request_context=self.desktop)
        target, error = policy._authorize(context, "current")
        self.assertIsNone(error)
        self.assertEqual((target.profile_user_id, target.session_id, target.conversation), ("master", "master", "master"))
        self.assertFalse(target.external)
        _, error = policy._authorize(context, "group:87")
        self.assertEqual(error["reason"], "cross_conversation_disabled")

    def test_real_memcore_continues_qq_history_without_copying_old_desktop(self):
        manager = MemcoreManager(backend="memcore", storage_path=Path(self.temp.name)/"mem.db",
                                visible_scope="conversation", enable_flavor=True, shadow_compare=False,
                                llm=object(), embedding_provider=_Embedding())
        self.addCleanup(manager.close)
        self.policy.manager = manager
        def append(source, text, session):
            return manager.append_standalone_message({"source_id": source, "content": text, "timestamp": 1777700000},
                    role="user", profile_user_id="master", session_id=session, character_pack_id="cecilia")
        self.assertTrue(append("qq-existing", "QQ已有记忆", "master")["ok"])
        self.assertTrue(append("desktop-old", "旧桌宠内容", "desktop-session")["ok"])
        with self.policy.scope(self.desktop):
            self.assertTrue(append("desktop-new", "桌面新经历", "desktop-session")["ok"])
            shared = manager._get_system(profile_user_id="master", session_id="desktop-session", character_pack_id="cecilia")
        qq = manager._get_system(profile_user_id="master", session_id="master", character_pack_id="cecilia")
        self.assertIs(shared, qq)
        history = json.dumps(qq.build_context_projection(provider_profile="openai_chat").payloads, ensure_ascii=False)
        self.assertIn("QQ已有记忆", history)
        self.assertIn("桌面新经历", history)
        self.assertNotIn("旧桌宠内容", history)
        old = manager._get_system(profile_user_id="master", session_id="desktop-session", character_pack_id="cecilia")
        self.assertIsNot(old, shared)

    def test_signed_memory_cursor_continues_between_owner_clients_only(self):
        policy = MemoryReadPolicy(store=SimpleNamespace(), bot_id="one", master_qq="12345",
                                  enabled=lambda: False, resolve_origin=self.refs.resolve, key=b"b"*32,
                                  private_policy=self.policy)
        desktop = SimpleNamespace(profile_user_id="master", session_id="desktop-session", character_pack_id="cecilia",
                                  client_mode="desktop_pet", request_context=self.desktop)
        ref = self.refs.issue_qq(profile_user_id="master", session_id="master", character_pack_id="cecilia",
                                 user_id=12345, group_id=0)
        qq = SimpleNamespace(profile_user_id="master", session_id="master", character_pack_id="cecilia",
                             client_mode="qq_text", request_context={"_memory_qq_ref": ref})
        for first, second in ((desktop, qq), (qq, desktop)):
            target, _, error = policy.prepare(context=first, call={}, tool="browse_memory")
            self.assertIsNone(error)
            cursor = policy.project(context=first, target=target, tool="browse_memory",
                                     result={"ok": True, "next_cursor": "native-page-2"})["next_cursor"]
            continued, args, error = policy.prepare(context=second, call={"cursor": cursor}, tool="browse_memory")
            self.assertIsNone(error)
            self.assertEqual(continued.session_id, "master")
            self.assertEqual(args["cursor"], "native-page-2")
            for changes in ({"character_pack_id": "reimu"}, {"profile_user_id": "other"}, {"request_context": {}}):
                forged = SimpleNamespace(**{**vars(desktop), **changes})
                _, _, error = policy.prepare(context=forged, call={"cursor": cursor}, tool="browse_memory")
                self.assertEqual(error["status"], "cursor_invalid")

    def test_shared_turn_keeps_raw_assistant_native_tools_and_retry_identity(self):
        manager = MemcoreManager(backend="memcore", storage_path=Path(self.temp.name)/"roundtrip.db",
                                visible_scope="conversation", enable_flavor=True, shadow_compare=False,
                                llm=object(), embedding_provider=_Embedding())
        self.addCleanup(manager.close)
        self.policy.manager = manager
        desktop_ids = dict(profile_user_id="master", session_id="desktop-session", character_pack_id="cecilia")
        qq_ids = {**desktop_ids, "session_id": "master"}
        raw = '{ "speech_units": [{"text":"已经记住了。","say":"覚えました。","style":"calm"}], "emotion":"happy" }'
        with self.policy.scope(self.desktop):
            opened = manager.begin_input_turn({"source_id": "desktop-turn", "content": "请记住蓝色书签",
                                               "timestamp": 1777700000, "memory_metadata": {"channel": "desktop"}}, **desktop_ids)
            self.assertTrue(opened["ok"], opened)
            batch = manager.record_tool_batch(turn_id=opened["turn_id"], exchanges=[{
                "tool_name": "open_memory", "tool_call_id": "call-private-bookmark",
                "tool_input": {"memory_id": "bookmark"}, "result": "主人选了蓝色书签。",
                "timestamp": 1777700001, "source_id_prefix": "private-bookmark", "result_status": "success",
            }], **desktop_ids)
            self.assertTrue(batch["ok"], batch)
            # Freeze exactly what the next model continuation sees.
            projection = manager.build_context_projection(provider_profile="openai_chat", **desktop_ids)
            self.assertIn("call-private-bookmark", json.dumps(projection, ensure_ascii=False, default=str))
            final_args = dict(turn_id=opened["turn_id"], assistant_record={"source_id": "desktop-final",
                              "content": "已经记住了。", "timestamp": 1777700002}, memory_metadata={},
                              provider_output_raw=raw, provider_profile="openai_chat",
                              provider_projection={"role": "assistant", "content": raw})
            self.assertTrue(manager.complete_input_turn(**final_args, **desktop_ids)["ok"])
        qq_projection = manager.build_context_projection(provider_profile="openai_chat", **qq_ids)
        text = json.dumps(qq_projection, ensure_ascii=False, default=str)
        self.assertIn("蓝色书签", text)
        self.assertIn("call-private-bookmark", text)
        self.assertTrue(any(m["payload"].get("content") == raw for m in qq_projection["messages"]))
        self.assertEqual(manager.complete_input_turn(**final_args, **qq_ids)["status"], "already_completed")
        after_retry = manager.build_context_projection(provider_profile="openai_chat", **qq_ids)
        self.assertEqual(qq_projection["messages"], after_retry["messages"])
        manager.append_standalone_message({"source_id": "qq-next", "content": "书签夹在第八页", "timestamp": 1777700003},
                                           role="user", **qq_ids)
        with self.policy.scope(self.desktop):
            reread = manager.build_context_projection(provider_profile="openai_chat", **desktop_ids)
        self.assertIn("书签夹在第八页", json.dumps(reread, ensure_ascii=False, default=str))

    def test_client_catalogs_do_not_overwrite_each_other(self):
        def source(role, client):
            return f"prompt-context:capability-catalog:2:{role}:digest:scope:anchor" + (f":{client}" if client else "")
        groups = [[source("base", "")], [source("base", "desktop_pet")], [source("update", "qq_text")],
                  [source("update", "desktop_pet")], ["message"]]
        original = {"history_turns": [{"role": "user", "content": content} for content in ("QQ基线", "桌宠基线", "QQ更新", "桌宠更新", "正文")],
                    "history_message_source_ids": groups}
        desktop, base = _shape_capability_catalog_projection(original, compaction_generation=2, client_scope="desktop_pet")
        self.assertEqual(base, "桌宠基线")
        self.assertEqual([m["content"] for m in desktop["history_turns"]], ["桌宠更新", "正文"])
        qq, base = _shape_capability_catalog_projection(original, compaction_generation=2, client_scope="qq_text")
        self.assertEqual(base, "QQ基线")
        self.assertEqual([m["content"] for m in qq["history_turns"]], ["QQ更新", "正文"])
        self.assertEqual(len(original["history_turns"]), 5)

    def test_presence_only_counts_proven_private_messages_for_current_character(self):
        from companion_v01.store import MemoryStore
        store = MemoryStore(Path(self.temp.name)/"host")
        for session, character, metadata, timestamp in (
            ("master", "cecilia", {"client_mode": "qq_text"}, 100),
            ("desktop-session", "cecilia", {"owner_private_shared": True}, 120),
            ("old-desktop", "cecilia", {}, 400),
            ("desktop-session", "reimu", {"owner_private_shared": True}, 500),
        ):
            store.add_message(profile_user_id="master", session_id=session, character_pack_id=character,
                              role="user", content="hi", timestamp=timestamp, memory_metadata=metadata)
        store.add_message(profile_user_id="master", session_id="master", character_pack_id="cecilia",
                          role="assistant", content="hi back", timestamp=121, memory_metadata={"owner_private_shared": True})
        self.assertEqual(store.owner_private_activity(character_pack_id="cecilia"), {"last_user_at": 120, "last_reply_at": 121})

    def test_other_client_input_does_not_interrupt_optional_turn(self):
        from companion_v01.turn_coordination import TurnCoordinator
        async def run():
            coordinator = TurnCoordinator()
            async with coordinator.hold("master", "master", actor_id="same-owner", channel="qq", turn_kind="plugin_event") as token:
                result = coordinator.offer_steer(profile_user_id="master", session_id="master", actor_id="same-owner",
                                                 content="from desktop", channel="desktop_pet")
                self.assertEqual(result["reason"], "reply_channel_mismatch")
                self.assertFalse(coordinator.drain(token)["stop_requested"])
        asyncio.run(run())

    def test_new_owner_input_cancels_unsent_proactive_only(self):
        from companion_v01.turn_coordination import cancellation_requested
        from companion_v01.model_request_control import ModelRequestCancelled
        qq = {"user_id": "master", "real_user_id": "master", "character_pack_id": "cecilia", "client_mode": "qq_text",
              "_memory_qq_ref": self.refs.issue_qq(profile_user_id="master", session_id="master",
                                                    character_pack_id="cecilia", user_id=12345, group_id=0)}
        proactive = {**self.desktop, "turn_kind": "desktop_pet_proactive"}
        with self.policy.scope(proactive) as scope:
            self.assertFalse(cancellation_requested())
            self.policy.note_input({**qq, "_memory_qq_ref": "forged"}, source_id="forged")
            self.policy.note_input(proactive, source_id="timer")
            self.assertFalse(cancellation_requested())
            self.policy.note_input(qq, source_id="qq-one")
            self.assertTrue(cancellation_requested())
            with self.assertRaises(ModelRequestCancelled):
                scope.proactive_guard.before_event({"type": "speech_chunk", "text": "旧主动问候"})
        with self.policy.scope(proactive) as scope:
            self.policy.note_input(qq, source_id="qq-one")  # Retry is not new activity.
            self.assertFalse(cancellation_requested())
            scope.proactive_guard.before_event({"type": "speech_chunk", "text": "已经开始的回复"})
            self.policy.note_input(qq, source_id="qq-two")
            self.assertFalse(cancellation_requested())
        with self.policy.scope(self.desktop):
            self.policy.note_input(qq, source_id="qq-three")
            self.assertFalse(cancellation_requested())  # Normal tasks retain their target.

    def test_http_binding_requires_auth_and_keeps_original_reply_request(self):
        import io
        from contextlib import redirect_stdout
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from companion_v01.deployment_security import AdminWriteAuth
        from companion_v01.desktop_conversation_auth import DesktopConversationAuth, build_desktop_conversation_auth_router
        from companion_v01.routes.think import build_think_router
        from companion_v01.turn_coordination import TurnCoordinator
        from tests.test_backend_route_modules import FakeGuard, FakeRuntimeMetrics
        captured = []
        coordinator = TurnCoordinator()
        policy = self.policy
        class Engine:
            owner_private_policy = policy
            @staticmethod
            def _resolve_payload_character_pack_id(payload):
                return payload.get("character_pack_id", "cecilia")
            @staticmethod
            def process_turn(payload):
                captured.append(payload)
                assert coordinator.is_busy("master", "master")
                assert not coordinator.is_busy("master", "desktop-session")
                return {"status": "ok", "speech": "hello", "emotion": "normal", "_debug": {}}
        app = FastAPI()
        auth = DesktopConversationAuth(AdminWriteAuth(token="test-token", require_token=True,
                                      allow_loopback_without_token=False), route_prefix="")
        app.include_router(build_desktop_conversation_auth_router(auth))
        app.include_router(build_think_router(engine=Engine(), public_guard=FakeGuard(), runtime_metrics=FakeRuntimeMetrics(),
                            log_event=lambda *a, **kw: None, turn_coordinator=coordinator,
                            admin_auth=auth))
        client = TestClient(app)
        self.assertEqual(client.post("/think_once", json={**self.desktop, "message": "hi"}).status_code, 401)
        self.assertFalse(captured)
        with redirect_stdout(io.StringIO()):
            result = client.post("/think_once", json={**self.desktop, "message": "hi"}, headers={"Authorization": "Bearer test-token"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(captured[0]["user_id"], "desktop-session")
        self.assertTrue(policy.resolve(captured[0]).shared)
        token = client.post("/control-center/desktop-chat-session", headers={"Authorization": "Bearer test-token"}).json()["token"]
        with redirect_stdout(io.StringIO()):
            result = client.post("/think_once", json={**self.desktop, "message": "desktop token"},
                                 headers={"X-Akane-Desktop-Chat-Token": token})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(policy.resolve(captured[1]).shared)
        self.assertEqual(captured[1]["message"], "desktop token")


if __name__ == "__main__":
    unittest.main()
