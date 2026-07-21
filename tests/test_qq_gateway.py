from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch

from companion_v01.qq_gateway import NapCatQQGateway, QQMessageContext
from companion_v01.qq_route_helpers import (
    apply_qq_current_outfit_visual,
    qq_attachment_ready_wait_seconds,
    qq_current_outfit_id_from_turn_payload,
    qq_pending_image_attachment_ids,
)


QQ_BOT_FIXTURE_ID = 10001
QQ_MASTER_FIXTURE_ID = 10002
QQ_USER_FIXTURE_ID = 10003
QQ_OTHER_USER_FIXTURE_ID = 10004
QQ_THIRD_USER_FIXTURE_ID = 10005
QQ_GROUP_FIXTURE_ID = 20001
QQ_FILE_GROUP_FIXTURE_ID = 20002


class FakeCharacterResourceService:
    def __init__(self) -> None:
        self.packs = {
            "reimu": {
                "pack_id": "reimu",
                "name": "Reimu",
                "app_name": "Reimu Pet",
                "user_title": "你",
            },
            "mika_sample": {
                "pack_id": "mika_sample",
                "name": "Mika",
                "app_name": "Mika Pet",
                "user_title": "店长",
            },
        }

    def list_character_packs(self):
        return list(self.packs.values())

    def build_character_identity(self, character_pack_id: str):
        item = self.packs.get(character_pack_id)
        if not item:
            return {}
        return {
            "character_id": character_pack_id,
            "assistant_name": item["name"],
            "app_name": item["app_name"],
            "user_label": item["user_title"],
            "pack_id": character_pack_id,
        }


def fake_outfit_manifest() -> dict:
    return {
        "schema_version": 2,
        "characters": {
            "outfits": [
                {
                    "id": "default",
                    "name": "默认服装",
                    "aliases": ["日常"],
                    "emotions": [{"id": "normal", "name": "普通"}],
                },
                {
                    "id": "sailor",
                    "name": "水手服",
                    "aliases": ["蓝白制服"],
                    "emotions": [{"id": "happy", "name": "开心"}],
                },
            ],
        },
        "defaults": {"outfit": "default", "emotion": "happy"},
        "clients": {"desktop_pet": {"default_outfit": "default"}},
    }


class QQGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.master_qq_patcher = patch(
            "companion_v01.qq_gateway.config.MASTER_QQ",
            str(QQ_MASTER_FIXTURE_ID),
        )
        self.bot_qq_patcher = patch(
            "companion_v01.qq_gateway.config.QQ_BOT_QQ",
            str(QQ_BOT_FIXTURE_ID),
        )
        self.master_qq_patcher.start()
        self.bot_qq_patcher.start()
        self.addCleanup(self.master_qq_patcher.stop)
        self.addCleanup(self.bot_qq_patcher.stop)

    def test_render_reply_messages_prefers_speech_segments(self) -> None:
        gateway = NapCatQQGateway()

        messages = gateway.render_reply_messages(
            {
                "speech": "第一句\n第二句",
                "speech_segments": ["第一句", "第二句"],
                "code_snippet": "",
            }
        )

        self.assertEqual(messages, ["第一句", "第二句"])

    def test_render_reply_messages_appends_code_to_last_segment(self) -> None:
        gateway = NapCatQQGateway()

        messages = gateway.render_reply_messages(
            {
                "speech": "",
                "speech_segments": ["我先说一句。", "代码在这里。"],
                "code_snippet": "print('hi')",
            }
        )

        self.assertEqual(messages, ["我先说一句", "代码在这里\n\nprint('hi')"])

    def test_duplicate_message_id_is_ignored(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": QQ_BOT_FIXTURE_ID,
            "user_id": QQ_MASTER_FIXTURE_ID,
            "message_id": "abc-1",
            "raw_message": "在吗",
        }

        first = gateway.build_message_context(event)
        second = gateway.build_message_context(dict(event))

        self.assertTrue(first.should_respond)
        self.assertFalse(second.should_respond)
        self.assertEqual(second.reason, "duplicate_event")

    @patch("companion_v01.qq_gateway.config.QQ_ALLOW_STALE_EVENTS", False)
    @patch("companion_v01.qq_gateway.config.QQ_EVENT_MAX_AGE_SECONDS", 300)
    def test_stale_message_event_is_ignored(self) -> None:
        gateway = NapCatQQGateway()

        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_USER_FIXTURE_ID,
                "message_id": "old-message-1",
                "time": int(time.time()) - 3600,
                "raw_message": "在吗",
            }
        )

        self.assertFalse(context.should_respond)
        self.assertEqual(context.reason, "stale_event")

    @patch("companion_v01.qq_gateway.config.QQ_ALLOW_STALE_EVENTS", True)
    @patch("companion_v01.qq_gateway.config.QQ_EVENT_MAX_AGE_SECONDS", 300)
    def test_stale_message_event_can_be_allowed_for_debug(self) -> None:
        gateway = NapCatQQGateway()

        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_MASTER_FIXTURE_ID,
                "message_id": "old-message-debug-1",
                "time": int(time.time()) - 3600,
                "raw_message": "在吗",
            }
        )

        self.assertTrue(context.should_respond)

    def test_group_follow_is_not_armed_after_mention(self) -> None:
        gateway = NapCatQQGateway()
        mention_event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": QQ_BOT_FIXTURE_ID,
            "user_id": QQ_MASTER_FIXTURE_ID,
            "group_id": QQ_GROUP_FIXTURE_ID,
            "message_id": "group-1",
            "message": [
                {"type": "at", "data": {"qq": str(QQ_BOT_FIXTURE_ID)}},
                {"type": "text", "data": {"text": " 在吗"}},
            ],
        }
        follow_event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": QQ_BOT_FIXTURE_ID,
            "user_id": QQ_MASTER_FIXTURE_ID,
            "group_id": QQ_GROUP_FIXTURE_ID,
            "message_id": "group-2",
            "message": [
                {"type": "text", "data": {"text": "我是在回复别人"}},
            ],
        }

        mentioned = gateway.build_message_context(mention_event)
        follow = gateway.build_message_context(follow_event)

        self.assertTrue(mentioned.should_respond)
        self.assertEqual(mentioned.reason, "group_mention")
        self.assertFalse(follow.should_respond)
        self.assertTrue(follow.should_record)
        self.assertEqual(follow.reason, "group_passive_observed")
        follow_payload = follow.to_turn_payload()
        self.assertEqual(follow_payload["message"], f"【QQ {QQ_MASTER_FIXTURE_ID}】我是在回复别人")
        self.assertEqual(follow_payload["actor_stable_id"], f"qq:{QQ_MASTER_FIXTURE_ID}")
        self.assertEqual(follow_payload["actor_display_name"], f"QQ {QQ_MASTER_FIXTURE_ID}")
        self.assertEqual(follow_payload["actor_platform"], "qq")

    def test_group_wake_word_triggers_response_without_at(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "group",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_MASTER_FIXTURE_ID,
                "group_id": QQ_GROUP_FIXTURE_ID,
                "message_id": "group-wake-word-1",
                "message": [
                    {"type": "text", "data": {"text": "Akane，在吗"}},
                ],
            }
        )

        self.assertTrue(context.should_respond)
        self.assertFalse(context.should_record)
        self.assertEqual(context.reason, "group_wake_word")

    def test_wake_word_prefix_can_trigger_fixed_commands(self) -> None:
        gateway = NapCatQQGateway()

        buy = gateway.parse_economy_command("Akane 购买 三色团子")
        shop = gateway.parse_economy_command("Akane，商店")
        model = gateway.parse_chat_model_command("Akane 模型列表")

        self.assertEqual(buy, {"action": "buy", "item_name": "三色团子", "quantity": 1})
        self.assertEqual(shop, {"action": "shop_list"})
        self.assertEqual(model, {"action": "list"})

    def test_economy_status_command_does_not_steal_natural_money_or_state_chat(self) -> None:
        gateway = NapCatQQGateway()

        natural_messages = [
            "多少钱？",
            "这个多少钱",
            "Akane 现在有钱吗",
            "你现在饿吗",
            "现在状态怎么样",
        ]
        for message in natural_messages:
            with self.subTest(message=message):
                self.assertIsNone(gateway.parse_economy_command(message))

    def test_economy_status_command_accepts_explicit_status_queries(self) -> None:
        gateway = NapCatQQGateway()

        explicit_messages = [
            "养成状态",
            "Akane 金币多少",
            "查余额",
            "查看饥饿",
            "当前好感度",
        ]
        for message in explicit_messages:
            with self.subTest(message=message):
                self.assertEqual(gateway.parse_economy_command(message), {"action": "status"})

    def test_outfit_command_requires_clear_separator_for_wear_shortcut(self) -> None:
        gateway = NapCatQQGateway()

        self.assertIsNone(gateway.parse_outfit_command("穿上泳装好看吗"))
        self.assertEqual(gateway.parse_outfit_command("穿上 泳装"), {"action": "switch", "outfit_id": "泳装"})

    def test_group_mention_opens_attachment_only_buffer_for_same_sender(self) -> None:
        gateway = NapCatQQGateway()
        mention_event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": QQ_BOT_FIXTURE_ID,
            "user_id": QQ_MASTER_FIXTURE_ID,
            "group_id": QQ_GROUP_FIXTURE_ID,
            "message_id": "group-buffer-1",
            "message": [
                {"type": "at", "data": {"qq": str(QQ_BOT_FIXTURE_ID)}},
                {"type": "text", "data": {"text": " 我等下补图"}},
            ],
        }
        image_event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": QQ_BOT_FIXTURE_ID,
            "user_id": QQ_MASTER_FIXTURE_ID,
            "group_id": QQ_GROUP_FIXTURE_ID,
            "message_id": "group-buffer-2",
            "message": [
                {
                    "type": "image",
                    "data": {
                        "file": "follow-up.jpg",
                        "url": "http://127.0.0.1/follow-up.jpg",
                    },
                },
            ],
        }
        text_event = {
            **image_event,
            "message_id": "group-buffer-3",
            "message": [{"type": "text", "data": {"text": "这句没有 at，不该回"}}],
        }
        other_user_image_event = {
            **image_event,
            "user_id": QQ_OTHER_USER_FIXTURE_ID,
            "message_id": "group-buffer-4",
        }

        mentioned = gateway.build_message_context(mention_event)
        buffered_image = gateway.build_message_context(image_event)
        plain_text = gateway.build_message_context(text_event)
        other_user_image = gateway.build_message_context(other_user_image_event)

        self.assertTrue(mentioned.should_respond)
        self.assertEqual(mentioned.reason, "group_mention")
        self.assertTrue(buffered_image.should_respond)
        self.assertEqual(buffered_image.reason, "group_attachment_buffer")
        self.assertEqual(buffered_image.clean_message, "发来了一张图片。")
        self.assertEqual(len(buffered_image.attachments or []), 1)
        self.assertFalse(plain_text.should_respond)
        self.assertTrue(plain_text.should_record)
        self.assertEqual(plain_text.reason, "group_passive_observed")
        self.assertFalse(other_user_image.should_respond)
        self.assertTrue(other_user_image.should_record)
        self.assertEqual(other_user_image.reason, "group_passive_observed")

    def test_group_members_share_group_scoped_memory(self) -> None:
        gateway = NapCatQQGateway()
        first_event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": QQ_BOT_FIXTURE_ID,
            "user_id": QQ_USER_FIXTURE_ID,
            "group_id": QQ_GROUP_FIXTURE_ID,
            "message_id": "group-member-1",
            "sender": {"card": "休比", "nickname": "fallback"},
            "message": [
                {"type": "at", "data": {"qq": str(QQ_BOT_FIXTURE_ID)}},
                {"type": "text", "data": {"text": " 你好"}},
            ],
        }
        second_event = {
            **first_event,
            "user_id": QQ_THIRD_USER_FIXTURE_ID,
            "message_id": "group-member-2",
        }

        first = gateway.build_message_context(first_event)
        second = gateway.build_message_context(second_event)

        self.assertTrue(first.should_respond)
        self.assertTrue(second.should_respond)
        self.assertEqual(first.session_id, f"qq_group_shared_{QQ_GROUP_FIXTURE_ID}")
        self.assertEqual(second.session_id, f"qq_group_shared_{QQ_GROUP_FIXTURE_ID}")
        self.assertEqual(first.profile_user_id, f"qq_group_shared_{QQ_GROUP_FIXTURE_ID}")
        self.assertEqual(second.profile_user_id, f"qq_group_shared_{QQ_GROUP_FIXTURE_ID}")

    def test_group_turn_payload_keeps_sender_label_for_shared_memory(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": QQ_BOT_FIXTURE_ID,
            "user_id": QQ_USER_FIXTURE_ID,
            "group_id": QQ_GROUP_FIXTURE_ID,
            "message_id": "group-speaker-1",
            "sender": {"card": "休比", "nickname": "fallback"},
            "message": [
                {"type": "at", "data": {"qq": str(QQ_BOT_FIXTURE_ID)}},
                {"type": "text", "data": {"text": " 你好"}},
            ],
        }

        context = gateway.build_message_context(event)
        payload = context.to_turn_payload()

        self.assertEqual(context.sender_label, "休比")
        self.assertEqual(payload["message"], "【休比】你好")
        self.assertEqual(payload["actor_stable_id"], f"qq:{QQ_USER_FIXTURE_ID}")
        self.assertEqual(payload["actor_display_name"], "休比")
        self.assertEqual(payload["actor_platform"], "qq")
        self.assertEqual(payload["qq_delivery_context"]["actor_stable_id"], f"qq:{QQ_USER_FIXTURE_ID}")
        self.assertEqual(payload["qq_delivery_context"]["actor_display_name"], "休比")
        self.assertIn("【昵称】", payload["extra_context"])

    def test_private_poke_notice_to_bot_becomes_normal_turn_payload(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "self_id": QQ_BOT_FIXTURE_ID,
            "user_id": QQ_USER_FIXTURE_ID,
            "sender_id": QQ_USER_FIXTURE_ID,
            "target_id": QQ_BOT_FIXTURE_ID,
            "time": int(time.time()),
        }

        context = gateway.build_message_context(event)
        payload = context.to_turn_payload()

        self.assertTrue(context.should_respond)
        self.assertEqual(context.reason, "qq_poke")
        self.assertFalse(context.is_group)
        self.assertEqual(context.target_id, QQ_USER_FIXTURE_ID)
        self.assertEqual(payload["message"], "刚才发生的互动：我在 QQ 里戳了戳你的头像。")
        self.assertNotIn("transient_user_message", payload)
        self.assertEqual(payload["client_mode"], "qq_text")
        self.assertNotIn("actor_stable_id", payload)
        self.assertNotIn("actor_display_name", payload)
        self.assertIn("我就是本轮戳一戳的发送者", payload["extra_context"])
        self.assertIn("戳了戳你", payload["extra_context"])

    def test_group_poke_notice_to_bot_uses_group_memory_and_sender_label(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "self_id": QQ_BOT_FIXTURE_ID,
            "user_id": QQ_USER_FIXTURE_ID,
            "sender_id": QQ_USER_FIXTURE_ID,
            "target_id": QQ_BOT_FIXTURE_ID,
            "group_id": QQ_GROUP_FIXTURE_ID,
            "sender": {"card": "休比", "nickname": "fallback"},
            "time": int(time.time()),
        }

        context = gateway.build_message_context(event)
        payload = context.to_turn_payload()

        self.assertTrue(context.should_respond)
        self.assertEqual(context.reason, "qq_poke")
        self.assertTrue(context.is_group)
        self.assertEqual(context.target_id, QQ_GROUP_FIXTURE_ID)
        self.assertEqual(context.session_id, f"qq_group_shared_{QQ_GROUP_FIXTURE_ID}")
        self.assertEqual(payload["message"], "【休比】刚才发生的互动：休比在 QQ 里戳了戳你的头像。")
        self.assertIn("休比双击头像戳了戳你", payload["extra_context"])

    def test_group_poke_notice_can_reuse_sender_label_from_recent_message(self) -> None:
        gateway = NapCatQQGateway()
        message_event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": QQ_BOT_FIXTURE_ID,
            "user_id": QQ_USER_FIXTURE_ID,
            "group_id": QQ_GROUP_FIXTURE_ID,
            "message_id": "group-speaker-before-poke",
            "sender": {"card": "休比", "nickname": "fallback"},
            "message": [
                {"type": "at", "data": {"qq": str(QQ_BOT_FIXTURE_ID)}},
                {"type": "text", "data": {"text": " 先打个招呼"}},
            ],
        }
        poke_event = {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "self_id": QQ_BOT_FIXTURE_ID,
            "user_id": QQ_USER_FIXTURE_ID,
            "sender_id": QQ_USER_FIXTURE_ID,
            "target_id": QQ_BOT_FIXTURE_ID,
            "group_id": QQ_GROUP_FIXTURE_ID,
            "time": int(time.time()),
        }

        message_context = gateway.build_message_context(message_event)
        poke_context = gateway.build_message_context(poke_event)
        payload = poke_context.to_turn_payload()

        self.assertTrue(message_context.should_respond)
        self.assertTrue(poke_context.should_respond)
        self.assertEqual(poke_context.sender_label, "休比")
        self.assertEqual(payload["message"], "【休比】刚才发生的互动：休比在 QQ 里戳了戳你的头像。")

    def test_group_poke_notice_fetches_sender_label_from_onebot_when_notice_has_no_sender(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "self_id": QQ_BOT_FIXTURE_ID,
            "user_id": QQ_USER_FIXTURE_ID,
            "target_id": QQ_BOT_FIXTURE_ID,
            "group_id": QQ_GROUP_FIXTURE_ID,
            "time": int(time.time()),
        }

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {"status": "ok", "retcode": 0, "data": {"card": "休比", "nickname": "fallback"}}

        with patch(
            "companion_v01.onebot_transport.requests.Session.request", return_value=FakeResponse()
        ) as mocked_post:
            context = gateway.build_message_context(event)

        payload = context.to_turn_payload()

        self.assertTrue(context.should_respond)
        self.assertEqual(context.sender_label, "休比")
        self.assertEqual(payload["message"], "【休比】刚才发生的互动：休比在 QQ 里戳了戳你的头像。")
        mocked_post.assert_called_once()
        self.assertTrue(mocked_post.call_args.args[1].endswith("/get_group_member_info"))
        self.assertEqual(mocked_post.call_args.kwargs["json"]["group_id"], QQ_GROUP_FIXTURE_ID)
        self.assertEqual(mocked_post.call_args.kwargs["json"]["user_id"], QQ_USER_FIXTURE_ID)

    def test_poke_notice_uses_operator_id_when_user_id_is_target(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "self_id": QQ_BOT_FIXTURE_ID,
            "operator_id": QQ_USER_FIXTURE_ID,
            "user_id": QQ_BOT_FIXTURE_ID,
            "target_id": QQ_BOT_FIXTURE_ID,
            "time": int(time.time()),
        }

        context = gateway.build_message_context(event)
        payload = context.to_turn_payload()

        self.assertTrue(context.should_respond)
        self.assertEqual(context.user_id, QQ_USER_FIXTURE_ID)
        self.assertEqual(context.target_id, QQ_USER_FIXTURE_ID)
        self.assertEqual(payload["message"], "刚才发生的互动：我在 QQ 里戳了戳你的头像。")

    def test_poke_notice_not_targeting_bot_is_ignored(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "self_id": QQ_BOT_FIXTURE_ID,
            "user_id": QQ_USER_FIXTURE_ID,
            "target_id": QQ_OTHER_USER_FIXTURE_ID,
            "time": int(time.time()),
        }

        context = gateway.build_message_context(event)

        self.assertFalse(context.should_respond)
        self.assertEqual(context.reason, "poke_not_for_bot")

    def test_duplicate_poke_notice_is_ignored(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "self_id": QQ_BOT_FIXTURE_ID,
            "sender_id": QQ_USER_FIXTURE_ID,
            "target_id": QQ_BOT_FIXTURE_ID,
            "time": int(time.time()),
        }

        first = gateway.build_message_context(event)
        second = gateway.build_message_context(dict(event))

        self.assertTrue(first.should_respond)
        self.assertFalse(second.should_respond)
        self.assertEqual(second.reason, "duplicate_event")

    @patch("companion_v01.qq_gateway.config.QQ_CHARACTER_PACK_ID", "reimu_demo")
    def test_turn_payload_includes_configured_character_pack_id(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": QQ_BOT_FIXTURE_ID,
            "user_id": QQ_MASTER_FIXTURE_ID,
            "message_id": "character-pack-1",
            "raw_message": "在吗",
        }

        context = gateway.build_message_context(event)
        payload = context.to_turn_payload()
        delivery_context = context.to_delivery_context()

        self.assertTrue(context.should_respond)
        self.assertEqual(context.character_pack_id, "reimu_demo")
        self.assertEqual(payload["character_pack_id"], "reimu_demo")
        self.assertEqual(payload["qq_delivery_context"]["character_pack_id"], "reimu_demo")
        self.assertEqual(delivery_context["character_pack_id"], "reimu_demo")
        self.assertEqual(gateway.status()["character_pack_id"], "reimu_demo")

    @patch("companion_v01.qq_gateway.config.QQ_CHARACTER_PACK_ID", "legacy-default")
    def test_bound_instance_character_pack_overrides_legacy_config(self) -> None:
        gateway = NapCatQQGateway(default_character_pack_id="reimu")

        self.assertEqual(gateway.default_character_pack_id, "reimu")
        self.assertEqual(gateway.character_pack_id, "reimu")
        self.assertEqual(gateway.status()["default_character_pack_id"], "reimu")

    @patch("companion_v01.qq_gateway.config.QQ_CHARACTER_PACK_ID", "../bad")
    def test_turn_payload_omits_invalid_character_pack_id(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": QQ_BOT_FIXTURE_ID,
            "user_id": QQ_MASTER_FIXTURE_ID,
            "message_id": "character-pack-invalid-1",
            "raw_message": "在吗",
        }

        context = gateway.build_message_context(event)
        payload = context.to_turn_payload()

        self.assertTrue(context.should_respond)
        self.assertEqual(context.character_pack_id, "")
        self.assertNotIn("character_pack_id", payload)
        self.assertNotIn("character_pack_id", payload["qq_delivery_context"])

    def test_character_command_switches_current_qq_session(self) -> None:
        gateway = NapCatQQGateway()
        service = FakeCharacterResourceService()
        switch_context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_USER_FIXTURE_ID,
                "message_id": "switch-character-1",
                "raw_message": "切换角色 reimu",
            }
        )

        result = gateway.handle_character_command(
            switch_context,
            character_resource_service=service,
        )

        self.assertIsNotNone(result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "switched")
        self.assertEqual(result["character_pack_id"], "reimu")
        self.assertEqual(gateway.resolve_character_pack_id(f"qq_pri_{QQ_USER_FIXTURE_ID}"), "reimu")

        next_context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_USER_FIXTURE_ID,
                "message_id": "switch-character-2",
                "raw_message": "在吗",
            }
        )
        self.assertEqual(next_context.character_pack_id, "reimu")
        self.assertEqual(next_context.to_turn_payload()["character_pack_id"], "reimu")

    def test_character_command_persists_current_qq_session_pack(self) -> None:
        service = FakeCharacterResourceService()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "qq_gateway_state.json"
            gateway = NapCatQQGateway(state_path=state_path)
            switch_context = gateway.build_message_context(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "self_id": QQ_BOT_FIXTURE_ID,
                    "user_id": QQ_USER_FIXTURE_ID,
                    "message_id": "switch-character-persist-1",
                    "raw_message": "切换角色 reimu",
                }
            )

            result = gateway.handle_character_command(
                switch_context,
                character_resource_service=service,
            )

            self.assertIsNotNone(result)
            self.assertTrue(result["ok"])
            self.assertTrue(state_path.is_file())

            restored_gateway = NapCatQQGateway(state_path=state_path)
            restored_context = restored_gateway.build_message_context(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "self_id": QQ_BOT_FIXTURE_ID,
                    "user_id": QQ_USER_FIXTURE_ID,
                    "message_id": "switch-character-persist-2",
                    "raw_message": "在吗",
                }
            )

            self.assertEqual(
                restored_gateway.resolve_character_pack_id(f"qq_pri_{QQ_USER_FIXTURE_ID}"),
                "reimu",
            )
            self.assertEqual(restored_context.character_pack_id, "reimu")
            self.assertEqual(restored_context.to_turn_payload()["character_pack_id"], "reimu")

    def test_legacy_finance_mode_state_is_ignored_and_dropped_on_next_save(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "qq_gateway_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "schema_version": "akane.qq_gateway_state.v1",
                        "character_pack_overrides": {},
                        "finance_mode_overrides": {f"qq_pri_{QQ_USER_FIXTURE_ID}": "push"},
                    }
                ),
                encoding="utf-8",
            )

            gateway = NapCatQQGateway(state_path=state_path)
            saved = gateway.set_session_character_pack_id(f"qq_pri_{QQ_USER_FIXTURE_ID}", "reimu")
            persisted = json.loads(state_path.read_text(encoding="utf-8"))

            self.assertTrue(saved)
            self.assertFalse(hasattr(gateway, "finance_mode_overrides"))
            self.assertNotIn("finance_mode_overrides", persisted)
            self.assertEqual(
                persisted["character_pack_overrides"][f"qq_pri_{QQ_USER_FIXTURE_ID}"],
                "reimu",
            )

    def test_group_vision_command_persists_per_group_setting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "qq_gateway_state.json"
            gateway = NapCatQQGateway(state_path=state_path)
            context = gateway.build_message_context(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "self_id": QQ_BOT_FIXTURE_ID,
                    "user_id": QQ_USER_FIXTURE_ID,
                    "group_id": QQ_GROUP_FIXTURE_ID,
                    "message_id": "group-vision-disable-1",
                    "message": [
                        {"type": "at", "data": {"qq": str(QQ_BOT_FIXTURE_ID)}},
                        {"type": "text", "data": {"text": " /识图关"}},
                    ],
                }
            )

            result = gateway.handle_group_vision_command(context, sender_role="admin")

            self.assertIsNotNone(result)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "disabled")
            self.assertEqual(result["reply"], "识图模式已关闭")
            self.assertTrue(result["state_persisted"])
            self.assertFalse(gateway.is_group_vision_enabled(QQ_GROUP_FIXTURE_ID))
            self.assertTrue(gateway.is_group_vision_enabled(QQ_GROUP_FIXTURE_ID + 1))

            restored_gateway = NapCatQQGateway(state_path=state_path)
            self.assertFalse(restored_gateway.is_group_vision_enabled(QQ_GROUP_FIXTURE_ID))
            self.assertEqual(restored_gateway.status()["disabled_group_vision_count"], 1)

            enable_context = restored_gateway.build_message_context(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "self_id": QQ_BOT_FIXTURE_ID,
                    "user_id": QQ_USER_FIXTURE_ID,
                    "group_id": QQ_GROUP_FIXTURE_ID,
                    "message_id": "group-vision-enable-1",
                    "message": [
                        {"type": "at", "data": {"qq": str(QQ_BOT_FIXTURE_ID)}},
                        {"type": "text", "data": {"text": " /识图开"}},
                    ],
                }
            )
            enabled = restored_gateway.handle_group_vision_command(enable_context, sender_role="admin")
            self.assertIsNotNone(enabled)
            self.assertEqual(enabled["status"], "enabled")
            self.assertEqual(enabled["reply"], "识图模式已打开")
            self.assertTrue(restored_gateway.is_group_vision_enabled(QQ_GROUP_FIXTURE_ID))

    def test_group_vision_command_rejects_non_admin_change(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "group",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_OTHER_USER_FIXTURE_ID,
                "group_id": QQ_GROUP_FIXTURE_ID,
                "message_id": "group-vision-forbidden-1",
                "message": [
                    {"type": "at", "data": {"qq": str(QQ_BOT_FIXTURE_ID)}},
                    {"type": "text", "data": {"text": " /识图关"}},
                ],
            }
        )

        result = gateway.handle_group_vision_command(context, sender_role="member")

        self.assertIsNotNone(result)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "forbidden")
        self.assertTrue(gateway.is_group_vision_enabled(QQ_GROUP_FIXTURE_ID))

    def test_bare_group_vision_command_does_not_wake_bot(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "group",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_USER_FIXTURE_ID,
                "group_id": QQ_GROUP_FIXTURE_ID,
                "message_id": "group-vision-bare-ignored-1",
                "raw_message": "/识图关",
            }
        )

        self.assertFalse(context.should_respond)
        self.assertTrue(context.should_record)
        self.assertEqual(context.reason, "group_passive_observed")
        self.assertTrue(gateway.is_group_vision_enabled(QQ_GROUP_FIXTURE_ID))

    @patch("companion_v01.qq_gateway.config.QQ_CHARACTER_PACK_ID", "mika_sample")
    def test_builtin_character_override_persists_across_restart(self) -> None:
        service = FakeCharacterResourceService()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "qq_gateway_state.json"
            gateway = NapCatQQGateway(state_path=state_path)
            gateway.set_session_character_pack_id(f"qq_pri_{QQ_USER_FIXTURE_ID}", "reimu")
            context = gateway.build_message_context(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "self_id": QQ_BOT_FIXTURE_ID,
                    "user_id": QQ_USER_FIXTURE_ID,
                    "message_id": "character-builtin-persist-1",
                    "raw_message": "切回Akane",
                }
            )

            result = gateway.handle_character_command(context, character_resource_service=service)

            self.assertIsNotNone(result)
            self.assertEqual(result["status"], "builtin")
            restored_gateway = NapCatQQGateway(state_path=state_path)
            self.assertEqual(
                restored_gateway.resolve_character_pack_id(f"qq_pri_{QQ_USER_FIXTURE_ID}"),
                "",
            )

    @patch("companion_v01.qq_gateway.config.QQ_CHARACTER_PACK_ID", "")
    def test_character_command_lists_current_and_resets_to_default(self) -> None:
        gateway = NapCatQQGateway()
        service = FakeCharacterResourceService()
        list_context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_USER_FIXTURE_ID,
                "message_id": "character-list-1",
                "raw_message": "角色列表",
            }
        )
        list_result = gateway.handle_character_command(list_context, character_resource_service=service)

        self.assertIsNotNone(list_result)
        self.assertEqual(list_result["status"], "listed")
        self.assertIn("可用角色包\n", list_result["reply"])
        self.assertIn("\n  reimu", list_result["reply"])
        self.assertIn("\n  mika_sample", list_result["reply"])
        self.assertIn("reimu", list_result["reply"])
        self.assertIn("mika_sample", list_result["reply"])

        gateway.set_session_character_pack_id(f"qq_pri_{QQ_USER_FIXTURE_ID}", "reimu")
        current_context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_USER_FIXTURE_ID,
                "message_id": "character-current-1",
                "raw_message": "当前角色",
            }
        )
        current_result = gateway.handle_character_command(current_context, character_resource_service=service)

        self.assertIsNotNone(current_result)
        self.assertEqual(current_result["status"], "current")
        self.assertIn("reimu", current_result["reply"])
        self.assertIn("本会话临时切换", current_result["reply"])

        reset_context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_USER_FIXTURE_ID,
                "message_id": "character-reset-1",
                "raw_message": "切回默认角色",
            }
        )
        reset_result = gateway.handle_character_command(reset_context, character_resource_service=service)

        self.assertIsNotNone(reset_result)
        self.assertEqual(reset_result["status"], "default")
        self.assertEqual(gateway.resolve_character_pack_id(f"qq_pri_{QQ_USER_FIXTURE_ID}"), "")

    def test_character_command_can_force_builtin_akane(self) -> None:
        gateway = NapCatQQGateway()
        service = FakeCharacterResourceService()
        gateway.set_session_character_pack_id(f"qq_pri_{QQ_USER_FIXTURE_ID}", "reimu")
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_USER_FIXTURE_ID,
                "message_id": "character-builtin-1",
                "raw_message": "切回Akane",
            }
        )

        result = gateway.handle_character_command(context, character_resource_service=service)

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "builtin")
        self.assertEqual(result["character_pack_id"], "")
        self.assertEqual(gateway.resolve_character_pack_id(f"qq_pri_{QQ_USER_FIXTURE_ID}"), "")

    def test_character_command_rejects_unknown_or_invalid_pack(self) -> None:
        gateway = NapCatQQGateway()
        service = FakeCharacterResourceService()
        unknown_context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_USER_FIXTURE_ID,
                "message_id": "character-unknown-1",
                "raw_message": "切换角色 missing_pack",
            }
        )
        invalid_context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_USER_FIXTURE_ID,
                "message_id": "character-invalid-1",
                "raw_message": "切换角色 ../bad",
            }
        )

        unknown = gateway.handle_character_command(unknown_context, character_resource_service=service)
        invalid = gateway.handle_character_command(invalid_context, character_resource_service=service)

        self.assertIsNotNone(unknown)
        self.assertFalse(unknown["ok"])
        self.assertEqual(unknown["status"], "unknown_character_pack")
        self.assertIn("当前可用：\n", unknown["reply"])
        self.assertIn("\n  reimu", unknown["reply"])
        self.assertIsNotNone(invalid)
        self.assertFalse(invalid["ok"])
        self.assertEqual(invalid["status"], "invalid_character_pack_id")

    def test_outfit_command_switches_and_persists_current_qq_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "qq_gateway_state.json"
            gateway = NapCatQQGateway(state_path=state_path)
            gateway.set_session_character_pack_id(f"qq_pri_{QQ_USER_FIXTURE_ID}", "reimu")
            context = gateway.build_message_context(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "self_id": QQ_BOT_FIXTURE_ID,
                    "user_id": QQ_USER_FIXTURE_ID,
                    "message_id": "switch-outfit-1",
                    "raw_message": "切换服装 水手服",
                }
            )

            result = gateway.handle_outfit_command(
                context,
                resource_manifest_builder=lambda _pack_id: fake_outfit_manifest(),
            )

            self.assertIsNotNone(result)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "switched")
            self.assertTrue(result["_llm_passthrough"])
            self.assertIn("水手服", result["qq_action_note"])
            self.assertIn("水手服", result["turn_message"])
            self.assertIn("我把你的 QQ 当前会话服装切换为", result["turn_message"])
            self.assertNotIn("用户刚刚", result["turn_message"])
            self.assertEqual(result["character_pack_id"], "reimu")
            self.assertEqual(result["outfit_id"], "sailor")
            self.assertEqual(gateway.resolve_session_outfit_id(f"qq_pri_{QQ_USER_FIXTURE_ID}"), "sailor")

            restored_gateway = NapCatQQGateway(state_path=state_path)
            self.assertEqual(restored_gateway.resolve_session_outfit_id(f"qq_pri_{QQ_USER_FIXTURE_ID}"), "sailor")

    def test_outfit_command_lists_current_and_resets_to_default(self) -> None:
        gateway = NapCatQQGateway()
        gateway.set_session_character_pack_id(f"qq_pri_{QQ_USER_FIXTURE_ID}", "reimu")
        gateway.set_session_outfit_id(f"qq_pri_{QQ_USER_FIXTURE_ID}", "sailor")

        list_context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_USER_FIXTURE_ID,
                "message_id": "outfit-list-1",
                "raw_message": "服装列表",
            }
        )
        current_context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_USER_FIXTURE_ID,
                "message_id": "outfit-current-1",
                "raw_message": "当前服装",
            }
        )
        reset_context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_USER_FIXTURE_ID,
                "message_id": "outfit-reset-1",
                "raw_message": "切回默认服装",
            }
        )
        unknown_context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_USER_FIXTURE_ID,
                "message_id": "outfit-unknown-1",
                "raw_message": "切换服装 不存在",
            }
        )

        list_result = gateway.handle_outfit_command(
            list_context, resource_manifest_builder=lambda _pack_id: fake_outfit_manifest()
        )
        current_result = gateway.handle_outfit_command(
            current_context, resource_manifest_builder=lambda _pack_id: fake_outfit_manifest()
        )
        reset_result = gateway.handle_outfit_command(
            reset_context, resource_manifest_builder=lambda _pack_id: fake_outfit_manifest()
        )
        unknown_result = gateway.handle_outfit_command(
            unknown_context, resource_manifest_builder=lambda _pack_id: fake_outfit_manifest()
        )

        self.assertIsNotNone(list_result)
        self.assertEqual(list_result["status"], "listed")
        self.assertIn("可用服装\n", list_result["reply"])
        self.assertIn("\n  default（默认服装）", list_result["reply"])
        self.assertIn("\n  sailor（水手服）", list_result["reply"])
        self.assertIn("sailor", list_result["reply"])
        self.assertIsNotNone(current_result)
        self.assertEqual(current_result["status"], "current")
        self.assertIn("本会话临时切换", current_result["reply"])
        self.assertIsNotNone(reset_result)
        self.assertEqual(reset_result["status"], "default")
        self.assertTrue(reset_result["_llm_passthrough"])
        self.assertIn("默认服装", reset_result["turn_message"])
        self.assertIsNotNone(unknown_result)
        self.assertEqual(unknown_result["status"], "unknown_outfit")
        self.assertIn("当前可用：\n", unknown_result["reply"])
        self.assertIn("\n  sailor（水手服）", unknown_result["reply"])
        self.assertEqual(gateway.resolve_session_outfit_id(f"qq_pri_{QQ_USER_FIXTURE_ID}"), "")

    def test_switching_character_clears_session_outfit_override(self) -> None:
        gateway = NapCatQQGateway()
        service = FakeCharacterResourceService()
        gateway.set_session_character_pack_id(f"qq_pri_{QQ_USER_FIXTURE_ID}", "reimu")
        gateway.set_session_outfit_id(f"qq_pri_{QQ_USER_FIXTURE_ID}", "sailor")
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_USER_FIXTURE_ID,
                "message_id": "switch-character-clears-outfit-1",
                "raw_message": "切换角色 mika_sample",
            }
        )

        result = gateway.handle_character_command(context, character_resource_service=service)

        self.assertIsNotNone(result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["character_pack_id"], "mika_sample")
        self.assertEqual(gateway.resolve_session_outfit_id(f"qq_pri_{QQ_USER_FIXTURE_ID}"), "")

    def test_qq_image_attachments_wait_for_vision_timeout_window(self) -> None:
        config_module = SimpleNamespace(
            QQ_ATTACHMENT_READY_WAIT_SECONDS=8.0,
            VISION_REQUEST_TIMEOUT=60.0,
        )
        image_context = SimpleNamespace(attachments=[{"kind": "image"}])
        document_context = SimpleNamespace(attachments=[{"kind": "document"}])

        self.assertEqual(qq_attachment_ready_wait_seconds(image_context, config_module), 65.0)
        self.assertEqual(qq_attachment_ready_wait_seconds(document_context, config_module), 8.0)

    def test_pending_image_attachment_ids_only_selects_pending_images(self) -> None:
        registered = [
            {"attachment_id": "img_1", "kind": "image"},
            {"attachment_id": "doc_1", "kind": "document"},
            {"attachment_id": "img_2", "kind": "image"},
        ]
        wait_result = {"pending": ["img_1", "doc_1"], "ready": ["img_2"], "failed": []}

        self.assertEqual(qq_pending_image_attachment_ids(registered, wait_result), ["img_1"])

    def test_pending_image_attachment_ids_can_use_wait_result_kinds(self) -> None:
        wait_result = {
            "pending": ["img_1", "doc_1"],
            "ready": [],
            "failed": [],
            "kinds_by_id": {"img_1": "image", "doc_1": "document"},
        }

        self.assertEqual(qq_pending_image_attachment_ids([], wait_result), ["img_1"])

    def test_qq_turn_payload_includes_current_outfit_visual(self) -> None:
        gateway = NapCatQQGateway()
        gateway.set_session_outfit_id(f"qq_pri_{QQ_USER_FIXTURE_ID}", "sailor")
        context = SimpleNamespace(
            session_id=f"qq_pri_{QQ_USER_FIXTURE_ID}",
            profile_user_id="qq_user",
        )
        engine = SimpleNamespace(
            build_resource_manifest=lambda **_kwargs: fake_outfit_manifest(),
        )
        turn_payload = {"character_pack_id": "reimu"}

        apply_qq_current_outfit_visual(turn_payload, qq_gateway=gateway, context=context, engine=engine)

        self.assertEqual(turn_payload["current_visual"]["character"]["outfit"], "sailor")
        self.assertEqual(turn_payload["current_visual"]["emotion"], "happy")

    @patch("companion_v01.qq_gateway.config.QQ_REPLY_MODE", "auto")
    def test_reply_mode_command_switches_current_qq_session(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_USER_FIXTURE_ID,
                "message_id": "reply-mode-voice-1",
                "raw_message": "语音模式",
            }
        )

        result = gateway.handle_reply_mode_command(context)

        self.assertIsNotNone(result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "switched")
        self.assertEqual(result["reply_mode"], "voice")
        self.assertEqual(gateway.resolve_reply_mode(f"qq_pri_{QQ_USER_FIXTURE_ID}"), "voice")

        next_context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_USER_FIXTURE_ID,
                "message_id": "reply-mode-voice-2",
                "raw_message": "在吗",
            }
        )
        self.assertEqual(next_context.reply_mode, "voice")
        self.assertEqual(next_context.to_turn_payload()["qq_reply_mode"], "voice")
        self.assertEqual(next_context.to_delivery_context()["reply_mode"], "voice")
        self.assertIn("当前 QQ 回复投递模式：语音模式", next_context.extra_context)

    def test_chat_model_command_switches_current_qq_session_for_master(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_MASTER_FIXTURE_ID,
                "message_id": "chat-model-switch-1",
                "raw_message": "切换模型 deepseek-v4-flash",
            }
        )

        command = gateway.parse_chat_model_command(context.clean_message)
        result = gateway.handle_chat_model_command(
            context,
            command=command,
            default_model="deepseek-chat",
        )

        self.assertIsNotNone(result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "switched")
        self.assertEqual(result["chat_model"], "deepseek-v4-flash")
        self.assertIn("供应商、密钥和 base_url 仍使用当前全局配置", result["reply"])
        self.assertEqual(gateway.resolve_chat_model_override(context.session_id), "deepseek-v4-flash")

        next_context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_MASTER_FIXTURE_ID,
                "message_id": "chat-model-switch-2",
                "raw_message": "在吗",
            }
        )
        self.assertEqual(next_context.chat_model_override, "deepseek-v4-flash")
        self.assertEqual(next_context.to_turn_payload()["chat_model_override"], "deepseek-v4-flash")
        self.assertEqual(next_context.to_delivery_context()["chat_model_override"], "deepseek-v4-flash")
        self.assertIn("当前 QQ 会话临时聊天模型：deepseek-v4-flash", next_context.extra_context)

    def test_chat_model_command_lists_current_provider_models(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_MASTER_FIXTURE_ID,
                "message_id": "chat-model-list-1",
                "raw_message": "模型列表",
            }
        )

        result = gateway.handle_chat_model_command(
            context,
            command=gateway.parse_chat_model_command(context.clean_message),
            default_model="deepseek-chat",
            available_models=["deepseek-v4-flash", "qwen/qwen3-coder"],
        )

        self.assertIsNotNone(result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "listed")
        self.assertIn("当前供应商可用模型", result["reply"])
        self.assertIn("\n  deepseek-v4-flash\n", result["reply"])
        self.assertIn("\n  qwen/qwen3-coder\n", result["reply"])
        self.assertIn("切换模型 模型名", result["reply"])

    def test_chat_model_command_rejects_non_master(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_OTHER_USER_FIXTURE_ID,
                "message_id": "chat-model-forbidden-1",
                "raw_message": "切换模型 deepseek-v4-flash",
            }
        )

        result = gateway.handle_chat_model_command(
            context,
            command=gateway.parse_chat_model_command(context.clean_message),
            default_model="deepseek-chat",
        )

        self.assertIsNotNone(result)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "forbidden")
        self.assertEqual(gateway.resolve_chat_model_override(context.session_id), "")

    def test_chat_model_command_state_persists_session_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "qq_gateway_state.json"
            gateway = NapCatQQGateway(state_path=state_path)
            context = gateway.build_message_context(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "self_id": QQ_BOT_FIXTURE_ID,
                    "user_id": QQ_MASTER_FIXTURE_ID,
                    "message_id": "chat-model-persist-1",
                    "raw_message": "model deepseek-v4-flash",
                }
            )

            result = gateway.handle_chat_model_command(
                context,
                command=gateway.parse_chat_model_command(context.clean_message),
                default_model="deepseek-chat",
            )

            self.assertIsNotNone(result)
            self.assertTrue(result["ok"])
            self.assertTrue(result["state_persisted"])
            self.assertTrue(state_path.is_file())

            restored_gateway = NapCatQQGateway(state_path=state_path)
            self.assertEqual(restored_gateway.resolve_chat_model_override(context.session_id), "deepseek-v4-flash")

    def test_chat_model_default_command_clears_session_override(self) -> None:
        gateway = NapCatQQGateway()
        session_id = "master"
        self.assertTrue(gateway.set_session_chat_model_override(session_id, "deepseek-v4-flash"))
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_MASTER_FIXTURE_ID,
                "message_id": "chat-model-default-1",
                "raw_message": "切回默认模型",
            }
        )

        result = gateway.handle_chat_model_command(
            context,
            command=gateway.parse_chat_model_command(context.clean_message),
            default_model="deepseek-chat",
        )

        self.assertIsNotNone(result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "default")
        self.assertEqual(result["chat_model"], "deepseek-chat")
        self.assertEqual(gateway.resolve_chat_model_override(session_id), "")

    def test_send_voice_uses_onebot_record_segment(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_USER_FIXTURE_ID,
                "message_id": "send-voice-1",
                "raw_message": "在吗",
            }
        )

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {"status": "ok"}

        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "reply.wav"
            audio_path.write_bytes(b"RIFF....WAVE")
            with patch(
                "companion_v01.onebot_transport.requests.Session.request", return_value=FakeResponse()
            ) as mocked_post:
                result = gateway.send_voice(context, audio_path=str(audio_path), name="reply")

        self.assertTrue(result["ok"])
        payload = mocked_post.call_args.kwargs["json"]
        self.assertEqual(payload["user_id"], QQ_USER_FIXTURE_ID)
        self.assertEqual(payload["message"][0], {"type": "reply", "data": {"id": "send-voice-1"}})
        record = next(item for item in payload["message"] if item["type"] == "record")
        self.assertIn("file", record["data"])

    def test_send_replies_quotes_only_the_first_segment(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_USER_FIXTURE_ID,
                "message_id": "multi-reply-1",
                "raw_message": "分段回复",
            }
        )

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"status": "ok", "retcode": 0, "data": {}}

        with (
            patch("companion_v01.qq_gateway.config.QQ_REPLY_SEGMENT_DELAY_SECONDS", 0),
            patch(
                "companion_v01.onebot_transport.requests.Session.request",
                side_effect=[FakeResponse(), FakeResponse()],
            ) as request,
        ):
            result = gateway.send_replies(context, ["第一段", "第二段"])

        self.assertTrue(result["ok"])
        first_message = request.call_args_list[0].kwargs["json"]["message"]
        second_message = request.call_args_list[1].kwargs["json"]["message"]
        self.assertEqual(first_message[0], {"type": "reply", "data": {"id": "multi-reply-1"}})
        self.assertEqual(second_message, [{"type": "text", "data": {"text": "第二段"}}])

    def test_send_image_checks_onebot_result_and_falls_back_to_base64(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_USER_FIXTURE_ID,
                "message_id": "send-image-base64-1",
                "raw_message": "发张表情图",
            }
        )

        class FakeResponse:
            def __init__(self, payload: dict) -> None:
                self.payload = payload

            def raise_for_status(self) -> None:
                return None

            def json(self):
                return dict(self.payload)

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "happy.png"
            image_path.write_bytes(b"fake-png-bytes")
            failed = FakeResponse({"status": "failed", "retcode": 200})
            succeeded = FakeResponse({"status": "ok", "retcode": 0, "data": {"message_id": 9}})
            with patch(
                "companion_v01.onebot_transport.requests.Session.request",
                side_effect=[failed, failed, succeeded],
            ) as mocked_post:
                result = gateway.send_image(context, image_path=str(image_path), name="开心")

        self.assertTrue(result["ok"])
        self.assertEqual(result["transport"], "base64")
        self.assertEqual(mocked_post.call_count, 3)
        image = next(item for item in mocked_post.call_args.kwargs["json"]["message"] if item["type"] == "image")
        final_file = image["data"]["file"]
        self.assertTrue(final_file.startswith("base64://"))
        self.assertNotIn("fake-png-bytes", str(result))

    def test_send_mface_uses_onebot_market_face_segment(self) -> None:
        gateway = NapCatQQGateway()
        context = QQMessageContext(
            should_respond=True,
            reason="test",
            is_group=True,
            target_id=QQ_GROUP_FIXTURE_ID,
            group_id=QQ_GROUP_FIXTURE_ID,
            session_id=f"qq_group_shared_{QQ_GROUP_FIXTURE_ID}",
            profile_user_id=f"qq_group_shared_{QQ_GROUP_FIXTURE_ID}",
        )

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {"status": "ok"}

        with patch(
            "companion_v01.onebot_transport.requests.Session.request", return_value=FakeResponse()
        ) as mocked_post:
            result = gateway.send_mface(
                context,
                mface={
                    "emoji_package_id": "123",
                    "emoji_id": "happy-001",
                    "key": "napcat-key",
                    "summary": "开心",
                },
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "send_group_msg")
        payload = mocked_post.call_args.kwargs["json"]
        self.assertEqual(payload["group_id"], QQ_GROUP_FIXTURE_ID)
        self.assertEqual(payload["message"][0]["type"], "mface")
        self.assertEqual(
            payload["message"][0]["data"],
            {
                "emoji_package_id": 123,
                "emoji_id": "happy-001",
                "key": "napcat-key",
                "summary": "开心",
            },
        )

    def test_send_emotion_mface_maps_final_emotion_and_dedupes(self) -> None:
        gateway = NapCatQQGateway()
        context = QQMessageContext(
            should_respond=True,
            reason="test",
            is_group=True,
            target_id=QQ_GROUP_FIXTURE_ID,
            group_id=QQ_GROUP_FIXTURE_ID,
            session_id=f"qq_group_shared_{QQ_GROUP_FIXTURE_ID}",
            profile_user_id=f"qq_group_shared_{QQ_GROUP_FIXTURE_ID}",
        )
        config = {
            "emotion_mfaces": {
                "enabled": True,
                "min_interval_seconds": 60,
                "map": {
                    "happy": {
                        "emoji_package_id": 123,
                        "emoji_id": "happy-001",
                        "key": "napcat-key",
                        "summary": "开心",
                    }
                },
            }
        }

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {"status": "ok"}

        with patch(
            "companion_v01.onebot_transport.requests.Session.request", return_value=FakeResponse()
        ) as mocked_post:
            first = gateway.send_emotion_mface(
                context,
                {"speech": "好。", "emotion": "happy"},
                qq_delivery_config=config,
            )
            second = gateway.send_emotion_mface(
                context,
                {"speech": "嗯。", "emotion": "happy"},
                qq_delivery_config=config,
            )

        self.assertTrue(first["ok"])
        self.assertEqual(first["status"], "sent")
        self.assertEqual(first["emotion"], "happy")
        self.assertEqual(second["status"], "skipped")
        self.assertEqual(second["reason"], "dedupe_interval")
        self.assertEqual(mocked_post.call_count, 1)

    def test_send_emotion_mface_skips_without_configured_mapping(self) -> None:
        gateway = NapCatQQGateway()
        context = QQMessageContext(
            should_respond=True,
            reason="test",
            is_group=False,
            target_id=QQ_USER_FIXTURE_ID,
            user_id=QQ_USER_FIXTURE_ID,
            session_id=f"qq_pri_{QQ_USER_FIXTURE_ID}",
            profile_user_id=f"qq_{QQ_USER_FIXTURE_ID}",
        )

        with patch("companion_v01.onebot_transport.requests.Session.request") as mocked_post:
            result = gateway.send_emotion_mface(
                context,
                {"speech": "好。", "emotion": "happy"},
                qq_delivery_config={"emotion_mfaces": {"enabled": True, "map": {}}},
            )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "disabled")
        mocked_post.assert_not_called()

    def test_send_emotion_image_uses_onebot_image_segment_and_dedupes(self) -> None:
        gateway = NapCatQQGateway()
        context = QQMessageContext(
            should_respond=True,
            reason="test",
            is_group=True,
            target_id=QQ_GROUP_FIXTURE_ID,
            group_id=QQ_GROUP_FIXTURE_ID,
            session_id=f"qq_group_shared_{QQ_GROUP_FIXTURE_ID}",
            profile_user_id=f"qq_group_shared_{QQ_GROUP_FIXTURE_ID}",
        )

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {"status": "ok"}

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "happy.png"
            image_path.write_bytes(b"png")
            with patch(
                "companion_v01.onebot_transport.requests.Session.request", return_value=FakeResponse()
            ) as mocked_post:
                first = gateway.send_emotion_image(
                    context,
                    {"speech": "好。", "emotion": "happy"},
                    image={"path": str(image_path), "emotion": "happy", "name": "开心"},
                    min_interval_seconds=60,
                )
                second = gateway.send_emotion_image(
                    context,
                    {"speech": "嗯。", "emotion": "happy"},
                    image={"path": str(image_path), "emotion": "happy", "name": "开心"},
                    min_interval_seconds=60,
                )

        self.assertTrue(first["ok"])
        self.assertEqual(first["status"], "sent")
        self.assertEqual(second["status"], "skipped")
        self.assertEqual(second["reason"], "dedupe_interval")
        self.assertEqual(mocked_post.call_count, 1)
        payload = mocked_post.call_args.kwargs["json"]
        self.assertEqual(payload["group_id"], QQ_GROUP_FIXTURE_ID)
        self.assertEqual(next(item for item in payload["message"] if item["type"] == "image")["type"], "image")

    def test_emotion_images_are_suppressed_during_generated_file_delivery(self) -> None:
        gateway = NapCatQQGateway()
        context = QQMessageContext(
            should_respond=True,
            reason="test",
            is_group=False,
            target_id=QQ_USER_FIXTURE_ID,
            user_id=QQ_USER_FIXTURE_ID,
            session_id=f"qq_pri_{QQ_USER_FIXTURE_ID}",
            profile_user_id=f"qq_{QQ_USER_FIXTURE_ID}",
        )

        artifact_frame = {
            "emotion": "happy",
            "tool_events": [{"type": "generated_file_ready", "send_to_user": True}],
        }

        mface_result = gateway.send_emotion_mface(
            context,
            artifact_frame,
            qq_delivery_config={"emotion_mface": {"enabled": True}},
        )
        image_result = gateway.send_emotion_image(context, artifact_frame, image={})

        self.assertEqual(mface_result["reason"], "artifact_delivery_turn")
        self.assertEqual(image_result["reason"], "artifact_delivery_turn")

    def test_current_outfit_id_is_read_from_turn_payload_for_emotion_image_fallback(self) -> None:
        self.assertEqual(
            qq_current_outfit_id_from_turn_payload(
                {
                    "current_visual": {
                        "emotion": "happy",
                        "character": {"outfit": "sailor"},
                    }
                }
            ),
            "sailor",
        )
        self.assertEqual(qq_current_outfit_id_from_turn_payload({"current_visual": {}}), "")

    def test_mface_config_command_extracts_market_face_segment(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": QQ_BOT_FIXTURE_ID,
            "user_id": QQ_MASTER_FIXTURE_ID,
            "message_id": "mface-config-1",
            "message": [
                {"type": "text", "data": {"text": "表情包配置 happy"}},
                {
                    "type": "mface",
                    "data": {
                        "emoji_package_id": 123,
                        "emoji_id": "happy-001",
                        "key": "napcat-key",
                        "summary": "开心",
                    },
                },
            ],
        }
        context = gateway.build_message_context(event)

        result = gateway.handle_mface_config_command(context, event)

        self.assertIsNotNone(result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "captured")
        self.assertEqual(result["emotion"], "happy")
        self.assertEqual(result["mface"]["emoji_id"], "happy-001")
        self.assertIn('"qq_delivery"', result["reply"])

    def test_mface_config_command_extracts_market_face_fields_from_image_segment(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": QQ_BOT_FIXTURE_ID,
            "user_id": QQ_MASTER_FIXTURE_ID,
            "group_id": QQ_GROUP_FIXTURE_ID,
            "message_id": "mface-config-image-1",
            "message": [
                {"type": "at", "data": {"qq": str(QQ_BOT_FIXTURE_ID)}},
                {"type": "text", "data": {"text": " 表情包配置 开心"}},
                {
                    "type": "image",
                    "data": {
                        "file": "market-face.png",
                        "emoji_package_id": "456",
                        "emoji_id": "happy-zh",
                        "key": "image-key",
                        "summary": "开心",
                    },
                },
            ],
        }
        context = gateway.build_message_context(event)

        result = gateway.handle_mface_config_command(context, event)

        self.assertIsNotNone(result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["emotion"], "开心")
        self.assertEqual(result["mface"]["emoji_package_id"], 456)
        self.assertEqual(result["mface"]["key"], "image-key")

    def test_mface_config_command_rejects_non_master(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": QQ_BOT_FIXTURE_ID,
            "user_id": QQ_OTHER_USER_FIXTURE_ID,
            "message_id": "mface-config-forbidden-1",
            "message": [
                {"type": "text", "data": {"text": "表情包配置 happy"}},
                {
                    "type": "mface",
                    "data": {
                        "emoji_package_id": 123,
                        "emoji_id": "happy-001",
                        "key": "napcat-key",
                        "summary": "开心",
                    },
                },
            ],
        }
        context = gateway.build_message_context(event)

        result = gateway.handle_mface_config_command(context, event)

        self.assertIsNotNone(result)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "forbidden")

    def test_extracts_image_and_file_attachments_from_segments(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": QQ_BOT_FIXTURE_ID,
            "user_id": QQ_MASTER_FIXTURE_ID,
            "message_id": "attachment-1",
            "message": [
                {"type": "text", "data": {"text": "看看这个"}},
                {
                    "type": "image",
                    "data": {
                        "file": "dinner.jpg",
                        "url": "http://127.0.0.1:3001/dinner.jpg",
                        "size": "1234",
                    },
                },
                {
                    "type": "file",
                    "data": {
                        "name": "计划.md",
                        "url": "http://127.0.0.1:3001/plan.md",
                    },
                },
            ],
        }

        context = gateway.build_message_context(event)

        self.assertTrue(context.should_respond)
        self.assertEqual(context.clean_message, "看看这个 [图片] [文件]")
        self.assertEqual(len(context.attachments or []), 2)
        image, document = context.attachments or []
        self.assertEqual(image["kind"], "image")
        self.assertEqual(image["file"], "dinner.jpg")
        self.assertEqual(image["origin_name"], "dinner.jpg")
        self.assertEqual(image["file_size"], 1234)
        self.assertEqual(document["kind"], "document")
        self.assertEqual(document["file"], "计划.md")
        self.assertEqual(document["origin_name"], "计划.md")

    def test_extracts_raw_cq_attachment_fallbacks(self) -> None:
        gateway = NapCatQQGateway()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": QQ_BOT_FIXTURE_ID,
            "user_id": QQ_MASTER_FIXTURE_ID,
            "message_id": "attachment-raw-1",
            "raw_message": "[CQ:image,file=cat.png,url=http://127.0.0.1/cat.png]",
        }

        context = gateway.build_message_context(event)

        self.assertTrue(context.should_respond)
        self.assertEqual(context.clean_message, "发来了一张图片。")
        self.assertEqual(len(context.attachments or []), 1)
        self.assertEqual((context.attachments or [])[0]["origin_name"], "cat.png")

    @patch("companion_v01.qq_gateway.config.QQ_ATTACHMENT_DEBOUNCE_SECONDS", 1.2)
    def test_attachment_debounce_only_latest_event_processes(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_MASTER_FIXTURE_ID,
                "message_id": "debounce-1",
                "raw_message": "[CQ:image,file=one.png,url=http://127.0.0.1/one.png]",
            }
        )

        first = gateway.register_attachment_debounce(context, attachment_ids=["attachment::1"])
        second = gateway.register_attachment_debounce(context, attachment_ids=["attachment::2"])

        self.assertFalse(gateway.consume_attachment_debounce(first)["process"])
        latest = gateway.consume_attachment_debounce(second)
        self.assertTrue(latest["process"])
        self.assertEqual(latest["attachment_ids"], ["attachment::1", "attachment::2"])

    @patch("companion_v01.qq_gateway.config.QQ_ATTACHMENT_DEBOUNCE_SECONDS", 0.0)
    def test_attachment_debounce_can_be_disabled(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_MASTER_FIXTURE_ID,
                "message_id": "debounce-disabled-1",
                "raw_message": "[CQ:image,file=one.png,url=http://127.0.0.1/one.png]",
            }
        )

        token = gateway.register_attachment_debounce(context, attachment_ids=["attachment::1"])

        self.assertFalse(token["enabled"])
        self.assertTrue(token["process"])
        self.assertEqual(token["attachment_ids"], ["attachment::1"])

    def test_send_generated_files_uses_onebot_upload_action(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_MASTER_FIXTURE_ID,
                "message_id": "generated-send-1",
                "raw_message": "发我文件",
            }
        )

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {"status": "ok"}

        with patch(
            "companion_v01.onebot_transport.requests.Session.request", return_value=FakeResponse()
        ) as mocked_post:
            result = gateway.send_generated_files(
                context,
                [
                    {
                        "type": "generated_file_ready",
                        "send_to_user": True,
                        "generated_file": {
                            "generated_id": "generated::1",
                            "absolute_path": "C:/tmp/akane.md",
                            "output_title": "Akane整理",
                            "file_ext": "md",
                        },
                    }
                ],
            )

        self.assertTrue(result["ok"])
        mocked_post.assert_called_once()
        url = mocked_post.call_args.args[1]
        payload = mocked_post.call_args.kwargs["json"]
        self.assertTrue(url.endswith("/upload_private_file"))
        self.assertEqual(payload["user_id"], QQ_MASTER_FIXTURE_ID)
        self.assertEqual(payload["file"], "C:/tmp/akane.md")
        self.assertEqual(payload["name"], "Akane整理.md")

    def test_send_generated_image_uses_onebot_image_message(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_MASTER_FIXTURE_ID,
                "message_id": "generated-image-send-1",
                "raw_message": "生成一张图片发给我",
            }
        )

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {"status": "ok"}

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "generated.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
            with patch(
                "companion_v01.onebot_transport.requests.Session.request", return_value=FakeResponse()
            ) as mocked_post:
                result = gateway.send_generated_files(
                    context,
                    [
                        {
                            "type": "generated_file_ready",
                            "send_to_user": True,
                            "generated_file": {
                                "generated_id": "generated::image-1",
                                "absolute_path": str(image_path),
                                "output_title": "生成图片",
                                "file_ext": "png",
                                "mime_type": "image/png",
                            },
                        }
                    ],
                )

        self.assertTrue(result["ok"])
        mocked_post.assert_called_once()
        url = mocked_post.call_args.args[1]
        payload = mocked_post.call_args.kwargs["json"]
        self.assertTrue(url.endswith("/send_private_msg"))
        self.assertEqual(payload["user_id"], QQ_MASTER_FIXTURE_ID)
        self.assertEqual(payload["message"][0], {"type": "reply", "data": {"id": "generated-image-send-1"}})
        self.assertEqual(payload["message"][1]["type"], "image")

    def test_send_generated_files_trusts_current_structured_delivery_event(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_MASTER_FIXTURE_ID,
                "message_id": "generated-structured-send-1",
                "raw_message": "啊这，我图呢",
            }
        )

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {"status": "ok"}

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "reimu.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
            with patch(
                "companion_v01.onebot_transport.requests.Session.request", return_value=FakeResponse()
            ) as mocked_post:
                result = gateway.send_generated_files(
                    context,
                    [
                        {
                            "type": "generated_file_ready",
                            "send_to_user": True,
                            "client_mode": "qq_text",
                            "generated_file": {
                                "generated_id": "generated::current",
                                "absolute_path": str(image_path),
                                "output_title": "博丽灵梦_神社傍晚",
                                "file_ext": "png",
                                "mime_type": "image/png",
                            },
                        }
                    ],
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["count"], 1)
        mocked_post.assert_called_once()

    def test_send_generated_files_requires_structured_send_flag(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_MASTER_FIXTURE_ID,
                "message_id": "generated-no-send-1",
                "raw_message": "把图发我",
            }
        )

        with patch("companion_v01.onebot_transport.requests.Session.request") as mocked_post:
            result = gateway.send_generated_files(
                context,
                [
                    {
                        "type": "generated_file_ready",
                        "send_to_user": False,
                        "client_mode": "qq_text",
                        "generated_file": {
                            "generated_id": "generated::not-selected",
                            "absolute_path": "C:/tmp/not-selected.png",
                            "output_title": "未选择图片",
                            "file_ext": "png",
                            "mime_type": "image/png",
                        },
                    }
                ],
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        mocked_post.assert_not_called()

    def test_send_generated_files_accepts_generic_file_ready_event(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "group",
                "self_id": QQ_BOT_FIXTURE_ID,
                "group_id": QQ_FILE_GROUP_FIXTURE_ID,
                "user_id": QQ_MASTER_FIXTURE_ID,
                "message_id": "file-ready-1",
                "message": [
                    {"type": "at", "data": {"qq": str(QQ_BOT_FIXTURE_ID)}},
                    {"type": "text", "data": {"text": " 发我文件"}},
                ],
            }
        )

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {"status": "ok"}

        with patch(
            "companion_v01.onebot_transport.requests.Session.request", return_value=FakeResponse()
        ) as mocked_post:
            result = gateway.send_generated_files(
                context,
                [
                    {
                        "type": "file_ready",
                        "send_to_user": True,
                        "file": {
                            "source_type": "attachment",
                            "source_id": "attachment::1",
                            "absolute_path": "C:/tmp/video.mp4",
                            "name": "video.mp4",
                        },
                    }
                ],
            )

        self.assertTrue(result["ok"])
        mocked_post.assert_called_once()
        url = mocked_post.call_args.args[1]
        payload = mocked_post.call_args.kwargs["json"]
        self.assertTrue(url.endswith("/upload_group_file"))
        self.assertEqual(payload["group_id"], QQ_FILE_GROUP_FIXTURE_ID)
        self.assertEqual(payload["file"], "C:/tmp/video.mp4")
        self.assertEqual(payload["name"], "video.mp4")

    def test_send_generated_files_ignores_desktop_client_file_events(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_MASTER_FIXTURE_ID,
                "message_id": "file-ready-desktop-1",
                "raw_message": "发我文件",
            }
        )

        with patch("companion_v01.onebot_transport.requests.Session.request") as mocked_post:
            result = gateway.send_generated_files(
                context,
                [
                    {
                        "type": "file_ready",
                        "client_mode": "desktop_pet",
                        "send_to_user": True,
                        "delivery_action": "save_desktop",
                        "desktop_delivery": {
                            "action": "save_desktop",
                            "path": "C:/tmp/video.mp4",
                            "name": "video.mp4",
                        },
                        "file": {
                            "source_type": "attachment",
                            "source_id": "attachment::1",
                            "absolute_path": "C:/tmp/video.mp4",
                            "name": "video.mp4",
                        },
                    }
                ],
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        mocked_post.assert_not_called()


class QQGatewaySelfCheckTests(unittest.TestCase):
    """self_check() 方法的结构化诊断测试，不依赖真实 NapCat 服务。"""

    def setUp(self) -> None:
        self.master_qq_patcher = patch(
            "companion_v01.qq_gateway.config.MASTER_QQ",
            str(QQ_MASTER_FIXTURE_ID),
        )
        self.bot_qq_patcher = patch(
            "companion_v01.qq_gateway.config.QQ_BOT_QQ",
            str(QQ_BOT_FIXTURE_ID),
        )
        self.master_qq_patcher.start()
        self.bot_qq_patcher.start()
        self.addCleanup(self.master_qq_patcher.stop)
        self.addCleanup(self.bot_qq_patcher.stop)

    @patch("companion_v01.qq_gateway.config.QQ_BRIDGE_ENABLED", False)
    def test_self_check_returns_bridge_disabled_when_not_enabled(self) -> None:
        gateway = NapCatQQGateway()
        result = gateway.self_check()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "bridge_disabled")
        self.assertIn("QQ_BRIDGE_ENABLED", result["reason"])
        self.assertNotIn("token", result.get("reason", "").lower())

    @patch("companion_v01.qq_gateway.config.QQ_BRIDGE_ENABLED", True)
    @patch("companion_v01.qq_gateway.config.QQ_ONEBOT_HTTP_URL", "not-a-url")
    def test_self_check_returns_invalid_url_for_bad_format(self) -> None:
        gateway = NapCatQQGateway()
        result = gateway.self_check()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "invalid_url")

    @patch("companion_v01.qq_gateway.config.QQ_BRIDGE_ENABLED", True)
    @patch("companion_v01.qq_gateway.config.QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:3001")
    def test_self_check_returns_unreachable_on_connection_error(self) -> None:
        import requests as req_module

        gateway = NapCatQQGateway()
        with patch(
            "companion_v01.onebot_transport.requests.Session.request",
            side_effect=req_module.exceptions.ConnectionError("refused"),
        ):
            result = gateway.self_check()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "unreachable")
        self.assertIn("端口", result["reason"])

    @patch("companion_v01.qq_gateway.config.QQ_BRIDGE_ENABLED", True)
    @patch("companion_v01.qq_gateway.config.QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:3001")
    def test_self_check_returns_timeout_on_request_timeout(self) -> None:
        import requests as req_module

        gateway = NapCatQQGateway()
        with patch(
            "companion_v01.onebot_transport.requests.Session.request",
            side_effect=req_module.exceptions.Timeout("timed out"),
        ):
            result = gateway.self_check()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "timeout")

    @patch("companion_v01.qq_gateway.config.QQ_BRIDGE_ENABLED", True)
    @patch("companion_v01.qq_gateway.config.QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:3001")
    def test_self_check_returns_auth_failed_on_401(self) -> None:
        gateway = NapCatQQGateway()

        class FakeResponse:
            status_code = 401

            def raise_for_status(self):
                pass

            def json(self):
                return {}

        with patch("companion_v01.onebot_transport.requests.Session.request", return_value=FakeResponse()):
            result = gateway.self_check()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "auth_failed")
        self.assertIn("鉴权", result["reason"])

    @patch("companion_v01.qq_gateway.config.QQ_BRIDGE_ENABLED", True)
    @patch("companion_v01.qq_gateway.config.QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:3001")
    def test_self_check_returns_connected_on_success(self) -> None:
        gateway = NapCatQQGateway()

        class LoginInfoResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"user_id": 12345678, "nickname": "阿卡内测试号"},
                }

        class StatusResponse:
            status_code = 200

            def json(self):
                return {"status": "ok", "retcode": 0, "data": {"online": True, "good": True}}

        with patch(
            "companion_v01.onebot_transport.requests.Session.request",
            side_effect=[LoginInfoResponse(), StatusResponse()],
        ) as mocked_get:
            result = gateway.self_check()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "connected")
        self.assertEqual(result["bot_qq"], "12345678")
        self.assertEqual(result["nickname"], "阿卡内测试号")
        self.assertTrue(result["checks"]["bridge_enabled"])
        self.assertTrue(result["checks"]["url_reachable"])
        self.assertTrue(result["checks"]["login_info"])
        self.assertTrue(result["checks"]["account_online"])
        self.assertEqual(result["checks"]["send_test"], "not_tested")
        self.assertEqual(mocked_get.call_count, 2)
        self.assertTrue(mocked_get.call_args_list[1].args[1].endswith("/get_status"))
        # 不能暴露 token/cookie/path
        result_str = str(result)
        self.assertNotIn("token", result_str.lower())
        self.assertNotIn("cookie", result_str.lower())

    @patch("companion_v01.qq_gateway.config.QQ_BRIDGE_ENABLED", True)
    @patch("companion_v01.qq_gateway.config.QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:3001")
    def test_self_check_rejects_cached_login_info_when_account_is_offline(self) -> None:
        gateway = NapCatQQGateway()

        class LoginInfoResponse:
            status_code = 200

            def json(self):
                return {"status": "ok", "retcode": 0, "data": {"user_id": 12345678, "nickname": "缓存账号"}}

        class StatusResponse:
            status_code = 200

            def json(self):
                return {"status": "ok", "retcode": 0, "data": {"online": False, "good": True}}

        with patch(
            "companion_v01.onebot_transport.requests.Session.request",
            side_effect=[LoginInfoResponse(), StatusResponse()],
        ):
            result = gateway.self_check()

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "account_offline")
        self.assertNotIn("12345678", str(result))

    @patch("companion_v01.qq_gateway.config.QQ_BRIDGE_ENABLED", True)
    @patch("companion_v01.qq_gateway.config.QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:3001")
    def test_self_check_does_not_expose_sensitive_fields(self) -> None:
        """self_check 结果只暴露安全字段（user_id、nickname），不包含 token / cookie / 路径。"""
        gateway = NapCatQQGateway()

        class LoginInfoResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "status": "ok",
                    "retcode": 0,
                    "data": {
                        "user_id": 99999,
                        "nickname": "test",
                        "token": "should-not-leak",
                        "cookie": "also-secret",
                    },
                }

        class StatusResponse:
            status_code = 200

            def json(self):
                return {"status": "ok", "retcode": 0, "data": {"online": True, "good": True}}

        with patch(
            "companion_v01.onebot_transport.requests.Session.request",
            side_effect=[LoginInfoResponse(), StatusResponse()],
        ):
            result = gateway.self_check()
        result_str = str(result)
        self.assertNotIn("should-not-leak", result_str)
        self.assertNotIn("also-secret", result_str)


if __name__ == "__main__":
    unittest.main()
