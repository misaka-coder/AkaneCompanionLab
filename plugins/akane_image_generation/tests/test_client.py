from __future__ import annotations

import asyncio
import base64
from collections import deque
from contextlib import contextmanager
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import sys
import threading
import time
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from akane_image_generation import ImageClient, ImageError, inspect_image
from akane_image_generation.types import runtime_health


def picture(color="blue", fmt="PNG", *, alpha=False, size=(8, 8)):
    stream = io.BytesIO()
    Image.new("RGBA" if alpha else "RGB", size, color).save(stream, format=fmt)
    return stream.getvalue()


def encoded(data):
    return base64.b64encode(data).decode("ascii")


def final(data, index=0):
    return {"type": "image_generation.completed", "output_index": index, "b64_json": encoded(data)}


def events(*payloads):
    return b"".join(b"data: " + json.dumps(item).encode() + b"\n\n" for item in payloads)


@contextmanager
def server(*replies):
    queue = deque(replies)
    calls = []
    waiting = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            calls.append((self.path, self.headers, body))
            if not queue:
                self.send_error(500)
                return
            reply = queue.popleft()
            if reply.get("block"):
                waiting.set()
                release.wait(reply.get("wait_seconds", 5))
            if reply.get("disconnect"):
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                finished.set()
                return
            body = reply.get("body", {"data": [{"b64_json": encoded(picture())}]})
            body = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(reply.get("status", 200))
            self.send_header("Content-Type", reply.get("mime", "application/json"))
            if not reply.get("keep_open"):
                self.send_header("Content-Length", str(len(body)))
            if reply.get("status") == 307:
                self.send_header("Location", "/must-not-follow")
            self.end_headers()
            try:
                self.wfile.write(body)
                self.wfile.flush()
                if reply.get("keep_open"):
                    waiting.set()
                    release.wait(reply.get("wait_seconds", 5))
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                finished.set()

        def do_DELETE(self):
            calls.append((self.path, self.headers, b"DELETE"))
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}/v1", calls, waiting, release, finished
    finally:
        release.set()
        http.shutdown()
        http.server_close()
        thread.join(5)


def client(url, **kwargs):
    return ImageClient(base_url=url, api_key="fixture-key", allow_loopback_http=True, **kwargs)


class ImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_actual_formats_options_and_multiple_outputs(self):
        for fmt in ("PNG", "JPEG", "WEBP"):
            with (
                self.subTest(fmt=fmt),
                server(
                    {
                        "body": {
                            "data": [
                                {"b64_json": encoded(picture(fmt=fmt))},
                                {"b64_json": encoded(picture("red", fmt))},
                            ]
                        }
                    }
                ) as (url, calls, *_),
            ):
                runtime = client(url)
                result = await runtime.generate(
                    prompt="two mugs",
                    n=2,
                    output_format=fmt.lower(),
                    compression=0,
                    quality="high",
                    size="512×1024",
                    background="opaque",
                    input_fidelity="high",
                )
                self.assertEqual(len(result.images), 2)
                self.assertEqual(result.images[0].output_format, fmt.lower())
                self.assertIn("provider_output_dimensions_changed", result.notices)
                self.assertNotEqual(result.images[0].data, result.images[1].data)
                fields = json.loads(calls[0][2])
                self.assertEqual(fields["size"], "512x1024")
                self.assertTrue(fields["stream"])
                self.assertEqual(fields["input_fidelity"], "high")
                if fmt != "PNG":
                    self.assertEqual(fields["output_compression"], 0)
                self.assertEqual(calls[0][1]["Authorization"], "Bearer fixture-key")
                await runtime.aclose()

    async def test_final_sse_ignores_partial_and_finishes_before_keepalive(self):
        partial = {"type": "image_generation.partial_image", "output_index": 0, "b64_json": encoded(picture("red"))}
        body = events(partial, final(picture("green"), 1), final(picture("blue"), 0))
        with server({"body": body, "mime": "text/event-stream", "keep_open": True}) as (url, _, _, release, finished):
            start = time.monotonic()
            result = await asyncio.wait_for(client(url).generate(prompt="mugs", n=2), 2)
            self.assertLess(time.monotonic() - start, 2)
            self.assertFalse(finished.is_set())
            self.assertEqual([image.data for image in result.images], [picture("blue"), picture("green")])
            release.set()

    async def test_partial_eof_never_becomes_final_and_honest_partial_result(self):
        partial = {"type": "image_generation.partial_image", "b64_json": encoded(picture())}
        with server({"body": events(partial), "mime": "text/event-stream"}) as (url, *_):
            with self.assertRaisesRegex(ImageError, "provider_stream_incomplete"):
                await client(url).generate(prompt="x")
        with server({"body": events(final(picture())), "mime": "text/event-stream"}) as (url, *_):
            result = await client(url).generate(prompt="x", n=2)
            self.assertEqual(len(result.images), 1)
            self.assertEqual(result.notices, ("provider_returned_fewer_images",))

    async def test_actual_reference_and_mask_multipart_validation(self):
        ref = inspect_image(picture(fmt="WEBP"))
        mask = inspect_image(picture(alpha=True))
        with server({}) as (url, calls, *_):
            await client(url).generate(prompt="edit", references=(ref,), mask=mask)
            body = calls[0][2]
            self.assertIn(b'name="image"', body)
            self.assertIn(b'name="mask"', body)
            self.assertIn(ref.data, body)
            self.assertIn(mask.data, body)
            self.assertIn(b"Content-Type: image/webp", body)
        with server() as (url, calls, *_):
            for supplied, code in [
                (inspect_image(picture()), "mask_requires_png_alpha"),
                (inspect_image(picture(alpha=True, size=(9, 8))), "mask_dimensions_mismatch"),
            ]:
                with self.assertRaisesRegex(ImageError, code):
                    await client(url).generate(prompt="edit", references=(ref,), mask=supplied)
            self.assertFalse(calls)

    async def test_multi_reference_upload_fallback_and_cleanup(self):
        ref = inspect_image(picture())
        mask = inspect_image(picture(alpha=True))
        with server(
            {"status": 400}, {"body": {"id": "file-a"}}, {"body": {"id": "file-b"}}, {"body": {"id": "file-mask"}}, {}
        ) as (url, calls, *_):
            result = await client(url).generate(prompt="edit", references=(ref, ref), mask=mask)
            self.assertEqual(len(result.images), 1)
            self.assertIn(b'name="image[]"', calls[0][2])
            payload = json.loads(calls[4][2])
            self.assertEqual(payload["images"], [{"file_id": "file-a"}, {"file_id": "file-b"}])
            self.assertEqual(payload["mask"], {"file_id": "file-mask"})
            self.assertEqual(
                [call[0] for call in calls[5:]], ["/v1/files/file-a", "/v1/files/file-b", "/v1/files/file-mask"]
            )
        with server({"status": 400}, {"body": {"id": "file-a"}}, {"body": {"id": "../../user-file"}}) as (
            url,
            calls,
            *_,
        ):
            with self.assertRaisesRegex(ImageError, "provider_file_upload_invalid_id"):
                await client(url).generate(prompt="edit", references=(ref, ref))
            self.assertEqual(calls[-1][0], "/v1/files/file-a")
            self.assertEqual(len(calls), 4)

    async def test_auth_redirect_and_ambiguous_failure_never_retried(self):
        for response, code in [
            ({"status": 401, "body": {"message": "fixture-key"}}, "provider_auth_or_network_forbidden"),
            ({"status": 307}, "provider_redirect_rejected"),
            ({"status": 502}, "provider_unavailable"),
            ({"disconnect": True}, "remote_completion_unconfirmed"),
        ]:
            with self.subTest(code=code), server(response) as (url, calls, *_):
                with self.assertRaisesRegex(ImageError, code) as raised:
                    await client(url).generate(prompt="x")
                self.assertNotIn("fixture-key", str(raised.exception))
                self.assertEqual(len(calls), 1)

    async def test_explicit_non_execution_rejection_can_retry(self):
        with server({"status": 503, "body": {"error": {"message": "No available compatible accounts"}}}, {}) as (
            url,
            calls,
            *_,
        ):
            result = await client(url, transient_retry_count=1).generate(prompt="x")
            self.assertEqual(len(result.images), 1)
            self.assertEqual(len(calls), 2)

    async def test_rejection_diagnostics_are_allowlisted_not_raw_provider_text(self):
        for supplied, expected in (("n", "n"), ("fixture-key", ""), ("https://private.example/?key=secret", "")):
            with server({"status": 400, "body": {"error": {"message": "private secret text", "param": supplied}}}) as (
                url,
                *_,
            ):
                with self.assertRaises(ImageError) as raised:
                    await client(url).generate(prompt="x")
                self.assertEqual(raised.exception.http_status, 400)
                self.assertEqual(raised.exception.rejected_parameter, expected)
                self.assertEqual(str(raised.exception), "provider_rejected_request")

    async def test_cancel_with_only_some_final_images_is_unconfirmed(self):
        with server({"block": True, "body": events(final(picture())), "mime": "text/event-stream"}) as (
            url,
            _,
            waiting,
            release,
            _,
        ):
            runtime = client(url)
            task = asyncio.create_task(runtime.generate(prompt="x", n=2))
            self.assertTrue(await asyncio.to_thread(waiting.wait, 2))
            task.cancel()
            release.set()
            with self.assertRaisesRegex(ImageError, "remote_completion_unconfirmed"):
                await task

    async def test_cancel_waits_for_remote_terminal_and_close_drains(self):
        with server({"block": True}) as (url, calls, waiting, release, finished):
            runtime = client(url)
            invocation = asyncio.create_task(runtime.generate(prompt="x"))
            self.assertTrue(await asyncio.to_thread(waiting.wait, 2))
            invocation.cancel()
            await asyncio.sleep(0.03)
            invocation.cancel()
            await asyncio.sleep(0.03)
            self.assertFalse(invocation.done())
            closing = asyncio.create_task(runtime.aclose())
            await asyncio.sleep(0.03)
            self.assertFalse(closing.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await invocation
            await closing
            self.assertTrue(finished.is_set())
            self.assertFalse(runtime._active)
            self.assertEqual(len(calls), 1)
            with self.assertRaisesRegex(ImageError, "image_client_closed"):
                await runtime.generate(prompt="x")

    async def test_cancel_disconnection_and_rejection_keep_actual_failure(self):
        for response, code in [
            ({"block": True, "disconnect": True}, "remote_completion_unconfirmed"),
            (
                {
                    "block": True,
                    "body": events({"type": "image_generation.partial_image", "b64_json": encoded(picture())}),
                    "mime": "text/event-stream",
                },
                "remote_completion_unconfirmed",
            ),
            ({"block": True, "status": 401}, "provider_auth_or_network_forbidden"),
        ]:
            with self.subTest(code=code), server(response) as (url, _, waiting, release, _):
                runtime = client(url)
                task = asyncio.create_task(runtime.generate(prompt="x"))
                self.assertTrue(await asyncio.to_thread(waiting.wait, 2))
                task.cancel()
                release.set()
                with self.assertRaisesRegex(ImageError, code):
                    await task
                self.assertFalse(runtime._active)

    async def test_invalid_and_bounded_payloads_are_not_images(self):
        cases = [
            ({"body": {"data": [{"b64_json": encoded(b"\x89PNG\r\n\x1a\nnot-a-real-image")}]}}, "invalid_image_data"),
            ({"body": {"data": [{"b64_json": "*bad*"}]}}, "provider_returned_invalid_base64"),
            ({"body": b'{"value":NaN}'}, "provider_invalid_json"),
            ({"body": b"{" * 10000}, "provider_response_size_limit"),
            ({"body": {"error": {"message": "hidden"}}}, "provider_generation_failed"),
            ({"body": events(final(picture(), 7)), "mime": "text/event-stream"}, "provider_invalid_image_index"),
            ({"body": b"data: " + b"x" * 50000, "mime": "text/event-stream"}, "provider_stream_size_limit"),
        ]
        for response, code in cases:
            with self.subTest(code=code), server(response) as (url, *_):
                with self.assertRaisesRegex(ImageError, code):
                    await client(url, max_output_bytes=1024).generate(prompt="x")

    async def test_validation_no_network_and_codec_health(self):
        self.assertEqual(runtime_health(), {"status": "runtime_ready", "reason": "provider_checked_on_invocation"})
        for url in (
            "http://external.example/v1",
            "https://user:password@host/v1",
            "https://host/v1?key=x",
            "https://host/v1#x",
        ):
            with self.assertRaisesRegex(ImageError, "invalid_provider_base_url"):
                ImageClient(base_url=url, api_key="x")
        with server() as (url, calls, *_):
            for kwargs in (
                {"n": 5},
                {"n": True},
                {"size": "4096x512"},
                {"compression": -1},
                {"quality": "fake"},
                {"prompt": ""},
            ):
                with self.assertRaises(ImageError):
                    await client(url).generate(**{"prompt": "x", **kwargs})
            self.assertFalse(calls)


if __name__ == "__main__":
    unittest.main()
