from __future__ import annotations

import asyncio
import tempfile
import textwrap
import unittest
from pathlib import Path

from capcore import CapabilityDescriptor, CapabilityIOSlot, CapabilityResult, InvocationContext

from companion_v01.plugin_generation import (
    PLUGIN_GENERATION_PROTOCOL,
    PluginGenerationError,
    PluginGenerationProcess,
)
from companion_v01.plugin_generation_codec import (
    PluginGenerationCodecError,
    capability_descriptor_from_wire,
    capability_descriptor_to_wire,
    capability_result_from_wire,
    capability_result_to_wire,
    invocation_context_from_wire,
    invocation_context_to_wire,
    json_snapshot,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_plugin_site(root: Path, *, broken: bool = False) -> Path:
    site = root / "site"
    package = site / "generation_fixture"
    dist_info = site / "generation_fixture-0.1.0.dist-info"
    package.mkdir(parents=True)
    dist_info.mkdir(parents=True)
    if broken:
        source = "def create_plugin():\n    raise RuntimeError('broken')\n"
    else:
        source = textwrap.dedent(
            """
            from companion_v01.plugin_api import (
                AKANE_PLUGIN_API_VERSION,
                DIRECT_CONVERSATION_EVENT,
                EVENT_SUBSCRIBE_PERMISSION,
                PluginEventResult,
                PluginManifest,
            )

            class Handler:
                async def handle_event(self, event):
                    return PluginEventResult()

            class Plugin:
                manifest = PluginManifest(
                    plugin_id="test.generation",
                    plugin_version="0.1.0",
                    plugin_api_version=AKANE_PLUGIN_API_VERSION,
                    permissions=(EVENT_SUBSCRIBE_PERMISSION,),
                )

                def register(self, registrar):
                    print("plugin output must not enter the protocol lane")
                    registrar.add_event_handler(DIRECT_CONVERSATION_EVENT, Handler())

            def create_plugin():
                return Plugin()
            """
        )
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: generation-fixture\nVersion: 0.1.0\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[akane.plugins.v1]\ntest.generation = generation_fixture:create_plugin\n",
        encoding="utf-8",
    )
    return site


def _write_capability_plugin_site(root: Path) -> Path:
    site = root / "site"
    package = site / "generation_fixture"
    dist_info = site / "generation_fixture-0.1.0.dist-info"
    package.mkdir(parents=True)
    dist_info.mkdir(parents=True)
    source = textwrap.dedent(
        """
        import asyncio

        from capcore import (
            CapabilityDescriptor,
            CapabilityIOSlot,
            CapabilityResult,
            HealthStatus,
        )
        from companion_v01.plugin_api import (
            AKANE_PLUGIN_API_VERSION,
            CAPABILITY_PROMPT_INVOKE_PERMISSION,
            PluginManifest,
        )

        CAPABILITY_ID = "test.generation.echo.v1"

        class Adapter:
            provider_id = "provider.test.generation"

            async def health(self):
                return HealthStatus(ok=True, status="ready")

            async def list_capabilities(self):
                return (
                    CapabilityDescriptor(
                        id=CAPABILITY_ID,
                        display_name="Generation echo",
                        short_hint="Echo public JSON values after an optional delay.",
                        visible_in=("diagnostics",),
                        prompt_exposed=True,
                        risk="low",
                        confirm="never",
                        effects=(),
                        trigger=None,
                        inputs=(
                            CapabilityIOSlot(name="value", kind="string", required=True),
                            CapabilityIOSlot(name="delay_ms", kind="integer", required=False),
                        ),
                        outputs=(),
                        raw={"contract": "generation-echo.v1"},
                    ),
                )

            async def invoke(self, capability_id, args, context):
                await asyncio.sleep(max(0, args.get("delay_ms", 0)) / 1000)
                return CapabilityResult(
                    is_error=False,
                    status="ok",
                    content={
                        "capability_id": capability_id,
                        "value": args["value"],
                        "profile_user_id": context.profile_user_id,
                        "session_id": context.session_id,
                        "client_mode": context.client_mode,
                    },
                )

            async def aclose(self):
                return None

        class Plugin:
            manifest = PluginManifest(
                plugin_id="test.generation",
                plugin_version="0.1.0",
                plugin_api_version=AKANE_PLUGIN_API_VERSION,
                permissions=(CAPABILITY_PROMPT_INVOKE_PERMISSION,),
            )

            def register(self, registrar):
                registrar.add_capability_adapter(Adapter())

        def create_plugin():
            return Plugin()
        """
    )
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: generation-fixture\nVersion: 0.1.0\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[akane.plugins.v1]\ntest.generation = generation_fixture:create_plugin\n",
        encoding="utf-8",
    )
    return site


