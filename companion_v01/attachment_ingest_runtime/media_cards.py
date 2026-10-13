"""Audio and video metadata probing and material-card projection."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


class AttachmentMediaCardsMixin:
    """Methods use the state owned by AttachmentIngestService."""

    def _build_media_card(
        self,
        *,
        source_path: Path,
        title: str,
        suffix: str,
        mime_type: str,
        file_size: int,
    ) -> dict[str, Any]:
        media_info = self._probe_media_info(source_path)
        file_kind = suffix.lstrip(".") or (
            "audio" if mime_type.startswith("audio/") else "video" if mime_type.startswith("video/") else "media"
        )
        detail: dict[str, Any] = {
            "summary": f"媒体文件 {title}，格式 {file_kind}，约 {file_size} bytes。",
            "file_kind": file_kind,
            "mime_type": mime_type,
            "file_size": file_size,
            "text_preview": "",
        }
        hint_parts = [detail["summary"]]
        if media_info:
            detail["media_info"] = media_info
            duration = self._format_duration_label(media_info.get("duration_seconds"))
            if duration:
                hint_parts.append(f"时长 {duration}")
            audio = media_info.get("audio") if isinstance(media_info.get("audio"), dict) else {}
            if audio:
                audio_bits = [
                    str(audio.get("codec") or "未知音频编码"),
                    f"{audio.get('sample_rate')}Hz" if audio.get("sample_rate") else "",
                    f"{audio.get('channels')}声道" if audio.get("channels") else "",
                ]
                hint_parts.append("音频 " + "，".join(bit for bit in audio_bits if bit))
            if mime_type.startswith("audio/") or file_kind == "audio":
                detail["transcribable"] = True
            video = media_info.get("video") if isinstance(media_info.get("video"), dict) else {}
            if video:
                size = ""
                if video.get("width") and video.get("height"):
                    size = f"{video.get('width')}x{video.get('height')}"
                fps = f"{video.get('fps'):g}fps" if isinstance(video.get("fps"), (int, float)) else ""
                hint_parts.append("视频 " + "，".join(bit for bit in (video.get("codec"), size, fps) if bit))
        else:
            detail["media_probe_status"] = "unavailable"
            hint_parts.append("暂未读取到媒体规格")
        return {
            "summary_title": title,
            "short_hint": "；".join(hint_parts)[:240],
            "detail": detail,
        }

    def _probe_media_info(self, source_path: Path) -> dict[str, Any] | None:
        ffprobe_path = shutil.which("ffprobe")
        if not ffprobe_path:
            return None
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
            completed = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        except Exception:
            return None
        if completed.returncode != 0:
            return None
        try:
            parsed = json.loads(str(completed.stdout or "{}"))
        except Exception:
            return None
        if not isinstance(parsed, dict):
            return None
        return self._normalize_media_info(parsed, source_path=source_path)

    def _normalize_media_info(self, data: dict[str, Any], *, source_path: Path) -> dict[str, Any]:
        format_info = data.get("format") if isinstance(data.get("format"), dict) else {}
        streams = data.get("streams") if isinstance(data.get("streams"), list) else []
        audio_streams = []
        video_streams = []
        for stream in streams:
            if not isinstance(stream, dict):
                continue
            kind = str(stream.get("codec_type") or "").strip().lower()
            if kind == "audio":
                audio_streams.append(
                    {
                        "codec": str(stream.get("codec_name") or "").strip(),
                        "sample_rate": self._safe_int(stream.get("sample_rate")),
                        "channels": self._safe_int(stream.get("channels")),
                        "bit_rate": self._safe_int(stream.get("bit_rate")),
                        "duration_seconds": self._safe_float(stream.get("duration")),
                    }
                )
            elif kind == "video":
                video_streams.append(
                    {
                        "codec": str(stream.get("codec_name") or "").strip(),
                        "width": self._safe_int(stream.get("width")),
                        "height": self._safe_int(stream.get("height")),
                        "fps": self._parse_frame_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate")),
                        "bit_rate": self._safe_int(stream.get("bit_rate")),
                        "duration_seconds": self._safe_float(stream.get("duration")),
                    }
                )
        duration = self._safe_float(format_info.get("duration"))
        if duration is None:
            duration_values = [
                item.get("duration_seconds")
                for item in [*audio_streams, *video_streams]
                if isinstance(item.get("duration_seconds"), (int, float))
            ]
            duration = max(duration_values) if duration_values else None
        return {
            "format_name": str(format_info.get("format_name") or source_path.suffix.lower().lstrip(".")).strip(),
            "duration_seconds": round(float(duration), 3) if duration is not None else None,
            "file_size": self._safe_int(format_info.get("size")),
            "bit_rate": self._safe_int(format_info.get("bit_rate")),
            "audio": audio_streams[0] if audio_streams else None,
            "video": video_streams[0] if video_streams else None,
            "audio_streams": audio_streams[:4],
            "video_streams": video_streams[:4],
        }

    def _parse_frame_rate(self, value: Any) -> float | None:
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

    def _format_duration_label(self, value: Any) -> str:
        if not isinstance(value, (int, float)):
            return ""
        seconds = max(0, int(round(float(value))))
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"
