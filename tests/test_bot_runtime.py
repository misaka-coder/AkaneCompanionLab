from __future__ import annotations

import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from companion_v01.bot_registry import BotRegistry, BotRegistryError
from companion_v01.bot_runtime import BotRuntime
from companion_v01.runtime_settings import BotSettingsView


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
        instance_context=SimpleNamespace(instance_id=bot_id),
        instance_runtime=SimpleNamespace(layout=SimpleNamespace()),
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
        runtime, *_ = _runtime("../escape")

        with self.assertRaises(BotRegistryError) as raised:
            registry.add(runtime)

        self.assertEqual(raised.exception.reason, "invalid_bot_id")
        self.assertEqual(len(registry), 0)


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


class AppBootstrapContractTests(unittest.TestCase):
    def test_app_has_one_runtime_factory_and_no_direct_bot_constructors(self) -> None:
        source = (  # noqa: PTH123 - repository source under test
            __import__("pathlib").Path(__file__).resolve().parents[1] / "companion_v01" / "app.py"
        ).read_text(encoding="utf-8")

        self.assertIn("BotRuntimeFactory(", source)
        self.assertIn("bot_registry = BotRegistry", source)
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
