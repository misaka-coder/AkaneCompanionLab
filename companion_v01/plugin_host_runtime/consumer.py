"""Cross-event-loop invocation bridge with cancellation acknowledgement after drain."""

from __future__ import annotations

from typing import Any, Mapping
import asyncio
import threading

from capcore import CapabilityResult, InvocationContext

from .timing import _await_with_optional_timeout, _combined_invocation_timeout


class PluginConsumerMixin:
    async def invoke_from_consumer(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        *,
        context: InvocationContext,
    ) -> CapabilityResult:
        """Run an invocation on the lifecycle loop from a synchronous Engine worker.

        Plugin adapters are activated on the FastAPI lifecycle loop and may own
        loop-bound async resources. Engine tool execution is synchronous and
        normally runs in a worker thread, so executing ``invoke`` through a new
        per-thread event loop would violate that ownership boundary.
        """

        runtime_loop = self._runtime_loop
        if runtime_loop is None or runtime_loop.is_closed() or not runtime_loop.is_running():
            return CapabilityResult(
                is_error=True,
                status="host_unavailable",
                reason="host_unavailable",
            )
        current_loop = asyncio.get_running_loop()
        if current_loop is runtime_loop:
            return await self.invoke(capability_id, args, context=context)
        cancel_requested = threading.Event()
        invocation_tasks: list[asyncio.Task] = []

        async def invoke_on_runtime() -> CapabilityResult:
            invocation_tasks.append(asyncio.current_task())
            if cancel_requested.is_set():
                raise asyncio.CancelledError()
            return await self.invoke(capability_id, args, context=context)

        invocation = invoke_on_runtime()
        try:
            concurrent_future = asyncio.run_coroutine_threadsafe(
                invocation,
                runtime_loop,
            )
        except RuntimeError:
            invocation.close()
            return CapabilityResult(
                is_error=True,
                status="host_unavailable",
                reason="host_unavailable",
            )
        consumer_future = asyncio.wrap_future(concurrent_future)

        async def cancel_and_drain() -> None:
            # Cancelling the concurrent Future itself acknowledges immediately,
            # before the lifecycle-loop task has released resources. Cancel the
            # actual task and retain its Future until that task really finishes.
            cancel_requested.set()

            def cancel_task() -> None:
                if invocation_tasks:
                    invocation_tasks[0].cancel()

            runtime_loop.call_soon_threadsafe(cancel_task)
            while not consumer_future.done():
                try:
                    await asyncio.shield(consumer_future)
                except asyncio.CancelledError:
                    continue

        try:
            return await _await_with_optional_timeout(
                asyncio.shield(consumer_future),
                timeout_seconds=_combined_invocation_timeout(
                    self._invoke_timeout_seconds,
                    self._managed_artifact_timeout_seconds,
                ),
            )
        except asyncio.CancelledError:
            await cancel_and_drain()
            return consumer_future.result()
        except (TimeoutError, asyncio.TimeoutError):
            await cancel_and_drain()
            if not consumer_future.cancelled():
                return consumer_future.result()
            return CapabilityResult(
                is_error=True,
                status="error",
                reason="plugin_invoke_timeout",
            )
        except Exception:
            return CapabilityResult(
                is_error=True,
                status="error",
                reason="plugin_invoke_failed",
            )
