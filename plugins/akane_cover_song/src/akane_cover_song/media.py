"""Cover media policy, independent of host storage and subprocess ownership.

The caller injects an async ``run(argv, capture=False, timeout=...)`` port.
Akane binds its public SDK process runner; other users may supply their own.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from .errors import CoverSongError


INPUT_OPTIONS = (
    "-protocol_whitelist",
    "file",
    "-format_whitelist",
    "aac,ac3,aiff,amr,ape,asf,avi,flac,flv,matroska,webm,mov,mp3,mpeg,mpegts,ogg,wav,w64,wv",
)
FORMATS = {
    "mp3": ("-c:a", "libmp3lame", "-b:a", "320k"),
    "flac": ("-c:a", "flac"),
    "wav": ("-c:a", "pcm_s24le"),
}


def failure(stage, reason):
    return CoverSongError(stage=stage, reason=reason, public_message="翻唱媒体处理未完成。")


class CoverMedia:
    def __init__(self, *, run, ffmpeg, ffprobe=""):
        self.run = run
        self.ffmpeg = str(ffmpeg or "")
        self.ffprobe = str(ffprobe or "")

    async def probe_audio(self, path: Path) -> dict:
        if not self.ffprobe:
            raise failure("source", "ffprobe_not_found")
        code, raw = await self.run(
            [
                self.ffprobe,
                "-v",
                "error",
                *INPUT_OPTIONS,
                "-select_streams",
                "a:0",
                "-show_entries",
                "format=duration,format_name:stream=duration,sample_rate,channels,codec_name",
                "-of",
                "json",
                path,
            ],
            capture=True,
            timeout=30,
        )
        if code:
            raise failure("source", "cover_media_invalid")
        try:
            data = json.loads(raw)
            stream = data["streams"][0]
            # Video containers may outlive their audio stream.
            duration = float(stream.get("duration") or data.get("format", {}).get("duration") or 0)
            if (
                not math.isfinite(duration)
                or duration <= 0
                or int(stream["sample_rate"]) <= 0
                or int(stream["channels"]) <= 0
            ):
                raise ValueError
        except (ValueError, TypeError, KeyError, IndexError):
            raise failure("source", "cover_media_invalid") from None
        return {
            "duration_seconds": duration,
            "sample_rate": int(stream["sample_rate"]),
            "channels": int(stream["channels"]),
            "codec": str(stream.get("codec_name") or ""),
            "container": str(data.get("format", {}).get("format_name") or ""),
        }

    async def probe_output(self, path, output_format):
        info = await self.probe_audio(path)
        if output_format not in FORMATS or info["container"] != output_format:
            raise failure("output", "cover_output_format_mismatch")
        return info

    async def probe_duration(self, path: Path) -> float:
        return (await self.probe_audio(path))["duration_seconds"]

    async def decode(self, *, source_path, output_path):
        await self._render(
            [
                *INPUT_OPTIONS,
                "-i",
                source_path,
                "-map",
                "0:a:0",
                "-vn",
                "-map_metadata",
                "-1",
                "-ar",
                "44100",
                "-ac",
                "2",
                "-c:a",
                "pcm_s16le",
            ],
            output_path,
            stage="decode",
            reason="audio_decode_failed",
            timeout=900,
        )

    async def mix(
        self, *, converted_vocals, instrumental, output_path, output_format, vocal_gain_db, instrumental_gain_db
    ):
        if output_format not in FORMATS:
            raise failure("mix", "cover_output_format_invalid")
        for value, lower, upper in ((vocal_gain_db, -12, 12), (instrumental_gain_db, -12, 6)):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not lower <= value <= upper
            ):
                raise failure("mix", "cover_gain_invalid")
        duration = max(await self.probe_duration(converted_vocals), await self.probe_duration(instrumental))
        # RVC commonly returns mono 40 kHz. Do not let that first input force
        # the stereo instrumental down to mono or lower the final sample rate.
        pcm = "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
        filters = (
            f"[0:a:0]{pcm},volume={vocal_gain_db:.3f}dB,apad=whole_dur={duration:.6f}[v];"
            f"[1:a:0]{pcm},volume={instrumental_gain_db:.3f}dB,apad=whole_dur={duration:.6f}[i];"
            # FFmpeg 4.3 lacks amix normalize=0. Undo two-input averaging
            # explicitly. Pad to equal durations to prevent amix doubling the
            # remaining track when the other track ends first.
            "[v][i]amix=inputs=2:duration=longest:dropout_transition=0,"
            "volume=2.0,alimiter=limit=0.95:attack=5:release=50[m]"
        )
        await self._render(
            [
                *INPUT_OPTIONS,
                "-i",
                converted_vocals,
                *INPUT_OPTIONS,
                "-i",
                instrumental,
                "-filter_complex",
                filters,
                "-map",
                "[m]",
                "-vn",
                "-map_metadata",
                "-1",
                "-t",
                f"{duration:.6f}",
                *FORMATS[output_format],
            ],
            output_path,
            stage="mix",
            reason="audio_mix_failed",
            timeout=1200,
        )

    async def _render(self, args, output_path, *, stage, reason, timeout):
        if not self.ffmpeg:
            raise failure(stage, "ffmpeg_not_found")
        output_path = Path(output_path)
        # Callers must allocate a fresh work output; never overwrite input or
        # an already-published artifact, including through a symlink.
        if output_path.exists() or output_path.is_symlink():
            raise failure(stage, "cover_output_already_exists")
        code, _ = await self.run(
            [
                self.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-n",
                *args,
                output_path,
            ],
            timeout=timeout,
        )
        if code or not output_path.is_file() or output_path.stat().st_size <= 0:
            raise failure(stage, reason)