def _descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id="test.generation.echo.v1",
        display_name="Generation echo",
        short_hint="Echo one value.",
        visible_in=("diagnostics",),
        prompt_exposed=True,
        risk="low",
        confirm="never",
        effects=(),
        trigger=None,
        inputs=(CapabilityIOSlot(name="value", kind="string", required=True),),
        outputs=(),
        raw={"contract": "generation-echo.v1"},
    )


class PluginGenerationCodecTests(unittest.TestCase):
    def test_public_capcore_values_round_trip_as_json(self) -> None:
        descriptor = _descriptor()
        context = InvocationContext(
            profile_user_id="主人",
            session_id="session-1",
            client_mode="qq_text",
        )
        result = CapabilityResult(
            is_error=False,
            status="ok",
            content={"text": "完成", "items": [1, True, None]},
        )

        self.assertEqual(
            capability_descriptor_from_wire(capability_descriptor_to_wire(descriptor)),
            descriptor,
        )
        self.assertEqual(
            invocation_context_from_wire(invocation_context_to_wire(context)),
            context,
        )
        self.assertEqual(
            capability_result_from_wire(capability_result_to_wire(result)),
            result,
        )

    def test_non_json_values_are_rejected_without_a_size_policy(self) -> None:
        with self.assertRaises(PluginGenerationCodecError):
            json_snapshot({"invalid": object()})
        with self.assertRaises(PluginGenerationCodecError):
            json_snapshot({"nested": {1: "key coercion is not allowed"}})
        with self.assertRaises(PluginGenerationCodecError):
            capability_descriptor_from_wire(
                {
                    **capability_descriptor_to_wire(_descriptor()),
                    "prompt_exposed": "true",
                }
            )

        payload = {"text": "字" * 100_000}
        self.assertEqual(json_snapshot(payload), payload)


