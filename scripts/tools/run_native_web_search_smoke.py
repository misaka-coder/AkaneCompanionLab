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

import config  # noqa: E402
from companion_v01.capability_registry import CapabilitySelection  # noqa: E402
from companion_v01.engine import AkaneMemoryEngine  # noqa: E402
from companion_v01.tool_runtime import ToolExecutionContext, ToolExecutionResult, WebSearchToolHandler  # noqa: E402


DEFAULT_MESSAGE = "查一下今天上海天气。"


class SmokeWebSearchToolHandler(WebSearchToolHandler):
    """Deterministic web_search executor for testing the real engine loop."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.executed_calls: list[dict[str, Any]] = []

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        action = str(call.get("action") or "search").strip() or "search"
        query = str(call.get("query") or "").strip()
        url = str(call.get("url") or "").strip()
        target = query or url or "未提供目标"
        self.executed_calls.append(
            {
                str(key): value
                for key, value in dict(call or {}).items()
                if not str(key).startswith("_tool_")
            }
        )
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "web_search_completed",
                    "provider": "smoke_fixture",
                    "action": action,
                    "status": "ok",
                    "target": target,
                }
            ],
            followup_context=(
                "【smoke web_search 结果】\n"
                f"动作：{action}\n"
                f"目标：{target}\n"
                "摘要：上海今天晴到多云，适合出门；这是 smoke 固定结果，用来验证原生工具轮是否能把工具结果交给最终回复。"
            ),
            state_updates={
                "web_search_status": "ok",
                "web_search_provider": "smoke_fixture",
                "web_search_smoke": True,
            },
        )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one real AkaneMemoryEngine.process_turn with native web_search enabled. "
            "The LLM is real; web_search execution is a deterministic local fixture."
        )
    )
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument("--user-id", default="native_web_search_smoke")
    parser.add_argument(
        "--base-dir",
        default="",
        help="Optional data dir. Defaults to reports/native_web_search_smoke_runtime/<timestamp>.",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Use process_turn_stream instead of process_turn and collect emitted stream events.",
    )
    parser.add_argument(
        "--real-web-search",
        action="store_true",
        help="Use the configured real AnySearch MCP web_search handler instead of the deterministic fixture.",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    original_native_enabled = getattr(config, "ENABLE_NATIVE_TOOL_DECISION", False)
    original_native_allowlist = getattr(config, "NATIVE_TOOL_DECISION_ALLOWLIST", "")
    original_pre_retrieval = getattr(config, "PRE_RETRIEVAL_DEFAULT_ENABLED", True)
    original_semantic_memory = getattr(config, "ENABLE_SEMANTIC_MEMORY", True)
    original_vision = getattr(config, "VISION_ENABLED", True)
    try:
        config.ENABLE_NATIVE_TOOL_DECISION = True
        config.NATIVE_TOOL_DECISION_ALLOWLIST = "web_search"
        config.PRE_RETRIEVAL_DEFAULT_ENABLED = False
        config.ENABLE_SEMANTIC_MEMORY = False
        config.VISION_ENABLED = False
        if str(args.base_dir or "").strip():
            summary = run_smoke(
                base_dir=Path(args.base_dir),
                message=str(args.message),
                user_id=str(args.user_id),
                stream=bool(args.stream),
                real_web_search=bool(args.real_web_search),
            )
        else:
            run_dir = (
                PROJECT_ROOT
                / "reports"
                / "native_web_search_smoke_runtime"
                / f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
            )
            summary = run_smoke(
                base_dir=run_dir,
                message=str(args.message),
                user_id=str(args.user_id),
                stream=bool(args.stream),
                real_web_search=bool(args.real_web_search),
            )
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if smoke_passed(summary, stream=bool(args.stream)) else 1
    finally:
        config.ENABLE_NATIVE_TOOL_DECISION = original_native_enabled
        config.NATIVE_TOOL_DECISION_ALLOWLIST = original_native_allowlist
        config.PRE_RETRIEVAL_DEFAULT_ENABLED = original_pre_retrieval
        config.ENABLE_SEMANTIC_MEMORY = original_semantic_memory
        config.VISION_ENABLED = original_vision


def run_smoke(*, base_dir: Path, message: str, user_id: str, stream: bool, real_web_search: bool) -> dict[str, Any]:
    engine = AkaneMemoryEngine(base_dir)
    web_search_handler = (
        engine.tool_handlers.get("web_search")
        if real_web_search
        else SmokeWebSearchToolHandler(config_base_dir=base_dir)
    )
    if real_web_search and hasattr(web_search_handler, "config_base_dir"):
        web_search_handler.config_base_dir = getattr(config, "DATA_DIR", "users_data")
    if web_search_handler is None:
        web_search_handler = SmokeWebSearchToolHandler(config_base_dir=base_dir)
    engine.tool_handlers = {"web_search": web_search_handler}
    engine._resolve_capability_selection = lambda **_kwargs: CapabilitySelection(
        light_hints=("本轮 smoke 只暴露 web_search，用于验证 native 工具轮。",),
        tool_names=("web_search",),
        module_names=("native_web_search_smoke",),
        layer_names=("web",),
    )
    engine._schedule_visual_observations_for_payload = lambda **_kwargs: None
    before = engine.llm.snapshot_metrics()
    payload = {
        "user_id": user_id,
        "real_user_id": user_id,
        "message": message,
        "timestamp": int(time.time()),
        "client_mode": "scene_static",
        "pre_retrieval_enabled": False,
    }
    stream_events: list[dict[str, Any]] = []
    if stream:
        stream_events = list(engine.process_turn_stream(payload))
        final_output = extract_final_payload(stream_events)
    else:
        final_output = engine.process_turn(payload)
    after = engine.llm.snapshot_metrics()
    tool_events = list(final_output.get("tool_events") or [])
    assistant_working_events = [
        event for event in stream_events
        if isinstance(event, dict) and event.get("type") == "assistant_working"
    ]
    unavailable_tool_events = [
        event for event in tool_events
        if isinstance(event, dict) and str(event.get("status") or "").strip().lower() not in {"", "ok", "success"}
    ]
    executed_tool_calls = list(getattr(web_search_handler, "executed_calls", []) or [])
    return {
        "status": "ok",
        "base_dir": str(base_dir),
        "stream": bool(stream),
        "real_web_search": bool(real_web_search),
        "message": message,
        "speech": str(final_output.get("speech") or ""),
        "emotion": str(final_output.get("emotion") or ""),
        "tool_event_count": len(tool_events),
        "tool_events": tool_events,
        "unavailable_tool_event_count": len(unavailable_tool_events),
        "unavailable_tool_events": unavailable_tool_events,
        "executed_tool_calls": executed_tool_calls,
        "assistant_working_count": len(assistant_working_events),
        "assistant_working_events": assistant_working_events,
        "stream_event_types": [
            str(event.get("type") or "")
            for event in stream_events
            if isinstance(event, dict)
        ],
        "native_tool_decision_sent_delta": metric_delta(before, after, "native_tool_decision_sent"),
        "native_tool_call_extracted_delta": metric_delta(before, after, "native_tool_call_extracted"),
        "native_tool_provider_unsupported_delta": metric_delta(before, after, "native_tool_provider_unsupported"),
        "chat_json_fallbacks_delta": metric_delta(before, after, "chat_json_fallbacks"),
    }


def extract_final_payload(events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        if event.get("type") == "final":
            payload = event.get("payload")
            return dict(payload) if isinstance(payload, dict) else {}
    return {}


def smoke_passed(summary: dict[str, Any], *, stream: bool) -> bool:
    if int(summary.get("native_tool_call_extracted_delta", 0) or 0) <= 0:
        return False
    if int(summary.get("tool_event_count", 0) or 0) <= 0:
        return False
    if stream and int(summary.get("assistant_working_count", 0) or 0) <= 0:
        return False
    return True


def metric_delta(before: dict[str, int], after: dict[str, int], key: str) -> int:
    return int(after.get(key, 0) or 0) - int(before.get(key, 0) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
