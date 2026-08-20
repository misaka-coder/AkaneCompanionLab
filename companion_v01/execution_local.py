"""TrustedLocalExecutor: the Phase 2 local execution provider.

Deliberately named "Trusted", not "Sandbox": this provider runs commands with
the host user's permissions. It is not an OS sandbox: both command arguments
and an explicit absolute ``cwd`` can reach directories available to that host
user. The inherited environment remains deliberately bounded.

Security model (Phase 2 scope; approval wiring lands in Phase 3):

* relative ``cwd`` stays inside ``workspace_root``; mount aliases resolve to
  host-authorized roots; an absolute ``cwd`` uses normal host permissions and
  must already exist as a directory.
* by default, child processes inherit the host user's ordinary environment so
  PATH, HOME and version-manager configuration work like a normal coding
  agent. Credential-like names and Akane-internal variables are removed. A
  host may opt into the older explicit-allowlist mode through configuration.
* cancellation is confirmed only after the process group is gone; a request
  alone is never reported as success (``cancel_failed`` otherwise).
* there is no command blacklist — command risk classification and ``ask``
  approvals belong to the Phase 3 capability/permission chain.

Process lifecycle:

* POSIX: ``start_new_session=True`` + ``os.killpg`` (SIGTERM, then SIGKILL
  after a grace period).
* Windows: ``CREATE_NEW_PROCESS_GROUP`` + ``taskkill /pid X /T /F`` tree kill.
* full stdout/stderr are appended to a controlled run log under
  ``run_log_dir/<run_id>.log`` from process start and surfaced through the
  ``runlog:<run_id>`` ``output_ref`` contract. ``output_ref`` is only returned
  when the log is genuinely open and being written.

This provider is intentionally not registered into the model tool schema yet
(Phase 3); Phase 2 validates process, timeout, env and path semantics with a
temporary workspace.
"""

from __future__ import annotations

import base64
import codecs
import ctypes
from ctypes import wintypes
import ntpath
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Sequence
from urllib.parse import urlsplit

from .execution_run import (
    ExecutionAvailability,
    ExecutionProvider,
    ExecutionRunOwner,
    ExecutionRunStore,
    ExecCancelResult,
    ExecRunStart,
    ExecRunStatus,
    is_valid_run_id,
    make_cursor,
    new_run_id,
    parse_cursor,
)
from .execution_specs import (
    EXEC_COMMAND_MAX_CHARS,
    EXEC_CWD_MAX_CHARS,
    EXEC_MAX_INITIAL_WAIT_SECONDS,
    EXEC_STATUS_CANCELLED,
    EXEC_STATUS_COMPLETED,
    EXEC_STATUS_EXECUTION_UNKNOWN,
    EXEC_STATUS_FAILED,
    EXEC_STATUS_RUNNING,
    EXEC_STATUS_TIMED_OUT,
    EXEC_STATUS_UNKNOWN,
    EXEC_TERMINAL_STATUSES,
    normalize_initial_wait_seconds,
    normalize_timeout_seconds,
)

_ALIAS_PREFIX = "alias:"
_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_RUN_LOG_RE = re.compile(r"^execrun_[a-f0-9]{32}\.log$")
_CANCEL_CONFIRM_GRACE_SECONDS = 3.0
_OUTPUT_DRAIN_GRACE_SECONDS = 3.0
_READ_CHUNK_BYTES = 4096
_PROXY_PROBE_TIMEOUT_SECONDS = 2.0
_PROXY_PROBE_TTL_SECONDS = 10.0
_PROXY_CONNECT_TARGET = "example.com:443"
_PROXY_ENV_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
_SENSITIVE_ENV_NAME_RE = re.compile(r"KEY|PASSWORD|SECRET|TOKEN", re.IGNORECASE)
_HOST_INTERNAL_ENV_PREFIXES = ("AKANE_",)


class ExecutionPathError(Exception):
    """Raised when a requested cwd cannot be resolved inside the workspace."""


def _unknown_status(run_id: str) -> ExecRunStatus:
    return ExecRunStatus(status=EXEC_STATUS_UNKNOWN, run_id=str(run_id or ""), reason="run_not_found")


def _split_windows_command_line(command: str) -> list[str] | None:
    """Parse a Windows command using CreateProcess-compatible quoting."""

    if os.name != "nt":
        return None
    argc = ctypes.c_int()
    shell32 = ctypes.windll.shell32
    shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    argv = shell32.CommandLineToArgvW(str(command or ""), ctypes.byref(argc))
    if not argv:
        return None
    try:
        return [argv[index] for index in range(argc.value)]
    finally:
        kernel32 = ctypes.windll.kernel32
        kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        kernel32.LocalFree.restype = wintypes.HLOCAL
        kernel32.LocalFree(ctypes.cast(argv, wintypes.HLOCAL))


