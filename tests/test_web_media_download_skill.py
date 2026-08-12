from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.skill_runtime import SkillRegistry


class WebMediaDownloadSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        bundled = root / "bundled"
        managed = root / "managed"
        workspace = root / "workspace"
        for path in (bundled, managed, workspace):
            path.mkdir(parents=True, exist_ok=True)
        source = Path(__file__).resolve().parents[1] / "skills" / "web-media-download"
        target = bundled / "web-media-download"
        target.mkdir()
        (target / "SKILL.md").write_text(
            (source / "SKILL.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.registry = SkillRegistry(
            bundled_root=bundled,
            managed_root=managed,
            execution_workspace_root=workspace,
        )

    def test_catalog_routes_public_web_media_downloads(self) -> None:
        entry = self.registry.snapshot().by_name()["web-media-download"]
        self.assertIn("shortened share link", entry.description)
        self.assertIn("public media identifier", entry.description)

    def test_loaded_skill_handles_incomplete_links_and_title_fallback(self) -> None:
        content = self.registry.load("web-media-download").content
        self.assertIn("domain such as `b23.tv` is incomplete", content)
        self.assertIn("search by that title", content)
        self.assertIn("`bilisearch:`", content)
        self.assertIn("not `bilisearchvideo:`", content)

    def test_loaded_skill_preserves_real_command_failure_and_delivery_contract(self) -> None:
        content = self.registry.load("web-media-download").content
        self.assertIn("Do not\npipe the main command", content)
        self.assertIn('output_globs=["outputs/media.*"]', content)
        self.assertIn("artifact_status` is `registered", content)
        self.assertIn("queueing, not final client receipt", content)


if __name__ == "__main__":
    unittest.main()
