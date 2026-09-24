"""One voice-cleaning recipe, with honest optional AI fallback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile

from companion_v01.plugin_subprocess import PluginProcessRunner


FORMATS = {"wav": ("pcm_s16le", "audio/wav"), "flac": ("flac", "audio/flac"), "mp3": ("libmp3lame", "audio/mpeg")}
MODES = ("denoise", "dereverb", "deecho", "voice_focus")
INPUT_OPTIONS = (
    "-protocol_whitelist",
    "file",
    "-format_whitelist",
    "aac,ac3,aiff,amr,ape,asf,avi,flac,flv,matroska,webm,mov,mp3,mpeg,mpegts,ogg,wav,w64,wv",
)
PROTECTED = {"ncm", "qmc", "qmc0", "qmc3", "qmcflac", "kgm", "kgma", "vpr", "uc", "mflac", "mgg", "tkm"}


class CleaningError(RuntimeError):
    pass


@dataclass(frozen=True)
class Options:
    mode: str = "denoise"
    quality: str = "auto"
    output_format: str = "wav"
    post_filter: bool = False

    def __post_init__(self):
        if self.mode not in MODES:
            raise CleaningError("cleaning_mode_invalid")
        if self.quality not in ("auto", "basic", "ai"):
            raise CleaningError("cleaning_quality_invalid")
        if self.output_format not in FORMATS:
            raise CleaningError("cleaning_format_invalid")
        if not isinstance(self.post_filter, bool):
            raise CleaningError("cleaning_post_filter_invalid")


def basic_filters(options):
    noise = {"denoise": "18:nf=-28", "voice_focus": "20:nf=-30", "dereverb": "22:nf=-26", "deecho": "22:nf=-26"}
    filters = ["highpass=f=70", "lowpass=f=12000", f"afftdn=nr={noise[options.mode]}"]
    if options.post_filter:
        filters.append("afftdn=nr=24:nf=-24")
    return ",".join(filters)


def executable(name):
    path = shutil.which(os.environ.get(f"AKANE_MEDIA_{name.upper()}", "").strip() or name)
    if not path:
        raise CleaningError(f"{name}_not_found")
    return path


class VoiceCleaner:
    def __init__(self, *, python=None, model_root=None, device=None):
        self.python = python or os.environ.get("AKANE_CLEAN_PYTHON", "").strip() or sys.executable
        self.model_root = model_root if model_root is not None else os.environ.get("AKANE_CLEAN_MODEL_ROOT", "").strip()
        self.device = device or os.environ.get("AKANE_CLEAN_DEVICE", "auto").strip()
        if self.device not in ("auto", "cpu", "cuda"):
            raise CleaningError("cleaning_device_invalid")
        self.runner = PluginProcessRunner()

    async def ai(self, *, source=None, output=None, post_filter=False):
        python = shutil.which(str(self.python))
        if not python:
            raise CleaningError("deepfilternet_python_not_found")
        argv = [python, str(Path(__file__).with_name("worker.py")), "--device", self.device]
        if self.model_root:
            argv.extend(("--model-root", str(self.model_root)))
        if post_filter:
            argv.append("--post-filter")
        argv.extend(("--source", str(source), "--output", str(output)) if source else ("--probe",))
        try:
            code, raw = await self.runner.run(argv, capture=True, timeout=1800 if source else 60)
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError
        except (OSError, ValueError):
            raise CleaningError("deepfilternet_worker_unavailable") from None
        except asyncio.TimeoutError:
            raise CleaningError("deepfilternet_timeout") from None
        if code or not result.get("ok"):
            allowed = {
                "deepfilternet_runtime_incompatible",
                "deepfilternet_model_missing",
                "deepfilternet_model_unavailable",
                "deepfilternet_inference_failed",
                "deepfilternet_execution_failed",
                "cleaning_input_missing",
                "cleaning_input_pcm_invalid",
            }
            reason = result.get("reason")
            raise CleaningError(reason if reason in allowed else "deepfilternet_execution_failed")
        return result

    async def health(self):
        # Optional AI does not disable real basic FFmpeg processing.
        for name in ("ffmpeg", "ffprobe"):
            code, _ = await self.runner.run([executable(name), "-version"], timeout=10)
            if code:
                raise CleaningError(f"{name}_not_executable")
        return {"ok": True, "backend": "basic_ffmpeg"}

    async def probe_audio(self, path):
        code, raw = await self.runner.run(
            [
                executable("ffprobe"),
                "-v",
                "error",
                *INPUT_OPTIONS,
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=sample_rate,channels,duration:format=duration",
                "-of",
                "json",
                path,
            ],
            capture=True,
            timeout=30,
        )
        if code:
            raise CleaningError("cleaning_media_invalid")
        try:
            data = json.loads(raw)
            stream = data["streams"][0]
            duration = float(stream.get("duration") or data.get("format", {}).get("duration") or 0)
            rate, channels = int(stream["sample_rate"]), int(stream["channels"])
            if not math.isfinite(duration) or duration <= 0 or rate <= 0 or channels <= 0:
                raise ValueError
            return {"duration_seconds": duration, "sample_rate": rate, "channels": channels}
        except (ValueError, KeyError, IndexError, TypeError):
            raise CleaningError("cleaning_media_invalid") from None

    async def encode(self, source, output, *, output_format="wav", filters=None):
        argv = [
            executable("ffmpeg"),
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
            "-ar",
            "48000",
            "-ac",
            "1",
            "-c:a",
            FORMATS[output_format][0],
        ]
        if filters:
            argv.extend(("-af", filters))
        code, _ = await self.runner.run([*argv, output], timeout=300)
        if code or not output.is_file() or output.stat().st_size == 0:
            raise CleaningError("cleaning_encoding_failed")

    async def clean(self, *, source, output, options=None):
        options = options or Options()
        source, output = Path(source), Path(output)
        if source.resolve() == output.resolve() or output.exists():
            raise CleaningError("cleaning_output_already_exists")
        if source.suffix.lower().lstrip(".") in PROTECTED:
            raise CleaningError("protected_media_format")
        succeeded = False
        try:
            input_media = await self.probe_audio(source)
            output.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="cleaning-", dir=output.parent) as tmp:
                prepared, cleaned = Path(tmp) / "prepared.wav", Path(tmp) / "cleaned.wav"
                await self.encode(source, prepared)
                backend, fallback, device_used = "basic_ffmpeg", "", ""
                if options.quality != "basic":
                    try:
                        ai_info = await self.ai(
                            source=prepared,
                            output=cleaned,
                            post_filter=options.post_filter or options.mode != "denoise",
                        )
                        backend = "deepfilternet"
                        device_used = ai_info["device_used"]
                    except CleaningError as exc:
                        if options.quality == "ai":
                            raise
                        fallback = str(exc)
                        cleaned.unlink(missing_ok=True)
                if backend == "basic_ffmpeg":
                    await self.encode(prepared, cleaned, filters=basic_filters(options))
                await self.encode(cleaned, output, output_format=options.output_format)
                media = await self.probe_audio(output)
                if abs(media["duration_seconds"] - input_media["duration_seconds"]) > max(
                    0.3, input_media["duration_seconds"] * 0.01
                ):
                    raise CleaningError("cleaning_output_duration_invalid")
                succeeded = True
                notices = []
                if options.mode in ("dereverb", "deecho"):
                    notices.append("这是降噪/人声增强预设，不是独立去混响或回声消除模型，不能保证移除混响或回声。")
                return {
                    "backend_used": backend,
                    "quality_requested": options.quality,
                    "mode": options.mode,
                    "post_filter": options.post_filter,
                    "post_filter_applied": options.post_filter
                    or (backend == "deepfilternet" and options.mode != "denoise"),
                    "fallback_reason": fallback,
                    "device_used": device_used,
                    "media_info": media,
                    "notices": notices,
                }
        except asyncio.TimeoutError:
            raise CleaningError("cleaning_timeout") from None
        except OSError:
            raise CleaningError("cleaning_io_failed") from None
        finally:
            if not succeeded:
                try:
                    output.unlink(missing_ok=True)
                except OSError:
                    import logging

                    logging.getLogger(__name__).warning("cleaning_temporary_output_cleanup_failed")

    async def aclose(self):
        await self.runner.aclose()
