from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from scripts.tools.run_petdesk_mvp_smoke import PetdeskSmokeError, parse_sse_events, run_smoke


AUDIO_TOKEN = "a" * 32
AUDIO_HANDLE = f"akane/tts/{AUDIO_TOKEN}"
AUDIO_URL = f"/audio/petdesk/{AUDIO_TOKEN}"
STATIC_HANDLE = "akane_sample/static/default/normal"
STATIC_URL = "/petdesk-character-packs/akane_sample/assets/characters/default/normal.png"


class PetdeskMvpSmokeTests(unittest.TestCase):
    def test_parse_sse_events_handles_json_and_done_frames(self) -> None:
        events = parse_sse_events(
            'event: resource_manifest\ndata: {"audio":{}}\n\n'
            'event: display\ndata: {"speech":"hi"}\n\n'
            "event: done\ndata: {}\n\n"
        )

        self.assertEqual([event.event for event in events], ["resource_manifest", "display", "done"])
        self.assertEqual(events[1].payload, {"speech": "hi"})

    def test_live_smoke_passes_when_audio_chain_is_complete(self) -> None:
        with SmokeServer(audio_enabled=True) as server:
            summary = run_smoke(base_url=server.base_url, text="hi", timeout_seconds=5)

        self.assertEqual(summary["status"], "ok")
        self.assertEqual(
            summary["event_order"], ["resource_manifest", "display", "resource_manifest", "display", "done"]
        )
        self.assertEqual(summary["startup_asset_handle"], STATIC_HANDLE)
        self.assertEqual(summary["startup_image_url"], STATIC_URL)
        self.assertEqual(summary["startup_image_bytes"], len(SmokeHandler.image_bytes))
        self.assertEqual(summary["startup_image_content_type"], "image/png")
        self.assertEqual(summary["audio_handle"], AUDIO_HANDLE)
        self.assertEqual(summary["audio_url"], AUDIO_URL)
        self.assertEqual(summary["audio_bytes"], len(SmokeHandler.audio_bytes))
        self.assertEqual(summary["audio_content_type"], "audio/mpeg")

    def test_live_smoke_requires_audio_unless_disabled(self) -> None:
        with SmokeServer(audio_enabled=False) as server:
            with self.assertRaises(PetdeskSmokeError) as raised:
                run_smoke(base_url=server.base_url, text="hi", timeout_seconds=5)
            summary = run_smoke(
                base_url=server.base_url,
                text="hi",
                timeout_seconds=5,
                require_audio=False,
            )

        self.assertEqual(raised.exception.reason, "missing_audio_handle")
        self.assertFalse(summary["audio_required"])
        self.assertFalse(summary["audio_present"])

    def test_live_smoke_fails_when_startup_snapshot_asset_is_not_in_manifest(self) -> None:
        with SmokeServer(audio_enabled=True, startup_mode="missing_asset") as server:
            with self.assertRaises(PetdeskSmokeError) as raised:
                run_smoke(base_url=server.base_url, text="hi", timeout_seconds=5)

        self.assertEqual(raised.exception.reason, "startup_asset_not_in_manifest")


class SmokeServer:
    def __init__(self, *, audio_enabled: bool, startup_mode: str = "ok") -> None:
        self.audio_enabled = audio_enabled
        self.startup_mode = startup_mode
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), SmokeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self) -> SmokeServer:
        SmokeHandler.audio_enabled = self.audio_enabled
        SmokeHandler.startup_mode = self.startup_mode
        self.thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class SmokeHandler(BaseHTTPRequestHandler):
    audio_enabled = True
    startup_mode = "ok"
    audio_bytes = b"fake-mp3-bytes"
    image_bytes = b"\x89PNG\r\n\x1a\nfake-image-bytes"

    def do_GET(self) -> None:
        if self.path == "/pet/health":
            self._write_json(
                {
                    "ok": True,
                    "status": "ready",
                    "version": "akane-petdesk-bridge.test",
                    "snapshot": "/pet/snapshot",
                    "turn": "/pet/turn",
                    "resourceManifest": {
                        "endpoint": "/pet/resource-manifest",
                        "staticImageCount": 1,
                        "characterPackId": "akane_sample",
                        "prefix": "/petdesk-character-packs",
                    },
                    "characterPacks": [{"pack_id": "akane_sample"}],
                    "defaultCharacterPackId": "akane_sample",
                    "runtimeEnv": {
                        "VITE_PETDESK_RESOURCE_MANIFEST_URL": "/pet/resource-manifest",
                    },
                }
            )
            return
        if self.path == "/pet/snapshot":
            self._write_json(
                {
                    "schemaVersion": "pet.display.v1",
                    "speech": "startup hello",
                    "visual": {
                        "renderer": "static_portrait",
                        "emotion": "normal",
                        "outfit": "default",
                        "motion": "speaking",
                        "assetHandle": (
                            "akane_sample/static/default/missing"
                            if self.startup_mode == "missing_asset"
                            else STATIC_HANDLE
                        ),
                    },
                    "metadata": {},
                }
            )
            return
        if self.path == "/pet/resource-manifest":
            self._write_json(
                {
                    "schemaVersion": "pet.resource_manifest.v1",
                    "staticImages": {
                        STATIC_HANDLE: {
                            "kind": "static_image",
                            "handle": STATIC_HANDLE,
                            "url": STATIC_URL,
                            "source": "test",
                        }
                    },
                    "live2dModels": {},
                    "audio": {},
                }
            )
            return
        if self.path == STATIC_URL:
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(self.image_bytes)))
            self.end_headers()
            self.wfile.write(self.image_bytes)
            return
        if self.path == AUDIO_URL:
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(self.audio_bytes)))
            self.end_headers()
            self.wfile.write(self.audio_bytes)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if self.path != "/pet/turn":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        for frame in self._turn_frames():
            self.wfile.write(frame.encode("utf-8"))

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _turn_frames(self) -> list[str]:
        static_manifest = {"staticImages": {}, "audio": {}}
        first_display = {"schemaVersion": "pet.display.v1", "speech": "hello", "metadata": {}}
        if not self.audio_enabled:
            return [
                _sse("resource_manifest", static_manifest),
                _sse("display", first_display),
                _sse("done", {}),
            ]
        audio_manifest = {
            "staticImages": {},
            "audio": {
                AUDIO_HANDLE: {
                    "kind": "audio",
                    "handle": AUDIO_HANDLE,
                    "url": AUDIO_URL,
                    "source": "akane_tts",
                }
            },
        }
        final_display = {
            "schemaVersion": "pet.display.v1",
            "speech": "hello",
            "audio": {"tts": {"enabled": True, "audioHandle": AUDIO_HANDLE}},
            "metadata": {},
        }
        return [
            _sse("resource_manifest", static_manifest),
            _sse("display", first_display),
            _sse("resource_manifest", audio_manifest),
            _sse("display", final_display),
            _sse("done", {}),
        ]

    def _write_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


if __name__ == "__main__":
    unittest.main()
