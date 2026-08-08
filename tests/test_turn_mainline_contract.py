from __future__ import annotations

from types import SimpleNamespace
import unittest

from companion_v01.client_protocol import ClientMode
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.tool_invocation import NATIVE_TOOL_CALL_FIELD, NATIVE_TOOL_CALLS_FIELD
from companion_v01.tool_runtime import ToolExecutionResult


def _tool_call(kind: str, call_id: str) -> dict[str, object]:
    return {"type": kind, "id": call_id, "arguments": {}}


def _speech_output(speech: str) -> dict[str, object]:
    return {"speech": speech, "emotion": "neutral", "memory_metadata": {}}


def _tool_round_output(speech: str, kind: str, call_id: str, *, native_preface: str = "") -> dict[str, object]:
    output = _speech_output(speech)
    output[NATIVE_TOOL_CALLS_FIELD] = [_tool_call(kind, call_id)]
    if native_preface:
        output["_native_preface_text"] = native_preface
    return output


class _Recorder:
    def __init__(self, result: object = None) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.result = result

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls.append((args, kwargs))
        return self.result


class _FakeStore:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.eval_turns: list[dict[str, object]] = []
        self.counter = 0

    def add_message(self, **kwargs: object) -> dict[str, object]:
        self.counter += 1
        record = {
            "source_id": str(kwargs.get("source_id") or f"src-{self.counter}"),
            "role": kwargs.get("role"),
            "content": kwargs.get("content"),
            "timestamp": kwargs.get("timestamp"),
            "semantic_tags": kwargs.get("semantic_tags") or [],
            "memory_metadata": kwargs.get("memory_metadata"),
        }
        self.messages.append(record)
        return dict(record)

    def update_message_semantic_tags(self, _source_id: str, _tags: object) -> None:
        pass

    def update_message_memory_metadata(self, _source_id: str, _metadata: object) -> None:
        pass

    def append_eval_turn(self, **kwargs: object) -> None:
        self.eval_turns.append(kwargs)


class _ScriptedOutputs:
    def __init__(self, outputs: list[dict[str, object]]) -> None:
        self.outputs = list(outputs)
        self.generation_calls: list[dict[str, object]] = []

    def _next(self) -> dict[str, object]:
        if not self.outputs:
            raise AssertionError("turn mainline requested more generation rounds than scripted")
        output = dict(self.outputs.pop(0))
        self.generation_calls.append(output)
        return output

    def sync_gen(self, **kwargs: object) -> dict[str, object]:
        return self._next()

    def stream_gen(self, **kwargs: object):
        output = self._next()
        yield {"type": "turn_start", "speaker": "Akane"}
        return output

    def decisions(
        self,
        *,
        final_output: dict[str, object],
        user_message: str,
        client_context: object,
        profile_user_id: str,
        session_id: str,
        domain_profile_id: str = "",
    ) -> tuple[dict[str, object], list[dict[str, object]], list[str]]:
        del user_message, client_context, profile_user_id, session_id, domain_profile_id
        calls: list[dict[str, object]] = []
        raw_list = final_output.pop(NATIVE_TOOL_CALLS_FIELD, None)
        if isinstance(raw_list, list):
            for call in raw_list:
                if isinstance(call, dict) and call:
                    calls.append(dict(call))
        raw_single = final_output.pop(NATIVE_TOOL_CALL_FIELD, None)
        if not calls and isinstance(raw_single, dict) and raw_single:
            calls.append(dict(raw_single))
        final_output["tool_call"] = None
        return final_output, calls, []


