"""Keep a stopped turn from waiting for a blocked synchronous model transport."""

from __future__ import annotations

from contextvars import copy_context
from queue import Empty, Full, Queue
import threading
from typing import Any, Callable


class ModelRequestCancelled(RuntimeError):
    """Host control flow: a revoked turn cannot consume or retry a request."""


class _CancellableRequest:
    def __init__(self, create: Callable[..., Any], payload: dict[str, Any], cancelled: Callable[[], bool]):
        self._cancelled = cancelled
        self._streaming = bool(payload.get("stream"))
        self._events: Queue[tuple[str, Any]] = Queue(maxsize=1)
        self._stop = threading.Event()
        self._close_lock = threading.Lock()
        self._response: Any = None
        self._response_closed = False
        self._worker = threading.Thread(
            target=copy_context().run,
            args=(self._read, create, payload),
            name="model-request",
            daemon=True,
        )
        self._worker.start()

    def _publish(self, kind: str, value: Any = None) -> bool:
        while not self._stop.is_set():
            try:
                self._events.put((kind, value), timeout=0.05)
                return True
            except Full:
                continue
        return False

    def _read(self, create: Callable[..., Any], payload: dict[str, Any]) -> None:
        try:
            if self._stop.is_set() or self._cancelled():
                raise ModelRequestCancelled("turn_scope_revoked")
            self._response = create(**payload)
            if not self._publish("response", self._response) or not self._streaming:
                return
            for chunk in self._response:
                if not self._publish("chunk", chunk):
                    return
            self._publish("done")
        except Exception as exc:
            self._publish("error", exc)
        finally:
            if self._streaming or self._stop.is_set():
                self._close_response()

    def _receive(self) -> tuple[str, Any]:
        while True:
            if self._cancelled():
                self.close()
                raise ModelRequestCancelled("turn_scope_revoked")
            try:
                kind, value = self._events.get(timeout=0.05)
            except Empty:
                continue
            if self._cancelled():
                self.close()
                raise ModelRequestCancelled("turn_scope_revoked")
            if kind == "error":
                raise value
            return kind, value

    def open(self) -> Any:
        _, response = self._receive()
        return self if self._streaming else response

    def __iter__(self):
        return self

    def __next__(self) -> Any:
        if self._stop.is_set():
            raise StopIteration
        kind, chunk = self._receive()
        if kind == "done":
            raise StopIteration
        return chunk

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)

    def _close_response(self) -> None:
        with self._close_lock:
            if self._response is None or self._response_closed:
                return
            close = getattr(self._response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    # A running generator can reject close; the reader retries
                    # after its outstanding read returns.
                    return
            self._response_closed = True

    def close(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        if self._response is not None and not self._response_closed:
            # Some synchronous transports block even in close(). Their cleanup
            # must not keep a stopped turn ahead of the next addressed message.
            threading.Thread(target=self._close_response, name="model-request-close", daemon=True).start()


def dispatch_model_request(
    create: Callable[..., Any], payload: dict[str, Any], cancelled: Callable[[], bool] | None
) -> Any:
    if cancelled is None:
        return create(**payload)
    if cancelled():
        raise ModelRequestCancelled("turn_scope_revoked")
    return _CancellableRequest(create, payload, cancelled).open()
