from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import unittest
from zoneinfo import ZoneInfo

from services.market_data import (
    PublicInstrument,
    PublicInstrumentRegistry,
    build_default_public_instrument_registry,
)
from services.market_data.types import MarketDataValidationError


class PublicInstrumentRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = build_default_public_instrument_registry()

    def test_default_registry_contains_five_indices_and_two_verified_etfs(self) -> None:
        instruments = self.registry.all()
        self.assertEqual(
            {instrument.canonical_code for instrument in instruments},
            {
                "NIKKEI225.INDEX",
                "SP500.INDEX",
                "NASDAQCOMPOSITE.INDEX",
                "HSI.INDEX",
                "CSI300.INDEX",
                "513000.SH",
                "513520.SH",
            },
        )
        self.assertEqual(len(self.registry.for_route("yahoo")), 5)
        self.assertEqual(len(self.registry.for_route("akshare_etf")), 2)

    def test_seed_metadata_has_valid_timezone_currency_and_distinct_vendor_symbol(self) -> None:
        for instrument in self.registry.all():
            with self.subTest(code=instrument.canonical_code):
                self.assertIsNotNone(ZoneInfo(instrument.exchange_timezone))
                self.assertRegex(instrument.currency, r"^[A-Z]{3}$")
                self.assertNotEqual(instrument.canonical_code, instrument.vendor_symbol)
                self.assertTrue(instrument.active)

    def test_exact_alias_resolution_does_not_use_partial_or_vendor_symbols(self) -> None:
        nikkei = self.registry.resolve("日经 225")
        nasdaq = self.registry.resolve("Nasdaq Composite")

        self.assertTrue(nikkei.ok)
        self.assertEqual(nikkei.instrument.canonical_code, "NIKKEI225.INDEX")
        self.assertTrue(nasdaq.ok)
        self.assertEqual(nasdaq.instrument.canonical_code, "NASDAQCOMPOSITE.INDEX")
        self.assertEqual(self.registry.resolve("纳指").status, "not_found")
        self.assertEqual(self.registry.resolve("^N225").status, "not_found")
        etf = self.registry.resolve("513000")
        self.assertTrue(etf.ok)
        self.assertEqual(etf.instrument.canonical_code, "513000.SH")
        csi300 = self.registry.resolve("000300")
        self.assertTrue(csi300.ok)
        self.assertEqual(csi300.instrument.canonical_code, "CSI300.INDEX")

    def test_public_serialization_excludes_vendor_routing_details(self) -> None:
        payload = self.registry.require("NIKKEI225.INDEX").to_public_dict()

        self.assertEqual(payload["code"], "NIKKEI225.INDEX")
        self.assertNotIn("vendor_symbol", payload)
        self.assertNotIn("route", payload)

    def test_etfs_track_nikkei_without_becoming_the_index(self) -> None:
        for code in ("513000.SH", "513520.SH"):
            etf = self.registry.require(code)
            self.assertEqual(etf.instrument_type, "etf")
            self.assertEqual(etf.tracking_target, "NIKKEI225.INDEX")
            self.assertEqual(etf.currency, "CNY")
            self.assertEqual(etf.quote_delay_kind, "unknown")
        index = self.registry.require("NIKKEI225.INDEX")
        self.assertEqual(index.instrument_type, "index")
        self.assertEqual(index.currency, "JPY")
        self.assertEqual(index.tracking_target, "")

    def test_unknown_instrument_fails_with_structured_error(self) -> None:
        with self.assertRaises(MarketDataValidationError) as raised:
            self.registry.require("UNKNOWN.INDEX")

        self.assertEqual(raised.exception.status, "invalid_arguments")
        self.assertEqual(raised.exception.code, "unknown_instrument")
        self.assertEqual(raised.exception.provider, "public_market")

    def test_ambiguous_exact_alias_returns_candidates_instead_of_guessing(self) -> None:
        first = self.registry.require("513000.SH")
        second = self.registry.require("513520.SH")
        target = self.registry.require("NIKKEI225.INDEX")
        registry = PublicInstrumentRegistry(
            [
                target,
                replace(first, aliases=(*first.aliases, "日经ETF")),
                replace(second, aliases=(*second.aliases, "日经ETF")),
            ]
        )

        resolution = registry.resolve("日经ETF")

        self.assertFalse(resolution.ok)
        self.assertEqual(resolution.status, "ambiguous")
        self.assertEqual(
            {candidate.canonical_code for candidate in resolution.candidates},
            {"513000.SH", "513520.SH"},
        )

    def test_registry_rejects_duplicate_codes_and_vendor_symbols(self) -> None:
        instrument = self.registry.require("NIKKEI225.INDEX")
        with self.assertRaisesRegex(ValueError, "duplicate public instrument code"):
            PublicInstrumentRegistry([instrument, instrument])
        with self.assertRaisesRegex(ValueError, "duplicate public vendor symbol"):
            PublicInstrumentRegistry(
                [instrument, replace(instrument, canonical_code="OTHER.INDEX", display_name="Other")]
            )

    def test_registry_rejects_unknown_or_non_index_tracking_targets(self) -> None:
        etf = self.registry.require("513000.SH")
        other_etf = replace(
            self.registry.require("513520.SH"),
            canonical_code="OTHERETF.SH",
            vendor_symbol="OTHERETF",
            tracking_target="",
        )
        with self.assertRaisesRegex(ValueError, "unknown tracking target"):
            PublicInstrumentRegistry([etf])
        with self.assertRaisesRegex(ValueError, "tracking target must be an index"):
            PublicInstrumentRegistry([replace(etf, tracking_target="OTHERETF.SH"), other_etf])

    def test_instrument_is_immutable_and_validates_enums(self) -> None:
        instrument = self.registry.require("HSI.INDEX")
        with self.assertRaises(FrozenInstanceError):
            instrument.currency = "USD"
        with self.assertRaises(MarketDataValidationError):
            replace(instrument, route="unknown")
        with self.assertRaises(MarketDataValidationError):
            replace(instrument, exchange_timezone="Mars/Olympus")

    def test_inactive_instruments_are_hidden_unless_explicitly_requested(self) -> None:
        inactive = replace(self.registry.require("HSI.INDEX"), active=False)
        registry = PublicInstrumentRegistry([inactive])

        self.assertEqual(registry.all(), ())
        self.assertIsNone(registry.get("HSI.INDEX"))
        self.assertIsNotNone(registry.get("HSI.INDEX", include_inactive=True))
        self.assertEqual(registry.resolve("恒指").status, "not_found")
        self.assertTrue(registry.resolve("恒指", include_inactive=True).ok)


if __name__ == "__main__":
    unittest.main()
