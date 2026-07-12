from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from companion_v01.finance import (
    FinanceCompositeEventSource,
    FinanceNewsModerationDecision,
    FinancePublicNewsEventSource,
)
from services.market_data import (
    MarketEventPollResult,
    MarketEventStore,
    PublicNewsFetchResult,
    PublicNewsItem,
)


NOW = int(datetime(2026, 7, 11, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())


def _item(item_id: str, title: str, *, published_at: int = NOW - 5, summary: str = "快讯摘要"):
    return PublicNewsItem(
        adapter_id="eastmoney_fast_news",
        item_id=item_id,
        title=title,
        summary=summary,
        published_at=published_at,
        fetched_at=NOW,
        source="东方财富 7×24 全球财经快讯",
        url=f"https://finance.eastmoney.com/a/{item_id}.html",
        labels=("eastmoney_fast_news", "source_report_only", "needs_official_verification"),
    )


class SequenceNewsAdapter:
    adapter_id = "eastmoney_fast_news"
    source_name = "东方财富 7×24 全球财经快讯"

    def __init__(self, batches):
        self.batches = list(batches)
        self.calls = 0

    def fetch_latest(self, *, limit=100):
        index = min(self.calls, len(self.batches) - 1)
        self.calls += 1
        value = self.batches[index]
        if isinstance(value, PublicNewsFetchResult):
            return value
        return PublicNewsFetchResult(
            ok=True,
            status="ok",
            adapter_id=self.adapter_id,
            source=self.source_name,
            items=tuple(value)[:limit],
        )


class FakeModerator:
    def __init__(self, *, blocked_ids=()):
        self.blocked_ids = set(blocked_ids)
        self.calls = []

    def moderate_items(self, items):
        self.calls.append(tuple(item.item_id for item in items))
        return {
            item.item_id: FinanceNewsModerationDecision(
                item.item_id not in self.blocked_ids,
                "allowed_foreign_or_finance" if item.item_id not in self.blocked_ids else "domestic_sensitive",
                0.95,
            )
            for item in items
        }


class RetryThenAllowModerator:
    def __init__(self):
        self.calls = []

    def moderate_items(self, items):
        self.calls.append(tuple(item.item_id for item in items))
        retryable = len(self.calls) == 1
        return {
            item.item_id: FinanceNewsModerationDecision(
                not retryable,
                "moderation_unavailable" if retryable else "ordinary_finance_news",
                0.0 if retryable else 0.98,
                retryable,
            )
            for item in items
        }


class AlwaysRetryableModerator:
    def __init__(self):
        self.calls = []

    def moderate_items(self, items):
        self.calls.append(tuple(item.item_id for item in items))
        return {
            item.item_id: FinanceNewsModerationDecision(
                False,
                "moderation_unavailable:TimeoutError",
                0.0,
                True,
            )
            for item in items
        }


class FinancePublicNewsEventSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = MarketEventStore(Path(self.temp_dir.name) / "news.sqlite3", clock=lambda: NOW)
        self.store.upsert_subscription(
            subscription_id="public-news-push",
            client="qq",
            target_id="872732158",
            is_group=True,
            session_id="qq_group_shared_872732158",
            profile_user_id="qq_group_shared_872732158",
            finance_mode="push",
            enabled=True,
            filters={"include_market_wide": True},
            delivery_policy={"level": "notify"},
            now_ts=NOW,
        )

    def _source(self, adapter, moderator, clock):
        return FinancePublicNewsEventSource(
            store=self.store,
            adapters=(adapter,),
            moderator=moderator,
            require_llm_moderation=True,
            minimum_poll_interval_seconds=5,
            clock=clock,
        )

    def test_first_poll_seeds_baseline_then_new_foreign_news_relays_original(self) -> None:
        clock = [NOW]
        old = _item("old", "美股盘前动态")
        new = _item("new", "特朗普表示将公布新的经济政策")
        adapter = SequenceNewsAdapter(((old,), (new, old)))
        moderator = FakeModerator()
        source = self._source(adapter, moderator, lambda: clock[0])

        first = source.poll_market_events()
        clock[0] += 10
        second = source.poll_market_events()

        self.assertEqual(first.events, ())
        self.assertEqual(len(second.events), 1)
        event = second.events[0]
        self.assertEqual(event.code, "GLOBAL.MARKET")
        self.assertEqual(event.content_type, "news_flash")
        self.assertIn("特朗普", event.title)
        self.assertIn("direct_relay", event.labels)
        self.assertIn("market_wide", event.labels)
        self.assertEqual(moderator.calls, [("new",)])

    def test_domestic_sensitive_terms_block_before_llm(self) -> None:
        clock = [NOW]
        adapter = SequenceNewsAdapter(
            ((_item("old", "普通财经快讯"),), (_item("sensitive", "中共中央政治局召开会议"),))
        )
        moderator = FakeModerator()
        source = self._source(adapter, moderator, lambda: clock[0])

        source.poll_market_events()
        clock[0] += 10
        result = source.poll_market_events()

        self.assertEqual(result.events, ())
        self.assertEqual(moderator.calls, [])
        rejection = self.store.list_market_data_rejections(provider="public_market")[0]
        self.assertEqual(rejection.stage, "news_policy")
        self.assertIn("blocked_domestic_political_term", rejection.reason)

    def test_cadre_term_blocks_before_llm(self) -> None:
        clock = [NOW]
        adapter = SequenceNewsAdapter(((_item("old", "普通财经快讯"),), (_item("cadre", "某地发布干部任免消息"),)))
        moderator = FakeModerator()
        source = self._source(adapter, moderator, lambda: clock[0])

        source.poll_market_events()
        clock[0] += 10
        result = source.poll_market_events()

        self.assertEqual(result.events, ())
        self.assertEqual(moderator.calls, [])
        rejection = self.store.list_market_data_rejections(provider="public_market")[0]
        self.assertEqual(rejection.stage, "news_policy")
        self.assertIn("blocked_domestic_political_term:干部", rejection.reason)

    def test_missing_or_blocking_llm_decision_fails_closed(self) -> None:
        clock = [NOW]
        adapter = SequenceNewsAdapter(((_item("old", "普通财经快讯"),), (_item("review", "某重要领导活动消息"),)))
        moderator = FakeModerator(blocked_ids=("review",))
        source = self._source(adapter, moderator, lambda: clock[0])

        source.poll_market_events()
        clock[0] += 10
        result = source.poll_market_events()

        self.assertEqual(result.events, ())
        rejection = self.store.list_market_data_rejections(provider="public_market")[0]
        self.assertEqual(rejection.stage, "news_moderation")
        self.assertIn("domestic_sensitive", rejection.reason)

    def test_watchlist_alias_maps_news_to_security_code(self) -> None:
        self.store.upsert_security(
            provider="public_market",
            code="002594.SZ",
            display_name="比亚迪",
            aliases=("比亚迪A股",),
            source="test master",
            as_of=NOW,
            now_ts=NOW,
        )
        clock = [NOW]
        adapter = SequenceNewsAdapter(((_item("old", "普通财经快讯"),), (_item("byd", "比亚迪发布最新经营数据"),)))
        source = self._source(adapter, FakeModerator(), lambda: clock[0])

        source.poll_market_events()
        clock[0] += 10
        result = source.poll_market_events()

        self.assertEqual(result.events[0].code, "002594.SZ")
        self.assertIn("security_matched", result.events[0].labels)

    def test_long_news_summary_does_not_break_security_resolution(self) -> None:
        clock = [NOW]
        long_summary = "企业披露经营进展。" * 80
        new = _item("long", "海外企业发布经营更新", summary=long_summary)
        adapter = SequenceNewsAdapter(((_item("old", "普通财经快讯"),), (new,)))
        source = self._source(adapter, FakeModerator(), lambda: clock[0])

        source.poll_market_events()
        clock[0] += 10
        result = source.poll_market_events()

        self.assertTrue(result.ok)
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0].code, "GLOBAL.MARKET")

    def test_retryable_moderation_failure_is_not_marked_seen_and_recovers_next_poll(self) -> None:
        clock = [NOW]
        new = _item("retry", "长江存储公布IPO辅导团队")
        adapter = SequenceNewsAdapter(((_item("old", "普通财经快讯"),), (new,), (new,)))
        moderator = RetryThenAllowModerator()
        source = self._source(adapter, moderator, lambda: clock[0])

        source.poll_market_events()
        clock[0] += 10
        failed = source.poll_market_events()
        pending_state = self.store.get_event_source_state("public_news:eastmoney_fast_news")
        clock[0] += 10
        recovered = source.poll_market_events()
        final_state = self.store.get_event_source_state("public_news:eastmoney_fast_news")

        self.assertEqual(failed.events, ())
        self.assertNotIn("retry", dict(pending_state.state)["seen_item_ids"])
        self.assertEqual(dict(pending_state.state)["moderation_attempts"], {"retry": 1})
        self.assertEqual(len(recovered.events), 1)
        self.assertIn("retry", dict(final_state.state)["seen_item_ids"])
        self.assertEqual(dict(final_state.state)["moderation_attempts"], {})
        self.assertEqual(moderator.calls, [("retry",), ("retry",)])

    def test_retryable_moderation_failure_is_finalized_only_after_defer_limit(self) -> None:
        clock = [NOW]
        new = _item("exhaust", "企业公布新的融资安排")
        adapter = SequenceNewsAdapter(((_item("old", "普通财经快讯"),), (new,), (new,), (new,)))
        moderator = AlwaysRetryableModerator()
        source = FinancePublicNewsEventSource(
            store=self.store,
            adapters=(adapter,),
            moderator=moderator,
            require_llm_moderation=True,
            minimum_poll_interval_seconds=5,
            moderation_defer_max_attempts=2,
            clock=lambda: clock[0],
        )

        source.poll_market_events()
        clock[0] += 10
        first_failure = source.poll_market_events()
        clock[0] += 10
        exhausted = source.poll_market_events()
        exhausted_state = self.store.get_event_source_state("public_news:eastmoney_fast_news")
        clock[0] += 10
        replay = source.poll_market_events()

        self.assertEqual(first_failure.events, ())
        self.assertEqual(exhausted.events, ())
        self.assertEqual(replay.events, ())
        self.assertIn("exhaust", dict(exhausted_state.state)["seen_item_ids"])
        self.assertEqual(dict(exhausted_state.state)["moderation_attempts"], {})
        self.assertEqual(moderator.calls, [("exhaust",), ("exhaust",)])
        rejection = self.store.list_market_data_rejections(provider="public_market")[0]
        self.assertEqual(rejection.stage, "news_moderation")
        self.assertIn("retry_exhausted_2", rejection.reason)

    def test_composite_source_keeps_news_when_quote_source_fails(self) -> None:
        class FailedSource:
            def poll_market_events(self, *, limit=100):
                return MarketEventPollResult(False, "unavailable", "public_market", "quotes", (), reason="down")

        class EmptySource:
            def poll_market_events(self, *, limit=100):
                return MarketEventPollResult(True, "empty", "public_market", "news", ())

        result = FinanceCompositeEventSource(sources=(FailedSource(), EmptySource())).poll_market_events()

        self.assertTrue(result.ok)
        self.assertTrue(result.reason.startswith("partial_event_source_failure:"))
        self.assertIn("down", result.reason)


if __name__ == "__main__":
    unittest.main()
