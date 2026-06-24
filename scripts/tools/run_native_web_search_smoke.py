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
from companion_v01.tool_runtime import (  # noqa: E402
    CheckInventoryToolHandler,
    InspectMediaInfoToolHandler,
    ListRemindersToolHandler,
    ReadMemoryTimelineToolHandler,
    RetrieveMemoryToolHandler,
    ToolExecutionContext,
    ToolExecutionResult,
    WebSearchToolHandler,
)


DEFAULT_MESSAGE = "查一下今天上海天气。"
DEFAULT_MEMORY_MESSAGE = "你还记得我之前跟你说过我最喜欢喝什么咖啡吗？"


def toolset_allowlist(toolset: str) -> str:
    normalized = str(toolset or "web_search").strip().lower()
    if normalized == "memory":
        return "retrieve_memory,read_memory_timeline"
    if normalized == "all":
        # Mirror the shipped default native allowlist (the full set that fires
        # under native-first), so `all` is a real full-toolset smoke.
        return "web_search,retrieve_memory,read_memory_timeline,list_reminders,check_inventory,inspect_media_info"
    return "web_search"


def default_message_for_toolset(toolset: str) -> str:
    return DEFAULT_MEMORY_MESSAGE if str(toolset or "").strip().lower() == "memory" else DEFAULT_MESSAGE


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


class _SmokeTimelineService:
    """Minimal timeline service: only normalize_time_periods is used (the smoke
    handler overrides execute), so no DB / real reads are touched."""

    _PERIODS = ("morning", "afternoon", "night", "midnight")

    def normalize_time_periods(self, values: Any) -> list[str]:
        normalized: list[str] = []
        for value in values or []:
            period = str(value or "").strip().lower()
            if period in self._PERIODS and period not in normalized:
                normalized.append(period)
        return normalized


class SmokeRetrieveMemoryHandler(RetrieveMemoryToolHandler):
    """Deterministic retrieve_memory executor: canned recall, no real store."""

    def __init__(self) -> None:
        super().__init__(retrieve_fn=self._fixture_retrieve)
        self.executed_calls: list[dict[str, Any]] = []

    def _fixture_retrieve(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        self.executed_calls.append(
            {str(key): value for key, value in dict(call or {}).items() if not str(key).startswith("_tool_")}
        )
        query = str(call.get("query") or "").strip()
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "retrieve_memory_completed",
                    "provider": "smoke_fixture",
                    "status": "ok",
                    "query": query,
                }
            ],
            followup_context=(
                "【smoke retrieve_memory 结果】\n"
                f"查询：{query}\n"
                "命中：主人最喜欢的是冰美式。这是 smoke 固定记忆，用来验证 native 记忆工具轮"
                "是否能把工具结果交给最终回复。"
            ),
            state_updates={"retrieve_memory_status": "ok", "retrieve_memory_smoke": True},
        )


class SmokeReadMemoryTimelineHandler(ReadMemoryTimelineToolHandler):
    """Deterministic read_memory_timeline executor: canned transcript, no DB."""

    def __init__(self) -> None:
        super().__init__(timeline_service=_SmokeTimelineService())
        self.executed_calls: list[dict[str, Any]] = []

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        self.executed_calls.append(
            {str(key): value for key, value in dict(call or {}).items() if not str(key).startswith("_tool_")}
        )
        date_from = str(call.get("date_from") or "")
        date_to = str(call.get("date_to") or "")
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "read_memory_timeline_completed",
                    "provider": "smoke_fixture",
                    "status": "ok",
                    "date_from": date_from,
                    "date_to": date_to,
                }
            ],
            followup_context=(
                "【smoke read_memory_timeline 结果】\n"
                f"范围：{date_from} ~ {date_to}\n"
                "原文：（smoke 固定逐句记录，用来验证 native 时间线工具轮能把结果交给最终回复）。"
            ),
            state_updates={"memory_timeline_status": "ok", "memory_timeline_smoke": True},
        )


