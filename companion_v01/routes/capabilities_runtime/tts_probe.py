"""Capability HTTP tts probe responsibilities."""
from __future__ import annotations
import base64
import time
from pathlib import Path
from typing import Any
from ...local_capability_config import get_voice_profile_runtime_config
from ...tts_service import synthesize_tts_service
from ...tts_provider_selection import IRODORI_PROVIDER_ID
from ...tool_runtime import ToolExecutionContext
from .definitions import ProviderTtsTestRunner, GPT_SOVITS_PROVIDER_ID, PROVIDER_TTS_TEST_AUDIO_MAX_BYTES
from .voice_payloads import (
    _resolve_provider_tts_test_endpoint,
    _safe_provider_tts_test_text,
    _provider_tts_test_profile_payload,
    _provider_tts_test_profile_checks,
    _safe_provider_tts_profile_id,
    _coerce_provider_tts_test_audio,
)
from .common import _maybe_thread


async def _run_provider_tts_test(
    *,
    base_dir: Path | None,
    profile_user_id: str,
    provider_id: str,
    payload: dict[str, Any],
    engine: Any = None,
    session_id: str = "",
    config_module: Any = None,
    settings: Any = None,
    runner: ProviderTtsTestRunner | None = None,
) -> dict[str, Any]:
    provider_id = str(provider_id or "").strip()
    if provider_id not in (GPT_SOVITS_PROVIDER_ID, IRODORI_PROVIDER_ID):
        return {
            "ok": False,
            "status": "unsupported_provider",
            "providerId": provider_id,
            "reason": "provider_tts_test_not_supported",
            "refresh": False,
        }

    endpoint_result = _resolve_provider_tts_test_endpoint(
        base_dir=base_dir,
        profile_user_id=profile_user_id,
        payload=payload,
        provider_id=provider_id,
    )
    if not endpoint_result.get("ok"):
        return {
            "ok": False,
            "status": endpoint_result.get("status") or "missing_config",
            "providerId": provider_id,
            "reason": endpoint_result.get("reason") or "provider_endpoint_missing",
            "refresh": False,
        }

    text = _safe_provider_tts_test_text(payload.get("text") or
        ("おかえりなさい。今日もお疲れさまでした。" if provider_id == IRODORI_PROVIDER_ID else ""))
    voice_profile_id = _safe_provider_tts_profile_id(
        payload.get("voiceProfileId") or payload.get("voice_profile_id") or payload.get("profileId")
    )
    voice_profile = (_provider_tts_test_profile_payload(payload) if provider_id == GPT_SOVITS_PROVIDER_ID
                     else ({"providerId": provider_id, "irodoriVoiceId": payload["irodoriVoiceId"]}
                           if payload.get("irodoriVoiceId") else {}))
    profile_source = "payload" if voice_profile else "none"
    if voice_profile_id:
        saved_profile = get_voice_profile_runtime_config(
            base_dir=base_dir,
            profile_user_id=profile_user_id,
            voice_profile_id=voice_profile_id,
        )
        if saved_profile.get("providerId") == provider_id:
            voice_profile = {
                **saved_profile,
                **voice_profile,
            }
            profile_source = "saved+payload" if profile_source == "payload" else "saved"
        elif profile_source == "none":
            profile_source = "missing"
    endpoint = str(endpoint_result.get("endpoint") or "")
    profile_checks = _provider_tts_test_profile_checks(
        endpoint=endpoint,
        voice_profile_id=voice_profile_id,
        voice_profile=voice_profile,
        profile_source=profile_source,
    )
    try:
        if runner is not None:
            result = await _maybe_thread(lambda: runner(endpoint=endpoint, text=text, voice_profile_id=voice_profile_id))
        else:
            result = await synthesize_tts_service(
                engine=engine, text=text,
                voice={"provider": provider_id, "profile_id": voice_profile_id},
                emotion=str(payload.get("emotion") or ""),
                context=ToolExecutionContext(profile_user_id, session_id or profile_user_id,
                    int(time.time()), {}, client_mode="web", result_consumer="program"),
                preview={"endpoint": endpoint, "profile": voice_profile},
            )
        audio, media_type = _coerce_provider_tts_test_audio(result)
    except ValueError:
        return {
            "ok": False,
            "status": "invalid_config",
            "providerId": provider_id,
            "reason": "provider_tts_test_invalid_config",
            "voiceProfileId": voice_profile_id,
            "profileApplied": bool(voice_profile),
            "profileSource": profile_source,
            "checks": profile_checks,
            "refresh": False,
        }
    except RuntimeError as exc:
        reason = getattr(exc, "reason", "") or (str(exc) if str(exc) == "provider_tts_test_empty_audio" else "provider_tts_test_failed")
        return {
            "ok": False,
            "status": "tts-test-failed",
            "providerId": provider_id,
            "reason": reason,
            "voiceProfileId": voice_profile_id,
            "profileApplied": bool(voice_profile),
            "profileSource": profile_source,
            "checks": profile_checks,
            "refresh": False,
        }
    except Exception:
        return {
            "ok": False,
            "status": "tts-test-failed",
            "providerId": provider_id,
            "reason": "provider_tts_test_failed",
            "voiceProfileId": voice_profile_id,
            "profileApplied": bool(voice_profile),
            "profileSource": profile_source,
            "checks": profile_checks,
            "refresh": False,
        }

    if len(audio) > PROVIDER_TTS_TEST_AUDIO_MAX_BYTES:
        return {
            "ok": False,
            "status": "tts-test-too-large",
            "providerId": provider_id,
            "reason": "provider_tts_test_audio_too_large",
            "refresh": False,
        }
    return {
        "ok": True,
        "status": "tts-test-ready",
        "providerId": provider_id,
        "mediaType": media_type,
        "audioBase64": base64.b64encode(audio).decode("ascii"),
        "audioBytes": len(audio),
        "textLength": len(text),
        "voiceProfileId": voice_profile_id,
        "profileApplied": bool(voice_profile),
        "profileSource": profile_source,
        "checks": profile_checks,
        "refresh": False,
    }
