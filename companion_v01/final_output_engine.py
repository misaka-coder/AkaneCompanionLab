from __future__ import annotations

from typing import Any

from .client_protocol import ClientCapability, ClientMode, ClientProtocolContext
from .persona_config import PERSONA
from .text_utils import normalize_text, parse_joined_tags
from .tool_invocation import NATIVE_TOOL_CALL_FIELD

_MUSIC_FALLBACK_ACTIONS = (
    (["暂停", "停一下", "先停", "停一停"], "pause"),
    (["继续播放", "继续放", "继续唱", "恢复播放", "接着放"], "resume"),
    (["下一首", "换一首", "切歌", "下首歌", "换首歌"], "next"),
    (["上一首", "上首歌", "前一首", "返回上一首"], "previous"),
    (["放首歌", "放歌", "播放音乐", "播音乐", "放一首", "来首歌"], "play"),
)
_MUSIC_FALLBACK_NEGATIONS = ("不要", "别", "先别", "不用", "不想", "不要再", "别再")

_MEMORY_SUBJECT_SCOPE_ALIASES = {
    "user": "user",
    "self": "user",
    "player": "user",
    "用户": "user",
    "玩家": "user",
    "主人": "user",
    "assistant": "assistant",
    "akane": "assistant",
    "character": "assistant",
    "角色": "assistant",
    "助手": "assistant",
    "当前助手": "assistant",
    "other": "other",
    "third_party": "other",
    "别人": "other",
    "他人": "other",
    "动物": "other",
    "relationship": ["user", "assistant"],
    "shared": ["user", "assistant"],
    "关系": ["user", "assistant"],
    "共同关系": ["user", "assistant"],
    "约定": ["user", "assistant"],
    "topic": "other",
    "project": "other",
    "object": "other",
    "话题": "other",
    "项目": "other",
}

_MEMORY_CATEGORY_ALIASES = {
    "casual": "casual",
    "闲聊": "casual",
    "preference": "preference",
    "偏好": "preference",
    "喜好": "preference",
    "personal_profile": "personal_profile",
    "profile": "personal_profile",
    "身份": "personal_profile",
    "习惯": "personal_profile",
    "plan_goal": "plan_goal",
    "plan": "plan_goal",
    "goal": "plan_goal",
    "计划": "plan_goal",
    "目标": "plan_goal",
    "project_work": "project_work",
    "project": "project_work",
    "work": "project_work",
    "项目": "project_work",
    "创作": "project_work",
    "relationship": "relationship",
    "关系": "relationship",
    "emotion_state": "emotion_state",
    "emotion": "emotion_state",
    "mood": "emotion_state",
    "情绪": "emotion_state",
    "状态": "emotion_state",
    "life_event": "life_event",
    "event": "life_event",
    "生活事件": "life_event",
    "memory_query": "memory_query",
    "memory": "memory_query",
    "记忆查询": "memory_query",
    "system_meta": "system_meta",
    "system": "system_meta",
    "meta": "system_meta",
    "系统": "system_meta",
}
_REPLY_MEDIUM_ALIASES = {
    "text": "text",
    "文字": "text",
    "文本": "text",
    "voice": "voice",
    "audio": "voice",
    "record": "voice",
    "语音": "voice",
    "both": "both",
    "all": "both",
    "text_voice": "both",
    "voice_text": "both",
    "文字语音": "both",
    "双发": "both",
}


def _strip_internal_tool_metadata(tool_call: Any) -> dict[str, Any] | None:
    if not isinstance(tool_call, dict):
        return None
    return {
        str(key): value
        for key, value in tool_call.items()
        if not str(key).startswith("_tool_")
    }

