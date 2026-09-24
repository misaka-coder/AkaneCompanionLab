"""Real protocol/ingest/route paths; only provider bytes, vision and sends are stubbed."""

from __future__ import annotations

import base64
import io
import json
import logging
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from channelcore_onebot import OutboundActionResult, parse_message_chain
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.attachment_ingest import AttachmentIngestService
from companion_v01.onebot_model_actions import model_onebot_capabilities
from companion_v01.qq_gateway import NapCatQQGateway
from companion_v01.qq_tool_delivery import QQToolDeliveryPort
from companion_v01.routes.qq import build_qq_router
from companion_v01.store import MemoryStore
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.qq_onebot import OneBotActionToolHandler
from companion_v01.vision_service import VisionObservationService
from tests.test_attachment_ingest import FakeVisionService


def image_bytes(fmt="PNG"):
    output = io.BytesIO()
    Image.new("RGB", (12, 8), (30, 90, 170)).save(output, format=fmt)
    return output.getvalue()


def card_segment(kind="json"):
    if kind == "xml":
        raw = '<msg url="https://example.org/video"><item><title>原始标题</title><summary>卡片说明</summary><extra value="保留字段"/></item></msg>'
    else:
        raw = json.dumps(
            {
                "app": "com.tencent.miniapp_01",
                "meta": {
                    "detail_1": {
                        "title": "原始标题",
                        "desc": "卡片说明",
                        "qqdocurl": "https://example.org/video",
                        "extra": "保留字段",
                        "musicUrl": "https://example.org/audio.mp3",
                    }
                },
            },
            ensure_ascii=False,
        )
    return {"type": kind, "data": {"data": raw}}


class InlineRunner:
    def submit(self, *, fn, args, **kwargs):
        fn(*args)


class RichMaterialTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = MemoryStore(self.root / "db")
        self.inbox = AttachmentInboxService(store=self.store, base_dir=self.root / "attachments")
        self.vision = FakeVisionService(self.store)
        self.ingest = AttachmentIngestService(
            base_dir=self.root / "attachments",
            store=self.store,
            attachment_service=self.inbox,
            vision_service=self.vision,
            background_tasks=InlineRunner(),
        )
        self.file_bytes = image_bytes()
        self.file_calls = []

        def read_file(action, params, **kwargs):
            self.file_calls.append((action, params))
            return SimpleNamespace(ok=True, data={"base64": base64.b64encode(self.file_bytes).decode("ascii")})

        self.ingest._onebot_transport.call = read_file
        for target, value in (("QQ_BOT_QQ", "101"), ("MASTER_QQ", "909"), ("QQ_ATTACHMENT_DEBOUNCE_SECONDS", 0)):
            started = patch("companion_v01.qq_gateway.config." + target, value)
            started.start()
            self.addCleanup(started.stop)
        nickname = patch.object(NapCatQQGateway, "_resolve_bot_nickname", return_value="助手")
        nickname.start()
        self.addCleanup(nickname.stop)
        self.gateway = NapCatQQGateway(wake_words=("助手",))
        self.raw_segments = [card_segment()]
        self.gateway._onebot_transport.call = self._onebot

    def _onebot(self, action, params, **kwargs):
        action = action.lstrip("/")
        if action == "get_msg":
            data = {
                "message_id": str(params["message_id"]),
                "message_type": "group",
                "group_id": 303,
                "user_id": 404,
                "self_id": 101,
                "sender": {"nickname": "原作者"},
                "message": self.raw_segments,
            }
        elif action == "get_forward_msg":
            data = {
                "messages": [
                    {
                        "user_id": 404,
                        "sender": {"nickname": "原作者"},
                        "message_id": "606",
                        "content": self.raw_segments,
                    }
                ]
            }
        elif action in {"get_group_member_info", "get_stranger_info"}:
            data = {"nickname": "成员", "user_id": params.get("user_id")}
        elif action in {"send_group_msg", "send_private_msg"}:
            data = {"message_id": "707"}
        else:
            raise AssertionError("unexpected OneBot action: " + action)
        return OutboundActionResult(True, "success", "ok", action, data=data, http_status=200)

    def event(self, segments):
        return dict(
            post_type="message",
            message_type="group",
            self_id=101,
            user_id=202,
            group_id=303,
            message_id="505",
            time=int(time.time()),
            sender={"nickname": "发送者"},
            message=segments,
        )

    def file_segment(self, name="photo.bin", mime="application/octet-stream"):
        return {
            "type": "file",
            "data": {
                "file_id": "opaque-file-id",
                "file": "wrong-display-name",
                "name": name,
                "mime_type": mime,
                "file_size": len(self.file_bytes),
            },
        }

    def _ingest_file(self, *, observe=True, name="photo.bin", mime="application/octet-stream"):
        attachments = self.gateway._legacy_attachments(
            parse_message_chain(self.event([self.file_segment(name, mime)])).attachments
        )
        original = self.ingest.ingest_qq_attachments(
            profile_user_id="master", session_id="qq_group_shared_303", attachments=attachments, observe_images=observe
        )[0]
        latest = self.store.get_attachment_inbox_item(
            profile_user_id="master", session_id="qq_group_shared_303", attachment_id=original["attachment_id"]
        )
        return original, latest

    def test_file_id_real_bytes_promote_and_keep_identity_provenance(self):
        original, latest = self._ingest_file()
        self.assertEqual(self.file_calls, [("/get_file", {"file": "opaque-file-id"})])
        self.assertEqual(latest["kind"], "image")
        self.assertEqual(latest["mime_type"], "image/png")
        self.assertEqual(latest["attachment_handle"], original["attachment_handle"])
        resolved = self.inbox.resolve_attachment(
            profile_user_id="master",
            session_id="qq_group_shared_303",
            target=original["attachment_handle"],
            kind="image",
        )
        self.assertEqual(resolved["attachment_id"], original["attachment_id"])
        self.assertEqual(latest["source_message_id"], "505")
        self.assertEqual(latest["detail"]["qq_source_segment_type"], "file")
        self.assertEqual(len(self.vision.scheduled), 1)
        pixels = self.inbox.build_native_image_inputs(
            profile_user_id="master",
            session_id="qq_group_shared_303",
            attachment_ids=[original["attachment_id"]],
            timeout_seconds=0,
        )
        self.assertEqual(pixels["image_count"], 1)
        self.assertTrue(pixels["images"][0]["data_url"].startswith("data:image/png;base64,"))
        self.assertEqual(base64.b64decode(pixels["images"][0]["data_url"].split(",")[1]), self.file_bytes)

    def test_claimed_mime_or_extension_does_not_prove_image_content(self):
        self.file_bytes = b"ordinary text, not an image"
        _, latest = self._ingest_file(name="fake.png", mime="image/png")
        self.assertEqual(latest["kind"], "document")
        self.assertEqual(self.vision.scheduled, [])

    def test_truncated_image_and_pixel_limit_are_structured_failures(self):
        self.file_bytes = b"\x89PNG\r\n\x1a\ncorrupt"
        _, latest = self._ingest_file()
        self.assertEqual(latest["status"], "failed")
        self.assertEqual(latest["error_message"], "image_content_invalid")
        self.assertEqual(self.vision.scheduled, [])
        self.file_bytes = image_bytes()
        with patch("companion_v01.image_materials.MAX_FILE_IMAGE_PIXELS", 1):
            _, latest = self._ingest_file()
        self.assertEqual(latest["error_message"], "image_pixel_limit")

    def test_passive_file_image_preserved_without_observation_or_fake_description(self):
        _, latest = self._ingest_file(observe=False)
        self.assertEqual(latest["kind"], "image")
        self.assertEqual(latest["detail"]["vision_status"], "not_requested")
        self.assertIn("未进行视觉分析", latest["short_hint"])
        self.assertEqual(self.vision.scheduled, [])

    def test_supported_image_bytes_override_file_extension_and_vision_mime(self):
        for fmt, mime in (("JPEG", "image/jpeg"), ("PNG", "image/png"), ("GIF", "image/gif"), ("WEBP", "image/webp")):
            with self.subTest(fmt=fmt):
                self.file_bytes = image_bytes(fmt)
                _, latest = self._ingest_file(observe=False, name="wrong.png", mime="image/png")
                self.assertEqual(latest["mime_type"], mime)
                service = object.__new__(VisionObservationService)
                service._client = object()
                service.settings = SimpleNamespace(vision_max_image_bytes=1024 * 1024)
                source = self.inbox.resolve_storage_path(latest)
                with (
                    patch.object(service, "_build_system_instruction", return_value="test"),
                    patch.object(service, "_build_user_instruction", return_value="test"),
                    patch.object(service, "_request_vision_text", return_value='{"summary":"test"}') as request,
                ):
                    service._analyze_with_remote_model(SimpleNamespace(source_path=source))
                self.assertTrue(request.call_args.kwargs["image_urls"][0].startswith(f"data:{mime};base64,"))

    def test_native_preparation_waits_for_file_classification(self):
        pending = self.inbox.create_pending(profile_user_id="master", session_id="slow", source="qq", kind="document")
        loaded = threading.Event()
        reader = self.inbox._load_attachment_items_by_id

        def read_items(**kwargs):
            loaded.set()
            return reader(**kwargs)

        with patch.object(self.inbox, "_load_attachment_items_by_id", read_items), ThreadPoolExecutor(1) as pool:
            future = pool.submit(
                self.inbox.build_native_image_inputs,
                profile_user_id="master",
                session_id="slow",
                attachment_ids=[pending["attachment_id"]],
                timeout_seconds=2,
            )
            self.assertTrue(loaded.wait(1))
            source = self.root / "attachments" / "late.bin"
            source.parent.mkdir(exist_ok=True)
            source.write_bytes(self.file_bytes)
            self.store.update_attachment_inbox_item(
                profile_user_id="master",
                session_id="slow",
                attachment_id=pending["attachment_id"],
                kind="image",
                mime_type="image/png",
                storage_relpath="late.bin",
            )
            self.assertEqual(future.result(timeout=3)["image_count"], 1)

    def _post(self, segments, *, images_allowed=True, repeat=False):
        self.gateway.set_group_vision_enabled(303, images_allowed)
        captured = []
        native_calls = []
        ingest = self.ingest
        inbox = self.inbox

        class Engine:
            desktop_pet_character_resources = None
            care_runtime = None

            def ingest_qq_attachments(self, **kwargs):
                return ingest.ingest_qq_attachments(**kwargs)

            def prepare_qq_native_image_inputs(self, **kwargs):
                native_calls.append(kwargs)
                kwargs.pop("chat_model_override", None)
                return inbox.build_native_image_inputs(**kwargs)

            def wait_for_qq_attachments_settled(self, **kwargs):
                return inbox.wait_for_attachments_settled(**kwargs)

            def prefetch_remote_media_links_for_message(self, **kwargs):
                return {}

            def process_turn_stream(self, payload):
                captured.append(payload)
                yield {"type": "final_ui", "payload": {"speech": "测试回复。", "emotion": "normal"}}

        app = FastAPI()
        app.include_router(
            build_qq_router(
                engine=Engine(),
                config_module=SimpleNamespace(QQ_BRIDGE_ENABLED=True, QQ_ATTACHMENT_READY_WAIT_SECONDS=0.01),
                qq_gateway=self.gateway,
                runtime_metrics=SimpleNamespace(observe_request=lambda *a, **k: None),
                logger=logging.getLogger("test.rich_materials"),
                log_event=lambda *a, **k: None,
            )
        )
        event = self.event(
            [{"type": "at", "data": {"qq": "101"}}, {"type": "text", "data": {"text": "请查看内容"}}, *segments]
        )
        client = TestClient(app)
        response = client.post("/api/qq/napcat/event", json=event)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ok", response.text)
        if repeat:
            repeated = client.post("/api/qq/napcat/event", json=event)
            self.assertEqual(repeated.status_code, 200)
            self.assertIn("duplicate", repeated.json()["reason"])
        self.assertEqual(len(captured), 1)
        return captured[0], native_calls

    def test_file_image_pixels_reach_actual_route_model_input(self):
        payload, calls = self._post([self.file_segment()])
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(payload["native_user_images"]), 1)
        self.assertIn("请查看内容", payload["message"])
        self.assertEqual(payload["native_user_images"][0]["attachment_id"], payload["qq_current_attachment_ids"][0])

    def test_duplicate_file_event_does_not_materialize_or_reply_twice(self):
        self._post([self.file_segment()], repeat=True)
        self.assertEqual(len(self.file_calls), 1)
        self.assertEqual(len(self.vision.scheduled), 1)

    def test_native_image_rechecked_when_file_finishes_during_wait(self):
        build = self.inbox.build_native_image_inputs
        attempts = []

        def initially_pending(**kwargs):
            attempts.append(kwargs)
            if len(attempts) == 1:
                return {"ok": False, "images": [], "status": "pending"}
            return build(**kwargs)

        with patch.object(self.inbox, "build_native_image_inputs", initially_pending):
            payload, _ = self._post([self.file_segment()])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[-1]["timeout_seconds"], 0)
        self.assertEqual(len(payload["native_user_images"]), 1)

    def test_disabled_group_blocks_native_and_background_file_image_vision(self):
        payload, calls = self._post([self.file_segment()], images_allowed=False)
        self.assertEqual(calls, [])
        self.assertEqual(self.vision.scheduled, [])
        self.assertNotIn("native_user_images", payload)
        self.assertIn("包括以文件发送的图片", payload["extra_context"])

    def test_disabled_group_also_blocks_quoted_and_forwarded_file_image_vision(self):
        for kind in ("reply", "forward"):
            with self.subTest(kind=kind):
                self.gateway = NapCatQQGateway(wake_words=("助手",))
                self.gateway._onebot_transport.call = self._onebot
                self.raw_segments = [self.file_segment()]
                payload, calls = self._post([{"type": kind, "data": {"id": "606"}}], images_allowed=False)
                self.assertEqual(calls, [])
                self.assertEqual(self.vision.scheduled, [])
                self.assertNotIn("native_user_images", payload)

    def test_quote_and_forward_file_images_keep_author_and_reach_pixels(self):
        for segment in ({"type": "reply", "data": {"id": "606"}}, {"type": "forward", "data": {"id": "forward-606"}}):
            with self.subTest(kind=segment["type"]):
                self.raw_segments = [self.file_segment()]
                # A fresh gateway admission ledger for the next synthetic event.
                if segment["type"] == "forward":
                    self.gateway = NapCatQQGateway(wake_words=("助手",))
                    self.gateway._onebot_transport.call = self._onebot
                payload, _ = self._post([segment])
                self.assertEqual(len(payload["native_user_images"]), 1)

    def test_cards_reach_direct_quote_forward_model_payload(self):
        for kind in ("direct", "reply", "forward"):
            with self.subTest(kind=kind):
                self.gateway = NapCatQQGateway(wake_words=("助手",))
                self.gateway._onebot_transport.call = self._onebot
                segments = [card_segment()] if kind == "direct" else [{"type": kind, "data": {"id": "606"}}]
                payload, _ = self._post(segments)
                self.assertIn("原始标题", json.dumps(payload, ensure_ascii=False))
                if kind != "direct":
                    self.assertIn("原作者", json.dumps(payload, ensure_ascii=False))
                self.assertNotIn("保留字段", payload["message"])

    def test_native_get_msg_tool_preserves_raw_json_and_xml_on_demand(self):
        handler = OneBotActionToolHandler(delivery_port=QQToolDeliveryPort(self.gateway))
        for kind in ("json", "xml"):
            self.raw_segments = [card_segment(kind)]
            event = self.event(self.raw_segments)
            event["message_id"] = "505" if kind == "json" else "506"
            context = self.gateway.build_message_context(event)
            result = handler.execute(
                call={"action": "get_msg", "params": {}, "message_selector": {"kind": "current_message"}},
                context=ToolExecutionContext(
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    now_ts=1,
                    visual_payload={},
                    client_mode="qq_text",
                    request_context=context.to_turn_payload(),
                ),
            )
            self.assertFalse(result.followup_context.startswith("<tool_use_error>"), result.followup_context)
            payload = json.loads(result.followup_context)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["message"][0]["data"]["data"], self.raw_segments[0]["data"]["data"])
            from companion_v01.tool_continuation import can_finish_tool_batch
            self.assertFalse(can_finish_tool_batch([result]))
        self.assertIn(
            "JSON/XML",
            next(item["summary"] for item in model_onebot_capabilities()["actions"] if item["action"] == "get_msg"),
        )


if __name__ == "__main__":
    unittest.main()
