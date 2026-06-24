from __future__ import annotations

import json
import math
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import config

from .embedding_provider import BaseEmbeddingProvider, CachedEmbeddingProvider, HashedEmbeddingProvider
from .huggingface_provider import HuggingFaceEmbeddingProvider
from .llm_runtime import LLMRuntime
from .persona_config import PERSONA
from .prompt_builder import PromptBuilder
from .retrieval_service import RetrievalService
from .retrieval_types import RetrievalPipelineResult
from .store import MemoryStore
from .text_utils import extract_semantic_tags
from .vector_store import VectorStore


@dataclass(frozen=True)
class RetrievalBenchmarkRuntime:
    store: MemoryStore
    vector_store: VectorStore
    llm: LLMRuntime
    prompt_builder: PromptBuilder
    retrieval_service: RetrievalService
    embedding_provider: BaseEmbeddingProvider
    provider_requested: str
    provider_fallback_reason: str


@dataclass(frozen=True)
class RetrievalBenchmarkCaseResult:
    eval_id: str
    query: str
    target_source_id: str
    entry_type: str
    profile_user_id: str
    review_status: str
    benchmark_force_retrieval: bool
    original_router_need_retrieval: bool
    original_router_route: str
    used_retrieval: bool
    router_route: str
    router_ready_at_ms: float | None
    match_result: str
    retry_triggered: bool
    verifier_ready_at_ms: float | None
    filtered_candidate_count: int
    elapsed_ms: float
    target_rank: int | None
    first_target_rank: int | None
    best_target_rank: int | None
    context_target_rank: int | None
    first_context_target_rank: int | None
    best_context_target_rank: int | None
    top1_hit: bool
    top4_hit: bool
    first_top4_hit: bool
    ever_top4_hit: bool
    context_top1_hit: bool
    context_top4_hit: bool
    first_context_top4_hit: bool
    ever_context_top4_hit: bool
    final_source_ids: list[str]
    attempt_source_ids: list[list[str]]
    final_context_source_ids: list[str]
    attempt_context_source_ids: list[list[str]]


def build_benchmark_runtime(
    *,
    base_dir: Path,
    embedding_provider_name: str = "",
    embedding_model_name: str = "",
    embedding_device: str = "",
    embedding_cache_size: int | None = None,
) -> RetrievalBenchmarkRuntime:
    provider, requested, fallback_reason = _build_embedding_provider(
        provider_mode=embedding_provider_name,
        model_name=embedding_model_name,
        device=embedding_device,
        cache_size=embedding_cache_size,
    )
    store = MemoryStore(base_dir)
    vector_store = VectorStore(base_dir / "chroma", embedding_provider=provider)
    llm = LLMRuntime()
    prompt_builder = PromptBuilder(PERSONA)
    retrieval_service = RetrievalService(
        store=store,
        vector_store=vector_store,
        llm=llm,
        prompt_builder=prompt_builder,
    )
    return RetrievalBenchmarkRuntime(
        store=store,
        vector_store=vector_store,
        llm=llm,
        prompt_builder=prompt_builder,
        retrieval_service=retrieval_service,
        embedding_provider=provider,
        provider_requested=requested,
        provider_fallback_reason=fallback_reason,
    )


