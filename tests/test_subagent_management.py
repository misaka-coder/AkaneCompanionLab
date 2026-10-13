from dataclasses import replace
import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from companion_v01.host_jobs import HostJobStore, HostJobOwner
from companion_v01.host_subagent_jobs import HostSubagentJobRuntime
from companion_v01.subagent_runtime import SubagentRunResult
from companion_v01.memory_read_policy import MemoryReadPolicy
from companion_v01.memcore_integration.timeline import MemcoreTimelineToolService
from companion_v01.tool_handlers.core import ToolExecutionContext, TaskExecutionScope
from companion_v01.tool_handlers.memory import OpenMemoryToolHandler
from companion_v01.tool_handlers.subagent import ManageSubagentsToolHandler
from companion_v01.store import MemoryStore
from companion_v01.tool_invocation import NATIVE_OPENAI, NATIVE_TOOL_CALL_FIELD, TOOL_SOURCE_FIELD, TOOL_INVOCATION_ID_FIELD
from tests import test_subagent_engine as fixtures
from tests import test_host_subagent_jobs as job_fixtures


class SubagentManagementTests(unittest.TestCase):
    def setUp(self):
        fixtures.SubagentEngineTests.setUp(self)
        self.jobs = HostJobStore(self.root / "jobs.db")
        self.owner = HostJobOwner("alice", "parent")
        self.runtime = HostSubagentJobRuntime(store=self.jobs, providers=job_fixtures._providers(
            lambda request, **kwargs: SubagentRunResult("succeeded", request.child_session_id, summary="Verified report.")),
            provider_name="in_process", background_tasks=job_fixtures._HoldingBackgroundTasks(),
            memory_deleter=self.engine.memcore_manager.forget_subagent)

    def submit(self, key="first", **overrides):
        return self.runtime.submit(**{**dict(owner=self.owner, task="Audit.", working_directory=str(self.root),
            conversation_ref="ref", tool_call_id=key, character_pack_id="char", channel="qq_text",
            execution_context={"actor_stable_id": "qq:alice"}), **overrides})

    def completed(self, key="first", **overrides):
        admitted = self.submit(key, **overrides)
        self.assertTrue(admitted.get("ok"), admitted)
        self.runtime._run(admitted["job_id"])
        return admitted

    def scope(self):
        return dict(owner=self.owner, character_pack_id="char", channel="qq_text", actor_id="qq:alice")

    def consume(self, admitted):
        job = self.jobs.get(admitted["job_id"], owner=self.owner)
        self.assertTrue(self.jobs.consume_model_completion(job, consumed_by="parent-next")["ok"])

    def wait_memory(self):
        manager = self.engine.memcore_manager
        with manager._jobs_condition:
            self.assertTrue(manager._jobs_condition.wait_for(lambda: not manager._background_futures, timeout=3))

    def test_stable_identity_new_execution_and_paged_owner_scoped_list(self):
        first = self.completed()
        second = self.completed("second", agent_id=first["agent_id"], task="Recheck.")
        self.assertEqual(first["agent_id"], second["agent_id"])
        self.assertNotEqual(first["job_id"], second["job_id"])
        third = self.completed("third")
        self.completed("other-actor", execution_context={"actor_stable_id": "qq:bob"})
        page = self.jobs.list_subagents(limit=1, **self.scope())
        self.assertEqual(page["agents"][0]["agent_id"], third["agent_id"])
        next_page = self.jobs.list_subagents(cursor=page["next_cursor"], limit=1, **self.scope())
        self.assertEqual(next_page["agents"][0]["job_id"], second["job_id"])
        self.assertIsNone(next_page["next_cursor"])
        self.assertFalse(self.submit("unauthorized", agent_id=first["agent_id"], execution_context={"actor_stable_id": "qq:bob"})["ok"])
        self.assertFalse(self.submit("other-character", agent_id=first["agent_id"], character_pack_id="other")["ok"])

    def test_running_child_steers_once_and_sealed_execution_does_not_accept_lost_input(self):
        first = self.submit()
        steer = self.submit("steer", agent_id=first["agent_id"], task="Check failures too.")
        duplicate = self.submit("steer", agent_id=first["agent_id"], task="Check failures too.")
        self.assertEqual(steer["status"], "steered")
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(len(self.jobs.subagent_inputs(first["job_id"], owner=self.owner)), 1)
        self.jobs.acknowledge_subagent_inputs(first["job_id"], owner=self.owner, source_ids=["steer"])
        self.assertTrue(self.jobs.seal_subagent_inputs(first["job_id"], owner=self.owner))
        denied = self.submit("late", agent_id=first["agent_id"], task="Too late to join.")
        self.assertEqual(denied["reason"], "subagent_execution_finishing")
        self.assertEqual(len(self.runtime.background_tasks.calls), 1)

    def test_running_workspace_stays_fixed_but_a_new_run_can_select_another_project(self):
        first = self.submit()
        other = str(self.root / "other-project")
        denied = self.submit("steer-other", agent_id=first["agent_id"], working_directory=other)
        self.assertEqual(denied["reason"], "subagent_workspace_changed")
        self.runtime._run(first["job_id"])
        resumed = self.submit("related-project", agent_id=first["agent_id"], working_directory=other)
        self.assertTrue(resumed.get("ok"), resumed)
        self.assertEqual(self.jobs.get(resumed["job_id"], owner=self.owner).payload["working_directory"], other)

    def test_large_unicode_listing_pages_fit_the_tool_result_without_losing_children(self):
        expected = set()
        for index in range(51):
            admitted = self.submit("large-" + str(index), model="型" * 200, label="标" * 80)
            expected.add(admitted["agent_id"])
            claimed = self.jobs.claim(admitted["job_id"], worker_id="fixture")
            self.jobs.succeed(admitted["job_id"], claim_token=claimed["claim_token"], result_summary="结" * 4000)
        received, cursor = set(), 0
        while True:
            page = self.jobs.list_subagents(limit=50, cursor=cursor, **self.scope())
            self.assertLess(len(json.dumps(page, ensure_ascii=False).encode("utf-8")), 65536)
            ids = {row["agent_id"] for row in page["agents"]}
            self.assertTrue(ids)
            self.assertFalse(ids.intersection(received))
            received.update(ids)
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(received, expected)

    def test_deletion_requires_stop_and_report_consumption_and_preserves_files_and_jobs(self):
        first = self.submit()
        self.assertEqual(self.runtime.delete(first["agent_id"], **self.scope())["reason"], "subagent_stop_before_delete")
        self.runtime._run(first["job_id"])
        self.assertEqual(self.runtime.delete(first["agent_id"], **self.scope())["reason"], "subagent_report_not_consumed")
        agent = first["agent_id"]
        manager = self.engine.memcore_manager
        manager.append_standalone_event({"event_type": "audit", "source": "host", "fields": {"result": "task evidence"}},
            profile_user_id=agent, session_id=agent, character_pack_id="", source_id="task-evidence")
        manager.append_standalone_event({"event_type": "audit", "source": "host", "fields": {"result": "parent evidence"}},
            profile_user_id="alice", session_id="parent", character_pack_id="char", source_id="parent-evidence")
        artifact = self.root / "report.md"
        artifact.write_text("Verified report.", encoding="utf-8")
        self.consume(first)
        self.wait_memory()
        deleted = self.runtime.delete(agent, **self.scope())
        self.assertTrue(deleted["ok"], deleted)
        self.assertEqual(self.jobs.get(first["job_id"], owner=self.owner).result_summary, "Verified report.")
        self.assertTrue(artifact.exists())
        self.assertEqual(self.jobs.list_subagents(**self.scope())["agents"], [])
        self.assertEqual(self.submit("recreate", agent_id=agent)["reason"], "subagent_deleted")
        parent = manager.open_memory(profile_user_id="alice", session_id="parent", character_pack_id="char",
                                     arguments={"memory_id": "parent-evidence", "view": "content"})
        self.assertTrue(parent["ok"], parent)
        self.assertTrue(self.runtime.delete(agent, **self.scope())["ok"])
        # A recreated Host Job store reads the same tombstone.
        reopened = HostJobStore(self.root / "jobs.db")
        self.assertTrue(reopened.subagent_record(agent, **self.scope())["deleted"])

    def test_partial_index_deletion_keeps_retry_ids_until_success(self):
        admitted = self.completed()
        self.consume(admitted)
        manager = self.engine.memcore_manager
        agent = admitted["agent_id"]
        manager.append_standalone_event({"event_type": "audit", "source": "host", "fields": {"result": "evidence"}},
            profile_user_id=agent, session_id=agent, character_pack_id="", source_id="index-evidence")
        self.wait_memory()
        with patch.object(manager._index, "delete", side_effect=RuntimeError("fixture failure")):
            failed = self.runtime.delete(agent, **self.scope())
        self.assertEqual(failed["status"], "partial")
        self.assertNotIn("retry_source_ids", failed)
        self.assertIn("index-evidence", self.jobs.subagent_delete_ids(agent, owner=self.owner))
        with patch.object(manager._index, "delete", wraps=manager._index.delete) as deleted:
            success = self.runtime.delete(agent, **self.scope())
        self.assertTrue(success["ok"], success)
        self.assertIn("index-evidence", deleted.call_args_list[0].args[0])
        self.assertEqual(self.jobs.subagent_delete_ids(agent, owner=self.owner), [])

    def test_active_compression_delete_is_nonblocking_and_cannot_repopulate_history(self):
        admitted = self.completed()
        self.consume(admitted)
        manager = self.engine.memcore_manager
        agent = admitted["agent_id"]
        entered, release = threading.Event(), threading.Event()
        def summarize(**kwargs):
            entered.set()
            release.wait(3)
            return {"diary_summary": "Verified task history.", "importance": .5, "key_events": [], "core_facts": []}
        self.addCleanup(release.set)
        manager._memory_config = replace(manager._memory_config, raw_token_trigger=1000)
        manager.bind_subagent_runtime(agent, SimpleNamespace(call_memcore_json=summarize))
        opened = manager.begin_input_turn({"source_id": "large-input", "content": "任务资料" * 2500, "timestamp": 1},
                                         profile_user_id=agent, session_id=agent)
        manager.complete_input_turn(turn_id=opened["turn_id"], assistant_record={"source_id": "large-result", "content": "Read."},
                                    memory_metadata=None, profile_user_id=agent, session_id=agent, provider_profile="openai_chat")
        manager.compact_due_background(profile_user_id=agent, session_id=agent, provider_profile="openai_chat")
        self.assertTrue(entered.wait(2))
        start = time.perf_counter()
        pending = self.runtime.delete(agent, **self.scope())
        self.assertEqual(pending["status"], "deleting")
        self.assertLess(time.perf_counter() - start, .2)
        # A slow summary in the child does not hold the shared DB/manager lock.
        start = time.perf_counter()
        parent = manager.begin_input_turn({"source_id": "parent-live", "content": "Are you there?"},
                                         profile_user_id="alice", session_id="parent", character_pack_id="char")
        self.assertTrue(parent["ok"], parent)
        self.assertLess(time.perf_counter() - start, .3)
        release.set()
        self.wait_memory()
        self.assertTrue(self.runtime.delete(agent, **self.scope())["ok"])

    def test_management_handler_reports_mixed_batch_without_deleting_unread_child(self):
        first, second = self.completed(), self.completed("second")
        self.consume(first)
        handler = ManageSubagentsToolHandler(runtime=self.runtime)
        self.wait_memory()
        context = ToolExecutionContext("alice", "parent", 1, {}, character_pack_id="char", client_mode="qq_text",
                                       request_context={"actor_stable_id": "qq:alice"})
        result = handler.execute(call={"type": "manage_subagents", "action": "delete",
            "agent_ids": [first["agent_id"], second["agent_id"]]}, context=context)
        report = json.loads(result.followup_context)
        self.assertFalse(report["ok"])
        self.assertTrue(report["results"][0]["ok"])
        self.assertEqual(report["results"][1]["reason"], "subagent_report_not_consumed")

    def test_child_memory_tool_can_reopen_own_card_but_cannot_read_parent(self):
        manager, agent = self.engine.memcore_manager, self.request.child_session_id
        manager.append_standalone_event({"event_type": "audit", "source": "host", "fields": {"result": "own evidence"}},
            profile_user_id=agent, session_id=agent, character_pack_id="", source_id="own-evidence")
        manager.append_standalone_event({"event_type": "audit", "source": "host", "fields": {"result": "private parent"}},
            profile_user_id="alice", session_id="parent", character_pack_id="", source_id="parent-private")
        policy = MemoryReadPolicy(store=MemoryStore(self.root / "policy"), bot_id="test", master_qq="123",
                                  enabled=lambda: True, resolve_origin=lambda ref: None, key=b"a" * 32)
        handler = OpenMemoryToolHandler(timeline_service=MemcoreTimelineToolService(memcore_manager=manager),
                                        read_policy_provider=lambda: policy)
        self.engine.tool_handlers["open_memory"] = handler
        self.engine.llm.responses = [fixtures.response({NATIVE_TOOL_CALL_FIELD: {"type": "open_memory", "memory_id": "own-evidence",
            "view": "content", TOOL_SOURCE_FIELD: NATIVE_OPENAI, TOOL_INVOCATION_ID_FIELD: "own-read"}}),
            fixtures.response({"status": "succeeded", "summary": "Own evidence read."})]
        result = self.driver(replace(self.request, allowed_tools=("open_memory",)), cancelled=lambda: False)
        self.assertEqual(result.status, "succeeded", result)
        history = self.engine.llm.requests[-1]["post_user_turns"]
        self.assertIn("own evidence", str(history))
        self.assertNotIn("private parent", str(history))
        context = ToolExecutionContext(agent, agent, 1, {}, client_mode="desktop_pet",
                                       execution_scope=TaskExecutionScope(str(self.root), agent))
        target, arguments, error = policy.prepare(context=context,
            call={"type": "open_memory", "conversation": "master", "memory_id": "parent-private"}, tool="open_memory")
        self.assertIsNotNone(error)

    def test_claim_heartbeat_extends_lease_without_restarting_job(self):
        now = [100.]
        store = HostJobStore(self.root / "leases.db", clock=lambda: now[0])
        created = store.create(owner=self.owner, capability_source="subagent", capability_id="spawn_subagent",
            payload={}, idempotency_key="once", argument_fingerprint="hash")
        claimed = store.claim(created["job_id"], worker_id="test", lease_seconds=300)
        now[0] = 350
        self.assertTrue(store.renew_claim(created["job_id"], claim_token=claimed["claim_token"], lease_seconds=300))
        now[0] = 450
        self.assertEqual(store.claim(created["job_id"], worker_id="other")["reason"], "host_job_not_claimable")
        self.assertEqual(store.get(created["job_id"], owner=self.owner).attempts, 1)
