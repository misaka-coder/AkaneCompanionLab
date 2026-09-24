"""Installed wheel + real offline speech; no host import of plugin source."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

from capcore import InvocationContext
from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.capability_registry import ExecutorBroker
from companion_v01.desktop_music_timeline import DesktopMusicTimelineService
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.extension_management import ExtensionManagementService, PluginSelectionStore
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_tool_jobs import HostToolJobRuntime
from companion_v01.legacy_tool_prompt import render_legacy_json_tool_instruction
from companion_v01.mode_profiles import ModeProfileRegistry
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.optional_media_binding import prepare_timeline_transcript
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
PLUGIN_ID = "akane.file-transcription"
CAPABILITY_ID = PLUGIN_ID + ".run.v1"


@unittest.skipUnless(os.name == "nt" and shutil.which("ffmpeg"), "Windows speech synthesis and FFmpeg required")
class TranscriptionInstallationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = root = Path(temporary.name)
        environment = patch.dict(
            os.environ,
            {
                "AKANE_ASR_MODEL": "tiny",
                "AKANE_ASR_DEVICE": "cpu",
                "AKANE_ASR_COMPUTE_TYPE": "int8",
                "AKANE_ASR_BACKEND": "local",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        index = await asyncio.to_thread(build_market, root / "market")
        self.artifacts = ManagedPluginArtifactStore(root / "artifacts", instance_id="test", project_root=ROOT)
        self.selections = PluginSelectionStore(root / "selections.json", defaults=(), instance_id="test")
        self.runtime = PluginGenerationRuntime(
            (),
            candidate_builder=PluginGenerationCandidateBuilder(
                source_resolver=self.artifacts,
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
        self.service = ExtensionManagementService(
            plugin_runtime=self.runtime,
            selection_store=self.selections,
            artifact_store=self.artifacts,
            market=StaticPluginMarket(index),
        )
        self.engine = EngineFacade(PluginCapabilityToolBridge(self.runtime, config_base_dir=root))
        self.engine.store, self.engine.capability_config_base_dir = self.files.store, root
        self.engine._get_generated_file_service = lambda: self.files
        self.source = root / "attachments/speech.wav"
        self.source.parent.mkdir(exist_ok=True)
        fixture = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-File",
                str(ROOT / "plugins/akane_file_transcription/tests/synthesize_fixture.ps1"),
                "-OutputPath",
                str(self.source),
            ],
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(fixture.returncode, 0, "speech fixture synthesis failed")
        self.source_id = self.register_attachment(self.source)
        self.before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        await self.runtime.start()

    def register_attachment(self, path):
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

    def snapshot(self, installed):
        result = []
        for mode in ("desktop_pet", "qq_text"):
            client = ModeProfileRegistry().resolve_from_payload({"client_mode": mode})
            handlers = self.engine._resolve_tool_handlers(
                client_context=client, profile_user_id="owner", session_id="session"
            )
            prompt = AkaneMemoryEngine._build_tool_prompt_context(
                self.engine, allow_tool_call=True, client_context=client, profile_user_id="owner", session_id="session"
            )
            self.assertNotIn("transcribe_media", handlers)
            self.assertNotIn("transcribe_media", prompt)
            self.assertEqual(CAPABILITY_ID in handlers, installed)
            self.assertEqual(CAPABILITY_ID in prompt, installed)
            result.append(json.dumps(build_openai_native_tool_specs(handlers), sort_keys=True))
        return result

    async def invoke(self, sources, **options):
        handler = self.engine._resolve_tool_handlers()[CAPABILITY_ID]
        return await asyncio.to_thread(
            handler.execute,
            call=handler.normalize_call({"type": CAPABILITY_ID, "source_ids": sources, "language": "en", **options}),
            context=ToolExecutionContext("owner", "session", 1, {}, client_mode="desktop_pet"),
        )

    def artifact_texts(self, result):
        self.assertEqual(result.state_updates["adapter_capability_status"], "ok", result)
        self.assertEqual(result.state_updates["plugin_result_experience"], "projected")
        events = [e for e in result.stream_events if e["type"] == "generated_file_ready"]
        texts = []
        for event in events:
            self.assertFalse(event["send_to_user"])
            ref = event["generated_file"]
            resolved = self.files.resolve_input_resource(
                profile_user_id="owner", session_id="session", target=ref["generated_handle"], timestamp=None
            )
            texts.append(Path(resolved["absolute_path"]).read_text(encoding="utf-8"))
        self.assertTrue(texts)
        self.assertTrue(all("hello world" in text.lower() for text in texts), texts)
        self.assertEqual(list((self.root / "copies").iterdir()), [])
        return texts

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
        owner = HostJobOwner("owner", "session")
        context = ToolExecutionContext("owner", "session", 1, {}, client_mode="qq_text")

        def submit(name, source):
            return runtime.submit(
                capability_id=CAPABILITY_ID,
                invocation_id=name,
                call={
                    "type": CAPABILITY_ID,
                    "arguments": {
                        "source_ids": [source],
                        "language": "en",
                        "vad_filter": False,
                        "output_format": "json",
                    },
                },
                context=context,
            ).stream_events[0]["job_id"]

        async def settle():
            self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=60))
            self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-job-completions", timeout=5))

        cancelling = ""
        try:
            started = time.monotonic()
            success = submit("asr-job", self.source_id)
            self.assertLess(time.monotonic() - started, 1)
            await settle()
            self.assertEqual(jobs.get(success, owner=owner).status, "succeeded")
            self.assertEqual(submit("asr-job", self.source_id), success)
            long = root / "attachments/long.wav"
            with wave.open(str(self.source), "rb") as source:
                params, pcm = source.getparams(), source.readframes(source.getnframes())
            with wave.open(str(long), "wb") as output:
                output.setparams(params)
                for _ in range(40):
                    output.writeframesraw(pcm)
            baseline = self.files.store.list_generated_files(profile_user_id="owner", session_id="session", limit=100)
            cancelling = submit("cancel-asr", self.register_attachment(long))
            for _ in range(600):
                if list((root / "copies").rglob("asr-*/prepared.wav")):
                    break
                await asyncio.sleep(0.05)
            self.assertTrue(list((root / "copies").rglob("asr-*/prepared.wav")))
            await asyncio.sleep(2)
            self.assertEqual(jobs.get(cancelling, owner=owner).status, "running")
            disabling = asyncio.create_task(self.service.set_enabled(plugin_id=PLUGIN_ID, enabled=False))
            for _ in range(100):
                if CAPABILITY_ID not in engine._resolve_tool_handlers():
                    break
                await asyncio.sleep(0.05)
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
            failed = submit("bad-asr", "gen_9999")
            await settle()
            self.assertEqual(jobs.get(failed, owner=owner).status, "failed")
            self.assertEqual([j.job_id for j in completed], [success, cancelling, failed])
            self.assertEqual(runtime.recover(), 0)
            delivery_acceptance.MediaPluginInstallationTests.verify_job_memory_and_delivery(
                self, root=root, files=self.files, completed=completed, expected_extension="json"
            )
        finally:
            if cancelling:
                jobs.request_cancel(cancelling, owner=owner)
            await asyncio.to_thread(runner.close, timeout=10)

    async def test_installed_batch_formats_jobs_lifecycle_and_timeline(self):
        self.snapshot(False)
        entry = next(item for item in (await self.service.browse_market())["plugins"] if item["plugin_id"] == PLUGIN_ID)
        staged = await self.service.stage_market(plugin_id=PLUGIN_ID, digest=entry["sha256"])
        self.assertTrue(staged["ok"], staged)
        installed = await self.service.install_stage(
            stage_id=staged["stage_id"], approved_permissions=staged["permissions"]
        )
        self.assertTrue(installed["ok"], installed)
        stable = self.snapshot(True)
        handler = self.engine._resolve_tool_handlers()[CAPABILITY_ID]
        self.assertEqual(handler.background_job_policy(), ("agent", "timeline"))
        self.assertEqual(handler.build_prompt_instruction(), render_legacy_json_tool_instruction(handler.tool_spec(), argument_envelope=True))
        self.assertEqual(self.runtime.stable_system_prompt_blocks(), ())
        generated_path = self.root / "generated/source.wav"
        shutil.copy2(self.source, generated_path)
        generated = self.files.register_generated_artifact(
            profile_user_id="owner",
            session_id="session",
            output_path=generated_path,
            output_title="generated speech",
            output_format="wav",
            mime_type="audio/wav",
            summary="actual synthesized speech fixture",
            content_card={"kind": "test_speech"},
            created_by_tool="test",
            send_to_user=False,
        )
        generated_id = generated["generated_handle"]
        for index, fmt in enumerate(("md", "txt", "json", "srt", "vtt")):
            result = await self.invoke([self.source_id if index % 2 else generated_id], output_format=fmt)
            texts = self.artifact_texts(result)
            self.assertEqual(len(texts), 1)
            if fmt == "json":
                self.assertEqual(json.loads(texts[0])["transcripts"][0]["model"], "tiny")
            if fmt in ("srt", "vtt"):
                self.assertIn("-->", texts[0])
            self.assertEqual(self.snapshot(True), stable)
        partial = await self.invoke([self.source_id, "gen_9999", generated_id], output_format="json")
        transcripts = json.loads(self.artifact_texts(partial)[0])["transcripts"]
        self.assertEqual(len(transcripts), 2)
        self.assertIn("1 份材料失败", partial.followup_context)
        separate = await self.invoke(
            [self.source_id, generated_id], output_format="txt", merge_outputs=False, with_timestamps=False
        )
        self.assertEqual(len(self.artifact_texts(separate)), 2)
        for context in (InvocationContext("other", "session", "web"), InvocationContext("owner", "other", "web")):
            denied = await self.service.invoke_capability(
                CAPABILITY_ID, {"source_ids": [generated_id]}, context=context
            )
            self.assertTrue(denied.is_error)
            self.assertIn("resource_not_found", str(denied.content))
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.before)
        await self.exercise_jobs()
        options = {"language": "en", "model_size": "tiny"}
        transcript = await asyncio.to_thread(
            prepare_timeline_transcript,
            self.engine,
            profile_user_id="owner",
            session_id="session",
            source_id=self.source_id,
            options=options,
        )
        self.assertEqual(transcript["status"], "ready", transcript)
        self.assertIn("hello world", transcript["text"].lower())
        timeline = DesktopMusicTimelineService(store=self.files.store, generated_file_service=self.files)
        reused = timeline._load_existing_transcript_segments(
            profile_user_id="owner", session_id="session", source={"handle": self.source_id}
        )
        self.assertTrue(reused.get("segments"), reused)
        unrelated = timeline._load_existing_transcript_segments(
            profile_user_id="owner", session_id="session", source={"handle": "audio_unrelated"}
        )
        self.assertEqual(unrelated, {})
        with patch.dict(os.environ, {"AKANE_ASR_CACHE_DIR": str(self.root / "missing-cache")}):
            bad = await self.service.stage_source(source_path=str(ROOT / "plugins/akane_file_transcription"))
        self.assertFalse(bad["ok"], bad)
        self.assertEqual(bad["reason"], "asr_model_missing")
        self.assertIn(CAPABILITY_ID, self.runtime.capability_ids)
        self.assertTrue((await self.service.set_enabled(plugin_id=PLUGIN_ID, enabled=False))["ok"])
        self.snapshot(False)
        unavailable = prepare_timeline_transcript(
            self.engine, profile_user_id="owner", session_id="session", source_id=self.source_id, options=options
        )
        self.assertEqual(unavailable["error"], "transcription_plugin_unavailable")
        self.assertTrue((await self.service.set_enabled(plugin_id=PLUGIN_ID, enabled=True))["ok"])
        self.assertEqual(self.snapshot(True), stable)
        self.assertTrue((await self.service.uninstall(plugin_id=PLUGIN_ID))["ok"])
        self.snapshot(False)
        self.assertEqual(self.artifacts.snapshot()["plugins"], [])
        self.assertEqual(self.selections.load(), ())


if __name__ == "__main__":
    unittest.main()
