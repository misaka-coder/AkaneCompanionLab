from __future__ import annotations

import json
import threading
import time
import unittest
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

import config
from companion_v01 import tool_orchestration_engine
from companion_v01.capability_registry import (
    CapabilityDisclosure,
    CapabilitySelection,
    WEB_SEARCH_TOOL_SPEC,
)
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.final_output_engine import normalize_final_output
from companion_v01.llm_runtime import LLMRuntime, ModelBundle
from companion_v01.native_tool_schema import build_openai_native_tool_from_spec
from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.tool_invocation import (
    NATIVE_ANTHROPIC,
    NATIVE_OPENAI,
    NATIVE_TOOL_CALL_FIELD,
    NATIVE_TOOL_CALLS_FIELD,
    TOOL_INVOCATION_ID_FIELD,
    TOOL_CAPABILITY_SELECTION_FIELD,
    TOOL_MODEL_NAME_FIELD,
    TOOL_SOURCE_FIELD,
)
from companion_v01.tool_runtime import (
    TOOL_METADATA_BY_TYPE,
    ToolExecutionResult,
    operation_tool_result,
)


class _NativeToolProjectionManager:
    """Small public-facade fake; provider rendering itself is covered by MemCore tests."""

    enabled = True

    def __init__(self, provider: str, *, assistant_preface: str = "") -> None:
        self.provider = provider
        self.assistant_preface = assistant_preface
        self.exchanges: list[dict] = []
        self.source_pairs: list[tuple[str, str]] = []

    def record_tool_batch(self, **kwargs):
        self.exchanges = [dict(item) for item in kwargs["exchanges"]]
        self.source_pairs = [(f"test-use-{index}", f"test-result-{index}") for index in range(len(self.exchanges))]
        return {
            "ok": True,
            "status": "recorded",
            "exchanges": [
                {"tool_use_source_id": use_id, "tool_result_source_id": result_id}
                for use_id, result_id in self.source_pairs
            ],
        }

    def build_context_projection(self, **kwargs):
        self.assert_provider(kwargs["provider_profile"])
        if self.provider == NATIVE_ANTHROPIC:
            assistant_content = []
            if self.assistant_preface:
                assistant_content.append({"type": "text", "text": self.assistant_preface})
            assistant_content.extend(
                {
                    "type": "tool_use",
                    "id": item["tool_call_id"],
                    "name": item["tool_name"],
                    "input": item["tool_input"],
                }
                for item in self.exchanges
            )
            result_content = []
            for item in self.exchanges:
                block = {
                    "type": "tool_result",
                    "tool_use_id": item["tool_call_id"],
                    "content": item["result"],
                }
                if item["result_status"] in {"error", "cancelled"}:
                    block["is_error"] = True
                result_content.append(block)
            payloads = [
                {"role": "assistant", "content": assistant_content},
                {"role": "user", "content": result_content},
            ]
            source_ids = [
                [use_id for use_id, _result_id in self.source_pairs],
                [result_id for _use_id, result_id in self.source_pairs],
            ]
        else:
            assistant = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": item["tool_call_id"],
                        "type": "function",
                        "function": {
                            "name": item["tool_name"],
                            "arguments": json.dumps(item["tool_input"], ensure_ascii=False, separators=(",", ":")),
                        },
                    }
                    for item in self.exchanges
                ],
            }
            if self.assistant_preface:
                assistant["content"] = self.assistant_preface
            payloads = [assistant] + [
                {"role": "tool", "tool_call_id": item["tool_call_id"], "content": item["result"]}
                for item in self.exchanges
            ]
            source_ids = [
                [use_id for use_id, _result_id in self.source_pairs],
                *[[result_id] for _use_id, result_id in self.source_pairs],
            ]
        return {
            "ok": True,
            "status": "ok",
            "provider_profile": self.provider,
            "messages": [{"payload": payload, "source_ids": ids} for payload, ids in zip(payloads, source_ids)],
        }

    def assert_provider(self, provider: str) -> None:
        if provider != self.provider:
            raise AssertionError(f"unexpected provider profile: {provider}")


