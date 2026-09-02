from __future__ import annotations

import asyncio
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path

from capcore import CapabilityDescriptor, CapabilityIOSlot, CapabilityResult, InvocationContext

from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.generated_files import GeneratedFileService
from companion_v01.plugin_api import NotificationIntent, NotificationResult
from companion_v01.plugin_generation import (
    PLUGIN_GENERATION_PROTOCOL,
    PluginGenerationError,
    PluginGenerationProcess,
)
from companion_v01.plugin_generation_codec import (
    PluginGenerationCodecError,
    capability_descriptor_from_wire,
    capability_descriptor_to_wire,
    capability_result_from_wire,
    capability_result_to_wire,
    invocation_context_from_wire,
    invocation_context_to_wire,
    json_snapshot,
    notification_intent_from_wire,
    notification_intent_to_wire,
    notification_result_from_wire,
    notification_result_to_wire,
)
from companion_v01.plugin_managed_artifacts import GeneratedFileManagedArtifactSink
from companion_v01.store import MemoryStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_plugin_site(root: Path, *, broken: bool = False) -> Path:
    site = root / "site"
    package = site / "generation_fixture"
    dist_info = site / "generation_fixture-0.1.0.dist-info"
    package.mkdir(parents=True)
    dist_info.mkdir(parents=True)
    if broken:
        source = "def create_plugin():\n    raise RuntimeError('broken')\n"
    else:
        source = textwrap.dedent(
            """
            from companion_v01.plugin_api import (
                AKANE_PLUGIN_API_VERSION,
                DIRECT_CONVERSATION_EVENT,
                EVENT_SUBSCRIBE_PERMISSION,
                PluginEventResult,
                PluginManifest,
            )

            class Handler:
                async def handle_event(self, event):
                    return PluginEventResult()

            class Plugin:
                manifest = PluginManifest(
                    plugin_id="test.generation",
                    plugin_version="0.1.0",
                    plugin_api_version=AKANE_PLUGIN_API_VERSION,
                    permissions=(EVENT_SUBSCRIBE_PERMISSION,),
                )

                def register(self, registrar):
                    print("plugin output must not enter the protocol lane")
                    registrar.add_event_handler(DIRECT_CONVERSATION_EVENT, Handler())

            def create_plugin():
                return Plugin()
            """
        )
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: generation-fixture\nVersion: 0.1.0\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[akane.plugins.v1]\ntest.generation = generation_fixture:create_plugin\n",
        encoding="utf-8",
    )
    return site


