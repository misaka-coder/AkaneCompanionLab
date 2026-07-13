from __future__ import annotations

from datetime import date, datetime, time as datetime_time
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

from services.market_data import (
    MarketDataValidationError,
    MarketNewsQuery,
    MarketQuoteRequest,
    MarketSeriesRequest,
)

from ..tool_runtime import BaseToolHandler, ToolExecutionContext, ToolExecutionResult, ToolMetadata
from .chart_provider import ChartRequest, LocalChartProvider
from .market_service import MarketDataToolService
from .report_provider import (
    FinanceChartReference,
    FinanceQuoteEvidence,
    FinanceReportProvider,
    FinanceReportRequest,
    FinanceSeriesEvidence,
)


_ZONE = ZoneInfo("Asia/Shanghai")
_FINANCE_METADATA = {
    "family": "finance_read",
    "operation": "read",
    "risk": "low",
    "default_round_budget": 12,
}

MARKET_RESOLVE_SECURITY_SCHEMA: dict[str, Any] = {
    "description": (
        "Resolve a user-provided security name or alias to a trusted provider code using the local security master "
        "and the current session watchlist. Call this before quote/news/series tools when the user did not provide "
        "a complete provider code. The query should be the literal security name or alias copied from the user's "
        "message, not the full task sentence and not a guessed vendor ticker such as ^N225. Never manufacture "
        ".SH/.SZ/.BJ suffixes."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": 120},
        "limit": {"type": "integer", "minimum": 1, "maximum": 10},
    },
    "required": ["query"],
}

MARKET_NEWS_SEARCH_SCHEMA: dict[str, Any] = {
    "description": (
        "Search stored and provider-backed market news using explicit provider security codes. "
        "Use for current announcements or news; results include source and as-of evidence. "
        "Never invent an exchange suffix: codes must come from a provider or trusted master data."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": 200},
        "codes": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        "content_types": {"type": "array", "items": {"type": "string"}, "maxItems": 16},
        "date_from": {"type": "string", "description": "Optional YYYY-MM-DD lower bound."},
        "date_to": {"type": "string", "description": "Optional YYYY-MM-DD upper bound."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
    },
}

MARKET_QUOTE_SNAPSHOT_SCHEMA: dict[str, Any] = {
    "description": (
        "Read current quote snapshots for explicit provider security codes. "
        "Returns provider, source, as-of time, raw quote fields, and program-computed change/range metrics."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "codes": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20},
    },
    "required": ["codes"],
}

MARKET_PRICE_SERIES_SCHEMA: dict[str, Any] = {
    "description": (
        "Read a historical OHLCV price series for one explicit provider security code. "
        "Returns source/as-of evidence plus deterministic return, range, volatility, moving-average, "
        "drawdown, breakout, and relative-volume metrics."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "code": {"type": "string", "minLength": 1, "maxLength": 40},
        "interval": {"type": "string", "enum": ["1d"]},
        "adjusted": {"type": "string", "enum": ["none"]},
        "date_from": {"type": "string", "description": "Optional YYYY-MM-DD lower bound."},
        "date_to": {"type": "string", "description": "Optional YYYY-MM-DD upper bound."},
        "limit": {"type": "integer", "minimum": 2, "maximum": 500},
    },
    "required": ["code"],
}

RENDER_MARKET_CHART_SCHEMA: dict[str, Any] = {
    "description": (
        "Render a deterministic local PNG from trusted provider OHLCV data. "
        "The model selects only a fixed chart enum and bounded parameters; it cannot pass price arrays, "
        "file paths, plotting code, or arbitrary styles."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "code": {"type": "string", "minLength": 1, "maxLength": 40},
        "chart_type": {"type": "string", "enum": ["candlestick_volume"]},
        "interval": {"type": "string", "enum": ["1d"]},
        "adjusted": {"type": "string", "enum": ["none"]},
        "lookback": {"type": "integer", "minimum": 20, "maximum": 250},
        "moving_averages": {
            "type": "array",
            "items": {"type": "integer", "enum": [5, 10, 20, 60]},
            "maxItems": 4,
            "uniqueItems": True,
        },
        "title": {"type": "string", "maxLength": 80},
        "send_to_user": {"type": "boolean"},
    },
    "required": ["code"],
}

COMPOSE_FINANCE_REPORT_SCHEMA: dict[str, Any] = {
    "description": (
        "Create a deterministic finance report from freshly fetched trusted quotes/series and optional "
        "render_market_chart outputs. The model may provide clearly labeled analysis/risk prose, but cannot "
        "pass raw market data, arbitrary files, templates, code, or paths."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "report_type": {"type": "string", "enum": ["security_brief", "market_comparison"]},
        "codes": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 40},
            "minItems": 1,
            "maxItems": 5,
            "uniqueItems": True,
        },
        "output_format": {"type": "string", "enum": ["md", "pdf", "xlsx"]},
        "interval": {"type": "string", "enum": ["1d"]},
        "adjusted": {"type": "string", "enum": ["none"]},
        "lookback": {"type": "integer", "minimum": 20, "maximum": 250},
        "chart_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 120},
            "maxItems": 5,
            "uniqueItems": True,
        },
        "title": {"type": "string", "maxLength": 100},
        "analysis_summary": {"type": "string", "maxLength": 6000},
        "risk_notes": {"type": "array", "items": {"type": "string", "maxLength": 500}, "maxItems": 10},
        "watch_items": {"type": "array", "items": {"type": "string", "maxLength": 500}, "maxItems": 10},
        "send_to_user": {"type": "boolean"},
    },
    "required": ["report_type", "codes", "output_format"],
}


