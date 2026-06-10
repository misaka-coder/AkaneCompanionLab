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


def resolve_media_sources_for_batch(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    source_targets: list[str],
    protected_media_extensions: set[str],
) -> tuple[list[dict[str, Any]], list[str], list[str], list[str]]:
    sources: list[dict[str, Any]] = []
    unresolved: list[str] = []
    missing_files: list[str] = []
    protected: list[str] = []
    seen_source_ids: set[str] = set()
    for target in source_targets:
        source = service._resolve_media_source(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
        )
        if source is None:
            unresolved.append(str(target or "").strip())
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
        sources.append(dict(source, absolute_path=source_path, input_ext=input_ext))
    return sources, unresolved, missing_files, protected


def normalize_transcript_output_format(value: Any) -> str:
    text = str(value or "md").strip().lower().lstrip(".")
    aliases = {
        "markdown": "md",
        "text": "txt",
        "plain": "txt",
        "subtitle": "srt",
        "subtitles": "srt",
        "caption": "srt",
        "captions": "srt",
        "webvtt": "vtt",
    }
    return aliases.get(text, text)


def normalize_transcript_language(value: Any) -> str:
    text = str(value or "zh").strip().lower().replace("_", "-")
    aliases = {
        "中文": "zh",
        "Chinese".lower(): "zh",
        "普通话": "zh",
        "国语": "zh",
        "英文": "en",
        "English".lower(): "en",
        "自动": "",
        "auto": "",
        "detect": "",
    }
    normalized = aliases.get(text, text)
    return re.sub(r"[^a-z-]+", "", normalized)[:12]


def normalize_whisper_model_size(value: Any) -> str:
    text = str(value or "small").strip().lower().replace("_", "-")
    aliases = {
        "tiny": "tiny",
        "base": "base",
        "small": "small",
        "medium": "medium",
        "large": "large-v3",
        "large-v3": "large-v3",
        "large-v2": "large-v2",
    }
    return aliases.get(text, "small")


def normalize_whisper_device(value: Any) -> str:
    text = str(value or "auto").strip().lower()
    if text in {"auto", "cuda", "cpu"}:
        return text
    return "auto"


def normalize_whisper_compute_type(value: Any) -> str:
    text = str(value or "auto").strip().lower()
    allowed = {"auto", "float16", "float32", "int8", "int8_float16"}
    return text if text in allowed else "auto"


def load_faster_whisper_model(service: Any, *, model_size: str, device: str, compute_type: str) -> Any:
    cache_key = (model_size, device, compute_type)
    if cache_key in service._whisper_model_cache:
        return service._whisper_model_cache[cache_key]
    from faster_whisper import WhisperModel  # type: ignore

    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    service._whisper_model_cache[cache_key] = model
    return model


