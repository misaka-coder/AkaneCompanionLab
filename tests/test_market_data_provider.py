from __future__ import annotations

from datetime import datetime
from pathlib import Path
import socket
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from services.market_data import (
    MarketDataResponse,
    MarketDataValidationError,
    MarketNewsQuery,
    MarketQuoteRequest,
    MarketSeriesRequest,
    MockMarketDataProvider,
    normalize_choice_news_record,
    normalize_choice_quote_record,
    normalize_choice_series_record,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "choice_market_data_synthetic_v1.json"


class ChoiceMarketDataNormalizerTests(unittest.TestCase):
    def test_news_record_normalizes_choice_fields(self) -> None:
        event = normalize_choice_news_record(
            {
                "datetime": "20260710093000",
                "eitime": "2026-07-10T09:31:20+08:00",
                "code": "000000.test",
                "content": "synthetic body",
                "title": "Synthetic announcement",
                "infoCode": "NEWS-001",
                "medianname": "Synthetic Source",
                "url": "https://example.invalid/news-001",
                "type": "CompanyNews",
                "label": "positive,synthetic,positive",
            },
            received_at=1_752_109_500,
        )

        self.assertEqual(event.event_id, "choice:NEWS-001")
        self.assertEqual(event.code, "000000.TEST")
        self.assertEqual(event.content_type, "companynews")
        self.assertEqual(event.sentiment, "positive")
        self.assertEqual(event.labels, ("positive", "synthetic"))
        self.assertEqual(len(event.raw_hash), 64)

    def test_news_without_info_code_gets_stable_content_hash_id(self) -> None:
        record = {
            "datetime": "2026-07-10 09:30:00",
            "code": "000000.TEST",
            "content": "synthetic body",
            "title": "Synthetic announcement",
            "medianname": "Synthetic Source",
            "type": "companynews",
        }

        first = normalize_choice_news_record(record, received_at=1_752_109_500)
        second = normalize_choice_news_record(record, received_at=1_752_109_999)

        self.assertTrue(first.event_id.startswith("choice:"))
        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(first.raw_hash, second.raw_hash)

    def test_invalid_news_field_returns_structured_failure(self) -> None:
        with self.assertRaises(MarketDataValidationError) as raised:
            normalize_choice_news_record(
                {
                    "datetime": "not-a-date",
                    "code": "000000.TEST",
                    "title": "Synthetic announcement",
                    "type": "companynews",
                }
            )

        failure = raised.exception.to_public_dict()
        self.assertFalse(failure["ok"])
        self.assertEqual(failure["status"], "invalid_data")
        self.assertEqual(failure["field"], "datetime")
        self.assertIn("timestamp", failure["reason"])

    def test_quote_snapshot_calculates_change_from_last_and_previous_close(self) -> None:
        quote = normalize_choice_quote_record(
            {
                "TIME": "2026-07-10 10:00:00",
                "CODE": "000000.TEST",
                "PRECLOSE": 100,
                "OPEN": 100.5,
                "HIGH": 103,
                "LOW": 99.5,
                "NOW": 102,
                "VOLUME": 123,
                "AMOUNT": 456,
            }
        )

        self.assertEqual(quote.change, 2.0)
        self.assertEqual(quote.change_pct, 2.0)
        self.assertEqual(quote.status, "unknown")
        self.assertEqual(quote.timezone, "Asia/Shanghai")

    def test_invalid_quote_numeric_field_is_not_silently_dropped(self) -> None:
        with self.assertRaises(MarketDataValidationError) as raised:
            normalize_choice_quote_record(
                {
                    "TIME": "2026-07-10 10:00:00",
                    "CODE": "000000.TEST",
                    "NOW": "not-a-price",
                }
            )

        self.assertEqual(raised.exception.field, "now")
        self.assertEqual(raised.exception.code, "invalid_field")

    def test_series_normalizes_and_orders_bars(self) -> None:
        series = normalize_choice_series_record(
            {
                "code": "000000.TEST",
                "interval": "1d",
                "points": [
                    {
                        "datetime": "2026-07-10 15:00:00",
                        "open": 100,
                        "high": 103,
                        "low": 99,
                        "close": 102,
                    },
                    {
                        "datetime": "2026-07-09 15:00:00",
                        "open": 99,
                        "high": 101,
                        "low": 98,
                        "close": 100,
                    },
                ],
            }
        )

        self.assertEqual(len(series.points), 2)
        self.assertLess(series.points[0].timestamp, series.points[1].timestamp)
        self.assertEqual(series.as_of, series.points[-1].timestamp)

    def test_series_rejects_impossible_ohlc(self) -> None:
        with self.assertRaises(MarketDataValidationError) as raised:
            normalize_choice_series_record(
                {
                    "code": "000000.TEST",
                    "interval": "1d",
                    "points": [
                        {
                            "datetime": "2026-07-10 15:00:00",
                            "open": 100,
                            "high": 99,
                            "low": 98,
                            "close": 102,
                        }
                    ],
                }
            )

        self.assertEqual(raised.exception.field, "points[0].high")


class MockMarketDataProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = MockMarketDataProvider.from_fixture_path(FIXTURE_PATH)

    def test_mock_requires_explicit_synthetic_marker(self) -> None:
        with self.assertRaises(MarketDataValidationError) as raised:
            MockMarketDataProvider({"news": [], "quotes": [], "series": []})

        self.assertEqual(raised.exception.field, "synthetic")

        with self.assertRaises(MarketDataValidationError) as wrong_schema:
            MockMarketDataProvider(
                {
                    "schema_version": "unknown.fixture.v1",
                    "synthetic": True,
                    "news": [],
                    "quotes": [],
                    "series": [],
                }
            )
        self.assertEqual(wrong_schema.exception.field, "schema_version")

    def test_health_contract_is_ready_but_never_claims_choice_login(self) -> None:
        health = self.provider.health().to_public_dict()

        self.assertTrue(health["ok"])
        self.assertEqual(health["status"], "ready")
        self.assertEqual(health["provider"], "mock_choice")
        self.assertEqual(health["source"], "Synthetic Choice Fixture")
        self.assertFalse(health["logged_in"])
        self.assertTrue(health["quota_status"]["synthetic"])
        self.assertEqual(health["quota_status"]["network"], "disabled")

    def test_news_query_filters_choice_shape_fixture(self) -> None:
        date_from = int(datetime(2026, 7, 10, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
        response = self.provider.search_news(
            MarketNewsQuery(
                query="季度经营",
                codes=("000000.test",),
                content_types=("companynews",),
                date_from=date_from,
                limit=5,
            )
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.status, "ok")
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0].event_id, "mock_choice:SYNTH-NEWS-001")
        public = response.to_public_dict()
        self.assertEqual(public["provider"], "mock_choice")
        self.assertTrue(public["as_of"].endswith("+08:00"))
        self.assertEqual(public["data"][0]["provider"], "mock_choice")

    def test_quote_and_series_requests_return_normalized_data(self) -> None:
        quote_response = self.provider.get_quote_snapshots(MarketQuoteRequest(codes=("000000.TEST",)))
        series_response = self.provider.get_price_series(
            MarketSeriesRequest(code="000000.TEST", interval="1d", limit=2)
        )

        self.assertEqual(quote_response.status, "ok")
        self.assertEqual(quote_response.data[0].change_pct, 2.0)
        self.assertEqual(series_response.status, "ok")
        self.assertEqual(len(series_response.data.points), 2)
        self.assertEqual(series_response.data.points[-1].close, 102.0)

    def test_missing_data_returns_structured_empty_not_fake_success_data(self) -> None:
        quote_response = self.provider.get_quote_snapshots(MarketQuoteRequest(codes=("999999.TEST",)))
        series_response = self.provider.get_price_series(MarketSeriesRequest(code="999999.TEST"))

        self.assertTrue(quote_response.ok)
        self.assertEqual(quote_response.status, "empty")
        self.assertEqual(quote_response.data, ())
        self.assertEqual(series_response.status, "empty")
        self.assertIsNone(series_response.data)

    def test_invalid_request_filters_fail_structurally_instead_of_broadening(self) -> None:
        with self.assertRaises(MarketDataValidationError) as raised:
            MarketNewsQuery(date_from=200, date_to=100)

        failure = raised.exception.to_public_dict()
        self.assertEqual(failure["status"], "invalid_arguments")
        self.assertEqual(failure["field"], "date_from")

        with self.assertRaises(MarketDataValidationError) as invalid_code:
            MarketQuoteRequest(codes=("not a code",))
        self.assertEqual(invalid_code.exception.status, "invalid_arguments")

    def test_response_contract_rejects_contradictory_ok_state(self) -> None:
        with self.assertRaises(MarketDataValidationError) as raised:
            MarketDataResponse(
                ok=True,
                status="permission_denied",
                provider="mock_choice",
                source="Synthetic Choice Fixture",
                as_of=None,
                reason="denied",
                data=None,
            )

        self.assertEqual(raised.exception.field, "ok")

    def test_mock_provider_never_needs_network(self) -> None:
        with patch.object(socket, "create_connection", side_effect=AssertionError("network access attempted")):
            health = self.provider.health()
            news = self.provider.search_news(MarketNewsQuery(limit=1))
            quote = self.provider.get_quote_snapshots(MarketQuoteRequest(codes=("000000.TEST",)))

        self.assertTrue(health.ok)
        self.assertEqual(news.status, "ok")
        self.assertEqual(quote.status, "ok")


if __name__ == "__main__":
    unittest.main()
