"""Authorized path -> immutable artifact -> QQ transport, with no live sends."""

from dataclasses import replace
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from companion_v01.tool_handlers.generated_media import SendFileToolHandler
from companion_v01.qq_gateway import NapCatQQGateway, QQMessageContext
from tests import test_workspace_artifact_handoff as workspace_tests


class FileDeliveryPathTests(unittest.TestCase):
    setUp = workspace_tests.WorkspaceArtifactHandoffTests.setUp

    def sender(self):
        return SendFileToolHandler(generated_file_service=self.h.generated_service,
                                  project_workspace_service=self.service)

    def test_formats_keep_bytes_and_use_one_qq_transport(self):
        # These fixtures test byte transport, not the formats' decoders.
        for extension in ("md", "pdf", "docx", "xlsx", "pptx", "zip", "gz", "akbin",
                          "png", "jpg", "webp", "svg", "tiff", "psd", "mp3", "wav", "mp4", "mkv"):
            with self.subTest(extension=extension):
                original = b"transport fixture\x00\xff\r\n" + extension.encode()
                name = "report." + extension
                (self.project / name).write_bytes(original)
                sender = self.sender()
                call = sender.normalize_call({"type": "send_file", "path": name})
                result = sender.execute(call=call, context=self.context)
                receipt = result.stream_events[0]["generated_file"]
                self.assertEqual(receipt["sha256"], hashlib.sha256(original).hexdigest())
                events = [e for e in result.stream_events if e["type"] == "file_ready"]
                self.assertEqual(len(events), 1, result)
                file = events[0]["file"]
                self.assertEqual(Path(file["absolute_path"]).read_bytes(), original)
                self.assertIn("投递队列", result.followup_context)
                self.assertIn("不能说", result.followup_context)
                self.assertNotIn(str(self.project), result.followup_context)
                gateway = NapCatQQGateway()
                # One original event plus a duplicate stream replay, routed locally.
                events[0]["client_mode"] = "qq_text"
                generated_event = {"type": "generated_file_ready", "generated_file": file["generated_file"],
                                   "send_to_user": True, "client_mode": "qq_text"}
                for delivery_events in (events + events, [generated_event, generated_event]):
                    with (patch.object(gateway, "send_file", return_value={"ok": True}) as send,
                          patch.object(gateway, "send_image", return_value={"ok": True}) as image,
                          patch.object(gateway, "send_voice", return_value={"ok": True}) as voice):
                        delivered = gateway.send_generated_files(
                            QQMessageContext(should_respond=True, reason="test", is_group=True,
                                             group_id=10002, target_id=10002), delivery_events)
                    self.assertTrue(delivered["ok"], delivered)
                    native_image = extension in {"png", "jpg", "webp"}
                    self.assertEqual(image.call_count, int(native_image))
                    self.assertEqual(send.call_count, int(not native_image))
                    self.assertEqual(voice.call_count, 0)

    def test_exact_handle_resends_without_registering_again(self):
        (self.project / "report.md").write_bytes(b"original")
        sender = self.sender()
        first = sender.execute(call={"path": "report.md"}, context=self.context)
        handle = first.state_updates["generated_file_handles"][0]
        (self.project / "report.md").write_bytes(b"later edit")
        with patch.object(self.h.generated_service, "register_workspace_artifact", side_effect=AssertionError):
            second = sender.execute(call={"target": handle}, context=self.context)
        self.assertEqual(Path(second.stream_events[0]["file"]["absolute_path"]).read_bytes(), b"original")
        self.assertEqual(second.state_updates, {})
        other = sender.execute(call={"target": handle}, context=replace(self.context, session_id="other"))
        self.assertFalse(any(e["type"] == "file_ready" for e in other.stream_events))

    def test_selected_project_alias_exports_without_absolute_path_or_extra_registration(self):
        (self.project / "exports").mkdir()
        (self.project / "exports" / "report.md").write_bytes(b"original exported note")
        for path, cwd in (("exports/report.md", "alias:project"), ("report.md", "alias:project/exports")):
            with self.subTest(cwd=cwd):
                result = self.sender().execute(call={"path": path, "cwd": cwd}, context=self.context)
                file_event = next(e for e in result.stream_events if e["type"] == "file_ready")
                self.assertEqual(Path(file_event["file"]["absolute_path"]).read_bytes(), b"original exported note")
        denied = self.sender().execute(call={"path": "report.md", "cwd": "alias:project/../"}, context=self.context)
        self.assertFalse(any(e["type"] == "file_ready" for e in denied.stream_events))

    def test_absolute_authorized_path_does_not_need_selected_project(self):
        source = self.project / "report.md"
        source.write_bytes(b"exact file")
        result = self.sender().execute(call={"path": str(source)},
            context=replace(self.context, execution_scope=None))
        self.assertTrue(result.state_updates["generated_file_handles"])

    def test_group_path_uses_verified_actor_project_and_group_artifact_scope(self):
        context = replace(self.context, client_mode="qq_text", profile_user_id="group",
                          session_id="qq_group_shared_10002", execution_scope=None,
                          request_context={"actor_stable_id": "qq:10003", "actor_profile_user_id": "alice"})
        scope = self.service.scope_for(profile_user_id=context.profile_user_id, session_id=context.session_id,
            client_mode=context.client_mode, **context.request_context)
        self.service.bind_existing(scope=scope, host_directory=str(self.project))
        (self.project / "group.md").write_bytes(b"group requested file")
        result = self.sender().execute(call={"path": "group.md"}, context=context)
        handle = result.state_updates["generated_file_handles"][0]
        self.assertIsNotNone(self.h.generated_service.resolve_generated_artifact(profile_user_id="group",
            session_id=context.session_id, target=handle))
        self.assertIsNone(self.h.generated_service.resolve_generated_artifact(profile_user_id="alice",
            session_id="private", target=handle))
        denied = self.sender().execute(call={"path": str(self.project / "group.md")},
            context=replace(context, request_context={"actor_stable_id": "qq:10004", "actor_profile_user_id": "bob"}))
        self.assertIn("output_cwd_not_registered", denied.followup_context)

    def test_failure_never_sends_old_latest_or_exposes_host_path(self):
        (self.project / "old.md").write_bytes(b"unrelated")
        self.sender().execute(call={"path": "old.md"}, context=self.context)
        (self.project / "empty.md").touch()
        (self.project / "README").write_bytes(b"no extension")
        for path, reason in (("missing.md", "source_missing"), ("empty.md", "artifact_source_empty"),
                             ("README", "artifact_source_extension_unsupported"), ("../outside.md", "outside")):
            with self.subTest(path=path), patch.object(self.h.generated_service, "send_file") as send:
                result = self.sender().execute(call={"path": path}, context=self.context)
                send.assert_not_called()
                self.assertEqual(result.state_updates, {})
                self.assertEqual(result.stream_events[0]["status"], "failed")
                self.assertNotIn(str(self.project), result.followup_context)
                if reason != "outside":
                    self.assertIn(reason, result.followup_context)

    def test_scope_conflicts_and_path_misuse_are_actionable(self):
        source = self.project / "report.md"
        source.write_bytes(b"file")
        sender = self.sender()
        for call in ({"path": str(source), "cwd": str(self.project)},
                     {"path": str(source), "target": "latest"}):
            with patch.object(self.h.generated_service, "send_file") as send:
                result = sender.execute(call=call, context=self.context)
                send.assert_not_called()
                self.assertIn("conflict", result.followup_context)
        denied = sender.execute(call={"path": str(source)},
            context=replace(self.context, profile_user_id="bob", execution_scope=None))
        self.assertIn("output_cwd_not_registered", denied.followup_context)
        wrong_arg = sender.execute(call={"target": "exports/report.md"}, context=self.context)
        self.assertIn("send_file(path=", wrong_arg.followup_context)
        for call in ({"path": ""}, {"path": "a.md", "targets": []}, {"cwd": "dir"}):
            self.assertIsNone(sender.normalize_call({"type": "send_file", **call}))


if __name__ == "__main__":
    unittest.main()
