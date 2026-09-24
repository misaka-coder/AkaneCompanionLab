from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace

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
from companion_v01.tool_handlers.core import TaskExecutionScope


class ProjectWorkspaceServiceTests(unittest.TestCase):
    def test_task_coordinate_is_frozen_and_shared_by_file_tools_and_real_shell(self) -> None:
        original = self.service.create(scope=self.private, display_name="Parent")
        inherited = original["working_directory"]
        child = replace(self._context(session_id="subagent_" + "a" * 32),
                        execution_scope=TaskExecutionScope(inherited))
        latest = self.service.create(scope=self.private, display_name="Parent switched")
        provider = TrustedLocalExecutor(workspace_root=self.execution_root, run_log_dir=self.root / "task-logs")
        write = WorkspaceWriteToolHandler(service=self.service, execution_provider=provider)
        inspect = ProjectInspectToolHandler(service=self.service, execution_provider=provider)
        patch = WorkspacePatchToolHandler(service=self.service, execution_provider=provider)
        written = write.execute(call={"type": "workspace_write", "path": "report.txt", "content": "before\n"}, context=child)
        self.assertEqual(written.stream_events[0]["status"], "succeeded")
        patched = patch.execute(call={"type": "workspace_patch", "patch":
            "*** Begin Patch\n*** Update File: report.txt\n@@\n-before\n+after\n*** End Patch\n"}, context=child)
        self.assertEqual(patched.stream_events[0]["status"], "succeeded")
        read = inspect.execute(call={"type": "project_inspect", "action": "read", "path": "report.txt"}, context=child)
        self.assertIn("after", read.followup_context)
        override = self.root / "override"
        override.mkdir()
        write.execute(call={"type": "workspace_write", "cwd": str(override), "path": "other.txt", "content": "one-shot"}, context=child)
        write.execute(call={"type": "workspace_write", "path": "again.txt", "content": "inherited"}, context=child)
        self.assertTrue((Path(inherited) / "again.txt").is_file())
        self.assertFalse((Path(latest["working_directory"]) / "report.txt").exists())
        shell = ExecRunToolHandler(execution_provider=provider, project_workspace_service=self.service)
        from types import SimpleNamespace
        shell._permission_decision = lambda *_args: SimpleNamespace(allowed=True)
        code = "from pathlib import Path; assert Path('report.txt').read_text().strip() == 'after'"
        args = [sys.executable, "-c", code]
        command = subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)
        executed = shell.execute(call={"type": "exec_run", "command": command, "initial_wait_seconds": 2}, context=child)
        self.assertEqual(executed.stream_events[0]["status"], "completed")
        self.assertEqual(self.service.current(scope=self.private)["workspace_id"], latest["workspace_id"])

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
        self.assertEqual(
            Path(selected["working_directory"]),
            self.execution_root / "Projects" / created["workspace_id"],
        )
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

        self.assertEqual(self.service.current(scope=bob)["workspace_id"], created["workspace_id"])
        self.assertEqual(self.service.list(scope=bob)["workspaces"][0]["workspace_id"], created["workspace_id"])
        self.assertEqual(self.service.list(scope=other_group)["workspaces"][0]["workspace_id"], created["workspace_id"])
        self.assertEqual(self.service.list(scope=self.private)["workspaces"][0]["workspace_id"], created["workspace_id"])
        private_project = self.service.create(scope=self.private, display_name="Not shared")
        with self.assertRaisesRegex(ProjectWorkspaceError, "workspace_not_found"):
            self.service.select(scope=bob, workspace_id=private_project["workspace_id"])
        self.assertEqual(self.service.current(scope=alice)["workspace_id"], created["workspace_id"])
        self.assertIsNone(self.service.current(scope=other_group))

    def test_group_selection_survives_member_switch_close_and_restart(self) -> None:
        group = "qq_group_shared_100"
        scopes = [self.service.scope_for(
            profile_user_id=group, session_id=group, client_mode="qq_text",
            actor_stable_id=f"qq:{actor}", actor_profile_user_id=actor,
        ) for actor in ("alice", "bob")]
        alice, bob = scopes
        first = self.service.create(scope=alice, display_name="Group project")
        provider = TrustedLocalExecutor(workspace_root=self.execution_root, run_log_dir=self.root / "logs")
        handler = ExecRunToolHandler(execution_provider=provider, project_workspace_service=self.service)
        coordinates = [handler.working_directory_context(
            profile_user_id=group, session_id=group, client_mode="qq_text",
            actor_stable_id=actor, actor_profile_user_id=actor,
        ) for actor in ("alice", "bob")]
        self.assertEqual(coordinates, [coordinates[0]] * 2)
        self.assertEqual(coordinates[0]["workspace_id"], first["workspace_id"])
        self.assertEqual(self.service.execution_cwd(scope=bob, alias_value="alias:project"),
                         self.service.execution_cwd(scope=alice, alias_value="alias:project"))
        second = self.service.create(scope=alice, display_name="Switched")
        self.assertEqual(self.service.current(scope=bob)["workspace_id"], second["workspace_id"])
        self.service.close(scope=alice)
        self.service = ProjectWorkspaceService(store=self.store, execution_workspace_root=self.execution_root)
        for scope in scopes:
            self.assertIsNone(self.service.current(scope=scope))
        self.service.select(scope=alice, workspace_id=first["workspace_id"])
        self.assertEqual(self.service.current(scope=bob)["workspace_id"], first["workspace_id"])
        self.service.archive(scope=alice, workspace_id=first["workspace_id"])
        self.assertIsNone(self.service.current(scope=bob))

    def test_group_ignores_legacy_member_selection_even_after_close(self) -> None:
        group = "qq_group_shared_100"
        project = self.service.create(scope=self.private, display_name="Existing")
        payload = f"qq_user\x1falice\x1f\x1f{group}".encode("utf-8")
        legacy_key = "project-selection:" + hashlib.sha256(payload).hexdigest()
        self.store.set_project_workspace_selection(selection_key=legacy_key, workspace_id=project["workspace_id"])
        bob = self.service.scope_for(
            profile_user_id=group, session_id=group, client_mode="qq_text",
            actor_stable_id="qq:bob", actor_profile_user_id="bob",
        )
        self.assertIsNone(self.service.current(scope=bob))
        alice = replace(bob, owner_id="alice")
        self.service.select(scope=alice, workspace_id=project["workspace_id"])
        self.assertEqual(self.service.current(scope=bob)["workspace_id"], project["workspace_id"])
        self.service.close(scope=bob)
        self.assertIsNone(self.service.current(scope=bob))
        self.assertEqual(self.service.current(scope=self.private)["workspace_id"], project["workspace_id"])
        self.assertEqual(self.store.get_project_workspace(project["workspace_id"])["owner_id"], "alice")

    def test_shared_group_cwd_does_not_inherit_owner_execution_permission(self) -> None:
        from unittest.mock import patch
        from companion_v01.local_capability_config import save_capability_approval_modes
        group = "qq_group_shared_100"
        owner = self.service.scope_for(
            profile_user_id=group, session_id=group, client_mode="qq_text",
            actor_stable_id="qq:owner", actor_profile_user_id="master",
        )
        selected = self.service.create(scope=owner, display_name="Shared task")
        policy_root = self.root / "policies"
        for actor, mode in (("master", "trusted_auto_allow"), ("bob", "disabled")):
            save_capability_approval_modes(base_dir=policy_root, profile_user_id=actor, modes={"ops": mode})
        provider = TrustedLocalExecutor(workspace_root=self.execution_root, run_log_dir=self.root / "logs")
        handler = ExecRunToolHandler(execution_provider=provider, project_workspace_service=self.service,
                                     config_base_dir=policy_root)
        contexts = [replace(self._context(session_id=group, actor_stable_id=f"qq:{actor}",
                                         actor_profile_user_id=actor), profile_user_id=group)
                    for actor in ("master", "bob")]
        self.assertTrue(handler._permission_decision(contexts[0], {"command": "echo test"}).allowed)
        self.assertFalse(handler._permission_decision(contexts[1], {"command": "echo test"}).allowed)
        with patch("companion_v01.tool_handlers.execution.execute_exec_run") as run:
            result = handler.execute(call={"type": "exec_run", "command": "echo test"}, context=contexts[1])
            run.assert_not_called()
        self.assertEqual(result.stream_events[0]["status"], "blocked")
        self.assertEqual(self.service.current(scope=owner)["workspace_id"], selected["workspace_id"])

    def test_missing_shared_project_does_not_clear_a_newer_selection(self) -> None:
        from unittest.mock import patch
        group = self.service.scope_for(
            profile_user_id="group", session_id="qq_group_shared_100", client_mode="qq_text",
            actor_stable_id="qq:alice", actor_profile_user_id="alice",
        )
        old = self.service.create(scope=group, display_name="Old")
        new = self.service.create(scope=group, display_name="New")
        self.service.select(scope=group, workspace_id=old["workspace_id"])
        def missing(*_args, **_kwargs):
            self.store.set_project_workspace_selection(selection_key=group.selection_key, workspace_id=new["workspace_id"])
            raise ProjectWorkspaceError("workspace_not_found")
        with patch.object(self.service, "_root_from_record", side_effect=missing):
            self.assertIsNone(self.service.current(scope=group))
        self.assertEqual(self.service.current(scope=group)["workspace_id"], new["workspace_id"])

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

    def test_close_clears_only_the_conversation_default(self) -> None:
        created = self.service.create(scope=self.private, display_name="Keep Registered")

        closed = self.service.close(scope=self.private)

        self.assertTrue(closed["closed"])
        self.assertEqual(closed["previous_workspace_id"], created["workspace_id"])
        self.assertEqual(Path(closed["working_directory"]), self.execution_root.resolve())
        self.assertIsNone(self.service.current(scope=self.private))
        self.assertEqual(
            self.service.list(scope=self.private)["workspaces"][0]["workspace_id"],
            created["workspace_id"],
        )

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
        self.assertEqual(Path(bound["working_directory"]), external.resolve())
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
        self.assertEqual(Path(opened["working_directory"]), external.resolve())

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
        self.assertEqual(Path(listed["effective_cwd"]), project.resolve())

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
        patch = """*** Begin Patch
*** Update File: a.txt
@@
 alpha
-beta
+gamma
*** Update File: b.txt
@@
 one
-two
+three
*** End Patch
"""
        result = self.service.patch(scope=self.private, patch_text=patch)
        self.assertEqual([item["path"] for item in result["files"]], ["a.txt", "b.txt"])

        failing = """*** Begin Patch
*** Update File: a.txt
@@
 alpha
-gamma
+changed
*** Update File: b.txt
@@
 missing
-three
+broken
*** End Patch
"""
        with self.assertRaisesRegex(ProjectWorkspaceError, "hunk_not_applicable"):
            self.service.patch(scope=self.private, patch_text=failing)
        project = self.execution_root / "Projects" / result["workspace_id"]
        self.assertEqual((project / "a.txt").read_text(encoding="utf-8"), "alpha\ngamma\n")
        self.assertEqual((project / "b.txt").read_text(encoding="utf-8"), "one\nthree\n")

    def test_patch_locates_a_unique_hunk_without_model_supplied_line_numbers(self) -> None:
        created = self.service.create(scope=self.private, display_name="Offset Patcher")
        self.service.write(
            scope=self.private,
            workspace_id=created["workspace_id"],
            path="module.py",
            content="preface\nalpha\nbeta\ngamma\n",
        )
        patch = """*** Begin Patch
*** Update File: module.py
@@
 alpha
-beta
+changed
 gamma
*** End Patch
"""

        result = self.service.patch(
            scope=self.private,
            workspace_id=created["workspace_id"],
            patch_text=patch,
        )

        self.assertEqual(result["status"], "succeeded")
        project = self.execution_root / "Projects" / created["workspace_id"]
        self.assertEqual(
            (project / "module.py").read_text(encoding="utf-8"),
            "preface\nalpha\nchanged\ngamma\n",
        )

    def test_patch_rejects_ambiguous_relocation_and_reports_candidate_lines(self) -> None:
        created = self.service.create(scope=self.private, display_name="Ambiguous Patcher")
        self.service.write(
            scope=self.private,
            workspace_id=created["workspace_id"],
            path="module.py",
            content="header\nrepeat\nother\nrepeat\n",
        )
        patch = """*** Begin Patch
*** Update File: module.py
@@
-repeat
+changed
*** End Patch
"""

        with self.assertRaises(ProjectWorkspaceError) as raised:
            self.service.patch(
                scope=self.private,
                workspace_id=created["workspace_id"],
                patch_text=patch,
            )

        self.assertEqual(raised.exception.reason, "hunk_not_applicable")
        self.assertEqual(raised.exception.details["mismatch"], "context_ambiguous")
        self.assertEqual(raised.exception.details["candidate_lines"], [2, 4])
        project = self.execution_root / "Projects" / created["workspace_id"]
        self.assertEqual(
            (project / "module.py").read_text(encoding="utf-8"),
            "header\nrepeat\nother\nrepeat\n",
        )

    def test_patch_anchor_disambiguates_repeated_context_without_line_numbers(self) -> None:
        created = self.service.create(scope=self.private, display_name="Anchored Patcher")
        self.service.write(
            scope=self.private,
            workspace_id=created["workspace_id"],
            path="module.py",
            content="class First:\n    value = 1\nclass Second:\n    value = 1\n",
        )

        result = self.service.patch(
            scope=self.private,
            workspace_id=created["workspace_id"],
            patch_text=(
                "*** Begin Patch\n"
                "*** Update File: module.py\n"
                "@@ class Second:\n"
                "-    value = 1\n"
                "+    value = 2\n"
                "*** End Patch\n"
            ),
        )

        self.assertEqual(result["status"], "succeeded")
        project = self.execution_root / "Projects" / created["workspace_id"]
        self.assertEqual(
            (project / "module.py").read_text(encoding="utf-8"),
            "class First:\n    value = 1\nclass Second:\n    value = 2\n",
        )

    def test_patch_rejects_traditional_unified_diff_with_actionable_format_feedback(self) -> None:
        created = self.service.create(scope=self.private, display_name="Format Feedback")
        self.service.write(
            scope=self.private,
            workspace_id=created["workspace_id"],
            path="module.py",
            content="value = 1\n",
        )

        with self.assertRaises(ProjectWorkspaceError) as raised:
            self.service.patch(
                scope=self.private,
                workspace_id=created["workspace_id"],
                patch_text=(
                    "--- a/module.py\n+++ b/module.py\n@@ -1 +1 @@\n"
                    "-value = 1\n+value = 2\n"
                ),
            )

        self.assertEqual(raised.exception.reason, "patch_format_invalid")
        self.assertEqual(
            raised.exception.details["recommended_action"],
            "use_the_count_free_workspace_patch_format",
        )

    def test_patch_syntax_error_reports_the_bad_line_and_expected_prefixes(self) -> None:
        self.service.create(scope=self.private, display_name="Syntax Feedback")
        self.service.write(scope=self.private, path="module.py", content="value = 1\n")
        handler = WorkspacePatchToolHandler(service=self.service)

        result = handler.execute(
            call={
                "type": "workspace_patch",
                "patch": (
                    "*** Begin Patch\n*** Update File: module.py\n@@\n"
                    "value = 1\n+value = 2\n*** End Patch\n"
                ),
            },
            context=self._context(),
        )

        self.assertEqual(result.stream_events[0]["status"], "rejected")
        self.assertIn("patch_line_prefix_invalid", result.followup_context)
        self.assertIn("space for context, - for removed text, or + for added text", result.followup_context)

    def test_patch_rejects_numeric_hunk_ranges_inside_the_new_envelope(self) -> None:
        created = self.service.create(scope=self.private, display_name="Range Feedback")
        self.service.write(
            scope=self.private,
            workspace_id=created["workspace_id"],
            path="module.py",
            content="value = 1\n",
        )

        with self.assertRaises(ProjectWorkspaceError) as raised:
            self.service.patch(
                scope=self.private,
                workspace_id=created["workspace_id"],
                patch_text=(
                    "*** Begin Patch\n*** Update File: module.py\n@@ -1 +1 @@\n"
                    "-value = 1\n+value = 2\n*** End Patch\n"
                ),
            )

        self.assertEqual(raised.exception.reason, "patch_numeric_range_not_allowed")
        self.assertEqual(
            raised.exception.details["recommended_action"],
            "remove_the_old_and_new_line_ranges_from_the_hunk_header",
        )

    def test_patch_anchor_can_insert_immediately_after_an_existing_line(self) -> None:
        created = self.service.create(scope=self.private, display_name="Anchored Insert")
        self.service.write(
            scope=self.private,
            workspace_id=created["workspace_id"],
            path="module.py",
            content="class Demo:\n    tail = True\n",
        )

        self.service.patch(
            scope=self.private,
            workspace_id=created["workspace_id"],
            patch_text=(
                "*** Begin Patch\n*** Update File: module.py\n@@ class Demo:\n"
                "+    value = 1\n*** End Patch\n"
            ),
        )

        project = self.execution_root / "Projects" / created["workspace_id"]
        self.assertEqual(
            (project / "module.py").read_text(encoding="utf-8"),
            "class Demo:\n    value = 1\n    tail = True\n",
        )

    def test_patch_atomically_creates_deletes_and_renames_utf8_files(self) -> None:
        created = self.service.create(scope=self.private, display_name="Full Patch")
        self.service.write(scope=self.private, path="old.txt", content="old\n")
        self.service.write(scope=self.private, path="remove.txt", content="remove\n")
        patch = """*** Begin Patch
*** Add File: new.txt
+new
*** Update File: old.txt
*** Move to: moved.txt
@@
-old
+moved
*** Delete File: remove.txt
*** End Patch
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
            patch_text=(
                "*** Begin Patch\n"
                "*** Update File: before.txt\n"
                "*** Move to: nested/after.txt\n"
                "*** End Patch\n"
            ),
        )

        project = self.execution_root / "Projects" / created["workspace_id"]
        self.assertFalse((project / "before.txt").exists())
        self.assertEqual((project / "nested" / "after.txt").read_text(encoding="utf-8"), "same\n")
        self.assertEqual(result["files"][0]["operation"], "rename")

    def test_patch_target_conflict_leaves_every_file_unchanged(self) -> None:
        created = self.service.create(scope=self.private, display_name="Conflict Patch")
        self.service.write(scope=self.private, path="a.txt", content="a\n")
        self.service.write(scope=self.private, path="occupied.txt", content="occupied\n")
        patch = """*** Begin Patch
