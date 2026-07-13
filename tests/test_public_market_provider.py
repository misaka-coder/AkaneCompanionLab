from __future__ import annotations

import unittest
from pathlib import Path
import tempfile

from companion_v01.finance.market_service import MarketDataToolService
from services.market_data import (
    MarketBar,
    MarketDataResponse,
    MarketEventStore,
    MarketNewsQuery,
    MarketQuoteRequest,
    MarketQuoteSnapshot,
    MarketSeries,
    MarketSeriesRequest,
    PublicMarketProvider,
)


def _snapshot(code: str, *, timezone: str, as_of: int) -> MarketQuoteSnapshot:
    return MarketQuoteSnapshot(
        provider="public_market",
        code=code,
        as_of=as_of,
        timezone=timezone,
        previous_close=9,
        open=9.5,
        high=11,
        low=9,
        last=10,
        volume=100,
        amount=1000,
        change=1,
        change_pct=11.11,
        status="test",
    )


class StubAdapter:
    def __init__(self, *, source: str, timezone: str, quote_failure: bool = False) -> None:
        self.source = source
        self.timezone = timezone
        self.quote_failure = quote_failure
        self.series_calls = []
        self.quote_calls = []
        self.search_results = []

    def search_quotes(self, query, *, max_results=10):
        return tuple(self.search_results[:max_results])

    def get_price_series(self, request):
        self.series_calls.append(request)
        point = MarketBar(timestamp=100, open=9, high=11, low=8, close=10)
        series = MarketSeries(
            provider="public_market",
            code=request.code,
            interval="1d",
            adjusted="none",
            timezone=self.timezone,
            points=(point,),
            as_of=100,
        )
        return MarketDataResponse(
            ok=True,
            status="ok",
            provider="public_market",
            source=self.source,
            as_of=100,
            reason="",
            data=series,
            timezone=self.timezone,
        )

    def get_quote_snapshots(self, request):
        self.quote_calls.append(request)
        if self.quote_failure:
            return MarketDataResponse(
                ok=False,
                status="unavailable",
                provider="public_market",
                source=self.source,
                as_of=None,
                reason="stub_failure",
                data=(),
                timezone=self.timezone,
            )
        data = tuple(
            _snapshot(code, timezone=self.timezone, as_of=100 + index) for index, code in enumerate(request.codes)
        )
        return MarketDataResponse(
            ok=True,
            status="ok",
            provider="public_market",
            source=self.source,
            as_of=max(item.as_of for item in data),
            reason="",
            data=data,
            timezone=self.timezone,
        )


