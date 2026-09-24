"""Real installation and host behavior for the SDK turn-game example."""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.durable_session_queue import DurableSessionWorkQueue
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.extension_management import ExtensionManagementService, PluginSelectionStore
from companion_v01.host_jobs import HostJobStore
from companion_v01.plugin_agent_events import HostAgentEventRouter
from companion_v01.plugin_capability_calls import EnginePluginCapabilityProvider
from companion_v01.plugin_conversation_refs import PluginConversationReferenceAuthority
from companion_v01.plugin_generation_candidate import PluginGenerationCandidateBuilder
from companion_v01.plugin_generation_runtime import PluginGenerationRuntime
from companion_v01.plugin_installation import ManagedPluginArtifactStore
from companion_v01.plugin_tasks import HostTaskProvider
from companion_v01.session_inbox import SessionInboxStore
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.turn_coordination import TurnCoordinator
from companion_v01.plugin_turn_intents import HostTurnResult
from tests.test_plugin_engine_bridge import EngineFacade
from tests.test_plugin_resources import services


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "examples" / "plugins" / "akane_sdk_turn_game"
PLUGIN_ID = "example.sdk-turn-game"


class InstalledSdkTurnGameTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._close)
        root = Path(self.temporary.name)
        self.root = root
        self.refs = PluginConversationReferenceAuthority(root / "conversation.key", instance_id="turn-game-test")
        self.store = SessionInboxStore(root / "inbox.db")
        self.queue = DurableSessionWorkQueue(self.store)
        self.coordinator = TurnCoordinator()
        self.turns = HostAgentEventRouter(self.refs.resolve)
        self.turns.bind_runtime(self.queue, self.coordinator)
        self.turn_requests = []

        async def handle(intent, resolved):
            self.turn_requests.append((intent, dict(resolved)))
            return HostTurnResult(True, "queued", delivery_status="queued")

        self.turns.register_channel("desktop_pet", handle)
        self.artifacts = ManagedPluginArtifactStore(root / "artifacts", instance_id="turn-game-test", project_root=ROOT)
        self.selections = PluginSelectionStore(root / "selections.json", defaults=(), instance_id="turn-game-test")
        self.jobs = HostJobStore(root / "jobs.db")
        self.runtime = PluginGenerationRuntime(
            (),
            candidate_builder=PluginGenerationCandidateBuilder(
                source_resolver=self.artifacts,
                project_root=ROOT,
                work_root=root / "generations",
            ),
        )
        self.runtime.bind_turn_router(self.turns)
        self.runtime.bind_task_provider(HostTaskProvider(self.jobs, generation_active=self.runtime.generation_active))
        self.engine = EngineFacade(
            __import__("companion_v01.plugin_tool_bridge", fromlist=["PluginCapabilityToolBridge"]).PluginCapabilityToolBridge(
                self.runtime,
                config_base_dir=root,
                conversation_ref_issuer=lambda context: self.refs.issue(context),
            )
        )
        self.engine.store, _, _ = services(root)
        self.engine.llm = Mock(side_effect=AssertionError("turn game must not call a model directly"))
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

    async def _install(self) -> None:
        staged = await self.service.stage_source(source_path=str(PROJECT))
        self.assertTrue(staged["ok"], staged)
        installed = await self.service.install_stage(
            stage_id=staged["stage_id"], approved_permissions=staged["permissions"]
        )
        self.assertTrue(installed["ok"], installed)

    async def _call(self, name: str, arguments: dict):
        handler = self.engine._resolve_tool_handlers()[name]
        execution = await asyncio.to_thread(
            handler.execute,
            call={"type": name, "arguments": arguments},
            context=ToolExecutionContext(
                "owner", "session", 1, {}, character_pack_id="reimu",
                client_mode="desktop_pet", result_consumer="program",
            ),
        )
        self.assertIsNotNone(execution.capability_result, execution)
        return execution.capability_result

    async def _wait_timeline(self, expected: int) -> None:
        async with asyncio.timeout(10):
            while len(self.timeline) < expected:
                await asyncio.sleep(0.01)

    async def test_real_game_task_state_version_and_control_contract(self):
        await self._install()

        created = (await self._call(f"{PLUGIN_ID}.create_game", {})).value
        self.assertEqual(created["status"], "created")
        game_id = created["state"]["game_id"]
        self.assertEqual(created["state"]["state_version"], 1)
        self.assertEqual(created["task"]["status"], "running")
        self.assertEqual(created["event"]["status"], "accepted")
        event_id = created["event"]["event_id"]
        self.assertTrue(event_id)
        await self._wait_timeline(1)
        publication = (await self._call(f"{PLUGIN_ID}.event_status", {
            "dispatch_id": created["event"]["dispatch_id"],
        })).value
        self.assertTrue(publication["complete"])
        capture = next(
            item for item in publication["deliveries"]
            if item["subscription_id"] == f"{PLUGIN_ID}.observe"
        )
        signed_event = capture["value"]
        self.assertEqual(signed_event["event_id"], event_id)
        self.assertEqual(signed_event["state_version"], 1)
        self.assertGreaterEqual(signed_event["event_version"], 1)

        first = (await self._call(f"{PLUGIN_ID}.submit_action", {
            "game_id": game_id, "action": "attack", "state_version": 1,
        })).value
        self.assertEqual(first["status"], "applied")
        self.assertEqual(first["state"]["state_version"], 2)
        self.assertNotEqual(first["event"]["event_id"], event_id)
        await self._wait_timeline(2)

        stale = (await self._call(f"{PLUGIN_ID}.submit_action", {
            "game_id": game_id, "action": "attack", "state_version": 1,
        })).value
        self.assertEqual(stale["status"], "stale")
        self.assertFalse(stale["side_effect_applied"])
        self.assertEqual(stale["state"]["state_version"], 2)
        self.assertEqual((await self._call(f"{PLUGIN_ID}.observe_state", {"game_id": game_id})).value["state"]["state_version"], 2)

        paused = (await self._call(f"{PLUGIN_ID}.pause_game", {"game_id": game_id})).value
        self.assertEqual(paused["status"], "paused")
        await self._wait_timeline(3)
        blocked = (await self._call(f"{PLUGIN_ID}.submit_action", {
            "game_id": game_id, "action": "attack", "state_version": paused["state"]["state_version"],
        })).value
        self.assertEqual(blocked["reason"], "game_not_running")
        self.assertFalse(blocked["side_effect_applied"])

        resumed = (await self._call(f"{PLUGIN_ID}.resume_game", {"game_id": game_id})).value
        self.assertEqual(resumed["status"], "resumed")
        self.assertEqual(resumed["task"]["status"], "running")
        self.assertEqual(resumed["turn"]["status"], "queued")
        self.assertEqual(len(self.turn_requests), 1)

        cancelled = (await self._call(f"{PLUGIN_ID}.cancel_game", {"game_id": game_id})).value
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["task"]["status"], "cancelled")
        await self._wait_timeline(5)
        after_cancel = (await self._call(f"{PLUGIN_ID}.submit_action", {
            "game_id": game_id, "action": "attack", "state_version": cancelled["state"]["state_version"],
        })).value
        self.assertEqual(after_cancel["reason"], "game_not_running")
        no_turn = (await self._call(f"{PLUGIN_ID}.request_game_turn", {"game_id": game_id})).value
        self.assertEqual(no_turn["reason"], "game_not_running")
        self.engine.llm.assert_not_called()

    async def test_game_completion_and_duplicate_event_key_do_not_request_turn(self):
        await self._install()
        created = (await self._call(f"{PLUGIN_ID}.create_game", {})).value
        game_id = created["state"]["game_id"]
        current = 1
        for _ in range(4):
            result = (await self._call(f"{PLUGIN_ID}.submit_action", {
                "game_id": game_id, "action": "attack", "state_version": current,
            })).value
            current = result["state"]["state_version"]
        self.assertEqual(result["state"]["status"], "won")
        self.assertEqual(result["task"]["status"], "completed")
        finished_turn = (await self._call(f"{PLUGIN_ID}.request_game_turn", {"game_id": game_id})).value
        self.assertEqual(finished_turn["reason"], "game_not_running")
        self.assertEqual(len(self.turn_requests), 0)

        duplicate = (await self._call(f"{PLUGIN_ID}.republish_state", {"game_id": game_id})).value
        self.assertEqual(duplicate["first"]["event_id"], duplicate["second"]["event_id"])
        await self._wait_timeline(5)


if __name__ == "__main__":
    unittest.main()
