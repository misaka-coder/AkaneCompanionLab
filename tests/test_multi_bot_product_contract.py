from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import config
from capcore import CapabilityDescriptor, CapabilityResult, HealthStatus, InvocationContext
from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.capability_registry import CapabilityRegistry
from companion_v01.domain_profiles import (
    DomainProfileRegistry,
    build_domain_profile_prompt,
    filter_tool_names,
)
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.engine_services.tool_rounds import max_tool_rounds, resolve_tool_handlers
from companion_v01.instance_profile import (
    InstanceContext,
    InstanceProfileError,
    PluginSelection,
    parse_instance_manifest,
)
from companion_v01.plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    CAPABILITY_PROMPT_INVOKE_PERMISSION,
    NETWORK_READ_PERMISSION,
    PluginManifest,
)
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.qq_gateway import NapCatQQGateway
from companion_v01.routes.desktop_pet import build_desktop_pet_router


ROOT = Path(__file__).resolve().parents[1]
FINANCE_PLUGIN_ID = "akane.finance"
FINANCE_CAPABILITY_ID = f"{FINANCE_PLUGIN_ID}.market.lookup.v1"


class _FakeDistribution:
    version = "0.1.0"
    metadata = {"Name": "akane-finance-product-contract-fixture"}

    def read_text(self, filename: str) -> str | None:
        del filename
        return None


class _FakeEntryPoint:
    name = FINANCE_PLUGIN_ID
    dist = _FakeDistribution()

    def __init__(self, factory: Any) -> None:
        self._factory = factory

    def load(self) -> Any:
        return self._factory


class _FinanceCapabilityAdapter:
    provider_id = "provider.akane.finance.contract"

    async def health(self) -> HealthStatus:
        return HealthStatus(ok=True, status="ready")

    async def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        return (
            CapabilityDescriptor(
                id=FINANCE_CAPABILITY_ID,
                display_name="Finance lookup",
                short_hint="Read one public finance value.",
                visible_in=("base", "web", "desktop", "qq"),
                prompt_exposed=True,
                risk="low",
                confirm="never",
                effects=("network",),
                trigger=None,
                inputs=(),
                outputs=(),
                raw={"contract": "finance.product-contract.v1"},
            ),
        )

    async def invoke(
        self,
        capability_id: str,
        args: dict[str, Any],
        ctx: InvocationContext,
    ) -> CapabilityResult:
        del args, ctx
        return CapabilityResult(
            is_error=False,
            status="ok",
            content={"capability_id": capability_id},
        )

    async def aclose(self) -> None:
        return None


class _FinancePlugin:
    manifest = PluginManifest(
        plugin_id=FINANCE_PLUGIN_ID,
        plugin_version="0.1.0",
        plugin_api_version=AKANE_PLUGIN_API_VERSION,
        permissions=(CAPABILITY_PROMPT_INVOKE_PERMISSION, NETWORK_READ_PERMISSION),
    )

    def __init__(self, adapter: _FinanceCapabilityAdapter) -> None:
        self._adapter = adapter

    def register(self, registrar: Any) -> None:
        registrar.add_capability_adapter(self._adapter)


class _OrdinaryHandler:
    tool_type = "ordinary.lookup"

    @staticmethod
    def capability_status() -> dict[str, Any]:
        return {"status": "ready", "reason": ""}


class _EngineFacade:
    def __init__(self, source: PluginCapabilityToolBridge) -> None:
        self.tool_handlers = {_OrdinaryHandler.tool_type: _OrdinaryHandler()}
        self.plugin_capability_source = source
        self.capability_registry = CapabilityRegistry()


class _RuntimeMetrics:
    def __init__(self) -> None:
        self.observed: list[tuple[str, bool]] = []

    def observe_request(self, name: str, *, duration_ms: float, ok: bool) -> None:
        del duration_ms
        self.observed.append((name, ok))


