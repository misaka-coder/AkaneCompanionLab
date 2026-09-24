from __future__ import annotations

import asyncio
import functools
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from companion_v01.plugin_installation import (
    ManagedPluginArtifactStore,
    PluginInstallationError,
)
from companion_v01.plugin_generation_candidate import PluginGenerationCandidateBuilder
from companion_v01.instance_profile import PluginSelection


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = PROJECT_ROOT / "examples" / "plugins" / "akane_gentle_checkin"
PLUGIN_ID = "akane.sample.gentle-checkin"


class ManagedPluginArtifactStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._build_temp = tempfile.TemporaryDirectory()
        cls.build_root = Path(cls._build_temp.name)
        cls.wheel_v1 = cls._build_wheel(cls._sample_version())
        cls.wheel_v2 = cls._build_wheel("0.3.0")
        cls.dependency_wheel, cls.dependency_plugin_wheel = cls._build_dependency_fixture()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._build_temp.cleanup()

    @classmethod
    def _sample_version(cls) -> str:
        """Read the sample's real version so a bump does not break fixtures."""

        return tomllib.loads((SAMPLE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]

    @classmethod
    def _build_wheel(cls, version: str) -> Path:
        source = cls.build_root / f"source-{version}"
        wheelhouse = cls.build_root / f"wheelhouse-{version}"
        shutil.copytree(SAMPLE_ROOT, source)
        if version != cls._sample_version():
            pyproject = source / "pyproject.toml"
            pyproject.write_text(
                pyproject.read_text(encoding="utf-8").replace(
                    f'version = "{cls._sample_version()}"',
                    f'version = "{version}"',
                ),
                encoding="utf-8",
            )
            plugin_source = source / "src" / "akane_gentle_checkin" / "__init__.py"
            plugin_source.write_text(
                plugin_source.read_text(encoding="utf-8").replace(
                    f'PLUGIN_VERSION = "{cls._sample_version()}"',
                    f'PLUGIN_VERSION = "{version}"',
                ),
                encoding="utf-8",
            )
        wheelhouse.mkdir()
        subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--outdir",
                str(wheelhouse),
                str(source),
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        wheels = tuple(wheelhouse.glob("akane_gentle_checkin-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("test_plugin_wheel_not_built")
        return wheels[0]

    @classmethod
    def _build_dependency_fixture(cls) -> tuple[Path, Path]:
        dependency_source = cls.build_root / "dependency-source"
        dependency_wheelhouse = cls.build_root / "dependency-wheelhouse"
        dependency_source.mkdir()
        (dependency_source / "src" / "dep_fixture").mkdir(parents=True)
        (dependency_source / "src" / "dep_fixture" / "__init__.py").write_text(
            'VALUE = "installed-from-wheelhouse"\n', encoding="utf-8"
        )
        (dependency_source / "pyproject.toml").write_text(
            """[build-system]\nrequires = [\"setuptools>=68\"]\nbuild-backend = \"setuptools.build_meta\"\n\n[project]\nname = \"dep-fixture\"\nversion = \"0.1.0\"\nrequires-python = \">=3.11\"\n\n[tool.setuptools.packages.find]\nwhere = [\"src\"]\n""",
            encoding="utf-8",
        )
        dependency_wheelhouse.mkdir()
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(dependency_wheelhouse), str(dependency_source)],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        dependency_wheels = tuple(dependency_wheelhouse.glob("dep_fixture-*.whl"))
        if len(dependency_wheels) != 1:
            raise RuntimeError("test_dependency_wheel_not_built")

        plugin_source = cls.build_root / "dependency-plugin-source"
        shutil.copytree(SAMPLE_ROOT, plugin_source)
        pyproject = plugin_source / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        if "dependencies = [" in text:
            # Extend the sample's own dependency list instead of declaring a
            # second ``dependencies`` key, which is invalid TOML.
            text = text.replace('dependencies = [', 'dependencies = ["dep-fixture==0.1.0", ', 1)
        else:
            text = text.replace(
                'requires-python = ">=3.11"',
                'requires-python = ">=3.11"\ndependencies = ["dep-fixture==0.1.0"]',
            )
        pyproject.write_text(text, encoding="utf-8")
        plugin_wheel_dir = cls.build_root / "dependency-plugin-wheelhouse"
        plugin_wheel_dir.mkdir()
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(plugin_wheel_dir), str(plugin_source)],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        plugin_wheels = tuple(plugin_wheel_dir.glob("akane_gentle_checkin-*.whl"))
        if len(plugin_wheels) != 1:
            raise RuntimeError("test_dependency_plugin_wheel_not_built")
        return dependency_wheels[0], plugin_wheels[0]

    def setUp(self) -> None:
        self._runtime_temp = tempfile.TemporaryDirectory()
        self.root = Path(self._runtime_temp.name)
        self.store = ManagedPluginArtifactStore(
            self.root / "artifacts",
            instance_id="test-instance",
            project_root=PROJECT_ROOT,
        )

    def tearDown(self) -> None:
        self._runtime_temp.cleanup()

    def test_stage_probes_real_wheel_without_publishing_or_leaking_paths(self) -> None:
        staged = self.store.stage_wheel(self.wheel_v1)

        self.assertEqual(staged["status"], "staged")
        self.assertEqual(staged["plugin_id"], PLUGIN_ID)
        self.assertEqual(staged["version"], self._sample_version())
        self.assertIn("agent.turn.request", staged["permissions"])
        self.assertNotIn("notification.send", staged["permissions"])
        self.assertIn("background_services", staged["contribution_snapshot"]["types"])
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["plugin_count"], 0)
        self.assertEqual(snapshot["staged_count"], 1)
        self.assertNotIn(str(self.root), json.dumps(snapshot))

    def test_real_probe_keeps_schema_failure_details_for_the_author(self):
        from tests.test_plugin_generation import _write_capability_plugin_site

        site = _write_capability_plugin_site(self.root / "schema-fixture", typed_schema=True, invalid_schema=True)
        with self.assertRaises(PluginInstallationError) as caught:
            self.store._probe(site, "test.generation", self.root / "schema-probe")
        self.assertEqual(caught.exception.reason, "invalid_schema")
        self.assertEqual(caught.exception.schema_errors[0]["field"], "$defs.cell.properties.count.minimum")
        self.assertNotIn(str(self.root), json.dumps(caught.exception.schema_errors))
        self.assertEqual(self.store.snapshot()["plugin_count"], 0)

    def test_local_source_is_built_then_enters_the_same_wheel_probe(self) -> None:
        source = self.root / "source"
        shutil.copytree(SAMPLE_ROOT, source)

        staged = self.store.stage_source(source)

        self.assertEqual(staged["status"], "staged")
        self.assertEqual(staged["plugin_id"], PLUGIN_ID)
        self.assertEqual(staged["version"], self._sample_version())
        build_root = self.root / "artifacts" / "source-builds"
        self.assertFalse(build_root.exists() and tuple(build_root.iterdir()))

    def test_source_tests_run_against_real_current_release_sdk(self) -> None:
        source = self.root / "source"
        shutil.copytree(SAMPLE_ROOT, source)
        tests_dir = source / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_sdk_contract.py").write_text(
            """import unittest

from capcore import CapabilityResult
from companion_v01.plugin_api import PluginManifest
from akane_gentle_checkin import create_plugin


class CurrentSdkContractTests(unittest.TestCase):
    def test_plugin_uses_real_sdk_types(self):
        plugin = create_plugin()
        self.assertIsInstance(plugin.manifest, PluginManifest)
        self.assertTrue(hasattr(CapabilityResult, \"__dataclass_fields__\"))


if __name__ == \"__main__\":
    unittest.main()
""",
            encoding="utf-8",
        )

        tested = self.store.test_source(source)

        self.assertTrue(tested["ok"])
        self.assertEqual(tested["status"], "passed")
        self.assertEqual(tested["test_framework"], "unittest")
        self.assertEqual(tested["exit_code"], 0)
        self.assertIn("Ran 1 test", tested["output"])
        self.assertFalse(tested["output_truncated"])
        self.assertEqual(self.store.snapshot()["staged_count"], 0)

    def test_source_test_failure_returns_actionable_real_output(self) -> None:
        source = self.root / "source"
        shutil.copytree(SAMPLE_ROOT, source)
        tests_dir = source / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_failure.py").write_text(
            """import unittest


class FailureTests(unittest.TestCase):
    def test_failure(self):
        self.assertEqual(1, 2, \"contract mismatch\")
""",
            encoding="utf-8",
        )

        tested = self.store.test_source(source)

        self.assertFalse(tested["ok"])
        self.assertEqual(tested["status"], "failed")
        self.assertEqual(tested["reason"], "plugin_source_tests_failed")
        self.assertNotEqual(tested["exit_code"], 0)
        self.assertIn("contract mismatch", tested["output"])

    def test_source_tests_do_not_inherit_host_credentials(self) -> None:
        source = self.root / "source"
        shutil.copytree(SAMPLE_ROOT, source)
        tests_dir = source / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_environment.py").write_text(
            """import os
import unittest


class EnvironmentTests(unittest.TestCase):
    def test_host_credentials_are_absent(self):
        self.assertNotIn("AKANE_TEST_SECRET", os.environ)
        self.assertNotIn("EXAMPLE_API_TOKEN", os.environ)
""",
            encoding="utf-8",
        )

        with mock.patch.dict(
            os.environ,
            {
                "AKANE_TEST_SECRET": "must-not-leak",
                "EXAMPLE_API_TOKEN": "must-not-leak",
            },
        ):
            tested = self.store.test_source(source)

        self.assertTrue(tested["ok"])
        self.assertEqual(tested["status"], "passed")

    def test_source_tests_require_a_real_test_directory(self) -> None:
        source = self.root / "source"
        shutil.copytree(SAMPLE_ROOT, source)

        with self.assertRaises(PluginInstallationError) as raised:
            self.store.test_source(source)

        self.assertEqual(raised.exception.status, "invalid_request")
        self.assertEqual(raised.exception.reason, "plugin_source_tests_required")

    def test_publish_requires_exact_permissions_and_selects_atomically(self) -> None:
        staged = self.store.stage_wheel(self.wheel_v1)

        rejected = self.store.publish_stage(
            staged["stage_id"],
            approved_permissions=(),
        )
        self.assertEqual(rejected["status"], "approval_required")
        self.assertEqual(self.store.snapshot()["plugin_count"], 0)

        before_sys_path = tuple(sys.path)
        published = self.store.publish_stage(
            staged["stage_id"],
            approved_permissions=staged["permissions"],
        )
        self.assertEqual(published["status"], "installed")
        self.assertTrue(published["activation_pending"])
        self.assertEqual(tuple(sys.path), before_sys_path)
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["plugin_count"], 1)
        self.assertTrue(snapshot["plugins"][0]["pending_activation"])
        self.assertEqual(
            snapshot["plugins"][0]["contribution_snapshot"],
            staged["contribution_snapshot"],
        )
        self.assertFalse(tuple((self.root / "artifacts" / "staging").iterdir()))

        source = self.store.resolve_generation_source(PLUGIN_ID)
        self.assertEqual(source.plugin_id, PLUGIN_ID)
        self.assertEqual(source.digest, published["digest"])
        self.assertEqual(source.approved_permissions, tuple(sorted(staged["permissions"])))
        self.assertTrue(source.site_dir.is_dir())
        self.assertNotIn(str(source.site_dir), json.dumps(self.store.snapshot()))

        republished = self.store.publish_stage(
            self.store.stage_wheel(self.wheel_v1)["stage_id"],
            approved_permissions=staged["permissions"],
        )
        self.assertTrue(republished["unchanged"])
        self.assertTrue(republished["activation_pending"])

    def test_successful_new_process_marks_current_release_last_good(self) -> None:
        staged = self.store.stage_wheel(self.wheel_v1)
        published = self.store.publish_stage(
            staged["stage_id"],
            approved_permissions=staged["permissions"],
        )

        result = self.store.reconcile_runtime(
            ({"plugin_id": PLUGIN_ID, "status": "active"},)
        )

        self.assertEqual(result["status"], "ready")
        item = self.store.snapshot()["plugins"][0]
        self.assertFalse(item["pending_activation"])
        self.assertEqual(item["last_good_digest"], published["digest"])

    def test_selected_release_builds_a_real_isolated_candidate(self) -> None:
        staged = self.store.stage_wheel(self.wheel_v1)
        self.store.publish_stage(
            staged["stage_id"],
            approved_permissions=staged["permissions"],
        )
        builder = PluginGenerationCandidateBuilder(
            source_resolver=self.store,
            project_root=PROJECT_ROOT,
            work_root=self.root / "generation-work",
            plugin_storage_data_root=self.root / "instance-data",
            plugin_storage_instance_id="test-instance",
        )

        async def exercise() -> None:
            snapshot = await builder.build((PluginSelection(PLUGIN_ID, True),))
            try:
                self.assertTrue(snapshot.ready)
                self.assertEqual(tuple(item.plugin_id for item in snapshot.processes), (PLUGIN_ID,))
                self.assertTrue(
                    (
                        self.root
                        / "instance-data"
                        / "instances"
                        / "test-instance"
                        / "plugins"
                        / PLUGIN_ID
                    ).is_dir()
                )
            finally:
                await asyncio.gather(
                    *(asyncio.to_thread(process.stop) for process in snapshot.processes)
                )

        asyncio.run(exercise())

    def test_failed_update_points_catalog_back_to_last_good(self) -> None:
        first = self.store.stage_wheel(self.wheel_v1)
        published_v1 = self.store.publish_stage(
            first["stage_id"],
            approved_permissions=first["permissions"],
        )
        self.store.reconcile_runtime(({"plugin_id": PLUGIN_ID, "status": "active"},))

        second = self.store.stage_wheel(self.wheel_v2)
        published_v2 = self.store.publish_stage(
            second["stage_id"],
            approved_permissions=second["permissions"],
        )
        self.assertNotEqual(published_v1["digest"], published_v2["digest"])

        rollback = self.store.reconcile_runtime(
            ({"plugin_id": PLUGIN_ID, "status": "unavailable", "reason": "plugin_load_failed"},)
        )

        self.assertEqual(rollback["status"], "rollback_scheduled")
        self.assertTrue(rollback["reload_required"])
        item = self.store.snapshot()["plugins"][0]
        self.assertEqual(item["digest"], published_v1["digest"])
        self.assertTrue(item["pending_activation"])

    def test_failed_first_activation_remains_visible_without_fake_rollback(self) -> None:
        staged = self.store.stage_wheel(self.wheel_v1)
        self.store.publish_stage(
            staged["stage_id"],
            approved_permissions=staged["permissions"],
        )

        result = self.store.reconcile_runtime(
            ({"plugin_id": PLUGIN_ID, "status": "unavailable"},)
        )

        self.assertEqual(result["status"], "activation_failed")
        self.assertEqual(result["failed_plugin_ids"], [PLUGIN_ID])
        self.assertFalse(result["reload_required"])
        self.assertTrue(self.store.snapshot()["plugins"][0]["pending_activation"])

    def test_unmentioned_pending_plugin_is_not_reported_as_failed(self) -> None:
        staged = self.store.stage_wheel(self.wheel_v1)
        self.store.publish_stage(
            staged["stage_id"],
            approved_permissions=staged["permissions"],
        )

        result = self.store.reconcile_runtime(())

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["failed_plugin_ids"], [])
        self.assertTrue(self.store.snapshot()["plugins"][0]["pending_activation"])

    def test_remove_withdraws_catalog_and_managed_release(self) -> None:
        staged = self.store.stage_wheel(self.wheel_v1)
        self.store.publish_stage(
            staged["stage_id"],
            approved_permissions=staged["permissions"],
        )

        removed = self.store.remove_plugin(PLUGIN_ID)

        self.assertTrue(removed["ok"])
        self.assertEqual(removed["status"], "removed")
        self.assertEqual(self.store.snapshot()["plugin_count"], 0)

    def test_v1_catalog_is_atomically_migrated_to_activation_state(self) -> None:
        artifact_root = self.root / "artifacts"
        artifact_root.mkdir(parents=True)
        catalog_path = artifact_root / "plugin-artifacts.json"
        catalog_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "instance_id": "test-instance",
                    "plugins": {
                        PLUGIN_ID: {
                            "current": "a" * 64,
                            "last_good": "",
                            "pending_process_restart": True,
                        }
                    },
                    "artifacts": {},
                }
            ),
            encoding="utf-8",
        )

        snapshot = self.store.snapshot()

        self.assertTrue(snapshot["plugins"][0]["pending_activation"])
        migrated = json.loads(catalog_path.read_text(encoding="utf-8"))
        self.assertEqual(migrated["schema_version"], 2)
        self.assertTrue(migrated["plugins"][PLUGIN_ID]["pending_activation"])
        self.assertNotIn("pending_process_restart", migrated["plugins"][PLUGIN_ID])

    def test_invalid_wheel_leaves_no_staged_candidate(self) -> None:
        invalid = self.root / "broken.whl"
        invalid.write_bytes(b"not-a-wheel")

        with self.assertRaises(PluginInstallationError) as raised:
            self.store.stage_wheel(invalid)

        self.assertEqual(raised.exception.reason, "plugin_wheel_install_failed")
        staging = self.root / "artifacts" / "staging"
        self.assertFalse(staging.exists() and tuple(staging.iterdir()))

    def test_external_dependency_requires_an_explicit_supply_source(self) -> None:
        store = ManagedPluginArtifactStore(
            self.root / "unconfigured-artifacts",
            instance_id="test-instance",
            project_root=PROJECT_ROOT,
        )

        with self.assertRaises(PluginInstallationError) as raised:
            store.stage_wheel(self.dependency_plugin_wheel)

        self.assertEqual(raised.exception.reason, "plugin_dependencies_unconfigured")
        self.assertEqual(raised.exception.status, "dependency_error")
        self.assertEqual(raised.exception.diagnostics[0]["package"], "dep-fixture")
        staging = self.root / "unconfigured-artifacts" / "staging"
        self.assertFalse(staging.exists() and tuple(staging.iterdir()))

    def test_configured_package_index_installs_external_dependency_in_isolated_stage(self) -> None:
        index_root = self.root / "simple-index"
        wheel_dir = index_root / "wheels"
        package_dir = index_root / "simple" / "dep-fixture"
        wheel_dir.mkdir(parents=True)
        package_dir.mkdir(parents=True)
        shutil.copy2(self.dependency_wheel, wheel_dir / self.dependency_wheel.name)
        (package_dir / "index.html").write_text(
            f'<a href="/wheels/{self.dependency_wheel.name}">{self.dependency_wheel.name}</a>\n',
            encoding="utf-8",
        )
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(index_root))
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            store = ManagedPluginArtifactStore(
                self.root / "index-artifacts",
                instance_id="test-instance",
                project_root=PROJECT_ROOT,
                dependency_index_url=f"http://127.0.0.1:{server.server_address[1]}/simple",
            )
            staged = store.stage_wheel(self.dependency_plugin_wheel)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(staged["status"], "staged")
        dependency_site = self.root / "index-artifacts" / "staging" / staged["stage_id"] / "site" / "dep_fixture"
        self.assertTrue((dependency_site / "__init__.py").is_file())
        self.assertEqual(store.snapshot()["dependency_supply"]["kind"], "package_index")

    def test_configured_wheelhouse_installs_external_dependency_in_isolated_stage(self) -> None:
        store = ManagedPluginArtifactStore(
            self.root / "wheelhouse-artifacts",
            instance_id="test-instance",
            project_root=PROJECT_ROOT,
            dependency_wheelhouse=self.dependency_wheel.parent,
        )

        staged = store.stage_wheel(self.dependency_plugin_wheel)

        self.assertEqual(staged["status"], "staged")
        dependency_site = self.root / "wheelhouse-artifacts" / "staging" / staged["stage_id"] / "site" / "dep_fixture"
        self.assertTrue((dependency_site / "__init__.py").is_file())
        self.assertEqual(store.snapshot()["dependency_supply"]["kind"], "wheelhouse")


if __name__ == "__main__":
    unittest.main()
