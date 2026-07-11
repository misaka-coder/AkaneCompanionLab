from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from types import MappingProxyType
from typing import Any, Generic, Mapping, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


T = TypeVar("T")

MARKET_RESULT_STATUSES = frozenset(
    {
        "ok",
        "empty",
        "unavailable",
        "permission_denied",
        "rate_limited",
        "invalid_arguments",
    }
)
MARKET_HEALTH_STATUSES = frozenset({"ready", "degraded", "disconnected", "permission_denied"})
MARKET_DATA_DELAY_KINDS = frozenset({"real_time", "delayed", "end_of_day", "unknown"})
MARKET_DATA_QUALITY_LEVELS = frozenset({"official", "vendor", "aggregated", "public_web", "derived", "unknown"})
MARKET_BAR_TIME_SEMANTICS = frozenset({"instant", "trading_date"})


class MarketDataValidationError(ValueError):
    """Structured failure raised when provider input or upstream data is invalid."""

    def __init__(
        self,
        *,
        field: str,
        reason: str,
        code: str = "invalid_field",
        status: str = "invalid_data",
        provider: str = "",
    ) -> None:
        self.field = str(field or "").strip()
        self.reason = str(reason or "invalid value").strip() or "invalid value"
        self.code = str(code or "invalid_field").strip() or "invalid_field"
        self.status = str(status or "invalid_data").strip() or "invalid_data"
        self.provider = str(provider or "").strip()
        super().__init__(f"{self.field or 'value'}: {self.reason}")

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "status": self.status,
            "code": self.code,
            "field": self.field,
            "provider": self.provider,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class MarketEvent:
    provider: str
    event_id: str
    published_at: int
    produced_at: int | None
    received_at: int
    code: str
    content_type: str
    title: str
    source: str
    url: str
    sentiment: str
    labels: tuple[str, ...]
    sector_code: str
    raw_hash: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "event_id": self.event_id,
            "published_at": self.published_at,
            "produced_at": self.produced_at,
            "received_at": self.received_at,
            "code": self.code,
            "content_type": self.content_type,
            "title": self.title,
            "source": self.source,
            "url": self.url,
            "sentiment": self.sentiment,
            "labels": list(self.labels),
            "sector_code": self.sector_code,
            "raw_hash": self.raw_hash,
        }


@dataclass(frozen=True)
class MarketEventPollResult:
    ok: bool
    status: str
    provider: str
    source: str
    events: tuple[MarketEvent, ...]
    raw_event_count: int = 0
    ignored_count: int = 0
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "events", tuple(self.events or ()))

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "status": str(self.status or ""),
            "provider": str(self.provider or ""),
            "source": str(self.source or ""),
            "events": [event.to_public_dict() for event in self.events],
            "raw_event_count": max(0, int(self.raw_event_count)),
            "ignored_count": max(0, int(self.ignored_count)),
            "reason": str(self.reason or ""),
        }


@dataclass(frozen=True)
class MarketQuoteSnapshot:
    provider: str
    code: str
    as_of: int
    timezone: str
    previous_close: float | None
    open: float | None
    high: float | None
    low: float | None
    last: float | None
    volume: float | None
    amount: float | None
    change: float | None
    change_pct: float | None
    status: str
    provenance: MarketDataProvenance | None = None

    def to_public_dict(self) -> dict[str, Any]:
        payload = {
            "provider": self.provider,
            "code": self.code,
            "as_of": self.as_of,
            "timezone": self.timezone,
            "previous_close": self.previous_close,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "last": self.last,
            "volume": self.volume,
            "amount": self.amount,
            "change": self.change,
            "change_pct": self.change_pct,
            "status": self.status,
        }
        if self.provenance is not None:
            payload["provenance"] = self.provenance.to_public_dict()
        return payload


