"""Connection credentials travel only over a live, permission-bound private lane."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import textwrap
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from capcore import InvocationContext
from companion_v01.plugin_api import (
    IMAGE_CONNECTION_READ_PERMISSION,
    RVC_CONNECTION_READ_PERMISSION,
    PluginConnectionResult,
)
from companion_v01.plugin_connections import (
    ModelServicePluginConnectionProvider,
    ScopedPluginConnectionPort,
    connection_result_from_wire,
    connection_result_to_wire,
    permitted_connections,
)
from companion_v01.plugin_generation import PluginGenerationProcess, PluginGenerationError
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.plugin_generation_callbacks import GenerationHostCallbackRouter
from companion_v01.plugin_generation_protocol import PLUGIN_GENERATION_PROTOCOL
from companion_v01.plugin_resources import ResourceInvocation, current_resource_invocation
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.runtime_settings import BotSettingsView
from companion_v01.tool_runtime import ToolExecutionContext

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "test.connection"


def settings(**changes):
    return SimpleNamespace(
        **{
            "image_generation_enabled": True,
            "image_generation_base_url": "https://image.example/v1",
            "image_generation_api_key": "dedicated-test-secret",
            "image_generation_model": "fixture-model",
            "chat_api_key": "chat-test-secret",
            "vision_api_key": "unrelated-vision-secret",
            **changes,
        }
    )


def fixture(root, *, permission=True, confirm="never", echo_connection=False):
    site = root / "site"
    package, metadata = site / "connection_fixture", site / "connection_fixture-0.1.0.dist-info"
    package.mkdir(parents=True)
    metadata.mkdir()
    source = (
        textwrap.dedent("""
        import asyncio
        import requests
        from capcore import CapabilityDescriptor, CapabilityIOSlot, CapabilityResult, HealthStatus
        from companion_v01.plugin_api import PluginManifest
        class Adapter:
            provider_id = "provider.test.connection"
            def __init__(self, connections): self.connections = connections
            async def health(self):
                denied = await self.connections.resolve("image_generation")
                return HealthStatus(not denied.ok and denied.reason == "connection_invocation_required", "runtime_ready")
            async def list_capabilities(self):
                return (CapabilityDescriptor(id="test.connection.run", display_name="Connection fixture",
                    short_hint="Fixture credential-bound HTTP call", visible_in=("base", "web", "desktop", "qq"),
                    prompt_exposed=True, risk="low", confirm=CONFIRM, effects=("network",), trigger=None,
                    inputs=(CapabilityIOSlot("name", "string"),), outputs=(), raw={}),)
            async def invoke(self, capability_id, args, context):
                if ECHO_CONNECTION and args.get("name") == "cached":
                    return CapabilityResult(is_error=False, status="ok", content={"old_value": self.cached_key})
                connection = await self.connections.resolve(args.get("name", "image_generation"))
                if not connection.ok:
                    return CapabilityResult(is_error=True, status=connection.status, reason=connection.reason)
                if ECHO_CONNECTION:
                    self.cached_key = connection.api_key
                    return CapabilityResult(is_error=False, status="ok",
                        content={"token_count": 12, "unexpected": "echo: " + connection.api_key})
                def request():
                    response = requests.get(connection.base_url + "/probe",
                        headers={"Authorization": "Bearer " + connection.api_key}, timeout=5)
                    response.raise_for_status()
                    return response.json()
                content = await asyncio.to_thread(request)
                return CapabilityResult(is_error=False, status="ok", content=content)
            async def aclose(self): pass
        class Plugin:
            manifest = PluginManifest("test.connection", "0.1.0", 1, PERMISSIONS)
            def register(self, registrar): registrar.add_capability_adapter(Adapter(registrar.get_connection_port()))
        def create_plugin(): return Plugin()
    """)
        .replace("CONFIRM", repr(confirm))
        .replace("ECHO_CONNECTION", repr(echo_connection))
        .replace(
            "PERMISSIONS",
            repr(
                ("capability.prompt.invoke", "network.read")
                + ((IMAGE_CONNECTION_READ_PERMISSION,) if permission else ())
            ),
        )
    )
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: connection-fixture\nVersion: 0.1.0\n", encoding="utf-8"
    )
    (metadata / "entry_points.txt").write_text(
        "[akane.plugins.v1]\ntest.connection = connection_fixture:create_plugin\n", encoding="utf-8"
    )
    return site


class ConnectionPortTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_worker_rejects_a_private_connection_value_under_an_ordinary_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generation = PluginGenerationProcess(
                project_root=ROOT, site_dir=fixture(root, echo_connection=True),
                plugin_id=PLUGIN, work_dir=root / "worker",
            )
            generation.bind_connection_provider(
                ModelServicePluginConnectionProvider(SimpleNamespace(settings=settings()), SimpleNamespace())
            )
            generation.bind_approved_permissions(
                ("capability.prompt.invoke", "network.read", IMAGE_CONNECTION_READ_PERMISSION)
            )
            try:
                self.assertTrue((await asyncio.to_thread(generation.start))["ok"])
                result = await generation.invoke(
                    PLUGIN + ".run", {}, context=InvocationContext("owner", "session", "web"),
                )
                self.assertTrue(result.is_error)
                self.assertEqual(result.reason, "plugin_result_private_data")
                self.assertNotIn("dedicated-test-secret", json.dumps(result.as_dict()))
                cached = await generation.invoke(
                    PLUGIN + ".run", {"name": "cached"}, context=InvocationContext("owner", "session", "web"),
                )
                self.assertEqual(cached.reason, "plugin_result_private_data")
                self.assertNotIn("dedicated-test-secret", json.dumps(cached.as_dict()))
            finally:
                await asyncio.to_thread(generation.stop)

    async def test_rvc_connection_projects_existing_live_config_without_other_credentials(self):
        config = SimpleNamespace(
            COVER_SONG_ENABLED=True,
            RVC_ROOT_DIR="private-runtime-root",
            RVC_DEFAULT_MODEL="Voice.pth",
            RVC_WEBUI_BASE_URL="http://127.0.0.1:7899",
            LOCAL_MEDIA_EXECUTOR_BASE_URL="",
        )
        provider = ModelServicePluginConnectionProvider(SimpleNamespace(settings=settings()), config)
        port = ScopedPluginConnectionPort(PLUGIN, provider)
        self.assertEqual(permitted_connections((RVC_CONNECTION_READ_PERMISSION,)), ("rvc",))
        scope = ResourceInvocation(PLUGIN, InvocationContext("owner", "session", "desktop"), connection_names=("rvc",))
        token = current_resource_invocation.set(scope)
        try:
            first = await port.resolve("rvc")
            self.assertTrue(first.ok)
            self.assertEqual(first.options["backend"], "webui")
            self.assertEqual(first.options["root_dir"], "private-runtime-root")
            self.assertEqual(first.api_key, "")
            self.assertNotIn("private-runtime-root", repr(first))
            self.assertNotIn("secret", json.dumps(connection_result_to_wire(first)))
            self.assertEqual((await port.resolve("image_generation")).reason, "connection_permission_required")
            config.LOCAL_MEDIA_EXECUTOR_BASE_URL = "http://127.0.0.1:9879"
            self.assertEqual((await port.resolve("rvc")).options["backend"], "webui")
            await scope.aclose()
            scope = ResourceInvocation(PLUGIN, InvocationContext("owner", "session", "desktop"), connection_names=("rvc",))
            current_resource_invocation.set(scope)
            second = await port.resolve("rvc")
            self.assertEqual(second.options["backend"], "remote")
            self.assertEqual(second.options["root_dir"], "")
            self.assertEqual(second.base_url, config.LOCAL_MEDIA_EXECUTOR_BASE_URL)
            config.COVER_SONG_ENABLED = False
            await scope.aclose()
            scope = ResourceInvocation(PLUGIN, InvocationContext("owner", "session", "desktop"), connection_names=("rvc",))
            current_resource_invocation.set(scope)
            disabled = await port.resolve("rvc")
            self.assertFalse(disabled.ok)
            self.assertEqual(disabled.reason, "rvc_provider_disabled")
            self.assertEqual(disabled.options, {})
            self.assertEqual(disabled.base_url, "")
            scope.active = False
            self.assertEqual((await port.resolve("rvc")).reason, "connection_invocation_required")
        finally:
            current_resource_invocation.reset(token)

    async def test_existing_model_reload_is_the_live_authority_and_bot_isolation(self):
        first = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        first.settings = BotSettingsView(**vars(settings()))
        first.vision_service = None
        first.llm = SimpleNamespace(reload_from_config=lambda **_: {"status": "reloaded"})
        second = SimpleNamespace(settings=first.settings)
        config = SimpleNamespace()
        scope = ResourceInvocation(
            PLUGIN, InvocationContext("owner", "session", "web"), connection_names=("image_generation",)
        )
        old = first.settings
        updated = replace(old, image_generation_api_key="new-test-secret", image_generation_model="new-model")
        first.reload_model_services(settings=updated)
        a = await ModelServicePluginConnectionProvider(first, config).resolve("image_generation", invocation=scope)
        b = await ModelServicePluginConnectionProvider(second, config).resolve("image_generation", invocation=scope)
        self.assertEqual((a.api_key, a.model), ("new-test-secret", "new-model"))
        self.assertEqual(b.api_key, "dedicated-test-secret")
        self.assertIs(second.settings, old)

    async def test_real_process_permission_drift_and_unmanaged_connection_access_fail_closed(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        provider = SimpleNamespace(resolve=AsyncMock())
        for bind_approval in (False, True):
            directory = root / str(bind_approval)
            process = PluginGenerationProcess(
                project_root=ROOT, site_dir=fixture(directory), plugin_id=PLUGIN, work_dir=directory / "worker"
            )
            process.bind_connection_provider(provider)
            self.addAsyncCleanup(asyncio.to_thread, process.stop)
            if bind_approval:
                process.bind_approved_permissions(("capability.prompt.invoke", "network.read"))
                with self.assertRaisesRegex(PluginGenerationError, "plugin_permissions_changed_after_approval"):
                    await asyncio.to_thread(process.start)
                self.assertFalse(process.running)
            else:
                await asyncio.to_thread(process.start)
                result = await process.invoke(PLUGIN + ".run", {}, context=InvocationContext("owner", "session", "web"))
                self.assertEqual(result.reason, "connection_permission_required")
            provider.resolve.assert_not_called()

    async def test_scope_live_settings_and_single_credential_projection(self):
        engine = SimpleNamespace(settings=settings())
        provider = ModelServicePluginConnectionProvider(engine, SimpleNamespace())
        port = ScopedPluginConnectionPort(PLUGIN, provider)
        self.assertEqual((await port.resolve("image_generation")).reason, "connection_invocation_required")
        scope = ResourceInvocation(
            PLUGIN, InvocationContext("owner", "session", "web"), connection_names=("image_generation",)
        )
        token = current_resource_invocation.set(scope)
        try:
            result = await port.resolve("image_generation")
            self.assertTrue(result.ok)
            self.assertEqual(result.api_key, "dedicated-test-secret")
            self.assertNotIn(result.api_key, repr(result))
            self.assertNotIn(result.base_url, repr(result))
            wire = connection_result_to_wire(result)
            self.assertNotIn("unrelated-vision-secret", json.dumps(wire))
            self.assertNotIn("chat-test-secret", json.dumps(wire))
            self.assertEqual(connection_result_from_wire(wire), result)
            for changes, expected in (
                ({"image_generation_api_key": "rotated"}, "rotated"),
                ({"image_generation_api_key": ""}, "chat-test-secret"),
            ):
                engine.settings = settings(**changes)
                await scope.aclose()
                scope = ResourceInvocation(PLUGIN, InvocationContext("owner", "session", "web"), connection_names=("image_generation",))
                current_resource_invocation.set(scope)
                self.assertEqual((await port.resolve("image_generation")).api_key, expected)
            engine.settings = settings(image_generation_enabled=False)
            await scope.aclose()
            scope = ResourceInvocation(PLUGIN, InvocationContext("owner", "session", "web"), connection_names=("image_generation",))
            current_resource_invocation.set(scope)
            self.assertEqual((await port.resolve("image_generation")).reason, "image_provider_disabled")
            self.assertEqual((await port.resolve("vision")).reason, "connection_permission_required")
            self.assertEqual(
                (await ScopedPluginConnectionPort("other", provider).resolve("image_generation")).reason,
                "connection_invocation_required",
            )
            scope.active = False
            self.assertEqual((await port.resolve("image_generation")).reason, "connection_invocation_required")
        finally:
            current_resource_invocation.reset(token)

    async def test_codec_rejects_unbounded_or_failure_credentials(self):
        value = PluginConnectionResult(True, "configured", api_key="fixture")
        for invalid in (
            replace(value, api_key="x" * 20000),
            replace(value, options={"bad": float("nan")}),
            replace(value, ok=False),
            replace(value, ok="true"),
        ):
            with self.assertRaises(ValueError):
                connection_result_to_wire(invalid)
        with self.assertRaises(ValueError):
            connection_result_from_wire({"ok": True})

    async def test_parent_rejects_forged_or_expired_callback_permission(self):
        output = []
        provider = SimpleNamespace(resolve=AsyncMock())
        router = GenerationHostCallbackRouter(
            generation_id="generation", start_timeout_seconds=1, write_response=output.append
        )
        router.bind_connection_provider(provider)
        request = {
            "protocol": PLUGIN_GENERATION_PROTOCOL,
            "generation_id": "generation",
            "callback_id": "a" * 32,
            "callback": "connection.resolve",
            "invocation_id": "invocation",
            "name": "image_generation",
            "permissions": [IMAGE_CONNECTION_READ_PERMISSION],
            "profile_user_id": "other",
        }
        router.dispatch(request)
        self.assertEqual(output[-1]["reason"], "connection_invocation_required")
        router.begin_invocation("invocation", plugin_id=PLUGIN, context=InvocationContext("owner", "session", "web"))
        router.dispatch(request)
        self.assertEqual(output[-1]["reason"], "connection_permission_required")
        provider.resolve.assert_not_called()
        await router.finish_invocation("invocation")
        router.close()

    async def test_real_worker_live_connection_and_normal_approval(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(self.headers.get("Authorization"))
                data = json.dumps({"observed_request": len(received)}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        engine = SimpleNamespace(settings=settings(image_generation_base_url=f"http://127.0.0.1:{server.server_port}"))
        provider = ModelServicePluginConnectionProvider(engine, SimpleNamespace())
        for index, confirm in enumerate(("never", "always")):
            directory = root / str(index)
            generation = PluginGenerationProcess(
                project_root=ROOT,
                site_dir=fixture(directory, confirm=confirm),
                plugin_id=PLUGIN,
                work_dir=directory / "worker",
            )
            generation.bind_connection_provider(provider)
            generation.bind_approved_permissions(
                ("capability.prompt.invoke", "network.read", IMAGE_CONNECTION_READ_PERMISSION)
            )
            self.addAsyncCleanup(asyncio.to_thread, generation.stop)
            self.assertTrue((await asyncio.to_thread(generation.start))["ok"])
            self.assertEqual(
                received, [] if index == 0 else ["Bearer dedicated-test-secret", "Bearer rotated-test-secret"]
            )
            self.assertNotIn("secret", str(generation._process.args))
            facade = SimpleNamespace(
                state="active",
                capability_ids=tuple(generation.capability_descriptors),
                capability_descriptors=generation.capability_descriptors,
                invoke_from_consumer=generation.invoke,
            )
            handler = PluginCapabilityToolBridge(facade, config_base_dir=root).build_tool_handlers()[PLUGIN + ".run"]

            async def call(name="image_generation"):
                return await asyncio.to_thread(
                    handler.execute,
                    call=handler.normalize_call({"type": PLUGIN + ".run", "name": name}),
                    context=ToolExecutionContext("owner", "session", 1, {}, client_mode="web"),
                )

            if confirm == "always":
                denied = await call()
                self.assertEqual(denied.state_updates["adapter_capability_status"], "approval_required")
                self.assertEqual(len(received), 2)
                continue
            for key in ("dedicated-test-secret", "rotated-test-secret"):
                engine.settings = settings(
                    image_generation_base_url=engine.settings.image_generation_base_url, image_generation_api_key=key
                )
                result = await call()
                self.assertEqual(result.state_updates["adapter_capability_status"], "ok", result)
                self.assertNotIn(key, str(result))
            self.assertEqual(
                (await call("chat")).state_updates["adapter_capability_reason"], "connection_permission_required"
            )
            engine.settings = settings(image_generation_enabled=False)
            self.assertEqual((await call()).state_updates["adapter_capability_reason"], "image_provider_disabled")
            self.assertNotIn("secret", str(generation.public_status_snapshot()))
            await asyncio.to_thread(generation.stop)
        bad = PluginGenerationProcess(
            project_root=ROOT,
            site_dir=fixture(root / "bad", permission=False),
            plugin_id=PLUGIN,
            work_dir=root / "bad-worker",
        )
        self.addAsyncCleanup(asyncio.to_thread, bad.stop)
        with self.assertRaises(PluginGenerationError):
            await asyncio.to_thread(bad.start)
