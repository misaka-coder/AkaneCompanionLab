from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any


POKE_OUTCOME_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("plain", 0.55),
    ("coin_change", 0.15),
    ("grant_inventory_item", 0.10),
    ("consume_inventory_item", 0.12),
    ("variant", 0.07),
    ("lottery", 0.01),
)


@dataclass(frozen=True, slots=True)
class PokeOutcome:
    """The resolved event facts passed from the host to the QQ turn."""

    event_kind: str
    source: str
    actor_label: str
    outcome_kind: str
    memory_text: str
    prompt_text: str
    mutations: tuple[dict[str, Any], ...] = ()
    status: str = "ok"
    reason: str = ""
    event_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_kind": self.event_kind,
            "source": self.source,
            "actor_label": self.actor_label,
            "outcome_kind": self.outcome_kind,
            "memory_text": self.memory_text,
            "prompt_text": self.prompt_text,
            "mutations": [dict(item) for item in self.mutations],
            "status": self.status,
            "reason": self.reason,
            "event_id": self.event_id,
        }


class PokeEventReactor:
    """Choose a poke plan without mutating state or generating model text."""

    def __init__(self, *, rng: Any = None) -> None:
        self._rng = rng or random

    def plan(
        self,
        *,
        snapshot: dict[str, Any],
        shop_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        roll = float(self._rng.random())
        cumulative = 0.0
        outcome_kind = "plain"
        for candidate, weight in POKE_OUTCOME_WEIGHTS:
            cumulative += weight
            if roll < cumulative:
                outcome_kind = candidate
                break

        if outcome_kind == "coin_change":
            sign = 1 if self._rng.random() < 0.65 else -1
            amount = int(self._rng.choice((1, 2, 3, 4, 5, 6))) * sign
            if int(snapshot.get("coins") or 0) <= 0 and amount < 0:
                amount = abs(amount)
            return {"outcome_kind": outcome_kind, "coin_delta": amount}

        if outcome_kind == "grant_inventory_item":
            candidates = [item for item in shop_items if _usable_in_qq(item)]
            if candidates:
                item = dict(self._rng.choice(candidates))
                return {"outcome_kind": outcome_kind, "item": item, "count": 1}
            return {"outcome_kind": "plain", "fallback_reason": "no_shop_item"}

        if outcome_kind == "consume_inventory_item":
            inventory = snapshot.get("inventory") if isinstance(snapshot.get("inventory"), dict) else {}
            candidates = [
                (str(item_id), dict(entry))
                for item_id, entry in inventory.items()
                if isinstance(entry, dict) and int(entry.get("count") or 0) > 0
            ]
            if candidates:
                item_id, entry = self._rng.choice(candidates)
                available = max(1, int(entry.get("count") or 1))
                count = 2 if available >= 2 and self._rng.random() < 0.3 else 1
                return {
                    "outcome_kind": outcome_kind,
                    "item_id": item_id,
                    "item": entry,
                    "count": count,
                }
            return {"outcome_kind": "plain", "fallback_reason": "empty_inventory"}

        if outcome_kind == "lottery" and int(snapshot.get("coins") or 0) < 5:
            return {"outcome_kind": "plain", "fallback_reason": "insufficient_coins"}

        if outcome_kind == "variant":
            return {
                "outcome_kind": outcome_kind,
                "variant": str(
                    self._rng.choice(("躲开了这一下", "装作没有被戳到", "被戳得晃了晃", "突然安静下来"))
                ),
            }

        return {"outcome_kind": outcome_kind}


def _usable_in_qq(item: dict[str, Any]) -> bool:
    usable_in = item.get("usable_in")
    if not isinstance(usable_in, list):
        return True
    return "qq" in {str(value or "").strip().lower() for value in usable_in}
