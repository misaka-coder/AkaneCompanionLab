from __future__ import annotations

import json
import logging
import random
import threading
import time
import uuid

logger = logging.getLogger(__name__)
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "akane.care_runtime.v1"

DEFAULT_CHECKIN_COINS = 10
# Passive energy recovery per hour (server-side, QQ mode only): 30/h = 1 point per 2 minutes
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
    "affection_familiar": 20,   # 陌生 → 熟悉
    "affection_warm": 45,       # 熟悉 → 亲近
    "affection_close": 70,      # 亲近 → 信任
    "affection_bond": 88,       # 信任 → 羁绊
}


# ── Seasonal shop ────────────────────────────────────────────────────────────
# Windows use (month*100+day) integers. Cross-year windows: start > end.
# Precomputed solar dates for lunar festivals 2024-2030.
# Key: festival name, value: {year: (month, day)} of the central date.
# Lunar festival dates precomputed up to 2030.
# After 2030, lunar-based seasonal items silently stop appearing.
# To extend: look up the solar date for each festival for the new year and add it here.
_LUNAR_DATES: dict[str, dict[int, tuple[int, int]]] = {
    "春节": {
        2024: (2, 10), 2025: (1, 29), 2026: (2, 17), 2027: (2, 6),
        2028: (1, 26), 2029: (2, 13), 2030: (2, 3),
    },
    "除夕": {
        2024: (2, 9),  2025: (1, 28), 2026: (2, 16), 2027: (2, 5),
        2028: (1, 25), 2029: (2, 12), 2030: (2, 2),
    },
    "端午": {
        2024: (6, 10), 2025: (5, 31), 2026: (6, 19), 2027: (6, 9),
        2028: (5, 28), 2029: (6, 16), 2030: (6, 6),
    },
    "七夕": {
        2024: (8, 10), 2025: (8, 29), 2026: (8, 19), 2027: (8, 8),
        2028: (8, 26), 2029: (8, 15), 2030: (8, 5),
    },
    "中秋": {
        2024: (9, 17), 2025: (10, 6), 2026: (9, 25), 2027: (9, 15),
        2028: (10, 3), 2029: (9, 22), 2030: (9, 12),
    },
    "重阳": {
        2024: (10, 11), 2025: (10, 29), 2026: (10, 18), 2027: (10, 8),
        2028: (10, 26), 2029: (10, 15), 2030: (10, 5),
    },
}


def _in_lunar_window(now: datetime, key: str, half_days: int = 3) -> bool:
    table = _LUNAR_DATES.get(key, {})
    center = table.get(now.year)
    if center is None:
        return False
    center_date = datetime(now.year, center[0], center[1]).date()
    return abs((now.date() - center_date).days) <= half_days


