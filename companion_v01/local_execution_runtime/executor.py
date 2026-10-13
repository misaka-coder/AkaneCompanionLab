"""TrustedLocalExecutor: the host-local execution provider.

Deliberately named "Trusted", not "Sandbox": this provider runs commands with
the host user's permissions. It is not an OS sandbox: both command arguments
and an explicit absolute ``cwd`` can reach directories available to that host
user. The inherited environment remains deliberately bounded.

Security model:

* relative ``cwd`` stays inside ``workspace_root``; mount aliases resolve to
  host-authorized roots; an absolute ``cwd`` uses normal host permissions and
  must already exist as a directory.
* by default, child processes inherit the host user's ordinary environment so
  PATH, HOME and version-manager configuration work like a normal coding
  agent. Credential-like names and Akane-internal variables are removed unless
  the host explicitly exposes a non-internal credential reference. A host may
  opt into the older explicit-allowlist mode through configuration.
* cancellation is confirmed only after the process group is gone; a request
  alone is never reported as success (``cancel_failed`` otherwise).
* there is no command blacklist — command risk classification and ``ask``
  approvals belong to the capability/permission chain.

Process lifecycle:

* POSIX: ``start_new_session=True`` + ``os.killpg`` (SIGTERM, then SIGKILL
  after a grace period).
* Windows: ``CREATE_NEW_PROCESS_GROUP`` + ``taskkill /pid X /T /F`` tree kill.
* full stdout/stderr are appended to a controlled run log under
  ``run_log_dir/<run_id>.log`` from process start and surfaced through the
  ``runlog:<run_id>`` ``output_ref`` contract. ``output_ref`` is only returned
  when the log is genuinely open and being written.
* output capture is reported separately from the command's own result. A command
  whose detached child keeps the inherited stdout/stderr handles open ends with
  its real exit code and ``reason=output_open_after_exit`` rather than a capture
  failure, and the readers left draining that pipe are released within a bound.

The built-in catalog registers this provider through the execution handlers.
Interactive runs retain a pipe stdin; ordinary runs continue to use DEVNULL."""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Sequence

from ..execution_run import (
    ExecutionAvailability, ExecutionProvider, ExecutionRunOwner, ExecutionRunStore,
    ExecRunStart, is_valid_run_id, new_run_id,
)
from ..execution_specs import (
    EXEC_DEFAULT_TIMEOUT_SECONDS,
    EXEC_MAX_INITIAL_WAIT_SECONDS, EXEC_STATUS_EXECUTION_UNKNOWN, EXEC_STATUS_FAILED,
    EXEC_STATUS_RUNNING, normalize_initial_wait_seconds, normalize_timeout_seconds,
)
from .paths import LocalPathsMixin, _ALIAS_RE, ExecutionPathError
from .environment import LocalEnvironmentMixin, _HOST_INTERNAL_ENV_PREFIXES, _CREDENTIAL_ENV_NAME_RE
from .shell import LocalShellMixin
from .process import (
    LocalProcessMixin,
    _CANCEL_CONFIRM_GRACE_SECONDS,
    _DetachedReaders,
    _MAX_DETACHED_READER_PAIRS,
    _OUTPUT_DRAIN_GRACE_SECONDS,
)
from .output import LocalOutputMixin


