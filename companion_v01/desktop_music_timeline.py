from __future__ import annotations

import importlib.util
import json
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import config


TIMELINE_BUILD_VERSION = "music_timeline_vocal_v1"
TIMELINE_QUALITY_VOCAL = "vocal_asr"
TIMELINE_QUALITY_MIXED = "mixed_asr"


class DesktopMusicTimelineService:
    """Session-scoped timeline index for desktop pet audio playback.

    This is deliberately a projection helper: it does not write memory and it
    does not change QQ/Web attachment semantics.
    """

    def __init__(
        self,
        *,
        store: Any,
        generated_file_service: Any,
        background_tasks: Any | None = None,
    ) -> None:
        self.store = store
        self.generated_file_service = generated_file_service
        self.background_tasks = background_tasks

    def prepare_timeline(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        activity: dict[str, Any] | None,
    ) -> dict[str, Any]:
        source = self._resolve_activity_source(
            profile_user_id=profile_user_id,
            session_id=session_id,
            activity=activity,
        )
        if source is None:
            return {"ok": False, "error": "source_not_found", "timeline": None}

        source_id = source["source_id"]
        source_path = Path(source["absolute_path"])
        source_size, source_mtime_ns = self._source_fingerprint(source_path)
        now_ts = int(time.time())
        existing = self.store.get_desktop_music_timeline_by_source(
            profile_user_id=profile_user_id,
            session_id=session_id,
            source_id=source_id,
        )
        if self._timeline_matches_source(existing, source_size=source_size, source_mtime_ns=source_mtime_ns):
            status = str(existing.get("status") or "")
            recently_touched = now_ts - int(existing.get("updated_at") or 0) < 600
            if status == "ready" or (status in {"pending", "processing"} and recently_touched):
                return {"ok": True, "timeline": self._public_timeline(existing), "scheduled": False}

        timeline = self.store.upsert_desktop_music_timeline(
            profile_user_id=profile_user_id,
            session_id=session_id,
            source_id=source_id,
            source_kind=self._timeline_source_kind(source, quality="pending"),
            source_handle=str(source.get("handle") or source_id),
            title=str(source.get("title") or source_id),
            status="pending",
            segments=[],
            rolling_summary="",
            ready_until_seconds=0,
            error_message="",
            source_size=source_size,
            source_mtime_ns=source_mtime_ns,
            timestamp=now_ts,
        )

        if bool(getattr(config, "DESKTOP_TIMELINE_REUSE_TRANSCRIPT", False)):
            existing_transcript = self._load_existing_transcript_segments(
                profile_user_id=profile_user_id,
                session_id=session_id,
                source=source,
            )
            if existing_transcript.get("segments"):
                ready = self._mark_timeline_ready(
                    timeline_id=str(timeline.get("timeline_id") or ""),
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    segments=list(existing_transcript["segments"]),
                    transcript_generated=existing_transcript.get("generated"),
                )
                return {"ok": True, "timeline": self._public_timeline(ready or timeline), "scheduled": False}

        scheduled = self._schedule_timeline_build(
            profile_user_id=profile_user_id,
            session_id=session_id,
            timeline_id=str(timeline.get("timeline_id") or ""),
            source=source,
        )
        return {"ok": True, "timeline": self._public_timeline(timeline), "scheduled": scheduled}

    def build_prompt_projection(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        activity: dict[str, Any] | None,
    ) -> str:
        if not isinstance(activity, dict):
            return ""
        timeline = self._find_timeline_for_activity(
            profile_user_id=profile_user_id,
            session_id=session_id,
            activity=activity,
        )
        if not timeline:
            return "\n".join(
                [
                    "【当前音乐位置】",
                    "- 这首歌的歌词线索还没准备好。",
                    "- 你现在只知道音频标题、播放状态和进度；不要编造歌词内容。",
                ]
            )

        status = str(timeline.get("status") or "").strip().lower()
        title = str(timeline.get("title") or activity.get("title") or "当前音频").strip()
        if status in {"pending", "processing"}:
            return "\n".join(
                [
                    "【当前音乐位置】",
                    f"- 正在播放：{title}",
                    "- 歌词线索还没准备好，当前还不能确定唱到哪一句。",
                    "- 不要编造歌词内容；如果主人问起，可以自然说现在只能感知播放进度。",
                ]
            )
        if status == "failed":
            return "\n".join(
                [
                    "【当前音乐位置】",
                    f"- 正在播放：{title}",
                    "- 歌词线索暂时没有准备好。",
                    "- 不要编造歌词内容。",
                ]
            )

        progress = self._safe_seconds(activity.get("progress_seconds"))
        ready_until = self._safe_seconds(timeline.get("ready_until_seconds"))
        segments = list(timeline.get("segments") or [])
        nearby = self._nearby_segments(segments, progress_seconds=progress)
        quality = self._timeline_quality(timeline)
        lines = [
            "【当前音乐位置】",
            f"- 正在播放：{title}",
        ]
        if progress:
            lines.append(f"- 当前进度：{self._format_time(progress)}。")
        if progress and ready_until and progress > ready_until + 5:
            lines.append("- 当前播放进度已经超过可用歌词线索范围，不要补编后续歌词。")

        if not nearby:
            lines.append("- 当前进度附近暂无可用歌词线索，不要编造歌词内容。")
            return "\n".join(lines)

        lines.append("- 此刻附近隐约听到的词句：")
        for segment in nearby:
            text = " ".join(str(segment.get("text") or "").split())[:160]
            if text:
                lines.append(f"  - “{text}”")
        lines.append("- 这些只是大致听到的人声，不保证逐字完全准确。")
        if quality == TIMELINE_QUALITY_MIXED:
            lines.append("- 当前词句可能受伴奏影响更明显，如果不确定就自然说听得不太清楚。")
        lines.append("- 请把这些当作你和主人一起听歌时自然注意到的内容，不要说自己在看文件。")
        return "\n".join(lines)

    def _schedule_timeline_build(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        timeline_id: str,
        source: dict[str, Any],
    ) -> bool:
        if not self.background_tasks:
            return False
        try:
            self.background_tasks.submit(
                lane="timeline",
                name=f"desktop_music_timeline:{source.get('handle') or source.get('source_id')}",
                fn=self._build_timeline_from_asr,
                kwargs={
                    "profile_user_id": profile_user_id,
                    "session_id": session_id,
                    "timeline_id": timeline_id,
                    "source": dict(source),
                },
            )
            return True
        except Exception:
            return False

    def _build_timeline_from_asr(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        timeline_id: str,
        source: dict[str, Any],
    ) -> None:
        self.store.update_desktop_music_timeline(
            profile_user_id=profile_user_id,
            session_id=session_id,
            timeline_id=timeline_id,
            status="processing",
            error_message="",
        )
        try:
            transcript = self._transcribe_source(source)
            if transcript.get("status") != "ready":
                self._mark_timeline_failed(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    timeline_id=timeline_id,
                    error_message=str(transcript.get("error") or "转写失败"),
                )
                return
            segments = self._normalize_segments(transcript.get("segments"))
            self._mark_timeline_ready(
                timeline_id=timeline_id,
                profile_user_id=profile_user_id,
                session_id=session_id,
                segments=segments,
                quality=str(transcript.get("quality") or TIMELINE_QUALITY_MIXED),
            )
        except Exception as exc:
            self._mark_timeline_failed(
                profile_user_id=profile_user_id,
                session_id=session_id,
                timeline_id=timeline_id,
                error_message=str(exc)[:240],
            )

    def _transcribe_source(self, source: dict[str, Any]) -> dict[str, Any]:
        if importlib.util.find_spec("faster_whisper") is None:
            return {"status": "failed", "error": "faster_whisper_not_found"}
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            return {"status": "failed", "error": "ffmpeg_not_found"}

        source_path = Path(source.get("absolute_path") or "")
        with tempfile.TemporaryDirectory(prefix="akane_desktop_timeline_") as tmp:
            work_dir = Path(tmp)
            if self._source_is_instrumental_stem(source):
                return {"status": "failed", "error": "instrumental_has_no_lyrics", "segments": []}

            if self._source_is_vocal_stem(source):
                return self._transcribe_audio_path(
                    audio_path=source_path,
                    source=source,
                    ffmpeg_path=str(ffmpeg_path),
                    quality=TIMELINE_QUALITY_VOCAL,
                )

            vocals_path = self._separate_vocals_to_cache(source_path=source_path, work_dir=work_dir)
            if vocals_path is not None:
                transcript = self._transcribe_audio_path(
                    audio_path=vocals_path,
                    source=source,
                    ffmpeg_path=str(ffmpeg_path),
                    quality=TIMELINE_QUALITY_VOCAL,
                )
                if transcript.get("status") == "ready" and self._transcript_has_segments(transcript):
                    return transcript

            return self._transcribe_audio_path(
                audio_path=source_path,
                source=source,
                ffmpeg_path=str(ffmpeg_path),
                quality=TIMELINE_QUALITY_MIXED,
            )

    def _separate_vocals_to_cache(self, *, source_path: Path, work_dir: Path) -> Path | None:
        separation_output_dir = work_dir / "vocal_cache"
        try:
            if importlib.util.find_spec("demucs") is not None:
                stems = self.generated_file_service._separate_audio_with_demucs_module(
                    source_path=source_path,
                    output_root=separation_output_dir,
                )
            else:
                demucs_command = self.generated_file_service._resolve_demucs_command()
                if demucs_command is None:
                    return None
                completed = self._run_demucs_command(
                    demucs_command=demucs_command,
                    source_path=source_path,
                    output_root=separation_output_dir,
                )
                if not completed:
                    return None
                stems = self.generated_file_service._collect_demucs_stems(separation_output_dir)
            vocals_path = stems.get("vocals")
            if vocals_path and Path(vocals_path).exists():
                return Path(vocals_path)
        except Exception:
            return None
        return None

    def _run_demucs_command(self, *, demucs_command: list[str], source_path: Path, output_root: Path) -> bool:
        import subprocess

        command = [
            *demucs_command,
            "--two-stems",
            "vocals",
            "-o",
            str(output_root),
            str(source_path),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=1800,
                check=False,
            )
        except Exception:
            return False
        return completed.returncode == 0

    def _transcribe_audio_path(
        self,
        *,
        audio_path: Path,
        source: dict[str, Any],
        ffmpeg_path: str,
        quality: str,
    ) -> dict[str, Any]:
        prepared_path = audio_path.parent / f"prepared_{quality}.wav"
        prepared = self.generated_file_service._prepare_transcription_input(
            ffmpeg_path=str(ffmpeg_path),
            source_path=audio_path,
            prepared_path=prepared_path,
        )
        if not prepared.get("ok"):
            return {"status": "failed", "error": str(prepared.get("error") or "audio_prepare_failed")[:300], "quality": quality}

        model_size = self.generated_file_service._normalize_whisper_model_size(
            getattr(config, "DESKTOP_TIMELINE_WHISPER_MODEL_SIZE", getattr(config, "WHISPER_MODEL_SIZE", "small"))
        )
        device = self.generated_file_service._normalize_whisper_device(
            getattr(config, "DESKTOP_TIMELINE_WHISPER_DEVICE", getattr(config, "WHISPER_DEVICE", "auto"))
        )
        compute_type = self.generated_file_service._normalize_whisper_compute_type(
            getattr(config, "DESKTOP_TIMELINE_WHISPER_COMPUTE_TYPE", getattr(config, "WHISPER_COMPUTE_TYPE", "auto"))
        )
        language = self.generated_file_service._normalize_transcript_language(
            getattr(config, "DESKTOP_TIMELINE_LANGUAGE", getattr(config, "ASR_LANGUAGE", "zh"))
        )
        model = self.generated_file_service._load_faster_whisper_model(
            model_size=model_size,
            device=device,
            compute_type=compute_type,
        )
        transcript = self.generated_file_service._transcribe_prepared_audio(
            model=model,
            audio_path=prepared_path,
            source=source,
            source_index=1,
            language=language,
            vad_filter=bool(getattr(config, "DESKTOP_TIMELINE_VAD_FILTER", False)),
        )
        transcript["quality"] = quality
        return transcript

    def _store_full_transcript(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source: dict[str, Any],
        transcript: dict[str, Any],
    ) -> dict[str, Any] | None:
        segments = self._normalize_segments(transcript.get("segments"))
        if not segments:
            return None
        title = Path(str(source.get("title") or source.get("handle") or "桌宠音频")).stem
        try:
            return self.generated_file_service._store_transcript_output(
                profile_user_id=profile_user_id,
                session_id=session_id,
                title=f"{title}_桌宠时间轴转写",
                output_format="json",
                transcripts=[dict(transcript, segments=segments)],
                all_transcripts=[dict(transcript, segments=segments)],
                language=str(transcript.get("language") or ""),
                with_timestamps=True,
                merge_outputs=True,
                model_size=str(getattr(config, "DESKTOP_TIMELINE_WHISPER_MODEL_SIZE", "small")),
                device=str(getattr(config, "DESKTOP_TIMELINE_WHISPER_DEVICE", "auto")),
                compute_type=str(getattr(config, "DESKTOP_TIMELINE_WHISPER_COMPUTE_TYPE", "auto")),
                send_to_user=False,
                timestamp=int(time.time()),
            )
        except Exception:
            return None

    def _mark_timeline_ready(
        self,
        *,
        timeline_id: str,
        profile_user_id: str,
        session_id: str,
        segments: list[dict[str, Any]],
        transcript_generated: dict[str, Any] | None = None,
        quality: str = TIMELINE_QUALITY_MIXED,
    ) -> dict[str, Any] | None:
        normalized_segments = self._normalize_segments(segments)
        ready_until = self._estimate_ready_until(normalized_segments)
        existing = self.store.get_desktop_music_timeline(timeline_id=timeline_id)
        source_kind = self._source_kind_with_quality(existing, quality=quality)
        return self.store.update_desktop_music_timeline(
            profile_user_id=profile_user_id,
            session_id=session_id,
            timeline_id=timeline_id,
            source_kind=source_kind,
            status="ready",
            segments=normalized_segments,
            rolling_summary=self._build_rolling_summary(normalized_segments),
            ready_until_seconds=ready_until,
            transcript_generated_id=str((transcript_generated or {}).get("generated_id") or ""),
            transcript_generated_handle=str((transcript_generated or {}).get("generated_handle") or ""),
            error_message="",
        )

    def _mark_timeline_failed(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        timeline_id: str,
        error_message: str,
    ) -> None:
        self.store.update_desktop_music_timeline(
            profile_user_id=profile_user_id,
            session_id=session_id,
            timeline_id=timeline_id,
            status="failed",
            error_message=str(error_message or "timeline_failed")[:240],
        )

    def _load_existing_transcript_segments(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source: dict[str, Any],
    ) -> dict[str, Any]:
        source_keys = {
            str(source.get("source_id") or "").strip(),
            str(source.get("handle") or "").strip(),
            str(source.get("internal_source_id") or "").strip(),
        }
        source_keys = {item for item in source_keys if item}
        try:
            generated_files = self.store.list_generated_files(
                profile_user_id=profile_user_id,
                session_id=session_id,
                statuses=["ready"],
                limit=50,
            )
        except Exception:
            return {}

        for item in generated_files:
            if str(item.get("created_by_tool") or "") != "transcribe_media":
                continue
            item_source_ids = {str(value or "").strip() for value in list(item.get("source_ids") or [])}
            if item_source_ids and not (item_source_ids & source_keys):
                continue
            path = self.generated_file_service.absolute_path(item)
            segments = self._parse_transcript_file(path, output_format=str(item.get("output_format") or ""))
            if segments:
                return {"segments": segments, "generated": item}
        return {}

    def _parse_transcript_file(self, path: Path, *, output_format: str) -> list[dict[str, Any]]:
        if not path.exists() or not path.is_file():
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
        if str(output_format or "").lower() == "json":
            try:
                payload = json.loads(text)
            except Exception:
                payload = {}
            transcripts = payload.get("transcripts") if isinstance(payload, dict) else []
            segments: list[dict[str, Any]] = []
            for transcript in (transcripts if isinstance(transcripts, list) else []):
                segments.extend(self._normalize_segments((transcript or {}).get("segments")))
            return segments
        return self._parse_timestamped_text(text)

    def _parse_timestamped_text(self, text: str) -> list[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        for index, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip().lstrip("-").strip()
            match = re.match(r"^\[(?P<start>[^\]]+?)\s*-\s*(?P<end>[^\]]+?)\]\s*(?P<text>.+)$", stripped)
            if not match:
                match = re.match(r"^(?P<start>\d+:\d\d(?::\d\d)?[,.]?\d*)\s+-->\s+(?P<end>\d+:\d\d(?::\d\d)?[,.]?\d*)\s*(?P<text>.*)$", stripped)
            if not match:
                continue
            segment_text = str(match.group("text") or "").strip()
            if not segment_text:
                continue
            segments.append(
                {
                    "index": len(segments) + 1,
                    "start": self._parse_time_label(match.group("start")),
                    "end": self._parse_time_label(match.group("end")),
                    "text": segment_text,
                }
            )
            if index > 5000:
                break
        return self._normalize_segments(segments)

    def _resolve_activity_source(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        activity: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(activity, dict):
            return None
        candidates = []
        for key in ("source_id", "generated_handle", "generated_id", "attachment_id", "attachment_handle", "handle"):
            value = str(activity.get(key) or "").strip()
            if value and value not in candidates:
                candidates.append(value)
        for target in candidates:
            try:
                source = self.generated_file_service._resolve_media_source(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    target=target,
                )
            except Exception:
                source = None
            if not source:
                continue
            source_path = Path(source.get("absolute_path") or "")
            if not source_path.exists() or not source_path.is_file():
                continue
            source_id = str(source.get("handle") or target).strip()
            return {
                **source,
                "source_id": source_id,
                "internal_source_id": str(source.get("source_id") or "").strip(),
                "absolute_path": source_path,
                "title": str(activity.get("title") or source.get("title") or source_id).strip(),
                "role": self._normalize_audio_role(
                    activity.get("role")
                    or activity.get("stem_role")
                    or source.get("role")
                    or source.get("stem_role")
                    or self._infer_audio_role_from_source(source)
                ),
            }
        return None

    def _find_timeline_for_activity(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        activity: dict[str, Any],
    ) -> dict[str, Any] | None:
        timeline_id = str(activity.get("timeline_id") or "").strip()
        if timeline_id:
            timeline = self.store.get_desktop_music_timeline(timeline_id=timeline_id)
            if timeline and str(timeline.get("profile_user_id")) == str(profile_user_id) and str(timeline.get("session_id")) == str(session_id):
                return timeline
        for key in ("source_id", "generated_handle", "generated_id", "attachment_id", "attachment_handle"):
            value = str(activity.get(key) or "").strip()
            if not value:
                continue
            timeline = self.store.get_desktop_music_timeline_by_source(
                profile_user_id=profile_user_id,
                session_id=session_id,
                source_id=value,
            )
            if timeline:
                return timeline
        return None

    def _normalize_segments(self, raw_segments: Any) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for raw in list(raw_segments or []):
            if not isinstance(raw, dict):
                continue
            text = " ".join(str(raw.get("text") or "").split()).strip()
            if not text:
                continue
            start = self._safe_seconds(raw.get("start"))
            end = self._safe_seconds(raw.get("end"))
            if end < start:
                end = start
            normalized.append(
                {
                    "index": len(normalized) + 1,
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "text": text[:500],
                }
            )
        return normalized

    def _transcript_has_segments(self, transcript: dict[str, Any]) -> bool:
        return bool(self._normalize_segments(transcript.get("segments")))

    def _source_is_vocal_stem(self, source: dict[str, Any]) -> bool:
        return self._normalize_audio_role(
            source.get("role")
            or source.get("stem_role")
            or self._infer_audio_role_from_source(source)
        ) == "vocals"

    def _source_is_instrumental_stem(self, source: dict[str, Any]) -> bool:
        return self._normalize_audio_role(
            source.get("role")
            or source.get("stem_role")
            or self._infer_audio_role_from_source(source)
        ) == "instrumental"

    def _infer_audio_role_from_source(self, source: dict[str, Any]) -> str:
        content_card = source.get("content_card") if isinstance(source.get("content_card"), dict) else {}
        separation = content_card.get("separation") if isinstance(content_card.get("separation"), dict) else {}
        explicit = self._normalize_audio_role(
            separation.get("stem_role")
            or source.get("stem_role")
            or source.get("role")
        )
        if explicit:
            return explicit

        title = str(source.get("title") or "").strip().lower()
        summary = str(source.get("summary") or "").strip().lower()
        text = f"{title} {summary}"
        if any(token in text for token in ("instrumental", "accompaniment", "no_vocals", "pure music", "纯音乐", "伴奏轨", "伴奏")):
            return "instrumental"
        if any(token in text for token in ("vocals", "vocal", "voice", "人声轨", "人声")):
            return "vocals"
        return ""

    def _normalize_audio_role(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        if text in {"vocals", "vocal", "voice", "人声", "人声轨"}:
            return "vocals"
        if text in {"instrumental", "accompaniment", "no_vocals", "pure music", "纯音乐", "伴奏", "伴奏轨"}:
            return "instrumental"
        return ""

    def _nearby_segments(self, segments: list[dict[str, Any]], *, progress_seconds: float) -> list[dict[str, Any]]:
        window_before = float(getattr(config, "DESKTOP_TIMELINE_PROMPT_BEFORE_SECONDS", 8) or 8)
        window_after = float(getattr(config, "DESKTOP_TIMELINE_PROMPT_AFTER_SECONDS", 10) or 10)
        lower = max(0.0, progress_seconds - window_before)
        upper = max(progress_seconds + window_after, lower + 1)
        limit = int(getattr(config, "DESKTOP_TIMELINE_PROMPT_SEGMENT_LIMIT", 3) or 3)
        limit = max(1, limit)
        candidates = [
            segment
            for segment in segments
            if self._safe_seconds(segment.get("end")) >= lower and self._safe_seconds(segment.get("start")) <= upper
        ]
        if not candidates and segments:
            previous = [segment for segment in segments if self._safe_seconds(segment.get("end")) <= progress_seconds]
            return previous[-min(3, limit):]

        def distance_from_progress(segment: dict[str, Any]) -> tuple[float, float]:
            start = self._safe_seconds(segment.get("start"))
            end = self._safe_seconds(segment.get("end"))
            if start <= progress_seconds <= end:
                distance = 0.0
            else:
                distance = min(abs(start - progress_seconds), abs(end - progress_seconds))
            return distance, start

        selected = sorted(candidates, key=distance_from_progress)[:limit]
        return sorted(selected, key=lambda segment: self._safe_seconds(segment.get("start")))

    def _build_rolling_summary(self, segments: list[dict[str, Any]]) -> str:
        if not segments:
            return ""
        snippets = []
        for segment in segments[:6]:
            text = " ".join(str(segment.get("text") or "").split()).strip()
            if text:
                snippets.append(text[:40])
        if not snippets:
            return ""
        return ("前面大致听到过：" + " / ".join(snippets))[:360]

    def _clean_summary_for_prompt(self, summary: str) -> str:
        text = " ".join(str(summary or "").split()).strip()
        legacy_prefix = "已根据前文转写片段建立轻量线索："
        if text.startswith(legacy_prefix):
            text = "前面大致听到过：" + text[len(legacy_prefix):].strip()
        text = text.replace("转写片段", "听歌线索").replace("转写稿", "歌词线索")
        return text[:360]

    def _estimate_ready_until(self, segments: list[dict[str, Any]]) -> float:
        if not segments:
            return 0.0
        return max(self._safe_seconds(segment.get("end")) for segment in segments)

    def _timeline_matches_source(self, timeline: dict[str, Any] | None, *, source_size: int, source_mtime_ns: int) -> bool:
        if not timeline:
            return False
        source_kind = str(timeline.get("source_kind") or "")
        return (
            int(timeline.get("source_size") or 0) == int(source_size or 0)
            and int(timeline.get("source_mtime_ns") or 0) == int(source_mtime_ns or 0)
            and f"#{TIMELINE_BUILD_VERSION}" in source_kind
        )

    def _timeline_source_kind(self, source: dict[str, Any], *, quality: str) -> str:
        base = str(source.get("source_type") or "audio").strip() or "audio"
        return f"{base}#{TIMELINE_BUILD_VERSION}#{self._normalize_quality(quality)}"

    def _source_kind_with_quality(self, timeline: dict[str, Any] | None, *, quality: str) -> str:
        current = str((timeline or {}).get("source_kind") or "").strip()
        base = current.split("#", 1)[0].strip() if current else "audio"
        return f"{base or 'audio'}#{TIMELINE_BUILD_VERSION}#{self._normalize_quality(quality)}"

    def _timeline_quality(self, timeline: dict[str, Any] | None) -> str:
        if not timeline:
            return TIMELINE_QUALITY_MIXED
        raw_quality = str(timeline.get("quality") or "").strip().lower()
        if raw_quality:
            return self._normalize_quality(raw_quality)
        source_kind = str(timeline.get("source_kind") or "").strip().lower()
        for item in source_kind.split("#"):
            if item in {TIMELINE_QUALITY_VOCAL, TIMELINE_QUALITY_MIXED}:
                return item
        return TIMELINE_QUALITY_MIXED

    def _normalize_quality(self, quality: str) -> str:
        normalized = str(quality or "").strip().lower()
        if normalized == TIMELINE_QUALITY_VOCAL:
            return TIMELINE_QUALITY_VOCAL
        if normalized == TIMELINE_QUALITY_MIXED:
            return TIMELINE_QUALITY_MIXED
        return "pending"

    def _source_fingerprint(self, path: Path) -> tuple[int, int]:
        try:
            stat = path.stat()
            return int(stat.st_size), int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)))
        except Exception:
            return 0, 0

    def _public_timeline(self, timeline: dict[str, Any] | None) -> dict[str, Any] | None:
        if not timeline:
            return None
        segments = list(timeline.get("segments") or [])
        return {
            "timeline_id": timeline.get("timeline_id"),
            "source_id": timeline.get("source_id"),
            "status": timeline.get("status"),
            "quality": self._timeline_quality(timeline),
            "ready_until_seconds": timeline.get("ready_until_seconds"),
            "transcript_generated_handle": timeline.get("transcript_generated_handle"),
            "updated_at": timeline.get("updated_at"),
            "segment_count": len(segments),
            "segments": segments[:1200] if str(timeline.get("status") or "") == "ready" else [],
        }

    def _safe_seconds(self, value: Any) -> float:
        try:
            return max(0.0, float(value or 0))
        except Exception:
            return 0.0

    def _format_time(self, seconds: float) -> str:
        total = max(0, int(round(seconds)))
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _parse_time_label(self, value: Any) -> float:
        text = str(value or "").strip().replace(",", ".")
        parts = text.split(":")
        try:
            if len(parts) == 3:
                return max(0.0, int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2]))
            if len(parts) == 2:
                return max(0.0, int(parts[0]) * 60 + float(parts[1]))
            return max(0.0, float(text))
        except Exception:
            return 0.0
