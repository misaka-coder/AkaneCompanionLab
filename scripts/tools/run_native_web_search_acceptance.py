from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import config  # noqa: E402
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
)

from run_native_web_search_smoke import (  # noqa: E402
    default_message_for_toolset,
    run_smoke,
    smoke_passed,
    toolset_allowlist,
)


DEFAULT_USER_ID = "master"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the 3d native web_search acceptance gate. By default this only "
            "runs deterministic eval fixtures. Use --live-llm and --smoke to "
            "exercise the real configured model and engine loop."
        )
    )
    parser.add_argument(
        "--toolset",
        choices=["web_search", "memory", "all"],
        default="web_search",
        help=(
            "Which native tool family to gate. Default web_search keeps existing behavior. "
            "all gates the full default native allowlist."
        ),
    )
    parser.add_argument("--live-llm", action="store_true", help="Run the real LLM tool-decision eval.")
    parser.add_argument("--smoke", action="store_true", help="Run real AkaneMemoryEngine smoke checks.")
    parser.add_argument(
        "--real-web-search",
        action="store_true",
        help="Use configured AnySearch MCP during smoke instead of the deterministic fixture.",
    )
    parser.add_argument("--limit", type=int, default=5, help="Case limit for --live-llm eval.")
    parser.add_argument(
        "--message",
        default="",
        help="Smoke prompt. Defaults to a toolset-appropriate message when omitted.",
    )
    parser.add_argument("--user-id", default=DEFAULT_USER_ID, help="Smoke user/profile id.")
    parser.add_argument(
        "--summary-path",
        default="",
        help="Optional JSON path for the acceptance summary.",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    toolset = str(args.toolset or "web_search")
    summary = run_acceptance(
        live_llm=bool(args.live_llm),
        smoke=bool(args.smoke),
        real_web_search=bool(args.real_web_search),
        limit=int(args.limit or 0),
        message=str(args.message or "").strip() or default_message_for_toolset(toolset),
        user_id=str(args.user_id),
        toolset=toolset,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if str(args.summary_path or "").strip():
        summary_path = Path(args.summary_path)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0 if summary.get("status") == "pass" else 1


def run_acceptance(
    *,
    live_llm: bool,
    smoke: bool,
    real_web_search: bool,
    limit: int,
    message: str,
    user_id: str,
    toolset: str = "web_search",
) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    failures: list[str] = []

    dry_summary = run_eval_summary(live_llm=False, limit=0, toolset=toolset)
    checks["dry_run_eval"] = {
        "summary": dry_summary,
        "gate": gate_eval_summary(dry_summary),
    }
    failures.extend(prefix_failures("dry_run_eval", checks["dry_run_eval"]["gate"]))

    if live_llm:
        live_summary = run_eval_summary(live_llm=True, limit=limit, toolset=toolset)
        checks["live_llm_eval"] = {
            "summary": live_summary,
            "gate": gate_eval_summary(live_summary),
        }
        failures.extend(prefix_failures("live_llm_eval", checks["live_llm_eval"]["gate"]))
    else:
        checks["live_llm_eval"] = {"skipped": True}

    if smoke:
        smoke_dir = PROJECT_ROOT / "reports" / "native_web_search_acceptance" / f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
        original_native_enabled = getattr(config, "ENABLE_NATIVE_TOOL_DECISION", False)
        original_native_allowlist = getattr(config, "NATIVE_TOOL_DECISION_ALLOWLIST", "")
        original_pre_retrieval = getattr(config, "PRE_RETRIEVAL_DEFAULT_ENABLED", True)
        original_semantic_memory = getattr(config, "ENABLE_SEMANTIC_MEMORY", True)
        original_vision = getattr(config, "VISION_ENABLED", True)
        try:
            config.ENABLE_NATIVE_TOOL_DECISION = True
            config.NATIVE_TOOL_DECISION_ALLOWLIST = toolset_allowlist(toolset)
            config.PRE_RETRIEVAL_DEFAULT_ENABLED = False
            config.ENABLE_SEMANTIC_MEMORY = False
            config.VISION_ENABLED = False
            non_stream = run_smoke(
                base_dir=smoke_dir / "non_stream",
                message=message,
                user_id=user_id,
                stream=False,
                real_web_search=real_web_search,
                toolset=toolset,
            )
            stream = run_smoke(
                base_dir=smoke_dir / "stream",
                message=message,
                user_id=user_id,
                stream=True,
                real_web_search=real_web_search,
                toolset=toolset,
            )
        finally:
            config.ENABLE_NATIVE_TOOL_DECISION = original_native_enabled
            config.NATIVE_TOOL_DECISION_ALLOWLIST = original_native_allowlist
            config.PRE_RETRIEVAL_DEFAULT_ENABLED = original_pre_retrieval
            config.ENABLE_SEMANTIC_MEMORY = original_semantic_memory
            config.VISION_ENABLED = original_vision
        checks["non_stream_smoke"] = {
            "summary": non_stream,
            "gate": gate_smoke_summary(non_stream, stream=False),
        }
        checks["stream_smoke"] = {
            "summary": stream,
            "gate": gate_smoke_summary(stream, stream=True),
        }
        failures.extend(prefix_failures("non_stream_smoke", checks["non_stream_smoke"]["gate"]))
        failures.extend(prefix_failures("stream_smoke", checks["stream_smoke"]["gate"]))
    else:
        checks["non_stream_smoke"] = {"skipped": True}
        checks["stream_smoke"] = {"skipped": True}

    return {
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "checks": checks,
    }


def run_eval_summary(*, live_llm: bool, limit: int, toolset: str = "web_search") -> dict[str, Any]:
    normalized = str(toolset or "web_search").strip().lower() or "web_search"
    if normalized == "memory":
        cases = list(DEFAULT_MEMORY_EVAL_CASES)
        engine = build_dry_run_memory_eval_engine()
    elif normalized == "all":
        cases = (
            list(DEFAULT_WEB_SEARCH_EVAL_CASES)
            + list(DEFAULT_MEMORY_EVAL_CASES)
            + list(DEFAULT_READ_TIER_EVAL_CASES)
        )
        engine = build_dry_run_eval_engine()
    else:
        cases = list(DEFAULT_WEB_SEARCH_EVAL_CASES)
        engine = build_dry_run_web_search_eval_engine()
    if live_llm and int(limit or 0) > 0:
        cases = cases[: max(0, int(limit))]
    provider = (
        LiveLLMToolDecisionResponseProvider(toolset=normalized)
        if live_llm
        else scripted_tool_decision_response_provider
    )
    results = run_tool_decision_eval(
        cases=cases,
        engine=engine,
        response_provider=provider,
        modes=("legacy", "native"),
        execute_tools=(not live_llm),
    )
    return summarize_tool_decision_eval_results(results)


def gate_eval_summary(summary: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    modes = summary.get("modes") if isinstance(summary.get("modes"), dict) else {}
    native = modes.get("native") if isinstance(modes.get("native"), dict) else {}
    legacy = modes.get("legacy") if isinstance(modes.get("legacy"), dict) else {}
    if not native:
        failures.append("missing_native_mode")
    if not legacy:
        failures.append("missing_legacy_mode")
    if int(native.get("provider_unsupported_count", 0) or 0) != 0:
        failures.append("native_provider_unsupported")
    if int(native.get("native_degraded_count", 0) or 0) != 0:
        failures.append("native_degraded")
    native_expectation = float(native.get("eligible_expectation_match_rate", 0.0) or 0.0)
    legacy_expectation = float(legacy.get("eligible_expectation_match_rate", 0.0) or 0.0)
    if native_expectation < legacy_expectation:
        failures.append("native_expectation_below_legacy")
    if int(native.get("fallback_hit_count", 0) or 0) > int(legacy.get("fallback_hit_count", 0) or 0):
        failures.append("native_fallback_above_legacy")
    if float(native.get("validation_success_rate", 0.0) or 0.0) < 0.95:
        failures.append("native_validation_below_0_95")
    return {"status": "pass" if not failures else "fail", "failures": failures}


def gate_smoke_summary(summary: dict[str, Any], *, stream: bool) -> dict[str, Any]:
    failures: list[str] = []
    if not smoke_passed(summary, stream=stream):
        failures.append("smoke_basic_gate_failed")
    if int(summary.get("chat_json_fallbacks_delta", 0) or 0) != 0:
        failures.append("chat_json_fallback_hit")
    if int(summary.get("native_tool_provider_unsupported_delta", 0) or 0) != 0:
        failures.append("native_provider_unsupported")
    if int(summary.get("unavailable_tool_event_count", 0) or 0) != 0:
        failures.append("tool_unavailable")
    if not str(summary.get("speech") or "").strip():
        failures.append("empty_final_speech")
    return {"status": "pass" if not failures else "fail", "failures": failures}


def prefix_failures(prefix: str, gate: dict[str, Any]) -> list[str]:
    return [f"{prefix}:{item}" for item in list(gate.get("failures") or [])]


if __name__ == "__main__":
    raise SystemExit(main())
