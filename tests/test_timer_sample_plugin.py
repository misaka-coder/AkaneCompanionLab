"""The SDK timer sample installed for real, then driven through the host."""

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

from akane_plugin import PluginInvocationContext, TurnReceipt
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_active_generation import ActivePluginGeneration, PluginGenerationSnapshot
from companion_v01.plugin_generation import PluginGenerationProcess
from companion_v01.plugin_storage import InstancePluginStorageService


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = PROJECT_ROOT / "examples" / "plugins" / "akane_timer"
PLUGIN_ID = "akane.timer"
CAPABILITY_ID = f"{PLUGIN_ID}.schedule.v1"


class _TurnRecorder:
    """Minimal turn router stub: records the real request payload."""

    shutdown_requested = False

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.received = asyncio.Event()

    async def request_turn(self, payload, **kwargs):
        print("TURN REQUEST:", payload, kwargs.get("binding"), file=sys.stderr)
        self.requests.append(payload)
        self.received.set()
        return TurnReceipt("turn-1", "completed", True, "", model_status="completed", delivery_status="delivered")

    def receipt(self, request_id, *, invocation=None, cancel=False):
        return TurnReceipt(request_id, "completed", True, "", model_status="completed", delivery_status="delivered")

    def capture_scope(self, context, delivery_id=""):
        return 0, ""

    def reconcile(self):
        return None

    def wait_request(self, request_id, delivery=None):
        return TurnReceipt(request_id, "completed", True, "", model_status="completed", delivery_status="delivered")

    def release_delivery(self, delivery_id, cancel=False):
        return None

    def cancel_delivery(self, delivery_id):
        return None


class InstalledTimerSampleTests(unittest.IsolatedAsyncioTestCase):
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
        wheels = tuple(wheelhouse.glob("akane_timer-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("timer_sample_wheel_not_built")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(cls.install_root), str(wheels[0])],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        sys.path.insert(0, str(cls.install_root))
        importlib.invalidate_caches()
        cls.plugin_module = importlib.import_module("akane_timer_plugin")

    @classmethod
    def tearDownClass(cls) -> None:
        sys.path.remove(str(cls.install_root))
        sys.modules.pop("akane_timer_plugin", None)
        importlib.invalidate_caches()
        cls._install_temp.cleanup()

    async def test_store_is_conversation_scoped_and_recovers_stuck_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            clock = [100]
            store = self.plugin_module.TimerStore(Path(temp), clock=lambda: clock[0])
            created = store.schedule(
                event_id="timer-one",
                session_id="session-a",
                conversation_ref="opaque-a",
                event_text="喝水",
                fields={"purpose": "hydration"},
                due_at=101,
            )
            self.assertEqual(created["status"], "scheduled")
            self.assertEqual(store.list_active(session_id="session-b"), ())
            clock[0] = 101
            due = store.claim_due()
            self.assertEqual(len(due), 1)
            self.assertEqual(due[0].conversation_ref, "opaque-a")
            self.assertEqual(store.claim_due(), ())
            clock[0] += self.plugin_module.FAILED_RETRY_SECONDS * 2
            self.assertEqual(store.recover_stuck(), 1)
            self.assertEqual(len(store.claim_due()), 1)

    async def test_real_isolated_generation_requests_a_normal_turn_when_due(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            turns = _TurnRecorder()
            generation = PluginGenerationProcess(
                project_root=PROJECT_ROOT,
                site_dir=self.install_root,
                plugin_id=PLUGIN_ID,
                work_dir=root / "work",
                plugin_storage_data_root=root / "data",
                plugin_storage_instance_id="timer-test",
            )
            active = ActivePluginGeneration()
            broker = active.build_event_broker()
            broker.bind_turn_router(turns)
            generation.bind_events_provider(broker)
            await asyncio.to_thread(generation.start)
            try:
                await active.publish(PluginGenerationSnapshot((PluginSelection(PLUGIN_ID, True),), (generation,)))
                self.assertEqual(
                    tuple(generation.capability_descriptors[CAPABILITY_ID].visible_in),
                    ("desktop", "qq"),
                )
                command = await generation.dispatch_qq_command(
                    command="/timer",
                    args="create 1 提醒我喝水",
                    qq_number=1906243651,
                    group_id=0,
                    is_group=False,
                    idempotency_key="timer-command-1",
                    profile_user_id="master",
                    session_id="master",
                    character_pack_id="reimu",
                    conversation_ref="opaque-current-conversation",
                )
                self.assertTrue(command.handled)
                self.assertFalse(command.reason)
                await asyncio.wait_for(turns.received.wait(), timeout=5.0)
                self.assertEqual(len(turns.requests), 1)
                request = turns.requests[0]
                self.assertEqual(request["data"]["event_type"], "akane.timer.due")
                self.assertEqual(request["data"]["source"], PLUGIN_ID)
                self.assertEqual(request["data"]["event_text"], "提醒我喝水")
                self.assertTrue(request["coalesce_key"].startswith(f"{PLUGIN_ID}:"))
                # The service has no conversation identity of its own: it may
                # only use the reference the host issued for this conversation.
                self.assertEqual(request["conversation_ref"], "opaque-current-conversation")
                await asyncio.sleep(1.0)
                self.assertEqual(len(turns.requests), 1)
            finally:
                await active.stop()
                if generation.running:
                    await asyncio.to_thread(generation.stop)

    async def test_schedule_tool_requires_host_conversation_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = self.plugin_module.TimerStore(Path(temp))
            schedule = self.plugin_module._schedule_tool(lambda: store)
            from types import SimpleNamespace

            missing = schedule(
                event_text="event",
                delay_seconds=1,
                ctx=SimpleNamespace(invocation=PluginInvocationContext("owner", "session", "qq_text", "")),
            )
            self.assertTrue(missing.is_error)
            self.assertEqual(missing.reason, "conversation_reference_required")
            created = schedule(
                event_text="event",
                delay_seconds=1,
                ctx=SimpleNamespace(invocation=PluginInvocationContext(
                    "owner", "session", "qq_text", "opaque-current-conversation",
                )),
            )
            self.assertFalse(created.is_error)
            self.assertEqual(created.status, "ok")
            self.assertEqual(created.value["events"][0]["event_text"], "event")
            too_long = schedule(
                event_text="x" * 2001,
                delay_seconds=1,
                ctx=SimpleNamespace(invocation=PluginInvocationContext(
                    "owner", "session", "qq_text", "opaque-current-conversation",
                )),
            )
            self.assertEqual(too_long.reason, "event_text_too_long")


if __name__ == "__main__":
    unittest.main()
