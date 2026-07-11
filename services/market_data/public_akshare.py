from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, time as datetime_time, timedelta
import math
import time
from typing import Any
from zoneinfo import ZoneInfo

from .provider import MarketQuoteRequest, MarketSeriesRequest
from .public_cache import TTLMarketDataCache
from .public_instruments import PublicInstrument, PublicInstrumentRegistry, build_default_public_instrument_registry
from .public_retry import call_with_transient_retry, normalize_public_retry_policy
from .types import MarketBar, MarketDataProvenance, MarketDataResponse, MarketDataValidationError, MarketQuoteSnapshot, MarketSeries


AkShareSpotLoader = Callable[[], Any]
AkShareHistoryLoader = Callable[..., Any]


class AkShareDependencyUnavailable(RuntimeError):
    pass


class AkShareSchemaError(ValueError):
    pass


class AkShareETFAdapter:
    provider_id = "public_market"
    source_name = "AkShare/Eastmoney public web data"
    adapter_version = "public_akshare_etf_v1"

    def __init__(
        self,
        *,
        registry: PublicInstrumentRegistry | None = None,
        spot_loader: AkShareSpotLoader | None = None,
        history_loader: AkShareHistoryLoader | None = None,
        cache: TTLMarketDataCache | None = None,
        cache_max_entries: int = 256,
        series_ttl_seconds: float = 300.0,
        quote_ttl_seconds: float = 15.0,
        failure_ttl_seconds: float = 15.0,
        retry_max_attempts: int = 3,
        retry_backoff_seconds: float = 0.2,
        retry_sleeper=time.sleep,
        clock=time.time,
    ) -> None:
        self.registry = registry or build_default_public_instrument_registry()
        self._spot_loader = spot_loader or _default_spot_loader
        self._history_loader = history_loader or _default_history_loader
        self.cache = cache if cache is not None else TTLMarketDataCache(max_entries=cache_max_entries)
        self.series_ttl_seconds = _bounded_ttl(series_ttl_seconds)
        self.quote_ttl_seconds = _bounded_ttl(quote_ttl_seconds)
        self.failure_ttl_seconds = _bounded_ttl(failure_ttl_seconds)
        self.retry_max_attempts, self.retry_backoff_seconds = normalize_public_retry_policy(
            max_attempts=retry_max_attempts,
            backoff_seconds=retry_backoff_seconds,
        )
        self._retry_sleeper = retry_sleeper
        self._clock = clock

    def get_price_series(self, request: MarketSeriesRequest) -> MarketDataResponse[MarketSeries | None]:
        instrument = self._require_request_instrument(request, MarketSeriesRequest)
        if request.interval != "1d" or request.adjusted != "none":
            raise MarketDataValidationError(field="interval", reason="AkShare ETF v1 only supports unadjusted 1d series", code="unsupported_series", status="invalid_arguments", provider=self.provider_id)
        key = (self.adapter_version, instrument.canonical_code, "price_series", request.interval, request.adjusted, request.date_from or 0, request.date_to or 0, request.limit)
        cached = self.cache.get(key)
        if cached.hit:
            return cached.value
        fetched_at = max(1, int(self._clock()))
        zone = ZoneInfo(instrument.exchange_timezone)
        end_date = datetime.fromtimestamp(request.date_to or fetched_at, tz=zone).date()
        start_date = datetime.fromtimestamp(request.date_from, tz=zone).date() if request.date_from else end_date - timedelta(days=max(30, request.limit * 2 + 30))
        try:
            frame = call_with_transient_retry(
                lambda: self._history_loader(symbol=instrument.vendor_symbol, period="daily", start_date=start_date.strftime("%Y%m%d"), end_date=end_date.strftime("%Y%m%d"), adjust=""),
                max_attempts=self.retry_max_attempts,
                backoff_seconds=self.retry_backoff_seconds,
                sleeper=self._retry_sleeper,
            )
            if frame is None or bool(getattr(frame, "empty", False)):
                return self._cache(key, self._empty(instrument, f"no_observations:{instrument.canonical_code}"), self.series_ttl_seconds)
            points = self._normalize_history(frame, instrument=instrument, request=request)
        except AkShareDependencyUnavailable:
            return self._cache(key, self._failure(instrument, "unavailable", "optional_dependency_missing:akshare"), self.series_ttl_seconds)
        except AkShareSchemaError as exc:
            return self._cache(key, self._failure(instrument, "unavailable", f"upstream_schema_changed:akshare_etf_v1:{exc}"), self.series_ttl_seconds)
        except Exception as exc:
            return self._cache(key, self._upstream_failure(instrument, exc), self.series_ttl_seconds)
        if not points:
            return self._cache(key, self._empty(instrument, f"no_observations:{instrument.canonical_code}"), self.series_ttl_seconds)
        provenance = self._provenance(instrument, fetched_at=fetched_at, session="daily", delay_kind="end_of_day")
        series = MarketSeries(provider=self.provider_id, code=instrument.canonical_code, interval="1d", adjusted="none", timezone=instrument.exchange_timezone, points=points, as_of=points[-1].timestamp, provenance=provenance)
        response = MarketDataResponse(ok=True, status="ok", provider=self.provider_id, source=self.source_name, as_of=series.as_of, reason="", data=series, timezone=instrument.exchange_timezone)
        return self._cache(key, response, self.series_ttl_seconds)

    def get_quote_snapshots(self, request: MarketQuoteRequest) -> MarketDataResponse[tuple[MarketQuoteSnapshot, ...]]:
        if not isinstance(request, MarketQuoteRequest):
            raise MarketDataValidationError(field="request", reason="MarketQuoteRequest is required", code="invalid_arguments", status="invalid_arguments", provider=self.provider_id)
        instruments = tuple(self._require_route(self.registry.require(code)) for code in request.codes)
        key = (self.adapter_version, "quote_snapshot", tuple(item.canonical_code for item in instruments))
        cached = self.cache.get(key)
        if cached.hit:
            return cached.value
        fetched_at = max(1, int(self._clock()))
        try:
            frame = call_with_transient_retry(
                self._spot_loader,
                max_attempts=self.retry_max_attempts,
                backoff_seconds=self.retry_backoff_seconds,
                sleeper=self._retry_sleeper,
            )
            if frame is None or bool(getattr(frame, "empty", False)):
                return self._cache(key, self._empty_quotes("no_observations:akshare_etf"), self.quote_ttl_seconds)
            snapshots = self._normalize_spot(frame, instruments=instruments, fetched_at=fetched_at)
        except AkShareDependencyUnavailable:
            return self._cache(key, self._failure_quotes("unavailable", "optional_dependency_missing:akshare"), self.quote_ttl_seconds)
        except AkShareSchemaError as exc:
            return self._cache(key, self._failure_quotes("unavailable", f"upstream_schema_changed:akshare_etf_v1:{exc}"), self.quote_ttl_seconds)
        except Exception as exc:
            return self._cache(key, self._upstream_quote_failure(exc), self.quote_ttl_seconds)
        if len(snapshots) != len(instruments):
            return self._cache(key, self._empty_quotes("missing_requested_etf_snapshot"), self.quote_ttl_seconds)
        response = MarketDataResponse(ok=True, status="ok", provider=self.provider_id, source=self.source_name, as_of=max(item.as_of for item in snapshots), reason="public_web_snapshot", data=snapshots, timezone="Asia/Shanghai")
        return self._cache(key, response, self.quote_ttl_seconds)

    def _normalize_history(self, frame: Any, *, instrument: PublicInstrument, request: MarketSeriesRequest) -> tuple[MarketBar, ...]:
        required = {"日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额"}
        if not required.issubset(set(getattr(frame, "columns", ()) )):
            raise AkShareSchemaError("missing_history_columns")
        records = _records(frame)
        zone = ZoneInfo(instrument.exchange_timezone)
        requested_from = datetime.fromtimestamp(request.date_from, tz=zone).date() if request.date_from else None
        requested_to = datetime.fromtimestamp(request.date_to, tz=zone).date() if request.date_to else None
        points = []
        seen = set()
        for row in records:
            trading_date = _parse_date(row.get("日期"))
            if requested_from is not None and trading_date < requested_from:
                continue
            if requested_to is not None and trading_date > requested_to:
                continue
            text_date = trading_date.isoformat()
            if text_date in seen:
                raise AkShareSchemaError(f"duplicate_trading_date:{text_date}")
            seen.add(text_date)
            open_value, close, high, low = (_number(row.get(name), name) for name in ("开盘", "收盘", "最高", "最低"))
            if high < max(open_value, close, low) or low > min(open_value, close, high):
                raise AkShareSchemaError(f"invalid_ohlc:{text_date}")
            volume_lots = _optional_number(row.get("成交量"), "成交量")
            amount = _optional_number(row.get("成交额"), "成交额")
            if volume_lots is not None and volume_lots < 0:
                raise AkShareSchemaError(f"negative_volume:{text_date}")
            if amount is not None and amount < 0:
                raise AkShareSchemaError(f"negative_amount:{text_date}")
            points.append(MarketBar(timestamp=int(datetime.combine(trading_date, datetime_time.min, tzinfo=zone).timestamp()), open=open_value, high=high, low=low, close=close, volume=volume_lots * 100 if volume_lots is not None else None, amount=amount, trading_date=text_date, time_semantics="trading_date"))
        return tuple(sorted(points, key=lambda item: item.timestamp))[-request.limit:]

    def _normalize_spot(self, frame: Any, *, instruments: tuple[PublicInstrument, ...], fetched_at: int) -> tuple[MarketQuoteSnapshot, ...]:
        required = {"代码", "最新价", "成交量", "成交额", "开盘价", "最高价", "最低价", "昨收", "数据日期", "更新时间"}
        if not required.issubset(set(getattr(frame, "columns", ()))):
            raise AkShareSchemaError("missing_spot_columns")
        wanted = {item.vendor_symbol: item for item in instruments}
        matched: dict[str, MarketQuoteSnapshot] = {}
        for row in _records(frame):
            vendor = str(row.get("代码") or "").strip()
            instrument = wanted.get(vendor)
            if instrument is None:
                continue
            if vendor in matched:
                raise AkShareSchemaError(f"duplicate_spot_code:{vendor}")
            as_of = _parse_datetime(row.get("更新时间"), zone=ZoneInfo(instrument.exchange_timezone))
            trading_date = _parse_date(row.get("数据日期")).isoformat()
            previous_close = _optional_number(row.get("昨收"), "昨收")
            last = _number(row.get("最新价"), "最新价")
            change = last - previous_close if previous_close is not None else None
            change_pct = (change / previous_close) * 100 if change is not None and previous_close else None
            volume_lots = _optional_number(row.get("成交量"), "成交量")
            amount = _optional_number(row.get("成交额"), "成交额")
            open_value = _optional_number(row.get("开盘价"), "开盘价")
            high = _optional_number(row.get("最高价"), "最高价")
            low = _optional_number(row.get("最低价"), "最低价")
            if volume_lots is not None and volume_lots < 0:
                raise AkShareSchemaError(f"negative_volume:{vendor}")
            if amount is not None and amount < 0:
                raise AkShareSchemaError(f"negative_amount:{vendor}")
            known_prices = tuple(value for value in (open_value, low, last) if value is not None)
            if high is not None and known_prices and high < max(known_prices):
                raise AkShareSchemaError(f"invalid_high:{vendor}")
            known_prices = tuple(value for value in (open_value, high, last) if value is not None)
            if low is not None and known_prices and low > min(known_prices):
                raise AkShareSchemaError(f"invalid_low:{vendor}")
            matched[vendor] = MarketQuoteSnapshot(provider=self.provider_id, code=instrument.canonical_code, as_of=as_of, timezone=instrument.exchange_timezone, previous_close=previous_close, open=open_value, high=high, low=low, last=last, volume=volume_lots * 100 if volume_lots is not None else None, amount=amount, change=change, change_pct=change_pct, status="public_web_snapshot", provenance=self._provenance(instrument, fetched_at=fetched_at, session="snapshot", delay_kind="unknown"), trading_date=trading_date, time_semantics="instant")
        return tuple(matched[item.vendor_symbol] for item in instruments if item.vendor_symbol in matched)

    def _require_request_instrument(self, request: Any, expected: type[Any]) -> PublicInstrument:
        if not isinstance(request, expected):
            raise MarketDataValidationError(field="request", reason=f"{expected.__name__} is required", code="invalid_arguments", status="invalid_arguments", provider=self.provider_id)
        return self._require_route(self.registry.require(request.code))

    def _require_route(self, instrument: PublicInstrument) -> PublicInstrument:
        if instrument.route != "akshare_etf":
            raise MarketDataValidationError(field="code", reason=f"instrument is not routed to AkShare ETF: {instrument.canonical_code}", code="unsupported_route", status="invalid_arguments", provider=self.provider_id)
        return instrument

    def _provenance(self, instrument: PublicInstrument, *, fetched_at: int, session: str, delay_kind: str) -> MarketDataProvenance:
        return MarketDataProvenance(source=self.source_name, vendor_symbol=instrument.vendor_symbol, fetched_at=fetched_at, exchange_timezone=instrument.exchange_timezone, currency=instrument.currency, session=session, delay_kind=delay_kind, delay_seconds=None, data_quality="aggregated", adapter_version=self.adapter_version)

    def _cache(self, key: tuple[Any, ...], response: MarketDataResponse[Any], success_ttl: float) -> MarketDataResponse[Any]:
        negative = response.status != "ok"
        self.cache.set(key, response, ttl_seconds=self.failure_ttl_seconds if negative else success_ttl, negative=negative)
        return response

    def _failure(self, instrument: PublicInstrument, status: str, reason: str) -> MarketDataResponse[Any]:
        return MarketDataResponse(ok=False, status=status, provider=self.provider_id, source=self.source_name, as_of=None, reason=reason, data=None, timezone=instrument.exchange_timezone)

    def _empty(self, instrument: PublicInstrument, reason: str) -> MarketDataResponse[Any]:
        return MarketDataResponse(ok=True, status="empty", provider=self.provider_id, source=self.source_name, as_of=None, reason=reason, data=None, timezone=instrument.exchange_timezone)

    def _failure_quotes(self, status: str, reason: str) -> MarketDataResponse[Any]:
        return MarketDataResponse(ok=False, status=status, provider=self.provider_id, source=self.source_name, as_of=None, reason=reason, data=(), timezone="Asia/Shanghai")

    def _empty_quotes(self, reason: str) -> MarketDataResponse[Any]:
        return MarketDataResponse(ok=True, status="empty", provider=self.provider_id, source=self.source_name, as_of=None, reason=reason, data=(), timezone="Asia/Shanghai")

    def _upstream_failure(self, instrument: PublicInstrument, exc: Exception) -> MarketDataResponse[Any]:
        status, reason = _exception_status(exc)
        return self._failure(instrument, status, reason)

    def _upstream_quote_failure(self, exc: Exception) -> MarketDataResponse[Any]:
        status, reason = _exception_status(exc)
        return self._failure_quotes(status, reason)