class _FinanceReadToolHandler(BaseToolHandler):
    input_schema: dict[str, Any] = {}
    required_provider_capabilities: tuple[str, ...] = ()
    require_provider_health = True

    def __init__(self, *, service: MarketDataToolService) -> None:
        self.service = service

    def tool_metadata(self) -> ToolMetadata:
        return ToolMetadata(**_FINANCE_METADATA, input_schema=self.input_schema)

    def capability_status(self) -> dict[str, Any]:
        return self.service.capability_status(
            self.required_provider_capabilities,
            require_provider_health=self.require_provider_health,
        )

    def _result(self, payload: dict[str, Any]) -> ToolExecutionResult:
        status = str(payload.get("status") or "unavailable").strip().lower()
        followup = (
            "以下是金融工具返回的结构化证据。区分来源事实、程序计算与分析推断；"
            "任何时效性结论都必须引用 as_of。\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        )
        evidence_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        event_ids, source_urls, codes = _collect_evidence_identifiers(payload.get("data"))
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "finance_tool_completed",
                    "tool_type": self.tool_type,
                    "status": status,
                    "provider": str(payload.get("provider") or ""),
                    "as_of": payload.get("as_of"),
                    "reason": str(payload.get("reason") or ""),
                }
            ],
            followup_context=followup,
            state_updates={
                "finance_evidence": {
                    "tool": self.tool_type,
                    "status": status,
                    "provider": str(payload.get("provider") or ""),
                    "as_of": payload.get("as_of"),
                    "result_hash": hashlib.sha256(evidence_payload.encode("utf-8")).hexdigest(),
                    "event_ids": event_ids,
                    "source_urls": source_urls,
                    "codes": codes,
                }
            },
        )

    def _failure(self, exc: Exception) -> ToolExecutionResult:
        if isinstance(exc, MarketDataValidationError):
            payload = exc.to_public_dict()
            payload["status"] = "invalid_arguments"
        else:
            payload = {
                "ok": False,
                "status": "unavailable",
                "provider": getattr(self.service.provider, "id", "market_data"),
                "source": getattr(self.service.provider, "source", "Market Data"),
                "as_of": None,
                "reason": f"market data tool failed: {type(exc).__name__}",
                "data": None,
            }
        return self._result(payload)


