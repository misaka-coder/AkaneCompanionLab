"""Installed cover plugin against explicit HTTP audio fixtures, not fake ML acceptance."""

import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from capcore import InvocationContext
from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.capability_registry import ExecutorBroker
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_tool_jobs import HostToolJobRuntime
from companion_v01.mode_profiles import ModeProfileRegistry
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.plugin_api import PluginConnectionResult
from companion_v01.tool_runtime import ToolExecutionContext
from tests.dependency_supply_fixture import wheelhouse_for_plugins
from tests.image_plugin_harness import ImageHarness
from tests import test_media_convert_plugin as delivery_acceptance
from plugins.akane_cover_song.tests.test_remote import endpoint
from plugins.akane_cover_song.tests.test_media import tone

PLUGIN_ID = "akane.cover-song"
CAPABILITY_ID = PLUGIN_ID + ".run.v1"
DEPENDENCY_WHEELHOUSE = wheelhouse_for_plugins("akane_cover_song")


class CoverHarness(ImageHarness):
    plugin_id = PLUGIN_ID
    capability_id = CAPABILITY_ID

    def __init__(self, root, connection_provider=None):
        super().__init__(root, connection_provider, dependency_wheelhouse=DEPENDENCY_WHEELHOUSE)

    def register_audio(self, path):
        return self.register_image(path, mime="audio/wav", kind="audio")