class PublicMarketProviderTests(unittest.TestCase):
    def test_capabilities_and_news_failure_are_explicit(self) -> None:
        provider = PublicMarketProvider(dependency_probe=lambda _name: True)
        self.assertEqual(provider.capabilities.enabled(), ("quote_snapshot", "price_series", "security_master"))
        result = provider.search_news(MarketNewsQuery(query="test"))
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.reason, "capability_unavailable:news_search")

    def test_security_master_seed_is_idempotent_and_resolves_exact_aliases(self) -> None:
        provider = PublicMarketProvider(dependency_probe=lambda _name: False)
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MarketEventStore(Path(temp_dir) / "market.sqlite3")
            first = provider.seed_security_master(store, now_ts=200)
            second = provider.seed_security_master(store, now_ts=300)
            nikkei = store.resolve_security("日经225", provider="public_market")
            nikkei_request = store.resolve_security("画一张日经225最近三个月K线", provider="public_market")
            etf = store.resolve_security("日经ETF华夏", provider="public_market")
            etf_code = store.resolve_security("513000", provider="public_market")
            csi300 = store.resolve_security("000300", provider="public_market")
            byd = store.resolve_security("比亚迪", provider="public_market")
            vendor = store.resolve_security("^N225", provider="public_market")
            service = MarketDataToolService(provider=provider, event_store=store)
            canonical_pair = service.canonicalize_trusted_codes(
                ("513000", "000300"),
                profile_user_id="qq_group_shared_123",
                session_id="qq_group_shared_123",
            )

        self.assertEqual(len(first), 11)
        self.assertEqual(len(second), 11)
        self.assertEqual(nikkei[0]["code"], "NIKKEI225.INDEX")
        self.assertEqual(nikkei[0]["match_type"], "exact")
        self.assertEqual(nikkei_request[0]["code"], "NIKKEI225.INDEX")
        self.assertEqual(nikkei_request[0]["match_type"], "embedded")
        self.assertEqual(etf[0]["code"], "513520.SH")
        self.assertEqual(etf_code[0]["code"], "513000.SH")
        self.assertEqual(etf_code[0]["match_type"], "exact")
        self.assertEqual(csi300[0]["code"], "CSI300.INDEX")
        self.assertEqual(csi300[0]["match_type"], "exact")
        self.assertEqual(canonical_pair, ("513000.SH", "CSI300.INDEX"))
        self.assertEqual({item["code"] for item in byd}, {"002594.SZ", "1211.HK"})
        self.assertEqual(vendor, ())

    def test_runtime_symbol_discovery_verifies_and_seeds_unknown_code(self) -> None:
        yahoo = StubAdapter(source="Yahoo", timezone="Asia/Shanghai")
        yahoo.search_results = [
            {
                "exchange": "NMS",
                "shortname": "Tesla, Inc.",
                "longname": "Tesla, Inc.",
                "quoteType": "EQUITY",
                "symbol": "TSLA",
            }
        ]
        provider = PublicMarketProvider(yahoo=yahoo, dependency_probe=lambda _name: True)
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MarketEventStore(Path(temp_dir) / "market.sqlite3")
            provider.seed_security_master(store, now_ts=200)
            service = MarketDataToolService(provider=provider, event_store=store, clock=lambda: 300)

            result = service.resolve_security(
                "TSLA",
                profile_user_id="qq_group_shared_123",
                session_id="qq_group_shared_123",
            )

        self.assertTrue(result["resolved"])
        self.assertEqual(result["resolved_code"], "TSLA.US")
        self.assertIn("Yahoo Finance Search", result["data"][0]["source"])

    def test_series_routes_by_canonical_instrument(self) -> None:
        yahoo = StubAdapter(source="Yahoo", timezone="Asia/Tokyo")
        akshare = StubAdapter(source="AkShare", timezone="Asia/Shanghai")
        provider = PublicMarketProvider(yahoo=yahoo, akshare=akshare, dependency_probe=lambda _name: True)
        index = provider.get_price_series(MarketSeriesRequest(code="NIKKEI225.INDEX"))
        etf = provider.get_price_series(MarketSeriesRequest(code="513000.SH"))
        self.assertTrue(index.ok and etf.ok)
        self.assertEqual(len(yahoo.series_calls), 1)
        self.assertEqual(len(akshare.series_calls), 1)

    def test_mixed_quote_routes_reassemble_original_order(self) -> None:
        yahoo = StubAdapter(source="Yahoo", timezone="Asia/Tokyo")
        akshare = StubAdapter(source="AkShare", timezone="Asia/Shanghai")
        provider = PublicMarketProvider(yahoo=yahoo, akshare=akshare, dependency_probe=lambda _name: True)
        request = MarketQuoteRequest(codes=("513000.SH", "NIKKEI225.INDEX", "513520.SH"))
        result = provider.get_quote_snapshots(request)
        self.assertTrue(result.ok)
        self.assertEqual([item.code for item in result.data], list(request.codes))
        self.assertEqual(result.timezone, "Asia/Shanghai")
        self.assertEqual(result.source, "AkShare + Yahoo")

    def test_mixed_quote_failure_returns_no_partial_data(self) -> None:
        provider = PublicMarketProvider(
            yahoo=StubAdapter(source="Yahoo", timezone="Asia/Tokyo", quote_failure=True),
            akshare=StubAdapter(source="AkShare", timezone="Asia/Shanghai"),
            dependency_probe=lambda _name: True,
        )
        result = provider.get_quote_snapshots(MarketQuoteRequest(codes=("513000.SH", "NIKKEI225.INDEX")))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "stub_failure")
        self.assertEqual(result.data, ())

    def test_disabled_route_is_unavailable_not_invalid_or_mock(self) -> None:
        provider = PublicMarketProvider(yahoo_enabled=False, akshare_enabled=True, dependency_probe=lambda _name: True)
        result = provider.get_price_series(MarketSeriesRequest(code="NIKKEI225.INDEX"))
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.reason, "public_route_disabled:yahoo")

    def test_health_reflects_enabled_optional_dependencies_without_network(self) -> None:
        ready = PublicMarketProvider(dependency_probe=lambda _name: True, clock=lambda: 100)
        degraded = PublicMarketProvider(dependency_probe=lambda name: name == "requests", clock=lambda: 100)
        disconnected = PublicMarketProvider(dependency_probe=lambda _name: False, clock=lambda: 100)
        self.assertEqual(ready.health().status, "ready")
        self.assertEqual(degraded.health().status, "degraded")
        self.assertEqual(disconnected.health().status, "disconnected")
        self.assertFalse(ready.health().logged_in)


if __name__ == "__main__":
    unittest.main()
