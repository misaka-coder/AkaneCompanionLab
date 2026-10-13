from __future__ import annotations

import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.care_runtime import CareModulePort
from companion_v01.channels.qq.cards.help import render_qq_help_card
from companion_v01.channels.qq.commands.handler import _is_qq_stop_command
from companion_v01.channels.qq.commands.parser import QQHelpRequest, build_qq_help, resolve_qq_command
from companion_v01.channels.qq.commands.workspace import _parse_qq_workspace_command
from companion_v01.channels.qq.context import QQMessageContext
from companion_v01.channels.qq.gateway import NapCatQQGateway
from companion_v01.plugin_api import PluginQQCommandResult
from companion_v01.routes.qq import build_qq_router
from tests.qq_card_fixtures import install_stationery_fixture


class QQCanonicalCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch.object(NapCatQQGateway, "_resolve_bot_nickname", return_value="塞西莉亚"))
        self.gateway = NapCatQQGateway(wake_words=("塞西莉亚",))

    def test_new_families_reach_existing_parsers(self) -> None:
        cases = (
            ("parse_chat_model_command", "/模型", {"action": "current"}),
            ("parse_chat_model_command", "/模型 列表", {"action": "list"}),
            ("parse_chat_model_command", "/模型 gemini-2.5-pro", {"action": "switch", "model": "gemini-2.5-pro"}),
            ("parse_chat_model_command", "/模型 默认", {"action": "default"}),
            ("parse_character_command", "/角色 列表", {"action": "list"}),
            ("parse_character_command", "/角色 reimu", {"action": "switch", "pack_id": "reimu"}),
            ("parse_character_command", "/角色 内置", {"action": "builtin"}),
            ("parse_outfit_command", "/服装 默认", {"action": "default"}),
            ("parse_outfit_command", "/服装 sailor", {"action": "switch", "outfit_id": "sailor"}),
            ("parse_reply_mode_command", "/回复", {"action": "current"}),
            ("parse_reply_mode_command", "/回复 双发", {"action": "switch", "reply_mode": "both"}),
            ("parse_reply_mode_command", "/回复 默认", {"action": "default"}),
            ("parse_group_vision_command", "/识图", {"action": "status"}),
            ("parse_group_vision_command", "/识图 开", {"action": "enable"}),
            ("parse_group_emotion_command", "/表情 关", {"action": "off"}),
            ("parse_group_attention_command", "/搭话 自然", {"action": "adaptive"}),
            ("parse_thinking_mode_command", "/思考 开", {"action": "enable"}),
            ("parse_access_permission_command", "/权限 扩展 询问", {"action": "ask", "scope": "extensions"}),
            ("parse_access_permission_command", "/权限 全部", {"action": "status", "scope": "all"}),
            ("parse_capability_approval_command", "/批准 abc-123", {"action": "approve", "selector": "abc-123"}),
            ("parse_economy_command", "/签到", {"action": "checkin"}),
            ("parse_economy_command", "/养成", {"action": "status"}),
            ("parse_economy_command", "/购买 三色团子 ×2", {"action": "buy", "item_name": "三色团子", "quantity": 2}),
            ("parse_economy_command", "/投喂 三色团子", {"action": "feed", "item_name": "三色团子", "quantity": 1}),
            ("parse_economy_command", "/供奉 状态", {"action": "offering_status"}),
            ("parse_economy_command", "/赛钱 10", {"action": "saiken", "amount": 10}),
            ("parse_economy_command", "/额度", {"action": "quota"}),
        )
        for parser, command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(getattr(self.gateway, parser)(command), expected)
                self.assertEqual(getattr(self.gateway, parser)(command.replace("/", "／", 1)), expected)

    def test_legacy_aliases_are_removed_and_natural_chat_is_not_a_command(self) -> None:
        retired = {
            "parse_economy_command": ("签到", "养成状态", "Akane 购买 三色团子"),
            "parse_chat_model_command": ("/模型列表", "/model list", "模型列表"),
            "parse_group_attention_command": ("/listen off", "listen off"),
            "parse_reply_mode_command": ("语音模式", "/reply voice"),
            "parse_character_command": ("切换角色 reimu", "/角色列表", "塞西莉亚 /角色 列表"),
            "parse_access_permission_command": ("/access all on",),
        }
        for parser, messages in retired.items():
            for message in messages:
                with self.subTest(message=message):
                    self.assertIsNone(getattr(self.gateway, parser)(message))
        for message in ("模型最近好慢", "投喂的话你喜欢什么", "角色好可爱", "帮我看看图片"):
            self.assertIsNone(resolve_qq_command(message))
            self.assertIsNone(self.gateway.parse_economy_command(message))

    def test_bad_arguments_do_not_become_unrelated_commands(self) -> None:
        for command in (
            "/回复 很多",
            "/搭话 开",
            "/识图 明天",
            "/权限 全部 不知道",
            "/购买",
            "/投喂",
            "/赛钱 -1",
            "/审批 extra",
            "/help 不存在",
            "/help 模型 100",
        ):
            with self.subTest(command=command):
                self.assertTrue(resolve_qq_command(command).error)
                self.assertIsNone(self.gateway.parse_economy_command(command))
                self.assertIsNone(self.gateway.parse_character_command(command))

    def test_workspace_clear_keeps_original_files_and_stop_uses_existing_control(self) -> None:
        parsed = _parse_qq_workspace_command(self.gateway, "/材料 清理 最近")
        self.assertEqual(parsed, {"action": "clear", "target": "latest", "kind": "any", "delete_storage": False})
        parsed = _parse_qq_workspace_command(self.gateway, "/材料 清理 attachment-1")
        self.assertEqual(parsed["target"], "attachment-1")
        self.assertFalse(parsed["delete_storage"])
        self.assertTrue(_is_qq_stop_command("/停止"))
        self.assertFalse(_is_qq_stop_command("/stop"))
        self.assertFalse(_is_qq_stop_command("停下来想想之后怎么做"))

    def test_vision_status_does_not_change_the_setting_or_require_admin(self) -> None:
        context = QQMessageContext(True, "group_mention", is_group=True, group_id=20001, clean_message="/识图")
        result = self.gateway.handle_group_vision_command(context, sender_role="member")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "current")
        self.assertTrue(self.gateway.is_group_vision_enabled(20001))
        context = replace(context, clean_message="/识图 关")
        self.assertEqual(self.gateway.handle_group_vision_command(context, sender_role="member")["status"], "forbidden")
        self.assertTrue(self.gateway.is_group_vision_enabled(20001))


class QQHelpDocumentTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        install_stationery_fixture(self, root)

    def _build(self, request: QQHelpRequest = QQHelpRequest(), **kwargs):
        return build_qq_help(
            request, **{"is_group": True, "is_master": True, "master_configured": True, "care_enabled": True, **kwargs}
        )

    def test_private_member_help_filters_unavailable_categories(self) -> None:
        document = self._build(is_group=False, is_master=False, care_enabled=False)
        usages = [entry.usage for entry in document.entries]
        self.assertNotIn("/help 群聊", usages)
        self.assertNotIn("/help 权限", usages)
        self.assertNotIn("/help 模型", usages)
        self.assertNotIn("/help 养成", usages)
        self.assertIn("/help 回复", usages)
        with self.assertRaises(ValueError):
            self._build(QQHelpRequest("权限"), is_master=False)

    def test_help_uses_current_plugins_and_reserved_help_is_not_duplicated(self) -> None:
        document = self._build(QQHelpRequest("插件"), plugin_commands=("/help", "/模型", "/ping", "/测试"))
        self.assertEqual([entry.usage for entry in document.entries], ["/ping", "/测试"])
        with self.assertRaises(ValueError):
            self._build(QQHelpRequest("插件"), plugin_commands=())

    def test_care_fits_one_extended_page_and_large_plugin_lists_are_paginated(self) -> None:
        first = self._build(QQHelpRequest("养成"))
        self.assertEqual(first.page_count, 1)
        self.assertEqual(len(first.entries), 11)
        self.assertIn("/供奉", first.as_text())
        self.assertIn("/签到", first.as_text())
        plugins = tuple(f"/custom_{index}" for index in range(13))
        first = self._build(QQHelpRequest("插件"), plugin_commands=plugins)
        second = self._build(QQHelpRequest("插件", page=2), plugin_commands=plugins)
        self.assertEqual(first.page_count, 2)
        self.assertEqual(len(first.entries), 12)
        self.assertEqual({entry.usage for entry in (*first.entries, *second.entries)}, set(plugins))
        with self.assertRaises(ValueError):
            self._build(QQHelpRequest("插件", page=3), plugin_commands=plugins)

    def test_missing_owner_binding_does_not_advertise_approval(self) -> None:
        document = self._build(master_configured=False)
        self.assertNotIn("/help 权限", document.as_text())
        self.assertIn("/help 模型", document.as_text())

    def test_help_format_and_category_aliases(self) -> None:
        self.assertEqual(resolve_qq_command("/help 文字 模型 2").help_request, QQHelpRequest("模型", True, 2))
        self.assertEqual(resolve_qq_command("／help 文字 回复").help_request, QQHelpRequest("回复", True))

    def test_card_render_is_readable_sized_cached_and_sensitive_to_visible_entries(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            try:
                path = render_qq_help_card(self._build(), directory)
            except RuntimeError as exc:
                if str(exc) == "chinese_font_unavailable":
                    self.skipTest("No CJK font; route text fallback is covered separately.")
                raise
            self.assertEqual(render_qq_help_card(self._build(), directory), path)
            with Image.open(path) as image:
                self.assertEqual(image.width, 900)
                self.assertLessEqual(image.height, 2400)
                image.verify()
            other = render_qq_help_card(self._build(is_group=False), directory)
            self.assertNotEqual(path, other)
            self.assertEqual(len(list(directory.glob("*.png"))), 2)
            self.assertFalse(list(directory.glob("*.tmp")))

    def test_variable_height_preserves_portrait_and_bottom_decoration(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            try:
                short = render_qq_help_card(self._build(QQHelpRequest("回复"), character_pack_id="cecilia"), directory)
                long = render_qq_help_card(self._build(QQHelpRequest("养成"), character_pack_id="cecilia"), directory)
            except RuntimeError as exc:
                if str(exc) == "chinese_font_unavailable":
                    self.skipTest("No CJK font; route text fallback is covered separately.")
                raise
            with Image.open(short) as first, Image.open(long) as second:
                self.assertGreater(second.height, first.height)
                self.assertLessEqual(second.height, 2400)
                self.assertEqual(first.crop((0, 0, 900, 450)).tobytes(), second.crop((0, 0, 900, 450)).tobytes())
                self.assertEqual(
                    first.crop((0, first.height - 110, 900, first.height)).tobytes(),
                    second.crop((0, second.height - 110, 900, second.height)).tobytes(),
                )


class _PluginBroker:
    registered_commands = ("/ping", "/help")

    def __init__(self):
        self.calls = []

    def handles(self, command):
        return command in self.registered_commands

    async def dispatch(self, **kwargs):
        self.calls.append(kwargs)
        return PluginQQCommandResult(handled=True, reply_text="pong")


class QQCommandRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch("companion_v01.channels.qq.gateway.config.MASTER_QQ", "10002"))
        self.enterContext(patch("companion_v01.channels.qq.gateway.config.QQ_BOT_QQ", "10001"))
        self.enterContext(patch.object(NapCatQQGateway, "_resolve_bot_nickname", return_value="塞西莉亚"))
        self.enterContext(patch.object(NapCatQQGateway, "_resolve_bot_group_label", return_value="塞西莉亚"))
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.gateway = NapCatQQGateway(state_path=root / "qq-state.json")
        self.broker = _PluginBroker()
        self.engine = SimpleNamespace(
            desktop_pet_character_resources=None,
            get_care_module=lambda: CareModulePort.disabled(),
            record_passive_qq_message=lambda _payload: {"ok": True, "status": "recorded"},
        )

        # A command must never fall through to inference in these tests.
        def unexpected_turn(_payload):
            self.fail("Local command entered the model pipeline")

        self.engine.process_turn_stream = unexpected_turn
        app = FastAPI()
        self.logs = []
        app.include_router(
            build_qq_router(
                engine=self.engine,
                config_module=SimpleNamespace(QQ_BRIDGE_ENABLED=True, MASTER_QQ="10002", DATA_DIR=str(root)),
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

        self.enterContext(patch("companion_v01.channels.qq.onebot.transport.requests.Session.request", side_effect=fake_request))
        image = root / "help.png"
        image.write_bytes(b"help-image-fixture")
        self.render = self.enterContext(patch("companion_v01.channels.qq.commands.card_results.render_qq_help_card", return_value=image))

    def _event(self, command, *, group=False, target=False, user_id=10002):
        self.sequence += 1
        return {
            "post_type": "message",
            "message_type": "group" if group else "private",
            "self_id": 10001,
            "user_id": user_id,
            "group_id": 20001 if group else 0,
            "message_id": str(60000 + self.sequence),
            "sender": {"role": "member"},
            "message": ([{"type": "at", "data": {"qq": "10001"}}] if target else [])
            + [{"type": "text", "data": {"text": command}}],
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

    def test_help_sends_an_image_locally_and_reserves_host_help_over_plugin(self) -> None:
        result = self._post("/help")
        self.assertEqual(result["help_format"], "image")
        self.assertTrue(any(segment["type"] == "image" for message in self.sent for segment in message["message"]))
        self.assertEqual(self.broker.calls, [])

    def test_copyable_help_never_renders_an_image(self) -> None:
        result = self._post("/help 回复 文字")
        self.assertEqual(result["help_format"], "text")
        self.assertIn("/回复", self._text())
        self.render.assert_not_called()

    def test_slash_command_images_text_and_errors_do_not_quote_input(self) -> None:
        for command in ("/help", "／help", "/help 回复 文字", "/回复 双发", "/不存在"):
            with self.subTest(command=command):
                self._post(command)
        self.assertTrue(self.sent)
        self.assertTrue(any(segment["type"] == "image" for message in self.sent for segment in message["message"]))
        self.assertFalse(any(segment["type"] == "reply" for message in self.sent for segment in message["message"]))
        context = QQMessageContext(
            True,
            "private_message",
            target_id=10002,
            user_id=10002,
            source_message_id="ordinary-chat",
            clean_message="晚上好",
        )
        self.gateway.send_reply(context, "晚上好呀")
        self.assertEqual(self.sent[-1]["message"][0], {"type": "reply", "data": {"id": "ordinary-chat"}})

    def test_group_help_and_unknown_chinese_commands_require_real_at(self) -> None:
        for command in ("/help", "／help", "/不存在", "@塞西莉亚 /help"):
            with self.subTest(command=command):
                result = self._post(command, group=True)
                self.assertEqual(result["reason"], "group_command_requires_explicit_mention")
        self.assertEqual(self.sent, [])
        result = self._post("/help 文字", group=True, target=True, user_id=10003)
        self.assertEqual(result["reason"], "qq_command_help")
        self.assertIn("/help 群聊", self._text())
        self.assertNotIn("/help 权限", self._text())

    def test_image_send_failure_falls_back_to_text_and_preserves_the_reason(self) -> None:
        with patch.object(self.gateway, "send_image", return_value={"ok": False, "reason": "napcat_unreachable"}):
            result = self._post("/help")
        self.assertEqual(result["help_format"], "text_fallback")
        self.assertEqual(result["image_failure_reason"], "napcat_unreachable")
        self.assertIn("/help 回复", self._text())

    def test_missing_font_falls_back_to_text(self) -> None:
        self.render.side_effect = RuntimeError("chinese_font_unavailable")
        result = self._post("/help")
        self.assertEqual(result["help_format"], "text_fallback")
        self.assertEqual(result["image_failure_reason"], "chinese_font_unavailable")

    def test_bad_arguments_unknown_commands_and_unavailable_pages_stay_local(self) -> None:
        for command in ("/回复 很多", "/help 不存在", "/help 群聊", "/help 角色 99", "/help 养成", "/不存在"):
            with self.subTest(command=command):
                result = self._post(command)
                self.assertFalse(result["command_ok"])
                self.assertIn(result["reason"], {"qq_command_error", "qq_command_help"})
        self.render.assert_not_called()

    def test_canonical_commands_reach_real_reply_and_permission_handlers(self) -> None:
        result = self._post("/回复 双发")
        self.assertEqual(result["reason"], "qq_reply_mode_command")
        self.assertEqual(result["reply_mode"], "both")
        result = self._post("/识图 关", group=True, target=True, user_id=10003)
        self.assertEqual(result["command_status"], "forbidden")
        self.assertTrue(self.gateway.is_group_vision_enabled(20001))
        result = self._post("/权限", user_id=10003)
        self.assertEqual(result["command_status"], "forbidden")

    def test_private_memory_command_requires_target_owner_and_available_host(self) -> None:
        self.enterContext(patch.object(self.gateway._event_admission, "_clock", lambda: self.sequence * 1000.0))
        result = self._post("/私人记忆 开", group=True)
        self.assertEqual(result["reason"], "group_command_requires_explicit_mention")
        self.assertFalse(self.gateway._preferences.private_memory_policy(20001)[0])
        result = self._post("/私人记忆 开", group=True, target=True, user_id=10003)
        self.assertEqual(result["command_status"], "forbidden")
        result = self._post("/私人记忆 开", group=True, target=True)
        self.assertEqual(result["command_status"], "unavailable")
        self.engine.owner_private_group_memory = SimpleNamespace(enabled=True, status=lambda session: {"pending": 0, "retry_pending": 0})
        result = self._post("/私人记忆 开", group=True, target=True)
        self.assertIn("command_ok", result, result)
        self.assertTrue(result["command_ok"])
        self.assertTrue(self.gateway._preferences.private_memory_policy(20001)[0])
        result = self._post("/私人记忆 状态", group=True, target=True)
        self.assertEqual(result["command_status"], "current")
        self._post("/help 群聊 文字", group=True, target=True)
        self.assertIn("/私人记忆", self._text())

    def test_duplicate_webhook_sends_help_once(self) -> None:
        event = self._event("/help")
        first = self.client.post("/api/qq/napcat/event", json=event)
        count = len(self.sent)
        second = self.client.post("/api/qq/napcat/event", json=event)
        self.assertEqual(first.json()["help_format"], "image")
        self.assertEqual(second.json()["reason"], "duplicate_event")
        self.assertEqual(len(self.sent), count)

    def test_plugins_remain_callable_and_help_reads_the_current_registry(self) -> None:
        result = self._post("／PING hi")
        self.assertEqual(result["reason"], "qq_plugin_command")
        self.assertEqual(self.broker.calls[0]["args"], "hi")
        self._post("/help 插件 文字")
        self.assertIn("/ping", self._text())
        self.broker.registered_commands = ()
        result = self._post("/help 插件 文字 1")
        self.assertEqual(result["command_status"], "unavailable_page")

    def _enable_care(self):
        self.gateway._bound_default_character_pack_id = "cecilia"
        care = CareModulePort.from_feature(enabled=True, storage_path=Path(self.temporary.name) / "care.json")
        self.engine.get_care_module = lambda: care
        renderer = self.enterContext(
            patch("companion_v01.channels.qq.commands.card_results.render_qq_care_card", return_value=self.render.return_value)
        )
        return care.runtime, renderer

    def test_personal_status_uses_requesting_users_real_assets_and_no_inference(self) -> None:
        runtime, renderer = self._enable_care()
        runtime.claim_daily_checkin(
            profile_user_id="shared",
            character_pack_id="cecilia",
            relation_user_id="qq:10002",
            date_key="2026-10-01",
            coins=40,
        )
        runtime.donate_to_shrine(
            profile_user_id="shared", character_pack_id="cecilia", relation_user_id="qq:10002", amount=7
        )
        result = self._post("/养成", group=True, target=True)
        self.assertEqual(result["card_format"], "image")
        first = renderer.call_args.args[0]
        self.assertEqual((first.coins, first.redeem_quota), (33, 7))
        self.assertIn("qq:10002", first.user_scope)
        self.assertNotIn("care_card", result)
        self.assertFalse(any(segment["type"] == "reply" for message in self.sent for segment in message["message"]))
        result = self._post("/养成", group=True, target=True, user_id=10003)
        self.assertEqual(result["card_format"], "image")
        other = renderer.call_args.args[0]
        self.assertEqual((other.coins, other.redeem_quota), (0, 0))
        self.assertIn("qq:10003", other.user_scope)
        self.assertEqual((first.hunger, first.energy), (other.hunger, other.energy))

    def test_status_image_failure_preserves_reason_and_sends_real_text(self) -> None:
        _runtime, renderer = self._enable_care()
        renderer.side_effect = RuntimeError("care_background_unavailable")
        result = self._post("/养成")
        self.assertEqual(result["card_format"], "text_fallback")
        self.assertEqual(result["image_failure_reason"], "care_background_unavailable")
        self.assertIn("金币 0", self._text())

    def test_status_name_uses_group_card_in_groups_and_qq_nickname_in_private(self) -> None:
        _runtime, renderer = self._enable_care()
        cases = (
            (20001, {"card": "本群的小圣女", "nickname": "QQ昵称"}, "本群的小圣女"),
            (20002, {"card": "另一个群的名片", "nickname": "QQ昵称"}, "另一个群的名片"),
            (20001, {"card": "刚改过的群名片", "nickname": "QQ昵称"}, "刚改过的群名片"),
            (20001, {"card": "本群的小圣女", "nickname": "刚改过的QQ昵称"}, "本群的小圣女"),
            (20001, {"card": "", "nickname": "QQ昵称"}, "QQ昵称"),
            (0, {"nickname": "私聊昵称"}, "私聊昵称"),
            (0, {"card": "其他场景的名片", "nickname": "刚改过的私聊昵称"}, "刚改过的私聊昵称"),
            (20001, {"card": "群名片"}, "群名片"),
            (20003, {}, "QQ 10002"),
        )
        start_time = int(time.time()) - len(cases)
        for index, (group_id, sender, expected) in enumerate(cases):
            with self.subTest(group_id=group_id, expected=expected):
                event = self._event("/养成", group=bool(group_id), target=bool(group_id))
                event.update(group_id=group_id, sender={"role": "member", **sender}, time=start_time + index)
                response = self.client.post("/api/qq/napcat/event", json=event)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json().get("card_format"), "image", response.text)
                document = renderer.call_args.args[0]
                self.assertEqual(document.user_label, expected)
                self.assertEqual(document.user_scope, f"cecilia|{response.json()['session_id']}|qq:10002")

    def test_disabled_care_does_not_render_fake_personal_stats(self) -> None:
        with patch("companion_v01.channels.qq.commands.card_results.render_qq_care_card") as renderer:
            result = self._post("/养成")
        self.assertEqual(result["command_status"], "disabled")
        self.assertEqual(result["card_format"], "text")
        renderer.assert_not_called()

    def test_duplicate_status_webhook_sends_only_one_card(self) -> None:
        _runtime, renderer = self._enable_care()
        event = self._event("/养成")
        first = self.client.post("/api/qq/napcat/event", json=event)
        count = len(self.sent)
        second = self.client.post("/api/qq/napcat/event", json=event)
        self.assertEqual(first.json()["card_format"], "image")
        self.assertEqual(second.json()["reason"], "duplicate_event")
        self.assertEqual(len(self.sent), count)
        renderer.assert_called_once()


if __name__ == "__main__":
    unittest.main()
