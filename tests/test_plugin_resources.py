from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from capcore import InvocationContext

from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.generated_files import GeneratedFileService
from companion_v01.plugin_api import ManagedArtifactDraft
from companion_v01.plugin_generation import PluginGenerationProcess, PluginGenerationError
from companion_v01.plugin_generation_callbacks import GenerationHostCallbackRouter
from companion_v01.plugin_generation_protocol import PLUGIN_GENERATION_PROTOCOL
from companion_v01.plugin_managed_artifacts import GeneratedFileManagedArtifactSink
from companion_v01.plugin_resources import (
    GeneratedFileResourceProvider,
    ResourceInvocation,
    ScopedPluginResourcePort,
    current_resource_invocation,
)
from companion_v01.plugin_subprocess import PluginProcessRunner
from companion_v01.store import MemoryStore


PLUGIN_ID = "test.resources"
CAPABILITY_ID = f"{PLUGIN_ID}.copy.v1"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_resource_plugin(root: Path, *, permission: bool = True) -> Path:
    site = root / "site"
    package = site / "resource_fixture"
    metadata = site / "resource_fixture-0.1.0.dist-info"
    package.mkdir(parents=True)
    metadata.mkdir()
    source = textwrap.dedent("""
        import asyncio
        import hashlib
        from capcore import CapabilityDescriptor, CapabilityIOSlot, CapabilityResult, HealthStatus
        from companion_v01.plugin_api import (
            AKANE_PLUGIN_API_VERSION, PluginManifest, ManagedArtifactDraft, ManagedArtifactPayload,
        )

        class Adapter:
            provider_id = "provider.test.resources"
            def __init__(self, port):
                self.port = port
            async def health(self):
                return HealthStatus(ok=True, status="ready")
            async def list_capabilities(self):
                return (CapabilityDescriptor(
                    id="test.resources.copy.v1", display_name="Copy input", short_hint="Copy a scoped input.",
                    visible_in=("diagnostics",), prompt_exposed=True, risk="low", confirm="never",
                    effects=("filesystem",), trigger=None,
                    inputs=(CapabilityIOSlot(name="target", kind="string", required=True),
                            CapabilityIOSlot(name="representation", kind="string", required=False),
                            CapabilityIOSlot(name="delay", kind="integer", required=False)),
                    outputs=(CapabilityIOSlot(name="file", kind="file", required=True,
                                              max_bytes=32*1024*1024, delivery="generated_file"),), raw={},
                ),)
            async def invoke(self, capability_id, args, context):
                if args["target"] == "new":
                    root = await self.port.work_directory()
                    output = root / "new.txt"
                    output.write_bytes(b"a" * (17 * 1024 * 1024))
                    await asyncio.sleep(args.get("delay", 0))
                    return CapabilityResult(is_error=False, status="ok", content=ManagedArtifactPayload(
                        content={"new_file": True}, artifacts=(ManagedArtifactDraft(path=output,
                            title="new-file", output_format="txt", mime_type="text/plain"),)))
                result = await self.port.open(args["target"], representation=args.get("representation", "original"))
                if not result.ok:
                    return CapabilityResult(is_error=True, status=result.status, reason=result.reason)
                digest = hashlib.sha256(result.path.read_bytes()).hexdigest()
                await asyncio.sleep(args.get("delay", 0))
                return CapabilityResult(is_error=False, status="ok", content=ManagedArtifactPayload(
                    content={"digest": digest, "handle": result.handle, "representation": result.representation},
                    artifacts=(ManagedArtifactDraft(path=result.path, title="copied-input",
                                                   output_format="txt", mime_type="text/plain"),),
                ))
            async def aclose(self):
                pass

        class Plugin:
            manifest = PluginManifest(plugin_id="test.resources", plugin_version="0.1.0",
                plugin_api_version=AKANE_PLUGIN_API_VERSION,
                permissions=("capability.prompt.invoke", "artifact.write", RESOURCE_PERMISSION))
            def register(self, registrar):
                registrar.add_capability_adapter(Adapter(registrar.get_resource_port()))
        def create_plugin():
            return Plugin()
    """).replace("RESOURCE_PERMISSION", '"resource.read"' if permission else '"network.read"')
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: resource-fixture\nVersion: 0.1.0\n", encoding="utf-8"
    )
    (metadata / "entry_points.txt").write_text(
        "[akane.plugins.v1]\ntest.resources = resource_fixture:create_plugin\n", encoding="utf-8"
    )
    return site


