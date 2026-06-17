from __future__ import annotations

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
        str(source.get("extra_context") or ""),
        build_desktop_context_prompt(source.get("desktop_context"), client_context),
        build_desktop_screen_vision_prompt(
            engine,
            client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
        ),
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
        "【桌面上下文（临时，不写入长期记忆）】",
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
        lines.append("- 窗口标题只是环境线索；除非用户明确提到，不要把它当成必须回应的主题。")
    else:
        lines.append("- 当前窗口：未知，勿猜测。")
    if clipboard_included and clipboard_text:
        lines.append(f"- 剪贴板文本（最多截断 500 字）：{clipboard_text}")
    return "\n".join(lines)


def build_desktop_screen_vision_prompt(
    engine: Any,
    client_context: ClientProtocolContext | None,
    *,
    profile_user_id: str = "",
    session_id: str = "",
) -> str:
    if (
        client_context is None
        or client_context.effective_mode != ClientMode.DESKTOP_PET
        or not client_context.has_capability(ClientCapability.SCREEN_VISION)
        or not profile_user_id
        or not session_id
    ):
        return ""
    builder = getattr(engine, "build_desktop_screen_vision_context", None)
    if builder is None:
        return ""
    try:
        return str(builder(profile_user_id=profile_user_id, session_id=session_id, limit=3) or "").strip()
    except Exception:
        return ""


