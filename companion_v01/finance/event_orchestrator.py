from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from services.market_data import (
    FinanceSubscription,
    MarketEvent,
    MarketEventStore,
    StoredMarketEvent,
)

from .event_contracts import FinanceAnalysisClient, FinanceAnalysisRequest, FinanceDeliveryAdapter
from .importance_policy import FinanceEventImportancePolicy, ImportanceDecision


@dataclass(frozen=True)
class FinanceDeliveryAttemptResult:
    event_id: str
    subscription_id: str
    status: str
    reason: str
    importance: ImportanceDecision
    analysis_id: str = ""
    attempt_count: int = 0

    def to_public_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "subscription_id": self.subscription_id,
            "status": self.status,
            "reason": self.reason,
            "importance": self.importance.to_public_dict(),
            "analysis_id": self.analysis_id,
            "attempt_count": self.attempt_count,
        }


@dataclass(frozen=True)
class FinanceEventRunResult:
    event_id: str
    upsert_status: str
    delivery_results: tuple[FinanceDeliveryAttemptResult, ...]

    @property
    def delivered_count(self) -> int:
        return sum(1 for item in self.delivery_results if item.status == "delivered")

    def to_public_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "upsert_status": self.upsert_status,
            "delivered_count": self.delivered_count,
            "delivery_results": [item.to_public_dict() for item in self.delivery_results],
        }


