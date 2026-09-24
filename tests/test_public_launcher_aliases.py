import os
from pathlib import Path
import subprocess
import tempfile
import unittest

@unittest.skipUnless(os.name == "nt", "Windows BAT integration")
class PublicLauncherAliasTests(unittest.TestCase):
    def test_no_argument_aliases_forward_to_public_launcher_and_propagate_exit(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="akane alias ") as tmp:
            folder = Path(tmp)
            (folder / "start_akane.bat").write_text("@echo off\necho public>called.txt\nexit /b 7\n")
            for name in ("start_akane_next.bat", "start_akane_local_test.bat", "start_akane_local_qq_test.bat", "start_akane_local_capabilities.bat"):
                with self.subTest(name=name):
                    (folder / name).write_bytes((root / name).read_bytes())
                    result = subprocess.run(["cmd", "/c", name], cwd=folder, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 7)
                    self.assertEqual((folder / "called.txt").read_text().strip(), "public")
                    (folder / "called.txt").unlink()
