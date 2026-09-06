from __future__ import annotations

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from capcore import CapabilityDescriptor, CapabilityIOSlot

from companion_v01.desktop_music_timeline import DesktopMusicTimelineService
from companion_v01.optional_media_binding import prepare_timeline_vocals
from companion_v01.plugin_tool_bridge import PluginCapabilityToolHandler


class TimelinePluginBindingTests(unittest.TestCase):
    def test_old_business_and_prompt_authority_are_deleted(self):
        from companion_v01.generated_files import GeneratedFileService
        from companion_v01 import generated_files_media

        self.assertFalse(hasattr(GeneratedFileService, "separate_audio_stems"))
        self.assertFalse(hasattr(GeneratedFileService, "audio_separation_status"))
        self.assertFalse(hasattr(generated_files_media, "separate_audio_with_demucs"))
        root = Path(__file__).resolve().parents[1]
        for folder in ("companion_v01", "scripts"):
            for path in (root / folder).rglob("*.py"):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("separate_audio_stems", source, str(path))
                self.assertNotIn("separate_audio_with_demucs", source, str(path))
                if path.name != "optional_media_binding.py":
                    self.assertNotIn("akane.audio-separation", source, str(path))

    def test_missing_plugin_and_missing_scope_never_call_business(self):
        engine = SimpleNamespace(_resolve_tool_handlers=Mock(return_value={}))
        result = prepare_timeline_vocals(engine, profile_user_id="owner", session_id="session", source_id="audio_1")
        self.assertEqual(result["reason"], "separation_plugin_unavailable")
        result = prepare_timeline_vocals(engine, profile_user_id="", session_id="session", source_id="audio_1")
        self.assertEqual(result["reason"], "separation_source_scope_missing")
        engine._resolve_tool_handlers.assert_called_once()

    def test_real_admission_does_not_bypass_permission_for_internal_preprocessing(self):
        capability = "akane.audio-separation.run.v1"
        adapter = SimpleNamespace(invoke=Mock(side_effect=AssertionError("Must not execute before approval")))
        descriptor = CapabilityDescriptor(
            id=capability,
            display_name="Separation permission fixture",
            short_hint="test",
            visible_in=("base", "desktop"),
            prompt_exposed=True,
            risk="high",
            confirm="always",
            effects=("filesystem",),
            trigger=None,
            inputs=(
                CapabilityIOSlot(name="source_id", kind="string", required=True),
                CapabilityIOSlot(name="output_format", kind="string", required=False),
                CapabilityIOSlot(name="send_to_user", kind="boolean", required=False),
            ),
            outputs=(),
            raw={},
        )
        with tempfile.TemporaryDirectory() as tmp:
            handler = PluginCapabilityToolHandler(
                capability_id=capability,
                adapter=adapter,
                descriptor=descriptor,
                config_base_dir=tmp,
            )
            engine = SimpleNamespace(_resolve_tool_handlers=lambda **_: {capability: handler})
            result = prepare_timeline_vocals(engine, profile_user_id="owner", session_id="session", source_id="audio_1")
        self.assertEqual(result["reason"], "separation_not_admitted_or_failed")
        adapter.invoke.assert_not_called()

    def test_timeline_copies_original_and_plugin_vocals_before_asr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original, vocals = root / "source.wav", root / "vocals.wav"
            original.write_bytes(b"original")
            vocals.write_bytes(b"fixture vocals")
            callback = Mock(return_value={"status": "ready", "absolute_path": str(vocals)})
            service = DesktopMusicTimelineService(store=None, generated_file_service=None, vocal_preparer=callback)
            captured = []

            def transcribe(**kwargs):
                path = kwargs["audio_path"]
                self.assertNotEqual(path.parent, root)
                self.assertTrue(path.is_file())
                (path.parent / "prepared.wav").write_bytes(b"ASR scratch")
                captured.append(path)
                return {"status": "ready", "quality": kwargs["quality"], "segments": [{"text": "fixture"}]}

            service._transcribe_audio_path = transcribe
            with (
                patch("companion_v01.desktop_music_timeline.importlib.util.find_spec", return_value=object()),
                patch("companion_v01.desktop_music_timeline.shutil.which", return_value="ffmpeg"),
            ):
                ready = service._transcribe_source(
                    {"absolute_path": str(original), "source_id": "audio_1"},
                    profile_user_id="owner",
                    session_id="session",
                )
                self.assertEqual(ready["quality"], "vocal_asr")
                callback.assert_called_once_with(profile_user_id="owner", session_id="session", source_id="audio_1")
                service.vocal_preparer = None
                mixed = service._transcribe_source({"absolute_path": str(original)})
            self.assertEqual(mixed["quality"], "mixed_asr")
            self.assertEqual(mixed["separation_reason"], "separation_plugin_unavailable")
            self.assertTrue(all(not p.exists() for p in captured))
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["source.wav", "vocals.wav"])
            self.assertEqual(original.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
