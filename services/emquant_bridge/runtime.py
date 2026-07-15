from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from .error_codes import classify_error_code
from .normalizers import (
    extract_choice_news_records,
    extract_choice_quote_records,
    extract_choice_series_records,
    result_error_code,
    result_error_reason,
    result_serial_id,
    snapshot_emquant_data,
)
from .sdk_loader import EmQuantSDKLoader, SDK_FUNCTION_NAMES
from .subscription_manager import EmQuantSubscriptionManager
from .types import (
    BridgeCallbackEvent,
    BridgeCapabilityState,
    BridgeHealth,
    BridgeResult,
    BridgeSubscriptionState,
)
from .validation import EmQuantValidationError, normalize_emquant_code


class EmQuantBridgeRuntime:
    def __init__(
        self,
        *,
        enabled: bool = False,
        api_root: str | Path | None = None,
        sdk: Any | None = None,
        sdk_loader: EmQuantSDKLoader | None = None,
        subscription_state_path: str | Path | None = None,
        provider_id: str = "choice_emquant",
        source_name: str = "Choice EmQuant",
        callback_queue_maxsize: int = 1000,
        http_timeout_seconds: int = 15,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.enabled = bool(enabled)
        self.api_root = str(api_root or "").strip()
        self.provider_id = str(provider_id or "choice_emquant").strip() or "choice_emquant"
        self.source_name = str(source_name or "Choice EmQuant").strip() or "Choice EmQuant"
        self._sdk = sdk
        self._loader = sdk_loader or EmQuantSDKLoader()
        self._clock = clock
        self.http_timeout_seconds = max(1, min(60, int(http_timeout_seconds)))
        self._lock = threading.RLock()
        self._queue: queue.Queue[BridgeCallbackEvent] = queue.Queue(maxsize=max(1, int(callback_queue_maxsize)))
        self._subscriptions = EmQuantSubscriptionManager(
            state_path=subscription_state_path,
            clock=clock,
        )
        self._capabilities: dict[str, BridgeCapabilityState] = {}
        self._logged_in = False
        self._status = "stopped" if self.enabled else "disabled"
        if self.enabled and self._sdk is None and not self.api_root:
            self._status = "missing_config"
        self._last_error_code = 0
        self._last_error_reason = ""
        self._last_news_at = 0
        self._last_quote_at = 0
        self._quota_status: dict[str, Any] = {}
        self._dropped_callback_count = 0
        if self._sdk is not None:
            self._probe_present_capabilities(self._sdk)

    def start(self) -> BridgeResult:
        with self._lock:
            if not self.enabled:
                self._status = "disabled"
                return BridgeResult(ok=False, status="missing_config", reason="EmQuant bridge is disabled")
            if self._logged_in:
                return BridgeResult(ok=True, status="ok", data=self.health().to_public_dict())
            if self._sdk is None:
                load_result = self._loader.load(self.api_root)
                if not load_result.ok:
                    self._status = load_result.status
                    self._set_error(0, load_result.reason)
                    return BridgeResult(
                        ok=False,
                        status=load_result.status,
                        reason=load_result.reason,
                        data=load_result.to_public_dict(),
                    )
                self._sdk = load_result.sdk
                self._probe_present_capabilities(self._sdk)
            start_function = getattr(self._sdk, "start", None)
            if not callable(start_function):
                self._status = "invalid_sdk"
                self._set_error(0, "SDK start function is unavailable")
                return BridgeResult(ok=False, status="invalid_sdk", reason=self._last_error_reason)

            self._status = "starting"
            start_options = f"ForceLogin=0,RecordLoginInfo=0,HTTPTimeout={self.http_timeout_seconds}"
            try:
                result = start_function(start_options, self._log_callback, self._main_callback)
            except Exception as exc:
                self._status = "invalid_sdk"
                self._set_error(0, f"SDK start failed: {type(exc).__name__}")
                self._update_capability("start", error_code=-1, reason=self._last_error_reason)
                return BridgeResult(ok=False, status="invalid_sdk", reason=self._last_error_reason)
            error_code = result_error_code(result)
            error_reason = result_error_reason(result)
            self._update_capability("start", error_code=error_code, reason=error_reason)
            if error_code != 0:
                classification = classify_error_code(error_code)
                self._status = classification.status
                self._logged_in = False
                self._set_error(error_code, error_reason or classification.status)
                return BridgeResult(
                    ok=False,
                    status=classification.status,
                    reason=self._last_error_reason,
                    error_code=error_code,
                )

            self._logged_in = True
            self._status = "ready"
            self._set_error(0, "")
            quota_result = self._query_data_statistics_locked()
            restored = self._subscriptions.restore_enabled(
                self._sdk,
                news_callback=self._news_callback,
                quote_callback=self._quote_callback,
                now_ts=self._now(),
            )
            for state in restored:
                self._update_capability(
                    "cnq" if state.spec.kind == "news" else "csq",
                    error_code=state.last_error_code,
                    reason=state.last_error_reason,
                    operational_override=state.active,
                )
            subscription_failures = [state for state in restored if not state.active]
            if not quota_result.ok or subscription_failures:
                self._status = "degraded"
            return BridgeResult(
                ok=self._status in {"ready", "degraded"},
                status="ok" if self._status == "ready" else self._status,
                data={
                    "health": self.health().to_public_dict(),
                    "restored_subscriptions": [state.to_public_dict() for state in restored],
                },
            )

    def stop(self) -> BridgeResult:
        with self._lock:
            if self._sdk is None:
                self._logged_in = False
                self._status = "stopped" if self.enabled else "disabled"
                return BridgeResult(ok=True, status="stopped")
            cancelled = self._subscriptions.cancel_all(self._sdk, now_ts=self._now())
            stop_function = getattr(self._sdk, "stop", None)
            if not callable(stop_function):
                self._logged_in = False
                self._status = "invalid_sdk"
                return BridgeResult(ok=False, status="invalid_sdk", reason="SDK stop function is unavailable")
            try:
                result = stop_function()
            except Exception as exc:
                self._logged_in = False
                self._status = "disconnected"
                self._set_error(0, f"SDK stop failed: {type(exc).__name__}")
                return BridgeResult(ok=False, status="disconnected", reason=self._last_error_reason)
            error_code = result_error_code(result)
            error_reason = result_error_reason(result)
            self._update_capability("stop", error_code=error_code, reason=error_reason)
            self._logged_in = False
            if error_code != 0:
                classification = classify_error_code(error_code)
                self._status = classification.status
                self._set_error(error_code, error_reason or classification.status)
                return BridgeResult(
                    ok=False,
                    status=classification.status,
                    reason=self._last_error_reason,
                    error_code=error_code,
                )
            self._status = "stopped"
            self._set_error(0, "")
            return BridgeResult(
                ok=True,
                status="stopped",
                data={"cancelled_subscriptions": [state.to_public_dict() for state in cancelled]},
            )

    def health(self) -> BridgeHealth:
        with self._lock:
            states = self._subscriptions.list_states()
            news_count = sum(1 for state in states if self._logged_in and state.active and state.spec.kind == "news")
            quote_count = sum(1 for state in states if self._logged_in and state.active and state.spec.kind == "quote")
            quota_status = dict(self._quota_status)
            quota_status["subscription_state"] = self._subscriptions.persistence_status()
            return BridgeHealth(
                ok=self._status in {"ready", "degraded"},
                status=self._status,
                provider=self.provider_id,
                source=self.source_name,
                checked_at=self._now(),
                logged_in=self._logged_in,
                news_subscription_count=news_count,
                quote_subscription_count=quote_count,
                last_news_at=self._last_news_at,
                last_quote_at=self._last_quote_at,
                last_error_code=self._last_error_code,
                last_error_reason=self._last_error_reason,
                quota_status=quota_status,
                capabilities=self._capabilities,
                queue_size=self._queue.qsize(),
                dropped_callback_count=self._dropped_callback_count,
            )

    def query_news(
        self,
        *,
        codes: Iterable[str],
        content_types: Iterable[str],
        mode: int = 2,
        options: str = "",
    ) -> BridgeResult:
        clean_codes = _normalize_codes(codes)
        clean_types = _normalize_fields(content_types, field="content_types")
        if isinstance(mode, bool) or int(mode) not in {1, 2}:
            raise _invalid_argument("mode", "mode must be 1 or 2")
        clean_options = _normalize_options(options)
        return self._call_query(
            "cfn",
            ",".join(clean_codes),
            ",".join(clean_types),
            int(mode),
            clean_options,
            data_builder=lambda result: {
                "records": list(extract_choice_news_records(result)),
                "sdk": snapshot_emquant_data(result),
            },
        )

    def quote_snapshot(
        self,
        *,
        codes: Iterable[str],
        indicators: Iterable[str] = ("TIME", "PRECLOSE", "OPEN", "HIGH", "LOW", "NOW", "VOLUME", "AMOUNT"),
        options: str = "Ispandas=0",
    ) -> BridgeResult:
        clean_codes = _normalize_codes(codes)
        clean_indicators = _normalize_fields(indicators, field="indicators")
        clean_options = _normalize_options(options)
        return self._call_query(
            "csqsnapshot",
            ",".join(clean_codes),
            ",".join(clean_indicators),
            clean_options,
            data_builder=lambda result: {
                "records": list(extract_choice_quote_records(result)),
                "sdk": snapshot_emquant_data(result),
            },
        )

    def price_series(
        self,
        *,
        codes: Iterable[str],
        indicators: Iterable[str] = ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "AMOUNT"),
        start_date: str,
        end_date: str,
        options: str = "Period=1,AdjustFlag=1,Order=1,RowIndex=1,Ispandas=0",
    ) -> BridgeResult:
        clean_codes = _normalize_codes(codes)
        clean_indicators = _normalize_fields(indicators, field="indicators")
        clean_start = _normalize_iso_date(start_date, field="start_date")
        clean_end = _normalize_iso_date(end_date, field="end_date")
        if clean_start > clean_end:
            raise _invalid_argument("start_date", "start_date cannot be later than end_date")
        clean_options = _normalize_options(options)
        return self._call_query(
            "csd",
            ",".join(clean_codes),
            ",".join(clean_indicators),
            clean_start,
            clean_end,
            clean_options,
            data_builder=lambda result: {
                "records": list(extract_choice_series_records(result)),
                "sdk": snapshot_emquant_data(result),
            },
        )

    def data_statistics(self) -> BridgeResult:
        with self._lock:
            return self._query_data_statistics_locked()

    def register_subscription(
        self,
        *,
        subscription_id: str,
        kind: str,
        codes: Iterable[str],
        fields: Iterable[str],
        options: str = "",
        enabled: bool = True,
    ) -> BridgeSubscriptionState:
        with self._lock:
            state = self._subscriptions.register(
                subscription_id=subscription_id,
                kind=kind,
                codes=codes,
                fields=fields,
                options=options,
                enabled=enabled,
                now_ts=self._now(),
            )
            if enabled and self._logged_in and self._sdk is not None:
                activated = self._subscriptions.activate(
                    state.spec.subscription_id,
                    self._sdk,
                    news_callback=self._news_callback,
                    quote_callback=self._quote_callback,
                    now_ts=self._now(),
                )
                if activated is not None:
                    self._update_capability(
                        "cnq" if activated.spec.kind == "news" else "csq",
                        error_code=activated.last_error_code,
                        reason=activated.last_error_reason,
                        operational_override=activated.active,
                    )
                    if not activated.active:
                        self._status = "degraded"
                        self._set_error(
                            activated.last_error_code,
                            activated.last_error_reason or activated.status,
                        )
                    return activated
            return state

    def disable_subscription(self, subscription_id: str) -> BridgeSubscriptionState | None:
        with self._lock:
            state = self._subscriptions.disable(
                subscription_id,
                sdk=self._sdk if self._logged_in else None,
                now_ts=self._now(),
            )
            if state is not None and state.status == "cancel_failed":
                self._status = "degraded"
                self._set_error(state.last_error_code, state.last_error_reason or "cancel_failed")
            return state

    def list_subscriptions(self) -> tuple[BridgeSubscriptionState, ...]:
        return self._subscriptions.list_states()

    def poll_events(self, *, limit: int = 100) -> tuple[BridgeCallbackEvent, ...]:
        if isinstance(limit, bool):
            raise _invalid_argument("limit", "limit must be an integer")
        clean_limit = max(1, min(1000, int(limit)))
        events: list[BridgeCallbackEvent] = []
        for _ in range(clean_limit):
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return tuple(events)

    def _call_query(
        self,
        function_name: str,
        *args: Any,
        data_builder: Callable[[Any], Any],
    ) -> BridgeResult:
        with self._lock:
            if not self._logged_in or self._sdk is None:
                return BridgeResult(ok=False, status="disconnected", reason="EmQuant bridge is not started")
            function = getattr(self._sdk, function_name, None)
            if not callable(function):
                self._update_capability(function_name, error_code=-1, reason="SDK function is unavailable")
                return BridgeResult(ok=False, status="unavailable", reason="SDK function is unavailable")
            try:
                result = function(*args)
            except Exception as exc:
                reason = f"SDK {function_name} failed: {type(exc).__name__}"
                self._update_capability(function_name, error_code=-1, reason=reason)
                self._status = "degraded"
                self._set_error(0, reason)
                return BridgeResult(ok=False, status="unavailable", reason=reason)
            error_code = result_error_code(result)
            error_reason = result_error_reason(result)
            classification = classify_error_code(error_code)
            self._update_capability(function_name, error_code=error_code, reason=error_reason)
            if error_code != 0:
                self._status = classification.status
                self._set_error(error_code, error_reason or classification.status)
                return BridgeResult(
                    ok=False,
                    status=classification.status,
                    reason=self._last_error_reason,
                    error_code=error_code,
                )
            if self._status not in {"starting", "stopped"}:
                self._status = "ready"
            self._set_error(0, "")
            return BridgeResult(ok=True, status="ok", data=data_builder(result))

    def _query_data_statistics_locked(self) -> BridgeResult:
        if not self._logged_in or self._sdk is None:
            return BridgeResult(ok=False, status="disconnected", reason="EmQuant bridge is not started")
        function = getattr(self._sdk, "datastatistics", None)
        if not callable(function):
            self._quota_status = {"status": "unavailable"}
            self._update_capability("datastatistics", error_code=-1, reason="SDK function is unavailable")
            return BridgeResult(ok=False, status="unavailable", reason="SDK function is unavailable")
        try:
            result = function(
                "",
                "FUNCENAME,FUNCNAME,SECUTYPE,PERIOD,STARTDATE,ENDDATE,THRESHOLD,USEDDATA,USEDRATIO,AVAILABEDATA",
                "Ispandas=0",
            )
        except Exception as exc:
            reason = f"SDK datastatistics failed: {type(exc).__name__}"
            self._quota_status = {"status": "unavailable", "reason": reason}
            self._update_capability("datastatistics", error_code=-1, reason=reason)
            return BridgeResult(ok=False, status="unavailable", reason=reason)
        error_code = result_error_code(result)
        error_reason = result_error_reason(result)
        classification = classify_error_code(error_code)
        self._update_capability("datastatistics", error_code=error_code, reason=error_reason)
        snapshot = snapshot_emquant_data(result)
        if error_code != 0:
            self._quota_status = {
                "status": classification.status,
                "error_code": error_code,
                "reason": error_reason or classification.status,
            }
            self._set_error(error_code, error_reason or classification.status)
            return BridgeResult(
                ok=False,
                status=classification.status,
                reason=error_reason or classification.status,
                error_code=error_code,
            )
        self._quota_status = {"status": "ok", "snapshot": snapshot}
        return BridgeResult(ok=True, status="ok", data=snapshot)

    def _probe_present_capabilities(self, sdk: Any) -> None:
        checked_at = self._now()
        self._capabilities = {
            name: BridgeCapabilityState(
                name=name,
                present=callable(getattr(sdk, name, None)),
                last_checked_at=checked_at,
            )
            for name in SDK_FUNCTION_NAMES
        }

    def _update_capability(
        self,
        function_name: str,
        *,
        error_code: int,
        reason: str,
        operational_override: bool | None = None,
    ) -> None:
        previous = self._capabilities.get(function_name)
        present = callable(getattr(self._sdk, function_name, None)) if self._sdk is not None else False
        classification = classify_error_code(error_code)
        operational = classification.operational if operational_override is None else operational_override
        self._capabilities[function_name] = BridgeCapabilityState(
            name=function_name,
            present=present,
            authorized=classification.authorized if error_code >= 0 else (previous.authorized if previous else None),
            quota_available=(
                classification.quota_available if error_code >= 0 else (previous.quota_available if previous else None)
            ),
            operational=operational if present else False,
            last_checked_at=self._now(),
            reason=str(reason or "")[:2000],
        )

    def _news_callback(self, result: Any) -> int:
        received_at = self._now()
        error_code = result_error_code(result)
        error_reason = result_error_reason(result)
        event = BridgeCallbackEvent(
            kind="news",
            serial_id=result_serial_id(result),
            received_at=received_at,
            error_code=error_code,
            error_reason=error_reason,
            payload={
                "records": list(extract_choice_news_records(result)),
                "sdk": snapshot_emquant_data(result, include_data=False),
            },
        )
        self._enqueue_callback(event)
        with self._lock:
            self._last_news_at = received_at
            self._subscriptions.mark_event(event.serial_id, received_at=received_at)
            self._record_callback_capability("cnq", error_code=error_code, reason=error_reason)
        return 1

    def _quote_callback(self, result: Any) -> int:
        received_at = self._now()
        error_code = result_error_code(result)
        error_reason = result_error_reason(result)
        event = BridgeCallbackEvent(
            kind="quote",
            serial_id=result_serial_id(result),
            received_at=received_at,
            error_code=error_code,
            error_reason=error_reason,
            payload={
                "records": list(extract_choice_quote_records(result)),
                "sdk": snapshot_emquant_data(result, include_data=False),
            },
        )
        self._enqueue_callback(event)
        with self._lock:
            self._last_quote_at = received_at
            self._subscriptions.mark_event(event.serial_id, received_at=received_at)
            self._record_callback_capability("csq", error_code=error_code, reason=error_reason)
        return 1

    def _record_callback_capability(self, function_name: str, *, error_code: int, reason: str) -> None:
        self._update_capability(function_name, error_code=error_code, reason=reason)
        if error_code == 0:
            return
        classification = classify_error_code(error_code)
        self._status = classification.status
        if classification.status == "disconnected":
            self._logged_in = False
        self._set_error(error_code, reason or classification.status)

    def _main_callback(self, result: Any) -> int:
        error_code = result_error_code(result)
        error_reason = result_error_reason(result)
        if error_code == 0:
            return 1
        classification = classify_error_code(error_code)
        event = BridgeCallbackEvent(
            kind="system",
            serial_id=0,
            received_at=self._now(),
            error_code=error_code,
            error_reason=error_reason or classification.status,
            payload=snapshot_emquant_data(result),
        )
        self._enqueue_callback(event)
        with self._lock:
            self._status = classification.status
            if classification.status == "disconnected":
                self._logged_in = False
            self._set_error(error_code, error_reason or classification.status)
        return 1

    @staticmethod
    def _log_callback(_message: Any) -> int:
        return 1

    def _enqueue_callback(self, event: BridgeCallbackEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            with self._lock:
                self._dropped_callback_count += 1
                self._status = "degraded"
                self._set_error(0, "callback queue is full")

    def _set_error(self, error_code: int, reason: str) -> None:
        self._last_error_code = int(error_code or 0)
        self._last_error_reason = str(reason or "")[:2000]

    def _now(self) -> int:
        return max(1, int(self._clock()))


def _normalize_codes(values: Iterable[Any]) -> tuple[str, ...]:
    if isinstance(values, str):
        raw_values = [values]
    else:
        try:
            raw_values = list(values)
        except TypeError as exc:
            raise _invalid_argument("codes", "codes must be a list") from exc
    codes: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        code = normalize_emquant_code(
            value,
            field="codes",
            status="invalid_arguments",
            error_code="invalid_arguments",
        )
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
    if not codes:
        raise _invalid_argument("codes", "at least one code is required")
    if len(codes) > 100:
        raise _invalid_argument("codes", "at most 100 codes are allowed")
    return tuple(codes)


def _normalize_fields(values: Iterable[Any], *, field: str) -> tuple[str, ...]:
    if isinstance(values, str):
        raw_values = [values]
    else:
        try:
            raw_values = list(values)
        except TypeError as exc:
            raise _invalid_argument(field, f"{field} must be a list") from exc
    fields: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        text = str(value or "").strip()
        if not text or len(text) > 120 or not all(char.isalnum() or char in "_.-" for char in text):
            raise _invalid_argument(field, f"{field} contains an invalid value")
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        fields.append(text)
    if not fields:
        raise _invalid_argument(field, f"at least one {field} value is required")
    if len(fields) > 64:
        raise _invalid_argument(field, f"at most 64 {field} values are allowed")
    return tuple(fields)


def _normalize_options(value: Any) -> str:
    options = str(value or "").strip()
    if len(options) > 4000:
        raise _invalid_argument("options", "options are too long")
    lowered = options.lower()
    if any(key in lowered for key in ("username=", "password=", "token=", "account=")):
        raise _invalid_argument("options", "sensitive account options are not allowed")
    return options


def _normalize_iso_date(value: Any, *, field: str) -> str:
    from datetime import date

    text = str(value or "").strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise _invalid_argument(field, f"{field} must use YYYY-MM-DD") from exc
    return parsed.isoformat()


def _invalid_argument(field: str, reason: str) -> EmQuantValidationError:
    return EmQuantValidationError(
        field=field,
        reason=reason,
        code="invalid_arguments",
        status="invalid_arguments",
        provider="emquant_bridge",
    )
