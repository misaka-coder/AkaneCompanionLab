"""The SDK gentle-check-in sample installed for real, then driven through the host."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Any

from akane_plugin import DIRECT_CONVERSATION_EVENT, GROUP_CONVERSATION_EVENT, PluginInvocationContext
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_storage import InstancePluginStorageService
from companion_v01.skill_runtime import SkillRegistry


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = PROJECT_ROOT / "examples" / "plugins" / "akane_gentle_checkin"
PLUGIN_ID = "akane.sample.gentle-checkin"
CAPABILITY_ID = f"{PLUGIN_ID}.configure.v1"


class _TurnRecorder:
    """Minimal background context: records the real request_turn payload."""

    shutdown_requested = False

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.slept = 0

    async def request_turn(self, reason, data=None, **kwargs):
        from akane_plugin import TurnReceipt

        self.requests.append({"reason": reason, "data": data, **kwargs})
        return TurnReceipt("turn-1", "completed", True, "", model_status="completed", delivery_status="delivered")

    async def sleep(self, seconds: float) -> bool:
        self.slept += 1
        self.shutdown_requested = True
        return False


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
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(wheelhouse), str(build_source)],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        wheels = tuple(wheelhouse.glob("akane_gentle_checkin-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("sample_plugin_wheel_not_built")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(cls.install_root), str(wheels[0])],
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
        self.host = PluginHost(
            (PluginSelection(PLUGIN_ID, True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
        )
        self.host.bind_plugin_storage_service(InstancePluginStorageService(self.root / "data", "test-instance"))
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

        self.assertEqual(status["status"], "active", status)
        contribution = status["plugins"][0]["contribution_snapshot"]
        self.assertEqual(contribution["skills"], ["gentle-checkin"])
        self.assertEqual(contribution["background_services"], ["quiet-checkin"])
        self.assertIn(CAPABILITY_ID, self.host.capability_descriptors)
        catalog = self.registry.prompt_catalog(available_tool_names={CAPABILITY_ID, "load_skill"})
        self.assertIn("gentle-checkin", catalog)
        loaded = self.registry.load("gentle-checkin")
        self.assertIn("/checkin on <minutes>", loaded.content)
        self.assertEqual(loaded.source, f"plugin:{PLUGIN_ID}")

        await self.host.stop()
        self.assertNotIn("gentle-checkin", self.registry.prompt_catalog())

    async def test_configure_tool_enables_and_reports_the_real_state(self) -> None:
        await self.host.start()
        context = PluginInvocationContext(
            "owner", "qq:private:123456789", "qq", character_pack_id="reimu",
            conversation_ref="opaque-current-conversation",
        )
        result = await self.host.invoke(
            CAPABILITY_ID, {"action": "enable", "idle_minutes": 12}, context=context,
        )
        self.assertFalse(result.is_error, result)
        self.assertEqual(result.value["configured"], True)
        self.assertEqual(result.value["enabled"], True)
        self.assertEqual(result.value["idle_minutes"], 12)

        status = await self.host.invoke(CAPABILITY_ID, {"action": "status"}, context=context)
        self.assertEqual(status.value["idle_minutes"], 12)
        disabled = await self.host.invoke(CAPABILITY_ID, {"action": "disable"}, context=context)
        self.assertEqual(disabled.value["enabled"], False)
        out_of_range = await self.host.invoke(
            CAPABILITY_ID, {"action": "enable", "idle_minutes": 0}, context=context,
        )
        self.assertEqual(out_of_range.status, "invalid_input")
        self.assertEqual(out_of_range.reason, "idle_minutes_out_of_range")

    async def test_group_command_requires_authority_and_activity_stays_silent(self) -> None:
        await self.host.start()
        broker = self.host.build_qq_command_broker()
        denied = await broker.dispatch(
            command="/checkin", args="on 5", qq_number=10001, group_id=20002, is_group=True,
            sender_role="member", profile_user_id="member", session_id="qq:group:20002",
            conversation_ref="opaque-group-ref",
        )
        self.assertTrue(denied.handled)
        self.assertEqual(denied.reason, "group_admin_required")

        enabled = await broker.dispatch(
            command="/checkin", args="on 5", qq_number=10002, group_id=20002, is_group=True,
            sender_role="admin", profile_user_id="admin", session_id="qq:group:20002",
            character_pack_id="reimu", conversation_ref="opaque-group-ref",
        )
        self.assertTrue(enabled.handled)
        self.assertFalse(enabled.reason)

        # A delivered message only re-arms the timer; it never asks for a model turn.
        event_broker = self.host.build_event_broker()
        context = PluginInvocationContext(
            "admin", "qq:group:20002", "qq", character_pack_id="reimu",
        )
        receipt = await event_broker.emit(
            GROUP_CONVERSATION_EVENT,
            {"session_id": "qq:group:20002", "channel": "qq"},
            context=context,
        )
        self.assertIn(receipt.status, {"unobserved", "accepted"}, receipt)

    async def test_background_service_requests_one_normal_turn_per_quiet_period(self) -> None:
        clock = [100]
        store = self.plugin_module.CheckinStore(self.root / "unit", clock=lambda: clock[0])
        store.configure(session_id="session-1", conversation_ref="opaque-session-1", idle_seconds=60)
        clock[0] = 160
        service = self.plugin_module._quiet_checkin_service(lambda: store)
        context = _TurnRecorder()
        await service(context)
        self.assertEqual(len(context.requests), 1)
        request = context.requests[0]
        self.assertEqual(request["data"]["event_type"], "companion.gentle_checkin_due")
        self.assertEqual(request["data"]["source"], PLUGIN_ID)
        self.assertEqual(request["data"]["quiet_seconds"], 60)
        self.assertTrue(request["coalesce_key"].startswith(f"{PLUGIN_ID}:"))
        self.assertEqual(store.status(profile_user_id="owner", session_id="session-1")["last_status"], "delivered")

        # The same quiet period is not announced twice.
        second = _TurnRecorder()
        await self.plugin_module._quiet_checkin_service(lambda: store)(second)
        self.assertEqual(second.requests, [])

    async def test_new_activity_rearms_the_next_checkin(self) -> None:
        clock = [100]
        store = self.plugin_module.CheckinStore(self.root / "rearm", clock=lambda: clock[0])
        store.configure(session_id="session-2", conversation_ref="opaque-session-2", idle_seconds=60)
        clock[0] = 160
        first = _TurnRecorder()
        await self.plugin_module._quiet_checkin_service(lambda: store)(first)
        self.assertEqual(len(first.requests), 1)
        self.assertEqual(store.mark_activity(session_id="session-2", event_id="message-2", occurred_at=170), 1)
        clock[0] = 230
        second = _TurnRecorder()
        await self.plugin_module._quiet_checkin_service(lambda: store)(second)
        self.assertEqual(len(second.requests), 1)

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
