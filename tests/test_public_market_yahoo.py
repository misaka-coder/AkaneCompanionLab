from __future__ import annotations

from datetime import datetime
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from services.market_data import (
    MarketQuoteRequest,
    MarketSeriesRequest,
    TTLMarketDataCache,
    YahooFinanceAdapter,
    YahooFinanceDependencyUnavailable,
)
from services.market_data.types import MarketDataValidationError
import services.market_data.public_yahoo as public_yahoo


TOKYO = ZoneInfo("Asia/Tokyo")
FIXED_NOW = int(datetime(2026, 7, 11, 12, 0, tzinfo=TOKYO).timestamp())


class MutableClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeFrame:
    def __init__(self, rows, *, columns=None, empty: bool = False) -> None:
        self._rows = list(rows)
        self.columns = tuple(columns or ("Open", "High", "Low", "Close", "Adj Close", "Volume"))
        self.empty = bool(empty)

    def reset_index(self):
        return self

    def to_dict(self, *, orient: str):
        if orient != "records":
            raise AssertionError("Yahoo adapter must request record orientation")
        return list(self._rows)


def _daily_rows():
    return [
        {"Date": "2026-07-08", "Open": 50000, "High": 50500, "Low": 49800, "Close": 50400, "Volume": 0},
        {"Date": "2026-07-10", "Open": 50800, "High": 51200, "Low": 50700, "Close": 51100, "Volume": None},
        {"Date": "2026-07-09", "Open": 50450, "High": 50900, "Low": 50300, "Close": 50800, "Volume": 0},
    ]


