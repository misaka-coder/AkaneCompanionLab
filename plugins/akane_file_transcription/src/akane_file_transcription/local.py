"""Owned FFmpeg preparation and offline inference; no host-private imports."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from companion_v01.plugin_subprocess import PluginProcessRunner


MODELS = ("auto", "tiny", "base", "small", "medium", "large-v2", "large-v3")
INPUT_OPTIONS = (
    "-protocol_whitelist",
    "file",
    "-format_whitelist",
    "aac,ac3,aiff,amr,ape,asf,avi,flac,flv,matroska,webm,mov,mp3,mpeg,mpegts,ogg,wav,w64,wv",
)
PROTECTED = {"ncm", "qmc", "qmc0", "qmc3", "qmcflac", "kgm", "kgma", "vpr", "uc", "mflac", "mgg", "tkm"}


class TranscriptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Options:
    model_size: str = "auto"
    device: str = "auto"
    compute_type: str = "auto"
    language: str = "zh"
    vad_filter: bool = True

    def __post_init__(self):
        if self.model_size not in MODELS:
            raise TranscriptionError("asr_model_invalid")
        if self.device not in ("auto", "cpu", "cuda"):
            raise TranscriptionError("asr_device_invalid")
        if self.compute_type not in ("auto", "float16", "float32", "int8", "int8_float16"):
            raise TranscriptionError("asr_compute_type_invalid")
        if not isinstance(self.language, str) or not re.fullmatch(r"auto|[a-z]{2,3}", self.language):
            raise TranscriptionError("asr_language_invalid")
        if not isinstance(self.vad_filter, bool):
            raise TranscriptionError("asr_vad_invalid")


class LocalTranscriber:
    def __init__(self, *, python=None, cache_dir=None, model=None, device=None, compute_type=None, ffmpeg=None):
        self.python = python or os.environ.get("AKANE_ASR_PYTHON", "").strip() or sys.executable
        self.cache_dir = cache_dir if cache_dir is not None else os.environ.get("AKANE_ASR_CACHE_DIR", "").strip()
        self.defaults = Options(
            model_size=model or os.environ.get("AKANE_ASR_MODEL", "small").strip(),
            device=device or os.environ.get("AKANE_ASR_DEVICE", "auto").strip(),
            compute_type=compute_type or os.environ.get("AKANE_ASR_COMPUTE_TYPE", "auto").strip(),
        )
        if self.defaults.model_size == "auto":
            raise TranscriptionError("asr_default_model_invalid")
        self.ffmpeg = ffmpeg
        self.runner = PluginProcessRunner()

    async def worker(self, *, source=None, options=None):
        options = options or self.defaults
        python = shutil.which(str(self.python))
        if not python:
            raise TranscriptionError("asr_python_not_found")
        args = [python, Path(__file__).with_name("worker.py"), "--language", options.language]
        for name, option, default in (
            ("model", options.model_size, self.defaults.model_size),
            ("device", options.device, self.defaults.device),
            ("compute-type", options.compute_type, self.defaults.compute_type),
        ):
            args.extend(("--" + name, default if option == "auto" else option))
        if self.cache_dir:
            args.extend(("--cache-dir", self.cache_dir))
        if source is not None:
            args.extend(("--source", source))
        if options.vad_filter:
            args.append("--vad-filter")
        try:
            code, raw = await self.runner.run(args, capture=True, timeout=1800 if source else 60)
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError
        except (OSError, ValueError):
            raise TranscriptionError("asr_worker_unavailable") from None
        except asyncio.TimeoutError:
            raise TranscriptionError("asr_timeout") from None
        if code or result.get("ok") is not True:
            reason = result.get("reason")
            allowed = {
                "asr_runtime_incompatible",
                "asr_model_missing",
                "asr_input_pcm_invalid",
                "asr_model_unavailable",
                "asr_inference_failed",
                "asr_no_speech",
                "asr_output_too_large",
            }
            raise TranscriptionError(reason if reason in allowed else "asr_execution_failed")
        return result

    def binary(self, name):
        value = (
            self.ffmpeg
            if name == "ffmpeg" and self.ffmpeg
            else os.environ.get(f"AKANE_MEDIA_{name.upper()}", "") or name
        )
        path = shutil.which(str(value))
        if not path:
            raise TranscriptionError(f"{name}_not_found")
        return path

    async def probe(self):
        for name in ("ffmpeg", "ffprobe"):
            code, _ = await self.runner.run([self.binary(name), "-version"], timeout=10)
            if code:
                raise TranscriptionError(f"{name}_unavailable")
        return await self.worker()

    async def transcribe(self, *, source, options=None, work_root=None):
        source = Path(source)
        if source.suffix.lower().lstrip(".") in PROTECTED:
            raise TranscriptionError("protected_media_format")
        if not source.is_file() or source.stat().st_size == 0:
            raise TranscriptionError("asr_source_missing")
        options = options or Options()
        try:
            with tempfile.TemporaryDirectory(prefix="asr-", dir=work_root) as tmp:
                prepared = Path(tmp) / "prepared.wav"
                await self.prepare(source=source, prepared=prepared)
                return await self.worker(source=prepared, options=options)
        except asyncio.TimeoutError:
            raise TranscriptionError("asr_timeout") from None
        except OSError:
            raise TranscriptionError("asr_io_failed") from None

    async def prepare(self, *, source, prepared):
        source, prepared = Path(source), Path(prepared)
        if source.resolve() == prepared.resolve() or prepared.exists():
            raise TranscriptionError("asr_prepared_already_exists")
        if source.suffix.lower().lstrip(".") in PROTECTED:
            raise TranscriptionError("protected_media_format")
        prepared.parent.mkdir(parents=True, exist_ok=True)
        succeeded = False
        try:
            args = [
                self.binary("ffmpeg"),
                "-v",
                "error",
                "-nostdin",
                "-y",
                *INPUT_OPTIONS,
                "-i",
                source,
                "-map",
                "0:a:0",
                "-vn",
                "-map_metadata",
                "-1",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                prepared,
            ]
            code, _ = await self.runner.run(args, timeout=900)
            if code or not prepared.is_file() or prepared.stat().st_size <= 44:
                raise TranscriptionError("asr_audio_prepare_failed")
            succeeded = True
        except asyncio.TimeoutError:
            raise TranscriptionError("asr_timeout") from None
        except OSError:
            raise TranscriptionError("asr_io_failed") from None
        finally:
            if not succeeded:
                prepared.unlink(missing_ok=True)

    async def aclose(self):
        await self.runner.aclose()
