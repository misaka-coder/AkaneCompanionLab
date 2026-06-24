from __future__ import annotations

import csv
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
from copy import copy
from pathlib import Path
from typing import Any, Callable

from . import generated_files_cards, generated_files_delivery, generated_files_io, generated_files_media
from .attachment_inbox import AttachmentInboxService
from .store import MemoryStore


SUPPORTED_OUTPUT_FORMATS = {"txt", "md", "docx", "xlsx", "pdf", "json", "csv", "html"}
MEDIA_OUTPUT_FORMATS = {"mp3", "wav", "flac", "m4a", "aac", "ogg", "opus"}
TRANSCRIPT_OUTPUT_FORMATS = {"md", "txt", "srt", "vtt", "json"}
TEXT_INSPECT_FORMATS = {"txt", "md", "json", "csv", "html", "srt", "vtt", "xml", "log", "yaml", "yml"}
PROTECTED_MEDIA_EXTENSIONS = {"kgm", "ncm", "qmc", "qmc0", "qmc3", "mflac", "mgg", "tkm"}
VIDEO_MEDIA_EXTENSIONS = {"mp4", "mov", "mkv", "webm", "avi"}
VOICE_DATASET_PRESETS: dict[str, dict[str, Any]] = {
    "gpt_sovits": {
        "target_sr": 44100,
        "mono": True,
        "min_clip_seconds": 3.0,
        "max_clip_seconds": 12.0,
        "silence_threshold_db": -40.0,
        "min_silence_ms": 300,
        "max_silence_kept_ms": 300,
    },
    "rvc": {
        "target_sr": 40000,
        "mono": True,
        "min_clip_seconds": 3.0,
        "max_clip_seconds": 15.0,
        "silence_threshold_db": -40.0,
        "min_silence_ms": 300,
        "max_silence_kept_ms": 250,
    },
    "archive": {
        "target_sr": 44100,
        "mono": True,
        "min_clip_seconds": 2.0,
        "max_clip_seconds": 30.0,
        "silence_threshold_db": -45.0,
        "min_silence_ms": 450,
        "max_silence_kept_ms": 500,
    },
}