class NativeWebSearchToolingTests(unittest.TestCase):
    def test_engine_native_chat_vision_requires_same_chat_and_vision_route(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        with patch.multiple(
            config,
            VISION_ENABLED=True,
            CHAT_API_KEY="shared-key",
            VISION_API_KEY="shared-key",
            CHAT_BASE_URL="https://api.pinaic.com",
            VISION_BASE_URL="https://api.pinaic.com/",
            CHAT_API_PROTOCOL="anthropic",
            VISION_API_PROTOCOL="anthropic",
            CHAT_MODEL_NAME="claude-sonnet-5",
            VISION_MODEL_NAME="claude-sonnet-5",
        ):
            enabled = engine.native_chat_vision_status()
            mismatched = engine.native_chat_vision_status(chat_model_override="deepseek-v4-flash")

        self.assertTrue(enabled["enabled"])
        self.assertEqual(enabled["protocol"], "anthropic")
        self.assertFalse(mismatched["enabled"])
        self.assertEqual(mismatched["reason"], "chat_vision_model_mismatch")

    def test_engine_native_user_image_context_keeps_only_handles_out_of_prompt(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        data_url = "data:image/png;base64,c3ludGhldGlj"

        images = engine._extract_native_user_images(
            {
                "native_user_images": [
                    {
                        "attachment_id": "attachment-1",
                        "attachment_handle": "img_001",
                        "title": "当前图片",
                        "data_url": data_url,
                    }
                ]
            }
        )
        context = engine._build_native_user_image_prompt_context(images)

        self.assertEqual(images[0]["data_url"], data_url)
        self.assertIn("img_001", context)
        self.assertNotIn("base64", context)
        self.assertNotIn("c3ludGhldGlj", context)

    def test_engine_merges_internal_tool_images_without_exposing_them_in_tool_text(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        loaded = ToolExecutionResult(
            tool_type="load_material",
            followup_context="已加载 img_002。",
            model_image_inputs=[
                {
                    "attachment_id": "attachment-2",
                    "attachment_handle": "img_002",
                    "title": "旧图",
                    "data_url": "data:image/png;base64,b2xkLWltYWdl",
                }
            ],
        )

        merged = engine._merge_tool_model_image_inputs([], [loaded])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["attachment_handle"], "img_002")
        self.assertNotIn("base64", loaded.followup_context)

    def test_engine_keeps_tool_image_in_memcore_but_not_text_only_model_projection(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        image_input = {
            "attachment_id": "generated-image-1",
            "attachment_handle": "gen_001",
            "data_url": "data:image/png;base64,AAAA",
        }
        engine._execute_tool_call = lambda **_kwargs: ToolExecutionResult(
            tool_type="generate_image",
            followup_context="图片生成完成，得到 gen_001。",
            model_image_inputs=[image_input],
        )
        engine._record_tool_result_artifacts_in_task_workspace = lambda **_kwargs: ([], "")
        engine._record_memcore_tool_batch = lambda **_kwargs: (
            ["tool-use-1", "tool-result-1"],
            None,
        )
        recorded_media: list[list[dict]] = []
        engine._record_memcore_tool_media_input = lambda **kwargs: (
            recorded_media.append(list(kwargs["model_image_inputs"])) or ["tool-media-1"]
        )
        projected_images: list[list[dict]] = []
        engine._append_tool_history_batch = lambda **kwargs: (
            projected_images.append(list(kwargs["model_image_inputs"])) or {"ok": True, "status": "projected"}
        )
        engine.native_chat_vision_status = lambda **_kwargs: {
            "enabled": False,
            "reason": "chat_vision_model_mismatch",
        }
        client_context = ClientProtocolContext(
            requested_mode=ClientMode.SCENE_STATIC,
            effective_mode=ClientMode.SCENE_STATIC,
        )

        engine._execute_and_record_tool_batch(
            tool_calls=[
                {
                    "type": "generate_image",
                    TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                    TOOL_INVOCATION_ID_FIELD: "call-image",
                }
            ],
            final_output={"speech": "", "tool_call": None},
            tool_results=[],
            tool_events=[],
            tool_followups=[],
            tool_turns=[],
            recent_raw_for_turn=[],
            profile_user_id="u",
            session_id="s",
            character_pack_id="",
            now_ts=100,
            current_user_source_id="user:1",
            client_context=client_context,
            memory_exclude_source_ids=[],
            request_context={"chat_model_override": "deepseek-v4-flash"},
            tool_history_turns=[],
            memcore_turn_id="turn-image",
        )

        self.assertEqual(recorded_media, [[image_input]])
        self.assertEqual(projected_images, [[]])

    def test_native_web_search_schema_respects_global_disable(self) -> None:
        original_enabled = getattr(config, "ENABLE_NATIVE_TOOL_DECISION", False)
        original_allowlist = getattr(config, "NATIVE_TOOL_DECISION_ALLOWLIST", "web_search")
        try:
            config.ENABLE_NATIVE_TOOL_DECISION = False
            config.NATIVE_TOOL_DECISION_ALLOWLIST = "web_search"
            self.assertEqual(
                tool_orchestration_engine.build_native_tool_schemas(
                    {"web_search": object()},
                    allow_tool_call=True,
                ),
                [],
            )
        finally:
            config.ENABLE_NATIVE_TOOL_DECISION = original_enabled
            config.NATIVE_TOOL_DECISION_ALLOWLIST = original_allowlist

    def test_native_tool_decision_is_default_on_for_verified_profiles(self) -> None:
        from config import Settings

        original_enabled = getattr(config, "ENABLE_NATIVE_TOOL_DECISION", False)
        original_allowlist = getattr(config, "NATIVE_TOOL_DECISION_ALLOWLIST", "web_search")
        try:
            config.ENABLE_NATIVE_TOOL_DECISION = bool(Settings.model_fields["ENABLE_NATIVE_TOOL_DECISION"].default)
            config.NATIVE_TOOL_DECISION_ALLOWLIST = "web_search"

            plan = tool_orchestration_engine.build_native_tool_decision_plan(
                {"web_search": object()},
                allow_tool_call=True,
                provider_supports_native_tools=True,
            )

            self.assertTrue(Settings.model_fields["ENABLE_NATIVE_TOOL_DECISION"].default)
            self.assertTrue(plan.enabled)
            self.assertEqual(plan.reason, "verified_native_tools")
        finally:
            config.ENABLE_NATIVE_TOOL_DECISION = original_enabled
            config.NATIVE_TOOL_DECISION_ALLOWLIST = original_allowlist

    def test_native_web_search_schema_respects_allowlist_and_handler(self) -> None:
        original_enabled = getattr(config, "ENABLE_NATIVE_TOOL_DECISION", False)
        original_allowlist = getattr(config, "NATIVE_TOOL_DECISION_ALLOWLIST", "web_search")
        try:
            config.ENABLE_NATIVE_TOOL_DECISION = True
            config.NATIVE_TOOL_DECISION_ALLOWLIST = "web_search"
            schemas = tool_orchestration_engine.build_native_tool_schemas(
                {"web_search": object()},
                allow_tool_call=True,
            )
            self.assertEqual(len(schemas), 1)
            self.assertEqual(schemas[0]["function"]["name"], "web_search")
            self.assertEqual(
                tool_orchestration_engine.native_legacy_prompt_exclusions(schemas),
                {"web_search"},
            )

            config.NATIVE_TOOL_DECISION_ALLOWLIST = "retrieve_memory"
            self.assertEqual(
                tool_orchestration_engine.build_native_tool_schemas(
                    {"web_search": object()},
                    allow_tool_call=True,
                ),
                [],
            )
        finally:
            config.ENABLE_NATIVE_TOOL_DECISION = original_enabled
            config.NATIVE_TOOL_DECISION_ALLOWLIST = original_allowlist

    def test_native_schema_allowlist_can_include_read_only_memory_tools(self) -> None:
        original_enabled = getattr(config, "ENABLE_NATIVE_TOOL_DECISION", False)
        original_allowlist = getattr(config, "NATIVE_TOOL_DECISION_ALLOWLIST", "web_search")
        original_memory_backend = getattr(config, "MEMORY_BACKEND", "memcore")
        try:
            config.ENABLE_NATIVE_TOOL_DECISION = True
            config.NATIVE_TOOL_DECISION_ALLOWLIST = "web_search,retrieve_memory,read_memory_timeline"
            config.MEMORY_BACKEND = "legacy"

            schemas = tool_orchestration_engine.build_native_tool_schemas(
                {
                    "web_search": object(),
                    "retrieve_memory": FakeNativeHandler("retrieve_memory"),
                    "read_memory_timeline": FakeNativeHandler("read_memory_timeline"),
                    "compose_file": FakeNativeHandler("compose_file"),
                },
                allow_tool_call=True,
            )

            names = [schema["function"]["name"] for schema in schemas]
            self.assertEqual(names, ["web_search", "retrieve_memory", "read_memory_timeline"])
            retrieve_schema = schemas[1]["function"]
            self.assertNotIn("tool_call", retrieve_schema["description"])
            self.assertEqual(retrieve_schema["parameters"]["additionalProperties"], False)
            self.assertIn("query", retrieve_schema["parameters"]["required"])
            self.assertEqual(
                tool_orchestration_engine.native_legacy_prompt_exclusions(schemas),
                {"web_search", "retrieve_memory", "read_memory_timeline"},
            )
        finally:
            config.ENABLE_NATIVE_TOOL_DECISION = original_enabled
            config.NATIVE_TOOL_DECISION_ALLOWLIST = original_allowlist
            config.MEMORY_BACKEND = original_memory_backend

    def test_native_tool_decision_plan_enables_single_prompt_channel(self) -> None:
        original_enabled = getattr(config, "ENABLE_NATIVE_TOOL_DECISION", False)
        original_allowlist = getattr(config, "NATIVE_TOOL_DECISION_ALLOWLIST", "web_search")
        original_memory_backend = getattr(config, "MEMORY_BACKEND", "memcore")
        try:
            config.ENABLE_NATIVE_TOOL_DECISION = True
            config.NATIVE_TOOL_DECISION_ALLOWLIST = "web_search"

            plan = tool_orchestration_engine.build_native_tool_decision_plan(
                {"web_search": object(), "send_file": object()},
                allow_tool_call=True,
                provider_supports_native_tools=True,
            )

            self.assertTrue(plan.enabled)
            self.assertEqual(plan.status, "enabled")
            self.assertEqual(plan.reason, "verified_native_tools")
            self.assertEqual(plan.tool_choice, "auto")
            self.assertEqual(plan.tools[0]["function"]["name"], "web_search")
            self.assertEqual(plan.legacy_prompt_exclusions, {"web_search"})
        finally:
            config.ENABLE_NATIVE_TOOL_DECISION = original_enabled
            config.NATIVE_TOOL_DECISION_ALLOWLIST = original_allowlist

    def test_default_allowlist_includes_validated_native_tools(self) -> None:
        # The shipped default allowlist contains validated read tools plus
        # bounded session-material/image-generation artifact tools. Read the
        # class field default (immune to .env / other tests).
        from config import Settings

        default = str(Settings.model_fields["NATIVE_TOOL_DECISION_ALLOWLIST"].default or "")
        self.assertEqual(
            {item.strip() for item in default.split(",") if item.strip()},
            {
                "web_search",
                "retrieve_memory",
                "read_memory_timeline",
                "open_memory",
                "list_reminders",
                "check_inventory",
                "inspect_media_info",
                "load_character_context",
                "inspect_attachment",
                "load_material",
                "read_attachment_section",
                "list_workspace",
                "read_workspace",
                "inspect_generated_file",
                "generate_image",
                "compose_file",
                "revise_generated_file",
                "apply_style_to_existing_file",
                "separate_audio_stems",
                "clean_voice_track",
                "transcribe_media",
                "prepare_voice_dataset",
                "convert_media_file",
                "cover_song",
                "send_file",
            },
        )

    def test_native_media_processing_and_delivery_share_one_tool_channel(self) -> None:
        original_enabled = getattr(config, "ENABLE_NATIVE_TOOL_DECISION", False)
        original_allowlist = getattr(config, "NATIVE_TOOL_DECISION_ALLOWLIST", "web_search")
        try:
            config.ENABLE_NATIVE_TOOL_DECISION = True
            config.NATIVE_TOOL_DECISION_ALLOWLIST = "inspect_media_info,separate_audio_stems,cover_song,send_file"
            plan = tool_orchestration_engine.build_native_tool_decision_plan(
                {
                    name: FakeNativeHandler(name)
                    for name in (
                        "inspect_media_info",
                        "separate_audio_stems",
                        "cover_song",
                        "send_file",
                    )
                },
                allow_tool_call=True,
                provider_supports_native_tools=True,
                allowed_tool_names=(
                    "inspect_media_info",
                    "separate_audio_stems",
                    "cover_song",
                    "send_file",
                ),
            )

            self.assertTrue(plan.enabled)
            names = [tool["function"]["name"] for tool in plan.tools]
            self.assertEqual(
                names,
                ["inspect_media_info", "separate_audio_stems", "cover_song", "send_file"],
            )
            self.assertEqual(plan.legacy_prompt_exclusions, set(names))
            for tool in plan.tools:
                self.assertIs(tool["function"]["parameters"]["additionalProperties"], False)
        finally:
            config.ENABLE_NATIVE_TOOL_DECISION = original_enabled
            config.NATIVE_TOOL_DECISION_ALLOWLIST = original_allowlist

    def test_read_tier_handlers_emit_precise_native_schemas(self) -> None:
        # 6b: each migrated read tool carries a precise input_schema (enum/limit
        # constraints, additionalProperties:False), not the loose generic spec.
        from companion_v01.native_tool_schema import build_openai_native_tool_specs

        for name, required_props in (
            ("list_reminders", ()),
            ("check_inventory", ()),
            ("inspect_media_info", ("source_id",)),
        ):
            specs = build_openai_native_tool_specs({name: FakeNativeHandler(name)})
            self.assertEqual(len(specs), 1, name)
            fn = specs[0]["function"]
            self.assertEqual(fn["name"], name)
            self.assertNotIn("tool_call", fn["description"])
            self.assertIs(fn["parameters"]["additionalProperties"], False)
            self.assertEqual(set(fn["parameters"].get("required", [])), set(required_props))
            self.assertNotIn("description", fn["parameters"])

    def test_n1b_read_tools_emit_precise_native_schemas(self) -> None:
        # N1b: the remaining read-only tools carry a precise input_schema whose
        # required set matches normalize_call (only targets-bearing tools require
        # input), with a clean envelope-free description.
        from companion_v01.native_tool_schema import build_openai_native_tool_specs

        expected_required = {
            "load_character_context": {"targets"},
            "inspect_attachment": set(),
            "read_attachment_section": set(),
            "sync_attachment_workspace": set(),
            "list_workspace": set(),
            "read_workspace": {"targets"},
            "inspect_generated_file": set(),
        }
        for name, required in expected_required.items():
            specs = build_openai_native_tool_specs({name: FakeNativeHandler(name)})
            self.assertEqual(len(specs), 1, name)
            fn = specs[0]["function"]
            self.assertEqual(fn["name"], name)
            self.assertTrue(fn["description"].strip(), name)
            self.assertNotIn("格式为", fn["description"], name)
            self.assertNotIn("tool_call", fn["description"], name)
            self.assertNotIn('{"type"', fn["description"], name)
            self.assertIs(fn["parameters"]["additionalProperties"], False, name)
            self.assertNotIn("description", fn["parameters"], name)
            self.assertEqual(set(fn["parameters"].get("required", [])), required, name)

    def test_default_allowlist_plan_sends_three_schemas_and_excludes_legacy(self) -> None:
        original_enabled = getattr(config, "ENABLE_NATIVE_TOOL_DECISION", False)
        original_allowlist = getattr(config, "NATIVE_TOOL_DECISION_ALLOWLIST", "web_search")
        try:
            config.ENABLE_NATIVE_TOOL_DECISION = True
            config.NATIVE_TOOL_DECISION_ALLOWLIST = "web_search,retrieve_memory,read_memory_timeline"

            plan = tool_orchestration_engine.build_native_tool_decision_plan(
                {
                    "web_search": object(),
                    "retrieve_memory": FakeNativeHandler("retrieve_memory"),
                    "read_memory_timeline": FakeNativeHandler("read_memory_timeline"),
                    "compose_file": FakeNativeHandler("compose_file"),
                },
                allow_tool_call=True,
                provider_supports_native_tools=True,
            )

            self.assertTrue(plan.enabled)
            names = [tool["function"]["name"] for tool in plan.tools]
            self.assertEqual(set(names), {"web_search", "retrieve_memory", "read_memory_timeline"})
            # Native-provided tools are excluded from the legacy prompt; the
            # write tool (compose_file) is not in the allowlist, so it stays legacy.
            self.assertEqual(
                plan.legacy_prompt_exclusions,
                {"web_search", "retrieve_memory", "read_memory_timeline"},
            )
        finally:
            config.ENABLE_NATIVE_TOOL_DECISION = original_enabled
            config.NATIVE_TOOL_DECISION_ALLOWLIST = original_allowlist

    def test_native_schema_building_respects_capability_tool_subset(self) -> None:
        original_enabled = getattr(config, "ENABLE_NATIVE_TOOL_DECISION", False)
        original_allowlist = getattr(config, "NATIVE_TOOL_DECISION_ALLOWLIST", "web_search")
        try:
            config.ENABLE_NATIVE_TOOL_DECISION = True
            config.NATIVE_TOOL_DECISION_ALLOWLIST = "web_search,retrieve_memory,read_memory_timeline"

            schemas = tool_orchestration_engine.build_native_tool_schemas(
                {
                    "web_search": object(),
                    "retrieve_memory": FakeNativeHandler("retrieve_memory"),
                    "read_memory_timeline": FakeNativeHandler("read_memory_timeline"),
                },
                allow_tool_call=True,
                allowed_tool_names=("retrieve_memory", "send_file"),
            )

            self.assertEqual([schema["function"]["name"] for schema in schemas], ["retrieve_memory"])
            self.assertEqual(
                tool_orchestration_engine.native_legacy_prompt_exclusions(schemas),
                {"retrieve_memory"},
            )
        finally:
            config.ENABLE_NATIVE_TOOL_DECISION = original_enabled
            config.NATIVE_TOOL_DECISION_ALLOWLIST = original_allowlist

    def test_native_tool_decision_plan_disables_when_allowlist_outside_capability_subset(self) -> None:
        original_enabled = getattr(config, "ENABLE_NATIVE_TOOL_DECISION", False)
        original_allowlist = getattr(config, "NATIVE_TOOL_DECISION_ALLOWLIST", "web_search")
        try:
            config.ENABLE_NATIVE_TOOL_DECISION = True
            config.NATIVE_TOOL_DECISION_ALLOWLIST = "web_search"

            plan = tool_orchestration_engine.build_native_tool_decision_plan(
                {"web_search": object(), "send_file": object()},
                allow_tool_call=True,
                provider_supports_native_tools=True,
                allowed_tool_names=("send_file",),
            )

            self.assertFalse(plan.enabled)
            self.assertEqual(plan.status, "disabled")
            self.assertEqual(plan.reason, "native_tool_not_in_capability_selection")
            self.assertEqual(plan.tools, [])
            self.assertEqual(plan.legacy_prompt_exclusions, set())
        finally:
            config.ENABLE_NATIVE_TOOL_DECISION = original_enabled
            config.NATIVE_TOOL_DECISION_ALLOWLIST = original_allowlist

    def test_native_tool_decision_plan_keeps_legacy_when_provider_unverified(self) -> None:
        original_enabled = getattr(config, "ENABLE_NATIVE_TOOL_DECISION", False)
        original_allowlist = getattr(config, "NATIVE_TOOL_DECISION_ALLOWLIST", "web_search")
        try:
            config.ENABLE_NATIVE_TOOL_DECISION = True
            config.NATIVE_TOOL_DECISION_ALLOWLIST = "web_search"

            plan = tool_orchestration_engine.build_native_tool_decision_plan(
                {"web_search": object()},
                allow_tool_call=True,
                provider_supports_native_tools=False,
            )

            self.assertFalse(plan.enabled)
            self.assertEqual(plan.status, "unsupported")
            self.assertEqual(plan.reason, "provider_profile_not_verified_for_native_tools")
            self.assertEqual(plan.legacy_prompt_exclusions, set())
        finally:
            config.ENABLE_NATIVE_TOOL_DECISION = original_enabled
            config.NATIVE_TOOL_DECISION_ALLOWLIST = original_allowlist

    def test_tool_prompt_can_exclude_native_web_search_from_legacy_channel(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine._resolve_capability_selection = lambda **_kwargs: SimpleNamespace(
            module_names=[],
            light_hints=[],
            tool_names=["web_search", "send_file"],
        )
        engine._resolve_tool_handlers = lambda **_kwargs: {
            "web_search": FakePromptHandler("web_search"),
            "send_file": FakePromptHandler("send_file"),
        }

        prompt = engine._build_tool_prompt_context(
            allow_tool_call=True,
            exclude_tool_types={"web_search"},
        )

        self.assertNotIn("web_search", prompt)
        self.assertIn("send_file", prompt)

    def test_native_tool_prompt_omits_transient_capability_status_from_stable_prefix(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        selection = SimpleNamespace(
            module_names=[],
            light_hints=["联网搜索当前可用。"],
            tool_names=[],
            disclosures=(
                CapabilityDisclosure(
                    capability_id="internet_access",
                    state="unavailable",
                    summary="联网搜索暂不可用。",
                    reason="探针正在检查。",
                    activation="检查结束后自动恢复。",
                    tool_names=("web_search",),
                ),
            ),
        )
        engine._resolve_tool_handlers = lambda **_kwargs: {
            "web_search": FakePromptHandler("web_search"),
        }

        prompt = engine._build_tool_prompt_context(
            allow_tool_call=True,
            exclude_tool_types={"web_search"},
            capability_selection=selection,
            include_capability_status=False,
        )

        self.assertIn("请求中实际附带的工具定义", prompt)
        self.assertNotIn("探针正在检查", prompt)
        self.assertNotIn("联网搜索暂不可用", prompt)
        self.assertNotIn("联网搜索当前可用", prompt)

    def test_final_response_context_scopes_native_tools_to_capability_selection(self) -> None:
        original_enabled = getattr(config, "ENABLE_NATIVE_TOOL_DECISION", False)
        original_allowlist = getattr(config, "NATIVE_TOOL_DECISION_ALLOWLIST", "web_search")
        original_memory_backend = getattr(config, "MEMORY_BACKEND", "memcore")
        try:
            config.ENABLE_NATIVE_TOOL_DECISION = True
            config.NATIVE_TOOL_DECISION_ALLOWLIST = "web_search,retrieve_memory,read_memory_timeline"
            config.MEMORY_BACKEND = "legacy"
            engine = build_native_context_engine(
                selected_tool_names=("retrieve_memory", "send_file"),
            )

            context = engine._prepare_final_response_context(
                session_id="s",
                profile_user_id="u",
                user_message="查一下记忆",
                recent_raw=[],
                recent_episodic_summaries=[],
                recent_semantic_summaries=[],
                confirmed_snippets=[],
                now_ts=0,
                client_context=ClientProtocolContext(
                    requested_mode=ClientMode.DESKTOP_PET,
                    effective_mode=ClientMode.DESKTOP_PET,
                ),
                enable_native_tools=True,
            )

            native_names = [tool["function"]["name"] for tool in context["native_tools"]]
            self.assertEqual(native_names, ["retrieve_memory"])
            self.assertEqual(
                context[TOOL_CAPABILITY_SELECTION_FIELD].native_tool_names,
                ("retrieve_memory",),
            )
            self.assertEqual(context["native_tool_choice"], "auto")
            self.assertNotIn("retrieve_memory", context["system_prompt"])
            self.assertIn("【本轮直接工具入口】", context["tool_prompt_context"])
            self.assertNotIn("retrieve_memory", context["tool_prompt_context"])
            self.assertNotIn("web_search", context["system_prompt"])
            self.assertIn("send_file", context["tool_prompt_context"])
            self.assertIn("【当前可调用工具（兼容 JSON 通道）】", context["tool_prompt_context"])
            self.assertNotIn("web_search", context["tool_prompt_context"])
        finally:
            config.ENABLE_NATIVE_TOOL_DECISION = original_enabled
            config.NATIVE_TOOL_DECISION_ALLOWLIST = original_allowlist
            config.MEMORY_BACKEND = original_memory_backend

    def test_final_response_context_keeps_native_schema_when_tool_budget_is_closed(self) -> None:
        original_enabled = getattr(config, "ENABLE_NATIVE_TOOL_DECISION", False)
        original_allowlist = getattr(config, "NATIVE_TOOL_DECISION_ALLOWLIST", "web_search")
        original_memory_backend = getattr(config, "MEMORY_BACKEND", "memcore")
        try:
            config.ENABLE_NATIVE_TOOL_DECISION = True
            config.NATIVE_TOOL_DECISION_ALLOWLIST = "retrieve_memory"
            config.MEMORY_BACKEND = "legacy"
            engine = build_native_context_engine(selected_tool_names=("retrieve_memory",))
            common = {
                "session_id": "s",
                "profile_user_id": "u",
                "user_message": "查一下记忆",
                "recent_raw": [],
                "recent_episodic_summaries": [],
                "recent_semantic_summaries": [],
                "confirmed_snippets": [],
                "now_ts": 0,
                "client_context": ClientProtocolContext(
                    requested_mode=ClientMode.DESKTOP_PET,
                    effective_mode=ClientMode.DESKTOP_PET,
                ),
                "enable_native_tools": True,
            }
            open_context = engine._prepare_final_response_context(**common, allow_tool_call=True)
            native_history = [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "retrieve_memory", "arguments": '{"query":"test"}'},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "structured result"},
            ]
            continuation_context = engine._prepare_final_response_context(
                **common,
                allow_tool_call=True,
                post_user_turns=native_history,
            )
            context = engine._prepare_final_response_context(
                **common,
                allow_tool_call=False,
                post_user_turns=native_history,
            )

            self.assertEqual(
                [tool["function"]["name"] for tool in open_context["native_tools"]],
                ["retrieve_memory"],
            )
            self.assertEqual(open_context["native_tool_choice"], "auto")
            self.assertEqual(
                [tool["function"]["name"] for tool in continuation_context["native_tools"]],
                ["retrieve_memory"],
            )
            self.assertEqual(continuation_context["native_tool_choice"], "auto")
            self.assertTrue(continuation_context["allow_tool_call"])
            self.assertEqual(continuation_context["post_user_turns"], native_history)
            self.assertEqual(open_context["tool_prompt_context"], context["tool_prompt_context"])
            self.assertEqual([tool["function"]["name"] for tool in context["native_tools"]], ["retrieve_memory"])
            self.assertEqual(context["native_tool_choice"], "none")
            self.assertFalse(context["allow_tool_call"])
            self.assertEqual(context["post_user_turns"], native_history)
            self.assertEqual(context["post_user_turns"][-1]["role"], "tool")
            self.assertEqual(len(native_history), 2)
            continuation_payload = LLMRuntime()._build_completion_kwargs(
                bundle=ModelBundle(
                    client=FakeClient("openai", base_url="https://api.deepseek.com/v1"),
                    model="deepseek-v4-flash",
                ),
                system_prompt="system",
                user_prompt="user",
                temperature=0.0,
                native_tools=continuation_context["native_tools"],
                native_tool_choice=continuation_context["native_tool_choice"],
            )
            self.assertEqual(continuation_payload["tool_choice"], "auto")
            payload = LLMRuntime()._build_completion_kwargs(
                bundle=ModelBundle(
                    client=FakeClient("openai", base_url="https://api.deepseek.com/v1"),
                    model="deepseek-v4-flash",
                ),
                system_prompt="system",
                user_prompt="user",
                temperature=0.0,
                native_tools=context["native_tools"],
                native_tool_choice=context["native_tool_choice"],
            )
            self.assertEqual(payload["tool_choice"], "none")
            self.assertEqual([tool["function"]["name"] for tool in payload["tools"]], ["retrieve_memory"])
        finally:
            config.ENABLE_NATIVE_TOOL_DECISION = original_enabled
            config.NATIVE_TOOL_DECISION_ALLOWLIST = original_allowlist
            config.MEMORY_BACKEND = original_memory_backend

    def test_final_response_context_keeps_legacy_when_native_candidates_not_selected(self) -> None:
        original_enabled = getattr(config, "ENABLE_NATIVE_TOOL_DECISION", False)
        original_allowlist = getattr(config, "NATIVE_TOOL_DECISION_ALLOWLIST", "web_search")
        original_memory_backend = getattr(config, "MEMORY_BACKEND", "memcore")
        try:
            config.ENABLE_NATIVE_TOOL_DECISION = True
            config.NATIVE_TOOL_DECISION_ALLOWLIST = "web_search,retrieve_memory"
            config.MEMORY_BACKEND = "legacy"
            engine = build_native_context_engine(
                selected_tool_names=("send_file",),
            )

            context = engine._prepare_final_response_context(
                session_id="s",
                profile_user_id="u",
                user_message="把文件发我",
                recent_raw=[],
                recent_episodic_summaries=[],
                recent_semantic_summaries=[],
                confirmed_snippets=[],
                now_ts=0,
                client_context=ClientProtocolContext(
                    requested_mode=ClientMode.DESKTOP_PET,
                    effective_mode=ClientMode.DESKTOP_PET,
                ),
                enable_native_tools=True,
            )

            self.assertEqual(context["native_tools"], [])
            self.assertEqual(context["native_tool_choice"], "")
            self.assertNotIn("native 工具轮优先规则", context["system_prompt"])
            self.assertIn("send_file", context["tool_prompt_context"])
            self.assertNotIn("web_search", context["tool_prompt_context"])
            self.assertNotIn("retrieve_memory", context["tool_prompt_context"])
        finally:
            config.ENABLE_NATIVE_TOOL_DECISION = original_enabled
            config.NATIVE_TOOL_DECISION_ALLOWLIST = original_allowlist
            config.MEMORY_BACKEND = original_memory_backend

    def test_native_tool_round_instruction_keeps_native_out_of_json_tool_call(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        instruction = engine._build_native_tool_round_instruction(
            [build_openai_native_tool_from_spec(WEB_SEARCH_TOOL_SPEC)]
        )

        self.assertNotIn("web_search", instruction)
        self.assertIn("真实工具调用", instruction)
        self.assertIn("最终 JSON 的 tool_call 保持 null", instruction)
        self.assertIn("请求中的工具定义为准", instruction)
        self.assertNotIn("互不依赖", instruction)
        self.assertNotIn("同一轮发出多个 native tool calls", instruction)

    def test_verified_profile_completion_payload_sends_native_tools(self) -> None:
        runtime = LLMRuntime()
        bundle = ModelBundle(
            client=FakeClient("openai", base_url="https://api.deepseek.com/v1"), model="deepseek-v4-flash"
        )
        schema = build_openai_native_tool_from_spec(WEB_SEARCH_TOOL_SPEC)

        payload = runtime._build_completion_kwargs(
            bundle=bundle,
            system_prompt="system",
            user_prompt="user",
            temperature=0.1,
            json_mode=True,
            native_tools=[schema],
            native_tool_choice="auto",
        )

        self.assertEqual(payload["tools"][0]["function"]["name"], "web_search")
        self.assertEqual(payload["tool_choice"], "auto")
        self.assertTrue(payload["parallel_tool_calls"])
        self.assertNotIn("response_format", payload)
        self.assertEqual(runtime.snapshot_metrics()["native_tool_decision_sent"], 1)
        self.assertEqual(runtime.snapshot_metrics()["native_tool_forced_json_suppressed"], 1)

    def test_unsupported_provider_does_not_send_native_tools(self) -> None:
        runtime = LLMRuntime()
        bundle = ModelBundle(client=FakeClient("ollama"), model="fake-model")
        schema = build_openai_native_tool_from_spec(WEB_SEARCH_TOOL_SPEC)

        payload = runtime._build_completion_kwargs(
            bundle=bundle,
            system_prompt="system",
            user_prompt="user",
            temperature=0.1,
            native_tools=[schema],
        )

        self.assertNotIn("tools", payload)
        self.assertEqual(runtime.snapshot_metrics()["native_tool_provider_unsupported"], 1)

    def test_extract_native_tool_call_marks_source_and_counts_extra_calls(self) -> None:
        runtime = LLMRuntime()
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        tool_calls=[
                            SimpleNamespace(
                                id="call_1",
                                function=SimpleNamespace(
                                    name="web_search",
                                    arguments='{"action":"search","query":"天气"}',
                                ),
                            ),
                            SimpleNamespace(
                                id="call_2",
                                function=SimpleNamespace(
                                    name="web_search",
                                    arguments='{"action":"search","query":"新闻"}',
                                ),
                            ),
                        ]
                    )
                )
            ]
        )

        tool_call = runtime._extract_native_tool_call(response)

        self.assertEqual(tool_call["type"], "web_search")
        self.assertEqual(tool_call["query"], "天气")
        self.assertEqual(tool_call[TOOL_SOURCE_FIELD], NATIVE_OPENAI)
        self.assertEqual(tool_call[TOOL_INVOCATION_ID_FIELD], "call_1")
        self.assertEqual(runtime.snapshot_metrics()["native_tool_calls_extra"], 1)

    def test_engine_tool_decision_accepts_native_web_search_call(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine._promote_narrated_tool_call = lambda final_output, **_kwargs: final_output
        engine._resolve_tool_handlers = lambda **_kwargs: {"web_search": FakeExecutableWebSearchHandler()}
        client_context = ClientProtocolContext(
            requested_mode=ClientMode.SCENE_STATIC,
            effective_mode=ClientMode.SCENE_STATIC,
        )

        _final_output, tool_call, rejection = engine._prepare_tool_round_decision(
            final_output={
                "speech": "",
                "tool_call": None,
                NATIVE_TOOL_CALL_FIELD: {
                    "type": "web_search",
                    "action": "search",
                    "query": "上海天气",
                    TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                    TOOL_INVOCATION_ID_FIELD: "call_native_1",
                },
            },
            user_message="查一下上海天气",
            client_context=client_context,
            profile_user_id="u",
            session_id="s",
        )

        self.assertEqual(rejection, "")
        self.assertNotIn(NATIVE_TOOL_CALL_FIELD, _final_output)
        self.assertIsNone(_final_output["tool_call"])
        self.assertEqual(tool_call["type"], "web_search")
        self.assertEqual(tool_call["query"], "上海天气")
        self.assertEqual(tool_call[TOOL_SOURCE_FIELD], NATIVE_OPENAI)

    def test_engine_tool_decision_preserves_native_tool_batch(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine._promote_narrated_tool_call = lambda final_output, **_kwargs: final_output
        engine._normalize_tool_call = lambda value, **_kwargs: dict(value or {})
        engine._describe_tool_call_rejection = lambda *_args, **_kwargs: "rejected"
        client_context = ClientProtocolContext(
            requested_mode=ClientMode.SCENE_STATIC,
            effective_mode=ClientMode.SCENE_STATIC,
        )

        _final_output, tool_calls, rejections = engine._prepare_tool_round_decisions(
            final_output={
                "speech": "",
                "tool_call": None,
                NATIVE_TOOL_CALLS_FIELD: [
                    {
                        "type": "web_search",
                        "query": "日经指数",
                        TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                        TOOL_INVOCATION_ID_FIELD: "call_1",
                    },
                    {
                        "type": "retrieve_memory",
                        "query": "风险偏好",
                        TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                        TOOL_INVOCATION_ID_FIELD: "call_2",
                    },
                ],
            },
            user_message="综合分析",
            client_context=client_context,
            profile_user_id="u",
            session_id="s",
        )

        self.assertEqual([call["type"] for call in tool_calls], ["web_search", "retrieve_memory"])
        self.assertEqual(
            [call[TOOL_INVOCATION_ID_FIELD] for call in tool_calls],
            ["call_1", "call_2"],
        )
        self.assertEqual(rejections, [])

    def test_engine_rejects_legacy_json_for_tool_exposed_in_native_schema(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine._promote_narrated_tool_call = lambda final_output, **_kwargs: final_output
        engine._normalize_tool_call = lambda value, **_kwargs: dict(value or {})
        engine._describe_tool_call_rejection = lambda *_args, **_kwargs: "unexpected"
        client_context = ClientProtocolContext(
            requested_mode=ClientMode.QQ_TEXT,
            effective_mode=ClientMode.QQ_TEXT,
        )
        selection = CapabilitySelection(
            light_hints=(),
            tool_names=("send_file",),
            module_names=("file_handoff",),
            schema_tool_names=("send_file",),
            native_tool_names=("send_file",),
        )

        final_output, tool_calls, rejections = engine._prepare_tool_round_decisions(
            final_output={
                "speech": "我发给你。",
                "tool_call": {"type": "send_file", "target": "gen_049"},
                TOOL_CAPABILITY_SELECTION_FIELD: selection,
            },
            user_message="发我",
            client_context=client_context,
            profile_user_id="u",
            session_id="s",
        )

        self.assertEqual(tool_calls, [])
        self.assertIsNone(final_output["tool_call"])
        self.assertEqual(len(rejections), 1)
        self.assertIn("没有执行这次歧义调用", rejections[0])
        self.assertIn("直接工具入口调用", rejections[0])

    def test_engine_still_accepts_legacy_json_for_non_native_tool(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine._promote_narrated_tool_call = lambda final_output, **_kwargs: final_output
        engine._normalize_tool_call = lambda value, **_kwargs: dict(value or {})
        engine._describe_tool_call_rejection = lambda *_args, **_kwargs: "unexpected"
        client_context = ClientProtocolContext(
            requested_mode=ClientMode.QQ_TEXT,
            effective_mode=ClientMode.QQ_TEXT,
        )
        selection = CapabilitySelection(
            light_hints=(),
            tool_names=("send_file", "legacy_only"),
            module_names=("mixed",),
            schema_tool_names=("send_file",),
            native_tool_names=("send_file",),
        )

        _final_output, tool_calls, rejections = engine._prepare_tool_round_decisions(
            final_output={
                "speech": "",
                "tool_call": {"type": "legacy_only", "value": "ok"},
                TOOL_CAPABILITY_SELECTION_FIELD: selection,
            },
            user_message="执行兼容工具",
            client_context=client_context,
            profile_user_id="u",
            session_id="s",
        )

        self.assertEqual(tool_calls, [{"type": "legacy_only", "value": "ok"}])
        self.assertEqual(rejections, [])

    def test_engine_accepts_native_carrier_for_native_schema_tool(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine._promote_narrated_tool_call = lambda final_output, **_kwargs: final_output
        engine._normalize_tool_call = lambda value, **_kwargs: dict(value or {})
        engine._describe_tool_call_rejection = lambda *_args, **_kwargs: "unexpected"
        client_context = ClientProtocolContext(
            requested_mode=ClientMode.QQ_TEXT,
            effective_mode=ClientMode.QQ_TEXT,
        )
        selection = CapabilitySelection(
            light_hints=(),
            tool_names=("send_file",),
            module_names=("file_handoff",),
            schema_tool_names=("send_file",),
            native_tool_names=("send_file",),
        )

        _final_output, tool_calls, rejections = engine._prepare_tool_round_decisions(
            final_output={
                "speech": "",
                "tool_call": None,
                NATIVE_TOOL_CALL_FIELD: {
                    "type": "send_file",
                    "target": "gen_049",
                    TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                    TOOL_INVOCATION_ID_FIELD: "call_native_send",
                },
                TOOL_CAPABILITY_SELECTION_FIELD: selection,
            },
            user_message="发我",
            client_context=client_context,
            profile_user_id="u",
            session_id="s",
        )

        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0]["type"], "send_file")
        self.assertEqual(tool_calls[0][TOOL_SOURCE_FIELD], NATIVE_OPENAI)
        self.assertEqual(rejections, [])

    def test_public_final_tool_call_strips_internal_native_metadata(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.resource_manifest = None
        engine._resolve_client_protocol_context = lambda _payload: ClientProtocolContext(
            requested_mode=ClientMode.SCENE_STATIC,
            effective_mode=ClientMode.SCENE_STATIC,
        )
        engine._normalize_tool_call = lambda value, **_kwargs: dict(value or {})
        engine._normalize_memory_tags = lambda _value: []
        engine._normalize_choices = lambda _value: []
        engine._get_persona_card_service = lambda: None
        engine._get_user_runtime_projection = lambda _profile_user_id: {
            "extra_bgm_tracks": [],
            "extra_scene_groups": [],
            "extra_character_outfits": [],
        }
        engine._get_output_adapter_registry = lambda: SimpleNamespace(
            normalize=lambda normalized, _client_context: normalized
        )

        normalized = normalize_final_output(
            engine,
            result={
                "speech": "我查一下。",
                "tool_call": {
                    "type": "web_search",
                    "query": "上海天气",
                    TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                    TOOL_INVOCATION_ID_FIELD: "call_native_1",
                },
            },
            visual_defaults={
                "emotion": "neutral",
                "outfit": "default",
                "major": "default",
                "minor": "default",
                "background": "default",
                "bgm": "none",
            },
            allow_tool_call=True,
            debug_enabled=False,
        )

        self.assertEqual(normalized["tool_call"], {"type": "web_search", "query": "上海天气"})
        self.assertNotIn(TOOL_SOURCE_FIELD, normalized["tool_call"])
        self.assertNotIn(TOOL_INVOCATION_ID_FIELD, normalized["tool_call"])

    def test_engine_tool_round_records_web_search_followup_for_final_response(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine._execute_tool_call = lambda **_kwargs: ToolExecutionResult(
            tool_type="web_search",
            followup_context="【AnySearch 联网搜索结果】上海今天晴。",
            stream_events=[{"type": "web_search_completed", "action": "search"}],
        )
        engine._record_tool_result_artifacts_in_task_workspace = lambda **_kwargs: ([], "")
        client_context = ClientProtocolContext(
            requested_mode=ClientMode.SCENE_STATIC,
            effective_mode=ClientMode.SCENE_STATIC,
        )
        tool_results: list[ToolExecutionResult] = []
        tool_events: list[dict] = []
        tool_followups: list[str] = []

        tool_result, current_events = engine._execute_and_record_tool_round(
            tool_call={"type": "web_search", "action": "search", "query": "上海天气"},
            final_output={"speech": "", "tool_call": None},
            tool_results=tool_results,
            tool_events=tool_events,
            tool_followups=tool_followups,
            tool_turns=[],
            recent_raw_for_turn=[],
            profile_user_id="u",
            session_id="s",
            character_pack_id="",
            now_ts=0,
            current_user_source_id="",
            client_context=client_context,
            memory_exclude_source_ids=[],
            request_context={},
        )

        self.assertIsNotNone(tool_result)
        self.assertEqual(current_events, [{"type": "web_search_completed", "action": "search"}])
        self.assertEqual(tool_events, current_events)
        self.assertIn("第 1 次工具（web_search）结果", tool_followups[0])
        self.assertIn("上海今天晴", tool_followups[0])

    def test_engine_tool_round_builds_native_anthropic_tool_result_history(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.memcore_manager = _NativeToolProjectionManager(
            NATIVE_ANTHROPIC,
            assistant_preface="我先试一下这个工具。",
        )
        engine._execute_tool_call = lambda **_kwargs: ToolExecutionResult(
            tool_type="mcp.demo.echo",
            followup_context="echo ok",
            stream_events=[{"type": "mcp_tool_completed", "status": "ok"}],
        )
        engine._record_tool_result_artifacts_in_task_workspace = lambda **_kwargs: ([], "workspace artifact recorded")
        client_context = ClientProtocolContext(
            requested_mode=ClientMode.SCENE_STATIC,
            effective_mode=ClientMode.SCENE_STATIC,
        )
        native_history: list[dict] = []

        engine._execute_and_record_tool_round(
            tool_call={
                "type": "mcp.demo.echo",
                "text": "hi",
                TOOL_SOURCE_FIELD: NATIVE_ANTHROPIC,
                TOOL_INVOCATION_ID_FIELD: "toolu_1",
                TOOL_MODEL_NAME_FIELD: "mcp_demo_echo_abcd123456",
            },
            final_output={"speech": "我先试一下这个工具。", "tool_call": None},
            tool_results=[],
            tool_events=[],
            tool_followups=[],
            tool_turns=[],
            recent_raw_for_turn=[],
            profile_user_id="u",
            session_id="s",
            character_pack_id="",
            now_ts=0,
            current_user_source_id="",
            client_context=client_context,
            memory_exclude_source_ids=[],
            request_context={},
            tool_history_turns=native_history,
            memcore_turn_id="turn-native-anthropic",
        )

        self.assertEqual([turn["role"] for turn in native_history], ["assistant", "user"])
        self.assertEqual(
            native_history[0]["content"][0],
            {"type": "text", "text": "我先试一下这个工具。"},
        )
        tool_use = native_history[0]["content"][1]
        tool_result = native_history[1]["content"][0]
        self.assertEqual(
            tool_use,
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "mcp_demo_echo_abcd123456",
                "input": {"text": "hi"},
            },
        )
        self.assertEqual(tool_result["type"], "tool_result")
        self.assertEqual(tool_result["tool_use_id"], "toolu_1")
        self.assertIn("echo ok", tool_result["content"])
        self.assertIn("workspace artifact recorded", tool_result["content"])
        self.assertNotIn("is_error", tool_result)

    def test_engine_tool_preface_dual_writes_visible_assistant_turn(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        memcore_records: list[dict] = []

        class FakeStore:
            def add_message(self, **kwargs):
                return {**kwargs, "source_id": "assistant-preface-1"}

        engine.store = FakeStore()
        engine._upsert_raw_record = lambda _record: None
        engine._append_memcore_turn_intermediate = lambda **kwargs: memcore_records.append(
            dict(kwargs["assistant_record"])
        )
        engine._memcore_owns_compaction = lambda: True
        preface_turns: list[dict[str, str]] = []
        recent_raw: list[dict] = []

        source_id = engine._record_assistant_preface_for_tool_call(
            tool_call={"type": "web_search", TOOL_SOURCE_FIELD: NATIVE_OPENAI},
            final_output={"speech": "我先查一下。"},
            preface_turns=preface_turns,
            recent_raw_for_turn=recent_raw,
            profile_user_id="u",
            session_id="s",
            character_pack_id="reimu",
            now_ts=100,
            date_label="2026-07-13",
            time_of_day="afternoon",
            memcore_turn_id="turn-1",
        )

        self.assertEqual(source_id, "assistant-preface-1")
        self.assertEqual(preface_turns[0]["speech"], "我先查一下。")
        self.assertEqual(recent_raw[0]["content"], "我先查一下。")
        self.assertEqual(memcore_records[0]["source_id"], "assistant-preface-1")

    def test_engine_parallel_batch_groups_anthropic_history_in_original_order(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.memcore_manager = _NativeToolProjectionManager(NATIVE_ANTHROPIC)
        barrier = threading.Barrier(2)

        def execute(**kwargs):
            call = kwargs["tool_call"]
            barrier.wait(timeout=2)
            if call["type"] == "web_search":
                time.sleep(0.04)
            return ToolExecutionResult(
                tool_type=call["type"],
                followup_context=f"result:{call['type']}",
                stream_events=[{"type": "tool_completed", "tool_type": call["type"], "status": "ok"}],
            )

        engine._execute_tool_call = execute
        engine._record_tool_result_artifacts_in_task_workspace = lambda **_kwargs: ([], "")
        client_context = ClientProtocolContext(
            requested_mode=ClientMode.SCENE_STATIC,
            effective_mode=ClientMode.SCENE_STATIC,
        )
        native_history: list[dict] = []
        accumulated: list[ToolExecutionResult] = []
        calls = [
            {
                "type": "web_search",
                "query": "日经指数",
                TOOL_SOURCE_FIELD: NATIVE_ANTHROPIC,
                TOOL_INVOCATION_ID_FIELD: "toolu_1",
            },
            {
                "type": "retrieve_memory",
                "query": "风险偏好",
                TOOL_SOURCE_FIELD: NATIVE_ANTHROPIC,
                TOOL_INVOCATION_ID_FIELD: "toolu_2",
            },
        ]

        results, _events = engine._execute_and_record_tool_batch(
            tool_calls=calls,
            final_output={"speech": "", "tool_call": None},
            tool_results=accumulated,
            tool_events=[],
            tool_followups=[],
            tool_turns=[],
            recent_raw_for_turn=[],
            profile_user_id="u",
            session_id="s",
            character_pack_id="",
            now_ts=100,
            current_user_source_id="user:1",
            client_context=client_context,
            memory_exclude_source_ids=[],
            request_context={},
            tool_history_turns=native_history,
            memcore_turn_id="turn-parallel-anthropic",
        )

        self.assertEqual([result.tool_type for result in results], ["web_search", "retrieve_memory"])
        self.assertEqual([result.tool_type for result in accumulated], ["web_search", "retrieve_memory"])
        self.assertEqual([turn["role"] for turn in native_history], ["assistant", "user"])
        self.assertEqual(
            [block["id"] for block in native_history[0]["content"]],
            ["toolu_1", "toolu_2"],
        )
        self.assertEqual(
            [block["tool_use_id"] for block in native_history[1]["content"]],
            ["toolu_1", "toolu_2"],
        )

    def test_engine_parallel_batch_builds_standard_openai_tool_history(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.memcore_manager = _NativeToolProjectionManager(
            NATIVE_OPENAI,
            assistant_preface="我一起查一下。",
        )
        engine._execute_tool_call = lambda **kwargs: ToolExecutionResult(
            tool_type=kwargs["tool_call"]["type"],
            followup_context=f"result:{kwargs['tool_call']['type']}",
            stream_events=[{"type": "tool_completed", "status": "ok"}],
        )
        engine._record_tool_result_artifacts_in_task_workspace = lambda **_kwargs: ([], "")
        client_context = ClientProtocolContext(
            requested_mode=ClientMode.SCENE_STATIC,
            effective_mode=ClientMode.SCENE_STATIC,
        )
        native_history: list[dict] = []
        tool_followups: list[str] = []
        calls = [
            {
                "type": "mcp.demo.echo",
                "text": "hi",
                TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                TOOL_INVOCATION_ID_FIELD: "call_1",
                TOOL_MODEL_NAME_FIELD: "mcp_demo_echo_abcd123456",
            },
            {
                "type": "web_search",
                "query": "日经指数",
                TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                TOOL_INVOCATION_ID_FIELD: "call_2",
            },
        ]

        engine._execute_and_record_tool_batch(
            tool_calls=calls,
            final_output={"speech": "我一起查一下。", "tool_call": None},
            tool_results=[],
            tool_events=[],
            tool_followups=tool_followups,
            tool_turns=[],
            recent_raw_for_turn=[],
            profile_user_id="u",
            session_id="s",
            character_pack_id="",
            now_ts=100,
            current_user_source_id="user:1",
            client_context=client_context,
            memory_exclude_source_ids=[],
            request_context={},
            tool_history_turns=native_history,
            memcore_turn_id="turn-parallel-openai",
        )

        self.assertEqual([turn["role"] for turn in native_history], ["assistant", "tool", "tool"])
        assistant_message = native_history[0]
        self.assertEqual(assistant_message["content"], "我一起查一下。")
        self.assertEqual(
            [call["id"] for call in assistant_message["tool_calls"]],
            ["call_1", "call_2"],
        )
        self.assertEqual(
            [call["function"]["name"] for call in assistant_message["tool_calls"]],
            ["mcp_demo_echo_abcd123456", "web_search"],
        )
        self.assertEqual(
            json.loads(assistant_message["tool_calls"][0]["function"]["arguments"]),
            {"text": "hi"},
        )
        self.assertEqual(
            [turn["tool_call_id"] for turn in native_history[1:]],
            ["call_1", "call_2"],
        )
        self.assertEqual(tool_followups, [])

    def test_engine_parallel_batch_isolates_one_tool_exception(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)

        def execute(**kwargs):
            call = kwargs["tool_call"]
            if call["type"] == "broken_tool":
                raise RuntimeError("boom")
            return ToolExecutionResult(
                tool_type=call["type"],
                followup_context="ok",
                stream_events=[{"type": "tool_completed", "status": "ok"}],
            )

        engine._execute_tool_call = execute
        engine._record_tool_result_artifacts_in_task_workspace = lambda **_kwargs: ([], "")
        client_context = ClientProtocolContext(
            requested_mode=ClientMode.SCENE_STATIC,
            effective_mode=ClientMode.SCENE_STATIC,
        )

        results, _events = engine._execute_and_record_tool_batch(
            tool_calls=[{"type": "broken_tool"}, {"type": "working_tool"}],
            final_output={"speech": "", "tool_call": None},
            tool_results=[],
            tool_events=[],
            tool_followups=[],
            tool_turns=[],
            recent_raw_for_turn=[],
            profile_user_id="u",
            session_id="s",
            character_pack_id="",
            now_ts=100,
            current_user_source_id="user:1",
            client_context=client_context,
            memory_exclude_source_ids=[],
            request_context={},
        )

        self.assertEqual([result.tool_type for result in results], ["broken_tool", "working_tool"])
        self.assertIn("<tool_use_error>", results[0].followup_context)
        self.assertEqual(results[1].followup_context, "ok")

    def test_tool_media_projection_write_failure_is_structured_before_next_model_request(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.memcore_manager = _NativeToolProjectionManager(NATIVE_OPENAI)
        engine._execute_tool_call = lambda **_kwargs: ToolExecutionResult(
            tool_type="load_material",
            followup_context="图片已加载。",
            model_image_inputs=[
                {
                    "attachment_id": "attachment-1",
                    "attachment_handle": "img_001",
                    "data_url": "data:image/png;base64,AAAA",
                }
            ],
        )
        engine._record_tool_result_artifacts_in_task_workspace = lambda **_kwargs: ([], "")
        client_context = ClientProtocolContext(
            requested_mode=ClientMode.SCENE_STATIC,
            effective_mode=ClientMode.SCENE_STATIC,
        )

        results, _events = engine._execute_and_record_tool_batch(
            tool_calls=[
                {
                    "type": "load_material",
                    TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                    TOOL_INVOCATION_ID_FIELD: "call-image",
                }
            ],
            final_output={"speech": "", "tool_call": None},
            tool_results=[],
            tool_events=[],
            tool_followups=[],
            tool_turns=[],
            recent_raw_for_turn=[],
            profile_user_id="u",
            session_id="s",
            character_pack_id="",
            now_ts=100,
            current_user_source_id="user:1",
            client_context=client_context,
            memory_exclude_source_ids=[],
            request_context={},
            tool_history_turns=[],
            memcore_turn_id="turn-image",
        )

        failure = engine._tool_batch_memcore_failure(results)
        self.assertEqual(
            failure,
            {
                "status": "failed",
                "reason": "tool_media_record_failed",
                "detail": "tool_media_unavailable",
            },
        )

    def test_engine_tool_batch_records_sanitized_memcore_trace_once_per_call(self) -> None:
        class FakeMemcoreManager:
            enabled = True

            def __init__(self):
                self.calls = []

            def record_tool_batch(self, **kwargs):
                self.calls.append(kwargs)
                return {
                    "ok": True,
                    "status": "recorded",
                    "exchanges": [
                        {
                            "tool_use_source_id": "trace-use-1",
                            "tool_result_source_id": "trace-result-1",
                        }
                    ],
                }

        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        manager = FakeMemcoreManager()
        engine.memcore_manager = manager
        engine._execute_tool_call = lambda **kwargs: ToolExecutionResult(
            tool_type=kwargs["tool_call"]["type"],
            followup_context="查询完成 Authorization: Bearer top-secret",
        )
        engine._record_tool_result_artifacts_in_task_workspace = lambda **_kwargs: ([], "")
        client_context = ClientProtocolContext(
            requested_mode=ClientMode.SCENE_STATIC,
            effective_mode=ClientMode.SCENE_STATIC,
        )
        recorded_ids: set[str] = set()
        prompt_exclusions: list[str] = []
        call = {
            "type": "web_search",
            "query": "Akane",
            "api_key": "do-not-store",
            "access_token": "also-do-not-store",
            "absolute_path": "F:/private folder/file.txt",
            TOOL_SOURCE_FIELD: NATIVE_ANTHROPIC,
            TOOL_INVOCATION_ID_FIELD: "toolu_trace",
        }

        for _ in range(2):
            engine._execute_and_record_tool_batch(
                tool_calls=[call],
                final_output={"speech": "", "tool_call": None},
                tool_results=[],
                tool_events=[],
                tool_followups=[],
                tool_turns=[],
                recent_raw_for_turn=[],
                profile_user_id="u",
                session_id="s",
                character_pack_id="reimu",
                now_ts=100,
                current_user_source_id="user:1",
                client_context=client_context,
                memory_exclude_source_ids=[],
                request_context={},
                prompt_exclude_source_ids=prompt_exclusions,
                recorded_tool_call_ids=recorded_ids,
                memcore_turn_id="turn-1",
            )

        self.assertEqual(len(manager.calls), 1)
        self.assertEqual(prompt_exclusions, ["trace-use-1", "trace-result-1"])
        stored = manager.calls[0]
        self.assertEqual(stored["turn_id"], "turn-1")
        exchange = stored["exchanges"][0]
        self.assertEqual(exchange["tool_call_id"], "toolu_trace")
        self.assertEqual(exchange["tool_input"], {"query": "Akane"})
        self.assertEqual(exchange["source"], NATIVE_ANTHROPIC)
        self.assertNotIn("top-secret", exchange["result"])
        self.assertIn("[redacted]", exchange["result"])

    def test_tool_trace_write_failure_preserves_real_result_for_model_followup(self) -> None:
        class ClosedTurnManager:
            enabled = True

            @staticmethod
            def record_tool_batch(**_kwargs):
                return {
                    "ok": False,
                    "status": "failed",
                    "reason": "turn_not_open",
                    "exchanges": [],
                }

        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.memcore_manager = ClosedTurnManager()
        engine._execute_tool_call = lambda **_kwargs: ToolExecutionResult(
            tool_type="send_file",
            followup_context="文件 gen_009 已经真实发送。",
        )
        engine._record_tool_result_artifacts_in_task_workspace = lambda **_kwargs: ([], "")
        client_context = ClientProtocolContext(
            requested_mode=ClientMode.QQ_TEXT,
            effective_mode=ClientMode.QQ_TEXT,
        )
        tool_followups: list[str] = []

        results, _events = engine._execute_and_record_tool_batch(
            tool_calls=[
                {
                    "type": "send_file",
                    "target": "gen_009",
                    TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                    TOOL_INVOCATION_ID_FIELD: "call-send-file",
                }
            ],
            final_output={"speech": "", "tool_call": None},
            tool_results=[],
            tool_events=[],
            tool_followups=tool_followups,
            tool_turns=[],
            recent_raw_for_turn=[],
            profile_user_id="u",
            session_id="s",
            character_pack_id="reimu",
            now_ts=100,
            current_user_source_id="user:send",
            client_context=client_context,
            memory_exclude_source_ids=[],
            request_context={},
            tool_history_turns=[],
            memcore_turn_id="closed-turn",
        )

        self.assertEqual(
            engine._tool_batch_memcore_failure(results),
            {
                "status": "failed",
                "reason": "tool_trace_record_failed",
                "detail": "turn_not_open",
            },
        )
        followup = "\n".join(tool_followups)
        self.assertIn("【本轮真实工具结果】", followup)
        self.assertIn("文件 gen_009 已经真实发送", followup)
        self.assertIn("不要把上下文存储状态误认为工具执行失败", followup)
        self.assertNotIn("会话记忆暂时读取失败", followup)

    def test_failed_operation_is_recorded_as_memcore_tool_result(self) -> None:
        class FakeMemcoreManager:
            enabled = True

            def __init__(self):
                self.calls = []

            def record_tool_batch(self, **kwargs):
                self.calls.append(kwargs)
                return {
                    "ok": True,
                    "status": "recorded",
                    "exchanges": [
                        {
                            "tool_use_source_id": "trace-use-failed",
                            "tool_result_source_id": "trace-result-failed",
                        }
                    ],
                }

        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        manager = FakeMemcoreManager()
        engine.memcore_manager = manager
        engine._execute_tool_call = lambda **_kwargs: operation_tool_result(
            tool_type="separate_audio_stems",
            operation_result={
                "ok": False,
                "error": "executor_offline",
                "followup_context": "本地媒体执行器当前离线。",
            },
        )
        engine._record_tool_result_artifacts_in_task_workspace = lambda **_kwargs: ([], "")
        client_context = ClientProtocolContext(
            requested_mode=ClientMode.QQ_TEXT,
            effective_mode=ClientMode.QQ_TEXT,
        )

        tool_followups: list[str] = []
        results, events = engine._execute_and_record_tool_batch(
            tool_calls=[
                {
                    "type": "separate_audio_stems",
                    "source_id": "file_031",
                    TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                    TOOL_INVOCATION_ID_FIELD: "call_failed_media",
                }
            ],
            final_output={"speech": "我来处理。", "tool_call": None},
            tool_results=[],
            tool_events=[],
            tool_followups=tool_followups,
            tool_turns=[],
            recent_raw_for_turn=[],
            profile_user_id="u",
            session_id="s",
            character_pack_id="reimu",
            now_ts=100,
            current_user_source_id="user:failed",
            client_context=client_context,
            memory_exclude_source_ids=[],
            request_context={},
            tool_history_turns=[],
            memcore_turn_id="turn-failed-media",
        )

        self.assertEqual(events[0]["type"], "tool_execution_failed")
        self.assertIn("<tool_use_error>", results[0].followup_context)
        self.assertEqual(len(manager.calls), 1)
        exchange = manager.calls[0]["exchanges"][0]
        self.assertEqual(exchange["result_status"], "error")
        self.assertIn("本地媒体执行器当前离线", exchange["result"])
        self.assertIn("不要声称已经完成", exchange["result"])

    def test_generated_artifact_handle_is_recorded_in_memcore_tool_result(self) -> None:
        class FakeMemcoreManager:
            enabled = True

            def __init__(self):
                self.calls = []

            def record_tool_batch(self, **kwargs):
                self.calls.append(kwargs)
                return {
                    "ok": True,
                    "status": "recorded",
                    "exchanges": [
                        {
                            "tool_use_source_id": "trace-use-artifact",
                            "tool_result_source_id": "trace-result-artifact",
                        }
                    ],
                }

        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        manager = FakeMemcoreManager()
        engine.memcore_manager = manager
        engine._execute_tool_call = lambda **kwargs: ToolExecutionResult(
            tool_type=kwargs["tool_call"]["type"],
            followup_context="文件已经生成。",
            stream_events=[
                {
                    "type": "generated_file_ready",
                    "generated_file": {
                        "generated_handle": "gen_009",
                        "output_title": "整理结果",
                        "output_format": "docx",
                    },
                }
            ],
        )
        engine._record_tool_result_artifacts_in_task_workspace = lambda **_kwargs: ([], "")
        client_context = ClientProtocolContext(
            requested_mode=ClientMode.QQ_TEXT,
            effective_mode=ClientMode.QQ_TEXT,
        )

        engine._execute_and_record_tool_batch(
            tool_calls=[
                {
                    "type": "compose_file",
                    TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                    TOOL_INVOCATION_ID_FIELD: "call_artifact",
                }
            ],
            final_output={"speech": "", "tool_call": None},
            tool_results=[],
            tool_events=[],
            tool_followups=[],
            tool_turns=[],
            recent_raw_for_turn=[],
            profile_user_id="u",
            session_id="s",
            character_pack_id="reimu",
            now_ts=100,
            current_user_source_id="user:artifact",
            client_context=client_context,
            memory_exclude_source_ids=[],
            request_context={},
            memcore_turn_id="turn-artifact",
        )

        exchange = manager.calls[0]["exchanges"][0]
        self.assertIn("handle=gen_009", exchange["result"])
        self.assertIn("直接调用 send_file", exchange["result"])

    def test_generated_artifact_batch_exposes_all_handles_for_send_file(self) -> None:
        result = tool_orchestration_engine.append_structured_artifact_receipts(
            "分轨完成。",
            stream_events=[
                {
                    "type": "generated_file_ready",
                    "generated_file": {
                        "generated_handle": "gen_031",
                        "output_title": "歌曲_人声",
                        "output_format": "wav",
                    },
                },
                {
                    "type": "generated_file_ready",
                    "generated_file": {
                        "generated_handle": "gen_032",
                        "output_title": "歌曲_伴奏",
                        "output_format": "wav",
                    },
                },
            ],
        )

        self.assertIn("handle=gen_031", result)
        self.assertIn("handle=gen_032", result)
        self.assertIn("直接调用 send_file", result)

    def test_final_output_preserves_internal_native_tool_batch(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.resource_manifest = None
        engine._resolve_client_protocol_context = lambda _payload: ClientProtocolContext(
            requested_mode=ClientMode.SCENE_STATIC,
            effective_mode=ClientMode.SCENE_STATIC,
        )
        engine._normalize_tool_call = lambda value, **_kwargs: value

        normalized = normalize_final_output(
            engine,
            result={
                NATIVE_TOOL_CALLS_FIELD: [
                    {"type": "web_search", "query": "A"},
                    {"type": "web_search", "query": "B"},
                ],
                "tool_call": None,
            },
            visual_defaults={
                "emotion": "normal",
                "outfit": "default",
                "major": "default",
                "minor": "default",
                "background": "default",
                "bgm": "none",
            },
            allow_tool_call=True,
            debug_enabled=False,
        )

        self.assertEqual(len(normalized[NATIVE_TOOL_CALLS_FIELD]), 2)
        self.assertEqual(normalized["speech"], "")

    def test_final_output_carries_frozen_capability_selection_for_json_tool_call(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.resource_manifest = None
        engine._resolve_client_protocol_context = lambda _payload: ClientProtocolContext(
            requested_mode=ClientMode.QQ_TEXT,
            effective_mode=ClientMode.QQ_TEXT,
        )
        selection = CapabilitySelection(
            light_hints=(),
            tool_names=("separate_audio_stems",),
            module_names=("media_tools",),
            schema_tool_names=("separate_audio_stems",),
        )
        captured: dict[str, object] = {}

        def normalize(value, **kwargs):
            captured.update(kwargs)
            return {
                **dict(value or {}),
                TOOL_CAPABILITY_SELECTION_FIELD: kwargs.get("capability_selection"),
            }

        engine._normalize_tool_call = normalize
        normalized = normalize_final_output(
            engine,
            result={
                "speech": "我来分离。",
                "tool_call": {
                    "type": "separate_audio_stems",
                    "source_id": "file_031",
                },
            },
            visual_defaults={
                "emotion": "normal",
                "outfit": "default",
                "major": "default",
                "minor": "default",
                "background": "default",
                "bgm": "none",
            },
            allow_tool_call=True,
            debug_enabled=False,
            capability_selection=selection,
        )

        self.assertIs(captured["capability_selection"], selection)
        self.assertIs(normalized[TOOL_CAPABILITY_SELECTION_FIELD], selection)
        self.assertNotIn(TOOL_CAPABILITY_SELECTION_FIELD, normalized["tool_call"])

    def test_frozen_capability_selection_executes_json_tool_call_without_reresolution(self) -> None:
        class FrozenHandler:
            tool_type = "separate_audio_stems"

            def __init__(self) -> None:
                self.executions: list[dict] = []

            def normalize_call(self, value):
                if str(value.get("type") or "") != self.tool_type:
                    return None
                source_id = str(value.get("source_id") or "").strip()
                if not source_id:
                    return None
                return {"type": self.tool_type, "source_id": source_id}

            def execute(self, *, call, context):
                del context
                self.executions.append(dict(call))
                return ToolExecutionResult(
                    tool_type=self.tool_type,
                    stream_events=[{"type": "audio_stems_ready", "status": "ok"}],
                    followup_context="人声和伴奏已经分离完成。",
                )

        handler = FrozenHandler()
        selection = CapabilitySelection(
            light_hints=(),
            tool_names=(handler.tool_type,),
            module_names=("media_tools",),
            schema_tool_names=(handler.tool_type,),
            resolved_handlers=MappingProxyType({handler.tool_type: handler}),
        )
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.tool_handlers = {}
        engine._resolve_tool_handlers = lambda **kwargs: {
            name: selection.resolved_handlers[name]
            for name in selection.tool_names
            if name in selection.resolved_handlers
        }

        normalized = tool_orchestration_engine.normalize_tool_call(
            engine,
            {
                "type": handler.tool_type,
                "source_id": "file_031",
            },
            profile_user_id="u",
            session_id="s",
            capability_selection=selection,
        )

        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertIs(normalized[TOOL_CAPABILITY_SELECTION_FIELD], selection)
        result = tool_orchestration_engine.execute_tool_call(
            engine,
            profile_user_id="u",
            session_id="s",
            tool_call=normalized,
            visual_payload={},
            now_ts=100,
        )

        self.assertIsInstance(result, ToolExecutionResult)
        self.assertEqual(handler.executions, [{"type": handler.tool_type, "source_id": "file_031"}])
        self.assertIn("分离完成", result.followup_context)

    def test_advertised_but_unavailable_tool_returns_structured_terminal_result(self) -> None:
        class UnavailableHandler:
            tool_type = "separate_audio_stems"

            def normalize_call(self, value):
                source_id = str(value.get("source_id") or "").strip()
                if not source_id:
                    return None
                return {"type": self.tool_type, "source_id": source_id}

            def execute(self, **_kwargs):
                raise AssertionError("unavailable tool must not dispatch")

        handler = UnavailableHandler()
        selection = CapabilitySelection(
            light_hints=(),
            tool_names=(),
            module_names=(),
            schema_tool_names=(handler.tool_type,),
            disclosures=(
                CapabilityDisclosure(
                    capability_id="media_tools",
                    state="unavailable",
                    summary="媒体处理暂不可用。",
                    reason="本地执行器离线。",
                    activation="电脑恢复连接后重试。",
                    tool_names=(handler.tool_type,),
                ),
            ),
            resolved_handlers=MappingProxyType({handler.tool_type: handler}),
        )
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.tool_handlers = {}
        engine._resolve_tool_handlers = lambda **_kwargs: {}

        normalized = tool_orchestration_engine.normalize_tool_call(
            engine,
            {
                "type": handler.tool_type,
                "source_id": "file_031",
            },
            profile_user_id="u",
            session_id="s",
            capability_selection=selection,
        )

        self.assertIsNotNone(normalized)
        assert normalized is not None
        result = tool_orchestration_engine.execute_tool_call(
            engine,
            profile_user_id="u",
            session_id="s",
            tool_call=normalized,
            visual_payload={},
            now_ts=100,
        )

        self.assertIsInstance(result, ToolExecutionResult)
        assert result is not None
        self.assertEqual(result.stream_events[0]["status"], "unavailable")
        self.assertEqual(result.stream_events[0]["reason"], "not_available")
        self.assertIn("本地执行器离线", result.followup_context)
        self.assertIn("不要假装已经执行", result.followup_context)

    def test_known_tool_with_bad_args_returns_structured_rejected_result(self) -> None:
        class Handler:
            tool_type = "separate_audio_stems"

            def normalize_call(self, value):
                source_id = str(value.get("source_id") or "").strip()
                if not source_id:
                    return None
                return {"type": self.tool_type, "source_id": source_id}

            def execute(self, **_kwargs):
                raise AssertionError("invalid tool call must not dispatch")

        handler = Handler()
        selection = CapabilitySelection(
            light_hints=(),
            tool_names=(handler.tool_type,),
            module_names=("media_tools",),
            schema_tool_names=(handler.tool_type,),
            resolved_handlers=MappingProxyType({handler.tool_type: handler}),
        )
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.tool_handlers = {}
        engine._resolve_tool_handlers = lambda **_kwargs: {handler.tool_type: handler}

        normalized = tool_orchestration_engine.normalize_tool_call(
            engine,
            {"type": handler.tool_type},
            profile_user_id="u",
            session_id="s",
            capability_selection=selection,
        )

        self.assertIsNotNone(normalized)
        assert normalized is not None
        result = tool_orchestration_engine.execute_tool_call(
            engine,
            profile_user_id="u",
            session_id="s",
            tool_call=normalized,
            visual_payload={},
            now_ts=100,
        )

        self.assertIsInstance(result, ToolExecutionResult)
        assert result is not None
        self.assertEqual(result.stream_events[0]["status"], "rejected")
        self.assertEqual(result.stream_events[0]["reason"], "bad_args")
        self.assertIn("参数不完整或格式不对", result.followup_context)

    def test_engine_native_tool_trace_preserves_error_and_cancelled_statuses(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        error = ToolExecutionResult(
            tool_type="web_search",
            followup_context="<tool_use_error>网络不可用</tool_use_error>",
        )
        cancelled = ToolExecutionResult(
            tool_type="web_search",
            followup_context="工具已取消。",
            stream_events=[{"type": "tool_cancelled", "status": "cancelled"}],
        )

        self.assertEqual(engine._tool_result_trace_status(error), "error")
        self.assertEqual(engine._tool_result_trace_status(cancelled), "cancelled")
        self.assertTrue(engine._tool_result_is_error(error))
        self.assertTrue(engine._tool_result_is_error(cancelled))

    def test_tool_call_signature_ignores_native_invocation_metadata(self) -> None:
        first = tool_orchestration_engine.tool_call_signature(
            {
                "type": "web_search",
                "action": "search",
                "query": "上海天气",
                TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                TOOL_INVOCATION_ID_FIELD: "call_a",
            }
        )
        second = tool_orchestration_engine.tool_call_signature(
            {
                "type": "web_search",
                "action": "search",
                "query": "上海天气",
                TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                TOOL_INVOCATION_ID_FIELD: "call_b",
            }
        )

        self.assertEqual(first, second)

    def test_web_search_signature_ignores_search_result_limit_for_duplicate_guard(self) -> None:
        first = tool_orchestration_engine.tool_call_signature(
            {
                "type": "web_search",
                "action": "search",
                "query": "上海天气",
                "max_results": 5,
            }
        )
        second = tool_orchestration_engine.tool_call_signature(
            {
                "type": "web_search",
                "action": "search",
                "query": "上海天气",
                "max_results": 3,
            }
        )

        self.assertEqual(first, second)

    def test_stream_final_response_passes_native_tools_to_runtime(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        schema = build_openai_native_tool_from_spec(WEB_SEARCH_TOOL_SPEC)
        captured: dict[str, object] = {}

        def fake_stream_chat_json(**kwargs):
            captured.update(kwargs)
            if False:
                yield {}
            return SimpleNamespace(
                parsed={"speech": "ok", "tool_call": None},
                error="",
                latest_emotion="",
                latest_speech="",
                latest_reply_medium="",
                native_preface_text="我先查一下。",
            )

        engine.llm = SimpleNamespace(stream_chat_json=fake_stream_chat_json)
        engine._prepare_final_response_context = lambda **_kwargs: {
            "system_prompt": "system",
            "user_prompt": "user",
            "fallback": {"speech": "", "tool_call": None},
            "visual_defaults": {},
            "debug_enabled": False,
            "allow_tool_call": True,
            "native_tools": [schema],
            "native_tool_choice": "auto",
            "system_extra_blocks": [],
            "history_turns": [],
            "prompt_audit_sections": [],
        }
        engine._resolve_turn_speaker_identity = lambda *_args, **_kwargs: {"assistant_name": "Akane"}
        engine._normalize_final_output = lambda **kwargs: kwargs["result"]

        events, result = exhaust_generator_return(
            engine._stream_final_response(
                session_id="s",
                profile_user_id="u",
                user_message="查一下天气",
                recent_raw=[],
                recent_episodic_summaries=[],
                recent_semantic_summaries=[],
                confirmed_snippets=[],
                now_ts=0,
            )
        )

        self.assertEqual(events, [{"type": "turn_start", "speaker": "Akane"}])
        self.assertEqual(result.pop("_memory_annotation_status"), "missing")
        self.assertFalse(result.pop("_memory_metadata_present"))
        self.assertEqual(
            result,
            {"speech": "ok", "tool_call": None, "_native_preface_text": "我先查一下。"},
        )
        self.assertEqual(captured["native_tools"], [schema])
        self.assertEqual(captured["native_tool_choice"], "auto")

    def test_stream_final_response_does_not_retry_after_speech_is_delivered(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        prompts = []
        metrics = []

        class FakeLLM:
            def stream_chat_json(self, **kwargs):
                prompts.append(kwargs["user_prompt"])
                if len(prompts) == 1:
                    yield {"type": "speech_segment", "text": "我在认真听你说，要不要再多告诉我一点？"}
                    return SimpleNamespace(
                        parsed={"speech": "我在认真听你说，要不要再多告诉我一点？", "tool_call": None},
                        error="",
                        latest_emotion="",
                        latest_speech="",
                        latest_reply_medium="",
                    )
                yield {"type": "speech_segment", "text": "这是重试后的完整答复。"}
                return SimpleNamespace(
                    parsed={"speech": "这是重试后的完整答复。", "tool_call": None},
                    error="",
                    latest_emotion="",
                    latest_speech="",
                    latest_reply_medium="",
                )

            def record_metric(self, name):
                metrics.append(name)

        engine.llm = FakeLLM()
        engine._prepare_final_response_context = lambda **_kwargs: {
            "system_prompt": "system",
            "user_prompt": "user",
            "fallback": {"speech": "我在认真听你说，要不要再多告诉我一点？", "tool_call": None},
            "visual_defaults": {},
            "debug_enabled": False,
            "allow_tool_call": True,
            "native_tools": [],
            "native_tool_choice": "",
            "system_extra_blocks": [],
            "history_turns": [],
            "prompt_audit_sections": [],
        }
        engine._resolve_turn_speaker_identity = lambda *_args, **_kwargs: {"assistant_name": "Akane"}
        engine._normalize_final_output = lambda **kwargs: kwargs["result"]

        events, result = exhaust_generator_return(
            engine._stream_final_response(
                session_id="s",
                profile_user_id="u",
                user_message="查一下天气",
                recent_raw=[],
                recent_episodic_summaries=[],
                recent_semantic_summaries=[],
                confirmed_snippets=[],
                now_ts=0,
            )
        )

        self.assertEqual(
            events,
            [
                {"type": "turn_start", "speaker": "Akane"},
                {"type": "speech_segment", "text": "我在认真听你说，要不要再多告诉我一点？"},
            ],
        )
        self.assertEqual(result["speech"], "我在认真听你说，要不要再多告诉我一点？")
        self.assertEqual(len(prompts), 1)
        self.assertEqual(metrics, [])

    def test_tool_working_stream_event_is_in_progress_only(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)

        event = engine._build_tool_working_stream_event({"type": "web_search"})

        self.assertEqual(event["type"], "assistant_working")
        self.assertEqual(event["status"], "running")
        self.assertEqual(event["phase"], "tool_call")
        self.assertEqual(event["tool_type"], "web_search")
        self.assertNotIn("done", str(event).lower())

    def test_final_fallback_and_progress_placeholders_are_retryable(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)

        self.assertTrue(
            engine._is_retryable_final_output({"speech": "我在认真听你说，要不要再多告诉我一点？", "tool_call": None})
        )
        self.assertTrue(engine._is_retryable_final_output({"speech": "还没处理完", "tool_call": None}))
        self.assertTrue(
            engine._is_retryable_final_output(
                {"speech": "已有完整答复。", "tool_call": None},
                parse_fallback=True,
            )
        )
        self.assertFalse(
            engine._is_retryable_final_output(
                {"speech": "我继续查一下。", "tool_call": {"type": "web_search", "query": "日经225"}}
            )
        )
        self.assertFalse(
            engine._is_retryable_final_output({"speech": "这是基于现有证据形成的完整结论。", "tool_call": None})
        )

    def test_transient_web_search_failure_allows_alternate_research_round(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)

        self.assertFalse(
            engine._should_stop_after_tool_events(
                [
                    {
                        "type": "web_search_completed",
                        "status": "unavailable",
                        "reason": "mcp_tool_call_timeout",
                    }
                ]
            )
        )
        self.assertTrue(
            engine._should_stop_after_tool_events(
                [{"type": "web_search_completed", "status": "unavailable", "reason": "missing_config"}]
            )
        )
        self.assertTrue(
            engine._should_stop_after_tool_events(
                [{"type": "other_tool_completed", "status": "unavailable", "reason": "timeout"}]
            )
        )
        self.assertFalse(engine._should_stop_after_tool_events([{"type": "web_search_completed", "status": "ok"}]))

    def test_tool_unavailable_stop_reason_tells_model_not_to_retry(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine._merge_extra_user_context = AkaneMemoryEngine._merge_extra_user_context.__get__(
            engine, AkaneMemoryEngine
        )

        context = engine._build_tool_round_extra_context(
            turn_extra_user_context="",
            tool_followups=["AnySearch 联网能力暂时不可用。"],
            allow_more=False,
            stop_reason="tool_unavailable",
        )

        self.assertIn("工具返回不可用或失败状态", context)
        self.assertIn("本轮不要再调用工具", context)

    def test_structured_native_history_does_not_rewrite_original_extra_context(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        context = engine._build_tool_round_extra_context(
            turn_extra_user_context="stable original event context",
            tool_followups=[],
            allow_more=False,
            stop_reason="tool_budget_exhausted",
        )

        self.assertEqual(context, "stable original event context")


class NativeDescriptionSanitizationTests(unittest.TestCase):
    """N1a: legacy tool_call envelope teaching is stripped from native descriptions."""

    def test_strip_removes_envelope_clauses_keeps_semantics(self) -> None:
        from companion_v01.native_tool_schema import _strip_legacy_envelope_clauses

        text = '- demo：当用户要做某事时使用。格式为 {"type":"demo","q":"x"}。q 要写具体内容，不要写空泛句。'
        cleaned = _strip_legacy_envelope_clauses(text)
        self.assertNotIn("格式为", cleaned)
        self.assertNotIn('{"type"', cleaned)
        # Real semantics (what/when to use) survive.
        self.assertIn("当用户要做某事时使用", cleaned)
        self.assertIn("q 要写具体内容", cleaned)

    def test_strip_never_returns_empty(self) -> None:
        from companion_v01.native_tool_schema import _strip_legacy_envelope_clauses

        # If a description is *only* an envelope clause, fall back to the original
        # rather than emit an empty tool description.
        only_envelope = '格式为 {"type":"x"}。'
        self.assertEqual(_strip_legacy_envelope_clauses(only_envelope), only_envelope)

    def test_fallback_handler_descriptions_have_no_legacy_envelope(self) -> None:
        # Tools without a precise input_schema fall back to build_prompt_instruction
        # (written for the legacy prompt). The native spec must not carry the
        # tool_call envelope teaching out of that fallback text.
        from companion_v01.native_tool_schema import build_openai_native_tool_specs
        from companion_v01.tool_runtime import CallNPCToolHandler, ComposeFileToolHandler

        handlers = {
            "compose_file": ComposeFileToolHandler(generated_file_service=None),
            "call_npc": CallNPCToolHandler(
                npc_runtime=None,
                describe_scene=lambda _ctx: "",
                build_followup_context=lambda _ctx: "",
            ),
        }
        specs = build_openai_native_tool_specs(handlers)
        self.assertEqual(len(specs), 2)
        for spec in specs:
            desc = spec["function"]["description"]
            self.assertTrue(desc.strip(), spec["function"]["name"])
            self.assertNotIn("格式为", desc)
            self.assertNotIn("tool_call", desc)
            self.assertNotIn('{"type"', desc)
            self.assertEqual(spec["function"]["parameters"]["additionalProperties"], False)


class FakePromptHandler:
    def __init__(self, name: str) -> None:
        self.name = name

    def build_prompt_instruction(self) -> str:
        return f"- {self.name}: available"


class FakeNativeHandler(FakePromptHandler):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.tool_type = name

    def tool_metadata(self):
        return TOOL_METADATA_BY_TYPE.get(self.tool_type)


class FakeExecutableWebSearchHandler(FakePromptHandler):
    tool_type = "web_search"

    def __init__(self) -> None:
        super().__init__("web_search")

    def normalize_call(self, value):
        if not isinstance(value, dict) or value.get("type") != "web_search":
            return None
        return dict(value)


class FakePromptProfile:
    supports_thought_debug = False
    system_prompt_override = ""

    def includes(self, module) -> bool:
        from companion_v01.prompt_profiles import PromptModule

        return module in {
            PromptModule.TOOLS,
        }

    def mode_prompt_override(self, *, debug_enabled: bool = False) -> str:
        return ""

    def to_public_dict(self) -> dict[str, object]:
        return {"name": "fake"}


class FakePromptBuilder:
    def build_final_generation_context(self, **kwargs):
        return {
            "system_prompt": "system",
            "user_prompt": "user",
            "fallback": {"speech": "", "tool_call": None},
            "visual_defaults": dict(kwargs.get("visual_defaults") or {}),
            "debug_enabled": bool(kwargs.get("debug_enabled")),
            "tool_prompt_context": str(kwargs.get("tool_prompt_context") or ""),
        }


def build_native_context_engine(*, selected_tool_names: tuple[str, ...]) -> AkaneMemoryEngine:
    engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
    handlers = {
        "web_search": FakeNativeHandler("web_search"),
        "retrieve_memory": FakeNativeHandler("retrieve_memory"),
        "read_memory_timeline": FakeNativeHandler("read_memory_timeline"),
        "send_file": FakeNativeHandler("send_file"),
    }
    selection = SimpleNamespace(
        module_names=(),
        light_hints=(),
        tool_names=selected_tool_names,
        layer_names=(),
    )
    engine.resource_manifest = None
    engine.store = SimpleNamespace()
    engine.vision_service = None
    engine.care_feature_status = lambda: {"enabled": True}
    engine.care_enabled_for_context = lambda **_kwargs: True
    engine.gift_service = SimpleNamespace(
        build_pending_prompt_context=lambda **_kwargs: "",
        resolve_focus_asset=lambda **_kwargs: None,
    )
    engine.llm = SimpleNamespace(
        chat_supports_native_tools=lambda: True,
        record_metric=lambda _name: None,
    )
    engine._get_prompt_profile_registry = lambda: SimpleNamespace(
        resolve=lambda _client_context, **_kwargs: FakePromptProfile()
    )
    engine._get_prompt_builder = lambda: FakePromptBuilder()
    engine._get_user_runtime_projection = lambda _profile_user_id: {
        "extra_bgm_tracks": [],
        "extra_scene_groups": [],
        "extra_character_outfits": [],
    }
    engine._split_history_records = lambda **_kwargs: ([], {})
    engine._render_current_message_line = lambda **_kwargs: "user: message"
    engine._get_attachment_inbox_service = lambda: None
    engine._get_generated_file_service = lambda: None
    engine._get_workspace_file_service = lambda: None
    engine._get_task_workspace_service = lambda: None
    engine._get_persona_card_service = lambda: None
    engine._build_desktop_pet_character_pack_prompt_context = lambda **_kwargs: {
        "system_context": "",
        "reference_context": "",
        "active_id": "",
    }
    engine._merge_prompt_persona_contexts = lambda _character_pack, _profile: {
        "system_context": "",
        "reference_context": "",
        "active_id": "",
    }
    engine._resolve_current_visual_payload = lambda **_kwargs: None
    engine._build_memory_relationship_context = lambda **_kwargs: ""
    engine._build_extra_context_audit_sections = lambda _candidates: []
    engine._resolve_capability_selection = lambda **_kwargs: selection
    engine._resolve_tool_handlers = lambda **_kwargs: {
        name: handlers[name] for name in selected_tool_names if name in handlers
    }
    return engine


class FakeClient:
    def __init__(self, protocol: str, *, base_url: str = "") -> None:
        self._akane_protocol = protocol
        self.base_url = base_url


def exhaust_generator_return(generator):
    events = []
    while True:
        try:
            events.append(next(generator))
        except StopIteration as exc:
            return events, exc.value


if __name__ == "__main__":
    unittest.main()
