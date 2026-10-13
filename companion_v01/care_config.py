"""Care configuration and legacy desktop input normalization."""

from __future__ import annotations

from typing import Any

from .care_catalog import MAX_DESKTOP_INVENTORY_ITEMS


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


def normalize_desktop_care_config(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    decay_source = source.get("decay") if isinstance(source.get("decay"), dict) else {}
    work_source = source.get("work") if isinstance(source.get("work"), dict) else {}
    allowance_source = source.get("allowance") if isinstance(source.get("allowance"), dict) else {}

    reward_min = _bounded_int(
        work_source.get("reward_coins_min", work_source.get("rewardCoinsMin")),
        0,
        999999,
        fallback=5,
    )
    reward_max = _bounded_int(
        work_source.get("reward_coins_max", work_source.get("rewardCoinsMax")),
        0,
        999999,
        fallback=reward_min,
    )

    shop_items: list[dict[str, Any]] = []
    raw_items = source.get("shop_items", source.get("shopItems"))
    if isinstance(raw_items, list):
        for raw_item in raw_items[:MAX_DESKTOP_INVENTORY_ITEMS]:
            if not isinstance(raw_item, dict):
                continue
            item_id = str(raw_item.get("id") or "").strip()[:120]
            item_name = str(raw_item.get("name") or item_id).strip()[:160]
            usable_in = raw_item.get("usable_in", raw_item.get("usableIn"))
            usable_modes = (
                [str(item).strip().lower() for item in usable_in if str(item).strip()]
                if isinstance(usable_in, list)
                else []
            )
            if not item_id or (usable_modes and "desktop_pet" not in usable_modes):
                continue
            effects_source = raw_item.get("effects") if isinstance(raw_item.get("effects"), dict) else {}
            shop_items.append(
                {
                    "id": item_id,
                    "name": item_name or item_id,
                    "description": str(raw_item.get("description") or "").strip()[:120],
                    "price": _bounded_int(raw_item.get("price"), 0, 999999, fallback=0),
                    "effects": {
                        "hunger": _bounded_int(effects_source.get("hunger"), -100, 100, fallback=0),
                        "energy": _bounded_int(effects_source.get("energy"), -100, 100, fallback=0),
                        "affection": _bounded_int(effects_source.get("affection"), -100, 100, fallback=0),
                    },
                }
            )

    return {
        "enabled": bool(source.get("enabled")),
        "initial_coins": _bounded_int(
            source.get("initial_coins", source.get("initialCoins")),
            0,
            999999,
            fallback=20,
        ),
        "initial_hunger": _bounded_int(
            source.get("initial_hunger", source.get("initialHunger")),
            0,
            100,
            fallback=55,
        ),
        "initial_energy": _bounded_int(
            source.get("initial_energy", source.get("initialEnergy")),
            0,
            100,
            fallback=70,
        ),
        "initial_affection": _bounded_int(
            source.get("initial_affection", source.get("initialAffection")),
            0,
            100,
            fallback=10,
        ),
        "decay": {
            "hunger_per_hour": _bounded_int(
                decay_source.get("hunger_per_hour", decay_source.get("hungerPerHour")),
                0,
                100,
                fallback=4,
            ),
            "energy_per_reply": _bounded_int(
                decay_source.get("energy_per_reply", decay_source.get("energyPerReply")),
                0,
                20,
                fallback=1,
            ),
            "energy_per_proactive": _bounded_int(
                decay_source.get("energy_per_proactive", decay_source.get("energyPerProactive")),
                0,
                20,
                fallback=0,
            ),
        },
        "work": {
            "enabled": bool(work_source.get("enabled")),
            "duration_seconds": _bounded_int(
                work_source.get("duration_seconds", work_source.get("durationSeconds")),
                1,
                3600,
                fallback=20,
            ),
            "reward_coins_min": min(reward_min, reward_max),
            "reward_coins_max": max(reward_min, reward_max),
            "min_hunger": _bounded_int(
                work_source.get("min_hunger", work_source.get("minHunger")),
                0,
                100,
                fallback=20,
            ),
            "min_energy": _bounded_int(
                work_source.get("min_energy", work_source.get("minEnergy")),
                0,
                100,
                fallback=25,
            ),
            "hunger_cost": _bounded_int(
                work_source.get("hunger_cost", work_source.get("hungerCost")),
                0,
                100,
                fallback=12,
            ),
            "energy_cost": _bounded_int(
                work_source.get("energy_cost", work_source.get("energyCost")),
                0,
                100,
                fallback=25,
            ),
        },
        "allowance": {
            "enabled": bool(allowance_source.get("enabled")),
            "coins": _bounded_int(allowance_source.get("coins"), 1, 999999, fallback=4),
            "cooldown_seconds": _bounded_int(
                allowance_source.get("cooldown_seconds", allowance_source.get("cooldownSeconds")),
                0,
                86400,
                fallback=300,
            ),
            "max_coins": _bounded_int(
                allowance_source.get("max_coins", allowance_source.get("maxCoins")),
                1,
                999999,
                fallback=6,
            ),
        },
        "shop_items": shop_items,
    }


def normalize_desktop_legacy_state(value: Any, *, config: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    inventory_source = value.get("inventory") if isinstance(value.get("inventory"), dict) else {}
    inventory: dict[str, int] = {}
    allowed_item_ids = {item["id"] for item in config["shop_items"]}
    for raw_id, raw_count in list(inventory_source.items())[:MAX_DESKTOP_INVENTORY_ITEMS]:
        item_id = str(raw_id or "").strip()
        count = _bounded_int(raw_count, 0, 999, fallback=0)
        if item_id in allowed_item_ids and count > 0:
            inventory[item_id] = count
    return {
        "coins": _bounded_int(value.get("coins"), 0, 999999, fallback=config["initial_coins"]),
        "hunger": _bounded_int(value.get("hunger"), 0, 100, fallback=config["initial_hunger"]),
        "energy": _bounded_int(value.get("energy"), 0, 100, fallback=config["initial_energy"]),
        "affection": _bounded_int(
            value.get("affection"),
            0,
            100,
            fallback=config["initial_affection"],
        ),
        "inventory": inventory,
        "work_task": _normalize_work_task(value.get("work_task", value.get("workTask"))),
        "last_allowance_at": max(
            0,
            int(value.get("last_allowance_at", value.get("lastAllowanceAt")) or 0),
        ),
    }


def _initial_desktop_state(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "coins": config["initial_coins"],
        "hunger": config["initial_hunger"],
        "energy": config["initial_energy"],
        "affection": config["initial_affection"],
        "inventory": {},
        "work_task": None,
        "last_allowance_at": 0,
    }


def _normalize_work_task(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    complete_at = max(0, int(value.get("complete_at", value.get("completeAt")) or 0))
    if complete_at <= 0:
        return None
    return {
        "status": "active",
        "started_at": max(0, int(value.get("started_at", value.get("startedAt")) or 0)),
        "complete_at": complete_at,
        "reward_coins": _bounded_int(
            value.get("reward_coins", value.get("rewardCoins")),
            0,
            999999,
            fallback=0,
        ),
    }