def prepare_transcription_input(
    service: Any,
    *,
    ffmpeg_path: str,
    source_path: Path,
    prepared_path: Path,
) -> dict[str, Any]:
    prepared_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(ffmpeg_path),
        "-y",
        "-i",
        str(source_path),
        "-vn",
        "-codec:a",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(prepared_path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if completed.returncode != 0 or not prepared_path.exists():
        return {
            "ok": False,
            "error": (completed.stderr or completed.stdout or "ffmpeg 转写预处理失败").strip()[:500],
        }
    return {"ok": True}


def transcribe_prepared_audio(
    service: Any,
    *,
    model: Any,
    audio_path: Path,
    source: dict[str, Any],
    source_index: int,
    language: str,
    vad_filter: bool,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "beam_size": 5,
        "vad_filter": bool(vad_filter),
    }
    if language:
        kwargs["language"] = language
    try:
        segments_iter, info = model.transcribe(str(audio_path), **kwargs)
        segments = list(segments_iter)
    except Exception as exc:
        return {
            "source_index": source_index,
            "source": service._transcript_source_card(source),
            "status": "failed",
            "error": str(exc)[:300],
            "segments": [],
            "text": "",
        }
    normalized_segments: list[dict[str, Any]] = []
    for index, segment in enumerate(segments, start=1):
        text = str(service._segment_value(segment, "text", "") or "").strip()
        if not text:
            continue
        start = service._safe_float(service._segment_value(segment, "start", 0)) or 0.0
        end = service._safe_float(service._segment_value(segment, "end", start)) or start
        normalized_segments.append(
            {
                "index": index,
                "start": round(start, 3),
                "end": round(max(start, end), 3),
                "text": text,
                "avg_logprob": service._safe_float(service._segment_value(segment, "avg_logprob", None)),
                "no_speech_prob": service._safe_float(service._segment_value(segment, "no_speech_prob", None)),
            }
        )
    info_language = str(service._segment_value(info, "language", language or "") or "").strip()
    duration = service._safe_float(service._segment_value(info, "duration", None))
    text = "\n".join(item["text"] for item in normalized_segments).strip()
    return {
        "source_index": source_index,
        "source": service._transcript_source_card(source),
        "status": "ready",
        "language": info_language or language,
        "duration_seconds": round(duration, 3) if duration is not None else service._estimate_transcript_duration(normalized_segments),
        "segment_count": len(normalized_segments),
        "segments": normalized_segments,
        "text": text,
    }


def segment_value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def transcript_source_card(source: dict[str, Any]) -> dict[str, Any]:
    source_path = Path(source.get("absolute_path") or "")
    return {
        "source_type": str(source.get("source_type") or "").strip(),
        "source_id": str(source.get("source_id") or "").strip(),
        "handle": str(source.get("handle") or "").strip(),
        "title": str(source.get("title") or source_path.name).strip(),
        "input_ext": str(source.get("input_ext") or source_path.suffix.lower().lstrip(".")).strip(),
    }


def infer_transcript_title(*, sources: list[dict[str, Any]], output_format: str, merged: bool) -> str:
    if not sources:
        return f"转写稿_{output_format}"
    first = sources[0]
    title = str(first.get("title") or first.get("handle") or "").strip()
    stem = Path(title).stem if title else "媒体"
    if merged and len(sources) > 1:
        return f"{stem}_等{len(sources)}份转写稿"
    return f"{stem}_转写稿"


def store_transcript_output(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    title: str,
    output_format: str,
    transcripts: list[dict[str, Any]],
    all_transcripts: list[dict[str, Any]],
    language: str,
    with_timestamps: bool,
    merge_outputs: bool,
    model_size: str,
    device: str,
    compute_type: str,
    send_to_user: bool,
    timestamp: int,
) -> dict[str, Any]:
    output_path = service._build_output_path(
        profile_user_id=profile_user_id,
        session_id=session_id,
        title=title,
        output_format=output_format,
        timestamp=timestamp,
    )
    content = service._render_transcript_output(
        transcripts=transcripts,
        output_format=output_format,
        title=title,
        with_timestamps=with_timestamps,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    source_ids: list[str] = []
    for transcript in transcripts:
        source = transcript.get("source") if isinstance(transcript.get("source"), dict) else {}
        source_id = str(source.get("source_id") or "").strip()
        if source_id and source_id not in source_ids:
            source_ids.append(source_id)
    content_card = service._build_transcript_content_card(
        title=title,
        output_format=output_format,
        transcripts=transcripts,
        all_transcripts=all_transcripts,
        language=language,
        with_timestamps=with_timestamps,
        merge_outputs=merge_outputs,
        model_size=model_size,
        device=device,
        compute_type=compute_type,
        content=content,
    )
    generated = service.store.add_generated_file(
        profile_user_id=profile_user_id,
        session_id=session_id,
        output_title=title,
        output_format=output_format,
        storage_relpath=service._storage_relpath(output_path),
        mime_type=service._mime_type_for_format(output_format),
        file_ext=output_format,
        file_size=output_path.stat().st_size,
        source_ids=source_ids,
        content_card=content_card,
        summary=str(content_card.get("summary") or "").strip(),
        created_by_tool="transcribe_media",
        delivery_status="pending" if send_to_user else "not_requested",
        timestamp=timestamp,
    )
    generated["absolute_path"] = str(service.absolute_path(generated))
    return generated


def estimate_transcript_duration(segments: list[dict[str, Any]]) -> float:
    if not segments:
        return 0.0
    ends = [float(item.get("end") or 0) for item in segments if isinstance(item, dict)]
    return round(max(ends) if ends else 0.0, 3)


def format_timestamp_label(value: Any) -> str:
    seconds = max(0.0, float(value or 0))
    total_ms = int(round(seconds * 1000))
    total_seconds, _ = divmod(total_ms, 1000)
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_srt_timestamp(value: Any) -> str:
    seconds = max(0.0, float(value or 0))
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_seconds = total_ms // 1000
    secs = total_seconds % 60
    total_minutes = total_seconds // 60
    minutes = total_minutes % 60
    hours = total_minutes // 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def format_vtt_timestamp(service: Any, value: Any) -> str:
    return service._format_srt_timestamp(value).replace(",", ".")


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
    clean_first: bool,
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
    if clean_first:
        filters.extend(service._build_basic_voice_clean_filter_chain(mode="voice_focus", post_filter=False))
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


def convert_media_file(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    source_target: str,
    output_format: str,
    output_title: str = "",
    bitrate: str = "",
    sample_rate: int = 0,
    channels: int = 0,
    start_time: Any = "",
    end_time: Any = "",
    normalize_volume: bool = False,
    volume_gain_db: Any = 0,
    trim_silence: bool = False,
    fade_in_seconds: Any = 0,
    fade_out_seconds: Any = 0,
    speed_ratio: Any = 0,
    send_to_user: bool = True,
    timestamp: int | None = None,
    media_output_formats: set[str],
    protected_media_extensions: set[str],
) -> dict[str, Any]:
    effective_ts = int(timestamp or time.time())
    normalized_format = service._normalize_media_output_format(output_format)
    if normalized_format not in media_output_formats:
        return {
            "ok": False,
            "generated": None,
            "error": f"暂不支持转成 {output_format or 'unknown'}。",
            "followup_context": "你刚刚想转换媒体文件，但目标格式不支持。请换成 mp3、wav、flac、m4a、aac、ogg 或 opus。",
        }

    source = service._resolve_media_source(
        profile_user_id=profile_user_id,
        session_id=session_id,
        target=source_target,
    )
    if source is None:
        return {
            "ok": False,
            "generated": None,
            "error": "source_not_found",
            "followup_context": "你刚刚想转换媒体文件，但没有找到明确的来源。请自然向用户确认要转换哪一个附件或生成文件。",
        }

    source_path = Path(source.get("absolute_path") or "")
    if not source_path.exists() or not source_path.is_file():
        return {
            "ok": False,
            "generated": None,
            "error": "source_file_missing",
            "followup_context": "你刚刚想转换媒体文件，但本地来源文件不存在。请自然告诉用户这个文件暂时无法转换。",
        }

    input_ext = source_path.suffix.lower().lstrip(".")
    if input_ext in protected_media_extensions:
        return {
            "ok": False,
            "generated": None,
            "error": "protected_media_format",
            "followup_context": (
                f"你刚刚识别到 {input_ext} 这类平台加密或专有缓存格式。"
                "不要尝试解密或绕过保护；请自然告诉用户可以提供普通 mp3、flac、wav、m4a 等非加密音频源文件。"
            ),
        }

    media_options = service._normalize_media_edit_options(
        start_time=start_time,
        end_time=end_time,
        normalize_volume=normalize_volume,
        volume_gain_db=volume_gain_db,
        trim_silence=trim_silence,
        fade_in_seconds=fade_in_seconds,
        fade_out_seconds=fade_out_seconds,
        speed_ratio=speed_ratio,
    )
    if media_options.get("error"):
        return {
            "ok": False,
            "generated": None,
            "error": str(media_options.get("error") or "invalid_media_options"),
            "followup_context": str(media_options.get("followup_context") or "媒体处理参数不够稳定，请自然向用户确认。"),
        }

    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        return {
            "ok": False,
            "generated": None,
            "error": "ffmpeg_not_found",
            "followup_context": "你刚刚想转换媒体文件，但本机没有找到 ffmpeg。请自然提醒用户先安装 ffmpeg 或配置 PATH。",
        }

    if float(media_options.get("fade_out_seconds") or 0) > 0:
        ffprobe_path = shutil.which("ffprobe")
        if not ffprobe_path:
            return {
                "ok": False,
                "generated": None,
                "error": "ffprobe_not_found",
                "followup_context": "你刚刚想给媒体文件做淡出，但本机没有找到 ffprobe，暂时无法准确计算淡出起点。请自然告诉用户需要先配置 ffprobe。",
            }
        duration = service._probe_media_duration(ffprobe_path=ffprobe_path, source_path=source_path)
        if duration is None:
            return {
                "ok": False,
                "generated": None,
                "error": "duration_probe_failed",
                "followup_context": "你刚刚想给媒体文件做淡出，但无法读取媒体总时长。请自然告诉用户这次没法准确做淡出。",
            }
        base_duration = float(media_options.get("duration_seconds") or 0)
        if base_duration <= 0:
            base_duration = max(0.0, duration - float(media_options.get("start_seconds") or 0))
        speed = float(media_options.get("speed_ratio") or 1.0)
        media_options["output_duration_seconds"] = base_duration / max(0.01, speed)

    title = service._normalize_title(output_title) or f"{Path(str(source.get('title') or source_path.stem)).stem}_{normalized_format}"
    output_path = service._build_output_path(
        profile_user_id=profile_user_id,
        session_id=session_id,
        title=title,
        output_format=normalized_format,
        timestamp=effective_ts,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    command = service._build_ffmpeg_command(
        ffmpeg_path=ffmpeg_path,
        source_path=source_path,
        output_path=output_path,
        output_format=normalized_format,
        bitrate=bitrate,
        sample_rate=sample_rate,
        channels=channels,
        media_options=media_options,
    )
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except Exception as exc:
        return {
            "ok": False,
            "generated": None,
            "error": str(exc),
            "followup_context": f"你刚刚转换媒体文件时失败了：{str(exc)[:180]}。请自然告诉用户失败原因。",
        }
    if completed.returncode != 0 or not output_path.exists():
        error_text = (completed.stderr or completed.stdout or "ffmpeg 转换失败").strip()[:500]
        return {
            "ok": False,
            "generated": None,
            "error": error_text,
            "followup_context": f"你刚刚转换媒体文件失败：{error_text[:180]}。请自然告诉用户失败原因。",
        }

    source_ids = [str(source.get("source_id") or "").strip()]
    source_ids.extend(str(item or "").strip() for item in list(source.get("extra_source_ids") or []))
    source_ids = [item for item in source_ids if item]
    source_media = source.get("media_info") if isinstance(source.get("media_info"), dict) else {}
    source_duration = service._safe_float(source_media.get("duration_seconds"))
    duration_hint = service._safe_float(media_options.get("output_duration_seconds"))
    duration_already_adjusted = duration_hint is not None and duration_hint > 0
    if duration_hint is None:
        duration_hint = service._safe_float(media_options.get("duration_seconds"))
    if (duration_hint is None or duration_hint <= 0) and source_duration is not None:
        start_offset = service._safe_float(media_options.get("start_seconds")) or 0.0
        duration_hint = max(0.0, source_duration - start_offset)
    speed_ratio = service._safe_float(media_options.get("speed_ratio")) or 1.0
    if duration_hint is not None and duration_hint > 0 and speed_ratio > 0 and not duration_already_adjusted:
        duration_hint = round(duration_hint / speed_ratio, 3)
    content_card = {
        "type": "media_conversion",
        "summary": f"把 {source.get('handle') or source.get('title') or '媒体文件'} 转换为 {normalized_format}。",
        "source": {
            "source_type": str(source.get("source_type") or "").strip(),
            "source_id": str(source.get("source_id") or "").strip(),
            "handle": str(source.get("handle") or "").strip(),
            "title": str(source.get("title") or "").strip(),
            "input_ext": input_ext,
        },
        "conversion": {
            "output_format": normalized_format,
            "bitrate": str(bitrate or "").strip(),
            "sample_rate": int(sample_rate or 0),
            "channels": int(channels or 0),
            "start_seconds": media_options.get("start_seconds"),
            "end_seconds": media_options.get("end_seconds"),
            "duration_seconds": media_options.get("duration_seconds"),
            "normalize_volume": bool(media_options.get("normalize_volume")),
            "volume_gain_db": media_options.get("volume_gain_db"),
            "trim_silence": bool(media_options.get("trim_silence")),
            "fade_in_seconds": media_options.get("fade_in_seconds"),
            "fade_out_seconds": media_options.get("fade_out_seconds"),
            "speed_ratio": media_options.get("speed_ratio"),
        },
    }
    content_card["media_info"] = build_generated_media_info_projection(
        service,
        output_path=output_path,
        output_format=normalized_format,
        source=source,
        hints={
            "duration_seconds": duration_hint,
            "sample_rate": int(sample_rate or 0),
            "channels": int(channels or 0),
            "bitrate": bitrate,
            "file_size": output_path.stat().st_size,
        },
    )
    generated = service.store.add_generated_file(
        profile_user_id=profile_user_id,
        session_id=session_id,
        output_title=title,
        output_format=normalized_format,
        storage_relpath=service._storage_relpath(output_path),
        mime_type=service._mime_type_for_format(normalized_format),
        file_ext=normalized_format,
        file_size=output_path.stat().st_size,
        source_ids=source_ids,
        content_card=content_card,
        summary=str(content_card.get("summary") or ""),
        created_by_tool="convert_media_file",
        delivery_status="pending" if send_to_user else "not_requested",
        timestamp=effective_ts,
    )
    generated["absolute_path"] = str(service.absolute_path(generated))
    return {
        "ok": True,
        "generated": generated,
        "send_to_user": bool(send_to_user),
        "followup_context": service._build_media_conversion_followup(
            generated=generated,
            source=source,
            output_format=normalized_format,
            send_to_user=send_to_user,
        ),
    }


def separate_audio_stems(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    source_target: str,
    mode: str = "vocals_instrumental",
    output_format: str = "wav",
    output_title: str = "",
    send_to_user: bool = True,
    timestamp: int | None = None,
    protected_media_extensions: set[str],
) -> dict[str, Any]:
    effective_ts = int(timestamp or time.time())
    normalized_mode = str(mode or "vocals_instrumental").strip().lower() or "vocals_instrumental"
    if normalized_mode != "vocals_instrumental":
        return {
            "ok": False,
            "generated_files": [],
            "error": "unsupported_separation_mode",
            "followup_context": (
                "你刚刚想做音频分离，但当前只接入了 vocals_instrumental（人声 / 伴奏）模式。"
                "请自然告诉用户这次先只能拆成人声和伴奏两轨。"
            ),
        }

    normalized_format = service._normalize_media_output_format(output_format)
    if normalized_format not in {"wav", "flac", "mp3"}:
        return {
            "ok": False,
            "generated_files": [],
            "error": "unsupported_separation_output_format",
            "followup_context": "你刚刚想输出分离后的音频文件，但当前只支持 wav、flac 或 mp3。",
        }

    source = service._resolve_media_source(
        profile_user_id=profile_user_id,
        session_id=session_id,
        target=source_target,
    )
    if source is None:
        return {
            "ok": False,
            "generated_files": [],
            "error": "source_not_found",
            "followup_context": "你刚刚想做人声伴奏分离，但没有找到明确的来源。请自然向用户确认要处理哪一个附件或生成文件。",
        }

    source_path = Path(source.get("absolute_path") or "")
    if not source_path.exists() or not source_path.is_file():
        return {
            "ok": False,
            "generated_files": [],
            "error": "source_file_missing",
            "followup_context": "你刚刚想做人声伴奏分离，但本地来源文件不存在。请自然告诉用户这个文件暂时无法处理。",
        }

    input_ext = source_path.suffix.lower().lstrip(".")
    if input_ext in protected_media_extensions:
        return {
            "ok": False,
            "generated_files": [],
            "error": "protected_media_format",
            "followup_context": (
                f"你刚刚识别到 {input_ext} 这类平台加密或专有缓存格式。"
                "不要尝试解密或绕过保护；请自然告诉用户可以提供普通 mp3、flac、wav、m4a 等非加密源文件。"
            ),
        }

    requires_ffmpeg = normalized_format != "wav" or service._is_video_media_format(str(source.get("output_format") or input_ext))
    ffmpeg_path = shutil.which("ffmpeg") if requires_ffmpeg else ""
    if requires_ffmpeg and not ffmpeg_path:
        return {
            "ok": False,
            "generated_files": [],
            "error": "ffmpeg_not_found",
            "followup_context": (
                "你刚刚想做人声伴奏分离，但当前流程需要 ffmpeg 做音轨准备或结果转码，"
                "本机暂时没有找到 ffmpeg。请自然提醒用户先安装 ffmpeg 或配置 PATH。"
            ),
        }

    base_name = Path(str(source.get("title") or source_path.stem)).stem.strip() or source_path.stem or "audio"
    separation_title = service._normalize_title(output_title) or base_name
    work_dir = service.work_dir / "_audio_separation_tmp" / service._safe_filename(profile_user_id or "profile")[:48] / service._safe_filename(session_id or "session")[:48] / str(effective_ts)
    extracted_input_path: Path | None = None
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        prepared_input = source_path
        if service._is_video_media_format(str(source.get("output_format") or input_ext)):
            prepared_input = work_dir / f"{service._safe_filename(base_name)[:48] or 'source'}_audio.wav"
            extract_command = [
                str(ffmpeg_path),
                "-y",
                "-i",
                str(source_path),
                "-vn",
                "-codec:a",
                "pcm_s16le",
                "-ar",
                "44100",
                "-ac",
                "2",
                str(prepared_input),
            ]
            extract_result = subprocess.run(
                extract_command,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            if extract_result.returncode != 0 or not prepared_input.exists():
                error_text = (extract_result.stderr or extract_result.stdout or "ffmpeg 抽取音轨失败").strip()[:500]
                return {
                    "ok": False,
                    "generated_files": [],
                    "error": error_text,
                    "followup_context": f"你刚刚想先从视频里提取音轨再做人声伴奏分离，但抽取失败：{error_text[:180]}。请自然告诉用户失败原因。",
                }
            extracted_input_path = prepared_input

        separation_output_dir = work_dir / "demucs_output"
        if importlib.util.find_spec("demucs") is not None:
            try:
                stems = service._separate_audio_with_demucs_module(
                    source_path=prepared_input,
                    output_root=separation_output_dir,
                )
            except Exception as exc:
                error_text = service._summarize_audio_separation_error(str(exc))
                return {
                    "ok": False,
                    "generated_files": [],
                    "error": error_text,
                    "followup_context": f"你刚刚做人声伴奏分离失败：{error_text[:220]}。请自然告诉用户失败原因。",
                }
        else:
            demucs_command = service._resolve_demucs_command()
            if demucs_command is None:
                return {
                    "ok": False,
                    "generated_files": [],
                    "error": "demucs_not_found",
                    "followup_context": (
                        "你刚刚想做人声伴奏分离，但本机没有找到可用的 Demucs 环境。"
                        "请自然提醒用户先安装 demucs 或配置可调用的本地分离脚本。"
                    ),
                }
            command = [
                *demucs_command,
                "--two-stems",
                "vocals",
                "-o",
                str(separation_output_dir),
                str(prepared_input),
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=1800,
                check=False,
            )
            if completed.returncode != 0:
                error_text = service._summarize_audio_separation_error(
                    completed.stderr or completed.stdout or "demucs 分离失败"
                )
                return {
                    "ok": False,
                    "generated_files": [],
                    "error": error_text,
                    "followup_context": f"你刚刚做人声伴奏分离失败：{error_text[:220]}。请自然告诉用户失败原因。",
                }
            stems = service._collect_demucs_stems(separation_output_dir)
        vocals_path = stems.get("vocals")
        instrumental_path = stems.get("instrumental")
        if not vocals_path or not instrumental_path:
            return {
                "ok": False,
                "generated_files": [],
                "error": "separation_outputs_missing",
                "followup_context": "你刚刚已经跑完了音频分离流程，但没有找到完整的人声/伴奏输出文件。请自然告诉用户这次分离没成功。",
            }

        source_ids = [str(source.get("source_id") or "").strip()]
        source_ids.extend(str(item or "").strip() for item in list(source.get("extra_source_ids") or []))
        source_ids = [item for item in source_ids if item]

        stem_specs = [
            ("vocals", vocals_path, "人声", "vocals"),
            ("instrumental", instrumental_path, "伴奏", "instrumental"),
        ]
        rendered_outputs: list[dict[str, Any]] = []
        for index, (stem_role, stem_source_path, role_label, role_slug) in enumerate(stem_specs):
            title = service._normalize_title(f"{separation_title}_{role_label}") or f"{base_name}_{role_slug}"
            output_path = service._build_output_path(
                profile_user_id=profile_user_id,
                session_id=session_id,
                title=title,
                output_format=normalized_format,
                timestamp=effective_ts + index,
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            service._render_separated_stem_output(
                stem_source_path=stem_source_path,
                output_path=output_path,
                output_format=normalized_format,
                ffmpeg_path=str(ffmpeg_path or ""),
            )
            rendered_outputs.append(
                {
                    "stem_role": stem_role,
                    "role_label": role_label,
                    "title": title,
                    "output_path": output_path,
                    "timestamp": effective_ts + index,
                }
            )

        generated_files: list[dict[str, Any]] = []
        for item in rendered_outputs:
            stem_role = str(item.get("stem_role") or "").strip().lower()
            role_label = str(item.get("role_label") or "").strip() or stem_role or "分离轨"
            title = str(item.get("title") or "").strip() or f"{base_name}_{stem_role or 'stem'}"
            output_path = item.get("output_path")
            if not isinstance(output_path, Path):
                raise RuntimeError("分离输出路径无效。")
            content_card = {
                "type": "audio_separation",
                "summary": f"从 {source.get('handle') or source.get('title') or '媒体文件'} 分离出{role_label}轨。",
                "source": {
                    "source_type": str(source.get("source_type") or "").strip(),
                    "source_id": str(source.get("source_id") or "").strip(),
                    "handle": str(source.get("handle") or "").strip(),
                    "title": str(source.get("title") or "").strip(),
                    "input_ext": input_ext,
                },
                "separation": {
                    "mode": normalized_mode,
                    "stem_role": stem_role,
                    "output_format": normalized_format,
                },
            }
            content_card["media_info"] = build_generated_media_info_projection(
                service,
                output_path=output_path,
                output_format=normalized_format,
                source=source,
                hints={
                    "file_size": output_path.stat().st_size,
                },
            )
            generated = service.store.add_generated_file(
                profile_user_id=profile_user_id,
                session_id=session_id,
                output_title=title,
                output_format=normalized_format,
                storage_relpath=service._storage_relpath(output_path),
                mime_type=service._mime_type_for_format(normalized_format),
                file_ext=normalized_format,
                file_size=output_path.stat().st_size,
                source_ids=source_ids,
                content_card=content_card,
                summary=str(content_card.get("summary") or "").strip(),
                created_by_tool="separate_audio_stems",
                delivery_status="pending" if send_to_user else "not_requested",
                timestamp=int(item.get("timestamp") or effective_ts),
            )
            generated["absolute_path"] = str(service.absolute_path(generated))
            generated_files.append(generated)

        return {
            "ok": True,
            "generated_files": generated_files,
            "send_to_user": bool(send_to_user),
            "followup_context": service._build_audio_separation_followup(
                generated_files=generated_files,
                source=source,
                output_format=normalized_format,
                send_to_user=send_to_user,
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "generated_files": [],
            "error": str(exc),
            "followup_context": f"你刚刚做人声伴奏分离时失败了：{str(exc)[:180]}。请自然告诉用户失败原因。",
        }
    finally:
        if extracted_input_path is not None and extracted_input_path.exists():
            try:
                extracted_input_path.unlink()
            except Exception:
                pass
        if work_dir.exists():
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
            except Exception:
                pass


def clean_voice_track(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    source_target: str,
    mode: str = "denoise",
    quality: str = "auto",
    output_format: str = "wav",
    output_title: str = "",
    post_filter: bool = False,
    send_to_user: bool = True,
    timestamp: int | None = None,
    protected_media_extensions: set[str],
) -> dict[str, Any]:
    effective_ts = int(timestamp or time.time())
    normalized_mode = service._normalize_voice_clean_mode(mode)
    normalized_quality = service._normalize_voice_clean_quality(quality)
    normalized_format = service._normalize_media_output_format(output_format)
    if normalized_format not in {"wav", "flac", "mp3"}:
        return {
            "ok": False,
            "generated": None,
            "error": "unsupported_voice_clean_output_format",
            "followup_context": "你刚刚想输出净化后的人声文件，但当前只支持 wav、flac 或 mp3。",
        }

    source = service._resolve_media_source(
        profile_user_id=profile_user_id,
        session_id=session_id,
        target=source_target,
    )
    if source is None:
        return {
            "ok": False,
            "generated": None,
            "error": "source_not_found",
            "followup_context": "你刚刚想净化一段人声，但没有找到明确的来源。请自然向用户确认要处理哪一个附件或生成文件。",
        }

    source_path = Path(source.get("absolute_path") or "")
    if not source_path.exists() or not source_path.is_file():
        return {
            "ok": False,
            "generated": None,
            "error": "source_file_missing",
            "followup_context": "你刚刚想净化一段人声，但本地来源文件不存在。请自然告诉用户这个文件暂时无法处理。",
        }

    input_ext = source_path.suffix.lower().lstrip(".")
    if input_ext in protected_media_extensions:
        return {
            "ok": False,
            "generated": None,
            "error": "protected_media_format",
            "followup_context": (
                f"你刚刚识别到 {input_ext} 这类平台加密或专有缓存格式。"
                "不要尝试解密或绕过保护；请自然告诉用户可以提供普通 mp3、flac、wav、m4a 等非加密源文件。"
            ),
        }

    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        return {
            "ok": False,
            "generated": None,
            "error": "ffmpeg_not_found",
            "followup_context": "你刚刚想净化人声，但本机没有找到 ffmpeg。请自然提醒用户先安装 ffmpeg 或配置 PATH。",
        }

    deepfilter = service._resolve_deepfilternet_runner()
    use_ai = normalized_quality == "ai" or (normalized_quality == "auto" and deepfilter is not None)
    if normalized_quality == "ai" and deepfilter is None:
        return {
            "ok": False,
            "generated": None,
            "error": "deepfilternet_not_found",
            "followup_context": (
                "你刚刚明确想用 AI 人声净化，但本机没有找到可用的 DeepFilterNet 环境。"
                "请自然提醒用户先安装 deepfilternet，再重新尝试。"
            ),
        }

    base_name = Path(str(source.get("title") or source_path.stem)).stem.strip() or source_path.stem or "voice"
    clean_label = {
        "denoise": "降噪净化",
        "dereverb": "去混响净化",
        "deecho": "去回声净化",
        "voice_focus": "人声聚焦净化",
    }.get(normalized_mode, "净化")
    clean_title = service._normalize_title(output_title) or f"{base_name}_{clean_label}"
    work_dir = (
        service.work_dir
        / "_voice_clean_tmp"
        / service._safe_filename(profile_user_id or "profile")[:48]
        / service._safe_filename(session_id or "session")[:48]
        / str(effective_ts)
    )
    prepared_input_path: Path | None = None
    enhanced_wav_path: Path | None = None
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        prepared_input_path = work_dir / f"{service._safe_filename(base_name)[:48] or 'source'}_clean_input.wav"
        prepare_command = [
            str(ffmpeg_path),
            "-y",
            "-i",
            str(source_path),
            "-vn",
            "-codec:a",
            "pcm_s16le",
            "-ar",
            "48000",
            "-ac",
            "1",
            str(prepared_input_path),
        ]
        prepare_result = subprocess.run(
            prepare_command,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if prepare_result.returncode != 0 or not prepared_input_path.exists():
            error_text = (prepare_result.stderr or prepare_result.stdout or "ffmpeg 预处理失败").strip()[:500]
            return {
                "ok": False,
                "generated": None,
                "error": error_text,
                "followup_context": f"你刚刚想净化人声，但前置音频准备失败：{error_text[:180]}。请自然告诉用户失败原因。",
            }

        if use_ai and deepfilter is not None:
            try:
                enhanced_wav_path = service._run_deepfilternet_cleaning(
                    runner=deepfilter,
                    prepared_input_path=prepared_input_path,
                    output_root=work_dir / "deepfilter_out",
                    post_filter=bool(post_filter or normalized_mode in {"dereverb", "deecho", "voice_focus"}),
                )
                backend_used = "deepfilternet"
            except Exception as exc:
                if normalized_quality == "ai":
                    error_text = service._summarize_voice_clean_error(str(exc))
                    return {
                        "ok": False,
                        "generated": None,
                        "error": error_text,
                        "followup_context": f"你刚刚想用 AI 人声净化，但执行失败：{error_text[:220]}。请自然告诉用户失败原因。",
                    }
                enhanced_wav_path = None
                backend_used = "basic_ffmpeg"
            else:
                backend_used = "deepfilternet"
        else:
            backend_used = "basic_ffmpeg"

        if enhanced_wav_path is None:
            enhanced_wav_path = work_dir / "basic_clean.wav"
            clean_command = [
                str(ffmpeg_path),
                "-y",
                "-i",
                str(prepared_input_path),
                "-vn",
                "-codec:a",
                "pcm_s16le",
                "-ar",
                "48000",
                "-ac",
                "1",
            ]
            basic_filters = service._build_basic_voice_clean_filter_chain(
                mode=normalized_mode,
                post_filter=bool(post_filter),
            )
            if basic_filters:
                clean_command.extend(["-filter:a", ",".join(basic_filters)])
            clean_command.append(str(enhanced_wav_path))
            clean_result = subprocess.run(
                clean_command,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            if clean_result.returncode != 0 or not enhanced_wav_path.exists():
                error_text = (clean_result.stderr or clean_result.stdout or "ffmpeg 基础净化失败").strip()[:500]
                return {
                    "ok": False,
                    "generated": None,
                    "error": error_text,
                    "followup_context": f"你刚刚想净化人声，但基础净化也失败了：{error_text[:180]}。请自然告诉用户失败原因。",
                }

        output_path = service._build_output_path(
            profile_user_id=profile_user_id,
            session_id=session_id,
            title=clean_title,
            output_format=normalized_format,
            timestamp=effective_ts,
        )
        service._render_clean_voice_output(
            cleaned_source_path=enhanced_wav_path,
            output_path=output_path,
            output_format=normalized_format,
            ffmpeg_path=str(ffmpeg_path),
        )

        source_ids = [str(source.get("source_id") or "").strip()]
        source_ids.extend(str(item or "").strip() for item in list(source.get("extra_source_ids") or []))
        source_ids = [item for item in source_ids if item]
        source_media = source.get("media_info") if isinstance(source.get("media_info"), dict) else {}
        content_card = {
            "type": "voice_cleaning",
            "summary": (
                f"基于 {source.get('handle') or source.get('title') or '媒体文件'} 做了{clean_label}，"
                f"使用 {'AI 净化' if backend_used == 'deepfilternet' else '基础净化'}。"
            ),
            "source": {
                "source_type": str(source.get("source_type") or "").strip(),
                "source_id": str(source.get("source_id") or "").strip(),
                "handle": str(source.get("handle") or "").strip(),
                "title": str(source.get("title") or "").strip(),
                "input_ext": input_ext,
            },
            "voice_cleaning": {
                "mode": normalized_mode,
                "quality_requested": normalized_quality,
                "backend_used": backend_used,
                "output_format": normalized_format,
                "post_filter": bool(post_filter),
            },
        }
        content_card["media_info"] = build_generated_media_info_projection(
            service,
            output_path=output_path,
            output_format=normalized_format,
            source=source,
            hints={
                "duration_seconds": source_media.get("duration_seconds"),
                "sample_rate": 48000,
                "channels": 1,
                "file_size": output_path.stat().st_size,
            },
        )
        generated = service.store.add_generated_file(
            profile_user_id=profile_user_id,
            session_id=session_id,
            output_title=clean_title,
            output_format=normalized_format,
            storage_relpath=service._storage_relpath(output_path),
            mime_type=service._mime_type_for_format(normalized_format),
            file_ext=normalized_format,
            file_size=output_path.stat().st_size,
            source_ids=source_ids,
            content_card=content_card,
            summary=str(content_card.get("summary") or "").strip(),
            created_by_tool="clean_voice_track",
            delivery_status="pending" if send_to_user else "not_requested",
            timestamp=effective_ts,
        )
        generated["absolute_path"] = str(service.absolute_path(generated))
        return {
            "ok": True,
            "generated": generated,
            "send_to_user": bool(send_to_user),
            "followup_context": service._build_voice_clean_followup(
                generated=generated,
                source=source,
                mode=normalized_mode,
                backend_used=backend_used,
                send_to_user=send_to_user,
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "generated": None,
            "error": str(exc),
            "followup_context": f"你刚刚做人声净化时失败了：{str(exc)[:180]}。请自然告诉用户失败原因。",
        }
    finally:
        if work_dir.exists():
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
            except Exception:
                pass


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
            prepare_result = service._prepare_voice_dataset_input(
                ffmpeg_path=str(ffmpeg_path),
                source_path=source_path,
                prepared_path=prepared_path,
                target_sr=int(options["target_sr"]),
                channels=1 if bool(options["mono"]) else 2,
                clean_first=bool(clean_first),
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


def transcribe_media(
    service: Any,
    *,
    profile_user_id: str,
    session_id: str,
    source_targets: list[str] | tuple[str, ...] | str,
    output_format: str = "md",
    output_title: str = "",
    language: str = "zh",
    with_timestamps: bool = True,
    merge_outputs: bool = True,
    model_size: str = "small",
    device: str = "auto",
    compute_type: str = "auto",
    vad_filter: bool = True,
    send_to_user: bool = True,
    timestamp: int | None = None,
    transcript_output_formats: set[str],
) -> dict[str, Any]:
    effective_ts = int(timestamp or time.time())
    normalized_format = service._normalize_transcript_output_format(output_format)
    if normalized_format not in transcript_output_formats:
        return {
            "ok": False,
            "generated": None,
            "generated_files": [],
            "error": "unsupported_transcript_output_format",
            "followup_context": "你刚刚想生成转写稿，但当前只支持 md、txt、srt、vtt 或 json。",
        }

    if importlib.util.find_spec("faster_whisper") is None:
        return {
            "ok": False,
            "generated": None,
            "generated_files": [],
            "error": "faster_whisper_not_found",
            "followup_context": (
                "你刚刚想给音频/视频生成文字稿，但本机没有找到 faster-whisper。"
                "请自然提醒用户先安装转写环境，例如安装 requirements-ml.txt 里的 faster-whisper。"
            ),
        }

    normalized_targets = service._normalize_targets(source_targets)
    if not normalized_targets:
        return {
            "ok": False,
            "generated": None,
            "generated_files": [],
            "error": "missing_sources",
            "followup_context": "你刚刚想转写音频/视频，但没有指定来源。请自然向用户确认要转写哪几个文件。",
        }

    sources, unresolved, missing_files, protected = service._resolve_media_sources_for_batch(
        profile_user_id=profile_user_id,
        session_id=session_id,
        source_targets=normalized_targets,
    )
    if not sources:
        reason = "、".join([*unresolved[:3], *missing_files[:3], *protected[:3]]) or "没有可转写的来源"
        return {
            "ok": False,
            "generated": None,
            "generated_files": [],
            "error": "no_usable_sources",
            "unresolved": unresolved,
            "missing_files": missing_files,
            "protected": protected,
            "followup_context": (
                f"你刚刚想转写音频/视频，但没有找到可用的普通媒体来源：{reason}。"
                "请自然告诉用户需要提供普通音频或带音轨视频。"
            ),
        }

    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        return {
            "ok": False,
            "generated": None,
            "generated_files": [],
            "error": "ffmpeg_not_found",
            "followup_context": "你刚刚想转写音频/视频，但本机没有找到 ffmpeg。请自然提醒用户先安装 ffmpeg 或配置 PATH。",
        }

    normalized_model_size = service._normalize_whisper_model_size(model_size)
    normalized_device = service._normalize_whisper_device(device)
    normalized_compute_type = service._normalize_whisper_compute_type(compute_type)
    normalized_language = service._normalize_transcript_language(language)
    work_dir = (
        service.work_dir
        / "_transcribe_tmp"
        / service._safe_filename(profile_user_id or "profile")[:48]
        / service._safe_filename(session_id or "session")[:48]
        / str(effective_ts)
    )
    transcripts: list[dict[str, Any]] = []
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        model = service._load_faster_whisper_model(
            model_size=normalized_model_size,
            device=normalized_device,
            compute_type=normalized_compute_type,
        )
        for index, source in enumerate(sources, start=1):
            source_path = Path(source.get("absolute_path") or "")
            prepared_path = work_dir / f"source_{index:02d}_transcribe.wav"
            prepared = service._prepare_transcription_input(
                ffmpeg_path=str(ffmpeg_path),
                source_path=source_path,
                prepared_path=prepared_path,
            )
            if not prepared.get("ok"):
                transcripts.append(
                    {
                        "source_index": index,
                        "source": service._transcript_source_card(source),
                        "status": "failed",
                        "error": str(prepared.get("error") or "")[:240],
                        "segments": [],
                        "text": "",
                    }
                )
                continue
            transcript = service._transcribe_prepared_audio(
                model=model,
                audio_path=prepared_path,
                source=source,
                source_index=index,
                language=normalized_language,
                vad_filter=bool(vad_filter),
            )
            transcripts.append(transcript)

        ready_transcripts = [item for item in transcripts if item.get("status") == "ready"]
        if not ready_transcripts:
            errors = [
                f"{item.get('source', {}).get('handle') or item.get('source_index')}: {item.get('error')}"
                for item in transcripts
                if item.get("status") == "failed"
            ]
            return {
                "ok": False,
                "generated": None,
                "generated_files": [],
                "error": "no_transcripts_created",
                "followup_context": (
                    "你刚刚想生成转写稿，但没有任何来源转写成功。"
                    + (f"失败信息：{'; '.join(errors[:3])}。" if errors else "")
                    + "请自然告诉用户可以先净化人声、换更清晰的音频，或检查转写环境。"
                ),
            }

        if merge_outputs:
            title = service._normalize_title(output_title) or service._infer_transcript_title(
                sources=sources,
                output_format=normalized_format,
                merged=True,
            )
            generated = service._store_transcript_output(
                profile_user_id=profile_user_id,
                session_id=session_id,
                title=title,
                output_format=normalized_format,
                transcripts=ready_transcripts,
                all_transcripts=transcripts,
                language=normalized_language,
                with_timestamps=with_timestamps,
                merge_outputs=True,
                model_size=normalized_model_size,
                device=normalized_device,
                compute_type=normalized_compute_type,
                send_to_user=send_to_user,
                timestamp=effective_ts,
            )
            return {
                "ok": True,
                "generated": generated,
                "generated_files": [generated],
                "send_to_user": bool(send_to_user),
                "unresolved": unresolved,
                "missing_files": missing_files,
                "protected": protected,
                "followup_context": service._build_transcribe_followup(
                    generated_files=[generated],
                    transcripts=transcripts,
                    output_format=normalized_format,
                    merge_outputs=True,
                    send_to_user=send_to_user,
                ),
            }

        generated_files: list[dict[str, Any]] = []
        for transcript in ready_transcripts:
            source_card = transcript.get("source") if isinstance(transcript.get("source"), dict) else {}
            title = service._normalize_title(output_title)
            if title and len(ready_transcripts) > 1:
                title = f"{title}_{source_card.get('handle') or transcript.get('source_index')}"
            if not title:
                title = service._infer_transcript_title(
                    sources=[source_card],
                    output_format=normalized_format,
                    merged=False,
                )
            generated_files.append(
                service._store_transcript_output(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    title=title,
                    output_format=normalized_format,
                    transcripts=[transcript],
                    all_transcripts=[transcript],
                    language=normalized_language,
                    with_timestamps=with_timestamps,
                    merge_outputs=False,
                    model_size=normalized_model_size,
                    device=normalized_device,
                    compute_type=normalized_compute_type,
                    send_to_user=send_to_user,
                    timestamp=effective_ts,
                )
            )
        return {
            "ok": True,
            "generated": generated_files[0] if generated_files else None,
            "generated_files": generated_files,
            "send_to_user": bool(send_to_user),
            "unresolved": unresolved,
            "missing_files": missing_files,
            "protected": protected,
            "followup_context": service._build_transcribe_followup(
                generated_files=generated_files,
                transcripts=transcripts,
                output_format=normalized_format,
                merge_outputs=False,
                send_to_user=send_to_user,
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "generated": None,
            "generated_files": [],
            "error": str(exc),
            "followup_context": f"你刚刚生成音频/视频文字稿时失败了：{str(exc)[:180]}。请自然告诉用户失败原因。",
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
        return {
            "ok": False,
            "source": source,
            "media_info": None,
            "error": "source_file_missing",
            "followup_context": "你刚刚想查看媒体文件信息，但本地来源文件不存在。请自然告诉用户这个文件暂时无法读取。",
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
