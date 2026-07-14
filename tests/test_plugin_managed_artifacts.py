from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from capcore import CapabilityDescriptor, CapabilityIOSlot, CapabilityResult, HealthStatus, InvocationContext

from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.generated_files import GeneratedFileService
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    CAPABILITY_PROMPT_INVOKE_PERMISSION,
    MANAGED_ARTIFACT_WRITE_PERMISSION,
    NETWORK_READ_PERMISSION,
    ManagedArtifactDraft,
    ManagedArtifactPayload,
    PluginManifest,
)
from companion_v01.plugin_contribution_policy import TrustedReadNetworkContributionPolicy
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_managed_artifacts import GeneratedFileManagedArtifactSink, ManagedArtifactError
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.routes.qq import _hydrate_plugin_managed_artifact_events
from companion_v01.store import MemoryStore
from companion_v01.tool_runtime import ToolExecutionContext, ToolExecutionResult


PLUGIN_ID = "akane.test.artifact"
CAPABILITY_ID = f"{PLUGIN_ID}.chart.v1"


def _descriptor(*, max_bytes: int = 1024) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id=CAPABILITY_ID,
        display_name="Managed chart",
        short_hint="Render one chart through host-owned storage.",
        visible_in=("base", "web", "desktop", "qq"),
        prompt_exposed=True,
        risk="low",
        confirm="never",
        effects=("network", "filesystem"),
        trigger=None,
        inputs=(),
        outputs=(
            CapabilityIOSlot(
                name="artifact",
                kind="file",
                required=True,
                max_bytes=max_bytes,
                delivery="generated_file",
            ),
        ),
        raw={"contract": "test.managed-artifact.v1"},
    )


class FakeDistribution:
    version = "0.1.0"
    metadata = {"Name": "akane-test-artifact-plugin"}

    def read_text(self, filename: str) -> None:
        del filename
        return None


class FakeEntryPoint:
    name = PLUGIN_ID
    dist = FakeDistribution()

    def __init__(self, factory: Any) -> None:
        self._factory = factory

    def load(self) -> Any:
        return self._factory


class ArtifactAdapter:
    provider_id = "provider.akane.test.artifact"

    def __init__(self, *, descriptor: CapabilityDescriptor, result: CapabilityResult) -> None:
        self.descriptor = descriptor
        self.result = result
        self.close_count = 0

    async def health(self) -> HealthStatus:
        return HealthStatus(ok=True, status="ready")

    async def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        return (self.descriptor,)

    async def invoke(
        self,
        capability_id: str,
        args: dict[str, Any],
        ctx: InvocationContext,
    ) -> CapabilityResult:
        del capability_id, args, ctx
        return self.result

    async def aclose(self) -> None:
        self.close_count += 1


class ArtifactPlugin:
    def __init__(
        self,
        adapter: ArtifactAdapter,
        *,
        permissions: tuple[str, ...] | None = None,
    ) -> None:
        self.adapter = adapter
        self.manifest = PluginManifest(
            plugin_id=PLUGIN_ID,
            plugin_version="0.1.0",
            plugin_api_version=AKANE_PLUGIN_API_VERSION,
            permissions=permissions
            or (
                CAPABILITY_PROMPT_INVOKE_PERMISSION,
                NETWORK_READ_PERMISSION,
                MANAGED_ARTIFACT_WRITE_PERMISSION,
            ),
        )

    def register(self, registrar: Any) -> None:
        registrar.add_capability_adapter(self.adapter)


def _artifact_result(
    data: bytes = b"test-png-bytes",
    *,
    content: Any = None,
) -> CapabilityResult:
    return CapabilityResult(
        is_error=False,
        status="ok",
        content=ManagedArtifactPayload(
            content={"chart": "ready"} if content is None else content,
            artifact=ManagedArtifactDraft(
                data=data,
                title="market-chart",
                output_format="png",
                mime_type="image/png",
                summary="A bounded test chart.",
                send_to_user=True,
            ),
        ),
    )


def _host_for(
    plugin: ArtifactPlugin,
    *,
    managed_artifact_timeout_seconds: float = 1.0,
) -> PluginHost:
    def factory() -> ArtifactPlugin:
        return plugin

    return PluginHost(
        (PluginSelection(PLUGIN_ID, True),),
        contribution_policy=TrustedReadNetworkContributionPolicy(),
        entry_points_provider=lambda: (FakeEntryPoint(factory),),
        managed_artifact_timeout_seconds=managed_artifact_timeout_seconds,
    )


class PluginManagedArtifactTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_materializes_path_free_payload_and_bridge_emits_safe_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "store")
            service = GeneratedFileService(
                base_dir=root / "outputs",
                store=store,
                attachment_service=AttachmentInboxService(store=store, base_dir=root / "attachments"),
            )
            adapter = ArtifactAdapter(descriptor=_descriptor(), result=_artifact_result())
            host = _host_for(ArtifactPlugin(adapter))
            host.bind_managed_artifact_sink(GeneratedFileManagedArtifactSink(service))
            started = await host.start()
            try:
                result = await host.invoke(
                    CAPABILITY_ID,
                    {},
                    context=InvocationContext(
                        profile_user_id="user-1",
                        session_id="session-1",
                        client_mode="qq_text",
                    ),
                )
                artifact = result.content["managed_artifacts"][0]
                resolved = service.resolve_generated_artifact(
                    profile_user_id="user-1",
                    session_id="session-1",
                    target=artifact["generated_id"],
                )

                self.assertEqual(started["status"], "active")
                self.assertFalse(result.is_error)
                self.assertNotIn("absolute_path", artifact)
                self.assertNotIn("storage_relpath", artifact)
                self.assertIsNotNone(resolved)
                self.assertEqual(Path(resolved["absolute_path"]).read_bytes(), b"test-png-bytes")

                handler = PluginCapabilityToolBridge(host).build_tool_handlers()[CAPABILITY_ID]
                projected = handler._finalize_execution_result(
                    ToolExecutionResult(tool_type=CAPABILITY_ID),
                    capability_result=result,
                    context=ToolExecutionContext(
                        profile_user_id="user-1",
                        session_id="session-1",
                        now_ts=1,
                        visual_payload={},
                        client_mode="qq_text",
                    ),
                )
                event = projected.stream_events[0]
                self.assertEqual(event["type"], "generated_file_ready")
                self.assertEqual(event["delivery_scope"], "plugin_managed_artifact")
                self.assertNotIn("absolute_path", event["generated_file"])
            finally:
                await host.stop()

    async def test_unsafe_public_content_and_reserved_key_never_materialize(self) -> None:
        class RecordingSink:
            def __init__(self) -> None:
                self.calls = 0

            async def materialize(self, draft: Any, *, context: Any, capability_id: str) -> dict[str, Any]:
                del draft, context, capability_id
                self.calls += 1
                return {}

        for content, expected_reason in (
            ({"source": r"C:\\private\\chart.png"}, "plugin_result_not_safe"),
            ({"managed_artifacts": []}, "plugin_result_reserved_key"),
        ):
            with self.subTest(expected_reason=expected_reason):
                adapter = ArtifactAdapter(
                    descriptor=_descriptor(),
                    result=_artifact_result(content=content),
                )
                host = _host_for(ArtifactPlugin(adapter))
                sink = RecordingSink()
                host.bind_managed_artifact_sink(sink)
                await host.start()
                try:
                    result = await host.invoke(
                        CAPABILITY_ID,
                        {},
                        context=InvocationContext("user", "session", "web"),
                    )
                    self.assertTrue(result.is_error)
                    self.assertEqual(result.reason, expected_reason)
                    self.assertEqual(sink.calls, 0)
                finally:
                    await host.stop()

    async def test_descriptor_limit_is_enforced_before_sink_and_late_binding_is_rejected(self) -> None:
        class RecordingSink:
            def __init__(self) -> None:
                self.calls = 0

            async def materialize(self, draft: Any, *, context: Any, capability_id: str) -> dict[str, Any]:
                del draft, context, capability_id
                self.calls += 1
                return {}

        adapter = ArtifactAdapter(descriptor=_descriptor(max_bytes=4), result=_artifact_result(data=b"12345"))
        host = _host_for(ArtifactPlugin(adapter))
        sink = RecordingSink()
        host.bind_managed_artifact_sink(sink)
        await host.start()
        try:
            result = await host.invoke(
                CAPABILITY_ID,
                {},
                context=InvocationContext("user", "session", "web"),
            )
            self.assertTrue(result.is_error)
            self.assertEqual(result.reason, "managed_artifact_too_large")
            self.assertEqual(sink.calls, 0)
            with self.assertRaisesRegex(RuntimeError, "plugin_host_already_started"):
                host.bind_managed_artifact_sink(sink)
        finally:
            await host.stop()

    async def test_sink_rejects_path_title_and_mime_mismatch_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "store")
            service = GeneratedFileService(
                base_dir=root / "outputs",
                store=store,
                attachment_service=AttachmentInboxService(store=store),
            )
            sink = GeneratedFileManagedArtifactSink(service)
            context = InvocationContext("user", "session", "web")
            invalid_drafts = (
                (
                    ManagedArtifactDraft(
                        data=b"content",
                        title=r"..\\private\\report",
                        output_format="md",
                        mime_type="text/markdown",
                    ),
                    "managed_artifact_title_invalid",
                ),
                (
                    ManagedArtifactDraft(
                        data=b"content",
                        title="report",
                        output_format="pdf",
                        mime_type="text/plain",
                    ),
                    "managed_artifact_mime_mismatch",
                ),
            )

            for draft, expected_reason in invalid_drafts:
                with self.subTest(expected_reason=expected_reason):
                    with self.assertRaises(ManagedArtifactError) as captured:
                        await sink.materialize(
                            draft,
                            context=context,
                            capability_id=CAPABILITY_ID,
                        )
                    self.assertEqual(captured.exception.reason, expected_reason)
            self.assertEqual(list((root / "outputs").rglob("*")), [])

    async def test_artifact_descriptor_without_permission_degrades_plugin(self) -> None:
        adapter = ArtifactAdapter(descriptor=_descriptor(), result=_artifact_result())
        plugin = ArtifactPlugin(
            adapter,
            permissions=(CAPABILITY_PROMPT_INVOKE_PERMISSION, NETWORK_READ_PERMISSION),
        )
        host = _host_for(plugin)

        status = await host.start()
        await host.stop()

        self.assertEqual(status["status"], "degraded")
        self.assertEqual(status["plugins"][0]["reason"], "managed_artifact_permission_required")
        self.assertEqual(host.capability_ids, ())

    async def test_missing_host_sink_is_structured_without_blocking_startup(self) -> None:
        adapter = ArtifactAdapter(descriptor=_descriptor(), result=_artifact_result())
        host = _host_for(ArtifactPlugin(adapter))

        status = await host.start()
        try:
            result = await host.invoke(
                CAPABILITY_ID,
                {},
                context=InvocationContext("user", "session", "web"),
            )
            self.assertEqual(status["status"], "active")
            self.assertTrue(result.is_error)
            self.assertEqual(result.reason, "managed_artifact_sink_unavailable")
        finally:
            await host.stop()