*** Update File: a.txt
@@
-a
+changed
*** Update File: a.txt
*** Move to: occupied.txt
@@
-a
+moved
*** End Patch
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
        patch = """*** Begin Patch
*** Delete File: remove.txt
*** Add File: generated/new.txt
+new
*** Update File: keep.txt
@@
-keep
+changed
*** End Patch
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
        patch = """*** Begin Patch
*** Delete File: remove.txt
*** Update File: keep.txt
@@
-keep
+changed
*** End Patch
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

        self.assertEqual(spec.spec_version, "2.2.0")
        self.assertEqual(spec.schema_version, 5)
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
        self.assertIn("Do not calculate unified-diff line ranges", native["description"])
        self.assertIn("*** Begin Patch", native["parameters"]["properties"]["patch"]["description"])

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

    def test_unregistered_default_root_continuation_is_pinned_to_its_real_cwd(self) -> None:
        self.execution_root.mkdir(parents=True, exist_ok=True)
        (self.execution_root / "large.txt").write_text("z" * 70_000, encoding="utf-8")
        provider = TrustedLocalExecutor(
            workspace_root=self.execution_root,
            run_log_dir=self.root / "runlogs-paged-root",
        )
        handler = ProjectInspectToolHandler(service=self.service, execution_provider=provider)
        first = handler.execute(
            call={
                "type": "project_inspect",
                "action": "read",
                "path": "large.txt",
                "start_line": 1,
                "line_count": 1,
            },
            context=self._context(),
        )
        continuation = dict(first.followup_envelope.continuation or {})

        self.assertEqual(Path(continuation["cwd"]), self.execution_root.resolve())
        self.service.create(scope=self.private, display_name="Later Selection")
        second = handler.execute(call=handler.normalize_call(continuation), context=self._context())
        self.assertTrue(second.followup_envelope.complete)
        self.assertNotIn("stale_cursor", second.followup_context)

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
                "patch": (
                    "*** Begin Patch\n*** Update File: main.js\n@@\n"
                    "-let x = 1;\n+let x = 2;\n*** End Patch\n"
                ),
            },
            context=self._context(),
        )

        self.assertEqual(created.stream_events[0]["status"], "succeeded")
        self.assertEqual(selected.stream_events[0]["status"], "succeeded")
        self.assertEqual(written.stream_events[0]["status"], "succeeded")
        self.assertEqual(patched.stream_events[0]["status"], "succeeded")
        self.assertIn('"path":"main.js"', written.followup_context)

    def test_file_tools_share_unregistered_absolute_cwd_with_shell(self) -> None:
        external = self.root / "unregistered project"
        external.mkdir()
        provider = TrustedLocalExecutor(
            workspace_root=self.execution_root,
            run_log_dir=self.root / "runlogs-direct",
        )
        write = WorkspaceWriteToolHandler(service=self.service, execution_provider=provider)
        inspect = ProjectInspectToolHandler(service=self.service, execution_provider=provider)
        patch_handler = WorkspacePatchToolHandler(service=self.service, execution_provider=provider)

        written = write.execute(
            call={
                "type": "workspace_write",
                "cwd": str(external),
                "path": "src/main.py",
                "content": "value = 1\n",
            },
            context=self._context(),
        )
        read = inspect.execute(
            call={
                "type": "project_inspect",
                "action": "read",
                "cwd": str(external),
                "path": "src/main.py",
                "start_line": 1,
                "line_count": 20,
            },
            context=self._context(),
        )
        patched = patch_handler.execute(
            call={
                "type": "workspace_patch",
                "cwd": str(external),
                "patch": (
                    "*** Begin Patch\n*** Update File: src/main.py\n@@\n"
                    "-value = 1\n+value = 2\n*** End Patch\n"
                ),
            },
            context=self._context(),
        )
        exec_handler = ExecRunToolHandler(execution_provider=provider)
        exec_handler._permission_decision = lambda context, preview: type(
            "Decision", (), {"allowed": True}
        )()
        code = "from pathlib import Path; assert Path('src/main.py').read_text(encoding='utf-8') == 'value = 2\\n'"
        command = (
            subprocess.list2cmdline([sys.executable, "-c", code])
            if os.name == "nt"
            else shlex.join([sys.executable, "-c", code])
        )
        executed = exec_handler.execute(
            call={
                "type": "exec_run",
                "cwd": str(external),
                "command": command,
                "initial_wait_seconds": 2,
            },
            context=self._context(),
        )

        self.assertEqual(written.stream_events[0]["status"], "succeeded")
        self.assertIn("value = 1", read.followup_context)
        self.assertEqual(patched.stream_events[0]["status"], "succeeded")
        self.assertEqual(executed.stream_events[0]["status"], "completed")
        self.assertEqual((external / "src" / "main.py").read_text(encoding="utf-8"), "value = 2\n")
        self.assertNotIn("workspace_not_selected", written.followup_context)

    def test_workspace_write_accepts_absolute_file_path_without_project_registration(self) -> None:
        external = self.root / "absolute-project"
        external.mkdir()
        provider = TrustedLocalExecutor(
            workspace_root=self.execution_root,
            run_log_dir=self.root / "runlogs-absolute",
        )
        handler = WorkspaceWriteToolHandler(service=self.service, execution_provider=provider)

        result = handler.execute(
            call={
                "type": "workspace_write",
                "path": str(external / "hello.txt"),
                "content": "hello\n",
            },
            context=self._context(),
        )

        self.assertEqual(result.stream_events[0]["status"], "succeeded")
        self.assertEqual((external / "hello.txt").read_text(encoding="utf-8"), "hello\n")

    def test_file_tools_default_to_execution_root_when_no_project_is_selected(self) -> None:
        provider = TrustedLocalExecutor(
            workspace_root=self.execution_root,
            run_log_dir=self.root / "runlogs-default",
        )
        handler = WorkspaceWriteToolHandler(service=self.service, execution_provider=provider)

        result = handler.execute(
            call={"type": "workspace_write", "path": "default.txt", "content": "ok\n"},
            context=self._context(),
        )

        self.assertEqual(result.stream_events[0]["status"], "succeeded")
        self.assertEqual((self.execution_root / "default.txt").read_text(encoding="utf-8"), "ok\n")
        self.assertEqual(
            Path(result.state_updates["project_workspace"]["effective_cwd"]),
            self.execution_root.resolve(),
        )

    def test_file_tools_default_to_current_selected_project(self) -> None:
        created = self.service.create(scope=self.private, display_name="Current Project")
        provider = TrustedLocalExecutor(
            workspace_root=self.execution_root,
            run_log_dir=self.root / "runlogs-default-selected",
        )
        handler = WorkspaceWriteToolHandler(service=self.service, execution_provider=provider)

        result = handler.execute(
            call={"type": "workspace_write", "path": "current-task.txt", "content": "same cwd\n"},
            context=self._context(),
        )

        state = result.state_updates["project_workspace"]
        project_file = self.execution_root / "Projects" / created["workspace_id"] / "current-task.txt"
        self.assertEqual(result.stream_events[0]["status"], "succeeded")
        self.assertEqual(state["workspace_id"], created["workspace_id"])
        self.assertEqual(Path(state["effective_cwd"]), project_file.parent.resolve())
        self.assertTrue(project_file.is_file())
        self.assertFalse((self.execution_root / "current-task.txt").exists())

    def test_direct_file_tools_do_not_require_group_actor_identity(self) -> None:
        provider = TrustedLocalExecutor(
            workspace_root=self.execution_root,
            run_log_dir=self.root / "runlogs-group-direct",
        )
        context = self._context(session_id="qq_group_shared_872732158")

        written = WorkspaceWriteToolHandler(service=self.service, execution_provider=provider).execute(
            call={"type": "workspace_write", "path": "group-task/source.py", "content": "Token = LexToken\n"},
            context=context,
        )
        inspected = ProjectInspectToolHandler(service=self.service, execution_provider=provider).execute(
            call={"type": "project_inspect", "action": "read", "path": "group-task/source.py"},
            context=context,
        )
        patched = WorkspacePatchToolHandler(service=self.service, execution_provider=provider).execute(
            call={
                "type": "workspace_patch",
                "patch": (
                    "*** Begin Patch\n"
                    "*** Update File: group-task/source.py\n"
                    "@@\n"
                    "-Token = LexToken\n"
                    "+Token = ParserToken\n"
                    "*** End Patch\n"
                ),
            },
            context=context,
        )

        self.assertEqual(written.stream_events[0]["status"], "succeeded")
        self.assertEqual(inspected.stream_events[0]["status"], "succeeded")
        self.assertIn("Token = LexToken", inspected.followup_context)
        self.assertEqual(patched.stream_events[0]["status"], "succeeded")
        self.assertEqual((self.execution_root / "group-task" / "source.py").read_text(encoding="utf-8"), "Token = ParserToken\n")

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
        self.assertEqual(Path(state["working_directory"]), external.resolve())

    def test_manage_workspace_schema_exposes_open_path_contract(self) -> None:
        spec = ManageProjectWorkspaceToolHandler(service=self.service).tool_spec()
        schema = spec.input_schema
        self.assertIn("requires display_name", spec.description)
        self.assertIn("Desktop", spec.description)
        self.assertIn("Do not use name", schema["properties"]["display_name"]["description"])

        self.assertEqual(
            schema["properties"]["action"]["enum"],
            ["list", "create", "open", "select", "archive", "current", "close"],
        )
        self.assertIn("path", schema["properties"])
        self.assertIn("working_directory", spec.output_schema["properties"])
        self.assertNotIn("effective_cwd", spec.output_schema["properties"])

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

    def test_exec_run_omitted_cwd_uses_current_project_and_explicit_cwd_is_one_shot(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        created = self.service.create(scope=self.private, display_name="Default Exec Project")
        provider = SimpleNamespace(provider_id="local")
        handler = ExecRunToolHandler(
            execution_provider=provider,
            project_workspace_service=self.service,
        )
        handler._permission_decision = lambda context, preview: SimpleNamespace(allowed=True)
        captured: list[str] = []

        def fake_execute(provider_arg, **kwargs):
            captured.append(str(kwargs.get("cwd") or ""))
            return SimpleNamespace(
                data={"status": "completed", "run_id": "execrun_" + str(len(captured)) * 32},
                event_status="completed",
                reason="",
                model_feedback="done",
                event={"type": "capability_execution_result", "status": "completed"},
            )

        with patch("companion_v01.tool_handlers.execution.execute_exec_run", side_effect=fake_execute):
            handler.execute(
                call={"type": "exec_run", "command": "echo default"},
                context=self._context(),
            )
            handler.execute(
                call={"type": "exec_run", "command": "echo override", "cwd": "scratch"},
                context=self._context(),
            )
            handler.execute(
                call={"type": "exec_run", "command": "echo default-again"},
                context=self._context(),
            )

        project_cwd = f"Projects/{created['workspace_id']}"
        self.assertEqual(captured, [project_cwd, "scratch", project_cwd])

    def test_exec_run_input_resources_do_not_inherit_current_project(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        self.service.create(scope=self.private, display_name="Resource Isolation")
        handler = ExecRunToolHandler(
            execution_provider=SimpleNamespace(provider_id="local"),
            project_workspace_service=self.service,
        )
        handler._permission_decision = lambda context, preview: SimpleNamespace(allowed=True)

        with patch.object(self.service, "execution_cwd") as resolve_project:
            result = handler.execute(
                call={
                    "type": "exec_run",
                    "command": "echo staged",
                    "input_resources": [{"handle": "file_001", "as": "input.txt"}],
                },
                context=self._context(),
            )

        resolve_project.assert_not_called()
        self.assertEqual(result.stream_events[0]["reason"], "execution_resources_unconfigured")

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
