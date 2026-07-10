from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from .normalizers import normalize_market_code
from .types import (
    MarketDataResponse,
    MarketDataValidationError,
    MarketEvent,
    MarketProviderHealth,
    MarketQuoteSnapshot,
    MarketSeries,
)


MARKET_PROVIDER_CAPABILITY_NAMES = (
    "news_search",
    "event_poll",
    "quote_snapshot",
    "price_series",
    "macro_series",
    "streaming",
    "security_master",
)


@dataclass(frozen=True)
class MarketProviderCapabilities:
    """Implemented capabilities of one normalized market-data adapter."""

    news_search: bool = False
    event_poll: bool = False
    quote_snapshot: bool = False
    price_series: bool = False
    macro_series: bool = False
    streaming: bool = False
    security_master: bool = False

    def enabled(self) -> tuple[str, ...]:
        return tuple(name for name in MARKET_PROVIDER_CAPABILITY_NAMES if bool(getattr(self, name)))

    def supports(self, capability: str) -> bool:
        name = str(capability or "").strip().lower()
        return name in MARKET_PROVIDER_CAPABILITY_NAMES and bool(getattr(self, name))

    def to_public_dict(self) -> dict[str, bool]:
        return {name: bool(getattr(self, name)) for name in MARKET_PROVIDER_CAPABILITY_NAMES}


@dataclass(frozen=True)
class MarketNewsQuery:
    query: str = ""
    codes: tuple[str, ...] = ()
    content_types: tuple[str, ...] = ()
    date_from: int | None = None
    date_to: int | None = None
    limit: int = 20

    def __post_init__(self) -> None:
        query = str(self.query or "").strip()
        if len(query) > 500:
            raise MarketDataValidationError(
                field="query",
                reason="query is too long",
                code="invalid_arguments",
                status="invalid_arguments",
            )
        object.__setattr__(self, "query", query)
        object.__setattr__(self, "codes", _unique_codes(self.codes, field="codes"))
        object.__setattr__(
            self,
            "content_types",
            _unique_text_values(self.content_types, field="content_types", lowercase=True, max_items=32),
        )
        object.__setattr__(self, "date_from", _optional_positive_int(self.date_from, field="date_from"))
        object.__setattr__(self, "date_to", _optional_positive_int(self.date_to, field="date_to"))
        if self.date_from is not None and self.date_to is not None and self.date_from > self.date_to:
            raise MarketDataValidationError(
                field="date_from",
                reason="date_from cannot be later than date_to",
                code="invalid_arguments",
                status="invalid_arguments",
            )
        limit = _bounded_request_int(self.limit, field="limit", lower=1, upper=100)
        object.__setattr__(self, "limit", limit)


@dataclass(frozen=True)
class MarketQuoteRequest:
    codes: tuple[str, ...]

    def __post_init__(self) -> None:
        codes = _unique_codes(self.codes, field="codes", max_items=100)
        if not codes:
            raise MarketDataValidationError(
                field="codes",
                reason="at least one code is required",
                code="invalid_arguments",
                status="invalid_arguments",
            )
        object.__setattr__(self, "codes", codes)


@dataclass(frozen=True)
class MarketSeriesRequest:
    code: str
    interval: str = "1d"
    adjusted: str = "none"
    date_from: int | None = None
    date_to: int | None = None
    limit: int = 500

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "code",
            normalize_market_code(
                self.code,
                field="code",
                status="invalid_arguments",
                error_code="invalid_arguments",
            ),
        )
        interval = str(self.interval or "1d").strip().lower() or "1d"
        if len(interval) > 40:
            raise MarketDataValidationError(
                field="interval",
                reason="interval is too long",
                code="invalid_arguments",
                status="invalid_arguments",
            )
        object.__setattr__(self, "interval", interval)
        adjusted = str(self.adjusted or "none").strip().lower() or "none"
        if adjusted not in {"none", "forward", "backward"}:
            raise MarketDataValidationError(
                field="adjusted",
                reason="adjusted must be none, forward, or backward",
                code="invalid_arguments",
                status="invalid_arguments",
            )
        object.__setattr__(self, "adjusted", adjusted)
        object.__setattr__(self, "date_from", _optional_positive_int(self.date_from, field="date_from"))
        object.__setattr__(self, "date_to", _optional_positive_int(self.date_to, field="date_to"))
        if self.date_from is not None and self.date_to is not None and self.date_from > self.date_to:
            raise MarketDataValidationError(
                field="date_from",
                reason="date_from cannot be later than date_to",
                code="invalid_arguments",
                status="invalid_arguments",
            )
        object.__setattr__(self, "limit", _bounded_request_int(self.limit, field="limit", lower=1, upper=5000))


