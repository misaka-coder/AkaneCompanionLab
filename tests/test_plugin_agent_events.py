from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from companion_v01.durable_session_queue import DurableSessionWorkQueue
from companion_v01.plugin_agent_events import HostAgentEventRouter
from companion_v01.plugin_conversation_refs import PluginConversationReferenceAuthority
from companion_v01.plugin_turn_intents import HostJobTurnIntent, HostTurnIntent, HostTurnResult
from companion_v01.qq_gateway import QQMessageContext
from companion_v01.routes import qq as qq_routes
from companion_v01.routes import think as think_routes
from companion_v01.session_inbox import SessionInboxStore


def turn_intent(
    *,
    trace_id: str = "trace-1",
    reference: str = "",
    message: str = "事件已到期",
    source: str = "host.jobs",
    event_type: str = "job.succeeded",
    data: dict | None = None,
    idempotency_key: str = "",
    requires_queue: bool = False,
) -> HostTurnIntent:
    intent_type = HostJobTurnIntent if idempotency_key else HostTurnIntent
    values = dict(
        message=message,
        data=data or {"job_id": "job-1", "origin_turn_id": "turn-1"},
        source=source,
        event_type=event_type,
        conversation_ref=reference,
        trace_id=trace_id,
        requires_queue=requires_queue,
    )
    if idempotency_key:
        values["idempotency_key"] = idempotency_key
    return intent_type(**values)


class HostTurnRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_submit_uses_the_signed_reference_and_preserves_structured_data(self):
        seen = []

        async def handle(request, resolved):
            seen.append((request, dict(resolved)))
            return HostTurnResult(True, "completed", delivery_status="queued")

        router = HostAgentEventRouter(lambda reference: {
            "channel": "desktop_pet",
            "kind": "direct",
            "recipient": "desktop:session-1",
            "session": "session-1",
            "profile": "profile-1",
            "character": "akane",
        } if reference == "signed-ref" else None)
        router.register_channel("desktop_pet", handle)

        result = await router.submit(turn_intent(reference="signed-ref", data={
            "count": 2,
            "nested": [False, None, {"label": "中文"}],
        }))

        self.assertTrue(result.ok)
        self.assertEqual(result.delivery_status, "queued")
        self.assertEqual(seen[0][0].data["nested"][2]["label"], "中文")
        self.assertEqual(seen[0][1]["session"], "session-1")

    async def test_unknown_channel_fails_without_fallback(self):
        router = HostAgentEventRouter(lambda _reference: {"channel": "future_channel"})
        result = await router.submit(turn_intent(reference="signed-ref"))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "agent_event_channel_unavailable")