def services(root: Path):
    store = MemoryStore(root / "store")
    attachments = AttachmentInboxService(store=store, base_dir=root / "attachments")
    service = GeneratedFileService(base_dir=root / "generated", store=store, attachment_service=attachments)
    return store, attachments, service


def add_attachment(root, attachments):
    (root / "attachments").mkdir(exist_ok=True)
    source = root / "attachments" / "source.txt"
    source.write_bytes(b"original resource")
    item = attachments.create_pending(
        profile_user_id="owner",
        session_id="session",
        source="test",
        kind="file",
        origin_name="source.txt",
        file_ext="txt",
        mime_type="text/plain",
        storage_relpath="source.txt",
        file_size=source.stat().st_size,
    )
    return source, attachments.mark_ready(
        profile_user_id="owner", session_id="session", attachment_id=item["attachment_id"]
    )


class ResourcePortTests(unittest.IsolatedAsyncioTestCase):
    async def test_reading_generated_source_does_not_request_delivery_or_change_recency(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, service = services(root)
            context = InvocationContext("owner", "session", "web")
            reference = await GeneratedFileManagedArtifactSink(service).materialize(
                ManagedArtifactDraft(data=b"original", title="source", output_format="txt", mime_type="text/plain", send_to_user=False),
                context=context, capability_id="test.source",
            )
            def resolve():
                return service.resolve_generated_artifact(profile_user_id="owner", session_id="session", target=reference["generated_handle"])
            before = resolve()
            self.assertEqual(before["delivery_status"], "not_requested")
            provider = GeneratedFileResourceProvider(service, work_root=root / "copies")
            scope = ResourceInvocation(PLUGIN_ID, context)
            try:
                for target in (reference["generated_handle"], reference["generated_id"], "latest_generated", "latest"):
                    result = await provider.open(target, invocation=scope)
                    self.assertTrue(result.ok, result.reason)
                    self.assertEqual(resolve(), before)
            finally:
                await scope.aclose()
            sent = service.send_file(profile_user_id="owner", session_id="session", target=reference["generated_handle"])
            self.assertTrue(sent["ok"], sent)
            self.assertEqual(resolve()["delivery_status"], "pending")
            self.assertEqual(sent["files"][0]["generated_file"]["delivery_status"], "pending")

    async def test_document_parser_cancellation_drains_real_child_and_partial_file(self):
        runners = []

        class SlowParser(PluginProcessRunner):
            def __init__(self):
                super().__init__()
                runners.append(self)

            async def run(self, argv, **options):
                return await super().run([
                    sys.executable, "-c",
                    "from pathlib import Path; import sys,time; Path(sys.argv[1]).write_bytes(b'partial'); time.sleep(120)",
                    argv[-1],
                ], **options)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, attachments, service = services(root)
            source, item = add_attachment(root, attachments)
            provider = GeneratedFileResourceProvider(service, work_root=root / "copies")
            scope = ResourceInvocation(PLUGIN_ID, InvocationContext("owner", "session", "web"))
            with patch("companion_v01.plugin_resources.PluginProcessRunner", SlowParser):
                task = asyncio.create_task(provider.open(item["attachment_id"], representation="document", invocation=scope))
                try:
                    for _ in range(250):
                        if list((root / "copies").glob("*/*.json")):
                            break
                        await asyncio.sleep(.02)
                    self.assertTrue(list((root / "copies").glob("*/*.json")))
                    child = next(iter(runners[0].processes))
                    closing = asyncio.create_task(scope.aclose())
                    await asyncio.sleep(0)
                    task.cancel()
                    await asyncio.wait_for(closing, 10)
                    self.assertTrue(task.cancelled())
                    self.assertIsNotNone(child.returncode)
                    self.assertFalse(runners[0].processes)
                    self.assertEqual(list((root / "copies").iterdir()), [])
                    self.assertEqual(source.read_bytes(), b"original resource")
                finally:
                    await scope.aclose()

    async def test_document_representation_real_worker_complete_content_and_scope(self):
        from companion_v01.document_material import read_document_material

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, attachments, service = services(root)
            source, item = add_attachment(root, attachments)
            source.write_text("原文\n" * 12000 + "结尾保留 0 False\n", encoding="utf-8")
            expected = json.dumps(read_document_material(source), ensure_ascii=False, allow_nan=False).encode("utf-8")
            sink = GeneratedFileManagedArtifactSink(service)
            context = InvocationContext("owner", "session", "web")
            generated = await sink.materialize(
                ManagedArtifactDraft(path=source, title="source", output_format="txt", mime_type="text/plain"),
                context=context, capability_id="source",
            )
            generation = PluginGenerationProcess(
                project_root=PROJECT_ROOT, site_dir=write_resource_plugin(root), plugin_id=PLUGIN_ID,
                work_dir=root / "work",
            )
            generation.bind_resource_provider(GeneratedFileResourceProvider(service, work_root=root / "copies"))
            generation.bind_managed_artifact_sink(sink)
            await asyncio.to_thread(generation.start)
            try:
                for target in (item["attachment_id"], generated["generated_handle"]):
                    result = await generation.invoke(CAPABILITY_ID, {"target": target, "representation": "document"}, context=context)
                    self.assertFalse(result.is_error, result.reason)
                    self.assertEqual(result.content["digest"], hashlib.sha256(expected).hexdigest())
                    self.assertEqual(result.content["representation"], "document")
                    self.assertNotIn(str(root), str(result.content))
                    self.assertEqual(list((root / "copies").iterdir()), [])
                    denied = await generation.invoke(CAPABILITY_ID, {"target": target, "representation": "document"},
                                                     context=InvocationContext("owner", "other", "web"))
                    self.assertEqual(denied.reason, "resource_not_found")
                invalid = await generation.invoke(CAPABILITY_ID, {"target": item["attachment_id"], "representation": "preview"}, context=context)
                self.assertEqual(invalid.reason, "resource_representation_invalid")
                source.write_bytes(b"\x00unreadable")
                failed = await generation.invoke(CAPABILITY_ID, {"target": item["attachment_id"], "representation": "document"}, context=context)
                self.assertEqual(failed.reason, "document_text_invalid")
                self.assertEqual(list((root / "copies").iterdir()), [])
            finally:
                await asyncio.to_thread(generation.stop)

    async def test_real_worker_large_input_free_file_and_cancellation_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scratch = root / "temporary"
            scratch.mkdir()
            _, _, service = services(root)
            generation = PluginGenerationProcess(
                project_root=PROJECT_ROOT,
                site_dir=write_resource_plugin(root),
                plugin_id=PLUGIN_ID,
                work_dir=root / "work",
            )
            generation.bind_resource_provider(GeneratedFileResourceProvider(service, work_root=root / "copies"))
            generation.bind_managed_artifact_sink(GeneratedFileManagedArtifactSink(service))
            with patch.dict(os.environ, {"TMPDIR": str(scratch), "TEMP": str(scratch), "TMP": str(scratch)}):
                await asyncio.to_thread(generation.start)
            try:
                context = InvocationContext("owner", "session", "web")
                result = await generation.invoke(CAPABILITY_ID, {"target": "new"}, context=context)
                self.assertFalse(result.is_error, result.reason)
                self.assertEqual(result.content["managed_artifacts"][0]["file_size"], 17 * 1024 * 1024)
                self.assertFalse(list(scratch.glob("akane-plugin-work-*")))
                task = asyncio.create_task(
                    generation.invoke(CAPABILITY_ID, {"target": "new", "delay": 60}, context=context)
                )
                for _ in range(200):
                    if list(scratch.glob("akane-plugin-work-*/new.txt")):
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(list(scratch.glob("akane-plugin-work-*/new.txt")))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertFalse(list(scratch.glob("akane-plugin-work-*")))
                self.assertFalse(list((root / "work").glob("outbox/**/*.*")))
            finally:
                await asyncio.to_thread(generation.stop)

    async def test_work_directory_without_input_has_invocation_lifetime(self):
        port = ScopedPluginResourcePort(PLUGIN_ID, None)
        with self.assertRaisesRegex(RuntimeError, "resource_invocation_required"):
            await port.work_directory()
        scope = ResourceInvocation(PLUGIN_ID, InvocationContext("owner", "session", "web"))
        token = current_resource_invocation.set(scope)
        try:
            path = await port.work_directory()
            (path / "new.bin").write_bytes(b"generated without an input")
            self.assertEqual(await port.work_directory(), path)
            with self.assertRaisesRegex(RuntimeError, "resource_invocation_required"):
                await ScopedPluginResourcePort("wrong.owner", None).work_directory()
            await scope.aclose()
            self.assertFalse(path.exists())
            with self.assertRaisesRegex(RuntimeError, "resource_invocation_required"):
                await port.work_directory()
        finally:
            current_resource_invocation.reset(token)
            await scope.aclose()

    async def test_resource_port_requires_manifest_permission(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generation = PluginGenerationProcess(
                project_root=PROJECT_ROOT,
                site_dir=write_resource_plugin(root, permission=False),
                plugin_id=PLUGIN_ID,
                work_dir=root / "work",
            )
            with self.assertRaises(PluginGenerationError):
                await asyncio.to_thread(generation.start)
            self.assertFalse(generation.capability_descriptors)
            self.assertFalse(generation.running)
            await asyncio.to_thread(generation.stop)

    async def test_port_has_no_scope_outside_invocation_or_after_close(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, attachments, service = services(root)
            source, item = add_attachment(root, attachments)
            provider = GeneratedFileResourceProvider(service, work_root=root / "copies")
            port = ScopedPluginResourcePort(PLUGIN_ID, provider)
            self.assertEqual((await port.open(item["attachment_id"])).reason, "resource_invocation_required")
            scope = ResourceInvocation(PLUGIN_ID, InvocationContext("owner", "session", "web"))
            token = current_resource_invocation.set(scope)
            try:
                result = await port.open(item["attachment_id"])
                self.assertTrue(result.ok, result.reason)
                self.assertNotEqual(result.path, source)
                result.path.write_bytes(b"changed work copy")
                self.assertEqual(source.read_bytes(), b"original resource")
                await scope.aclose()
                self.assertFalse(result.path.exists())
                self.assertEqual((await port.open(item["attachment_id"])).reason, "resource_invocation_required")
                with self.assertRaises(TypeError):
                    await port.open(item["attachment_id"], session_id="other")
            finally:
                current_resource_invocation.reset(token)

    async def test_callback_rejects_unknown_or_expired_invocation(self):
        responses = []
        router = GenerationHostCallbackRouter(
            generation_id="gen", start_timeout_seconds=1, write_response=responses.append
        )
        request = dict(
            protocol=PLUGIN_GENERATION_PROTOCOL,
            generation_id="gen",
            callback_id="a" * 32,
            callback="resource.open",
            invocation_id="unknown",
            target="gen_001",
        )
        router.dispatch(request)
        self.assertEqual(responses[-1]["reason"], "resource_invocation_expired")
        router.begin_invocation("unknown", plugin_id=PLUGIN_ID, context=InvocationContext("owner", "session", "web"))
        router.dispatch(request)
        self.assertEqual(responses[-1]["reason"], "resource_read_permission_required")
        await router.finish_invocation("unknown")
        router.dispatch(request)
        self.assertEqual(responses[-1]["reason"], "resource_invocation_expired")
        router.close()

    async def test_input_copy_cancellation_drains_before_cleanup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, attachments, service = services(root)
            source, item = add_attachment(root, attachments)
            scope = ResourceInvocation(PLUGIN_ID, InvocationContext("owner", "session", "web"))
            provider = GeneratedFileResourceProvider(service, work_root=root / "copies")
            started = threading.Event()

            def slow_copy(source, target, *, expected_size, cancelled):
                target.write_bytes(b"partial")
                started.set()
                if not cancelled.wait(5):
                    raise AssertionError("no cancellation")
                target.write_bytes(b"late write")

            with patch("companion_v01.plugin_resources.copy_file", slow_copy):
                task = asyncio.create_task(provider.open(item["attachment_id"], invocation=scope))
                self.assertTrue(await asyncio.to_thread(started.wait, 5))
                await scope.aclose()
                self.assertTrue(task.cancelled())
            self.assertEqual(list((root / "copies").iterdir()), [])
            self.assertEqual(source.read_bytes(), b"original resource")

    async def test_real_generation_reads_attachment_and_generated_handles_scoped_and_cleans(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, attachments, service = services(root)
            source, item = add_attachment(root, attachments)
            context = InvocationContext("owner", "session", "web")
            sink = GeneratedFileManagedArtifactSink(service)
            generated = await sink.materialize(
                ManagedArtifactDraft(
                    data=b"generated resource", title="source", output_format="txt", mime_type="text/plain"
                ),
                context=context,
                capability_id="source",
            )
            generation = PluginGenerationProcess(
                project_root=PROJECT_ROOT,
                site_dir=write_resource_plugin(root),
                plugin_id=PLUGIN_ID,
                work_dir=root / "work",
            )
            generation.bind_resource_provider(GeneratedFileResourceProvider(service, work_root=root / "copies"))
            generation.bind_managed_artifact_sink(sink)
            await asyncio.to_thread(generation.start)
            try:
                for target in (item["attachment_id"], generated["generated_handle"]):
                    result = await generation.invoke(CAPABILITY_ID, {"target": target}, context=context)
                    self.assertFalse(result.is_error, result.reason)
                    ref = result.content["managed_artifacts"][0]
                    self.assertTrue(ref["generated_id"].startswith("generated::"))
                    self.assertNotIn(str(root), str(result.content))
                    self.assertEqual(list((root / "copies").iterdir()), [])
                for unauthorized in (
                    InvocationContext("other", "session", "web"),
                    InvocationContext("owner", "other", "web"),
                ):
                    for target in (item["attachment_id"], generated["generated_id"]):
                        result = await generation.invoke(CAPABILITY_ID, {"target": target}, context=unauthorized)
                        self.assertTrue(result.is_error)
                        self.assertEqual(result.reason, "resource_not_found")
                result = await generation.invoke(CAPABILITY_ID, {"target": "gen_999"}, context=context)
                self.assertEqual(result.reason, "resource_not_found")
                self.assertEqual(source.read_bytes(), b"original resource")
            finally:
                await asyncio.to_thread(generation.stop)

    async def test_cancelled_worker_releases_resource_before_parent_returns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, attachments, service = services(root)
            source, item = add_attachment(root, attachments)
            ready = asyncio.Event()
            provider = GeneratedFileResourceProvider(service, work_root=root / "copies")

            async def observed_open(target, *, invocation):
                result = await provider.open(target, invocation=invocation)
                if result.ok:
                    ready.set()
                return result

            generation = PluginGenerationProcess(
                project_root=PROJECT_ROOT,
                site_dir=write_resource_plugin(root),
                plugin_id=PLUGIN_ID,
                work_dir=root / "work",
            )
            generation.bind_resource_provider(SimpleNamespace(open=observed_open))
            generation.bind_managed_artifact_sink(GeneratedFileManagedArtifactSink(service))
            await asyncio.to_thread(generation.start)
            try:
                context = InvocationContext("owner", "session", "web")
                task = asyncio.create_task(
                    generation.invoke(CAPABILITY_ID, {"target": item["attachment_id"], "delay": 60}, context=context)
                )
                await asyncio.wait_for(ready.wait(), 5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
                self.assertEqual(list((root / "copies").iterdir()), [])
                self.assertEqual(source.read_bytes(), b"original resource")
                result = await generation.invoke(CAPABILITY_ID, {"target": item["attachment_id"]}, context=context)
                self.assertFalse(result.is_error, result.reason)
            finally:
                await asyncio.to_thread(generation.stop)


if __name__ == "__main__":
    unittest.main()
