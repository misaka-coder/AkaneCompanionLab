from __future__ import annotations

import json
import shutil
import subprocess
import wave
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
        attachment = service._resolve_attachment_media_source(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=normalized,
        )
        if attachment is not None:
            return attachment
    generated = service._resolve_generated_media_source(
        profile_user_id=profile_user_id,
        session_id=session_id,
        target=normalized,
    )
    if generated is not None:
        return generated
    if looks_like_generated:
        return None
    return service._resolve_attachment_media_source(
        profile_user_id=profile_user_id,
        session_id=session_id,
        target=normalized,
    )


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
