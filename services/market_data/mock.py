from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

from .normalizers import (
    normalize_choice_news_record,
    normalize_choice_quote_record,
    normalize_choice_series_record,
    parse_market_timestamp,
)
from .provider import (
    MarketDataProvider,
    MarketNewsQuery,
    MarketProviderCapabilities,
    MarketQuoteRequest,
    MarketSeriesRequest,
)
from .types import (
    MarketDataResponse,
    MarketDataValidationError,
    MarketEvent,
    MarketProviderHealth,
    MarketQuoteSnapshot,
    MarketSeries,
)


MOCK_CHOICE_FIXTURE_SCHEMA = "akane.market_data.choice_fixture.v1"


class MockMarketDataProvider(MarketDataProvider):
    """Offline provider backed only by explicitly synthetic Choice-shaped fixtures."""

    provider_id = "mock_choice"
    source_name = "Synthetic Choice Fixture"
    capabilities = MarketProviderCapabilities(
        news_search=True,
        quote_snapshot=True,
        price_series=True,
    )

    def __init__(self, fixture: Mapping[str, Any]) -> None:
        if not isinstance(fixture, Mapping):
            raise MarketDataValidationError(field="fixture", reason="fixture must be an object", provider=self.id)
        if fixture.get("synthetic") is not True:
            raise MarketDataValidationError(
                field="synthetic",
                reason="mock fixtures must explicitly declare synthetic=true",
                provider=self.id,
            )
        self.schema_version = str(fixture.get("schema_version") or "").strip()
        if self.schema_version != MOCK_CHOICE_FIXTURE_SCHEMA:
            raise MarketDataValidationError(
                field="schema_version",
                reason=f"mock fixture schema must be {MOCK_CHOICE_FIXTURE_SCHEMA}",
                provider=self.id,
            )
        received_at = parse_market_timestamp(
            fixture.get("received_at") or int(time.time()),
            field="received_at",
            provider=self.id,
            timezone_name="Asia/Shanghai",
        )
        raw_news = _fixture_list(fixture, "news", provider=self.id)
        raw_quotes = _fixture_list(fixture, "quotes", provider=self.id)
        raw_series = _fixture_list(fixture, "series", provider=self.id)
        self._news = tuple(
            normalize_choice_news_record(
                item,
                provider=self.id,
                event_namespace=self.id,
                received_at=int(received_at),
            )
            for item in raw_news
        )
        self._quotes = tuple(normalize_choice_quote_record(item, provider=self.id) for item in raw_quotes)
        self._series = tuple(normalize_choice_series_record(item, provider=self.id) for item in raw_series)
        self._news_search_text = {
            event.event_id: " ".join(
                (
                    event.code,
                    event.content_type,
                    event.title,
                    event.source,
                    str(raw.get("content") or ""),
                    " ".join(event.labels),
                )
            ).lower()
            for event, raw in zip(self._news, raw_news, strict=True)
        }

    @classmethod
    def from_fixture_path(cls, path: str | Path) -> MockMarketDataProvider:
        fixture_path = Path(path).expanduser().resolve()
        try:
            payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MarketDataValidationError(
                field="fixture",
                reason="unable to read a valid JSON fixture",
                provider=cls.provider_id,
            ) from exc
        return cls(payload)

    def health(self) -> MarketProviderHealth:
        return MarketProviderHealth(
            ok=True,
            status="ready",
            provider=self.id,
            source=self.source,
            checked_at=int(time.time()),
            logged_in=False,
            news_subscription_count=0,
            quote_subscription_count=0,
            last_news_at=max((item.published_at for item in self._news), default=0),
            last_quote_at=max((item.as_of for item in self._quotes), default=0),
            quota_status={"mode": "mock", "synthetic": True, "network": "disabled"},
        )

    def search_news(self, request: MarketNewsQuery) -> MarketDataResponse[tuple[MarketEvent, ...]]:
        if not isinstance(request, MarketNewsQuery):
            raise MarketDataValidationError(
                field="request",
                reason="MarketNewsQuery is required",
                code="invalid_arguments",
                status="invalid_arguments",
                provider=self.id,
            )
        code_filter = set(request.codes)
        type_filter = set(request.content_types)
        query_text = request.query.lower()
        matches = [
            event
            for event in self._news
            if (not code_filter or event.code in code_filter)
            and (not type_filter or event.content_type in type_filter)
            and (request.date_from is None or event.published_at >= request.date_from)
            and (request.date_to is None or event.published_at <= request.date_to)
            and (not query_text or query_text in self._news_search_text.get(event.event_id, ""))
        ]
        matches.sort(key=lambda item: (item.published_at, item.event_id), reverse=True)
        data = tuple(matches[: request.limit])
        if not data:
            return self._response(status="empty", data=(), as_of=None, reason="no synthetic news matched")
        return self._response(status="ok", data=data, as_of=max(item.published_at for item in data))

    def get_quote_snapshots(
        self,
        request: MarketQuoteRequest,
    ) -> MarketDataResponse[tuple[MarketQuoteSnapshot, ...]]:
        if not isinstance(request, MarketQuoteRequest):
            raise MarketDataValidationError(
                field="request",
                reason="MarketQuoteRequest is required",
                code="invalid_arguments",
                status="invalid_arguments",
                provider=self.id,
            )
        by_code = {item.code: item for item in self._quotes}
        data = tuple(by_code[code] for code in request.codes if code in by_code)
        if not data:
            return self._response(status="empty", data=(), as_of=None, reason="no synthetic quotes matched")
        missing = [code for code in request.codes if code not in by_code]
        reason = f"missing synthetic quotes: {', '.join(missing)}" if missing else ""
        return self._response(status="ok", data=data, as_of=max(item.as_of for item in data), reason=reason)

    def get_price_series(self, request: MarketSeriesRequest) -> MarketDataResponse[MarketSeries | None]:
        if not isinstance(request, MarketSeriesRequest):
            raise MarketDataValidationError(
                field="request",
                reason="MarketSeriesRequest is required",
                code="invalid_arguments",
                status="invalid_arguments",
                provider=self.id,
            )
        candidate = next(
            (
                item
                for item in self._series
                if item.code == request.code and item.interval == request.interval and item.adjusted == request.adjusted
            ),
            None,
        )
        if candidate is None:
            return self._response(status="empty", data=None, as_of=None, reason="no synthetic series matched")
        points = tuple(
            point
            for point in candidate.points
            if (request.date_from is None or point.timestamp >= request.date_from)
            and (request.date_to is None or point.timestamp <= request.date_to)
        )[-request.limit :]
        if not points:
            return self._response(status="empty", data=None, as_of=None, reason="no synthetic bars matched")
        filtered = MarketSeries(
            provider=candidate.provider,
            code=candidate.code,
            interval=candidate.interval,
            adjusted=candidate.adjusted,
            timezone=candidate.timezone,
            points=points,
            as_of=points[-1].timestamp,
        )
        return self._response(status="ok", data=filtered, as_of=filtered.as_of, timezone=filtered.timezone)

    def _response(
        self,
        *,
        status: str,
        data: Any,
        as_of: int | None,
        reason: str = "",
        timezone: str = "Asia/Shanghai",
    ) -> MarketDataResponse[Any]:
        return MarketDataResponse(
            ok=status in {"ok", "empty"},
            status=status,
            provider=self.id,
            source=self.source,
            as_of=as_of,
            reason=reason,
            data=data,
            timezone=timezone,
        )


def _fixture_list(fixture: Mapping[str, Any], key: str, *, provider: str) -> list[Mapping[str, Any]]:
    value = fixture.get(key, [])
    if not isinstance(value, list):
        raise MarketDataValidationError(field=key, reason="fixture field must be a list", provider=provider)
    if any(not isinstance(item, Mapping) for item in value):
        raise MarketDataValidationError(field=key, reason="fixture entries must be objects", provider=provider)
    return value
