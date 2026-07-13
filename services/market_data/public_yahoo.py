from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, time as datetime_time, timedelta, timezone
import math
import time
from typing import Any
from zoneinfo import ZoneInfo

import requests

from .provider import MarketQuoteRequest, MarketSeriesRequest
from .public_cache import TTLMarketDataCache
from .public_instruments import PublicInstrument, PublicInstrumentRegistry, build_default_public_instrument_registry
from .public_retry import call_with_transient_retry, normalize_public_retry_policy
from .types import (
    MarketBar,
    MarketDataProvenance,
    MarketDataResponse,
    MarketDataValidationError,
    MarketQuoteSnapshot,
    MarketSeries,
)


YahooDownloader = Callable[..., Any]
YahooSearcher = Callable[..., Any]


class YahooFinanceDependencyUnavailable(RuntimeError):
    pass


class YahooFinanceSchemaError(ValueError):
    pass


class YahooFinanceRateLimitError(RuntimeError):
    pass


class YahooFinanceAdapter:
    provider_id = "public_market"
    source_name = "Yahoo Finance"
    adapter_version = "public_yahoo_v1"

    def __init__(
        self,
        *,
        registry: PublicInstrumentRegistry | None = None,
        downloader: YahooDownloader | None = None,
        searcher: YahooSearcher | None = None,
        timeout_seconds: float = 8.0,
        cache: TTLMarketDataCache | None = None,
        cache_max_entries: int = 256,
        series_ttl_seconds: float = 900.0,
        quote_ttl_seconds: float = 60.0,
        failure_ttl_seconds: float = 15.0,
        retry_max_attempts: int = 3,
        retry_backoff_seconds: float = 0.2,
        retry_sleeper=time.sleep,
        clock=time.time,
    ) -> None:
        self.registry = registry or build_default_public_instrument_registry()
        self._downloader = downloader or _default_yahoo_chart_downloader
        self._searcher = searcher or _default_yahoo_searcher
        self.timeout_seconds = max(1.0, min(60.0, float(timeout_seconds)))
        self.cache = cache if cache is not None else TTLMarketDataCache(max_entries=cache_max_entries)
        self.series_ttl_seconds = _bounded_ttl(series_ttl_seconds, field="series_ttl_seconds")
        self.quote_ttl_seconds = _bounded_ttl(quote_ttl_seconds, field="quote_ttl_seconds")
        self.failure_ttl_seconds = _bounded_ttl(failure_ttl_seconds, field="failure_ttl_seconds")
        self.retry_max_attempts, self.retry_backoff_seconds = normalize_public_retry_policy(
            max_attempts=retry_max_attempts,
            backoff_seconds=retry_backoff_seconds,
        )
        self._retry_sleeper = retry_sleeper
        self._clock = clock

    def search_quotes(self, query: str, *, max_results: int = 10) -> tuple[dict[str, Any], ...]:
        clean_query = str(query or "").strip()
        if not clean_query:
            return ()
        bounded_results = max(1, min(20, int(max_results)))
        try:
            result = call_with_transient_retry(
                lambda: self._searcher(query=clean_query, max_results=bounded_results, news_count=0),
                max_attempts=self.retry_max_attempts,
                backoff_seconds=self.retry_backoff_seconds,
                sleeper=self._retry_sleeper,
            )
        except Exception:
            return ()
        quotes = getattr(result, "quotes", result)
        if not isinstance(quotes, (list, tuple)):
            return ()
        return tuple(dict(item) for item in quotes[:bounded_results] if isinstance(item, Mapping))

    def get_price_series(self, request: MarketSeriesRequest) -> MarketDataResponse[MarketSeries | None]:
        if not isinstance(request, MarketSeriesRequest):
            raise MarketDataValidationError(
                field="request",
                reason="MarketSeriesRequest is required",
                code="invalid_arguments",
                status="invalid_arguments",
                provider=self.provider_id,
            )
        instrument = self.registry.require(request.code)
        if instrument.route != "yahoo":
            raise MarketDataValidationError(
                field="code",
                reason=f"instrument is not routed to Yahoo Finance: {instrument.canonical_code}",
                code="unsupported_route",
                status="invalid_arguments",
                provider=self.provider_id,
            )
        if request.interval != "1d":
            raise MarketDataValidationError(
                field="interval",
                reason="public Yahoo adapter v1 only supports 1d",
                code="unsupported_interval",
                status="invalid_arguments",
                provider=self.provider_id,
            )
        if request.adjusted != "none":
            raise MarketDataValidationError(
                field="adjusted",
                reason="public Yahoo adapter v1 only supports unadjusted series",
                code="unsupported_adjustment",
                status="invalid_arguments",
                provider=self.provider_id,
            )

        cache_key = self._series_cache_key(request)
        cached = self.cache.get(cache_key)
        if cached.hit:
            return cached.value

        fetched_at = max(1, int(self._clock()))
        download_arguments = self._download_arguments(request, instrument=instrument, now_ts=fetched_at)
        try:
            frame = call_with_transient_retry(
                lambda: self._downloader(**download_arguments),
                max_attempts=self.retry_max_attempts,
                backoff_seconds=self.retry_backoff_seconds,
                sleeper=self._retry_sleeper,
            )
        except YahooFinanceDependencyUnavailable:
            return self._cache_response(
                cache_key,
                self._failure("unavailable", "optional_dependency_missing:yfinance", instrument=instrument),
                success_ttl=self.series_ttl_seconds,
            )
        except YahooFinanceSchemaError as exc:
            return self._cache_response(
                cache_key,
                self._failure(
                    "unavailable",
                    f"upstream_schema_changed:yahoo_chart_v1:{str(exc)}",
                    instrument=instrument,
                ),
                success_ttl=self.series_ttl_seconds,
            )
        except Exception as exc:
            exception_name = type(exc).__name__.lower()
            if "ratelimit" in exception_name or "rate_limit" in exception_name:
                return self._cache_response(
                    cache_key,
                    self._failure("rate_limited", "upstream_rate_limited:yahoo", instrument=instrument),
                    success_ttl=self.series_ttl_seconds,
                )
            if "timeout" in exception_name:
                return self._cache_response(
                    cache_key,
                    self._failure("unavailable", "upstream_timeout:yahoo", instrument=instrument),
                    success_ttl=self.series_ttl_seconds,
                )
            return self._cache_response(
                cache_key,
                self._failure(
                    "unavailable",
                    f"upstream_unavailable:yahoo:{type(exc).__name__}",
                    instrument=instrument,
                ),
                success_ttl=self.series_ttl_seconds,
            )

        if frame is None or bool(getattr(frame, "empty", False)):
            return self._cache_response(
                cache_key,
                self._empty(instrument, reason=f"no_observations:{instrument.canonical_code}"),
                success_ttl=self.series_ttl_seconds,
            )
        try:
            points = self._normalize_frame(frame, instrument=instrument, request=request)
        except YahooFinanceSchemaError as exc:
            return self._cache_response(
                cache_key,
                self._failure(
                    "unavailable",
                    f"upstream_schema_changed:yahoo_v1:{str(exc)}",
                    instrument=instrument,
                ),
                success_ttl=self.series_ttl_seconds,
            )
        if not points:
            return self._cache_response(
                cache_key,
                self._empty(instrument, reason=f"no_observations:{instrument.canonical_code}"),
                success_ttl=self.series_ttl_seconds,
            )

        provenance = MarketDataProvenance(
            source=self.source_name,
            vendor_symbol=instrument.vendor_symbol,
            fetched_at=fetched_at,
            exchange_timezone=instrument.exchange_timezone,
            currency=instrument.currency,
            session="daily",
            delay_kind="end_of_day",
            delay_seconds=None,
            data_quality="public_web",
            adapter_version=self.adapter_version,
        )
        series = MarketSeries(
            provider=self.provider_id,
            code=instrument.canonical_code,
            interval="1d",
            adjusted="none",
            timezone=instrument.exchange_timezone,
            points=points,
            as_of=points[-1].timestamp,
            provenance=provenance,
        )
        return self._cache_response(
            cache_key,
            MarketDataResponse(
                ok=True,
                status="ok",
                provider=self.provider_id,
                source=self.source_name,
                as_of=series.as_of,
                reason="",
                data=series,
                timezone=instrument.exchange_timezone,
            ),
            success_ttl=self.series_ttl_seconds,
        )

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
                provider=self.provider_id,
            )
        instruments = tuple(self.registry.require(code) for code in request.codes)
        for instrument in instruments:
            if instrument.route != "yahoo":
                raise MarketDataValidationError(
                    field="codes",
                    reason=f"instrument is not routed to Yahoo Finance: {instrument.canonical_code}",
                    code="unsupported_route",
                    status="invalid_arguments",
                    provider=self.provider_id,
                )

        snapshots: list[MarketQuoteSnapshot] = []
        for instrument in instruments:
            result = self._get_quote_snapshot(instrument)
            if result.status == "empty":
                return MarketDataResponse(
                    ok=True,
                    status="empty",
                    provider=self.provider_id,
                    source=self.source_name,
                    as_of=None,
                    reason=result.reason,
                    data=(),
                    timezone=instrument.exchange_timezone,
                )
            if not result.ok or result.data is None:
                return MarketDataResponse(
                    ok=False,
                    status=result.status,
                    provider=self.provider_id,
                    source=self.source_name,
                    as_of=None,
                    reason=result.reason,
                    data=(),
                    timezone=instrument.exchange_timezone,
                )
            snapshots.append(result.data)

        response_timezone = instruments[0].exchange_timezone if len(instruments) == 1 else "Asia/Shanghai"
        return MarketDataResponse(
            ok=True,
            status="ok",
            provider=self.provider_id,
            source=self.source_name,
            as_of=max(snapshot.as_of for snapshot in snapshots),
            reason="latest_completed_daily_bar",
            data=tuple(snapshots),
            timezone=response_timezone,
        )

    def _get_quote_snapshot(
        self,
        instrument: PublicInstrument,
    ) -> MarketDataResponse[MarketQuoteSnapshot | None]:
        cache_key = (self.adapter_version, instrument.canonical_code, "quote_snapshot")
        cached = self.cache.get(cache_key)
        if cached.hit:
            return cached.value
        series_result = self.get_price_series(MarketSeriesRequest(code=instrument.canonical_code, limit=5))
        if not series_result.ok:
            return self._cache_response(
                cache_key,
                self._failure(series_result.status, series_result.reason, instrument=instrument),
                success_ttl=self.quote_ttl_seconds,
            )
        if series_result.status == "empty" or series_result.data is None:
            return self._cache_response(
                cache_key,
                self._empty(instrument, reason=series_result.reason),
                success_ttl=self.quote_ttl_seconds,
            )

        series = series_result.data
        fetched_at = series.provenance.fetched_at if series.provenance is not None else max(1, int(self._clock()))
        fetched_date = datetime.fromtimestamp(fetched_at, tz=ZoneInfo(instrument.exchange_timezone)).date()
        completed = tuple(
            point
            for point in series.points
            if point.trading_date and date.fromisoformat(point.trading_date) < fetched_date
        )
        if not completed:
            return self._cache_response(
                cache_key,
                self._failure("unavailable", "observation_time_unavailable", instrument=instrument),
                success_ttl=self.quote_ttl_seconds,
            )
        latest = completed[-1]
        previous = completed[-2] if len(completed) > 1 else None
        previous_close = previous.close if previous is not None else None
        change = latest.close - previous_close if previous_close is not None else None
        change_pct = (change / previous_close) * 100.0 if change is not None and previous_close else None
        snapshot = MarketQuoteSnapshot(
            provider=self.provider_id,
            code=instrument.canonical_code,
            as_of=latest.timestamp,
            timezone=instrument.exchange_timezone,
            previous_close=previous_close,
            open=latest.open,
            high=latest.high,
            low=latest.low,
            last=latest.close,
            volume=latest.volume,
            amount=latest.amount,
            change=change,
            change_pct=change_pct,
            status="end_of_day",
            provenance=series.provenance,
            trading_date=latest.trading_date,
            time_semantics="trading_date",
        )
        return self._cache_response(
            cache_key,
            MarketDataResponse(
                ok=True,
                status="ok",
                provider=self.provider_id,
                source=self.source_name,
                as_of=snapshot.as_of,
                reason="latest_completed_daily_bar",
                data=snapshot,
                timezone=instrument.exchange_timezone,
            ),
            success_ttl=self.quote_ttl_seconds,
        )

    def _download_arguments(
        self,
        request: MarketSeriesRequest,
        *,
        instrument: PublicInstrument,
        now_ts: int,
    ) -> dict[str, Any]:
        zone = ZoneInfo(instrument.exchange_timezone)
        now_date = datetime.fromtimestamp(now_ts, tz=zone).date()
        end_date = (
            datetime.fromtimestamp(request.date_to, tz=zone).date() + timedelta(days=1)
            if request.date_to is not None
            else now_date + timedelta(days=1)
        )
        lookback_days = max(30, min(20_000, request.limit * 2 + 30))
        start_date = (
            datetime.fromtimestamp(request.date_from, tz=zone).date()
            if request.date_from is not None
            else end_date - timedelta(days=lookback_days)
        )
        return {
            "tickers": instrument.vendor_symbol,
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "interval": "1d",
            "auto_adjust": False,
            "multi_level_index": False,
            "threads": False,
            "progress": False,
            "timeout": self.timeout_seconds,
        }

    def _normalize_frame(
        self,
        frame: Any,
        *,
        instrument: PublicInstrument,
        request: MarketSeriesRequest,
    ) -> tuple[MarketBar, ...]:
        columns = {_normalize_column_name(value) for value in tuple(getattr(frame, "columns", ()))}
        missing = {"Open", "High", "Low", "Close"} - columns
        if missing:
            raise YahooFinanceSchemaError("missing_columns:" + ",".join(sorted(missing)))
        try:
            records = frame.reset_index().to_dict(orient="records")
        except Exception as exc:
            raise YahooFinanceSchemaError("records_unavailable") from exc
        if not isinstance(records, list):
            raise YahooFinanceSchemaError("records_not_list")

        zone = ZoneInfo(instrument.exchange_timezone)
        requested_date_from = (
            datetime.fromtimestamp(request.date_from, tz=zone).date() if request.date_from is not None else None
        )
        requested_date_to = (
            datetime.fromtimestamp(request.date_to, tz=zone).date() if request.date_to is not None else None
        )
        points: list[MarketBar] = []
        seen_dates: set[str] = set()
        for raw_record in records:
            if not isinstance(raw_record, Mapping):
                raise YahooFinanceSchemaError("record_not_mapping")
            record = {_normalize_column_name(key): value for key, value in raw_record.items()}
            raw_date = next(
                (record.get(key) for key in ("Date", "Datetime", "index") if record.get(key) is not None), None
            )
            trading_date = _parse_trading_date(raw_date, zone=zone)
            if requested_date_from is not None and trading_date < requested_date_from:
                continue
            if requested_date_to is not None and trading_date > requested_date_to:
                continue
            trading_date_text = trading_date.isoformat()
            if trading_date_text in seen_dates:
                raise YahooFinanceSchemaError(f"duplicate_trading_date:{trading_date_text}")
            seen_dates.add(trading_date_text)
            timestamp = int(datetime.combine(trading_date, datetime_time.min, tzinfo=zone).timestamp())
            open_value = _required_number(record.get("Open"), field="Open")
            high = _required_number(record.get("High"), field="High")
            low = _required_number(record.get("Low"), field="Low")
            close = _required_number(record.get("Close"), field="Close")
            volume = _optional_number(record.get("Volume"), field="Volume")
            if high < max(open_value, low, close):
                raise YahooFinanceSchemaError(f"invalid_high:{trading_date_text}")
            if low > min(open_value, high, close):
                raise YahooFinanceSchemaError(f"invalid_low:{trading_date_text}")
            if volume is not None and volume < 0:
                raise YahooFinanceSchemaError(f"negative_volume:{trading_date_text}")
            points.append(
                MarketBar(
                    timestamp=timestamp,
                    open=open_value,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    amount=None,
                    trading_date=trading_date_text,
                    time_semantics="trading_date",
                )
            )

        ordered = tuple(sorted(points, key=lambda point: point.timestamp))
        return ordered[-request.limit :]

    def _series_cache_key(self, request: MarketSeriesRequest) -> tuple[Any, ...]:
        return (
            self.adapter_version,
            request.code,
            "price_series",
            request.interval,
            request.adjusted,
            request.date_from or 0,
            request.date_to or 0,
            request.limit,
        )

    def _cache_response(
        self,
        key: tuple[Any, ...],
        response: MarketDataResponse[Any],
        *,
        success_ttl: float,
    ) -> MarketDataResponse[Any]:
        negative = response.status != "ok"
        ttl = self.failure_ttl_seconds if negative else success_ttl
        self.cache.set(key, response, ttl_seconds=ttl, negative=negative)
        return response

    def _failure(
        self,
        status: str,
        reason: str,
        *,
        instrument: PublicInstrument,
    ) -> MarketDataResponse[Any]:
        return MarketDataResponse(
            ok=False,
            status=status,
            provider=self.provider_id,
            source=self.source_name,
            as_of=None,
            reason=reason,
            data=None,
            timezone=instrument.exchange_timezone,
        )

    def _empty(
        self,
        instrument: PublicInstrument,
        *,
        reason: str,
    ) -> MarketDataResponse[Any]:
        return MarketDataResponse(
            ok=True,
            status="empty",
            provider=self.provider_id,
            source=self.source_name,
            as_of=None,
            reason=reason,
            data=None,
            timezone=instrument.exchange_timezone,
        )


