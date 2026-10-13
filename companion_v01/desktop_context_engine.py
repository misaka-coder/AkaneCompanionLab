from __future__ import annotations

from datetime import datetime
from typing import Any

from .client_protocol import ClientCapability, ClientMode, ClientProtocolContext


def build_turn_extra_user_context(
    engine: Any,
    payload: dict[str, Any] | None,
    client_context: ClientProtocolContext | None,
) -> str:
    source = payload if isinstance(payload, dict) else {}
    session_id = str(source.get("user_id") or source.get("session_id") or "default_session")
    profile_user_id = str(source.get("real_user_id") or source.get("profile_user_id") or session_id)
    return merge_extra_user_context(
        str(source.get("qq_action_note") or ""),
        str(source.get("extra_context") or ""),
        build_desktop_context_prompt(source.get("desktop_context"), client_context),
        build_desktop_care_prompt(source.get("desktop_care"), client_context),
        build_desktop_activity_prompt(
            engine,
            source.get("desktop_activity") or source.get("current_activity"),
            client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
        ),
    )


def merge_extra_user_context(*parts: Any) -> str:
    return "\n\n".join(str(part or "").strip() for part in parts if str(part or "").strip())


def build_desktop_context_prompt(
    desktop_context: Any,
    client_context: ClientProtocolContext | None,
) -> str:
    if (
        client_context is None
        or client_context.effective_mode != ClientMode.DESKTOP_PET
        or not client_context.has_capability(ClientCapability.DESKTOP_CONTEXT)
    ):
        return ""
    if not isinstance(desktop_context, dict) or desktop_context.get("enabled") is False:
        return ""

    foreground = desktop_context.get("foreground") if isinstance(desktop_context.get("foreground"), dict) else {}
    title = sanitize_desktop_context_text(foreground.get("title"), 180)
    process_name = sanitize_desktop_context_text(foreground.get("process_name"), 80)
    source = sanitize_desktop_context_text(foreground.get("source"), 40)

    clipboard_payload = desktop_context.get("clipboard")
    clipboard_text = ""
    clipboard_included = False
    if isinstance(clipboard_payload, dict) and clipboard_payload.get("included") is True:
        clipboard_included = True
        clipboard_text = sanitize_desktop_context_text(clipboard_payload.get("text"), 500)

    lines = [
        "【桌面线索】",
    ]
    if title or process_name:
        if source == "foreground":
            window_label = "当前前台窗口"
        elif source == "last_external_window":
            window_label = "最近外部前台窗口"
        elif source == "nearby_process":
            window_label = "桌面上可见窗口线索"
        else:
            window_label = "桌宠附近窗口线索"
        window_text = title or "未知标题"
        if process_name:
            window_text += f"（进程：{process_name}）"
        lines.append(f"- {window_label}：{window_text}")
    else:
        lines.append("- 当前窗口：未知")
    if clipboard_included and clipboard_text:
        lines.append(f"- 剪贴板文本：{clipboard_text}")
    return "\n".join(lines)


