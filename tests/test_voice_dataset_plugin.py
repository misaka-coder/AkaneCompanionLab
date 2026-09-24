"""Real market wheels, FFmpeg/PCM archives, installed dependency and Host Jobs."""

from __future__ import annotations

import asyncio
from array import array
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch
import wave
import zipfile

from capcore import InvocationContext
from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.capability_registry import ExecutorBroker
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.extension_management import ExtensionManagementService, PluginSelectionStore
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_tool_jobs import HostToolJobRuntime
from companion_v01.legacy_tool_prompt import render_legacy_json_tool_instruction
from companion_v01.mode_profiles import ModeProfileRegistry
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.plugin_capability_calls import EnginePluginCapabilityProvider
from companion_v01.plugin_generation_candidate import PluginGenerationCandidateBuilder
from companion_v01.plugin_generation_runtime import PluginGenerationRuntime
from companion_v01.plugin_installation import ManagedPluginArtifactStore
from companion_v01.plugin_managed_artifacts import GeneratedFileManagedArtifactSink
from companion_v01.plugin_market import StaticPluginMarket
from companion_v01.plugin_resources import GeneratedFileResourceProvider
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.tool_runtime import ToolExecutionContext
from scripts.build_plugin_market import build_market
from tests import test_media_convert_plugin as delivery_acceptance
from tests.test_plugin_engine_bridge import EngineFacade
from tests.test_plugin_resources import services

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "akane.voice-dataset"
CAPABILITY_ID = PLUGIN_ID + ".run.v1"


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "Real FFmpeg required")
class DatasetInstallationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = root = Path(temporary.name)
        index = await asyncio.to_thread(build_market, root / "market")
        artifacts = ManagedPluginArtifactStore(root / "artifacts", instance_id="test", project_root=ROOT)
        selections = PluginSelectionStore(root / "selections.json", defaults=(), instance_id="test")
        self.runtime = PluginGenerationRuntime(
            (),
            candidate_builder=PluginGenerationCandidateBuilder(
                source_resolver=artifacts,
                project_root=ROOT,
                work_root=root / "workers",
                plugin_storage_data_root=root / "data",
                plugin_storage_instance_id="test",
            ),
        )
        self.addAsyncCleanup(self.runtime.stop)
        _, self.attachments, self.files = services(root)
        self.runtime.bind_managed_artifact_sink(GeneratedFileManagedArtifactSink(self.files))
        self.runtime.bind_resource_provider(GeneratedFileResourceProvider(self.files, work_root=root / "copies"))
        self.engine = EngineFacade(PluginCapabilityToolBridge(self.runtime, config_base_dir=root))
        self.engine.store, self.engine.capability_config_base_dir = self.files.store, root
        self.engine._get_generated_file_service = lambda: self.files
        self.runtime.bind_capability_provider(EnginePluginCapabilityProvider(self.engine))
        self.service = ExtensionManagementService(
            plugin_runtime=self.runtime,
            selection_store=selections,
            artifact_store=artifacts,
            market=StaticPluginMarket(index),
        )
        self.source = root / "attachments/source.wav"
        self.source.parent.mkdir(exist_ok=True)
        self.make_audio(self.source)
        self.source_id = self.register(self.source)
        self.before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        await self.runtime.start()

    def make_audio(self, path, seconds=4, rate=16000, channels=1):
        values = array("h")
        for i in range(rate):
            value = int(8000 * math.sin(2 * math.pi * 330 * i / rate))
            values.extend([value] if channels == 1 else [value, -value])
        with wave.open(str(path), "wb") as audio:
            audio.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
            for _ in range(seconds):
                audio.writeframesraw(values.tobytes())

    def register(self, path):
        item = self.attachments.create_pending(
            profile_user_id="owner",
            session_id="session",
            source="test",
            kind="audio",
            origin_name=path.name,
            file_ext=path.suffix.lstrip("."),
            mime_type="audio/wav",
            storage_relpath=path.name,
            file_size=path.stat().st_size,
        )
        self.attachments.mark_ready(profile_user_id="owner", session_id="session", attachment_id=item["attachment_id"])
        return item["attachment_handle"]

    async def install(self, plugin_id):
        entry = next(e for e in (await self.service.browse_market())["plugins"] if e["plugin_id"] == plugin_id)
        staged = await self.service.stage_market(plugin_id=plugin_id, digest=entry["sha256"])
        self.assertTrue(staged["ok"], staged)
        installed = await self.service.install_stage(
            stage_id=staged["stage_id"], approved_permissions=staged["permissions"]
        )
        self.assertTrue(installed["ok"], installed)

    def snapshot(self, installed):
        snapshots = []
        for mode in ("desktop_pet", "qq_text"):
            client = ModeProfileRegistry().resolve_from_payload({"client_mode": mode})
            handlers = self.engine._resolve_tool_handlers(
                client_context=client, profile_user_id="owner", session_id="session"
            )
            prompt = AkaneMemoryEngine._build_tool_prompt_context(
                self.engine, allow_tool_call=True, client_context=client, profile_user_id="owner", session_id="session"
            )
            self.assertEqual(CAPABILITY_ID in handlers, installed)
            self.assertEqual(CAPABILITY_ID in prompt, installed)
            self.assertNotIn("prepare_voice_dataset", handlers)
            self.assertNotIn("prepare_voice_dataset", prompt)
            snapshots.append(json.dumps(build_openai_native_tool_specs(handlers), sort_keys=True))
        return snapshots

    async def invoke(self, sources, **options):
        handler = self.engine._resolve_tool_handlers()[CAPABILITY_ID]
        return await asyncio.to_thread(
            handler.execute,
            call=handler.normalize_call({"type": CAPABILITY_ID, "source_ids": sources, **options}),
            context=ToolExecutionContext("owner", "session", 1, {}, client_mode="desktop_pet"),
        )

    def archive(self, result):
        self.assertEqual(result.state_updates["adapter_capability_status"], "ok", result)
        self.assertEqual(result.state_updates["plugin_result_experience"], "projected")
        events = [e for e in result.stream_events if e["type"] == "generated_file_ready"]
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]["send_to_user"])
        handle = events[0]["generated_file"]["generated_handle"]
        resolved = self.files.resolve_input_resource(
            profile_user_id="owner", session_id="session", target=handle, timestamp=None
        )
        with zipfile.ZipFile(resolved["absolute_path"]) as archive:
            self.assertIsNone(archive.testzip())
            manifest = json.loads(archive.read("manifest.json"))
            names = [n for n in archive.namelist() if n.startswith("slices/")]
            self.assertEqual(len(names), manifest["stats"]["slice_count"])
            self.assertNotIn(str(self.root), str(manifest))
            self.assertIn("README.md", archive.namelist())
            audio = []
            for name in names:
                with wave.open(io.BytesIO(archive.read(name))) as wav:
                    audio.append((wav.getframerate(), wav.getnchannels(), wav.getnframes()))
        self.assertEqual(list((self.root / "copies").iterdir()), [])
        return manifest, audio

    async def exercise_jobs(self):
        engine, root = self.engine, self.root
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
        owner, cancelling = HostJobOwner("owner", "session"), ""

        def submit(name, source, **options):
            return runtime.submit(
                capability_id=CAPABILITY_ID,
                invocation_id=name,
                call={"type": CAPABILITY_ID, "arguments": {"source_ids": [source], **options}},
                context=ToolExecutionContext("owner", "session", 1, {}, client_mode="qq_text"),
            ).stream_events[0]["job_id"]

        async def settle():
            self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=60))
            self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-job-completions", timeout=5))

        try:
            start = time.monotonic()
            success = submit("dataset-job", self.source_id)
            self.assertLess(time.monotonic() - start, 1)
            await settle()
            self.assertEqual(jobs.get(success, owner=owner).status, "succeeded")
            self.assertEqual(submit("dataset-job", self.source_id), success)
            long = root / "attachments/long.wav"
            self.make_audio(long, seconds=600, rate=96000)
            baseline = self.files.store.list_generated_files(profile_user_id="owner", session_id="session", limit=100)
            cancelling = submit("cancel-dataset", self.register(long), target_sr=96000, profile="archive")
            for _ in range(600):
                if list((root / "copies").rglob("dataset-*/request.json")):
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(list((root / "copies").rglob("dataset-*/request.json")), "No real archive worker input")
            self.assertEqual(jobs.get(cancelling, owner=owner).status, "running")
            disabling = asyncio.create_task(self.service.set_enabled(plugin_id=PLUGIN_ID, enabled=False))
            for _ in range(1500):
                if CAPABILITY_ID not in engine._resolve_tool_handlers():
                    break
                await asyncio.sleep(0.01)
            self.assertNotIn(CAPABILITY_ID, engine._resolve_tool_handlers())
            self.assertFalse(jobs.request_cancel(cancelling, owner=HostJobOwner("other", "session"))["ok"])
            self.assertTrue(jobs.request_cancel(cancelling, owner=owner)["ok"])
            self.assertTrue(jobs.request_cancel(cancelling, owner=owner)["ok"])
            await settle()
            self.assertTrue((await disabling)["ok"])
            self.assertEqual(jobs.get(cancelling, owner=owner).status, "cancelled")
            self.assertEqual(jobs.get(cancelling, owner=owner).artifacts, ())
            self.assertEqual(
                self.files.store.list_generated_files(profile_user_id="owner", session_id="session", limit=100),
                baseline,
            )
            self.assertEqual(list((root / "copies").iterdir()), [])
            self.assertTrue((await self.service.set_enabled(plugin_id=PLUGIN_ID, enabled=True))["ok"])
            failed = submit("bad-dataset", "gen_9999")
            await settle()
            self.assertEqual(jobs.get(failed, owner=owner).status, "failed")
            self.assertEqual([j.job_id for j in completed], [success, cancelling, failed])
            self.assertEqual(runtime.recover(), 0)
            delivery_acceptance.MediaPluginInstallationTests.verify_job_memory_and_delivery(
                self, root=root, files=self.files, completed=completed, expected_extension="zip"
            )
        finally:
            if cancelling:
                jobs.request_cancel(cancelling, owner=owner)
            await asyncio.to_thread(runner.close, timeout=10)

    async def test_real_market_profiles_dependency_jobs_and_lifecycle(self):
        self.snapshot(False)
        await self.install(PLUGIN_ID)
        stable = self.snapshot(True)
        handler = self.engine._resolve_tool_handlers()[CAPABILITY_ID]
        self.assertEqual(handler.background_job_policy(), ("agent", "timeline"))
        self.assertEqual(
            set(handler.tool_spec().input_schema["properties"]),
            {
                "source_ids",
                "profile",
                "target_sr",
                "mono",
                "min_clip_seconds",
                "max_clip_seconds",
                "silence_threshold_db",
                "min_silence_ms",
                "max_silence_kept_ms",
                "clean_first",
                "normalize_volume",
                "output_title",
                "send_to_user",
            },
        )
        self.assertEqual(handler.build_prompt_instruction(), render_legacy_json_tool_instruction(handler.tool_spec(), argument_envelope=True))
        self.assertEqual(self.runtime.stable_system_prompt_blocks(), ())
        path = self.root / "generated/generated.wav"
        shutil.copy2(self.source, path)
        generated = self.files.register_generated_artifact(
            profile_user_id="owner",
            session_id="session",
            output_path=path,
            output_title="generated tone",
            output_format="wav",
            mime_type="audio/wav",
            summary="actual PCM fixture",
            content_card={"kind": "test"},
            created_by_tool="test",
            send_to_user=False,
        )["generated_handle"]
        for profile, rate in (("gpt_sovits", 44100), ("rvc", 40000), ("archive", 44100)):
            manifest, audio = self.archive(
                await self.invoke([generated if profile == "rvc" else self.source_id], profile=profile)
            )
            self.assertEqual(audio, [(rate, 1, rate * 4)])
            self.assertEqual(manifest["stats"]["recommended_count"], 1)
            self.assertEqual(self.snapshot(True), stable)
        stereo = self.root / "attachments/stereo.wav"
        self.make_audio(stereo, channels=2)
        manifest, audio = self.archive(
            await self.invoke([self.register(stereo)], target_sr=96000, mono=False, max_clip_seconds=3)
        )
        self.assertEqual(audio, [(96000, 2, 384000)])
        self.assertEqual(manifest["stats"]["recommended_count"], 0)
        self.assertIn("too_long", manifest["issue_slices"])
        partial = await self.invoke([self.source_id, "gen_9999", generated], normalize_volume=True)
        manifest, audio = self.archive(partial)
        self.assertEqual(manifest["stats"]["failed_source_count"], 1)
        self.assertEqual(len(audio), 2)
        self.assertIn("1 个来源失败", partial.followup_context)
        for context in (InvocationContext("other", "session", "web"), InvocationContext("owner", "other", "web")):
            denied = await self.service.invoke_capability(
                CAPABILITY_ID, {"source_ids": [self.source_id]}, context=context
            )
            self.assertTrue(denied.is_error)
            self.assertIn("resource_not_found", str(denied.content))
        missing = await self.invoke([self.source_id], clean_first=True)
        self.assertEqual(missing.state_updates["adapter_capability_reason"], "dataset_no_usable_sources")
        self.assertNotIn("generated_file_ready", str(missing.stream_events))
        await self.install("akane.voice-clean")
        cleaned, _ = self.archive(await self.invoke([self.source_id], clean_first=True))
        self.assertTrue(cleaned["sources"][0]["cleaned"])
        self.assertEqual(cleaned["sources"][0]["handle"], self.source_id)
        self.assertTrue((await self.service.set_enabled(plugin_id="akane.voice-clean", enabled=False))["ok"])
        denied = await self.invoke([self.source_id], clean_first=True)
        self.assertEqual(denied.state_updates["adapter_capability_reason"], "dataset_no_usable_sources")
        self.assertTrue((await self.service.set_enabled(plugin_id="akane.voice-clean", enabled=True))["ok"])
        self.archive(await self.invoke([generated], clean_first=True))
        await self.exercise_jobs()
        with patch.dict(os.environ, {"AKANE_MEDIA_FFMPEG": str(self.root / "missing-ffmpeg")}):
            bad = await self.service.stage_source(source_path=str(ROOT / "plugins/akane_voice_dataset"))
        self.assertFalse(bad["ok"], bad)
        self.assertEqual(bad["reason"], "ffmpeg_not_found")
        self.assertIn(CAPABILITY_ID, self.runtime.capability_ids)
        self.archive(await self.invoke([self.source_id]))
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.before)
        self.assertTrue((await self.service.set_enabled(plugin_id=PLUGIN_ID, enabled=False))["ok"])
        self.snapshot(False)
        self.assertTrue((await self.service.set_enabled(plugin_id=PLUGIN_ID, enabled=True))["ok"])
        self.snapshot(True)
        removed = await self.service.uninstall(plugin_id=PLUGIN_ID)
        self.assertTrue(removed["ok"], removed)
        self.snapshot(False)
