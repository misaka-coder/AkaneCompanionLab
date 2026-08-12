"""Phase 3 acceptance tests: Skill runtime edge cases and real-handler MemCore round.

Covers the acceptance-matrix rows that the existing suites do not exercise yet:
  1. Broken frontmatter (bad YAML) isolates only that Skill; others still load.
  2. publish conflict when replace=false and the Skill already exists.
  3. publish rollback when the final os.replace fails mid-install.
  4. Package limits: too many files, package too large, symlink rejected,
     oversized/non-UTF8 SKILL.md.
  5. Reference read failures: oversized and non-UTF8 resources.
  6. Draft path traversal / absolute path / missing directory rejection.
  7. Prompt catalog truncation (100 items / 16k chars) with an honest note.
  8. Managed Skill that was never valid does not shadow a same-named bundled Skill.
  9. publish installs references/ and scripts/ and they stay loadable/listed.
 10. MemCore round records the REAL load_skill handler output (not a fabricated
     string) and it survives settlement with open_memory reload.
 11. Published scripts really execute through the local execution provider;
     success output and non-zero stderr/exit status remain truthful.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from companion_v01.skill_runtime import (
    SkillRegistry,
    SKILL_MAX_PACKAGE_FILES,
    SKILL_MAX_PROMPT_ITEMS,
)
from companion_v01.execution_local import TrustedLocalExecutor
from companion_v01.execution_run import ExecutionRunOwner
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.skills import LoadSkillToolHandler


def _write_skill(root: Path, name: str, description: str, body: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return skill_dir


def _new_registry(tmp: Path) -> SkillRegistry:
    bundled = tmp / "bundled"
    managed = tmp / "managed"
    workspace = tmp / "workspace"
    for path in (bundled, managed, workspace):
        path.mkdir(parents=True, exist_ok=True)
    return SkillRegistry(
        bundled_root=bundled,
        managed_root=managed,
        execution_workspace_root=workspace,
    )


class SkillRuntimeAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = _new_registry(self.root)

    def test_bad_frontmatter_yaml_isolates_only_that_skill(self) -> None:
        _write_skill(self.registry.managed_root, "good-a", "A.", "a body")
        _write_skill(self.registry.managed_root, "good-b", "B.", "b body")
        bad_dir = self.registry.managed_root / "bad"
        bad_dir.mkdir()
        (bad_dir / "SKILL.md").write_text("---\nname: bad\ndescription: [unclosed\n---\nbody\n", encoding="utf-8")
        snapshot = self.registry.snapshot()
        names = {entry.name for entry in snapshot.entries}
        self.assertEqual(names, {"good-a", "good-b"})
        self.assertEqual(snapshot.by_name()["good-a"].instructions, "a body")
        self.assertEqual(snapshot.by_name()["good-b"].instructions, "b body")
        bad_diag = next((item for item in snapshot.diagnostics if item["name"] == "bad"), None)
        self.assertIsNotNone(bad_diag)
        self.assertEqual(bad_diag["reason"], "skill_frontmatter_invalid")
        self.assertEqual(bad_diag["fallback"], "ignored")
        self.assertEqual(self.registry.load("good-b").status, "loaded")

    def test_hot_update_bad_yaml_falls_back_to_last_good(self) -> None:
        skill_dir = _write_skill(self.registry.managed_root, "demo", "Valid.", "valid body")
        self.registry.snapshot()
        (skill_dir / "SKILL.md").write_text("---\nname: demo\ndescription: [unclosed\n---\nbody\n", encoding="utf-8")
        snapshot = self.registry.snapshot()
        self.assertEqual(snapshot.by_name()["demo"].instructions, "valid body")
        diag = next(item for item in snapshot.diagnostics if item["name"] == "demo")
        self.assertEqual(diag["fallback"], "last_good")

    def test_publish_conflict_when_replace_false(self) -> None:
        _write_skill(self.registry.execution_workspace_root / "skill_drafts", "demo", "New demo.", "new body")
        _write_skill(self.registry.managed_root, "demo", "Old demo.", "old body")
        result = self.registry.publish("skill_drafts/demo")
        self.assertEqual(result.status, "conflict")
        self.assertEqual(result.reason, "skill_exists")
        self.assertEqual(self.registry.load("demo").content, "old body")

    def test_publish_rolls_back_when_final_replace_fails(self) -> None:
        _write_skill(self.registry.managed_root, "demo", "Old demo.", "old body")
        _write_skill(self.registry.execution_workspace_root / "skill_drafts", "demo", "New demo.", "new body")
        real_replace = os.replace
        calls = {"count": 0}

        def flaky_replace(src, dst):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("simulated replace failure")
            return real_replace(src, dst)

        with mock.patch("companion_v01.skill_runtime.os.replace", side_effect=flaky_replace):
            result = self.registry.publish("skill_drafts/demo", replace=True)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.reason, "skill_publish_failed")
        self.assertEqual(self.registry.load("demo").content, "old body")
        leftovers = [
            item.name
            for item in self.registry.managed_root.iterdir()
            if ".tmp-" in item.name or ".old-" in item.name
        ]
        self.assertEqual(leftovers, [])

    def test_package_file_count_limit_rejected(self) -> None:
        skill_dir = _write_skill(self.registry.managed_root, "bulk", "Bulk.", "body")
        for index in range(SKILL_MAX_PACKAGE_FILES):
            (skill_dir / f"extra_{index:03d}.txt").write_text("x", encoding="utf-8")
        snapshot = self.registry.snapshot()
        self.assertNotIn("bulk", snapshot.by_name())
        diag = next(item for item in snapshot.diagnostics if item["name"] == "bulk")
        self.assertEqual(diag["reason"], "skill_package_too_many_files")

    def test_package_total_size_limit_rejected(self) -> None:
        skill_dir = _write_skill(self.registry.managed_root, "big", "Big.", "body")
        (skill_dir / "payload.bin").write_bytes(b"\x00" * (9 * 1024 * 1024))
        snapshot = self.registry.snapshot()
        self.assertNotIn("big", snapshot.by_name())
        diag = next(item for item in snapshot.diagnostics if item["name"] == "big")
        self.assertEqual(diag["reason"], "skill_package_too_large")

    def test_skill_md_too_large_and_non_utf8_rejected(self) -> None:
        skill_dir = _write_skill(self.registry.managed_root, "huge", "Huge.", "body")
        (skill_dir / "SKILL.md").write_bytes(b"\x00" * (257 * 1024))
        diag = next(
            item
            for item in self.registry.snapshot().diagnostics
            if item["name"] == "huge"
        )
        self.assertEqual(diag["reason"], "skill_instructions_too_large")

        not_utf8 = self.registry.managed_root / "raw"
        not_utf8.mkdir()
        (not_utf8 / "SKILL.md").write_bytes(b"\xff\xfe\x00\x81")
        diag = next(
            item
            for item in self.registry.snapshot().diagnostics
            if item["name"] == "raw"
        )
        self.assertEqual(diag["reason"], "skill_instructions_not_utf8")

    def test_package_symlink_rejected(self) -> None:
        target = self.registry.managed_root / "real.txt"
        target.write_text("x", encoding="utf-8")
        skill_dir = _write_skill(self.registry.managed_root, "linky", "Linky.", "body")
        try:
            (skill_dir / "link.txt").symlink_to(target)
        except OSError:
            self.skipTest("symlink creation unavailable on this platform")
        snapshot = self.registry.snapshot()
        self.assertNotIn("linky", snapshot.by_name())
        diag = next(item for item in snapshot.diagnostics if item["name"] == "linky")
        self.assertEqual(diag["reason"], "skill_package_symlink_not_allowed")

    def test_resource_too_large_and_non_utf8_read_errors(self) -> None:
        skill_dir = _write_skill(self.registry.managed_root, "demo", "Demo.", "body")
        (skill_dir / "references").mkdir(exist_ok=True)
        (skill_dir / "references" / "big.bin").write_bytes(b"\x00" * (257 * 1024))
        (skill_dir / "references" / "raw.bin").write_bytes(b"\xff\xfe\x00\x81")
        big = self.registry.load("demo", resource="references/big.bin")
        self.assertEqual(big.status, "error")
        self.assertEqual(big.reason, "skill_resource_too_large")
        raw = self.registry.load("demo", resource="references/raw.bin")
        self.assertEqual(raw.status, "error")
        self.assertEqual(raw.reason, "skill_resource_not_utf8")

    def test_draft_path_traversal_absolute_and_missing_rejected(self) -> None:
        for bad_path in ("../outside", "skill_drafts/../outside", "/etc/passwd", "C:/Windows/win.ini"):
            result = self.registry.validate_draft(bad_path)
            self.assertEqual(result.status, "invalid", bad_path)
        result = self.registry.validate_draft("skill_drafts/nope")
        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.reason, "skill_draft_directory_missing")

    def test_prompt_catalog_truncates_at_limit_with_honest_note(self) -> None:
        for index in range(SKILL_MAX_PROMPT_ITEMS + 5):
            _write_skill(
                self.registry.managed_root,
                f"skill_{index:03d}",
                "Description for a skill used in the truncation probe.",
                f"body {index}",
            )
        catalog = self.registry.prompt_catalog()
        self.assertIn(f"目录已截到 {SKILL_MAX_PROMPT_ITEMS}/{SKILL_MAX_PROMPT_ITEMS + 5} 项", catalog)
        self.assertIn("可用 load_skill 按已知名称打开", catalog)

    def test_never_valid_managed_skill_does_not_shadow_bundled(self) -> None:
        _write_skill(self.registry.bundled_root, "demo", "Bundled demo.", "bundled body")
        broken = self.registry.managed_root / "demo"
        broken.mkdir()
        (broken / "SKILL.md").write_text("---\nname: demo\ndescription: [unclosed\n---\nbody\n", encoding="utf-8")
        snapshot = self.registry.snapshot()
        self.assertEqual(snapshot.by_name()["demo"].source, "bundled")
        self.assertEqual(snapshot.by_name()["demo"].instructions, "bundled body")

    def test_publish_installs_references_and_scripts_and_keeps_them_usable(self) -> None:
        draft = self.registry.execution_workspace_root / "skill_drafts" / "helper"
        _write_skill(draft.parent, "helper", "Helper.", "Read references/guide.md when needed; scripts/tool.py is run via exec_run.")
        (draft / "references").mkdir()
        (draft / "references" / "guide.md").write_text("guidance", encoding="utf-8")
        (draft / "scripts").mkdir()
        (draft / "scripts" / "tool.py").write_text("print('ok')", encoding="utf-8")
        published = self.registry.publish("skill_drafts/helper")
        self.assertEqual(published.status, "published")
        loaded = self.registry.load("helper")
        self.assertEqual(loaded.status, "loaded")
        self.assertIn("references/guide.md", loaded.files)
        self.assertIn("scripts/tool.py", loaded.files)
        guide = self.registry.load("helper", resource="references/guide.md")
        self.assertEqual(guide.status, "loaded")
        self.assertEqual(guide.content, "guidance")

    def test_published_scripts_execute_via_shell_with_real_success_and_failure(self) -> None:
        draft = self.registry.execution_workspace_root / "skill_drafts" / "runner"
        _write_skill(draft.parent, "runner", "Runner.", "Run scripts through exec_run.")
        (draft / "scripts").mkdir()
        (draft / "scripts" / "ok.py").write_text("print('skill-script-ok')\n", encoding="utf-8")
        (draft / "scripts" / "fail.py").write_text(
            "import sys\nprint('skill-script-failed', file=sys.stderr)\nsys.exit(7)\n",
            encoding="utf-8",
        )
        published = self.registry.publish("skill_drafts/runner")
        self.assertEqual(published.status, "published")

        executor = TrustedLocalExecutor(
            workspace_root=self.registry.execution_workspace_root,
            run_log_dir=self.root / "runlogs",
            mounts=self.registry.mount_paths(),
        )
        owner = ExecutionRunOwner(profile_user_id="master", session_id="s1", provider_id="local")

        def command_for(relative_script: str) -> str:
            args = [sys.executable, relative_script]
            return subprocess.list2cmdline(args) if os.name == "nt" else " ".join(shlex.quote(arg) for arg in args)

        succeeded = executor.run(
            owner=owner,
            command=command_for("runner/scripts/ok.py"),
            cwd="alias:skills",
            initial_wait_seconds=3,
        )
        self.assertEqual(succeeded.status, "completed")
        self.assertEqual(succeeded.exit_code, 0)
        self.assertIn("skill-script-ok", succeeded.stdout)

        failed = executor.run(
            owner=owner,
            command=command_for("runner/scripts/fail.py"),
            cwd="alias:skills",
            initial_wait_seconds=3,
        )
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.exit_code, 7)
        self.assertIn("skill-script-failed", failed.stderr)


class _FakeLLM:
    pass


class _FakeEmbeddingProvider:
    name = "hashed"
    version = "test"
    dimension = 8

    def embed_text(self, text: str) -> list[float]:
        return [0.0] * self.dimension


class SkillMemcoreRealHandlerTests(unittest.TestCase):
    """MemCore round uses the REAL load_skill handler output, not fabricated text."""

    def test_real_load_skill_handler_result_survives_settlement_and_reloads(self) -> None:
        import config
        from unittest.mock import patch

        from companion_v01.memcore_integration.manager import MemcoreManager

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            registry = _new_registry(tmp / "skills")
            long_body = "执行步骤：" + ("遵循步骤并逐步执行" * 220)
            _write_skill(registry.managed_root, "demo", "Demo workflow.", long_body)
            handler = LoadSkillToolHandler(registry=registry)
            result = handler.execute(
                call={"type": "load_skill", "name": "demo"},
                context=ToolExecutionContext("master", "s1", 1, {}),
            )
            real_output = result.followup_context
            self.assertIn("遵循步骤并逐步执行", real_output)
            self.assertGreater(len(real_output), 512)

            with patch.object(config, "MEMCORE_OPERATION_PROJECTION_POLICY", "compact_after_terminal"):
                manager = MemcoreManager(
                    backend="memcore",
                    storage_path=tmp / "skill.sqlite3",
                    visible_scope="conversation",
                    enable_flavor=False,
                    shadow_compare=False,
                    llm=_FakeLLM(),
                    embedding_provider=_FakeEmbeddingProvider(),
                )
                try:
                    opened = manager.begin_input_turn(
                        {"source_id": "user-skill", "content": "加载一下 demo 技能", "timestamp": 100},
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                    )
                    turn_id = str(opened.get("turn_id") or "")
                    batch = manager.record_tool_batch(
                        exchanges=[
                            {
                                "tool_name": "load_skill",
                                "tool_call_id": "call-load-demo",
                                "tool_input": {"name": "demo"},
                                "result": real_output,
                                "source": "load_skill",
                                "timestamp": 101,
                                "source_id_prefix": "tooltrace-skill",
                                "result_status": "success",
                            }
                        ],
                        turn_id=turn_id,
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                    )
                    self.assertTrue(batch["ok"], batch)
                    observation_sid = str(batch["exchanges"][0]["tool_result_source_id"])
                    manager.build_context_projection(
                        provider_profile="openai",
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                    )
                    manager.complete_input_turn(
                        turn_id=turn_id,
                        assistant_record={"source_id": "assistant-skill", "content": "已加载。", "timestamp": 102},
                        memory_metadata={},
                        provider_output_raw="已加载。",
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                        provider_profile="openai",
                        provider_projection={"role": "assistant", "content": "已加载。"},
                    )
                    projection = manager.build_context_projection(
                        provider_profile="openai",
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                    )
                    tool_payloads = [m for m in projection.get("payloads") or [] if m.get("role") == "tool"]
                    self.assertEqual(len(tool_payloads), 1)
                    card_text = str(tool_payloads[0].get("content") or "")
                    self.assertIn("[compact_reloadable]", card_text)
                    self.assertIn("tool: load_skill", card_text)
                    self.assertIn("reload: open_memory(memory_id=", card_text)
                    self.assertNotIn("遵循步骤并逐步执行" * 4, card_text)
                    self.assertNotIn(str(tmp), card_text)
                    self.assertNotIn("C:\\", card_text)
                    expanded = manager.open_memory(
                        profile_user_id="u1",
                        session_id="s1",
                        character_pack_id="char",
                        arguments={"memory_id": observation_sid, "view": "content"},
                    )
                    self.assertTrue(expanded["ok"], expanded)
                    self.assertIn("遵循步骤并逐步执行", expanded["text"])
                finally:
                    manager.close()


if __name__ == "__main__":
    unittest.main()
