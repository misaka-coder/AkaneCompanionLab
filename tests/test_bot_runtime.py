from __future__ import annotations

import asyncio
import logging
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import config
from companion_v01.bot_registry import BotRegistry, BotRegistryError
from companion_v01.bot_profile import BotConfig, BotQQChannelConfig
from companion_v01.bot_runtime import BotRuntime, BotRuntimeFactory, _host_job_completion_request
from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_tool_jobs import HostToolJobRuntime
from companion_v01.host_bot_bootstrap import build_host_bot_registry
from companion_v01.instance_profile import instance_context_from_bot_config
from companion_v01.instance_runtime import bind_instance_runtime
from companion_v01.plugin_generation_runtime import PluginGenerationRuntime
from companion_v01.plugin_agent_events import HostAgentEventRouter
from companion_v01.plugin_turn_intents import HostTurnResult
from companion_v01.runtime_settings import BotSettingsView
from companion_v01.settings_overrides import RuntimeConfigView, SettingsOverrideStore
from companion_v01.turn_coordination import TurnCoordinator


class _FakePluginHost:
    def __init__(self) -> None:
        self.start_count = 0
        self.stop_count = 0

    async def start(self) -> dict[str, Any]:
        self.start_count += 1
        return {"status": "active", "plugins": []}

    def build_qq_command_broker(self, host_registrations: tuple = ()) -> str:
        self.host_registrations = host_registrations
        return "broker"

    def build_event_broker(self) -> str:
        return "event-broker"

    async def stop(self) -> dict[str, Any]:
        self.stop_count += 1
        return {"status": "stopped", "close_failure_count": 0}


class _FakeEngine:
    def __init__(self) -> None:
        self.close_count = 0
        self.reloaded_settings = None
        self.settings = BotSettingsView()
        self.llm = SimpleNamespace(settings=self.settings)

    def close(self) -> dict[str, str]:
        self.close_count += 1
        return {"status": "stopped"}

    def reload_model_services(self, *, settings) -> dict[str, str]:
        self.reloaded_settings = settings
        return {"status": "reloaded"}


class _FakeFollowups:
    def __init__(self) -> None:
        self.close_count = 0

    async def close(self, *, timeout: float) -> dict[str, Any]:
        self.close_count += 1
        self.timeout = timeout
        return {"status": "stopped", "remaining": 0}


def _runtime(bot_id: str = "bot-a") -> tuple[BotRuntime, _FakePluginHost, _FakeEngine, _FakeFollowups]:
    plugin_host = _FakePluginHost()
    engine = _FakeEngine()
    followups = _FakeFollowups()
    runtime = BotRuntime(
        bot_config=BotConfig(
            schema_version=1,
            bot_id=bot_id,
            enabled=True,
            display_name=bot_id,
            character_pack_id="",
            memory_space_id=bot_id,
            model_profile_ref="default",
            capability_profile_ref="default",
            care_enabled=True,
            qq=BotQQChannelConfig(),
            plugins=(),
        ),
        instance_context=SimpleNamespace(instance_id=bot_id),
        instance_runtime=SimpleNamespace(layout=SimpleNamespace(data_root=Path("test-roots") / bot_id)),
        deployment_security=SimpleNamespace(),
        resources=SimpleNamespace(),
        desktop_pet_character_resources=SimpleNamespace(),
        model_service_config_store=SimpleNamespace(),
        settings_override_store=SimpleNamespace(),
        desktop_satellite_service=SimpleNamespace(),
        plugin_runtime=plugin_host,
        plugin_agent_event_router=HostAgentEventRouter(lambda _reference: None),
        extension_management_service=SimpleNamespace(),
        plugin_capability_source=SimpleNamespace(),
        engine=engine,
        settings=BotSettingsView(),
        tts_client=SimpleNamespace(),
        runtime_metrics=SimpleNamespace(),
        public_guard=SimpleNamespace(),
        turn_coordinator=TurnCoordinator(),
        qq_gateway=None,
        qq_followup_tasks=followups,
        config_module=SimpleNamespace(),
        logger=logging.getLogger("test.bot_runtime"),
    )
    return runtime, plugin_host, engine, followups


