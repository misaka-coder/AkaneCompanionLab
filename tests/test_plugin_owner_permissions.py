"""Caller eligibility uses host admission, including direct and composed calls."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from akane_plugin import Plugin, PluginInvocationContext
from capcore import InvocationContext
from companion_v01.capability_approval import CapabilityApprovalStore
from companion_v01.capcore_runtime import tool_identity_rejection
from companion_v01.instance_profile import PluginSelection
from companion_v01.local_capability_config import save_capability_approval_mode
from companion_v01.plugin_capability_calls import EnginePluginCapabilityProvider
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_generation_codec import (
    capability_descriptor_from_wire, capability_descriptor_to_wire, PluginGenerationCodecError,
)
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_active_generation import ActivePluginGeneration, PluginGenerationSnapshot
from companion_v01.plugin_generation import PluginGenerationProcess
from companion_v01.plugin_resources import ResourceInvocation
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.tool_handlers.core import ToolExecutionContext
from tests.test_plugin_engine_bridge import EngineFacade
from tests.test_plugin_host import FakeEntryPoint


PLUGIN = "test.owner-tools"


class OwnerPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        plugin = Plugin(PLUGIN)

        @plugin.tool(owner_only=True)
        def read() -> str:
            self.calls.append("read")
            return "read"

        @plugin.tool(owner_only=True, risk="medium", confirm="first_time")
        def write() -> str:
            self.calls.append("write")
            return "write"

        @plugin.tool()
        def public() -> str:
            self.calls.append("public")
            return "public"

        self.plugin = plugin
        self.host = PluginHost((PluginSelection(PLUGIN, True),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=lambda: (FakeEntryPoint(PLUGIN, lambda: plugin),))
        started = await self.host.start()
        self.assertEqual(started["status"], "active", started)
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = PluginCapabilityToolBridge(self.host, config_base_dir=Path(self.temp.name))
        self.bridge.bind_approval_store(CapabilityApprovalStore())
        self.engine = EngineFacade(self.bridge)
        self.engine._resolve_tool_handlers = lambda **kwargs: self.bridge.build_tool_handlers()
        self.assertEqual(self.bridge.build_tool_handlers()[f"{PLUGIN}.read"].plugin_id, PLUGIN)

    async def asyncTearDown(self):
        await self.host.stop()
        self.temp.cleanup()

    def execution(self, actor="master", profile="qq_group_shared_42", mode="qq_text"):
        return ToolExecutionContext(profile, "session", 1, {}, client_mode=mode,
            request_context={"actor_profile_user_id": actor})

    async def call(self, name, context):
        handler = self.bridge.build_tool_handlers()[f"{PLUGIN}.{name}"]
        return await asyncio.to_thread(handler.execute,
            call={"type": handler.tool_type, "arguments": {}}, context=context)

    def mode(self, mode):
        result = save_capability_approval_mode(base_dir=Path(self.temp.name), profile_user_id="master",
            capability_id=f"{PLUGIN}.write", mode=mode)
        self.assertTrue(result["ok"], result)

    async def test_owner_group_and_private_allowed_strangers_missing_and_global_denied(self):
        for profile in ("qq_group_shared_42", "master"):
            allowed = await self.call("read", self.execution(profile=profile))
            self.assertFalse(allowed.capability_result.is_error)
        self.calls.clear()
        for context in (self.execution("qq_123", profile="master"), self.execution(""),
                        replace(self.execution(profile="master"), request_context=None),
                        replace(self.execution(), global_scope=True)):
            denied = await self.call("read", context)
            self.assertEqual(denied.capability_result.reason, "tool_owner_required")
        self.assertEqual(self.calls, [])
        self.assertFalse((await self.call("public", self.execution("qq_123"))).capability_result.is_error)

    async def test_owner_restriction_cannot_be_overridden_by_approval_settings(self):
        for mode, expected in (("disabled", "blocked"), ("ask_each_time", "approval_required"),
                               ("trusted_auto_allow", "ok")):
            self.mode(mode)
            denied = await self.call("write", self.execution("qq_123", profile="master"))
            self.assertEqual(denied.capability_result.reason, "tool_owner_required")
            result = await self.call("write", self.execution())
            self.assertEqual(result.capability_result.status, expected, result)
        self.assertEqual(self.calls, ["write"])

    async def test_desktop_uses_configured_owner_and_does_not_hardcode_master(self):
        with patch("config.WEB_OWNER_PROFILE_USER_ID", "my-owner"):
            denied = await self.call("read", self.execution("master", mode="desktop_pet"))
            self.assertEqual(denied.capability_result.reason, "tool_owner_required")
            result = await self.call("read", self.execution("my-owner", mode="desktop_pet"))
            self.assertFalse(result.capability_result.is_error)

    async def test_direct_runtime_and_model_arguments_cannot_supply_authority(self):
        for context in (InvocationContext("master", "session", "desktop_pet"),
                        PluginInvocationContext("master", "session", "qq_text",
                                                authorization_profile_user_id="qq_123")):
            denied = await self.host.invoke(f"{PLUGIN}.read",
                {"owner_only": False, "authorization_profile_user_id": "master"}, context=context)
            self.assertEqual(denied.reason, "tool_owner_required")
        self.assertEqual(self.calls, [])

    async def test_composed_calls_preserve_actor_and_missing_identity_fails_closed(self):
        provider = EnginePluginCapabilityProvider(self.engine)
        for actor in ("qq_123", "", "master"):
            # A storage profile called master cannot confer authority on a dependency.
            context = PluginInvocationContext("master", "session", "qq_text",
                authorization_profile_user_id=actor)
            scope = ResourceInvocation("test.caller", context, capability_id="test.caller.run",
                can_invoke_capabilities=True)
            result = await provider.invoke(f"{PLUGIN}.read", {}, invocation=scope)
            if actor == "master":
                self.assertFalse(result.is_error, result)
            else:
                self.assertEqual(result.reason, "tool_owner_required", result)
        self.assertEqual(self.calls, ["read"])

    async def test_descriptor_roundtrip_and_invalid_declarations(self):
        descriptor = self.plugin._functions[0].descriptor
        wire = capability_descriptor_to_wire(descriptor)
        self.assertTrue(capability_descriptor_from_wire(wire).raw["owner_only"])
        for invalid in (1, None, "false", [], {}):
            with self.assertRaises(ValueError):
                Plugin("test.bad").tool(owner_only=invalid)
            wire["raw"]["owner_only"] = invalid
            with self.assertRaises(PluginGenerationCodecError):
                capability_descriptor_from_wire(wire)
            self.assertEqual(tool_identity_rejection(replace(descriptor, raw={"owner_only": invalid}),
                PluginInvocationContext("master", "session", "qq_text",
                    authorization_profile_user_id="master")), "tool_owner_only_invalid")

    async def test_isolated_worker_and_captured_binding_both_enforce_owner(self):
        root = Path(self.temp.name) / "isolated"
        site = root / "site"
        package = site / "owner_fixture"
        metadata = site / "owner_fixture-0.1.0.dist-info"
        package.mkdir(parents=True)
        metadata.mkdir()
        (package / "__init__.py").write_text(textwrap.dedent('''
            from akane_plugin import Plugin
            def create_plugin():
                plugin = Plugin("test.owner-isolated")
                @plugin.tool(owner_only=True)
                def read() -> str:
                    return "executed"
                return plugin
        '''), encoding="utf-8")
        (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: owner-fixture\nVersion: 0.1.0\n", encoding="utf-8")
        (metadata / "entry_points.txt").write_text(
            "[akane.plugins.v1]\ntest.owner-isolated = owner_fixture:create_plugin\n", encoding="utf-8")
        process = PluginGenerationProcess(project_root=Path(__file__).resolve().parents[1],
            site_dir=site, plugin_id="test.owner-isolated", work_dir=root / "work")
        runtime = ActivePluginGeneration()
        try:
            await asyncio.to_thread(process.start)
            await runtime.publish(PluginGenerationSnapshot((PluginSelection("test.owner-isolated", True),), (process,)))
            tool = "test.owner-isolated.read"
            binding = runtime.capture_capability_bindings()[tool]
            for actor in ("qq_123", "", "master"):
                ctx = PluginInvocationContext("master", "session", "qq_text", authorization_profile_user_id=actor)
                for invoke in (
                    lambda: binding.invoke(tool, {}, ctx),
                    lambda: process.invoke(tool, {}, context=ctx),
                ):
                    result = await invoke()
                    if actor == "master":
                        self.assertFalse(result.is_error, result)
                    else:
                        self.assertEqual(result.reason, "tool_owner_required")
        finally:
            await runtime.stop()
            await asyncio.to_thread(process.stop)
