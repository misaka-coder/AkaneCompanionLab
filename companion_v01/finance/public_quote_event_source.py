from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
import math
import time
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from services.market_data import (
    MarketEvent,
    MarketEventPollResult,
    MarketEventStore,
    MarketQuoteRequest,
    MarketQuoteSnapshot,
    PublicMarketProvider,
)


class QuoteQualityError(ValueError):
    pass


class FinancePublicQuoteEventSource:
    """Turn validated public quote observations into conservative push events.

    Yahoo observations are completed daily bars. AkShare ETF observations may be
    treated as intraday snapshots only after strict freshness and consistency checks.
    The first observation only creates a baseline; no event is emitted until a later
    independently fetched observation confirms it.
    """

    provider_id = "public_market"

    def __init__(
        self,
        *,
        provider: PublicMarketProvider,
        store: MarketEventStore,
        confirmation_observations: int = 2,
        instant_stale_seconds: int = 20 * 60,
        instant_move_threshold_pct: float = 1.0,
        confirmation_max_jump_pct: float = 3.0,
        maximum_absolute_change_pct: float = 35.0,
        clock=time.time,
    ) -> None:
        if not isinstance(provider, PublicMarketProvider):
            raise TypeError("FinancePublicQuoteEventSource requires PublicMarketProvider")
        self.provider = provider
        self.store = store
        self.confirmation_observations = max(2, min(5, int(confirmation_observations)))
        self.instant_stale_seconds = max(60, min(2 * 60 * 60, int(instant_stale_seconds)))
        self.instant_move_threshold_pct = max(0.1, min(20.0, float(instant_move_threshold_pct)))
        self.confirmation_max_jump_pct = max(0.1, min(20.0, float(confirmation_max_jump_pct)))
        self.maximum_absolute_change_pct = max(5.0, min(100.0, float(maximum_absolute_change_pct)))
        self._clock = clock

    def poll_market_events(self, *, limit: int = 100) -> MarketEventPollResult:
        bounded_limit = max(1, min(1000, int(limit)))
        now = max(1, int(self._clock()))
        codes = self._watchlist_codes()
        if not codes:
            return MarketEventPollResult(
                ok=True,
                status="empty",
                provider=self.provider_id,
                source=self.provider.source,
                events=(),
                reason="no_public_market_watchlist_codes",
            )

        events: list[MarketEvent] = []
        ignored_count = 0
        failure_count = 0
        for code in codes:
            if len(events) >= bounded_limit:
                break
            try:
                result = self.provider.get_quote_snapshots(MarketQuoteRequest(codes=(code,)))
            except Exception as exc:
                failure_count += 1
                self._reject(code=code, now=now, stage="upstream", reason=f"{type(exc).__name__}:{exc}")
                continue
            if result.status != "ok" or len(result.data) != 1:
                failure_count += 1
                self._reject(
                    code=code,
                    now=now,
                    stage="upstream",
                    reason=f"{result.status}:{result.reason or 'quote_snapshot_unavailable'}",
                    payload=result.to_public_dict(),
                )
                continue
            snapshot = result.data[0]
            try:
                instrument = self.provider.registry.require(code)
                payload = self._validate_snapshot(snapshot, expected_code=code, route=instrument.route, now=now)
                event = self._observe_valid_snapshot(payload, route=instrument.route, now=now)
            except QuoteQualityError as exc:
                ignored_count += 1
                self._reject(
                    code=code,
                    now=now,
                    stage="quality_gate",
                    reason=str(exc),
                    payload=snapshot.to_public_dict(),
                )
                continue
            except Exception as exc:
                failure_count += 1
                self._reject(
                    code=code,
                    now=now,
                    stage="registry",
                    reason=f"{type(exc).__name__}:{exc}",
                    payload=snapshot.to_public_dict(),
                )
                continue
            if event is None:
                ignored_count += 1
            else:
                events.append(event)

        if failure_count == len(codes):
            return MarketEventPollResult(
                ok=False,
                status="unavailable",
                provider=self.provider_id,
                source=self.provider.source,
                events=(),
                raw_event_count=len(codes),
                ignored_count=ignored_count,
                reason="all_public_quote_routes_unavailable",
            )
        return MarketEventPollResult(
            ok=True,
            status="ok" if events else "empty",
            provider=self.provider_id,
            source=self.provider.source,
            events=tuple(events),
            raw_event_count=len(codes),
            ignored_count=ignored_count + failure_count,
            reason="partial_public_quote_failure" if failure_count else "",
        )

    def _watchlist_codes(self) -> tuple[str, ...]:
        ranked: dict[str, float] = {}
        for subscription in self.store.list_subscriptions(enabled=True):
            if subscription.finance_mode != "push":
                continue
            for item in self.store.list_watchlist(subscription.subscription_id):
                if item.provider != self.provider_id:
                    continue
                ranked[item.code] = max(ranked.get(item.code, 0.0), float(item.priority))
        return tuple(code for code, _priority in sorted(ranked.items(), key=lambda item: (-item[1], item[0])))

    def _validate_snapshot(
        self,
        snapshot: MarketQuoteSnapshot,
        *,
        expected_code: str,
        route: str,
        now: int,
    ) -> dict[str, Any]:
        if snapshot.provider != self.provider_id:
            raise QuoteQualityError("provider_mismatch")
        if snapshot.code != expected_code:
            raise QuoteQualityError("code_mismatch")
        if snapshot.as_of <= 0:
            raise QuoteQualityError("invalid_as_of")
        try:
            zone = ZoneInfo(snapshot.timezone)
        except ZoneInfoNotFoundError as exc:
            raise QuoteQualityError("invalid_timezone") from exc
        if snapshot.as_of > now + 5 * 60:
            raise QuoteQualityError("future_as_of")
        provenance = snapshot.provenance
        if provenance is None:
            raise QuoteQualityError("missing_provenance")
        if provenance.exchange_timezone != snapshot.timezone:
            raise QuoteQualityError("provenance_timezone_mismatch")
        if provenance.fetched_at <= 0 or provenance.fetched_at > now + 2 * 60:
            raise QuoteQualityError("invalid_fetched_at")
        if provenance.data_quality not in {"aggregated", "public_web"}:
            raise QuoteQualityError("unsupported_data_quality")
        if not provenance.source.strip() or not provenance.vendor_symbol.strip():
            raise QuoteQualityError("incomplete_provenance")

        last = _positive_number(snapshot.last, field="last")
        previous_close = _positive_number(snapshot.previous_close, field="previous_close")
        open_value = _optional_positive_number(snapshot.open, field="open")
        high = _optional_positive_number(snapshot.high, field="high")
        low = _optional_positive_number(snapshot.low, field="low")
        volume = _optional_nonnegative_number(snapshot.volume, field="volume")
        amount = _optional_nonnegative_number(snapshot.amount, field="amount")
        known_for_high = tuple(value for value in (open_value, low, last) if value is not None)
        if high is not None and known_for_high and high + _price_tolerance(high) < max(known_for_high):
            raise QuoteQualityError("invalid_ohlc_high")
        known_for_low = tuple(value for value in (open_value, high, last) if value is not None)
        if low is not None and known_for_low and low - _price_tolerance(low) > min(known_for_low):
            raise QuoteQualityError("invalid_ohlc_low")

        expected_change = last - previous_close
        expected_change_pct = expected_change / previous_close * 100.0
        _require_close(snapshot.change, expected_change, field="change")
        _require_close(snapshot.change_pct, expected_change_pct, field="change_pct")
        if abs(expected_change_pct) > self.maximum_absolute_change_pct:
            raise QuoteQualityError("absolute_change_outlier")

        trading_date = str(snapshot.trading_date or "").strip()
        if route == "yahoo":
            if snapshot.time_semantics != "trading_date" or provenance.delay_kind != "end_of_day":
                raise QuoteQualityError("yahoo_snapshot_not_completed_daily_bar")
            try:
                parsed_date = date.fromisoformat(trading_date)
            except ValueError as exc:
                raise QuoteQualityError("invalid_trading_date") from exc
            if datetime.fromtimestamp(snapshot.as_of, tz=zone).date() != parsed_date:
                raise QuoteQualityError("trading_date_as_of_mismatch")
        elif route == "akshare_etf":
            if snapshot.time_semantics != "instant":
                raise QuoteQualityError("akshare_snapshot_not_instant")
            try:
                parsed_date = date.fromisoformat(trading_date)
            except ValueError as exc:
                raise QuoteQualityError("invalid_trading_date") from exc
            observation_time = datetime.fromtimestamp(snapshot.as_of, tz=zone)
            current_time = datetime.fromtimestamp(now, tz=zone)
            if observation_time.date() != parsed_date:
                raise QuoteQualityError("instant_trading_date_mismatch")
            if current_time.date() != parsed_date:
                raise QuoteQualityError("instant_not_current_trading_date")
            if not _is_a_share_continuous_session(observation_time) or not _is_a_share_continuous_session(current_time):
                raise QuoteQualityError("outside_supported_intraday_session")
            if now - snapshot.as_of > self.instant_stale_seconds:
                raise QuoteQualityError("stale_instant_snapshot")
        else:
            raise QuoteQualityError("unsupported_public_route")

        payload = snapshot.to_public_dict()
        payload.update(
            {
                "last": last,
                "previous_close": previous_close,
                "open": open_value,
                "high": high,
                "low": low,
                "volume": volume,
                "amount": amount,
                "change": expected_change,
                "change_pct": expected_change_pct,
            }
        )
        return payload

    def _observe_valid_snapshot(self, payload: dict[str, Any], *, route: str, now: int) -> MarketEvent | None:
        code = str(payload["code"])
        provenance = dict(payload.get("provenance") or {})
        fetched_at = int(provenance.get("fetched_at") or 0)
        baseline = self.store.get_quote_baseline(provider=self.provider_id, code=code)
        if baseline is not None and fetched_at <= baseline.last_fetch_at:
            return None

        confirmed = dict(baseline.confirmed_snapshot) if baseline is not None else {}
        candidate = dict(baseline.candidate_snapshot) if baseline is not None else {}
        candidate_count = int(baseline.candidate_count) if baseline is not None else 0
        last_event_key = baseline.last_event_key if baseline is not None else ""

        if confirmed and int(payload["as_of"]) < int(confirmed.get("as_of") or 0):
            raise QuoteQualityError("timestamp_regression")
        if route == "yahoo" and confirmed and int(payload["as_of"]) == int(confirmed.get("as_of") or 0):
            self._save_baseline(
                code=code,
                confirmed=confirmed,
                candidate={},
                candidate_count=0,
                fetched_at=fetched_at,
                last_event_key=last_event_key,
                now=now,
            )
            return None

        if candidate:
            consistent = self._candidate_consistent(candidate, payload, route=route)
            candidate_count = candidate_count + 1 if consistent else 1
        else:
            consistent = True
            candidate_count = 1
        candidate = payload

        if not consistent:
            self._reject(
                code=code,
                now=now,
                stage="confirmation",
                reason="candidate_observation_inconsistent",
                payload=payload,
            )
        if candidate_count < self.confirmation_observations:
            self._save_baseline(
                code=code,
                confirmed=confirmed,
                candidate=candidate,
                candidate_count=candidate_count,
                fetched_at=fetched_at,
                last_event_key=last_event_key,
                now=now,
            )
            return None

        event = None if not confirmed else self._build_event(previous=confirmed, current=payload, route=route, now=now)
        event_key = self._event_key(payload, route=route) if event is not None else last_event_key
        if event is not None and event_key == last_event_key:
            event = None
        self._save_baseline(
            code=code,
            confirmed=payload,
            candidate={},
            candidate_count=0,
            fetched_at=fetched_at,
            last_event_key=event_key,
            now=now,
        )
        return event

    def _candidate_consistent(self, previous: Mapping[str, Any], current: Mapping[str, Any], *, route: str) -> bool:
        if str(previous.get("code") or "") != str(current.get("code") or ""):
            return False
        if route == "yahoo":
            return (
                int(previous.get("as_of") or 0) == int(current.get("as_of") or 0)
                and _numbers_close(previous.get("last"), current.get("last"))
                and _numbers_close(previous.get("previous_close"), current.get("previous_close"))
                and str(previous.get("trading_date") or "") == str(current.get("trading_date") or "")
            )
        if int(current.get("as_of") or 0) < int(previous.get("as_of") or 0):
            return False
        if str(previous.get("trading_date") or "") != str(current.get("trading_date") or ""):
            return False
        previous_last = float(previous.get("last") or 0.0)
        current_last = float(current.get("last") or 0.0)
        if previous_last <= 0 or current_last <= 0:
            return False
        jump_pct = abs(current_last / previous_last - 1.0) * 100.0
        return jump_pct <= self.confirmation_max_jump_pct

    def _build_event(
        self,
        *,
        previous: Mapping[str, Any],
        current: Mapping[str, Any],
        route: str,
        now: int,
    ) -> MarketEvent | None:
        if route == "yahoo":
            if int(current["as_of"]) <= int(previous.get("as_of") or 0):
                return None
            content_type = "daily_close"
        else:
            if abs(float(current["change_pct"])) < self.instant_move_threshold_pct:
                return None
            content_type = "quote_move"
        event_key = self._event_key(current, route=route)
        title = self._event_title(current, route=route)
        facts = {
            "provider": self.provider_id,
            "code": current["code"],
            "content_type": content_type,
            "event_key": event_key,
            "as_of": current["as_of"],
            "last": current["last"],
            "previous_close": current["previous_close"],
            "change": current["change"],
            "change_pct": current["change_pct"],
            "source": dict(current.get("provenance") or {}).get("source"),
        }
        material = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        raw_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
        event_id = f"public_quote:{hashlib.sha256(event_key.encode('utf-8')).hexdigest()[:32]}"
        change_pct = float(current["change_pct"])
        labels = [
            "validated_quote",
            "deterministic_calculation",
            "completed_daily_bar" if route == "yahoo" else "public_intraday_snapshot",
        ]
        if abs(change_pct) >= 3.0:
            labels.append("move_ge_3pct")
        if abs(change_pct) >= 5.0:
            labels.append("move_ge_5pct")
        provenance = dict(current.get("provenance") or {})
        return MarketEvent(
            provider=self.provider_id,
            event_id=event_id,
            published_at=now,
            produced_at=int(provenance.get("fetched_at") or now),
            received_at=now,
            code=str(current["code"]),
            content_type=content_type,
            title=title,
            source=str(provenance.get("source") or self.provider.source),
            url="",
            sentiment="positive" if change_pct > 0 else "negative" if change_pct < 0 else "neutral",
            labels=tuple(labels),
            sector_code="",
            raw_hash=raw_hash,
        )

    def _event_key(self, payload: Mapping[str, Any], *, route: str) -> str:
        code = str(payload.get("code") or "")
        trading_date = str(payload.get("trading_date") or "")
        if route == "yahoo":
            return f"{code}|daily_close|{trading_date}"
        change_pct = float(payload.get("change_pct") or 0.0)
        direction = "up" if change_pct > 0 else "down" if change_pct < 0 else "flat"
        return f"{code}|quote_move|{trading_date}|{direction}|{_move_bucket(abs(change_pct))}"

    @staticmethod
    def _event_title(payload: Mapping[str, Any], *, route: str) -> str:
        as_of = datetime.fromtimestamp(int(payload["as_of"]), tz=ZoneInfo(str(payload["timezone"]))).isoformat()
        change_pct = float(payload["change_pct"])
        verb = "上涨" if change_pct > 0 else "下跌" if change_pct < 0 else "持平"
        semantics = "最近完成交易日收盘" if route == "yahoo" else "公开聚合盘中快照"
        return (
            f"{payload['code']} {semantics}：{verb}{abs(change_pct):.2f}%，"
            f"最新价 {float(payload['last']):.6g}，昨收 {float(payload['previous_close']):.6g}，"
            f"数据时间 {as_of}"
        )

    def _save_baseline(
        self,
        *,
        code: str,
        confirmed: Mapping[str, Any],
        candidate: Mapping[str, Any],
        candidate_count: int,
        fetched_at: int,
        last_event_key: str,
        now: int,
    ) -> None:
        self.store.upsert_quote_baseline(
            provider=self.provider_id,
            code=code,
            confirmed_snapshot=confirmed,
            candidate_snapshot=candidate,
            candidate_count=candidate_count,
            last_fetch_at=fetched_at,
            last_event_key=last_event_key,
            now_ts=now,
        )

    def _reject(
        self,
        *,
        code: str,
        now: int,
        stage: str,
        reason: str,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        payload_hash = ""
        if payload:
            material = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, default=str)
            payload_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
        self.store.record_market_data_rejection(
            provider=self.provider_id,
            code=code,
            observed_at=now,
            stage=stage,
            reason=str(reason or "unknown_rejection")[:1000],
            payload_hash=payload_hash,
            now_ts=now,
        )