class SmokeListRemindersHandler(ListRemindersToolHandler):
    """Deterministic list_reminders executor: canned reminders, no store."""

    def __init__(self) -> None:
        super().__init__(store=None)
        self.executed_calls: list[dict[str, Any]] = []

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        self.executed_calls.append(
            {str(key): value for key, value in dict(call or {}).items() if not str(key).startswith("_tool_")}
        )
        status = str(call.get("status") or "pending")
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[{"type": "reminder_list", "status": status, "items": []}],
            followup_context=(
                "【smoke list_reminders 结果】\n"
                f"状态：{status}\n"
                "1. [今晚 20:00] 给妈妈打电话（smoke 固定提醒，用来验证 native 提醒工具轮能把结果交给最终回复）。"
            ),
            state_updates={"reminder_list_status": "ok", "reminder_list_smoke": True},
        )


class SmokeCheckInventoryHandler(CheckInventoryToolHandler):
    """Deterministic check_inventory executor: canned inventory, no gift service."""

    def __init__(self) -> None:
        super().__init__(gift_service=None)
        self.executed_calls: list[dict[str, Any]] = []

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        self.executed_calls.append(
            {str(key): value for key, value in dict(call or {}).items() if not str(key).startswith("_tool_")}
        )
        scope = str(call.get("scope") or "pending_recent")
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[{"type": "inventory_snapshot", "scope": scope, "items": [], "total_count": 1, "overflow_count": 0}],
            followup_context=(
                "【smoke check_inventory 结果】\n"
                f"范围：{scope}\n"
                "1. 一束向日葵（smoke 固定库存，用来验证 native 库存工具轮能把结果交给最终回复）。"
            ),
            state_updates={"inventory_status": "ok", "inventory_smoke": True},
        )


class SmokeInspectMediaInfoHandler(InspectMediaInfoToolHandler):
    """Deterministic inspect_media_info executor: canned specs, no disk."""

    def __init__(self) -> None:
        super().__init__(generated_file_service=None)
        self.executed_calls: list[dict[str, Any]] = []

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        self.executed_calls.append(
            {str(key): value for key, value in dict(call or {}).items() if not str(key).startswith("_tool_")}
        )
        source_id = str(call.get("source_id") or "")
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "media_info_inspected",
                    "source_id": source_id,
                    "media_info": {"duration_seconds": 183, "codec": "aac", "sample_rate": 44100, "channels": 2},
                }
            ],
            followup_context=(
                "【smoke inspect_media_info 结果】\n"
                f"目标：{source_id}\n"
                "规格：时长 3 分 3 秒、编码 aac、采样率 44100Hz、双声道（smoke 固定规格，用来验证 native 媒体工具轮能把结果交给最终回复）。"
            ),
            state_updates={"media_info_status": "ok", "media_info_smoke": True},
        )


