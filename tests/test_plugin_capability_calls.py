"""Real isolated composition, normal admission, resource scope and cancellation."""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from capcore import InvocationContext
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_active_generation import ActivePluginGeneration, PluginGenerationSnapshot
from companion_v01.plugin_capability_calls import EnginePluginCapabilityProvider, ScopedPluginCapabilityPort
from companion_v01.plugin_generation import PluginGenerationProcess, PluginGenerationError
from companion_v01.plugin_generation_callbacks import GenerationHostCallbackRouter
from companion_v01.plugin_generation_protocol import PLUGIN_GENERATION_PROTOCOL
from companion_v01.plugin_managed_artifacts import GeneratedFileManagedArtifactSink
from companion_v01.plugin_resources import (
    GeneratedFileResourceProvider,
    ResourceInvocation,
    current_resource_invocation,
)
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from tests.test_plugin_engine_bridge import EngineFacade
from tests.test_plugin_resources import services, add_attachment


ROOT = Path(__file__).resolve().parents[1]
A, B = "test.compose-a", "test.compose-b"


def write_composition_plugin(root, plugin_id, *, permission=True, confirm="never"):
    site = root / "site"
    package, metadata = site / "composition_fixture", site / "composition_fixture-0.1.0.dist-info"
    package.mkdir(parents=True)
    metadata.mkdir()
    source = (
        textwrap.dedent("""
        import asyncio
        from pathlib import Path
        from capcore import CapabilityDescriptor, CapabilityIOSlot, CapabilityResult, HealthStatus
        from companion_v01.plugin_api import PluginManifest, ManagedArtifactPayload, ManagedArtifactDraft
        PLUGIN_ID = PLUGIN_ID_VALUE
        class Adapter:
            provider_id = "provider." + PLUGIN_ID
            def __init__(self, resources, capabilities): self.resources, self.capabilities = resources, capabilities
            async def health(self): return HealthStatus(True, "ready")
            async def list_capabilities(self):
                return (CapabilityDescriptor(
                    id=PLUGIN_ID + ".run", display_name="Composition fixture", short_hint="Copy through an installed peer.",
                    visible_in=("base", "web", "desktop", "qq"), prompt_exposed=True, risk="low", confirm=CONFIRM_VALUE,
                    effects=("filesystem",), trigger=None,
                    inputs=(CapabilityIOSlot("target", "string", required=True), CapabilityIOSlot("peer", "string"),
                            CapabilityIOSlot("return_to", "string"), CapabilityIOSlot("outcome", "string"),
                            CapabilityIOSlot("ready_path", "string"),
                            CapabilityIOSlot("forward", "boolean"), CapabilityIOSlot("tamper", "string"),
                            CapabilityIOSlot("replay", "boolean"),
                            CapabilityIOSlot("send_to_user", "boolean")),
                    outputs=(CapabilityIOSlot("file", "file", required=True, max_bytes=1024, delivery="generated_file"),), raw={},
                ),)
            async def invoke(self, capability_id, args, context):
                if args.get("replay"):
                    return self.previous_result
                target = args["target"]
                if args.get("peer"):
                    if args.get("forward"):
                        result = await self.capabilities.call_result(args["peer"], {"target": target})
                        self.previous_result = result
                        if args.get("tamper") == "delivery":
                            result.content["managed_artifacts"][0]["send_to_user"] = True
                        if args.get("tamper") == "origin":
                            result.content["managed_artifacts"][0]["created_by_tool"] = PLUGIN_ID + ".run"
                        if args.get("tamper") == "value":
                            result.value["source"] = "invented source"
                        return result
                    result = await self.capabilities.invoke(args["peer"], {"target": target, "peer": args.get("return_to", ""),
                        "outcome": args.get("outcome", ""), "ready_path": args.get("ready_path", ""),
                        "send_to_user": False})
                    if result.is_error: return result
                    target = result.content["artifacts"][0]
                source = await self.resources.open(target)
                if not source.ok: return CapabilityResult(is_error=True, status="error", reason=source.reason)
                if args.get("outcome") and not args.get("peer"):
                    if args.get("ready_path"): Path(args["ready_path"]).write_text("awaiting cancellation")
                    try: await asyncio.sleep(30)
                    except asyncio.CancelledError:
                        await asyncio.sleep(0.15)
                        if args["outcome"] == "cancel": raise
                        if args["outcome"] == "failed":
                            return CapabilityResult(is_error=True, status="error", reason="remote_completion_unconfirmed")
                return CapabilityResult(is_error=False, status="ok", content=ManagedArtifactPayload(
                    content={"source": source.handle}, artifacts=(ManagedArtifactDraft(
                        data=source.path.read_bytes() + b"|" + PLUGIN_ID.encode(), title=PLUGIN_ID,
                        output_format="txt", mime_type="text/plain", send_to_user=False),),
                ))
            async def aclose(self): pass
        class Plugin:
            manifest = PluginManifest(PLUGIN_ID, "0.1.0", 1, PERMISSIONS_VALUE)
            def register(self, registrar):
                registrar.add_capability_adapter(Adapter(registrar.get_resource_port(), registrar.get_capability_port()))
        def create_plugin(): return Plugin()
    """)
        .replace("PLUGIN_ID_VALUE", repr(plugin_id))
        .replace("CONFIRM_VALUE", repr(confirm))
        .replace(
            "PERMISSIONS_VALUE",
            repr(
                ("capability.prompt.invoke", "resource.read", "artifact.write")
                + (("capability.invoke",) if permission else ())
            ),
        )
    )
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: composition-fixture\nVersion: 0.1.0\n", encoding="utf-8"
    )
    (metadata / "entry_points.txt").write_text(
        f"[akane.plugins.v1]\n{plugin_id} = composition_fixture:create_plugin\n", encoding="utf-8"
    )
    return site