_MEMORY_MOOD_TAG_ALIASES = {
    "calm": "calm",
    "平静": "calm",
    "安静": "calm",
    "warm": "warm",
    "温柔": "warm",
    "温暖": "warm",
    "affectionate": "affectionate",
    "亲近": "affectionate",
    "亲昵": "affectionate",
    "happy": "happy",
    "开心": "happy",
    "高兴": "happy",
    "playful": "playful",
    "俏皮": "playful",
    "吐槽": "playful",
    "curious": "curious",
    "好奇": "curious",
    "thoughtful": "thoughtful",
    "认真": "thoughtful",
    "思考": "thoughtful",
    "touched": "touched",
    "感动": "touched",
    "触动": "touched",
    "proud": "proud",
    "骄傲": "proud",
    "欣慰": "proud",
    "worried": "worried",
    "担心": "worried",
    "忧虑": "worried",
    "lonely": "lonely",
    "孤单": "lonely",
    "寂寞": "lonely",
    "sad": "sad",
    "难过": "sad",
    "低落": "sad",
    "embarrassed": "embarrassed",
    "害羞": "embarrassed",
    "不好意思": "embarrassed",
    "tense": "tense",
    "紧张": "tense",
    "压迫": "tense",
    "annoyed": "annoyed",
    "烦躁": "annoyed",
    "不爽": "annoyed",
    "determined": "determined",
    "坚定": "determined",
    "认真推进": "determined",
}


def normalize_final_output(
    engine: Any,
    *,
    result: dict[str, Any] | None,
    visual_defaults: dict[str, Any],
    profile_user_id: str = "",
    session_id: str = "",
    allow_tool_call: bool,
    debug_enabled: bool,
    client_context: ClientProtocolContext | None = None,
    resource_manifest: Any = None,
    user_message: str = "",
) -> dict[str, Any]:
    client_context = client_context or engine._resolve_client_protocol_context({})
    manifest_service = (
        resource_manifest
        if client_context.effective_mode == ClientMode.QQ_TEXT
        else resource_manifest or engine.resource_manifest
    )
    raw_result = result if isinstance(result, dict) else {}
    normalized = dict(raw_result or {})
    native_tool_call = raw_result.get(NATIVE_TOOL_CALL_FIELD)
    if isinstance(native_tool_call, dict) and native_tool_call:
        normalized[NATIVE_TOOL_CALL_FIELD] = dict(native_tool_call)
    else:
        normalized.pop(NATIVE_TOOL_CALL_FIELD, None)
    persona_request_present = "persona" in raw_result
    persona_request_active = ""
    raw_persona = raw_result.get("persona")
    if isinstance(raw_persona, dict):
        persona_request_active = str(raw_persona.get("active") or "").strip()
    elif persona_request_present:
        persona_request_active = str(raw_persona or "").strip()
    if debug_enabled:
        thought = str(normalized.get("thought") or "").strip()
        normalized["thought"] = thought or PERSONA.final_fallback_thought
    else:
        normalized.pop("thought", None)
    normalized.setdefault("status", "final")
    normalized.setdefault("emotion", visual_defaults["emotion"])
    normalized_tool_call = (
        engine._normalize_tool_call(
            normalized.get("tool_call"),
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )
        if allow_tool_call
        else None
    )
    normalized["tool_call"] = _strip_internal_tool_metadata(normalized_tool_call)
    if client_context.effective_mode == ClientMode.DESKTOP_PET and client_context.has_capability(ClientCapability.AUDIO_PLAYBACK):
        normalized["activity"] = normalize_activity_action(normalized.get("activity"))
    else:
        normalized.pop("activity", None)
    if normalized.get("activity") is None and client_context and client_context.effective_mode == ClientMode.DESKTOP_PET and client_context.has_capability(ClientCapability.AUDIO_PLAYBACK):
        user_text = str(user_message or "").strip().lower()
        if user_text and not any(negation in user_text for negation in _MUSIC_FALLBACK_NEGATIONS):
            for keywords, action in _MUSIC_FALLBACK_ACTIONS:
                if any(kw in user_text for kw in keywords):
                    normalized["activity"] = normalize_activity_action({"action": action, "target": "current"})
                    break
    speech, speech_segments = normalize_speech_payload(
        speech=normalized.get("speech"),
        speech_segments=normalized.get("speech_segments"),
        fallback_to_default=not bool(normalized.get("tool_call") or normalized.get(NATIVE_TOOL_CALL_FIELD)),
    )
    normalized["speech"] = speech
    normalized["speech_segments"] = speech_segments
    if client_context.effective_mode == ClientMode.QQ_TEXT:
        normalized["reply_medium"] = _normalize_reply_medium(
            normalized.get("reply_medium"),
            delivery=normalized.get("delivery"),
            default="text",
        )
    else:
        normalized.pop("reply_medium", None)
    normalized.pop("delivery", None)
    if client_context.effective_mode in (ClientMode.SCENE_STATIC, ClientMode.SCENE_LIVE2D):
        normalized["code_snippet"] = normalize_code_snippet(normalized.get("code_snippet"))
    else:
        normalized.pop("code_snippet", None)
    memory_metadata = normalize_memory_metadata(
        engine,
        normalized.get("memory_metadata"),
        legacy_memory_tags=normalized.get("memory_tags"),
    )
    normalized["memory_metadata"] = memory_metadata
    normalized.pop("memory_tags", None)
    normalized["state_request"] = normalize_state_request(normalized.get("state_request"))
    normalized["choices"] = engine._normalize_choices(normalized.get("choices"))
    persona_service = engine._get_persona_card_service()
    current_persona_id = (
        persona_service.get_active_id(profile_user_id=profile_user_id, session_id=session_id)
        if persona_service is not None and profile_user_id and session_id
        else ""
    )
    normalized["persona"] = {
        "active": persona_request_active if persona_request_present else current_persona_id,
    }
    normalized["_persona_request"] = {
        "present": bool(persona_request_present),
        "active": persona_request_active,
    }
    if not isinstance(normalized.get("character"), dict):
        normalized["character"] = {"outfit": visual_defaults["outfit"]}
    normalized["character"].setdefault("outfit", visual_defaults["outfit"])
    if client_context.effective_mode == ClientMode.DESKTOP_PET:
        # Desktop pet outfit is controlled by the local tray setting. The model
        # may choose an emotion, but should not silently change outfit.
        normalized["character"]["outfit"] = visual_defaults["outfit"]
    if not isinstance(normalized.get("scene"), dict):
        normalized["scene"] = {
            "major": visual_defaults["major"],
            "minor": visual_defaults["minor"],
            "background": visual_defaults["background"],
            "bgm": visual_defaults["bgm"],
        }
    normalized["scene"].setdefault("major", visual_defaults["major"])
    normalized["scene"].setdefault("minor", visual_defaults["minor"])
    normalized["scene"].setdefault("background", visual_defaults["background"])
    normalized["scene"].setdefault("bgm", visual_defaults["bgm"])
    if manifest_service:
        if client_context.effective_mode == ClientMode.QQ_TEXT:
            normalize_emotion = getattr(manifest_service, "normalize_emotion_output", None)
            if callable(normalize_emotion):
                normalized = normalize_emotion(normalized)
        else:
            runtime_projection = engine._get_user_runtime_projection(profile_user_id)
            normalized = manifest_service.normalize_visual_output(
                normalized,
                extra_bgm_tracks=list(runtime_projection.get("extra_bgm_tracks") or []),
                extra_scene_groups=list(runtime_projection.get("extra_scene_groups") or []),
                extra_character_outfits=list(runtime_projection.get("extra_character_outfits") or []),
            )
    normalized = engine._get_output_adapter_registry().normalize(normalized, client_context)
    return normalized