def sanitize_desktop_context_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    if limit > 0 and len(text) > limit:
        return text[:limit]
    return text


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
    if activity_type not in {"audio_playback", "audio_recommendations", "vocal_performance"}:
        return ""

    title = sanitize_desktop_context_text(activity.get("title"), 120) or (
        "Akane 音乐推荐" if activity_type == "audio_recommendations" else "未命名音频"
    )
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
        f"- 类型：{'角色表演/唱歌' if activity_type == 'vocal_performance' else '可播放音乐推荐' if activity_type == 'audio_recommendations' else '普通音频播放'}",
        f"- 音频：{title}" + (f"（{source_id}）" if source_id else ""),
        f"- 状态：{status_label}",
    ]
    if progress:
        timing = f"进度 {progress}"
        if duration:
            timing += f" / {duration}"
        lines.append(f"- {timing}")
    if activity_type in {"audio_playback", "audio_recommendations"}:
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
            raw_titles = activity.get("queue_titles") or activity.get("queueTitles") or []
            if isinstance(raw_titles, list):
                titles = [
                    sanitize_desktop_context_text(item, 40)
                    for item in raw_titles[:6]
                    if sanitize_desktop_context_text(item, 40)
                ]
                if titles:
                    lines.append(f"- 队列概况：{'；'.join(titles)}")
        raw_recs = activity.get("recommendations") or []
        if isinstance(raw_recs, list):
            rec_lines = []
            for rec in raw_recs[:3]:
                if not isinstance(rec, dict):
                    continue
                rec_title = sanitize_desktop_context_text(rec.get("title"), 40)
                reason = sanitize_desktop_context_text(rec.get("reason"), 30)
                rec_source_id = sanitize_desktop_context_text(rec.get("source_id"), 60)
                if rec_title:
                    line = f"{rec_title}（{reason}）" if reason else rec_title
                    if rec_source_id:
                        line += f"，source_id: {rec_source_id}"
                    rec_lines.append(line)
            if rec_lines:
                lines.append(f"- 当前 Akane 音乐推荐：{'；'.join(rec_lines)}")
        raw_catalog = activity.get("catalog") or []
        if isinstance(raw_catalog, list) and raw_catalog:
            catalog_lines = []
            for cat_item in raw_catalog[:12]:
                if not isinstance(cat_item, dict):
                    continue
                cat_title = sanitize_desktop_context_text(cat_item.get("title"), 50)
                cat_source = sanitize_desktop_context_text(cat_item.get("source_id"), 60)
                cat_reason = sanitize_desktop_context_text(cat_item.get("reason"), 20)
                if cat_title and cat_source:
                    line = f"{cat_title}（source_id: {cat_source}）"
                    if cat_reason:
                        line += f" - {cat_reason}"
                    catalog_lines.append(line)
            if catalog_lines:
                lines.append("【当前可播放音乐】")
                for line in catalog_lines:
                    lines.append(f"- {line}")
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
    if activity_type == "audio_playback":
        if is_external_system_media:
            lines.append(
                "- 系统媒体来自 Windows 当前媒体会话；你可以请求桌宠对当前系统播放器执行播放、暂停、停止、上一首或下一首。"
            )
        else:
            lines.append(
                "- 普通音频不会因为本轮消息自动暂停；如果你想控制播放，请输出 activity action。"
            )
    elif status == "interrupted":
        lines.append(
            "- 主人发消息时表演已暂停；如果你想继续表演，需要输出 activity action，而不是假装仍在继续。"
        )
    if is_external_system_media:
        lines.append(
            f'- 可选 activity 输出：{{"action":"play|pause|resume|stop|previous|next","target":"current","source_id":"{source_id}"}}；不需要控制时输出 null。'
        )
        lines.append(
            "- activity 是给桌宠执行的系统媒体控制请求，不是执行成功回执；speech 里不要说已经播放、已经暂停或已经切歌，"
            "可以自然说“我帮你试一下”“我去切一下”。如果播放器不支持系统媒体控制，前端会自己提示失败。"
        )
    else:
        lines.append(
            '- 可选 activity 输出：{"action":"play|pause|resume|stop|previous|next","target":"current","source_id":"可选 workspace:attachment:xxx / workspace:generated:xxx / file/audio/gen handle"}；不需要控制时输出 null。'
        )
        lines.append(
            "- activity 是给桌宠执行的请求，不是执行成功回执；speech 里不要说已经播放、已经暂停或已经继续，"
            "可以自然说“我来试试”“我帮你继续”。"
        )
        lines.append(
            "- 切换到某个具体音频时，play 应尽量带 source_id（推荐列表中已有 source_id）；只继续当前音频时，用 resume + target=current。"
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
    title = sanitize_desktop_context_text(activity.get("title"), 120) or "当前系统音乐"
    lyric_status = str(activity.get("lyric_status") or activity.get("lyricStatus") or "").strip().lower()
    lyric_reason = str(activity.get("lyric_reason") or activity.get("lyricReason") or "").strip().lower()
    lines = [
        "【当前音乐位置】",
        f"- 正在播放：{title}",
    ]
    progress = format_activity_time(activity.get("progress_seconds"))
    duration = format_activity_time(activity.get("duration_seconds"))
    if progress:
        timing = f"当前进度：{progress}"
        if duration:
            timing += f" / {duration}"
        lines.append(f"- {timing}。")
    if lyric_status == "low-confidence":
        lines.append("- 在线歌词匹配可信度不够，当前不要引用歌词原文。")
    elif lyric_status == "not-found":
        lines.append("- 暂时没有找到这首歌的同步歌词。")
    elif lyric_status == "disabled":
        lines.append("- 在线歌词检索已关闭。")
    elif lyric_status == "unavailable":
        reason_hint = "provider 不可用" if lyric_reason else "歌词线索暂不可用"
        lines.append(f"- {reason_hint}，当前不能确定唱到哪一句。")
    else:
        lines.append("- 歌词线索还没准备好，当前还不能确定唱到哪一句。")
    lines.append("- 你现在只知道系统正在播放的歌曲、播放状态和进度；不要编造歌词内容。")
    lines.append("- 请把这理解成你自然注意到旁边正在播放的音乐，不要说自己在读文件。")
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


def build_client_mode_prompt_context(client_context: ClientProtocolContext | None) -> str:
    if client_context is None:
        return ""
    public = client_context.to_public_dict()
    lines = [
        "【客户端模式】",
        f"当前有效模式：{public.get('effective_mode')}",
        f"输出 profile：{public.get('output_profile')}",
        "本轮只需要遵循当前 profile 的输出字段；不要在台词里解释这些系统字段。",
    ]
    if public.get("degraded_from"):
        lines.append(
            f"请求模式 {public.get('degraded_from')} 已降级为 {public.get('effective_mode')}；"
            "按有效模式输出即可。"
        )
    if public.get("effective_mode") == ClientMode.DESKTOP_PET.value:
        lines.append(
            "桌宠只实际渲染 character.outfit 与 emotion；scene/bgm 不会在桌宠端表现。"
            "请优先保持当前服装，只从当前服装可用表情中选择 emotion。"
        )
        if client_context.has_capability(ClientCapability.AUDIO_PLAYBACK):
            lines.append(
                "桌宠支持轻量 activity 控制：只有当【当前桌宠活动】存在且你确实要控制播放时，"
                "才输出 activity；否则 activity 输出 null。activity 是执行请求，不是完成回执；"
                "不要在 speech 里假装已经播放、暂停或继续。"
                "播放、暂停、继续、切歌这类轻量桌宠播放控制不要创建任务工作区或委托后台任务。"
            )
    return "\n".join(lines)
