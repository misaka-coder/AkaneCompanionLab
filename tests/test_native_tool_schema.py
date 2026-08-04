from __future__ import annotations

import inspect
import unittest

from memcore import build_native_memory_tool_specs

from companion_v01.capability_adapters import CapabilityDescriptor, CapabilityIOSlot
from companion_v01.capability_registry import (
    READ_MEMORY_ENTRY_TOOL_SPEC,
    READ_MEMORY_TIMELINE_TOOL_SPEC,
    RETRIEVE_MEMORY_TOOL_SPEC,
)
from companion_v01.generated_files import GeneratedFileService
from companion_v01.generated_files_media import separate_audio_stems as separate_audio_stems_service
from companion_v01.native_tool_schema import NATIVE_TOOL_CAPABILITY_ID_FIELD, build_openai_native_tool_specs
from companion_v01.tool_orchestration_engine import native_legacy_prompt_exclusions
from companion_v01.tool_runtime import (
    AdapterCapabilityToolHandler,
    ApplyStyleToExistingFileToolHandler,
    CleanVoiceTrackToolHandler,
    ComposeFileToolHandler,
    ConvertMediaFileToolHandler,
    PrepareVoiceDatasetToolHandler,
    ReviseGeneratedFileToolHandler,
    SendFileToolHandler,
    SeparateAudioStemsToolHandler,
    TOOL_METADATA_BY_TYPE,
    TOOL_SPEC_BY_TYPE,
    TranscribeMediaToolHandler,
)


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
            "read_entry": "read_memory_entry",
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
            (READ_MEMORY_ENTRY_TOOL_SPEC, "read_entry"),
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
                self.assertNotIn("read_entry", rendered_contract)

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
        separate = SeparateAudioStemsToolHandler(generated_file_service=None)
        clean = CleanVoiceTrackToolHandler(generated_file_service=None)
        transcribe = TranscribeMediaToolHandler(generated_file_service=None)
        dataset = PrepareVoiceDatasetToolHandler(generated_file_service=None)
        convert = ConvertMediaFileToolHandler(generated_file_service=None)

        default_separation = separate.normalize_call(
            {"type": "separate_audio_stems", "source_id": "audio_001"}
        )
        self.assertEqual(default_separation["output_format"], "mp3")
        self.assertEqual(
            inspect.signature(GeneratedFileService.separate_audio_stems).parameters["output_format"].default,
            "mp3",
        )
        self.assertEqual(
            inspect.signature(separate_audio_stems_service).parameters["output_format"].default,
            "mp3",
        )
        separation_call = separate.normalize_call(
            {
                "type": "separate_audio_stems",
                "source_id": "audio_001",
                "output_format": "flac",
                "output_title": "幻听",
            }
        )
        self.assertEqual(separation_call["output_format"], "flac")
        self.assertEqual(separation_call["output_title"], "幻听")

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

        convert_call = convert.normalize_call(
            {
                "type": "convert_media_file",
                "source_id": "file_001",
                "output_format": "mp3",
                "output_title": "片段",
                "start_time": "00:00:35",
                "end_time": "00:01:20",
                "normalize_volume": True,
                "volume_gain_db": 3,
                "trim_silence": True,
                "fade_in_seconds": 1,
                "fade_out_seconds": 2,
                "speed_ratio": 1.25,
                "bitrate": "192k",
                "sample_rate": 44100,
                "channels": 2,
            }
        )
        self.assertEqual(convert_call["output_title"], "片段")
        self.assertEqual(convert_call["start_time"], "00:00:35")
        self.assertEqual(convert_call["end_time"], "00:01:20")
        self.assertTrue(convert_call["normalize_volume"])
        self.assertTrue(convert_call["trim_silence"])
        self.assertEqual(convert_call["speed_ratio"], 1.25)

        expected_properties = {
            "separate_audio_stems": {"source_id", "output_format", "output_title"},
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
            "convert_media_file": {
                "source_id",
                "output_format",
                "output_title",
                "start_time",
                "end_time",
                "normalize_volume",
                "volume_gain_db",
                "trim_silence",
                "fade_in_seconds",
                "fade_out_seconds",
                "speed_ratio",
                "bitrate",
                "sample_rate",
                "channels",
            },
        }
        handlers = {
            "separate_audio_stems": separate,
            "clean_voice_track": clean,
            "transcribe_media": transcribe,
            "prepare_voice_dataset": dataset,
            "convert_media_file": convert,
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