def _default_spot_loader() -> Any:
    try:
        import akshare as ak
    except ImportError as exc:
        raise AkShareDependencyUnavailable() from exc
    return ak.fund_etf_spot_em()


def _default_history_loader(**kwargs: Any) -> Any:
    try:
        import akshare as ak
    except ImportError as exc:
        raise AkShareDependencyUnavailable() from exc
    return ak.fund_etf_hist_em(**kwargs)


def _records(frame: Any) -> list[Mapping[str, Any]]:
    try:
        records = frame.to_dict(orient="records")
    except Exception as exc:
        raise AkShareSchemaError("records_unavailable") from exc
    if not isinstance(records, list) or any(not isinstance(row, Mapping) for row in records):
        raise AkShareSchemaError("records_not_mappings")
    return records


def _parse_date(value: Any) -> date:
    if hasattr(value, "to_pydatetime") and callable(value.to_pydatetime):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "").strip()[:10])
    except ValueError as exc:
        raise AkShareSchemaError("invalid_date") from exc


def _parse_datetime(value: Any, *, zone: ZoneInfo) -> int:
    if hasattr(value, "to_pydatetime") and callable(value.to_pydatetime):
        value = value.to_pydatetime()
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(str(value or "").strip())
        except ValueError as exc:
            raise AkShareSchemaError("invalid_observation_time") from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=zone)
    return int(value.timestamp())


