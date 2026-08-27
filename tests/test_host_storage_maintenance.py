from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "deploy"
    / "maintenance"
    / "akane-host-storage-maintenance.py"
)
SPEC = importlib.util.spec_from_file_location("akane_host_storage_maintenance", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class HostStorageMaintenanceTests(unittest.TestCase):
    def _directory(self, root: Path, name: str, *, age: int) -> Path:
        path = root / name
        path.mkdir(parents=True)
        (path / "payload.bin").write_bytes(b"x" * 32)
        epoch = time.time() - age
        os.utime(path, (epoch, epoch))
        return path

    def test_release_retention_never_selects_active_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            active = self._directory(root, "active", age=500)
            old = self._directory(root, "old", age=400)
            newest = self._directory(root, "newest", age=10)

            targets = MODULE.collect_release_targets(root, active_release=active, keep=1)

            self.assertEqual([target.path for target in targets], [old])
            self.assertTrue(active.exists())
            self.assertTrue(newest.exists())

    def test_data_backup_retention_ignores_non_predeploy_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ignored = self._directory(root, "manual-snapshot", age=500)
            old = self._directory(root, "one-predeploy", age=400)
            newest = self._directory(root, "two-predeploy", age=10)

            targets = MODULE.collect_backup_targets(
                root,
                category="data",
                keep=1,
                required_fragment="-predeploy",
            )

            self.assertEqual([target.path for target in targets], [old])
            self.assertTrue(ignored.exists())
            self.assertTrue(newest.exists())

    def test_expired_prompt_audit_is_removed_but_recent_file_remains(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "old.jsonl"
            recent = root / "recent.jsonl"
            old.write_text("old", encoding="utf-8")
            recent.write_text("recent", encoding="utf-8")
            os.utime(old, (1, 1))

            targets = MODULE.collect_expired_targets(
                [root],
                category="audit",
                older_than_epoch=time.time() - 60,
                direct_children_only=False,
            )
            removed, reclaimed = MODULE.remove_targets(targets, dry_run=False)

            self.assertEqual([item["name"] for item in removed], ["old.jsonl"])
            self.assertGreater(reclaimed, 0)
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())

    def test_dry_run_does_not_remove_selected_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target_path = self._directory(root, "old", age=100)
            target = MODULE.RetentionTarget(target_path, "release", 32)

            removed, reclaimed = MODULE.remove_targets([target], dry_run=True)

            self.assertTrue(target_path.exists())
            self.assertEqual(reclaimed, 0)
            self.assertTrue(removed[0]["dry_run"])

    def test_ephemeral_directories_keep_newest_even_when_all_are_expired(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            oldest = self._directory(root, "2026-08-20", age=500)
            newest = self._directory(root, "2026-08-21", age=400)

            targets = MODULE.collect_expired_targets(
                [root],
                category="workspace_artifact",
                older_than_epoch=time.time() - 60,
                direct_children_only=True,
                keep_newest=1,
            )

            self.assertEqual([target.path for target in targets], [oldest])
            self.assertTrue(newest.exists())

    def test_missing_target_during_removal_does_not_abort_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vanished = root / "vanished.tmp"
            remaining = root / "remaining.tmp"
            remaining.write_bytes(b"payload")
            targets = [
                MODULE.RetentionTarget(vanished, "transport", 5),
                MODULE.RetentionTarget(remaining, "transport", 7),
            ]

            removed, reclaimed = MODULE.remove_targets(targets, dry_run=False)

            self.assertEqual(len(removed), 2)
            self.assertEqual(reclaimed, 12)
            self.assertFalse(remaining.exists())


if __name__ == "__main__":
    unittest.main()
