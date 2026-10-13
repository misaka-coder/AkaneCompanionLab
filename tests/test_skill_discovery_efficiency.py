from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from companion_v01.skill_runtime import ContributedSkillRoot, SkillRegistry


def _markdown(description: str = "First routing.", body: str = "old body", tool: str = "exec_run") -> bytes:
    return (
        f"---\nname: demo\ndescription: {description}\nmetadata:\n"
        f"  required_tools: [{tool}]\n---\n\n{body}\n"
    ).encode("utf-8")


class SkillDiscoveryEfficiencyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.registry = SkillRegistry(
            bundled_root=self.root / "bundled",
            managed_root=self.root / "managed",
            execution_workspace_root=self.root / "workspace",
        )
        self.skill = self.registry.managed_root / "demo"
        self.skill.mkdir()
        self.markdown = self.skill / "SKILL.md"
        self.markdown.write_bytes(_markdown())

    def test_same_size_and_timestamp_edit_updates_routing_and_body(self) -> None:
        old = self.registry.snapshot()
        before_stat = self.markdown.stat()
        updated = _markdown("Other routing.", "new body", "web_open")
        self.assertEqual(len(updated), before_stat.st_size)
        self.markdown.write_bytes(updated)
        os.utime(self.markdown, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))

        new = self.registry.snapshot()
        entry = new.by_name()["demo"]
        self.assertEqual(entry.instructions, "new body")
        self.assertEqual(entry.description, "Other routing.")
        self.assertEqual(entry.required_tools, ("web_open",))
        self.assertNotEqual(old.catalog_revision, new.catalog_revision)
        self.assertNotEqual(old.by_name()["demo"].revision, entry.revision)
        self.assertEqual(old.by_name()["demo"].instructions, "old body")
        self.assertNotIn("demo：", self.registry.prompt_catalog(available_tool_names={"exec_run"}))
        self.assertIn("demo：", self.registry.prompt_catalog(available_tool_names={"web_open"}))

    def test_revision_matches_the_instructions_read_during_atomic_update(self) -> None:
        original = _markdown()
        replacement = _markdown(body="new body")
        self.registry.snapshot()
        read_bytes = Path.read_bytes
        changed = False

        def read_then_replace(path: Path) -> bytes:
            nonlocal changed
            raw = read_bytes(path)
            if path == self.markdown and not changed:
                changed = True
                pending = path.with_suffix(".pending")
                pending.write_bytes(replacement)
                pending.replace(path)
            return raw

        with patch.object(Path, "read_bytes", read_then_replace):
            entry = self.registry.snapshot().by_name()["demo"]
        self.assertTrue(changed)
        self.assertEqual(entry.instructions, "old body")
        self.assertEqual(entry.instruction_sha256, hashlib.sha256(original).hexdigest())
        next_entry = self.registry.snapshot().by_name()["demo"]
        self.assertEqual(next_entry.instructions, "new body")
        self.assertEqual(next_entry.instruction_sha256, hashlib.sha256(replacement).hexdigest())

    def test_unchanged_markdown_still_checks_file_changes_and_package_size(self) -> None:
        with patch("companion_v01.skill_runtime.SKILL_MAX_PACKAGE_BYTES", 512):
            self.registry.snapshot()
            reference = self.skill / "reference.txt"
            reference.write_text("reference", encoding="utf-8")
            self.assertEqual(self.registry.snapshot().by_name()["demo"].files, ("reference.txt",))
            reference.unlink()
            self.assertEqual(self.registry.snapshot().by_name()["demo"].files, ())

            payload = self.skill / "payload.bin"
            payload.write_bytes(b"x" * 513)
            invalid = self.registry.snapshot()
            self.assertEqual(invalid.diagnostics[0]["reason"], "skill_package_too_large")
            self.assertEqual(invalid.diagnostics[0]["fallback"], "last_good")
            self.assertEqual(invalid.by_name()["demo"].files, ())
            payload.write_bytes(b"x")
            recovered = self.registry.snapshot()
            self.assertEqual(recovered.diagnostics, ())
            self.assertEqual(recovered.by_name()["demo"].files, ("payload.bin",))

    def test_cached_instructions_do_not_bypass_read_failure(self) -> None:
        self.registry.snapshot()
        read_bytes = Path.read_bytes

        def denied(path: Path) -> bytes:
            if path == self.markdown:
                raise PermissionError("unreadable skill")
            return read_bytes(path)

        with patch.object(Path, "read_bytes", denied):
            invalid = self.registry.snapshot()
        self.assertEqual(invalid.diagnostics[0]["reason"], "skill_read_failed")
        self.assertEqual(invalid.diagnostics[0]["fallback"], "last_good")
        self.markdown.write_bytes(_markdown(body="new body"))
        recovered = self.registry.snapshot()
        self.assertEqual(recovered.diagnostics, ())
        self.assertEqual(recovered.by_name()["demo"].instructions, "new body")

    def test_package_stat_failure_keeps_last_good_then_recovers(self) -> None:
        reference = self.skill / "reference.txt"
        reference.write_text("reference", encoding="utf-8")
        self.registry.snapshot()
        lstat = Path.lstat

        def denied(path: Path) -> os.stat_result:
            if path == reference:
                raise PermissionError("unreadable package file")
            return lstat(path)

        with patch.object(Path, "lstat", denied):
            invalid = self.registry.snapshot()
        self.assertEqual(invalid.diagnostics[0]["reason"], "skill_package_stat_failed")
        self.assertEqual(invalid.diagnostics[0]["fallback"], "last_good")
        self.assertEqual(self.registry.snapshot().diagnostics, ())

    def test_added_symlink_is_rejected_after_markdown_is_cached(self) -> None:
        self.registry.snapshot()
        outside = self.root / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        try:
            (self.skill / "reference.txt").symlink_to(outside)
        except OSError:
            self.skipTest("symlink creation unavailable")
        invalid = self.registry.snapshot()
        self.assertEqual(invalid.diagnostics[0]["reason"], "skill_package_symlink_not_allowed")
        self.assertEqual(invalid.diagnostics[0]["fallback"], "last_good")
        self.assertEqual(invalid.by_name()["demo"].files, ())

    def test_symlink_file_metadata_is_rejected_even_with_cached_instructions(self) -> None:
        reference = self.skill / "reference.txt"
        reference.write_text("reference", encoding="utf-8")
        self.registry.snapshot()
        lstat = Path.lstat

        def link_metadata(path: Path) -> os.stat_result:
            metadata = lstat(path)
            if path == reference:
                values = list(metadata)
                values[0] = stat.S_IFLNK | stat.S_IMODE(metadata.st_mode)
                return os.stat_result(values)
            return metadata

        with patch.object(Path, "lstat", link_metadata):
            invalid = self.registry.snapshot()
        self.assertEqual(invalid.diagnostics[0]["reason"], "skill_package_symlink_not_allowed")
        self.assertEqual(invalid.diagnostics[0]["fallback"], "last_good")
        self.assertEqual(self.registry.snapshot().diagnostics, ())

    def test_plugin_root_and_generation_switch_refresh_execution_locators(self) -> None:
        roots = []
        registry = SkillRegistry(
            bundled_root=self.root / "empty-bundled",
            managed_root=self.root / "empty-managed",
            execution_workspace_root=self.root / "workspace",
            contributed_roots_provider=lambda: tuple(roots),
        )
        for generation in (1, 2):
            root = self.root / f"plugin-{generation}" / "demo"
            root.mkdir(parents=True)
            (root / "SKILL.md").write_bytes(_markdown())
            (root / f"reference-{generation}.txt").write_text(str(generation), encoding="utf-8")
            mount = f"plugin_skill_generation_{generation}"
            roots[:] = [ContributedSkillRoot(source="plugin:demo", root=root, mount_name=mount)]
            loaded = registry.load("demo")
            self.assertEqual(loaded.execution_cwd, f"alias:{mount}")
            self.assertEqual(loaded.execution_path, "SKILL.md")
            self.assertEqual(loaded.files, (f"reference-{generation}.txt",))
            self.assertEqual(registry.mount_paths()[mount], root.resolve())
        roots.clear()
        self.assertEqual(registry.load("demo").status, "not_found")
        self.assertNotIn("demo：", registry.prompt_catalog())


if __name__ == "__main__":
    unittest.main()
