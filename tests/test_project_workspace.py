from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from companion_v01.project_workspace import ProjectWorkspaceError, ProjectWorkspaceService
from companion_v01.execution_local import TrustedLocalExecutor
from companion_v01.store import MemoryStore
from companion_v01.tool_handlers.execution import ExecRunToolHandler
from companion_v01.tool_handlers.project_workspace import (
    ManageProjectWorkspaceToolHandler,
    ProjectInspectToolHandler,
    WorkspacePatchToolHandler,
    WorkspaceWriteToolHandler,
)
from companion_v01.tool_runtime import ToolExecutionContext


class ProjectWorkspaceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = MemoryStore(self.root / "data")
        self.execution_root = self.root / "execution"
        self.service = ProjectWorkspaceService(
            store=self.store,
            execution_workspace_root=self.execution_root,
        )
        self.private = self.service.scope_for(
            profile_user_id="alice",
            session_id="private-a",
            client_mode="qq",
        )

    @staticmethod
    def _context(
        *,
        session_id: str = "private-a",
        actor_stable_id: str = "",
        actor_profile_user_id: str = "",
    ) -> ToolExecutionContext:
        request_context = {}
        if actor_stable_id:
            request_context["actor_stable_id"] = actor_stable_id
        if actor_profile_user_id:
            request_context["actor_profile_user_id"] = actor_profile_user_id
        return ToolExecutionContext(
            profile_user_id="alice",
            session_id=session_id,
            now_ts=0,
            visual_payload={},
            client_mode="qq",
            request_context=request_context,
        )

    def test_catalog_crosses_conversations_but_selection_does_not(self) -> None:
        created = self.service.create(scope=self.private, display_name="Demo Project")
        other_session = self.service.scope_for(
            profile_user_id="alice",
            session_id="private-b",
            client_mode="qq",
        )

        current = self.service.current(scope=other_session)
        listed = self.service.list(scope=other_session)

        self.assertIsNone(current)
        self.assertEqual([item["workspace_id"] for item in listed["workspaces"]], [created["workspace_id"]])
        selected = self.service.select(scope=other_session, workspace_id=created["workspace_id"])
        self.assertEqual(selected["alias"], "alias:project")
        self.assertNotIn(str(self.execution_root), str(selected))
        cwd = self.service.execution_cwd(scope=other_session, alias_value="alias:project")
        self.assertEqual(cwd, f"Projects/{created['workspace_id']}")

    def test_qq_catalog_follows_actor_across_private_and_groups(self) -> None:
        with self.assertRaisesRegex(ProjectWorkspaceError, "group_actor_required"):
            self.service.scope_for(
                profile_user_id="group-profile",
                session_id="qq_group_shared_100",
                client_mode="qq",
            )
        alice = self.service.scope_for(
            profile_user_id="group-profile",
            session_id="qq_group_shared_100",
            client_mode="qq",
            actor_stable_id="qq:1",
            actor_profile_user_id="alice",
        )
        bob = self.service.scope_for(
            profile_user_id="group-profile",
            session_id="qq_group_shared_100",
            client_mode="qq",
            actor_stable_id="qq:2",
            actor_profile_user_id="bob",
        )
        other_group = self.service.scope_for(
            profile_user_id="group-profile",
            session_id="qq_group_shared_200",
            client_mode="qq",
            actor_stable_id="qq:1",
            actor_profile_user_id="alice",
        )
        created = self.service.create(scope=alice, display_name="Alice Project")

        self.assertEqual(self.service.list(scope=bob)["workspaces"], [])
        self.assertEqual(self.service.list(scope=other_group)["workspaces"][0]["workspace_id"], created["workspace_id"])
        self.assertEqual(self.service.list(scope=self.private)["workspaces"][0]["workspace_id"], created["workspace_id"])
        with self.assertRaisesRegex(ProjectWorkspaceError, "workspace_not_found"):
            self.service.select(scope=bob, workspace_id=created["workspace_id"])
        self.assertIsNone(self.service.current(scope=other_group))

    def test_real_qq_text_mode_shares_catalog_across_private_and_multiple_groups(self) -> None:
        private = self.service.scope_for(
            profile_user_id="qq_10003",
            session_id="qq_pri_10003",
            client_mode="qq_text",
        )
        group_a = self.service.scope_for(
            profile_user_id="qq_group_shared_20001",
            session_id="qq_group_shared_20001",
            client_mode="qq_text",
            actor_stable_id="qq:10003",
            actor_profile_user_id="qq_10003",
        )
        group_b = self.service.scope_for(
            profile_user_id="qq_group_shared_20002",
            session_id="qq_group_shared_20002",
            client_mode="qq_text",
            actor_stable_id="qq:10003",
            actor_profile_user_id="qq_10003",
        )
        created = self.service.create(scope=private, display_name="Cross Channel")

        for scope in (group_a, group_b):
            self.assertEqual(self.service.list(scope=scope)["workspaces"][0]["workspace_id"], created["workspace_id"])
            self.assertIsNone(self.service.current(scope=scope))
        self.service.select(scope=group_a, workspace_id=created["workspace_id"])
        self.assertEqual(self.service.current(scope=group_a)["workspace_id"], created["workspace_id"])
        self.assertIsNone(self.service.current(scope=group_b))
        self.assertEqual(self.service.current(scope=private)["workspace_id"], created["workspace_id"])

    def test_legacy_private_catalog_migration_is_idempotent(self) -> None:
        legacy = self.service.scope_for(
            profile_user_id="alice",
            session_id="legacy-private",
            client_mode="legacy",
        )
        created = self.service.create(scope=legacy, display_name="Legacy Project")

        first = self.service.list(scope=self.private)
        second = self.service.list(scope=self.private)

        self.assertEqual(first["workspaces"][0]["workspace_id"], created["workspace_id"])
        self.assertEqual(second, first)
        record = self.store.get_project_workspace(created["workspace_id"])
        self.assertEqual(record["owner_kind"], "qq_user")

    def test_archive_clears_selection_without_deleting_files(self) -> None:
        created = self.service.create(scope=self.private, display_name="Keep Files")
        self.service.write(scope=self.private, path="src/main.js", content="console.log('ok');\n")

        archived = self.service.archive(scope=self.private, workspace_id=created["workspace_id"])

        self.assertEqual(archived["state"], "archived")
        self.assertIsNone(self.service.current(scope=self.private))
        physical = self.execution_root / "Projects" / created["workspace_id"] / "src" / "main.js"
        self.assertTrue(physical.is_file())

    def test_desktop_host_binding_is_private_idempotent_and_executable(self) -> None:
        desktop = self.service.scope_for(
            profile_user_id="alice",
            session_id="desktop-a",
            client_mode="desktop_pet",
        )
        external = self.root / "外部 项目"
        external.mkdir()
        (external / "src").mkdir()
        bound = self.service.bind_existing(scope=desktop, host_directory=external)
        rebound = self.service.bind_existing(scope=desktop, host_directory=external)

        self.assertEqual(bound["workspace_id"], rebound["workspace_id"])
        self.assertTrue(rebound["already_bound"])
        self.assertEqual(bound["root_kind"], "host_bound")
        self.assertTrue(bound["available"])
        self.assertNotIn(str(external), str(bound))
        self.assertNotIn("host_root_path", bound)
        self.service.write(scope=desktop, path="src/main.js", content="console.log('bound');\n")
        self.assertEqual(
            (external / "src" / "main.js").read_text(encoding="utf-8"),
            "console.log('bound');\n",
        )

        provider = TrustedLocalExecutor(
            workspace_root=self.execution_root,
            run_log_dir=self.root / "runlogs",
        )
        cwd = self.service.execution_cwd(
            scope=desktop,
            alias_value="alias:project/src",
            execution_provider=provider,
        )
        self.assertTrue(cwd.startswith("alias:project_"))
        self.assertEqual(provider._resolve_workdir(cwd), (external / "src").resolve())

    def test_host_binding_accepts_qq_but_rejects_protected_or_managed_roots(self) -> None:
        external = self.root / "safe-project"
        external.mkdir()
        opened = self.service.bind_existing(scope=self.private, host_directory=external)
        self.assertEqual(opened["root_kind"], "host_bound")
        self.assertNotIn(str(external), str(opened))

        desktop = self.service.scope_for(
            profile_user_id="alice",
            session_id="desktop-a",
            client_mode="desktop_pet",
        )
        with self.assertRaisesRegex(ProjectWorkspaceError, "host_directory_managed_by_runtime"):
            self.service.bind_existing(scope=desktop, host_directory=self.execution_root)
        with self.assertRaisesRegex(ProjectWorkspaceError, "host_directory_protected"):
            self.service.bind_existing(scope=desktop, host_directory=Path(self.store.base_dir))

    def test_host_binding_allows_project_parent_that_contains_internal_subdirectory(self) -> None:
        host_root = self.root / "host-repository"
        internal = host_root / ".akane-state"
        internal.mkdir(parents=True)
        service = ProjectWorkspaceService(
            store=self.store,
            execution_workspace_root=self.execution_root,
            protected_roots=(internal,),
        )

        opened = service.bind_existing(scope=self.private, host_directory=host_root)

        self.assertEqual(opened["root_kind"], "host_bound")

    def test_missing_bound_directory_clears_selection_without_leaking_path(self) -> None:
        desktop = self.service.scope_for(
            profile_user_id="alice",
            session_id="desktop-a",
            client_mode="desktop_pet",
        )
        external = self.root / "temporary-project"
        external.mkdir()
        bound = self.service.bind_existing(scope=desktop, host_directory=external)
        external.rmdir()

        self.assertIsNone(self.service.current(scope=desktop))
        listed = self.service.list(scope=desktop)["workspaces"][0]
        self.assertEqual(listed["workspace_id"], bound["workspace_id"])
        self.assertFalse(listed["available"])
        self.assertFalse(listed["selected"])
        self.assertNotIn(str(external), str(listed))
        self.assertEqual(self.service.list(scope=desktop)["selected_workspace_id"], "")

    def test_write_is_atomic_hash_guarded_and_confined(self) -> None:
        self.service.create(scope=self.private, display_name="Writer")
        first = self.service.write(scope=self.private, path="src/app.js", content="one\n", mode="create")
        expected = hashlib.sha256(b"one\n").hexdigest()
        second = self.service.write(
            scope=self.private,
            path="src/app.js",
            content="two\n",
            expected_sha256=expected,
            mode="replace",
        )
        self.assertFalse(second["created"])
        self.assertEqual(second["sha256"], hashlib.sha256(b"two\n").hexdigest())
        with self.assertRaisesRegex(ProjectWorkspaceError, "base_hash_mismatch"):
            self.service.write(
                scope=self.private,
                path="src/app.js",
                content="bad\n",
                expected_sha256=expected,
                mode="replace",
            )
        with self.assertRaisesRegex(ProjectWorkspaceError, "path_outside_workspace"):
            self.service.write(scope=self.private, path="../escape.txt", content="no")
        target = self.execution_root / "Projects" / first["workspace_id"] / "src" / "app.js"
        self.assertEqual(target.read_text(encoding="utf-8"), "two\n")

    def test_project_inspect_lists_searches_and_reads_relative_utf8_source(self) -> None:
        created = self.service.create(scope=self.private, display_name="Inspector")
        self.service.write(
            scope=self.private,
            path="src/app.py",
            content="def alpha():\n    return 'Needle'\n",
        )
        self.service.write(scope=self.private, path="README.md", content="# Demo\n")
        project = self.execution_root / "Projects" / created["workspace_id"]
        (project / ".hidden.py").write_text("Needle hidden\n", encoding="utf-8")
        (project / "node_modules" / "pkg").mkdir(parents=True)
        (project / "node_modules" / "pkg" / "index.js").write_text("Needle dependency\n", encoding="utf-8")

        listed = self.service.inspect_list(scope=self.private, path=".", max_depth=3)
        searched = self.service.inspect_search(
            scope=self.private,
            path=".",
            query="needle",
            include="*.py",
            case_sensitive=False,
        )
        read = self.service.inspect_read(scope=self.private, path="src/app.py")

        listed_paths = [item["path"] for item in listed["entries"]]
        self.assertIn("src/app.py", listed_paths)
        self.assertIn("node_modules/", listed_paths)
        self.assertNotIn("node_modules/pkg/index.js", listed_paths)
        self.assertNotIn(".hidden.py", listed_paths)
        self.assertEqual(listed["pruned_directories"], 1)
        self.assertEqual([(item["path"], item["line"]) for item in searched["matches"]], [("src/app.py", 2)])
        self.assertTrue(searched["scan_complete"])
        self.assertEqual(read["lines"], ["def alpha():", "    return 'Needle'"])
        self.assertEqual(read["sha256"], hashlib.sha256(b"def alpha():\n    return 'Needle'\n").hexdigest())
        self.assertNotIn(str(project), str(listed))

    def test_project_inspect_handler_discloses_scan_boundaries(self) -> None:
        created = self.service.create(scope=self.private, display_name="Inspection Disclosure")
        project = self.execution_root / "Projects" / created["workspace_id"]
        (project / "binary.dat").write_bytes(b"\x00\xffbinary")
        (project / "__pycache__").mkdir()
        (project / "__pycache__" / "cached.pyc").write_bytes(b"\x00cache")

        handler = ProjectInspectToolHandler(service=self.service)
        list_call = handler.normalize_call(
            {"type": "project_inspect", "action": "list", "path": ".", "max_depth": 2}
        )
        search_call = handler.normalize_call(
            {
                "type": "project_inspect",
                "action": "search",
                "path": ".",
                "query": "needle",
                "include": "*",
            }
        )

        listed = handler.execute(call=list_call, context=self._context())
        searched = handler.execute(call=search_call, context=self._context())

        self.assertIn("未递归标准缓存/构建目录 1 个", listed.followup_context)
        self.assertIn("扫描：0 个 UTF-8 文本文件 / 0 字节；跳过二进制 1 个", searched.followup_context)
        self.assertEqual(searched.followup_envelope.diagnostics["skipped_binary"], 1)

    def test_project_inspect_rejects_escape_and_binary_text(self) -> None:
        created = self.service.create(scope=self.private, display_name="Inspector Limits")
        project = self.execution_root / "Projects" / created["workspace_id"]
        (project / "binary.dat").write_bytes(b"\x00\xff")
        (project / "target.txt").write_text("inside\n", encoding="utf-8")
        with self.assertRaisesRegex(ProjectWorkspaceError, "path_outside_workspace"):
            self.service.inspect_read(scope=self.private, path="../outside.py")
        with self.assertRaisesRegex(ProjectWorkspaceError, "source_not_text"):
            self.service.inspect_read(scope=self.private, path="binary.dat")
        try:
            (project / "linked.txt").symlink_to(project / "target.txt")
        except OSError:
            pass
        else:
            with self.assertRaisesRegex(ProjectWorkspaceError, "path_outside_workspace"):
                self.service.inspect_read(scope=self.private, path="linked.txt")

    def test_project_inspect_search_reports_regex_and_preview_boundaries(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        self.service.create(scope=self.private, display_name="Search Boundaries")
        long_line = "prefix " + ("x" * 900) + " NEEDLE"
        self.service.write(scope=self.private, path="src/long.txt", content=long_line + "\n")

        result = self.service.inspect_search(
            scope=self.private,
            query=r"NE+DLE$",
            include="src/*.txt",
            regex=True,
        )

        self.assertEqual(len(result["matches"]), 1)
        self.assertTrue(result["matches"][0]["preview_truncated"])
        self.assertEqual(len(result["matches"][0]["text"]), 800)
        self.assertIn("NEEDLE", result["matches"][0]["text"])
        self.assertGreater(result["matches"][0]["preview_start_column"], 1)
        with self.assertRaisesRegex(ProjectWorkspaceError, "invalid_regex"):
            self.service.inspect_search(scope=self.private, query="[", regex=True)
        with patch(
            "companion_v01.project_workspace.regex_engine.compile",
            return_value=SimpleNamespace(search=lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError())),
        ):
            with self.assertRaisesRegex(ProjectWorkspaceError, "search_timeout"):
                self.service.inspect_search(scope=self.private, query="needle")

    def test_multi_file_patch_applies_and_failed_hunk_leaves_every_file_unchanged(self) -> None:
        self.service.create(scope=self.private, display_name="Patcher")
        self.service.write(scope=self.private, path="a.txt", content="alpha\nbeta\n")
        self.service.write(scope=self.private, path="b.txt", content="one\ntwo\n")
        patch = """--- a/a.txt
+++ b/a.txt
@@ -1,2 +1,2 @@
 alpha
-beta
+gamma
--- a/b.txt
+++ b/b.txt
@@ -1,2 +1,2 @@
 one
-two
+three
"""
        result = self.service.patch(scope=self.private, patch_text=patch)
        self.assertEqual([item["path"] for item in result["files"]], ["a.txt", "b.txt"])

        failing = """--- a/a.txt
+++ b/a.txt
@@ -1,2 +1,2 @@
 alpha
-gamma
+changed
--- a/b.txt
+++ b/b.txt
@@ -1,2 +1,2 @@
 missing
-three
+broken
"""
        with self.assertRaisesRegex(ProjectWorkspaceError, "hunk_not_applicable"):
            self.service.patch(scope=self.private, patch_text=failing)
        project = self.execution_root / "Projects" / result["workspace_id"]
        self.assertEqual((project / "a.txt").read_text(encoding="utf-8"), "alpha\ngamma\n")
        self.assertEqual((project / "b.txt").read_text(encoding="utf-8"), "one\nthree\n")

    def test_patch_atomically_creates_deletes_and_renames_utf8_files(self) -> None:
        created = self.service.create(scope=self.private, display_name="Full Patch")
        self.service.write(scope=self.private, path="old.txt", content="old\n")
        self.service.write(scope=self.private, path="remove.txt", content="remove\n")
        patch = """--- /dev/null
+++ b/new.txt
@@ -0,0 +1 @@
+new
--- a/old.txt
+++ b/moved.txt
@@ -1 +1 @@
-old
+moved
--- a/remove.txt
+++ /dev/null
@@ -1 +0,0 @@
-remove
"""

        result = self.service.patch(scope=self.private, patch_text=patch)

        project = self.execution_root / "Projects" / created["workspace_id"]
        self.assertEqual((project / "new.txt").read_text(encoding="utf-8"), "new\n")
        self.assertEqual((project / "moved.txt").read_text(encoding="utf-8"), "moved\n")
        self.assertFalse((project / "old.txt").exists())
        self.assertFalse((project / "remove.txt").exists())
        self.assertEqual(
            [item["operation"] for item in result["files"]],
            ["create", "rename", "delete"],
        )
        self.assertEqual(result["files"][1]["source_path"], "old.txt")
        self.assertEqual(result["files"][2]["sha256"], "")

    def test_patch_supports_content_preserving_rename(self) -> None:
        created = self.service.create(scope=self.private, display_name="Rename Patch")
        self.service.write(scope=self.private, path="before.txt", content="same\n")

        result = self.service.patch(
            scope=self.private,
            patch_text="--- a/before.txt\n+++ b/nested/after.txt\n",
        )

        project = self.execution_root / "Projects" / created["workspace_id"]
        self.assertFalse((project / "before.txt").exists())
        self.assertEqual((project / "nested" / "after.txt").read_text(encoding="utf-8"), "same\n")
        self.assertEqual(result["files"][0]["operation"], "rename")

    def test_patch_target_conflict_leaves_every_file_unchanged(self) -> None:
        created = self.service.create(scope=self.private, display_name="Conflict Patch")
        self.service.write(scope=self.private, path="a.txt", content="a\n")
        self.service.write(scope=self.private, path="occupied.txt", content="occupied\n")
        patch = """--- a/a.txt
+++ b/a.txt
@@ -1 +1 @@
-a
+changed
--- a/a.txt
+++ b/occupied.txt
@@ -1 +1 @@
-a
+moved
"""

        with self.assertRaisesRegex(ProjectWorkspaceError, "duplicate_patch_target"):
            self.service.patch(scope=self.private, patch_text=patch)

        project = self.execution_root / "Projects" / created["workspace_id"]
        self.assertEqual((project / "a.txt").read_text(encoding="utf-8"), "a\n")
        self.assertEqual((project / "occupied.txt").read_text(encoding="utf-8"), "occupied\n")

    def test_patch_commit_failure_restores_deleted_and_created_files(self) -> None:
        from unittest.mock import patch as mock_patch

        created = self.service.create(scope=self.private, display_name="Rollback Patch")
        self.service.write(scope=self.private, path="remove.txt", content="remove\n")
        self.service.write(scope=self.private, path="keep.txt", content="keep\n")
        patch = """--- a/remove.txt
+++ /dev/null
@@ -1 +0,0 @@
-remove
--- /dev/null
+++ b/generated/new.txt
@@ -0,0 +1 @@
+new
--- a/keep.txt
+++ b/keep.txt
@@ -1 +1 @@
-keep
+changed
"""
        real_write = self.service._atomic_write
        calls = 0

        def fail_second_write(target, data):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated commit failure")
            return real_write(target, data)

        with mock_patch.object(self.service, "_atomic_write", side_effect=fail_second_write):
            with self.assertRaisesRegex(ProjectWorkspaceError, "write_failed"):
                self.service.patch(scope=self.private, patch_text=patch)

        project = self.execution_root / "Projects" / created["workspace_id"]
        self.assertEqual((project / "remove.txt").read_text(encoding="utf-8"), "remove\n")
        self.assertEqual((project / "keep.txt").read_text(encoding="utf-8"), "keep\n")
        self.assertFalse((project / "generated").exists())

    def test_patch_reports_rollback_failure_without_hiding_commit_reason(self) -> None:
        from unittest.mock import patch as mock_patch

        created = self.service.create(scope=self.private, display_name="Rollback Failure")
        self.service.write(scope=self.private, path="remove.txt", content="remove\n")
        self.service.write(scope=self.private, path="keep.txt", content="keep\n")
        patch = """--- a/remove.txt
+++ /dev/null
@@ -1 +0,0 @@
-remove
--- a/keep.txt
+++ b/keep.txt
@@ -1 +1 @@
-keep
+changed
"""

        with mock_patch.object(self.service, "_atomic_write", side_effect=OSError("disk unavailable")):
            with self.assertRaises(ProjectWorkspaceError) as raised:
                self.service.patch(scope=self.private, patch_text=patch)

        self.assertEqual(raised.exception.reason, "rollback_failed")
        self.assertEqual(raised.exception.details["original_reason"], "write_failed")
        self.assertEqual(raised.exception.details["failed_paths"], ["remove.txt"])
        project = self.execution_root / "Projects" / created["workspace_id"]
        self.assertFalse((project / "remove.txt").exists())
        self.assertEqual((project / "keep.txt").read_text(encoding="utf-8"), "keep\n")

    def test_workspace_patch_schema_and_native_projection_expose_all_operations(self) -> None:
        from companion_v01.native_tool_schema import build_openai_native_tool_specs

        handler = WorkspacePatchToolHandler(service=self.service)
        spec = handler.tool_spec()
        item_schema = spec.output_schema["properties"]["files"]["items"]

        self.assertEqual(spec.spec_version, "1.1.0")
        self.assertEqual(spec.schema_version, 2)
        self.assertEqual(
            item_schema["properties"]["operation"]["enum"],
            ["update", "create", "delete", "rename"],
        )
        self.assertIn("source_path", item_schema["properties"])
        native = build_openai_native_tool_specs(
            {"workspace_patch": handler},
            allowed_tool_names={"workspace_patch"},
        )[0]["function"]
        self.assertEqual(native["parameters"], spec.input_schema)
        for operation in ("update", "create", "delete", "rename"):
            self.assertIn(operation, native["description"])

    def test_project_inspect_schema_and_native_projection_expose_one_read_authority(self) -> None:
        from companion_v01.native_tool_schema import build_openai_native_tool_specs

        handler = ProjectInspectToolHandler(service=self.service)
        spec = handler.tool_spec()
        native = build_openai_native_tool_specs(
            {"project_inspect": handler},
            allowed_tool_names={"project_inspect"},
        )[0]["function"]

        self.assertEqual(spec.input_schema["properties"]["action"]["enum"], ["list", "search", "read"])
        self.assertEqual(native["parameters"], spec.input_schema)
        self.assertEqual(spec.effects, ())
        self.assertEqual(spec.idempotency, "read_only")

    def test_project_inspect_handler_pages_long_lines_without_loss_and_detects_changes(self) -> None:
        self.service.create(scope=self.private, display_name="Paged Inspector")
        source = "x" * 70_000
        self.service.write(scope=self.private, path="src/minified.js", content=source)
        handler = ProjectInspectToolHandler(service=self.service)
        call = handler.normalize_call(
            {
                "type": "project_inspect",
                "action": "read",
                "path": "src/minified.js",
                "start_line": 1,
                "line_count": 1,
            }
        )
        self.assertIsNotNone(call)

        first = handler.execute(call=call, context=self._context())
        continuation = dict(first.followup_envelope.continuation or {})
        second_call = handler.normalize_call(continuation)
        self.assertFalse(first.followup_envelope.complete)
        self.assertIsNotNone(second_call)
        second = handler.execute(call=second_call, context=self._context())
        self.assertTrue(second.followup_envelope.complete)
        self.assertIn("1:49991", second.followup_context)
        first_chunk = first.followup_context.splitlines()[3].split(" | ", 1)[1]
        second_chunk = second.followup_context.splitlines()[3].split(" | ", 1)[1]
        self.assertEqual(first_chunk + second_chunk, source)

        self.service.write(scope=self.private, path="src/minified.js", content="changed\n", mode="replace")
        stale = handler.execute(call=second_call, context=self._context())
        self.assertIn("stale_cursor", stale.followup_context)

    def test_project_inspect_cursor_is_session_bound_and_does_not_embed_search_query(self) -> None:
        created = self.service.create(scope=self.private, display_name="Search Paging")
        project = self.execution_root / "Projects" / created["workspace_id"] / "src"
        project.mkdir()
        for index in range(300):
            (project / f"module_{index:03d}_{'x' * 80}.py").write_text(
                f"secret-search-phrase = {index}\n", encoding="utf-8"
            )
        handler = ProjectInspectToolHandler(service=self.service)
        call = handler.normalize_call(
            {
                "type": "project_inspect",
                "action": "search",
                "path": ".",
                "query": "secret-search-phrase",
                "include": "*.py",
            }
        )
        first = handler.execute(call=call, context=self._context())
        continuation = dict(first.followup_envelope.continuation or {})
        cursor = str(continuation.get("cursor") or "")

        self.assertFalse(first.followup_envelope.complete)
        self.assertNotIn("secret-search-phrase", bytes.fromhex(cursor.split(".", 3)[3]).decode("utf-8"))
        foreign = handler.execute(
            call=handler.normalize_call(continuation),
            context=self._context(session_id="private-other"),
        )
        self.assertIn("cursor_invalid", foreign.followup_context)

    def test_project_inspect_list_cursor_rejects_changed_result(self) -> None:
        self.service.create(scope=self.private, display_name="List Paging")
        for index in range(300):
            self.service.write(
                scope=self.private,
                path=f"src/{index:03d}_{'y' * 90}.txt",
                content="ok\n",
            )
        handler = ProjectInspectToolHandler(service=self.service)
        call = handler.normalize_call(
            {"type": "project_inspect", "action": "list", "path": ".", "max_depth": 2}
        )
        first = handler.execute(call=call, context=self._context())
        continuation = dict(first.followup_envelope.continuation or {})
        self.assertFalse(first.followup_envelope.complete)

        self.service.write(scope=self.private, path="src/new.txt", content="new\n")
        stale = handler.execute(call=handler.normalize_call(continuation), context=self._context())
        self.assertIn("stale_cursor", stale.followup_context)

    def test_model_handlers_share_the_service_contract(self) -> None:
        manage = ManageProjectWorkspaceToolHandler(service=self.service)
        write = WorkspaceWriteToolHandler(service=self.service)
        patch = WorkspacePatchToolHandler(service=self.service)
        created = manage.execute(
            call={"type": "manage_project_workspace", "action": "create", "display_name": "Tool Project"},
            context=self._context(),
        )
        workspace_id = created.state_updates["project_workspace"]["workspace_id"]
        selected = manage.execute(
            call={"type": "manage_project_workspace", "action": "select", "workspace_id": workspace_id},
            context=self._context(session_id="private-b"),
        )
        written = write.execute(
            call={"type": "workspace_write", "path": "main.js", "content": "let x = 1;\n"},
            context=self._context(session_id="private-b"),
        )
        patched = patch.execute(
            call={
                "type": "workspace_patch",
                "patch": "--- a/main.js\n+++ b/main.js\n@@ -1 +1 @@\n-let x = 1;\n+let x = 2;\n",
            },
            context=self._context(),
        )

        self.assertEqual(created.stream_events[0]["status"], "succeeded")
        self.assertEqual(selected.stream_events[0]["status"], "succeeded")
        self.assertEqual(written.stream_events[0]["status"], "succeeded")
        self.assertEqual(patched.stream_events[0]["status"], "succeeded")
        self.assertIn('"path":"main.js"', written.followup_context)

    def test_manage_handler_opens_existing_host_directory(self) -> None:
        external = self.root / "existing-project"
        external.mkdir()
        manage = ManageProjectWorkspaceToolHandler(service=self.service)
        normalized = manage.normalize_call(
            {
                "type": "manage_project_workspace",
                "action": "open",
                "path": str(external),
                "display_name": "Existing Project",
            }
        )

        self.assertIsNotNone(normalized)
        result = manage.execute(call=normalized, context=self._context())

        state = result.state_updates["project_workspace"]
        self.assertEqual(state["status"], "succeeded")
        self.assertEqual(state["display_name"], "Existing Project")
        self.assertEqual(state["root_kind"], "host_bound")
        self.assertNotIn(str(external), result.followup_context)

    def test_manage_workspace_schema_exposes_open_path_contract(self) -> None:
        spec = ManageProjectWorkspaceToolHandler(service=self.service).tool_spec()
        schema = spec.input_schema
        self.assertIn("requires display_name", spec.description)
        self.assertIn("Desktop", spec.description)
        self.assertIn("Do not use name", schema["properties"]["display_name"]["description"])

        self.assertEqual(
            schema["properties"]["action"]["enum"],
            ["list", "create", "open", "select", "archive", "current"],
        )
        self.assertIn("path", schema["properties"])

    def test_exec_alias_project_resolves_before_provider_dispatch(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        created = self.service.create(scope=self.private, display_name="Exec Project")
        other_scope = self.service.scope_for(
            profile_user_id="alice",
            session_id="private-b",
            client_mode="qq",
        )
        self.service.select(scope=other_scope, workspace_id=created["workspace_id"])
        provider = SimpleNamespace(provider_id="local")
        handler = ExecRunToolHandler(
            execution_provider=provider,
            project_workspace_service=self.service,
        )
        handler._permission_decision = lambda context, preview: SimpleNamespace(allowed=True)
        captured = {}

        def fake_execute(provider_arg, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                data={"status": "completed", "run_id": "execrun_" + "1" * 32},
                event_status="completed",
                reason="",
                model_feedback="done",
                event={"type": "capability_execution_result", "status": "completed"},
            )

        with patch("companion_v01.tool_handlers.execution.execute_exec_run", side_effect=fake_execute):
            result = handler.execute(
                call={"type": "exec_run", "command": "echo ok", "cwd": "alias:project"},
                context=self._context(session_id="private-b"),
            )

        self.assertEqual(result.stream_events[0]["status"], "completed")
        self.assertEqual(captured["cwd"], f"Projects/{created['workspace_id']}")

    def test_exec_alias_project_without_selection_is_actionable_rejection(self) -> None:
        from types import SimpleNamespace

        handler = ExecRunToolHandler(
            execution_provider=SimpleNamespace(provider_id="local"),
            project_workspace_service=self.service,
        )
        result = handler.execute(
            call={"type": "exec_run", "command": "echo ok", "cwd": "alias:project"},
            context=ToolExecutionContext(
                profile_user_id="bob",
                session_id="private-bob",
                now_ts=0,
                visual_payload={},
                client_mode="qq",
            ),
        )
        self.assertEqual(result.stream_events[0]["status"], "rejected")
        self.assertEqual(result.stream_events[0]["reason"], "workspace_not_selected")


if __name__ == "__main__":
    unittest.main()
