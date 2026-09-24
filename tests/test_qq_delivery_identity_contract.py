from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from companion_v01.engine import AkaneMemoryEngine
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.onebot_transport import OneBotActionTransport
from companion_v01.plugin_event_delivery import apply_plugin_notification_output_policy
from tests import test_memcore_integration as fixtures
from tests import test_qq_nickname_addressing as nickname_fixtures
from tests import test_onebot_transport as transport_fixtures


class QQDeliveryIdentityContractTests(unittest.TestCase):
    def manager(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        manager = MemcoreManager(
            backend="memcore",
            storage_path=Path(temp) / "memcore.db",
            visible_scope="conversation",
            enable_flavor=True,
            shadow_compare=False,
            llm=fixtures._FakeLLM(),
            embedding_provider=fixtures._FakeEmbeddingProvider(),
        )
        self.addCleanup(manager.close)
        return manager

    def engine(self, manager):
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine._memcore_manager_if_enabled = lambda: manager
        engine._memcore_owns_compaction = lambda: False
        engine._warn_on_memcore_write_result = lambda *args: None
        engine._chat_provider_protocol_for_memcore = lambda **kwargs: "openai_chat"
        engine._clear_open_memcore_turn_guard = lambda turn_id: None
        engine._resolve_payload_character_pack_id = lambda payload: payload.get("character_pack_id", "")
        return engine

    def test_real_at_projection_identifies_self_and_only_current_group_nickname(self):
        setup = nickname_fixtures.QQNicknameAddressingTests()
        self.addCleanup(setup.doCleanups)
        gateway = setup.gateway()
        # Exercise the package projection as well as the gateway. Returning a
        # pre-normalized stub hid the loss of member ids in the old package.
        gateway._onebot_transport = OneBotActionTransport(
            gateway._channel_config,
            session=transport_fixtures._Session(
                [
                    transport_fixtures._Response(
                        {
                            "status": "ok",
                            "data": {"user_id": 10001, "group_id": 30003, "card": "天为", "nickname": "山城高岭"},
                        }
                    ),
                    transport_fixtures._Response({"status": "ok", "data": {"user_id": 10001, "nickname": "山城高岭"}}),
                ]
            ),
        )
        context = gateway.build_message_context(setup.event("？", targets=("10001",)))
        payload = context.to_turn_payload()
        addressing = AkaneMemoryEngine._normalize_message_addressing(payload, fallback_mode="current_request")
        record = {"source_id": "actual-at", "content": payload["memory_message"], "timestamp": 1788834534}
        target_id, target_name = AkaneMemoryEngine._apply_message_addressing(record, addressing)
        manager = self.manager()
        opened = manager.begin_input_turn(
            record,
            profile_user_id="group",
            session_id="group",
            character_pack_id="test",
            actor_stable_id="qq:20002",
            actor_display_name="群友",
            target_actor_id=target_id,
            target_actor_display_name=target_name,
        )
        self.assertTrue(opened["ok"], opened)
        projection = manager.build_context_projection(
            provider_profile="openai_chat", profile_user_id="group", session_id="group", character_pack_id="test"
        )
        text = "\n".join(str(item.get("content") or "") for item in projection["payloads"])
        self.assertIn("target: assistant", text)
        self.assertIn("天为 (id=assistant)", text)
        self.assertIn("@天为 ？", text)
        self.assertNotIn("本群昵称", text)
        self.assertNotIn("山城高岭", text)
        self.assertTrue(addressing["explicit_assistant_mention"])

    def test_suppressed_draft_is_not_replayed_as_an_assistant_message(self):
        manager = self.manager()
        engine = self.engine(manager)
        opened = manager.begin_input_turn(
            {"source_id": "news-1", "content": "已有新闻重复更新", "timestamp": 1788835000},
            profile_user_id="group",
            session_id="group",
            character_pack_id="test",
            external_event={"event_type": "finance.news", "source": "test", "fields": {"title": "已有新闻重复更新"}},
        )
        self.assertTrue(opened["ok"], opened)
        raw = '{"speech":"【暂不推送】只有旧信息。"}'
        frame = {"speech": "【暂不推送】只有旧信息。", "_provider_output_raw": raw}
        apply_plugin_notification_output_policy(
            frame,
            {
                "client_mode": "qq_text",
                "turn_kind": "plugin_event",
                "plugin_text_delivery": "single_message",
                "plugin_external_event": {"event_type": "finance.news"},
            },
        )
        self.assertEqual(frame["_provider_output_raw"], raw)
        self.assertFalse(frame.get("_deliberate_silence"))
        self.assertFalse(engine._should_persist_completed_assistant(True, frame))
        complete = engine._finalize_memcore_input_turn_for_delivery(
            final_output=frame,
            turn_id=opened["turn_id"],
            assistant_record={"source_id": "not-an-assistant-message", "content": "", "timestamp": 1788835001},
            memory_metadata={},
            provider_output_raw=raw,
            chat_model_override="",
            annotation_status="plain",
            profile_user_id="group",
            session_id="group",
            character_pack_id="test",
        )
        self.assertTrue(complete)
        self.assertNotIn("_memcore_failure", frame)
        manager.begin_input_turn(
            {"source_id": "next", "content": "你好", "timestamp": 1788835002},
            profile_user_id="group",
            session_id="group",
            character_pack_id="test",
        )
        projection = manager.build_context_projection(
            provider_profile="openai_chat", profile_user_id="group", session_id="group", character_pack_id="test"
        )
        self.assertFalse(any(item.get("role") == "assistant" for item in projection["payloads"]))
        serialized = json.dumps(projection["payloads"], ensure_ascii=False)
        self.assertNotIn("【暂不推送】", serialized)
        self.assertIn("notification.delivery", serialized)
        self.assertIn("suppressed", serialized)
        self.assertIn("text_sent: false", serialized)

    def test_receipt_failure_is_structured_without_restoring_blocked_speech(self):
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine._complete_memcore_input_turn = lambda **kwargs: {"ok": True}
        engine._clear_open_memcore_turn_guard = lambda turn_id: None
        frame = {"speech": "", "_notification_suppressed": "legacy_notification_silence"}
        with patch.object(engine, "record_plugin_timeline_event", side_effect=RuntimeError("private diagnostic")):
            self.assertTrue(
                engine._finalize_memcore_input_turn_for_delivery(
                    final_output=frame,
                    turn_id="test",
                    assistant_record={},
                    memory_metadata={},
                    provider_output_raw="",
                    chat_model_override="",
                    annotation_status="plain",
                    profile_user_id="test",
                    session_id="test",
                    character_pack_id="test",
                )
            )
        self.assertEqual(frame["speech"], "")
        self.assertEqual(frame["_memcore_failure"]["reason"], "notification_suppression_receipt_failed")
        self.assertNotIn("private diagnostic", json.dumps(frame))


if __name__ == "__main__":
    unittest.main()
