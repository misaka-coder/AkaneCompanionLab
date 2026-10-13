"""Exercise provider selection, the real plugin worker and the serial API lane."""
import asyncio
import io
import json
import tempfile
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.desktop_pet_character_resources import DesktopPetCharacterResourceService
from companion_v01.local_capability_config import (
    save_provider_config, save_voice_profile_config, get_voice_profile_runtime_config,
    save_capability_approval_mode, list_voice_profile_configs,
)
from companion_v01.routes.voice import build_voice_router
from companion_v01.speech_policy import character_speech_policy
from companion_v01.speech_policy import speech_prompt_profile, irodori_caption, IRODORI_CAPTION_MAX_CHARS
from companion_v01.speech_units import project_speech_units, voice_segments, continuous_voice_chunks
from companion_v01.prompt_profiles import PromptProfileRegistry
from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.tts_provider_runtime import resolve_tts_runtime_provider, synthesize_speech_units
from companion_v01.tts_provider_selection import IRODORI_PROVIDER_ID, GPT_SOVITS_PROVIDER_ID, select_tts_provider
from scripts.irodori_server import SerialSpeechApp
from tests.test_petdesk_bridge import FakeRuntimeMetrics
from tests.tts_plugin_harness import ThreadedTTSHarness


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x10\x00" * 480)
    return output.getvalue()


def make_character(root):
    path = root / "characters" / "cecilia" / "character.json"
    path.parent.mkdir(parents=True)
    data = {"identity": {"id": "cecilia"}, "voice": {"provider": "irodori", "profile_id": "iro_calm"},
            "speech_output": {"enabled": True, "language": "日语", "default_style": "calm",
                "styles": {"calm": {"profile_id": "gpt_calm"}, "happy": {"profile_id": "gpt_happy"},
                           "shy": {"profile_id": "gpt_shy"}, "earnest": {"profile_id": "gpt_earnest"}}}}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path, data