class _Harness:
    def __init__(
        self,
        outputs: list[dict[str, object]],
        *,
        memcore_owns_compaction: bool = False,
        client_mode: ClientMode = ClientMode.SCENE_STATIC,
    ) -> None:
        self.engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine = self.engine
        self.store = _FakeStore()
        engine.store = self.store
        self.script = _ScriptedOutputs(outputs)
        self.rec = rec = {}

        def record(name: str, result: object = None) -> _Recorder:
            recorder = _Recorder(result)
            rec[name] = recorder
            return recorder

        engine._resolve_client_protocol_context = (
            lambda payload: SimpleNamespace(effective_mode=client_mode)
        )
        engine._resolve_payload_character_pack_id = lambda payload: "akane_v1"
        engine._resolve_turn_actor = lambda payload: ("", "")
        engine._resolve_turn_domain_profile = lambda payload: ""
        engine._resolve_turn_resource_manifest = lambda payload, client_context: None
        engine._prepare_care_context_for_turn = (
            lambda payload, client_context, **kwargs: payload
        )
        engine._build_turn_extra_user_context = lambda payload, client_context: ""
        engine._extract_native_user_images = lambda payload: []
        engine._extract_desktop_screen_frame_images = lambda payload: []
        engine._resolve_turn_execution_target = lambda **kwargs: None
        engine._memcore_owns_compaction = lambda: memcore_owns_compaction
        engine.consume_due_reminders = record("consume_due_reminders")
        engine._apply_message_addressing = lambda user_record, message_addressing: ("", "")
        engine._load_turn_visible_memory = lambda **kwargs: ([], [], [])
        engine._run_pre_retrieval_pipeline = lambda **kwargs: SimpleNamespace(
            router_output={},
            router_timing={},
            retrieval_result={"fused_hits": []},
            verifier_output={},
            confirmed_snippets=[],
            verifier_timing={},
        )
        engine._apply_user_vector_index_policy = (
            lambda *, user_record, **kwargs: user_record
        )
        engine._upsert_raw_record = record("upsert_raw_record")
        engine._begin_memcore_input_turn = record(
            "begin_memcore_input_turn", {"turn_id": "turn-generated-1"}
        )
        engine._stage_memcore_turn_metadata = record("stage_memcore_turn_metadata")
        engine._finalize_memcore_input_turn_for_delivery = record(
            "finalize_memcore_input_turn_for_delivery", False
        )
        engine._append_memcore_turn_intermediate = record(
            "append_memcore_turn_intermediate"
        )
        engine._abort_memcore_input_turn = record("abort_memcore_input_turn")
        engine._schedule_memcore_compaction = record("schedule_memcore_compaction")
        engine._schedule_summary_cycle = record("schedule_summary_cycle")
        engine._schedule_visual_observations_for_payload = record(
            "schedule_visual_observations_for_payload"
        )

        persona_recorder = record("apply_persona_state_to_final_output")

        def apply_persona(**kwargs: object) -> dict[str, object]:
            persona_recorder.calls.append(((), kwargs))
            return kwargs["final_output"]

        engine._apply_persona_state_to_final_output = apply_persona
        engine._apply_care_state_request = record("apply_care_state_request")
        engine._resolve_turn_speaker_identity = (
            lambda client_context, character_pack_id: {"assistant_name": "Akane"}
        )
        engine._resolve_tool_handlers = lambda **kwargs: {}
        engine._resolve_tool_round_budget = (
            lambda **kwargs: kwargs["current_budget"]
        )
        engine._recompute_turn_execution_target = (
            lambda **kwargs: kwargs["current_target"]
        )
        engine._build_tool_round_extra_context = (
            lambda **kwargs: "extra-context"
        )

        def execute_tool_call(
            *,
            profile_user_id: str,
            session_id: str,
            character_pack_id: str,
            tool_call: dict[str, object],
            visual_payload: object,
            now_ts: int,
            current_user_source_id: str,
            client_context: object,
            memory_exclude_source_ids: object,
            request_context: object,
            domain_profile_id: str = "",
        ) -> ToolExecutionResult:
            del (
                profile_user_id,
                session_id,
                character_pack_id,
                visual_payload,
                now_ts,
                current_user_source_id,
                client_context,
                memory_exclude_source_ids,
                request_context,
                domain_profile_id,
            )
            tool_type = str(tool_call.get("type") or "unknown")
            return ToolExecutionResult(
                tool_type=tool_type,
                stream_events=[
                    {"type": "tool_execution_start", "tool_type": tool_type}
                ],
                followup_context=f"真实结果：{tool_type}",
            )

        engine._execute_tool_call = execute_tool_call
        engine._record_memcore_tool_batch = record(
            "record_memcore_tool_batch", (["trace-action", "trace-result"], None)
        )
        engine._record_memcore_tool_media_input = record(
            "record_memcore_tool_media_input", []
        )
        engine._append_tool_history_batch = record(
            "append_tool_history_batch", {"ok": True, "status": "skipped"}
        )
        engine._record_tool_result_artifacts_in_task_workspace = (
            lambda **kwargs: ([], "")
        )
        engine._build_retrieval_debug_payload = lambda **kwargs: {}
        engine.native_chat_vision_status = (
            lambda **kwargs: {"enabled": False}
        )

        engine._build_final_response = self.script.sync_gen
        engine._stream_final_response = self.script.stream_gen
        engine._prepare_tool_round_decisions = self.script.decisions

    def payload(self, **extra: object) -> dict[str, object]:
        base: dict[str, object] = {
            "user_id": "s1",
            "real_user_id": "u1",
            "character_pack_id": "akane_v1",
            "message": "帮我查一下",
            "timestamp": 1_784_016_000,
        }
        base.update(extra)
        return base

    def run_sync(self, payload: dict[str, object]) -> dict[str, object]:
        return self.engine.process_turn(payload)

    def run_stream(self, payload: dict[str, object]) -> list[dict[str, object]]:
        return list(self.engine.process_turn_stream(payload))


