"""Failure and concurrency checks for the Bot runtime's lifecycle boundary."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from companion_v01.bot_runtime_components import factory as factory_module
from companion_v01.bot_runtime_components import plugins as plugin_module
from companion_v01.instance_profile import instance_context_from_bot_config
from companion_v01.instance_runtime import InstanceRuntimeError, bind_instance_runtime
from companion_v01.runtime_settings import BotSettingsView
from tests.test_bot_runtime import _leased_runtime, _runtime


class BotLifecycleBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_mirror_start_failure_remains_retryable_without_claiming_active(self):
        runtime, plugins, engine, _followups = _runtime()
        mirror = SimpleNamespace(start=AsyncMock(side_effect=[RuntimeError("mirror failed"), None]))
        engine.owner_private_group_memory = mirror

        with self.assertRaises(RuntimeError):
            await runtime.start()
        self.assertFalse(runtime._started)
        result = await runtime.start()

        self.assertEqual(result["status"], "active")
        self.assertEqual(mirror.start.await_count, 2)
        self.assertEqual(plugins.start_count, 2)
        self.assertEqual(engine.close_count, 0)

    async def test_cancelled_mirror_start_does_not_claim_active(self):
        runtime, _plugins, engine, _followups = _runtime()
        entered = asyncio.Event()

        async def start_mirror():
            entered.set()
            await asyncio.Event().wait()

        engine.owner_private_group_memory = SimpleNamespace(start=start_mirror)
        task = asyncio.create_task(runtime.start())
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(runtime._started)
        self.assertEqual(engine.close_count, 0)

    async def test_repeated_start_retains_plugin_degradation(self):
        runtime, plugins, _engine, _followups = _runtime()
        original_start = plugins.start

        async def degraded_start():
            return {**await original_start(), "status": "degraded", "reason": "generation_unavailable"}

        plugins.start = degraded_start
        first = await runtime.start()
        repeated = await runtime.start()

        self.assertEqual(first["status"], "degraded")
        self.assertEqual(repeated["status"], "degraded")
        self.assertEqual(repeated["reason"], "plugin_runtime_degraded")
        self.assertEqual(plugins.start_count, 1)

    async def test_concurrent_starts_recover_the_queue_once(self):
        runtime, plugins, _engine, _followups = _runtime()
        entered, release = asyncio.Event(), asyncio.Event()
        recover = AsyncMock()
        original_start = plugins.start

        async def gated_start():
            entered.set()
            await release.wait()
            return await original_start()

        plugins.start = gated_start
        runtime.session_work_queue = SimpleNamespace(recover=recover)
        first = asyncio.create_task(runtime.start())
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(runtime.start())
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), 2)

        self.assertEqual([item["status"] for item in results], ["active", "active"])
        self.assertEqual(plugins.start_count, 1)
        self.assertEqual(recover.await_count, 1)

    async def test_concurrent_stops_close_shared_services_once(self):
        runtime, plugins, engine, followups = _runtime()
        entered, release = asyncio.Event(), asyncio.Event()
        original_stop = plugins.stop

        async def gated_stop():
            entered.set()
            await release.wait()
            return await original_stop()

        plugins.stop = gated_stop
        first = asyncio.create_task(runtime.stop())
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(runtime.stop())
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), 2)

        self.assertEqual(results[0], results[1])
        self.assertEqual(plugins.stop_count, 1)
        self.assertEqual(followups.close_count, 1)
        self.assertEqual(engine.close_count, 1)

    async def test_stop_waits_for_inflight_start_before_closing_engine(self):
        runtime, plugins, engine, _followups = _runtime()
        entered, release = asyncio.Event(), asyncio.Event()
        stopped_after_start = []
        original_start, original_stop = plugins.start, plugins.stop

        async def gated_start():
            entered.set()
            await release.wait()
            return await original_start()

        async def observed_stop():
            stopped_after_start.append(release.is_set())
            return await original_stop()

        plugins.start, plugins.stop = gated_start, observed_stop
        start = asyncio.create_task(runtime.start())
        await asyncio.wait_for(entered.wait(), 2)
        stop = asyncio.create_task(runtime.stop())
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(asyncio.gather(start, stop), 2)

        self.assertEqual(stopped_after_start, [True])
        self.assertEqual(engine.close_count, 1)
        self.assertEqual((await runtime.start())["status"], "unavailable")

    async def test_incomplete_queue_shutdown_blocks_restart_and_allows_drain_retry(self):
        runtime, plugins, engine, _followups = _runtime()
        await runtime.start()
        close = AsyncMock(side_effect=[{"status": "degraded", "remaining": 1}, {"status": "stopped"}])
        runtime.session_work_queue = SimpleNamespace(close=close)

        partial = await runtime.stop()
        blocked = await runtime.start()
        self.assertEqual(partial["reason"], "session_queue_shutdown_incomplete")
        self.assertEqual(blocked["status"], "unavailable")
        self.assertEqual(engine.close_count, 0)
        self.assertEqual(plugins.stop_count, 0)
        self.assertEqual((await runtime.stop())["status"], "stopped")
        self.assertEqual(engine.close_count, 1)

    async def test_mirror_close_failure_defers_engine_cleanup_and_can_retry(self):
        runtime, plugins, engine, _followups = _runtime()
        mirror = SimpleNamespace(close=AsyncMock(side_effect=[RuntimeError("mirror busy"), True]))
        engine.owner_private_group_memory = mirror

        failed = await runtime.stop()
        self.assertEqual(failed["status"], "degraded")
        self.assertEqual(failed["reason"], "private_memory_import_shutdown_failed")
        self.assertEqual(failed["engine_status"]["status"], "deferred")
        self.assertEqual(engine.close_count, 0)
        self.assertEqual(plugins.stop_count, 0)
        self.assertEqual((await runtime.stop())["status"], "stopped")
        self.assertEqual(engine.close_count, 1)

    async def test_router_drain_failure_defers_engine_cleanup_and_can_retry(self):
        runtime, plugins, engine, _followups = _runtime()
        original_close = runtime.plugin_agent_event_router.aclose
        runtime.plugin_agent_event_router.aclose = AsyncMock(side_effect=RuntimeError("drain failed"))

        failed = await runtime.stop()
        self.assertEqual(failed["status"], "degraded")
        self.assertEqual(failed["reason"], "agent_turn_shutdown_failed")
        self.assertEqual(failed["engine_status"]["status"], "deferred")
        self.assertEqual(engine.close_count, 0)
        self.assertEqual(plugins.stop_count, 0)
        runtime.plugin_agent_event_router.aclose = original_close
        self.assertEqual((await runtime.stop())["status"], "stopped")

    async def test_router_shutdown_signal_failure_defers_engine_cleanup_and_can_retry(self):
        runtime, plugins, engine, _followups = _runtime()
        original_signal = runtime.plugin_agent_event_router.request_shutdown
        runtime.plugin_agent_event_router.request_shutdown = Mock(side_effect=RuntimeError("signal failed"))

        failed = await runtime.stop()

        self.assertEqual(failed["reason"], "agent_turn_shutdown_signal_failed")
        self.assertEqual(failed["engine_status"]["status"], "deferred")
        self.assertEqual(engine.close_count, 0)
        self.assertEqual(plugins.stop_count, 0)
        self.assertEqual((await runtime.start())["status"], "unavailable")
        runtime.plugin_agent_event_router.request_shutdown = original_signal
        self.assertEqual((await runtime.stop())["status"], "stopped")

    async def test_callback_unbind_failure_does_not_skip_remaining_cleanup(self):
        runtime, plugins, engine, followups = _runtime()
        runtime.host_tool_jobs = SimpleNamespace(bind_terminal_callback=Mock(side_effect=RuntimeError("unbind failed")))

        result = await runtime.stop()

        self.assertEqual(result["status"], "degraded")
        self.assertIn("host_job_callback_unbind_failed", result["failures"])
        self.assertEqual(plugins.stop_count, 1)
        self.assertEqual(followups.close_count, 1)
        self.assertEqual(engine.close_count, 1)

    async def test_degraded_plugin_stop_without_cleanup_count_is_not_success(self):
        runtime, plugins, engine, _followups = _runtime()
        plugins.stop = AsyncMock(return_value={"status": "degraded", "reason": "worker_still_alive"})

        result = await runtime.stop()

        self.assertEqual(result["status"], "degraded")
        self.assertIn("plugin_runtime_shutdown_incomplete", result["failures"])
        self.assertEqual(result["plugin_status"]["reason"], "worker_still_alive")
        self.assertEqual(engine.close_count, 1)

    async def test_stop_keeps_instance_lease_until_process_owner_releases_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime, _plugins, _engine, _followups = _leased_runtime(root, "bot-a")
            try:
                self.assertEqual((await runtime.stop())["status"], "stopped")
                self.assertEqual(runtime.instance_runtime.root_binding_status, "valid")
                with self.assertRaises(InstanceRuntimeError):
                    bind_instance_runtime(runtime.instance_context, data_root=root, explicit_data_root=True)
            finally:
                runtime.instance_runtime.release()


class BotAssemblyBoundaryTests(unittest.TestCase):
    def _assert_failed_construction(self, stage, *, cleanup_fails=False):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            prototype, _plugins, _engine, _followups = _runtime()
            leases, close_states = [], []
            original_error = ValueError("assembly failed")

            def bind(*args, **kwargs):
                lease = bind_instance_runtime(*args, **kwargs)
                leases.append(lease)
                return lease

            def close():
                close_states.append(leases[0].root_binding_status)
                if cleanup_fails:
                    raise RuntimeError("cleanup failed")
                return {"status": "stopped"}

            engine = SimpleNamespace(close=Mock(side_effect=close))
            plugins = SimpleNamespace(
                plugin_runtime=Mock(),
                plugin_agent_event_router=Mock(),
                plugin_conversation_refs=Mock(),
                extension_management_service=Mock(),
                plugin_capability_source=Mock(),
            )
            conversations = SimpleNamespace(
                qq_gateway=None,
                qq_followup_tasks=None,
                turn_coordinator=Mock(),
                session_inbox_store=Mock(),
                session_work_queue=Mock(),
            )
            jobs = SimpleNamespace(
                job_store=Mock(),
                host_tool_jobs=Mock(),
                host_subagent_jobs=Mock(),
                host_execution_jobs=Mock(),
                host_workflow_jobs=Mock(),
            )
            replacements = {
                "bind_instance_runtime": Mock(side_effect=bind),
                "ResourceManifest": Mock(),
                "DesktopPetCharacterResourceService": Mock(),
                "_load_runtime_settings": Mock(return_value=(SimpleNamespace(), BotSettingsView(), Mock(), Mock())),
                "resolve_instance_deployment_security": Mock(
                    return_value=SimpleNamespace(qq=SimpleNamespace(enabled=False), satellite=SimpleNamespace(token=""))
                ),
                "build_plugin_services": Mock(return_value=plugins),
                "AkaneMemoryEngine": Mock(return_value=engine),
                "bind_plugin_engine": Mock(),
                "build_conversation_services": Mock(return_value=conversations),
                "build_host_job_services": Mock(return_value=jobs),
                "_bind_voice_runtime": Mock(),
            }
            replacements[stage].side_effect = original_error
            with ExitStack() as stack:
                for name, replacement in replacements.items():
                    stack.enter_context(patch.object(factory_module, name, replacement))
                factory = factory_module.BotRuntimeFactory(config_module=SimpleNamespace(), assets_dir=root)
                with self.assertRaises(ValueError) as raised:
                    factory.create(data_root=root, bot_config=prototype.bot_config, explicit_data_root=True)

            self.assertIs(raised.exception, original_error)
            expected_close_states = [] if stage in {"build_plugin_services", "AkaneMemoryEngine"} else ["valid"]
            self.assertEqual(close_states, expected_close_states)
            self.assertEqual(leases[0].root_binding_status, "released")
            # Rebinding checks the actual process/file lock, not only its status string.
            replacement_lease = bind_instance_runtime(
                instance_context_from_bot_config(prototype.bot_config), data_root=root, explicit_data_root=True
            )
            replacement_lease.release()

    def test_failure_in_each_engine_binding_stage_closes_engine_before_releasing_lease(self):
        for stage in (
            "bind_plugin_engine",
            "build_conversation_services",
            "build_host_job_services",
            "_bind_voice_runtime",
        ):
            with self.subTest(stage=stage):
                self._assert_failed_construction(stage)

    def test_failure_before_engine_assignment_still_releases_lease(self):
        for stage in ("build_plugin_services", "AkaneMemoryEngine"):
            with self.subTest(stage=stage):
                self._assert_failed_construction(stage)

    def test_engine_cleanup_exception_preserves_construction_error_and_releases_lease(self):
        self._assert_failed_construction("bind_plugin_engine", cleanup_fails=True)

    def test_plugin_worker_and_default_market_keep_repository_root_after_relocation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            prototype, _plugins, _engine, _followups = _runtime()
            context = instance_context_from_bot_config(prototype.bot_config)
            lease = bind_instance_runtime(context, data_root=root, explicit_data_root=True)
            try:
                with patch.object(
                    plugin_module,
                    "PluginGenerationCandidateBuilder",
                    wraps=plugin_module.PluginGenerationCandidateBuilder,
                ) as builder:
                    services = plugin_module.build_plugin_services(
                        runtime_layout=lease.layout,
                        instance_context=context,
                        runtime_config=SimpleNamespace(),
                        execution_policies=(),
                    )
                project_root = Path(__file__).resolve().parents[1]
                self.assertEqual(builder.call_args.kwargs["project_root"], project_root)
                self.assertEqual(
                    services.extension_management_service.market.source,
                    str(project_root / ".plugin-market" / "index.json"),
                )
            finally:
                lease.release()


if __name__ == "__main__":
    unittest.main()
