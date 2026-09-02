from __future__ import annotations

import asyncio
import unittest
from types import MappingProxyType
from typing import Any, Mapping

from capcore import CapabilityDescriptor, CapabilityResult, InvocationContext

from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_active_generation import (
    ActivePluginGeneration,
    PluginActiveGenerationError,
    PluginGenerationSnapshot,
)
from companion_v01.plugin_api import (
    PluginEventEnvelope,
    PluginExternalEvent,
    PluginHookEnvelope,
    PluginOutboundDecoration,
    PluginQQCommandResult,
    PluginToolCallSnapshot,
)
from companion_v01.plugin_events import PluginEventDispatchResult
from companion_v01.plugin_hooks import PluginHookDispatchResult


def _descriptor(capability_id: str) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id=capability_id,
        display_name=capability_id,
        short_hint="Test one generation route.",
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


class FakeGeneration:
    def __init__(
        self,
        plugin_id: str,
        capability_id: str,
        value: str,
        *,
        command: str = "",
        running: bool = True,
        started: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
        stop_result: Mapping[str, Any] | None = None,
        event_result: PluginEventDispatchResult | None = None,
        hook_result: PluginHookDispatchResult | None = None,
        command_result: PluginQQCommandResult | None = None,
    ) -> None:
        self.plugin_id = plugin_id
        self.generation_id = f"generation-{value}"
        self.running = running
        self.capability_descriptors = MappingProxyType(
            {capability_id: _descriptor(capability_id)}
        )
        self.registered_event_types = ("message.received",)
        self.registered_hook_types = ("before_tool_call",)
        self.registered_background_service_ids = ()
        self.registered_qq_commands = (command,) if command else ()
        self.value = value
        self.started = started
        self.release = release
        self.stop_result = dict(stop_result or {"ok": True, "status": "stopped"})
        self.stop_count = 0
        self.event_result = event_result or PluginEventDispatchResult(True, "observed")
        self.hook_result = hook_result or PluginHookDispatchResult(True, "observed")
        self.command_result = command_result or PluginQQCommandResult(
            handled=True,
            reply_text=value,
        )

    def stable_system_prompt_blocks(self) -> tuple[str, ...]:
        return (f"prompt:{self.plugin_id}:{self.value}",)

    def public_status_snapshot(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "enabled": True,
            "status": "active" if self.running else "unavailable",
            "reason": "" if self.running else "plugin_generation_unavailable",
            "plugin_version": self.value,
            "contribution_snapshot": {
                "plugin_id": self.plugin_id,
                "capabilities": list(self.capability_descriptors),
            },
        }

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
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        return CapabilityResult(
            is_error=False,
            status="ok",
            content={"generation": self.value},
        )

    async def dispatch(
        self,
        event: PluginEventEnvelope | PluginHookEnvelope,
    ) -> PluginEventDispatchResult | PluginHookDispatchResult:
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        return self.hook_result if isinstance(event, PluginHookEnvelope) else self.event_result

    async def dispatch_qq_command(self, **command_args: Any) -> PluginQQCommandResult:
        del command_args
        return self.command_result

    def stop(self) -> dict[str, Any]:
        self.stop_count += 1
        self.running = False
        return dict(self.stop_result)


class PluginGenerationSnapshotTests(unittest.TestCase):
    def test_complete_snapshot_routes_multiple_plugins_and_keeps_disabled_selection(self) -> None:
        first = FakeGeneration("akane.test.first", "akane.test.first.read.v1", "first")
        second = FakeGeneration(
            "akane.test.second",
            "akane.test.second.read.v1",
            "second",
            command="/second",
        )
        selections = (
            PluginSelection(first.plugin_id, True),
            PluginSelection("akane.test.disabled", False),
            PluginSelection(second.plugin_id, True),
        )

        snapshot = PluginGenerationSnapshot(selections, (second, first))

        self.assertEqual(snapshot.processes, (first, second))
        self.assertEqual(
            snapshot.capability_ids,
            ("akane.test.first.read.v1", "akane.test.second.read.v1"),
        )
        self.assertIs(snapshot.process_for_qq_command("/SECOND"), second)
        self.assertEqual(snapshot.processes_for_event("message.received"), (first, second))
        self.assertEqual(
            snapshot.stable_system_prompt_blocks(),
            ("prompt:akane.test.first:first", "prompt:akane.test.second:second"),
        )

    def test_snapshot_rejects_incomplete_or_conflicting_candidate(self) -> None:
        active = FakeGeneration("akane.test.first", "akane.test.shared.v1", "active")
        conflict = FakeGeneration("akane.test.second", "akane.test.shared.v1", "conflict")
        not_ready = FakeGeneration(
            "akane.test.first",
            "akane.test.first.read.v1",
            "not-ready",
            running=False,
        )

        with self.assertRaisesRegex(
            PluginActiveGenerationError,
            "duplicate_plugin_capability",
        ):
            PluginGenerationSnapshot(
                (
                    PluginSelection(active.plugin_id, True),
                    PluginSelection(conflict.plugin_id, True),
                ),
                (active, conflict),
            )
        with self.assertRaisesRegex(
            PluginActiveGenerationError,
            "plugin_generation_not_ready",
        ):
            PluginGenerationSnapshot(
                (PluginSelection(not_ready.plugin_id, True),),
                (not_ready,),
            )


class ActivePluginGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_snapshot_composes_events_hooks_and_qq_commands(self) -> None:
        event_a = PluginExternalEvent("test.a", (("value", "a"),), source="first")
        event_b = PluginExternalEvent("test.b", (("value", "b"),), source="second")
        decoration = PluginOutboundDecoration(text_prefix="[second]")
        first = FakeGeneration(
            "akane.test.first",
            "akane.test.first.read.v1",
            "first",
            event_result=PluginEventDispatchResult(
                True,
                "observed",
                current_turn_events=(event_a,),
            ),
            hook_result=PluginHookDispatchResult(
                True,
                "observed",
                diagnostics=(("akane.test.first", "seen"),),
            ),
        )
        second = FakeGeneration(
            "akane.test.second",
            "akane.test.second.read.v1",
            "second",
            command="/second",
            event_result=PluginEventDispatchResult(
                True,
                "observed",
                timeline_events=(event_b,),
                request_agent_turn=True,
            ),
            hook_result=PluginHookDispatchResult(
                True,
                "observed",
                outbound_decorations=(("akane.test.second", decoration),),
            ),
        )
        runtime = ActivePluginGeneration()
        await runtime.publish(
            PluginGenerationSnapshot(
                (
                    PluginSelection(first.plugin_id, True),
                    PluginSelection(second.plugin_id, True),
                ),
                (second, first),
            )
        )

        event_broker = runtime.build_event_broker()
        event_result = await event_broker.dispatch(
            PluginEventEnvelope("event-1", "message.received", "test", 1)
        )
        hook_broker = runtime.build_hook_broker()
        hook_result = await hook_broker.dispatch(
            PluginHookEnvelope(
                "hook-1",
                "before_tool_call",
                1,
                "tool",
                PluginToolCallSnapshot("call-1", "read", "test", "u", "s", "c", "{}"),
            )
        )
        command_broker = runtime.build_qq_command_broker()
        command_result = await command_broker.dispatch(
            command="/SECOND",
            args="",
            qq_number=1,
            group_id=2,
            is_group=True,
        )

        self.assertEqual(event_result.current_turn_events, (event_a,))
        self.assertEqual(event_result.timeline_events, (event_b,))
        self.assertTrue(event_result.request_agent_turn)
        self.assertEqual(hook_result.diagnostics, (("akane.test.first", "seen"),))
        self.assertEqual(
            hook_result.outbound_decorations,
            (("akane.test.second", decoration),),
        )
        self.assertEqual(command_result.reply_text, "second")

    async def test_event_lease_keeps_old_process_alive_during_generation_switch(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        old = FakeGeneration(
            "akane.test.event",
            "akane.test.event.read.v1",
            "old",
            started=started,
            release=release,
        )
        new = FakeGeneration(
            "akane.test.event",
            "akane.test.event.read.v1",
            "new",
        )
        selections = (PluginSelection(old.plugin_id, True),)
        runtime = ActivePluginGeneration()
        await runtime.publish(PluginGenerationSnapshot(selections, (old,)))
        dispatch = asyncio.create_task(
            runtime.dispatch_event(
                PluginEventEnvelope("event-1", "message.received", "test", 1)
            )
        )
        await started.wait()
        switching = asyncio.create_task(
            runtime.publish(PluginGenerationSnapshot(selections, (new,)))
        )
        await asyncio.sleep(0)

        self.assertFalse(switching.done())
        self.assertEqual(old.stop_count, 0)
        release.set()
        await dispatch
        await switching
        self.assertEqual(old.stop_count, 1)

    async def test_publish_routes_new_work_immediately_and_drains_old_work(self) -> None:
        capability_id = "akane.test.switch.read.v1"
        old_started = asyncio.Event()
        old_release = asyncio.Event()
        old = FakeGeneration(
            "akane.test.switch",
            capability_id,
            "old",
            started=old_started,
            release=old_release,
        )
        new = FakeGeneration("akane.test.switch", capability_id, "new")
        selections = (PluginSelection(old.plugin_id, True),)
        runtime = ActivePluginGeneration()
        await runtime.publish(PluginGenerationSnapshot(selections, (old,)))

        old_call = asyncio.create_task(
            runtime.invoke(capability_id, {}, context=InvocationContext())
        )
        await old_started.wait()
        switching = asyncio.create_task(
            runtime.publish(PluginGenerationSnapshot(selections, (new,)))
        )
        await asyncio.sleep(0)

        new_result = await runtime.invoke(
            capability_id,
            {},
            context=InvocationContext(),
        )
        self.assertEqual(new_result.content, {"generation": "new"})
        self.assertFalse(switching.done())
        self.assertEqual(old.stop_count, 0)

        old_release.set()
        old_result = await old_call
        switched = await switching

        self.assertEqual(old_result.content, {"generation": "old"})
        self.assertTrue(switched["ok"])
        self.assertEqual(switched["generation"], 2)
        self.assertEqual(old.stop_count, 1)
        self.assertEqual(new.stop_count, 0)

    async def test_invalid_candidate_never_replaces_the_active_snapshot(self) -> None:
        capability_id = "akane.test.stable.read.v1"
        active = FakeGeneration("akane.test.stable", capability_id, "active")
        runtime = ActivePluginGeneration()
        selections = (PluginSelection(active.plugin_id, True),)
        await runtime.publish(PluginGenerationSnapshot(selections, (active,)))
        broken = FakeGeneration(
            active.plugin_id,
            capability_id,
            "broken",
            running=False,
        )

        with self.assertRaises(PluginActiveGenerationError):
            PluginGenerationSnapshot(selections, (broken,))

        result = await runtime.invoke(capability_id, {}, context=InvocationContext())
        self.assertEqual(result.content, {"generation": "active"})
        self.assertEqual(active.stop_count, 0)

    async def test_candidate_that_exits_before_publish_never_replaces_active(self) -> None:
        capability_id = "akane.test.race.read.v1"
        active = FakeGeneration("akane.test.race", capability_id, "active")
        candidate_process = FakeGeneration("akane.test.race", capability_id, "candidate")
        selections = (PluginSelection(active.plugin_id, True),)
        runtime = ActivePluginGeneration()
        await runtime.publish(PluginGenerationSnapshot(selections, (active,)))
        candidate = PluginGenerationSnapshot(selections, (candidate_process,))
        candidate_process.running = False

        with self.assertRaisesRegex(
            PluginActiveGenerationError,
            "plugin_generation_not_ready",
        ):
            await runtime.publish(candidate)

        result = await runtime.invoke(capability_id, {}, context=InvocationContext())
        self.assertEqual(result.content, {"generation": "active"})
        self.assertEqual(active.stop_count, 0)

    async def test_active_process_exit_is_visible_as_degraded(self) -> None:
        capability_id = "akane.test.health.read.v1"
        process = FakeGeneration("akane.test.health", capability_id, "active")
        selections = (PluginSelection(process.plugin_id, True),)
        runtime = ActivePluginGeneration()
        await runtime.publish(PluginGenerationSnapshot(selections, (process,)))

        process.running = False
        status = runtime.status_snapshot()

        self.assertFalse(status["ok"])
        self.assertEqual(status["status"], "degraded")
        self.assertEqual(status["reason"], "plugin_runtime_failed")

    async def test_cleanup_failure_is_reported_without_rolling_back_new_generation(self) -> None:
        capability_id = "akane.test.cleanup.read.v1"
        old = FakeGeneration(
            "akane.test.cleanup",
            capability_id,
            "old",
            stop_result={"ok": False, "status": "failed", "reason": "still_running"},
        )
        new = FakeGeneration("akane.test.cleanup", capability_id, "new")
        selections = (PluginSelection(old.plugin_id, True),)
        runtime = ActivePluginGeneration()
        await runtime.publish(PluginGenerationSnapshot(selections, (old,)))

        switched = await runtime.publish(PluginGenerationSnapshot(selections, (new,)))
        result = await runtime.invoke(capability_id, {}, context=InvocationContext())

        self.assertFalse(switched["ok"])
        self.assertEqual(switched["status"], "active")
        self.assertEqual(switched["reason"], "old_generation_stop_failed")
        self.assertEqual(result.content, {"generation": "new"})

    async def test_cancelled_publish_still_drains_the_retired_generation(self) -> None:
        capability_id = "akane.test.cancel.read.v1"
        old_started = asyncio.Event()
        old_release = asyncio.Event()
        old = FakeGeneration(
            "akane.test.cancel",
            capability_id,
            "old",
            started=old_started,
            release=old_release,
        )
        new = FakeGeneration("akane.test.cancel", capability_id, "new")
        selections = (PluginSelection(old.plugin_id, True),)
        runtime = ActivePluginGeneration()
        await runtime.publish(PluginGenerationSnapshot(selections, (old,)))
        old_call = asyncio.create_task(
            runtime.invoke(capability_id, {}, context=InvocationContext())
        )
        await old_started.wait()
        switching = asyncio.create_task(
            runtime.publish(PluginGenerationSnapshot(selections, (new,)))
        )
        await asyncio.sleep(0)

        switching.cancel()
        old_release.set()
        await old_call
        with self.assertRaises(asyncio.CancelledError):
            await switching

        self.assertEqual(old.stop_count, 1)
        result = await runtime.invoke(capability_id, {}, context=InvocationContext())
        self.assertEqual(result.content, {"generation": "new"})


if __name__ == "__main__":
    unittest.main()
