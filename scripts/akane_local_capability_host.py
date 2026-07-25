from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from companion_v01.cover_song import CoverSongError, RvcWebUiProvider
from companion_v01.generated_files_media import (
    load_faster_whisper_model,
    normalize_transcript_language,
    normalize_whisper_compute_type,
    normalize_whisper_device,
    normalize_whisper_model_size,
    prepare_transcription_input,
    separate_audio_with_demucs,
)
from companion_v01.local_media_executor import safe_model_fingerprint, safe_uploaded_suffix


MAX_UPLOAD_BYTES = 256 * 1024 * 1024
DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 9879


class LocalDemucsRuntime:
    def __init__(
        self,
        *,
        python_path: Path | None,
        package_root: Path | None,
    ) -> None:
        self.python_path = Path(python_path).resolve() if python_path else None
        self.package_root = Path(package_root).resolve() if package_root else None
        self.worker_path = (PROJECT_ROOT / "scripts" / "akane_demucs_worker.py").resolve()
        self._external_status = self._probe_external()
        self._in_process_ready = importlib.util.find_spec("demucs") is not None

    @property
    def ready(self) -> bool:
        return bool(self._external_status.get("ok")) or self._in_process_ready

    def public_status(self) -> dict[str, Any]:
        if bool(self._external_status.get("ok")):
            cuda_ready = bool(self._external_status.get("cuda_available"))
            return {
                "ready": True,
                "reason": "",
                "provider": "demucs",
                "model": "htdemucs",
                "executor": "isolated_cuda" if cuda_ready else "isolated_cpu",
                "device": "cuda" if cuda_ready else "cpu",
                "device_name": str(self._external_status.get("device") or ""),
            }
        if self._in_process_ready:
            return {
                "ready": True,
                "reason": "",
                "provider": "demucs",
                "model": "htdemucs",
                "executor": "in_process",
                "device": "cuda" if _in_process_cuda_available() else "cpu",
                "device_name": "",
            }
        return {
            "ready": False,
            "reason": str(self._external_status.get("reason") or "demucs_not_found"),
            "provider": "",
            "model": "",
            "executor": "",
            "device": "",
            "device_name": "",
        }

    def separate(
        self,
        *,
        source_path: Path,
        output_root: Path,
        model: str,
        timeout_seconds: float = 1800.0,
    ) -> dict[str, Any]:
        if bool(self._external_status.get("ok")):
            assert self.python_path is not None
            assert self.package_root is not None
            started = time.perf_counter()
            completed = subprocess.run(
                [
                    str(self.python_path),
                    str(self.worker_path),
                    "--package-root",
                    str(self.package_root),
                    "--source",
                    str(source_path),
                    "--output-root",
                    str(output_root),
                    "--model",
                    model,
                ],
                capture_output=True,
                text=True,
                timeout=max(30.0, min(3600.0, float(timeout_seconds))),
                check=False,
            )
            payload = _last_json_object(completed.stdout)
            if completed.returncode != 0 or not bool(payload.get("ok")):
                raise RuntimeError(str(payload.get("reason") or "demucs_cuda_worker_failed"))
            vocals = output_root / "vocals.wav"
            instrumental = output_root / "instrumental.wav"
            if not vocals.is_file() or not instrumental.is_file():
                raise RuntimeError("demucs_outputs_missing")
            return {
                "vocals": vocals,
                "instrumental": instrumental,
                "device_used": str(payload.get("device") or ""),
                "seconds": round(time.perf_counter() - started, 3),
            }
        if not self._in_process_ready:
            raise RuntimeError("demucs_not_found")
        return separate_audio_with_demucs(
            source_path=source_path,
            output_root=output_root,
            model_name=model,
        )

    def _probe_external(self) -> dict[str, Any]:
        if (
            self.python_path is None
            or self.package_root is None
            or not self.python_path.is_file()
            or not self.package_root.is_dir()
            or not self.worker_path.is_file()
        ):
            return {"ok": False, "reason": "demucs_cuda_runtime_not_configured"}
        try:
            completed = subprocess.run(
                [
                    str(self.python_path),
                    str(self.worker_path),
                    "--package-root",
                    str(self.package_root),
                    "--probe",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except Exception:
            return {"ok": False, "reason": "demucs_cuda_probe_failed"}
        payload = _last_json_object(completed.stdout)
        if completed.returncode != 0 or not bool(payload.get("ok")):
            return {"ok": False, "reason": str(payload.get("reason") or "demucs_cuda_probe_failed")}
        return payload


class LocalAsrRuntime:
    def __init__(self, *, ffmpeg_path: Path, cache_dir: Path | None, default_model: str) -> None:
        self.ffmpeg_path = Path(ffmpeg_path)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.default_model = normalize_whisper_model_size(default_model)
        self.device = normalize_whisper_device(os.environ.get("AKANE_LOCAL_ASR_DEVICE", "cpu"))
        self.compute_type = normalize_whisper_compute_type(
            os.environ.get("AKANE_LOCAL_ASR_COMPUTE_TYPE", "int8")
        )
        self._whisper_model_cache: dict[tuple[str, str, str, str], Any] = {}
        self._lock = threading.RLock()

    @property
    def ready(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
        except Exception:
            return False
        return self.ffmpeg_path.exists() and self.ffmpeg_path.is_file()

    def transcribe(
        self,
        *,
        source_path: Path,
        model_size: str,
        language: str,
        vad_filter: bool,
    ) -> dict[str, Any]:
        if not self.ready:
            raise RuntimeError("local_asr_dependencies_missing")
        normalized_model = normalize_whisper_model_size(model_size or self.default_model)
        normalized_language = normalize_transcript_language(language)
        with tempfile.TemporaryDirectory(prefix="akane_local_asr_") as tmp:
            prepared_path = Path(tmp) / "prepared.wav"
            prepared = prepare_transcription_input(
                self,
                ffmpeg_path=str(self.ffmpeg_path),
                source_path=source_path,
                prepared_path=prepared_path,
            )
            if not prepared.get("ok"):
                raise RuntimeError("audio_prepare_failed")
            with self._lock:
                model = load_faster_whisper_model(
                    self,
                    model_size=normalized_model,
                    device=self.device,
                    compute_type=self.compute_type,
                    download_root=str(self.cache_dir) if self.cache_dir else None,
                )
                kwargs: dict[str, Any] = {"beam_size": 5, "vad_filter": bool(vad_filter)}
                if normalized_language:
                    kwargs["language"] = normalized_language
                segments_iter, info = model.transcribe(str(prepared_path), **kwargs)
                raw_segments = list(segments_iter)
        segments = []
        for index, segment in enumerate(raw_segments, start=1):
            text = str(getattr(segment, "text", "") or "").strip()
            if not text:
                continue
            start = float(getattr(segment, "start", 0.0) or 0.0)
            end = max(start, float(getattr(segment, "end", start) or start))
            segments.append(
                {
                    "id": index - 1,
                    "index": index,
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "text": text,
                    "avg_logprob": _optional_float(getattr(segment, "avg_logprob", None)),
                    "no_speech_prob": _optional_float(getattr(segment, "no_speech_prob", None)),
                }
            )
        return {
            "text": " ".join(item["text"] for item in segments).strip(),
            "language": str(getattr(info, "language", normalized_language) or normalized_language),
            "duration": _optional_float(getattr(info, "duration", None)),
            "segments": segments,
            "model": normalized_model,
        }


def create_app(
    *,
    ffmpeg_path: Path,
    whisper_cache_dir: Path | None,
    whisper_model: str,
    rvc_base_url: str,
    rvc_root_dir: Path | None,
    separation_model: str,
    demucs_python_path: Path | None = None,
    demucs_package_root: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="Akane Local Capability Host", docs_url=None, redoc_url=None)
    asr_runtime = LocalAsrRuntime(
        ffmpeg_path=ffmpeg_path,
        cache_dir=whisper_cache_dir,
        default_model=whisper_model,
    )
    rvc_provider = RvcWebUiProvider(
        base_url=rvc_base_url,
        root_dir=rvc_root_dir or "",
        timeout_seconds=float(os.environ.get("AKANE_LOCAL_RVC_TIMEOUT_SECONDS", "1800") or 1800),
        separation_model=separation_model,
    )
    demucs_runtime = LocalDemucsRuntime(
        python_path=demucs_python_path,
        package_root=demucs_package_root,
    )
    demucs_lock = threading.RLock()

    @app.get("/health")
    def health() -> dict[str, Any]:
        rvc_status = rvc_provider.capability_status()
        rvc_ready = bool(rvc_status.get("enabled"))
        model_count = 0
        if rvc_ready:
            try:
                model_count = len(rvc_provider.list_voice_models())
            except Exception:
                rvc_ready = False
        return {
            "ok": bool(asr_runtime.ready or demucs_runtime.ready or rvc_ready),
            "status": "ready" if asr_runtime.ready and demucs_runtime.ready and rvc_ready else "degraded",
            "service": "akane_local_media_capabilities",
            "protocol_version": 1,
            "asr": {
                "ready": asr_runtime.ready,
                "reason": "" if asr_runtime.ready else "local_asr_dependencies_missing",
                "model": asr_runtime.default_model,
                "device": asr_runtime.device,
                "compute_type": asr_runtime.compute_type,
            },
            "separation": demucs_runtime.public_status(),
            "rvc": {
                "ready": rvc_ready,
                "reason": "" if rvc_ready else str(rvc_status.get("reason") or "local_rvc_unavailable"),
                "model_count": model_count,
            },
        }

    @app.post("/v1/audio/separate")
    async def separate_audio(
        file: UploadFile = File(...),
        model: str = Form("htdemucs"),
        output_format: str = Form("wav"),
    ) -> Response:
        if not demucs_runtime.ready:
            raise HTTPException(
                status_code=503,
                detail={"reason": "demucs_not_found", "message": "本地高质量分轨环境暂时不可用。"},
            )
        normalized_model = str(model or "htdemucs").strip()
        if normalized_model not in {"htdemucs", "htdemucs_ft"}:
            raise HTTPException(
                status_code=400,
                detail={"reason": "demucs_model_not_allowed", "message": "请求的分轨模型不在允许范围内。"},
            )
        normalized_format = str(output_format or "wav").strip().lower().lstrip(".")
        if normalized_format not in {"wav", "flac", "mp3"}:
            raise HTTPException(
                status_code=400,
                detail={
                    "reason": "demucs_output_format_not_allowed",
                    "message": "请求的分轨输出格式不受支持。",
                },
            )
        source_path = await _store_upload(file)
        try:
            with demucs_lock:
                with tempfile.TemporaryDirectory(prefix="akane_local_demucs_") as tmp:
                    separation_started = time.perf_counter()
                    stems = demucs_runtime.separate(
                        source_path=source_path,
                        output_root=Path(tmp) / "stems",
                        model=normalized_model,
                    )
                    separation_seconds = time.perf_counter() - separation_started
                    vocals = stems.get("vocals")
                    instrumental = stems.get("instrumental")
                    if not vocals or not instrumental:
                        raise RuntimeError("demucs_outputs_missing")
                    encode_started = time.perf_counter()
                    transfer_vocals = _render_stem_for_transfer(
                        source_path=vocals,
                        output_dir=Path(tmp) / "transfer",
                        stem_name="vocals",
                        output_format=normalized_format,
                        ffmpeg_path=ffmpeg_path,
                    )
                    transfer_instrumental = _render_stem_for_transfer(
                        source_path=instrumental,
                        output_dir=Path(tmp) / "transfer",
                        stem_name="instrumental",
                        output_format=normalized_format,
                        ffmpeg_path=ffmpeg_path,
                    )
                    archive_buffer = io.BytesIO()
                    with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_STORED) as archive:
                        archive.write(transfer_vocals, arcname=transfer_vocals.name)
                        archive.write(transfer_instrumental, arcname=transfer_instrumental.name)
                    timings = {
                        "separation": round(separation_seconds, 3),
                        "device": str(stems.get("device_used") or ""),
                        "encode": round(time.perf_counter() - encode_started, 3),
                        "output_format": normalized_format,
                        "archive_bytes": len(archive_buffer.getvalue()),
                    }
                    return Response(
                        content=archive_buffer.getvalue(),
                        media_type="application/zip",
                        headers={
                            "X-Akane-Media-Timings": json.dumps(timings, ensure_ascii=True),
                        },
                    )
        except HTTPException:
            raise
        except Exception as exc:
            raise _http_error("local_audio_separation_failed", "本地高质量分轨失败。", exc) from exc
        finally:
            _unlink_quietly(source_path)

    @app.post("/v1/audio/transcriptions")
    async def transcribe_audio(
        file: UploadFile = File(...),
        model: str = Form(""),
        language: str = Form("zh"),
        response_format: str = Form("verbose_json"),
        vad_filter: str = Form("true"),
    ) -> dict[str, Any]:
        del response_format
        source_path = await _store_upload(file)
        try:
            return asr_runtime.transcribe(
                source_path=source_path,
                model_size=model,
                language=language,
                vad_filter=str(vad_filter).strip().lower() not in {"0", "false", "off", "no"},
            )
        except Exception as exc:
            raise _http_error("local_asr_failed", "本地音频转写失败。", exc) from exc
        finally:
            _unlink_quietly(source_path)

    @app.get("/v1/rvc/models")
    def list_rvc_models(force: bool = False) -> dict[str, Any]:
        try:
            names = rvc_provider.list_voice_models(force=force)
        except Exception as exc:
            raise _http_error("local_rvc_unavailable", "本地 RVC 服务暂时不可用。", exc) from exc
        model_cards = []
        for name in names:
            model_path = (rvc_root_dir / "assets" / "weights" / Path(name).name) if rvc_root_dir else Path(name)
            indices = _matching_indices(rvc_root_dir, name)
            model_cards.append(safe_model_fingerprint(model_path, indices=indices))
        return {"ok": True, "models": model_cards}

    @app.post("/v1/rvc/separate")
    async def separate_rvc(
        file: UploadFile = File(...),
        requested_separation_model: str = Form("", alias="separation_model"),
    ) -> Response:
        source_path = await _store_upload(file)
        original_model = rvc_provider.separation_model
        try:
            if requested_separation_model.strip():
                rvc_provider.separation_model = requested_separation_model.strip()
            with tempfile.TemporaryDirectory(prefix="akane_local_rvc_separate_") as tmp:
                vocals, instrumental = rvc_provider.separate_vocals(
                    source_path=source_path,
                    work_dir=Path(tmp),
                )
                archive_buffer = io.BytesIO()
                with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_STORED) as archive:
                    archive.writestr("vocals.wav", vocals.read_bytes())
                    archive.writestr("instrumental.wav", instrumental.read_bytes())
                return Response(content=archive_buffer.getvalue(), media_type="application/zip")
        except Exception as exc:
            raise _http_error("local_rvc_separation_failed", "本地人声分离失败。", exc) from exc
        finally:
            rvc_provider.separation_model = original_model
            _unlink_quietly(source_path)

    @app.post("/v1/rvc/convert")
    async def convert_rvc(
        file: UploadFile = File(...),
        model_name: str = Form(...),
        pitch_shift: int = Form(0),
        index_rate: float = Form(0.6),
        filter_radius: int = Form(3),
        rms_mix_rate: float = Form(0.25),
        protect: float = Form(0.33),
    ) -> Response:
        source_path = await _store_upload(file)
        try:
            with tempfile.TemporaryDirectory(prefix="akane_local_rvc_convert_") as tmp:
                output_path = Path(tmp) / "converted.wav"
                with rvc_provider.exclusive():
                    result = rvc_provider.convert_voice(
                        source_path=source_path,
                        output_path=output_path,
                        model_name=model_name,
                        pitch_shift=max(-24, min(24, int(pitch_shift))),
                        index_rate=max(0.0, min(1.0, float(index_rate))),
                        filter_radius=max(0, min(7, int(filter_radius))),
                        rms_mix_rate=max(0.0, min(1.0, float(rms_mix_rate))),
                        protect=max(0.0, min(0.5, float(protect))),
                    )
                if not output_path.exists() or output_path.stat().st_size <= 0:
                    raise RuntimeError("rvc_output_missing")
                headers = {
                    "X-Akane-RVC-Timings": json.dumps(result.get("timings") or {}, ensure_ascii=True),
                }
                return Response(content=output_path.read_bytes(), media_type="audio/wav", headers=headers)
        except Exception as exc:
            raise _http_error("local_rvc_conversion_failed", "本地 RVC 音色转换失败。", exc) from exc
        finally:
            _unlink_quietly(source_path)

    return app


async def _store_upload(upload: UploadFile) -> Path:
    suffix = safe_uploaded_suffix(upload.filename or "")
    fd, raw_path = tempfile.mkstemp(prefix="akane_local_media_", suffix=suffix)
    os.close(fd)
    path = Path(raw_path)
    total = 0
    try:
        with path.open("wb") as stream:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail={"reason": "audio_too_large", "message": "音频超过本地能力处理上限。"},
                    )
                stream.write(chunk)
    except Exception:
        _unlink_quietly(path)
        raise
    if total < 512:
        _unlink_quietly(path)
        raise HTTPException(
            status_code=400,
            detail={"reason": "audio_too_short", "message": "音频内容过短。"},
        )
    return path


