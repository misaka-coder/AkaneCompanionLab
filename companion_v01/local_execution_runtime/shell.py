"""Windows shell selection and exact command rendering for the disclosed command language."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import ntpath
import os
import re
import shutil
from pathlib import Path


_LEADING_QUOTED_WINDOWS_EXECUTABLE_RE = re.compile(
    r"^(?P<indent>\s*)(?P<quote>[\"'])(?P<path>[^\"'\r\n]+)(?P=quote)(?=\s|$)",
)


def _split_windows_command_line(command: str) -> list[str] | None:
    """Parse an explicit Windows program invocation without a shell hop."""

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


class LocalShellMixin:
    def _resolve_windows_shell_path(self) -> str:
        """Choose one stable Windows command language for the executor process.

        PowerShell 7 is preferred because it is the Windows shell used by the
        coding-agent path and supports modern pipeline operators.  Windows
        PowerShell 5.1 remains a compatibility fallback.  The selected value
        is fixed for this provider instance and is the same value disclosed to
        the model; commands are never silently routed through ``cmd.exe``.
        """

        path_value = str(self.host_env.get("PATH") or "")
        pwsh = shutil.which("pwsh.exe", path=path_value) or shutil.which("pwsh", path=path_value)
        if pwsh:
            return str(Path(pwsh).resolve(strict=False))
        program_files = str(self.host_env.get("ProgramFiles") or "").strip()
        if program_files:
            common_pwsh = Path(program_files) / "PowerShell" / "7" / "pwsh.exe"
            if common_pwsh.is_file():
                return str(common_pwsh.resolve(strict=False))
        powershell = shutil.which("powershell.exe", path=path_value) or shutil.which(
            "powershell", path=path_value
        )
        if powershell:
            return str(Path(powershell).resolve(strict=False))
        system_root = str(self.host_env.get("SystemRoot") or r"C:\Windows").strip()
        return str(Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")

    def _prepare_windows_command(self, command: str) -> list[str]:
        """Pass one exact script argument to the disclosed PowerShell host."""

        # Compatibility for saved trajectories that already spell out a
        # PowerShell executable. Launch that argv directly so an outer
        # PowerShell never expands the inner script's `$variables` first.
        explicit = _split_windows_command_line(command)
        if explicit and ntpath.basename(explicit[0]).casefold() in {
            "powershell",
            "powershell.exe",
            "pwsh",
            "pwsh.exe",
        }:
            return explicit
        # A quoted executable is valid in cmd.exe-style argv rendering, but
        # PowerShell parses the same leading string as a value expression and
        # rejects the following arguments (for example ``-c``). Add
        # PowerShell's call operator only when the quoted token is an existing
        # executable/script path; ordinary string-output expressions keep
        # their exact meaning.
        command = self._normalize_leading_windows_executable(command)
        preamble = (
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
            "$OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
            "$global:LASTEXITCODE=$null; "
        )
        epilogue = (
            "\n$__akaneCommandSucceeded=$?; "
            "$__akaneNativeExitCode=$global:LASTEXITCODE; "
            "if (-not $__akaneCommandSucceeded) { "
            "if ($null -ne $__akaneNativeExitCode) { exit [int]$__akaneNativeExitCode }; "
            # Let successful scripts end naturally: PowerShell defers table
            # formatting, and an explicit exit 0 discards pending object rows
            # (for example Get-Item | Select-Object FullName,Length).
            "exit 1 }"
        )
        return [
            self.windows_shell_path,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"{preamble}{command}{epilogue}",
        ]

    @staticmethod
    def _normalize_leading_windows_executable(command: str) -> str:
        raw = str(command or "")
        matched = _LEADING_QUOTED_WINDOWS_EXECUTABLE_RE.match(raw)
        if matched is None:
            return raw
        candidate = Path(matched.group("path")).expanduser()
        if not candidate.is_file():
            return raw
        suffix = candidate.suffix.casefold()
        if suffix not in {".exe", ".com", ".cmd", ".bat", ".ps1"}:
            return raw
        insert_at = len(matched.group("indent"))
        return f"{raw[:insert_at]}& {raw[insert_at:]}"
