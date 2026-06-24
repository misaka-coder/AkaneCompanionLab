from __future__ import annotations

import unittest

from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.tool_runtime import TOOL_METADATA_BY_TYPE


class NativeToolSchemaTests(unittest.TestCase):
    def test_build_openai_native_tool_specs_from_handlers(self) -> None:
        class FakeHandler:
            tool_type = "web_search"

            def build_prompt_instruction(self) -> str:
                return "- web_search：搜索公开网页，参数为 query 和 max_results。"

        specs = build_openai_native_tool_specs({"web_search": FakeHandler()})

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["type"], "function")
        self.assertEqual(specs[0]["function"]["name"], "web_search")
        self.assertIn("搜索公开网页", specs[0]["function"]["description"])
        self.assertEqual(specs[0]["function"]["parameters"]["type"], "object")

    def test_build_openai_native_tool_specs_filters_allowed_and_invalid_names(self) -> None:
        class SearchHandler:
            tool_type = "web_search"

            def build_prompt_instruction(self) -> str:
                return "search"

        class BadHandler:
            tool_type = "bad.tool"

            def build_prompt_instruction(self) -> str:
                return "bad"

        specs = build_openai_native_tool_specs(
            {
                "web_search": SearchHandler(),
                "bad.tool": BadHandler(),
                "ignored": SearchHandler(),
            },
            allowed_tool_names={"web_search"},
        )

        self.assertEqual([item["function"]["name"] for item in specs], ["web_search"])

    def test_build_openai_native_tool_specs_prefers_metadata_input_schema(self) -> None:
        class MemoryHandler:
            tool_type = "retrieve_memory"

            def tool_metadata(self):
                return TOOL_METADATA_BY_TYPE["retrieve_memory"]

            def build_prompt_instruction(self) -> str:
                return "legacy prompt mentions tool_call and should not be used"

        specs = build_openai_native_tool_specs({"retrieve_memory": MemoryHandler()})

        self.assertEqual(len(specs), 1)
        function = specs[0]["function"]
        self.assertEqual(function["name"], "retrieve_memory")
        self.assertNotIn("tool_call", function["description"])
        self.assertIn("long-term memory", function["description"])
        self.assertEqual(function["parameters"]["additionalProperties"], False)
        self.assertIn("query", function["parameters"]["required"])
        self.assertNotIn("description", function["parameters"])


if __name__ == "__main__":
    unittest.main()
