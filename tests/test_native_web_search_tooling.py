from __future__ import annotations

import unittest
from types import SimpleNamespace

import config
from companion_v01 import tool_orchestration_engine
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.final_output_engine import normalize_final_output
from companion_v01.llm_runtime import LLMRuntime, ModelBundle
from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.tool_invocation import NATIVE_OPENAI, NATIVE_TOOL_CALL_FIELD, TOOL_INVOCATION_ID_FIELD, TOOL_SOURCE_FIELD
from companion_v01.tool_runtime import TOOL_METADATA_BY_TYPE, ToolExecutionResult


class NativeWebSearchToolingTests(unittest.TestCase):
    def test_native_web_search_schema_is_default_off(self) -> None:
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
        try:
            config.ENABLE_NATIVE_TOOL_DECISION = True
            config.NATIVE_TOOL_DECISION_ALLOWLIST = "web_search,retrieve_memory,read_memory_timeline"

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

    def test_native_tool_decision_plan_enables_single_prompt_channel(self) -> None:
        original_enabled = getattr(config, "ENABLE_NATIVE_TOOL_DECISION", False)
        original_allowlist = getattr(config, "NATIVE_TOOL_DECISION_ALLOWLIST", "web_search")
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

    def test_default_allowlist_includes_validated_read_only_tools(self) -> None:
        # 5e/6b: the shipped default allowlist contains the low-risk read-only
        # tools. Read the class field default (immune to .env / other tests).
        from config import Settings

        default = str(Settings.model_fields["NATIVE_TOOL_DECISION_ALLOWLIST"].default or "")
        self.assertEqual(
            {item.strip() for item in default.split(",") if item.strip()},
            {
                "web_search",
                "retrieve_memory",
                "read_memory_timeline",
                "list_reminders",
                "check_inventory",
                "inspect_media_info",
            },
        )

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
            self.assertEqual(
                set(names), {"web_search", "retrieve_memory", "read_memory_timeline"}
            )
            # Native-provided tools are excluded from the legacy prompt; the
            # write tool (compose_file) is not in the allowlist, so it stays legacy.
            self.assertEqual(
                plan.legacy_prompt_exclusions,
                {"web_search", "retrieve_memory", "read_memory_timeline"},
            )
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

    def test_native_tool_round_instruction_keeps_native_out_of_json_tool_call(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        instruction = engine._build_native_tool_round_instruction(
            [tool_orchestration_engine.native_web_search_tool_schema()]
        )

        self.assertIn("web_search", instruction)
        self.assertIn("provider tool_calls", instruction)
        self.assertIn("不要在 JSON 的 tool_call 字段里手写这些 native 工具", instruction)
        self.assertIn("legacy 工具", instruction)
        self.assertIn("tool_call 字段必须为 null", instruction)

    def test_verified_profile_completion_payload_sends_native_tools(self) -> None:
        runtime = LLMRuntime()
        bundle = ModelBundle(client=FakeClient("openai", base_url="https://api.deepseek.com/v1"), model="deepseek-v4-flash")
        schema = tool_orchestration_engine.native_web_search_tool_schema()

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
        self.assertNotIn("response_format", payload)
        self.assertEqual(runtime.snapshot_metrics()["native_tool_decision_sent"], 1)
        self.assertEqual(runtime.snapshot_metrics()["native_tool_forced_json_suppressed"], 1)

    def test_unsupported_provider_does_not_send_native_tools(self) -> None:
        runtime = LLMRuntime()
        bundle = ModelBundle(client=FakeClient("anthropic"), model="fake-model")
        schema = tool_orchestration_engine.native_web_search_tool_schema()

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
        schema = tool_orchestration_engine.native_web_search_tool_schema()
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
        self.assertEqual(result, {"speech": "ok", "tool_call": None})
        self.assertEqual(captured["native_tools"], [schema])
        self.assertEqual(captured["native_tool_choice"], "auto")

    def test_tool_working_stream_event_is_in_progress_only(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)

        event = engine._build_tool_working_stream_event({"type": "web_search"})

        self.assertEqual(event["type"], "assistant_working")
        self.assertEqual(event["status"], "running")
        self.assertEqual(event["phase"], "tool_call")
        self.assertEqual(event["tool_type"], "web_search")
        self.assertNotIn("done", str(event).lower())

    def test_unavailable_tool_event_stops_more_tool_rounds(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)

        self.assertTrue(
            engine._should_stop_after_tool_events(
                [{"type": "web_search_completed", "status": "unavailable", "reason": "timeout"}]
            )
        )
        self.assertFalse(
            engine._should_stop_after_tool_events(
                [{"type": "web_search_completed", "status": "ok"}]
            )
        )

    def test_tool_unavailable_stop_reason_tells_model_not_to_retry(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine._merge_extra_user_context = AkaneMemoryEngine._merge_extra_user_context.__get__(engine, AkaneMemoryEngine)

        context = engine._build_tool_round_extra_context(
            turn_extra_user_context="",
            tool_followups=["AnySearch 联网能力暂时不可用。"],
            allow_more=False,
            stop_reason="tool_unavailable",
        )

        self.assertIn("工具返回不可用或失败状态", context)
        self.assertIn("本轮不要再调用工具", context)


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
