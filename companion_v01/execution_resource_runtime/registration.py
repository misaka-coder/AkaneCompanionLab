"""Collect declared outputs, enforce budgets and register artifact receipts."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from ..execution_specs import (
    ARTIFACT_STATUS_NOT_REGISTERED,
    ARTIFACT_STATUS_NOT_REQUESTED,
    ARTIFACT_STATUS_REGISTERED,
    ARTIFACT_STATUS_REGISTRATION_FAILED,
)
from .contracts import RegisteredOutput, _RunBinding, _same_owner, normalize_output_glob



class ResourceRegistrationMixin:
    def register_outputs(self, *, run_id: str, owner: Any) -> dict[str, Any]:
        """Register declared outputs once per run, idempotently.

        Only a ``completed`` terminal run registers outputs; cancelled /
        timed_out / failed runs never register partial artifacts.  Repeated or
        concurrent calls return the same ``gen_*`` handles.
        """
        with self._registration_lock:
            return self._register_outputs_once(run_id=run_id, owner=owner)

    def _register_outputs_once(self, *, run_id: str, owner: Any) -> dict[str, Any]:
        clean_run_id = str(run_id or "").strip()
        with self._lock:
            binding = self._bindings.get(clean_run_id)
        if binding is None:
            return {
                "ok": False,
                "artifact_status": ARTIFACT_STATUS_NOT_REGISTERED,
                "generated_resources": [],
                "reason": "execution_resources_unknown_run",
            }
        if not _same_owner(binding.execution_owner, owner):
            return {
                "ok": False,
                "artifact_status": ARTIFACT_STATUS_NOT_REGISTERED,
                "generated_resources": [],
                "reason": "execution_resources_owner_mismatch",
            }
        with self._lock:
            if binding.finalized:
                return self._registration_result(binding)
            if binding.artifact_status in {ARTIFACT_STATUS_REGISTERED, ARTIFACT_STATUS_REGISTRATION_FAILED}:
                binding.finalized = True
                return self._registration_result(binding)

        if not binding.output_globs:
            with self._lock:
                binding.artifact_status = ARTIFACT_STATUS_NOT_REQUESTED
                binding.finalized = True
            result = self._registration_result(binding)
            self._cleanup_binding_workspace(binding)
            return result

        run_dir = binding.operation_root or self.workspace_root / binding.cwd_relpath
        matches, overflow = self._collect_output_matches(binding.output_globs, run_dir)
        if overflow:
            with self._lock:
                binding.artifact_status = ARTIFACT_STATUS_REGISTRATION_FAILED
                binding.artifact_reason = "output_file_limit_exceeded"
                binding.finalized = True
            result = self._registration_result(binding)
            self._cleanup_binding_workspace(binding)
            return result
        baseline = {
            relpath: (size, mtime_ns, ctime_ns)
            for relpath, size, mtime_ns, ctime_ns in binding.baseline_outputs
        }
        if baseline:
            changed_matches = []
            for relpath in matches:
                try:
                    fingerprint = self._output_fingerprint((run_dir / relpath).resolve())
                except (OSError, ValueError, RuntimeError):
                    continue
                if baseline.get(relpath) != fingerprint:
                    changed_matches.append(relpath)
            matches = changed_matches
        if not matches:
            with self._lock:
                binding.artifact_status = ARTIFACT_STATUS_REGISTRATION_FAILED
                binding.artifact_reason = (
                    "output_not_created_or_changed" if baseline else "output_not_found"
                )
                binding.finalized = True
            result = self._registration_result(binding)
            self._cleanup_binding_workspace(binding)
            return result

        registered: list[RegisteredOutput] = []
        failures: list[str] = []
        total_bytes = 0
        for relpath in matches:
            source = run_dir / relpath
            try:
                if source.absolute() != source.resolve(strict=True):
                    raise ValueError("output_path_changed")
                size = source.stat().st_size
            except (OSError, ValueError):
                failures.append(f"{relpath}:output_path_changed")
                continue
            if size > self.max_output_bytes_per_file:
                failures.append(f"{relpath}:output_too_large")
                continue
            total_bytes += size
            if total_bytes > self.max_output_total_bytes:
                failures.append(f"{relpath}:output_total_size_exceeded")
                continue
            try:
                output = self._register_one_output(source, relpath, binding)
            except Exception as exc:
                failures.append(f"{relpath}:{type(exc).__name__}")
                continue
            if output is None:
                failures.append(f"{relpath}:unsupported_output_format")
                continue
            registered.append(output)
            if len(registered) + len(failures) >= self.max_outputs:
                break

        with self._lock:
            binding.registered = tuple(registered)
            if registered and not failures:
                binding.artifact_status = ARTIFACT_STATUS_REGISTERED
            else:
                binding.artifact_status = ARTIFACT_STATUS_REGISTRATION_FAILED
                binding.artifact_reason = "output_registration_incomplete" if failures else "output_not_found"
            binding.finalized = True
        self._cleanup_binding_workspace(binding)
        return self._registration_result(binding)

    def _registration_result(self, binding: _RunBinding) -> dict[str, Any]:
        resources = [
            {
                "handle": item.handle,
                "name": item.name,
                "media_type": item.media_type,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
            }
            for item in binding.registered
        ]
        return {
            "ok": binding.artifact_status == ARTIFACT_STATUS_REGISTERED,
            "artifact_status": binding.artifact_status,
            "generated_resources": resources,
            "reason": binding.artifact_reason,
        }

    def _collect_output_matches(self, globs: Sequence[str], run_dir: Path) -> tuple[list[str], bool]:
        matches: dict[str, Path] = {}
        for raw_glob in globs:
            pattern = normalize_output_glob(raw_glob)
            if pattern is None:
                continue
            try:
                candidates = run_dir.glob(pattern)
            except (NotImplementedError, ValueError):
                continue
            for candidate in candidates:
                try:
                    resolved = candidate.resolve()
                    resolved.relative_to(run_dir.resolve())
                    if candidate.absolute() != resolved or not resolved.is_file():
                        continue
                    if resolved.stat().st_size <= 0:
                        continue
                    relpath = str(resolved.relative_to(run_dir.resolve())).replace("\\", "/")
                except (OSError, ValueError, RuntimeError):
                    continue
                if relpath not in matches:
                    matches[relpath] = resolved
                    if len(matches) > self.max_outputs:
                        return sorted(matches.keys()), True
        return sorted(matches.keys()), False

    @staticmethod
    def _output_fingerprint(path: Path) -> tuple[int, int, int]:
        stat = path.stat()
        return (int(stat.st_size), int(stat.st_mtime_ns), int(stat.st_ctime_ns))

    def _register_one_output(self, source: Path, relpath: str, binding: _RunBinding) -> RegisteredOutput | None:
        service = self.generated_file_service
        extension = str(source.suffix or "").lstrip(".").lower()
        if not extension:
            return None
        title = str(source.stem or "akane_output").strip()[:60] or "akane_output"
        generated = service.register_workspace_artifact(
            source=source, root=binding.operation_root or self.workspace_root / binding.cwd_relpath,
            profile_user_id=binding.resource_scope.profile_user_id, session_id=binding.resource_scope.session_id,
            created_by_tool="exec_run", source_ids=list(binding.input_handles) or None,
            timestamp=int(self._now()), max_bytes=self.max_output_bytes_per_file,
        )
        mime_type = str(generated.get("mime_type") or "application/octet-stream")
        handle = str(generated.get("generated_handle") or "").strip()
        if not handle:
            return None
        return RegisteredOutput(
            handle=handle,
            name=f"{title}.{extension}",
            media_type=mime_type,
            size_bytes=int(generated.get("file_size") or source.stat().st_size),
            sha256=str(generated.get("sha256") or ""),
        )
