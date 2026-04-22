from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Iterable

from .persona_config import PERSONA

TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+")
_HASHED_EMBEDDING_PROVIDERS: dict[int, Any] = {}

STOPWORDS = {
    "的",
    "了",
    "呢",
    "啊",
    "呀",
    "吗",
    "吧",
    "哦",
    "喵",
    "我",
    "你",
    "他",
    "她",
    "它",
    "我们",
    "你们",
    "他们",
    "然后",
    "就是",
    "这个",
    "那个",
    "现在",
    "一下",
}


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def tokenize(text: str) -> list[str]:
    raw = normalize_text(text)
    if not raw:
        return []

    tokens: list[str] = []
    for match in TOKEN_RE.findall(raw.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", match):
            if len(match) <= 3:
                tokens.append(match)
            else:
                tokens.append(match)
                for size in (2, 3):
                    for idx in range(0, max(0, len(match) - size + 1)):
                        tokens.append(match[idx : idx + size])
        else:
            tokens.append(match)
    return [item for item in tokens if item and item not in STOPWORDS]


def extract_semantic_tags(text: str, limit: int = 8) -> list[str]:
    counts = Counter(tokenize(text))
    ranked = sorted(
        counts.items(),
        key=lambda item: (-item[1], -len(item[0]), item[0]),
    )
    return [token for token, _ in ranked[:limit]]


def hashed_embedding(text: str, dim: int = 128) -> list[float]:
    dimension = max(1, int(dim))
    provider = _HASHED_EMBEDDING_PROVIDERS.get(dimension)
    if provider is None:
        from .embedding_provider import HashedEmbeddingProvider

        provider = HashedEmbeddingProvider(dimension=dimension)
        _HASHED_EMBEDDING_PROVIDERS[dimension] = provider
    return provider.embed_text(text)


def cosine_similarity(vec_a: Iterable[float], vec_b: Iterable[float]) -> float:
    a = list(vec_a)
    b = list(vec_b)
    if not a or not b or len(a) != len(b):
        return 0.0
    return float(sum(x * y for x, y in zip(a, b)))


def infer_time_of_day(timestamp: int | float) -> str:
    hour = datetime.fromtimestamp(float(timestamp)).hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 24:
        return "night"
    return "midnight"


def detect_time_of_day_from_text(text: str) -> str | None:
    raw = str(text or "")
    if any(word in raw for word in ("上午", "清晨", "早上", "morning")):
        return "morning"
    if any(word in raw for word in ("下午", "午后", "afternoon")):
        return "afternoon"
    if any(word in raw for word in ("晚上", "今晚", "夜里", "夜晚", "night")):
        return "night"
    if any(word in raw for word in ("凌晨", "半夜", "midnight")):
        return "midnight"
    return None


def timestamp_to_date_label(timestamp: int | float) -> str:
    return datetime.fromtimestamp(float(timestamp)).strftime("%Y-%m-%d")


def timestamp_to_datetime_label(timestamp: int | float) -> str:
    return datetime.fromtimestamp(float(timestamp)).strftime("%Y-%m-%d %H:%M")


def timestamp_to_time_label(timestamp: int | float) -> str:
    return datetime.fromtimestamp(float(timestamp)).strftime("%H:%M")


def resolve_reminder_due_timestamp(
    *,
    now_ts: int | float,
    time_text: str = "",
    date_label: str | None = None,
    time_of_day: str | None = None,
    hour: int | None = None,
    minute: int | None = None,
    offset_minutes: int | None = None,
) -> int | None:
    raw_time_text = normalize_text(time_text)
    lowered_time_text = raw_time_text.lower()
    explicit_date = _normalize_date_label(date_label)
    explicit_hour = _normalize_hour(hour)
    explicit_minute = _normalize_minute(minute)
    explicit_offset_minutes = _normalize_positive_int(offset_minutes)
    inferred_time_of_day = _normalize_time_of_day(time_of_day) or detect_time_of_day_from_text(raw_time_text)

    relative_offset_seconds = _resolve_relative_offset_seconds(
        raw_time_text,
        explicit_offset_minutes=explicit_offset_minutes,
    )
    if relative_offset_seconds is not None:
        return int(float(now_ts) + relative_offset_seconds)

    if _contains_ambiguous_reminder_time(lowered_time_text):
        return None

    parsed_hour, parsed_minute = _extract_clock_time(raw_time_text)
    final_hour = explicit_hour if explicit_hour is not None else parsed_hour
    final_minute = explicit_minute if explicit_minute is not None else parsed_minute

    if final_hour is None:
        final_hour = _default_hour_for_time_of_day(inferred_time_of_day)
        final_minute = 0 if final_minute is None else final_minute
    if final_hour is None:
        return None

    final_hour = _apply_cn_time_period_bias(
        final_hour,
        text=lowered_time_text,
        time_of_day=inferred_time_of_day,
    )
    if final_hour is None or not 0 <= final_hour <= 23:
        return None
    if final_minute is None:
        final_minute = 0
    if not 0 <= final_minute <= 59:
        return None

    now_dt = datetime.fromtimestamp(float(now_ts))
    candidate_date = explicit_date or _infer_relative_date_label(raw_time_text, now_dt=now_dt)
    date_was_explicit = explicit_date is not None or candidate_date is not None
    if candidate_date is None:
        candidate_dt = now_dt.replace(
            hour=final_hour,
            minute=final_minute,
            second=0,
            microsecond=0,
        )
        if candidate_dt.timestamp() <= float(now_ts):
            candidate_dt += timedelta(days=1)
        return int(candidate_dt.timestamp())

    try:
        base_dt = datetime.strptime(candidate_date, "%Y-%m-%d")
    except ValueError:
        return None
    candidate_dt = base_dt.replace(hour=final_hour, minute=final_minute, second=0, microsecond=0)
    if candidate_dt.timestamp() <= float(now_ts):
        if date_was_explicit:
            return None
        candidate_dt += timedelta(days=1)
    return int(candidate_dt.timestamp())


def _normalize_date_label(date_label: str | None) -> str | None:
    raw = normalize_text(date_label or "")
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _normalize_time_of_day(value: str | None) -> str | None:
    raw = normalize_text(value or "").lower()
    if raw in {"morning", "afternoon", "night", "midnight"}:
        return raw
    return None


def _normalize_hour(value: int | None) -> int | None:
    if value is None:
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    if 0 <= result <= 23:
        return result
    return None


def _normalize_minute(value: int | None) -> int | None:
    if value is None:
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    if 0 <= result <= 59:
        return result
    return None


def _normalize_positive_int(value: int | None) -> int | None:
    if value is None:
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    if result > 0:
        return result
    return None


def _extract_clock_time(text: str) -> tuple[int | None, int | None]:
    raw = normalize_text(text)
    if not raw:
        return None, None

    colon_match = re.search(r"(?<!\d)(\d{1,2})[:：](\d{1,2})(?!\d)", raw)
    if colon_match:
        return int(colon_match.group(1)), int(colon_match.group(2))

    half_match = re.search(r"(?<!\d)(\d{1,2})点半", raw)
    if half_match:
        return int(half_match.group(1)), 30

    minute_match = re.search(r"(?<!\d)(\d{1,2})点(\d{1,2})分?", raw)
    if minute_match:
        return int(minute_match.group(1)), int(minute_match.group(2))

    hour_match = re.search(r"(?<!\d)(\d{1,2})点(?!\d)", raw)
    if hour_match:
        return int(hour_match.group(1)), 0

    return None, None


def _contains_ambiguous_reminder_time(lowered_text: str) -> bool:
    ambiguous_markers = (
        "改天",
        "回头",
        "之后",
        "以后",
        "有空",
        "过会",
        "过一会",
        "一会",
        "周末",
        "下周",
        "月底",
        "月初",
        "有时间",
    )
    return any(marker in lowered_text for marker in ambiguous_markers)


def _infer_relative_date_label(text: str, *, now_dt: datetime) -> str | None:
    raw = normalize_text(text)
    if not raw:
        return None
    if any(token in raw for token in ("今天", "今日", "今晚", "今早", "今晨", "今天晚上")):
        return now_dt.strftime("%Y-%m-%d")
    if any(token in raw for token in ("明天", "明早", "明晨", "明晚", "明天下午", "明天晚上")):
        return (now_dt + timedelta(days=1)).strftime("%Y-%m-%d")
    if "后天" in raw:
        return (now_dt + timedelta(days=2)).strftime("%Y-%m-%d")
    return None


def _default_hour_for_time_of_day(time_of_day: str | None) -> int | None:
    if time_of_day == "morning":
        return 9
    if time_of_day == "afternoon":
        return 15
    if time_of_day == "night":
        return 20
    if time_of_day == "midnight":
        return 1
    return None


def _apply_cn_time_period_bias(hour: int, *, text: str, time_of_day: str | None) -> int | None:
    normalized_hour = int(hour)
    if normalized_hour == 24:
        return 0
    if normalized_hour < 0 or normalized_hour > 24:
        return None

    if any(token in text for token in ("下午", "晚上", "今晚", "夜里", "夜晚")) or time_of_day == "night":
        if 1 <= normalized_hour <= 11:
            return normalized_hour + 12
    if "中午" in text and 1 <= normalized_hour <= 10:
        return normalized_hour + 12
    if any(token in text for token in ("凌晨", "半夜")) or time_of_day == "midnight":
        if normalized_hour == 12:
            return 0
    return normalized_hour


def _resolve_relative_offset_seconds(
    text: str,
    *,
    explicit_offset_minutes: int | None,
) -> int | None:
    if explicit_offset_minutes is not None:
        return explicit_offset_minutes * 60

    raw = normalize_text(text)
    if not raw:
        return None

    patterns: list[tuple[str, int | float]] = [
        (r"([0-9零〇一二两三四五六七八九十百]+)\s*个?半小时(?:后|以后|之后)", 5400),
        (r"半个?小时(?:后|以后|之后)", 1800),
        (r"([0-9零〇一二两三四五六七八九十百]+)\s*分钟(?:后|以后|之后)", 60),
        (r"([0-9零〇一二两三四五六七八九十百]+)\s*个?小时(?:后|以后|之后)", 3600),
        (r"([0-9零〇一二两三四五六七八九十百]+)\s*天(?:后|以后|之后)", 86400),
    ]
    for pattern, unit_seconds in patterns:
        match = re.search(pattern, raw)
        if not match:
            continue
        if match.lastindex:
            amount = _parse_human_number(match.group(1))
            if amount is None:
                return None
            if unit_seconds == 5400:
                return amount * 3600 + 1800
            return amount * int(unit_seconds)
        return int(unit_seconds)
    return None


def _parse_human_number(text: str) -> int | None:
    raw = normalize_text(text)
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)

    digit_map = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }

    if raw == "十":
        return 10
    if "百" in raw:
        parts = raw.split("百", 1)
        hundreds = digit_map.get(parts[0] or "一")
        if hundreds is None:
            return None
        remainder = _parse_human_number(parts[1]) if parts[1] else 0
        if remainder is None:
            return None
        return hundreds * 100 + remainder
    if "十" in raw:
        tens_part, ones_part = raw.split("十", 1)
        tens = 1 if not tens_part else digit_map.get(tens_part)
        if tens is None:
            return None
        ones = 0
        if ones_part:
            ones = digit_map.get(ones_part)
            if ones is None:
                return None
        return tens * 10 + ones

    if len(raw) == 1:
        return digit_map.get(raw)
    return None