class YahooFinanceAdapterTests(unittest.TestCase):
    def test_retry_policy_allows_three_attempts_but_rejects_more_or_negative_backoff(self) -> None:
        YahooFinanceAdapter(retry_max_attempts=3)
        with self.assertRaises(ValueError):
            YahooFinanceAdapter(retry_max_attempts=4)
        with self.assertRaises(ValueError):
            YahooFinanceAdapter(retry_backoff_seconds=-0.1)

    def test_default_downloader_uses_single_ticker_history_with_errors_enabled(self) -> None:
        calls = []

        class FakeTicker:
            def __init__(self, symbol: str) -> None:
                self.symbol = symbol

            def history(self, **kwargs):
                calls.append((self.symbol, kwargs))
                return "frame"

        fake_yfinance = SimpleNamespace(Ticker=FakeTicker)
        with patch.dict(sys.modules, {"yfinance": fake_yfinance}):
            result = public_yahoo._default_yahoo_downloader(
                tickers="^N225",
                start="2026-07-01",
                end="2026-07-11",
                interval="1d",
                auto_adjust=False,
                multi_level_index=False,
                threads=False,
                progress=False,
                timeout=8.0,
            )

        self.assertEqual(result, "frame")
        self.assertEqual(calls[0][0], "^N225")
        self.assertEqual(
            calls[0][1],
            {
                "start": "2026-07-01",
                "end": "2026-07-11",
                "interval": "1d",
                "auto_adjust": False,
                "timeout": 8.0,
                "actions": False,
                "back_adjust": False,
                "repair": False,
                "keepna": False,
                "raise_errors": True,
            },
        )

    def test_default_chart_downloader_uses_bounded_http_and_normalizes_daily_rows(self) -> None:
        calls = []

        class FakeResponse:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {
                    "chart": {
                        "error": None,
                        "result": [
                            {
                                "meta": {"exchangeTimezoneName": "Asia/Tokyo"},
                                "timestamp": [
                                    int(datetime(2026, 7, 8, 0, 0, tzinfo=TOKYO).timestamp()),
                                    int(datetime(2026, 7, 9, 0, 0, tzinfo=TOKYO).timestamp()),
                                ],
                                "indicators": {
                                    "quote": [
                                        {
                                            "open": [50000, 50400],
                                            "high": [50500, 50900],
                                            "low": [49800, 50300],
                                            "close": [50400, 50800],
                                            "volume": [0, 0],
                                        }
                                    ]
                                },
                            }
                        ],
                    }
                }

        def fake_get(url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

        with patch.object(public_yahoo.requests, "get", side_effect=fake_get):
            frame = public_yahoo._default_yahoo_chart_downloader(
                tickers="^N225",
                start="2026-07-01",
                end="2026-07-11",
                interval="1d",
                timeout=4.0,
            )

        rows = frame.reset_index().to_dict(orient="records")
        self.assertIn("%5EN225", calls[0][0])
        self.assertEqual(calls[0][1]["timeout"], 4.0)
        self.assertEqual(rows[0]["Date"], "2026-07-08")
        self.assertEqual(rows[-1]["Close"], 50800)

    def test_search_quotes_returns_bounded_mapping_results(self) -> None:
        calls = []

        def searcher(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                quotes=[
                    {"symbol": "002594.SZ", "quoteType": "EQUITY"},
                    {"symbol": "1211.HK", "quoteType": "EQUITY"},
                ]
            )

        adapter = YahooFinanceAdapter(searcher=searcher)

        result = adapter.search_quotes("比亚迪代码", max_results=1)

        self.assertEqual(result, ({"symbol": "002594.SZ", "quoteType": "EQUITY"},))
        self.assertEqual(calls[0], {"query": "比亚迪代码", "max_results": 1, "news_count": 0})

    def test_daily_series_uses_canonical_code_and_preserves_provenance(self) -> None:
        calls = []

        def downloader(**kwargs):
            calls.append(kwargs)
            return FakeFrame(_daily_rows())

        adapter = YahooFinanceAdapter(downloader=downloader, clock=lambda: FIXED_NOW)
        result = adapter.get_price_series(MarketSeriesRequest(code="NIKKEI225.INDEX", limit=2))

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.provider, "public_market")
        self.assertEqual(result.source, "Yahoo Finance")
        self.assertEqual(result.timezone, "Asia/Tokyo")
        self.assertEqual(result.data.code, "NIKKEI225.INDEX")
        self.assertEqual([point.trading_date for point in result.data.points], ["2026-07-09", "2026-07-10"])
        self.assertTrue(all(point.time_semantics == "trading_date" for point in result.data.points))
        self.assertEqual(result.data.adjusted, "none")
        self.assertEqual(result.data.provenance.vendor_symbol, "^N225")
        self.assertEqual(result.data.provenance.fetched_at, FIXED_NOW)
        self.assertEqual(result.data.provenance.currency, "JPY")
        self.assertEqual(result.data.provenance.delay_kind, "end_of_day")
        self.assertEqual(result.data.provenance.data_quality, "public_web")
        self.assertEqual(len(calls), 1)

    def test_series_success_and_failures_use_separate_ttls(self) -> None:
        cache_clock = MutableClock()
        cache = TTLMarketDataCache(max_entries=10, clock=cache_clock)
        calls = []

        def downloader(**kwargs):
            calls.append(kwargs)
            return FakeFrame(_daily_rows())

        adapter = YahooFinanceAdapter(
            downloader=downloader,
            cache=cache,
            series_ttl_seconds=30,
            failure_ttl_seconds=5,
            clock=lambda: FIXED_NOW,
        )
        request = MarketSeriesRequest(code="NIKKEI225.INDEX", limit=2)
        first = adapter.get_price_series(request)
        cache_clock.value = 120
        second = adapter.get_price_series(request)
        cache_clock.value = 131
        third = adapter.get_price_series(request)

        self.assertIs(first, second)
        self.assertEqual(first.data.provenance.fetched_at, second.data.provenance.fetched_at)
        self.assertIsNot(first, third)
        self.assertEqual(len(calls), 2)

        failure_calls = []

        class CaptureTimeoutError(Exception):
            pass

        def failing(**_kwargs):
            failure_calls.append(True)
            raise CaptureTimeoutError()

        failing_adapter = YahooFinanceAdapter(
            downloader=failing,
            cache=TTLMarketDataCache(max_entries=10, clock=cache_clock),
            failure_ttl_seconds=5,
            retry_sleeper=lambda _seconds: None,
            clock=lambda: FIXED_NOW,
        )
        cache_clock.value = 200
        failure_request = MarketSeriesRequest(code="HSI.INDEX", limit=2)
        failed_first = failing_adapter.get_price_series(failure_request)
        cache_clock.value = 204
        failed_second = failing_adapter.get_price_series(failure_request)
        cache_clock.value = 206
        failing_adapter.get_price_series(failure_request)

        self.assertIs(failed_first, failed_second)
        self.assertEqual(len(failure_calls), 6)

    def test_ssl_transport_failure_retries_up_to_third_attempt(self) -> None:
        calls = []

        def downloader(**_kwargs):
            calls.append(True)
            if len(calls) < 3:
                raise RuntimeError("curl: (35) SSL connect error")
            return FakeFrame(_daily_rows())

        result = YahooFinanceAdapter(
            downloader=downloader,
            retry_sleeper=lambda _seconds: None,
            clock=lambda: FIXED_NOW,
        ).get_price_series(MarketSeriesRequest(code="NIKKEI225.INDEX", limit=2))

        self.assertTrue(result.ok)
        self.assertEqual(len(calls), 3)

    def test_series_cache_key_includes_limit_and_date_range(self) -> None:
        calls = []

        def downloader(**kwargs):
            calls.append(kwargs)
            return FakeFrame(_daily_rows())

        adapter = YahooFinanceAdapter(downloader=downloader, clock=lambda: FIXED_NOW)
        adapter.get_price_series(MarketSeriesRequest(code="NIKKEI225.INDEX", limit=1))
        adapter.get_price_series(MarketSeriesRequest(code="NIKKEI225.INDEX", limit=2))
        adapter.get_price_series(
            MarketSeriesRequest(
                code="NIKKEI225.INDEX",
                limit=2,
                date_from=int(datetime(2026, 7, 9, tzinfo=TOKYO).timestamp()),
            )
        )

        self.assertEqual(len(calls), 3)

    def test_download_arguments_override_yfinance_defaults_and_use_exclusive_end(self) -> None:
        calls = []

        def downloader(**kwargs):
            calls.append(kwargs)
            return FakeFrame(_daily_rows())

        date_from = int(datetime(2026, 7, 8, 15, 0, tzinfo=TOKYO).timestamp())
        date_to = int(datetime(2026, 7, 10, 23, 0, tzinfo=TOKYO).timestamp())
        adapter = YahooFinanceAdapter(downloader=downloader, timeout_seconds=9, clock=lambda: FIXED_NOW)
        result = adapter.get_price_series(
            MarketSeriesRequest(
                code="NIKKEI225.INDEX",
                date_from=date_from,
                date_to=date_to,
                limit=10,
            )
        )

        self.assertEqual(
            [point.trading_date for point in result.data.points],
            ["2026-07-08", "2026-07-09", "2026-07-10"],
        )
        self.assertEqual(
            calls[0],
            {
                "tickers": "^N225",
                "start": "2026-07-08",
                "end": "2026-07-11",
                "interval": "1d",
                "auto_adjust": False,
                "multi_level_index": False,
                "threads": False,
                "progress": False,
                "timeout": 9.0,
            },
        )

    def test_public_dict_marks_daily_dates_without_calling_them_intraday_instants(self) -> None:
        adapter = YahooFinanceAdapter(downloader=lambda **_kwargs: FakeFrame(_daily_rows()), clock=lambda: FIXED_NOW)

        result = adapter.get_price_series(MarketSeriesRequest(code="NIKKEI225.INDEX", limit=1))
        payload = result.to_public_dict()

        point = payload["data"]["points"][0]
        self.assertEqual(point["trading_date"], "2026-07-10")
        self.assertEqual(point["time_semantics"], "trading_date")
        self.assertEqual(payload["data"]["provenance"]["fetched_at"], FIXED_NOW)
        self.assertEqual(payload["data"]["provenance"]["vendor_symbol"], "^N225")

    def test_empty_frame_returns_structured_empty(self) -> None:
        adapter = YahooFinanceAdapter(downloader=lambda **_kwargs: FakeFrame([], empty=True), clock=lambda: FIXED_NOW)

        result = adapter.get_price_series(MarketSeriesRequest(code="SP500.INDEX", limit=5))

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "empty")
        self.assertIsNone(result.data)
        self.assertEqual(result.reason, "no_observations:SP500.INDEX")

    def test_quote_snapshot_is_explicitly_latest_completed_daily_bar(self) -> None:
        calls = []

        def downloader(**kwargs):
            calls.append(kwargs)
            return FakeFrame(_daily_rows())

        adapter = YahooFinanceAdapter(downloader=downloader, clock=lambda: FIXED_NOW)
        request = MarketQuoteRequest(codes=("NIKKEI225.INDEX",))
        first = adapter.get_quote_snapshots(request)
        second = adapter.get_quote_snapshots(request)

        self.assertTrue(first.ok)
        self.assertEqual(first.status, "ok")
        self.assertEqual(first.reason, "latest_completed_daily_bar")
        self.assertEqual(len(first.data), 1)
        snapshot = first.data[0]
        self.assertEqual(snapshot.code, "NIKKEI225.INDEX")
        self.assertEqual(snapshot.status, "end_of_day")
        self.assertEqual(snapshot.trading_date, "2026-07-10")
        self.assertEqual(snapshot.time_semantics, "trading_date")
        self.assertEqual(snapshot.previous_close, 50800)
        self.assertEqual(snapshot.last, 51100)
        self.assertEqual(snapshot.change, 300)
        self.assertAlmostEqual(snapshot.change_pct, 300 / 50800 * 100)
        self.assertEqual(snapshot.provenance.delay_kind, "end_of_day")
        self.assertIs(first.data[0], second.data[0])
        self.assertEqual(len(calls), 1)

    def test_quote_excludes_same_day_partial_bar_without_guessing_market_close(self) -> None:
        current_day = int(datetime(2026, 7, 10, 12, 0, tzinfo=TOKYO).timestamp())
        adapter = YahooFinanceAdapter(
            downloader=lambda **_kwargs: FakeFrame(_daily_rows()),
            clock=lambda: current_day,
        )

        result = adapter.get_quote_snapshots(MarketQuoteRequest(codes=("NIKKEI225.INDEX",)))

        self.assertTrue(result.ok)
        self.assertEqual(result.data[0].trading_date, "2026-07-09")
        self.assertEqual(result.data[0].last, 50800)

    def test_quote_without_completed_daily_observation_is_unavailable(self) -> None:
        rows = [{"Date": "2026-07-11", "Open": 10, "High": 12, "Low": 9, "Close": 11, "Volume": 0}]
        adapter = YahooFinanceAdapter(
            downloader=lambda **_kwargs: FakeFrame(rows),
            clock=lambda: FIXED_NOW,
        )

        result = adapter.get_quote_snapshots(MarketQuoteRequest(codes=("NIKKEI225.INDEX",)))

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.reason, "observation_time_unavailable")
        self.assertEqual(result.data, ())

    def test_multi_code_quote_fails_closed_without_returning_partial_data(self) -> None:
        def downloader(**kwargs):
            if kwargs["tickers"] == "^GSPC":
                raise TimeoutError()
            return FakeFrame(_daily_rows())

        adapter = YahooFinanceAdapter(
            downloader=downloader,
            retry_sleeper=lambda _seconds: None,
            clock=lambda: FIXED_NOW,
        )

        result = adapter.get_quote_snapshots(MarketQuoteRequest(codes=("NIKKEI225.INDEX", "SP500.INDEX")))

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.reason, "upstream_timeout:yahoo")
        self.assertEqual(result.data, ())

    def test_timeout_retries_once_then_recovers(self) -> None:
        calls = []
        sleeps = []

        def downloader(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise TimeoutError("temporary timeout")
            return FakeFrame(_daily_rows())

        adapter = YahooFinanceAdapter(
            downloader=downloader,
            retry_sleeper=sleeps.append,
            clock=lambda: FIXED_NOW,
        )

        result = adapter.get_price_series(MarketSeriesRequest(code="NIKKEI225.INDEX", limit=2))

        self.assertTrue(result.ok)
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [0.2])

    def test_explicit_http_503_retries_but_http_400_does_not(self) -> None:
        class HTTPFailure(Exception):
            def __init__(self, status_code):
                super().__init__(f"HTTP {status_code}")
                self.response = SimpleNamespace(status_code=status_code)

        retry_calls = []

        def temporary_503(**_kwargs):
            retry_calls.append(True)
            if len(retry_calls) == 1:
                raise HTTPFailure(503)
            return FakeFrame(_daily_rows())

        retry_result = YahooFinanceAdapter(
            downloader=temporary_503,
            retry_sleeper=lambda _seconds: None,
            clock=lambda: FIXED_NOW,
        ).get_price_series(MarketSeriesRequest(code="SP500.INDEX", limit=2))

        bad_request_calls = []

        def permanent_400(**_kwargs):
            bad_request_calls.append(True)
            raise HTTPFailure(400)

        failure_result = YahooFinanceAdapter(
            downloader=permanent_400,
            retry_sleeper=lambda _seconds: None,
            clock=lambda: FIXED_NOW,
        ).get_price_series(MarketSeriesRequest(code="HSI.INDEX", limit=2))

        self.assertTrue(retry_result.ok)
        self.assertEqual(len(retry_calls), 2)
        self.assertFalse(failure_result.ok)
        self.assertEqual(len(bad_request_calls), 1)

    def test_missing_dependency_timeout_and_rate_limit_are_structured(self) -> None:
        def missing(**_kwargs):
            raise YahooFinanceDependencyUnavailable()

        class CaptureTimeoutError(Exception):
            pass

        class YFRateLimitError(Exception):
            pass

        cases = (
            (missing, "unavailable", "optional_dependency_missing:yfinance"),
            (lambda **_kwargs: (_ for _ in ()).throw(CaptureTimeoutError()), "unavailable", "upstream_timeout:yahoo"),
            (
                lambda **_kwargs: (_ for _ in ()).throw(YFRateLimitError()),
                "rate_limited",
                "upstream_rate_limited:yahoo",
            ),
        )
        for downloader, status, reason in cases:
            with self.subTest(status=status, reason=reason):
                result = YahooFinanceAdapter(downloader=downloader, clock=lambda: FIXED_NOW).get_price_series(
                    MarketSeriesRequest(code="HSI.INDEX", limit=5)
                )
                self.assertFalse(result.ok)
                self.assertEqual(result.status, status)
                self.assertEqual(result.reason, reason)
                self.assertIsNone(result.data)

    def test_schema_failures_do_not_return_partial_or_fake_series(self) -> None:
        frames = (
            FakeFrame(_daily_rows(), columns=("Open", "High", "Close", "Volume")),
            FakeFrame([{"Date": "2026-07-10", "Open": 10, "High": 9, "Low": 8, "Close": 10}]),
            FakeFrame([{"Date": "not-a-date", "Open": 8, "High": 10, "Low": 7, "Close": 9}]),
        )
        for frame in frames:
            with self.subTest(columns=frame.columns):
                result = YahooFinanceAdapter(
                    downloader=lambda **_kwargs: frame, clock=lambda: FIXED_NOW
                ).get_price_series(MarketSeriesRequest(code="NIKKEI225.INDEX", limit=5))
                self.assertFalse(result.ok)
                self.assertEqual(result.status, "unavailable")
                self.assertTrue(result.reason.startswith("upstream_schema_changed:yahoo_v1:"))
                self.assertIsNone(result.data)

    def test_non_yahoo_codes_and_unsupported_requests_fail_before_network(self) -> None:
        calls = []

        def downloader(**kwargs):
            calls.append(kwargs)
            return FakeFrame(_daily_rows())

        adapter = YahooFinanceAdapter(downloader=downloader, clock=lambda: FIXED_NOW)
        requests = (
            MarketSeriesRequest(code="513000.SH"),
            MarketSeriesRequest(code="NIKKEI225.INDEX", interval="1m"),
            MarketSeriesRequest(code="NIKKEI225.INDEX", adjusted="forward"),
        )
        for request in requests:
            with self.subTest(request=request):
                with self.assertRaises(MarketDataValidationError):
                    adapter.get_price_series(request)
        self.assertEqual(calls, [])

    def test_tuple_columns_are_tolerated_if_yfinance_ignores_single_level_request(self) -> None:
        symbol = "^N225"
        columns = tuple((name, symbol) for name in ("Open", "High", "Low", "Close", "Volume"))
        rows = [
            {
                ("Date", ""): "2026-07-10",
                ("Open", symbol): 10,
                ("High", symbol): 12,
                ("Low", symbol): 9,
                ("Close", symbol): 11,
                ("Volume", symbol): 0,
            }
        ]
        adapter = YahooFinanceAdapter(
            downloader=lambda **_kwargs: FakeFrame(rows, columns=columns),
            clock=lambda: FIXED_NOW,
        )

        result = adapter.get_price_series(MarketSeriesRequest(code="NIKKEI225.INDEX"))

        self.assertTrue(result.ok)
        self.assertEqual(result.data.points[0].close, 11)


if __name__ == "__main__":
    unittest.main()
