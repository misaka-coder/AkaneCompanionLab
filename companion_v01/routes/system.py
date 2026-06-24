from __future__ import annotations

import time
import tracemalloc
from typing import Any, Callable

from fastapi import APIRouter
from fastapi.responses import Response


LogEvent = Callable[..., None]


def build_system_router(
    *,
    engine: Any,
    runtime_metrics: Any,
    public_guard: Any,
    log_event: LogEvent,
) -> APIRouter:
    router = APIRouter()

    @router.get("/metrics")
    async def metrics() -> Response:
        current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        llm_metrics = engine.llm.snapshot_metrics()
        vector_entries = engine.vector_store.count_entries()
        reindex_metrics = engine.snapshot_embedding_reindex_status()
        counters = runtime_metrics.snapshot()
        guard_metrics = public_guard.snapshot()

        lines = [
            "# TYPE akane_runtime gauge",
            f"akane_tracemalloc_current_bytes {int(current_bytes)}",
            f"akane_tracemalloc_peak_bytes {int(peak_bytes)}",
            f"akane_vector_entries {int(vector_entries)}",
            f"akane_embedding_reindex_total {int(reindex_metrics.get('total') or 0)}",
            f"akane_embedding_reindex_processed {int(reindex_metrics.get('processed') or 0)}",
            f"akane_embedding_reindex_running {1 if str(reindex_metrics.get('state') or '') == 'running' else 0}",
            f"akane_public_guard_enabled {1 if guard_metrics.get('enabled') else 0}",
            f"akane_public_guard_max_concurrent_thinks {int(guard_metrics.get('max_concurrent_thinks') or 0)}",
            f"akane_public_guard_daily_think_limit {int(guard_metrics.get('daily_think_limit') or 0)}",
            f"akane_public_guard_active_thinks {int(guard_metrics.get('active_thinks') or 0)}",
            f"akane_public_guard_used_today {int(guard_metrics.get('used_today') or 0)}",
        ]
        for key, value in sorted(counters.items()):
            lines.append(f"akane_{key} {value}")
        for key, value in sorted(llm_metrics.items()):
            lines.append(f"akane_llm_{key} {int(value)}")
        return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    @router.post("/reset")
    async def reset() -> dict[str, str]:
        started_at = time.perf_counter()
        engine.reset()
        runtime_metrics.observe_request("reset", duration_ms=(time.perf_counter() - started_at) * 1000, ok=True)
        log_event("reset_complete", duration_ms=round((time.perf_counter() - started_at) * 1000, 1))
        return {"status": "reset"}

    return router
