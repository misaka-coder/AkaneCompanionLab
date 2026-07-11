from __future__ import annotations

from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from services.market_data import EastmoneyFastNewsAdapter


NOW = int(datetime(2026, 7, 11, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())


def _payload(*, title: str = "日经225指数涨幅扩大", code: str = "202607111234567890"):
    return {
        "data": {
            "fastNewsList": [
                {
                    "title": title,
                    "summary": "东方财富快讯摘要",
                    "showTime": "2026-07-11 17:59:30",
                    "code": code,
                }
            ]
        }
    }


class EastmoneyFastNewsAdapterTests(unittest.TestCase):
    def test_normalizes_public_fast_news_with_source_time_and_link(self) -> None:
        calls = []

        def loader(**kwargs):
            calls.append(kwargs)
            return _payload()

        result = EastmoneyFastNewsAdapter(loader=loader, clock=lambda: NOW).fetch_latest(limit=10)

        self.assertTrue(result.ok)
        self.assertEqual(len(result.items), 1)
        item = result.items[0]
        self.assertEqual(item.source, "东方财富 7×24 全球财经快讯")
        self.assertEqual(item.title, "日经225指数涨幅扩大")
        self.assertEqual(
            item.published_at, int(datetime(2026, 7, 11, 17, 59, 30, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
        )
        self.assertEqual(item.url, "https://finance.eastmoney.com/a/202607111234567890.html")
        self.assertIn("needs_official_verification", item.labels)
        self.assertEqual(calls[0]["timeout_seconds"], 6.0)

    def test_timeout_retries_and_circuit_breaker_fail_closed(self) -> None:
        calls = []

        def timeout(**_kwargs):
            calls.append(True)
            raise TimeoutError("synthetic")

        adapter = EastmoneyFastNewsAdapter(
            loader=timeout,
            retry_max_attempts=2,
            circuit_failure_threshold=2,
            retry_sleeper=lambda _seconds: None,
            clock=lambda: NOW,
        )

        first = adapter.fetch_latest()
        second = adapter.fetch_latest()
        third = adapter.fetch_latest()

        self.assertFalse(first.ok)
        self.assertFalse(second.ok)
        self.assertEqual(third.status, "circuit_open")
        self.assertEqual(len(calls), 4)

    def test_schema_change_is_structured_and_never_returns_fake_items(self) -> None:
        result = EastmoneyFastNewsAdapter(loader=lambda **_kwargs: {"data": {}}, clock=lambda: NOW).fetch_latest()

        self.assertFalse(result.ok)
        self.assertEqual(result.items, ())
        self.assertIn("upstream_schema_changed", result.reason)


if __name__ == "__main__":
    unittest.main()
