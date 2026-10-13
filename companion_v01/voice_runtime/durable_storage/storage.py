"""Conversation namespaces, canonical records and atomic artifact writes."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any


def _conversation_storage_root(
    *,
    state_dir: Path,
    conversation_id: str,
    conversation_generation: int,
) -> Path:
    if not isinstance(state_dir, Path):
        raise TypeError("voice_runtime_state_dir_must_be_path")
    try:
        resolved_state = state_dir.expanduser().resolve()
        runtime_root = resolved_state / "voice_runtime"
        if runtime_root.resolve() != runtime_root:
            raise ValueError
        runtime_root.relative_to(resolved_state)
        namespace = sha256(f"{conversation_id}\0{conversation_generation}".encode("utf-8")).hexdigest()
        conversation_root = runtime_root / "conversations" / namespace
        if conversation_root.resolve() != conversation_root:
            raise ValueError
        conversation_root.relative_to(runtime_root)
    except (OSError, ValueError, RuntimeError):
        raise ValueError("voice_runtime_storage_path_invalid") from None
    return conversation_root


def _required_identity(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"voice_runtime_{field_name}_invalid")
    return value


def _required_generation(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("voice_runtime_conversation_generation_invalid")
    return value


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return _canonical_json_value(dict(payload))


def _canonical_json_value(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    canonical.encode("utf-8")
    return canonical


def _utf8_digest(value: str) -> str | None:
    try:
        return sha256(value.encode("utf-8")).hexdigest()
    except UnicodeError:
        return None


def _storage_path_is_owned(path: Path) -> bool:
    try:
        return not path.is_symlink() and path.resolve() == path
    except (OSError, ValueError, RuntimeError):
        return False


def _record_set_digest(canonical_records: list[str]) -> str:
    return sha256("\n".join(canonical_records).encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (_canonical_json(payload) + "\n").encode("utf-8")
    _atomic_write_bytes(path, encoded)


def _atomic_write_bytes(path: Path, encoded: bytes) -> None:
    _atomic_publish_bytes(path, encoded, replace=True)


def _atomic_create_bytes(path: Path, encoded: bytes) -> None:
    """Publish a complete artifact only if its immutable key is still free."""

    _atomic_publish_bytes(path, encoded, replace=False)


def _atomic_publish_bytes(path: Path, encoded: bytes, *, replace: bool) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    file_descriptor = -1
    try:
        file_descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(file_descriptor, "wb") as handle:
            file_descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
        if os.name == "posix":
            directory_descriptor = os.open(
                path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
