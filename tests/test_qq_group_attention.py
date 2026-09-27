from __future__ import annotations

import tempfile
import unittest
import asyncio
import time
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.engine import AkaneMemoryEngine
from companion_v01.qq_gateway import NapCatQQGateway
from companion_v01.qq_group_attention import QQGroupAttentionState
from companion_v01.turn_coordination import TurnCoordinator
from companion_v01.routes.qq import (
    _apply_group_attention_actor_scope,
    _group_attention_review_event,
    _process_qq_turn_streaming,
    build_qq_router,
)


class QQGroupAttentionStateTests(unittest.TestCase):
    def test_only_window_participants_can_supersede_and_restart_after_finish(self):
        now = [0.0]
        state = QQGroupAttentionState(clock=lambda: now[0])
        ticket, _ = state.arm("group", mode="adaptive", delay_seconds=10, actor_keys=("a",))
        now[0] = 5
        same, _ = state.arm("group", mode="adaptive", delay_seconds=10, actor_keys=("b",))
        self.assertEqual(same.deadline, 10)
        state.claim(ticket)
        self.assertEqual(state.supersede_for_actor("group", "newcomer"), "")
        self.assertEqual(state.supersede_for_actor("group", "b"), ticket.token)
        self.assertFalse(state.begin_reply(ticket))
        self.assertEqual(state.supersede_for_actor("group", "b"), "")
        self.assertEqual(state.arm("group", mode="adaptive", delay_seconds=10)[1], "in_flight_dirty")
        self.assertFalse(state.has_pending("group"))
        now[0] = 30
        self.assertTrue(state.finish(ticket))
        next_ticket, _ = state.arm("group", mode="adaptive", delay_seconds=10, actor_keys=("b",))
        self.assertEqual(next_ticket.deadline, 40)

    def test_reply_started_or_finalizing_observation_is_not_superseded(self):
        for finalizing in (False, True):
            with self.subTest(finalizing=finalizing):
                state = QQGroupAttentionState(clock=lambda: 0)
                ticket, _ = state.arm("group", mode="adaptive", delay_seconds=0, actor_keys=("a",))
                state.claim(ticket)
                if not finalizing:
                    self.assertTrue(state.begin_reply(ticket))
                self.assertEqual(state.supersede_for_actor("group", "a", stop_running=lambda _token: False), "")
                self.assertFalse(state.is_superseded(ticket))

    def test_review_event_preserves_attention_semantics_without_dynamic_prose(self) -> None:
        self.assertEqual(
            _group_attention_review_event("engaged_followup"),
            "event.group_attention_followup_review",
        )
        self.assertEqual(
            _group_attention_review_event("idle_observation"),
            "event.group_attention_idle_review",
        )

    def test_actor_scope_is_kept_only_for_concrete_engaged_followup(self) -> None:
        fields = {
            "actor_stable_id": "qq:20002",
            "actor_profile_user_id": "qq_actor_20002",
            "actor_display_name": "群成员",
            "actor_platform": "qq",
        }
        engaged = dict(fields)
        _apply_group_attention_actor_scope(engaged, reason="engaged_followup")
        self.assertEqual(engaged, fields)

        idle = dict(fields)
        _apply_group_attention_actor_scope(idle, reason="idle_observation")
        self.assertEqual(idle, {})

    def test_pending_ticket_has_fixed_deadline(self) -> None:
        now = [100.0]
        state = QQGroupAttentionState(clock=lambda: now[0])
        state.mark_visible_reply("group", ttl_seconds=120)

        first, first_reason = state.arm("group", mode="engaged", delay_seconds=10)
        now[0] = 108.0
        second, second_reason = state.arm("group", mode="engaged", delay_seconds=10)

        self.assertEqual(first_reason, "armed")
        self.assertEqual(second_reason, "already_pending")
        self.assertIs(first, second)
        self.assertEqual(first.deadline, 110.0)

    def test_engaged_and_adaptive_modes_are_distinct(self) -> None:
        now = [10.0]
        state = QQGroupAttentionState(clock=lambda: now[0])

        ticket, reason = state.arm("group-a", mode="engaged", delay_seconds=0)
        adaptive, adaptive_reason = state.arm("group-b", mode="adaptive", delay_seconds=0)

        self.assertIsNone(ticket)
        self.assertEqual(reason, "outside_engagement")
        self.assertIsNotNone(adaptive)
        self.assertEqual(adaptive_reason, "armed")
        self.assertEqual(adaptive.reason, "idle_observation")

    def test_idle_observation_cooldown_is_message_armed(self) -> None:
        now = [20.0]
        state = QQGroupAttentionState(clock=lambda: now[0])
        ticket, _ = state.arm("group", mode="adaptive", delay_seconds=0)
        self.assertTrue(state.claim(ticket))
        self.assertFalse(state.finish(ticket))
        state.mark_idle_observed("group", cooldown_seconds=60)

        blocked, reason = state.arm("group", mode="adaptive", delay_seconds=0)
        now[0] = 81.0
        allowed, allowed_reason = state.arm("group", mode="adaptive", delay_seconds=0)

        self.assertIsNone(blocked)
        self.assertEqual(reason, "idle_cooldown")
        self.assertIsNotNone(allowed)
        self.assertEqual(allowed_reason, "armed")

    def test_in_flight_messages_coalesce_into_one_new_generation(self) -> None:
        state = QQGroupAttentionState(clock=lambda: 100.0)
        state.mark_visible_reply("group", ttl_seconds=120)
        ticket, reason = state.arm("group", mode="engaged", delay_seconds=10)
        self.assertEqual(reason, "armed")
        self.assertTrue(state.claim(ticket))
        self.assertTrue(state.is_in_flight("group"))

        second, second_reason = state.arm("group", mode="engaged", delay_seconds=10)
        third, third_reason = state.arm("group", mode="engaged", delay_seconds=10)

        self.assertIsNone(second)
        self.assertIsNone(third)
        self.assertEqual(second_reason, "in_flight_dirty")
        self.assertEqual(third_reason, "in_flight_dirty")
        self.assertFalse(state.has_pending("group"))
        self.assertTrue(state.finish(ticket))
        self.assertFalse(state.is_in_flight("group"))

        next_ticket, next_reason = state.arm("group", mode="engaged", delay_seconds=10)
        self.assertIsNotNone(next_ticket)
        self.assertEqual(next_reason, "armed")

    def test_cancel_invalidates_claimed_ticket_without_finishing_its_replacement(self) -> None:
        state = QQGroupAttentionState(clock=lambda: 100.0)
        old, _ = state.arm("group", mode="adaptive", delay_seconds=0)
        self.assertTrue(state.claim(old))
        self.assertTrue(state.cancel("group"))
        self.assertFalse(state.is_current(old))
        new, reason = state.arm("group", mode="adaptive", delay_seconds=0)
        self.assertEqual(reason, "armed")
        self.assertTrue(state.claim(new))
        self.assertFalse(state.finish(old, discard_dirty=True))
        self.assertTrue(state.is_current(new))
        self.assertTrue(state.is_in_flight("group"))


