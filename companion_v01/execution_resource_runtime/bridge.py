"""Own one resource bridge, its shared locks, bindings and cleanup lifecycle."""
from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..execution_run import is_valid_run_id
from ..execution_specs import ARTIFACT_STATUS_NOT_REGISTERED, ARTIFACT_STATUS_NOT_REQUESTED
from .contracts import (
    EXEC_MAX_INPUT_RESOURCES,
    EXEC_MAX_INPUT_BYTES_PER_FILE,
    EXEC_MAX_INPUT_TOTAL_BYTES,
    EXEC_MAX_OUTPUT_FILES,
    EXEC_MAX_OUTPUT_BYTES_PER_FILE,
    EXEC_MAX_OUTPUT_TOTAL_BYTES,
    _RunBinding,
    _same_owner,
)
from .staging import ResourceStagingMixin
from .registration import ResourceRegistrationMixin



class ExecutionResourceBridge(ResourceStagingMixin, ResourceRegistrationMixin):
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
        self._pending_run_ids: set[str] = set()

    @contextmanager
    def _admit_run(self, run_id: str) -> Iterator[bool]:
        """Reserve one ID across staging and project snapshots without locking I/O."""
        with self._lock:
            admitted = run_id not in self._bindings and run_id not in self._pending_run_ids
            if admitted:
                self._pending_run_ids.add(run_id)
        try:
            yield admitted
        finally:
            if admitted:
                with self._lock:
                    self._pending_run_ids.discard(run_id)

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
            self._cleanup_binding_workspace(binding)
            return result

    def _prune_bindings(self) -> None:
        """Evict old terminal receipts, retaining runs without confirmed outcomes."""
        stale_bindings: list[_RunBinding] = []
        with self._registration_lock:
            with self._lock:
                for run_id, binding in list(self._bindings.items()):
                    if binding.finalized and self._now() - binding.created_at > 3600:
                        stale_bindings.append(binding)
                        self._bindings.pop(run_id, None)
            for binding in stale_bindings:
                self._cleanup_binding_workspace(binding)

    def _cleanup_workspace(self, run_dir: Path) -> None:
        owned_root = self.workspace_root / ".akane_exec_runs"
        if run_dir.parent != owned_root or not is_valid_run_id(run_dir.name):
            return
        try:
            resolved = run_dir.resolve()
            resolved.relative_to(self.workspace_root)
            if run_dir.absolute() != resolved:
                return
            if run_dir.is_dir():
                shutil.rmtree(run_dir)
        except (OSError, ValueError, RuntimeError):
            pass

    def _cleanup_binding_workspace(self, binding: _RunBinding) -> None:
        if binding.cleanup_workspace:
            self._cleanup_workspace(self.workspace_root / binding.cwd_relpath)