class GeneratedFileService:
    """Create and project files authored by Akane.

    The model decides the intent and content; this service handles safe local
    rendering, persistence, and a small prompt projection for later revision.
    """

    def __init__(
        self,
        *,
        base_dir: Path,
        store: MemoryStore,
        attachment_service: AttachmentInboxService,
        legacy_base_dirs: list[Path] | tuple[Path, ...] | None = None,
        ensure_storage_ready: Callable[[], Any] | None = None,
        work_dir: Path | None = None,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir = Path(work_dir) if work_dir is not None else self.base_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.legacy_base_dirs = [
            Path(item)
            for item in list(legacy_base_dirs or [])
            if Path(item) != self.base_dir
        ]
        self.ensure_storage_ready = ensure_storage_ready
        self.store = store
        self.attachment_service = attachment_service
        self._whisper_model_cache: dict[tuple[str, str, str], Any] = {}

    def compose_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source_targets: list[str],
        task: str,
        output_format: str,
        output_title: str,
        structure: str = "",
        style: str = "",
        fidelity: str = "",
        content_markdown: str = "",
        table_rows: list[list[Any]] | None = None,
        formatting: dict[str, Any] | None = None,
        send_to_user: bool = True,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        normalized_format = self._normalize_output_format(output_format)
        if normalized_format not in SUPPORTED_OUTPUT_FORMATS:
            return {
                "ok": False,
                "generated": None,
                "error": f"暂不支持生成 {output_format or 'unknown'} 格式。",
                "followup_context": (
                    f"你刚刚想生成 {output_format or 'unknown'} 文件，但这个输出格式还没有接入。"
                    "请换成 md、txt、docx、xlsx、pdf、json、csv 或 html。"
                ),
            }

        sources, unresolved = self._resolve_sources(
            profile_user_id=profile_user_id,
            session_id=session_id,
            source_targets=source_targets,
        )
        if unresolved and not sources and not str(content_markdown or "").strip() and not table_rows:
            return {
                "ok": False,
                "generated": None,
                "error": "source_not_found",
                "followup_context": (
                    f"你刚刚想生成文件，但没有找到这些来源：{', '.join(unresolved[:5])}。"
                    "请自然向用户确认要处理哪份文件，不要重复调用 compose_file。"
                ),
            }

        title = self._normalize_title(output_title) or self._infer_title(
            task=task,
            output_format=normalized_format,
            sources=sources,
        )
        content = str(content_markdown or "").strip()
        rows = self._normalize_table_rows(table_rows)
        style_rules = self._normalize_formatting(formatting)
        content = self._maybe_replace_partial_source_content(
            content=content,
            rows=rows,
            sources=sources,
            task=task,
            structure=structure,
            fidelity=fidelity,
            output_format=normalized_format,
        )
        if not content:
            if (
                not rows
                and sources
                and self._looks_like_faithful_source_export(
                    task=task,
                    structure=structure,
                    fidelity=fidelity,
                    output_format=normalized_format,
                )
            ):
                content = self._build_source_only_markdown(sources)
            else:
                content = self._build_fallback_markdown(
                    title=title,
                    task=task,
                    structure=structure,
                    style=style,
                    fidelity=fidelity,
                    sources=sources,
                    rows=rows,
                )
        if normalized_format == "xlsx" and not rows:
            rows = self._extract_table_rows_from_markdown(content)
        if normalized_format == "json" and not self._looks_like_json(content):
            content = json.dumps(
                {
                    "title": title,
                    "task": str(task or "").strip(),
                    "content": content,
                },
                ensure_ascii=False,
                indent=2,
            )

        output_path = self._build_output_path(
            profile_user_id=profile_user_id,
            session_id=session_id,
            title=title,
            output_format=normalized_format,
            timestamp=effective_ts,
        )
        try:
            self._render_output_file(
                output_path=output_path,
                output_format=normalized_format,
                title=title,
                content=content,
                table_rows=rows,
                formatting=style_rules,
            )
        except Exception as exc:
            return {
                "ok": False,
                "generated": None,
                "error": str(exc),
                "followup_context": (
                    f"你刚刚尝试生成「{title}」但渲染失败：{str(exc)[:180]}。"
                    "请自然告诉用户失败原因；如果是缺少依赖，可以提醒先安装对应 Python 库。"
                ),
            }

        source_ids = [str(source.get("source_id") or "").strip() for source in sources]
        source_ids = [source_id for source_id in source_ids if source_id]
        content_card = self._build_content_card(
            title=title,
            output_format=normalized_format,
            task=task,
            structure=structure,
            style=style,
            fidelity=fidelity,
            sources=sources,
            content=content,
            table_rows=rows,
            formatting=style_rules,
        )
        generated = self.store.add_generated_file(
            profile_user_id=profile_user_id,
            session_id=session_id,
            output_title=title,
            output_format=normalized_format,
            storage_relpath=self._storage_relpath(output_path),
            mime_type=self._mime_type_for_format(normalized_format),
            file_ext=normalized_format,
            file_size=output_path.stat().st_size,
            source_ids=source_ids,
            content_card=content_card,
            summary=str(content_card.get("summary") or "").strip(),
            created_by_tool="compose_file",
            delivery_status="pending" if send_to_user else "not_requested",
            timestamp=effective_ts,
        )
        generated["absolute_path"] = str(self.absolute_path(generated))
        return {
            "ok": True,
            "generated": generated,
            "unresolved": unresolved,
            "send_to_user": bool(send_to_user),
            "followup_context": self._build_compose_followup(
                generated=generated,
                unresolved=unresolved,
                send_to_user=send_to_user,
            ),
        }

    def convert_media_file(
        self,
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
    ) -> dict[str, Any]:
        return generated_files_media.convert_media_file(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            source_target=source_target,
            output_format=output_format,
            output_title=output_title,
            bitrate=bitrate,
            sample_rate=sample_rate,
            channels=channels,
            start_time=start_time,
            end_time=end_time,
            normalize_volume=normalize_volume,
            volume_gain_db=volume_gain_db,
            trim_silence=trim_silence,
            fade_in_seconds=fade_in_seconds,
            fade_out_seconds=fade_out_seconds,
            speed_ratio=speed_ratio,
            send_to_user=send_to_user,
            timestamp=timestamp,
            media_output_formats=MEDIA_OUTPUT_FORMATS,
            protected_media_extensions=PROTECTED_MEDIA_EXTENSIONS,
        )

    def separate_audio_stems(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source_target: str,
        mode: str = "vocals_instrumental",
        output_format: str = "wav",
        output_title: str = "",
        send_to_user: bool = True,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        return generated_files_media.separate_audio_stems(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            source_target=source_target,
            mode=mode,
            output_format=output_format,
            output_title=output_title,
            send_to_user=send_to_user,
            timestamp=timestamp,
            protected_media_extensions=PROTECTED_MEDIA_EXTENSIONS,
        )

    def clean_voice_track(
        self,
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
    ) -> dict[str, Any]:
        return generated_files_media.clean_voice_track(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            source_target=source_target,
            mode=mode,
            quality=quality,
            output_format=output_format,
            output_title=output_title,
            post_filter=post_filter,
            send_to_user=send_to_user,
            timestamp=timestamp,
            protected_media_extensions=PROTECTED_MEDIA_EXTENSIONS,
        )

    def prepare_voice_dataset(
        self,
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
    ) -> dict[str, Any]:
        return generated_files_media.prepare_voice_dataset(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            source_targets=source_targets,
            profile=profile,
            output_title=output_title,
            target_sr=target_sr,
            mono=mono,
            min_clip_seconds=min_clip_seconds,
            max_clip_seconds=max_clip_seconds,
            silence_threshold_db=silence_threshold_db,
            min_silence_ms=min_silence_ms,
            max_silence_kept_ms=max_silence_kept_ms,
            clean_first=clean_first,
            normalize_volume=normalize_volume,
            send_to_user=send_to_user,
            timestamp=timestamp,
            protected_media_extensions=PROTECTED_MEDIA_EXTENSIONS,
            voice_dataset_presets=VOICE_DATASET_PRESETS,
        )

    def transcribe_media(
        self,
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
    ) -> dict[str, Any]:
        return generated_files_media.transcribe_media(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            source_targets=source_targets,
            output_format=output_format,
            output_title=output_title,
            language=language,
            with_timestamps=with_timestamps,
            merge_outputs=merge_outputs,
            model_size=model_size,
            device=device,
            compute_type=compute_type,
            vad_filter=vad_filter,
            send_to_user=send_to_user,
            timestamp=timestamp,
            transcript_output_formats=TRANSCRIPT_OUTPUT_FORMATS,
        )

    def inspect_media_info(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source_target: str,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        return generated_files_media.inspect_media_info(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            source_target=source_target,
            timestamp=timestamp,
        )

    def build_prompt_context(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        limit: int = 3,
    ) -> str:
        return generated_files_cards.build_prompt_context(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            limit=limit,
        )

    def revise_generated_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
        instruction: str,
        output_format: str = "",
        output_title: str = "",
        content_markdown: str = "",
        table_rows: list[list[Any]] | None = None,
        formatting: dict[str, Any] | None = None,
        send_to_user: bool = True,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        original = self._resolve_generated_file(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
        )
        if original is None:
            return {
                "ok": False,
                "generated": None,
                "error": "generated_file_not_found",
                "followup_context": (
                    f"你刚刚想修改生成文件 {target or 'latest'}，但没有找到这个生成物。"
                    "请自然向用户确认要改哪一份，不要重复调用 revise_generated_file。"
                ),
            }

        normalized_format = self._normalize_output_format(
            output_format or original.get("output_format") or "md"
        )
        if normalized_format not in SUPPORTED_OUTPUT_FORMATS:
            return {
                "ok": False,
                "generated": None,
                "error": f"暂不支持生成 {output_format or 'unknown'} 格式。",
                "followup_context": (
                    f"你刚刚想把修改版导出为 {output_format or 'unknown'}，但这个格式还不支持。"
                    "请换成 md、txt、docx、xlsx、pdf、json、csv 或 html。"
                ),
            }

        content = str(content_markdown or "").strip()
        rows = self._normalize_table_rows(table_rows)
        style_rules = self._normalize_formatting(formatting)
        if not content and not rows:
            return {
                "ok": False,
                "generated": None,
                "error": "missing_revised_content",
                "followup_context": (
                    "你刚刚想修改生成文件，但没有把修改后的最终正文或表格交给工具。"
                    "请根据生成文件工作台里的预览和用户要求，先在心里整理出修改后的完整内容；"
                    "下一次需要调用时，把最终内容写进 content_markdown 或 table_rows。"
                ),
            }

        title = self._normalize_title(output_title) or str(original.get("output_title") or "").strip()
        if not title:
            title = "生成文件修改版"
        if normalized_format == "xlsx" and not rows:
            rows = self._extract_table_rows_from_markdown(content)
        if normalized_format == "json" and content and not self._looks_like_json(content):
            content = json.dumps(
                {
                    "title": title,
                    "instruction": str(instruction or "").strip(),
                    "content": content,
                },
                ensure_ascii=False,
                indent=2,
            )

        output_path = self._build_output_path(
            profile_user_id=profile_user_id,
            session_id=session_id,
            title=title,
            output_format=normalized_format,
            timestamp=effective_ts,
        )
        try:
            self._render_output_file(
                output_path=output_path,
                output_format=normalized_format,
                title=title,
                content=content,
                table_rows=rows,
                formatting=style_rules,
            )
        except Exception as exc:
            return {
                "ok": False,
                "generated": None,
                "error": str(exc),
                "followup_context": (
                    f"你刚刚尝试生成「{title}」修改版但渲染失败：{str(exc)[:180]}。"
                    "请自然告诉用户失败原因；如果是缺少依赖，可以提醒先安装对应 Python 库。"
                ),
            }

        original_id = str(original.get("generated_id") or "").strip()
        original_card = original.get("content_card") if isinstance(original.get("content_card"), dict) else {}
        sources = [
            {
                "source_type": "generated",
                "source_id": original_id,
                "handle": str(original.get("generated_handle") or "").strip(),
                "title": str(original.get("output_title") or "").strip(),
                "summary": str(original.get("summary") or original_card.get("summary") or "").strip(),
                "preview": str(original_card.get("content_preview") or "").strip(),
                "file_kind": str(original.get("output_format") or "").strip(),
            }
        ]
        content_card = self._build_content_card(
            title=title,
            output_format=normalized_format,
            task=f"修改生成文件：{str(instruction or '').strip()}",
            structure="revision",
            style="",
            fidelity="preserve_accepted_parts",
            sources=sources,
            content=content,
            table_rows=rows,
            formatting=style_rules,
        )
        source_ids = [original_id]
        for item in list(original.get("source_ids") or [])[:8]:
            text = str(item or "").strip()
            if text and text not in source_ids:
                source_ids.append(text)
        generated = self.store.add_generated_file(
            profile_user_id=profile_user_id,
            session_id=session_id,
            output_title=title,
            output_format=normalized_format,
            storage_relpath=self._storage_relpath(output_path),
            mime_type=self._mime_type_for_format(normalized_format),
            file_ext=normalized_format,
            file_size=output_path.stat().st_size,
            source_ids=source_ids,
            content_card=content_card,
            summary=str(content_card.get("summary") or "").strip(),
            created_by_tool="revise_generated_file",
            version_of_generated_id=original_id,
            version_no=int(original.get("version_no") or 1) + 1,
            delivery_status="pending" if send_to_user else "not_requested",
            timestamp=effective_ts,
        )
        generated["absolute_path"] = str(self.absolute_path(generated))
        return {
            "ok": True,
            "generated": generated,
            "original": original,
            "send_to_user": bool(send_to_user),
            "followup_context": self._build_revise_followup(
                original=original,
                generated=generated,
                send_to_user=send_to_user,
            ),
        }

    def absolute_path(self, generated: dict[str, Any]) -> Path:
        relpath = str(generated.get("storage_relpath") or "").strip()
        if not relpath:
            return self.base_dir
        relative_path = Path(relpath)
        if relative_path.is_absolute():
            return self.base_dir
        storage_roots = [self.base_dir, *self.legacy_base_dirs]
        fallback = self.base_dir
        for index, storage_root in enumerate(storage_roots):
            candidate = (storage_root / relative_path).resolve()
            try:
                candidate.relative_to(storage_root.resolve())
            except Exception:
                continue
            if index == 0:
                fallback = candidate
            if candidate.exists():
                return candidate
        return fallback

    def is_managed_storage_path(self, path: Path) -> bool:
        try:
            resolved = Path(path).resolve()
        except Exception:
            return False
        for storage_root in [self.base_dir, *self.legacy_base_dirs]:
            try:
                resolved.relative_to(storage_root.resolve())
                return True
            except Exception:
                continue
        return False

    def mark_delivery_status(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        generated_id: str,
        delivery_status: str,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        return self.store.update_generated_file(
            profile_user_id=profile_user_id,
            session_id=session_id,
            generated_id=generated_id,
            delivery_status=delivery_status,
            updated_at=timestamp,
        )

    def send_generated_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str = "latest",
        targets: list[str] | tuple[str, ...] | None = None,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        return generated_files_delivery.send_generated_file(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
            targets=targets,
            timestamp=timestamp,
        )

    def send_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str = "latest",
        targets: list[str] | tuple[str, ...] | None = None,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Send existing files from either Attachment Inbox or GeneratedFileStore."""
        return generated_files_delivery.send_file(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
            targets=targets,
            timestamp=timestamp,
        )

    def inspect_generated_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str = "latest",
        section: str = "content",
        max_chars: int = 12000,
    ) -> dict[str, Any]:
        """Read back a generated file or generated bundle without mutating it."""
        return generated_files_delivery.inspect_generated_file(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
            section=section,
            max_chars=max_chars,
        )

    def apply_style_to_existing_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str = "latest",
        instruction: str = "",
        output_title: str = "",
        formatting: dict[str, Any] | None = None,
        send_to_user: bool = True,
        target_type: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Create a styled copy of an existing file without regenerating content."""
        return generated_files_delivery.apply_style_to_existing_file(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
            instruction=instruction,
            output_title=output_title,
            formatting=formatting,
            send_to_user=send_to_user,
            target_type=target_type,
            timestamp=timestamp,
        )

    def manage_generated_files(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        action: str,
        targets: list[str] | tuple[str, ...] | set[str] | str | None = None,
        reason: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Archive or delete generated files without touching source attachments."""
        return generated_files_delivery.manage_generated_files(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            action=action,
            targets=targets,
            reason=reason,
            timestamp=timestamp,
        )

    def _resolve_sources(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source_targets: list[str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        sources: list[dict[str, Any]] = []
        unresolved: list[str] = []
        for target in source_targets:
            normalized = str(target or "").strip()
            if not normalized:
                continue
            generated = None
            if normalized.lower().startswith("gen_") or normalized.lower().startswith("generated::"):
                generated = self.store.find_generated_file(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    query=normalized,
                    statuses=["ready"],
                )
            if generated is not None:
                sources.append(self._generated_source_card(generated))
                continue

            attachment = self.attachment_service.resolve_attachment(
                profile_user_id=profile_user_id,
                session_id=session_id,
                target=normalized,
                kind="any",
            )
            if attachment is None:
                unresolved.append(normalized)
                continue
            sources.append(self._attachment_source_card(attachment))
        return sources, unresolved

    def _maybe_replace_partial_source_content(
        self,
        *,
        content: str,
        rows: list[list[str]],
        sources: list[dict[str, Any]],
        task: str,
        structure: str,
        fidelity: str,
        output_format: str,
    ) -> str:
        """Guard against the model copying a prompt excerpt as full source.

        For faithful conversion tasks, Akane should leave content empty so the
        backend reads the stored attachment. If she copied only the visible
        excerpt, recover by replacing that prefix with the fuller source card.
        """

        if not content or rows or not sources:
            return content
        if not self._looks_like_faithful_source_export(
            task=task,
            structure=structure,
            fidelity=fidelity,
            output_format=output_format,
        ):
            return content
        if len(sources) != 1:
            return content
        source = sources[0]
        if str(source.get("source_type") or "") != "attachment":
            return content
        material = self._clean_source_preview_for_output(str(source.get("preview") or "").strip())
        if len(material) <= len(content) + 40:
            return content
        if self._normalized_text_startswith(material, content):
            return material
        return content

    def _looks_like_faithful_source_export(
        self,
        *,
        task: str,
        structure: str,
        fidelity: str,
        output_format: str,
    ) -> bool:
        text = " ".join(str(part or "") for part in (task, structure, fidelity, output_format)).lower()
        if any(token in text for token in ("summary", "summarize", "摘要", "总结", "提取重点", "整理重点", "改写", "重写")):
            return False
        return any(
            token in text
            for token in (
                "转换",
                "转成",
                "转为",
                "转pdf",
                "转 pdf",
                "转word",
                "转 word",
                "导出",
                "原文",
                "原样",
                "忠实",
                "完整",
                "保留",
                "pdf",
                "docx",
                "word",
            )
        )

    def _normalized_text_startswith(self, full_text: str, prefix: str) -> bool:
        def normalize(value: str) -> str:
            return re.sub(r"\s+", "", str(value or "")).strip()

        full = normalize(full_text)
        head = normalize(prefix)
        return bool(head) and full.startswith(head)

    def _resolve_generated_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
    ) -> dict[str, Any] | None:
        return generated_files_delivery.resolve_generated_file(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
        )

    def _resolve_generated_file_targets(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        targets: list[str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        return generated_files_delivery.resolve_generated_file_targets(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            targets=targets,
        )

    def _resolve_generated_file_any_status(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
    ) -> dict[str, Any] | None:
        return generated_files_delivery.resolve_generated_file_any_status(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
        )

    def _normalize_targets(self, value: list[str] | tuple[str, ...] | set[str] | str | None) -> list[str]:
        return generated_files_delivery.normalize_targets(self, value)

    def _normalize_generated_file_action(self, value: Any) -> str:
        return generated_files_delivery.normalize_generated_file_action(self, value)

    def _delete_generated_file_on_disk(self, item: dict[str, Any]) -> tuple[bool, str]:
        return generated_files_delivery.delete_generated_file_on_disk(self, item)

    def _resolve_sendable_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
        timestamp: int | None,
    ) -> tuple[dict[str, Any] | None, str]:
        return generated_files_delivery.resolve_sendable_file(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
            timestamp=timestamp,
        )

    def _resolve_latest_sendable_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        timestamp: int | None,
    ) -> tuple[dict[str, Any] | None, str]:
        return generated_files_delivery.resolve_latest_sendable_file(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            timestamp=timestamp,
        )

    def _resolve_generated_sendable_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
        timestamp: int | None,
    ) -> tuple[dict[str, Any] | None, str]:
        return generated_files_delivery.resolve_generated_sendable_file(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
            timestamp=timestamp,
        )

    def _resolve_attachment_sendable_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
    ) -> tuple[dict[str, Any] | None, str]:
        return generated_files_delivery.resolve_attachment_sendable_file(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
        )

    def _generated_item_to_sendable_file(self, generated: dict[str, Any], *, timestamp: int | None) -> tuple[dict[str, Any] | None, str]:
        return generated_files_delivery.generated_item_to_sendable_file(
            self,
            generated,
            timestamp=timestamp,
        )

    def _attachment_item_to_sendable_file(self, attachment: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        return generated_files_delivery.attachment_item_to_sendable_file(self, attachment)

    def _normalize_generated_inspection_section(self, value: Any) -> str:
        return generated_files_delivery.normalize_generated_inspection_section(self, value)

    def _normalize_inspection_max_chars(self, value: Any) -> int:
        return generated_files_delivery.normalize_inspection_max_chars(self, value)

    def _inspect_generated_file_content(
        self,
        *,
        generated: dict[str, Any],
        path: Path,
        section: str,
        max_chars: int,
    ) -> dict[str, Any]:
        return generated_files_delivery.inspect_generated_file_content(
            self,
            generated=generated,
            path=path,
            section=section,
            max_chars=max_chars,
        )

    def _render_generated_summary_inspection(self, *, generated: dict[str, Any], path: Path) -> str:
        return generated_files_cards.render_generated_summary_inspection(
            self,
            generated=generated,
            path=path,
        )

    def _render_generated_binary_inspection(self, *, generated: dict[str, Any], path: Path, output_format: str) -> str:
        return generated_files_cards.render_generated_binary_inspection(
            self,
            generated=generated,
            path=path,
            output_format=output_format,
        )

    def _read_generated_text_material(self, *, path: Path, output_format: str, max_chars: int) -> str:
        return generated_files_delivery.read_generated_text_material(
            self,
            path=path,
            output_format=output_format,
            max_chars=max_chars,
            text_inspect_formats=TEXT_INSPECT_FORMATS,
        )

    def _read_plain_text_file(self, path: Path, *, max_chars: int) -> str:
        return generated_files_delivery.read_plain_text_file(self, path, max_chars=max_chars)

    def _read_generated_docx(self, *, path: Path, max_chars: int) -> str:
        return generated_files_delivery.read_generated_docx(self, path=path, max_chars=max_chars)

    def _read_generated_xlsx(self, *, path: Path, max_chars: int) -> str:
        return generated_files_delivery.read_generated_xlsx(self, path=path, max_chars=max_chars)

    def _read_generated_pdf(self, *, path: Path, max_chars: int) -> str:
        return generated_files_delivery.read_generated_pdf(self, path=path, max_chars=max_chars)

    def _inspect_generated_zip(
        self,
        *,
        generated: dict[str, Any],
        path: Path,
        section: str,
        max_chars: int,
    ) -> dict[str, Any]:
        return generated_files_delivery.inspect_generated_zip(
            self,
            generated=generated,
            path=path,
            section=section,
            max_chars=max_chars,
        )

    def _render_zip_file_list(self, archive: zipfile.ZipFile) -> str:
        return generated_files_delivery.render_zip_file_list(self, archive)

    def _resolve_zip_member_name(self, archive: zipfile.ZipFile, target: str) -> str:
        return generated_files_delivery.resolve_zip_member_name(self, archive, target)

    def _read_zip_member_for_inspection(self, archive: zipfile.ZipFile, *, member_name: str, max_chars: int) -> str:
        return generated_files_delivery.read_zip_member_for_inspection(
            self,
            archive,
            member_name=member_name,
            max_chars=max_chars,
            text_inspect_formats=TEXT_INSPECT_FORMATS,
        )

    def _slice_inspection_text(self, text: str, *, section: str, max_chars: int) -> dict[str, Any]:
        return generated_files_delivery.slice_inspection_text(
            self,
            text,
            section=section,
            max_chars=max_chars,
        )

    def _clip_inspection_text(self, text: str, *, max_chars: int) -> str:
        return generated_files_delivery.clip_inspection_text(
            self,
            text,
            max_chars=max_chars,
        )

    def _build_generated_inspection_followup(self, *, generated: dict[str, Any], inspection: dict[str, Any]) -> str:
        return generated_files_cards.build_generated_inspection_followup(
            self,
            generated=generated,
            inspection=inspection,
        )

    def _generated_display_name(self, item: dict[str, Any]) -> str:
        return generated_files_cards.generated_display_name(item)

    def _resolve_media_source(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
    ) -> dict[str, Any] | None:
        return generated_files_media.resolve_media_source(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
        )

    def _resolve_existing_file_for_style(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
        target_type: str,
    ) -> dict[str, Any] | None:
        return generated_files_delivery.resolve_existing_file_for_style(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
            target_type=target_type,
        )

    def _resolve_generated_style_source(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
    ) -> dict[str, Any] | None:
        return generated_files_delivery.resolve_generated_style_source(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
        )

    def _resolve_attachment_style_source(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
    ) -> dict[str, Any] | None:
        return generated_files_delivery.resolve_attachment_style_source(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
        )

    def _attachment_source_card(self, item: dict[str, Any]) -> dict[str, Any]:
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        handle = str(item.get("attachment_handle") or item.get("attachment_id") or "").strip()
        title = (
            str(item.get("summary_title") or "").strip()
            or str(item.get("origin_name") or "").strip()
            or handle
            or "未命名附件"
        )
        summary = str(item.get("short_hint") or detail.get("summary") or "").strip()
        preview = ""
        if hasattr(self.attachment_service, "read_material_for_generation"):
            try:
                preview = str(
                    self.attachment_service.read_material_for_generation(  # type: ignore[attr-defined]
                        item,
                        max_chars=30000,
                    )
                    or ""
                ).strip()
            except Exception:
                preview = ""
        if not preview:
            preview = str(detail.get("text_preview") or detail.get("content_preview") or "").strip()
        if detail.get("preview_is_truncated") and preview:
            preview += "\n\n（注意：上面是系统可安全展开的片段；如果还需要更后面的内容，应先用 read_attachment_section 按页、行号或 sheet 继续展开。）"
        return {
            "source_type": "attachment",
            "source_id": str(item.get("attachment_id") or "").strip(),
            "handle": handle,
            "title": title,
            "summary": summary,
            "preview": preview,
            "file_kind": str(detail.get("file_kind") or item.get("kind") or "").strip(),
        }

    def _generated_source_card(self, item: dict[str, Any]) -> dict[str, Any]:
        content_card = item.get("content_card") if isinstance(item.get("content_card"), dict) else {}
        handle = str(item.get("generated_handle") or item.get("generated_id") or "").strip()
        return {
            "source_type": "generated",
            "source_id": str(item.get("generated_id") or "").strip(),
            "handle": handle,
            "title": str(item.get("output_title") or handle or "生成文件").strip(),
            "summary": str(item.get("summary") or content_card.get("summary") or "").strip(),
            "preview": str(content_card.get("content_preview") or "").strip(),
            "file_kind": str(item.get("output_format") or "").strip(),
        }

    def _build_fallback_markdown(
        self,
        *,
        title: str,
        task: str,
        structure: str,
        style: str,
        fidelity: str,
        sources: list[dict[str, Any]],
        rows: list[list[str]],
    ) -> str:
        lines = [f"# {title}", ""]
        if task:
            lines.extend([f"任务：{str(task).strip()}", ""])
        if structure or style or fidelity:
            meta = "；".join(part for part in [structure, style, fidelity] if str(part or "").strip())
            if meta:
                lines.extend([f"整理要求：{meta}", ""])
        if rows:
            lines.append("## 表格")
            lines.extend(self._markdown_table(rows))
            lines.append("")
        if sources:
            lines.append("## 来源摘录")
            for source in sources:
                lines.append(f"### {source.get('handle')} {source.get('title')}")
                summary = str(source.get("summary") or "").strip()
                preview = str(source.get("preview") or "").strip()
                if summary:
                    lines.append(summary)
                if preview:
                    lines.append("")
                    lines.append(preview[:20000])
                lines.append("")
        else:
            lines.append("（本文件根据当前对话生成。）")
        return "\n".join(lines).strip()

    def _build_source_only_markdown(self, sources: list[dict[str, Any]]) -> str:
        blocks: list[str] = []
        for source in sources:
            preview = self._clean_source_preview_for_output(str(source.get("preview") or "").strip())
            if not preview:
                preview = str(source.get("summary") or "").strip()
            if not preview:
                continue
            if len(sources) == 1:
                blocks.append(preview)
            else:
                title = str(source.get("title") or source.get("handle") or "来源").strip()
                blocks.append(f"## {title}\n\n{preview}".strip())
        return "\n\n".join(blocks).strip()

    def _clean_source_preview_for_output(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(
            r"\n*\s*（注意：上面是系统可安全展开的片段；如果还需要更后面的内容，应先用 read_attachment_section 按页、行号或 sheet 继续展开。）\s*$",
            "",
            text,
        )
        return text.strip()

    def _render_output_file(
        self,
        *,
        output_path: Path,
        output_format: str,
        title: str,
        content: str,
        table_rows: list[list[str]],
        formatting: dict[str, Any],
    ) -> None:
        return generated_files_io.render_output_file(
            self,
            output_path=output_path,
            output_format=output_format,
            title=title,
            content=content,
            table_rows=table_rows,
            formatting=formatting,
        )

    def _style_existing_xlsx(self, *, source_path: Path, output_path: Path, formatting: dict[str, Any]) -> None:
        return generated_files_io.style_existing_xlsx(
            self,
            source_path=source_path,
            output_path=output_path,
            formatting=formatting,
        )

    def _style_existing_docx(self, *, source_path: Path, output_path: Path, formatting: dict[str, Any]) -> None:
        return generated_files_io.style_existing_docx(
            self,
            source_path=source_path,
            output_path=output_path,
            formatting=formatting,
        )

    def _docx_table_rows(self, table: Any) -> list[list[str]]:
        return generated_files_io.docx_table_rows(table)

    def _write_docx(self, *, output_path: Path, title: str, content: str, formatting: dict[str, Any]) -> None:
        return generated_files_io.write_docx(
            self,
            output_path=output_path,
            title=title,
            content=content,
            formatting=formatting,
        )

    def _write_xlsx(
        self,
        *,
        output_path: Path,
        title: str,
        content: str,
        table_rows: list[list[str]],
        formatting: dict[str, Any],
    ) -> None:
        return generated_files_io.write_xlsx(
            self,
            output_path=output_path,
            title=title,
            content=content,
            table_rows=table_rows,
            formatting=formatting,
        )

    def _apply_xlsx_formatting(self, *, sheet: Any, rows: list[list[str]], formatting: dict[str, Any]) -> None:
        return generated_files_io.apply_xlsx_formatting(
            self,
            sheet=sheet,
            rows=rows,
            formatting=formatting,
        )

    def _apply_xlsx_cell_style(self, cell: Any, style: dict[str, Any]) -> None:
        return generated_files_io.apply_xlsx_cell_style(cell, style)

    def _apply_xlsx_auto_width(self, sheet: Any) -> None:
        return generated_files_io.apply_xlsx_auto_width(sheet)

    def _xlsx_row_matches(self, *, sheet: Any, row_index: int, headers: list[str], rule: dict[str, Any]) -> bool:
        return generated_files_io.xlsx_row_matches(
            self,
            sheet=sheet,
            row_index=row_index,
            headers=headers,
            rule=rule,
        )

    def _apply_docx_table_formatting(self, table: Any, rows: list[list[str]], formatting: dict[str, Any]) -> None:
        return generated_files_io.apply_docx_table_formatting(self, table, rows, formatting)

    def _apply_docx_cell_style(self, cell: Any, style: dict[str, Any]) -> None:
        return generated_files_io.apply_docx_cell_style(self, cell, style)

    def _apply_docx_paragraph_rules(self, paragraph: Any, formatting: dict[str, Any]) -> None:
        return generated_files_io.apply_docx_paragraph_rules(self, paragraph, formatting)

    def _apply_docx_runs_style(self, paragraph: Any, style: dict[str, Any]) -> None:
        return generated_files_io.apply_docx_runs_style(self, paragraph, style)

    def _apply_docx_run_style(self, run: Any, style: dict[str, Any]) -> None:
        return generated_files_io.apply_docx_run_style(self, run, style)

    def _shade_docx_cell(self, cell: Any, fill_color: str) -> None:
        return generated_files_io.shade_docx_cell(cell, fill_color)

    def _docx_highlight_color(self, color: str) -> Any | None:
        return generated_files_io.docx_highlight_color(color)

    def _write_pdf(self, *, output_path: Path, title: str, content: str) -> None:
        return generated_files_io.write_pdf(
            self,
            output_path=output_path,
            title=title,
            content=content,
        )

    def _build_content_card(
        self,
        *,
        title: str,
        output_format: str,
        task: str,
        structure: str,
        style: str,
        fidelity: str,
        sources: list[dict[str, Any]],
        content: str,
        table_rows: list[list[str]],
        formatting: dict[str, Any],
    ) -> dict[str, Any]:
        return generated_files_cards.build_content_card(
            self,
            title=title,
            output_format=output_format,
            task=task,
            structure=structure,
            style=style,
            fidelity=fidelity,
            sources=sources,
            content=content,
            table_rows=table_rows,
            formatting=formatting,
        )

    def _build_style_content_card(
        self,
        *,
        title: str,
        output_format: str,
        source: dict[str, Any],
        instruction: str,
        formatting: dict[str, Any],
    ) -> dict[str, Any]:
        return generated_files_cards.build_style_content_card(
            self,
            title=title,
            output_format=output_format,
            source=source,
            instruction=instruction,
            formatting=formatting,
        )

    def _build_compose_followup(
        self,
        *,
        generated: dict[str, Any],
        unresolved: list[str],
        send_to_user: bool,
    ) -> str:
        return generated_files_cards.build_compose_followup(
            self,
            generated=generated,
            unresolved=unresolved,
            send_to_user=send_to_user,
        )

    def _build_revise_followup(
        self,
        *,
        original: dict[str, Any],
        generated: dict[str, Any],
        send_to_user: bool,
    ) -> str:
        return generated_files_cards.build_revise_followup(
            self,
            original=original,
            generated=generated,
            send_to_user=send_to_user,
        )

    def _build_style_followup(
        self,
        *,
        source: dict[str, Any],
        generated: dict[str, Any],
        send_to_user: bool,
    ) -> str:
        return generated_files_cards.build_style_followup(
            self,
            source=source,
            generated=generated,
            send_to_user=send_to_user,
        )

    def _build_media_conversion_followup(
        self,
        *,
        generated: dict[str, Any],
        source: dict[str, Any],
        output_format: str,
        send_to_user: bool,
    ) -> str:
        return generated_files_cards.build_media_conversion_followup(
            self,
            generated=generated,
            source=source,
            output_format=output_format,
            send_to_user=send_to_user,
        )

    def _build_audio_separation_followup(
        self,
        *,
        generated_files: list[dict[str, Any]],
        source: dict[str, Any],
        output_format: str,
        send_to_user: bool,
    ) -> str:
        return generated_files_cards.build_audio_separation_followup(
            self,
            generated_files=generated_files,
            source=source,
            output_format=output_format,
            send_to_user=send_to_user,
        )

    def _build_voice_clean_followup(
        self,
        *,
        generated: dict[str, Any],
        source: dict[str, Any],
        mode: str,
        backend_used: str,
        send_to_user: bool,
    ) -> str:
        return generated_files_cards.build_voice_clean_followup(
            self,
            generated=generated,
            source=source,
            mode=mode,
            backend_used=backend_used,
            send_to_user=send_to_user,
        )

    def _build_voice_dataset_followup(
        self,
        *,
        generated: dict[str, Any],
        manifest: dict[str, Any],
        send_to_user: bool,
    ) -> str:
        return generated_files_cards.build_voice_dataset_followup(
            self,
            generated=generated,
            manifest=manifest,
            send_to_user=send_to_user,
        )

    def _normalize_voice_dataset_profile(self, value: Any) -> str:
        return generated_files_media.normalize_voice_dataset_profile(value)

    def _normalize_voice_dataset_options(
        self,
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
        return generated_files_media.normalize_voice_dataset_options(
            self,
            preset=preset,
            target_sr=target_sr,
            mono=mono,
            min_clip_seconds=min_clip_seconds,
            max_clip_seconds=max_clip_seconds,
            silence_threshold_db=silence_threshold_db,
            min_silence_ms=min_silence_ms,
            max_silence_kept_ms=max_silence_kept_ms,
        )

    def _infer_voice_dataset_title(self, *, sources: list[dict[str, Any]], profile: str) -> str:
        return generated_files_media.infer_voice_dataset_title(sources=sources, profile=profile)

    def _prepare_voice_dataset_input(
        self,
        *,
        ffmpeg_path: str,
        source_path: Path,
        prepared_path: Path,
        target_sr: int,
        channels: int,
        clean_first: bool,
        normalize_volume: bool,
    ) -> dict[str, Any]:
        return generated_files_media.prepare_voice_dataset_input(
            self,
            ffmpeg_path=ffmpeg_path,
            source_path=source_path,
            prepared_path=prepared_path,
            target_sr=target_sr,
            channels=channels,
            clean_first=clean_first,
            normalize_volume=normalize_volume,
        )

    def _read_pcm16_wav_samples(self, path: Path) -> tuple[array, int]:
        return generated_files_media.read_pcm16_wav_samples(path)

    def _write_pcm16_wav(self, path: Path, *, samples: array, sample_rate: int, channels: int = 1) -> None:
        return generated_files_io.write_pcm16_wav(
            path,
            samples=samples,
            sample_rate=sample_rate,
            channels=channels,
        )

    def _slice_voice_samples(
        self,
        *,
        samples: array,
        sample_rate: int,
        silence_threshold_db: float,
        min_silence_ms: int,
        max_silence_kept_ms: int,
    ) -> list[dict[str, Any]]:
        return generated_files_media.slice_voice_samples(
            self,
            samples=samples,
            sample_rate=sample_rate,
            silence_threshold_db=silence_threshold_db,
            min_silence_ms=min_silence_ms,
            max_silence_kept_ms=max_silence_kept_ms,
        )

    def _merge_tiny_voice_intervals(self, intervals: list[dict[str, Any]], *, sample_rate: int) -> list[dict[str, Any]]:
        return generated_files_media.merge_tiny_voice_intervals(intervals, sample_rate=sample_rate)

    def _analyze_voice_slice(
        self,
        *,
        samples: array,
        sample_rate: int,
        min_clip_seconds: float,
        max_clip_seconds: float,
    ) -> dict[str, Any]:
        return generated_files_media.analyze_voice_slice(
            self,
            samples=samples,
            sample_rate=sample_rate,
            min_clip_seconds=min_clip_seconds,
            max_clip_seconds=max_clip_seconds,
        )

    def _rms_dbfs_for_samples(self, samples: array) -> float:
        return generated_files_media.rms_dbfs_for_samples(samples)

    def _peak_dbfs_for_samples(self, samples: array) -> float:
        return generated_files_media.peak_dbfs_for_samples(samples)

    def _build_voice_dataset_manifest(
        self,
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
        return generated_files_media.build_voice_dataset_manifest(
            title=title,
            profile=profile,
            options=options,
            clean_first=clean_first,
            normalize_volume=normalize_volume,
            sources=sources,
            slices=slices,
            unresolved=unresolved,
            protected=protected,
            missing_files=missing_files,
            timestamp=timestamp,
        )

    def _build_voice_dataset_content_card(self, manifest: dict[str, Any]) -> dict[str, Any]:
        return generated_files_cards.build_voice_dataset_content_card(self, manifest)

    def _render_voice_dataset_readme(self, manifest: dict[str, Any]) -> str:
        return generated_files_io.render_voice_dataset_readme(self, manifest)

    def _resolve_media_sources_for_batch(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source_targets: list[str],
    ) -> tuple[list[dict[str, Any]], list[str], list[str], list[str]]:
        return generated_files_media.resolve_media_sources_for_batch(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            source_targets=source_targets,
            protected_media_extensions=PROTECTED_MEDIA_EXTENSIONS,
        )

    def _normalize_transcript_output_format(self, value: Any) -> str:
        return generated_files_media.normalize_transcript_output_format(value)

    def _normalize_transcript_language(self, value: Any) -> str:
        return generated_files_media.normalize_transcript_language(value)

    def _normalize_whisper_model_size(self, value: Any) -> str:
        return generated_files_media.normalize_whisper_model_size(value)

    def _normalize_whisper_device(self, value: Any) -> str:
        return generated_files_media.normalize_whisper_device(value)

    def _normalize_whisper_compute_type(self, value: Any) -> str:
        return generated_files_media.normalize_whisper_compute_type(value)

    def _load_faster_whisper_model(self, *, model_size: str, device: str, compute_type: str) -> Any:
        return generated_files_media.load_faster_whisper_model(
            self,
            model_size=model_size,
            device=device,
            compute_type=compute_type,
        )

    def _prepare_transcription_input(
        self,
        *,
        ffmpeg_path: str,
        source_path: Path,
        prepared_path: Path,
    ) -> dict[str, Any]:
        return generated_files_media.prepare_transcription_input(
            self,
            ffmpeg_path=ffmpeg_path,
            source_path=source_path,
            prepared_path=prepared_path,
        )

    def _transcribe_prepared_audio(
        self,
        *,
        model: Any,
        audio_path: Path,
        source: dict[str, Any],
        source_index: int,
        language: str,
        vad_filter: bool,
    ) -> dict[str, Any]:
        return generated_files_media.transcribe_prepared_audio(
            self,
            model=model,
            audio_path=audio_path,
            source=source,
            source_index=source_index,
            language=language,
            vad_filter=vad_filter,
        )

    def _segment_value(self, obj: Any, key: str, default: Any = None) -> Any:
        return generated_files_media.segment_value(obj, key, default)

    def _transcript_source_card(self, source: dict[str, Any]) -> dict[str, Any]:
        return generated_files_media.transcript_source_card(source)

    def _infer_transcript_title(self, *, sources: list[dict[str, Any]], output_format: str, merged: bool) -> str:
        return generated_files_media.infer_transcript_title(
            sources=sources,
            output_format=output_format,
            merged=merged,
        )

    def _store_transcript_output(
        self,
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
        return generated_files_media.store_transcript_output(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
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
            send_to_user=send_to_user,
            timestamp=timestamp,
        )

    def _render_transcript_output(
        self,
        *,
        transcripts: list[dict[str, Any]],
        output_format: str,
        title: str,
        with_timestamps: bool,
    ) -> str:
        return generated_files_io.render_transcript_output(
            self,
            transcripts=transcripts,
            output_format=output_format,
            title=title,
            with_timestamps=with_timestamps,
        )

    def _render_markdown_transcripts(self, transcripts: list[dict[str, Any]], *, title: str, with_timestamps: bool) -> str:
        return generated_files_io.render_markdown_transcripts(
            self,
            transcripts,
            title=title,
            with_timestamps=with_timestamps,
        )

    def _render_plain_transcripts(self, transcripts: list[dict[str, Any]], *, with_timestamps: bool) -> str:
        return generated_files_io.render_plain_transcripts(
            self,
            transcripts,
            with_timestamps=with_timestamps,
        )

    def _render_srt_transcripts(self, transcripts: list[dict[str, Any]]) -> str:
        return generated_files_io.render_srt_transcripts(self, transcripts)

    def _render_vtt_transcripts(self, transcripts: list[dict[str, Any]]) -> str:
        return generated_files_io.render_vtt_transcripts(self, transcripts)

    def _build_transcript_content_card(
        self,
        *,
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
        content: str,
    ) -> dict[str, Any]:
        return generated_files_cards.build_transcript_content_card(
            self,
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

    def _build_transcribe_followup(
        self,
        *,
        generated_files: list[dict[str, Any]],
        transcripts: list[dict[str, Any]],
        output_format: str,
        merge_outputs: bool,
        send_to_user: bool,
    ) -> str:
        return generated_files_cards.build_transcribe_followup(
            self,
            generated_files=generated_files,
            transcripts=transcripts,
            output_format=output_format,
            merge_outputs=merge_outputs,
            send_to_user=send_to_user,
        )

    def _estimate_transcript_duration(self, segments: list[dict[str, Any]]) -> float:
        return generated_files_media.estimate_transcript_duration(segments)

    def _format_timestamp_label(self, value: Any) -> str:
        return generated_files_media.format_timestamp_label(value)

    def _format_srt_timestamp(self, value: Any) -> str:
        return generated_files_media.format_srt_timestamp(value)

    def _format_vtt_timestamp(self, value: Any) -> str:
        return generated_files_media.format_vtt_timestamp(self, value)

    def _build_media_info_followup(
        self,
        *,
        source: dict[str, Any],
        media_info: dict[str, Any],
    ) -> str:
        return generated_files_cards.build_media_info_followup(
            self,
            source=source,
            media_info=media_info,
        )

    def _build_send_followup(self, *, generated: dict[str, Any]) -> str:
        return generated_files_cards.build_send_followup(self, generated=generated)

    def _build_send_followup_batch(
        self,
        *,
        generated_files: list[dict[str, Any]],
        unresolved: list[str],
        missing_on_disk: list[str],
        ambiguous_targets: list[str] | None = None,
    ) -> str:
        return generated_files_cards.build_send_followup_batch(
            self,
            generated_files=generated_files,
            unresolved=unresolved,
            missing_on_disk=missing_on_disk,
            ambiguous_targets=ambiguous_targets,
        )

    def _build_send_followup_missing(
        self,
        *,
        requested_targets: list[str],
        unresolved: list[str],
        missing_on_disk: list[str],
        ambiguous_targets: list[str] | None = None,
    ) -> str:
        return generated_files_cards.build_send_followup_missing(
            self,
            requested_targets=requested_targets,
            unresolved=unresolved,
            missing_on_disk=missing_on_disk,
            ambiguous_targets=ambiguous_targets,
        )

    def _build_send_file_followup_batch(
        self,
        *,
        files: list[dict[str, Any]],
        unresolved: list[str],
        missing_on_disk: list[str],
        ambiguous_targets: list[str] | None = None,
    ) -> str:
        return generated_files_cards.build_send_file_followup_batch(
            self,
            files=files,
            unresolved=unresolved,
            missing_on_disk=missing_on_disk,
            ambiguous_targets=ambiguous_targets,
        )

    def _build_send_file_followup_missing(
        self,
        *,
        requested_targets: list[str],
        unresolved: list[str],
        missing_on_disk: list[str],
        ambiguous_targets: list[str] | None = None,
    ) -> str:
        return generated_files_cards.build_send_file_followup_missing(
            self,
            requested_targets=requested_targets,
            unresolved=unresolved,
            missing_on_disk=missing_on_disk,
            ambiguous_targets=ambiguous_targets,
        )

    def _sendable_file_label(self, file_ref: dict[str, Any]) -> str:
        return generated_files_delivery.sendable_file_label(self, file_ref)

    def _normalize_send_targets(
        self,
        *,
        target: str = "latest",
        targets: list[str] | tuple[str, ...] | None = None,
    ) -> list[str]:
        return generated_files_delivery.normalize_send_targets(
            self,
            target=target,
            targets=targets,
        )

    def _render_generated_prompt_item(self, item: dict[str, Any]) -> list[str]:
        return generated_files_cards.render_generated_prompt_item(self, item)

    def _build_output_path(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        title: str,
        output_format: str,
        timestamp: int,
    ) -> Path:
        if self.ensure_storage_ready is not None:
            self.ensure_storage_ready()
        local_time = time.localtime(timestamp)
        date_slug = time.strftime("%Y-%m-%d", local_time)
        time_slug = time.strftime("%H%M%S", local_time)
        readable_title = self._safe_filename(title)[:60] or "akane_output"
        output_dir = self.base_dir / date_slug
        output_path = output_dir / f"{time_slug}_{readable_title}.{output_format}"
        sequence = 2
        while output_path.exists():
            output_path = output_dir / f"{time_slug}_{readable_title}_{sequence}.{output_format}"
            sequence += 1
        try:
            output_path.resolve(strict=False).relative_to(self.base_dir.resolve())
        except Exception:
            raise RuntimeError("generated output destination escaped the managed workspace") from None
        return output_path

    def _storage_relpath(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.base_dir)).replace("\\", "/")
        except ValueError:
            return str(path)

    def _build_ffmpeg_command(
        self,
        *,
        ffmpeg_path: str,
        source_path: Path,
        output_path: Path,
        output_format: str,
        bitrate: str,
        sample_rate: int,
        channels: int,
        media_options: dict[str, Any] | None = None,
    ) -> list[str]:
        options = media_options if isinstance(media_options, dict) else {}
        command = [ffmpeg_path, "-y"]
        start_seconds = float(options.get("start_seconds") or 0)
        if start_seconds > 0:
            command.extend(["-ss", self._format_seconds_arg(start_seconds)])
        command.extend(["-i", str(source_path)])
        duration_seconds = float(options.get("duration_seconds") or 0)
        if duration_seconds > 0:
            command.extend(["-t", self._format_seconds_arg(duration_seconds)])
        command.append("-vn")
        codec_args = {
            "mp3": ["-codec:a", "libmp3lame"],
            "wav": ["-codec:a", "pcm_s16le"],
            "flac": ["-codec:a", "flac"],
            "m4a": ["-codec:a", "aac"],
            "aac": ["-codec:a", "aac"],
            "ogg": ["-codec:a", "libvorbis"],
            "opus": ["-codec:a", "libopus"],
        }.get(output_format, [])
        command.extend(codec_args)
        filters = self._build_audio_filter_chain(options)
        if filters:
            command.extend(["-filter:a", ",".join(filters)])
        normalized_bitrate = self._normalize_bitrate(bitrate)
        if normalized_bitrate and output_format in {"mp3", "m4a", "aac", "ogg", "opus"}:
            command.extend(["-b:a", normalized_bitrate])
        if int(sample_rate or 0) > 0:
            command.extend(["-ar", str(int(sample_rate))])
        if int(channels or 0) in {1, 2}:
            command.extend(["-ac", str(int(channels))])
        command.append(str(output_path))
        return command

    def _resolve_demucs_command(self) -> list[str] | None:
        demucs_path = shutil.which("demucs")
        if demucs_path:
            return [demucs_path]
        if importlib.util.find_spec("demucs") is not None:
            return [sys.executable, "-m", "demucs.separate"]
        return None

    def _resolve_deepfilternet_runner(self) -> dict[str, Any] | None:
        for candidate in ("deepFilter", "deep-filter"):
            resolved = shutil.which(candidate)
            if resolved:
                return {"kind": "binary", "command": [resolved]}
        scripts_dir = Path(sys.executable).parent
        for candidate in ("deepFilter.exe", "deep-filter.exe", "deepFilter", "deep-filter"):
            resolved = scripts_dir / candidate
            if resolved.exists():
                return {"kind": "binary", "command": [str(resolved)]}
        if importlib.util.find_spec("df") is not None:
            return {"kind": "python", "command": [sys.executable, "-m", "df.enhance"]}
        return None

    def _run_deepfilternet_cleaning(
        self,
        *,
        runner: dict[str, Any],
        prepared_input_path: Path,
        output_root: Path,
        post_filter: bool,
    ) -> Path:
        command_prefix = list(runner.get("command") or [])
        runner_kind = str(runner.get("kind") or "").strip().lower()
        if not command_prefix:
            raise RuntimeError("DeepFilterNet 运行入口无效。")
        output_root.mkdir(parents=True, exist_ok=True)
        if runner_kind == "binary":
            command = [
                *command_prefix,
                "-m",
                "DeepFilterNet2",
                "-o",
                str(output_root),
            ]
        else:
            command = [
                *command_prefix,
                "-m",
                "DeepFilterNet2",
                "--output-dir",
                str(output_root),
            ]
        if post_filter:
            command.append("--pf")
        command.append(str(prepared_input_path))
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        if completed.returncode != 0:
            error_text = self._summarize_voice_clean_error(completed.stderr or completed.stdout or "DeepFilterNet 净化失败")
            raise RuntimeError(error_text)
        output_path = self._collect_deepfilternet_output(output_root=output_root, source_path=prepared_input_path)
        if output_path is None:
            raise RuntimeError("DeepFilterNet 已执行，但没有找到净化后的输出文件。")
        return output_path

    def _separate_audio_with_demucs_module(
        self,
        *,
        source_path: Path,
        output_root: Path,
        model_name: str = "htdemucs",
    ) -> dict[str, Path]:
        import numpy as np
        import torch
        from demucs.apply import apply_model
        from demucs.audio import AudioFile
        from demucs.pretrained import get_model

        def run_for_device(device_name: str):
            model = get_model(model_name)
            model.to(device_name)
            model.eval()
            source_names = list(getattr(model, "sources", []) or [])
            samplerate = int(getattr(model, "samplerate", 44100) or 44100)
            audio_channels = int(getattr(model, "audio_channels", 2) or 2)
            waveform = AudioFile(source_path).read(
                streams=0,
                samplerate=samplerate,
                channels=audio_channels,
            )
            if waveform.dim() == 2:
                waveform = waveform[None]
            waveform = waveform.to(device_name)
            with torch.no_grad():
                separated = apply_model(
                    model,
                    waveform,
                    device=device_name,
                    progress=False,
                )
            if hasattr(model, "models") and getattr(model, "models", None):
                source_names = list(getattr(model.models[0], "sources", source_names) or source_names)
            return separated[0].detach().cpu(), source_names, samplerate

        preferred_device = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            separated, source_names, samplerate = run_for_device(preferred_device)
        except RuntimeError as exc:
            lowered = str(exc).lower()
            if preferred_device == "cuda" and any(token in lowered for token in ("out of memory", "cuda", "cudnn")):
                torch.cuda.empty_cache()
                separated, source_names, samplerate = run_for_device("cpu")
            else:
                raise

        vocals_index = next(
            (index for index, name in enumerate(source_names) if str(name).strip().lower() == "vocals"),
            -1,
        )
        if vocals_index < 0:
            raise RuntimeError("Demucs 没有返回 vocals 轨道。")

        output_root.mkdir(parents=True, exist_ok=True)
        vocals = separated[vocals_index]
        instrumental_indices = [index for index in range(len(source_names)) if index != vocals_index]
        if instrumental_indices:
            instrumental = separated[instrumental_indices].sum(dim=0)
        else:
            instrumental = -vocals

        vocals_path = output_root / "vocals.wav"
        instrumental_path = output_root / "instrumental.wav"
        self._write_audio_tensor_to_wav(vocals, vocals_path, sample_rate=samplerate)
        self._write_audio_tensor_to_wav(instrumental, instrumental_path, sample_rate=samplerate)
        return {
            "vocals": vocals_path,
            "instrumental": instrumental_path,
        }

    def _write_audio_tensor_to_wav(
        self,
        tensor: Any,
        output_path: Path,
        *,
        sample_rate: int,
    ) -> None:
        import numpy as np

        if hasattr(tensor, "detach"):
            tensor = tensor.detach()
        if hasattr(tensor, "cpu"):
            tensor = tensor.cpu()
        if hasattr(tensor, "dim") and tensor.dim() == 1:
            tensor = tensor.unsqueeze(0)
        if hasattr(tensor, "transpose"):
            array = tensor.transpose(0, 1).contiguous().numpy()
        else:
            array = np.asarray(tensor)
        array = np.clip(array, -1.0, 1.0)
        pcm16 = np.round(array * 32767.0).astype(np.int16)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), "wb") as wf:
            channels = int(pcm16.shape[1]) if pcm16.ndim > 1 else 1
            wf.setnchannels(channels)
            wf.setsampwidth(2)
            wf.setframerate(int(sample_rate or 44100))
            wf.writeframes(pcm16.tobytes())

    def _collect_demucs_stems(self, output_root: Path) -> dict[str, Path]:
        stems: dict[str, Path] = {}
        if not output_root.exists():
            return stems
        for path in output_root.rglob("*"):
            if not path.is_file():
                continue
            name = path.name.lower()
            if name == "vocals.wav":
                stems["vocals"] = path
            elif name in {"no_vocals.wav", "instrumental.wav", "accompaniment.wav"}:
                stems["instrumental"] = path
        return stems

    def _summarize_audio_separation_error(self, raw: str) -> str:
        text = str(raw or "").replace("\r", "\n")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        cleaned = [
            line
            for line in lines
            if "seconds/s" not in line
            and "Selected model is a bag" not in line
            and "Separating track " not in line
            and not line.startswith(("0%", "1%", "2%", "3%", "4%", "5%", "6%", "7%", "8%", "9%"))
        ]
        if not cleaned:
            return "音频分离失败，但没有拿到明确的错误细节。"
        return " | ".join(cleaned[-8:])[:500]

    def _collect_deepfilternet_output(self, *, output_root: Path, source_path: Path) -> Path | None:
        if not output_root.exists():
            return None
        preferred_stem = source_path.stem.lower()
        candidates = [path for path in output_root.rglob("*.wav") if path.is_file()]
        if not candidates:
            candidates = [path for path in output_root.rglob("*") if path.is_file()]
        if not candidates:
            return None
        exact = [path for path in candidates if path.stem.lower() == preferred_stem]
        if exact:
            return sorted(exact)[0]
        partial = [path for path in candidates if preferred_stem in path.stem.lower()]
        if partial:
            return sorted(partial)[0]
        return sorted(candidates)[0]

    def _summarize_voice_clean_error(self, raw: str) -> str:
        text = str(raw or "").replace("\r", "\n")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        cleaned = [
            line
            for line in lines
            if "processing time total" not in line.lower()
            and "enhance.py" not in line.lower()
            and "log level" not in line.lower()
        ]
        if not cleaned:
            return "人声净化失败，但没有拿到明确的错误细节。"
        return " | ".join(cleaned[-8:])[:500]

    def _render_separated_stem_output(
        self,
        *,
        stem_source_path: Path,
        output_path: Path,
        output_format: str,
        ffmpeg_path: str = "",
    ) -> None:
        if output_format == "wav":
            shutil.copy2(stem_source_path, output_path)
            return
        if not ffmpeg_path:
            raise RuntimeError("缺少 ffmpeg，无法把分离结果转成目标格式。")
        command = self._build_ffmpeg_command(
            ffmpeg_path=ffmpeg_path,
            source_path=stem_source_path,
            output_path=output_path,
            output_format=output_format,
            bitrate="",
            sample_rate=0,
            channels=0,
            media_options={},
        )
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if completed.returncode != 0 or not output_path.exists():
            error_text = (completed.stderr or completed.stdout or "ffmpeg 转换失败").strip()[:500]
            raise RuntimeError(error_text)

    def _render_clean_voice_output(
        self,
        *,
        cleaned_source_path: Path,
        output_path: Path,
        output_format: str,
        ffmpeg_path: str,
    ) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_format == "wav":
            shutil.copy2(cleaned_source_path, output_path)
            return
        command = self._build_ffmpeg_command(
            ffmpeg_path=ffmpeg_path,
            source_path=cleaned_source_path,
            output_path=output_path,
            output_format=output_format,
            bitrate="",
            sample_rate=0,
            channels=0,
            media_options={},
        )
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if completed.returncode != 0 or not output_path.exists():
            error_text = (completed.stderr or completed.stdout or "ffmpeg 转换失败").strip()[:500]
            raise RuntimeError(error_text)

    def _is_video_media_format(self, value: str) -> bool:
        return str(value or "").strip().lower().lstrip(".") in VIDEO_MEDIA_EXTENSIONS

    def _normalize_media_edit_options(
        self,
        *,
        start_time: Any,
        end_time: Any,
        normalize_volume: Any,
        volume_gain_db: Any,
        trim_silence: Any,
        fade_in_seconds: Any,
        fade_out_seconds: Any,
        speed_ratio: Any,
    ) -> dict[str, Any]:
        start_present = self._has_value(start_time)
        end_present = self._has_value(end_time)
        start_seconds = self._parse_time_seconds(start_time) if start_present else None
        end_seconds = self._parse_time_seconds(end_time) if end_present else None
        if start_present and start_seconds is None:
            return {
                "error": "invalid_start_time",
                "followup_context": "你刚刚想截取媒体片段，但开始时间格式不稳定。请自然向用户确认开始时间。",
            }
        if end_present and end_seconds is None:
            return {
                "error": "invalid_end_time",
                "followup_context": "你刚刚想截取媒体片段，但结束时间格式不稳定。请自然向用户确认结束时间。",
            }
        start_value = float(start_seconds or 0)
        end_value = float(end_seconds) if end_seconds is not None else None
        if end_value is not None and end_value <= start_value:
            return {
                "error": "invalid_time_range",
                "followup_context": "你刚刚想截取媒体片段，但结束时间不晚于开始时间。请自然向用户确认要截哪一段。",
            }
        duration = (end_value - start_value) if end_value is not None else 0.0
        fade_in = self._normalize_media_seconds(fade_in_seconds, maximum=600.0)
        fade_out = self._normalize_media_seconds(fade_out_seconds, maximum=600.0)
        speed = self._normalize_speed_ratio(speed_ratio)
        gain_db = self._normalize_volume_gain_db(volume_gain_db)
        return {
            "start_seconds": round(start_value, 3),
            "end_seconds": round(end_value, 3) if end_value is not None else None,
            "duration_seconds": round(duration, 3) if duration > 0 else 0.0,
            "normalize_volume": self._coerce_bool(normalize_volume, default=False),
            "volume_gain_db": gain_db,
            "trim_silence": self._coerce_bool(trim_silence, default=False),
            "fade_in_seconds": fade_in,
            "fade_out_seconds": fade_out,
            "speed_ratio": speed,
        }

    def _has_value(self, value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        return True

    def _parse_time_seconds(self, value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            seconds = float(value)
            return seconds if seconds >= 0 else None
        text = str(value or "").strip().lower()
        if not text:
            return None
        text = text.replace("秒钟", "秒").replace("分钟", "分")
        text = text.replace("seconds", "s").replace("second", "s").replace("secs", "s").replace("sec", "s")
        text = text.replace("minutes", "m").replace("minute", "m").replace("mins", "m").replace("min", "m")

        chinese = re.fullmatch(r"(?:(\d+(?:\.\d+)?)\s*分)?\s*(?:(\d+(?:\.\d+)?)\s*秒?)?", text)
        if chinese and (chinese.group(1) or chinese.group(2)):
            minutes = float(chinese.group(1) or 0)
            seconds = float(chinese.group(2) or 0)
            return minutes * 60 + seconds

        suffix = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([sm])?", text)
        if suffix:
            amount = float(suffix.group(1))
            unit = suffix.group(2) or "s"
            return amount * 60 if unit == "m" else amount

        if ":" in text:
            parts = text.split(":")
            if not 2 <= len(parts) <= 3:
                return None
            try:
                numbers = [float(part.strip()) for part in parts]
            except Exception:
                return None
            if any(number < 0 for number in numbers):
                return None
            if len(numbers) == 2:
                minutes, seconds = numbers
                return minutes * 60 + seconds
            hours, minutes, seconds = numbers
            return hours * 3600 + minutes * 60 + seconds
        return None

    def _normalize_media_seconds(self, value: Any, *, maximum: float) -> float:
        if not self._has_value(value):
            return 0.0
        seconds = self._parse_time_seconds(value)
        if seconds is None:
            return 0.0
        return round(max(0.0, min(float(seconds), maximum)), 3)

    def _normalize_speed_ratio(self, value: Any) -> float:
        if not self._has_value(value):
            return 1.0
        if isinstance(value, (int, float)):
            speed = float(value)
        else:
            text = str(value or "").strip().lower()
            text = text.replace("倍速", "").replace("倍", "").replace("x", "").strip()
            try:
                if text.endswith("%"):
                    speed = float(text[:-1]) / 100.0
                else:
                    speed = float(text)
            except Exception:
                return 1.0
        if speed <= 0:
            return 1.0
        return round(max(0.25, min(speed, 4.0)), 4)

    def _normalize_volume_gain_db(self, value: Any) -> float:
        if not self._has_value(value):
            return 0.0
        if isinstance(value, (int, float)):
            amount = float(value)
        else:
            text = str(value or "").strip().lower().replace(" ", "")
            text = text.replace("分贝", "db")
            text = text[:-2] if text.endswith("db") else text
            try:
                amount = float(text)
            except Exception:
                return 0.0
        return round(max(-24.0, min(amount, 24.0)), 2)

    def _build_audio_filter_chain(self, options: dict[str, Any]) -> list[str]:
        filters: list[str] = []
        if bool(options.get("trim_silence")):
            filters.extend(
                [
                    "silenceremove=start_periods=1:start_duration=0.12:"
                    "start_threshold=-50dB:start_silence=0.02:detection=peak",
                    "areverse",
                    "silenceremove=start_periods=1:start_duration=0.12:"
                    "start_threshold=-50dB:start_silence=0.02:detection=peak",
                    "areverse",
                ]
            )
        speed = float(options.get("speed_ratio") or 1.0)
        if abs(speed - 1.0) > 0.001:
            filters.extend(f"atempo={self._format_filter_number(item)}" for item in self._split_atempo_filters(speed))
        if bool(options.get("normalize_volume")):
            filters.append("loudnorm")
        gain_db = float(options.get("volume_gain_db") or 0)
        if abs(gain_db) > 0.01:
            filters.append(f"volume={self._format_filter_number(gain_db)}dB")
        fade_in = float(options.get("fade_in_seconds") or 0)
        if fade_in > 0:
            filters.append(f"afade=t=in:st=0:d={self._format_filter_number(fade_in)}")
        fade_out = float(options.get("fade_out_seconds") or 0)
        output_duration = float(options.get("output_duration_seconds") or 0)
        if fade_out > 0 and output_duration > 0:
            start = max(0.0, output_duration - fade_out)
            filters.append(
                "afade=t=out:"
                f"st={self._format_filter_number(start)}:"
                f"d={self._format_filter_number(min(fade_out, output_duration))}"
            )
        return filters

    def _build_basic_voice_clean_filter_chain(self, *, mode: str, post_filter: bool) -> list[str]:
        filters: list[str] = ["highpass=f=70", "lowpass=f=12000"]
        if mode == "denoise":
            filters.append("afftdn=nr=18:nf=-28")
        elif mode == "voice_focus":
            filters.append("afftdn=nr=20:nf=-30")
        elif mode in {"dereverb", "deecho"}:
            filters.append("afftdn=nr=22:nf=-26")
        else:
            filters.append("afftdn=nr=18:nf=-28")
        if post_filter:
            filters.append("afftdn=nr=24:nf=-24")
        return filters

    def _split_atempo_filters(self, speed: float) -> list[float]:
        factors: list[float] = []
        remaining = max(0.25, min(float(speed or 1.0), 4.0))
        while remaining > 2.0:
            factors.append(2.0)
            remaining /= 2.0
        while remaining < 0.5:
            factors.append(0.5)
            remaining /= 0.5
        factors.append(remaining)
        return factors

    def _probe_media_duration(self, *, ffprobe_path: str, source_path: Path) -> float | None:
        return generated_files_media.probe_media_duration(ffprobe_path=ffprobe_path, source_path=source_path)

    def _probe_media_info(self, *, ffprobe_path: str, source_path: Path) -> dict[str, Any] | None:
        return generated_files_media.probe_media_info(ffprobe_path=ffprobe_path, source_path=source_path)

    def _normalize_media_probe_info(
        self,
        data: dict[str, Any],
        *,
        source: dict[str, Any],
        source_path: Path,
    ) -> dict[str, Any]:
        return generated_files_media.normalize_media_probe_info(
            self,
            data,
            source=source,
            source_path=source_path,
        )

    def _normalize_audio_stream(self, stream: dict[str, Any]) -> dict[str, Any]:
        return generated_files_media.normalize_audio_stream(self, stream)

    def _normalize_video_stream(self, stream: dict[str, Any]) -> dict[str, Any]:
        return generated_files_media.normalize_video_stream(self, stream)

    def _parse_frame_rate(self, value: Any) -> float | None:
        return generated_files_media.parse_frame_rate(value)

    def _safe_int(self, value: Any) -> int | None:
        return generated_files_media.safe_int(value)

    def _safe_float(self, value: Any) -> float | None:
        return generated_files_media.safe_float(value)

    def _format_duration_label(self, value: Any) -> str:
        return generated_files_media.format_duration_label(value)

    def _format_file_size(self, value: Any) -> str:
        return generated_files_media.format_file_size(value)

    def _format_bitrate(self, value: Any) -> str:
        return generated_files_media.format_bitrate(value)

    def _format_seconds_arg(self, value: float) -> str:
        return self._format_filter_number(value)

    def _format_filter_number(self, value: float) -> str:
        text = f"{float(value):.3f}".rstrip("0").rstrip(".")
        return text or "0"

    def _coerce_bool(self, value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "开启", "打开", "需要", "是"}:
            return True
        if text in {"0", "false", "no", "n", "off", "关闭", "不需要", "否"}:
            return False
        return default

    def _normalize_output_format(self, value: Any) -> str:
        text = str(value or "md").strip().lower().lstrip(".")
        aliases = {
            "markdown": "md",
            "text": "txt",
            "plain": "txt",
            "word": "docx",
            "excel": "xlsx",
        }
        return aliases.get(text, text)

    def _normalize_media_output_format(self, value: Any) -> str:
        text = str(value or "mp3").strip().lower().lstrip(".")
        aliases = {
            "mpeg3": "mp3",
            "wave": "wav",
            "waveform": "wav",
            "mp4a": "m4a",
            "oga": "ogg",
        }
        return aliases.get(text, text)

    def _normalize_voice_clean_mode(self, value: Any) -> str:
        text = str(value or "denoise").strip().lower()
        aliases = {
            "denoise": "denoise",
            "noise": "denoise",
            "remove_noise": "denoise",
            "降噪": "denoise",
            "去噪": "denoise",
            "dereverb": "dereverb",
            "reverb": "dereverb",
            "去混响": "dereverb",
            "deecho": "deecho",
            "echo": "deecho",
            "去回声": "deecho",
            "voice_focus": "voice_focus",
            "speech": "voice_focus",
            "focus": "voice_focus",
            "人声聚焦": "voice_focus",
            "净化人声": "voice_focus",
        }
        return aliases.get(text, "denoise")

    def _normalize_voice_clean_quality(self, value: Any) -> str:
        text = str(value or "auto").strip().lower()
        aliases = {
            "auto": "auto",
            "默认": "auto",
            "ai": "ai",
            "model": "ai",
            "deepfilternet": "ai",
            "basic": "basic",
            "ffmpeg": "basic",
            "基础": "basic",
        }
        return aliases.get(text, "auto")

    def _normalize_bitrate(self, value: Any) -> str:
        text = str(value or "").strip().lower().replace(" ", "")
        if not text:
            return ""
        match = re.match(r"^(\d{2,4})(k|kbps|m|mbps)?$", text)
        if not match:
            return ""
        amount = int(match.group(1))
        suffix = match.group(2) or "k"
        if suffix in {"m", "mbps"}:
            return f"{max(1, min(amount, 10))}m"
        return f"{max(16, min(amount, 512))}k"

    def _normalize_formatting(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, Any] = {}
        header = self._normalize_style_rule(value.get("header") or value.get("table_header"))
        if header:
            normalized["header"] = header

        list_specs = {
            "columns": 40,
            "rows": 80,
            "cells": 120,
            "highlights": 80,
            "paragraphs": 80,
            "row_rules": 80,
        }
        for key, limit in list_specs.items():
            items = value.get(key)
            if not isinstance(items, list):
                continue
            cleaned: list[dict[str, Any]] = []
            for item in items[:limit]:
                if not isinstance(item, dict):
                    continue
                rule = self._normalize_style_rule(item)
                if not rule:
                    continue
                for selector_key in (
                    "match_header",
                    "header",
                    "column",
                    "letter",
                    "index",
                    "row",
                    "row_index",
                    "start",
                    "end",
                    "from",
                    "to",
                    "text",
                    "contains",
                    "paragraph_index",
                ):
                    if selector_key in item and item.get(selector_key) not in (None, ""):
                        rule[selector_key] = str(item.get(selector_key)).strip()[:120]
                if isinstance(item.get("where"), dict):
                    rule["where"] = self._normalize_where_rule(item.get("where"))
                cleaned.append(rule)
            if cleaned:
                normalized[key] = cleaned

        if "auto_width" in value:
            normalized["auto_width"] = bool(value.get("auto_width"))
        return normalized

    def _normalize_style_rule(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        rule: dict[str, Any] = {}
        for key in ("bold", "italic"):
            if key in value:
                rule[key] = bool(value.get(key))
        for key in ("font_color", "fill_color", "highlight_color"):
            color = self._normalize_color(value.get(key))
            if color:
                rule[key] = color
        return rule

    def _normalize_where_rule(self, value: dict[str, Any]) -> dict[str, Any]:
        rule: dict[str, Any] = {}
        for key in ("match_text", "contains", "column", "match_header", "eq", "ne", "lt", "lte", "gt", "gte"):
            if key in value and value.get(key) not in (None, ""):
                rule[key] = str(value.get(key)).strip()[:120]
        return rule

    def _normalize_color(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        aliases = {
            "red": "FF0000",
            "红": "FF0000",
            "红色": "FF0000",
            "blue": "0000FF",
            "蓝": "0000FF",
            "蓝色": "0000FF",
            "green": "00AA00",
            "绿": "00AA00",
            "绿色": "00AA00",
            "yellow": "FFFF00",
            "黄": "FFFF00",
            "黄色": "FFFF00",
            "orange": "FFC000",
            "橙": "FFC000",
            "橙色": "FFC000",
            "gray": "D9D9D9",
            "grey": "D9D9D9",
            "灰": "D9D9D9",
            "灰色": "D9D9D9",
            "pink": "F4CCCC",
            "粉": "F4CCCC",
            "粉色": "F4CCCC",
        }
        if text in aliases:
            return aliases[text]
        text = text.lstrip("#")
        if re.fullmatch(r"[0-9a-fA-F]{6}", text):
            return text.upper()
        if re.fullmatch(r"[0-9a-fA-F]{8}", text):
            return text[-6:].upper()
        return ""

    def _resolve_column_index(self, selector: dict[str, Any], headers: list[str]) -> int | None:
        raw_index = selector.get("column_index") or selector.get("index")
        index = self._coerce_int(raw_index)
        if index:
            return index

        column = selector.get("column")
        if column is not None:
            column_text = str(column).strip()
            column_index = self._coerce_int(column_text)
            if column_index:
                return column_index
            letter_index = self._column_letter_index(column_text)
            if letter_index:
                return letter_index
            match = self._match_header_index(column_text, headers)
            if match:
                return match

        letter = str(selector.get("letter") or "").strip()
        if letter:
            return self._column_letter_index(letter)

        match_header = str(selector.get("match_header") or selector.get("header") or "").strip()
        if match_header:
            return self._match_header_index(match_header, headers)
        return None

    def _match_header_index(self, target: str, headers: list[str]) -> int | None:
        clean_target = str(target or "").strip()
        if not clean_target:
            return None
        for index, header in enumerate(headers, start=1):
            if str(header or "").strip() == clean_target:
                return index
        for index, header in enumerate(headers, start=1):
            if clean_target in str(header or "").strip() or str(header or "").strip() in clean_target:
                return index
        return None

    def _column_letter_index(self, value: str) -> int | None:
        text = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{1,3}", text):
            return None
        from openpyxl.utils import column_index_from_string  # type: ignore

        try:
            return int(column_index_from_string(text))
        except Exception:
            return None

    def _resolve_row_range(self, rule: dict[str, Any], *, max_row: int) -> tuple[int | None, int]:
        start = self._coerce_int(rule.get("start") or rule.get("from") or rule.get("row_start"))
        end = self._coerce_int(rule.get("end") or rule.get("to") or rule.get("row_end"))
        index = self._coerce_int(rule.get("row") or rule.get("row_index") or rule.get("index"))
        if index:
            start = index
            end = index
        if not start:
            return None, 0
        start = max(1, min(max_row, start))
        end = max(start, min(max_row, end or start))
        return start, end

    def _coerce_int(self, value: Any) -> int | None:
        text = str(value or "").strip()
        if not text:
            return None
        match = re.search(r"\d+", text)
        if not match:
            return None
        try:
            return int(match.group(0))
        except Exception:
            return None

    def _coerce_float(self, value: Any) -> float | None:
        text = str(value or "").strip().replace(",", "")
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            return None
        try:
            return float(match.group(0))
        except Exception:
            return None

    def _normalize_title(self, value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip())[:80]

    def _infer_title(self, *, task: str, output_format: str, sources: list[dict[str, Any]]) -> str:
        if sources:
            source_title = str(sources[0].get("title") or "").strip()
            if source_title:
                stem = Path(source_title).stem
                return f"{stem}_整理"
        clean_task = re.sub(r"[^\w\u4e00-\u9fff]+", "_", str(task or "").strip()).strip("_")
        return (clean_task[:32] if clean_task else f"生成文件_{output_format}")

    def _safe_filename(self, value: Any) -> str:
        text = str(value or "").strip()
        text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text)
        text = re.sub(r"\s+", "_", text).strip("._ ")
        return text or "untitled"

    def _safe_sheet_name(self, value: Any) -> str:
        text = re.sub(r"[\[\]:*?/\\]+", "_", str(value or "Sheet").strip())
        return (text or "Sheet")[:31]

    def _mime_type_for_format(self, output_format: str) -> str:
        return {
            "txt": "text/plain; charset=utf-8",
            "md": "text/markdown; charset=utf-8",
            "html": "text/html; charset=utf-8",
            "json": "application/json",
            "csv": "text/csv; charset=utf-8",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "pdf": "application/pdf",
            "srt": "application/x-subrip; charset=utf-8",
            "vtt": "text/vtt; charset=utf-8",
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "flac": "audio/flac",
            "m4a": "audio/mp4",
            "aac": "audio/aac",
            "ogg": "audio/ogg",
            "opus": "audio/opus",
            "zip": "application/zip",
        }.get(output_format, "application/octet-stream")

    def _normalize_table_rows(self, value: list[list[Any]] | None) -> list[list[str]]:
        if not isinstance(value, list):
            return []
        rows: list[list[str]] = []
        for row in value[:1000]:
            if not isinstance(row, (list, tuple)):
                continue
            cells = [str(cell or "").strip()[:500] for cell in list(row)[:50]]
            if any(cells):
                rows.append(cells)
        return rows

    def _extract_table_rows_from_markdown(self, content: str) -> list[list[str]]:
        rows: list[list[str]] = []
        for line in str(content or "").splitlines():
            stripped = line.strip()
            if not stripped.startswith("|") or not stripped.endswith("|"):
                continue
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells and not all(re.fullmatch(r":?-{2,}:?", cell or "") for cell in cells):
                rows.append(cells)
        return rows[:1000]

    def _markdown_table(self, rows: list[list[str]]) -> list[str]:
        if not rows:
            return []
        width = max(len(row) for row in rows)
        normalized = [row + [""] * (width - len(row)) for row in rows]
        lines = ["| " + " | ".join(normalized[0]) + " |"]
        lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
        for row in normalized[1:]:
            lines.append("| " + " | ".join(row) + " |")
        return lines

    def _split_markdown_blocks(self, content: str) -> list[str]:
        blocks = re.split(r"\n\s*\n", str(content or "").strip())
        return [block.strip() for block in blocks if block.strip()]

    def _looks_like_json(self, content: str) -> bool:
        try:
            json.loads(content)
            return True
        except Exception:
            return False

    def _escape_pdf_text(self, value: str) -> str:
        return (
            str(value or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
