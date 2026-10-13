"""Hash-guarded source writes and transactional multi-file patches with rollback."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import uuid
from typing import Any, Mapping

from .definitions import (
    ProjectWorkspaceError, ProjectWorkspaceScope, PROJECT_WRITE_MAX_CHARS, PROJECT_PATCH_MAX_CHARS,
)


class ProjectWritingMixin:
    def write(
        self,
        *,
        scope: ProjectWorkspaceScope | None,
        path: str,
        content: str,
        workspace_id: str = "",
        operation_root: str | Path | None = None,
        expected_sha256: str = "",
        mode: str = "create_or_replace",
    ) -> dict[str, Any]:
        text = str(content)
        if len(text) > PROJECT_WRITE_MAX_CHARS:
            raise ProjectWorkspaceError(
                "content_too_large",
                max_chars=PROJECT_WRITE_MAX_CHARS,
                actual_chars=len(text),
                recommended_action="split_source_file_or_use_smaller_patch",
            )
        clean_mode = str(mode or "create_or_replace").strip()
        if clean_mode not in {"create", "replace", "create_or_replace"}:
            raise ProjectWorkspaceError("invalid_write_mode")
        record, root = self._resolve_operation_root(
            scope=scope,
            workspace_id=workspace_id,
            operation_root=operation_root,
        )
        relative = self._relative_path(path)
        target = self._safe_child(root, relative.as_posix())
        with self._lock:
            exists = target.exists()
            if exists and (target.is_symlink() or not target.is_file()):
                raise ProjectWorkspaceError("path_conflict")
            if clean_mode == "create" and exists:
                raise ProjectWorkspaceError("path_conflict")
            if clean_mode == "replace" and not exists:
                raise ProjectWorkspaceError("source_missing")
            current = target.read_bytes() if exists else b""
            self._check_expected_hash(current=current, expected_sha256=expected_sha256, exists=exists)
            data = text.encode("utf-8")
            self._atomic_write(target, data)
        return {
            "status": "succeeded",
            "workspace_id": str(record["workspace_id"]),
            "effective_cwd": str(root),
            "path": relative.as_posix(),
            "created": not exists,
            "replaced": exists,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def patch(
        self,
        *,
        scope: ProjectWorkspaceScope | None,
        patch_text: str,
        workspace_id: str = "",
        operation_root: str | Path | None = None,
        expected_files: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        text = str(patch_text or "")
        if not text.strip():
            raise ProjectWorkspaceError("patch_required")
        if len(text) > PROJECT_PATCH_MAX_CHARS:
            raise ProjectWorkspaceError(
                "content_too_large",
                max_chars=PROJECT_PATCH_MAX_CHARS,
                actual_chars=len(text),
                recommended_action="split_patch_by_file",
            )
        record, root = self._resolve_operation_root(
            scope=scope,
            workspace_id=workspace_id,
            operation_root=operation_root,
        )
        file_patches = self._parse_model_patch(text)
        expected = {
            self._relative_path(key).as_posix(): str(value or "").lower()
            for key, value in dict(expected_files or {}).items()
        }
        with self._lock:
            prepared, affected = self._prepare_patch(root=root, file_patches=file_patches, expected=expected)
            self._commit_patch(root=root, prepared=prepared, affected=affected)
        return {
            "status": "succeeded",
            "workspace_id": str(record["workspace_id"]),
            "effective_cwd": str(root),
            "files": [
                {
                    "path": item["relative"].as_posix(),
                    "operation": item["operation"],
                    "bytes": len(item["updated"]),
                    "sha256": "" if item["operation"] == "delete" else hashlib.sha256(item["updated"]).hexdigest(),
                    **({"source_path": item["source_relative"].as_posix()} if item["operation"] == "rename" else {}),
                }
                for item in prepared
            ],
        }

    @staticmethod
    def _check_expected_hash(*, current: bytes, expected_sha256: str, exists: bool) -> None:
        expected = str(expected_sha256 or "").strip().lower()
        if not expected:
            return
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ProjectWorkspaceError("invalid_expected_sha256")
        actual = hashlib.sha256(current).hexdigest() if exists else ""
        if actual != expected:
            raise ProjectWorkspaceError("base_hash_mismatch", actual_sha256=actual)

    @staticmethod
    def _atomic_write(target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.parent.is_symlink():
            raise ProjectWorkspaceError("path_outside_workspace")
        temp = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
        try:
            with temp.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        except Exception as exc:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            if isinstance(exc, ProjectWorkspaceError):
                raise
            raise ProjectWorkspaceError("write_failed") from exc

    @staticmethod
    def _prune_empty_parents(directory: Path, *, root: Path) -> None:
        current = directory
        while current != root:
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent

    def _prepare_patch(
        self,
        *,
        root: Path,
        file_patches: list[dict[str, Any]],
        expected: Mapping[str, str],
    ) -> tuple[list[dict[str, Any]], dict[str, tuple[Path, bool, bytes]]]:
        prepared: list[dict[str, Any]] = []
        affected: dict[str, tuple[Path, bool, bytes]] = {}
        touched_paths: set[Path] = set()
        for file_patch in file_patches:
            operation = str(file_patch.get("operation") or "update")
            old_relative = self._relative_path(file_patch["old_path"]) if file_patch.get("old_path") else None
            relative = self._relative_path(file_patch["path"])
            source_relative = old_relative or relative
            source = self._safe_child(root, source_relative.as_posix())
            target = self._safe_child(root, relative.as_posix())
            # Compare resolved paths using host path semantics, so spelling,
            # separators, and Windows case aliases cannot target a file twice.
            file_paths = {source, target}
            if touched_paths.intersection(file_paths):
                raise ProjectWorkspaceError("duplicate_patch_target")
            touched_paths.update(file_paths)
            source_exists = source.exists()
            target_exists = target.exists()
            if source_exists and (source.is_symlink() or not source.is_file()):
                raise ProjectWorkspaceError("path_conflict", path=source_relative.as_posix())
            if operation == "create" and target_exists:
                raise ProjectWorkspaceError("path_conflict", path=relative.as_posix())
            if operation in {"update", "delete", "rename"} and not source_exists:
                raise ProjectWorkspaceError("source_missing", path=source_relative.as_posix())
            if operation == "rename" and target_exists:
                raise ProjectWorkspaceError("path_conflict", path=relative.as_posix())
            original = source.read_bytes() if source_exists else b""
            self._check_expected_hash(
                current=original,
                expected_sha256=expected.get(source_relative.as_posix(), expected.get(relative.as_posix(), "")),
                exists=source_exists,
            )
            try:
                original_text = original.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ProjectWorkspaceError("source_not_utf8", path=source_relative.as_posix()) from exc
            if operation == "create":
                updated = str(file_patch.get("content") or "").encode("utf-8")
            elif operation == "delete":
                updated = b""
            else:
                updated = self._apply_hunks(
                    original_text,
                    file_patch["hunks"],
                    path=source_relative.as_posix(),
                ).encode("utf-8")
            for item_path, item_relative in ((source, source_relative), (target, relative)):
                key = item_relative.as_posix()
                if key not in affected:
                    affected[key] = (
                        item_path,
                        item_path.exists(),
                        item_path.read_bytes() if item_path.is_file() else b"",
                    )
            prepared.append(
                {
                    "operation": operation,
                    "source": source,
                    "target": target,
                    "source_relative": source_relative,
                    "relative": relative,
                    "updated": updated,
                }
            )
        return prepared, affected

    def _commit_patch(
        self,
        *,
        root: Path,
        prepared: list[dict[str, Any]],
        affected: dict[str, tuple[Path, bool, bytes]],
    ) -> None:
        mutated: list[str] = []
        try:
            for item in prepared:
                if item["operation"] != "delete":
                    self._atomic_write(item["target"], item["updated"])
                    mutated.append(item["relative"].as_posix())
                if item["operation"] in {"delete", "rename"}:
                    item["source"].unlink()
                    mutated.append(item["source_relative"].as_posix())
        except Exception as exc:
            rollback_failed: list[str] = []
            for key in reversed(tuple(dict.fromkeys(mutated))):
                path, existed, original = affected[key]
                try:
                    if existed:
                        self._atomic_write(path, original)
                    elif path.exists() or path.is_symlink():
                        path.unlink()
                        self._prune_empty_parents(path.parent, root=root)
                except Exception:
                    rollback_failed.append(key)
            if rollback_failed:
                original_reason = exc.reason if isinstance(exc, ProjectWorkspaceError) else "write_failed"
                raise ProjectWorkspaceError(
                    "rollback_failed",
                    original_reason=original_reason,
                    failed_paths=rollback_failed,
                ) from exc
            if isinstance(exc, ProjectWorkspaceError):
                raise
            raise ProjectWorkspaceError("write_failed") from exc
