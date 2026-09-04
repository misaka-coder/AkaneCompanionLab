"""Async runner for the durable session inbox."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Awaitable, Callable

from .session_inbox import SessionInboxItem, SessionInboxStore


class DurableSessionWorkQueue:
    """Drain one durable item at a time per session, in parallel across sessions."""

    def __init__(
        self,
        store: SessionInboxStore,
        handler: Callable[[str, list[SessionInboxItem]], Awaitable[None]],
        *,
        schedule_task: Callable[[Awaitable[None]], Any] | None = None,
        on_error: Callable[[str, list[SessionInboxItem], BaseException], None] | None = None,
        worker_id: str = "",
        lease_seconds: float = 300.0,
    ) -> None:
        self._store = store
        self._handler = handler
        self._schedule_task = schedule_task or asyncio.create_task
        self._on_error = on_error
        self._worker_id = str(worker_id or "").strip() or f"inbox_worker_{uuid.uuid4().hex}"
        self._lease_seconds = max(1.0, float(lease_seconds))
        self._workers: dict[str, Any] = {}

    async def enqueue(self, *, schedule: bool = True, **fields: Any) -> dict[str, Any]:
        result = await asyncio.to_thread(self._store.enqueue, **fields)
        if schedule and result.get("ok") and result.get("status") in {"queued", "duplicate"}:
            item = result.get("item")
            if result.get("status") == "queued" or getattr(item, "status", "") == "queued":
                self._ensure_worker(str(fields.get("session_key") or "").strip())
        return result

    async def claim_for_active_turn(self, session_key: Any, item_id: Any) -> dict[str, Any]:
        """Claim one just-persisted head item before offering it as a steer."""

        return await asyncio.to_thread(
            self._store.claim_next,
            session_key,
            worker_id=self._worker_id,
            lease_seconds=self._lease_seconds,
            expected_item_id=item_id,
        )

    async def schedule_session(self, session_key: Any) -> None:
        self._ensure_worker(str(session_key or "").strip())

    async def recover(self) -> int:
        await asyncio.to_thread(self._store.recover_abandoned_claims)
        keys = await asyncio.to_thread(self._store.pending_session_keys)
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
        if not key:
            return
        current = self._workers.get(key)
        if current is not None and not bool(getattr(current, "done", lambda: False)()):
            return
        self._workers[key] = self._schedule_task(self._drain(key))

    async def _drain(self, key: str) -> None:
        try:
            while True:
                claim = await asyncio.to_thread(
                    self._store.claim_next,
                    key,
                    worker_id=self._worker_id,
                    lease_seconds=self._lease_seconds,
                )
                if not claim.get("ok"):
                    return
                item = claim.get("item")
                if not isinstance(item, SessionInboxItem):
                    return
                try:
                    await self._handler(key, [item])
                except asyncio.CancelledError:
                    await asyncio.shield(
                        asyncio.to_thread(
                            self._store.fail,
                            item.item_id,
                            claim_token=claim.get("claim_token"),
                            error="worker_cancelled",
                            retryable=True,
                        )
                    )
                    raise
                except Exception as exc:
                    await asyncio.to_thread(
                        self._store.fail,
                        item.item_id,
                        claim_token=claim.get("claim_token"),
                        error=exc.__class__.__name__,
                        retryable=False,
                    )
                    if self._on_error is not None:
                        self._on_error(key, [item], exc)
                    continue
                await asyncio.to_thread(
                    self._store.commit,
                    item.item_id,
                    claim_token=claim.get("claim_token"),
                )
        finally:
            self._workers.pop(key, None)


__all__ = ["DurableSessionWorkQueue"]
