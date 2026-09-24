"""Public SDK declarations exercised through the real host and program port."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Literal
from unittest.mock import AsyncMock, Mock

from capcore import CapabilityResult, InvocationContext, SchemaDefinitionError

from akane_plugin import Plugin, PluginManifest, ServiceDependency, ToolCallError, ToolContext, Tools
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_capability_calls import EnginePluginCapabilityProvider, ScopedPluginCapabilityPort
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_resources import ResourceInvocation, current_resource_invocation
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.tool_runtime import ToolExecutionContext
from tests.test_plugin_engine_bridge import EngineFacade
from tests.test_plugin_host import FakeEntryPoint
from tests.test_plugin_resources import services


class PublicSdkTests(unittest.IsolatedAsyncioTestCase):
    async def test_effects_permissions_and_service_requirements_remain_distinct(self):
        plugin = Plugin(
            "example.contract-boundaries",
            permissions=("network.read",),
            requires_services=(ServiceDependency("statistics", version=1),),
        )

        @plugin.tool(effects=("network",))
        def fetch(label: str) -> str:
            return label

        self.assertEqual(
            plugin.manifest.permissions,
            ("network.read", "capability.prompt.invoke"),
        )
        self.assertEqual(
            plugin.manifest.requires_services,
            (ServiceDependency("statistics", version=1),),
        )

        class Registrar:
            def add_capability_adapter(self, adapter):
                self.adapter = adapter

        registrar = Registrar()
        plugin.register(registrar)
        descriptor, = await registrar.adapter.list_capabilities()
        self.assertEqual(descriptor.effects, ("network",))
        self.assertNotIn("network", plugin.manifest.permissions)
        await registrar.adapter.aclose()

    async def test_event_only_plugin_has_no_implicit_model_tool_permission(self):
        plugin = Plugin("example.observer")

        @plugin.on("example.updated")
        async def observe(event, ctx):
            return event.data

        self.assertEqual(plugin.manifest.permissions, ("event.subscribe",))
        host, engine = await self.start(plugin)
        self.assertEqual(host.capability_ids, ())
        self.assertEqual(engine._resolve_tool_handlers(), {})

    def test_tool_permission_is_added_only_after_a_valid_declaration(self):
        plugin = Plugin("example.math", permissions=("event.emit",))
        self.assertEqual(plugin.manifest.permissions, ("event.emit",))
        def untyped(value):
            return value
        with self.assertRaises(TypeError):
            plugin.tool(untyped)
        self.assertEqual(plugin.manifest.permissions, ("event.emit",))

        @plugin.tool
        def add(a: int) -> int:
            return a + 1

        self.assertEqual(plugin.manifest.permissions, ("event.emit", "capability.prompt.invoke"))

    async def start(self, plugin):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        host = PluginHost(
            (PluginSelection(plugin.manifest.plugin_id, True),),
            entry_points_provider=lambda: (FakeEntryPoint(plugin.manifest.plugin_id, lambda: plugin),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
        )
        self.addAsyncCleanup(host.stop)
        status = await host.start()
        self.assertEqual(host.state, "active", status)
        # Any attempt to create a model preview material fails the test.
        sink = Mock()
        sink.materialize = AsyncMock(side_effect=AssertionError("program result must not materialize a model preview"))
        bridge = PluginCapabilityToolBridge(host, config_base_dir=Path(temporary.name), result_preview_chars=128)
        bridge.bind_result_sink(sink)
        engine = EngineFacade(bridge)
        engine.store, _, _ = services(Path(temporary.name))
        engine.capability_config_base_dir = Path(temporary.name)
        engine.llm = Mock(side_effect=AssertionError("program consumer must not call a model"))
        engine.result_sink = sink
        return host, engine

    async def test_typed_functions_defaults_full_schema_and_output_validation(self):
        plugin = Plugin("example.math")
        calls = []

        @plugin.tool
        def add(a: int, b: int = 1) -> int:
            """Add integer values."""
            calls.append((a, b))
            return a + b

        schema = {"$defs": {"body": {
            "type": "object", "properties": {"ctx": {"const": "business"}, "type": {"type": "string"}},
            "required": ["ctx", "type"], "additionalProperties": False,
        }}, "$ref": "#/$defs/body", "description": "完整说明\n" * 400}

        @plugin.tool(input_schema=schema, output_schema={"type": "object"})
        async def echo(arguments, ctx: ToolContext):
            self.assertEqual(ctx.invocation.profile_user_id, "owner")
            return arguments

        @plugin.tool
        def broken() -> int:
            calls.append("broken")
            return "not an integer"

        host, engine = await self.start(plugin)
        handler = engine._resolve_tool_handlers()["example.math.add"]
        self.assertNotIn("ctx", handler.tool_spec().input_schema["properties"])
        self.assertEqual(handler.tool_spec().input_schema["required"], ["a"])
        good = await host.invoke("example.math.add", {"a": 2}, context=InvocationContext("owner", "session"))
        self.assertEqual(good.value, 3)
        bad = await host.invoke("example.math.add", {"a": "2"}, context=InvocationContext("owner", "session"))
        self.assertEqual(bad.status, "validation_error")
        self.assertEqual(calls, [(2, 1)])
        echoed = await host.invoke("example.math.echo", {"ctx": "business", "type": "invoice"},
                                   context=InvocationContext("owner", "session"))
        self.assertEqual(echoed.value, {"ctx": "business", "type": "invoice"})
        self.assertEqual(host.capability_descriptors["example.math.echo"].input_schema, schema)
        failed = await host.invoke("example.math.broken", {}, context=InvocationContext("owner", "session"))
        self.assertEqual(failed.status, "result_validation_error")
        self.assertFalse(failed.content["retryable"])
        self.assertEqual(calls, [(2, 1), "broken"])

    async def test_program_values_do_not_become_previews_or_artifact_lists(self):
        plugin = Plugin("example.values")
        calls = []

        @plugin.tool
        def values(kind: Literal["null", "false", "zero", "large"]) -> Any:
            """Return a JSON value for program composition."""
            calls.append(kind)
            return {"null": None, "false": False, "zero": 0,
                    "large": [{"name": "长数据" * 200, "index": index} for index in range(200)]}[kind]

        host, engine = await self.start(plugin)
        provider = EnginePluginCapabilityProvider(engine)
        scope = ResourceInvocation("example.caller", InvocationContext("owner", "session", "web"),
                                   capability_id="example.caller.run", can_invoke_capabilities=True)
        token = current_resource_invocation.set(scope)
        try:
            tools = Tools(ScopedPluginCapabilityPort("example.caller", provider))
            for kind, expected in (("null", None), ("false", False), ("zero", 0)):
                result = await tools.call("example.values.values", {"kind": kind})
                self.assertIs(result, expected)
            large = await tools.call("example.values.values", {"kind": "large"})
            self.assertEqual(len(large), 200)
            self.assertEqual(large[-1]["name"], "长数据" * 200)
            self.assertGreater(len(json.dumps(large, ensure_ascii=False)), 64 * 1024)
            with self.assertRaises(ToolCallError) as caught:
                await tools.call("example.values.values", {"kind": "wrong"})
            self.assertEqual(caught.exception.result.status, "validation_error")
            self.assertTrue(caught.exception.result.content["errors"])
            self.assertEqual(calls, ["null", "false", "zero", "large"])
            self.assertEqual((await tools.call_result("example.missing", {})).status, "unavailable")
        finally:
            current_resource_invocation.reset(token)
            await scope.aclose()
        engine.llm.assert_not_called()
        engine.result_sink.materialize.assert_not_called()
        handler = engine._resolve_tool_handlers()["example.values.values"]
        execution = await asyncio.to_thread(handler.execute,
            call={"type": "example.values.values", "arguments": {"kind": "large"}},
            context=ToolExecutionContext("owner", "session", 1, {}, result_consumer="program"))
        self.assertEqual(execution.capability_result.value, large)
        self.assertEqual(execution.stream_events, [])
        self.assertEqual(execution.followup_context, "")
        self.assertIsNone(execution.followup_envelope)
        self.assertFalse(execution.followup.requires_model)
        self.assertEqual(execution.followup.reason, "program_consumer")

    async def test_qq_command_declaration_reaches_the_real_command_broker(self):
        plugin = Plugin("example.ping")
        received = []

        @plugin.qq_command("/ping")
        async def ping(request):
            received.append(request)
            return {"handled": True, "reply_text": f"pong {request.args}".strip()}

        self.assertEqual(plugin.manifest.permissions, ("qq.command.register",))
        host, engine = await self.start(plugin)
        # A command-only plugin contributes no model tool.
        self.assertEqual(host.capability_ids, ())
        self.assertEqual(engine._resolve_tool_handlers(), {})
        broker = host.build_qq_command_broker()
        result = await broker.dispatch(command="/ping", args="now", qq_number=300, group_id=200, is_group=True)
        self.assertTrue(result.handled, result)
        self.assertEqual(result.reply_text, "pong now")
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].command, "/ping")
        self.assertEqual(received[0].args, "now")
        self.assertEqual(received[0].qq_number, 300)
        self.assertTrue(received[0].is_group)
        unmatched = await broker.dispatch(command="/other", args="", qq_number=300, group_id=0, is_group=False)
        self.assertFalse(unmatched.handled)

    def test_qq_command_declaration_rejects_invalid_tokens(self):
        plugin = Plugin("example.commands")
        with self.assertRaisesRegex(ValueError, "qq_command_invalid"):
            plugin.qq_command("ping")(lambda request: None)
        with self.assertRaisesRegex(ValueError, "qq_command_invalid"):
            plugin.qq_command("/bad token")(lambda request: None)
        # A rejected declaration must not grant the permission.
        self.assertEqual(plugin.manifest.permissions, ())
        plugin.qq_command("/ok")(lambda request: None)
        with self.assertRaisesRegex(ValueError, "qq_command_duplicate"):
            plugin.qq_command("/OK")(lambda request: None)
        self.assertEqual(plugin.manifest.permissions, ("qq.command.register",))

    async def test_skill_declaration_registers_the_real_package_root(self):
        plugin = Plugin("example.skill")
        skill_root = Path(tempfile.mkdtemp()) / "sample-companion"
        skill_root.mkdir()
        (skill_root / "SKILL.md").write_text(
            "---\nname: sample-companion\ndescription: A declared plugin skill.\n---\nBody.\n", encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "skill_name_invalid"):
            plugin.skill("")
        declared = plugin.skill("sample-companion", root=skill_root)
        self.assertEqual(declared, skill_root)
        self.assertEqual(plugin.manifest.permissions, ("skill.contribute",))
        with self.assertRaisesRegex(ValueError, "skill_declaration_duplicate"):
            plugin.skill("sample-companion", root=skill_root)
        captured = {}

        class _Registrar:
            def add_skill(self, root):
                captured["root"] = Path(root)

        plugin.register(_Registrar())
        self.assertEqual(captured["root"], skill_root)

    async def test_background_declaration_runs_through_the_host_job_protocol(self):
        plugin = Plugin("example.sensor")
        ticks = []

        @plugin.background("poll")
        async def poll(ctx):
            while not ctx.shutdown_requested:
                ticks.append("tick")
                if not await ctx.sleep(0.01):
                    break
            ticks.append("stopped")

        # A supervised service may also request a normal turn through the host.
        self.assertEqual(plugin.manifest.permissions, ("job.run", "agent.turn.request"))
        with self.assertRaisesRegex(ValueError, "background_service_id_invalid"):
            plugin.background("Bad Id")(lambda ctx: None)
        captured = {}

        class _Registrar:
            def add_background_service(self, service_id, service):
                captured["service_id"] = service_id
                captured["service"] = service

            def add_capability_adapter(self, adapter):
                captured["adapter"] = adapter

            def get_events_port(self):
                return None

        plugin.register(_Registrar())
        self.assertEqual(captured["service_id"], "poll")
        self.assertNotIn("adapter", captured, "a service-only plugin must not fake a model tool")

        class _Controller:
            shutdown_requested = False

            async def wait_for_shutdown(self, timeout=None):
                await asyncio.sleep(0.02 if timeout is None else timeout)
                return self.shutdown_requested

        controller = _Controller()
        service = captured["service"]
        task = asyncio.create_task(service.start(controller))
        await asyncio.sleep(0.05)
        controller.shutdown_requested = True
        await asyncio.wait_for(task, 1)
        self.assertIn("tick", ticks)
        self.assertEqual(ticks[-1], "stopped")

    def test_public_import_identity_and_author_errors(self):
        from companion_v01.plugin_api import PluginManifest as LegacyManifest
        from companion_v01.plugin_subprocess import PluginProcessRunner as LegacyRunner
        from akane_plugin import PluginProcessRunner
        self.assertIs(PluginManifest, LegacyManifest)
        self.assertIs(PluginProcessRunner, LegacyRunner)
        plugin = Plugin("example.declarations")
        with self.assertRaisesRegex(TypeError, "annotation_unsupported"):
            plugin.tool(lambda untyped: untyped, name="untyped")
        with self.assertRaisesRegex(TypeError, "one_argument_object"):
            plugin.tool(lambda a, b: 1, name="bad_signature", input_schema={"type": "object"})
        with self.assertRaises(SchemaDefinitionError):
            plugin.tool(lambda args: args, name="bad_schema", input_schema={"type": "object", "maxProperties": "many"})


if __name__ == "__main__":
    unittest.main()
