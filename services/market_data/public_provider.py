from __future__ import annotations

import importlib.util
import time
from typing import Callable

from .provider import (
    MarketDataProvider,
    MarketNewsQuery,
    MarketProviderCapabilities,
    MarketQuoteRequest,
    MarketSeriesRequest,
)
from .public_akshare import AkShareETFAdapter
from .public_instruments import PublicInstrumentRegistry, build_default_public_instrument_registry
from .public_yahoo import YahooFinanceAdapter
from .types import (
    MarketDataResponse,
    MarketDataValidationError,
    MarketEvent,
    MarketProviderHealth,
    MarketQuoteSnapshot,
    MarketSeries,
)


class PublicMarketProvider(MarketDataProvider):
    provider_id = "public_market"
    source_name = "Public Market (Yahoo Finance + AkShare/Eastmoney)"
    capabilities = MarketProviderCapabilities(quote_snapshot=True, price_series=True)

    def __init__(
        self,
        *,
        registry: PublicInstrumentRegistry | None = None,
        yahoo: YahooFinanceAdapter | None = None,
        akshare: AkShareETFAdapter | None = None,
        yahoo_enabled: bool = True,
        akshare_enabled: bool = True,
        dependency_probe: Callable[[str], bool] | None = None,
        clock=time.time,
    ) -> None:
        self.registry = registry or build_default_public_instrument_registry()
        self.yahoo = yahoo or YahooFinanceAdapter(registry=self.registry)
        self.akshare = akshare or AkShareETFAdapter(registry=self.registry)
        self.yahoo_enabled = bool(yahoo_enabled)
        self.akshare_enabled = bool(akshare_enabled)
        self._dependency_probe = dependency_probe or _dependency_available
        self._clock = clock

    def health(self) -> MarketProviderHealth:
        routes = {
            "yahoo": {"enabled": self.yahoo_enabled, "dependency": "yfinance"},
            "akshare_etf": {"enabled": self.akshare_enabled, "dependency": "akshare"},
        }
        enabled = [name for name, item in routes.items() if item["enabled"]]
        available = [name for name in enabled if self._dependency_probe(str(routes[name]["dependency"]))]
        if enabled and len(available) == len(enabled):
            status, ok, reason = "ready", True, ""
        elif available:
            status, ok, reason = "degraded", True, "some_public_routes_unavailable"
        else:
            status, ok, reason = "disconnected", False, "all_public_routes_unavailable"
        return MarketProviderHealth(
            ok=ok,
            status=status,
            provider=self.id,
            source=self.source,
            checked_at=max(1, int(self._clock())),
            logged_in=False,
            last_error_reason=reason,
            quota_status={
                "mode": "public_web",
                "routes": {
                    name: {
                        "enabled": bool(item["enabled"]),
                        "dependency_available": self._dependency_probe(str(item["dependency"]))
                        if item["enabled"]
                        else False,
                    }
                    for name, item in routes.items()
                },
            },
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
        return MarketDataResponse(
            ok=False,
            status="unavailable",
            provider=self.id,
            source=self.source,
            as_of=None,
            reason="capability_unavailable:news_search",
            data=(),
        )

    def get_price_series(self, request: MarketSeriesRequest) -> MarketDataResponse[MarketSeries | None]:
        if not isinstance(request, MarketSeriesRequest):
            raise MarketDataValidationError(
                field="request",
                reason="MarketSeriesRequest is required",
                code="invalid_arguments",
                status="invalid_arguments",
                provider=self.id,
            )
        instrument = self.registry.require(request.code)
        if not self._route_enabled(instrument.route):
            return MarketDataResponse(
                ok=False,
                status="unavailable",
                provider=self.id,
                source=self.source,
                as_of=None,
                reason=f"public_route_disabled:{instrument.route}",
                data=None,
                timezone=instrument.exchange_timezone,
            )
        adapter = self._adapter_for_route(instrument.route)
        return adapter.get_price_series(request)

    def get_quote_snapshots(self, request: MarketQuoteRequest) -> MarketDataResponse[tuple[MarketQuoteSnapshot, ...]]:
        if not isinstance(request, MarketQuoteRequest):
            raise MarketDataValidationError(
                field="request",
                reason="MarketQuoteRequest is required",
                code="invalid_arguments",
                status="invalid_arguments",
                provider=self.id,
            )
        grouped: dict[str, list[str]] = {}
        for code in request.codes:
            instrument = self.registry.require(code)
            grouped.setdefault(instrument.route, []).append(instrument.canonical_code)
        by_code: dict[str, MarketQuoteSnapshot] = {}
        sources: list[str] = []
        for route, codes in grouped.items():
            if not self._route_enabled(route):
                return MarketDataResponse(
                    ok=False,
                    status="unavailable",
                    provider=self.id,
                    source=self.source,
                    as_of=None,
                    reason=f"public_route_disabled:{route}",
                    data=(),
                )
            result = self._adapter_for_route(route).get_quote_snapshots(MarketQuoteRequest(codes=tuple(codes)))
            if not result.ok:
                return MarketDataResponse(
                    ok=False,
                    status=result.status,
                    provider=self.id,
                    source=self.source,
                    as_of=None,
                    reason=result.reason,
                    data=(),
                )
            if result.status == "empty":
                return MarketDataResponse(
                    ok=True,
                    status="empty",
                    provider=self.id,
                    source=self.source,
                    as_of=None,
                    reason=result.reason,
                    data=(),
                )
            sources.append(result.source)
            by_code.update({item.code: item for item in result.data})
        if any(code not in by_code for code in request.codes):
            return MarketDataResponse(
                ok=True,
                status="empty",
                provider=self.id,
                source=self.source,
                as_of=None,
                reason="missing_requested_public_snapshot",
                data=(),
            )
        snapshots = tuple(by_code[code] for code in request.codes)
        return MarketDataResponse(
            ok=True,
            status="ok",
            provider=self.id,
            source=" + ".join(dict.fromkeys(sources)),
            as_of=max(item.as_of for item in snapshots),
            reason="",
            data=snapshots,
            timezone=snapshots[0].timezone if len({item.timezone for item in snapshots}) == 1 else "Asia/Shanghai",
        )

    def _adapter_for_route(self, route: str):
        if route == "yahoo":
            return self.yahoo
        if route == "akshare_etf":
            return self.akshare
        raise MarketDataValidationError(
            field="code",
            reason=f"unsupported public market route: {route}",
            code="unsupported_route",
            status="invalid_arguments",
            provider=self.id,
        )

    def _route_enabled(self, route: str) -> bool:
        return (route == "yahoo" and self.yahoo_enabled) or (route == "akshare_etf" and self.akshare_enabled)


def _dependency_available(package: str) -> bool:
    return importlib.util.find_spec(str(package or "")) is not None


__all__ = ["PublicMarketProvider"]
