from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


DEFAULT_DOMAIN_PROFILE_ID = "default"

# Compatibility identifiers for finance source files that remain in the
# documented M65 migration window. They no longer register a runtime profile.
FINANCE_DOMAIN_PROFILE_ID = "finance_v1"
FINANCE_MODES = ("off", "qa", "push")


def normalize_finance_mode(value: Any, *, default: str = "off") -> str:
    """Normalize frozen finance migration data without activating a profile."""

    fallback = str(default or "off").strip().lower()
    if fallback not in FINANCE_MODES:
        fallback = "off"
    mode = str(value or "").strip().lower()
    return mode if mode in FINANCE_MODES else fallback


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
    """Runtime domain profiles.

    Finance capabilities are contributed by installed plugins and stay usable
    in the current character/profile. The former finance-specific prompt and
    static tool allowlist are intentionally not runtime profiles anymore.
    """

    def __init__(
        self,
        *,
        finance_enabled: bool | None = None,
        finance_tool_round_budget: int | None = None,
        finance_tool_round_hard_limit: int | None = None,
        finance_push_enabled: bool | None = None,
    ) -> None:
        del (
            finance_enabled,
            finance_tool_round_budget,
            finance_tool_round_hard_limit,
            finance_push_enabled,
        )
        self._default = DomainProfile(id=DEFAULT_DOMAIN_PROFILE_ID, enabled=True)

    def get(self, profile_id: Any) -> DomainProfile:
        del profile_id
        return self._default

    def resolve(self, *, profile_id: Any = "", finance_mode: Any = "off") -> DomainProfile:
        del profile_id, finance_mode
        return self._default


def resolve_turn_domain_context(payload: dict[str, Any] | None) -> tuple[DomainProfile, str]:
    del payload
    return DomainProfileRegistry().get(DEFAULT_DOMAIN_PROFILE_ID), "off"


def build_domain_profile_prompt(profile: DomainProfile | None) -> str:
    del profile
    return ""


def filter_tool_names(tool_names: tuple[str, ...] | list[str], profile: DomainProfile | None) -> tuple[str, ...]:
    del profile
    return tuple(str(name or "").strip() for name in tool_names if str(name or "").strip())
