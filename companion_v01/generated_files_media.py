from __future__ import annotations

import importlib.util
import json
import math
import re
import shutil
import subprocess
import sys
import time
import wave
import zipfile
from array import array
from pathlib import Path
from typing import Any


def _media_source_unavailable_feedback(
    source: dict[str, Any],
    *,
    action: str,
    default_message: str,
) -> tuple[str, str]:
    status = str(source.get("status") or "").strip().lower()
    failure = source.get("failure") if isinstance(source.get("failure"), dict) else {}
    failure_code = str(failure.get("code") or "").strip()
    failure_reason = " ".join(str(failure.get("reason") or "").split()).strip()
    if status == "failed" or failure_code:
        code = failure_code or "attachment_failed"
        reason = failure_reason or "这个附件在接收或处理阶段失败了。"
        return (
            code,
            f"你刚刚想{action}，但来源附件此前已经失败：{reason} 请依据这个真实原因自然告诉用户，不要改说成本地文件不存在。",
        )
    return "source_file_missing", default_message


def resolve_media_source(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    target: str,
) -> dict[str, Any] | None:
    normalized = str(target or "").strip() or "latest"
    lowered = normalized.lower()
    looks_like_generated = lowered.startswith(("gen_", "generated::"))
    if not looks_like_generated:
        attachment = service._resolve_attachment_style_source(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=normalized,
        )
        if attachment is not None:
            return attachment
    generated = service._resolve_generated_style_source(
        profile_user_id=profile_user_id,
        session_id=session_id,
        target=normalized,
    )
    if generated is not None:
        return generated
    if looks_like_generated:
        return None
    return service._resolve_attachment_style_source(
        profile_user_id=profile_user_id,
        session_id=session_id,
        target=normalized,
    )


def normalize_voice_dataset_profile(value: Any) -> str:
    text = str(value or "gpt_sovits").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "gpt_sovits": "gpt_sovits",
        "gptsovits": "gpt_sovits",
        "gpt_sovits_dataset": "gpt_sovits",
        "so_vits": "gpt_sovits",
        "sovits": "gpt_sovits",
        "rvc": "rvc",
        "voice_conversion": "rvc",
        "archive": "archive",
        "整理归档": "archive",
        "归档": "archive",
    }
    return aliases.get(text, "gpt_sovits")


def normalize_voice_dataset_options(
    service: Any,
    *,
    preset: dict[str, Any],
    target_sr: Any,
    mono: Any,
    min_clip_seconds: Any,
    max_clip_seconds: Any,
    silence_threshold_db: Any,
    min_silence_ms: Any,
    max_silence_kept_ms: Any,
) -> dict[str, Any]:
    def number(value: Any, default: float, minimum: float, maximum: float) -> float:
        try:
            parsed = float(str(value).strip())
        except Exception:
            parsed = default
        return max(minimum, min(maximum, parsed))

    def integer(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(float(str(value).strip()))
        except Exception:
            parsed = default
        return max(minimum, min(maximum, parsed))

    sample_rate = integer(target_sr, int(preset.get("target_sr") or 44100), 8000, 96000)
    min_clip = number(min_clip_seconds, float(preset.get("min_clip_seconds") or 3.0), 0.5, 60.0)
    max_clip = number(max_clip_seconds, float(preset.get("max_clip_seconds") or 12.0), 1.0, 120.0)
    if max_clip < min_clip:
        max_clip = min_clip
    threshold = number(
        silence_threshold_db,
        float(preset.get("silence_threshold_db") or -40.0),
        -80.0,
        -10.0,
    )
    min_silence = integer(min_silence_ms, int(preset.get("min_silence_ms") or 300), 80, 3000)
    keep_silence = integer(max_silence_kept_ms, int(preset.get("max_silence_kept_ms") or 300), 0, 2000)
    return {
        "target_sr": sample_rate,
        "mono": True if mono is None else service._coerce_bool(mono, default=True),
        "min_clip_seconds": round(min_clip, 3),
        "max_clip_seconds": round(max_clip, 3),
        "silence_threshold_db": round(threshold, 2),
        "min_silence_ms": min_silence,
        "max_silence_kept_ms": keep_silence,
    }


def infer_voice_dataset_title(*, sources: list[dict[str, Any]], profile: str) -> str:
    if sources:
        source_title = str(sources[0].get("title") or sources[0].get("handle") or "").strip()
        if source_title:
            return f"{Path(source_title).stem}_{profile}_训练素材"
    return f"{profile}_训练素材"


def prepare_voice_dataset_input(
    service: Any,
    *,
    ffmpeg_path: str,
    source_path: Path,
    prepared_path: Path,
    target_sr: int,
    channels: int,
    normalize_volume: bool,
) -> dict[str, Any]:
    prepared_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(ffmpeg_path),
        "-y",
        "-i",
        str(source_path),
        "-vn",
    ]
    filters: list[str] = []
    if normalize_volume:
        filters.append("loudnorm=I=-18:TP=-1.5:LRA=11")
    if filters:
        command.extend(["-filter:a", ",".join(filters)])
    command.extend(
        [
            "-codec:a",
            "pcm_s16le",
            "-ar",
            str(int(target_sr or 44100)),
            "-ac",
            "1" if int(channels or 1) <= 1 else "2",
            str(prepared_path),
        ]
    )
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if completed.returncode != 0 or not prepared_path.exists():
        return {
            "ok": False,
            "error": (completed.stderr or completed.stdout or "ffmpeg 训练素材预处理失败").strip()[:500],
        }
    return {"ok": True}


