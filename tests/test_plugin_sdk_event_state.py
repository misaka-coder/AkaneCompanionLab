"""Install the SDK-first state event example and exercise its real worker ports."""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from capcore import InvocationContext

from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.capability_registry import ExecutorBroker
from companion_v01.durable_session_queue import DurableSessionWorkQueue
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.extension_management import ExtensionManagementService, PluginSelectionStore
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_tool_jobs import HostToolJobRuntime
from companion_v01.plugin_agent_events import HostAgentEventRouter
from companion_v01.plugin_capability_calls import EnginePluginCapabilityProvider
from companion_v01.plugin_conversation_refs import PluginConversationReferenceAuthority
from companion_v01.plugin_generation_candidate import PluginGenerationCandidateBuilder
from companion_v01.plugin_generation_runtime import PluginGenerationRuntime
from companion_v01.plugin_installation import ManagedPluginArtifactStore
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.session_inbox import SessionInboxStore
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.turn_coordination import TurnCoordinator
from companion_v01.plugin_turn_intents import HostTurnResult
from tests.test_plugin_engine_bridge import EngineFacade
from tests.test_plugin_resources import services


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "examples" / "plugins" / "akane_sdk_event_state"
PLUGIN_ID = "example.sdk-event-state"


class InstalledSdkStateEventTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._close)
        root = Path(self.temporary.name)
        self.root = root
        self.refs = PluginConversationReferenceAuthority(root / "conversation.key", instance_id="state-test")
        self.store = SessionInboxStore(root / "inbox.db")
        self.queue = DurableSessionWorkQueue(self.store)
        self.coordinator = TurnCoordinator()
        self.turns = HostAgentEventRouter(self.refs.resolve)
        self.turns.bind_runtime(self.queue, self.coordinator)
        self.turn_requests = []

        async def handle(intent, resolved):
            self.turn_requests.append((intent, dict(resolved)))
            status = "completed" if intent.message.startswith("event:") else "queued"
            return HostTurnResult(True, status, delivery_status="queued")

        self.turns.register_channel("desktop_pet", handle)
        self.artifacts = ManagedPluginArtifactStore(root / "artifacts", instance_id="state-test", project_root=ROOT)
        self.selections = PluginSelectionStore(root / "selections.json", defaults=(), instance_id="state-test")
        self.runtime = PluginGenerationRuntime(
            (),
            candidate_builder=PluginGenerationCandidateBuilder(
                source_resolver=self.artifacts,
                project_root=ROOT,
                work_root=root / "generations",
            ),
        )
        self.runtime.bind_turn_router(self.turns)
        self.engine = EngineFacade(
            PluginCapabilityToolBridge(
                self.runtime,
                config_base_dir=root,
                conversation_ref_issuer=lambda context: self.refs.issue(context),
            )
        )
        self.engine.store, _, _ = services(root)
        self.engine.llm = Mock(side_effect=AssertionError("state example must not call a model directly"))
        self.timeline = []
        self.engine.record_plugin_timeline_event = (
            lambda payload: self.timeline.append(dict(payload)) or {"ok": True, "status": "recorded"}
        )
        self.runtime.bind_capability_provider(EnginePluginCapabilityProvider(self.engine))
        self.service = ExtensionManagementService(
            plugin_runtime=self.runtime,
            selection_store=self.selections,
            artifact_store=self.artifacts,
        )
        await self.runtime.start()
        self.runtime.bind_timeline_recorder(self.engine.record_plugin_timeline_event)

    async def _close(self) -> None:
        await self.runtime.stop()
        await self.queue.close()
        self.temporary.cleanup()

    async def _call(self, name: str, arguments: dict):
        handler = self.engine._resolve_tool_handlers()[name]
        execution = await asyncio.to_thread(
            handler.execute,
            call={"type": name, "arguments": arguments},
            context=ToolExecutionContext(
                "owner",
                "session",
                1,
                {},
                character_pack_id="reimu",
                client_mode="desktop_pet",
                result_consumer="program",
            ),
        )
        self.assertIsNotNone(execution.capability_result, execution)
        return execution.capability_result

    async def test_installation_worker_signed_event_state_and_explicit_turn_contract(self):
        staged = await self.service.stage_source(source_path=str(PROJECT))
        self.assertTrue(staged["ok"], staged)
        self.assertIn("event.emit", staged["permissions"])
        self.assertIn("event.subscribe", staged["permissions"])
        self.assertIn("agent.turn.request", staged["permissions"])
        subscriptions = staged["contribution_snapshot"]["event_subscriptions"]
        self.assertEqual(
            {item["subscription_id"] for item in subscriptions},
            {
                f"{PLUGIN_ID}.capture",
                f"{PLUGIN_ID}.timeline",
                f"{PLUGIN_ID}.decide",
            },
        )
        installed = await self.service.install_stage(
            stage_id=staged["stage_id"],
            approved_permissions=staged["permissions"],
        )
        self.assertTrue(installed["ok"], installed)
        descriptor = self.runtime.capability_descriptors[f"{PLUGIN_ID}.checkpoint"]
        self.assertEqual(descriptor.raw["execution_class"], "long_task")

        bound = await self._call(f"{PLUGIN_ID}.bind", {})
        self.assertEqual(bound.value["status"], "bound")
        self.assertEqual(
            {item["subscription_id"] for item in bound.value["bindings"]},
            {
                f"{PLUGIN_ID}.capture",
                f"{PLUGIN_ID}.timeline",
                f"{PLUGIN_ID}.decide",
            },
        )
        observation = await self._call(
            f"{PLUGIN_ID}.observe",
            {"state_version": 7, "state": {"mode": "ready", "count": 0}},
        )
        self.assertEqual(observation.value["status"], "observed")
        first_observation_version = observation.value["version"]
        newer = await self._call(
            f"{PLUGIN_ID}.observe",
            {"state_version": 8, "state": {"mode": "ready", "count": 1}},
        )
        self.assertGreater(newer.value["version"], first_observation_version)

        stale = await self._call(f"{PLUGIN_ID}.decide", {"observation_version": first_observation_version})
        self.assertEqual(stale.value["status"], "queued")
        with self.assertRaisesRegex(Exception, "observation_version_stale"):
            self.turns.begin(stale.value["request_id"], "stale-turn")
        stale_terminal = await self._call(
            f"{PLUGIN_ID}.turn_status",
            {"request_id": stale.value["request_id"]},
        )
        self.assertTrue(stale_terminal.is_error)
        self.assertEqual(stale_terminal.reason, "observation_version_stale")
        self.assertEqual(stale_terminal.content["status"], "rejected")
        self.assertEqual(stale_terminal.content["reason"], "observation_version_stale")

        accepted = await self._call(f"{PLUGIN_ID}.decide", {"observation_version": newer.value["version"]})
        self.assertEqual(accepted.value["status"], "queued")
        self.turns.begin(accepted.value["request_id"], "accepted-turn")
        self.turns.finish(accepted.value["request_id"], model_status="completed", delivery_status="queued")
        accepted_terminal = await self._call(
            f"{PLUGIN_ID}.turn_status",
            {"request_id": accepted.value["request_id"]},
        )
        self.assertEqual(accepted_terminal.value["status"], "completed")
        self.assertEqual(accepted_terminal.value["model_status"], "completed")
        # Both explicit requests enter the normal queue; the stale one is
        # rejected only when the host freezes observations at decision time.
        self.assertEqual(len(self.turn_requests), 2)
        self.assertEqual(self.turn_requests[0][1]["session"], "session")

        published = await self._call(
            f"{PLUGIN_ID}.publish",
            {"state_version": 9, "state": {"mode": "running", "count": 2}},
        )
        self.assertEqual(published.value["status"], "accepted")
        publication = published.value
        async with asyncio.timeout(10):
            while not publication.get("complete"):
                await asyncio.sleep(0.01)
                status_result = (
                    await self._call(
                        f"{PLUGIN_ID}.status",
                        {"dispatch_id": publication["dispatch_id"]},
                    )
                )
                publication = status_result.value

        # The capture handler returns the complete host-signed identity and the
        # same publication's timeline subscriber records exactly one durable fact.
        capture = next(
            item for item in publication["deliveries"]
            if item["subscription_id"] == f"{PLUGIN_ID}.capture"
        )
        event = capture["value"]["event"]
        self.assertEqual(event["source"], PLUGIN_ID)
        self.assertTrue(event["event_id"])
        self.assertTrue(event["scope"])
        self.assertGreaterEqual(event["version"], 1)
        self.assertGreater(event["received_at_ms"], 0)
        self.assertEqual(capture["value"]["data"]["state_version"], 9)
        self.assertEqual(capture["value"]["observation"]["status"], "observed")
        self.assertEqual(len(self.timeline), 1)
        self.assertEqual(len(self.turn_requests), 2)
        self.assertEqual(self.timeline[0]["event"]["event_type"], "example.state.changed")
        self.assertEqual(self.timeline[0]["event"]["fields"]["data"]["state_version"], 9)
        self.engine.llm.assert_not_called()

    async def test_real_host_tool_job_checkpoint_pause_resume_keeps_job_identity(self):
        """A queued SDK long task pauses, resumes, and settles with its real result."""
        staged = await self.service.stage_source(source_path=str(PROJECT))
        self.assertTrue(staged["ok"], staged)
        installed = await self.service.install_stage(
            stage_id=staged["stage_id"],
            approved_permissions=staged["permissions"],
        )
        self.assertTrue(installed["ok"], installed)

        # The small EngineFacade used by this test intentionally omits the
        # production engine's unrelated mode/profile services. Supply only
        # the real executor and result-status hooks required by Job runtime.
        self.engine.executor_broker = ExecutorBroker(None)
        self.engine._tool_hook_result_status = AkaneMemoryEngine._tool_hook_result_status
        self.engine._resolve_client_protocol_context = lambda payload: SimpleNamespace(
            effective_mode=payload.get("client_mode"),
        )

        runner = BackgroundTaskRunner({"host-jobs": 1, "host-job-completions": 1})
        release = threading.Event()
        entered = threading.Event()
        completed = []
        store = HostJobStore(self.root / "jobs.db")
        runtime = HostToolJobRuntime(
            engine=self.engine,
            store=store,
            background_tasks=runner,
            conversation_ref_issuer=lambda _context: "conversation-ref",
            terminal_callback=lambda job: completed.append(job) or True,
        )
        owner = HostJobOwner("owner", "session")
        checkpoint_id = f"{PLUGIN_ID}.checkpoint"
        context = ToolExecutionContext(
            "owner",
            "session",
            1,
            {},
            character_pack_id="reimu",
            client_mode="desktop_pet",
            result_consumer="program",
        )
        try:
            def occupy_lane():
                entered.set()
                release.wait(timeout=5)

            runner.submit(lane="host-jobs", name="hold", fn=occupy_lane)
            self.assertTrue(entered.wait(timeout=2))

            accepted = runtime.submit(
                capability_id=checkpoint_id,
                invocation_id="checkpoint-1",
                call={
                    "type": checkpoint_id,
                    "arguments": {"state_version": 11, "steps": 32},
                },
                context=context,
                handler=self.engine._resolve_tool_handlers()[checkpoint_id],
            )
            self.assertEqual(accepted.stream_events[0]["type"], "background_job_accepted")
            job_id = accepted.stream_events[0]["job_id"]
            queued = store.get(job_id, owner=owner)
            self.assertIsNotNone(queued)
            self.assertEqual(queued.status, "queued")

            paused = store.control(job_id, owner=owner, action="pause")
            self.assertEqual(paused["status"], "paused")
            self.assertEqual(store.get(job_id, owner=owner).job_id, job_id)
            resumed = store.control(job_id, owner=owner, action="resume")
            self.assertEqual(resumed["status"], "queued")
            self.assertEqual(store.get(job_id, owner=owner).job_id, job_id)

            release.set()
            self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=5))
            self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-job-completions", timeout=5))

            finished = store.get(job_id, owner=owner)
            self.assertEqual(finished.status, "succeeded")
            self.assertEqual(finished.control_state, "stopped")
            self.assertEqual(finished.result["capability_result"]["content"]["state_version"], 11)
            self.assertEqual(finished.result["capability_result"]["content"]["steps"], 32)
            self.assertEqual([job.job_id for job in completed], [job_id])
        finally:
            release.set()
            runner.close(timeout=5)


if __name__ == "__main__":
    unittest.main()
