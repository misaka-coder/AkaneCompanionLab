from __future__ import annotations

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from capcore import CapabilityDescriptor, CapabilityIOSlot

from companion_v01.desktop_music_timeline import DesktopMusicTimelineService
from companion_v01.optional_media_binding import (
    prepare_timeline_vocals,
    prepare_timeline_transcript,
)
from companion_v01.plugin_tool_bridge import PluginCapabilityToolHandler


class TimelinePluginBindingTests(unittest.TestCase):
    def test_document_business_has_one_plugin_authority(self):
        from companion_v01.generated_files import GeneratedFileService
        from companion_v01.attachment_inbox import AttachmentInboxService
        from companion_v01.capability_registry import CapabilityRegistry
        from companion_v01.client_protocol import ClientMode

        retired = ("compose_file", "revise_generated_file", "apply_style_to_existing_file")
        for name in retired:
            self.assertFalse(hasattr(GeneratedFileService, name))
        self.assertFalse(hasattr(AttachmentInboxService, "read_material_for_generation"))
        for mode in ClientMode:
            self.assertTrue(set(retired).isdisjoint(CapabilityRegistry().tool_names_for_mode(mode)))
        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / "companion_v01/generated_files_io.py").exists())
        for path in (root / "companion_v01").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for symbol in (
                *retired,
                "COMPOSE_FILE_TOOL_SPEC",
                "REVISE_GENERATED_FILE_TOOL_SPEC",
                "APPLY_STYLE_TO_EXISTING_FILE_TOOL_SPEC",
                "from akane_document_writer",
                "from plugins.akane_document_writer",
                "def _normalize_formatting(",
                "def _build_fallback_markdown(",
            ):
                self.assertNotIn(symbol, source, str(path))

    def test_cover_song_has_one_plugin_authority(self):
        from companion_v01.local_media_executor import LocalMediaExecutorClient
        from companion_v01.capability_registry import CapabilityRegistry
        from companion_v01.client_protocol import ClientMode

        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / "companion_v01/cover_song.py").exists())
        self.assertFalse(hasattr(LocalMediaExecutorClient, "render_cover_song"))
        for mode in ClientMode:
            self.assertNotIn("cover_song", CapabilityRegistry().tool_names_for_mode(mode))
        for path in (root / "companion_v01").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for symbol in (
                "CoverSongToolHandler",
                "COVER_SONG_TOOL_SPEC",
                "COVER_SONG_INPUT_SCHEMA",
                "_get_cover_song_service",
                "has_cover_song_cache",
                "LocalRvcExecutorProvider",
                "from akane_cover_song",
                "from plugins.akane_cover_song",
            ):
                self.assertNotIn(symbol, source, str(path))

    def test_image_generation_has_one_plugin_authority(self):
        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / "companion_v01/image_generation.py").exists())
        for path in (root / "companion_v01").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for symbol in (
                "GenerateImageToolHandler",
                "GENERATE_IMAGE_TOOL_SPEC",
                "PinAIImageProvider",
                "_get_image_generation_service",
            ):
                self.assertNotIn(symbol, source, str(path))

    def test_dataset_business_has_one_plugin_authority(self):
        from companion_v01.generated_files import GeneratedFileService
        from companion_v01 import generated_files_media, generated_files_cards

        self.assertFalse(hasattr(GeneratedFileService, "prepare_voice_dataset"))
        self.assertFalse(hasattr(generated_files_media, "slice_voice_samples"))
        self.assertFalse(hasattr(generated_files_cards, "build_voice_dataset_content_card"))
        root = Path(__file__).resolve().parents[1]
        for folder in ("companion_v01", "scripts"):
            for path in (root / folder).rglob("*.py"):
                source = path.read_text(encoding="utf-8")
                for symbol in (
                    "prepare_voice_dataset",
                    "PrepareVoiceDatasetToolHandler",
                    "VOICE_DATASET_PRESETS",
                    "PREPARE_VOICE_DATASET_TOOL_SPEC",
                    "prepare_dataset_voice",
                    "akane.voice-dataset",
                ):
                    self.assertNotIn(symbol, source, str(path))

    def test_old_business_and_prompt_authority_are_deleted(self):
        from companion_v01.generated_files import GeneratedFileService
        from companion_v01 import generated_files_media

        self.assertFalse(hasattr(GeneratedFileService, "separate_audio_stems"))
        self.assertFalse(hasattr(GeneratedFileService, "audio_separation_status"))
        self.assertFalse(hasattr(generated_files_media, "separate_audio_with_demucs"))
        self.assertFalse(hasattr(GeneratedFileService, "clean_voice_track"))
        self.assertFalse(hasattr(GeneratedFileService, "voice_cleaning_status"))
        self.assertFalse(hasattr(generated_files_media, "clean_voice_track"))
        root = Path(__file__).resolve().parents[1]
        for folder in ("companion_v01", "scripts"):
            for path in (root / folder).rglob("*.py"):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("separate_audio_stems", source, str(path))
                self.assertNotIn("separate_audio_with_demucs", source, str(path))
                self.assertNotIn("clean_voice_track", source, str(path))
                self.assertNotIn("_build_basic_voice_clean_filter_chain", source, str(path))
                if path.name != "optional_media_binding.py":
                    self.assertNotIn("akane.audio-separation", source, str(path))
                    self.assertNotIn("akane.voice-clean", source, str(path))

    def test_transcription_internal_call_does_not_bypass_admission(self):
        capability = "akane.file-transcription.run.v1"
        adapter = SimpleNamespace(invoke=Mock(side_effect=AssertionError("Approval required")))
        descriptor = CapabilityDescriptor(
            id=capability,
            display_name="Transcription",
            short_hint="test",
            visible_in=("base", "desktop"),
            prompt_exposed=True,
            risk="high",
            confirm="always",
            effects=("filesystem",),
            trigger=None,
            inputs=(
                CapabilityIOSlot("source_ids", "array", raw={"items": {"type": "string"}}),
                CapabilityIOSlot("output_format", "string"),
                CapabilityIOSlot("merge_outputs", "boolean"),
                CapabilityIOSlot("with_timestamps", "boolean"),
                CapabilityIOSlot("send_to_user", "boolean"),
            ),
            outputs=(),
            raw={},
        )
        with tempfile.TemporaryDirectory() as tmp:
            handler = PluginCapabilityToolHandler(
                capability_id=capability, adapter=adapter, descriptor=descriptor, config_base_dir=tmp
            )
            engine = SimpleNamespace(_resolve_tool_handlers=lambda **_: {capability: handler})
            result = prepare_timeline_transcript(
                engine, profile_user_id="owner", session_id="session", source_id="audio_1", options={}
            )
        self.assertEqual(result["error"], "transcription_not_admitted_or_failed")
        adapter.invoke.assert_not_called()

    def test_old_file_transcription_business_is_deleted_but_voice_shape_remains(self):
        from companion_v01.generated_files import GeneratedFileService
        from companion_v01 import generated_files_media

        self.assertFalse(hasattr(GeneratedFileService, "transcribe_media"))
        self.assertFalse(hasattr(GeneratedFileService, "asr_status"))
        self.assertFalse(hasattr(generated_files_media, "load_faster_whisper_model"))
        self.assertFalse(hasattr(generated_files_media, "transcribe_media"))
        self.assertTrue(hasattr(GeneratedFileService, "_prepare_transcription_input"))
        root = Path(__file__).resolve().parents[1]
        for folder in ("companion_v01", "scripts"):
            for path in (root / folder).rglob("*.py"):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("def transcribe_media(", source, str(path))
                self.assertNotIn("class TranscribeMediaToolHandler", source, str(path))
                self.assertNotIn("TRANSCRIBE_MEDIA_TOOL_SPEC", source, str(path))
                self.assertNotIn("from faster_whisper import WhisperModel", source, str(path))

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

    def test_timeline_only_passes_scoped_handles_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "source.wav"
            original.write_bytes(b"original")
            separation = Mock(return_value={"status": "ready", "handle": "gen_vocals"})
            transcription = Mock(return_value={"status": "ready", "segments": [{"text": "fixture"}]})
            service = DesktopMusicTimelineService(
                store=None, generated_file_service=None, vocal_preparer=separation, transcriber=transcription
            )
            source = {"absolute_path": str(original), "source_id": "audio_1"}
            result = service._transcribe_source(source, profile_user_id="owner", session_id="session")
            self.assertEqual(result["quality"], "vocal_asr")
            self.assertEqual(transcription.call_args.kwargs["source_id"], "gen_vocals")
            self.assertNotIn("absolute_path", transcription.call_args.kwargs)
            self.assertEqual(transcription.call_args.kwargs["profile_user_id"], "owner")
            service.vocal_preparer = None
            result = service._transcribe_source(source, profile_user_id="owner", session_id="session")
            self.assertEqual(result["quality"], "mixed_asr")
            self.assertEqual(result["separation_reason"], "separation_plugin_unavailable")
            self.assertEqual(transcription.call_args.kwargs["source_id"], "audio_1")
            self.assertEqual(original.read_bytes(), b"original")
            self.assertEqual(list(Path(tmp).iterdir()), [original])


if __name__ == "__main__":
    unittest.main()