def _normalize_reply_medium(
    value: Any,
    *,
    delivery: Any = None,
    default: str = "text",
) -> str:
    raw_value = value
    if not str(raw_value or "").strip() and isinstance(delivery, dict):
        raw_value = delivery.get("medium") or delivery.get("reply_medium")
    text = str(raw_value or "").strip().lower().replace("-", "_")
    return _REPLY_MEDIUM_ALIASES.get(text, default)


def normalize_memory_metadata(
    engine: Any,
    value: Any,
    *,
    legacy_memory_tags: Any = None,
) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    keyword_inputs = _collect_tag_inputs(raw.get("keywords"), raw.get("memory_tags"), legacy_memory_tags)
    keywords = _normalize_memory_keywords(engine, keyword_inputs)
    subject_scopes = _normalize_enum_list(
        raw.get("subject_scopes") or raw.get("subjects") or raw.get("scope"),
        aliases=_MEMORY_SUBJECT_SCOPE_ALIASES,
        limit=3,
    )
    categories = _normalize_enum_list(
        raw.get("categories") or raw.get("category"),
        aliases=_MEMORY_CATEGORY_ALIASES,
        limit=3,
    )
    mood_tags = _normalize_enum_list(
        raw.get("mood_tags") or raw.get("moods") or raw.get("memory_mood") or raw.get("mood"),
        aliases=_MEMORY_MOOD_TAG_ALIASES,
        limit=3,
    )
    return {
        "keywords": keywords,
        "subject_scopes": subject_scopes,
        "categories": categories,
        "mood_tags": mood_tags,
        "importance": _coerce_unit_float(raw.get("importance"), default=0.0),
        "confidence": _coerce_unit_float(raw.get("confidence"), default=0.0),
    }


