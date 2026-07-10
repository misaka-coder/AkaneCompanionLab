from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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

    def to_public_dict(self) -> dict[str, Any]:
        return {
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


@dataclass(frozen=True)
class MarketBar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    amount: float | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "amount": self.amount,
        }


@dataclass(frozen=True)
class MarketSeries:
    provider: str
    code: str
    interval: str
    adjusted: str
    timezone: str
    points: tuple[MarketBar, ...]
    as_of: int

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "code": self.code,
            "interval": self.interval,
            "adjusted": self.adjusted,
            "timezone": self.timezone,
            "points": [point.to_public_dict() for point in self.points],
            "as_of": self.as_of,
        }


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