def read_pcm16_wav_samples(path: Path) -> tuple[array, int]:
    with wave.open(str(path), "rb") as reader:
        sample_rate = int(reader.getframerate() or 44100)
        channels = int(reader.getnchannels() or 1)
        sample_width = int(reader.getsampwidth() or 2)
        raw = reader.readframes(reader.getnframes())
    if sample_width != 2:
        raise RuntimeError("训练素材切片目前只支持 16-bit PCM wav。")
    samples = array("h")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    if channels > 1:
        mono = array("h")
        for index in range(0, len(samples), channels):
            chunk = samples[index : index + channels]
            if not chunk:
                continue
            mono.append(int(sum(int(value) for value in chunk) / len(chunk)))
        samples = mono
    return samples, sample_rate


def slice_voice_samples(
    service: Any,
    *,
    samples: array,
    sample_rate: int,
    silence_threshold_db: float,
    min_silence_ms: int,
    max_silence_kept_ms: int,
) -> list[dict[str, Any]]:
    total_samples = len(samples)
    if total_samples <= 0 or sample_rate <= 0:
        return []
    hop_samples = max(1, int(sample_rate * 0.02))
    min_silence_samples = max(hop_samples, int(sample_rate * max(0, min_silence_ms) / 1000.0))
    keep_samples = max(0, int(sample_rate * max(0, max_silence_kept_ms) / 1000.0))
    intervals: list[dict[str, Any]] = []
    current_start: int | None = None
    silence_start: int | None = None
    best_cut_sample = 0
    best_cut_db = 0.0

    frame_start = 0
    while frame_start < total_samples:
        frame_end = min(total_samples, frame_start + hop_samples)
        dbfs = service._rms_dbfs_for_samples(samples[frame_start:frame_end])
        silent = dbfs < silence_threshold_db
        if current_start is None:
            if not silent:
                current_start = max(0, frame_start - keep_samples)
            frame_start += hop_samples
            continue

        if silent:
            if silence_start is None:
                silence_start = frame_start
                best_cut_sample = frame_start
                best_cut_db = dbfs
            elif dbfs < best_cut_db:
                best_cut_sample = frame_start
                best_cut_db = dbfs
            if frame_start - silence_start + hop_samples >= min_silence_samples:
                end_sample = min(total_samples, best_cut_sample + keep_samples)
                if end_sample > current_start:
                    intervals.append(
                        {
                            "start_sample": current_start,
                            "end_sample": end_sample,
                            "start_seconds": round(current_start / float(sample_rate), 3),
                            "end_seconds": round(end_sample / float(sample_rate), 3),
                        }
                    )
                current_start = None
                silence_start = None
        else:
            silence_start = None
        frame_start += hop_samples

    if current_start is not None and total_samples > current_start:
        intervals.append(
            {
                "start_sample": current_start,
                "end_sample": total_samples,
                "start_seconds": round(current_start / float(sample_rate), 3),
                "end_seconds": round(total_samples / float(sample_rate), 3),
            }
        )
    return service._merge_tiny_voice_intervals(intervals, sample_rate=sample_rate)


def merge_tiny_voice_intervals(intervals: list[dict[str, Any]], *, sample_rate: int) -> list[dict[str, Any]]:
    if not intervals:
        return []
    merged: list[dict[str, Any]] = []
    min_gap = int(sample_rate * 0.12)
    for interval in intervals:
        if not merged:
            merged.append(dict(interval))
            continue
        previous = merged[-1]
        gap = int(interval.get("start_sample") or 0) - int(previous.get("end_sample") or 0)
        if gap <= min_gap:
            previous["end_sample"] = max(int(previous.get("end_sample") or 0), int(interval.get("end_sample") or 0))
            previous["end_seconds"] = round(int(previous["end_sample"]) / float(sample_rate), 3)
        else:
            merged.append(dict(interval))
    return merged


