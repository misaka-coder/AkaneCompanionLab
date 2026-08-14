"""ExecutionResourceBridge: thin host service between ``exec_run`` and the
existing resource stores.

This module is deliberately small.  It does exactly two things:

* ``stage_inputs``: resolve declared handles (``file_*`` / ``img_*`` /
  ``audio_*`` / ``gen_*``) into a run-scoped workspace inside the execution
  workspace, so a command can read them through safe relative paths without
  ever learning the original storage path.
* ``register_outputs``: expand declared ``output_globs`` inside the same
  run-scoped workspace, validate each match (regular file, containment,
  size / count limits), copy it into the existing GeneratedFileService
  managed storage, and register it as ``gen_*`` using the existing service.

It is not a ResourceManager, a database, or a global registry.  Per-run
binding is only the mapping needed to make ``exec_status`` register outputs
exactly once, and it is evicted with the run.

Every model-visible result carries ``gen_*`` handles, never absolute paths.
"""

from __future__ import annotations

import mimetypes
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .execution_run import is_valid_run_id
from .execution_specs import (
    ARTIFACT_STATUS_NOT_REGISTERED,
    ARTIFACT_STATUS_NOT_REQUESTED,
    ARTIFACT_STATUS_REGISTERED,
    ARTIFACT_STATUS_REGISTRATION_FAILED,
)

# Conservative host-side limits.  These guard a single run's declared resource
# scope; they are not per-command adapters and never scan the whole workspace.
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
    registered: tuple[RegisteredOutput, ...] = ()
    artifact_status: str = ARTIFACT_STATUS_NOT_REQUESTED
    artifact_reason: str = ""
    created_at: float = 0.0
    finalized: bool = False


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