def _default_yahoo_downloader(**kwargs: Any) -> Any:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise YahooFinanceDependencyUnavailable("yfinance is not installed") from exc
    arguments = dict(kwargs)
    symbol = str(arguments.pop("tickers", "") or "").strip()
    arguments.pop("multi_level_index", None)
    arguments.pop("threads", None)
    arguments.pop("progress", None)
    return yf.Ticker(symbol).history(
        **arguments,
        actions=False,
        back_adjust=False,
        repair=False,
        keepna=False,
        raise_errors=True,
    )


class _YahooChartFrame:
    columns = ("Date", "Open", "High", "Low", "Close", "Volume")

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.empty = not rows

    def reset_index(self) -> "_YahooChartFrame":
        return self

    def to_dict(self, *, orient: str) -> list[dict[str, Any]]:
        if orient != "records":
            raise ValueError("Yahoo chart frame only supports records orientation")
        return [dict(row) for row in self._rows]


def _default_yahoo_chart_downloader(**kwargs: Any) -> _YahooChartFrame:
    symbol = str(kwargs.get("tickers") or "").strip()
    if not symbol:
        raise YahooFinanceSchemaError("missing_symbol")
    try:
        start_date = date.fromisoformat(str(kwargs.get("start") or ""))
        end_date = date.fromisoformat(str(kwargs.get("end") or ""))
    except ValueError as exc:
        raise YahooFinanceSchemaError("invalid_date_range") from exc
    timeout_seconds = max(1.0, min(60.0, float(kwargs.get("timeout") or 8.0)))
    response = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(symbol, safe='')}",
        params={
            "period1": int(datetime.combine(start_date, datetime_time.min, tzinfo=timezone.utc).timestamp()),
            "period2": int(datetime.combine(end_date, datetime_time.min, tzinfo=timezone.utc).timestamp()),
            "interval": "1d",
            "events": "history",
        },
        headers={"User-Agent": "AkaneCompanionLab/1.0 market-readiness"},
        timeout=timeout_seconds,
    )
    if response.status_code == 429:
        raise YahooFinanceRateLimitError("Yahoo chart API rate limited the request")
    response.raise_for_status()
    payload = response.json()
    chart = payload.get("chart") if isinstance(payload, Mapping) else None
    error = chart.get("error") if isinstance(chart, Mapping) else None
    results = chart.get("result") if isinstance(chart, Mapping) else None
    if error:
        raise YahooFinanceSchemaError("chart_error")
    if not isinstance(results, list) or not results or not isinstance(results[0], Mapping):
        raise YahooFinanceSchemaError("missing_chart_result")
    result = results[0]
    timestamps = result.get("timestamp")
    indicators = result.get("indicators")
    quote_items = indicators.get("quote") if isinstance(indicators, Mapping) else None
    quote = quote_items[0] if isinstance(quote_items, list) and quote_items else None
    if not isinstance(timestamps, list) or not isinstance(quote, Mapping):
        raise YahooFinanceSchemaError("missing_chart_observations")
    exchange_timezone = str((result.get("meta") or {}).get("exchangeTimezoneName") or "UTC")
    try:
        zone = ZoneInfo(exchange_timezone)
    except Exception:
        zone = ZoneInfo("UTC")
    rows: list[dict[str, Any]] = []
    for index, raw_timestamp in enumerate(timestamps):
        values = {
            "Open": _sequence_item(quote.get("open"), index),
            "High": _sequence_item(quote.get("high"), index),
            "Low": _sequence_item(quote.get("low"), index),
            "Close": _sequence_item(quote.get("close"), index),
            "Volume": _sequence_item(quote.get("volume"), index),
        }
        if any(values[name] is None for name in ("Open", "High", "Low", "Close")):
            continue
        try:
            trading_date = datetime.fromtimestamp(int(raw_timestamp), tz=zone).date().isoformat()
        except (TypeError, ValueError, OSError, OverflowError):
            continue
        rows.append({"Date": trading_date, **values})
    return _YahooChartFrame(rows)


