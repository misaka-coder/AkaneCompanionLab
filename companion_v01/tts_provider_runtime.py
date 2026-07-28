from __future__ import annotations

import inspect
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from services.tts_client import GptSovitsTTSClient

from .local_capability_config import (
    CONFIGURABLE_PROVIDER_BY_ID,
    build_provider_config_entry,
    get_voice_profile_runtime_config,
    load_capability_config,
)
from .runtime_settings import runtime_setting


GPT_SOVITS_PROVIDER_ID = "provider.tts.gpt_sovits.local"
EDGE_TTS_PROVIDER_ID = "provider.tts.edge"
TEXT_ONLY_PROVIDER_ID = "provider.voice.text_only"


class ResolvedTTSClient:
    """Bind one resolved provider/profile behind the common ``synthesize`` port."""

    def __init__(
        self,
        *,
        provider_id: str,
        client: Any,
        voice_profile_id: str = "",
        voice_profile: Mapping[str, Any] | None = None,
    ) -> None:
        normalized_provider = str(provider_id or "").strip()
        if not normalized_provider or client is None:
            raise ValueError("resolved_tts_client_invalid")
        self.provider_id = normalized_provider
        self.client = client
        self.voice_profile_id = str(voice_profile_id or "").strip()
        self.voice_profile = dict(voice_profile or {})

    async def synthesize(self, text: str) -> Any:
        synthesize = getattr(self.client, "synthesize", None)
        if not callable(synthesize):
            raise RuntimeError("tts_client_unavailable")
        if self.provider_id == GPT_SOVITS_PROVIDER_ID:
            result = synthesize(
                text,
                voice_profile_id=self.voice_profile_id,
                profile=self.voice_profile,
            )
        else:
            result = synthesize(text)
        if inspect.isawaitable(result):
            return await result
        return result


def resolve_tts_runtime_provider(
    *,
    engine: Any,
    payload: Mapping[str, Any],
    base_dir: Path | None,
    config_module: Any,
    edge_tts_available: bool,
    gpt_sovits_client_factory: Callable[[str], Any] | None,
    settings: Any = None,
) -> dict[str, Any]:
    payload_voice = _resolve_payload_voice_preference(payload)
    character_voice = payload_voice or _resolve_character_voice_preference(engine, payload)
    request_source = "payload" if payload_voice else ("character_pack" if character_voice else "default")
    raw_provider = str(character_voice.get("provider") or "").strip()
    voice_profile_id = _safe_voice_profile_id(character_voice.get("profileId") or character_voice.get("profile_id"))
    requested_provider_id = _normalize_voice_provider_id(raw_provider)
    if not requested_provider_id:
        requested_provider_id = (
            GPT_SOVITS_PROVIDER_ID if voice_profile_id and not raw_provider else EDGE_TTS_PROVIDER_ID
        )
    profile_user_id = _resolve_profile_user_id(payload)
    resolution: dict[str, Any] = {
        "status": "ready",
        "reason": "",
        "requestSource": request_source,
        "requestedProviderId": requested_provider_id,
        "activeProviderId": EDGE_TTS_PROVIDER_ID if edge_tts_available else "",
        "fallbackProviderId": "",
        "voiceProfileId": voice_profile_id,
        "profileUserId": profile_user_id,
        "client": None,
        "voiceProfile": {},
    }

    if requested_provider_id != GPT_SOVITS_PROVIDER_ID:
        if not edge_tts_available:
            resolution.update({"status": "unavailable", "reason": "tts_client_unavailable"})
        return resolution

    if not voice_profile_id:
        return _with_edge_fallback(
            resolution,
            edge_tts_available=edge_tts_available,
            reason="requested_voice_profile_missing",
        )

    provider_spec = CONFIGURABLE_PROVIDER_BY_ID.get(GPT_SOVITS_PROVIDER_ID)
    config = load_capability_config(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
    )
    provider_config = config.get("providers", {}).get(GPT_SOVITS_PROVIDER_ID)
    provider_entry = build_provider_config_entry(provider_spec, provider_config) if provider_spec is not None else {}
    status = str(provider_entry.get("status") or "").strip()
    endpoint = str(provider_entry.get("endpoint") or "").strip()
    if status not in {"configured", "ready"} or not endpoint:
        return _with_edge_fallback(
            resolution,
            edge_tts_available=edge_tts_available,
            reason=_provider_unavailable_reason(status),
        )

    try:
        factory = gpt_sovits_client_factory or default_gpt_sovits_client_factory(
            config_module,
            settings=settings,
        )
        client = factory(endpoint)
    except Exception:
        return _with_edge_fallback(
            resolution,
            edge_tts_available=edge_tts_available,
            reason="gpt_sovits_client_unavailable",
        )

    resolution.update(
        {
            "status": "ready",
            "reason": "",
            "activeProviderId": GPT_SOVITS_PROVIDER_ID,
            "fallbackProviderId": "",
            "client": client,
            "voiceProfile": get_voice_profile_runtime_config(
                base_dir=base_dir,
                profile_user_id=profile_user_id,
                voice_profile_id=voice_profile_id,
            ),
        }
    )
    return resolution


