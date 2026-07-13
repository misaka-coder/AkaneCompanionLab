from __future__ import annotations

from collections.abc import Mapping
import inspect
import threading
import time
from typing import Any


_READY_STATUSES = {"available", "degraded", "ok", "ready"}
_UNAVAILABLE_STATUSES = {
    "disabled",
    "disconnected",
    "error",
    "missing_command",
    "missing_config",
    "missing_executor",
    "missing_model",
    "permission_denied",
    "rate_limited",
    "unavailable",
    "unsupported",
}


class ToolReadinessGate:
    """Cache-aware gate between registered handlers and model-visible tools."""

    def __init__(
        self,
        *,
        ready_ttl_seconds: float = 15.0,
        unavailable_ttl_seconds: float = 5.0,
        clock=time.monotonic,
    ) -> None:
        self.ready_ttl_seconds = max(0.5, float(ready_ttl_seconds))
        self.unavailable_ttl_seconds = max(0.5, float(unavailable_ttl_seconds))
        self._clock = clock
        self._cache: dict[tuple[int, str, str, str], tuple[float, dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def filter_handlers(
        self,
        handlers: Mapping[str, Any],
        *,
        profile_user_id: str = "",
        session_id: str = "",
        client_mode: str = "",
    ) -> dict[str, Any]:
        return {
            name: handler
            for name, handler in handlers.items()
            if self.status(
                handler,
                profile_user_id=profile_user_id,
                session_id=session_id,
                client_mode=client_mode,
            )["enabled"]
        }

    def status(
        self,
        handler: Any,
        *,
        profile_user_id: str = "",
        session_id: str = "",
        client_mode: str = "",
    ) -> dict[str, Any]:
        status_fn = getattr(handler, "capability_status", None)
        if not callable(status_fn):
            return {"enabled": True, "status": "ready", "reason": "status_check_not_required"}

        key = (
            id(handler),
            str(profile_user_id or "").strip(),
            str(session_id or "").strip(),
            str(client_mode or "").strip().lower(),
        )
        now = float(self._clock())
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and cached[0] > now:
                return dict(cached[1])

        try:
            raw_status = self._call_status(
                status_fn,
                profile_user_id=key[1],
                session_id=key[2],
                client_mode=key[3],
            )
            normalized = self._normalize_status(raw_status)
        except Exception as exc:
            normalized = {
                "enabled": False,
                "status": "unavailable",
                "reason": f"capability_status_failed:{type(exc).__name__}",
            }

        default_ttl = self.ready_ttl_seconds if normalized["enabled"] else self.unavailable_ttl_seconds
        ttl = self._bounded_ttl(normalized.get("cache_ttl_seconds"), default=default_ttl)
        with self._lock:
            self._cache[key] = (now + ttl, dict(normalized))
            if len(self._cache) > 512:
                self._cache = {cache_key: value for cache_key, value in self._cache.items() if value[0] > now}
        return normalized

    @staticmethod
    def _call_status(status_fn: Any, **context: str) -> Any:
        try:
            signature = inspect.signature(status_fn)
        except (TypeError, ValueError):
            return status_fn()
        parameters = signature.parameters
        if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
            return status_fn(**context)
        supported = {name: value for name, value in context.items() if name in parameters}
        return status_fn(**supported)

    @staticmethod
    def _normalize_status(raw_status: Any) -> dict[str, Any]:
        if isinstance(raw_status, bool):
            return {
                "enabled": raw_status,
                "status": "ready" if raw_status else "unavailable",
                "reason": "",
            }
        if not isinstance(raw_status, Mapping):
            return {"enabled": False, "status": "unavailable", "reason": "invalid_capability_status"}

        status = str(raw_status.get("status") or "").strip().lower()
        explicit_enabled = raw_status.get("enabled")
        enabled = bool(explicit_enabled) if explicit_enabled is not None else status in _READY_STATUSES
        if status in _UNAVAILABLE_STATUSES:
            enabled = False
        elif status in _READY_STATUSES and explicit_enabled is not False:
            enabled = True
        elif not status:
            status = "ready" if enabled else "unavailable"

        normalized = {
            "enabled": enabled,
            "status": status,
            "reason": str(raw_status.get("reason") or "").strip()[:240],
        }
        if raw_status.get("cache_ttl_seconds") is not None:
            normalized["cache_ttl_seconds"] = raw_status.get("cache_ttl_seconds")
        return normalized

    @staticmethod
    def _bounded_ttl(value: Any, *, default: float) -> float:
        try:
            ttl = float(value)
        except (TypeError, ValueError):
            ttl = default
        return max(0.5, min(300.0, ttl))


__all__ = ["ToolReadinessGate"]
