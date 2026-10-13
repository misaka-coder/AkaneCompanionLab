"""Care poke plans and fortune draws with shared event receipts."""

from __future__ import annotations

import random
import time
from typing import Any

from .care_config import _bounded_int, _coerce_positive_int
from .care_state import _detect_and_store_tier_event


class CarePokeMixin:
    """Apply planned poke outcomes and fortune draws using existing care transactions."""

    def apply_poke_plan(
        self,
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        relation_user_id: str = "",
        plan: dict[str, Any],
        event_id: str,
        scope_key: str,
        group_scope_key: str = "",
        cooldown_ms: int = 30_000,
        group_cooldown_ms: int = 2_000,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Atomically apply a resolved poke plan with replay and cooldown guards."""
        conversation_id = conversation_id or profile_user_id
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        plan = dict(plan or {})
        outcome_kind = str(plan.get("outcome_kind") or "plain")
        stateful = outcome_kind in {
            "coin_change",
            "grant_inventory_item",
            "consume_inventory_item",
            "lottery",
        }
        with self._lock:
            state = self._load()
            previous = self._find_care_event(state, event_id)
            if previous is not None:
                return {"status": "duplicate", "event": previous}
            if stateful and self._poke_cooldown_active(
                state,
                scope_key=scope_key,
                group_scope_key=group_scope_key,
                now_ms=now_ms,
                cooldown_ms=max(0, int(cooldown_ms or 0)),
                group_cooldown_ms=max(0, int(group_cooldown_ms or 0)),
            ):
                event = {
                    "event_id": event_id,
                    "source": "poke",
                    "kind": "poke_cooldown",
                    "status": "cooldown",
                    "scope_key": scope_key,
                    "group_scope_key": group_scope_key,
                    "timestamp_ms": now_ms,
                }
                self._record_care_event(state, event)
                self._save(state)
                return {"status": "cooldown", "event": event}

            if outcome_kind == "consume_inventory_item":
                item = plan.get("item") if isinstance(plan.get("item"), dict) else {}
                result = self.use_from_inventory(
                    profile_user_id=profile_user_id,
                    character_pack_id=character_pack_id,
                    relation_user_id=relation_user_id,
                    item_id=str(plan.get("item_id") or ""),
                    item_effects=dict(item.get("effects") or {}),
                    count=int(plan.get("count") or 1),
                    source="poke_consume",
                    event_id=event_id,
                    scope_key=scope_key,
                    group_scope_key=group_scope_key,
                    now_ms=now_ms,
                    conversation_id=conversation_id,
                )
                if result.get("status") == "ok":
                    result["outcome_kind"] = outcome_kind
                return result

            if outcome_kind == "grant_inventory_item":
                item = plan.get("item") if isinstance(plan.get("item"), dict) else {}
                return self.grant_to_inventory(
                    profile_user_id=profile_user_id,
                    character_pack_id=character_pack_id,
                    relation_user_id=relation_user_id,
                    item_id=str(item.get("id") or ""),
                    item_name=str(item.get("name") or item.get("id") or "物品"),
                    count=int(plan.get("count") or 1),
                    item_effects=dict(item.get("effects") or {}),
                    item_category=str(item.get("category") or ""),
                    item_metadata=item,
                    source="poke_drop",
                    event_id=event_id,
                    scope_key=scope_key,
                    group_scope_key=group_scope_key,
                    now_ms=now_ms,
                    conversation_id=conversation_id,
                )

            if outcome_kind == "coin_change":
                coin_delta = int(plan.get("coin_delta") or 0)
                if coin_delta < 0:
                    # 戳一戳扣金币 → 顺走变赛钱，获得等额兑换额度
                    return self.donate_poke_coins(
                        profile_user_id=profile_user_id,
                        character_pack_id=character_pack_id,
                        relation_user_id=relation_user_id,
                        amount=abs(coin_delta),
                        source="poke",
                        event_id=event_id,
                        scope_key=scope_key,
                        group_scope_key=group_scope_key,
                        now_ms=now_ms,
                        conversation_id=conversation_id,
                    )
                return self.adjust_qq_coins(
                    profile_user_id=profile_user_id,
                    character_pack_id=character_pack_id,
                    relation_user_id=relation_user_id,
                    amount=coin_delta,
                    source="poke",
                    event_id=event_id,
                    scope_key=scope_key,
                    group_scope_key=group_scope_key,
                    now_ms=now_ms,
                    conversation_id=conversation_id,
                )

            if outcome_kind == "lottery":
                lottery = self._draw_fortune_slip_on_state(
                    state,
                    profile_user_id=profile_user_id,
                    character_pack_id=character_pack_id,
                    relation_user_id=relation_user_id,
                    slip_cost=5,
                    now_ms=now_ms,
                    conversation_id=conversation_id,
                )
                if lottery.get("status") != "ok":
                    return lottery
                event = {
                    "event_id": event_id,
                    "source": "poke",
                    "kind": "lottery",
                    "status": "ok",
                    "scope_key": scope_key,
                    "group_scope_key": group_scope_key,
                    "mutation": dict(lottery),
                    "timestamp_ms": now_ms,
                }
                self._record_care_event(state, event)
                self._save(state)
                lottery["outcome_kind"] = outcome_kind
                return lottery

            event = {
                "event_id": event_id,
                "source": "poke",
                "kind": outcome_kind,
                "status": "ok",
                "scope_key": scope_key,
                "group_scope_key": group_scope_key,
                "variant": str(plan.get("variant") or ""),
                "timestamp_ms": now_ms,
            }
            self._record_care_event(state, event)
            self._save(state)
            return {
                "status": "ok",
                "outcome_kind": outcome_kind,
                "event": event,
                "snapshot": self._snapshot(
                    state,
                    character_pack_id=character_pack_id,
                    client_mode="qq_text",
                    relation_user_id=relation_user_id or profile_user_id,
                    now_ms=now_ms,
                    conversation_id=conversation_id,
                ),
            }

    def _draw_fortune_slip_on_state(
        self,
        state: dict[str, Any],
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        relation_user_id: str = "",
        slip_cost: int = 5,
        now_ms: int,
    ) -> dict[str, Any]:
        """Resolve and apply one fortune draw without persisting the state."""
        conversation_id = conversation_id or profile_user_id
        fortunes = [
            {"name": "大吉", "prob": 0.10, "quota_delta": 25, "affection_delta": 3},
            {"name": "中吉", "prob": 0.25, "quota_delta": 12, "affection_delta": 0},
            {"name": "小吉", "prob": 0.30, "quota_delta": 6, "affection_delta": 0},
            {"name": "末吉", "prob": 0.25, "quota_delta": 0, "affection_delta": 0},
            {"name": "凶", "prob": 0.10, "quota_delta": -3, "affection_delta": 0},
        ]
        slip_cost = max(1, int(slip_cost))
        rel_key = relation_user_id or profile_user_id
        relation = self._relation_entry(
            state,
            character_pack_id=character_pack_id,
            relation_user_id=rel_key,
            conversation_id=conversation_id,
        )
        account = self._account_entry(state, character_pack_id=character_pack_id, relation_user_id=rel_key)
        quota_before = _bounded_int(account.get("qq_redeem_quota"), 0, 999999, fallback=0)
        if quota_before < slip_cost:
            return {
                "status": "insufficient_quota",
                "quota_before": quota_before,
                "quota_needed": slip_cost,
            }

        roll = random.random()
        cumulative = 0.0
        fortune = fortunes[-1]
        for candidate in fortunes:
            cumulative += candidate["prob"]
            if roll < cumulative:
                fortune = candidate
                break

        net_quota = fortune["quota_delta"] - slip_cost
        new_quota = max(0, min(999999, quota_before + net_quota))
        account["qq_redeem_quota"] = new_quota
        if fortune["affection_delta"] != 0:
            aff_before = _bounded_int(relation.get("qq_affection"), 0, 100, fallback=10)
            aff_after = max(0, min(100, aff_before + fortune["affection_delta"]))
            _detect_and_store_tier_event(relation, aff_before, aff_after)
            relation["qq_affection"] = aff_after
        relation["updated_at"] = now_ms
        return {
            "status": "ok",
            "fortune": fortune["name"],
            "quota_delta": fortune["quota_delta"],
            "affection_delta": fortune["affection_delta"],
            "slip_cost": slip_cost,
            "net_quota": net_quota,
            "quota_before": quota_before,
            "quota_after": new_quota,
            "snapshot": self._snapshot(
                state,
                character_pack_id=character_pack_id,
                client_mode="qq_text",
                relation_user_id=rel_key,
                now_ms=now_ms,
                conversation_id=conversation_id,
            ),
        }

    def draw_fortune_slip(
        self,
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        relation_user_id: str = "",
        slip_cost: int = 5,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Draw a shrine fortune slip using redeem quota and apply effects atomically.

        Fortune table (cumulative probability):
          大吉 10% → +25 quota, affection +3
          中吉 25% → +12 quota
          小吉 30% → +6 quota
          末吉 25% → 0 return
          凶   10% → -3 extra quota (total loss = cost + 3)
        """
        conversation_id = conversation_id or profile_user_id
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        with self._lock:
            state = self._load()
            result = self._draw_fortune_slip_on_state(
                state,
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                relation_user_id=relation_user_id,
                slip_cost=slip_cost,
                now_ms=now_ms,
                conversation_id=conversation_id,
            )
            if result.get("status") == "ok":
                self._save(state)
            return result
