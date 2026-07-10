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
        self.poll_batch_size = max(1, min(1000, int(poll_batch_size)))
        self.recovery_max_age_seconds = max(60, min(7 * 24 * 60 * 60, int(recovery_max_age_seconds)))
        self.recovery_limit = max(1, min(1000, int(recovery_limit)))
        self._clock = clock
        self._log_event = log_event
        self._stop_event = threading.Event()
        self._cycle_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._recovery_complete = False
        self._cycle_count = 0
        self._last_cycle_at = 0
        self._last_status = "disabled" if not self.enabled else "idle"
        self._last_reason = ""

    def start(self) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "status": "disabled", "reason": "finance_event_ingestion_disabled"}
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return {"ok": True, "status": "already_running"}
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="finance-event-worker",
                daemon=True,
            )
            self._thread.start()
            self._last_status = "running"
        return {"ok": True, "status": "started"}

    def stop(self, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
        self._stop_event.set()
        with self._state_lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, min(30.0, float(timeout_seconds))))
        alive = bool(thread is not None and thread.is_alive())
        with self._state_lock:
            self._last_status = "stopping" if alive else "stopped"
            if not alive:
                self._thread = None
        return {"ok": not alive, "status": "stopping" if alive else "stopped"}

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            running = bool(self._thread is not None and self._thread.is_alive())
            return {
                "enabled": self.enabled,
                "running": running,
                "status": self._last_status,
                "reason": self._last_reason,
                "cycle_count": self._cycle_count,
                "last_cycle_at": self._last_cycle_at,
                "poll_interval_seconds": self.poll_interval_seconds,
                "poll_batch_size": self.poll_batch_size,
                "recovery_complete": self._recovery_complete,
            }

    def run_once(self, *, now_ts: int | None = None) -> FinanceEventWorkerCycleResult:
        if not self.enabled:
            return FinanceEventWorkerCycleResult(
                status="disabled",
                reason="finance_event_ingestion_disabled",
            )
        if not self._cycle_lock.acquire(blocking=False):
            return FinanceEventWorkerCycleResult(status="busy", reason="cycle_already_running")
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

            retry_results = self.orchestrator.retry_pending(limit=self.recovery_limit, now_ts=now)
            recovery_results = self._recover_recent(now_ts=now)
            batch = self.source.poll_market_events(limit=self.poll_batch_size)
            if not batch.ok:
                result = FinanceEventWorkerCycleResult(
                    status="source_unavailable",
                    delivered_count=self._delivered_count(retry_results, recovery_results),
                    failed_count=self._failed_count(retry_results, recovery_results),
                    retry_count=len(retry_results),
                    recovery_count=len(recovery_results),
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
                processed = self.orchestrator.process_stored_event(
                    event_id,
                    upsert_status="bridge_callback",
                    now_ts=now,
                )
                if processed is not None:
                    event_results.append(processed)
            all_results = [*retry_results, *recovery_results, *event_results]
            result = FinanceEventWorkerCycleResult(
                status="processed" if batch.events else "idle",
                polled_count=len(batch.events),
                persisted_count=persisted_count,
                delivered_count=self._delivered_count(all_results),
                failed_count=self._failed_count(all_results),
                ignored_count=batch.ignored_count,
                retry_count=len(retry_results),
                recovery_count=len(recovery_results),
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
            self._cycle_lock.release()

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
            processed = self.orchestrator.process_stored_event(
                record.event.event_id,
                upsert_status="recovered",
                now_ts=now_ts,
                retry_existing=False,
            )
            if processed is not None:
                results.append(processed)
        with self._state_lock:
            self._recovery_complete = True
        return tuple(results)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            started = time.monotonic()
            self.run_once()
            elapsed = max(0.0, time.monotonic() - started)
            self._stop_event.wait(max(0.0, self.poll_interval_seconds - elapsed))

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