def _number(value: Any, field: str) -> float:
    parsed = _optional_number(value, field)
    if parsed is None:
        raise AkShareSchemaError(f"missing_numeric:{field}")
    return parsed


def _optional_number(value: Any, field: str) -> float | None:
    if value is None or (isinstance(value, str) and value.strip().lower() in {"", "--", "null", "none", "n/a"}):
        return None
    if isinstance(value, bool):
        raise AkShareSchemaError(f"invalid_numeric:{field}")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise AkShareSchemaError(f"invalid_numeric:{field}") from exc
    if math.isnan(parsed):
        return None
    if not math.isfinite(parsed):
        raise AkShareSchemaError(f"invalid_numeric:{field}")
    return parsed


def _exception_status(exc: Exception) -> tuple[str, str]:
    name = type(exc).__name__.lower()
    if "ratelimit" in name or "rate_limit" in name:
        return "rate_limited", "upstream_rate_limited:akshare"
    if "timeout" in name:
        return "unavailable", "upstream_timeout:akshare"
    return "unavailable", f"upstream_unavailable:akshare:{type(exc).__name__}"


def _bounded_ttl(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("TTL must be numeric")
    ttl = float(value)
    if not 0 < ttl <= 86400:
        raise ValueError("TTL must be greater than zero and at most one day")
    return ttl


__all__ = ["AkShareDependencyUnavailable", "AkShareETFAdapter", "AkShareSchemaError"]