def build_desktop_care_prompt(
    desktop_care: Any,
    client_context: ClientProtocolContext | None,
) -> str:
    if client_context is None or client_context.effective_mode not in {ClientMode.DESKTOP_PET, ClientMode.QQ_TEXT}:
        return ""
    if not isinstance(desktop_care, dict) or desktop_care.get("enabled") is False:
        return ""

    hunger = coerce_bounded_int(desktop_care.get("hunger"), 0, 100)
    energy = coerce_bounded_int(desktop_care.get("energy"), 0, 100)
    affection = coerce_bounded_int(desktop_care.get("affection"), 0, 100)
    thresholds = desktop_care.get("thresholds") if isinstance(desktop_care.get("thresholds"), dict) else {}
    hunger_low = coerce_bounded_int(thresholds.get("hunger_low"), 0, 100, fallback=25)
    hunger_critical = coerce_bounded_int(thresholds.get("hunger_critical"), 0, 100, fallback=12)
    energy_low = coerce_bounded_int(thresholds.get("energy_low"), 0, 100, fallback=25)
    energy_critical = coerce_bounded_int(thresholds.get("energy_critical"), 0, 100, fallback=12)
    affection_familiar = coerce_bounded_int(thresholds.get("affection_familiar"), 0, 100, fallback=20)
    affection_warm = coerce_bounded_int(thresholds.get("affection_warm"), 0, 100, fallback=45)
    affection_close = coerce_bounded_int(thresholds.get("affection_close"), 0, 100, fallback=70)
    affection_bond = coerce_bounded_int(thresholds.get("affection_bond"), 0, 100, fallback=88)

    is_qq_text = client_context.effective_mode == ClientMode.QQ_TEXT
    scope = "qq_text" if is_qq_text else "desktop_pet"
    lines = [
        "【care.state｜宿主当前值】",
        f"- scope: {scope}",
    ]
    if not is_qq_text and desktop_care.get("work_task_active") is True:
        lines.append("- work_task=active")

    lines.extend(
        build_desktop_care_expression_lines(
            hunger=hunger,
            energy=energy,
            affection=affection,
            hunger_low=hunger_low,
            hunger_critical=hunger_critical,
            energy_low=energy_low,
            energy_critical=energy_critical,
            affection_familiar=affection_familiar,
            affection_warm=affection_warm,
            affection_close=affection_close,
            affection_bond=affection_bond,
            now=desktop_care.get("now"),
        )
    )
    # Tier event is injected once on the first turn after crossing a threshold.
    tier_event = desktop_care.get("pending_tier_event")
    if isinstance(tier_event, dict):
        from_tier = str(tier_event.get("from_tier") or "")
        to_tier = str(tier_event.get("to_tier") or "")
        direction = str(tier_event.get("direction") or "up")
        if from_tier and to_tier:
            lines.append(f"- pending_tier_event: {from_tier} -> {to_tier} ({direction})")

    # Memory anchors: behavioral metadata — inject softly when enough history exists
    anchor_lines = _build_anchor_lines(desktop_care.get("anchors"), desktop_care.get("now"))
    if anchor_lines:
        lines.extend(anchor_lines)

    return "\n".join(lines)


def build_desktop_care_expression_lines(
    *,
    hunger: int,
    energy: int,
    affection: int,
    hunger_low: int,
    hunger_critical: int,
    energy_low: int,
    energy_critical: int,
    affection_familiar: int = 20,
    affection_warm: int = 45,
    affection_close: int = 70,
    affection_bond: int = 88,
    now: Any = None,
) -> list[str]:
    return [
        f"- hunger: {hunger}/100 ({_care_level(hunger, hunger_low, hunger_critical)}); "
        f"energy: {energy}/100 ({_care_level(energy, energy_low, energy_critical)})",
        f"- affection: {affection}/100 ({_care_affection_tier(affection, affection_familiar, affection_warm, affection_close, affection_bond)})",
    ]


def _care_level(value: int, low: int, critical: int) -> str:
    if value <= critical:
        return "critical"
    if value <= low:
        return "low"
    return "normal"


def _care_affection_tier(
    affection: int,
    familiar: int,
    warm: int,
    close: int,
    bond: int,
) -> str:
    if affection >= bond:
        return "bond"
    if affection >= close:
        return "close"
    if affection >= warm:
        return "warm"
    if affection >= familiar:
        return "familiar"
    return "stranger"


