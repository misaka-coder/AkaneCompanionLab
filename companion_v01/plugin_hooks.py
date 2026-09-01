"""Host-owned lifecycle Hook broker for trusted in-process plugins.

Hooks observe immutable snapshots on the PluginHost lifecycle loop. Only the
explicit outbound text-decoration contract can affect a plan; a slow or broken
handler never changes the host outcome.
"""

from __future__ import annotations

import asyncio
import re
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Callable

from .plugin_api import (
    AFTER_DELIVERY_HOOK,
    AFTER_TOOL_CALL_HOOK,
    BEFORE_OUTBOUND_PLAN_HOOK,
    BEFORE_TOOL_CALL_HOOK,
    PluginHookEnvelope,
    PluginHookHandler,
    PluginHookResult,
    PluginOutboundDecoration,
)


DEFAULT_HOOK_HANDLER_TIMEOUT_SECONDS = 1.0
SUPPORTED_TOOL_HOOK_TYPES = frozenset({BEFORE_TOOL_CALL_HOOK, AFTER_TOOL_CALL_HOOK})
SUPPORTED_OUTBOUND_HOOK_TYPES = frozenset({BEFORE_OUTBOUND_PLAN_HOOK, AFTER_DELIVERY_HOOK})
SUPPORTED_HOOK_TYPES = SUPPORTED_TOOL_HOOK_TYPES | SUPPORTED_OUTBOUND_HOOK_TYPES
_DIAGNOSTIC_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass(frozen=True, slots=True)
class _PluginHookRegistration:
    plugin_id: str
    hook_type: str
    handler: PluginHookHandler


@dataclass(frozen=True, slots=True)
class PluginHookDispatchResult:
    """Aggregate diagnostics plus validated outbound decorations."""

    ok: bool
    status: str
    diagnostics: tuple[tuple[str, str], ...] = ()
    failures: tuple[tuple[str, str], ...] = ()
    outbound_decorations: tuple[tuple[str, PluginOutboundDecoration], ...] = ()


@dataclass(frozen=True, slots=True)
class _PluginHookHandlerOutcome:
    diagnostics: tuple[str, ...]
    outbound_decoration: PluginOutboundDecoration | None = None