class ExecutionResourceBridge:
    """Stage declared inputs and register declared outputs for one run."""

    def __init__(
        self,
        *,
        generated_file_service: Any,
        workspace_root: str | Path,
        max_inputs: int = EXEC_MAX_INPUT_RESOURCES,
        max_input_bytes_per_file: int = EXEC_MAX_INPUT_BYTES_PER_FILE,
        max_input_total_bytes: int = EXEC_MAX_INPUT_TOTAL_BYTES,
        max_outputs: int = EXEC_MAX_OUTPUT_FILES,
        max_output_bytes_per_file: int = EXEC_MAX_OUTPUT_BYTES_PER_FILE,
        max_output_total_bytes: int = EXEC_MAX_OUTPUT_TOTAL_BYTES,
        now: Any = None,
    ) -> None:
        self.generated_file_service = generated_file_service
        self.workspace_root = Path(workspace_root).resolve()
        self.max_inputs = max(1, int(max_inputs))
        self.max_input_bytes_per_file = max(1, int(max_input_bytes_per_file))
        self.max_input_total_bytes = max(1, int(max_input_total_bytes))
        self.max_outputs = max(1, int(max_outputs))
        self.max_output_bytes_per_file = max(1, int(max_output_bytes_per_file))
        self.max_output_total_bytes = max(1, int(max_output_total_bytes))
        self._now = now or time.time
        self._lock = threading.RLock()
        # Serializes both the per-run idempotence decision and the existing
        # GeneratedFileService's sequence allocation.  exec_status is a
        # read-only tool and may run concurrently for multiple runs; without
        # this lock two callers could both mint artifacts for one run (or race
        # the same session's next gen_* handle).
        self._registration_lock = threading.RLock()
        self._bindings: dict[str, _RunBinding] = {}

    # -- staging -----------------------------------------------------------------

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
        with self._lock:
            if clean_run_id in self._bindings:
                return {"ok": False, "reason": "duplicate_execution_run_id"}
        cwd_relpath = self._run_cwd_relpath(clean_run_id)
        run_dir = self.workspace_root / cwd_relpath
        staged: list[StagedInput] = []
        seen_targets: set[str] = set()
        total_bytes = 0
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return {"ok": False, "reason": "execution_workspace_unavailable"}
        for item in inputs:
            handle = str(item.get("handle") or "").strip()
            relpath = normalize_resource_as(item.get("as"))
            if not handle or relpath is None:
                self._cleanup_workspace(run_dir)
                return {"ok": False, "reason": "invalid_input_resource_path"}
            if relpath in seen_targets:
                self._cleanup_workspace(run_dir)
                return {"ok": False, "reason": "duplicate_input_resource_target"}
            seen_targets.add(relpath)
            file_ref = self._resolve_input_handle(handle, resource_scope)
            if not file_ref:
                self._cleanup_workspace(run_dir)
                return {"ok": False, "reason": f"input_handle_not_found:{handle}"}
            source_path = Path(str(file_ref.get("absolute_path") or "")).resolve()
            if not source_path.is_file():
                self._cleanup_workspace(run_dir)
                return {"ok": False, "reason": f"input_resource_not_a_file:{handle}"}
            size = source_path.stat().st_size
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
            destination = run_dir / relpath
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

    def run_cwd_relpath(self, run_id: str) -> str:
        """Public stable cwd for a run (empty string when unknown)."""
        if not run_id:
            return ""
        with self._lock:
            binding = self._bindings.get(str(run_id or "").strip())
        return str(binding.cwd_relpath or "") if binding is not None else ""

    def output_globs_for(self, run_id: str) -> tuple[str, ...]:
        with self._lock:
            binding = self._bindings.get(str(run_id or "").strip())
        return tuple(binding.output_globs) if binding is not None else ()

    def has_binding(self, *, run_id: str, owner: Any) -> bool:
        """Return whether this bridge owns resource state for the run."""
        with self._lock:
            binding = self._bindings.get(str(run_id or "").strip())
        return binding is not None and _same_owner(binding.execution_owner, owner)

    # -- registration ------------------------------------------------------------

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
            self._cleanup_workspace(self.workspace_root / binding.cwd_relpath)
            return result

        run_dir = self.workspace_root / binding.cwd_relpath
        matches, overflow = self._collect_output_matches(binding.output_globs, run_dir)
        if overflow:
            with self._lock:
                binding.artifact_status = ARTIFACT_STATUS_REGISTRATION_FAILED
                binding.artifact_reason = "output_file_limit_exceeded"
                binding.finalized = True
            result = self._registration_result(binding)
            self._cleanup_workspace(run_dir)
            return result
        if not matches:
            with self._lock:
                binding.artifact_status = ARTIFACT_STATUS_REGISTRATION_FAILED
                binding.artifact_reason = "output_not_found"
                binding.finalized = True
            result = self._registration_result(binding)
            self._cleanup_workspace(run_dir)
            return result

        registered: list[RegisteredOutput] = []
        failures: list[str] = []
        total_bytes = 0
        for relpath in matches:
            source = (run_dir / relpath).resolve()
            size = source.stat().st_size
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
        self._cleanup_workspace(run_dir)
        return self._registration_result(binding)

    def finalize_without_outputs(self, *, run_id: str, owner: Any, reason: str) -> dict[str, Any]:
        """Close resource state for a run that did not complete successfully.

        This never registers partial files.  It exists so failed, timed-out and
        cancelled runs do not leave staged inputs or immortal in-memory
        bindings behind.
        """
        clean_run_id = str(run_id or "").strip()
        with self._registration_lock:
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
                if not binding.finalized:
                    binding.artifact_status = (
                        ARTIFACT_STATUS_NOT_REGISTERED
                        if binding.output_globs
                        else ARTIFACT_STATUS_NOT_REQUESTED
                    )
                    binding.artifact_reason = str(reason or "command_not_completed") if binding.output_globs else ""
                    binding.finalized = True
                result = self._registration_result(binding)
            self._cleanup_workspace(self.workspace_root / binding.cwd_relpath)
            return result

    def _registration_result(self, binding: _RunBinding) -> dict[str, Any]:
        resources = [
            {
                "handle": item.handle,
                "name": item.name,
                "media_type": item.media_type,
                "size_bytes": item.size_bytes,
            }
            for item in binding.registered
        ]
        return {
            "ok": binding.artifact_status == ARTIFACT_STATUS_REGISTERED,
            "artifact_status": binding.artifact_status,
            "generated_resources": resources,
            "reason": binding.artifact_reason,
        }

    # -- helpers -----------------------------------------------------------------

    def _prune_bindings(self) -> None:
        """Evict old bindings so abandoned provider starts cannot grow forever."""
        stale_bindings: list[_RunBinding] = []
        with self._registration_lock:
            with self._lock:
                for run_id, binding in list(self._bindings.items()):
                    if self._now() - binding.created_at > 3600:
                        stale_bindings.append(binding)
                        self._bindings.pop(run_id, None)
            for binding in stale_bindings:
                self._cleanup_workspace(self.workspace_root / binding.cwd_relpath)

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
                except (OSError, ValueError):
                    continue
                if not resolved.is_file() or resolved.is_symlink():
                    continue
                if resolved.stat().st_size <= 0:
                    continue
                relpath = str(resolved.relative_to(run_dir.resolve())).replace("\\", "/")
                if relpath not in matches:
                    matches[relpath] = resolved
                    if len(matches) > self.max_outputs:
                        return sorted(matches.keys()), True
        return sorted(matches.keys()), False

    def _register_one_output(self, source: Path, relpath: str, binding: _RunBinding) -> RegisteredOutput | None:
        service = self.generated_file_service
        extension = str(source.suffix or "").lstrip(".").lower()
        if not extension:
            return None
        title = str(source.stem or "akane_output").strip()[:60] or "akane_output"
        target = service.allocate_output_path(
            profile_user_id=str(binding.resource_scope.profile_user_id or ""),
            session_id=str(binding.resource_scope.session_id or ""),
            title=title,
            output_format=extension,
            timestamp=int(self._now()),
            allow_generic_format=True,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source, target)
            mime_type = mimetypes.guess_type(source.name)[0] or service._mime_type_for_format(extension)
            generated = service.register_generated_artifact(
                profile_user_id=str(binding.resource_scope.profile_user_id or ""),
                session_id=str(binding.resource_scope.session_id or ""),
                output_path=target,
                output_title=title,
                output_format=extension,
                mime_type=mime_type,
                content_card={},
                summary=f"exec_run 输出：{title}.{extension}",
                created_by_tool="exec_run",
                source_ids=list(binding.input_handles) or None,
                send_to_user=False,
                timestamp=int(self._now()),
                allow_generic_format=True,
            )
        except Exception:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        handle = str(generated.get("generated_handle") or "").strip()
        if not handle:
            return None
        return RegisteredOutput(
            handle=handle,
            name=f"{title}.{extension}",
            media_type=mime_type,
            size_bytes=int(generated.get("file_size") or source.stat().st_size),
        )

    @staticmethod
    def _run_cwd_relpath(run_id: str) -> str:
        return f".akane_exec_runs/{str(run_id or '').strip()}"

    @staticmethod
    def _safe_destination(destination: Path, run_dir: Path) -> bool:
        try:
            destination.resolve().relative_to(run_dir.resolve())
        except (OSError, ValueError):
            return False
        return True

    def _cleanup_workspace(self, run_dir: Path) -> None:
        try:
            if run_dir.is_dir():
                shutil.rmtree(run_dir)
        except OSError:
            pass


def _same_owner(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    for name in ("profile_user_id", "session_id", "provider_id"):
        if str(getattr(left, name, "") or "") != str(getattr(right, name, "") or ""):
            return False
    return True
