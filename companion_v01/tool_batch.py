"""Shared bounded scheduling; tool execution and authorization remain in the host."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import replace
import json
import logging
import time
from typing import Any, Callable

from .tool_handlers.core import ToolExecutionResult
from .tool_invocation import TOOL_INVOCATION_ID_FIELD
from .tool_execution_policy import tool_parallel_limit


logger = logging.getLogger("akane.tool_batch")


def cancelled_tool_result(tool_type):
    return ToolExecutionResult(
        tool_type=tool_type,
        stream_events=[{"type": "tool_execution_cancelled", "tool_type": tool_type,
                        "status": "cancelled", "reason": "cancelled_before_execution"}],
        followup_context="<tool_use_error>Cancelled before execution; this call did not run.</tool_use_error>",
    )


def execute_tool_batch(
    calls: list[dict[str, Any]],
    *,
    execute: Callable[[dict[str, Any]], ToolExecutionResult],
    handler_for: Callable[[dict[str, Any]], Any],
    cancelled: Callable[[], bool] | None = None,
    scope_id: str = "",
) -> list[ToolExecutionResult]:
    """Parallelize adjacent declared reads; all other calls are order barriers.

    Return exactly one result per call in request order, including calls skipped
    after cancellation. A read after a write must observe that completed write.
    This is batch scheduling, not a filesystem lock across independent tasks.
    """
    def run(call):
        tool_type = str(call.get("type") or "unknown")
        started_at, started = time.time(), time.monotonic()
        try:
            if cancelled is not None and cancelled():
                result = cancelled_tool_result(tool_type)
            else:
                result = execute(call)
                if not isinstance(result, ToolExecutionResult):
                    raise TypeError("tool_result_required")
        except Exception as exc:
            # Never expose exception messages: commands and provider errors may
            # contain secrets. A failed hook must not discard other tool results.
            result = ToolExecutionResult(
                tool_type=tool_type,
                stream_events=[{"type": "tool_execution_failed", "tool_type": tool_type,
                                "status": "failed", "reason": "tool_exception:" + type(exc).__name__}],
                followup_context=f"<tool_use_error>Tool execution failed ({type(exc).__name__}); inspect other results before continuing.</tool_use_error>",
            )
        timing = {
            "started_at": started_at, "finished_at": time.time(),
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
        }
        try:
            logger.info("tool_execution_timing %s", json.dumps({
                "scope_id": scope_id, "tool_type": tool_type,
                "call_id": str(call.get(TOOL_INVOCATION_ID_FIELD) or ""), **timing,
            }, ensure_ascii=True))
        except Exception:
            # Diagnostic sink failure must not erase an executed side effect.
            pass
        return replace(result, execution_timing=timing)

    def is_read(call):
        try:
            handler = handler_for(call)
            getter = getattr(handler, "tool_metadata", None)
            metadata = getter() if callable(getter) else None
            return metadata is not None and bool(getattr(metadata, "is_read_only", False))
        except Exception:
            # Metadata failure does not bypass normal dispatch/validation; it
            # only prevents speculative parallel execution.
            return False

    results = []
    reads = []
    parallel_limit = tool_parallel_limit()

    def flush():
        if len(reads) == 1:
            results.append(run(reads[0]))
        elif reads:
            with ThreadPoolExecutor(max_workers=min(parallel_limit, len(reads)), thread_name_prefix="akane-tool") as pool:
                futures = [pool.submit(copy_context().run, run, call) for call in reads]
                results.extend(future.result() for future in futures)
        reads.clear()

    for call in calls:
        if is_read(call):
            reads.append(call)
        else:
            flush()
            results.append(run(call))
    flush()
    return results
