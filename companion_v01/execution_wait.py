"""One foreground deadline; providers retain their execution timeout/lifecycle."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
import time

_deadline = ContextVar("akane_tool_batch_deadline", default=None)


def batch_wait_seconds():
    try:
        return max(0.0, min(10.0, float(os.getenv("AKANE_TOOL_BATCH_WAIT_SECONDS", "8"))))
    except (ValueError, TypeError):
        return 8.0


@contextmanager
def batch_wait_budget(seconds=None):
    deadline = time.monotonic() + (batch_wait_seconds() if seconds is None else max(0.0, seconds))
    outer = _deadline.get()
    if outer is not None:
        deadline = min(deadline, outer)
    token = _deadline.set(deadline)
    try:
        yield deadline
    finally:
        _deadline.reset(token)


def foreground_wait_seconds(requested):
    deadline = _deadline.get()
    return float(requested) if deadline is None else max(0.0, min(float(requested), deadline - time.monotonic()))
