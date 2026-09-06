"""Shared media preparation for the plugin and standalone service bindings."""

from __future__ import annotations

import json
import math
import os
import shutil

from .process import ProcessRunner


FORMATS = {"wav": ("pcm_s16le", "audio/wav"), "flac": ("flac", "audio/flac"), "mp3": ("libmp3lame", "audio/mpeg")}

INPUT_OPTIONS = (
    "-protocol_whitelist",
    "file",
    "-format_whitelist",
    "aac,ac3,aiff,amr,ape,asf,avi,flac,flv,matroska,webm,mov,mp3,mpeg,mpegts,ogg,wav,w64,wv",
)

PROTECTED = {"ncm", "qmc", "qmc0", "qmc3", "qmcflac", "kgm", "kgma", "vpr", "uc", "mflac", "mgg", "tkm"}


class SeparationError(RuntimeError):
    pass


def executable(name):
    path = shutil.which(os.environ.get(f"AKANE_MEDIA_{name.upper()}", "").strip() or name)
    if not path:
        raise SeparationError(f"{name}_not_found")
    return path


class MediaTools:
    def __init__(self, *, ffmpeg=None, ffprobe=None):
        self.runner = ProcessRunner()
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    async def probe_audio(self, path):
        code, raw = await self.runner.run(
            [
                self.ffprobe or executable("ffprobe"),
                "-v",
                "error",
                *INPUT_OPTIONS,
                "-select_streams",
                "a:0",
                "-show_entries",
                "format=duration:stream=sample_rate,channels,duration",
                "-of",
                "json",
                path,
            ],
            capture=True,
            timeout=30,
        )
        if code:
            raise SeparationError("separation_media_invalid")
        try:
            data = json.loads(raw)
            stream = data["streams"][0]
            rate, channels = int(stream["sample_rate"]), int(stream["channels"])
            duration = float(stream.get("duration") or data.get("format", {}).get("duration") or 0)
            if rate <= 0 or channels <= 0 or not math.isfinite(duration) or duration <= 0:
                raise ValueError
            return {"sample_rate": rate, "channels": channels, "duration_seconds": duration}
        except (ValueError, KeyError, IndexError, TypeError):
            raise SeparationError("separation_media_invalid") from None

    async def encode(self, source, target, output_format, *, sample_rate=None, channels=None):
        args = [
            self.ffmpeg or executable("ffmpeg"),
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
            "-c:a",
            FORMATS[output_format][0],
        ]
        if sample_rate:
            args.extend(("-ar", str(sample_rate)))
        if channels:
            args.extend(("-ac", str(channels)))
        code, _ = await self.runner.run([*args, target], timeout=300)
        if code or not target.is_file() or target.stat().st_size == 0:
            raise SeparationError("separation_encoding_failed")

    async def aclose(self):
        await self.runner.aclose()
