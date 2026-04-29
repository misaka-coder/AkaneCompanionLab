from __future__ import annotations

import asyncio
import importlib.util
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ..desktop_pet_contract import DESKTOP_PET_CONTRACT_VERSION, build_desktop_pet_error_payload


LogEvent = Callable[..., None]


def build_voice_router(
    *,
    engine: Any,
    config_module: Any,
    tts_client: Any,
    runtime_metrics: Any,
    log_event: LogEvent,
) -> APIRouter:
    router = APIRouter()

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

        try:
            audio = await tts_client.synthesize(text)
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
                headers={"Cache-Control": "no-store"},
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
                headers={"Cache-Control": "no-store"},
            )

        duration_ms = (time.perf_counter() - started_at) * 1000
        runtime_metrics.observe_request("tts", duration_ms=duration_ms, ok=True)
        log_event("tts_complete", duration_ms=round(duration_ms, 1), text_length=len(text))
        return Response(
            content=audio,
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "no-store",
                "X-Akane-Contract": DESKTOP_PET_CONTRACT_VERSION,
            },
        )

    return router


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
