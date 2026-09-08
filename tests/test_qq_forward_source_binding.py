from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from channelcore_onebot import OutboundActionResult
from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.qq_gateway import NapCatQQGateway
from companion_v01.qq_tool_delivery import QQToolDeliveryPort
from companion_v01.routes.qq import _qq_quoted_message_reference, _qq_structured_forward_references, build_qq_router


class QQForwardSourceBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bot_patch = patch("companion_v01.qq_gateway.config.QQ_BOT_QQ", "10001")
        self.bot_patch.start()
        self.addCleanup(self.bot_patch.stop)
        self.owner_patch = patch("companion_v01.qq_gateway.config.MASTER_QQ", "10002")
        self.owner_patch.start()
        self.addCleanup(self.owner_patch.stop)
        self.gateway = NapCatQQGateway(wake_words=("Akane",))
        self.calls = []
        self.quote_group = 20001
        self.forward_fails = False
        self.gateway._onebot_transport.call = self._call

    def _call(self, action, params, *, timeout):
        self.calls.append((action, params))
        if action == "get_msg":
            data = {
                "message_id": "quoted",
                "message_type": "group",
                "self_id": 10001,
                "group_id": self.quote_group,
                "user_id": 10004,
                "sender": {"nickname": "Bob"},
                "message": [{"type": "forward", "data": {"id": "forward-quoted"}}],
            }
        else:
            data = {
                "messages": [
                    {
                        "user_id": 10005,
                        "sender": {"nickname": "Carol"},
                        "message_id": "node-1",
                        "content": [
                            {"type": "text", "data": {"text": "节点真实正文"}},
                            {"type": "image", "data": {"file": "node.png"}},
                        ],
                    }
                ]
            }
        ok = not (action == "get_forward_msg" and self.forward_fails)
        return OutboundActionResult(
            ok,
            "success" if ok else "failed",
            "ok" if ok else "onebot_status_error",
            action,
            data=data if ok else {},
            http_status=200,
        )

    def _event(self, *, quote=True):
        return {
            "post_type": "message",
            "message_type": "group",
            "self_id": 10001,
            "user_id": 10003,
            "group_id": 20001,
            "message_id": "current",
            "sender": {"nickname": "Alice"},
            "message": [
                {"type": "reply", "data": {"id": "quoted"}}
                if quote
                else {"type": "forward", "data": {"id": "forward-direct"}},
                {"type": "text", "data": {"text": "Akane 看看这个"}},
            ],
        }

    def test_quoted_forward_expands_nodes_and_images_after_scope_validation(self) -> None:
        event = self._event()
        context = self.gateway.build_message_context(event)
        result = self.gateway.resolve_quoted_message_evidence(event, context=context)
        self.assertEqual([action for action, _params in self.calls], ["get_msg", "get_forward_msg"])
        self.assertEqual(result["forwards"][0]["nodes"][0]["text"], "节点真实正文 [图片]")
        self.assertEqual(result["forwards"][0]["source_message_id"], "quoted")
        self.assertEqual(result["attachments"][0]["forward_id"], "forward-quoted")
        self.assertEqual(result["quoted_message"]["attachment_count"], 1)

    def test_cross_group_quote_never_expands_forward(self) -> None:
        self.quote_group = 20002
        event = self._event()
        result = self.gateway.resolve_quoted_message_evidence(
            event,
            context=self.gateway.build_message_context(event),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "scope_mismatch")
        self.assertEqual([action for action, _params in self.calls], ["get_msg"])
        self.assertEqual(result["attachments"], [])

    def test_non_owner_can_retry_only_source_bound_forward_after_delivery_restore(self) -> None:
        for quote in (False, True):
            with self.subTest(quote=quote):
                # Fresh admitted context, independent of the preceding subtest.
                event = self._event(quote=quote)
                event["message_id"] += str(quote)
                context = self.gateway.build_message_context(event)
                if quote:
                    evidence = self.gateway.resolve_quoted_message_evidence(event, context=context)
                    context = replace(context, reply_reference=_qq_quoted_message_reference(evidence))
                else:
                    evidence = self.gateway.resolve_forward_message_evidence(event, context=context)
                context = replace(context, forward_references=_qq_structured_forward_references(evidence))
                restored = self.gateway.context_from_delivery_context(context.to_delivery_context())
                forward_id = "forward-quoted" if quote else "forward-direct"
                self.calls.clear()
                allowed = self.gateway.call_model_onebot_action(
                    restored,
                    action="get_forward_msg",
                    params={"message_id": forward_id},
                )
                self.assertTrue(allowed["ok"], allowed)
                self.assertEqual(len(self.calls), 1)
                delivered = QQToolDeliveryPort(self.gateway).call_onebot_action(
                    request_context={"qq_delivery_context": context.to_delivery_context()},
                    action="get_forward_msg",
                    params={"message_id": forward_id},
                )
                self.assertTrue(delivered["ok"])
                self.calls.pop()
                for candidate in ("unknown-forward", context.source_message_id, ""):
                    denied = self.gateway.call_model_onebot_action(
                        restored,
                        action="get_forward_msg",
                        params={"message_id": candidate},
                    )
                    self.assertFalse(denied["ok"])
                    self.assertEqual(denied["reason"], "forward_source_unverified")
                self.assertEqual(len(self.calls), 1)
                wrong_scope = replace(restored, group_id=20002, target_id=20002)
                denied = self.gateway.call_model_onebot_action(
                    wrong_scope,
                    action="get_forward_msg",
                    params={"message_id": forward_id},
                )
                self.assertFalse(denied["ok"])
                self.assertEqual(len(self.calls), 1)

    def test_owner_read_does_not_grant_other_actors_or_global_discovery(self) -> None:
        context = self.gateway.build_message_context(self._event())
        owner = replace(context, user_id=10002)
        self.calls.clear()
        allowed = self.gateway.call_model_onebot_action(
            owner,
            action="get_forward_msg",
            params={"message_id": "owner-known-forward"},
        )
        self.assertTrue(allowed["ok"])
        for action, params in (
            ("get_forward_msg", {"message_id": "owner-known-forward", "verified_forward_ids": ["owner-known-forward"]}),
            ("get_group_list", {}),
            ("get_friend_list", {}),
            ("delete_msg", {"message_id": context.source_message_id}),
            ("send_group_msg", {"group_id": 20002, "message": "not sent"}),
        ):
            denied = self.gateway.call_model_onebot_action(context, action=action, params=params)
            self.assertFalse(denied["ok"], action)
        self.assertEqual(len(self.calls), 1)

    def test_failed_forward_lookup_keeps_real_source_and_failure_not_fake_nodes(self) -> None:
        self.forward_fails = True
        event = self._event()
        result = self.gateway.resolve_quoted_message_evidence(event, context=self.gateway.build_message_context(event))
        self.assertTrue(result["ok"])  # The enclosing quote itself was resolved.
        forward = result["forwards"][0]
        self.assertFalse(forward["ok"])
        self.assertEqual(forward["nodes"], [])
        self.assertEqual(forward["status"], "lookup_rejected")
        self.assertEqual(result["attachments"], [])

    def test_quoted_forward_route_delivers_nodes_pixels_and_source_proof_to_turn(self) -> None:
        processed = []
        ingested = []
        prepared = []

        class Engine:
            desktop_pet_character_resources = None

            def ingest_qq_attachments(self, **kwargs):
                ingested.extend(kwargs["attachments"])
                return [
                    {"attachment_id": "node-image", "attachment_handle": "img_001", "kind": "image", "status": "ready"}
                ]

            def prepare_qq_native_image_inputs(self, **kwargs):
                prepared.append(kwargs["attachment_ids"])
                return {
                    "ok": True,
                    "status": "ready",
                    "images": [
                        {
                            "attachment_id": "node-image",
                            "attachment_handle": "img_001",
                            "data_url": "data:image/png;base64,dGVzdC1waXhlbHM=",
                        }
                    ],
                }

            def prefetch_remote_media_links_for_message(self, **_kwargs):
                return {}

            def process_turn_stream(self, payload):
                processed.append(dict(payload))
                yield {"type": "final_ui", "payload": {"_deliberate_silence": True, "speech": "", "tool_events": []}}

        app = FastAPI()
        app.include_router(
            build_qq_router(
                engine=Engine(),
                qq_gateway=self.gateway,
                config_module=SimpleNamespace(QQ_BRIDGE_ENABLED=True, QQ_GROUP_ATTENTION_MODE="off"),
                runtime_metrics=SimpleNamespace(observe_request=lambda *_args, **_kwargs: None),
                logger=SimpleNamespace(exception=lambda *_args, **_kwargs: None),
                log_event=lambda *_args, **_kwargs: None,
            )
        )
        event = self._event()
        event["message"].append({"type": "at", "data": {"qq": "10001"}})
        response = TestClient(app).post("/api/qq/napcat/event", json=event)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(processed), 1, response.json())
        payload = processed[0]
        self.assertIn("节点真实正文", payload["forward_references"][0]["nodes"][0]["text"])
        self.assertEqual(payload["message_addressing"]["reply_reference"]["message_id"], "quoted")
        self.assertEqual(payload["qq_current_attachment_ids"], ["node-image"])
        self.assertEqual(prepared, [["node-image"]])
        self.assertEqual(ingested[0]["forward_id"], "forward-quoted")
        self.assertEqual(payload["native_user_images"][0]["attachment_id"], "node-image")
        restored = self.gateway.context_from_delivery_context(payload["qq_delivery_context"])
        self.assertEqual(restored.verified_forward_ids, ("forward-quoted",))
        self.assertNotIn("nodes", payload["qq_delivery_context"]["forward_references"][0])


if __name__ == "__main__":
    unittest.main()
