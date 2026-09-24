from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import config
from companion_v01.client_protocol import ClientMode
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.tool_invocation import NATIVE_TOOL_CALL_FIELD, NATIVE_TOOL_CALLS_FIELD
from companion_v01.tool_runtime import ToolExecutionResult, ToolFollowupEnvelope
from companion_v01.turn_coordination import SteeringInput


def _tool_call(kind: str, call_id: str) -> dict[str, object]:
    return {"type": kind, "id": call_id, "arguments": {}}


def _speech_output(speech: str) -> dict[str, object]:
    return {"speech": speech, "emotion": "neutral", "memory_metadata": {}}


def _continue_output(speech: str) -> dict[str, object]:
    return {**_speech_output(speech), "status": "continue"}


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
        self.generation_kwargs: list[dict[str, object]] = []

    def _next(self) -> dict[str, object]:
        if not self.outputs:
            raise AssertionError("turn mainline requested more generation rounds than scripted")
        output = dict(self.outputs.pop(0))
        self.generation_calls.append(output)
        return output

    def sync_gen(self, **kwargs: object) -> dict[str, object]:
        self.generation_kwargs.append(dict(kwargs))
        return self._next()

    def stream_gen(self, **kwargs: object):
        self.generation_kwargs.append(dict(kwargs))
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
        engine._begin_memcore_existing_input_turn = record(
            "begin_memcore_existing_input_turn",
            {"ok": True, "status": "opened", "turn_id": "turn-attention-1", "writable": True},
        )
        engine._begin_memcore_hidden_host_turn = record(
            "begin_memcore_hidden_host_turn",
            {
                "ok": True,
                "status": "opened",
                "source_id": "host-attention:1",
                "turn_id": "turn-attention-1",
                "writable": True,
            },
        )
        engine._stage_memcore_turn_metadata = record("stage_memcore_turn_metadata")
        engine._finalize_memcore_input_turn_for_delivery = record(
            "finalize_memcore_input_turn_for_delivery", False
        )
        engine._append_memcore_turn_intermediate = record(
            "append_memcore_turn_intermediate"
        )
        engine._append_memcore_turn_user_input = record(
            "append_memcore_turn_user_input", {"ok": True, "status": "recorded"}
        )
        engine._abort_memcore_input_turn = record("abort_memcore_input_turn")
        engine._schedule_memcore_compaction = record("schedule_memcore_compaction")
        engine._append_memcore_standalone_assistant = record("append_memcore_standalone_assistant")
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
        engine._recompute_turn_execution_target = (
            lambda **kwargs: kwargs["current_target"]
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

    def test_qq_actor_relation_is_frozen_into_each_current_request(self) -> None:
        cases = (
            ("qq:1906243651", "owner"),
            ("qq:2660153472", "participant"),
        )
        for actor_stable_id, expected_relation in cases:
            with self.subTest(actor_stable_id=actor_stable_id):
                harness = _Harness([_speech_output("收到。")], client_mode=ClientMode.QQ_TEXT)
                harness.engine._resolve_turn_actor = lambda _payload, actor=actor_stable_id: (actor, "群成员")
                with patch.object(config, "MASTER_QQ", "1906243651"):
                    harness.run_sync(harness.payload(message="戳一戳"))

                projection_state = harness.script.generation_kwargs[0]["request_projection_state"]
                self.assertEqual(projection_state["current_actor_relation"], expected_relation)

    def test_user_steer_is_persisted_and_regenerates_before_stale_final_delivery(self) -> None:
        harness = _Harness([
            _speech_output("旧方向已经做完。"),
            _speech_output("收到调整，我先补测试。"),
        ])

        receipt_store = SimpleNamespace(
            commit=_Recorder({"ok": True, "status": "committed"}),
            fail=_Recorder({"ok": True, "status": "failed"}),
        )
        harness.engine.session_inbox_store = receipt_store

        class Coordinator:
            drained = False

            def drain(self, _token: str) -> dict[str, object]:
                if self.drained:
                    return {"ok": True, "stop_requested": False, "steers": []}
                self.drained = True
                return {
                    "ok": True,
                    "stop_requested": False,
                    "steers": [
                        SteeringInput(
                            source_id="steer-1",
                            content="改一下，先把测试补齐",
                            timestamp=1_784_016_010,
                            actor_id="qq:1",
                            actor_display_name="伙伴",
                            channel="qq",
                            receipt_item_id="inbox-steer-1",
                            receipt_claim_token="claim-steer-1",
                        )
                    ],
                }

            def begin_finalization(self, _token: str) -> dict[str, object]:
                return {"ok": True, "status": "finalizing", "stop_requested": False, "steers": []}

        harness.engine.turn_coordinator = Coordinator()
        events = harness.run_stream(harness.payload(_turn_control_id="control-1"))

        self.assertTrue(any(event.get("type") == "turn_steer_applied" for event in events))
        final = next(event["payload"] for event in events if event.get("type") == "final")
        self.assertEqual(final["speech"], "收到调整，我先补测试。")
        append_call = harness.rec["append_memcore_turn_user_input"].calls[0][1]
        self.assertEqual(append_call["user_record"]["source_id"], "steer-1")
        self.assertEqual(append_call["actor_stable_id"], "qq:1")
        self.assertEqual(
            receipt_store.commit.calls,
            [(('inbox-steer-1',), {"claim_token": "claim-steer-1"})],
        )
        self.assertEqual(receipt_store.fail.calls, [])
        self.assertEqual([item.get("role") for item in harness.store.messages[:2]], ["user", "user"])
        self.assertEqual(len(harness.script.generation_kwargs), 2)
        self.assertTrue(harness.script.generation_kwargs[0]["allow_tool_call"])
        self.assertTrue(harness.script.generation_kwargs[1]["allow_tool_call"])
        self.assertEqual(
            harness.script.generation_kwargs[1]["post_user_turns"] or [],
            harness.script.generation_kwargs[0]["post_user_turns"] or [],
        )

    def test_failed_steer_memcore_append_marks_durable_receipt_failed(self) -> None:
        harness = _Harness([_speech_output("原回复继续。")])
        harness.engine._append_memcore_turn_user_input = _Recorder(
            {"ok": False, "status": "failed", "reason": "memcore_write_failed"}
        )
        receipt_store = SimpleNamespace(
            commit=_Recorder({"ok": True, "status": "committed"}),
            fail=_Recorder({"ok": True, "status": "failed"}),
        )
        harness.engine.session_inbox_store = receipt_store

        class Coordinator:
            drained = False

            def drain(self, _token: str) -> dict[str, object]:
                if self.drained:
                    return {"ok": True, "stop_requested": False, "steers": []}
                self.drained = True
                return {
                    "ok": True,
                    "stop_requested": False,
                    "steers": [
                        SteeringInput(
                            source_id="steer-failed-1",
                            content="这条落 MemCore 时失败",
                            timestamp=1_784_016_012,
                            actor_id="qq:1",
                            receipt_item_id="inbox-failed-1",
                            receipt_claim_token="claim-failed-1",
                        )
                    ],
                }

            def begin_finalization(self, _token: str) -> dict[str, object]:
                return {"ok": True, "status": "finalizing", "stop_requested": False, "steers": []}

        harness.engine.turn_coordinator = Coordinator()
        events = harness.run_stream(harness.payload(_turn_control_id="control-failed-steer"))

        self.assertTrue(any(event.get("type") == "turn_steer_failed" for event in events))
        self.assertEqual(receipt_store.commit.calls, [])
        self.assertEqual(
            receipt_store.fail.calls,
            [
                (
                    ('inbox-failed-1',),
                    {
                        "claim_token": "claim-failed-1",
                        "error": "memcore_turn_user_input_append_failed",
                        "retryable": False,
                    },
                )
            ],
        )

    def test_image_steer_upgrades_next_existing_round_without_load_material_or_extra_round(self) -> None:
        harness = _Harness([
            _speech_output("我先按文字处理。"),
            _speech_output("我已经直接看到了你追加的原图。"),
        ])
        resolved_targets: list[dict[str, object]] = []

        def resolve_target(**kwargs: object) -> object:
            resolved_targets.append(dict(kwargs))
            role = "vision" if kwargs.get("has_real_images") or kwargs.get("tool_image_upgrade") else "chat"
            return SimpleNamespace(role=role, protocol="openai_chat")

        harness.engine._resolve_turn_execution_target = resolve_target

        class Coordinator:
            drained = False

            def drain(self, _token: str) -> dict[str, object]:
                if self.drained:
                    return {"ok": True, "stop_requested": False, "steers": []}
                self.drained = True
                return {
                    "ok": True,
                    "stop_requested": False,
                    "steers": [
                        SteeringInput(
                            source_id="steer-image-1",
                            content="再看这张图，按图里的内容判断",
                            timestamp=1_784_016_011,
                            actor_id="qq:1",
                            actor_display_name="伙伴",
                            channel="qq",
                            native_user_images=(
                                {
                                    "attachment_id": "attachment-image-1",
                                    "attachment_handle": "img_001",
                                    "data_url": "data:image/png;base64,cGl4ZWxz",
                                },
                            ),
                        )
                    ],
                }

            def begin_finalization(self, _token: str) -> dict[str, object]:
                return {"ok": True, "status": "finalizing", "stop_requested": False, "steers": []}

        harness.engine.turn_coordinator = Coordinator()
        events = harness.run_stream(harness.payload(_turn_control_id="control-image-steer"))

        final = next(event["payload"] for event in events if event.get("type") == "final")
        self.assertEqual(final["speech"], "我已经直接看到了你追加的原图。")
        self.assertEqual(len(harness.script.generation_kwargs), 2)
        self.assertEqual(harness.script.generation_kwargs[0]["execution_target"].role, "chat")
        self.assertEqual(harness.script.generation_kwargs[1]["execution_target"].role, "vision")
        self.assertEqual(
            harness.script.generation_kwargs[1]["user_images"][0]["attachment_handle"],
            "img_001",
        )
        self.assertIn("provider 原生多模态通道", harness.script.generation_kwargs[1]["extra_user_context"])
        self.assertEqual([item.get("type") for item in harness.script.generation_calls], [None, None])
        self.assertEqual(
            resolved_targets,
            [
                {"has_real_images": False, "chat_model_override": ""},
                {
                    "has_real_images": True,
                    "tool_image_upgrade": False,
                    "chat_model_override": "",
                },
            ],
        )

    def test_stop_request_aborts_open_turn_without_delivering_stale_final(self) -> None:
        harness = _Harness([_speech_output("这条不应该交付。")])

        class Coordinator:
            def drain(self, _token: str) -> dict[str, object]:
                return {"ok": True, "stop_requested": True, "steers": []}

        harness.engine.turn_coordinator = Coordinator()
        events = harness.run_stream(harness.payload(_turn_control_id="control-stop"))

        self.assertTrue(any(event.get("type") == "turn_stopped" for event in events))
        self.assertFalse(any(event.get("type") == "final" for event in events))
        abort_call = harness.rec["abort_memcore_input_turn"].calls[0][1]
        self.assertEqual(abort_call["reason"], "user_stopped")
        self.assertEqual(len(harness.store.eval_turns), 0)

    def test_preemption_reason_survives_engine_stop_frame_and_memory_settlement(self) -> None:
        harness = _Harness([_speech_output("stale output")])
        reason = "addressed_input_preempts_optional_turn"
        harness.engine.turn_coordinator = SimpleNamespace(drain=lambda token: {
            "ok": True, "stop_requested": True, "stop_reason": reason, "steers": []})
        events = harness.run_stream(harness.payload(_turn_control_id="control-preempt"))
        stopped = next(event for event in events if event.get("type") == "turn_stopped")
        self.assertEqual(stopped["reason"], reason)
        self.assertEqual(stopped["payload"]["reason"], reason)
        self.assertEqual(harness.rec["abort_memcore_input_turn"].calls[0][1]["reason"], reason)
        self.assertFalse(any(event.get("type") == "final" for event in events))

    def test_stop_requests_cancellation_for_a_confirmed_running_exec(self) -> None:
        harness = _Harness([
            _tool_round_output("开始跑。", "exec_run", "call-run"),
            _speech_output("还在执行。"),
        ])
        executed_types: list[str] = []

        def execute_tool_call(*, tool_call: dict[str, object], **_kwargs: object) -> ToolExecutionResult:
            tool_type = str(tool_call.get("type") or "")
            executed_types.append(tool_type)
            if tool_type == "exec_run":
                return ToolExecutionResult(
                    tool_type="exec_run",
                    followup_context="命令仍在执行",
                    state_updates={
                        "capability_execution": {
                            "tool_type": "exec_run",
                            "status": "running",
                            "run_id": "run_12345678",
                        }
                    },
                )
            return ToolExecutionResult(
                tool_type="exec_cancel",
                followup_context="命令已停止",
                state_updates={
                    "capability_execution": {
                        "tool_type": "exec_cancel",
                        "status": "cancelled",
                        "run_id": "run_12345678",
                    }
                },
            )

        class Coordinator:
            polls = 0

            def drain(self, _token: str) -> dict[str, object]:
                self.polls += 1
                return {
                    "ok": True,
                    "stop_requested": self.polls >= 2,
                    "steers": [],
                }

        harness.engine._execute_tool_call = execute_tool_call
        harness.engine.turn_coordinator = Coordinator()
        events = harness.run_stream(harness.payload(_turn_control_id="control-stop-run"))

        self.assertEqual(executed_types, ["exec_run", "exec_cancel"])
        stopped = next(event for event in events if event.get("type") == "turn_stopped")
        self.assertEqual(
            stopped["payload"]["execution_cancellations"][0]["capability_execution"]["status"],
            "cancelled",
        )

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

    def test_load_material_pixels_append_as_media_evidence_for_next_decision(self) -> None:
        harness = _Harness(
            [
                _tool_round_output("我加载原图。", "load_material", "call-image"),
                _speech_output("看到了。"),
            ]
        )
        image_input = {
            "attachment_id": "attachment::image-1",
            "attachment_handle": "img_001",
            "data_url": "data:image/png;base64,AAAA",
        }

        def execute_tool_call(**kwargs: object) -> ToolExecutionResult:
            tool_call = dict(kwargs["tool_call"])
            return ToolExecutionResult(
                tool_type=str(tool_call.get("type") or "load_material"),
                followup_context="原始图片已加载。",
                model_image_inputs=[image_input],
            )

        def append_tool_history_batch(**kwargs: object) -> dict[str, object]:
            self.assertEqual(kwargs["model_image_inputs"], [image_input])
            history = kwargs["tool_history_turns"]
            history[:] = [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-image",
                            "type": "function",
                            "function": {"name": "load_material", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call-image", "content": "原始图片已加载。"},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "工具为当前请求加载了图片：img_001。",
                        },
                        {"type": "image_url", "image_url": {"url": image_input["data_url"]}},
                    ],
                },
            ]
            return {"ok": True, "status": "projected"}

        harness.engine._execute_tool_call = execute_tool_call
        harness.engine.native_chat_vision_status = lambda **_kwargs: {"enabled": True}
        harness.engine._append_tool_history_batch = append_tool_history_batch
        result = harness.run_sync(harness.payload(message="再看一下刚才的图"))

        self.assertEqual(result.get("speech"), "看到了。")
        self.assertEqual(harness.script.generation_kwargs[0]["user_images"], [])
        self.assertEqual(harness.script.generation_kwargs[1]["user_images"], [])
        next_history = harness.script.generation_kwargs[1]["post_user_turns"]
        self.assertIn("img_001", next_history[-1]["content"][0]["text"])
        self.assertEqual(next_history[-1]["content"][-1]["image_url"]["url"], image_input["data_url"])
        self.assertEqual(len(harness.rec["record_memcore_tool_batch"].calls), 1)
        media_calls = harness.rec["record_memcore_tool_media_input"].calls
        self.assertEqual(len(media_calls), 1)
        self.assertEqual(media_calls[0][1]["model_image_inputs"], [image_input])

    def test_warning_is_injected_once_while_tools_remain_available(self) -> None:
        harness = _Harness(
            [
                _tool_round_output("第一步。", "inspect_one", "call-1"),
                _tool_round_output("第二步。", "inspect_two", "call-2"),
                _tool_round_output("第三步。", "inspect_three", "call-3"),
                _speech_output("完成。"),
            ]
        )
        harness.engine._max_tool_rounds = lambda **_kwargs: 4
        harness.engine._tool_round_warning_remaining = lambda **_kwargs: 2

        result = harness.run_sync(harness.payload(message="完成长程任务"))

        warning_calls = [
            kwargs
            for kwargs in harness.script.generation_kwargs
            if "工具预算提醒" in str(kwargs.get("extra_user_context") or "")
        ]
        self.assertEqual(len(warning_calls), 1)
        self.assertTrue(warning_calls[0]["allow_tool_call"])
        self.assertIn("已使用 2/4", warning_calls[0]["extra_user_context"])
        self.assertEqual(result.get("speech"), "完成。")

    def test_unlimited_tool_rounds_continue_beyond_legacy_limit_without_warning(self) -> None:
        tool_rounds = 49
        harness = _Harness(
            [
                _tool_round_output(f"执行第 {index} 步。", "inspect", f"call-{index}")
                for index in range(1, tool_rounds + 1)
            ]
            + [_speech_output("四十九轮完成并正常交付。")]
        )
        harness.engine._max_tool_rounds = lambda **_kwargs: 0
        harness.engine._tool_round_warning_remaining = lambda **_kwargs: 0

        result = harness.run_sync(harness.payload(message="完成超过旧上限的长程任务"))

        self.assertEqual(len(harness.rec["record_memcore_tool_batch"].calls), tool_rounds)
        self.assertEqual(result.get("speech"), "四十九轮完成并正常交付。")
        self.assertTrue(all(kwargs.get("allow_tool_call") for kwargs in harness.script.generation_kwargs))
        self.assertFalse(
            any(
                "工具预算提醒" in str(kwargs.get("extra_user_context") or "")
                for kwargs in harness.script.generation_kwargs
            )
        )

    def test_hard_limit_executes_last_batch_then_blocks_next_tool(self) -> None:
        harness = _Harness(
            [
                _tool_round_output("第一步。", "inspect_one", "call-1"),
                _tool_round_output("第二步。", "inspect_two", "call-2"),
                _tool_round_output("第三步。", "inspect_three", "call-3"),
                _tool_round_output("还想执行第四步。", "inspect_four", "call-4"),
                _speech_output("三轮已执行，第四轮没有执行；任务尚未完成。"),
            ]
        )
        harness.engine._max_tool_rounds = lambda **_kwargs: 3
        harness.engine._tool_round_warning_remaining = lambda **_kwargs: 1

        result = harness.run_sync(harness.payload(message="完成长程任务"))

        batches = harness.rec["record_memcore_tool_batch"].calls
        self.assertEqual(len(batches), 3)
        executed = [call[1]["items"][0][0]["type"] for call in batches]
        self.assertEqual(executed, ["inspect_one", "inspect_two", "inspect_three"])
        self.assertEqual(result.get("speech"), "三轮已执行，第四轮没有执行；任务尚未完成。")
        delivery_only = [
            kwargs for kwargs in harness.script.generation_kwargs if not kwargs.get("allow_tool_call")
        ]
        self.assertEqual(len(delivery_only), 2)
        self.assertIn("最后一批工具已经真实执行", delivery_only[0]["extra_user_context"])

    def test_failed_tool_result_does_not_close_other_tools(self) -> None:
        harness = _Harness(
            [
                _tool_round_output("先试第一种。", "first_tool", "call-1"),
                _tool_round_output("换第二种。", "second_tool", "call-2"),
                _speech_output("第二种成功。"),
            ]
        )
        executed: list[str] = []

        def execute_tool_call(**kwargs: object) -> ToolExecutionResult:
            tool_type = str(dict(kwargs["tool_call"]).get("type") or "")
            executed.append(tool_type)
            failed = tool_type == "first_tool"
            return ToolExecutionResult(
                tool_type=tool_type,
                stream_events=[
                    {
                        "type": "tool_execution_result",
                        "tool_type": tool_type,
                        "status": "failed" if failed else "succeeded",
                    }
                ],
                followup_context="失败，换其它办法。" if failed else "成功。",
            )

        harness.engine._execute_tool_call = execute_tool_call
        result = harness.run_sync(harness.payload())

        self.assertEqual(executed, ["first_tool", "second_tool"])
        self.assertEqual(result.get("speech"), "第二种成功。")

    def test_toolless_continue_status_is_not_a_hidden_host_action(self) -> None:
        harness = _Harness(
            [
                _continue_output("基础代码写完了，但验收还没跑完。"),
            ]
        )

        result = harness.run_sync(harness.payload(message="完成这个编程任务"))

        self.assertEqual(result.get("speech"), "基础代码写完了，但验收还没跑完。")
        self.assertEqual(len(harness.rec["record_memcore_tool_batch"].calls), 0)
        self.assertEqual(len(harness.script.generation_calls), 1)
        self.assertEqual(len(harness.store.eval_turns), 1)

    def test_producer_continuation_may_repeat_the_same_observation_call(self) -> None:
        repeated_call = _tool_call("poll_status", "same-call")
        harness = _Harness(
            [
                _tool_round_output("我等一下。", "poll_status", "same-call"),
                _tool_round_output("还在运行，我继续等。", "poll_status", "same-call"),
                _speech_output("任务完成了。"),
            ]
        )
        executions: list[dict[str, object]] = []

        def execute_tool_call(**kwargs: object) -> ToolExecutionResult:
            tool_call = dict(kwargs["tool_call"])
            executions.append(tool_call)
            first = len(executions) == 1
            return ToolExecutionResult(
                tool_type="poll_status",
                followup_context="仍在运行" if first else "已经完成",
                followup_envelope=ToolFollowupEnvelope(
                    content="仍在运行" if first else "已经完成",
                    producer_bounded=True,
                    complete=not first,
                    continuation=dict(repeated_call) if first else None,
                ),
            )

        harness.engine._execute_tool_call = execute_tool_call
        result = harness.run_sync(harness.payload())
        self.assertEqual(len(executions), 2)
        self.assertEqual(result.get("speech"), "任务完成了。")

    def test_exact_repeated_tool_call_executes_without_hidden_terminal_phase(self) -> None:
        repeated_call = _tool_call("load_mcp", "same-load")
        harness = _Harness(
            [
                _tool_round_output("我先加载。", "load_mcp", "same-load"),
                _tool_round_output("服务重启了，我重新加载。", "load_mcp", "same-load"),
                _tool_round_output("再确认一次。", "load_mcp", "same-load"),
                _tool_round_output("最后确认。", "load_mcp", "same-load"),
                _speech_output("重新加载后验证完成。"),
            ]
        )
        harness.engine._max_tool_rounds = lambda **_kwargs: 8
        executions: list[dict[str, object]] = []

        def execute_tool_call(**kwargs: object) -> ToolExecutionResult:
            tool_call = dict(kwargs["tool_call"])
            executions.append(tool_call)
            return ToolExecutionResult(
                tool_type="load_mcp",
                followup_context=f"第 {len(executions)} 次加载完成",
            )

        harness.engine._execute_tool_call = execute_tool_call
        result = harness.run_sync(harness.payload())

        self.assertEqual(executions, [repeated_call] * 4)
        self.assertEqual(result.get("speech"), "重新加载后验证完成。")
        self.assertTrue(all(kwargs.get("allow_tool_call") for kwargs in harness.script.generation_kwargs[:4]))
        self.assertFalse(
            any(
                "tool_decision_invalid" in str(kwargs.get("extra_user_context") or "")
                for kwargs in harness.script.generation_kwargs
            )
        )

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

    def test_parser_streamed_native_preface_is_not_emitted_twice(self) -> None:
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
        original_stream = harness.engine._stream_final_response

        def stream_with_parser_preface(**kwargs: object):
            generator = original_stream(**kwargs)
            try:
                first = next(generator)
            except StopIteration as stop:
                return stop.value
            yield first
            while True:
                try:
                    event = next(generator)
                except StopIteration as stop:
                    output = dict(stop.value)
                    if output.get("_native_preface_text"):
                        yield {"type": "speech_segment", "index": 0, "text": output["_native_preface_text"]}
                        output["_native_preface_streamed"] = True
                    return output
                yield event

        harness.engine._stream_final_response = stream_with_parser_preface
        events = harness.run_stream(harness.payload())

        self.assertEqual(
            [event.get("text") for event in events if event.get("type") == "speech_segment"],
            ["我查一下资料。"],
        )

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
        self.assertEqual(len(rec["apply_persona_state_to_final_output"].calls), 0)
        self.assertEqual(len(rec["apply_care_state_request"].calls), 0)
        self.assertEqual(len(rec["schedule_summary_cycle"].calls), 0)
        self.assertEqual(harness.store.messages, [])
        self.assertEqual(harness.store.eval_turns, [])

    def test_attention_uses_hidden_host_turn_without_relinking_passive_sources(self) -> None:
        harness = _Harness(self._two_round_script())
        result = harness.run_sync(
            harness.payload(
                turn_kind="qq_attention",
                transient_user_message=True,
                memory_attention_reference_source_ids=["observed-source-1"],
            )
        )

        self.assertEqual(result["speech"], "查到了，是一份 PDF。")
        open_call = harness.rec["begin_memcore_hidden_host_turn"].calls[0][1]
        self.assertEqual(open_call["referenced_source_ids"], ["observed-source-1"])
        self.assertEqual(len(harness.rec["begin_memcore_existing_input_turn"].calls), 0)
        self.assertEqual(len(harness.rec["begin_memcore_input_turn"].calls), 0)
        batch_call = harness.rec["record_memcore_tool_batch"].calls[0][1]
        self.assertEqual(batch_call["memcore_turn_id"], "turn-attention-1")
        stage_call = harness.rec["stage_memcore_turn_metadata"].calls[0][1]
        self.assertEqual(stage_call["source_id"], "host-attention:1")
        finalize_call = harness.rec["finalize_memcore_input_turn_for_delivery"].calls[0][1]
        self.assertEqual(finalize_call["turn_id"], "turn-attention-1")
        self.assertEqual(len(harness.rec["append_memcore_standalone_assistant"].calls), 0)
        projection_state = harness.script.generation_kwargs[0]["request_projection_state"]
        self.assertFalse(projection_state["record_request_projection"])
        self.assertEqual(projection_state["current_user_source_id"], "")

    def test_current_turn_event_uses_normal_prompt_and_hidden_tool_owner(self) -> None:
        harness = _Harness(self._two_round_script())
        result = harness.run_sync(harness.payload(turn_kind="plugin_event", transient_user_message=True,
            plugin_external_event={"event_type": "job.completed", "source": "host.jobs", "fields": {"status": "succeeded"}}))
        self.assertEqual(result["speech"], "查到了，是一份 PDF。")
        self.assertEqual(len(harness.rec["begin_memcore_hidden_host_turn"].calls), 1)
        self.assertEqual(len(harness.rec["begin_memcore_input_turn"].calls), 0)
        state = harness.script.generation_kwargs[0]["request_projection_state"]
        self.assertTrue(state["current_input_transient"])
        self.assertFalse(state["record_request_projection"])
        self.assertEqual(state["current_user_source_id"], "")
        self.assertEqual(harness.script.generation_kwargs[0]["prompt_scope"], "")
        self.assertEqual(harness.rec["record_memcore_tool_batch"].calls[0][1]["memcore_turn_id"], "turn-attention-1")
        self.assertEqual(len(harness.rec["finalize_memcore_input_turn_for_delivery"].calls), 1)

    def test_attention_keeps_visible_delivery_when_existing_turn_primitive_is_unavailable(self) -> None:
        harness = _Harness([_speech_output("我也看到了。")])
        harness.rec["begin_memcore_hidden_host_turn"].result = {
            "ok": False,
            "status": "unsupported",
            "reason": "existing_stimulus_turn_unsupported",
            "turn_id": "",
            "writable": False,
        }
        harness.rec["append_memcore_standalone_assistant"].result = {
            "ok": True,
            "status": "recorded",
        }

        result = harness.run_sync(
            harness.payload(
                turn_kind="qq_attention",
                transient_user_message=True,
                memory_attention_reference_source_ids=["observed-source-fallback"],
            )
        )

        self.assertEqual(result["speech"], "我也看到了。")
        self.assertEqual(len(harness.rec["finalize_memcore_input_turn_for_delivery"].calls), 0)
        self.assertEqual(len(harness.rec["append_memcore_standalone_assistant"].calls), 1)

    def test_non_speculative_turn_does_persist_persona_care_and_summary(self) -> None:
        harness = _Harness([_speech_output("好的，明白了。")])
        harness.run_sync(harness.payload())

        rec = harness.rec
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