def _sequence_item(value: Any, index: int) -> Any:
    if not isinstance(value, (list, tuple)) or index >= len(value):
        return None
    return value[index]


def _default_yahoo_searcher(**kwargs: Any) -> Any:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise YahooFinanceDependencyUnavailable("yfinance is not installed") from exc
    return yf.Search(**kwargs)


def _normalize_column_name(value: Any) -> str:
    if isinstance(value, tuple):
        value = value[0] if value else ""
    text = str(value or "").strip()
    aliases = {
        "date": "Date",
        "datetime": "Datetime",
        "index": "index",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "adj close": "Adj Close",
        "volume": "Volume",
    }
    return aliases.get(text.casefold(), text)


def _parse_trading_date(value: Any, *, zone: ZoneInfo) -> date:
    if value is None:
        raise YahooFinanceSchemaError("missing_trading_date")
    if hasattr(value, "to_pydatetime") and callable(value.to_pydatetime):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(zone).date()
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise YahooFinanceSchemaError("invalid_trading_date") from exc


def _required_number(value: Any, *, field: str) -> float:
    parsed = _optional_number(value, field=field)
    if parsed is None:
        raise YahooFinanceSchemaError(f"missing_numeric:{field}")
    return parsed


def _optional_number(value: Any, *, field: str) -> float | None:
    if value is None or (isinstance(value, str) and value.strip().lower() in {"", "--", "null", "none", "n/a"}):
        return None
    if isinstance(value, bool):
        raise YahooFinanceSchemaError(f"invalid_numeric:{field}")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise YahooFinanceSchemaError(f"invalid_numeric:{field}") from exc
    if math.isnan(parsed):
        return None
    if not math.isfinite(parsed):
        raise YahooFinanceSchemaError(f"invalid_numeric:{field}")
    return parsed


def _bounded_ttl(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    ttl = float(value)
    if not 0 < ttl <= 24 * 60 * 60:
        raise ValueError(f"{field} must be greater than zero and at most one day")
    return ttl


__all__ = [
    "YahooFinanceAdapter",
    "YahooFinanceDependencyUnavailable",
    "YahooFinanceSchemaError",
]
