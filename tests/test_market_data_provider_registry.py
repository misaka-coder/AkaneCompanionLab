from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import config
from companion_v01.engine import AkaneMemoryEngine
from services.market_data import (
    DisabledMarketDataProvider,
    EmQuantBridgeMarketDataProvider,
    MARKET_PROVIDER_CAPABILITY_NAMES,
    MarketDataProvider,
    MarketDataProviderRegistry,
    MarketDataProviderSettings,
    MarketDataValidationError,
    MarketNewsQuery,
    MarketProviderCapabilities,
    MarketQuoteRequest,
    MarketSeriesRequest,
    MockMarketDataProvider,
    build_default_market_data_provider_registry,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "choice_market_data_synthetic_v1.json"


class MarketDataProviderContractTests(unittest.TestCase):
    def test_provider_capability_contract_is_shared_by_all_current_adapters(self) -> None:
        providers = (
            DisabledMarketDataProvider(clock=lambda: 1_752_153_600),
            MockMarketDataProvider.from_fixture_path(FIXTURE_PATH),
            EmQuantBridgeMarketDataProvider(clock=lambda: 1_752_153_600),
        )

        for provider in providers:
            with self.subTest(provider=provider.id):
                self.assertIsInstance(provider, MarketDataProvider)
                self.assertTrue(provider.id)
                self.assertTrue(provider.source)
                self.assertIsInstance(provider.capabilities, MarketProviderCapabilities)
                self.assertEqual(
                    tuple(provider.capabilities.to_public_dict()),
                    MARKET_PROVIDER_CAPABILITY_NAMES,
                )
                for capability in MARKET_PROVIDER_CAPABILITY_NAMES:
                    self.assertEqual(
                        provider.supports(capability),
                        capability in provider.capabilities.enabled(),
                    )
                self.assertFalse(provider.supports("unknown_capability"))

    def test_current_adapters_declare_only_capabilities_they_implement(self) -> None:
        disabled = DisabledMarketDataProvider()
        mock = MockMarketDataProvider.from_fixture_path(FIXTURE_PATH)
        emquant = EmQuantBridgeMarketDataProvider()

        self.assertEqual(disabled.capabilities.enabled(), ())
        self.assertEqual(
            mock.capabilities.enabled(),
            ("news_search", "quote_snapshot", "price_series"),
        )
        self.assertEqual(
            emquant.capabilities.enabled(),
            ("news_search", "event_poll", "quote_snapshot", "price_series"),
        )
        self.assertTrue(callable(getattr(emquant, "poll_market_events", None)))
        self.assertFalse(callable(getattr(mock, "poll_market_events", None)))
        self.assertFalse(emquant.supports("security_master"))

    def test_disabled_provider_returns_structured_unavailable_without_fake_data(self) -> None:
        provider = DisabledMarketDataProvider(
            reason="disabled for contract test",
            clock=lambda: 1_752_153_600,
        )

        health = provider.health()
        news = provider.search_news(MarketNewsQuery(limit=1))
        quote = provider.get_quote_snapshots(MarketQuoteRequest(codes=("000000.TEST",)))
        series = provider.get_price_series(MarketSeriesRequest(code="000000.TEST"))

        self.assertFalse(health.ok)
        self.assertEqual(health.status, "disconnected")
        self.assertEqual(health.quota_status["mode"], "disabled")
        for result in (news, quote, series):
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "unavailable")
            self.assertEqual(result.provider, "disabled")
            self.assertIn("disabled", result.reason)
        self.assertEqual(news.data, ())
        self.assertEqual(quote.data, ())
        self.assertIsNone(series.data)


class MarketDataProviderRegistryTests(unittest.TestCase):
    def test_default_registry_is_explicit_and_never_registers_mock(self) -> None:
        registry = build_default_market_data_provider_registry()

        self.assertEqual(registry.provider_ids(), ("disabled", "emquant"))
        disabled = registry.create(MarketDataProviderSettings(provider="disabled"))
        emquant = registry.create(MarketDataProviderSettings(provider="emquant"))

        self.assertIsInstance(disabled, DisabledMarketDataProvider)
        self.assertIsInstance(emquant, EmQuantBridgeMarketDataProvider)
        self.assertNotIsInstance(disabled, MockMarketDataProvider)
        self.assertNotIsInstance(emquant, MockMarketDataProvider)

    def test_unknown_provider_fails_closed_instead_of_falling_back(self) -> None:
        registry = build_default_market_data_provider_registry()

        with self.assertRaises(MarketDataValidationError) as raised:
            registry.create(MarketDataProviderSettings(provider="typo-provider"))

        self.assertEqual(raised.exception.field, "FINANCE_MARKET_PROVIDER")
        self.assertEqual(raised.exception.code, "unsupported_provider")
        self.assertEqual(raised.exception.status, "invalid_arguments")
        self.assertEqual(raised.exception.provider, "typo-provider")

    def test_future_provider_can_register_without_engine_changes(self) -> None:
        registry = MarketDataProviderRegistry()
        registry.register(
            "future_feed",
            lambda _settings: DisabledMarketDataProvider(reason="future adapter test double"),
        )

        provider = registry.create(MarketDataProviderSettings(provider="future_feed"))

        self.assertIsInstance(provider, DisabledMarketDataProvider)
        self.assertEqual(registry.provider_ids(), ("future_feed",))

    def test_engine_uses_selected_provider_factory_and_keeps_local_store(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                config,
                "FINANCE_ASSISTANT_ENABLED",
                True,
            ),
            patch.object(config, "FINANCE_MARKET_PROVIDER", "disabled", create=True),
            patch.object(
                config,
                "FINANCE_EVENT_DB_PATH",
                str(Path(temp_dir) / "market.sqlite3"),
            ),
        ):
            service = engine._build_market_data_tool_service()

        self.assertIsNotNone(service)
        self.assertIsInstance(service.provider, DisabledMarketDataProvider)
        self.assertEqual(engine.market_data_provider_registry.provider_ids(), ("disabled", "emquant"))

    def test_engine_rejects_unknown_provider_without_constructing_mock(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                config,
                "FINANCE_ASSISTANT_ENABLED",
                True,
            ),
            patch.object(config, "FINANCE_MARKET_PROVIDER", "unknown", create=True),
            patch.object(
                config,
                "FINANCE_EVENT_DB_PATH",
                str(Path(temp_dir) / "market.sqlite3"),
            ),
        ):
            service = engine._build_market_data_tool_service()

        self.assertIsNone(service)


if __name__ == "__main__":
    unittest.main()
