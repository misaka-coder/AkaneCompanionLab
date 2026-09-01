from __future__ import annotations

import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from typing import Any, Callable

from companion_v01.engine import AkaneMemoryEngine
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_api import (
    AFTER_TOOL_CALL_HOOK,
    AKANE_PLUGIN_API_VERSION,
    BEFORE_TOOL_CALL_HOOK,
    HOOK_SUBSCRIBE_PERMISSION,
    PluginHookEnvelope,
    PluginHookResult,
    PluginManifest,
    PluginToolCallSnapshot,
)
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_hooks import (
    PluginHookBroker,
    _PluginHookRegistration,
)
from companion_v01.plugin_host import PluginHost
from companion_v01.tool_handlers.core import ToolExecutionResult
from companion_v01.tool_invocation import TOOL_INVOCATION_ID_FIELD, TOOL_SOURCE_FIELD


PLUGIN_ID = "akane.test.hooks"


class _Handler:
    def __init__(self, result: PluginHookResult | None = None) -> None:
        self.result = result or PluginHookResult()
        self.received: list[PluginHookEnvelope] = []

    async def handle_hook(self, hook: PluginHookEnvelope) -> PluginHookResult:
        self.received.append(hook)
        return self.result


def _hook(hook_type: str = BEFORE_TOOL_CALL_HOOK) -> PluginHookEnvelope:
    return PluginHookEnvelope(
        hook_id="call-1:before",
        hook_type=hook_type,
        occurred_at=int(time.time()),
        subject="tool:read_workspace",
        payload=PluginToolCallSnapshot(
            invocation_id="call-1",
            tool_name="read_workspace",
            source="native_openai",
            profile_user_id="profile",
            session_id="session",
            character_pack_id="character",
            arguments_json='{"path":"README.md"}',
        ),
    )


class PluginHookBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_returns_typed_diagnostics(self) -> None:
        handler = _Handler(PluginHookResult(diagnostics=("cache.observed",)))
        broker = PluginHookBroker(
            (_PluginHookRegistration(PLUGIN_ID, BEFORE_TOOL_CALL_HOOK, handler),)
        )

        result = await broker.dispatch(_hook())

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "observed")
        self.assertEqual(result.diagnostics, ((PLUGIN_ID, "cache.observed"),))
        self.assertEqual(handler.received[0].payload.arguments_json, '{"path":"README.md"}')
        self.assertEqual(
            broker.status_snapshot()["last_diagnostics"],
            [{"plugin_id": PLUGIN_ID, "code": "cache.observed"}],
        )

    async def test_handler_failure_does_not_discard_sibling_observation(self) -> None:
        class Broken:
            async def handle_hook(self, hook: PluginHookEnvelope) -> PluginHookResult:
                del hook
                raise RuntimeError("boom")

        working = _Handler(PluginHookResult(diagnostics=("audit.recorded",)))
        broker = PluginHookBroker(
            (
                _PluginHookRegistration("broken", BEFORE_TOOL_CALL_HOOK, Broken()),
                _PluginHookRegistration("working", BEFORE_TOOL_CALL_HOOK, working),
            )
        )

        result = await broker.dispatch(_hook())

        self.assertFalse(result.ok)
        self.assertEqual(result.failures, (("broken", "handler_exception"),))
        self.assertEqual(result.diagnostics, (("working", "audit.recorded"),))
        self.assertEqual(broker.status_snapshot()["dispatch_failure_count"], 1)

    async def test_sync_engine_consumer_dispatches_on_lifecycle_loop(self) -> None:
        handler = _Handler()
        runtime_loop = asyncio.get_running_loop()
        broker = PluginHookBroker(
            (_PluginHookRegistration(PLUGIN_ID, BEFORE_TOOL_CALL_HOOK, handler),),
            runtime_loop_provider=lambda: runtime_loop,
        )

        result = await asyncio.to_thread(broker.dispatch_from_consumer, _hook())

        self.assertTrue(result.ok)
        self.assertEqual(len(handler.received), 1)


class _Distribution:
    version = "0.1.0"
    metadata = {"Name": "akane-test-hooks"}

    def read_text(self, _filename: str) -> str | None:
        return None


class _EntryPoint:
    name = PLUGIN_ID
    dist = _Distribution()

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory

    def load(self) -> Callable[[], Any]:
        return self._factory


