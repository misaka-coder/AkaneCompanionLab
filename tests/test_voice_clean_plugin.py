"""Real wheel/worker/model integration; the host never imports plugin source."""

from __future__ import annotations

import asyncio
from array import array
import hashlib
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

from companion_v01.extension_management import ExtensionManagementService, PluginSelectionStore
from companion_v01.legacy_tool_prompt import render_legacy_json_tool_instruction
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.plugin_generation_candidate import PluginGenerationCandidateBuilder
from companion_v01.plugin_generation_runtime import PluginGenerationRuntime
from companion_v01.plugin_installation import ManagedPluginArtifactStore
from companion_v01.plugin_managed_artifacts import GeneratedFileManagedArtifactSink
from companion_v01.plugin_market import StaticPluginMarket
from companion_v01.plugin_resources import GeneratedFileResourceProvider
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.capability_registry import ExecutorBroker
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_tool_jobs import HostToolJobRuntime
from companion_v01.mode_profiles import ModeProfileRegistry
from tests import test_media_convert_plugin as delivery_acceptance
from companion_v01.tool_runtime import ToolExecutionContext
from scripts.build_plugin_market import build_market
from tests.test_plugin_engine_bridge import EngineFacade
from tests.test_plugin_resources import services


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "akane.voice-clean"
CAPABILITY_ID = PLUGIN_ID + ".run.v1"


