from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.skill_runtime import SkillRegistry


ROOT = Path(__file__).resolve().parents[1]
SKILL_SOURCE = ROOT / "skills" / "plugin-development"


class PluginDevelopmentSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        bundled = root / "bundled"
        managed = root / "managed"
        workspace = root / "workspace"
        for path in (bundled, managed, workspace):
            path.mkdir()
        target = bundled / "plugin-development"
        target.mkdir()
        (target / "SKILL.md").write_text(
            (SKILL_SOURCE / "SKILL.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.registry = SkillRegistry(
            bundled_root=bundled,
            managed_root=managed,
            execution_workspace_root=workspace,
        )

    def test_catalog_keeps_only_plugin_development_routing_metadata(self) -> None:
        tools = {
            "manage_project_workspace",
            "project_inspect",
            "workspace_write",
            "workspace_patch",
            "exec_run",
            "exec_status",
            "exec_cancel",
            "manage_extension",
        }
        catalog = self.registry.prompt_catalog(available_tool_names=tools)
        self.assertIn("plugin-development", catalog)
        self.assertIn("creating, testing, updating, installing, or debugging", catalog)
        self.assertNotIn("alias:akane-sdk", catalog)
        self.assertNotIn("approved_permissions", catalog)

    def test_skill_exposes_one_authoritative_build_to_activation_loop(self) -> None:
        content = self.registry.load("plugin-development").content
        for marker in (
            "alias:akane-sdk",
            'cwd="alias:akane-sdk"',
            "add_background_service",
            'manage_extension(action="test_source"',
            'manage_extension(action="stage_source"',
            'manage_extension(action="install"',
            "copying the exact permissions array",
            "Source tests alone do not prove host integration",
        ):
            self.assertIn(marker, content)
        self.assertNotIn("add_background_job", content)
        self.assertNotIn("/admin/plugins", content)
        self.assertNotIn("pip install", content)
        self.assertIn("Do not\n   replace `capcore`", content)


if __name__ == "__main__":
    unittest.main()
