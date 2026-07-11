from __future__ import annotations

from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from services.market_data import AkShareETFAdapter, MarketQuoteRequest, MarketSeriesRequest
from services.market_data.types import MarketDataValidationError


SHANGHAI = ZoneInfo("Asia/Shanghai")
FIXED_NOW = int(datetime(2026, 7, 11, 12, 0, tzinfo=SHANGHAI).timestamp())


class FakeFrame:
    def __init__(self, rows, columns, *, empty=False):
        self.rows = list(rows)
        self.columns = tuple(columns)
        self.empty = empty

    def to_dict(self, *, orient):
        assert orient == "records"
        return list(self.rows)


HISTORY_COLUMNS = ("日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额", "振幅", "涨跌幅", "涨跌额", "换手率")
SPOT_COLUMNS = ("代码", "名称", "最新价", "成交量", "成交额", "开盘价", "最高价", "最低价", "昨收", "数据日期", "更新时间")


def history_frame():
    return FakeFrame([
        {"日期": "2026-07-09", "开盘": 2.408, "收盘": 2.39, "最高": 2.412, "最低": 2.375, "成交量": 830305, "成交额": 198783710},
        {"日期": "2026-07-10", "开盘": 2.42, "收盘": 2.403, "最高": 2.458, "最低": 2.4, "成交量": 1145308, "成交额": 278431674},
    ], HISTORY_COLUMNS)


def spot_frame():
    return FakeFrame([
        {"代码": "513000", "名称": "日经225ETF易方达", "最新价": 2.403, "成交量": 1145308, "成交额": 278431674, "开盘价": 2.42, "最高价": 2.458, "最低价": 2.4, "昨收": 2.39, "数据日期": "2026-07-10", "更新时间": "2026-07-10T16:11:36+08:00"},
        {"代码": "513520", "名称": "日经ETF华夏", "最新价": 2.343, "成交量": 1907128, "成交额": 451333607, "开盘价": 2.373, "最高价": 2.384, "最低价": 2.343, "昨收": 2.345, "数据日期": "2026-07-10", "更新时间": "2026-07-10T16:11:41+08:00"},
    ], SPOT_COLUMNS)