def _write_capability_plugin_site(root: Path) -> Path:
    site = root / "site"
    package = site / "generation_fixture"
    dist_info = site / "generation_fixture-0.1.0.dist-info"
    package.mkdir(parents=True)
    dist_info.mkdir(parents=True)
    source = textwrap.dedent(
        """
        import asyncio

        from capcore import (
            CapabilityDescriptor,
            CapabilityIOSlot,
            CapabilityResult,
            HealthStatus,
        )
        from companion_v01.plugin_api import (
            AKANE_PLUGIN_API_VERSION,
            CAPABILITY_PROMPT_INVOKE_PERMISSION,
            MANAGED_ARTIFACT_WRITE_PERMISSION,
            NOTIFICATION_SEND_PERMISSION,
            ManagedArtifactDraft,
            ManagedArtifactPayload,
            NotificationIntent,
            PluginManifest,
        )

        CAPABILITY_ID = "test.generation.echo.v1"
        ARTIFACT_CAPABILITY_ID = "test.generation.artifact.v1"
        NOTIFICATION_CAPABILITY_ID = "test.generation.notification.v1"

        class Adapter:
            provider_id = "provider.test.generation"

            def __init__(self, notification_port):
                self._notification_port = notification_port

            async def health(self):
                return HealthStatus(ok=True, status="ready")

            async def list_capabilities(self):
                return (
                    CapabilityDescriptor(
                        id=CAPABILITY_ID,
                        display_name="Generation echo",
                        short_hint="Echo public JSON values after an optional delay.",
                        visible_in=("diagnostics",),
                        prompt_exposed=True,
                        risk="low",
                        confirm="never",
                        effects=(),
                        trigger=None,
                        inputs=(
                            CapabilityIOSlot(name="value", kind="string", required=True),
                            CapabilityIOSlot(name="delay_ms", kind="integer", required=False),
                        ),
                        outputs=(),
                        raw={"contract": "generation-echo.v1"},
                    ),
                    CapabilityDescriptor(
                        id=ARTIFACT_CAPABILITY_ID,
                        display_name="Generation artifact",
                        short_hint="Create one host-managed Markdown artifact.",
                        visible_in=("diagnostics",),
                        prompt_exposed=True,
                        risk="low",
                        confirm="never",
                        effects=("filesystem",),
                        trigger=None,
                        inputs=(
                            CapabilityIOSlot(name="size", kind="integer", required=False),
                        ),
                        outputs=(
                            CapabilityIOSlot(
                                name="report",
                                kind="file",
                                required=True,
                                max_bytes=2 * 1024 * 1024,
                                delivery="generated_file",
                            ),
                        ),
                        raw={"contract": "generation-artifact.v1"},
                    ),
                    CapabilityDescriptor(
                        id=NOTIFICATION_CAPABILITY_ID,
                        display_name="Generation notification",
                        short_hint="Send one host-owned test notification.",
                        visible_in=("diagnostics",),
                        prompt_exposed=True,
                        risk="low",
                        confirm="never",
                        effects=(),
                        trigger=None,
                        inputs=(
                            CapabilityIOSlot(name="recipient_id", kind="string", required=True),
                            CapabilityIOSlot(name="text", kind="string", required=True),
                            CapabilityIOSlot(name="idempotency_key", kind="string", required=True),
                        ),
                        outputs=(),
                        raw={"contract": "generation-notification.v1"},
                    ),
                )

            async def invoke(self, capability_id, args, context):
                if capability_id == ARTIFACT_CAPABILITY_ID:
                    size = max(1, int(args.get("size", 24)))
                    return CapabilityResult(
                        is_error=False,
                        status="ok",
                        content=ManagedArtifactPayload(
                            content={"report": "ready"},
                            artifact=ManagedArtifactDraft(
                                data=b"generation-report\\n".ljust(size, b"x"),
                                title="generation-report",
                                output_format="md",
                                mime_type="text/markdown",
                                summary="A generation handoff test report.",
                                send_to_user=True,
                            ),
                        ),
                    )
                if capability_id == NOTIFICATION_CAPABILITY_ID:
                    delivery = await self._notification_port.send(
                        NotificationIntent(
                            channel="qq_text",
                            recipient_id=args["recipient_id"],
                            text=args["text"],
                            idempotency_key=args["idempotency_key"],
                        )
                    )
                    return CapabilityResult(
                        is_error=False,
                        status="ok",
                        content={
                            "delivery_ok": delivery.ok,
                            "delivery_status": delivery.status,
                            "delivery_reason": delivery.reason,
                        },
                    )
                await asyncio.sleep(max(0, args.get("delay_ms", 0)) / 1000)
                return CapabilityResult(
                    is_error=False,
                    status="ok",
                    content={
                        "capability_id": capability_id,
                        "value": args["value"],
                        "profile_user_id": context.profile_user_id,
                        "session_id": context.session_id,
                        "client_mode": context.client_mode,
                    },
                )

            async def aclose(self):
                return None

        class Plugin:
            manifest = PluginManifest(
                plugin_id="test.generation",
                plugin_version="0.1.0",
                plugin_api_version=AKANE_PLUGIN_API_VERSION,
                permissions=(
                    CAPABILITY_PROMPT_INVOKE_PERMISSION,
                    MANAGED_ARTIFACT_WRITE_PERMISSION,
                    NOTIFICATION_SEND_PERMISSION,
                ),
            )

            def register(self, registrar):
                registrar.add_capability_adapter(
                    Adapter(registrar.get_notification_port())
                )

        def create_plugin():
            return Plugin()
        """
    )
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: generation-fixture\nVersion: 0.1.0\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[akane.plugins.v1]\ntest.generation = generation_fixture:create_plugin\n",
        encoding="utf-8",
    )
    return site


