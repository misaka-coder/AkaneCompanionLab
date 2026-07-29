from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.generated_files import GeneratedFileService
from companion_v01.image_generation import ImageGenerationError, ImageGenerationService, PinAIImageProvider
from companion_v01.image_materials import SessionImageMaterialResolver
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.store import MemoryStore
from companion_v01.tool_runtime import GenerateImageToolHandler, LoadMaterialToolHandler, ToolExecutionContext


PNG_BYTES = b"\x89PNG\r\n\x1a\nsynthetic-image-bytes"


class FakeResponse:
    def __init__(
        self,
        payload,
        *,
        content_type: str = "application/json",
        status_code: int = 200,
        lines: list[str] | None = None,
    ):
        self.payload = payload
        self.headers = {"Content-Type": content_type}
        self.status_code = status_code
        self.lines = list(lines or [])
        self.closed = False

    def json(self):
        return self.payload

    def iter_lines(self, decode_unicode: bool = True):
        return iter(self.lines)

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected provider request")
        return self.responses.pop(0)

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected provider request")
        return self.responses.pop(0)


class ImageGenerationTests(unittest.TestCase):
    def _services(self, root: Path):
        store = MemoryStore(root / "store")
        attachment_root = root / "inbox"
        output_root = root / "outputs"
        attachment_service = AttachmentInboxService(store=store, base_dir=attachment_root)
        generated_service = GeneratedFileService(
            base_dir=output_root,
            store=store,
            attachment_service=attachment_service,
            work_dir=root / "work",
        )
        resolver = SessionImageMaterialResolver(
            attachment_service=attachment_service,
            generated_file_service=generated_service,
        )
        return store, attachment_service, generated_service, resolver

    def _add_attachment(self, root: Path, attachment_service: AttachmentInboxService) -> dict:
        image_path = root / "inbox" / "user" / "session" / "reference.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(PNG_BYTES)
        item = attachment_service.create_pending(
            profile_user_id="user",
            session_id="session",
            source="qq",
            kind="image",
            origin_name="reference.png",
            mime_type="image/png",
            storage_relpath="user/session/reference.png",
            timestamp=100,
        )
        return (
            attachment_service.mark_ready(
                profile_user_id="user",
                session_id="session",
                attachment_id=item["attachment_id"],
                summary_title="参考图",
                short_hint="合成测试图片",
                timestamp=101,
            )
            or item
        )

    def test_load_material_returns_internal_model_images_without_public_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _store, attachment_service, generated_service, resolver = self._services(root)
            attachment = self._add_attachment(root, attachment_service)
            handler = LoadMaterialToolHandler(image_material_resolver=resolver)
            context = ToolExecutionContext(profile_user_id="user", session_id="session", now_ts=200, visual_payload={})

            result = handler.execute(
                call={"type": "load_material", "targets": [attachment["attachment_handle"]]},
                context=context,
            )
            isolated = resolver.build_model_image_inputs(
                profile_user_id="other",
                session_id="session",
                targets=[attachment["attachment_handle"]],
            )

            self.assertEqual(result.tool_type, "load_material")
            self.assertEqual(result.stream_events[0]["handles"], ["img_001"])
            self.assertEqual(len(result.model_image_inputs), 1)
            self.assertTrue(result.model_image_inputs[0]["data_url"].startswith("data:image/png;base64,"))
            self.assertNotIn("base64", result.followup_context)
            self.assertNotIn(str(root), result.followup_context)
            self.assertNotIn("data_url", str(result.stream_events))
            self.assertFalse(isolated["ok"])
            self.assertIsNotNone(generated_service)

    def test_image_tools_publish_precise_native_schemas(self) -> None:
        load_handler = LoadMaterialToolHandler(image_material_resolver=object())
        generate_handler = GenerateImageToolHandler(image_generation_service=object())

        specs = build_openai_native_tool_specs(
            {"load_material": load_handler, "generate_image": generate_handler},
            allowed_tool_names={"load_material", "generate_image"},
        )
        by_name = {spec["function"]["name"]: spec["function"]["parameters"] for spec in specs}

        self.assertFalse(by_name["load_material"]["additionalProperties"])
        self.assertEqual(by_name["load_material"]["properties"]["targets"]["maxItems"], 5)
        self.assertFalse(by_name["generate_image"]["additionalProperties"])
        self.assertEqual(by_name["generate_image"]["required"], ["prompt"])
        self.assertEqual(by_name["generate_image"]["properties"]["reference_images"]["maxItems"], 5)

    @patch("companion_v01.engine.ImageGenerationService")
    @patch("companion_v01.engine.PinAIImageProvider")
    @patch("companion_v01.engine.config.IMAGE_GENERATION_API_KEY", "")
    def test_image_generation_reuses_current_bot_key_when_dedicated_key_is_absent(
        self,
        provider_type,
        service_type,
    ) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.settings = SimpleNamespace(
            chat_api_key="bot-scoped-secret",
            image_generation_enabled=True,
            image_generation_api_key="",
            image_generation_base_url="https://images.example/v1",
            image_generation_model="gpt-image-2",
        )
        engine._get_image_material_resolver = lambda: object()
        engine._get_generated_file_service = lambda: object()
        provider_type.return_value.configured = True
        service = object()
        service_type.return_value = service

        result = engine._get_image_generation_service()

        self.assertIs(result, service)
        self.assertEqual(provider_type.call_args.kwargs["api_key"], "bot-scoped-secret")
        self.assertEqual(provider_type.call_args.kwargs["base_url"], "https://images.example/v1")
        self.assertIs(engine.image_generation_service, service)

    def test_pinai_text_to_image_uses_stream_request_and_decodes_json(self) -> None:
        encoded = base64.b64encode(PNG_BYTES).decode("ascii")
        session = FakeSession([FakeResponse({"data": [{"b64_json": encoded}]})])
        provider = PinAIImageProvider(
            base_url="https://us.pinai-cn.com/v1",
            api_key="secret-key",
            session=session,
        )

        outputs = provider.generate(
            prompt="蓝白科技海报",
            size="1024x1024",
            quality="high",
            background="opaque",
            output_format="png",
            compression=90,
            n=1,
        )

        self.assertEqual(outputs[0].data, PNG_BYTES)
        self.assertEqual(outputs[0].output_format, "png")
        call = session.calls[0]
        self.assertTrue(call["url"].endswith("/images/generations"))
        self.assertTrue(call["stream"])
        self.assertEqual(call["timeout"], (10.0, 300.0))
        self.assertEqual(call["json"]["model"], "gpt-image-2")
        self.assertEqual(call["json"]["response_format"], "b64_json")
        self.assertTrue(call["json"]["stream"])

    def test_pinai_sse_keeps_latest_final_image_per_output_index(self) -> None:
        partial = base64.b64encode(b"\x89PNG\r\n\x1a\npartial").decode("ascii")
        final = base64.b64encode(PNG_BYTES).decode("ascii")
        session = FakeSession(
            [
                FakeResponse(
                    {},
                    content_type="text/event-stream; charset=utf-8",
                    lines=[
                        f'data: {{"type":"image_generation.partial_image","output_index":0,"b64_json":"{partial}"}}',
                        f'data: {{"type":"image_generation.completed","output_index":0,"b64_json":"{final}"}}',
                        "data: [DONE]",
                    ],
                )
            ]
        )
        provider = PinAIImageProvider(
            base_url="https://us.pinai-cn.com/v1",
            api_key="secret-key",
            session=session,
        )

        outputs = provider.generate(
            prompt="流式测试",
            size="1024x1024",
            quality="low",
            background="auto",
            output_format="png",
            compression=90,
            n=1,
        )

        self.assertEqual(outputs[0].data, PNG_BYTES)

    def test_pinai_sse_stops_after_complete_image_without_waiting_for_done(self) -> None:
        final = base64.b64encode(PNG_BYTES).decode("ascii")

        class ResponseThatStaysOpen(FakeResponse):
            def iter_lines(self, decode_unicode: bool = True):
                yield (f'data: {{"type":"image_generation.completed","output_index":0,"b64_json":"{final}"}}')
                raise AssertionError("completed image stream should not be consumed further")

        response = ResponseThatStaysOpen(
            {},
            content_type="text/event-stream; charset=utf-8",
        )
        provider = PinAIImageProvider(
            base_url="https://us.pinai-cn.com/v1",
            api_key="secret-key",
            session=FakeSession([response]),
        )

        outputs = provider.generate(
            prompt="完成后仍保持连接的流",
            size="1024x1024",
            quality="low",
            background="auto",
            output_format="png",
            compression=90,
            n=1,
        )

        self.assertEqual(outputs[0].data, PNG_BYTES)
        self.assertTrue(response.closed)

    def test_pinai_forbidden_response_is_structured_without_body_or_key(self) -> None:
        session = FakeSession([FakeResponse({"secret": "must-not-leak"}, status_code=403)])
        provider = PinAIImageProvider(
            base_url="https://us.pinai-cn.com/v1",
            api_key="secret-key",
            session=session,
        )

        with self.assertRaises(ImageGenerationError) as raised:
            provider.generate(
                prompt="测试",
                size="1024x1024",
                quality="low",
                background="auto",
                output_format="png",
                compression=90,
                n=1,
            )

        self.assertEqual(raised.exception.code, "provider_auth_or_network_forbidden")
        self.assertNotIn("secret-key", str(raised.exception))
        self.assertNotIn("must-not-leak", str(raised.exception))

    def test_pinai_retries_transient_account_pool_and_recovers_readiness(self) -> None:
        encoded = base64.b64encode(PNG_BYTES).decode("ascii")
        session = FakeSession(
            [
                FakeResponse(
                    {"error": {"type": "api_error", "message": "No available compatible accounts"}},
                    status_code=503,
                ),
                FakeResponse({"data": [{"b64_json": encoded}]}),
            ]
        )
        sleeps: list[float] = []
        provider = PinAIImageProvider(
            base_url="https://api.pinaic.com/v1",
            api_key="secret-key",
            session=session,
            retry_sleep=sleeps.append,
        )

        outputs = provider.generate(
            prompt="测试",
            size="1024x1024",
            quality="low",
            background="auto",
            output_format="png",
            compression=90,
            n=1,
        )

        self.assertEqual(outputs[0].data, PNG_BYTES)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(sleeps, [1.0])
        self.assertEqual(provider.capability_status()["status"], "ready")

    def test_pinai_final_account_pool_failure_keeps_tool_degraded_and_retryable(self) -> None:
        response = FakeResponse(
            {"error": {"type": "api_error", "message": "No available compatible accounts"}},
            status_code=503,
        )
        session = FakeSession([response, response, response])
        provider = PinAIImageProvider(
            base_url="https://api.pinaic.com/v1",
            api_key="secret-key",
            session=session,
            retry_sleep=lambda _seconds: None,
        )

        with self.assertRaises(ImageGenerationError) as raised:
            provider.generate(
                prompt="测试",
                size="1024x1024",
                quality="low",
                background="auto",
                output_format="png",
                compression=90,
                n=1,
            )

        self.assertEqual(raised.exception.code, "provider_no_compatible_accounts")
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(len(session.calls), 3)
        status = provider.capability_status()
        self.assertTrue(status["enabled"])
        self.assertEqual(status["status"], "degraded")
        self.assertEqual(status["reason"], "provider_no_compatible_accounts")

    def test_pinai_images_api_unsupported_is_specific_and_non_retryable(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    {"error": {"type": "not_found_error", "message": "Images API is not supported for this platform"}},
                    status_code=404,
                )
            ]
        )
        provider = PinAIImageProvider(
            base_url="https://api.pinaic.com/v1",
            api_key="secret-key",
            session=session,
        )

        with self.assertRaises(ImageGenerationError) as raised:
            provider.generate(
                prompt="测试",
                size="1024x1024",
                quality="low",
                background="auto",
                output_format="png",
                compression=90,
                n=1,
            )

        self.assertEqual(raised.exception.code, "provider_images_api_unsupported")
        self.assertFalse(raised.exception.retryable)

    def test_pinai_readiness_probe_hides_unsupported_platform_without_generating(self) -> None:
        unsupported = FakeSession(
            [
                FakeResponse(
                    {"error": {"type": "not_found_error", "message": "Images API is not supported for this platform"}},
                    status_code=404,
                )
            ]
        )
        provider = PinAIImageProvider(
            base_url="https://api.pinaic.com/v1",
            api_key="secret-key",
            session=unsupported,
            readiness_probe_in_background=False,
        )

        status = provider.capability_status()

        self.assertFalse(status["enabled"])
        self.assertEqual(status["status"], "unsupported")
        self.assertEqual(status["reason"], "image_key_not_bound_to_openai_platform")
        self.assertTrue(unsupported.calls[0]["url"].endswith("/models"))
        self.assertNotIn("json", unsupported.calls[0])

    def test_pinai_readiness_probe_accepts_available_image_model(self) -> None:
        supported = FakeSession([FakeResponse({"data": [{"id": "gpt-image-2"}]})])
        provider = PinAIImageProvider(
            base_url="https://images.example.com/v1",
            api_key="secret-key",
            session=supported,
            readiness_probe_in_background=False,
        )

        status = provider.capability_status()

        self.assertTrue(status["enabled"])
        self.assertEqual(status["status"], "ready")

    @patch("companion_v01.image_generation.threading.Thread")
    def test_pinai_background_probe_keeps_configured_tool_available_while_first_check_runs(
        self,
        thread_type,
    ) -> None:
        provider = PinAIImageProvider(
            base_url="https://images.example.com/v1",
            api_key="secret-key",
            session=FakeSession([]),
        )

        status = provider.capability_status()

        self.assertTrue(status["enabled"])
        self.assertEqual(status["status"], "degraded")
        self.assertEqual(status["reason"], "image_provider_probe_pending")
        self.assertTrue(status["refreshing"])
        thread_type.return_value.start.assert_called_once_with()

    @patch("companion_v01.image_generation.threading.Thread")
    def test_pinai_background_refresh_preserves_last_ready_status(
        self,
        thread_type,
    ) -> None:
        now = [0.0]
        provider = PinAIImageProvider(
            base_url="https://images.example.com/v1",
            api_key="secret-key",
            session=FakeSession([]),
            readiness_ready_ttl_seconds=30,
            readiness_clock=lambda: now[0],
        )
        provider._remember_capability_status({"enabled": True, "status": "ready", "reason": ""})
        now[0] = 31.0

        status = provider.capability_status()

        self.assertTrue(status["enabled"])
        self.assertEqual(status["status"], "ready")
        self.assertTrue(status["refreshing"])
        thread_type.return_value.start.assert_called_once_with()

    def test_pinai_readiness_probe_hides_key_without_image_model(self) -> None:
        unsupported = FakeSession([FakeResponse({"data": [{"id": "claude-sonnet-4-5"}]})])
        provider = PinAIImageProvider(
            base_url="https://images.example.com/v1",
            api_key="secret-key",
            session=unsupported,
            readiness_probe_in_background=False,
        )

        status = provider.capability_status()

        self.assertFalse(status["enabled"])
        self.assertEqual(status["status"], "unsupported")
        self.assertEqual(status["reason"], "image_model_not_available_for_key")

    def test_pinai_multi_image_edit_uses_repeated_image_array_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _store, attachment_service, generated_service, resolver = self._services(root)
            attachment = self._add_attachment(root, attachment_service)
            source = resolver.resolve_many(
                profile_user_id="user",
                session_id="session",
                targets=[attachment["attachment_handle"]],
            )["materials"][0]
            generated_path = generated_service.allocate_output_path(
                profile_user_id="user",
                session_id="session",
                title="旧生成图",
                output_format="png",
                timestamp=110,
            )
            generated_path.parent.mkdir(parents=True, exist_ok=True)
            generated_path.write_bytes(PNG_BYTES)
            generated = generated_service.register_generated_artifact(
                profile_user_id="user",
                session_id="session",
                output_path=generated_path,
                output_title="旧生成图",
                output_format="png",
                mime_type="image/png",
                content_card={},
                summary="旧生成图",
                created_by_tool="fixture",
                timestamp=110,
            )
            second = resolver.resolve_many(
                profile_user_id="user",
                session_id="session",
                targets=[generated["generated_handle"]],
            )["materials"][0]
            encoded = base64.b64encode(PNG_BYTES).decode("ascii")
            session = FakeSession([FakeResponse({"data": [{"b64_json": encoded}]})])
            provider = PinAIImageProvider(
                base_url="https://us.pinai-cn.com/v1",
                api_key="secret-key",
                session=session,
            )

            outputs = provider.generate(
                prompt="融合两张参考图",
                size="1536x1024",
                quality="medium",
                background="auto",
                output_format="webp",
                compression=85,
                n=1,
                references=[source, second],
                input_fidelity="high",
            )

            self.assertEqual(len(outputs), 1)
            call = session.calls[0]
            self.assertTrue(call["url"].endswith("/images/edits"))
            self.assertEqual([name for name, _value in call["files"]], ["image[]", "image[]"])
            self.assertEqual(call["data"]["input_fidelity"], "high")
            self.assertNotIn(str(root), str(call["files"]))

    def test_pinai_multi_image_edit_falls_back_to_temporary_file_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _store, attachment_service, generated_service, resolver = self._services(root)
            attachment = self._add_attachment(root, attachment_service)
            first = resolver.resolve_many(
                profile_user_id="user",
                session_id="session",
                targets=[attachment["attachment_handle"]],
            )["materials"][0]
            generated_path = generated_service.allocate_output_path(
                profile_user_id="user",
                session_id="session",
                title="第二张图",
                output_format="png",
                timestamp=120,
            )
            generated_path.parent.mkdir(parents=True, exist_ok=True)
            generated_path.write_bytes(PNG_BYTES)
            generated = generated_service.register_generated_artifact(
                profile_user_id="user",
                session_id="session",
                output_path=generated_path,
                output_title="第二张图",
                output_format="png",
                mime_type="image/png",
                content_card={},
                summary="第二张图",
                created_by_tool="fixture",
                timestamp=120,
            )
            second = resolver.resolve_many(
                profile_user_id="user",
                session_id="session",
                targets=[generated["generated_handle"]],
            )["materials"][0]
            encoded = base64.b64encode(PNG_BYTES).decode("ascii")
            session = FakeSession(
                [
                    FakeResponse({}, status_code=400),
                    FakeResponse({"id": "file-sub2-first"}),
                    FakeResponse({"id": "file-sub2-second"}),
                    FakeResponse({"data": [{"b64_json": encoded}]}),
                ]
            )
            provider = PinAIImageProvider(
                base_url="https://us.pinai-cn.com/v1",
                api_key="secret-key",
                session=session,
            )

            outputs = provider.generate(
                prompt="融合两张图",
                size="1024x1024",
                quality="high",
                background="auto",
                output_format="png",
                compression=90,
                n=1,
                references=[first, second],
            )

            self.assertEqual(len(outputs), 1)
            self.assertEqual(
                [call["url"].rsplit("/", 1)[-1] for call in session.calls], ["edits", "files", "files", "edits"]
            )
            final_payload = session.calls[-1]["json"]
            self.assertEqual(
                final_payload["images"],
                [{"file_id": "file-sub2-first"}, {"file_id": "file-sub2-second"}],
            )
            self.assertNotIn("file-sub2-first", str(outputs))

    def test_generate_image_tool_registers_and_reloads_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _store, attachment_service, generated_service, resolver = self._services(root)
            attachment = self._add_attachment(root, attachment_service)
            encoded = base64.b64encode(PNG_BYTES).decode("ascii")
            session = FakeSession([FakeResponse({"data": [{"b64_json": encoded}]})])
            provider = PinAIImageProvider(
                base_url="https://us.pinai-cn.com/v1",
                api_key="secret-key",
                session=session,
            )
            service = ImageGenerationService(
                provider=provider,
                image_material_resolver=resolver,
                generated_file_service=generated_service,
            )
            handler = GenerateImageToolHandler(image_generation_service=service)
            context = ToolExecutionContext(profile_user_id="user", session_id="session", now_ts=200, visual_payload={})

            result = handler.execute(
                call={
                    "type": "generate_image",
                    "prompt": "保持主体，改成夜景",
                    "reference_images": [attachment["attachment_handle"]],
                    "mask_image": "",
                    "size": "1024x1024",
                    "quality": "high",
                    "background": "auto",
                    "output_format": "png",
                    "compression": 90,
                    "input_fidelity": "high",
                    "n": 1,
                    "output_title": "夜景版本",
                    "send_to_user": True,
                },
                context=context,
            )

            generated_events = [event for event in result.stream_events if event["type"] == "generated_file_ready"]
            self.assertEqual(len(generated_events), 1)
            generated = generated_events[0]["generated_file"]
            self.assertEqual(generated["generated_handle"], "gen_001")
            self.assertEqual(generated["created_by_tool"], "generate_image")
            self.assertTrue(Path(generated["absolute_path"]).is_file())
            self.assertTrue(generated_events[0]["send_to_user"])
            self.assertEqual(len(result.model_image_inputs), 1)
            self.assertEqual(result.model_image_inputs[0]["attachment_handle"], "gen_001")
            self.assertNotIn("secret-key", str(result.stream_events))
            self.assertNotIn("data:image", str(result.stream_events))


if __name__ == "__main__":
    unittest.main()
