from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import dataclass
from typing import Any, Callable
from pathlib import Path

import httpx
from capcore import CapabilityDescriptor, CapabilityIOSlot, CapabilityResult, HealthStatus, InvocationContext
from fastapi import FastAPI

from companion_v01.distribution_artifacts import audit_distribution_artifact
from companion_v01.instance_profile import PluginSelection
from companion_v01.extension_management import ExtensionManagementService, PluginSelectionStore
from companion_v01.plugin_contribution_policy import (
    ContributionPolicyDecision,
    M65CDiagnosticContributionPolicy,
)
from companion_v01.plugin_api import AKANE_PLUGIN_API_VERSION, PluginManifest
from companion_v01.plugin_host import PluginHost
from companion_v01.routes.plugins import MAX_PLUGIN_REQUEST_BYTES, build_plugins_router


PLUGIN_ID = "akane.test.diagnostic"
CAPABILITY_ID = f"{PLUGIN_ID}.ping.v1"


class FakeDistribution:
    def __init__(self, *, version: str = "0.1.0", direct_url: str | None = None) -> None:
        self.version = version
        self.metadata = {"Name": "akane-diagnostic-plugin"}
        self._direct_url = direct_url

    def read_text(self, filename: str) -> str | None:
        return self._direct_url if filename == "direct_url.json" else None


class FakeEntryPoint:
    def __init__(
        self,
        name: str,
        factory: Callable[..., Any],
        *,
        distribution: Any | None = None,
    ) -> None:
        self.name = name
        self.dist = distribution if distribution is not None else FakeDistribution()
        self._factory = factory
        self.load_count = 0

    def load(self) -> Callable[..., Any]:
        self.load_count += 1
        return self._factory


class FakeAdapter:
    provider_id = "provider.test.diagnostic"

    def __init__(
        self,
        *descriptors: CapabilityDescriptor,
        result: CapabilityResult | None = None,
        close_order: list[str] | None = None,
        close_name: str = "adapter",
        close_fails: bool = False,
        invoke_gate: asyncio.Event | None = None,
    ) -> None:
        self.descriptors = tuple(descriptors or (_descriptor(),))
        self.result = result or CapabilityResult(
            is_error=False,
            status="ok",
            content={
                "plugin_id": PLUGIN_ID,
                "diagnostic": "ready",
                "plugin_api_version": AKANE_PLUGIN_API_VERSION,
            },
        )
        self.close_order = close_order
        self.close_name = close_name
        self.close_fails = close_fails
        self.invoke_gate = invoke_gate
        self.close_count = 0
        self.invoke_count = 0
        self.invocation_contexts: list[InvocationContext] = []

    async def health(self) -> HealthStatus:
        return HealthStatus(ok=True, status="ready")

    async def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        return self.descriptors

    async def invoke(self, capability_id: str, args: dict[str, Any], ctx: Any) -> CapabilityResult:
        self.invoke_count += 1
        self.invocation_contexts.append(ctx)
        if self.invoke_gate is not None:
            await self.invoke_gate.wait()
        return self.result

    async def aclose(self) -> None:
        self.close_count += 1
        if self.close_order is not None:
            self.close_order.append(self.close_name)
        if self.close_fails:
            raise RuntimeError("must not escape")


@dataclass
class FakePlugin:
    adapters: tuple[FakeAdapter, ...]
    manifest: PluginManifest = PluginManifest(
        plugin_id=PLUGIN_ID,
        plugin_version="0.1.0",
        plugin_api_version=AKANE_PLUGIN_API_VERSION,
        permissions=("diagnostics.invoke",),
    )
    fail_after_register: bool = False

    def register(self, registrar: Any) -> None:
        for adapter in self.adapters:
            registrar.add_capability_adapter(adapter)
        if self.fail_after_register:
            raise RuntimeError("private activation detail")


def _descriptor(
    capability_id: str = CAPABILITY_ID,
    *,
    inputs: tuple[CapabilityIOSlot, ...] = (),
    prompt_exposed: bool = False,
    risk: str = "low",
    confirm: str = "never",
    effects: tuple[str, ...] = (),
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id=capability_id,
        display_name="Diagnostic Ping",
        short_hint="Return a side-effect-free diagnostic result.",
        visible_in=("diagnostics",),
        prompt_exposed=prompt_exposed,
        risk=risk,  # type: ignore[arg-type]
        confirm=confirm,  # type: ignore[arg-type]
        effects=effects,
        trigger=None,
        inputs=inputs,
        outputs=(),
        raw={},
    )


