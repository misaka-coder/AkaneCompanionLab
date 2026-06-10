from __future__ import annotations

import tempfile
import unittest
import shutil
from pathlib import Path
from unittest.mock import patch

import config
from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.attachment_ingest import AttachmentIngestService
from companion_v01.capability_registry import CapabilityRegistry, CapabilitySnapshot
from companion_v01.client_protocol import ClientMode
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.generated_files import GeneratedFileService
from companion_v01.store import MemoryStore
from companion_v01.tool_runtime import (
    FocusWorkspaceToolHandler,
    ListWorkspaceToolHandler,
    ReadWorkspaceToolHandler,
    RegisterWorkspaceItemsToolHandler,
    ToolExecutionContext,
)
from companion_v01.workspace_files import WorkspaceFileService


class WorkspaceFileServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.store = MemoryStore(self.base_dir / "data")
        self.root = self.base_dir / "Akane Workspace"
        self.service = WorkspaceFileService(root_dir=self.root, store=self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_creates_layered_root_and_batch_lists_live_files(self) -> None:
        for folder_name in ("Inbox", "Outputs", "Archive"):
            self.assertTrue((self.root / folder_name).is_dir())
        (self.root / "Inbox" / "one.md").write_text("one", encoding="utf-8")
        (self.root / "Outputs" / "two.txt").write_text("two", encoding="utf-8")

        result = self.service.list_items(
            profile_user_id="master",
            session_id="desktop",
            paths=["workspace:/Inbox", "workspace:/Outputs"],
            depth=1,
        )

        self.assertEqual(result["status"], "ok")
        uris = [
            entry["uri"]
            for target in result["results"]
            for entry in target["entries"]
        ]
        self.assertEqual(uris, ["workspace:/Inbox/one.md", "workspace:/Outputs/two.txt"])
        self.assertNotIn(str(self.root), str(result))

    def test_prompt_context_always_exposes_workspace_overview_and_recent_files(self) -> None:
        note = self.root / "Inbox" / "note.md"
        note.write_text("recent", encoding="utf-8")

        context = self.service.build_prompt_context(
            profile_user_id="master",
            session_id="desktop",
        )

        self.assertIn("workspace:/（位于用户设置中配置的 Akane Workspace 文件夹）", context)
        self.assertIn("先主动调用 list_workspace", context)
        self.assertIn("workspace:/Inbox/note.md", context)
        self.assertNotIn(str(self.root), context)

    def test_rejects_absolute_and_traversal_paths(self) -> None:
        result = self.service.list_items(
            profile_user_id="master",
            session_id="desktop",
            paths=["C:/Windows", "workspace:/../outside"],
            depth=1,
        )

        self.assertEqual([item["status"] for item in result["results"]], ["denied", "denied"])
        self.assertNotIn(str(self.base_dir), str(result))
        self.assertNotIn("C:/Windows", str(result))

    def test_batch_reads_text_and_reports_binary_without_fake_content(self) -> None:
        (self.root / "Inbox" / "note.md").write_text("hello Akane", encoding="utf-8")
        (self.root / "Inbox" / "track.wav").write_bytes(b"RIFFstub")

        result = self.service.read_items(
            profile_user_id="master",
            session_id="desktop",
            targets=["workspace:/Inbox/note.md", "workspace:/Inbox/track.wav"],
        )

        self.assertEqual(result["items"][0]["status"], "ok")
        self.assertEqual(result["items"][0]["content"], "hello Akane")
        self.assertEqual(result["items"][1]["status"], "unsupported_binary")
        self.assertNotIn("absolute", str(result).lower())

    def test_focus_state_persists_and_hidden_file_remains_discoverable(self) -> None:
        note = self.root / "Inbox" / "note.md"
        note.write_text("persistent context", encoding="utf-8")

        focused = self.service.focus_items(
            profile_user_id="master",
            session_id="desktop",
            targets=["workspace:/Inbox/note.md"],
            action="add",
        )
        self.assertEqual(focused["focused"], ["workspace:/Inbox/note.md"])
        self.assertIn(
            "persistent context",
            self.service.build_prompt_context(
                profile_user_id="master",
                session_id="desktop",
            ),
        )

        reloaded = WorkspaceFileService(root_dir=self.root, store=MemoryStore(self.base_dir / "data"))
        self.assertIn(
            "persistent context",
            reloaded.build_prompt_context(
                profile_user_id="master",
                session_id="desktop",
            ),
        )
        hidden = reloaded.focus_items(
            profile_user_id="master",
            session_id="desktop",
            targets=["workspace:/Inbox/note.md"],
            action="remove",
        )
        self.assertEqual(hidden["focused"], [])
        listing = reloaded.list_items(
            profile_user_id="master",
            session_id="desktop",
            paths=["workspace:/Inbox"],
            depth=1,
        )
        self.assertEqual(listing["results"][0]["entries"][0]["workspace_status"], "hidden")
        self.assertTrue(note.exists())

    def test_missing_focused_file_can_still_be_removed(self) -> None:
        note = self.root / "Inbox" / "gone.md"
        note.write_text("temporary", encoding="utf-8")
        self.service.focus_items(
            profile_user_id="master",
            session_id="desktop",
            targets=["workspace:/Inbox/gone.md"],
            action="add",
        )
        note.unlink()

        result = self.service.focus_items(
            profile_user_id="master",
            session_id="desktop",
            targets=["workspace:/Inbox/gone.md"],
            action="remove",
        )

        self.assertEqual(result["focused"], [])

    def test_deleted_file_disappears_from_live_workspace_listing(self) -> None:
        note = self.root / "Inbox" / "gone.md"
        note.write_text("temporary", encoding="utf-8")
        before = self.service.list_items(
            profile_user_id="master",
            session_id="desktop",
            paths=["workspace:/Inbox"],
            depth=1,
        )
        self.assertEqual(
            [entry["uri"] for entry in before["results"][0]["entries"]],
            ["workspace:/Inbox/gone.md"],
        )

        note.unlink()

        after = self.service.list_items(
            profile_user_id="master",
            session_id="desktop",
            paths=["workspace:/Inbox"],
            depth=1,
        )
        self.assertEqual(after["results"][0]["entries"], [])

    def test_recursive_directory_focus_loads_multiple_files(self) -> None:
        nested = self.root / "Inbox" / "project" / "nested"
        nested.mkdir(parents=True)
        (self.root / "Inbox" / "project" / "a.md").write_text("A", encoding="utf-8")
        (nested / "b.md").write_text("B", encoding="utf-8")

        result = self.service.focus_items(
            profile_user_id="master",
            session_id="desktop",
            targets=["workspace:/Inbox/project"],
            action="set",
            recursive=True,
        )

        self.assertEqual(
            result["focused"],
            [
                "workspace:/Inbox/project/a.md",
                "workspace:/Inbox/project/nested/b.md",
            ],
        )

    def test_resolve_file_targets_reports_truncation_only_when_files_remain(self) -> None:
        project = self.root / "Inbox" / "project"
        project.mkdir()
        (project / "a.md").write_text("A", encoding="utf-8")
        (project / "b.md").write_text("B", encoding="utf-8")

        exact_files, _, exact_truncated = self.service.resolve_file_targets(
            targets=["workspace:/Inbox/project"],
            max_files=2,
        )
        limited_files, _, limited_truncated = self.service.resolve_file_targets(
            targets=["workspace:/Inbox/project"],
            max_files=1,
        )

        self.assertEqual(len(exact_files), 2)
        self.assertFalse(exact_truncated)
        self.assertEqual(len(limited_files), 1)
        self.assertTrue(limited_truncated)

    def test_drag_import_recreates_deleted_workspace_and_writes_visible_inbox_file(self) -> None:
        inbox_dir = self.service.layer_dir("Inbox")
        inbox = AttachmentInboxService(
            store=self.store,
            base_dir=inbox_dir,
            legacy_base_dirs=[self.base_dir / "legacy_attachments"],
        )
        ingest = AttachmentIngestService(
            base_dir=inbox_dir,
            store=self.store,
            attachment_service=inbox,
            vision_service=object(),  # type: ignore[arg-type]
            legacy_base_dirs=[self.base_dir / "legacy_attachments"],
            ensure_storage_ready=self.service.ensure_layout,
        )
        source = self.base_dir / "source" / "note.md"
        source.parent.mkdir()
        source.write_text("# visible inbox", encoding="utf-8")
        shutil.rmtree(self.root)

        item = ingest.ingest_local_file(
            profile_user_id="master",
            session_id="desktop",
            source_path=source,
            origin_name=source.name,
            kind="document",
            source="desktop_pet",
            timestamp=200,
        )

        for folder_name in ("Inbox", "Outputs", "Archive"):
            self.assertTrue((self.root / folder_name).is_dir())
        stored_path = inbox.resolve_storage_path(item)
        self.assertIsNotNone(stored_path)
        assert stored_path is not None
        self.assertTrue(stored_path.is_relative_to(self.root / "Inbox"))
        self.assertEqual(stored_path.parent.name, "1970-01-01")
        self.assertEqual(stored_path.name, "file_001__note.md")
        self.assertEqual(stored_path.read_text(encoding="utf-8"), "# visible inbox")

    def test_readable_inbox_names_do_not_overwrite_across_sessions(self) -> None:
        inbox_dir = self.service.layer_dir("Inbox")
        inbox = AttachmentInboxService(store=self.store, base_dir=inbox_dir)
        ingest = AttachmentIngestService(
            base_dir=inbox_dir,
            store=self.store,
            attachment_service=inbox,
            vision_service=None,
        )
        source = self.base_dir / "note.md"
        source.write_text("shared name", encoding="utf-8")

        first = ingest.ingest_local_file(
            profile_user_id="master",
            session_id="session-a",
            source_path=source,
            kind="document",
            timestamp=200,
        )
        second = ingest.ingest_local_file(
            profile_user_id="master",
            session_id="session-b",
            source_path=source,
            kind="document",
            timestamp=200,
        )
        first_path = inbox.resolve_storage_path(first)
        second_path = inbox.resolve_storage_path(second)

        self.assertIsNotNone(first_path)
        self.assertIsNotNone(second_path)
        assert first_path is not None and second_path is not None
        self.assertEqual(first_path.name, "file_001__note.md")
        self.assertEqual(second_path.name, "file_001__note_2.md")
        self.assertNotEqual(first_path, second_path)

    def test_generation_recreates_deleted_workspace_and_writes_visible_output(self) -> None:
        outputs_dir = self.service.layer_dir("Outputs")
        inbox = AttachmentInboxService(store=self.store, base_dir=self.service.layer_dir("Inbox"))
        generated_service = GeneratedFileService(
            base_dir=outputs_dir,
            store=self.store,
            attachment_service=inbox,
            legacy_base_dirs=[self.base_dir / "legacy_generated"],
            ensure_storage_ready=self.service.ensure_layout,
            work_dir=self.base_dir / "generated_work",
        )
        shutil.rmtree(self.root)

        result = generated_service.compose_file(
            profile_user_id="master",
            session_id="desktop",
            source_targets=[],
            task="生成测试文件",
            output_format="md",
            output_title="测试产物",
            content_markdown="# visible output",
            send_to_user=False,
            timestamp=210,
        )

        self.assertTrue(result["ok"])
        for folder_name in ("Inbox", "Outputs", "Archive"):
            self.assertTrue((self.root / folder_name).is_dir())
        output_path = generated_service.absolute_path(result["generated"])
        self.assertTrue(output_path.is_relative_to(self.root / "Outputs"))
        self.assertEqual(output_path.parent, self.root / "Outputs" / "1970-01-01")
        self.assertNotIn("desktop", output_path.relative_to(self.root / "Outputs").parts)
        self.assertEqual(output_path.read_text(encoding="utf-8"), "# visible output")
        self.assertFalse(any(path.name.startswith("_") for path in (self.root / "Outputs").iterdir()))

    def test_new_services_resolve_legacy_attachment_and_generated_paths(self) -> None:
        legacy_attachments = self.base_dir / "legacy_attachments"
        legacy_generated = self.base_dir / "legacy_generated"
        attachment_relpath = Path("master") / "desktop" / "file_001.md"
        generated_relpath = Path("master") / "desktop" / "result.md"
        legacy_attachment_path = legacy_attachments / attachment_relpath
        legacy_generated_path = legacy_generated / generated_relpath
        legacy_attachment_path.parent.mkdir(parents=True)
        legacy_generated_path.parent.mkdir(parents=True)
        legacy_attachment_path.write_text("legacy input", encoding="utf-8")
        legacy_generated_path.write_text("legacy output", encoding="utf-8")

        inbox = AttachmentInboxService(
            store=self.store,
            base_dir=self.service.layer_dir("Inbox"),
            legacy_base_dirs=[legacy_attachments],
        )
        attachment = self.store.add_attachment_inbox_item(
            profile_user_id="master",
            session_id="desktop",
            source="desktop_pet",
            kind="document",
            status="ready",
            origin_name="old.md",
            file_ext="md",
            storage_relpath=attachment_relpath.as_posix(),
            timestamp=220,
        )
        generated_service = GeneratedFileService(
            base_dir=self.service.layer_dir("Outputs"),
            store=self.store,
            attachment_service=inbox,
            legacy_base_dirs=[legacy_generated],
            work_dir=self.base_dir / "generated_work",
        )
        generated = self.store.add_generated_file(
            profile_user_id="master",
            session_id="desktop",
            output_title="旧产物",
            output_format="md",
            storage_relpath=generated_relpath.as_posix(),
            timestamp=221,
        )

        self.assertEqual(inbox.resolve_storage_path(attachment), legacy_attachment_path)
        self.assertEqual(generated_service.absolute_path(generated), legacy_generated_path)
        self.assertTrue(generated_service.is_managed_storage_path(legacy_generated_path))

        deleted = generated_service.manage_generated_files(
            profile_user_id="master",
            session_id="desktop",
            action="delete",
            targets=["gen_001"],
            timestamp=222,
        )
        self.assertTrue(deleted["ok"])
        self.assertTrue(deleted["managed"][0]["file_deleted"])
        self.assertFalse(legacy_generated_path.exists())

    def test_generated_absolute_path_rejects_external_absolute_storage_path(self) -> None:
        outside = self.base_dir / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        generated_service = GeneratedFileService(
            base_dir=self.service.layer_dir("Outputs"),
            store=self.store,
            attachment_service=AttachmentInboxService(store=self.store),
        )

        resolved = generated_service.absolute_path(
            {"storage_relpath": str(outside.resolve())}
        )

        self.assertEqual(resolved, self.service.layer_dir("Outputs"))
        self.assertNotEqual(resolved, outside)

    def test_registers_workspace_media_in_place_and_reuses_handle(self) -> None:
        source = self.root / "Projects" / "audio.wav"
        source.parent.mkdir()
        source.write_bytes(b"RIFFstub")
        inbox = AttachmentInboxService(
            store=self.store,
            base_dir=self.service.layer_dir("Inbox"),
            workspace_uri_resolver=self.service.resolve_file_uri,
        )
        ingest = AttachmentIngestService(
            base_dir=self.service.layer_dir("Inbox"),
            store=self.store,
            attachment_service=inbox,
            vision_service=None,
            workspace_uri_resolver=self.service.resolve_file_uri,
        )

        registered = ingest.register_workspace_file(
            profile_user_id="master",
            session_id="desktop",
            workspace_uri="workspace:/Projects/audio.wav",
            timestamp=230,
        )
        item = registered["item"]

        self.assertEqual(registered["status"], "registered")
        self.assertEqual(item["attachment_handle"], "audio_001")
        self.assertEqual(item["status"], "ready")
        self.assertEqual(item["storage_relpath"], "workspace:/Projects/audio.wav")
        self.assertEqual(inbox.resolve_storage_path(item), source)
        self.assertFalse(any(path.is_file() for path in self.service.layer_dir("Inbox").rglob("*")))

        generated_service = GeneratedFileService(
            base_dir=self.service.layer_dir("Outputs"),
            store=self.store,
            attachment_service=inbox,
        )
        sendable, error = generated_service._resolve_attachment_sendable_file(
            profile_user_id="master",
            session_id="desktop",
            target="audio_001",
        )
        self.assertEqual(error, "")
        self.assertIsNotNone(sendable)
        assert sendable is not None
        self.assertEqual(Path(sendable["absolute_path"]), source)

        refreshed = ingest.register_workspace_file(
            profile_user_id="master",
            session_id="desktop",
            workspace_uri="workspace:/Projects/audio.wav",
            timestamp=231,
        )
        self.assertEqual(refreshed["status"], "refreshed")
        self.assertEqual(refreshed["item"]["attachment_handle"], "audio_001")
        self.assertEqual(
            len(
                self.store.list_attachment_inbox_items(
                    profile_user_id="master",
                    session_id="desktop",
                    limit=20,
                )
            ),
            1,
        )


class WorkspaceToolHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base_dir = Path(self.temp_dir.name)
        self.root = base_dir / "workspace"
        self.store = MemoryStore(base_dir / "data")
        self.service = WorkspaceFileService(
            root_dir=self.root,
            store=self.store,
        )
        (self.root / "Inbox" / "note.md").write_text("tool content", encoding="utf-8")
        self.context = ToolExecutionContext(
            profile_user_id="master",
            session_id="desktop",
            now_ts=100,
            visual_payload={},
            client_mode="desktop_pet",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_handlers_support_batch_paths_and_return_relative_context(self) -> None:
        list_handler = ListWorkspaceToolHandler(workspace_service=self.service)
        read_handler = ReadWorkspaceToolHandler(workspace_service=self.service)
        focus_handler = FocusWorkspaceToolHandler(workspace_service=self.service)

        list_call = list_handler.normalize_call(
            {"type": "list_workspace", "paths": ["workspace:/Inbox", "workspace:/Outputs"]}
        )
        read_call = read_handler.normalize_call(
            {"type": "read_workspace", "targets": ["workspace:/Inbox/note.md"]}
        )
        focus_call = focus_handler.normalize_call(
            {"type": "focus_workspace", "action": "add", "targets": ["workspace:/Inbox/note.md"]}
        )

        self.assertIsNotNone(list_call)
        self.assertIsNotNone(read_call)
        self.assertIsNotNone(focus_call)
        list_result = list_handler.execute(call=list_call or {}, context=self.context)
        read_result = read_handler.execute(call=read_call or {}, context=self.context)
        focus_result = focus_handler.execute(call=focus_call or {}, context=self.context)
        self.assertIn("workspace:/Inbox/note.md", list_result.followup_context)
        self.assertIn("tool content", read_result.followup_context)
        self.assertIn("tool content", focus_result.followup_context)
        self.assertNotIn(str(self.root), list_result.followup_context)
        self.assertNotIn(str(self.root), read_result.followup_context)
        self.assertNotIn(str(self.root), focus_result.followup_context)

    def test_register_handler_batches_directories_without_leaking_absolute_paths(self) -> None:
        project = self.root / "Project"
        project.mkdir()
        (project / "voice.wav").write_bytes(b"RIFFstub")
        (project / "notes.md").write_text("# notes", encoding="utf-8")
        inbox = AttachmentInboxService(
            store=self.store,
            base_dir=self.service.layer_dir("Inbox"),
            workspace_uri_resolver=self.service.resolve_file_uri,
        )
        ingest = AttachmentIngestService(
            base_dir=self.service.layer_dir("Inbox"),
            store=self.store,
            attachment_service=inbox,
            vision_service=None,
            workspace_uri_resolver=self.service.resolve_file_uri,
        )
        handler = RegisterWorkspaceItemsToolHandler(
            workspace_service=self.service,
            attachment_ingest_service=ingest,
        )
        call = handler.normalize_call(
            {
                "type": "register_workspace_items",
                "targets": ["workspace:/Project", "C:/outside"],
                "recursive": True,
            }
        )

        self.assertIsNotNone(call)
        result = handler.execute(call=call or {}, context=self.context)
        event_items = result.stream_events[0]["items"]
        self.assertEqual([item["handle"] for item in event_items], ["file_001", "audio_001"])
        self.assertIn("workspace:/Project/notes.md", result.followup_context)
        self.assertIn("workspace:/Project/voice.wav", result.followup_context)
        self.assertIn("(invalid workspace path): denied", result.followup_context)
        self.assertNotIn(str(self.root), result.followup_context)
        self.assertNotIn("C:/outside", result.followup_context)

    def test_workspace_tools_are_desktop_only(self) -> None:
        registry = CapabilityRegistry()
        desktop = registry.select(CapabilitySnapshot(client_mode=ClientMode.DESKTOP_PET))
        qq = registry.select(CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT))

        for tool_name in ("list_workspace", "read_workspace", "focus_workspace", "register_workspace_items"):
            self.assertIn(tool_name, desktop.tool_names)
            self.assertNotIn(tool_name, qq.tool_names)
        self.assertIn("desktop_file_workspace", desktop.module_names)

        with_media = registry.select(
            CapabilitySnapshot(
                client_mode=ClientMode.DESKTOP_PET,
                has_any_attachment=True,
                has_media_attachment=True,
            )
        )
        self.assertIn("transcribe_media", with_media.tool_names)
        self.assertIn("inspect_media_info", with_media.tool_names)


class WorkspaceEngineWiringTests(unittest.TestCase):
    def test_engine_services_use_configured_visible_layers_with_legacy_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            configured_root = base_dir / "Visible Workspace"
            engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
            engine.base_dir = base_dir / "engine_data"
            engine.base_dir.mkdir()
            engine.store = MemoryStore(engine.base_dir)
            engine.vision_service = object()
            engine.background_tasks = None

            with patch.object(config, "AKANE_WORKSPACE_ROOT", str(configured_root)):
                workspace = engine._get_workspace_file_service()
                inbox = engine._get_attachment_inbox_service()
                ingest = engine._get_attachment_ingest_service()
                generated = engine._get_generated_file_service()

            self.assertIsNotNone(workspace)
            self.assertIsNotNone(inbox)
            self.assertIsNotNone(ingest)
            self.assertIsNotNone(generated)
            assert workspace is not None and inbox is not None and ingest is not None and generated is not None
            self.assertEqual(inbox.base_dir, configured_root / "Inbox")
            self.assertIsNotNone(inbox.workspace_uri_resolver)
            self.assertIsNotNone(ingest.workspace_uri_resolver)
            self.assertEqual(generated.base_dir, configured_root / "Outputs")
            self.assertIn(engine.base_dir / "attachment_inbox_files", inbox.legacy_base_dirs)
            self.assertIn(engine.base_dir / "generated_files", generated.legacy_base_dirs)
            self.assertEqual(generated.work_dir, engine.base_dir / "generated_work")


if __name__ == "__main__":
    unittest.main()
