from __future__ import annotations

import asyncio
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
                            CapabilityIOSlot(name="delay", kind="integer", required=False)),
                    outputs=(CapabilityIOSlot(name="file", kind="file", required=True,
                                              max_bytes=1024*1024, delivery="generated_file"),), raw={},
                ),)
            async def invoke(self, capability_id, args, context):
                result = await self.port.open(args["target"])
                if not result.ok:
                    return CapabilityResult(is_error=True, status=result.status, reason=result.reason)
                digest = hashlib.sha256(result.path.read_bytes()).hexdigest()
                await asyncio.sleep(args.get("delay", 0))
                return CapabilityResult(is_error=False, status="ok", content=ManagedArtifactPayload(
                    content={"digest": digest, "handle": result.handle},
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