class _DesktopContractEngine:
    def __init__(self) -> None:
        self.care_profiles: list[str] = []
        self.workspace_calls: list[dict[str, str]] = []

    def build_desktop_care_snapshot(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str,
        legacy_state: Any,
        now_ms: int,
    ) -> dict[str, Any]:
        del character_pack_id, legacy_state, now_ms
        self.care_profiles.append(profile_user_id)
        return {"ok": True, "status": "ready"}

    def manage_desktop_pet_workspace_panel(self, **kwargs: Any) -> dict[str, Any]:
        self.workspace_calls.append(
            {
                "profile_user_id": str(kwargs.get("profile_user_id") or ""),
                "session_id": str(kwargs.get("session_id") or ""),
            }
        )
        return {"ok": True, "status": "updated"}


def _manifest_payload(
    bot_id: str,
    *,
    care: bool = True,
    finance_enabled: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "instance_id": bot_id,
        "character_pack_id": "akane_v1",
        "features": {"care": care},
        "channels": {"qq": {"enabled": True, "profile_ref": f"qq.{bot_id}"}},
        "plugins": [{"id": FINANCE_PLUGIN_ID, "enabled": finance_enabled}],
    }


def _load_app_function(function_name: str) -> Any:
    source_path = ROOT / "companion_v01" / "app.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(source_path))
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
        ),
        None,
    )
    if function is None:
        raise AssertionError(f"production identity resolver missing: {function_name}")
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict[str, Any] = {}
    exec(compile(module, str(source_path), "exec"), namespace)
    return namespace[function_name]


class MultiBotConfigurationContractTests(unittest.TestCase):
    def test_three_bot_records_use_one_data_driven_manifest_type(self) -> None:
        manifests = (
            parse_instance_manifest(_manifest_payload("bot-a"), selected_instance_id="bot-a"),
            parse_instance_manifest(
                _manifest_payload("bot-b", care=False, finance_enabled=True),
                selected_instance_id="bot-b",
            ),
            parse_instance_manifest(_manifest_payload("bot-c"), selected_instance_id="bot-c"),
        )
        contexts = tuple(InstanceContext(manifest=manifest, source="manifest") for manifest in manifests)

        self.assertEqual({type(context) for context in contexts}, {InstanceContext})
        self.assertEqual([context.instance_id for context in contexts], ["bot-a", "bot-b", "bot-c"])
        self.assertEqual([context.features.care for context in contexts], [True, False, True])
        self.assertEqual(
            [selection.enabled for selection in contexts[1].plugins if selection.plugin_id == FINANCE_PLUGIN_ID],
            [True],
        )
        self.assertEqual(
            [selection.enabled for selection in contexts[0].plugins if selection.plugin_id == FINANCE_PLUGIN_ID],
            [False],
        )

    def test_bot_configuration_rejects_secrets_paths_and_non_boolean_care(self) -> None:
        cases = (
            ({"api_key": "not-allowed"}, "api_key"),
            ({"data_root": "not-allowed"}, "data_root"),
            ({"path": "not-allowed"}, "path"),
        )
        for extra_root, expected_field in cases:
            with self.subTest(expected_field=expected_field):
                payload = _manifest_payload("bot-a")
                payload.update(extra_root)
                with self.assertRaises(InstanceProfileError) as raised:
                    parse_instance_manifest(payload, selected_instance_id="bot-a")
                self.assertEqual(raised.exception.reason, "unsupported_manifest_field")
                self.assertEqual(raised.exception.field, expected_field)

        channel_payload = _manifest_payload("bot-a")
        channel_payload["channels"]["qq"]["token"] = "not-allowed"
        with self.assertRaises(InstanceProfileError) as raised:
            parse_instance_manifest(channel_payload, selected_instance_id="bot-a")
        self.assertEqual(raised.exception.field, "channels.qq.token")

        plugin_payload = _manifest_payload("bot-a")
        plugin_payload["plugins"][0]["path"] = "not-allowed"
        with self.assertRaises(InstanceProfileError) as raised:
            parse_instance_manifest(plugin_payload, selected_instance_id="bot-a")
        self.assertEqual(raised.exception.field, "plugins.0.path")

        care_payload = _manifest_payload("bot-a")
        care_payload["features"]["care"] = "yes"
        with self.assertRaises(InstanceProfileError) as raised:
            parse_instance_manifest(care_payload, selected_instance_id="bot-a")
        self.assertEqual(raised.exception.reason, "feature_must_be_boolean")
        self.assertEqual(raised.exception.field, "features.care")