class MarketResolveSecurityToolHandler(_FinanceReadToolHandler):
    tool_type = "market_resolve_security"
    input_schema = MARKET_RESOLVE_SECURITY_SCHEMA
    require_provider_health = False

    def build_prompt_instruction(self) -> str:
        return (
            "- market_resolve_security：把用户给出的证券名称/别名解析成可信 provider code。格式为 "
            '{"type":"market_resolve_security","query":"贵州茅台","limit":5}。'
            "query 只放用户原文里的证券名称/别名，不放整句任务，也不要把日经225改写成猜测的 ^N225 等 vendor symbol。"
            "用户没有直接给出完整 provider code 时，必须先用本工具；只有 resolved=true 才能直接继续查行情。"
            "not_found 只表示本次查询词没匹配，不代表 provider 不支持；先用用户原文中的纯证券名重试一次。"
            "ambiguous/needs_confirmation 时向用户澄清，不要自行拼 .SH/.SZ/.BJ。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not _is_tool_call(value, self.tool_type):
            return None
        if not _has_only_keys(value, {"type", "query", "limit"}):
            return None
        query = str(value.get("query") or "").strip()
        limit = _bounded_int(value.get("limit"), default=5, lower=1, upper=10)
        if not query or len(query) > 120 or limit is None:
            return None
        return {"type": self.tool_type, "query": query, "limit": limit}

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        try:
            query = str(call.get("query") or "")
            limit = int(call.get("limit") or 5)
            payload = self.service.resolve_security(
                query,
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                limit=limit,
            )
            resolution_status = str(payload.get("resolution_status") or "").strip()
            request_text = "\n".join(
                str(context.request_context.get(key) or "").strip()
                for key in ("message", "raw_message", "clean_message")
                if str(context.request_context.get(key) or "").strip()
            )
            if (
                not bool(payload.get("resolved"))
                and resolution_status in {"not_found", "needs_confirmation"}
                and request_text
                and request_text.strip() != query.strip()
            ):
                recovered = self.service.resolve_security(
                    request_text,
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    limit=limit,
                )
                if bool(recovered.get("resolved")):
                    recovered["reason"] = (
                        "model query did not uniquely match; recovered a unique trusted alias from the current "
                        "user message"
                    )
                    recovered["resolution_recovery"] = {
                        "used": True,
                        "source": "current_user_message",
                        "model_query": query,
                    }
                    payload = recovered
            return self._result(payload)
        except Exception as exc:
            return self._failure(exc)


class MarketNewsSearchToolHandler(_FinanceReadToolHandler):
    tool_type = "market_news_search"
    input_schema = MARKET_NEWS_SEARCH_SCHEMA
    required_provider_capabilities = ("news_search",)

    def build_prompt_instruction(self) -> str:
        return (
            '- market_news_search：查询市场新闻/公告。格式为 {"type":"market_news_search","query":"关键词",'
            '"codes":["provider代码"],"content_types":["companynews"],"date_from":"YYYY-MM-DD",'
            '"date_to":"YYYY-MM-DD","limit":10}。证券代码必须来自可信来源，不要自行拼接交易所后缀；'
            "缺少显式 codes/content_types 时只查本地事件库，不会广播查询全市场。"
            "用户只给名称/别名时先调用 market_resolve_security。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not _is_tool_call(value, self.tool_type):
            return None
        if not _has_only_keys(
            value,
            {"type", "query", "codes", "content_types", "date_from", "date_to", "limit"},
        ):
            return None
        query = str(value.get("query") or "").strip()
        if len(query) > 200:
            return None
        codes = _string_list(value.get("codes"), limit=20, max_length=40, uppercase=True)
        content_types = _string_list(value.get("content_types"), limit=16, max_length=120, lowercase=True)
        limit = _bounded_int(value.get("limit"), default=10, lower=1, upper=50)
        if codes is None or content_types is None or limit is None or (not query and not codes):
            return None
        date_from = _date_to_timestamp(value.get("date_from"), end_of_day=False)
        date_to = _date_to_timestamp(value.get("date_to"), end_of_day=True)
        if value.get("date_from") and date_from is None:
            return None
        if value.get("date_to") and date_to is None:
            return None
        try:
            request = MarketNewsQuery(
                query=query,
                codes=tuple(codes),
                content_types=tuple(content_types),
                date_from=date_from,
                date_to=date_to,
                limit=limit,
            )
        except MarketDataValidationError:
            return None
        return {
            "type": self.tool_type,
            "query": request.query,
            "codes": list(request.codes),
            "content_types": list(request.content_types),
            "date_from": request.date_from,
            "date_to": request.date_to,
            "limit": request.limit,
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        try:
            codes = self.service.canonicalize_trusted_codes(
                tuple(call.get("codes") or ()),
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
            )
            self.service.ensure_trusted_codes(
                codes,
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                request_context=context.request_context,
            )
            request = MarketNewsQuery(
                query=str(call.get("query") or ""),
                codes=codes,
                content_types=tuple(call.get("content_types") or ()),
                date_from=call.get("date_from"),
                date_to=call.get("date_to"),
                limit=call.get("limit", 10),
            )
            return self._result(self.service.search_news(request))
        except Exception as exc:
            return self._failure(exc)


class MarketQuoteSnapshotToolHandler(_FinanceReadToolHandler):
    tool_type = "market_quote_snapshot"
    input_schema = MARKET_QUOTE_SNAPSHOT_SCHEMA
    required_provider_capabilities = ("quote_snapshot",)

    def build_prompt_instruction(self) -> str:
        return (
            '- market_quote_snapshot：查询当前行情快照。格式为 {"type":"market_quote_snapshot",'
            '"codes":["600519.SH"]}。用户只给名称/别名时先调用 market_resolve_security；'
            "代码必须来自用户原文、resolver、当前会话 watchlist 或可信主数据；返回值含 as_of 和程序计算指标。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not _is_tool_call(value, self.tool_type):
            return None
        if not _has_only_keys(value, {"type", "codes", "code"}):
            return None
        codes = _string_list(value.get("codes") or value.get("code"), limit=20, max_length=40, uppercase=True)
        if codes is None:
            return None
        try:
            request = MarketQuoteRequest(codes=tuple(codes))
        except MarketDataValidationError:
            return None
        return {"type": self.tool_type, "codes": list(request.codes)}

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        try:
            codes = self.service.canonicalize_trusted_codes(
                tuple(call.get("codes") or ()),
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
            )
            self.service.ensure_trusted_codes(
                codes,
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                request_context=context.request_context,
            )
            return self._result(self.service.quote_snapshots(MarketQuoteRequest(codes=codes)))
        except Exception as exc:
            return self._failure(exc)


class MarketPriceSeriesToolHandler(_FinanceReadToolHandler):
    tool_type = "market_price_series"
    input_schema = MARKET_PRICE_SERIES_SCHEMA
    required_provider_capabilities = ("price_series",)

    def build_prompt_instruction(self) -> str:
        return (
            '- market_price_series：查询单个标的历史 OHLCV 与程序计算指标。格式为 {"type":"market_price_series",'
            '"code":"600519.SH","interval":"1d","adjusted":"none","date_from":"YYYY-MM-DD",'
            '"date_to":"YYYY-MM-DD","limit":120}。用户只给名称/别名时先调用 market_resolve_security；'
            "模型负责解释，不要自行重算或编造价格。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not _is_tool_call(value, self.tool_type):
            return None
        if not _has_only_keys(
            value,
            {"type", "code", "interval", "adjusted", "date_from", "date_to", "limit"},
        ):
            return None
        code = str(value.get("code") or "").strip().upper()
        interval = str(value.get("interval") or "1d").strip().lower()
        adjusted = str(value.get("adjusted") or "none").strip().lower()
        if interval != "1d" or adjusted != "none":
            return None
        limit = _bounded_int(value.get("limit"), default=120, lower=2, upper=500)
        if limit is None:
            return None
        date_from = _date_to_timestamp(value.get("date_from"), end_of_day=False)
        date_to = _date_to_timestamp(value.get("date_to"), end_of_day=True)
        if value.get("date_from") and date_from is None:
            return None
        if value.get("date_to") and date_to is None:
            return None
        try:
            request = MarketSeriesRequest(
                code=code,
                interval=interval,
                adjusted=adjusted,
                date_from=date_from,
                date_to=date_to,
                limit=limit,
            )
        except MarketDataValidationError:
            return None
        return {
            "type": self.tool_type,
            "code": request.code,
            "interval": request.interval,
            "adjusted": request.adjusted,
            "date_from": request.date_from,
            "date_to": request.date_to,
            "limit": request.limit,
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        try:
            codes = self.service.canonicalize_trusted_codes(
                (str(call.get("code") or ""),),
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
            )
            code = codes[0] if codes else ""
            self.service.ensure_trusted_codes(
                (code,),
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                request_context=context.request_context,
            )
            request = MarketSeriesRequest(
                code=code,
                interval=str(call.get("interval") or "1d"),
                adjusted=str(call.get("adjusted") or "none"),
                date_from=call.get("date_from"),
                date_to=call.get("date_to"),
                limit=call.get("limit", 120),
            )
            return self._result(self.service.price_series(request))
        except Exception as exc:
            return self._failure(exc)


class RenderMarketChartToolHandler(_FinanceReadToolHandler):
    tool_type = "render_market_chart"
    input_schema = RENDER_MARKET_CHART_SCHEMA
    required_provider_capabilities = ("price_series",)

    def __init__(
        self,
        *,
        service: MarketDataToolService,
        generated_file_service: Any,
        chart_provider: LocalChartProvider | None = None,
    ) -> None:
        super().__init__(service=service)
        self.generated_file_service = generated_file_service
        self.chart_provider = chart_provider or LocalChartProvider()

    def tool_metadata(self) -> ToolMetadata:
        return ToolMetadata(
            family="finance_artifact",
            operation="control",
            risk="low",
            default_round_budget=12,
            input_schema=self.input_schema,
        )

    def build_prompt_instruction(self) -> str:
        return (
            "- render_market_chart：从可信行情源重新读取 OHLCV，并用本地固定模板生成 K 线+成交量 PNG。格式为 "
            '{"type":"render_market_chart","code":"600519.SH","chart_type":"candlestick_volume",'
            '"interval":"1d","adjusted":"none","lookback":120,"moving_averages":[5,20],'
            '"title":"贵州茅台日线量价","send_to_user":true}。'
            "用户只给名称/别名时先调用 market_resolve_security；不得传价格数组、任意样式、文件路径或绘图代码。"
            "工具会校验数据、计算均线、写入 GeneratedFileStore，并返回来源、区间和 as_of。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not _is_tool_call(value, self.tool_type):
            return None
        if not _has_only_keys(
            value,
            {
                "type",
                "code",
                "chart_type",
                "interval",
                "adjusted",
                "lookback",
                "moving_averages",
                "title",
                "send_to_user",
            },
        ):
            return None
        raw_moving_averages = value.get("moving_averages", (5, 20))
        if not isinstance(raw_moving_averages, (list, tuple)) or len(raw_moving_averages) > 4:
            return None
        if isinstance(value.get("send_to_user"), bool):
            send_to_user = bool(value.get("send_to_user"))
        elif value.get("send_to_user") is None:
            send_to_user = True
        else:
            return None
        try:
            request = ChartRequest(
                code=str(value.get("code") or ""),
                chart_type=str(value.get("chart_type") or "candlestick_volume"),
                interval=str(value.get("interval") or "1d"),
                adjusted=str(value.get("adjusted") or "none"),
                lookback=value.get("lookback", 120),
                moving_averages=tuple(raw_moving_averages),
                title=str(value.get("title") or ""),
            )
        except MarketDataValidationError:
            return None
        return {
            "type": self.tool_type,
            "code": request.code,
            "chart_type": request.chart_type,
            "interval": request.interval,
            "adjusted": request.adjusted,
            "lookback": request.lookback,
            "moving_averages": list(request.moving_averages),
            "title": request.title,
            "send_to_user": send_to_user,
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        output_path = None
        try:
            request = ChartRequest(
                code=str(call.get("code") or ""),
                chart_type=str(call.get("chart_type") or "candlestick_volume"),
                interval=str(call.get("interval") or "1d"),
                adjusted=str(call.get("adjusted") or "none"),
                lookback=call.get("lookback", 120),
                moving_averages=tuple(call.get("moving_averages") or ()),
                title=str(call.get("title") or ""),
            )
            canonical_codes = self.service.canonicalize_trusted_codes(
                (request.code,),
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
            )
            if canonical_codes and canonical_codes[0] != request.code:
                request = ChartRequest(
                    code=canonical_codes[0],
                    chart_type=request.chart_type,
                    interval=request.interval,
                    adjusted=request.adjusted,
                    lookback=request.lookback,
                    moving_averages=request.moving_averages,
                    title=request.title,
                )
            self.service.ensure_trusted_codes(
                (request.code,),
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                request_context=context.request_context,
            )
            response = self.service.price_series_response(
                MarketSeriesRequest(
                    code=request.code,
                    interval=request.interval,
                    adjusted=request.adjusted,
                    limit=request.lookback,
                )
            )
            if response.status != "ok" or response.data is None:
                return self._result(response.to_public_dict())

            output_title = request.title or f"{request.code}_日线K线与成交量"
            output_path = self.generated_file_service.allocate_output_path(
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                title=output_title,
                output_format="png",
                timestamp=context.now_ts,
            )
            artifact = self.chart_provider.render(
                series=response.data,
                request=request,
                output_path=output_path,
                source=response.source,
            )
            evidence = artifact.evidence_metadata()
            summary = (
                f"{artifact.code} {artifact.date_from} 至 {artifact.date_to} 的日线 K 线与成交量图，"
                f"数据截至 {artifact.as_of}。"
            )
            source_ids = []
            if context.current_user_source_id:
                source_ids.append(str(context.current_user_source_id))
            market_event = (
                context.request_context.get("market_event")
                if isinstance(context.request_context.get("market_event"), dict)
                else {}
            )
            event_id = str(market_event.get("event_id") or "").strip()
            if event_id and event_id not in source_ids:
                source_ids.append(event_id)
            generated = self.generated_file_service.register_generated_artifact(
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                output_path=artifact.output_path,
                output_title=artifact.title,
                output_format="png",
                mime_type="image/png",
                content_card={
                    "kind": "market_chart",
                    "title": artifact.title,
                    "summary": summary,
                    "chart": evidence,
                },
                summary=summary,
                created_by_tool=self.tool_type,
                source_ids=source_ids,
                send_to_user=bool(call.get("send_to_user", True)),
                timestamp=context.now_ts,
            )
            output_path = None
            public_generated = {
                "generated_id": str(generated.get("generated_id") or ""),
                "generated_handle": str(generated.get("generated_handle") or ""),
                "output_title": str(generated.get("output_title") or artifact.title),
                "output_format": "png",
                "mime_type": "image/png",
                "file_size": int(generated.get("file_size") or 0),
                "created_by_tool": self.tool_type,
            }
            payload = {
                "ok": True,
                "status": "ok",
                "provider": artifact.provider,
                "source": artifact.source,
                "as_of": artifact.as_of,
                "reason": "",
                "data": {
                    "generated_file": public_generated,
                    "chart": evidence,
                },
            }
            base_result = self._result(payload)
            base_result.stream_events = [
                {
                    "type": "market_chart_ready",
                    "generated_file": generated,
                    "send_to_user": bool(call.get("send_to_user", True)),
                    "delivery_scope": "finance_market_chart",
                    "chart": evidence,
                }
            ]
            return base_result
        except Exception as exc:
            if output_path is not None:
                try:
                    path = Path(output_path)
                    if self.generated_file_service.is_managed_storage_path(path):
                        path.unlink(missing_ok=True)
                except Exception:
                    pass
            return self._chart_failure(exc)

    def _chart_failure(self, exc: Exception) -> ToolExecutionResult:
        if isinstance(exc, MarketDataValidationError):
            payload = exc.to_public_dict()
            if payload.get("status") not in {"invalid_arguments", "unavailable"}:
                payload["status"] = "unavailable"
            payload["data"] = None
            return self._result(payload)
        return self._failure(exc)


class ComposeFinanceReportToolHandler(_FinanceReadToolHandler):
    tool_type = "compose_finance_report"
    input_schema = COMPOSE_FINANCE_REPORT_SCHEMA
    required_provider_capabilities = ("quote_snapshot", "price_series")

    def __init__(
        self,
        *,
        service: MarketDataToolService,
        generated_file_service: Any,
        report_provider: FinanceReportProvider | None = None,
    ) -> None:
        super().__init__(service=service)
        self.generated_file_service = generated_file_service
        self.report_provider = report_provider or FinanceReportProvider()

    def tool_metadata(self) -> ToolMetadata:
        return ToolMetadata(
            family="finance_artifact",
            operation="control",
            risk="low",
            default_round_budget=12,
            input_schema=self.input_schema,
        )

    def build_prompt_instruction(self) -> str:
        return (
            "- compose_finance_report：生成带可信行情、程序指标、来源、as_of 和免责声明的金融报告。格式为 "
            '{"type":"compose_finance_report","report_type":"security_brief|market_comparison",'
            '"codes":["600519.SH"],"output_format":"md|pdf|xlsx","interval":"1d",'
            '"adjusted":"none","lookback":120,"chart_ids":["gen_001"],'
            '"title":"贵州茅台证券简报","analysis_summary":"明确标注为分析的解读",'
            '"risk_notes":["待验证风险"],"watch_items":["后续观察"],"send_to_user":true}。'
            "工具会重新读取每个代码的可信行情并计算指标；不得传原始价格数组、任意文件、模板、路径或代码。"
            "chart_ids 只能引用当前会话由 render_market_chart 生成的 PNG；PDF/XLSX 会嵌入图表，MD 会写受管相对引用。"
            "analysis_summary、risk_notes 和 watch_items 会被明确标成模型分析/风险观察，不能替代事实证据。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not _is_tool_call(value, self.tool_type):
            return None
        if not _has_only_keys(
            value,
            {
                "type",
                "report_type",
                "codes",
                "output_format",
                "interval",
                "adjusted",
                "lookback",
                "chart_ids",
                "title",
                "analysis_summary",
                "risk_notes",
                "watch_items",
                "send_to_user",
            },
        ):
            return None
        codes = _string_list(value.get("codes"), limit=5, max_length=40, uppercase=True)
        chart_ids = _artifact_id_list(value.get("chart_ids"), limit=5)
        risk_notes = _bounded_text_list(value.get("risk_notes"), limit=10, max_length=500)
        watch_items = _bounded_text_list(value.get("watch_items"), limit=10, max_length=500)
        if not codes or chart_ids is None or risk_notes is None or watch_items is None:
            return None
        if isinstance(value.get("send_to_user"), bool):
            send_to_user = bool(value.get("send_to_user"))
        elif value.get("send_to_user") is None:
            send_to_user = True
        else:
            return None
        try:
            request = FinanceReportRequest(
                report_type=str(value.get("report_type") or ""),
                codes=tuple(codes),
                output_format=str(value.get("output_format") or ""),
                interval=str(value.get("interval") or "1d"),
                adjusted=str(value.get("adjusted") or "none"),
                lookback=value.get("lookback", 120),
                chart_targets=tuple(chart_ids),
                title=str(value.get("title") or ""),
                analysis_summary=str(value.get("analysis_summary") or ""),
                risk_notes=tuple(risk_notes),
                watch_items=tuple(watch_items),
            )
        except MarketDataValidationError:
            return None
        return {
            "type": self.tool_type,
            "report_type": request.report_type,
            "codes": list(request.codes),
            "output_format": request.output_format,
            "interval": request.interval,
            "adjusted": request.adjusted,
            "lookback": request.lookback,
            "chart_ids": list(request.chart_targets),
            "title": request.title,
            "analysis_summary": request.analysis_summary,
            "risk_notes": list(request.risk_notes),
            "watch_items": list(request.watch_items),
            "send_to_user": send_to_user,
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        output_path = None
        try:
            request = FinanceReportRequest(
                report_type=str(call.get("report_type") or ""),
                codes=tuple(call.get("codes") or ()),
                output_format=str(call.get("output_format") or ""),
                interval=str(call.get("interval") or "1d"),
                adjusted=str(call.get("adjusted") or "none"),
                lookback=call.get("lookback", 120),
                chart_targets=tuple(call.get("chart_ids") or ()),
                title=str(call.get("title") or ""),
                analysis_summary=str(call.get("analysis_summary") or ""),
                risk_notes=tuple(call.get("risk_notes") or ()),
                watch_items=tuple(call.get("watch_items") or ()),
            )
            canonical_codes = self.service.canonicalize_trusted_codes(
                request.codes,
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
            )
            if canonical_codes != request.codes:
                request = FinanceReportRequest(
                    report_type=request.report_type,
                    codes=canonical_codes,
                    output_format=request.output_format,
                    interval=request.interval,
                    adjusted=request.adjusted,
                    lookback=request.lookback,
                    chart_targets=request.chart_targets,
                    title=request.title,
                    analysis_summary=request.analysis_summary,
                    risk_notes=request.risk_notes,
                    watch_items=request.watch_items,
                )
            self.service.ensure_trusted_codes(
                request.codes,
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                request_context=context.request_context,
            )
            series_evidence: list[FinanceSeriesEvidence] = []
            series_failures: list[dict[str, Any]] = []
            for code in request.codes:
                response = self.service.price_series_response(
                    MarketSeriesRequest(
                        code=code,
                        interval=request.interval,
                        adjusted=request.adjusted,
                        limit=request.lookback,
                    )
                )
                if response.status != "ok" or response.data is None:
                    series_failures.append(
                        {
                            "code": code,
                            "status": response.status,
                            "provider": response.provider,
                            "source": response.source,
                            "reason": response.reason,
                        }
                    )
                    continue
                series_evidence.append(FinanceSeriesEvidence(source=response.source, series=response.data))
            if series_failures:
                return self._result(
                    {
                        "ok": False,
                        "status": "unavailable",
                        "provider": self.service.provider.id,
                        "source": self.service.provider.source,
                        "as_of": None,
                        "reason": "report requires complete trusted series for every requested code",
                        "data": {"series_failures": series_failures},
                    }
                )

            quote_response = self.service.quote_snapshots_response(MarketQuoteRequest(codes=request.codes))
            quotes = tuple(quote_response.data) if quote_response.status == "ok" else ()
            missing_quote_codes = sorted(set(request.codes) - {quote.code for quote in quotes})
            quote_status = quote_response.status
            quote_reason = quote_response.reason
            if quote_response.status == "ok" and missing_quote_codes:
                quote_status = "partial"
                quote_reason = f"missing quote snapshots for: {', '.join(missing_quote_codes)}"
            quote_evidence = FinanceQuoteEvidence(
                source=quote_response.source,
                quotes=quotes,
                status=quote_status,
                reason=quote_reason,
            )
            chart_references = tuple(
                self._resolve_chart_reference(
                    target,
                    request=request,
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                )
                for target in request.chart_targets
            )
            output_title = request.title or (
                f"{'_'.join(request.codes)}_{'证券简报' if request.report_type == 'security_brief' else '市场对比简报'}"
            )
            output_path = self.generated_file_service.allocate_output_path(
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                title=output_title,
                output_format=request.output_format,
                timestamp=context.now_ts,
            )
            artifact = self.report_provider.render(
                request=request,
                series_evidence=tuple(series_evidence),
                quote_evidence=quote_evidence,
                chart_references=chart_references,
                output_path=output_path,
                created_at=context.now_ts,
            )
            report_metadata = artifact.public_metadata()
            source_ids = [chart.generated_id for chart in chart_references]
            if context.current_user_source_id and context.current_user_source_id not in source_ids:
                source_ids.append(str(context.current_user_source_id))
            market_event = (
                context.request_context.get("market_event")
                if isinstance(context.request_context.get("market_event"), dict)
                else {}
            )
            event_id = str(market_event.get("event_id") or "").strip()
            if event_id and event_id not in source_ids:
                source_ids.append(event_id)
            summary = (
                f"{artifact.title}（{artifact.output_format.upper()}），覆盖 {'、'.join(artifact.codes)}，"
                f"数据截至 {artifact.as_of}。"
            )
            generated = self.generated_file_service.register_generated_artifact(
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                output_path=artifact.output_path,
                output_title=artifact.title,
                output_format=artifact.output_format,
                mime_type=_finance_report_mime_type(artifact.output_format),
                content_card={
                    "kind": "finance_report",
                    "title": artifact.title,
                    "summary": summary,
                    "report": report_metadata,
                    "content_preview": artifact.markdown[:4000],
                },
                summary=summary,
                created_by_tool=self.tool_type,
                source_ids=source_ids,
                send_to_user=bool(call.get("send_to_user", True)),
                timestamp=context.now_ts,
            )
            output_path = None
            public_generated = {
                "generated_id": str(generated.get("generated_id") or ""),
                "generated_handle": str(generated.get("generated_handle") or ""),
                "output_title": str(generated.get("output_title") or artifact.title),
                "output_format": artifact.output_format,
                "mime_type": _finance_report_mime_type(artifact.output_format),
                "file_size": int(generated.get("file_size") or 0),
                "created_by_tool": self.tool_type,
            }
            payload = {
                "ok": True,
                "status": "ok",
                "provider": self.service.provider.id,
                "source": " + ".join(artifact.sources),
                "as_of": artifact.as_of,
                "reason": quote_evidence.reason if quote_evidence.status not in {"ok", "empty"} else "",
                "data": {
                    "generated_file": public_generated,
                    "report": report_metadata,
                },
            }
            base_result = self._result(payload)
            base_result.stream_events = [
                {
                    "type": "finance_report_ready",
                    "generated_file": generated,
                    "send_to_user": bool(call.get("send_to_user", True)),
                    "delivery_scope": "finance_report",
                    "report": {
                        "report_type": artifact.report_type,
                        "codes": list(artifact.codes),
                        "as_of": artifact.as_of,
                        "evidence_sha256": artifact.evidence_sha256,
                    },
                }
            ]
            return base_result
        except Exception as exc:
            if output_path is not None:
                try:
                    path = Path(output_path)
                    if self.generated_file_service.is_managed_storage_path(path):
                        path.unlink(missing_ok=True)
                except Exception:
                    pass
            return self._report_failure(exc)

    def _resolve_chart_reference(
        self,
        target: str,
        *,
        request: FinanceReportRequest,
        profile_user_id: str,
        session_id: str,
    ) -> FinanceChartReference:
        generated = self.generated_file_service.resolve_generated_artifact(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
        )
        if not isinstance(generated, dict):
            raise MarketDataValidationError(
                field="chart_ids",
                reason=f"chart reference was not found: {target}",
                code="invalid_arguments",
                status="invalid_arguments",
                provider=self.report_provider.provider_id,
            )
        content_card = generated.get("content_card") if isinstance(generated.get("content_card"), dict) else {}
        chart = content_card.get("chart") if isinstance(content_card.get("chart"), dict) else {}
        if (
            str(generated.get("created_by_tool") or "").strip() != "render_market_chart"
            or str(generated.get("output_format") or "").strip().lower() != "png"
            or str(content_card.get("kind") or "").strip() != "market_chart"
        ):
            raise MarketDataValidationError(
                field="chart_ids",
                reason=f"referenced generated file is not a trusted market chart: {target}",
                code="invalid_arguments",
                status="invalid_arguments",
                provider=self.report_provider.provider_id,
            )
        code = str(chart.get("code") or "").strip().upper()
        if code not in request.codes:
            raise MarketDataValidationError(
                field="chart_ids",
                reason=f"chart code {code or '<empty>'} is outside the report code set",
                code="invalid_arguments",
                status="invalid_arguments",
                provider=self.report_provider.provider_id,
            )
        if (
            str(chart.get("interval") or "").strip().lower() != request.interval
            or str(chart.get("adjusted") or "").strip().lower() != request.adjusted
        ):
            raise MarketDataValidationError(
                field="chart_ids",
                reason="chart interval or adjustment does not match the report request",
                code="invalid_arguments",
                status="invalid_arguments",
                provider=self.report_provider.provider_id,
            )
        as_of = str(chart.get("as_of") or "").strip()
        source = str(chart.get("source") or "").strip()
        series_sha256 = str(chart.get("series_sha256") or "").strip().lower()
        try:
            width = int(chart.get("width") or 0)
            height = int(chart.get("height") or 0)
        except (TypeError, ValueError):
            width = 0
            height = 0
        if not as_of or not source or not re.fullmatch(r"[0-9a-f]{64}", series_sha256) or width <= 0 or height <= 0:
            raise MarketDataValidationError(
                field="chart_ids",
                reason="trusted chart metadata is incomplete",
                code="invalid_arguments",
                status="invalid_arguments",
                provider=self.report_provider.provider_id,
            )
        return FinanceChartReference(
            generated_id=str(generated.get("generated_id") or ""),
            generated_handle=str(generated.get("generated_handle") or ""),
            title=str(generated.get("output_title") or "市场图表"),
            path=Path(str(generated.get("absolute_path") or "")),
            code=code,
            as_of=as_of,
            source=source,
            series_sha256=series_sha256,
            width=width,
            height=height,
        )

    def _report_failure(self, exc: Exception) -> ToolExecutionResult:
        if isinstance(exc, MarketDataValidationError):
            payload = exc.to_public_dict()
            if payload.get("status") not in {"invalid_arguments", "unavailable"}:
                payload["status"] = "unavailable"
            payload["data"] = None
            return self._result(payload)
        return self._failure(exc)


def build_market_tool_handlers(
    service: MarketDataToolService,
    *,
    generated_file_service: Any | None = None,
    chart_provider: LocalChartProvider | None = None,
    report_provider: FinanceReportProvider | None = None,
) -> dict[str, BaseToolHandler]:
    handlers: list[BaseToolHandler] = [
        MarketResolveSecurityToolHandler(service=service),
        MarketNewsSearchToolHandler(service=service),
        MarketQuoteSnapshotToolHandler(service=service),
        MarketPriceSeriesToolHandler(service=service),
    ]
    if generated_file_service is not None:
        handlers.append(
            RenderMarketChartToolHandler(
                service=service,
                generated_file_service=generated_file_service,
                chart_provider=chart_provider,
            )
        )
        handlers.append(
            ComposeFinanceReportToolHandler(
                service=service,
                generated_file_service=generated_file_service,
                report_provider=report_provider,
            )
        )
    return {handler.tool_type: handler for handler in handlers}


def _is_tool_call(value: Any, tool_type: str) -> bool:
    return isinstance(value, dict) and str(value.get("type") or "").strip() == tool_type


def _has_only_keys(value: dict[str, Any], allowed: set[str]) -> bool:
    return all(str(key) in allowed or str(key).startswith("_tool_") for key in value)


def _string_list(
    value: Any,
    *,
    limit: int,
    max_length: int,
    uppercase: bool = False,
    lowercase: bool = False,
) -> list[str] | None:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        return None
    if len(values) > limit:
        return None
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw or "").strip()
        if uppercase:
            text = text.upper()
        if lowercase:
            text = text.lower()
        if not text or len(text) > max_length:
            return None
        if text in seen:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", text):
            return None
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _artifact_id_list(value: Any, *, limit: int) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or len(value) > limit:
        return None
    result: list[str] = []
    for raw in value:
        text = str(raw or "").strip()
        if not text or len(text) > 120 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", text):
            return None
        if text not in result:
            result.append(text)
    return result


def _bounded_text_list(value: Any, *, limit: int, max_length: int) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or len(value) > limit:
        return None
    result: list[str] = []
    for raw in value:
        text = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not text or len(text) > max_length or any(ord(char) < 32 for char in text):
            return None
        if text not in result:
            result.append(text)
    return result


def _finance_report_mime_type(output_format: str) -> str:
    return {
        "md": "text/markdown; charset=utf-8",
        "pdf": "application/pdf",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(str(output_format or "").strip().lower(), "application/octet-stream")


def _date_to_timestamp(value: Any, *, end_of_day: bool) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return None
    clock = datetime_time(23, 59, 59) if end_of_day else datetime_time(0, 0, 0)
    return int(datetime.combine(parsed, clock, tzinfo=_ZONE).timestamp())


def _bounded_int(value: Any, *, default: int, lower: int, upper: int) -> int | None:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if lower <= number <= upper else None


def _collect_evidence_identifiers(value: Any) -> tuple[list[str], list[str], list[str]]:
    event_ids: set[str] = set()
    source_urls: set[str] = set()
    codes: set[str] = set()

    def visit(item: Any, *, depth: int = 0) -> None:
        if depth > 5:
            return
        if isinstance(item, dict):
            event_id = str(item.get("event_id") or "").strip()
            source_url = str(item.get("url") or "").strip()
            code = str(item.get("code") or "").strip()
            if event_id:
                event_ids.add(event_id)
            if source_url:
                source_urls.add(source_url)
            if code:
                codes.add(code)
            for child in item.values():
                visit(child, depth=depth + 1)
        elif isinstance(item, (list, tuple)):
            for child in item[:500]:
                visit(child, depth=depth + 1)

    visit(value)
    return sorted(event_ids), sorted(source_urls), sorted(codes)


__all__ = [
    "MARKET_NEWS_SEARCH_SCHEMA",
    "COMPOSE_FINANCE_REPORT_SCHEMA",
    "MARKET_PRICE_SERIES_SCHEMA",
    "MARKET_QUOTE_SNAPSHOT_SCHEMA",
    "MARKET_RESOLVE_SECURITY_SCHEMA",
    "RENDER_MARKET_CHART_SCHEMA",
    "MarketNewsSearchToolHandler",
    "ComposeFinanceReportToolHandler",
    "MarketPriceSeriesToolHandler",
    "MarketQuoteSnapshotToolHandler",
    "MarketResolveSecurityToolHandler",
    "RenderMarketChartToolHandler",
    "build_market_tool_handlers",
]
