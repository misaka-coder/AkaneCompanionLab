from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from companion_v01.finance import (
    FinanceAnalysisResult,
    FinanceDeliveryAuthorization,
    FinanceDeliveryResult,
    FinanceEventOrchestrator,
    FinanceEventWorker,
    FinancePushGovernancePolicy,
    ensure_market_push_contract,
)
from services.market_data import MarketEvent, MarketEventPollResult, MarketEventStore


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _event(event_id: str, title: str, *, published_at: int = 1_752_153_000) -> MarketEvent:
    return MarketEvent(
        provider="mock_choice",
        event_id=event_id,
        published_at=published_at,
        produced_at=published_at + 10,
        received_at=published_at + 20,
        code="000000.TEST",
        content_type="companynews",
        title=title,
        source="Synthetic Worker Source",
        url=f"https://example.invalid/{event_id}",
        sentiment="unknown",
        labels=("earnings", "synthetic"),
        sector_code="",
        raw_hash=_hash(event_id),
    )


class _FakeSource:
    def __init__(self, batches):
        self.batches = list(batches)
        self.calls = 0

    def poll_market_events(self, *, limit=100):
        self.calls += 1
        if self.batches:
            return self.batches.pop(0)
        return MarketEventPollResult(
            ok=True,
            status="empty",
            provider="mock_choice",
            source="Synthetic Worker Source",
            events=(),
        )


class _FakeAnalysis:
    def __init__(self, *, before_analyze=None):
        self.requests = []
        self.before_analyze = before_analyze

    def analyze(self, request):
        if self.before_analyze is not None:
            self.before_analyze(request)
        self.requests.append(request)
        messages = ensure_market_push_contract(
            request=request,
            frame={"speech": "合成分析结果，仍需核验完整正文与带时间的行情。"},
        )
        return FinanceAnalysisResult(
            ok=True,
            status="analyzed",
            analysis_id=request.analysis_id,
            messages=messages,
        )


class _FakeDelivery:
    def __init__(self):
        self.deliveries = []

    def authorize(self, subscription):
        return FinanceDeliveryAuthorization(True, "authorized")

    def deliver_part(self, *, subscription, part):
        self.deliveries.append((subscription, part))
        return FinanceDeliveryResult(True, "delivered")


class _FailingDelivery(_FakeDelivery):
    def deliver_part(self, *, subscription, part):
        self.deliveries.append((subscription, part))
        return FinanceDeliveryResult(False, "failed", "synthetic_offline")


class FinanceEventWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = MarketEventStore(Path(self.temp_dir.name) / "worker.sqlite3")

    def _subscription(self):
        return self.store.upsert_subscription(
            subscription_id="sub-worker",
            client="qq",
            target_id="20001",
            is_group=True,
            session_id="qq_group_shared_20001",
            profile_user_id="qq_group_shared_20001",
            finance_mode="push",
            enabled=True,
            filters={"codes": ["000000.TEST"]},
            delivery_policy={"level": "notify"},
            now_ts=100,
        )

    def test_worker_is_disabled_by_default_and_does_not_poll(self) -> None:
        source = _FakeSource([])
        worker = FinanceEventWorker(
            source=source,
            orchestrator=FinanceEventOrchestrator(
                store=self.store,
                analysis_client=_FakeAnalysis(),
                delivery_adapter=_FakeDelivery(),
            ),
        )

        started = worker.start()
        cycle = worker.run_once(now_ts=1_752_153_600)

        self.assertEqual(started["status"], "disabled")
        self.assertEqual(cycle.status, "disabled")
        self.assertEqual(source.calls, 0)

    def test_enabled_worker_thread_starts_and_stops_without_subscription(self) -> None:
        source = _FakeSource([])
        worker = FinanceEventWorker(
            source=source,
            orchestrator=FinanceEventOrchestrator(
                store=self.store,
                analysis_client=_FakeAnalysis(),
                delivery_adapter=_FakeDelivery(),
            ),
            enabled=True,
            poll_interval_seconds=60,
        )

        started = worker.start()
        stopped = worker.stop(timeout_seconds=2)

        self.assertEqual(started["status"], "started")
        self.assertEqual(stopped["status"], "stopped")
        self.assertFalse(worker.status()["running"])
        self.assertEqual(source.calls, 0)

    def test_worker_does_not_drain_bridge_without_enabled_push_subscription(self) -> None:
        source = _FakeSource(
            [
                MarketEventPollResult(
                    ok=True,
                    status="ok",
                    provider="mock_choice",
                    source="Synthetic Worker Source",
                    events=(_event("choice:no-sub", "合成公司披露季度经营数据"),),
                )
            ]
        )
        worker = FinanceEventWorker(
            source=source,
            orchestrator=FinanceEventOrchestrator(
                store=self.store,
                analysis_client=_FakeAnalysis(),
                delivery_adapter=_FakeDelivery(),
            ),
            enabled=True,
        )

        result = worker.run_once(now_ts=1_752_153_600)

        self.assertEqual(result.status, "idle")
        self.assertEqual(result.reason, "no_enabled_push_subscriptions")
        self.assertEqual(source.calls, 0)
        self.assertIsNone(self.store.get_event("choice:no-sub"))

    def test_worker_persists_entire_batch_before_running_ai(self) -> None:
        self._subscription()
        first = _event("choice:worker-a", "合成公司披露季度经营数据")
        second = _event(
            "choice:worker-b",
            "合成公司宣布重大回购方案",
            published_at=first.published_at + 60,
        )
        source = _FakeSource(
            [
                MarketEventPollResult(
                    ok=True,
                    status="ok",
                    provider="mock_choice",
                    source="Synthetic Worker Source",
                    events=(first, second),
                )
            ]
        )

        def assert_batch_is_persisted(_request):
            self.assertIsNotNone(self.store.get_event(first.event_id))
            self.assertIsNotNone(self.store.get_event(second.event_id))

        analysis = _FakeAnalysis(before_analyze=assert_batch_is_persisted)
        delivery = _FakeDelivery()
        worker = FinanceEventWorker(
            source=source,
            orchestrator=FinanceEventOrchestrator(
                store=self.store,
                analysis_client=analysis,
                delivery_adapter=delivery,
            ),
            enabled=True,
            recovery_max_age_seconds=3600,
        )

        result = worker.run_once(now_ts=1_752_153_600)

        self.assertEqual(result.status, "processed")
        self.assertEqual(result.polled_count, 2)
        self.assertEqual(result.persisted_count, 2)
        self.assertEqual(result.delivered_count, 2)
        self.assertEqual(len(analysis.requests), 2)
        self.assertEqual(len(delivery.deliveries), 2)

    def test_first_cycle_recovers_recent_event_already_persisted(self) -> None:
        self._subscription()
        event = _event("choice:worker-recovery", "合成公司披露季度经营数据")
        self.store.upsert_event(event, now_ts=1_752_153_100)
        source = _FakeSource([])
        analysis = _FakeAnalysis()
        delivery = _FakeDelivery()
        worker = FinanceEventWorker(
            source=source,
            orchestrator=FinanceEventOrchestrator(
                store=self.store,
                analysis_client=analysis,
                delivery_adapter=delivery,
            ),
            enabled=True,
            recovery_max_age_seconds=3600,
        )

        first = worker.run_once(now_ts=1_752_153_600)
        second = worker.run_once(now_ts=1_752_153_700)

        self.assertEqual(first.recovery_count, 1)
        self.assertEqual(first.delivered_count, 1)
        self.assertEqual(second.recovery_count, 0)
        self.assertEqual(len(analysis.requests), 1)
        self.assertEqual(len(delivery.deliveries), 1)

    def test_worker_reports_scheduled_push_and_delivers_it_when_due(self) -> None:
        self._subscription()
        event = _event("choice:worker-scheduled", "合成公司披露季度经营数据")
        source = _FakeSource(
            [
                MarketEventPollResult(
                    ok=True,
                    status="ok",
                    provider="mock_choice",
                    source="Synthetic Worker Source",
                    events=(event,),
                )
            ]
        )
        analysis = _FakeAnalysis()
        delivery = _FakeDelivery()
        worker = FinanceEventWorker(
            source=source,
            orchestrator=FinanceEventOrchestrator(
                store=self.store,
                analysis_client=analysis,
                delivery_adapter=delivery,
                push_governance=FinancePushGovernancePolicy(
                    enabled=True,
                    cluster_coalesce_seconds=90,
                    cluster_max_wait_seconds=180,
                ),
            ),
            enabled=True,
            recovery_max_age_seconds=3600,
        )

        scheduled = worker.run_once(now_ts=1_752_153_600)
        delivered = worker.run_once(now_ts=1_752_153_690)

        self.assertEqual(scheduled.scheduled_count, 1)
        self.assertEqual(scheduled.delivered_count, 0)
        self.assertEqual(scheduled.failed_count, 0)
        self.assertEqual(delivered.delivered_count, 1)
        self.assertEqual(len(analysis.requests), 1)
        self.assertEqual(len(delivery.deliveries), 1)

    def test_recovery_does_not_retry_failed_delivery_twice_in_same_cycle(self) -> None:
        subscription = self._subscription()
        event = _event("choice:worker-failed-once", "合成公司披露季度经营数据")
        self.store.upsert_event(event, now_ts=1_752_153_100)
        self.store.ensure_delivery(
            event_id=event.event_id,
            subscription_id=subscription.subscription_id,
            now_ts=1_752_153_110,
        )
        self.store.record_delivery_attempt(
            event_id=event.event_id,
            subscription_id=subscription.subscription_id,
            now_ts=1_752_153_120,
        )
        self.store.mark_delivery_failed(
            event_id=event.event_id,
            subscription_id=subscription.subscription_id,
            reason="synthetic_offline",
            now_ts=1_752_153_130,
        )
        analysis = _FakeAnalysis()
        delivery = _FailingDelivery()
        worker = FinanceEventWorker(
            source=_FakeSource([]),
            orchestrator=FinanceEventOrchestrator(
                store=self.store,
                analysis_client=analysis,
                delivery_adapter=delivery,
            ),
            enabled=True,
            recovery_max_age_seconds=3600,
        )

        result = worker.run_once(now_ts=1_752_153_600)

        self.assertEqual(result.retry_count, 1)
        self.assertEqual(len(analysis.requests), 1)
        self.assertEqual(len(delivery.deliveries), 1)
        stored = self.store.get_delivery(
            event_id=event.event_id,
            subscription_id=subscription.subscription_id,
        )
        self.assertEqual(stored.attempt_count, 2)

    def test_source_failure_is_structured_and_does_not_create_fake_events(self) -> None:
        self._subscription()
        source = _FakeSource(
            [
                MarketEventPollResult(
                    ok=False,
                    status="unavailable",
                    provider="choice_emquant",
                    source="Choice EmQuant",
                    events=(),
                    reason="bridge offline",
                )
            ]
        )
        worker = FinanceEventWorker(
            source=source,
            orchestrator=FinanceEventOrchestrator(
                store=self.store,
                analysis_client=_FakeAnalysis(),
                delivery_adapter=_FakeDelivery(),
            ),
            enabled=True,
        )

        result = worker.run_once(now_ts=1_752_153_600)

        self.assertEqual(result.status, "source_unavailable")
        self.assertEqual(result.reason, "bridge offline")
        self.assertEqual(self.store.list_events(), ())


if __name__ == "__main__":
    unittest.main()
