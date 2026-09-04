from __future__ import annotations

import asyncio
import types
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from companion_v01.plugin_agent_events import CallbackAgentEventPort, _PluginScopedAgentEventPort
from companion_v01.plugin_conversation_refs import PluginConversationReferenceAuthority
from companion_v01.plugin_api import (
    PluginAgentEventRequest,
    PluginAgentEventResult,
    PluginExternalEvent,
)
from companion_v01.routes import qq as qq_routes


class CallbackAgentEventPortTests(unittest.IsolatedAsyncioTestCase):
    async def test_delegate_result_is_preserved(self) -> None:
        expected = PluginAgentEventResult(True, "completed", "", "delivered")

        async def delegate(_request: PluginAgentEventRequest) -> PluginAgentEventResult:
            return expected

        port = CallbackAgentEventPort(delegate)
        result = await port.submit(
            PluginAgentEventRequest(
                trace_id="trace",
                conversation_ref="ref",
                message="event",
                event=PluginExternalEvent("timer.fired", (("label", "x"),)),
            )
        )
        self.assertEqual(result, expected)

    async def test_unavailable_host_fails_without_calling_delegate(self) -> None:
        called = False

        async def delegate(_request: PluginAgentEventRequest) -> PluginAgentEventResult:
            nonlocal called
            called = True
            return PluginAgentEventResult(True, "completed")

        port = CallbackAgentEventPort(delegate, availability_provider=lambda: False)
        result = await port.submit(object())  # type: ignore[arg-type]
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "host_unavailable")
        self.assertFalse(called)

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


class QQPluginAgentEventTests(unittest.IsolatedAsyncioTestCase):
    def _build(self):
        captured: list[object] = []
        references = {
            "ref-master": {
                "channel": "qq", "kind": "direct", "recipient": "user:1906243651",
                "session": "master", "profile": "master", "character": "reimu",
            },
            "ref-123": {
                "channel": "qq", "kind": "direct", "recipient": "user:456",
                "session": "qq_pri_123", "profile": "qq_123", "character": "reimu",
            },
        }

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
            plugin_agent_event_port_binder=captured.append,
            plugin_conversation_ref_resolver=references.get,
        )
        self.assertTrue(router.routes)
        self.assertEqual(len(captured), 1)
        return captured[0]

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
        self.assertNotEqual(payload["message"], "")

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
                )
            )
        self.assertTrue(result.ok)
        payload = observed["turn_payload"]
        self.assertEqual(payload["plugin_text_delivery"], "single_message")
        self.assertEqual(payload["plugin_text_prefix"], "【快讯】")
        self.assertEqual(payload["plugin_text_suffix"], "原文链接：https://example.test")


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

    def test_non_qq_or_incomplete_context_is_not_issued(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            authority = PluginConversationReferenceAuthority(Path(temp) / "key", instance_id="personal")
            context = types.SimpleNamespace(
                profile_user_id="master", session_id="master", character_pack_id="reimu",
                client_mode="desktop", request_context={},
            )
            self.assertEqual(authority.issue(context), "")
