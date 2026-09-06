"""Install the real distribution; never import plugin source into the host test."""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import shutil
import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import patch

from capcore import InvocationContext

from companion_v01.extension_management import ExtensionManagementService, PluginSelectionStore
from companion_v01.plugin_generation_candidate import PluginGenerationCandidateBuilder
from companion_v01.plugin_generation_runtime import PluginGenerationRuntime
from companion_v01.plugin_installation import ManagedPluginArtifactStore
from companion_v01.plugin_managed_artifacts import GeneratedFileManagedArtifactSink
from companion_v01.plugin_market import StaticPluginMarket
from companion_v01.plugin_resources import GeneratedFileResourceProvider
from scripts.build_plugin_market import build_market
from tests.test_plugin_resources import services


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "akane.media-convert"
CAPABILITY_ID = f"{PLUGIN_ID}.run.v1"


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and FFprobe required")
class MediaPluginInstallationTests(unittest.IsolatedAsyncioTestCase):
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
            try:
                self.assertNotIn(CAPABILITY_ID, runtime.capability_ids)
                catalog = await service.browse_market()
                self.assertTrue(catalog["ok"], catalog)
                entry = catalog["plugins"][0]
                self.assertEqual(entry["plugin_id"], PLUGIN_ID)
                self.assertEqual(entry["installed_status"], "not_installed")
                staged = await service.stage_market(plugin_id=PLUGIN_ID, digest=entry["sha256"])
                self.assertTrue(staged["ok"], staged)
                installed = await service.install_stage(
                    stage_id=staged["stage_id"], approved_permissions=staged["permissions"]
                )
                self.assertTrue(installed["ok"], installed)
                self.assertEqual((await service.browse_market())["plugins"][0]["installed_status"], "active")
                self.assertIn(CAPABILITY_ID, runtime.capability_ids)
                self.assertEqual(runtime.capability_descriptors[CAPABILITY_ID].raw["execution_class"], "long_task")
                self.assertEqual(runtime.stable_system_prompt_blocks(), ())
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
                disabled = await service.set_enabled(plugin_id=PLUGIN_ID, enabled=False)
                self.assertTrue(disabled["ok"], disabled)
                self.assertNotIn(CAPABILITY_ID, runtime.capability_ids)
                absent = await service.invoke_capability(CAPABILITY_ID, {}, context=context)
                self.assertTrue(absent.is_error)
                enabled = await service.set_enabled(plugin_id=PLUGIN_ID, enabled=True)
                self.assertTrue(enabled["ok"], enabled)
                self.assertIn(CAPABILITY_ID, runtime.capability_ids)
                removed = await service.uninstall(plugin_id=PLUGIN_ID)
                self.assertTrue(removed["ok"], removed)
                self.assertNotIn(CAPABILITY_ID, runtime.capability_ids)
                self.assertEqual(artifacts.snapshot()["plugins"], [])
                self.assertEqual(selections.load(), ())
                self.assertEqual((await service.browse_market())["plugins"][0]["installed_status"], "not_installed")
            finally:
                await runtime.stop()
