from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import wave
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.capability_registry import CapabilityRegistry, CapabilitySnapshot
from companion_v01.client_protocol import ClientMode
from companion_v01.domain_profiles import DomainProfileRegistry, filter_tool_names
from companion_v01.cover_song import CoverSongService, RvcWebUiProvider
from companion_v01.generated_files import GeneratedFileService
from companion_v01.qq_gateway import NapCatQQGateway, QQMessageContext
from companion_v01.store import MemoryStore
from companion_v01.tool_runtime import CoverSongToolHandler, ToolExecutionContext


def _write_silent_wav(path: Path, *, seconds: float = 1.0, sample_rate: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(b"\x00\x00" * int(seconds * sample_rate))


class _FakeRvcProvider:
    provider_id = "fake_rvc"
    separation_model = "fake_separator"

    def __init__(self) -> None:
        self.separation_calls = 0
        self.conversion_calls = 0

    def capability_status(self):
        return {"enabled": True, "status": "ready", "reason": ""}

    def list_voice_models(self):
        return ["Akie-test.pth", "mi-test.pth"]

    def resolve_voice_model(self, requested: str, *, default_model: str = ""):
        if str(requested or "").lower() in {"", "auto"}:
            return default_model or "Akie-test.pth"
        return str(requested)

    def model_fingerprint(self, model_name: str):
        return {"model": model_name, "size": 1, "mtime_ns": 2}

    @contextmanager
    def exclusive(self):
        yield

    def separate_vocals(self, *, source_path: Path, work_dir: Path):
        self.separation_calls += 1
        vocals = work_dir / "vocals.wav"
        instrumental = work_dir / "instrumental.wav"
        shutil.copy2(source_path, vocals)
        shutil.copy2(source_path, instrumental)
        return vocals, instrumental

    def convert_voice(self, *, source_path: Path, output_path: Path, **kwargs):
        self.conversion_calls += 1
        shutil.copy2(source_path, output_path)
        return {
            "index_path": "fake.index",
            "info": "Success.",
            "timings": {
                "model_selection": 0.12,
                "inference_request": 0.34,
                "feature_extraction": 0.1,
                "pitch_extraction": 0.2,
                "voice_synthesis": 0.04,
            },
        }


class _FakeLocalPipelineProvider(_FakeRvcProvider):
    provider_id = "fake_local_rvc"
    separation_model = "htdemucs"

    def __init__(self) -> None:
        super().__init__()
        self.render_calls = 0

    def render_full_cover(self, *, source_path: Path, output_path: Path, **kwargs):
        self.render_calls += 1
        shutil.copy2(source_path, output_path)
        return {
            "index_path": "",
            "info": "Success.",
            "timings": {
                "separation": 6.2,
                "voice_conversion": 1.4,
                "mix": 0.3,
                "total": 7.9,
            },
        }


class _TestCoverSongService(CoverSongService):
    def _probe_duration(self, path: Path) -> float:
        return 1.0

    def _decode_source(self, *, source_path: Path, output_path: Path) -> None:
        shutil.copy2(source_path, output_path)

    def _mix_tracks(self, *, converted_vocals: Path, instrumental: Path, output_path: Path, **kwargs) -> None:
        shutil.copy2(converted_vocals, output_path)


class CoverSongTests(unittest.TestCase):
    def _build_service(self, root: Path):
        attachment_root = root / "attachments"
        stored = attachment_root / "user" / "song.wav"
        _write_silent_wav(stored)
        store = MemoryStore(root / "db")
        attachment_service = AttachmentInboxService(store=store, base_dir=attachment_root)
        attachment = attachment_service.create_pending(
            profile_user_id="user",
            session_id="session",
            source="qq",
            kind="audio",
            origin_name="测试歌曲.wav",
            storage_relpath="user/song.wav",
            timestamp=100,
        )
        attachment_service.mark_ready(
            profile_user_id="user",
            session_id="session",
            attachment_id=attachment["attachment_id"],
            summary_title="测试歌曲",
            short_hint="测试歌曲音频",
            detail={"file_kind": "wav"},
            timestamp=101,
        )
        generated_service = GeneratedFileService(
            base_dir=root / "outputs",
            work_dir=root / "work",
            store=store,
            attachment_service=attachment_service,
        )
        provider = _FakeRvcProvider()
        service = _TestCoverSongService(
            generated_file_service=generated_service,
            provider=provider,
            cache_root=root / "cache",
            default_model="Akie-test.pth",
            default_output_format="wav",
            ffmpeg_path="ffmpeg",
        )
        return service, provider, generated_service

    def test_cover_song_generates_and_reuses_content_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, provider, _generated = self._build_service(Path(temp_dir))
            self.assertFalse(service.has_cached_cover(profile_user_id="user"))
            first = service.cover_song(
                profile_user_id="user",
                session_id="session",
                source_target="audio_001",
                song_title="测试歌曲",
                voice_model="auto",
                output_format="wav",
                delivery="none",
                timestamp=200,
            )
            second = service.cover_song(
                profile_user_id="user",
                session_id="session",
                source_target="audio_001",
                song_title="测试歌曲",
                voice_model="auto",
                output_format="wav",
                delivery="none",
                timestamp=300,
            )

            self.assertTrue(first["ok"])
            self.assertTrue(service.has_cached_cover(profile_user_id="user"))
            self.assertFalse(first["cache_hit"])
            self.assertTrue(second["ok"])
            self.assertTrue(second["cache_hit"])
            self.assertEqual(provider.separation_calls, 1)
            self.assertEqual(provider.conversion_calls, 1)
            self.assertEqual(first["generated"]["generated_handle"], "gen_001")
            self.assertEqual(second["generated"]["generated_handle"], "gen_002")
            self.assertTrue(Path(second["generated"]["absolute_path"]).exists())

    def test_local_full_pipeline_skips_old_stem_transfer_chain_and_reuses_final_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, _old_provider, _generated = self._build_service(Path(temp_dir))
            provider = _FakeLocalPipelineProvider()
            service.provider = provider

            first = service.cover_song(
                profile_user_id="user",
                session_id="session",
                source_target="audio_001",
                song_title="测试歌曲",
                voice_model="auto",
                output_format="wav",
                delivery="none",
                timestamp=200,
            )
            second = service.cover_song(
                profile_user_id="user",
                session_id="session",
                source_target="audio_001",
                song_title="测试歌曲",
                voice_model="auto",
                output_format="wav",
                delivery="none",
                timestamp=300,
            )

            self.assertTrue(first["ok"])
            self.assertFalse(first["cache_hit"])
            self.assertTrue(second["ok"])
            self.assertTrue(second["cache_hit"])
            self.assertEqual(provider.render_calls, 1)
            self.assertEqual(provider.separation_calls, 0)
            self.assertEqual(provider.conversion_calls, 0)
            self.assertIn("local_full_pipeline", first["processing"]["seconds"])
            self.assertEqual(first["processing"]["rvc"]["separation"], 6.2)

    def test_cover_song_restores_cached_song_without_new_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, provider, _generated = self._build_service(Path(temp_dir))
            created = service.cover_song(
                profile_user_id="user",
                session_id="session",
                source_target="audio_001",
                song_title="测试歌曲",
                artist="测试原唱",
                voice_model="auto",
                output_format="wav",
                delivery="none",
                timestamp=200,
            )
            restored = service.cover_song(
                profile_user_id="user",
                session_id="next_session",
                source_target="",
                song_title="测试歌曲",
                artist="测试原唱",
                voice_model="auto",
                output_format="wav",
                delivery="file",
                timestamp=400,
            )

            self.assertTrue(created["ok"])
            self.assertTrue(restored["ok"])
            self.assertTrue(restored["cache_hit"])
            self.assertEqual(provider.separation_calls, 1)
            self.assertEqual(provider.conversion_calls, 1)
            self.assertEqual(restored["generated"]["generated_handle"], "gen_001")
            self.assertEqual(restored["delivery_mode"], "file")

    def test_cover_song_reuses_stems_when_voice_model_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, provider, _generated = self._build_service(Path(temp_dir))
            first = service.cover_song(
                profile_user_id="user",
                session_id="session",
                source_target="audio_001",
                song_title="测试歌曲",
                voice_model="Akie-test.pth",
                output_format="wav",
                delivery="none",
                timestamp=200,
            )
            second = service.cover_song(
                profile_user_id="user",
                session_id="session",
                source_target="audio_001",
                song_title="测试歌曲",
                voice_model="mi-test.pth",
                output_format="wav",
                delivery="none",
                timestamp=300,
            )

            self.assertTrue(first["ok"])
            self.assertTrue(second["ok"])
            self.assertEqual(provider.separation_calls, 1)
            self.assertEqual(provider.conversion_calls, 2)
            self.assertFalse(first["processing"]["stems_cache_hit"])
            self.assertTrue(first["processing"]["stems_cache_stored"])
            self.assertTrue(second["processing"]["stems_cache_hit"])
            self.assertNotIn("separation", second["processing"]["seconds"])
            self.assertEqual(second["processing"]["rvc"]["pitch_extraction"], 0.2)
            self.assertNotIn(str(Path(temp_dir)), json.dumps(second["processing"], ensure_ascii=False))

    def test_cover_song_force_rebuild_bypasses_stem_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, provider, _generated = self._build_service(Path(temp_dir))
            first = service.cover_song(
                profile_user_id="user",
                session_id="session",
                source_target="audio_001",
                voice_model="auto",
                output_format="wav",
                delivery="none",
                timestamp=200,
            )
            rebuilt = service.cover_song(
                profile_user_id="user",
                session_id="session",
                source_target="audio_001",
                voice_model="auto",
                output_format="wav",
                delivery="none",
                force_rebuild=True,
                timestamp=300,
            )

            self.assertTrue(first["ok"])
            self.assertTrue(rebuilt["ok"])
            self.assertEqual(provider.separation_calls, 2)
            self.assertEqual(provider.conversion_calls, 2)
            self.assertFalse(rebuilt["processing"]["stems_cache_hit"])

    def test_rvc_provider_extracts_safe_stage_timings(self) -> None:
        provider = RvcWebUiProvider(base_url="http://127.0.0.1:7899")

        timings = provider._parse_rvc_timings(
            "Success.\nIndex:\nC:/private/model.index.\nTime:\nnpy: 1.25s, f0: 2.50s, infer: 3.75s."
        )

        self.assertEqual(
            timings,
            {
                "feature_extraction": 1.25,
                "pitch_extraction": 2.5,
                "voice_synthesis": 3.75,
            },
        )
        self.assertNotIn("private", json.dumps(timings))

    def test_cover_song_tool_auto_delivery_becomes_qq_voice(self) -> None:
        class FakeService:
            def capability_status(self):
                return {"enabled": True, "status": "ready", "reason": ""}

            def cover_song(self, **kwargs):
                self.kwargs = kwargs
                return {
                    "ok": True,
                    "generated": {"generated_id": "generated::1", "generated_handle": "gen_001"},
                    "send_to_user": True,
                    "delivery_mode": kwargs["delivery"],
                    "followup_context": "翻唱完成。",
                }

        fake_service = FakeService()
        handler = CoverSongToolHandler(cover_song_service=fake_service)
        call = handler.normalize_call(
            {"type": "cover_song", "source_id": "audio_001", "song_title": "测试歌曲", "delivery": "auto"}
        )
        result = handler.execute(
            call=call or {},
            context=ToolExecutionContext(
                profile_user_id="user",
                session_id="session",
                now_ts=100,
                visual_payload={},
                client_mode="qq_text",
            ),
        )

        self.assertEqual(fake_service.kwargs["delivery"], "voice")
        self.assertEqual(result.stream_events[0]["delivery_mode"], "voice")
        self.assertEqual(result.stream_events[0]["delivery_scope"], "cover_song")

    def test_cover_song_tool_is_latent_without_media_or_cache(self) -> None:
        selection = CapabilityRegistry().select(CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT))

        self.assertNotIn("cover_song", selection.module_names)
        self.assertNotIn("cover_song", selection.tool_names)
        self.assertNotIn("media_workbench", selection.module_names)
        disclosure = next(item for item in selection.disclosures if item.capability_id == "cover_song")
        self.assertEqual(disclosure.state, "latent")
        self.assertIn("上传一首歌", disclosure.activation)

    def test_cover_song_tool_is_visible_for_cached_requests(self) -> None:
        selection = CapabilityRegistry().select(
            CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT, has_cover_song_cache=True)
        )

        self.assertIn("cover_song", selection.module_names)
        self.assertIn("cover_song", selection.tool_names)
        self.assertNotIn("media_workbench", selection.module_names)

    def test_default_domain_profile_keeps_explicit_cover_song_request(self) -> None:
        profile = DomainProfileRegistry().get("default")

        self.assertEqual(filter_tool_names(("cover_song",), profile), ("cover_song",))

    def test_rvc_provider_discovers_model_choices_from_named_dependency(self) -> None:
        provider = RvcWebUiProvider(base_url="http://127.0.0.1:7899")
        config = {
            "components": [
                {"id": 6, "type": "dropdown", "props": {"label": "推理音色", "choices": ["A.pth", ["B.pth", "B.pth"]]}},
            ],
            "dependencies": [{"api_name": "infer_change_voice", "inputs": [6], "outputs": []}],
        }
        with patch.object(provider, "_load_config", return_value=config):
            self.assertEqual(provider.list_voice_models(), ["A.pth", "B.pth"])
            self.assertEqual(provider.resolve_voice_model("b", default_model=""), "B.pth")

    def test_rvc_capability_probe_uses_short_forced_timeout(self) -> None:
        provider = RvcWebUiProvider(base_url="http://127.0.0.1:7899")
        config = {
            "components": [
                {"id": 6, "type": "dropdown", "props": {"label": "推理音色", "choices": ["A.pth"]}},
            ],
            "dependencies": [{"api_name": "infer_change_voice", "inputs": [6], "outputs": []}],
        }

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return config

        with patch("companion_v01.cover_song.requests.get", return_value=FakeResponse()) as request:
            status = provider.capability_status()

        self.assertTrue(status["enabled"])
        self.assertEqual(request.call_args.kwargs["timeout"], 2.0)

    def test_qq_cover_delivery_scope_can_send_voice_without_generic_file_wording(self) -> None:
        gateway = NapCatQQGateway()
        context = QQMessageContext(
            should_respond=True,
            reason="test",
            target_id=123,
            user_id=123,
            session_id="qq_pri_123",
            profile_user_id="qq_123",
            clean_message="给我翻唱这首歌",
        )
        event = {
            "type": "generated_file_ready",
            "send_to_user": True,
            "delivery_scope": "cover_song",
            "delivery_mode": "voice",
            "generated_file": {
                "generated_id": "generated::1",
                "absolute_path": "C:/tmp/cover.mp3",
                "output_title": "测试翻唱",
                "file_ext": "mp3",
                "mime_type": "audio/mpeg",
            },
        }
        with (
            patch.object(gateway, "send_voice", return_value={"ok": True}) as send_voice,
            patch.object(gateway, "send_file", return_value={"ok": True}) as send_file,
        ):
            result = gateway.send_generated_files(context, [event])

        self.assertTrue(result["ok"])
        send_voice.assert_called_once()
        send_file.assert_not_called()

    def test_cover_song_both_delivery_result_hides_local_path(self) -> None:
        gateway = NapCatQQGateway()
        context = QQMessageContext(
            should_respond=True,
            reason="test",
            target_id=123,
            user_id=123,
            session_id="qq_pri_123",
            profile_user_id="qq_123",
            clean_message="语音和文件都发我",
        )
        private_path = r"C:\private\cover.mp3"
        event = {
            "type": "generated_file_ready",
            "send_to_user": True,
            "delivery_scope": "cover_song",
            "delivery_mode": "both",
            "generated_file": {
                "generated_id": "generated::1",
                "absolute_path": private_path,
                "output_title": "测试翻唱",
                "file_ext": "mp3",
                "mime_type": "audio/mpeg",
            },
        }
        with (
            patch.object(gateway, "send_voice", return_value={"ok": True}) as send_voice,
            patch.object(gateway, "send_file", return_value={"ok": True}) as send_file,
        ):
            result = gateway.send_generated_files(context, [event])

        self.assertTrue(result["ok"])
        self.assertEqual(result["results"][0]["mode"], "both")
        self.assertNotIn(private_path, repr(result))
        send_voice.assert_called_once()
        send_file.assert_called_once()


if __name__ == "__main__":
    unittest.main()
