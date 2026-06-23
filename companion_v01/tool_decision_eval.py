from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Sequence

from .native_tool_schema import build_openai_native_tool_specs
from .tool_invocation import LEGACY_JSON, NATIVE_OPENAI, NATIVE_TOOL_CALL_FIELD, TOOL_INVOCATION_ID_FIELD, TOOL_SOURCE_FIELD
from .tool_invocation import invocation_to_legacy_tool_call
from .tool_orchestration_engine import native_web_search_tool_schema
from .tool_orchestration_engine import execute_tool_invocation, normalize_tool_invocation
from .tool_orchestration_engine import validate_legacy_tool_call, validate_tool_invocation
from .llm_runtime import LLMRuntime
from .tool_runtime import (
    BaseToolHandler,
    CheckInventoryToolHandler,
    InspectMediaInfoToolHandler,
    ListRemindersToolHandler,
    ReadMemoryTimelineToolHandler,
    RetrieveMemoryToolHandler,
    ToolExecutionContext,
    ToolExecutionResult,
    WebSearchToolHandler,
)


ModeName = str


@dataclass(frozen=True)
class ToolDecisionEvalCase:
    eval_id: str
    user_prompt: str
    expect_tool: bool
    expected_tool_name: str = "web_search"
    expected_action: str = ""
    expected_arguments: dict[str, Any] = field(default_factory=dict)
    category: str = ""
    notes: str = ""


@dataclass(frozen=True)
class ToolDecisionModelResponse:
    final_output: dict[str, Any]
    fallback_hit: bool = False
    provider_unsupported: bool = False
    native_sent: bool = False
    native_extracted: bool = False
    metrics: dict[str, int] = field(default_factory=dict)
    error_detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolDecisionEvalCaseResult:
    eval_id: str
    mode: str
    category: str
    user_prompt: str
    expect_tool: bool
    expected_tool_name: str
    expected_action: str
    called_tool: bool
    tool_name: str
    tool_source: str
    tool_action: str
    normalized_ok: bool
    validation_ok: bool
    execution_status: str
    fallback_hit: bool
    provider_unsupported: bool
    native_sent: bool
    native_extracted: bool
    native_degraded: bool
    speech_present: bool
    comparison_eligible: bool
    expectation_met: bool
    error_code: str
    error_message: str
    metric_delta: dict[str, int] = field(default_factory=dict)
    error_detail: dict[str, Any] = field(default_factory=dict)
    raw_tool_call: dict[str, Any] | None = None
    normalized_tool_call: dict[str, Any] | None = None


ResponseProvider = Callable[[ToolDecisionEvalCase, ModeName], ToolDecisionModelResponse]


DEFAULT_WEB_SEARCH_EVAL_CASES: tuple[ToolDecisionEvalCase, ...] = (
    ToolDecisionEvalCase(
        eval_id="latest_release_news",
        user_prompt="帮我查一下 Akane 项目依赖的最新 OpenAI API 变更。",
        expect_tool=True,
        expected_action="search",
        expected_arguments={"action": "search", "query": "OpenAI API latest changes", "max_results": 5},
        category="current_info",
    ),
    ToolDecisionEvalCase(
        eval_id="public_url_extract",
        user_prompt="把这个公开网页的主要内容提取一下：https://example.com/news",
        expect_tool=True,
        expected_action="extract",
        expected_arguments={"action": "extract", "url": "https://example.com/news", "max_chars": 3000},
        category="url_extract",
    ),
    ToolDecisionEvalCase(
        eval_id="weather_current",
        user_prompt="查一下今天上海的天气。",
        expect_tool=True,
        expected_action="search",
        expected_arguments={"action": "search", "query": "上海 今天 天气", "max_results": 5},
        category="current_info",
    ),
    ToolDecisionEvalCase(
        eval_id="batch_compare",
        user_prompt="帮我搜一下 Claude Code 和 Codex CLI 最近的工具调用设计差异。",
        expect_tool=True,
        expected_action="batch_search",
        expected_arguments={
            "action": "batch_search",
            "queries": ["Claude Code tool calling design", "Codex CLI tool calling design"],
            "max_results": 3,
        },
        category="current_info",
    ),
    ToolDecisionEvalCase(
        eval_id="stable_fact_no_search",
        user_prompt="法国首都是哪里？",
        expect_tool=False,
        category="stable_fact",
    ),
    ToolDecisionEvalCase(
        eval_id="casual_chat_no_search",
        user_prompt="今天有点累，你陪我聊会儿。",
        expect_tool=False,
        category="casual",
    ),
    ToolDecisionEvalCase(
        eval_id="private_localhost_no_search",
        user_prompt="帮我提取 http://localhost:9999/private 里面的内容。",
        expect_tool=False,
        category="unsafe_url",
    ),
    ToolDecisionEvalCase(
        eval_id="local_file_no_search",
        user_prompt="看看 F:\\Akane\\secret.txt 里写了什么。",
        expect_tool=False,
        category="local_path",
    ),
)