class PluginGenerationProcessTests(unittest.TestCase):
    def test_generation_starts_reports_health_and_drains(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generation = PluginGenerationProcess(
                project_root=PROJECT_ROOT,
                site_dir=_write_plugin_site(root),
                plugin_id="test.generation",
                work_dir=root / "work",
            )

            ready = generation.start()
            self.assertTrue(ready["ok"])
            self.assertEqual(ready["protocol"], PLUGIN_GENERATION_PROTOCOL)
            self.assertEqual(ready["plugin_id"], "test.generation")
            self.assertGreater(ready["startup_ms"], 0)
            self.assertTrue(generation.running)

            health = generation.health()
            self.assertTrue(health["ok"])
            self.assertEqual(health["status"], "active")
            self.assertEqual(health["snapshot"]["plugin_count"], 1)

            stopped = generation.stop()
            self.assertTrue(stopped["ok"])
            self.assertEqual(stopped["status"], "stopped")
            self.assertFalse(generation.running)
            self.assertEqual(generation.stop()["reason"], "already_stopped")

    def test_failed_candidate_does_not_remain_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generation = PluginGenerationProcess(
                project_root=PROJECT_ROOT,
                site_dir=_write_plugin_site(root, broken=True),
                plugin_id="test.generation",
                work_dir=root / "work",
            )

            with self.assertRaises(PluginGenerationError):
                generation.start()
            self.assertFalse(generation.running)

    def test_generation_publishes_and_invokes_capabilities(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=root / "work",
                )
                ready = generation.start()
                try:
                    self.assertEqual(len(ready["capabilities"]), 1)
                    self.assertEqual(
                        tuple(generation.capability_descriptors),
                        ("test.generation.echo.v1",),
                    )
                    result = await generation.invoke(
                        "test.generation.echo.v1",
                        {"value": "你好"},
                        context=InvocationContext(
                            profile_user_id="owner",
                            session_id="session-1",
                            client_mode="qq_text",
                        ),
                    )
                    self.assertFalse(result.is_error)
                    self.assertEqual(result.content["value"], "你好")
                    self.assertEqual(result.content["session_id"], "session-1")
                    missing = await generation.invoke(
                        "test.generation.missing.v1",
                        {},
                        context=InvocationContext(),
                    )
                    self.assertTrue(missing.is_error)
                    self.assertEqual(missing.status, "not_found")
                    self.assertEqual(missing.reason, "unknown_capability")
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_concurrent_invocations_and_health_are_correlated(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=root / "work",
                )
                self.assertIsNone(generation.stop_timeout_seconds)
                generation.start()
                try:
                    context = InvocationContext(session_id="concurrent")
                    slow = asyncio.create_task(
                        generation.invoke(
                            "test.generation.echo.v1",
                            {"value": "slow", "delay_ms": 500},
                            context=context,
                        )
                    )
                    await asyncio.sleep(0.05)
                    fast = await generation.invoke(
                        "test.generation.echo.v1",
                        {"value": "fast", "delay_ms": 10},
                        context=context,
                    )
                    health = await asyncio.to_thread(generation.health)

                    self.assertEqual(fast.content["value"], "fast")
                    self.assertFalse(slow.done())
                    self.assertTrue(health["ok"])
                    self.assertEqual((await slow).content["value"], "slow")
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_cancellation_reaches_the_worker_and_generation_remains_usable(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=root / "work",
                )
                generation.start()
                try:
                    context = InvocationContext(session_id="cancel")
                    pending = asyncio.create_task(
                        generation.invoke(
                            "test.generation.echo.v1",
                            {"value": "cancel", "delay_ms": 5_000},
                            context=context,
                        )
                    )
                    await asyncio.sleep(0.1)
                    pending.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await pending
                    result = await asyncio.wait_for(
                        generation.invoke(
                            "test.generation.echo.v1",
                            {"value": "after-cancel"},
                            context=context,
                        ),
                        timeout=2.0,
                    )
                    self.assertEqual(result.content["value"], "after-cancel")
                finally:
                    generation.stop()

        asyncio.run(scenario())

    def test_stop_drains_an_invocation_accepted_before_stop(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                generation = PluginGenerationProcess(
                    project_root=PROJECT_ROOT,
                    site_dir=_write_capability_plugin_site(root),
                    plugin_id="test.generation",
                    work_dir=root / "work",
                )
                generation.start()
                invocation = asyncio.create_task(
                    generation.invoke(
                        "test.generation.echo.v1",
                        {"value": "drain", "delay_ms": 400},
                        context=InvocationContext(session_id="drain"),
                    )
                )
                await asyncio.sleep(0.05)
                stop_task = asyncio.create_task(asyncio.to_thread(generation.stop))
                await asyncio.sleep(0.05)
                with self.assertRaises(PluginGenerationError) as rejected:
                    await generation.invoke(
                        "test.generation.echo.v1",
                        {"value": "late"},
                        context=InvocationContext(session_id="drain"),
                    )

                self.assertEqual(rejected.exception.reason, "plugin_generation_unavailable")
                self.assertEqual((await invocation).content["value"], "drain")
                stopped = await stop_task
                self.assertTrue(stopped["ok"])
                self.assertFalse(generation.running)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
