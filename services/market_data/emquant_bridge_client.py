from __future__ import annotations

from datetime import datetime
import time
from typing import Any, Mapping
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from .normalizers import (
    normalize_choice_news_record,
    normalize_choice_quote_record,
    normalize_choice_series_record,
)
from .provider import MarketDataProvider, MarketNewsQuery, MarketQuoteRequest, MarketSeriesRequest
from .types import (
    MarketDataResponse,
    MarketDataValidationError,
    MarketEvent,
    MarketProviderHealth,
    MarketQuoteSnapshot,
    MarketSeries,
)


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_BRIDGE_FAILURE_STATUS = {
    "permission_denied": "permission_denied",
    "unauthorized": "permission_denied",
    "rate_limited": "rate_limited",
    "invalid_arguments": "invalid_arguments",
}
_INTERVAL_PERIOD = {"1d": 1, "1w": 2, "1mo": 3}
# Choice's official demo uses AdjustFlag=1. The other two mappings stay in one
# provider-specific location so they can be corrected after the real-account
# smoke test without changing the public MarketSeries contract.
_ADJUST_FLAG = {"none": 1, "forward": 2, "backward": 3}
_MARKET_ZONE = ZoneInfo("Asia/Shanghai")


class EmQuantBridgeMarketDataProvider(MarketDataProvider):
    """Read-only MarketDataProvider backed by the loopback EmQuant bridge."""

    provider_id = "choice_emquant"
    source_name = "Choice EmQuant"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:9910",
        *,
        access_token: str = "",
        timeout_seconds: float = 15.0,
        session: Any = None,
        provider_id: str = "choice_emquant",
        source_name: str = "Choice EmQuant",
        clock=time.time,
    ) -> None:
        self.base_url = _normalize_loopback_url(base_url)
        self.access_token = str(access_token or "").strip()
        self.timeout_seconds = max(1.0, min(60.0, float(timeout_seconds)))
        self.session = session or requests.Session()
        self.provider_id = str(provider_id or "choice_emquant").strip() or "choice_emquant"
        self.source_name = str(source_name or "Choice EmQuant").strip() or "Choice EmQuant"
        self._clock = clock

    def health(self) -> MarketProviderHealth:
        payload, failure = self._request("GET", "/health")
        checked_at = max(1, int(self._clock()))
        if failure is not None or payload is None:
            return MarketProviderHealth(
                ok=False,
                status="permission_denied" if failure and failure[0] == "permission_denied" else "disconnected",
                provider=self.id,
                source=self.source,
                checked_at=checked_at,
                logged_in=False,
                last_error_reason=failure[1] if failure else "bridge unavailable",
            )
        status = str(payload.get("status") or "disconnected").strip().lower()
        if status not in {"ready", "degraded", "disconnected", "permission_denied"}:
            status = "disconnected"
        return MarketProviderHealth(
            ok=status in {"ready", "degraded"},
            status=status,
            provider=str(payload.get("provider") or self.id),
            source=str(payload.get("source") or self.source),
            checked_at=_positive_int(payload.get("checked_at"), fallback=checked_at),
            logged_in=bool(payload.get("logged_in")),
            news_subscription_count=_nonnegative_int(payload.get("news_subscription_count")),
            quote_subscription_count=_nonnegative_int(payload.get("quote_subscription_count")),
            last_news_at=_nonnegative_int(payload.get("last_news_at")),
            last_quote_at=_nonnegative_int(payload.get("last_quote_at")),
            last_error_code=_safe_int(payload.get("last_error_code")),
            last_error_reason=str(payload.get("last_error_reason") or "")[:2000],
            quota_status=dict(payload.get("quota_status") or {}) if isinstance(payload.get("quota_status"), Mapping) else {},
        )

    def search_news(self, request: MarketNewsQuery) -> MarketDataResponse[tuple[MarketEvent, ...]]:
        if not isinstance(request, MarketNewsQuery):
            raise _invalid_request("request", "MarketNewsQuery is required", provider=self.id)
        if not request.codes or not request.content_types:
            return self._response(
                status="unavailable",
                data=(),
                reason="Choice news lookup requires explicit provider codes and content_types; broad market query was not sent",
            )
        payload, failure = self._request(
            "POST",
            "/news/query",
            json_body={
                "codes": list(request.codes),
                "content_types": list(request.content_types),
                "mode": 2,
                "options": f"count={request.limit}",
            },
        )
        if failure is not None or payload is None:
            return self._response(status=failure[0], data=(), reason=failure[1])
        records = _bridge_records(payload)
        query_text = request.query.casefold()
        events: list[MarketEvent] = []
        for raw in records:
            search_text = " ".join(
                str(raw.get(key) or "") for key in ("code", "title", "content", "medianname", "label", "type")
            ).casefold()
            if query_text and query_text not in search_text:
                continue
            try:
                event = normalize_choice_news_record(
                    raw,
                    provider=self.id,
                    event_namespace="choice",
                    received_at=max(1, int(self._clock())),
                )
            except MarketDataValidationError:
                continue
            if request.date_from is not None and event.published_at < request.date_from:
                continue
            if request.date_to is not None and event.published_at > request.date_to:
                continue
            events.append(event)
        events.sort(key=lambda item: (item.published_at, item.event_id), reverse=True)
        data = tuple(events[: request.limit])
        return self._response(
            status="ok" if data else "empty",
            data=data,
            as_of=max((item.published_at for item in data), default=None),
            reason="" if data else "Choice returned no matching normalized news records",
        )

    def get_quote_snapshots(
        self,
        request: MarketQuoteRequest,
    ) -> MarketDataResponse[tuple[MarketQuoteSnapshot, ...]]:
        if not isinstance(request, MarketQuoteRequest):
            raise _invalid_request("request", "MarketQuoteRequest is required", provider=self.id)
        payload, failure = self._request(
            "POST",
            "/quotes/snapshot",
            json_body={"codes": list(request.codes)},
        )
        if failure is not None or payload is None:
            return self._response(status=failure[0], data=(), reason=failure[1])
        quotes: list[MarketQuoteSnapshot] = []
        invalid_count = 0
        for raw in _bridge_records(payload):
            try:
                quotes.append(normalize_choice_quote_record(raw, provider=self.id))
            except MarketDataValidationError:
                invalid_count += 1
        reason = f"ignored {invalid_count} invalid quote record(s)" if invalid_count else ""
        data = tuple(quotes)
        return self._response(
            status="ok" if data else "empty",
            data=data,
            as_of=max((item.as_of for item in data), default=None),
            reason=reason or ("Choice returned no normalized quote records" if not data else ""),
        )

    def get_price_series(self, request: MarketSeriesRequest) -> MarketDataResponse[MarketSeries | None]:
        if not isinstance(request, MarketSeriesRequest):
            raise _invalid_request("request", "MarketSeriesRequest is required", provider=self.id)
        if request.interval not in _INTERVAL_PERIOD:
            raise _invalid_request("interval", "bridge supports only 1d, 1w, or 1mo", provider=self.id)
        end_ts = request.date_to or max(1, int(self._clock()))
        start_ts = request.date_from or max(1, end_ts - _default_lookback_seconds(request.interval, request.limit))
        start_date = datetime.fromtimestamp(start_ts, _MARKET_ZONE).date().isoformat()
        end_date = datetime.fromtimestamp(end_ts, _MARKET_ZONE).date().isoformat()
        options = (
            f"Period={_INTERVAL_PERIOD[request.interval]},"
            f"AdjustFlag={_ADJUST_FLAG[request.adjusted]},Order=1,RowIndex=1,Ispandas=0"
        )
        payload, failure = self._request(
            "POST",
            "/prices/series",
            json_body={
                "codes": [request.code],
                "indicators": ["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "AMOUNT"],
                "start_date": start_date,
                "end_date": end_date,
                "options": options,
            },
        )
        if failure is not None or payload is None:
            return self._response(status=failure[0], data=None, reason=failure[1])
        points = [
            dict(raw)
            for raw in _bridge_records(payload)
            if str(raw.get("code") or "").strip().upper() == request.code
        ]
        if not points:
            return self._response(status="empty", data=None, reason="Choice returned no normalized price bars")
        try:
            series = normalize_choice_series_record(
                {
                    "code": request.code,
                    "interval": request.interval,
                    "adjusted": request.adjusted,
                    "timezone": "Asia/Shanghai",
                    "points": points[-request.limit :],
                },
                provider=self.id,
            )
        except MarketDataValidationError as exc:
            return self._response(status="unavailable", data=None, reason=f"invalid Choice series payload: {exc.field}")
        return self._response(status="ok", data=series, as_of=series.as_of, timezone=series.timezone)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, tuple[str, str] | None]:
        headers = {"Accept": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                json=dict(json_body) if json_body is not None else None,
                headers=headers,
                timeout=self.timeout_seconds,
                allow_redirects=False,
            )
        except requests.Timeout:
            return None, ("unavailable", "EmQuant bridge request timed out")
        except requests.RequestException:
            return None, ("unavailable", "EmQuant bridge is unreachable")
        except Exception:
            return None, ("unavailable", "EmQuant bridge transport failed")
        try:
            payload = response.json()
        except Exception:
            return None, ("unavailable", "EmQuant bridge returned invalid JSON")
        if not isinstance(payload, Mapping):
            return None, ("unavailable", "EmQuant bridge returned an invalid response object")
        result = dict(payload)
        if 200 <= int(response.status_code) < 300 and result.get("ok", True) is not False:
            return result, None
        raw_status = str(result.get("status") or "").strip().lower()
        status = _BRIDGE_FAILURE_STATUS.get(raw_status, "unavailable")
        reason = str(result.get("reason") or raw_status or f"bridge HTTP {response.status_code}").strip()
        return None, (status, reason[:2000])

    def _response(
        self,
        *,
        status: str,
        data: Any,
        reason: str = "",
        as_of: int | None = None,
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


def _normalize_loopback_url(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    try:
        parsed = urlparse(text)
        hostname = str(parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise MarketDataValidationError(
            field="base_url",
            reason="EmQuant bridge URL is invalid",
            code="invalid_arguments",
            status="invalid_arguments",
            provider="emquant_bridge",
        ) from exc
    if parsed.scheme != "http" or hostname not in LOOPBACK_HOSTS:
        raise MarketDataValidationError(
            field="base_url",
            reason="EmQuant bridge URL must use http on a loopback host",
            code="invalid_arguments",
            status="invalid_arguments",
            provider="emquant_bridge",
        )
    if port is not None and not 1 <= port <= 65535:
        raise MarketDataValidationError(
            field="base_url",
            reason="EmQuant bridge URL port is invalid",
            code="invalid_arguments",
            status="invalid_arguments",
            provider="emquant_bridge",
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise MarketDataValidationError(
            field="base_url",
            reason="EmQuant bridge URL cannot contain credentials, path, query, or fragment",
            code="invalid_arguments",
            status="invalid_arguments",
            provider="emquant_bridge",
        )
    return text


def _bridge_records(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    data = payload.get("data")
    records = data.get("records") if isinstance(data, Mapping) else None
    if not isinstance(records, list):
        return ()
    return tuple(item for item in records if isinstance(item, Mapping))


def _default_lookback_seconds(interval: str, limit: int) -> int:
    days_per_point = {"1d": 2, "1w": 9, "1mo": 35}[interval]
    return max(1, min(5000, int(limit))) * days_per_point * 24 * 60 * 60


def _invalid_request(field: str, reason: str, *, provider: str) -> MarketDataValidationError:
    return MarketDataValidationError(
        field=field,
        reason=reason,
        code="invalid_arguments",
        status="invalid_arguments",
        provider=provider,
    )


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _nonnegative_int(value: Any) -> int:
    return max(0, _safe_int(value))


def _positive_int(value: Any, *, fallback: int) -> int:
    parsed = _safe_int(value)
    return parsed if parsed > 0 else max(1, int(fallback))
