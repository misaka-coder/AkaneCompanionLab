from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from companion_v01.tool_decision_eval import (  # noqa: E402
    DEFAULT_MEMORY_EVAL_CASES,
    DEFAULT_READ_TIER_EVAL_CASES,
    DEFAULT_WEB_SEARCH_EVAL_CASES,
    LiveLLMToolDecisionResponseProvider,
    build_dry_run_eval_engine,
    build_dry_run_memory_eval_engine,
    build_dry_run_web_search_eval_engine,
    run_tool_decision_eval,
    scripted_tool_decision_response_provider,
    summarize_tool_decision_eval_results,
    tool_decision_results_as_dicts,
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the deterministic web_search tool-decision eval fixture. "
            "This dry-run does not call the real LLM or AnySearch MCP."
        )
    )
    parser.add_argument(
        "--mode",
        choices=["legacy", "native", "both"],
        default="both",
        help="Which tool-call channel fixture to evaluate.",
    )
    parser.add_argument(
        "--toolset",
        choices=["web_search", "memory", "all"],
        default="web_search",
        help=(
            "Which tool family to evaluate. Default web_search keeps existing "
            "behavior. memory/all cover the read-only memory tools (dry-run only)."
        ),
    )
    parser.add_argument(
        "--summary-path",
        default="",
        help="Optional JSON path for the summary report.",
    )
    parser.add_argument(
        "--details-path",
        default="",
        help="Optional JSONL path for per-case details.",
    )
    parser.add_argument(
        "--no-execute-tools",
        action="store_true",
        help="Skip dry-run tool execution after validation.",
    )
    parser.add_argument(
        "--live-llm",
        action="store_true",
        help="Call the configured chat LLM for real. By default this script uses deterministic dry-run fixtures.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit the number of eval cases. Useful with --live-llm to control cost.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="LLM temperature for --live-llm.",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    modes = ("legacy", "native") if args.mode == "both" else (args.mode,)

    toolset = str(args.toolset or "web_search")
    if toolset == "memory":
        engine = build_dry_run_memory_eval_engine()
        cases = list(DEFAULT_MEMORY_EVAL_CASES)
    elif toolset == "all":
        engine = build_dry_run_eval_engine()
        cases = (
            list(DEFAULT_WEB_SEARCH_EVAL_CASES)
            + list(DEFAULT_MEMORY_EVAL_CASES)
            + list(DEFAULT_READ_TIER_EVAL_CASES)
        )
    else:
        engine = build_dry_run_web_search_eval_engine()
        cases = list(DEFAULT_WEB_SEARCH_EVAL_CASES)

    if int(args.limit or 0) > 0:
        cases = cases[: max(0, int(args.limit))]
    response_provider = (
        LiveLLMToolDecisionResponseProvider(toolset=toolset, temperature=float(args.temperature))
        if bool(args.live_llm)
        else scripted_tool_decision_response_provider
    )
    results = run_tool_decision_eval(
        cases=cases,
        engine=engine,
        response_provider=response_provider,
        modes=modes,
        execute_tools=(not bool(args.no_execute_tools)) and not bool(args.live_llm),
    )
    summary = summarize_tool_decision_eval_results(results)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))

    if str(args.summary_path or "").strip():
        summary_path = Path(args.summary_path)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if str(args.details_path or "").strip():
        details_path = Path(args.details_path)
        details_path.parent.mkdir(parents=True, exist_ok=True)
        rows = tool_decision_results_as_dicts(results)
        details_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
