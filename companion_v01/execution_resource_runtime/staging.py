"""Resolve input handles and admit staged or persistent workspaces."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..execution_run import is_valid_run_id
from .contracts import ExecutionResourceScope, StagedInput, _RunBinding, normalize_output_glob, normalize_resource_as



class ResourceStagingMixin:
    def stage_inputs(
        self,
        *,
        run_id: str,
        owner: Any,
        resource_scope: ExecutionResourceScope,
        input_resources: Sequence[Mapping[str, Any]] | None,
        output_globs: Sequence[str] | None,
    ) -> dict[str, Any]:
        """Resolve and copy declared inputs into a run-scoped workspace.

        Returns ``{"ok": True, "cwd_relpath": ...}`` on success, or a structured
        rejection with a stable ``reason``.  Nothing is registered here; the
        command has not run yet.
        """
        if not is_valid_run_id(run_id):
            return {"ok": False, "reason": "invalid_execution_run_id"}
        inputs = [dict(item) for item in input_resources or [] if isinstance(item, Mapping)]
        globs = [
            normalized
            for raw in (output_globs or [])
            if (normalized := normalize_output_glob(raw)) is not None
        ]
        if len(inputs) > self.max_inputs:
            return {"ok": False, "reason": "execution_input_limit_exceeded"}
        if (output_globs and len(globs) != len(list(output_globs))) or (not output_globs and globs):
            return {"ok": False, "reason": "invalid_output_glob"}

        clean_run_id = str(run_id or "").strip()
        with self._admit_run(clean_run_id) as admitted:
            if not admitted:
                return {"ok": False, "reason": "duplicate_execution_run_id"}
            return self._stage_inputs_admitted(clean_run_id, owner, resource_scope, inputs, globs)

    def _stage_inputs_admitted(
        self,
        clean_run_id: str,
        owner: Any,
        resource_scope: ExecutionResourceScope,
        inputs: list[dict[str, Any]],
        globs: list[str],
    ) -> dict[str, Any]:
        cwd_relpath = self._run_cwd_relpath(clean_run_id)
        run_dir = self.workspace_root / cwd_relpath
        staged: list[StagedInput] = []
        seen_targets: set[Path] = set()
        total_bytes = 0
        try:
            if run_dir.absolute() != run_dir.resolve() or not self._safe_destination(run_dir, self.workspace_root):
                return {"ok": False, "reason": "execution_workspace_unavailable"}
            run_dir.mkdir(parents=True, exist_ok=False)
        except (OSError, ValueError, RuntimeError):
            return {"ok": False, "reason": "execution_workspace_unavailable"}
        for item in inputs:
            handle = str(item.get("handle") or "").strip()
            relpath = normalize_resource_as(item.get("as"))
            if not handle or relpath is None:
                self._cleanup_workspace(run_dir)
                return {"ok": False, "reason": "invalid_input_resource_path"}
            destination = run_dir / relpath
            try:
                target_key = destination.resolve()
            except (OSError, ValueError, RuntimeError):
                self._cleanup_workspace(run_dir)
                return {"ok": False, "reason": "invalid_input_resource_path"}
            if target_key == run_dir:
                self._cleanup_workspace(run_dir)
                return {"ok": False, "reason": "invalid_input_resource_path"}
            if target_key in seen_targets:
                self._cleanup_workspace(run_dir)
                return {"ok": False, "reason": "duplicate_input_resource_target"}
            seen_targets.add(target_key)
            try:
                file_ref = self._resolve_input_handle(handle, resource_scope)
                if file_ref:
                    source_path = Path(str(file_ref.get("absolute_path") or "")).resolve()
                    if not source_path.is_file():
                        self._cleanup_workspace(run_dir)
                        return {"ok": False, "reason": f"input_resource_not_a_file:{handle}"}
                    size = source_path.stat().st_size
            except (OSError, ValueError, RuntimeError):
                self._cleanup_workspace(run_dir)
                return {"ok": False, "reason": f"input_staging_failed:{handle}"}
            if not file_ref:
                self._cleanup_workspace(run_dir)
                return {"ok": False, "reason": f"input_handle_not_found:{handle}"}
            if size <= 0:
                self._cleanup_workspace(run_dir)
                return {"ok": False, "reason": f"input_resource_empty:{handle}"}
            if size > self.max_input_bytes_per_file:
                self._cleanup_workspace(run_dir)
                return {"ok": False, "reason": f"input_resource_too_large:{handle}"}
            total_bytes += size
            if total_bytes > self.max_input_total_bytes:
                self._cleanup_workspace(run_dir)
                return {"ok": False, "reason": "input_total_size_exceeded"}
            if not self._safe_destination(destination, run_dir):
                self._cleanup_workspace(run_dir)
                return {"ok": False, "reason": "input_resource_escapes_workspace"}
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, destination)
            except OSError:
                self._cleanup_workspace(run_dir)
                return {"ok": False, "reason": f"input_staging_failed:{handle}"}
            staged.append(StagedInput(handle=handle, relpath=relpath, size_bytes=size))

        input_handles = tuple(item.handle for item in staged)
        self._prune_bindings()
        with self._lock:
            self._bindings[clean_run_id] = _RunBinding(
                run_id=clean_run_id,
                execution_owner=owner,
                resource_scope=resource_scope,
                cwd_relpath=cwd_relpath,
                output_globs=tuple(globs),
                input_handles=input_handles,
                created_at=self._now(),
            )
        return {
            "ok": True,
            "cwd_relpath": cwd_relpath,
            "staged_inputs": [
                {"handle": item.handle, "as": item.relpath, "size_bytes": item.size_bytes}
                for item in staged
            ],
            "input_handles": list(input_handles),
            "output_globs": list(globs),
        }

    def bind_workspace_outputs(
        self,
        *,
        run_id: str,
        owner: Any,
        resource_scope: ExecutionResourceScope,
        cwd: str,
        output_globs: Sequence[str] | None,
        authorized_root: Path | None = None,
    ) -> dict[str, Any]:
        """Bind output registration to an existing workspace project.

        Unlike ``stage_inputs``, this mode never owns or removes the directory.
        A pre-run stat snapshot ensures an already-present, unchanged file is
        not mistaken for output produced by the current command.
        """
        if not is_valid_run_id(run_id):
            return {"ok": False, "reason": "invalid_execution_run_id"}
        globs = [
            normalized
            for raw in (output_globs or [])
            if (normalized := normalize_output_glob(raw)) is not None
        ]
        if not globs or len(globs) != len(list(output_globs or [])):
            return {"ok": False, "reason": "invalid_output_glob"}
        cwd_relpath = normalize_resource_as(cwd)
        try:
            if Path(cwd).is_absolute():
                run_dir = Path(cwd).resolve(strict=False)
                cwd_relpath = ""
            elif cwd_relpath is not None:
                run_dir = (self.workspace_root / cwd_relpath).resolve(strict=False)
            else:
                return {"ok": False, "reason": "invalid_output_cwd"}
            try:
                run_dir.relative_to(self.workspace_root)
            except ValueError:
                if authorized_root is None or run_dir != Path(authorized_root).resolve(strict=True):
                    return {"ok": False, "reason": "output_cwd_escapes_workspace"}
        except (OSError, ValueError, RuntimeError):
            return {"ok": False, "reason": "execution_workspace_unavailable"}
        if not run_dir.is_dir():
            return {"ok": False, "reason": "output_cwd_not_found"}

        clean_run_id = str(run_id or "").strip()
        with self._admit_run(clean_run_id) as admitted:
            if not admitted:
                return {"ok": False, "reason": "duplicate_execution_run_id"}
            return self._bind_workspace_outputs_admitted(clean_run_id, owner, resource_scope, cwd_relpath, run_dir, globs)

    def _bind_workspace_outputs_admitted(
        self,
        clean_run_id: str,
        owner: Any,
        resource_scope: ExecutionResourceScope,
        cwd_relpath: str,
        run_dir: Path,
        globs: list[str],
    ) -> dict[str, Any]:
        try:
            matches, overflow = self._collect_output_matches(globs, run_dir)
            if overflow:
                return {"ok": False, "reason": "existing_output_file_limit_exceeded"}
            baseline = tuple(
                (relpath, *self._output_fingerprint((run_dir / relpath).resolve()))
                for relpath in matches
            )
        except (OSError, ValueError, RuntimeError):
            return {"ok": False, "reason": "execution_workspace_unavailable"}
        self._prune_bindings()
        with self._lock:
            self._bindings[clean_run_id] = _RunBinding(
                run_id=clean_run_id,
                execution_owner=owner,
                resource_scope=resource_scope,
                cwd_relpath=cwd_relpath,
                output_globs=tuple(globs),
                input_handles=(),
                cleanup_workspace=False,
                baseline_outputs=baseline,
                created_at=self._now(),
                operation_root=run_dir,
            )
        return {
            "ok": True,
            "cwd_relpath": cwd_relpath,
            "effective_cwd": str(run_dir),
            "staged_inputs": [],
            "input_handles": [],
            "output_globs": list(globs),
            "output_mode": "persistent_workspace",
        }

    def _resolve_input_handle(
        self,
        handle: str,
        resource_scope: ExecutionResourceScope,
    ) -> dict[str, Any] | None:
        service = self.generated_file_service
        resolver = getattr(service, "resolve_input_resource", None)
        if callable(resolver):
            resolved = resolver(
                profile_user_id=str(resource_scope.profile_user_id or ""),
                session_id=str(resource_scope.session_id or ""),
                target=handle,
                timestamp=int(self._now()),
            )
            if not isinstance(resolved, dict):
                return None
            # input_resources is an exact-address contract.  Do not let
            # aliases such as "latest" silently resolve to a different file
            # after approval.
            resolved_handle = str(resolved.get("handle") or "").strip()
            if not resolved_handle or resolved_handle.lower() != str(handle or "").strip().lower():
                return None
            return resolved
        return None

    @staticmethod
    def _run_cwd_relpath(run_id: str) -> str:
        return f".akane_exec_runs/{str(run_id or '').strip()}"

    @staticmethod
    def _safe_destination(destination: Path, run_dir: Path) -> bool:
        try:
            destination.resolve().relative_to(run_dir.resolve())
        except (OSError, ValueError, RuntimeError):
            return False
        return True
