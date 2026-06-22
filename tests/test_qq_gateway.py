from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from companion_v01.qq_gateway import NapCatQQGateway, QQMessageContext


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

        self.assertEqual(messages, ["我先说一句。", "代码在这里。\n\nprint('hi')"])

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
        self.assertEqual(follow.reason, "group_message_without_mention")

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
        self.assertEqual(plain_text.reason, "group_message_without_mention")
        self.assertFalse(other_user_image.should_respond)
        self.assertEqual(other_user_image.reason, "group_message_without_mention")

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
        self.assertEqual(payload["message"], f"刚才发生的互动：QQ {QQ_USER_FIXTURE_ID}在 QQ 里戳了戳你的头像。")
        self.assertNotIn("transient_user_message", payload)
        self.assertEqual(payload["client_mode"], "qq_text")
        self.assertIn(f"QQ {QQ_USER_FIXTURE_ID}", payload["extra_context"])
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
                return {"retcode": 0, "data": {"card": "休比", "nickname": "fallback"}}

        with patch("companion_v01.qq_gateway.requests.post", return_value=FakeResponse()) as mocked_post:
            context = gateway.build_message_context(event)

        payload = context.to_turn_payload()

        self.assertTrue(context.should_respond)
        self.assertEqual(context.sender_label, "休比")
        self.assertEqual(payload["message"], "【休比】刚才发生的互动：休比在 QQ 里戳了戳你的头像。")
        mocked_post.assert_called_once()
        self.assertTrue(mocked_post.call_args.args[0].endswith("/get_group_member_info"))
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
        self.assertEqual(payload["message"], f"刚才发生的互动：QQ {QQ_USER_FIXTURE_ID}在 QQ 里戳了戳你的头像。")

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
        self.assertIsNotNone(invalid)
        self.assertFalse(invalid["ok"])
        self.assertEqual(invalid["status"], "invalid_character_pack_id")

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
            with patch("companion_v01.qq_gateway.requests.post", return_value=FakeResponse()) as mocked_post:
                result = gateway.send_voice(context, audio_path=str(audio_path), name="reply")

        self.assertTrue(result["ok"])
        payload = mocked_post.call_args.kwargs["json"]
        self.assertEqual(payload["user_id"], QQ_USER_FIXTURE_ID)
        self.assertEqual(payload["message"][0]["type"], "record")
        self.assertIn("file", payload["message"][0]["data"])

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

        with patch("companion_v01.qq_gateway.requests.post", return_value=FakeResponse()) as mocked_post:
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

        with patch("companion_v01.qq_gateway.requests.post", return_value=FakeResponse()) as mocked_post:
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

        with patch("companion_v01.qq_gateway.requests.post") as mocked_post:
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
            with patch("companion_v01.qq_gateway.requests.post", return_value=FakeResponse()) as mocked_post:
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
        self.assertEqual(payload["message"][0]["type"], "image")

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

        with patch("companion_v01.qq_gateway.requests.post", return_value=FakeResponse()) as mocked_post:
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
        url = mocked_post.call_args.args[0]
        payload = mocked_post.call_args.kwargs["json"]
        self.assertTrue(url.endswith("/upload_private_file"))
        self.assertEqual(payload["user_id"], QQ_MASTER_FIXTURE_ID)
        self.assertEqual(payload["file"], "C:/tmp/akane.md")
        self.assertEqual(payload["name"], "Akane整理.md")

    @patch("companion_v01.qq_gateway.config.QQ_REQUIRE_FILE_DELIVERY_INTENT", True)
    def test_send_generated_files_blocks_without_current_delivery_intent(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_MASTER_FIXTURE_ID,
                "message_id": "generated-block-1",
                "raw_message": "在吗",
            }
        )

        with patch("companion_v01.qq_gateway.requests.post") as mocked_post:
            result = gateway.send_generated_files(
                context,
                [
                    {
                        "type": "generated_file_ready",
                        "send_to_user": True,
                        "generated_file": {
                            "generated_id": "generated::old",
                            "absolute_path": "C:/tmp/old.md",
                            "output_title": "旧文件",
                            "file_ext": "md",
                        },
                    }
                ],
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["blocked_count"], 1)
        self.assertEqual(result["reason"], "missing_file_delivery_intent")
        mocked_post.assert_not_called()

    @patch("companion_v01.qq_gateway.config.QQ_REQUIRE_FILE_DELIVERY_INTENT", True)
    def test_send_generated_files_allows_current_generated_file_without_repeated_delivery_phrase(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_FIXTURE_ID,
                "user_id": QQ_MASTER_FIXTURE_ID,
                "message_id": "generated-current-1",
                "raw_message": "嗯？好了吗",
            }
        )

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {"status": "ok"}

        with patch("companion_v01.qq_gateway.requests.post", return_value=FakeResponse()) as mocked_post:
            result = gateway.send_generated_files(
                context,
                [
                    {
                        "type": "generated_file_ready",
                        "send_to_user": True,
                        "generated_file": {
                            "generated_id": "generated::1",
                            "absolute_path": "C:/tmp/story.md",
                            "output_title": "会说话的猫和它的室友",
                            "file_ext": "md",
                            "created_by_tool": "compose_file",
                            "delivery_status": "pending",
                        },
                    }
                ],
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        mocked_post.assert_called_once()
        payload = mocked_post.call_args.kwargs["json"]
        self.assertEqual(payload["file"], "C:/tmp/story.md")
        self.assertEqual(payload["name"], "会说话的猫和它的室友.md")

    def test_file_delivery_intent_respects_negative_request(self) -> None:
        gateway = NapCatQQGateway()

        self.assertTrue(gateway.message_requests_file_delivery("把 gen_001 发我一下"))
        self.assertFalse(gateway.message_requests_file_delivery("先别发文件，我只是问问进度"))

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

        with patch("companion_v01.qq_gateway.requests.post", return_value=FakeResponse()) as mocked_post:
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
        url = mocked_post.call_args.args[0]
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

        with patch("companion_v01.qq_gateway.requests.post") as mocked_post:
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
        with patch("companion_v01.qq_gateway.requests.get",
                   side_effect=req_module.exceptions.ConnectionError("refused")):
            result = gateway.self_check()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "unreachable")
        self.assertIn("端口", result["reason"])

    @patch("companion_v01.qq_gateway.config.QQ_BRIDGE_ENABLED", True)
    @patch("companion_v01.qq_gateway.config.QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:3001")
    def test_self_check_returns_timeout_on_request_timeout(self) -> None:
        import requests as req_module
        gateway = NapCatQQGateway()
        with patch("companion_v01.qq_gateway.requests.get",
                   side_effect=req_module.exceptions.Timeout("timed out")):
            result = gateway.self_check()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "timeout")

    @patch("companion_v01.qq_gateway.config.QQ_BRIDGE_ENABLED", True)
    @patch("companion_v01.qq_gateway.config.QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:3001")
    def test_self_check_returns_auth_failed_on_401(self) -> None:
        gateway = NapCatQQGateway()

        class FakeResponse:
            status_code = 401
            def raise_for_status(self): pass
            def json(self): return {}

        with patch("companion_v01.qq_gateway.requests.get", return_value=FakeResponse()):
            result = gateway.self_check()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "auth_failed")
        self.assertIn("鉴权", result["reason"])

    @patch("companion_v01.qq_gateway.config.QQ_BRIDGE_ENABLED", True)
    @patch("companion_v01.qq_gateway.config.QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:3001")
    def test_self_check_returns_connected_on_success(self) -> None:
        gateway = NapCatQQGateway()

        class FakeResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {
                    "retcode": 0,
                    "data": {"user_id": 12345678, "nickname": "阿卡内测试号"},
                }

        with patch("companion_v01.qq_gateway.requests.get", return_value=FakeResponse()):
            result = gateway.self_check()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "connected")
        self.assertEqual(result["bot_qq"], "12345678")
        self.assertEqual(result["nickname"], "阿卡内测试号")
        self.assertTrue(result["checks"]["bridge_enabled"])
        self.assertTrue(result["checks"]["url_reachable"])
        self.assertTrue(result["checks"]["login_info"])
        self.assertEqual(result["checks"]["send_test"], "not_tested")
        # 不能暴露 token/cookie/path
        result_str = str(result)
        self.assertNotIn("token", result_str.lower())
        self.assertNotIn("cookie", result_str.lower())

    @patch("companion_v01.qq_gateway.config.QQ_BRIDGE_ENABLED", True)
    @patch("companion_v01.qq_gateway.config.QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:3001")
    def test_self_check_does_not_expose_sensitive_fields(self) -> None:
        """self_check 结果只暴露安全字段（user_id、nickname），不包含 token / cookie / 路径。"""
        gateway = NapCatQQGateway()

        class FakeResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {
                    "retcode": 0,
                    "data": {
                        "user_id": 99999,
                        "nickname": "test",
                        "token": "should-not-leak",
                        "cookie": "also-secret",
                    },
                }

        with patch("companion_v01.qq_gateway.requests.get", return_value=FakeResponse()):
            result = gateway.self_check()
        result_str = str(result)
        self.assertNotIn("should-not-leak", result_str)
        self.assertNotIn("also-secret", result_str)


if __name__ == "__main__":
    unittest.main()
