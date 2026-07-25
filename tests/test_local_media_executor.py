from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from companion_v01.local_media_executor import (
    LocalMediaExecutorClient,
    LocalRvcExecutorProvider,
)


class _Response:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload=None,
        content: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.headers = headers or {}

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if not self.ok:
            raise RuntimeError(f"http_{self.status_code}")


class _Session:
    def __init__(self) -> None:
        self.get_responses: list[_Response] = []
        self.post_responses: list[_Response] = []
        self.posts: list[dict] = []

    def get(self, url, **kwargs):
        return self.get_responses.pop(0)

    def post(self, url, **kwargs):
        self.posts.append({"url": url, **kwargs})
        return self.post_responses.pop(0)


class LocalMediaExecutorTests(unittest.TestCase):
    def test_client_rejects_non_loopback_endpoint(self) -> None:
        with self.assertRaises(ValueError):
            LocalMediaExecutorClient(base_url="https://media.example.com")

    def test_transcribe_posts_audio_and_normalizes_segments(self) -> None:
        session = _Session()
        session.post_responses.append(
            _Response(
                payload={
                    "text": "你好 这是测试",
                    "language": "zh",
                    "duration": 2.5,
                    "segments": [
                        {"start": 0.0, "end": 1.0, "text": "你好"},
                        {"start": 1.2, "end": 2.5, "text": "这是测试"},
                    ],
                }
            )
        )
        client = LocalMediaExecutorClient(base_url="http://127.0.0.1:19879", session=session)

        result = client.transcribe_bytes(
            b"audio bytes",
            filename="voice.wav",
            content_type="audio/wav",
            language="zh",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "你好 这是测试")
        self.assertEqual(len(result["segments"]), 2)
        self.assertEqual(session.posts[0]["url"], "http://127.0.0.1:19879/v1/audio/transcriptions")
        self.assertEqual(session.posts[0]["files"]["file"][0], "voice.wav")

    def test_demucs_separation_transfers_stems_without_local_paths(self) -> None:
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            archive.writestr("vocals.wav", b"demucs vocals")
            archive.writestr("instrumental.wav", b"demucs instrumental")
        session = _Session()
        session.post_responses.append(_Response(content=archive_buffer.getvalue()))
        client = LocalMediaExecutorClient(base_url="http://127.0.0.1:19879", session=session)

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "song.flac"
            source.write_bytes(b"compressed song")
            vocals, instrumental = client.separate_audio_stems(
                source_path=source,
                model="htdemucs",
            )

        self.assertEqual(vocals, b"demucs vocals")
        self.assertEqual(instrumental, b"demucs instrumental")
        self.assertEqual(session.posts[0]["url"], "http://127.0.0.1:19879/v1/audio/separate")
        self.assertEqual(session.posts[0]["data"]["model"], "htdemucs")
        self.assertEqual(session.posts[0]["files"]["file"][0], "song.flac")

    def test_rvc_provider_transfers_stems_and_converted_audio_without_paths(self) -> None:
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            archive.writestr("vocals.wav", b"vocals")
            archive.writestr("instrumental.wav", b"instrumental")
        session = _Session()
        session.get_responses.extend(
            [
                _Response(
                    payload={
                        "models": [
                            {
                                "name": "Akie-test.pth",
                                "size": 10,
                                "mtime_ns": 20,
                                "indices": [{"name": "Akie.index", "size": 30, "mtime_ns": 40}],
                            }
                        ]
                    }
                ),
                _Response(
                    payload={
                        "models": [
                            {
                                "name": "Akie-test.pth",
                                "size": 10,
                                "mtime_ns": 20,
                                "indices": [],
                            }
                        ]
                    }
                ),
            ]
        )
        session.post_responses.extend(
            [
                _Response(content=archive_buffer.getvalue()),
                _Response(
                    content=b"converted wav",
                    headers={"X-Akane-RVC-Timings": json.dumps({"voice_synthesis": 0.4})},
                ),
            ]
        )
        client = LocalMediaExecutorClient(base_url="http://127.0.0.1:19879", session=session)
        provider = LocalRvcExecutorProvider(client=client, default_model="Akie-test.pth")
        self.assertEqual(provider.timeout_seconds, client.timeout_seconds)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.wav"
            source.write_bytes(b"source audio")
            model = provider.resolve_voice_model("auto")
            fingerprint = provider.model_fingerprint(model)
            vocals, instrumental = provider.separate_vocals(source_path=source, work_dir=root / "work")
            output = root / "converted.wav"
            result = provider.convert_voice(
                source_path=vocals,
                output_path=output,
                model_name=model,
                pitch_shift=0,
                index_rate=0.6,
                filter_radius=3,
                rms_mix_rate=0.25,
                protect=0.33,
            )

            self.assertEqual(model, "Akie-test.pth")
            self.assertEqual(fingerprint["weight"]["size"], 10)
            self.assertEqual(vocals.read_bytes(), b"vocals")
            self.assertEqual(instrumental.read_bytes(), b"instrumental")
            self.assertEqual(output.read_bytes(), b"converted wav")
            self.assertEqual(result["timings"]["voice_synthesis"], 0.4)
            self.assertNotIn(str(root), json.dumps(fingerprint))
