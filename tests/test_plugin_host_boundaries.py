"""PluginHost regressions for cancellation, generations and scoped cleanup."""
from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace

from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_host import PluginHost
from tests.test_plugin_host import FakeAdapter, FakeEntryPoint, FakePlugin, PLUGIN_ID, _descriptor, _entry_point_for, _host
from tests.test_plugin_jobs import PLUGIN_ID as JOB_PLUGIN_ID, _make_job_plugin, _plugin_entry_points


class PluginActivationBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_activation_closes_every_staged_adapter_in_reverse(self):
        entered = asyncio.Event()
        close_order = []

        class SlowHealthAdapter(FakeAdapter):
            async def health(self):
                entered.set()
                await asyncio.Event().wait()

        first = SlowHealthAdapter(close_order=close_order, close_name="first")
        second = FakeAdapter(close_order=close_order, close_name="second")
        host = _host(
            (PluginSelection(PLUGIN_ID, True),),
            entry_points_provider=lambda: [_entry_point_for(FakePlugin((first, second)))],
        )
        self.addAsyncCleanup(host.stop)
        starting = asyncio.create_task(host.start())
        await asyncio.wait_for(entered.wait(), timeout=1)
        starting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await starting
        self.assertEqual(close_order, ["second", "first"])
        await host.stop()
        self.assertEqual((first.close_count, second.close_count), (1, 1))

    async def test_cancelled_later_plugin_cleans_predecessor_and_can_restart(self):
        entered = asyncio.Event()
        close_order = []
        second_id = "akane.test.second"
        first_adapters, second_adapters = [], []

        class SlowHealthAdapter(FakeAdapter):
            async def health(self):
                entered.set()
                await asyncio.Event().wait()

        def first_factory():
            adapter = FakeAdapter(close_order=close_order, close_name="first")
            first_adapters.append(adapter)
            return FakePlugin((adapter,))

        def second_factory():
            adapter_type = SlowHealthAdapter if not second_adapters else FakeAdapter
            adapter = adapter_type(_descriptor(f"{second_id}.ping.v1"), close_order=close_order, close_name="second")
            second_adapters.append(adapter)
            return FakePlugin((adapter,), manifest=replace(FakePlugin(()).manifest, plugin_id=second_id))

        entries = [FakeEntryPoint(PLUGIN_ID, first_factory), FakeEntryPoint(second_id, second_factory)]
        host = _host((PluginSelection(PLUGIN_ID, True), PluginSelection(second_id, True)),
                     entry_points_provider=lambda: entries)
        self.addAsyncCleanup(host.stop)
        starting = asyncio.create_task(host.start())
        await asyncio.wait_for(entered.wait(), timeout=1)
        starting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await starting
        self.assertEqual(close_order, ["second", "first"])
        self.assertEqual(host.state, "stopped")
        self.assertIsNone(host.runtime_loop)
        self.assertEqual(host.capability_ids, ())
        restarted = await host.restart()
        self.assertEqual((restarted["status"], restarted["plugin_count"]), ("active", 2))
        self.assertEqual((first_adapters[0].close_count, second_adapters[0].close_count), (1, 1))
        self.assertEqual((first_adapters[1].close_count, second_adapters[1].close_count), (0, 0))

    async def test_cancelled_prepared_activation_closes_registered_adapters(self):
        entered = asyncio.Event()

        class SlowHealthAdapter(FakeAdapter):
            async def health(self):
                entered.set()
                await asyncio.Event().wait()

        adapter = SlowHealthAdapter()
        host = _host((PluginSelection(PLUGIN_ID, True),),
                     entry_points_provider=lambda: [_entry_point_for(FakePlugin((adapter,)))])
        self.addAsyncCleanup(host.stop)
        self.assertEqual((await host.prepare())["status"], "prepared")
        activating = asyncio.create_task(host.activate_prepared())
        await asyncio.wait_for(entered.wait(), timeout=1)
        activating.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await activating
        self.assertEqual(host.state, "stopped")
        self.assertEqual(adapter.close_count, 1)
        self.assertEqual(host.capability_ids, ())

    async def test_retry_after_failed_prepare_closes_previous_partial_generation(self):
        adapters = []

        def factory():
            adapter = FakeAdapter()
            adapters.append(adapter)
            return FakePlugin((adapter,))

        host = _host((PluginSelection(PLUGIN_ID, True), PluginSelection("akane.test.missing", True)),
                     entry_points_provider=lambda: [FakeEntryPoint(PLUGIN_ID, factory)])
        self.addAsyncCleanup(host.stop)
        self.assertEqual((await host.prepare())["status"], "failed")
        self.assertEqual(adapters[0].close_count, 0)
        self.assertEqual((await host.start())["status"], "degraded")
        self.assertEqual((adapters[0].close_count, adapters[1].close_count), (1, 0))

    async def test_repeated_activation_cancellation_drains_staged_cleanup(self):
        health_entered, close_entered, release_close = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class SlowAdapter(FakeAdapter):
            async def health(self):
                health_entered.set()
                await asyncio.Event().wait()

            async def aclose(self):
                close_entered.set()
                await release_close.wait()
                await super().aclose()

        adapter = SlowAdapter()
        host = _host((PluginSelection(PLUGIN_ID, True),),
                     entry_points_provider=lambda: [_entry_point_for(FakePlugin((adapter,)))])
        self.addAsyncCleanup(host.stop)
        self.addCleanup(release_close.set)
        starting = asyncio.create_task(host.start())
        await asyncio.wait_for(health_entered.wait(), timeout=1)
        starting.cancel()
        await asyncio.wait_for(close_entered.wait(), timeout=1)
        starting.cancel()
        release_close.set()
        with self.assertRaises(asyncio.CancelledError):
            await starting
        self.assertEqual(adapter.close_count, 1)
        self.assertEqual(host.state, "stopped")

    async def test_cancelled_stop_finishes_adapter_cleanup_and_remains_idempotent(self):
        close_entered, release_close = asyncio.Event(), asyncio.Event()

        class SlowCloseAdapter(FakeAdapter):
            async def aclose(self):
                close_entered.set()
                await release_close.wait()
                await super().aclose()

        adapter = SlowCloseAdapter()
        host = _host((PluginSelection(PLUGIN_ID, True),),
                     entry_points_provider=lambda: [_entry_point_for(FakePlugin((adapter,)))])
        self.addAsyncCleanup(host.stop)
        self.addCleanup(release_close.set)
        await host.start()
        stopping = asyncio.create_task(host.stop())
        await asyncio.wait_for(close_entered.wait(), timeout=1)
        stopping.cancel()
        release_close.set()
        with self.assertRaises(asyncio.CancelledError):
            await stopping
        self.assertEqual(host.state, "stopped")
        self.assertEqual(adapter.close_count, 1)
        self.assertEqual((await host.stop())["status"], "stopped")
        self.assertEqual(adapter.close_count, 1)


class PluginBackgroundBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_job_completion_cannot_overwrite_restarted_service_status(self):
        old_cancelled, old_release, old_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
        jobs = []

        class DelayedJob:
            async def start(self, controller):
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    old_cancelled.set()
                    await old_release.wait()
                finally:
                    old_done.set()

            async def stop(self):
                pass

        class CurrentJob:
            async def start(self, controller):
                await controller.wait_for_shutdown()

            async def stop(self):
                pass

        def factory():
            job = DelayedJob() if not jobs else CurrentJob()
            jobs.append(job)
            return _make_job_plugin(job, include_capability=False)()

        host = PluginHost((PluginSelection(JOB_PLUGIN_ID, True),),
                          contribution_policy=TrustedStatefulPluginContributionPolicy(),
                          entry_points_provider=_plugin_entry_points(factory), close_timeout_seconds=0.01)
        host._background_service_stop_timeout_seconds = 0.01
        self.addAsyncCleanup(host.stop)
        self.addCleanup(old_release.set)
        await host.start()
        await host.stop()
        await asyncio.wait_for(old_cancelled.wait(), timeout=1)
        restarted = await host.restart()
        self.assertEqual(restarted["background_services"][0]["status"], "running")
        old_release.set()
        await asyncio.wait_for(old_done.wait(), timeout=1)
        await asyncio.sleep(0)
        self.assertEqual(host.status_snapshot()["background_services"][0]["status"], "running")


class PluginBindingBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_and_task_bindings_cannot_change_after_generation_start(self):
        class Provider:
            async def request(self, *args, **kwargs):
                return {}

        host = _host((), entry_points_provider=lambda: ())
        self.addAsyncCleanup(host.stop)
        host.bind_events_provider(Provider())
        host.bind_tasks_provider(Provider())
        await host.start()
        for state in ("active", "stopped"):
            for binder in (host.bind_events_provider, host.bind_tasks_provider):
                with self.subTest(state=state, binder=binder.__name__):
                    with self.assertRaises(RuntimeError):
                        binder(Provider())
            await host.stop()
