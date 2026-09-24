from __future__ import annotations

import argparse
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

from scripts.akane_separation_package import local_demucs_class, run_completed

from scripts.akane_cover_package import cover_business

_cover = cover_business()
CoverMedia, CoverSongError, RvcWebUiProvider = _cover.CoverMedia, _cover.CoverSongError, _cover.RvcWebUiProvider
CoverOptions, CoverPipeline, ProviderCalls = _cover.CoverOptions, _cover.CoverPipeline, _cover.ProviderCalls
safe_model_fingerprint = _cover.safe_model_fingerprint
from companion_v01.plugin_subprocess import PluginProcessRunner
from services.asr_business import module as asr_business_module
from companion_v01.local_media_executor import safe_uploaded_suffix


MAX_UPLOAD_BYTES = 256 * 1024 * 1024
DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 9879


class LocalDemucsRuntime:
    """Legacy service shape over the optional package's sole ML implementation."""

    def __init__(self, *, python_path: Path | None, package_root: Path | None, ffmpeg_path: Path | None = None) -> None:
        self.python_path = python_path
        self.package_root = package_root
        self.ffmpeg_path = ffmpeg_path
        self._runtime_class = None
        self._status = {"ok": False, "reason": "separation_package_missing"}
        try:
            self._runtime_class = local_demucs_class()
            self._status = run_completed(self._probe)
            if not self._status.get("ok") and (python_path or package_root):
                external_reason = self._status.get("reason", "demucs_probe_failed")
                self.python_path, self.package_root = None, None
                self._status = run_completed(self._probe)
                self._status["fallback_reason"] = external_reason
        except Exception:
            self._status = {"ok": False, "reason": "separation_package_unavailable"}

    def _runtime(self, model="htdemucs"):
        return self._runtime_class(
            python=self.python_path or sys.executable,
            package_root=self.package_root or "",
            model=model,
        )

    async def _probe(self):
        runtime = self._runtime()
        try:
            return await runtime.probe()
        except RuntimeError as exc:
            return {"ok": False, "reason": str(exc)}
        except Exception:
            return {"ok": False, "reason": "demucs_probe_failed"}
        finally:
            await runtime.aclose()

    @property
    def ready(self) -> bool:
        return bool(self._status.get("ok"))

    def public_status(self) -> dict[str, Any]:
        cuda = bool(self._status.get("cuda_available"))
        return {
            "ready": self.ready,
            "reason": "" if self.ready else self._status.get("reason", "demucs_not_found"),
            "provider": "demucs" if self.ready else "",
            "model": "htdemucs" if self.ready else "",
            "executor": ("isolated_cuda" if cuda else "isolated_cpu") if self.ready else "",
            "device": ("cuda" if cuda else "cpu") if self.ready else "",
            "device_name": "",
            "fallback_reason": self._status.get("fallback_reason", ""),
        }

    def separate(
        self, *, source_path: Path, output_root: Path, model: str, timeout_seconds: float = 1800.0
    ) -> dict[str, Any]:
        if not self.ready:
            raise RuntimeError(self._status.get("reason") or "demucs_not_found")

        async def execute():
            runtime = self._runtime(model)
            started = time.perf_counter()
            try:
                result = await runtime.separate_media(
                    source=source_path,
                    output_root=output_root,
                    model_info=self._status if model == "htdemucs" else None,
                    timeout=max(1.0, min(3600.0, float(timeout_seconds))),
                    ffmpeg=self.ffmpeg_path,
                )
                return {
                    "vocals": output_root / "vocals.wav",
                    "instrumental": output_root / "instrumental.wav",
                    "device_used": result.get("device_used", ""),
                    "seconds": round(time.perf_counter() - started, 3),
                }
            finally:
                await runtime.aclose()

        return run_completed(execute)


