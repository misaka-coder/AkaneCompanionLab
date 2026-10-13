from __future__ import annotations

import ast
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from companion_v01.channels.qq.context import QQMessageContext
from companion_v01.channels.qq.gateway import NapCatQQGateway


class QQGatewayModuleTests(unittest.TestCase):
    def test_gateway_builds_the_canonical_context_type(self) -> None:
        restored = NapCatQQGateway().context_from_delivery_context({"target_id": 41})
        self.assertIs(type(restored), QQMessageContext)

    def test_preferences_and_reload_preserve_map_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            gateway = NapCatQQGateway(state_path=path)
            other = NapCatQQGateway()
            original_map = gateway.character_pack_overrides
            self.assertTrue(gateway.set_session_character_pack_id("qq_pri_41", "reimu"))
            gateway._load_persisted_state()
            self.assertIs(gateway.character_pack_overrides, original_map)
            self.assertEqual(gateway.resolve_character_pack_id("qq_pri_41"), "reimu")
            self.assertEqual(other.resolve_character_pack_id("qq_pri_41"), other.default_character_pack_id)
            self.assertEqual(other.character_pack_overrides, {})

    def test_concurrent_snapshots_cannot_overwrite_a_newer_preference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            gateway = NapCatQQGateway(state_path=path)
            old_write_started = threading.Event()
            release_old_write = threading.Event()
            newer_write_finished = threading.Event()
            errors: list[BaseException] = []
            results: list[bool] = []
            original_replace = Path.replace

            def controlled_replace(source: Path, destination: Path) -> Path:
                if threading.current_thread().name == "older-qq-snapshot":
                    old_write_started.set()
                    if not release_old_write.wait(3):
                        raise TimeoutError("test_snapshot_release_timeout")
                return original_replace(source, destination)

            def save_old() -> None:
                try:
                    results.append(gateway.set_session_character_pack_id("qq_pri_41", "reimu"))
                except BaseException as exc:
                    errors.append(exc)

            def save_new() -> None:
                try:
                    results.append(gateway.set_session_chat_model_override("qq_pri_41", "model-new"))
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    newer_write_finished.set()

            older = threading.Thread(target=save_old, name="older-qq-snapshot")
            newer = threading.Thread(target=save_new, name="newer-qq-snapshot")
            with patch.object(Path, "replace", controlled_replace):
                try:
                    older.start()
                    self.assertTrue(old_write_started.wait(2))
                    newer.start()
                    # Old code lets the newer write finish first, then overwrites
                    # it. Ordered persistence holds the newer mutation until
                    # the first complete snapshot has reached the target file.
                    newer_write_finished.wait(0.1)
                finally:
                    release_old_write.set()
                    older.join(3)
                    if newer.ident is not None:
                        newer.join(3)
            self.assertFalse(older.is_alive())
            self.assertFalse(newer.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(results, [True, True])
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["character_pack_overrides"], {"qq_pri_41": "reimu"})
            self.assertEqual(persisted["chat_model_overrides"], {"qq_pri_41": "model-new"})
            restored = NapCatQQGateway(state_path=path)
            self.assertEqual(restored.resolve_chat_model_override("qq_pri_41"), "model-new")

    def test_failed_atomic_state_write_retains_file_and_cleans_temporary_files(self) -> None:
        for operation in ("write_text", "replace"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "state.json"
                gateway = NapCatQQGateway(state_path=path)
                self.assertTrue(gateway.set_session_character_pack_id("qq_pri_41", "reimu"))
                original_bytes = path.read_bytes()
                with patch.object(Path, operation, side_effect=PermissionError("private-path-marker")):
                    self.assertFalse(gateway.set_session_chat_model_override("qq_pri_41", "model-new"))
                self.assertEqual(path.read_bytes(), original_bytes)
                self.assertEqual(list(Path(temp_dir).glob("*.tmp")), [])
                self.assertEqual(gateway.status()["state_error"], "PermissionError")
                self.assertNotIn("private-path-marker", json.dumps(gateway.status()))
                self.assertTrue(gateway._persist_gateway_state())
                self.assertEqual(gateway.status()["state_error"], "")

    def test_invalid_utf8_state_is_observable_and_can_recover(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            path.write_bytes(b"\xff\xfe")
            gateway = NapCatQQGateway(state_path=path)
            self.assertEqual(gateway.status()["state_error"], "UnicodeDecodeError")
            path.write_text('{"character_pack_overrides": {"qq_pri_41": "reimu"}}', encoding="utf-8")
            gateway._load_persisted_state()
            self.assertEqual(gateway.status()["state_error"], "")
            self.assertEqual(gateway.resolve_character_pack_id("qq_pri_41"), "reimu")

    def test_care_and_poke_failures_retain_type_without_private_exception_text(self) -> None:
        sentinel = "private-path-and-token-marker"

        def unavailable(**_kwargs):
            raise RuntimeError(sentinel)

        gateway = NapCatQQGateway()
        care_module = SimpleNamespace(runtime=SimpleNamespace(snapshot_for_client=unavailable))
        context = QQMessageContext(
            True, "private", user_id=41, target_id=41, session_id="qq_pri_41",
            profile_user_id="qq:41", clean_message="/养成", character_pack_id="reimu",
        )
        result = gateway.handle_economy_command(context, care_module=care_module)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "care_command_failed")
        self.assertEqual(result["error"], "RuntimeError")
        self.assertNotIn(sentinel, json.dumps(result))
        poke = QQMessageContext(
            True, "qq_poke", user_id=41, target_id=41, session_id="qq_pri_41",
            profile_user_id="qq:41", character_pack_id="reimu",
        )
        outcome = gateway.handle_poke_event(poke, {"event_id": "failure-fixture"}, care_module=care_module)
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.reason, "poke_processing_failed:RuntimeError")
        self.assertNotIn(sentinel, str(outcome))

    def test_care_actions_use_each_requests_character_actor_and_timestamp(self) -> None:
        observed = []

        def snapshot(**kwargs):
            observed.append(kwargs)
            return {"hunger": 1, "energy": 2, "affection": 3, "coins": 4, "redeem_quota": 5}

        gateway = NapCatQQGateway()
        care_module = SimpleNamespace(runtime=SimpleNamespace(snapshot_for_client=snapshot))
        for user_id, pack, timestamp in ((41, "reimu", 1000), (42, "marisa", 2000)):
            context = QQMessageContext(
                True, "private", user_id=user_id, target_id=user_id, session_id=f"qq_pri_{user_id}",
                profile_user_id=f"profile:{user_id}", clean_message="/养成", character_pack_id=pack,
            )
            result = gateway.handle_economy_command(context, care_module=care_module, now_ms=timestamp)
            self.assertTrue(result["ok"])
        self.assertEqual(observed, [
            {"profile_user_id": "profile:41", "conversation_id": "qq_pri_41", "character_pack_id": "reimu", "client_mode": "qq_text", "relation_user_id": "qq:41", "now_ms": 1000},
            {"profile_user_id": "profile:42", "conversation_id": "qq_pri_42", "character_pack_id": "marisa", "client_mode": "qq_text", "relation_user_id": "qq:42", "now_ms": 2000},
        ])

    def test_gateway_owners_have_no_reverse_import_or_import_cycle(self) -> None:
        root = Path(__file__).resolve().parents[1]
        package = root / "companion_v01" / "channels" / "qq"
        modules = {
            ".".join(p.relative_to(root).with_suffix("").parts): p
            for p in package.rglob("*.py")
            if p.stem not in {"__init__", "gateway", "assembly"}
        }
        self.assertIn("companion_v01.channels.qq.inbound.context_builder", modules)
        graph = {name: set() for name in modules}

        def runtime_imports(node):
            if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
                for child in node.orelse:
                    yield from runtime_imports(child)
                return
            if isinstance(node, (ast.ImportFrom, ast.Import)):
                yield node
            for child in ast.iter_child_nodes(node):
                yield from runtime_imports(child)

        for name, path in modules.items():
            for node in runtime_imports(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom):
                    target = node.module or ""
                    self.assertNotIn(target, {"companion_v01.channels.qq.gateway", "companion_v01.channels.qq.assembly"}, name)
                    for dependency in [target, *(target + "." + alias.name for alias in node.names)]:
                        if dependency in modules:
                            graph[name].add(dependency)
                elif isinstance(node, ast.Import):
                    self.assertNotIn("companion_v01.channels.qq.gateway", [n.name for n in node.names], path.name)
                    graph[name].update(alias.name for alias in node.names if alias.name in modules)

        visited = set()
        active = set()

        def visit(name):
            self.assertNotIn(name, active, f"gateway owner import cycle: {name}")
            if name in visited:
                return
            active.add(name)
            for dependency in graph[name]:
                visit(dependency)
            active.remove(name)
            visited.add(name)

        for name in graph:
            visit(name)

    def test_attachment_debounce_state_is_owned_by_each_gateway(self) -> None:
        context = QQMessageContext(
            True, "private", user_id=41, target_id=41, session_id="qq_pri_41",
            profile_user_id="qq:41", character_pack_id="reimu",
        )
        first = NapCatQQGateway()
        second = NapCatQQGateway()
        with patch("companion_v01.channels.qq.gateway.config.QQ_ATTACHMENT_DEBOUNCE_SECONDS", 1.2):
            older = first.register_attachment_debounce(context, attachment_ids=["first-old"])
            latest = first.register_attachment_debounce(context, attachment_ids=["first-new"])
            other = second.register_attachment_debounce(context, attachment_ids=["second"])
        self.assertFalse(first.consume_attachment_debounce(older)["process"])
        self.assertEqual(second.consume_attachment_debounce(other)["attachment_ids"], ["second"])
        self.assertEqual(first.consume_attachment_debounce(latest)["attachment_ids"], ["first-old", "first-new"])


if __name__ == "__main__":
    unittest.main()
