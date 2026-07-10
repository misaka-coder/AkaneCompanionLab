from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import hashlib
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from companion_v01.finance import (
    FinanceAnalysisResult,
    FinanceDeliveryAuthorization,
    FinanceDeliveryResult,
    FinanceEventOrchestrator,
    FinancePushGovernancePolicy,
    ImportanceDecision,
    ensure_market_push_contract,
)
from services.market_data import MarketEvent, MarketEventStore


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _event(
    event_id: str,
    title: str,
    *,
    content_type: str = "companynews",
    labels: tuple[str, ...] = ("earnings", "synthetic"),
    published_at: int = 1_752_153_000,
) -> MarketEvent:
    return MarketEvent(
        provider="mock_choice",
        event_id=event_id,
        published_at=published_at,
        produced_at=published_at + 10,
        received_at=published_at + 20,
        code="000000.TEST",
        content_type=content_type,
        title=title,
        source="Synthetic Governance Source",
        url=f"https://example.invalid/{event_id}",
        sentiment="unknown",
        labels=labels,
        sector_code="",
        raw_hash=_hash(event_id),
    )


def _decision(level: str, *, minimum_level: str = "notify") -> ImportanceDecision:
    return ImportanceDecision(
        level=level,
        score={"archive": 0.2, "digest": 0.45, "notify": 0.7, "alert": 0.9}[level],
        should_analyze=level in {"notify", "alert"},
        should_deliver=level in {"notify", "alert"},
        minimum_level=minimum_level,
        reasons=(f"test:{level}",),
    )


class _FakeAnalysis:
    def __init__(self) -> None:
        self.requests = []

    def analyze(self, request):
        self.requests.append(request)
        messages = ensure_market_push_contract(
            request=request,
            frame={"speech": "这批事件可能影响市场预期，但仍需核验完整正文与实时行情。"},
        )
        return FinanceAnalysisResult(
            ok=True,
            status="analyzed",
            analysis_id=request.analysis_id,
            messages=messages,
        )


class _FakeDelivery:
    def __init__(self) -> None:
        self.deliveries = []

    def authorize(self, _subscription):
        return FinanceDeliveryAuthorization(True, "authorized")

    def deliver_part(self, *, subscription, part):
        self.deliveries.append((subscription, part))
        return FinanceDeliveryResult(True, "delivered")


class FinancePushGovernancePolicyTests(unittest.TestCase):
    def test_alert_bypasses_quiet_hours_and_rate_limits(self) -> None:
        timezone = ZoneInfo("Asia/Shanghai")
        now = int(datetime(2026, 7, 10, 23, 30, tzinfo=timezone).timestamp())
        policy = FinancePushGovernancePolicy(
            enabled=True,
            cluster_coalesce_seconds=90,
            digest_enabled=True,
            min_interval_seconds=60,
            max_notifications_per_window=1,
            quiet_hours_enabled=True,
            quiet_start="23:00",
            quiet_end="07:00",
        )

        initial = policy.plan_initial(importance=_decision("alert"), now_ts=now)
        runtime = policy.plan_runtime(
            importance=_decision("alert"),
            delivery_mode="immediate",
            now_ts=now,
            recent_delivery_times=(now - 1,),
        )

        self.assertEqual(initial.action, "deliver")
        self.assertEqual(initial.available_at, now)
        self.assertTrue(initial.bypassed)
        self.assertEqual(runtime.action, "deliver")
        self.assertEqual(runtime.available_at, now)

    def test_notify_waits_until_quiet_hours_end(self) -> None:
        timezone = ZoneInfo("Asia/Shanghai")
        now = int(datetime(2026, 7, 10, 23, 30, tzinfo=timezone).timestamp())
        expected = int(datetime(2026, 7, 11, 7, 0, tzinfo=timezone).timestamp())
        policy = FinancePushGovernancePolicy(
            enabled=True,
            cluster_coalesce_seconds=90,
            quiet_hours_enabled=True,
            quiet_start="23:00",
            quiet_end="07:00",
        )

        result = policy.plan_initial(importance=_decision("notify"), now_ts=now)

        self.assertEqual(result.action, "defer")
        self.assertEqual(result.available_at, expected)
        self.assertIn("quiet_hours", result.reason)

    def test_digest_uses_next_local_window(self) -> None:
        timezone = ZoneInfo("Asia/Shanghai")
        now = int(datetime(2026, 7, 10, 10, 5, tzinfo=timezone).timestamp())
        expected = int(datetime(2026, 7, 10, 10, 30, tzinfo=timezone).timestamp())
        policy = FinancePushGovernancePolicy(
            enabled=True,
            digest_enabled=True,
            digest_interval_seconds=30 * 60,
        )

        result = policy.plan_initial(importance=_decision("digest"), now_ts=now)

        self.assertEqual(result.action, "defer")
        self.assertEqual(result.delivery_mode, "digest")
        self.assertEqual(result.available_at, expected)

    def test_archive_subscription_never_enters_push_queue(self) -> None:
        policy = FinancePushGovernancePolicy(enabled=True, digest_enabled=True)

        result = policy.plan_initial(
            importance=_decision("alert", minimum_level="archive"),
            now_ts=100,
        )

        self.assertEqual(result.action, "skip")
        self.assertEqual(result.reason, "below_subscription_threshold")


class FinancePushGovernanceIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = MarketEventStore(Path(self.temp_dir.name) / "governance.sqlite3")
        self.subscription = self.store.upsert_subscription(
            subscription_id="sub-governance",
            client="qq",
            target_id="20001",
            is_group=True,
            session_id="qq_group_shared_20001",
            profile_user_id="qq_group_shared_20001",
            character_pack_id="akane_default",
            finance_mode="push",
            enabled=True,
            filters={"codes": ["000000.TEST"]},
            delivery_policy={"level": "notify"},
            now_ts=100,
        )

    def _orchestrator(self, policy: FinancePushGovernancePolicy):
        analysis = _FakeAnalysis()
        delivery = _FakeDelivery()
        orchestrator = FinanceEventOrchestrator(
            store=self.store,
            analysis_client=analysis,
            delivery_adapter=delivery,
            push_governance=policy,
        )
        return orchestrator, analysis, delivery

    def test_notify_is_persisted_and_delivered_only_when_due(self) -> None:
        orchestrator, analysis, delivery = self._orchestrator(
            FinancePushGovernancePolicy(
                enabled=True,
                cluster_coalesce_seconds=90,
                cluster_max_wait_seconds=180,
            )
        )
        event = _event("choice:governance-notify", "合成公司披露季度经营数据")

        scheduled = orchestrator.process_event(event, now_ts=200)
        early = orchestrator.retry_pending(now_ts=289)
        due = orchestrator.retry_pending(now_ts=290)

        result = scheduled.delivery_results[0]
        self.assertEqual(result.status, "scheduled")
        self.assertEqual(result.available_at, 290)
        self.assertEqual(early, ())
        self.assertEqual(due[0].status, "delivered")
        self.assertEqual(len(analysis.requests), 1)
        self.assertEqual(len(delivery.deliveries), 1)

    def test_same_cluster_coalesces_and_reaches_ai_as_one_batch(self) -> None:
        orchestrator, analysis, delivery = self._orchestrator(
            FinancePushGovernancePolicy(
                enabled=True,
                cluster_coalesce_seconds=90,
                cluster_max_wait_seconds=180,
            )
        )
        first = _event("choice:governance-cluster-a", "合成公司披露季度经营数据")
        second = _event(
            "choice:governance-cluster-b",
            "合成公司披露季度经营数据 更新",
            published_at=first.published_at + 60,
        )

        first_result = orchestrator.process_event(first, now_ts=200)
        second_result = orchestrator.process_event(second, now_ts=250)
        early = orchestrator.retry_pending(now_ts=339)
        due = orchestrator.retry_pending(now_ts=340)

        self.assertEqual(first_result.delivery_results[0].status, "scheduled")
        self.assertEqual(second_result.delivery_results[0].status, "cluster_coalesced")
        self.assertEqual(early, ())
        self.assertEqual(due[0].status, "delivered")
        self.assertEqual(len(analysis.requests), 1)
        self.assertEqual(analysis.requests[0].batch_kind, "cluster")
        self.assertEqual(len(analysis.requests[0].related_event_records), 1)
        self.assertEqual(len(delivery.deliveries), 1)
        covered = self.store.get_delivery(
            event_id=second.event_id,
            subscription_id=self.subscription.subscription_id,
        )
        self.assertEqual(covered.status, "cancelled")
        self.assertEqual(covered.reason, f"clustered_into:{first.event_id}")
        self.assertEqual(covered.analysis_id, analysis.requests[0].analysis_id)

    def test_digest_combines_different_events_into_one_recoverable_delivery(self) -> None:
        orchestrator, analysis, delivery = self._orchestrator(
            FinancePushGovernancePolicy(
                enabled=True,
                digest_enabled=True,
                digest_interval_seconds=30 * 60,
            )
        )
        now = int(datetime(2026, 7, 10, 10, 5, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
        first = _event(
            "choice:governance-digest-a",
            "合成公司行业动态简讯",
            content_type="news",
            labels=("synthetic",),
        )
        second = _event(
            "choice:governance-digest-b",
            "合成公司产品渠道观察",
            content_type="news",
            labels=("synthetic",),
            published_at=first.published_at + 60,
        )

        first_result = orchestrator.process_event(first, now_ts=now)
        second_result = orchestrator.process_event(second, now_ts=now + 10)
        available_at = first_result.delivery_results[0].available_at
        due = orchestrator.retry_pending(now_ts=available_at)

        self.assertEqual(first_result.delivery_results[0].status, "scheduled")
        self.assertEqual(first_result.delivery_results[0].delivery_mode, "digest")
        self.assertEqual(second_result.delivery_results[0].status, "digest_coalesced")
        self.assertEqual(due[0].status, "delivered")
        self.assertEqual(len(analysis.requests), 1)
        self.assertEqual(analysis.requests[0].batch_kind, "digest")
        self.assertEqual(len(analysis.requests[0].related_event_records), 1)
        self.assertEqual(len(delivery.deliveries), 1)

    def test_normal_rate_limit_reschedules_without_dropping_event(self) -> None:
        orchestrator, analysis, delivery = self._orchestrator(
            FinancePushGovernancePolicy(
                enabled=True,
                min_interval_seconds=60,
                max_notifications_per_window=0,
            )
        )
        first = _event("choice:governance-rate-a", "合成公司披露季度经营数据")
        second = _event(
            "choice:governance-rate-b",
            "合成公司宣布回购计划",
            published_at=first.published_at + 600,
        )

        delivered = orchestrator.process_event(first, now_ts=200)
        scheduled = orchestrator.process_event(second, now_ts=210)
        early = orchestrator.retry_pending(now_ts=259)
        due = orchestrator.retry_pending(now_ts=260)

        self.assertEqual(delivered.delivery_results[0].status, "delivered")
        self.assertEqual(scheduled.delivery_results[0].status, "scheduled")
        self.assertEqual(scheduled.delivery_results[0].available_at, 260)
        self.assertEqual(early, ())
        self.assertEqual(due[0].status, "delivered")
        self.assertEqual(len(analysis.requests), 2)
        self.assertEqual(len(delivery.deliveries), 2)

    def test_alert_is_immediate_even_during_quiet_hours(self) -> None:
        orchestrator, analysis, delivery = self._orchestrator(
            FinancePushGovernancePolicy(
                enabled=True,
                cluster_coalesce_seconds=90,
                min_interval_seconds=60,
                max_notifications_per_window=1,
                quiet_hours_enabled=True,
                quiet_start="23:00",
                quiet_end="07:00",
            )
        )
        now = int(datetime(2026, 7, 10, 23, 30, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
        alert = _event("choice:governance-alert", "合成公司因重大事项停牌")

        result = orchestrator.process_event(alert, now_ts=now)

        self.assertEqual(result.delivery_results[0].status, "delivered")
        self.assertEqual(result.delivery_results[0].delivery_mode, "immediate")
        self.assertEqual(len(analysis.requests), 1)
        self.assertEqual(len(delivery.deliveries), 1)

    def test_scheduled_event_promoted_to_alert_is_released_immediately(self) -> None:
        orchestrator, analysis, delivery = self._orchestrator(
            FinancePushGovernancePolicy(
                enabled=True,
                cluster_coalesce_seconds=90,
                cluster_max_wait_seconds=180,
            )
        )
        original = _event("choice:governance-promoted-alert", "合成公司披露季度经营数据")
        promoted = replace(
            original,
            title="合成公司因重大事项停牌",
            received_at=original.received_at + 10,
            raw_hash=_hash("choice:governance-promoted-alert-v2"),
        )

        scheduled = orchestrator.process_event(original, now_ts=200)
        released = orchestrator.process_event(promoted, now_ts=210)

        self.assertEqual(scheduled.delivery_results[0].status, "scheduled")
        self.assertEqual(released.delivery_results[0].status, "delivered")
        self.assertEqual(released.delivery_results[0].delivery_mode, "immediate")
        self.assertEqual(len(analysis.requests), 1)
        self.assertEqual(len(delivery.deliveries), 1)


if __name__ == "__main__":
    unittest.main()