def _write_notification_job_plugin_site(root: Path) -> Path:
    site = root / "site"
    package = site / "generation_notification_fixture"
    dist_info = site / "generation_notification_fixture-0.1.0.dist-info"
    package.mkdir(parents=True)
    dist_info.mkdir(parents=True)
    source = textwrap.dedent(
        """
        from companion_v01.plugin_api import (
            AKANE_PLUGIN_API_VERSION,
            BACKGROUND_JOB_PERMISSION,
            NOTIFICATION_SEND_PERMISSION,
            NotificationIntent,
            PluginManifest,
        )

        class StartupNotificationJob:
            def __init__(self, notification_port):
                self._notification_port = notification_port

            async def start(self, controller):
                del controller
                await self._notification_port.send(
                    NotificationIntent(
                        channel="qq_text",
                        recipient_id="user:123456",
                        text="background-ready",
                        idempotency_key="background-ready-1",
                    )
                )

            async def stop(self):
                return None

        class Plugin:
            manifest = PluginManifest(
                plugin_id="test.generation.notification-job",
                plugin_version="0.1.0",
                plugin_api_version=AKANE_PLUGIN_API_VERSION,
                permissions=(
                    BACKGROUND_JOB_PERMISSION,
                    NOTIFICATION_SEND_PERMISSION,
                ),
            )

            def register(self, registrar):
                registrar.add_background_service(
                    "startup-notification",
                    StartupNotificationJob(registrar.get_notification_port()),
                )

        def create_plugin():
            return Plugin()
        """
    )
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\n"
        "Name: generation-notification-fixture\n"
        "Version: 0.1.0\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[akane.plugins.v1]\n"
        "test.generation.notification-job = "
        "generation_notification_fixture:create_plugin\n",
        encoding="utf-8",
    )
    return site


def _descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id="test.generation.echo.v1",
        display_name="Generation echo",
        short_hint="Echo one value.",
        visible_in=("diagnostics",),
        prompt_exposed=True,
        risk="low",
        confirm="never",
        effects=(),
        trigger=None,
        inputs=(CapabilityIOSlot(name="value", kind="string", required=True),),
        outputs=(),
        raw={"contract": "generation-echo.v1"},
    )


class PluginGenerationCodecTests(unittest.TestCase):
    def test_public_capcore_values_round_trip_as_json(self) -> None:
        descriptor = _descriptor()
        context = InvocationContext(
            profile_user_id="主人",
            session_id="session-1",
            client_mode="qq_text",
        )
        result = CapabilityResult(
            is_error=False,
            status="ok",
            content={"text": "完成", "items": [1, True, None]},
        )
        intent = NotificationIntent(
            channel="qq_text",
            recipient_id="user:123",
            text="提醒",
            idempotency_key="notice-1",
        )
        notification_result = NotificationResult(
            ok=True,
            status="delivered",
            reason="",
        )

        self.assertEqual(
            capability_descriptor_from_wire(capability_descriptor_to_wire(descriptor)),
            descriptor,
        )
        self.assertEqual(
            invocation_context_from_wire(invocation_context_to_wire(context)),
            context,
        )
        self.assertEqual(
            capability_result_from_wire(capability_result_to_wire(result)),
            result,
        )
        self.assertEqual(
            notification_intent_from_wire(notification_intent_to_wire(intent)),
            intent,
        )
        self.assertEqual(
            notification_result_from_wire(
                notification_result_to_wire(notification_result)
            ),
            notification_result,
        )

    def test_non_json_values_are_rejected_without_a_size_policy(self) -> None:
        with self.assertRaises(PluginGenerationCodecError):
            json_snapshot({"invalid": object()})
        with self.assertRaises(PluginGenerationCodecError):
            json_snapshot({"nested": {1: "key coercion is not allowed"}})
        with self.assertRaises(PluginGenerationCodecError):
            capability_descriptor_from_wire(
                {
                    **capability_descriptor_to_wire(_descriptor()),
                    "prompt_exposed": "true",
                }
            )

        payload = {"text": "字" * 100_000}
        self.assertEqual(json_snapshot(payload), payload)


