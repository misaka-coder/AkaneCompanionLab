from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from capcore import CapabilityDescriptor, CapabilityResult, InvocationContext

from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_generation import PluginGenerationError
from companion_v01.plugin_generation_candidate import (
    PluginGenerationCandidateBuilder,
    PluginGenerationCandidateError,
)
from companion_v01.plugin_installation import PluginGenerationSource, PluginInstallationError


def _descriptor(capability_id: str) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id=capability_id,
        display_name=capability_id,
        short_hint="candidate test",
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


class _Resolver:
    def __init__(self, sources: Mapping[str, PluginGenerationSource]) -> None:
        self.sources = dict(sources)
        self.calls: list[str] = []

    def resolve_generation_source(self, plugin_id: str) -> PluginGenerationSource:
        self.calls.append(plugin_id)
        try:
            return self.sources[plugin_id]
        except KeyError:
            raise PluginInstallationError("plugin_not_installed", status="not_found") from None


class _Process:
    def __init__(
        self,
        *,
        plugin_id: str,
        site_dir: Path,
        fail: bool = False,
        capability_id: str = "",
        **kwargs: Any,
    ) -> None:
        del kwargs
        self.plugin_id = plugin_id
        self.site_dir = site_dir
        self.generation_id = f"generation-{plugin_id}"
        self.running = False
        self.fail = fail
        capability = capability_id or f"{plugin_id}.read.v1"
        self.capability_descriptors = MappingProxyType({capability: _descriptor(capability)})
        self.registered_event_types = ()
        self.registered_hook_types = ()
        self.registered_background_service_ids = ()
        self.registered_qq_commands = ()
        self.stop_count = 0
        self.bindings: dict[str, Any] = {}

    def bind_managed_artifact_sink(self, value: Any) -> None:
        self.bindings["artifact"] = value

    def bind_approved_permissions(self, value: Any) -> None:
        self.bindings["approved_permissions"] = value

    def bind_notification_port(self, value: Any) -> None:
        self.bindings["notification"] = value

    def start(self) -> dict[str, Any]:
        if self.fail:
            raise PluginGenerationError("plugin_generation_start_failed")
        self.running = True
        return {"ok": True, "status": "active", "plugin_id": self.plugin_id}

    def prepare(self):
        return self.start()

    def activate(self):
        return {"ok": True, "status": "active"}

    def stop(self) -> dict[str, Any]:
        self.stop_count += 1
        self.running = False
        return {"ok": True, "status": "stopped"}

    def public_status_snapshot(self) -> dict[str, Any]:
        return {"plugin_id": self.plugin_id, "status": "active"}

    def stable_system_prompt_blocks(self) -> tuple[str, ...]:
        return ()

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
        return CapabilityResult(is_error=False, status="ok")


class PluginGenerationCandidateBuilderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.site_a = self.root / "site-a"
        self.site_b = self.root / "site-b"
        self.site_a.mkdir()
        self.site_b.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def test_waiting_dependency_requires_confirmed_prepared_worker_cleanup(self):
        class WaitingProcess(_Process):
            def public_status_snapshot(self):
                return {"plugin_id": self.plugin_id, "enabled": True, "status": "prepared",
                    "contribution_snapshot": {"requires_services": [{"service_id": "missing", "version": 1}]}}
            def stop(self):
                self.running = False
                return {"ok": False, "reason": "cleanup_failed"}
            def activate(self):
                raise AssertionError("a waiting consumer must not activate")
        source = PluginGenerationSource("example.consumer", self.site_a)
        builder = PluginGenerationCandidateBuilder(source_resolver=_Resolver({source.plugin_id: source}),
            project_root=self.root, work_root=self.root / "workers", process_factory=WaitingProcess)
        with self.assertRaisesRegex(PluginGenerationCandidateError, "plugin_dependency_wait_cleanup_failed"):
            await builder.build((PluginSelection(source.plugin_id, True),))

    async def test_builds_complete_candidate_and_binds_existing_host_ports(self) -> None:
        resolver = _Resolver(
            {
                "akane.test.a": PluginGenerationSource("akane.test.a", self.site_a, "a", ("diagnostics.invoke",)),
                "akane.test.b": PluginGenerationSource("akane.test.b", self.site_b, "b"),
            }
        )
        created: list[_Process] = []

        def factory(**kwargs: Any) -> _Process:
            process = _Process(**kwargs)
            created.append(process)
            return process

        builder = PluginGenerationCandidateBuilder(
            source_resolver=resolver,
            project_root=self.root,
            work_root=self.root / "work",
            process_factory=factory,
            managed_artifact_sink="artifact",
            notification_port="notification",
        )
        snapshot = await builder.build(
            (
                PluginSelection("akane.test.a", True),
                PluginSelection("akane.test.disabled", False),
                PluginSelection("akane.test.b", True),
            )
        )

        self.assertEqual(resolver.calls, ["akane.test.a", "akane.test.b"])
        self.assertEqual(tuple(item.plugin_id for item in snapshot.processes), ("akane.test.a", "akane.test.b"))
        self.assertTrue(snapshot.ready)
        self.assertEqual(
            [item.bindings for item in created],
            [
                {
                    "approved_permissions": ("diagnostics.invoke",),
                    "artifact": "artifact",
                    "notification": "notification",
                },
                {
                    "artifact": "artifact",
                    "notification": "notification",
                },
            ],
        )
        self.assertEqual(
            [item.site_dir for item in created],
            [self.site_a.resolve(), self.site_b.resolve()],
        )

    async def test_missing_source_creates_no_partial_processes(self) -> None:
        resolver = _Resolver(
            {"akane.test.a": PluginGenerationSource("akane.test.a", self.site_a)}
        )
        created: list[_Process] = []
        builder = PluginGenerationCandidateBuilder(
            source_resolver=resolver,
            project_root=self.root,
            work_root=self.root / "work",
            process_factory=lambda **kwargs: created.append(_Process(**kwargs)) or created[-1],
        )

        with self.assertRaises(PluginGenerationCandidateError) as raised:
            await builder.build(
                (
                    PluginSelection("akane.test.a", True),
                    PluginSelection("akane.test.missing", True),
                )
            )

        self.assertEqual(raised.exception.reason, "plugin_not_installed")
        self.assertEqual(raised.exception.plugin_id, "akane.test.missing")
        self.assertEqual(created, [])

    async def test_failed_or_conflicting_candidate_stops_every_started_process(self) -> None:
        resolver = _Resolver(
            {
                "akane.test.a": PluginGenerationSource("akane.test.a", self.site_a),
                "akane.test.b": PluginGenerationSource("akane.test.b", self.site_b),
            }
        )
        created: list[_Process] = []

        def factory(**kwargs: Any) -> _Process:
            process = _Process(
                **kwargs,
                capability_id="akane.test.shared.read.v1",
            )
            created.append(process)
            return process

        builder = PluginGenerationCandidateBuilder(
            source_resolver=resolver,
            project_root=self.root,
            work_root=self.root / "work",
            process_factory=factory,
        )

        with self.assertRaises(PluginGenerationCandidateError) as raised:
            await builder.build(
                (
                    PluginSelection("akane.test.a", True),
                    PluginSelection("akane.test.b", True),
                )
            )

        self.assertEqual(raised.exception.reason, "duplicate_plugin_capability")
        self.assertEqual([item.stop_count for item in created], [1, 1])
        self.assertFalse(any(item.running for item in created))


if __name__ == "__main__":
    unittest.main()