def extract_memory_keywords(engine: Any, final_output: dict[str, Any]) -> list[str]:
    output = final_output if isinstance(final_output, dict) else {}
    metadata = output.get("memory_metadata")
    if isinstance(metadata, dict):
        keywords = metadata.get("keywords")
    else:
        keywords = output.get("memory_tags")
    if hasattr(engine, "_normalize_memory_tags"):
        normalized = engine._normalize_memory_tags(keywords)
        if isinstance(normalized, list):
            return [str(item).strip() for item in normalized if str(item).strip()][:4]
    return _normalize_memory_keywords(engine, _collect_tag_inputs(keywords))


def _collect_tag_inputs(*values: Any) -> list[str]:
    items: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, list):
            for item in value:
                if str(item or "").strip():
                    items.append(str(item).strip())
            continue
        if isinstance(value, str):
            normalized = (
                value.replace("，", ",")
                .replace("、", ",")
                .replace("；", ",")
                .replace(";", ",")
                .replace("|", ",")
            )
            items.extend(parse_joined_tags(normalized))
            continue
        text = str(value or "").strip()
        if text:
            items.append(text)
    return items


def _normalize_memory_keywords(engine: Any, raw_items: list[str]) -> list[str]:
    if hasattr(engine, "_normalize_memory_tags"):
        normalized = engine._normalize_memory_tags(raw_items)
        if isinstance(normalized, list):
            return [str(item).strip() for item in normalized if str(item).strip()][:4]
        if isinstance(normalized, str):
            raw_items = _collect_tag_inputs(normalized)
    normalized_items: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        compact = normalize_text(item).strip("[](){}\"' ")
        if not compact or len(compact) > 16:
            continue
        dedupe_key = compact.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized_items.append(compact)
        if len(normalized_items) >= 4:
            break
    return normalized_items


def _normalize_enum_list(
    value: Any,
    *,
    aliases: dict[str, str | list[str]],
    limit: int,
) -> list[str]:
    normalized_items: list[str] = []
    seen: set[str] = set()
    for item in _collect_tag_inputs(value):
        if len(normalized_items) >= limit:
            break
        key = normalize_text(item).lower()
        mapped = aliases.get(key) or aliases.get(key.replace("-", "_"))
        mapped_items = mapped if isinstance(mapped, list) else [mapped]
        for mapped_item in mapped_items:
            if len(normalized_items) >= limit:
                break
            if not mapped_item or mapped_item in seen:
                continue
            seen.add(mapped_item)
            normalized_items.append(mapped_item)
    return normalized_items


