"""Real loopback transport; explicit response fixtures, not claimed ASR."""

from __future__ import annotations

import asyncio
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from akane_file_transcription.local import Options
from akane_file_transcription.remote import RemoteTranscriber, RemoteTranscriptionError


class RemoteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "private-name.wav"
        self.source.write_bytes(b"explicit fixture bytes" * 100000)
        self.started, self.release, self.finished = threading.Event(), threading.Event(), threading.Event()
        self.release.set()
        self.requests, self.disconnect, self.status = [], False, 200
        self.payload = {
            "text": "fixture transcript",
            "language": "en",
            "model": "tiny",
            "duration": 2,
            "segments": [{"start": 0.1, "end": 1.9, "text": "fixture transcript"}],
        }
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"asr":{"ready":true,"model":"tiny"}}')

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                parts = BytesParser(policy=default).parsebytes(
                    f"Content-Type: {self.headers['Content-Type']}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
                )
                fields = {
                    part.get_param("name", header="content-disposition"): (
                        part.get_filename(),
                        part.get_payload(decode=True),
                    )
                    for part in parts.iter_parts()
                }
                owner.requests.append((self.path, fields))
                owner.started.set()
                owner.release.wait(10)
                owner.finished.set()
                if owner.disconnect:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                raw = json.dumps(owner.payload).encode()
                self.send_response(owner.status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = RemoteTranscriber(f"http://127.0.0.1:{self.server.server_port}", timeout=10)

    async def asyncTearDown(self):
        self.release.set()
        await self.client.aclose()
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    async def test_original_stream_and_all_protocol_options(self):
        self.assertTrue((await self.client.probe())["ok"])
        result = await self.client.transcribe(
            source=self.source, options=Options(model_size="tiny", language="auto", vad_filter=False)
        )
        self.assertEqual(result["text"], "fixture transcript")
        endpoint, fields = self.requests[0]
        self.assertEqual(endpoint, "/v1/audio/transcriptions")
        self.assertEqual(fields["file"], ("source.wav", self.source.read_bytes()))
        self.assertEqual(fields["language"][1], b"")
        self.assertEqual(fields["vad_filter"][1], b"false")
        self.assertEqual(fields["model"][1], b"tiny")
        self.assertNotIn(str(self.root), str(result))

    async def test_repeated_cancel_waits_for_actual_completed_response(self):
        self.release.clear()
        task = asyncio.create_task(self.client.transcribe(source=self.source))
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

    async def test_transport_loss_during_cancel_never_claims_stopped(self):
        self.release.clear()
        self.disconnect = True
        task = asyncio.create_task(self.client.transcribe(source=self.source))
        self.assertTrue(await asyncio.to_thread(self.started.wait, 5))
        task.cancel()
        self.release.set()
        with self.assertRaisesRegex(RemoteTranscriptionError, "^remote_completion_unconfirmed$"):
            await task
        self.assertEqual(len(self.requests), 1)

    async def test_failed_service_and_invalid_timestamps_are_not_success(self):
        self.status = 500
        with self.assertRaisesRegex(RemoteTranscriptionError, "^asr_remote_failed$"):
            await self.client.transcribe(source=self.source)
        self.status = 200
        self.payload["segments"][0]["start"] = -1
        with self.assertRaisesRegex(RemoteTranscriptionError, "^asr_remote_response_invalid$"):
            await self.client.transcribe(source=self.source)
        self.payload["segments"] = []
        with self.assertRaisesRegex(RemoteTranscriptionError, "^asr_no_speech$"):
            await self.client.transcribe(source=self.source)

    async def test_unsupported_remote_controls_fail_before_upload(self):
        with self.assertRaisesRegex(RemoteTranscriptionError, "^asr_remote_option_unsupported$"):
            await self.client.transcribe(source=self.source, options=Options(device="cuda"))
        self.assertEqual(self.requests, [])
        for url in ("https://127.0.0.1", "http://example.com", "http://u:p@localhost", "http://localhost/../x"):
            with self.assertRaisesRegex(RemoteTranscriptionError, "^asr_endpoint_invalid$"):
                RemoteTranscriber(url)


if __name__ == "__main__":
    unittest.main()
