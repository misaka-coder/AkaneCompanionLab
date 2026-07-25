from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import threading
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from .cover_song import CoverSongError


_AUDIO_SUFFIXES = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".webm"}
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
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
        normalized = str(base_url or "").strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme != "http" or parsed.hostname not in _LOOPBACK_HOSTS:
            raise ValueError("local media executor endpoint must use loopback HTTP")
        self.base_url = normalized
        self.timeout_seconds = max(5.0, min(7200.0, float(timeout_seconds or 1800.0)))
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

    def list_rvc_models(self, *, force: bool = False) -> list[dict[str, Any]]:
        try:
            response = self.session.get(
                f"{self.base_url}/v1/rvc/models",
                params={"force": "true" if force else "false"},
                timeout=min(20.0, self.timeout_seconds),
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise LocalMediaExecutorError("local_rvc_unreachable", "本地 RVC 服务暂时无法连接。") from exc
        models = payload.get("models") if isinstance(payload, dict) else None
        output: list[dict[str, Any]] = []
        for item in models if isinstance(models, list) else []:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                if name:
                    output.append(
                        {
                            "name": name,
                            "size": max(0, int(item.get("size") or 0)),
                            "mtime_ns": max(0, int(item.get("mtime_ns") or 0)),
                            "indices": list(item.get("indices") or [])[:12],
                        }
                    )
        return output

    def separate_audio_stems(
        self,
        *,
        source_path: Path,
        model: str = "htdemucs",
        output_format: str = "wav",
    ) -> tuple[bytes, bytes]:
        normalized_format = str(output_format or "wav").strip().lower().lstrip(".")
        if normalized_format not in {"wav", "flac", "mp3"}:
            raise LocalMediaExecutorError(
                "local_audio_separation_format_invalid",
                "本地高质量分轨不支持这个输出格式。",
            )
        try:
            with source_path.open("rb") as source_file:
                response = self.session.post(
                    f"{self.base_url}/v1/audio/separate",
                    files={
                        "file": (
                            source_path.name,
                            source_file,
                            _audio_content_type(source_path.suffix),
                        )
                    },
                    data={
                        "model": str(model or "htdemucs"),
                        "output_format": normalized_format,
                    },
                    timeout=self.timeout_seconds,
                )
        except Exception as exc:
            raise LocalMediaExecutorError(
                "local_audio_separation_unreachable",
                "本地高质量分轨服务暂时无法连接。",
            ) from exc
        if not response.ok:
            raise LocalMediaExecutorError(
                _response_reason(response, "local_audio_separation_failed"),
                _response_message(response, "本地高质量分轨没有成功。"),
            )
        return _read_stem_archive(response.content, output_format=normalized_format)

    def separate_rvc_vocals(
        self,
        *,
        source_path: Path,
        separation_model: str,
    ) -> tuple[bytes, bytes]:
        try:
            with source_path.open("rb") as source_file:
                response = self.session.post(
                    f"{self.base_url}/v1/rvc/separate",
                    files={
                        "file": (
                            source_path.name,
                            source_file,
                            _audio_content_type(source_path.suffix),
                        )
                    },
                    data={"separation_model": str(separation_model or "")},
                    timeout=self.timeout_seconds,
                )
        except Exception as exc:
            raise LocalMediaExecutorError("local_rvc_separation_unreachable", "本地人声分离服务暂时无法连接。") from exc
        if not response.ok:
            raise LocalMediaExecutorError(
                _response_reason(response, "local_rvc_separation_failed"),
                _response_message(response, "本地人声分离没有成功。"),
            )
        return _read_stem_archive(
            response.content,
            invalid_reason="local_rvc_separation_response_invalid",
            missing_reason="local_rvc_separation_output_missing",
        )

    def convert_rvc_voice(
        self,
        *,
        source_path: Path,
        model_name: str,
        pitch_shift: int,
        index_rate: float,
        filter_radius: int,
        rms_mix_rate: float,
        protect: float,
    ) -> tuple[bytes, dict[str, Any]]:
        try:
            with source_path.open("rb") as source_file:
                response = self.session.post(
                    f"{self.base_url}/v1/rvc/convert",
                    files={
                        "file": (
                            source_path.name,
                            source_file,
                            _audio_content_type(source_path.suffix),
                        )
                    },
                    data={
                        "model_name": model_name,
                        "pitch_shift": str(int(pitch_shift)),
                        "index_rate": str(float(index_rate)),
                        "filter_radius": str(int(filter_radius)),
                        "rms_mix_rate": str(float(rms_mix_rate)),
                        "protect": str(float(protect)),
                    },
                    timeout=self.timeout_seconds,
                )
        except Exception as exc:
            raise LocalMediaExecutorError("local_rvc_conversion_unreachable", "本地 RVC 转换服务暂时无法连接。") from exc
        if not response.ok:
            raise LocalMediaExecutorError(
                _response_reason(response, "local_rvc_conversion_failed"),
                _response_message(response, "本地 RVC 转换没有成功。"),
            )
        if not response.content:
            raise LocalMediaExecutorError("local_rvc_conversion_output_missing", "本地 RVC 没有返回转换音频。")
        timings: dict[str, Any] = {}
        raw_timings = str(response.headers.get("X-Akane-RVC-Timings") or "").strip()
        if raw_timings:
            try:
                decoded = json.loads(raw_timings)
                if isinstance(decoded, dict):
                    timings = {
                        str(key): round(float(value), 3)
                        for key, value in decoded.items()
                        if isinstance(value, (int, float))
                    }
            except Exception:
                timings = {}
        return bytes(response.content), timings


class LocalRvcExecutorProvider:
    """CoverSongService provider backed by the PC-side media host."""

    provider_id = "local_rvc_executor"
    _locks_guard = threading.RLock()
    _locks: dict[str, threading.RLock] = {}

    def __init__(
        self,
        *,
        client: LocalMediaExecutorClient,
        default_model: str = "",
        separation_model: str = "HP5_only_main_vocal",
    ) -> None:
        self.client = client
        self.timeout_seconds = client.timeout_seconds
        self.default_model = str(default_model or "").strip()
        self.separation_model = str(separation_model or "HP5_only_main_vocal").strip()
        with self._locks_guard:
            self._operation_lock = self._locks.setdefault(client.base_url, threading.RLock())

    def exclusive(self):
        return self._operation_lock

    def capability_status(self) -> dict[str, Any]:
        status = self.client.capability_status().get("rvc") or {}
        if not bool(status.get("ready")):
            return {
                "enabled": False,
                "status": "unavailable",
                "reason": str(status.get("reason") or "local_rvc_unavailable"),
            }
        if int(status.get("modelCount") or 0) <= 0:
            return {"enabled": False, "status": "missing_model", "reason": "rvc_voice_models_missing"}
        return {"enabled": True, "status": "ready", "reason": ""}

    def list_voice_models(
        self,
        *,
        force: bool = False,
        request_timeout_seconds: float | None = None,
    ) -> list[str]:
        del request_timeout_seconds
        return [str(item.get("name") or "") for item in self.client.list_rvc_models(force=force)]

    def resolve_voice_model(self, requested: str, *, default_model: str = "") -> str:
        models = self.list_voice_models()
        raw = str(requested or "").strip()
        if not raw or raw.lower() in {"auto", "default", "默认", "自动"}:
            raw = str(default_model or self.default_model or "").strip() or (models[0] if models else "")
        if not raw:
            raise CoverSongError(
                stage="voice_model",
                reason="voice_model_missing",
                public_message="本机 RVC 暂时没有可用的目标音色模型。",
            )
        lowered = raw.lower()
        exact = [
            item for item in models if item.lower() == lowered or Path(item).stem.lower() == Path(raw).stem.lower()
        ]
        if len(exact) == 1:
            return exact[0]
        normalized = _normalize_model_key(raw)
        fuzzy = [item for item in models if normalized and normalized in _normalize_model_key(item)]
        if len(fuzzy) == 1:
            return fuzzy[0]
        if len(fuzzy) > 1:
            raise CoverSongError(
                stage="voice_model",
                reason="voice_model_ambiguous",
                public_message=f"目标音色名称不够明确，可匹配到：{'、'.join(fuzzy[:6])}。",
            )
        raise CoverSongError(
            stage="voice_model",
            reason="voice_model_not_found",
            public_message=f"没有找到目标 RVC 音色“{raw}”。当前可用音色包括：{'、'.join(models[:8])}。",
        )

    def model_fingerprint(self, model_name: str) -> dict[str, Any]:
        normalized = str(model_name or "").strip().lower()
        for item in self.client.list_rvc_models():
            name = str(item.get("name") or "")
            if name.lower() == normalized:
                return {
                    "model": name,
                    "weight": {
                        "size": max(0, int(item.get("size") or 0)),
                        "mtime_ns": max(0, int(item.get("mtime_ns") or 0)),
                    },
                    "indices": list(item.get("indices") or [])[:12],
                }
        return {"model": str(model_name or ""), "missing": True}

    def separate_vocals(self, *, source_path: Path, work_dir: Path) -> tuple[Path, Path]:
        try:
            vocals, instrumental = self.client.separate_rvc_vocals(
                source_path=source_path,
                separation_model=self.separation_model,
            )
        except LocalMediaExecutorError as exc:
            raise CoverSongError(
                stage="separation",
                reason=exc.reason,
                public_message=exc.public_message,
            ) from exc
        output_dir = work_dir / "local_executor_stems"
        output_dir.mkdir(parents=True, exist_ok=True)
        vocals_path = output_dir / "vocals.wav"
        instrumental_path = output_dir / "instrumental.wav"
        vocals_path.write_bytes(vocals)
        instrumental_path.write_bytes(instrumental)
        return vocals_path, instrumental_path

    def convert_voice(
        self,
        *,
        source_path: Path,
        output_path: Path,
        model_name: str,
        pitch_shift: int,
        index_rate: float,
        filter_radius: int,
        rms_mix_rate: float,
        protect: float,
    ) -> dict[str, Any]:
        try:
            audio, timings = self.client.convert_rvc_voice(
                source_path=source_path,
                model_name=model_name,
                pitch_shift=pitch_shift,
                index_rate=index_rate,
                filter_radius=filter_radius,
                rms_mix_rate=rms_mix_rate,
                protect=protect,
            )
        except LocalMediaExecutorError as exc:
            raise CoverSongError(
                stage="voice_conversion",
                reason=exc.reason,
                public_message=exc.public_message,
            ) from exc
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio)
        return {"index_path": "", "info": "Success", "timings": timings}


