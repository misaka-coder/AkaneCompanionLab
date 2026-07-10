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
from .push_governance import FinancePushGovernancePolicy


@dataclass(frozen=True)
class FinanceDeliveryAttemptResult:
    event_id: str
    subscription_id: str
    status: str
    reason: str
    importance: ImportanceDecision
    analysis_id: str = ""
    attempt_count: int = 0
    delivery_mode: str = "immediate"
    available_at: int = 0
    coalesced_event_count: int = 0

    def to_public_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "subscription_id": self.subscription_id,
            "status": self.status,
            "reason": self.reason,
            "importance": self.importance.to_public_dict(),
            "analysis_id": self.analysis_id,
            "attempt_count": self.attempt_count,
            "delivery_mode": self.delivery_mode,
            "available_at": self.available_at,
            "coalesced_event_count": self.coalesced_event_count,
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
        push_governance: FinancePushGovernancePolicy | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.analysis_client = analysis_client
        self.delivery_adapter = delivery_adapter
        self.importance_policy = importance_policy or FinanceEventImportancePolicy()
        self.push_governance = push_governance or FinancePushGovernancePolicy()
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
        for delivery in self.store.list_retryable_deliveries(limit=limit, now_ts=now):
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
        existing_delivery = self.store.get_delivery(
            event_id=event.event_id,
            subscription_id=subscription.subscription_id,
        )
        delivery_preexisted = existing_delivery is not None
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
        if cluster_delivered:
            if existing_delivery is not None and existing_delivery.status not in {"delivered", "cancelled"}:
                self.store.mark_delivery_cancelled(
                    event_id=event.event_id,
                    subscription_id=subscription.subscription_id,
                    reason="same_cluster_already_delivered",
                    now_ts=now_ts,
                )
            return FinanceDeliveryAttemptResult(
                event_id=event.event_id,
                subscription_id=subscription.subscription_id,
                status="duplicate_cluster",
                reason="same_cluster_already_delivered",
                importance=decision,
                delivery_mode=existing_delivery.delivery_mode if existing_delivery is not None else "cluster",
                available_at=existing_delivery.available_at if existing_delivery is not None else 0,
            )

        initial_plan = self.push_governance.plan_initial(importance=decision, now_ts=now_ts)
        if existing_delivery is None and initial_plan.action == "skip":
            status = (
                "deferred_digest" if decision.level == "digest" or decision.minimum_level == "digest" else "archived"
            )
            return FinanceDeliveryAttemptResult(
                event_id=event.event_id,
                subscription_id=subscription.subscription_id,
                status=status,
                reason=initial_plan.reason or (decision.reasons[-1] if decision.reasons else "importance_policy"),
                importance=decision,
                delivery_mode=initial_plan.delivery_mode,
                available_at=initial_plan.available_at,
            )

        authorization = self.delivery_adapter.authorize(subscription)
        if not authorization.allowed:
            return FinanceDeliveryAttemptResult(
                event_id=event.event_id,
                subscription_id=subscription.subscription_id,
                status="unauthorized",
                reason=authorization.reason or authorization.status,
                importance=decision,
                delivery_mode=(
                    existing_delivery.delivery_mode if existing_delivery is not None else initial_plan.delivery_mode
                ),
                available_at=(existing_delivery.available_at if existing_delivery is not None else 0),
            )

        if existing_delivery is None:
            coalesced = self._coalesce_initial_delivery(
                record=record,
                subscription=subscription,
                decision=decision,
                delivery_mode=initial_plan.delivery_mode,
                available_at=initial_plan.available_at,
                reason=initial_plan.reason,
                now_ts=now_ts,
            )
            if coalesced is not None:
                return coalesced
            reservation = self.store.ensure_delivery(
                event_id=event.event_id,
                subscription_id=subscription.subscription_id,
                available_at=initial_plan.available_at,
                delivery_mode=initial_plan.delivery_mode,
                importance_level=decision.level,
                reason=f"scheduled:{initial_plan.reason}" if initial_plan.action == "defer" else "",
                now_ts=now_ts,
            )
            existing_delivery = reservation.delivery
            if initial_plan.action == "defer":
                return self._scheduled_result(
                    event_id=event.event_id,
                    subscription_id=subscription.subscription_id,
                    decision=decision,
                    delivery=existing_delivery,
                    reason=initial_plan.reason,
                )
        elif initial_plan.bypassed and existing_delivery.status in {"pending", "failed"}:
            existing_delivery = (
                self.store.reschedule_delivery(
                    event_id=event.event_id,
                    subscription_id=subscription.subscription_id,
                    available_at=now_ts,
                    reason="scheduled:alert_bypass",
                    delivery_mode="immediate",
                    importance_level="alert",
                    now_ts=now_ts,
                )
                or existing_delivery
            )

        if existing_delivery.status == "delivered":
            return FinanceDeliveryAttemptResult(
                event_id=event.event_id,
                subscription_id=subscription.subscription_id,
                status="already_delivered",
                reason=existing_delivery.reason or "delivered",
                importance=decision,
                analysis_id=existing_delivery.analysis_id,
                attempt_count=existing_delivery.attempt_count,
                delivery_mode=existing_delivery.delivery_mode,
                available_at=existing_delivery.available_at,
            )
        if existing_delivery.status == "cancelled":
            return FinanceDeliveryAttemptResult(
                event_id=event.event_id,
                subscription_id=subscription.subscription_id,
                status="cancelled",
                reason=existing_delivery.reason or "cancelled",
                importance=decision,
                analysis_id=existing_delivery.analysis_id,
                attempt_count=existing_delivery.attempt_count,
                delivery_mode=existing_delivery.delivery_mode,
                available_at=existing_delivery.available_at,
            )
        if existing_delivery.status == "processing":
            return FinanceDeliveryAttemptResult(
                event_id=event.event_id,
                subscription_id=subscription.subscription_id,
                status="in_progress",
                reason=existing_delivery.reason or "processing",
                importance=decision,
                analysis_id=existing_delivery.analysis_id,
                attempt_count=existing_delivery.attempt_count,
                delivery_mode=existing_delivery.delivery_mode,
                available_at=existing_delivery.available_at,
            )
        if not retry_existing and delivery_preexisted:
            return FinanceDeliveryAttemptResult(
                event_id=event.event_id,
                subscription_id=subscription.subscription_id,
                status="scheduled" if existing_delivery.available_at > now_ts else existing_delivery.status,
                reason=existing_delivery.reason or "existing_delivery_not_retried_during_recovery",
                importance=decision,
                analysis_id=existing_delivery.analysis_id,
                attempt_count=existing_delivery.attempt_count,
                delivery_mode=existing_delivery.delivery_mode,
                available_at=existing_delivery.available_at,
            )
        if existing_delivery.available_at > now_ts:
            return self._scheduled_result(
                event_id=event.event_id,
                subscription_id=subscription.subscription_id,
                decision=decision,
                delivery=existing_delivery,
                reason=existing_delivery.reason or "not_due",
            )

        parts = self.store.list_delivery_parts(
            event_id=event.event_id,
            subscription_id=subscription.subscription_id,
        )
        if not parts:
            recent_delivery_times = self.store.list_recent_delivery_times(
                subscription_id=subscription.subscription_id,
                since_ts=max(
                    0,
                    now_ts
                    - max(
                        self.push_governance.rate_window_seconds,
                        self.push_governance.min_interval_seconds,
                    ),
                ),
            )
            runtime_plan = self.push_governance.plan_runtime(
                importance=decision,
                delivery_mode=existing_delivery.delivery_mode,
                now_ts=now_ts,
                recent_delivery_times=recent_delivery_times,
            )
            if runtime_plan.action == "defer":
                rescheduled = self.store.reschedule_delivery(
                    event_id=event.event_id,
                    subscription_id=subscription.subscription_id,
                    available_at=runtime_plan.available_at,
                    reason=f"scheduled:{runtime_plan.reason}",
                    now_ts=now_ts,
                )
                return self._scheduled_result(
                    event_id=event.event_id,
                    subscription_id=subscription.subscription_id,
                    decision=decision,
                    delivery=rescheduled or existing_delivery,
                    reason=runtime_plan.reason,
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
                delivery_mode=existing_delivery.delivery_mode,
                available_at=existing_delivery.available_at,
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
                delivery_mode=claim.delivery.delivery_mode,
                available_at=claim.delivery.available_at,
            )

        analysis_id = claim.delivery.analysis_id
        coalesced_records = self.store.list_coalesced_events(
            representative_event_id=event.event_id,
            subscription_id=subscription.subscription_id,
        )
        if not parts:
            request = FinanceAnalysisRequest.create(
                event_record=record,
                subscription=subscription,
                importance=decision,
                requested_at=now_ts,
                attempt_count=claim.delivery.attempt_count,
                related_event_records=coalesced_records,
                batch_kind=(claim.delivery.delivery_mode if coalesced_records else "single"),
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
                delivery_mode=claim.delivery.delivery_mode,
                available_at=claim.delivery.available_at,
                coalesced_event_count=len(coalesced_records),
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
            delivery_mode=claim.delivery.delivery_mode,
            available_at=claim.delivery.available_at,
            coalesced_event_count=len(coalesced_records),
        )

    def _coalesce_initial_delivery(
        self,
        *,
        record: StoredMarketEvent,
        subscription: FinanceSubscription,
        decision: ImportanceDecision,
        delivery_mode: str,
        available_at: int,
        reason: str,
        now_ts: int,
    ) -> FinanceDeliveryAttemptResult | None:
        if delivery_mode == "cluster":
            representative = self.store.find_open_cluster_delivery(
                subscription_id=subscription.subscription_id,
                cluster_id=record.cluster_id,
                exclude_event_id=record.event.event_id,
            )
            relation = "clustered_into"
            status = "cluster_coalesced"
        elif delivery_mode == "digest":
            representative = self.store.find_open_digest_delivery(
                subscription_id=subscription.subscription_id,
                exclude_event_id=record.event.event_id,
            )
            relation = "digested_into"
            status = "digest_coalesced"
        else:
            return None
        if representative is None:
            return None

        current = self.store.ensure_delivery(
            event_id=record.event.event_id,
            subscription_id=subscription.subscription_id,
            available_at=available_at,
            delivery_mode=delivery_mode,
            importance_level=decision.level,
            reason=f"scheduled:{reason}",
            now_ts=now_ts,
        ).delivery
        self.store.mark_delivery_cancelled(
            event_id=record.event.event_id,
            subscription_id=subscription.subscription_id,
            reason=f"{relation}:{representative.event_id}",
            analysis_id=representative.analysis_id,
            now_ts=now_ts,
        )
        if delivery_mode == "cluster" and representative.status in {"pending", "failed"}:
            max_wait_at = representative.created_at + self.push_governance.cluster_max_wait_seconds
            extended_at = min(max_wait_at, max(representative.available_at, int(available_at)))
            if extended_at > representative.available_at:
                representative = (
                    self.store.reschedule_delivery(
                        event_id=representative.event_id,
                        subscription_id=subscription.subscription_id,
                        available_at=extended_at,
                        reason="scheduled:cluster_coalesce",
                        now_ts=now_ts,
                    )
                    or representative
                )
        return FinanceDeliveryAttemptResult(
            event_id=record.event.event_id,
            subscription_id=subscription.subscription_id,
            status=status,
            reason=f"{relation}:{representative.event_id}",
            importance=decision,
            analysis_id=representative.analysis_id,
            attempt_count=current.attempt_count,
            delivery_mode=delivery_mode,
            available_at=representative.available_at,
            coalesced_event_count=1,
        )

    @staticmethod
    def _scheduled_result(
        *,
        event_id: str,
        subscription_id: str,
        decision: ImportanceDecision,
        delivery,
        reason: str,
    ) -> FinanceDeliveryAttemptResult:
        return FinanceDeliveryAttemptResult(
            event_id=event_id,
            subscription_id=subscription_id,
            status="scheduled",
            reason=str(reason or delivery.reason or "scheduled"),
            importance=decision,
            analysis_id=delivery.analysis_id,
            attempt_count=delivery.attempt_count,
            delivery_mode=delivery.delivery_mode,
            available_at=delivery.available_at,
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
        delivery = self.store.mark_delivery_failed(
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
            delivery_mode=delivery.delivery_mode if delivery is not None else "immediate",
            available_at=delivery.available_at if delivery is not None else 0,
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
