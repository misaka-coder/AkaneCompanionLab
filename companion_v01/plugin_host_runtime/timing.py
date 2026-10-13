"""Optional timeouts and cancellation-aware activation bounds."""

from __future__ import annotations

from typing import Any
import asyncio

from .models import _ActivationFailure


def _combined_invocation_timeout(
    invoke_timeout_seconds: float | None,
    managed_artifact_timeout_seconds: float,
) -> float | None:
    if invoke_timeout_seconds is None or managed_artifact_timeout_seconds <= 0:
        return None
    return invoke_timeout_seconds + managed_artifact_timeout_seconds + 0.5


async def _await_with_optional_timeout(awaitable: Any, *, timeout_seconds: float | None) -> Any:
    if timeout_seconds is None:
        return await awaitable
    return await asyncio.wait_for(awaitable, timeout=timeout_seconds)


async def _bounded_adapter_call(awaitable: Any, *, timeout_seconds: float, failure_reason: str) -> Any:
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout_seconds)
    except asyncio.CancelledError:
        if _current_task_is_cancelling():
            raise
        raise _ActivationFailure(failure_reason) from None
    except (TimeoutError, asyncio.TimeoutError):
        raise _ActivationFailure(failure_reason) from None
    except Exception:
        raise _ActivationFailure(failure_reason) from None


def _current_task_is_cancelling() -> bool:
    task = asyncio.current_task()
    return bool(task is not None and task.cancelling())
