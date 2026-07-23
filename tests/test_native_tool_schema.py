from __future__ import annotations

import unittest

from memcore import build_native_memory_tool_specs

from companion_v01.capability_adapters import CapabilityDescriptor, CapabilityIOSlot
from companion_v01.capability_registry import RETRIEVE_MEMORY_TOOL_SPEC
from companion_v01.native_tool_schema import NATIVE_TOOL_CAPABILITY_ID_FIELD, build_openai_native_tool_specs
from companion_v01.tool_runtime import AdapterCapabilityToolHandler, TOOL_METADATA_BY_TYPE


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

            def tool_spec(self):
                return RETRIEVE_MEMORY_TOOL_SPEC

            def tool_metadata(self):
                return TOOL_METADATA_BY_TYPE["retrieve_memory"]

            def build_prompt_instruction(self) -> str:
                return "legacy prompt mentions tool_call and should not be used"

        specs = build_openai_native_tool_specs({"retrieve_memory": MemoryHandler()})

        self.assertEqual(len(specs), 1)
        function = specs[0]["function"]
        self.assertEqual(function["name"], "retrieve_memory")
        self.assertNotIn("tool_call", function["description"])
        self.assertIn("Fuzzy memory search", function["description"])
        self.assertEqual(function["parameters"]["additionalProperties"], False)
        self.assertIn("query", function["parameters"]["required"])
        self.assertIn("include_explicit", function["parameters"]["properties"])
        self.assertIn("kind_patterns", function["parameters"]["properties"])
        self.assertIn("entity_anchors", function["parameters"]["properties"])
        self.assertIn("topic_terms", function["parameters"]["properties"])
        self.assertIn("memory_facets", function["parameters"]["properties"])
        self.assertIn("about_roles", function["parameters"]["properties"])
        self.assertNotIn("keywords", function["parameters"]["properties"])
        self.assertNotIn("categories", function["parameters"]["properties"])
        self.assertNotIn("importance_min", function["parameters"]["properties"])
        self.assertNotIn("limit", function["parameters"]["properties"])
        self.assertNotIn("description", function["parameters"])

    def test_memory_capability_properties_are_package_owned(self) -> None:
        package_spec = next(
            item
            for item in build_native_memory_tool_specs(
                tool_format="plain",
                include_material_tool=False,
            )
            if item["name"] == "retrieve_for_turn"
        )

        self.assertEqual(
            RETRIEVE_MEMORY_TOOL_SPEC.input_schema["properties"],
            package_spec["parameters"]["properties"],
        )
        self.assertEqual(
            RETRIEVE_MEMORY_TOOL_SPEC.input_schema["required"],
            package_spec["parameters"]["required"],
        )

    def test_adapter_capability_native_schema_uses_capcore_projection(self) -> None:
        descriptor = CapabilityDescriptor(
            id="mcp_demo_echo",
            display_name="echo",
            short_hint="Echo text",
            visible_in=("desktop",),
            prompt_exposed=True,
            risk="low",
            confirm="never",
            effects=(),
            trigger=None,
            inputs=(
                CapabilityIOSlot(
                    name="text",
                    kind="string",
                    required=True,
                    raw={"description": "Text to echo"},
                ),
            ),
            outputs=(),
            raw={
                "inputSchema": {
                    "type": "object",
                    "properties": {"wrong": {"type": "string"}},
                    "required": ["wrong"],
                }
            },
        )
        handler = AdapterCapabilityToolHandler(
            capability_id="mcp_demo_echo",
            adapter=object(),
            descriptor=descriptor,
            config_base_dir="unused",
        )

        specs = build_openai_native_tool_specs({"mcp_demo_echo": handler}, allowed_tool_names={"mcp_demo_echo"})

        self.assertEqual(len(specs), 1)
        function = specs[0]["function"]
        self.assertEqual(function["name"], "mcp_demo_echo")
        self.assertNotIn("tool_call", function["description"])
        self.assertNotIn("wrong", function["description"])
        self.assertEqual(function["parameters"]["additionalProperties"], False)
        self.assertEqual(function["parameters"]["required"], ["text"])
        self.assertEqual(function["parameters"]["properties"]["text"]["type"], "string")
        self.assertEqual(function["parameters"]["properties"]["text"]["description"], "Text to echo")
        self.assertNotIn("wrong", function["parameters"]["properties"])
        self.assertEqual(specs[0][NATIVE_TOOL_CAPABILITY_ID_FIELD], "mcp_demo_echo")

    def test_dotted_adapter_capability_native_schema_uses_provider_safe_name_mapping(self) -> None:
        descriptor = CapabilityDescriptor(
            id="mcp.demo.echo",
            display_name="echo",
            short_hint="Echo text",
            visible_in=("desktop",),
            prompt_exposed=True,
            risk="low",
            confirm="never",
            effects=(),
            trigger=None,
            inputs=(
                CapabilityIOSlot(
                    name="text",
                    kind="string",
                    required=True,
                    raw={"description": "Text to echo"},
                ),
            ),
            outputs=(),
            raw={},
        )
        handler = AdapterCapabilityToolHandler(
            capability_id="mcp.demo.echo",
            adapter=object(),
            descriptor=descriptor,
            config_base_dir="unused",
        )

        specs = build_openai_native_tool_specs({"mcp.demo.echo": handler}, allowed_tool_names={"mcp.demo.echo"})

        self.assertEqual(len(specs), 1)
        function = specs[0]["function"]
        self.assertRegex(function["name"], r"^mcp_demo_echo_[0-9a-f]{10}$")
        self.assertEqual(specs[0][NATIVE_TOOL_CAPABILITY_ID_FIELD], "mcp.demo.echo")
        self.assertEqual(function["parameters"]["required"], ["text"])


if __name__ == "__main__":
    unittest.main()
