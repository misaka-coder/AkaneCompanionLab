from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from companion_v01.capability_registry import CapabilityRegistry, CapabilitySnapshot
from companion_v01.client_protocol import ClientMode
from companion_v01.qq_gateway import NapCatQQGateway, QQMessageContext
from companion_v01.sticker_assets import StickerAssetService
from companion_v01.tool_runtime import SendStickerToolHandler, ToolExecutionContext


PROJECT_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_DIR / "web" / "assets"


class StickerAssetTests(unittest.TestCase):
    def test_sticker_tool_is_available_in_qq_mode_only_for_v0(self) -> None:
        registry = CapabilityRegistry()

        qq_selection = registry.select(CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT))
        desktop_selection = registry.select(CapabilitySnapshot(client_mode=ClientMode.DESKTOP_PET))

        self.assertIn("send_sticker", qq_selection.tool_names)
        self.assertNotIn("send_sticker", desktop_selection.tool_names)

    def test_sticker_manifest_resolves_id_and_alias(self) -> None:
        service = StickerAssetService(assets_dir=ASSETS_DIR)

        by_id = service.resolve("biexiao")
        by_alias = service.resolve("憨笑")

        self.assertTrue(by_id.ok)
        self.assertTrue(by_alias.ok)
        self.assertEqual(by_id.sticker["display_name"], "憋笑")
        self.assertEqual(by_alias.sticker["id"], "biexiao")
        self.assertTrue(Path(by_id.sticker["absolute_path"]).exists())
        self.assertIn("haoxingfu(好幸福", service.build_prompt_list())

    def test_send_sticker_tool_emits_sticker_event(self) -> None:
        service = StickerAssetService(assets_dir=ASSETS_DIR)
        handler = SendStickerToolHandler(sticker_service=service)
        call = handler.normalize_call({"type": "send_sticker", "sticker": "装死"})

        self.assertIsNotNone(call)
        result = handler.execute(
            call=call or {},
            context=ToolExecutionContext(
                profile_user_id="master",
                session_id="qq_private_1",
                now_ts=100,
                visual_payload={},
            ),
        )

        self.assertEqual(result.tool_type, "send_sticker")
        self.assertEqual(len(result.stream_events), 1)
        event = result.stream_events[0]
        self.assertEqual(event["type"], "sticker_ready")
        self.assertEqual(event["sticker"]["display_name"], "装死")
        self.assertTrue(event["send_to_user"])

    def test_send_sticker_unknown_returns_followup_without_event(self) -> None:
        service = StickerAssetService(assets_dir=ASSETS_DIR)
        handler = SendStickerToolHandler(sticker_service=service)

        result = handler.execute(
            call={"type": "send_sticker", "sticker": "不存在的表情"},
            context=ToolExecutionContext(
                profile_user_id="master",
                session_id="qq_private_1",
                now_ts=100,
                visual_payload={},
            ),
        )

        self.assertEqual(result.stream_events, [])
        self.assertIn("没有找到", result.followup_context)

    @patch("companion_v01.qq_gateway.requests.post")
    def test_qq_gateway_sends_sticker_as_image_message(self, post: Mock) -> None:
        post.return_value = Mock(
            raise_for_status=Mock(),
            json=Mock(return_value={"status": "ok"}),
        )
        service = StickerAssetService(assets_dir=ASSETS_DIR)
        sticker = service.resolve("haoxingfu").sticker
        gateway = NapCatQQGateway()
        result = gateway.send_stickers(
            QQMessageContext(
                should_respond=True,
                reason="test",
                is_group=False,
                target_id=123456,
                session_id="qq_private_123456",
                profile_user_id="master",
            ),
            [
                {
                    "type": "sticker_ready",
                    "sticker": sticker,
                    "send_to_user": True,
                }
            ],
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["user_id"], 123456)
        self.assertEqual(payload["message"][0]["type"], "image")
        self.assertIn("file:///", payload["message"][0]["data"]["file"])


if __name__ == "__main__":
    unittest.main()