class QQGroupAttentionGatewayTests(unittest.TestCase):
    def test_listen_command_is_admitted_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "qq-state.json"
            gateway = NapCatQQGateway(state_path=state_path)
            context = gateway.build_message_context(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "self_id": 10001,
                    "user_id": 20002,
                    "group_id": 30003,
                    "message_id": "listen-1",
                    "raw_message": "/listen adaptive",
                }
            )

            self.assertTrue(context.should_respond)
            self.assertTrue(context.addressed_to_assistant)
            result = gateway.handle_group_attention_command(
                context,
                sender_role="admin",
                default_mode="engaged",
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["attention_mode"], "adaptive")
            restored = NapCatQQGateway(state_path=state_path)
            self.assertEqual(restored.resolve_group_attention_mode(30003), "adaptive")

    def test_passive_message_is_not_falsely_addressed_to_assistant(self) -> None:
        gateway = NapCatQQGateway()
        context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "group",
                "self_id": 10001,
                "user_id": 20002,
                "group_id": 30003,
                "message_id": "passive-1",
                "raw_message": "大家晚上吃什么？",
            }
        )

        self.assertFalse(context.should_respond)
        self.assertFalse(context.addressed_to_assistant)
        self.assertFalse(context.to_turn_payload()["message_addressing"]["addressed_to_assistant"])


