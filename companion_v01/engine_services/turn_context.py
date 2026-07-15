"""Turn context parsing helpers extracted from engine.py.

Pure functions that extract profile/session/character/client metadata
from a turn payload without referencing engine internals.
"""

from __future__ import annotations
from typing import Any


def resolve_payload_character_pack_id(payload):
    for key in ("character_pack_id", "characterPackId", "character_pack"):
        value = str((payload or {}).get(key) or "").strip()
        if value:
            return value
    current_visual = (payload or {}).get("current_visual")
    if isinstance(current_visual, dict):
        for key in ("character_pack_id", "characterPackId", "character_pack"):
            value = str(current_visual.get(key) or "").strip()
            if value:
                return value
        character = current_visual.get("character")
        if isinstance(character, dict):
            for key in ("character_pack_id", "characterPackId", "character_pack", "pack_id"):
                value = str(character.get(key) or "").strip()
                if value:
                    return value
    return ""


def resolve_turn_actor(payload):
    source = payload if isinstance(payload, dict) else {}
    stable_id = str(source.get("actor_stable_id") or "").strip()
    if not stable_id:
        return "", ""
    display_name = str(source.get("actor_display_name") or "").strip()
    return stable_id[:160], display_name[:160]


def coerce_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return None


def is_transient_user_turn(payload):
    turn_kind = str(payload.get("turn_kind") or payload.get("client_turn_kind") or "").strip().lower()
    return bool(payload.get("transient_user_message")) or turn_kind in {
        "desktop_pet_proactive",
        "proactive",
    }


def build_transient_user_record(*, user_message, now_ts, date_label, time_of_day):
    return {
        "source_id": "",
        "role": "user",
        "content": user_message,
        "timestamp": now_ts,
        "date_label": date_label,
        "time_of_day": time_of_day,
        "semantic_tags": [],
    }


def extract_desktop_screen_frame_images(payload):
    frames = payload.get("desktop_screen_frames") if isinstance(payload, dict) else None
    if not isinstance(frames, list):
        return []
    images = []
    for item in frames[-5:]:
        if not isinstance(item, dict):
            continue
        data_url = str(item.get("data_url") or item.get("dataUrl") or "").strip()
        if not data_url.startswith("data:image/") or len(data_url) > 2_000_000:
            continue
        images.append(
            {
                "data_url": data_url,
                "captured_at": int(item.get("captured_at") or item.get("capturedAt") or 0),
                "width": int(float(item.get("width") or 0)),
                "height": int(float(item.get("height") or 0)),
            }
        )
    return images


def build_desktop_screen_frame_prompt_context(frames):
    usable = [f for f in frames if str(f.get("data_url") or "").startswith("data:image/")]
    if not usable:
        return ""
    first_ts = int(usable[0].get("captured_at") or 0)
    last_ts = int(usable[-1].get("captured_at") or 0)
    duration = max(0, last_ts - first_ts)
    duration_text = f"，大约是最近 {duration} 秒里的变化" if duration > 0 else ""
    return "\n".join(
        [
            "【刚才一起看到的情况】",
            f"你刚才在主人旁边看了几眼{duration_text}。",
            "请优先贴着能看清的具体内容回应，像一起看视频、打游戏或做事时顺着眼前的小事接话。",
            "不要只泛泛地说主人看得认真或还在看同一个东西；看不清的地方就轻轻带过，别把拿不准的内容说死，也不要解释自己是怎么看到的。",
        ]
    )
