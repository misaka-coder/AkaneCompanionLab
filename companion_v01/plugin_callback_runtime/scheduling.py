"""Callback futures, cancellation and fallback-loop ownership."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Mapping
from ..plugin_generation_protocol import PLUGIN_GENERATION_PROTOCOL


class CallbackScheduling:
    """Callback futures, cancellation and fallback-loop ownership."""

    __slots__ = ()

    def _schedule(
        self,
        callback_id: str,
        coroutine: Any,
        *,
        loop: asyncio.AbstractEventLoop | None,
        unavailable_reason: str,
    ) -> None:
        with self._lock:
            closed = self._closed
        if not closed and (loop is None or loop.is_closed() or not loop.is_running()):
            loop = self._ensure_fallback_loop()
        reason = ""
        with self._lock:
            if self._closed:
                reason = "callback_router_closed"
            elif callback_id in self._futures:
                reason = "duplicate_callback_id"
            elif loop is None:
                reason = unavailable_reason
            else:
                try:
                    # Reserve and submit under the same lock. Register the done
                    # callback afterwards: it may run synchronously on add.
                    future = asyncio.run_coroutine_threadsafe(coroutine, loop)
                    self._futures[callback_id] = future
                except RuntimeError:
                    reason = unavailable_reason
            if reason and callback_id not in self._futures:
                control = self._capability_callbacks.get(callback_id)
                if control is not None and control["task"] is None:
                    self._capability_callbacks.pop(callback_id)
        if reason:
            coroutine.close()
            self._send_failure(callback_id, reason)
            return
        future.add_done_callback(
            lambda done, cid=callback_id: self._discard(cid, done)
        )

    def cancel(self, request: Mapping[str, Any]) -> None:
        if (
            request.get("protocol") != PLUGIN_GENERATION_PROTOCOL
            or request.get("generation_id") != self._generation_id
        ):
            return
        callback_id = str(request.get("callback_id") or "")
        with self._lock:
            control = self._capability_callbacks.get(callback_id)
            if control is not None:
                control["cancelled"] = True
                task = control["task"]
                if task is not None:
                    task.get_loop().call_soon_threadsafe(task.cancel)
                return
            future = self._futures.get(callback_id)
        if future is not None:
            future.cancel()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for invocation in self._invocations.values():
                invocation.revoke()
            futures = tuple(self._futures.values())
            loop = self._fallback_loop
            thread = self._fallback_thread
            stop_loop = loop is not None and not loop.is_closed() and not self._fallback_stop_requested
            if stop_loop:
                self._fallback_stop_requested = True
        for future in futures:
            future.cancel()
        if stop_loop:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                if not loop.is_closed():
                    raise RuntimeError("callback_cleanup_incomplete") from None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        if thread is not None and thread.is_alive():
            # A timed-out thread still owns its loop and pending cleanup.
            # Retain both handles so another close can confirm termination.
            raise RuntimeError("callback_cleanup_incomplete")
        with self._lock:
            for callback_id, future in tuple(self._futures.items()):
                if future.done():
                    self._futures.pop(callback_id)
            if self._fallback_thread is thread:
                self._fallback_loop = None
                self._fallback_thread = None

    def _discard(self, callback_id: str, future: Any) -> None:
        with self._lock:
            if self._futures.get(callback_id) is future:
                self._futures.pop(callback_id, None)
                control = self._capability_callbacks.get(callback_id)
                if future.cancelled() and control is not None and control["task"] is None:
                    self._capability_callbacks.pop(callback_id)

    def _ensure_fallback_loop(self) -> asyncio.AbstractEventLoop | None:
        with self._lock:
            if self._closed:
                return None
            loop = self._fallback_loop
            thread = self._fallback_thread
            if thread is not None and thread.is_alive():
                if loop is not None:
                    return loop
            else:
                self._fallback_loop_ready.clear()
                thread = threading.Thread(
                    target=self._run_fallback_loop,
                    name=f"plugin-generation-callback:{self._generation_id[:8]}",
                    daemon=True,
                )
                self._fallback_thread = thread
                try:
                    thread.start()
                except RuntimeError:
                    self._fallback_thread = None
                    return None
        if not self._fallback_loop_ready.wait(timeout=self._start_timeout_seconds):
            return None
        with self._lock:
            return None if self._closed else self._fallback_loop

    def _run_fallback_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._lock:
            run = not self._closed
            if run:
                self._fallback_loop = loop
        self._fallback_loop_ready.set()
        try:
            if run:
                loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()