class TurnMainlineContractTests(unittest.TestCase):
    def _two_round_script(self) -> list[dict[str, object]]:
        return [
            _tool_round_output("我查一下资料。", "load_material", "call-1"),
            _speech_output("查到了，是一份 PDF。"),
        ]

    def test_sync_and_stream_final_outputs_match(self) -> None:
        sync = _Harness(self._two_round_script())
        sync_result = sync.run_sync(sync.payload())

        stream = _Harness(self._two_round_script())
        events = stream.run_stream(stream.payload())
        finals = [event for event in events if event.get("type") == "final"]
        self.assertEqual(len(finals), 1)
        stream_final = finals[0]["payload"]
        self.assertEqual(stream_final.get("speech"), sync_result.get("speech"))
        self.assertEqual(stream_final.get("emotion"), sync_result.get("emotion"))
        self.assertEqual(stream_final.get("memory_metadata"), sync_result.get("memory_metadata"))
        self.assertEqual(stream_final.get("tool_events"), sync_result.get("tool_events"))
        self.assertEqual(stream_final.get("npc_turns"), sync_result.get("npc_turns"))
        self.assertEqual(stream_final.get("dialogue_turns"), sync_result.get("dialogue_turns"))
        self.assertIn("trace_id", stream_final)
        self.assertIn("trace_id", sync_result)
        self.assertIn("_debug", stream_final)
        self.assertIn("_debug", sync_result)

    def test_two_tool_rounds_drive_batches_in_order_and_accumulate(self) -> None:
        harness = _Harness(
            [
                _tool_round_output("先查原图。", "load_material", "call-1"),
                _tool_round_output("再搜一下。", "web_search", "call-2"),
                _speech_output("都拿到了。"),
            ]
        )
        result = harness.run_sync(harness.payload())

        batch_calls = harness.rec["record_memcore_tool_batch"].calls
        self.assertEqual(len(batch_calls), 2)
        first_items = batch_calls[0][1]["items"]
        second_items = batch_calls[1][1]["items"]
        self.assertEqual(first_items[0][0]["type"], "load_material")
        self.assertEqual(second_items[0][0]["type"], "web_search")
        self.assertEqual(result.get("speech"), "都拿到了。")
        self.assertEqual(len(result.get("npc_turns") or []), 0)
        roles = [str(message.get("role")) for message in harness.store.messages]
        self.assertEqual(roles, ["user", "assistant", "assistant", "assistant"])
        self.assertEqual(len(harness.store.eval_turns), 1)

    def test_stream_event_order_is_locked(self) -> None:
        harness = _Harness(
            [
                _tool_round_output(
                    "我查一下资料。",
                    "load_material",
                    "call-1",
                    native_preface="我查一下资料。",
                ),
                _speech_output("查到了。"),
            ]
        )
        events = harness.run_stream(harness.payload())
        kinds = [str(event.get("type")) for event in events]
        self.assertEqual(
            kinds,
            [
                "turn_start",
                "speech_segment",
                "assistant_stage_decision",
                "assistant_working",
                "tool_execution_start",
                "turn_start",
                "assistant_stage_decision",
                "final_ui",
                "final",
            ],
        )
        stage = events[2]
        self.assertTrue(stage.get("has_tool_call"))
        self.assertEqual(stage.get("tool_type"), "load_material")
        self.assertEqual(stage.get("tool_count"), 1)
        self.assertFalse(stage.get("rejected_tool_call"))
        final_stage = events[6]
        self.assertFalse(final_stage.get("has_tool_call"))
        self.assertEqual(events[0]["speaker"], "Akane")
        self.assertEqual(events[1]["text"], "我查一下资料。")
        self.assertEqual(events[3]["phase"], "tool_call")

    def test_final_ui_and_final_payload_contract(self) -> None:
        harness = _Harness(self._two_round_script())
        events = harness.run_stream(harness.payload())
        by_type = {str(event.get("type")): event for event in events}
        final_ui = by_type["final_ui"]
        final = by_type["final"]
        self.assertNotIn("_debug", final_ui["payload"])
        self.assertNotIn("trace_id", final_ui["payload"])
        self.assertIn("_debug", final["payload"])
        self.assertIn("trace_id", final["payload"])
        self.assertEqual(final_ui["payload"].get("speech"), final["payload"].get("speech"))

    def test_speculative_voice_skips_persona_care_reminders_summary(self) -> None:
        harness = _Harness([_speech_output("我还不确定你在说什么。")])
        payload = harness.payload(
            transient_user_message=True,
            transient_assistant_message=True,
            voice_speculative_candidate=True,
        )
        harness.run_stream(payload)

        rec = harness.rec
        self.assertEqual(len(rec["consume_due_reminders"].calls), 0)
        self.assertEqual(len(rec["apply_persona_state_to_final_output"].calls), 0)
        self.assertEqual(len(rec["apply_care_state_request"].calls), 0)
        self.assertEqual(len(rec["schedule_summary_cycle"].calls), 0)
        self.assertEqual(harness.store.messages, [])
        self.assertEqual(harness.store.eval_turns, [])

    def test_non_speculative_turn_does_persist_persona_care_and_summary(self) -> None:
        harness = _Harness([_speech_output("好的，明白了。")])
        harness.run_sync(harness.payload())

        rec = harness.rec
        self.assertGreaterEqual(len(rec["consume_due_reminders"].calls), 1)
        self.assertEqual(len(rec["apply_persona_state_to_final_output"].calls), 1)
        self.assertGreaterEqual(len(rec["apply_care_state_request"].calls), 1)
        self.assertGreaterEqual(len(rec["schedule_summary_cycle"].calls), 1)
        self.assertEqual(len(harness.store.eval_turns), 1)

    def test_precommitted_memcore_turn_reuses_original_id(self) -> None:
        harness = _Harness(self._two_round_script())
        precommitted = {
            "source_id": "src-voice",
            "turn_id": "turn-9",
            "voice_turn_id": "v-9",
        }
        events = list(
            harness.engine.process_turn_stream(
                harness.payload(
                    transient_user_message=True,
                    transient_assistant_message=True,
                ),
                _precommitted_memcore_turn=precommitted,
            )
        )
        self.assertTrue(any(event.get("type") == "final" for event in events))
        rec = harness.rec
        self.assertEqual(len(rec["begin_memcore_input_turn"].calls), 0)
        self.assertEqual(len(rec["stage_memcore_turn_metadata"].calls), 0)
        self.assertEqual(len(rec["finalize_memcore_input_turn_for_delivery"].calls), 0)
        self.assertEqual(len(rec["abort_memcore_input_turn"].calls), 0)
        batch_call = rec["record_memcore_tool_batch"].calls[0]
        self.assertEqual(batch_call[1]["memcore_turn_id"], "turn-9")
        preface_call = rec["append_memcore_turn_intermediate"].calls[0]
        self.assertEqual(preface_call[1]["turn_id"], "turn-9")


