"""Selection changes reach HTTP bytes, pet display, QQ delivery and realtime binding."""

import io
import json
import tempfile
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.desktop_pet_character_resources import DesktopPetCharacterResourceService
from companion_v01.local_capability_catalog import _build_voice_provider_resolutions
from companion_v01.local_capability_config import save_provider_config, save_voice_profile_config, save_capability_approval_mode
from companion_v01.routes.petdesk import build_petdesk_router
from companion_v01.routes.qq import _process_qq_turn_streaming, _synthesize_qq_voice_file
from companion_v01.routes.voice import build_voice_router
from companion_v01.tts_provider_runtime import resolve_character_tts_client
from companion_v01.tts_provider_selection import EDGE_TTS_PROVIDER_ID, GPT_SOVITS_PROVIDER_ID
from tests.test_petdesk_bridge import FakeRuntimeMetrics, _sse_payloads
from tests.test_qq_voice_delivery import FakeQQGateway


class TTSProviderSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.characters = self.root / "characters"
        self.preferences = {
            "silent": {"provider": "text_only"},
            "unknown": {"provider": "example.tts.new"},
            "invalid": {"provider": "https://private.invalid/secret"},
            "missing": {"provider": "gpt_sovits"},
            "badprofile": {"profileId": "../private"},
            "gpt": {"provider": "gpt_sovits", "profileId": "voice_one"},
            "default": {},
        }
        for pack, voice in self.preferences.items():
            directory = self.characters / pack
            directory.mkdir(parents=True)
            portrait = directory / "assets" / "characters" / "default" / "happy.png"
            portrait.parent.mkdir(parents=True)
            portrait.write_bytes(b"portrait-fixture")
            (directory / "character.json").write_text(
                json.dumps({"identity": {"id": pack}, "voice": voice}), encoding="utf-8"
            )
        self.resources = DesktopPetCharacterResourceService(characters_dir=self.characters)
        self.config = SimpleNamespace(DATA_DIR=self.root, WEB_OWNER_PROFILE_USER_ID="master")
        self.logs = []
        self.metrics = FakeRuntimeMetrics()

        class Engine:
            desktop_pet_character_resources = self.resources
            capability_config_base_dir = self.root

            def process_turn(self, payload):
                return {"speech": "保留这句文字。", "emotion": "happy"}

            def process_turn_stream(self, payload):
                yield {
                    "type": "final_ui",
                    "payload": {
                        "speech": "保留这句文字。",
                        "speech_segments": ["保留这句文字。"],
                        "reply_medium": "voice",
                        "tool_events": [],
                    },
                }

        class Edge:
            calls = []

            async def synthesize(self, text):
                self.calls.append(text)
                return b"edge-test-audio"

        self.engine, self.edge = Engine(), Edge()
        from tests.tts_plugin_harness import ThreadedTTSHarness
        self.plugins = ThreadedTTSHarness(self.root, self.engine, self.config)
        self.addCleanup(self.plugins.close)
        self.engine.plugin_capability_source = self.plugins.harness.engine.plugin_capability_source
        self.engine._get_generated_file_service = self.plugins.harness.engine._get_generated_file_service
        save_capability_approval_mode(base_dir=self.root, profile_user_id="master",
            capability_id="akane.tts.service.tts.v1.synthesize", mode="trusted_auto_allow")
        app = FastAPI()
        args = dict(
            engine=self.engine,
            config_module=self.config,
            tts_client=self.edge,
            runtime_metrics=self.metrics,
            log_event=lambda event, **fields: self.logs.append((event, fields)),
        )
        app.include_router(build_voice_router(**args))
        app.include_router(build_petdesk_router(**args))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def payload(self, pack):
        return {"text": "保留这句文字。", "real_user_id": "master", "session_id": "session", "character_pack_id": pack}

    def context(self, pack):
        return SimpleNamespace(
            profile_user_id="master", session_id="session", character_pack_id=pack, reply_mode="voice"
        )

    def test_non_speaking_selections_never_call_another_voice_across_consumers(self):
        reasons = {
            "silent": "text_only_requested",
            "unknown": "requested_provider_unknown",
            "invalid": "requested_provider_invalid",
            "missing": "requested_voice_profile_missing",
            "badprofile": "requested_voice_profile_missing",
            "gpt": "requested_provider_missing_config",
        }
        for pack, reason in reasons.items():
            with self.subTest(pack=pack):
                for _ in range(2):
                    response = self.client.post("/tts", json=self.payload(pack))
                    self.assertEqual(response.status_code, 503, response.text)
                    self.assertEqual(response.headers["x-akane-tts-reason"], reason)
                    self.assertEqual(response.headers["x-akane-tts-provider"], "")
                    self.assertEqual(response.headers["x-akane-tts-fallback"], "")
                    self.assertNotIn("private.invalid", response.text + str(response.headers))
                turn = self.client.post("/pet/turn", json={"text": "hi", "metadata": self.payload(pack)})
                self.assertEqual(turn.status_code, 200, turn.text)
                displays = _sse_payloads(turn.text, "display")
                self.assertEqual(len(displays), 1)
                self.assertEqual(displays[0]["speech"], "保留这句文字。")
                self.assertNotIn("audio", displays[0])
                self.assertIn("event: done", turn.text)
                result = _synthesize_qq_voice_file(
                    engine=self.engine,
                    config_module=self.config,
                    tts_client=self.edge,
                    text="hi",
                    context=self.context(pack),
                )
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["reason"], reason)
                self.assertNotIn("path", result)
                client = resolve_character_tts_client(
                        engine=self.engine,
                        profile_user_id="master",
                        session_id="session",
                        character_pack_id=pack,
                    )
                if pack in {"unknown", "gpt"}:
                    from companion_v01.tts_service import TTSServiceError
                    with self.assertRaises(TTSServiceError) as error:
                        self.plugins.run(client.synthesize("hi"))
                    self.assertEqual(error.exception.reason, reason)
                else:
                    self.assertIsNone(client)
        self.assertEqual(self.edge.calls, [])
        self.assertFalse((self.root / "qq_voice_cache").exists())

    def test_payload_selection_overrides_character_without_defaulting_invalid_input(self):
        for preference, reason in (
            ({"voiceProvider": "none"}, "text_only_requested"),
            ({"voiceProvider": "example.new"}, "requested_provider_unknown"),
            ({"voiceProvider": "未知"}, "requested_provider_invalid"),
            ({"voiceProfileId": "../private"}, "requested_voice_profile_missing"),
        ):
            response = self.client.post("/tts", json={**self.payload("default"), **preference})
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.headers["x-akane-tts-reason"], reason)
        self.assertEqual(self.edge.calls, [])
        response = self.client.post("/tts", json={**self.payload("silent"), "voiceProvider": "example.new"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["x-akane-tts-reason"], "requested_provider_unknown")

    def test_qq_voice_failure_delivers_text_once_and_never_a_record(self):
        gateway = FakeQQGateway()
        result = _process_qq_turn_streaming(
            engine=self.engine,
            qq_gateway=gateway,
            context=self.context("silent"),
            turn_payload={"message": "hi"},
            config_module=self.config,
            tts_client=self.edge,
        )
        self.assertTrue(result["send_result"]["ok"], result)
        self.assertEqual(result["send_result"]["delivery"]["voice_reason"], "text_only_requested")
        self.assertEqual(gateway.text_sends, [["保留这句文字。"]])
        self.assertEqual(gateway.voice_sends, [])
        self.assertEqual(self.edge.calls, [])

    def test_catalog_does_not_advertise_an_unselected_fallback(self):
        entries = [
            {"id": provider, "status": "ready", "enabled": True}
            for provider in (EDGE_TTS_PROVIDER_ID, GPT_SOVITS_PROVIDER_ID, "provider.voice.text_only")
        ]
        for pack in ("silent", "unknown", "invalid", "missing", "badprofile"):
            result = _build_voice_provider_resolutions(entries, character_voice=self.preferences[pack])[
                "voice.tts.character"
            ]
            self.assertEqual(result["activeProviderId"], "", (pack, result))
            self.assertEqual(result["fallbackProviderId"], "")
            self.assertNotIn("private.invalid", json.dumps(result))
        ready = _build_voice_provider_resolutions(entries, character_voice={"profileId": "voice_one"})[
            "voice.tts.character"
        ]
        self.assertEqual(ready["activeProviderId"], GPT_SOVITS_PROVIDER_ID)
        self.assertEqual(ready["strategy"], "selected_provider")

    def test_real_gpt_http_failure_keeps_pet_text_then_next_request_uses_same_provider(self):
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\0\0" * 1600)
        audio = output.getvalue()
        requests = []
        state = {"status": 500}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                body = audio if state["status"] == 200 else b"private-provider-error"
                self.send_response(state["status"])
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        saved = save_provider_config(
            base_dir=self.root,
            profile_user_id="master",
            provider_id=GPT_SOVITS_PROVIDER_ID,
            payload={"enabled": True, "endpoint": f"http://127.0.0.1:{server.server_port}"},
        )
        self.assertTrue(saved["ok"], saved)
        reference = self.root / "reference.wav"
        reference.write_bytes(audio)
        saved_voice = save_voice_profile_config(
            base_dir=self.root,
            profile_user_id="master",
            voice_profile_id="voice_one",
            payload={"enabled": True, "refAudioPath": str(reference), "promptText": "参考语音", "promptLang": "zh"},
        )
        self.assertTrue(saved_voice["ok"], saved_voice)
        for _ in range(2):
            response = self.client.post("/pet/turn", json={"text": "hi", "metadata": self.payload("gpt")})
            self.assertEqual(response.status_code, 200)
            displays = _sse_payloads(response.text, "display")
            self.assertEqual(len(displays), 1)
            self.assertNotIn("audio", displays[0])
            self.assertEqual(displays[0]["speech"], "保留这句文字。")
        self.assertEqual(len(requests), 2)
        self.assertEqual(self.edge.calls, [])
        self.assertNotIn("private-provider-error", json.dumps(self.logs))
        state["status"] = 200
        response = self.client.post("/pet/turn", json={"text": "hi", "metadata": self.payload("gpt")})
        display = _sse_payloads(response.text, "display")[-1]
        handle = display["audio"]["tts"]["audioHandle"]
        manifest = _sse_payloads(response.text, "resource_manifest")[-1]
        delivered = self.client.get(manifest["audio"][handle]["url"])
        self.assertEqual(delivered.content, audio)
        self.assertEqual(delivered.headers["content-type"], "audio/wav")
        self.assertEqual(len(requests), 3)
        self.assertEqual(requests[-1]["voice_profile_id"], "voice_one")
        self.assertEqual(self.edge.calls, [])
