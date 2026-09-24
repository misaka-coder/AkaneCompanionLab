from __future__ import annotations

import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from channelcore_onebot import OutboundActionResult

from companion_v01.capability_registry import CapabilityRegistry, CapabilitySnapshot
from companion_v01.client_protocol import ClientMode
from companion_v01.onebot_model_actions import (
    MODEL_ONEBOT_ACTION_NAMES,
    authorize_model_onebot_action,
    model_onebot_capabilities,
)
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.qq_gateway import NapCatQQGateway, QQMessageContext
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.qq_onebot import OneBotActionToolHandler


def _tool_context(*, request_context=None) -> ToolExecutionContext:
    return ToolExecutionContext(
        profile_user_id="qq:10001",
        session_id="qq:group:20001",
        now_ts=1,
        visual_payload={},
        client_mode="qq_text",
        request_context=dict(request_context or {"qq_delivery_context": {"target_id": 20001}}),
    )


class _Port:
    def __init__(self, payload=None) -> None:
        self.payload = payload or {"ok": True, "status": "success", "action": "group_poke"}
        self.calls = []

    def call_onebot_action(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.payload)


class _Transport:
    def __init__(self) -> None:
        self.calls = []

    def call(self, action, params, *, timeout):
        self.calls.append((action, params, timeout))
        return OutboundActionResult(
            True,
            "success",
            "ok",
            action,
            data={"message_id": 77},
            http_status=200,
        )


