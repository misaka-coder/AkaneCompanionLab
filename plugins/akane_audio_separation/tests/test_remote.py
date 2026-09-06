"""Real local HTTP transport, with explicit fixture (not ML) server outputs."""

from __future__ import annotations

import asyncio
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import wave
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from akane_audio_separation.remote import RemoteSeparation, RemoteSeparationError, unpack_stems
from akane_audio_separation.plugin import AudioSeparation, CAPABILITY_ID
from companion_v01.plugin_api import PluginResourceResult


def fixture_zip():
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x01\x00" * 8000)
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        for name in ("vocals.wav", "instrumental.wav"):
            archive.writestr(name, audio.getvalue())
    return result.getvalue()


class RemoteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "private-name.wav"
        self.source.write_bytes(b"fixture upload" * 50000)
        self.started, self.release, self.finished = threading.Event(), threading.Event(), threading.Event()
        self.release.set()
        self.requests = []
        self.uvr = False
        self.disconnect = False
        self.response_status = 200
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                body = json.dumps(
                    {"separation": {"ready": not owner.uvr, "model": "htdemucs"}, "rvc": {"ready": owner.uvr}}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                message = BytesParser(policy=default).parsebytes(
                    f"Content-Type: {self.headers['Content-Type']}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
                )
                fields = {
                    part.get_param("name", header="content-disposition"): (
                        part.get_filename(),
                        part.get_payload(decode=True),
                    )
                    for part in message.iter_parts()
                }
                owner.requests.append((self.path, fields))
                owner.started.set()
                owner.release.wait(10)
                owner.finished.set()
                if owner.disconnect:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                response = fixture_zip()
                self.send_response(owner.response_status)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = RemoteSeparation(f"http://127.0.0.1:{self.server.server_port}", timeout=10)

    async def asyncTearDown(self):
        self.release.set()
        await self.client.aclose()
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        self.thread.join()
        self.temporary.cleanup()

    async def test_demucs_and_uvr_stream_original_upload_and_unpack_two_files(self):
        for uvr in (False, True):
            self.uvr = uvr
            selected = await self.client.probe()
            outputs = await self.client.separate(
                source=self.source, output_root=self.root / str(uvr), **selected, output_format="wav"
            )
            self.assertEqual(set(outputs), {"vocals", "instrumental"})
            for path in outputs.values():
                with wave.open(str(path), "rb") as wav:
                    self.assertEqual(wav.getnframes(), 8000)
            endpoint, fields = self.requests[-1]
            self.assertEqual(endpoint, "/v1/rvc/separate" if uvr else "/v1/audio/separate")
            self.assertEqual(fields["file"], ("source.wav", self.source.read_bytes()))
            self.assertIn("separation_model" if uvr else "model", fields)
            self.assertFalse((self.root / str(uvr) / "response.zip").exists())

    async def test_cancel_waits_for_server_completion_then_acknowledges(self):
        self.release.clear()
        task = asyncio.create_task(
            self.client.separate(
                source=self.source,
                output_root=self.root / "output",
                backend="remote_demucs",
                model="htdemucs",
                output_format="wav",
            )
        )
        self.assertTrue(await asyncio.to_thread(self.started.wait, 5))
        task.cancel()
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.sleep(0.05)
        self.assertFalse(task.done())
        self.assertFalse(self.finished.is_set())
        self.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.finished.is_set())
        self.assertFalse(self.client.active)
        self.assertEqual(len(self.requests), 1)

    async def test_cancel_with_transport_loss_does_not_claim_server_cancelled(self):
        self.release.clear()
        self.disconnect = True
        task = asyncio.create_task(
            self.client.separate(
                source=self.source,
                output_root=self.root / "output",
                backend="remote_demucs",
                model="htdemucs",
                output_format="wav",
            )
        )
        self.assertTrue(await asyncio.to_thread(self.started.wait, 5))
        task.cancel()
        self.release.set()
        with self.assertRaisesRegex(RemoteSeparationError, "^remote_completion_unconfirmed$"):
            await task
        self.assertEqual(len(self.requests), 1)

    async def test_server_error_during_cancel_is_not_proof_nested_inference_stopped(self):
        self.release.clear()
        self.response_status = 500
        task = asyncio.create_task(
            self.client.separate(
                source=self.source,
                output_root=self.root / "output",
                backend="remote_uvr",
                model="HP5_only_main_vocal",
                output_format="wav",
            )
        )
        self.assertTrue(await asyncio.to_thread(self.started.wait, 5))
        task.cancel()
        self.release.set()
        with self.assertRaisesRegex(RemoteSeparationError, "^remote_completion_unconfirmed$"):
            await task

    async def test_adapter_cancel_discards_outputs_only_after_server_finishes(self):
        with wave.open(str(self.source), "wb") as source:
            source.setnchannels(1)
            source.setsampwidth(2)
            source.setframerate(8000)
            source.writeframes(b"\x01\x00" * 8000)

        async def resource(_target):
            return PluginResourceResult(True, "ready", path=self.source, name="source.wav", handle="audio_1")

        with patch.dict(
            os.environ,
            {
                "AKANE_SEPARATION_BACKEND": "remote",
                "AKANE_SEPARATION_REMOTE_URL": f"http://127.0.0.1:{self.server.server_port}",
            },
        ):
            adapter = AudioSeparation(SimpleNamespace(open=resource))
        try:
            self.assertTrue((await adapter.health()).ok)
            self.release.clear()
            task = asyncio.create_task(
                adapter.invoke(CAPABILITY_ID, {"source_id": "audio_1", "output_format": "wav"}, None)
            )
            self.assertTrue(await asyncio.to_thread(self.started.wait, 5))
            task.cancel()
            await asyncio.sleep(0.05)
            self.assertFalse(task.done())
            self.release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(list(self.root.glob("separation-*")))
            self.assertTrue(self.finished.is_set())
        finally:
            self.release.set()
            await adapter.aclose()


