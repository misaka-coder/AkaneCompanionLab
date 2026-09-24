from __future__ import annotations

import asyncio
from contextlib import closing
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from capcore import CapabilityResult
from companion_v01.bot_runtime import _record_host_job_completion
from companion_v01.client_protocol import ClientMode
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_tool_jobs import HostToolJobRuntime
from companion_v01.plugin_generation_codec import capability_descriptor_to_wire, capability_descriptor_from_wire
from companion_v01.turn_coordination import SteeringInput
from companion_v01.plugin_contribution_policy import _validate_background_semantics
from companion_v01.plugin_tool_bridge import PluginCapabilityToolHandler
from companion_v01.tool_continuation import can_finish_tool_batch, resolve_followup
from companion_v01.tool_handlers.core import ToolExecutionResult
from companion_v01.tool_handlers.qq_onebot import OneBotActionToolHandler
from tests.test_onebot_action_tool import _Port, _tool_context
from tests.test_plugin_engine_bridge import RecordingAdapter
from tests.test_turn_mainline_contract import _Harness, _speech_output, _tool_round_output
from tests.test_memcore_retention_anchor_slice import _manager


class ToolContinuationTests(unittest.TestCase):
    def test_onebot_requires_explicit_final_action_and_real_success(self):
        for ok, action, finish, expected in (
            (True, "group_poke", True, True),
            (True, "group_poke", False, False),
            (False, "group_poke", True, False),
            (True, "get_msg", True, False),
        ):
            with self.subTest(ok=ok, action=action, finish=finish):
                port = _Port({"ok": ok, "action": action, "status": "success" if ok else "failed"})
                handler = OneBotActionToolHandler(delivery_port=port)
                call = handler.normalize_call(
                    {"type": "onebot_action", "action": action, "params": {}, "finish_turn": finish}
                )
                result = handler.execute(call=call, context=_tool_context())
                self.assertEqual(can_finish_tool_batch([result]), expected)
                self.assertNotIn("finish_turn", port.calls[0]["params"])
                self.assertIn('"ok":', result.followup_context)

    def test_batch_cannot_hide_other_results_or_pending_delivery(self):
        done = ToolExecutionResult("action", followup=resolve_followup(default="none"))
        self.assertTrue(can_finish_tool_batch([done, done]))
        for other in (
            ToolExecutionResult("query"),
            ToolExecutionResult("job", stream_events=[{"type": "background_job_accepted"}]),
            ToolExecutionResult("artifact", stream_events=[{"type": "generated_file_ready"}]),
            ToolExecutionResult("image", model_image_inputs=[{}]),
            ToolExecutionResult("error", followup=resolve_followup(default="none"), stream_events=[{"type": "tool_execution_failed"}]),
        ):
            self.assertFalse(can_finish_tool_batch([done, other]))
        self.assertFalse(can_finish_tool_batch([]))

    def test_real_mainline_finishes_after_recording_in_sync_and_stream(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                harness = _Harness([_tool_round_output("", "onebot_action", "poke-1")], client_mode=ClientMode.QQ_TEXT)
                handler = OneBotActionToolHandler(delivery_port=_Port())
                harness.engine._execute_tool_call = lambda **kw: handler.execute(
                    call={"action": "group_poke", "params": {}, "finish_turn": True}, context=_tool_context()
                )
                if streaming:
                    result = [e["payload"] for e in harness.run_stream(harness.payload()) if e.get("type") == "final"][
                        0
                    ]
                else:
                    result = harness.run_sync(harness.payload())
                self.assertEqual(len(harness.script.generation_calls), 1)
                self.assertEqual(result["speech"], "")
                self.assertTrue(result["_deliberate_silence"])
                self.assertEqual(len(harness.rec["record_memcore_tool_batch"].calls), 1)
                self.assertEqual(len(harness.rec["finalize_memcore_input_turn_for_delivery"].calls), 1)
                self.assertFalse(harness.rec["abort_memcore_input_turn"].calls)
                self.assertFalse([m for m in harness.store.messages if m["role"] == "assistant"])

    def test_real_mainline_failure_returns_to_model(self):
        harness = _Harness([_tool_round_output("", "onebot_action", "poke-1"), _speech_output("未成功")])
        handler = OneBotActionToolHandler(delivery_port=_Port({"ok": False, "action": "group_poke"}))
        harness.engine._execute_tool_call = lambda **kw: handler.execute(
            call={"action": "group_poke", "params": {}, "finish_turn": True}, context=_tool_context()
        )
        result = harness.run_sync(harness.payload())
        self.assertEqual(len(harness.script.generation_calls), 2)
        self.assertEqual(result["speech"], "未成功")

    def test_user_steer_during_final_action_is_not_dropped(self):
        harness = _Harness([_tool_round_output("", "onebot_action", "poke-1"), _speech_output("也收到新的要求")])
        pending = []

        def execute(**kwargs):
            pending.append(SteeringInput(source_id="new-input", content="再帮我查一下", timestamp=100, actor_id="user"))
            return ToolExecutionResult("onebot_action", followup=resolve_followup(default="none"))

        def drain(token):
            steers = list(pending)
            pending.clear()
            return {"ok": True, "steers": steers, "stop_requested": False}

        harness.engine._execute_tool_call = execute
        harness.engine.turn_coordinator = SimpleNamespace(drain=drain, begin_finalization=drain)
        result = harness.run_sync(harness.payload(_turn_control_id="control"))
        self.assertEqual(result["speech"], "也收到新的要求")
        self.assertEqual(len(harness.script.generation_calls), 2)
        self.assertEqual(len(harness.rec["append_memcore_turn_user_input"].calls), 1)

    def test_empty_host_final_preserves_real_memcore_tool_pair(self):
        with tempfile.TemporaryDirectory() as directory, closing(_manager(directory)) as manager:
            scope = {"profile_user_id": "p", "session_id": "s", "character_pack_id": "reimu"}
            opened = manager.begin_input_turn({"source_id": "u", "content": "戳我", "timestamp": 100}, **scope)
            recorded = manager.record_tool_batch(
                turn_id=opened["turn_id"],
                exchanges=[
                    {
                        "tool_name": "onebot_action",
                        "tool_call_id": "poke",
                        "tool_input": {"action": "group_poke", "finish_turn": True},
                        "result": '{"ok":true,"status":"success"}',
                        "source": "native_openai",
                        "timestamp": 101,
                        "result_status": "success",
                    }
                ],
                **scope,
            )
            self.assertTrue(recorded["ok"], recorded)
            engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
            engine._memcore_manager_if_enabled = lambda: manager
            engine._chat_provider_protocol_for_memcore = lambda **kwargs: "openai_chat"
            final = engine._finalize_memcore_input_turn_for_delivery(
                final_output={"_tool_finished_turn": True, "_deliberate_silence": True},
                turn_id=opened["turn_id"],
                assistant_record={"source_id": "silent-final", "content": "", "timestamp": 102},
                memory_metadata={},
                provider_output_raw="",
                chat_model_override="",
                annotation_status="missing",
                **scope,
            )
            self.assertTrue(final)
            projected = manager.build_context_projection(provider_profile="openai_chat", **scope)
            self.assertIn("group_poke", str(projected))
            self.assertIn("success", str(projected))
            self.assertNotIn("silent-final", str(projected))
            self.assertEqual(
                manager._store._conn.execute("SELECT COUNT(*) FROM messages WHERE turn_role='final'").fetchone()[0], 0
            )

    def test_mixed_batch_continues_in_real_mainline(self):
        output = _tool_round_output("", "onebot_action", "poke-1")
        from companion_v01.tool_invocation import NATIVE_TOOL_CALLS_FIELD

        output[NATIVE_TOOL_CALLS_FIELD].append({"type": "web_search", "id": "query", "arguments": {}})
        harness = _Harness([output, _speech_output("查到了")])
        harness.engine._execute_tool_call = lambda **kw: ToolExecutionResult(
            kw["tool_call"]["type"], followup=resolve_followup(
                default="none" if kw["tool_call"]["type"] == "onebot_action" else "required")
        )
        result = harness.run_sync(harness.payload())
        self.assertEqual(result["speech"], "查到了")
        self.assertEqual(len(harness.script.generation_calls), 2)

    def test_plugin_registration_controls_schema_and_keeps_business_args_clean(self):
        adapter = RecordingAdapter()
        descriptor = asyncio.run(adapter.list_capabilities())[0]
        for policy, error in (("required", False), ("optional", False), ("optional", True)):
            with self.subTest(policy=policy, error=error):
                configured = replace(descriptor, raw={**descriptor.raw, "model_followup": policy})
                configured = capability_descriptor_from_wire(capability_descriptor_to_wire(configured))
                self.assertTrue(_validate_background_semantics(configured).accepted)
                handler = PluginCapabilityToolHandler(
                    capability_id=descriptor.id, descriptor=configured, adapter=adapter
                )
                self.assertNotIn("finish_turn", handler.tool_spec().input_schema["properties"])
                args = {"type": descriptor.id, "query": "test"}
                if policy == "optional":
                    args["finish_turn"] = True
                call = handler.normalize_call(args)
                from companion_v01.tool_invocation import TOOL_MODEL_ARGUMENTS_FIELD

                native_wire = {**args, TOOL_MODEL_ARGUMENTS_FIELD: {
                    key: value for key, value in args.items() if key != "type"
                }}
                self.assertEqual(handler.normalize_call(native_wire), call)
                adapter.result = CapabilityResult(
                    is_error=error, status="error" if error else "ok", content={"value": 1}
                )
                original = adapter.invoke
                seen = []

                async def invoke(capability_id, arguments, ctx):
                    seen.append(dict(arguments))
                    return await original(capability_id, arguments, ctx)

                adapter.invoke = invoke
                try:
                    result = handler.execute(call=call, context=_tool_context())
                finally:
                    adapter.invoke = original
                self.assertEqual(can_finish_tool_batch([result]), policy == "optional" and not error)
                self.assertEqual(seen, [{"query": "test"}])
        invalid = replace(descriptor, raw={"model_followup": "silent"})
        self.assertFalse(_validate_background_semantics(invalid).accepted)
        background = replace(descriptor, raw={"model_followup": "optional", "execution_class": "long_task"})
        handler = PluginCapabilityToolHandler(capability_id=descriptor.id, descriptor=background, adapter=adapter)
        self.assertNotIn("finish_turn", handler.tool_spec().input_schema["properties"])
        collision = replace(
            descriptor, raw={"model_followup": "optional"}, inputs=(replace(descriptor.inputs[0], name="finish_turn"),)
        )
        self.assertTrue(_validate_background_semantics(collision).accepted)
        handler = PluginCapabilityToolHandler(capability_id=descriptor.id, descriptor=collision, adapter=adapter)
        self.assertEqual(handler.normalize_call({"type": descriptor.id, "finish_turn": "business"})["arguments"],
                         {"finish_turn": "business"})


class SilentJobMemoryTests(unittest.TestCase):
    def test_persistence_failure_remains_pending_and_recovery_uses_same_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = HostJobStore(Path(directory) / "jobs.db")
            owner = HostJobOwner("p", "s")
            created = store.create(
                owner=owner,
                capability_source="tool",
                capability_id="plugin.test",
                payload={},
                idempotency_key="one",
                argument_fingerprint="x",
                character_pack_id="reimu",
                completion_mode="silent",
                memory_mode="timeline",
            )
            store.request_cancel(created["job_id"], owner=owner)
            recorder = Mock(side_effect=[{"ok": False, "reason": "busy"}, {"ok": True}])
            engine = SimpleNamespace(record_plugin_timeline_event=recorder)
            runtime = HostToolJobRuntime(
                engine=engine,
                store=store,
                background_tasks=None,
                terminal_callback=lambda job: _record_host_job_completion(engine, job),
            )
            runtime._publish_terminal(created["job_id"], owner)
            pending = store.pending_completions()[0]
            self.assertEqual(pending.completion_last_error, "busy")
            self.assertEqual(pending.status, "cancelled")
            runtime._publish_terminal(created["job_id"], owner)
            runtime._publish_terminal(created["job_id"], owner)
            self.assertEqual(recorder.call_count, 2)
            self.assertEqual(recorder.call_args_list[0].args[0], recorder.call_args_list[1].args[0])
            self.assertEqual(store.pending_completions(), [])

    def test_timeline_result_is_pending_durable_and_memcore_idempotent_without_model(self):
        with tempfile.TemporaryDirectory() as directory, closing(_manager(directory)) as manager:
            store = HostJobStore(Path(directory) / "jobs.db")
            owner = HostJobOwner("p", "s")
            created = store.create(
                owner=owner,
                capability_source="tool",
                capability_id="plugin.test",
                payload={},
                idempotency_key="one",
                argument_fingerprint="x",
                character_pack_id="reimu",
                completion_mode="silent",
                memory_mode="timeline",
            )
            claimed = store.claim(created["job_id"], worker_id="test")
            store.succeed(created["job_id"], claim_token=claimed["claim_token"], result_summary="unique final fact")
            reopened = HostJobStore(store.database_path)
            job = reopened.pending_completions()[0]
            engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
            engine._memcore_manager_if_enabled = lambda: manager
            engine._resolve_payload_character_pack_id = lambda p: p["character_pack_id"]
            engine._memcore_owns_compaction = lambda: False
            engine._generate_round = Mock(side_effect=AssertionError("must not wake model"))
            first = _record_host_job_completion(engine, job)
            count_before = manager._store._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            second = _record_host_job_completion(engine, job)
            self.assertTrue(first.ok, first)
            self.assertTrue(second.ok, second)
            self.assertEqual(manager._store._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], count_before)
            projection = manager.build_context_projection(
                provider_profile="openai_chat", profile_user_id="p", session_id="s", character_pack_id="reimu"
            )
            self.assertIn("unique final fact", str(projection))
            engine._generate_round.assert_not_called()
            reopened.mark_completion_delivered(job.job_id, completion_event_id=job.completion_event_id)
            self.assertEqual(reopened.pending_completions(), [])

    def test_current_turn_only_result_stays_in_job_store(self):
        engine = SimpleNamespace(record_plugin_timeline_event=Mock(side_effect=AssertionError("must not write")))
        result = _record_host_job_completion(engine, SimpleNamespace(memory_mode="current_turn"))
        self.assertTrue(result.ok)
        engine.record_plugin_timeline_event.assert_not_called()
