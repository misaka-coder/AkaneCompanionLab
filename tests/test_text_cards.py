from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.capability_registry import CapabilityRegistry, CapabilitySnapshot
from companion_v01.client_protocol import ClientMode, ClientProtocolContext
from companion_v01.generated_files import GeneratedFileService
from companion_v01.image_materials import SessionImageMaterialResolver
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.qq_card_artwork import _font_paths, wrap_card_text
from companion_v01.qq_gateway import NapCatQQGateway
from companion_v01.routes.qq import _process_qq_turn_streaming, _qq_file_delivery_event_identity
from companion_v01.store import MemoryStore
from companion_v01.text_cards import TextCardDocument, layout_text_card
from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.generated_media import InspectGeneratedFileToolHandler
from companion_v01.tool_handlers.text_cards import RenderCardToolHandler
from tests.test_qq_voice_delivery import FakeQQGateway
from tests.qq_card_fixtures import install_stationery_fixture


class TextCardTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        install_stationery_fixture(self, self.root)
        try:
            _font_paths()
        except RuntimeError:
            self.skipTest("No CJK font; unavailable status is checked separately.")
        self.store = MemoryStore(self.root / "store")
        self.attachments = AttachmentInboxService(store=self.store)
        self.service = GeneratedFileService(base_dir=self.root / "output", store=self.store, attachment_service=self.attachments)
        self.resolver = SessionImageMaterialResolver(attachment_service=self.attachments, generated_file_service=self.service)
        self.identity = SimpleNamespace(build_character_identity=lambda pack: {"assistant_name": "塞西莉亚" if pack == "cecilia" else "灵梦"})
        self.handler = RenderCardToolHandler(generated_file_service=self.service, image_material_resolver=self.resolver,
                                             character_resource_service=self.identity)
        self.context = ToolExecutionContext("owner", "chat", 1800000000, {}, character_pack_id="reimu",
                                            client_mode="qq_text", current_user_source_id="message-1", invocation_id="call-1")

    def run_card(self, context=None, **values):
        call = self.handler.normalize_call({"type": "render_card", "title": "今天的小记", "body": "今天也谢谢你。", **values})
        return self.handler.execute(call=call, context=context or self.context)

    def artifacts(self, result):
        return [event["generated_file"] for event in result.stream_events if event["type"] == "generated_file_ready"]

    def test_card_is_real_managed_readable_png_and_receipt_is_compact(self):
        result = self.run_card(body="# 一起做的事\n整理工具回执。\n\n> 先把好看这件事做好。")
        artifacts = self.artifacts(result)
        self.assertEqual(len(artifacts), 1)
        artifact = artifacts[0]
        self.assertTrue(self.service.is_managed_storage_path(Path(artifact["absolute_path"])))
        self.assertEqual(artifact["delivery_status"], "pending")
        with Image.open(artifact["absolute_path"]) as image:
            self.assertEqual(image.width, 900)
            self.assertEqual(image.info["Body"], "# 一起做的事\n整理工具回执。\n\n> 先把好看这件事做好。")
            image.verify()
        inspected = self.service.inspect_generated_file(profile_user_id="owner", session_id="chat", target=artifact["generated_handle"])
        self.assertIn("整理工具回执", inspected["inspection"]["content"])
        self.assertNotIn("binary_or_unsupported", inspected["inspection"]["source_kind"])
        self.assertIn(artifact["generated_handle"], result.followup_context)
        self.assertNotIn(artifact["absolute_path"], result.followup_context)
        self.assertLess(len(result.followup_context), 500)
        self.assertTrue(result.followup_envelope.complete)

    def test_long_body_is_one_image_with_complete_text_and_fixed_artwork_scale(self):
        body = "# 第一部分\n" + "这是一段需要完整保留的文字，句号也不能落下。" * 40 + "\n\n# 第二部分\n" + "（最后的内容也要读得到。）" * 25
        layout = layout_text_card(TextCardDocument("长文测试", body))
        self.assertGreater(layout.height, 2200)
        self.assertEqual(layout.body, body)
        context = replace(self.context, character_pack_id="cecilia")
        result = self.run_card(context, body=body)
        artifacts = self.artifacts(result)
        self.assertEqual(len(artifacts), 1, result.followup_context)
        self.assertEqual(artifacts[0]["output_title"], "今天的小记")
        self.assertNotIn("pages", json.loads(result.followup_context.split("\n", 1)[0]))
        with Image.open(artifacts[0]["absolute_path"]) as long_image:
            self.assertEqual(long_image.size, (900, layout.height))
            self.assertEqual(long_image.info["Body"], body)
            # Long content stretches the clean paper, leaving the portrait and
            # corner artwork exactly as they appear on a short card.
            short = self.artifacts(self.run_card(context))[0]
            with Image.open(short["absolute_path"]) as short_image:
                self.assertEqual(long_image.crop((450, 0, 900, 550)).tobytes(),
                                 short_image.crop((450, 0, 900, 550)).tobytes())
                self.assertEqual(long_image.crop((0, long_image.height - 235, 900, long_image.height)).tobytes(),
                                 short_image.crop((0, short_image.height - 235, 900, short_image.height)).tobytes())
        reader = InspectGeneratedFileToolHandler(generated_file_service=self.service)
        first = reader.execute(call={"type": "inspect_generated_file", "target": artifacts[0]["generated_handle"], "max_chars": 500}, context=context)
        source = first.stream_events[0]["inspection"]["content"]
        while not first.followup_envelope.complete:
            first = reader.execute(call=dict(first.followup_envelope.continuation), context=context)
            source += first.stream_events[0]["inspection"]["content"]
        self.assertEqual(source, "今天的小记\n\n" + body)

    def test_retries_parallel_calls_and_new_turn_have_correct_artifact_identity(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.run_card(), range(2)))
        ids = [self.artifacts(result)[0]["generated_id"] for result in results]
        self.assertEqual(ids[0], ids[1])
        reopened = RenderCardToolHandler(generated_file_service=self.service, image_material_resolver=self.resolver,
                                        character_resource_service=self.identity)
        result = reopened.execute(call={"type": "render_card", "title": "今天的小记", "body": "今天也谢谢你。"}, context=self.context)
        self.assertEqual(self.artifacts(result)[0]["generated_id"], ids[0])
        for context in (replace(self.context, session_id="other"), replace(self.context, profile_user_id="another"),
                        replace(self.context, character_pack_id="cecilia"), replace(self.context, current_user_source_id="message-2")):
            other = self.artifacts(self.run_card(context))[0]
            self.assertNotEqual(other["generated_id"], ids[0])
        self.assertIsNone(self.service.resolve_generated_artifact(profile_user_id="another", session_id="chat", target=ids[0]))

    def test_image_handle_is_scoped_and_image_is_actually_inserted(self):
        path = self.service.base_dir / "source.png"
        with Image.new("RGB", (240, 120), "#123456") as image:
            image.save(path)
        source = self.service.register_generated_artifact(profile_user_id="owner", session_id="chat", output_path=path,
                    output_title="插图", output_format="png", mime_type="image/png", content_card={}, summary="", created_by_tool="fixture")
        result = self.run_card(image_id=source["generated_handle"])
        with Image.open(self.artifacts(result)[0]["absolute_path"]) as image:
            self.assertEqual(image.getpixel((450, 620)), (18, 52, 86))
        missing = self.run_card(replace(self.context, session_id="other"), image_id=source["generated_handle"])
        self.assertFalse(self.artifacts(missing))
        self.assertIn("image_not_found", missing.followup_context)

    def test_invalid_text_missing_image_font_failure_and_write_failure_never_claim_success(self):
        for values in ({"body": "  "}, {"body": "界" * 4001}, {"body": "短句\n" * 1000},
                       {"title": 1}, {"image_id": "C:/private.png"}, {"image_id": "gen_999"}):
            result = self.run_card(**values)
            self.assertFalse(self.artifacts(result))
            self.assertIn("<tool_use_error>", result.followup_context)
        with patch("companion_v01.text_cards._font_paths", side_effect=RuntimeError("chinese_font_unavailable")):
            self.assertFalse(self.handler.capability_status()["enabled"])
            self.assertIn("chinese_font_unavailable", self.run_card().followup_context)
        with patch("companion_v01.tool_handlers.text_cards.paint_text_card", side_effect=OSError("F:/private/secret")):
            result = self.run_card()
            self.assertIn("card_render_failed", result.followup_context)
            self.assertNotIn("private", result.followup_context)
            self.assertFalse(self.artifacts(result))
        self.assertFalse(list(self.service.base_dir.rglob("*.tmp")))

    def test_long_valid_heading_and_subtitle_stay_inside_the_header(self):
        result = self.run_card(title="很长的标题" * 4, subtitle="日期与副标题" * 8)
        self.assertEqual(len(self.artifacts(result)), 1, result.followup_context)

    def test_cancel_before_or_during_render_never_queues_images(self):
        result = self.run_card(replace(self.context, cancel_requested=lambda: True))
        self.assertIn("card_cancelled", result.followup_context)
        self.assertFalse(self.artifacts(result))
        from companion_v01.text_cards import paint_text_card
        stopped = False
        def paint(*args, **kwargs):
            nonlocal stopped
            canvas = paint_text_card(*args, **kwargs)
            stopped = True
            return canvas
        with patch("companion_v01.tool_handlers.text_cards.paint_text_card", side_effect=paint):
            result = self.run_card(replace(self.context, cancel_requested=lambda: stopped))
        self.assertFalse(self.artifacts(result))
        self.assertFalse(list(self.service.base_dir.rglob("*.tmp")))
        self.assertFalse(list(self.service.base_dir.rglob("card.png")))

    def test_shared_wrapping_is_lossless_and_does_not_isolate_closing_punctuation(self):
        font = ImageFont.truetype(str(_font_paths()[0]), 34)
        with Image.new("RGB", (1, 1)) as image:
            draw = ImageDraw.Draw(image)
            text = "正文末尾的「引用结束了。」接着说下一句。 English words should wrap comfortably."
            lines = wrap_card_text(draw, text, font, 240)
            self.assertEqual("".join(lines), text)
            self.assertFalse(any(line.startswith(tuple("，。！？）》】」』”’")) for line in lines))
            self.assertTrue(all(draw.textlength(line, font=font) <= 240 for line in lines))

    def test_tool_is_in_normal_qq_and_desktop_catalog_with_one_contract(self):
        for mode in (ClientMode.QQ_TEXT, ClientMode.DESKTOP_PET):
            selection = CapabilityRegistry().select(CapabilitySnapshot(mode), allowed_tool_names=("render_card",))
            self.assertIn("render_card", selection.tool_names)
        specs = build_openai_native_tool_specs({"render_card": self.handler})
        from companion_v01.tool_scheduling import scheduled_tool_spec
        self.assertEqual(specs[0]["function"]["parameters"], scheduled_tool_spec(self.handler.tool_spec()).input_schema)
        self.assertIn("body", self.handler.build_prompt_instruction())
        self.assertEqual(self.handler.default_exposure_mode, "on_demand")

    def test_real_chat_discovery_load_and_invoke_render_the_card(self):
        from companion_v01.capability_exposure import directory_text, route_invocation
        from companion_v01.engine_services.tool_rounds import resolve_capability_selection
        from companion_v01.tool_orchestration_engine import normalize_tool_invocation, execute_tool_invocation

        engine = SimpleNamespace(tool_handlers={"render_card": self.handler}, capability_config_base_dir=self.root,
                                 store=self.store)
        engine._resolve_tool_handlers = lambda capability_selection=None, **_: dict(capability_selection.resolved_handlers)
        client = ClientProtocolContext(requested_mode=ClientMode.QQ_TEXT, effective_mode=ClientMode.QQ_TEXT)
        selection = resolve_capability_selection(engine, client_context=client, profile_user_id="owner",
                                                 session_id="chat", character_pack_id="reimu")
        self.assertIn("render_card", directory_text(selection))
        self.assertNotIn("render_card", selection.schema_tool_names)

        def execute(call, selected=selection):
            invocation = normalize_tool_invocation(engine, call, profile_user_id="owner",
                                                   session_id="chat", capability_selection=selected)
            return execute_tool_invocation(engine, invocation=invocation, profile_user_id="owner", session_id="chat",
                                           character_pack_id="reimu", client_context=client, visual_payload={},
                                           now_ts=1800000000, current_user_source_id="discovery-message")[0]

        loaded = execute({"type": "capability_load", "capability_ids": ["render_card"]})
        contract = json.loads(loaded.followup_context.split("\n", 1)[1])["capabilities"][0]
        call, selected = route_invocation({"type": "capability_invoke", "capability_id": "render_card",
            "arguments": {"title": "聊天里的图卡", "body": "工具能够正常调用。"}}, selection)
        rendered = execute(call, selected)
        self.assertEqual(len(self.artifacts(rendered)), 1, rendered.followup_context)
        self.assertEqual(rendered.stream_events[0]["client_mode"], "qq_text")

    def test_real_qq_image_delivery_deduplicates_stream_and_final_and_reports_failure(self):
        event = self.run_card(body="较长的正文也应该完整放在一张图里。" * 80).stream_events[0]
        with Image.open(event["generated_file"]["absolute_path"]) as image:
            self.assertGreater(image.height, 2200)
        actual = NapCatQQGateway(state_path=self.root / "qq.json")
        calls = []
        context = SimpleNamespace(session_id="chat", profile_user_id="owner", character_pack_id="reimu", reply_mode="text")
        file_event = {"type": "file_ready", "send_to_user": True, "client_mode": "qq_text",
                      "file": {"source_type": "generated", "source_id": event["generated_file"]["generated_id"],
                               "absolute_path": event["generated_file"]["absolute_path"], "name": "card.png"}}
        self.assertEqual(_qq_file_delivery_event_identity(event), _qq_file_delivery_event_identity(file_event))
        for succeeds in (True, False):
            calls.clear()
            actual.send_image = lambda context, **kwargs: calls.append(kwargs) or {"ok": succeeds}
            class Gateway(FakeQQGateway):
                def send_generated_files(self, context, events, **kwargs):
                    return actual.send_generated_files(context, events, **kwargs)
                def add_delivery_note(self, *args):
                    pass
            class Engine:
                def process_turn_stream(self, payload):
                    yield event
                    yield {"type": "final_ui", "payload": {"speech": "图卡正在发送。", "tool_events": [event, file_event]}}
            gateway = Gateway()
            result = _process_qq_turn_streaming(engine=Engine(), qq_gateway=gateway, context=context,
                turn_payload={"message": "小记", "client_mode": "qq_text"},
                config_module=SimpleNamespace(QQ_STREAM_REPLIES_ENABLED=True))
            self.assertEqual(len(calls), 1)
            self.assertEqual(result["file_send_result"]["ok"], succeeds)
            if not succeeds:
                self.assertIn("发送失败", "".join(sum(gateway.text_sends, [])))


if __name__ == "__main__":
    unittest.main()
