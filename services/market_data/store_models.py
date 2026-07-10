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
            "code": self.code,
            "display_name": self.display_name,
            "aliases": list(self.aliases),
            "priority": self.priority,
            "created_by_actor_id": self.created_by_actor_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
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

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "subscription_id": self.subscription_id,
            "status": self.status,
            "analysis_id": self.analysis_id,
            "attempt_count": self.attempt_count,
            "last_attempt_at": self.last_attempt_at,
            "delivered_at": self.delivered_at,
            "reason": self.reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


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
