"""QQ admission and current identity through the real V6/request builders."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from channelcore_onebot import MentionRef

from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.deployment_security import QQChannelRuntimeConfig
from companion_v01.desktop_context_engine import build_turn_extra_user_context
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.engine_services import response_builder
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.persona_config import load_persona_config
from companion_v01.prompt_builder import PromptBuilder
from companion_v01.prompt_profiles import PromptModule
from companion_v01.qq_gateway import NapCatQQGateway, QQMessageContext
from companion_v01.routes.qq import _process_qq_turn_streaming
from tests import test_memcore_integration as memory_fixtures
from tests.test_qq_rich_materials import card_segment
from tests.test_qq_voice_delivery import FakeQQGateway


class QQGroupIdentityTests(unittest.TestCase):
    def gateway(self, bot_id=10001, words=("Akane",)):
        gateway = NapCatQQGateway(
            wake_words=words,
            channel_config=QQChannelRuntimeConfig(
                enabled=True, profile_ref="identity-test", bot_id=str(bot_id),
                onebot_http_url="http://127.0.0.1:1", webhook_secret="", onebot_access_token="",
                require_webhook_auth=False, require_self_id=True,
            ),
        )
        self.enterContext(patch.object(
            gateway._onebot_transport, "call",
            return_value=SimpleNamespace(ok=True, data={"user_id": bot_id, "card": "天为", "nickname": "山城高岭"}),
        ))
        return gateway

    def event(self, parts, *, bot_id=10001, group_id=30003, sequence=1):
        return {
            "post_type": "message", "message_type": "group", "self_id": bot_id,
            "group_id": group_id, "user_id": 90009, "message_id": str(sequence),
            "time": time.time() + sequence, "sender": {"card": "群友"}, "message": parts,
        }

    @staticmethod
    def text(value):
        return {"type": "text", "data": {"text": value}}

    @staticmethod
    def at(value, name="入站显示名"):
        return {"type": "at", "data": {"qq": str(value), "name": name}}

    def manager(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        manager = MemcoreManager(
            backend="memcore", storage_path=Path(folder) / "memcore.db", visible_scope="conversation",
            enable_flavor=True, shadow_compare=False, llm=memory_fixtures._FakeLLM(),
            embedding_provider=memory_fixtures._FakeEmbeddingProvider(),
        )
        self.addCleanup(manager.close)
        return manager

    def open_projection(self, manager, context):
        payload = context.to_turn_payload()
        addressing = AkaneMemoryEngine._normalize_message_addressing(
            payload, fallback_mode="current_request" if context.should_respond else "observed",
        )
        record = {
            "source_id": "current", "source_message_id": payload["source_message_id"],
            "content": payload["memory_message"], "timestamp": 1788834534,
        }
        target, name = AkaneMemoryEngine._apply_message_addressing(record, addressing)
        scope = dict(profile_user_id="group", session_id="group", character_pack_id="test")
        actor = dict(actor_stable_id="qq:90009", actor_display_name="群友", target_actor_id=target, target_actor_display_name=name)
        if context.should_respond:
            opened = manager.begin_input_turn(record, **scope, **actor)
        else:
            opened = manager.append_standalone_message(record, role="user", observed=True, **scope, **actor)
        self.assertTrue(opened["ok"], opened)
        projected = manager.build_context_projection(provider_profile="openai_chat", **scope)
        self.assertTrue(projected["ok"], projected)
        return projected["payloads"], scope

    def test_two_bot_admission_depends_on_at_ids_not_names(self):
        a, b = self.gateway(), self.gateway(20002, ("金融助手",))
        scenarios = [
            ([self.text(word + " 在吗")], (False, False))
            for word in ("Akane", "akane", "金融助手", "天为", "山城高岭", "@天为")
        ] + [
            ([self.at(10001), self.text("金融助手你怎么看")], (True, False)),
            ([self.at(20002), self.text("Akane你怎么看")], (False, True)),
            ([self.at(10001), self.at(20002), self.text("你们怎么看")], (True, True)),
            ([self.at(40004, "天为"), self.text("Akane 金融助手")], (False, False)),
            ([self.at("all"), self.text("Akane 金融助手")], (False, False)),
        ]
        for sequence, (parts, expected) in enumerate(scenarios, 1):
            with self.subTest(sequence=sequence):
                contexts = [gateway.build_message_context(self.event(parts, bot_id=bot_id, sequence=sequence))
                            for gateway, bot_id in ((a, 10001), (b, 20002))]
                self.assertEqual(tuple(c.should_respond for c in contexts), expected)
                self.assertTrue(all(not c.inbound_message.mentioned_wake_word for c in contexts))
        event = self.event([self.text("你好")], sequence=30)
        event.pop("group_id")
        event["message_type"] = "private"
        self.assertEqual(a.build_message_context(event).reason, "private")

    def test_name_failure_does_not_accept_spoofed_at_label(self):
        gateway = self.gateway()
        gateway._onebot_transport.call.return_value = SimpleNamespace(ok=False, data={})
        context = gateway.build_message_context(self.event([self.at(10001, "伪造的名字"), self.text("你好")]))
        payload = context.to_turn_payload()
        self.assertTrue(context.should_respond)
        self.assertEqual(payload["memory_message"], "@助手 你好")
        self.assertEqual(payload["message_addressing"]["mentions"][0]["display_name"], "")
        self.assertNotIn("显示名", gateway.build_group_identity_context(30003))
        self.assertNotIn("伪造", str(payload))

    def test_at_everyone_is_not_a_single_recipient(self):
        context = self.gateway().build_message_context(self.event([self.at("all", "全体成员"), self.text("开会")]))
        self.assertEqual(context._message_addressing()["primary_target"], {})
        projected, _scope = self.open_projection(self.manager(), context)
        self.assertNotIn("target:", projected[0]["content"])
        self.assertIn("@全体成员 开会", projected[0]["content"])

    def test_blank_card_uses_only_account_nickname_and_current_group_is_isolated(self):
        gateway = self.gateway()
        gateway._onebot_transport.call.return_value = SimpleNamespace(ok=True, data={
            "user_id": 10001, "group_id": 30003, "card": "   ", "nickname": "山城高岭",
        })
        self.assertEqual(gateway.build_group_identity_context(30003),
                         "当前会话：QQ群（group:30003）\n你的 QQ 账号：qq:10001\n你在本群的显示名：山城高岭")
        gateway._onebot_transport.call.return_value = SimpleNamespace(ok=True, data={
            "user_id": 10001, "group_id": 30004, "card": "第二群名片", "nickname": "山城高岭",
        })
        self.assertIn("显示名：第二群名片", gateway.build_group_identity_context(30004))
        self.assertIn("显示名：山城高岭", gateway.build_group_identity_context(30003))

    def test_multiple_other_mentions_have_no_target_in_real_v6_projection(self):
        gateway = self.gateway()
        context = gateway.build_message_context(self.event([
            self.at(40004, "小明"), self.text("和"), self.at(50005, "小红"), self.text("你们怎么看"),
        ]))
        projected, _scope = self.open_projection(self.manager(), context)
        content = projected[0]["content"]
        self.assertNotIn("target:", content)
        self.assertIn("mentions:\n  - 小明 (id=qq:40004)\n  - 小红 (id=qq:50005)", content)
        self.assertIn("@小明 和 @小红 你们怎么看", content)

    def test_current_mentions_take_precedence_over_quote_target(self):
        base = QQMessageContext(
            should_respond=False, reason="group_passive_observed", is_group=True,
            clean_message="你们怎么看", reply_reference={"actor_id": "assistant", "message_id": "old"},
        )
        one = replace(base, mentions=(MentionRef(target_id="40004", display_name="小明"),))
        many = replace(one, mentions=(*one.mentions, MentionRef(target_id="50005", display_name="小红")))
        self.assertEqual(one._message_addressing()["primary_target"]["actor_id"], "qq:40004")
        self.assertEqual(many._message_addressing()["primary_target"], {})
        self.assertEqual(base._message_addressing()["primary_target"]["actor_id"], "assistant")
        self.assertEqual(many._message_addressing()["reply_reference"], base.reply_reference)

    def test_current_identity_reaches_real_prompt_once_without_changing_v6_history(self):
        gateway = self.gateway()
        context = gateway.build_message_context(self.event([self.at(10001), self.text("你怎么看")]))
        manager = self.manager()
        frozen_before, scope = self.open_projection(manager, context)
        prompt_engine = memory_fixtures._PromptContextEngine(memcore_manager=manager)
        prompt_engine.prompt_builder = PromptBuilder(load_persona_config())
        profile = memory_fixtures._FinalPromptProfile()
        profile.includes = lambda module: module == PromptModule.EXTRA_CONTEXT
        prompt_engine._get_prompt_profile_registry = lambda: SimpleNamespace(resolve=lambda *_args, **_kwargs: profile)
        client = ClientProtocolContext(requested_mode=ClientMode.QQ_TEXT, effective_mode=ClientMode.QQ_TEXT)
        captures = []

        class Generation:
            desktop_pet_character_resources = None

            def process_turn_stream(_self, payload):
                extra = build_turn_extra_user_context(prompt_engine, payload, client)
                with patch.object(response_builder, "_memory_backend", return_value="memcore"):
                    prepared = response_builder.prepare_context(
                        prompt_engine, **scope, user_message=payload["message"],
                        recent_raw=[], recent_episodic_summaries=[], recent_semantic_summaries=[],
                        confirmed_snippets=[], now_ts=1788834534, extra_user_context=extra,
                        client_context=client, current_user_source_id="current",
                    )
                captures.append(prepared)
                yield {"type": "final_ui", "payload": {"speech": "收到", "tool_events": []}}

        delivery = FakeQQGateway()
        delivery.build_group_identity_context = gateway.build_group_identity_context
        original = context.to_turn_payload()
        saved = deepcopy(original)
        for name in ("天为", "新群名片"):
            gateway._bot_group_labels[("10001", 30003)] = (0, "expired")
            gateway._onebot_transport.call.return_value = SimpleNamespace(ok=True, data={
                "user_id": 10001, "group_id": 30003, "card": name, "nickname": "山城高岭",
            })
            _process_qq_turn_streaming(
                engine=Generation(), qq_gateway=delivery, context=context, turn_payload=original,
                config_module=SimpleNamespace(QQ_STREAM_REPLIES_ENABLED=False),
            )
            prepared = captures[-1]
            self.assertNotIn("memcore_projection_failure", prepared)
            messages = [*prepared["history_turns"], {"role": "user", "content": prepared["user_prompt"]},
                        *prepared.get("ephemeral_turns", [])]
            text = "\n".join(str(m.get("content") or "") for m in messages)
            self.assertEqual(text.count("你在本群的显示名："), 1)
            self.assertIn("你在本群的显示名：" + name, text)
            self.assertNotIn("山城高岭", text)
            self.assertIn("@天为 你怎么看", text)  # The saved message keeps its original name.
        self.assertEqual(original, saved)
        self.assertEqual(manager.build_context_projection(provider_profile="openai_chat", **scope)["payloads"], frozen_before)

    def test_direct_json_and_xml_cards_keep_title_and_link_in_v6_text(self):
        for kind in ("json", "xml"):
            with self.subTest(kind=kind):
                gateway = self.gateway()
                context = gateway.build_message_context(self.event([self.at(10001), card_segment(kind)]))
                projected, _scope = self.open_projection(self.manager(), context)
                text = projected[0]["content"]
                self.assertIn("原始标题", text)
                self.assertIn("https://example.org/video", text)
                self.assertNotIn("保留字段", text)


if __name__ == "__main__":
    unittest.main()
