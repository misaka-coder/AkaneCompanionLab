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
)
from companion_v01.local_media_executor import safe_model_fingerprint, safe_uploaded_suffix


MAX_UPLOAD_BYTES = 256 * 1024 * 1024
DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 9879


class LocalAsrRuntime:
    def __init__(self, *, ffmpeg_path: Path, cache_dir: Path | None, default_model: str) -> None:
        self.ffmpeg_path = Path(ffmpeg_path)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.default_model = normalize_whisper_model_size(default_model)
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
        device = normalize_whisper_device(os.environ.get("AKANE_LOCAL_ASR_DEVICE", "cpu"))
        compute_type = normalize_whisper_compute_type(os.environ.get("AKANE_LOCAL_ASR_COMPUTE_TYPE", "int8"))
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
                    device=device,
                    compute_type=compute_type,
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
            "ok": bool(asr_runtime.ready or rvc_ready),
            "status": "ready" if asr_runtime.ready and rvc_ready else "degraded",
            "service": "akane_local_media_capabilities",
            "protocol_version": 1,
            "asr": {
                "ready": asr_runtime.ready,
                "reason": "" if asr_runtime.ready else "local_asr_dependencies_missing",
                "model": asr_runtime.default_model,
            },
            "rvc": {
                "ready": rvc_ready,
                "reason": "" if rvc_ready else str(rvc_status.get("reason") or "local_rvc_unavailable"),
                "model_count": model_count,
            },
        }

    @app.post("/v1/audio/transcriptions")
    async def transcribe_audio(
        file: UploadFile = File(...),
        model: str = Form("small"),
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
    app = create_app(
        ffmpeg_path=ffmpeg_path,
        whisper_cache_dir=Path(cache_raw).resolve() if cache_raw else None,
        whisper_model=os.environ.get("AKANE_LOCAL_WHISPER_MODEL", "small"),
        rvc_base_url=os.environ.get("AKANE_LOCAL_RVC_BASE_URL", "http://127.0.0.1:7899"),
        rvc_root_dir=rvc_root,
        separation_model=os.environ.get("AKANE_LOCAL_RVC_SEPARATION_MODEL", "HP5_only_main_vocal"),
    )
    import uvicorn

    uvicorn.run(app, host=args.host, port=max(1, min(65535, int(args.port))), log_level="info")


if __name__ == "__main__":
    main()
