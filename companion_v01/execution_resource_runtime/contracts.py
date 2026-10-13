"""Resource limits, conversation contracts and relative-path validation."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..execution_specs import ARTIFACT_STATUS_NOT_REQUESTED


EXEC_MAX_INPUT_RESOURCES = 8


EXEC_MAX_INPUT_BYTES_PER_FILE = 256 * 1024 * 1024


EXEC_MAX_INPUT_TOTAL_BYTES = 1024 * 1024 * 1024


EXEC_MAX_OUTPUT_FILES = 32


EXEC_MAX_OUTPUT_BYTES_PER_FILE = 256 * 1024 * 1024


EXEC_MAX_OUTPUT_TOTAL_BYTES = 1024 * 1024 * 1024


EXEC_RESOURCE_AS_MAX_CHARS = 512


EXEC_OUTPUT_GLOB_MAX_CHARS = 1024


_DRIVE_RE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True, slots=True)
class StagedInput:
    handle: str
    relpath: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class RegisteredOutput:
    handle: str
    name: str
    media_type: str
    size_bytes: int
    sha256: str = ""


@dataclass(frozen=True, slots=True)
class ExecutionResourceScope:
    """Conversation namespace used by existing attachment/generated stores.

    This is intentionally distinct from ``ExecutionRunOwner``.  A shared QQ
    conversation scopes command control to the member who started a run, while
    its attachments and generated artifacts remain visible to that shared
    conversation.  Mixing the two scopes makes a freshly returned ``gen_*``
    handle impossible to deliver from the same turn.
    """

    profile_user_id: str
    session_id: str


@dataclass(slots=True)
class _RunBinding:
    run_id: str
    execution_owner: Any
    resource_scope: ExecutionResourceScope
    cwd_relpath: str
    output_globs: tuple[str, ...]
    input_handles: tuple[str, ...]
    cleanup_workspace: bool = True
    baseline_outputs: tuple[tuple[str, int, int, int], ...] = ()
    registered: tuple[RegisteredOutput, ...] = ()
    artifact_status: str = ARTIFACT_STATUS_NOT_REQUESTED
    artifact_reason: str = ""
    created_at: float = 0.0
    finalized: bool = False
    operation_root: Path | None = None


def normalize_resource_as(value: Any) -> str | None:
    """Return a safe forward-slash relative target, or None when unsafe.

    Rejects absolute paths, drive letters, UNC, ``..`` and colon segments.
    """
    text = str(value or "").strip().replace("\\", "/")
    if not text or len(text) > EXEC_RESOURCE_AS_MAX_CHARS:
        return None
    if text.startswith("/") or text.startswith("\\\\") or text.startswith("//"):
        return None
    if _DRIVE_RE.match(text):
        return None
    parts = [part for part in text.split("/") if part]
    if not parts or ".." in parts or any(":" in part for part in parts):
        return None
    return "/".join(parts)


def normalize_output_glob(value: Any) -> str | None:
    """Return a safe forward-slash glob, or None when unsafe."""
    text = str(value or "").strip().replace("\\", "/")
    if not text or len(text) > EXEC_OUTPUT_GLOB_MAX_CHARS:
        return None
    if text.startswith("/") or text.startswith("//"):
        return None
    if _DRIVE_RE.match(text):
        return None
    if any(part == ".." for part in text.split("/")):
        return None
    return text


def _same_owner(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    for name in ("profile_user_id", "session_id", "provider_id"):
        if str(getattr(left, name, "") or "") != str(getattr(right, name, "") or ""):
            return False
    return True
