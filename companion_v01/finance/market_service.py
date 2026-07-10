from __future__ import annotations

import math
from statistics import stdev
from typing import Any

from services.market_data import (
    MarketDataProvider,
    MarketDataValidationError,
    MarketEvent,
    MarketEventStore,
    MarketNewsQuery,
    MarketQuoteRequest,
    MarketQuoteSnapshot,
    MarketSeries,
    MarketSeriesRequest,
)
from services.market_data.types import timestamp_to_iso


class MarketDataToolService:
    """Personality-free orchestration for the model-facing finance read tools."""

    def __init__(self, *, provider: MarketDataProvider, event_store: MarketEventStore) -> None:
        self.provider = provider
        self.event_store = event_store

    def search_news(self, request: MarketNewsQuery) -> dict[str, Any]:
        local_records = self.event_store.list_events(
            query=request.query,
            codes=request.codes,
            content_types=request.content_types,
            date_from=request.date_from,
            date_to=request.date_to,
            limit=request.limit,
        )
        by_id: dict[str, MarketEvent] = {record.event.event_id: record.event for record in local_records}
        provider_status = "not_queried"
        provider_reason = ""
        if request.codes and request.content_types:
            upstream = self.provider.search_news(request)
            provider_status = upstream.status
            provider_reason = upstream.reason
            if upstream.ok:
                for event in upstream.data:
                    self.event_store.upsert_event(event)
                    by_id[event.event_id] = event
        else:
            provider_reason = "provider query skipped because explicit codes and content_types were not both supplied"

        events = sorted(by_id.values(), key=lambda item: (item.published_at, item.event_id), reverse=True)[: request.limit]
        if events:
            reason_parts = []
            if provider_status not in {"ok", "empty", "not_queried"}:
                reason_parts.append(f"upstream {provider_status}: {provider_reason}")
            elif provider_status == "not_queried":
                reason_parts.append(provider_reason)
            return {
                "ok": True,
                "status": "ok",
                "provider": self.provider.id,
                "source": f"MarketEventStore + {self.provider.source}",
                "as_of": timestamp_to_iso(max(event.published_at for event in events), "Asia/Shanghai"),
                "reason": "; ".join(part for part in reason_parts if part),
                "provider_status": provider_status,
                "data": [_event_public(event) for event in events],
            }
        status = provider_status if provider_status in {"permission_denied", "rate_limited", "unavailable"} else "empty"
        return {
            "ok": status == "empty",
            "status": status,
            "provider": self.provider.id,
            "source": f"MarketEventStore + {self.provider.source}",
            "as_of": None,
            "reason": provider_reason or "no matching market news was found",
            "provider_status": provider_status,
            "data": [],
        }

    def quote_snapshots(self, request: MarketQuoteRequest) -> dict[str, Any]:
        response = self.provider.get_quote_snapshots(request)
        return {
            **response.to_public_dict(),
            "data": [
                {
                    **quote.to_public_dict(),
                    "as_of_iso": timestamp_to_iso(quote.as_of, quote.timezone),
                    "program_metrics": compute_quote_metrics(quote),
                }
                for quote in response.data
            ],
        }

    def price_series(self, request: MarketSeriesRequest) -> dict[str, Any]:
        response = self.provider.get_price_series(request)
        public = response.to_public_dict()
        series = response.data
        if series is None:
            public["data"] = None
            return public
        visible_points = series.points[-120:]
        public["data"] = {
            "provider": series.provider,
            "code": series.code,
            "interval": series.interval,
            "adjusted": series.adjusted,
            "timezone": series.timezone,
            "as_of": timestamp_to_iso(series.as_of, series.timezone),
            "point_count": len(series.points),
            "points_truncated": len(series.points) > len(visible_points),
            "points": [
                {
                    **point.to_public_dict(),
                    "datetime": timestamp_to_iso(point.timestamp, series.timezone),
                }
                for point in visible_points
            ],
            "program_metrics": compute_series_metrics(series),
        }
        return public


def compute_quote_metrics(quote: MarketQuoteSnapshot) -> dict[str, float | None]:
    previous_close = quote.previous_close
    gap_pct = None
    intraday_range_pct = None
    position_in_range_pct = None
    if previous_close not in {None, 0} and quote.open is not None:
        gap_pct = ((quote.open - previous_close) / previous_close) * 100.0
    if previous_close not in {None, 0} and quote.high is not None and quote.low is not None:
        intraday_range_pct = ((quote.high - quote.low) / previous_close) * 100.0
    if quote.last is not None and quote.high is not None and quote.low is not None and quote.high != quote.low:
        position_in_range_pct = ((quote.last - quote.low) / (quote.high - quote.low)) * 100.0
    return {
        "change": _rounded(quote.change),
        "change_pct": _rounded(quote.change_pct),
        "gap_pct": _rounded(gap_pct),
        "intraday_range_pct": _rounded(intraday_range_pct),
        "position_in_range_pct": _rounded(position_in_range_pct),
    }


def compute_series_metrics(series: MarketSeries) -> dict[str, Any]:
    points = series.points
    closes = [point.close for point in points]
    returns = [
        (closes[index] / closes[index - 1]) - 1.0
        for index in range(1, len(closes))
        if closes[index - 1] != 0
    ]
    interval_return_pct = ((closes[-1] / closes[0]) - 1.0) * 100.0 if len(closes) >= 2 and closes[0] else None
    latest_return_pct = returns[-1] * 100.0 if returns else None
    max_high = max(point.high for point in points)
    min_low = min(point.low for point in points)
    total_range_pct = ((max_high - min_low) / closes[0]) * 100.0 if closes and closes[0] else None
    annualization = {"1d": 252, "1w": 52, "1mo": 12}.get(series.interval)
    annualized_volatility_pct = None
    if annualization and len(returns) >= 2:
        annualized_volatility_pct = stdev(returns) * math.sqrt(annualization) * 100.0

    peak = closes[0]
    max_drawdown = 0.0
    for close in closes:
        peak = max(peak, close)
        if peak:
            max_drawdown = max(max_drawdown, (peak - close) / peak)

    moving_averages = {
        f"ma{window}": _rounded(sum(closes[-window:]) / window) if len(closes) >= window else None
        for window in (5, 10, 20, 60)
    }
    volumes = [point.volume for point in points]
    relative_volume_20 = None
    relative_volume_window = 0
    if len(volumes) >= 2 and volumes[-1] is not None:
        history = [value for value in volumes[-21:-1] if value is not None]
        relative_volume_window = len(history)
        if history and sum(history) != 0:
            relative_volume_20 = volumes[-1] / (sum(history) / len(history))

    prior_20 = points[-21:-1]
    breakout_20d = None
    breakdown_20d = None
    if prior_20:
        breakout_20d = closes[-1] > max(point.high for point in prior_20)
        breakdown_20d = closes[-1] < min(point.low for point in prior_20)

    return {
        "observation_count": len(points),
        "start_close": _rounded(closes[0]),
        "end_close": _rounded(closes[-1]),
        "interval_return_pct": _rounded(interval_return_pct),
        "latest_return_pct": _rounded(latest_return_pct),
        "total_range_pct": _rounded(total_range_pct),
        "annualized_volatility_pct": _rounded(annualized_volatility_pct),
        "max_drawdown_pct": _rounded(max_drawdown * 100.0),
        "relative_volume_20": _rounded(relative_volume_20),
        "relative_volume": {
            "value": _rounded(relative_volume_20),
            "formula": "latest_volume / mean(previous_completed_period_volumes)",
            "window": relative_volume_window,
            "target_window": 20,
            "alignment": "completed periods only; no intraday elapsed-time adjustment",
        },
        "breakout_20_period": breakout_20d,
        "breakdown_20_period": breakdown_20d,
        "moving_averages": moving_averages,
    }


def _event_public(event: MarketEvent) -> dict[str, Any]:
    return {
        **event.to_public_dict(),
        "published_at_iso": timestamp_to_iso(event.published_at, "Asia/Shanghai"),
        "produced_at_iso": timestamp_to_iso(event.produced_at, "Asia/Shanghai"),
        "received_at_iso": timestamp_to_iso(event.received_at, "Asia/Shanghai"),
    }


def _rounded(value: float | None, digits: int = 6) -> float | None:
    return round(float(value), digits) if value is not None and math.isfinite(float(value)) else None


__all__ = [
    "MarketDataToolService",
    "MarketDataValidationError",
    "compute_quote_metrics",
    "compute_series_metrics",
]