class PluginHookBroker:
    """Dispatch exact lifecycle Hooks with failure and loop isolation."""

    def __init__(
        self,
        registrations: tuple[_PluginHookRegistration, ...],
        *,
        handler_timeout_seconds: float = DEFAULT_HOOK_HANDLER_TIMEOUT_SECONDS,
        availability_provider: Callable[[], bool] | None = None,
        registrations_provider: Callable[[], tuple[_PluginHookRegistration, ...]] | None = None,
        runtime_loop_provider: Callable[[], asyncio.AbstractEventLoop | None] | None = None,
    ) -> None:
        self._handler_timeout_seconds = max(0.01, float(handler_timeout_seconds))
        self._availability_provider = availability_provider or (lambda: True)
        self._static_registrations = tuple(registrations)
        self._registrations_provider = registrations_provider
        self._runtime_loop_provider = runtime_loop_provider or (lambda: None)
        self._status_lock = threading.Lock()
        self._dispatch_count = 0
        self._dispatch_failure_count = 0
        self._diagnostic_count = 0
        self._last_diagnostics: tuple[tuple[str, str], ...] = ()
        self._last_failures: tuple[tuple[str, str], ...] = ()

    @property
    def registered_hook_types(self) -> tuple[str, ...]:
        return tuple(sorted({item.hook_type for item in self._current_registrations()}))

    def observes(self, hook_type: str) -> bool:
        normalized = _normalize_hook_type(hook_type)
        return any(item.hook_type == normalized for item in self._current_registrations())

    async def dispatch(self, hook: PluginHookEnvelope) -> PluginHookDispatchResult:
        if not isinstance(hook, PluginHookEnvelope):
            return self._record(PluginHookDispatchResult(False, "invalid_hook", failures=(("host", "invalid_hook"),)))
        hook_type = _normalize_hook_type(hook.hook_type)
        if not hook.hook_id or not hook_type:
            return self._record(PluginHookDispatchResult(False, "invalid_hook", failures=(("host", "invalid_hook"),)))
        try:
            available = bool(self._availability_provider())
        except Exception:
            available = False
        if not available:
            return self._record(PluginHookDispatchResult(False, "host_unavailable"))

        registrations = tuple(
            item for item in self._current_registrations() if item.hook_type == hook_type
        )
        if not registrations:
            return self._record(PluginHookDispatchResult(True, "unobserved"))
        outcomes = await asyncio.gather(
            *(self._call_handler(item, hook) for item in registrations),
            return_exceptions=False,
        )
        diagnostics: list[tuple[str, str]] = []
        failures: list[tuple[str, str]] = []
        outbound_decorations: list[tuple[str, PluginOutboundDecoration]] = []
        for registration, outcome in zip(registrations, outcomes):
            if isinstance(outcome, str):
                failures.append((registration.plugin_id, outcome))
                continue
            diagnostics.extend((registration.plugin_id, code) for code in outcome.diagnostics)
            if outcome.outbound_decoration is not None:
                outbound_decorations.append((registration.plugin_id, outcome.outbound_decoration))
        return self._record(
            PluginHookDispatchResult(
                ok=not failures,
                status="observed" if not failures else "partially_observed",
                diagnostics=tuple(diagnostics),
                failures=tuple(failures),
                outbound_decorations=tuple(outbound_decorations),
            )
        )

    def dispatch_from_consumer(self, hook: PluginHookEnvelope) -> PluginHookDispatchResult:
        """Dispatch from the synchronous Engine worker onto PluginHost's loop."""

        try:
            runtime_loop = self._runtime_loop_provider()
        except Exception:
            runtime_loop = None
        if runtime_loop is None or runtime_loop.is_closed() or not runtime_loop.is_running():
            return self._record(PluginHookDispatchResult(False, "host_unavailable"))
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is runtime_loop:
            return self._record(
                PluginHookDispatchResult(
                    False,
                    "wrong_execution_context",
                    failures=(("host", "hook_requires_worker_thread"),),
                )
            )
        try:
            future = asyncio.run_coroutine_threadsafe(self.dispatch(hook), runtime_loop)
        except RuntimeError:
            return self._record(PluginHookDispatchResult(False, "host_unavailable"))
        try:
            return future.result(timeout=self._handler_timeout_seconds + 0.25)
        except FutureTimeoutError:
            future.cancel()
            return self._record(
                PluginHookDispatchResult(
                    False,
                    "dispatch_timeout",
                    failures=(("host", "dispatch_timeout"),),
                )
            )
        except Exception:
            return self._record(
                PluginHookDispatchResult(
                    False,
                    "dispatch_failed",
                    failures=(("host", "dispatch_failed"),),
                )
            )

    def status_snapshot(self) -> dict[str, object]:
        with self._status_lock:
            return {
                "dispatch_count": self._dispatch_count,
                "dispatch_failure_count": self._dispatch_failure_count,
                "diagnostic_count": self._diagnostic_count,
                "last_diagnostics": [
                    {"plugin_id": plugin_id, "code": code}
                    for plugin_id, code in self._last_diagnostics
                ],
                "last_failures": [
                    {"plugin_id": plugin_id, "reason": reason}
                    for plugin_id, reason in self._last_failures
                ],
            }

    async def _call_handler(
        self,
        registration: _PluginHookRegistration,
        hook: PluginHookEnvelope,
    ) -> _PluginHookHandlerOutcome | str:
        try:
            result = await asyncio.wait_for(
                registration.handler.handle_hook(hook),
                timeout=self._handler_timeout_seconds,
            )
        except (asyncio.TimeoutError, TimeoutError):
            return "handler_timeout"
        except asyncio.CancelledError:
            raise
        except Exception:
            return "handler_exception"
        if not isinstance(result, PluginHookResult):
            return "invalid_handler_result"
        if not isinstance(result.diagnostics, tuple):
            return "invalid_diagnostics"
        diagnostics: list[str] = []
        for code in result.diagnostics:
            normalized = str(code or "").strip().lower()
            if _DIAGNOSTIC_PATTERN.fullmatch(normalized) is None:
                return "invalid_diagnostics"
            diagnostics.append(normalized)
        decoration = result.outbound_decoration
        if decoration is not None:
            if hook.hook_type != BEFORE_OUTBOUND_PLAN_HOOK:
                return "unexpected_outbound_decoration"
            if not isinstance(decoration, PluginOutboundDecoration):
                return "invalid_outbound_decoration"
            if not isinstance(decoration.text_prefix, str) or not isinstance(decoration.text_suffix, str):
                return "invalid_outbound_decoration"
        return _PluginHookHandlerOutcome(tuple(diagnostics), decoration)

    def _current_registrations(self) -> tuple[_PluginHookRegistration, ...]:
        if self._registrations_provider is not None:
            try:
                provided = self._registrations_provider()
            except Exception:
                provided = ()
            if isinstance(provided, tuple):
                try:
                    available = bool(self._availability_provider())
                except Exception:
                    available = False
                if available:
                    self._static_registrations = provided
        return self._static_registrations

    def _record(self, result: PluginHookDispatchResult) -> PluginHookDispatchResult:
        with self._status_lock:
            self._dispatch_count += 1
            self._diagnostic_count += len(result.diagnostics)
            if result.diagnostics:
                self._last_diagnostics = tuple(result.diagnostics)
            if result.failures or not result.ok:
                self._dispatch_failure_count += 1
                self._last_failures = tuple(result.failures)
        return result


def _normalize_hook_type(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in SUPPORTED_HOOK_TYPES else ""


__all__ = [
    "DEFAULT_HOOK_HANDLER_TIMEOUT_SECONDS",
    "PluginHookBroker",
    "PluginHookDispatchResult",
    "SUPPORTED_HOOK_TYPES",
    "SUPPORTED_OUTBOUND_HOOK_TYPES",
    "SUPPORTED_TOOL_HOOK_TYPES",
    "_PluginHookRegistration",
]