def _entry_point_for(plugin: FakePlugin, *, distribution: Any | None = None) -> FakeEntryPoint:
    def factory() -> FakePlugin:
        return plugin

    return FakeEntryPoint(PLUGIN_ID, factory, distribution=distribution)


def _host(
    selections: tuple[PluginSelection, ...],
    **kwargs: Any,
) -> PluginHost:
    return PluginHost(
        selections,
        contribution_policy=M65CDiagnosticContributionPolicy(),
        **kwargs,
    )


class DistributionArtifactAuditTests(unittest.TestCase):
    def test_wheel_and_archive_metadata_are_allowed(self) -> None:
        installed_wheel = audit_distribution_artifact(FakeDistribution())
        local_wheel_archive = audit_distribution_artifact(
            FakeDistribution(direct_url=json.dumps({"url": "file:///artifact/plugin.whl", "archive_info": {}}))
        )

        self.assertTrue(installed_wheel.ok)
        self.assertTrue(local_wheel_archive.ok)
        self.assertEqual(installed_wheel.version, "0.1.0")

    def test_source_and_invalid_direct_url_metadata_are_rejected(self) -> None:
        source = audit_distribution_artifact(
            FakeDistribution(direct_url=json.dumps({"url": "file:///source", "dir_info": {}}))
        )
        invalid = audit_distribution_artifact(FakeDistribution(direct_url="not-json"))

        self.assertEqual(source.reason, "source_directory_install_forbidden")
        self.assertEqual(invalid.reason, "invalid_direct_url_metadata")


class PluginHostTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_mechanics_do_not_hardcode_m65c_descriptor_policy(self) -> None:
        class BroaderCapabilityPolicy:
            policy_id = "test.broader-capability.v1"

            def validate_manifest(self, manifest: PluginManifest) -> ContributionPolicyDecision:
                return ContributionPolicyDecision.allow()

            def validate_capability(
                self,
                *,
                plugin_id: str,
                descriptor: CapabilityDescriptor,
            ) -> ContributionPolicyDecision:
                return ContributionPolicyDecision.allow()

        adapter = FakeAdapter(
            _descriptor(
                prompt_exposed=True,
                risk="high",
                confirm="always",
                effects=("network",),
            )
        )
        plugin = FakePlugin((adapter,))
        plugin.manifest = PluginManifest(
            plugin_id=PLUGIN_ID,
            plugin_version="0.1.0",
            plugin_api_version=AKANE_PLUGIN_API_VERSION,
            permissions=("finance.invoke",),
        )
        host = PluginHost(
            (PluginSelection(PLUGIN_ID, True),),
            contribution_policy=BroaderCapabilityPolicy(),
            entry_points_provider=lambda: (_entry_point_for(plugin),),
        )

        status = await host.start()
        await host.stop()

        self.assertEqual(status["status"], "active")
        self.assertEqual(status["contribution_policy"], "test.broader-capability.v1")
        self.assertEqual(status["capability_count"], 1)

    async def test_init_is_inert_and_disabled_selection_never_discovers_or_loads(self) -> None:
        provider_calls = 0
        entry_point = _entry_point_for(FakePlugin((FakeAdapter(),)))

        def provider() -> tuple[FakeEntryPoint, ...]:
            nonlocal provider_calls
            provider_calls += 1
            return (entry_point,)

        host = _host((PluginSelection(PLUGIN_ID, False),), entry_points_provider=provider)

        self.assertEqual(host.state, "created")
        self.assertEqual(provider_calls, 0)
        self.assertEqual(entry_point.load_count, 0)

        started = await host.start()

        self.assertEqual(started["status"], "active")
        self.assertEqual(started["plugins"][0]["status"], "disabled")
        self.assertNotIn("contribution_snapshot", started["plugins"][0])
        self.assertEqual(host.contribution_snapshots, ())
        self.assertEqual(provider_calls, 0)
        self.assertEqual(entry_point.load_count, 0)

    async def test_active_plugin_invokes_through_capcore_validation_and_lifecycle_is_idempotent(self) -> None:
        descriptor = _descriptor(
            inputs=(
                CapabilityIOSlot(
                    name="probe",
                    kind="string",
                    required=True,
                    raw={"minLength": 1, "maxLength": 12},
                ),
            )
        )
        adapter = FakeAdapter(descriptor)
        entry_point = _entry_point_for(FakePlugin((adapter,)))
        provider_calls = 0

        def provider() -> tuple[FakeEntryPoint, ...]:
            nonlocal provider_calls
            provider_calls += 1
            return (entry_point,)

        host = _host((PluginSelection(PLUGIN_ID, True),), entry_points_provider=provider)

        first = await host.start()
        second = await host.start()
        descriptor_snapshot = host.capability_descriptors
        contribution_snapshot = host.contribution_snapshots
        descriptor.raw["plugin_mutation"] = True  # type: ignore[index]
        descriptor_snapshot[CAPABILITY_ID].raw["consumer_mutation"] = True  # type: ignore[index]
        current_descriptor = host.capability_descriptors[CAPABILITY_ID]
        context = InvocationContext(
            profile_user_id="user-42",
            session_id="session-7",
            client_mode="qq",
        )
        invalid_context = await host.invoke(CAPABILITY_ID, {"probe": "ready"}, context=None)  # type: ignore[arg-type]
        invalid = await host.invoke(CAPABILITY_ID, {}, context=context)
        valid = await host.invoke(CAPABILITY_ID, {"probe": "ready"}, context=context)
        stopped = await host.stop()
        stopped_again = await host.stop()
        after_stop = await host.invoke(CAPABILITY_ID, {"probe": "ready"}, context=context)

        self.assertEqual(first["status"], "active")
        self.assertIsNone(first["contract"]["timeouts"]["invoke_seconds"])
        self.assertEqual(first["contract"]["registration_limits"]["capabilities_per_plugin"], 64)
        self.assertEqual(second, first)
        self.assertEqual(provider_calls, 1)
        self.assertEqual(entry_point.load_count, 1)
        self.assertEqual(tuple(descriptor_snapshot), (CAPABILITY_ID,))
        self.assertEqual(len(contribution_snapshot), 1)
        self.assertEqual(contribution_snapshot[0].plugin_id, PLUGIN_ID)
        self.assertEqual(contribution_snapshot[0].generation, first["generation"])
        self.assertEqual(contribution_snapshot[0].capability_ids, (CAPABILITY_ID,))
        self.assertEqual(contribution_snapshot[0].contribution_types, ("capabilities",))
        self.assertEqual(
            first["plugins"][0]["contribution_snapshot"],
            contribution_snapshot[0].as_dict(),
        )
        with self.assertRaises(AttributeError):
            contribution_snapshot[0].capability_ids = ()  # type: ignore[misc]
        first["plugins"][0]["contribution_snapshot"]["capabilities"].clear()
        self.assertEqual(contribution_snapshot[0].capability_ids, (CAPABILITY_ID,))
        self.assertIsNot(descriptor_snapshot[CAPABILITY_ID], descriptor)
        with self.assertRaises(TypeError):
            descriptor_snapshot["akane.test.forbidden"] = descriptor  # type: ignore[index]
        self.assertNotIn("plugin_mutation", current_descriptor.raw)
        self.assertNotIn("consumer_mutation", current_descriptor.raw)
        self.assertEqual(invalid_context.reason, "invalid_invocation_context")
        self.assertEqual(host.capability_ids, ())
        self.assertEqual(host.capability_descriptors, {})
        self.assertEqual(invalid.status, "validation_error")
        self.assertEqual(invalid.reason, "missing_required")
        self.assertFalse(valid.is_error)
        self.assertEqual(adapter.invoke_count, 1)
        self.assertEqual(adapter.invocation_contexts, [context])
        self.assertEqual(tuple(descriptor_snapshot), (CAPABILITY_ID,))
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(host.contribution_snapshots, ())
        self.assertEqual(stopped_again, stopped)
        self.assertEqual(adapter.close_count, 1)
        self.assertEqual(after_stop.status, "host_unavailable")

    async def test_empty_plugin_is_rejected_but_capability_is_not_mandatory(self) -> None:
        host = _host(
            (PluginSelection(PLUGIN_ID, True),),
            entry_points_provider=lambda: (_entry_point_for(FakePlugin(())),),
        )

        status = await host.start()

        self.assertEqual(status["status"], "degraded")
        self.assertEqual(status["plugins"][0]["reason"], "plugin_registered_no_contributions")
        await host.stop()

    async def test_invocation_timeout_is_opt_in(self) -> None:
        class SlowAdapter(FakeAdapter):
            async def invoke(self, capability_id: str, args: dict[str, Any], ctx: Any) -> CapabilityResult:
                await asyncio.sleep(0.15)
                return await super().invoke(capability_id, args, ctx)

        unlimited_adapter = SlowAdapter()
        unlimited = _host(
            (PluginSelection(PLUGIN_ID, True),),
            entry_points_provider=lambda: (_entry_point_for(FakePlugin((unlimited_adapter,))),),
        )
        bounded_adapter = SlowAdapter()
        bounded = _host(
            (PluginSelection(PLUGIN_ID, True),),
            entry_points_provider=lambda: (_entry_point_for(FakePlugin((bounded_adapter,))),),
            invoke_timeout_seconds=0.1,
        )
        context = InvocationContext(client_mode="test")

        await unlimited.start()
        completed = await unlimited.invoke(CAPABILITY_ID, {}, context=context)
        await unlimited.stop()
        await bounded.start()
        timed_out = await bounded.invoke(CAPABILITY_ID, {}, context=context)
        await bounded.stop()

        self.assertFalse(completed.is_error)
        self.assertTrue(timed_out.is_error)
        self.assertEqual(timed_out.reason, "plugin_invoke_timeout")

    async def test_missing_enabled_plugin_degrades_without_removing_active_plugin(self) -> None:
        active_id = PLUGIN_ID
        missing_id = "akane.test.missing"
        adapter = FakeAdapter()
        host = _host(
            (PluginSelection(active_id, True), PluginSelection(missing_id, True)),
            entry_points_provider=lambda: (_entry_point_for(FakePlugin((adapter,))),),
        )

        status = await host.start()
        result = await host.invoke(CAPABILITY_ID, {}, context=InvocationContext(client_mode="test"))

        self.assertEqual(status["status"], "degraded")
        self.assertEqual(status["plugin_count"], 1)
        self.assertEqual(status["plugins"][1]["reason"], "plugin_not_installed")
        self.assertFalse(result.is_error)

    async def test_host_preserves_safe_business_error_and_rejects_unsafe_result(self) -> None:
        adapter = FakeAdapter(
            result=CapabilityResult(
                is_error=True,
                status="unavailable",
                reason="provider_unavailable",
                content={"provider": "public_market", "retryable": True},
            )
        )
        host = _host(
            (PluginSelection(PLUGIN_ID, True),),
            entry_points_provider=lambda: (_entry_point_for(FakePlugin((adapter,))),),
        )
        context = InvocationContext(
            profile_user_id="user-42",
            session_id="session-7",
            client_mode="web",
        )
        await host.start()

        reported = await host.invoke(CAPABILITY_ID, {}, context=context)
        adapter.result = CapabilityResult(
            is_error=True,
            status="error",
            reason="provider_unavailable",
            content={"storage_path": "C:\\private\\market.sqlite3"},
        )
        unsafe = await host.invoke(CAPABILITY_ID, {}, context=context)
        await host.stop()

        self.assertTrue(reported.is_error)
        self.assertEqual(reported.status, "unavailable")
        self.assertEqual(reported.reason, "provider_unavailable")
        self.assertEqual(reported.content, {"provider": "public_market", "retryable": True})
        self.assertEqual(unsafe.status, "error")
        self.assertEqual(unsafe.reason, "plugin_result_not_safe")
        self.assertIsNone(unsafe.content)

    async def test_artifact_audit_happens_before_entry_point_load(self) -> None:
        direct_url = json.dumps({"url": "file:///private/source", "dir_info": {"editable": True}})
        entry_point = _entry_point_for(
            FakePlugin((FakeAdapter(),)),
            distribution=FakeDistribution(direct_url=direct_url),
        )
        host = _host((PluginSelection(PLUGIN_ID, True),), entry_points_provider=lambda: (entry_point,))

        status = await host.start()

        self.assertEqual(status["status"], "degraded")
        self.assertEqual(status["plugins"][0]["reason"], "editable_install_forbidden")
        self.assertEqual(entry_point.load_count, 0)
        self.assertNotIn("private", json.dumps(status))

    async def test_duplicate_artifact_and_missing_distribution_metadata_never_import(self) -> None:
        class BrokenDistributionEntryPoint:
            name = PLUGIN_ID
            load_count = 0

            @property
            def dist(self) -> object:
                raise RuntimeError("private metadata error")

            def load(self) -> Callable[..., Any]:
                self.load_count += 1
                raise AssertionError("load must not run")

        duplicate_a = _entry_point_for(FakePlugin((FakeAdapter(),)))
        duplicate_b = _entry_point_for(FakePlugin((FakeAdapter(),)))
        duplicate_host = _host(
            (PluginSelection(PLUGIN_ID, True),),
            entry_points_provider=lambda: (duplicate_a, duplicate_b),
        )
        missing_metadata = _entry_point_for(FakePlugin((FakeAdapter(),)), distribution=object())
        metadata_host = _host(
            (PluginSelection(PLUGIN_ID, True),),
            entry_points_provider=lambda: (missing_metadata,),
        )
        broken_distribution = BrokenDistributionEntryPoint()
        broken_host = _host(
            (PluginSelection(PLUGIN_ID, True),),
            entry_points_provider=lambda: (broken_distribution,),
        )

        duplicate_status = await duplicate_host.start()
        metadata_status = await metadata_host.start()
        broken_status = await broken_host.start()

        self.assertEqual(duplicate_status["plugins"][0]["reason"], "duplicate_plugin_artifact")
        self.assertEqual(duplicate_a.load_count, 0)
        self.assertEqual(duplicate_b.load_count, 0)
        self.assertEqual(metadata_status["plugins"][0]["reason"], "distribution_metadata_unavailable")
        self.assertEqual(missing_metadata.load_count, 0)
        self.assertEqual(broken_status["plugins"][0]["reason"], "distribution_metadata_unavailable")
        self.assertEqual(broken_distribution.load_count, 0)

    async def test_factory_manifest_and_descriptor_contract_failures_are_structured(self) -> None:
        def factory_with_argument(required: object) -> FakePlugin:
            return FakePlugin((FakeAdapter(),))

        invalid_factory = FakeEntryPoint(PLUGIN_ID, factory_with_argument)
        bad_manifest_plugin = FakePlugin((FakeAdapter(),))
        bad_manifest_plugin.manifest = PluginManifest(
            plugin_id=PLUGIN_ID,
            plugin_version="0.1.0",
            plugin_api_version=AKANE_PLUGIN_API_VERSION + 1,
            permissions=("diagnostics.invoke",),
        )
        broader_permission_plugin = FakePlugin((FakeAdapter(),))
        broader_permission_plugin.manifest = PluginManifest(
            plugin_id=PLUGIN_ID,
            plugin_version="0.1.0",
            plugin_api_version=AKANE_PLUGIN_API_VERSION,
            permissions=("finance.invoke",),
        )
        forbidden_adapter = FakeAdapter(_descriptor(prompt_exposed=True))

        cases = (
            (
                _host(
                    (PluginSelection(PLUGIN_ID, True),),
                    entry_points_provider=lambda: (invalid_factory,),
                ),
                "plugin_factory_must_be_zero_parameter",
                "",
                None,
            ),
            (
                _host(
                    (PluginSelection(PLUGIN_ID, True),),
                    entry_points_provider=lambda: (_entry_point_for(bad_manifest_plugin),),
                ),
                "plugin_api_version_mismatch",
                "",
                None,
            ),
            (
                _host(
                    (PluginSelection(PLUGIN_ID, True),),
                    entry_points_provider=lambda: (_entry_point_for(broader_permission_plugin),),
                ),
                "contribution_policy_rejected",
                "manifest_contributions",
                None,
            ),
            (
                _host(
                    (PluginSelection(PLUGIN_ID, True),),
                    entry_points_provider=lambda: (_entry_point_for(FakePlugin((forbidden_adapter,))),),
                ),
                "contribution_policy_rejected",
                "capability_contributions",
                forbidden_adapter,
            ),
        )
        for host, expected_reason, expected_stage, adapter in cases:
            with self.subTest(expected_reason=expected_reason):
                status = await host.start()
                self.assertEqual(status["status"], "degraded")
                self.assertEqual(status["plugins"][0]["reason"], expected_reason)
                self.assertEqual(status["plugins"][0].get("stage", ""), expected_stage)
                self.assertEqual(status["capability_count"], 0)
                if adapter is not None:
                    self.assertEqual(adapter.close_count, 1)

    async def test_activation_failure_closes_all_staged_adapters_in_reverse_without_overwriting_reason(self) -> None:
        close_order: list[str] = []
        first = FakeAdapter(close_order=close_order, close_name="first", close_fails=True)
        second = FakeAdapter(close_order=close_order, close_name="second")
        plugin = FakePlugin((first, second), fail_after_register=True)
        host = _host(
            (PluginSelection(PLUGIN_ID, True),),
            entry_points_provider=lambda: (_entry_point_for(plugin),),
        )

        status = await host.start()
        await host.stop()

        self.assertEqual(status["plugins"][0]["reason"], "plugin_registration_failed")
        self.assertEqual(status["capability_count"], 0)
        self.assertEqual(status["close_failure_count"], 1)
        self.assertEqual(close_order, ["second", "first"])
        self.assertEqual(first.close_count, 1)
        self.assertEqual(second.close_count, 1)

    async def test_activation_cleanup_is_bounded_when_adapter_close_hangs(self) -> None:
        class HangingCloseAdapter(FakeAdapter):
            async def aclose(self) -> None:
                self.close_count += 1
                await asyncio.sleep(10)

        adapter = HangingCloseAdapter()
        host = _host(
            (PluginSelection(PLUGIN_ID, True),),
            entry_points_provider=lambda: (_entry_point_for(FakePlugin((adapter,), fail_after_register=True)),),
            close_timeout_seconds=0.1,
        )
        started_at = asyncio.get_running_loop().time()

        status = await host.start()
        elapsed = asyncio.get_running_loop().time() - started_at

        self.assertLess(elapsed, 1.0)
        self.assertEqual(status["plugins"][0]["reason"], "plugin_registration_failed")
        self.assertEqual(status["close_failure_count"], 1)
        self.assertEqual(adapter.close_count, 1)

    async def test_stop_rejects_new_invocations_and_closes_in_reverse_activation_order(self) -> None:
        gate = asyncio.Event()
        close_order: list[str] = []
        first = FakeAdapter(close_order=close_order, close_name="first", invoke_gate=gate)
        second_descriptor = _descriptor(f"{PLUGIN_ID}.second.v1")
        second = FakeAdapter(second_descriptor, close_order=close_order, close_name="second")
        host = _host(
            (PluginSelection(PLUGIN_ID, True),),
            entry_points_provider=lambda: (_entry_point_for(FakePlugin((first, second))),),
            invoke_timeout_seconds=1.0,
        )
        await host.start()
        context = InvocationContext(client_mode="test")
        inflight = asyncio.create_task(host.invoke(CAPABILITY_ID, {}, context=context))
        await asyncio.sleep(0)
        stopping = asyncio.create_task(host.stop())
        while host.state != "stopping":
            await asyncio.sleep(0)

        rejected = await host.invoke(CAPABILITY_ID, {}, context=context)
        gate.set()
        completed = await inflight
        await stopping

        self.assertEqual(rejected.status, "host_unavailable")
        self.assertFalse(completed.is_error)
        self.assertEqual(close_order, ["second", "first"])
        self.assertEqual(first.close_count, 1)
        self.assertEqual(second.close_count, 1)

    async def test_restart_recreates_installed_plugin_instances_and_advances_generation(self) -> None:
        adapters: list[FakeAdapter] = []

        def factory() -> FakePlugin:
            adapter = FakeAdapter()
            adapters.append(adapter)
            return FakePlugin((adapter,))

        entry_point = FakeEntryPoint(PLUGIN_ID, factory)
        host = _host(
            (PluginSelection(PLUGIN_ID, True),),
            entry_points_provider=lambda: (entry_point,),
        )
        context = InvocationContext(client_mode="test")

        first = await host.start()
        before = await host.invoke(CAPABILITY_ID, {}, context=context)
        restarted = await host.restart()
        after = await host.invoke(CAPABILITY_ID, {}, context=context)
        await host.stop()

        self.assertEqual(first["generation"], 1)
        self.assertEqual(restarted["generation"], 2)
        self.assertEqual(restarted["status"], "active")
        self.assertEqual(entry_point.load_count, 2)
        self.assertEqual(len(adapters), 2)
        self.assertFalse(before.is_error)
        self.assertFalse(after.is_error)
        self.assertEqual(adapters[0].invoke_count, 1)
        self.assertEqual(adapters[0].close_count, 1)
        self.assertEqual(adapters[1].invoke_count, 1)
        self.assertEqual(adapters[1].close_count, 1)


class PluginDiagnosticsRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.adapters: list[FakeAdapter] = []

        def factory() -> FakePlugin:
            adapter = FakeAdapter()
            self.adapters.append(adapter)
            return FakePlugin((adapter,))

        self.entry_point = FakeEntryPoint(PLUGIN_ID, factory)
        self.host = _host(
            (PluginSelection(PLUGIN_ID, True),),
            entry_points_provider=lambda: (self.entry_point,),
        )
        await self.host.start()
        self.selection_store = PluginSelectionStore(
            Path(self.temp_dir.name) / "plugin-selections.json",
            defaults=(PluginSelection(PLUGIN_ID, True),),
            instance_id="test-instance",
        )
        self.extension_management = ExtensionManagementService(
            plugin_host=self.host,
            selection_store=self.selection_store,
        )
        self.adapter = self.adapters[0]
        app = FastAPI()
        app.include_router(
            build_plugins_router(
                plugin_host=self.host,
                extension_management_service=self.extension_management,
            )
        )
        self.app = app

    async def asyncTearDown(self) -> None:
        await self.host.stop()
        self.temp_dir.cleanup()

    async def _request(self, *, peer: str = "127.0.0.1") -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=self.app, client=(peer, 54321))
        return httpx.AsyncClient(transport=transport, base_url="http://akane.local")

    async def test_loopback_route_invokes_real_adapter_and_unknown_capability_is_not_fake_success(self) -> None:
        async with await self._request() as client:
            status = await client.get("/admin/plugins/status")
            invoked = await client.post(f"/admin/plugins/capabilities/{CAPABILITY_ID}/invoke", json={})
            unknown = await client.post("/admin/plugins/capabilities/akane.test.unknown.ping/invoke", json={})

        self.assertEqual(status.status_code, 200)
        status_payload = status.json()
        self.assertEqual(status_payload["capability_count"], 1)
        self.assertEqual(status_payload["kind"], "plugin")
        self.assertEqual(
            status_payload["plugins"][0]["contribution_snapshot"]["capabilities"],
            [CAPABILITY_ID],
        )
        self.assertEqual(
            status_payload["plugins"][0]["contribution_snapshot"]["generation"],
            status_payload["generation"],
        )
        self.assertEqual(invoked.status_code, 200)
        self.assertEqual(invoked.json()["content"]["diagnostic"], "ready")
        self.assertEqual(
            self.adapter.invocation_contexts,
            [InvocationContext(client_mode="plugin_admin")],
        )
        self.assertEqual(unknown.status_code, 404)
        self.assertFalse(unknown.json()["ok"])
        self.assertEqual(unknown.json()["reason"], "unknown_capability")

    async def test_restart_route_recreates_instances_and_reports_real_generation(self) -> None:
        async with await self._request() as client:
            before = await client.get("/admin/plugins/status")
            restarted = await client.post("/admin/plugins/restart")
            invoked = await client.post(f"/admin/plugins/capabilities/{CAPABILITY_ID}/invoke", json={})

        self.assertEqual(before.status_code, 200)
        self.assertEqual(before.json()["generation"], 1)
        self.assertEqual(restarted.status_code, 200)
        self.assertEqual(restarted.json()["generation"], 2)
        self.assertEqual(restarted.json()["status"], "active")
        self.assertEqual(self.entry_point.load_count, 2)
        self.assertEqual(len(self.adapters), 2)
        self.assertEqual(self.adapters[0].close_count, 1)
        self.assertEqual(invoked.status_code, 200)
        self.assertEqual(self.adapters[1].invoke_count, 1)

    async def test_enable_route_persists_and_reconfigures_the_real_host(self) -> None:
        async with await self._request() as client:
            disabled = await client.patch(
                f"/admin/plugins/{PLUGIN_ID}/enabled",
                json={"enabled": False},
            )
            after_disable = await client.get("/admin/plugins/status")
            enabled = await client.patch(
                f"/admin/plugins/{PLUGIN_ID}/enabled",
                json={"enabled": True},
            )

        self.assertEqual(disabled.status_code, 200)
        self.assertEqual(disabled.json()["status"], "disabled")
        self.assertEqual(after_disable.json()["plugins"][0]["status"], "disabled")
        self.assertEqual(enabled.status_code, 200)
        self.assertEqual(enabled.json()["status"], "enabled")
        self.assertTrue(self.selection_store.load()[0].enabled)

    async def test_model_sync_bridge_runs_lifecycle_on_host_loop(self) -> None:
        disabled = await asyncio.to_thread(
            self.extension_management.execute_sync,
            action="disable",
            plugin_id=PLUGIN_ID,
        )
        enabled = await asyncio.to_thread(
            self.extension_management.execute_sync,
            action="enable",
            plugin_id=PLUGIN_ID,
        )

        self.assertTrue(disabled["ok"])
        self.assertEqual(disabled["status"], "disabled")
        self.assertTrue(enabled["ok"])
        self.assertEqual(enabled["status"], "enabled")

    async def test_enable_missing_candidate_rolls_back_without_persisting(self) -> None:
        await self.host.stop()
        missing_host = _host(
            (PluginSelection(PLUGIN_ID, False),),
            entry_points_provider=lambda: (),
        )
        await missing_host.start()
        store = PluginSelectionStore(
            Path(self.temp_dir.name) / "missing-plugin-selections.json",
            defaults=(PluginSelection(PLUGIN_ID, False),),
            instance_id="test-instance",
        )
        service = ExtensionManagementService(plugin_host=missing_host, selection_store=store)

        result = await service.set_enabled(plugin_id=PLUGIN_ID, enabled=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "activation_failed")
        self.assertEqual(missing_host.status_snapshot()["plugins"][0]["status"], "disabled")
        self.assertFalse(store.path.exists())
        await missing_host.stop()

    async def test_forwarded_headers_cannot_turn_non_loopback_peer_into_local_request(self) -> None:
        async with await self._request(peer="203.0.113.8") as client:
            response = await client.get(
                "/admin/plugins/status",
                headers={"x-forwarded-for": "127.0.0.1", "forwarded": "for=127.0.0.1"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["reason"], "local_request_required")

    async def test_invoke_requires_bounded_json_object(self) -> None:
        async with await self._request() as client:
            array_response = await client.post(
                f"/admin/plugins/capabilities/{CAPABILITY_ID}/invoke",
                content="[]",
                headers={"content-type": "application/json"},
            )
            oversized = await client.post(
                f"/admin/plugins/capabilities/{CAPABILITY_ID}/invoke",
                content=b"{" + b" " * MAX_PLUGIN_REQUEST_BYTES + b"}",
                headers={"content-type": "application/json"},
            )

        self.assertEqual(array_response.status_code, 400)
        self.assertEqual(array_response.json()["reason"], "json_object_required")
        self.assertEqual(oversized.status_code, 400)
        self.assertEqual(oversized.json()["reason"], "request_too_large")

    async def test_plugin_result_with_local_path_is_replaced_by_safe_error(self) -> None:
        self.adapter.result = CapabilityResult(
            is_error=False,
            status="ok",
            content={"message": "loaded from C:\\private\\plugin.py"},
        )
        async with await self._request() as client:
            response = await client.post(f"/admin/plugins/capabilities/{CAPABILITY_ID}/invoke", json={})

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json(),
            {
                "ok": False,
                "status": "error",
                "reason": "plugin_result_not_safe",
                "content": None,
            },
        )
        self.assertNotIn("private", response.text)

    async def test_plugin_exception_and_oversized_result_are_structured_without_raw_details(self) -> None:
        self.adapter.result = CapabilityResult(
            is_error=True,
            status="C:\\private\\status",
            reason="api_key_must-not-leak",
            content={"secret": "must-not-leak"},
        )
        async with await self._request() as client:
            reported = await client.post(f"/admin/plugins/capabilities/{CAPABILITY_ID}/invoke", json={})

        self.assertEqual(reported.status_code, 502)
        self.assertEqual(reported.json()["reason"], "plugin_result_not_safe")
        self.assertIsNone(reported.json()["content"])
        self.assertNotIn("private", reported.text)
        self.assertNotIn("must-not-leak", reported.text)

        async def failing_invoke(capability_id: str, args: dict[str, Any], ctx: Any) -> CapabilityResult:
            raise RuntimeError("C:\\private\\plugin.py api_key=must-not-leak")

        self.adapter.invoke = failing_invoke  # type: ignore[method-assign]
        async with await self._request() as client:
            failed = await client.post(f"/admin/plugins/capabilities/{CAPABILITY_ID}/invoke", json={})

        self.assertEqual(failed.status_code, 502)
        self.assertEqual(failed.json()["reason"], "plugin_invoke_failed")
        self.assertNotIn("private", failed.text)
        self.assertNotIn("must-not-leak", failed.text)

        async def oversized_invoke(capability_id: str, args: dict[str, Any], ctx: Any) -> CapabilityResult:
            return CapabilityResult(
                is_error=False,
                status="ok",
                content=["x" * 2000 for _ in range(64)],
            )

        self.adapter.invoke = oversized_invoke  # type: ignore[method-assign]
        async with await self._request() as client:
            oversized = await client.post(f"/admin/plugins/capabilities/{CAPABILITY_ID}/invoke", json={})

        self.assertEqual(oversized.status_code, 502)
        self.assertEqual(oversized.json()["reason"], "plugin_result_too_large")


if __name__ == "__main__":
    unittest.main()