class TrustedLocalExecutor(ExecutionProvider):
    """Local process provider with the host user's real filesystem authority."""

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        run_log_dir: str | Path,
        provider_id: str = "local",
        allowed_env_names: Sequence[str] | None = None,
        host_env: Mapping[str, str] | None = None,
        proxy_url: str = "",
        proxy_probe: Any = None,
        mounts: Mapping[str, str | Path] | None = None,
        store: ExecutionRunStore | None = None,
        cancel_confirm_grace_seconds: float = _CANCEL_CONFIRM_GRACE_SECONDS,
        output_drain_grace_seconds: float = _OUTPUT_DRAIN_GRACE_SECONDS,
        max_kill_attempts: int = 60,
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
        self.host_env = dict(host_env) if host_env is not None else dict(os.environ)
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
        retention = self.store.retention_seconds if run_log_retention_seconds is None else run_log_retention_seconds
        self.run_log_retention_seconds = max(1, int(retention))
        self._lock = threading.RLock()
        self._procs: dict[str, subprocess.Popen] = {}
        self._logs: dict[str, BinaryIO] = {}
        self._capture_failures: set[str] = set()
        self._unusable_logs: set[str] = set()
        self._pending_reasons: dict[str, str] = {}
        self.prune_run_logs()

    # -- ExecutionProvider interface -------------------------------------------------

    def availability(self) -> ExecutionAvailability:
        if not self.workspace_root.is_dir():
            return ExecutionAvailability(enabled=False, status="unavailable", reason="workspace_missing")
        return ExecutionAvailability(enabled=True, status="ready", reason="")

    def bind_authorized_mount(self, name: str, path: str | Path) -> None:
        """Bind a host-authorized directory without exposing it to tool arguments."""

        clean_name = str(name or "").strip()
        if not _ALIAS_RE.fullmatch(clean_name):
            raise ValueError("invalid_execution_mount_alias")
        target = Path(path).expanduser().resolve(strict=True)
        if not target.is_dir() or target.is_symlink():
            raise ValueError("execution_mount_directory_missing")
        with self._lock:
            existing = self.mounts.get(clean_name)
            if existing is not None and existing != target:
                raise ValueError("execution_mount_alias_conflict")
            self.mounts[clean_name] = target

    def model_environment(self) -> dict[str, Any]:
        """Return non-sensitive host facts plus the diagnostic toolchain manifest."""

        environment = self.prompt_environment()
        environment["toolchain"] = self._toolchain_manifest()
        return environment

    def prompt_environment(self) -> dict[str, Any]:
        """Return stable host facts without spawning version-probe processes."""

        if os.name == "nt":
            path_value = str(self.host_env.get("PATH") or "")
            has_pwsh = any((Path(part) / "pwsh.exe").is_file() for part in path_value.split(os.pathsep) if part)
            environment: dict[str, Any] = {
                "platform": "windows",
                "command_shell": "cmd.exe",
                "preferred_script_shell": "pwsh" if has_pwsh else "powershell.exe",
            }
        else:
            environment = {
                "platform": "macos" if sys.platform == "darwin" else "linux",
                "command_shell": "/bin/sh",
                "preferred_script_shell": "/bin/sh",
            }
        environment["dependency_storage"] = {
            "runtime": "host_path",
            "download_cache": "host_shared",
            "pnpm_store": "host_shared_content_addressed",
            "project_resolution": "ecosystem_native",
        }
        environment["host_access"] = {
            "filesystem": "host_user_permissions",
            "absolute_cwd": "supported",
            "environment": "ambient_non_secret",
        }
        return environment

    def _toolchain_manifest(self) -> dict[str, dict[str, str]]:
        manifest: dict[str, dict[str, str]] = {}
        path_value = self._managed_path_value()
        probes = {
            "python": (("python", "python3"), "--version"),
            "node": ("node", "--version"),
            "npm": ("npm", "--version"),
            "pnpm": ("pnpm", "--version"),
            "bun": ("bun", "--version"),
            "uv": ("uv", "--version"),
            "git": ("git", "--version"),
            "rg": ("rg", "--version"),
            "go": ("go", "version"),
            "cargo": ("cargo", "--version"),
            "rustc": ("rustc", "--version"),
            "java": ("java", "-version"),
            "dotnet": ("dotnet", "--version"),
            "cmake": ("cmake", "--version"),
            "ninja": ("ninja", "--version"),
            "gcc": ("gcc", "--version"),
            "clang": ("clang", "--version"),
            "ffmpeg": ("ffmpeg", "-version"),
        }
        for name, (binary_value, version_arg) in probes.items():
            binaries = binary_value if isinstance(binary_value, tuple) else (binary_value,)
            executable = next(
                (candidate for binary in binaries if (candidate := shutil.which(binary, path=path_value))),
                None,
            )
            if not executable:
                manifest[name] = {"status": "unavailable", "version": ""}
                continue
            try:
                completed = subprocess.run(
                    [executable, version_arg],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=False,
                    env=self._version_probe_env(path_value),
                    creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
                )
                first_line = (completed.stdout or completed.stderr or "").splitlines()[0].strip()[:120]
                manifest[name] = {
                    "status": "available" if completed.returncode == 0 else "unavailable",
                    "version": first_line if completed.returncode == 0 else "",
                }
            except (OSError, subprocess.SubprocessError, IndexError):
                manifest[name] = {"status": "unavailable", "version": ""}
        return manifest

    def _version_probe_env(self, path_value: str) -> dict[str, str]:
        env = self._inherited_environment()
        env["PATH"] = path_value
        return env

    def _inherited_environment(self) -> dict[str, str]:
        env: dict[str, str] = {}
        host_values = self.host_env
        if self.inherit_scrubbed_host_env:
            for raw_name, raw_value in host_values.items():
                name = str(raw_name or "")
                upper = name.upper()
                if (
                    raw_value is None
                    or _SENSITIVE_ENV_NAME_RE.search(name)
                    or any(upper.startswith(prefix) for prefix in _HOST_INTERNAL_ENV_PREFIXES)
                ):
                    continue
                env[name] = str(raw_value)
            return env
        if os.name == "nt":
            folded = {str(key).casefold(): (str(key), value) for key, value in host_values.items()}
            for name in sorted(self.allowed_env_names):
                original_and_value = folded.get(name.casefold())
                if original_and_value is None:
                    continue
                output_name, value = original_and_value
                if value is not None:
                    env[output_name] = str(value)
            return env
        for name in sorted(self.allowed_env_names):
            value = host_values.get(name)
            if value is not None:
                env[name] = str(value)
        return env

    @staticmethod
    def _set_env_default(
        env: dict[str, str],
        name: str,
        value: str,
        *,
        aliases: Sequence[str] = (),
    ) -> bool:
        candidates = (name, *aliases)
        if os.name == "nt":
            existing = {key.casefold() for key in env}
            if any(candidate.casefold() in existing for candidate in candidates):
                return False
        elif any(candidate in env for candidate in candidates):
            return False
        env[name] = value
        return True

    def _managed_path_entries(self) -> list[Path]:
        entries: list[Path] = []
        if os.name == "nt":
            python_bin = self.python_user_base / f"Python{sys.version_info.major}{sys.version_info.minor}" / "Scripts"
        else:
            python_bin = self.python_user_base / "bin"
        if not self.inherit_scrubbed_host_env:
            entries.append(python_bin)
        if self.pnpm_home.is_dir():
            entries.append(self.pnpm_home)
        return entries

    def _managed_path_value(self, existing_path: str | None = None) -> str:
        inherited = str(self.host_env.get("PATH") or "") if existing_path is None else str(existing_path or "")
        entries = [str(item) for item in self._managed_path_entries()]
        if inherited:
            entries.append(inherited)
        return os.pathsep.join(entries)

    def run(
        self,
        *,
        owner: ExecutionRunOwner,
        command: str,
        cwd: str = "",
        timeout_seconds: int = 300,
        initial_wait_seconds: int = EXEC_MAX_INITIAL_WAIT_SECONDS,
        run_id: str = "",
    ) -> ExecRunStart:
        if not self._owner_matches(owner):
            return ExecRunStart(status=EXEC_STATUS_EXECUTION_UNKNOWN, reason="execution_provider_mismatch")
        clean_command = str(command or "").strip()
        if not clean_command or len(clean_command) > EXEC_COMMAND_MAX_CHARS:
            return ExecRunStart(status=EXEC_STATUS_FAILED, reason="invalid_execution_command")
        try:
            workdir = self._resolve_workdir(cwd)
        except ExecutionPathError as exc:
            return ExecRunStart(status=EXEC_STATUS_FAILED, reason=f"invalid_execution_cwd:{exc}")

        self.prune_run_logs()
        clean_run_id = str(run_id or "").strip()
        run_id = clean_run_id if is_valid_run_id(clean_run_id) else new_run_id()
        output_ref = self._open_run_log(run_id)
        try:
            self.store.register(run_id, owner=owner, output_ref=output_ref)
        except (TypeError, ValueError, RuntimeError) as exc:
            self._discard_unregistered_log(run_id)
            return ExecRunStart(status=EXEC_STATUS_FAILED, reason=str(exc))

        env = self._build_env(workdir=workdir)
        try:
            spawn_command: str | Sequence[str] = clean_command
            if os.name == "nt":
                spawn_command = self._prepare_windows_command(clean_command)
            proc = self._spawn(spawn_command, workdir, env)
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
        threading.Thread(
            target=self._watcher,
            args=(run_id, owner, proc, normalize_timeout_seconds(timeout_seconds)),
            daemon=True,
        ).start()

        deadline = time.monotonic() + normalize_initial_wait_seconds(initial_wait_seconds)
        while time.monotonic() < deadline:
            record = self.store.get(run_id, owner=owner)
            if record is None or record.status != EXEC_STATUS_RUNNING:
                break
            time.sleep(0.02)
        return self._window(run_id, owner) or ExecRunStart(
            status=EXEC_STATUS_EXECUTION_UNKNOWN, run_id=run_id, reason="run_record_missing"
        )

    def status(
        self,
        *,
        owner: ExecutionRunOwner,
        run_id: str,
        cursor: str | None = None,
    ) -> ExecRunStatus:
        if not self._owner_matches(owner):
            return _unknown_status(run_id)
        result = self.store.read(run_id, cursor, owner=owner)
        record = self.store.get(run_id, owner=owner)
        if record is None:
            return result
        supplied = str(cursor or "").strip()
        parsed = parse_cursor(supplied, run_id=record.run_id) if supplied else None
        should_reload = bool(
            record.output_ref
            and run_id not in self._unusable_logs
            and (
                (not supplied and record.window_start_bytes > 0)
                or (parsed is not None and parsed < record.window_start_bytes)
            )
        )
        if should_reload:
            reloaded = self._persisted_status(record, 0 if parsed is None else parsed)
            if reloaded is not None:
                return reloaded
        return self._decorate_status(result)

    def cancel(self, *, owner: ExecutionRunOwner, run_id: str) -> ExecCancelResult:
        if not self._owner_matches(owner):
            return ExecCancelResult(ok=True, status=EXEC_STATUS_UNKNOWN, run_id=str(run_id or ""), reason="run_not_found")
        clean_run_id = str(run_id or "")
        requested = self.store.request_cancel(clean_run_id, owner=owner)
        if requested == EXEC_STATUS_UNKNOWN:
            return ExecCancelResult(ok=True, status=EXEC_STATUS_UNKNOWN, run_id=clean_run_id, reason="run_not_found")
        record = self.store.get(clean_run_id, owner=owner)
        if record is not None and record.status == EXEC_STATUS_EXECUTION_UNKNOWN:
            return ExecCancelResult(ok=False, status="cancel_failed", run_id=clean_run_id, reason="termination_unconfirmed")
        if requested == "already_ended":
            return ExecCancelResult(ok=True, status="already_ended", run_id=clean_run_id, reason="already_ended")

        with self._lock:
            proc = self._procs.get(clean_run_id)
        if proc is None:
            return ExecCancelResult(ok=False, status="cancel_failed", run_id=clean_run_id, reason="process_handle_missing")

        deadline = time.monotonic() + self.cancel_confirm_grace_seconds
        while time.monotonic() < deadline:
            record = self.store.get(clean_run_id, owner=owner)
            if record is not None and record.status != EXEC_STATUS_RUNNING:
                break
            time.sleep(0.02)
        record = self.store.get(clean_run_id, owner=owner)
        if record is not None and record.status == EXEC_STATUS_CANCELLED:
            return ExecCancelResult(ok=True, status=EXEC_STATUS_CANCELLED, run_id=clean_run_id, reason="")
        if record is not None and record.status == EXEC_STATUS_EXECUTION_UNKNOWN:
            return ExecCancelResult(ok=False, status="cancel_failed", run_id=clean_run_id, reason="termination_unconfirmed")
        if record is not None and record.status in EXEC_TERMINAL_STATUSES:
            return ExecCancelResult(ok=True, status="already_ended", run_id=clean_run_id, reason=record.status)
        return ExecCancelResult(ok=False, status="cancel_failed", run_id=clean_run_id, reason="termination_not_confirmed")

    # -- helpers ---------------------------------------------------------------------

    def _owner_matches(self, owner: ExecutionRunOwner) -> bool:
        return isinstance(owner, ExecutionRunOwner) and owner.provider_id == self.provider_id

    def _window(self, run_id: str, owner: ExecutionRunOwner) -> ExecRunStart | None:
        result = self.store.window_snapshot(run_id, owner=owner)
        record = self.store.get(run_id, owner=owner)
        if result is None or record is None:
            return result
        if record.window_start_bytes > 0 and record.output_ref and run_id not in self._unusable_logs:
            page = self._read_persisted_page(
                run_id,
                offset=0,
                absolute_end=record.absolute_end_bytes,
                max_bytes=self.store.initial_output_bytes,
                max_lines=self.store.initial_output_lines,
            )
            if page is not None:
                text, page_end = page
                return ExecRunStart(
                    status=record.status,
                    run_id=record.run_id,
                    exit_code=record.exit_code,
                    stdout=text,
                    stderr="",
                    next_cursor=(
                        make_cursor(record.run_id, page_end)
                        if page_end < record.absolute_end_bytes or record.status == EXEC_STATUS_RUNNING
                        else None
                    ),
                    output_ref=record.output_ref,
                    reason="output_reloaded",
                )
        reason = self._visible_reason(run_id, result.reason)
        output_ref = None if run_id in self._unusable_logs else result.output_ref
        return replace(result, reason=reason, output_ref=output_ref)

    def _resolve_workdir(self, value: str) -> Path:
        raw = str(value or "").strip()
        if not raw:
            return self.workspace_root
        if len(raw) > EXEC_CWD_MAX_CHARS:
            raise ExecutionPathError("cwd_too_long")
        if raw.startswith(_ALIAS_PREFIX):
            alias_value = raw[len(_ALIAS_PREFIX) :].strip().replace("\\", "/")
            name, _, suffix = alias_value.partition("/")
            if not name or name not in self.mounts:
                raise ExecutionPathError("unknown_mount_alias")
            resolved = self.mounts[name]
            if not resolved.is_dir():
                raise ExecutionPathError("mount_directory_missing")
            if not suffix:
                return resolved
            suffix_path = Path(suffix)
            if suffix_path.is_absolute() or ".." in suffix_path.parts:
                raise ExecutionPathError("path_traversal_not_allowed")
            candidate = (resolved / suffix_path).resolve(strict=False)
            try:
                candidate.relative_to(resolved)
            except ValueError:
                raise ExecutionPathError("path_escapes_mount") from None
            if not candidate.is_dir():
                raise ExecutionPathError("cwd_not_found")
            return candidate
        candidate_path = Path(raw)
        if candidate_path.is_absolute():
            try:
                candidate = candidate_path.expanduser().resolve(strict=True)
            except (OSError, RuntimeError):
                raise ExecutionPathError("cwd_not_found") from None
            if not candidate.is_dir():
                raise ExecutionPathError("cwd_not_found")
            return candidate
        if ".." in candidate_path.parts:
            raise ExecutionPathError("path_traversal_not_allowed")
        candidate = (self.workspace_root / candidate_path).resolve(strict=False)
        try:
            candidate.relative_to(self.workspace_root)
        except ValueError:
            raise ExecutionPathError("path_escapes_workspace") from None
        if not candidate.is_dir():
            raise ExecutionPathError("cwd_not_found")
        return candidate

    def _build_env(self, *, workdir: Path | None = None) -> dict[str, str]:
        env = self._inherited_environment()
        # Resource-mode commands own this directory and must keep temporary
        # artifacts inside it. Ordinary host/project commands retain the
        # user's real temp environment like a normal local coding agent.
        effective_workdir = (workdir or self.workspace_root).resolve()
        try:
            relative_workdir = effective_workdir.relative_to(self.workspace_root)
        except ValueError:
            relative_workdir = None
        if relative_workdir is not None and relative_workdir.parts[:1] == (".akane_exec_runs",):
            managed_tmp = str(effective_workdir)
            env["TMPDIR"] = managed_tmp
            env["TMP"] = managed_tmp
            env["TEMP"] = managed_tmp
        managed_defaults = (
            ("PIP_CACHE_DIR", self.pip_cache_dir, ()),
            ("NPM_CONFIG_CACHE", self.npm_cache_dir, ("npm_config_cache",)),
            ("PNPM_HOME", self.pnpm_home, ()),
            ("COREPACK_HOME", self.corepack_home, ()),
            ("NPM_CONFIG_STORE_DIR", self.pnpm_store_dir, ("npm_config_store_dir",)),
        )
        for name, directory, aliases in managed_defaults:
            if self._set_env_default(env, name, str(directory), aliases=aliases):
                directory.mkdir(parents=True, exist_ok=True)
        self._set_env_default(env, "PIP_DISABLE_PIP_VERSION_CHECK", "1")
        if os.name != "nt" and self._set_env_default(env, "XDG_CACHE_HOME", str(self.shared_cache_root)):
            self.shared_cache_root.mkdir(parents=True, exist_ok=True)
        if not self.inherit_scrubbed_host_env:
            self.python_user_base.mkdir(parents=True, exist_ok=True)
            env["PYTHONUSERBASE"] = str(self.python_user_base)
            env["PIP_USER"] = "1"
            # PYTHONNOUSERSITE treats even "0" as enabled, so absence is the
            # only correct value for this explicitly managed user-site mode.
            env.pop("PYTHONNOUSERSITE", None)
            env.pop("PIP_REQUIRE_VIRTUALENV", None)
        existing_path = str(env.get("PATH") or "")
        env["PATH"] = self._managed_path_value(existing_path)
        if self.proxy_url:
            # A configured optional proxy is host-owned state, not part of the
            # model tool schema or command arguments. Never leave stale proxy
            # values inherited from the service environment: use the proxy only
            # after an end-to-end CONNECT probe succeeds, otherwise preserve the
            # executor's normal direct network behavior.
            for name in _PROXY_ENV_NAMES:
                env.pop(name, None)
            if self._optional_proxy_available():
                for name in _PROXY_ENV_NAMES:
                    env[name] = self.proxy_url
                env["NO_PROXY"] = "127.0.0.1,localhost,::1"
                env["no_proxy"] = env["NO_PROXY"]
        return env

    def _optional_proxy_available(self) -> bool:
        if not self.proxy_url:
            return False
        now = time.monotonic()
        with self._proxy_probe_lock:
            if now - self._proxy_probe_at < _PROXY_PROBE_TTL_SECONDS:
                return self._proxy_probe_result
            try:
                available = bool(self._proxy_probe(self.proxy_url))
            except Exception:
                available = False
            self._proxy_probe_at = now
            self._proxy_probe_result = available
            return available

    @staticmethod
    def _probe_http_proxy(proxy_url: str) -> bool:
        parsed = urlsplit(str(proxy_url or ""))
        if parsed.scheme.lower() != "http" or not parsed.hostname or not parsed.port:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        request = (
            f"CONNECT {_PROXY_CONNECT_TARGET} HTTP/1.1\r\n"
            f"Host: {_PROXY_CONNECT_TARGET}\r\n"
            "Proxy-Connection: close\r\n\r\n"
        ).encode("ascii")
        with socket.create_connection(
            (parsed.hostname, int(parsed.port)),
            timeout=_PROXY_PROBE_TIMEOUT_SECONDS,
        ) as connection:
            connection.settimeout(_PROXY_PROBE_TIMEOUT_SECONDS)
            connection.sendall(request)
            response = connection.recv(256)
        first_line = response.split(b"\r\n", 1)[0]
        parts = first_line.split(b" ", 2)
        return len(parts) >= 2 and parts[0].startswith(b"HTTP/") and parts[1] == b"200"

    def _open_run_log(self, run_id: str) -> str | None:
        try:
            handle = open(self._run_log_path(run_id), "xb")
        except OSError:
            return None
        with self._lock:
            self._logs[run_id] = handle
        return f"runlog:{run_id}"

    def _prepare_windows_command(self, command: str) -> str | list[str]:
        """Run explicit PowerShell programs via ``-EncodedCommand``.

        Passing a non-trivial ``powershell -Command`` program through
        ``cmd.exe /c`` adds a second quoting language. Loops, dictionaries and
        nested quotes can then hang or execute a different command. A direct
        argv launch with PowerShell's UTF-16LE encoded-command contract
        preserves the exact program without a temporary file or path leak.
        """

        argv = _split_windows_command_line(command)
        if not argv:
            return command
        executable = ntpath.basename(argv[0]).casefold()
        if executable not in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
            return command
        command_index = next(
            (index for index, value in enumerate(argv[1:], start=1) if value.casefold() in {"-command", "-c"}),
            -1,
        )
        if command_index < 0 or command_index + 2 != len(argv):
            return command
        script = argv[command_index + 1]
        if not script or script == "-":
            return command
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        return [*argv[:command_index], "-EncodedCommand", encoded]

    def _spawn(self, command: str | Sequence[str], workdir: Path, env: dict[str, str]) -> subprocess.Popen:
        if os.name == "nt":
            # Passing the command string with shell=True routes it through
            # COMSPEC verbatim; an argv-list `cmd /c` form re-quotes the string
            # (list2cmdline) and cmd's quote-stripping mangles inner quotes.
            return subprocess.Popen(
                command,
                cwd=str(workdir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=isinstance(command, str),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        return subprocess.Popen(
            ["/bin/sh", "-c", command],
            cwd=str(workdir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

    def _kill_process_group(self, proc: subprocess.Popen) -> bool:
        if proc.poll() is not None:
            return True
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/pid", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            return proc.poll() is not None
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            return proc.poll() is not None
        except PermissionError:
            return False
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        return proc.poll() is not None

    def _watcher(self, run_id: str, owner: ExecutionRunOwner, proc: subprocess.Popen, timeout_seconds: int) -> None:
        readers = [
            threading.Thread(
                target=self._read_pipe,
                args=(run_id, owner, proc.stdout, "stdout"),
                daemon=True,
            ),
            threading.Thread(
                target=self._read_pipe,
                args=(run_id, owner, proc.stderr, "stderr"),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()
        start = time.monotonic()
        cancellation_triggered = False
        timeout_triggered = False
        record_missing = False
        gave_up = False
        kill_attempts = 0
        try:
            while True:
                if self.store.get(run_id, owner=owner) is None:
                    record_missing = True
                    if proc.poll() is None and not self._kill_process_group(proc):
                        time.sleep(0.1)
                        continue
                    break
                exit_code = proc.poll()
                if exit_code is not None:
                    break

                if not cancellation_triggered and not timeout_triggered:
                    if self.store.cancel_requested(run_id, owner=owner):
                        cancellation_triggered = True
                        self._set_pending_reason(run_id, "cancel_termination_pending")
                    elif timeout_seconds and time.monotonic() - start >= timeout_seconds:
                        timeout_triggered = True
                        self._set_pending_reason(run_id, "timeout_termination_pending")

                if cancellation_triggered or timeout_triggered:
                    if self._kill_process_group(proc):
                        continue
                    kill_attempts += 1
                    if kill_attempts >= self.max_kill_attempts:
                        # Honest give-up: never claim cancelled/timed_out without
                        # confirmation. Record an evictable execution_unknown
                        # terminal and stop retrying.
                        gave_up = True
                        self._set_pending_reason(run_id, "termination_unconfirmed")
                        if self.store.get(run_id, owner=owner) is not None:
                            self.store.mark_terminal(
                                run_id,
                                EXEC_STATUS_EXECUTION_UNKNOWN,
                                owner=owner,
                                reason="termination_unconfirmed",
                            )
                        break
                    time.sleep(0.1)
                    continue
                time.sleep(0.02)

            drain_ok = self._drain_readers(readers, (proc.stdout, proc.stderr))
            capture_failed = self._capture_failed(run_id)
            log_failed = self._log_failed(run_id)
            if record_missing or gave_up:
                return
            if timeout_triggered:
                reason = "execution_timeout"
                if not drain_ok or capture_failed:
                    reason = "execution_timeout_output_incomplete"
                elif log_failed:
                    reason = "execution_timeout_output_persistence_failed"
                self.store.mark_terminal(
                    run_id,
                    EXEC_STATUS_TIMED_OUT,
                    owner=owner,
                    exit_code=exit_code,
                    reason=reason,
                )
            elif cancellation_triggered:
                if self.store.confirm_cancelled(run_id, owner=owner) and (not drain_ok or capture_failed):
                    # Cancellation is still confirmed, but do not hide a partial transcript.
                    self._set_pending_reason(run_id, "cancelled_output_incomplete")
            elif not drain_ok or capture_failed:
                self.store.mark_terminal(
                    run_id,
                    EXEC_STATUS_FAILED,
                    owner=owner,
                    exit_code=exit_code,
                    reason="output_capture_incomplete",
                )
            else:
                reason = "output_persistence_failed" if log_failed else ""
                self.store.mark_terminal(
                    run_id,
                    EXEC_STATUS_COMPLETED if exit_code == 0 else EXEC_STATUS_FAILED,
                    owner=owner,
                    exit_code=exit_code,
                    reason=reason,
                )
        except Exception as exc:
            terminated = self._kill_process_group(proc)
            self._drain_readers(readers, (proc.stdout, proc.stderr))
            if terminated:
                self.store.mark_terminal(
                    run_id,
                    EXEC_STATUS_FAILED,
                    owner=owner,
                    exit_code=proc.poll(),
                    reason=f"execution_watcher_failed:{type(exc).__name__}",
                )
            else:
                self._set_pending_reason(run_id, "watcher_failed_termination_not_confirmed")
        finally:
            if proc.poll() is not None or gave_up:
                self._cleanup(run_id)

    def _drain_readers(self, readers: Sequence[threading.Thread], pipes: Sequence[Any]) -> bool:
        # Never close the pipes from here: a reader blocked in pipe.read() (the
        # process is still alive, e.g. after an unconfirmed kill) would keep the
        # close() blocked on Windows and deadlock the watcher. Readers close
        # their own pipe in their finally once read() hits EOF.
        del pipes
        deadline = time.monotonic() + self.output_drain_grace_seconds
        for reader in readers:
            reader.join(timeout=max(0.0, deadline - time.monotonic()))
        return not any(reader.is_alive() for reader in readers)

    def _read_pipe(self, run_id: str, owner: ExecutionRunOwner, pipe: Any, stream: str) -> None:
        # Decode with an incremental UTF-8 decoder so a multi-byte character
        # split across two read chunks is never replaced by U+FFFD (the decoder
        # buffers incomplete sequences internally). The store and the run log
        # must keep the same byte stream, otherwise cursor offsets and the
        # persisted output_ref diverge from the visible text.
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while True:
                chunk = pipe.read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                text = decoder.decode(chunk, final=False)
                if text:
                    self._record_output(run_id, owner, stream, text)
            tail = decoder.decode(b"", final=True)
            if tail:
                self._record_output(run_id, owner, stream, tail)
        except Exception:
            with self._lock:
                self._capture_failures.add(run_id)
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    def _record_output(self, run_id: str, owner: ExecutionRunOwner, stream: str, text: str) -> None:
        data = text.encode("utf-8")
        with self._lock:
            try:
                # Store and file writes share one ordering lock, so a cursor's
                # absolute byte offset means the same thing in both places.
                self.store.append_output(run_id, stream, text, owner=owner)
            except Exception:
                self._capture_failures.add(run_id)
                return
            handle = self._logs.get(run_id)
            if handle is None or run_id in self._unusable_logs:
                return
            try:
                written = handle.write(data)
                handle.flush()
                if written != len(data):
                    raise OSError("short execution log write")
            except Exception:
                self._unusable_logs.add(run_id)
                self._logs.pop(run_id, None)
                try:
                    handle.close()
                except Exception:
                    pass

    def _persisted_status(self, record: Any, offset: int) -> ExecRunStatus | None:
        page = self._read_persisted_page(
            record.run_id,
            offset=offset,
            absolute_end=record.absolute_end_bytes,
            max_bytes=self.store.status_output_bytes,
            max_lines=self.store.status_output_lines,
        )
        if page is None:
            return None
        text, page_end = page
        return ExecRunStatus(
            status=record.status,
            run_id=record.run_id,
            exit_code=record.exit_code,
            tail=text,
            next_cursor=(
                make_cursor(record.run_id, page_end)
                if page_end < record.absolute_end_bytes or record.status == EXEC_STATUS_RUNNING
                else None
            ),
            output_ref=record.output_ref,
            reason="output_reloaded",
            started_at=float(getattr(record, "created_at", 0.0) or 0.0),
            finished_at=float(getattr(record, "finished_at", 0.0) or 0.0),
        )

    def _read_persisted_page(
        self,
        run_id: str,
        *,
        offset: int,
        absolute_end: int,
        max_bytes: int,
        max_lines: int,
    ) -> tuple[str, int] | None:
        if offset < 0 or offset > absolute_end:
            return None
        path = self._run_log_path(run_id)
        try:
            with self._lock:
                handle = self._logs.get(run_id)
                if handle is not None:
                    handle.flush()
                with open(path, "rb") as source:
                    size = min(os.fstat(source.fileno()).st_size, absolute_end)
                    if offset > size:
                        return None
                    source.seek(offset)
                    data = source.read(min(max_bytes + 4, size - offset))
        except OSError:
            return None
        if data and data[0] & 0xC0 == 0x80:
            return None
        take = _bounded_utf8_prefix(data, max_bytes=max_bytes, max_lines=max_lines)
        page = data[:take]
        try:
            text = page.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None
        return text, offset + len(page)

    def _decorate_status(self, result: ExecRunStatus) -> ExecRunStatus:
        return replace(
            result,
            reason=self._visible_reason(result.run_id, result.reason),
            output_ref=None if result.run_id in self._unusable_logs else result.output_ref,
        )

    def _visible_reason(self, run_id: str, fallback: str) -> str:
        with self._lock:
            pending = self._pending_reasons.get(run_id, "")
            unusable = run_id in self._unusable_logs
        if pending:
            return pending
        if unusable:
            return "output_compacted_without_persistence" if fallback == "output_compacted" else "output_persistence_failed"
        return fallback

    def _set_pending_reason(self, run_id: str, reason: str) -> None:
        with self._lock:
            self._pending_reasons[run_id] = reason

    def _capture_failed(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._capture_failures

    def _log_failed(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._unusable_logs

    def _run_log_path(self, run_id: str) -> Path:
        return self.run_log_dir / f"{run_id}.log"

    def _discard_unregistered_log(self, run_id: str) -> None:
        with self._lock:
            handle = self._logs.pop(run_id, None)
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        try:
            self._run_log_path(run_id).unlink(missing_ok=True)
        except OSError:
            pass

    def prune_run_logs(self) -> int:
        """Delete expired executor-owned log files; active logs are never touched."""

        cutoff = time.time() - self.run_log_retention_seconds
        removed = 0
        with self._lock:
            active = set(self._logs)
        try:
            candidates = list(self.run_log_dir.iterdir())
        except OSError:
            return 0
        for path in candidates:
            if path.stem in active or not _RUN_LOG_RE.fullmatch(path.name):
                continue
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    run_id = path.stem
                    with self._lock:
                        self._unusable_logs.discard(run_id)
                    removed += 1
            except OSError:
                continue
        return removed

    def _cleanup(self, run_id: str) -> None:
        with self._lock:
            self._procs.pop(run_id, None)
            handle = self._logs.pop(run_id, None)
            self._pending_reasons.pop(run_id, None)
            self._capture_failures.discard(run_id)
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass


def _bounded_utf8_prefix(data: bytes, *, max_bytes: int, max_lines: int) -> int:
    end = min(len(data), max(1, int(max_bytes)))
    while end > 0 and end < len(data) and data[end] & 0xC0 == 0x80:
        end -= 1
    bounded = data[:end]
    position = -1
    for _ in range(max(1, int(max_lines))):
        position = bounded.find(b"\n", position + 1)
        if position < 0:
            return end
    next_newline = bounded.find(b"\n", position + 1)
    return end if next_newline < 0 else position + 1
