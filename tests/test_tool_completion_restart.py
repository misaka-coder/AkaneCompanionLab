"""Restarted request receipts, using only temporary jobs/MemCore databases."""
from __future__ import annotations

import copy
import json
import tempfile
import threading
import unittest
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.client_protocol import ClientMode
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.engine_services.response_projection import _build_memcore_provider_history
from companion_v01.engine_services.turn_contracts import (
    PreparedTurn, TurnDiagnostics, TurnIdentity, TurnMemoryState, TurnPersistence,
    TurnPromptState, TurnRequest,
)
from companion_v01.engine_services.turn_rounds import run_turn_rounds
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_tool_jobs import HostToolJobRuntime
from companion_v01.llm_runtime import LLMRuntime, ModelRequestCancelled
from companion_v01.model_runtime.models import _CompletionPayload
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.tool_handlers.core import ToolExecutionContext, ToolExecutionResult
from companion_v01.tool_invocation import NATIVE_OPENAI, TOOL_SOURCE_FIELD
from companion_v01.turn_coordination import cancellation_scope
from tests import test_llm_runtime_stream as llm_helpers
from tests import test_stream_final_nonstream_fallback as response_helpers
from tests.test_tool_foreground_execution import BusinessTool, Engine as ToolEngine
from companion_v01.host_tool_job_runtime.contracts import _job_accepted_result
from tests import test_tool_completion_boundary as boundary_helpers


def exhaust(iterator):
    events = []
    while True:
        try:
            events.append(next(iterator))
        except StopIteration as stopped:
            return events, stopped.value