class LocalAsrRuntime:
    """Existing service protocol over the package's shared inference authority."""

    def __init__(self, *, ffmpeg_path: Path, cache_dir: Path | None, default_model: str) -> None:
        self.ffmpeg_path, self.cache_dir = Path(ffmpeg_path), cache_dir
        self.default_model, self.device, self.compute_type = "small", "cpu", "int8"
        self._status = {"ok": False, "reason": "asr_business_unavailable"}
        try:
            compatibility = asr_business_module("compatibility")
            self.default_model = compatibility.normalize_model(default_model)
            self.device = compatibility.normalize_device(os.environ.get("AKANE_LOCAL_ASR_DEVICE", "cpu"))
            self.compute_type = compatibility.normalize_compute(os.environ.get("AKANE_LOCAL_ASR_COMPUTE_TYPE", "int8"))
            self._status = run_completed(self._probe)
        except Exception as exc:
            reason = str(exc)
            self._status = {"ok": False, "reason": reason if reason.startswith(("asr_", "ffmpeg_", "ffprobe_")) else "asr_business_unavailable"}

    def _runtime(self):
        return asr_business_module("local").LocalTranscriber(
            model=self.default_model, device=self.device, compute_type=self.compute_type,
            cache_dir=self.cache_dir, ffmpeg=self.ffmpeg_path,
        )

    async def _probe(self):
        runtime = self._runtime()
        try:
            return await runtime.probe()
        finally:
            await runtime.aclose()

    @property
    def ready(self) -> bool:
        return self._status.get("ok") is True

    def public_status(self):
        return {
            "ready": self.ready, "reason": "" if self.ready else self._status.get("reason", "asr_unavailable"),
            "model": self.default_model, "device": self._status.get("device", self.device),
            "compute_type": self._status.get("compute_type", self.compute_type),
        }

    def transcribe(self, *, source_path: Path, model_size: str, language: str, vad_filter: bool):
        if not self.ready:
            raise RuntimeError(self._status.get("reason", "asr_unavailable"))
        local = asr_business_module("local")
        compatibility = asr_business_module("compatibility")
        options = local.Options(
            model_size=compatibility.normalize_model(model_size or self.default_model),
            language=compatibility.normalize_language(language) or "auto", vad_filter=vad_filter,
        )

        async def execute():
            runtime = self._runtime()
            try:
                return await runtime.transcribe(source=source_path, options=options)
            finally:
                await runtime.aclose()

        result = run_completed(execute)
        return {"text": result["text"], "language": result["language"], "duration": result["duration_seconds"],
                "segments": result["segments"], "model": result["model"]}


class _DemucsCoverProvider:
    """Product binding: existing Demucs deployment + package RVC provider."""

    provider_id = "local_demucs_rvc"
    prepares_source = True

    def __init__(self, rvc, demucs, lock, model):
        self.rvc, self.demucs, self.lock, self.separation_model = rvc, demucs, lock, model

    def resolve_voice_model(self, *args, **kwargs):
        return self.rvc.resolve_voice_model(*args, **kwargs)

    def model_fingerprint(self, model):
        return self.rvc.model_fingerprint(model)

    def convert_voice(self, **kwargs):
        return self.rvc.convert_voice(**kwargs)

    def separate_vocals(self, *, source_path, work_dir):
        with self.lock:
            if self.rvc.cancelled():
                raise CoverSongError(stage="cancelled", reason="operation_cancelled", public_message="翻唱已取消。")
            stems = self.demucs.separate(
                source_path=source_path, output_root=work_dir / "stems", model=self.separation_model
            )
        vocals, instrumental = stems.get("vocals"), stems.get("instrumental")
        if not isinstance(vocals, Path) or not isinstance(instrumental, Path):
            raise CoverSongError(stage="separation", reason="demucs_outputs_missing", public_message="未得到完整分轨。")
        return vocals, instrumental


