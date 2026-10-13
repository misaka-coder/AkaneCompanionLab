"""Stable host facts, toolchain probes, scrubbed environments, caches, and optional proxy."""
from __future__ import annotations

import ntpath
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit


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


_CREDENTIAL_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class LocalEnvironmentMixin:
    def model_environment(self) -> dict[str, Any]:
        """Return non-sensitive host facts plus the diagnostic toolchain manifest."""

        environment = self.prompt_environment()
        environment["toolchain"] = self._toolchain_manifest()
        return environment

    def prompt_environment(self) -> dict[str, Any]:
        """Return stable host facts without spawning version-probe processes."""

        if os.name == "nt":
            shell_name = ntpath.basename(self.windows_shell_path).casefold()
            environment: dict[str, Any] = {
                "platform": "windows",
                "command_shell": "pwsh" if shell_name == "pwsh.exe" else "powershell.exe",
                "preferred_script_shell": "pwsh" if shell_name == "pwsh.exe" else "powershell.exe",
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
        if self.credential_env_names:
            environment["host_access"]["credential_env_refs"] = {
                name: "configured" if self._host_env_value(name) is not None else "missing"
                for name in sorted(self.credential_env_names)
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
        env = self._inherited_environment(include_credentials=False)
        env["PATH"] = path_value
        return env

    def _host_env_value(self, name: str) -> str | None:
        if os.name == "nt":
            hit = next((value for key, value in self.host_env.items() if str(key).casefold() == name.casefold()), None)
        else:
            hit = self.host_env.get(name)
        return None if hit is None or not str(hit) else str(hit)

    def _inherited_environment(self, *, include_credentials: bool = True) -> dict[str, str]:
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
        elif os.name == "nt":
            folded = {str(key).casefold(): (str(key), value) for key, value in host_values.items()}
            for name in sorted(self.allowed_env_names):
                original_and_value = folded.get(name.casefold())
                if original_and_value is None:
                    continue
                output_name, value = original_and_value
                if value is not None:
                    env[output_name] = str(value)
        else:
            for name in sorted(self.allowed_env_names):
                value = host_values.get(name)
                if value is not None:
                    env[name] = str(value)
        if os.name == "nt":
            # PowerShell can start with a deliberately small allowlist, but
            # native children launched from it still require Windows' basic
            # process environment (notably SystemRoot for DLL resolution).
            # These are OS facts, not credentials or Akane-private state.
            for name in ("SystemRoot", "WINDIR", "SystemDrive", "COMSPEC", "PATHEXT"):
                if any(str(existing).casefold() == name.casefold() for existing in env):
                    continue
                value = self._host_env_value(name)
                if value is None:
                    value = next(
                        (
                            str(item)
                            for key, item in os.environ.items()
                            if str(key).casefold() == name.casefold() and str(item)
                        ),
                        None,
                    )
                if value is not None:
                    env[name] = value
        if include_credentials:
            for name in sorted(self.credential_env_names):
                value = self._host_env_value(name)
                if value is not None:
                    env[name] = value
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
