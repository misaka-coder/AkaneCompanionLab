from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from capcore import InvocationContext

from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_contribution_policy import M65CDiagnosticContributionPolicy
from companion_v01.plugin_host import PluginHost


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "akane_diagnostic_plugin"
PLUGIN_ID = "akane.test.diagnostic"
CAPABILITY_ID = f"{PLUGIN_ID}.ping.v1"


class InstalledPluginArtifactSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_built_wheel_is_discovered_audited_and_invoked_from_installed_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            build_source = temp_root / "source"
            wheelhouse = temp_root / "wheelhouse"
            install_root = temp_root / "installed"
            shutil.copytree(FIXTURE_ROOT, build_source)
            wheelhouse.mkdir()
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "build",
                    "--wheel",
                    "--outdir",
                    str(wheelhouse),
                    str(build_source),
                ],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            wheels = tuple(wheelhouse.glob("akane_diagnostic_plugin-*.whl"))
            self.assertEqual(len(wheels), 1)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-deps",
                    "--target",
                    str(install_root),
                    str(wheels[0]),
                ],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            sys.path.insert(0, str(install_root))
            importlib.invalidate_caches()
            try:
                host = PluginHost(
                    (PluginSelection(PLUGIN_ID, True),),
                    contribution_policy=M65CDiagnosticContributionPolicy(),
                )
                status = await host.start()
                result = await host.invoke(
                    CAPABILITY_ID,
                    {},
                    context=InvocationContext(client_mode="artifact_smoke"),
                )
                await host.stop()
            finally:
                sys.path.remove(str(install_root))
                sys.modules.pop("akane_diagnostic_plugin", None)
                importlib.invalidate_caches()

        self.assertEqual(status["status"], "active")
        self.assertEqual(status["plugin_count"], 1)
        self.assertEqual(status["capability_count"], 1)
        self.assertFalse(result.is_error)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.content["diagnostic"], "ready")


if __name__ == "__main__":
    unittest.main()
