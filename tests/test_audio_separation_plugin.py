"""Real wheel/worker/model integration; the host never imports plugin source."""

from __future__ import annotations

import asyncio
from array import array
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import wave

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
from companion_v01.tool_runtime import ToolExecutionContext
from scripts.build_plugin_market import build_market
from tests.test_plugin_engine_bridge import EngineFacade
from tests.test_plugin_resources import services


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "akane.audio-separation"
CAPABILITY_ID = PLUGIN_ID + ".run.v1"


@unittest.skipUnless(
    importlib.util.find_spec("demucs") and shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "Prepared Demucs and FFmpeg required",
)
class SeparationInstallationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_market_install_model_conversion_scope_and_lifecycle(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                os.environ,
                {
                    "AKANE_SEPARATION_BACKEND": "local",
                    "AKANE_SEPARATION_MODEL": "htdemucs",
                    "AKANE_SEPARATION_DEVICE": "cpu",
                },
            ),
        ):
            root = Path(temporary)
            source_project = root / "source"
            shutil.copytree(PROJECT_ROOT / "plugins" / "akane_audio_separation", source_project)
            # A real private release catalog exercises the market before public
            # cutover. Do not expose duplicate old/new tools in the user catalog.
            manifest = root / "market.toml"
            manifest.write_text(
                """schema_version = 1
[[plugins]]
source = "source"
plugin_id = "akane.audio-separation"
display_name = "人声／伴奏分轨"
summary = "真实 Demucs 或现有本机媒体服务"
permissions = ["capability.prompt.invoke", "resource.read", "artifact.write", "network.read"]
requirements = ["已准备的 Demucs/PyTorch 运行时、模型与 FFmpeg/FFprobe；不自动下载。"]
""",
                encoding="utf-8",
            )
            index = await asyncio.to_thread(build_market, root / "market", manifest=manifest)
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
            await runtime.start()
            try:
                self.assertNotIn(CAPABILITY_ID, engine._resolve_tool_handlers())
                entry = (await service.browse_market())["plugins"][0]
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
                snapshot = json.dumps(build_openai_native_tool_specs(handlers), sort_keys=True)
                self.assertEqual(
                    handler.build_prompt_instruction(), render_legacy_json_tool_instruction(handler.tool_spec())
                )
                self.assertEqual(handler.background_job_policy(), ("agent", "timeline"))
                source_id = item["attachment_id"]
                for fmt in ("wav", "flac", "mp3"):
                    projected = await asyncio.to_thread(
                        handler.execute,
                        call=handler.normalize_call(
                            {"type": CAPABILITY_ID, "source_id": source_id, "output_format": fmt}
                        ),
                        context=ToolExecutionContext("owner", "session", 1, {}, client_mode="desktop_pet"),
                    )
                    self.assertEqual(projected.state_updates["adapter_capability_status"], "ok", projected)
                    events = [event for event in projected.stream_events if event["type"] == "generated_file_ready"]
                    self.assertEqual(len(events), 2, projected)
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
                                    (44100, 2, 88200),
                                )
                    source_id = events[0]["generated_file"]["generated_handle"]
                    self.assertEqual(list((root / "copies").iterdir()), [])
                    self.assertEqual(
                        json.dumps(build_openai_native_tool_specs(engine._resolve_tool_handlers()), sort_keys=True),
                        snapshot,
                    )
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
                with patch.dict(os.environ, {"AKANE_SEPARATION_MODEL_ROOT": str(root / "missing-model")}):
                    bad = await service.stage_source(source_path=str(source_project))
                self.assertFalse(bad["ok"], bad)
                self.assertEqual(bad["reason"], "demucs_model_missing")
                self.assertIn(CAPABILITY_ID, runtime.capability_ids)
                disabled = await service.set_enabled(plugin_id=PLUGIN_ID, enabled=False)
                self.assertTrue(disabled["ok"], disabled)
                self.assertNotIn(CAPABILITY_ID, engine._resolve_tool_handlers())
                enabled = await service.set_enabled(plugin_id=PLUGIN_ID, enabled=True)
                self.assertTrue(enabled["ok"], enabled)
                self.assertEqual(
                    json.dumps(build_openai_native_tool_specs(engine._resolve_tool_handlers()), sort_keys=True),
                    snapshot,
                )
                removed = await service.uninstall(plugin_id=PLUGIN_ID)
                self.assertTrue(removed["ok"], removed)
                self.assertNotIn(CAPABILITY_ID, engine._resolve_tool_handlers())
                self.assertEqual(artifacts.snapshot()["plugins"], [])
                self.assertEqual(selections.load(), ())
            finally:
                await runtime.stop()


if __name__ == "__main__":
    unittest.main()
