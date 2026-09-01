from __future__ import annotations

import asyncio
import importlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_api import (
    DIRECT_CONVERSATION_EVENT,
    GROUP_CONVERSATION_EVENT,
    NotificationResult,
    PluginEventEnvelope,
    PluginReasoningResult,
)
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_storage import InstancePluginStorageService
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.skill_runtime import SkillRegistry
from companion_v01.tool_runtime import ToolExecutionContext


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = PROJECT_ROOT / "examples" / "plugins" / "akane_gentle_checkin"
PLUGIN_ID = "akane.sample.gentle-checkin"
CAPABILITY_ID = f"{PLUGIN_ID}.configure.v1"


class _ReasoningPort:
    def __init__(self, text: str = "忙完了吗？记得也稍微休息一下。") -> None:
        self.text = text
        self.requests: list[Any] = []

    async def analyze(self, request: Any) -> PluginReasoningResult:
        self.requests.append(request)
        return PluginReasoningResult(ok=True, status="completed", text=self.text)


class _NotificationPort:
    def __init__(self) -> None:
        self.intents: list[Any] = []

    async def send(self, intent: Any) -> NotificationResult:
        self.intents.append(intent)
        return NotificationResult(ok=True, status="delivered")


class _OnePassController:
    shutdown_requested = False

    async def wait_for_shutdown(self, timeout: float | None = None) -> bool:
        del timeout
        self.shutdown_requested = True
        return True


class InstalledGentleCheckinSampleTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._install_temp = tempfile.TemporaryDirectory()
        temp_root = Path(cls._install_temp.name)
        build_source = temp_root / "source"
        wheelhouse = temp_root / "wheelhouse"
        cls.install_root = temp_root / "installed"
        shutil.copytree(SAMPLE_ROOT, build_source)
        wheelhouse.mkdir()
        subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--outdir",
                str(wheelhouse),
                str(build_source),
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        wheels = tuple(wheelhouse.glob("akane_gentle_checkin-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("sample_plugin_wheel_not_built")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--target",
                str(cls.install_root),
                str(wheels[0]),
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        sys.path.insert(0, str(cls.install_root))
        importlib.invalidate_caches()
        cls.plugin_module = importlib.import_module("akane_gentle_checkin")

    @classmethod
    def tearDownClass(cls) -> None:
        sys.path.remove(str(cls.install_root))
        sys.modules.pop("akane_gentle_checkin", None)
        importlib.invalidate_caches()
        cls._install_temp.cleanup()

    async def asyncSetUp(self) -> None:
        self._runtime_temp = tempfile.TemporaryDirectory()
        self.root = Path(self._runtime_temp.name)
        self.reasoning = _ReasoningPort()
        self.notifications = _NotificationPort()
        self.host = PluginHost(
            (PluginSelection(PLUGIN_ID, True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
        )
        self.host.bind_plugin_storage_service(
            InstancePluginStorageService(self.root / "data", "test-instance")
        )
        self.host.bind_reasoning_port(self.reasoning)
        self.host.bind_notification_port(self.notifications)
        self.registry = SkillRegistry(
            bundled_root=self.root / "bundled",
            managed_root=self.root / "managed",
            execution_workspace_root=self.root / "execution",
            contributed_roots_provider=self.host.skill_roots,
        )

    async def asyncTearDown(self) -> None:
        await self.host.stop()
        self._runtime_temp.cleanup()

    async def test_real_wheel_publishes_every_claimed_contribution_without_prompt_bloat(self) -> None:
        status = await self.host.start()

        self.assertEqual(status["status"], "active")
        contribution = status["plugins"][0]["contribution_snapshot"]
        self.assertEqual(
            contribution["types"],
            ["capabilities", "commands", "event_handlers", "background_services", "skills"],
        )
        self.assertEqual(contribution["skills"], ["gentle-checkin"])
        self.assertEqual(contribution["background_services"], ["quiet-checkin"])
        descriptor = self.host.capability_descriptors[CAPABILITY_ID]
        self.assertEqual(descriptor.effects, ("plugin_state",))
        self.assertNotIn("network", descriptor.effects)
        catalog = self.registry.prompt_catalog(available_tool_names={CAPABILITY_ID, "load_skill"})
        self.assertIn("gentle-checkin", catalog)
        self.assertNotIn("# Gentle Check-in", catalog)
        loaded = self.registry.load("gentle-checkin")
        self.assertIn("/checkin on <minutes>", loaded.content)
        self.assertEqual(loaded.source, f"plugin:{PLUGIN_ID}")

        bridge = PluginCapabilityToolBridge(self.host, config_base_dir=self.root)
        handlers = bridge.build_tool_handlers(
            client_context=ClientProtocolContext(
                requested_mode=ClientMode.QQ_TEXT,
                effective_mode=ClientMode.QQ_TEXT,
            )
        )
        configured = await asyncio.to_thread(
            handlers[CAPABILITY_ID].execute,
            call={
                "type": CAPABILITY_ID,
                "arguments": {
                    "action": "enable",
                    "idle_minutes": 12,
                    "recipient_qq_number": "123456789",
                },
            },
            context=ToolExecutionContext(
                profile_user_id="owner",
                session_id="qq:private:123456789",
                now_ts=2_000_000_000,
                visual_payload={},
                client_mode="qq_text",
            ),
        )
        self.assertEqual(configured.state_updates["adapter_capability_status"], "ok")
        self.assertIn('"idle_minutes": 12', configured.followup_context)
        self.assertNotIn("recipient_id", configured.followup_context)

        await self.host.stop()
        self.assertNotIn("gentle-checkin", self.registry.prompt_catalog())

    async def test_group_command_requires_authority_and_events_remain_internal(self) -> None:
        await self.host.start()
        broker = self.host.build_qq_command_broker()
        denied = await broker.dispatch(
            command="/checkin",
            args="on 5",
            qq_number=10001,
            group_id=20002,
            is_group=True,
            sender_role="member",
            profile_user_id="member",
            session_id="qq:group:20002",
        )
        self.assertTrue(denied.handled)
        self.assertEqual(denied.reason, "group_admin_required")

        enabled = await broker.dispatch(
            command="/checkin",
            args="on 5",
            qq_number=10002,
            group_id=20002,
            is_group=True,
            sender_role="admin",
            profile_user_id="admin",
            session_id="qq:group:20002",
            character_pack_id="reimu",
        )
        self.assertTrue(enabled.handled)
        self.assertFalse(enabled.reason)

        event_result = await self.host.build_event_broker().dispatch(
            PluginEventEnvelope(
                event_id="qq-message-1",
                event_type=GROUP_CONVERSATION_EVENT,
                source="channelcore-onebot",
                occurred_at=2_000_000_000,
                subject="qq:group:20002",
                fields=(("conversation_kind", "group"),),
            )
        )
        self.assertEqual(event_result.status, "observed")
        self.assertFalse(event_result.request_agent_turn)
        self.assertEqual(event_result.current_turn_events, ())
        self.assertEqual(event_result.timeline_events, ())

    async def test_one_checkin_per_quiet_period_and_new_activity_rearms(self) -> None:
        clock = [100]
        store = self.plugin_module.CheckinStore(self.root / "unit", clock=lambda: clock[0])
        store.configure(
            profile_user_id="owner",
            session_id="session-1",
            character_pack_id="reimu",
            recipient_id="user:12345",
            conversation_kind="direct",
            idle_seconds=60,
        )
        clock[0] = 160
        service = self.plugin_module.CheckinService(
            store,
            self.reasoning,
            self.notifications,
            poll_seconds=0.01,
        )
        await service.start(_OnePassController())
        self.assertEqual(len(self.reasoning.requests), 1)
        self.assertEqual(len(self.notifications.intents), 1)
        request = self.reasoning.requests[0]
        self.assertEqual(request.profile_user_id, "owner")
        self.assertEqual(request.session_id, "session-1")
        self.assertEqual(request.character_pack_id, "reimu")
        self.assertEqual(request.external_event.event_type, "companion.gentle_checkin_due")
        self.assertEqual(request.external_event.source, PLUGIN_ID)
        self.assertTrue(request.memory_idempotency_key.startswith(f"{PLUGIN_ID}:"))
        self.assertEqual(self.notifications.intents[0].recipient_id, "user:12345")
        self.assertTrue(
            self.notifications.intents[0].idempotency_key.startswith("gentle-checkin:")
        )

        await self.plugin_module.CheckinService(
            store,
            self.reasoning,
            self.notifications,
            poll_seconds=0.01,
        ).start(_OnePassController())
        self.assertEqual(len(self.notifications.intents), 1)

        await self.plugin_module.CheckinEventHandler(store).handle_event(
            PluginEventEnvelope(
                event_id="message-2",
                event_type=DIRECT_CONVERSATION_EVENT,
                source="desktop_pet",
                occurred_at=170,
                subject="session-1",
            )
        )
        clock[0] = 230
        await self.plugin_module.CheckinService(
            store,
            self.reasoning,
            self.notifications,
            poll_seconds=0.01,
        ).start(_OnePassController())
        self.assertEqual(len(self.notifications.intents), 2)

    async def test_activity_arriving_during_reasoning_suppresses_stale_delivery(self) -> None:
        clock = [100]
        store = self.plugin_module.CheckinStore(self.root / "race", clock=lambda: clock[0])
        store.configure(
            profile_user_id="owner",
            session_id="session-race",
            character_pack_id="reimu",
            recipient_id="user:54321",
            conversation_kind="direct",
            idle_seconds=60,
        )
        clock[0] = 160

        class _ReasoningWithActivity:
            async def analyze(_self, request: Any) -> PluginReasoningResult:
                del request
                store.mark_activity(session_id="session-race", event_id="new-message", occurred_at=161)
                return PluginReasoningResult(ok=True, status="completed", text="过时的问候")

        service = self.plugin_module.CheckinService(
            store,
            _ReasoningWithActivity(),
            self.notifications,
            poll_seconds=0.01,
        )
        await service.start(_OnePassController())
        self.assertEqual(self.notifications.intents, [])
        self.assertEqual(
            store.status(profile_user_id="owner", session_id="session-race")["last_status"],
            "superseded_by_activity",
        )

    async def test_corrupt_state_is_reported_instead_of_becoming_empty_success(self) -> None:
        storage = self.root / "corrupt"
        storage.mkdir(parents=True)
        (storage / "checkins.json").write_text("not-json", encoding="utf-8")
        store = self.plugin_module.CheckinStore(storage)
        with self.assertRaises(self.plugin_module.CheckinStateError) as raised:
            store.status(profile_user_id="owner", session_id="session")
        self.assertEqual(raised.exception.reason, "state_invalid")


if __name__ == "__main__":
    unittest.main()
