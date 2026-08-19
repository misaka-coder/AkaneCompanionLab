from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from companion_v01.project_workspace import ProjectWorkspaceError, ProjectWorkspaceService
from companion_v01.store import MemoryStore
from companion_v01.tool_handlers.execution import ExecRunToolHandler
from companion_v01.tool_handlers.project_workspace import (
    ManageProjectWorkspaceToolHandler,
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
    def _context(*, session_id: str = "private-a", actor_stable_id: str = "") -> ToolExecutionContext:
        return ToolExecutionContext(
            profile_user_id="alice",
            session_id=session_id,
            now_ts=0,
            visual_payload={},
            client_mode="qq",
            request_context={"actor_stable_id": actor_stable_id} if actor_stable_id else {},
        )

    def test_create_select_and_cross_conversation_scope(self) -> None:
        created = self.service.create(scope=self.private, display_name="Demo Project")
        other_session = self.service.scope_for(
            profile_user_id="alice",
            session_id="private-b",
            client_mode="qq",
        )

        current = self.service.current(scope=other_session)

        self.assertEqual(current["workspace_id"], created["workspace_id"])
        self.assertEqual(current["alias"], "alias:project")
        self.assertNotIn(str(self.execution_root), str(current))
        cwd = self.service.execution_cwd(scope=other_session, alias_value="alias:project")
        self.assertEqual(cwd, f"Projects/{created['workspace_id']}")

    def test_group_scope_requires_actor_and_isolates_members_and_groups(self) -> None:
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
        )
        bob = self.service.scope_for(
            profile_user_id="group-profile",
            session_id="qq_group_shared_100",
            client_mode="qq",
            actor_stable_id="qq:2",
        )
        other_group = self.service.scope_for(
            profile_user_id="group-profile",
            session_id="qq_group_shared_200",
            client_mode="qq",
            actor_stable_id="qq:1",
        )
        created = self.service.create(scope=alice, display_name="Alice Project")

        self.assertEqual(self.service.list(scope=bob)["workspaces"], [])
        self.assertEqual(self.service.list(scope=other_group)["workspaces"], [])
        for scope in (bob, other_group):
            with self.assertRaisesRegex(ProjectWorkspaceError, "workspace_not_found"):
                self.service.select(scope=scope, workspace_id=created["workspace_id"])

    def test_archive_clears_selection_without_deleting_files(self) -> None:
        created = self.service.create(scope=self.private, display_name="Keep Files")
        self.service.write(scope=self.private, path="src/main.js", content="console.log('ok');\n")

        archived = self.service.archive(scope=self.private, workspace_id=created["workspace_id"])

        self.assertEqual(archived["state"], "archived")
        self.assertIsNone(self.service.current(scope=self.private))
        physical = self.execution_root / "Projects" / created["workspace_id"] / "src" / "main.js"
        self.assertTrue(physical.is_file())

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

    def test_model_handlers_share_the_service_contract(self) -> None:
        manage = ManageProjectWorkspaceToolHandler(service=self.service)
        write = WorkspaceWriteToolHandler(service=self.service)
        patch = WorkspacePatchToolHandler(service=self.service)
        created = manage.execute(
            call={"type": "manage_project_workspace", "action": "create", "display_name": "Tool Project"},
            context=self._context(),
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
        self.assertEqual(written.stream_events[0]["status"], "succeeded")
        self.assertEqual(patched.stream_events[0]["status"], "succeeded")
        self.assertIn('"path":"main.js"', written.followup_context)

    def test_exec_alias_project_resolves_before_provider_dispatch(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        created = self.service.create(scope=self.private, display_name="Exec Project")
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