DEFAULT_MEMORY_EVAL_CASES: tuple[ToolDecisionEvalCase, ...] = (
    ToolDecisionEvalCase(
        eval_id="recall_coffee_preference",
        user_prompt="你还记得我之前跟你说过我最喜欢喝什么咖啡吗？",
        expect_tool=True,
        expected_tool_name="retrieve_memory",
        expected_arguments={"query": "我最喜欢的咖啡"},
        category="memory_recall",
    ),
    ToolDecisionEvalCase(
        eval_id="recall_shared_project",
        user_prompt="我们之前一起弄的那个项目叫什么名字来着？",
        expect_tool=True,
        expected_tool_name="retrieve_memory",
        expected_arguments={"query": "一起做的项目名称"},
        category="memory_recall",
    ),
    ToolDecisionEvalCase(
        eval_id="read_specific_morning",
        user_prompt="把 2026-06-01 上午我们聊的原话翻出来给我看看。",
        expect_tool=True,
        expected_tool_name="read_memory_timeline",
        expected_arguments={
            "date_from": "2026-06-01",
            "date_to": "2026-06-01",
            "time_periods": ["morning"],
        },
        category="timeline_read",
    ),
    ToolDecisionEvalCase(
        eval_id="casual_no_memory",
        user_prompt="今天有点闷，陪我说说话吧。",
        expect_tool=False,
        expected_tool_name="retrieve_memory",
        category="casual",
    ),
    ToolDecisionEvalCase(
        eval_id="stable_fact_no_memory",
        user_prompt="一公里等于多少米？",
        expect_tool=False,
        expected_tool_name="retrieve_memory",
        category="stable_fact",
    ),
)


# 6b put list_reminders / check_inventory / inspect_media_info into the default
# native allowlist but never gave them eval coverage. These cases close that gap
# so the `all` toolset validates the full set of tools that fire under native-first.
DEFAULT_READ_TIER_EVAL_CASES: tuple[ToolDecisionEvalCase, ...] = (
    ToolDecisionEvalCase(
        eval_id="list_pending_reminders",
        user_prompt="看看我现在有哪些提醒？",
        expect_tool=True,
        expected_tool_name="list_reminders",
        expected_arguments={"status": "pending"},
        category="reminder_list",
    ),
    ToolDecisionEvalCase(
        eval_id="casual_no_list_reminders",
        user_prompt="今天天气真不错，心情也好。",
        expect_tool=False,
        expected_tool_name="list_reminders",
        category="casual",
    ),
    ToolDecisionEvalCase(
        eval_id="check_pending_gifts",
        user_prompt="我手边还有哪些没拆的礼物？",
        expect_tool=True,
        expected_tool_name="check_inventory",
        expected_arguments={"scope": "pending_recent"},
        category="inventory_check",
    ),
    ToolDecisionEvalCase(
        eval_id="thanks_no_check_inventory",
        user_prompt="谢谢你一直陪着我。",
        expect_tool=False,
        expected_tool_name="check_inventory",
        category="casual",
    ),
    ToolDecisionEvalCase(
        eval_id="inspect_audio_specs",
        user_prompt="看看 audio_001 这段音频多长、码率多少。",
        expect_tool=True,
        expected_tool_name="inspect_media_info",
        expected_arguments={"source_id": "audio_001"},
        category="media_inspect",
    ),
    ToolDecisionEvalCase(
        eval_id="stable_fact_no_inspect_media",
        user_prompt="一般一首歌大概几分钟？",
        expect_tool=False,
        expected_tool_name="inspect_media_info",
        category="stable_fact",
    ),
)