class StreamFallbackContractTests(unittest.TestCase):
    def test_stream_final_response_transport_fallback_still_reaches_user(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)

        class FakeLLM:
            supports_request_observer = False

            def snapshot_metrics(self) -> dict[str, object]:
                return {}

            def record_metric(self, _name: str) -> None:
                pass

            def stream_chat_json(self, **kwargs: object):
                del kwargs
                yield from ()
                return SimpleNamespace(
                    parsed=None,
                    raw_text="",
                    error="transport_error",
                    latest_emotion="",
                    latest_speech="",
                )

            def call_chat_json_result(self, **kwargs: object):
                del kwargs
                return SimpleNamespace(
                    parsed={"speech": "降级后的答复", "emotion": "neutral", "memory_metadata": {}},
                    raw_text="raw-fallback",
                    error="",
                    fallback_used=False,
                )

        engine.llm = FakeLLM()
        engine._resolve_turn_speaker_identity = (
            lambda client_context, character_pack_id: {"assistant_name": "Akane"}
        )
        engine._prepare_final_response_context = lambda **kwargs: {
            "system_prompt": "stable persona [assistant_state_marker]",
            "user_prompt": "hi",
            "fallback": {"persona": {"active": "akane"}},
            "visual_defaults": {"emotion": "neutral"},
            "allow_tool_call": True,
            "debug_enabled": True,
            "prompt_scope": "",
        }

        def normalize(**kwargs: object) -> dict[str, object]:
            raw_result = kwargs.get("result")
            if raw_result is None:
                return {"speech": "", "emotion": "neutral", "memory_metadata": {}}
            return {
                "speech": str(raw_result.get("speech") or "ok"),
                "emotion": "neutral",
                "memory_metadata": dict(raw_result.get("memory_metadata") or {}),
            }

        engine._normalize_final_output = normalize

        events: list[dict[str, object]] = []
        result: dict[str, object] = {}

        def drive() -> None:
            nonlocal result
            generator = engine._stream_final_response(
                session_id="s",
                profile_user_id="u",
                user_message="hi",
                recent_raw=[],
                recent_episodic_summaries=[],
                recent_semantic_summaries=[],
                confirmed_snippets=[],
                now_ts=1,
                client_context=None,
                resource_manifest=None,
                character_pack_id="akane_v1",
                user_images=[],
                allow_tool_call=True,
                final_debug_enabled=None,
                chat_model_override="",
                execution_target=None,
                post_user_turns=None,
                prompt_exclude_source_ids=None,
                domain_profile_id="",
                prompt_scope="",
            )
            while True:
                try:
                    events.append(next(generator))
                except StopIteration as stop:
                    result = stop.value
                    return

        drive()
        self.assertEqual([str(event.get("type")) for event in events], ["turn_start"])
        self.assertEqual(result.get("speech"), "降级后的答复")
        self.assertEqual(result.get("_provider_output_raw"), "raw-fallback")


class SyncDrainContractTests(unittest.TestCase):
    def _build_sync_engine(self):
        harness = _Harness([_speech_output("好的。")])
        harness.engine._build_final_response = (
            lambda **kwargs: _speech_output("好的。")
        )
        return harness

    def test_sync_drain_rejects_unexpected_stream_event(self) -> None:
        harness = self._build_sync_engine()

        def leaking_generate_round(**kwargs: object):
            del kwargs
            yield {"type": "leaked_event"}
            return _speech_output("好的。")

        harness.engine._generate_round = leaking_generate_round
        with self.assertRaisesRegex(RuntimeError, "sync_path_emitted"):
            harness.engine.process_turn(harness.payload())


if __name__ == "__main__":
    unittest.main()
