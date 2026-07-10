from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from services.market_data import (
    MARKET_STORE_SCHEMA_VERSION,
    MarketDataValidationError,
    MarketEvent,
    MarketEventStore,
    MarketNewsQuery,
    MockMarketDataProvider,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "choice_market_data_synthetic_v1.json"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class MarketEventStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "market_events.sqlite3"
        self.store = MarketEventStore(self.db_path, clock=lambda: 1_752_110_000)
        provider = MockMarketDataProvider.from_fixture_path(FIXTURE_PATH)
        events = provider.search_news(MarketNewsQuery(limit=10)).data
        self.event = next(item for item in events if item.event_id.endswith("SYNTH-NEWS-001"))
        self.older_event = next(item for item in events if item.event_id.endswith("SYNTH-NEWS-002"))

    def _subscription(self, subscription_id: str, target_id: str, *, filters=None, now_ts: int = 100):
        return self.store.upsert_subscription(
            subscription_id=subscription_id,
            client="qq",
            target_id=target_id,
            session_id=f"qq_group_{target_id}",
            profile_user_id=f"qq_group_{target_id}",
            character_pack_id="akane_default",
            finance_mode="push",
            enabled=True,
            filters=filters or {},
            delivery_policy={"level": "notify"},
            created_by_actor_id="qq:10001",
            now_ts=now_ts,
        )

    def test_schema_is_independent_and_has_required_indexes(self) -> None:
        self.assertEqual(self.store.schema_version(), MARKET_STORE_SCHEMA_VERSION)
        self.assertTrue(self.db_path.exists())

        with closing(sqlite3.connect(self.db_path)) as connection:
            tables = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
            indexes = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
            }

        self.assertTrue(
            {
                "market_events",
                "finance_subscriptions",
                "watchlist_items",
                "market_event_deliveries",
            }.issubset(tables)
        )
        self.assertIn("idx_market_events_raw_hash_unique", indexes)
        self.assertIn("idx_market_event_deliveries_status", indexes)
        self.assertNotIn("chat_messages", tables)

    def test_exact_event_replay_is_idempotent(self) -> None:
        first = self.store.upsert_event(self.event, now_ts=100)
        replay = self.store.upsert_event(self.event, now_ts=200)

        self.assertTrue(first.inserted)
        self.assertTrue(replay.duplicate)
        self.assertEqual(replay.status, "duplicate_event_id")
        self.assertEqual(replay.canonical_event_id, self.event.event_id)
        self.assertEqual(replay.record.created_at, 100)
        self.assertEqual(replay.record.updated_at, 100)
        self.assertEqual(replay.record.revision, 1)

    def test_raw_hash_deduplicates_different_provider_event_id(self) -> None:
        first = self.store.upsert_event(self.event, now_ts=100)
        alias_event = replace(self.event, event_id="mock_choice:ALIAS-001")

        duplicate = self.store.upsert_event(alias_event, now_ts=200)

        self.assertTrue(first.inserted)
        self.assertEqual(duplicate.status, "duplicate_raw_hash")
        self.assertEqual(duplicate.canonical_event_id, self.event.event_id)
        self.assertIsNone(self.store.get_event(alias_event.event_id))

    def test_same_event_id_with_new_content_updates_revision(self) -> None:
        first = self.store.upsert_event(self.event, now_ts=100)
        updated_event = replace(
            self.event,
            title=f"{self.event.title}（更新）",
            raw_hash=_hash("synthetic-news-001-revision-2"),
            received_at=self.event.received_at + 60,
        )

        updated = self.store.upsert_event(updated_event, now_ts=200)

        self.assertTrue(first.inserted)
        self.assertTrue(updated.updated)
        self.assertEqual(updated.record.revision, 2)
        self.assertEqual(updated.record.created_at, 100)
        self.assertEqual(updated.record.updated_at, 200)
        self.assertEqual(updated.record.event.title, updated_event.title)
        self.assertEqual(updated.record.cluster_id, first.record.cluster_id)

    def test_similar_nearby_titles_share_cluster_but_other_types_do_not(self) -> None:
        first = self.store.upsert_event(self.event, now_ts=100)
        related = replace(
            self.event,
            event_id="mock_choice:SYNTH-NEWS-001-FOLLOWUP",
            title=f"{self.event.title} 更新",
            published_at=self.event.published_at + 600,
            raw_hash=_hash("synthetic-news-001-followup"),
        )
        unrelated_type = replace(
            related,
            event_id="mock_choice:SYNTH-REPORT-001",
            content_type="regularreport",
            raw_hash=_hash("synthetic-report-001"),
        )

        related_result = self.store.upsert_event(related, now_ts=200)
        unrelated_result = self.store.upsert_event(unrelated_type, now_ts=300)

        self.assertEqual(related_result.record.cluster_id, first.record.cluster_id)
        self.assertNotEqual(unrelated_result.record.cluster_id, first.record.cluster_id)

    def test_event_query_filters_are_strict_and_do_not_broaden(self) -> None:
        self.store.upsert_event(self.event, now_ts=100)
        self.store.upsert_event(self.older_event, now_ts=101)

        results = self.store.list_events(
            query="季度经营",
            codes=("000000.test",),
            content_types=("companynews",),
            date_from=self.event.published_at - 60,
            date_to=self.event.published_at + 60,
        )

        self.assertEqual([item.event.event_id for item in results], [self.event.event_id])
        with self.assertRaises(MarketDataValidationError) as raised:
            self.store.list_events(codes=("not a code",))
        self.assertEqual(raised.exception.status, "invalid_arguments")

    def test_empty_subscription_matches_nothing_until_watchlist_exists(self) -> None:
        self._subscription("sub-empty", "20001")
        self.store.upsert_event(self.event, now_ts=100)

        self.assertEqual(self.store.match_subscriptions(self.event), ())
        self.assertEqual(self.store.ensure_matching_deliveries(self.event.event_id, now_ts=105), ())

        item = self.store.upsert_watchlist_item(
            subscription_id="sub-empty",
            code=self.event.code,
            display_name="合成测试标的",
            aliases=("测试标的", "TEST"),
            priority=0.8,
            now_ts=110,
        )
        matches = self.store.match_subscriptions(self.event)
        reservations = self.store.ensure_matching_deliveries(self.event.event_id, now_ts=120)

        self.assertEqual(item.code, "000000.TEST")
        self.assertEqual([item.subscription_id for item in matches], ["sub-empty"])
        self.assertEqual(len(reservations), 1)
        self.assertTrue(reservations[0].created)
        self.assertTrue(reservations[0].should_deliver)

    def test_subscription_filters_and_disabled_state_fail_closed(self) -> None:
        self._subscription(
            "sub-filter",
            "20001",
            filters={
                "codes": [self.event.code],
                "content_types": ["companynews"],
                "providers": ["mock_choice"],
            },
        )
        self.assertEqual(
            [item.subscription_id for item in self.store.match_subscriptions(self.event)],
            ["sub-filter"],
        )
        self.assertEqual(self.store.match_subscriptions(self.older_event), ())

        disabled = self.store.set_subscription_enabled("sub-filter", enabled=False, now_ts=200)
        self.assertIsNotNone(disabled)
        self.assertFalse(disabled.enabled)
        self.assertEqual(self.store.match_subscriptions(self.event), ())

        with self.assertRaises(MarketDataValidationError) as raised:
            self._subscription("sub-bad", "20002", filters={"unknown_filter": ["x"]})
        self.assertEqual(raised.exception.field, "filters")

    def test_subscription_owner_fields_cannot_be_reassigned(self) -> None:
        original = self._subscription("sub-owner", "20001", now_ts=100)

        updated = self.store.upsert_subscription(
            subscription_id="sub-owner",
            client="qq",
            target_id=original.target_id,
            session_id=original.session_id,
            profile_user_id=original.profile_user_id,
            character_pack_id="akane_default",
            finance_mode="push",
            enabled=True,
            filters={"codes": [self.event.code]},
            created_by_actor_id="qq:99999",
            now_ts=150,
        )

        with self.assertRaises(MarketDataValidationError) as raised:
            self.store.upsert_subscription(
                subscription_id="sub-owner",
                client="qq",
                target_id="99999",
                session_id=original.session_id,
                profile_user_id=original.profile_user_id,
                finance_mode="push",
                enabled=True,
                now_ts=200,
            )

        self.assertEqual(updated.created_by_actor_id, "qq:10001")
        self.assertEqual(raised.exception.field, "target_id")
        self.assertEqual(self.store.get_subscription("sub-owner").target_id, "20001")

    def test_watchlist_creator_attribution_is_immutable(self) -> None:
        self._subscription("sub-watch-owner", "20001")
        original = self.store.upsert_watchlist_item(
            subscription_id="sub-watch-owner",
            code=self.event.code,
            display_name="初始名称",
            created_by_actor_id="qq:10001",
            now_ts=100,
        )
        updated = self.store.upsert_watchlist_item(
            subscription_id="sub-watch-owner",
            code=self.event.code,
            display_name="更新名称",
            created_by_actor_id="qq:99999",
            now_ts=200,
        )

        self.assertEqual(original.created_by_actor_id, "qq:10001")
        self.assertEqual(updated.created_by_actor_id, "qq:10001")
        self.assertEqual(updated.display_name, "更新名称")

    def test_delivery_state_is_isolated_per_subscription(self) -> None:
        self.store.upsert_event(self.event, now_ts=100)
        self._subscription("sub-group-a", "20001")
        self._subscription("sub-group-b", "20002")

        first = self.store.ensure_delivery(
            event_id=self.event.event_id,
            subscription_id="sub-group-a",
            now_ts=200,
        )
        second = self.store.ensure_delivery(
            event_id=self.event.event_id,
            subscription_id="sub-group-b",
            now_ts=201,
        )
        attempt = self.store.record_delivery_attempt(
            event_id=self.event.event_id,
            subscription_id="sub-group-a",
            now_ts=210,
        )
        delivered = self.store.mark_delivery_delivered(
            event_id=self.event.event_id,
            subscription_id="sub-group-a",
            analysis_id="analysis-a",
            now_ts=220,
        )

        self.assertTrue(first.created)
        self.assertTrue(second.created)
        self.assertEqual(attempt.status, "processing")
        self.assertEqual(delivered.status, "delivered")
        self.assertEqual(delivered.analysis_id, "analysis-a")
        group_b = self.store.get_delivery(
            event_id=self.event.event_id,
            subscription_id="sub-group-b",
        )
        self.assertEqual(group_b.status, "pending")
        self.assertEqual(group_b.attempt_count, 0)

    def test_restart_keeps_event_and_delivery_idempotency(self) -> None:
        self.store.upsert_event(self.event, now_ts=100)
        self._subscription("sub-restart", "20001")
        self.store.ensure_delivery(
            event_id=self.event.event_id,
            subscription_id="sub-restart",
            now_ts=200,
        )
        self.store.record_delivery_attempt(
            event_id=self.event.event_id,
            subscription_id="sub-restart",
            now_ts=210,
        )
        self.store.mark_delivery_delivered(
            event_id=self.event.event_id,
            subscription_id="sub-restart",
            now_ts=220,
        )

        restarted = MarketEventStore(self.db_path, clock=lambda: 1_752_111_000)
        replay = restarted.upsert_event(self.event, now_ts=300)
        reservation = restarted.ensure_delivery(
            event_id=self.event.event_id,
            subscription_id="sub-restart",
            now_ts=310,
        )

        self.assertEqual(replay.status, "duplicate_event_id")
        self.assertFalse(reservation.created)
        self.assertFalse(reservation.should_deliver)
        self.assertEqual(reservation.delivery.status, "delivered")
        self.assertEqual(reservation.delivery.attempt_count, 1)

    def test_failed_delivery_is_retryable_and_attempt_count_persists(self) -> None:
        self.store.upsert_event(self.event, now_ts=100)
        self._subscription("sub-retry", "20001")
        self.store.ensure_delivery(
            event_id=self.event.event_id,
            subscription_id="sub-retry",
            now_ts=200,
        )
        first_attempt = self.store.record_delivery_attempt(
            event_id=self.event.event_id,
            subscription_id="sub-retry",
            now_ts=210,
        )
        failed = self.store.mark_delivery_failed(
            event_id=self.event.event_id,
            subscription_id="sub-retry",
            reason="qq_unreachable",
            now_ts=220,
        )
        retry = self.store.ensure_delivery(
            event_id=self.event.event_id,
            subscription_id="sub-retry",
            now_ts=230,
        )
        second_attempt = self.store.record_delivery_attempt(
            event_id=self.event.event_id,
            subscription_id="sub-retry",
            now_ts=240,
        )

        self.assertEqual(first_attempt.attempt_count, 1)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.reason, "qq_unreachable")
        self.assertTrue(retry.should_deliver)
        self.assertEqual(second_attempt.attempt_count, 2)
        self.assertEqual(second_attempt.status, "processing")

    def test_disabling_subscription_cancels_open_deliveries(self) -> None:
        self.store.upsert_event(self.event, now_ts=100)
        self._subscription("sub-disable", "20001")
        self.store.ensure_delivery(
            event_id=self.event.event_id,
            subscription_id="sub-disable",
            now_ts=200,
        )

        self.store.set_subscription_enabled("sub-disable", enabled=False, now_ts=210)
        delivery = self.store.get_delivery(
            event_id=self.event.event_id,
            subscription_id="sub-disable",
        )
        reservation = self.store.ensure_delivery(
            event_id=self.event.event_id,
            subscription_id="sub-disable",
            now_ts=220,
        )

        self.assertEqual(delivery.status, "cancelled")
        self.assertEqual(delivery.reason, "subscription_disabled")
        self.assertFalse(reservation.should_deliver)
        self.assertEqual(self.store.list_retryable_deliveries(), ())


if __name__ == "__main__":
    unittest.main()