def _cover_media(ffmpeg_path, runner):
    sibling = Path(ffmpeg_path).with_name("ffprobe" + Path(ffmpeg_path).suffix)
    return CoverMedia(
        run=runner.run, ffmpeg=ffmpeg_path, ffprobe=sibling if sibling.is_file() else shutil.which("ffprobe")
    )


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

    def request_rvc(calls=None, model=None):
        return RvcWebUiProvider(
            base_url=rvc_base_url,
            root_dir=rvc_root_dir or "",
            timeout_seconds=float(os.environ.get("AKANE_LOCAL_RVC_TIMEOUT_SECONDS", "1800") or 1800),
            separation_model=model or separation_model,
            cancelled=calls.cancelled if calls else lambda: False,
        )

    rvc_provider = request_rvc()
    demucs_runtime = LocalDemucsRuntime(
        python_path=demucs_python_path,
        package_root=demucs_package_root,
        ffmpeg_path=ffmpeg_path,
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
            "asr": asr_runtime.public_status(),
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
        calls = ProviderCalls()
        try:
            provider = request_rvc(calls, requested_separation_model.strip())
            with tempfile.TemporaryDirectory(prefix="akane_local_rvc_separate_") as tmp:
                vocals, instrumental = await calls.call(
                    provider.separate_vocals, source_path=source_path, work_dir=Path(tmp)
                )
                archive_buffer = io.BytesIO()
                with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_STORED) as archive:
                    archive.writestr("vocals.wav", vocals.read_bytes())
                    archive.writestr("instrumental.wav", instrumental.read_bytes())
                return Response(content=archive_buffer.getvalue(), media_type="application/zip")
        except Exception as exc:
            raise _http_error("local_rvc_separation_failed", "本地人声分离失败。", exc) from exc
        finally:
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
        calls = ProviderCalls()
        try:
            provider = request_rvc(calls)
            options = CoverOptions(
                pitch_shift=pitch_shift,
                index_rate=index_rate,
                filter_radius=filter_radius,
                rms_mix_rate=rms_mix_rate,
                protect=protect,
            )
            with tempfile.TemporaryDirectory(prefix="akane_local_rvc_convert_") as tmp:
                output_path = Path(tmp) / "converted.wav"
                result = await calls.call(
                    provider.convert_voice,
                    source_path=source_path,
                    output_path=output_path,
                    model_name=model_name,
                    pitch_shift=options.pitch_shift,
                    index_rate=options.index_rate,
                    filter_radius=options.filter_radius,
                    rms_mix_rate=options.rms_mix_rate,
                    protect=options.protect,
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

    @app.post("/v1/rvc/cover")
    async def render_cover(
        file: UploadFile = File(...),
        model_name: str = Form(...),
        demucs_model: str = Form("htdemucs"),
        output_format: str = Form("mp3"),
        pitch_shift: int = Form(0),
        index_rate: float = Form(0.6),
        filter_radius: int = Form(3),
        rms_mix_rate: float = Form(0.25),
        protect: float = Form(0.33),
        vocal_gain_db: float = Form(0.0),
        instrumental_gain_db: float = Form(-1.0),
    ) -> Response:
        if not demucs_runtime.ready:
            raise HTTPException(
                status_code=503,
                detail={"reason": "demucs_not_found", "message": "本地 Demucs 暂时不可用。"},
            )
        normalized_demucs = str(demucs_model or "htdemucs").strip()
        if normalized_demucs not in {"htdemucs", "htdemucs_ft"}:
            raise HTTPException(
                status_code=400,
                detail={"reason": "demucs_model_not_allowed", "message": "请求的 Demucs 模型不受支持。"},
            )
        normalized_format = str(output_format or "mp3").strip().lower().lstrip(".")
        if normalized_format not in {"mp3", "flac", "wav"}:
            raise HTTPException(
                status_code=400,
                detail={"reason": "cover_output_format_not_allowed", "message": "请求的翻唱格式不受支持。"},
            )
        source_path = await _store_upload(file)
        runner, calls = PluginProcessRunner(), ProviderCalls()
        try:
            provider = _DemucsCoverProvider(request_rvc(calls), demucs_runtime, demucs_lock, normalized_demucs)
            options = CoverOptions(
                pitch_shift=pitch_shift,
                index_rate=index_rate,
                filter_radius=filter_radius,
                rms_mix_rate=rms_mix_rate,
                protect=protect,
                vocal_gain_db=vocal_gain_db,
                instrumental_gain_db=instrumental_gain_db,
            )
            with tempfile.TemporaryDirectory(prefix="akane_local_cover_") as tmp:
                result = await CoverPipeline(
                    provider=provider,
                    media=_cover_media(ffmpeg_path, runner),
                    calls=calls,
                ).run(
                    source_path=source_path,
                    work_dir=Path(tmp),
                    voice_model=model_name,
                    options=options,
                    output_format=normalized_format,
                )
                timings = result["processing"]["seconds"]
                timings.update({f"rvc_{k}": v for k, v in result["processing"]["rvc"].items()})
                return Response(
                    content=result["path"].read_bytes(),
                    media_type={"mp3": "audio/mpeg", "flac": "audio/flac", "wav": "audio/wav"}[normalized_format],
                    headers={"X-Akane-Cover-Timings": json.dumps(timings, ensure_ascii=True)},
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise _http_error("local_cover_failed", "本地完整翻唱失败。", exc) from exc
        finally:
            try:
                await runner.aclose()
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
    codec_args = ["-c:a", "libmp3lame", "-b:a", "320k"] if normalized_format == "mp3" else ["-c:a", "flac"]
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
