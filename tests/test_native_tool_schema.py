from __future__ import annotations

import unittest

from companion_v01.native_tool_schema import build_openai_native_tool_specs


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


if __name__ == "__main__":
    unittest.main()