def _read_stem_archive(
    payload: bytes,
    *,
    output_format: str = "wav",
    invalid_reason: str = "local_audio_separation_response_invalid",
    missing_reason: str = "local_audio_separation_output_missing",
) -> tuple[bytes, bytes]:
    normalized_format = str(output_format or "wav").strip().lower().lstrip(".")
    try:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
            names = set(archive.namelist())
            vocals_name = (
                f"vocals.{normalized_format}"
                if f"vocals.{normalized_format}" in names
                else "vocals.wav"
            )
            instrumental_name = (
                f"instrumental.{normalized_format}"
                if f"instrumental.{normalized_format}" in names
                else "instrumental.wav"
            )
            vocals = archive.read(vocals_name)
            instrumental = archive.read(instrumental_name)
    except Exception as exc:
        raise LocalMediaExecutorError(
            invalid_reason,
            "本地人声分离返回了不完整的结果。",
        ) from exc
    if not vocals or not instrumental:
        raise LocalMediaExecutorError(
            missing_reason,
            "本地人声分离没有产生完整音轨。",
        )
    return vocals, instrumental


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
    }.get(str(suffix or "").lower(), "application/octet-stream")


def _normalize_model_key(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())


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
    return suffix if suffix in _AUDIO_SUFFIXES else ".bin"


def safe_model_fingerprint(path: Path, *, indices: list[Path] | None = None) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        return {"name": path.name, "missing": True}
    index_cards = []
    for index_path in list(indices or [])[:12]:
        try:
            index_stat = index_path.stat()
        except OSError:
            continue
        index_cards.append(
            {
                "name": index_path.name,
                "size": int(index_stat.st_size),
                "mtime_ns": int(index_stat.st_mtime_ns),
            }
        )
    return {
        "name": path.name,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "sha256_hint": hashlib.sha256(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()[:16],
        "indices": index_cards,
    }
