from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import dataclass
from typing import Any, Callable

import httpx
from capcore import CapabilityDescriptor, CapabilityIOSlot, CapabilityResult, HealthStatus
from fastapi import FastAPI

from companion_v01.distribution_artifacts import audit_distribution_artifact
from companion_v01.instance_profile import PluginSelection
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

    async def health(self) -> HealthStatus:
        return HealthStatus(ok=True, status="ready")

    async def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        return self.descriptors

    async def invoke(self, capability_id: str, args: dict[str, Any], ctx: Any) -> CapabilityResult:
        self.invoke_count += 1
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
        invalid = await host.invoke(CAPABILITY_ID, {})
        valid = await host.invoke(CAPABILITY_ID, {"probe": "ready"})
        stopped = await host.stop()
        stopped_again = await host.stop()
        after_stop = await host.invoke(CAPABILITY_ID, {"probe": "ready"})

        self.assertEqual(first["status"], "active")
        self.assertEqual(second, first)
        self.assertEqual(provider_calls, 1)
        self.assertEqual(entry_point.load_count, 1)
        self.assertEqual(host.capability_ids, ())
        self.assertEqual(invalid.status, "validation_error")
        self.assertEqual(invalid.reason, "missing_required")
        self.assertFalse(valid.is_error)
        self.assertEqual(adapter.invoke_count, 1)
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped_again, stopped)
        self.assertEqual(adapter.close_count, 1)
        self.assertEqual(after_stop.status, "host_unavailable")

    async def test_missing_enabled_plugin_degrades_without_removing_active_plugin(self) -> None:
        active_id = PLUGIN_ID
        missing_id = "akane.test.missing"
        adapter = FakeAdapter()
        host = _host(
            (PluginSelection(active_id, True), PluginSelection(missing_id, True)),
            entry_points_provider=lambda: (_entry_point_for(FakePlugin((adapter,))),),
        )

        status = await host.start()
        result = await host.invoke(CAPABILITY_ID, {})

        self.assertEqual(status["status"], "degraded")
        self.assertEqual(status["plugin_count"], 1)
        self.assertEqual(status["plugins"][1]["reason"], "plugin_not_installed")
        self.assertFalse(result.is_error)

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
        inflight = asyncio.create_task(host.invoke(CAPABILITY_ID, {}))
        await asyncio.sleep(0)
        stopping = asyncio.create_task(host.stop())
        while host.state != "stopping":
            await asyncio.sleep(0)

        rejected = await host.invoke(CAPABILITY_ID, {})
        gate.set()
        completed = await inflight
        await stopping

        self.assertEqual(rejected.status, "host_unavailable")
        self.assertFalse(completed.is_error)
        self.assertEqual(close_order, ["second", "first"])
        self.assertEqual(first.close_count, 1)
        self.assertEqual(second.close_count, 1)


class PluginDiagnosticsRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.adapter = FakeAdapter()
        self.host = _host(
            (PluginSelection(PLUGIN_ID, True),),
            entry_points_provider=lambda: (_entry_point_for(FakePlugin((self.adapter,))),),
        )
        await self.host.start()
        app = FastAPI()
        app.include_router(build_plugins_router(plugin_host=self.host))
        self.app = app

    async def asyncTearDown(self) -> None:
        await self.host.stop()

    async def _request(self, *, peer: str = "127.0.0.1") -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=self.app, client=(peer, 54321))
        return httpx.AsyncClient(transport=transport, base_url="http://akane.local")

    async def test_loopback_route_invokes_real_adapter_and_unknown_capability_is_not_fake_success(self) -> None:
        async with await self._request() as client:
            status = await client.get("/admin/plugins/status")
            invoked = await client.post(f"/admin/plugins/capabilities/{CAPABILITY_ID}/invoke", json={})
            unknown = await client.post("/admin/plugins/capabilities/akane.test.unknown.ping/invoke", json={})

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["capability_count"], 1)
        self.assertEqual(invoked.status_code, 200)
        self.assertEqual(invoked.json()["content"]["diagnostic"], "ready")
        self.assertEqual(unknown.status_code, 404)
        self.assertFalse(unknown.json()["ok"])
        self.assertEqual(unknown.json()["reason"], "unknown_capability")

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
        self.assertEqual(reported.json()["reason"], "plugin_reported_error")
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
