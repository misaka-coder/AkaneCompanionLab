from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config

from companion_v01.retrieval_eval_benchmark import (
    build_benchmark_runtime,
    default_benchmark_output_paths,
    load_eval_dataset_rows,
    run_benchmark,
    summarize_benchmark_results,
    write_benchmark_details_jsonl,
    write_benchmark_summary_json,
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Akane retrieval benchmark against a reviewed or pending eval JSONL file.")
    parser.add_argument("dataset", help="Path to the retrieval eval JSONL file.")
    parser.add_argument(
        "--base-dir",
        default=str(Path(config.DATA_DIR) / "akane_memory_v01"),
        help="Memory store base directory. Defaults to users_data/akane_memory_v01.",
    )
    parser.add_argument("--output", default="", help="Benchmark summary JSON output path.")
    parser.add_argument("--details-output", default="", help="Per-case benchmark JSONL output path.")
    parser.add_argument("--limit", type=int, default=0, help="Only benchmark the first N rows after filtering.")
    parser.add_argument(
        "--review-statuses",
        default="",
        help="Optional comma-separated review statuses to include, for example approved,golden or pending.",
    )
    parser.add_argument(
        "--embedding-provider",
        default="",
        help="Override embedding provider for this benchmark run, for example hashed or huggingface.",
    )
    parser.add_argument(
        "--embedding-model-name",
        default="",
        help="Override embedding model name for this benchmark run.",
    )
    parser.add_argument(
        "--embedding-device",
        default="",
        help="Override embedding device, for example cpu or cuda.",
    )
    parser.add_argument(
        "--embedding-cache-size",
        type=int,
        default=-1,
        help="Override embedding cache size. Negative value means use config default.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=10,
        help="Print benchmark progress every N rows. Use 0 to disable progress logs.",
    )
    parser.add_argument(
        "--force-retrieval",
        action="store_true",
        help="Ignore router retrieval gating and force every eval row through the retrieval chain.",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    review_statuses = {
        item.strip().lower()
        for item in str(args.review_statuses or "").split(",")
        if item.strip()
    }
    rows = load_eval_dataset_rows(
        dataset_path,
        review_statuses=review_statuses or None,
        limit=max(0, int(args.limit or 0)),
    )
    if not rows:
        parser.error("No benchmark rows matched the current dataset path and review-status filter.")

    runtime = build_benchmark_runtime(
        base_dir=Path(args.base_dir),
        embedding_provider_name=str(args.embedding_provider or ""),
        embedding_model_name=str(args.embedding_model_name or ""),
        embedding_device=str(args.embedding_device or ""),
        embedding_cache_size=None if int(args.embedding_cache_size or -1) < 0 else int(args.embedding_cache_size),
    )
    vector_entries = runtime.vector_store.count_entries()
    vectorizable_records = runtime.store.count_vectorizable_records()
    if vectorizable_records > 0 and vector_entries < vectorizable_records:
        print(
            "[WARN] Current vector collection looks incomplete for this provider: "
            f"{vector_entries}/{vectorizable_records} entries. Benchmark scores may be lower until reindex catches up."
        )
    if runtime.provider_fallback_reason:
        print(
            "[WARN] Embedding provider fallback was used: "
            f"requested={runtime.provider_requested}, actual={runtime.embedding_provider.name}, "
            f"reason={runtime.provider_fallback_reason}"
        )

    print(
        "[INFO] Benchmark runtime: "
        f"provider={runtime.embedding_provider.name}, version={runtime.embedding_provider.version}, "
        f"dimension={runtime.embedding_provider.dimension}, collection={runtime.vector_store.collection_name}"
    )
    print(f"[INFO] Loaded {len(rows)} eval rows from {dataset_path}")
    print(f"[INFO] Benchmark mode: {'force_retrieval' if args.force_retrieval else 'normal'}")

    results = run_benchmark(
        rows=rows,
        runtime=runtime,
        progress_every=max(0, int(args.progress_every or 0)),
        force_retrieval=bool(args.force_retrieval),
    )
    summary = summarize_benchmark_results(
        results,
        dataset_path=dataset_path,
        runtime=runtime,
        force_retrieval=bool(args.force_retrieval),
    )

    default_summary_path, default_details_path = default_benchmark_output_paths(dataset_path)
    summary_path = Path(args.output) if str(args.output or "").strip() else default_summary_path
    details_path = Path(args.details_output) if str(args.details_output or "").strip() else default_details_path

    write_benchmark_summary_json(summary, summary_path)
    write_benchmark_details_jsonl(results, details_path)

    print(f"[INFO] Wrote benchmark summary -> {summary_path}")
    print(f"[INFO] Wrote benchmark details -> {details_path}")
    print(json.dumps(summary["overall"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
