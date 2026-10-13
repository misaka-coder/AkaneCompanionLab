"""Independent integration guards around the restarted-request receipt path."""
from __future__ import annotations

import threading
import unittest

from companion_v01.llm_runtime import ModelRequestCancelled
from companion_v01.turn_coordination import cancellation_scope
from tests import test_tool_completion_restart as fixtures


class RestartedCompletionRequestGuardTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ToolCompletionRestartTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def restored_result(self):
        fixture = self.fixture
        job, result = fixture.managed_result()
        fixture.record_result(job, result)
        fixture.freeze_before_restart()
        fixture.restart()
        return job

    def test_provider_failure_does_not_consume_restored_result(self):
        job = self.restored_result()
        fixture = self.fixture
        fixture.run_restored(failure=True)
        fixture.assert_unread(job)
        self.assertGreaterEqual(len(fixture.requests), 1)

    def test_cancelled_request_does_not_consume_restored_result(self):
        job = self.restored_result()
        stopped = threading.Event()
        with cancellation_scope(stopped.is_set):
            try:
                self.fixture.run_restored(cancelled=stopped)
            except ModelRequestCancelled:
                pass
        self.fixture.assert_unread(job)
        self.assertTrue(stopped.is_set())

    def test_omitted_result_is_not_acknowledged_from_context_alone(self):
        job = self.restored_result()
        self.fixture.run_restored(omit_results=True)
        self.fixture.assert_unread(job)

    def test_only_the_result_in_the_request_is_consumed_other_notice_survives(self):
        fixture = self.fixture
        observed, result = fixture.managed_result(1)
        unseen, _ = fixture.managed_result(2)
        fixture.record_result(observed, result)
        fixture.freeze_before_restart()
        fixture.restart()
        fixture.run_restored()
        fixture.assert_consumed(observed)
        fixture.assert_unread(unseen)
        self.assertEqual(fixture.runtime.recover(), 1)
        self.assertTrue(fixture.runner.wait_idle(lane="host-job-completions", timeout=3))
        self.assertEqual([job.job_id for job in fixture.published], [unseen.job_id])

    def test_request_content_without_owned_projection_cannot_restore_receipt(self):
        from companion_v01.host_tool_job_runtime.request_receipts import request_completion_jobs
        job = self.restored_result()
        fixture = self.fixture
        engine = fixture.build_engine()
        projection = fixture.projection(engine)
        work = fixture.runtime.completion_work.begin(owner=fixture.owner, character_pack_id="fixture",
            channel="qq_text", turn_id=fixture.turn_id, request_bound=True)
        self.addCleanup(fixture.runtime.completion_work.close, work)
        request = {"history_messages": [message["payload"] for message in projection["current_turn_messages"]]}
        self.assertEqual(request_completion_jobs(engine, work, {}, request), [])
        work.observe_request([])
        work.commit()
        fixture.assert_unread(job)

    def test_running_query_does_not_consume_a_job_that_finished_later(self):
        from companion_v01.tool_handlers.core import ToolExecutionResult
        fixture = self.fixture
        job, _ = fixture.managed_result()
        query_result = ToolExecutionResult(tool_type="exec_status",
            followup_context=f"job_id={job.job_id}; tool=project_inspect; status=running\nstill working")
        fixture.record_result(job, query_result, name="exec_status", call_id="running-status-query",
                              arguments={"run_id": job.job_id})
        fixture.freeze_before_restart()
        fixture.restart()
        fixture.run_restored()
        fixture.assert_unread(job)

    def test_generic_running_query_remains_unread(self):
        from companion_v01.tool_handlers.core import ToolExecutionResult
        fixture = self.fixture
        job, _ = fixture.managed_result()
        query_result = ToolExecutionResult(tool_type="exec_status",
            followup_context=f"job_id={job.job_id}; tool=project_inspect; status=running\nstill working")
        fixture.record_result(job, query_result, name="capability_invoke", call_id="wrapped-running-query",
            arguments={"capability_id": "exec_status", "arguments": {"run_id": job.job_id}})
        fixture.freeze_before_restart()
        fixture.restart()
        fixture.run_restored()
        fixture.assert_unread(job)

    def test_invalid_generic_arguments_do_not_resolve_a_job(self):
        fixture = self.fixture
        job, result = fixture.managed_result()
        fixture.record_result(job, result, name="capability_invoke",
            arguments={"capability_id": "project_inspect", "arguments": "invalid"})
        fixture.freeze_before_restart()
        fixture.restart()
        fixture.run_restored()
        fixture.assert_unread(job)


if __name__ == "__main__":
    unittest.main()
