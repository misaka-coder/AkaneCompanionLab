"""Real managed invocation, foreground deadlines, controls and complete results."""
from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from capcore import CapabilityDescriptor, CapabilityResult, CapabilityToolSpec

from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.client_protocol import ClientMode
from companion_v01.engine_services.tool_hooks import ToolHooksMixin
from companion_v01.executor_broker import ExecutorBroker
from companion_v01.execution_wait import batch_wait_budget
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_tool_jobs import HostToolJobRuntime
from companion_v01.native_tool_schema import build_openai_native_tool_from_spec
from companion_v01.tool_batch import execute_tool_batch
from companion_v01.tool_handlers.core import BaseToolHandler, ToolExecutionAdmission, ToolExecutionContext, ToolExecutionResult, ToolFollowupEnvelope, ToolMetadata
from companion_v01.tool_handlers.execution import ExecStatusToolHandler, ExecCancelToolHandler
from companion_v01.tool_handlers.adapters import AdapterCapabilityToolHandler
from companion_v01.plugin_tool_bridge import PluginCapabilityToolHandler
from companion_v01.tool_invocation import ToolInvocation
from companion_v01.tool_orchestration.execution import execute_tool_invocation, execute_tool_call


class BusinessTool(BaseToolHandler):
    def __init__(self, name="business.read", *, delay=0, release=None, read_only=True, result=None, business_background=False):
        self.tool_type, self.delay, self.release = name, delay, release
        self.read_only, self.result, self.business_background = read_only, result, business_background
        self.started = threading.Event()
        self.calls = []

    def tool_spec(self):
        properties = {"value": {"type": "string"}}
        if self.business_background:
            properties["background"] = {"type": "boolean"}
        return CapabilityToolSpec(capability_id=self.tool_type, display_name="Test tool", description="Test business tool",
            input_schema={"type": "object", "properties": properties, "additionalProperties": False},
            risk="low", confirm="never", effects=(), visible_in=("qq", "desktop"),
            idempotency="read_only" if self.read_only else "effectful")

    def tool_metadata(self):
        return ToolMetadata(family="test", operation="read" if self.read_only else "write", risk="low")

    def normalize_call(self, value):
        args = {key: item for key, item in value.items() if key != "type" and not key.startswith("_tool_")}
        if set(args) - set(self.tool_spec().input_schema["properties"]):
            return None
        return {"type": self.tool_type, **args}

    def execute(self, *, call, context):
        self.calls.append((call, context))
        self.started.set()
        if self.release:
            self.release.wait(3)
        if self.delay:
            time.sleep(self.delay)
        return self.result or ToolExecutionResult(tool_type=self.tool_type, followup_context="actual:" + str(call.get("value", "")))


class Engine(ToolHooksMixin):
    def __init__(self):
        self.tool_handlers = {"exec_status": ExecStatusToolHandler(execution_provider=None),
                              "exec_cancel": ExecCancelToolHandler(execution_provider=None)}
        self.executor_broker = ExecutorBroker(None)

    def _resolve_tool_handlers(self, **kwargs):
        selection = kwargs.get("capability_selection")
        return selection.resolved_handlers if selection is not None else self.tool_handlers


class ForegroundExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.engine = Engine()
        self.store = HostJobStore(self.root / "jobs.db")
        self.runner = BackgroundTaskRunner({"host-jobs": 3})
        self.published = []
        self.runtime = HostToolJobRuntime(engine=self.engine, store=self.store, background_tasks=self.runner,
            conversation_ref_issuer=lambda context: "signed-origin",
            terminal_callback=lambda job: self.published.append(job) or True)
        self.engine.host_tool_jobs = self.runtime
        self.owner = HostJobOwner("owner", "conversation")
        self.client = SimpleNamespace(effective_mode=ClientMode.QQ_TEXT)
        self.context = ToolExecutionContext(profile_user_id="owner", session_id="conversation", now_ts=123,
            visual_payload={"nested": {"value": "original"}}, character_pack_id="cecilia", client_mode="qq_text",
            current_user_source_id="message-1", request_context={"_host_origin_turn_id": "turn-1", "actor_stable_id": "qq:owner"})
        self.work = self.runtime.completion_work.begin(owner=self.owner, character_pack_id="cecilia", channel="qq_text", turn_id="turn-1")

    def tearDown(self):
        for handler in self.engine.tool_handlers.values():
            release = getattr(handler, "release", None)
            if release:
                release.set()
        self.runner.wait_idle(lane="host-jobs", timeout=4)
        self.runtime.completion_work.close(self.work)
        self.runner.close()
        self.tmp.cleanup()

    def add(self, handler):
        self.engine.tool_handlers[handler.tool_type] = handler
        return handler

    def invoke(self, name, args=None, *, call_id="call-1", context=None):
        context = context or self.context
        return execute_tool_invocation(self.engine, invocation=ToolInvocation(name=name, id=call_id, arguments=args or {}),
            profile_user_id=context.profile_user_id, session_id=context.session_id, character_pack_id=context.character_pack_id,
            now_ts=context.now_ts, visual_payload=context.visual_payload, current_user_source_id=context.current_user_source_id,
            request_context=context.request_context, client_context=self.client)[0]

    def idle(self):
        self.assertTrue(self.runner.wait_idle(lane="host-jobs", timeout=3))

    def test_fast_sync_preserves_original_context_and_all_result_facets(self):
        result = ToolExecutionResult(tool_type="business.read", raw_turns=[{"speaker": "店员", "speech": "完成"}],
            followup_context="whole-page-END", followup_envelope=ToolFollowupEnvelope("whole-page-END", True, False, {"cursor": "next"}),
            stream_events=[{"type": "generated_file_ready", "generated_file": {"generated_handle": "gen_001"}}],
            model_image_inputs=[{"data_url": "data:image/png;base64,aGVsbG8=", "attachment_id": "image-1"}],
            state_updates={"character_context": {"location": "library"}}, trace_receipt={"source_id": "receipt"})
        handler = self.add(BusinessTool(result=result))
        target = object()
        self.context.request_context["_model_execution_target"] = target
        actual = self.invoke(handler.tool_type, {"value": "hello"})
        self.assertEqual(actual.followup_context, result.followup_context)
        self.assertEqual(actual.state_updates, result.state_updates)
        self.assertEqual(actual.model_image_inputs, result.model_image_inputs)
        self.assertEqual(actual.raw_turns, result.raw_turns)
        self.assertIs(handler.calls[0][1].request_context["_model_execution_target"], target)
        self.assertEqual(handler.calls[0][1].invocation_id, "call-1")
        self.assertEqual(self.work.collect(), [])
        self.work.commit()
        job = self.store.get_by_key(owner=self.owner, capability_source="tool", capability_id=handler.tool_type, idempotency_key="call-1")
        self.assertEqual(job.model_consumed_by, "turn-1")
        self.assertNotIn("_model_execution_target", job.payload["request_context"])

    def test_slow_sync_returns_same_queryable_id_and_completes_once(self):
        release = threading.Event()
        handler = self.add(BusinessTool(release=release, result=ToolExecutionResult(tool_type="business.read",
            followup_context="long-result-END", state_updates={"character_context": {"location": "shop"}},
            model_image_inputs=[{"data_url": "data:image/png;base64,aGVsbG8=", "attachment_id": "slow-image"}])))
        started = time.monotonic()
        with batch_wait_budget(.08):
            pending = self.invoke(handler.tool_type)
        self.assertLess(time.monotonic() - started, .5)
        job_id = pending.state_updates["capability_execution"]["job_id"]
        self.assertIn(pending.state_updates["capability_execution"]["job_status"], {"queued", "running"})
        self.assertEqual(len(handler.calls), 1)
        status = self.invoke("exec_status", {"run_id": job_id}, call_id="status-1")
        self.assertIn("running", status.followup_context)
        release.set(); self.idle()
        evidence = self.work.collect()
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].job.job_id, job_id)
        self.assertTrue(evidence[0].result.followup_context.endswith("long-result-END"))
        self.assertEqual(evidence[0].result.state_updates["character_context"]["location"], "shop")
        self.assertEqual(evidence[0].result.model_image_inputs[0]["attachment_id"], "slow-image")
        self.work.commit()
        self.assertEqual(self.work.collect(), [])
        self.assertEqual(len(handler.calls), 1)

    def test_explicit_background_skips_the_window_and_later_fast_batch(self):
        handler = self.add(BusinessTool(release=threading.Event()))
        started = time.monotonic()
        with batch_wait_budget(8):
            pending = self.invoke(handler.tool_type, {"background": True})
        self.assertLess(time.monotonic() - started, .4)
        self.assertFalse(self.work.waiting_ids())
        fast = self.add(BusinessTool("business.fast"))
        started = time.monotonic()
        with batch_wait_budget(8):
            actual = self.invoke(fast.tool_type, {"value": "next"}, call_id="next")
        self.assertEqual(actual.followup_context, "actual:next")
        self.assertLess(time.monotonic() - started, .4)
        self.assertTrue(handler.started.wait(1))
        self.assertNotIn("background", handler.calls[0][0])
        self.assertIn("job_id", pending.followup_context)

    def test_mixed_read_batch_only_waits_for_foreground_and_all_default_share_budget(self):
        slow = self.add(BusinessTool("business.slow", release=threading.Event()))
        medium = self.add(BusinessTool("business.medium", delay=.04))
        calls = [{"type": slow.tool_type, "background": True}, {"type": medium.tool_type, "value": "B"}]
        def execute(call):
            return execute_tool_call(self.engine, tool_call=call, profile_user_id="owner", session_id="conversation",
                character_pack_id="cecilia", now_ts=123, visual_payload={}, client_context=self.client,
                request_context=self.context.request_context)
        started = time.monotonic()
        results = execute_tool_batch(calls, execute=execute, handler_for=lambda call: self.engine.tool_handlers[call["type"]], wait_seconds=.6)
        self.assertLess(time.monotonic() - started, .4)
        self.assertIn("job_id", results[0].followup_context)
        self.assertEqual(results[1].followup_context, "actual:B")
        # Three default calls share one deadline, including pool queue time.
        third = self.add(BusinessTool("business.third", release=threading.Event()))
        started = time.monotonic()
        results = execute_tool_batch([{"type": name} for name in (medium.tool_type, slow.tool_type, third.tool_type)],
            execute=execute, handler_for=lambda call: self.engine.tool_handlers[call["type"]], wait_seconds=.25)
        self.assertLess(time.monotonic() - started, .65)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0].followup_context, "actual:")
        self.assertTrue(all("job_id" in item.followup_context for item in results[1:]))

    def test_unfinished_write_allows_independent_work_and_controls(self):
        write = self.add(BusinessTool("business.write", release=threading.Event(), read_only=False))
        read = self.add(BusinessTool("business.after"))
        def execute(call):
            return self.invoke(call["type"], {key: value for key, value in call.items() if key != "type"}, call_id=call["type"])
        results = execute_tool_batch([{"type": write.tool_type}, {"type": read.tool_type, "background": True}],
            execute=execute, handler_for=lambda call: self.engine.tool_handlers[call["type"]], wait_seconds=.05)
        read_job_id = results[1].state_updates["capability_execution"]["job_id"]
        read_result = self.invoke("exec_status", {"run_id": read_job_id, "wait_seconds": 2}, call_id="read-status")
        self.assertIn("actual:", read_result.followup_context)
        self.assertEqual(len(read.calls), 1)
        self.assertFalse(write.release.is_set())
        job_id = results[0].state_updates["capability_execution"]["job_id"]
        cancelled = self.invoke("exec_cancel", {"run_id": job_id}, call_id="cancel")
        self.assertIn("cancelling", cancelled.followup_context)
        self.assertEqual(self.store.get(job_id, owner=self.owner).status, "running")
        write.release.set(); self.idle()
        self.assertEqual(self.store.get(job_id, owner=self.owner).status, "succeeded")

    def test_query_wait_suppresses_notice_and_pagination_survives_consumption(self):
        body = "完整正文。" * 6000 + "END"
        release = threading.Event()
        handler = self.add(BusinessTool(release=release, result=ToolExecutionResult(tool_type="business.read",
            followup_context=body, stream_events=[{"type": "result_presentation", "value": "only once"}])))
        pending = self.invoke(handler.tool_type, {"background": True})
        job_id = pending.state_updates["capability_execution"]["job_id"]
        timer = threading.Timer(.04, release.set); timer.start()
        page = self.invoke("exec_status", {"run_id": job_id, "wait_seconds": 2}, call_id="wait")
        timer.join()
        self.assertIn("END", body)
        self.assertTrue(page.followup_envelope.producer_bounded)
        self.work.commit()
        self.assertEqual(self.work.collect(), [])
        chunks = [page.followup_context.split("\n", 1)[1]]
        cursor = page.state_updates["capability_execution"]["next_cursor"]
        while cursor:
            page = self.invoke("exec_status", {"run_id": job_id, "cursor": cursor}, call_id=cursor)
            chunks.append(page.followup_context.split("\n", 1)[1])
            self.assertEqual(page.stream_events, [])
            cursor = page.state_updates["capability_execution"]["next_cursor"]
        self.assertEqual("".join(chunks), body)

    def test_invalid_flag_and_approval_stop_before_task_admission(self):
        handler = self.add(BusinessTool())
        failure = self.invoke(handler.tool_type, {"background": "yes"})
        self.assertIn("must_be_boolean", failure.followup_context)
        self.assertEqual(handler.calls, [])
        handler.admit_execution = lambda **kwargs: ToolExecutionAdmission.stop(ToolExecutionResult(tool_type=handler.tool_type,
            stream_events=[{"type": "approval_required"}], followup_context="approval required"))
        blocked = self.invoke(handler.tool_type, {"background": True}, call_id="approval")
        self.assertEqual(blocked.followup_context, "approval required")
        self.assertNotIn("job_id", blocked.state_updates)
        self.assertEqual(self.store.pending_job_ids(capability_source="tool"), [])

    def test_business_background_collision_preserves_provider_argument(self):
        handler = self.add(BusinessTool(business_background=True))
        schema = build_openai_native_tool_from_spec(handler.tool_spec())["function"]["parameters"]
        self.assertIn("background", schema["properties"])
        self.assertIn("host_background", schema["properties"])
        pending = self.invoke(handler.tool_type, {"background": True, "host_background": True})
        self.idle()
        self.assertEqual(handler.calls[0][0]["background"], True)
        self.assertNotIn("host_background", handler.calls[0][0])
        self.assertIn("job_id", pending.followup_context)

    def test_native_mcp_and_plugin_host_flag_never_reaches_business_adapter(self):
        from companion_v01.tool_invocation import TOOL_MODEL_ARGUMENTS_FIELD, TOOL_MODEL_NAME_FIELD, TOOL_SOURCE_FIELD, TOOL_INVOCATION_ID_FIELD
        class Adapter:
            def __init__(self):
                self.calls = []
            async def invoke(self, capability_id, args, ctx):
                self.calls.append((capability_id, args, ctx))
                return CapabilityResult(is_error=False, value={"echo": args["value"]})
        for handler_class, name in ((AdapterCapabilityToolHandler, "mcp.demo.echo"),
                                    (PluginCapabilityToolHandler, "plugin.demo.echo")):
            with self.subTest(name=name):
                adapter = Adapter()
                descriptor = CapabilityDescriptor(id=name, display_name="Echo", short_hint="Echo a value",
                    visible_in=("qq", "desktop"), prompt_exposed=True, risk="low", confirm="never",
                    effects=(), trigger=None, inputs=(), outputs=(), raw={},
                    input_schema={"type": "object", "properties": {"value": {"type": "string"}},
                                  "required": ["value"], "additionalProperties": False})
                handler = self.add(handler_class(capability_id=name, adapter=adapter, descriptor=descriptor,
                    config_base_dir=self.root))
                actual = execute_tool_call(self.engine, tool_call={"type": name, "value": "hello", "background": True,
                    TOOL_MODEL_NAME_FIELD: name.replace(".", "_"), TOOL_MODEL_ARGUMENTS_FIELD: {"value": "hello", "background": True},
                    TOOL_SOURCE_FIELD: "native_openai", TOOL_INVOCATION_ID_FIELD: name},
                    profile_user_id="owner", session_id="conversation", character_pack_id="cecilia",
                    now_ts=123, visual_payload={}, client_context=self.client, request_context=self.context.request_context)
                self.assertIn("job_id", actual.followup_context)
                self.idle()
                self.assertEqual(len(adapter.calls), 1)
                self.assertEqual(adapter.calls[0][1], {"value": "hello"})
                self.assertEqual(adapter.calls[0][2].profile_user_id, "owner")
                if handler_class is PluginCapabilityToolHandler:
                    self.assertEqual(adapter.calls[0][2].character_pack_id, "cecilia")

    def test_queued_cancel_and_duplicate_id_do_not_execute_twice(self):
        release = threading.Event()
        for index in range(3):
            self.runner.submit(lane="host-jobs", name="hold", fn=release.wait, args=(3,))
        handler = self.add(BusinessTool())
        try:
            first = self.invoke(handler.tool_type, {"background": True})
            second = self.invoke(handler.tool_type, {"background": True})
            job_id = first.state_updates["capability_execution"]["job_id"]
            self.assertEqual(second.state_updates["capability_execution"]["job_id"], job_id)
            self.assertEqual(self.store.get(job_id, owner=self.owner).status, "queued")
            cancelled = self.invoke("exec_cancel", {"run_id": job_id}, call_id="cancel-queued")
            self.assertIn("cancelled", cancelled.followup_context)
        finally:
            release.set()
        self.idle()
        self.assertEqual(handler.calls, [])

    def test_context_containers_are_frozen_and_wrong_actor_cannot_query(self):
        handler = self.add(BusinessTool(release=threading.Event()))
        pending = self.invoke(handler.tool_type, {"background": True})
        self.context.visual_payload["nested"]["value"] = "changed"
        self.context.request_context["actor_stable_id"] = "qq:other"
        self.assertTrue(handler.started.wait(1))
        self.assertEqual(handler.calls[0][1].visual_payload["nested"]["value"], "original")
        job_id = pending.state_updates["capability_execution"]["job_id"]
        denied = self.invoke("exec_status", {"run_id": job_id}, call_id="wrong-actor")
        self.assertIn("unavailable", denied.followup_context)

    def test_bound_desktop_branch_keeps_original_receipt_and_returns_without_waiting(self):
        from companion_v01.executor_broker import BrokerExecutionResult
        from companion_v01.tool_handlers.web_browser import OpenBrowserToolHandler
        release, entered = threading.Event(), threading.Event()
        observed = []
        class Broker:
            def execute(self, **kwargs):
                observed.append(kwargs)
                entered.set()
                release.wait(3)
                return BrokerExecutionResult(status="succeeded", model_feedback="Opened on the bound device.")
        self.add(OpenBrowserToolHandler())
        self.engine.executor_broker = Broker()
        try:
            invocation = ToolInvocation(name="open_browser", arguments={"url": "https://example.com", "background": True},
                id="desktop-call", execution_receipt={"instance_id": "original-device", "offer_id": "original-offer"})
            started = time.monotonic()
            pending, _ = execute_tool_invocation(self.engine, invocation=invocation,
                profile_user_id="owner", session_id="conversation", character_pack_id="cecilia",
                now_ts=123, visual_payload={}, client_context=self.client, request_context=self.context.request_context)
            self.assertLess(time.monotonic() - started, .5)
            self.assertIn("job_id", pending.followup_context)
            self.assertTrue(entered.wait(1))
            self.assertEqual(observed[0]["receipt_value"]["instance_id"], "original-device")
            self.assertNotIn("background", observed[0]["arguments"])
        finally:
            release.set()
        self.idle()
        evidence = self.work.collect()
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].result.state_updates["capability_execution"]["status"], "succeeded")

    def test_queued_context_lost_on_restart_is_failed_without_replay(self):
        handler = self.add(BusinessTool())
        created = self.store.create(owner=self.owner, capability_source="tool", capability_id=handler.tool_type,
            payload={"managed_invocation": True, "call": {"type": handler.tool_type}},
            idempotency_key="recovery", argument_fingerprint="test", character_pack_id="cecilia", channel="qq_text",
            delivery_target="signed-origin", turn_id="old-turn", completion_mode="agent", memory_mode="timeline")
        self.runtime.recover(); self.idle()
        self.assertEqual(self.store.get(created["job_id"], owner=self.owner).status, "failed")
        self.assertEqual(self.store.get(created["job_id"], owner=self.owner).last_error, "original_execution_context_lost_no_replay")
        self.assertEqual(handler.calls, [])


if __name__ == "__main__":
    unittest.main()