@unittest.skipUnless(
    os.environ.get("AKANE_CLEAN_PYTHON") and shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "Prepared DeepFilterNet and FFmpeg required",
)
class CleaningInstallationTests(unittest.IsolatedAsyncioTestCase):
    def discovery_snapshot(self, engine, *, installed):
        snapshots = []
        for mode in ("desktop_pet", "qq_text"):
            client = ModeProfileRegistry().resolve_from_payload({"client_mode": mode})
            handlers = engine._resolve_tool_handlers(
                client_context=client, profile_user_id="owner", session_id="session"
            )
            prompt = AkaneMemoryEngine._build_tool_prompt_context(
                engine, allow_tool_call=True, client_context=client, profile_user_id="owner", session_id="session"
            )
            self.assertNotIn("clean_voice_track", prompt)
            self.assertEqual(CAPABILITY_ID in handlers, installed)
            self.assertEqual(CAPABILITY_ID in prompt, installed)
            snapshots.append(json.dumps(build_openai_native_tool_specs(handlers), sort_keys=True))
        return snapshots

    async def exercise_jobs(self, *, root, engine, files, attachments, source_id, service):
        engine.executor_broker = ExecutorBroker(None)
        engine._tool_hook_result_status = AkaneMemoryEngine._tool_hook_result_status
        engine._resolve_client_protocol_context = ModeProfileRegistry().resolve_from_payload
        jobs = HostJobStore(root / "jobs.db")
        runner = BackgroundTaskRunner({"host-jobs": 1})
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

        async def settle():
            self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=60))
            self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-job-completions", timeout=5))

        def submit(invocation, source, fmt="mp3"):
            return runtime.submit(
                capability_id=CAPABILITY_ID,
                invocation_id=invocation,
                call={"type": CAPABILITY_ID, "arguments": {"source_id": source, "output_format": fmt, "quality": "ai"}},
                context=context,
            ).stream_events[0]["job_id"]

        cancel_id = ""
        try:
            start = time.monotonic()
            success_id = submit("cleaning-job", source_id)
            self.assertLess(time.monotonic() - start, 1)
            await settle()
            success = jobs.get(success_id, owner=owner)
            self.assertEqual(success.status, "succeeded", success)
            self.assertEqual(len(success.artifacts), 1)
            self.assertEqual(submit("cleaning-job", source_id), success_id)
            self.assertEqual(len(completed), 1)

            source = root / "attachments/long.wav"
            with wave.open(str(source), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                samples = array("h", (int(8000 * math.sin(2 * math.pi * 330 * i / 16000)) for i in range(16000)))
                for _ in range(120):
                    audio.writeframesraw(samples.tobytes())
            item = attachments.create_pending(
                profile_user_id="owner",
                session_id="session",
                source="test",
                kind="audio",
                origin_name="long.wav",
                file_ext="wav",
                mime_type="audio/wav",
                storage_relpath="long.wav",
                file_size=source.stat().st_size,
            )
            attachments.mark_ready(profile_user_id="owner", session_id="session", attachment_id=item["attachment_id"])
            baseline = files.store.list_generated_files(profile_user_id="owner", session_id="session", limit=100)
            cancel_id = submit("cancel-cleaning", item["attachment_id"], "wav")
            deadline = time.monotonic() + 30
            while not list((root / "copies").rglob("cleaning-*/prepared.wav")) and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            self.assertTrue(list((root / "copies").rglob("cleaning-*/prepared.wav")), "No real model input prepared")
            await asyncio.sleep(2)
            self.assertEqual(jobs.get(cancel_id, owner=owner).status, "running")
            disabling = asyncio.create_task(service.set_enabled(plugin_id=PLUGIN_ID, enabled=False))
            deadline = time.monotonic() + 5
            while CAPABILITY_ID in engine._resolve_tool_handlers() and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            self.assertNotIn(CAPABILITY_ID, engine._resolve_tool_handlers())
            self.assertFalse(jobs.request_cancel(cancel_id, owner=HostJobOwner("other", "session"))["ok"])
            self.assertEqual(jobs.request_cancel(cancel_id, owner=owner)["status"], "cancelling")
            self.assertTrue(jobs.request_cancel(cancel_id, owner=owner)["ok"])
            await settle()
            cancelled = jobs.get(cancel_id, owner=owner)
            self.assertEqual(cancelled.status, "cancelled", cancelled)
            self.assertEqual(cancelled.artifacts, ())
            self.assertTrue((await disabling)["ok"])
            self.assertEqual(list((root / "copies").iterdir()), [])
            self.assertEqual(
                files.store.list_generated_files(profile_user_id="owner", session_id="session", limit=100), baseline
            )

            self.assertTrue((await service.set_enabled(plugin_id=PLUGIN_ID, enabled=True))["ok"])
            failure_id = submit("missing-source", "gen_9999")
            await settle()
            failure = jobs.get(failure_id, owner=owner)
            self.assertEqual(failure.status, "failed", failure)
            self.assertEqual(failure.artifacts, ())
            self.assertEqual([job.job_id for job in completed], [success_id, cancel_id, failure_id])
            self.assertEqual(runtime.recover(), 0)
            # Same real MemCore/QQ transport-boundary/Tauri module assertions as
            # the converter, exercised for the real cleaned MP3 output.
            for artifact_index in range(1):
                delivery_acceptance.MediaPluginInstallationTests.verify_job_memory_and_delivery(
                    self, root=root, files=files, completed=completed, artifact_index=artifact_index
                )
        finally:
            if cancel_id:
                jobs.request_cancel(cancel_id, owner=owner)
            await asyncio.to_thread(runner.close, timeout=10)

    async def test_real_market_install_model_conversion_scope_and_lifecycle(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                os.environ,
                {
                    "AKANE_CLEAN_DEVICE": "cpu",
                },
            ),
        ):
            root = Path(temporary)
            source_project = root / "source"
            shutil.copytree(PROJECT_ROOT / "plugins" / "akane_voice_clean", source_project)
            index = await asyncio.to_thread(build_market, root / "market")
            artifacts = ManagedPluginArtifactStore(root / "artifacts", instance_id="test", project_root=PROJECT_ROOT)
            selections = PluginSelectionStore(root / "selections.json", defaults=(), instance_id="test")
            runtime = PluginGenerationRuntime(
                (),
                candidate_builder=PluginGenerationCandidateBuilder(
                    source_resolver=artifacts,
                    project_root=PROJECT_ROOT,
                    work_root=root / "workers",
                    plugin_storage_data_root=root / "data",
                    plugin_storage_instance_id="test",
                ),
            )
            _, attachments, files = services(root)
            runtime.bind_managed_artifact_sink(GeneratedFileManagedArtifactSink(files))
            runtime.bind_resource_provider(GeneratedFileResourceProvider(files, work_root=root / "copies"))
            service = ExtensionManagementService(
                plugin_runtime=runtime,
                selection_store=selections,
                artifact_store=artifacts,
                market=StaticPluginMarket(index),
            )
            source = root / "attachments" / "source.wav"
            source.parent.mkdir(exist_ok=True)
            samples = array("h", (int(8000 * math.sin(2 * math.pi * 330 * i / 16000)) for i in range(32000)))
            with wave.open(str(source), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(samples.tobytes())
            original = hashlib.sha256(source.read_bytes()).hexdigest()
            item = attachments.create_pending(
                profile_user_id="owner",
                session_id="session",
                source="test",
                kind="audio",
                origin_name="source.wav",
                file_ext="wav",
                mime_type="audio/wav",
                storage_relpath="source.wav",
                file_size=source.stat().st_size,
            )
            attachments.mark_ready(profile_user_id="owner", session_id="session", attachment_id=item["attachment_id"])
            context = InvocationContext("owner", "session", "web")
            engine = EngineFacade(PluginCapabilityToolBridge(runtime, config_base_dir=root))
            engine.store, engine.capability_config_base_dir = files.store, root
            engine._get_generated_file_service = lambda: files
            await runtime.start()
            try:
                self.assertNotIn(CAPABILITY_ID, engine._resolve_tool_handlers())
                self.discovery_snapshot(engine, installed=False)
                entry = next(
                    item for item in (await service.browse_market())["plugins"] if item["plugin_id"] == PLUGIN_ID
                )
                self.assertEqual(entry["plugin_id"], PLUGIN_ID)
                staged = await service.stage_market(plugin_id=PLUGIN_ID, digest=entry["sha256"])
                self.assertTrue(staged["ok"], staged)
                installed = await service.install_stage(
                    stage_id=staged["stage_id"], approved_permissions=staged["permissions"]
                )
                self.assertTrue(installed["ok"], installed)
                self.assertIn(CAPABILITY_ID, runtime.capability_ids)
                self.assertEqual(runtime.stable_system_prompt_blocks(), ())
                handlers = engine._resolve_tool_handlers()
                handler = handlers[CAPABILITY_ID]
                mode_snapshots = self.discovery_snapshot(engine, installed=True)
                snapshot = json.dumps(build_openai_native_tool_specs(handlers), sort_keys=True)
                self.assertEqual(
                    handler.build_prompt_instruction(), render_legacy_json_tool_instruction(handler.tool_spec(), argument_envelope=True)
                )
                self.assertEqual(handler.background_job_policy(), ("agent", "timeline"))
                source_id = item["attachment_id"]
                for fmt in ("wav", "flac", "mp3"):
                    projected = await asyncio.to_thread(
                        handler.execute,
                        call=handler.normalize_call(
                            {
                                "type": CAPABILITY_ID,
                                "source_id": source_id,
                                "output_format": fmt,
                                "quality": "ai",
                                "mode": "denoise",
                                "post_filter": True,
                            }
                        ),
                        context=ToolExecutionContext("owner", "session", 1, {}, client_mode="desktop_pet"),
                    )
                    self.assertEqual(projected.state_updates["adapter_capability_status"], "ok", projected)
                    self.assertEqual(projected.state_updates["plugin_result_experience"], "projected")
                    self.assertIn("AI 人声增强", projected.followup_context)
                    self.assertIn("原始文件未修改", projected.followup_context)
                    events = [event for event in projected.stream_events if event["type"] == "generated_file_ready"]
                    self.assertEqual(len(events), 1, projected)
                    for event in events:
                        self.assertFalse(event["send_to_user"])
                        ref = event["generated_file"]
                        delivered = files.send_file(
                            profile_user_id="owner", session_id="session", target=ref["generated_handle"]
                        )
                        self.assertTrue(delivered["ok"], delivered)
                        self.assertEqual(delivered["files"][0]["file_ext"], fmt)
                        resolved = files.resolve_input_resource(
                            profile_user_id="owner",
                            session_id="session",
                            target=ref["generated_handle"],
                            timestamp=None,
                        )
                        self.assertGreater(Path(resolved["absolute_path"]).stat().st_size, 44)
                        if fmt == "wav":
                            with wave.open(resolved["absolute_path"], "rb") as output:
                                self.assertEqual(
                                    (output.getframerate(), output.getnchannels(), output.getnframes()),
                                    (48000, 1, 96000),
                                )
                    source_id = events[0]["generated_file"]["generated_handle"]
                    self.assertEqual(list((root / "copies").iterdir()), [])
                    self.assertEqual(
                        json.dumps(build_openai_native_tool_specs(engine._resolve_tool_handlers()), sort_keys=True),
                        snapshot,
                    )
                    self.assertEqual(self.discovery_snapshot(engine, installed=True), mode_snapshots)
                for bad_context in (
                    InvocationContext("other", "session", "web"),
                    InvocationContext("owner", "other", "web"),
                ):
                    rejected = await service.invoke_capability(
                        CAPABILITY_ID, {"source_id": source_id}, context=bad_context
                    )
                    self.assertTrue(rejected.is_error)
                    self.assertEqual(rejected.reason, "resource_not_found")
                unknown = await service.invoke_capability(CAPABILITY_ID, {"source_id": "gen_9999"}, context=context)
                self.assertEqual(unknown.reason, "resource_not_found")
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original)
                await self.exercise_jobs(
                    root=root,
                    engine=engine,
                    files=files,
                    attachments=attachments,
                    source_id=item["attachment_id"],
                    service=service,
                )
                with patch.dict(os.environ, {"AKANE_MEDIA_FFMPEG": str(root / "missing-ffmpeg")}):
                    bad = await service.stage_source(source_path=str(source_project))
                self.assertFalse(bad["ok"], bad)
                self.assertEqual(bad["reason"], "ffmpeg_not_found")
                self.assertIn(CAPABILITY_ID, runtime.capability_ids)
                disabled = await service.set_enabled(plugin_id=PLUGIN_ID, enabled=False)
                self.assertTrue(disabled["ok"], disabled)
                self.assertNotIn(CAPABILITY_ID, engine._resolve_tool_handlers())
                self.discovery_snapshot(engine, installed=False)
                with patch.dict(os.environ, {"AKANE_CLEAN_PYTHON": str(root / "missing-python")}):
                    self.assertTrue((await service.set_enabled(plugin_id=PLUGIN_ID, enabled=True))["ok"])
                fallback_handler = engine._resolve_tool_handlers()[CAPABILITY_ID]
                auto_result = await asyncio.to_thread(
                    fallback_handler.execute,
                    call=fallback_handler.normalize_call(
                        {"type": CAPABILITY_ID, "source_id": item["attachment_id"], "quality": "auto"}
                    ),
                    context=ToolExecutionContext("owner", "session", 1, {}, client_mode="qq_text"),
                )
                self.assertEqual(auto_result.state_updates["adapter_capability_status"], "ok", auto_result)
                self.assertIn("基础滤波降噪", auto_result.followup_context)
                self.assertIn("deepfilternet_python_not_found", auto_result.followup_context)
                self.assertIn("降级", auto_result.followup_context)
                explicit_ai = await service.invoke_capability(
                    CAPABILITY_ID, {"source_id": item["attachment_id"], "quality": "ai"}, context=context
                )
                self.assertTrue(explicit_ai.is_error)
                self.assertEqual(explicit_ai.reason, "deepfilternet_python_not_found")
                self.assertTrue((await service.set_enabled(plugin_id=PLUGIN_ID, enabled=False))["ok"])
                enabled = await service.set_enabled(plugin_id=PLUGIN_ID, enabled=True)
                self.assertTrue(enabled["ok"], enabled)
                self.assertEqual(
                    json.dumps(build_openai_native_tool_specs(engine._resolve_tool_handlers()), sort_keys=True),
                    snapshot,
                )
                removed = await service.uninstall(plugin_id=PLUGIN_ID)
                self.assertTrue(removed["ok"], removed)
                self.assertNotIn(CAPABILITY_ID, engine._resolve_tool_handlers())
                self.discovery_snapshot(engine, installed=False)
                self.assertEqual(artifacts.snapshot()["plugins"], [])
                self.assertEqual(selections.load(), ())
            finally:
                await runtime.stop()


if __name__ == "__main__":
    unittest.main()
