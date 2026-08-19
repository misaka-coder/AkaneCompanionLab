from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import sys
import threading
import time
import uuid
from typing import Any, Iterable, Mapping

from .store import MemoryStore


PROJECT_WRITE_MAX_CHARS = 256 * 1024
PROJECT_PATCH_MAX_CHARS = 256 * 1024
_PROJECT_ID_RE = re.compile(r"^proj_[a-f0-9]{32}$")
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_ROOT_KIND_MANAGED = "managed"
_ROOT_KIND_HOST_BOUND = "host_bound"


class ProjectWorkspaceError(ValueError):
    def __init__(self, reason: str, **details: Any) -> None:
        super().__init__(reason)
        self.reason = str(reason)
        self.details = dict(details)


@dataclass(frozen=True)
class ProjectWorkspaceScope:
    owner_kind: str
    owner_id: str
    actor_scope: str
    selection_scope: str = ""

    @property
    def selection_key(self) -> str:
        base = f"{self.owner_kind}\x1f{self.owner_id}\x1f{self.actor_scope}"
        payload = (base if not self.selection_scope else f"{base}\x1f{self.selection_scope}").encode("utf-8")
        return "project-selection:" + hashlib.sha256(payload).hexdigest()


class ProjectWorkspaceService:
    """Persistent project identity and confined source mutation.

    This is separate from the attachment ``workspace:/`` and TaskWorkspace
    ledger. Physical roots stay host-internal; model-facing callers use a
    workspace id and the dynamic ``alias:project`` execution alias.
    """

    def __init__(
        self,
        *,
        store: MemoryStore,
        execution_workspace_root: str | Path,
        protected_roots: Iterable[str | Path] = (),
    ) -> None:
        self.store = store
        self.execution_workspace_root = Path(execution_workspace_root).resolve()
        self.projects_root = (self.execution_workspace_root / "Projects").resolve()
        default_protected = (
            Path(sys.prefix).resolve(),
            Path(self.store.base_dir).resolve(),
        )
        self.protected_roots = tuple(
            dict.fromkeys(
                Path(item).expanduser().resolve(strict=False)
                for item in (*default_protected, *protected_roots)
                if str(item or "").strip()
            )
        )
        self._lock = threading.RLock()
        self._ensure_root()

    def scope_for(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        client_mode: str,
        actor_stable_id: str = "",
        actor_profile_user_id: str = "",
    ) -> ProjectWorkspaceScope:
        profile = str(profile_user_id or "").strip()
        session = str(session_id or "").strip()
        actor = str(actor_stable_id or "").strip()
        actor_profile = str(actor_profile_user_id or "").strip()
        mode = str(client_mode or "").strip().lower()
        if not profile:
            raise ProjectWorkspaceError("workspace_owner_required")
        if mode.startswith("qq"):
            if session.startswith("qq_group_shared_"):
                if not actor:
                    raise ProjectWorkspaceError("group_actor_required")
                if not actor_profile:
                    raise ProjectWorkspaceError("group_actor_profile_required")
                return ProjectWorkspaceScope("qq_user", actor_profile, "", session)
            return ProjectWorkspaceScope("qq_user", actor_profile or profile, "", session)
        if session.startswith("qq_group_shared_"):
            if not actor:
                raise ProjectWorkspaceError("group_actor_required")
            return ProjectWorkspaceScope("group", session, actor)
        if mode == "desktop_pet":
            return ProjectWorkspaceScope("desktop", profile, "")
        return ProjectWorkspaceScope("private", profile, "")

    def create(self, *, scope: ProjectWorkspaceScope, display_name: str) -> dict[str, Any]:
        self._prepare_scope(scope)
        name = self._display_name(display_name)
        workspace_id = "proj_" + uuid.uuid4().hex
        root_relpath = f"Projects/{workspace_id}"
        final_root = self._root_from_relpath(root_relpath)
        temp_root = self.projects_root / f".{workspace_id}.{uuid.uuid4().hex}.tmp"
        with self._lock:
            temp_root.mkdir(parents=False, exist_ok=False)
            try:
                os.replace(temp_root, final_root)
                record = self.store.add_project_workspace(
                    workspace_id=workspace_id,
                    display_name=name,
                    owner_kind=scope.owner_kind,
                    owner_id=scope.owner_id,
                    actor_scope=scope.actor_scope,
                    root_relpath=root_relpath,
                )
                self.store.set_project_workspace_selection(
                    selection_key=scope.selection_key,
                    workspace_id=workspace_id,
                )
            except Exception:
                if temp_root.exists():
                    temp_root.rmdir()
                if final_root.exists() and not any(final_root.iterdir()):
                    final_root.rmdir()
                raise
        return self._public_record(record, selected=True)

    def bind_existing(
        self,
        *,
        scope: ProjectWorkspaceScope,
        host_directory: str | Path,
        display_name: str = "",
    ) -> dict[str, Any]:
        self._prepare_scope(scope)
        root = self._validate_host_binding_root(host_directory)
        name = self._display_name(display_name or root.name)
        canonical = os.path.normcase(str(root))
        with self._lock:
            existing_records = self.store.list_project_workspaces(
                owner_kind=scope.owner_kind,
                owner_id=scope.owner_id,
                actor_scope=scope.actor_scope,
                include_archived=True,
            )
            for existing in existing_records:
                if str(existing.get("root_kind") or _ROOT_KIND_MANAGED) != _ROOT_KIND_HOST_BOUND:
                    continue
                existing_path = os.path.normcase(str(existing.get("host_root_path") or ""))
                if existing_path != canonical:
                    continue
                if str(existing.get("state") or "") != "active":
                    existing = self.store.update_project_workspace_state(
                        workspace_id=str(existing["workspace_id"]),
                        state="active",
                    ) or existing
                self.store.set_project_workspace_selection(
                    selection_key=scope.selection_key,
                    workspace_id=str(existing["workspace_id"]),
                )
                result = self._public_record(existing, selected=True)
                result["already_bound"] = True
                return result
            workspace_id = "proj_" + uuid.uuid4().hex
            record = self.store.add_project_workspace(
                workspace_id=workspace_id,
                display_name=name,
                owner_kind=scope.owner_kind,
                owner_id=scope.owner_id,
                actor_scope=scope.actor_scope,
                root_relpath="",
                root_kind=_ROOT_KIND_HOST_BOUND,
                host_root_path=str(root),
            )
            self.store.set_project_workspace_selection(
                selection_key=scope.selection_key,
                workspace_id=workspace_id,
            )
        return self._public_record(record, selected=True)

    def list(self, *, scope: ProjectWorkspaceScope, include_archived: bool = False) -> dict[str, Any]:
        self._prepare_scope(scope)
        selected_id = self.store.get_project_workspace_selection(selection_key=scope.selection_key)
        records = self.store.list_project_workspaces(
            owner_kind=scope.owner_kind,
            owner_id=scope.owner_id,
            actor_scope=scope.actor_scope,
            include_archived=include_archived,
        )
        public_records = [
            self._public_record(item, selected=item["workspace_id"] == selected_id)
            for item in records
        ]
        if selected_id and not any(item["selected"] for item in public_records):
            self.store.clear_project_workspace_selection(
                selection_key=scope.selection_key,
                workspace_id=selected_id,
            )
            selected_id = ""
        return {
            "status": "ok",
            "selected_workspace_id": selected_id,
            "workspaces": public_records,
        }

    def select(self, *, scope: ProjectWorkspaceScope, workspace_id: str) -> dict[str, Any]:
        self._prepare_scope(scope)
        record = self._owned_record(scope=scope, workspace_id=workspace_id, require_active=True)
        self._root_from_record(record, require_exists=True)
        self.store.set_project_workspace_selection(
            selection_key=scope.selection_key,
            workspace_id=str(record["workspace_id"]),
        )
        return self._public_record(record, selected=True)

    def archive(self, *, scope: ProjectWorkspaceScope, workspace_id: str) -> dict[str, Any]:
        self._prepare_scope(scope)
        record = self._owned_record(scope=scope, workspace_id=workspace_id, require_active=False)
        if str(record.get("state") or "") == "archived":
            return self._public_record(record, selected=False)
        updated = self.store.update_project_workspace_state(
            workspace_id=str(record["workspace_id"]),
            state="archived",
        )
        self.store.clear_project_workspace_selection(
            selection_key=scope.selection_key,
            workspace_id=str(record["workspace_id"]),
        )
        return self._public_record(updated or record, selected=False)

    def current(self, *, scope: ProjectWorkspaceScope) -> dict[str, Any] | None:
        self._prepare_scope(scope)
        workspace_id = self.store.get_project_workspace_selection(selection_key=scope.selection_key)
        if not workspace_id:
            return None
        try:
            record = self._owned_record(scope=scope, workspace_id=workspace_id, require_active=True)
            self._root_from_record(record, require_exists=True)
        except ProjectWorkspaceError:
            self.store.clear_project_workspace_selection(selection_key=scope.selection_key)
            return None
        return self._public_record(record, selected=True)

    def execution_cwd(
        self,
        *,
        scope: ProjectWorkspaceScope,
        alias_value: str,
        execution_provider: Any | None = None,
    ) -> str:
        raw = str(alias_value or "").strip()
        if raw != "alias:project" and not raw.startswith("alias:project/"):
            raise ProjectWorkspaceError("invalid_project_alias")
        selected = self.current(scope=scope)
        if selected is None:
            raise ProjectWorkspaceError("workspace_not_selected")
        record = self._owned_record(
            scope=scope,
            workspace_id=str(selected["workspace_id"]),
            require_active=True,
        )
        suffix = raw[len("alias:project") :].lstrip("/")
        root_kind = str(record.get("root_kind") or _ROOT_KIND_MANAGED)
        if root_kind == _ROOT_KIND_HOST_BOUND:
            root = self._root_from_record(record, require_exists=True)
            binder = getattr(execution_provider, "bind_authorized_mount", None)
            if not callable(binder):
                raise ProjectWorkspaceError("host_bound_execution_unavailable")
            mount_name = f"project_{str(record['workspace_id'])[5:29]}"
            try:
                binder(mount_name, root)
            except (OSError, ValueError) as exc:
                raise ProjectWorkspaceError("host_bound_execution_unavailable") from exc
            if suffix:
                suffix = self._relative_path(suffix).as_posix()
                target = self._safe_child(root, suffix)
                if not target.is_dir():
                    raise ProjectWorkspaceError("cwd_not_found")
                return f"alias:{mount_name}/{suffix}"
            return f"alias:{mount_name}"
        root_relpath = str(record["root_relpath"])
        if suffix:
            suffix = self._relative_path(suffix).as_posix()
            target = self._safe_child(self._root_from_record(record, require_exists=True), suffix)
            if not target.is_dir():
                raise ProjectWorkspaceError("cwd_not_found")
            return f"{root_relpath}/{suffix}"
        return root_relpath

    def write(
        self,
        *,
        scope: ProjectWorkspaceScope,
        path: str,
        content: str,
        workspace_id: str = "",
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
        record, root = self._resolve_project(scope=scope, workspace_id=workspace_id)
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
            "path": relative.as_posix(),
            "created": not exists,
            "replaced": exists,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def patch(
        self,
        *,
        scope: ProjectWorkspaceScope,
        patch_text: str,
        workspace_id: str = "",
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
        record, root = self._resolve_project(scope=scope, workspace_id=workspace_id)
        file_patches = self._parse_unified_diff(text)
        expected = {self._relative_path(key).as_posix(): str(value or "").lower() for key, value in dict(expected_files or {}).items()}
        prepared: list[tuple[Path, PurePosixPath, bytes, bytes]] = []
        with self._lock:
            for file_patch in file_patches:
                relative = self._relative_path(file_patch["path"])
                target = self._safe_child(root, relative.as_posix())
                if not target.exists():
                    raise ProjectWorkspaceError("source_missing", path=relative.as_posix())
                if target.is_symlink() or not target.is_file():
                    raise ProjectWorkspaceError("path_conflict", path=relative.as_posix())
                original = target.read_bytes()
                self._check_expected_hash(
                    current=original,
                    expected_sha256=expected.get(relative.as_posix(), ""),
                    exists=True,
                )
                try:
                    original_text = original.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ProjectWorkspaceError("source_not_utf8", path=relative.as_posix()) from exc
                updated_text = self._apply_hunks(original_text, file_patch["hunks"], path=relative.as_posix())
                prepared.append((target, relative, original, updated_text.encode("utf-8")))
            committed: list[tuple[Path, bytes]] = []
            try:
                for target, _relative, original, updated in prepared:
                    self._atomic_write(target, updated)
                    committed.append((target, original))
            except Exception as exc:
                for target, original in reversed(committed):
                    self._atomic_write(target, original)
                if isinstance(exc, ProjectWorkspaceError):
                    raise
                raise ProjectWorkspaceError("write_failed") from exc
        return {
            "status": "succeeded",
            "workspace_id": str(record["workspace_id"]),
            "files": [
                {
                    "path": relative.as_posix(),
                    "bytes": len(updated),
                    "sha256": hashlib.sha256(updated).hexdigest(),
                }
                for _target, relative, _original, updated in prepared
            ],
        }

    def _resolve_project(
        self,
        *,
        scope: ProjectWorkspaceScope,
        workspace_id: str,
    ) -> tuple[dict[str, Any], Path]:
        self._prepare_scope(scope)
        resolved_id = str(workspace_id or "").strip()
        if not resolved_id:
            resolved_id = self.store.get_project_workspace_selection(selection_key=scope.selection_key)
        if not resolved_id:
            raise ProjectWorkspaceError("workspace_not_selected")
        record = self._owned_record(scope=scope, workspace_id=resolved_id, require_active=True)
        return record, self._root_from_record(record, require_exists=True)

    def _prepare_scope(self, scope: ProjectWorkspaceScope) -> None:
        if scope.owner_kind != "qq_user":
            return
        # Early Project Workspace releases stored private QQ catalogs as
        # ``private/<profile>``. That identity is unambiguous and can be moved
        # safely. Group-wide legacy catalogs are deliberately not guessed:
        # their original creator was not stored by the broken runtime path.
        self.store.reassign_project_workspaces(
            from_owner_kind="private",
            from_owner_id=scope.owner_id,
            from_actor_scope="",
            to_owner_kind=scope.owner_kind,
            to_owner_id=scope.owner_id,
            to_actor_scope=scope.actor_scope,
        )

    def _owned_record(
        self,
        *,
        scope: ProjectWorkspaceScope,
        workspace_id: str,
        require_active: bool,
    ) -> dict[str, Any]:
        clean_id = str(workspace_id or "").strip()
        if not _PROJECT_ID_RE.fullmatch(clean_id):
            raise ProjectWorkspaceError("workspace_not_found")
        record = self.store.get_project_workspace(clean_id)
        if record is None:
            raise ProjectWorkspaceError("workspace_not_found")
        if (
            str(record.get("owner_kind") or "") != scope.owner_kind
            or str(record.get("owner_id") or "") != scope.owner_id
            or str(record.get("actor_scope") or "") != scope.actor_scope
        ):
            raise ProjectWorkspaceError("workspace_not_found")
        if require_active and str(record.get("state") or "") != "active":
            raise ProjectWorkspaceError("workspace_archived")
        return record

    def _root_from_record(self, record: Mapping[str, Any], *, require_exists: bool) -> Path:
        root_kind = str(record.get("root_kind") or _ROOT_KIND_MANAGED)
        if root_kind == _ROOT_KIND_HOST_BOUND:
            raw = str(record.get("host_root_path") or "").strip()
            source = Path(raw).expanduser()
            root = source.resolve(strict=False)
            source_absolute = source.absolute()
            if (
                not raw
                or not root.is_absolute()
                or source.is_symlink()
                or os.path.normcase(str(root)) != os.path.normcase(str(source_absolute))
            ):
                raise ProjectWorkspaceError("invalid_workspace_root")
        elif root_kind == _ROOT_KIND_MANAGED:
            root = self._root_from_relpath(str(record.get("root_relpath") or ""))
        else:
            raise ProjectWorkspaceError("invalid_workspace_root")
        if require_exists and (not root.is_dir() or root.is_symlink()):
            raise ProjectWorkspaceError("workspace_missing")
        return root

    def _validate_host_binding_root(self, value: str | Path) -> Path:
        raw = str(value or "").strip()
        if not raw:
            raise ProjectWorkspaceError("host_directory_required")
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            raise ProjectWorkspaceError("host_directory_must_be_absolute")
        try:
            root = candidate.resolve(strict=True)
        except OSError as exc:
            raise ProjectWorkspaceError("host_directory_missing") from exc
        if not root.is_dir() or root.is_symlink():
            raise ProjectWorkspaceError("host_directory_invalid")
        if self._path_is_within(root, self.execution_workspace_root):
            raise ProjectWorkspaceError("host_directory_managed_by_runtime")
        if any(self._path_is_within(root, protected) for protected in self.protected_roots):
            raise ProjectWorkspaceError("host_directory_protected")
        return root

    @staticmethod
    def _path_is_within(candidate: Path, root: Path) -> bool:
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            return False

    def _root_from_relpath(self, value: str) -> Path:
        relative = PurePosixPath(str(value or ""))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ProjectWorkspaceError("invalid_workspace_root")
        candidate = (self.execution_workspace_root / Path(*relative.parts)).resolve(strict=False)
        try:
            candidate.relative_to(self.projects_root)
        except ValueError as exc:
            raise ProjectWorkspaceError("invalid_workspace_root") from exc
        return candidate

    def _safe_child(self, root: Path, relative: str) -> Path:
        candidate = (root / Path(*PurePosixPath(relative).parts)).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ProjectWorkspaceError("path_outside_workspace") from exc
        current = root
        for part in PurePosixPath(relative).parts[:-1]:
            current = current / part
            if current.exists() and current.is_symlink():
                raise ProjectWorkspaceError("path_outside_workspace")
        return candidate

    @staticmethod
    def _relative_path(value: str) -> PurePosixPath:
        raw = str(value or "").replace("\\", "/").strip()
        path = PurePosixPath(raw)
        if not raw or path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
            raise ProjectWorkspaceError("path_outside_workspace")
        if any(part in {"", "."} for part in path.parts):
            raise ProjectWorkspaceError("invalid_relative_path")
        return path

    def _ensure_root(self) -> None:
        if self.execution_workspace_root.exists() and not self.execution_workspace_root.is_dir():
            raise ProjectWorkspaceError("execution_workspace_missing")
        self.execution_workspace_root.mkdir(parents=True, exist_ok=True)
        if self.execution_workspace_root.is_symlink():
            raise ProjectWorkspaceError("execution_workspace_linked")
        self.projects_root.mkdir(parents=True, exist_ok=True)
        if self.projects_root.is_symlink():
            raise ProjectWorkspaceError("projects_root_linked")

    @staticmethod
    def _display_name(value: str) -> str:
        name = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
        name = re.sub(r"\s+", " ", name).strip()
        if not name:
            raise ProjectWorkspaceError("display_name_required")
        if len(name) > 80:
            raise ProjectWorkspaceError("display_name_too_long", max_chars=80, actual_chars=len(name))
        return name

    def _public_record(self, record: Mapping[str, Any], *, selected: bool) -> dict[str, Any]:
        root_kind = str(record.get("root_kind") or _ROOT_KIND_MANAGED)
        try:
            available = self._root_from_record(record, require_exists=True).is_dir()
        except ProjectWorkspaceError:
            available = False
        return {
            "workspace_id": str(record.get("workspace_id") or ""),
            "display_name": str(record.get("display_name") or ""),
            "owner_kind": str(record.get("owner_kind") or ""),
            "state": str(record.get("state") or ""),
            "root_kind": root_kind,
            "available": available,
            "selected": bool(selected and available),
            "alias": "alias:project" if selected and available and str(record.get("state") or "") == "active" else "",
            "created_at": int(record.get("created_at") or 0),
            "updated_at": int(record.get("updated_at") or 0),
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
    def _parse_unified_diff(text: str) -> list[dict[str, Any]]:
        lines = text.splitlines(keepends=True)
        patches: list[dict[str, Any]] = []
        index = 0
        while index < len(lines):
            if not lines[index].startswith("--- "):
                index += 1
                continue
            old_path = lines[index][4:].strip().split("\t", 1)[0]
            index += 1
            if index >= len(lines) or not lines[index].startswith("+++ "):
                raise ProjectWorkspaceError("patch_parse_failed")
            new_path = lines[index][4:].strip().split("\t", 1)[0]
            index += 1
            if old_path == "/dev/null" or new_path == "/dev/null":
                raise ProjectWorkspaceError("patch_create_delete_unsupported")
            path = new_path[2:] if new_path.startswith("b/") else new_path
            old_normalized = old_path[2:] if old_path.startswith("a/") else old_path
            if path != old_normalized:
                raise ProjectWorkspaceError("patch_rename_unsupported")
            hunks: list[dict[str, Any]] = []
            while index < len(lines) and not lines[index].startswith("--- "):
                match = _HUNK_RE.match(lines[index])
                if not match:
                    if lines[index].strip():
                        raise ProjectWorkspaceError("patch_parse_failed", path=path)
                    index += 1
                    continue
                old_count = int(match.group(2) or 1)
                new_count = int(match.group(4) or 1)
                hunk = {
                    "old_start": int(match.group(1)),
                    "old_count": old_count,
                    "new_start": int(match.group(3)),
                    "new_count": new_count,
                    "lines": [],
                }
                index += 1
                while index < len(lines) and not lines[index].startswith(("@@ ", "--- ")):
                    line = lines[index]
                    if line.startswith("\\ No newline at end of file"):
                        index += 1
                        continue
                    if not line.startswith((" ", "+", "-")):
                        raise ProjectWorkspaceError("patch_parse_failed", path=path)
                    hunk["lines"].append(line)
                    index += 1
                hunks.append(hunk)
            if not hunks:
                raise ProjectWorkspaceError("patch_parse_failed", path=path)
            patches.append({"path": path, "hunks": hunks})
        if not patches:
            raise ProjectWorkspaceError("patch_parse_failed")
        paths = [item["path"] for item in patches]
        if len(paths) != len(set(paths)):
            raise ProjectWorkspaceError("duplicate_patch_target")
        return patches

    @staticmethod
    def _apply_hunks(original: str, hunks: list[dict[str, Any]], *, path: str) -> str:
        source = original.splitlines(keepends=True)
        newline = "\r\n" if "\r\n" in original else "\n"
        output: list[str] = []
        cursor = 0
        for hunk_index, hunk in enumerate(hunks, start=1):
            start = max(0, int(hunk["old_start"]) - 1)
            if start < cursor or start > len(source):
                raise ProjectWorkspaceError("hunk_not_applicable", path=path, hunk=hunk_index)
            output.extend(source[cursor:start])
            cursor = start
            consumed = 0
            produced = 0
            for raw in hunk["lines"]:
                marker = raw[0]
                body = raw[1:]
                body_without_eol = body.rstrip("\r\n")
                if marker in {" ", "-"}:
                    if cursor >= len(source) or source[cursor].rstrip("\r\n") != body_without_eol:
                        raise ProjectWorkspaceError("hunk_not_applicable", path=path, hunk=hunk_index)
                    if marker == " ":
                        output.append(source[cursor])
                        produced += 1
                    cursor += 1
                    consumed += 1
                elif marker == "+":
                    has_eol = body.endswith(("\n", "\r"))
                    output.append(body_without_eol + (newline if has_eol else ""))
                    produced += 1
            if consumed != int(hunk["old_count"]) or produced != int(hunk["new_count"]):
                raise ProjectWorkspaceError("patch_count_mismatch", path=path, hunk=hunk_index)
        output.extend(source[cursor:])
        return "".join(output)


__all__ = [
    "PROJECT_PATCH_MAX_CHARS",
    "PROJECT_WRITE_MAX_CHARS",
    "ProjectWorkspaceError",
    "ProjectWorkspaceScope",
    "ProjectWorkspaceService",
]