class PluginManagedArtifactQQDeliveryTests(unittest.TestCase):
    def test_transport_hydrates_scoped_handle_without_mutating_public_event(self) -> None:
        class Service:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, str]] = []

            def resolve_generated_artifact(
                self,
                *,
                profile_user_id: str,
                session_id: str,
                target: str,
            ) -> dict[str, Any]:
                self.calls.append((profile_user_id, session_id, target))
                return {
                    "generated_id": target,
                    "generated_handle": "gen_001",
                    "absolute_path": r"D:\\host-only\\market-chart.png",
                }

        service = Service()
        engine = SimpleNamespace(_get_generated_file_service=lambda: service)
        public_event = {
            "type": "generated_file_ready",
            "generated_file": {
                "generated_id": "generated::1",
                "generated_handle": "gen_001",
                "output_format": "png",
            },
            "send_to_user": True,
            "delivery_scope": "plugin_managed_artifact",
        }

        hydrated, failures = _hydrate_plugin_managed_artifact_events(
            engine=engine,
            context=SimpleNamespace(profile_user_id="user-1", session_id="session-1"),
            tool_events=[public_event],
        )

        self.assertEqual(failures, [])
        self.assertEqual(service.calls, [("user-1", "session-1", "generated::1")])
        self.assertNotIn("absolute_path", public_event["generated_file"])
        self.assertEqual(hydrated[0]["generated_file"]["absolute_path"], r"D:\\host-only\\market-chart.png")

    def test_transport_reports_unavailable_artifact_instead_of_fake_success(self) -> None:
        service = SimpleNamespace(resolve_generated_artifact=lambda **kwargs: None)
        engine = SimpleNamespace(_get_generated_file_service=lambda: service)
        event = {
            "type": "generated_file_ready",
            "generated_file": {"generated_id": "generated::missing", "generated_handle": "gen_001"},
            "send_to_user": True,
            "delivery_scope": "plugin_managed_artifact",
        }

        hydrated, failures = _hydrate_plugin_managed_artifact_events(
            engine=engine,
            context=SimpleNamespace(profile_user_id="user-1", session_id="session-1"),
            tool_events=[event],
        )

        self.assertEqual(len(hydrated), 1)
        self.assertEqual(failures[0]["reason"], "managed_artifact_unavailable")
        self.assertFalse(failures[0]["ok"])


if __name__ == "__main__":
    unittest.main()