class ArchiveTests(unittest.TestCase):
    def test_bad_archive_cannot_escape_or_be_mistaken_for_two_stems(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for names in (("../vocals.wav", "instrumental.wav"), ("vocals.wav",), ("vocals.wav", "vocals.wav")):
                archive = root / "bad.zip"
                with zipfile.ZipFile(archive, "w") as target:
                    for name in names:
                        target.writestr(name, b"bad")
                with self.assertRaisesRegex(RemoteSeparationError, "separation_archive_invalid"):
                    unpack_stems(archive, output_root=root, output_format="wav")
            with zipfile.ZipFile(root / "symlink.zip", "w") as target:
                link = zipfile.ZipInfo("vocals.wav")
                link.create_system = 3
                link.external_attr = (stat.S_IFLNK | 0o777) << 16
                target.writestr(link, "../elsewhere")
                target.writestr("instrumental.wav", b"bad")
            with self.assertRaises(RemoteSeparationError):
                unpack_stems(root / "symlink.zip", output_root=root, output_format="wav")
            self.assertFalse((root / "vocals.wav").exists())

    def test_endpoint_rejects_credentials_remote_hosts_and_redirect_shapes(self):
        for url in (
            "https://127.0.0.1",
            "http://example.com",
            "http://user:pass@localhost",
            "http://localhost?x=1",
            "http://localhost/../escape",
        ):
            with self.assertRaises(RemoteSeparationError):
                RemoteSeparation(url)
        self.assertEqual(RemoteSeparation("http://localhost", uvr_model="中文 UVR (人声)").uvr_model, "中文 UVR (人声)")
        for model in ("bad\r\nfield", "../model", "", "bad\0name"):
            with self.assertRaises(RemoteSeparationError):
                RemoteSeparation("http://localhost", uvr_model=model)


if __name__ == "__main__":
    unittest.main()