@dataclass(frozen=True)
class MarketDataProvenance:
    source: str
    vendor_symbol: str
    fetched_at: int
    exchange_timezone: str
    currency: str
    session: str
    delay_kind: str
    delay_seconds: int | None
    data_quality: str
    adapter_version: str

    def __post_init__(self) -> None:
        source = str(self.source or "").strip()
        vendor_symbol = str(self.vendor_symbol or "").strip()
        exchange_timezone = str(self.exchange_timezone or "").strip()
        currency = str(self.currency or "").strip().upper()
        session = str(self.session or "").strip().lower()
        delay_kind = str(self.delay_kind or "").strip().lower()
        data_quality = str(self.data_quality or "").strip().lower()
        adapter_version = str(self.adapter_version or "").strip()
        if not source:
            raise MarketDataValidationError(field="source", reason="provenance source is required")
        if not vendor_symbol:
            raise MarketDataValidationError(field="vendor_symbol", reason="vendor symbol is required")
        try:
            ZoneInfo(exchange_timezone)
        except ZoneInfoNotFoundError as exc:
            raise MarketDataValidationError(field="exchange_timezone", reason="unknown timezone") from exc
        if len(currency) != 3 or not currency.isalpha():
            raise MarketDataValidationError(field="currency", reason="currency must be a three-letter code")
        if not session or len(session) > 40:
            raise MarketDataValidationError(field="session", reason="session must be between 1 and 40 characters")
        if delay_kind not in MARKET_DATA_DELAY_KINDS:
            raise MarketDataValidationError(field="delay_kind", reason="unsupported market data delay kind")
        if data_quality not in MARKET_DATA_QUALITY_LEVELS:
            raise MarketDataValidationError(field="data_quality", reason="unsupported market data quality level")
        if not adapter_version or len(adapter_version) > 80:
            raise MarketDataValidationError(
                field="adapter_version",
                reason="adapter version must be between 1 and 80 characters",
            )
        delay_seconds = self.delay_seconds
        if delay_seconds is not None:
            delay_seconds = _require_nonnegative_int(delay_seconds, field="delay_seconds")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "vendor_symbol", vendor_symbol)
        object.__setattr__(self, "fetched_at", _require_positive_int(self.fetched_at, field="fetched_at"))
        object.__setattr__(self, "exchange_timezone", exchange_timezone)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "session", session)
        object.__setattr__(self, "delay_kind", delay_kind)
        object.__setattr__(self, "delay_seconds", delay_seconds)
        object.__setattr__(self, "data_quality", data_quality)
        object.__setattr__(self, "adapter_version", adapter_version)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "vendor_symbol": self.vendor_symbol,
            "fetched_at": self.fetched_at,
            "exchange_timezone": self.exchange_timezone,
            "currency": self.currency,
            "session": self.session,
            "delay_kind": self.delay_kind,
            "delay_seconds": self.delay_seconds,
            "data_quality": self.data_quality,
            "adapter_version": self.adapter_version,
        }


@dataclass(frozen=True)
class MarketBar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    amount: float | None = None
    trading_date: str = ""
    time_semantics: str = "instant"

    def __post_init__(self) -> None:
        trading_date = str(self.trading_date or "").strip()
        time_semantics = str(self.time_semantics or "instant").strip().lower() or "instant"
        if time_semantics not in MARKET_BAR_TIME_SEMANTICS:
            raise MarketDataValidationError(field="time_semantics", reason="unsupported market bar time semantics")
        if trading_date:
            try:
                date.fromisoformat(trading_date)
            except ValueError as exc:
                raise MarketDataValidationError(field="trading_date", reason="expected YYYY-MM-DD") from exc
        if time_semantics == "trading_date" and not trading_date:
            raise MarketDataValidationError(
                field="trading_date",
                reason="trading_date is required when time semantics is trading_date",
            )
        object.__setattr__(self, "trading_date", trading_date)
        object.__setattr__(self, "time_semantics", time_semantics)

    def to_public_dict(self) -> dict[str, Any]:
        payload = {
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "amount": self.amount,
        }
        if self.trading_date or self.time_semantics != "instant":
            payload["trading_date"] = self.trading_date
            payload["time_semantics"] = self.time_semantics
        return payload


@dataclass(frozen=True)
class MarketSeries:
    provider: str
    code: str
    interval: str
    adjusted: str
    timezone: str
    points: tuple[MarketBar, ...]
    as_of: int
    provenance: MarketDataProvenance | None = None

    def to_public_dict(self) -> dict[str, Any]:
        payload = {
            "provider": self.provider,
            "code": self.code,
            "interval": self.interval,
            "adjusted": self.adjusted,
            "timezone": self.timezone,
            "points": [point.to_public_dict() for point in self.points],
            "as_of": self.as_of,
        }
        if self.provenance is not None:
            payload["provenance"] = self.provenance.to_public_dict()
        return payload


