from __future__ import annotations

import time
from typing import Any

from .persona_config import PERSONA
from .text_utils import (
    extract_semantic_tags,
    infer_time_of_day,
    normalize_text,
    timestamp_to_date_label,
    timestamp_to_datetime_label,
)


def consume_due_reminders(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    now_ts: int | None = None,
    current_visual_payload: Any = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    effective_now_ts = int(now_ts or time.time())
    due_records = engine.store.claim_due_reminders(
        profile_user_id=profile_user_id,
        session_id=session_id,
        now_ts=effective_now_ts,
        limit=limit,
    )
    if not due_records:
        return []

    visual_payload = engine._resolve_current_visual_payload(
        session_id=session_id,
        current_visual_payload=current_visual_payload,
    )
    notifications = [
        build_reminder_notification_payload(
            engine,
            reminder=record,
            visual_payload=visual_payload,
        )
        for record in due_records
    ]
    for notification in notifications:
        persist_due_reminder_notification(
            engine,
            profile_user_id=profile_user_id,
            session_id=session_id,
            notification=notification,
            now_ts=effective_now_ts,
        )
    return notifications


def build_reminder_notification_payload(
    engine: Any,
    *,
    reminder: dict[str, Any],
    visual_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    visual = engine._coerce_visual_payload(visual_payload or {}) or {
        "emotion": "normal",
        "character": {},
        "scene": {},
    }
    speech = generate_reminder_notification_speech(
        engine,
        reminder=reminder,
        visual_payload=visual,
    )
    return {
        "reminder_id": reminder["reminder_id"],
        "source": "reminder",
        "emotion": str(visual.get("emotion") or "normal"),
        "speech": speech,
        "memory_tags": "",
        "status": "final",
        "tool_call": None,
        "choices": [],
        "character": dict(visual.get("character") or {}),
        "scene": dict(visual.get("scene") or {}),
        "dialogue_turns": [
            {
                "speaker": PERSONA.assistant_name,
                "speech": speech,
            }
        ],
        "due_ts": int(reminder["due_ts"]),
        "fired_at": int(reminder.get("fired_at") or reminder["due_ts"]),
    }


def generate_reminder_notification_speech(
    engine: Any,
    *,
    reminder: dict[str, Any],
    visual_payload: dict[str, Any],
) -> str:
    fallback_speech = format_reminder_notification(reminder)
    visual_context = engine._describe_tool_scene_context(visual_payload)
    result = engine.llm.call_chat_json(
        system_prompt=(
            "你是当前前台角色。现在有一条已经到时间的提醒需要你自然地说出口。"
            "你只输出一个合法 JSON 对象，字段固定为 speech。"
            "speech 要像当前前台角色当下自然想起这件事后对用户说的一句提醒，口吻亲近、简短、自然。"
            "只需要 1 到 2 句，不要解释系统原理，不要说自己忘记了，也不要输出多余字段。"
        ),
        user_prompt=(
            f"当前演出状态：{visual_context}\n"
            f"提醒内容：{str(reminder.get('content') or '').strip()}\n"
            f"原始提醒时间说法：{str(reminder.get('raw_time_text') or '').strip() or '(未提供)'}\n"
            f"当前时间：{timestamp_to_datetime_label(int(reminder.get('fired_at') or reminder.get('due_ts') or time.time()))}\n"
            "请用当前前台角色的语气说一句现在该提醒用户的话。"
        ),
        fallback={"speech": fallback_speech},
        temperature=0.85,
        prompt_cache_key="chat:reminder_notification",
    )
    speech = normalize_text(str(result.get("speech") or fallback_speech))
    return speech[:120] if speech else fallback_speech


def persist_due_reminder_notification(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    notification: dict[str, Any],
    now_ts: int,
) -> None:
    speech = str(notification.get("speech") or "").strip()
    if not speech:
        return
    reminder_ts = int(notification.get("fired_at") or now_ts)
    record = engine.store.add_message(
        profile_user_id=profile_user_id,
        session_id=session_id,
        role="assistant",
        content=speech,
        timestamp=reminder_ts,
        date_label=timestamp_to_date_label(reminder_ts),
        time_of_day=infer_time_of_day(reminder_ts),
        semantic_tags=extract_semantic_tags(speech),
    )
    engine._upsert_raw_record(record)
    engine._schedule_summary_cycle(profile_user_id=profile_user_id, session_id=session_id)


def format_reminder_notification(reminder: dict[str, Any]) -> str:
    content = str(reminder.get("content") or "").strip()
    raw_time_text = str(reminder.get("raw_time_text") or "").strip()
    if raw_time_text:
        return f"到时间啦。你之前让我在{raw_time_text}提醒你“{content}”，现在该去做啦。"
    return f"到时间啦。你之前让我提醒你的事是“{content}”，现在该去做啦。"