def resolve_character_tts_client(
    *,
    engine: Any,
    profile_user_id: str,
    session_id: str,
    character_pack_id: str,
    base_dir: Path | None,
    config_module: Any,
    settings: Any,
    edge_tts_client: Any,
    allow_requested_provider_fallback: bool = False,
    gpt_sovits_client_factory: Callable[[str], Any] | None = None,
) -> ResolvedTTSClient | None:
    resolution = resolve_tts_runtime_provider(
        engine=engine,
        payload={
            "real_user_id": str(profile_user_id or ""),
            "session_id": str(session_id or ""),
            "character_pack_id": str(character_pack_id or ""),
            "client_mode": "desktop_pet",
        },
        base_dir=base_dir,
        config_module=config_module,
        settings=settings,
        edge_tts_available=edge_tts_client is not None,
        gpt_sovits_client_factory=gpt_sovits_client_factory,
    )
    requested_provider = str(resolution.get("requestedProviderId") or "")
    active_provider = str(resolution.get("activeProviderId") or "")
    if (
        requested_provider == GPT_SOVITS_PROVIDER_ID
        and active_provider != requested_provider
        and not allow_requested_provider_fallback
    ):
        return None
    if active_provider == GPT_SOVITS_PROVIDER_ID:
        client = resolution.get("client")
    elif active_provider == EDGE_TTS_PROVIDER_ID:
        client = edge_tts_client
    else:
        return None
    if client is None:
        return None
    return ResolvedTTSClient(
        provider_id=active_provider,
        client=client,
        voice_profile_id=str(resolution.get("voiceProfileId") or ""),
        voice_profile=(resolution.get("voiceProfile") if isinstance(resolution.get("voiceProfile"), Mapping) else {}),
    )


def default_gpt_sovits_client_factory(
    config_module: Any,
    *,
    settings: Any = None,
) -> Callable[[str], GptSovitsTTSClient]:
    timeout_seconds = float(
        runtime_setting(
            settings,
            config_module,
            "gpt_sovits_tts_timeout_seconds",
            "GPT_SOVITS_TTS_TIMEOUT_SECONDS",
            45.0,
        )
        or 45.0
    )
    text_lang = str(
        runtime_setting(
            settings,
            config_module,
            "gpt_sovits_text_lang",
            "GPT_SOVITS_TEXT_LANG",
            "zh",
        )
        or "zh"
    )
    media_type = str(
        runtime_setting(
            settings,
            config_module,
            "gpt_sovits_media_type",
            "GPT_SOVITS_MEDIA_TYPE",
            "wav",
        )
        or "wav"
    )
    streaming_mode = bool(
        runtime_setting(
            settings,
            config_module,
            "gpt_sovits_streaming_mode",
            "GPT_SOVITS_STREAMING_MODE",
            False,
        )
    )
    parallel_infer = runtime_setting(
        settings,
        config_module,
        "gpt_sovits_parallel_infer",
        "GPT_SOVITS_PARALLEL_INFER",
        None,
    )
    split_bucket = runtime_setting(
        settings,
        config_module,
        "gpt_sovits_split_bucket",
        "GPT_SOVITS_SPLIT_BUCKET",
        None,
    )
    batch_size = runtime_setting(
        settings,
        config_module,
        "gpt_sovits_batch_size",
        "GPT_SOVITS_BATCH_SIZE",
        None,
    )
    speed_factor = runtime_setting(
        settings,
        config_module,
        "gpt_sovits_speed_factor",
        "GPT_SOVITS_SPEED_FACTOR",
        None,
    )
    fragment_interval = runtime_setting(
        settings,
        config_module,
        "gpt_sovits_fragment_interval",
        "GPT_SOVITS_FRAGMENT_INTERVAL",
        None,
    )
    text_split_method = str(
        runtime_setting(
            settings,
            config_module,
            "gpt_sovits_text_split_method",
            "GPT_SOVITS_TEXT_SPLIT_METHOD",
            "",
        )
        or ""
    )

    def factory(endpoint: str) -> GptSovitsTTSClient:
        return GptSovitsTTSClient(
            endpoint,
            timeout_seconds=timeout_seconds,
            text_lang=text_lang,
            media_type=media_type,
            streaming_mode=streaming_mode,
            parallel_infer=parallel_infer,
            split_bucket=split_bucket,
            batch_size=batch_size,
            speed_factor=speed_factor,
            fragment_interval=fragment_interval,
            text_split_method=text_split_method,
        )

    return factory


