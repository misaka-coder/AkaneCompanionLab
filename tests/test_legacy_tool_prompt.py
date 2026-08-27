from __future__ import annotations

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
                self.assertNotIn('"description"', legacy)

    def test_legacy_signature_projects_every_top_level_field_in_schema_order(self) -> None:
        for handler in self._handlers():
            with self.subTest(tool=handler.tool_type):
                spec = handler.tool_spec()
                instruction = handler.build_prompt_instruction()
                signature = instruction.split("参数字段（! 为必填）：", 1)[1]
                positions = [signature.index(f"{name}:") for name in spec.input_schema["properties"]]
                self.assertEqual(positions, sorted(positions))
                self.assertEqual(instruction, handler.build_prompt_instruction())

    def test_long_enum_is_counted_instead_of_bloating_stable_prompt(self) -> None:
        instruction = OneBotActionToolHandler().build_prompt_instruction()

        self.assertIn("action:string![enum_count=23]", instruction)
        self.assertIn("Use capabilities", instruction)
        self.assertNotIn("get_recent_contact", instruction)

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

    def test_first_batch_stays_bounded_without_full_schema_duplication(self) -> None:
        rendered = [handler.build_prompt_instruction() for handler in self._handlers()]

        self.assertLessEqual(sum(map(len, rendered)), 6800)
        self.assertTrue(all('"properties"' not in item for item in rendered))

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
