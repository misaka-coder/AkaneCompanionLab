from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_api import (
    DIRECT_CONVERSATION_EVENT,
    GROUP_CONVERSATION_EVENT,
    PluginEventEnvelope,
)
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_host import PluginHost


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = PROJECT_ROOT / "examples" / "plugins" / "akane_poke_streak"
PLUGIN_ID = "akane.sample.poke-streak"


def _event(
    event_id: str,
    *,
    occurred_at: int,
    actor_id: str = "300",
    subject: str = "qq-group:200",
    event_type: str = GROUP_CONVERSATION_EVENT,
    trigger_reason: str = "qq_poke",
    source: str = "channelcore-onebot",
) -> PluginEventEnvelope:
    conversation_kind = "group" if event_type == GROUP_CONVERSATION_EVENT else "direct"
    return PluginEventEnvelope(
        event_id=event_id,
        event_type=event_type,
        source=source,
        occurred_at=occurred_at,
        subject=subject,
        fields=(
            ("event_kind", "notice"),
            ("trigger_reason", trigger_reason),
            ("conversation_kind", conversation_kind),
            ("conversation_id", "200"),
            ("actor_id", actor_id),
        ),
    )


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
        wheels = tuple(wheelhouse.glob("akane_poke_streak-*.whl"))
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

    @classmethod
    def tearDownClass(cls) -> None:
        sys.path.remove(str(cls.install_root))
        sys.modules.pop("akane_poke_streak", None)
        importlib.invalidate_caches()
        cls._temp.cleanup()

    async def _host(self, *, enabled: bool = True) -> PluginHost:
        host = PluginHost(
            (PluginSelection(PLUGIN_ID, enabled),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
        )
        await host.start()
        return host

    async def test_installed_event_only_plugin_activates_without_model_surface(self) -> None:
        host = await self._host()
        status = host.status_snapshot()

        self.assertEqual(status["status"], "active")
        self.assertEqual(status["capability_count"], 0)
        self.assertEqual(status["prompt_block_count"], 0)
        self.assertEqual(
            host.build_event_broker().registered_event_types,
            (DIRECT_CONVERSATION_EVENT, GROUP_CONVERSATION_EVENT),
        )
        self.assertEqual(status["plugins"][0]["contribution_snapshot"]["types"], ["event_handlers"])
        await host.stop()

    async def test_second_poke_adds_one_request_local_fact_and_duplicate_is_idempotent(self) -> None:
        host = await self._host()
        broker = host.build_event_broker()

        first = await broker.dispatch(_event("poke-1", occurred_at=1_000))
        second = await broker.dispatch(_event("poke-2", occurred_at=1_030))
        duplicate = await broker.dispatch(_event("poke-2", occurred_at=1_030))

        self.assertTrue(first.ok)
        self.assertEqual(first.current_turn_events, ())
        self.assertEqual(len(second.current_turn_events), 1)
        emitted = second.current_turn_events[0]
        self.assertEqual(emitted.event_type, "interaction.qq_poke_streak")
        self.assertEqual(dict(emitted.fields)["consecutive_count"], "2")
        self.assertTrue(second.request_agent_turn)
        self.assertEqual(duplicate.current_turn_events, ())
        self.assertFalse(duplicate.request_agent_turn)
        await host.stop()

    async def test_private_group_and_actor_streaks_are_isolated(self) -> None:
        host = await self._host()
        broker = host.build_event_broker()

        await broker.dispatch(_event("group-a-1", occurred_at=2_000))
        other_actor = await broker.dispatch(_event("group-b-1", occurred_at=2_010, actor_id="301"))
        private_first = await broker.dispatch(
            _event(
                "private-a-1",
                occurred_at=2_020,
                subject="qq-private:300",
                event_type=DIRECT_CONVERSATION_EVENT,
            )
        )
        group_second = await broker.dispatch(_event("group-a-2", occurred_at=2_030))

        self.assertEqual(other_actor.current_turn_events, ())
        self.assertEqual(private_first.current_turn_events, ())
        self.assertEqual(dict(group_second.current_turn_events[0].fields)["consecutive_count"], "2")
        await host.stop()

    async def test_non_poke_non_qq_and_expired_poke_are_silent(self) -> None:
        host = await self._host()
        broker = host.build_event_broker()

        normal_message = await broker.dispatch(
            _event("message-1", occurred_at=3_000, trigger_reason="mention")
        )
        foreign_source = await broker.dispatch(
            _event("poke-foreign", occurred_at=3_010, source="desktop-pet-next")
        )
        await broker.dispatch(_event("poke-old", occurred_at=3_020))
        expired = await broker.dispatch(_event("poke-late", occurred_at=3_111))

        self.assertEqual(normal_message.current_turn_events, ())
        self.assertEqual(foreign_source.current_turn_events, ())
        self.assertEqual(expired.current_turn_events, ())
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