class CapabilityPortTests(unittest.IsolatedAsyncioTestCase):
    async def test_scope_permission_identity_cycles_budgets_and_safe_request(self):
        backend = SimpleNamespace(invoke=AsyncMock())
        port = ScopedPluginCapabilityPort(A, backend)
        self.assertEqual((await port.invoke(B + ".run", {})).reason, "capability_invocation_required")
        scope = ResourceInvocation(A, InvocationContext("owner", "session", "web"), capability_id=A + ".run")
        token = current_resource_invocation.set(scope)
        try:
            self.assertEqual((await port.invoke(B + ".run", {})).reason, "capability_invoke_permission_required")
            scope.can_invoke_capabilities = True
            other = ScopedPluginCapabilityPort(B, backend)
            self.assertEqual((await other.invoke(B + ".run", {})).reason, "capability_invocation_required")
            await scope.aclose()
            self.assertEqual((await port.invoke(B + ".run", {})).reason, "capability_invocation_required")
        finally:
            current_resource_invocation.reset(token)
        backend.invoke.assert_not_called()
        engine = SimpleNamespace(_resolve_tool_handlers=Mock(side_effect=AssertionError("must not dispatch")))
        provider = EnginePluginCapabilityProvider(engine)
        from companion_v01.execution_resource_policy import ExecutionResourcePolicy, ExecutionResourceBudget
        from companion_v01.capability_registry import ExecutorBroker
        policy = ExecutionResourcePolicy.from_config({"max_input_bytes": 16384, "max_dependency_depth": 4, "max_dependency_calls": 32})
        engine.executor_broker = ExecutorBroker(None, resource_policy=policy)
        exhausted = ExecutionResourceBudget(policy)
        for _ in range(32):
            exhausted.reserve_dependency(depth=1)
        for kwargs, target, args, reason in (
            ({}, A + ".run", {}, "capability_dependency_cycle"),
            ({"resource_budget": exhausted}, B + ".run", {}, "execution_resource_limit_exceeded"),
            ({"dependency_chain": ("1", "2", "3", "4")}, B + ".run", {}, "execution_resource_limit_exceeded"),
            ({}, B + ".run", {"value": float("nan")}, "capability_dependency_request_invalid"),
            ({}, B + ".run", {"value": "x" * 17000}, "execution_resource_limit_exceeded"),
        ):
            scope = ResourceInvocation(
                A,
                InvocationContext("owner", "session", "web"),
                capability_id=A + ".run",
                can_invoke_capabilities=True,
                **kwargs,
            )
            self.assertEqual((await provider.invoke(target, args, invocation=scope)).reason, reason)
        engine._resolve_tool_handlers.assert_not_called()

    async def test_reverse_callback_permission_and_cancel_before_scheduling(self):
        output, scheduled = [], []
        provider = SimpleNamespace(invoke=AsyncMock())
        router = GenerationHostCallbackRouter(
            generation_id="generation", start_timeout_seconds=1, write_response=output.append
        )
        router.bind_capability_provider(provider)
        router.begin_invocation(
            "request", plugin_id=A, context=InvocationContext("owner", "session", "web"), capability_id=A + ".run"
        )
        request = {
            "protocol": PLUGIN_GENERATION_PROTOCOL,
            "generation_id": "generation",
            "callback_id": "a" * 32,
            "callback": "capability.invoke",
            "invocation_id": "request",
            "capability_id": B + ".run",
            "arguments": {},
        }
        router.dispatch(request)
        self.assertEqual(output[-1]["reason"], "capability_invoke_permission_required")
        await router.finish_invocation("request")
        router.begin_invocation(
            "request",
            plugin_id=A,
            context=InvocationContext("owner", "session", "web"),
            capability_id=A + ".run",
            permissions=("capability.invoke",),
        )
        router._schedule = lambda callback, coroutine, **_: scheduled.append(coroutine)
        router.dispatch(request)
        router.cancel(request)
        await scheduled.pop()
        self.assertEqual(output[-1]["result"]["status"], "cancelled")
        provider.invoke.assert_not_called()
        await router.finish_invocation("request")
        router.close()


class IsolatedCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = root = Path(temporary.name)
        self.store, attachments, self.files = services(root)
        self.source, attachment = add_attachment(root, attachments)
        self.target = attachment["attachment_handle"]
        self.active = ActivePluginGeneration()
        self.addAsyncCleanup(self.active.stop)
        self.engine = EngineFacade(PluginCapabilityToolBridge(self.active, config_base_dir=root))
        self.engine.store = self.store
        self.engine.capability_config_base_dir = root
        self.provider = EnginePluginCapabilityProvider(self.engine)
        self.processes = []

    async def create(self, plugin_id, *, permission=True, confirm="never"):
        root = self.root / (plugin_id + str(len(self.processes)))
        process = PluginGenerationProcess(
            project_root=ROOT,
            site_dir=write_composition_plugin(root, plugin_id, permission=permission, confirm=confirm),
            plugin_id=plugin_id,
            work_dir=root / "worker",
        )
        process.bind_managed_artifact_sink(GeneratedFileManagedArtifactSink(self.files))
        process.bind_resource_provider(GeneratedFileResourceProvider(self.files, work_root=self.root / "copies"))
        process.bind_capability_provider(self.provider)
        self.processes.append(process)
        self.addAsyncCleanup(asyncio.to_thread, process.stop)
        await asyncio.to_thread(process.start)
        return process

    async def publish(self, processes):
        snapshot = PluginGenerationSnapshot(
            tuple(PluginSelection(p.plugin_id, True) for p in processes), tuple(processes)
        )
        return await self.active.publish(snapshot)

    async def invoke(self, **kwargs):
        return await self.active.invoke(
            A + ".run",
            {"target": self.target, "peer": B + ".run", **kwargs},
            context=InvocationContext("owner", "session", "qq_text"),
        )

    async def test_complete_result_forwarding_registers_once_and_rejects_tampering_and_replay(self):
        from companion_v01.plugin_result_delivery import plugin_artifact_events

        a, b = await self.create(A), await self.create(B)
        await self.publish((a, b))
        result = await self.invoke(forward=True)
        self.assertFalse(result.is_error, result)
        reference, = result.content["managed_artifacts"]
        self.assertEqual(reference["created_by_tool"], B + ".run")
        self.assertEqual(reference["forwarded_by_tool"], A + ".run")
        rows = self.store.list_generated_files(profile_user_id="owner", session_id="session", limit=100)
        self.assertEqual(len(rows), 1)
        resolved = self.files.resolve_input_resource(profile_user_id="owner", session_id="session",
                                                     target=reference["generated_handle"], timestamp=None)
        self.assertEqual(Path(resolved["absolute_path"]).read_bytes(), b"original resource|test.compose-b")
        events = plugin_artifact_events(result, capability_id=A + ".run", client_mode="qq_text")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["generated_file"]["created_by_tool"], B + ".run")
        replayed = await self.invoke(replay=True)
        self.assertEqual(replayed.reason, "plugin_result_reserved_key", replayed)
        for tamper in ("delivery", "origin", "value"):
            invalid = await self.invoke(forward=True, tamper=tamper)
            self.assertEqual(invalid.reason, "plugin_result_reserved_key", invalid)
        self.assertEqual(len(self.store.list_generated_files(profile_user_id="owner", session_id="session", limit=100)), 4)
        self.assertFalse(list(self.root.glob("**/outbox/**/*.*")))

    async def test_real_peers_artifact_handles_scope_cycle_disable_and_reenable(self):
        a, b = await self.create(A), await self.create(B)
        await self.publish((a, b))
        result = await self.invoke()
        self.assertFalse(result.is_error, result)
        ref = result.content["managed_artifacts"][0]
        output = self.files.resolve_input_resource(
            profile_user_id="owner", session_id="session", target=ref["generated_handle"], timestamp=None
        )
        self.assertEqual(Path(output["absolute_path"]).read_bytes(), b"original resource|test.compose-b|test.compose-a")
        self.assertEqual(self.source.read_bytes(), b"original resource")
        self.assertEqual(list((self.root / "copies").iterdir()), [])
        self.assertEqual((await self.invoke(return_to=A + ".run")).reason, "capability_dependency_cycle")
        denied = await self.active.invoke(
            A + ".run",
            {"target": self.target, "peer": B + ".run"},
            context=InvocationContext("other", "session", "qq_text"),
        )
        self.assertEqual(denied.reason, "resource_not_found")
        await self.publish((a,))
        self.assertEqual((await self.invoke()).reason, "capability_dependency_unavailable")
        b = await self.create(B)
        await self.publish((a, b))
        self.assertFalse((await self.invoke()).is_error)

    async def test_real_target_ordinary_permission_not_bypassed(self):
        a, b = await self.create(A), await self.create(B, confirm="always")
        await self.publish((a, b))
        result = await self.invoke()
        self.assertTrue(result.is_error)
        self.assertEqual(result.status, "approval_required")
        self.assertEqual(self.store.list_generated_files(profile_user_id="owner", session_id="session", limit=10), [])
        self.assertFalse(list((self.root / "copies").glob("*")))

    async def test_missing_manifest_permission_rejects_activation(self):
        with self.assertRaises(PluginGenerationError):
            await self.create(A, permission=False)

    async def test_legacy_port_rejects_delivery_and_target_schema_rejects_identity_override(self):
        a, b = await self.create(A), await self.create(B)
        await self.publish((a, b))
        scope = ResourceInvocation(
            A, InvocationContext("owner", "session", "qq_text"), capability_id=A + ".run", can_invoke_capabilities=True
        )
        port = ScopedPluginCapabilityPort(A, self.provider)
        token = current_resource_invocation.set(scope)
        try:
            denied = await port.invoke(B + ".run", {"target": self.target, "send_to_user": True})
        finally:
            current_resource_invocation.reset(token)
        self.assertEqual(denied.reason, "capability_dependency_delivery_forbidden")
        invalid = await self.provider.invoke(
            B + ".run", {"target": self.target, "profile_user_id": "other"}, invocation=scope
        )
        self.assertEqual(invalid.status, "validation_error")
        self.assertEqual(self.store.list_generated_files(profile_user_id="owner", session_id="session", limit=10), [])

    async def test_disable_running_peer_withdraws_discovery_then_drains_cancel(self):
        a, b = await self.create(A), await self.create(B)
        await self.publish((a, b))
        ready = self.root / "peer-ready"
        pending = asyncio.create_task(self.invoke(outcome="cancel", ready_path=str(ready)))
        for _ in range(1000):
            if pending.done():
                self.fail(f"completed before cancellation: {pending.result()}")
            if ready.exists():
                break
            await asyncio.sleep(0.01)
        self.assertTrue(list((self.root / "copies").glob("input-*/*")))
        self.assertTrue(ready.exists())
        disabling = asyncio.create_task(self.publish((a,)))
        await asyncio.sleep(0.05)
        self.assertNotIn(B + ".run", self.active.capability_ids)
        self.assertEqual((await disabling)["cleanup_status"], "draining")
        self.assertFalse(pending.done())
        result = await pending
        self.assertEqual(result.status, "cancelled", result)
        self.assertTrue((await disabling)["ok"])
        await self.active.drain_retired()
        self.assertFalse(b.running)
        self.assertEqual((await self.invoke()).reason, "capability_dependency_unavailable")
        self.assertEqual(list((self.root / "copies").iterdir()), [])

    async def test_cancel_parent_drains_actual_peer_and_keeps_unconfirmed_failure(self):
        a, b = await self.create(A), await self.create(B)
        await self.publish((a, b))
        for outcome in ("cancel", "failed", "success"):
            ready = self.root / ("peer-ready-" + outcome)
            pending = asyncio.create_task(self.invoke(outcome=outcome, ready_path=str(ready)))
            for _ in range(1000):
                if pending.done():
                    self.fail(f"completed before cancellation: {pending.result()}")
                if ready.exists():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(list((self.root / "copies").glob("input-*/*")))
            self.assertTrue(ready.exists())
            pending.cancel()
            await asyncio.sleep(0.02)
            pending.cancel()
            if outcome == "failed":
                result = await pending
                self.assertTrue(result.is_error)
                self.assertEqual(result.reason, "remote_completion_unconfirmed")
            else:
                with self.assertRaises(asyncio.CancelledError):
                    await pending
            self.assertEqual(list((self.root / "copies").iterdir()), [])
            files = self.store.list_generated_files(profile_user_id="owner", session_id="session", limit=10)
            # A peer that really finished before acknowledging cancellation
            # retains its own result; the cancelled parent never adds a second.
            self.assertEqual(len(files), int(outcome == "success"))
            await asyncio.sleep(0.05)
            self.assertEqual(
                self.store.list_generated_files(profile_user_id="owner", session_id="session", limit=10), files
            )


if __name__ == "__main__":
    unittest.main()
