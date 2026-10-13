"""Public inbox boundaries and dependencies after extracting implementation modules."""

import ast
import builtins
import symtable
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from companion_v01 import attachment_inbox
from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.store import MemoryStore


ROOT = Path(__file__).resolve().parents[1] / "companion_v01"
ENTRY = ROOT / "attachment_inbox.py"
PACKAGE = ROOT / "attachment_inbox_runtime"


class AttachmentInboxBoundaryTests(unittest.TestCase):
    def test_modules_do_not_depend_on_unbound_globals(self):
        for path in PACKAGE.glob("*.py"):
            table = symtable.symtable(path.read_text(encoding="utf-8"), str(path), "exec")
            bound = {symbol.get_name() for symbol in table.get_symbols()
                     if symbol.is_imported() or symbol.is_assigned() or symbol.is_namespace()}
            bound.update(dir(builtins))
            bound.update({"__name__", "__package__", "__file__", "__doc__", "__builtins__"})

            def check(scope):
                for symbol in scope.get_symbols():
                    if symbol.is_referenced() and symbol.is_global():
                        self.assertIn(symbol.get_name(), bound, f"{path.name}: unbound global")
                for child in scope.get_children():
                    check(child)

            check(table)

    def test_implementation_modules_are_reachable_without_reverse_imports_or_cycles(self):
        graph = {}

        def visit(path):
            if path in graph:
                return
            graph[path] = set()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.ImportFrom) or not node.level:
                    continue
                base = path.parent
                for _ in range(node.level - 1):
                    base = base.parent
                dependency = base.joinpath(*(node.module or "").split(".")).with_suffix(".py")
                if path.is_relative_to(PACKAGE):
                    self.assertNotEqual(dependency, ENTRY, f"{path.name} imports the entry")
                if dependency.is_relative_to(PACKAGE) and dependency.exists():
                    graph[path].add(dependency)
                    visit(dependency)

        visit(ENTRY)
        self.assertEqual(set(graph) - {ENTRY}, set(PACKAGE.glob("*.py")) - {PACKAGE / "__init__.py"})
        active, complete = set(), set()

        def check(path):
            self.assertNotIn(path, active, f"cyclic inbox import at {path.name}")
            if path in complete:
                return
            active.add(path)
            for dependency in graph[path]:
                check(dependency)
            active.remove(path)
            complete.add(path)

        check(ENTRY)

    def test_original_constant_exports_keep_their_identity(self):
        from companion_v01.attachment_inbox_runtime import definitions
        for name in (
            "ACTIVE_ATTACHMENT_STATUSES", "READY_ATTACHMENT_STATUSES", "AUTO_FOCUS_BATCH_SECONDS",
            "DEFAULT_WORKSPACE_CHAR_BUDGET", "WORKSPACE_ITEM_CHAR_BUDGET", "WORKSPACE_MAX_TARGETS",
            "AUTO_FOCUS_MAX_ITEMS", "INTERNAL_ATTACHMENT_DETAIL_KEYS", "NATIVE_IMAGE_MEDIA_TYPES", "logger",
        ):
            self.assertIs(getattr(attachment_inbox, name), getattr(definitions, name))

    def test_service_methods_have_a_single_owner_and_caches_belong_to_the_instance(self):
        owners = {}
        for owner in AttachmentInboxService.__mro__:
            if not owner.__module__.startswith("companion_v01.attachment_inbox"):
                continue
            for name, value in vars(owner).items():
                if name.startswith("__") or not callable(value):
                    continue
                self.assertNotIn(name, owners, f"{name} has conflicting method owners")
                owners[name] = owner
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary))
            first = AttachmentInboxService(store=store)
            second = AttachmentInboxService(store=store)
            self.assertIsNot(first._section_cache, second._section_cache)

    def test_focus_budget_overflow_keeps_material_metadata_in_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary))
            service = AttachmentInboxService(store=store)
            for index in range(1, 4):
                pending = service.create_pending(
                    profile_user_id="p1", session_id="s1", source="qq", kind="document",
                    origin_name=f"large-{index}.txt", timestamp=100 + index,
                )
                service.mark_ready(
                    profile_user_id="p1", session_id="s1", attachment_id=pending["attachment_id"],
                    detail={"file_kind": "txt", "line_count": index, "text_preview": f"BODY-{index}" * 500},
                    timestamp=110 + index,
                )
            with patch("companion_v01.attachment_inbox_runtime.context.DEFAULT_WORKSPACE_CHAR_BUDGET", 1800):
                prompt = service.build_prompt_context(profile_user_id="p1", session_id="s1")
            self.assertIn("旁边的材料清单 Manifest", prompt)
            manifest = prompt.split("旁边的材料清单 Manifest", 1)[1]
            for handle in ("file_002", "file_003"):
                self.assertIn(handle, manifest)
            self.assertIn("格式=txt", manifest)
            self.assertNotIn("BODY-2", manifest)
            self.assertNotIn("BODY-3", manifest)
            focused = store.list_attachment_inbox_items(profile_user_id="p1", session_id="s1", statuses=["ready"])
            self.assertTrue(all(item["focus_rank"] > 0 for item in focused))

    def test_focus_does_not_present_a_short_partial_preview_as_full_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary))
            service = AttachmentInboxService(store=store)
            pending = service.create_pending(
                profile_user_id="p1", session_id="s1", source="qq", kind="document",
                origin_name="large.txt", timestamp=100,
            )
            service.mark_ready(
                profile_user_id="p1", session_id="s1", attachment_id=pending["attachment_id"],
                detail={"text_preview": "只有开头", "preview_is_truncated": True}, timestamp=110,
            )
            prompt = service.build_prompt_context(profile_user_id="p1", session_id="s1")
            self.assertIn("只有开头", prompt)
            self.assertIn("这里只展开了预览", prompt)

    def test_precise_handle_still_resolves_when_it_is_outside_the_recent_item_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary))
            service = AttachmentInboxService(store=store)
            item = store.add_attachment_inbox_item(
                profile_user_id="p1", session_id="s1", kind="document", status="ready", origin_name="old.txt",
            )
            with patch.object(store, "list_attachment_inbox_items", return_value=[]):
                resolved = service.resolve_attachment(
                    profile_user_id="p1", session_id="s1", target=item["attachment_handle"], kind="document",
                )
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved["attachment_id"], item["attachment_id"])
                self.assertNotIn("_match_rank", resolved)
                self.assertIsNone(service.resolve_attachment(
                    profile_user_id="p2", session_id="s1", target=item["attachment_handle"], kind="document",
                ))

    def test_wait_by_id_is_independent_of_the_recent_list_window_and_excludes_cleared_items(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary))
            service = AttachmentInboxService(store=store)
            item = store.add_attachment_inbox_item(profile_user_id="p1", session_id="s1", status="ready")
            with patch.object(store, "list_attachment_inbox_items", return_value=[]):
                settled = service.wait_for_attachments_settled(
                    profile_user_id="p1", session_id="s1", attachment_ids=[item["attachment_id"]], timeout_seconds=0,
                )
                self.assertEqual(settled["ready"], [item["attachment_id"]])
                store.update_attachment_inbox_item(
                    profile_user_id="p1", session_id="s1", attachment_id=item["attachment_id"], status="cleared",
                )
                missing = service.wait_for_attachments_settled(
                    profile_user_id="p1", session_id="s1", attachment_ids=[item["attachment_id"]], timeout_seconds=0,
                )
                self.assertEqual(missing["missing"], [item["attachment_id"]])

    def test_missing_managed_file_is_reported_already_absent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            storage = root / "attachments"
            storage.mkdir()
            store = MemoryStore(root / "store")
            service = AttachmentInboxService(store=store, base_dir=storage)
            item = store.add_attachment_inbox_item(
                profile_user_id="p1", session_id="s1", status="ready", storage_relpath="missing.txt",
            )
            result = service.clear_focus(
                profile_user_id="p1", session_id="s1", target="all", delete_storage=True,
            )
            self.assertEqual(result["already_absent_files"], [item["attachment_handle"]])
            self.assertEqual(result["purge_failures"], [])

    def test_purge_keeps_directories_and_files_outside_the_managed_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            storage = root / "attachments"
            storage.mkdir()
            folder = storage / "folder"
            folder.mkdir()
            outside = root / "outside.txt"
            outside.write_text("keep", encoding="utf-8")
            store = MemoryStore(root / "store")
            service = AttachmentInboxService(store=store, base_dir=storage)
            for relative in ("folder", "../outside.txt"):
                store.add_attachment_inbox_item(
                    profile_user_id="p1", session_id="s1", status="ready", storage_relpath=relative,
                )
            result = service.clear_focus(profile_user_id="p1", session_id="s1", target="all", delete_storage=True)
            self.assertEqual(result["purged_files"], [])
            self.assertEqual(len(result["purge_failures"]), 2)
            self.assertTrue(folder.is_dir())
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
