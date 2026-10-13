"""Serialized local inference and observable, idle-only CUDA cache cleanup."""

from __future__ import annotations

import sys
import threading
from contextlib import contextmanager
from typing import Any, Callable


class LocalEmbeddingRuntime:
    def __init__(self, model: Any, *, device: str, batch_size: int, idle_seconds: float):
        self._lock = threading.RLock()
        self._closing = threading.Event()
        self._closed = False
        self._timer: threading.Timer | None = None
        self._generation = 0
        self._idle_seconds = idle_seconds
        self._cuda = sys.modules.get("torch") if device.startswith("cuda") else None
        self._device = device
        self._state: dict[str, Any] = {
            "device": device,
            "precision": "unknown",
            "model_weight_bytes": 0,
            "batch_size": batch_size,
            "effective_batch_size": batch_size,
            "active_calls": 0,
            "calls": 0,
            "failed_calls": 0,
            "oom_retries": 0,
            "cache_cleanup_count": 0,
            "cache_cleanup_status": "not_needed",
            "last_error_type": "",
            "cuda_metrics_scope": "process",
            "cuda_metrics_available": False,
        }
        parameters = getattr(model, "parameters", None)
        if callable(parameters):
            for parameter in parameters():
                self._state["precision"] = str(parameter.dtype).removeprefix("torch.")
                self._state["model_weight_bytes"] += parameter.numel() * parameter.element_size()
        self._sample_cuda()
        self._schedule_cleanup()

    @property
    def batch_size(self) -> int:
        return int(self._state["effective_batch_size"])

    @contextmanager
    def inference(self):
        with self._lock:
            if self._closing.is_set():
                raise RuntimeError("embedding_provider_closed")
            self._cancel_timer()
            self._state["active_calls"] = 1
            self._state["calls"] += 1
            try:
                yield
            except BaseException as exc:
                self._state["failed_calls"] += 1
                self._state["last_error_type"] = type(exc).__name__
                raise
            finally:
                self._state["active_calls"] = 0
                self._sample_cuda()
                self._schedule_cleanup()

    def is_cuda_oom(self, error: BaseException) -> bool:
        oom_type = getattr(self._cuda, "OutOfMemoryError", None)
        return isinstance(oom_type, type) and isinstance(error, oom_type)

    def reduce_batch(self, batch_size: int) -> None:
        self._state["oom_retries"] += 1
        self._state["effective_batch_size"] = batch_size
        self._cleanup_cuda_cache()

    def snapshot(self) -> dict[str, Any]:
        # Diagnostics must not hold the async HTTP loop behind a long encode.
        acquired = self._lock.acquire(blocking=False)
        try:
            if acquired:
                self._sample_cuda()
            state = dict(self._state)
            state["status"] = (
                "closed" if self._closed else "closing" if self._closing.is_set()
                else "busy" if state["active_calls"] else "ready"
            )
            return state
        finally:
            if acquired:
                self._lock.release()

    def close(self, release_model: Callable[[], None], *, timeout: float) -> bool:
        self._closing.set()
        if not self._lock.acquire(timeout=max(0.0, timeout)):
            return False
        try:
            if self._closed:
                return True
            self._cancel_timer()
            release_model()
            self._state["model_weight_bytes"] = 0
            self._cleanup_cuda_cache()
            self._sample_cuda()
            self._closed = True
            return True
        finally:
            self._lock.release()

    def _sample_cuda(self) -> None:
        cuda = getattr(self._cuda, "cuda", None)
        if cuda is None:
            return
        try:
            if not cuda.is_initialized():
                return
            self._state.update(
                cuda_allocated_bytes=int(cuda.memory_allocated(self._device)),
                cuda_reserved_bytes=int(cuda.memory_reserved(self._device)),
                cuda_peak_allocated_bytes=int(cuda.max_memory_allocated(self._device)),
                cuda_peak_reserved_bytes=int(cuda.max_memory_reserved(self._device)),
                cuda_metrics_available=True,
            )
        except Exception as exc:
            self._state["cuda_metrics_available"] = False
            self._state["last_error_type"] = type(exc).__name__

    def _cancel_timer(self) -> None:
        self._generation += 1
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _schedule_cleanup(self) -> None:
        if self._cuda is None or self._idle_seconds <= 0 or self._closing.is_set():
            return
        self._cancel_timer()
        generation = self._generation
        self._state["cache_cleanup_status"] = "pending"
        self._timer = threading.Timer(self._idle_seconds, self._idle_cleanup, args=(generation,))
        self._timer.daemon = True
        self._timer.start()

    def _idle_cleanup(self, generation: int) -> None:
        with self._lock:
            if generation != self._generation or self._closing.is_set():
                return
            self._timer = None
            self._cleanup_cuda_cache()
            self._sample_cuda()

    def _cleanup_cuda_cache(self) -> None:
        cuda = getattr(self._cuda, "cuda", None)
        if cuda is None:
            return
        try:
            if not cuda.is_initialized():
                return
            # Never release live model tensors or call GC on the turn path.
            with cuda.device(self._device):
                cuda.empty_cache()
            self._state["cache_cleanup_count"] += 1
            self._state["cache_cleanup_status"] = "ok"
        except Exception as exc:
            self._state["cache_cleanup_status"] = "failed"
            self._state["last_error_type"] = type(exc).__name__
