"""Private outbox handoff for PluginHost generation artifacts.

Artifact bytes stay off the line-delimited JSON protocol. The worker stages one
validated draft under its generation work directory and returns only an opaque
token. The parent consumes that token through the existing host-owned sink.
"""

from __future__ import annotations

import json
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from capcore import InvocationContext

from .plugin_api import ManagedArtifactDraft
from .plugin_managed_artifacts import (
    ManagedArtifactError,
    validate_managed_artifact_draft,
)


_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_PENDING_ID_PREFIX = "generated::generation-artifact:"
_PENDING_HANDLE_PREFIX = "generation-artifact-"
_MAX_METADATA_BYTES = 16 * 1024
_handoff_cleanup: ContextVar[list | None] = ContextVar("artifact_handoff_cleanup", default=None)


@contextmanager
def artifact_handoff_scope():
    """Unsent worker results never leave orphaned outbox files on cancellation."""
    pending = []
    token = _handoff_cleanup.set(pending)
    try:
        yield pending
    finally:
        _handoff_cleanup.reset(token)
        for paths in pending:
            _cleanup_paths(*paths)


@dataclass(frozen=True, slots=True)
class StagedGenerationArtifact:
    draft: ManagedArtifactDraft
    data_path: Path
    metadata_path: Path

    def cleanup(self) -> None:
        for path in (self.metadata_path, self.data_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


class GenerationArtifactOutboxSink:
    """Stage one path-free draft and return an internal pending reference."""

    def __init__(self, outbox_dir: Path) -> None:
        self._outbox_dir = Path(outbox_dir).resolve()

    async def materialize(
        self,
        draft: ManagedArtifactDraft,
        *,
        context: InvocationContext,
        capability_id: str,
    ) -> Mapping[str, Any]:
        del context
        artifact = validate_managed_artifact_draft(draft)
        title, output_format, mime_type, summary = (
            artifact.title,
            artifact.output_format,
            artifact.mime_type,
            artifact.summary,
        )
        token = uuid.uuid4().hex
        data_path, metadata_path = _artifact_paths(self._outbox_dir, token)
        data_temp = data_path.with_suffix(".bin.tmp")
        metadata_temp = metadata_path.with_suffix(".json.tmp")
        metadata = {
            "token": token,
            "title": title,
            "output_format": output_format,
            "mime_type": mime_type,
            "summary": summary,
            "send_to_user": draft.send_to_user,
            "delivery_mode": draft.delivery_mode,
            "source_handles": list(draft.source_handles),
            "revision_of": draft.revision_of,
            "file_size": artifact.file_size,
            "capability_id": capability_id,
        }
        staged = False
        try:
            self._outbox_dir.mkdir(parents=True, exist_ok=True)
            await artifact.copy_to_async(data_temp)
            data_temp.replace(data_path)
            metadata_temp.write_text(
                json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            metadata_temp.replace(metadata_path)
            staged = True
        except Exception:
            raise ManagedArtifactError("managed_artifact_handoff_failed") from None
        finally:
            if not staged:
                _cleanup_paths(metadata_temp, data_temp, metadata_path, data_path)
        pending = _handoff_cleanup.get()
        if pending is not None:
            pending.append((metadata_path, data_path))
        return {
            "generated_id": f"{_PENDING_ID_PREFIX}{token}",
            "generated_handle": f"{_PENDING_HANDLE_PREFIX}{token}",
            "output_title": title,
            "output_format": output_format,
            "mime_type": mime_type,
            "file_size": artifact.file_size,
            "created_by_tool": capability_id,
            "send_to_user": draft.send_to_user,
            "delivery_mode": draft.delivery_mode,
            **({"source_handles": list(draft.source_handles)} if draft.source_handles else {}),
            **({"revision_of": draft.revision_of} if draft.revision_of else {}),
        }


def is_generation_artifact_reference(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    generated_id = str(value.get("generated_id") or "")
    token = generated_id.removeprefix(_PENDING_ID_PREFIX)
    return generated_id.startswith(_PENDING_ID_PREFIX) and bool(_TOKEN_RE.fullmatch(token))


def discard_generation_artifacts(outbox_dir: Path, references: object) -> None:
    """Remove this result's outbox entries, including unconsumed trailing files."""
    if not isinstance(references, list):
        return
    for reference in references:
        if not is_generation_artifact_reference(reference):
            continue
        token = reference["generated_id"].removeprefix(_PENDING_ID_PREFIX)
        try:
            data_path, metadata_path = _artifact_paths(Path(outbox_dir).resolve(), token)
        except ManagedArtifactError:
            continue
        _cleanup_paths(metadata_path, data_path)


def consume_generation_artifact(
    outbox_dir: Path,
    reference: Mapping[str, Any],
    *,
    capability_id: str,
) -> StagedGenerationArtifact:
    generated_id = str(reference.get("generated_id") or "")
    token = generated_id.removeprefix(_PENDING_ID_PREFIX)
    if (
        not generated_id.startswith(_PENDING_ID_PREFIX)
        or not _TOKEN_RE.fullmatch(token)
        or str(reference.get("generated_handle") or "") != f"{_PENDING_HANDLE_PREFIX}{token}"
    ):
        raise ManagedArtifactError("managed_artifact_handoff_invalid")
    data_path, metadata_path = _artifact_paths(Path(outbox_dir).resolve(), token)
    try:
        metadata = json.loads(_read_bounded(metadata_path, max_bytes=_MAX_METADATA_BYTES).decode("utf-8"))
        if not isinstance(metadata, Mapping):
            raise ValueError
        # Old API-v1 outboxes did not include a mode; their only delivery was file.
        metadata = {"delivery_mode": "file", "source_handles": [], "revision_of": "", **metadata}
        if not data_path.is_file():
            raise ValueError
        file_size = data_path.stat().st_size
    except ManagedArtifactError:
        _cleanup_paths(metadata_path, data_path)
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        _cleanup_paths(metadata_path, data_path)
        raise ManagedArtifactError("managed_artifact_handoff_unavailable") from None
    expected = {
        "token": token,
        "title": str(reference.get("output_title") or ""),
        "output_format": str(reference.get("output_format") or ""),
        "mime_type": str(reference.get("mime_type") or ""),
        "send_to_user": reference.get("send_to_user"),
        "delivery_mode": reference.get("delivery_mode", "file"),
        "source_handles": reference.get("source_handles", []),
        "revision_of": reference.get("revision_of", ""),
        "file_size": file_size,
        "capability_id": capability_id,
    }
    reference_file_size = reference.get("file_size")
    if (
        isinstance(reference_file_size, bool)
        or reference_file_size != file_size
        or str(reference.get("created_by_tool") or "") != capability_id
        or any(metadata.get(key) != value for key, value in expected.items())
    ):
        _cleanup_paths(metadata_path, data_path)
        raise ManagedArtifactError("managed_artifact_handoff_invalid")
    draft = ManagedArtifactDraft(
        path=data_path,
        title=expected["title"],
        output_format=expected["output_format"],
        mime_type=expected["mime_type"],
        summary=str(metadata.get("summary") or ""),
        send_to_user=expected["send_to_user"],
        delivery_mode=expected["delivery_mode"],
        source_handles=tuple(expected["source_handles"]) if isinstance(expected["source_handles"], list) else expected["source_handles"],
        revision_of=expected["revision_of"],
    )
    try:
        validate_managed_artifact_draft(draft)
    except ManagedArtifactError:
        _cleanup_paths(metadata_path, data_path)
        raise
    return StagedGenerationArtifact(
        draft=draft,
        data_path=data_path,
        metadata_path=metadata_path,
    )


def _artifact_paths(outbox_dir: Path, token: str) -> tuple[Path, Path]:
    if not _TOKEN_RE.fullmatch(token):
        raise ManagedArtifactError("managed_artifact_handoff_invalid")
    data_path = (outbox_dir / f"{token}.bin").resolve()
    metadata_path = (outbox_dir / f"{token}.json").resolve()
    try:
        data_path.relative_to(outbox_dir)
        metadata_path.relative_to(outbox_dir)
    except ValueError:
        raise ManagedArtifactError("managed_artifact_handoff_invalid") from None
    return data_path, metadata_path


def _cleanup_paths(*paths: Path) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _read_bounded(path: Path, *, max_bytes: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ManagedArtifactError("managed_artifact_handoff_invalid")
    return data


__all__ = [
    "GenerationArtifactOutboxSink",
    "StagedGenerationArtifact",
    "consume_generation_artifact",
    "discard_generation_artifacts",
    "is_generation_artifact_reference",
]
