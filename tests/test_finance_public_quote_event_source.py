from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from companion_v01.finance import FinancePublicQuoteEventSource
from services.market_data import (
    MarketDataProvenance,
    MarketDataResponse,
    MarketEventStore,
    MarketQuoteSnapshot,
    PublicMarketProvider,
)


NOW = int(datetime(2026, 7, 10, 14, 30, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())


class SequenceQuoteAdapter:
    def __init__(self, snapshots: list[MarketQuoteSnapshot], *, source: str) -> None:
        self.snapshots = list(snapshots)
        self.source = source
        self.calls = 0

    def get_quote_snapshots(self, request):
        index = min(self.calls, len(self.snapshots) - 1)
        self.calls += 1
        snapshot = self.snapshots[index]
        return MarketDataResponse(
            ok=True,
            status="ok",
            provider="public_market",
            source=self.source,
            as_of=snapshot.as_of,
            reason="",
            data=(snapshot,),
            timezone=snapshot.timezone,
        )

    def get_price_series(self, request):
        raise AssertionError("event source should request quote snapshots only")

    def search_quotes(self, query, *, max_results=10):
        return ()


def _akshare_snapshot(*, fetched_at: int, as_of: int, last: float, previous_close: float = 1.0):
    change = last - previous_close
    return MarketQuoteSnapshot(
        provider="public_market",
        code="513000.SH",
        as_of=as_of,
        timezone="Asia/Shanghai",
        previous_close=previous_close,
        open=1.0,
        high=max(1.0, last),
        low=min(1.0, last),
        last=last,
        volume=1000,
        amount=10000,
        change=change,
        change_pct=change / previous_close * 100.0,
        status="public_web_snapshot",
        provenance=MarketDataProvenance(
            source="AkShare/Eastmoney public web data",
            vendor_symbol="513000",
            fetched_at=fetched_at,
            exchange_timezone="Asia/Shanghai",
            currency="CNY",
            session="snapshot",
            delay_kind="unknown",
            delay_seconds=None,
            data_quality="aggregated",
            adapter_version="test_akshare_v1",
        ),
        trading_date="2026-07-10",
        time_semantics="instant",
    )


def _yahoo_snapshot(*, fetched_at: int, trading_date: str, last: float, previous_close: float):
    as_of = int(datetime.fromisoformat(trading_date).replace(tzinfo=ZoneInfo("Asia/Tokyo")).timestamp())
    change = last - previous_close
    return MarketQuoteSnapshot(
        provider="public_market",
        code="NIKKEI225.INDEX",
        as_of=as_of,
        timezone="Asia/Tokyo",
        previous_close=previous_close,
        open=previous_close,
        high=max(previous_close, last),
        low=min(previous_close, last),
        last=last,
        volume=0,
        amount=None,
        change=change,
        change_pct=change / previous_close * 100.0,
        status="end_of_day",
        provenance=MarketDataProvenance(
            source="Yahoo Finance",
            vendor_symbol="^N225",
            fetched_at=fetched_at,
            exchange_timezone="Asia/Tokyo",
            currency="JPY",
            session="daily",
            delay_kind="end_of_day",
            delay_seconds=None,
            data_quality="public_web",
            adapter_version="test_yahoo_v1",
        ),
        trading_date=trading_date,
        time_semantics="trading_date",
    )


class FinancePublicQuoteEventSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = MarketEventStore(Path(self.temp_dir.name) / "market.sqlite3", clock=lambda: NOW)
        subscription = self.store.upsert_subscription(
            subscription_id="public-push",
            client="qq",
            target_id="872732158",
            is_group=True,
            session_id="qq_group_shared_872732158",
            profile_user_id="qq_group_shared_872732158",
            finance_mode="push",
            enabled=True,
            delivery_policy={"level": "notify"},
            now_ts=NOW,
        )
        self.store.upsert_watchlist_item(
            subscription_id=subscription.subscription_id,
            provider="public_market",
            code="513000.SH",
            display_name="日经225ETF易方达",
            priority=0.7,
            now_ts=NOW,
        )

    def _source(self, snapshots: list[MarketQuoteSnapshot]) -> FinancePublicQuoteEventSource:
        adapter = SequenceQuoteAdapter(snapshots, source="AkShare")
        provider = PublicMarketProvider(
            yahoo_enabled=False,
            akshare=adapter,
            dependency_probe=lambda _name: True,
        )
        return FinancePublicQuoteEventSource(provider=provider, store=self.store, clock=lambda: NOW)

    def test_first_observations_only_build_confirmed_baseline_then_new_bucket_emits(self) -> None:
        source = self._source(
            [
                _akshare_snapshot(fetched_at=NOW - 40, as_of=NOW - 45, last=1.005),
                _akshare_snapshot(fetched_at=NOW - 30, as_of=NOW - 35, last=1.006),
                _akshare_snapshot(fetched_at=NOW - 20, as_of=NOW - 25, last=1.012),
                _akshare_snapshot(fetched_at=NOW - 10, as_of=NOW - 15, last=1.013),
            ]
        )

        first = source.poll_market_events()
        second = source.poll_market_events()
        third = source.poll_market_events()
        fourth = source.poll_market_events()

        self.assertEqual(first.events, ())
        self.assertEqual(second.events, ())
        self.assertEqual(third.events, ())
        self.assertEqual(len(fourth.events), 1)
        event = fourth.events[0]
        self.assertEqual(event.content_type, "quote_move")
        self.assertIn("513000.SH", event.title)
        self.assertIn("1.30%", event.title)
        self.assertIn("validated_quote", event.labels)
        baseline = self.store.get_quote_baseline(provider="public_market", code="513000.SH")
        self.assertEqual(baseline.last_event_key, "513000.SH|quote_move|2026-07-10|up|ge_1pct")

    def test_dirty_change_is_rejected_and_never_becomes_baseline(self) -> None:
        dirty = _akshare_snapshot(fetched_at=NOW - 10, as_of=NOW - 15, last=1.02)
        dirty = MarketQuoteSnapshot(
            **{
                **dirty.__dict__,
                "change": 99.0,
                "change_pct": 999.0,
            }
        )
        source = self._source([dirty])

        result = source.poll_market_events()

        self.assertEqual(result.events, ())
        self.assertIsNone(self.store.get_quote_baseline(provider="public_market", code="513000.SH"))
        rejections = self.store.list_market_data_rejections(provider="public_market", code="513000.SH")
        self.assertEqual(rejections[0].stage, "quality_gate")
        self.assertIn("inconsistent_change", rejections[0].reason)

    def test_stale_intraday_snapshot_is_rejected(self) -> None:
        source = self._source([_akshare_snapshot(fetched_at=NOW - 10, as_of=NOW - 60 * 60, last=1.02)])

        result = source.poll_market_events()

        self.assertEqual(result.events, ())
        rejection = self.store.list_market_data_rejections(provider="public_market")[0]
        self.assertEqual(rejection.reason, "stale_instant_snapshot")

    def test_fresh_after_hours_snapshot_is_not_called_intraday(self) -> None:
        after_hours = int(datetime(2026, 7, 10, 16, 5, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
        source = self._source([_akshare_snapshot(fetched_at=after_hours, as_of=after_hours - 5, last=1.02)])
        source._clock = lambda: after_hours

        result = source.poll_market_events()

        self.assertEqual(result.events, ())
        rejection = self.store.list_market_data_rejections(provider="public_market")[0]
        self.assertEqual(rejection.reason, "outside_supported_intraday_session")

    def test_yahoo_route_only_emits_confirmed_completed_daily_close(self) -> None:
        self.store.set_subscription_enabled("public-push", enabled=False, now_ts=NOW)
        subscription = self.store.upsert_subscription(
            subscription_id="public-yahoo-push",
            client="qq",
            target_id="872732158",
            is_group=True,
            session_id="qq_group_shared_872732158",
            profile_user_id="qq_group_shared_872732158",
            finance_mode="push",
            enabled=True,
            delivery_policy={"level": "notify"},
            now_ts=NOW,
        )
        self.store.upsert_watchlist_item(
            subscription_id=subscription.subscription_id,
            provider="public_market",
            code="NIKKEI225.INDEX",
            display_name="日经225",
            priority=0.7,
            now_ts=NOW,
        )
        adapter = SequenceQuoteAdapter(
            [
                _yahoo_snapshot(fetched_at=NOW - 40, trading_date="2026-07-08", last=50000, previous_close=49500),
                _yahoo_snapshot(fetched_at=NOW - 30, trading_date="2026-07-08", last=50000, previous_close=49500),
                _yahoo_snapshot(fetched_at=NOW - 20, trading_date="2026-07-09", last=51000, previous_close=50000),
                _yahoo_snapshot(fetched_at=NOW - 10, trading_date="2026-07-09", last=51000, previous_close=50000),
            ],
            source="Yahoo Finance",
        )
        provider = PublicMarketProvider(
            yahoo=adapter,
            akshare_enabled=False,
            dependency_probe=lambda _name: True,
        )
        source = FinancePublicQuoteEventSource(provider=provider, store=self.store, clock=lambda: NOW)

        results = [source.poll_market_events() for _ in range(4)]

        self.assertEqual([len(result.events) for result in results], [0, 0, 0, 1])
        event = results[-1].events[0]
        self.assertEqual(event.content_type, "daily_close")
        self.assertIn("最近完成交易日收盘", event.title)
        self.assertNotIn("盘中", event.title)
        self.assertIn("completed_daily_bar", event.labels)


if __name__ == "__main__":
    unittest.main()
