from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Any, Callable

from capcore import CapabilityDescriptor, CapabilityResult, HealthStatus

from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    CAPABILITY_PROMPT_INVOKE_PERMISSION,
    NETWORK_READ_PERMISSION,
    SYSTEM_PROMPT_CONTRIBUTION_PERMISSION,
    PluginManifest,
)
from companion_v01.plugin_contribution_policy import (
    TrustedStatefulPluginContributionPolicy,
)
from companion_v01.plugin_host import PluginHost
from companion_v01.persona_config import load_persona_config
from companion_v01.prompt_builder import PromptBuilder


BASE_PERMISSIONS = (
    CAPABILITY_PROMPT_INVOKE_PERMISSION,
    NETWORK_READ_PERMISSION,
)


class _Distribution:
    version = "0.1.0"
    metadata = {"Name": "akane-test-prompt-plugin"}

    @staticmethod
    def read_text(filename: str) -> None:
        del filename
        return None


class _EntryPoint:
    def __init__(self, name: str, factory: Callable[[], Any]) -> None:
        self.name = name
        self.dist = _Distribution()
        self._factory = factory

    def load(self) -> Callable[[], Any]:
        return self._factory


class _Adapter:
    def __init__(self, plugin_id: str, *, healthy: bool = True) -> None:
        self.provider_id = f"provider.{plugin_id}"
        self._plugin_id = plugin_id
        self._healthy = healthy
        self.close_count = 0

    async def health(self) -> HealthStatus:
        return HealthStatus(ok=self._healthy, status="ready" if self._healthy else "down")

    async def list_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        return (
            CapabilityDescriptor(
                id=f"{self._plugin_id}.read.v1",
                display_name="Read test data",
                short_hint="Read trusted test data.",
                visible_in=("test",),
                prompt_exposed=True,
                risk="low",
                confirm="never",
                effects=("network",),
                trigger=None,
                inputs=(),
                outputs=(),
                raw={},
            ),
        )

    async def invoke(self, capability_id: str, args: dict[str, Any], ctx: Any) -> CapabilityResult:
        del capability_id, args, ctx
        return CapabilityResult(is_error=False, status="ok", content={})

    async def aclose(self) -> None:
        self.close_count += 1


@dataclass
class _Plugin:
    plugin_id: str
    blocks: tuple[tuple[str, str], ...]
    permissions: tuple[str, ...]
    healthy: bool = True

    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            plugin_id=self.plugin_id,
            plugin_version="0.1.0",
            plugin_api_version=AKANE_PLUGIN_API_VERSION,
            permissions=self.permissions,
        )

    def register(self, registrar: Any) -> None:
        for block_id, text in self.blocks:
            registrar.add_prompt_block(block_id, text)
        registrar.add_capability_adapter(_Adapter(self.plugin_id, healthy=self.healthy))


def _entry_point(plugin: _Plugin) -> _EntryPoint:
    return _EntryPoint(plugin.plugin_id, lambda: plugin)


def _host(*plugins: _Plugin, selection_order: tuple[str, ...] | None = None) -> PluginHost:
    selected = selection_order or tuple(plugin.plugin_id for plugin in plugins)
    return PluginHost(
        tuple(PluginSelection(plugin_id=plugin_id, enabled=True) for plugin_id in selected),
        contribution_policy=TrustedStatefulPluginContributionPolicy(),
        entry_points_provider=lambda: tuple(_entry_point(plugin) for plugin in plugins),
    )


def _build_final_context(
    builder: PromptBuilder,
    *,
    message: str,
    prompt_scope: str = "",
) -> dict[str, Any]:
    return builder.build_final_generation_context(
        now_ts=1_712_400_000,
        raw_text=message,
        current_message_text=message,
        episodic_summary_text="",
        semantic_summary_text="",
        memory_text="",
        current_visual_context="",
        resource_context="",
        extra_context="",
        visual_defaults={
            "major": "home",
            "minor": "room",
            "background": "morning",
            "bgm": "",
            "outfit": "default",
            "emotion": "normal",
        },
        allow_tool_call=True,
        tool_prompt_context="tools",
        debug_enabled=False,
        prompt_scope=prompt_scope,
        current_message_in_raw=prompt_scope == "plugin_proactive",
    )


class PluginPromptContributionPolicyTests(unittest.TestCase):
    def test_prompt_permission_is_optional_but_requires_trusted_base_permissions(self) -> None:
        policy = TrustedStatefulPluginContributionPolicy()
        allowed = PluginManifest(
            plugin_id="akane.test.prompt",
            plugin_version="0.1.0",
            plugin_api_version=AKANE_PLUGIN_API_VERSION,
            permissions=BASE_PERMISSIONS + (SYSTEM_PROMPT_CONTRIBUTION_PERMISSION,),
        )
        prompt_only = PluginManifest(
            plugin_id="akane.test.prompt",
            plugin_version="0.1.0",
            plugin_api_version=AKANE_PLUGIN_API_VERSION,
            permissions=(SYSTEM_PROMPT_CONTRIBUTION_PERMISSION,),
        )

        self.assertTrue(policy.validate_manifest(allowed).accepted)
        self.assertFalse(policy.validate_manifest(prompt_only).accepted)