def _build_smoke_tools(
    engine: AkaneMemoryEngine,
    *,
    toolset: str,
    real_web_search: bool,
    base_dir: Path,
) -> tuple[dict[str, Any], CapabilitySelection]:
    normalized = str(toolset or "web_search").strip().lower()
    if normalized == "memory":
        handlers: dict[str, Any] = {
            "retrieve_memory": SmokeRetrieveMemoryHandler(),
            "read_memory_timeline": SmokeReadMemoryTimelineHandler(),
        }
        selection = CapabilitySelection(
            light_hints=("本轮 smoke 只暴露 retrieve_memory / read_memory_timeline，用于验证 native 记忆工具轮。",),
            tool_names=("retrieve_memory", "read_memory_timeline"),
            module_names=("native_memory_smoke",),
            layer_names=("memory",),
        )
        return handlers, selection

    if normalized == "all":
        all_web_search = (
            engine.tool_handlers.get("web_search")
            if real_web_search
            else SmokeWebSearchToolHandler(config_base_dir=base_dir)
        )
        if all_web_search is None:
            all_web_search = SmokeWebSearchToolHandler(config_base_dir=base_dir)
        all_handlers: dict[str, Any] = {
            "web_search": all_web_search,
            "retrieve_memory": SmokeRetrieveMemoryHandler(),
            "read_memory_timeline": SmokeReadMemoryTimelineHandler(),
            "list_reminders": SmokeListRemindersHandler(),
            "check_inventory": SmokeCheckInventoryHandler(),
            "inspect_media_info": SmokeInspectMediaInfoHandler(),
        }
        all_selection = CapabilitySelection(
            light_hints=("本轮 smoke 暴露默认 native allowlist 全集，用于验证 native 工具轮在全工具下的闭环。",),
            tool_names=tuple(all_handlers.keys()),
            module_names=("native_all_smoke",),
            layer_names=("all",),
        )
        return all_handlers, all_selection

    web_search_handler = (
        engine.tool_handlers.get("web_search")
        if real_web_search
        else SmokeWebSearchToolHandler(config_base_dir=base_dir)
    )
    if real_web_search and hasattr(web_search_handler, "config_base_dir"):
        web_search_handler.config_base_dir = getattr(config, "DATA_DIR", "users_data")
    if web_search_handler is None:
        web_search_handler = SmokeWebSearchToolHandler(config_base_dir=base_dir)
    selection = CapabilitySelection(
        light_hints=("本轮 smoke 只暴露 web_search，用于验证 native 工具轮。",),
        tool_names=("web_search",),
        module_names=("native_web_search_smoke",),
        layer_names=("web",),
    )
    return {"web_search": web_search_handler}, selection


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one real AkaneMemoryEngine.process_turn with native web_search enabled. "
            "The LLM is real; web_search execution is a deterministic local fixture."
        )
    )
    parser.add_argument(
        "--toolset",
        choices=["web_search", "memory", "all"],
        default="web_search",
        help=(
            "Which native tool family to smoke. Default web_search keeps existing behavior. "
            "all exposes the full default native allowlist."
        ),
    )
    parser.add_argument(
        "--message",
        default="",
        help="Smoke prompt. Defaults to a toolset-appropriate message when omitted.",
    )
    parser.add_argument("--user-id", default="native_tool_smoke")
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
    toolset = str(args.toolset or "web_search")
    message = str(args.message or "").strip() or default_message_for_toolset(toolset)
    try:
        config.ENABLE_NATIVE_TOOL_DECISION = True
        config.NATIVE_TOOL_DECISION_ALLOWLIST = toolset_allowlist(toolset)
        config.PRE_RETRIEVAL_DEFAULT_ENABLED = False
        config.ENABLE_SEMANTIC_MEMORY = False
        config.VISION_ENABLED = False
        if str(args.base_dir or "").strip():
            summary = run_smoke(
                base_dir=Path(args.base_dir),
                message=message,
                user_id=str(args.user_id),
                stream=bool(args.stream),
                real_web_search=bool(args.real_web_search),
                toolset=toolset,
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
                message=message,
                user_id=str(args.user_id),
                stream=bool(args.stream),
                real_web_search=bool(args.real_web_search),
                toolset=toolset,
            )
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if smoke_passed(summary, stream=bool(args.stream)) else 1
    finally:
        config.ENABLE_NATIVE_TOOL_DECISION = original_native_enabled
        config.NATIVE_TOOL_DECISION_ALLOWLIST = original_native_allowlist
        config.PRE_RETRIEVAL_DEFAULT_ENABLED = original_pre_retrieval
        config.ENABLE_SEMANTIC_MEMORY = original_semantic_memory
        config.VISION_ENABLED = original_vision


def run_smoke(
    *,
    base_dir: Path,
    message: str,
    user_id: str,
    stream: bool,
    real_web_search: bool,
    toolset: str = "web_search",
) -> dict[str, Any]:
    normalized_toolset = str(toolset or "web_search").strip().lower() or "web_search"
    engine = AkaneMemoryEngine(base_dir)
    handlers, selection = _build_smoke_tools(
        engine,
        toolset=normalized_toolset,
        real_web_search=real_web_search,
        base_dir=base_dir,
    )
    engine.tool_handlers = handlers
    engine._resolve_capability_selection = lambda **_kwargs: selection
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
    executed_tool_calls: list[dict[str, Any]] = []
    for handler in handlers.values():
        executed_tool_calls.extend(list(getattr(handler, "executed_calls", []) or []))
    return {
        "status": "ok",
        "base_dir": str(base_dir),
        "toolset": normalized_toolset,
        "stream": bool(stream),
        "real_web_search": bool(real_web_search) and normalized_toolset == "web_search",
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
