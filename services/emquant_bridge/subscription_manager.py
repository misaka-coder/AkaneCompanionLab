from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from .error_codes import classify_error_code
from .normalizers import result_error_code, result_error_reason, result_serial_id
from .types import BridgeSubscriptionSpec, BridgeSubscriptionState
from .validation import EmQuantValidationError, normalize_emquant_code


SUBSCRIPTION_STATE_SCHEMA = "akane.emquant_bridge.subscriptions.v1"
SUBSCRIPTION_KINDS = frozenset({"news", "quote"})
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,200}$")
_SAFE_FIELD_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
_SENSITIVE_OPTION_RE = re.compile(r"(?:username|password|token|account)\s*=", re.IGNORECASE)


class EmQuantSubscriptionManager:
    def __init__(
        self,
        *,
        state_path: str | Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.state_path = Path(state_path).expanduser() if state_path else None
        self._clock = clock
        self._lock = threading.RLock()
        self._states: dict[str, BridgeSubscriptionState] = {}
        self._serial_to_subscription: dict[int, str] = {}
        self._state_error = ""
        self._load()

    def register(
        self,
        *,
        subscription_id: str,
        kind: str,
        codes: Iterable[str],
        fields: Iterable[str],
        options: str = "",
        enabled: bool = True,
        now_ts: int | None = None,
    ) -> BridgeSubscriptionState:
        now = self._now(now_ts)
        clean_id = _safe_id(subscription_id, field="subscription_id")
        clean_kind = str(kind or "").strip().lower()
        if clean_kind not in SUBSCRIPTION_KINDS:
            raise _invalid_argument("kind", "kind must be news or quote")
        clean_codes = _normalize_codes(codes)
        clean_fields = _normalize_fields(fields)
        clean_options = _normalize_options(options)
        if not isinstance(enabled, bool):
            raise _invalid_argument("enabled", "enabled must be a boolean")
        with self._lock:
            existing = self._states.get(clean_id)
            created_at = existing.spec.created_at if existing is not None else now
            spec = BridgeSubscriptionSpec(
                subscription_id=clean_id,
                kind=clean_kind,
                codes=clean_codes,
                fields=clean_fields,
                options=clean_options,
                enabled=enabled,
                created_at=created_at,
                updated_at=now,
            )
            state = BridgeSubscriptionState(spec=spec, status="registered" if enabled else "disabled")
            if existing is not None and existing.active:
                state = BridgeSubscriptionState(
                    spec=spec,
                    serial_id=existing.serial_id,
                    status=existing.status if enabled else "disabled",
                    last_error_code=existing.last_error_code,
                    last_error_reason=existing.last_error_reason,
                    last_event_at=existing.last_event_at,
                )
            self._states[clean_id] = state
            if not enabled and existing is not None and existing.serial_id:
                self._serial_to_subscription.pop(existing.serial_id, None)
            self._persist()
            return state

    def disable(
        self, subscription_id: str, *, sdk: Any | None = None, now_ts: int | None = None
    ) -> BridgeSubscriptionState | None:
        clean_id = _safe_id(subscription_id, field="subscription_id")
        now = self._now(now_ts)
        with self._lock:
            state = self._states.get(clean_id)
            if state is None:
                return None
            if sdk is not None and state.active:
                cancel_state = self._cancel_state(sdk, state, now_ts=now)
                if cancel_state.status == "cancel_failed":
                    disabled_spec = BridgeSubscriptionSpec(
                        subscription_id=state.spec.subscription_id,
                        kind=state.spec.kind,
                        codes=state.spec.codes,
                        fields=state.spec.fields,
                        options=state.spec.options,
                        enabled=False,
                        created_at=state.spec.created_at,
                        updated_at=now,
                    )
                    failed = BridgeSubscriptionState(
                        spec=disabled_spec,
                        serial_id=cancel_state.serial_id,
                        status="cancel_failed",
                        last_error_code=cancel_state.last_error_code,
                        last_error_reason=cancel_state.last_error_reason,
                        last_event_at=cancel_state.last_event_at,
                    )
                    self._states[clean_id] = failed
                    self._persist()
                    return failed
            spec = BridgeSubscriptionSpec(
                subscription_id=state.spec.subscription_id,
                kind=state.spec.kind,
                codes=state.spec.codes,
                fields=state.spec.fields,
                options=state.spec.options,
                enabled=False,
                created_at=state.spec.created_at,
                updated_at=now,
            )
            disabled = BridgeSubscriptionState(
                spec=spec,
                status="disabled",
                last_error_code=state.last_error_code,
                last_error_reason=state.last_error_reason,
                last_event_at=state.last_event_at,
            )
            self._states[clean_id] = disabled
            if state.serial_id:
                self._serial_to_subscription.pop(state.serial_id, None)
            self._persist()
            return disabled

    def restore_enabled(
        self,
        sdk: Any,
        *,
        news_callback: Callable[[Any], Any],
        quote_callback: Callable[[Any], Any],
        now_ts: int | None = None,
    ) -> tuple[BridgeSubscriptionState, ...]:
        now = self._now(now_ts)
        restored: list[BridgeSubscriptionState] = []
        with self._lock:
            self._serial_to_subscription.clear()
            for subscription_id in sorted(self._states):
                state = self._states[subscription_id]
                if not state.spec.enabled:
                    continue
                restored_state = self._activate_state(
                    sdk,
                    state.spec,
                    news_callback=news_callback,
                    quote_callback=quote_callback,
                    now_ts=now,
                )
                self._states[subscription_id] = restored_state
                if restored_state.serial_id:
                    self._serial_to_subscription[restored_state.serial_id] = subscription_id
                restored.append(restored_state)
            self._persist()
        return tuple(restored)

    def activate(
        self,
        subscription_id: str,
        sdk: Any,
        *,
        news_callback: Callable[[Any], Any],
        quote_callback: Callable[[Any], Any],
        now_ts: int | None = None,
    ) -> BridgeSubscriptionState | None:
        clean_id = _safe_id(subscription_id, field="subscription_id")
        now = self._now(now_ts)
        with self._lock:
            state = self._states.get(clean_id)
            if state is None or not state.spec.enabled:
                return state
            if state.active:
                return state
            activated = self._activate_state(
                sdk,
                state.spec,
                news_callback=news_callback,
                quote_callback=quote_callback,
                now_ts=now,
            )
            self._states[clean_id] = activated
            if activated.serial_id:
                self._serial_to_subscription[activated.serial_id] = clean_id
            self._persist()
            return activated

    def cancel_all(self, sdk: Any, *, now_ts: int | None = None) -> tuple[BridgeSubscriptionState, ...]:
        now = self._now(now_ts)
        cancelled: list[BridgeSubscriptionState] = []
        with self._lock:
            for subscription_id in sorted(self._states):
                state = self._states[subscription_id]
                if state.serial_id <= 0 or state.status not in {"active", "cancel_failed"}:
                    continue
                next_state = self._cancel_state(sdk, state, now_ts=now)
                self._states[subscription_id] = next_state
                cancelled.append(next_state)
            self._serial_to_subscription.clear()
            self._persist()
        return tuple(cancelled)

    def mark_event(self, serial_id: int, *, received_at: int | None = None) -> BridgeSubscriptionState | None:
        serial = max(0, int(serial_id or 0))
        event_at = self._now(received_at)
        with self._lock:
            subscription_id = self._serial_to_subscription.get(serial)
            if not subscription_id:
                return None
            state = self._states.get(subscription_id)
            if state is None:
                return None
            updated = BridgeSubscriptionState(
                spec=state.spec,
                serial_id=state.serial_id,
                status=state.status,
                last_error_code=state.last_error_code,
                last_error_reason=state.last_error_reason,
                last_event_at=event_at,
            )
            self._states[subscription_id] = updated
            return updated

    def list_states(self) -> tuple[BridgeSubscriptionState, ...]:
        with self._lock:
            return tuple(self._states[key] for key in sorted(self._states))

    def persistence_status(self) -> dict[str, Any]:
        with self._lock:
            if self.state_path is None:
                return {"enabled": False, "status": "disabled", "reason": ""}
            return {
                "enabled": True,
                "status": "error" if self._state_error else "ok",
                "reason": self._state_error,
            }

    def get(self, subscription_id: str) -> BridgeSubscriptionState | None:
        clean_id = _safe_id(subscription_id, field="subscription_id")
        with self._lock:
            return self._states.get(clean_id)

    def _activate_state(
        self,
        sdk: Any,
        spec: BridgeSubscriptionSpec,
        *,
        news_callback: Callable[[Any], Any],
        quote_callback: Callable[[Any], Any],
        now_ts: int,
    ) -> BridgeSubscriptionState:
        codes = ",".join(spec.codes)
        fields = ",".join(spec.fields)
        callback = news_callback if spec.kind == "news" else quote_callback
        function_name = "cnq" if spec.kind == "news" else "csq"
        function = getattr(sdk, function_name, None)
        if not callable(function):
            return BridgeSubscriptionState(
                spec=spec,
                status="unavailable",
                last_error_reason=f"SDK function {function_name} is unavailable",
            )
        result = function(
            codes,
            fields,
            spec.options,
            callback,
            {"subscription_id": spec.subscription_id},
        )
        error_code = result_error_code(result)
        error_reason = result_error_reason(result)
        serial_id = result_serial_id(result)
        classification = classify_error_code(error_code)
        if error_code == 0 and serial_id > 0:
            return BridgeSubscriptionState(
                spec=spec,
                serial_id=serial_id,
                status="active",
            )
        return BridgeSubscriptionState(
            spec=spec,
            status=classification.status,
            last_error_code=error_code,
            last_error_reason=error_reason or classification.status,
        )

    def _cancel_state(self, sdk: Any, state: BridgeSubscriptionState, *, now_ts: int) -> BridgeSubscriptionState:
        function_name = "cnqcancel" if state.spec.kind == "news" else "csqcancel"
        function = getattr(sdk, function_name, None)
        if not callable(function):
            return BridgeSubscriptionState(
                spec=state.spec,
                serial_id=state.serial_id,
                status="cancel_failed",
                last_error_reason=f"SDK function {function_name} is unavailable",
                last_event_at=state.last_event_at,
            )
        result = function(state.serial_id)
        error_code = result_error_code(result)
        error_reason = result_error_reason(result)
        if error_code == 0:
            self._serial_to_subscription.pop(state.serial_id, None)
            return BridgeSubscriptionState(
                spec=state.spec,
                status="registered" if state.spec.enabled else "disabled",
                last_event_at=state.last_event_at,
            )
        classification = classify_error_code(error_code)
        return BridgeSubscriptionState(
            spec=state.spec,
            serial_id=state.serial_id,
            status="cancel_failed",
            last_error_code=error_code,
            last_error_reason=error_reason or classification.status,
            last_event_at=state.last_event_at,
        )

    def _load(self) -> None:
        if self.state_path is None or not self.state_path.is_file():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except OSError as exc:
            self._state_error = f"subscription state read failed: {type(exc).__name__}"
            return
        except json.JSONDecodeError:
            self._state_error = "subscription state JSON is invalid"
            return
        if payload.get("schema_version") != SUBSCRIPTION_STATE_SCHEMA:
            return
        raw_items = payload.get("subscriptions")
        if not isinstance(raw_items, list):
            return
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            try:
                state = self.register(
                    subscription_id=item.get("subscription_id"),
                    kind=item.get("kind"),
                    codes=item.get("codes") or (),
                    fields=item.get("fields") or (),
                    options=item.get("options") or "",
                    enabled=bool(item.get("enabled")),
                    now_ts=int(item.get("updated_at") or item.get("created_at") or self._clock()),
                )
                created_at = int(item.get("created_at") or state.spec.created_at)
                self._states[state.spec.subscription_id] = BridgeSubscriptionState(
                    spec=BridgeSubscriptionSpec(
                        subscription_id=state.spec.subscription_id,
                        kind=state.spec.kind,
                        codes=state.spec.codes,
                        fields=state.spec.fields,
                        options=state.spec.options,
                        enabled=state.spec.enabled,
                        created_at=created_at,
                        updated_at=state.spec.updated_at,
                    ),
                    status=state.status,
                )
            except (EmQuantValidationError, TypeError, ValueError):
                continue
        self._persist()

    def _persist(self) -> None:
        if self.state_path is None:
            return
        payload = {
            "schema_version": SUBSCRIPTION_STATE_SCHEMA,
            "subscriptions": [state.spec.to_public_dict() for state in self.list_states()],
        }
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.state_path.with_suffix(f"{self.state_path.suffix}.tmp")
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            temp_path.replace(self.state_path)
            self._state_error = ""
        except OSError as exc:
            self._state_error = f"subscription state write failed: {type(exc).__name__}"
            return

    def _now(self, value: int | None) -> int:
        raw = int(self._clock()) if value is None else int(value)
        return max(1, raw)


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


def _normalize_fields(values: Iterable[Any]) -> tuple[str, ...]:
    if isinstance(values, str):
        raw_values = [values]
    else:
        try:
            raw_values = list(values)
        except TypeError as exc:
            raise _invalid_argument("fields", "fields must be a list") from exc
    fields: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        field_name = str(value or "").strip()
        if not _SAFE_FIELD_RE.fullmatch(field_name):
            raise _invalid_argument("fields", "field contains unsupported characters")
        normalized = field_name.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        fields.append(field_name)
    if not fields:
        raise _invalid_argument("fields", "at least one field is required")
    if len(fields) > 64:
        raise _invalid_argument("fields", "at most 64 fields are allowed")
    return tuple(fields)


def _normalize_options(value: Any) -> str:
    options = str(value or "").strip()
    if len(options) > 2000:
        raise _invalid_argument("options", "options are too long")
    if _SENSITIVE_OPTION_RE.search(options):
        raise _invalid_argument("options", "sensitive account options are not allowed")
    return options


def _safe_id(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_ID_RE.fullmatch(text):
        raise _invalid_argument(field, "value contains unsupported characters or has an invalid length")
    return text


def _invalid_argument(field: str, reason: str) -> EmQuantValidationError:
    return EmQuantValidationError(
        field=field,
        reason=reason,
        code="invalid_arguments",
        status="invalid_arguments",
        provider="emquant_bridge",
    )
