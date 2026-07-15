"""Tests for PluginStoragePort: InstancePluginStorageService + PluginHost integration."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

from capcore import CapabilityDescriptor, CapabilityResult, HealthStatus, InvocationContext

from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    CAPABILITY_PROMPT_INVOKE_PERMISSION,
    NETWORK_READ_PERMISSION,
    PLUGIN_STORAGE_WRITE_PERMISSION,
    PluginManifest,
)
from companion_v01.plugin_contribution_policy import (
    TrustedReadNetworkContributionPolicy,
    TrustedStatefulPluginContributionPolicy,
)
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_storage import InstancePluginStorageService


PLUGIN_ID = "akane.test.storage"
CAPABILITY_ID = f"{PLUGIN_ID}.query.v1"


# ---------------------------------------------------------------------------
# InstancePluginStorageService unit tests
# ---------------------------------------------------------------------------

class InstancePluginStorageServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_creates_scoped_directory_on_demand(self) -> None:
        svc = InstancePluginStorageService(self._root, "local-default")
        result = svc.get_plugin_data_dir("akane.finance")
        self.assertTrue(result.is_dir())
        # Must be under the expected subtree
        expected = self._root.resolve() / "instances" / "local-default" / "plugins" / "akane.finance"
        self.assertEqual(result.resolve(), expected)

    def test_returns_same_path_on_repeated_calls(self) -> None:
        svc = InstancePluginStorageService(self._root, "local-default")
        p1 = svc.get_plugin_data_dir("akane.finance")
        p2 = svc.get_plugin_data_dir("akane.finance")
        self.assertEqual(p1, p2)

    def test_different_plugins_get_isolated_dirs(self) -> None:
        svc = InstancePluginStorageService(self._root, "local-default")
        p_finance = svc.get_plugin_data_dir("akane.finance")
        p_other = svc.get_plugin_data_dir("akane.other")
        self.assertNotEqual(p_finance, p_other)
        # Neither should be a parent of the other
        self.assertFalse(p_finance.is_relative_to(p_other))
        self.assertFalse(p_other.is_relative_to(p_finance))

    def test_invalid_instance_id_raises(self) -> None:
        with self.assertRaises(ValueError):
            InstancePluginStorageService(self._root, "../bad")
        with self.assertRaises(ValueError):
            InstancePluginStorageService(self._root, "")

    def test_invalid_plugin_id_raises(self) -> None:
        svc = InstancePluginStorageService(self._root, "local-default")
        with self.assertRaises(ValueError):
            svc.get_plugin_data_dir("../escape")
        with self.assertRaises(ValueError):
            svc.get_plugin_data_dir("")

    def test_data_root_must_be_path(self) -> None:
        with self.assertRaises(TypeError):
            InstancePluginStorageService(str(self._root), "local-default")  # type: ignore[arg-type]

    def test_instance_id_property(self) -> None:
        svc = InstancePluginStorageService(self._root, "my-instance")
        self.assertEqual(svc.instance_id, "my-instance")


# ---------------------------------------------------------------------------
# Shared helpers for PluginHost integration tests
# ---------------------------------------------------------------------------

class FakeDistribution:
    def __init__(self, *, version: str = "0.1.0") -> None:
        self.version = version
        self.metadata = {"Name": "akane-test-storage-plugin"}

    def read_text(self, filename: str) -> str | None:
        return None


class FakeEntryPoint:
    def __init__(self, name: str, factory: Callable[..., Any], *, version: str = "0.1.0") -> None:
        self.name = name
        self.dist = FakeDistribution(version=version)
        self._factory = factory

    def load(self) -> Callable[..., Any]:
        return self._factory


def _descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id=CAPABILITY_ID,
        display_name="Storage query",
        short_hint="Query plugin-owned data.",
        visible_in=("base", "web", "desktop", "qq"),
        prompt_exposed=True,
        risk="low",
        confirm="never",
        effects=("network",),
        trigger=None,
        inputs=(),
        outputs=(),
        raw={},
    )


class FakeAdapter:
    provider_id = "provider.test.storage"
    _captured_storage_dir: Path | None = None

    def __init__(self, *, storage_dir: Path | None = None) -> None:
        self._storage_dir = storage_dir
        FakeAdapter._captured_storage_dir = storage_dir

    async def health(self) -> HealthStatus:
        return HealthStatus(ok=True, status="ok")

    async def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        return (_descriptor(),)

    async def invoke(
        self,
        capability_id: str,
        args: Any,
        ctx: InvocationContext,
    ) -> CapabilityResult:
        return CapabilityResult(is_error=False, status="ok", content={"storage_dir": str(self._storage_dir)})

    async def aclose(self) -> None:
        pass


def _make_plugin_with_storage(
    permissions: tuple[str, ...] = (
        CAPABILITY_PROMPT_INVOKE_PERMISSION,
        NETWORK_READ_PERMISSION,
        PLUGIN_STORAGE_WRITE_PERMISSION,
    ),
) -> tuple[Callable, list[Path | None]]:
    """Return a factory and a list that receives the storage_dir captured during register()."""
    captured: list[Path | None] = []

    def factory():
        class Plugin:
            manifest = PluginManifest(
                plugin_id=PLUGIN_ID,
                plugin_version="0.1.0",
                plugin_api_version=AKANE_PLUGIN_API_VERSION,
                permissions=permissions,
            )

            def register(self, registrar):
                storage_dir = registrar.get_storage_dir()
                captured.append(storage_dir)
                adapter = FakeAdapter(storage_dir=storage_dir)
                registrar.add_capability_adapter(adapter)

        return Plugin()

    return factory, captured


def _plugin_entry_points(factory: Callable) -> Callable[[], tuple[FakeEntryPoint, ...]]:
    return lambda: (FakeEntryPoint(PLUGIN_ID, factory),)


# ---------------------------------------------------------------------------
# PluginHost integration tests for storage port
# ---------------------------------------------------------------------------

class PluginHostStorageIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    async def test_plugin_with_storage_write_receives_scoped_dir(self) -> None:
        """A plugin declaring storage.write gets its scoped directory from the registrar."""
        factory, captured = _make_plugin_with_storage()
        storage_svc = InstancePluginStorageService(self._root, "local-default")
        host = PluginHost(
            (PluginSelection(plugin_id=PLUGIN_ID, enabled=True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=_plugin_entry_points(factory),
        )
        host.bind_plugin_storage_service(storage_svc)
        status = await host.start()
        await host.stop()

        self.assertEqual(status["status"], "active")
        self.assertEqual(len(captured), 1)
        self.assertIsInstance(captured[0], Path)
        self.assertTrue(captured[0].is_dir())
        expected = self._root.resolve() / "instances" / "local-default" / "plugins" / PLUGIN_ID
        self.assertEqual(captured[0].resolve(), expected)

    async def test_plugin_without_storage_write_cannot_call_get_storage_dir(self) -> None:
        """Calling get_storage_dir() without storage.write fails activation."""
        # Plugin declares only network permissions, no storage.write
        factory, _ = _make_plugin_with_storage(
            permissions=(
                CAPABILITY_PROMPT_INVOKE_PERMISSION,
                NETWORK_READ_PERMISSION,
            )
        )
        storage_svc = InstancePluginStorageService(self._root, "local-default")
        host = PluginHost(
            (PluginSelection(plugin_id=PLUGIN_ID, enabled=True),),
            contribution_policy=TrustedReadNetworkContributionPolicy(),
            entry_points_provider=_plugin_entry_points(factory),
        )
        host.bind_plugin_storage_service(storage_svc)
        status = await host.start()
        await host.stop()

        # Policy rejects manifest with storage.write via TrustedReadNetworkContributionPolicy,
        # but here permissions lack storage.write so policy passes, but get_storage_dir()
        # raises RuntimeError -> plugin_registration_failed
        # Either contribution_policy_rejected or plugin_registration_failed is acceptable;
        # the host must NOT be active.
        plugin_info = status["plugins"][0]
        self.assertNotEqual(plugin_info["status"], "active")
        # No storage dir should have been created for this plugin
        expected = self._root.resolve() / "instances" / "local-default" / "plugins" / PLUGIN_ID
        self.assertFalse(expected.exists())

    async def test_storage_service_unavailable_fails_plugin_activation(self) -> None:
        """If storage.write is declared but no storage service is bound, plugin fails."""
        factory, _ = _make_plugin_with_storage()
        host = PluginHost(
            (PluginSelection(plugin_id=PLUGIN_ID, enabled=True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=_plugin_entry_points(factory),
        )
        # NO call to bind_plugin_storage_service
        status = await host.start()
        await host.stop()

        self.assertEqual(status["status"], "degraded")
        plugin_info = status["plugins"][0]
        self.assertEqual(plugin_info["status"], "failed")
        self.assertEqual(plugin_info["reason"], "storage_service_unavailable")

    async def test_bind_storage_service_after_start_raises(self) -> None:
        """bind_plugin_storage_service must be called before start."""
        storage_svc = InstancePluginStorageService(self._root, "local-default")
        host = PluginHost(
            (),
            contribution_policy=TrustedReadNetworkContributionPolicy(),
        )
        await host.start()
        with self.assertRaises(RuntimeError):
            host.bind_plugin_storage_service(storage_svc)
        await host.stop()

    def test_bind_invalid_storage_service_raises(self) -> None:
        """bind_plugin_storage_service rejects objects without get_plugin_data_dir."""
        host = PluginHost(
            (),
            contribution_policy=TrustedReadNetworkContributionPolicy(),
        )
        with self.assertRaises(TypeError):
            host.bind_plugin_storage_service(object())  # type: ignore[arg-type]

    async def test_storage_dir_is_isolated_per_plugin_id(self) -> None:
        """Two different plugins get two different non-overlapping directories."""
        PLUGIN_ID_2 = "akane.test.storage2"
        captured_1: list[Path | None] = []
        captured_2: list[Path | None] = []

        def factory_1():
            class P:
                manifest = PluginManifest(
                    plugin_id=PLUGIN_ID,
                    plugin_version="0.1.0",
                    plugin_api_version=AKANE_PLUGIN_API_VERSION,
                    permissions=(
                        CAPABILITY_PROMPT_INVOKE_PERMISSION,
                        NETWORK_READ_PERMISSION,
                        PLUGIN_STORAGE_WRITE_PERMISSION,
                    ),
                )
                def register(self, registrar):
                    captured_1.append(registrar.get_storage_dir())
                    registrar.add_capability_adapter(FakeAdapter())
            return P()

        def factory_2():
            cap2 = CapabilityDescriptor(
                id=f"{PLUGIN_ID_2}.query.v1",
                display_name="Q2", short_hint="q2",
                visible_in=("base",), prompt_exposed=True,
                risk="low", confirm="never", effects=("network",),
                trigger=None, inputs=(), outputs=(), raw={},
            )
            class Adapter2:
                provider_id = "provider.test.storage2"
                async def health(self): return HealthStatus(ok=True, status="ok")
                async def list_capabilities(self): return (cap2,)
                async def invoke(self, *a, **k): return CapabilityResult(is_error=False, status="ok")
                async def aclose(self): pass
            class P:
                manifest = PluginManifest(
                    plugin_id=PLUGIN_ID_2,
                    plugin_version="0.1.0",
                    plugin_api_version=AKANE_PLUGIN_API_VERSION,
                    permissions=(
                        CAPABILITY_PROMPT_INVOKE_PERMISSION,
                        NETWORK_READ_PERMISSION,
                        PLUGIN_STORAGE_WRITE_PERMISSION,
                    ),
                )
                def register(self, registrar):
                    captured_2.append(registrar.get_storage_dir())
                    registrar.add_capability_adapter(Adapter2())
            return P()

        eps = lambda: (
            FakeEntryPoint(PLUGIN_ID, factory_1),
            FakeEntryPoint(PLUGIN_ID_2, factory_2),
        )
        storage_svc = InstancePluginStorageService(self._root, "local-default")
        host = PluginHost(
            (
                PluginSelection(plugin_id=PLUGIN_ID, enabled=True),
                PluginSelection(plugin_id=PLUGIN_ID_2, enabled=True),
            ),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=eps,
        )
        host.bind_plugin_storage_service(storage_svc)
        status = await host.start()
        await host.stop()

        self.assertEqual(status["status"], "active")
        self.assertEqual(len(captured_1), 1)
        self.assertEqual(len(captured_2), 1)
        self.assertNotEqual(captured_1[0], captured_2[0])
        # Neither dir is a parent of the other
        self.assertFalse(captured_1[0].is_relative_to(captured_2[0]))
        self.assertFalse(captured_2[0].is_relative_to(captured_1[0]))


# ---------------------------------------------------------------------------
# TrustedStatefulPluginContributionPolicy storage permission tests
# ---------------------------------------------------------------------------

class TrustedStatefulStoragePolicyTests(unittest.TestCase):
    def _policy(self) -> TrustedStatefulPluginContributionPolicy:
        return TrustedStatefulPluginContributionPolicy()

    def test_accepts_network_and_storage_permissions(self) -> None:
        policy = self._policy()
        manifest = PluginManifest(
            plugin_id=PLUGIN_ID,
            plugin_version="0.1.0",
            plugin_api_version=AKANE_PLUGIN_API_VERSION,
            permissions=(
                CAPABILITY_PROMPT_INVOKE_PERMISSION,
                NETWORK_READ_PERMISSION,
                PLUGIN_STORAGE_WRITE_PERMISSION,
            ),
        )
        self.assertTrue(policy.validate_manifest(manifest).accepted)

    def test_accepts_network_only_permissions(self) -> None:
        policy = self._policy()
        manifest = PluginManifest(
            plugin_id=PLUGIN_ID,
            plugin_version="0.1.0",
            plugin_api_version=AKANE_PLUGIN_API_VERSION,
            permissions=(
                CAPABILITY_PROMPT_INVOKE_PERMISSION,
                NETWORK_READ_PERMISSION,
            ),
        )
        self.assertTrue(policy.validate_manifest(manifest).accepted)

    def test_rejects_storage_only_permissions(self) -> None:
        policy = self._policy()
        manifest = PluginManifest(
            plugin_id=PLUGIN_ID,
            plugin_version="0.1.0",
            plugin_api_version=AKANE_PLUGIN_API_VERSION,
            permissions=(PLUGIN_STORAGE_WRITE_PERMISSION,),
        )
        self.assertFalse(policy.validate_manifest(manifest).accepted)

    def test_accepts_network_storage_and_artifact_permissions(self) -> None:
        from companion_v01.plugin_api import MANAGED_ARTIFACT_WRITE_PERMISSION
        policy = self._policy()
        manifest = PluginManifest(
            plugin_id=PLUGIN_ID,
            plugin_version="0.1.0",
            plugin_api_version=AKANE_PLUGIN_API_VERSION,
            permissions=(
                CAPABILITY_PROMPT_INVOKE_PERMISSION,
                NETWORK_READ_PERMISSION,
                PLUGIN_STORAGE_WRITE_PERMISSION,
                MANAGED_ARTIFACT_WRITE_PERMISSION,
            ),
        )
        self.assertTrue(policy.validate_manifest(manifest).accepted)

    def test_capability_validation_matches_read_policy(self) -> None:
        """Capability rules are identical to TrustedReadNetworkContributionPolicy."""
        policy = self._policy()
        read_policy = TrustedReadNetworkContributionPolicy()
        for desc in (
            _descriptor(),
            CapabilityDescriptor(
                id=CAPABILITY_ID,
                display_name="Bad", short_hint="bad",
                visible_in=("base",), prompt_exposed=False,
                risk="low", confirm="never", effects=("network",),
                trigger=None, inputs=(), outputs=(), raw={},
            ),
        ):
            with self.subTest(desc=desc.id, prompt_exposed=desc.prompt_exposed):
                expected = read_policy.validate_capability(plugin_id=PLUGIN_ID, descriptor=desc)
                actual = policy.validate_capability(plugin_id=PLUGIN_ID, descriptor=desc)
                self.assertEqual(expected.accepted, actual.accepted)


if __name__ == "__main__":
    unittest.main()