def run_tool_decision_eval(
    *,
    cases: Sequence[ToolDecisionEvalCase],
    engine: Any,
    response_provider: ResponseProvider,
    modes: Sequence[str] = ("legacy", "native"),
    execute_tools: bool = False,
) -> list[ToolDecisionEvalCaseResult]:
    results: list[ToolDecisionEvalCaseResult] = []
    for mode in modes:
        normalized_mode = str(mode or "").strip() or "legacy"
        for case in cases:
            response = response_provider(case, normalized_mode)
            results.append(
                evaluate_tool_decision_case(
                    case=case,
                    mode=normalized_mode,
                    engine=engine,
                    response=response,
                    execute_tools=execute_tools,
                )
            )
    return results


def evaluate_tool_decision_case(
    *,
    case: ToolDecisionEvalCase,
    mode: str,
    engine: Any,
    response: ToolDecisionModelResponse,
    execute_tools: bool = False,
) -> ToolDecisionEvalCaseResult:
    final_output = response.final_output if isinstance(response.final_output, dict) else {}
    raw_tool_call = final_output.get("tool_call")
    if not isinstance(raw_tool_call, dict) or not raw_tool_call:
        native_tool_call = final_output.get(NATIVE_TOOL_CALL_FIELD)
        if isinstance(native_tool_call, dict) and native_tool_call:
            raw_tool_call = native_tool_call
    raw_tool_call_present = isinstance(raw_tool_call, dict) and bool(raw_tool_call)
    called_tool = raw_tool_call_present and bool(str(raw_tool_call.get("type") or "").strip())
    invocation = normalize_tool_invocation(engine, raw_tool_call) if called_tool else None
    normalized_tool_call = (
        invocation_to_legacy_tool_call(invocation, include_metadata=True)
        if invocation is not None
        else None
    )
    validation = (
        validate_legacy_tool_call(engine, raw_tool_call)
        if called_tool
        else validate_tool_invocation(engine, invocation)
    )
    execution_status = "not_requested"
    if execute_tools and invocation is not None and validation.ok:
        _result, envelope = execute_tool_invocation(
            engine,
            invocation=invocation,
            profile_user_id="tool_eval",
            session_id=f"eval_{case.eval_id}",
            visual_payload={},
            now_ts=0,
        )
        execution_status = str(envelope.status or "unknown")

    tool_name = str(invocation.name if invocation is not None else "").strip()
    tool_source = str(invocation.source if invocation is not None else "").strip()
    tool_action = str((invocation.arguments or {}).get("action") if invocation is not None else "").strip()
    normalized_ok = invocation is not None
    raw_tool_source = str(raw_tool_call.get(TOOL_SOURCE_FIELD) or "").strip() if isinstance(raw_tool_call, dict) else ""
    normalized_mode = str(mode or "").strip() or "legacy"
    native_extracted = bool(response.native_extracted or raw_tool_source == NATIVE_OPENAI)
    native_sent = bool(response.native_sent)
    native_degraded = bool(
        normalized_mode == "native"
        and native_sent
        and raw_tool_call_present
        and not native_extracted
    )
    speech_present = _final_output_has_speech(final_output)
    comparison_eligible = not bool(response.provider_unsupported) and not native_degraded
    expectation_met = _case_expectation_met(
        case=case,
        called_tool=called_tool,
        tool_name=tool_name,
        tool_action=tool_action,
        validation_ok=validation.ok,
    )
    return ToolDecisionEvalCaseResult(
        eval_id=case.eval_id,
        mode=str(mode or "").strip() or "legacy",
        category=case.category,
        user_prompt=case.user_prompt,
        expect_tool=case.expect_tool,
        expected_tool_name=case.expected_tool_name,
        expected_action=case.expected_action,
        called_tool=called_tool,
        tool_name=tool_name,
        tool_source=tool_source,
        tool_action=tool_action,
        normalized_ok=normalized_ok,
        validation_ok=bool(validation.ok),
        execution_status=execution_status,
        fallback_hit=bool(response.fallback_hit),
        provider_unsupported=bool(response.provider_unsupported),
        native_sent=native_sent,
        native_extracted=native_extracted,
        native_degraded=native_degraded,
        speech_present=speech_present,
        comparison_eligible=comparison_eligible,
        expectation_met=expectation_met,
        error_code=str(validation.code or ""),
        error_message=str(validation.message or ""),
        metric_delta=dict(response.metrics or {}),
        error_detail=dict(response.error_detail or {}),
        raw_tool_call=dict(raw_tool_call) if isinstance(raw_tool_call, dict) else None,
        normalized_tool_call=normalized_tool_call,
    )


