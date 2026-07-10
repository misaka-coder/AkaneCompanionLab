from __future__ import annotations

from datetime import date, datetime, time as datetime_time
import hashlib
import json
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
from .market_service import MarketDataToolService


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
        "a complete provider code. Never manufacture .SH/.SZ/.BJ suffixes."
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
        "interval": {"type": "string", "enum": ["1d", "1w", "1mo"]},
        "adjusted": {"type": "string", "enum": ["none", "forward", "backward"]},
        "date_from": {"type": "string", "description": "Optional YYYY-MM-DD lower bound."},
        "date_to": {"type": "string", "description": "Optional YYYY-MM-DD upper bound."},
        "limit": {"type": "integer", "minimum": 2, "maximum": 500},
    },
    "required": ["code"],
}


class _FinanceReadToolHandler(BaseToolHandler):
    input_schema: dict[str, Any] = {}

    def __init__(self, *, service: MarketDataToolService) -> None:
        self.service = service

    def tool_metadata(self) -> ToolMetadata:
        return ToolMetadata(**_FINANCE_METADATA, input_schema=self.input_schema)

    def _result(self, payload: dict[str, Any]) -> ToolExecutionResult:
        status = str(payload.get("status") or "unavailable").strip().lower()
        followup = (
            "以下是金融只读工具返回的结构化证据。区分来源事实、程序计算与分析推断；"
            "任何时效性结论都必须引用 as_of。\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
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

    def build_prompt_instruction(self) -> str:
        return (
            '- market_resolve_security：把用户给出的证券名称/别名解析成可信 provider code。格式为 '
            '{"type":"market_resolve_security","query":"贵州茅台","limit":5}。'
            "用户没有直接给出完整 provider code 时，必须先用本工具；只有 resolved=true 才能直接继续查行情。"
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
            return self._result(
                self.service.resolve_security(
                    str(call.get("query") or ""),
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    limit=int(call.get("limit") or 5),
                )
            )
        except Exception as exc:
            return self._failure(exc)


class MarketNewsSearchToolHandler(_FinanceReadToolHandler):
    tool_type = "market_news_search"
    input_schema = MARKET_NEWS_SEARCH_SCHEMA

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
            self.service.ensure_trusted_codes(
                tuple(call.get("codes") or ()),
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                request_context=context.request_context,
            )
            request = MarketNewsQuery(
                query=str(call.get("query") or ""),
                codes=tuple(call.get("codes") or ()),
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
            self.service.ensure_trusted_codes(
                tuple(call.get("codes") or ()),
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                request_context=context.request_context,
            )
            return self._result(
                self.service.quote_snapshots(MarketQuoteRequest(codes=tuple(call.get("codes") or ())))
            )
        except Exception as exc:
            return self._failure(exc)


class MarketPriceSeriesToolHandler(_FinanceReadToolHandler):
    tool_type = "market_price_series"
    input_schema = MARKET_PRICE_SERIES_SCHEMA

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
        if interval not in {"1d", "1w", "1mo"} or adjusted not in {"none", "forward", "backward"}:
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
            self.service.ensure_trusted_codes(
                (str(call.get("code") or ""),),
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                request_context=context.request_context,
            )
            request = MarketSeriesRequest(
                code=str(call.get("code") or ""),
                interval=str(call.get("interval") or "1d"),
                adjusted=str(call.get("adjusted") or "none"),
                date_from=call.get("date_from"),
                date_to=call.get("date_to"),
                limit=call.get("limit", 120),
            )
            return self._result(self.service.price_series(request))
        except Exception as exc:
            return self._failure(exc)


def build_market_tool_handlers(service: MarketDataToolService) -> dict[str, BaseToolHandler]:
    handlers: tuple[BaseToolHandler, ...] = (
        MarketResolveSecurityToolHandler(service=service),
        MarketNewsSearchToolHandler(service=service),
        MarketQuoteSnapshotToolHandler(service=service),
        MarketPriceSeriesToolHandler(service=service),
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
    "MARKET_PRICE_SERIES_SCHEMA",
    "MARKET_QUOTE_SNAPSHOT_SCHEMA",
    "MARKET_RESOLVE_SECURITY_SCHEMA",
    "MarketNewsSearchToolHandler",
    "MarketPriceSeriesToolHandler",
    "MarketQuoteSnapshotToolHandler",
    "MarketResolveSecurityToolHandler",
    "build_market_tool_handlers",
]