class QQGroupAttentionDeliveryTests(unittest.TestCase):
    def test_exact_silent_frame_never_reaches_qq_or_failure_notice(self) -> None:
        class Engine:
            desktop_pet_character_resources = None

            def __init__(self, frame=None):
                self.frame = frame or {
                    "_deliberate_silence": True,
                    "speech": "",
                    "speech_segments": [],
                    "tool_events": [],
                }

            def process_turn_stream(self, _payload):
                yield {
                    "type": "final_ui",
                    "payload": dict(self.frame),
                }

            def process_turn(self, _payload):
                raise AssertionError("final frame was already delivered")

        class Gateway:
            def __init__(self):
                self.sent = []

            def resolve_reply_mode(self, _session_id):
                return "text"

            def render_reply_messages(self, frame):
                speech = str(frame.get("speech") or "").strip()
                return [speech] if speech else []

            def send_replies(self, _context, messages):
                self.sent.extend(messages)
                return {"ok": True, "count": len(messages), "results": []}

            def send_reply(self, _context, message):
                self.sent.append(message)
                return {"ok": True}

            def send_generated_files(self, _context, _events):
                return {"ok": True, "count": 0, "results": []}

            def send_music_cards(self, _context, _events):
                return {"ok": True, "count": 0, "results": []}

            def send_market_charts(self, _context, _events, **_kwargs):
                return {"ok": True, "count": 0, "results": []}

            def send_finance_reports(self, _context, _events, **_kwargs):
                return {"ok": True, "count": 0, "results": []}

            def send_emotion_mface(self, *_args, **_kwargs):
                raise AssertionError("silent observation cannot send emotion")

            def send_stickers(self, _context, _events):
                return {"ok": True, "count": 0, "results": []}

        gateway = Gateway()
        result = _process_qq_turn_streaming(
            engine=Engine(),
            qq_gateway=gateway,
            context=SimpleNamespace(session_id="qq-group", reply_mode="text"),
            turn_payload={"user_id": "qq-group", "message": "event.group_attention_review"},
            config_module=SimpleNamespace(
                QQ_STREAM_REPLIES_ENABLED=True,
                QQ_STREAM_IMMEDIATE_SEGMENTS=8,
                QQ_REPLY_MAX_SEGMENTS=8,
                QQ_VOICE_MAX_SEGMENTS=3,
                QQ_VOICE_MAX_TEXT_CHARS=280,
            ),
        )

        self.assertEqual(gateway.sent, [])
        self.assertEqual(result["reply_messages"], [])
        self.assertEqual(result["final_failure_notice_result"]["status"], "skipped")

        action_gateway = Gateway()
        action_result = _process_qq_turn_streaming(
            engine=Engine(
                {
                    "_deliberate_silence": True,
                    "speech": "",
                    "speech_segments": [],
                    "tool_events": [
                        {
                            "type": "qq_visible_action_receipt",
                            "action": "group_poke",
                            "status": "success",
                            "ok": True,
                        }
                    ],
                }
            ),
            qq_gateway=action_gateway,
            context=SimpleNamespace(session_id="qq-group", reply_mode="text"),
            turn_payload={"user_id": "qq-group", "message": "戳一下"},
            config_module=SimpleNamespace(
                QQ_STREAM_REPLIES_ENABLED=True,
                QQ_STREAM_IMMEDIATE_SEGMENTS=8,
                QQ_REPLY_MAX_SEGMENTS=8,
                QQ_VOICE_MAX_SEGMENTS=3,
                QQ_VOICE_MAX_TEXT_CHARS=280,
            ),
        )
        self.assertTrue(action_result["visible_action_delivered"])
        self.assertEqual(action_result["final_failure_notice_result"]["status"], "skipped")
        self.assertEqual(action_gateway.sent, [])

    def test_old_attention_silent_protocol_is_removed(self) -> None:
        self.assertFalse(hasattr(AkaneMemoryEngine, "_is_qq_optional_silent_result"))
        self.assertFalse(hasattr(AkaneMemoryEngine, "_mark_qq_optional_silent"))

    def test_adaptive_passive_message_runs_one_memcore_backed_observation(self) -> None:
        recorded = []
        processed = []
        scheduled = []

        class Engine:
            desktop_pet_character_resources = None

            def record_passive_qq_message(self, payload):
                recorded.append(dict(payload))
                return {"ok": True, "status": "recorded", "source_id": "observed-1"}

            @staticmethod
            def prefetch_remote_media_links_for_message(**_kwargs):
                return {}

            def process_turn_stream(self, payload):
                processed.append(dict(payload))
                yield {
                    "type": "final_ui",
                    "payload": {"_deliberate_silence": True, "speech": "", "tool_events": []},
                }

        class Supervisor:
            @staticmethod
            def create_task(coroutine):
                scheduled.append(coroutine)
                return SimpleNamespace(done=lambda: False)

        class Metrics:
            @staticmethod
            def observe_request(*_args, **_kwargs):
                return None

        app = FastAPI()
        app.include_router(
            build_qq_router(
                engine=Engine(),
                config_module=SimpleNamespace(
                    QQ_BRIDGE_ENABLED=True,
                    QQ_GROUP_ATTENTION_MODE="adaptive",
                    QQ_GROUP_ATTENTION_DELAY_SECONDS=0,
                    QQ_GROUP_ATTENTION_TTL_SECONDS=120,
                    QQ_GROUP_ATTENTION_IDLE_COOLDOWN_SECONDS=60,
                ),
                qq_gateway=NapCatQQGateway(),
                runtime_metrics=Metrics(),
                logger=SimpleNamespace(exception=lambda *_args, **_kwargs: None),
                log_event=lambda *_args, **_kwargs: None,
                async_task_supervisor=Supervisor(),
            )
        )

        response = TestClient(app).post(
            "/api/qq/napcat/event",
            json={
                "post_type": "message",
                "message_type": "group",
                "self_id": 10001,
                "user_id": 20002,
                "group_id": 30003,
                "message_id": "ambient-1",
                "message": [{"type": "text", "data": {"text": "这游戏今晚更新了"}}],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(recorded), 1)
        self.assertTrue(response.json()["attention"]["scheduled"])
        self.assertEqual(len(scheduled), 1)
        asyncio.run(scheduled.pop())
        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0]["turn_kind"], "qq_attention")
        self.assertTrue(processed[0]["transient_user_message"])
        self.assertEqual(processed[0]["message"], "event.group_attention_idle_review")
        self.assertEqual(processed[0]["memory_attention_reference_source_ids"], ["observed-1"])
        self.assertNotIn("event.group_attention_idle_review", processed[0].get("extra_context", ""))
        self.assertEqual(processed[0]["message_addressing"]["trigger"], "group_attention_idle_review")
        self.assertFalse(processed[0]["message_addressing"]["addressed_to_assistant"])
        self.assertEqual(processed[0]["qq_delivery_context"]["source_message_id"], "")
        self.assertNotIn("actor_stable_id", processed[0])

    def test_passive_image_is_recorded_with_handle_without_starting_attention(self) -> None:
        recorded = []
        processed = []
        scheduled = []
        log_calls = []

        class Engine:
            desktop_pet_character_resources = None

            @staticmethod
            def ingest_qq_attachments(**_kwargs):
                return [
                    {
                        "attachment_id": "attachment-image-1",
                        "attachment_handle": "img_001",
                        "kind": "image",
                        "status": "pending_observation",
                        "detail": {"qq_sender_label": "群成员"},
                    }
                ]

            def record_passive_qq_messages(self, payloads):
                recorded.extend(dict(payload) for payload in payloads)
                return {
                    "ok": True,
                    "status": "recorded",
                    "count": len(payloads),
                    "recorded_count": len(payloads),
                    "failed_count": 0,
                    "results": [{"ok": True, "source_id": "observed-image-1"}],
                }

            def process_turn_stream(self, payload):
                processed.append(dict(payload))
                raise AssertionError("a passive image without pixels must not start attention")

        class Supervisor:
            @staticmethod
            def create_task(coroutine):
                scheduled.append(coroutine)
                return SimpleNamespace(done=lambda: False)

        class Metrics:
            @staticmethod
            def observe_request(*_args, **_kwargs):
                return None

        app = FastAPI()
        app.include_router(
            build_qq_router(
                engine=Engine(),
                config_module=SimpleNamespace(
                    QQ_BRIDGE_ENABLED=True,
                    QQ_GROUP_ATTENTION_MODE="adaptive",
                    QQ_GROUP_ATTENTION_DELAY_SECONDS=0,
                    QQ_GROUP_ATTENTION_IDLE_COOLDOWN_SECONDS=60,
                ),
                qq_gateway=NapCatQQGateway(),
                runtime_metrics=Metrics(),
                logger=SimpleNamespace(exception=lambda *_args, **_kwargs: None),
                log_event=lambda name, **kwargs: log_calls.append((name, kwargs)),
                async_task_supervisor=Supervisor(),
            )
        )

        response = TestClient(app).post(
            "/api/qq/napcat/event",
            json={
                "post_type": "message",
                "message_type": "group",
                "self_id": 10001,
                "user_id": 20002,
                "group_id": 30003,
                "message_id": "ambient-image-1",
                "sender": {"nickname": "群成员"},
                "message": [
                    {
                        "type": "image",
                        "data": {"file": "ambient.jpg", "url": "http://127.0.0.1/ambient.jpg"},
                    }
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "buffered")
        self.assertEqual(len(scheduled), 1)
        asyncio.run(scheduled.pop())
        self.assertEqual(scheduled, [])
        self.assertEqual(processed, [])
        self.assertEqual(len(recorded), 1)
        self.assertIn("【群成员】[图片]", recorded[0]["message"])
        self.assertIn('handle: "img_001"', recorded[0]["message"])
        self.assertNotIn("发来了一张图片", recorded[0]["message"])
        attention_logs = [payload for name, payload in log_calls if name == "qq_group_attention_considered"]
        self.assertEqual(len(attention_logs), 1)
        self.assertFalse(attention_logs[0]["scheduled"])
        self.assertEqual(attention_logs[0]["reason"], "passive_image_recorded")

    def test_following_same_sender_text_binds_exact_passive_image_to_attention_turn(self) -> None:
        scheduled = []
        processed = []
        recorded = []

        class Engine:
            desktop_pet_character_resources = None

            @staticmethod
            def ingest_qq_attachments(**_kwargs):
                return [
                    {
                        "attachment_id": "attachment-image-exact",
                        "attachment_handle": "img_exact",
                        "kind": "image",
                        "status": "ready",
                    }
                ]

            def record_passive_qq_messages(self, payloads):
                recorded.extend(dict(payload) for payload in payloads)
                return {
                    "ok": True,
                    "status": "recorded",
                    "count": len(payloads),
                    "recorded_count": len(payloads),
                    "failed_count": 0,
                    "results": [{"ok": True, "source_id": "observed-image"}],
                }

            def record_passive_qq_message(self, payload):
                recorded.append(dict(payload))
                source_id = "observed-text-2" if "再看" in str(payload.get("message") or "") else "observed-text"
                return {"ok": True, "status": "recorded", "source_id": source_id}

            @staticmethod
            def prepare_qq_native_image_inputs(**kwargs):
                self.assertEqual(kwargs["attachment_ids"], ["attachment-image-exact"])
                return {
                    "ok": True,
                    "status": "ready",
                    "images": [
                        {
                            "attachment_id": "attachment-image-exact",
                            "attachment_handle": "img_exact",
                            "data_url": "data:image/png;base64,cGl4ZWxz",
                        }
                    ],
                }

            @staticmethod
            def prefetch_remote_media_links_for_message(**_kwargs):
                return {}

            def process_turn_stream(self, payload):
                processed.append(dict(payload))
                yield {
                    "type": "final_ui",
                    "payload": {"_deliberate_silence": True, "speech": "", "tool_events": []},
                }

        class Supervisor:
            @staticmethod
            def create_task(coroutine):
                scheduled.append(coroutine)
                return SimpleNamespace(done=lambda: False)

        class Metrics:
            @staticmethod
            def observe_request(*_args, **_kwargs):
                return None

        app = FastAPI()
        app.include_router(
            build_qq_router(
                engine=Engine(),
                config_module=SimpleNamespace(
                    QQ_BRIDGE_ENABLED=True,
                    QQ_GROUP_ATTENTION_MODE="adaptive",
                    QQ_GROUP_ATTENTION_DELAY_SECONDS=0,
                    QQ_GROUP_ATTENTION_TTL_SECONDS=120,
                    QQ_GROUP_ATTENTION_IDLE_COOLDOWN_SECONDS=60,
                    QQ_ATTACHMENT_READY_WAIT_SECONDS=0,
                ),
                qq_gateway=NapCatQQGateway(),
                runtime_metrics=Metrics(),
                logger=SimpleNamespace(exception=lambda *_args, **_kwargs: None),
                log_event=lambda *_args, **_kwargs: None,
                async_task_supervisor=Supervisor(),
            )
        )
        event_ts = int(time.time())

        image_response = TestClient(app).post(
            "/api/qq/napcat/event",
            json={
                "post_type": "message",
                "message_type": "group",
                "self_id": 10001,
                "user_id": 20002,
                "group_id": 30003,
                "time": event_ts,
                "message_id": "ambient-image-exact",
                "sender": {"nickname": "群成员"},
                "message": [
                    {
                        "type": "image",
                        "data": {"file": "answer.png", "url": "http://127.0.0.1/answer.png"},
                    }
                ],
            },
        )
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(len(scheduled), 1)
        asyncio.run(scheduled.pop(0))
        self.assertEqual(processed, [])

        text_response = TestClient(app).post(
            "/api/qq/napcat/event",
            json={
                "post_type": "message",
                "message_type": "group",
                "self_id": 10001,
                "user_id": 20002,
                "group_id": 30003,
                "time": event_ts + 1,
                "message_id": "ambient-text-exact",
                "sender": {"nickname": "群成员"},
                "message": [{"type": "text", "data": {"text": "这便是答案"}}],
            },
        )
        self.assertEqual(text_response.status_code, 200)
        self.assertTrue(text_response.json()["attention"]["scheduled"])
        self.assertEqual(len(scheduled), 1)

        second_text_response = TestClient(app).post(
            "/api/qq/napcat/event",
            json={
                "post_type": "message",
                "message_type": "group",
                "self_id": 10001,
                "user_id": 20002,
                "group_id": 30003,
                "time": event_ts + 2,
                "message_id": "ambient-text-exact-2",
                "sender": {"nickname": "群成员"},
                "message": [{"type": "text", "data": {"text": "再看清楚些"}}],
            },
        )
        self.assertEqual(second_text_response.status_code, 200)
        self.assertFalse(second_text_response.json()["attention"]["scheduled"])
        self.assertEqual(second_text_response.json()["attention"]["reason"], "already_pending")
        self.assertEqual(len(scheduled), 1)
        asyncio.run(scheduled.pop(0))

        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0]["qq_current_attachment_ids"], ["attachment-image-exact"])
        self.assertEqual(processed[0]["native_user_images"][0]["attachment_handle"], "img_exact")
        self.assertEqual(
            processed[0]["memory_attention_reference_source_ids"],
            ["observed-image", "observed-text", "observed-text-2"],
        )

    def test_passive_message_without_memcore_source_does_not_schedule_attention(self) -> None:
        scheduled = []

        class Engine:
            desktop_pet_character_resources = None

            @staticmethod
            def record_passive_qq_message(_payload):
                return {"ok": True, "status": "recorded", "source_id": ""}

            @staticmethod
            def prefetch_remote_media_links_for_message(**_kwargs):
                return {}

        class Supervisor:
            @staticmethod
            def create_task(coroutine):
                scheduled.append(coroutine)
                return SimpleNamespace(done=lambda: False)

        class Metrics:
            @staticmethod
            def observe_request(*_args, **_kwargs):
                return None

        app = FastAPI()
        app.include_router(
            build_qq_router(
                engine=Engine(),
                config_module=SimpleNamespace(
                    QQ_BRIDGE_ENABLED=True,
                    QQ_GROUP_ATTENTION_MODE="adaptive",
                    QQ_GROUP_ATTENTION_DELAY_SECONDS=0,
                ),
                qq_gateway=NapCatQQGateway(),
                runtime_metrics=Metrics(),
                logger=SimpleNamespace(exception=lambda *_args, **_kwargs: None),
                log_event=lambda *_args, **_kwargs: None,
                async_task_supervisor=Supervisor(),
            )
        )
        response = TestClient(app).post(
            "/api/qq/napcat/event",
            json={
                "post_type": "message",
                "message_type": "group",
                "self_id": 10001,
                "user_id": 20002,
                "group_id": 30003,
                "message_id": "ambient-no-anchor",
                "message": [{"type": "text", "data": {"text": "普通消息"}}],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["attention"]["reason"], "projection_anchor_missing")
        self.assertEqual(scheduled, [])

    def test_quote_only_message_to_bot_enters_optional_reply_path(self) -> None:
        processed = []
        gateway = NapCatQQGateway()
        gateway.resolve_quoted_message_evidence = lambda _event, *, context: {
            "ok": True,
            "status": "resolved",
            "quoted_message": {
                "message_id": "bot-message-1",
                "text": "要不要一起玩？",
                "actor_id": "10001",
                "actor_label": "Akane",
                "actor_is_bot": True,
                "timestamp": 100,
                "conversation_kind": "group",
                "conversation_id": "30003",
                "attachment_count": 0,
            },
            "attachments": [],
        }

        class Engine:
            desktop_pet_character_resources = None

            @staticmethod
            def prefetch_remote_media_links_for_message(**_kwargs):
                return {}

            def process_turn_stream(self, payload):
                processed.append(dict(payload))
                yield {"type": "final_ui", "payload": {"speech": "好呀。", "tool_events": []}}

        class Metrics:
            @staticmethod
            def observe_request(*_args, **_kwargs):
                return None

        class Response:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"status": "ok", "data": {"message_id": 1}}

        app = FastAPI()
        app.include_router(
            build_qq_router(
                engine=Engine(),
                config_module=SimpleNamespace(QQ_BRIDGE_ENABLED=True),
                qq_gateway=gateway,
                runtime_metrics=Metrics(),
                logger=SimpleNamespace(exception=lambda *_args, **_kwargs: None),
                log_event=lambda *_args, **_kwargs: None,
            )
        )
        with patch("companion_v01.onebot_transport.requests.Session.request", return_value=Response()):
            response = TestClient(app).post(
                "/api/qq/napcat/event",
                json={
                    "post_type": "message",
                    "message_type": "group",
                    "self_id": 10001,
                    "user_id": 20002,
                    "group_id": 30003,
                    "message_id": "quote-current-1",
                    "message": [
                        {"type": "reply", "data": {"id": "bot-message-1"}},
                        {"type": "text", "data": {"text": "可以"}},
                    ],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0]["turn_kind"], "qq_optional_reply")
        self.assertTrue(
            processed[0]["extra_context"].endswith(
                "event.qq_optional_reply_review"
            )
        )
        self.assertNotIn("若不需要", processed[0]["extra_context"])
        self.assertIn("可以", processed[0]["message"])
        self.assertEqual(
            processed[0]["message_addressing"]["reply_reference"]["excerpt"],
            "要不要一起玩？",
        )

    def test_quote_to_group_member_records_structured_reply_relationship(self) -> None:
        recorded = []
        gateway = NapCatQQGateway()
        gateway.resolve_quoted_message_evidence = lambda _event, *, context: {
            "ok": True,
            "status": "resolved",
            "quoted_message": {
                "message_id": "member-message-1",
                "text": "今晚八点开黑",
                "actor_id": "40004",
                "actor_label": "天为",
                "actor_is_bot": False,
                "timestamp": 100,
                "conversation_kind": "group",
                "conversation_id": "30003",
                "attachment_count": 0,
            },
            "attachments": [],
        }

        class Engine:
            def record_passive_qq_message(self, payload):
                recorded.append(dict(payload))
                return {"ok": True, "status": "recorded", "source_id": "observed-reply-1"}

        class Metrics:
            @staticmethod
            def observe_request(*_args, **_kwargs):
                return None

        app = FastAPI()
        app.include_router(
            build_qq_router(
                engine=Engine(),
                config_module=SimpleNamespace(QQ_BRIDGE_ENABLED=True),
                qq_gateway=gateway,
                runtime_metrics=Metrics(),
                logger=SimpleNamespace(exception=lambda *_args, **_kwargs: None),
                log_event=lambda *_args, **_kwargs: None,
            )
        )
        response = TestClient(app).post(
            "/api/qq/napcat/event",
            json={
                "post_type": "message",
                "message_type": "group",
                "self_id": 10001,
                "user_id": 20002,
                "group_id": 30003,
                "message_id": "member-reply-current-1",
                "message": [
                    {"type": "reply", "data": {"id": "member-message-1"}},
                    {"type": "text", "data": {"text": "我也来"}},
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "recorded")
        self.assertEqual(len(recorded), 1)
        addressing = recorded[0]["message_addressing"]
        self.assertEqual(addressing["mode"], "observed")
        self.assertEqual(addressing["primary_target"], {"actor_id": "qq:40004", "display_name": "天为"})
        self.assertEqual(
            addressing["reply_reference"],
            {
                "actor_id": "qq:40004",
                "actor_display_name": "天为",
                "message_id": "member-message-1",
                "excerpt": "今晚八点开黑",
                "timestamp": 100,
                "conversation_kind": "group",
                "conversation_id": "30003",
            },
        )



class QQGroupAttentionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.scheduled = []
        self.processed = []
        self.logs = []
        self.now = [0.0]
        self.state = QQGroupAttentionState(clock=lambda: self.now[0])
        self.sequence = 0
        self.gateway = NapCatQQGateway()
        self.gateway.send_reply = lambda *_args, **_kwargs: {"ok": True}

        def record(payload):
            return {"ok": True, "source_id": f"source-{payload.get('message', '')}"}

        def process(payload):
            self.processed.append(dict(payload))
            yield {"type": "final_ui", "payload": {"speech": "", "_deliberate_silence": True}}

        def prepare_qq_native_image_inputs(**_kwargs):
            return {"status": "ready", "images": [{"data_url": "data:image/png;base64,cGl4ZWxz"}]}

        def prefetch_remote_media_links_for_message(**_kwargs):
            return {}

        engine = SimpleNamespace(
            desktop_pet_character_resources=None,
            ingest_qq_attachments=lambda **_kwargs: [
                {"attachment_id": "image-1", "attachment_handle": "img_1", "kind": "image", "status": "ready"}
            ],
            record_passive_qq_message=record,
            record_passive_qq_messages=lambda payloads: {"ok": True, "results": [record(p) for p in payloads]},
            prepare_qq_native_image_inputs=prepare_qq_native_image_inputs,
            prefetch_remote_media_links_for_message=prefetch_remote_media_links_for_message,
            process_turn_stream=process,
        )
        self.engine = engine
        self.coordinator = TurnCoordinator()
        self.config = SimpleNamespace(
            QQ_BRIDGE_ENABLED=True, QQ_GROUP_ATTENTION_MODE="adaptive",
            QQ_GROUP_ATTENTION_DELAY_SECONDS=0, QQ_GROUP_ATTENTION_TTL_SECONDS=120,
            QQ_GROUP_ATTENTION_IDLE_COOLDOWN_SECONDS=60, QQ_ATTACHMENT_READY_WAIT_SECONDS=0,
        )
        def schedule(coroutine):
            self.scheduled.append(coroutine)
            return SimpleNamespace(done=lambda: False)

        app = FastAPI()
        with patch("companion_v01.routes.qq.QQGroupAttentionState", return_value=self.state):
            app.include_router(build_qq_router(
                engine=engine,
                config_module=self.config,
                turn_coordinator=self.coordinator,
                qq_gateway=self.gateway,
                runtime_metrics=SimpleNamespace(observe_request=lambda *_args, **_kwargs: None),
                logger=SimpleNamespace(exception=lambda *_args, **_kwargs: None),
                log_event=lambda name, **kwargs: self.logs.append((name, kwargs)),
                async_task_supervisor=SimpleNamespace(create_task=schedule),
            ))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.addCleanup(lambda: [coroutine.close() for coroutine in self.scheduled])

    def post(self, text="看看这张图", *, image=False, user_id=20002):
        self.sequence += 1
        if not text.startswith("/listen"):
            text = f"{text} {self.sequence}"
        message = ([{"type": "image", "data": {"file": "test.png", "url": "http://127.0.0.1/test.png"}}]
                   if image else [{"type": "text", "data": {"text": text}}])
        response = self.client.post("/api/qq/napcat/event", json={
            "post_type": "message", "message_type": "group", "self_id": 10001,
            "user_id": user_id, "group_id": 30003, "message_id": f"recovery-{self.sequence}",
            "time": int(time.time()), "sender": {"nickname": "群成员", "role": "admin"},
            "message": message,
        })
        self.assertEqual(response.status_code, 200)
        return response.json()

    async def stage_image(self):
        self.post(image=True)
        await self.scheduled.pop(0)
        self.assertTrue(self.post()["attention"]["scheduled"])

    async def recover_with_new_message(self):
        self.now[0] += 61
        self.assertTrue(self.post("新的话题")["attention"]["scheduled"])
        await self.scheduled.pop(0)
        self.assertEqual(len(self.processed), 1)

    def test_image_preparation_failure_releases_group_and_preserves_reason(self):
        async def scenario():
            await self.stage_image()
            original = asyncio.to_thread
            async def fail(fn, *args, **kwargs):
                if fn.__name__ == "prepare_qq_native_image_inputs":
                    raise RuntimeError("test preparation failure")
                return await original(fn, *args, **kwargs)
            with patch("asyncio.to_thread", fail):
                await self.scheduled.pop(0)
            self.assertEqual(self.processed, [])
            self.assertTrue(any(name == "qq_group_attention_failed" and data["reason"] == "RuntimeError"
                                for name, data in self.logs))
            self.assertEqual(self.post()["attention"]["reason"], "idle_cooldown")
            await self.recover_with_new_message()
        asyncio.run(scenario())

    async def pause_and_interrupt(self, *, phase="prepare_qq_native_image_inputs", reopen=False,
                                  cancel_task=False, late_failure=False):
        await self.stage_image()
        original = asyncio.to_thread
        entered = asyncio.Event()
        release = asyncio.Event()
        async def pause(fn, *args, **kwargs):
            if fn.__name__ == phase:
                entered.set()
                await release.wait()
                if late_failure:
                    raise RuntimeError("obsolete preparation failed")
            return await original(fn, *args, **kwargs)
        with patch("asyncio.to_thread", pause):
            task = asyncio.create_task(self.scheduled.pop(0))
            try:
                await asyncio.wait_for(entered.wait(), timeout=5)
                if cancel_task:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    self.assertEqual(self.post("/listen off")["attention_mode"], "off")
                    if reopen:
                        self.assertEqual(self.post("/listen adaptive")["attention_mode"], "adaptive")
                        self.assertTrue(self.post("重新开启后的话题")["attention"]["scheduled"])
                    release.set()
                    await asyncio.wait_for(task, timeout=5)
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.processed, [])
        if reopen:
            # Finishing or failing an obsolete preparation must preserve the
            # replacement snapshot, its ticket, and its cooldown state.
            await self.scheduled.pop(0)
            self.assertEqual(len(self.processed), 1)
        elif cancel_task:
            await self.recover_with_new_message()
        else:
            self.assertEqual(self.post()["attention"]["reason"], "mode_off")
            self.post("/listen adaptive")
            await self.recover_with_new_message()

    def test_disable_during_image_preparation_prevents_model_call(self):
        asyncio.run(self.pause_and_interrupt())

    def test_disable_during_link_preparation_prevents_model_call(self):
        asyncio.run(self.pause_and_interrupt(phase="prefetch_remote_media_links_for_message"))

    def test_reenable_does_not_revive_old_preparation_or_remove_new_snapshot(self):
        asyncio.run(self.pause_and_interrupt(reopen=True))

    def test_obsolete_preparation_failure_does_not_damage_new_observation(self):
        asyncio.run(self.pause_and_interrupt(reopen=True, late_failure=True))

    def test_cancelled_task_releases_group(self):
        asyncio.run(self.pause_and_interrupt(cancel_task=True))

    async def exercise_message_during_generation(self, *, newcomer=False, reply_started=False, finalizing=False):
        await self.stage_image()
        # Both participants belong to the same initial observation window.
        self.post("我也在这个话题里", user_id=20003)
        self.config.QQ_GROUP_ATTENTION_DELAY_SECONDS = 10
        entered = threading.Event()
        release = threading.Event()
        settled = []
        sent = []
        original_process = self.engine.process_turn_stream
        original_record = self.engine.record_passive_qq_messages
        original_arm = self.state.arm
        armed = []

        def track_arm(*args, **kwargs):
            result = original_arm(*args, **kwargs)
            if result[1] == "armed":
                armed.append(result[0])
            return result

        def send(_context, text, **_kwargs):
            sent.append(text)
            return {"ok": True}

        def process(payload):
            self.processed.append(dict(payload))
            if reply_started:
                yield {"type": "speech_segment", "text": "正在回复旧话题。"}
            if finalizing:
                self.coordinator.begin_finalization(payload["_turn_control_id"])
            entered.set()
            if not release.wait(timeout=10):
                raise TimeoutError("test model was not released")
            settled.append("model_and_persistence_finished")
            try:
                yield {"type": "final_ui", "payload": {"speech": "正在回复旧话题。", "tool_events": []}}
                settled.append("stream_exhausted")
            finally:
                settled.append("stream_closed")

        def record(payloads):
            self.assertIn("stream_closed", settled, "new history must wait for the old turn to settle")
            return original_record(payloads)

        self.engine.process_turn_stream = process
        self.engine.record_passive_qq_messages = record
        self.gateway.send_reply = send
        with patch.object(self.state, "arm", side_effect=track_arm):
            task = asyncio.create_task(self.scheduled.pop(0))
            worker = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                self.now[0] = 50
                response = self.post("补充刚才的话", user_id=20004 if newcomer else 20003)
                self.assertEqual(response["status"], "buffered")
                interrupted = any(name == "qq_group_attention_superseded" for name, _ in self.logs)
                self.assertEqual(interrupted, not (newcomer or reply_started or finalizing))
                self.assertEqual(armed, [])
                self.assertEqual(len(self.scheduled), 1)  # Passive writes, not another model request.
                worker = asyncio.create_task(self.scheduled.pop(0))
                await asyncio.sleep(0)
                self.assertEqual(armed, [])
                self.now[0] = 80
                release.set()
                await asyncio.wait_for(task, 5)
                await asyncio.wait_for(worker, 5)
                self.assertEqual(len(armed), 1)
                self.assertEqual(armed[0].deadline, 90)
                self.assertEqual(bool(sent), newcomer or reply_started or finalizing)
                self.assertEqual("stream_exhausted" in settled, newcomer or reply_started or finalizing)
                self.assertEqual(len(self.processed), 1)
                self.engine.process_turn_stream = original_process
                await self.scheduled.pop(0)
                self.assertEqual(len(self.processed), 2)
                self.assertNotIn("participant_keys", self.processed[-1])
            finally:
                release.set()
                await asyncio.gather(task, *([worker] if worker is not None else []), return_exceptions=True)

    def test_window_participant_followup_cancels_unspoken_reply_and_restarts_after_settlement(self):
        asyncio.run(self.exercise_message_during_generation())

    def test_newcomer_waits_for_reply_and_persistence_before_next_countdown(self):
        asyncio.run(self.exercise_message_during_generation(newcomer=True))

    def test_started_reply_finishes_before_next_countdown(self):
        asyncio.run(self.exercise_message_during_generation(reply_started=True))

    def test_finalizing_reply_finishes_before_next_countdown(self):
        asyncio.run(self.exercise_message_during_generation(finalizing=True))

    def test_participant_followup_during_preparation_restarts_after_worker_finishes(self):
        async def scenario():
            await self.stage_image()
            original = asyncio.to_thread
            entered, release = asyncio.Event(), asyncio.Event()

            async def pause(fn, *args, **kwargs):
                if fn.__name__ == "prepare_qq_native_image_inputs":
                    entered.set()
                    await release.wait()
                return await original(fn, *args, **kwargs)

            with patch("asyncio.to_thread", pause):
                task = asyncio.create_task(self.scheduled.pop(0))
                try:
                    await asyncio.wait_for(entered.wait(), 5)
                    response = self.post("再补充一点")
                    self.assertEqual(response["attention"]["reason"], "in_flight_dirty")
                    self.assertEqual(self.scheduled, [])
                    self.config.QQ_GROUP_ATTENTION_DELAY_SECONDS = 10
                    self.now[0] = 50
                    release.set()
                    await asyncio.wait_for(task, 5)
                finally:
                    release.set()
                    await asyncio.gather(task, return_exceptions=True)
            self.assertEqual(self.processed, [])
            self.assertEqual(len(self.scheduled), 1)
            response = self.post("等待期间继续补充")
            self.assertEqual(response["attention"]["reason"], "already_pending")
            await self.scheduled.pop(0)
            self.assertEqual(len(self.processed), 1)
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
