from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from companion_v01.plugin_generation import (
    PLUGIN_GENERATION_PROTOCOL,
    PluginGenerationError,
    PluginGenerationProcess,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_plugin_site(root: Path, *, broken: bool = False) -> Path:
    site = root / "site"
    package = site / "generation_fixture"
    dist_info = site / "generation_fixture-0.1.0.dist-info"
    package.mkdir(parents=True)
    dist_info.mkdir(parents=True)
    if broken:
        source = "def create_plugin():\n    raise RuntimeError('broken')\n"
    else:
        source = textwrap.dedent(
            """
            from companion_v01.plugin_api import (
                AKANE_PLUGIN_API_VERSION,
                DIRECT_CONVERSATION_EVENT,
                EVENT_SUBSCRIBE_PERMISSION,
                PluginEventResult,
                PluginManifest,
            )

            class Handler:
                async def handle_event(self, event):
                    return PluginEventResult()

            class Plugin:
                manifest = PluginManifest(
                    plugin_id="test.generation",
                    plugin_version="0.1.0",
                    plugin_api_version=AKANE_PLUGIN_API_VERSION,
                    permissions=(EVENT_SUBSCRIBE_PERMISSION,),
                )

                def register(self, registrar):
                    print("plugin output must not enter the protocol lane")
                    registrar.add_event_handler(DIRECT_CONVERSATION_EVENT, Handler())

            def create_plugin():
                return Plugin()
            """
        )
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: generation-fixture\nVersion: 0.1.0\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[akane.plugins.v1]\ntest.generation = generation_fixture:create_plugin\n",
        encoding="utf-8",
    )
    return site


class PluginGenerationProcessTests(unittest.TestCase):
    def test_generation_starts_reports_health_and_drains(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generation = PluginGenerationProcess(
                project_root=PROJECT_ROOT,
                site_dir=_write_plugin_site(root),
                plugin_id="test.generation",
                work_dir=root / "work",
            )

            ready = generation.start()
            self.assertTrue(ready["ok"])
            self.assertEqual(ready["protocol"], PLUGIN_GENERATION_PROTOCOL)
            self.assertEqual(ready["plugin_id"], "test.generation")
            self.assertGreater(ready["startup_ms"], 0)
            self.assertTrue(generation.running)

            health = generation.health()
            self.assertTrue(health["ok"])
            self.assertEqual(health["status"], "active")
            self.assertEqual(health["snapshot"]["plugin_count"], 1)

            stopped = generation.stop()
            self.assertTrue(stopped["ok"])
            self.assertEqual(stopped["status"], "stopped")
            self.assertFalse(generation.running)
            self.assertEqual(generation.stop()["reason"], "already_stopped")

    def test_failed_candidate_does_not_remain_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generation = PluginGenerationProcess(
                project_root=PROJECT_ROOT,
                site_dir=_write_plugin_site(root, broken=True),
                plugin_id="test.generation",
                work_dir=root / "work",
            )

            with self.assertRaises(PluginGenerationError):
                generation.start()
            self.assertFalse(generation.running)


if __name__ == "__main__":
    unittest.main()