def _matching_indices(root_dir: Path | None, model_name: str) -> list[Path]:
    if root_dir is None:
        return []
    key = "".join(ch for ch in Path(model_name).stem.lower() if ch.isalnum())
    candidates = []
    for base in (root_dir / "assets" / "indices", root_dir / "logs"):
        if not base.exists():
            continue
        for path in base.rglob("*.index"):
            candidate_key = "".join(ch for ch in path.stem.lower() if ch.isalnum())
            if key and key.split("test")[0] in candidate_key:
                candidates.append(path)
    return sorted(candidates, key=lambda item: item.name.lower())[:12]


def _http_error(reason: str, message: str, exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, CoverSongError):
        reason = exc.reason
        message = exc.public_message
    return HTTPException(
        status_code=503,
        detail={
            "reason": str(reason or "local_media_failed")[:80],
            "message": str(message or "本地媒体处理失败。")[:240],
        },
    )


def _optional_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return round(number, 6)


def _render_stem_for_transfer(
    *,
    source_path: Path,
    output_dir: Path,
    stem_name: str,
    output_format: str,
    ffmpeg_path: Path,
) -> Path:
    normalized_format = str(output_format or "wav").strip().lower().lstrip(".")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{stem_name}.{normalized_format}"
    if normalized_format == "wav":
        shutil.copy2(source_path, output_path)
        return output_path
    codec_args = (
        ["-c:a", "libmp3lame", "-b:a", "320k"]
        if normalized_format == "mp3"
        else ["-c:a", "flac"]
    )
    completed = subprocess.run(
        [
            str(ffmpeg_path),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source_path),
            *codec_args,
            str(output_path),
        ],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if completed.returncode != 0 or not output_path.is_file() or output_path.stat().st_size <= 0:
        raise RuntimeError("demucs_output_encode_failed")
    return output_path


