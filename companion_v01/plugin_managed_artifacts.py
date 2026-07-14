"""Host-owned materialization for path-free plugin artifact drafts."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Mapping
from typing import Any, Protocol

from capcore import CapabilityResult, InvocationContext

from .generated_files import GeneratedFileService
from .plugin_api import MAX_MANAGED_ARTIFACT_BYTES, ManagedArtifactDraft
from .plugin_result_projection import project_capability_result


_ALLOWED_FORMAT_MIME_TYPES: dict[str, frozenset[str]] = {
    "png": frozenset({"image/png"}),
    "md": frozenset({"text/markdown", "text/markdown; charset=utf-8"}),
    "pdf": frozenset({"application/pdf"}),
    "xlsx": frozenset({"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}),
}


class ManagedArtifactError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "managed_artifact_invalid")
        super().__init__(self.reason)


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
        return await asyncio.to_thread(
            self._materialize_sync,
            draft,
            context=context,
            capability_id=capability_id,
        )

    def _materialize_sync(
        self,
        draft: ManagedArtifactDraft,
        *,
        context: InvocationContext,
        capability_id: str,
    ) -> Mapping[str, Any]:
        data, title, output_format, mime_type, summary = _validate_draft(draft)
        if not isinstance(context, InvocationContext):
            raise ManagedArtifactError("managed_artifact_context_required")
        profile_user_id = str(context.profile_user_id or "").strip()
        session_id = str(context.session_id or "").strip()
        if not profile_user_id or not session_id:
            raise ManagedArtifactError("managed_artifact_context_required")

        target = self._service.allocate_output_path(
            profile_user_id=profile_user_id,
            session_id=session_id,
            title=title,
            output_format=output_format,
            timestamp=int(time.time()),
        )
        temp_target = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        if not self._service.is_managed_storage_path(target) or not self._service.is_managed_storage_path(temp_target):
            raise ManagedArtifactError("managed_artifact_target_invalid")

        registered = False
        generated: dict[str, Any] = {}
        effective_ts = int(time.time())
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp_target.write_bytes(data)
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
                source_ids=(),
                send_to_user=bool(draft.send_to_user),
                timestamp=effective_ts,
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
            "file_size": int(generated.get("file_size") or len(data)),
            "created_by_tool": capability_id,
            "send_to_user": bool(draft.send_to_user),
        }


def _validate_draft(draft: ManagedArtifactDraft) -> tuple[bytes, str, str, str, str]:
    if not isinstance(draft, ManagedArtifactDraft):
        raise ManagedArtifactError("managed_artifact_draft_required")
    if not isinstance(draft.data, bytes) or not draft.data:
        raise ManagedArtifactError("managed_artifact_bytes_required")
    if len(draft.data) > MAX_MANAGED_ARTIFACT_BYTES:
        raise ManagedArtifactError("managed_artifact_too_large")
    if not isinstance(draft.send_to_user, bool):
        raise ManagedArtifactError("managed_artifact_delivery_invalid")

    title = str(draft.title or "").strip()
    if not title or len(title) > 120 or any(char in title for char in ("/", "\\", "\x00")):
        raise ManagedArtifactError("managed_artifact_title_invalid")
    output_format = str(draft.output_format or "").strip().lower().lstrip(".")
    mime_type = str(draft.mime_type or "").strip().lower()
    if output_format not in _ALLOWED_FORMAT_MIME_TYPES:
        raise ManagedArtifactError("managed_artifact_format_unsupported")
    if mime_type not in _ALLOWED_FORMAT_MIME_TYPES[output_format]:
        raise ManagedArtifactError("managed_artifact_mime_mismatch")
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
    return draft.data, title, output_format, mime_type, summary


__all__ = [
    "GeneratedFileManagedArtifactSink",
    "ManagedArtifactError",
    "ManagedArtifactSink",
]
