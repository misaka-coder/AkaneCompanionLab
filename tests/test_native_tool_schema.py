from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from memcore import build_native_memory_tool_specs
from capcore import CapabilityToolSpec

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
    ApplyStyleToExistingFileToolHandler,
    CleanVoiceTrackToolHandler,
    ComposeFileToolHandler,
    PrepareVoiceDatasetToolHandler,
    ReviseGeneratedFileToolHandler,
    SendFileToolHandler,
    TOOL_METADATA_BY_TYPE,
    TOOL_SPEC_BY_TYPE,
    TranscribeMediaToolHandler,
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
            image_generation_service=object(),
            cover_song_service=object(),
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
        self.assertEqual(len(handlers), 49)
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
        compose = ComposeFileToolHandler(generated_file_service=None)
        revised = ReviseGeneratedFileToolHandler(generated_file_service=None)
        styled = ApplyStyleToExistingFileToolHandler(generated_file_service=None)
        send = SendFileToolHandler(generated_file_service=None)

        compose_call = compose.normalize_call(
            {
                "type": "compose_file",
                "source_ids": ["file_001"],
                "task": "整理报告",
                "output_format": "docx",
                "output_title": "报告",
                "structure": "report",
                "style": "formal",
                "fidelity": "preserve",
                "content_markdown": "# 报告",
                "table_rows": [["列", "值"], ["A", "1"]],
                "formatting": {"header": {"bold": True}},
            }
        )
        self.assertEqual(compose_call["task"], "整理报告")
        self.assertEqual(compose_call["output_format"], "docx")
        self.assertEqual(compose_call["content_markdown"], "# 报告")
        self.assertEqual(compose_call["formatting"], {"header": {"bold": True}})

        revise_call = revised.normalize_call(
            {
                "type": "revise_generated_file",
                "target": "gen_001",
                "instruction": "补一段总结",
                "output_format": "pdf",
                "output_title": "报告修订版",
                "content_markdown": "# 修订版",
                "table_rows": [["列", "值"]],
                "formatting": {"header": {"bold": True}},
            }
        )
        self.assertEqual(revise_call["instruction"], "补一段总结")
        self.assertEqual(revise_call["output_format"], "pdf")
        self.assertEqual(revise_call["content_markdown"], "# 修订版")

        style_call = styled.normalize_call(
            {
                "type": "apply_style_to_existing_file",
                "target": "file_001",
                "target_type": "attachment",
                "instruction": "姓名列标红",
                "output_title": "样式版",
                "formatting": {"columns": [{"match_header": "姓名", "font_color": "red"}]},
            }
        )
        self.assertEqual(style_call["target_type"], "attachment")
        self.assertEqual(style_call["instruction"], "姓名列标红")
        self.assertEqual(style_call["formatting"]["columns"][0]["font_color"], "red")

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
            ("compose_file", compose),
            ("revise_generated_file", revised),
            ("apply_style_to_existing_file", styled),
            ("send_file", send),
        ):
            native = build_openai_native_tool_specs({tool_name: handler})[0]["function"]
            self.assertEqual(
                native["parameters"]["properties"],
                TOOL_SPEC_BY_TYPE[tool_name].input_schema["properties"],
            )
            self.assertNotIn("格式为", native["description"])
            self.assertNotIn("tool_call", native["description"])

    def test_media_native_specs_match_handlers_and_do_not_silently_drop_primary_options(self) -> None:
        clean = CleanVoiceTrackToolHandler(generated_file_service=None)
        transcribe = TranscribeMediaToolHandler(generated_file_service=None)
        dataset = PrepareVoiceDatasetToolHandler(generated_file_service=None)

        clean_call = clean.normalize_call(
            {
                "type": "clean_voice_track",
                "source_id": "gen_001",
                "mode": "dereverb",
                "quality": "ai",
                "output_format": "wav",
                "output_title": "净化人声",
                "post_filter": True,
            }
        )
        self.assertEqual(clean_call["mode"], "dereverb")
        self.assertEqual(clean_call["quality"], "ai")
        self.assertTrue(clean_call["post_filter"])

        transcribe_call = transcribe.normalize_call(
            {
                "type": "transcribe_media",
                "source_ids": ["audio_001", "file_002"],
                "output_format": "srt",
                "output_title": "字幕",
                "language": "auto",
                "with_timestamps": True,
                "merge_outputs": False,
                "model_size": "medium",
                "vad_filter": True,
            }
        )
        self.assertEqual(transcribe_call["source_ids"], ["audio_001", "file_002"])
        self.assertEqual(transcribe_call["output_format"], "srt")
        self.assertFalse(transcribe_call["merge_outputs"])
        self.assertEqual(transcribe_call["model_size"], "medium")

        dataset_call = dataset.normalize_call(
            {
                "type": "prepare_voice_dataset",
                "source_ids": ["gen_001"],
                "profile": "gpt_sovits",
                "output_title": "训练集",
                "target_sr": 44100,
                "mono": True,
                "min_clip_seconds": 3,
                "max_clip_seconds": 12,
                "silence_threshold_db": -40,
                "min_silence_ms": 300,
                "max_silence_kept_ms": 300,
                "clean_first": True,
                "normalize_volume": True,
            }
        )
        self.assertEqual(dataset_call["source_ids"], ["gen_001"])
        self.assertEqual(dataset_call["profile"], "gpt_sovits")
        self.assertEqual(dataset_call["target_sr"], 44100)
        self.assertTrue(dataset_call["clean_first"])

        expected_properties = {
            "clean_voice_track": {
                "source_id",
                "mode",
                "quality",
                "output_format",
                "output_title",
                "post_filter",
            },
            "transcribe_media": {
                "source_ids",
                "output_format",
                "output_title",
                "language",
                "with_timestamps",
                "merge_outputs",
                "model_size",
                "vad_filter",
            },
            "prepare_voice_dataset": {
                "source_ids",
                "profile",
                "output_title",
                "target_sr",
                "mono",
                "min_clip_seconds",
                "max_clip_seconds",
                "silence_threshold_db",
                "min_silence_ms",
                "max_silence_kept_ms",
                "clean_first",
                "normalize_volume",
            },
        }
        handlers = {
            "clean_voice_track": clean,
            "transcribe_media": transcribe,
            "prepare_voice_dataset": dataset,
        }
        for tool_name, expected in expected_properties.items():
            spec = TOOL_SPEC_BY_TYPE[tool_name]
            self.assertEqual(set(spec.input_schema["properties"]), expected, tool_name)
            native = build_openai_native_tool_specs({tool_name: handlers[tool_name]})[0]["function"]
            self.assertEqual(set(native["parameters"]["properties"]), expected, tool_name)
            self.assertNotIn("model", native["parameters"]["properties"], tool_name)
            self.assertNotIn("stems", native["parameters"]["properties"], tool_name)


if __name__ == "__main__":
    unittest.main()