def resolve_speaker_name(role: str) -> str:
    normalized = str(role or "").strip()
    lowered = normalized.lower()
    if lowered == "assistant":
        return PERSONA.assistant_name
    if lowered.startswith("npc:"):
        name = normalized.split(":", 1)[1].strip()
        return name or "NPC"
    if lowered == "npc":
        return "NPC"
    return PERSONA.user_label


def render_chat_line(
    role: str,
    content: str,
    timestamp: int | float | None = None,
) -> str:
    speaker = resolve_speaker_name(role)
    base = f"{speaker}: {normalize_text(content)}"
    labels: list[str] = []
    if timestamp is not None:
        labels.append(timestamp_to_datetime_label(timestamp))
    if not labels:
        return base
    return f"[{' | '.join(labels)}] {base}"


def render_chat_timeline(rows: Iterable[dict[str, Any]]) -> str:
    lines: list[str] = []
    current_date_label: str | None = None
    for row in rows:
        timestamp = row.get("timestamp")
        if timestamp is None:
            lines.append(
                render_chat_line(
                    role=str(row.get("role", "")),
                    content=str(row.get("content", "")),
                    timestamp=None,
                )
            )
            continue

        timestamp_value = float(timestamp)
        date_label = timestamp_to_date_label(timestamp_value)
        if date_label != current_date_label:
            lines.append(f"[日期 {date_label}]")
            current_date_label = date_label

        speaker = resolve_speaker_name(str(row.get("role", "")))
        lines.append(
            f"[{timestamp_to_time_label(timestamp_value)}] {speaker}: {normalize_text(row.get('content', ''))}"
        )
    return "\n".join(lines)


def join_tags(tags: Iterable[str]) -> str:
    return ",".join(str(item).strip() for item in tags if str(item).strip())


def parse_joined_tags(raw: str) -> list[str]:
    text = str(raw or "").replace("，", ",")
    return [item.strip() for item in text.split(",") if item.strip()]