class FinanceEventOrchestrator:
    def __init__(
        self,
        *,
        store: MarketEventStore,
        analysis_client: FinanceAnalysisClient,
        delivery_adapter: FinanceDeliveryAdapter,
        importance_policy: FinanceEventImportancePolicy | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.analysis_client = analysis_client
        self.delivery_adapter = delivery_adapter
        self.importance_policy = importance_policy or FinanceEventImportancePolicy()
        self._clock = clock

    def process_event(self, event: MarketEvent, *, now_ts: int | None = None) -> FinanceEventRunResult:
        now = self._now(now_ts)
        upsert = self.store.upsert_event(event, now_ts=now)
        record = upsert.record
        subscriptions = self.store.match_subscriptions(record)
        results = tuple(
            self._process_subscription(record=record, subscription=subscription, now_ts=now)
            for subscription in subscriptions
        )
        return FinanceEventRunResult(
            event_id=record.event.event_id,
            upsert_status=upsert.status,
            delivery_results=results,
        )

    def retry_pending(self, *, limit: int = 100, now_ts: int | None = None) -> tuple[FinanceDeliveryAttemptResult, ...]:
        now = self._now(now_ts)
        results: list[FinanceDeliveryAttemptResult] = []
        for delivery in self.store.list_retryable_deliveries(limit=limit):
            record = self.store.get_event(delivery.event_id)
            subscription = self.store.get_subscription(delivery.subscription_id)
            if record is None or subscription is None:
                continue
            results.append(self._process_subscription(record=record, subscription=subscription, now_ts=now))
        return tuple(results)

    def _process_subscription(
        self,
        *,
        record: StoredMarketEvent,
        subscription: FinanceSubscription,
        now_ts: int,
    ) -> FinanceDeliveryAttemptResult:
        event = record.event
        cluster_delivered = self.store.has_delivered_cluster(
            subscription_id=subscription.subscription_id,
            cluster_id=record.cluster_id,
            exclude_event_id=event.event_id,
        )
        decision = self.importance_policy.evaluate(
            event=event,
            subscription=subscription,
            watchlist_priority=self._watchlist_priority(subscription, event.code),
            cluster_already_delivered=cluster_delivered,
        )
        if not decision.should_deliver:
            status = (
                "duplicate_cluster"
                if "same_cluster_already_delivered" in decision.reasons
                else "deferred_digest"
                if decision.level == "digest" or decision.minimum_level == "digest"
                else "archived"
            )
            return FinanceDeliveryAttemptResult(
                event_id=event.event_id,
                subscription_id=subscription.subscription_id,
                status=status,
                reason=decision.reasons[-1] if decision.reasons else "importance_policy",
                importance=decision,
            )

        authorization = self.delivery_adapter.authorize(subscription)
        if not authorization.allowed:
            return FinanceDeliveryAttemptResult(
                event_id=event.event_id,
                subscription_id=subscription.subscription_id,
                status="unauthorized",
                reason=authorization.reason or authorization.status,
                importance=decision,
            )

        reservation = self.store.ensure_delivery(
            event_id=event.event_id,
            subscription_id=subscription.subscription_id,
            now_ts=now_ts,
        )
        if not reservation.should_deliver:
            return FinanceDeliveryAttemptResult(
                event_id=event.event_id,
                subscription_id=subscription.subscription_id,
                status="already_delivered"
                if reservation.delivery.status == "delivered"
                else "in_progress"
                if reservation.delivery.status == "processing"
                else "cancelled",
                reason=reservation.delivery.reason or reservation.delivery.status,
                importance=decision,
                analysis_id=reservation.delivery.analysis_id,
                attempt_count=reservation.delivery.attempt_count,
            )

        claim = self.store.claim_delivery_attempt(
            event_id=event.event_id,
            subscription_id=subscription.subscription_id,
            now_ts=now_ts,
        )
        if claim is None:
            return FinanceDeliveryAttemptResult(
                event_id=event.event_id,
                subscription_id=subscription.subscription_id,
                status="failed",
                reason="delivery_claim_missing",
                importance=decision,
            )
        if not claim.acquired:
            return FinanceDeliveryAttemptResult(
                event_id=event.event_id,
                subscription_id=subscription.subscription_id,
                status="in_progress" if claim.delivery.status == "processing" else claim.delivery.status,
                reason=claim.delivery.reason or "delivery_not_claimed",
                importance=decision,
                analysis_id=claim.delivery.analysis_id,
                attempt_count=claim.delivery.attempt_count,
            )

        request = FinanceAnalysisRequest.create(
            event_record=record,
            subscription=subscription,
            importance=decision,
            requested_at=now_ts,
            attempt_count=claim.delivery.attempt_count,
        )
        try:
            analysis = self.analysis_client.analyze(request)
        except Exception as exc:
            return self._fail(
                request=request,
                decision=decision,
                reason=f"analysis_exception:{type(exc).__name__}:{exc}",
                attempt_count=claim.delivery.attempt_count,
                now_ts=now_ts,
            )
        if not analysis.ok:
            return self._fail(
                request=request,
                decision=decision,
                reason=f"analysis_failed:{analysis.reason or analysis.status}",
                attempt_count=claim.delivery.attempt_count,
                now_ts=now_ts,
            )

        try:
            delivered = self.delivery_adapter.deliver(subscription=subscription, analysis=analysis)
        except Exception as exc:
            return self._fail(
                request=request,
                decision=decision,
                reason=f"delivery_exception:{type(exc).__name__}:{exc}",
                attempt_count=claim.delivery.attempt_count,
                now_ts=now_ts,
            )
        if not delivered.ok:
            return self._fail(
                request=request,
                decision=decision,
                reason=f"delivery_failed:{delivered.reason or delivered.status}",
                attempt_count=claim.delivery.attempt_count,
                now_ts=now_ts,
            )

        final_delivery = self.store.mark_delivery_delivered(
            event_id=event.event_id,
            subscription_id=subscription.subscription_id,
            analysis_id=request.analysis_id,
            now_ts=now_ts,
        )
        return FinanceDeliveryAttemptResult(
            event_id=event.event_id,
            subscription_id=subscription.subscription_id,
            status="delivered",
            reason="",
            importance=decision,
            analysis_id=request.analysis_id,
            attempt_count=final_delivery.attempt_count if final_delivery else claim.delivery.attempt_count,
        )

    def _fail(
        self,
        *,
        request: FinanceAnalysisRequest,
        decision: ImportanceDecision,
        reason: str,
        attempt_count: int,
        now_ts: int,
    ) -> FinanceDeliveryAttemptResult:
        self.store.mark_delivery_failed(
            event_id=request.event_record.event.event_id,
            subscription_id=request.subscription.subscription_id,
            analysis_id=request.analysis_id,
            reason=reason,
            now_ts=now_ts,
        )
        return FinanceDeliveryAttemptResult(
            event_id=request.event_record.event.event_id,
            subscription_id=request.subscription.subscription_id,
            status="failed",
            reason=reason,
            importance=decision,
            analysis_id=request.analysis_id,
            attempt_count=attempt_count,
        )

    def _watchlist_priority(self, subscription: FinanceSubscription, code: str) -> float:
        priorities = [
            item.priority for item in self.store.list_watchlist(subscription.subscription_id) if item.code == code
        ]
        return max(priorities, default=0.0)

    def _now(self, value: int | None) -> int:
        return int(self._clock()) if value is None else int(value)
