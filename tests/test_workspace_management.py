from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.capability_registry import MANAGE_GENERATED_FILE_TOOL_SPEC
from companion_v01.generated_files import GeneratedFileService
from companion_v01.native_tool_schema import build_openai_native_tool_from_spec
from companion_v01.store import MemoryStore
from companion_v01.task_workspace import TaskWorkspaceService
from companion_v01.tool_runtime import ManageGeneratedFileToolHandler
from companion_v01.workspace_management import clear_workspace_files, list_workspace_files


class _WorkspaceEngine:
    def __init__(
        self,
        *,
        attachment_service: AttachmentInboxService,
        generated_service: GeneratedFileService,
        task_service: TaskWorkspaceService,
    ) -> None:
        self.attachment_service = attachment_service
        self.generated_service = generated_service
        self.task_service = task_service
        self.generated_cleanup_events: list[dict] = []

    def _get_attachment_inbox_service(self):
        return self.attachment_service

    def _get_generated_file_service(self):
        return self.generated_service

    def _get_task_workspace_service(self):
        return self.task_service

    def _record_generated_workspace_cleanup(self, **kwargs):
        self.generated_cleanup_events.append(dict(kwargs))
        return {
            "ok": True,
            "status": "recorded",
            "source_id": f"workspace:event:{len(self.generated_cleanup_events)}",
        }