class PluginGenerationProcessTests(unittest.TestCase):
    def test_generation_starts_reports_health_and_drains(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generation = PluginGenerationProcess(
                project_root=PROJECT_ROOT,
                site_dir=_write_plugin_site(root),
                plugin_id="test.generation",
                work_dir=root / "work",
            )

            ready = generation.start()
            self.assertTrue(ready["ok"])
            self.assertEqual(ready["protocol"], PLUGIN_GENERATION_PROTOCOL)
            self.assertEqual(ready["plugin_id"], "test.generation")
            self.assertGreater(ready["startup_ms"], 0)
            self.assertTrue(generation.running)

            health = generation.health()
            self.assertTrue(health["ok"])
            self.assertEqual(health["status"], "active")
            self.assertEqual(health["snapshot"]["plugin_count"], 1)

            stopped = generation.stop()
            self.assertTrue(stopped["ok"])
            self.assertEqual(stopped["status"], "stopped")
            self.assertFalse(generation.running)
            self.assertEqual(generation.stop()["reason"], "already_stopped")

    def test_failed_candidate_does_not_remain_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generation = PluginGenerationProcess(
                project_root=PROJECT_ROOT,
                site_dir=_write_plugin_site(root, broken=True),
                plugin_id="test.generation",
                work_dir=root / "work",
            )

            with self.assertRaises(PluginGenerationError):
                generation.start()
            self.assertFalse(generation.running)

    def test_generation_publishes_and_invokes_capabilities(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=root / "work",
                )
                ready = generation.start()
                try:
                    self.assertEqual(len(ready["capabilities"]), 3)
                    self.assertEqual(
                        tuple(generation.capability_descriptors),
                        (
                            "test.generation.artifact.v1",
                            "test.generation.echo.v1",
                            "test.generation.notification.v1",
                        ),
                    )
                    result = await generation.invoke(
                        "test.generation.echo.v1",
                        {"value": "你好"},
                        context=InvocationContext(
                            profile_user_id="owner",
                            session_id="session-1",
                            client_mode="qq_text",
                        ),
                    )
                    self.assertFalse(result.is_error)
                    self.assertEqual(result.content["value"], "你好")
                    self.assertEqual(result.content["session_id"], "session-1")
                    missing = await generation.invoke(
                        "test.generation.missing.v1",
                        {},
                        context=InvocationContext(),
                    )
                    self.assertTrue(missing.is_error)
                    self.assertEqual(missing.status, "not_found")
                    self.assertEqual(missing.reason, "unknown_capability")
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_generation_materializes_artifact_through_host_owned_sink(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                work_dir = root / "work"
                store = MemoryStore(root / "store")
                service = GeneratedFileService(
                    base_dir=root / "outputs",
                    store=store,
                    attachment_service=AttachmentInboxService(
                        store=store,
                        base_dir=root / "attachments",
                    ),
                )
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=work_dir,
                )
                generation.bind_managed_artifact_sink(
                    GeneratedFileManagedArtifactSink(service)
                )
                generation.start()
                try:
                    result = await generation.invoke(
                        "test.generation.artifact.v1",
                        {"size": 1024 * 1024},
                        context=InvocationContext(
                            profile_user_id="owner",
                            session_id="artifact-session",
                            client_mode="qq_text",
                        ),
                    )
                    self.assertFalse(result.is_error, result.reason)
                    artifact = result.content["managed_artifacts"][0]
                    resolved = service.resolve_generated_artifact(
                        profile_user_id="owner",
                        session_id="artifact-session",
                        target=artifact["generated_id"],
                    )

                    self.assertEqual(result.content["report"], "ready")
                    self.assertTrue(artifact["generated_id"].startswith("generated::"))
                    self.assertNotIn("generation-artifact", artifact["generated_id"])
                    self.assertNotIn("absolute_path", artifact)
                    self.assertNotIn("storage_relpath", artifact)
                    self.assertEqual(artifact["file_size"], 1024 * 1024)
                    self.assertIsNotNone(resolved)
                    self.assertEqual(
                        Path(resolved["absolute_path"]).stat().st_size,
                        1024 * 1024,
                    )
                    self.assertEqual(
                        list((work_dir / "outbox").rglob("*.bin")),
                        [],
                    )
                    self.assertEqual(
                        list((work_dir / "outbox").rglob("*.json")),
                        [],
                    )
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_generation_artifact_requires_bound_host_sink_and_cleans_handoff(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                work_dir = root / "work"
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=work_dir,
                )
                generation.start()
                try:
                    result = await generation.invoke(
                        "test.generation.artifact.v1",
                        {},
                        context=InvocationContext("owner", "artifact-session", "web"),
                    )
                    self.assertTrue(result.is_error)
                    self.assertEqual(result.reason, "managed_artifact_sink_unavailable")
                    self.assertEqual(list((work_dir / "outbox").rglob("*.*")), [])
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "plugin_generation_already_started",
                    ):
                        generation.bind_managed_artifact_sink(
                            GeneratedFileManagedArtifactSink(
                                GeneratedFileService(
                                    base_dir=root / "outputs",
                                    store=MemoryStore(root / "late-store"),
                                    attachment_service=AttachmentInboxService(
                                        store=MemoryStore(root / "late-attachments-store"),
                                        base_dir=root / "late-attachments",
                                    ),
                                )
                            )
                        )
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_stop_drains_parent_artifact_materialization(self) -> None:
        async def scenario() -> None:
            class BlockingSink:
                def __init__(self, delegate: GeneratedFileManagedArtifactSink) -> None:
                    self.delegate = delegate
                    self.started = asyncio.Event()
                    self.release = asyncio.Event()

                async def materialize(self, draft, *, context, capability_id):
                    self.started.set()
                    await self.release.wait()
                    return await self.delegate.materialize(
                        draft,
                        context=context,
                        capability_id=capability_id,
                    )

            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                store = MemoryStore(root / "store")
                service = GeneratedFileService(
                    base_dir=root / "outputs",
                    store=store,
                    attachment_service=AttachmentInboxService(store=store),
                )
                sink = BlockingSink(GeneratedFileManagedArtifactSink(service))
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=root / "work",
                )
                generation.bind_managed_artifact_sink(sink)
                generation.start()
                invocation = asyncio.create_task(
                    generation.invoke(
                        "test.generation.artifact.v1",
                        {},
                        context=InvocationContext("owner", "drain-artifact", "web"),
                    )
                )
                await sink.started.wait()
                stop_task = asyncio.create_task(asyncio.to_thread(generation.stop))
                await asyncio.sleep(0.05)
                self.assertFalse(stop_task.done())

                sink.release.set()
                result = await invocation
                stopped = await stop_task

                self.assertFalse(result.is_error, result.reason)
                self.assertTrue(stopped["ok"])
                self.assertFalse(generation.running)

        asyncio.run(scenario())

    def test_generation_notification_uses_host_port_and_deduplicates(self) -> None:
        class RecordingPort:
            def __init__(self) -> None:
                self.intents: list[NotificationIntent] = []

            async def send(self, intent: NotificationIntent) -> NotificationResult:
                self.intents.append(intent)
                if intent.idempotency_key == "invalid-result":
                    return NotificationResult(  # type: ignore[arg-type]
                        ok=True,
                        status=object(),
                    )
                return NotificationResult(ok=True, status="delivered")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            port = RecordingPort()
            generation = PluginGenerationProcess(
                project_root=PROJECT_ROOT,
                site_dir=_write_capability_plugin_site(root),
                plugin_id="test.generation",
                work_dir=root / "work",
            )
            generation.bind_notification_port(port)
            generation.start()
            try:
                async def invoke_notifications() -> tuple[
                    CapabilityResult,
                    CapabilityResult,
                    CapabilityResult,
                ]:
                    arguments = {
                        "recipient_id": "user:123456",
                        "text": "该休息一下了",
                        "idempotency_key": "generation-notice-1",
                    }
                    context = InvocationContext("owner", "notification", "qq_text")
                    first = await generation.invoke(
                        "test.generation.notification.v1",
                        arguments,
                        context=context,
                    )
                    duplicate = await generation.invoke(
                        "test.generation.notification.v1",
                        arguments,
                        context=context,
                    )
                    invalid = await generation.invoke(
                        "test.generation.notification.v1",
                        {**arguments, "idempotency_key": "invalid-result"},
                        context=context,
                    )
                    return first, duplicate, invalid

                first, duplicate, invalid = asyncio.run(invoke_notifications())
                self.assertEqual(first.content["delivery_status"], "delivered")
                self.assertEqual(
                    duplicate.content["delivery_status"],
                    "already_delivered",
                )
                self.assertEqual(invalid.content["delivery_status"], "error")
                self.assertEqual(
                    invalid.content["delivery_reason"],
                    "invalid_notification_result",
                )
                self.assertEqual(len(port.intents), 2)
                self.assertEqual(port.intents[0].text, "该休息一下了")
            finally:
                generation.stop()
            self.assertFalse(
                any(
                    thread.name.startswith(
                        f"plugin-generation-callback:{generation.generation_id[:8]}"
                    )
                    for thread in threading.enumerate()
                )
            )

    def test_generation_notification_without_host_port_is_structured(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=root / "work",
                )
                generation.start()
                try:
                    result = await generation.invoke(
                        "test.generation.notification.v1",
                        {
                            "recipient_id": "group:123456",
                            "text": "hello",
                            "idempotency_key": "missing-port",
                        },
                        context=InvocationContext("owner", "notification", "web"),
                    )
                    self.assertFalse(result.is_error)
                    self.assertFalse(result.content["delivery_ok"])
                    self.assertEqual(
                        result.content["delivery_status"],
                        "not_configured",
                    )
                    self.assertEqual(
                        result.content["delivery_reason"],
                        "no_notification_port_bound",
                    )
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_background_job_notification_reaches_host_after_ready(self) -> None:
        async def scenario() -> None:
            class ObservedPort:
                def __init__(self) -> None:
                    self.intent: NotificationIntent | None = None
                    self.delivered = asyncio.Event()

                async def send(self, intent: NotificationIntent) -> NotificationResult:
                    self.intent = intent
                    self.delivered.set()
                    return NotificationResult(ok=True, status="delivered")

            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                port = ObservedPort()
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_notification_job_plugin_site(root),
                    plugin_id="test.generation.notification-job",
                    work_dir=root / "work",
                )
                generation.bind_notification_port(port)
                ready = generation.start()
                try:
                    await asyncio.wait_for(port.delivered.wait(), timeout=2.0)
                    self.assertTrue(ready["ok"])
                    self.assertEqual(ready["capabilities"], [])
                    self.assertIsNotNone(port.intent)
                    self.assertEqual(port.intent.text, "background-ready")
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_concurrent_notification_callbacks_are_correlated(self) -> None:
        async def scenario() -> None:
            class DelayedPort:
                async def send(self, intent: NotificationIntent) -> NotificationResult:
                    await asyncio.sleep(0.05 if intent.text == "slow" else 0.01)
                    return NotificationResult(
                        ok=True,
                        status="delivered",
                        reason=intent.text,
                    )

            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=root / "work",
                )
                generation.bind_notification_port(DelayedPort())
                generation.start()
                try:
                    async def invoke(text: str) -> CapabilityResult:
                        return await generation.invoke(
                            "test.generation.notification.v1",
                            {
                                "recipient_id": "user:123456",
                                "text": text,
                                "idempotency_key": f"correlated-{text}",
                            },
                            context=InvocationContext("owner", "concurrent", "web"),
                        )

                    slow_task = asyncio.create_task(invoke("slow"))
                    fast_task = asyncio.create_task(invoke("fast"))
                    slow, fast = await asyncio.gather(slow_task, fast_task)
                    self.assertEqual(slow.content["delivery_reason"], "slow")
                    self.assertEqual(fast.content["delivery_reason"], "fast")
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_notification_callback_cancellation_and_stop_drain(self) -> None:
        async def scenario() -> None:
            class BlockingPort:
                def __init__(self) -> None:
                    self.calls = 0
                    self.started = asyncio.Event()
                    self.cancelled = asyncio.Event()
                    self.second_started = asyncio.Event()
                    self.release = asyncio.Event()

                async def send(self, intent: NotificationIntent) -> NotificationResult:
                    del intent
                    self.calls += 1
                    if self.calls == 1:
                        self.started.set()
                        try:
                            await asyncio.Future()
                        except asyncio.CancelledError:
                            self.cancelled.set()
                            raise
                    self.second_started.set()
                    await self.release.wait()
                    return NotificationResult(ok=True, status="delivered")

            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                port = BlockingPort()
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=root / "work",
                )
                generation.bind_notification_port(port)
                generation.start()
                try:
                    pending = asyncio.create_task(
                        generation.invoke(
                            "test.generation.notification.v1",
                            {
                                "recipient_id": "user:123456",
                                "text": "cancel me",
                                "idempotency_key": "cancel-notification",
                            },
                            context=InvocationContext("owner", "cancel", "web"),
                        )
                    )
                    await port.started.wait()
                    pending.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await pending
                    await asyncio.wait_for(port.cancelled.wait(), timeout=2.0)
                    healthy = await generation.invoke(
                        "test.generation.echo.v1",
                        {"value": "after-notification-cancel"},
                        context=InvocationContext(session_id="cancel"),
                    )
                    self.assertEqual(
                        healthy.content["value"],
                        "after-notification-cancel",
                    )
                    draining = asyncio.create_task(
                        generation.invoke(
                            "test.generation.notification.v1",
                            {
                                "recipient_id": "user:123456",
                                "text": "drain me",
                                "idempotency_key": "drain-notification",
                            },
                            context=InvocationContext("owner", "drain", "web"),
                        )
                    )
                    await port.second_started.wait()
                    stop_task = asyncio.create_task(asyncio.to_thread(generation.stop))
                    await asyncio.sleep(0.05)
                    self.assertFalse(stop_task.done())

                    port.release.set()
                    delivered = await draining
                    stopped = await stop_task
                    self.assertEqual(delivered.content["delivery_status"], "delivered")
                    self.assertTrue(stopped["ok"])
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_concurrent_invocations_and_health_are_correlated(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=root / "work",
                )
                self.assertIsNone(generation.stop_timeout_seconds)
                generation.start()
                try:
                    context = InvocationContext(session_id="concurrent")
                    slow = asyncio.create_task(
                        generation.invoke(
                            "test.generation.echo.v1",
                            {"value": "slow", "delay_ms": 500},
                            context=context,
                        )
                    )
                    await asyncio.sleep(0.05)
                    fast = await generation.invoke(
                        "test.generation.echo.v1",
                        {"value": "fast", "delay_ms": 10},
                        context=context,
                    )
                    health = await asyncio.to_thread(generation.health)

                    self.assertEqual(fast.content["value"], "fast")
                    self.assertFalse(slow.done())
                    self.assertTrue(health["ok"])
                    self.assertEqual((await slow).content["value"], "slow")
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_cancellation_reaches_the_worker_and_generation_remains_usable(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=root / "work",
                )
                generation.start()
                try:
                    context = InvocationContext(session_id="cancel")
                    pending = asyncio.create_task(
                        generation.invoke(
                            "test.generation.echo.v1",
                            {"value": "cancel", "delay_ms": 5_000},
                            context=context,
                        )
                    )
                    await asyncio.sleep(0.1)
                    pending.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await pending
                    result = await asyncio.wait_for(
                        generation.invoke(
                            "test.generation.echo.v1",
                            {"value": "after-cancel"},
                            context=context,
                        ),
                        timeout=2.0,
                    )
                    self.assertEqual(result.content["value"], "after-cancel")
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_stop_drains_an_invocation_accepted_before_stop(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=root / "work",
                )
                generation.start()
                invocation = asyncio.create_task(
                    generation.invoke(
                        "test.generation.echo.v1",
                        {"value": "drain", "delay_ms": 400},
                        context=InvocationContext(session_id="drain"),
                    )
                )
                await asyncio.sleep(0.05)
                stop_task = asyncio.create_task(asyncio.to_thread(generation.stop))
                await asyncio.sleep(0.05)
                with self.assertRaises(PluginGenerationError) as rejected:
                    await generation.invoke(
                        "test.generation.echo.v1",
                        {"value": "late"},
                        context=InvocationContext(session_id="drain"),
                    )

                self.assertEqual(rejected.exception.reason, "plugin_generation_unavailable")
                self.assertEqual((await invocation).content["value"], "drain")
                stopped = await stop_task
                self.assertTrue(stopped["ok"])
                self.assertFalse(generation.running)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
