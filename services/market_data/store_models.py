from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from .types import MarketEvent


@dataclass(frozen=True)
class StoredMarketEvent:
    event: MarketEvent
    cluster_id: str
    status: str
    revision: int
    created_at: int
    updated_at: int

    def to_public_dict(self) -> dict[str, Any]:
        payload = self.event.to_public_dict()
        payload.update(
            {
                "cluster_id": self.cluster_id,
                "status": self.status,
                "revision": self.revision,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            }
        )
        return payload


@dataclass(frozen=True)
class EventUpsertResult:
    status: str
    record: StoredMarketEvent
    canonical_event_id: str
    duplicate_of: str = ""

    @property
    def inserted(self) -> bool:
        return self.status == "inserted"

    @property
    def updated(self) -> bool:
        return self.status == "updated"

    @property
    def duplicate(self) -> bool:
        return self.status in {"duplicate_event_id", "duplicate_raw_hash"}

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "canonical_event_id": self.canonical_event_id,
            "duplicate_of": self.duplicate_of,
            "record": self.record.to_public_dict(),
        }


@dataclass(frozen=True)
class FinanceSubscription:
    subscription_id: str
    client: str
    target_id: str
    is_group: bool
    session_id: str
    profile_user_id: str
    character_pack_id: str
    finance_mode: str
    enabled: bool
    filters: Mapping[str, Any] = field(default_factory=dict)
    delivery_policy: Mapping[str, Any] = field(default_factory=dict)
    created_by_actor_id: str = ""
    created_at: int = 0
    updated_at: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "filters", MappingProxyType(dict(self.filters or {})))
        object.__setattr__(self, "delivery_policy", MappingProxyType(dict(self.delivery_policy or {})))

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "subscription_id": self.subscription_id,
            "client": self.client,
            "target_id": self.target_id,
            "is_group": bool(self.is_group),
            "session_id": self.session_id,
            "profile_user_id": self.profile_user_id,
            "character_pack_id": self.character_pack_id,
            "finance_mode": self.finance_mode,
            "enabled": bool(self.enabled),
            "filters": dict(self.filters),
            "delivery_policy": dict(self.delivery_policy),
            "created_by_actor_id": self.created_by_actor_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class WatchlistItem:
    subscription_id: str
    provider: str
    code: str
    display_name: str
    aliases: tuple[str, ...]
    priority: float
    created_by_actor_id: str
    created_at: int
    updated_at: int

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "subscription_id": self.subscription_id,
            "provider": self.provider,
            "code": self.code,
            "display_name": self.display_name,
            "aliases": list(self.aliases),
            "priority": self.priority,
            "created_by_actor_id": self.created_by_actor_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class MarketQuoteBaseline:
    provider: str
    code: str
    confirmed_snapshot: Mapping[str, Any] = field(default_factory=dict)
    candidate_snapshot: Mapping[str, Any] = field(default_factory=dict)
    candidate_count: int = 0
    last_fetch_at: int = 0
    last_event_key: str = ""
    created_at: int = 0
    updated_at: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "confirmed_snapshot", MappingProxyType(dict(self.confirmed_snapshot or {})))
        object.__setattr__(self, "candidate_snapshot", MappingProxyType(dict(self.candidate_snapshot or {})))

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "code": self.code,
            "confirmed_snapshot": dict(self.confirmed_snapshot),
            "candidate_snapshot": dict(self.candidate_snapshot),
            "candidate_count": max(0, int(self.candidate_count)),
            "last_fetch_at": max(0, int(self.last_fetch_at)),
            "last_event_key": self.last_event_key,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class MarketDataRejection:
    rejection_id: int
    provider: str
    code: str
    observed_at: int
    stage: str
    reason: str
    payload_hash: str
    created_at: int

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "rejection_id": self.rejection_id,
            "provider": self.provider,
            "code": self.code,
            "observed_at": self.observed_at,
            "stage": self.stage,
            "reason": self.reason,
            "payload_hash": self.payload_hash,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class MarketSecurity:
    provider: str
    code: str
    display_name: str
    aliases: tuple[str, ...]
    market: str
    security_type: str
    source: str
    as_of: int
    created_at: int
    updated_at: int

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "code": self.code,
            "display_name": self.display_name,
            "aliases": list(self.aliases),
            "market": self.market,
            "security_type": self.security_type,
            "source": self.source,
            "as_of": self.as_of,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class MarketEventDelivery:
    event_id: str
    subscription_id: str
    status: str
    analysis_id: str
    attempt_count: int
    last_attempt_at: int | None
    delivered_at: int | None
    reason: str
    created_at: int
    updated_at: int
    delivery_mode: str = "immediate"
    importance_level: str = "notify"
    available_at: int = 0

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "subscription_id": self.subscription_id,
            "status": self.status,
            "delivery_mode": self.delivery_mode,
            "importance_level": self.importance_level,
            "available_at": self.available_at,
            "analysis_id": self.analysis_id,
            "attempt_count": self.attempt_count,
            "last_attempt_at": self.last_attempt_at,
            "delivered_at": self.delivered_at,
            "reason": self.reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class MarketEventDeliveryPart:
    event_id: str
    subscription_id: str
    part_key: str
    part_type: str
    status: str
    payload: Mapping[str, Any]
    attempt_count: int
    last_attempt_at: int | None
    delivered_at: int | None
    reason: str
    created_at: int
    updated_at: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload or {})))

    def to_public_dict(self, *, include_payload: bool = False) -> dict[str, Any]:
        result = {
            "event_id": self.event_id,
            "subscription_id": self.subscription_id,
            "part_key": self.part_key,
            "part_type": self.part_type,
            "status": self.status,
            "attempt_count": self.attempt_count,
            "last_attempt_at": self.last_attempt_at,
            "delivered_at": self.delivered_at,
            "reason": self.reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_payload:
            result["payload"] = dict(self.payload)
        return result


@dataclass(frozen=True)
class DeliveryReservation:
    created: bool
    should_deliver: bool
    delivery: MarketEventDelivery

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "should_deliver": self.should_deliver,
            "delivery": self.delivery.to_public_dict(),
        }


@dataclass(frozen=True)
class DeliveryClaim:
    acquired: bool
    delivery: MarketEventDelivery

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "acquired": self.acquired,
            "delivery": self.delivery.to_public_dict(),
        }


@dataclass(frozen=True)
class DeliveryPartClaim:
    acquired: bool
    part: MarketEventDeliveryPart

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "acquired": self.acquired,
            "part": self.part.to_public_dict(),
        }
