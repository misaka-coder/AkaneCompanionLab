from __future__ import annotations

import asyncio
import types
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from companion_v01.plugin_agent_events import (
    HostAgentEventRouter,
    _PluginScopedAgentEventPort,
)
from companion_v01.plugin_conversation_refs import PluginConversationReferenceAuthority
from companion_v01.durable_session_queue import DurableSessionWorkQueue
from companion_v01.session_inbox import SessionInboxStore
from companion_v01.plugin_api import (
    PluginAgentEventRequest,
    PluginAgentEventResult,
    PluginExternalEvent,
)
from companion_v01.routes import qq as qq_routes
from companion_v01.routes import think as think_routes


class PluginScopedAgentEventPortTests(unittest.IsolatedAsyncioTestCase):
    async def test_plugin_scope_owns_source_and_coalesces_idempotent_retries(self) -> None:
        requests: list[PluginAgentEventRequest] = []
        release = asyncio.Event()

        class Delegate:
            async def submit(self, request: PluginAgentEventRequest) -> PluginAgentEventResult:
                requests.append(request)
                await release.wait()
                return PluginAgentEventResult(True, "completed", "", "delivered")

        port = _PluginScopedAgentEventPort(
            plugin_id="akane.timer",
            delegate=Delegate(),
            availability_provider=lambda: True,
        )
        request = PluginAgentEventRequest(
            trace_id="trace",
            conversation_ref="opaque-ref",
            message="event",
            event=PluginExternalEvent("timer.fired", (("label", "water"),), "forged.source"),
            memory_idempotency_key="timer:one",
        )
        first = asyncio.create_task(port.submit(request))
        second = asyncio.create_task(port.submit(request))
        await asyncio.sleep(0)
        release.set()
        first_result, second_result = await asyncio.gather(first, second)
        self.assertTrue(first_result.ok)
        self.assertEqual(first_result, second_result)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].event.source, "akane.timer")

    async def test_plugin_scope_rejects_unsafe_event_fields(self) -> None:
        class Delegate:
            async def submit(self, _request: PluginAgentEventRequest) -> PluginAgentEventResult:
                raise AssertionError("delegate_should_not_run")

        port = _PluginScopedAgentEventPort(
            plugin_id="akane.timer",
            delegate=Delegate(),
            availability_provider=lambda: True,
        )
        result = await port.submit(
            PluginAgentEventRequest(
                trace_id="trace",
                conversation_ref="opaque-ref",
                message="event",
                event=PluginExternalEvent("timer.fired", (("access_token", "secret"),)),
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "invalid_external_event_fields")

    async def test_plugin_scope_rejects_unsupported_text_delivery(self) -> None:
        class Delegate:
            async def submit(self, _request: PluginAgentEventRequest) -> PluginAgentEventResult:
                raise AssertionError("delegate_should_not_run")

        port = _PluginScopedAgentEventPort(
            plugin_id="akane.timer",
            delegate=Delegate(),
            availability_provider=lambda: True,
        )
        result = await port.submit(
            PluginAgentEventRequest(
                trace_id="trace",
                conversation_ref="opaque-ref",
                message="event",
                event=PluginExternalEvent("timer.fired", (("label", "water"),)),
                text_delivery="finance_special",
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "unsupported_text_delivery")

    async def test_leading_address_policy_requires_single_message_delivery(self) -> None:
        class Delegate:
            async def submit(self, _request: PluginAgentEventRequest) -> PluginAgentEventResult:
                raise AssertionError("delegate_should_not_run")

        port = _PluginScopedAgentEventPort(
            plugin_id="akane.timer",
            delegate=Delegate(),
            availability_provider=lambda: True,
        )
        result = await port.submit(
            PluginAgentEventRequest(
                trace_id="trace",
                conversation_ref="opaque-ref",
                message="event",
                event=PluginExternalEvent("timer.fired", (("label", "water"),)),
                text_strip_leading_addresses=("主人",),
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(
            result.reason,
            "text_strip_leading_addresses_requires_single_message",
        )


class QQPluginAgentEventTests(unittest.IsolatedAsyncioTestCase):
    def _build(self, *, session_work_queue=None):
        references = {
            "ref-master": {
                "channel": "qq", "kind": "direct", "recipient": "user:1906243651",
                "session": "master", "profile": "master", "character": "reimu",
            },
            "ref-123": {
                "channel": "qq", "kind": "direct", "recipient": "user:456",
                "session": "qq_pri_123", "profile": "qq_123", "character": "reimu",
            },
            "ref-group": {
                "channel": "qq", "kind": "group", "recipient": "group:87",
                "session": "qq_group_shared_87", "profile": "qq_group_shared_87",
                "character": "reimu", "actor": "qq:123", "actor_profile": "qq_user_123",
            },
        }
        host_port = HostAgentEventRouter(references.get)

        class Gateway:
            master_qq = "1906243651"

            def resolve_identity(self, *, user_id: int, group_id: int = 0):
                if group_id:
                    return f"qq_group_shared_{group_id}", f"qq_group_shared_{group_id}"
                if str(user_id) == self.master_qq:
                    return "master", "master"
                return f"qq_pri_{user_id}", f"qq_{user_id}"

            def resolve_character_pack_id(self, _session_id):
                return "reimu"

            def resolve_reply_mode(self, _session_id):
                return "text"

            def resolve_chat_model_override(self, _session_id):
                return ""

            def build_extra_context(self, **_kwargs):
                return "qq.reply_delivery: text"

            def prefetch_remote_media_links_for_message(self, **_kwargs):
                return {}

            def send_reply(self, _context, text, **_kwargs):
                return {"ok": True, "status": "sent", "text": text}

            def send_replies(self, _context, messages):
                return {"ok": True, "status": "sent", "count": len(messages)}

            def resolve_group_attention_mode(self, _group_id, *, default="engaged"):
                return default

        class Engine:
            def prefetch_remote_media_links_for_message(self, **_kwargs):
                return {}

            def mark_generated_file_delivery(self, **_kwargs):
                return {"ok": True}

        router = qq_routes.build_qq_router(
            engine=Engine(),
            config_module=types.SimpleNamespace(QQ_GROUP_ATTENTION_TTL_SECONDS=120),
            qq_gateway=Gateway(),
            runtime_metrics=types.SimpleNamespace(observe_request=lambda *args, **kwargs: None),
            logger=types.SimpleNamespace(exception=lambda *args, **kwargs: None),
            log_event=lambda *args, **kwargs: None,
            plugin_agent_event_handler_registrar=host_port.register_channel,
            session_work_queue=session_work_queue,
        )
        self.assertTrue(router.routes)
        return host_port

    async def test_durable_event_is_handed_to_session_inbox_before_agent_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionInboxStore(Path(temp_dir) / "inbox.db")

            def defer(coroutine):
                coroutine.close()
                return types.SimpleNamespace(done=lambda: False)

            queue = DurableSessionWorkQueue(store, schedule_task=defer)
            port = self._build(session_work_queue=queue)

            result = await port.submit(
                PluginAgentEventRequest(
                    trace_id="job-completed:1",
                    conversation_ref="ref-master",
                    message="图片生成完成",
                    event=PluginExternalEvent(
                        "job.succeeded",
                        (("job_id", "job-1"),),
                        "host.jobs",
                    ),
                    memory_idempotency_key="job-completed:1",
                )
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.delivery_status, "queued")
            claimed = store.claim_next("master\0master", worker_id="test")
            self.assertTrue(claimed["ok"])
            self.assertEqual(claimed["item"].source_event_id, "job-completed:1")
            turn_payload = claimed["item"].payload["turn_payload"]
            self.assertEqual(turn_payload["source_message_id"], "job-completed:1")
            self.assertIn("系统事件", turn_payload["qq_delivery_context"]["sender_label"])

    async def test_event_uses_normal_qq_delivery_and_structured_payload(self) -> None:
        port = self._build()
        observed: dict[str, object] = {}

        def fake_process(**kwargs):
            observed.update(kwargs)
            return {
                "frame": {"speech": "定时到了", "emotion": "happy"},
                "reply_messages": ["定时到了"],
                "send_result": {"ok": True, "status": "sent"},
                "file_send_result": {"ok": True, "count": 0, "results": []},
            }

        with patch.object(qq_routes, "_process_qq_turn_streaming", fake_process):
            result = await port.submit(
                PluginAgentEventRequest(
                    trace_id="trace-1",
                    conversation_ref="ref-master",
                    message="提醒主人喝水",
                    event=PluginExternalEvent("timer.fired", (("minutes", "30"),)),
                    memory_idempotency_key="timer:1",
                )
            )
        self.assertTrue(result.ok)
        payload = observed["turn_payload"]
        self.assertEqual(payload["turn_kind"], "plugin_event")
        self.assertEqual(payload["plugin_external_event"]["event_type"], "timer.fired")
        self.assertEqual(payload["memory_idempotency_key"], "timer:1")
        self.assertEqual(payload["message_addressing"]["mode"], "current_request")
        self.assertNotEqual(payload["message"], "")

    async def test_group_event_restores_signed_causal_actor_for_run_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionInboxStore(Path(temp_dir) / "inbox.db")

            def defer(coroutine):
                coroutine.close()
                return types.SimpleNamespace(done=lambda: False)

            queue = DurableSessionWorkQueue(store, schedule_task=defer)
            port = self._build(session_work_queue=queue)
            result = await port.submit(
                PluginAgentEventRequest(
                    trace_id="job-completed:group",
                    conversation_ref="ref-group",
                    message="命令已结束",
                    event=PluginExternalEvent(
                        "job.succeeded",
                        (("job_id", "job-group"),),
                        "host.jobs",
                    ),
                    memory_idempotency_key="job-completed:group",
                )
            )

            self.assertTrue(result.ok)
            claimed = store.claim_next(
                "qq_group_shared_87\0qq_group_shared_87",
                worker_id="test",
            )
            payload = claimed["item"].payload["turn_payload"]
            self.assertEqual(payload["actor_stable_id"], "qq:123")
            self.assertEqual(payload["actor_profile_user_id"], "qq_user_123")
            self.assertIn("系统事件", payload["qq_delivery_context"]["sender_label"])

    async def test_context_mismatch_is_rejected_before_model(self) -> None:
        port = self._build()
        result = await port.submit(
            PluginAgentEventRequest(
                trace_id="trace-2",
                conversation_ref="ref-123",
                message="event",
                event=PluginExternalEvent("timer.fired", (("label", "x"),)),
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "event_context_mismatch")

    async def test_current_turn_delivery_marks_user_event_transient(self) -> None:
        port = self._build()
        observed: dict[str, object] = {}

        def fake_process(**kwargs):
            observed.update(kwargs)
            return {
                "frame": {"speech": "收到"},
                "reply_messages": ["收到"],
                "send_result": {"ok": True, "status": "sent"},
                "file_send_result": {"ok": True, "count": 0, "results": []},
            }

        with patch.object(qq_routes, "_process_qq_turn_streaming", fake_process):
            result = await port.submit(
                PluginAgentEventRequest(
                    trace_id="trace-3",
                    conversation_ref="ref-master",
                    message="只在本轮处理",
                    event=PluginExternalEvent("screen.changed", (("title", "B站"),)),
                    delivery="current_turn",
                )
            )
        self.assertTrue(result.ok)
        self.assertTrue(observed["turn_payload"]["transient_user_message"])

    async def test_single_message_presentation_reaches_ordinary_qq_delivery(self) -> None:
        port = self._build()
        observed: dict[str, object] = {}

        def fake_process(**kwargs):
            observed.update(kwargs)
            return {
                "frame": {"speech": "正文", "emotion": "thinking"},
                "reply_messages": ["【快讯】\n正文\n原文链接：https://example.test"],
                "send_result": {"ok": True, "status": "sent"},
                "file_send_result": {"ok": True, "count": 0, "results": []},
            }

        with patch.object(qq_routes, "_process_qq_turn_streaming", fake_process):
            result = await port.submit(
                PluginAgentEventRequest(
                    trace_id="trace-4",
                    conversation_ref="ref-master",
                    message="处理快讯",
                    event=PluginExternalEvent("news.fired", (("title", "测试"),)),
                    text_delivery="single_message",
                    text_prefix="【快讯】",
                    text_suffix="原文链接：https://example.test",
                    text_strip_leading_addresses=("主人",),
                )
            )
        self.assertTrue(result.ok)
        payload = observed["turn_payload"]
        self.assertEqual(payload["plugin_text_delivery"], "single_message")
        self.assertEqual(payload["plugin_text_prefix"], "【快讯】")
        self.assertEqual(payload["plugin_text_suffix"], "原文链接：https://example.test")
        self.assertEqual(payload["plugin_text_strip_leading_addresses"], ["主人"])


class DesktopPluginAgentEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_durable_event_is_handed_to_desktop_inbox_even_while_offline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            captured_handlers: dict[str, object] = {}
            store = SessionInboxStore(Path(temp_dir) / "inbox.db")

            def defer(coroutine):
                coroutine.close()
                return types.SimpleNamespace(done=lambda: False)

            queue = DurableSessionWorkQueue(store, schedule_task=defer)
            think_routes.build_think_router(
                engine=types.SimpleNamespace(),
                public_guard=types.SimpleNamespace(),
                runtime_metrics=types.SimpleNamespace(),
                log_event=lambda *args, **kwargs: None,
                session_work_queue=queue,
                plugin_agent_event_handler_registrar=captured_handlers.__setitem__,
                desktop_agent_frame_delivery=None,
            )
            handler = captured_handlers["desktop_pet"]

            result = await handler(
                PluginAgentEventRequest(
                    trace_id="job-completed:desk",
                    conversation_ref="opaque",
                    message="图片生成完成",
                    event=PluginExternalEvent(
                        "job.succeeded",
                        (("job_id", "job-desk"),),
                        "host.jobs",
                    ),
                    memory_idempotency_key="job-completed:desk",
                ),
                {
                    "channel": "desktop_pet",
                    "kind": "direct",
                    "recipient": "desktop:desk-1",
                    "session": "desk-1",
                    "profile": "master",
                    "character": "reimu",
                },
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.delivery_status, "queued")
            claimed = store.claim_next("master\0desk-1", worker_id="test")
            self.assertTrue(claimed["ok"])
            self.assertEqual(claimed["item"].source_event_id, "job-completed:desk")
            self.assertEqual(
                claimed["item"].payload["turn_payload"]["character_pack_id"],
                "reimu",
            )

    async def test_event_uses_normal_desktop_turn_and_existing_frame_contract(self) -> None:
        captured_handlers: dict[str, object] = {}
        turn_payloads: list[dict[str, object]] = []
        delivered_frames: list[dict[str, object]] = []

        class Engine:
            def process_turn(self, payload):
                turn_payloads.append(dict(payload))
                return {
                    "speech": "该喝水啦",
                    "speech_segments": ["该喝水啦"],
                    "emotion": "happy",
                    "activity": {"action": "pause"},
                    "_debug": {"must_not_reach_desktop": True},
                }

        async def deliver(frame):
            delivered_frames.append(dict(frame))
            return {"ok": True, "status": "queued", "reason": ""}

        think_routes.build_think_router(
            engine=Engine(),
            public_guard=types.SimpleNamespace(),
            runtime_metrics=types.SimpleNamespace(),
            log_event=lambda *args, **kwargs: None,
            plugin_agent_event_handler_registrar=captured_handlers.__setitem__,
            desktop_agent_frame_delivery=deliver,
            desktop_agent_event_available=lambda: True,
        )
        handler = captured_handlers["desktop_pet"]
        result = await handler(
            PluginAgentEventRequest(
                trace_id="trace-desk",
                conversation_ref="opaque",
                message="提醒主人喝水",
                event=PluginExternalEvent("timer.fired", (("label", "喝水"),), "akane.timer"),
                memory_idempotency_key="timer:desk:1",
            ),
            {
                "channel": "desktop_pet",
                "kind": "direct",
                "recipient": "desktop:desk-1",
                "session": "desk-1",
                "profile": "master",
                "character": "reimu",
            },
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.delivery_status, "queued")
        self.assertEqual(turn_payloads[0]["turn_kind"], "plugin_event")
        self.assertEqual(turn_payloads[0]["client_mode"], "desktop_pet")
        self.assertEqual(turn_payloads[0]["character_pack_id"], "reimu")
        self.assertEqual(turn_payloads[0]["plugin_external_event"]["source"], "akane.timer")
        self.assertEqual(turn_payloads[0]["message_addressing"]["mode"], "current_request")
        self.assertEqual(delivered_frames[0]["speech"], "该喝水啦")
        self.assertEqual(delivered_frames[0]["emotion"], "happy")
        self.assertNotIn("_debug", delivered_frames[0])

    async def test_desktop_event_does_not_run_model_without_delivery_channel(self) -> None:
        captured_handlers: dict[str, object] = {}

        class Engine:
            def process_turn(self, _payload):
                raise AssertionError("model_should_not_run")

        think_routes.build_think_router(
            engine=Engine(),
            public_guard=types.SimpleNamespace(),
            runtime_metrics=types.SimpleNamespace(),
            log_event=lambda *args, **kwargs: None,
            plugin_agent_event_handler_registrar=captured_handlers.__setitem__,
            desktop_agent_frame_delivery=None,
        )
        handler = captured_handlers["desktop_pet"]
        result = await handler(
            PluginAgentEventRequest(
                trace_id="trace-offline",
                conversation_ref="opaque",
                message="event",
                event=PluginExternalEvent("timer.fired", (("label", "x"),)),
            ),
            {
                "channel": "desktop_pet",
                "kind": "direct",
                "recipient": "desktop:desk-1",
                "session": "desk-1",
                "profile": "master",
                "character": "reimu",
            },
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "desktop_client_unavailable")


class ConversationReferenceAuthorityTests(unittest.TestCase):
    def test_reference_survives_restart_and_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            key_path = Path(temp) / "conversation-ref.key"
            context = types.SimpleNamespace(
                profile_user_id="master",
                session_id="master",
                character_pack_id="reimu",
                client_mode="qq",
                request_context={"user_id": 1906243651, "group_id": 0},
            )
            first = PluginConversationReferenceAuthority(key_path, instance_id="personal")
            reference = first.issue(context)
            self.assertTrue(reference.startswith("acr1."))
            second = PluginConversationReferenceAuthority(key_path, instance_id="personal")
            resolved = second.resolve(reference)
            self.assertEqual(resolved["session"], "master")
            self.assertEqual(resolved["character"], "reimu")
            self.assertIsNone(second.resolve(reference[:-1] + ("A" if reference[-1] != "A" else "B")))

    def test_group_reference_preserves_host_verified_causal_actor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            key_path = Path(temp) / "conversation-ref.key"
            authority = PluginConversationReferenceAuthority(key_path, instance_id="personal")
            reference = authority.issue_qq(
                profile_user_id="qq_group_shared_87",
                session_id="qq_group_shared_87",
                character_pack_id="reimu",
                group_id=87,
                actor_stable_id="qq:123",
                actor_profile_user_id="qq_user_123",
            )

            resolved = authority.resolve(reference)

            self.assertEqual(resolved["actor"], "qq:123")
            self.assertEqual(resolved["actor_profile"], "qq_user_123")

    def test_desktop_reference_is_restart_stable_and_channel_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            key_path = Path(temp) / "key"
            authority = PluginConversationReferenceAuthority(key_path, instance_id="personal")
            context = types.SimpleNamespace(
                profile_user_id="master", session_id="master", character_pack_id="reimu",
                client_mode="desktop_pet", request_context={},
            )
            reference = authority.issue(context)
            self.assertTrue(reference.startswith("acr1."))
            resolved = PluginConversationReferenceAuthority(key_path, instance_id="personal").resolve(reference)
            self.assertEqual(resolved["channel"], "desktop_pet")
            self.assertEqual(resolved["recipient"], "desktop:master")

    def test_unsupported_or_incomplete_context_is_not_issued(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            authority = PluginConversationReferenceAuthority(Path(temp) / "key", instance_id="personal")
            unsupported = types.SimpleNamespace(
                profile_user_id="master", session_id="master", character_pack_id="reimu",
                client_mode="scene_static", request_context={},
            )
            incomplete = types.SimpleNamespace(
                profile_user_id="master", session_id="", character_pack_id="reimu",
                client_mode="desktop_pet", request_context={},
            )
            self.assertEqual(authority.issue(unsupported), "")
            self.assertEqual(authority.issue(incomplete), "")


class HostAgentEventRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_routes_only_to_channel_from_signed_reference(self) -> None:
        calls: list[tuple[str, str]] = []

        async def desktop(request, resolved):
            calls.append((request.trace_id, resolved["session"]))
            return PluginAgentEventResult(True, "completed", "", "queued")

        router = HostAgentEventRouter(
            lambda reference: {
                "channel": "desktop_pet",
                "kind": "direct",
                "recipient": "desktop:desk-1",
                "session": "desk-1",
                "profile": "master",
                "character": "reimu",
            }
            if reference == "valid"
            else None
        )
        router.register_channel("desktop_pet", desktop)
        result = await router.submit(
            PluginAgentEventRequest(
                trace_id="trace-desktop",
                conversation_ref="valid",
                message="到点了",
                event=PluginExternalEvent("timer.fired", (("label", "water"),)),
            )
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.delivery_status, "queued")
        self.assertEqual(calls, [("trace-desktop", "desk-1")])

    async def test_unknown_channel_fails_without_fallback(self) -> None:
        router = HostAgentEventRouter(lambda _reference: {"channel": "future_channel"})
        result = await router.submit(
            PluginAgentEventRequest(
                trace_id="trace-future",
                conversation_ref="valid",
                message="event",
                event=PluginExternalEvent("timer.fired", (("label", "x"),)),
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "agent_event_channel_unavailable")
