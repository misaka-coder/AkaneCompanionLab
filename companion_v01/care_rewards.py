"""Daily care check-in streaks and offering rewards."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

from .care_catalog import DEFAULT_CHECKIN_COINS
from .care_config import _bounded_int, _coerce_positive_int
from .care_state import _detect_and_store_tier_event


class CareRewardsMixin:
    """Settle daily rewards together with the original relationship and account state."""

    def claim_daily_checkin(
        self,
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        relation_user_id: str = "",
        date_key: str,
        coins: int = DEFAULT_CHECKIN_COINS,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Claim daily check-in coins per relation_user_id.

        Returns ``{"status": "ok"|"already", "coins_granted": int, "snapshot": dict}``.
        """
        conversation_id = conversation_id or profile_user_id
        date_key = str(date_key or "").strip()
        coins = max(1, int(coins or DEFAULT_CHECKIN_COINS))
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        rel_key = relation_user_id or profile_user_id
        with self._lock:
            state = self._load()
            relation = self._relation_entry(
                state,
                character_pack_id=character_pack_id,
                relation_user_id=rel_key,
                conversation_id=conversation_id,
            )
            account = self._account_entry(state, character_pack_id=character_pack_id, relation_user_id=rel_key)
            if str(account.get("last_checkin_date") or "") == date_key:
                snapshot = self._snapshot(
                    state,
                    character_pack_id=character_pack_id,
                    client_mode="qq_text",
                    relation_user_id=rel_key,
                    now_ms=now_ms,
                    conversation_id=conversation_id,
                )
                return {
                    "status": "already",
                    "coins_granted": 0,
                    "date_key": date_key,
                    "streak": int(account.get("checkin_streak") or 1),
                    "snapshot": snapshot,
                }

            # ── Streak calculation ────────────────────────────────────
            last_date_str = str(account.get("last_checkin_date") or "")
            current_streak = int(account.get("checkin_streak") or 0)
            days_absent = 0
            streak_broken = False
            try:
                today_dt = datetime.strptime(date_key, "%Y-%m-%d")
                yesterday_str = (today_dt - timedelta(days=1)).strftime("%Y-%m-%d")
                if last_date_str == yesterday_str:
                    new_streak = current_streak + 1
                else:
                    if last_date_str:
                        try:
                            last_dt = datetime.strptime(last_date_str, "%Y-%m-%d")
                            days_absent = max(0, (today_dt - last_dt).days - 1)
                        except Exception:
                            days_absent = 0
                    streak_broken = current_streak >= 3
                    new_streak = 1
            except Exception:
                new_streak = max(1, current_streak + 1)

            # ── Coin multiplier ───────────────────────────────────────
            if new_streak >= 30:
                multiplier = 3.0
            elif new_streak >= 7:
                multiplier = 2.0
            elif new_streak >= 3:
                multiplier = 1.5
            else:
                multiplier = 1.0
            actual_coins = max(1, round(coins * multiplier))

            account["qq_coins"] = _bounded_int(int(account.get("qq_coins", 0)) + actual_coins, 0, 999999)
            account["last_checkin_date"] = date_key
            account["checkin_streak"] = new_streak
            anchors = relation.setdefault("anchors", {})
            if new_streak > int(anchors.get("max_checkin_streak") or 0):
                anchors["max_checkin_streak"] = new_streak
            relation["updated_at"] = now_ms
            self._save(state)
            snapshot = self._snapshot(
                state,
                character_pack_id=character_pack_id,
                client_mode="qq_text",
                relation_user_id=rel_key,
                now_ms=now_ms,
                conversation_id=conversation_id,
            )
            return {
                "status": "ok",
                "coins_granted": actual_coins,
                "streak": new_streak,
                "streak_milestone": new_streak in (3, 7, 14, 30),
                "streak_broken": streak_broken,
                "days_absent": days_absent,
                "date_key": date_key,
                "snapshot": snapshot,
            }

    def claim_daily_offering(
        self,
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        relation_user_id: str = "",
        date_key: str,
        affection_bonus: int = 3,
        item_price: int = 0,
        inventory_item_id: str = "",
        event_id: str = "",
        item_effects: dict[str, Any] | None = None,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Process a QQ group offering action.

        Affection is granted at most once per user per day (first offering).
        Subsequent offerings apply hunger/energy effects if the item has them,
        but grant 0 affection.  If item_price > 0, deducts from qq_coins first.

        Returns:
            {
                "status": "ok" | "already" | "insufficient_coins",
                "affection_granted": int,
                "daily_bonus": bool,
                "snapshot": dict,
            }
        """
        conversation_id = conversation_id or profile_user_id
        date_key = str(date_key or "").strip()
        affection_bonus = max(0, int(affection_bonus or 0))
        item_price = max(0, int(item_price or 0))
        item_effects = item_effects if isinstance(item_effects, dict) else {}
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        rel_key = relation_user_id or profile_user_id
        with self._lock:
            state = self._load()
            prior = self._find_care_event(state, event_id) if event_id else None
            if prior is not None:
                return {
                    **dict(prior.get("result") or {}),
                    "status": "duplicate",
                    "snapshot": self._snapshot(
                        state,
                        character_pack_id=character_pack_id,
                        client_mode="qq_text",
                        relation_user_id=rel_key,
                        now_ms=now_ms,
                        conversation_id=conversation_id,
                    ),
                }
            relation = self._relation_entry(
                state,
                character_pack_id=character_pack_id,
                relation_user_id=rel_key,
                conversation_id=conversation_id,
            )
            account = self._account_entry(state, character_pack_id=character_pack_id, relation_user_id=rel_key)
            body = self._body_entry(state, character_pack_id=character_pack_id, conversation_id=conversation_id)
            state_before = {
                "hunger": int(body.get("hunger", 55)),
                "energy": int(body.get("energy", 70)),
                "affection": int(relation.get("qq_affection", 10)),
            }
            if inventory_item_id:
                inventory = account.get("inventory") or {}
                item = inventory.get(inventory_item_id)
                if not isinstance(item, dict) or int(item.get("count", 0)) < 1:
                    return {"status": "not_in_inventory", "affection_granted": 0, "daily_bonus": False}
                item["count"] -= 1
                if item["count"] <= 0:
                    del inventory[inventory_item_id]
            if item_price > 0:
                coins_before = int(account.get("qq_coins", 0))
                if coins_before < item_price:
                    snapshot = self._snapshot(
                        state,
                        character_pack_id=character_pack_id,
                        client_mode="qq_text",
                        relation_user_id=rel_key,
                        now_ms=now_ms,
                        conversation_id=conversation_id,
                    )
                    return {
                        "status": "insufficient_coins",
                        "affection_granted": 0,
                        "daily_bonus": False,
                        "coins_before": coins_before,
                        "coins_needed": item_price,
                        "snapshot": snapshot,
                    }
                account["qq_coins"] = coins_before - item_price
            is_daily_bonus = str(account.get("last_offering_date") or "") != date_key
            hunger_delta = int(item_effects.get("hunger", 0) or 0)
            energy_delta = int(item_effects.get("energy", 0) or 0)
            if hunger_delta:
                body["hunger"] = _bounded_int(body.get("hunger", 55) + hunger_delta, 0, 100)
            if energy_delta:
                body["energy"] = _bounded_int(body.get("energy", 70) + energy_delta, 0, 100)
            if is_daily_bonus:
                item_affection = int(item_effects.get("affection", 0) or 0)
                affection_granted = item_affection + affection_bonus
                account["last_offering_date"] = date_key
            else:
                affection_granted = 0
            if affection_granted:
                aff_before_o = _bounded_int(int(relation.get("qq_affection", 10)), 0, 100)
                aff_after_o = _bounded_int(aff_before_o + affection_granted, 0, 100)
                _detect_and_store_tier_event(relation, aff_before_o, aff_after_o)
                relation["qq_affection"] = aff_after_o
            body["updated_at"] = now_ms
            relation["updated_at"] = now_ms
            result = {
                "status": "ok" if is_daily_bonus else "already",
                "affection_granted": affection_granted,
                "daily_bonus": is_daily_bonus,
                "state_before": state_before,
                "effects_applied": {**item_effects, "affection": affection_granted},
            }
            if event_id:
                self._record_care_event(
                    state,
                    {
                        "event_id": event_id,
                        "kind": "offering",
                        "result": result,
                        "timestamp_ms": now_ms,
                        "character_pack_id": character_pack_id,
                        "conversation_id": conversation_id,
                        "relation_user_id": rel_key,
                    },
                )
            self._save(state)
            snapshot = self._snapshot(
                state,
                character_pack_id=character_pack_id,
                client_mode="qq_text",
                relation_user_id=rel_key,
                now_ms=now_ms,
                conversation_id=conversation_id,
            )
            return {**result, "snapshot": snapshot}