class WorkspaceManagementTests(unittest.TestCase):
    def _services(self, root: Path):
        store = MemoryStore(root)
        attachment_root = root / "attachments"
        generated_root = root / "generated"
        attachment_service = AttachmentInboxService(store=store, base_dir=attachment_root)
        generated_service = GeneratedFileService(
            base_dir=generated_root,
            store=store,
            attachment_service=attachment_service,
        )
        task_service = TaskWorkspaceService(store)
        engine = _WorkspaceEngine(
            attachment_service=attachment_service,
            generated_service=generated_service,
            task_service=task_service,
        )
        return store, attachment_service, generated_service, task_service, engine

    def _add_files(self, root: Path, store: MemoryStore):
        attachment_path = root / "attachments" / "user" / "session" / "source.txt"
        attachment_path.parent.mkdir(parents=True, exist_ok=True)
        attachment_path.write_text("source", encoding="utf-8")
        attachment = store.add_attachment_inbox_item(
            profile_user_id="user",
            session_id="session",
            source="qq",
            kind="document",
            status="ready",
            origin_name="source.txt",
            storage_relpath="user/session/source.txt",
            summary_title="源文件",
            timestamp=100,
        )

        generated_path = root / "generated" / "user" / "session" / "result.txt"
        generated_path.parent.mkdir(parents=True, exist_ok=True)
        generated_path.write_text("result", encoding="utf-8")
        generated = store.add_generated_file(
            profile_user_id="user",
            session_id="session",
            output_title="生成结果",
            output_format="txt",
            storage_relpath="user/session/result.txt",
            mime_type="text/plain",
            file_ext="txt",
            file_size=generated_path.stat().st_size,
            content_card={"summary": "result"},
            created_by_tool="compose_file",
            timestamp=110,
        )
        return attachment, attachment_path, generated, generated_path

    def test_soft_clear_unifies_attachments_generated_files_and_linked_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store, _attachments, _generated, task_service, engine = self._services(root)
            attachment, attachment_path, generated, generated_path = self._add_files(root, store)
            task = store.add_task_workspace(
                profile_user_id="user",
                session_id="session",
                status="running",
                normalized_goal="处理文件",
                artifacts=[
                    {
                        "id": attachment["attachment_handle"],
                        "attachment_id": attachment["attachment_id"],
                    },
                    {
                        "id": generated["generated_handle"],
                        "generated_id": generated["generated_id"],
                    },
                ],
                timestamp=120,
            )

            before = list_workspace_files(engine, profile_user_id="user", session_id="session")
            result = clear_workspace_files(
                engine,
                profile_user_id="user",
                session_id="session",
                target="all",
                delete_storage=False,
                timestamp=130,
            )
            after = list_workspace_files(engine, profile_user_id="user", session_id="session")

            self.assertEqual(len(before["attachments"]), 1)
            self.assertEqual(len(before["generated_files"]), 1)
            self.assertTrue(result["ok"])
            self.assertTrue(result["complete"])
            self.assertEqual(result["status"], "cleared")
            self.assertEqual(len(result["attachments"]["cleared"]), 1)
            self.assertEqual(len(result["generated_files"]["managed"]), 1)
            self.assertEqual([item["task_id"] for item in result["cleaned_tasks"]], [task["task_id"]])
            self.assertTrue(attachment_path.exists())
            self.assertTrue(generated_path.exists())
            self.assertEqual(task_service.get_task(task["task_id"])["status"], "cleaned")
            self.assertEqual(after["attachments"], [])
            self.assertEqual(after["generated_files"], [])
            self.assertEqual(len(engine.generated_cleanup_events), 1)
            self.assertEqual(engine.generated_cleanup_events[0]["action"], "archive")
            self.assertEqual(
                [item["generated_handle"] for item in engine.generated_cleanup_events[0]["managed"]],
                [generated["generated_handle"]],
            )
            self.assertEqual(result["generated_timeline_event"]["status"], "recorded")

    def test_purge_deletes_managed_bytes_for_both_shelves(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store, _attachments, _generated, _tasks, engine = self._services(root)
            _attachment, attachment_path, generated, generated_path = self._add_files(root, store)

            result = clear_workspace_files(
                engine,
                profile_user_id="user",
                session_id="session",
                target="current",
                delete_storage=True,
                timestamp=130,
            )

            self.assertEqual(result["status"], "cleared")
            self.assertFalse(attachment_path.exists())
            self.assertFalse(generated_path.exists())
            self.assertEqual(result["attachments"]["purged_files"], ["file_001"])
            stored_generated = store.get_generated_file(
                profile_user_id="user",
                session_id="session",
                generated_id=generated["generated_id"],
            )
            self.assertEqual(stored_generated["status"], "removed")
            self.assertEqual(stored_generated["storage_relpath"], "")
            self.assertTrue(stored_generated["content_card"]["purged"])

    def test_generated_delete_failure_keeps_record_visible_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store, _attachments, generated_service, _tasks, _engine = self._services(root)
            _attachment, _attachment_path, generated, generated_path = self._add_files(root, store)

            with patch.object(
                generated_service,
                "_delete_generated_file_on_disk",
                return_value=(False, "delete_failed"),
            ):
                result = generated_service.manage_generated_files(
                    profile_user_id="user",
                    session_id="session",
                    action="purge",
                    targets=[generated["generated_handle"]],
                    timestamp=130,
                )

            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["managed"], [])
            self.assertEqual(result["failures"][0]["code"], "delete_failed")
            stored = store.get_generated_file(
                profile_user_id="user",
                session_id="session",
                generated_id=generated["generated_id"],
            )
            self.assertEqual(stored["status"], "ready")
            self.assertTrue(stored["storage_relpath"])
            self.assertTrue(generated_path.exists())

    def test_direct_generated_delete_failure_is_recorded_once_for_model_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store, _attachments, generated_service, _tasks, engine = self._services(root)
            _attachment, _attachment_path, generated, generated_path = self._add_files(root, store)

            with patch.object(
                generated_service,
                "_delete_generated_file_on_disk",
                return_value=(False, "delete_failed"),
            ):
                result = clear_workspace_files(
                    engine,
                    profile_user_id="user",
                    session_id="session",
                    character_pack_id="akane_v1",
                    target=generated["generated_handle"],
                    delete_storage=True,
                    timestamp=130,
                )

            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "failed")
            self.assertTrue(generated_path.exists())
            self.assertEqual(len(engine.generated_cleanup_events), 1)
            recorded = engine.generated_cleanup_events[0]
            self.assertEqual(recorded["character_pack_id"], "akane_v1")
            self.assertEqual(recorded["status"], "failed")
            self.assertEqual(recorded["managed"], [])
            self.assertEqual(recorded["failures"][0]["code"], "delete_failed")
            self.assertEqual(result["generated_timeline_event"]["status"], "recorded")

    def test_attachment_only_clear_does_not_emit_generated_cleanup_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store, _attachments, _generated, _tasks, engine = self._services(root)
            attachment, _attachment_path, _generated_item, _generated_path = self._add_files(root, store)

            result = clear_workspace_files(
                engine,
                profile_user_id="user",
                session_id="session",
                target=attachment["attachment_handle"],
                delete_storage=False,
                timestamp=130,
            )

            self.assertTrue(result["ok"])
            self.assertEqual(engine.generated_cleanup_events, [])
            self.assertEqual(result["generated_timeline_event"]["status"], "skipped")

    def test_external_attachment_purge_reports_retained_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store, _attachments, _generated, _tasks, engine = self._services(root)
            store.add_attachment_inbox_item(
                profile_user_id="user",
                session_id="session",
                source="workspace",
                kind="document",
                status="ready",
                origin_name="outside.txt",
                storage_relpath="workspace:outside.txt",
                summary_title="外部文件",
                timestamp=100,
            )

            result = clear_workspace_files(
                engine,
                profile_user_id="user",
                session_id="session",
                target="all",
                delete_storage=True,
                timestamp=130,
            )

            self.assertTrue(result["ok"])
            self.assertFalse(result["complete"])
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["failures"][0]["code"], "external_source_retained")

    def test_generated_file_native_schema_matches_handler_contract(self) -> None:
        schema = MANAGE_GENERATED_FILE_TOOL_SPEC.input_schema
        self.assertEqual(
            schema["properties"]["action"]["enum"],
            ["archive", "delete", "purge"],
        )
        self.assertIn("targets", schema["properties"])
        self.assertEqual(MANAGE_GENERATED_FILE_TOOL_SPEC.confirm, "never")
        native = build_openai_native_tool_from_spec(MANAGE_GENERATED_FILE_TOOL_SPEC)
        parameters = native["function"]["parameters"]
        self.assertIn("purge", parameters["properties"]["action"]["enum"])
        self.assertEqual(parameters["properties"]["targets"]["type"], "array")

        handler = ManageGeneratedFileToolHandler(generated_file_service=object())
        self.assertEqual(
            handler.normalize_call(
                {
                    "type": "manage_generated_file",
                    "action": "purge",
                    "targets": ["gen_001", "gen_002"],
                }
            ),
            {
                "type": "manage_generated_file",
                "action": "purge",
                "targets": ["gen_001", "gen_002"],
                "reason": "",
            },
        )

    def test_native_generated_cleanup_closes_only_linked_active_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store, _attachments, generated_service, task_service, _engine = self._services(root)
            _attachment, _attachment_path, generated, _generated_path = self._add_files(root, store)
            linked = store.add_task_workspace(
                profile_user_id="user",
                session_id="session",
                status="running",
                normalized_goal="处理生成结果",
                artifacts=[{"id": generated["generated_handle"]}],
                timestamp=120,
            )
            unrelated = store.add_task_workspace(
                profile_user_id="user",
                session_id="session",
                status="running",
                normalized_goal="另一个任务",
                artifacts=[{"id": "gen_999"}],
                timestamp=121,
            )
            handler = ManageGeneratedFileToolHandler(
                generated_file_service=generated_service,
                task_workspace_service=task_service,
            )
            context = type(
                "_Context",
                (),
                {
                    "profile_user_id": "user",
                    "session_id": "session",
                    "now_ts": 130,
                },
            )()

            result = handler.execute(
                call={
                    "type": "manage_generated_file",
                    "action": "archive",
                    "targets": [generated["generated_handle"]],
                    "reason": "用户不再需要",
                },
                context=context,
            )

            self.assertEqual(store.get_task_workspace(linked["task_id"])["status"], "cleaned")
            self.assertEqual(store.get_task_workspace(unrelated["task_id"])["status"], "running")
            self.assertEqual(
                [event["type"] for event in result.stream_events],
                ["generated_files_managed", "task_workspaces_cleaned"],
            )
            self.assertIn("关闭了 1 个", result.followup_context)

    def test_native_generated_cleanup_failure_returns_model_visible_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store, _attachments, generated_service, _task_service, _engine = self._services(root)
            _attachment, _attachment_path, generated, generated_path = self._add_files(root, store)
            handler = ManageGeneratedFileToolHandler(generated_file_service=generated_service)
            context = type(
                "_Context",
                (),
                {
                    "profile_user_id": "user",
                    "session_id": "session",
                    "now_ts": 130,
                },
            )()

            with patch.object(
                generated_service,
                "_delete_generated_file_on_disk",
                return_value=(False, "delete_failed"),
            ):
                result = handler.execute(
                    call={
                        "type": "manage_generated_file",
                        "action": "purge",
                        "targets": [generated["generated_handle"]],
                        "reason": "用户要求彻底清理",
                    },
                    context=context,
                )

            self.assertTrue(generated_path.exists())
            self.assertEqual([event["type"] for event in result.stream_events], ["generated_files_manage_failed"])
            self.assertEqual(result.stream_events[0]["failures"][0]["code"], "delete_failed")
            self.assertIn("未能安全完成", result.followup_context)
            self.assertIn("不要声称已经删除", result.followup_context)


if __name__ == "__main__":
    unittest.main()
