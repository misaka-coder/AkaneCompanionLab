from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class BridgeCapabilityState:
    name: str
    present: bool
    authorized: bool | None = None
    quota_available: bool | None = None
    operational: bool | None = None
    last_checked_at: int = 0
    reason: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "present": bool(self.present),
            "authorized": self.authorized,
            "quota_available": self.quota_available,
            "operational": self.operational,
            "last_checked_at": max(0, int(self.last_checked_at)),
            "reason": str(self.reason or ""),
        }


@dataclass(frozen=True)
class BridgeResult:
    ok: bool
    status: str
    reason: str = ""
    error_code: int = 0
    data: Any = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "status": str(self.status or ""),
            "reason": str(self.reason or ""),
            "error_code": int(self.error_code or 0),
            "data": _serialize_public(self.data),
        }


@dataclass(frozen=True)
class BridgeCallbackEvent:
    kind: str
    serial_id: int
    received_at: int
    error_code: int
    error_reason: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload or {})))

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "serial_id": self.serial_id,
            "received_at": self.received_at,
            "error_code": self.error_code,
            "error_reason": self.error_reason,
            "payload": _serialize_public(self.payload),
        }


@dataclass(frozen=True)
class BridgeSubscriptionSpec:
    subscription_id: str
    kind: str
    codes: tuple[str, ...]
    fields: tuple[str, ...]
    options: str = ""
    enabled: bool = True
    created_at: int = 0
    updated_at: int = 0

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "subscription_id": self.subscription_id,
            "kind": self.kind,
            "codes": list(self.codes),
            "fields": list(self.fields),
            "options": self.options,
            "enabled": bool(self.enabled),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class BridgeSubscriptionState:
    spec: BridgeSubscriptionSpec
    serial_id: int = 0
    status: str = "registered"
    last_error_code: int = 0
    last_error_reason: str = ""
    last_event_at: int = 0

    @property
    def active(self) -> bool:
        return self.status == "active" and self.serial_id > 0

    def to_public_dict(self) -> dict[str, Any]:
        payload = self.spec.to_public_dict()
        payload.update(
            {
                "serial_id": self.serial_id,
                "status": self.status,
                "last_error_code": self.last_error_code,
                "last_error_reason": self.last_error_reason,
                "last_event_at": self.last_event_at,
            }
        )
        return payload


@dataclass(frozen=True)
class BridgeHealth:
    ok: bool
    status: str
    provider: str
    source: str
    checked_at: int
    logged_in: bool
    news_subscription_count: int = 0
    quote_subscription_count: int = 0
    last_news_at: int = 0
    last_quote_at: int = 0
    last_error_code: int = 0
    last_error_reason: str = ""
    quota_status: Mapping[str, Any] = field(default_factory=dict)
    capabilities: Mapping[str, BridgeCapabilityState] = field(default_factory=dict)
    queue_size: int = 0
    dropped_callback_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "quota_status", MappingProxyType(dict(self.quota_status or {})))
        object.__setattr__(self, "capabilities", MappingProxyType(dict(self.capabilities or {})))

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "status": self.status,
            "provider": self.provider,
            "source": self.source,
            "checked_at": self.checked_at,
            "logged_in": bool(self.logged_in),
            "news_subscription_count": max(0, int(self.news_subscription_count)),
            "quote_subscription_count": max(0, int(self.quote_subscription_count)),
            "last_news_at": max(0, int(self.last_news_at)),
            "last_quote_at": max(0, int(self.last_quote_at)),
            "last_error_code": int(self.last_error_code),
            "last_error_reason": str(self.last_error_reason or ""),
            "quota_status": _serialize_public(self.quota_status),
            "capabilities": {
                name: capability.to_public_dict() for name, capability in sorted(self.capabilities.items())
            },
            "queue_size": max(0, int(self.queue_size)),
            "dropped_callback_count": max(0, int(self.dropped_callback_count)),
        }


def _serialize_public(value: Any) -> Any:
    if hasattr(value, "to_public_dict") and callable(value.to_public_dict):
        return value.to_public_dict()
    if isinstance(value, Mapping):
        return {str(key): _serialize_public(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_serialize_public(item) for item in value]
    return value