class QQTurnRouterTests(unittest.IsolatedAsyncioTestCase):
    def _build(self, *, session_work_queue=None, prefetch_calls=None):
        references = {
            "ref-master": {
                "channel": "qq", "kind": "direct", "recipient": "user:1906243651",
                "session": "master", "profile": "master", "character": "reimu",
            },
            "ref-group": {
                "channel": "qq", "kind": "group", "recipient": "group:87",
                "session": "qq_group_shared_87", "profile": "qq_group_shared_87",
                "character": "reimu", "actor": "qq:123", "actor_profile": "qq_user_123",
            },
        }
        host_router = HostAgentEventRouter(references.get)

        class Gateway:
            master_qq = "1906243651"

            def context_from_delivery_context(self, payload):
                return QQMessageContext(
                    should_respond=True,
                    reason="plugin_event",
                    should_record=False,
                    **{key: payload[key] for key in (
                        "is_group", "target_id", "user_id", "group_id", "session_id", "profile_user_id",
                        "clean_message", "raw_message", "sender_label", "source_message_id", "character_pack_id",
                    ) if key in payload},
                )

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
            def prefetch_remote_media_links_for_message(self, **kwargs):
                if prefetch_calls is not None:
                    prefetch_calls.append(dict(kwargs))
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
            plugin_agent_event_handler_registrar=host_router.register_channel,
            session_work_queue=session_work_queue,
        )
        self.assertTrue(router.routes)
        return host_router

    async def test_plugin_event_source_link_skips_remote_media_prefetch(self):
        prefetch_calls = []
        turn_payloads = []
        router = self._build(prefetch_calls=prefetch_calls)

        def complete_turn(**kwargs):
            turn_payloads.append(dict(kwargs["turn_payload"]))
            return {
                "send_result": {"ok": True, "status": "sent"},
                "final_frame_received": True,
                "frame": {"speech": "事件分析"},
            }

        with unittest.mock.patch.object(qq_routes, "_process_qq_turn_streaming", side_effect=complete_turn):
            result = await router.submit(
                turn_intent(
                    reference="ref-group",
                    source="akane.finance",
                    event_type="finance.news",
                    message=(
                        "外部财经快讯\n"
                        "原文链接：https://finance.eastmoney.com/a/202609103870885740.html"
                    ),
                )
            )

        self.assertTrue(result.ok, result)
        self.assertEqual(prefetch_calls, [])
        self.assertEqual(len(turn_payloads), 1)
        payload = turn_payloads[0]
        self.assertIn("https://finance.eastmoney.com/a/", payload["message"])
        self.assertIn("【插件事件来源链接】", payload["extra_context"])
        self.assertIn("不是用户提交的媒体下载请求", payload["extra_context"])
        self.assertNotIn("【链接素材预处理结果】", payload["extra_context"])

    async def test_job_completion_is_frozen_in_the_session_inbox(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionInboxStore(Path(temp_dir) / "inbox.db")

            def defer(coroutine):
                coroutine.close()
                return types.SimpleNamespace(done=lambda: False)

            queue = DurableSessionWorkQueue(store, schedule_task=defer)
            router = self._build(session_work_queue=queue)
            result = await router.submit(turn_intent(
                trace_id="job-completed:1",
                reference="ref-master",
                message="图片生成完成",
                idempotency_key="job-completed:1",
                requires_queue=True,
            ))

            self.assertTrue(result.ok)
            self.assertEqual(result.delivery_status, "queued")
            claimed = store.claim_next("master\0master", worker_id="test")
            self.assertTrue(claimed["ok"])
            self.assertEqual(claimed["item"].source_event_id, "job-completed:1")
            self.assertEqual(
                claimed["item"].payload["turn_payload"]["plugin_external_event"]["data"]["job_id"],
                "job-1",
            )

    async def test_group_reference_keeps_host_verified_actor_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionInboxStore(Path(temp_dir) / "inbox.db")

            def defer(coroutine):
                coroutine.close()
                return types.SimpleNamespace(done=lambda: False)

            queue = DurableSessionWorkQueue(store, schedule_task=defer)
            router = self._build(session_work_queue=queue)
            result = await router.submit(turn_intent(
                reference="ref-group",
                idempotency_key="job-completed:group",
                requires_queue=True,
            ))
            self.assertTrue(result.ok)
            claimed = store.claim_next("qq_group_shared_87\0qq_group_shared_87", worker_id="test")
            payload = claimed["item"].payload["turn_payload"]
            self.assertEqual(payload["actor_stable_id"], "qq:123")
            self.assertEqual(payload["actor_profile_user_id"], "qq_user_123")


class DesktopTurnRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_desktop_turn_uses_the_normal_frame_contract(self):
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
            log_event=lambda *a, **k: None,
            plugin_agent_event_handler_registrar=captured_handlers.__setitem__,
            desktop_agent_frame_delivery=deliver,
            desktop_agent_event_available=lambda: True,
        )
        handler = captured_handlers["desktop_pet"]
        result = await handler(
            turn_intent(reference="opaque", source="akane.timer", event_type="timer.fired",
                        data={"label": "喝水"}, trace_id="trace-desk"),
            {
                "channel": "desktop_pet", "kind": "direct", "recipient": "desktop:desk-1",
                "session": "desk-1", "profile": "master", "character": "reimu",
            },
        )
        self.assertTrue(result.ok)
        self.assertEqual(turn_payloads[0]["turn_kind"], "plugin_event")
        self.assertEqual(turn_payloads[0]["plugin_external_event"]["data"], {"label": "喝水"})
        self.assertEqual(delivered_frames[0]["speech"], "该喝水啦")
        self.assertNotIn("_debug", delivered_frames[0])


class ConversationReferenceAuthorityTests(unittest.TestCase):
    def test_reference_survives_restart_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            key_path = Path(temp) / "conversation-ref.key"
            context = types.SimpleNamespace(
                profile_user_id="master",
                session_id="master",
                character_pack_id="reimu",
                client_mode="qq",
                request_context=QQMessageContext(
                    should_respond=True,
                    reason="private",
                    user_id=1906243651,
                    target_id=1906243651,
                    session_id="master",
                    profile_user_id="master",
                    character_pack_id="reimu",
                ).to_turn_payload(),
            )
            first = PluginConversationReferenceAuthority(key_path, instance_id="personal")
            reference = first.issue(context)
            resolved = PluginConversationReferenceAuthority(key_path, instance_id="personal").resolve(reference)
            self.assertEqual(resolved["session"], "master")
            self.assertIsNone(first.resolve(reference[:-1] + ("A" if reference[-1] != "A" else "B")))

    def test_group_reference_preserves_host_verified_causal_actor(self):
        with tempfile.TemporaryDirectory() as temp:
            authority = PluginConversationReferenceAuthority(Path(temp) / "key", instance_id="personal")
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


if __name__ == "__main__":
    unittest.main()
