from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.deployment_security import QQChannelRuntimeConfig
from companion_v01.channels.qq.gateway import NapCatQQGateway
from companion_v01.routes.qq import build_qq_router


class QQGroupNoticeTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(NapCatQQGateway, "_resolve_bot_nickname", return_value="助手"))
        self.channel = QQChannelRuntimeConfig(
            enabled=True, profile_ref="qq.test", bot_id="101", onebot_http_url="http://127.0.0.1:3001",
            webhook_secret="", onebot_access_token="", require_webhook_auth=False, require_self_id=True,
        )

    def gateway(self):
        gateway = NapCatQQGateway(channel_config=self.channel)
        for user_id, label in ((404, "甲"), (405, "乙")):
            key = gateway._sender_label_cache_key(group_id=303, user_id=user_id)
            gateway.sender_label_cache[key] = label
        return gateway

    def event(self, **overrides):
        return {
            "post_type": "notice", "notice_type": "notify", "sub_type": "poke", "self_id": 101,
            "group_id": 303, "user_id": 404, "target_id": 405, "time": int(time.time()), **overrides,
        }

    def test_member_pokes_are_observed_with_both_participants_and_no_forced_reply(self):
        for variant in ({}, {"notice_type": "poke"}, {"sub_type": "", "notice_sub_type": "poke"}):
            with self.subTest(variant=variant):
                context = self.gateway().build_message_context(self.event(**variant))
                payload = context.to_turn_payload()
                self.assertFalse(context.should_respond)
                self.assertTrue(context.should_record)
                self.assertEqual(context.reason, "group_poke_observed")
                self.assertEqual(context.session_id, "qq_group_shared_303")
                self.assertEqual(payload["memory_message"], "QQ互动：甲（QQ 404）戳了戳乙（QQ 405）。")
                self.assertEqual(payload["actor_stable_id"], "qq:404")
                self.assertEqual(payload["actor_display_name"], "甲")
                self.assertEqual(payload["message_addressing"]["mode"], "observed")
                self.assertFalse(payload["message_addressing"]["addressed_to_assistant"])
                self.assertNotIn("戳了戳你", payload["extra_context"])

    def test_operator_identity_wins_over_target_user_id_and_target_sender_card(self):
        event = self.event(operator_id=404, user_id=405, sender={"user_id": 405, "card": "乙"})
        context = self.gateway().build_message_context(event)
        self.assertTrue(context.should_record)
        self.assertEqual(context.user_id, 404)
        self.assertEqual(context.sender_label, "甲")
        self.assertEqual(context.to_turn_payload()["memory_message"], "QQ互动：甲（QQ 404）戳了戳乙（QQ 405）。")

    def test_two_operators_poking_same_target_in_one_second_are_distinct_events(self):
        gateway = self.gateway()
        gateway.sender_label_cache[gateway._sender_label_cache_key(group_id=303, user_id=406)] = "丙"
        event = self.event(operator_id=404, user_id=405)
        first = gateway.build_message_context(event)
        second = gateway.build_message_context({**event, "operator_id": 406})
        self.assertTrue(first.should_record)
        self.assertTrue(second.should_record)
        self.assertNotEqual(first.source_message_id, second.source_message_id)
        self.assertIn("丙（QQ 406）戳了戳乙", second.clean_message)

    def test_poke_admission_keeps_identity_freshness_and_duplicate_guards(self):
        gateway = self.gateway()
        event = self.event()
        self.assertTrue(gateway.build_message_context(event).should_record)
        duplicate = gateway.build_message_context(deepcopy(event))
        self.assertFalse(duplicate.should_record)
        self.assertEqual(duplicate.reason, "duplicate_event")
        for overrides, reason in (
            ({"self_id": 999}, "qq_self_id_mismatch"),
            ({"time": int(time.time()) - 100000}, "stale_event"),
            ({"user_id": 101}, "self_message"),
        ):
            with self.subTest(reason=reason):
                context = self.gateway().build_message_context(self.event(**overrides))
                self.assertFalse(context.should_record)
                self.assertFalse(context.should_respond)
                self.assertEqual(context.reason, reason)

    def test_non_group_or_incomplete_notices_do_not_create_passive_memory(self):
        for overrides in (
            {"group_id": 0}, {"target_id": 0}, {"user_id": 0},
            {"notice_type": "group_admin", "sub_type": "set"},
        ):
            with self.subTest(overrides=overrides):
                context = self.gateway().build_message_context(self.event(**overrides))
                self.assertFalse(context.should_respond)
                self.assertFalse(context.should_record)

    def test_queued_notice_restores_participants_after_restart_without_readmission(self):
        event = self.event()
        original = self.gateway().build_message_context(event)
        restored_gateway = self.gateway()
        self.assertTrue(restored_gateway.build_message_context(event).should_record)
        with patch("time.time", return_value=event["time"] + 86400):
            restored = restored_gateway.restore_admitted_passive_context(event, original.to_delivery_context())
        self.assertTrue(restored.should_record)
        self.assertFalse(restored.should_respond)
        self.assertEqual(restored.to_turn_payload()["memory_message"], original.to_turn_payload()["memory_message"])
        self.assertEqual(restored.inbound_message.actor.id, "404")
        for changes in ({"user_id": 406}, {"target_id": 406}):
            with self.assertRaisesRegex(ValueError, "queued_context_source_mismatch"):
                restored_gateway.restore_admitted_passive_context({**event, **changes}, original.to_delivery_context())

    def test_bot_pokes_keep_resolved_interaction_in_memory_and_normal_reply_behavior(self):
        context = self.gateway().build_message_context(self.event(target_id=101))
        self.assertTrue(context.should_respond)
        self.assertFalse(context.should_record)
        self.assertEqual(context.reason, "qq_poke")
        self.assertEqual(context.to_turn_payload()["memory_message"], context.clean_message)
        self.assertIn("甲在 QQ 里戳了戳你的头像", context.to_turn_payload()["memory_message"])

    def test_webhook_records_member_poke_once_without_chat_or_care_effect(self):
        gateway = self.gateway()
        gateway.handle_poke_event = Mock()
        records = []
        engine = SimpleNamespace(
            record_passive_qq_message=lambda payload: (
                records.append(payload) or {"ok": True, "status": "recorded", "source_id": "notice-1"}
            ),
            process_turn_stream=Mock(),
        )
        app = FastAPI()
        app.include_router(build_qq_router(
            engine=engine, config_module=SimpleNamespace(QQ_BRIDGE_ENABLED=True), qq_gateway=gateway,
            channel_config=self.channel,
            runtime_metrics=SimpleNamespace(observe_request=lambda *a, **k: None, incr=lambda *a, **k: None),
            logger=SimpleNamespace(exception=lambda *a, **k: None), log_event=lambda *a, **k: None,
        ))
        with TestClient(app) as client:
            event = self.event()
            first = client.post("/api/qq/napcat/event", json=event)
            duplicate = client.post("/api/qq/napcat/event", json=event)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["status"], "recorded")
        self.assertEqual(first.json()["reason"], "group_poke_observed")
        self.assertEqual(duplicate.json()["status"], "ignored")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["memory_message"], "QQ互动：甲（QQ 404）戳了戳乙（QQ 405）。")
        engine.process_turn_stream.assert_not_called()
        gateway.handle_poke_event.assert_not_called()


if __name__ == "__main__":
    unittest.main()
