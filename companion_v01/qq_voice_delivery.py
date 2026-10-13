"""QQ speech synthesis and bounded, atomic audio artifact caching."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path
from typing import Any

from .tts_provider_runtime import (
    resolve_tts_runtime_provider as _resolve_tts_runtime_provider,
    synthesize_tts_resolution,
    tts_resolution_failure,
)


def _media_type_extension(media_type: str) -> str:
    clean = str(media_type or "").split(";", 1)[0].strip().lower()
    return {
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/ogg": "ogg",
        "audio/opus": "opus",
        "audio/flac": "flac",
        "audio/aac": "aac",
        "audio/mp4": "m4a",
    }.get(clean, "wav")


def _run_async_safely(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def _resolve_qq_tts_profile_user_id(*, config_module: Any, context: Any, settings: Any = None) -> str:
    from .tts_connection_source import qq_voice_config_profile

    return qq_voice_config_profile(config_module, context, settings)


def _voice_cache_key(*, text: str, synthesis: Any) -> str:
    """Name the delivered artifact from the effective synthesis inputs."""
    raw = (
        f"{text}\nprovider={synthesis.provider_id}\nprofile={synthesis.voice_profile_id}"
        f"\nemotion={synthesis.emotion}\nemotion_voice={synthesis.emotion_voice_id}"
        f"\nprofile_fingerprint={synthesis.profile_fingerprint}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _prune_voice_cache(cache_dir: Path, *, max_mb: int = 50) -> None:
    """Trim the voice cache to at most *max_mb* by removing the oldest files."""
    max_bytes = max(1, max_mb) * 1024 * 1024
    try:
        entries = sorted(cache_dir.iterdir(), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    total = 0
    # Walk newest-first and decide what to keep
    for entry in reversed(entries):
        try:
            total += entry.stat().st_size
        except OSError:
            continue
    for entry in entries:
        if total <= max_bytes:
            break
        try:
            size = entry.stat().st_size
            entry.unlink()
            total -= size
        except OSError:
            continue


def _synthesize_qq_voice_file(
    *,
    engine: Any,
    config_module: Any,
    tts_client: Any,
    text: str,
    context: Any,
    emotion: str = "",
    settings: Any = None,
    utterances: list[dict[str, Any]] | None = None,
    max_voice_segments: int | None = None,
) -> dict[str, Any]:
    clean_text = str(text or "").strip()
    if not clean_text:
        return {"ok": False, "reason": "empty_voice_text"}

    data_dir = Path(str(getattr(config_module, "DATA_DIR", "users_data") or "users_data"))
    tts_profile_user_id = _resolve_qq_tts_profile_user_id(
        config_module=config_module,
        context=context,
        settings=settings,
    )
    payload = {
        "text": clean_text,
        "real_user_id": str(getattr(context, "profile_user_id", "") or tts_profile_user_id),
        "profile_user_id": str(getattr(context, "profile_user_id", "") or tts_profile_user_id),
        "character_pack_id": str(getattr(context, "character_pack_id", "") or ""),
        "session_id": str(getattr(context, "session_id", "") or ""),
        "client_mode": "qq",
        "emotion": str(emotion or "").strip(),
    }
    from .speech_policy import character_speech_policy
    expects_speech_units = utterances is not None or bool(
        character_speech_policy(engine, payload["character_pack_id"])
    )
    if expects_speech_units:
        payload["speech_protocol"] = "speech_units_v1"
    resolution = _resolve_tts_runtime_provider(
        engine=engine,
        payload=payload,
    )

    active_provider = str(resolution.get("activeProviderId") or "")
    requested_provider = str(resolution.get("requestedProviderId") or "")
    failure = tts_resolution_failure(resolution)
    if failure:
        return {
            "ok": False,
            "reason": failure,
            "provider": active_provider,
            "requested_provider": requested_provider,
            "resolution_status": str(resolution.get("status") or ""),
            "tts_profile_user_id": tts_profile_user_id,
        }
    from .tts_service import TTSServiceError
    try:
        if expects_speech_units and utterances is None:
            raise TTSServiceError("speech_units_required", outcome_known=True)
        if utterances is not None:
            from .tts_provider_runtime import synthesize_speech_units
            synthesis = _run_async_safely(synthesize_speech_units(
                engine=engine, units=utterances, payload=payload, max_segments=max_voice_segments,
            ))
        else:
            synthesis = _run_async_safely(synthesize_tts_resolution(
                resolution=resolution, text=clean_text, payload=payload,
            ))
    except TTSServiceError as exc:
        return {"ok": False, "reason": exc.reason, "provider": "",
                "requested_provider": requested_provider, "resolution_status": exc.status,
                "tts_profile_user_id": tts_profile_user_id}

    cache_dir = data_dir / "qq_voice_cache"
    ext = _media_type_extension(synthesis.media_type)
    cache_key = _voice_cache_key(text=clean_text, synthesis=synthesis)
    path = cache_dir / f"{cache_key}.{ext}"
    temp_path = cache_dir / f".{cache_key}.{uuid.uuid4().hex}.tmp"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            temp_path.write_bytes(synthesis.audio)
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)
    except OSError:
        return {
            "ok": False,
            "reason": "voice_cache_write_failed",
            "provider": active_provider,
            "requested_provider": requested_provider,
            "resolution_status": str(resolution.get("status") or ""),
            "tts_profile_user_id": tts_profile_user_id,
        }
    _prune_voice_cache(cache_dir, max_mb=50)
    return {
        "ok": True,
        "path": str(path),
        "media_type": synthesis.media_type,
        "provider": active_provider,
        "emotion": synthesis.emotion,
        "emotion_voice_id": synthesis.emotion_voice_id,
        "resolution_status": str(resolution.get("status") or ""),
        "tts_profile_user_id": tts_profile_user_id,
    }
