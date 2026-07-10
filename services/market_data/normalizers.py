from __future__ import annotations

import hashlib
import json
import math
import re
import time
from datetime import datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .types import (
    MarketBar,
    MarketDataValidationError,
    MarketEvent,
    MarketQuoteSnapshot,
    MarketSeries,
)


_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{1,39}$")
_EMPTY_NUMERIC_MARKERS = frozenset({"", "--", "null", "none", "n/a"})


def normalize_market_code(
    value: Any,
    *,
    field: str = "code",
    provider: str = "",
    status: str = "invalid_data",
    error_code: str = "invalid_field",
) -> str:
    code = str(value or "").strip().upper()
    if not code:
        raise MarketDataValidationError(
            field=field,
            reason="code is required",
            provider=provider,
            status=status,
            code=error_code,
        )
    if not _CODE_RE.fullmatch(code):
        raise MarketDataValidationError(
            field=field,
            reason="code contains unsupported characters or has an invalid length",
            provider=provider,
            status=status,
            code=error_code,
        )
    return code


def normalize_choice_news_record(
    record: Mapping[str, Any],
    *,
    provider: str = "choice_emquant",
    event_namespace: str = "choice",
    received_at: int | None = None,
    timezone_name: str = "Asia/Shanghai",
) -> MarketEvent:
    row = _casefold_mapping(record, provider=provider)
    code = normalize_market_code(_get(row, "code"), provider=provider)
    published_at = parse_market_timestamp(
        _get(row, "datetime", "published_at"),
        field="datetime",
        provider=provider,
        timezone_name=timezone_name,
    )
    produced_at = parse_market_timestamp(
        _get(row, "eitime", "produced_at"),
        field="eitime",
        provider=provider,
        timezone_name=timezone_name,
        required=False,
    )
    title = _required_text(_get(row, "title"), field="title", provider=provider, max_length=2000)
    content_type = _required_text(
        _get(row, "type", "content_type"),
        field="type",
        provider=provider,
        max_length=120,
    ).lower()
    source = _optional_text(_get(row, "medianname", "source"), field="medianname", provider=provider, max_length=500)
    url = _optional_text(_get(row, "url"), field="url", provider=provider, max_length=4000)
    content = _optional_text(_get(row, "content"), field="content", provider=provider, max_length=2_000_000)
    labels = _normalize_labels(_get(row, "label", "labels"))
    sentiment = _normalize_sentiment(_get(row, "sentiment"), labels)
    sector_code = _optional_text(
        _get(row, "sectorcode", "sector_code"), field="sectorcode", provider=provider, max_length=80
    ).upper()
    received = int(time.time()) if received_at is None else _positive_timestamp(received_at, "received_at", provider)

    raw_hash_payload = {
        "code": code,
        "content_type": content_type,
        "title": title,
        "content": content,
        "published_at": published_at,
        "source": source,
        "url": url,
    }
    raw_hash = hashlib.sha256(
        json.dumps(raw_hash_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    info_code = _optional_text(_get(row, "infocode", "info_code"), field="infoCode", provider=provider, max_length=240)
    namespace = str(event_namespace or "choice").strip().lower() or "choice"
    event_id = f"{namespace}:{info_code}" if info_code else f"{namespace}:{raw_hash}"

    return MarketEvent(
        provider=str(provider or "choice_emquant").strip() or "choice_emquant",
        event_id=event_id,
        published_at=published_at,
        produced_at=produced_at,
        received_at=received,
        code=code,
        content_type=content_type,
        title=title,
        source=source,
        url=url,
        sentiment=sentiment,
        labels=labels,
        sector_code=sector_code,
        raw_hash=raw_hash,
    )


def normalize_choice_quote_record(
    record: Mapping[str, Any],
    *,
    provider: str = "choice_emquant",
    timezone_name: str = "Asia/Shanghai",
) -> MarketQuoteSnapshot:
    row = _casefold_mapping(record, provider=provider)
    code = normalize_market_code(_get(row, "code"), provider=provider)
    as_of = parse_market_timestamp(
        _get(row, "time", "datetime", "as_of"),
        field="time",
        provider=provider,
        timezone_name=timezone_name,
    )
    previous_close = _optional_float(_get(row, "preclose", "previous_close", "prev_close"), "preclose", provider)
    open_value = _optional_float(_get(row, "open"), "open", provider)
    high = _optional_float(_get(row, "high"), "high", provider)
    low = _optional_float(_get(row, "low"), "low", provider)
    last = _optional_float(_get(row, "now", "last", "close"), "now", provider)
    volume = _optional_float(_get(row, "volume"), "volume", provider)
    amount = _optional_float(_get(row, "amount"), "amount", provider)
    if all(value is None for value in (previous_close, open_value, high, low, last, volume, amount)):
        raise MarketDataValidationError(
            field="quote",
            reason="at least one quote value is required",
            provider=provider,
        )
    if volume is not None and volume < 0:
        raise MarketDataValidationError(field="volume", reason="volume cannot be negative", provider=provider)
    if amount is not None and amount < 0:
        raise MarketDataValidationError(field="amount", reason="amount cannot be negative", provider=provider)
    if high is not None and low is not None and high < low:
        raise MarketDataValidationError(field="high", reason="high cannot be lower than low", provider=provider)

    change = None
    change_pct = None
    if previous_close is not None and last is not None:
        change = last - previous_close
        if previous_close != 0:
            change_pct = (change / previous_close) * 100.0

    status = _optional_text(_get(row, "status"), field="status", provider=provider, max_length=80).lower() or "unknown"
    return MarketQuoteSnapshot(
        provider=str(provider or "choice_emquant").strip() or "choice_emquant",
        code=code,
        as_of=as_of,
        timezone=_validate_timezone(timezone_name, provider=provider),
        previous_close=previous_close,
        open=open_value,
        high=high,
        low=low,
        last=last,
        volume=volume,
        amount=amount,
        change=change,
        change_pct=change_pct,
        status=status,
    )


def normalize_choice_series_record(
    record: Mapping[str, Any],
    *,
    provider: str = "choice_emquant",
    default_timezone: str = "Asia/Shanghai",
) -> MarketSeries:
    row = _casefold_mapping(record, provider=provider)
    code = normalize_market_code(_get(row, "code"), provider=provider)
    interval = _required_text(_get(row, "interval"), field="interval", provider=provider, max_length=40).lower()
    adjusted = (
        _optional_text(_get(row, "adjusted"), field="adjusted", provider=provider, max_length=40).lower() or "none"
    )
    timezone_name = _validate_timezone(
        _optional_text(_get(row, "timezone"), field="timezone", provider=provider, max_length=80) or default_timezone,
        provider=provider,
    )
    raw_points = _get(row, "points", "bars")
    if not isinstance(raw_points, (list, tuple)) or not raw_points:
        raise MarketDataValidationError(field="points", reason="at least one market bar is required", provider=provider)

    points = tuple(
        _normalize_market_bar(item, index=index, provider=provider, timezone_name=timezone_name)
        for index, item in enumerate(raw_points)
    )
    ordered = tuple(sorted(points, key=lambda point: point.timestamp))
    if len({point.timestamp for point in ordered}) != len(ordered):
        raise MarketDataValidationError(field="points", reason="bar timestamps must be unique", provider=provider)
    explicit_as_of = parse_market_timestamp(
        _get(row, "as_of"),
        field="as_of",
        provider=provider,
        timezone_name=timezone_name,
        required=False,
    )
    as_of = explicit_as_of or ordered[-1].timestamp
    if as_of < ordered[-1].timestamp:
        raise MarketDataValidationError(
            field="as_of",
            reason="series as_of cannot be earlier than the latest point",
            provider=provider,
        )
    return MarketSeries(
        provider=str(provider or "choice_emquant").strip() or "choice_emquant",
        code=code,
        interval=interval,
        adjusted=adjusted,
        timezone=timezone_name,
        points=ordered,
        as_of=as_of,
    )


def parse_market_timestamp(
    value: Any,
    *,
    field: str,
    provider: str = "",
    timezone_name: str = "Asia/Shanghai",
    required: bool = True,
) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise MarketDataValidationError(field=field, reason="timestamp is required", provider=provider)
        return None
    if isinstance(value, bool):
        raise MarketDataValidationError(field=field, reason="boolean is not a timestamp", provider=provider)
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            raise MarketDataValidationError(field=field, reason="timestamp must be positive", provider=provider)
        if number >= 1_000_000_000_000:
            number /= 1000.0
        return int(number)

    text = str(value).strip()
    if text.isdigit() and len(text) in {10, 13}:
        return parse_market_timestamp(int(text), field=field, provider=provider, timezone_name=timezone_name)
    zone_name = _validate_timezone(timezone_name, provider=provider)
    zone = ZoneInfo(zone_name)
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    if parsed is None:
        for pattern in (
            "%Y%m%d%H%M%S",
            "%Y%m%d",
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        raise MarketDataValidationError(field=field, reason="unsupported timestamp format", provider=provider)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return int(parsed.timestamp())


def _normalize_market_bar(
    value: Any,
    *,
    index: int,
    provider: str,
    timezone_name: str,
) -> MarketBar:
    if not isinstance(value, Mapping):
        raise MarketDataValidationError(
            field=f"points[{index}]",
            reason="market bar must be an object",
            provider=provider,
        )
    row = _casefold_mapping(value, provider=provider)
    timestamp = parse_market_timestamp(
        _get(row, "datetime", "time", "timestamp"),
        field=f"points[{index}].datetime",
        provider=provider,
        timezone_name=timezone_name,
    )
    open_value = _required_float(_get(row, "open"), f"points[{index}].open", provider)
    high = _required_float(_get(row, "high"), f"points[{index}].high", provider)
    low = _required_float(_get(row, "low"), f"points[{index}].low", provider)
    close = _required_float(_get(row, "close", "now", "last"), f"points[{index}].close", provider)
    volume = _optional_float(_get(row, "volume"), f"points[{index}].volume", provider)
    amount = _optional_float(_get(row, "amount"), f"points[{index}].amount", provider)
    if high < max(open_value, low, close):
        raise MarketDataValidationError(
            field=f"points[{index}].high",
            reason="high must be at least open, low, and close",
            provider=provider,
        )
    if low > min(open_value, high, close):
        raise MarketDataValidationError(
            field=f"points[{index}].low",
            reason="low must be at most open, high, and close",
            provider=provider,
        )
    if volume is not None and volume < 0:
        raise MarketDataValidationError(
            field=f"points[{index}].volume", reason="volume cannot be negative", provider=provider
        )
    if amount is not None and amount < 0:
        raise MarketDataValidationError(
            field=f"points[{index}].amount", reason="amount cannot be negative", provider=provider
        )
    return MarketBar(
        timestamp=int(timestamp),
        open=open_value,
        high=high,
        low=low,
        close=close,
        volume=volume,
        amount=amount,
    )


def _casefold_mapping(value: Mapping[str, Any], *, provider: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MarketDataValidationError(field="record", reason="record must be an object", provider=provider)
    return {str(key or "").strip().lower(): item for key, item in value.items()}


def _get(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        key = str(name or "").strip().lower()
        if key in row:
            return row[key]
    return None


def _required_text(value: Any, *, field: str, provider: str, max_length: int) -> str:
    text = _optional_text(value, field=field, provider=provider, max_length=max_length)
    if not text:
        raise MarketDataValidationError(field=field, reason="value is required", provider=provider)
    return text


def _optional_text(value: Any, *, field: str, provider: str, max_length: int) -> str:
    text = str(value or "").strip()
    if len(text) > max_length:
        raise MarketDataValidationError(
            field=field,
            reason=f"text exceeds maximum length of {max_length}",
            provider=provider,
        )
    return text


def _required_float(value: Any, field: str, provider: str) -> float:
    parsed = _optional_float(value, field, provider)
    if parsed is None:
        raise MarketDataValidationError(field=field, reason="numeric value is required", provider=provider)
    return parsed


def _optional_float(value: Any, field: str, provider: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in _EMPTY_NUMERIC_MARKERS:
        return None
    if isinstance(value, bool):
        raise MarketDataValidationError(field=field, reason="boolean is not numeric", provider=provider)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise MarketDataValidationError(field=field, reason="invalid numeric value", provider=provider) from exc
    if not math.isfinite(parsed):
        raise MarketDataValidationError(field=field, reason="numeric value must be finite", provider=provider)
    return parsed


def _positive_timestamp(value: Any, field: str, provider: str) -> int:
    timestamp = parse_market_timestamp(value, field=field, provider=provider, timezone_name="Asia/Shanghai")
    return int(timestamp)


def _normalize_labels(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set, frozenset)):
        raw_items = list(value)
    else:
        raw_items = re.split(r"[,，;；|]", str(value or ""))
    labels: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        label = str(item or "").strip().lower()
        if not label or label in seen:
            continue
        seen.add(label)
        labels.append(label[:120])
    return tuple(labels[:32])


def _normalize_sentiment(value: Any, labels: tuple[str, ...]) -> str:
    explicit = str(value or "").strip().lower()
    aliases = {
        "positive": "positive",
        "bullish": "positive",
        "利好": "positive",
        "negative": "negative",
        "bearish": "negative",
        "利空": "negative",
        "neutral": "neutral",
        "中性": "neutral",
    }
    if explicit:
        return aliases.get(explicit, explicit[:40])
    for label in labels:
        if label in aliases:
            return aliases[label]
    return "unknown"


def _validate_timezone(value: Any, *, provider: str) -> str:
    timezone_name = str(value or "Asia/Shanghai").strip() or "Asia/Shanghai"
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise MarketDataValidationError(field="timezone", reason="unknown timezone", provider=provider) from exc
    return timezone_name