class IrodoriConfigTests(unittest.TestCase):
    def test_continuous_delivery_bounds_long_text_without_losing_the_tail(self):
        short = "少し疲れているみたいですね。無理をしないで、ゆっくり休んでください。"
        self.assertEqual(continuous_voice_chunks(short), [short])
        self.assertEqual(continuous_voice_chunks(""), [])
        for text in (short * 12, "「" + "落ち着いて話しましょう。" * 25 + "」", "あ" * 365):
            chunks = continuous_voice_chunks(text)
            self.assertTrue(all(0 < len(chunk) <= 120 for chunk in chunks))
            self.assertEqual("".join(chunks), text)

    def test_provider_styles_follow_selection_and_preserve_gpt_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, data = make_character(root)
            engine = SimpleNamespace(desktop_pet_character_resources=DesktopPetCharacterResourceService(
                characters_dir=root / "characters"))
            self.assertEqual(character_speech_policy(engine, "cecilia"), {"language": "日语", "style_mode": "description"})
            context = ClientProtocolContext(effective_mode=ClientMode.DESKTOP_PET, capabilities=("speech_units_v1",))
            prompt = speech_prompt_profile(PromptProfileRegistry().resolve(context), character_speech_policy(engine, "cecilia"))
            self.assertIn("随语境自由表达", prompt.fast_mode_prompt)
            self.assertIn("style 用日语一句简短自然语言", prompt.fast_mode_prompt)
            self.assertIn("通常 10～40 字，最多 80 字", prompt.fast_mode_prompt)
            self.assertIn("描述本组 say 的情绪、语气和节奏", prompt.fast_mode_prompt)
            self.assertNotIn("[hidden]", prompt.fast_mode_prompt)
            self.assertNotIn("历史回复（显示记录）", prompt.fast_mode_prompt)
            self.assertNotIn("[archived]", prompt.fast_mode_prompt)
            for preset in ("calm", "happy", "shy", "earnest", "gpt_calm", "iro_calm"):
                self.assertNotIn(preset, prompt.fast_mode_prompt)
            expressive = {"utterances": [{"style": "うれしくて少し照れながら、自然に話す。", "text": "太好啦！", "say": "やった！"}]}
            projected = project_speech_units(expressive)
            self.assertEqual(projected["utterances"], expressive["utterances"])
            segment = voice_segments(projected["utterances"])[0]
            resolution = resolve_tts_runtime_provider(engine=engine, payload={"character_pack_id": "cecilia",
                "speech_protocol": "speech_units_v1", "voice_style": segment["style"]})
            self.assertEqual(resolution["voiceStyleRequested"], expressive["utterances"][0]["style"])
            self.assertEqual(resolution["voiceStyle"], expressive["utterances"][0]["style"])
            self.assertFalse(resolution["voiceStyleFallback"])
            self.assertEqual(projected["utterances"], expressive["utterances"], "Voice fallback must not rewrite saved expression")
            from companion_v01.assistant_history_projection import project_assistant_history_content, ARCHIVED_STYLE
            reading_record = project_assistant_history_content(json.dumps(expressive, ensure_ascii=False))
            self.assertIn("过往发言", reading_record)
            self.assertNotIn(expressive["utterances"][0]["style"], reading_record)
            history = json.loads(project_assistant_history_content(json.dumps(expressive, ensure_ascii=False), history_format="json"))
            self.assertEqual(history["utterances"][0]["style"], ARCHIVED_STYLE)
            self.assertEqual(projected["utterances"], expressive["utterances"], "History archiving must preserve actual delivery intent")
            self.assertEqual(history["utterances"][0]["text"], "太好啦！")
            from companion_v01.llm_runtime import _TopLevelJSONStreamTap
            tap = _TopLevelJSONStreamTap()
            events = tap.feed(json.dumps(expressive, ensure_ascii=False))
            self.assertEqual([event["style"] for event in events if event["type"] == "voice_segment"],
                             [expressive["utterances"][0]["style"]])
            self.assertNotIn("styles", character_speech_policy(engine, "cecilia", provider_id=IRODORI_PROVIDER_ID))
            request = {"character_pack_id": "cecilia", "speech_protocol": "speech_units_v1", "voice_style": "happy"}
            result = resolve_tts_runtime_provider(engine=engine, payload=request)
            self.assertEqual((result["activeProviderId"], result["voiceProfileId"]), (IRODORI_PROVIDER_ID, "iro_calm"))
            self.assertTrue(result["voiceStyleFallback"])
            self.assertEqual(result["voiceStyleRequested"], "happy")
            self.assertEqual(request["voice_style"], "happy", "Execution must not rewrite expression intent")
            selected = resolve_tts_runtime_provider(engine=engine, payload={**request,
                "voiceProvider": "irodori", "voiceProfileId": "another_registered_speaker"})
            self.assertEqual(selected["voiceProfileId"], "another_registered_speaker")
            self.assertEqual(selected["voiceStyle"], "", "An untested voice style must not be claimed as applied")
            gpt = resolve_tts_runtime_provider(engine=engine, payload={**request, "voiceProvider": "gpt_sovits"})
            self.assertEqual((gpt["activeProviderId"], gpt["voiceProfileId"]), (GPT_SOVITS_PROVIDER_ID, "gpt_happy"))
            bad = resolve_tts_runtime_provider(engine=engine, payload={**request, "voiceProvider": "unknown"})
            self.assertEqual(bad["status"], "unavailable")
            data["voice"] = {"provider": "gpt_sovits", "profile_id": "gpt_calm"}
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(set(character_speech_policy(engine, "cecilia")["styles"]), {"calm", "happy", "shy", "earnest"})

    def test_caption_bounds_do_not_rewrite_intent_or_choose_another_speaker(self):
        self.assertEqual(irodori_caption(""), ("", ""))
        self.assertEqual(irodori_caption("calm"), ("", "legacy_style_id"))
        for marker in ("[archived]", "[hidden]"):
            self.assertEqual(irodori_caption(marker), ("", "caption_archived"))
        self.assertEqual(irodori_caption("声\x01"), ("", "caption_invalid"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_character(root)
            engine = SimpleNamespace(desktop_pet_character_resources=DesktopPetCharacterResourceService(characters_dir=root / "characters"))
            for style in ("あ" * IRODORI_CAPTION_MAX_CHARS, "あ" * (IRODORI_CAPTION_MAX_CHARS + 1)):
                payload = {"character_pack_id": "cecilia", "speech_protocol": "speech_units_v1", "voice_style": style}
                result = resolve_tts_runtime_provider(engine=engine, payload=payload)
                self.assertEqual(result["voiceStyleRequested"], style)
                self.assertEqual(payload["voice_style"], style)
                self.assertEqual(result["voiceProfileId"], "iro_calm")
                self.assertEqual(result["voiceStyleFallback"], len(style) > IRODORI_CAPTION_MAX_CHARS)
                if result["voiceStyleFallback"]:
                    self.assertEqual(result["voiceStyle"], "")
                    self.assertEqual(result["voiceStyleFallbackReason"], "caption_too_long")

    def test_voice_ids_round_trip_without_reference_paths_or_captions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            saved = save_voice_profile_config(base_dir=root, profile_user_id="master", voice_profile_id="iro_calm",
                payload={"providerId": IRODORI_PROVIDER_ID, "irodoriVoiceId": "cecilia", "caption": "ignored",
                         "refAudioPath": "private/reference.wav", "enabled": True})
            self.assertTrue(saved["ok"])
            profile = get_voice_profile_runtime_config(base_dir=root, profile_user_id="master", voice_profile_id="iro_calm")
            self.assertEqual(profile["irodoriVoiceId"], "cecilia")
            self.assertNotIn("caption", profile)
            self.assertNotIn("refAudioPath", profile)
            public = list_voice_profile_configs(base_dir=root, profile_user_id="master")["voiceProfiles"][0]
            self.assertTrue(public["configured"])
            for invalid in ("../cecilia", "none", "http://private", "cecilia.speaker.safetensors"):
                result = save_voice_profile_config(base_dir=root, profile_user_id="master", voice_profile_id="bad",
                    payload={"providerId": IRODORI_PROVIDER_ID, "irodoriVoiceId": invalid})
                self.assertFalse(result["ok"], invalid)
            self.assertEqual(select_tts_provider({"provider": "irodori"}).reason, "requested_voice_profile_missing")


class IrodoriWorkerTests(unittest.TestCase):
    def test_http_bytes_prewarm_style_fallback_and_qq_concatenation(self):
        calls, state = [], {"fail": False}
        audio = wav_bytes()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_POST(self):
                calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(503 if state["fail"] else 200)
                self.send_header("Content-Type", "application/json" if state["fail"] else "audio/wav")
                self.end_headers()
                self.wfile.write(b'{"error":"private/server/path"}' if state["fail"] else audio)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
                root = Path(directory)
                make_character(root)
                config = SimpleNamespace(DATA_DIR=root, WEB_OWNER_PROFILE_USER_ID="master")
                source = SimpleNamespace(capability_config_base_dir=root, settings=None)
                source.desktop_pet_character_resources = DesktopPetCharacterResourceService(characters_dir=root / "characters")
                save_provider_config(base_dir=root, profile_user_id="master", provider_id=IRODORI_PROVIDER_ID,
                    payload={"enabled": True, "endpoint": f"http://127.0.0.1:{server.server_port}"})
                save_voice_profile_config(base_dir=root, profile_user_id="master", voice_profile_id="iro_calm",
                    payload={"providerId": IRODORI_PROVIDER_ID, "enabled": True, "irodoriVoiceId": "cecilia"})
                save_capability_approval_mode(base_dir=root, profile_user_id="master",
                    capability_id="akane.tts.service.tts.v1.synthesize", mode="trusted_auto_allow")
                plugins = ThreadedTTSHarness(root, source, config)
                try:
                    engine = plugins.harness.engine
                    engine.desktop_pet_character_resources = source.desktop_pet_character_resources
                    app = FastAPI()
                    app.include_router(build_voice_router(engine=engine, config_module=config, tts_client=None,
                        runtime_metrics=FakeRuntimeMetrics(), log_event=lambda *_args, **_kwargs: None))
                    payload = {"text": "おかえり。", "real_user_id": "master", "session_id": "session",
                               "character_pack_id": "cecilia", "speech_protocol": "speech_units_v1", "voice_style": "shy"}
                    with TestClient(app) as client:
                        reply = client.post("/tts", json=payload)
                        self.assertEqual(reply.status_code, 200, reply.text)
                        self.assertEqual(reply.content, audio)
                        self.assertEqual(reply.headers["x-akane-tts-provider"], IRODORI_PROVIDER_ID)
                        self.assertEqual(reply.headers["x-akane-voice-style"], "")
                        self.assertEqual(reply.headers["x-akane-voice-style-fallback"], "true")
                        self.assertEqual(reply.headers["x-akane-voice-style-fallback-reason"], "legacy_style_id")
                        style = "親しい相手に、力を抜いて自然に優しく話す。"
                        styled = client.post("/tts", json={**payload, "voice_style": style})
                        self.assertEqual(styled.status_code, 200, styled.text)
                        self.assertEqual(unquote(styled.headers["x-akane-voice-style"]), style)
                        self.assertEqual(unquote(styled.headers["x-akane-voice-style-requested"]), style)
                        self.assertEqual(styled.headers["x-akane-voice-style-fallback"], "false")
                        self.assertEqual(calls[-1]["irodori"]["caption"], style)
                        self.assertEqual(calls[-1]["irodori"]["cfg_scale_caption"], 2.0)
                        overlong = client.post("/tts", json={**payload, "voice_style": "あ" * 81})
                        self.assertEqual(overlong.status_code, 200, overlong.text)
                        self.assertEqual(overlong.headers["x-akane-voice-style-fallback-reason"], "caption_too_long")
                        self.assertNotIn("caption", calls[-1]["irodori"])
                        missing = client.post("/tts", json={**payload, "voice_style": ""})
                        self.assertEqual(missing.status_code, 200, missing.text)
                        self.assertNotIn("caption", calls[-1]["irodori"])
                        for marker in ("[archived]", "[hidden]"):
                            archived = client.post("/tts", json={**payload, "voice_style": marker})
                            self.assertEqual(archived.status_code, 200, archived.text)
                            self.assertEqual(unquote(archived.headers["x-akane-voice-style-requested"]), marker)
                            self.assertEqual(archived.headers["x-akane-voice-style-fallback"], "true")
                            self.assertEqual(archived.headers["x-akane-voice-style-fallback-reason"], "caption_archived")
                            self.assertNotIn("caption", calls[-1]["irodori"])
                        warm = client.post("/tts", json={**payload, "text": "嗯。", "prewarm": True})
                        self.assertEqual(warm.status_code, 200, warm.text)
                        self.assertEqual(calls[-1]["input"], "おかえり。")
                        state["fail"] = True
                        failed = client.post("/tts", json=payload)
                        self.assertEqual(failed.status_code, 503)
                        self.assertIn("tts_irodori_not_ready", failed.text)
                        self.assertNotIn("private/server/path", failed.text)
                        state["fail"] = False
                    units = [{"text": "回来啦。今天辛苦了。", "say": "おかえり。お疲れさま。", "style": "自然に優しくねぎらう。"},
                             {"text": "一起休息吧。喝杯茶吧。", "say": "一緒に休みましょう。お茶にしましょう。", "style": "力を抜いて穏やかに誘う。"}]
                    before = len(calls)
                    result = plugins.run(synthesize_speech_units(engine=engine, units=units,
                        payload={"real_user_id": "master", "session_id": "qq-session", "client_mode": "qq",
                                 "character_pack_id": "cecilia"}))
                    with wave.open(io.BytesIO(result.audio), "rb") as decoded:
                        self.assertEqual((decoded.getframerate(), decoded.getnframes()), (48000, 960))
                    self.assertEqual([call["input"] for call in calls[before:]], [unit["say"] for unit in units])
                    self.assertEqual([call["irodori"]["caption"] for call in calls[before:]], [unit["style"] for unit in units])
                    for call in calls:
                        self.assertEqual(call["voice"], "cecilia")
                        self.assertEqual(call["irodori"]["num_steps"], 32)
                        self.assertNotIn("ref_embed", call["irodori"])
                        self.assertFalse(call["irodori"]["chunking_enabled"], "Short expressions must not be split again")
                finally:
                    plugins.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)


class SerialLaneTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_response_disconnect_is_not_a_discard(self):
        incoming = asyncio.Queue()
        incoming.put_nowait({"type": "http.request", "body": b"test"})

        async def endpoint(scope, receive, send):
            await receive()
            await send({"type": "http.response.start", "status": 200})
            await send({"type": "http.response.body", "body": b"audio"})
            await asyncio.sleep(.01)

        async def send(message):
            if message["type"] == "http.response.body":
                incoming.put_nowait({"type": "http.disconnect"})

        lane = SerialSpeechApp(endpoint, runtime_status=lambda: {}, clear_cache=lambda: None)
        await lane({"type": "http", "path": "/v1/audio/speech", "method": "POST"}, incoming.get, send)
        self.assertEqual((lane.completed, lane.discarded), (1, 0))

    async def test_desktop_disconnect_reaches_synthesis_cancellation(self):
        from unittest.mock import patch
        from companion_v01.routes.voice import _synthesize_for_request
        disconnected = asyncio.Event()
        observed = asyncio.Event()

        class Request:
            async def is_disconnected(self):
                return disconnected.is_set()

        async def synthesize(*, cancel_requested, **_kwargs):
            while not cancel_requested():
                await asyncio.sleep(0.01)
            observed.set()
            return "cancelled"

        with patch("companion_v01.routes.voice.synthesize_tts_resolution", synthesize):
            work = asyncio.create_task(_synthesize_for_request(request=Request()))
            await asyncio.sleep(0)
            disconnected.set()
            self.assertEqual(await asyncio.wait_for(work, 1), "cancelled")
            self.assertTrue(observed.is_set())

    async def test_cancel_queued_and_drain_active_without_overlapping_work(self):
        first_started, release = asyncio.Event(), asyncio.Event()
        running, peak, texts = 0, 0, []
        async def endpoint(scope, receive, send):
            nonlocal running, peak
            message = await receive()
            texts.append(message["body"])
            running += 1
            peak = max(peak, running)
            first_started.set()
            await release.wait()
            running -= 1
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"audio"})
        lane = SerialSpeechApp(endpoint, runtime_status=lambda: {"ready": True}, clear_cache=lambda: None)
        async def call(text):
            incoming = asyncio.Queue()
            incoming.put_nowait({"type": "http.request", "body": text, "more_body": False})
            sent = []
            async def send(message):
                sent.append(message)
            task = asyncio.create_task(lane({"type": "http", "path": "/v1/audio/speech", "method": "POST"}, incoming.get, send))
            return task, incoming, sent
        first, first_input, first_sent = await call(b"first")
        await first_started.wait()
        second, second_input, second_sent = await call(b"stale")
        await asyncio.sleep(.01)
        second_input.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(second, 1)
        self.assertEqual(texts, [b"first"])
        first_input.put_nowait({"type": "http.disconnect"})
        first.cancel()
        await asyncio.sleep(.01)
        self.assertFalse(first.done())
        third, _third_input, third_sent = await call(b"next")
        release.set()
        await asyncio.wait_for(asyncio.gather(first, third), 1)
        self.assertEqual(texts, [b"first", b"next"])
        self.assertEqual(peak, 1)
        self.assertEqual(first_sent, [])
        self.assertEqual(second_sent, [])
        self.assertTrue(third_sent)
        self.assertEqual((lane.queued, lane.active, lane.discarded), (0, 0, 2))


if __name__ == "__main__":
    unittest.main()
