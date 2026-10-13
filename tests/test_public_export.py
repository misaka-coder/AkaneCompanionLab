from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "Windows public exporter")
class PublicExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source = self.root / "source"
        self.source.mkdir()
        scripts = self.source / "scripts"
        scripts.mkdir()
        shutil.copyfile(ROOT / "scripts/export_public_alpha.ps1", scripts / "export_public_alpha.ps1")
        # This fixture isolates selection and privacy behavior from the full audit.
        (scripts / "audit_public_release.ps1").write_text("param([string]$ReleaseRoot)\n", encoding="utf-8")
        self.write("marker.txt", "committed")
        self.write(".env", "PRIVATE=test-only\n")
        self.write(".env.saved", "PRIVATE=test-only\n")
        self.write("desktop_pet_creator_kit/characters/akane_v1/persona.md", "starter")
        self.write("desktop_pet_creator_kit/characters/akane_v1/_local/history.txt", "private")
        self.write("desktop_pet_creator_kit/characters/custom/persona.md", "private")
        self.write("scripts/relogin_cloud_bot.ps1", "private")
        self.write("deploy/recovery/akane-bot-recovery-root.sh", "private")
        self.git("init", "--initial-branch", "main")
        self.git("add", "--all")
        self.git("-c", "commit.gpgsign=false", "-c", "user.name=ReleaseFixture", "-c",
                 "user.email=release-fixture@example.invalid", "commit", "-m", "fixture")
        self.write("marker.txt", "working-change")
        self.write("scratch.py", "private untracked scratch")

    def write(self, relative: str, content: str) -> None:
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.source), *args], check=True, capture_output=True)

    def export(self, name: str, *extra: str) -> Path:
        output = self.root / name
        subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-File", str(self.source / "scripts/export_public_alpha.ps1"),
             "-OutputPath", str(output), *extra],
            check=True, capture_output=True, timeout=120,
        )
        self.assertFalse((output / ".git").exists())
        self.assertFalse((output / ".public-source.zip").exists())
        for private in (".env", ".env.saved", "scratch.py", "scripts/relogin_cloud_bot.ps1",
                        "deploy/recovery/akane-bot-recovery-root.sh",
                        "desktop_pet_creator_kit/characters/custom",
                        "desktop_pet_creator_kit/characters/akane_v1/_local"):
            self.assertFalse((output / private).exists(), private)
        self.assertEqual((output / "desktop_pet_creator_kit/characters/akane_v1/persona.md").read_text(), "starter")
        return output

    def test_commit_export_ignores_working_changes_and_untracked_files(self) -> None:
        output = self.export("committed-export", "-SourceRef", "HEAD")
        self.assertEqual((output / "marker.txt").read_text(), "committed")

    def test_working_export_includes_only_tracked_files(self) -> None:
        output = self.export("working-export")
        self.assertEqual((output / "marker.txt").read_text(), "working-change")