class MarketDataProvider(ABC):
    """Personality-agnostic, QQ-agnostic read-only market data boundary."""

    provider_id = "market_data"
    source_name = "Market Data"
    capabilities = MarketProviderCapabilities()

    @property
    def id(self) -> str:
        return str(self.provider_id or "market_data").strip() or "market_data"

    @property
    def source(self) -> str:
        return str(self.source_name or "Market Data").strip() or "Market Data"

    def supports(self, capability: str) -> bool:
        return self.capabilities.supports(capability)

    @abstractmethod
    def health(self) -> MarketProviderHealth:
        raise NotImplementedError

    @abstractmethod
    def search_news(self, request: MarketNewsQuery) -> MarketDataResponse[tuple[MarketEvent, ...]]:
        raise NotImplementedError

    @abstractmethod
    def get_quote_snapshots(
        self,
        request: MarketQuoteRequest,
    ) -> MarketDataResponse[tuple[MarketQuoteSnapshot, ...]]:
        raise NotImplementedError

    @abstractmethod
    def get_price_series(self, request: MarketSeriesRequest) -> MarketDataResponse[MarketSeries | None]:
        raise NotImplementedError


def _unique_codes(values: Any, *, field: str, max_items: int = 100) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        raw_values = [values]
    else:
        try:
            raw_values = list(values)
        except TypeError as exc:
            raise MarketDataValidationError(
                field=field,
                reason="value must be a list of market codes",
                code="invalid_arguments",
                status="invalid_arguments",
            ) from exc
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        code = normalize_market_code(
            raw,
            field=field,
            status="invalid_arguments",
            error_code="invalid_arguments",
        )
        if code in seen:
            continue
        seen.add(code)
        normalized.append(code)
    if len(normalized) > max_items:
        raise MarketDataValidationError(
            field=field,
            reason=f"at most {max_items} values are allowed",
            code="invalid_arguments",
            status="invalid_arguments",
        )
    return tuple(normalized)


def _unique_text_values(
    values: Any,
    *,
    field: str,
    lowercase: bool,
    max_items: int,
) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        raw_values = [values]
    else:
        try:
            raw_values = list(values)
        except TypeError as exc:
            raise MarketDataValidationError(
                field=field,
                reason="value must be a list",
                code="invalid_arguments",
                status="invalid_arguments",
            ) from exc
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        text = str(raw or "").strip()
        if lowercase:
            text = text.lower()
        if not text or text in seen:
            continue
        if len(text) > 120:
            raise MarketDataValidationError(
                field=field,
                reason="filter value is too long",
                code="invalid_arguments",
                status="invalid_arguments",
            )
        seen.add(text)
        normalized.append(text)
    if len(normalized) > max_items:
        raise MarketDataValidationError(
            field=field,
            reason=f"at most {max_items} values are allowed",
            code="invalid_arguments",
            status="invalid_arguments",
        )
    return tuple(normalized)


def _optional_positive_int(value: Any, *, field: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise MarketDataValidationError(
            field=field,
            reason="boolean is not a timestamp",
            code="invalid_arguments",
            status="invalid_arguments",
        )
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise MarketDataValidationError(
            field=field,
            reason="value must be a positive integer timestamp",
            code="invalid_arguments",
            status="invalid_arguments",
        ) from exc
    if number <= 0:
        raise MarketDataValidationError(
            field=field,
            reason="timestamp must be positive",
            code="invalid_arguments",
            status="invalid_arguments",
        )
    return number


def _bounded_request_int(value: Any, *, field: str, lower: int, upper: int) -> int:
    if isinstance(value, bool):
        raise MarketDataValidationError(
            field=field,
            reason="boolean is not an integer",
            code="invalid_arguments",
            status="invalid_arguments",
        )
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise MarketDataValidationError(
            field=field,
            reason="value must be an integer",
            code="invalid_arguments",
            status="invalid_arguments",
        ) from exc
    if number < lower or number > upper:
        raise MarketDataValidationError(
            field=field,
            reason=f"value must be between {lower} and {upper}",
            code="invalid_arguments",
            status="invalid_arguments",
        )
    return number
