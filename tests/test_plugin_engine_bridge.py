from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

import config
from capcore import CapabilityDescriptor, CapabilityIOSlot, CapabilityResult, HealthStatus, InvocationContext

from companion_v01.capability_registry import CapabilityRegistry
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.engine_services.tool_rounds import resolve_capability_selection, resolve_tool_handlers
from companion_v01.instance_profile import PluginSelection
from companion_v01.native_tool_schema import NATIVE_TOOL_CAPABILITY_ID_FIELD
from companion_v01.plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    CAPABILITY_PROMPT_INVOKE_PERMISSION,
    NETWORK_READ_PERMISSION,
    PluginManifest,
)
from companion_v01.plugin_contribution_policy import TrustedReadNetworkContributionPolicy
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.tool_orchestration_engine import build_native_tool_schemas
from companion_v01.tool_runtime import ToolExecutionContext


PLUGIN_ID = "akane.test.read"
CAPABILITY_ID = f"{PLUGIN_ID}.lookup.v1"


class FakeDistribution:
    version = "0.1.0"
    metadata = {"Name": "akane-test-read-plugin"}

    def read_text(self, filename: str) -> str | None:
        del filename
        return None


class FakeEntryPoint:
    name = PLUGIN_ID
    dist = FakeDistribution()

    def __init__(self, factory: Any) -> None:
        self._factory = factory

    def load(self) -> Any:
        return self._factory


class RecordingAdapter:
    provider_id = "provider.akane.test.read"

    def __init__(self) -> None:
        self.contexts: list[InvocationContext] = []
        self.close_count = 0
        self.result: CapabilityResult | None = None

    async def health(self) -> HealthStatus:
        return HealthStatus(ok=True, status="ready")

    async def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        return (
            CapabilityDescriptor(
                id=CAPABILITY_ID,
                display_name="Public Lookup",
                short_hint="Read one public value from an installed plugin.",
                visible_in=("base", "web", "desktop", "qq"),
                prompt_exposed=True,
                risk="low",
                confirm="never",
                effects=("network",),
                trigger=None,
                inputs=(
                    CapabilityIOSlot(
                        name="query",
                        kind="string",
                        required=True,
                        max_bytes=80,
                        raw={"description": "Public lookup query", "minLength": 1, "maxLength": 80},
                    ),
                ),
                outputs=(),
                raw={"contract": "test.read.v1"},
            ),
        )

    async def invoke(
        self,
        capability_id: str,
        args: dict[str, Any],
        ctx: InvocationContext,
    ) -> CapabilityResult:
        self.contexts.append(ctx)
        return self.result or CapabilityResult(
            is_error=False,
            status="ok",
            content={"capability": capability_id, "query": args["query"], "source": "public"},
        )

    async def aclose(self) -> None:
        self.close_count += 1


class ReadPlugin:
    manifest = PluginManifest(
        plugin_id=PLUGIN_ID,
        plugin_version="0.1.0",
        plugin_api_version=AKANE_PLUGIN_API_VERSION,
        permissions=(CAPABILITY_PROMPT_INVOKE_PERMISSION, NETWORK_READ_PERMISSION),
    )

    def __init__(self, adapter: RecordingAdapter) -> None:
        self._adapter = adapter

    def register(self, registrar: Any) -> None:
        registrar.add_capability_adapter(self._adapter)


class EngineFacade:
    def __init__(self, source: PluginCapabilityToolBridge) -> None:
        self.tool_handlers: dict[str, Any] = {}
        self.plugin_capability_source = source
        self.capability_registry = CapabilityRegistry()

    def _resolve_tool_handlers(self, **kwargs: Any) -> dict[str, Any]:
        return resolve_tool_handlers(self, **kwargs)

    def _resolve_capability_selection(self, **kwargs: Any) -> Any:
        return resolve_capability_selection(self, **kwargs)


class PluginEngineBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.adapter = RecordingAdapter()
        plugin = ReadPlugin(self.adapter)

        def factory() -> ReadPlugin:
            return plugin

        self.host = PluginHost(
            (PluginSelection(PLUGIN_ID, True),),
            contribution_policy=TrustedReadNetworkContributionPolicy(),
            entry_points_provider=lambda: (FakeEntryPoint(factory),),
        )
        self.started = await self.host.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.bridge = PluginCapabilityToolBridge(
            self.host,
            config_base_dir=Path(self.temp_dir.name),
        )
        self.engine = EngineFacade(self.bridge)

    async def asyncTearDown(self) -> None:
        await self.host.stop()
        self.temp_dir.cleanup()

    async def test_active_plugin_enters_prompt_native_schema_and_real_execution_once(self) -> None:
        handlers = self.engine._resolve_tool_handlers(
            profile_user_id="user-42",
            session_id="session-7",
        )
        prompt = AkaneMemoryEngine._build_tool_prompt_context(
            self.engine,
            allow_tool_call=True,
            profile_user_id="user-42",
            session_id="session-7",
        )

        original_native_enabled = getattr(config, "ENABLE_NATIVE_TOOL_DECISION", False)
        original_allowlist = getattr(config, "NATIVE_TOOL_DECISION_ALLOWLIST", "web_search")
        try:
            config.ENABLE_NATIVE_TOOL_DECISION = True
            config.NATIVE_TOOL_DECISION_ALLOWLIST = "web_search"
            native_tools = build_native_tool_schemas(
                handlers,
                allow_tool_call=True,
                allowed_tool_names=handlers,
            )
        finally:
            config.ENABLE_NATIVE_TOOL_DECISION = original_native_enabled
            config.NATIVE_TOOL_DECISION_ALLOWLIST = original_allowlist

        result = await asyncio.to_thread(
            handlers[CAPABILITY_ID].execute,
            call={"type": CAPABILITY_ID, "arguments": {"query": "Nikkei 225"}},
            context=ToolExecutionContext(
                profile_user_id="user-42",
                session_id="session-7",
                now_ts=1_720_000_000,
                visual_payload={},
                client_mode="qq_text",
            ),
        )

        self.assertEqual(self.started["status"], "active")
        self.assertEqual(tuple(handlers), (CAPABILITY_ID,))
        self.assertEqual(prompt.count(f"- {CAPABILITY_ID}："), 1)
        self.assertEqual(len(native_tools), 1)
        self.assertEqual(native_tools[0][NATIVE_TOOL_CAPABILITY_ID_FIELD], CAPABILITY_ID)
        self.assertIn("Nikkei 225", result.followup_context)
        self.assertEqual(result.state_updates["adapter_capability_status"], "ok")
        self.assertEqual(
            self.adapter.contexts,
            [
                InvocationContext(
                    profile_user_id="user-42",
                    session_id="session-7",
                    client_mode="qq_text",
                )
            ],
        )

        self.assertNotIn("FINANCE_ASSISTANT_ENABLED", config.Settings.model_fields)
        handlers_with_legacy_profile = self.engine._resolve_tool_handlers(
            profile_user_id="user-42",
            session_id="session-7",
            domain_profile_id="finance_v1",
        )
        self.assertIn(CAPABILITY_ID, handlers_with_legacy_profile)

    async def test_stopped_host_removes_plugin_from_real_handler_resolution(self) -> None:
        self.assertIn(CAPABILITY_ID, self.engine._resolve_tool_handlers())

        await self.host.stop()

        self.assertEqual(self.engine._resolve_tool_handlers(), {})
        self.assertEqual(self.host.capability_descriptors, {})
        self.assertEqual(self.adapter.close_count, 1)

    async def test_structured_plugin_business_failure_reaches_engine_without_fake_success(self) -> None:
        self.adapter.result = CapabilityResult(
            is_error=True,
            status="rate_limited",
            reason="provider_rate_limited",
            content={"provider": "public_market", "retryable": True},
        )
        handler = self.engine._resolve_tool_handlers()[CAPABILITY_ID]

        result = await asyncio.to_thread(
            handler.execute,
            call={"type": CAPABILITY_ID, "arguments": {"query": "Nikkei 225"}},
            context=ToolExecutionContext(
                profile_user_id="user-42",
                session_id="session-7",
                now_ts=1_720_000_000,
                visual_payload={},
                client_mode="web",
            ),
        )

        self.assertEqual(result.stream_events[0]["status"], "rate_limited")
        self.assertEqual(result.stream_events[0]["reason"], "provider_rate_limited")
        self.assertEqual(result.state_updates["adapter_capability_status"], "rate_limited")
        self.assertIn("rate_limited/provider_rate_limited", result.followup_context)


class TrustedReadNetworkPolicyTests(unittest.TestCase):
    def test_policy_uses_permission_and_descriptor_shape_not_plugin_id(self) -> None:
        policy = TrustedReadNetworkContributionPolicy()
        manifest = ReadPlugin(RecordingAdapter()).manifest
        descriptor = asyncio.run(RecordingAdapter().list_capabilities())[0]
        wrong_manifest = PluginManifest(
            plugin_id="another.plugin",
            plugin_version="0.1.0",
            plugin_api_version=AKANE_PLUGIN_API_VERSION,
            permissions=("diagnostics.invoke",),
        )

        self.assertTrue(policy.validate_manifest(manifest).accepted)
        self.assertFalse(policy.validate_manifest(wrong_manifest).accepted)
        self.assertTrue(policy.validate_capability(plugin_id="unrelated.plugin", descriptor=descriptor).accepted)
        rejected = replace(descriptor, risk="high")
        self.assertFalse(policy.validate_capability(plugin_id="unrelated.plugin", descriptor=rejected).accepted)


if __name__ == "__main__":
    unittest.main()
