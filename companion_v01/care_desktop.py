"""Authoritative desktop buying, feeding, work, and allowance actions."""

from __future__ import annotations

import random
import time
from typing import Any

from .care_config import (
    _bounded_int,
    _coerce_positive_int,
    _normalize_work_task,
    _safe_key,
    normalize_desktop_care_config,
)


class CareDesktopMixin:
    """Validate desktop gameplay and commit effects through the existing authority view."""

    def perform_desktop_action(
        self,
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        relation_user_id: str = "",
        shared_account: bool = False,
        care_config: Any = None,
        action: str,
        item_id: str = "",
        count: int = 1,
        request_id: str = "",
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Apply one validated desktop Care operation to the authority store."""
        conversation_id = conversation_id or profile_user_id
        relation_user_id = relation_user_id or profile_user_id

        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        config = normalize_desktop_care_config(care_config)
        normalized_action = str(action or "").strip().lower()
        with self._lock:
            state = self._load()
            character_key = _safe_key(character_pack_id or "default_character")
            existing_body = conversation_id in state.get("bodies", {}).get(character_key, {})
            body = self._desktop_view(
                state,
                character_pack_id=character_pack_id,
                conversation_id=conversation_id,
                relation_user_id=relation_user_id,
                shared_account=shared_account,
            )
            self._ensure_desktop_authority(
                body,
                config=config,
                legacy_state=None,
                had_existing_body=existing_body,
                now_ms=now_ms,
            )
            (self._apply_body_time_decay if shared_account else self._apply_desktop_hunger_decay)(
                body,
                now_ms,
                hunger_per_hour=8.0 if shared_account else float(config["decay"]["hunger_per_hour"]),
            )

            cleaned_request_id = str(request_id or "").strip()
            desktop_receipts = body.setdefault("desktop_receipts", {})
            fingerprint = [conversation_id, normalized_action, str(item_id or "").strip(), int(count or 1)]
            if cleaned_request_id and cleaned_request_id in desktop_receipts:
                saved = desktop_receipts[cleaned_request_id]
                if saved.get("fingerprint") == fingerprint:
                    return dict(saved.get("result") or {})
                return {"ok": False, "status": "conflict", "reason": "request_id_conflict"}

            if normalized_action == "buy":
                result = self._desktop_buy(body, config=config, item_id=item_id, now_ms=now_ms)
            elif normalized_action == "feed":
                result = self._desktop_feed(body, config=config, item_id=item_id, count=count, now_ms=now_ms)
            elif normalized_action == "start_work":
                result = self._desktop_start_work(body, config=config, now_ms=now_ms)
            elif normalized_action == "settle_work":
                result = self._desktop_settle_work(body, config=config, now_ms=now_ms)
            elif normalized_action == "claim_allowance":
                result = self._desktop_claim_allowance(body, config=config, now_ms=now_ms)
            elif normalized_action == "refresh":
                result = {"ok": True, "status": "ok", "reason": "snapshot_refreshed"}
            else:
                result = {"ok": False, "status": "invalid_action", "reason": "unsupported_care_action"}

            self._commit_desktop_view(
                state,
                body,
                character_pack_id=character_pack_id,
                conversation_id=conversation_id,
                relation_user_id=relation_user_id,
            )
            result["snapshot"] = self._snapshot(
                state,
                character_pack_id=character_pack_id,
                client_mode="desktop_pet",
                relation_user_id=relation_user_id,
                now_ms=now_ms,
                conversation_id=conversation_id,
            )
            if cleaned_request_id and result.get("ok"):
                desktop_receipts[cleaned_request_id] = {
                    "fingerprint": fingerprint,
                    "result": dict(result),
                }
                if len(desktop_receipts) > 200:
                    oldest_k = next(iter(desktop_receipts))
                    desktop_receipts.pop(oldest_k, None)
            self._commit_desktop_view(
                state,
                body,
                character_pack_id=character_pack_id,
                conversation_id=conversation_id,
                relation_user_id=relation_user_id,
            )
            self._save(state)
            return result

    @staticmethod
    def _desktop_item(config: dict[str, Any], item_id: str) -> dict[str, Any] | None:
        target = str(item_id or "").strip()
        if not target:
            return None
        return next((item for item in config["shop_items"] if item["id"] == target), None)

    def _desktop_buy(
        self,
        body: dict[str, Any],
        *,
        config: dict[str, Any],
        item_id: str,
        now_ms: int,
    ) -> dict[str, Any]:
        item = self._desktop_item(config, item_id)
        if item is None:
            return {"ok": False, "status": "invalid_item", "reason": "item_not_available"}
        coins = _bounded_int(body.get("coins"), 0, 999999, fallback=config["initial_coins"])
        if coins < item["price"]:
            return {
                "ok": False,
                "status": "insufficient_coins",
                "reason": "insufficient_coins",
                "coins_needed": item["price"],
            }
        inventory = body.setdefault("desktop_inventory", {})
        current = _bounded_int(inventory.get(item["id"]), 0, 999, fallback=0)
        body["coins"] = coins - item["price"]
        inventory[item["id"]] = min(999, current + 1)
        body.setdefault("desktop_inventory_metadata", {})[item["id"]] = dict(item)
        body["updated_at"] = now_ms
        return {
            "ok": True,
            "status": "ok",
            "reason": "item_purchased",
            "item_id": item["id"],
            "item_name": item["name"],
        }

    def _desktop_feed(
        self,
        body: dict[str, Any],
        *,
        config: dict[str, Any],
        item_id: str,
        count: int = 1,
        now_ms: int,
    ) -> dict[str, Any]:
        item = self._desktop_item(config, item_id)
        inventory = body.setdefault("desktop_inventory", {})
        available = _bounded_int(inventory.get(str(item_id or "").strip()), 0, 999, fallback=0)
        if item is None or available <= 0:
            return {"ok": False, "status": "not_in_inventory", "reason": "item_not_in_inventory"}

        qty = max(1, min(int(count or 1), available))

        before = {
            "hunger": _bounded_int(body.get("hunger"), 0, 100, fallback=config["initial_hunger"]),
            "energy": _bounded_int(body.get("energy"), 0, 100, fallback=config["initial_energy"]),
            "affection": _bounded_int(
                body.get("desktop_affection"),
                0,
                100,
                fallback=config["initial_affection"],
            ),
        }
        inventory[item["id"]] = available - qty
        if inventory[item["id"]] <= 0:
            inventory.pop(item["id"], None)
        effects = item["effects"]
        body["hunger"] = _bounded_int(before["hunger"] + effects["hunger"] * qty, 0, 100, fallback=before["hunger"])
        body["energy"] = _bounded_int(before["energy"] + effects["energy"] * qty, 0, 100, fallback=before["energy"])
        body["desktop_affection"] = _bounded_int(
            before["affection"] + effects["affection"] * qty,
            0,
            100,
            fallback=before["affection"],
        )
        body["vitals_updated_at_ms"] = now_ms
        body["updated_at"] = now_ms
        applied = {
            key: int(body["desktop_affection"] if key == "affection" else body[key]) - value
            for key, value in before.items()
        }
        return {
            "ok": True,
            "status": "ok",
            "reason": "item_used",
            "item_id": item["id"],
            "item_name": item["name"],
            "effects_applied": applied,
            "quantity": qty,
        }

    def _desktop_start_work(
        self,
        body: dict[str, Any],
        *,
        config: dict[str, Any],
        now_ms: int,
    ) -> dict[str, Any]:
        work = config["work"]
        if not work["enabled"]:
            return {"ok": False, "status": "disabled", "reason": "work_not_configured"}
        if _normalize_work_task(body.get("desktop_work_task")) is not None:
            return {"ok": False, "status": "work_active", "reason": "work_already_active"}
        hunger = _bounded_int(body.get("hunger"), 0, 100, fallback=config["initial_hunger"])
        energy = _bounded_int(body.get("energy"), 0, 100, fallback=config["initial_energy"])
        if hunger < work["min_hunger"]:
            return {"ok": False, "status": "blocked", "reason": "hunger_too_low"}
        if energy < work["min_energy"]:
            return {"ok": False, "status": "blocked", "reason": "energy_too_low"}
        reward = random.randint(work["reward_coins_min"], work["reward_coins_max"])
        body["hunger"] = max(0, hunger - work["hunger_cost"])
        body["energy"] = max(0, energy - work["energy_cost"])
        body["desktop_work_task"] = {
            "status": "active",
            "started_at": now_ms,
            "complete_at": now_ms + work["duration_seconds"] * 1000,
            "reward_coins": reward,
        }
        body["vitals_updated_at_ms"] = now_ms
        body["updated_at"] = now_ms
        return {"ok": True, "status": "ok", "reason": "work_started"}

    def _desktop_settle_work(
        self,
        body: dict[str, Any],
        *,
        config: dict[str, Any],
        now_ms: int,
    ) -> dict[str, Any]:
        task = _normalize_work_task(body.get("desktop_work_task"))
        if task is None:
            return {"ok": False, "status": "not_active", "reason": "work_not_active"}
        if now_ms < task["complete_at"]:
            return {
                "ok": False,
                "status": "not_due",
                "reason": "work_not_due",
                "remaining_ms": task["complete_at"] - now_ms,
            }
        work = config["work"]
        reward = _bounded_int(
            task["reward_coins"],
            work["reward_coins_min"],
            work["reward_coins_max"],
            fallback=work["reward_coins_min"],
        )
        body["coins"] = _bounded_int(
            int(body.get("coins") or 0) + reward,
            0,
            999999,
            fallback=0,
        )
        body["desktop_work_task"] = None
        body["updated_at"] = now_ms
        return {
            "ok": True,
            "status": "ok",
            "reason": "work_completed",
            "reward_coins": reward,
        }

    def _desktop_claim_allowance(
        self,
        body: dict[str, Any],
        *,
        config: dict[str, Any],
        now_ms: int,
    ) -> dict[str, Any]:
        allowance = config["allowance"]
        if not allowance["enabled"]:
            return {"ok": False, "status": "disabled", "reason": "allowance_not_configured"}
        coins = _bounded_int(body.get("coins"), 0, 999999, fallback=config["initial_coins"])
        if coins >= allowance["max_coins"]:
            return {"ok": False, "status": "blocked", "reason": "coins_not_low_enough"}
        last_at = max(0, int(body.get("desktop_last_allowance_at_ms") or 0))
        next_at = last_at + allowance["cooldown_seconds"] * 1000
        if now_ms < next_at:
            return {
                "ok": False,
                "status": "cooldown",
                "reason": "allowance_cooldown",
                "remaining_ms": next_at - now_ms,
            }
        grant = min(allowance["coins"], allowance["max_coins"] - coins)
        body["coins"] = min(999999, coins + grant)
        body["desktop_last_allowance_at_ms"] = now_ms
        body["updated_at"] = now_ms
        return {
            "ok": True,
            "status": "ok",
            "reason": "allowance_claimed",
            "coins_granted": grant,
        }