class ToolCompletionRestartTests(unittest.TestCase):
    scope = dict(profile_user_id="restart-user", session_id="restart-session", character_pack_id="fixture")
    profile = "openai_chat"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="akane-completion-restart-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.owner = HostJobOwner(self.scope["profile_user_id"], self.scope["session_id"])
        self.published = []
        self.source_id = "restart-input"
        self.requests = []
        self.managers = []
        self.runners = []
        self.addCleanup(self.close_resources)
        self.new_runtime()
        opened = self.manager.begin_input_turn(
            {"source_id": "restart-input", "content": "finish the fixture task", "timestamp": 100}, **self.scope)
        self.assertTrue(opened["ok"], opened)
        self.turn_id = opened["turn_id"]
        self.old_work = self.runtime.completion_work.begin(owner=self.owner,
            character_pack_id="fixture", channel="qq_text", turn_id=self.turn_id)
        self.context = ToolExecutionContext(**self.scope, now_ts=100, visual_payload={}, client_mode="qq_text",
            current_user_source_id="restart-input", request_context={"_host_origin_turn_id": self.turn_id})

    def close_resources(self):
        for runner in self.runners:
            runner.close()
        for manager in self.managers:
            manager.close()

    def new_runtime(self):
        self.store = HostJobStore(self.root / "jobs.db")
        self.runner = BackgroundTaskRunner({"host-jobs": 2, "host-job-completions": 1})
        self.runners.append(self.runner)
        self.runtime = HostToolJobRuntime(engine=ToolEngine(), store=self.store, background_tasks=self.runner,
            conversation_ref_issuer=lambda _: "fixture-ref",
            terminal_callback=lambda job: self.published.append(job) or False)
        self.manager = MemcoreManager(backend="memcore", storage_path=self.root / "memcore.db",
            visible_scope="conversation", enable_flavor=True, shadow_compare=False,
            llm=SimpleNamespace(), embedding_provider=None, retrieval_mode="navigation")
        self.assertTrue(self.manager.available, self.manager._reason)
        self.managers.append(self.manager)

    def managed_result(self, number=1, *, text=None):
        handler = BusinessTool("project_inspect", result=ToolExecutionResult(tool_type="project_inspect",
            followup_context=text or f"terminal-fixture-{number}-END"))
        result = self.runtime.invoke(handler=handler, capability_id=handler.tool_type,
            invocation_id=f"fixture-call-{number}", call={"type": handler.tool_type, "value": str(number)},
            context=self.context, dispatch=lambda context, call, handler: handler.execute(call=call, context=context))
        self.assertTrue(self.runner.wait_idle(lane="host-jobs", timeout=3))
        job = self.store.get_by_key(owner=self.owner, capability_source="tool", capability_id=handler.tool_type,
            idempotency_key=f"fixture-call-{number}")
        self.assertEqual(job.status, "succeeded")
        self.assertIn(job.job_id, self.old_work.queried)
        self.assertFalse(job.model_consumed_by)
        self.assertEqual(len(handler.calls), 1)
        return job, result

    def record_result(self, job, result, *, name="project_inspect", call_id=None, arguments=None):
        recorded = self.manager.record_tool_batch(turn_id=self.turn_id, exchanges=[{
            "tool_name": name, "tool_call_id": call_id or job.tool_call_id,
            "tool_input": arguments if arguments is not None else {"value": "fixture"},
            "result": result.followup_context, "result_status": "success", "source": "native_openai",
            "timestamp": 101, "source_id_prefix": "trace:" + (call_id or job.tool_call_id)}], **self.scope)
        self.assertTrue(recorded["ok"], recorded)
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.memcore_manager = self.manager
        history = []
        projected = engine._append_tool_history_batch(tool_history_turns=history,
            items=[({"type": name, TOOL_SOURCE_FIELD: NATIVE_OPENAI}, result, result.followup_context)],
            trace_source_ids=[recorded["exchanges"][0][key] for key in ("tool_use_source_id", "tool_result_source_id")],
            provider_profile=self.profile, memcore_turn_id=self.turn_id, current_user_source_id="restart-input", **self.scope)
        self.assertTrue(projected["ok"], projected)
        self.assertTrue(history)
        return recorded

    def projection(self, engine):
        with patch("config.MEMORY_BACKEND", "memcore"):
            result = _build_memcore_provider_history(engine, current_source_id=self.source_id,
                chat_model_override="", **self.scope)
        self.assertTrue(result["ok"], result)
        return result

    def freeze_before_restart(self):
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.memcore_manager = self.manager
        engine.llm = SimpleNamespace(supports_request_observer=True, chat_provider_protocol=lambda **_: self.protocol())
        projection = self.projection(engine)
        context = {"memcore_projection_read": projection}
        observer = engine._build_memcore_request_observer(generation_context=context, **self.scope)
        actual = [copy.deepcopy(message["payload"]) for message in projection["current_turn_messages"]]
        actual[0].pop("text", None)  # Provider wire users contain content, not the rendering hint.
        result = observer({"protocol": self.profile, "persistent_turn_messages": actual,
            "history_messages": actual, "audit_history_messages": actual})
        self.assertEqual(result["status"], "recorded", context)
        self.assertTrue(all(message["projection_status"] == "request_frozen"
            for message in self.projection(engine)["current_turn_messages"]))

    def protocol(self):
        return "anthropic" if self.profile.startswith("anthropic") else "openai"

    def restart(self):
        # Do not close the old work: that would simulate a graceful release,
        # not the lost in-memory receipt relationship under investigation.
        self.runner.close()
        self.manager.close()
        self.new_runtime()
        self.published.clear()

    def build_engine(self, *, failure=False, cancelled=None, omit_results=False, before_reply=None,
                     fail_attempts=0, omit_after_first=False, change_result=False, native_tools=None):
        runtime = llm_helpers.TopLevelJSONStreamTapTests._sync_runtime('{"speech":"fixture valid reply","tool_call":null}')
        runtime.snapshot_metrics = lambda: {}
        runtime.supports_request_observer = True
        runtime.chat_provider_protocol = lambda **_: self.protocol()
        bundle = SimpleNamespace(client=SimpleNamespace(protocol=self.protocol()), model="fixture")
        runtime._request_bundle = lambda **_: bundle
        runtime._is_responses_protocol = lambda _: False
        runtime._normalize_history_turns_for_payload = lambda messages, **_: list(messages or [])

        def payload(**kwargs):
            suffix = copy.deepcopy(list(kwargs.get("post_user_turns") or []))
            if change_result:
                for message in suffix:
                    if message.get("role") == "tool":
                        message["content"] = "different untrusted result"
            user_content = kwargs["user_prompt"]
            if self.protocol() == "anthropic":
                user_content = [{"type": "text", "text": user_content}]
            messages = [
                {"role": "system", "content": kwargs["system_prompt"]},
                *list(kwargs.get("history_turns") or []),
                {"role": "user", "content": user_content},
                *([] if omit_results or (omit_after_first and self.requests) else suffix)]
            built = _CompletionPayload(model="fixture", messages=messages, stream=kwargs.get("stream", False))
            built.persistent_message_indexes = tuple(range(1 + len(kwargs.get("history_turns") or []), len(messages)))
            return built

        def create(**kwargs):
            self.requests.append(copy.deepcopy(kwargs["payload"]["messages"]))
            if before_reply:
                before_reply()
            if cancelled is not None:
                cancelled.set()
                raise ModelRequestCancelled("fixture cancelled provider")
            if failure or len(self.requests) <= fail_attempts:
                raise RuntimeError("fixture provider unavailable")
            if kwargs["payload"].get("stream"):
                return [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
                    content='{"speech":"fixture valid reply","tool_call":null}'), finish_reason="stop")])]
            return SimpleNamespace()

        runtime._build_completion_kwargs = payload
        runtime._create_completion = create
        runtime._capture_runtime_error = lambda *_args, **_kwargs: None
        runtime._record_stream_cache_metrics = lambda *_args, **_kwargs: None
        engine = response_helpers.StreamFinalNonstreamFallbackTests()._build_engine(runtime)
        engine.memcore_manager = self.manager
        engine.host_tool_jobs = self.runtime
        engine.job_store = self.store
        # These fixtures contain no credentials. Avoid deployment-secret reads
        # while exercising the production trace shaping and request boundary.
        engine._sanitize_tool_trace_text = lambda text: text
        engine._max_tool_rounds = lambda **_: 0
        engine._tool_round_warning_remaining = lambda **_: 0
        engine._prepare_tool_round_decisions = lambda **kwargs: (kwargs["final_output"], [], [])
        engine._build_tool_round_extra_context = lambda **kwargs: kwargs["turn_extra_user_context"]
        # The wire request and all persistence/receipt boundaries are real;
        # persona/prompt rendering and the remote provider are isolated seams.
        def context(**kwargs):
            projection = self.projection(engine)
            return {"system_prompt": "fixture system", "user_prompt": engine._provider_message_text(projection["current_message"]["content"]),
                "fallback": {"speech": "fixture fallback", "tool_call": None}, "visual_defaults": {},
                "debug_enabled": False, "allow_tool_call": True, "native_tools": native_tools or [], "native_tool_choice": "auto",
                "system_extra_blocks": [], "history_turns": projection["history_turns"],
                "post_user_turns": projection["active_turn_messages"], "prompt_audit_sections": [],
                "memcore_projection_read": projection}
        engine._prepare_final_response_context = context
        return engine

    def run_restored(self, *, mode="sync", **kwargs):
        engine = self.build_engine(**kwargs)
        turn = PreparedTurn(TurnIdentity(self.owner.session_id, self.owner.profile_user_id, "fixture", "", "", "", "trace"),
            TurnRequest(mode, {}, SimpleNamespace(effective_mode=ClientMode.QQ_TEXT), "finish the fixture task", 100,
                "", "", "", "", None), TurnPersistence(False, False, False, True, False, None, "qq_private"),
            TurnPromptState(None, "", "", [], SimpleNamespace(protocol="openai"), False, [], [], [], [], [],
                {"current_user_source_id": self.source_id, "turn_id": self.turn_id}),
            TurnMemoryState({}, self.source_id, self.turn_id, None), TurnDiagnostics({}, {}, {}, {}, {}))
        return exhaust(run_turn_rounds(engine, turn))

    def assert_consumed(self, job):
        saved = self.store.get(job.job_id, owner=job.owner)
        self.assertEqual(saved.model_consumed_by, self.turn_id)
        self.assertEqual(saved.completion_status, "delivered")
        self.assertEqual(saved.delivery_receipt, {})

    def assert_unread(self, job):
        saved = self.store.get(job.job_id, owner=job.owner)
        self.assertEqual(saved.model_consumed_by, "")
        self.assertEqual(saved.completion_status, "pending")

    def test_frozen_terminal_native_result_rebuilds_pending_receipt_after_restart(self):
        job, result = self.managed_result()
        self.record_result(job, result)
        self.freeze_before_restart()
        self.restart()
        _, outcome = self.run_restored(before_reply=lambda: self.assert_unread(job))
        self.assertEqual(outcome.state.final_output["speech"], "fixture valid reply")
        self.assertEqual(len(self.requests), 1)
        self.assertIn(result.followup_context, json.dumps(self.requests[0]))
        self.assert_consumed(job)
        self.assertFalse(self.published)
        self.assertEqual(self.runtime.recover(), 0)
        self.assertTrue(self.runner.wait_idle(lane="host-job-completions", timeout=3))
        self.assertFalse(self.published)


    def test_only_visible_terminal_results_are_consumed_and_unread_completion_still_notifies(self):
        visible, result = self.managed_result(1)
        unread, _ = self.managed_result(2)
        self.record_result(visible, result)
        self.restart()
        self.run_restored(before_reply=lambda: (self.assert_unread(visible), self.assert_unread(unread)))
        self.assert_consumed(visible)
        self.assert_unread(unread)
        self.assertEqual(self.runtime.recover(), 1)
        self.assertTrue(self.runner.wait_idle(lane="host-job-completions", timeout=3))
        self.assertEqual([job.job_id for job in self.published], [unread.job_id])
        self.assert_unread(unread)

    def test_old_accepted_result_cannot_consume_a_job_that_finished_before_restart(self):
        job, _ = self.managed_result()
        accepted = _job_accepted_result(job.capability_id, job_id=job.job_id,
            duplicate=False, job_status="running")
        self.record_result(job, accepted)
        self.freeze_before_restart()
        self.restart()
        self.run_restored()
        self.assert_unread(job)
        self.assertEqual(self.runtime.recover(), 1)
        self.assertTrue(self.runner.wait_idle(lane="host-job-completions", timeout=3))
        self.assertEqual([item.job_id for item in self.published], [job.job_id])

    def test_frozen_but_not_in_actual_request_does_not_consume(self):
        job, result = self.managed_result()
        self.record_result(job, result)
        self.freeze_before_restart()
        self.restart()
        _, outcome = self.run_restored(omit_results=True)
        self.assertEqual(outcome.state.final_output["speech"], "fixture valid reply")
        self.assertNotIn(result.followup_context, json.dumps(self.requests))
        self.assert_unread(job)

    def test_failed_provider_request_releases_result_without_confirming_model_receipt(self):
        job, result = self.managed_result()
        self.record_result(job, result)
        self.freeze_before_restart()
        self.restart()
        _, outcome = self.run_restored(failure=True)
        self.assertTrue(outcome.state.final_output["_transient_final_failure"])
        self.assert_unread(job)
        self.assertTrue(self.runner.wait_idle(lane="host-job-completions", timeout=3))
        self.assertEqual([item.job_id for item in self.published], [job.job_id])
        self.assert_unread(job)

    def test_successful_retry_that_omits_prior_failed_input_cannot_consume_it(self):
        job, result = self.managed_result()
        self.record_result(job, result)
        self.freeze_before_restart()
        self.restart()
        _, outcome = self.run_restored(fail_attempts=1, omit_after_first=True)
        self.assertEqual(outcome.state.final_output["speech"], "fixture valid reply")
        self.assertEqual(len(self.requests), 2)
        self.assertIn(result.followup_context, json.dumps(self.requests[0]))
        self.assertNotIn(result.followup_context, json.dumps(self.requests[1]))
        self.assert_unread(job)

    def test_changed_result_body_is_not_confirmed_from_a_degraded_freeze(self):
        job, result = self.managed_result()
        self.record_result(job, result)
        self.freeze_before_restart()
        self.restart()
        _, outcome = self.run_restored(change_result=True)
        self.assertEqual(outcome.state.final_output["speech"], "fixture valid reply")
        self.assertEqual(outcome.state.final_output["_memcore_failure"]["detail"], "projection_immutable_conflict")
        self.assert_unread(job)

    def test_status_query_page_restores_receipt_without_consuming_output_pages(self):
        job, _ = self.managed_result(text="Q" * 15000 + "TAIL-END")
        page = self.runtime.task_control(tool_type="exec_status", call={"run_id": job.job_id}, context=self.context)
        self.assertFalse(page.followup_envelope.complete)
        cursor = page.followup_envelope.continuation["cursor"]
        self.record_result(job, page, name="exec_status", call_id="fixture-status-call", arguments={"run_id": job.job_id})
        self.freeze_before_restart()
        self.restart()
        self.run_restored()
        self.assert_consumed(job)
        tail = self.runtime.task_control(tool_type="exec_status", call={"run_id": job.job_id, "cursor": cursor},
            context=self.context)
        self.assertIn("TAIL-END", tail.followup_context)
        self.assertTrue(tail.followup_envelope.complete)
        self.assertEqual(self.runtime.recover(), 0)

    def test_boundary_event_restores_receipt_and_native_overlap_is_deduplicated(self):
        first, result = self.managed_result(1)
        second, _ = self.managed_result(2)
        self.record_result(first, result)
        for job in (first, second):
            recorded = self.manager.append_standalone_event(
                {"event_type": "task.work.completed", "source": "host.jobs", "fields": {"result": job.result_summary}},
                source_id=job.completion_event_id + ":boundary", turn_id=self.turn_id, timestamp=102, **self.scope)
            self.assertTrue(recorded["ok"], recorded)
        self.freeze_before_restart()
        self.restart()
        with (patch.object(self.store, "consume_model_completions", wraps=self.store.consume_model_completions) as consume,
              patch.object(self.store, "pending_completions", side_effect=AssertionError("no whole job-library scan"))):
            self.run_restored()
        self.assertEqual(consume.call_count, 1)
        self.assertEqual({job.job_id for job in consume.call_args.args[0]}, {first.job_id, second.job_id})
        self.assert_consumed(first)
        self.assert_consumed(second)

    def test_query_without_live_turn_owner_cannot_persist_receipt(self):
        job, _ = self.managed_result()
        self.restart()
        self.assertFalse(self.runtime.completion_work.query(job, consumed_by=self.turn_id))
        self.assert_unread(job)

    def test_cancelled_model_cannot_commit_the_observed_frozen_input(self):
        job, result = self.managed_result()
        self.record_result(job, result)
        self.freeze_before_restart()
        self.restart()
        cancelled = threading.Event()
        with cancellation_scope(cancelled.is_set):
            _, outcome = self.run_restored(cancelled=cancelled)
        self.assertTrue(outcome.stopped_output)
        self.assert_unread(job)

    def test_streaming_valid_model_reply_confirms_the_same_restored_request(self):
        job, result = self.managed_result()
        self.record_result(job, result)
        self.freeze_before_restart()
        self.restart()
        events, outcome = self.run_restored(mode="stream", before_reply=lambda: self.assert_unread(job))
        self.assertEqual(outcome.state.final_output["speech"], "fixture valid reply")
        self.assertTrue(any(event["type"].startswith("speech_") for event in events))
        self.assertEqual(len(self.requests), 1)
        self.assert_consumed(job)

    def test_anthropic_block_results_restore_the_same_receipt(self):
        self.profile = "anthropic"
        job, result = self.managed_result()
        self.record_result(job, result)
        self.freeze_before_restart()
        self.restart()
        self.run_restored()
        self.assertIn('"tool_result"', json.dumps(self.requests[0]))
        self.assert_consumed(job)

    def test_provider_alias_resolves_the_original_canonical_job(self):
        job, result = self.managed_result()
        self.record_result(job, result, name="project_inspect_alias")
        self.restart()
        self.run_restored(native_tools=[{"_akane_capability_id": "project_inspect",
            "type": "function", "function": {"name": "project_inspect_alias"}}])
        self.assert_consumed(job)

    def restored_generic_result(self, *, name="capability_invoke", native_tools=None):
        job, result = self.managed_result()
        self.record_result(job, result, name=name, arguments={
            "capability_id": "project_inspect", "arguments": {"value": "fixture"}})
        self.freeze_before_restart()
        self.restart()
        self.run_restored(native_tools=native_tools, before_reply=lambda: self.assert_unread(job))
        self.assert_consumed(job)
        self.assertEqual(self.runtime.recover(), 0)

    def test_generic_invocation_restores_the_routed_job_after_restart(self):
        self.restored_generic_result()

    def test_anthropic_generic_invocation_restores_the_routed_job(self):
        self.profile = "anthropic"
        self.restored_generic_result()

    def test_generic_provider_alias_restores_the_routed_job(self):
        self.restored_generic_result(name="invoke_alias", native_tools=[{
            "_akane_capability_id": "capability_invoke", "type": "function",
            "function": {"name": "invoke_alias"}}])

    def test_generic_status_query_restores_a_terminal_result(self):
        job, _ = self.managed_result(text="Q" * 15000 + "TAIL-END")
        page = self.runtime.task_control(tool_type="exec_status", call={"run_id": job.job_id}, context=self.context)
        self.assertFalse(page.followup_envelope.complete)
        cursor = page.followup_envelope.continuation["cursor"]
        self.record_result(job, page, name="capability_invoke", call_id="fixture-wrapped-status", arguments={
            "capability_id": "exec_status", "arguments": {"run_id": job.job_id}})
        self.freeze_before_restart()
        self.restart()
        self.run_restored()
        self.assert_consumed(job)
        tail = self.runtime.task_control(tool_type="exec_status", call={"run_id": job.job_id, "cursor": cursor},
            context=self.context)
        self.assertIn("TAIL-END", tail.followup_context)

    def test_result_restored_as_prior_turn_history_receives_current_turn_confirmation(self):
        job, result = self.managed_result()
        self.record_result(job, result)
        self.freeze_before_restart()
        completed = self.manager.complete_input_turn(turn_id=self.turn_id,
            assistant_record={"source_id": "restart-final", "content": "prior fixture reply", "timestamp": 102},
            memory_metadata={}, provider_output_raw='{"speech":"prior fixture reply"}',
            provider_profile=self.profile, provider_projection={"role": "assistant", "content": "prior fixture reply"}, **self.scope)
        self.assertTrue(completed["ok"], completed)
        self.source_id = "restart-next-input"
        opened = self.manager.begin_input_turn(
            {"source_id": self.source_id, "content": "continue fixture", "timestamp": 103}, **self.scope)
        self.assertTrue(opened["ok"], opened)
        self.turn_id = opened["turn_id"]
        self.restart()
        self.run_restored()
        self.assert_consumed(job)

    def test_registered_scheduler_failure_remains_unread_until_the_model_receives_it(self):
        self.runner.close()
        handler = BusinessTool("project_inspect")
        result = self.runtime.invoke(handler=handler, capability_id=handler.tool_type, invocation_id="fixture-schedule-failed",
            call={"type": handler.tool_type, "value": "fixture"}, context=self.context,
            dispatch=lambda context, call, handler: handler.execute(call=call, context=context))
        job = self.store.get_by_key(owner=self.owner, capability_source="tool", capability_id=handler.tool_type,
            idempotency_key="fixture-schedule-failed")
        self.assertEqual(job.status, "failed")
        self.assertEqual(handler.calls, [])
        self.assert_unread(job)
        self.record_result(job, result)
        self.freeze_before_restart()
        self.restart()
        self.run_restored(before_reply=lambda: self.assert_unread(job))
        self.assert_consumed(job)

    def manual_job(self, number, **changes):
        arguments = dict(owner=self.owner, capability_source="tool", capability_id="project_inspect", payload={},
            idempotency_key=f"manual-call-{number}", argument_fingerprint=f"manual-args-{number}",
            character_pack_id="fixture", channel="qq_text", delivery_target="fixture-ref", turn_id=self.turn_id,
            tool_call_id=f"manual-call-{number}", completion_mode="agent", memory_mode="timeline")
        arguments.update(changes)
        created = self.store.create(**arguments)
        self.assertTrue(created["ok"], created)
        return self.store.get(created["job_id"], owner=arguments["owner"])

    def test_successful_reply_does_not_consume_foreign_or_revoked_job_evidence(self):
        jobs = []
        changes = [{"owner": HostJobOwner("another-user", self.owner.session_id)},
            {"character_pack_id": "another-character"}, {"channel": "desktop_pet"},
            {"payload": {"task_scope": {"task_id": "subagent-task"}}}, {}]
        for number, change in enumerate(changes):
            job = self.manual_job(number, **change)
            claim = self.store.claim(job.job_id, worker_id="fixture")
            result = ToolExecutionResult(tool_type=job.capability_id, followup_context=f"isolated-result-{number}")
            self.assertTrue(self.store.succeed(job.job_id, claim_token=claim["claim_token"],
                result_summary=result.followup_context)["ok"])
            job = self.store.get(job.job_id, owner=job.owner)
            if number == 4:
                self.store.revoke_job_capability(job.job_id, owner=job.owner)
            jobs.append(job)
            self.record_result(job, result)
        self.restart()
        self.run_restored()
        for job in jobs:
            self.assert_unread(job)

    def test_failed_and_cancelled_business_results_are_not_consumed_before_a_valid_reply(self):
        jobs = []
        for number, status in enumerate(("failed", "cancelled")):
            job = self.manual_job(number)
            claim = self.store.claim(job.job_id, worker_id="fixture")
            text = f"business result really {status}"
            if status == "failed":
                settled = self.store.fail(job.job_id, claim_token=claim["claim_token"], retryable=False,
                    error="fixture_failure", result_summary=text)
            else:
                self.store.request_cancel(job.job_id, owner=job.owner)
                settled = self.store.confirm_cancelled(job.job_id, claim_token=claim["claim_token"], result_summary=text)
            self.assertTrue(settled["ok"], settled)
            job = self.store.get(job.job_id, owner=job.owner)
            jobs.append(job)
            self.record_result(job, ToolExecutionResult(tool_type=job.capability_id, followup_context=text))
            self.assert_unread(job)
        self.restart()
        self.run_restored(before_reply=lambda: [self.assert_unread(job) for job in jobs])
        for job in jobs:
            self.assert_consumed(job)


    def test_inflight_completion_is_not_consumed_until_its_next_actual_request(self):
        job = self.manual_job(1)
        accepted = _job_accepted_result(job.capability_id, job_id=job.job_id, duplicate=False, job_status="running")
        self.record_result(job, accepted)
        self.restart()
        finished = []
        def finish_during_response():
            self.assertEqual(self.store.get(job.job_id, owner=job.owner).model_consumed_by, "")
            if finished:
                return
            claim = self.store.claim(job.job_id, worker_id="fixture")
            self.assertTrue(self.store.succeed(job.job_id, claim_token=claim["claim_token"],
                result_summary="inflight-terminal-END")["ok"])
            finished.append(job.job_id)
            self.assertTrue(self.runtime.completion_work.hold(self.store.get(job.job_id, owner=self.owner)))
        self.run_restored(before_reply=finish_during_response)
        self.assertEqual(len(self.requests), 2)
        self.assertNotIn("inflight-terminal-END", json.dumps(self.requests[0]))
        self.assertIn("inflight-terminal-END", json.dumps(self.requests[1]))
        self.assert_consumed(job)

    def test_real_shell_status_result_restores_after_provider_and_registry_restart(self):
        from companion_v01.execution_local import TrustedLocalExecutor
        from companion_v01.execution_wait import batch_wait_budget
        from companion_v01.host_execution_jobs import HostExecutionJobRuntime
        from companion_v01.tool_handlers.execution import ExecRunToolHandler, ExecStatusToolHandler
        workspace = self.root / "shell-workspace"
        workspace.mkdir()
        self.provider = TrustedLocalExecutor(workspace_root=workspace, run_log_dir=self.root / "shell-runlogs")
        self.addCleanup(boundary_helpers.RealExecutionCompletionTests.shutdown_provider, self)
        bridge = HostExecutionJobRuntime(store=self.store, conversation_ref_issuer=lambda _: "fixture-ref",
            completion_work=self.runtime.completion_work, completion_publisher=self.runtime.publish_completion)
        bridge.bind_provider(self.provider)
        handler = ExecRunToolHandler(execution_provider=self.provider)
        status = ExecStatusToolHandler(execution_provider=self.provider)
        handler.bind_job_runtime(bridge)
        status.bind_job_runtime(bridge)
        context = replace(self.context, invocation_id="fixture-shell-call")
        with batch_wait_budget(0):
            started = handler._execute_command(self.provider, {"type": "exec_run", "command":
                boundary_helpers.python_command('import time; time.sleep(0.15); print("real-shell-terminal-END")')}, context)
        run_id = started.state_updates["capability_execution"]["run_id"]
        page = status.execute(call={"type": "exec_status", "run_id": run_id, "wait_seconds": 5}, context=context)
        self.assertEqual(page.state_updates["capability_execution"]["status"], "completed")
        deadline = time.monotonic() + 5
        while True:
            job = self.store.get_by_key(owner=self.owner, capability_source="execution", capability_id="exec_run", idempotency_key=run_id)
            if job.status == "succeeded" or time.monotonic() >= deadline:
                break
            time.sleep(.01)
        self.assertEqual(job.status, "succeeded")
        self.record_result(job, page, name="exec_status", call_id="fixture-shell-status", arguments={"run_id": run_id})
        self.freeze_before_restart()
        self.provider.request_shutdown()
        self.restart()
        self.run_restored(before_reply=lambda: self.assert_unread(job))
        self.assert_consumed(job)
        self.assertEqual(self.runtime.recover(), 0)


if __name__ == "__main__":
    unittest.main()
