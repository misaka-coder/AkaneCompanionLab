from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from services.asr_business import module as asr_business_module

from typing import Any, Callable

from . import generated_files_cards, generated_files_delivery, generated_files_media
from .attachment_inbox import AttachmentInboxService
from .store import MemoryStore


DOCUMENT_ARTIFACT_FORMATS = {"txt", "md", "docx", "xlsx", "pdf", "json", "csv", "html"}
MEDIA_OUTPUT_FORMATS = {"mp3", "wav", "flac", "m4a", "aac", "ogg", "opus"}
TRANSCRIPT_OUTPUT_FORMATS = {"md", "txt", "srt", "vtt", "json"}
REGISTERED_ARTIFACT_FORMATS = (
    DOCUMENT_ARTIFACT_FORMATS | MEDIA_OUTPUT_FORMATS | TRANSCRIPT_OUTPUT_FORMATS | {"png", "zip"}
)
_GENERIC_ARTIFACT_FORMAT_RE = re.compile(r"^[a-z0-9][a-z0-9_+-]{0,15}$")
TEXT_INSPECT_FORMATS = {"txt", "md", "json", "csv", "html", "srt", "lrc", "vtt", "xml", "log", "yaml", "yml"}


class GeneratedFileService:
    """Own managed artifact storage, inspection, delivery and prompt projection.

    Optional plugins own document authoring; this service never renders documents.
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
        if resolved.stat().st_size <= 0:
            raise ValueError("artifact_source_empty")
        extension = source.suffix.lstrip(".").lower()
        if not _GENERIC_ARTIFACT_FORMAT_RE.fullmatch(extension):
            raise ValueError("artifact_source_extension_unsupported")
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

    def _resolve_generated_media_source(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
    ) -> dict[str, Any] | None:
        return generated_files_delivery.resolve_generated_media_source(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
        )

    def _resolve_attachment_media_source(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
    ) -> dict[str, Any] | None:
        return generated_files_delivery.resolve_attachment_media_source(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
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


    def _safe_filename(self, value: Any) -> str:
        text = str(value or "").strip()
        text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text)
        text = re.sub(r"\s+", "_", text).strip("._ ")
        return text or "untitled"

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