class AkShareETFAdapterTests(unittest.TestCase):
    def test_history_normalizes_lots_to_shares_and_preserves_source(self):
        calls = []
        adapter = AkShareETFAdapter(history_loader=lambda **kwargs: calls.append(kwargs) or history_frame(), spot_loader=spot_frame, clock=lambda: FIXED_NOW)
        result = adapter.get_price_series(MarketSeriesRequest(code="513000.SH", limit=2))
        self.assertTrue(result.ok)
        self.assertEqual(result.data.code, "513000.SH")
        self.assertEqual(result.data.points[-1].volume, 114530800)
        self.assertEqual(result.data.points[-1].amount, 278431674)
        self.assertEqual(result.data.points[-1].trading_date, "2026-07-10")
        self.assertEqual(result.data.provenance.source, "AkShare/Eastmoney public web data")
        self.assertEqual(result.data.provenance.data_quality, "aggregated")
        self.assertEqual(calls[0]["symbol"], "513000")
        self.assertEqual(calls[0]["adjust"], "")

    def test_spot_uses_observation_time_not_fetch_time_and_normalizes_volume(self):
        calls = []
        adapter = AkShareETFAdapter(spot_loader=lambda: calls.append(True) or spot_frame(), history_loader=lambda **_kwargs: history_frame(), clock=lambda: FIXED_NOW)
        request = MarketQuoteRequest(codes=("513000.SH", "513520.SH"))
        first = adapter.get_quote_snapshots(request)
        second = adapter.get_quote_snapshots(request)
        self.assertTrue(first.ok)
        self.assertEqual(len(first.data), 2)
        self.assertEqual(first.data[0].as_of, int(datetime(2026, 7, 10, 16, 11, 36, tzinfo=SHANGHAI).timestamp()))
        self.assertNotEqual(first.data[0].as_of, FIXED_NOW)
        self.assertEqual(first.data[0].volume, 114530800)
        self.assertEqual(first.data[0].trading_date, "2026-07-10")
        self.assertEqual(first.data[0].provenance.delay_kind, "unknown")
        self.assertIs(first, second)
        self.assertEqual(len(calls), 1)

    def test_missing_requested_row_fails_closed(self):
        frame = FakeFrame([spot_frame().rows[0]], SPOT_COLUMNS)
        adapter = AkShareETFAdapter(spot_loader=lambda: frame, history_loader=lambda **_kwargs: history_frame(), clock=lambda: FIXED_NOW)
        result = adapter.get_quote_snapshots(MarketQuoteRequest(codes=("513000.SH", "513520.SH")))
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "empty")
        self.assertEqual(result.data, ())

    def test_invalid_route_and_series_shape_fail_before_fake_success(self):
        adapter = AkShareETFAdapter(spot_loader=spot_frame, history_loader=lambda **_kwargs: history_frame(), clock=lambda: FIXED_NOW)
        with self.assertRaises(MarketDataValidationError):
            adapter.get_price_series(MarketSeriesRequest(code="NIKKEI225.INDEX"))
        with self.assertRaises(MarketDataValidationError):
            adapter.get_price_series(MarketSeriesRequest(code="513000.SH", adjusted="forward"))
        bad = FakeFrame(history_frame().rows, ("日期", "开盘"))
        result = AkShareETFAdapter(spot_loader=spot_frame, history_loader=lambda **_kwargs: bad, clock=lambda: FIXED_NOW).get_price_series(MarketSeriesRequest(code="513000.SH"))
        self.assertFalse(result.ok)
        self.assertTrue(result.reason.startswith("upstream_schema_changed:akshare_etf_v1:"))

    def test_timeout_is_negative_cached(self):
        calls = []
        def timeout():
            calls.append(True)
            raise TimeoutError()
        adapter = AkShareETFAdapter(
            spot_loader=timeout,
            history_loader=lambda **_kwargs: history_frame(),
            retry_sleeper=lambda _seconds: None,
            clock=lambda: FIXED_NOW,
        )
        request = MarketQuoteRequest(codes=("513000.SH",))
        first = adapter.get_quote_snapshots(request)
        second = adapter.get_quote_snapshots(request)
        self.assertFalse(first.ok)
        self.assertEqual(first.reason, "upstream_timeout:akshare")
        self.assertIs(first, second)
        self.assertEqual(len(calls), 2)

    def test_connection_reset_retries_once_then_history_recovers(self):
        calls = []
        sleeps = []

        def history(**_kwargs):
            calls.append(True)
            if len(calls) == 1:
                raise ConnectionResetError("reset by peer")
            return history_frame()

        adapter = AkShareETFAdapter(
            spot_loader=spot_frame,
            history_loader=history,
            retry_sleeper=sleeps.append,
            clock=lambda: FIXED_NOW,
        )

        result = adapter.get_price_series(MarketSeriesRequest(code="513000.SH", limit=2))

        self.assertTrue(result.ok)
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [0.2])

    def test_empty_and_schema_failures_are_not_retried(self):
        empty_calls = []
        schema_calls = []

        empty_result = AkShareETFAdapter(
            spot_loader=lambda: empty_calls.append(True) or FakeFrame([], SPOT_COLUMNS, empty=True),
            history_loader=lambda **_kwargs: history_frame(),
            retry_sleeper=lambda _seconds: self.fail("empty data must not retry"),
            clock=lambda: FIXED_NOW,
        ).get_quote_snapshots(MarketQuoteRequest(codes=("513000.SH",)))

        bad = FakeFrame(history_frame().rows, ("日期", "开盘"))
        schema_result = AkShareETFAdapter(
            spot_loader=spot_frame,
            history_loader=lambda **_kwargs: schema_calls.append(True) or bad,
            retry_sleeper=lambda _seconds: self.fail("schema failures must not retry"),
            clock=lambda: FIXED_NOW,
        ).get_price_series(MarketSeriesRequest(code="513000.SH"))

        self.assertEqual(empty_result.status, "empty")
        self.assertEqual(len(empty_calls), 1)
        self.assertFalse(schema_result.ok)
        self.assertEqual(len(schema_calls), 1)


if __name__ == "__main__":
    unittest.main()
