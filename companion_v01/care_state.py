"""Care ownership entries, affection tiers, and elapsed-time vitals."""

from __future__ import annotations

import time
from typing import Any

from .care_catalog import DEFAULT_ENERGY_RECOVERY_PER_HOUR, DEFAULT_THRESHOLDS
from .care_config import _bounded_int, _safe_key


def _affection_tier(affection: int) -> str:
    """Return the tier label for a given affection value using DEFAULT_THRESHOLDS."""
    if affection >= DEFAULT_THRESHOLDS.get("affection_bond", 88):
        return "羁绊"
    if affection >= DEFAULT_THRESHOLDS.get("affection_close", 70):
        return "信任"
    if affection >= DEFAULT_THRESHOLDS.get("affection_warm", 45):
        return "亲近"
    if affection >= DEFAULT_THRESHOLDS.get("affection_familiar", 20):
        return "熟悉"
    return "陌生"


def _detect_and_store_tier_event(relation: dict, aff_before: int, aff_after: int) -> None:
    """Write pending_tier_event to relation when affection crosses a named tier boundary."""
    tier_before = _affection_tier(aff_before)
    tier_after = _affection_tier(aff_after)
    if tier_before != tier_after:
        relation["pending_tier_event"] = {
            "from_tier": tier_before,
            "to_tier": tier_after,
            "direction": "up" if aff_after > aff_before else "down",
        }


class CareStateMixin:
    """Resolve existing ownership keys and update vitals inside the authority lock."""

    def _body_entry(
        self,
        state: dict[str, Any],
        *,
        character_pack_id: str,
        conversation_id: str,
    ) -> dict[str, Any]:
        character_key = _safe_key(character_pack_id or "default_character")
        conversation_key = str(conversation_id or "").strip()
        if not conversation_key:
            raise ValueError("care_conversation_required")
        bodies = state.setdefault("bodies", {}).setdefault(character_key, {})
        body = bodies.setdefault(conversation_key, {})
        body.setdefault("hunger", 55)
        body.setdefault("energy", 70)
        body.setdefault("updated_at", int(time.time() * 1000))
        body.setdefault("vitals_updated_at_ms", 0)
        return body

    def _relation_entry(
        self,
        state: dict[str, Any],
        *,
        character_pack_id: str,
        relation_user_id: str,
        conversation_id: str,
    ) -> dict[str, Any]:
        character_key = _safe_key(character_pack_id or "default_character")
        conversation_key = str(conversation_id or "").strip()
        if not conversation_key:
            raise ValueError("care_conversation_required")
        users = state.setdefault("relationships", {}).setdefault(character_key, {}).setdefault(conversation_key, {})
        return users.setdefault(
            _safe_key(relation_user_id or "unknown_qq_user"),
            {
                "qq_affection": 10,
                "anchors": {},
                "updated_at": int(time.time() * 1000),
            },
        )

    @staticmethod
    def _account_entry(
        state: dict[str, Any],
        *,
        character_pack_id: str,
        relation_user_id: str,
    ) -> dict[str, Any]:
        character_key = _safe_key(character_pack_id or "default_character")
        users = state.setdefault("accounts", {}).setdefault(character_key, {})
        return users.setdefault(
            _safe_key(relation_user_id or "unknown_qq_user"),
            {
                "qq_coins": 0,
                "qq_redeem_quota": 0,
                "qq_saiken_balance": 0,
                "qq_saiken_count": 0,
                "inventory": {},
                "last_offering_date": "",
            },
        )

    @staticmethod
    def _economy_entry(state: dict[str, Any], *, character_pack_id: str) -> dict[str, Any]:
        key = _safe_key(character_pack_id or "default_character")
        return state.setdefault("economies", {}).setdefault(key, {"shrine_total": 0})

    def _apply_body_time_decay(
        self,
        body: dict[str, Any],
        now_ms: int,
        *,
        hunger_per_hour: float = 8.0,
        energy_recovery_per_hour: float = DEFAULT_ENERGY_RECOVERY_PER_HOUR,
    ) -> None:
        """Apply time-elapsed vitals change inside the authority lock."""
        last_ms = int(body.get("vitals_updated_at_ms") or 0)
        if last_ms <= 0:
            body["vitals_updated_at_ms"] = now_ms
            return
        elapsed_ms = now_ms - last_ms
        if elapsed_ms < 60_000:  # skip if < 1 minute
            return
        # Cap at 48h: prevents extreme decay from stale timestamps
        elapsed_hours = min(elapsed_ms / 3_600_000, 48.0)
        hunger = _bounded_int(body.get("hunger"), 0, 100, fallback=55)
        energy = _bounded_int(body.get("energy"), 0, 100, fallback=70)
        # Use fractional accumulators so sub-1-point changes aren't lost across short intervals
        hunger_frac = float(body.get("hunger_frac") or 0.0)
        energy_frac = float(body.get("energy_frac") or 0.0)
        raw_hunger = hunger - hunger_per_hour * elapsed_hours + hunger_frac
        raw_energy = energy + energy_recovery_per_hour * elapsed_hours + energy_frac
        new_hunger = round(max(0.0, min(100.0, raw_hunger)), 9)
        new_energy = round(max(0.0, min(100.0, raw_energy)), 9)
        body["hunger"] = int(new_hunger)
        body["energy"] = int(new_energy)
        body["hunger_frac"] = new_hunger - int(new_hunger)
        body["energy_frac"] = new_energy - int(new_energy)
        body["vitals_updated_at_ms"] = now_ms
        body["updated_at"] = now_ms

    def _apply_desktop_hunger_decay(
        self,
        body: dict[str, Any],
        now_ms: int,
        *,
        hunger_per_hour: float,
    ) -> None:
        last_ms = int(body.get("vitals_updated_at_ms") or 0)
        if last_ms <= 0:
            body["vitals_updated_at_ms"] = now_ms
            return
        elapsed_ms = now_ms - last_ms
        if elapsed_ms <= 0:
            return
        elapsed_hours = min(elapsed_ms / 3_600_000, 48.0)
        accumulated = max(0.0, float(body.get("desktop_hunger_decay_fraction") or 0.0))
        accumulated += max(0.0, float(hunger_per_hour)) * elapsed_hours
        decay = int(accumulated)
        body["desktop_hunger_decay_fraction"] = accumulated - decay
        if decay > 0:
            hunger = _bounded_int(body.get("hunger"), 0, 100, fallback=55)
            body["hunger"] = max(0, hunger - decay)
            body["updated_at"] = now_ms
        body["vitals_updated_at_ms"] = now_ms
