"""Public observations through admission, real worker RPC and model context."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import tempfile
import textwrap
from typing import Any
import unittest
from unittest.mock import AsyncMock, patch

from akane_plugin import PluginInvocationContext as InvocationContext
from akane_plugin import EventBinding, ObservationReceipt, Plugin, ToolContext
from companion_v01.engine_services import response_builder
from companion_v01.instance_profile import PluginSelection
from companion_v01.plugin_active_generation import ActivePluginGeneration, PluginGenerationSnapshot
from companion_v01.plugin_generation import PluginGenerationProcess
from companion_v01.plugin_managed_artifacts import GeneratedFileManagedArtifactSink
from companion_v01.plugin_resources import ResourceInvocation
from companion_v01.prompt_profiles import PromptModule
from companion_v01.prompt_builder import PromptBuilder
from companion_v01.persona_config import load_persona_config
from tests import test_plugin_events_v2 as event_tests
from tests.test_memcore_integration import _PromptContextEngine, _PromptContextMemcoreManager


CONTEXT = InvocationContext("owner", "session", "web", character_pack_id="akane")


def observer(permission=True):
    plugin = Plugin("example.observer", permissions=("context.observe",) if permission else ())

    @plugin.tool
    async def update(key: str, data: Any, ctx: ToolContext) -> ObservationReceipt:
        return await ctx.observe(key, data)

    return plugin


class ObservationTests(unittest.IsolatedAsyncioTestCase):
    start = event_tests.PluginEventV2Tests.start
    terminal = event_tests.PluginEventV2Tests.terminal

    async def update(self, data, key="board", context=CONTEXT):
        result = await self.host.invoke("example.observer.update", {"key": key, "data": data}, context=context)
        self.assertIsInstance(result.content, dict, result)
        self.assertIn("version", result.content, result)
        return result.content

    def prompt(self, context=CONTEXT):
        return self.broker.observations.prompt_context(profile_user_id=context.profile_user_id,
            session_id=context.session_id, character_pack_id=context.character_pack_id)

    async def test_typed_latest_isolated_and_real_model_context_is_frozen(self):
        await self.start(observer())
        first = await self.update({"turn": 0, "done": False, "nested": [None, 0.5, {"move": "A1"}]})
        self.assertEqual(first["status"], "observed")
        frozen = self.prompt()
        self.assertIn('"done": false', frozen)
        self.assertIn('null', frozen)
        for context in (InvocationContext("other", "session", "web", character_pack_id="akane"),
                        InvocationContext("owner", "other", "web", character_pack_id="akane"),
                        InvocationContext("owner", "session", "web", character_pack_id="other")):
            self.assertEqual(self.prompt(context), "")
        manager = _PromptContextMemcoreManager({})
        manager.enabled = manager.available = False
        engine = _PromptContextEngine(memcore_manager=manager)
        profile = engine._get_prompt_profile_registry().resolve(None)
        profile.includes = lambda module: module == PromptModule.EXTRA_CONTEXT
        engine._get_prompt_profile_registry = lambda: type("Registry", (), {"resolve": lambda *args, **kwargs: profile})()
        engine.plugin_observations_provider = self.broker.observations.prompt_context
        def prepare():
            with patch.object(response_builder, "_memory_backend", return_value="legacy"):
                return response_builder.prepare_context(engine, session_id="session", profile_user_id="owner",
                    character_pack_id="akane", user_message="next move", recent_raw=[],
                    recent_episodic_summaries=[], recent_semantic_summaries=[], confirmed_snippets=[], now_ts=100)
        before = prepare()
        provider_context = PromptBuilder(load_persona_config()).build_final_generation_context(**engine.prompt_builder.kwargs)
        self.assertIn("A1", str(provider_context["ephemeral_turns"]))
        self.assertIn('"move": "A1"', json.dumps(before["ephemeral_turns"], ensure_ascii=False).replace('\\"', '"'))
        self.assertNotIn("插件当前观察", engine.prompt_builder.kwargs["extra_context"])
        second = await self.update(False)
        self.assertGreater(second["version"], first["version"])
        self.assertTrue(self.prompt().endswith("\nfalse"))
        self.assertIn('"move": "A1"', frozen)
        self.assertIn("A1", str(before["ephemeral_turns"]))
        after = prepare()
        self.assertNotIn("A1", str(after["ephemeral_turns"]))
        self.assertIn("false", str(after["ephemeral_turns"]))
        self.assertEqual((await self.update(0, key="棋盘/当前状态"))["status"], "observed")
        self.assertIn("棋盘/当前状态", self.prompt())
        self.engine.llm.assert_not_called()
        await self.host.stop()
        self.assertEqual(self.prompt(), "")

    async def test_permission_global_scope_invalid_json_and_private_values_rejected(self):
        await self.start(observer(False))
        self.assertEqual((await self.update(None))["reason"], "context_observe_permission_required")
        await self.host.stop()
        await self.start(observer())
        result = await self.update(0, context=InvocationContext())
        self.assertEqual(result["reason"], "context_unbound")
        self.assertEqual((await self.update(0, key=""))["reason"], "observation_key_invalid")
        # Exercise the parent authority with a payload that bypassed local SDK
        # validation, as a compromised worker could do.
        invocation = ResourceInvocation("example.observer", CONTEXT, generation_id=str(self.host._generation),
                                        can_observe_context=True, private_values={"fixture-private-key"})
        for data, reason in ((float("nan"), "plugin_result_non_finite_number"),
                             ({"business": "fixture-private-key"}, "plugin_result_private_data")):
            reply = await self.broker.request("observe", {"key": "bad", "data": data}, invocation=invocation)
            self.assertEqual(reply["reason"], reason)
        invocation.revoke()
        reply = await self.broker.request("observe", {"key": "bad", "data": 1}, invocation=invocation)
        self.assertEqual(reply["reason"], "event_invocation_expired")
        self.assertEqual(self.prompt(), "")

    async def test_bound_event_observes_own_context_unbind_revokes_and_global_cannot_borrow_origin(self):
        plugin = observer()

        @plugin.tool
        async def bind(ctx: ToolContext) -> EventBinding:
            return await ctx.events.bind("example.observer.bound")

        @plugin.tool
        async def unbind(scope_id: str, ctx: ToolContext) -> EventBinding:
            return await ctx.events.unbind(scope_id)

        @plugin.on("test.board", name="bound", scope="conversation")
        async def bound(event, ctx):
            return (await ctx.observe("board", event.data)).as_dict()

        @plugin.on("test.board", name="global")
        async def global_handler(event, ctx):
            return (await ctx.observe("borrowed", event.data)).as_dict()

        await self.start(plugin)
        binding = await self.host.invoke("example.observer.bind", {}, context=CONTEXT)
        other_character = InvocationContext("owner", "session", "web", character_pack_id="other")
        other_binding = await self.host.invoke("example.observer.bind", {}, context=other_character)
        self.assertNotEqual(binding.value["scope_id"], other_binding.value["scope_id"])
        event = await self.broker.emit("test.board", {"position": [0, False, None]}, context=CONTEXT)
        done = await self.terminal(event.dispatch_id)
        self.assertEqual(len(done.deliveries), 2)
        values = {item.subscription_id: item.value for item in done.deliveries}
        self.assertEqual(values["example.observer.bound"]["status"], "observed")
        self.assertEqual(values["example.observer.global"]["reason"], "context_unbound")
        self.assertIn("position", self.prompt())
        self.assertEqual(self.prompt(other_character), "")
        reply = await self.host.invoke("example.observer.unbind", {"scope_id": binding.value["scope_id"]}, context=CONTEXT)
        self.assertEqual(reply.value["status"], "unbound")
        self.assertEqual(self.prompt(), "")

    async def test_program_dependency_preserves_character_and_source_namespace(self):
        relay = Plugin("example.relay", permissions=("capability.invoke", "context.observe"))

        @relay.tool
        async def update(data: Any, ctx: ToolContext) -> dict[str, Any]:
            own = await ctx.observe("board", {"relay": True})
            called = await ctx.tools.call("example.observer.update", {"key": "board", "data": data})
            return {"own": own.as_dict(), "called": called}

        await self.start(observer(), relay)
        result = await self.host.invoke("example.relay.update", {"data": [0, False, None]}, context=CONTEXT)
        self.assertFalse(result.is_error, result)
        prompt = self.prompt()
        self.assertIn('"source": "example.observer"', prompt)
        self.assertIn('"source": "example.relay"', prompt)
        self.assertIn("false", prompt)
        self.assertEqual(self.prompt(InvocationContext("owner", "session", "web")), "")
        self.engine.llm.assert_not_called()

    async def test_large_value_uses_real_artifact_and_storage_failure_preserves_previous(self):
        await self.start(observer())
        await self.update(0)
        value = {"board": "完整观察" * 9000, "tail": [0, False, None]}
        failed = await self.update(value)
        self.assertEqual(failed["status"], "rejected")
        self.assertEqual(failed["reason"], "plugin_result_storage_unavailable")
        self.assertTrue(self.prompt().endswith("\n0"))
        self.broker.observations.sink = GeneratedFileManagedArtifactSink(self.files)
        receipt = await self.update(value)
        self.assertEqual(receipt["status"], "observed", receipt)
        prompt = self.prompt()
        handle = re.search(r"同次执行的完整 JSON 已保存为 (\S+)，", prompt).group(1)
        self.assertIn('inspect_generated_file(target="' + handle, prompt)
        artifact = self.files.resolve_generated_artifact(profile_user_id="owner", session_id="session", target=handle)
        self.assertIsNotNone(artifact)
        self.assertEqual(json.loads(Path(artifact["absolute_path"]).read_text(encoding="utf-8")), value)
        self.assertEqual(artifact["delivery_status"], "not_requested")
        inspection = self.files.inspect_generated_file(profile_user_id="owner", session_id="session",
                                                       target=handle, section="content")
        self.assertTrue(inspection["ok"], inspection)
        self.assertIsNone(self.files.resolve_generated_artifact(profile_user_id="other", session_id="session", target=handle))
        self.assertNotIn(str(self.root), prompt)
        self.engine.llm.assert_not_called()

    async def test_slow_old_material_cannot_overwrite_new_or_commit_after_revocation(self):
        await self.start(observer())
        entered, release = asyncio.Event(), asyncio.Event()
        self.addCleanup(release.set)
        async def materialize(*args, **kwargs):
            entered.set()
            await release.wait()
            return await GeneratedFileManagedArtifactSink(self.files).materialize(*args, **kwargs)
        self.broker.observations.sink = type("Sink", (), {"materialize": staticmethod(materialize)})()
        pending = asyncio.create_task(self.update("old" * 9000))
        await asyncio.wait_for(entered.wait(), 2)
        newer = await self.update(None)
        release.set()
        older = await pending
        self.assertEqual(older["status"], "superseded")
        self.assertLess(older["version"], newer["version"])
        self.assertTrue(self.prompt().endswith("\nnull"))
        entered.clear()
        release.clear()
        invocation = ResourceInvocation("example.observer", CONTEXT, generation_id=str(self.host._generation),
                                        can_observe_context=True)
        pending = asyncio.create_task(self.broker.request("observe", {"key": "late", "data": "x" * 20000}, invocation=invocation))
        await asyncio.wait_for(entered.wait(), 2)
        invocation.revoke()
        release.set()
        self.assertEqual((await pending)["reason"], "observation_scope_expired")
        self.assertNotIn('"late"', self.prompt())

    async def test_real_worker_public_sdk_and_generation_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = root / "site"
            package = site / "observation_fixture"
            metadata = site / "observation_fixture-0.1.0.dist-info"
            package.mkdir(parents=True)
            metadata.mkdir()
            (package / "__init__.py").write_text(textwrap.dedent('''
                from typing import Any
                from akane_plugin import Plugin, ToolContext, ObservationReceipt, EventBinding
                plugin = Plugin("test.observation", permissions=("context.observe",))
                @plugin.tool
                async def update(data: Any, ctx: ToolContext) -> ObservationReceipt:
                    return await ctx.observe("board", data)
                @plugin.tool
                async def bind(ctx: ToolContext) -> EventBinding:
                    return await ctx.events.bind("test.observation.bound")
                @plugin.on("test.worker-board", name="bound", scope="conversation")
                async def bound(event, ctx):
                    return await ctx.observe("board", event.data)
                def create_plugin(): return plugin
            '''), encoding="utf-8")
            (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: observation-fixture\nVersion: 0.1.0\n", encoding="utf-8")
            (metadata / "entry_points.txt").write_text("[akane.plugins.v1]\ntest.observation = observation_fixture:create_plugin\n", encoding="utf-8")
            active = ActivePluginGeneration()
            broker = active.build_event_broker()
            process = PluginGenerationProcess(project_root=Path(__file__).resolve().parents[1], site_dir=site,
                                               plugin_id="test.observation", work_dir=root / "worker")
            process.bind_events_provider(broker)
            try:
                await asyncio.to_thread(process.start)
                await active.publish(PluginGenerationSnapshot((PluginSelection("test.observation", True),), (process,)))
                result = await active.invoke("test.observation.update", {"data": [0, False, None]}, context=CONTEXT)
                self.assertFalse(result.is_error, result)
                self.assertEqual(result.value["status"], "observed", result)
                text = broker.observations.prompt_context(profile_user_id="owner", session_id="session", character_pack_id="akane")
                self.assertIn("false", text)
                self.assertIn("null", text)
                binding = await active.invoke("test.observation.bind", {}, context=CONTEXT)
                self.assertFalse(binding.is_error, binding)
                event = await broker.emit("test.worker-board", {"move": "B2", "done": False}, context=CONTEXT)
                async with asyncio.timeout(5):
                    while not (done := await broker.receipt(event.dispatch_id)).complete:
                        await asyncio.sleep(0.01)
                self.assertEqual(done.status, "completed", done)
                self.assertEqual(done.deliveries[0].value["status"], "observed")
                text = broker.observations.prompt_context(profile_user_id="owner", session_id="session", character_pack_id="akane")
                self.assertIn("B2", text)
                await active.publish(PluginGenerationSnapshot((), ()))
                self.assertEqual(broker.observations.prompt_context(profile_user_id="owner", session_id="session", character_pack_id="akane"), "")
            finally:
                await active.stop()
                if process.running:
                    await asyncio.to_thread(process.stop)
