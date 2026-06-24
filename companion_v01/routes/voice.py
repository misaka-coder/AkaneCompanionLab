from __future__ import annotations

import asyncio
import importlib.util
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ..capability_adapters import CapabilityResult, InvocationContext, OpenAICompatASRAdapter, OpenAICompatTTSAdapter
from ..desktop_pet_contract import DESKTOP_PET_CONTRACT_VERSION, build_desktop_pet_error_payload
from ..local_capability_config import (
    CONFIGURABLE_PROVIDER_BY_ID,
    build_provider_config_entry,
    get_voice_profile_runtime_config,
    load_capability_config,
)
from services.tts_client import GptSovitsTTSClient, SynthesizedAudio


LogEvent = Callable[..., None]


def build_voice_router(
    *,
    engine: Any,
    config_module: Any,
    tts_client: Any,
    runtime_metrics: Any,
    log_event: LogEvent,
    capability_config_base_dir: str | Path | None = None,
    gpt_sovits_client_factory: Callable[[str], Any] | None = None,
    asr_adapter_factory: Callable[[str], Any] | None = None,
) -> APIRouter:
    router = APIRouter()
    provider_config_base_dir = _resolve_provider_config_base_dir(
        capability_config_base_dir=capability_config_base_dir,
        config_module=config_module,
    )

    @router.post("/asr")
    async def asr(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        try:
            form = await request.form()
        except Exception as exc:
            runtime_metrics.observe_request("asr", duration_ms=(time.perf_counter() - started_at) * 1000, ok=False)
            return JSONResponse(
                {
                    "ok": False,
                    "error": "multipart_parse_failed",
                    "message": f"无法读取录音上传内容：{str(exc)[:160]}",
                },
                status_code=400,
            )

        upload = form.get("file") or form.get("audio")
        if upload is None or not hasattr(upload, "read"):
            runtime_metrics.observe_request("asr", duration_ms=(time.perf_counter() - started_at) * 1000, ok=False)
            return JSONResponse(
                {"ok": False, "error": "missing_file", "message": "没有收到录音文件。"},
                status_code=400,
            )

        audio_bytes = await upload.read()
        max_bytes = int(float(getattr(config_module, "ASR_MAX_UPLOAD_MB", 20)) * 1024 * 1024)
        if len(audio_bytes) > max_bytes:
            runtime_metrics.observe_request("asr", duration_ms=(time.perf_counter() - started_at) * 1000, ok=False)
            return JSONResponse(
                {"ok": False, "error": "audio_too_large", "message": "录音太长啦，先说短一点试试。"},
                status_code=413,
            )
        if len(audio_bytes) < 512:
            runtime_metrics.observe_request("asr", duration_ms=(time.perf_counter() - started_at) * 1000, ok=False)
            return JSONResponse({"ok": False, "error": "audio_too_short", "message": "录音太短啦，我没听清。"})

        filename = str(getattr(upload, "filename", "") or "akane_voice_input.webm")
        language = str(form.get("language") or "zh").strip()
        profile_user_id = str(form.get("real_user_id") or form.get("profileUserId") or form.get("profile_user_id") or "").strip() or "master"
        asr_resolution = _resolve_asr_runtime_provider(
            base_dir=provider_config_base_dir,
            profile_user_id=profile_user_id,
            config_module=config_module,
            asr_adapter_factory=asr_adapter_factory,
        )
        if asr_resolution.get("activeProviderId") == OPENAI_COMPAT_ASR_PROVIDER_ID:
            try:
                result = await _invoke_asr_adapter(
                    adapter=asr_resolution["adapter"],
                    audio_bytes=audio_bytes,
                    filename=filename,
                    content_type=str(getattr(upload, "content_type", "") or ""),
                    language=language,
                    profile_user_id=profile_user_id,
                )
                duration_ms = (time.perf_counter() - started_at) * 1000
                runtime_metrics.observe_request("asr", duration_ms=duration_ms, ok=bool(result.get("ok")))
                log_event(
                    "asr_complete",
                    ok=bool(result.get("ok")),
                    duration_ms=round(duration_ms, 1),
                    text_length=len(str(result.get("text") or "")),
                    provider=OPENAI_COMPAT_ASR_PROVIDER_ID,
                    error=str(result.get("error") or ""),
                )
                status_code = 200 if result.get("ok") else int(result.get("_status_code") or 200)
                result.pop("_status_code", None)
                return JSONResponse(result, status_code=status_code)
            except Exception as exc:
                log_event(
                    "asr_provider_fallback",
                    provider=OPENAI_COMPAT_ASR_PROVIDER_ID,
                    fallbackProviderId="provider.asr.faster_whisper",
                    reason="openai_compat_asr_failed",
                    errorType=_safe_tts_reason(type(exc).__name__),
                )
        try:
            result = await asyncio.to_thread(
                run_asr_transcription,
                engine=engine,
                config_module=config_module,
                audio_bytes=audio_bytes,
                filename=filename,
                language=language,
                content_type=str(getattr(upload, "content_type", "") or ""),
            )
        except Exception as exc:
            runtime_metrics.observe_request("asr", duration_ms=(time.perf_counter() - started_at) * 1000, ok=False)
            log_event("asr_error", message=str(exc)[:240])
            return JSONResponse(
                {"ok": False, "error": "asr_failed", "message": f"语音识别失败：{str(exc)[:160]}"},
                status_code=500,
            )

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("asr", duration_ms=duration_ms, ok=bool(result.get("ok")))
        log_event(
            "asr_complete",
            ok=bool(result.get("ok")),
            duration_ms=round(duration_ms, 1),
            text_length=len(str(result.get("text") or "")),
            error=str(result.get("error") or ""),
        )
        status_code = 200 if result.get("ok") else int(result.get("_status_code") or 200)
        result.pop("_status_code", None)
        return JSONResponse(result, status_code=status_code)

    @router.post("/tts")
    async def tts(request: Request) -> Response:
        started_at = time.perf_counter()
        try:
            payload = await request.json()
        except Exception as exc:
            runtime_metrics.observe_request("tts", duration_ms=(time.perf_counter() - started_at) * 1000, ok=False)
            return JSONResponse(
                build_desktop_pet_error_payload(
                    error="invalid_json",
                    message=f"无法读取 TTS 请求：{str(exc)[:160]}",
                    retryable=False,
                ),
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        text = str(payload.get("text") or "").strip()
        if not text:
            runtime_metrics.observe_request("tts", duration_ms=(time.perf_counter() - started_at) * 1000, ok=False)
            return JSONResponse(
                build_desktop_pet_error_payload(
                    error="missing_text",
                    message="text is required",
                    retryable=False,
                ),
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )

        resolution = _resolve_tts_runtime_provider(
            engine=engine,
            payload=payload,
            base_dir=provider_config_base_dir,
            config_module=config_module,
            edge_tts_available=tts_client is not None,
            gpt_sovits_client_factory=gpt_sovits_client_factory,
        )
        headers = _tts_response_headers(resolution)

        if resolution["activeProviderId"] == GPT_SOVITS_PROVIDER_ID:
            try:
                audio, media_type = await _invoke_tts_adapter(
                    provider_id=GPT_SOVITS_PROVIDER_ID,
                    client=resolution["client"],
                    text=text,
                    payload=payload,
                    resolution=resolution,
                    default_media_type="audio/wav",
                )
                duration_ms = (time.perf_counter() - started_at) * 1000
                runtime_metrics.observe_request("tts", duration_ms=duration_ms, ok=True)
                log_event(
                    "tts_complete",
                    duration_ms=round(duration_ms, 1),
                    text_length=len(text),
                    provider=GPT_SOVITS_PROVIDER_ID,
                    status=resolution.get("status"),
                )
                return Response(content=audio, media_type=media_type, headers=headers)
            except Exception as exc:
                resolution = {
                    **resolution,
                    "status": "degraded",
                    "activeProviderId": EDGE_TTS_PROVIDER_ID if tts_client is not None else "",
                    "fallbackProviderId": EDGE_TTS_PROVIDER_ID if tts_client is not None else "",
                    "reason": "gpt_sovits_failed",
                }
                headers = _tts_response_headers(resolution)
                log_event(
                    "tts_provider_fallback",
                    provider=GPT_SOVITS_PROVIDER_ID,
                    fallbackProviderId=resolution.get("fallbackProviderId"),
                    reason="gpt_sovits_failed",
                    errorType=_safe_tts_reason(type(exc).__name__),
                    text_length=len(text),
                )

        if tts_client is None:
            runtime_metrics.observe_request("tts", duration_ms=(time.perf_counter() - started_at) * 1000, ok=False)
            return JSONResponse(
                build_desktop_pet_error_payload(
                    error="tts_disabled",
                    message="TTS client is unavailable.",
                    retryable=False,
                ),
                status_code=503,
                headers=headers,
            )

        try:
            audio, media_type = await _invoke_tts_adapter(
                provider_id=EDGE_TTS_PROVIDER_ID,
                client=tts_client,
                text=text,
                payload=payload,
                resolution=resolution,
                default_media_type="audio/mpeg",
            )
        except ValueError as exc:
            runtime_metrics.observe_request("tts", duration_ms=(time.perf_counter() - started_at) * 1000, ok=False)
            log_event("tts_error", message=str(exc), text_length=len(text))
            return JSONResponse(
                build_desktop_pet_error_payload(
                    error="tts_request_invalid",
                    message=str(exc),
                    retryable=False,
                ),
                status_code=400,
                headers=headers,
            )
        except Exception as exc:
            runtime_metrics.observe_request("tts", duration_ms=(time.perf_counter() - started_at) * 1000, ok=False)
            log_event("tts_error", message=str(exc), text_length=len(text))
            return JSONResponse(
                build_desktop_pet_error_payload(
                    error="tts_failed",
                    message=f"TTS failed: {str(exc)[:200]}",
                    retryable=True,
                ),
                status_code=502,
                headers=headers,
            )

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("tts", duration_ms=duration_ms, ok=True)
        log_event(
            "tts_complete",
            duration_ms=round(duration_ms, 1),
            text_length=len(text),
            provider=resolution.get("activeProviderId") or EDGE_TTS_PROVIDER_ID,
            status=resolution.get("status"),
        )
        return Response(
            content=audio,
            media_type=media_type,
            headers=headers,
        )

    return router


async def _invoke_asr_adapter(
    *,
    adapter: Any,
    audio_bytes: bytes,
    filename: str,
    content_type: str,
    language: str,
    profile_user_id: str,
) -> dict[str, Any]:
    result = await adapter.invoke(
        "asr.transcribe",
        {
            "audio": audio_bytes,
            "filename": filename,
            "content_type": content_type,
            "language": language,
        },
        InvocationContext(profile_user_id=profile_user_id, client_mode="desktop_pet"),
    )
    content = result.content if isinstance(result.content, Mapping) else {}
    text = " ".join(str(content.get("text") or "").split()).strip()
    if result.is_error or not text:
        return {
            "ok": False,
            "error": result.reason or result.status or "no_speech",
            "message": "没听清，可以再说一次。",
        }
    payload: dict[str, Any] = {
        "ok": True,
        "text": text,
        "providerId": OPENAI_COMPAT_ASR_PROVIDER_ID,
    }
    language_value = str(content.get("language") or "").strip()
    if language_value:
        payload["language"] = language_value
    duration = content.get("duration_seconds")
    if isinstance(duration, (int, float)) and duration >= 0:
        payload["duration_seconds"] = duration
    return payload


async def _invoke_tts_adapter(
    *,
    provider_id: str,
    client: Any,
    text: str,
    payload: Mapping[str, Any],
    resolution: Mapping[str, Any],
    default_media_type: str,
) -> tuple[bytes, str]:
    adapter = OpenAICompatTTSAdapter(
        provider_id=provider_id,
        client=client,
        default_media_type=default_media_type,
        display_name="Akane TTS",
    )
    args: dict[str, Any] = {
        "text": text,
        "emotion": _resolve_tts_emotion(payload),
    }
    if provider_id == GPT_SOVITS_PROVIDER_ID:
        voice_profile_id = str(resolution.get("voiceProfileId") or "").strip()
        if voice_profile_id:
            args["voice_profile_id"] = voice_profile_id
        voice_profile = resolution.get("voiceProfile")
        if isinstance(voice_profile, Mapping) and voice_profile:
            args["profile"] = voice_profile
    result = await adapter.invoke(
        "tts.synthesize",
        args,
        InvocationContext(
            profile_user_id=str(resolution.get("profileUserId") or ""),
            session_id=str(payload.get("session_id") or payload.get("user_id") or ""),
            client_mode=str(payload.get("client_mode") or payload.get("client") or ""),
        ),
    )
    return _coerce_tts_capability_result(result, default_media_type=default_media_type)


GPT_SOVITS_PROVIDER_ID = "provider.tts.gpt_sovits.local"
EDGE_TTS_PROVIDER_ID = "provider.tts.edge"
TEXT_ONLY_PROVIDER_ID = "provider.voice.text_only"
OPENAI_COMPAT_ASR_PROVIDER_ID = "provider.asr.openai_compat.local"


def _resolve_asr_runtime_provider(
    *,
    base_dir: Path | None,
    profile_user_id: str,
    config_module: Any,
    asr_adapter_factory: Callable[[str], Any] | None,
) -> dict[str, Any]:
    resolution: dict[str, Any] = {
        "status": "default",
        "reason": "",
        "requestedProviderId": "provider.asr.faster_whisper",
        "activeProviderId": "",
        "adapter": None,
        "profileUserId": profile_user_id,
    }
    provider_spec = CONFIGURABLE_PROVIDER_BY_ID.get(OPENAI_COMPAT_ASR_PROVIDER_ID)
    if provider_spec is None:
        return resolution
    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
    provider_config = config.get("providers", {}).get(OPENAI_COMPAT_ASR_PROVIDER_ID)
    provider_entry = build_provider_config_entry(provider_spec, provider_config)
    status = str(provider_entry.get("status") or "").strip()
    endpoint = str(provider_entry.get("endpoint") or "").strip()
    if status not in {"configured", "ready"} or not endpoint:
        return {
            **resolution,
            "status": "degraded",
            "reason": _provider_unavailable_reason(status),
            "requestedProviderId": OPENAI_COMPAT_ASR_PROVIDER_ID if provider_config else "provider.asr.faster_whisper",
        }
    try:
        if asr_adapter_factory is not None:
            adapter = asr_adapter_factory(endpoint)
        else:
            adapter = OpenAICompatASRAdapter(
                provider_id=OPENAI_COMPAT_ASR_PROVIDER_ID,
                endpoint=endpoint,
                timeout_seconds=float(getattr(config_module, "OPENAI_COMPAT_ASR_TIMEOUT_SECONDS", 45.0) or 45.0),
                model=str(getattr(config_module, "OPENAI_COMPAT_ASR_MODEL", "whisper-1") or "whisper-1"),
            )
    except Exception:
        return {
            **resolution,
            "status": "degraded",
            "reason": "openai_compat_asr_adapter_unavailable",
            "requestedProviderId": OPENAI_COMPAT_ASR_PROVIDER_ID,
        }
    return {
        **resolution,
        "status": "ready",
        "reason": "",
        "requestedProviderId": OPENAI_COMPAT_ASR_PROVIDER_ID,
        "activeProviderId": OPENAI_COMPAT_ASR_PROVIDER_ID,
        "adapter": adapter,
    }


def _resolve_tts_runtime_provider(
    *,
    engine: Any,
    payload: Mapping[str, Any],
    base_dir: Path | None,
    config_module: Any,
    edge_tts_available: bool,
    gpt_sovits_client_factory: Callable[[str], Any] | None,
) -> dict[str, Any]:
    payload_voice = _resolve_payload_voice_preference(payload)
    character_voice = payload_voice or _resolve_character_voice_preference(engine, payload)
    request_source = "payload" if payload_voice else ("character_pack" if character_voice else "default")
    raw_provider = str(character_voice.get("provider") or "").strip()
    voice_profile_id = _safe_voice_profile_id(character_voice.get("profileId") or character_voice.get("profile_id"))
    requested_provider_id = _normalize_voice_provider_id(raw_provider)
    if not requested_provider_id:
        requested_provider_id = GPT_SOVITS_PROVIDER_ID if voice_profile_id and not raw_provider else EDGE_TTS_PROVIDER_ID
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
    config = load_capability_config(base_dir=base_dir, profile_user_id=profile_user_id)
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
        factory = gpt_sovits_client_factory or _default_gpt_sovits_client_factory(config_module)
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


def _default_gpt_sovits_client_factory(config_module: Any) -> Callable[[str], GptSovitsTTSClient]:
    timeout_seconds = float(getattr(config_module, "GPT_SOVITS_TTS_TIMEOUT_SECONDS", 45.0) or 45.0)
    text_lang = str(getattr(config_module, "GPT_SOVITS_TEXT_LANG", "zh") or "zh")
    media_type = str(getattr(config_module, "GPT_SOVITS_MEDIA_TYPE", "wav") or "wav")
    streaming_mode = bool(getattr(config_module, "GPT_SOVITS_STREAMING_MODE", False))
    parallel_infer = getattr(config_module, "GPT_SOVITS_PARALLEL_INFER", None)
    split_bucket = getattr(config_module, "GPT_SOVITS_SPLIT_BUCKET", None)
    batch_size = getattr(config_module, "GPT_SOVITS_BATCH_SIZE", None)
    speed_factor = getattr(config_module, "GPT_SOVITS_SPEED_FACTOR", None)
    fragment_interval = getattr(config_module, "GPT_SOVITS_FRAGMENT_INTERVAL", None)
    text_split_method = str(getattr(config_module, "GPT_SOVITS_TEXT_SPLIT_METHOD", "") or "")

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


def _resolve_character_voice_preference(engine: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    pack_id = str(
        payload.get("character_pack_id")
        or payload.get("characterPackId")
        or payload.get("character_pack")
        or ""
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
        payload.get("real_user_id")
        or payload.get("profileUserId")
        or payload.get("profile_user_id")
        or ""
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


def _resolve_tts_emotion(payload: Mapping[str, Any]) -> str:
    return _safe_voice_profile_id(
        payload.get("emotion")
        or payload.get("currentEmotion")
        or payload.get("current_emotion")
        or payload.get("finalEmotion")
        or payload.get("final_emotion")
    )


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


def _coerce_tts_capability_result(result: CapabilityResult, *, default_media_type: str) -> tuple[bytes, str]:
    if result.is_error:
        raise RuntimeError(result.reason or result.status or "tts_capability_error")
    content = result.content if isinstance(result.content, Mapping) else {}
    audio = bytes(content.get("audio") or b"")
    media_type = str(content.get("mediaType") or content.get("media_type") or default_media_type)
    if not audio:
        raise RuntimeError("tts returned empty audio")
    return audio, _safe_media_type(media_type, default=default_media_type)


def _coerce_synthesized_audio(result: Any, *, default_media_type: str) -> tuple[bytes, str]:
    if isinstance(result, bytes):
        audio = result
        media_type = default_media_type
    elif isinstance(result, SynthesizedAudio):
        audio = result.audio
        media_type = result.media_type or default_media_type
    else:
        audio = bytes(getattr(result, "audio", b"") or b"")
        media_type = str(getattr(result, "media_type", "") or default_media_type)
    if not audio:
        raise RuntimeError("tts returned empty audio")
    return audio, _safe_media_type(media_type, default=default_media_type)


def _safe_media_type(value: Any, *, default: str) -> str:
    text = str(value or "").split(";", 1)[0].strip().lower()
    if "/" not in text or any(ch in text for ch in "\r\n"):
        return default
    return text[:80]


def _tts_response_headers(resolution: Mapping[str, Any]) -> dict[str, str]:
    requested = str(resolution.get("requestedProviderId") or EDGE_TTS_PROVIDER_ID)
    active = str(resolution.get("activeProviderId") or "")
    return {
        "Cache-Control": "no-store",
        "X-Akane-Contract": DESKTOP_PET_CONTRACT_VERSION,
        "X-Akane-TTS-Requested-Provider": requested,
        "X-Akane-TTS-Provider": active,
        "X-Akane-TTS-Status": str(resolution.get("status") or ""),
        "X-Akane-TTS-Fallback": str(resolution.get("fallbackProviderId") or ""),
        "X-Akane-TTS-Reason": _safe_tts_reason(resolution.get("reason")),
    }


def _safe_tts_reason(value: Any) -> str:
    reason = str(value or "").strip()
    if not reason:
        return ""
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in reason)
    return safe[:120]


def _resolve_provider_config_base_dir(
    *,
    capability_config_base_dir: str | Path | None,
    config_module: Any = None,
) -> Path | None:
    if capability_config_base_dir is not None:
        return Path(capability_config_base_dir)
    data_dir = getattr(config_module, "DATA_DIR", None)
    if data_dir:
        return Path(data_dir)
    return None


def run_asr_transcription(
    *,
    engine: Any,
    config_module: Any,
    audio_bytes: bytes,
    filename: str,
    language: str,
    content_type: str,
) -> dict[str, object]:
    if importlib.util.find_spec("faster_whisper") is None:
        return {
            "ok": False,
            "error": "faster_whisper_not_found",
            "message": "本机还没有安装 faster-whisper，暂时不能语音识别。",
            "_status_code": 503,
        }

    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        return {
            "ok": False,
            "error": "ffmpeg_not_found",
            "message": "本机没有找到 ffmpeg，暂时不能处理录音。",
            "_status_code": 503,
        }

    service = engine._get_generated_file_service()
    if service is None:
        return {
            "ok": False,
            "error": "asr_service_unavailable",
            "message": "语音识别服务暂时不可用。",
            "_status_code": 503,
        }

    suffix = safe_audio_suffix(filename, content_type)
    with tempfile.TemporaryDirectory(prefix="akane_asr_") as tmp:
        work_dir = Path(tmp)
        source_path = work_dir / f"input{suffix}"
        prepared_path = work_dir / "prepared.wav"
        source_path.write_bytes(audio_bytes)

        prepared = service._prepare_transcription_input(
            ffmpeg_path=ffmpeg_path,
            source_path=source_path,
            prepared_path=prepared_path,
        )
        if not prepared.get("ok"):
            return {
                "ok": False,
                "error": "audio_prepare_failed",
                "message": f"录音预处理失败：{str(prepared.get('error') or '')[:160]}",
            }

        model_size = service._normalize_whisper_model_size(
            getattr(config_module, "ASR_WHISPER_MODEL_SIZE", getattr(config_module, "WHISPER_MODEL_SIZE", "small"))
        )
        device = service._normalize_whisper_device(
            getattr(config_module, "ASR_WHISPER_DEVICE", getattr(config_module, "WHISPER_DEVICE", "auto"))
        )
        compute_type = service._normalize_whisper_compute_type(
            getattr(config_module, "ASR_WHISPER_COMPUTE_TYPE", getattr(config_module, "WHISPER_COMPUTE_TYPE", "auto"))
        )
        normalized_language = service._normalize_transcript_language(
            language or getattr(config_module, "ASR_LANGUAGE", "zh")
        )
        model = service._load_faster_whisper_model(
            model_size=model_size,
            device=device,
            compute_type=compute_type,
        )
        transcript = service._transcribe_prepared_audio(
            model=model,
            audio_path=prepared_path,
            source={
                "source_type": "desktop_pet_voice",
                "source_id": "desktop_pet_voice",
                "handle": "voice_input",
                "title": filename,
                "absolute_path": source_path,
                "input_ext": suffix.lstrip("."),
            },
            source_index=1,
            language=normalized_language,
            vad_filter=bool(getattr(config_module, "ASR_VAD_FILTER", True)),
        )

    if transcript.get("status") != "ready":
        return {
            "ok": False,
            "error": "transcribe_failed",
            "message": str(transcript.get("error") or "语音识别失败")[:160],
        }

    text = " ".join(str(transcript.get("text") or "").split()).strip()
    if not text:
        return {"ok": False, "error": "no_speech", "message": "没听清，可以再说一次。"}

    return {
        "ok": True,
        "text": text,
        "language": transcript.get("language") or normalized_language,
        "duration_seconds": transcript.get("duration_seconds"),
    }


def safe_audio_suffix(filename: str, content_type: str) -> str:
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix in {".webm", ".ogg", ".oga", ".mp3", ".wav", ".m4a", ".mp4", ".aac", ".opus"}:
        return suffix
    mime = str(content_type or "").lower()
    if "ogg" in mime or "opus" in mime:
        return ".ogg"
    if "mp4" in mime or "m4a" in mime:
        return ".m4a"
    if "wav" in mime:
        return ".wav"
    return ".webm"
