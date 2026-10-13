"""Async runner for the durable session inbox."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

from .session_inbox import SessionInboxItem, SessionInboxStore

logger = logging.getLogger(__name__)

_SQLITE_LOCK_RETRY_DELAYS = (0.1, 0.25, 0.5)


def _is_sqlite_lock_error(error: sqlite3.OperationalError) -> bool:
    code = getattr(error, "sqlite_errorcode", None)
    if isinstance(code, int) and (code & 0xFF) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
        return True
    message = str(error).lower()
    return "database is locked" in message or "database is busy" in message


class SessionWorkError(RuntimeError):
    """A terminal failure with a host-authored, log-safe reason code."""

    def __init__(self, reason: str) -> None:
        if not reason or len(reason) > 160 or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_" for c in reason):
            raise ValueError("invalid_session_work_reason")
        super().__init__(reason)
        self.reason = reason


class RetryableSessionWorkError(RuntimeError):
    def __init__(self, reason: str, *, retry_delay_seconds: float = 5.0) -> None:
        super().__init__(str(reason or "session_work_retryable"))
        self.reason = str(reason or "session_work_retryable")
        self.retry_delay_seconds = max(0.01, float(retry_delay_seconds))


class DurableSessionWorkQueue:
    """Drain one durable envelope per session; sources opt into compatible batches."""

    def __init__(
        self,
        store: SessionInboxStore,
        handler: Callable[[str, list[SessionInboxItem]], Awaitable[None]] | None = None,
        *,
        schedule_task: Callable[[Awaitable[None]], Any] | None = None,
        on_error: Callable[[str, list[SessionInboxItem], BaseException], None] | None = None,
        worker_id: str = "",
        lease_seconds: float = 300.0,
        max_batch_items: int = 32,
        max_batch_bytes: int = 65_536,
    ) -> None:
        self._store = store
        self._fallback_handler = handler
        self._source_handlers: dict[
            str,
            Callable[[str, list[SessionInboxItem]], Awaitable[None]],
        ] = {}
        self._source_error_handlers: dict[
            str,
            Callable[[str, list[SessionInboxItem], BaseException], None],
        ] = {}
        self._batch_keys: dict[str, Callable[[SessionInboxItem], str]] = {}
        self._retry_yield_policies: dict[str, Callable[[SessionInboxItem], bool]] = {}
        self._schedule_task = schedule_task or asyncio.create_task
        self._on_error = on_error
        self._worker_id = str(worker_id or "").strip() or f"inbox_worker_{uuid.uuid4().hex}"
        self._lease_seconds = max(1.0, float(lease_seconds))
        self._max_batch_items = max(1, int(max_batch_items))
        self._max_batch_bytes = max(1, int(max_batch_bytes))
        self._workers: dict[str, Any] = {}
        self._wake_events: dict[str, asyncio.Event] = {}
        self._closing = False
        self._store_tasks: set[asyncio.Task[Any]] = set()

    def request_shutdown(self) -> None:
        """Fence admission and wake deferred workers without cancelling handlers."""
        self._closing = True
        for event in self._wake_events.values():
            event.set()

    def is_idle_for_restart(self) -> bool:
        """Include work before/after its conversational hold, and pending DB threads."""
        return not any(not worker.done() for worker in self._workers.values()) and not any(
            not task.done() for task in self._store_tasks)

    async def _store_call(self, function, *args, **kwargs):
        # Cancelling an asyncio waiter does not stop its database thread. Keep
        # ownership until the thread has really finished, even during shutdown.
        for attempt in range(len(_SQLITE_LOCK_RETRY_DELAYS) + 1):
            task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
            self._store_tasks.add(task)
            task.add_done_callback(self._store_done)
            try:
                return await asyncio.shield(task)
            except sqlite3.OperationalError as exc:
                if not _is_sqlite_lock_error(exc) or attempt >= len(_SQLITE_LOCK_RETRY_DELAYS):
                    raise
                await asyncio.sleep(_SQLITE_LOCK_RETRY_DELAYS[attempt])

    def _store_done(self, task: asyncio.Task[Any]) -> None:
        self._store_tasks.discard(task)
        if not task.cancelled():
            task.exception()  # Also observe failures after a caller is cancelled.

    async def close(self, *, timeout: float = 10.0) -> dict[str, Any]:
        self.request_shutdown()
        deadline = asyncio.get_running_loop().time() + max(0.0, float(timeout))
        while True:
            workers = [worker for worker in self._workers.values() if not worker.done()]
            operations = [task for task in self._store_tasks if not task.done()]
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            if workers or operations:
                if remaining:
                    await asyncio.wait(set(workers + operations), timeout=remaining)
                workers = [worker for worker in self._workers.values() if not worker.done()]
                operations = [task for task in self._store_tasks if not task.done()]
                if workers or operations:
                    return {"status": "degraded", "reason": "session_queue_drain_timeout",
                            "workers": len(workers), "store_operations": len(operations)}
            # A steer can be owned by a direct request, outside _workers.
            count_task = asyncio.create_task(self._store_call(self._store.owned_claim_count, self._worker_id))
            done, _ = await asyncio.wait((count_task,), timeout=max(0.01, remaining))
            if not done:
                count_task.cancel()
                await asyncio.gather(count_task, return_exceptions=True)
                return {"status": "degraded", "reason": "session_queue_store_timeout"}
            claims = count_task.result()
            if not claims and not self._store_tasks:
                return {"status": "stopped", "reason": "", "workers": 0, "store_operations": 0, "owned_claims": 0}
            if asyncio.get_running_loop().time() >= deadline:
                return {"status": "degraded", "reason": "session_queue_claims_pending", "owned_claims": claims}
            await asyncio.sleep(min(0.05, max(0.0, deadline - asyncio.get_running_loop().time())))
            if asyncio.get_running_loop().time() >= deadline:
                return {"status": "degraded", "reason": "session_queue_claims_pending", "owned_claims": claims}

    def register_handler(
        self,
        source: Any,
        handler: Callable[[str, list[SessionInboxItem]], Awaitable[None]],
        *,
        on_error: Callable[[str, list[SessionInboxItem], BaseException], None] | None = None,
        batch_key: Callable[[SessionInboxItem], str] | None = None,
        can_yield_retry: Callable[[SessionInboxItem], bool] | None = None,
    ) -> None:
        normalized = str(source or "").strip()
        if not normalized or not callable(handler):
            raise ValueError("session_work_source_and_handler_required")
        self._source_handlers[normalized] = handler
        if batch_key is None:
            self._batch_keys.pop(normalized, None)
        else:
            self._batch_keys[normalized] = batch_key
        if can_yield_retry is None:
            self._retry_yield_policies.pop(normalized, None)
        else:
            self._retry_yield_policies[normalized] = can_yield_retry
        if on_error is None:
            self._source_error_handlers.pop(normalized, None)
        else:
            self._source_error_handlers[normalized] = on_error

    async def requeue_claim(
        self,
        item_id: Any,
        *,
        claim_token: Any,
        error: Any,
    ) -> dict[str, Any]:
        result = await self._store_call(
            self._store.fail,
            item_id,
            claim_token=claim_token,
            error=error,
            retryable=True,
        )
        await self._wake_after_settlement(item_id, result)
        return result

    async def commit_claim(self, item_id: Any, *, claim_token: Any) -> dict[str, Any]:
        result = await self._store_call(
            self._store.commit,
            item_id,
            claim_token=claim_token,
        )
        await self._wake_after_settlement(item_id, result)
        return result

    async def _wake_after_settlement(self, item_id: Any, result: dict[str, Any]) -> None:
        if result.get("ok"):
            item = await self._store_call(self._store.get, item_id)
            if not self._closing and item is not None and await self._store_call(self._store.pending_count, item.session_key):
                self._ensure_worker(item.session_key)

    def _batch_key(self, item: SessionInboxItem) -> str:
        policy = self._batch_keys.get(item.source)
        return policy(item) if policy is not None else ""

    def _can_yield_retry(self, item: SessionInboxItem) -> bool:
        policy = self._retry_yield_policies.get(item.source)
        return bool(policy and policy(item))

    async def begin_processing(self, items: list[SessionInboxItem]) -> None:
        result = await self._store_call(self._store.begin_processing, items)
        if not result.get("ok"):
            raise RuntimeError(str(result.get("reason") or "session_processing_fence_failed"))

    async def enqueue(self, *, schedule: bool = True, **fields: Any) -> dict[str, Any]:
        if self._closing:
            return {"ok": False, "status": "stopping", "reason": "session_queue_stopping"}
        result = await self._store_call(self._store.enqueue, **fields)
        if schedule and result.get("ok") and result.get("status") in {"queued", "duplicate"}:
            item = result.get("item")
            if result.get("status") == "queued" or getattr(item, "status", "") == "queued":
                self._ensure_worker(str(fields.get("session_key") or "").strip())
        return result

    async def claim_for_active_turn(self, session_key: Any, item_id: Any) -> dict[str, Any]:
        """Reserve a just-persisted steer without waiting behind ordinary turns."""

        if self._closing:
            return {"ok": False, "status": "stopping", "reason": "session_queue_stopping"}
        claim = await self._store_call(
            self._store.claim_next,
            session_key,
            worker_id=self._worker_id,
            lease_seconds=self._lease_seconds,
            expected_item_id=item_id,
        )
        if self._closing and claim.get("ok"):
            await self._store_call(self._store.settle_claims, list(claim.get("items") or [claim["item"]]),
                                   status="queued", error="session_queue_stopping")
            return {"ok": False, "status": "stopping", "reason": "session_queue_stopping"}
        return claim

    async def schedule_session(self, session_key: Any) -> None:
        self._ensure_worker(str(session_key or "").strip())

    async def recover(self) -> int:
        if self._closing:
            return 0
        await self._store_call(self._store.recover_abandoned_claims)
        keys = await self._store_call(self._store.pending_session_keys)
        for key in keys:
            self._ensure_worker(key)
        return len(keys)

    def has_work(self, session_key: Any) -> bool:
        key = str(session_key or "").strip()
        if not key:
            return False
        worker = self._workers.get(key)
        if worker is not None and not bool(getattr(worker, "done", lambda: False)()):
            return True
        return self._store.pending_count(key) > 0

    def pending_count(self, session_key: Any) -> int:
        return self._store.pending_count(session_key)

    def _ensure_worker(self, key: str) -> None:
        if not key or self._closing:
            return
        self._wake_events.setdefault(key, asyncio.Event()).set()
        current = self._workers.get(key)
        if current is not None and not bool(getattr(current, "done", lambda: False)()):
            return
        self._workers[key] = self._schedule_task(self._drain(key))

    async def _drain(self, key: str) -> None:
        recheck_idle = False
        wake = self._wake_events.setdefault(key, asyncio.Event())
        try:
            while not self._closing:
                # Clear before the DB read so an enqueue racing that read is
                # still observed when this worker reaches its deferred wait.
                wake.clear()
                claim = await self._store_call(
                    self._store.claim_next,
                    key,
                    worker_id=self._worker_id,
                    lease_seconds=self._lease_seconds,
                    batch_key=self._batch_key,
                    can_yield_retry=self._can_yield_retry,
                    max_batch_items=self._max_batch_items,
                    max_batch_bytes=self._max_batch_bytes,
                )
                if not claim.get("ok"):
                    if claim.get("status") == "deferred":
                        # Ordinary deferred input remains an ordering barrier.
                        # New admission may make source-authorized retry work
                        # yield; recheck immediately rather than sleeping through it.
                        try:
                            await asyncio.wait_for(wake.wait(), timeout=max(0.01, float(claim["retry_after"])))
                        except asyncio.TimeoutError:
                            pass
                        continue
                    recheck_idle = claim.get("status") == "idle"
                    return
                item = claim.get("item")
                if not isinstance(item, SessionInboxItem):
                    return
                items = list(claim.get("items") or [item])
                if self._closing:
                    await self._store_call(self._store.settle_claims, items,
                                           status="queued", error="session_queue_stopping")
                    return
                try:
                    handler = self._source_handlers.get(item.source) or self._fallback_handler
                    if handler is None:
                        raise LookupError("session_work_handler_unavailable")
                    await self._handle_with_lease(handler, key, items)
                except asyncio.CancelledError:
                    await asyncio.shield(
                        self._store_call(
                            self._store.settle_claims,
                            items,
                            status="queued",
                            error="worker_cancelled",
                        )
                    )
                    raise
                except RetryableSessionWorkError as exc:
                    await self._store_call(
                        self._store.settle_claims,
                        items,
                        status="queued",
                        error=exc.reason,
                        retry_delay_seconds=exc.retry_delay_seconds,
                    )
                    self._report_error(key, items, exc)
                    continue
                except Exception as exc:
                    await self._store_call(
                        self._store.settle_claims,
                        items,
                        status="failed",
                        error=exc.reason if isinstance(exc, SessionWorkError) else exc.__class__.__name__,
                    )
                    self._report_error(key, items, exc)
                    continue
                settled = await self._store_call(
                    self._store.settle_claims,
                    items,
                    status="committed",
                )
                if not settled.get("ok"):
                    self._report_error(key, items, RuntimeError("session_claim_settlement_failed"))
                    return
        finally:
            self._workers.pop(key, None)
            self._wake_events.pop(key, None)
            # enqueue() can race the final empty DB check while this worker
            # still looks busy. Retire and recheck without an await in between.
            if not self._closing and recheck_idle and self._store.pending_count(key):
                self._ensure_worker(key)

    async def _renew_claim_lease(self, items: list[SessionInboxItem]) -> None:
        while True:
            await asyncio.sleep(self._lease_seconds / 3)
            result = await self._store_call(self._store.renew_claims, items, lease_seconds=self._lease_seconds)
            if not result.get("ok"):
                raise RuntimeError("session_claim_lease_lost")

    @asynccontextmanager
    async def keep_claim_alive(self, items: list[SessionInboxItem]):
        """Renew foreground claims, including time spent waiting for the turn lock."""
        owner = asyncio.current_task()
        renewal_error = None

        async def renew() -> None:
            nonlocal renewal_error
            try:
                await self._renew_claim_lease(items)
            except Exception as exc:
                renewal_error = exc
                owner.cancel()

        heartbeat = asyncio.create_task(renew())
        try:
            yield
        except asyncio.CancelledError:
            if renewal_error is not None:
                owner.uncancel()
                raise renewal_error
            raise
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _handle_with_lease(self, handler, key: str, items: list[SessionInboxItem]) -> None:
        work = asyncio.create_task(handler(key, items))
        heartbeat = asyncio.create_task(self._renew_claim_lease(items))
        try:
            done, _ = await asyncio.wait((work, heartbeat), return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                await heartbeat
            await work
        finally:
            for task in (work, heartbeat):
                if not task.done():
                    task.cancel()
            await asyncio.gather(work, heartbeat, return_exceptions=True)

    def _report_error(self, key: str, items: list[SessionInboxItem], exc: BaseException) -> None:
        handler = self._source_error_handlers.get(items[0].source) or self._on_error
        if handler is not None:
            try:
                handler(key, items, exc)
            except Exception:
                logger.exception("session work error observer failed")
        else:
            logger.error("session work failed: %s", type(exc).__name__)


__all__ = ["DurableSessionWorkQueue", "RetryableSessionWorkError", "SessionWorkError"]
