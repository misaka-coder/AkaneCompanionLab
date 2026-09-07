"""Miniapp generation through the real Skill, handler, port and HTTP adapter."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import requests

from companion_v01.capability_registry import CapabilityRegistry, CapabilitySnapshot
from companion_v01.client_protocol import ClientMode
from companion_v01.deployment_security import QQChannelRuntimeConfig
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.onebot_transport import OneBotActionTransport
from companion_v01.qq_gateway import NapCatQQGateway
from companion_v01.qq_miniapp import project_miniapp_result, validate_miniapp_params
from companion_v01.qq_tool_delivery import QQToolDeliveryPort
from companion_v01.skill_runtime import SkillRegistry
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.qq_onebot import OneBotActionToolHandler
from companion_v01.tool_handlers.skills import LoadSkillToolHandler


ROOT = Path(__file__).resolve().parents[1]
PARAMS = {
    "type": "bili",
    "title": "测试视频",
    "desc": "",
    "picUrl": "https://i0.hdslb.com/bfs/archive/fixture.jpg?sign=keep-me",
    "jumpUrl": "pages/video/video.html?avid=170001",
    "webUrl": "https://www.bilibili.com/video/av170001/",
}
ARK = {
    "app": "com.tencent.miniapp_01",
    "view": "view_8C8E89B49BE609866298ADDFF2DBABA4",
    "ver": "1.0.0.1",
    "prompt": "[分享]测试视频",
    "config": {"forward": 0},
    "meta": {
        "detail_1": {
            "title": "哔哩哔哩",
            "desc": "测试视频",
            "qqdocurl": PARAMS["webUrl"],
            "qqsign": "fixture-signature",
        }
    },
}


class _Response:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


class _Session:
    """Only the external HTTP boundary is replaced. No model or delivery stub."""

    def __init__(self):
        self.calls = []
        self.response = {"status": "ok", "retcode": 0, "data": {"data": ARK}}

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return _Response(self.response)


class MiniappWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.session = _Session()
        channel = QQChannelRuntimeConfig(
            enabled=True,
            profile_ref="fixture",
            bot_id="10000",
            onebot_http_url="http://127.0.0.1:3333",
            webhook_secret="",
            onebot_access_token="fixture-token",
            require_webhook_auth=False,
            require_self_id=True,
        )
        self.gateway = object.__new__(NapCatQQGateway)
        self.gateway._onebot_transport = OneBotActionTransport(channel, session=self.session)
        self.handler = OneBotActionToolHandler(delivery_port=QQToolDeliveryPort(self.gateway))
        self.context = ToolExecutionContext(
            profile_user_id="qq:10002",
            session_id="qq:group:20001",
            now_ts=1,
            visual_payload={},
            client_mode="qq_text",
            request_context={
                "qq_delivery_context": {
                    "is_group": True,
                    "target_id": 20001,
                    "group_id": 20001,
                    "user_id": 10002,
                    "actor_profile_user_id": "qq:10002",
                    "source_message_id": "30001",
                }
            },
        )
        owner = patch("companion_v01.qq_gateway.config.MASTER_QQ", "10001")
        owner.start()
        self.addCleanup(owner.stop)

    def call(self, action="get_mini_app_ark", params=None, **kwargs):
        call = self.handler.normalize_call(
            {
                "type": "onebot_action",
                "action": action,
                "params": dict(PARAMS if params is None else params),
                **kwargs,
            }
        )
        self.assertIsNotNone(call)
        result = self.handler.execute(call=call, context=self.context)
        text = result.followup_context.removeprefix("<tool_use_error>").removesuffix("</tool_use_error>")
        return result, json.loads(text)

    def test_skill_discovery_load_generate_send_with_real_adapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = SkillRegistry(
                bundled_root=ROOT / "skills",
                managed_root=Path(tmp) / "managed",
                execution_workspace_root=Path(tmp) / "workspace",
            )
            selection = CapabilityRegistry().select(CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT))
            catalog = registry.prompt_catalog(available_tool_names=selection.tool_names)
            self.assertIn("qq-miniapp-share", catalog)
            self.assertNotIn("rawArkData", catalog)  # Body is progressive, not stable prompt text.
            self.assertNotIn("qq-miniapp-share", registry.prompt_catalog(available_tool_names=["load_skill"]))
            loaded = LoadSkillToolHandler(registry=registry).execute(
                call={"type": "load_skill", "name": "qq-miniapp-share"}, context=self.context
            )
            self.assertEqual(loaded.stream_events[0]["status"], "loaded")
            self.assertIn("references/custom.md", loaded.followup_context)
            self.assertEqual(registry.load("qq-miniapp-share", resource="references/custom.md").status, "loaded")

        schema = build_openai_native_tool_specs({"onebot_action": self.handler})
        self.assertIn("get_mini_app_ark", schema[0]["function"]["parameters"]["properties"]["action"]["enum"])
        generated, data = self.call(finish_turn=True)
        self.assertTrue(data["ok"])
        self.assertEqual(data["stage"], "generated")
        self.assertEqual(data["scope"], "current_conversation_generation")
        self.assertEqual(generated.stream_events, [])
        self.assertFalse(generated.finish_turn)
        self.assertEqual(json.loads(data["message"][0]["data"]["data"]), ARK)
        self.assertEqual(data["data"]["data"], ARK)
        self.assertEqual(self.session.calls[0][2]["json"], PARAMS)
        self.assertEqual(self.session.calls[0][0], "POST")
        self.assertFalse(self.session.calls[0][2]["allow_redirects"])
        self.assertNotIn("fixture-token", generated.followup_context)
        self.assertNotIn("127.0.0.1", generated.followup_context)
        self.assertEqual(len(self.session.calls), 1)  # No auto send.

        self.session.response = {"status": "ok", "retcode": 0, "data": {"message_id": 77}}
        sent, receipt = self.call("send_group_msg", {"message": data["message"]}, finish_turn=True)
        self.assertTrue(sent.finish_turn)
        self.assertEqual(receipt["data"]["message_id"], 77)
        self.assertEqual(sent.stream_events[0]["type"], "qq_visible_action_receipt")
        self.assertEqual(self.session.calls[1][2]["json"], {"group_id": 20001, "message": data["message"]})

    def test_weibo_and_custom_use_same_endpoint_without_rewriting(self):
        custom = {key: value for key, value in PARAMS.items() if key != "type"}
        custom.update(
            {
                "iconUrl": "https://example.com/icon.png",
                "appId": "100000",
                "scene": "1",
                "templateType": "1",
                "businessType": "0",
                "verType": "3",
                "shareType": "0",
                "versionId": "fixture-version",
                "sdkId": "fixture-sdk",
                "withShareTicket": "0",
            }
        )
        for params in ({**PARAMS, "type": "weibo"}, custom):
            with self.subTest(template=params.get("type", "custom")):
                _, result = self.call(params=params)
                self.assertTrue(result["ok"])
                self.assertEqual(self.session.calls[-1][2]["json"], params)
                self.assertTrue(self.session.calls[-1][1].endswith("/get_mini_app_ark"))

    def test_generation_does_not_grant_cross_conversation_send(self):
        _, generated = self.call()
        for action, target in (("send_group_msg", {"group_id": 29999}), ("send_private_msg", {"user_id": 10003})):
            _, result = self.call(action, {**target, "message": generated["message"]})
            self.assertEqual(result["status"], "forbidden")
        self.assertEqual(len(self.session.calls), 1)

    def test_private_conversation_generation_and_current_peer_send(self):
        self.context.request_context["qq_delivery_context"].update(is_group=False, target_id=10002, group_id=0)
        _, generated = self.call()
        self.session.response = {"status": "ok", "retcode": 0, "data": {"message_id": 78}}
        _, result = self.call("send_private_msg", {"message": generated["message"]})
        self.assertTrue(result["ok"])
        self.assertEqual(self.session.calls[-1][2]["json"]["user_id"], 10002)

    def test_owner_still_can_explicitly_send_to_other_group(self):
        self.context.request_context["qq_delivery_context"]["user_id"] = 10001
        _, generated = self.call()
        self.session.response = {"status": "ok", "retcode": 0, "data": {"message_id": 79}}
        _, result = self.call("send_group_msg", {"group_id": 29999, "message": generated["message"]})
        self.assertTrue(result["ok"])
        self.assertEqual(result["scope"], "owner")

    def test_non_qq_and_missing_context_do_not_call_transport(self):
        self.context = replace(self.context, client_mode="desktop_pet")
        _, result = self.call()
        self.assertEqual(result["reason"], "qq_only")
        self.context = replace(self.context, client_mode="qq_text", request_context={})
        _, result = self.call()
        self.assertEqual(result["reason"], "qq_delivery_context_missing")
        self.assertEqual(self.session.calls, [])

    def test_invalid_parameters_stop_before_transport(self):
        cases = [
            ({**PARAMS, "type": "unknown"}, "miniapp_template_unsupported"),
            ({**PARAMS, "appId": "1"}, "miniapp_unknown_fields"),
            ({**PARAMS, "group_id": 9}, "miniapp_unknown_fields"),
            ({**PARAMS, "title": 123}, "miniapp_string_required"),
            ({**PARAMS, "title": " "}, "miniapp_field_empty"),
            ({**PARAMS, "desc": "a" * 4097}, "miniapp_field_invalid"),
            ({**PARAMS, "rawArkData": "yes"}, "miniapp_raw_flag_invalid"),
            ({**PARAMS, "rawArkData": True}, "miniapp_string_required"),
            ({key: value for key, value in PARAMS.items() if key != "title"}, "miniapp_missing_fields"),
            ({key: value for key, value in PARAMS.items() if key != "type"}, "miniapp_missing_fields"),
        ]
        for params, reason in cases:
            with self.subTest(reason=reason, keys=list(params)):
                _, result = self.call(params=params)
                self.assertEqual(result["status"], "invalid")
                self.assertEqual(result["reason"], reason)
        self.assertEqual(self.session.calls, [])

    def test_failure_timeout_and_bad_success_do_not_create_visible_receipt(self):
        failures = [
            (requests.Timeout("sensitive transport detail"), "timeout"),
            (
                {"status": "failed", "retcode": 1, "message": "packetBackend unavailable secret detail"},
                "onebot_status_error",
            ),
            ({"status": "ok", "retcode": 0, "data": {"data": {}}}, "miniapp_ark_invalid"),
        ]
        for response, code in failures:
            with self.subTest(code=code):
                self.session.response = response
                result, data = self.call(finish_turn=True)
                self.assertFalse(data["ok"])
                self.assertEqual(data["code"], code)
                self.assertNotIn("message", data)
                self.assertEqual(result.stream_events, [])
                self.assertFalse(result.finish_turn)
                self.assertNotIn("secret detail", result.followup_context)
                self.assertNotIn("sensitive transport", result.followup_context)
        self.assertEqual(len(self.session.calls), 3)  # No hidden retries or link fallback.

    def test_send_failure_after_generation_does_not_finish_or_auto_fallback(self):
        _, generated = self.call()
        self.session.response = {"status": "failed", "retcode": 100, "data": {}}
        result, data = self.call("send_group_msg", {"message": generated["message"]}, finish_turn=True)
        self.assertFalse(data["ok"])
        self.assertFalse(result.finish_turn)
        self.assertEqual(result.stream_events, [])
        self.assertEqual(len(self.session.calls), 2)

    def test_raw_ark_is_preserved_but_not_claimed_send_ready(self):
        raw = {"appName": ARK["app"], "appView": ARK["view"], "metaData": ARK["meta"], "extra": "keep"}
        self.session.response = {"status": "ok", "retcode": 0, "data": {"data": raw}}
        result, data = self.call(params={**PARAMS, "rawArkData": "true"}, finish_turn=True)
        self.assertTrue(data["ok"])
        self.assertTrue(data["raw_ark"])
        self.assertEqual(data["data"]["data"], raw)
        self.assertNotIn("message", data)
        self.assertFalse(result.finish_turn)


class MiniappValidationTests(unittest.TestCase):
    def test_custom_numeric_fields_reject_guesses_and_non_string_values(self):
        params = {key: value for key, value in PARAMS.items() if key != "type"}
        params.update(
            {
                "iconUrl": "https://example.com/icon.png",
                "appId": "100000",
                "scene": "1",
                "templateType": "1",
                "businessType": "0",
                "verType": "3",
                "shareType": "0",
                "versionId": "fixture-version",
                "sdkId": "fixture-sdk",
                "withShareTicket": "0",
            }
        )
        self.assertEqual(validate_miniapp_params(params), "")
        for value in ("NaN", "-1", "1.5", "１２", "1" * 17):
            self.assertEqual(validate_miniapp_params({**params, "scene": value}), "miniapp_numeric_string_required")
        self.assertEqual(validate_miniapp_params({**params, "scene": 1}), "miniapp_string_required")

    def test_urls_are_not_rewritten_and_local_or_credential_urls_are_rejected(self):
        self.assertEqual(validate_miniapp_params(PARAMS), "")
        for url in (
            "file:///tmp/pic.jpg",
            "https://a:b@example.com/pic",
            "http://127.0.0.1/pic",
            "http://[::1]/pic",
            "http://localhost/pic",
            "https://example.com:bad/pic",
            "https://@example.com/pic",
            "https://example.com:0/pic",
            "http://224.0.0.1/pic",
            "http://198.18.0.119/pic",
        ):
            with self.subTest(url=url):
                self.assertEqual(validate_miniapp_params({**PARAMS, "picUrl": url}), "miniapp_public_url_required")
        for path in ("C:/private/file", "../private", "javascript:alert(1)", "//localhost/path", "x\ny"):
            self.assertEqual(validate_miniapp_params({**PARAMS, "jumpUrl": path}), "miniapp_jump_invalid")

    def test_serialized_ark_is_kept_exactly_and_unexpected_output_fails(self):
        original = json.dumps(ARK, ensure_ascii=False, indent=2)
        for ark in (original, {"ark": original}):
            result = project_miniapp_result({"ok": True, "data": {"data": ark}})
            self.assertEqual(result["message"][0]["data"]["data"], original)
        for ark in (None, "broken", {}, [], {"app": "x"}, {**ARK, "padding": "x" * (256 * 1024)}):
            self.assertFalse(project_miniapp_result({"ok": True, "data": {"data": ark}})["ok"])


if __name__ == "__main__":
    unittest.main()
