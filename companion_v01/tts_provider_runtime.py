"""Voice preference compatibility over the single installed TTS service."""
from __future__ import annotations

from dataclasses import dataclass
import time
import hashlib
import io
import wave
from typing import Any

from .tool_handlers.core import ToolExecutionContext
from .tts_service import SynthesizedTTSResult, TTSServiceError, synthesize_tts_service
from .tts_provider_selection import (
    EDGE_TTS_PROVIDER_ID, GPT_SOVITS_PROVIDER_ID, IRODORI_PROVIDER_ID, TEXT_ONLY_PROVIDER_ID, select_tts_provider,
)


def resolve_tts_runtime_provider(*, engine, payload):
    preference = _resolve_payload_voice_preference(payload)
    character = preference or _resolve_character_voice_preference(engine, payload)
    selection = select_tts_provider(character, allow_unknown=True)
    style = ""
    requested_style = ""
    style_fallback = False
    style_fallback_reason = ""
    if payload.get("speech_protocol") == "speech_units_v1" and selection.reason != "text_only_requested":
        from .speech_policy import character_speech_policy
        provider_id = selection.provider_id
        if not character:
            provider_id = GPT_SOVITS_PROVIDER_ID
        if provider_id not in (GPT_SOVITS_PROVIDER_ID, IRODORI_PROVIDER_ID):
            return {"status": "unavailable", "reason": selection.reason or "speech_units_provider_unsupported",
                    "requestedProviderId": provider_id, "activeProviderId": ""}
        policy = character_speech_policy(engine, str(payload.get("character_pack_id") or payload.get("characterPackId") or ""),
                                         provider_id=provider_id)
        if not policy:
            return {"status": "unavailable", "reason": "character_speech_policy_unavailable"}
        requested = str(payload.get("voice_style") or "")
        requested_style = requested
        if provider_id == IRODORI_PROVIDER_ID:
            from .speech_policy import irodori_caption
            style, style_fallback_reason = irodori_caption(requested)
            style_fallback = bool(requested and not style)
        else:
            style = requested if requested in policy["styles"] else policy["default_style"]
            style_fallback = bool(requested and requested != style)
            character = {**character, "provider": provider_id, "profileId": policy["styles"][style]["profile_id"]}
    selection = select_tts_provider(character, allow_unknown=True)
    return {"status": ("disabled" if selection.reason == "text_only_requested" else "unavailable")
                if selection.reason else "ready",
        "reason": selection.reason, "requestSource": "payload" if preference else "character_pack" if character else "default",
        "requestedProviderId": selection.provider_id, "activeProviderId": "" if selection.reason else selection.provider_id,
        "fallbackProviderId": "", "voiceProfileId": selection.voice_profile_id,
        "profileUserId": _resolve_profile_user_id(payload), "engine": engine,
        "voiceStyle": style, "voiceStyleRequested": requested_style, "voiceStyleFallback": style_fallback,
        "voiceStyleFallbackReason": style_fallback_reason}


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
        emotion="" if resolution.get("voiceStyle") or resolution["requestedProviderId"] == IRODORI_PROVIDER_ID
            else _resolve_tts_emotion(payload), context=context, transient=True,
        **({"caption": resolution["voiceStyle"]} if resolution["requestedProviderId"] == IRODORI_PROVIDER_ID
           and resolution.get("voiceStyle") else {}))


async def synthesize_speech_units(*, engine, units, payload, max_segments=None):
    """QQ composes provider-specific delivery chunks into one complete record."""
    from .speech_units import voice_segments, continuous_voice_chunks
    from .tts_service import MAX_AUDIO_BYTES
    segments = voice_segments(units)
    if max_segments is not None:
        segments = segments[:max(0, int(max_segments))]
    if not segments:
        raise TTSServiceError("empty_voice_text", outcome_known=True)
    chunks, fingerprints = [], []
    params = None
    total = 0
    synthesis = None
    requests = []
    for segment in segments:
        request = {**payload, "speech_protocol": "speech_units_v1", "voice_style": segment["style"]}
        resolution = resolve_tts_runtime_provider(engine=engine, payload=request)
        failure = tts_resolution_failure(resolution)
        if failure:
            raise TTSServiceError(failure, outcome_known=True)
        requests.append((segment, request, resolution))
    # Pin voice selection for the reply before awaiting any synthesis. Switching
    # the character during a QQ reply cannot mix 32 kHz and 48 kHz providers.
    providers = {resolution["requestedProviderId"] for _, _, resolution in requests}
    if len(providers) != 1:
        raise TTSServiceError("speech_units_provider_changed", outcome_known=True)
    if providers == {IRODORI_PROVIDER_ID}:
        grouped = []
        for segment, request, resolution in requests:
            if (grouped and grouped[-1][0]["unit_index"] == segment["unit_index"]
                    and grouped[-1][0]["style"] == segment["style"]
                    and grouped[-1][2]["voiceProfileId"] == resolution["voiceProfileId"]):
                previous, previous_request, previous_resolution = grouped[-1]
                grouped[-1] = ({**previous, "text": previous["text"] + segment["text"]},
                               previous_request, previous_resolution)
            else:
                grouped.append((segment, request, resolution))
        requests = [({**segment, "text": text}, request, resolution)
                    for segment, request, resolution in grouped
                    for text in continuous_voice_chunks(segment["text"])]
    for segment, request, resolution in requests:
        synthesis = await synthesize_tts_resolution(resolution=resolution, text=segment["text"], payload=request)
        if synthesis.media_type != "audio/wav":
            raise TTSServiceError("speech_units_require_wav", outcome_known=True)
        with wave.open(io.BytesIO(synthesis.audio), "rb") as audio:
            current = (audio.getnchannels(), audio.getsampwidth(), audio.getframerate())
            if params is not None and params != current:
                raise TTSServiceError("speech_units_audio_format_mismatch", outcome_known=True)
            params = current
            chunk = audio.readframes(audio.getnframes())
        total += len(chunk)
        if total > MAX_AUDIO_BYTES:
            raise TTSServiceError("tts_audio_size_invalid", outcome_known=True)
        chunks.append(chunk)
        fingerprints.append(synthesis.profile_fingerprint + ":" + synthesis.voice_profile_id)
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(params[0])
        audio.setsampwidth(params[1])
        audio.setframerate(params[2])
        for chunk in chunks:
            audio.writeframesraw(chunk)
    return SynthesizedTTSResult(audio=output.getvalue(), media_type="audio/wav", provider_id=synthesis.provider_id,
        voice_profile_id=synthesis.voice_profile_id, emotion="", emotion_voice_id="",
        profile_fingerprint=hashlib.sha256("\n".join(fingerprints).encode()).hexdigest())


@dataclass(frozen=True)
class ResolvedTTSClient:
    """Original conversation identity; resolve provider afresh at each dequeue."""
    engine: Any
    profile_user_id: str
    session_id: str
    character_pack_id: str

    async def synthesize(self, text):
        return await self.synthesize_command(text)

    async def synthesize_command(self, text, *, invocation_id="", cancel_requested=None, speech_protocol="", voice_style=""):
        if cancel_requested and cancel_requested():
            raise TTSServiceError("invocation_cancelled", status="cancelled", outcome_known=True)
        payload = {"real_user_id": self.profile_user_id, "session_id": self.session_id,
                   "character_pack_id": self.character_pack_id, "client_mode": "desktop_pet",
                   "speech_protocol": speech_protocol, "voice_style": voice_style}
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
