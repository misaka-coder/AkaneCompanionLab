from __future__ import annotations

import hashlib
import mimetypes
import os
import json
import re
import shutil
import time
import zipfile
from pathlib import Path
from services.asr_business import module as asr_business_module

from typing import Any, Callable

from . import generated_files_cards, generated_files_delivery, generated_files_io, generated_files_media
from .attachment_inbox import AttachmentInboxService
from .store import MemoryStore


SUPPORTED_OUTPUT_FORMATS = {"txt", "md", "docx", "xlsx", "pdf", "json", "csv", "html"}
MEDIA_OUTPUT_FORMATS = {"mp3", "wav", "flac", "m4a", "aac", "ogg", "opus"}
TRANSCRIPT_OUTPUT_FORMATS = {"md", "txt", "srt", "vtt", "json"}
REGISTERED_ARTIFACT_FORMATS = (
    SUPPORTED_OUTPUT_FORMATS | MEDIA_OUTPUT_FORMATS | TRANSCRIPT_OUTPUT_FORMATS | {"png", "zip"}
)
_GENERIC_ARTIFACT_FORMAT_RE = re.compile(r"^[a-z0-9][a-z0-9_+-]{0,15}$")
TEXT_INSPECT_FORMATS = {"txt", "md", "json", "csv", "html", "srt", "vtt", "xml", "log", "yaml", "yml"}
PROTECTED_MEDIA_EXTENSIONS = {"kgm", "ncm", "qmc", "qmc0", "qmc3", "mflac", "mgg", "tkm"}
VIDEO_MEDIA_EXTENSIONS = {"mp4", "mov", "mkv", "webm", "avi"}
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
        self._whisper_model_cache: dict[tuple[str, str, str, str], Any] = {}


    def media_inspection_status(self) -> dict[str, Any]:
        ffprobe_path = shutil.which("ffprobe")
        return {
            "enabled": bool(ffprobe_path),
            "status": "ready" if ffprobe_path else "missing_executor",
            "reason": "" if ffprobe_path else "ffprobe_not_found",
            "provider": "ffprobe" if ffprobe_path else "",
        }

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

    def allocate_output_path(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        title: str,
        output_format: str,
        timestamp: int | None = None,
        allow_generic_format: bool = False,
    ) -> Path:
        """Reserve a safe path inside GeneratedFileStore-managed output storage."""
        normalized_format = str(output_format or "").strip().lower().lstrip(".")
        if normalized_format not in REGISTERED_ARTIFACT_FORMATS and not (
            allow_generic_format and _GENERIC_ARTIFACT_FORMAT_RE.fullmatch(normalized_format)
        ):
            raise ValueError("generated output format is invalid")
        return self._build_output_path(
            profile_user_id=profile_user_id,
            session_id=session_id,
            title=title,
            output_format=normalized_format,
            timestamp=int(timestamp or time.time()),
        )

    def register_workspace_artifact(
        self, *, source: Path, root: Path, profile_user_id: str, session_id: str,
        created_by_tool: str, timestamp: int, source_ids=None, max_bytes: int = 256 * 1024 * 1024,
    ) -> dict[str, Any]:
        """Copy authorized workspace bytes into the ordinary artifact store unchanged."""
        source, root = Path(source).absolute(), Path(root).resolve(strict=True)
        resolved = source.resolve(strict=True)
        resolved.relative_to(root)
        if resolved != source or not resolved.is_file():
            raise ValueError("artifact_source_not_regular")
        extension = source.suffix.lstrip(".").lower()
        target = self.allocate_output_path(profile_user_id=profile_user_id, session_id=session_id,
            title=source.stem, output_format=extension, timestamp=timestamp, allow_generic_format=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        created = False
        try:
            with source.open("rb") as original, target.open("xb") as copied:
                created = True
                before = os.fstat(original.fileno())
                while chunk := original.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("artifact_source_too_large")
                    copied.write(chunk)
                    digest.update(chunk)
                after = os.fstat(original.fileno())
                current = source.stat()
                fingerprint = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
                if fingerprint(before) != fingerprint(after) or fingerprint(after) != fingerprint(current):
                    raise ValueError("artifact_source_changed")
            generated = self.register_generated_artifact(
                profile_user_id=profile_user_id, session_id=session_id, output_path=target,
                output_title=source.stem, output_format=extension,
                mime_type=mimetypes.guess_type(source.name)[0] or "application/octet-stream",
                content_card={"sha256": digest.hexdigest()}, summary=f"Workspace artifact: {source.name}",
                created_by_tool=created_by_tool, source_ids=source_ids, timestamp=timestamp,
                send_to_user=False, allow_generic_format=True,
            )
            return {**generated, "sha256": digest.hexdigest()}
        except Exception:
            if created:
                target.unlink(missing_ok=True)
            raise

    def register_generated_artifact(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        output_path: Path,
        output_title: str,
        output_format: str,
        mime_type: str,
        content_card: dict[str, Any],
        summary: str,
        created_by_tool: str,
        source_ids: list[str] | tuple[str, ...] | None = None,
        send_to_user: bool = False,
        timestamp: int | None = None,
        allow_generic_format: bool = False,
        revision_of: str = "",
    ) -> dict[str, Any]:
        """Register a non-empty artifact already rendered into managed output storage."""
        target = Path(output_path)
        if not self.is_managed_storage_path(target):
            raise RuntimeError("generated artifact is outside managed output storage")
        if not target.is_file():
            raise RuntimeError("generated artifact was not created")
        file_size = target.stat().st_size
        if file_size <= 0:
            raise RuntimeError("generated artifact is empty")
        normalized_format = str(output_format or target.suffix).strip().lower().lstrip(".")
        if normalized_format not in REGISTERED_ARTIFACT_FORMATS and not (
            allow_generic_format and _GENERIC_ARTIFACT_FORMAT_RE.fullmatch(normalized_format)
        ):
            raise RuntimeError("generated artifact format is unsupported")
        if target.suffix.lower() != f".{normalized_format}":
            raise RuntimeError("generated artifact format does not match its file extension")
        parent = None
        if revision_of:
            parent = self.resolve_generated_artifact(
                profile_user_id=profile_user_id, session_id=session_id, target=revision_of,
            )
            if parent is None:
                raise RuntimeError("generated_artifact_revision_source_unavailable")
        provenance = list(source_ids or ())
        if parent:
            for source_id in [parent["generated_id"], *list(parent.get("source_ids") or ())]:
                if source_id not in provenance:
                    provenance.append(source_id)
        generated = self.store.add_generated_file(
            profile_user_id=profile_user_id,
            session_id=session_id,
            output_title=str(output_title or target.stem).strip(),
            output_format=normalized_format,
            storage_relpath=self._storage_relpath(target),
            mime_type=str(mime_type or "application/octet-stream").strip(),
            file_ext=normalized_format,
            file_size=file_size,
            source_ids=provenance,
            content_card=content_card if isinstance(content_card, dict) else {},
            summary=str(summary or "").strip(),
            created_by_tool=str(created_by_tool or "").strip(),
            version_of_generated_id=str(parent.get("generated_id") or "") if parent else "",
            version_no=int(parent.get("version_no") or 1) + 1 if parent else 1,
            delivery_status="pending" if send_to_user else "not_requested",
            timestamp=timestamp,
        )
        generated["absolute_path"] = str(self.absolute_path(generated))
        return generated

    def resolve_generated_artifact(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
    ) -> dict[str, Any] | None:
        """Resolve an exact generated id/handle and verify its managed file still exists."""
        normalized = str(target or "").strip()
        if not normalized:
            return None
        if normalized.lower().startswith("generated::"):
            generated = self.store.get_generated_file(
                profile_user_id=profile_user_id,
                session_id=session_id,
                generated_id=normalized,
            )
        else:
            generated = self.store.find_generated_file(
                profile_user_id=profile_user_id,
                session_id=session_id,
                query=normalized,
                statuses=["ready"],
            )
        if not isinstance(generated, dict):
            return None
        exact_values = {
            str(generated.get("generated_id") or "").strip().lower(),
            str(generated.get("generated_handle") or "").strip().lower(),
        }
        if normalized.lower() not in exact_values:
            return None
        if str(generated.get("status") or "").strip().lower() != "ready":
            return None
        path = self.absolute_path(generated)
        if not self.is_managed_storage_path(path) or not path.is_file() or path.stat().st_size <= 0:
            return None
        resolved = dict(generated)
        resolved["absolute_path"] = str(path)
        return resolved

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

    def resolve_input_resource(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
        timestamp: int | None,
    ) -> dict[str, Any] | None:
        """Resolve a workspace handle (``file_*``/``img_*``/``audio_*``/``gen_*``)
        to a physical file for staging by the execution resource bridge.

        Returns a host-only file ref with ``absolute_path`` for copying; the
        model never sees this path.  Resolution is scoped to the current user
        and session exactly like ``send_file`` resolution.
        """
        file_ref, _error = self._resolve_sendable_file(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=str(target or "").strip(),
            timestamp=timestamp,
        )
        if not isinstance(file_ref, dict) or not str(file_ref.get("absolute_path") or "").strip():
            return None
        return {
            "absolute_path": str(file_ref.get("absolute_path") or "").strip(),
            "source_type": str(file_ref.get("source_type") or "").strip(),
            "source_id": str(file_ref.get("source_id") or "").strip(),
            "handle": str(file_ref.get("handle") or "").strip(),
            "name": str(file_ref.get("name") or "").strip(),
        }

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


    def _normalize_transcript_language(self, value):
        return asr_business_module("compatibility").normalize_language(value)

    def _normalize_whisper_model_size(self, value):
        return asr_business_module("compatibility").normalize_model(value)

    def _normalize_whisper_device(self, value):
        return asr_business_module("compatibility").normalize_device(value)

    def _normalize_whisper_compute_type(self, value):
        return asr_business_module("compatibility").normalize_compute(value)

    def _load_faster_whisper_model(self, *, model_size, device, compute_type, download_root=None):
        return asr_business_module("compatibility").cached_model(
            self._whisper_model_cache, model_size=model_size, device=device,
            compute_type=compute_type, download_root=download_root,
        )

    def _prepare_transcription_input(self, *, ffmpeg_path, source_path, prepared_path):
        return asr_business_module("compatibility").prepare_input(
            ffmpeg_path=ffmpeg_path, source_path=source_path, prepared_path=prepared_path,
        )

    def _transcribe_prepared_audio(self, **kwargs):
        return asr_business_module("compatibility").transcribe_prepared(**kwargs)

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
    ) -> list[str]:
        command = [ffmpeg_path, "-y", "-i", str(source_path), "-vn"]
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
        normalized_bitrate = self._normalize_bitrate(bitrate)
        if normalized_bitrate and output_format in {"mp3", "m4a", "aac", "ogg", "opus"}:
            command.extend(["-b:a", normalized_bitrate])
        if int(sample_rate or 0) > 0:
            command.extend(["-ar", str(int(sample_rate))])
        if int(channels or 0) in {1, 2}:
            command.extend(["-ac", str(int(channels))])
        command.append(str(output_path))
        return command


    def _is_video_media_format(self, value: str) -> bool:
        return str(value or "").strip().lower().lstrip(".") in VIDEO_MEDIA_EXTENSIONS

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
            "png": "image/png",
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
