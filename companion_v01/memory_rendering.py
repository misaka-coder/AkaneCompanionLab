from __future__ import annotations

from datetime import datetime
from typing import Any

from .text_utils import normalize_text, timestamp_to_datetime_label


RELATIVE_TIME_TOKENS = (
    "今天",
    "今日",
    "今晚",
    "今早",
    "明天",
    "明早",
    "明晚",
    "昨天",
    "昨晚",
    "前天",
    "后天",
    "上周",
    "下周",
    "上个月",
    "下个月",
    "去年",
    "今年",
    "最近",
    "这段时间",
    "当前",
    "现在",
    "刚才",
    "一会儿",
    "过几天",
)


def resolve_record_time_range(record: dict[str, Any], *, store: Any) -> tuple[int | None, int | None]:
    if record.get("period_start_ts") is not None or record.get("period_end_ts") is not None:
        start_ts = record.get("period_start_ts")
        end_ts = record.get("period_end_ts") or record.get("timestamp")
        return (
            int(start_ts) if start_ts is not None else None,
            int(end_ts) if end_ts is not None else None,
        )

    session_id = str(record.get("session_id") or "")
    start_seq = record.get("source_start_seq")
    end_seq = record.get("source_end_seq")
    start_record = None
    end_record = None
    if session_id and start_seq is not None:
        start_record = store.get_message_by_seq_no(
            session_id,
            int(start_seq),
            profile_user_id=str(record.get("profile_user_id") or ""),
            character_pack_id=str(record.get("character_pack_id") or ""),
        )
    if session_id and end_seq is not None:
        end_record = store.get_message_by_seq_no(
            session_id,
            int(end_seq),
            profile_user_id=str(record.get("profile_user_id") or ""),
            character_pack_id=str(record.get("character_pack_id") or ""),
        )
    start_ts = (start_record or {}).get("timestamp")
    end_ts = (end_record or {}).get("timestamp") or record.get("timestamp")
    return (
        int(start_ts) if start_ts is not None else None,
        int(end_ts) if end_ts is not None else None,
    )


def format_time_range_label(
    *,
    start_ts: int | float | None,
    end_ts: int | float | None,
) -> str:
    if start_ts is None and end_ts is None:
        return ""
    if start_ts is None:
        return timestamp_to_datetime_label(end_ts)
    if end_ts is None:
        return timestamp_to_datetime_label(start_ts)

    start_dt = datetime.fromtimestamp(float(start_ts))
    end_dt = datetime.fromtimestamp(float(end_ts))
    if start_dt.date() == end_dt.date():
        return f"{start_dt.strftime('%Y-%m-%d %H:%M')} ~ {end_dt.strftime('%H:%M')}"
    return f"{start_dt.strftime('%Y-%m-%d %H:%M')} ~ {end_dt.strftime('%Y-%m-%d %H:%M')}"


def build_summary_time_range_label(record: dict[str, Any], *, store: Any) -> str:
    start_ts, end_ts = resolve_record_time_range(record, store=store)
    return format_time_range_label(start_ts=start_ts, end_ts=end_ts)


def render_memory_mood_line(record: dict[str, Any]) -> str:
    metadata = record.get("memory_metadata") if isinstance(record.get("memory_metadata"), dict) else {}
    mood_tags = metadata.get("mood_tags") if isinstance(metadata, dict) else []
    if not isinstance(mood_tags, list):
        mood_tags = []
    rendered = [
        normalize_text(str(item or "")).strip()
        for item in mood_tags
        if normalize_text(str(item or "")).strip()
    ]
    if not rendered:
        return ""
    return "记忆情绪：" + " / ".join(rendered[:3])


def render_relative_time_anchor_line(*, text: str, time_range_label: str) -> str:
    if not time_range_label:
        return ""
    normalized = normalize_text(text)
    if not any(token in normalized for token in RELATIVE_TIME_TOKENS):
        return ""
    return f"相对时间锚点：本条记忆中的今天/明天/昨天/最近等说法，均以 {time_range_label} 为准，不按当前日期重算。"


def render_summary_snippet(record: dict[str, Any], *, store: Any) -> str:
    labels = []
    time_range_label = build_summary_time_range_label(record, store=store)
    if time_range_label:
        labels.append(time_range_label)
    if record.get("period_label"):
        labels.append(f"阶段:{record['period_label']}")
    if record.get("event_type"):
        labels.append(f"类型:{record['event_type']}")
    key_events = "；".join(record.get("key_events") or [])
    core_facts = "；".join(record.get("core_facts") or [])
    prefix = f"【摘要回忆】[{ ' | '.join(labels) }] " if labels else "【摘要回忆】"
    parts = [f"{prefix}{record.get('diary_summary', '')}"]
    anchor_line = render_relative_time_anchor_line(
        text=" ".join(
            [str(record.get("diary_summary") or "")]
            + [str(event) for event in (record.get("key_events") or [])]
            + [str(fact) for fact in (record.get("core_facts") or [])]
        ),
        time_range_label=time_range_label,
    )
    if anchor_line:
        parts.append(anchor_line)
    mood_line = render_memory_mood_line(record)
    if mood_line:
        parts.append(mood_line)
    if key_events:
        parts.append(f"关键事件：{key_events}")
    if core_facts:
        parts.append(f"核心事实：{core_facts}")
    return "\n".join(parts)


