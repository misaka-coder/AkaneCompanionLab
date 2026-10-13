"""Route requests and responses over the shared JSON control lane."""

from __future__ import annotations

import json
import queue
import time
import uuid
from typing import Any, Mapping, TextIO
from ..plugin_generation_protocol import PLUGIN_GENERATION_PROTOCOL
from .contracts import PluginGenerationError


def _encode_frame(payload: Mapping[str, Any]) -> str:
    """Validate a complete JSON/UTF-8 frame before registering or writing it."""

    try:
        frame = json.dumps(
            dict(payload), ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ) + "\n"
        # TextIOWrapper rejects unpaired surrogates before a request reaches
        # the worker. Treat them as an invalid payload, not a live waiter.
        frame.encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise PluginGenerationError("plugin_generation_invocation_invalid") from exc
    return frame


class GenerationTransportMixin:
    """Route requests and responses over the shared JSON control lane."""

    def _request(
        self,
        command: str,
        *,
        timeout_seconds: float | None,
    ) -> dict[str, Any]:
        request_id, response_queue = self._send_request(command)
        try:
            response = self._next_response(response_queue, timeout_seconds)
        finally:
            self._discard_pending(request_id, response_queue)
        self._validate_response(response, expected_type="response")
        if response.get("request_id") != request_id:
            raise PluginGenerationError("plugin_generation_protocol_invalid")
        return response

    def _send_request(
        self,
        command: str,
        extra: Mapping[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> tuple[str, queue.Queue[dict[str, Any] | None]]:
        response_queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)
        request_id = request_id or uuid.uuid4().hex
        with self._write_lock:
            process = self._process
            if (
                self._closed
                or (self._stopping and command != "cancel")
                or process is None
                or process.poll() is not None
            ):
                raise PluginGenerationError("plugin_generation_unavailable")
            if process.stdin is None:
                raise PluginGenerationError("plugin_generation_unavailable")
            payload = {
                "protocol": PLUGIN_GENERATION_PROTOCOL,
                "type": "request",
                "request_id": request_id,
                "command": command,
                **dict(extra or {}),
            }
            frame = _encode_frame(payload)
            if command == "stop":
                self._stopping = True
            with self._pending_lock:
                self._pending[request_id] = response_queue
            try:
                process.stdin.write(frame)
                process.stdin.flush()
                if command in {
                    "invoke",
                    "event.invoke",
                    "event.dispatch",
                    "hook.dispatch",
                    "qq_command.dispatch",
                }:
                    with self._active_invocation_condition:
                        self._active_invocations += 1
            except (OSError, ValueError) as exc:
                self._discard_pending(request_id, response_queue)
                raise PluginGenerationError("plugin_generation_unavailable") from exc
        return request_id, response_queue

    def _finish_invocation(self) -> None:
        with self._active_invocation_condition:
            self._active_invocations -= 1
            self._active_invocation_condition.notify_all()

    def _wait_for_invocations(self, deadline: float | None) -> bool:
        with self._active_invocation_condition:
            while self._active_invocations:
                if deadline is None:
                    self._active_invocation_condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._active_invocation_condition.wait(timeout=remaining)
            return True

    def _next_response(
        self,
        response_queue: queue.Queue[dict[str, Any] | None],
        timeout_seconds: float | None,
    ) -> dict[str, Any]:
        try:
            if timeout_seconds is None:
                response = response_queue.get()
            else:
                response = response_queue.get(timeout=max(0.1, float(timeout_seconds)))
        except queue.Empty as exc:
            raise PluginGenerationError("plugin_generation_timeout") from exc
        if response is None:
            raise PluginGenerationError("plugin_generation_exited")
        return response

    def _read_responses(self, stream: TextIO) -> None:
        try:
            for line in stream:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    self._fail_pending({"protocol": "", "type": "invalid"})
                    continue
                if not isinstance(payload, Mapping):
                    self._fail_pending({"protocol": "", "type": "invalid"})
                    continue
                response = dict(payload)
                if response.get("type") == "callback_request":
                    self._callback_router.dispatch(response)
                    continue
                if response.get("type") == "callback_cancel":
                    self._callback_router.cancel(response)
                    continue
                if response.get("type") == "ready":
                    self._ready.put(response)
                    continue
                request_id = str(response.get("request_id") or "")
                with self._pending_lock:
                    response_queue = self._pending.get(request_id)
                if response_queue is not None:
                    try:
                        response_queue.put_nowait(response)
                    except queue.Full:
                        pass
        finally:
            self._ready.put(None)
            self._fail_pending(None)

    def _discard_pending(
        self,
        request_id: str,
        response_queue: queue.Queue[dict[str, Any] | None],
    ) -> None:
        with self._pending_lock:
            if self._pending.get(request_id) is response_queue:
                self._pending.pop(request_id, None)

    def _fail_pending(self, response: dict[str, Any] | None) -> None:
        with self._pending_lock:
            pending = tuple(self._pending.values())
        for response_queue in pending:
            try:
                response_queue.put_nowait(response)
            except queue.Full:
                pass

    def _send_cancel(self, target_request_id: str) -> None:
        try:
            cancel_id, cancel_queue = self._send_request(
                "cancel",
                {"target_request_id": target_request_id},
            )
        except PluginGenerationError:
            return
        self._discard_pending(cancel_id, cancel_queue)

    def _validate_response(
        self,
        response: Mapping[str, Any],
        *,
        expected_type: str,
    ) -> None:
        if (
            response.get("protocol") != PLUGIN_GENERATION_PROTOCOL
            or response.get("type") != expected_type
            or response.get("generation_id") != self.generation_id
        ):
            raise PluginGenerationError("plugin_generation_protocol_invalid")

    def _write_callback_response(self, payload: Mapping[str, Any]) -> None:
        with self._write_lock:
            process = self._process
            if (
                self._closed
                or process is None
                or process.poll() is not None
                or process.stdin is None
            ):
                return
            try:
                frame = _encode_frame(payload)
            except PluginGenerationError:
                callback_id = payload.get("callback_id")
                if payload.get("type") != "callback_response" or not isinstance(callback_id, str) or not callback_id:
                    return
                try:
                    frame = _encode_frame({
                        "protocol": PLUGIN_GENERATION_PROTOCOL,
                        "type": "callback_response",
                        "generation_id": self.generation_id,
                        "callback_id": callback_id,
                        "ok": False,
                        "reason": "callback_result_invalid",
                    })
                except PluginGenerationError:
                    return
            try:
                process.stdin.write(frame)
                process.stdin.flush()
            except (OSError, ValueError):
                return