class FinancePluginProductContractTests(unittest.IsolatedAsyncioTestCase):
    async def _resolved_handler_names(self, *, enabled: bool) -> tuple[str, ...]:
        adapter = _FinanceCapabilityAdapter()
        plugin = _FinancePlugin(adapter)

        def factory() -> _FinancePlugin:
            return plugin

        host = PluginHost(
            (PluginSelection(FINANCE_PLUGIN_ID, enabled),),
            contribution_policy=TrustedStatefulPluginContributionPolicy(),
            entry_points_provider=lambda: (_FakeEntryPoint(factory),),
        )
        status = await host.start()
        self.addAsyncCleanup(host.stop)
        self.assertEqual(status["status"], "active")
        with tempfile.TemporaryDirectory() as temp_dir:
            bridge = PluginCapabilityToolBridge(host, config_base_dir=Path(temp_dir))
            handlers = resolve_tool_handlers(_EngineFacade(bridge))
        return tuple(handlers)

    async def test_finance_plugin_only_adds_handlers_to_the_same_host_pipeline(self) -> None:
        disabled_handlers = await self._resolved_handler_names(enabled=False)
        enabled_handlers = await self._resolved_handler_names(enabled=True)

        self.assertEqual(disabled_handlers, (_OrdinaryHandler.tool_type,))
        self.assertEqual(set(enabled_handlers), {*disabled_handlers, FINANCE_CAPABILITY_ID})
        self.assertIn(_OrdinaryHandler.tool_type, enabled_handlers)

    def test_legacy_finance_flags_cannot_change_prompt_tools_or_round_budget(self) -> None:
        registry = DomainProfileRegistry()
        default_profile = registry.resolve(profile_id="default")
        legacy_finance_profile = registry.resolve(profile_id="finance_v1")
        tool_names = ("ordinary.lookup", FINANCE_CAPABILITY_ID)

        self.assertIs(legacy_finance_profile, default_profile)
        self.assertEqual(build_domain_profile_prompt(legacy_finance_profile), "")
        self.assertEqual(filter_tool_names(tool_names, legacy_finance_profile), tool_names)
        self.assertEqual(max_tool_rounds(domain_profile_id="finance_v1"), max_tool_rounds())

        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        self.assertEqual(
            engine._resolve_turn_domain_profile({"finance_mode": "push", "domain_profile": "finance_v1"}),
            "default",
        )


class DesktopDefaultBotMemoryContractTests(unittest.TestCase):
    def test_desktop_uses_default_qq_profile_with_a_distinct_short_session(self) -> None:
        resolve_payload = _load_app_function("_resolve_identity_from_payload")
        engine = _DesktopContractEngine()
        runtime_metrics = _RuntimeMetrics()
        app = FastAPI()
        app.include_router(
            build_desktop_pet_router(
                engine=engine,
                config_module=SimpleNamespace(),
                runtime_metrics=runtime_metrics,
                log_event=lambda *_args, **_kwargs: None,
                resolve_identity_from_query=lambda _request: ("desktop:device-a", "unused"),
                resolve_identity_from_payload=resolve_payload,
            )
        )

        with patch.object(config, "MASTER_QQ", ""):
            qq_session_id, qq_profile_user_id = NapCatQQGateway().resolve_identity(user_id=42_424_242)

        desktop_session_id = "desktop:device-a"
        client = TestClient(app)
        care_response = client.post(
            "/desktop-pet/care/snapshot",
            json={"user_id": desktop_session_id, "real_user_id": qq_profile_user_id},
        )
        workspace_response = client.post(
            "/desktop-pet/workspace/action",
            json={
                "user_id": desktop_session_id,
                "real_user_id": qq_profile_user_id,
                "action": "refresh",
            },
        )

        self.assertEqual(care_response.status_code, 200)
        self.assertEqual(workspace_response.status_code, 200)
        self.assertEqual(engine.care_profiles, [qq_profile_user_id])
        self.assertEqual(
            engine.workspace_calls,
            [{"profile_user_id": qq_profile_user_id, "session_id": desktop_session_id}],
        )
        self.assertNotEqual(desktop_session_id, qq_session_id)


if __name__ == "__main__":
    unittest.main()
