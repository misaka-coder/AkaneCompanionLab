"""Keep desktop streams observable while one synchronous engine step waits."""

from __future__ import annotations

import asyncio
from contextvars import copy_context
import logging
import threading
import time
from typing import Any, AsyncIterator, Callable

from anyio import CancelScope

from ..model_request_control import ModelRequestCancelled
from ..turn_coordination import cancellation_scope

logger = logging.getLogger("akane.think")
STREAM_PROGRESS_INTERVAL_SECONDS = 5.0


def _advance(stream: Any) -> tuple[bool, Any]:
    try:
        return True, next(stream)
    except StopIteration:
        return False, None


async def iterate_turn_events(stream_factory: Callable[[], Any]) -> AsyncIterator[dict[str, Any]]:
    """Advance once at a time; disconnects revoke work before closing it.

    A heartbeat does not advance or restart the engine. In particular, a slow
    model must not occupy the UI silently until its transport timeout expires.
    """
    stopped = threading.Event()
    pending: asyncio.Task | None = None
    started = time.perf_counter()
    context = copy_context()
    control = cancellation_scope(stopped.is_set)
    context.run(control.__enter__)
    stream = None
    try:
        # The real engine captures its own Context on creation. Construct it
        # inside the cancellation scope, so it also sees disconnect revocation.
        stream = context.run(stream_factory)
        while True:
            pending = asyncio.create_task(asyncio.to_thread(context.run, _advance, stream))
            while True:
                done, _ = await asyncio.wait({pending}, timeout=STREAM_PROGRESS_INTERVAL_SECONDS)
                if done:
                    break
                yield {
                    "type": "turn_progress",
                    "phase": "waiting_response",
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                }
            has_event, event = pending.result()
            pending = None
            if not has_event:
                return
            yield event
    finally:
        stopped.set()
        # Closing a generator while next() is running fails and can leave its
        # memory turn open. Revoke consumption, drain, then close in the same
        # Context as next(), even when the consumer closes from another Task.
        try:
            with CancelScope(shield=True):
                if pending is not None:
                    try:
                        await asyncio.shield(pending)
                    except ModelRequestCancelled:
                        pass
                    except Exception as exc:
                        logger.warning("think step cleanup failed: %s", type(exc).__name__)
                close = getattr(stream, "close", None)
                if callable(close):
                    try:
                        context.run(close)
                    except Exception as exc:
                        logger.warning("think stream cleanup failed: %s", type(exc).__name__)
        finally:
            context.run(control.__exit__, None, None, None)