def load_eval_dataset_rows(
    dataset_path: Path,
    *,
    review_statuses: set[str] | None = None,
    limit: int = 0,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    normalized_statuses = {
        str(status or "").strip().lower()
        for status in (review_statuses or set())
        if str(status or "").strip()
    }
    with Path(dataset_path).open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = str(line or "").strip()
            if not raw:
                continue
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                continue
            if normalized_statuses:
                row_status = str(payload.get("review_status") or "").strip().lower()
                if row_status not in normalized_statuses:
                    continue
            if not str(payload.get("query") or "").strip():
                continue
            if not str(payload.get("target_source_id") or "").strip():
                continue
            rows.append(payload)
            if int(limit or 0) > 0 and len(rows) >= int(limit):
                break
    return rows


def default_benchmark_output_paths(dataset_path: Path) -> tuple[Path, Path]:
    dataset_path = Path(dataset_path)
    stem = dataset_path.stem
    if "candidates" in stem:
        stem = stem.replace("candidates", "benchmark")
    else:
        stem = f"{stem}_benchmark"
    summary_path = dataset_path.with_name(f"{stem}.json")
    details_path = dataset_path.with_name(f"{stem}.details.jsonl")
    return summary_path, details_path


def run_benchmark(
    *,
    rows: Sequence[dict[str, Any]],
    runtime: RetrievalBenchmarkRuntime,
    progress_every: int = 10,
    force_retrieval: bool = False,
) -> list[RetrievalBenchmarkCaseResult]:
    original_drift_probability = float(getattr(config, "DRIFT_PROBABILITY", 0.0) or 0.0)
    original_router_debug = bool(getattr(config, "ROUTER_DEBUG", False))
    original_verifier_debug = bool(getattr(config, "VERIFIER_DEBUG", False))
    config.DRIFT_PROBABILITY = 0.0
    config.ROUTER_DEBUG = False
    config.VERIFIER_DEBUG = False
    try:
        results: list[RetrievalBenchmarkCaseResult] = []
        total = len(rows)
        for index, row in enumerate(rows, start=1):
            results.append(
                    _run_single_case(
                        row=row,
                        retrieval_service=runtime.retrieval_service,
                        force_retrieval=force_retrieval,
                    )
                )
            if progress_every > 0 and (index == total or index % progress_every == 0):
                print(f"[INFO] benchmark progress: {index}/{total}")
        return results
    finally:
        config.DRIFT_PROBABILITY = original_drift_probability
        config.ROUTER_DEBUG = original_router_debug
        config.VERIFIER_DEBUG = original_verifier_debug


def build_benchmark_case_result(
    *,
    row: dict[str, Any],
    pipeline_result: RetrievalPipelineResult,
    elapsed_ms: float,
    benchmark_force_retrieval: bool = False,
    original_router_output: dict[str, Any] | None = None,
    final_context_source_ids: Sequence[str] | None = None,
    attempt_context_source_ids: Sequence[Sequence[str]] | None = None,
) -> RetrievalBenchmarkCaseResult:
    target_source_id = str(row.get("target_source_id") or "").strip()
    final_source_ids = _normalize_source_ids(
        [hit.get("source_id") for hit in pipeline_result.retrieval_result.get("fused_hits", [])]
    )
    attempt_source_ids = [
        _normalize_source_ids(attempt.get("retrieved_source_ids") or [])
        for attempt in pipeline_result.verifier_timing.get("attempts", []) or []
    ]
    if not attempt_source_ids and final_source_ids:
        attempt_source_ids = [list(final_source_ids)]

    target_rank = _find_rank(final_source_ids, target_source_id)
    first_target_rank = _find_rank(attempt_source_ids[0], target_source_id) if attempt_source_ids else None
    best_target_rank = _best_rank(attempt_source_ids, target_source_id)
    normalized_final_context_source_ids = _normalize_source_ids(final_context_source_ids or final_source_ids)
    normalized_attempt_context_source_ids = [
        _normalize_source_ids(values)
        for values in (attempt_context_source_ids or attempt_source_ids)
    ]
    if not normalized_attempt_context_source_ids and normalized_final_context_source_ids:
        normalized_attempt_context_source_ids = [list(normalized_final_context_source_ids)]
    context_target_rank = _find_rank(normalized_final_context_source_ids, target_source_id)
    first_context_target_rank = (
        _find_rank(normalized_attempt_context_source_ids[0], target_source_id)
        if normalized_attempt_context_source_ids
        else None
    )
    best_context_target_rank = _best_rank(normalized_attempt_context_source_ids, target_source_id)
    selected_attempt_index = int(pipeline_result.verifier_timing.get("selected_attempt") or 0)
    verifier_ready_at_ms = None
    if selected_attempt_index > 0:
        attempts = list(pipeline_result.verifier_timing.get("attempts") or [])
        if selected_attempt_index <= len(attempts):
            verifier_ready_at_ms = _coerce_float(
                (attempts[selected_attempt_index - 1].get("verifier_timing") or {}).get("ready_at_ms")
            )

    return RetrievalBenchmarkCaseResult(
        eval_id=str(row.get("eval_id") or ""),
        query=str(row.get("query") or ""),
        target_source_id=target_source_id,
        entry_type=str(row.get("entry_type") or ""),
        profile_user_id=str(row.get("profile_user_id") or ""),
        review_status=str(row.get("review_status") or ""),
        benchmark_force_retrieval=bool(benchmark_force_retrieval),
        original_router_need_retrieval=bool((original_router_output or {}).get("need_retrieval")),
        original_router_route=str((original_router_output or {}).get("route") or ""),
        used_retrieval=bool(pipeline_result.used_retrieval),
        router_route=str(pipeline_result.router_output.get("route") or ""),
        router_ready_at_ms=_coerce_float(pipeline_result.router_timing.get("ready_at_ms")),
        match_result=str(pipeline_result.verifier_output.get("match_result") or ""),
        retry_triggered=len(attempt_source_ids) > 1,
        verifier_ready_at_ms=verifier_ready_at_ms,
        filtered_candidate_count=int(pipeline_result.retrieval_result.get("filtered_candidate_count") or 0),
        elapsed_ms=round(float(elapsed_ms), 3),
        target_rank=target_rank,
        first_target_rank=first_target_rank,
        best_target_rank=best_target_rank,
        context_target_rank=context_target_rank,
        first_context_target_rank=first_context_target_rank,
        best_context_target_rank=best_context_target_rank,
        top1_hit=target_rank == 1,
        top4_hit=target_rank is not None and target_rank <= 4,
        first_top4_hit=first_target_rank is not None and first_target_rank <= 4,
        ever_top4_hit=best_target_rank is not None and best_target_rank <= 4,
        context_top1_hit=context_target_rank == 1,
        context_top4_hit=context_target_rank is not None and context_target_rank <= 4,
        first_context_top4_hit=first_context_target_rank is not None and first_context_target_rank <= 4,
        ever_context_top4_hit=best_context_target_rank is not None and best_context_target_rank <= 4,
        final_source_ids=final_source_ids,
        attempt_source_ids=attempt_source_ids,
        final_context_source_ids=normalized_final_context_source_ids,
        attempt_context_source_ids=normalized_attempt_context_source_ids,
    )


def summarize_benchmark_results(
    results: Sequence[RetrievalBenchmarkCaseResult],
    *,
    dataset_path: Path,
    runtime: RetrievalBenchmarkRuntime,
    force_retrieval: bool = False,
) -> dict[str, Any]:
    vector_entries = int(runtime.vector_store.count_entries())
    vectorizable_records = int(runtime.store.count_vectorizable_records())
    return {
        "dataset_path": str(Path(dataset_path)),
        "provider_requested": runtime.provider_requested,
        "embedding_provider": runtime.embedding_provider.name,
        "embedding_version": str(runtime.embedding_provider.version),
        "embedding_dimension": int(runtime.embedding_provider.dimension),
        "provider_fallback_reason": runtime.provider_fallback_reason,
        "collection_name": str(runtime.vector_store.collection_name),
        "benchmark_mode": "force_retrieval" if force_retrieval else "normal",
        "vector_entries": vector_entries,
        "vectorizable_records": vectorizable_records,
        "collection_complete": bool(vector_entries >= vectorizable_records) if vectorizable_records > 0 else True,
        "entry_type_counts": _count_values(result.entry_type for result in results),
        "review_status_counts": _count_values(result.review_status for result in results),
        "overall": _summarize_subset(results),
        "by_entry_type": {
            entry_type: _summarize_subset([result for result in results if result.entry_type == entry_type])
            for entry_type in ("raw", "summary", "semantic_summary")
        },
    }


def write_benchmark_summary_json(summary: dict[str, Any], output_path: Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def write_benchmark_details_jsonl(
    results: Iterable[RetrievalBenchmarkCaseResult],
    output_path: Path,
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
    return output_path


def _run_single_case(
    *,
    row: dict[str, Any],
    retrieval_service: RetrievalService,
    force_retrieval: bool = False,
) -> RetrievalBenchmarkCaseResult:
    query = str(row.get("query") or "").strip()
    timestamp = int(row.get("timestamp") or row.get("created_at") or time.time())
    current_source_id = f"eval_current::{str(row.get('eval_id') or time.time())}"
    current_user_record = {
        "source_id": current_source_id,
        "role": "user",
        "content": query,
        "timestamp": timestamp,
    }
    started_at = time.perf_counter()
    profile_user_id = str(row.get("profile_user_id") or "")
    if force_retrieval:
        original_router_output, router_timing = retrieval_service._build_router_output(
            profile_user_id=profile_user_id,
            user_message=query,
            current_user_record=current_user_record,
            recent_raw=[],
            now_ts=timestamp,
            router_debug_enabled=False,
        )
        forced_router_output = dict(original_router_output)
        forced_router_output["need_retrieval"] = True
        forced_router_output["route"] = str(forced_router_output.get("route") or "forced_memory_search")
        if forced_router_output["route"] == "direct_answer":
            forced_router_output["route"] = "forced_memory_search"
        if not str(forced_router_output.get("rewritten_query") or "").strip():
            forced_router_output["rewritten_query"] = query
        if not list(forced_router_output.get("keywords") or []):
            forced_router_output["keywords"] = extract_semantic_tags(query, limit=6)
        retrieval_result, verifier_output, confirmed_snippets, verifier_timing = retrieval_service._run_retrieval_chain(
            profile_user_id=profile_user_id,
            original_query=query,
            now_ts=timestamp,
            router_output=forced_router_output,
            exclude_source_ids=[current_source_id],
            verifier_debug_enabled=False,
        )
        pipeline_result = RetrievalPipelineResult(
            used_retrieval=True,
            confirmed_snippets=confirmed_snippets,
            router_output=forced_router_output,
            router_timing=router_timing,
            retrieval_result=retrieval_result,
            verifier_output=verifier_output,
            verifier_timing=verifier_timing,
        )
    else:
        original_router_output = None
        pipeline_result = retrieval_service.run(
            profile_user_id=profile_user_id,
            user_message=query,
            now_ts=timestamp,
            current_user_record=current_user_record,
            recent_raw=[],
            exclude_source_ids=[current_source_id],
            router_debug_enabled=False,
            verifier_debug_enabled=False,
        )
    final_source_ids = _normalize_source_ids(
        [hit.get("source_id") for hit in pipeline_result.retrieval_result.get("fused_hits", [])]
    )
    attempt_source_ids = [
        _normalize_source_ids(attempt.get("retrieved_source_ids") or [])
        for attempt in pipeline_result.verifier_timing.get("attempts", []) or []
    ]
    final_context_source_ids = _expand_context_source_ids(
        source_ids=final_source_ids,
        retrieval_service=retrieval_service,
    )
    attempt_context_source_ids = [
        _expand_context_source_ids(
            source_ids=source_ids,
            retrieval_service=retrieval_service,
        )
        for source_ids in attempt_source_ids
    ]
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    return build_benchmark_case_result(
        row=row,
        pipeline_result=pipeline_result,
        elapsed_ms=elapsed_ms,
        benchmark_force_retrieval=force_retrieval,
        original_router_output=original_router_output,
        final_context_source_ids=final_context_source_ids,
        attempt_context_source_ids=attempt_context_source_ids,
    )


def _build_embedding_provider(
    *,
    provider_mode: str = "",
    model_name: str = "",
    device: str = "",
    cache_size: int | None = None,
) -> tuple[BaseEmbeddingProvider, str, str]:
    requested = str(provider_mode or getattr(config, "EMBEDDING_PROVIDER", "auto") or "auto").strip().lower() or "auto"
    default_model_name = str(getattr(config, "DEFAULT_EMBEDDING_MODEL_NAME", "BAAI/bge-m3") or "BAAI/bge-m3")
    requested_model_name = str(model_name or getattr(config, "EMBEDDING_MODEL_NAME", "") or default_model_name).strip() or default_model_name
    requested_device = str(device or getattr(config, "EMBEDDING_DEVICE", "") or "").strip() or None
    local_files_only = bool(getattr(config, "EMBEDDING_LOCAL_FILES_ONLY", True))
    cache_folder = str(getattr(config, "EMBEDDING_CACHE_FOLDER", "") or "").strip() or None
    hf_endpoint = str(getattr(config, "HF_ENDPOINT", "") or "").strip() or None
    resolved_cache_size = int(getattr(config, "EMBEDDING_CACHE_SIZE", 0) if cache_size is None else cache_size)

    base_provider: BaseEmbeddingProvider = HashedEmbeddingProvider()
    fallback_reason = ""
    if requested in {"auto", "huggingface", "hf", "sentence-transformer", "sentence-transformers"}:
        try:
            base_provider = HuggingFaceEmbeddingProvider(
                model_name=requested_model_name,
                device=requested_device,
                local_files_only=local_files_only,
                cache_folder=cache_folder,
                hf_endpoint=hf_endpoint,
            )
        except Exception as exc:
            base_provider = HashedEmbeddingProvider()
            fallback_reason = str(exc)
    elif requested in {"hashed", "hash"}:
        base_provider = HashedEmbeddingProvider()
    else:
        fallback_reason = f"Unknown embedding provider '{requested}', falling back to hashed."
        base_provider = HashedEmbeddingProvider()

    if resolved_cache_size > 0:
        return (
            CachedEmbeddingProvider(base_provider, max_entries=resolved_cache_size),
            requested,
            fallback_reason,
        )
    return base_provider, requested, fallback_reason


def _normalize_source_ids(values: Iterable[Any]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        source_id = str(value or "").strip()
        if source_id:
            normalized.append(source_id)
    return normalized


def _find_rank(source_ids: Sequence[str], target_source_id: str) -> int | None:
    target = str(target_source_id or "").strip()
    if not target:
        return None
    for index, source_id in enumerate(source_ids, start=1):
        if str(source_id or "").strip() == target:
            return index
    return None


def _best_rank(attempt_source_ids: Sequence[Sequence[str]], target_source_id: str) -> int | None:
    ranks = [
        rank
        for rank in (_find_rank(source_ids, target_source_id) for source_ids in attempt_source_ids)
        if rank is not None
    ]
    if not ranks:
        return None
    return min(ranks)


def _expand_context_source_ids(
    *,
    source_ids: Sequence[str],
    retrieval_service: RetrievalService,
) -> list[str]:
    expanded: list[str] = []
    seen: set[str] = set()
    for source_id in source_ids:
        for candidate_source_id in _covered_source_ids_for_anchor(
            source_id=source_id,
            retrieval_service=retrieval_service,
        ):
            normalized = str(candidate_source_id or "").strip()
            if not normalized or normalized in seen:
                continue
            expanded.append(normalized)
            seen.add(normalized)
    return expanded


def _covered_source_ids_for_anchor(
    *,
    source_id: str,
    retrieval_service: RetrievalService,
) -> list[str]:
    normalized_source_id = str(source_id or "").strip()
    if not normalized_source_id:
        return []
    record = retrieval_service.store.get_record_by_source_id(normalized_source_id)
    if not isinstance(record, dict):
        return [normalized_source_id]
    if str(record.get("entry_type") or "") != "raw":
        return [normalized_source_id]
    session_id = str(record.get("session_id") or "").strip()
    seq_no = int(record.get("seq_no") or 0)
    if not session_id or seq_no <= 0:
        return [normalized_source_id]
    window = 2 if retrieval_service._is_question_like(str(record.get("content") or "")) else 1
    rows = retrieval_service.store.get_context_slice(
        session_id,
        seq_no,
        window=window,
        profile_user_id=str(record.get("profile_user_id") or ""),
        character_pack_id=str(record.get("character_pack_id") or ""),
    )
    expanded = [
        str(row.get("source_id") or "").strip()
        for row in rows
        if str(row.get("source_id") or "").strip()
    ]
    return expanded or [normalized_source_id]


def _summarize_subset(results: Sequence[RetrievalBenchmarkCaseResult]) -> dict[str, Any]:
    total = len(results)
    elapsed_values = [float(result.elapsed_ms) for result in results]
    router_ready_values = [value for value in (_coerce_float(result.router_ready_at_ms) for result in results) if value is not None]
    verifier_ready_values = [value for value in (_coerce_float(result.verifier_ready_at_ms) for result in results) if value is not None]
    filtered_candidate_values = [int(result.filtered_candidate_count) for result in results]

    used_retrieval_count = sum(1 for result in results if result.used_retrieval)
    top1_hits = sum(1 for result in results if result.top1_hit)
    top4_hits = sum(1 for result in results if result.top4_hit)
    first_top4_hits = sum(1 for result in results if result.first_top4_hit)
    ever_top4_hits = sum(1 for result in results if result.ever_top4_hit)
    context_top1_hits = sum(1 for result in results if result.context_top1_hit)
    context_top4_hits = sum(1 for result in results if result.context_top4_hit)
    first_context_top4_hits = sum(1 for result in results if result.first_context_top4_hit)
    ever_context_top4_hits = sum(1 for result in results if result.ever_context_top4_hit)
    final_match_count = sum(1 for result in results if result.match_result == "match")
    retry_triggered_count = sum(1 for result in results if result.retry_triggered)
    original_router_positive_count = sum(1 for result in results if result.original_router_need_retrieval)
    return {
        "count": total,
        "benchmark_force_retrieval_count": sum(1 for result in results if result.benchmark_force_retrieval),
        "original_router_positive_count": original_router_positive_count,
        "original_router_positive_rate": _ratio(original_router_positive_count, total),
        "used_retrieval_count": used_retrieval_count,
        "used_retrieval_rate": _ratio(used_retrieval_count, total),
        "skipped_retrieval_count": total - used_retrieval_count,
        "top1_hits": top1_hits,
        "top1_recall": _ratio(top1_hits, total),
        "top4_hits": top4_hits,
        "top4_recall": _ratio(top4_hits, total),
        "first_top4_hits": first_top4_hits,
        "first_top4_recall": _ratio(first_top4_hits, total),
        "ever_top4_hits": ever_top4_hits,
        "ever_top4_recall": _ratio(ever_top4_hits, total),
        "context_top1_hits": context_top1_hits,
        "context_top1_recall": _ratio(context_top1_hits, total),
        "context_top4_hits": context_top4_hits,
        "context_top4_recall": _ratio(context_top4_hits, total),
        "first_context_top4_hits": first_context_top4_hits,
        "first_context_top4_recall": _ratio(first_context_top4_hits, total),
        "ever_context_top4_hits": ever_context_top4_hits,
        "ever_context_top4_recall": _ratio(ever_context_top4_hits, total),
        "final_match_count": final_match_count,
        "final_match_rate": _ratio(final_match_count, total),
        "retry_triggered_count": retry_triggered_count,
        "retry_triggered_rate": _ratio(retry_triggered_count, total),
        "avg_elapsed_ms": _round_or_zero(_average(elapsed_values)),
        "median_elapsed_ms": _round_or_zero(statistics.median(elapsed_values) if elapsed_values else 0.0),
        "p95_elapsed_ms": _round_or_zero(_percentile(elapsed_values, 0.95)),
        "avg_router_ready_ms": _round_or_zero(_average(router_ready_values)),
        "avg_verifier_ready_ms": _round_or_zero(_average(verifier_ready_values)),
        "avg_filtered_candidate_count": _round_or_zero(_average(filtered_candidate_values)),
    }


def _count_values(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _average(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return float(sum(values) / len(values))


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = max(0, min(len(ordered) - 1, math.ceil(float(quantile) * len(ordered)) - 1))
    return float(ordered[position])


def _round_or_zero(value: float) -> float:
    return round(float(value or 0.0), 3)


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None