class TrustedLocalExecutor(
    LocalPathsMixin,
    LocalEnvironmentMixin,
    LocalShellMixin,
    LocalProcessMixin,
    LocalOutputMixin,
    ExecutionProvider,
):
    """Local process provider with the host user's real filesystem authority."""

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        run_log_dir: str | Path,
        provider_id: str = "local",
        allowed_env_names: Sequence[str] | None = None,
        credential_env_names: Sequence[str] | None = None,
        host_env: Mapping[str, str] | None = None,
        proxy_url: str = "",
        proxy_probe: Any = None,
        mounts: Mapping[str, str | Path] | None = None,
        store: ExecutionRunStore | None = None,
        cancel_confirm_grace_seconds: float = _CANCEL_CONFIRM_GRACE_SECONDS,
        output_drain_grace_seconds: float = _OUTPUT_DRAIN_GRACE_SECONDS,
        max_kill_attempts: int = 60,
        detached_reader_limit: int = _MAX_DETACHED_READER_PAIRS,
        run_log_retention_seconds: int | None = None,
        now: Any = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.run_log_dir = Path(run_log_dir).expanduser().resolve(strict=False)
        self.run_log_dir.mkdir(parents=True, exist_ok=True)
        if not self.run_log_dir.is_dir():
            raise ValueError("execution_run_log_directory_required")
        self.provider_id = str(provider_id or "").strip() or "local"
        self.inherit_scrubbed_host_env = allowed_env_names is None
        allowed = {str(name or "").strip() for name in (allowed_env_names or ())}
        self.allowed_env_names = {name for name in allowed if name}
        credential_names = {str(name or "").strip() for name in (credential_env_names or ())}
        invalid_credentials = sorted(
            name
            for name in credential_names
            if name
            and (
                _CREDENTIAL_ENV_NAME_RE.fullmatch(name) is None
                or any(name.upper().startswith(prefix) for prefix in _HOST_INTERNAL_ENV_PREFIXES)
            )
        )
        if invalid_credentials:
            raise ValueError(f"invalid_execution_credential_env_name:{invalid_credentials[0]}")
        self.credential_env_names = {name for name in credential_names if name}
        self.host_env = dict(host_env) if host_env is not None else dict(os.environ)
        self.windows_shell_path = self._resolve_windows_shell_path() if os.name == "nt" else ""
        self.proxy_url = str(proxy_url or "").strip()
        self._proxy_probe = proxy_probe or self._probe_http_proxy
        self._proxy_probe_lock = threading.Lock()
        self._proxy_probe_at = 0.0
        self._proxy_probe_result = False
        # These are host-executor shared caches, not project runtimes. Normal
        # full-access execution keeps the user's own Python/Node toolchains;
        # the Python user base below is only for explicit restricted mode.
        runtime_root = self.workspace_root / ".runtime"
        self.runtime_root = runtime_root
        self.python_user_base = runtime_root / "python_userbase"
        self.pip_cache_dir = runtime_root / "pip_cache"
        self.shared_cache_root = runtime_root / "cache"
        self.npm_cache_dir = runtime_root / "npm_cache"
        self.pnpm_store_dir = runtime_root / "pnpm_store"
        self.pnpm_home = runtime_root / "pnpm_home"
        self.corepack_home = runtime_root / "corepack_home"
        self.mounts: dict[str, Path] = {}
        for raw_name, raw_path in (mounts or {}).items():
            name = str(raw_name or "").strip()
            if not _ALIAS_RE.fullmatch(name):
                raise ValueError("invalid_execution_mount_alias")
            target = Path(raw_path).expanduser().resolve(strict=False)
            if not target.is_dir():
                raise ValueError(f"execution_mount_directory_missing:{name}")
            self.mounts[name] = target
        self.store = store or ExecutionRunStore(now=now)
        self.cancel_confirm_grace_seconds = max(0.1, float(cancel_confirm_grace_seconds))
        self.output_drain_grace_seconds = max(0.1, float(output_drain_grace_seconds))
        self.max_kill_attempts = max(1, int(max_kill_attempts))
        # How many inherited output-pipe pairs may stay parked after their run
        # ended. The oldest are released first; no live run is ever touched.
        self.detached_reader_limit = max(1, int(detached_reader_limit))
        retention = self.store.retention_seconds if run_log_retention_seconds is None else run_log_retention_seconds
        self.run_log_retention_seconds = max(1, int(retention))
        self._lock = threading.RLock()
        self._procs: dict[str, subprocess.Popen] = {}
        self._input_channels: dict[str, Any] = {}
        self._logs: dict[str, BinaryIO] = {}
        self._capture_failures: set[str] = set()
        self._unusable_logs: set[str] = set()
        self._pending_reasons: dict[str, str] = {}
        self._detached_readers: dict[str, _DetachedReaders] = {}
        self._open_streams: dict[str, set[str]] = {}
        self._reader_retire: dict[str, threading.Event] = {}
        self._run_secret_values: dict[str, tuple[str, ...]] = {}
        self._shutdown_requested = threading.Event()
        self.prune_run_logs()

    def availability(self) -> ExecutionAvailability:
        if not self.workspace_root.is_dir():
            return ExecutionAvailability(enabled=False, status="unavailable", reason="workspace_missing")
        return ExecutionAvailability(enabled=True, status="ready", reason="")

    def run(
        self,
        *,
        owner: ExecutionRunOwner,
        command: str,
        cwd: str = "",
        timeout_seconds: int = EXEC_DEFAULT_TIMEOUT_SECONDS,
        initial_wait_seconds: int = EXEC_MAX_INITIAL_WAIT_SECONDS,
        run_id: str = "",
        interactive: bool = False,
        background: bool = False,
    ) -> ExecRunStart:
        if not self._owner_matches(owner):
            return ExecRunStart(status=EXEC_STATUS_EXECUTION_UNKNOWN, reason="execution_provider_mismatch")
        if self._shutdown_requested.is_set():
            return ExecRunStart(status=EXEC_STATUS_FAILED, reason="execution_provider_stopping")
        clean_command = str(command or "").strip()
        if not clean_command:
            return ExecRunStart(status=EXEC_STATUS_FAILED, reason="invalid_execution_command")
        try:
            execution_timeout = normalize_timeout_seconds(timeout_seconds)
        except ValueError:
            return ExecRunStart(status=EXEC_STATUS_FAILED, reason="invalid_execution_timeout")
        try:
            workdir = self._resolve_workdir(cwd)
        except ExecutionPathError as exc:
            return ExecRunStart(status=EXEC_STATUS_FAILED, reason=f"invalid_execution_cwd:{exc}")

        self.prune_run_logs()
        self._reap_detached_readers()
        clean_run_id = str(run_id or "").strip()
        run_id = clean_run_id if is_valid_run_id(clean_run_id) else new_run_id()
        # Reserve the log and record together. A rejected competing request
        # owns only a log that this attempt actually created.
        with self._lock:
            output_ref = self._open_run_log(run_id)
            try:
                self.store.register(run_id, owner=owner, output_ref=output_ref)
            except (TypeError, ValueError, RuntimeError) as exc:
                if output_ref is not None:
                    self._discard_unregistered_log(run_id)
                return ExecRunStart(status=EXEC_STATUS_FAILED, reason=str(exc))

        env = self._build_env(workdir=workdir)
        with self._lock:
            self._run_secret_values[run_id] = tuple(
                value
                for name in sorted(self.credential_env_names)
                if (value := self._host_env_value(name)) is not None
            )
        try:
            spawn_command: str | Sequence[str] = clean_command
            if os.name == "nt":
                spawn_command = self._prepare_windows_command(clean_command)
            proc = self._spawn(spawn_command, workdir, env, interactive=True) if interactive else self._spawn(spawn_command, workdir, env)
        except Exception as exc:
            self.store.mark_terminal(
                run_id,
                EXEC_STATUS_FAILED,
                owner=owner,
                reason=f"spawn_failed:{type(exc).__name__}",
            )
            self._cleanup(run_id)
            return self._window(run_id, owner) or ExecRunStart(
                status=EXEC_STATUS_EXECUTION_UNKNOWN, run_id=run_id, reason="spawn_failed"
            )

        with self._lock:
            self._procs[run_id] = proc
            if interactive:
                from ..execution_input import InputChannel
                for key, channel in list(self._input_channels.items()):
                    if len(self._input_channels) < self.store.max_runs:
                        break
                    if channel.ended:
                        del self._input_channels[key]
                self._input_channels[run_id] = InputChannel(proc.stdin)
        if self._shutdown_requested.is_set():
            # Close the narrow race where Host shutdown begins after the
            # admission check but before the process handle is registered.
            self.store.request_cancel(run_id, owner=owner)
        threading.Thread(
            target=self._watcher,
            args=(run_id, owner, proc, execution_timeout),
            daemon=True,
        ).start()

        from ..execution_wait import foreground_wait_seconds
        deadline = time.monotonic() + (0 if background or interactive else foreground_wait_seconds(normalize_initial_wait_seconds(initial_wait_seconds)))
        while time.monotonic() < deadline:
            record = self.store.get(run_id, owner=owner)
            if record is None or record.status != EXEC_STATUS_RUNNING:
                break
            time.sleep(0.02)
        return self._window(run_id, owner) or ExecRunStart(
            status=EXEC_STATUS_EXECUTION_UNKNOWN, run_id=run_id, reason="run_record_missing"
        )

    def request_shutdown(self) -> int:
        """Stop accepting work and cooperatively terminate every active process tree."""

        self._shutdown_requested.set()
        self._reap_detached_readers(force=True)
        return self.store.request_cancel_all(provider_id=self.provider_id)

    def input(self, *, owner: ExecutionRunOwner, run_id: str, action: str = "status",
              sequence: int = 0, text: str = "", close: bool = False) -> dict[str, Any]:
        if not self._owner_matches(owner) or self.store.get(run_id, owner=owner) is None:
            return {"status": "rejected", "reason": "run_not_found"}
        with self._lock:
            channel = self._input_channels.get(run_id)
        if channel is None:
            return {"status": "rejected", "reason": "stdin_not_enabled_use_exec_run_interactive"}
        if action == "status":
            return channel.status()
        if (action != "write" or type(sequence) is not int or sequence < 1 or not isinstance(text, str)
                or len(text) > 16384 or type(close) is not bool):
            return {"status": "rejected", "reason": "invalid_input_arguments"}
        return channel.submit(sequence=sequence, text=text, close=close)

    def _owner_matches(self, owner: ExecutionRunOwner) -> bool:
        return isinstance(owner, ExecutionRunOwner) and owner.provider_id == self.provider_id

    def _cleanup(self, run_id: str) -> None:
        with self._lock:
            channel = self._input_channels.get(run_id)
        if channel is not None:
            channel.finish()
        with self._lock:
            self._procs.pop(run_id, None)
            handle = self._logs.pop(run_id, None)
            self._pending_reasons.pop(run_id, None)
            self._run_secret_values.pop(run_id, None)
            self._capture_failures.discard(run_id)
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass
