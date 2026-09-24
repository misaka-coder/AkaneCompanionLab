from __future__ import annotations

import asyncio
import unittest
from types import MappingProxyType
from typing import Any, Mapping

from capcore import CapabilityDescriptor, CapabilityResult, InvocationContext

from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_active_generation import PluginGenerationSnapshot
from companion_v01.plugin_generation_candidate import PluginGenerationCandidateError
from companion_v01.plugin_generation_runtime import PluginGenerationRuntime


PLUGIN_ID = "akane.test.generation-runtime"
CAPABILITY_ID = f"{PLUGIN_ID}.read.v1"


def _descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id=CAPABILITY_ID,
        display_name="generation runtime test",
        short_hint="Inspect the active generation.",
        visible_in=("diagnostics",),
        prompt_exposed=True,
        risk="low",
        confirm="never",
        effects=(),
        trigger=None,
        inputs=(),
        outputs=(),
        raw={},
    )


class _Process:
    def __init__(self, value: str) -> None:
        self.plugin_id = PLUGIN_ID
        self.generation_id = f"generation-{value}"
        self.running = True
        self.value = value
        self.stop_count = 0
        self.capability_descriptors = MappingProxyType({CAPABILITY_ID: _descriptor()})
        self.registered_event_types = ()
        self.registered_hook_types = ()
        self.registered_background_service_ids = ()
        self.registered_qq_commands = ()

    def public_status_snapshot(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "enabled": True,
            "status": "active",
            "reason": "",
            "plugin_version": self.value,
        }

    def stable_system_prompt_blocks(self) -> tuple[str, ...]:
        return (f"prompt:{self.value}",)

    def skill_roots(self) -> tuple:
        return ()

    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        *,
        context: InvocationContext,
    ) -> CapabilityResult:
        del capability_id, args, context
        return CapabilityResult(is_error=False, status="ok", content={"value": self.value})

    async def dispatch(self, event: Any) -> Any:
        raise AssertionError(event)

    async def dispatch_qq_command(self, **command_args: Any) -> Any:
        raise AssertionError(command_args)

    def stop(self) -> dict[str, Any]:
        self.stop_count += 1
        self.running = False
        return {"ok": True, "status": "stopped"}


class _Builder:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[PluginSelection, ...]] = []
        self.managed_artifact_sink = None
        self.notification_port = None

    async def build(
        self,
        selections: tuple[PluginSelection, ...],
    ) -> PluginGenerationSnapshot:
        self.calls.append(selections)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return PluginGenerationSnapshot(selections, (outcome,) if outcome is not None else ())


class PluginGenerationRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_and_restart_switch_all_consumers_to_the_new_generation(self) -> None:
        old = _Process("old")
        new = _Process("new")
        builder = _Builder([old, new])
        selections = (PluginSelection(PLUGIN_ID, True),)
        runtime = PluginGenerationRuntime(selections, candidate_builder=builder)

        started = await runtime.start()
        first = await runtime.invoke(
            CAPABILITY_ID,
            {},
            context=InvocationContext(client_mode="diagnostics"),
        )
        restarted = await runtime.restart()
        second = await runtime.invoke(
            CAPABILITY_ID,
            {},
            context=InvocationContext(client_mode="diagnostics"),
        )

        self.assertTrue(started["published"])
        self.assertTrue(restarted["published"])
        self.assertEqual(first.content, {"value": "old"})
        self.assertEqual(second.content, {"value": "new"})
        self.assertEqual(old.stop_count, 1)
        self.assertEqual(runtime.stable_system_prompt_blocks(), ("prompt:new",))
        self.assertEqual(runtime.code_reload_mode, "atomic_generation_switch")

    async def test_rejected_candidate_keeps_the_previous_generation_and_selection(self) -> None:
        old = _Process("old")
        builder = _Builder(
            [
                old,
                PluginGenerationCandidateError(
                    "plugin_probe_failed",
                    plugin_id=PLUGIN_ID,
                ),
            ]
        )
        selections = (PluginSelection(PLUGIN_ID, True),)
        runtime = PluginGenerationRuntime(selections, candidate_builder=builder)
        await runtime.start()

        failed = await runtime.reconfigure((PluginSelection(PLUGIN_ID, False),))
        result = await runtime.invoke(
            CAPABILITY_ID,
            {},
            context=InvocationContext(client_mode="diagnostics"),
        )

        self.assertFalse(failed["published"])
        self.assertEqual(failed["status"], "candidate_rejected")
        self.assertEqual(failed["plugins"][0]["status"], "unavailable")
        self.assertFalse(failed["plugins"][0]["enabled"])
        self.assertEqual(runtime.selections, selections)
        self.assertEqual(result.content, {"value": "old"})
        self.assertEqual(old.stop_count, 0)

    async def test_initial_candidate_failure_is_degraded_but_remains_manageable(self) -> None:
        builder = _Builder(
            [PluginGenerationCandidateError("plugin_not_installed", plugin_id=PLUGIN_ID)]
        )
        selections = (PluginSelection(PLUGIN_ID, True),)
        runtime = PluginGenerationRuntime(selections, candidate_builder=builder)

        result = await runtime.start()

        self.assertEqual(result["status"], "degraded")
        self.assertFalse(result["published"])
        self.assertEqual(runtime.state, "degraded")
        self.assertIs(runtime.runtime_loop, asyncio.get_running_loop())
        self.assertEqual(result["plugins"][0]["reason"], "plugin_not_installed")

    async def test_bindings_feed_future_candidates_and_stop_is_idempotent(self) -> None:
        builder = _Builder([None])
        runtime = PluginGenerationRuntime((), candidate_builder=builder)
        artifact = object()
        notification = object()
        agent_event = object()

        runtime.bind_managed_artifact_sink(artifact)
        runtime.bind_notification_port(notification)
        runtime.bind_turn_router(agent_event)
        await runtime.start()
        first = await runtime.stop()
        second = await runtime.stop()

        self.assertIs(builder.managed_artifact_sink, artifact)
        self.assertIs(builder.notification_port, notification)
        self.assertEqual(first["status"], "stopped")
        self.assertEqual(second["status"], "stopped")
        self.assertEqual(second["configured_plugin_count"], 0)


if __name__ == "__main__":
    unittest.main()
