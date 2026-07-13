from __future__ import annotations

import math
import re
from statistics import stdev
import threading
import time
from typing import Any

from services.market_data import (
    MarketDataProvider,
    MarketDataResponse,
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

    def __init__(
        self,
        *,
        provider: MarketDataProvider,
        event_store: MarketEventStore,
        clock=time.time,
        resolved_code_ttl_seconds: int = 10 * 60,
        readiness_health_ttl_seconds: float = 15.0,
        readiness_failure_ttl_seconds: float = 60.0,
    ) -> None:
        self.provider = provider
        self.event_store = event_store
        self._clock = clock
        self._resolved_code_ttl_seconds = max(30, min(60 * 60, int(resolved_code_ttl_seconds)))
        self._resolved_codes: dict[tuple[str, str, str], int] = {}
        self._resolved_codes_lock = threading.RLock()
        self._readiness_health_ttl_seconds = max(1.0, float(readiness_health_ttl_seconds))
        self._readiness_failure_ttl_seconds = max(5.0, float(readiness_failure_ttl_seconds))
        self._readiness_lock = threading.RLock()
        self._provider_health_cache: tuple[float, Any] | None = None
        self._capability_failures: dict[str, tuple[float, str]] = {}

    def capability_status(
        self,
        capabilities: str | tuple[str, ...],
        *,
        require_provider_health: bool = True,
    ) -> dict[str, Any]:
        required = (capabilities,) if isinstance(capabilities, str) else tuple(capabilities)
        unsupported = [name for name in required if not self.provider.supports(name)]
        if unsupported:
            return {
                "enabled": False,
                "status": "unsupported",
                "reason": "provider_capability_missing:" + ",".join(unsupported),
                "cache_ttl_seconds": 30.0,
            }

        now = float(self._clock())
        with self._readiness_lock:
            for name in required:
                failure = self._capability_failures.get(name)
                if failure is not None and failure[0] > now:
                    return {
                        "enabled": False,
                        "status": "unavailable",
                        "reason": failure[1],
                        "cache_ttl_seconds": 5.0,
                    }
        if not require_provider_health:
            return {"enabled": True, "status": "ready", "reason": "", "cache_ttl_seconds": 15.0}

        try:
            health = self._cached_provider_health(now)
        except Exception as exc:
            return {
                "enabled": False,
                "status": "unavailable",
                "reason": f"provider_health_failed:{type(exc).__name__}",
                "cache_ttl_seconds": 5.0,
            }
        return {
            "enabled": bool(health.ok),
            "status": str(health.status or "unavailable"),
            "reason": str(health.last_error_reason or ""),
            "cache_ttl_seconds": 5.0,
        }

    def _cached_provider_health(self, now: float) -> Any:
        with self._readiness_lock:
            cached = self._provider_health_cache
            if cached is not None and cached[0] > now:
                return cached[1]
        health = self.provider.health()
        with self._readiness_lock:
            self._provider_health_cache = (now + self._readiness_health_ttl_seconds, health)
        return health

    def _record_provider_result(self, capability: str, response: MarketDataResponse[Any]) -> None:
        status = str(response.status or "").strip().lower()
        with self._readiness_lock:
            if response.ok or status == "empty":
                self._capability_failures.pop(capability, None)
                return
            if status in {"permission_denied", "rate_limited", "unavailable"}:
                self._capability_failures[capability] = (
                    float(self._clock()) + self._readiness_failure_ttl_seconds,
                    str(response.reason or f"provider_{status}")[:240],
                )

    def resolve_security(
        self,
        query: str,
        *,
        profile_user_id: str,
        session_id: str,
        limit: int = 10,
    ) -> dict[str, Any]:
        candidates = list(
            self.event_store.resolve_security(
                query,
                provider=self.provider.id,
                profile_user_id=profile_user_id,
                session_id=session_id,
                limit=limit,
            )
        )
        if not candidates:
            discover = getattr(self.provider, "discover_securities", None)
            if callable(discover):
                try:
                    discovered = tuple(discover(query, limit=limit) or ())
                except Exception:
                    discovered = ()
                now_ts = max(1, int(self._clock()))
                for instrument in discovered:
                    try:
                        self.event_store.upsert_security(
                            provider=self.provider.id,
                            code=instrument.canonical_code,
                            display_name=instrument.display_name,
                            aliases=instrument.aliases,
                            market=instrument.market,
                            security_type=instrument.instrument_type,
                            source="Yahoo Finance Search (runtime verified)",
                            as_of=now_ts,
                            now_ts=now_ts,
                        )
                    except Exception:
                        continue
                if discovered:
                    candidates = list(
                        self.event_store.resolve_security(
                            query,
                            provider=self.provider.id,
                            profile_user_id=profile_user_id,
                            session_id=session_id,
                            limit=limit,
                        )
                    )
        by_code: dict[str, dict[str, Any]] = {}
        match_priority = {"exact": 0, "embedded": 1, "partial": 2}
        for candidate in candidates:
            code = str(candidate.get("code") or "").strip()
            if not code:
                continue
            existing = by_code.get(code)
            candidate_priority = match_priority.get(str(candidate.get("match_type") or ""), 3)
            existing_priority = match_priority.get(str((existing or {}).get("match_type") or ""), 3)
            if existing is None or candidate_priority < existing_priority:
                by_code[code] = dict(candidate)
        unique = list(by_code.values())
        exact = [item for item in unique if str(item.get("match_type") or "") == "exact"]
        embedded = [item for item in unique if str(item.get("match_type") or "") == "embedded"]
        resolved_candidates = exact if exact else embedded
        resolved = len(resolved_candidates) == 1
        resolved_code = str(resolved_candidates[0].get("code") or "") if resolved else ""
        if resolved_code:
            self._remember_resolved_code(
                resolved_code,
                profile_user_id=profile_user_id,
                session_id=session_id,
            )
        if not unique:
            reason = (
                "no trusted security master or current-session watchlist entry matched the supplied query; "
                "retry with only the literal user-provided security name or alias, not a guessed vendor symbol"
            )
            resolution_status = "not_found"
        elif resolved:
            reason = "unique exact alias match" if exact else "unique embedded literal alias match"
            resolution_status = "resolved"
        elif exact or embedded:
            reason = "multiple exact or embedded matches; ask the user to disambiguate"
            resolution_status = "ambiguous"
        else:
            reason = "only partial trusted candidates matched; confirm the intended security before querying data"
            resolution_status = "needs_confirmation"
        as_of_ts = max((int(item.get("as_of") or 0) for item in unique), default=0)
        return {
            "ok": bool(unique),
            "status": "ok" if unique else "empty",
            "provider": self.provider.id,
            "source": f"Trusted Security Master ({self.provider.id}) + Current Session Watchlist",
            "provider_capabilities": self.provider.capabilities.to_public_dict(),
            "as_of": timestamp_to_iso(as_of_ts, "Asia/Shanghai") if as_of_ts else None,
            "reason": reason,
            "resolution_status": resolution_status,
            "resolved": resolved,
            "resolved_code": resolved_code,
            "data": [
                {
                    **item,
                    "as_of_iso": timestamp_to_iso(int(item.get("as_of") or 0), "Asia/Shanghai")
                    if int(item.get("as_of") or 0) > 0
                    else None,
                }
                for item in unique
            ],
        }

    def ensure_trusted_codes(
        self,
        codes: tuple[str, ...] | list[str],
        *,
        profile_user_id: str,
        session_id: str,
        request_context: dict[str, Any] | None = None,
    ) -> None:
        context = request_context if isinstance(request_context, dict) else {}
        user_text = "\n".join(
            str(context.get(key) or "")
            for key in ("message", "raw_message", "clean_message")
            if str(context.get(key) or "").strip()
        )
        blocked: list[str] = []
        for code in codes:
            clean_code = str(code or "").strip().upper()
            if not clean_code:
                continue
            if _code_is_literal_in_text(clean_code, user_text):
                continue
            if self._was_recently_resolved(
                clean_code,
                profile_user_id=profile_user_id,
                session_id=session_id,
            ):
                continue
            if (
                profile_user_id
                and session_id
                and self.event_store.is_session_watchlist_code(
                    clean_code,
                    provider=self.provider.id,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                )
            ):
                continue
            blocked.append(clean_code)
        if blocked:
            raise MarketDataValidationError(
                field="codes",
                reason=(
                    "provider code provenance is missing; call market_resolve_security first, "
                    "use a current-session watchlist/master-data code, or quote the full code in the user message"
                ),
                code="untrusted_provider_code",
                status="invalid_arguments",
                provider=self.provider.id,
            )

    def canonicalize_trusted_codes(
        self,
        codes: tuple[str, ...] | list[str],
        *,
        profile_user_id: str,
        session_id: str,
    ) -> tuple[str, ...]:
        canonical: list[str] = []
        for code in codes:
            clean_code = str(code or "").strip().upper()
            if not clean_code:
                continue
            candidates = self.event_store.resolve_security(
                clean_code,
                provider=self.provider.id,
                profile_user_id=profile_user_id,
                session_id=session_id,
                limit=10,
            )
            exact_codes = {
                str(candidate.get("code") or "").strip().upper()
                for candidate in candidates
                if str(candidate.get("match_type") or "") == "exact" and str(candidate.get("code") or "").strip()
            }
            resolved_code = next(iter(exact_codes)) if len(exact_codes) == 1 else clean_code
            if len(exact_codes) == 1:
                self._remember_resolved_code(
                    resolved_code,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                )
            canonical.append(resolved_code)
        return tuple(canonical)

    def _remember_resolved_code(self, code: str, *, profile_user_id: str, session_id: str) -> None:
        expires_at = max(1, int(self._clock())) + self._resolved_code_ttl_seconds
        key = (str(profile_user_id or ""), str(session_id or ""), str(code or "").upper())
        with self._resolved_codes_lock:
            self._resolved_codes[key] = expires_at
            self._purge_resolved_codes_locked(now_ts=max(1, int(self._clock())))

    def _was_recently_resolved(self, code: str, *, profile_user_id: str, session_id: str) -> bool:
        now = max(1, int(self._clock()))
        key = (str(profile_user_id or ""), str(session_id or ""), str(code or "").upper())
        with self._resolved_codes_lock:
            self._purge_resolved_codes_locked(now_ts=now)
            return int(self._resolved_codes.get(key) or 0) >= now

    def _purge_resolved_codes_locked(self, *, now_ts: int) -> None:
        expired = [key for key, expires_at in self._resolved_codes.items() if int(expires_at) < now_ts]
        for key in expired:
            self._resolved_codes.pop(key, None)

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
            self._record_provider_result("news_search", upstream)
            provider_status = upstream.status
            provider_reason = upstream.reason
            if upstream.ok:
                for event in upstream.data:
                    self.event_store.upsert_event(event)
                    by_id[event.event_id] = event
        else:
            provider_reason = "provider query skipped because explicit codes and content_types were not both supplied"

        events = sorted(by_id.values(), key=lambda item: (item.published_at, item.event_id), reverse=True)[
            : request.limit
        ]
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
        response = self.quote_snapshots_response(request)
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

    def quote_snapshots_response(
        self,
        request: MarketQuoteRequest,
    ) -> MarketDataResponse[tuple[MarketQuoteSnapshot, ...]]:
        """Return normalized quote objects for deterministic downstream artifacts."""
        response = self.provider.get_quote_snapshots(request)
        self._record_provider_result("quote_snapshot", response)
        return response

    def price_series(self, request: MarketSeriesRequest) -> dict[str, Any]:
        response = self.price_series_response(request)
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

    def price_series_response(self, request: MarketSeriesRequest) -> MarketDataResponse[MarketSeries | None]:
        """Return the normalized provider object for deterministic downstream renderers."""
        response = self.provider.get_price_series(request)
        self._record_provider_result("price_series", response)
        return response


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
    returns = [(closes[index] / closes[index - 1]) - 1.0 for index in range(1, len(closes)) if closes[index - 1] != 0]
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


def _code_is_literal_in_text(code: str, text: str) -> bool:
    if not code or not text:
        return False
    pattern = rf"(?<![A-Za-z0-9_.]){re.escape(code)}(?![A-Za-z0-9_.])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


__all__ = [
    "MarketDataToolService",
    "MarketDataValidationError",
    "compute_quote_metrics",
    "compute_series_metrics",
]
