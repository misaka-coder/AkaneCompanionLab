"""Care coin balances, shrine donations, redemption quotas, and legacy purchases."""

from __future__ import annotations

import time
from typing import Any

from .care_config import _bounded_int, _coerce_positive_int
from .care_state import _detect_and_store_tier_event


class CareEconomyMixin:
    """Commit currency transfers once without creating a separate account authority."""

    def _apply_saiken_on_state(
        self,
        state: dict[str, Any],
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        relation_user_id: str = "",
        amount: int,
        source: str,
        event_id: str = "",
        scope_key: str = "",
        group_scope_key: str = "",
        now_ms: int,
    ) -> dict[str, Any]:
        """Apply the single authoritative coins -> shrine + quota mutation."""
        conversation_id = conversation_id or profile_user_id

        if amount <= 0:
            return {"status": "invalid_amount", "amount": amount, "message": "赛钱金额必须大于 0"}
        if event_id:
            previous = self._find_care_event(state, event_id)
            if previous is not None:
                return {"status": "duplicate", "event_id": event_id, "event": previous}
        rel_key = relation_user_id or profile_user_id
        self._body_entry(state, character_pack_id=character_pack_id, conversation_id=conversation_id)
        relation = self._relation_entry(
            state,
            character_pack_id=character_pack_id,
            relation_user_id=rel_key,
            conversation_id=conversation_id,
        )
        account = self._account_entry(state, character_pack_id=character_pack_id, relation_user_id=rel_key)
        coins_before = _bounded_int(account.get("qq_coins"), 0, 999999, fallback=0)
        if coins_before < amount:
            return {
                "status": "insufficient_coins",
                "coins_before": coins_before,
                "coins_needed": amount,
                "snapshot": self._snapshot(
                    state,
                    character_pack_id=character_pack_id,
                    client_mode="qq_text",
                    relation_user_id=rel_key,
                    now_ms=now_ms,
                    conversation_id=conversation_id,
                ),
            }
        shrine_before = _bounded_int(
            self._economy_entry(state, character_pack_id=character_pack_id).get("shrine_total"),
            0,
            999999999,
            fallback=0,
        )
        quota_before = _bounded_int(account.get("qq_redeem_quota"), 0, 999999, fallback=0)
        saiken_before = _bounded_int(account.get("qq_saiken_balance"), 0, 999999999, fallback=0)
        shrine_after = shrine_before + amount
        quota_after = quota_before + amount
        saiken_after = saiken_before + amount
        if shrine_after > 999999999 or quota_after > 999999 or saiken_after > 999999999:
            return {
                "status": "balance_limit",
                "reason": "saiken_target_limit",
                "amount": amount,
                "snapshot": self._snapshot(
                    state,
                    character_pack_id=character_pack_id,
                    client_mode="qq_text",
                    relation_user_id=rel_key,
                    now_ms=now_ms,
                    conversation_id=conversation_id,
                ),
            }

        account["qq_coins"] = coins_before - amount
        self._economy_entry(state, character_pack_id=character_pack_id)["shrine_total"] = shrine_after
        account["qq_redeem_quota"] = quota_after
        account["qq_saiken_balance"] = saiken_after
        account["qq_saiken_count"] = max(0, int(account.get("qq_saiken_count") or 0)) + 1
        relation["updated_at"] = now_ms
        mutation = {
            "kind": "coin_change",
            "requested_delta": -amount,
            "actual_delta": -amount,
            "coins_before": coins_before,
            "coins_after": account["qq_coins"],
            "donated_to_shrine": amount,
            "shrine_total": shrine_after,
            "quota_before": quota_before,
            "quota_after": quota_after,
            "saiken_balance": saiken_after,
            "saiken_count": account["qq_saiken_count"],
            "source": str(source or "saiken"),
        }
        self._record_care_event(
            state,
            {
                "event_id": str(event_id or ""),
                "source": str(source or "saiken"),
                "kind": "coin_change",
                "status": "ok",
                "profile_user_id": profile_user_id,
                "character_pack_id": character_pack_id,
                "relation_user_id": rel_key,
                "scope_key": str(scope_key or ""),
                "group_scope_key": str(group_scope_key or ""),
                "mutation": mutation,
                "timestamp_ms": now_ms,
            },
        )
        return {
            "status": "ok",
            **mutation,
            "mutation": mutation,
            "snapshot": self._snapshot(
                state,
                character_pack_id=character_pack_id,
                client_mode="qq_text",
                relation_user_id=rel_key,
                now_ms=now_ms,
                conversation_id=conversation_id,
            ),
        }

    def donate_to_shrine(
        self,
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        relation_user_id: str = "",
        amount: int,
        event_id: str = "",
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Atomically exchange QQ coins for permanent shrine total and spendable quota."""
        conversation_id = conversation_id or profile_user_id

        try:
            clean_amount = int(amount or 0)
        except (TypeError, ValueError):
            clean_amount = 0
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        with self._lock:
            state = self._load()
            result = self._apply_saiken_on_state(
                state,
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                relation_user_id=relation_user_id,
                amount=clean_amount,
                source="saiken",
                event_id=event_id,
                now_ms=now_ms,
                conversation_id=conversation_id,
            )
            if result.get("status") == "ok":
                self._save(state)
            return result

    def adjust_qq_coins(
        self,
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        relation_user_id: str = "",
        amount: int,
        source: str = "poke",
        event_id: str = "",
        scope_key: str = "",
        group_scope_key: str = "",
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Apply a signed coin delta to one QQ relation."""
        conversation_id = conversation_id or profile_user_id
        amount = int(amount or 0)
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
            before = _bounded_int(account.get("qq_coins"), 0, 999999, fallback=0)
            after = _bounded_int(before + amount, 0, 999999)
            actual_delta = after - before
            account["qq_coins"] = after
            relation["updated_at"] = now_ms
            mutation = {
                "kind": "coin_change",
                "requested_delta": amount,
                "actual_delta": actual_delta,
                "coins_before": before,
                "coins_after": after,
                "source": str(source or "poke"),
            }
            self._record_care_event(
                state,
                {
                    "event_id": str(event_id or ""),
                    "source": str(source or "poke"),
                    "kind": "coin_change",
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

    def donate_poke_coins(
        self,
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        relation_user_id: str = "",
        amount: int,
        source: str = "poke",
        event_id: str = "",
        scope_key: str = "",
        group_scope_key: str = "",
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """戳一戳扣金币走赛钱：顺走的金币投进赛钱箱，获得等额兑换额度。

        金币不能直接买商店，因此 poke 扣币统一转成赛钱：
        qq_coins -N, shrine_total +N, redeem_quota +N,
        saiken_balance +N, saiken_count +1。箱子只进不出。
        """
        conversation_id = conversation_id or profile_user_id
        amount = max(1, int(amount or 0))
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        with self._lock:
            state = self._load()
            result = self._apply_saiken_on_state(
                state,
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                relation_user_id=relation_user_id,
                amount=amount,
                source=source,
                event_id=event_id,
                scope_key=scope_key,
                group_scope_key=group_scope_key,
                now_ms=now_ms,
                conversation_id=conversation_id,
            )
            if result.get("status") == "ok":
                self._save(state)
            return result

    def purchase_item(
        self,
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        relation_user_id: str = "",
        price: int,
        effects: dict[str, Any],
        client_mode: str = "qq_text",
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Atomically deduct price from client coins and apply item effects.

        Hunger and energy apply only to this conversation body.
        Affection applies only to the requesting ``relation_user_id`` (QQ mode).

        Returns ``{"status": "ok"|"insufficient_coins", "coins_before": int,
        "coins_after": int, "snapshot": dict}``.
        """
        conversation_id = conversation_id or profile_user_id
        price = max(0, int(price or 0))
        effects = effects if isinstance(effects, dict) else {}
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        rel_key = relation_user_id or profile_user_id
        with self._lock:
            state = self._load()
            body = self._body_entry(state, character_pack_id=character_pack_id, conversation_id=conversation_id)
            relation = self._relation_entry(
                state, character_pack_id=character_pack_id, relation_user_id=rel_key, conversation_id=conversation_id
            )
            account = self._account_entry(state, character_pack_id=character_pack_id, relation_user_id=rel_key)
            coins_before = int(account.get("qq_coins", 0))
            if coins_before < price:
                snapshot = self._snapshot(
                    state,
                    character_pack_id=character_pack_id,
                    client_mode=client_mode,
                    relation_user_id=rel_key,
                    now_ms=now_ms,
                    conversation_id=conversation_id,
                )
                return {
                    "status": "insufficient_coins",
                    "coins_before": coins_before,
                    "coins_needed": price,
                    "snapshot": snapshot,
                }
            account["qq_coins"] = coins_before - price
            relation["updated_at"] = now_ms
            hunger_delta = int(effects.get("hunger", 0) or 0)
            energy_delta = int(effects.get("energy", 0) or 0)
            if hunger_delta:
                body["hunger"] = _bounded_int(body.get("hunger", 55) + hunger_delta, 0, 100)
            if energy_delta:
                body["energy"] = _bounded_int(body.get("energy", 70) + energy_delta, 0, 100)
            body["updated_at"] = now_ms
            affection_delta = int(effects.get("affection", 0) or 0)
            if affection_delta:
                before = int(relation.get("qq_affection", 10))
                after = _bounded_int(before + affection_delta, 0, 100)
                relation["qq_affection"] = after
                _detect_and_store_tier_event(relation, before, after)
            self._save(state)
            snapshot = self._snapshot(
                state,
                character_pack_id=character_pack_id,
                client_mode=client_mode,
                relation_user_id=rel_key,
                now_ms=now_ms,
                conversation_id=conversation_id,
            )
            coins_after = int(account["qq_coins"])
            return {
                "status": "ok",
                "coins_before": coins_before,
                "coins_after": coins_after,
                "snapshot": snapshot,
            }

    def add_coins(
        self,
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        amount: int,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Add coins to the shared body pool. Returns snapshot."""
        conversation_id = conversation_id or profile_user_id
        amount = max(0, int(amount or 0))
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        with self._lock:
            state = self._load()
            body = self._body_entry(state, character_pack_id=character_pack_id, conversation_id=conversation_id)
            account = self._account_entry(state, character_pack_id=character_pack_id, relation_user_id=profile_user_id)
            account["qq_coins"] = _bounded_int(account.get("qq_coins", 0) + amount, 0, 999999)
            body["updated_at"] = now_ms
            self._save(state)
            return self._snapshot(
                state,
                character_pack_id=character_pack_id,
                client_mode="desktop_pet",
                relation_user_id=profile_user_id,
                now_ms=now_ms,
                conversation_id=conversation_id,
            )