def summarize_tool_decision_eval_results(results: Sequence[ToolDecisionEvalCaseResult]) -> dict[str, Any]:
    by_mode: dict[str, dict[str, Any]] = {}
    for mode in sorted({str(result.mode or "legacy") for result in results}):
        mode_results = [result for result in results if str(result.mode or "legacy") == mode]
        by_mode[mode] = _summarize_mode(mode_results)
    comparison = _build_mode_comparison(by_mode)
    return {
        "total_results": len(results),
        "modes": by_mode,
        "comparison": comparison,
    }


class LiveLLMToolDecisionResponseProvider:
    """Real-LLM response provider for the tool-decision eval."""

    def __init__(
        self,
        *,
        toolset: str = "web_search",
        runtime: LLMRuntime | None = None,
        temperature: float = 0.0,
        prompt_cache_key: str = "eval:tool_decision",
    ) -> None:
        self.runtime = runtime or LLMRuntime()
        self.temperature = float(temperature)
        self.prompt_cache_key = str(prompt_cache_key or "eval:tool_decision").strip() or "eval:tool_decision"
        self.toolset = str(toolset or "web_search").strip() or "web_search"
        self._handlers = _build_live_tool_decision_handlers(self.toolset)

    def __call__(self, case: ToolDecisionEvalCase, mode: str) -> ToolDecisionModelResponse:
        normalized_mode = str(mode or "").strip().lower() or "legacy"
        native_tools = _build_live_native_tool_specs(self._handlers) if normalized_mode == "native" else None
        before = self.runtime.snapshot_metrics()
        final_output = self.runtime.call_chat_json(
            system_prompt=self._build_system_prompt(mode=normalized_mode),
            user_prompt=self._build_user_prompt(case),
            fallback={"speech": "", "tool_call": None},
            temperature=self.temperature,
            prompt_cache_key=self.prompt_cache_key,
            native_tools=native_tools,
            native_tool_choice="auto" if native_tools else "",
        )
        after = self.runtime.snapshot_metrics()
        diff = _metric_diff(before, after)
        error_detail: dict[str, Any] = {}
        if diff.get("errors", 0) > 0:
            snapshot_last_error = getattr(self.runtime, "snapshot_last_error", None)
            if callable(snapshot_last_error):
                captured = snapshot_last_error()
                if isinstance(captured, dict):
                    error_detail = dict(captured)
        raw_tool_call = final_output.get("tool_call") if isinstance(final_output, dict) else None
        if (not isinstance(raw_tool_call, dict) or not raw_tool_call) and isinstance(final_output, dict):
            native_tool_call = final_output.get(NATIVE_TOOL_CALL_FIELD)
            if isinstance(native_tool_call, dict) and native_tool_call:
                raw_tool_call = native_tool_call
        raw_source = str(raw_tool_call.get(TOOL_SOURCE_FIELD) or "").strip() if isinstance(raw_tool_call, dict) else ""
        native_extracted = bool(diff.get("native_tool_call_extracted", 0) > 0 or raw_source == NATIVE_OPENAI)
        return ToolDecisionModelResponse(
            final_output=final_output if isinstance(final_output, dict) else {"speech": "", "tool_call": None},
            fallback_hit=bool(diff.get("chat_json_fallbacks", 0) > 0),
            provider_unsupported=bool(diff.get("native_tool_provider_unsupported", 0) > 0),
            native_sent=bool(diff.get("native_tool_decision_sent", 0) > 0),
            native_extracted=native_extracted,
            metrics=diff,
            error_detail=error_detail,
        )

    def _build_system_prompt(self, *, mode: str) -> str:
        tool_names = sorted(str(name) for name in self._handlers.keys())
        tool_name_text = "、".join(tool_names)
        base = [
            "你是 Akane 工具决策评测器。只判断这一轮是否需要工具，不执行工具本身。",
            f"本轮可用工具：{tool_name_text}。",
            *_build_live_tool_policy_lines(tool_names),
        ]
        if mode == "native":
            base.extend(
                [
                    "如果需要任一可用工具，请直接使用 provider native tool_calls 通道；调用工具时不要输出 JSON 正文。",
                    "只有不需要工具时，才返回一个 JSON object：{\"speech\":\"\", \"tool_call\":null}。",
                    f"不要在 JSON 的 tool_call 字段里手写这些 native 工具：{tool_name_text}。",
                ]
            )
        else:
            base.extend(
                [
                    "必须返回一个 JSON object，字段固定为 speech 和 tool_call；不要输出 Markdown。",
                    "如果不需要工具，tool_call 必须是 null。",
                    "本轮没有 native tool 通道；如果需要工具，必须在 JSON 的 tool_call 字段里写工具调用。",
                    _build_legacy_tool_instructions(self._handlers),
                ]
            )
        return "\n".join(part for part in base if str(part).strip())

    def _build_user_prompt(self, case: ToolDecisionEvalCase) -> str:
        return (
            "评测样本：\n"
            f"id: {case.eval_id}\n"
            f"category: {case.category}\n"
            f"用户消息: {case.user_prompt}\n"
            "请只做工具决策。"
        )


