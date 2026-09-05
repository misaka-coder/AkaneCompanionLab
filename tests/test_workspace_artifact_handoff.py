from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from companion_v01.project_workspace import ProjectWorkspaceService
from companion_v01.tool_handlers.core import TaskExecutionScope, ToolExecutionContext
from companion_v01.tool_handlers.generated_media import ManageGeneratedFileToolHandler
from companion_v01.execution_run import new_run_id
from tests.test_execution_resources import _Harness, OWNER, RESOURCE_SCOPE


class WorkspaceArtifactHandoffTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.h = _Harness(Path(temp.name))
        self.service = ProjectWorkspaceService(store=self.h.store, execution_workspace_root=self.h.workspace)
        self.scope = self.service.scope_for(profile_user_id="alice", session_id="s1", client_mode="desktop_pet")
        self.project = Path(temp.name) / "external-project"
        self.project.mkdir()
        self.service.bind_existing(scope=self.scope, host_directory=str(self.project))
        self.handler = ManageGeneratedFileToolHandler(generated_file_service=self.h.generated_service,
                                                       project_workspace_service=self.service)
        self.context = ToolExecutionContext(profile_user_id="alice", session_id="s1", now_ts=123,
            visual_payload={}, client_mode="desktop_pet", execution_scope=TaskExecutionScope(str(self.project), "child-a"))

    def test_child_existing_bytes_are_registered_once_per_call_and_parent_resolves_same_hash(self):
        data = b"# report\r\nunchanged bytes\r\n"
        (self.project / "report.md").write_bytes(data)
        call = self.handler.normalize_call({"type": "manage_generated_file", "action": "register", "path": "report.md"})
        result = self.handler.execute(call=call, context=self.context)
        receipt = json.loads(result.followup_context)
        self.assertEqual(receipt["sha256"], hashlib.sha256(data).hexdigest())
        generated = self.h.generated_service.resolve_generated_artifact(profile_user_id="alice", session_id="s1", target=receipt["handle"])
        self.assertEqual(Path(generated["absolute_path"]).read_bytes(), data)
        self.assertEqual((self.project / "report.md").read_bytes(), data)
        self.assertNotIn(str(self.h.managed), result.followup_context)
        from companion_v01.host_tool_jobs import _artifact_references
        from companion_v01.subagent_runtime import _normalize_artifacts
        self.assertEqual(_normalize_artifacts(_artifact_references(result))[0]["sha256"], receipt["sha256"])
        self.assertIsNone(self.h.generated_service.resolve_generated_artifact(profile_user_id="bob", session_id="s1", target=receipt["handle"]))

    def test_registration_is_visible_before_the_first_generated_artifact(self):
        from tests.test_capability_adapter_mcp_orchestration import build_engine
        from companion_v01.tool_handlers.execution import ExecRunToolHandler
        from companion_v01.execution_local import TrustedLocalExecutor
        from companion_v01.subagent_policy import task_capability_selection
        engine = build_engine(self.h.root)
        provider = TrustedLocalExecutor(workspace_root=self.h.workspace, run_log_dir=self.h.root / "runlogs")
        engine.execution_provider = provider
        engine.store = self.h.store
        engine.tool_handlers.update({"manage_generated_file": self.handler,
            "exec_run": ExecRunToolHandler(execution_provider=provider, project_workspace_service=self.service)})
        selection = engine._resolve_capability_selection(
            client_context=engine._resolve_client_protocol_context({"client_mode": "desktop_pet"}),
            profile_user_id="alice", session_id="s1")
        self.assertIn("manage_generated_file", selection.schema_tool_names)
        self.assertIn("manage_generated_file", task_capability_selection(selection).schema_tool_names)

    def test_unregistered_external_project_and_other_owner_rejected(self):
        (self.project / "report.md").write_text("report")
        result = self.handler.execute(call={"action": "register", "path": str(self.project / "report.md")},
            context=replace(self.context, profile_user_id="bob", execution_scope=None))
        self.assertIn("output_cwd_not_registered", result.followup_context)

    def test_missing_directory_and_traversal_are_structured_errors(self):
        for path in (str(self.project / "missing" / "file.md"), "../outside.md"):
            result = self.handler.execute(call={"action": "register", "path": path}, context=self.context)
            self.assertIn("<tool_use_error>", result.followup_context)
            self.assertEqual(result.state_updates, {})

    def test_symlink_source_is_not_registered(self):
        original = self.project / "original.md"
        original.write_text("original")
        link = self.project / "link.md"
        try:
            link.symlink_to(original)
        except OSError:
            self.skipTest("symlink creation unavailable on this host")
        result = self.handler.execute(call={"action": "register", "path": "link.md"}, context=self.context)
        self.assertIn("<tool_use_error>", result.followup_context)
        matches, _ = self.h.bridge._collect_output_matches(["link.md"], self.project)
        self.assertEqual(matches, [])

    def test_external_shell_outputs_require_authority_and_keep_project(self):
        run_id = new_run_id()
        denied = self.h.bridge.bind_workspace_outputs(run_id=run_id, owner=OWNER, resource_scope=RESOURCE_SCOPE,
            cwd=str(self.project), output_globs=["*.md"])
        self.assertFalse(denied["ok"])
        authorized = self.service.output_directory(scope=self.scope, cwd=self.project)
        result = self.h.bridge.bind_workspace_outputs(run_id=run_id, owner=OWNER, resource_scope=RESOURCE_SCOPE,
            cwd=str(self.project), output_globs=["*.md"], authorized_root=authorized)
        self.assertTrue(result["ok"], result)
        (self.project / "report.md").write_bytes(b"source")
        registered = self.h.bridge.register_outputs(run_id=run_id, owner=OWNER)
        self.assertTrue(registered["ok"], registered)
        self.assertEqual(registered["generated_resources"][0]["sha256"], hashlib.sha256(b"source").hexdigest())
        self.assertEqual(self.h.bridge.register_outputs(run_id=run_id, owner=OWNER), registered)
        self.assertTrue((self.project / "report.md").exists())

    def test_real_child_shell_returns_id_and_completion_retains_original_artifact(self):
        from unittest.mock import patch
        from capcore import PermissionDecision
        from companion_v01.execution_local import TrustedLocalExecutor
        from companion_v01.tool_handlers.execution import ExecRunToolHandler
        from companion_v01.task_work import TaskWork
        from tests.test_execution_resources import _python_command
        import time
        provider = TrustedLocalExecutor(workspace_root=self.h.workspace, run_log_dir=self.h.root / "runlogs")
        self.addCleanup(provider.request_shutdown)
        handler = ExecRunToolHandler(execution_provider=provider, resource_bridge=self.h.bridge,
                                     project_workspace_service=self.service)
        work = TaskWork()
        context = replace(self.context, execution_scope=TaskExecutionScope(str(self.project), "child-a", work))
        with patch("companion_v01.tool_handlers.execution.resolve_permission_for_profile",
                   return_value=PermissionDecision(allowed=True, requires_user_decision=False,
                                                   mode="trusted_auto_allow", reason="test")):
            result = handler.execute(call={"type": "exec_run", "initial_wait_seconds": 0,
                "command": _python_command("import time,pathlib\ndeadline=time.monotonic()+20\n"
                    "while not pathlib.Path('independent.txt').exists() and time.monotonic()<deadline: time.sleep(0.05)\n"
                    "pathlib.Path('shell.md').write_bytes(b'original')"),
                "output_globs": ["shell.md"]}, context=context)
        self.assertEqual(result.state_updates["capability_execution"]["status"], "running", result)
        self.assertTrue(work.pending)
        # Independent foreground work remains possible after admission.
        (self.project / "independent.txt").write_text("done")
        deadline = time.monotonic() + 15
        completed = work.wait(lambda: time.monotonic() > deadline)
        self.assertTrue(completed)
        self.assertEqual(completed[0]["status"], "completed", completed)
        self.assertNotIn("send_file", completed[0]["summary"])
        receipt = completed[0]["artifacts"][0]
        self.assertEqual(receipt["sha256"], hashlib.sha256(b"original").hexdigest())
        output = self.h.generated_service.resolve_generated_artifact(profile_user_id="alice", session_id="s1", target=receipt["handle"])
        self.assertEqual(Path(output["absolute_path"]).read_bytes(), b"original")

        # Explicit status receipt consumes ownership without a duplicate wake.
        from companion_v01.tool_handlers.execution import ExecStatusToolHandler
        run_id = result.state_updates["capability_execution"]["run_id"]
        work.track(run_id, inspect=lambda: {"status": "completed"}, cancel=lambda: None)
        status_handler = ExecStatusToolHandler(execution_provider=provider, resource_bridge=self.h.bridge)
        status = status_handler.execute(call={"run_id": run_id}, context=context)
        self.assertEqual(status.state_updates["capability_execution"]["status"], "completed")
        self.assertEqual(work.collect(), [])


if __name__ == "__main__":
    unittest.main()