class PluginHookHostTests(unittest.IsolatedAsyncioTestCase):
    async def test_hook_only_plugin_activates_and_publishes_real_snapshot(self) -> None:
        def factory() -> Any:
            class Plugin:
                manifest = PluginManifest(
                    plugin_id=PLUGIN_ID,
                    plugin_version="0.1.0",
                    plugin_api_version=AKANE_PLUGIN_API_VERSION,
                    permissions=(HOOK_SUBSCRIBE_PERMISSION,),
                )

                def register(self, registrar: Any) -> None:
                    registrar.add_hook_handler(BEFORE_TOOL_CALL_HOOK, _Handler())
                    registrar.add_hook_handler(AFTER_TOOL_CALL_HOOK, _Handler())

            return Plugin()

        host = PluginHost(
            (PluginSelection(plugin_id=PLUGIN_ID, enabled=True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=lambda: (_EntryPoint(factory),),
        )

        status = await host.start()
        broker = host.build_hook_broker()

        self.assertEqual(status["status"], "active")
        self.assertEqual(status["hook_handler_count"], 2)
        self.assertEqual(
            broker.registered_hook_types,
            (AFTER_TOOL_CALL_HOOK, BEFORE_TOOL_CALL_HOOK),
        )
        snapshot = status["plugins"][0]["contribution_snapshot"]
        self.assertEqual(snapshot["types"], ["hooks"])
        self.assertEqual(snapshot["hooks"], [AFTER_TOOL_CALL_HOOK, BEFORE_TOOL_CALL_HOOK])
        await host.stop()


class _RecordingBroker:
    def __init__(self) -> None:
        self.received: list[PluginHookEnvelope] = []

    def observes(self, hook_type: str) -> bool:
        return hook_type in {BEFORE_TOOL_CALL_HOOK, AFTER_TOOL_CALL_HOOK}

    def dispatch_from_consumer(self, hook: PluginHookEnvelope) -> None:
        self.received.append(hook)


class PluginHookEngineTests(unittest.TestCase):
    def test_tool_execution_publishes_sanitized_before_and_real_after_snapshots(self) -> None:
        engine = object.__new__(AkaneMemoryEngine)
        broker = _RecordingBroker()
        engine.plugin_hook_broker = broker
        expected = ToolExecutionResult(
            tool_type="read_workspace",
            stream_events=[{"type": "workspace_read", "status": "succeeded"}],
            followup_context="真实内容",
        )
        engine._execute_tool_call = lambda **_kwargs: expected

        result = engine._execute_tool_call_with_hooks(
            call={
                "type": "read_workspace",
                "path": "README.md",
                "access_token": "do-not-copy",
                "nested": {"api_key": "also-secret", "mode": "read"},
                TOOL_SOURCE_FIELD: "native_openai",
                TOOL_INVOCATION_ID_FIELD: "call-42",
                "_tool_private": "hidden",
            },
            final_output={},
            profile_user_id="profile",
            session_id="session",
            character_pack_id="character",
            now_ts=int(time.time()),
            current_user_source_id="source",
            client_context=SimpleNamespace(),
            memory_exclude_source_ids=[],
            request_context={},
        )

        self.assertIs(result, expected)
        self.assertEqual([item.hook_type for item in broker.received], [BEFORE_TOOL_CALL_HOOK, AFTER_TOOL_CALL_HOOK])
        before = broker.received[0].payload
        self.assertEqual(
            json.loads(before.arguments_json),
            {
                "access_token": "<configured>",
                "nested": {"api_key": "<configured>", "mode": "read"},
                "path": "README.md",
            },
        )
        after = broker.received[1].payload
        self.assertEqual(after.status, "succeeded")
        self.assertEqual(after.model_feedback, "真实内容")
        self.assertEqual(after.event_types, ("workspace_read",))

    def test_no_registered_hooks_add_no_snapshot_work(self) -> None:
        engine = object.__new__(AkaneMemoryEngine)
        engine.plugin_hook_broker = SimpleNamespace(observes=lambda _hook_type: False)
        expected = ToolExecutionResult(tool_type="read_workspace", followup_context="ok")
        engine._execute_tool_call = lambda **_kwargs: expected

        result = engine._execute_tool_call_with_hooks(
            call={"type": "read_workspace", "not_json": object()},
            final_output={},
            profile_user_id="profile",
            session_id="session",
            character_pack_id="",
            now_ts=int(time.time()),
            current_user_source_id="",
            client_context=SimpleNamespace(),
            memory_exclude_source_ids=[],
            request_context={},
        )

        self.assertIs(result, expected)

    def test_broken_hook_broker_does_not_change_tool_result(self) -> None:
        engine = object.__new__(AkaneMemoryEngine)
        engine.plugin_hook_broker = SimpleNamespace(
            observes=lambda _hook_type: True,
            dispatch_from_consumer=lambda _hook: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        expected = ToolExecutionResult(tool_type="read_workspace", followup_context="ok")
        engine._execute_tool_call = lambda **_kwargs: expected

        result = engine._execute_tool_call_with_hooks(
            call={"type": "read_workspace", "path": "README.md"},
            final_output={},
            profile_user_id="profile",
            session_id="session",
            character_pack_id="",
            now_ts=int(time.time()),
            current_user_source_id="",
            client_context=SimpleNamespace(),
            memory_exclude_source_ids=[],
            request_context={},
        )

        self.assertIs(result, expected)


if __name__ == "__main__":
    unittest.main()
