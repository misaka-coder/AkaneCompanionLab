from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


DEFAULT_DOMAIN_PROFILE_ID = "default"


@dataclass(frozen=True)
class DomainProfile:
    id: str
    enabled: bool
    prompt_block_ids: tuple[str, ...] = field(default_factory=tuple)
    allowed_tool_names: tuple[str, ...] = field(default_factory=tuple)
    hidden_tool_names: tuple[str, ...] = field(default_factory=tuple)
    capability_hints: tuple[str, ...] = field(default_factory=tuple)
    default_tool_round_budget: int = 3
    hard_tool_round_limit: int = 5
    proactive_delivery_enabled: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "enabled": self.enabled,
            "prompt_block_ids": list(self.prompt_block_ids),
            "allowed_tool_names": list(self.allowed_tool_names),
            "hidden_tool_names": list(self.hidden_tool_names),
            "default_tool_round_budget": self.default_tool_round_budget,
            "hard_tool_round_limit": self.hard_tool_round_limit,
            "proactive_delivery_enabled": self.proactive_delivery_enabled,
        }


class DomainProfileRegistry:
    """Runtime domain profiles owned by the Akane host."""

    def __init__(self) -> None:
        self._default = DomainProfile(id=DEFAULT_DOMAIN_PROFILE_ID, enabled=True)

    def get(self, profile_id: Any) -> DomainProfile:
        del profile_id
        return self._default

    def resolve(self, *, profile_id: Any = "") -> DomainProfile:
        return self.get(profile_id)


def build_domain_profile_prompt(profile: DomainProfile | None) -> str:
    del profile
    return ""


def filter_tool_names(tool_names: tuple[str, ...] | list[str], profile: DomainProfile | None) -> tuple[str, ...]:
    del profile
    return tuple(str(name or "").strip() for name in tool_names if str(name or "").strip())
