"""Reverse callbacks used by one PluginHost generation process.

The parent router owns host event-loop dispatch.  The worker-side port only
projects public notification values onto the existing generation control lane.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import Callable
from typing import Any, Mapping

from .plugin_api import NotificationIntent, NotificationResult
from .plugin_generation_codec import (
    PluginGenerationCodecError,
    notification_intent_from_wire,
    notification_intent_to_wire,
    notification_result_from_wire,
    notification_result_to_wire,
)
from .plugin_generation_protocol import PLUGIN_GENERATION_PROTOCOL


class GenerationHostCallbackRouter:
    """Dispatch worker callbacks to the one host-owned runtime port."""

    def __init__(
        self,
        *,
        generation_id: str,
        start_timeout_seconds: float,
        write_response: Callable[[Mapping[str, Any]], None],
    ) -> None:
        self._generation_id = str(generation_id or "")
        self._start_timeout_seconds = max(0.1, float(start_timeout_seconds))
        self._write_response = write_response
        self._notification_port: Any = None
        self._notification_loop: asyncio.AbstractEventLoop | None = None
        self._fallback_loop: asyncio.AbstractEventLoop | None = None
        self._fallback_thread: threading.Thread | None = None
        self._fallback_loop_ready = threading.Event()
        self._lock = threading.Lock()
        self._futures: dict[str, Any] = {}

    def bind_notification_port(self, port: Any) -> None:
        if not callable(getattr(port, "send", None)):
            raise TypeError("invalid_notification_port")
        self._notification_port = port
        try:
            self._notification_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._notification_loop = None

    def dispatch(self, request: Mapping[str, Any]) -> None:
        callback_id = str(request.get("callback_id") or "")
        if (
            request.get("protocol") != PLUGIN_GENERATION_PROTOCOL
            or request.get("generation_id") != self._generation_id
            or len(callback_id) != 32
            or any(char not in "0123456789abcdef" for char in callback_id)
            or request.get("callback") != "notification.send"
        ):
            self._send_failure(callback_id, "callback_protocol_invalid")
            return
        try:
            intent = notification_intent_from_wire(request.get("intent"))
        except PluginGenerationCodecError:
            self._send_failure(callback_id, "notification_protocol_invalid")
            return
        if self._notification_port is None:
            self._send_result(
                callback_id,
                NotificationResult(
                    ok=False,
                    status="not_configured",
                    reason="no_notification_port_bound",
                ),
            )
            return
        loop = self._notification_loop
        if loop is None or loop.is_closed() or not loop.is_running():
            loop = self._ensure_fallback_loop()
        if loop is None:
            self._send_failure(callback_id, "notification_host_unavailable")
            return
        with self._lock:
            if callback_id in self._futures:
                self._send_failure(callback_id, "duplicate_callback_id")
                return
        coroutine = self._deliver_notification(callback_id, intent)
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except RuntimeError:
            coroutine.close()
            self._send_failure(callback_id, "notification_host_unavailable")
            return
        with self._lock:
            self._futures[callback_id] = future
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
            future = self._futures.get(callback_id)
        if future is not None:
            future.cancel()

    def close(self) -> None:
        with self._lock:
            futures = tuple(self._futures.values())
            loop = self._fallback_loop
            thread = self._fallback_thread
        for future in futures:
            future.cancel()
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            self._futures.clear()
            self._fallback_loop = None
            self._fallback_thread = None

    async def _deliver_notification(
        self,
        callback_id: str,
        intent: NotificationIntent,
    ) -> None:
        try:
            result = await self._notification_port.send(intent)
            if not isinstance(result, NotificationResult):
                result = NotificationResult(
                    ok=False,
                    status="error",
                    reason="invalid_notification_result",
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            result = NotificationResult(
                ok=False,
                status="error",
                reason="delivery_exception",
            )
        self._send_result(callback_id, result)

    def _send_result(self, callback_id: str, result: NotificationResult) -> None:
        try:
            wire_result = notification_result_to_wire(result)
        except PluginGenerationCodecError:
            wire_result = notification_result_to_wire(
                NotificationResult(
                    ok=False,
                    status="error",
                    reason="invalid_notification_result",
                )
            )
        self._write_response(
            {
                "protocol": PLUGIN_GENERATION_PROTOCOL,
                "type": "callback_response",
                "generation_id": self._generation_id,
                "callback_id": callback_id,
                "ok": True,
                "result": wire_result,
            }
        )

    def _send_failure(self, callback_id: str, reason: str) -> None:
        if not callback_id:
            return
        self._write_response(
            {
                "protocol": PLUGIN_GENERATION_PROTOCOL,
                "type": "callback_response",
                "generation_id": self._generation_id,
                "callback_id": callback_id,
                "ok": False,
                "reason": str(reason or "notification_callback_failed"),
            }
        )

    def _discard(self, callback_id: str, future: Any) -> None:
        with self._lock:
            if self._futures.get(callback_id) is future:
                self._futures.pop(callback_id, None)

    def _ensure_fallback_loop(self) -> asyncio.AbstractEventLoop | None:
        with self._lock:
            loop = self._fallback_loop
            thread = self._fallback_thread
            if loop is not None and thread is not None and thread.is_alive():
                return loop
            self._fallback_loop_ready.clear()
            thread = threading.Thread(
                target=self._run_fallback_loop,
                name=f"plugin-generation-callback:{self._generation_id[:8]}",
                daemon=True,
            )
            self._fallback_thread = thread
            thread.start()
        if not self._fallback_loop_ready.wait(timeout=self._start_timeout_seconds):
            return None
        with self._lock:
            return self._fallback_loop

    def _run_fallback_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._lock:
            self._fallback_loop = loop
        self._fallback_loop_ready.set()
        try:
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


class GenerationNotificationPort:
    """Worker-side notification port projected through the control lane."""

    def __init__(
        self,
        *,
        generation_id: str,
        emit: Callable[[Mapping[str, Any]], None],
        pending: dict[str, asyncio.Future[Mapping[str, Any]]],
    ) -> None:
        self._generation_id = generation_id
        self._emit = emit
        self._pending = pending

    async def send(self, intent: NotificationIntent) -> NotificationResult:
        try:
            wire_intent = notification_intent_to_wire(intent)
        except PluginGenerationCodecError:
            return NotificationResult(
                ok=False,
                status="rejected",
                reason="invalid_notification_intent",
            )
        callback_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[callback_id] = future
        self._emit(
            {
                "protocol": PLUGIN_GENERATION_PROTOCOL,
                "type": "callback_request",
                "generation_id": self._generation_id,
                "callback_id": callback_id,
                "callback": "notification.send",
                "intent": wire_intent,
            }
        )
        try:
            response = await future
        except asyncio.CancelledError:
            self._emit(
                {
                    "protocol": PLUGIN_GENERATION_PROTOCOL,
                    "type": "callback_cancel",
                    "generation_id": self._generation_id,
                    "callback_id": callback_id,
                }
            )
            raise
        finally:
            self._pending.pop(callback_id, None)
        if not response.get("ok"):
            return NotificationResult(
                ok=False,
                status="error",
                reason=str(
                    response.get("reason") or "notification_callback_failed"
                ),
            )
        try:
            return notification_result_from_wire(response.get("result"))
        except PluginGenerationCodecError:
            return NotificationResult(
                ok=False,
                status="error",
                reason="invalid_notification_result",
            )


__all__ = ["GenerationHostCallbackRouter", "GenerationNotificationPort"]
