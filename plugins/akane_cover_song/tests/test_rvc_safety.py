"""Actual process/HTTP safety tests; these do not pretend to run RVC models."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))
from akane_cover_song import CoverSongError, RvcWebUiProvider  # noqa: E402
from akane_cover_song.lease import EndpointLease  # noqa: E402

CONFIG = {"dependencies": [{"api_name": "uvr_convert"}]}


@contextmanager
def http_server(replies):
    calls, waiting, release = [], threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(body)
            reply = replies[len(calls) - 1]
            if reply.get("wait"):
                waiting.set()
                release.wait(10)
            if reply.get("disconnect"):
                self.close_connection = True
                return
            raw = json.dumps(reply["body"]).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls, waiting, release
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join()


def child_request(url, state_dir):
    code = """
import sys
from akane_cover_song import RvcWebUiProvider
p = RvcWebUiProvider(base_url=sys.argv[1], state_dir=sys.argv[2])
print('started', flush=True)
p._post_predict({'dependencies': [{'api_name': 'uvr_convert'}]}, 'uvr_convert', [])
print('completed', flush=True)
"""
    return subprocess.Popen(
        [sys.executable, "-B", "-c", code, url, str(state_dir)],
        env={**os.environ, "PYTHONPATH": str(SOURCE)},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


FINAL = {"body": {"is_generating": False, "data": ["Success"]}}


class RvcSafetyTests(unittest.TestCase):
    def test_cache_namespace_tracks_endpoint_root_and_separator_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = RvcWebUiProvider(base_url="http://localhost:7899", root_dir=root)
            alias = RvcWebUiProvider(base_url="http://127.0.0.1:7899", root_dir=root)
            original = provider.cache_namespace()
            self.assertEqual(original, alias.cache_namespace())
            self.assertNotEqual(
                original, RvcWebUiProvider(base_url="http://localhost:7898", root_dir=root).cache_namespace()
            )
            self.assertNotEqual(
                original, RvcWebUiProvider(base_url="http://localhost:7899", root_dir=root / "other").cache_namespace()
            )
            weights = root / "assets/uvr5_weights"
            weights.mkdir(parents=True)
            (weights / "HP5_only_main_vocal.pth").write_bytes(b"explicit-weight-stat-fixture")
            self.assertNotEqual(original, provider.cache_namespace())
            self.assertNotIn(str(root), provider.cache_namespace())

    def test_cross_process_lock_and_loopback_aliases(self):
        with tempfile.TemporaryDirectory() as directory, http_server([FINAL]) as (url, calls, *_):
            lease = EndpointLease(url, state_dir=directory)
            alias = EndpointLease(url.replace("127.0.0.1", "localhost") + "/proxy", state_dir=directory)
            self.assertEqual(lease.key, alias.key)
            with lease.hold():
                process = child_request(url, directory)
                try:
                    self.assertEqual(process.stdout.readline().strip(), "started")
                    time.sleep(0.25)
                    self.assertIsNone(process.poll())
                    self.assertEqual(calls, [])
                except BaseException:
                    process.kill()
                    process.communicate(timeout=5)
                    raise
            try:
                out, err = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, err)
                self.assertIn("completed", out)
                self.assertEqual(len(calls), 1)
                self.assertFalse(lease.marker.exists())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)

    def test_killed_worker_fences_new_calls_until_explicit_recovery(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            http_server([{**FINAL, "wait": True}]) as (url, calls, waiting, release),
        ):
            process = child_request(url, directory)
            try:
                self.assertTrue(waiting.wait(5))
                process.kill()
                process.communicate(timeout=5)
                provider = RvcWebUiProvider(base_url=url, state_dir=directory)
                self.assertTrue(provider.lease.marker.exists())
                with self.assertRaises(CoverSongError) as raised:
                    provider._post_predict(CONFIG, "uvr_convert", [])
                self.assertEqual(raised.exception.reason, "rvc_remote_completion_unconfirmed")
                self.assertEqual(len(calls), 1)
                with self.assertRaises(ValueError):
                    provider.lease.acknowledge_restart()
                self.assertTrue(provider.lease.marker.exists())
                release.set()
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
        # Recovery is tested against an isolated marker, not a running model.
        with tempfile.TemporaryDirectory() as directory:
            lease = EndpointLease("http://127.0.0.1:7899", state_dir=directory)
            with lease.hold():
                lease.begin("test-session")
            lease.acknowledge_restart(confirmed=True)
            self.assertFalse(lease.marker.exists())

    def test_cancel_drains_generator_before_releasing_fence(self):
        cancel = threading.Event()
        replies = [
            {"body": {"is_generating": True, "data": ["Success"]}},
            {**FINAL, "wait": True},
            FINAL,
        ]
        with (
            tempfile.TemporaryDirectory() as directory,
            http_server(replies) as (url, calls, waiting, release),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            provider = RvcWebUiProvider(base_url=url, state_dir=directory, cancelled=cancel.is_set)
            future = pool.submit(provider._post_predict, CONFIG, "uvr_convert", [])
            self.assertTrue(waiting.wait(3))
            cancel.set()
            self.assertFalse(future.done())
            self.assertTrue(provider.lease.marker.exists())
            release.set()
            with self.assertRaises(CoverSongError) as raised:
                future.result(timeout=5)
            self.assertEqual(raised.exception.reason, "operation_cancelled")
            self.assertFalse(provider.lease.marker.exists())
            self.assertEqual(calls[0]["session_hash"], calls[1]["session_hash"])
            RvcWebUiProvider(base_url=url, state_dir=directory)._post_predict(CONFIG, "uvr_convert", [])
            self.assertEqual(len(calls), 3)

    def test_cancel_and_disconnect_is_unconfirmed_not_cancelled(self):
        cancel = threading.Event()
        with (
            tempfile.TemporaryDirectory() as directory,
            http_server([{"wait": True, "disconnect": True}]) as (url, calls, waiting, release),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            provider = RvcWebUiProvider(base_url=url, state_dir=directory, cancelled=cancel.is_set)
            future = pool.submit(provider._post_predict, CONFIG, "uvr_convert", [])
            self.assertTrue(waiting.wait(3))
            cancel.set()
            release.set()
            with self.assertRaises(CoverSongError) as raised:
                future.result(timeout=5)
            self.assertEqual(raised.exception.reason, "rvc_uvr_convert_request_failed")
            self.assertTrue(provider.lease.marker.exists())
            self.assertEqual(len(calls), 1)

    def test_output_boundary_and_successful_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_dir = root / "TEMP"
            output_dir.mkdir()
            outside = root / "private.wav"
            outside.write_bytes(b"private")
            inside = output_dir / "converted.wav"
            inside.write_bytes(b"model-output")
            target = root / "published.wav"
            provider = RvcWebUiProvider(base_url="http://127.0.0.1:17899", root_dir=root, state_dir=root / "lease")
            for path in (outside, inside):
                with (
                    patch.object(provider, "_load_config", return_value={}),
                    patch.object(provider, "_build_api_inputs", return_value=[]),
                    patch.object(
                        provider,
                        "_post_predict",
                        side_effect=[{"data": []}, {"data": ["Success", {"name": str(path)}]}],
                    ),
                    patch.object(provider, "_extract_change_voice_index", return_value=""),
                ):
                    kwargs = dict(
                        source_path=root / "input.wav",
                        output_path=target,
                        model_name="A.pth",
                        pitch_shift=0,
                        index_rate=0.6,
                        filter_radius=3,
                        rms_mix_rate=0.25,
                        protect=0.33,
                    )
                    if path == outside:
                        with self.assertRaises(CoverSongError) as raised:
                            provider.convert_voice(**kwargs)
                        self.assertEqual(raised.exception.reason, "rvc_output_outside_directory")
                        self.assertFalse(target.exists())
                    else:
                        provider.convert_voice(**kwargs)
                        self.assertEqual(target.read_bytes(), b"model-output")

    def test_unknown_parameter_layout_and_credentialed_endpoint_rejected(self):
        provider = RvcWebUiProvider(base_url="http://127.0.0.1:7899")
        with self.assertRaises(CoverSongError) as raised:
            provider._build_api_inputs(CONFIG, "uvr_convert", {})
        self.assertEqual(raised.exception.reason, "rvc_api_schema_unsupported")
        for url in ("http://user:password@localhost:7899", "http://localhost:7899?token=secret", "https://example.com"):
            with self.assertRaises(ValueError):
                RvcWebUiProvider(base_url=url)


if __name__ == "__main__":
    unittest.main()
