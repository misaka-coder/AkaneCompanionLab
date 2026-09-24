"""Host TTS consumer: service admission, scoped media read, no provider client."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import io
from pathlib import Path
import shutil
import wave

from .generated_files_media import probe_media_info
from .plugin_capability_calls import EnginePluginCapabilityProvider
from .tool_handlers.core import ToolExecutionContext

MAX_AUDIO_BYTES = 32 * 1024 * 1024


class TTSServiceError(RuntimeError):
    def __init__(self, reason, *, status="error", origin=None, outcome_known=False):
        self.reason, self.status = reason, status
        self.origin = dict(origin or {})
        self.outcome_known = bool(outcome_known)
        super().__init__(reason)


@dataclass(frozen=True)
class SynthesizedTTSResult:
    audio: bytes
    media_type: str
    provider_id: str
    voice_profile_id: str
    emotion: str
    emotion_voice_id: str
    profile_fingerprint: str
    generated_handle: str = ""
    origin: dict = field(default_factory=dict)


def _read_audio(resource, *, media_type):
    path = Path(resource["absolute_path"])
    if not 0 < path.stat().st_size <= MAX_AUDIO_BYTES:
        raise TTSServiceError("tts_audio_size_invalid", outcome_known=True)
    audio = path.read_bytes()
    if media_type == "audio/wav":
        try:
            with wave.open(io.BytesIO(audio), "rb") as decoded:
                frames = decoded.getnframes()
                expected = frames * decoded.getnchannels() * decoded.getsampwidth()
                if frames <= 0 or len(decoded.readframes(frames)) != expected:
                    raise ValueError()
        except (wave.Error, EOFError, ValueError):
            raise TTSServiceError("tts_audio_invalid", outcome_known=True) from None
    elif media_type in {"audio/mpeg", "audio/flac", "audio/ogg"}:
        decoder = shutil.which("ffprobe")
        probe = probe_media_info(ffprobe_path=decoder, source_path=path) if decoder else None
        formats = {"audio/mpeg": "mp3", "audio/flac": "flac", "audio/ogg": "ogg"}
        if (not probe or formats[media_type] not in str(probe.get("format", {}).get("format_name", "")).split(",")
                or not any(stream.get("codec_type") == "audio" for stream in probe.get("streams", []))):
            raise TTSServiceError("tts_audio_invalid", outcome_known=True)
    else:
        raise TTSServiceError("tts_media_type_unsupported", outcome_known=True)
    return audio


async def synthesize_tts_service(*, engine, text, voice, emotion, context: ToolExecutionContext, preview=None, transient=False):
    if transient:
        from .plugin_managed_artifacts import transient_audio_scope
        with transient_audio_scope(context.profile_user_id, context.session_id) as resources:
            return await _synthesize_tts_service(engine=engine, text=text, voice=voice, emotion=emotion,
                context=context, preview=preview, transient_resources=resources)
    return await _synthesize_tts_service(engine=engine, text=text, voice=voice, emotion=emotion,
        context=context, preview=preview)


async def _synthesize_tts_service(*, engine, text, voice, emotion, context, preview=None, transient_resources=None):
    files_factory = getattr(engine, "_get_generated_file_service", None)
    if not callable(files_factory):
        raise TTSServiceError("tts_media_service_unavailable", outcome_known=True)
    files = files_factory()
    if files is None:
        raise TTSServiceError("tts_media_service_unavailable", outcome_known=True)
    response = await EnginePluginCapabilityProvider(engine).call_service("tts", "synthesize",
        {"text": text, "voice": dict(voice), "emotion": emotion}, context=context,
        private_parameters={"tts_preview": {**preview, "voice": dict(voice)}} if preview is not None else None)
    result = response.result
    if result.is_error:
        content = result.content if isinstance(result.content, dict) else {}
        raise TTSServiceError(result.reason or "tts_synthesis_failed", status=result.status,
            origin=response.origin, outcome_known=content.get("outcome_known", result.status not in {"error", "unknown"}))
    if response.cancellation_requested:
        raise TTSServiceError("invocation_cancelled", status="cancelled", origin=response.origin, outcome_known=True)
    if callable(response.delivery_allowed) and not response.delivery_allowed():
        raise TTSServiceError("plugin_capability_revoked", status="rejected", origin=response.origin, outcome_known=True)
    content = result.content if isinstance(result.content, dict) else {}
    artifacts = content.get("managed_artifacts")
    value = result.value if result.has_value else None
    if not isinstance(value, dict) or not isinstance(artifacts, list) or len(artifacts) != 1:
        raise TTSServiceError("tts_service_result_invalid", origin=response.origin, outcome_known=True)
    if value.get("provider_id") != voice.get("provider") or value.get("voice_profile_id") != voice.get("profile_id"):
        raise TTSServiceError("tts_voice_mismatch", origin=response.origin, outcome_known=True)
    artifact = artifacts[0]
    handle = artifact.get("generated_handle", "")
    resource = (transient_resources.get(handle) if transient_resources is not None else
        files.resolve_input_resource(profile_user_id=context.profile_user_id,
            session_id=context.session_id, target=handle, timestamp=context.now_ts))
    if not resource or artifact.get("created_by_tool") != response.origin.get("capability_id"):
        raise TTSServiceError("tts_audio_handle_invalid", origin=response.origin, outcome_known=True)
    media_type = artifact.get("mime_type", "")
    if value.get("media_type") != media_type:
        raise TTSServiceError("tts_media_type_mismatch", origin=response.origin, outcome_known=True)
    audio = await asyncio.to_thread(_read_audio, resource, media_type=media_type)
    if callable(response.delivery_allowed) and not response.delivery_allowed():
        raise TTSServiceError("plugin_capability_revoked", status="rejected", origin=response.origin, outcome_known=True)
    if context.cancel_requested and context.cancel_requested():
        raise TTSServiceError("invocation_cancelled", status="cancelled", origin=response.origin, outcome_known=True)
    return SynthesizedTTSResult(audio=audio, media_type=media_type, provider_id=str(value.get("provider_id", "")),
        voice_profile_id=str(value.get("voice_profile_id", "")), emotion=str(value.get("emotion", "")),
        emotion_voice_id=str(value.get("emotion_voice_id", "")), profile_fingerprint=str(value.get("profile_fingerprint", "")),
        generated_handle="" if transient_resources is not None else handle,
        origin={**response.origin, **({"generated_handle": handle} if transient_resources is None else {})})
