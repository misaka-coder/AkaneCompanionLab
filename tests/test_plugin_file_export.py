"""The public file helper crosses a real worker boundary into host artifacts."""

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from akane_plugin import InvocationContext, ManagedArtifactDraft
from companion_v01.plugin_generation import PluginGenerationProcess
from companion_v01.plugin_managed_artifacts import GeneratedFileManagedArtifactSink, validate_managed_artifact_draft
from tests.test_plugin_artifact_delivery import write_plugin, ROOT, PLUGIN, CAPABILITY
from tests.test_plugin_resources import services


class PluginFileExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_file_helper_materializes_generic_formats_in_real_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, files = services(root)
            site = write_plugin(root)
            process = PluginGenerationProcess(project_root=ROOT, site_dir=site, plugin_id=PLUGIN,
                                              work_dir=root / "worker")
            process.bind_managed_artifact_sink(GeneratedFileManagedArtifactSink(files))
            await asyncio.to_thread(process.start)
            try:
                for extension in ("md", "pptx", "zip", "svg", "tiff", "psd", "mp4", "mkv", "akbin"):
                    with self.subTest(extension=extension):
                        result = await process.invoke(CAPABILITY, {"output_format": extension, "send_to_user": False},
                            context=InvocationContext("owner", "session", "qq_text"))
                        self.assertFalse(result.is_error, result)
                        ref = result.content["managed_artifacts"][0]
                        self.assertFalse(ref["send_to_user"])
                        self.assertNotIn(str(site), str(result.content))
                        stored = files.resolve_generated_artifact(profile_user_id="owner", session_id="session",
                                                                  target=ref["generated_handle"])
                        self.assertEqual(Path(stored["absolute_path"]).read_bytes(),
                                         (site / ("render." + extension)).read_bytes())
                        self.assertIsNone(files.resolve_generated_artifact(profile_user_id="owner", session_id="other",
                                                                           target=ref["generated_handle"]))
            finally:
                await asyncio.to_thread(process.stop)

    def test_helper_resolves_path_and_keeps_host_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "report.md"
            source.write_bytes(b"# report")
            with patch("mimetypes.guess_type", return_value=("application/wrong", None)):
                draft = ManagedArtifactDraft.from_file(source)
            self.assertEqual(draft.path, source.resolve())
            self.assertEqual(draft.mime_type, "text/markdown")
            self.assertFalse(draft.send_to_user)
            self.assertEqual(validate_managed_artifact_draft(draft).file_size, 8)
            with self.assertRaisesRegex(ValueError, "format_unsupported"):
                no_ext = source.with_name("README")
                no_ext.write_bytes(b"file")
                ManagedArtifactDraft.from_file(no_ext)
            with self.assertRaises(FileNotFoundError):
                ManagedArtifactDraft.from_file(source.with_name("missing.md"))
            bad = ManagedArtifactDraft.from_file(source, mime_type="image/png")
            with self.assertRaisesRegex(Exception, "mime_mismatch"):
                validate_managed_artifact_draft(bad)


if __name__ == "__main__":
    unittest.main()
