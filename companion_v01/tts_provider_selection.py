"""Shared TTS preference interpretation for runtime and capability presentation."""

from dataclasses import dataclass
import re
from typing import Any, Mapping


GPT_SOVITS_PROVIDER_ID = "provider.tts.gpt_sovits.local"
EDGE_TTS_PROVIDER_ID = "provider.tts.edge"
TEXT_ONLY_PROVIDER_ID = "provider.voice.text_only"


@dataclass(frozen=True)
class TTSProviderSelection:
    provider_id: str
    voice_profile_id: str
    reason: str = ""


def select_tts_provider(preference: Mapping[str, Any], *, allow_unknown: bool = False) -> TTSProviderSelection:
    raw_provider = str(preference.get("provider") or "").strip()
    raw_profile = preference.get("profileId") or preference.get("profile_id")
    profile_id = _safe_voice_profile_id(raw_profile)
    aliases = {
        "edge": EDGE_TTS_PROVIDER_ID,
        "edge_tts": EDGE_TTS_PROVIDER_ID,
        "edge-tts": EDGE_TTS_PROVIDER_ID,
        "gpt_sovits": GPT_SOVITS_PROVIDER_ID,
        "gpt-sovits": GPT_SOVITS_PROVIDER_ID,
        "gptsovits": GPT_SOVITS_PROVIDER_ID,
        "text": TEXT_ONLY_PROVIDER_ID,
        "text_only": TEXT_ONLY_PROVIDER_ID,
        "none": TEXT_ONLY_PROVIDER_ID,
        **{value: value for value in (EDGE_TTS_PROVIDER_ID, GPT_SOVITS_PROVIDER_ID, TEXT_ONLY_PROVIDER_ID)},
    }
    if raw_provider and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", raw_provider):
        return TTSProviderSelection("", profile_id, "requested_provider_invalid")
    provider = (
        aliases.get(raw_provider.lower())
        if raw_provider
        else (GPT_SOVITS_PROVIDER_ID if raw_profile else EDGE_TTS_PROVIDER_ID)
    )
    if provider is None:
        return TTSProviderSelection(raw_provider, profile_id, "" if allow_unknown else "requested_provider_unknown")
    if provider == TEXT_ONLY_PROVIDER_ID:
        return TTSProviderSelection(provider, profile_id, "text_only_requested")
    if provider == GPT_SOVITS_PROVIDER_ID and not profile_id:
        return TTSProviderSelection(provider, "", "requested_voice_profile_missing")
    return TTSProviderSelection(provider, profile_id)


def _safe_voice_profile_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 120:
        return ""
    lowered = text.lower()
    if any(part in text for part in ("://", "/", "\\", ":", "..")) or any(
        part in lowered for part in ("token", "secret", "password", "api_key")
    ):
        return ""
    return text
