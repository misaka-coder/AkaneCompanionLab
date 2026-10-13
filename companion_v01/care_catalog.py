"""Care defaults, shop catalog, and seasonal availability."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger("companion_v01.care_runtime")

DESKTOP_AUTHORITY_VERSION = 1

MAX_DESKTOP_INVENTORY_ITEMS = 128

MAX_CARE_EVENT_LEDGER = 2048

DEFAULT_CHECKIN_COINS = 10

# Passive energy recovery: 30/h = 1 point per 2 minutes.
DEFAULT_ENERGY_RECOVERY_PER_HOUR: float = 30.0

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
    # ── 歪门邪道 ──────────────────────────────────────────────
    {
        "id": "hunger_zero_card",
        "name": "饥饿置零卡",
        "price": 15,
        "category": "trick",
        "preference_tags": ["trick"],
        "usable_in": ["qq"],
        "effects": {"hunger_set": 0},
        "description": "将饥饿度直接清零，让她立刻陷入极饿状态。后果自负。",
    },
    {
        "id": "energy_full_charm",
        "name": "精力满格符",
        "price": 20,
        "category": "charm",
        "preference_tags": ["charm"],
        "usable_in": ["qq"],
        "effects": {"energy_set": 100},
        "description": "将精力直接拉满，但饥饿不变。",
    },
    {
        "id": "reversal_card",
        "name": "逆转卡",
        "price": 12,
        "category": "trick",
        "preference_tags": ["trick"],
        "usable_in": ["qq"],
        "effects": {"hunger_energy_swap": True},
        "description": "将饥饿度和精力值互换。赌运气专用。",
    },
    {
        "id": "random_charm",
        "name": "随机符",
        "price": 5,
        "category": "charm",
        "preference_tags": ["charm"],
        "usable_in": ["qq"],
        "effects": {"random_vitals": True},
        "description": "随机对饥饿和精力各施加 ±25 的效果。效果完全随机。",
    },
    {
        "id": "full_restore_potion",
        "name": "全复活药水",
        "price": 55,
        "category": "potion",
        "preference_tags": ["potion"],
        "usable_in": ["qq"],
        "effects": {"hunger_set": 100, "energy_set": 100},
        "description": "将饥饿和精力同时拉满。价格不菲。",
    },
    {
        "id": "affection_gamble",
        "name": "好感赌注",
        "price": 8,
        "category": "charm",
        "preference_tags": ["charm"],
        "usable_in": ["qq"],
        "effects": {"random_affection": True},
        "description": "好感随机 +15 或 -10，七三开。",
    },
]

DEFAULT_THRESHOLDS = {
    "hunger_low": 25,
    "hunger_critical": 12,
    "energy_low": 25,
    "energy_critical": 12,
    "affection_familiar": 20,  # 陌生 → 熟悉
    "affection_warm": 45,  # 熟悉 → 亲近
    "affection_close": 70,  # 亲近 → 信任
    "affection_bond": 88,  # 信任 → 羁绊
}

_LUNAR_DATES: dict[str, dict[int, tuple[int, int]]] = {
    "春节": {
        2024: (2, 10),
        2025: (1, 29),
        2026: (2, 17),
        2027: (2, 6),
        2028: (1, 26),
        2029: (2, 13),
        2030: (2, 3),
    },
    "除夕": {
        2024: (2, 9),
        2025: (1, 28),
        2026: (2, 16),
        2027: (2, 5),
        2028: (1, 25),
        2029: (2, 12),
        2030: (2, 2),
    },
    "端午": {
        2024: (6, 10),
        2025: (5, 31),
        2026: (6, 19),
        2027: (6, 9),
        2028: (5, 28),
        2029: (6, 16),
        2030: (6, 6),
    },
    "七夕": {
        2024: (8, 10),
        2025: (8, 29),
        2026: (8, 19),
        2027: (8, 8),
        2028: (8, 26),
        2029: (8, 15),
        2030: (8, 5),
    },
    "中秋": {
        2024: (9, 17),
        2025: (10, 6),
        2026: (9, 25),
        2027: (9, 15),
        2028: (10, 3),
        2029: (9, 22),
        2030: (9, 12),
    },
    "重阳": {
        2024: (10, 11),
        2025: (10, 29),
        2026: (10, 18),
        2027: (10, 8),
        2028: (10, 26),
        2029: (10, 15),
        2030: (10, 5),
    },
}


def _in_lunar_window(now: datetime, key: str, half_days: int = 3) -> bool:
    table = _LUNAR_DATES.get(key, {})
    center = table.get(now.year)
    if center is None:
        return False
    center_date = datetime(now.year, center[0], center[1]).date()
    return abs((now.date() - center_date).days) <= half_days


_SEASONAL_EVENTS: list[dict[str, Any]] = [
    # ── 固定公历节日 ─────────────────────────────────────
    {
        "label": "元日",
        "emoji": "🎍",
        "start": 101,
        "end": 107,
        "items": [
            {
                "id": "newyear_fukubukuro",
                "name": "新年福袋",
                "price": 30,
                "category": "food",
                "usable_in": ["qq"],
                "seasonal": True,
                "effects": {"hunger": 25, "energy": 20, "affection": 5},
            }
        ],
    },
    {
        "label": "雛祭り",
        "emoji": "🎎",
        "start": 303,
        "end": 305,
        "items": [
            {
                "id": "hishi_mochi",
                "name": "菱饼",
                "price": 10,
                "category": "food",
                "usable_in": ["qq"],
                "seasonal": True,
                "effects": {"hunger": 15, "affection": 6},
            }
        ],
    },
    {
        "label": "樱花季",
        "emoji": "🌸",
        "start": 325,
        "end": 415,
        "items": [
            {
                "id": "sakura_mochi",
                "name": "樱饼",
                "price": 10,
                "category": "food",
                "usable_in": ["qq"],
                "seasonal": True,
                "effects": {"hunger": 18, "affection": 5},
            }
        ],
    },
    {
        "label": "清明",
        "emoji": "🌿",
        "start": 404,
        "end": 406,
        "items": [
            {
                "id": "qingtuan",
                "name": "青团",
                "price": 8,
                "category": "food",
                "usable_in": ["qq"],
                "seasonal": True,
                "effects": {"hunger": 22, "energy": 5},
            }
        ],
    },
    {
        "label": "お盆",
        "emoji": "🏮",
        "start": 813,
        "end": 816,
        "items": [
            {
                "id": "obon_offering",
                "name": "精灵供果",
                "price": 12,
                "category": "offering",
                "usable_in": ["qq"],
                "seasonal": True,
                "effects": {"affection": 6, "hunger": 10},
            }
        ],
    },
    {
        "label": "红叶季",
        "emoji": "🍁",
        "start": 1020,
        "end": 1120,
        "items": [
            {
                "id": "momiji_manju",
                "name": "红叶馒头",
                "price": 9,
                "category": "food",
                "usable_in": ["qq"],
                "seasonal": True,
                "effects": {"hunger": 20, "affection": 3},
            }
        ],
    },
    {
        "label": "冬至",
        "emoji": "❄️",
        "start": 1221,
        "end": 1223,
        "items": [
            {
                "id": "tangyuan",
                "name": "汤圆",
                "price": 8,
                "category": "food",
                "usable_in": ["qq"],
                "seasonal": True,
                "effects": {"hunger": 25, "energy": 8},
            }
        ],
    },
    {
        "label": "圣诞",
        "emoji": "🎄",
        "start": 1224,
        "end": 1226,
        "items": [
            {
                "id": "gingerbread",
                "name": "姜饼人",
                "price": 11,
                "category": "food",
                "usable_in": ["qq"],
                "seasonal": True,
                "effects": {"hunger": 20, "affection": 5},
            }
        ],
    },
    {
        "label": "跨年",
        "emoji": "🎆",
        "start": 1230,
        "end": 107,  # crosses year boundary
        "items": [
            {
                "id": "countdown_drink",
                "name": "跨年特饮",
                "price": 15,
                "category": "drink",
                "usable_in": ["qq"],
                "seasonal": True,
                "effects": {"energy": 40, "affection": 3},
            }
        ],
    },
    # ── 农历节日（按 _LUNAR_DATES 查找表） ──────────────
    {
        "label": "除夕",
        "emoji": "🧨",
        "lunar_key": "除夕",
        "half_days": 1,
        "items": [
            {
                "id": "new_year_eve_dumplings",
                "name": "年夜饺子",
                "price": 10,
                "category": "food",
                "usable_in": ["qq"],
                "seasonal": True,
                "effects": {"hunger": 30, "affection": 5},
            }
        ],
    },
    {
        "label": "春节",
        "emoji": "🧧",
        "lunar_key": "春节",
        "half_days": 5,
        "items": [
            {
                "id": "new_year_cake",
                "name": "年糕",
                "price": 12,
                "category": "food",
                "usable_in": ["qq"],
                "seasonal": True,
                "effects": {"hunger": 30, "affection": 4},
            }
        ],
    },
    {
        "label": "端午",
        "emoji": "🎋",
        "lunar_key": "端午",
        "half_days": 3,
        "items": [
            {
                "id": "zongzi",
                "name": "粽子",
                "price": 9,
                "category": "food",
                "usable_in": ["qq"],
                "seasonal": True,
                "effects": {"hunger": 28, "energy": 5},
            }
        ],
    },
    {
        "label": "七夕",
        "emoji": "⭐",
        "lunar_key": "七夕",
        "half_days": 2,
        "items": [
            {
                "id": "meteor_candy",
                "name": "流星糖",
                "price": 14,
                "category": "food",
                "usable_in": ["qq"],
                "seasonal": True,
                "effects": {"affection": 10, "hunger": 8},
            }
        ],
    },
    {
        "label": "中秋",
        "emoji": "🥮",
        "lunar_key": "中秋",
        "half_days": 3,
        "items": [
            {
                "id": "mooncake",
                "name": "月饼",
                "price": 13,
                "category": "food",
                "usable_in": ["qq"],
                "seasonal": True,
                "effects": {"hunger": 30, "affection": 6},
            }
        ],
    },
    {
        "label": "重阳",
        "emoji": "🌼",
        "lunar_key": "重阳",
        "half_days": 2,
        "items": [
            {
                "id": "chrysanthemum_cake",
                "name": "菊花糕",
                "price": 9,
                "category": "food",
                "usable_in": ["qq"],
                "seasonal": True,
                "effects": {"hunger": 15, "energy": 10},
            }
        ],
    },
]

_LUNAR_DATES_MAX_YEAR = 2030


def get_seasonal_shop_items(now: "datetime | None" = None) -> list[dict[str, Any]]:
    """Return shop items available for the current date based on seasonal windows.

    Lunar-festival items rely on a precomputed table up to 2030 (_LUNAR_DATES).
    In years beyond that, lunar items silently drop out; solar-fixed items are unaffected.
    """
    if now is None:
        now = datetime.now()
    if now.year > _LUNAR_DATES_MAX_YEAR:
        logger.warning(
            "get_seasonal_shop_items: year %d exceeds precomputed lunar table (%d); "
            "lunar seasonal items will not appear. Update _LUNAR_DATES to extend coverage.",
            now.year,
            _LUNAR_DATES_MAX_YEAR,
        )
    mmdd = now.month * 100 + now.day
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for event in _SEASONAL_EVENTS:
        if "lunar_key" in event:
            in_window = _in_lunar_window(now, event["lunar_key"], event.get("half_days", 3))
        else:
            start, end = event["start"], event["end"]
            in_window = (mmdd >= start and mmdd <= end) if start <= end else (mmdd >= start or mmdd <= end)
        if not in_window:
            continue
        for item in event.get("items", []):
            item_id = str(item.get("id") or "")
            if item_id and item_id not in seen_ids:
                seen_ids.add(item_id)
                result.append(dict(item, seasonal_label=event["label"], seasonal_emoji=event["emoji"]))
    return result
