from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.care_runtime import CareModulePort
from companion_v01.plugin_api import PluginQQCommandResult
from companion_v01.qq_commands import resolve_qq_command
from companion_v01.qq_gateway import NapCatQQGateway, QQMessageContext
from companion_v01.routes.qq import build_qq_router
from tests.test_qq_gateway import fake_outfit_manifest


class _DispatchPluginBroker:
    registered_commands = ("/ping", "/help")

    def __init__(self):
        self.calls = []

    def handles(self, command):
        return command in self.registered_commands

    async def dispatch(self, **kwargs):
        self.calls.append(kwargs)
        return PluginQQCommandResult(handled=True, reply_text="pong")


class QQCommandDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch("companion_v01.qq_gateway.config.MASTER_QQ", "10002"))
        self.enterContext(patch("companion_v01.qq_gateway.config.QQ_BOT_QQ", "10001"))
        self.enterContext(patch.object(NapCatQQGateway, "_resolve_bot_nickname", return_value="塞西莉亚"))
        self.enterContext(patch.object(NapCatQQGateway, "_resolve_bot_group_label", return_value="塞西莉亚"))
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.gateway = NapCatQQGateway(state_path=self.root / "qq-state.json")
        self.broker = _DispatchPluginBroker()
        self.engine = SimpleNamespace(
            desktop_pet_character_resources=None,
            get_care_module=lambda: CareModulePort.disabled(),
            record_passive_qq_message=lambda _payload: {"ok": True, "status": "recorded"},
            build_resource_manifest=lambda **_kwargs: fake_outfit_manifest(),
            prefetch_remote_media_links_for_message=lambda **_kwargs: {},
            mark_generated_file_delivery=lambda **_kwargs: {"ok": True},
        )

        self.turns_called = []

        def fake_turn_stream(turn_payload):
            self.turns_called.append(turn_payload)
            yield {
                "type": "final_ui",
                "payload": {
                    "reply_medium": "text",
                    "speech": "好的，衣服已经换好了。",
                    "speech_segments": ["好的，衣服已经换好了。"],
                    "tool_events": [],
                },
            }

        self.engine.process_turn_stream = fake_turn_stream

        app = FastAPI()
        self.logs = []
        app.include_router(
            build_qq_router(
                engine=self.engine,
                config_module=SimpleNamespace(QQ_BRIDGE_ENABLED=True, MASTER_QQ="10002", DATA_DIR=str(self.root)),
                qq_gateway=self.gateway,
                runtime_metrics=SimpleNamespace(observe_request=lambda *_args, **_kwargs: None),
                logger=SimpleNamespace(exception=lambda *_args, **_kwargs: None),
                log_event=lambda name, **kwargs: self.logs.append((name, kwargs)),
                plugin_command_broker_provider=lambda: self.broker,
            )
        )
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.sequence = 0
        self.sent = []

        def fake_request(_method, _url, **kwargs):
            if "message" in kwargs["json"]:
                self.sent.append(kwargs["json"])
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"status": "ok", "data": {"message_id": 50000 + len(self.sent)}},
            )

        self.enterContext(patch("companion_v01.onebot_transport.requests.Session.request", side_effect=fake_request))
        image = self.root / "help.png"
        image.write_bytes(b"help-image-fixture")
        self.render = self.enterContext(patch("companion_v01.qq_command_cards.render_qq_help_card", return_value=image))

    def _event(self, command, *, group=False, target=False, user_id=10002, message_override=None):
        self.sequence += 1
        msg = message_override if message_override is not None else (
            ([{"type": "at", "data": {"qq": "10001"}}] if target else [])
            + [{"type": "text", "data": {"text": command}}]
        )
        return {
            "post_type": "message",
            "message_type": "group" if group else "private",
            "self_id": 10001,
            "user_id": user_id,
            "group_id": 20001 if group else 0,
            "message_id": str(60000 + self.sequence),
            "sender": {"role": "member"},
            "message": msg,
        }

    def _post(self, command, **kwargs):
        response = self.client.post("/api/qq/napcat/event", json=self._event(command, **kwargs))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _text(self):
        return "\n".join(
            str(segment["data"].get("text", ""))
            for message in self.sent
            for segment in message.get("message", ())
            if segment["type"] == "text"
        )

    def test_outfit_switch_and_reset_enters_model_turn_without_unknown_command(self) -> None:
        self.gateway.set_session_character_pack_id("master", "reimu")
        result = self._post("/服装 水手服")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(self.turns_called), 1)
        self.assertIn("水手服", self.turns_called[0].get("qq_action_note", ""))
        self.assertEqual(self.gateway.resolve_session_outfit_id("master"), "sailor")
        self.assertNotIn("未识别这条指令", self._text())

        result_reset = self._post("/服装 默认")
        self.assertEqual(result_reset["status"], "ok")
        self.assertEqual(len(self.turns_called), 2)
        self.assertIn("默认服装", self.turns_called[1].get("qq_action_note", ""))
        self.assertNotIn("未识别这条指令", self._text())

        # Unknown outfit fails locally and never reaches the model pipeline
        result_unknown = self._post("/服装 不存在")
        self.assertEqual(result_unknown["command_status"], "unknown_outfit")
        self.assertFalse(result_unknown["command_ok"])
        self.assertEqual(len(self.turns_called), 2)

    def test_command_service_stage_parses_once_and_passes_payload_directly(self) -> None:
        economy_spy = MagicMock(wraps=self.gateway.handle_economy_command)
        char_spy = MagicMock(wraps=self.gateway.handle_character_command)
        reply_spy = MagicMock(wraps=self.gateway.handle_reply_mode_command)
        parse_reply_spy = MagicMock(wraps=self.gateway.parse_reply_mode_command)

        with patch.object(self.gateway, "handle_economy_command", economy_spy), \
             patch.object(self.gateway, "handle_character_command", char_spy), \
             patch.object(self.gateway, "handle_reply_mode_command", reply_spy), \
             patch.object(self.gateway, "parse_reply_mode_command", parse_reply_spy):

            result = self._post("/回复 双发")
            self.assertEqual(result["reason"], "qq_reply_mode_command")
            self.assertEqual(result["reply_mode"], "both")

            # Only reply_mode handler was called; other domains were not touched
            reply_spy.assert_called_once()
            economy_spy.assert_not_called()
            char_spy.assert_not_called()

            # The domain handler received the pre-parsed command payload and did NOT call its parser
            call_kwargs = reply_spy.call_args.kwargs
            self.assertEqual(call_kwargs.get("command"), {"action": "switch", "reply_mode": "both"})
            parse_reply_spy.assert_not_called()

    def test_selectors_do_not_cross_wires(self) -> None:
        # /模型, /角色 列表, /服装 are all selector kinds; verify they route cleanly
        result_model = self._post("/模型")
        self.assertEqual(result_model["reason"], "qq_chat_model_command")

        result_char = self._post("/角色 列表")
        self.assertEqual(result_char["reason"], "qq_character_command")

        result_outfit = self._post("/服装 列表")
        self.assertEqual(result_outfit["reason"], "qq_outfit_command")

    def test_unknown_and_ordinary_chat_do_not_probe_economy(self) -> None:
        economy_spy = MagicMock(wraps=self.gateway.handle_economy_command)
        with patch.object(self.gateway, "handle_economy_command", economy_spy):
            # Unknown command fails locally without loading economy
            result_unknown = self._post("/不存在")
            self.assertEqual(result_unknown["reason"], "qq_command_error")
            self.assertEqual(result_unknown["command_status"], "unknown_command")
            economy_spy.assert_not_called()

            # Plugin command is intercepted without touching economy
            result_plugin = self._post("/ping")
            self.assertEqual(result_plugin["reason"], "qq_plugin_command")
            economy_spy.assert_not_called()

            # Ordinary chat passes through without touching economy
            self._post("今天天气真好")
            economy_spy.assert_not_called()

    def test_empty_resolution_is_reused_without_reparsing(self) -> None:
        # Isolate dispatch from the turn runtime's separate stop classification.
        for message in ("今天天气真好", "/不存在", "/ping"):
            with self.subTest(message=message), patch(
                "companion_v01.qq_inbound_events.resolve_qq_command", wraps=resolve_qq_command,
            ) as entry_parser, patch(
                "companion_v01.qq_command_handlers.resolve_qq_command", wraps=resolve_qq_command,
            ) as handler_parser, patch("companion_v01.qq_turn_runtime._is_qq_stop_command", return_value=False):
                self._post(message)
                entry_parser.assert_called_once_with(message)
                handler_parser.assert_not_called()

    def test_missing_local_result_fails_without_requesting_model(self) -> None:
        with patch.object(self.gateway, "handle_reply_mode_command", return_value=None):
            result = self._post("/回复 双发")
        self.assertFalse(result["command_ok"])
        self.assertEqual(result["command_status"], "unexpected_result")
        self.assertEqual(self.turns_called, [])
        self.assertIn("处理失败", self._text())

    def test_mface_command_cleans_media_placeholders_and_retains_emotion(self) -> None:
        res = resolve_qq_command("/表情包 happy [QQ商城表情face_123]")
        self.assertIsNotNone(res)
        self.assertFalse(res.error)
        self.assertEqual(res.payload, {"emotion": "happy"})

        # Empty emotion after placeholder fallback to happy
        res_empty = resolve_qq_command("/表情包 [图片]")
        self.assertIsNotNone(res_empty)
        self.assertEqual(res_empty.payload, {"emotion": "happy"})
        self.assertEqual(self.gateway.parse_mface_config_command("[图片] /表情包 happy"), "happy")
        self.assertEqual(resolve_qq_command("/投喂 [图片]").payload["item_text"], "[图片]")
        self.assertIsNone(resolve_qq_command("[图片] /投喂 蛋糕"))

    def test_mface_before_or_after_command_uses_same_local_handler(self) -> None:
        face = {"type": "mface", "data": {
            "emoji_package_id": 123, "emoji_id": "happy-001", "key": "fixture-key",
        }}
        command = {"type": "text", "data": {"text": " /表情包 happy "}}
        for group in (False, True):
            for segments in ([command, face], [face, command]):
                with self.subTest(group=group, segments=segments):
                    message = ([{"type": "at", "data": {"qq": "10001"}}] if group else []) + segments
                    result = self._post("", group=group, message_override=message)
                    self.assertEqual(result.get("reason"), "qq_mface_config_command")
                    self.assertEqual(result["command_status"], "captured")
                    self.assertEqual(result["emotion"], "happy")
        result = self._post("", group=True, message_override=[face, command])
        self.assertEqual(result["reason"], "group_command_requires_explicit_mention")
        self.assertEqual(self.turns_called, [])

    def test_stop_command_bypasses_unknown_command_and_passes_to_turn_runtime(self) -> None:
        # /停止 must not produce "unknown_command" error, but fall through to runtime
        result = self._post("/停止")
        # In this test client, falling through without local error returns the turn outcome
        self.assertNotEqual(result.get("command_status"), "unknown_command")
        self.assertNotIn("未识别这条指令", self._text())


if __name__ == "__main__":
    unittest.main()