def _last_json_object(value: str) -> dict[str, Any]:
    for line in reversed(str(value or "").splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _in_process_cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _resolve_existing_file(raw: str, *, fallback: Path | None = None) -> Path:
    candidate = Path(str(raw or "").strip()) if str(raw or "").strip() else fallback
    if candidate is None or not candidate.exists() or not candidate.is_file():
        raise RuntimeError("required_local_executable_missing")
    return candidate.resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description="Akane loopback-only local media capability host")
    parser.add_argument("--host", default=DEFAULT_BIND_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("local_capability_host_must_bind_loopback")
    rvc_root_raw = os.environ.get("AKANE_LOCAL_RVC_ROOT", "").strip()
    rvc_root = Path(rvc_root_raw).resolve() if rvc_root_raw else None
    fallback_ffmpeg = (rvc_root / "ffmpeg.exe") if rvc_root else None
    ffmpeg_path = _resolve_existing_file(
        os.environ.get("AKANE_LOCAL_FFMPEG_PATH", "") or str(shutil.which("ffmpeg") or ""),
        fallback=fallback_ffmpeg,
    )
    cache_raw = os.environ.get("AKANE_LOCAL_WHISPER_CACHE_DIR", "").strip()
    demucs_python_raw = os.environ.get("AKANE_LOCAL_DEMUCS_PYTHON", "").strip()
    demucs_package_raw = os.environ.get("AKANE_LOCAL_DEMUCS_PACKAGE_ROOT", "").strip()
    app = create_app(
        ffmpeg_path=ffmpeg_path,
        whisper_cache_dir=Path(cache_raw).resolve() if cache_raw else None,
        whisper_model=os.environ.get("AKANE_LOCAL_WHISPER_MODEL", "small"),
        rvc_base_url=os.environ.get("AKANE_LOCAL_RVC_BASE_URL", "http://127.0.0.1:7899"),
        rvc_root_dir=rvc_root,
        separation_model=os.environ.get("AKANE_LOCAL_RVC_SEPARATION_MODEL", "HP5_only_main_vocal"),
        demucs_python_path=Path(demucs_python_raw).resolve() if demucs_python_raw else None,
        demucs_package_root=Path(demucs_package_raw).resolve() if demucs_package_raw else None,
    )
    import uvicorn

    uvicorn.run(app, host=args.host, port=max(1, min(65535, int(args.port))), log_level="info")


if __name__ == "__main__":
    main()