def analyze_voice_slice(
    service: Any,
    *,
    samples: array,
    sample_rate: int,
    min_clip_seconds: float,
    max_clip_seconds: float,
) -> dict[str, Any]:
    duration = len(samples) / float(sample_rate or 44100)
    rms_dbfs = service._rms_dbfs_for_samples(samples)
    peak_dbfs = service._peak_dbfs_for_samples(samples)
    flags: list[str] = []
    if duration < float(min_clip_seconds or 0):
        flags.append("too_short")
    if duration > float(max_clip_seconds or 0):
        flags.append("too_long")
    if rms_dbfs < -35.0:
        flags.append("low_volume")
    if peak_dbfs > -0.5:
        flags.append("clipping")
    if rms_dbfs < -60.0 or duration <= 0.05:
        flags.append("empty_or_failed")
    return {
        "duration_seconds": round(duration, 3),
        "rms_dbfs": rms_dbfs,
        "peak_dbfs": peak_dbfs,
        "flags": flags,
    }


def rms_dbfs_for_samples(samples: array) -> float:
    if not samples:
        return -120.0
    total = 0.0
    for value in samples:
        sample = int(value)
        total += sample * sample
    rms = math.sqrt(total / max(1, len(samples)))
    if rms <= 0:
        return -120.0
    return round(20.0 * math.log10(rms / 32768.0), 2)


def peak_dbfs_for_samples(samples: array) -> float:
    if not samples:
        return -120.0
    peak = max(abs(int(value)) for value in samples)
    if peak <= 0:
        return -120.0
    return round(20.0 * math.log10(peak / 32768.0), 2)


def build_voice_dataset_manifest(
    *,
    title: str,
    profile: str,
    options: dict[str, Any],
    clean_first: bool,
    normalize_volume: bool,
    sources: list[dict[str, Any]],
    slices: list[dict[str, Any]],
    unresolved: list[str],
    protected: list[str],
    missing_files: list[str],
    timestamp: int,
) -> dict[str, Any]:
    total_duration = round(sum(float(item.get("duration_seconds") or 0) for item in slices), 3)
    issue_slices: dict[str, list[dict[str, Any]]] = {}
    for item in slices:
        for flag in list(item.get("flags") or []):
            issue_slices.setdefault(str(flag), []).append(
                {
                    "filename": item.get("filename"),
                    "source_handle": item.get("source_handle"),
                    "duration_seconds": item.get("duration_seconds"),
                    "rms_dbfs": item.get("rms_dbfs"),
                    "peak_dbfs": item.get("peak_dbfs"),
                }
            )
    flagged = [item for item in slices if item.get("flags")]
    return {
        "title": title,
        "profile": profile,
        "created_at": timestamp,
        "options": dict(options, clean_first=bool(clean_first), normalize_volume=bool(normalize_volume)),
        "stats": {
            "source_count": len(sources),
            "slice_count": len(slices),
            "recommended_count": len(slices) - len(flagged),
            "flagged_count": len(flagged),
            "total_duration_seconds": total_duration,
            "average_duration_seconds": round(total_duration / max(1, len(slices)), 3),
        },
        "sources": sources,
        "slices": slices,
        "issue_slices": issue_slices,
        "unresolved": unresolved,
        "protected": protected,
        "missing_files": missing_files,
    }