def _leased_runtime(root: Path, bot_id: str) -> tuple[BotRuntime, _FakePluginHost, _FakeEngine, _FakeFollowups]:
    config = BotConfig(
        schema_version=1,
        bot_id=bot_id,
        enabled=True,
        display_name=f"Akane {bot_id}",
        character_pack_id="",
        memory_space_id=bot_id,
        model_profile_ref="default",
        capability_profile_ref="default",
        care_enabled=True,
        qq=BotQQChannelConfig(),
        plugins=(),
    )
    context = instance_context_from_bot_config(config)
    lease = bind_instance_runtime(context, data_root=root, explicit_data_root=True)
    plugin_host = _FakePluginHost()
    engine = _FakeEngine()
    followups = _FakeFollowups()
    runtime = BotRuntime(
        bot_config=config,
        instance_context=context,
        instance_runtime=lease,
        deployment_security=SimpleNamespace(),
        resources=SimpleNamespace(),
        desktop_pet_character_resources=SimpleNamespace(),
        model_service_config_store=SimpleNamespace(),
        settings_override_store=SimpleNamespace(),
        desktop_satellite_service=SimpleNamespace(),
        plugin_runtime=plugin_host,
        plugin_agent_event_router=HostAgentEventRouter(lambda _reference: None),
        extension_management_service=SimpleNamespace(),
        plugin_capability_source=SimpleNamespace(),
        engine=engine,
        settings=BotSettingsView(),
        tts_client=SimpleNamespace(),
        runtime_metrics=SimpleNamespace(),
        public_guard=SimpleNamespace(),
        turn_coordinator=TurnCoordinator(),
        qq_gateway=None,
        qq_followup_tasks=followups,
        config_module=SimpleNamespace(),
        logger=logging.getLogger(f"test.bot_runtime.{bot_id}"),
    )
    return runtime, plugin_host, engine, followups


class BotRegistryTests(unittest.TestCase):
    def test_registry_has_one_default_authority_and_rejects_duplicate_ids(self) -> None:
        registry = BotRegistry()
        first, *_ = _runtime("bot-a")
        second, *_ = _runtime("bot-a")

        registry.add(first, default=True)

        self.assertEqual(registry.default_bot_id, "bot-a")
        self.assertIs(registry.default(), first)
        with self.assertRaises(BotRegistryError) as raised:
            registry.add(second)
        self.assertEqual(raised.exception.reason, "duplicate_bot_id")

    def test_registry_rejects_unsafe_ids_before_registration(self) -> None:
        registry = BotRegistry()
        runtime = SimpleNamespace(
            bot_id="../escape",
            runtime_layout=SimpleNamespace(data_root=Path("test-roots") / "unsafe"),
        )

        with self.assertRaises(BotRegistryError) as raised:
            registry.add(runtime)

        self.assertEqual(raised.exception.reason, "invalid_bot_id")
        self.assertEqual(len(registry), 0)

    def test_registry_rejects_two_bots_bound_to_the_same_data_root(self) -> None:
        registry = BotRegistry()
        first, *_ = _runtime("bot-a")
        second, *_ = _runtime("bot-b")
        second.instance_runtime.layout.data_root = first.instance_runtime.layout.data_root

        registry.add(first)
        with self.assertRaises(BotRegistryError) as raised:
            registry.add(second)

        self.assertEqual(raised.exception.reason, "duplicate_data_root")


class BotRegistryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_deadline_does_not_depend_on_runtime_cancellation_cooperation(self) -> None:
        release = asyncio.Event()
        cancelled = asyncio.Event()

        class CancellationResistantRuntime:
            bot_id = "bot-a"
            display_name = "bot-a"
            runtime_layout = SimpleNamespace(data_root=Path("bot-a"))

            async def stop(self) -> dict[str, str]:
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    await release.wait()
                return {"status": "stopped"}

        registry = BotRegistry(default_bot_id="bot-a")
        registry.add(CancellationResistantRuntime(), default=True)
        started_at = asyncio.get_running_loop().time()

        stopped = await registry.stop("bot-a", timeout_seconds=0.1)
        elapsed = asyncio.get_running_loop().time() - started_at

        self.assertLess(elapsed, 0.5)
        self.assertEqual(stopped["status"], "degraded")
        self.assertEqual(stopped["reason"], "bot_stop_timeout")
        await asyncio.wait_for(cancelled.wait(), timeout=0.5)
        release.set()
        await asyncio.sleep(0)

    async def test_stop_all_stops_runtimes_concurrently_under_one_host_deadline(self) -> None:
        all_stopping = asyncio.Event()
        stopping_count = 0

        class CoordinatedRuntime:
            def __init__(self, bot_id: str, data_root: Path) -> None:
                self.bot_id = bot_id
                self.display_name = bot_id
                self.runtime_layout = SimpleNamespace(data_root=data_root)

            async def start(self) -> dict[str, str]:
                return {"status": "active"}

            async def stop(self) -> dict[str, str]:
                nonlocal stopping_count
                stopping_count += 1
                if stopping_count == 2:
                    all_stopping.set()
                await all_stopping.wait()
                return {"status": "stopped"}

        with tempfile.TemporaryDirectory() as temp_dir:
            registry = BotRegistry(default_bot_id="bot-a")
            root = Path(temp_dir)
            registry.add(CoordinatedRuntime("bot-a", root / "bot-a"), default=True)
            registry.add(CoordinatedRuntime("bot-b", root / "bot-b"))
            await registry.start_all(timeout_seconds=0.5)

            stopped = await registry.stop_all(timeout_seconds=0.5)

        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopping_count, 2)
        self.assertTrue(all(item["state"] == "stopped" for item in stopped["bots"]))

    async def test_stop_all_keeps_blocking_engine_closes_off_the_lifecycle_loop(self) -> None:
        registry = BotRegistry(default_bot_id="bot-a")
        runtimes = [_runtime(bot_id)[0] for bot_id in ("bot-a", "bot-b")]
        both_engines_closing = threading.Event()
        close_guard = threading.Lock()
        close_count = 0
        overlap_observed: list[bool] = []

        def coordinated_close() -> dict[str, str]:
            nonlocal close_count
            with close_guard:
                close_count += 1
                if close_count == 2:
                    both_engines_closing.set()
            overlap_observed.append(both_engines_closing.wait(timeout=0.5))
            return {"status": "stopped"}

        for runtime in runtimes:
            runtime.engine.close = coordinated_close  # type: ignore[method-assign]
            registry.add(runtime, default=runtime.bot_id == "bot-a")
        await registry.start_all(timeout_seconds=1.0)

        stopped = await registry.stop_all(timeout_seconds=1.0)

        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(overlap_observed, [True, True])

    async def test_three_bot_runtimes_use_independent_roots_and_start_failure_is_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            runtimes = [_leased_runtime(base / bot_id, bot_id) for bot_id in ("bot-a", "bot-b", "bot-c")]
            registry = BotRegistry(default_bot_id="bot-a")
            for runtime, *_ in runtimes:
                registry.add(runtime, default=runtime.bot_id == "bot-a")

            async def failed_start() -> dict[str, Any]:
                raise RuntimeError("synthetic startup failure with secret/path")

            runtimes[1][1].start = failed_start  # type: ignore[method-assign]
            try:
                start_status = await registry.start_all(timeout_seconds=2.0)

                self.assertEqual(start_status["status"], "degraded")
                self.assertEqual(
                    {item["bot_id"]: item["state"] for item in start_status["bots"]},
                    {"bot-a": "online", "bot-b": "degraded", "bot-c": "online"},
                )
                self.assertEqual(
                    {runtime.runtime_layout.data_root for runtime, *_ in runtimes},
                    {(base / bot_id).resolve() for bot_id in ("bot-a", "bot-b", "bot-c")},
                )
                public = registry.public_snapshot()
                self.assertEqual(public["count"], 3)
                self.assertNotIn(temp_dir, str(public))
                self.assertNotIn("synthetic startup failure", str(public))

                stop_status = await registry.stop_all(timeout_seconds=2.0)
                self.assertEqual(stop_status["status"], "stopped")
                self.assertTrue(all(item["state"] == "stopped" for item in stop_status["bots"]))
            finally:
                for runtime, *_ in runtimes:
                    runtime.instance_runtime.release()


class BotRuntimeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_recovers_durable_jobs_before_accepting_runtime_work(self) -> None:
        runtime, _plugin_host, _engine, _followups = _runtime()
        recovered: list[str] = []
        runtime.job_store = SimpleNamespace(
            recover_abandoned_claims=lambda: recovered.append("jobs") or 2,
        )
        runtime.host_workflow_jobs = SimpleNamespace(
            recover=lambda: recovered.append("workflows") or 2,
        )

        result = await runtime.start()

        self.assertEqual(result["status"], "active")
        self.assertEqual(recovered, ["jobs", "workflows"])

    async def test_start_binds_and_recovers_host_tool_jobs(self) -> None:
        runtime, _plugin_host, _engine, _followups = _runtime()

        class RecoverableToolJobs:
            callback = None
            recover_count = 0

            def bind_terminal_callback(self, callback) -> None:
                self.callback = callback

            def recover(self) -> int:
                self.recover_count += 1
                return 0

        jobs = RecoverableToolJobs()
        runtime.host_tool_jobs = jobs

        await runtime.start()

        self.assertTrue(callable(jobs.callback))
        self.assertEqual(jobs.recover_count, 1)

    async def test_start_recovers_orphaned_execution_jobs_before_completion_delivery(self) -> None:
        runtime, _plugin_host, _engine, _followups = _runtime()
        order = []

        class RecoverableToolJobs:
            def bind_terminal_callback(self, _callback) -> None:
                order.append("bind-delivery")

            def recover(self) -> int:
                order.append("deliver")
                return 0

        runtime.host_tool_jobs = RecoverableToolJobs()
        runtime.host_execution_jobs = SimpleNamespace(
            recover=lambda: order.append("settle-execution") or 1,
        )
        runtime.host_subagent_jobs = SimpleNamespace(
            recover=lambda: order.append("resume-subagents") or 1,
        )

        await runtime.start()

        self.assertEqual(order, ["bind-delivery", "settle-execution", "resume-subagents", "deliver"])

    async def test_terminal_host_job_becomes_contextual_agent_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            created = store.create(
                owner=HostJobOwner("profile-a", "session-a"),
                capability_source="tool",
                capability_id="generate_image",
                payload={"call": {"prompt": "moon"}},
                idempotency_key="call-a",
                argument_fingerprint="sha256:test",
                character_pack_id="reimu",
                channel="qq_text",
                delivery_target="signed-conversation-ref",
            )
            claimed = store.claim(created["job_id"], worker_id="worker-a")
            store.succeed(
                created["job_id"],
                claim_token=claimed["claim_token"],
                result_summary="图片生成完成。",
                artifacts=[{"handle": "generated-file:image-1"}],
            )
            job = store.get(
                created["job_id"],
                owner=HostJobOwner("profile-a", "session-a"),
            )

            request = _host_job_completion_request(job)

            self.assertEqual(request.conversation_ref, "signed-conversation-ref")
            self.assertEqual(request.event_type, "job.succeeded")
            self.assertEqual(request.source, "host.jobs")
            self.assertEqual(request.idempotency_key, job.completion_event_id)
            self.assertIn("generated-file:image-1", request.message)
            self.assertIn("尚未由本完成事件确认发送", request.message)
            self.assertIn(
                ("artifact_delivery_status", "available_not_delivered"),
                request.data.items(),
            )

    async def test_start_delivers_pending_job_through_the_instance_agent_event_router(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime, _plugin_host, engine, _followups = _runtime()
            store = HostJobStore(Path(temp_dir) / "jobs.db")
            created = store.create(
                owner=HostJobOwner("profile-a", "session-a"),
                capability_source="tool",
                capability_id="generate_image",
                payload={"call": {"prompt": "moon"}},
                idempotency_key="call-a",
                argument_fingerprint="sha256:test",
                character_pack_id="reimu",
                channel="qq_text",
                delivery_target="conversation-ref",
                completion_mode="agent",
                memory_mode="timeline",
            )
            claimed = store.claim(created["job_id"], worker_id="worker-a")
            store.succeed(
                created["job_id"],
                claim_token=claimed["claim_token"],
                result_summary="图片生成完成。",
                artifacts=[{"handle": "generated-file:image-1"}],
            )
            observed: list[tuple] = []
            agent_router = HostAgentEventRouter(
                lambda reference: {
                    "channel": "qq",
                    "kind": "direct",
                    "recipient": "user:1",
                    "session": "session-a",
                    "profile": "profile-a",
                    "character": "reimu",
                }
                if reference == "conversation-ref"
                else None
            )

            async def deliver(request, resolved):
                observed.append((request, dict(resolved)))
                return HostTurnResult(True, "completed", "", "delivered")

            agent_router.register_channel("qq", deliver)
            background = BackgroundTaskRunner({"host-jobs": 1})
            self.addCleanup(background.close)
            runtime.job_store = store
            runtime.plugin_agent_event_router = agent_router
            runtime.host_tool_jobs = HostToolJobRuntime(
                engine=engine,
                store=store,
                background_tasks=background,
            )

            await runtime.start()
            self.assertTrue(await asyncio.to_thread(background.wait_idle, lane="host-job-completions", timeout=2.0))

            completed = store.get(created["job_id"], owner=HostJobOwner("profile-a", "session-a"))
            self.assertEqual(completed.completion_status, "delivered")
            self.assertEqual(len(observed), 1)
            request, resolved = observed[0]
            self.assertEqual(request.trace_id, completed.completion_event_id)
            self.assertEqual(request.event_type, "job.succeeded")
            self.assertEqual(request.memory_mode, "timeline")
            self.assertEqual(resolved["character"], "reimu")
            self.assertIn("generated-file:image-1", request.message)

    async def test_start_recovers_shared_session_inbox_runner_once(self) -> None:
        runtime, _plugin_host, _engine, _followups = _runtime()

        class RecoverableQueue:
            recover_count = 0

            async def recover(self) -> int:
                self.recover_count += 1
                return 0

        queue = RecoverableQueue()
        runtime.session_work_queue = queue

        await runtime.start()
        await runtime.start()

        self.assertEqual(queue.recover_count, 1)

    async def test_thinking_mode_update_is_persisted_and_applied_to_one_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime, _plugin_host, engine, _followups = _runtime()
            store = SettingsOverrideStore(Path(temp_dir) / "settings_overrides.json")
            runtime.settings_override_store = store
            runtime.config_module = RuntimeConfigView(config, {})

            applied = runtime.set_llm_thinking_mode("enabled")

            self.assertEqual(applied, "enabled")
            self.assertEqual(runtime.settings.llm_thinking_mode, "enabled")
            self.assertIs(engine.settings, runtime.settings)
            self.assertIs(engine.llm.settings, runtime.settings)
            self.assertEqual(store.load()["LLM_THINKING_MODE"], "enabled")

    async def test_model_reload_replaces_only_this_runtime_settings_snapshot(self) -> None:
        runtime, _plugin_host, engine, _followups = _runtime()
        original = runtime.settings
        model_settings = SimpleNamespace(
            api_key="bot-key",
            base_url="https://bot.example/v1",
            chat_model="bot-model",
            protocol="responses",
            use_for_vision=True,
            vision_model="bot-vision",
        )

        result = runtime.reload_model_services(model_settings)

        self.assertEqual(result["status"], "reloaded")
        self.assertEqual(result["chat_model_overrides_status"], "unchanged")
        self.assertIsNot(runtime.settings, original)
        self.assertEqual(runtime.settings.chat_model_name, "bot-model")
        self.assertEqual(runtime.settings.vision_model_name, "bot-vision")
        self.assertIs(engine.reloaded_settings, runtime.settings)

    async def test_model_provider_change_clears_qq_session_model_overrides(self) -> None:
        runtime, _plugin_host, _engine, _followups = _runtime()
        gateway = SimpleNamespace(clear_all_chat_model_overrides=lambda: True)
        runtime.qq_gateway = gateway
        runtime.settings = BotSettingsView(
            chat_api_key="old-key",
            chat_base_url="https://old.example/v1",
            chat_model_name="old-model",
            chat_api_protocol="openai",
        )
        model_settings = SimpleNamespace(
            api_key="new-key",
            base_url="https://new.example/v1",
            chat_model="[group]new-model",
            protocol="openai",
            use_for_vision=True,
            vision_model="[group]new-model",
            use_for_image_generation=False,
            image_generation_api_key="",
            image_generation_base_url="",
            image_generation_model="gpt-image-2",
            chat_reasoning_effort="",
            chat_max_output_tokens=0,
        )

        result = runtime.reload_model_services(model_settings)

        self.assertEqual(result["chat_model_overrides_status"], "cleared")

    async def test_start_and_stop_are_idempotent_and_keep_one_lifecycle_owner(self) -> None:
        runtime, plugin_host, engine, followups = _runtime()

        first_start = await runtime.start()
        second_start = await runtime.start()
        first_stop = await runtime.stop()
        second_stop = await runtime.stop()

        self.assertEqual(first_start["status"], "active")
        self.assertEqual(second_start["reason"], "already_started")
        self.assertEqual(first_stop["status"], "stopped")
        self.assertEqual(second_stop, first_stop)
        self.assertEqual(plugin_host.start_count, 1)
        self.assertEqual(plugin_host.stop_count, 1)
        self.assertEqual(engine.close_count, 1)
        self.assertEqual(followups.close_count, 1)
        self.assertEqual(runtime.plugin_command_broker, None)

    async def test_turn_admission_is_revoked_before_queue_and_engine_close(self):
        runtime, _plugins, engine, _followups = _runtime()
        order = []
        original_close = engine.close
        original_signal = runtime.plugin_agent_event_router.request_shutdown
        original_drain = runtime.plugin_agent_event_router.aclose
        def signal():
            order.append("turn-signal")
            original_signal()
        async def queue_close(**kwargs):
            self.assertTrue(runtime.plugin_agent_event_router._closing)
            order.append("queue-close")
            return {"status": "stopped"}
        async def drain():
            await original_drain()
            order.append("turn-drained")
        def close():
            self.assertIn("turn-drained", order)
            order.append("engine-close")
            return original_close()
        runtime.plugin_agent_event_router.request_shutdown = signal
        runtime.plugin_agent_event_router.aclose = drain
        runtime.session_work_queue = SimpleNamespace(close=queue_close)
        engine.close = close
        result = await runtime.stop()
        self.assertEqual(result["status"], "stopped")
        self.assertLess(order.index("turn-signal"), order.index("queue-close"))
        self.assertLess(order.index("queue-close"), order.index("turn-drained"))
        self.assertLess(order.index("turn-drained"), order.index("engine-close"))

    async def test_start_retries_last_good_once_after_candidate_rollback(self) -> None:
        runtime, plugin_runtime, _engine, _followups = _runtime()
        async def failed_start() -> dict[str, Any]:
            return {
                "status": "degraded",
                "published": False,
                "plugins": [{"plugin_id": "akane.test", "status": "unavailable"}],
            }

        async def fallback_restart() -> dict[str, Any]:
            return {
                "status": "active",
                "published": True,
                "plugins": [{"plugin_id": "akane.test", "status": "active"}],
            }

        plugin_runtime.start = failed_start  # type: ignore[method-assign]
        plugin_runtime.restart = fallback_restart  # type: ignore[attr-defined]
        runtime.extension_management_service = SimpleNamespace(
            reconcile_runtime=Mock(
                side_effect=(
                    {"status": "rollback_scheduled", "reload_required": True},
                    {"status": "ready", "reload_required": False},
                )
            )
        )

        result = await runtime.start()

        self.assertEqual(result["status"], "active")
        self.assertTrue(result["plugin_artifact_status"]["fallback_attempted"])
        self.assertEqual(
            result["plugin_artifact_status"]["initial_status"],
            "rollback_scheduled",
        )

    async def test_start_labels_host_commands_with_public_display_name(self) -> None:
        runtime, plugin_host, _engine, _followups = _runtime("internal-bot-id")
        runtime.bot_config = replace(runtime.bot_config, display_name="Akane Public")

        await runtime.start()

        registration = plugin_host.host_registrations[0]
        self.assertEqual(registration.handler._bot_label, "Akane Public")

    async def test_shared_hook_broker_binds_engine_and_gateway_then_unbinds_gateway(self) -> None:
        runtime, plugin_host, engine, _followups = _runtime()
        broker = object()
        engine_bindings: list[Any] = []
        gateway_bindings: list[Any] = []
        plugin_host.build_hook_broker = lambda: broker  # type: ignore[attr-defined]
        engine.bind_plugin_hook_broker = engine_bindings.append  # type: ignore[attr-defined]
        runtime.qq_gateway = SimpleNamespace(bind_plugin_hook_broker=gateway_bindings.append)

        await runtime.start()
        await runtime.stop()

        self.assertEqual(engine_bindings, [broker])
        self.assertEqual(gateway_bindings, [broker, None])

    async def test_stop_closes_voice_runtime_before_memory_engine(self) -> None:
        runtime, _plugin_host, engine, _followups = _runtime()
        close_order: list[str] = []

        class _VoiceRuntime:
            def close(self) -> dict[str, Any]:
                close_order.append("voice")
                return {"status": "stopped"}

        original_engine_close = engine.close

        def close_engine() -> dict[str, str]:
            close_order.append("engine")
            return original_engine_close()

        engine.close = close_engine  # type: ignore[method-assign]
        runtime.voice_runtime_service = _VoiceRuntime()  # type: ignore[assignment]

        result = await runtime.stop()

        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["voice_status"]["status"], "stopped")
        self.assertEqual(close_order, ["voice", "engine"])

    async def test_stop_reports_structured_failure_without_skipping_engine_close(self) -> None:
        runtime, plugin_host, engine, followups = _runtime()

        async def failed_stop() -> dict[str, Any]:
            raise RuntimeError("synthetic failure")

        plugin_host.stop = failed_stop  # type: ignore[method-assign]

        result = await runtime.stop()

        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["reason"], "plugin_runtime_shutdown_failed")
        self.assertEqual(engine.close_count, 1)
        self.assertEqual(followups.close_count, 1)


