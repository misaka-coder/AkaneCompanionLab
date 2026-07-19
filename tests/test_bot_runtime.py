from __future__ import annotations

import asyncio
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import config
from companion_v01.bot_registry import BotRegistry, BotRegistryError
from companion_v01.bot_profile import BotConfig, BotQQChannelConfig
from companion_v01.bot_runtime import BotRuntime, BotRuntimeFactory
from companion_v01.host_bot_bootstrap import build_host_bot_registry
from companion_v01.instance_profile import instance_context_from_bot_config
from companion_v01.instance_runtime import bind_instance_runtime
from companion_v01.runtime_settings import BotSettingsView
from companion_v01.settings_overrides import RuntimeConfigView, SettingsOverrideStore


class _FakePluginHost:
    def __init__(self) -> None:
        self.start_count = 0
        self.stop_count = 0

    async def start(self) -> dict[str, Any]:
        self.start_count += 1
        return {"status": "active", "plugins": []}

    def build_qq_command_broker(self) -> str:
        return "broker"

    async def stop(self) -> dict[str, Any]:
        self.stop_count += 1
        return {"status": "stopped", "close_failure_count": 0}


class _FakeEngine:
    def __init__(self) -> None:
        self.close_count = 0
        self.reloaded_settings = None

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
        plugin_host=plugin_host,
        plugin_capability_source=SimpleNamespace(),
        engine=engine,
        settings=BotSettingsView(),
        tts_client=SimpleNamespace(),
        runtime_metrics=SimpleNamespace(),
        public_guard=SimpleNamespace(),
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
        plugin_host=plugin_host,
        plugin_capability_source=SimpleNamespace(),
        engine=engine,
        settings=BotSettingsView(),
        tts_client=SimpleNamespace(),
        runtime_metrics=SimpleNamespace(),
        public_guard=SimpleNamespace(),
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
        self.assertIsNot(runtime.settings, original)
        self.assertEqual(runtime.settings.chat_model_name, "bot-model")
        self.assertEqual(runtime.settings.vision_model_name, "bot-vision")
        self.assertIs(engine.reloaded_settings, runtime.settings)

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

    async def test_stop_reports_structured_failure_without_skipping_engine_close(self) -> None:
        runtime, plugin_host, engine, followups = _runtime()

        async def failed_stop() -> dict[str, Any]:
            raise RuntimeError("synthetic failure")

        plugin_host.stop = failed_stop  # type: ignore[method-assign]

        result = await runtime.stop()

        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["reason"], "plugin_host_shutdown_failed")
        self.assertEqual(engine.close_count, 1)
        self.assertEqual(followups.close_count, 1)


class BotRuntimeFactoryMultiBotTests(unittest.TestCase):
    def test_factory_constructs_and_runs_three_bots_without_override_cross_talk(self) -> None:
        original_max_tool_rounds = config.MAX_TOOL_ROUNDS
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
                        {"MAX_TOOL_ROUNDS": index}
                    )

                bootstrap = build_host_bot_registry(factory=factory, host_data_root=root)
                registry = bootstrap.registry
                runtimes.extend(registry.values())

                self.assertEqual(
                    [runtime.config_module.MAX_TOOL_ROUNDS for runtime in runtimes],
                    [3, 4, 5],
                )
                self.assertEqual(config.MAX_TOOL_ROUNDS, original_max_tool_rounds)
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