class LiveLLMWebSearchResponseProvider(LiveLLMToolDecisionResponseProvider):
    """Compatibility alias for the historical web_search-only live provider."""

    def __init__(
        self,
        *,
        runtime: LLMRuntime | None = None,
        temperature: float = 0.0,
        prompt_cache_key: str = "eval:tool_decision",
    ) -> None:
        super().__init__(
            toolset="web_search",
            runtime=runtime,
            temperature=temperature,
            prompt_cache_key=prompt_cache_key,
        )


def tool_decision_results_as_dicts(results: Iterable[ToolDecisionEvalCaseResult]) -> list[dict[str, Any]]:
    return [asdict(result) for result in results]


def scripted_web_search_response_provider(
    case: ToolDecisionEvalCase,
    mode: str,
) -> ToolDecisionModelResponse:
    if not case.expect_tool:
        return ToolDecisionModelResponse(
            final_output={"speech": "我可以直接回答。", "tool_call": None}
        )
    tool_call = {"type": case.expected_tool_name, **dict(case.expected_arguments or {})}
    if str(mode or "").strip().lower() == "native":
        tool_call[TOOL_SOURCE_FIELD] = NATIVE_OPENAI
        tool_call[TOOL_INVOCATION_ID_FIELD] = f"call_{case.eval_id}"
    return ToolDecisionModelResponse(
        final_output={"speech": "我查一下。", "tool_call": tool_call},
        native_sent=str(mode or "").strip().lower() == "native",
        native_extracted=str(mode or "").strip().lower() == "native",
    )


# The scripted provider is already tool-agnostic (it keys off
# case.expected_tool_name / expected_arguments). Expose a neutral name for
# memory / future toolsets while keeping the historical web_search alias.
scripted_tool_decision_response_provider = scripted_web_search_response_provider


def build_dry_run_web_search_eval_engine() -> Any:
    return _DryRunToolDecisionEngine({"web_search": _DryRunWebSearchHandler()})


