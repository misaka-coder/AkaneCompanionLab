from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

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
    PluginResultExperience,
    PluginResultPayload,
)
from companion_v01.plugin_contribution_policy import TrustedReadNetworkContributionPolicy
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_managed_artifacts import (
    GeneratedFileManagedArtifactSink,
    ManagedArtifactError,
    ValidatedArtifact,
    validate_managed_artifact_draft,
)
from companion_v01.plugin_generation_artifacts import (
    GenerationArtifactOutboxSink,
    consume_generation_artifact,
    artifact_handoff_scope,
)
from companion_v01.plugin_generation import PluginGenerationProcess
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.routes.qq import _hydrate_plugin_managed_artifact_events
from companion_v01.store import MemoryStore
from companion_v01.tool_runtime import ToolExecutionContext, ToolExecutionResult


PLUGIN_ID = "akane.test.artifact"
CAPABILITY_ID = f"{PLUGIN_ID}.chart.v1"


class DocumentMimeTests(unittest.TestCase):
    def test_canonical_document_types_ignore_machine_file_associations(self):
        mimes = {
            "csv": "text/csv", "txt": "text/plain", "html": "text/html", "json": "application/json",
            "lrc": "text/plain", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
        with patch("companion_v01.plugin_managed_artifacts.mimetypes.guess_type", return_value=("application/x-unexpected", None)):
            for fmt, mime in mimes.items():
                with self.subTest(fmt=fmt):
                    value = validate_managed_artifact_draft(ManagedArtifactDraft(data=b"test", title="document", output_format=fmt, mime_type=mime))
                    self.assertEqual(value.mime_type, mime)
                    with self.assertRaisesRegex(ManagedArtifactError, "managed_artifact_mime_mismatch"):
                        validate_managed_artifact_draft(ManagedArtifactDraft(data=b"test", title="document", output_format=fmt, mime_type="image/png"))


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
            artifacts=(
                ManagedArtifactDraft(
                    data=data,
                    title="market-chart",
                    output_format="png",
                    mime_type="image/png",
                    summary="A bounded test chart.",
                    send_to_user=True,
                ),
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
    async def test_artifact_metadata_and_experience_do_not_change_the_typed_result_value(self):
        class CountingSink(GeneratedFileManagedArtifactSink):
            calls = 0

            async def materialize(self, *args, **kwargs):
                self.calls += 1
                return await super().materialize(*args, **kwargs)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "store")
            service = GeneratedFileService(base_dir=root / "outputs", store=store,
                                           attachment_service=AttachmentInboxService(store=store))
            sink = CountingSink(service)
            adapter = ArtifactAdapter(
                descriptor=replace(_descriptor(), output_schema={"type": "array", "items": {"type": "integer"}}),
                result=_artifact_result(content=PluginResultPayload(
                    content=[0, 1], experience=PluginResultExperience(summary="Chart ready"),
                )),
            )
            host = _host_for(ArtifactPlugin(adapter))
            host.bind_managed_artifact_sink(sink)
            await host.start()
            try:
                result = await host.invoke_from_consumer(CAPABILITY_ID, {}, context=InvocationContext("owner", "session", "web"))
                self.assertFalse(result.is_error, result.reason)
                self.assertEqual(result.value, [0, 1])
                self.assertEqual(result.content["result"], [0, 1])
                self.assertEqual(len(result.content["managed_artifacts"]), 1)
                self.assertEqual(sink.calls, 1)
                adapter.result = _artifact_result(content=[False])
                failed = await host.invoke_from_consumer(CAPABILITY_ID, {}, context=InvocationContext("owner", "session", "web"))
                self.assertEqual(failed.reason, "plugin_result_schema_mismatch")
                self.assertEqual(sink.calls, 1, "invalid result must not publish an artifact or rerun the producer")
            finally:
                await host.stop()

    async def test_unsent_worker_handoff_removes_already_staged_files_on_cancel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sink = GenerationArtifactOutboxSink(root)
            with self.assertRaises(asyncio.CancelledError):
                with artifact_handoff_scope():
                    await sink.materialize(
                        ManagedArtifactDraft(
                            data=b"content", title="report", output_format="md", mime_type="text/markdown"
                        ),
                        context=InvocationContext("user", "session", "web"),
                        capability_id=CAPABILITY_ID,
                    )
                    self.assertEqual(len(list(root.iterdir())), 2)
                    raise asyncio.CancelledError
            self.assertEqual(list(root.iterdir()), [])

    async def test_api_v1_singular_constructor_is_only_a_tuple_adapter(self) -> None:
        draft = ManagedArtifactDraft(data=b"data", title="report", output_format="md", mime_type="text/markdown")
        payload = ManagedArtifactPayload(content={}, artifact=draft)
        self.assertEqual(payload.artifacts, (draft,))
        self.assertEqual(ManagedArtifactPayload({}, draft).artifacts, (draft,))
        self.assertEqual(ManagedArtifactDraft(b"data", "report", "md", "text/markdown"), draft)
        self.assertFalse(hasattr(payload, "artifact"))
        with self.assertRaisesRegex(TypeError, "ambiguous"):
            ManagedArtifactPayload(content={}, artifacts=(draft,), artifact=draft)

    async def test_file_and_multiple_artifacts_use_total_budget_and_emit_all_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.wav"
            with source.open("wb") as stream:
                for _ in range(17):
                    stream.write(b"x" * 1024 * 1024)
            size = source.stat().st_size
            store = MemoryStore(root / "store")
            service = GeneratedFileService(
                base_dir=root / "outputs", store=store, attachment_service=AttachmentInboxService(store=store)
            )
            drafts = (
                ManagedArtifactDraft(path=source, title="large-audio", output_format="wav", mime_type="audio/wav"),
                ManagedArtifactDraft(data=b"report", title="notes", output_format="md", mime_type="text/markdown"),
            )
            adapter = ArtifactAdapter(
                descriptor=_descriptor(max_bytes=size + 6),
                result=CapabilityResult(
                    is_error=False, status="ok", content=ManagedArtifactPayload(content={}, artifacts=drafts)
                ),
            )
            host = _host_for(ArtifactPlugin(adapter), managed_artifact_timeout_seconds=0)
            host.bind_managed_artifact_sink(GeneratedFileManagedArtifactSink(service))
            await host.start()
            try:
                with patch.object(Path, "read_bytes", side_effect=AssertionError("whole-file read forbidden")):
                    result = await host.invoke(CAPABILITY_ID, {}, context=InvocationContext("user", "session", "web"))
                self.assertFalse(result.is_error, result.reason)
                refs = result.content["managed_artifacts"]
                self.assertEqual([r["file_size"] for r in refs], [size, 6])
                handler = PluginCapabilityToolBridge(host).build_tool_handlers()[CAPABILITY_ID]
                projected = handler._finalize_execution_result(
                    ToolExecutionResult(tool_type=CAPABILITY_ID),
                    capability_result=result,
                    context=ToolExecutionContext(
                        profile_user_id="user", session_id="session", now_ts=1, visual_payload={}
                    ),
                )
                self.assertEqual(len(projected.stream_events), 2)
                self.assertEqual(
                    [e["generated_file"]["generated_id"] for e in projected.stream_events],
                    [r["generated_id"] for r in refs],
                )
                self.assertEqual(source.stat().st_size, size)
                with source.open("rb") as stream:
                    self.assertEqual(stream.read(1024), b"x" * 1024)
            finally:
                await host.stop()

    async def test_multi_artifact_budget_and_partial_failure(self) -> None:
        class FailingSecondSink:
            calls = 0

            async def materialize(self, draft, *, context, capability_id):
                self.calls += 1
                if self.calls == 2:
                    raise ManagedArtifactError("managed_artifact_write_failed")
                return dict(
                    generated_id="generated::first",
                    generated_handle="gen_001",
                    output_title=draft.title,
                    output_format=draft.output_format,
                    mime_type=draft.mime_type,
                    file_size=len(draft.data),
                    created_by_tool=capability_id,
                    send_to_user=draft.send_to_user,
                )

        draft = ManagedArtifactDraft(data=b"123", title="report", output_format="md", mime_type="text/markdown")
        for budget, expected_calls, expected_status in ((5, 0, "error"), (6, 2, "partial")):
            with self.subTest(budget=budget):
                adapter = ArtifactAdapter(
                    descriptor=_descriptor(max_bytes=budget),
                    result=CapabilityResult(
                        is_error=False,
                        status="ok",
                        content=ManagedArtifactPayload(content={}, artifacts=(draft, draft)),
                    ),
                )
                sink = FailingSecondSink()
                host = _host_for(ArtifactPlugin(adapter))
                host.bind_managed_artifact_sink(sink)
                await host.start()
                try:
                    result = await host.invoke(CAPABILITY_ID, {}, context=InvocationContext("user", "session", "web"))
                    self.assertTrue(result.is_error)
                    self.assertEqual(result.status, expected_status)
                    self.assertEqual(sink.calls, expected_calls)
                    if expected_status == "partial":
                        self.assertEqual(result.content["managed_artifacts"][0]["generated_id"], "generated::first")
                finally:
                    await host.stop()

    async def test_cancelled_copy_is_drained_before_cleanup_and_never_registers(self) -> None:
        for outbox in (False, True):
            with self.subTest(outbox=outbox), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                store = MemoryStore(root / "store")
                service = GeneratedFileService(
                    base_dir=root / "outputs", store=store, attachment_service=AttachmentInboxService(store=store)
                )
                sink = (
                    GenerationArtifactOutboxSink(root / "outputs")
                    if outbox
                    else GeneratedFileManagedArtifactSink(service)
                )
                started, finished = threading.Event(), threading.Event()

                def slow_copy(artifact, target, *, cancelled):
                    target.write_bytes(b"partial")
                    started.set()
                    if not cancelled.wait(5):
                        raise AssertionError("copy cancellation was not signalled")
                    target.write_bytes(b"late write")
                    finished.set()
                    raise ManagedArtifactError("managed_artifact_copy_cancelled")

                with (
                    patch.object(ValidatedArtifact, "copy_to", slow_copy),
                    patch.object(
                        service, "register_generated_artifact", side_effect=AssertionError("cancelled file registered")
                    ),
                ):
                    task = asyncio.create_task(
                        sink.materialize(
                            ManagedArtifactDraft(
                                data=b"data", title="report", output_format="md", mime_type="text/markdown"
                            ),
                            context=InvocationContext("user", "session", "web"),
                            capability_id=CAPABILITY_ID,
                        )
                    )
                    self.assertTrue(await asyncio.to_thread(started.wait, 5))
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertTrue(finished.is_set())
                self.assertEqual([p for p in (root / "outputs").rglob("*") if p.is_file()], [])

    async def test_outbox_rejects_forged_size_and_unsafe_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sink = GenerationArtifactOutboxSink(root)
            ref = await sink.materialize(
                ManagedArtifactDraft(data=b"data", title="report", output_format="md", mime_type="text/markdown"),
                context=InvocationContext("user", "session", "web"),
                capability_id=CAPABILITY_ID,
            )
            ref["file_size"] += 1
            with self.assertRaisesRegex(ManagedArtifactError, "handoff_invalid"):
                consume_generation_artifact(root, ref, capability_id=CAPABILITY_ID)
            self.assertEqual(list(root.iterdir()), [])
            with self.assertRaisesRegex(ManagedArtifactError, "handoff_invalid"):
                consume_generation_artifact(
                    root, {"generated_id": "generated::generation-artifact:../escape"}, capability_id=CAPABILITY_ID
                )

    async def test_parent_rejects_duplicate_total_budget_and_cleans_trailing_outbox(self) -> None:
        for failure in ("duplicate", "budget", "unavailable"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=root, site_dir=root, plugin_id=PLUGIN_ID, work_dir=root
                )
                generation._capability_descriptors = {
                    CAPABILITY_ID: _descriptor(max_bytes=3 if failure == "budget" else 1024)
                }
                sink = GenerationArtifactOutboxSink(generation._artifact_outbox_dir)
                references = []
                for _ in range(3):
                    references.append(
                        await sink.materialize(
                            ManagedArtifactDraft(
                                data=b"data", title="report", output_format="md", mime_type="text/markdown"
                            ),
                            context=InvocationContext("user", "session", "web"),
                            capability_id=CAPABILITY_ID,
                        )
                    )
                if failure == "duplicate":
                    references.append(references[0])
                if failure == "unavailable":
                    (generation._artifact_outbox_dir / (references[1]["generated_id"].split(":")[-1] + ".bin")).unlink()
                result = await generation._materialize_generation_artifact(
                    CapabilityResult(is_error=False, status="ok", content={"managed_artifacts": references}),
                    capability_id=CAPABILITY_ID,
                    context=InvocationContext("user", "session", "web"),
                )
                self.assertTrue(result.is_error)
                self.assertEqual(
                    result.reason,
                    {
                        "duplicate": "managed_artifact_handoff_invalid",
                        "budget": "managed_artifact_too_large",
                        "unavailable": "managed_artifact_handoff_unavailable",
                    }[failure],
                )
                self.assertEqual(list(generation._artifact_outbox_dir.iterdir()), [])

    async def test_host_materializes_path_free_payload_and_bridge_emits_safe_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "store")
            service = GeneratedFileService(
                base_dir=root / "outputs",
                store=store,
                attachment_service=AttachmentInboxService(store=store, base_dir=root / "attachments"),
            )
            adapter = ArtifactAdapter(
                descriptor=_descriptor(),
                result=_artifact_result(
                    content=PluginResultPayload(
                        content={"chart": "ready"},
                        experience=PluginResultExperience(
                            summary="测试图表已经生成。",
                            facts=("图表包含最近二十个交易日。",),
                            as_of="2026-07-14 15:00:00 Asia/Shanghai",
                            warnings=("图表只代表历史数据。",),
                            interpretation_notes=("价格采用后复权口径。",),
                        ),
                    )
                ),
            )
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
                metadata = handler.tool_metadata()
                model_feedback = handler._format_capability_result(result)
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
                self.assertIn("Akane 已登记", model_feedback)
                self.assertIn("此工具结果尚不代表投递成功", model_feedback)
                self.assertEqual(metadata.family, "plugin_artifact")
                self.assertEqual(metadata.operation, "mixed")
                self.assertFalse(metadata.is_read_only)
            finally:
                await host.stop()

    async def test_invalid_json_and_reserved_key_never_materialize(self) -> None:
        class RecordingSink:
            def __init__(self) -> None:
                self.calls = 0

            async def materialize(self, draft: Any, *, context: Any, capability_id: str) -> dict[str, Any]:
                del draft, context, capability_id
                self.calls += 1
                return {}

        for content, expected_reason in (
            ({"source": object()}, "plugin_result_unsupported_type"),
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

    async def test_portable_mime_is_independent_of_windows_file_associations(self):
        from companion_v01.plugin_managed_artifacts import validate_managed_artifact_draft

        with patch("companion_v01.plugin_managed_artifacts.mimetypes.guess_type", return_value=("text/plain", None)):
            for fmt, mime in (("srt", "application/x-subrip"), ("vtt", "text/vtt"), ("zip", "application/zip")):
                result = validate_managed_artifact_draft(
                    ManagedArtifactDraft(data=b"fixture subtitle", title="test", output_format=fmt, mime_type=mime)
                )
                self.assertEqual(result.mime_type, mime)

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