class OneBotActionContractTests(unittest.TestCase):
    def test_manifest_exposes_useful_actions_but_not_secrets_or_admin(self) -> None:
        manifest = model_onebot_capabilities()
        names = {item["action"] for item in manifest["actions"]}

        self.assertIn("delete_msg", names)
        self.assertIn("send_group_forward_msg", names)
        self.assertIn("set_msg_emoji_like", names)
        self.assertIn("get_group_msg_history", names)
        self.assertNotIn("get_cookies", names)
        self.assertNotIn("get_clientkey", names)
        self.assertNotIn("set_group_ban", names)
        self.assertEqual(MODEL_ONEBOT_ACTION_NAMES[0], "capabilities")
        forward = next(item for item in manifest["actions"] if item["action"] == "get_forward_msg")
        self.assertEqual(forward["params"], "message_id=forward_id/res_id")
        self.assertIn("不是普通 QQ message_id", forward["summary"])

    def test_non_owner_same_group_is_allowed_but_cross_group_and_recall_are_explicitly_denied(self) -> None:
        current = authorize_model_onebot_action(
            "group_poke",
            {"group_id": 20001, "user_id": 10002},
            is_master=False,
            is_group=True,
            group_id=20001,
            user_id=10001,
            source_message_id="30001",
        )
        cross = authorize_model_onebot_action(
            "send_group_msg",
            {"group_id": 29999, "message": "hello"},
            is_master=False,
            is_group=True,
            group_id=20001,
            user_id=10001,
            source_message_id="30001",
        )
        recall = authorize_model_onebot_action(
            "delete_msg",
            {"message_id": 30001},
            is_master=False,
            is_group=True,
            group_id=20001,
            user_id=10001,
            source_message_id="30001",
        )

        self.assertEqual(current, (True, "", "current_group"))
        self.assertEqual(cross, (False, "cross_conversation_requires_owner", "current_group"))
        self.assertEqual(recall, (False, "owner_required", "owner_only"))

    def test_owner_can_recall_and_cross_conversation_through_gateway_without_credentials(self) -> None:
        gateway = object.__new__(NapCatQQGateway)
        gateway._onebot_transport = _Transport()
        context = QQMessageContext(
            should_respond=True,
            reason="mention",
            is_group=True,
            target_id=20001,
            user_id=10001,
            group_id=20001,
            source_message_id="30001",
        )

        with patch("companion_v01.qq_gateway.config.MASTER_QQ", "10001"):
            result = gateway.call_model_onebot_action(
                context,
                action="delete_msg",
                params={"message_id": 30001},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["scope"], "owner")
        self.assertEqual(gateway._onebot_transport.calls[0][0], "delete_msg")
        self.assertNotIn("token", json.dumps(result))

    def test_gateway_fills_unambiguous_current_group_and_sender_targets(self) -> None:
        gateway = object.__new__(NapCatQQGateway)
        gateway._onebot_transport = _Transport()
        context = QQMessageContext(
            should_respond=True,
            reason="mention",
            is_group=True,
            target_id=20001,
            user_id=10002,
            group_id=20001,
            source_message_id="30001",
        )

        with patch("companion_v01.qq_gateway.config.MASTER_QQ", "10001"):
            result = gateway.call_model_onebot_action(context, action="group_poke", params={})

        self.assertTrue(result["ok"])
        self.assertEqual(result["scope"], "current_group")
        self.assertEqual(result["defaults_applied"], ["current_group", "current_sender"])
        self.assertEqual(
            gateway._onebot_transport.calls[0][1],
            {"group_id": 20001, "user_id": 10002},
        )

    def test_gateway_resolves_current_and_replied_message_selectors_to_real_ids(self) -> None:
        gateway = object.__new__(NapCatQQGateway)
        gateway._onebot_transport = _Transport()
        context = QQMessageContext(
            should_respond=True,
            reason="mention",
            is_group=True,
            target_id=20001,
            user_id=10001,
            group_id=20001,
            source_message_id="30001",
            reply_reference={"message_id": "29999"},
        )

        with patch("companion_v01.qq_gateway.config.MASTER_QQ", "10001"):
            current = gateway.call_model_onebot_action(
                context,
                action="set_msg_emoji_like",
                params={"emoji_id": 66},
                message_selector={"kind": "current_message"},
            )
            replied = gateway.call_model_onebot_action(
                context,
                action="delete_msg",
                params={},
                message_selector={"kind": "replied_message"},
            )

        self.assertTrue(current["ok"])
        self.assertTrue(replied["ok"])
        self.assertEqual(gateway._onebot_transport.calls[0][1]["message_id"], "30001")
        self.assertEqual(gateway._onebot_transport.calls[1][1]["message_id"], "29999")
        self.assertEqual(current["message_selector_applied"], "current_message")
        self.assertEqual(replied["message_selector_applied"], "replied_message")

    def test_recent_bot_message_selector_uses_only_successful_outbound_receipts(self) -> None:
        gateway = NapCatQQGateway()
        gateway._onebot_transport = _Transport()
        context = QQMessageContext(
            should_respond=True,
            reason="mention",
            is_group=True,
            target_id=20001,
            user_id=10001,
            group_id=20001,
            source_message_id="30001",
        )

        sent = gateway.send_reply(context, "第一条")
        self.assertTrue(sent["ok"])
        with patch("companion_v01.qq_gateway.config.MASTER_QQ", "10001"):
            result = gateway.call_model_onebot_action(
                context,
                action="delete_msg",
                params={},
                message_selector={"kind": "recent_bot_message", "position": 1},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(gateway._onebot_transport.calls[-1][1]["message_id"], "77")
        self.assertEqual(result["message_selector_applied"], "recent_bot_message")

    def test_unavailable_message_selector_returns_structured_feedback_without_transport_call(self) -> None:
        gateway = NapCatQQGateway()
        gateway._onebot_transport = _Transport()
        context = QQMessageContext(
            should_respond=True,
            reason="mention",
            is_group=True,
            target_id=20001,
            user_id=10001,
            group_id=20001,
            source_message_id="30001",
        )

        with patch("companion_v01.qq_gateway.config.MASTER_QQ", "10001"):
            result = gateway.call_model_onebot_action(
                context,
                action="delete_msg",
                params={},
                message_selector={"kind": "replied_message"},
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "replied_message_unavailable")
        self.assertEqual(gateway._onebot_transport.calls, [])

    def test_non_owner_can_react_to_host_resolved_replied_message_in_current_conversation(self) -> None:
        gateway = NapCatQQGateway()
        gateway._onebot_transport = _Transport()
        context = QQMessageContext(
            should_respond=True,
            reason="mention",
            is_group=True,
            target_id=20001,
            user_id=10002,
            group_id=20001,
            source_message_id="30001",
            reply_reference={"message_id": "29999"},
        )

        with patch("companion_v01.qq_gateway.config.MASTER_QQ", "10001"):
            result = gateway.call_model_onebot_action(
                context,
                action="set_msg_emoji_like",
                params={"emoji_id": 66},
                message_selector={"kind": "replied_message"},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["scope"], "current_conversation_message")
        self.assertEqual(gateway._onebot_transport.calls[-1][1]["message_id"], "29999")

    def test_handler_preserves_large_complete_result_for_memcore_settlement(self) -> None:
        large_text = "结果" * 50_000
        port = _Port(
            {
                "ok": True,
                "status": "success",
                "action": "get_group_msg_history",
                "data": {"messages": [{"message": large_text}]},
            }
        )
        handler = OneBotActionToolHandler(delivery_port=port)
        call = handler.normalize_call(
            {
                "type": "onebot_action",
                "action": "get_group_msg_history",
                "params": {"group_id": 20001, "count": 100},
            }
        )
        self.assertIsNotNone(call)

        result = handler.execute(call=call or {}, context=_tool_context())

        self.assertIn(large_text, result.followup_context)
        self.assertIsNotNone(result.followup_envelope)
        self.assertTrue(result.followup_envelope.complete)
        self.assertFalse(result.followup_envelope.producer_bounded)
        self.assertEqual(result.followup_envelope.content, result.followup_context)

    def test_handler_marks_real_onebot_failure_without_emitting_public_progress(self) -> None:
        port = _Port(
            {
                "ok": False,
                "status": "failed",
                "reason": "onebot_retcode_error",
                "action": "group_poke",
            }
        )
        handler = OneBotActionToolHandler(delivery_port=port)

        result = handler.execute(
            call={"type": "onebot_action", "action": "group_poke", "params": {"group_id": 20001}},
            context=_tool_context(),
        )

        self.assertIn("<tool_use_error>", result.followup_context)
        self.assertIn("onebot_retcode_error", result.followup_context)
        self.assertEqual(result.stream_events, [])

    def test_successful_write_emits_visible_delivery_receipt_but_read_does_not(self) -> None:
        write_handler = OneBotActionToolHandler(
            delivery_port=_Port(
                {
                    "ok": True,
                    "status": "success",
                    "action": "send_group_msg",
                    "data": {"message_id": 77},
                }
            )
        )
        write_result = write_handler.execute(
            call={"type": "onebot_action", "action": "send_group_msg", "params": {"message": "你好"}},
            context=_tool_context(),
        )
        self.assertEqual(
            write_result.stream_events,
            [
                {
                    "type": "qq_visible_action_receipt",
                    "action": "send_group_msg",
                    "status": "success",
                    "ok": True,
                    "message_id": 77,
                }
            ],
        )

        read_handler = OneBotActionToolHandler(
            delivery_port=_Port(
                {
                    "ok": True,
                    "status": "success",
                    "action": "get_group_msg_history",
                    "data": {"messages": []},
                }
            )
        )
        read_result = read_handler.execute(
            call={"type": "onebot_action", "action": "get_group_msg_history", "params": {}},
            context=_tool_context(),
        )
        self.assertEqual(read_result.stream_events, [])

    def test_independent_native_actions_are_handler_safe_in_parallel(self) -> None:
        port = _Port()
        handler = OneBotActionToolHandler(delivery_port=port)
        calls = [
            {"type": "onebot_action", "action": "group_poke", "params": {"group_id": 20001, "user_id": value}}
            for value in (10002, 10003)
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda call: handler.execute(call=call, context=_tool_context()), calls))

        self.assertEqual(len(port.calls), 2)
        self.assertTrue(all('"ok":true' in result.followup_context for result in results))

    def test_qq_capability_selection_and_native_spec_include_onebot_action(self) -> None:
        selection = CapabilityRegistry().select(CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT))
        handler = OneBotActionToolHandler()

        self.assertIn("onebot_action", selection.tool_names)
        self.assertIn("onebot_action", selection.schema_tool_names)
        self.assertEqual(handler.tool_spec().capability_id, "onebot_action")
        self.assertEqual(handler.tool_spec().input_schema["properties"]["action"]["enum"], list(MODEL_ONEBOT_ACTION_NAMES))
        first = build_openai_native_tool_specs({"onebot_action": handler})
        second = build_openai_native_tool_specs({"onebot_action": handler})
        self.assertEqual(first, second)
        self.assertEqual(first[0]["function"]["name"], "onebot_action")
        self.assertEqual(
            first[0]["function"]["parameters"]["properties"]["action"]["enum"],
            list(MODEL_ONEBOT_ACTION_NAMES),
        )
        selector = first[0]["function"]["parameters"]["properties"]["message_selector"]
        self.assertEqual(selector["properties"]["position"]["minimum"], 1)


if __name__ == "__main__":
    unittest.main()
