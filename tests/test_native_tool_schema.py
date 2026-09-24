from __future__ import annotations

import inspect
import unittest
from dataclasses import replace
from unittest.mock import patch

from memcore import build_native_memory_tool_specs
from capcore import CapabilityToolSpec, SchemaDefinitionError

from companion_v01.capability_adapters import CapabilityDescriptor, CapabilityIOSlot
from companion_v01 import capability_registry
from companion_v01.capability_registry import (
    BROWSE_MEMORY_TOOL_SPEC,
    OPEN_MEMORY_TOOL_SPEC,
    READ_MEMORY_TIMELINE_TOOL_SPEC,
    RETRIEVE_MEMORY_TOOL_SPEC,
)
from companion_v01.generated_files import GeneratedFileService
from companion_v01.native_tool_schema import NATIVE_TOOL_CAPABILITY_ID_FIELD, build_openai_native_tool_specs
from companion_v01.tool_orchestration_engine import native_legacy_prompt_exclusions
from companion_v01.tool_runtime import (
    AdapterCapabilityToolHandler,
    SendFileToolHandler,
    TOOL_METADATA_BY_TYPE,
    TOOL_SPEC_BY_TYPE,
)
from companion_v01.tool_handlers.catalog import build_builtin_tool_handlers


class NativeToolSchemaTests(unittest.TestCase):
    def test_missing_memcore_native_contract_has_actionable_error(self) -> None:
        with patch.object(
            capability_registry,
            "build_native_memory_tool_specs",
            return_value=[{"name": "retrieve_for_turn"}],
        ):
            for tool_name in ("browse_memory", "open_memory"):
                with self.subTest(tool_name=tool_name):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        rf"^memcore_native_tool_contract_missing:{tool_name}$",
                    ):
                        capability_registry._memcore_tool_contract(tool_name, tool_name)

    def test_build_openai_native_tool_specs_from_handlers(self) -> None:
        class FakeHandler:
            tool_type = "web_search"

            def tool_spec(self):
                return TOOL_SPEC_BY_TYPE["web_search"]

        specs = build_openai_native_tool_specs({"web_search": FakeHandler()})

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["type"], "function")
        self.assertEqual(specs[0]["function"]["name"], "web_search")
        self.assertEqual(
            specs[0]["function"]["description"],
            TOOL_SPEC_BY_TYPE["web_search"].description,
        )
        self.assertEqual(specs[0]["function"]["parameters"]["type"], "object")

    def test_build_openai_native_tool_specs_filters_allowed_names(self) -> None:
        class SearchHandler:
            tool_type = "web_search"

            def tool_spec(self):
                return TOOL_SPEC_BY_TYPE["web_search"]

        class BadHandler:
            tool_type = "bad.tool"

            def tool_spec(self):
                return None

        specs = build_openai_native_tool_specs(
            {
                "web_search": SearchHandler(),
                "bad.tool": BadHandler(),
                "ignored": SearchHandler(),
            },
            allowed_tool_names={"web_search"},
        )

        self.assertEqual([item["function"]["name"] for item in specs], ["web_search"])

    def test_native_projection_does_not_reconstruct_legacy_handler_semantics(self) -> None:
        class LegacyOnlyHandler:
            tool_type = "legacy_only"

            def tool_metadata(self):
                return TOOL_METADATA_BY_TYPE["web_search"]

            def build_prompt_instruction(self) -> str:
                return "legacy prose must not become a native schema"

        self.assertEqual(
            build_openai_native_tool_specs({"legacy_only": LegacyOnlyHandler()}),
            [],
        )

    def test_every_builtin_handler_has_a_canonical_capcore_tool_spec(self) -> None:
        execution_provider = type("ExecutionProvider", (), {"workspace_root": None})()
        handlers = build_builtin_tool_handlers(
            store=object(),
            sticker_assets=object(),
            capability_offer_source=object(),
            capability_config_base_dir=".",
            memory_timeline_service=object(),
            context_libraries=object(),
            attachment_service=object(),
            image_material_resolver=object(),
            workspace_file_service=object(),
            attachment_ingest_service=object(),
            generated_file_service=object(),
            retrieve_fn=lambda *_args, **_kwargs: None,
            skill_registry=object(),
            execution_provider=execution_provider,
            approval_store=object(),
            project_workspace_service=object(),
        )

        missing = [
            name
            for name, handler in handlers.items()
            if not isinstance(handler.tool_spec(), CapabilityToolSpec)
        ]
        self.assertEqual(len(handlers), 41)
        self.assertNotIn("cover_song", handlers)
        self.assertNotIn("generate_image", handlers)
        self.assertNotIn("convert_media_file", handlers)
        self.assertNotIn("separate_audio_stems", handlers)
        self.assertEqual(missing, [])

    def test_retired_tools_are_absent_and_sticker_has_one_exact_contract(self) -> None:
        from companion_v01.capability_registry import CapabilityRegistry, SEND_STICKER_TOOL_SPEC
        from companion_v01.client_protocol import ClientMode

        retired = {
            "call_npc",
            "check_inventory",
            "manage_gift",
            "manage_artifact",
            "manage_persona",
            "focus_workspace",
            "sync_attachment_workspace",
        }
        registry = CapabilityRegistry()
        for mode in ClientMode:
            self.assertTrue(retired.isdisjoint(registry.tool_names_for_mode(mode)))

        schema = SEND_STICKER_TOOL_SPEC.input_schema
        self.assertEqual(schema["required"], ["sticker"])
        self.assertEqual(set(schema["properties"]), {"sticker"})

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
        explicit_description = function["parameters"]["properties"]["include_explicit"]["description"]
        kind_description = function["parameters"]["properties"]["kind_patterns"]["description"]
        self.assertIn("WITH kind_patterns", explicit_description)
        self.assertIn("true without kind_patterns is rejected", explicit_description)
        self.assertIn("Only valid together with include_explicit=true", kind_description)
        self.assertIn("entity_anchors", function["parameters"]["properties"])
        self.assertIn("topic_terms", function["parameters"]["properties"])
        self.assertIn("memory_facets", function["parameters"]["properties"])
        self.assertIn("about_roles", function["parameters"]["properties"])
        self.assertNotIn("keywords", function["parameters"]["properties"])
        self.assertNotIn("categories", function["parameters"]["properties"])
        self.assertNotIn("importance_min", function["parameters"]["properties"])
        self.assertNotIn("limit", function["parameters"]["properties"])
        self.assertNotIn("description", function["parameters"])

    def test_send_file_native_schema_prefers_exact_handle_over_latest(self) -> None:
        specs = build_openai_native_tool_specs(
            {"send_file": SendFileToolHandler(generated_file_service=object())},
            allowed_tool_names={"send_file"},
        )

        self.assertEqual(len(specs), 1)
        function = specs[0]["function"]
        self.assertIn("必须传入那个精确句柄", function["description"])
        self.assertIn("latest_generated", function["description"])
        self.assertIn("latest_attachment", function["description"])
        self.assertIn(
            "Reuse handles returned by prior tools",
            function["parameters"]["properties"]["targets"]["description"],
        )

    def test_memory_capability_properties_are_package_owned(self) -> None:
        aliases = {
            "retrieve_for_turn": "retrieve_memory",
            "read_timeline": "read_memory_timeline",
        }

        def project_names(value):
            if isinstance(value, dict):
                return {key: project_names(item) for key, item in value.items()}
            if isinstance(value, list):
                return [project_names(item) for item in value]
            if isinstance(value, str):
                for source_name, target_name in aliases.items():
                    value = value.replace(source_name, target_name)
            return value

        package_specs = {
            item["name"]: item
            for item in build_native_memory_tool_specs(
                tool_format="plain",
                include_material_tool=False,
            )
        }
        for product_spec, package_name in (
            (RETRIEVE_MEMORY_TOOL_SPEC, "retrieve_for_turn"),
            (READ_MEMORY_TIMELINE_TOOL_SPEC, "read_timeline"),
            (BROWSE_MEMORY_TOOL_SPEC, "browse_memory"),
            (OPEN_MEMORY_TOOL_SPEC, "open_memory"),
        ):
            with self.subTest(package_name=package_name):
                package_spec = package_specs[package_name]
                self.assertEqual(
                    product_spec.input_schema["properties"],
                    project_names(package_spec["parameters"]["properties"]),
                )
                self.assertEqual(
                    product_spec.input_schema["required"],
                    package_spec["parameters"]["required"],
                )
                rendered_contract = repr(
                    {
                        "description": product_spec.description,
                        "schema": product_spec.input_schema,
                    }
                )
                self.assertNotIn("retrieve_for_turn", rendered_contract)
                self.assertNotIn("read_timeline", rendered_contract)
                if package_name not in {"browse_memory", "open_memory"}:
                    self.assertNotIn(package_name, rendered_contract)

    def test_retrieve_memory_schema_exposes_exact_time_without_unknown_answer_anchor(self) -> None:
        time_hint = RETRIEVE_MEMORY_TOOL_SPEC.input_schema["properties"]["time_hint"]

        self.assertIn("start_at", time_hint["properties"])
        self.assertIn("end_at", time_hint["properties"])
        self.assertIn("inclusive", time_hint["description"])
        self.assertIn("exclusive", time_hint["description"])
        self.assertIn("must not be guessed", RETRIEVE_MEMORY_TOOL_SPEC.description)

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

        invalid = AdapterCapabilityToolHandler(
            capability_id=descriptor.id, adapter=object(),
            descriptor=replace(descriptor, inputs=(), input_schema={"type": "object", "minProperties": "many"}),
        )
        for projection in (invalid.tool_spec, invalid.tool_metadata, invalid.build_prompt_instruction):
            with self.assertRaises(SchemaDefinitionError) as caught:
                projection()
            self.assertEqual(caught.exception.code, "invalid_schema")
        self.assertEqual(build_openai_native_tool_specs({descriptor.id: invalid}), [])

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
        self.assertEqual(native_legacy_prompt_exclusions(specs), {"mcp.demo.echo"})

    def test_file_native_specs_match_the_arguments_handlers_actually_consume(self) -> None:
        send = SendFileToolHandler(generated_file_service=None)
        send_call = send.normalize_call(
            {
                "type": "send_file",
                "targets": ["file_001", "gen_001"],
                "delivery_action": "reveal",
            }
        )
        self.assertEqual(send_call["targets"], ["file_001", "gen_001"])
        self.assertEqual(send_call["delivery_action"], "reveal")

        for tool_name, handler in (
            ("send_file", send),
        ):
            native = build_openai_native_tool_specs({tool_name: handler})[0]["function"]
            self.assertEqual(
                native["parameters"]["properties"],
                TOOL_SPEC_BY_TYPE[tool_name].input_schema["properties"],
            )
            self.assertNotIn("格式为", native["description"])
            self.assertNotIn("tool_call", native["description"])

if __name__ == "__main__":
    unittest.main()