def probe_media_duration(*, ffprobe_path: str, source_path: Path) -> float | None:
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(source_path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    try:
        duration = float(str(completed.stdout or "").strip())
    except Exception:
        return None
    return duration if duration > 0 else None


def probe_media_info(*, ffprobe_path: str, source_path: Path) -> dict[str, Any] | None:
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(source_path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    try:
        parsed = json.loads(str(completed.stdout or "{}"))
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def normalize_media_probe_info(
    service: Any,
    data: dict[str, Any],
    *,
    source: dict[str, Any],
    source_path: Path,
) -> dict[str, Any]:
    format_info = data.get("format") if isinstance(data.get("format"), dict) else {}
    streams = data.get("streams") if isinstance(data.get("streams"), list) else []
    audio_streams: list[dict[str, Any]] = []
    video_streams: list[dict[str, Any]] = []
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        kind = str(stream.get("codec_type") or "").strip().lower()
        if kind == "audio":
            audio_streams.append(service._normalize_audio_stream(stream))
        elif kind == "video":
            video_streams.append(service._normalize_video_stream(stream))

    duration = service._safe_float(format_info.get("duration"))
    if duration is None:
        duration_values = [
            item.get("duration_seconds")
            for item in [*audio_streams, *video_streams]
            if isinstance(item.get("duration_seconds"), (int, float))
        ]
        duration = max(duration_values) if duration_values else None
    file_size = service._safe_int(format_info.get("size"))
    if file_size is None:
        try:
            file_size = int(source_path.stat().st_size)
        except Exception:
            file_size = None
    bit_rate = service._safe_int(format_info.get("bit_rate"))
    return {
        "source": {
            "source_type": str(source.get("source_type") or "").strip(),
            "source_id": str(source.get("source_id") or "").strip(),
            "handle": str(source.get("handle") or "").strip(),
            "title": str(source.get("title") or source_path.name).strip(),
            "file_ext": source_path.suffix.lower().lstrip("."),
        },
        "format_name": str(format_info.get("format_name") or source_path.suffix.lower().lstrip(".")).strip(),
        "format_long_name": str(format_info.get("format_long_name") or "").strip(),
        "duration_seconds": round(float(duration), 3) if duration is not None else None,
        "file_size": file_size,
        "bit_rate": bit_rate,
        "audio": audio_streams[0] if audio_streams else None,
        "video": video_streams[0] if video_streams else None,
        "audio_streams": audio_streams[:4],
        "video_streams": video_streams[:4],
    }


def normalize_audio_stream(service: Any, stream: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": service._safe_int(stream.get("index")),
        "codec": str(stream.get("codec_name") or "").strip(),
        "codec_long_name": str(stream.get("codec_long_name") or "").strip(),
        "sample_rate": service._safe_int(stream.get("sample_rate")),
        "channels": service._safe_int(stream.get("channels")),
        "channel_layout": str(stream.get("channel_layout") or "").strip(),
        "bit_rate": service._safe_int(stream.get("bit_rate")),
        "duration_seconds": service._safe_float(stream.get("duration")),
    }


def normalize_video_stream(service: Any, stream: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": service._safe_int(stream.get("index")),
        "codec": str(stream.get("codec_name") or "").strip(),
        "codec_long_name": str(stream.get("codec_long_name") or "").strip(),
        "width": service._safe_int(stream.get("width")),
        "height": service._safe_int(stream.get("height")),
        "fps": service._parse_frame_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate")),
        "pix_fmt": str(stream.get("pix_fmt") or "").strip(),
        "bit_rate": service._safe_int(stream.get("bit_rate")),
        "duration_seconds": service._safe_float(stream.get("duration")),
    }


def parse_frame_rate(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text == "0/0":
        return None
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        try:
            den = float(denominator)
            if den == 0:
                return None
            return round(float(numerator) / den, 3)
        except Exception:
            return None
    try:
        return round(float(text), 3)
    except Exception:
        return None


def safe_int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None


def safe_float(value: Any) -> float | None:
    try:
        number = float(str(value).strip())
    except Exception:
        return None
    return number if number >= 0 else None


def format_duration_label(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return ""
    seconds = max(0, int(round(float(value))))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_file_size(value: Any) -> str:
    if not isinstance(value, int) or value < 0:
        return ""
    units = ["B", "KB", "MB", "GB"]
    amount = float(value)
    unit = units[0]
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            break
        amount /= 1024
    if unit == "B":
        return f"{int(amount)}B"
    return f"{amount:.2f}{unit}"


def format_bitrate(value: Any) -> str:
    if not isinstance(value, int) or value <= 0:
        return ""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}Mbps"
    return f"{value / 1000:.0f}kbps"


def parse_bitrate_value(value: Any) -> int | None:
    text = str(value or "").strip().lower().replace(" ", "")
    if not text:
        return None
    multiplier = 1
    if text.endswith(("kbps", "kbit/s")):
        multiplier = 1000
        text = text.split("k", 1)[0]
    elif text.endswith(("mbps", "mbit/s")):
        multiplier = 1_000_000
        text = text.split("m", 1)[0]
    elif text.endswith("k"):
        multiplier = 1000
        text = text[:-1]
    elif text.endswith("m"):
        multiplier = 1_000_000
        text = text[:-1]
    try:
        parsed = float(text)
    except Exception:
        return None
    if parsed <= 0:
        return None
    return int(parsed * multiplier)


def generated_media_codec_for_format(output_format: str) -> str:
    normalized = str(output_format or "").strip().lower().lstrip(".")
    aliases = {
        "mp3": "mp3",
        "wav": "pcm_s16le",
        "flac": "flac",
        "m4a": "aac",
        "aac": "aac",
        "ogg": "vorbis",
        "opus": "opus",
    }
    return aliases.get(normalized, normalized)


def fallback_wav_media_info(path: Path) -> dict[str, Any]:
    try:
        with wave.open(str(path), "rb") as reader:
            channels = int(reader.getnchannels() or 0)
            sample_rate = int(reader.getframerate() or 0)
            sample_width = int(reader.getsampwidth() or 0)
            frames = int(reader.getnframes() or 0)
    except Exception:
        return {}
    duration = round(frames / float(sample_rate), 3) if sample_rate > 0 else None
    bit_rate = sample_rate * channels * sample_width * 8 if sample_rate > 0 and channels > 0 and sample_width > 0 else None
    return {
        "duration_seconds": duration,
        "audio": {
            "codec": "pcm_s16le" if sample_width == 2 else "pcm",
            "sample_rate": sample_rate or None,
            "channels": channels or None,
            "bit_rate": bit_rate,
            "duration_seconds": duration,
        },
    }


def build_generated_media_info_projection(
    service: Any,
    *,
    output_path: Path,
    output_format: str,
    source: dict[str, Any] | None = None,
    hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the lightweight media card shown in the generated-file workbench."""
    normalized_format = str(output_format or output_path.suffix.lstrip(".")).strip().lower()
    hints = hints if isinstance(hints, dict) else {}
    source = source if isinstance(source, dict) else {}
    source_media = source.get("media_info") if isinstance(source.get("media_info"), dict) else {}
    source_audio = source_media.get("audio") if isinstance(source_media.get("audio"), dict) else {}
    source_video = source_media.get("video") if isinstance(source_media.get("video"), dict) else {}

    wav_info = fallback_wav_media_info(output_path) if normalized_format == "wav" else {}
    wav_audio = wav_info.get("audio") if isinstance(wav_info.get("audio"), dict) else {}

    duration = (
        service._safe_float(hints.get("duration_seconds"))
        if hints.get("duration_seconds") is not None
        else None
    )
    if isinstance(duration, (int, float)) and duration <= 0:
        duration = None
    if duration is None:
        duration = service._safe_float(wav_info.get("duration_seconds"))
    if isinstance(duration, (int, float)) and duration <= 0:
        duration = None
    if duration is None:
        duration = service._safe_float(source_media.get("duration_seconds"))
    if isinstance(duration, (int, float)) and duration <= 0:
        duration = None

    sample_rate = service._safe_int(hints.get("sample_rate"))
    if sample_rate is None:
        sample_rate = service._safe_int(wav_audio.get("sample_rate"))
    if sample_rate is None:
        sample_rate = service._safe_int(source_audio.get("sample_rate"))
    if isinstance(sample_rate, int) and sample_rate <= 0:
        sample_rate = None

    channels = service._safe_int(hints.get("channels"))
    if channels is None:
        channels = service._safe_int(wav_audio.get("channels"))
    if channels is None:
        channels = service._safe_int(source_audio.get("channels"))
    if isinstance(channels, int) and channels <= 0:
        channels = None

    bit_rate = service._safe_int(hints.get("bit_rate"))
    if bit_rate is None:
        bit_rate = parse_bitrate_value(hints.get("bitrate"))
    if bit_rate is None:
        bit_rate = service._safe_int(wav_audio.get("bit_rate"))
    if bit_rate is None and normalized_format == "wav" and sample_rate and channels:
        bit_rate = int(sample_rate) * int(channels) * 16
    if isinstance(bit_rate, int) and bit_rate <= 0:
        bit_rate = None

    file_size = None
    try:
        file_size = int(output_path.stat().st_size)
    except Exception:
        file_size = service._safe_int(hints.get("file_size"))

    audio: dict[str, Any] | None = None
    if normalized_format in {"mp3", "wav", "flac", "m4a", "aac", "ogg", "opus"} or source_audio or sample_rate or channels:
        audio = {
            "codec": str(hints.get("codec") or wav_audio.get("codec") or generated_media_codec_for_format(normalized_format)).strip(),
            "sample_rate": sample_rate,
            "channels": channels,
            "channel_layout": str(source_audio.get("channel_layout") or "").strip(),
            "bit_rate": bit_rate,
            "duration_seconds": round(float(duration), 3) if duration is not None else None,
        }

    video: dict[str, Any] | None = None
    if normalized_format in {"mp4", "mov", "mkv", "webm", "avi"} or source_video:
        video = {
            "codec": str(hints.get("video_codec") or source_video.get("codec") or "").strip(),
            "width": service._safe_int(hints.get("width")) or source_video.get("width"),
            "height": service._safe_int(hints.get("height")) or source_video.get("height"),
            "fps": service._safe_float(hints.get("fps")) or source_video.get("fps"),
            "bit_rate": service._safe_int(hints.get("video_bit_rate")) or source_video.get("bit_rate"),
            "duration_seconds": round(float(duration), 3) if duration is not None else None,
        }

    return {
        "format_name": str(hints.get("format_name") or normalized_format or output_path.suffix.lstrip(".")).strip(),
        "format_long_name": str(hints.get("format_long_name") or "").strip(),
        "duration_seconds": round(float(duration), 3) if duration is not None else None,
        "file_size": file_size,
        "bit_rate": bit_rate,
        "audio": audio,
        "video": video,
        "audio_streams": [audio] if audio else [],
        "video_streams": [video] if video else [],
        "projection_source": "generated_file_lightweight",
    }


def prepare_voice_dataset(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    source_targets: list[str] | tuple[str, ...] | str,
    profile: str = "gpt_sovits",
    output_title: str = "",
    target_sr: int = 0,
    mono: bool = True,
    min_clip_seconds: Any = 0,
    max_clip_seconds: Any = 0,
    silence_threshold_db: Any = None,
    min_silence_ms: Any = 0,
    max_silence_kept_ms: Any = 0,
    clean_first: bool = False,
    normalize_volume: bool = False,
    send_to_user: bool = True,
    timestamp: int | None = None,
    protected_media_extensions: set[str],
    voice_dataset_presets: dict[str, dict[str, Any]],
    client_mode: str = "web",
) -> dict[str, Any]:
    effective_ts = int(timestamp or time.time())
    preset_name = service._normalize_voice_dataset_profile(profile)
    preset = dict(voice_dataset_presets.get(preset_name) or voice_dataset_presets["gpt_sovits"])
    normalized_targets = service._normalize_targets(source_targets)
    if not normalized_targets:
        return {
            "ok": False,
            "generated": None,
            "error": "missing_sources",
            "followup_context": "你刚刚想准备语音训练集，但没有指定要切片的音频来源。请自然向用户确认要处理哪几个文件。",
        }

    resolved_sources: list[dict[str, Any]] = []
    unresolved: list[str] = []
    protected: list[str] = []
    missing_files: list[str] = []
    seen_source_ids: set[str] = set()
    for target in normalized_targets:
        source = service._resolve_media_source(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
        )
        if source is None:
            unresolved.append(target)
            continue
        source_id = str(source.get("source_id") or source.get("handle") or target).strip()
        if source_id in seen_source_ids:
            continue
        source_path = Path(source.get("absolute_path") or "")
        if not source_path.exists() or not source_path.is_file():
            missing_files.append(str(source.get("handle") or target))
            continue
        input_ext = source_path.suffix.lower().lstrip(".")
        if input_ext in protected_media_extensions:
            protected.append(f"{source.get('handle') or target}({input_ext})")
            continue
        seen_source_ids.add(source_id)
        resolved_sources.append(dict(source, absolute_path=source_path, input_ext=input_ext))

    if not resolved_sources:
        reason = "、".join([*unresolved[:3], *missing_files[:3], *protected[:3]]) or "没有可处理的音频来源"
        return {
            "ok": False,
            "generated": None,
            "error": "no_usable_sources",
            "unresolved": unresolved,
            "missing_files": missing_files,
            "protected": protected,
            "followup_context": (
                f"你刚刚想准备语音训练集，但没有找到可用的普通音频/视频来源：{reason}。"
                "请自然告诉用户需要提供普通 mp3、wav、flac、m4a 或带音轨视频。"
            ),
        }

    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        return {
            "ok": False,
            "generated": None,
            "error": "ffmpeg_not_found",
            "followup_context": "你刚刚想准备语音训练集，但本机没有找到 ffmpeg。请自然提醒用户先安装 ffmpeg 或配置 PATH。",
        }

    options = service._normalize_voice_dataset_options(
        preset=preset,
        target_sr=target_sr,
        mono=mono,
        min_clip_seconds=min_clip_seconds,
        max_clip_seconds=max_clip_seconds,
        silence_threshold_db=silence_threshold_db,
        min_silence_ms=min_silence_ms,
        max_silence_kept_ms=max_silence_kept_ms,
    )
    title = service._normalize_title(output_title) or service._infer_voice_dataset_title(
        sources=resolved_sources,
        profile=preset_name,
    )
    output_path = service._build_output_path(
        profile_user_id=profile_user_id,
        session_id=session_id,
        title=title,
        output_format="zip",
        timestamp=effective_ts,
    )
    work_dir = (
        service.work_dir
        / "_voice_dataset_tmp"
        / service._safe_filename(profile_user_id or "profile")[:48]
        / service._safe_filename(session_id or "session")[:48]
        / str(effective_ts)
    )
    slice_dir = work_dir / "slices"
    prepared_paths: list[Path] = []
    manifest_slices: list[dict[str, Any]] = []
    source_stats: list[dict[str, Any]] = []
    global_index = 1
    try:
        slice_dir.mkdir(parents=True, exist_ok=True)
        for source_index, source in enumerate(resolved_sources, start=1):
            source_path = Path(source.get("absolute_path") or "")
            source_label = str(source.get("handle") or source.get("title") or f"source_{source_index}").strip()
            source_title = str(source.get("title") or source_path.name).strip()
            prepared_path = work_dir / f"source_{source_index:02d}.wav"
            prepared_paths.append(prepared_path)
            if clean_first:
                preparer = getattr(service, "voice_preparer", None)
                try:
                    cleaned = preparer(
                        profile_user_id=profile_user_id, session_id=session_id,
                        source_id=str(source.get("source_id") or source.get("handle") or ""),
                        client_mode=client_mode,
                    ) if callable(preparer) else {"reason": "cleaning_plugin_unavailable"}
                except Exception:
                    cleaned = {"reason": "cleaning_binding_failed"}
                if cleaned.get("status") != "ready" or not cleaned.get("absolute_path"):
                    source_stats.append({
                        "source_index": source_index, "source_id": str(source.get("source_id") or ""),
                        "handle": source_label, "title": source_title, "status": "failed", "slice_count": 0,
                        "error": cleaned.get("reason") or "cleaning_preparation_failed",
                    })
                    continue
                source_path = Path(cleaned["absolute_path"])
            prepare_result = service._prepare_voice_dataset_input(
                ffmpeg_path=str(ffmpeg_path),
                source_path=source_path,
                prepared_path=prepared_path,
                target_sr=int(options["target_sr"]),
                channels=1 if bool(options["mono"]) else 2,
                normalize_volume=bool(normalize_volume),
            )
            if not prepare_result.get("ok"):
                source_stats.append(
                    {
                        "source_index": source_index,
                        "source_id": str(source.get("source_id") or "").strip(),
                        "handle": source_label,
                        "title": source_title,
                        "status": "failed",
                        "error": str(prepare_result.get("error") or "")[:180],
                        "slice_count": 0,
                    }
                )
                continue

            samples, sample_rate = service._read_pcm16_wav_samples(prepared_path)
            intervals = service._slice_voice_samples(
                samples=samples,
                sample_rate=sample_rate,
                silence_threshold_db=float(options["silence_threshold_db"]),
                min_silence_ms=int(options["min_silence_ms"]),
                max_silence_kept_ms=int(options["max_silence_kept_ms"]),
            )
            if not intervals and len(samples) > 0:
                intervals = [
                    {
                        "start_sample": 0,
                        "end_sample": len(samples),
                        "start_seconds": 0.0,
                        "end_seconds": round(len(samples) / float(sample_rate), 3),
                    }
                ]

            source_slice_count = 0
            for local_index, interval in enumerate(intervals, start=1):
                start_sample = max(0, int(interval.get("start_sample") or 0))
                end_sample = min(len(samples), int(interval.get("end_sample") or 0))
                if end_sample <= start_sample:
                    continue
                slice_samples = samples[start_sample:end_sample]
                duration = round((end_sample - start_sample) / float(sample_rate), 3)
                metrics = service._analyze_voice_slice(
                    samples=slice_samples,
                    sample_rate=sample_rate,
                    min_clip_seconds=float(options["min_clip_seconds"]),
                    max_clip_seconds=float(options["max_clip_seconds"]),
                )
                filename = f"src{source_index:02d}_slice_{local_index:03d}.wav"
                slice_path = slice_dir / filename
                service._write_pcm16_wav(slice_path, samples=slice_samples, sample_rate=sample_rate, channels=1)
                manifest_slices.append(
                    {
                        "filename": filename,
                        "source_index": source_index,
                        "source_id": str(source.get("source_id") or "").strip(),
                        "source_handle": source_label,
                        "source_title": source_title,
                        "global_index": global_index,
                        "local_index": local_index,
                        "start_time": round(start_sample / float(sample_rate), 3),
                        "end_time": round(end_sample / float(sample_rate), 3),
                        "duration_seconds": duration,
                        "sample_rate": sample_rate,
                        "channels": 1,
                        "rms_dbfs": metrics["rms_dbfs"],
                        "peak_dbfs": metrics["peak_dbfs"],
                        "flags": metrics["flags"],
                        "included": True,
                    }
                )
                source_slice_count += 1
                global_index += 1
            source_stats.append(
                {
                    "source_index": source_index,
                    "source_id": str(source.get("source_id") or "").strip(),
                    "handle": source_label,
                    "title": source_title,
                    "status": "ready",
                    "slice_count": source_slice_count,
                }
            )

        if not manifest_slices:
            errors = [
                f"{item.get('handle')}: {item.get('error')}"
                for item in source_stats
                if item.get("status") == "failed"
            ]
            return {
                "ok": False,
                "generated": None,
                "error": "no_slices_created",
                "source_failures": source_stats,
                "followup_context": (
                    "你刚刚想准备语音训练集，但没有成功切出任何片段。"
                    + (f"失败信息：{'; '.join(errors[:3])}。" if errors else "")
                    + "请自然告诉用户可以换更清晰的人声音频，或先做人声分离/净化。"
                ),
            }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = service._build_voice_dataset_manifest(
            title=title,
            profile=preset_name,
            options=options,
            clean_first=bool(clean_first),
            normalize_volume=bool(normalize_volume),
            sources=source_stats,
            slices=manifest_slices,
            unresolved=unresolved,
            protected=protected,
            missing_files=missing_files,
            timestamp=effective_ts,
        )
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            archive.writestr("README.md", service._render_voice_dataset_readme(manifest))
            for item in manifest_slices:
                filename = str(item.get("filename") or "").strip()
                slice_path = slice_dir / filename
                if slice_path.exists():
                    archive.write(slice_path, f"slices/{filename}")

        source_ids = [
            str(source.get("source_id") or "").strip()
            for source in resolved_sources
            if str(source.get("source_id") or "").strip()
        ]
        content_card = service._build_voice_dataset_content_card(manifest)
        generated = service.store.add_generated_file(
            profile_user_id=profile_user_id,
            session_id=session_id,
            output_title=title,
            output_format="zip",
            storage_relpath=service._storage_relpath(output_path),
            mime_type=service._mime_type_for_format("zip"),
            file_ext="zip",
            file_size=output_path.stat().st_size,
            source_ids=source_ids,
            content_card=content_card,
            summary=str(content_card.get("summary") or "").strip(),
            created_by_tool="prepare_voice_dataset",
            delivery_status="pending" if send_to_user else "not_requested",
            timestamp=effective_ts,
        )
        generated["absolute_path"] = str(service.absolute_path(generated))
        return {
            "ok": True,
            "generated": generated,
            "send_to_user": bool(send_to_user),
            "unresolved": unresolved,
            "protected": protected,
            "missing_files": missing_files,
            "followup_context": service._build_voice_dataset_followup(
                generated=generated,
                manifest=manifest,
                send_to_user=send_to_user,
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "generated": None,
            "error": str(exc),
            "followup_context": f"你刚刚准备语音训练素材时失败了：{str(exc)[:180]}。请自然告诉用户失败原因。",
        }
    finally:
        if work_dir.exists():
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
            except Exception:
                pass


def inspect_media_info(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    source_target: str,
    timestamp: int | None = None,
) -> dict[str, Any]:
    source = service._resolve_media_source(
        profile_user_id=profile_user_id,
        session_id=session_id,
        target=source_target,
    )
    if source is None:
        return {
            "ok": False,
            "source": None,
            "media_info": None,
            "error": "source_not_found",
            "followup_context": "你刚刚想查看媒体文件信息，但没有找到明确的来源。请自然向用户确认要查看哪一个附件或生成文件。",
        }

    source_path = Path(source.get("absolute_path") or "")
    if not source_path.exists() or not source_path.is_file():
        error, followup_context = _media_source_unavailable_feedback(
            source,
            action="查看媒体文件信息",
            default_message="你刚刚想查看媒体文件信息，但本地来源文件不存在。请自然告诉用户这个文件暂时无法读取。",
        )
        return {
            "ok": False,
            "source": source,
            "media_info": None,
            "error": error,
            "followup_context": followup_context,
        }

    ffprobe_path = shutil.which("ffprobe")
    if not ffprobe_path:
        return {
            "ok": False,
            "source": source,
            "media_info": None,
            "error": "ffprobe_not_found",
            "followup_context": "你刚刚想查看媒体文件信息，但本机没有找到 ffprobe。请自然提醒用户先安装 ffmpeg/ffprobe 或配置 PATH。",
        }

    probe = service._probe_media_info(ffprobe_path=ffprobe_path, source_path=source_path)
    if probe is None:
        return {
            "ok": False,
            "source": source,
            "media_info": None,
            "error": "ffprobe_failed",
            "followup_context": "你刚刚想查看媒体文件信息，但 ffprobe 没能读取这个文件。请自然告诉用户它可能不是普通媒体文件，或者文件本身不完整。",
        }

    media_info = service._normalize_media_probe_info(probe, source=source, source_path=source_path)
    return {
        "ok": True,
        "source": source,
        "media_info": media_info,
        "followup_context": service._build_media_info_followup(
            source=source,
            media_info=media_info,
        ),
    }