def _dry_run_retrieve_memory(*, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
    query = str((call or {}).get("query") or "").strip()
    return ToolExecutionResult(
        tool_type="retrieve_memory",
        followup_context=f"[dry_run] retrieve_memory query={query}",
        state_updates={"retrieve_memory_status": "dry_run"},
    )


class _StubTimelineService:
    """Minimal timeline service for dry-run eval: no DB, no real reads."""

    _ALLOWED_PERIODS = ("morning", "afternoon", "night", "midnight")

    def normalize_time_periods(self, values: Any) -> list[str]:
        normalized: list[str] = []
        for value in values or []:
            period = str(value or "").strip().lower()
            if period in self._ALLOWED_PERIODS and period not in normalized:
                normalized.append(period)
        return normalized

    def read(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "ok",
            "reason": "",
            "date_from": str(kwargs.get("date_from") or ""),
            "date_to": str(kwargs.get("date_to") or ""),
            "time_periods": list(kwargs.get("time_periods") or []),
            "active_dates": [],
            "message_count": 0,
        }

    def render_tool_context(self, result: dict[str, Any]) -> str:
        return f"[dry_run] read_memory_timeline status={str((result or {}).get('status') or '')}"


def _build_dry_run_memory_handlers() -> dict[str, Any]:
    return {
        "retrieve_memory": RetrieveMemoryToolHandler(retrieve_fn=_dry_run_retrieve_memory),
        "read_memory_timeline": ReadMemoryTimelineToolHandler(timeline_service=_StubTimelineService()),
    }


def build_dry_run_memory_eval_engine() -> Any:
    return _DryRunToolDecisionEngine(_build_dry_run_memory_handlers())


class _StubReminderStore:
    """Minimal reminder store for dry-run eval: canned list, no DB."""

    def list_reminders(self, *, profile_user_id: str, session_id: str, status: str, limit: int) -> list[dict[str, Any]]:
        reminders = [
            {"reminder_id": "rem_dry_1", "content": "晚上八点给妈妈打电话", "due_ts": 0, "raw_time_text": "晚上八点"},
        ]
        return reminders[: max(1, int(limit or 5))]


class _StubGiftInventoryService:
    """Minimal gift service for dry-run eval: canned inventory, no store."""

    def list_inventory(self, *, profile_user_id: str, session_id: str, scope: str, limit: int) -> dict[str, Any]:
        return {
            "scope": str(scope or "pending_recent"),
            "items": [{"summary": "一束向日葵"}],
            "total_count": 1,
            "overflow_count": 0,
        }


class _StubMediaInfoService:
    """Minimal generated-file service for dry-run eval: canned specs, no disk."""

    def inspect_media_info(self, *, profile_user_id: str, session_id: str, source_target: str, timestamp: int) -> dict[str, Any]:
        return {
            "media_info": {"duration_seconds": 183, "codec": "aac", "sample_rate": 44100, "channels": 2},
            "followup_context": f"[dry_run] inspect_media_info source={source_target} duration=183s",
        }


def _build_dry_run_read_tier_handlers() -> dict[str, Any]:
    return {
        "list_reminders": ListRemindersToolHandler(store=_StubReminderStore()),
        "check_inventory": CheckInventoryToolHandler(gift_service=_StubGiftInventoryService()),
        "inspect_media_info": InspectMediaInfoToolHandler(generated_file_service=_StubMediaInfoService()),
    }


def build_dry_run_eval_engine() -> Any:
    """Combined dry-run engine: the full default native allowlist (web_search +
    read-only memory tools + reminder/inventory/media read tools)."""
    handlers: dict[str, Any] = {"web_search": _DryRunWebSearchHandler()}
    handlers.update(_build_dry_run_memory_handlers())
    handlers.update(_build_dry_run_read_tier_handlers())
    return _DryRunToolDecisionEngine(handlers)


def _build_live_tool_decision_handlers(toolset: str) -> dict[str, Any]:
    normalized = str(toolset or "web_search").strip().lower() or "web_search"
    handlers: dict[str, Any] = {}
    if normalized in {"web_search", "all"}:
        handlers["web_search"] = WebSearchToolHandler()
    if normalized in {"memory", "all"}:
        handlers.update(_build_dry_run_memory_handlers())
    if normalized in {"read_tier", "all"}:
        handlers.update(_build_dry_run_read_tier_handlers())
    if not handlers:
        handlers["web_search"] = WebSearchToolHandler()
    return handlers


def _build_live_native_tool_specs(handlers: dict[str, Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for tool_name, handler in handlers.items():
        if tool_name == "web_search":
            specs.append(native_web_search_tool_schema())
            continue
        generated = build_openai_native_tool_specs({tool_name: handler}, allowed_tool_names={tool_name})
        if generated:
            specs.append(generated[0])
    return specs


def _build_legacy_tool_instructions(handlers: dict[str, Any]) -> str:
    lines: list[str] = []
    for handler in handlers.values():
        build_prompt_instruction = getattr(handler, "build_prompt_instruction", None)
        if not callable(build_prompt_instruction):
            continue
        try:
            instruction = str(build_prompt_instruction() or "").strip()
        except Exception:
            instruction = ""
        if instruction:
            lines.append(instruction)
    return "\n".join(lines)


def _build_live_tool_policy_lines(tool_names: Sequence[str]) -> list[str]:
    names = {str(name or "").strip() for name in tool_names}
    lines: list[str] = []
    if "web_search" in names:
        lines.extend(
            [
                "web_search 只用于公开网页搜索、最新信息核对、公开 URL 内容提取。",
                "用户问今天/现在/最新/最近/天气/版本/API 变更等当前公开信息时，应调用 web_search。",
                "用户要求比较多个公开项目或多个独立搜索目标的最近信息时，优先用 web_search 的 batch_search。",
                "不要用 web_search 访问 localhost、内网地址、file 路径、登录页、付费页或私密链接。",
            ]
        )
    if "retrieve_memory" in names:
        lines.extend(
            [
                "retrieve_memory 只用于当前上下文不足时回想用户的旧事实、偏好、称呼、约定、项目或过往事件。",
                "当用户问“之前/以前/上次/来着/还记得”这类旧共同经历、旧项目、旧偏好或旧约定，且本轮提示没有直接给出答案时，应调用 retrieve_memory。",
            ]
        )
    if "read_memory_timeline" in names:
        lines.append(
            "read_memory_timeline 只用于用户明确要求查看某一天、日期范围或上午/下午/夜晚/凌晨的原始逐句对话。"
        )
    if {"retrieve_memory", "read_memory_timeline"} & names:
        lines.append("不要为了普通闲聊、稳定常识、或当前上下文已经足够的问题调用记忆工具。")
    if "list_reminders" in names:
        lines.append(
            "list_reminders 只用于用户想查看自己当前有哪些提醒；普通闲聊或设置新提醒时不要调用。"
        )
    if "check_inventory" in names:
        lines.append(
            "check_inventory 只用于用户问手边或礼物箱里有哪些礼物；与礼物无关的闲聊不要调用。"
        )
    if "inspect_media_info" in names:
        lines.append(
            "inspect_media_info 只用于用户问某个已有音视频/文件的时长、编码、采样率、码率、分辨率、帧率等真实规格；泛泛的常识问题不要调用。"
        )
    return lines


def _case_expectation_met(
    *,
    case: ToolDecisionEvalCase,
    called_tool: bool,
    tool_name: str,
    tool_action: str,
    validation_ok: bool,
) -> bool:
    if not case.expect_tool:
        return not called_tool
    if not validation_ok:
        return False
    if tool_name != str(case.expected_tool_name or "").strip():
        return False
    expected_action = str(case.expected_action or "").strip()
    if expected_action and tool_action != expected_action:
        return False
    return True


def _summarize_mode(results: Sequence[ToolDecisionEvalCaseResult]) -> dict[str, Any]:
    total = len(results)
    called_results = [result for result in results if result.called_tool]
    executed_results = [result for result in results if result.execution_status != "not_requested"]
    validation_errors: dict[str, int] = {}
    metric_totals: dict[str, int] = {}
    llm_error_types: dict[str, int] = {}
    for result in results:
        if result.error_code:
            validation_errors[result.error_code] = validation_errors.get(result.error_code, 0) + 1
        for key, value in dict(result.metric_delta or {}).items():
            metric_totals[str(key)] = metric_totals.get(str(key), 0) + int(value or 0)
        error_type = str(dict(result.error_detail or {}).get("type") or "").strip()
        if error_type:
            llm_error_types[error_type] = llm_error_types.get(error_type, 0) + 1
    return {
        "case_count": total,
        "expected_tool_cases": sum(1 for result in results if result.expect_tool),
        "no_tool_cases": sum(1 for result in results if not result.expect_tool),
        "tool_call_count": len(called_results),
        "web_search_call_count": sum(1 for result in results if result.tool_name == "web_search"),
        "native_source_call_count": sum(1 for result in results if result.tool_source == NATIVE_OPENAI),
        "legacy_source_call_count": sum(1 for result in results if result.tool_source == LEGACY_JSON),
        "expectation_match_count": sum(1 for result in results if result.expectation_met),
        "expectation_match_rate": _rate(sum(1 for result in results if result.expectation_met), total),
        "normalize_success_count": sum(1 for result in called_results if result.normalized_ok),
        "normalize_success_rate": _rate(sum(1 for result in called_results if result.normalized_ok), len(called_results)),
        "validation_success_count": sum(1 for result in called_results if result.validation_ok),
        "validation_success_rate": _rate(sum(1 for result in called_results if result.validation_ok), len(called_results)),
        "execution_success_count": sum(1 for result in executed_results if result.execution_status == "ok"),
        "execution_success_rate": _rate(
            sum(1 for result in executed_results if result.execution_status == "ok"),
            len(executed_results),
        ),
        "fallback_hit_count": sum(1 for result in results if result.fallback_hit),
        "provider_unsupported_count": sum(1 for result in results if result.provider_unsupported),
        "llm_error_count": int(metric_totals.get("errors", 0)),
        "chat_json_fallback_metric_count": int(metric_totals.get("chat_json_fallbacks", 0)),
        "native_sent_count": sum(1 for result in results if result.native_sent),
        "native_extracted_count": sum(1 for result in results if result.native_extracted),
        "native_degraded_count": sum(1 for result in results if result.native_degraded),
        "speech_present_count": sum(1 for result in results if result.speech_present),
        "comparison_eligible_count": sum(1 for result in results if result.comparison_eligible),
        "comparison_excluded_ids": [result.eval_id for result in results if not result.comparison_eligible],
        "eligible_expectation_match_rate": _rate(
            sum(1 for result in results if result.comparison_eligible and result.expectation_met),
            sum(1 for result in results if result.comparison_eligible),
        ),
        "validation_errors": validation_errors,
        "llm_error_types": llm_error_types,
        "metric_totals": metric_totals,
        "mismatch_ids": [result.eval_id for result in results if not result.expectation_met],
    }


def _build_mode_comparison(by_mode: dict[str, dict[str, Any]]) -> dict[str, Any]:
    legacy = by_mode.get("legacy")
    native = by_mode.get("native")
    if not legacy or not native:
        return {}
    return {
        "native_vs_legacy_expectation_match_rate_delta": round(
            float(native.get("eligible_expectation_match_rate") or 0.0)
            - float(legacy.get("eligible_expectation_match_rate") or 0.0),
            4,
        ),
        "native_vs_legacy_fallback_hit_delta": int(native.get("fallback_hit_count") or 0)
        - int(legacy.get("fallback_hit_count") or 0),
        "native_vs_legacy_validation_success_rate_delta": round(
            float(native.get("validation_success_rate") or 0.0)
            - float(legacy.get("validation_success_rate") or 0.0),
            4,
        ),
        "native_comparison_excluded_ids": list(native.get("comparison_excluded_ids") or []),
    }


def _rate(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(float(count) / float(total), 4)


def _metric_diff(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    keys = set(before.keys()) | set(after.keys())
    diff: dict[str, int] = {}
    for key in sorted(keys):
        delta = int(after.get(key, 0) or 0) - int(before.get(key, 0) or 0)
        if delta:
            diff[key] = delta
    return diff


def _final_output_has_speech(final_output: dict[str, Any]) -> bool:
    if str(final_output.get("speech") or "").strip():
        return True
    segments = final_output.get("speech_segments")
    if isinstance(segments, list):
        return any(str(item or "").strip() for item in segments)
    return False


class _DryRunToolDecisionEngine:
    def __init__(self, handlers: dict[str, Any]) -> None:
        self._handlers = dict(handlers)

    def _resolve_tool_handlers(self, **_kwargs: Any) -> dict[str, Any]:
        return dict(self._handlers)


class _DryRunWebSearchHandler(BaseToolHandler):
    tool_type = "web_search"

    def __init__(self) -> None:
        self._normalizer = WebSearchToolHandler()

    def build_prompt_instruction(self) -> str:
        return self._normalizer.build_prompt_instruction()

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        return self._normalizer.normalize_call(value)

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        action = str(call.get("action") or "search").strip() or "search"
        return ToolExecutionResult(
            tool_type=self.tool_type,
            followup_context=f"[dry_run] web_search action={action}",
            state_updates={"web_search_status": "dry_run", "web_search_action": action},
        )
