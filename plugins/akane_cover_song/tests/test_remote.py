"""Real loopback HTTP transport tests; response audio is an explicit fixture."""

import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from akane_cover_song import CoverSongError, ProviderCalls, RemoteRvcClient, RemoteRvcProvider


@contextmanager
def endpoint(*, wait_seconds=5):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path.startswith("/v1/rvc/models"):
                payload = {"models": server.models}
            else:
                payload = {"rvc": {"ready": True, "model_count": 1}, "separation": {"ready": True}}
            raw = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            server.requests.append((self.path, self.rfile.read(int(self.headers["Content-Length"]))))
            server.started.set()
            if not server.release.wait(wait_seconds):
                return
            if server.disconnect:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send_response(server.status)
            self.send_header("Content-Length", str(len(server.payload)))
            self.send_header(
                "X-Akane-Cover-Timings",
                json.dumps({"total": 1.2, "voice_synthesis": 0.5, "private/path": 7, "mix": float("nan")}),
            )
            self.end_headers()
            self.wfile.write(server.payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.models = [
        {"name": "Fixture.pth", "size": 7, "mtime_ns": 8, "indices": [{"name": "Fixture.index", "size": 9}]}
    ]
    server.requests, server.payload, server.disconnect, server.status = [], b"explicit-test-audio", False, 200
    server.started, server.release = threading.Event(), threading.Event()
    server.release.set()
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


def run_cover(client, source):
    return client.render_cover_song(
        source_path=source,
        model_name="Fixture.pth",
        demucs_model="htdemucs",
        output_format="wav",
        pitch_shift=2,
        vocal_gain_db=1.5,
    )


class RemoteTests(unittest.TestCase):
    def test_real_http_models_parameters_and_full_cover(self):
        with tempfile.TemporaryDirectory() as tmp, endpoint() as (server, url):
            root = Path(tmp)
            client = RemoteRvcClient(base_url=url, state_dir=root / "leases")
            provider = RemoteRvcProvider(client=client, default_model="Fixture.pth")
            self.assertTrue(provider.capability_status()["enabled"])
            self.assertEqual(provider.resolve_voice_model("auto"), "Fixture.pth")
            self.assertEqual(provider.resolve_voice_model("Fixture"), "Fixture.pth")
            self.assertEqual(provider.resolve_voice_model("Fixt"), "Fixture.pth")
            self.assertEqual(provider.model_fingerprint("Fixture.pth")["weight"]["size"], 7)
            source = root / "fixture.mp4"
            source.write_bytes(b"compressed-test-input")
            output = root / "work" / "output.wav"
            result = provider.render_full_cover(
                source_path=source,
                output_path=output,
                model_name="Fixture.pth",
                output_format="wav",
                pitch_shift=2,
                vocal_gain_db=1.5,
            )
            self.assertEqual(output.read_bytes(), server.payload)
            self.assertEqual(result["timings"], {"total": 1.2, "voice_synthesis": 0.5})
            route, body = server.requests[0]
            self.assertEqual(route, "/v1/rvc/cover")
            for value in (b"Fixture.pth", b"htdemucs", b"video/mp4", b"compressed-test-input", b"1.5"):
                self.assertIn(value, body)
            self.assertNotIn(str(root).encode(), body)
            self.assertFalse(client.lease.marker.exists())
            with self.assertRaises(FileExistsError):
                provider._output(output, b"overwrite", {})
            self.assertEqual(output.read_bytes(), server.payload)

    def test_archive_boundary_and_typed_errors_do_not_expose_server_data(self):
        with tempfile.TemporaryDirectory() as tmp, endpoint() as (server, url):
            root = Path(tmp)
            source = root / "source.wav"
            source.write_bytes(b"fixture")
            client = RemoteRvcClient(base_url=url, state_dir=root / "leases")
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr("vocals.wav", b"vocals-fixture")
                archive.writestr("instrumental.wav", b"instrumental-fixture")
            server.payload = stream.getvalue()
            self.assertEqual(
                client.separate_rvc_vocals(source_path=source, separation_model="HP5"),
                (b"vocals-fixture", b"instrumental-fixture"),
            )
            # Bound uncompressed sizes, not only HTTP body length.
            with patch("akane_cover_song.remote.MAX_AUDIO_BYTES", 4):
                with self.assertRaises(CoverSongError) as caught:
                    client.separate_rvc_vocals(source_path=source, separation_model="HP5")
                self.assertEqual(caught.exception.reason, "local_rvc_separation_response_invalid")
            self.assertFalse(client.lease.marker.exists())
            server.models[0]["indices"][0]["name"] = "F:/private/model.index"
            with self.assertRaises(CoverSongError) as caught:
                client.list_rvc_models()
            self.assertEqual(caught.exception.reason, "local_rvc_models_invalid")
            self.assertNotIn("private", str(caught.exception))

    def test_disconnect_and_http_error_preserve_cross_client_fence(self):
        for disconnect in (True, False):
            with self.subTest(disconnect=disconnect), tempfile.TemporaryDirectory() as tmp, endpoint() as (server, url):
                root = Path(tmp)
                source = root / "source.wav"
                source.write_bytes(b"fixture")
                server.disconnect, server.status = disconnect, 502
                client = RemoteRvcClient(base_url=url, state_dir=root / "leases")
                with self.assertRaises(CoverSongError) as caught:
                    run_cover(client, source)
                self.assertEqual(caught.exception.reason, "rvc_remote_completion_unconfirmed")
                replacement = RemoteRvcClient(base_url=url.replace("127.0.0.1", "localhost"), state_dir=root / "leases")
                with self.assertRaises(CoverSongError):
                    run_cover(replacement, source)
                self.assertEqual(len(server.requests), 1)
                self.assertTrue(replacement.lease.marker.exists())
                with self.assertRaises(ValueError):
                    replacement.lease.acknowledge_restart()

    def test_cancel_waits_for_actual_http_terminal_then_cleans_fence(self):
        async def exercise(root, server, url):
            calls = ProviderCalls()
            client = RemoteRvcClient(base_url=url, state_dir=root / "leases", cancelled=calls.cancelled)
            source = root / "source.wav"
            source.write_bytes(b"fixture")
            server.release.clear()
            task = asyncio.create_task(calls.call(run_cover, client, source))
            self.assertTrue(await asyncio.to_thread(server.started.wait, 3))
            task.cancel()
            await asyncio.sleep(0.03)
            task.cancel()
            await asyncio.sleep(0.03)
            self.assertFalse(task.done())
            self.assertTrue(client.lease.marker.exists())
            server.release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            self.assertFalse(client.lease.marker.exists())
            fresh = RemoteRvcClient(base_url=url, state_dir=root / "leases")
            self.assertEqual((await asyncio.to_thread(run_cover, fresh, source))[0], server.payload)

        with tempfile.TemporaryDirectory() as tmp, endpoint() as (server, url):
            asyncio.run(exercise(Path(tmp), server, url))

    def test_cancel_with_disconnect_is_failure_not_confirmed_cancel(self):
        async def exercise(root, server, url):
            calls = ProviderCalls()
            client = RemoteRvcClient(base_url=url, state_dir=root / "leases", cancelled=calls.cancelled)
            source = root / "source.wav"
            source.write_bytes(b"fixture")
            server.release.clear()
            task = asyncio.create_task(calls.call(run_cover, client, source))
            self.assertTrue(await asyncio.to_thread(server.started.wait, 3))
            task.cancel()
            await asyncio.sleep(0.03)
            server.disconnect = True
            server.release.set()
            with self.assertRaises(CoverSongError) as caught:
                await task
            self.assertEqual(caught.exception.reason, "rvc_remote_completion_unconfirmed")
            self.assertTrue(client.lease.marker.exists())

        with tempfile.TemporaryDirectory() as tmp, endpoint() as (server, url):
            asyncio.run(exercise(Path(tmp), server, url))

    def test_endpoint_and_options_reject_before_dispatch(self):
        for url in (
            "https://example.test",
            "http://user:pass@localhost:9",
            "http://localhost:9?secret=x",
            "http://localhost:9#x",
        ):
            with self.assertRaises(ValueError):
                RemoteRvcClient(base_url=url)
        with tempfile.TemporaryDirectory() as tmp, endpoint() as (server, url):
            client = RemoteRvcClient(base_url=url, state_dir=Path(tmp) / "leases")
            with self.assertRaises(CoverSongError):
                client.render_cover_song(
                    source_path=Path(tmp) / "absent.wav",
                    model_name="Fixture",
                    demucs_model="htdemucs",
                    output_format="wav",
                    protect=float("nan"),
                )
            self.assertEqual(server.requests, [])
            self.assertFalse(client.lease.marker.exists())
