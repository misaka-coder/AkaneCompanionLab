"""Focused contract tests for model-directed QQ card/audio delivery."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import urllib.request
from unittest.mock import patch

from channelcore_onebot import music_segment

from companion_v01.capability_registry import CapabilityRegistry, CapabilitySnapshot
from companion_v01.client_protocol import ClientMode
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.qq_gateway import NapCatQQGateway
from companion_v01 import qq_music_audio
from companion_v01.qq_music_audio import resolve_public_audio_url
from companion_v01.qq_tool_delivery import QQToolDeliveryPort
from companion_v01.skill_runtime import SkillRegistry
from companion_v01.tool_handlers.core import TOOL_SPEC_BY_TYPE, ToolExecutionContext
from companion_v01.tool_handlers.music import SendAudioToolHandler, SendMusicCardToolHandler


ROOT = Path(__file__).resolve().parents[1]
SEARCH_SCRIPT = ROOT / "skills" / "music-card-share" / "scripts" / "search_music.py"
QQ_BOT_ID = 10001
QQ_MASTER_ID = 10002
QQ_GROUP_ID = 20001


def _load_search_script():
    spec = importlib.util.spec_from_file_location("akane_music_search", SEARCH_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeOneBotOk:
    status_code = 200

    def json(self):
        return {"status": "ok", "retcode": 0, "data": {"message_id": 7}}


class _FakeOneBotFailed:
    status_code = 200

    def json(self):
        return {"status": "failed", "retcode": 100, "data": {}}


class _FakeDeliveryPort:
    def __init__(self, *, result: dict | None = None) -> None:
        self.result = result or {"ok": True, "status": "sent"}
        self.calls: list[tuple[str, dict]] = []

    def send_music_card(self, **kwargs):
        self.calls.append(("card", dict(kwargs)))
        return dict(self.result)

    def send_audio_url(self, **kwargs):
        self.calls.append(("url", dict(kwargs)))
        return dict(self.result)

    def send_audio_file(self, **kwargs):
        self.calls.append(("file", dict(kwargs)))
        return dict(self.result)


class _FakeGeneratedFiles:
    def __init__(self, file_ref: dict | None = None, *, ok: bool = True) -> None:
        self.file_ref = file_ref or {
            "handle": "audio_001",
            "name": "sample.mp3",
            "mime_type": "audio/mpeg",
            "absolute_path": str(ROOT / "tests" / "fixtures" / "sample.mp3"),
        }
        self.ok = ok

    def send_file(self, **_kwargs):
        if not self.ok:
            return {"ok": False, "files": [], "error": "file_not_found", "followup_context": "没找到。"}
        return {"ok": True, "files": [dict(self.file_ref)], "followup_context": "已找到。"}


def _tool_context() -> ToolExecutionContext:
    return ToolExecutionContext(
        profile_user_id="alice",
        session_id="s1",
        now_ts=1,
        visual_payload={},
        client_mode="qq_text",
        request_context={"qq_delivery_context": {"target_id": QQ_MASTER_ID}},
    )


class MusicSegmentContractTests(unittest.TestCase):
    def test_music_segment_projects_exact_onebot_json(self) -> None:
        self.assertEqual(
            music_segment("163", "2703973041").as_onebot(),
            {"type": "music", "data": {"type": "163", "id": "2703973041"}},
        )


class ModelDirectedDeliveryHandlerTests(unittest.TestCase):
    def test_card_success_is_a_real_receipt_not_a_queue_claim(self) -> None:
        port = _FakeDeliveryPort()
        handler = SendMusicCardToolHandler(delivery_port=port)
        result = handler.execute(
            call={"type": "send_music_card", "platform": "netease_music", "track_id": "2703973041"},
            context=_tool_context(),
        )
        self.assertIn("真实发送成功", result.followup_context)
        self.assertNotIn("进入本轮", result.followup_context)
        self.assertEqual(port.calls[0][0], "card")

    def test_card_failure_returns_to_model_and_never_auto_sends_voice(self) -> None:
        port = _FakeDeliveryPort(result={"ok": False, "status": "failed", "reason": "onebot_rejected"})
        handler = SendMusicCardToolHandler(delivery_port=port)
        result = handler.execute(
            call={"type": "send_music_card", "platform": "netease_music", "track_id": "2703973041"},
            context=_tool_context(),
        )
        self.assertIn("没有发送成功", result.followup_context)
        self.assertIn("没有自动改发语音", result.followup_context)
        self.assertIn("send_audio", result.followup_context)
        self.assertEqual([name for name, _ in port.calls], ["card"])

    def test_delivery_exception_becomes_tool_feedback_instead_of_escaping_turn(self) -> None:
        class ExplodingPort(_FakeDeliveryPort):
            def send_music_card(self, **_kwargs):
                raise RuntimeError("transport exploded")

        result = SendMusicCardToolHandler(delivery_port=ExplodingPort()).execute(
            call={"type": "send_music_card", "platform": "netease_music", "track_id": "2703973041"},
            context=_tool_context(),
        )
        self.assertIn("qq_music_card_transport_exception", result.followup_context)
        self.assertTrue(result.stream_events)

    def test_public_url_and_local_handle_share_one_audio_tool(self) -> None:
        port = _FakeDeliveryPort()
        handler = SendAudioToolHandler(generated_file_service=_FakeGeneratedFiles(), delivery_port=port)
        url_result = handler.execute(
            call={"type": "send_audio", "source": "https://cdn.example/song.mp3"},
            context=_tool_context(),
        )
        file_result = handler.execute(
            call={"type": "send_audio", "source": "audio_001"},
            context=_tool_context(),
        )
        self.assertIn("真实发送成功", url_result.followup_context)
        self.assertIn("真实发送成功", file_result.followup_context)
        self.assertEqual([name for name, _ in port.calls], ["url", "file"])
        self.assertNotIn("delivery_mode", port.calls[1][1])

    def test_non_audio_handle_is_rejected_before_transport(self) -> None:
        port = _FakeDeliveryPort()
        files = _FakeGeneratedFiles(
            {"handle": "file_001", "name": "report.pdf", "mime_type": "application/pdf", "absolute_path": "x.pdf"}
        )
        result = SendAudioToolHandler(generated_file_service=files, delivery_port=port).execute(
            call={"type": "send_audio", "source": "file_001"},
            context=_tool_context(),
        )
        self.assertIn("不是可确认的音频文件", result.followup_context)
        self.assertEqual(port.calls, [])


class GatewayTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        patchers = [
            patch("companion_v01.qq_gateway.config.MASTER_QQ", str(QQ_MASTER_ID)),
            patch("companion_v01.qq_gateway.config.QQ_BOT_QQ", str(QQ_BOT_ID)),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.gateway = NapCatQQGateway()
        self.private_context = self.gateway.build_message_context(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": QQ_BOT_ID,
                "user_id": QQ_MASTER_ID,
                "message_id": "music-private-1",
                "raw_message": "推歌",
            }
        )

    def test_netease_card_returns_real_onebot_success(self) -> None:
        with patch("companion_v01.onebot_transport.requests.Session.request", return_value=_FakeOneBotOk()) as request:
            result = self.gateway.send_music_card(
                self.private_context, platform="netease_music", track_id="2703973041"
            )
        self.assertTrue(result["ok"])
        payload = request.call_args.kwargs["json"]
        self.assertEqual(payload["message"][0], {"type": "music", "data": {"type": "163", "id": "2703973041"}})

    def test_unsupported_card_platform_returns_failure_without_voice(self) -> None:
        with patch("companion_v01.onebot_transport.requests.Session.request") as request:
            result = self.gateway.send_music_card(
                self.private_context, platform="qq_music", track_id="002XWgfo0IKPOH"
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unsupported_music_card_platform")
        request.assert_not_called()

    def test_card_tool_schema_exposes_only_the_working_netease_platform(self) -> None:
        platform_schema = TOOL_SPEC_BY_TYPE["send_music_card"].input_schema["properties"]["platform"]
        self.assertEqual(platform_schema["enum"], ["netease_music"])
        self.assertIsNone(
            SendMusicCardToolHandler().normalize_call(
                {"type": "send_music_card", "platform": "qq_music", "track_id": "002XWgfo0IKPOH"}
            )
        )

    def test_audio_tool_schema_has_one_voice_surface(self) -> None:
        properties = TOOL_SPEC_BY_TYPE["send_audio"].input_schema["properties"]
        self.assertNotIn("delivery_mode", properties)

    def test_port_preflights_url_then_sends_one_voice_segment(self) -> None:
        port = QQToolDeliveryPort(self.gateway)
        with (
            patch(
                "companion_v01.qq_tool_delivery.resolve_public_audio_url",
                return_value={"ok": True, "status": "ready", "url": "https://cdn.example/song.mp3"},
            ),
            patch("companion_v01.onebot_transport.requests.Session.request", return_value=_FakeOneBotOk()) as request,
        ):
            result = port.send_audio_url(
                request_context={"qq_delivery_context": self.private_context.to_delivery_context()},
                audio_url="https://source.example/song.mp3",
                name="song",
            )
        self.assertTrue(result["ok"])
        message = request.call_args.kwargs["json"]["message"]
        self.assertEqual(message[0]["type"], "record")
        self.assertEqual(message[0]["data"]["file"], "https://cdn.example/song.mp3")


class PublicAudioPreflightTests(unittest.TestCase):
    class Response:
        headers = {"Content-Type": "audio/mpeg"}

        def __init__(self, final_url: str = "https://cdn.example/song.mp3") -> None:
            self.final_url = final_url

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size=-1):
            return b"ID3\x04" + b"x" * 64

        def geturl(self):
            return self.final_url

    @staticmethod
    def public_dns(*_args, **_kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    def test_verified_public_audio_is_accepted(self) -> None:
        result = resolve_public_audio_url(
            "https://source.example/song.mp3",
            opener=lambda *_a, **_k: self.Response(),
            resolver=self.public_dns,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["url"], "https://cdn.example/song.mp3")

    def test_private_host_and_html_are_rejected(self) -> None:
        private = resolve_public_audio_url("http://127.0.0.1/secret.mp3")
        self.assertFalse(private["ok"])
        self.assertEqual(private["status"], "private_or_reserved_host")

        class Html(self.Response):
            headers = {"Content-Type": "text/html"}

            def read(self, _size=-1):
                return b"<!DOCTYPE html>"

        html = resolve_public_audio_url(
            "https://source.example/song.mp3",
            opener=lambda *_a, **_k: Html(),
            resolver=self.public_dns,
        )
        self.assertFalse(html["ok"])
        self.assertEqual(html["status"], "not_public_audio")

    def test_redirect_handler_rejects_private_target_before_following_it(self) -> None:
        handler = qq_music_audio._PublicAudioRedirectHandler(resolver=lambda *_a, **_k: [
            (2, 1, 6, "", ("127.0.0.1", 0))
        ])
        request = urllib.request.Request("https://source.example/a.mp3")
        with self.assertRaises(qq_music_audio._UnsafeAudioRedirectError):
            handler.redirect_request(request, None, 302, "Found", {}, "http://localhost/internal")

    def test_fake_ip_exception_is_narrowly_limited_to_real_provider_hosts(self) -> None:
        fake_dns = lambda *_a, **_k: [(2, 1, 6, "", ("198.18.0.7", 0))]
        valid = resolve_public_audio_url(
            "https://m701.music.126.net/song.mp3",
            opener=lambda *_a, **_k: self.Response("https://m701.music.126.net/song.mp3"),
            resolver=fake_dns,
        )
        deceptive = resolve_public_audio_url(
            "https://music.126.net.attacker.example/song.mp3",
            opener=lambda *_a, **_k: self.Response(),
            resolver=fake_dns,
        )
        self.assertTrue(valid["ok"])
        self.assertFalse(deceptive["ok"])
        self.assertEqual(deceptive["status"], "private_or_reserved_host")


class _SpecShim:
    def __init__(self, spec) -> None:
        self.tool_type = spec.capability_id
        self._spec = spec

    def tool_spec(self):
        return self._spec


class CapabilityAndSkillTests(unittest.TestCase):
    def test_qq_has_card_and_audio_tools_desktop_has_neither(self) -> None:
        registry = CapabilityRegistry()
        qq = registry.select(CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT))
        desktop = registry.select(CapabilitySnapshot(client_mode=ClientMode.DESKTOP_PET))
        for name in ("send_music_card", "send_audio"):
            self.assertIn(name, qq.tool_names)
            self.assertIn(name, qq.schema_tool_names)
            self.assertNotIn(name, desktop.tool_names)

    def test_qq_schema_is_byte_stable_for_same_profile(self) -> None:
        selection = CapabilityRegistry().select(CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT))
        names = set(selection.schema_tool_names)
        handlers = {name: _SpecShim(TOOL_SPEC_BY_TYPE[name]) for name in names if name in TOOL_SPEC_BY_TYPE}
        first = build_openai_native_tool_specs(handlers, allowed_tool_names=names)
        second = build_openai_native_tool_specs(handlers, allowed_tool_names=names)
        self.assertEqual(
            json.dumps(first, ensure_ascii=False, separators=(",", ":")),
            json.dumps(second, ensure_ascii=False, separators=(",", ":")),
        )

    def test_skill_explains_model_directed_fallback_and_audio_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = SkillRegistry(
                bundled_root=ROOT / "skills",
                managed_root=Path(temp_dir) / "managed",
                execution_workspace_root=Path(temp_dir) / "workspace",
            )
            loaded = registry.load("music-card-share")
        self.assertEqual(loaded.status, "loaded")
        for text in ("send_music_card", "send_audio", "--audio-track-id", "never turn"):
            self.assertIn(text, loaded.content)
        self.assertNotIn(str(ROOT), loaded.content)

    def test_search_script_resolves_netease_audio_without_downloading(self) -> None:
        script = _load_search_script()
        result = script._resolve_audio_url("netease", "1971144922")
        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["audio_url"],
            "https://music.163.com/song/media/outer/url?id=1971144922.mp3",
        )


if __name__ == "__main__":
    unittest.main()
