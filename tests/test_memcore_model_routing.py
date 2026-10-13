from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from memcore import LLMRequest, MemCoreRuntime, TaskType
from companion_v01.memcore_integration.adapters import build_akane_llm_client
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.channels.qq.commands.handler import QQCommandHandler
from companion_v01.channels.qq.context import QQMessageContext
from tests.test_memcore_integration import _FakeEmbeddingProvider


class _Runtime:
    def __init__(self):
        self.calls = []

    def call_memcore_json(self, **kwargs):
        self.calls.append(kwargs)
        return {"summary": "usable"}


def _request():
    return LLMRequest(task_type=TaskType.SUMMARY, system_prompt="system", user_prompt="history", fallback={"summary": ""})


class MemcoreModelRoutingTests(unittest.TestCase):
    def test_old_turn_compaction_cannot_replace_an_explicit_model_switch_or_default(self):
        for operation in ("compact_due_background", "compact_due_sync"):
            for selected in ("new-model", ""):
                with self.subTest(operation=operation, selected=selected), tempfile.TemporaryDirectory() as temp_dir:
                    runtime = _Runtime()
                    manager = MemcoreManager(backend="memcore", storage_path=Path(temp_dir) / "memory.db",
                        visible_scope="conversation", enable_flavor=False, shadow_compare=False,
                        llm=runtime, embedding_provider=_FakeEmbeddingProvider())
                    scope = {"profile_user_id": "owner", "session_id": "group:a", "character_pack_id": "cecilia"}
                    try:
                        system = manager._get_system(**scope, warm_index=False)
                        manager.set_compaction_chat_model_override(**scope, chat_model_override=selected)
                        result = {"status": "not_due"}
                        if operation == "compact_due_background":
                            future = Future()
                            future.set_result(result)
                            result = future
                        with patch.object(system, operation, return_value=result):
                            self.assertTrue(getattr(manager, operation)(**scope, chat_model_override="old-model")["ok"])
                        self.assertTrue(system.llm.call(_request()).ok)
                        self.assertEqual(runtime.calls[-1]["chat_model_override"], selected)
                    finally:
                        manager.close()

    def test_turn_model_hints_follow_turns_without_an_explicit_session_choice(self):
        runtime = _Runtime()
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(backend="memcore", storage_path=Path(temp_dir) / "memory.db",
                visible_scope="conversation", enable_flavor=False, shadow_compare=False,
                llm=runtime, embedding_provider=_FakeEmbeddingProvider())
            scope = {"profile_user_id": "owner", "session_id": "group:a", "character_pack_id": "cecilia"}
            try:
                system = manager._get_system(**scope, warm_index=False)
                with patch.object(system, "compact_due_sync", return_value={"status": "not_due"}):
                    for model in ("first-model", "second-model", ""):
                        self.assertTrue(manager.compact_due_sync(**scope, chat_model_override=model)["ok"])
                        system.llm.call(_request())
            finally:
                manager.close()
        self.assertEqual([call["chat_model_override"] for call in runtime.calls], ["first-model", "second-model", ""])

    def test_namespace_model_switch_and_default_do_not_change_other_conversations(self):
        runtime = _Runtime()
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MemcoreManager(backend="memcore", storage_path=Path(temp_dir) / "memory.db",
                visible_scope="conversation", enable_flavor=False, shadow_compare=False,
                llm=runtime, embedding_provider=_FakeEmbeddingProvider())
            try:
                scopes = [{"profile_user_id": "owner", "session_id": session, "character_pack_id": "cecilia"}
                    for session in ("group:a", "private:owner")]
                systems = [manager._get_system(**scope, warm_index=False) for scope in scopes]
                manager.set_compaction_chat_model_override(**scopes[0], chat_model_override="group-model")
                manager.set_compaction_chat_model_override(**scopes[1], chat_model_override="private-model")
                systems[0].llm.call(_request())
                systems[1].llm.call(_request())
                manager.set_compaction_chat_model_override(**scopes[0], chat_model_override="switched-group-model")
                systems[0].llm.call(_request())
                systems[1].llm.call(_request())
                manager.set_compaction_chat_model_override(**scopes[0], chat_model_override="")
                systems[0].llm.call(_request())
            finally:
                manager.close()
        self.assertEqual([call["chat_model_override"] for call in runtime.calls], [
            "group-model", "private-model", "switched-group-model", "private-model", "",
        ])

    def test_queued_background_compaction_uses_updated_session_model(self):
        runtime = _Runtime()
        workers = MemCoreRuntime(compaction_workers=1, index_workers=1)
        ready, release = threading.Event(), threading.Event()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                manager = MemcoreManager(backend="memcore", storage_path=Path(temp_dir) / "memory.db",
                    visible_scope="conversation", enable_flavor=False, shadow_compare=False,
                    llm=runtime, embedding_provider=_FakeEmbeddingProvider(), runtime=workers)
                scope = {"profile_user_id": "owner", "session_id": "group:a", "character_pack_id": "cecilia"}
                system = manager._get_system(**scope, warm_index=False)
                def compact():
                    ready.set()
                    if not release.wait(timeout=3):
                        raise AssertionError("test worker not released")
                    self.assertTrue(system.llm.call(_request()).ok)
                    return {"status": "not_due"}
                try:
                    with patch.object(system, "compact_due_background", side_effect=lambda **_kwargs: workers.submit_compaction(compact)):
                        result = manager.compact_due_background(**scope, provider_profile="openai", chat_model_override="old-model")
                        self.assertEqual(result["status"], "scheduled")
                        self.assertTrue(ready.wait(timeout=1))
                        future = next(iter(manager._background_compactions.values()))
                        manager.set_compaction_chat_model_override(**scope, chat_model_override="new-model")
                        release.set()
                        self.assertEqual(future.result(timeout=2)["status"], "not_due")
                        self.assertTrue(manager.close())
                finally:
                    release.set()
                    manager.close()
        finally:
            release.set()
            workers.close()
        self.assertEqual([call["chat_model_override"] for call in runtime.calls], ["new-model"])

    @patch("companion_v01.memcore_integration.adapters.time.sleep")
    def test_retry_resolves_current_session_model_again(self, _sleep):
        runtime = _Runtime()
        selected = ["old-model"]
        original = runtime.call_memcore_json
        def call(**kwargs):
            data = original(**kwargs)
            if len(runtime.calls) == 1:
                selected[0] = "new-model"
                return kwargs["fallback"]
            return data
        runtime.call_memcore_json = call
        client = build_akane_llm_client(runtime, chat_model_override_provider=lambda: selected[0])
        result = client.call(_request())
        self.assertTrue(result.ok)
        self.assertEqual(result.attempts, 2)
        self.assertEqual([call["chat_model_override"] for call in runtime.calls], ["old-model", "new-model"])


class QQSummaryModelSwitchTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_commands_update_only_successful_switch_and_default(self):
        context = QQMessageContext(should_respond=True, reason="command", profile_user_id="owner", session_id="group:a",
            character_pack_id="cecilia", clean_message="/模型 new-model")
        for status, ok, model in (("switched", True, "new-model"), ("default", True, ""),
                                  ("listed", True, ""), ("forbidden", False, "")):
            with self.subTest(status=status):
                setter = Mock()
                handler = QQCommandHandler.__new__(QQCommandHandler)
                handler.engine = SimpleNamespace(memcore_manager=SimpleNamespace(set_compaction_chat_model_override=setter))
                handler.config_module = SimpleNamespace(CHAT_MODEL_NAME="default-model")
                handler.qq_gateway = SimpleNamespace(
                    parse_chat_model_command=lambda _text: {"action": "switch"},
                    handle_chat_model_command=lambda *_args, **_kwargs: {"ok": ok, "status": status, "chat_model_override": model},
                )
                handler._reply_to_result = AsyncMock(return_value="reply")
                with patch("companion_v01.channels.qq.commands.handler.effective_settings_from_config"), \
                     patch("companion_v01.channels.qq.commands.handler.probe_model_ids", return_value=["new-model"]):
                    await handler._handle_chat_model(context=context, command_context=context, started_at=0)
                if status in {"switched", "default"}:
                    setter.assert_called_once_with(profile_user_id="owner", session_id="group:a",
                        character_pack_id="cecilia", chat_model_override=model)
                else:
                    setter.assert_not_called()


if __name__ == "__main__":
    unittest.main()
