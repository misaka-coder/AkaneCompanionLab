"""Small shared copy primitive for private plugin input/output handoffs."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any, Callable


class PluginFileCopyError(RuntimeError):
    pass


def copy_file(
    source: Path,
    target: Path,
    *,
    expected_size: int,
    cancelled: threading.Event,
) -> None:
    copied = 0
    with source.open("rb") as incoming, target.open("wb") as outgoing:
        while chunk := incoming.read(1024 * 1024):
            if cancelled.is_set():
                raise PluginFileCopyError("copy_cancelled")
            copied += len(chunk)
            if copied > expected_size:
                raise PluginFileCopyError("source_changed")
            outgoing.write(chunk)
    if copied != expected_size:
        raise PluginFileCopyError("source_changed")


async def run_cancellable_copy(copy: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Cancellation never leaves a writer running after its caller cleans up."""
    cancelled = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(copy, *args, cancelled=cancelled, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancelled.set()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise
