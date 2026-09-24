"""Voice preference compatibility over the single installed TTS service."""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from .tool_handlers.core import ToolExecutionContext
from .tts_service import SynthesizedTTSResult, TTSServiceError, synthesize_tts_service
from .tts_provider_selection import (
    EDGE_TTS_PROVIDER_ID, GPT_SOVITS_PROVIDER_ID, TEXT_ONLY_PROVIDER_ID, select_tts_provider,
)


def resolve_tts_runtime_provider(*, engine, payload):
    preference = _resolve_payload_voice_preference(payload)
    character = preference or _resolve_character_voice_preference(engine, payload)
    selection = select_tts_provider(character, allow_unknown=True)
    return {"status": ("disabled" if selection.reason == "text_only_requested" else "unavailable")
                if selection.reason else "ready",
        "reason": selection.reason, "requestSource": "payload" if preference else "character_pack" if character else "default",
        "requestedProviderId": selection.provider_id, "activeProviderId": "" if selection.reason else selection.provider_id,
        "fallbackProviderId": "", "voiceProfileId": selection.voice_profile_id,
        "profileUserId": _resolve_profile_user_id(payload), "engine": engine}


def tts_resolution_failure(resolution):
    if resolution.get("status") != "ready" or resolution.get("activeProviderId") != resolution.get("requestedProviderId"):
        return str(resolution.get("reason") or "requested_tts_provider_unavailable")
    return ""


async def synthesize_tts_resolution(*, resolution, text, payload, default_media_type="audio/mpeg",
                                    invocation_id="", cancel_requested=None):
    failure = tts_resolution_failure(resolution)
    if failure:
        raise TTSServiceError(failure, status=resolution.get("status", "unavailable"), outcome_known=True)
    engine = resolution.get("engine")
    if engine is None:
        raise TTSServiceError("tts_service_unavailable")
    profile = _resolve_profile_user_id(payload)
    session = str(payload.get("session_id") or payload.get("user_id") or profile).strip()
    context = ToolExecutionContext(profile, session, int(time.time()), {},
        character_pack_id=str(payload.get("character_pack_id") or payload.get("characterPackId") or ""),
        client_mode=str(payload.get("client_mode") or payload.get("client") or "web"),
        invocation_id=invocation_id, cancel_requested=cancel_requested, result_consumer="program")
    return await synthesize_tts_service(engine=engine, text=str(text or "").strip(),
        voice={"provider": resolution["requestedProviderId"], "profile_id": resolution.get("voiceProfileId", "")},
        emotion=_resolve_tts_emotion(payload), context=context, transient=True)


@dataclass(frozen=True)
class ResolvedTTSClient:
    """Original conversation identity; resolve provider afresh at each dequeue."""
    engine: Any
    profile_user_id: str
    session_id: str
    character_pack_id: str

    async def synthesize(self, text):
        return await self.synthesize_command(text)

    async def synthesize_command(self, text, *, invocation_id="", cancel_requested=None):
        if cancel_requested and cancel_requested():
            raise TTSServiceError("invocation_cancelled", status="cancelled", outcome_known=True)
        payload = {"real_user_id": self.profile_user_id, "session_id": self.session_id,
                   "character_pack_id": self.character_pack_id, "client_mode": "desktop_pet"}
        resolution = resolve_tts_runtime_provider(engine=self.engine, payload=payload)
        return await synthesize_tts_resolution(resolution=resolution, text=text, payload=payload,
            invocation_id=invocation_id, cancel_requested=cancel_requested)


def resolve_character_tts_client(*, engine, profile_user_id, session_id, character_pack_id):
    payload = {"real_user_id": profile_user_id, "session_id": session_id, "character_pack_id": character_pack_id}
    if tts_resolution_failure(resolve_tts_runtime_provider(engine=engine, payload=payload)):
        return None
    return ResolvedTTSClient(engine, str(profile_user_id), str(session_id), str(character_pack_id))


def _resolve_tts_emotion(payload):
    value = (payload.get("emotion") or payload.get("currentEmotion") or payload.get("current_emotion")
        or payload.get("finalEmotion") or payload.get("final_emotion"))
    if not value:
        return ""
    from capcore_adapter_speech import safe_emotion_id
    return safe_emotion_id(value)


def _resolve_payload_voice_preference(payload):
    raw_provider = next((payload.get(key) for key in ("voiceProvider", "voice_provider", "ttsProvider", "tts_provider",
        "ttsProviderId", "tts_provider_id", "requestedProviderId") if payload.get(key)), None)
    raw_profile = next((payload.get(key) for key in ("voiceProfileId", "voice_profile_id", "profileId", "profile_id")
                        if payload.get(key)), None)
    return {"provider": raw_provider, "profileId": raw_profile} if raw_provider or raw_profile else {}


def _resolve_character_voice_preference(engine, payload):
    pack_id = str(payload.get("character_pack_id") or payload.get("characterPackId") or payload.get("character_pack") or "").strip()
    builder = getattr(getattr(engine, "desktop_pet_character_resources", None), "build_character_voice_preference", None)
    if not pack_id or not callable(builder):
        return {}
    try:
        result = builder(pack_id)
    except Exception:
        return {}
    return result if isinstance(result, dict) else {}


def _resolve_profile_user_id(payload):
    return str(payload.get("real_user_id") or payload.get("profileUserId") or payload.get("profile_user_id") or "master").strip()