# Seasonal events table.
# Fixed solar events: use "start"/"end" as MMDD integers (cross-year if start > end).
# Lunar events: use "lunar_key" + "half_days" — matched against _LUNAR_DATES lookup.
_SEASONAL_EVENTS: list[dict[str, Any]] = [
    # ── 固定公历节日 ─────────────────────────────────────
    {
        "label": "元日", "emoji": "🎍",
        "start": 101, "end": 107,
        "items": [{
            "id": "newyear_fukubukuro", "name": "新年福袋", "price": 30,
            "category": "food", "usable_in": ["qq"], "seasonal": True,
            "effects": {"hunger": 25, "energy": 20, "affection": 5},
        }],
    },
    {
        "label": "雛祭り", "emoji": "🎎",
        "start": 303, "end": 305,
        "items": [{
            "id": "hishi_mochi", "name": "菱饼", "price": 10,
            "category": "food", "usable_in": ["qq"], "seasonal": True,
            "effects": {"hunger": 15, "affection": 6},
        }],
    },
    {
        "label": "樱花季", "emoji": "🌸",
        "start": 325, "end": 415,
        "items": [{
            "id": "sakura_mochi", "name": "樱饼", "price": 10,
            "category": "food", "usable_in": ["qq"], "seasonal": True,
            "effects": {"hunger": 18, "affection": 5},
        }],
    },
    {
        "label": "清明", "emoji": "🌿",
        "start": 404, "end": 406,
        "items": [{
            "id": "qingtuan", "name": "青团", "price": 8,
            "category": "food", "usable_in": ["qq"], "seasonal": True,
            "effects": {"hunger": 22, "energy": 5},
        }],
    },
    {
        "label": "お盆", "emoji": "🏮",
        "start": 813, "end": 816,
        "items": [{
            "id": "obon_offering", "name": "精灵供果", "price": 12,
            "category": "offering", "usable_in": ["qq"], "seasonal": True,
            "effects": {"affection": 6, "hunger": 10},
        }],
    },
    {
        "label": "红叶季", "emoji": "🍁",
        "start": 1020, "end": 1120,
        "items": [{
            "id": "momiji_manju", "name": "红叶馒头", "price": 9,
            "category": "food", "usable_in": ["qq"], "seasonal": True,
            "effects": {"hunger": 20, "affection": 3},
        }],
    },
    {
        "label": "冬至", "emoji": "❄️",
        "start": 1221, "end": 1223,
        "items": [{
            "id": "tangyuan", "name": "汤圆", "price": 8,
            "category": "food", "usable_in": ["qq"], "seasonal": True,
            "effects": {"hunger": 25, "energy": 8},
        }],
    },
    {
        "label": "圣诞", "emoji": "🎄",
        "start": 1224, "end": 1226,
        "items": [{
            "id": "gingerbread", "name": "姜饼人", "price": 11,
            "category": "food", "usable_in": ["qq"], "seasonal": True,
            "effects": {"hunger": 20, "affection": 5},
        }],
    },
    {
        "label": "跨年", "emoji": "🎆",
        "start": 1230, "end": 107,   # crosses year boundary
        "items": [{
            "id": "countdown_drink", "name": "跨年特饮", "price": 15,
            "category": "drink", "usable_in": ["qq"], "seasonal": True,
            "effects": {"energy": 40, "affection": 3},
        }],
    },
    # ── 农历节日（按 _LUNAR_DATES 查找表） ──────────────
    {
        "label": "除夕", "emoji": "🧨",
        "lunar_key": "除夕", "half_days": 1,
        "items": [{
            "id": "new_year_eve_dumplings", "name": "年夜饺子", "price": 10,
            "category": "food", "usable_in": ["qq"], "seasonal": True,
            "effects": {"hunger": 30, "affection": 5},
        }],
    },
    {
        "label": "春节", "emoji": "🧧",
        "lunar_key": "春节", "half_days": 5,
        "items": [{
            "id": "new_year_cake", "name": "年糕", "price": 12,
            "category": "food", "usable_in": ["qq"], "seasonal": True,
            "effects": {"hunger": 30, "affection": 4},
        }],
    },
    {
        "label": "端午", "emoji": "🎋",
        "lunar_key": "端午", "half_days": 3,
        "items": [{
            "id": "zongzi", "name": "粽子", "price": 9,
            "category": "food", "usable_in": ["qq"], "seasonal": True,
            "effects": {"hunger": 28, "energy": 5},
        }],
    },
    {
        "label": "七夕", "emoji": "⭐",
        "lunar_key": "七夕", "half_days": 2,
        "items": [{
            "id": "meteor_candy", "name": "流星糖", "price": 14,
            "category": "food", "usable_in": ["qq"], "seasonal": True,
            "effects": {"affection": 10, "hunger": 8},
        }],
    },
    {
        "label": "中秋", "emoji": "🥮",
        "lunar_key": "中秋", "half_days": 3,
        "items": [{
            "id": "mooncake", "name": "月饼", "price": 13,
            "category": "food", "usable_in": ["qq"], "seasonal": True,
            "effects": {"hunger": 30, "affection": 6},
        }],
    },
    {
        "label": "重阳", "emoji": "🌼",
        "lunar_key": "重阳", "half_days": 2,
        "items": [{
            "id": "chrysanthemum_cake", "name": "菊花糕", "price": 9,
            "category": "food", "usable_in": ["qq"], "seasonal": True,
            "effects": {"hunger": 15, "energy": 10},
        }],
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
            desktop_tier_event = None
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
                    aff_before = int(body.get("desktop_affection", 10))
                    aff_after = _bounded_int(
                        care_payload.get("affection"), 0, 100, fallback=aff_before
                    )
                    tier_before = _affection_tier(aff_before)
                    tier_after = _affection_tier(aff_after)
                    if tier_before != tier_after:
                        desktop_tier_event = {
                            "from_tier": tier_before,
                            "to_tier": tier_after,
                            "direction": "up" if aff_after > aff_before else "down",
                        }
                    body["desktop_affection"] = aff_after
            body["updated_at"] = now_ms
            body["last_client_sync_at"] = now_ms
            self._save(state)
            snap = self._snapshot(
                state,
                character_pack_id=character_pack_id,
                client_mode=client_mode,
                relation_user_id=relation_user_id or profile_user_id,
                now_ms=now_ms,
            )
            if desktop_tier_event:
                snap["pending_tier_event"] = desktop_tier_event
            return snap

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
            pending_tier_event = None
            if _is_qq_mode(client_mode):
                relation = self._relation_entry(
                    state,
                    character_pack_id=character_pack_id,
                    relation_user_id=relation_user_id or profile_user_id,
                )
                self._apply_body_time_decay(
                    body,
                    now_ms,
                    hunger_per_hour=hunger_per_hour if hunger_per_hour is not None else 8.0,
                )
                # Consume pending tier event (one-shot: read and clear atomically before save)
                pending_tier_event = relation.pop("pending_tier_event", None)
            self._save(state)
            snap = self._snapshot(
                state,
                character_pack_id=character_pack_id,
                client_mode=client_mode,
                relation_user_id=relation_user_id or profile_user_id,
                now_ms=now_ms,
            )
            if pending_tier_event:
                snap["pending_tier_event"] = pending_tier_event
            return snap

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
                aff_before = _bounded_int(relation.get("qq_affection"), 0, 100, fallback=10)
                aff_after = _bounded_int(aff_before + delta_value, 0, 100, fallback=aff_before)
                relation["qq_affection"] = aff_after
                relation["updated_at"] = now_ms
                # Detect tier crossing and store a one-shot pending event
                tier_before = _affection_tier(aff_before)
                tier_after = _affection_tier(aff_after)
                if tier_before != tier_after:
                    relation["pending_tier_event"] = {
                        "from_tier": tier_before,
                        "to_tier": tier_after,
                        "direction": "up" if delta_value > 0 else "down",
                    }
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
            inventory = {k: dict(v) for k, v in (relation.get("inventory") or {}).items() if isinstance(v, dict)}
            checkin_streak = int(relation.get("checkin_streak") or 0)
            relation_anchors = dict(relation.get("anchors") or {})
        else:
            affection = _bounded_int(body.get("desktop_affection"), 0, 100, fallback=10)
            affection_scope = "desktop_pet"
            coins = _bounded_int(body.get("coins"), 0, 999999, fallback=20)
            last_offering_date = ""
            inventory = {}
            checkin_streak = 0
            desktop_rel = self._relation_entry(
                state, character_pack_id=character_pack_id, relation_user_id=relation_user_id
            )
            relation_anchors = dict(desktop_rel.get("anchors") or {})
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
            "checkin_streak": checkin_streak,
            "last_offering_date": last_offering_date,
            "inventory": inventory,
            "thresholds": dict(DEFAULT_THRESHOLDS),
            "anchors": relation_anchors,
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
        # Use fractional accumulators so sub-1-point changes aren't lost across short intervals
        hunger_frac = float(body.get("hunger_frac") or 0.0)
        energy_frac = float(body.get("energy_frac") or 0.0)
        raw_hunger = hunger - hunger_per_hour * elapsed_hours + hunger_frac
        raw_energy = energy + energy_recovery_per_hour * elapsed_hours + energy_frac
        new_hunger = max(0.0, min(100.0, raw_hunger))
        new_energy = max(0.0, min(100.0, raw_energy))
        body["hunger"] = int(new_hunger)
        body["energy"] = int(new_energy)
        body["hunger_frac"] = new_hunger - int(new_hunger)
        body["energy_frac"] = new_energy - int(new_energy)
        body["vitals_updated_at_ms"] = now_ms

    def record_turn(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        relation_user_id: str = "",
        now_ms: int | None = None,
    ) -> None:
        """Update per-turn memory anchors: first_seen_ms, total_turns, late_night_turns."""
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        with self._lock:
            state = self._load()
            relation = self._relation_entry(
                state,
                character_pack_id=character_pack_id,
                relation_user_id=relation_user_id or profile_user_id,
            )
            anchors = relation.setdefault("anchors", {})
            if "first_seen_ms" not in anchors:
                anchors["first_seen_ms"] = now_ms
            anchors["total_turns"] = int(anchors.get("total_turns") or 0) + 1
            try:
                hour = datetime.fromtimestamp(now_ms / 1000).hour
                if hour >= 22 or hour < 4:
                    anchors["late_night_turns"] = int(anchors.get("late_night_turns") or 0) + 1
            except Exception:
                pass
            relation["updated_at"] = now_ms
            self._save(state)

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

    def buy_to_inventory(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        relation_user_id: str = "",
        item_id: str,
        item_name: str,
        price: int,
        count: int = 1,
        item_effects: dict[str, Any] | None = None,
        item_category: str = "",
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Deduct coins and place item(s) in user's personal inventory. Does not apply item effects."""
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
            relation = self._relation_entry(
                state,
                character_pack_id=character_pack_id,
                relation_user_id=relation_user_id or profile_user_id,
            )
            coins_before = _bounded_int(relation.get("qq_coins"), 0, 999999, fallback=0)
            if coins_before < total_price:
                return {
                    "status": "insufficient_coins",
                    "coins_before": coins_before,
                    "coins_needed": total_price,
                    "snapshot": self._snapshot(
                        state,
                        character_pack_id=character_pack_id,
                        client_mode="qq_text",
                        relation_user_id=relation_user_id or profile_user_id,
                        now_ms=now_ms,
                    ),
                }
            relation["qq_coins"] = coins_before - total_price
            inventory = relation.setdefault("inventory", {})
            if item_id in inventory and isinstance(inventory[item_id], dict):
                inventory[item_id]["count"] = inventory[item_id].get("count", 0) + count
                if item_effects:
                    inventory[item_id]["effects"] = dict(item_effects)
            else:
                entry_data: dict[str, Any] = {"name": item_name, "count": count}
                if item_effects:
                    entry_data["effects"] = dict(item_effects)
                if item_category:
                    entry_data["category"] = str(item_category)
                inventory[item_id] = entry_data
            relation["updated_at"] = now_ms
            self._save(state)
            return {
                "status": "ok",
                "coins_before": coins_before,
                "coins_after": relation["qq_coins"],
                "item_id": item_id,
                "item_name": item_name,
                "item_count": inventory[item_id]["count"],
                "snapshot": self._snapshot(
                    state,
                    character_pack_id=character_pack_id,
                    client_mode="qq_text",
                    relation_user_id=relation_user_id or profile_user_id,
                    now_ms=now_ms,
                ),
            }

    def use_from_inventory(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        relation_user_id: str = "",
        item_id: str,
        item_effects: dict[str, Any] | None = None,
        count: int = 1,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Consume item(s) from user's inventory and apply scaled effects to shared body."""
        item_id = str(item_id or "").strip()
        effects = dict(item_effects or {})
        count = max(1, min(99, int(count or 1)))
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        with self._lock:
            state = self._load()
            body = self._body_entry(state, character_pack_id=character_pack_id)
            relation = self._relation_entry(
                state,
                character_pack_id=character_pack_id,
                relation_user_id=relation_user_id or profile_user_id,
            )
            inventory = relation.get("inventory") or {}
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
            aff_before = _bounded_int(relation.get("qq_affection"), 0, 100, fallback=10)
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
            # Record first-ever feed anchor (only written once)
            anchors = relation.setdefault("anchors", {})
            if "first_fed" not in anchors:
                anchors["first_fed"] = {"name": item_name, "ms": now_ms}
            relation["updated_at"] = now_ms
            self._save(state)
            return {
                "status": "ok",
                "item_id": item_id,
                "item_name": item_name,
                "count_used": count,
                "effects_applied": effects,
                "snapshot": self._snapshot(
                    state,
                    character_pack_id=character_pack_id,
                    client_mode="qq_text",
                    relation_user_id=relation_user_id or profile_user_id,
                    now_ms=now_ms,
                ),
            }

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
        relation.setdefault("inventory", {})
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
                return {
                    "status": "already",
                    "coins_granted": 0,
                    "date_key": date_key,
                    "streak": int(relation.get("checkin_streak") or 1),
                    "snapshot": snapshot,
                }

            # ── Streak calculation ────────────────────────────────────
            last_date_str = str(relation.get("last_checkin_date") or "")
            current_streak = int(relation.get("checkin_streak") or 0)
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

            relation["qq_coins"] = _bounded_int(int(relation.get("qq_coins", 0)) + actual_coins, 0, 999999)
            relation["last_checkin_date"] = date_key
            relation["checkin_streak"] = new_streak
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

    def draw_fortune_slip(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        relation_user_id: str = "",
        slip_cost: int = 5,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Draw a shrine fortune slip (御神签). Deducts cost, rolls fortune, applies effects atomically.

        Fortune table (cumulative probability):
          大吉 10% → +25 coins, affection +3
          中吉 25% → +12 coins
          小吉 30% → +6 coins
          末吉 25% → 0 return
          凶   10% → -3 extra coins (total loss = cost + 3)
        """
        FORTUNES = [
            {"name": "大吉", "prob": 0.10, "coins_delta": 25, "affection_delta": 3},
            {"name": "中吉", "prob": 0.25, "coins_delta": 12, "affection_delta": 0},
            {"name": "小吉", "prob": 0.30, "coins_delta": 6,  "affection_delta": 0},
            {"name": "末吉", "prob": 0.25, "coins_delta": 0,  "affection_delta": 0},
            {"name": "凶",   "prob": 0.10, "coins_delta": -3, "affection_delta": 0},
        ]
        slip_cost = max(1, int(slip_cost))
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        rel_key = relation_user_id or profile_user_id
        with self._lock:
            state = self._load()
            relation = self._relation_entry(
                state, character_pack_id=character_pack_id, relation_user_id=rel_key
            )
            coins_before = _bounded_int(relation.get("qq_coins"), 0, 999999, fallback=0)
            if coins_before < slip_cost:
                return {
                    "status": "insufficient_coins",
                    "coins_before": coins_before,
                    "coins_needed": slip_cost,
                }
            # Roll fortune
            roll = random.random()
            cumulative = 0.0
            fortune = FORTUNES[-1]
            for f in FORTUNES:
                cumulative += f["prob"]
                if roll < cumulative:
                    fortune = f
                    break
            # Apply coin effects: deduct cost then add fortune reward
            net_coins = fortune["coins_delta"] - slip_cost
            new_coins = max(0, min(999999, coins_before + net_coins))
            relation["qq_coins"] = new_coins
            # Apply affection
            if fortune["affection_delta"] != 0:
                aff_before_f = _bounded_int(relation.get("qq_affection"), 0, 100, fallback=10)
                aff_after_f = max(0, min(100, aff_before_f + fortune["affection_delta"]))
                _detect_and_store_tier_event(relation, aff_before_f, aff_after_f)
                relation["qq_affection"] = aff_after_f
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
                "status": "ok",
                "fortune": fortune["name"],
                "coins_delta": fortune["coins_delta"],
                "affection_delta": fortune["affection_delta"],
                "slip_cost": slip_cost,
                "net_coins": net_coins,
                "coins_before": coins_before,
                "coins_after": new_coins,
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
                aff_before_o = _bounded_int(int(relation.get("qq_affection", 10)), 0, 100)
                aff_after_o = _bounded_int(aff_before_o + affection_granted, 0, 100)
                _detect_and_store_tier_event(relation, aff_before_o, aff_after_o)
                relation["qq_affection"] = aff_after_o
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
