from __future__ import annotations

import unittest

from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.tool_runtime import BaseToolHandler, ToolExecutionContext, ToolExecutionResult


class StubStore:
    def list_attachment_inbox_items(self, **kwargs):
        return []

    def list_generated_files(self, **kwargs):
        return []


class StrictWebSearchHandler(BaseToolHandler):
    """Only accepts a web_search call that also carries a non-empty query."""

    tool_type = "web_search"

    def build_prompt_instruction(self) -> str:
        return "- web_search: needs query"

    def normalize_call(self, value):
        if isinstance(value, dict) and value.get("type") == self.tool_type and str(value.get("query") or "").strip():
            return value
        return None

    def execute(self, *, call: dict, context: ToolExecutionContext) -> ToolExecutionResult:
        return ToolExecutionResult(tool_type=self.tool_type, followup_context="ok")


def _context() -> ClientProtocolContext:
    return ClientProtocolContext(
        requested_mode=ClientMode.DESKTOP_PET,
        effective_mode=ClientMode.DESKTOP_PET,
    )


def _engine() -> AkaneMemoryEngine:
    engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
    engine.tool_handlers = {"web_search": StrictWebSearchHandler()}
    engine.store = StubStore()
    return engine


class ToolCallRejectionTests(unittest.TestCase):
    def _reject(self, value):
        return _engine()._describe_tool_call_rejection(
            value,
            client_context=_context(),
            profile_user_id="alice",
            session_id="s1",
        )

    def test_null_or_empty_is_not_a_rejection(self) -> None:
        self.assertEqual(self._reject(None), "")
        self.assertEqual(self._reject({}), "")
        self.assertEqual(self._reject({"type": ""}), "")

    def test_valid_call_is_not_a_rejection(self) -> None:
        self.assertEqual(self._reject({"type": "web_search", "query": "天气"}), "")

    def test_unknown_tool_explains_and_lists_available(self) -> None:
        reason = self._reject({"type": "make_coffee", "size": "large"})
        self.assertTrue(reason)
        self.assertIn("make_coffee", reason)
        # the model should be told which tools it can actually use this turn
        self.assertIn("web_search", reason)

    def test_invalid_args_explains_the_attempt(self) -> None:
        reason = self._reject({"type": "web_search"})  # missing required query
        self.assertTrue(reason)
        self.assertIn("web_search", reason)
        self.assertIn("参数", reason)


if __name__ == "__main__":
    unittest.main()