class BotRuntimeFactoryMultiBotTests(unittest.TestCase):
    def test_factory_constructs_and_runs_three_bots_without_override_cross_talk(self) -> None:
        original_max_tool_rounds = config.TOOL_ROUND_HARD_LIMIT
        runtime_config = RuntimeConfigView(
            config,
            {
                "AKANE_ADMIN_TOKEN": "test-admin-token",
                "AKANE_DESKTOP_SATELLITE_TOKEN": "",
                "QQ_BRIDGE_ENABLED": False,
                "QQ_CHANNEL_PROFILE_REF": "",
                "QQ_BOT_QQ": "",
                "QQ_WEBHOOK_SECRET": "",
                "QQ_ONEBOT_ACCESS_TOKEN": "",
                "PROMPT_CACHE_NAMESPACE": "host-cache",
                "AKANE_EXECUTION_RESOURCE_POLICY": '{"max_dependency_calls":777}',
            },
        )
        factory = BotRuntimeFactory(
            config_module=runtime_config,
            assets_dir=Path(__file__).resolve().parents[1] / "web" / "assets",
        )
        runtimes: list[BotRuntime] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            try:
                root.joinpath("bots.toml").write_text(
                    """\
schema_version = 1
default_bot_id = "bot-a"

[[bots]]
bot_id = "bot-a"
enabled = true
display_name = "bot-a"
memory_space_id = "bot-a"
care_enabled = true

[[bots]]
bot_id = "bot-b"
enabled = true
display_name = "bot-b"
memory_space_id = "bot-b"
care_enabled = true

[[bots]]
bot_id = "bot-c"
enabled = true
display_name = "bot-c"
memory_space_id = "bot-c"
care_enabled = true
""",
                    encoding="utf-8",
                )
                for index, bot_id in enumerate(("bot-a", "bot-b", "bot-c"), start=3):
                    bot_root = root / "bots" / bot_id
                    SettingsOverrideStore(bot_root / "users_data" / "_local" / "settings_overrides.json").save(
                        {"TOOL_ROUND_HARD_LIMIT": index}
                    )

                bootstrap = build_host_bot_registry(factory=factory, host_data_root=root)
                registry = bootstrap.registry
                runtimes.extend(registry.values())

                self.assertEqual(
                    [runtime.config_module.TOOL_ROUND_HARD_LIMIT for runtime in runtimes],
                    [3, 4, 5],
                )
                self.assertEqual(config.TOOL_ROUND_HARD_LIMIT, original_max_tool_rounds)
                self.assertEqual(len({runtime.runtime_layout.data_root for runtime in runtimes}), 3)
                self.assertEqual(
                    [runtime.settings.prompt_cache_namespace for runtime in runtimes],
                    [
                        "host-cache:bot:bot-a",
                        "host-cache:bot:bot-b",
                        "host-cache:bot:bot-c",
                    ],
                )
                self.assertEqual(bootstrap.default_runtime.bot_id, "bot-a")
                self.assertEqual(bootstrap.mode, "bot_profile")
                for runtime in runtimes:
                    self.assertEqual(runtime.engine.executor_broker.resource_policy.max_dependency_calls, 777)
                    self.assertIs(runtime.engine.host_workflow_jobs.executor_broker, runtime.engine.executor_broker)
                shared_satellite_service = runtimes[0].desktop_satellite_service
                self.assertTrue(
                    all(runtime.desktop_satellite_service is shared_satellite_service for runtime in runtimes)
                )
                self.assertTrue(
                    all(
                        runtime.engine.capability_offer_source.service is shared_satellite_service
                        for runtime in runtimes
                    )
                )
                self.assertTrue(
                    all(
                        runtime.engine.capability_registry.offer_source is runtime.engine.capability_offer_source
                        for runtime in runtimes
                    )
                )
                self.assertTrue(
                    all(
                        runtime.engine.executor_broker.offer_source is runtime.engine.capability_offer_source
                        for runtime in runtimes
                    )
                )
                self.assertEqual(
                    len({runtime.engine.capability_offer_source.instance_id for runtime in runtimes}),
                    3,
                )
                self.assertTrue(
                    all(runtime.voice_runtime_service is not None for runtime in runtimes)
                )
                self.assertEqual(
                    len({id(runtime.voice_runtime_service) for runtime in runtimes}),
                    3,
                )
                self.assertEqual(
                    [
                        runtime.voice_runtime_service.state_dir
                        for runtime in runtimes
                        if runtime.voice_runtime_service is not None
                    ],
                    [runtime.runtime_layout.state_dir for runtime in runtimes],
                )
                self.assertTrue(
                    all(
                        runtime.engine.stable_system_blocks_provider.__self__ is runtime.plugin_runtime
                        for runtime in runtimes
                    )
                )
                self.assertTrue(
                    all(
                        isinstance(runtime.plugin_runtime, PluginGenerationRuntime)
                        for runtime in runtimes
                    )
                )
                self.assertTrue(all(runtime.job_store is not None for runtime in runtimes))
                self.assertEqual(len({id(runtime.job_store) for runtime in runtimes}), 3)
                self.assertTrue(
                    all(runtime.job_store.database_path == runtime.engine.store.db_path for runtime in runtimes)
                )
                self.assertTrue(all(runtime.engine.stable_system_blocks_provider() == () for runtime in runtimes))
                self.assertEqual(
                    [runtime.desktop_pet_character_resources.public_prefix for runtime in runtimes],
                    [
                        "/api/bots/bot-a/desktop-pet-character-packs",
                        "/api/bots/bot-b/desktop-pet-character-packs",
                        "/api/bots/bot-c/desktop-pet-character-packs",
                    ],
                )
                self.assertEqual(
                    [runtime.engine.gift_service.public_prefix for runtime in runtimes],
                    [
                        "/api/bots/bot-a/user-assets",
                        "/api/bots/bot-b/user-assets",
                        "/api/bots/bot-c/user-assets",
                    ],
                )

                async def run_lifecycle() -> tuple[dict[str, Any], dict[str, Any]]:
                    started = await registry.start_all(timeout_seconds=5.0)
                    stopped = await registry.stop_all(timeout_seconds=5.0)
                    return started, stopped

                start_status, stop_status = asyncio.run(run_lifecycle())
                self.assertEqual(start_status["status"], "active")
                self.assertTrue(all(item["state"] == "online" for item in start_status["bots"]))
                self.assertEqual(stop_status["status"], "stopped")
            finally:

                async def stop_remaining() -> None:
                    for item in reversed(runtimes):
                        await item.stop()

                asyncio.run(stop_remaining())
                for runtime in reversed(runtimes):
                    runtime.instance_runtime.release()


