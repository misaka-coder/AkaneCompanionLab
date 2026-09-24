from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(os.name == "nt", "Windows launcher binding")
class LocalRuntimeBindingTests(unittest.TestCase):
    def test_sourcing_helper_does_not_enable_personal_binding(self):
        script = Path(__file__).resolve().parents[1] / "scripts/akane_local_runtime.ps1"
        code = (
            "$beforePath=$env:PATH; $beforeRoot=$env:EXECUTION_WORKSPACE_ROOT; "
            f". '{str(script).replace(chr(39), chr(39) * 2)}'; "
            "@{pathUnchanged=($beforePath -eq $env:PATH);"
            "rootUnchanged=($beforeRoot -eq $env:EXECUTION_WORKSPACE_ROOT)} | ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", code], capture_output=True, text=True, check=True, timeout=15
        )
        self.assertEqual(json.loads(result.stdout), {"pathUnchanged": True, "rootUnchanged": True})

    def test_binding_reuses_interpreter_across_workdirs_without_global_path_changes(self):
        script = Path(__file__).resolve().parents[1] / "scripts/akane_local_runtime.ps1"
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            code = (
                f". {quote(script)}; "
                "$oldUserPath=[Environment]::GetEnvironmentVariable('PATH','User'); "
                "$oldMachinePath=[Environment]::GetEnvironmentVariable('PATH','Machine'); "
                f"$null=Set-AkaneLocalRuntime -Root {quote(root)} -Python {quote(sys.executable)}; "
                "$first=python -c 'import sys; print(sys.executable)'; "
                "Set-Location $env:EXECUTION_WORKSPACE_ROOT; "
                "$second=python -c 'import sys; print(sys.executable)'; "
                "@{first=$first;second=$second;document=$env:AKANE_DOCUMENT_PYTHON;temp=$env:TEMP;"
                "workspace=$env:EXECUTION_WORKSPACE_ROOT;cache=$env:PIP_CACHE_DIR;"
                "userUnchanged=($oldUserPath -eq [Environment]::GetEnvironmentVariable('PATH','User'));"
                "machineUnchanged=($oldMachinePath -eq [Environment]::GetEnvironmentVariable('PATH','Machine'))} | ConvertTo-Json -Compress"
            )
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", code],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            binding = json.loads(result.stdout)
            for key in ("first", "second", "document"):
                self.assertEqual(Path(binding[key]).resolve(), Path(sys.executable).resolve())
            for key in ("temp", "workspace", "cache"):
                self.assertTrue(Path(binding[key]).is_relative_to(root))
                self.assertTrue(Path(binding[key]).is_dir())
            self.assertTrue(binding["userUnchanged"])
            self.assertTrue(binding["machineUnchanged"])
            self.assertFalse((root / ".venv").exists())

    def test_rejects_drive_root_before_mutation(self):
        script = Path(__file__).resolve().parents[1] / "scripts/akane_local_runtime.ps1"
        code = (
            f". '{script}'; try {{ Set-AkaneLocalRuntime -Root '{Path.cwd().anchor}' -Python 'missing' }} "
            "catch { $_.Exception.Message; exit 0 }; exit 1"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", code], capture_output=True, text=True, check=True, timeout=15
        )
        self.assertIn("runtime_root_must_be_dedicated_absolute_directory", result.stdout)
