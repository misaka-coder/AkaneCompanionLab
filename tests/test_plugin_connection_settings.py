"""Declared connections: private persistence, invocation snapshots and installed HTTP use."""

import asyncio
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from akane_plugin import ConnectionSpec, Plugin, ToolContext, InvocationContext
from companion_v01.extension_management import ExtensionManagementService, PluginSelectionStore
from companion_v01.instance_profile import PluginSelection
from companion_v01.local_capability_config import load_capability_config, write_capability_config
from companion_v01.plugin_connection_settings import PluginConnectionSettings
from companion_v01.plugin_connections import ModelServicePluginConnectionProvider, ScopedPluginConnectionPort
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_generation_candidate import PluginGenerationCandidateBuilder
from companion_v01.plugin_generation_runtime import PluginGenerationRuntime
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_installation import ManagedPluginArtifactStore
from companion_v01.plugin_resources import ResourceInvocation, current_resource_invocation
from companion_v01.routes.plugins import build_plugins_router
from tests.test_plugin_host import FakeEntryPoint

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "example.http-query"
SPEC = ConnectionSpec("query_api", {"type": "object", "properties": {
    "endpoint": {"type": "string", "minLength": 1}, "credential": {"type": "string", "minLength": 1},
    "timeout_seconds": {"type": "number", "default": 10, "minimum": 0.1, "maximum": 60},
}, "required": ["endpoint", "credential"], "additionalProperties": False}, private_fields=("credential",))


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = PluginConnectionSettings(self.root)

    def save(self, **changes):
        return self.store.save("owner", PLUGIN, SPEC, {"expected_revision": 0,
            "values": {"endpoint": "http://127.0.0.1/api", "credential": "private-first"}, **changes})

    def test_schema_validation_projection_clear_rotation_and_scope_isolation(self):
        missing = self.store.read("owner", PLUGIN, SPEC)
        self.assertEqual((missing["status"], missing["revision"]), ("not_configured", 0))
        saved = self.save()
        self.assertTrue(saved["ok"], saved)
        self.assertNotIn("private-first", json.dumps(saved))
        self.assertEqual(saved["values"]["timeout_seconds"], 10)
        self.assertEqual(saved["configured_private_fields"], ["credential"])
        bad = self.save(expected_revision=1, values={"timeout_seconds": "private-invalid"})
        self.assertFalse(bad["ok"])
        self.assertNotIn("private-invalid", json.dumps(bad))
        self.assertEqual(self.store.read("owner", PLUGIN, SPEC)["revision"], 1)
        too_large = self.save(expected_revision=1, values={"credential": "x" * 20000})
        self.assertEqual(too_large["reason"], "connection_result_too_large")
        self.assertEqual(self.store.read("owner", PLUGIN, SPEC)["revision"], 1)
        self.assertEqual(self.save()["reason"], "connection_revision_conflict")
        self.assertEqual(self.store.resolve("another", PLUGIN, SPEC).reason, "connection_not_configured")
        self.assertEqual(self.store.resolve("owner", "example.other", SPEC).reason, "connection_not_configured")
        self.assertEqual(PluginConnectionSettings(self.root / "bot2").resolve("owner", PLUGIN, SPEC).reason,
                         "connection_not_configured")
        self.assertEqual(self.store.resolve("../owner", PLUGIN, SPEC).reason, "connection_profile_invalid")
        rotated = self.save(expected_revision=1, values={"credential": "private-next"})
        self.assertEqual(rotated["revision"], 2)
        self.assertEqual(self.store.resolve("owner", PLUGIN, SPEC).options["credential"], "private-next")
        # Clearing a required value is an explicit disabled draft, never fake readiness.
        cleared = self.save(expected_revision=2, values={}, clear_fields=["credential"], enabled=False)
        self.assertTrue(cleared["ok"], cleared)
        self.assertEqual(cleared["configured_private_fields"], [])
        self.assertEqual(self.store.resolve("owner", PLUGIN, SPEC).reason, "connection_disabled")
        self.assertFalse(self.save(expected_revision=3, values={}, enabled=True)["ok"])

    def test_legacy_writes_preserve_latest_private_section_and_version_upgrade_keeps_privacy(self):
        self.assertTrue(self.save()["ok"])
        stale = load_capability_config(base_dir=self.root, profile_user_id="owner")
        self.assertNotIn("pluginConnections", stale)
        self.assertTrue(self.save(expected_revision=1, values={"credential": "rotated-private"})["ok"])
        write_capability_config(base_dir=self.root, profile_user_id="owner", config=stale)
        self.assertEqual(self.store.resolve("owner", PLUGIN, SPEC).options["credential"], "rotated-private")
        upgraded = replace(SPEC, version=2, private_fields=())
        self.assertEqual(self.store.resolve("owner", PLUGIN, upgraded).reason, "connection_schema_version_changed")
        self.assertNotIn("rotated-private", json.dumps(self.store.read("owner", PLUGIN, upgraded)))
        result = self.store.save("owner", PLUGIN, upgraded, {"expected_revision": 2, "values": {}})
        self.assertNotIn("rotated-private", json.dumps(result))
        self.assertIn("credential", self.store.resolve("owner", PLUGIN, upgraded).private_option_fields)

    def test_corrupt_store_and_io_failure_are_structured_and_do_not_overwrite(self):
        self.assertTrue(self.save()["ok"])
        with patch("companion_v01.local_capability_config._write_capability_config", side_effect=OSError("private-path")):
            failed = self.save(expected_revision=1, values={"credential": "replacement"})
        self.assertEqual(failed["reason"], "connection_settings_io_failed")
        self.assertNotIn("private-path", json.dumps(failed))
        self.assertEqual(self.store.read("owner", PLUGIN, SPEC)["revision"], 1)
        path = self.root / "owner/capabilities/capabilities.yaml"
        path.write_text("[broken", encoding="utf-8")
        self.assertFalse(self.save()["ok"])
        self.assertEqual(path.read_text(encoding="utf-8"), "[broken")

    def test_public_declarations_validate_permission_schema_and_private_fields(self):
        plugin = Plugin(PLUGIN)
        plugin.connection("query_api", schema=SPEC.schema, private_fields=SPEC.private_fields)
        self.assertIn("connection.query_api.read", plugin.manifest.permissions)
        with self.assertRaises(ValueError):
            plugin.connection("query_api", schema=SPEC.schema)
        for invalid in ("", "../escape", "bad name"):
            with self.assertRaises(ValueError):
                ConnectionSpec(invalid, SPEC.schema)
        with self.assertRaises(ValueError):
            ConnectionSpec("valid", SPEC.schema, private_fields=(["bad"],))
        with self.assertRaisesRegex(ValueError, "private_default"):
            ConnectionSpec("valid", {"type": "object", "properties": {"secret": {"type": "string", "default": "bad"}}},
                           private_fields=("secret",))


class ScopedSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_parallel_reads_rotation_and_revocation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PluginConnectionSettings(directory)
            store.save("owner", PLUGIN, SPEC, {"expected_revision": 0,
                "values": {"endpoint": "http://localhost", "credential": "first-private"}})
            provider = ModelServicePluginConnectionProvider(None, None, connection_settings=store)
            port = ScopedPluginConnectionPort(PLUGIN, provider)
            def scope():
                return ResourceInvocation(PLUGIN, InvocationContext("owner", "session", "web"),
                                          connection_names=(SPEC.name,), connection_specs=(SPEC,))
            old = scope()
            token = current_resource_invocation.set(old)
            try:
                a, b = await asyncio.gather(port.resolve(SPEC.name), port.resolve(SPEC.name))
                a.options["credential"] = "plugin-mutated"
                self.assertEqual(b.options["credential"], "first-private")
                store.save("owner", PLUGIN, SPEC, {"expected_revision": 1, "values": {"credential": "second-private"}})
                self.assertEqual((await port.resolve(SPEC.name)).options["credential"], "first-private")
                old.revoke()
                self.assertFalse((await port.resolve(SPEC.name)).ok)
                await old.aclose()
                self.assertEqual(old.connection_snapshots, {})
                fresh = scope()
                current_resource_invocation.set(fresh)
                self.assertEqual((await port.resolve(SPEC.name)).options["credential"], "second-private")
                await fresh.aclose()
            finally:
                current_resource_invocation.reset(token)

    async def test_real_host_context_tools_events_and_private_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PluginConnectionSettings(directory)
            store.save("owner", PLUGIN, SPEC, {"expected_revision": 0,
                "values": {"endpoint": "http://localhost", "credential": "private-marker"}})
            plugin = Plugin(PLUGIN)
            plugin.connection(SPEC.name, schema=SPEC.schema, private_fields=SPEC.private_fields)
            cache = []
            @plugin.tool(output_schema={"type": "object"})
            async def leak(cached: bool, ctx: ToolContext):
                if not cached:
                    config = await ctx.connections.require(SPEC.name)
                    cache[:] = [config["credential"]]
                return {"ordinary_business_name": cache[0]}
            @plugin.on("example.query")
            async def event_handler(event, ctx):
                config = await ctx.connections.require(SPEC.name)
                return {"timeout": config["timeout_seconds"]}
            host = PluginHost((PluginSelection(PLUGIN, True),),
                entry_points_provider=lambda: (FakeEntryPoint(PLUGIN, lambda: plugin),),
                contribution_policy=TrustedStatefulPluginContributionPolicy())
            host.bind_connection_provider(ModelServicePluginConnectionProvider(None, None, connection_settings=store))
            try:
                self.assertEqual((await host.start())["status"], "active")
                for cached in (False, True):
                    result = await host.invoke(PLUGIN + ".leak", {"cached": cached},
                                               context=InvocationContext("owner", "session", "web"))
                    self.assertEqual(result.reason, "plugin_result_private_data")
                    self.assertNotIn("private-marker", json.dumps(result.as_dict()))
                from akane_plugin import Event
                subscription = plugin._subscriptions[0][0]
                result = await host.invoke_event(subscription.subscription_id, Event("event-one", "example.query", PLUGIN, {}),
                    context=InvocationContext("owner", "session", "web"), scope_id="scope", delivery_id="delivery")
                self.assertFalse(result.is_error, result)
                self.assertEqual(result.content, {"timeout": 10})
            finally:
                await host.stop()

    async def test_nested_private_values_and_numbers_are_protected_in_real_tool_results(self):
        with tempfile.TemporaryDirectory() as directory:
            spec = ConnectionSpec("account", {"type": "object", "properties": {
                "credentials": {"type": "object"}}, "required": ["credentials"]}, private_fields=("credentials",))
            store = PluginConnectionSettings(directory)
            saved = store.save("owner", PLUGIN, spec, {"expected_revision": 0, "values": {
                "credentials": {"private-inner-key": ["private-inner-value", 73489123]}}})
            self.assertTrue(saved["ok"], saved)
            plugin = Plugin(PLUGIN)
            plugin.connection(spec.name, schema=spec.schema, private_fields=spec.private_fields)
            @plugin.tool(output_schema={"type": "object"})
            async def echo(index: int, ctx: ToolContext):
                config = await ctx.connections.require("account")
                credentials = config["credentials"]
                return {"ordinary": [*credentials, *credentials["private-inner-key"]][index]}
            host = PluginHost((PluginSelection(PLUGIN, True),),
                entry_points_provider=lambda: (FakeEntryPoint(PLUGIN, lambda: plugin),),
                contribution_policy=TrustedStatefulPluginContributionPolicy())
            host.bind_connection_provider(ModelServicePluginConnectionProvider(None, None, connection_settings=store))
            try:
                self.assertEqual((await host.start())["status"], "active")
                for index in range(3):
                    result = await host.invoke(PLUGIN + ".echo", {"index": index}, context=InvocationContext("owner", "session", "web"))
                    self.assertEqual(result.reason, "plugin_result_private_data")
            finally:
                await host.stop()

    async def test_installed_public_http_plugin_admin_config_and_worker_rotation(self):
        received = []
        waiting, release = threading.Event(), threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(self.headers.get("Authorization"))
                number = len(received)
                if "q=slow" in self.path:
                    waiting.set()
                    release.wait(20)
                data = json.dumps({"request_number": number, "items": list(range(150))}).encode()
                if "q=leak" in self.path:
                    data = json.dumps({"ordinary": self.headers.get("Authorization")}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = ManagedPluginArtifactStore(root / "artifacts", instance_id="connection-test", project_root=ROOT)
            selections = PluginSelectionStore(root / "selection.json", defaults=(), instance_id="connection-test")
            runtime = PluginGenerationRuntime((), candidate_builder=PluginGenerationCandidateBuilder(
                source_resolver=artifacts, project_root=ROOT, work_root=root / "workers"))
            store = PluginConnectionSettings(root / "profiles")
            runtime.bind_connection_provider(ModelServicePluginConnectionProvider(None, None, connection_settings=store))
            service = ExtensionManagementService(plugin_runtime=runtime, selection_store=selections,
                                                 artifact_store=artifacts, connection_settings=store)
            app = FastAPI()
            app.include_router(build_plugins_router(extension_management_service=service))
            pending = None
            try:
                await runtime.start()
                staged = await service.stage_source(source_path=str(ROOT / "examples/plugins/akane_sdk_http_query"))
                self.assertTrue(staged["ok"], staged)
                installed = await service.install_stage(stage_id=staged["stage_id"], approved_permissions=staged["permissions"])
                self.assertTrue(installed["ok"], installed)
                context = InvocationContext("owner", "session", "web")
                call = lambda text: runtime.invoke(PLUGIN + ".query", {"text": text}, context=context)
                self.assertEqual((await call("before")).reason, "connection_not_configured")
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 4567)), base_url="http://test") as client:
                    url = f"/admin/plugins/{PLUGIN}/connections/query_api?profile_user_id=owner"
                    saved = await client.put(url, json={"expected_revision": 0, "values": {
                        "endpoint": f"http://127.0.0.1:{server.server_port}/query", "credential": "installed-private-first"}})
                    self.assertEqual(saved.status_code, 200, saved.text)
                    self.assertNotIn("installed-private-first", saved.text)
                    pending = asyncio.create_task(call("slow"))
                    self.assertTrue(await asyncio.to_thread(waiting.wait, 10))
                    rotated = await client.put(url, json={"expected_revision": 1, "values": {"credential": "installed-private-next"}})
                    self.assertEqual(rotated.status_code, 200, rotated.text)
                    fresh = await call("fresh")
                    self.assertFalse(fresh.is_error, fresh)
                    self.assertEqual(fresh.value["items"], list(range(150)))
                    release.set()
                    self.assertFalse((await pending).is_error)
                    self.assertEqual(received, ["Bearer installed-private-first", "Bearer installed-private-next"])
                    public = await client.get("/plugins/catalog")
                    self.assertIn("query_api", public.text)
                    self.assertNotIn("installed-private", public.text)
                    self.assertNotIn("installed-private", (await client.get(url)).text)
                    leaked = await call("leak")
                    self.assertEqual(leaked.reason, "plugin_result_private_data")
                    self.assertNotIn("installed-private", json.dumps(leaked.as_dict()))
                    self.assertEqual((await client.put(url, json={"expected_revision": 1})).status_code, 409)
                    disabled = await client.put(url, json={"expected_revision": 2, "enabled": False})
                    self.assertEqual(disabled.status_code, 200, disabled.text)
                    self.assertEqual((await call("disabled")).reason, "connection_disabled")
                    self.assertEqual(len(received), 3)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("192.0.2.1", 4567)), base_url="http://test") as remote:
                    self.assertEqual((await remote.get(url)).status_code, 403)
                    self.assertEqual((await remote.put(url, json={"expected_revision": 3})).status_code, 403)
            finally:
                release.set()
                if pending is not None:
                    await asyncio.gather(pending, return_exceptions=True)
                await runtime.stop()
                await asyncio.to_thread(server.shutdown)
                server.server_close()
                thread.join(2)
