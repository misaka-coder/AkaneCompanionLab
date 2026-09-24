"""The SDK poke-streak sample installed for real, then driven through the host."""

from __future__ import annotations

import asyncio
from pathlib import Path
import importlib
import sys
import tempfile
import shutil
import subprocess
import unittest

from akane_plugin import POKE_CONVERSATION_EVENT, PluginInvocationContext
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_host import PluginHost


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = PROJECT_ROOT / "examples" / "plugins" / "akane_poke_streak"
PLUGIN_ID = "akane.sample.poke-streak"
OBSERVATION_KEY = "poke.streak"


def _poke_event(
    event_id: str,
    *,
    actor_id: str = "300",
    conversation_id: str = "200",
    conversation_kind: str = "group",
) -> dict:
    return {
        "event_kind": "poke",
        "channel": "qq",
        "conversation_kind": conversation_kind,
        "conversation_id": conversation_id,
        "actor_id": actor_id,
        "actor_label": "Olivia",
        "outcome_kind": "plain",
        "status": "ok",
        "reason": "",
    }


class InstalledPokeStreakSampleTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temp = tempfile.TemporaryDirectory()
        temp_root = Path(cls._temp.name)
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
        wheels = tuple(wheelhouse.glob("akane_poke_streak-*.whl"))
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

    @classmethod
    def tearDownClass(cls) -> None:
        sys.path.remove(str(cls.install_root))
        sys.modules.pop("akane_poke_streak", None)
        importlib.invalidate_caches()
        cls._temp.cleanup()

    async def _host(self, *, enabled: bool = True):
        host = PluginHost(
            (PluginSelection(PLUGIN_ID, enabled),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
        )
        await host.start()
        return host

    async def _bind(self, host):
        """Bind the conversation through the plugin's own public tool."""

        context = PluginInvocationContext("owner", "session", "qq", character_pack_id="akane")
        result = await host.invoke(f"{PLUGIN_ID}.bind", {}, context=context)
        self.assertFalse(result.is_error, result)
        self.assertEqual(result.value["status"], "bound")
        return context

    async def test_installed_event_only_plugin_activates_without_model_surface(self) -> None:
        host = await self._host()
        status = host.status_snapshot()

        self.assertEqual(status["status"], "active")
        self.assertEqual(host.build_event_broker().registered_event_types, (POKE_CONVERSATION_EVENT,))
        self.assertEqual(status["prompt_block_count"], 0)
        await host.stop()

    async def _settle(self, broker, dispatch_id: str):
        for _ in range(100):
            receipt = await broker.receipt(dispatch_id)
            if receipt.complete:
                return receipt
            await asyncio.sleep(0.02)
        self.fail(f"event dispatch did not settle: {dispatch_id}")

    async def _emit(self, broker, context, event: dict):
        receipt = await broker.emit(POKE_CONVERSATION_EVENT, event, context=context)
        self.assertEqual(receipt.status, "accepted", receipt)
        done = await self._settle(broker, receipt.dispatch_id)
        self.assertEqual(done.status, "completed", done)
        return done

    def _observation_text(self, broker):
        text, versions = broker.observations.decision_snapshot(
            profile_user_id="owner", session_id="session", character_pack_id="akane", owner=PLUGIN_ID,
        )
        return text, versions

    async def test_second_poke_updates_one_observation_and_never_requests_a_model(self) -> None:
        host = await self._host()
        broker = host.build_event_broker()
        context = await self._bind(host)

        # Each test uses its own conversation/actor pair so the plugin's own
        # in-memory streak state stays isolated between cases.
        await self._emit(broker, context, _poke_event("first-1", conversation_id="100", actor_id="100"))
        self.assertEqual(self._observation_text(broker)[1], {})
        await self._emit(broker, context, _poke_event("first-2", conversation_id="100", actor_id="100"))
        text, versions = self._observation_text(broker)
        self.assertIn(OBSERVATION_KEY, versions)
        self.assertIn('"consecutive_count": 2', text)
        self.assertIn('"actor_id": "100"', text)
        # No prompt block and no implicit model request.
        self.assertEqual(host.status_snapshot()["prompt_block_count"], 0)
        await host.stop()

    async def test_streaks_are_isolated_per_conversation_and_actor(self) -> None:
        host = await self._host()
        broker = host.build_event_broker()
        context = await self._bind(host)

        await self._emit(broker, context, _poke_event("group-1", conversation_id="201", actor_id="301"))
        await self._emit(broker, context, _poke_event("group-other", conversation_id="201", actor_id="302"))
        await self._emit(broker, context, _poke_event("private-1", conversation_id="301", actor_id="301",
                                                      conversation_kind="private"))
        await self._emit(broker, context, _poke_event("group-2", conversation_id="201", actor_id="301"))

        text, versions = self._observation_text(broker)
        self.assertIn(OBSERVATION_KEY, versions)
        # Only the repeated group/actor pair produced a fact.
        self.assertIn('"consecutive_count": 2', text)
        self.assertEqual(text.count("consecutive_count"), 1)
        await host.stop()

    async def test_disabled_installed_plugin_has_no_runtime_contribution(self) -> None:
        host = await self._host(enabled=False)
        status = host.status_snapshot()

        self.assertEqual(status["status"], "active")
        self.assertEqual(status["plugins"][0]["status"], "disabled")
        self.assertEqual(status["capability_count"], 0)
        self.assertEqual(host.build_event_broker().registered_event_types, ())
        await host.stop()


if __name__ == "__main__":
    unittest.main()