@dataclass(frozen=True)
class MarketProviderHealth:
    ok: bool
    status: str
    provider: str
    source: str
    checked_at: int
    logged_in: bool
    news_subscription_count: int = 0
    quote_subscription_count: int = 0
    last_news_at: int = 0
    last_quote_at: int = 0
    last_error_code: int = 0
    last_error_reason: str = ""
    quota_status: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        status = str(self.status or "").strip().lower()
        if status not in MARKET_HEALTH_STATUSES:
            raise MarketDataValidationError(
                field="status",
                reason=f"unsupported provider health status: {status or '<empty>'}",
            )
        expected_ok = status in {"ready", "degraded"}
        if bool(self.ok) != expected_ok:
            raise MarketDataValidationError(
                field="ok",
                reason=f"ok must be {str(expected_ok).lower()} when health status is {status}",
            )
        provider = str(self.provider or "").strip()
        source = str(self.source or "").strip()
        if not provider:
            raise MarketDataValidationError(field="provider", reason="provider is required")
        if not source:
            raise MarketDataValidationError(field="source", reason="source is required")
        checked_at = _require_positive_int(self.checked_at, field="checked_at")
        counts = {
            "news_subscription_count": self.news_subscription_count,
            "quote_subscription_count": self.quote_subscription_count,
            "last_news_at": self.last_news_at,
            "last_quote_at": self.last_quote_at,
        }
        for field_name, value in counts.items():
            object.__setattr__(self, field_name, _require_nonnegative_int(value, field=field_name))
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "checked_at", checked_at)
        object.__setattr__(self, "quota_status", MappingProxyType(dict(self.quota_status or {})))

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "status": self.status,
            "provider": self.provider,
            "source": self.source,
            "checked_at": self.checked_at,
            "logged_in": bool(self.logged_in),
            "news_subscription_count": int(self.news_subscription_count),
            "quote_subscription_count": int(self.quote_subscription_count),
            "last_news_at": int(self.last_news_at),
            "last_quote_at": int(self.last_quote_at),
            "last_error_code": int(self.last_error_code),
            "last_error_reason": str(self.last_error_reason or ""),
            "quota_status": dict(self.quota_status),
        }


@dataclass(frozen=True)
class MarketDataResponse(Generic[T]):
    ok: bool
    status: str
    provider: str
    source: str
    as_of: int | None
    reason: str
    data: T
    timezone: str = "Asia/Shanghai"

    def __post_init__(self) -> None:
        status = str(self.status or "").strip().lower()
        if status not in MARKET_RESULT_STATUSES:
            raise MarketDataValidationError(
                field="status",
                reason=f"unsupported market result status: {status or '<empty>'}",
            )
        expected_ok = status in {"ok", "empty"}
        if bool(self.ok) != expected_ok:
            raise MarketDataValidationError(
                field="ok",
                reason=f"ok must be {str(expected_ok).lower()} when result status is {status}",
            )
        provider = str(self.provider or "").strip()
        source = str(self.source or "").strip()
        if not provider:
            raise MarketDataValidationError(field="provider", reason="provider is required")
        if not source:
            raise MarketDataValidationError(field="source", reason="source is required")
        if self.as_of is not None:
            object.__setattr__(self, "as_of", _require_positive_int(self.as_of, field="as_of"))
        timezone_name = str(self.timezone or "Asia/Shanghai").strip() or "Asia/Shanghai"
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise MarketDataValidationError(field="timezone", reason="unknown timezone") from exc
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "timezone", timezone_name)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "status": self.status,
            "provider": self.provider,
            "as_of": timestamp_to_iso(self.as_of, self.timezone),
            "source": self.source,
            "reason": str(self.reason or ""),
            "data": _serialize_public(self.data),
        }


def timestamp_to_iso(timestamp: int | None, timezone_name: str) -> str:
    if timestamp is None:
        return ""
    try:
        zone = ZoneInfo(str(timezone_name or "Asia/Shanghai"))
    except ZoneInfoNotFoundError as exc:
        raise MarketDataValidationError(field="timezone", reason="unknown timezone") from exc
    return datetime.fromtimestamp(int(timestamp), tz=zone).isoformat()


def _serialize_public(value: Any) -> Any:
    if hasattr(value, "to_public_dict") and callable(value.to_public_dict):
        return value.to_public_dict()
    if isinstance(value, Mapping):
        return {str(key): _serialize_public(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_serialize_public(item) for item in value]
    return value


def _require_positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise MarketDataValidationError(field=field, reason="boolean is not a positive integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise MarketDataValidationError(field=field, reason="value must be a positive integer") from exc
    if number <= 0:
        raise MarketDataValidationError(field=field, reason="value must be positive")
    return number


def _require_nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise MarketDataValidationError(field=field, reason="boolean is not a non-negative integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise MarketDataValidationError(field=field, reason="value must be a non-negative integer") from exc
    if number < 0:
        raise MarketDataValidationError(field=field, reason="value cannot be negative")
    return number
