from __future__ import annotations

import time
from typing import Any

from .provider import MarketDataProvider, MarketNewsQuery, MarketQuoteRequest, MarketSeriesRequest
from .types import (
    MarketDataResponse,
    MarketDataValidationError,
    MarketEvent,
    MarketProviderHealth,
    MarketQuoteSnapshot,
    MarketSeries,
)


class DisabledMarketDataProvider(MarketDataProvider):
    """Explicit no-network provider used when no licensed feed is selected."""

    provider_id = "disabled"
    source_name = "Disabled Market Data Provider"

    def __init__(self, *, reason: str = "market data provider is disabled", clock=time.time) -> None:
        self.reason = str(reason or "market data provider is disabled").strip() or "market data provider is disabled"
        self._clock = clock

    def health(self) -> MarketProviderHealth:
        return MarketProviderHealth(
            ok=False,
            status="disconnected",
            provider=self.id,
            source=self.source,
            checked_at=max(1, int(self._clock())),
            logged_in=False,
            last_error_reason=self.reason,
            quota_status={"mode": "disabled", "network": "disabled"},
        )

    def search_news(self, request: MarketNewsQuery) -> MarketDataResponse[tuple[MarketEvent, ...]]:
        self._require_request(request, MarketNewsQuery)
        return self._response(data=())

    def get_quote_snapshots(
        self,
        request: MarketQuoteRequest,
    ) -> MarketDataResponse[tuple[MarketQuoteSnapshot, ...]]:
        self._require_request(request, MarketQuoteRequest)
        return self._response(data=())

    def get_price_series(self, request: MarketSeriesRequest) -> MarketDataResponse[MarketSeries | None]:
        self._require_request(request, MarketSeriesRequest)
        return self._response(data=None)

    def _response(self, *, data: Any) -> MarketDataResponse[Any]:
        return MarketDataResponse(
            ok=False,
            status="unavailable",
            provider=self.id,
            source=self.source,
            as_of=None,
            reason=self.reason,
            data=data,
        )

    def _require_request(self, request: Any, expected_type: type[Any]) -> None:
        if isinstance(request, expected_type):
            return
        raise MarketDataValidationError(
            field="request",
            reason=f"{expected_type.__name__} is required",
            code="invalid_arguments",
            status="invalid_arguments",
            provider=self.id,
        )


__all__ = ["DisabledMarketDataProvider"]
