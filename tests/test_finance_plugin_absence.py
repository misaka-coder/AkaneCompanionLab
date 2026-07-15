from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from typing import Any

from companion_v01.capability_registry import CapabilityRegistry
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.engine_services.tool_rounds import resolve_tool_handlers
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_host import PluginHost
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge


FINANCE_PLUGIN_ID = "akane.finance"
LEGACY_FINANCE_TOOL_NAMES = {
    "market_resolve_security",
    "market_quote_snapshot",
    "market_price_series",
}


class _FakeDistribution:
    version = "0.1.0"
    metadata = {"Name": "akane-finance-failing-fixture"}

    def read_text(self, filename: str) -> str | None:
        del filename
        return None


class _FailingEntryPoint:
    name = FINANCE_PLUGIN_ID
    dist = _FakeDistribution()

    def load(self) -> Any:
        def factory() -> Any:
            raise RuntimeError("synthetic activation failure")

        return factory


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


class FinancePluginAbsenceTests(unittest.IsolatedAsyncioTestCase):
    async def _assert_absent(self, host: PluginHost) -> dict[str, Any]:
        status = await host.start()
        self.addAsyncCleanup(host.stop)
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = _EngineFacade(PluginCapabilityToolBridge(host, config_base_dir=Path(temp_dir)))
            handlers = resolve_tool_handlers(engine)

        self.assertEqual(tuple(handlers), (_OrdinaryHandler.tool_type,))
        self.assertEqual(host.capability_descriptors, {})
        self.assertFalse(any(name.startswith(f"{FINANCE_PLUGIN_ID}.") for name in handlers))
        return status

    async def test_disabled_finance_plugin_does_not_change_ordinary_tools(self) -> None:
        status = await self._assert_absent(
            PluginHost(
                (PluginSelection(FINANCE_PLUGIN_ID, False),),
                contribution_policy=TrustedStatefulPluginContributionPolicy(),
                entry_points_provider=lambda: (),
            )
        )

        self.assertEqual(status["status"], "active")

    async def test_missing_finance_plugin_degrades_host_without_changing_ordinary_tools(self) -> None:
        status = await self._assert_absent(
            PluginHost(
                (PluginSelection(FINANCE_PLUGIN_ID, True),),
                contribution_policy=TrustedStatefulPluginContributionPolicy(),
                entry_points_provider=lambda: (),
            )
        )

        self.assertEqual(status["status"], "degraded")
        plugin_status = next(item for item in status["plugins"] if item["plugin_id"] == FINANCE_PLUGIN_ID)
        self.assertEqual(plugin_status["reason"], "plugin_not_installed")

    async def test_failed_finance_plugin_degrades_host_without_changing_ordinary_tools(self) -> None:
        status = await self._assert_absent(
            PluginHost(
                (PluginSelection(FINANCE_PLUGIN_ID, True),),
                contribution_policy=TrustedStatefulPluginContributionPolicy(),
                entry_points_provider=lambda: (_FailingEntryPoint(),),
            )
        )

        self.assertEqual(status["status"], "degraded")
        plugin_status = next(item for item in status["plugins"] if item["plugin_id"] == FINANCE_PLUGIN_ID)
        self.assertEqual(plugin_status["reason"], "plugin_factory_failed")


class CoreFinanceCutoverTests(unittest.TestCase):
    def test_public_stateful_subscription_and_direct_qq_authorities_are_deleted(self) -> None:
        finance_root = Path(__file__).resolve().parents[1] / "companion_v01" / "finance"
        self.assertFalse((finance_root / "subscription_service.py").exists())
        self.assertFalse((finance_root / "qq_delivery.py").exists())

    def test_engine_never_builds_legacy_finance_runtime_even_if_old_switch_is_true(self) -> None:
        self.assertFalse(hasattr(AkaneMemoryEngine, "_build_market_data_tool_service"))
        source = inspect.getsource(AkaneMemoryEngine._build_tool_handlers)
        for tool_name in LEGACY_FINANCE_TOOL_NAMES:
            self.assertNotIn(tool_name, source)
        self.assertNotIn("MarketDataToolService", source)

        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        self.assertEqual(
            engine._resolve_turn_domain_profile({"finance_mode": "push", "domain_profile": "finance_v1"}),
            ("default", "off"),
        )


if __name__ == "__main__":
    unittest.main()
