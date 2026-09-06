"""Public plugin SDK subprocess helper; no media policy or job scheduling."""

from __future__ import annotations

import asyncio
import subprocess
from concurrent.futures import ThreadPoolExecutor


def run_completed(factory):
    """Drain an async operation from a legacy synchronous call, never queue it."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(factory())).result()


async def drain(task):
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    return result, cancelled


async def terminate(process):
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 2)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


class PluginProcessRunner:
    def __init__(self):
        self.processes = set()
        self.creating = set()
        self.closed = False

    async def run(self, argv, *, capture=False, timeout=1800):
        if self.closed:
            raise RuntimeError("plugin_process_runner_closed")
        # Shield creation too: cancellation during process creation must not
        # leave an untracked child, notably with Windows subprocess transport.
        creation = asyncio.create_task(
            asyncio.create_subprocess_exec(
                *map(str, argv),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE if capture else asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        )
        self.creating.add(creation)
        try:
            process, cancelled = await drain(creation)
            self.processes.add(process)
        finally:
            self.creating.discard(creation)
        reader = asyncio.create_task(process.communicate())
        try:
            if cancelled or self.closed:
                raise asyncio.CancelledError
            stdout, _ = await asyncio.wait_for(asyncio.shield(reader), timeout)
            return process.returncode, stdout or b""
        except (asyncio.CancelledError, asyncio.TimeoutError):
            await drain(asyncio.create_task(terminate(process)))
            await drain(reader)
            raise
        finally:
            self.processes.discard(process)

    async def aclose(self):
        self.closed = True
        processes = set(self.processes)
        for creation in tuple(self.creating):
            try:
                process, _ = await drain(creation)
                processes.add(process)
            except OSError:
                # No child was created; the caller receives the startup error.
                pass
        processes.update(self.processes)
        tasks = [asyncio.create_task(terminate(process)) for process in processes]
        for task in tasks:
            await drain(task)


async def stop_process(process):
    """Stop one owned child, draining cleanup even under repeated cancellation."""
    _, cancelled = await drain(asyncio.create_task(terminate(process)))
    if cancelled:
        raise asyncio.CancelledError
