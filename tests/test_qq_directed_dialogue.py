from __future__ import annotations

from dataclasses import replace
import asyncio
import httpx
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.care_runtime import CareModulePort
from companion_v01.dialogue_targets import DialogueRegistry, DialogueTarget, trusted_target
from companion_v01.durable_session_queue import DurableSessionWorkQueue
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.channels.qq.gateway import NapCatQQGateway
from companion_v01.routes.qq import build_qq_router
from companion_v01.session_inbox import SessionInboxStore
from companion_v01.turn_coordination import TurnCoordinator
from tests.test_memcore_integration import _FakeEmbeddingProvider, _FakeLLM
from tests.test_qq_commands import _PluginBroker
from tests import test_qq_multi_bot_dispatch as multi_fixtures
from tests.test_service_character_shop import make_resources, qq_protocol


class _MemoryEngine(multi_fixtures._Engine):
    def __init__(self, resources, manager):
        super().__init__("好的。已经记下了。")
        self.desktop_pet_character_resources = resources
        self.manager = manager
        self.surfaces = []

    def get_care_module(self):
        return CareModulePort.disabled()

    def process_turn_stream(self, payload):
        self.turns.append(dict(payload))
        scope = dict(
            profile_user_id=payload["real_user_id"],
            session_id=payload["user_id"],
            character_pack_id=AkaneMemoryEngine._resolve_payload_character_pack_id(payload),
        )
        source = "incoming:" + payload["source_message_id"]
        opened = self.manager.begin_input_turn(
            {"source_id": source, "content": payload["memory_message"], "timestamp": 1790790000},
            actor_stable_id=payload.get("actor_stable_id", ""),
            **scope,
        )
        if not opened["ok"]:
            raise AssertionError(opened)
        surface = self.manager.build_context_surface(provider_profile="openai", current_source_id=source, **scope)
        self.surfaces.append(surface)
        raw = json.dumps({"speech": self.reply})
        completed = self.manager.complete_input_turn(
            turn_id=opened["turn_id"],
            assistant_record={"source_id": source + ":reply", "content": self.reply, "timestamp": 1790790001},
            memory_metadata=None,
            annotation_status="missing",
            provider_profile="openai",
            provider_output_raw=raw,
            provider_projection={"role": "assistant", "content": raw},
            **scope,
        )
        if not completed["ok"]:
            raise AssertionError(completed)
        yield {
            "type": "final_ui",
            "payload": {
                "reply_medium": "text",
                "speech": self.reply,
                "speech_segments": ["好的。", "已经记下了。"],
                "tool_events": [],
            },
        }


class QQDirectedDialogueTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("companion_v01.channels.qq.gateway.config.MASTER_QQ", "10002"))
        self.enterContext(patch("companion_v01.channels.qq.gateway.config.QQ_BOT_QQ", "10001"))
        self.enterContext(patch.object(NapCatQQGateway, "_resolve_bot_nickname", return_value="塞西莉亚"))
        self.enterContext(patch.object(NapCatQQGateway, "_resolve_bot_group_label", return_value="塞西莉亚"))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        resources = make_resources(self.root / "characters")
        manager = MemcoreManager(
            backend="memcore",
            storage_path=self.root / "memory.db",
            visible_scope="conversation",
            enable_flavor=True,
            shadow_compare=False,
            llm=_FakeLLM(),
            embedding_provider=_FakeEmbeddingProvider(),
        )
        self.addCleanup(manager.close)
        self.engine = _MemoryEngine(resources, manager)
        self.gateway = NapCatQQGateway(state_path=self.root / "state.json")
        self.gateway.character_pack_overrides.update(master="cecilia", qq_group_shared_42="cecilia")
        self.broker = _PluginBroker()
        self.logs = []
        self.coordinator = TurnCoordinator()
        self.app = self.application()
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.sent = []
        self.sequence = 0

        def transport(_method, _url, **kwargs):
            if "message" in kwargs["json"]:
                self.sent.append(kwargs["json"])
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"status": "ok", "data": {"message_id": 70000 + len(self.sent)}},
            )

        self.enterContext(patch("companion_v01.channels.qq.onebot.transport.requests.Session.request", side_effect=transport))

    def application(self, queue=None):
        app = FastAPI()
        app.include_router(
            build_qq_router(
                engine=self.engine,
                qq_gateway=self.gateway,
                config_module=SimpleNamespace(
                    QQ_BRIDGE_ENABLED=True, QQ_STREAM_REPLIES_ENABLED=False, DATA_DIR=str(self.root), MASTER_QQ="10002"
                ),
                runtime_metrics=SimpleNamespace(observe_request=lambda *args, **kwargs: None),
                logger=SimpleNamespace(exception=lambda *args, **kwargs: None),
                log_event=lambda name, **fields: self.logs.append((name, fields)),
                plugin_command_broker_provider=lambda: self.broker,
                session_work_queue=queue,
                turn_coordinator=self.coordinator,
            )
        )
        return app

    def event(self, message, *, group=False, target=False):
        self.sequence += 1
        return {
            "post_type": "message",
            "message_type": "group" if group else "private",
            "self_id": 10001,
            "user_id": 10002,
            "group_id": 42 if group else 0,
            "message_id": str(60000 + self.sequence),
            "time": int(time.time()) + self.sequence,
            "sender": {"role": "member", "nickname": "主人", "card": "群内昵称"},
            "message": ([{"type": "at", "data": {"qq": "10001"}}] if target else [])
            + [{"type": "text", "data": {"text": message}}],
        }

    def post(self, message, **kwargs):
        response = self.client.post("/api/qq/napcat/event", json=self.event(message, **kwargs))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_default_directed_default_use_real_separate_memory_without_switching(self):
        for text in ("初次见面", "/reimu 灵梦你好", "我们继续聊"):
            result = self.post(text)
            self.assertEqual(result["status"], "ok", result)
        self.assertEqual([t["character_pack_id"] for t in self.engine.turns], ["cecilia", "reimu", "cecilia"])
        self.assertEqual(self.gateway.resolve_character_pack_id("master"), "cecilia")
        self.assertEqual(self.engine.turns[1]["memory_message"], "灵梦你好")
        self.assertNotIn("初次见面", str(self.engine.surfaces[1]))
        self.assertIn("初次见面", str(self.engine.surfaces[2]))
        self.assertNotIn("灵梦你好", str(self.engine.surfaces[2]))
        self.assertEqual(len(self.sent), 6)  # Two existing speech segments per reply.
        self.assertEqual(self.engine.records, [])

    def test_addressing_default_role_reuses_its_original_history(self):
        self.post("默认入口留下的记录")
        self.post("/cecilia 单次再来")
        self.assertIn("默认入口留下的记录", str(self.engine.surfaces[-1]))
        self.assertEqual(self.engine.turns[-1]["character_pack_id"], "cecilia")

    def test_service_target_tracks_persistent_default_and_reply_mode_stays_local(self):
        self.gateway.reply_mode_overrides["master"] = "voice"
        self.gateway.chat_model_overrides["master"] = "fixture-model"
        self.post("/售货员 看看蛋糕")
        first = self.engine.turns[-1]
        self.assertEqual(first["_host_dialogue_target"], DialogueTarget("service", "shopkeeper", "cecilia"))
        self.assertEqual(first["qq_reply_mode"], "text")
        self.assertEqual(first["chat_model_override"], "fixture-model")
        self.assertEqual(self.gateway.resolve_reply_mode("master"), "voice")
        self.post("/reimu 只是单次问候")
        self.post("/售货员 还在吗")
        self.assertIn("看看蛋糕", str(self.engine.surfaces[-1]))
        self.assertNotIn("只是单次问候", str(self.engine.surfaces[-1]))
        switched = self.post("/角色 reimu")
        self.assertTrue(switched.get("command_ok"), switched)
        self.post("/售货员 给灵梦看看商店")
        self.assertEqual(self.engine.turns[-1]["character_pack_id"], "service:shopkeeper:reimu")
        self.assertNotIn("看看蛋糕", str(self.engine.surfaces[-1]))
        self.assertFalse(any(s["type"] == "record" for message in self.sent for s in message["message"]))

    def test_group_directed_entry_requires_real_current_mention_and_preserves_actor(self):
        result = self.post("/reimu 你好", group=True)
        self.assertNotEqual(result["status"], "ok", result)
        self.assertEqual(self.engine.turns, [])
        self.assertEqual(self.engine.records, [])
        result = self.post("@塞西莉亚 /reimu 你好", group=True)
        self.assertNotEqual(result["status"], "ok", result)
        self.assertEqual(self.engine.turns, [])
        self.assertEqual(len(self.engine.records), 1)  # Ordinary unaddressed group history.
        result = self.post("/reimu 同学你好", group=True, target=True)
        self.assertEqual(result["status"], "ok", result)
        payload = self.engine.turns[0]
        self.assertEqual(payload["character_pack_id"], "reimu")
        self.assertEqual(payload["actor_stable_id"], "qq:10002")
        self.assertEqual(payload["memory_message"], "同学你好")
        self.assertTrue(payload["message_addressing"]["explicit_assistant_mention"])

    def test_unknown_empty_standard_and_plugin_entries_stay_local(self):
        self.post("/not_installed 一句话")
        empty = self.post("/reimu")
        self.assertEqual(empty["command_status"], "dialogue_body_required")
        self.post("/ping")
        self.post("/角色 列表")
        self.assertEqual(self.engine.turns, [])
        self.assertEqual(len(self.broker.calls), 1)

    def test_directed_body_is_not_recursively_executed_as_host_commands(self):
        self.post("/reimu /角色 reimu")
        self.post("/reimu /停止")
        self.assertEqual(len(self.engine.turns), 2)
        self.assertEqual(self.engine.turns[0]["memory_message"], "/角色 reimu")
        self.assertEqual(self.engine.turns[1]["memory_message"], "/停止")
        self.assertEqual(self.gateway.resolve_character_pack_id("master"), "cecilia")

    def test_durable_context_freezes_target_and_rejects_removed_service_or_pack(self):
        context = self.gateway.build_message_context(self.event("/售货员 先保存"))
        selected = self.engine.dialogue_registry.parse(context.clean_message, default_pack=context.character_pack_id)
        context = replace(
            context,
            clean_message=selected.body,
            dialogue_target=selected.target,
            character_pack_id=selected.target.memory_domain,
            reply_mode="text",
        )
        durable = json.loads(json.dumps(context.to_turn_payload()))
        self.assertNotIn("_host_dialogue_target", durable)
        self.gateway.character_pack_overrides["master"] = "reimu"
        restored = self.gateway.context_from_delivery_context(durable["qq_delivery_context"])
        self.assertEqual(restored.dialogue_target.service_target, "cecilia")
        self.assertEqual(restored.to_turn_payload()["memory_message"], "先保存")
        del self.engine.dialogue_registry.services["shopkeeper"]
        with self.assertRaisesRegex(ValueError, "dialogue_target_unavailable"):
            self.gateway.context_from_delivery_context(durable["qq_delivery_context"])

    def test_service_persona_has_no_character_resources_or_care_wallet(self):
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.desktop_pet_character_resources = self.engine.desktop_pet_character_resources
        target = DialogueTarget("service", "shopkeeper", "cecilia")
        payload = {"_host_dialogue_target": target, "character_pack_id": "reimu", "care_state": {"coins": 1}}
        self.assertEqual(engine._resolve_payload_character_pack_id(payload), target.memory_domain)
        self.assertIsNone(trusted_target({"_host_dialogue_target": target.to_dict()}))
        persona = engine._build_desktop_pet_character_pack_prompt_context(character_pack_id=target.memory_domain)
        self.assertIn("你是售货员", persona["system_context"])
        self.assertIn("本次服务对象：塞西莉亚", persona["system_context"])
        self.assertEqual(persona["resource_context"], "")
        self.assertNotIn("service:shopkeeper", str(persona))
        self.assertIsNone(engine._resolve_turn_resource_manifest(payload, qq_protocol()))
        self.assertEqual(
            engine._resolve_turn_speaker_identity(qq_protocol(), target.memory_domain)["assistant_name"], "售货员"
        )
        sanitized = engine._prepare_care_context_for_turn(
            payload, qq_protocol(), profile_user_id="master", character_pack_id=target.memory_domain, now_ts=1790790000
        )
        self.assertNotIn("care_state", sanitized)

    def test_registered_definition_is_separate_from_character_packages_and_conflicts_are_explicit(self):
        registry = self.engine.dialogue_registry
        self.assertNotIn("shopkeeper", registry.packs().values())
        directory = self.root / "definitions"
        directory.mkdir()
        (directory / "conflict.json").write_text(
            json.dumps(
                {
                    "id": "other",
                    "name": "测试",
                    "command": "reimu",
                    "persona": "测试",
                    "tools": ["read_memory_timeline"],
                }
            ),
            encoding="utf-8",
        )
        conflict = DialogueRegistry(self.engine.desktop_pet_character_resources, directory)
        self.assertEqual(conflict.parse("/reimu hello", default_pack="cecilia").error, "dialogue_command_conflict")
        self.assertIsNone(registry.parse("开头 /reimu hello", default_pack="cecilia"))
        self.assertIsNone(registry.parse("/ping", default_pack="cecilia", plugin_broker=self.broker))

    def test_duplicate_webhook_has_one_model_turn_and_one_segmented_delivery(self):
        event = self.event("/售货员 看一下现有商品")
        first = self.client.post("/api/qq/napcat/event", json=event).json()
        second = self.client.post("/api/qq/napcat/event", json=event).json()
        self.assertEqual(first["status"], "ok", first)
        self.assertEqual(second["status"], "ignored", second)
        self.assertEqual(len(self.engine.turns), 1)
        self.assertEqual(len(self.sent), 2)

    def test_two_bots_only_the_explicitly_mentioned_one_runs_directed_turn(self):
        fixture = multi_fixtures.QQMultiBotDispatchTests()
        app, a, b, gateway_a, gateway_b = fixture._app(state_root=self.root)
        for engine, gateway in ((a, gateway_a), (b, gateway_b)):
            engine.desktop_pet_character_resources = self.engine.desktop_pet_character_resources
            engine.dialogue_registry.character_resources = engine.desktop_pet_character_resources
        event = {
            "post_type": "message",
            "message_type": "group",
            "user_id": 10002,
            "group_id": 42,
            "message_id": "two-bots",
            "time": int(time.time()),
            "message": [{"type": "at", "data": {"qq": "10000001"}}, {"type": "text", "data": {"text": "/reimu 你好"}}],
        }
        with TestClient(app) as client:
            chosen = client.post(
                "/api/bots/bot-a/qq/napcat/event",
                headers={"Authorization": "Bearer secret-a"},
                json={**event, "self_id": "10000001"},
            ).json()
            other = client.post(
                "/api/bots/bot-b/qq/napcat/event",
                headers={"Authorization": "Bearer secret-b"},
                json={**event, "self_id": "10000002"},
            ).json()
        self.assertEqual(chosen["status"], "ok", chosen)
        self.assertEqual(other["status"], "ignored", other)
        self.assertEqual(len(a.turns), 1)
        self.assertEqual(a.turns[0]["character_pack_id"], "reimu")
        self.assertEqual(b.turns, [])
        self.assertEqual(b.records, [])

    def test_legal_pack_ids_with_digits_and_dots_have_callable_entries(self):
        pack = self.root / "characters" / "2nd.role"
        pack.mkdir()
        (pack / "character.json").write_text('{"identity":{"name":"测试"}}', encoding="utf-8")
        result = self.post("/2nd.role 你好")
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(self.engine.turns[-1]["character_pack_id"], "2nd.role")

    def test_durable_queue_keeps_other_speaker_out_of_active_turn_and_freezes_service_target(self):
        async def run():
            queue = DurableSessionWorkQueue(SessionInboxStore(self.root / "inbox.db"))
            app = self.application(queue)
            loop = asyncio.get_running_loop()
            processed = asyncio.Event()
            original = self.engine.process_turn_stream

            def processing(payload):
                yield from original(payload)
                loop.call_soon_threadsafe(processed.set)

            self.engine.process_turn_stream = processing
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    async with self.coordinator.hold(
                        "master", "master", actor_id="qq:10002", channel="qq", memory_domain="reimu"
                    ) as token:
                        response = await client.post("/api/qq/napcat/event", json=self.event("/售货员 先看看蛋糕"))
                        result = response.json()
                        self.assertEqual(result["send_result"]["status"], "queued", result)
                        self.assertEqual(self.coordinator.drain(token)["steers"], [])
                        self.assertEqual(self.engine.turns, [])
                        self.gateway.character_pack_overrides["master"] = "reimu"
                    await asyncio.wait_for(processed.wait(), timeout=5)
                closed = await queue.close(timeout=5)
                self.assertEqual(closed["status"], "stopped", closed)
                self.assertEqual(len(self.engine.turns), 1)
                self.assertEqual(self.engine.turns[0]["character_pack_id"], "service:shopkeeper:cecilia")
                self.assertEqual(self.engine.turns[0]["_host_dialogue_target"].service_target, "cecilia")
                self.assertFalse(any(name == "qq_session_work_failed" for name, _ in self.logs), self.logs)
            finally:
                await queue.close(timeout=5)

        asyncio.run(run())

    def test_same_service_scope_still_accepts_steer_but_other_roles_and_targets_do_not(self):
        async def run():
            domain = "service:shopkeeper:cecilia"
            async with self.coordinator.hold("master", "master", actor_id="qq:10002", memory_domain=domain) as token:
                for other in ("cecilia", "reimu", "service:shopkeeper:reimu"):
                    result = self.coordinator.offer_steer(
                        profile_user_id="master",
                        session_id="master",
                        actor_id="qq:10002",
                        content="继续",
                        memory_domain=other,
                    )
                    self.assertEqual(result["status"], "busy_other_scope")
                self.assertEqual(self.coordinator.drain(token)["steers"], [])
                result = self.coordinator.offer_steer(
                    profile_user_id="master",
                    session_id="master",
                    actor_id="qq:10002",
                    content="再加一个草莓",
                    memory_domain=domain,
                )
                self.assertTrue(result["ok"], result)
                self.assertEqual(len(self.coordinator.drain(token)["steers"]), 1)

        asyncio.run(run())

    def test_help_shows_registered_directed_entries_without_inference(self):
        result = self.post("/help 角色 文字")
        self.assertEqual(result["help_format"], "text", result)
        text = str(self.sent)
        self.assertIn("/<角色包ID> 正文", text)
        self.assertIn("/售货员 正文", text)
        self.assertEqual(self.engine.turns, [])


if __name__ == "__main__":
    unittest.main()