def _with_edge_fallback(
    resolution: dict[str, Any],
    *,
    edge_tts_available: bool,
    reason: str,
) -> dict[str, Any]:
    active = EDGE_TTS_PROVIDER_ID if edge_tts_available else ""
    return {
        **resolution,
        "status": "degraded" if active else "unavailable",
        "reason": reason,
        "activeProviderId": active,
        "fallbackProviderId": active,
        "client": None,
    }


def _resolve_payload_voice_preference(payload: Mapping[str, Any]) -> dict[str, str]:
    provider = _safe_voice_hint_text(
        payload.get("voiceProvider")
        or payload.get("voice_provider")
        or payload.get("ttsProvider")
        or payload.get("tts_provider")
        or payload.get("ttsProviderId")
        or payload.get("tts_provider_id")
        or payload.get("requestedProviderId")
    )
    profile_id = _safe_voice_profile_id(
        payload.get("voiceProfileId")
        or payload.get("voice_profile_id")
        or payload.get("profileId")
        or payload.get("profile_id")
    )
    if not (provider or profile_id):
        return {}
    return {
        "provider": provider,
        "profileId": profile_id,
    }


def _resolve_character_voice_preference(
    engine: Any,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    pack_id = str(
        payload.get("character_pack_id") or payload.get("characterPackId") or payload.get("character_pack") or ""
    ).strip()
    if not pack_id:
        return {}
    service = getattr(engine, "desktop_pet_character_resources", None)
    builder = getattr(service, "build_character_voice_preference", None)
    if not callable(builder):
        return {}
    try:
        result = builder(pack_id)
    except Exception:
        return {}
    return result if isinstance(result, dict) else {}


def _resolve_profile_user_id(payload: Mapping[str, Any]) -> str:
    value = str(
        payload.get("real_user_id") or payload.get("profileUserId") or payload.get("profile_user_id") or ""
    ).strip()
    return value or "master"


def _safe_voice_hint_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 120:
        return ""
    lowered = text.lower()
    if (
        "://" in text
        or "/" in text
        or "\\" in text
        or ":" in text
        or ".." in text
        or "token" in lowered
        or "secret" in lowered
        or "password" in lowered
        or "api_key" in lowered
    ):
        return ""
    return text


def _normalize_voice_provider_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    aliases = {
        "edge": EDGE_TTS_PROVIDER_ID,
        "edge_tts": EDGE_TTS_PROVIDER_ID,
        "edge-tts": EDGE_TTS_PROVIDER_ID,
        EDGE_TTS_PROVIDER_ID: EDGE_TTS_PROVIDER_ID,
        "gpt_sovits": GPT_SOVITS_PROVIDER_ID,
        "gpt-sovits": GPT_SOVITS_PROVIDER_ID,
        "gptsovits": GPT_SOVITS_PROVIDER_ID,
        GPT_SOVITS_PROVIDER_ID: GPT_SOVITS_PROVIDER_ID,
        "text": TEXT_ONLY_PROVIDER_ID,
        "text_only": TEXT_ONLY_PROVIDER_ID,
        "none": TEXT_ONLY_PROVIDER_ID,
        TEXT_ONLY_PROVIDER_ID: TEXT_ONLY_PROVIDER_ID,
    }
    return aliases.get(raw.lower(), "")


def _safe_voice_profile_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 120:
        return ""
    lowered = text.lower()
    if (
        "://" in text
        or "/" in text
        or "\\" in text
        or ":" in text
        or ".." in text
        or "token" in lowered
        or "secret" in lowered
        or "password" in lowered
        or "api_key" in lowered
    ):
        return ""
    return text


def _provider_unavailable_reason(status: str) -> str:
    if status == "missing_config":
        return "requested_provider_missing_config"
    if status == "disabled":
        return "requested_provider_disabled"
    if status == "invalid_config":
        return "requested_provider_invalid_config"
    if status == "unreachable":
        return "requested_provider_unreachable"
    if status:
        return "requested_provider_not_ready"
    return "requested_provider_unknown"


__all__ = [
    "EDGE_TTS_PROVIDER_ID",
    "GPT_SOVITS_PROVIDER_ID",
    "ResolvedTTSClient",
    "TEXT_ONLY_PROVIDER_ID",
    "default_gpt_sovits_client_factory",
    "resolve_character_tts_client",
    "resolve_tts_runtime_provider",
]
