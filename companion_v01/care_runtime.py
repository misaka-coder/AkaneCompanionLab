from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "akane.care_runtime.v1"

DEFAULT_CHECKIN_COINS = 10
# Passive energy recovery per hour (server-side, QQ mode only)
DEFAULT_ENERGY_RECOVERY_PER_HOUR: float = 1.0

DEFAULT_CARE_SHOP_ITEMS: list[dict[str, Any]] = [
    {
        "id": "sanshoku_dango",
        "name": "三色团子",
        "price": 7,
        "category": "food",
        "preference_tags": ["sweet", "traditional_snack", "shrine"],
        "usable_in": ["desktop_pet", "qq"],
        "effects": {"hunger": 20, "energy": 4, "affection": 4},
    },
    {
        "id": "warm_genmaicha",
        "name": "温热玄米茶",
        "price": 6,
        "category": "drink",
        "preference_tags": ["tea", "traditional_snack"],
        "usable_in": ["desktop_pet", "qq"],
        "effects": {"hunger": 6, "energy": 16, "affection": 2},
    },
    {
        "id": "red_bean_daifuku",
        "name": "红豆大福",
        "price": 10,
        "category": "food",
        "preference_tags": ["sweet", "traditional_snack"],
        "usable_in": ["desktop_pet", "qq"],
        "effects": {"hunger": 22, "energy": 4, "affection": 4},
    },
    {
        "id": "senbei",
        "name": "仙贝",
        "price": 3,
        "category": "food",
        "preference_tags": ["traditional_snack"],
        "usable_in": ["desktop_pet", "qq"],
        "effects": {"hunger": 12, "energy": 0, "affection": 1},
    },
    {
        "id": "saisen_offering",
        "name": "赛錢小供品",
        "price": 8,
        "category": "offering",
        "preference_tags": ["offering", "shrine"],
        "usable_in": ["qq"],
        "effects": {"affection": 5},
    },
]

DEFAULT_THRESHOLDS = {
    "hunger_low": 25,
    "hunger_critical": 12,
    "energy_low": 25,
    "energy_critical": 12,
    "affection_warm": 45,
    "affection_close": 75,
}


