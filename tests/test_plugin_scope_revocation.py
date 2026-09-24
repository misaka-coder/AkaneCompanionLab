"""Cancellation withdraws new calls before draining real plugin work."""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from capcore import CapabilityResult, InvocationContext
from akane_plugin import Plugin, PluginConnectionResult, PluginResourceResult, ToolContext
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_generation import PluginGenerationProcess
from companion_v01.plugin_generation_callbacks import GenerationHostCallbackRouter
from companion_v01.plugin_generation_protocol import PLUGIN_GENERATION_PROTOCOL
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_resources import GeneratedFileResourceProvider
from tests.test_plugin_host import FakeEntryPoint
from tests.test_plugin_resources import services, add_attachment


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "test.revocation"
CONTEXT = InvocationContext("owner", "session", "web")


class ScopeRevocationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_host_revokes_before_plugin_uncancels_or_spawns_followup(self):
        for timeout in (None, 0.1):
            with self.subTest(timeout=timeout):
                entered, releasing = asyncio.Event(), asyncio.Event()
                followups = SimpleNamespace(invoke=AsyncMock(return_value=CapabilityResult(
                    is_error=False, status="ok", content=42,
                )))
                plugin = Plugin(PLUGIN_ID, permissions=("capability.invoke", "resource.read"))
                work_paths = []

                @plugin.tool
                async def run(ctx: ToolContext) -> dict[str, str]:
                    work_path = await ctx.resources.work_directory()
                    work_paths.append(work_path)
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        asyncio.current_task().uncancel()
                        # A fresh task has no cancellation count. The captured
                        # invocation grant must already have been withdrawn.
                        result = await asyncio.create_task(ctx.tools.call_result("test.peer.run", {}))
                        self.assertTrue(work_path.is_dir())
                        await releasing.wait()
                        return {"followup": result.reason, "actual_result": "finished"}

                host = PluginHost(
                    (PluginSelection(PLUGIN_ID, True),),
                    entry_points_provider=lambda: (FakeEntryPoint(PLUGIN_ID, lambda: plugin),),
                    invoke_timeout_seconds=timeout,
                    contribution_policy=TrustedStatefulPluginContributionPolicy(),
                )
                host.bind_capability_provider(followups)
                host.bind_resource_provider(SimpleNamespace(open=AsyncMock(
                    side_effect=AssertionError("this test uses only the real scoped work directory"),
                )))
                self.addAsyncCleanup(host.stop)
                status = await host.start()
                self.assertEqual(host.state, "active", status)
                pending = asyncio.create_task(host.invoke(PLUGIN_ID + ".run", {}, context=CONTEXT))
                try:
                    await asyncio.wait_for(entered.wait(), 2)
                    if timeout is None:
                        pending.cancel()
                    await asyncio.sleep(0.15)
                    self.assertFalse(pending.done())
                    self.assertTrue(work_paths[0].is_dir())
                    if timeout is None:
                        pending.cancel()  # Repeated signals still drain once.
                    releasing.set()
                    result = await asyncio.wait_for(pending, 2)
                    self.assertEqual(result.value, {
                        "followup": "capability_invocation_required", "actual_result": "finished",
                    })
                    followups.invoke.assert_not_called()
                    self.assertFalse(work_paths[0].exists())
                finally:
                    releasing.set()
                    await host.stop()

    async def test_parent_rejects_queued_and_late_callbacks_after_revocation(self):
        output, scheduled = [], []
        provider = SimpleNamespace(invoke=AsyncMock(), open=AsyncMock(), resolve=AsyncMock())
        router = GenerationHostCallbackRouter(
            generation_id="generation", start_timeout_seconds=1, write_response=output.append,
        )
        router.bind_capability_provider(provider)
        router.bind_resource_provider(provider)
        router.bind_connection_provider(provider)
        router.begin_invocation("request", plugin_id=PLUGIN_ID, context=CONTEXT, permissions=(
            "capability.invoke", "resource.read", "connection.image_generation.read",
        ))
        router._schedule = lambda callback, coroutine, **_: scheduled.append(coroutine)
        requests = []
        for index, (callback, extra) in enumerate((
            ("capability.invoke", {"capability_id": "test.peer.run", "arguments": {}}),
            ("resource.open", {"target": "attachment-id"}),
            ("connection.resolve", {"name": "image_generation"}),
        ), 1):
            request = {
                "protocol": PLUGIN_GENERATION_PROTOCOL, "generation_id": "generation",
                "callback_id": str(index) * 32, "callback": callback,
                "invocation_id": "request", **extra,
            }
            router.dispatch(request)
            requests.append(request)
        self.assertEqual(len(scheduled), 3)
        router.revoke_invocation("request")
        for coroutine in scheduled:
            await coroutine
        self.assertEqual([item["result"]["reason"] for item in output], [
            "capability_invocation_expired", "resource_invocation_expired", "connection_invocation_expired",
        ])
        for request in requests:
            router.dispatch({**request, "callback_id": "f" + request["callback_id"][1:]})
        self.assertEqual(len(scheduled), 3)
        self.assertTrue(all(not item["ok"] for item in output[3:]))
        provider.invoke.assert_not_called()
        provider.open.assert_not_called()
        provider.resolve.assert_not_called()
        await router.finish_invocation("request")
        router.close()

    async def test_revocation_withholds_private_callbacks_already_awaiting_provider(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "input.txt"
            path.write_text("input", encoding="utf-8")
            release = asyncio.Event()
            resource_started, connection_started = asyncio.Event(), asyncio.Event()

            async def open_resource(*args, **kwargs):
                resource_started.set()
                await release.wait()
                return PluginResourceResult(True, "ready", path=path, file_size=5)

            async def resolve_connection(*args, **kwargs):
                connection_started.set()
                await release.wait()
                return PluginConnectionResult(True, "configured", api_key="fixture-only-key")

            output, scheduled = [], []
            router = GenerationHostCallbackRouter(
                generation_id="generation", start_timeout_seconds=1, write_response=output.append,
            )
            router.bind_resource_provider(SimpleNamespace(open=open_resource))
            router.bind_connection_provider(SimpleNamespace(resolve=resolve_connection))
            router.begin_invocation("request", plugin_id=PLUGIN_ID, context=CONTEXT, permissions=(
                "resource.read", "connection.image_generation.read",
            ))
            router._schedule = lambda callback, coroutine, **_: scheduled.append(asyncio.create_task(coroutine))
            for index, callback, extra in (
                ("1", "resource.open", {"target": "attachment"}),
                ("2", "connection.resolve", {"name": "image_generation"}),
            ):
                router.dispatch({
                    "protocol": PLUGIN_GENERATION_PROTOCOL, "generation_id": "generation",
                    "callback_id": index * 32, "callback": callback, "invocation_id": "request", **extra,
                })
            await asyncio.wait_for(asyncio.gather(resource_started.wait(), connection_started.wait()), 2)
            router.revoke_invocation("request")
            release.set()
            await asyncio.gather(*scheduled)
            results = {item["callback_id"][0]: item["result"] for item in output}
            self.assertEqual(results["1"]["reason"], "resource_invocation_expired")
            self.assertEqual(results["1"]["path"], "")
            self.assertEqual(results["2"]["reason"], "connection_invocation_expired")
            self.assertEqual(results["2"]["api_key"], "")
            await router.finish_invocation("request")
            router.close()

    async def test_real_worker_cannot_resume_calls_but_keeps_existing_copy_until_terminal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, attachments, files = services(root)
            _, attachment = add_attachment(root, attachments)
            site = root / "site"
            package = site / "revocation_fixture"
            metadata = site / "revocation_fixture-0.1.0.dist-info"
            package.mkdir(parents=True)
            metadata.mkdir()
            (package / "__init__.py").write_text(textwrap.dedent('''
                import asyncio
                from dataclasses import replace
                from pathlib import Path
                from akane_plugin import Plugin, ToolContext
                from companion_v01.plugin_resources import current_resource_invocation
                plugin = Plugin("test.revocation", permissions=("capability.invoke", "resource.read"))
                @plugin.tool
                async def run(target: str, marker: str, ctx: ToolContext) -> dict[str, str]:
                    source = await ctx.resources.open(target)
                    if not source.ok: return {"failure": source.reason}
                    Path(marker).write_text("ready")
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        asyncio.current_task().uncancel()
                        followup = await asyncio.create_task(ctx.tools.call_result("test.peer.run", {}))
                        late_source = await asyncio.create_task(ctx.resources.open(target))
                        # Adversarial wire test only: bypass the public port's
                        # local guard with a copied worker scope. The parent
                        # must still reject the original opaque request ID.
                        forged = replace(current_resource_invocation.get(), active=True)
                        wire_followup = await ctx.tools._port._provider.invoke(
                            "test.peer.run", {}, invocation=forged)
                        # The actual already-open resource survives cancellation
                        # until this terminal result, despite followup rejection.
                        return {"followup": followup.reason, "wire_followup": wire_followup.reason,
                                "resource": late_source.reason,
                                "existing": source.path.read_text()}
                def create_plugin(): return plugin
            '''), encoding="utf-8")
            (metadata / "METADATA").write_text(
                "Metadata-Version: 2.1\nName: revocation-fixture\nVersion: 0.1.0\n", encoding="utf-8",
            )
            (metadata / "entry_points.txt").write_text(
                "[akane.plugins.v1]\ntest.revocation = revocation_fixture:create_plugin\n", encoding="utf-8",
            )
            process = PluginGenerationProcess(project_root=ROOT, site_dir=site, plugin_id=PLUGIN_ID,
                                              work_dir=root / "worker")
            followups = SimpleNamespace(invoke=AsyncMock(return_value=CapabilityResult(
                is_error=False, status="ok", content=42,
            )))
            process.bind_capability_provider(followups)
            process.bind_resource_provider(GeneratedFileResourceProvider(files, work_root=root / "copies"))
            await asyncio.to_thread(process.start)
            pending = asyncio.create_task(process.invoke(PLUGIN_ID + ".run", {
                "target": attachment["attachment_handle"], "marker": str(root / "ready"),
            }, context=CONTEXT))
            try:
                async with asyncio.timeout(10):
                    while not (root / "ready").exists():
                        if pending.done():
                            self.fail(f"worker finished before cancellation: {pending.result()}")
                        await asyncio.sleep(0.01)
                pending.cancel()
                result = await asyncio.wait_for(pending, 10)
                self.assertEqual(result.value, {"followup": "capability_invocation_required",
                                               "wire_followup": "capability_invocation_expired",
                                               "resource": "resource_invocation_required",
                                               "existing": "original resource"})
                followups.invoke.assert_not_called()
                self.assertEqual(list((root / "copies").iterdir()), [])
                self.assertEqual(process._callback_router._invocations, {})
            finally:
                if not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                await asyncio.to_thread(process.stop)


if __name__ == "__main__":
    unittest.main()
