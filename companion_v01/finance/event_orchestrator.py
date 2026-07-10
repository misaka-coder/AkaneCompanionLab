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

from .event_contracts import (
    FinanceAnalysisClient,
    FinanceAnalysisRequest,
    FinanceDeliveryAdapter,
    FinanceDeliveryPartSpec,
    build_finance_delivery_parts,
)
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
        return self._process_record(
            record=upsert.record,
            upsert_status=upsert.status,
            now_ts=now,
            retry_existing=True,
        )

    def process_stored_event(
        self,
        event_id: str,
        *,
        upsert_status: str = "stored",
        now_ts: int | None = None,
        retry_existing: bool = True,
    ) -> FinanceEventRunResult | None:
        record = self.store.get_event(event_id)
        if record is None:
            return None
        return self._process_record(
            record=record,
            upsert_status=upsert_status,
            now_ts=self._now(now_ts),
            retry_existing=retry_existing,
        )

    def _process_record(
        self,
        *,
        record: StoredMarketEvent,
        upsert_status: str,
        now_ts: int,
        retry_existing: bool,
    ) -> FinanceEventRunResult:
        subscriptions = self.store.match_subscriptions(record)
        results = tuple(
            self._process_subscription(
                record=record,
                subscription=subscription,
                now_ts=now_ts,
                retry_existing=retry_existing,
            )
            for subscription in subscriptions
        )
        return FinanceEventRunResult(
            event_id=record.event.event_id,
            upsert_status=upsert_status,
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
        retry_existing: bool = True,
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
            watchlist_priority=self._watchlist_priority(subscription, event),
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

        if not retry_existing:
            existing_delivery = self.store.get_delivery(
                event_id=event.event_id,
                subscription_id=subscription.subscription_id,
            )
            if existing_delivery is not None:
                return FinanceDeliveryAttemptResult(
                    event_id=event.event_id,
                    subscription_id=subscription.subscription_id,
                    status=(
                        "already_delivered"
                        if existing_delivery.status == "delivered"
                        else "in_progress"
                        if existing_delivery.status == "processing"
                        else existing_delivery.status
                    ),
                    reason=existing_delivery.reason or "existing_delivery_not_retried_during_recovery",
                    importance=decision,
                    analysis_id=existing_delivery.analysis_id,
                    attempt_count=existing_delivery.attempt_count,
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

        parts = self.store.list_delivery_parts(
            event_id=event.event_id,
            subscription_id=subscription.subscription_id,
        )
        analysis_id = claim.delivery.analysis_id
        if not parts:
            request = FinanceAnalysisRequest.create(
                event_record=record,
                subscription=subscription,
                importance=decision,
                requested_at=now_ts,
                attempt_count=claim.delivery.attempt_count,
            )
            analysis_id = request.analysis_id
            try:
                analysis = self.analysis_client.analyze(request)
            except Exception as exc:
                return self._fail_delivery(
                    record=record,
                    subscription=subscription,
                    decision=decision,
                    analysis_id=analysis_id,
                    reason=f"analysis_exception:{type(exc).__name__}:{exc}",
                    attempt_count=claim.delivery.attempt_count,
                    now_ts=now_ts,
                )
            if not analysis.ok:
                failure_prefix = "analysis_exhausted" if "exhausted" in analysis.reason else "analysis_failed"
                return self._fail_delivery(
                    record=record,
                    subscription=subscription,
                    decision=decision,
                    analysis_id=analysis_id,
                    reason=f"{failure_prefix}:{analysis.reason or analysis.status}",
                    attempt_count=claim.delivery.attempt_count,
                    now_ts=now_ts,
                )
            part_specs = build_finance_delivery_parts(analysis)
            if not part_specs:
                return self._fail_delivery(
                    record=record,
                    subscription=subscription,
                    decision=decision,
                    analysis_id=analysis_id,
                    reason="analysis_failed:no_delivery_parts",
                    attempt_count=claim.delivery.attempt_count,
                    now_ts=now_ts,
                )

        current_subscription = self.store.get_subscription(subscription.subscription_id)
        if (
            current_subscription is None
            or not current_subscription.enabled
            or current_subscription.finance_mode != "push"
        ):
            current_delivery = self.store.get_delivery(
                event_id=event.event_id,
                subscription_id=subscription.subscription_id,
            )
            return FinanceDeliveryAttemptResult(
                event_id=event.event_id,
                subscription_id=subscription.subscription_id,
                status="cancelled",
                reason="subscription_disabled_before_delivery",
                importance=decision,
                analysis_id=analysis_id,
                attempt_count=(
                    current_delivery.attempt_count if current_delivery is not None else claim.delivery.attempt_count
                ),
            )
        if not parts:
            try:
                parts = self.store.ensure_delivery_parts(
                    event_id=event.event_id,
                    subscription_id=subscription.subscription_id,
                    analysis_id=analysis_id,
                    parts=(part.to_store_dict() for part in part_specs),
                    now_ts=now_ts,
                )
            except Exception as exc:
                return self._fail_delivery(
                    record=record,
                    subscription=subscription,
                    decision=decision,
                    analysis_id=analysis_id,
                    reason=f"delivery_parts_persist_failed:{type(exc).__name__}:{exc}",
                    attempt_count=claim.delivery.attempt_count,
                    now_ts=now_ts,
                )
        delivery_authorization = self.delivery_adapter.authorize(current_subscription)
        if not delivery_authorization.allowed:
            return self._fail_delivery(
                record=record,
                subscription=subscription,
                decision=decision,
                analysis_id=analysis_id,
                reason=f"delivery_authorization_lost:{delivery_authorization.reason or delivery_authorization.status}",
                attempt_count=claim.delivery.attempt_count,
                now_ts=now_ts,
            )

        deliver_part = getattr(self.delivery_adapter, "deliver_part", None)
        for stored_part in parts:
            if stored_part.status == "delivered":
                continue
            part_claim = self.store.claim_delivery_part(
                event_id=event.event_id,
                subscription_id=subscription.subscription_id,
                part_key=stored_part.part_key,
                now_ts=now_ts,
            )
            if part_claim is None or not part_claim.acquired:
                continue
            if not callable(deliver_part):
                self.store.mark_delivery_part_failed(
                    event_id=event.event_id,
                    subscription_id=subscription.subscription_id,
                    part_key=stored_part.part_key,
                    reason="delivery_part_adapter_unavailable",
                    now_ts=now_ts,
                )
                continue
            part = FinanceDeliveryPartSpec(
                part_key=part_claim.part.part_key,
                part_type=part_claim.part.part_type,
                payload=part_claim.part.payload,
            )
            try:
                delivered = deliver_part(subscription=current_subscription, part=part)
            except Exception as exc:
                delivered = None
                failure_reason = f"delivery_exception:{type(exc).__name__}:{exc}"
            else:
                failure_reason = f"delivery_failed:{delivered.reason or delivered.status}"
            if delivered is not None and delivered.ok:
                self.store.mark_delivery_part_delivered(
                    event_id=event.event_id,
                    subscription_id=subscription.subscription_id,
                    part_key=part.part_key,
                    now_ts=now_ts,
                )
            else:
                self.store.mark_delivery_part_failed(
                    event_id=event.event_id,
                    subscription_id=subscription.subscription_id,
                    part_key=part.part_key,
                    reason=failure_reason,
                    now_ts=now_ts,
                )

        final_delivery = self.store.finalize_delivery_parts(
            event_id=event.event_id,
            subscription_id=subscription.subscription_id,
            analysis_id=analysis_id,
            now_ts=now_ts,
        )
        delivered_ok = final_delivery is not None and final_delivery.status == "delivered"
        return FinanceDeliveryAttemptResult(
            event_id=event.event_id,
            subscription_id=subscription.subscription_id,
            status="delivered" if delivered_ok else "failed",
            reason="" if delivered_ok else (final_delivery.reason if final_delivery else "delivery_finalize_failed"),
            importance=decision,
            analysis_id=analysis_id,
            attempt_count=final_delivery.attempt_count if final_delivery else claim.delivery.attempt_count,
        )

    def _fail_delivery(
        self,
        *,
        record: StoredMarketEvent,
        subscription: FinanceSubscription,
        decision: ImportanceDecision,
        analysis_id: str,
        reason: str,
        attempt_count: int,
        now_ts: int,
    ) -> FinanceDeliveryAttemptResult:
        self.store.mark_delivery_failed(
            event_id=record.event.event_id,
            subscription_id=subscription.subscription_id,
            analysis_id=analysis_id,
            reason=reason,
            now_ts=now_ts,
        )
        return FinanceDeliveryAttemptResult(
            event_id=record.event.event_id,
            subscription_id=subscription.subscription_id,
            status="failed",
            reason=reason,
            importance=decision,
            analysis_id=analysis_id,
            attempt_count=attempt_count,
        )

    def _watchlist_priority(self, subscription: FinanceSubscription, event: MarketEvent) -> float:
        priorities = [
            item.priority
            for item in self.store.list_watchlist(subscription.subscription_id)
            if item.provider == event.provider and item.code == event.code
        ]
        return max(priorities, default=0.0)

    def _now(self, value: int | None) -> int:
        return int(self._clock()) if value is None else int(value)
