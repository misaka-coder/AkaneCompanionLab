"""Install the real distribution; never import plugin source into the host test."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import wave
from array import array
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from capcore import InvocationContext

import config
from companion_v01.mode_profiles import ModeProfileRegistry
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.background_tasks import BackgroundTaskRunner
from companion_v01.bot_runtime import _host_job_completion_request
from companion_v01.capability_registry import ExecutorBroker
from companion_v01.host_jobs import HostJobOwner, HostJobStore
from companion_v01.host_tool_jobs import HostToolJobRuntime
from companion_v01.deployment_security import QQChannelRuntimeConfig
from companion_v01.memcore_integration.manager import MemcoreManager
from companion_v01.qq_gateway import NapCatQQGateway
from companion_v01.tool_handlers.generated_media import SendFileToolHandler
from companion_v01.generated_files import GeneratedFileService
from companion_v01.legacy_tool_prompt import render_legacy_json_tool_instruction
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.extension_management import ExtensionManagementService, PluginSelectionStore
from companion_v01.plugin_generation_candidate import PluginGenerationCandidateBuilder
from companion_v01.plugin_generation_runtime import PluginGenerationRuntime
from companion_v01.plugin_installation import ManagedPluginArtifactStore
from companion_v01.plugin_managed_artifacts import GeneratedFileManagedArtifactSink
from companion_v01.plugin_market import StaticPluginMarket
from companion_v01.plugin_resources import GeneratedFileResourceProvider
from companion_v01.plugin_tool_bridge import PluginCapabilityToolBridge
from companion_v01.tool_runtime import ToolExecutionContext
from scripts.build_plugin_market import build_market
from tests.test_plugin_engine_bridge import EngineFacade
from tests.test_plugin_resources import services
from tests.test_media_skill_shell_profile_m68 import _FakeEmbeddingProvider, _FakeLLM


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "akane.media-convert"
CAPABILITY_ID = f"{PLUGIN_ID}.run.v1"


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and FFprobe required")
class MediaPluginInstallationTests(unittest.IsolatedAsyncioTestCase):
    def verify_job_memory_and_delivery(self, *, root, files, completed, artifact_index=0, expected_extension="mp3"):
        manager = MemcoreManager(
            backend="memcore",
            storage_path=root / "memory.sqlite3",
            visible_scope="conversation",
            enable_flavor=False,
            shadow_compare=False,
            llm=_FakeLLM(),
            embedding_provider=_FakeEmbeddingProvider(),
        )
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.memcore_manager = manager
        engine._resolve_payload_character_pack_id = lambda _payload: "test-character"
        engine._memcore_owns_compaction = lambda: False
        try:
            for job in completed:
                request = _host_job_completion_request(job)
                payload = {
                    "source_id": request.trace_id,
                    "user_id": "session",
                    "real_user_id": "owner",
                    "timestamp": int(job.finished_at),
                    "event": {
                        "event_type": request.event_type,
                        "source": request.source,
                        "fields": dict(request.data),
                    },
                }
                for _ in range(2):
                    recorded = engine.record_plugin_timeline_event(payload)
                    self.assertTrue(recorded["ok"], recorded)
                stored = manager._store.get_record_by_source_id(job.completion_event_id)
                self.assertEqual(stored["kind"], f"event.job.{job.status}")
                self.assertEqual(stored["payload"]["status"], job.status)
                self.assertNotIn(str(root), str(stored["payload"]))
                projection = manager.build_context_projection(
                    provider_profile="openai",
                    profile_user_id="owner",
                    session_id="session",
                    character_pack_id="test-character",
                )
                self.assertNotIn("absolute_path", str(projection.get("payloads")))
                self.assertIn(job.job_id, str(projection.get("payloads")))
        finally:
            manager.close()

        success = completed[0]
        handler = SendFileToolHandler(generated_file_service=files)
        call = handler.normalize_call(
            {"type": "send_file", "target": success.artifacts[artifact_index]["handle"], "delivery_action": "open"}
        )
        desktop = handler.execute(
            call=call, context=ToolExecutionContext("owner", "session", 1, {}, client_mode="desktop_pet")
        )
        event = desktop.stream_events[0]
        self.assertEqual(event["desktop_delivery"]["action"], "open")
        self.assertNotIn("path", event["desktop_delivery"])
        if shutil.which("node"):
            smoke = subprocess.run(
                [
                    "node",
                    str(PROJECT_ROOT / "desktop_pet_next/scripts/media-plugin-delivery-smoke.mjs"),
                    expected_extension,
                ],
                input=json.dumps(event),
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(smoke.returncode, 0, smoke.stderr)
        qq = handler.execute(call=call, context=ToolExecutionContext("owner", "session", 1, {}, client_mode="qq_text"))
        self.assertNotIn("desktop_delivery", qq.stream_events[0])
        gateway = NapCatQQGateway(
            channel_config=QQChannelRuntimeConfig(
                enabled=True,
                profile_ref="test",
                bot_id="10001",
                onebot_http_url="http://127.0.0.1:9",
                webhook_secret="",
                onebot_access_token="",
                require_webhook_auth=False,
                require_self_id=True,
            )
        )
        qq_context = gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": 10001,
                "user_id": 10002,
                "message_id": "test-delivery",
                "raw_message": "发送转换结果",
            }
        )
        response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"status": "ok", "retcode": 0})
        with patch("companion_v01.onebot_transport.requests.Session.request", return_value=response) as transport:
            sent = gateway.send_generated_files(qq_context, qq.stream_events)
        self.assertTrue(sent["ok"], sent)
        transport.assert_called_once()
        uploaded = transport.call_args.kwargs["json"]
        if expected_extension in {"png", "jpeg", "webp"}:
            self.assertTrue(transport.call_args.args[1].endswith("/send_private_msg"))
            images = [segment for segment in uploaded["message"] if segment["type"] == "image"]
            self.assertEqual(len(images), 1)
            path = Path(qq.stream_events[0]["file"]["absolute_path"]).resolve()
            self.assertTrue(path.is_file())
            self.assertEqual(path.suffix, "." + expected_extension)
            self.assertIn(images[0]["data"]["file"], (str(path), path.as_uri()))
        else:
            self.assertTrue(transport.call_args.args[1].endswith("/upload_private_file"))
            self.assertTrue(Path(uploaded["file"]).is_file())
            self.assertTrue(uploaded["name"].endswith("." + expected_extension))

    async def exercise_host_jobs(self, *, root, engine, files, attachments, source_id):
        engine.executor_broker = ExecutorBroker(None)
        engine._tool_hook_result_status = AkaneMemoryEngine._tool_hook_result_status
        engine._resolve_client_protocol_context = ModeProfileRegistry().resolve_from_payload
        jobs = HostJobStore(root / "jobs.db")
        runner = BackgroundTaskRunner({"host-jobs": 1})
        completed = []
        job_runtime = HostToolJobRuntime(
            engine=engine,
            store=jobs,
            background_tasks=runner,
            conversation_ref_issuer=lambda _context: "test-conversation",
            terminal_callback=lambda job: completed.append(job) or True,
        )
        context = ToolExecutionContext("owner", "session", 1, {}, client_mode="qq_text")
        owner = HostJobOwner("owner", "session")
        call = {"type": CAPABILITY_ID, "arguments": {"source_id": source_id, "output_format": "mp3"}}
        job_id = ""
        try:
            started = time.monotonic()
            accepted = job_runtime.submit(
                capability_id=CAPABILITY_ID, invocation_id="convert-job", call=call, context=context
            )
            self.assertLess(time.monotonic() - started, 1)
            self.assertEqual(accepted.stream_events[0]["type"], "background_job_accepted", accepted)
            self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=10))
            self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-job-completions", timeout=5))
            success = jobs.get(accepted.stream_events[0]["job_id"], owner=owner)
            self.assertEqual(success.status, "succeeded", success)
            self.assertEqual(len(success.artifacts), 1)
            request = _host_job_completion_request(success)
            self.assertEqual(dict(request.event.fields)["artifact_delivery_status"], "available_not_delivered")
            self.assertIn(success.artifacts[0]["handle"], request.message)
            self.assertNotIn(str(root), request.message)
            again = job_runtime.submit(
                capability_id=CAPABILITY_ID, invocation_id="convert-job", call=call, context=context
            )
            self.assertEqual(again.stream_events[0]["job_id"], success.job_id)
            self.assertEqual(len(completed), 1)

            # Long real audio + real FFmpeg, no delayed adapter or fake result.
            audio = root / "attachments" / "long.wav"
            with wave.open(str(audio), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                chunk = array("h", (int(8000 * math.sin(2 * math.pi * 440 * i / 8000)) for i in range(8000))).tobytes()
                for _ in range(1200):
                    output.writeframesraw(chunk)
            item = attachments.create_pending(
                profile_user_id="owner",
                session_id="session",
                source="test",
                kind="audio",
                origin_name="long.wav",
                file_ext="wav",
                mime_type="audio/wav",
                storage_relpath="long.wav",
                file_size=audio.stat().st_size,
            )
            attachments.mark_ready(profile_user_id="owner", session_id="session", attachment_id=item["attachment_id"])
            baseline = files.store.list_generated_files(profile_user_id="owner", session_id="session", limit=100)
            cancel_call = {
                "type": CAPABILITY_ID,
                "arguments": {
                    "source_id": item["attachment_id"],
                    "output_format": "wav",
                    "normalize_volume": True,
                    "speed_ratio": 0.25,
                },
            }
            accepted = job_runtime.submit(
                capability_id=CAPABILITY_ID, invocation_id="cancel-convert", call=cancel_call, context=context
            )
            job_id = accepted.stream_events[0]["job_id"]
            deadline = time.monotonic() + 15
            while not list((root / "copies").rglob("converted-*")) and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            self.assertTrue(list((root / "copies").rglob("converted-*")), "FFmpeg never opened its output")
            self.assertEqual(jobs.get(job_id, owner=owner).status, "running")
            self.assertFalse(jobs.request_cancel(job_id, owner=HostJobOwner("other", "session"))["ok"])
            self.assertEqual(jobs.request_cancel(job_id, owner=owner)["status"], "cancelling")
            self.assertTrue(jobs.request_cancel(job_id, owner=owner)["ok"])
            self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=10))
            self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-job-completions", timeout=5))
            cancelled = jobs.get(job_id, owner=owner)
            self.assertEqual(cancelled.status, "cancelled", cancelled)
            self.assertEqual(cancelled.artifacts, ())
            self.assertEqual(list((root / "copies").iterdir()), [])
            self.assertEqual(
                files.store.list_generated_files(profile_user_id="owner", session_id="session", limit=100), baseline
            )
            self.assertEqual([job.job_id for job in completed], [success.job_id, job_id])
            self.assertEqual(job_runtime.recover(), 0)
            request = _host_job_completion_request(cancelled)
            self.assertEqual(request.event.event_type, "job.cancelled")
            self.assertNotIn("artifact_handles", dict(request.event.fields))
            bad = job_runtime.submit(
                capability_id=CAPABILITY_ID,
                invocation_id="missing-source-job",
                call={"type": CAPABILITY_ID, "arguments": {"source_id": "gen_9999", "output_format": "mp3"}},
                context=context,
            )
            self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-jobs", timeout=10))
            self.assertTrue(await asyncio.to_thread(runner.wait_idle, lane="host-job-completions", timeout=5))
            failed = jobs.get(bad.stream_events[0]["job_id"], owner=owner)
            self.assertEqual(failed.status, "failed", failed)
            self.assertEqual(failed.artifacts, ())
            self.assertEqual(len(completed), 3)
            self.assertEqual(job_runtime.recover(), 0)
            self.verify_job_memory_and_delivery(root=root, files=files, completed=completed)
        finally:
            if job_id:
                jobs.request_cancel(job_id, owner=owner)
            await asyncio.to_thread(runner.close, timeout=10)

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
            self.assertNotIn("convert_media_file", prompt)
            self.assertEqual(CAPABILITY_ID in handlers, installed)
            self.assertEqual(CAPABILITY_ID in prompt, installed)
            native = build_openai_native_tool_specs(handlers)
            if installed:
                handler = handlers[CAPABILITY_ID]
                spec = handler.tool_spec()
                self.assertEqual(spec.execution_class, "long_task")
                self.assertEqual(handler.background_job_policy(), ("agent", "timeline"))
                self.assertEqual(native[0]["function"]["parameters"], spec.input_schema)
                self.assertEqual(handler.build_prompt_instruction(), render_legacy_json_tool_instruction(spec, argument_envelope=True))
                args = {"source_id": "audio_001", "output_format": "mp3", "speed_ratio": 1.25}
                self.assertEqual(
                    handler.normalize_call({"type": CAPABILITY_ID, **args}),
                    handler.normalize_call({"type": CAPABILITY_ID, "arguments": args}),
                )
            snapshots.append(json.dumps(native, ensure_ascii=False, sort_keys=True))
        return snapshots

    async def test_real_wheel_activation_conversion_scope_failure_and_uninstall(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_project = root / "source"
            shutil.copytree(PROJECT_ROOT / "plugins" / "akane_media_convert", source_project)
            artifacts = ManagedPluginArtifactStore(root / "artifacts", instance_id="test", project_root=PROJECT_ROOT)
            selections = PluginSelectionStore(root / "selections.json", defaults=(), instance_id="test")
            runtime = PluginGenerationRuntime(
                (),
                candidate_builder=PluginGenerationCandidateBuilder(
                    source_resolver=artifacts,
                    project_root=PROJECT_ROOT,
                    work_root=root / "work",
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
                market=StaticPluginMarket(await asyncio.to_thread(build_market, root / "market")),
            )
            audio = root / "attachments" / "source.wav"
            audio.parent.mkdir(exist_ok=True)
            samples = array("h", (int(8000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(64000)))
            with wave.open(str(audio), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(samples.tobytes())
            before = hashlib.sha256(audio.read_bytes()).hexdigest()
            item = attachments.create_pending(
                profile_user_id="owner",
                session_id="session",
                source="test",
                kind="audio",
                origin_name="source.wav",
                file_ext="wav",
                mime_type="audio/wav",
                storage_relpath="source.wav",
                file_size=audio.stat().st_size,
            )
            attachments.mark_ready(profile_user_id="owner", session_id="session", attachment_id=item["attachment_id"])
            context = InvocationContext("owner", "session", "web")
            await runtime.start()
            engine = EngineFacade(PluginCapabilityToolBridge(runtime, config_base_dir=root))
            engine.store = files.store
            engine.capability_config_base_dir = root
            try:
                self.assertNotIn(CAPABILITY_ID, runtime.capability_ids)
                self.discovery_snapshot(engine, installed=False)
                catalog = await service.browse_market()
                self.assertTrue(catalog["ok"], catalog)
                entry = next(item for item in catalog["plugins"] if item["plugin_id"] == PLUGIN_ID)
                self.assertEqual(entry["plugin_id"], PLUGIN_ID)
                self.assertEqual(entry["installed_status"], "not_installed")
                staged = await service.stage_market(plugin_id=PLUGIN_ID, digest=entry["sha256"])
                self.assertTrue(staged["ok"], staged)
                installed = await service.install_stage(
                    stage_id=staged["stage_id"], approved_permissions=staged["permissions"]
                )
                self.assertTrue(installed["ok"], installed)
                self.assertEqual(
                    next(item for item in (await service.browse_market())["plugins"] if item["plugin_id"] == PLUGIN_ID)[
                        "installed_status"
                    ],
                    "active",
                )
                self.assertIn(CAPABILITY_ID, runtime.capability_ids)
                self.assertEqual(runtime.capability_descriptors[CAPABILITY_ID].raw["execution_class"], "long_task")
                self.assertEqual(runtime.stable_system_prompt_blocks(), ())
                first_snapshot = self.discovery_snapshot(engine, installed=True)
                self.assertEqual(first_snapshot, self.discovery_snapshot(engine, installed=True))
                engine.execution_provider = object()
                self.assertEqual(first_snapshot, self.discovery_snapshot(engine, installed=True))
                engine.execution_provider = None
                result = await service.invoke_capability(
                    CAPABILITY_ID,
                    dict(
                        source_id=item["attachment_id"],
                        output_format="wav",
                        start_time="1",
                        end_time="3",
                        speed_ratio=2,
                        sample_rate=8000,
                        channels=1,
                    ),
                    context=context,
                )
                self.assertFalse(result.is_error, result)
                self.assertNotIn(str(root), str(result))
                ref = result.content["managed_artifacts"][0]
                resolved = files.resolve_input_resource(
                    profile_user_id="owner", session_id="session", target=ref["generated_handle"], timestamp=None
                )
                with wave.open(resolved["absolute_path"], "rb") as output:
                    self.assertEqual(output.getframerate(), 8000)
                    self.assertEqual(output.getnchannels(), 1)
                    self.assertAlmostEqual(output.getnframes() / 8000, 1, delta=0.06)
                second = await service.invoke_capability(
                    CAPABILITY_ID, dict(source_id=ref["generated_handle"], output_format="mp3"), context=context
                )
                self.assertFalse(second.is_error, second)
                handler = engine._resolve_tool_handlers()[CAPABILITY_ID]
                projected = await asyncio.to_thread(
                    handler.execute,
                    call=handler.normalize_call(
                        {"type": CAPABILITY_ID, "source_id": ref["generated_handle"], "output_format": "flac"}
                    ),
                    context=ToolExecutionContext("owner", "session", 1, {}, client_mode="qq_text"),
                )
                self.assertEqual(projected.state_updates["adapter_capability_status"], "ok", projected)
                events = [event for event in projected.stream_events if event["type"] == "generated_file_ready"]
                self.assertEqual(len(events), 1)
                self.assertFalse(events[0]["send_to_user"])
                self.assertEqual(events[0]["delivery_scope"], "plugin_managed_artifact")
                delivery = files.send_file(
                    profile_user_id="owner",
                    session_id="session",
                    target=events[0]["generated_file"]["generated_handle"],
                )
                self.assertTrue(delivery["ok"], delivery)
                self.assertEqual(delivery["files"][0]["file_ext"], "flac")
                self.assertEqual(first_snapshot, self.discovery_snapshot(engine, installed=True))
                for invalid_context in (
                    InvocationContext("other", "session", "web"),
                    InvocationContext("owner", "other", "web"),
                ):
                    rejected = await service.invoke_capability(
                        CAPABILITY_ID, dict(source_id=ref["generated_id"], output_format="wav"), context=invalid_context
                    )
                    self.assertEqual(rejected.reason, "resource_not_found")
                unknown = await service.invoke_capability(
                    CAPABILITY_ID, dict(source_id="gen_999", output_format="wav"), context=context
                )
                self.assertEqual(unknown.reason, "resource_not_found")
                self.assertEqual(list((root / "copies").iterdir()), [])
                self.assertEqual(hashlib.sha256(audio.read_bytes()).hexdigest(), before)
                # A bad dependency in a new candidate must not replace the live one.
                with patch.dict(os.environ, {"AKANE_MEDIA_FFMPEG": "akane-missing-ffmpeg"}):
                    failed = await service.stage_source(source_path=str(source_project))
                self.assertFalse(failed["ok"], failed)
                self.assertEqual(failed["reason"], "ffmpeg_not_found")
                good = await service.invoke_capability(
                    CAPABILITY_ID, dict(source_id=item["attachment_id"], output_format="flac"), context=context
                )
                self.assertFalse(good.is_error, good)
                await self.exercise_host_jobs(
                    root=root, engine=engine, files=files, attachments=attachments, source_id=item["attachment_id"]
                )
                disabled = await service.set_enabled(plugin_id=PLUGIN_ID, enabled=False)
                self.assertTrue(disabled["ok"], disabled)
                self.assertNotIn(CAPABILITY_ID, runtime.capability_ids)
                self.discovery_snapshot(engine, installed=False)
                absent = await service.invoke_capability(CAPABILITY_ID, {}, context=context)
                self.assertTrue(absent.is_error)
                enabled = await service.set_enabled(plugin_id=PLUGIN_ID, enabled=True)
                self.assertTrue(enabled["ok"], enabled)
                self.assertIn(CAPABILITY_ID, runtime.capability_ids)
                self.assertEqual(first_snapshot, self.discovery_snapshot(engine, installed=True))
                removed = await service.uninstall(plugin_id=PLUGIN_ID)
                self.assertTrue(removed["ok"], removed)
                self.assertNotIn(CAPABILITY_ID, runtime.capability_ids)
                self.discovery_snapshot(engine, installed=False)
                self.assertEqual(artifacts.snapshot()["plugins"], [])
                self.assertEqual(selections.load(), ())
                self.assertEqual(
                    next(item for item in (await service.browse_market())["plugins"] if item["plugin_id"] == PLUGIN_ID)[
                        "installed_status"
                    ],
                    "not_installed",
                )
            finally:
                await runtime.stop()


class MediaConversionCutoverTests(unittest.TestCase):
    def test_no_host_conversion_authority_or_fixed_plugin_prompt(self):
        self.assertFalse(hasattr(GeneratedFileService, "convert_media_file"))
        self.assertFalse(hasattr(GeneratedFileService, "media_conversion_status"))
        self.assertNotIn("CONVERT_MEDIA_FILE", vars(config))
        for path in (PROJECT_ROOT / "companion_v01").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("convert_media_file", source, str(path))
            self.assertNotIn("akane.media-convert", source, str(path))
