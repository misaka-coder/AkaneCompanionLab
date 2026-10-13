from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from tests.test_execution_resources import OWNER, RESOURCE_SCOPE, _Harness


class ExecutionResourceBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.harness = _Harness(Path(temporary.name))
        self.bridge = self.harness.bridge

    @staticmethod
    def run_id():
        return "execrun_" + uuid.uuid4().hex

    def stage(self, run_id, inputs=(), globs=()):
        return self.bridge.stage_inputs(
            run_id=run_id, owner=OWNER, resource_scope=RESOURCE_SCOPE,
            input_resources=inputs, output_globs=globs,
        )

    def bind(self, run_id, cwd, globs=("out.txt",)):
        return self.bridge.bind_workspace_outputs(
            run_id=run_id, owner=OWNER, resource_scope=RESOURCE_SCOPE,
            cwd=cwd, output_globs=globs,
        )

    @contextmanager
    def paused_worker(self, method_name):
        entered = threading.Event()
        release = threading.Event()
        caller_thread = threading.get_ident()
        original = getattr(self.bridge, method_name)

        def paused(*args, **kwargs):
            if threading.get_ident() != caller_thread:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("resource_boundary_worker_not_released")
            return original(*args, **kwargs)

        with patch.object(self.bridge, method_name, side_effect=paused):
            with ThreadPoolExecutor(max_workers=1) as executor:
                try:
                    yield executor, entered
                finally:
                    release.set()

    def test_rejected_concurrent_stage_keeps_the_first_staged_file(self):
        run_id = self.run_id()
        item = self.harness.register_input(content="first request bytes")
        inputs = [{"handle": item["attachment_handle"], "as": "input.txt"}]
        with self.paused_worker("_prune_bindings") as (executor, entered):
            first = executor.submit(self.stage, run_id, inputs)
            self.assertTrue(entered.wait(3))
            staged_file = self.harness.workspace / ".akane_exec_runs" / run_id / "input.txt"
            self.assertEqual(staged_file.read_text(encoding="utf-8"), "first request bytes")
            rejected = self.stage(run_id, [{"handle": item["attachment_handle"], "as": "../bad.txt"}])
        self.assertTrue(first.result()["ok"])
        self.assertEqual(rejected, {"ok": False, "reason": "duplicate_execution_run_id"})
        self.assertEqual(staged_file.read_text(encoding="utf-8"), "first request bytes")

    def test_concurrent_valid_stages_admit_only_one_request(self):
        run_id = self.run_id()
        item = self.harness.register_input()
        inputs = [{"handle": item["attachment_handle"], "as": "input.txt"}]
        with self.paused_worker("_resolve_input_handle") as (executor, entered):
            first = executor.submit(self.stage, run_id, inputs, ("first.txt",))
            self.assertTrue(entered.wait(3))
            rejected = self.stage(run_id, globs=("second.txt",))
        self.assertTrue(first.result()["ok"])
        self.assertEqual(rejected["reason"], "duplicate_execution_run_id")
        self.assertEqual(self.bridge.output_globs_for(run_id), ("first.txt",))

    def test_stage_and_persistent_bind_share_run_admission(self):
        run_id = self.run_id()
        item = self.harness.register_input()
        project = self.harness.workspace / "project"
        project.mkdir()
        original_file = project / "out.txt"
        original_file.write_text("existing project bytes", encoding="utf-8")
        with self.paused_worker("_resolve_input_handle") as (executor, entered):
            first = executor.submit(self.stage, run_id, [{"handle": item["attachment_handle"], "as": "input.txt"}])
            self.assertTrue(entered.wait(3))
            rejected = self.bind(run_id, "project")
        self.assertTrue(first.result()["ok"])
        self.assertEqual(rejected["reason"], "duplicate_execution_run_id")
        self.assertEqual(original_file.read_text(encoding="utf-8"), "existing project bytes")

    def test_slow_staging_does_not_block_another_run(self):
        item = self.harness.register_input()
        with self.paused_worker("_resolve_input_handle") as (executor, entered):
            first = executor.submit(self.stage, self.run_id(), [{"handle": item["attachment_handle"], "as": "input.txt"}])
            self.assertTrue(entered.wait(3))
            other = self.stage(self.run_id())
        self.assertTrue(other["ok"])
        self.assertTrue(first.result()["ok"])

    def test_concurrent_project_snapshots_admit_only_one_request(self):
        run_id = self.run_id()
        for name in ("first-project", "second-project"):
            (self.harness.workspace / name).mkdir()
        with self.paused_worker("_collect_output_matches") as (executor, entered):
            first = executor.submit(self.bind, run_id, "first-project")
            self.assertTrue(entered.wait(3))
            rejected = self.bind(run_id, "second-project")
        self.assertTrue(first.result()["ok"])
        self.assertEqual(rejected["reason"], "duplicate_execution_run_id")
        self.assertEqual(self.bridge.run_cwd_relpath(run_id), "first-project")

    def test_failed_staging_releases_admission_for_retry(self):
        run_id = self.run_id()
        invalid = self.stage(run_id, [{"handle": "file_1", "as": "../bad.txt"}])
        self.assertFalse(invalid["ok"])
        self.assertTrue(self.stage(run_id)["ok"])

    def test_equivalent_input_targets_are_rejected_before_overwrite(self):
        first = self.harness.register_input(name="first.txt", content="first")
        second = self.harness.register_input(name="second.txt", content="second")
        pairs = [("out.txt", "./out.txt"), ("sub/file.txt", "sub/./file.txt")]
        if os.name == "nt":
            pairs.append(("input.txt", "INPUT.txt"))
        for left, right in pairs:
            with self.subTest(targets=(left, right)):
                run_id = self.run_id()
                result = self.stage(run_id, [
                    {"handle": first["attachment_handle"], "as": left},
                    {"handle": second["attachment_handle"], "as": right},
                ])
                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], "duplicate_input_resource_target")
                self.assertFalse((self.harness.workspace / ".akane_exec_runs" / run_id).exists())

    def test_input_target_must_name_a_file(self):
        item = self.harness.register_input()
        result = self.stage(self.run_id(), [{"handle": item["attachment_handle"], "as": "."}])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid_input_resource_path")

    def test_preexisting_run_directory_is_not_adopted_or_removed(self):
        run_id = self.run_id()
        original = self.harness.workspace / ".akane_exec_runs" / run_id / "keep.txt"
        original.parent.mkdir(parents=True)
        original.write_text("not created by this request", encoding="utf-8")
        result = self.stage(run_id)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "execution_workspace_unavailable")
        self.assertEqual(original.read_text(encoding="utf-8"), "not created by this request")
        self.assertFalse(self.bridge.has_binding(run_id=run_id, owner=OWNER))

    def test_cleanup_does_not_remove_a_persistent_directory(self):
        project = self.harness.workspace / "project"
        project.mkdir()
        original = project / "keep.txt"
        original.write_text("keep project", encoding="utf-8")
        self.bridge._cleanup_workspace(project)
        self.assertEqual(original.read_text(encoding="utf-8"), "keep project")

    def test_unconfirmed_old_runs_keep_files_and_can_register_later(self):
        clock = [100.0]
        self.bridge._now = lambda: clock[0]
        for persistent in (False, True):
            with self.subTest(persistent=persistent):
                run_id = self.run_id()
                if persistent:
                    project = self.harness.workspace / "long-project"
                    project.mkdir()
                    start = self.bind(run_id, "long-project")
                else:
                    start = self.stage(run_id, globs=("out.txt",))
                    project = self.harness.workspace / start["cwd_relpath"]
                self.assertTrue(start["ok"])
                output = project / "out.txt"
                output.write_text("long-running output", encoding="utf-8")
                clock[0] += 7200
                self.assertTrue(self.stage(self.run_id())["ok"])
                self.assertTrue(self.bridge.has_binding(run_id=run_id, owner=OWNER))
                self.assertEqual(output.read_text(encoding="utf-8"), "long-running output")
                result = self.bridge.register_outputs(run_id=run_id, owner=OWNER)
                self.assertEqual(result["artifact_status"], "registered")
                self.assertEqual(len(result["generated_resources"]), 1)
                self.assertEqual(project.exists(), persistent)

    def test_finalized_old_bindings_are_still_pruned(self):
        clock = [100.0]
        self.bridge._now = lambda: clock[0]
        run_id = self.run_id()
        self.assertTrue(self.stage(run_id)["ok"])
        self.bridge.finalize_without_outputs(run_id=run_id, owner=OWNER, reason="command_failed")
        clock[0] += 7200
        self.assertTrue(self.stage(self.run_id())["ok"])
        self.assertFalse(self.bridge.has_binding(run_id=run_id, owner=OWNER))

    def test_input_disappearing_during_stat_is_a_rejection_with_cleanup(self):
        run_id = self.run_id()
        source = self.harness.root / "vanishing.txt"
        source.write_text("vanishing input", encoding="utf-8")
        original_is_file = Path.is_file

        def disappears(path):
            result = original_is_file(path)
            if result and path == source:
                source.unlink()
            return result

        with patch.object(self.bridge, "_resolve_input_handle", return_value={"absolute_path": str(source)}):
            with patch.object(Path, "is_file", disappears):
                result = self.stage(run_id, [{"handle": "file_1", "as": "input.txt"}])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "input_staging_failed:file_1")
        self.assertFalse((self.harness.workspace / ".akane_exec_runs" / run_id).exists())
        self.assertTrue(self.stage(run_id)["ok"])

    def test_output_disappearing_during_collection_is_an_honest_failure(self):
        run_id = self.run_id()
        start = self.stage(run_id, globs=("out.txt",))
        run_dir = self.harness.workspace / start["cwd_relpath"]
        source = run_dir / "out.txt"
        source.write_text("vanishing output", encoding="utf-8")
        original_is_file = Path.is_file

        def disappears(path):
            result = original_is_file(path)
            if result and path == source:
                source.unlink()
            return result

        with patch.object(Path, "is_file", disappears):
            result = self.bridge.register_outputs(run_id=run_id, owner=OWNER)
        self.assertEqual(result["artifact_status"], "registration_failed")
        self.assertEqual(result["generated_resources"], [])
        self.assertFalse(run_dir.exists())

    def test_snapshot_failure_preserves_project_and_releases_admission(self):
        run_id = self.run_id()
        project = self.harness.workspace / "project"
        project.mkdir()
        (project / "out.txt").write_text("existing output", encoding="utf-8")
        with patch.object(self.bridge, "_output_fingerprint", side_effect=FileNotFoundError):
            result = self.bind(run_id, "project")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "execution_workspace_unavailable")
        self.assertEqual((project / "out.txt").read_text(encoding="utf-8"), "existing output")
        self.assertTrue(self.bind(run_id, "project")["ok"])

    def test_removed_authorized_project_is_a_structured_error(self):
        removed_project = self.harness.root / "removed-project"
        result = self.bridge.bind_workspace_outputs(
            run_id=self.run_id(), owner=OWNER, resource_scope=RESOURCE_SCOPE,
            cwd=str(removed_project), output_globs=("out.txt",), authorized_root=removed_project,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "execution_workspace_unavailable")

    def test_output_disappearing_during_baseline_comparison_preserves_project(self):
        run_id = self.run_id()
        project = self.harness.workspace / "project"
        project.mkdir()
        source = project / "out.txt"
        source.write_text("original output", encoding="utf-8")
        self.assertTrue(self.bind(run_id, "project")["ok"])
        source.write_text("changed output", encoding="utf-8")
        original_fingerprint = self.bridge._output_fingerprint

        def disappears(path):
            source.unlink()
            return original_fingerprint(path)

        with patch.object(self.bridge, "_output_fingerprint", side_effect=disappears):
            result = self.bridge.register_outputs(run_id=run_id, owner=OWNER)
        self.assertEqual(result["artifact_status"], "registration_failed")
        self.assertEqual(result["generated_resources"], [])
        self.assertTrue(project.is_dir())
        self.assertEqual(result, self.bridge.register_outputs(run_id=run_id, owner=OWNER))

    def test_linked_run_parent_cannot_stage_or_cleanup_outside_workspace(self):
        outside = self.harness.root / "outside"
        outside.mkdir()
        keep = outside / "keep.txt"
        keep.write_text("outside bytes", encoding="utf-8")
        linked_parent = self.harness.workspace / ".akane_exec_runs"
        if os.name == "nt":
            powershell = shutil.which("pwsh")
            if not powershell:
                self.skipTest("PowerShell is unavailable for creating a directory junction")
            script = self.harness.root / "junction.ps1"
            script.write_text(
                'param([string]$Link, [string]$Target)\n$ErrorActionPreference = "Stop"\n'
                'New-Item -ItemType Junction -Path $Link -Target $Target | Out-Null\n',
                encoding="utf-8",
            )
            created = subprocess.run(
                [powershell, "-NoProfile", "-File", str(script), "-Link", str(linked_parent), "-Target", str(outside)],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if created.returncode:
                self.skipTest("Directory junction creation is unavailable")
        else:
            try:
                linked_parent.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("Directory symlink creation is unavailable")
        try:
            run_id = self.run_id()
            result = self.stage(run_id)
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "execution_workspace_unavailable")
            self.assertFalse((outside / run_id).exists())
            original_run = outside / self.run_id()
            original_run.mkdir()
            (original_run / "input.txt").write_text("keep external input", encoding="utf-8")
            self.bridge._cleanup_workspace(linked_parent / original_run.name)
            self.assertEqual((original_run / "input.txt").read_text(encoding="utf-8"), "keep external input")
            self.assertEqual(keep.read_text(encoding="utf-8"), "outside bytes")
        finally:
            if os.name == "nt":
                os.rmdir(linked_parent)
            else:
                linked_parent.unlink()


if __name__ == "__main__":
    unittest.main()
