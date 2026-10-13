"""Session-scoped server-local offers with TTL-cached capability_status probes."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Mapping


_SERVER_OFFER_READY_TTL: float = 15.0
_SERVER_OFFER_UNAVAILABLE_TTL: float = 5.0
_SERVER_OFFER_READY_STATUSES: frozenset[str] = frozenset(
    {"available", "degraded", "ok", "ready"}
)


class ServerLocalOfferIndex:
    """Lightweight per-process offer index backed by capability_status() probes.

    Handlers are registered by tool_id. A handler may explicitly return an
    ``offered`` configuration fact when transient readiness must not control
    execution authority. Other handlers retain the enabled/ready contract.
    """

    def __init__(
        self,
        *,
        ready_ttl_seconds: float = _SERVER_OFFER_READY_TTL,
        unavailable_ttl_seconds: float = _SERVER_OFFER_UNAVAILABLE_TTL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._handlers: dict[str, Any] = {}
        self._handler_generations: dict[str, object] = {}
        self._cache: dict[tuple[str, str, str, str], tuple[float, bool]] = {}
        self._probes: dict[tuple[str, str, str, str], object] = {}
        self._lock = threading.RLock()
        self._ready_ttl = max(1.0, float(ready_ttl_seconds))
        self._unavailable_ttl = max(1.0, float(unavailable_ttl_seconds))
        self._clock = clock

    def register(self, tool_id: str, handler: Any) -> None:
        tool_id = str(tool_id or "").strip()
        if not tool_id or handler is None:
            return
        with self._lock:
            self._handlers[tool_id] = handler
            self._invalidate_locked({tool_id})

    def replace_handlers(self, handlers: Mapping[str, Any]) -> None:
        replacement = {
            str(tool_id or "").strip(): handler
            for tool_id, handler in dict(handlers or {}).items()
            if str(tool_id or "").strip() and handler is not None
        }
        with self._lock:
            changed = {
                tool_id
                for tool_id in set(self._handlers).union(replacement)
                if self._handlers.get(tool_id) is not replacement.get(tool_id)
            }
            self._handlers = replacement
            if changed:
                self._invalidate_locked(changed)

    def _invalidate_locked(self, tool_ids: set[str]) -> None:
        """Invalidate cached and in-flight results while holding the index lock."""
        for tool_id in tool_ids:
            if tool_id in self._handlers:
                # A fresh token also fences removal/reinstallation of the same
                # handler object and explicit re-registration after config edits.
                self._handler_generations[tool_id] = object()
            else:
                self._handler_generations.pop(tool_id, None)
        self._cache = {key: value for key, value in self._cache.items() if key[0] not in tool_ids}
        self._probes = {key: value for key, value in self._probes.items() if key[0] not in tool_ids}

    def is_offered(
        self,
        tool_id: str,
        *,
        profile_user_id: str = "",
        session_id: str = "",
        client_mode: str = "",
    ) -> bool:
        tool_id = str(tool_id or "").strip()
        if not tool_id:
            return False
        cache_key = (
            tool_id,
            str(profile_user_id or ""),
            str(session_id or ""),
            str(client_mode or ""),
        )
        with self._lock:
            handler = self._handlers.get(tool_id)
            if handler is None:
                return False
            now = self._clock()
            cached = self._cache.get(cache_key)
            if cached is not None and cached[0] > now:
                return cached[1]
            generation = self._handler_generations[tool_id]
            probe_token = object()
            self._probes[cache_key] = probe_token
        # Probe outside the lock to avoid blocking other callers.
        offered = self._probe(handler, profile_user_id=profile_user_id, session_id=session_id, client_mode=client_mode)
        ttl = self._ready_ttl if offered else self._unavailable_ttl
        with self._lock:
            if self._handler_generations.get(tool_id) is not generation:
                return False
            if self._probes.get(cache_key) is not probe_token:
                # A newer probe owns this scope. Reuse its live result if it
                # has finished; otherwise fail closed until it completes.
                cached = self._cache.get(cache_key)
                return cached is not None and cached[0] > self._clock() and cached[1]
            self._probes.pop(cache_key, None)
            self._cache[cache_key] = (self._clock() + ttl, offered)
        return offered

    def _probe(
        self,
        handler: Any,
        *,
        profile_user_id: str,
        session_id: str,
        client_mode: str,
    ) -> bool:
        try:
            fn = getattr(handler, "capability_status", None)
            if not callable(fn):
                return True
            import inspect
            sig = inspect.signature(fn)
            params = set(sig.parameters)
            accepts_kwargs = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in sig.parameters.values()
            )
            kwargs: dict[str, Any] = {}
            if accepts_kwargs or "profile_user_id" in params:
                kwargs["profile_user_id"] = profile_user_id
            if accepts_kwargs or "session_id" in params:
                kwargs["session_id"] = session_id
            if accepts_kwargs or "client_mode" in params:
                kwargs["client_mode"] = client_mode
            result = fn(**kwargs)
        except Exception:
            return False
        if isinstance(result, bool):
            return result
        if isinstance(result, Mapping):
            if isinstance(result.get("offered"), bool):
                return bool(result.get("offered"))
            if result.get("enabled") is not True:
                return False
            status = str(result.get("status") or "").strip().lower()
            return status in _SERVER_OFFER_READY_STATUSES
        return False
