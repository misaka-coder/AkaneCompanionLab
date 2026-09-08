from types import SimpleNamespace
import unittest
from unittest.mock import patch

from companion_v01.bot_profile import bot_config_from_instance_context
from companion_v01.deployment_security import QQChannelRuntimeConfig
from companion_v01.qq_gateway import NapCatQQGateway


class QQNicknameAddressingTests(unittest.TestCase):
    def gateway(self, words=()):
        gateway = NapCatQQGateway(
            wake_words=words,
            channel_config=QQChannelRuntimeConfig(
                enabled=True,
                profile_ref="test",
                bot_id="10001",
                onebot_http_url="http://127.0.0.1:1",
                webhook_secret="",
                onebot_access_token="",
                require_webhook_auth=False,
                require_self_id=True,
            ),
        )
        self.login = SimpleNamespace(ok=True, code="ok", data={"user_id": 10001, "nickname": "塞西莉亚"})
        self.transport = self.enterContext(patch.object(gateway._onebot_transport, "call", return_value=self.login))
        return gateway

    def event(self, text, *, targets=(), message_id="1"):
        return {
            "post_type": "message",
            "message_type": "group",
            "self_id": "10001",
            "user_id": "20002",
            "group_id": "30003",
            "message_id": message_id,
            "sender": {"nickname": "用户"},
            "message": [{"type": "at", "data": {"qq": target, "name": "Akane"}} for target in targets]
            + [{"type": "text", "data": {"text": text}}],
        }

    def test_legacy_adapter_does_not_supply_fixed_wake_word(self):
        self.assertEqual(bot_config_from_instance_context(SimpleNamespace(instance_id="test")).wake_words, ())

    def test_group_nickname_and_product_name_do_not_wake(self):
        gateway = self.gateway()
        self.assertEqual(gateway.build_message_context(self.event("塞西莉亚，在吗")).reason, "group_passive_observed")
        self.assertFalse(gateway.build_message_context(self.event("Akane 在吗", message_id="2")).should_respond)
        self.assertEqual(self.transport.call_count, 1)
        self.assertEqual(gateway._strip_wake_word_command_prefix("塞西莉亚，当前角色"), "当前角色")

    def test_other_mention_is_passive_and_self_projection_uses_verified_nickname(self):
        gateway = self.gateway()
        other = gateway.build_message_context(self.event("塞西莉亚 在吗", targets=("40004",)))
        self.assertFalse(other.should_respond)
        self.assertEqual(other.to_turn_payload()["message_addressing"]["primary_target"]["actor_id"], "qq:40004")
        own = gateway.build_message_context(self.event("你好", targets=("10001", "40004"), message_id="2"))
        self.assertEqual(own.reason, "group_mention")
        self.assertIn("@塞西莉亚", own.to_turn_payload()["memory_message"])
        self.assertNotIn("本群昵称", own.to_turn_payload()["memory_message"])
        self.assertTrue(own.to_turn_payload()["message_addressing"]["explicit_assistant_mention"])

    def test_failed_lookup_backs_off_and_keeps_explicit_mentions(self):
        gateway = self.gateway()
        self.transport.return_value = SimpleNamespace(ok=False, code="timeout", data={})
        self.assertFalse(gateway.build_message_context(self.event("Akane 在吗")).should_respond)
        own = gateway.build_message_context(self.event("你好", targets=("10001",), message_id="2"))
        self.assertTrue(own.should_respond)
        self.assertIn("@助手", own.to_turn_payload()["memory_message"])
        self.assertEqual(self.transport.call_count, 2)
        self.assertEqual(gateway._bot_nickname_status, "timeout")

    def test_group_card_and_self_identity_reach_turn_payload(self):
        gateway = self.gateway()
        self.transport.side_effect = [
            SimpleNamespace(
                ok=True, data={"user_id": 10001, "group_id": 30003, "card": "天为", "nickname": "山城高岭"}
            ),
            self.login,
        ]
        context = gateway.build_message_context(self.event("？", targets=("10001",)))
        payload = context.to_turn_payload()
        self.assertEqual(context.reason, "group_mention")
        self.assertEqual(payload["memory_message"], "@天为 ？")
        addressing = payload["message_addressing"]
        self.assertTrue(addressing["explicit_assistant_mention"])
        self.assertEqual(addressing["primary_target"], {"actor_id": "assistant", "display_name": ""})
        self.assertEqual(addressing["mentions"][0]["display_name"], "天为")
        self.assertTrue(addressing["mentions"][0]["is_assistant"])
        self.assertEqual(gateway._project_mention_evidence(context.mentions)[0]["display_name"], "天为")
        self.assertIn("你在本群的显示名：天为", gateway.build_group_identity_context(30003))
        self.assertNotIn("山城高岭", gateway.build_group_identity_context(30003))
        self.assertEqual(self.transport.call_count, 2)

    def test_group_cards_are_isolated_refreshed_and_identity_checked(self):
        gateway = self.gateway()
        member = SimpleNamespace(ok=True, data={"user_id": 10001, "group_id": 30003, "card": "天为"})
        self.transport.return_value = member
        self.assertEqual(gateway._resolve_bot_group_label(30003), "天为")
        self.assertEqual(gateway._resolve_bot_group_label(30003), "天为")
        self.assertEqual(self.transport.call_count, 1)
        member.data.update(group_id=30004, card="第二群")
        self.assertEqual(gateway._resolve_bot_group_label(30004), "第二群")
        self.assertEqual(gateway._resolve_bot_group_label(30003), "天为")
        gateway._bot_group_labels[("10001", 30003)] = (0, "天为")
        member.data.update(group_id=30003, card="新名片")
        self.assertEqual(gateway._resolve_bot_group_label(30003), "新名片")
        for invalid in (
            {"user_id": 40004, "group_id": 30003, "card": "冒名"},
            {"user_id": 10001, "group_id": 30004, "card": "跨群"},
            {"user_id": 10001, "card": "bad\ncard"},
        ):
            gateway._bot_group_labels[("10001", 30003)] = (0, "新名片")
            self.transport.return_value = SimpleNamespace(ok=True, data=invalid)
            self.assertEqual(gateway._resolve_bot_group_label(30003), "")
        self.transport.side_effect = None

    def test_empty_card_falls_back_and_failed_refresh_never_keeps_old_card(self):
        gateway = self.gateway()
        self.transport.return_value = SimpleNamespace(
            ok=True, data={"user_id": 10001, "card": "", "nickname": "账号昵称"}
        )
        self.assertEqual(gateway._resolve_bot_group_label(30003), "账号昵称")
        gateway._bot_group_labels[("10001", 30003)] = (0, "账号昵称")
        self.transport.return_value = SimpleNamespace(ok=False, data={})
        self.assertEqual(gateway._resolve_bot_group_label(30003), "")
        self.assertEqual(gateway._resolve_bot_group_label(30003), "")
        self.assertEqual(self.transport.call_count, 2)

    def test_nickname_refresh_and_account_mismatch_do_not_keep_stale_alias(self):
        gateway = self.gateway()
        self.assertEqual(gateway._effective_command_prefixes(), ("塞西莉亚",))
        self.login.data["nickname"] = "新昵称"
        gateway._bot_nickname_refresh_at = 0
        self.assertEqual(gateway._effective_command_prefixes(), ("新昵称",))
        self.assertEqual(gateway._strip_wake_word_command_prefix("塞西莉亚，当前角色"), "塞西莉亚，当前角色")
        self.login.data["user_id"] = 40004
        gateway._bot_nickname_refresh_at = 0
        self.assertEqual(gateway._effective_command_prefixes(), ())
        self.assertEqual(gateway._bot_nickname_status, "account_identity_mismatch")

    def test_configured_aliases_only_supply_command_prefixes(self):
        gateway = self.gateway(("小塞",))
        self.assertEqual(gateway._strip_wake_word_command_prefix("小塞，当前角色"), "当前角色")
        self.assertEqual(gateway._strip_wake_word_command_prefix("塞西莉亚，当前角色"), "塞西莉亚，当前角色")
        self.assertFalse(gateway.build_message_context(self.event("小塞 在吗")).should_respond)
        self.transport.assert_not_called()

    def test_invalid_nickname_never_becomes_a_command_prefix(self):
        for nickname in ("", "x" * 33, "bad\nname"):
            gateway = self.gateway()
            self.login.data["nickname"] = nickname
            self.assertEqual(gateway._effective_command_prefixes(), ())
            self.assertEqual(gateway._bot_nickname_status, "invalid_login_nickname")

    def test_self_check_reports_text_wake_disabled(self):
        gateway = self.gateway()
        self.transport.side_effect = [self.login, SimpleNamespace(ok=True, data={"online": True})]
        result = gateway.self_check()
        self.assertTrue(result["ok"])
        self.assertEqual(result["wake_word_mode"], "disabled")
        self.assertEqual(result["wake_words"], [])
        self.assertEqual(result["wake_word_status"], "disabled")
        self.assertEqual(self.transport.call_count, 2)
