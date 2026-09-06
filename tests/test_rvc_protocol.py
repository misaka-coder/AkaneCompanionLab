"""Real loopback HTTP protocol, not a substitute for RVC model acceptance."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest

from plugins.akane_cover_song.src.akane_cover_song import CoverSongError, RvcWebUiProvider


@contextmanager
def server(replies):
    calls = []
    waiting, release = threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            reply = replies[len(calls) - 1]
            if reply.get("wait"):
                waiting.set()
                release.wait(5)
            if reply.get("disconnect"):
                self.close_connection = True
                return
            raw = json.dumps(reply["body"]).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}", calls, waiting, release
    finally:
        release.set()
        http.shutdown()
        http.server_close()
        thread.join()


CONFIG = {"dependencies": [{"api_name": "uvr_convert"}]}


class RvcProtocolTests(unittest.TestCase):
    def test_generator_is_drained_and_separate_operations_have_unique_sessions(self):
        replies = [
            {"body": {"is_generating": True, "data": ["source->Success"]}},
            {"wait": True, "body": {"is_generating": True, "data": ["source->Success"]}},
            {"body": {"is_generating": False, "data": [{"__type__": "update"}]}},
            {"body": {"is_generating": False, "data": ["next result"]}},
        ]
        with server(replies) as (url, calls, waiting, release), ThreadPoolExecutor(max_workers=1) as pool:
            provider = RvcWebUiProvider(base_url=url)
            future = pool.submit(provider._post_predict, CONFIG, "uvr_convert", ["test-input"])
            try:
                self.assertTrue(waiting.wait(3))
                self.assertFalse(future.done(), "Intermediate generator values must not settle the call")
            finally:
                release.set()
            result = future.result(timeout=5)
            self.assertIs(result["is_generating"], False)
            self.assertEqual(result["data"], ["source->Success"])
            second = provider._post_predict(CONFIG, "uvr_convert", ["second-input"])
            self.assertEqual(second["data"], ["next result"])
            self.assertEqual(len(calls), 4)
            sessions = [call["session_hash"] for call in calls]
            self.assertEqual(len(set(sessions[:3])), 1)
            self.assertEqual(len(sessions[0]), 32)
            self.assertNotEqual(sessions[0], sessions[3])
            self.assertTrue(all(call["fn_index"] == 0 for call in calls))
            self.assertEqual([call["data"] for call in calls[:3]], [["test-input"]] * 3)

    def test_mid_generator_disconnect_and_missing_final_marker_are_not_success(self):
        for replies in (
            [{"body": {"is_generating": True, "data": ["source->Success"]}}, {"disconnect": True}],
            [{"body": {"data": ["source->Success"]}}],
        ):
            with server(replies) as (url, calls, *_):
                with self.assertRaises(CoverSongError) as raised:
                    RvcWebUiProvider(base_url=url)._post_predict(CONFIG, "uvr_convert", [])
                self.assertEqual(raised.exception.reason, "rvc_uvr_convert_request_failed")
                self.assertEqual(len(calls), len(replies), "Never retry unconfirmed work")


if __name__ == "__main__":
    unittest.main()