class AppBootstrapContractTests(unittest.TestCase):
    def test_app_has_one_runtime_factory_and_no_direct_bot_constructors(self) -> None:
        source = (  # noqa: PTH123 - repository source under test
            __import__("pathlib").Path(__file__).resolve().parents[1] / "companion_v01" / "app.py"
        ).read_text(encoding="utf-8")

        self.assertIn("BotRuntimeFactory(", source)
        self.assertIn("build_host_bot_registry(", source)
        self.assertIn("bot_runtime = host_bot_bootstrap.default_runtime", source)
        self.assertIn("runtime_config = bot_runtime.config_module", source)
        self.assertIn("app.state.akane_default_bot_id = bot_registry.default_bot_id", source)
        self.assertIn("build_bot_runtime_routers(", source)
        self.assertIn('route_prefix = f"/api/bots/{http_bot_runtime.bot_id}"', source)
        self.assertIn("app.include_router(runtime_router, prefix=route_prefix)", source)
        self.assertEqual(source.count("config_module=config,"), 1)
        self.assertIn("await bot_registry.start_all()", source)
        self.assertIn("await bot_registry.stop_all()", source)
        for constructor in (
            "AkaneMemoryEngine(",
            "PluginHost(",
            "NapCatQQGateway(",
            "DesktopSatelliteService(",
        ):
            self.assertNotIn(constructor, source)

    def test_bot_factory_captures_saved_model_settings_without_mutating_global_config(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "companion_v01" / "bot_runtime.py").read_text(encoding="utf-8")

        self.assertIn("saved_model_settings = model_store.load()", source)
        self.assertIn("settings.with_model_service(saved_model_settings)", source)
        self.assertNotIn("load_and_apply_saved_model_service", source)


if __name__ == "__main__":
    unittest.main()
