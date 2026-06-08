from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config

from companion_v01.llm_runtime import LLMRuntime
from companion_v01.retrieval_eval_dataset import (
    count_eval_source_records,
    collect_eval_source_records,
    default_output_path,
    generate_eval_dataset_rows,
    sample_eval_source_records,
    write_eval_dataset_jsonl,
)
from companion_v01.store import MemoryStore


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate retrieval evaluation candidates from Akane memory SQLite data.")
    parser.add_argument(
        "--base-dir",
        default=str(Path(config.DATA_DIR) / "akane_memory_v01"),
        help="Memory store base directory. Defaults to users_data/akane_memory_v01.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output JSONL path. Defaults to documents/projects/retrieval_eval_candidates_<timestamp>.jsonl",
    )
    parser.add_argument("--count", type=int, default=48, help="How many candidate evaluation rows to generate.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed used for balanced sampling.")
    parser.add_argument("--profile-user-id", default="", help="Optionally restrict sampling to one profile_user_id.")
    parser.add_argument("--raw-window", type=int, default=1, help="Context window size for raw-message candidates.")
    parser.add_argument("--skip-llm", action="store_true", help="Skip LLM paraphrasing and only use fallback queries.")
    parser.add_argument("--no-raw", action="store_true", help="Exclude raw-message candidates.")
    parser.add_argument("--no-summary", action="store_true", help="Exclude episodic summary candidates.")
    parser.add_argument("--no-semantic", action="store_true", help="Exclude semantic summary candidates.")
    parser.add_argument("--target-raw", type=int, default=0, help="Optional exact target count for raw candidates.")
    parser.add_argument("--target-summary", type=int, default=0, help="Optional exact target count for episodic summary candidates.")
    parser.add_argument("--target-semantic", type=int, default=0, help="Optional exact target count for semantic summary candidates.")
    parser.add_argument(
        "--allow-repeat-scarce",
        action="store_true",
        help="Allow summary/semantic layers to reuse the same source record when the target quota exceeds inventory.",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    base_dir = Path(args.base_dir)
    store = MemoryStore(base_dir)
    profile_user_id = str(args.profile_user_id or "").strip() or None
    include_raw = not bool(args.no_raw)
    include_summary = not bool(args.no_summary)
    include_semantic = not bool(args.no_semantic)
    if not any((include_raw, include_summary, include_semantic)):
        parser.error("At least one source type must be enabled.")

    sources = collect_eval_source_records(
        store=store,
        profile_user_id=profile_user_id,
        include_raw=include_raw,
        include_summary=include_summary,
        include_semantic=include_semantic,
        raw_context_window=max(0, int(args.raw_window)),
    )
    inventory_counts = count_eval_source_records(sources)
    print(f"[INFO] inventory counts: {inventory_counts}")

    target_counts = {
        "raw": max(0, int(args.target_raw)),
        "summary": max(0, int(args.target_summary)),
        "semantic_summary": max(0, int(args.target_semantic)),
    }
    use_explicit_targets = any(value > 0 for value in target_counts.values())
    if use_explicit_targets:
        requested_total = sum(target_counts.values())
        sampled = sample_eval_source_records(
            sources,
            total_count=requested_total,
            seed=int(args.seed),
            target_counts=target_counts,
            allow_repeat_for={"summary", "semantic_summary"} if args.allow_repeat_scarce else set(),
        )
    else:
        sampled = sample_eval_source_records(
            sources,
            total_count=max(1, int(args.count)),
            seed=int(args.seed),
        )
    llm = None if args.skip_llm else LLMRuntime()
    rows = generate_eval_dataset_rows(
        sources=sampled,
        llm=llm,
        use_llm=not bool(args.skip_llm),
    )

    output_path = Path(args.output) if str(args.output or "").strip() else default_output_path(Path(config.BASE_DIR))
    write_eval_dataset_jsonl(rows, output_path)

    entry_type_counts = Counter(row["entry_type"] for row in rows)
    mode_counts = Counter(row["generation_mode"] for row in rows)
    print(f"[INFO] Generated {len(rows)} retrieval-eval candidates -> {output_path}")
    if profile_user_id:
        print(f"[INFO] profile_user_id={profile_user_id}")
    if use_explicit_targets:
        print(f"[INFO] requested target counts: {target_counts}")
        print(f"[INFO] allow_repeat_scarce: {bool(args.allow_repeat_scarce)}")
    print(f"[INFO] entry_type counts: {dict(entry_type_counts)}")
    print(f"[INFO] generation_mode counts: {dict(mode_counts)}")
    print("[INFO] review_status is set to 'pending' for every row; please do a quick human pass before treating it as golden.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