def render_semantic_summary_snippet(record: dict[str, Any], *, store: Any) -> str:
    labels = []
    time_range_label = build_summary_time_range_label(record, store=store)
    if time_range_label:
        labels.append(time_range_label)
    if record.get("importance") is not None:
        labels.append(f"重要度:{float(record.get('importance') or 0.0):.2f}")
    prefix = f"【长期语义记忆】[{ ' | '.join(labels) }] " if labels else "【长期语义记忆】"
    parts = [f"{prefix}{record.get('semantic_summary', '')}"]
    anchor_line = render_relative_time_anchor_line(
        text=" ".join(
            [str(record.get("semantic_summary") or "")]
            + [str(fact) for fact in (record.get("stable_facts") or [])]
            + [str(topic) for topic in (record.get("recurring_topics") or [])]
            + [str(person) for person in (record.get("important_people") or [])]
            + [str(item_text) for item_text in (record.get("open_loops") or [])]
        ),
        time_range_label=time_range_label,
    )
    if anchor_line:
        parts.append(anchor_line)
    mood_line = render_memory_mood_line(record)
    if mood_line:
        parts.append(mood_line)
    stable_facts = "；".join(record.get("stable_facts") or [])
    recurring_topics = "；".join(record.get("recurring_topics") or [])
    important_people = "；".join(record.get("important_people") or [])
    open_loops = "；".join(record.get("open_loops") or [])
    if stable_facts:
        parts.append(f"稳定事实：{stable_facts}")
    if recurring_topics:
        parts.append(f"反复话题：{recurring_topics}")
    if important_people:
        parts.append(f"重要人物：{important_people}")
    if open_loops:
        parts.append(f"待续线索：{open_loops}")
    return "\n".join(parts)


def render_summary_timeline(summaries: list[dict[str, Any]], *, store: Any) -> str:
    if not summaries:
        return ""

    ordered = sorted(summaries, key=lambda item: int(item.get("timestamp") or 0))
    blocks: list[str] = []
    for item in ordered:
        labels: list[str] = []
        time_range_label = build_summary_time_range_label(item, store=store)
        if time_range_label:
            labels.append(time_range_label)
        if item.get("period_label"):
            labels.append(f"阶段:{item['period_label']}")
        if item.get("event_type"):
            labels.append(f"类型:{item['event_type']}")

        header = f"[{' | '.join(labels)}] 摘要: {normalize_text(item.get('diary_summary', ''))}"
        details: list[str] = [header]
        anchor_line = render_relative_time_anchor_line(
            text=" ".join(
                [str(item.get("diary_summary") or "")]
                + [str(event) for event in (item.get("key_events") or [])]
                + [str(fact) for fact in (item.get("core_facts") or [])]
            ),
            time_range_label=time_range_label,
        )
        if anchor_line:
            details.append(anchor_line)
        mood_line = render_memory_mood_line(item)
        if mood_line:
            details.append(mood_line)
        key_events = item.get("key_events") or []
        core_facts = item.get("core_facts") or []
        if key_events:
            details.append("关键事件：" + "；".join(str(event) for event in key_events))
        if core_facts:
            details.append("核心事实：" + "；".join(str(fact) for fact in core_facts))
        blocks.append("\n".join(details))
    return "\n\n".join(blocks)


def render_semantic_summary_timeline(summaries: list[dict[str, Any]], *, store: Any) -> str:
    if not summaries:
        return ""

    ordered = sorted(
        summaries,
        key=lambda item: (
            int(item.get("last_reinforced_ts") or 0),
            float(item.get("importance") or 0.0),
            int(item.get("timestamp") or 0),
        ),
    )
    blocks: list[str] = []
    for item in ordered:
        labels: list[str] = []
        time_range_label = build_summary_time_range_label(item, store=store)
        if time_range_label:
            labels.append(time_range_label)
        labels.append(f"重要度:{float(item.get('importance') or 0.0):.2f}")
        labels.append(f"强化:{int(item.get('reinforcement_count') or 1)}")
        header = f"[{' | '.join(labels)}] 长期记忆: {normalize_text(item.get('semantic_summary', ''))}"
        details: list[str] = [header]
        anchor_line = render_relative_time_anchor_line(
            text=" ".join(
                [str(item.get("semantic_summary") or "")]
                + [str(fact) for fact in (item.get("stable_facts") or [])]
                + [str(topic) for topic in (item.get("recurring_topics") or [])]
                + [str(person) for person in (item.get("important_people") or [])]
                + [str(item_text) for item_text in (item.get("open_loops") or [])]
            ),
            time_range_label=time_range_label,
        )
        if anchor_line:
            details.append(anchor_line)
        mood_line = render_memory_mood_line(item)
        if mood_line:
            details.append(mood_line)
        stable_facts = item.get("stable_facts") or []
        recurring_topics = item.get("recurring_topics") or []
        important_people = item.get("important_people") or []
        open_loops = item.get("open_loops") or []
        if stable_facts:
            details.append("稳定事实：" + "；".join(str(fact) for fact in stable_facts))
        if recurring_topics:
            details.append("反复话题：" + "；".join(str(topic) for topic in recurring_topics))
        if important_people:
            details.append("重要人物：" + "；".join(str(person) for person in important_people))
        if open_loops:
            details.append("待续线索：" + "；".join(str(item_text) for item_text in open_loops))
        blocks.append("\n".join(details))
    return "\n\n".join(blocks)
