"""Real market install, image bytes and HTTP fixture; never paid inference."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import time
from types import SimpleNamespace
import unittest

from capcore import InvocationContext
from PIL import Image

from companion_v01.engine import AkaneMemoryEngine
from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.capability_registry import ExecutorBroker
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_tool_jobs import HostToolJobRuntime
from companion_v01.image_materials import SessionImageMaterialResolver
from companion_v01.legacy_tool_prompt import render_legacy_json_tool_instruction
from companion_v01.mode_profiles import ModeProfileRegistry
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.plugin_api import PluginConnectionResult
from companion_v01.plugin_connections import ModelServicePluginConnectionProvider
from companion_v01.runtime_settings import BotSettingsView
from companion_v01.tool_runtime import LoadMaterialToolHandler, ToolExecutionContext
from tests import test_media_convert_plugin as delivery_acceptance
from tests.dependency_supply_fixture import wheelhouse_for_plugins
from tests.image_plugin_harness import ImageHarness, ROOT, PLUGIN_ID, CAPABILITY_ID
from plugins.akane_image_generation.tests.test_client import server, picture, encoded, events, final


IMAGE_DEPENDENCY_WHEELHOUSE = wheelhouse_for_plugins("akane_image_generation")


class ImageInstallationTests(unittest.IsolatedAsyncioTestCase):
    async def test_installed_http_jobs_drain_then_report_real_terminal(self):
        replies = [
            {"body": {"data": [{"b64_json": encoded(picture())}, {"b64_json": encoded(picture("red"))}]}},
            {"block": True, "wait_seconds": 30},
            {"block": True, "disconnect": True, "wait_seconds": 30},
        ]
        with tempfile.TemporaryDirectory() as temporary, server(*replies) as (url, calls, waiting, release, finished):
            root = Path(temporary)

            async def resolve(name, *, invocation):
                return PluginConnectionResult(
                    True,
                    "configured",
                    base_url=url,
                    api_key="fixture-key",
                    model="fixture-model",
                    options={"allow_loopback_http": True, "transient_retry_count": 0},
                )

            harness = await ImageHarness(
                root, SimpleNamespace(resolve=resolve), dependency_wheelhouse=IMAGE_DEPENDENCY_WHEELHOUSE
            ).start()
            await harness.install()
            self.assertTrue(harness.approve_test_profile()["ok"])
            engine = harness.engine
            engine.executor_broker = ExecutorBroker(None)
            engine._tool_hook_result_status = AkaneMemoryEngine._tool_hook_result_status
            engine._resolve_client_protocol_context = ModeProfileRegistry().resolve_from_payload
            jobs, runner = HostJobStore(root / "jobs.db"), BackgroundTaskRunner({"host-jobs": 1})
            completed = []
            runtime = HostToolJobRuntime(
                engine=engine,
                store=jobs,
                background_tasks=runner,
                conversation_ref_issuer=lambda _: "test-conversation",
                terminal_callback=lambda job: completed.append(job) or True,
            )
            owner = HostJobOwner("owner", "session")

            def submit(name, **options):
                accepted = runtime.submit(
                    capability_id=CAPABILITY_ID,
                    invocation_id=name,
                    call={"type": CAPABILITY_ID, "arguments": {"prompt": "test image", **options}},
                    context=ToolExecutionContext("owner", "session", 1, {}, client_mode="qq_text"),
                )
                return accepted.stream_events[0]["job_id"]

            async def settle():
                self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=30))
                self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-job-completions", timeout=5))

            try:
                start = time.monotonic()
                success = submit("image-job", n=2)
                self.assertLess(time.monotonic() - start, 1)
                await settle()
                self.assertEqual(jobs.get(success, owner=owner).status, "succeeded")
                self.assertEqual(len(jobs.get(success, owner=owner).artifacts), 2)
                self.assertEqual(submit("image-job", n=2), success)
                self.assertEqual(len(calls), 1)
                finished.clear()
                baseline = harness.files.store.list_generated_files(
                    profile_user_id="owner", session_id="session", limit=100
                )
                cancelled = submit("cancel-image")
                self.assertTrue(await asyncio.to_thread(waiting.wait, 5))
                disabling = asyncio.create_task(harness.service.set_enabled(plugin_id=PLUGIN_ID, enabled=False))
                for _ in range(500):
                    if CAPABILITY_ID not in harness.handlers():
                        break
                    await asyncio.sleep(0.01)
                self.assertNotIn(CAPABILITY_ID, harness.handlers())
                self.assertFalse(jobs.request_cancel(cancelled, owner=HostJobOwner("other", "session"))["ok"])
                self.assertTrue(jobs.request_cancel(cancelled, owner=owner)["ok"])
                self.assertTrue(jobs.request_cancel(cancelled, owner=owner)["ok"])
                await asyncio.sleep(0.3)
                self.assertTrue((await disabling)["ok"])
                self.assertEqual(harness.runtime.status_snapshot()["cleanup_status"], "draining")
                self.assertEqual(jobs.get(cancelled, owner=owner).status, "running")
                self.assertTrue(jobs.get(cancelled, owner=owner).cancel_requested)
                self.assertFalse(finished.is_set())
                release.set()
                await settle()
                await harness.runtime._active.drain_retired()
                self.assertEqual(harness.runtime.status_snapshot()["cleanup_status"], "completed")
                self.assertEqual(jobs.get(cancelled, owner=owner).status, "cancelled")
                self.assertEqual(jobs.get(cancelled, owner=owner).artifacts, ())
                self.assertTrue((await harness.service.set_enabled(plugin_id=PLUGIN_ID, enabled=True))["ok"])
                waiting.clear()
                finished.clear()
                release.clear()
                unconfirmed = submit("unconfirmed-image")
                self.assertTrue(await asyncio.to_thread(waiting.wait, 5))
                self.assertTrue(jobs.request_cancel(unconfirmed, owner=owner)["ok"])
                await asyncio.sleep(0.3)
                release.set()
                await settle()
                failed = jobs.get(unconfirmed, owner=owner)
                self.assertEqual(failed.status, "failed")
                self.assertEqual(failed.last_error, "remote_completion_unconfirmed")
                self.assertEqual(
                    harness.files.store.list_generated_files(profile_user_id="owner", session_id="session", limit=100),
                    baseline,
                )
                self.assertEqual([job.job_id for job in completed], [success, cancelled, unconfirmed])
                self.assertEqual(runtime.recover(), 0)
                self.assertFalse(list((root / "workers").glob("**/outbox/**/*.*")))
                for index in range(2):
                    delivery_acceptance.MediaPluginInstallationTests.verify_job_memory_and_delivery(
                        self,
                        root=root,
                        files=harness.files,
                        completed=completed,
                        artifact_index=index,
                        expected_extension="png",
                    )
            finally:
                release.set()
                await asyncio.to_thread(runner.close, timeout=10)
                await harness.close()

    def snapshot(self, harness, installed):
        snapshots = []
        for mode in ("desktop_pet", "qq_text"):
            client = ModeProfileRegistry().resolve_from_payload({"client_mode": mode})
            handlers = harness.engine._resolve_tool_handlers(
                client_context=client, profile_user_id="owner", session_id="session"
            )
            prompt = AkaneMemoryEngine._build_tool_prompt_context(
                harness.engine,
                allow_tool_call=True,
                client_context=client,
                profile_user_id="owner",
                session_id="session",
            )
            self.assertEqual(CAPABILITY_ID in handlers, installed)
            self.assertEqual(CAPABILITY_ID in prompt, installed)
            snapshots.append(json.dumps(build_openai_native_tool_specs(handlers), sort_keys=True))
        return snapshots

    def images(self, harness, result, count):
        self.assertEqual(result.state_updates["adapter_capability_status"], "ok", result)
        self.assertEqual(result.state_updates["plugin_result_experience"], "projected")
        ready = [e for e in result.stream_events if e["type"] == "generated_file_ready"]
        self.assertEqual(len(ready), count)
        found = []
        for item in ready:
            self.assertFalse(item["send_to_user"])
            handle = item["generated_file"]["generated_handle"]
            data = Path(harness.resolve(handle)["absolute_path"]).read_bytes()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                self.assertEqual(image.size, (8, 8))
            found.append((handle, data))
        self.assertNotIn("fixture-key", str(result))
        self.assertNotIn(str(harness.root), result.followup_context)
        self.assertFalse(list((harness.root / "workers").glob("**/outbox/**/*.*")))
        if (harness.root / "copies").exists():
            self.assertFalse(list((harness.root / "copies").iterdir()))
        return found

    async def test_real_installed_generation_edits_settings_and_lifecycle(self):
        blue, red = picture(), picture("red")
        replies = [
            {"body": events(final(blue, 0), final(red, 1)), "mime": "text/event-stream"},
            {"body": {"data": [{"b64_json": encoded(red)}]}},
            {"body": {"data": [{"b64_json": encoded(picture(fmt="JPEG"))}]}},
            {"body": {"data": [{"b64_json": encoded(picture(fmt="WEBP"))}]}},
            {"status": 401},
            {},
        ]
        with tempfile.TemporaryDirectory() as temporary, server(*replies) as (url, calls, *_):
            root = Path(temporary)
            engine = SimpleNamespace(
                settings=BotSettingsView(
                    image_generation_enabled=True,
                    image_generation_base_url=url,
                    image_generation_api_key="fixture-key",
                    image_generation_model="fixture-model",
                )
            )
            original = ModelServicePluginConnectionProvider(engine, SimpleNamespace())
            resolution_calls = []

            async def resolve(name, *, invocation):
                resolution_calls.append(name)
                result = await original.resolve(name, invocation=invocation)
                return (
                    replace(result, options={**result.options, "allow_loopback_http": True, "transient_retry_count": 0})
                    if result.ok
                    else result
                )

            harness = await ImageHarness(
                root, SimpleNamespace(resolve=resolve), dependency_wheelhouse=IMAGE_DEPENDENCY_WHEELHOUSE
            ).start()
            try:
                self.snapshot(harness, False)
                await harness.install()
                self.assertFalse(calls)
                self.assertFalse(resolution_calls, "Staging/health must not access credentials")
                denied = await harness.invoke(prompt="requires normal approval")
                self.assertEqual(denied.state_updates["adapter_capability_status"], "approval_required")
                self.assertFalse(calls)
                self.assertFalse(resolution_calls)
                self.assertTrue(harness.approve_test_profile()["ok"])
                self.assertTrue(harness.approve_test_profile("other")["ok"])
                stable = self.snapshot(harness, True)
                handler = harness.handlers()[CAPABILITY_ID]
                self.assertEqual(handler.background_job_policy(), ("agent", "timeline"))
                self.assertEqual(
                    handler.build_prompt_instruction(), render_legacy_json_tool_instruction(handler.tool_spec(), argument_envelope=True)
                )
                self.assertEqual(harness.runtime.stable_system_prompt_blocks(), ())
                outputs = self.images(harness, await harness.invoke(prompt="mugs", n=2), 2)
                self.assertEqual([item[1] for item in outputs], [blue, red])
                material = LoadMaterialToolHandler(
                    image_material_resolver=SessionImageMaterialResolver(
                        attachment_service=harness.attachments, generated_file_service=harness.files
                    )
                ).execute(
                    call={"type": "load_material", "targets": [outputs[0][0]]},
                    context=ToolExecutionContext("owner", "session", 1, {}),
                )
                self.assertEqual(len(material.model_image_inputs), 1)
                self.assertEqual(material.model_image_inputs[0]["data_url"], "data:image/png;base64," + encoded(blue))
                self.assertNotIn("base64", material.followup_context)
                self.assertNotIn("data_url", str(material.stream_events))
                edited = self.images(
                    harness, await harness.invoke(prompt="red mug", reference_images=[outputs[0][0]]), 1
                )
                self.assertEqual(edited[0][1], red)
                self.assertEqual(Path(harness.resolve(outputs[0][0])["absolute_path"]).read_bytes(), blue)
                (root / "attachments").mkdir(exist_ok=True)
                source, mask = root / "attachments/source.png", root / "attachments/mask.png"
                source.write_bytes(blue)
                mask.write_bytes(picture(alpha=True))
                source_id, mask_id = harness.register_image(source), harness.register_image(mask)
                before = hashlib.sha256(source.read_bytes()).hexdigest()
                self.images(
                    harness,
                    await harness.invoke(
                        prompt="masked edit",
                        reference_images=[source_id],
                        mask_image=mask_id,
                        output_format="jpeg",
                        compression=0,
                    ),
                    1,
                )
                self.assertIn(b'name="mask"', calls[-1][2])
                self.assertIn(b'name="output_compression"\r\n\r\n0', calls[-1][2])
                engine.settings = replace(engine.settings, image_generation_api_key="rotated-fixture-key")
                self.images(harness, await harness.invoke(prompt="new webp", output_format="webp"), 1)
                self.assertEqual(calls[-1][1]["Authorization"], "Bearer rotated-fixture-key")
                self.assertEqual(self.snapshot(harness, True), stable)
                count = len(calls)
                for context in (
                    InvocationContext("other", "session", "desktop"),
                    InvocationContext("owner", "other", "desktop"),
                ):
                    result = await harness.service.invoke_capability(
                        CAPABILITY_ID, {"prompt": "x", "reference_images": [source_id]}, context=context
                    )
                    self.assertTrue(result.is_error)
                    self.assertEqual(result.reason, "image_resource_unavailable")
                for args in (
                    {"reference_images": ["gen_9999"]},
                    {"mask_image": source_id, "reference_images": [source_id]},
                    {"reference_images": [str(source)]},
                ):
                    result = await harness.invoke(prompt="bad", **args)
                    self.assertNotIn("generated_file_ready", str(result.stream_events))
                self.assertEqual(len(calls), count)
                engine.settings = replace(engine.settings, image_generation_enabled=False)
                result = await harness.invoke(prompt="disabled")
                self.assertEqual(result.state_updates["adapter_capability_reason"], "image_provider_disabled")
                self.assertEqual(len(calls), count)
                engine.settings = replace(engine.settings, image_generation_enabled=True)
                result = await harness.invoke(prompt="provider denied")
                self.assertEqual(
                    result.state_updates["adapter_capability_reason"], "provider_auth_or_network_forbidden"
                )
                self.assertNotIn("generated_file_ready", str(result.stream_events))
                bad_source = root / "bad-source"
                shutil.copytree(
                    ROOT / "plugins/akane_image_generation",
                    bad_source,
                    ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
                )
                codec = bad_source / "src/akane_image_generation/types.py"
                codec.write_text(
                    codec.read_text(encoding="utf-8").replace(
                        'for fmt in ("PNG", "JPEG", "WEBP"):', 'for fmt in ("NOT_A_CODEC",):'
                    ),
                    encoding="utf-8",
                )
                bad = await harness.service.stage_source(source_path=str(bad_source))
                self.assertTrue(bad["ok"], bad)
                rejected = await harness.service.install_stage(stage_id=bad["stage_id"],
                    approved_permissions=bad["permissions"])
                self.assertFalse(rejected["ok"], rejected)
                self.assertEqual(rejected["reason"], "image_codec_unavailable")
                self.assertIn(CAPABILITY_ID, harness.runtime.capability_ids)
                self.images(harness, await harness.invoke(prompt="still active"), 1)
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
                self.assertTrue((await harness.service.set_enabled(plugin_id=PLUGIN_ID, enabled=False))["ok"])
                self.snapshot(harness, False)
                self.assertTrue((await harness.service.set_enabled(plugin_id=PLUGIN_ID, enabled=True))["ok"])
                self.snapshot(harness, True)
                self.assertTrue((await harness.service.uninstall(plugin_id=PLUGIN_ID))["ok"])
                self.snapshot(harness, False)
            finally:
                await harness.close()


if __name__ == "__main__":
    unittest.main()
