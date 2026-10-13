"""Publication and cleanup failures must preserve real artifact ownership."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from companion_v01.plugin_installation import (
    ManagedPluginArtifactStore,
    PluginInstallationError,
    StagedPluginArtifact,
    _pip_failure,
    _wheel_runtime_requirements,
)


PLUGIN_ID = "example.artifact"
OTHER_PLUGIN_ID = "example.other-artifact"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PluginArtifactBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = self.make_store(self.root / "artifacts")

    def make_store(self, root):
        return ManagedPluginArtifactStore(root, instance_id="boundary", project_root=PROJECT_ROOT)

    def stage(self, version="1.0.0", *, store=None, plugin_id=PLUGIN_ID):
        store = store or self.store
        stage = StagedPluginArtifact(
            stage_id=uuid.uuid4().hex,
            plugin_id=plugin_id,
            distribution_name="artifact-fixture",
            version=version,
            digest=hashlib.sha256(f"{plugin_id}:{version}".encode()).hexdigest(),
            permissions=("agent.turn.request",),
            contribution_snapshot={"types": ["tools"]},
        )
        directory = store.staging_root / stage.stage_id
        site = directory / "site"
        info = site / "artifact_fixture.dist-info"
        info.mkdir(parents=True)
        (info / "METADATA").write_text(f"Name: artifact-fixture\nVersion: {version}\n", encoding="utf-8")
        (info / "entry_points.txt").write_text(
            f"[akane.plugins.v1]\n{plugin_id} = artifact_fixture:create_plugin\n", encoding="utf-8"
        )
        (site / "payload.py").write_text(f"VERSION = {version!r}\n", encoding="utf-8")
        (directory / "stage.json").write_text(json.dumps(stage.as_dict()), encoding="utf-8")
        return stage

    def publish(self, stage, *, store=None):
        return (store or self.store).publish_stage(stage.stage_id, approved_permissions=stage.permissions)

    def install(self, version="1.0.0", *, store=None, plugin_id=PLUGIN_ID):
        store = store or self.store
        stage = self.stage(version, store=store, plugin_id=plugin_id)
        self.publish(stage, store=store)
        store.reconcile_runtime(({"plugin_id": plugin_id, "status": "active"},))
        return stage, store.resolve_generation_source(plugin_id).site_dir

    def catalog(self, *, store=None):
        return json.loads((store or self.store).catalog_path.read_text(encoding="utf-8"))

    def assert_stage_preserved(self, stage):
        self.assertTrue((self.store.staging_root / stage.stage_id / "site" / "payload.py").is_file())
        self.assertTrue((self.store.staging_root / stage.stage_id / "stage.json").is_file())

    def test_invalid_catalog_does_not_consume_a_reviewed_stage(self):
        first, old_site = self.install()
        before = self.store.catalog_path.read_bytes()
        candidate = self.stage("2.0.0")
        self.store.catalog_path.write_text("invalid-json", encoding="utf-8")
        with self.assertRaises(PluginInstallationError) as caught:
            self.publish(candidate)
        self.assertEqual(caught.exception.reason, "plugin_catalog_invalid")
        self.assert_stage_preserved(candidate)
        self.assertTrue(old_site.is_dir())
        self.store.catalog_path.write_bytes(before)
        self.assertEqual(self.store.resolve_generation_source(PLUGIN_ID).digest, first.digest)
        self.assertTrue(self.publish(candidate)["ok"])

    def test_failed_first_catalog_commit_restores_the_stage_for_retry(self):
        candidate = self.stage()
        with patch.object(self.store, "_write_catalog_locked", side_effect=OSError("controlled write failure")):
            with self.assertRaises(PluginInstallationError) as caught:
                self.publish(candidate)
        self.assertEqual(caught.exception.reason, "plugin_catalog_write_failed")
        self.assert_stage_preserved(candidate)
        self.assertEqual(self.store.snapshot()["plugin_count"], 0)
        self.assertTrue(self.publish(candidate)["ok"])

    def test_failed_update_commit_preserves_current_and_last_good(self):
        first, old_site = self.install()
        before = self.catalog()
        candidate = self.stage("2.0.0")
        with patch.object(self.store, "_write_catalog_locked", side_effect=OSError("controlled write failure")):
            with self.assertRaises(PluginInstallationError):
                self.publish(candidate)
        self.assertEqual(self.catalog(), before)
        self.assertEqual(self.store.resolve_generation_source(PLUGIN_ID).digest, first.digest)
        self.assertTrue((old_site / "payload.py").is_file())
        self.assert_stage_preserved(candidate)
        self.assertTrue(self.publish(candidate)["ok"])

    def test_failed_duplicate_commit_preserves_both_stage_and_existing_release(self):
        first, old_site = self.install()
        candidate = self.stage()
        with patch.object(self.store, "_write_catalog_locked", side_effect=OSError("controlled write failure")):
            with self.assertRaises(PluginInstallationError):
                self.publish(candidate)
        self.assert_stage_preserved(candidate)
        self.assertTrue((old_site / "payload.py").is_file())
        self.assertEqual(self.catalog()["plugins"][PLUGIN_ID]["last_good"], first.digest)
        self.assertTrue(self.publish(candidate)["unchanged"])

    def test_committed_duplicate_reports_stage_cleanup_failure_and_can_discard_later(self):
        self.install()
        candidate = self.stage()
        with patch.object(shutil, "rmtree", side_effect=OSError("controlled cleanup failure")):
            result = self.publish(candidate)
        self.assertTrue(result["ok"])
        self.assertTrue(result["stage_cleanup_pending"])
        self.assertEqual(result["cleanup_reason"], "plugin_stage_cleanup_failed")
        self.assert_stage_preserved(candidate)
        self.assertTrue(self.store.resolve_generation_source(PLUGIN_ID).site_dir.is_dir())
        self.assertEqual(self.store.discard_stage(candidate.stage_id)["status"], "discarded")

    def test_failed_restore_reports_the_real_failure_and_keeps_old_release(self):
        first, old_site = self.install()
        candidate = self.stage("2.0.0")
        stage_dir = self.store.staging_root / candidate.stage_id
        release_dir = self.store.releases_root / PLUGIN_ID / candidate.digest
        replace = os.replace

        def cannot_restore(source, target):
            if Path(source) == release_dir and Path(target) == stage_dir:
                raise OSError("controlled restore failure")
            return replace(source, target)

        with patch.object(self.store, "_write_catalog_locked", side_effect=OSError("controlled commit failure")):
            with patch.object(os, "replace", side_effect=cannot_restore):
                with self.assertRaises(PluginInstallationError) as caught:
                    self.publish(candidate)
        self.assertEqual(caught.exception.reason, "plugin_publication_restore_failed")
        self.assertEqual(self.store.resolve_generation_source(PLUGIN_ID).digest, first.digest)
        self.assertTrue(old_site.is_dir())
        self.assertTrue((release_dir / "stage.json").is_file())
        # A newly staged copy of the same content can reuse the retained release.
        self.assertTrue(self.publish(self.stage("2.0.0"))["ok"])

    def test_duplicate_does_not_select_a_release_with_missing_payload(self):
        first, old_site = self.install()
        shutil.rmtree(old_site)
        candidate = self.stage()
        with self.assertRaises(PluginInstallationError) as caught:
            self.publish(candidate)
        self.assertEqual(caught.exception.reason, "plugin_artifact_unavailable")
        self.assert_stage_preserved(candidate)
        self.assertEqual(self.catalog()["plugins"][PLUGIN_ID]["current"], first.digest)

    def test_failed_uninstall_cleanup_keeps_a_record_and_retries_without_a_pointer(self):
        first, site = self.install()
        with patch.object(shutil, "rmtree", side_effect=OSError("controlled cleanup failure")):
            result = self.store.remove_plugin(PLUGIN_ID)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "removed_cleanup_pending")
        self.assertEqual(result["reason"], "plugin_artifact_cleanup_failed")
        self.assertEqual(self.store.snapshot()["plugin_count"], 0)
        self.assertIn(first.digest, self.catalog()["artifacts"])
        self.assertTrue(site.is_dir())
        self.assertEqual(self.store.remove_plugin(PLUGIN_ID)["status"], "removed")
        self.assertFalse(site.exists())
        self.assertNotIn(first.digest, self.catalog()["artifacts"])

    def test_reconciliation_reports_failed_cleanup_and_can_retry(self):
        first, site = self.install()
        held = self.store.remove_plugin(PLUGIN_ID, retained_site_dirs=(site,))
        self.assertTrue(held["ok"])
        with patch.object(shutil, "rmtree", side_effect=OSError("controlled cleanup failure")):
            result = self.store.reconcile_runtime(())
        self.assertEqual(result["cleanup_status"], "failed")
        self.assertEqual(result["cleanup_failed_plugin_ids"], [PLUGIN_ID])
        self.assertFalse(result["reload_required"])
        self.assertIn(first.digest, self.catalog()["artifacts"])
        self.assertTrue(site.is_dir())
        self.assertEqual(self.store.reconcile_runtime(())["status"], "ready")
        self.assertFalse(site.exists())
        self.assertNotIn(first.digest, self.catalog()["artifacts"])

    def test_one_failed_release_does_not_prevent_other_cleanup(self):
        first, old_site = self.install()
        candidate = self.stage("2.0.0")
        self.publish(candidate)
        self.store.reconcile_runtime(({"plugin_id": PLUGIN_ID, "status": "active"},), retained_site_dirs=(old_site,))
        new_site = self.store.resolve_generation_source(PLUGIN_ID).site_dir
        rmtree = shutil.rmtree

        def fail_old_release(path, *args, **kwargs):
            if Path(path) in (old_site.parent, old_site.parent.parent):
                raise OSError("controlled old-release failure")
            return rmtree(path, *args, **kwargs)

        with patch.object(shutil, "rmtree", side_effect=fail_old_release):
            result = self.store.remove_plugin(PLUGIN_ID)
        self.assertFalse(result["ok"])
        self.assertTrue(old_site.is_dir())
        self.assertFalse(new_site.exists())
        self.assertEqual(set(self.catalog()["artifacts"]), {first.digest})
        self.assertEqual(self.store.reconcile_runtime(())["status"], "ready")
        self.assertFalse(old_site.exists())

    def test_cleanup_catalog_write_failure_keeps_durable_retry_evidence(self):
        first, site = self.install()
        self.store.remove_plugin(PLUGIN_ID, retained_site_dirs=(site,))
        with patch.object(self.store, "_write_catalog_locked", side_effect=OSError("controlled write failure")):
            result = self.store.reconcile_runtime(())
        self.assertEqual(result["cleanup_status"], "failed")
        self.assertEqual(result["cleanup_failed_plugin_ids"], [PLUGIN_ID])
        self.assertIn(first.digest, self.catalog()["artifacts"])
        self.assertEqual(self.store.reconcile_runtime(())["status"], "ready")
        self.assertNotIn(first.digest, self.catalog()["artifacts"])
        self.assertFalse(site.exists())

    def test_uninstall_does_not_delete_untracked_sibling_directories(self):
        _, site = self.install()
        untracked = self.store.releases_root / PLUGIN_ID / ("b" * 64)
        untracked.mkdir()
        (untracked / "preserve.txt").write_text("untracked", encoding="utf-8")
        self.assertEqual(self.store.remove_plugin(PLUGIN_ID)["status"], "removed")
        self.assertFalse(site.exists())
        self.assertTrue((untracked / "preserve.txt").is_file())

    def test_invalid_cleanup_locators_cannot_delete_current_or_other_plugins(self):
        for relative in ("releases", f"releases/{PLUGIN_ID}", "foreign"):
            with self.subTest(relative=relative):
                store = self.make_store(self.root / uuid.uuid4().hex)
                first, site = self.install(store=store)
                other, other_site = self.install(store=store, plugin_id=OTHER_PLUGIN_ID)
                catalog = self.catalog(store=store)
                bad = dict(catalog["artifacts"][first.digest])
                bad["relative_root"] = (
                    f"releases/{OTHER_PLUGIN_ID}/{other.digest}" if relative == "foreign" else relative
                )
                catalog["artifacts"]["e" * 64] = bad
                store.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
                result = store.reconcile_runtime(())
                self.assertTrue((site / "payload.py").is_file())
                self.assertTrue((other_site / "payload.py").is_file())
                self.assertEqual(result["cleanup_status"], "failed")
                self.assertNotIn(str(store.root), json.dumps(result))

    def test_mismatched_catalog_digest_cannot_delete_a_live_release(self):
        first, site = self.install()
        catalog = self.catalog()
        catalog["artifacts"]["e" * 64] = dict(catalog["artifacts"][first.digest])
        self.store.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        result = self.store.reconcile_runtime(())
        self.assertTrue((site / "payload.py").is_file())
        self.assertEqual(result["cleanup_status"], "failed")
        self.assertEqual(self.store.resolve_generation_source(PLUGIN_ID).digest, first.digest)

    def test_selected_catalog_digest_must_match_the_release_directory(self):
        first, _ = self.install()
        catalog = self.catalog()
        catalog["artifacts"]["e" * 64] = dict(catalog["artifacts"][first.digest])
        catalog["plugins"][PLUGIN_ID]["current"] = "e" * 64
        self.store.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        with self.assertRaises(PluginInstallationError) as caught:
            self.store.resolve_generation_source(PLUGIN_ID)
        self.assertEqual(caught.exception.reason, "plugin_catalog_invalid")

    def test_rollback_does_not_point_to_a_missing_last_good_site(self):
        first, old_site = self.install()
        candidate = self.stage("2.0.0")
        self.publish(candidate)
        shutil.rmtree(old_site)
        result = self.store.rollback_to_last_good(PLUGIN_ID)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "plugin_last_good_unavailable")
        pointer = self.catalog()["plugins"][PLUGIN_ID]
        self.assertEqual(pointer["current"], candidate.digest)
        self.assertEqual(pointer["last_good"], first.digest)

    def test_repeated_rollback_keeps_pending_activation_truthful(self):
        self.install()
        self.publish(self.stage("2.0.0"))
        self.store.reconcile_runtime(({"plugin_id": PLUGIN_ID, "status": "unavailable"},))
        result = self.store.rollback_to_last_good(PLUGIN_ID)
        self.assertEqual(result["status"], "unchanged")
        self.assertTrue(result["activation_pending"])
        self.assertTrue(self.catalog()["plugins"][PLUGIN_ID]["pending_activation"])

    def test_failed_activation_does_not_schedule_a_missing_last_good_release(self):
        self.install()
        old_site = self.store.resolve_generation_source(PLUGIN_ID).site_dir
        candidate = self.stage("2.0.0")
        self.publish(candidate)
        shutil.rmtree(old_site)
        result = self.store.reconcile_runtime(({"plugin_id": PLUGIN_ID, "status": "unavailable"},))
        self.assertEqual(result["status"], "activation_failed")
        self.assertFalse(result["reload_required"])
        self.assertEqual(self.catalog()["plugins"][PLUGIN_ID]["current"], candidate.digest)

    def test_stage_alias_cannot_discard_another_reviewed_candidate(self):
        first, second = self.stage(), self.stage("2.0.0")
        first_dir = self.store.staging_root / first.stage_id
        second_dir = self.store.staging_root / second.stage_id
        resolve = Path.resolve

        def redirect_stage(path, *args, **kwargs):
            return second_dir if path == first_dir else resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", redirect_stage):
            with self.assertRaises(PluginInstallationError) as caught:
                self.store.discard_stage(first.stage_id)
        self.assertEqual(caught.exception.reason, "plugin_stage_id_invalid")
        self.assert_stage_preserved(first)
        self.assert_stage_preserved(second)

    def test_release_alias_cannot_delete_another_directory_inside_the_managed_root(self):
        first, site = self.install()
        digest = "b" * 64
        expected = self.store.releases_root / PLUGIN_ID / digest
        other = self.store.releases_root / OTHER_PLUGIN_ID / digest
        other.mkdir(parents=True)
        (other / "preserve.txt").write_text("other-owned", encoding="utf-8")
        catalog = self.catalog()
        artifact = dict(catalog["artifacts"][first.digest])
        artifact["relative_root"] = f"releases/{PLUGIN_ID}/{digest}"
        catalog["artifacts"][digest] = artifact
        self.store.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        resolve = Path.resolve

        def redirect_release(path, *args, **kwargs):
            return other if path == expected else resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", redirect_release):
            result = self.store.reconcile_runtime(())
        self.assertEqual(result["cleanup_status"], "failed")
        self.assertTrue((other / "preserve.txt").is_file())
        self.assertTrue((site / "payload.py").is_file())

    def test_unavailable_build_supply_does_not_leave_a_source_build_directory(self):
        source = self.root / "source"
        source.mkdir()
        (source / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")
        store = ManagedPluginArtifactStore(
            self.root / "build-artifacts",
            instance_id="boundary",
            dependency_wheelhouse=self.root / "missing-wheelhouse",
        )
        with self.assertRaises(PluginInstallationError) as caught:
            store.stage_source(source)
        self.assertEqual(caught.exception.reason, "plugin_dependency_wheelhouse_unavailable")
        builds = store.root / "source-builds"
        self.assertFalse(builds.exists() and tuple(builds.iterdir()))

    def test_dependency_metadata_errors_do_not_expose_raw_url_credentials(self):
        wheel = self.root / "invalid.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr(
                "fixture.dist-info/METADATA",
                "Name: fixture\nRequires-Dist: https://synthetic-user:synthetic-secret@example.invalid/dependency\n",
            )
        with self.assertRaises(PluginInstallationError) as caught:
            _wheel_runtime_requirements(wheel)
        self.assertEqual(caught.exception.reason, "plugin_dependency_metadata_invalid")
        diagnostics = json.dumps(caught.exception.diagnostics)
        self.assertNotIn("synthetic-secret", diagnostics)
        self.assertNotIn("example.invalid", diagnostics)

    def test_pip_missing_requirement_diagnostics_do_not_echo_urls(self):
        failure = _pip_failure(
            "plugin_dependency_install_failed",
            stage="dependency_install",
            output=b"No matching distribution found for https://synthetic-user:synthetic-secret@example.invalid/dependency",
        )
        diagnostics = json.dumps(failure.diagnostics)
        self.assertNotIn("synthetic-secret", diagnostics)
        self.assertNotIn("example.invalid", diagnostics)
        ordinary = _pip_failure(
            "failed", stage="dependency_install", output=b"No matching distribution found for dep-fixture==1.0"
        )
        self.assertTrue(any(item.get("code") == "requirement_unavailable" for item in ordinary.diagnostics))

    def test_host_distribution_aliases_use_the_same_dependency_supply(self):
        wheel = self.root / "alias.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr(
                "fixture.dist-info/METADATA",
                "Name: fixture\nRequires-Dist: Akane.Plugin>=0.6\nRequires-Dist: capcore>=1.0\n",
            )
        requirements = _wheel_runtime_requirements(wheel)
        self.assertTrue(all(item["host_provided"] for item in requirements))

    def test_retained_release_lasts_until_its_real_owner_releases_it(self):
        first, site = self.install()
        result = self.store.remove_plugin(PLUGIN_ID, retained_site_dirs=(site,))
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "removed_cleanup_pending")
        self.store.reconcile_runtime((), retained_site_dirs=(site,))
        self.assertTrue((site / "payload.py").is_file())
        self.assertIn(first.digest, self.catalog()["artifacts"])
        self.store.reconcile_runtime(())
        self.assertFalse(site.exists())

    def test_failed_pointer_withdrawal_never_deletes_the_selected_release(self):
        first, site = self.install()
        with patch.object(self.store, "_write_catalog_locked", side_effect=OSError("controlled write failure")):
            with self.assertRaises(OSError):
                self.store.remove_plugin(PLUGIN_ID)
        self.assertTrue((site / "payload.py").is_file())
        self.assertEqual(self.store.resolve_generation_source(PLUGIN_ID).digest, first.digest)

    def test_permission_rejection_keeps_current_release_and_reviewed_stage(self):
        first, site = self.install()
        candidate = self.stage("2.0.0")
        result = self.store.publish_stage(candidate.stage_id, approved_permissions=())
        self.assertEqual(result["status"], "approval_required")
        self.assertTrue(site.is_dir())
        self.assert_stage_preserved(candidate)
        self.assertEqual(self.store.resolve_generation_source(PLUGIN_ID).digest, first.digest)


class PluginArtifactManagementBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_committed_stage_cleanup_warning_reaches_the_real_management_result(self):
        from companion_v01.extension_management import ExtensionManagementService

        runtime = Mock()
        runtime.selections = ()
        runtime.retained_site_dirs.return_value = ()
        runtime.reconfigure = AsyncMock(
            return_value={
                "status": "active",
                "published": True,
                "plugins": [{"plugin_id": PLUGIN_ID, "status": "active"}],
            }
        )
        selections = Mock()
        selections.load.return_value = ()
        artifacts = Mock()
        artifacts.publish_stage.return_value = {
            "ok": True,
            "status": "installed",
            "plugin_id": PLUGIN_ID,
            "stage_cleanup_pending": True,
            "cleanup_reason": "plugin_stage_cleanup_failed",
        }
        artifacts.reconcile_runtime.return_value = {"status": "ready", "reload_required": False}
        service = ExtensionManagementService(
            plugin_runtime=runtime, selection_store=selections, artifact_store=artifacts
        )
        result = await service.install_stage(stage_id="a" * 32, approved_permissions=())
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "active")
        self.assertTrue(result["stage_cleanup_pending"])
        self.assertEqual(result["cleanup_reason"], "plugin_stage_cleanup_failed")
        runtime.reconfigure.assert_awaited_once()
