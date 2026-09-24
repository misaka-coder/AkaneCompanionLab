from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion_v01.skill_runtime import SkillRegistry
from scripts import verify_plugin_sdk_docs

import akane_plugin


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
            "imports only `akane_plugin`",
            "@plugin.tool",
            "@plugin.on",
            "@plugin.service",
            '@plugin.connection("name")',
            'execution_class="long_task"',
            'manage_extension(action="test_source"',
            'manage_extension(action="stage_source"',
            'manage_extension(action="install"',
            "copying the exact permissions array",
            "Source tests alone do not prove host integration",
        ):
            self.assertIn(marker, content)
        # The Skill teaches the current SDK, not the API-v1 registrar that new
        # projects must not use.
        for legacy in ("add_capability_adapter", "get_agent_event_port", "add_background_service"):
            self.assertNotIn(legacy, content)
        self.assertNotIn("add_background_job", content)
        self.assertNotIn("/admin/plugins", content)
        self.assertNotIn("pip install", content)
        self.assertIn("Do not\n   replace `akane_plugin`", content)
        self.assertNotIn("companion_v01.plugin_api", content)

    def test_sdk_documentation_matches_the_packaged_version_and_api(self) -> None:
        self.assertEqual(verify_plugin_sdk_docs.check(), ())

    def test_sdk_documentation_check_detects_a_stale_claim(self) -> None:
        original = verify_plugin_sdk_docs.EXAMPLES_README.read_text(encoding="utf-8")
        current = akane_plugin.__version__
        stale = original.replace(f"**{current}**", "**0.6.0**")
        self.assertNotEqual(stale, original)
        with tempfile.TemporaryDirectory() as temporary:
            document = Path(temporary) / "README.md"
            document.write_text(stale, encoding="utf-8")
            verify_plugin_sdk_docs.EXAMPLES_README = document
            try:
                problems = verify_plugin_sdk_docs.check_release_claims()
            finally:
                verify_plugin_sdk_docs.EXAMPLES_README = ROOT / "examples" / "plugins" / "README.md"
        self.assertTrue(any("0.6.0" in problem for problem in problems), problems)


if __name__ == "__main__":
    unittest.main()
