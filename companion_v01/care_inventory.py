"""Personal care inventory purchases, grants, and consumption."""

from __future__ import annotations

import random
import time
from typing import Any

from .care_config import _bounded_int, _coerce_positive_int
from .care_state import _detect_and_store_tier_event


class CareInventoryMixin:
    """Mutate personal inventory and the active conversation body under the care lock."""

    def buy_to_inventory(
        self,
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        relation_user_id: str = "",
        item_id: str,
        item_name: str,
        price: int,
        count: int = 1,
        item_effects: dict[str, Any] | None = None,
        item_category: str = "",
        item_metadata: dict[str, Any] | None = None,
        source: str = "direct_purchase",
        event_id: str = "",
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Spend redemption quota once and add personal inventory without applying item effects."""
        conversation_id = conversation_id or profile_user_id
        price = max(0, int(price))
        count = max(1, min(99, int(count or 1)))
        total_price = price * count
        item_id = str(item_id or "").strip()
        item_name = str(item_name or item_id).strip()
        if not item_id:
            return {"status": "invalid_item"}
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        with self._lock:
            state = self._load()
            if event_id:
                previous = self._find_care_event(state, event_id)
                if previous is not None:
                    return {"status": "duplicate", "event_id": event_id, "event": previous}
            relation = self._relation_entry(
                state,
                character_pack_id=character_pack_id,
                relation_user_id=relation_user_id or profile_user_id,
                conversation_id=conversation_id,
            )
            account = self._account_entry(
                state, character_pack_id=character_pack_id, relation_user_id=relation_user_id or profile_user_id
            )
            quota_before = _bounded_int(account.get("qq_redeem_quota"), 0, 999999, fallback=0)
            if quota_before < total_price:
                return {
                    "status": "insufficient_quota",
                    "quota_before": quota_before,
                    "quota_needed": total_price,
                    "snapshot": self._snapshot(
                        state,
                        character_pack_id=character_pack_id,
                        client_mode="qq_text",
                        relation_user_id=relation_user_id or profile_user_id,
                        now_ms=now_ms,
                        conversation_id=conversation_id,
                    ),
                }
            account["qq_redeem_quota"] = quota_before - total_price
            inventory = account.setdefault("inventory", {})
            if item_id in inventory and isinstance(inventory[item_id], dict):
                inventory[item_id]["count"] = inventory[item_id].get("count", 0) + count
                if item_effects:
                    inventory[item_id]["effects"] = dict(item_effects)
                if isinstance(item_metadata, dict):
                    for key in ("description", "seasonal", "seasonal_label", "seasonal_emoji", "image", "art_kind"):
                        if key in item_metadata:
                            inventory[item_id][key] = item_metadata[key]
                sources = inventory[item_id].get("acquisition_sources")
                if not isinstance(sources, dict):
                    sources = {}
                    inventory[item_id]["acquisition_sources"] = sources
                sources[source] = int(sources.get(source) or 0) + count
            else:
                entry_data: dict[str, Any] = {"name": item_name, "count": count}
                if item_effects:
                    entry_data["effects"] = dict(item_effects)
                if item_category:
                    entry_data["category"] = str(item_category)
                if isinstance(item_metadata, dict):
                    for key in ("description", "seasonal", "seasonal_label", "seasonal_emoji", "image", "art_kind"):
                        if key in item_metadata:
                            entry_data[key] = item_metadata[key]
                entry_data["acquisition_sources"] = {source: count}
                inventory[item_id] = entry_data
            relation["updated_at"] = now_ms
            self._record_care_event(
                state,
                {
                    "event_id": str(event_id or ""),
                    "source": str(source or "direct_purchase"),
                    "kind": "inventory_grant",
                    "profile_user_id": profile_user_id,
                    "character_pack_id": character_pack_id,
                    "relation_user_id": relation_user_id or profile_user_id,
                    "item_id": item_id,
                    "item_name": item_name,
                    "count": count,
                    "timestamp_ms": now_ms,
                },
            )
            self._save(state)
            return {
                "status": "ok",
                "quota_before": quota_before,
                "quota_after": account["qq_redeem_quota"],
                "item_id": item_id,
                "item_name": item_name,
                "item_count": inventory[item_id]["count"],
                "snapshot": self._snapshot(
                    state,
                    character_pack_id=character_pack_id,
                    client_mode="qq_text",
                    relation_user_id=relation_user_id or profile_user_id,
                    now_ms=now_ms,
                    conversation_id=conversation_id,
                ),
            }

    def use_from_inventory(
        self,
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        relation_user_id: str = "",
        item_id: str,
        item_effects: dict[str, Any] | None = None,
        count: int = 1,
        source: str = "direct_feed",
        event_id: str = "",
        scope_key: str = "",
        group_scope_key: str = "",
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Consume item(s) from user's inventory and apply scaled effects to shared body."""
        conversation_id = conversation_id or profile_user_id
        item_id = str(item_id or "").strip()
        effects = dict(item_effects or {})
        count = max(1, min(99, int(count or 1)))
        source_key = str(source or "direct_feed").strip() or "direct_feed"
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        with self._lock:
            state = self._load()
            if event_id:
                previous = self._find_care_event(state, event_id)
                if previous is not None:
                    return {"status": "duplicate", "event_id": event_id, "event": previous}
            body = self._body_entry(state, character_pack_id=character_pack_id, conversation_id=conversation_id)
            relation = self._relation_entry(
                state,
                character_pack_id=character_pack_id,
                relation_user_id=relation_user_id or profile_user_id,
                conversation_id=conversation_id,
            )
            account = self._account_entry(
                state, character_pack_id=character_pack_id, relation_user_id=relation_user_id or profile_user_id
            )
            inventory = account.get("inventory") or {}
            entry = inventory.get(item_id)
            available = int(entry.get("count", 0)) if entry and isinstance(entry, dict) else 0
            if not entry or not isinstance(entry, dict) or available <= 0:
                return {
                    "status": "not_in_inventory",
                    "item_id": item_id,
                    "available": 0,
                    "snapshot": self._snapshot(
                        state,
                        character_pack_id=character_pack_id,
                        client_mode="qq_text",
                        relation_user_id=relation_user_id or profile_user_id,
                        now_ms=now_ms,
                        conversation_id=conversation_id,
                    ),
                }
            if available < count:
                return {
                    "status": "insufficient_count",
                    "item_id": item_id,
                    "available": available,
                    "requested": count,
                    "snapshot": self._snapshot(
                        state,
                        character_pack_id=character_pack_id,
                        client_mode="qq_text",
                        relation_user_id=relation_user_id or profile_user_id,
                        now_ms=now_ms,
                        conversation_id=conversation_id,
                    ),
                }
            item_name = str(entry.get("name") or item_id)
            # Fall back to snapshot-stored effects when caller doesn't supply them
            if not effects:
                effects = dict(entry.get("effects") or {})
            entry["count"] -= count
            if entry["count"] <= 0:
                del inventory[item_id]

            # ── Apply effects ─────────────────────────────────────────
            hunger_now = _bounded_int(body.get("hunger"), 0, 100, fallback=55)
            energy_now = _bounded_int(body.get("energy"), 0, 100, fallback=70)
            aff_before = _bounded_int(relation.get("qq_affection"), 0, 100, fallback=10)
            state_before = {
                "hunger": hunger_now,
                "energy": energy_now,
                "affection": aff_before,
                "coins": _bounded_int(account.get("qq_coins"), 0, 999999, fallback=0),
            }

            # Additive
            for key in ("hunger", "energy"):
                if key in effects:
                    val = int(effects[key]) * count
                    if key == "hunger":
                        hunger_now = max(0, min(100, hunger_now + val))
                    else:
                        energy_now = max(0, min(100, energy_now + val))

            # Set (direct value override, applied per-item so count is irrelevant for pure-set items)
            if "hunger_set" in effects:
                hunger_now = max(0, min(100, int(effects["hunger_set"])))
            if "energy_set" in effects:
                energy_now = max(0, min(100, int(effects["energy_set"])))

            # Swap
            if effects.get("hunger_energy_swap"):
                hunger_now, energy_now = energy_now, hunger_now

            # Random vitals: ±25 independently for each
            if effects.get("random_vitals"):
                h_delta = random.choice([-25, -15, -5, 5, 10, 15, 20, 25])
                e_delta = random.choice([-25, -15, -5, 5, 10, 15, 20, 25])
                hunger_now = max(0, min(100, hunger_now + h_delta))
                energy_now = max(0, min(100, energy_now + e_delta))
                effects = dict(effects, _resolved_h_delta=h_delta, _resolved_e_delta=e_delta)

            body["hunger"] = hunger_now
            body["energy"] = energy_now
            body["vitals_updated_at_ms"] = now_ms
            body["updated_at"] = now_ms

            # Affection
            current_aff = aff_before
            if "affection" in effects:
                current_aff = max(0, min(100, current_aff + int(effects["affection"]) * count))
            if effects.get("affection_set") is not None:
                current_aff = max(0, min(100, int(effects["affection_set"])))
            if effects.get("random_affection"):
                # 7:3 odds: +15 vs -10
                aff_delta = 15 if random.random() < 0.7 else -10
                current_aff = max(0, min(100, current_aff + aff_delta))
                effects = dict(effects, _resolved_aff_delta=aff_delta)
            _detect_and_store_tier_event(relation, aff_before, current_aff)
            relation["qq_affection"] = current_aff
            # Poke consumption is already represented by its QQ event; it is
            # not a first-feed relationship anchor.
            if source_key == "direct_feed":
                anchors = relation.setdefault("anchors", {})
                if "first_fed" not in anchors:
                    anchors["first_fed"] = {"name": item_name, "ms": now_ms}
            relation["updated_at"] = now_ms
            self._record_care_event(
                state,
                {
                    "event_id": str(event_id or ""),
                    "source": source_key,
                    "kind": "inventory_consume",
                    "status": "ok",
                    "profile_user_id": profile_user_id,
                    "character_pack_id": character_pack_id,
                    "relation_user_id": relation_user_id or profile_user_id,
                    "scope_key": str(scope_key or ""),
                    "group_scope_key": str(group_scope_key or ""),
                    "item_id": item_id,
                    "item_name": item_name,
                    "count": count,
                    "effects_applied": dict(effects),
                    "timestamp_ms": now_ms,
                },
            )
            self._save(state)
            return {
                "status": "ok",
                "item_id": item_id,
                "item_name": item_name,
                "count_used": count,
                "effects_applied": effects,
                "state_before": state_before,
                "snapshot": self._snapshot(
                    state,
                    character_pack_id=character_pack_id,
                    client_mode="qq_text",
                    relation_user_id=relation_user_id or profile_user_id,
                    now_ms=now_ms,
                    conversation_id=conversation_id,
                ),
            }

    def grant_to_inventory(
        self,
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        relation_user_id: str = "",
        item_id: str,
        item_name: str,
        count: int = 1,
        item_effects: dict[str, Any] | None = None,
        item_category: str = "",
        item_metadata: dict[str, Any] | None = None,
        source: str = "poke_drop",
        event_id: str = "",
        scope_key: str = "",
        group_scope_key: str = "",
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Grant inventory items without routing the action through purchase."""
        conversation_id = conversation_id or profile_user_id
        item_id = str(item_id or "").strip()
        item_name = str(item_name or item_id).strip()
        count = max(1, min(99, int(count or 1)))
        if not item_id:
            return {"status": "invalid_item"}
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        with self._lock:
            state = self._load()
            if event_id:
                previous = self._find_care_event(state, event_id)
                if previous is not None:
                    return {"status": "duplicate", "event_id": event_id, "event": previous}
            relation = self._relation_entry(
                state,
                character_pack_id=character_pack_id,
                relation_user_id=relation_user_id or profile_user_id,
                conversation_id=conversation_id,
            )
            account = self._account_entry(
                state, character_pack_id=character_pack_id, relation_user_id=relation_user_id or profile_user_id
            )
            inventory = account.setdefault("inventory", {})
            entry = inventory.get(item_id)
            if not isinstance(entry, dict):
                entry = {"name": item_name, "count": 0}
                inventory[item_id] = entry
            entry["name"] = item_name
            entry["count"] = min(999, int(entry.get("count") or 0) + count)
            if item_effects:
                entry["effects"] = dict(item_effects)
            if item_category:
                entry["category"] = str(item_category)
            if isinstance(item_metadata, dict):
                for key in ("description", "seasonal", "seasonal_label", "seasonal_emoji", "image", "art_kind"):
                    if key in item_metadata:
                        entry[key] = item_metadata[key]
            sources = entry.get("acquisition_sources")
            if not isinstance(sources, dict):
                sources = {}
                entry["acquisition_sources"] = sources
            source_key = str(source or "poke_drop")
            sources[source_key] = int(sources.get(source_key) or 0) + count
            relation["updated_at"] = now_ms
            mutation = {
                "kind": "inventory_grant",
                "item_id": item_id,
                "item_name": item_name,
                "count": count,
                "source": source_key,
                "inventory_count": entry["count"],
            }
            self._record_care_event(
                state,
                {
                    "event_id": str(event_id or ""),
                    "source": source_key,
                    "kind": "inventory_grant",
                    "status": "ok",
                    "profile_user_id": profile_user_id,
                    "character_pack_id": character_pack_id,
                    "relation_user_id": relation_user_id or profile_user_id,
                    "scope_key": str(scope_key or ""),
                    "group_scope_key": str(group_scope_key or ""),
                    "mutation": mutation,
                    "timestamp_ms": now_ms,
                },
            )
            self._save(state)
            return {
                "status": "ok",
                "mutation": mutation,
                "snapshot": self._snapshot(
                    state,
                    character_pack_id=character_pack_id,
                    client_mode="qq_text",
                    relation_user_id=relation_user_id or profile_user_id,
                    now_ms=now_ms,
                    conversation_id=conversation_id,
                ),
            }
