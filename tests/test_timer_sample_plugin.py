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

from companion_v01.plugin_api import PluginAgentEventResult, PluginInvocationContext
from companion_v01.plugin_generation import PluginGenerationProcess


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = PROJECT_ROOT / "examples" / "plugins" / "akane_timer"
PLUGIN_ID = "akane.timer"
CAPABILITY_ID = f"{PLUGIN_ID}.schedule.v1"


class _RecordingAgentEventPort:
    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.received = asyncio.Event()

    async def submit(self, request: Any) -> PluginAgentEventResult:
        self.requests.append(request)
        self.received.set()
        return PluginAgentEventResult(
            ok=True,
            status="completed",
            delivery_status="delivered",
        )


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
        wheels = tuple(wheelhouse.glob("akane_timer-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("timer_sample_wheel_not_built")
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

    async def test_real_isolated_generation_submits_due_event_to_host(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            agent_events = _RecordingAgentEventPort()
            generation = PluginGenerationProcess(
                project_root=PROJECT_ROOT,
                site_dir=self.install_root,
                plugin_id=PLUGIN_ID,
                work_dir=root / "work",
                plugin_storage_data_root=root / "data",
                plugin_storage_instance_id="timer-test",
            )
            generation.bind_agent_event_port(agent_events)
            generation.start()
            try:
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
                await asyncio.wait_for(agent_events.received.wait(), timeout=5.0)
                self.assertEqual(len(agent_events.requests), 1)
                request = agent_events.requests[0]
                self.assertEqual(request.conversation_ref, "opaque-current-conversation")
                self.assertEqual(request.event.event_type, "akane.timer.due")
                self.assertEqual(dict(request.event.fields)["event_text"], "提醒我喝水")
                self.assertEqual(request.delivery, "timeline")
                self.assertTrue(request.memory_idempotency_key.startswith("akane.timer:"))
                await asyncio.sleep(1.0)
                self.assertEqual(len(agent_events.requests), 1)
            finally:
                generation.stop()

    async def test_capability_requires_host_conversation_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = self.plugin_module.TimerStore(Path(temp))
            capability = self.plugin_module._schedule_capability(store)
            missing = capability(
                event_text="event",
                delay_seconds=1,
                ctx=PluginInvocationContext("owner", "session", "qq_text", ""),
            )
            self.assertTrue(missing.is_error)
            self.assertEqual(missing.reason, "conversation_reference_required")
            created = capability(
                event_text="event",
                delay_seconds=1,
                ctx=PluginInvocationContext(
                    "owner",
                    "session",
                    "qq_text",
                    "opaque-current-conversation",
                ),
            )
            self.assertFalse(created.is_error)
            self.assertEqual(created.status, "ok")


if __name__ == "__main__":
    unittest.main()
