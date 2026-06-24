from __future__ import annotations

import inspect
import re
from typing import Any, Mapping

from .types import (
    CapabilityDescriptor,
    CapabilityIOSlot,
    CapabilityProtocolError,
    CapabilityResult,
    HealthStatus,
    InvocationContext,
)


_CAPABILITY_ID = "tts.synthesize"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_MEDIA_TYPE_RE = re.compile(r"^[a-z0-9.+-]+/[a-z0-9.+-]+$")
_SECRET_MARKERS = ("api_key", "password", "secret", "token")


class OpenAICompatTTSAdapter:
    type = "openai_compat_tts"

    def __init__(
        self,
        *,
        provider_id: str,
        client: Any,
        default_media_type: str = "audio/mpeg",
        display_name: str = "Text to speech",
    ) -> None:
        self.provider_id = _safe_public_token(provider_id) or "provider.tts"
        self.client = client
        self.default_media_type = _safe_media_type(default_media_type, default="audio/mpeg")
        self.display_name = str(display_name or "Text to speech").strip()[:80] or "Text to speech"

    async def health(self) -> HealthStatus:
        if self.client is None or not callable(getattr(self.client, "synthesize", None)):
            return HealthStatus(ok=False, status="missing_config", reason="tts_client_unavailable")
        return HealthStatus(ok=True, status="ready")

    async def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        return (
            CapabilityDescriptor(
                id=_CAPABILITY_ID,
                display_name=self.display_name,
                short_hint="Synthesize assistant speech as audio.",
                visible_in=("desktop", "web", "qq"),
                prompt_exposed=False,
                risk="low",
                confirm="never",
                effects=("media_generation",),
                trigger=None,
                inputs=(
                    CapabilityIOSlot(name="text", kind="string", required=True),
                    CapabilityIOSlot(name="emotion", kind="string", required=False),
                    CapabilityIOSlot(name="voice_profile_id", kind="string", required=False),
                ),
                outputs=(CapabilityIOSlot(name="audio", kind="audio_bytes", delivery="tts_audio"),),
                raw={"providerId": self.provider_id},
            ),
        )

    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        ctx: InvocationContext,
    ) -> CapabilityResult:
        if str(capability_id or "").strip() != _CAPABILITY_ID:
            raise CapabilityProtocolError("unknown_capability")
        text = str((args or {}).get("text") or "").strip()
        if not text:
            raise CapabilityProtocolError("text_required")
        client = self.client
        synthesize = getattr(client, "synthesize", None)
        if not callable(synthesize):
            raise CapabilityProtocolError("tts_client_unavailable")

        profile = _profile_with_emotion_overlay((args or {}).get("profile"), (args or {}).get("emotion"))
        voice_profile_id = _safe_public_token(
            (args or {}).get("voice_profile_id")
            or (args or {}).get("voiceProfileId")
            or (profile.get("id") if isinstance(profile, Mapping) else "")
        )
        try:
            if profile or voice_profile_id:
                result = synthesize(text, voice_profile_id=voice_profile_id, profile=profile)
            else:
                result = synthesize(text)
            if inspect.isawaitable(result):
                result = await result
            audio, media_type = _coerce_audio(result, default_media_type=self.default_media_type)
        except ValueError:
            raise
        except CapabilityProtocolError:
            raise
        except Exception as exc:
            raise CapabilityProtocolError("tts_provider_failed") from exc
        return CapabilityResult(
            is_error=False,
            status="ok",
            content={
                "audio": audio,
                "mediaType": media_type,
                "providerId": self.provider_id,
                "voiceProfileId": voice_profile_id,
                "emotion": _safe_public_token((args or {}).get("emotion")),
            },
        )

    async def aclose(self) -> None:
        return None


def _profile_with_emotion_overlay(profile: Any, emotion: Any) -> dict[str, Any]:
    base = dict(profile) if isinstance(profile, Mapping) else {}
    emotion_key = _safe_public_token(emotion)
    if not emotion_key:
        return base
    raw_map = base.get("emotionVoiceMap") or base.get("emotion_voice_map")
    if not isinstance(raw_map, Mapping):
        return base
    raw_entry = raw_map.get(emotion_key) or raw_map.get(emotion_key.lower())
    if raw_entry is None:
        lowered = {str(key).strip().lower(): value for key, value in raw_map.items()}
        raw_entry = lowered.get(emotion_key.lower())
    overlay = _sanitize_emotion_voice_entry(raw_entry)
    if overlay:
        base.update(overlay)
    return base


def _sanitize_emotion_voice_entry(raw_entry: Any) -> dict[str, Any]:
    if isinstance(raw_entry, str):
        ref_audio_path = _safe_private_path(raw_entry)
        return {"refAudioPath": ref_audio_path} if ref_audio_path else {}
    if not isinstance(raw_entry, Mapping):
        return {}
    result: dict[str, Any] = {}
    ref_audio_path = _safe_private_path(
        raw_entry.get("refAudioPath")
        or raw_entry.get("ref_audio_path")
        or raw_entry.get("referenceAudioPath")
        or raw_entry.get("reference_audio_path")
    )
    if ref_audio_path:
        result["refAudioPath"] = ref_audio_path
    prompt_text = _safe_prompt_text(raw_entry.get("promptText") or raw_entry.get("prompt_text"))
    if prompt_text:
        result["promptText"] = prompt_text
    for canonical_key, aliases in {
        "textLang": ("textLang", "text_lang"),
        "promptLang": ("promptLang", "prompt_lang"),
        "mediaType": ("mediaType", "media_type"),
    }.items():
        raw_value = next((raw_entry.get(alias) for alias in aliases if raw_entry.get(alias) not in (None, "")), "")
        value = _safe_public_token(raw_value)
        if value:
            result[canonical_key] = value
    return result


def _coerce_audio(result: Any, *, default_media_type: str) -> tuple[bytes, str]:
    if isinstance(result, bytes):
        audio = result
        media_type = default_media_type
    else:
        audio = bytes(getattr(result, "audio", b"") or b"")
        media_type = str(getattr(result, "media_type", "") or default_media_type)
    if not audio:
        raise CapabilityProtocolError("tts_returned_empty_audio")
    return audio, _safe_media_type(media_type, default=default_media_type)


def _safe_media_type(value: Any, *, default: str) -> str:
    text = str(value or "").split(";", 1)[0].strip().lower()
    return text[:80] if _MEDIA_TYPE_RE.fullmatch(text) else default


def _safe_public_token(value: Any) -> str:
    text = str(value or "").strip()
    return text if _TOKEN_RE.fullmatch(text) else ""


def _safe_private_path(value: Any) -> str:
    text = str(value or "").strip().replace("\r", "").replace("\n", "")
    if not text:
        return ""
    lowered = text.lower()
    if "://" in text or any(marker in lowered for marker in _SECRET_MARKERS):
        return ""
    return text[:500]


def _safe_prompt_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\r\n", "\n").replace("\r", "\n")).strip()
    if not text:
        return ""
    lowered = text.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        return ""
    return text[:300]
