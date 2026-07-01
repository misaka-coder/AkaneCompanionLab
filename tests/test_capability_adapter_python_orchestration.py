from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.tool_runtime import BaseToolHandler, ToolExecutionContext, ToolExecutionResult


class StubStore:
    def list_attachment_inbox_items(self, **kwargs):
        return []

    def list_generated_files(self, **kwargs):
        return []


class StubWebSearchHandler(BaseToolHandler):
    tool_type = "web_search"

    def build_prompt_instruction(self) -> str:
        return "- web_search：stub"

    def normalize_call(self, value):
        return value if isinstance(value, dict) and value.get("type") == self.tool_type else None

    def execute(self, *, call: dict, context: ToolExecutionContext) -> ToolExecutionResult:
        return ToolExecutionResult(tool_type=self.tool_type, followup_context="ok")


def context() -> ClientProtocolContext:
    return ClientProtocolContext(
        requested_mode=ClientMode.DESKTOP_PET,
        effective_mode=ClientMode.DESKTOP_PET,
    )


def execution_context() -> ToolExecutionContext:
    return ToolExecutionContext(
        profile_user_id="alice",
        session_id="s1",
        now_ts=1712400000,
        visual_payload={},
        client_mode="desktop_pet",
    )


def build_engine() -> AkaneMemoryEngine:
    engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
    engine.tool_handlers = {"web_search": StubWebSearchHandler()}
    engine.store = StubStore()
    return engine


class CapabilityAdapterPythonOrchestrationTests(unittest.TestCase):
    def test_python_adapter_tools_are_profile_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            engine = build_engine()
            no_profile = engine._resolve_tool_handlers(client_context=context(), profile_user_id="", session_id="s1")
            alice = engine._resolve_tool_handlers(client_context=context(), profile_user_id="alice", session_id="s1")

        self.assertNotIn("python.akane.normalize_text", no_profile)
        self.assertIn("python.akane.normalize_text", alice)
        self.assertIn("python.akane.extract_semantic_tags", alice)
        self.assertIn("python.akane.detect_time_of_day", alice)

    def test_python_adapter_tool_executes_through_capcore_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            handler = build_engine()._resolve_tool_handlers(
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )["python.akane.normalize_text"]
            call = handler.normalize_call({"type": "python.akane.normalize_text", "text": "  hello   world  "})
            assert call is not None
            result = handler.execute(call=call, context=execution_context())

        self.assertEqual(result.stream_events[0]["type"], "adapter_capability_completed")
        self.assertEqual(result.stream_events[0]["status"], "ok")
        self.assertIn("本地 Python 能力返回", result.followup_context)
        self.assertIn("hello world", result.followup_context)

    def test_python_adapter_tool_uses_capcore_schema_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            handler = build_engine()._resolve_tool_handlers(
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )["python.akane.extract_semantic_tags"]
            result = handler.execute(
                call={
                    "type": "python.akane.extract_semantic_tags",
                    "arguments": {"text": "Akane Akane project", "limit": 999},
                },
                context=execution_context(),
            )

        self.assertEqual(result.stream_events[0]["type"], "adapter_capability_failed")
        self.assertEqual(result.stream_events[0]["status"], "validation_error")
        self.assertEqual(result.stream_events[0]["reason"], "maximum_violation")

    def test_python_adapter_prompt_is_not_labeled_as_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("config.DATA_DIR", temp_dir):
            prompt = build_engine()._build_tool_prompt_context(
                allow_tool_call=True,
                client_context=context(),
                profile_user_id="alice",
                session_id="s1",
            )

        self.assertIn("python.akane.detect_time_of_day", prompt)
        self.assertIn("该能力来自本地 Python 能力", prompt)
        self.assertNotIn("该能力来自本地 MCP server", prompt)


if __name__ == "__main__":
    unittest.main()
