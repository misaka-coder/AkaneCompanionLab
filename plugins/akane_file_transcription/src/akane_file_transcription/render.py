"""Transcript rendering authority, independent of storage and delivery."""

from __future__ import annotations

import json


FORMATS = {
    "md": "text/markdown",
    "txt": "text/plain",
    "json": "application/json",
    "srt": "application/x-subrip",
    "vtt": "text/vtt",
}


def timestamp(value, *, subtitle=False, vtt=False):
    total = max(0, round(float(value) * 1000))
    seconds, milliseconds = divmod(total, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if subtitle:
        separator = "." if vtt else ","
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{milliseconds:03d}"
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def render(transcripts, *, output_format, title, with_timestamps=True):
    if output_format not in FORMATS:
        raise ValueError("asr_output_format_invalid")
    if output_format == "json":
        return (
            json.dumps({"title": title, "transcripts": transcripts}, ensure_ascii=False, indent=2, allow_nan=False)
            + "\n"
        )
    lines = ["WEBVTT", ""] if output_format == "vtt" else ([f"# {title}", ""] if output_format == "md" else [])
    offset, cue = 0.0, 1
    for index, transcript in enumerate(transcripts, 1):
        source = transcript.get("source", {})
        label = source.get("handle") or source.get("title") or f"source_{index}"
        segments = transcript.get("segments", [])
        if output_format in ("srt", "vtt"):
            if output_format == "vtt":
                lines.extend([f"NOTE {label}", ""])
            for segment in segments:
                start, end = offset + segment["start"], offset + segment["end"]
                if output_format == "srt":
                    lines.append(str(cue))
                lines.extend(
                    [
                        f"{timestamp(start, subtitle=True, vtt=output_format == 'vtt')} --> {timestamp(end, subtitle=True, vtt=output_format == 'vtt')}",
                        segment["text"].strip(),
                        "",
                    ]
                )
                cue += 1
            offset += float(transcript.get("duration_seconds") or max((s["end"] for s in segments), default=0))
        else:
            lines.extend([f"## {label}" if output_format == "md" else f"【{label}】", ""])
            if with_timestamps:
                prefix = "- " if output_format == "md" else ""
                lines.extend(f"{prefix}[{timestamp(s['start'])} - {timestamp(s['end'])}] {s['text']}" for s in segments)
            else:
                lines.append(transcript.get("text", "").strip())
            lines.append("")
    return "\n".join(lines).strip() + "\n"