class CoverInstallationTests(unittest.IsolatedAsyncioTestCase):
    def check_discovery(self, harness, installed):
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
            self.assertNotIn("cover_song", handlers)
            self.assertNotIn("cover_song", prompt)
            specs = str(build_openai_native_tool_specs(handlers))
            self.assertEqual(CAPABILITY_ID in specs, installed)

    async def test_installed_jobs_cancel_and_uncertain_remote_completion(self):
        with tempfile.TemporaryDirectory() as tmp, endpoint(wait_seconds=30) as (server, url):
            root = Path(tmp)

            async def resolve(name, *, invocation):
                return PluginConnectionResult(
                    True,
                    "configured",
                    base_url=url,
                    model="Fixture.pth",
                    options={"backend": "remote", "default_output_format": "wav"},
                )

            harness = await CoverHarness(root, SimpleNamespace(resolve=resolve)).start()
            await harness.install()
            self.assertTrue(harness.approve_test_profile()["ok"])
            (root / "attachments").mkdir(exist_ok=True)
            source = root / "attachments" / "source.wav"
            tone(source)
            server.payload = source.read_bytes()
            handle = harness.register_audio(source)
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

            def submit(name):
                result = runtime.submit(
                    capability_id=CAPABILITY_ID,
                    invocation_id=name,
                    call={
                        "type": CAPABILITY_ID,
                        "arguments": {"source_id": handle, "song_title": "任务翻唱", "force_rebuild": True},
                    },
                    context=ToolExecutionContext("owner", "session", 1, {}, client_mode="qq_text"),
                )
                return result.stream_events[0]["job_id"]

            async def settle():
                self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=30))
                self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-job-completions", timeout=5))

            try:
                success = submit("cover-success")
                await settle()
                job = jobs.get(success, owner=owner)
                self.assertEqual(job.status, "succeeded", job)
                self.assertEqual(len(job.artifacts), 1)
                self.assertEqual(submit("cover-success"), success)
                self.assertEqual(len(server.requests), 1)
                server.started.clear()
                server.release.clear()
                cancelled = submit("cover-cancel")
                self.assertTrue(await asyncio.to_thread(server.started.wait, 5))
                disabling = asyncio.create_task(harness.service.set_enabled(plugin_id=PLUGIN_ID, enabled=False))
                for _ in range(300):
                    if CAPABILITY_ID not in harness.handlers():
                        break
                    await asyncio.sleep(0.01)
                self.assertNotIn(CAPABILITY_ID, harness.handlers())
                self.assertTrue(jobs.request_cancel(cancelled, owner=owner)["ok"])
                self.assertTrue(jobs.request_cancel(cancelled, owner=owner)["ok"])
                await asyncio.sleep(0.2)
                disabled = await disabling
                self.assertTrue(disabled["ok"], disabled)
                self.assertEqual(jobs.get(cancelled, owner=owner).status, "running")
                server.release.set()
                await settle()
                self.assertTrue((await disabling)["ok"])
                self.assertEqual(jobs.get(cancelled, owner=owner).status, "cancelled")
                self.assertEqual(jobs.get(cancelled, owner=owner).artifacts, ())
                self.assertTrue((await harness.service.set_enabled(plugin_id=PLUGIN_ID, enabled=True))["ok"])
                server.started.clear()
                server.release.clear()
                uncertain = submit("cover-uncertain")
                self.assertTrue(await asyncio.to_thread(server.started.wait, 5))
                self.assertTrue(jobs.request_cancel(uncertain, owner=owner)["ok"])
                await asyncio.sleep(0.2)
                server.disconnect = True
                server.release.set()
                await settle()
                failed = jobs.get(uncertain, owner=owner)
                self.assertEqual(failed.status, "failed")
                self.assertEqual(failed.last_error, "rvc_remote_completion_unconfirmed")
                self.assertEqual(failed.artifacts, ())
                self.assertEqual(len(completed), 3)
                self.assertEqual(runtime.recover(), 0)
                delivery_acceptance.MediaPluginInstallationTests.verify_job_memory_and_delivery(
                    self, root=root, files=harness.files, completed=completed, expected_extension="wav"
                )
            finally:
                server.release.set()
                await asyncio.to_thread(runner.close, timeout=10)
                await harness.close()

    async def test_installed_cache_context_delivery_and_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp, endpoint() as (server, url):
            root = Path(tmp)
            resolutions = []

            async def resolve(name, *, invocation):
                resolutions.append((name, invocation.context.profile_user_id))
                return PluginConnectionResult(
                    True,
                    "configured",
                    base_url=url,
                    model="Fixture.pth",
                    options={"backend": "remote", "default_output_format": "wav"},
                )

            harness = await CoverHarness(root, SimpleNamespace(resolve=resolve)).start()
            try:
                self.check_discovery(harness, False)
                await harness.install()
                self.assertFalse(resolutions, "Registration/health must not read private connections")
                self.assertTrue(harness.approve_test_profile()["ok"])
                self.check_discovery(harness, True)
                (root / "attachments").mkdir(exist_ok=True)
                source = root / "attachments" / "source.wav"
                tone(source)
                server.payload = source.read_bytes()  # deliberately mono historical/old-server output
                handle = harness.register_audio(source)

                async def invoke(**kwargs):
                    return await harness.invoke(source_id=handle, song_title="测试曲", **kwargs)

                def ready(result):
                    self.assertEqual(result.state_updates["adapter_capability_status"], "ok", result)
                    events = [e for e in result.stream_events if e["type"] == "generated_file_ready"]
                    self.assertEqual(len(events), 1, result)
                    item = events[0]
                    path = Path(harness.resolve(item["generated_file"]["generated_handle"])["absolute_path"])
                    self.assertEqual(path.read_bytes(), server.payload)
                    self.assertNotIn(str(root), result.followup_context)
                    self.assertIn("provider_output_audio_format_changed", result.followup_context)
                    return item

                result = await invoke(delivery="none")
                first = ready(result)
                self.assertFalse(first["send_to_user"])
                generated_handle = first["generated_file"]["generated_handle"]
                ready(await harness.invoke(source_id=generated_handle, song_title="生成音频再输入", delivery="none"))
                denied = await harness.runtime.invoke(
                    CAPABILITY_ID,
                    {"source_id": generated_handle, "song_title": "越界", "delivery": "none"},
                    context=InvocationContext("owner", "other-session", "desktop_pet"),
                )
                self.assertTrue(denied.is_error)
                self.assertEqual(len(server.requests), 1)
                self.assertTrue(ready(await invoke())["send_to_user"])
                handler = harness.handlers()[CAPABILITY_ID]
                qq = await asyncio.to_thread(
                    handler.execute,
                    call=handler.normalize_call({"type": CAPABILITY_ID, "song_title": "测试曲"}),
                    context=ToolExecutionContext("owner", "session", 1, {}, client_mode="qq_text"),
                )
                self.assertEqual(ready(qq)["delivery_mode"], "voice")
                both = ready(await invoke(delivery="both"))
                self.assertEqual(both["delivery_mode"], "both")
                self.assertEqual(len(server.requests), 1, "Source cache must avoid inference")
                cached = ready(await harness.invoke(song_title="测试曲", delivery="none"))
                self.assertEqual(cached["delivery_mode"], "file")
                self.assertEqual(len(server.requests), 1)
                await invoke(force_rebuild=True, delivery="none")
                self.assertEqual(len(server.requests), 2)
                bad = await invoke(pitch_shift=99)
                self.assertNotEqual(bad.state_updates["adapter_capability_status"], "ok")
                self.assertEqual(len(server.requests), 2)
                # Cache is scoped by trusted profile, even if the same title is known.
                result = await harness.runtime.invoke(
                    CAPABILITY_ID,
                    {"song_title": "测试曲", "output_format": "wav"},
                    context=InvocationContext("other", "session", "qq_text"),
                )
                self.assertTrue(result.is_error)
                self.assertNotEqual(result.reason, "")
                self.assertTrue((await harness.service.set_enabled(plugin_id=PLUGIN_ID, enabled=False))["ok"])
                self.check_discovery(harness, False)
                self.assertTrue((await harness.service.set_enabled(plugin_id=PLUGIN_ID, enabled=True))["ok"])
                self.check_discovery(harness, True)
                ready(await harness.invoke(song_title="测试曲", delivery="none"))
                self.assertEqual(len(server.requests), 2)
                malformed = await invoke(output_format="mp3", delivery="none")
                self.assertEqual(malformed.state_updates["adapter_capability_reason"], "cover_output_format_mismatch")
                self.assertFalse([e for e in malformed.stream_events if e["type"] == "generated_file_ready"])
                self.assertEqual(len(server.requests), 3)
                self.assertTrue((await harness.service.uninstall(plugin_id=PLUGIN_ID))["ok"])
                self.check_discovery(harness, False)
                self.assertFalse(list((root / "workers").glob("**/outbox/**/*.*")))
            finally:
                await harness.close()
