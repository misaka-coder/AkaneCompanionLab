from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from services.market_data import MarketEventPollResult

from .event_orchestrator import FinanceEventOrchestrator


class FinanceMarketEventSource(Protocol):
    def poll_market_events(self, *, limit: int = 100) -> MarketEventPollResult: ...


@dataclass(frozen=True)
class FinanceEventWorkerCycleResult:
    status: str
    polled_count: int = 0
    persisted_count: int = 0
    delivered_count: int = 0
    failed_count: int = 0
    ignored_count: int = 0
    retry_count: int = 0
    recovery_count: int = 0
    reason: str = ""
    scheduled_count: int = 0
    coalesced_count: int = 0

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "polled_count": self.polled_count,
            "persisted_count": self.persisted_count,
            "delivered_count": self.delivered_count,
            "failed_count": self.failed_count,
            "ignored_count": self.ignored_count,
            "retry_count": self.retry_count,
            "recovery_count": self.recovery_count,
            "scheduled_count": self.scheduled_count,
            "coalesced_count": self.coalesced_count,
            "reason": self.reason,
        }


class FinanceEventWorker:
    def __init__(
        self,
        *,
        source: FinanceMarketEventSource,
        orchestrator: FinanceEventOrchestrator,
        enabled: bool = False,
        poll_interval_seconds: float = 2.0,
        delivery_interval_seconds: float = 0.5,
        poll_batch_size: int = 20,
        recovery_max_age_seconds: int = 6 * 60 * 60,
        recovery_limit: int = 200,
        clock: Callable[[], float] = time.time,
        log_event: Callable[..., Any] | None = None,
    ) -> None:
        self.source = source
        self.orchestrator = orchestrator
        self.enabled = bool(enabled)
        self.poll_interval_seconds = max(0.25, min(60.0, float(poll_interval_seconds)))
        self.delivery_interval_seconds = max(0.1, min(5.0, float(delivery_interval_seconds)))
        self.poll_batch_size = max(1, min(1000, int(poll_batch_size)))
        self.recovery_max_age_seconds = max(60, min(7 * 24 * 60 * 60, int(recovery_max_age_seconds)))
        self.recovery_limit = max(1, min(1000, int(recovery_limit)))
        self._clock = clock
        self._log_event = log_event
        self._stop_event = threading.Event()
        self._poll_cycle_lock = threading.Lock()
        self._delivery_cycle_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._poll_thread: threading.Thread | None = None
        self._delivery_thread: threading.Thread | None = None
        self._recovery_complete = False
        self._cycle_count = 0
        self._delivery_cycle_count = 0
        self._last_cycle_at = 0
        self._last_delivery_cycle_at = 0
        self._last_status = "disabled" if not self.enabled else "idle"
        self._last_reason = ""
        self._last_delivery_status = "disabled" if not self.enabled else "idle"
        self._last_delivery_reason = ""
        self._interrupted_recovery_count = 0

    def start(self) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "status": "disabled", "reason": "finance_event_ingestion_disabled"}
        with self._state_lock:
            poll_alive = bool(self._poll_thread is not None and self._poll_thread.is_alive())
            delivery_alive = bool(self._delivery_thread is not None and self._delivery_thread.is_alive())
            if poll_alive and delivery_alive:
                return {"ok": True, "status": "already_running"}
            self._stop_event.clear()
            if not poll_alive and not delivery_alive:
                try:
                    self._interrupted_recovery_count = self.orchestrator.store.recover_interrupted_deliveries(
                        now_ts=int(self._clock()),
                    )
                except Exception as exc:
                    self._last_status = "failed"
                    self._last_reason = f"interrupted_delivery_recovery_failed:{type(exc).__name__}"
                    return {"ok": False, "status": "failed", "reason": self._last_reason}
            if not poll_alive:
                self._poll_thread = threading.Thread(
                    target=self._run_poll_loop,
                    name="finance-event-poll-worker",
                    daemon=True,
                )
                self._poll_thread.start()
            if not delivery_alive:
                self._delivery_thread = threading.Thread(
                    target=self._run_delivery_loop,
                    name="finance-event-delivery-worker",
                    daemon=True,
                )
                self._delivery_thread.start()
            self._last_status = "running"
            self._last_delivery_status = "running"
        return {"ok": True, "status": "started"}

    def stop(self, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
        self._stop_event.set()
        with self._state_lock:
            threads = tuple(thread for thread in (self._poll_thread, self._delivery_thread) if thread is not None)
        timeout = max(0.0, min(30.0, float(timeout_seconds)))
        deadline = time.monotonic() + timeout
        for thread in threads:
            if thread.is_alive():
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
        alive = any(thread.is_alive() for thread in threads)
        with self._state_lock:
            self._last_status = "stopping" if alive else "stopped"
            self._last_delivery_status = "stopping" if alive else "stopped"
            if not alive:
                self._poll_thread = None
                self._delivery_thread = None
        return {"ok": not alive, "status": "stopping" if alive else "stopped"}

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            poll_running = bool(self._poll_thread is not None and self._poll_thread.is_alive())
            delivery_running = bool(self._delivery_thread is not None and self._delivery_thread.is_alive())
            return {
                "enabled": self.enabled,
                "running": poll_running and delivery_running,
                "poll_running": poll_running,
                "delivery_running": delivery_running,
                "status": self._last_status,
                "reason": self._last_reason,
                "cycle_count": self._cycle_count,
                "last_cycle_at": self._last_cycle_at,
                "delivery_status": self._last_delivery_status,
                "delivery_reason": self._last_delivery_reason,
                "delivery_cycle_count": self._delivery_cycle_count,
                "last_delivery_cycle_at": self._last_delivery_cycle_at,
                "poll_interval_seconds": self.poll_interval_seconds,
                "delivery_interval_seconds": self.delivery_interval_seconds,
                "poll_batch_size": self.poll_batch_size,
                "recovery_complete": self._recovery_complete,
                "interrupted_recovery_count": self._interrupted_recovery_count,
                "push_governance": self.orchestrator.push_governance.to_public_dict(),
            }

    def run_once(self, *, now_ts: int | None = None) -> FinanceEventWorkerCycleResult:
        if not self.enabled:
            return FinanceEventWorkerCycleResult(
                status="disabled",
                reason="finance_event_ingestion_disabled",
            )
        if not self._poll_cycle_lock.acquire(blocking=False):
            return FinanceEventWorkerCycleResult(status="busy", reason="poll_cycle_already_running")
        try:
            now = int(self._clock()) if now_ts is None else int(now_ts)
            push_subscriptions = tuple(
                subscription
                for subscription in self.orchestrator.store.list_subscriptions(enabled=True)
                if subscription.finance_mode == "push"
            )
            if not push_subscriptions:
                result = FinanceEventWorkerCycleResult(
                    status="idle",
                    reason="no_enabled_push_subscriptions",
                )
                self._record_cycle(result, now_ts=now)
                return result

            recovery_results = self._recover_recent(now_ts=now)
            batch = self.source.poll_market_events(limit=self.poll_batch_size)
            if not batch.ok:
                result = FinanceEventWorkerCycleResult(
                    status="source_unavailable",
                    recovery_count=len(recovery_results),
                    scheduled_count=self._status_count(
                        recovery_results,
                        statuses={"scheduled"},
                    ),
                    coalesced_count=self._status_count(
                        recovery_results,
                        statuses={"cluster_coalesced", "digest_coalesced"},
                    ),
                    reason=batch.reason or batch.status,
                )
                self._record_cycle(result, now_ts=now)
                return result

            canonical_ids: list[str] = []
            persisted_count = 0
            seen_ids: set[str] = set()
            for event in batch.events:
                upsert = self.orchestrator.store.upsert_event(event, now_ts=now)
                persisted_count += 1
                canonical_id = upsert.canonical_event_id
                if canonical_id and canonical_id not in seen_ids:
                    seen_ids.add(canonical_id)
                    canonical_ids.append(canonical_id)

            event_results = []
            for event_id in canonical_ids:
                processed = self.orchestrator.schedule_stored_event(
                    event_id,
                    upsert_status="bridge_callback",
                    now_ts=now,
                )
                if processed is not None:
                    event_results.append(processed)
            all_results = [*recovery_results, *event_results]
            result = FinanceEventWorkerCycleResult(
                status="processed" if batch.events else "idle",
                polled_count=len(batch.events),
                persisted_count=persisted_count,
                failed_count=self._failed_count(all_results),
                ignored_count=batch.ignored_count,
                recovery_count=len(recovery_results),
                scheduled_count=self._status_count(all_results, statuses={"scheduled"}),
                coalesced_count=self._status_count(
                    all_results,
                    statuses={"cluster_coalesced", "digest_coalesced"},
                ),
                reason=batch.reason,
            )
            self._record_cycle(result, now_ts=now)
            return result
        except Exception as exc:
            result = FinanceEventWorkerCycleResult(
                status="failed",
                reason=type(exc).__name__,
            )
            self._record_cycle(result, now_ts=int(self._clock()) if now_ts is None else int(now_ts))
            return result
        finally:
            self._poll_cycle_lock.release()

    def run_delivery_once(self, *, now_ts: int | None = None) -> FinanceEventWorkerCycleResult:
        if not self.enabled:
            return FinanceEventWorkerCycleResult(
                status="disabled",
                reason="finance_event_ingestion_disabled",
            )
        if not self._delivery_cycle_lock.acquire(blocking=False):
            return FinanceEventWorkerCycleResult(status="busy", reason="delivery_cycle_already_running")
        try:
            now = int(self._clock()) if now_ts is None else int(now_ts)
            retry_results = self.orchestrator.retry_pending(limit=self.recovery_limit, now_ts=now)
            result = FinanceEventWorkerCycleResult(
                status="processed" if retry_results else "idle",
                delivered_count=self._delivered_count(retry_results),
                failed_count=self._failed_count(retry_results),
                retry_count=len(retry_results),
                scheduled_count=self._status_count(retry_results, statuses={"scheduled"}),
                coalesced_count=self._status_count(
                    retry_results,
                    statuses={"cluster_coalesced", "digest_coalesced"},
                ),
            )
            self._record_delivery_cycle(result, now_ts=now)
            return result
        except Exception as exc:
            result = FinanceEventWorkerCycleResult(status="failed", reason=type(exc).__name__)
            self._record_delivery_cycle(
                result,
                now_ts=int(self._clock()) if now_ts is None else int(now_ts),
            )
            return result
        finally:
            self._delivery_cycle_lock.release()

    def _recover_recent(self, *, now_ts: int):
        with self._state_lock:
            if self._recovery_complete:
                return ()
        records = self.orchestrator.store.list_events(
            date_from=max(1, now_ts - self.recovery_max_age_seconds),
            date_to=now_ts,
            limit=self.recovery_limit,
        )
        results = []
        for record in reversed(records):
            processed = self.orchestrator.schedule_stored_event(
                record.event.event_id,
                upsert_status="recovered",
                now_ts=now_ts,
            )
            if processed is not None:
                results.append(processed)
        with self._state_lock:
            self._recovery_complete = True
        return tuple(results)

    def _run_poll_loop(self) -> None:
        while not self._stop_event.is_set():
            started = time.monotonic()
            self.run_once()
            elapsed = max(0.0, time.monotonic() - started)
            self._stop_event.wait(max(0.0, self.poll_interval_seconds - elapsed))

    def _run_delivery_loop(self) -> None:
        while not self._stop_event.is_set():
            started = time.monotonic()
            self.run_delivery_once()
            elapsed = max(0.0, time.monotonic() - started)
            self._stop_event.wait(max(0.0, self.delivery_interval_seconds - elapsed))

    def _record_cycle(self, result: FinanceEventWorkerCycleResult, *, now_ts: int) -> None:
        with self._state_lock:
            self._cycle_count += 1
            self._last_cycle_at = now_ts
            self._last_status = result.status
            self._last_reason = result.reason
        if self._log_event is None:
            return
        try:
            self._log_event("finance_event_worker_cycle", **result.to_public_dict())
        except Exception:
            return

    def _record_delivery_cycle(self, result: FinanceEventWorkerCycleResult, *, now_ts: int) -> None:
        with self._state_lock:
            self._delivery_cycle_count += 1
            self._last_delivery_cycle_at = now_ts
            self._last_delivery_status = result.status
            self._last_delivery_reason = result.reason
        if self._log_event is None or (result.status == "idle" and not result.reason):
            return
        try:
            self._log_event("finance_event_delivery_cycle", **result.to_public_dict())
        except Exception:
            return

    @staticmethod
    def _delivered_count(*result_groups: Any) -> int:
        count = 0
        for group in result_groups:
            for result in group or ():
                if hasattr(result, "delivered_count"):
                    count += int(getattr(result, "delivered_count", 0) or 0)
                elif getattr(result, "status", "") == "delivered":
                    count += 1
        return count

    @staticmethod
    def _failed_count(*result_groups: Any) -> int:
        count = 0
        for group in result_groups:
            for result in group or ():
                delivery_results = getattr(result, "delivery_results", ()) or ()
                if delivery_results:
                    count += sum(1 for item in delivery_results if getattr(item, "status", "") == "failed")
                elif getattr(result, "status", "") == "failed":
                    count += 1
        return count

    @staticmethod
    def _status_count(*result_groups: Any, statuses: set[str]) -> int:
        count = 0
        for group in result_groups:
            for result in group or ():
                delivery_results = getattr(result, "delivery_results", ()) or ()
                if delivery_results:
                    count += sum(1 for item in delivery_results if getattr(item, "status", "") in statuses)
                elif getattr(result, "status", "") in statuses:
                    count += 1
        return count
