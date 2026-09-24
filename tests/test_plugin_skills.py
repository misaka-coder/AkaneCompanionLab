from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    SKILL_CONTRIBUTION_PERMISSION,
    PluginManifest,
)
from companion_v01.plugin_contribution_policy import TrustedStatefulPluginContributionPolicy
from companion_v01.plugin_host import PluginHost
from companion_v01.skill_runtime import SkillRegistry


class _Distribution:
    version = "0.1.0"
    metadata = {"Name": "akane-test-plugin-skill"}

    @staticmethod
    def read_text(filename: str) -> None:
        del filename
        return None


class _EntryPoint:
    def __init__(self, name: str, factory: Callable[[], Any]) -> None:
        self.name = name
        self.dist = _Distribution()
        self._factory = factory

    def load(self) -> Callable[[], Any]:
        return self._factory


class _SkillPlugin:
    def __init__(self, plugin_id: str, root: Path, *, permission: bool = True) -> None:
        self.root = root
        self.manifest = PluginManifest(
            plugin_id=plugin_id,
            plugin_version="0.1.0",
            plugin_api_version=AKANE_PLUGIN_API_VERSION,
            permissions=(SKILL_CONTRIBUTION_PERMISSION,) if permission else (),
        )

    def register(self, registrar: Any) -> None:
        registrar.add_skill(self.root)


def _write_skill(root: Path, *, name: str, body: str = "Follow the real workflow.") -> Path:
    skill_root = root / name
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: A plugin-shipped workflow used only when the task matches.\n"
        "metadata:\n"
        "  required_tools: [exec_run]\n"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )
    (skill_root / "reference.txt").write_text("plugin reference\n", encoding="utf-8")
    return skill_root


class PluginSkillContributionTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_plugin_skill_uses_existing_registry_and_disappears_on_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill_root = _write_skill(root / "package", name="sample-companion")
            plugin_id = "akane.test.skill"
            plugin = _SkillPlugin(plugin_id, skill_root)
            host = PluginHost(
                (PluginSelection(plugin_id, True),),
                contribution_policy=TrustedStatefulPluginContributionPolicy(),
                entry_points_provider=lambda: (_EntryPoint(plugin_id, lambda: plugin),),
            )
            registry = SkillRegistry(
                bundled_root=root / "bundled",
                managed_root=root / "managed",
                execution_workspace_root=root / "execution",
                contributed_roots_provider=host.skill_roots,
            )

            started = await host.start()
            self.assertEqual(started["status"], "active")
            self.assertEqual(started["skill_count"], 1)
            self.assertEqual(started["plugins"][0]["skill_names"], ["sample-companion"])
            self.assertEqual(
                host.contribution_snapshots[0].as_dict()["skills"],
                ["sample-companion"],
            )

            catalog = registry.prompt_catalog(available_tool_names={"exec_run", "load_skill"})
            self.assertIn("sample-companion", catalog)
            self.assertNotIn("Follow the real workflow", catalog)
            loaded = registry.load("sample-companion")
            self.assertEqual(loaded.status, "loaded")
            self.assertEqual(loaded.source, f"plugin:{plugin_id}")
            self.assertEqual(loaded.execution_path, "SKILL.md")
            self.assertTrue(loaded.execution_cwd.startswith("alias:plugin_skill_"))
            mount_name = loaded.execution_cwd.removeprefix("alias:")
            self.assertEqual(registry.mount_paths()[mount_name], skill_root.resolve())
            reference = registry.load("sample-companion", resource="reference.txt")
            self.assertEqual(reference.content, "plugin reference\n")
            self.assertEqual(reference.execution_path, "reference.txt")

            await host.stop()
            self.assertNotIn("sample-companion", registry.prompt_catalog())
            self.assertEqual(registry.load("sample-companion").status, "not_found")

    async def test_missing_permission_and_cross_plugin_name_conflict_fail_activation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill_a = _write_skill(root / "a", name="same-skill", body="A")
            skill_b = _write_skill(root / "b", name="same-skill", body="B")
            plugin_a = _SkillPlugin("akane.test.skill-a", skill_a)
            plugin_b = _SkillPlugin("akane.test.skill-b", skill_b)
            host = PluginHost(
                (
                    PluginSelection("akane.test.skill-a", True),
                    PluginSelection("akane.test.skill-b", True),
                ),
                contribution_policy=TrustedStatefulPluginContributionPolicy(),
                entry_points_provider=lambda: (
                    _EntryPoint("akane.test.skill-a", lambda: plugin_a),
                    _EntryPoint("akane.test.skill-b", lambda: plugin_b),
                ),
            )
            status = await host.start()
            self.assertEqual(status["status"], "degraded")
            self.assertEqual(status["plugins"][0]["status"], "active")
            self.assertEqual(status["plugins"][1]["reason"], "skill_name_conflict")
            await host.stop()

            denied = _SkillPlugin("akane.test.skill-denied", skill_a, permission=False)
            denied_host = PluginHost(
                (PluginSelection("akane.test.skill-denied", True),),
                contribution_policy=TrustedStatefulPluginContributionPolicy(),
                entry_points_provider=lambda: (
                    _EntryPoint("akane.test.skill-denied", lambda: denied),
                ),
            )
            denied_status = await denied_host.start()
            self.assertEqual(denied_status["plugins"][0]["status"], "failed")
            self.assertEqual(denied_status["plugins"][0]["reason"], "contribution_policy_rejected")
            await denied_host.stop()


if __name__ == "__main__":
    unittest.main()
