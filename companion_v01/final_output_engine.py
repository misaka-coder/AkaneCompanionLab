from __future__ import annotations

from typing import Any

import config
from memcore import coerce_memory_metadata

from .client_protocol import ClientCapability, ClientMode, ClientProtocolContext
from .persona_config import PERSONA
from .tool_invocation import NATIVE_TOOL_CALL_FIELD, NATIVE_TOOL_CALLS_FIELD

_MUSIC_FALLBACK_ACTIONS = (
    (["暂停", "停一下", "先停", "停一停"], "pause"),
    (["继续播放", "继续放", "继续唱", "恢复播放", "接着放"], "resume"),
    (["下一首", "换一首", "切歌", "下首歌", "换首歌"], "next"),
    (["上一首", "上首歌", "前一首", "返回上一首"], "previous"),
    (["放首歌", "放歌", "播放音乐", "播音乐", "放一首", "来首歌"], "play"),
)
_MUSIC_FALLBACK_NEGATIONS = ("不要", "别", "先别", "不用", "不想", "不要再", "别再")

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
    return {str(key): value for key, value in tool_call.items() if not str(key).startswith("_tool_")}


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
    native_tool_calls = raw_result.get(NATIVE_TOOL_CALLS_FIELD)
    if isinstance(native_tool_calls, list):
        normalized_calls = [dict(call) for call in native_tool_calls if isinstance(call, dict) and call]
        if normalized_calls:
            normalized[NATIVE_TOOL_CALLS_FIELD] = normalized_calls
        else:
            normalized.pop(NATIVE_TOOL_CALLS_FIELD, None)
    else:
        normalized.pop(NATIVE_TOOL_CALLS_FIELD, None)
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
    if client_context.effective_mode == ClientMode.DESKTOP_PET and client_context.has_capability(
        ClientCapability.AUDIO_PLAYBACK
    ):
        normalized["activity"] = normalize_activity_action(normalized.get("activity"))
    else:
        normalized.pop("activity", None)
    if (
        normalized.get("activity") is None
        and client_context
        and client_context.effective_mode == ClientMode.DESKTOP_PET
        and client_context.has_capability(ClientCapability.AUDIO_PLAYBACK)
    ):
        user_text = str(user_message or "").strip().lower()
        if user_text and not any(negation in user_text for negation in _MUSIC_FALLBACK_NEGATIONS):
            for keywords, action in _MUSIC_FALLBACK_ACTIONS:
                if any(kw in user_text for kw in keywords):
                    normalized["activity"] = normalize_activity_action({"action": action, "target": "current"})
                    break
    speech, speech_segments = normalize_speech_payload(
        speech=normalized.get("speech"),
        speech_segments=normalized.get("speech_segments"),
        fallback_to_default=not bool(
            normalized.get("tool_call")
            or normalized.get(NATIVE_TOOL_CALL_FIELD)
            or normalized.get(NATIVE_TOOL_CALLS_FIELD)
        ),
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
    memory_metadata = normalize_memory_metadata(engine, normalized.get("memory_metadata"))
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
) -> dict[str, Any]:
    manager = getattr(engine, "memcore_manager", None)
    enable_flavor = bool(
        getattr(manager, "enable_flavor", getattr(config, "MEMCORE_ENABLE_FLAVOR", True))
    )
    return coerce_memory_metadata(value, enable_flavor=enable_flavor).to_dict()


def extract_memory_search_terms(final_output: dict[str, Any]) -> list[str]:
    """Project canonical anchors/topics into the legacy store's semantic tags.

    This projection is not a metadata contract and must not mutate the canonical
    ``memory_metadata`` object.
    """

    output = final_output if isinstance(final_output, dict) else {}
    metadata = output.get("memory_metadata")
    if not isinstance(metadata, dict):
        return []
    terms: list[str] = []
    seen: set[str] = set()
    for field in ("entity_anchors", "topic_terms"):
        for item in list(metadata.get(field) or []):
            term = str(item or "").strip()
            key = term.casefold()
            if not term or key in seen:
                continue
            seen.add(key)
            terms.append(term)
    return terms


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
