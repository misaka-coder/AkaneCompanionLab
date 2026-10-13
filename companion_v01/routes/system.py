from __future__ import annotations

import time
import tracemalloc
from typing import Any, Callable

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from ..deployment_security import AdminWriteAuth


LogEvent = Callable[..., None]


def build_system_router(
    *,
    engine: Any,
    runtime_metrics: Any,
    public_guard: Any,
    log_event: LogEvent,
    admin_auth: AdminWriteAuth | None = None,
) -> APIRouter:
    router = APIRouter()
    management_auth = admin_auth or AdminWriteAuth.local_compatibility()

    @router.get("/metrics")
    async def metrics() -> Response:
        current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        llm_metrics = engine.llm.snapshot_metrics()
        vector_entries = (
            engine.vector_store.count_entries()
            if getattr(engine, "vector_store", None) is not None
            else 0
        )
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
        embedding_snapshot = getattr(engine, "snapshot_embedding_runtime", None)
        if callable(embedding_snapshot):
            embedding = embedding_snapshot()
            lines.append(f"akane_embedding_model_ready {int(embedding.get('status') in {'ready', 'busy'})}")
            lines.append(f"akane_embedding_precision_bits {dict(float32=32, float16=16, bfloat16=16).get(embedding.get('precision'), 0)}")
            for key in (
                "model_weight_bytes", "batch_size", "effective_batch_size", "active_calls", "calls",
                "failed_calls", "oom_retries", "cached_entries", "cache_max_entries", "cache_cleanup_count",
                "cuda_metrics_available", "cuda_allocated_bytes", "cuda_reserved_bytes",
                "cuda_peak_allocated_bytes", "cuda_peak_reserved_bytes",
            ):
                value = embedding.get(key)
                if isinstance(value, (bool, int)):
                    lines.append(f"akane_embedding_{key} {int(value)}")
        return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    @router.post("/reset")
    async def reset() -> dict[str, str]:
        started_at = time.perf_counter()
        engine.reset()
        runtime_metrics.observe_request("reset", duration_ms=(time.perf_counter() - started_at) * 1000, ok=True)
        log_event("reset_complete", duration_ms=round((time.perf_counter() - started_at) * 1000, 1))
        return {"status": "reset"}

    @router.post("/admin/memcore/backfill")
    async def backfill_memcore(
        request: Request,
        profile_user_id: str = "",
        character_pack_id: str | None = None,
        batch_size: int = 64,
        limit: int | None = None,
    ) -> Any:
        """Legacy memory migration is retired; MemCore is the authoritative engine."""
        authorization = management_auth.authorize(request)
        if not authorization.ok:
            return JSONResponse(
                {
                    "ok": False,
                    "status": "forbidden",
                    "reason": authorization.reason,
                },
                status_code=authorization.status_code,
                headers={"Cache-Control": "no-store"},
            )
        started_at = time.perf_counter()
        result = {
            "ok": True,
            "status": "retired",
            "message": "legacy memory migration is retired and unnecessary; memcore is the sole authority",
        }
        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("backfill_memcore", duration_ms=duration_ms, ok=True)
        log_event("backfill_memcore_complete", ok=True, duration_ms=round(duration_ms, 1))
        return dict(result, duration_ms=round(duration_ms, 1))

    return router