class PluginPromptContributionHostTests(unittest.IsolatedAsyncioTestCase):
    async def test_permission_is_required_and_failed_registration_publishes_nothing(self) -> None:
        plugin = _Plugin(
            plugin_id="akane.test.prompt",
            blocks=(("research", "stable research method"),),
            permissions=BASE_PERMISSIONS,
        )
        host = _host(plugin)

        status = await host.start()

        self.assertEqual(status["status"], "degraded")
        self.assertEqual(status["plugins"][0]["reason"], "plugin_registration_failed")
        self.assertEqual(status["prompt_block_count"], 0)
        self.assertEqual(host.stable_system_prompt_blocks(), ())
        await host.stop()

    async def test_later_activation_failure_does_not_publish_staged_blocks(self) -> None:
        plugin = _Plugin(
            plugin_id="akane.test.prompt",
            blocks=(("research", "stable research method"),),
            permissions=BASE_PERMISSIONS + (SYSTEM_PROMPT_CONTRIBUTION_PERMISSION,),
            healthy=False,
        )
        host = _host(plugin)

        status = await host.start()

        self.assertEqual(status["plugins"][0]["reason"], "plugin_health_unavailable")
        self.assertEqual(status["prompt_block_count"], 0)
        self.assertEqual(host.stable_system_prompt_blocks(), ())
        await host.stop()

    async def test_blocks_use_stable_plugin_and_block_key_order_then_clear_on_stop(self) -> None:
        first = _Plugin(
            plugin_id="akane.test.alpha",
            blocks=(
                ("z-risk", "alpha risk rules\r\nwith evidence"),
                ("a-research", "alpha research method"),
            ),
            permissions=BASE_PERMISSIONS + (SYSTEM_PROMPT_CONTRIBUTION_PERMISSION,),
        )
        second = _Plugin(
            plugin_id="akane.test.zeta",
            blocks=(("research", "zeta research method"),),
            permissions=BASE_PERMISSIONS + (SYSTEM_PROMPT_CONTRIBUTION_PERMISSION,),
        )
        host = _host(
            second,
            first,
            selection_order=(second.plugin_id, first.plugin_id),
        )

        status = await host.start()

        self.assertEqual(status["status"], "active")
        self.assertEqual(status["prompt_block_count"], 3)
        self.assertEqual(
            host.stable_system_prompt_blocks(),
            (
                "alpha research method",
                "alpha risk rules\nwith evidence",
                "zeta research method",
            ),
        )

        stopped = await host.stop()

        self.assertEqual(stopped["prompt_block_count"], 0)
        self.assertEqual(host.stable_system_prompt_blocks(), ())

    async def test_active_host_blocks_reach_normal_and_proactive_generation_contexts(self) -> None:
        plugin = _Plugin(
            plugin_id="akane.test.prompt",
            blocks=(("research", "stable research method"),),
            permissions=BASE_PERMISSIONS + (SYSTEM_PROMPT_CONTRIBUTION_PERMISSION,),
        )
        host = _host(plugin)
        builder = PromptBuilder(
            load_persona_config(),
            stable_system_blocks_provider=host.stable_system_prompt_blocks,
        )

        before_start = _build_final_context(builder, message="User: before start")
        await host.start()
        normal = _build_final_context(builder, message="User: normal conversation")
        proactive = _build_final_context(
            builder,
            message="event.finance: market news",
            prompt_scope="plugin_proactive",
        )

        self.assertEqual(before_start["system_extra_blocks"], [])
        self.assertEqual(normal["system_extra_blocks"], ["stable research method"])
        self.assertEqual(proactive["system_extra_blocks"], ["stable research method"])
        self.assertEqual(
            normal["stable_system_context_hash"],
            proactive["stable_system_context_hash"],
        )

        await host.stop()
        after_stop = _build_final_context(builder, message="User: after stop")
        self.assertEqual(after_stop["system_extra_blocks"], [])

    async def test_invalid_or_unbounded_prompt_blocks_fail_activation(self) -> None:
        cases = (
            (("Invalid", "text"),),
            (("valid", "bad\x00text"),),
            (("same", "first"), ("same", "second")),
            tuple((f"block-{index}", "text") for index in range(9)),
            (
                ("first", "a" * 16_000),
                ("second", "b" * 16_000),
                ("third", "c"),
            ),
        )
        permissions = BASE_PERMISSIONS + (SYSTEM_PROMPT_CONTRIBUTION_PERMISSION,)

        for index, blocks in enumerate(cases):
            with self.subTest(index=index):
                plugin = _Plugin(
                    plugin_id="akane.test.prompt",
                    blocks=blocks,
                    permissions=permissions,
                )
                host = _host(plugin)

                status = await host.start()

                self.assertEqual(status["plugins"][0]["reason"], "plugin_registration_failed")
                self.assertEqual(host.stable_system_prompt_blocks(), ())
                await host.stop()


if __name__ == "__main__":
    unittest.main()
