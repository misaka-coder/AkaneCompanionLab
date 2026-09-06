"""Media conversion domain logic. No host implementation imports."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


FORMATS = {
    "mp3": ("libmp3lame", "audio/mpeg"),
    "wav": ("pcm_s16le", "audio/wav"),
    "flac": ("flac", "audio/flac"),
    "m4a": ("aac", "audio/mp4"),
    "aac": ("aac", "audio/aac"),
    "ogg": ("libvorbis", "audio/ogg"),
    "opus": ("libopus", "audio/ogg"),
}
# Only self-contained media containers; never playlists that could cause a
# decoder to open arbitrary host files or network streams outside the input.
INPUT_FORMATS = "aac,ac3,aiff,amr,ape,asf,avi,flac,flv,matroska,webm,mov,mp3,mpeg,mpegts,ogg,wav,w64,wv"
INPUT_OPTIONS = ("-protocol_whitelist", "file", "-format_whitelist", INPUT_FORMATS)
PROTECTED_FORMATS = frozenset(
    {"ncm", "qmc", "qmc0", "qmc3", "qmcflac", "kgm", "kgma", "vpr", "uc", "mflac", "mgg", "tkm"}
)


class ConversionError(ValueError):
    pass


def seconds(value: Any, *, field: str) -> float:
    text = str(value).strip().lower()
    text = text.replace("秒钟", "秒").replace("分钟", "分")
    for original, short in (
        ("seconds", "s"),
        ("second", "s"),
        ("secs", "s"),
        ("sec", "s"),
        ("minutes", "m"),
        ("minute", "m"),
        ("mins", "m"),
        ("min", "m"),
    ):
        text = text.replace(original, short)
    try:
        if ":" in text:
            parts = [float(part) for part in text.split(":")]
            if not 2 <= len(parts) <= 3 or any(part < 0 for part in parts):
                raise ValueError
            amount = sum(part * 60**index for index, part in enumerate(reversed(parts)))
        else:
            match = re.fullmatch(r"(?:(\d+(?:\.\d+)?)\s*[m分])?\s*(?:(\d+(?:\.\d+)?)\s*[s秒]?)?", text)
            if not match or not any(match.groups()):
                raise ValueError
            amount = float(match[1] or 0) * 60 + float(match[2] or 0)
        if not math.isfinite(amount) or amount < 0:
            raise ValueError
        return amount
    except (ValueError, TypeError, OverflowError):
        raise ConversionError(f"invalid_{field}") from None


def _number(args: Mapping[str, Any], field: str, default: float, minimum: float, maximum: float | None = None) -> float:
    value = args.get(field, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConversionError(f"invalid_{field}")
    value = float(value)
    if not math.isfinite(value) or value < minimum or (maximum is not None and value > maximum):
        raise ConversionError(f"invalid_{field}")
    return value


@dataclass(frozen=True)
class Options:
    output_format: str
    bitrate: str
    sample_rate: int
    channels: int
    start: float
    end: float | None
    normalize_volume: bool
    gain: float
    trim_silence: bool
    fade_in: float
    fade_out: float
    speed: float

    @classmethod
    def from_args(cls, args: Mapping[str, Any]) -> "Options":
        output_format = str(args.get("output_format") or "").strip().lower().lstrip(".")
        if output_format not in FORMATS:
            raise ConversionError("unsupported_output_format")
        start = seconds(args.get("start_time") or "0", field="start_time")
        end = seconds(args["end_time"], field="end_time") if args.get("end_time") not in (None, "") else None
        if end is not None and end <= start:
            raise ConversionError("invalid_time_range")
        bitrate = str(args.get("bitrate") or "").strip().lower()
        if bitrate and not re.fullmatch(r"[1-9]\d*(?:\.\d+)?[km]?", bitrate):
            raise ConversionError("invalid_bitrate")
        sample_rate = _number(args, "sample_rate", 0, 0)
        channels = _number(args, "channels", 0, 0, 2)
        if sample_rate != int(sample_rate) or channels not in (0, 1, 2):
            raise ConversionError("invalid_audio_specification")
        for field in ("normalize_volume", "trim_silence", "send_to_user"):
            if field in args and not isinstance(args[field], bool):
                raise ConversionError(f"invalid_{field}")
        return cls(
            output_format,
            bitrate,
            int(sample_rate),
            int(channels),
            start,
            end,
            args.get("normalize_volume", False),
            _number(args, "volume_gain_db", 0, -24, 24),
            args.get("trim_silence", False),
            _number(args, "fade_in_seconds", 0, 0, 600),
            _number(args, "fade_out_seconds", 0, 0, 600),
            _number(args, "speed_ratio", 1, 0.25, 4),
        )


def number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def audio_filters(options: Options, *, source_duration: float) -> list[str]:
    filters = []
    if options.trim_silence:
        # A 120 ms onset gate clips the first/last phoneme. Detect immediately
        # and keep a small edge padding instead of removing non-silent speech.
        trim = "silenceremove=start_periods=1:start_duration=0:start_threshold=-50dB:start_silence=0.02:detection=peak"
        filters.extend((trim, "areverse", trim, "areverse"))
    speed = options.speed
    while speed > 2:
        filters.append("atempo=2")
        speed /= 2
    while speed < 0.5:
        filters.append("atempo=0.5")
        speed /= 0.5
    if abs(speed - 1) > 0.000001:
        filters.append(f"atempo={number(speed)}")
    if options.normalize_volume:
        filters.append("loudnorm")
    if options.gain:
        filters.append(f"volume={number(options.gain)}dB")
    if options.fade_in:
        filters.append(f"afade=t=in:st=0:d={number(options.fade_in)}")
    if options.fade_out:
        if options.trim_silence:
            # The trimmed duration is not known before the filters execute.
            filters.extend(("areverse", f"afade=t=in:st=0:d={number(options.fade_out)}", "areverse"))
        else:
            duration = (min(options.end or source_duration, source_duration) - options.start) / options.speed
            fade = min(options.fade_out, duration)
            filters.append(f"afade=t=out:st={number(max(0, duration - fade))}:d={number(fade)}")
    return filters


def command(ffmpeg: str, source: Path, output: Path, options: Options, *, source_duration: float) -> list[str]:
    result = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *INPUT_OPTIONS]
    if options.start:
        result.extend(("-ss", number(options.start)))
    if options.end is not None:
        # Input duration, not output duration: speed changes must not select a
        # different source segment or silently truncate the resulting audio.
        result.extend(("-t", number(options.end - options.start)))
    result.extend(
        ("-i", str(source), "-map", "0:a:0", "-vn", "-sn", "-dn", "-codec:a", FORMATS[options.output_format][0])
    )
    filters = audio_filters(options, source_duration=source_duration)
    if filters:
        result.extend(("-filter:a", ",".join(filters)))
    if options.bitrate and options.output_format not in {"wav", "flac"}:
        result.extend(("-b:a", options.bitrate))
    if options.sample_rate:
        result.extend(("-ar", str(options.sample_rate)))
    if options.channels:
        result.extend(("-ac", str(options.channels)))
    result.append(str(output))
    return result
