from __future__ import annotations

import json
import unittest

from capcore import CapabilityToolSpec

from companion_v01.legacy_tool_prompt import render_legacy_json_tool_instruction
from companion_v01.native_tool_schema import build_openai_native_tool_from_spec
from companion_v01.tool_handlers.core import BaseToolHandler
from companion_v01.tool_handlers.memory import (
    BrowseMemoryToolHandler,
    OpenMemoryToolHandler,
    ReadMemoryTimelineToolHandler,
    RetrieveMemoryToolHandler,
)
from companion_v01.tool_handlers.qq_onebot import OneBotActionToolHandler
from companion_v01.tool_handlers.web_browser import WebSearchToolHandler


class LegacyToolPromptTests(unittest.TestCase):
    def _handlers(self):
        return (
            WebSearchToolHandler(),
            RetrieveMemoryToolHandler(retrieve_fn=lambda **_kwargs: None),
            ReadMemoryTimelineToolHandler(timeline_service=None),
            BrowseMemoryToolHandler(timeline_service=None),
            OpenMemoryToolHandler(timeline_service=None),
            OneBotActionToolHandler(),
        )

    def test_first_migration_batch_uses_base_canonical_renderer(self) -> None:
        for handler in self._handlers():
            with self.subTest(tool=handler.tool_type):
                self.assertIs(handler.__class__.build_prompt_instruction, BaseToolHandler.build_prompt_instruction)

    def test_legacy_and_native_share_exact_canonical_description(self) -> None:
        for handler in self._handlers():
            with self.subTest(tool=handler.tool_type):
                spec = handler.tool_spec()
                legacy = handler.build_prompt_instruction()
                native = build_openai_native_tool_from_spec(spec)["function"]
                self.assertIn(spec.description, legacy)
                self.assertEqual(native["description"], spec.description)
                self.assertIn(f'"type":"{spec.capability_id}"', legacy)
                self.assertEqual(json.loads(legacy.split("业务参数完整 JSON Schema：", 1)[1]), spec.input_schema)

    def test_legacy_schema_preserves_every_field_and_constraint(self) -> None:
        for handler in self._handlers():
            with self.subTest(tool=handler.tool_type):
                spec = handler.tool_spec()
                instruction = handler.build_prompt_instruction()
                projected = json.loads(instruction.split("业务参数完整 JSON Schema：", 1)[1])
                self.assertEqual(projected, spec.input_schema)
                self.assertEqual(list(projected["properties"]), list(spec.input_schema["properties"]))
                self.assertEqual(instruction, handler.build_prompt_instruction())

    def test_long_enum_remains_callable_in_the_legacy_lane(self) -> None:
        instruction = OneBotActionToolHandler().build_prompt_instruction()

        projected = json.loads(instruction.split("业务参数完整 JSON Schema：", 1)[1])
        self.assertEqual(projected, OneBotActionToolHandler().tool_spec().input_schema)
        self.assertIn("Use capabilities", instruction)
        self.assertIn("get_recent_contact", instruction)

    def test_web_search_cursor_only_continuation_is_not_blocked_by_schema(self) -> None:
        handler = WebSearchToolHandler()
        spec = handler.tool_spec()

        self.assertEqual(spec.input_schema["required"], [])
        self.assertIn("unless cursor is supplied alone", spec.input_schema["properties"]["action"]["description"])
        self.assertEqual(
            handler.normalize_call({"type": "web_search", "cursor": "opaque-next-page"}),
            {"type": "web_search", "cursor": "opaque-next-page"},
        )
        self.assertIsNone(handler.normalize_call({"type": "web_search"}))

    def test_schema_is_projected_once_instead_of_a_second_partial_signature(self) -> None:
        rendered = [handler.build_prompt_instruction() for handler in self._handlers()]

        self.assertTrue(all(item.count("业务参数完整 JSON Schema：") == 1 for item in rendered))
        self.assertTrue(all("参数字段（! 为必填）" not in item for item in rendered))

    def test_renderer_rejects_noncanonical_input(self) -> None:
        with self.assertRaisesRegex(TypeError, "canonical_tool_spec_required"):
            render_legacy_json_tool_instruction(object())  # type: ignore[arg-type]
        empty_id = CapabilityToolSpec(
            capability_id=" ",
            display_name="empty",
            description="empty",
            input_schema={"type": "object", "properties": {}, "required": []},
            risk="low",
            confirm="never",
            effects=(),
            visible_in=(),
        )
        with self.assertRaisesRegex(ValueError, "canonical_tool_spec_id_required"):
            render_legacy_json_tool_instruction(empty_id)


if __name__ == "__main__":
    unittest.main()