def _build_anchor_lines(anchors: Any, now: Any = None) -> list[str]:
    """Format memory anchor hints for injection into the care prompt.

    Returns an empty list if not enough history exists yet (total_turns < 5).
    These are relationship facts; the stable care rules guide their expression.
    """
    if not isinstance(anchors, dict):
        return []
    total_turns = int(anchors.get("total_turns") or 0)
    if total_turns < 5:
        return []

    # Resolve current timestamp for "days known" calculation
    now_ms: int = 0
    if isinstance(now, (int, float)) and now > 0:
        val = float(now)
        now_ms = int(val if val < 1_000_000_000_000 else val)
    elif isinstance(now, str) and now.strip():
        try:
            now_ms = int(datetime.fromisoformat(now.strip().replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            pass
    if now_ms <= 0:
        now_ms = int(datetime.now().timestamp() * 1000)

    detail_lines: list[str] = []

    first_seen_ms = int(anchors.get("first_seen_ms") or 0)
    if first_seen_ms > 0:
        days_known = max(0, (now_ms - first_seen_ms) // (1000 * 86400))
        if days_known >= 1:
            detail_lines.append(f"你们认识已经 {days_known} 天，聊过 {total_turns} 轮")
        else:
            detail_lines.append(f"今天刚认识，聊过 {total_turns} 轮")
    else:
        detail_lines.append(f"聊过 {total_turns} 轮")

    first_fed = anchors.get("first_fed")
    if isinstance(first_fed, dict) and first_fed.get("name"):
        detail_lines.append(f"她第一次收到的投喂是「{first_fed['name']}」")

    late_night = int(anchors.get("late_night_turns") or 0)
    if late_night >= 3:
        detail_lines.append(f"其中深夜聊天 {late_night} 轮")

    max_streak = int(anchors.get("max_checkin_streak") or 0)
    if max_streak >= 3:
        detail_lines.append(f"历史最长连续签到：{max_streak} 天")

    if not detail_lines:
        return []
    return ["【关系记录】"] + [f"- {l}" for l in detail_lines]


def get_desktop_care_time_phase(now: Any = None) -> str:
    hour = None
    if isinstance(now, (int, float)) and now > 0:
        try:
            value = float(now)
            if value > 1_000_000_000_000:
                value = value / 1000
            hour = datetime.fromtimestamp(value).hour
        except (OverflowError, OSError, ValueError):
            hour = None
    elif isinstance(now, str) and now.strip():
        try:
            hour = datetime.fromisoformat(now.strip().replace("Z", "+00:00")).hour
        except ValueError:
            hour = None
    if hour is None:
        hour = datetime.now().hour

    if 5 <= hour < 9:
        return "清晨，适合轻一点、慢慢醒来的语气"
    if 9 <= hour < 18:
        return "白天，适合清醒但不过度兴奋的日常陪伴"
    if 18 <= hour < 23:
        return "傍晚，适合松弛一点的陪伴感"
    return "深夜，适合收声、短句、带一点困意"


def _vitals_state_label(
    h: int,
    e: int,
    h_low: int,
    h_crit: int,
    e_low: int,
    e_crit: int,
) -> str:
    """Return a concise state label covering all hunger×energy combinations."""
    h_critical = h <= h_crit
    h_low_state = h_crit < h <= h_low
    e_critical = e <= e_crit
    e_low_state = e_crit < e <= e_low

    if h_critical and e_critical:
        return "又饿又困——两项都到临界线"
    if h_critical and e_low_state:
        return "极度饥饿，且有些疲惫——以饿为主"
    if h_critical:
        return "只饿不困——极度饥饿，精力还有"
    if e_critical and h_low_state:
        return "精力告急，且有些饿——以困为主"
    if e_critical:
        return "只困不饿——精力告急，不饿"
    if h_low_state and e_low_state:
        return "有些饿也有些困"
    if h_low_state:
        return "只饿不困——有些饿，精力还行"
    if e_low_state:
        return "只困不饿——有些倦，不饿"
    return "吃饱了也有精神——状态很好"


def describe_desktop_care_vitality(
    hunger: int,
    energy: int,
    hunger_low: int = 25,
    hunger_critical: int = 12,
    energy_low: int = 25,
    energy_critical: int = 12,
) -> str:
    if hunger <= hunger_critical and energy <= energy_critical:
        return "身体状态很差，像是又饿又困"
    if hunger <= hunger_low and energy <= energy_low:
        return "身体状态偏低，容易没精神也惦记吃的"
    if energy >= 70 and hunger >= 50:
        return "身体状态不错，可以自然接话"
    if energy <= energy_low:
        return "精力不足，回应会更轻、更短"
    if hunger <= hunger_low:
        return "饥饿感明显，注意力会偏向吃的"
    return "身体状态平稳，按当前对话自然回应"




def sanitize_desktop_context_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    if limit > 0 and len(text) > limit:
        return text[:limit]
    return text


def coerce_bounded_int(value: Any, minimum: int, maximum: int, *, fallback: int = 0) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = fallback
    return min(maximum, max(minimum, number))


def coerce_activity_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def build_desktop_activity_prompt(
    engine: Any,
    activity: Any,
    client_context: ClientProtocolContext | None,
    *,
    profile_user_id: str = "",
    session_id: str = "",
) -> str:
    if (
        client_context is None
        or client_context.effective_mode != ClientMode.DESKTOP_PET
        or not client_context.has_capability(ClientCapability.AUDIO_PLAYBACK)
    ):
        return ""
    if not isinstance(activity, dict):
        return ""

    activity_type = str(activity.get("type") or "").strip().lower()
    if activity_type in {"video_playback", "media_playback"}:
        title = sanitize_desktop_context_text(activity.get("title"), 120) or "当前媒体"
        status = {"running": "正在播放", "paused": "已暂停", "stopped": "已停止"}.get(str(activity.get("status")), "未知")
        progress = format_activity_time(activity.get("progress_seconds"))
        duration = format_activity_time(activity.get("duration_seconds"))
        captured_at = safe_activity_seconds(activity.get("captured_at"))
        return "\n".join([
            "【当前系统媒体】",
            f"- 类型：{'视频' if activity_type == 'video_playback' else '浏览器或未分类媒体（未确认是音乐）'}",
            f"- 标题：{title}；状态：{status}",
            f"- 采样时进度：{progress or '未知'} / {duration or '未知'}；采样 Unix 秒：{captured_at:.3f}",
        ])
    if activity_type not in {"audio_playback", "vocal_performance"}:
        return ""

    title = sanitize_desktop_context_text(activity.get("title"), 120) or "未命名音频"
    source_id = sanitize_desktop_context_text(activity.get("source_id") or activity.get("handle"), 60)
    status = str(activity.get("status") or "").strip().lower() or "unknown"
    source_kind = str(activity.get("source_kind") or activity.get("sourceKind") or "").strip().lower()
    is_external_system_media = source_kind == "system_media" or activity.get("system_media") is True
    progress = format_activity_time(activity.get("progress_seconds"))
    duration = format_activity_time(activity.get("duration_seconds"))

    status_label = {
        "ready": "已放在手边，尚未播放",
        "running": "正在播放",
        "paused": "已暂停",
        "interrupted": "因主人发来消息已暂停",
        "stopped": "已停止",
        "completed": "已播放结束",
    }.get(status, status or "未知")

    lines = [
        "【当前桌宠活动】",
        f"- 类型：{'角色表演/唱歌' if activity_type == 'vocal_performance' else '普通音频播放'}",
        f"- 音频：{title}" + (f"（{source_id}）" if source_id else ""),
        f"- 状态：{status_label}",
    ]
    if progress:
        timing = f"进度 {progress}"
        if duration:
            timing += f" / {duration}"
        lines.append(f"- {timing}")
    if activity_type == "audio_playback":
        queue_count = coerce_activity_int(activity.get("queue_count") or activity.get("queueCount"))
        queue_index = coerce_activity_int(activity.get("queue_index") or activity.get("queueIndex"))
        if queue_count > 1:
            if queue_index > 0:
                lines.append(f"- 队列：第 {queue_index} 首 / 共 {queue_count} 首")
            next_title = sanitize_desktop_context_text(
                activity.get("next_title") or activity.get("nextTitle"),
                80,
            )
            if next_title:
                lines.append(f"- 下一首：{next_title}")
        lyric_current = sanitize_desktop_context_text(
            activity.get("lyric_current") or activity.get("lyricCurrent"),
            120,
        )
        lyric_previous = sanitize_desktop_context_text(
            activity.get("lyric_previous") or activity.get("lyricPrevious"),
            100,
        )
        lyric_next = sanitize_desktop_context_text(
            activity.get("lyric_next") or activity.get("lyricNext"),
            100,
        )
        if lyric_current:
            lines.append(f"- 当前歌词：{lyric_current}")
        elif lyric_next:
            lines.append(f"- 下一句歌词：{lyric_next}")
        if lyric_previous:
            lines.append(f"- 上一句歌词：{lyric_previous}")
        if lyric_next and lyric_current:
            lines.append(f"- 下一句歌词：{lyric_next}")
    lines.append(
        "- 来源：" + ("系统播放器；control_tool: system_media_control" if is_external_system_media
                     else "桌宠播放器；control_tool: local_music_control")
    )
    activity_prompt = "\n".join(lines)
    timeline_prompt = build_desktop_music_timeline_prompt(
        engine,
        activity,
        profile_user_id=profile_user_id,
        session_id=session_id,
    )
    co_listen_prompt = build_co_listen_memory_block(
        engine,
        activity,
        profile_user_id=profile_user_id,
    )
    return merge_extra_user_context(activity_prompt, timeline_prompt, co_listen_prompt)


def build_co_listen_memory_block(
    engine: Any,
    activity: dict[str, Any],
    *,
    profile_user_id: str = "",
) -> str:
    """Render the cross-source "我们的共听记忆" block.

    Implements `docs/listening_together_demo_v1.md` §3 (history fields) and §4
    (track-change trigger) on top of the existing activity prompt path. Stays
    silent for first listens and missing identities so it can be safely
    appended unconditionally.
    """
    if not profile_user_id:
        return ""
    if not isinstance(activity, dict):
        return ""
    if str(activity.get("type") or "").strip().lower() != "audio_playback":
        return ""

    getter = getattr(engine, "_get_music_context_assembler", None)
    if getter is None:
        return ""
    try:
        assembler = getter()
    except Exception:
        return ""
    if assembler is None:
        return ""

    try:
        from .music_context import build_co_listen_memory_prompt

        context = assembler.assemble(
            activity=activity,
            profile_user_id=profile_user_id,
        )
        return build_co_listen_memory_prompt(context)
    except Exception:
        return ""


def build_desktop_music_timeline_prompt(
    engine: Any,
    activity: dict[str, Any],
    *,
    profile_user_id: str = "",
    session_id: str = "",
) -> str:
    if not profile_user_id or not session_id:
        return ""
    if (
        activity.get("lyric_current")
        or activity.get("lyricCurrent")
        or activity.get("lyric_next")
        or activity.get("lyricNext")
    ):
        return ""
    status = str(activity.get("status") or "").strip().lower()
    progress_seconds = safe_activity_seconds(activity.get("progress_seconds"))
    source_kind = str(activity.get("source_kind") or activity.get("sourceKind") or "").strip().lower()
    is_external_system_media = source_kind == "system_media" or activity.get("system_media") is True
    if is_external_system_media:
        return build_system_media_no_lyrics_prompt(activity)
    service = engine._get_desktop_music_timeline_service()
    if service is None:
        return ""
    should_prepare = (
        not is_external_system_media
        and (status in {"running", "paused", "interrupted"} or progress_seconds > 0)
    )
    if should_prepare:
        try:
            service.prepare_timeline(
                profile_user_id=profile_user_id,
                session_id=session_id,
                activity=activity,
            )
        except Exception:
            pass
    return service.build_prompt_projection(
        profile_user_id=profile_user_id,
        session_id=session_id,
        activity=activity,
    )


def build_system_media_no_lyrics_prompt(activity: dict[str, Any]) -> str:
    lyric_status = str(activity.get("lyric_status") or activity.get("lyricStatus") or "").strip().lower()
    lyric_reason = str(activity.get("lyric_reason") or activity.get("lyricReason") or "").strip().lower()
    lines = ["【当前音乐位置】"]
    if lyric_status == "low-confidence":
        lines.append("- 歌词：匹配可信度不足。")
    elif lyric_status == "not-found":
        lines.append("- 暂时没有找到这首歌的同步歌词。")
    elif lyric_status == "disabled":
        lines.append("- 在线歌词检索已关闭。")
    elif lyric_status == "unavailable":
        reason_hint = "provider 不可用" if lyric_reason else "歌词线索暂不可用"
        lines.append(f"- 歌词：{reason_hint}。")
    else:
        lines.append("- 歌词线索还没准备好，当前还不能确定唱到哪一句。")
    return "\n".join(lines)


def safe_activity_seconds(value: Any) -> float:
    try:
        return max(0.0, float(value or 0))
    except Exception:
        return 0.0


def format_activity_time(value: Any) -> str:
    try:
        seconds = max(0, int(round(float(value))))
    except Exception:
        return ""
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
