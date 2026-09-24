"""Host-owned materialization for path-free plugin artifact drafts."""

from __future__ import annotations

from contextvars import ContextVar
from contextlib import contextmanager
import tempfile

import mimetypes
import re
import stat
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Protocol, TYPE_CHECKING

from capcore import CapabilityResult, InvocationContext

if TYPE_CHECKING:
    from .generated_files import GeneratedFileService
from .plugin_api import MAX_MANAGED_ARTIFACT_BYTES, ManagedArtifactDraft
from .plugin_file_io import PluginFileCopyError, copy_file, run_cancellable_copy
from .plugin_result_projection import project_capability_result


_KNOWN_FORMAT_MIME_TYPES: dict[str, frozenset[str]] = {
    "png": frozenset({"image/png"}),
    "md": frozenset({"text/markdown", "text/markdown; charset=utf-8"}),
    "txt": frozenset({"text/plain"}),
    "csv": frozenset({"text/csv"}),
    "html": frozenset({"text/html"}),
    "json": frozenset({"application/json"}),
    "lrc": frozenset({"text/plain"}),
    "docx": frozenset({"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}),
    # Windows registry associations vary for subtitles and archives. Accept
    # canonical MIME types independently of machine-local associations.
    "srt": frozenset({"application/x-subrip", "text/plain"}),
    "vtt": frozenset({"text/vtt"}),
    "zip": frozenset({"application/zip", "application/x-zip-compressed"}),
    "pdf": frozenset({"application/pdf"}),
    "xlsx": frozenset({"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}),
    "wav": frozenset({"audio/wav", "audio/x-wav", "audio/wave"}),
    "mp3": frozenset({"audio/mpeg", "audio/mp3"}),
    "mp4": frozenset({"video/mp4", "audio/mp4"}),
    "m4a": frozenset({"audio/mp4", "audio/x-m4a"}),
    "flac": frozenset({"audio/flac", "audio/x-flac"}),
    "ogg": frozenset({"audio/ogg", "video/ogg", "application/ogg"}),
    "opus": frozenset({"audio/ogg", "audio/opus"}),
}
_FORMAT_RE = re.compile(r"^[a-z0-9]{1,16}$")
_MIME_RE = re.compile(r"^[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+(?:;[^\r\n]*)?$")


_transient_audio = ContextVar("transient_managed_audio", default=None)

@contextmanager
def transient_audio_scope(profile_user_id, session_id):
    """Host byte consumers own short-lived audio; never register a deliverable."""
    with tempfile.TemporaryDirectory(prefix="akane-playback-") as directory:
        scope = {"profile": profile_user_id, "session": session_id,
                 "root": Path(directory), "resources": {}}
        token = _transient_audio.set(scope)
        try:
            yield scope["resources"]
        finally:
            _transient_audio.reset(token)


class ManagedArtifactError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "managed_artifact_invalid")
        super().__init__(self.reason)


@dataclass(frozen=True)
class ValidatedArtifact:
    data: bytes
    path: Path | None
    file_size: int
    title: str
    output_format: str
    mime_type: str
    summary: str

    def copy_to(self, target: Path, *, cancelled: threading.Event | None = None) -> None:
        """Copy without loading a file into RAM; detect size changes during IO."""
        if self.path is None:
            target.write_bytes(self.data)
            return
        try:
            copy_file(self.path, target, expected_size=self.file_size, cancelled=cancelled or threading.Event())
        except PluginFileCopyError as exc:
            raise ManagedArtifactError(f"managed_artifact_{exc}") from None

    async def copy_to_async(self, target: Path) -> None:
        """Drain cancelled IO before callers remove its temporary files."""
        await run_cancellable_copy(self.copy_to, target)


class ManagedArtifactSink(Protocol):
    async def materialize(
        self,
        draft: ManagedArtifactDraft,
        *,
        context: InvocationContext,
        capability_id: str,
    ) -> Mapping[str, Any]: ...


class GeneratedFileManagedArtifactSink:
    """Atomically store one validated draft in Akane's GeneratedFileStore."""

    def __init__(self, generated_file_service: GeneratedFileService) -> None:
        from .generated_files import GeneratedFileService
        if not isinstance(generated_file_service, GeneratedFileService):
            raise TypeError("generated_file_service_required")
        self._service = generated_file_service

    async def materialize(
        self,
        draft: ManagedArtifactDraft,
        *,
        context: InvocationContext,
        capability_id: str,
    ) -> Mapping[str, Any]:
        artifact = validate_managed_artifact_draft(draft)
        title, output_format, mime_type, summary = (
            artifact.title,
            artifact.output_format,
            artifact.mime_type,
            artifact.summary,
        )
        if not isinstance(context, InvocationContext):
            raise ManagedArtifactError("managed_artifact_context_required")
        profile_user_id = str(context.profile_user_id or "").strip()
        session_id = str(context.session_id or "").strip()
        if not profile_user_id or not session_id:
            raise ManagedArtifactError("managed_artifact_context_required")
        transient = _transient_audio.get()
        if (transient is not None and transient["profile"] == profile_user_id
                and transient["session"] == session_id
                and capability_id.endswith(".service.tts.v1.synthesize")
                and mime_type.startswith("audio/") and not draft.send_to_user
                and not draft.source_handles and not draft.revision_of):
            token = uuid.uuid4().hex
            handle = "gen_transient_" + token
            target = transient["root"] / (token + "." + output_format)
            await artifact.copy_to_async(target)
            transient["resources"][handle] = {"absolute_path": str(target)}
            return {"generated_id": "generated::transient:" + token, "generated_handle": handle,
                    "output_title": title, "output_format": output_format, "mime_type": mime_type,
                    "file_size": artifact.file_size, "created_by_tool": capability_id,
                    "send_to_user": False, "delivery_mode": draft.delivery_mode}
        source_ids = []
        for handle in draft.source_handles:
            resource = self._service.resolve_input_resource(
                profile_user_id=profile_user_id, session_id=session_id, target=handle,
                timestamp=int(time.time()),
            )
            if not resource or str(resource.get("handle") or "").lower() != handle.lower() or not resource.get("source_id"):
                raise ManagedArtifactError("managed_artifact_source_reference_unavailable")
            if resource["source_id"] not in source_ids:
                source_ids.append(resource["source_id"])
        if draft.revision_of and self._service.resolve_generated_artifact(
            profile_user_id=profile_user_id, session_id=session_id, target=draft.revision_of,
        ) is None:
            raise ManagedArtifactError("managed_artifact_revision_source_unavailable")

        target = self._service.allocate_output_path(
            profile_user_id=profile_user_id,
            session_id=session_id,
            title=title,
            output_format=output_format,
            timestamp=int(time.time()),
            allow_generic_format=True,
        )
        temp_target = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        if not self._service.is_managed_storage_path(target) or not self._service.is_managed_storage_path(temp_target):
            raise ManagedArtifactError("managed_artifact_target_invalid")

        registered = False
        generated: dict[str, Any] = {}
        effective_ts = int(time.time())
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            await artifact.copy_to_async(temp_target)
            temp_target.replace(target)
            generated = self._service.register_generated_artifact(
                profile_user_id=profile_user_id,
                session_id=session_id,
                output_path=target,
                output_title=title,
                output_format=output_format,
                mime_type=mime_type,
                content_card={
                    "kind": "plugin_managed_artifact",
                    "title": title,
                    "summary": summary,
                    "capability_id": capability_id,
                },
                summary=summary,
                created_by_tool=capability_id,
                source_ids=source_ids,
                revision_of=draft.revision_of,
                send_to_user=bool(draft.send_to_user),
                timestamp=effective_ts,
                allow_generic_format=True,
            )
            registered = True
        except ManagedArtifactError:
            raise
        except Exception:
            raise ManagedArtifactError("managed_artifact_write_failed") from None
        finally:
            try:
                temp_target.unlink(missing_ok=True)
            except OSError:
                pass
            if not registered:
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass

        return {
            "generated_id": str(generated.get("generated_id") or ""),
            "generated_handle": str(generated.get("generated_handle") or ""),
            "output_title": str(generated.get("output_title") or title),
            "output_format": output_format,
            "mime_type": mime_type,
            "file_size": int(generated.get("file_size") or artifact.file_size),
            "created_by_tool": capability_id,
            "send_to_user": bool(draft.send_to_user),
            "delivery_mode": draft.delivery_mode,
            **({"source_handles": list(draft.source_handles)} if draft.source_handles else {}),
            **({"revision_of": draft.revision_of} if draft.revision_of else {}),
        }


def normalize_managed_artifact_reference(
    value: Any,
    *,
    draft: ManagedArtifactDraft,
    capability_id: str,
) -> dict[str, Any] | None:
    """Validate the one public, path-free reference returned by a host sink."""

    if not isinstance(value, Mapping):
        return None
    try:
        expected_size = validate_managed_artifact_draft(draft).file_size
    except ManagedArtifactError:
        return None
    generated_id = str(value.get("generated_id") or "").strip()
    generated_handle = str(value.get("generated_handle") or "").strip()
    output_title = str(value.get("output_title") or "").strip()
    output_format = str(value.get("output_format") or "").strip().lower().lstrip(".")
    mime_type = str(value.get("mime_type") or "").strip().lower()
    created_by_tool = str(value.get("created_by_tool") or "").strip()
    file_size = value.get("file_size")
    send_to_user = value.get("send_to_user")
    delivery_mode = value.get("delivery_mode", "file")
    if (
        not generated_id.startswith("generated::")
        or len(generated_id) > 128
        or not generated_handle
        or len(generated_handle) > 64
        or output_title != str(draft.title or "").strip()
        or output_format != str(draft.output_format or "").strip().lower().lstrip(".")
        or mime_type != str(draft.mime_type or "").strip().lower()
        or created_by_tool != capability_id
        or isinstance(file_size, bool)
        or not isinstance(file_size, int)
        or file_size != expected_size
        or not isinstance(send_to_user, bool)
        or send_to_user is not draft.send_to_user
        or delivery_mode != draft.delivery_mode
        or value.get("source_handles", []) != list(draft.source_handles)
        or value.get("revision_of", "") != draft.revision_of
    ):
        return None
    return {
        "generated_id": generated_id,
        "generated_handle": generated_handle,
        "output_title": output_title,
        "output_format": output_format,
        "mime_type": mime_type,
        "file_size": file_size,
        "created_by_tool": created_by_tool,
        "send_to_user": send_to_user,
        "delivery_mode": delivery_mode,
        **({"source_handles": list(draft.source_handles)} if draft.source_handles else {}),
        **({"revision_of": draft.revision_of} if draft.revision_of else {}),
    }


def validate_managed_artifact_draft(
    draft: ManagedArtifactDraft,
) -> ValidatedArtifact:
    if not isinstance(draft, ManagedArtifactDraft):
        raise ManagedArtifactError("managed_artifact_draft_required")
    if not isinstance(draft.data, bytes) or (draft.path is not None and draft.data):
        raise ManagedArtifactError("managed_artifact_bytes_required")
    source_path = None
    if draft.path is None:
        if not draft.data:
            raise ManagedArtifactError("managed_artifact_bytes_required")
        if len(draft.data) > MAX_MANAGED_ARTIFACT_BYTES:
            raise ManagedArtifactError("managed_artifact_too_large")
        file_size = len(draft.data)
    else:
        try:
            if not isinstance(draft.path, Path):
                raise ValueError
            source_path = draft.path.resolve(strict=True)
            source_stat = source_path.stat()
            if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_size <= 0:
                raise ValueError
            file_size = source_stat.st_size
        except (OSError, ValueError):
            raise ManagedArtifactError("managed_artifact_source_unavailable") from None
    if not isinstance(draft.send_to_user, bool):
        raise ManagedArtifactError("managed_artifact_delivery_invalid")
    def valid_handle(value: Any, *, generated_only: bool = False) -> bool:
        prefixes = ("gen_", "generated::") if generated_only else ("gen_", "file_", "img_", "audio_", "video_")
        return (isinstance(value, str) and len(value) <= 128 and value.startswith(prefixes)
                and re.fullmatch(r"[A-Za-z0-9_:.-]+", value) is not None)
    if (not isinstance(draft.source_handles, tuple) or len(draft.source_handles) > 20
            or any(not valid_handle(handle) for handle in draft.source_handles)
            or len(set(draft.source_handles)) != len(draft.source_handles)):
        raise ManagedArtifactError("managed_artifact_source_reference_invalid")
    if not isinstance(draft.revision_of, str) or draft.revision_of and not valid_handle(draft.revision_of, generated_only=True):
        raise ManagedArtifactError("managed_artifact_revision_reference_invalid")
    if not isinstance(draft.delivery_mode, str) or draft.delivery_mode not in {"file", "voice", "both"}:
        raise ManagedArtifactError("managed_artifact_delivery_invalid")

    title = str(draft.title or "").strip()
    if not title or len(title) > 120 or any(char in title for char in ("/", "\\", "\x00")):
        raise ManagedArtifactError("managed_artifact_title_invalid")
    output_format = str(draft.output_format or "").strip().lower().lstrip(".")
    mime_type = str(draft.mime_type or "").strip().lower()
    if not _FORMAT_RE.fullmatch(output_format):
        raise ManagedArtifactError("managed_artifact_format_unsupported")
    if not _MIME_RE.fullmatch(mime_type):
        raise ManagedArtifactError("managed_artifact_mime_mismatch")
    expected_mimes = _KNOWN_FORMAT_MIME_TYPES.get(output_format)
    if expected_mimes is None:
        guessed, _encoding = mimetypes.guess_type(f"artifact.{output_format}")
        expected_mimes = frozenset({guessed}) if guessed else frozenset()
    if expected_mimes and mime_type not in expected_mimes and mime_type.partition(";")[0] not in expected_mimes:
        raise ManagedArtifactError("managed_artifact_mime_mismatch")
    if draft.delivery_mode != "file" and (
        output_format not in {"wav", "mp3", "flac", "m4a", "aac", "ogg", "opus"}
        or not mime_type.startswith("audio/")
    ):
        raise ManagedArtifactError("managed_artifact_voice_requires_audio")
    summary = str(draft.summary or "").strip()
    if len(summary) > 1000:
        raise ManagedArtifactError("managed_artifact_summary_too_large")
    safe_metadata = project_capability_result(
        CapabilityResult(
            is_error=False,
            status="ok",
            content={"title": title, "summary": summary},
        )
    )
    if not bool(safe_metadata.get("ok")):
        raise ManagedArtifactError("managed_artifact_metadata_not_safe")
    return ValidatedArtifact(draft.data, source_path, file_size, title, output_format, mime_type, summary)


__all__ = [
    "GeneratedFileManagedArtifactSink",
    "ManagedArtifactError",
    "ManagedArtifactSink",
    "normalize_managed_artifact_reference",
    "validate_managed_artifact_draft",
]