def _positive_number(value: Any, *, field: str) -> float:
    parsed = _finite_number(value, field=field)
    if parsed <= 0:
        raise QuoteQualityError(f"nonpositive_{field}")
    return parsed


def _optional_positive_number(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    return _positive_number(value, field=field)


def _optional_nonnegative_number(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    parsed = _finite_number(value, field=field)
    if parsed < 0:
        raise QuoteQualityError(f"negative_{field}")
    return parsed


def _finite_number(value: Any, *, field: str) -> float:
    if value is None or isinstance(value, bool):
        raise QuoteQualityError(f"missing_{field}")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise QuoteQualityError(f"invalid_{field}") from exc
    if not math.isfinite(parsed):
        raise QuoteQualityError(f"nonfinite_{field}")
    return parsed


def _require_close(actual: Any, expected: float, *, field: str) -> None:
    parsed = _finite_number(actual, field=field)
    tolerance = max(1e-8, abs(expected) * 1e-5)
    if abs(parsed - expected) > tolerance:
        raise QuoteQualityError(f"inconsistent_{field}")


def _numbers_close(left: Any, right: Any) -> bool:
    try:
        left_value = float(left)
        right_value = float(right)
    except (TypeError, ValueError):
        return False
    return math.isclose(left_value, right_value, rel_tol=1e-5, abs_tol=1e-8)


def _price_tolerance(value: float) -> float:
    return max(1e-8, abs(float(value)) * 1e-7)


def _move_bucket(value: float) -> str:
    for threshold in (10.0, 8.0, 5.0, 3.0, 2.0, 1.0):
        if value >= threshold:
            return f"ge_{threshold:g}pct"
    return "lt_1pct"


def _is_a_share_continuous_session(value: datetime) -> bool:
    if value.weekday() >= 5:
        return False
    minutes = value.hour * 60 + value.minute
    return (9 * 60 + 30) <= minutes <= (11 * 60 + 30) or (13 * 60) <= minutes <= (15 * 60)


__all__ = ["FinancePublicQuoteEventSource", "QuoteQualityError"]