def _coerce_unit_float(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return float(max(0.0, min(1.0, number)))


def normalize_speech_payload(
    *,
    speech: Any,
    speech_segments: Any,
    fallback_to_default: bool = True,
) -> tuple[str, list[str]]:
    segments: list[str] = []
    if isinstance(speech_segments, list):
        for item in speech_segments:
            value = item
            if isinstance(item, dict):
                value = item.get("speech") or item.get("text") or ""
            text = " ".join(str(value or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()).strip()
            if not text:
                continue
            segments.append(text[:500])
            if len(segments) >= 3:
                break

    if segments:
        return "\n".join(segments), segments

    text = str(speech or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        if not fallback_to_default:
            return "", []
        text = PERSONA.final_fallback_speech
    inferred_segments = [line.strip() for line in text.split("\n") if line.strip()]
    if 1 < len(inferred_segments) <= 3:
        return "\n".join(inferred_segments), inferred_segments
    return text, [text]


def apply_persona_state_to_final_output(
    engine: Any,
    *,
    profile_user_id: str,
    session_id: str,
    final_output: dict[str, Any],
    now_ts: int,
    source_id: str = "",
    tool_result: Any = None,
) -> dict[str, Any]:
    normalized = dict(final_output or {})
    request = normalized.pop("_persona_request", {})
    request_present = bool(request.get("present")) if isinstance(request, dict) else False
    requested_active = str(request.get("active") or "").strip() if isinstance(request, dict) else ""
    persona_tool_changed = bool(
        tool_result
        and isinstance(tool_result.state_updates, dict)
        and tool_result.state_updates.get("persona_state_changed")
    )
    persona_service = engine._get_persona_card_service()
    if persona_service is None:
        existing_persona = normalized.get("persona")
        active_id = str(existing_persona.get("active") or "").strip() if isinstance(existing_persona, dict) else ""
        normalized["persona"] = {"active": active_id}
        return normalized
    state = persona_service.apply_final_persona_request(
        profile_user_id=profile_user_id,
        session_id=session_id,
        requested_active=requested_active,
        request_present=request_present,
        allow_transition=not persona_tool_changed,
        timestamp=now_ts,
        source_id=source_id,
    )
    normalized["persona"] = {
        "active": str(state.get("active_id") or ""),
    }
    return normalized


def normalize_state_request(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    affinity = value.get("affinity")
    if affinity is not None:
        try:
            result["affinity"] = max(-5, min(5, int(affinity)))
        except (TypeError, ValueError):
            pass
    return result or None


def normalize_code_snippet(value: Any) -> str:
    text = str(value or "")
    if not text.strip():
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if normalized.startswith("```") and normalized.endswith("```"):
        lines = normalized.splitlines()
        if len(lines) >= 2:
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            normalized = "\n".join(lines).strip()
    return normalized[:4000]


def normalize_activity_action(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    action = str(value.get("action") or "").strip().lower()
    if action not in {"play", "pause", "resume", "stop", "previous", "next"}:
        return None
    target = str(value.get("target") or "current").strip()[:80] or "current"
    normalized: dict[str, Any] = {
        "action": action,
        "target": target,
    }
    source_id = str(value.get("source_id") or value.get("source") or value.get("handle") or "").strip()
    if source_id:
        normalized["source_id"] = source_id[:80]
    activity_type = str(value.get("type") or value.get("activity_type") or "").strip().lower()
    if activity_type in {"audio_playback", "vocal_performance"}:
        normalized["type"] = activity_type
    return normalized


def build_assistant_dialogue_turn(speech: Any, *, speaker_name: str | None = None) -> dict[str, str] | None:
    text = str(speech or "").strip()
    if not text:
        return None
    return {
        "speaker": speaker_name or PERSONA.assistant_name,
        "speech": text,
    }


def build_dialogue_turns(
    *,
    preface_turn: dict[str, str] | list[dict[str, str]] | None,
    npc_turns: list[dict[str, Any]],
    final_speech: Any,
    final_speech_segments: Any = None,
    speaker_name: str | None = None,
) -> list[dict[str, str]]:
    turns: list[dict[str, str]] = []
    if isinstance(preface_turn, list):
        turns.extend([turn for turn in preface_turn if isinstance(turn, dict)])
    elif preface_turn:
        turns.append(preface_turn)

    for npc_turn in npc_turns:
        speaker = str(npc_turn.get("speaker") or "NPC").strip() or "NPC"
        speech = str(npc_turn.get("speech") or "").strip()
        if not speech:
            continue
        turns.append(
            {
                "speaker": speaker,
                "speech": speech,
            }
        )

    if isinstance(final_speech_segments, list) and final_speech_segments:
        for segment in final_speech_segments:
            final_turn = build_assistant_dialogue_turn(segment, speaker_name=speaker_name)
            if final_turn:
                turns.append(final_turn)
    else:
        final_turn = build_assistant_dialogue_turn(final_speech, speaker_name=speaker_name)
        if final_turn:
            turns.append(final_turn)

    normalized: list[dict[str, str]] = []
    for turn in turns:
        if normalized and normalized[-1] == turn:
            continue
        normalized.append(turn)
    return normalized
