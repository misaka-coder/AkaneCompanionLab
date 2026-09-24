from __future__ import annotations

import math
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests


_AUDIO_SUFFIXES = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".webm"}
_MEDIA_SUFFIXES = _AUDIO_SUFFIXES | {".avi", ".mkv", ".mov", ".mp4"}
_MAX_ERROR_TEXT = 240


class LocalMediaExecutorError(RuntimeError):
    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = str(reason or "local_media_executor_failed")
        self.public_message = str(message or "本地媒体能力暂时不可用。").strip()


class LocalMediaExecutorClient:
    """Loopback-only client for the PC-side media capability host.

    The cloud process may reach this loopback address through an SSH reverse
    tunnel. Audio bytes cross that tunnel; local absolute paths never do.
    """

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 1800.0,
        session: requests.Session | None = None,
    ) -> None:
        normalized = str(base_url).strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
        ):
            raise ValueError("local media executor endpoint must use credential-free loopback HTTP")
        if not math.isfinite(float(timeout_seconds)) or float(timeout_seconds) <= 0:
            raise ValueError("invalid_remote_timeout")
        self.base_url = normalized
        self.timeout_seconds = min(7200, float(timeout_seconds))
        self.session = session or requests.Session()
        self._health_lock = threading.RLock()
        self._health_cache: tuple[float, dict[str, Any]] | None = None

    def health(self, *, force: bool = False, timeout_seconds: float = 2.0) -> dict[str, Any]:
        now = time.time()
        with self._health_lock:
            if not force and self._health_cache is not None and now - self._health_cache[0] < 2.0:
                return dict(self._health_cache[1])
        try:
            response = self.session.get(
                f"{self.base_url}/health",
                timeout=max(0.25, min(float(timeout_seconds or 2.0), 10.0)),
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("health payload must be an object")
        except Exception:
            payload = {
                "ok": False,
                "status": "unavailable",
                "asr": {"ready": False, "reason": "local_media_executor_unreachable"},
                "separation": {"ready": False, "reason": "local_media_executor_unreachable"},
                "rvc": {"ready": False, "reason": "local_media_executor_unreachable"},
            }
        with self._health_lock:
            self._health_cache = (now, dict(payload))
        return payload

    def capability_status(self) -> dict[str, Any]:
        health = self.health()
        asr = health.get("asr") if isinstance(health.get("asr"), dict) else {}
        separation = health.get("separation") if isinstance(health.get("separation"), dict) else {}
        rvc = health.get("rvc") if isinstance(health.get("rvc"), dict) else {}
        return {
            "status": "ready" if bool(health.get("ok")) else "unavailable",
            "asr": {
                "ready": bool(asr.get("ready")),
                "reason": str(asr.get("reason") or ""),
                "model": str(asr.get("model") or ""),
                "device": str(asr.get("device") or ""),
                "computeType": str(asr.get("compute_type") or ""),
            },
            "separation": {
                "ready": bool(separation.get("ready")),
                "reason": str(separation.get("reason") or ""),
                "provider": str(separation.get("provider") or ""),
                "model": str(separation.get("model") or ""),
            },
            "rvc": {
                "ready": bool(rvc.get("ready")),
                "reason": str(rvc.get("reason") or ""),
                "modelCount": max(0, int(rvc.get("model_count") or 0)),
            },
        }

    def transcribe_file(
        self,
        path: Path | str,
        *,
        language: str = "zh",
        model: str = "",
        vad_filter: bool = True,
    ) -> dict[str, Any]:
        source = Path(path)
        if not source.exists() or not source.is_file():
            raise LocalMediaExecutorError("audio_not_found", "没有找到要转写的音频。")
        return self.transcribe_bytes(
            source.read_bytes(),
            filename=source.name,
            content_type=_audio_content_type(source.suffix),
            language=language,
            model=model,
            vad_filter=vad_filter,
        )

    def transcribe_bytes(
        self,
        audio: bytes,
        *,
        filename: str,
        content_type: str = "application/octet-stream",
        language: str = "zh",
        model: str = "",
        vad_filter: bool = True,
    ) -> dict[str, Any]:
        if not audio:
            raise LocalMediaExecutorError("empty_audio", "音频内容为空。")
        try:
            response = self.session.post(
                f"{self.base_url}/v1/audio/transcriptions",
                files={"file": (Path(filename or "audio.wav").name, audio, content_type)},
                data={
                    "model": str(model or ""),
                    "language": str(language or ""),
                    "response_format": "verbose_json",
                    "vad_filter": "true" if vad_filter else "false",
                },
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            raise LocalMediaExecutorError(
                "local_asr_unreachable",
                "本地转写服务暂时无法连接。",
            ) from exc
        if not response.ok:
            raise LocalMediaExecutorError(
                _response_reason(response, "local_asr_failed"),
                _response_message(response, "本地转写没有成功。"),
            )
        try:
            payload = response.json()
        except Exception as exc:
            raise LocalMediaExecutorError("local_asr_response_invalid", "本地转写返回了无法识别的结果。") from exc
        if not isinstance(payload, dict):
            raise LocalMediaExecutorError("local_asr_response_invalid", "本地转写返回了无法识别的结果。")
        text = " ".join(str(payload.get("text") or "").split()).strip()
        segments = _normalize_segments(payload.get("segments"))
        if not text and segments:
            text = "\n".join(str(item.get("text") or "") for item in segments).strip()
        if not text:
            raise LocalMediaExecutorError("no_speech", "没有从音频中识别到清晰语音。")
        return {
            "ok": True,
            "text": text,
            "language": str(payload.get("language") or language or ""),
            "duration_seconds": _safe_float(payload.get("duration")),
            "segments": segments,
            "provider": "local_media_executor",
        }


def _normalize_segments(value: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, item in enumerate(value if isinstance(value, list) else [], start=1):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        start = _safe_float(item.get("start")) or 0.0
        end = _safe_float(item.get("end"))
        output.append(
            {
                "index": index,
                "start": round(start, 3),
                "end": round(max(start, end if end is not None else start), 3),
                "text": text,
                "avg_logprob": _safe_float(item.get("avg_logprob")),
                "no_speech_prob": _safe_float(item.get("no_speech_prob")),
            }
        )
    return output


def _response_reason(response: requests.Response, default: str) -> str:
    try:
        payload = response.json()
    except Exception:
        return default
    if not isinstance(payload, dict):
        return default
    detail = payload.get("detail")
    if isinstance(detail, dict):
        return str(detail.get("reason") or detail.get("error") or default)[:80]
    return str(payload.get("reason") or payload.get("error") or default)[:80]


def _response_message(response: requests.Response, default: str) -> str:
    try:
        payload = response.json()
    except Exception:
        return default
    if not isinstance(payload, dict):
        return default
    detail = payload.get("detail")
    if isinstance(detail, dict):
        value = detail.get("message") or detail.get("reason")
    else:
        value = payload.get("message") or detail
    return str(value or default).strip()[:_MAX_ERROR_TEXT]


def _audio_content_type(suffix: str) -> str:
    return {
        ".aac": "audio/aac",
        ".flac": "audio/flac",
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg",
        ".opus": "audio/opus",
        ".wav": "audio/wav",
        ".webm": "audio/webm",
        ".avi": "video/x-msvideo",
        ".mkv": "video/x-matroska",
        ".mov": "video/quicktime",
        ".mp4": "video/mp4",
    }.get(str(suffix or "").lower(), "application/octet-stream")


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def safe_uploaded_suffix(filename: str) -> str:
    suffix = Path(str(filename or "")).suffix.lower()
    return suffix if suffix in _MEDIA_SUFFIXES else ".bin"
