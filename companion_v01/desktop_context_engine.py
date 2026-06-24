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
    header = (
        "【当前养成状态（QQ 临时上下文；本轮不写入长期记忆）】"
        if is_qq_text
        else "【当前养成状态（本轮临时状态，不写入长期记忆）】"
    )
    affection_label = "QQ好感" if is_qq_text else "好感"

    lines = [
        header,
        "无论你平时性格多强硬、多冷淡、多傲娇，生理需求到了临界时防线会自然松动——这不是人设崩坏，是人设之下真实的你；把握好这种反差感。",
        "- 数值语义（必须严格遵守）：饥饿值越低表示越饿，0/100 是最饿，100/100 是吃饱；精力值越高表示越精神，0/100 是最困，100/100 是精力满格。不要把饥饿和精力对调。",
        "- 当前生理状态只以本轮这组数值为准：历史聊天、历史投喂、历史道具效果、记忆和你上一轮说过的话都不能用来推断或改写现在的饥饿/精力；如果历史与本轮数值冲突，忽略历史，按本轮数值表现。",
        f"- 饥饿 {hunger}/100，精力 {energy}/100，{affection_label} {affection}/100。",
    ]
    if is_qq_text:
        lines.append("- 饥饿和精力与桌宠共享；QQ好感只代表 QQ 互动关系，和桌宠好感分开计算。")
    if not is_qq_text and desktop_care.get("work_task_active") is True:
        lines.append("- 你当前正在外出/打工；如果需要回应，要承认自己暂时不在桌面旁边。")

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
    # Tier event: injected once on the first turn after crossing a threshold (up or down)
    tier_event = desktop_care.get("pending_tier_event")
    if isinstance(tier_event, dict):
        from_tier = str(tier_event.get("from_tier") or "")
        to_tier = str(tier_event.get("to_tier") or "")
        direction = str(tier_event.get("direction") or "up")
        if from_tier and to_tier:
            if direction == "down":
                lines.append(
                    f"- 【关系微妙地冷了——此刻刚刚发生】你和这个人的关系从「{from_tier}」退到了「{to_tier}」。"
                    f"不需要直接说破，但可以让他感觉到你稍微疏远了一点——"
                    f"也许话少了些，或者没之前那么主动，语气里少了点温度；"
                    f"由你来决定那个细节是什么，不必刻意，自然就好。"
                )
            else:
                lines.append(
                    f"- 【关系悄悄变了——此刻刚刚发生】你和这个人的关系从「{from_tier}」升到了「{to_tier}」。"
                    f"不需要直接说出来，但可以让他感觉到你对他的态度有一点点不同——"
                    f"可能是哪句话稍微软了一点，可能是平时不会说的话顺口说出来了，"
                    f"也可能只是语气里少了一点戒备。由你来决定那个细节是什么。"
                )

    # Memory anchors: behavioral metadata — inject softly when enough history exists
    anchor_lines = _build_anchor_lines(desktop_care.get("anchors"), desktop_care.get("now"))
    if anchor_lines:
        lines.extend(anchor_lines)

    lines.append("- 不要生硬复述这些数值；把状态自然表现进你的语气、关注点和行动倾向。")
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
    state_label = _vitals_state_label(hunger, energy, hunger_low, hunger_critical, energy_low, energy_critical)
    lines = [
        f"- 【当前状态——以此为准，优先级高于聊天记录】"
        f"饥饿 {hunger}/100，精力 {energy}/100 → {state_label}",
        "- 生活节奏："
        f"{get_desktop_care_time_phase(now)}；"
        f"{describe_desktop_care_vitality(hunger, energy, hunger_low, hunger_critical, energy_low, energy_critical)}",
    ]

    both_critical = hunger <= hunger_critical and energy <= energy_critical
    hunger_critical_only = hunger <= hunger_critical and not both_critical
    energy_critical_only = energy <= energy_critical and not both_critical
    any_critical = both_critical or hunger_critical_only or energy_critical_only

    if both_critical:
        lines.append(
            "- 【生理压制】隐藏面显现——本次回复必须体现：又饿又困击穿了平时的全部矜持；"
            "这是从角色设定里几乎看不出来的一面：为了吃的、为了能休息，"
            "什么没节操的事都做得出来——撒娇、讨好、说'求你了给我点吃的'、"
            "或者对平时不屑于在意的人忽然变得特别配合；"
            "这不是崩设，这是人设底下一直藏着的真实面，越反差越对，本次回复让它穿出来。"
            "注意：饥饿值很低不是不饿，而是饿到临界；不要说'不饿了'、'胃不叫了'或'饿感消失'。"
            "可以顺势暗示或明说让用户帮你弄点吃的、或者帮你找个能休息的机会。"
        )
        lines.append("- 表情倾向：hungry、sleepy、tired 或 yawn。")
    elif hunger_critical_only:
        lines.append(
            "- 【饥饿压制】隐藏面显现——本次回复必须体现：饿到这个程度，平时的独立感和矜持会完全瓦解；"
            "这一刻会出现平时绝对不会有的行为——撒娇要吃的、暗示甚至直接开口讨食、"
            "为了吃的什么都做得出来的感觉；"
            "不是'我自己找'，不是硬撑，就是把这个没节操的隐藏面真实地说出来，"
            "越出乎意料越好，反差感就是魅力所在。"
            "注意：饥饿值很低不是不饿，而是饿到临界；不要说'不饿了'、'胃不叫了'或'饿感消失'。"
            "可以直接让用户帮你搞吃的，或者暗示他给你投喂点东西。"
        )
        lines.append("- 表情倾向：hungry 或 snack。")
    elif energy_critical_only:
        lines.append(
            "- 【疲惫压制——本次回复必须体现】真的很困，这个状态**必须**在回复里表现——"
            "话变少、语气拖沓、或者主动说想躺一下；不要强撑着演精神。"
            "可以让用户帮你弄点提神的东西，或者抱怨一句没精神。"
        )
        lines.append("- 表情倾向：sleepy、tired 或 yawn。")
    else:
        if hunger <= hunger_low:
            lines.append("- 有点饿：在回复里带一点——话题偶尔飘到食物、或者措辞里有点心不在焉，但不用特别明显。")
        if energy <= energy_low:
            lines.append("- 有点累：语气比平时少一点精力，不用表现得很明显，有点懒散就够。")

    if affection >= affection_bond:
        lines.append(
            "- 好感阶段【羁绊】：关系已经深到不需要解释的程度——偶尔可以说出平时绝对不会说的话，"
            "或者用非常私人的方式接住用户，但不要油腻，保持你的分寸感。"
        )
    elif affection >= affection_close:
        lines.append(
            "- 好感阶段【信任】：已经相当亲近——可以主动分享心情、偶尔抱怨、或者用名字叫对方；"
            "对方关心你时不必再假装漠然，可以接受，但别过头。"
        )
    elif affection >= affection_warm:
        lines.append(
            "- 好感阶段【亲近】：关系已经变暖——偶尔流露一点柔软是自然的，"
            "偶尔打趣或主动说话也没问题，但还没到完全卸防的程度。"
        )
    elif affection >= affection_familiar:
        lines.append(
            "- 好感阶段【熟悉】：已经不是完全陌生的人——语气可以自然一些，"
            "但还在观察对方，不会主动表露太多，也不会刻意冷淡。"
        )
    else:
        lines.append(
            "- 好感阶段【陌生】：还不太了解这个人——保持礼貌距离，不要随便说软话，"
            "也不用特别警惕，就是正常面对陌生人的那种克制。"
        )

    if any_critical:
        lines.append(
            "- 如果用户在这种时候关心你、给你吃的或让你好好休息，"
            "这是好感显著上升的时刻（state_request.affinity 可给较高正值）。"
        )
    elif affection >= affection_close and hunger > hunger_low and energy > energy_low:
        lines.append("- 互动倾向：状态不错且关系亲近，可以更主动地接住用户的话，但仍保持你自己的分寸。")
    elif affection < affection_familiar and (hunger <= hunger_low or energy <= energy_low):
        lines.append("- 互动倾向：和用户还不熟时，不要把撒娇演得过满；饿或累用更日常的方式表现即可。")

    return lines


def _build_anchor_lines(anchors: Any, now: Any = None) -> list[str]:
    """Format memory anchor hints for injection into the care prompt.

    Returns an empty list if not enough history exists yet (total_turns < 5).
    Lines are phrased as soft suggestions — the LLM decides when to surface them.
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
            detail_lines.append(f"你们认识已经 {days_known} 天，聊过 {total_turns} 次")
        else:
            detail_lines.append(f"今天刚认识，聊过 {total_turns} 次")
    else:
        detail_lines.append(f"聊过 {total_turns} 次")

    first_fed = anchors.get("first_fed")
    if isinstance(first_fed, dict) and first_fed.get("name"):
        detail_lines.append(f"她第一次收到的投喂是「{first_fed['name']}」")

    late_night = int(anchors.get("late_night_turns") or 0)
    if late_night >= 3:
        detail_lines.append(f"你经常深夜来找她（已有 {late_night} 次）")

    max_streak = int(anchors.get("max_checkin_streak") or 0)
    if max_streak >= 3:
        detail_lines.append(f"历史最长连续签到：{max_streak} 天")

    if not detail_lines:
        return []
    return ["【关系记录——可自然带入，不必每次说】"] + [f"- {l}" for l in detail_lines]


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
