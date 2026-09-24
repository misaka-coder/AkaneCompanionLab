"""QQ event facts use the same structured broker contract as every channel."""

from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from capcore import CapabilityResult
from fastapi import FastAPI
from akane_plugin import EventSubscription, GROUP_CONVERSATION_EVENT, PluginInvocationContext
from companion_v01.async_task_supervisor import AsyncTaskSupervisor
from companion_v01.deployment_security import QQChannelRuntimeConfig
from companion_v01.durable_session_queue import DurableSessionWorkQueue
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.plugin_events import PluginEventBroker, _PluginEventRegistration
from companion_v01.plugin_conversation_refs import PluginConversationReferenceAuthority
from companion_v01.plugin_turn_requests import HostTurnRequests
from companion_v01.qq_gateway import NapCatQQGateway
from companion_v01.routes.qq import build_qq_router
from companion_v01.session_inbox import SessionInboxStore
from companion_v01.turn_coordination import TurnCoordinator
from tests.test_plugin_events import _Recorder
from tests.test_plugin_events import _Response, _RouteEngine, _Metrics


class QQInboundTurnsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Provide the shared QQ/channel fixture used by completion tests."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.refs = PluginConversationReferenceAuthority(root / "refs.key", instance_id="test")
        self.store = SessionInboxStore(root / "inbox.db")
        self.queue = DurableSessionWorkQueue(self.store)
        self.coordinator = TurnCoordinator()
        self.turns = HostTurnRequests(self.refs.resolve)
        self.turns.bind_runtime(self.queue, self.coordinator)
        self.owners = {"example.scene": "g1"}
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.prompts, self.steers, self.logs, self.passive = [], [], [], []
        self.reject_steers = False
        self.model_error = False

        async def execute(_registration, event, **_kwargs):
            return CapabilityResult(is_error=False, status="ok", content=event.data)

        self.broker = PluginEventBroker(
            tuple(
                _PluginEventRegistration(
                    "example.scene",
                    event_type,
                    EventSubscription(f"example.scene.{event_type}", event_type),
                    generation_id="g1",
                )
                for event_type in (GROUP_CONVERSATION_EVENT, "conversation.direct.inbound")
            ),
            executor=execute,
            owners_provider=lambda: self.owners,
        )
        self.broker.bind_turn_router(self.turns)

        channel = QQChannelRuntimeConfig(
            enabled=True,
            profile_ref="qq.test",
            bot_id="100",
            onebot_http_url="http://127.0.0.1:3001",
            webhook_secret="",
            onebot_access_token="",
            require_webhook_auth=False,
            require_self_id=True,
        )
        self.gateway = NapCatQQGateway(channel_config=channel, wake_words=("Akane",))
        engine = self.engine = _RouteEngine()
        engine.session_inbox_store = self.store
        engine.plugin_steering_observer = self.turns.steering_results

        def stream(payload):
            engine.turns.append(dict(payload))
            self.entered.set()
            if not self.release.wait(5):
                raise RuntimeError("test_model_timeout")
            control = self.coordinator.drain(payload["_turn_control_id"])
            steers = control.get("steers", [])
            self.steers.extend(steers)
            applied = [] if self.reject_steers else [item.source_id for item in steers]
            failed = [item.source_id for item in steers] if self.reject_steers else []
            AkaneMemoryEngine._settle_turn_steering_receipts(
                engine,
                steers=steers,
                applied_source_ids=applied,
                failed_source_ids=failed,
            )
            if self.model_error:
                raise RuntimeError("test_model_failed")
            self.prompts.append(
                self.turns.prompt_context(
                    profile_user_id=payload["real_user_id"],
                    session_id=payload["user_id"],
                    character_pack_id=payload["character_pack_id"],
                )
            )
            if control.get("stop_requested"):
                yield {"type": "turn_stopped", "payload": {"status": "stopped", "speech": ""}}
            else:
                yield {
                    "type": "final_ui",
                    "payload": {
                        "status": "ok",
                        "speech": "收到事件",
                        "speech_segments": ["收到事件"],
                        "tool_events": [],
                    },
                }

        engine.process_turn_stream = stream
        engine.record_passive_qq_message = lambda payload: self.passive.append(dict(payload)) or {
            "ok": True,
            "status": "recorded",
            "source_id": "recorded",
        }
        self.supervisor = AsyncTaskSupervisor(name="qq-inbound-test")
        self.app = FastAPI()
        self.app.include_router(
            build_qq_router(
                engine=engine,
                config_module=SimpleNamespace(
                    QQ_BRIDGE_ENABLED=True,
                    QQ_STREAM_REPLIES_ENABLED=False,
                    QQ_GROUP_PASSIVE_MEMORY_MODE="all",
                    QQ_GROUP_ATTENTION_ENABLED=False,
                ),
                qq_gateway=self.gateway,
                runtime_metrics=_Metrics(),
                channel_config=channel,
                logger=SimpleNamespace(exception=lambda *a, **k: None, error=lambda *a, **k: None),
                log_event=lambda name, **data: self.logs.append((name, data)),
                turn_coordinator=self.coordinator,
                session_work_queue=self.queue,
                async_task_supervisor=self.supervisor,
                plugin_turn_router=self.turns,
                plugin_agent_event_handler_registrar=self.turns.register_channel,
                plugin_event_broker_provider=lambda: self.broker,
                plugin_conversation_ref_issuer=self.refs.issue_qq,
            )
        )
        self.network_patch = patch(
            "companion_v01.onebot_transport.requests.Session.request",
            return_value=_Response(),
        )
        self.network = self.network_patch.start()
        self.addCleanup(self.network_patch.stop)

        async def close():
            self.release.set()
            await self.supervisor.close()
            await self.queue.close()
            await self.turns.aclose()

        self.addAsyncCleanup(close)

    async def test_group_event_preserves_channel_facts_and_host_identity(self):
        recorder = _Recorder()
        broker = PluginEventBroker(
            (_PluginEventRegistration(
                "example.qq",
                GROUP_CONVERSATION_EVENT,
                EventSubscription("example.qq.group", GROUP_CONVERSATION_EVENT),
            ),),
            executor=recorder,
        )
        context = PluginInvocationContext(
            "qq_group_shared_87",
            "qq_group_shared_87",
            "qq",
            character_pack_id="reimu",
            conversation_ref="signed-reference",
        )
        receipt = await broker.emit(GROUP_CONVERSATION_EVENT, {
            "conversation_kind": "group",
            "conversation_id": "87",
            "actor_id": "qq:123",
            "text": "今晚风很大",
            "channel": "qq",
        }, context=context, event_key="group-event-1")
        async with asyncio.timeout(5):
            while not (done := await broker.receipt(receipt.dispatch_id)).complete:
                await asyncio.sleep(0.01)

        self.assertEqual(done.status, "completed")
        event = recorder.events[0]
        self.assertTrue(event.event_id)
        self.assertNotEqual(event.event_id, "group-event-1")
        self.assertEqual(event.source, "@host")
        self.assertEqual(event.data["actor_id"], "qq:123")
        self.assertEqual(event.data["conversation_kind"], "group")
        self.assertEqual(event.version, 1)


if __name__ == "__main__":
    unittest.main()