class CareRuntimeStore:
    """JSON store for one character body plus per-client relationships."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._state: dict[str, Any] | None = None

    def sync_from_client(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        client_mode: str = "",
        care_payload: Any,
        relation_user_id: str = "",
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(care_payload, dict) or care_payload.get("enabled") is False:
            return self.snapshot_for_client(
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                client_mode=client_mode,
                relation_user_id=relation_user_id,
                now_ms=now_ms,
            )

        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        with self._lock:
            state = self._load()
            body = self._body_entry(state, character_pack_id=character_pack_id)
            vitals_written = False
            for key in ("hunger", "energy"):
                if key in care_payload:
                    body[key] = _bounded_int(care_payload.get(key), 0, 100, fallback=int(body.get(key, 0)))
                    vitals_written = True
            if vitals_written:
                body["vitals_updated_at_ms"] = now_ms
            if not _is_qq_mode(client_mode) and "coins" in care_payload:
                body["coins"] = _bounded_int(care_payload.get("coins"), 0, 999999, fallback=int(body.get("coins", 20)))
            if "affection" in care_payload:
                if _is_qq_mode(client_mode):
                    relation = self._relation_entry(
                        state,
                        character_pack_id=character_pack_id,
                        relation_user_id=relation_user_id or profile_user_id,
                    )
                    relation["qq_affection"] = _bounded_int(
                        care_payload.get("affection"),
                        0,
                        100,
                        fallback=int(relation.get("qq_affection", 10)),
                    )
                    relation["updated_at"] = now_ms
                else:
                    body["desktop_affection"] = _bounded_int(
                        care_payload.get("affection"),
                        0,
                        100,
                        fallback=int(body.get("desktop_affection", 10)),
                    )
            body["updated_at"] = now_ms
            body["last_client_sync_at"] = now_ms
            self._save(state)
            return self._snapshot(
                state,
                character_pack_id=character_pack_id,
                client_mode=client_mode,
                relation_user_id=relation_user_id or profile_user_id,
                now_ms=now_ms,
            )

    def snapshot_for_client(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        client_mode: str = "",
        relation_user_id: str = "",
        now_ms: int | None = None,
        hunger_per_hour: float | None = None,
    ) -> dict[str, Any]:
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        with self._lock:
            state = self._load()
            body = self._body_entry(state, character_pack_id=character_pack_id)
            if _is_qq_mode(client_mode):
                self._relation_entry(
                    state,
                    character_pack_id=character_pack_id,
                    relation_user_id=relation_user_id or profile_user_id,
                )
                self._apply_body_time_decay(
                    body,
                    now_ms,
                    hunger_per_hour=hunger_per_hour if hunger_per_hour is not None else 8.0,
                )
            self._save(state)
            return self._snapshot(
                state,
                character_pack_id=character_pack_id,
                client_mode=client_mode,
                relation_user_id=relation_user_id or profile_user_id,
                now_ms=now_ms,
            )

    def apply_affinity_delta(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        client_mode: str = "",
        relation_user_id: str = "",
        delta: Any,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        delta_value = _bounded_int(delta, -5, 5, fallback=0)
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        with self._lock:
            state = self._load()
            body = self._body_entry(state, character_pack_id=character_pack_id)
            if _is_qq_mode(client_mode):
                relation = self._relation_entry(
                    state,
                    character_pack_id=character_pack_id,
                    relation_user_id=relation_user_id or profile_user_id,
                )
                relation["qq_affection"] = _bounded_int(
                    int(relation.get("qq_affection", 10)) + delta_value,
                    0,
                    100,
                    fallback=int(relation.get("qq_affection", 10)),
                )
                relation["updated_at"] = now_ms
            else:
                body["desktop_affection"] = _bounded_int(
                    int(body.get("desktop_affection", 10)) + delta_value,
                    0,
                    100,
                    fallback=int(body.get("desktop_affection", 10)),
                )
                body["updated_at"] = now_ms
            self._save(state)
            return self._snapshot(
                state,
                character_pack_id=character_pack_id,
                client_mode=client_mode,
                relation_user_id=relation_user_id or profile_user_id,
                now_ms=now_ms,
            )

    def _snapshot(
        self,
        state: dict[str, Any],
        *,
        character_pack_id: str,
        client_mode: str,
        relation_user_id: str,
        now_ms: int,
    ) -> dict[str, Any]:
        body = self._body_entry(state, character_pack_id=character_pack_id)
        if _is_qq_mode(client_mode):
            relation = self._relation_entry(
                state,
                character_pack_id=character_pack_id,
                relation_user_id=relation_user_id,
            )
            affection = _bounded_int(relation.get("qq_affection"), 0, 100, fallback=10)
            affection_scope = "qq_text"
            coins = _bounded_int(relation.get("qq_coins"), 0, 999999, fallback=0)
            last_offering_date = str(relation.get("last_offering_date") or "")
        else:
            affection = _bounded_int(body.get("desktop_affection"), 0, 100, fallback=10)
            affection_scope = "desktop_pet"
            coins = _bounded_int(body.get("coins"), 0, 999999, fallback=20)
            last_offering_date = ""
        return {
            "enabled": True,
            "source": "care_runtime",
            "shared_vitals": True,
            "affection_scope": affection_scope,
            "now": now_ms,
            "hunger": _bounded_int(body.get("hunger"), 0, 100, fallback=55),
            "energy": _bounded_int(body.get("energy"), 0, 100, fallback=70),
            "coins": coins,
            "affection": affection,
            "last_offering_date": last_offering_date,
            "thresholds": dict(DEFAULT_THRESHOLDS),
        }

    def _body_entry(self, state: dict[str, Any], *, character_pack_id: str) -> dict[str, Any]:
        character_key = _safe_key(character_pack_id or "default_character")
        characters = state.setdefault("characters", {})
        body = characters.setdefault(
            character_key,
            {
                "hunger": 55,
                "energy": 70,
                "coins": 20,
                "desktop_affection": 10,
                "updated_at": int(time.time() * 1000),
            },
        )
        if not isinstance(body, dict):
            body = {}
            characters[character_key] = body
        body.setdefault("hunger", 55)
        body.setdefault("energy", 70)
        body.setdefault("coins", 20)
        body.setdefault("desktop_affection", 10)
        body.setdefault("updated_at", int(time.time() * 1000))
        body.setdefault("vitals_updated_at_ms", 0)
        return body

    def _apply_body_time_decay(
        self,
        body: dict[str, Any],
        now_ms: int,
        *,
        hunger_per_hour: float = 8.0,
        energy_recovery_per_hour: float = DEFAULT_ENERGY_RECOVERY_PER_HOUR,
    ) -> None:
        """Apply time-elapsed hunger decay and passive energy recovery to body.

        Only used in QQ mode — desktop pet handles its own decay client-side.
        Called inside an existing lock; does not acquire _lock itself.
        """
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
        body["hunger"] = max(0, min(100, round(hunger - hunger_per_hour * elapsed_hours)))
        body["energy"] = max(0, min(100, round(energy + energy_recovery_per_hour * elapsed_hours)))
        body["vitals_updated_at_ms"] = now_ms

    def apply_energy_cost(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        relation_user_id: str = "",
        energy_cost: int = 1,
        coin_reward: int = 0,
        now_ms: int | None = None,
    ) -> None:
        """Deduct energy from shared body; optionally credit coins to the sender."""
        cost = max(0, int(energy_cost))
        reward = max(0, int(coin_reward))
        if cost == 0 and reward == 0:
            return
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        with self._lock:
            state = self._load()
            body = self._body_entry(state, character_pack_id=character_pack_id)
            if cost > 0:
                current = _bounded_int(body.get("energy"), 0, 100, fallback=70)
                body["energy"] = max(0, current - cost)
                body["vitals_updated_at_ms"] = now_ms
            if reward > 0:
                rel_key = relation_user_id or profile_user_id
                relation = self._relation_entry(
                    state,
                    character_pack_id=character_pack_id,
                    relation_user_id=rel_key,
                )
                current_coins = _bounded_int(relation.get("qq_coins"), 0, 999999, fallback=0)
                relation["qq_coins"] = min(999999, current_coins + reward)
                relation["updated_at"] = now_ms
            self._save(state)

    def _relation_entry(
        self,
        state: dict[str, Any],
        *,
        character_pack_id: str,
        relation_user_id: str,
    ) -> dict[str, Any]:
        character_key = _safe_key(character_pack_id or "default_character")
        relation_key = _safe_key(relation_user_id or "unknown_qq_user")
        relations = state.setdefault("relations", {})
        character_relations = relations.setdefault(character_key, {})
        relation = character_relations.setdefault(
            relation_key,
            {
                "qq_affection": 10,
                "qq_coins": 0,
                "last_offering_date": "",
                "updated_at": int(time.time() * 1000),
            },
        )
        if not isinstance(relation, dict):
            relation = {}
            character_relations[relation_key] = relation
        relation.setdefault("qq_affection", 10)
        relation.setdefault("qq_coins", 0)
        relation.setdefault("last_offering_date", "")
        relation.setdefault("updated_at", int(time.time() * 1000))
        return relation

    def claim_daily_checkin(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        relation_user_id: str = "",
        date_key: str,
        coins: int = DEFAULT_CHECKIN_COINS,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Claim daily check-in coins per relation_user_id.

        Returns ``{"status": "ok"|"already", "coins_granted": int, "snapshot": dict}``.
        """
        date_key = str(date_key or "").strip()
        coins = max(1, int(coins or DEFAULT_CHECKIN_COINS))
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        rel_key = relation_user_id or profile_user_id
        with self._lock:
            state = self._load()
            relation = self._relation_entry(
                state, character_pack_id=character_pack_id, relation_user_id=rel_key
            )
            if str(relation.get("last_checkin_date") or "") == date_key:
                snapshot = self._snapshot(
                    state,
                    character_pack_id=character_pack_id,
                    client_mode="qq_text",
                    relation_user_id=rel_key,
                    now_ms=now_ms,
                )
                return {"status": "already", "coins_granted": 0, "date_key": date_key, "snapshot": snapshot}
            relation["qq_coins"] = _bounded_int(int(relation.get("qq_coins", 0)) + coins, 0, 999999)
            relation["last_checkin_date"] = date_key
            relation["updated_at"] = now_ms
            self._save(state)
            snapshot = self._snapshot(
                state,
                character_pack_id=character_pack_id,
                client_mode="qq_text",
                relation_user_id=rel_key,
                now_ms=now_ms,
            )
            return {"status": "ok", "coins_granted": coins, "date_key": date_key, "snapshot": snapshot}

    def purchase_item(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        relation_user_id: str = "",
        price: int,
        effects: dict[str, Any],
        client_mode: str = "qq_text",
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Atomically deduct price from client coins and apply item effects.

        Hunger and energy apply to the shared body pool.
        Affection applies only to the requesting ``relation_user_id`` (QQ mode).

        Returns ``{"status": "ok"|"insufficient_coins", "coins_before": int,
        "coins_after": int, "snapshot": dict}``.
        """
        price = max(0, int(price or 0))
        effects = effects if isinstance(effects, dict) else {}
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        rel_key = relation_user_id or profile_user_id
        with self._lock:
            state = self._load()
            body = self._body_entry(state, character_pack_id=character_pack_id)
            if _is_qq_mode(client_mode):
                relation = self._relation_entry(
                    state, character_pack_id=character_pack_id, relation_user_id=rel_key
                )
                coins_before = int(relation.get("qq_coins", 0))
            else:
                relation = None
                coins_before = int(body.get("coins", 0))
            if coins_before < price:
                snapshot = self._snapshot(
                    state,
                    character_pack_id=character_pack_id,
                    client_mode=client_mode,
                    relation_user_id=rel_key,
                    now_ms=now_ms,
                )
                return {
                    "status": "insufficient_coins",
                    "coins_before": coins_before,
                    "coins_needed": price,
                    "snapshot": snapshot,
                }
            if _is_qq_mode(client_mode):
                relation["qq_coins"] = coins_before - price
                relation["updated_at"] = now_ms
            else:
                body["coins"] = coins_before - price
            hunger_delta = int(effects.get("hunger", 0) or 0)
            energy_delta = int(effects.get("energy", 0) or 0)
            if hunger_delta:
                body["hunger"] = _bounded_int(body.get("hunger", 55) + hunger_delta, 0, 100)
            if energy_delta:
                body["energy"] = _bounded_int(body.get("energy", 70) + energy_delta, 0, 100)
            body["updated_at"] = now_ms
            affection_delta = int(effects.get("affection", 0) or 0)
            if affection_delta:
                if _is_qq_mode(client_mode):
                    if relation is None:
                        relation = self._relation_entry(
                            state, character_pack_id=character_pack_id, relation_user_id=rel_key
                        )
                    relation["qq_affection"] = _bounded_int(
                        relation.get("qq_affection", 10) + affection_delta, 0, 100
                    )
                    relation["updated_at"] = now_ms
                else:
                    body["desktop_affection"] = _bounded_int(
                        body.get("desktop_affection", 10) + affection_delta, 0, 100
                    )
            self._save(state)
            snapshot = self._snapshot(
                state,
                character_pack_id=character_pack_id,
                client_mode=client_mode,
                relation_user_id=rel_key,
                now_ms=now_ms,
            )
            coins_after = int(relation["qq_coins"] if _is_qq_mode(client_mode) else body["coins"])
            return {
                "status": "ok",
                "coins_before": coins_before,
                "coins_after": coins_after,
                "snapshot": snapshot,
            }

    def claim_daily_offering(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        relation_user_id: str = "",
        date_key: str,
        affection_bonus: int = 3,
        item_price: int = 0,
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
        date_key = str(date_key or "").strip()
        affection_bonus = max(0, int(affection_bonus or 0))
        item_price = max(0, int(item_price or 0))
        item_effects = item_effects if isinstance(item_effects, dict) else {}
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        rel_key = relation_user_id or profile_user_id
        with self._lock:
            state = self._load()
            relation = self._relation_entry(
                state, character_pack_id=character_pack_id, relation_user_id=rel_key
            )
            body = self._body_entry(state, character_pack_id=character_pack_id)
            if item_price > 0:
                coins_before = int(relation.get("qq_coins", 0))
                if coins_before < item_price:
                    snapshot = self._snapshot(
                        state,
                        character_pack_id=character_pack_id,
                        client_mode="qq_text",
                        relation_user_id=rel_key,
                        now_ms=now_ms,
                    )
                    return {
                        "status": "insufficient_coins",
                        "affection_granted": 0,
                        "daily_bonus": False,
                        "coins_before": coins_before,
                        "coins_needed": item_price,
                        "snapshot": snapshot,
                    }
                relation["qq_coins"] = coins_before - item_price
            is_daily_bonus = str(relation.get("last_offering_date") or "") != date_key
            hunger_delta = int(item_effects.get("hunger", 0) or 0)
            energy_delta = int(item_effects.get("energy", 0) or 0)
            if hunger_delta:
                body["hunger"] = _bounded_int(body.get("hunger", 55) + hunger_delta, 0, 100)
            if energy_delta:
                body["energy"] = _bounded_int(body.get("energy", 70) + energy_delta, 0, 100)
            if is_daily_bonus:
                item_affection = int(item_effects.get("affection", 0) or 0)
                affection_granted = item_affection + affection_bonus
                relation["last_offering_date"] = date_key
            else:
                affection_granted = 0
            if affection_granted:
                relation["qq_affection"] = _bounded_int(
                    int(relation.get("qq_affection", 10)) + affection_granted, 0, 100
                )
            body["updated_at"] = now_ms
            relation["updated_at"] = now_ms
            self._save(state)
            snapshot = self._snapshot(
                state,
                character_pack_id=character_pack_id,
                client_mode="qq_text",
                relation_user_id=rel_key,
                now_ms=now_ms,
            )
            return {
                "status": "ok" if is_daily_bonus else "already",
                "affection_granted": affection_granted,
                "daily_bonus": is_daily_bonus,
                "snapshot": snapshot,
            }

    def add_coins(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        amount: int,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Add coins to the shared body pool. Returns snapshot."""
        amount = max(0, int(amount or 0))
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        with self._lock:
            state = self._load()
            body = self._body_entry(state, character_pack_id=character_pack_id)
            body["coins"] = _bounded_int(body.get("coins", 0) + amount, 0, 999999)
            body["updated_at"] = now_ms
            self._save(state)
            return self._snapshot(
                state,
                character_pack_id=character_pack_id,
                client_mode="desktop_pet",
                relation_user_id=profile_user_id,
                now_ms=now_ms,
            )

    def _load(self) -> dict[str, Any]:
        if self._state is not None:
            return self._state
        state: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "characters": {}, "relations": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    state.update(loaded)
            except Exception:
                state = {"schema_version": SCHEMA_VERSION, "characters": {}, "relations": {}}
        state["schema_version"] = SCHEMA_VERSION
        if not isinstance(state.get("characters"), dict):
            state["characters"] = {}
        if not isinstance(state.get("relations"), dict):
            state["relations"] = {}
        self._state = state
        return state

    def _save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f"{self.path.name}.{uuid.uuid4().hex}.tmp")
        tmp_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(self.path)


def _is_qq_mode(client_mode: str) -> bool:
    return str(client_mode or "").strip().lower() == "qq_text"


def _safe_key(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "default"
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in text)[:120] or "default"


def _coerce_positive_int(value: Any, *, fallback: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = fallback
    return number if number > 0 else fallback


def _bounded_int(value: Any, minimum: int, maximum: int, *, fallback: int = 0) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = fallback
    return min(maximum, max(minimum, number))
