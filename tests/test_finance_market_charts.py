from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from PIL import Image

import config
from companion_v01.attachment_inbox import AttachmentInboxService
from companion_v01.finance import (
    ChartRequest,
    FinanceAnalysisResult,
    LocalChartProvider,
    MarketDataToolService,
    QQFinanceDeliveryAdapter,
    build_market_tool_handlers,
)
from companion_v01.generated_files import GeneratedFileService
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.qq_gateway import NapCatQQGateway, QQMessageContext
from companion_v01.store import MemoryStore
from companion_v01.tool_runtime import ToolExecutionContext
from services.market_data import (
    FinanceSubscription,
    MarketBar,
    MarketDataResponse,
    MarketDataValidationError,
    MarketEventStore,
    MarketSeries,
    MockMarketDataProvider,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "choice_market_data_synthetic_v1.json"
MARKET_ZONE = ZoneInfo("Asia/Shanghai")


class LocalChartProviderTests(unittest.TestCase):
    def _series(self, *, invalid_order: bool = False) -> MarketSeries:
        start = int(datetime(2026, 6, 1, 15, 0, tzinfo=MARKET_ZONE).timestamp())
        points = []
        for index in range(30):
            close = 100.0 + index * 0.25
            points.append(
                MarketBar(
                    timestamp=start + index * 86400,
                    open=close - 0.2,
                    high=close + 0.8,
                    low=close - 0.7,
                    close=close,
                    volume=100_000 + index * 1000,
                )
            )
        if invalid_order:
            points[-2], points[-1] = points[-1], points[-2]
        return MarketSeries(
            provider="synthetic_choice",
            code="000000.TEST",
            interval="1d",
            adjusted="none",
            timezone="Asia/Shanghai",
            points=tuple(points),
            as_of=max(point.timestamp for point in points),
        )

    def test_fixed_renderer_preserves_data_title_range_and_as_of_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "chart.png"
            request = ChartRequest(
                code="000000.TEST",
                lookback=20,
                moving_averages=(5, 10, 20),
                title="合成测试公司日线量价",
            )

            result = LocalChartProvider().render(
                series=self._series(),
                request=request,
                output_path=output_path,
                source="Synthetic Choice Fixture",
            )

            self.assertGreater(output_path.stat().st_size, 0)
            self.assertEqual((result.width, result.height), (1280, 720))
            self.assertEqual(result.point_count, 20)
            self.assertEqual(result.latest_bar[4], 107.25)
            self.assertEqual(result.latest_bar[5], 129_000)
            with Image.open(output_path) as image:
                metadata = json.loads(image.text["akane_chart"])
                self.assertEqual(image.size, (1280, 720))
                self.assertEqual(metadata["title"], "合成测试公司日线量价")
                self.assertEqual(metadata["code"], "000000.TEST")
                self.assertEqual(metadata["date_from"], result.date_from)
                self.assertEqual(metadata["date_to"], result.date_to)
                self.assertEqual(metadata["as_of"], result.as_of)
                self.assertEqual(metadata["latest_bar"]["close"], 107.25)
                self.assertEqual(metadata["latest_bar"]["volume"], 129_000.0)
                self.assertEqual(metadata["series_sha256"], result.series_sha256)
                self.assertAlmostEqual(metadata["moving_average_latest"]["ma5"], 106.75)

    def test_invalid_market_series_fails_closed_without_creating_png(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "invalid.png"

            with self.assertRaises(MarketDataValidationError) as raised:
                LocalChartProvider().render(
                    series=self._series(invalid_order=True),
                    request=ChartRequest(code="000000.TEST", lookback=20),
                    output_path=output_path,
                    source="Synthetic Choice Fixture",
                )

            self.assertEqual(raised.exception.code, "invalid_market_series")
            self.assertFalse(output_path.exists())

    def test_request_rejects_arbitrary_chart_grammar(self) -> None:
        for kwargs in (
            {"chart_type": "python_code"},
            {"interval": "5m"},
            {"lookback": 9999},
            {"moving_averages": (7,)},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(MarketDataValidationError):
                ChartRequest(code="000000.TEST", **kwargs)


class RenderMarketChartToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.memory_store = MemoryStore(self.root / "memory")
        self.generated_service = GeneratedFileService(
            base_dir=self.root / "generated_files",
            store=self.memory_store,
            attachment_service=AttachmentInboxService(store=self.memory_store),
        )
        provider = MockMarketDataProvider.from_fixture_path(FIXTURE_PATH)
        self.market_service = MarketDataToolService(
            provider=provider,
            event_store=MarketEventStore(self.root / "events.sqlite3"),
            clock=lambda: 1_752_153_600,
        )
        self.handlers = build_market_tool_handlers(
            self.market_service,
            generated_file_service=self.generated_service,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_tool_schema_has_no_raw_data_code_style_or_path_inputs(self) -> None:
        specs = build_openai_native_tool_specs(self.handlers)
        chart = next(item for item in specs if item["function"]["name"] == "render_market_chart")
        properties = chart["function"]["parameters"]["properties"]

        self.assertFalse(chart["function"]["parameters"]["additionalProperties"])
        self.assertEqual(chart["function"]["parameters"]["required"], ["code"])
        self.assertEqual(properties["chart_type"]["enum"], ["candlestick_volume"])
        self.assertTrue({"points", "prices", "code_snippet", "style", "output_path"}.isdisjoint(properties))
        handler = self.handlers["render_market_chart"]
        metadata = handler.tool_metadata()
        self.assertEqual(metadata.family, "finance_artifact")
        self.assertEqual(metadata.operation, "control")
        self.assertEqual(metadata.risk, "low")
        self.assertIsNone(
            handler.normalize_call(
                {
                    "type": "render_market_chart",
                    "code": "000000.TEST",
                    "points": [{"close": 999999}],
                }
            )
        )

    def test_tool_refetches_trusted_series_registers_png_and_returns_safe_evidence(self) -> None:
        handler = self.handlers["render_market_chart"]
        call = handler.normalize_call(
            {
                "type": "render_market_chart",
                "code": "000000.TEST",
                "lookback": 20,
                "moving_averages": [5, 20],
                "title": "合成测试图",
                "send_to_user": True,
            }
        )
        context = ToolExecutionContext(
            profile_user_id="owner",
            session_id="session",
            now_ts=1_752_153_600,
            visual_payload={},
            current_user_source_id="message::chart-request",
            request_context={"message": "请画 000000.TEST 的 K 线图"},
        )

        result = handler.execute(call=call or {}, context=context)

        self.assertEqual(result.stream_events[0]["type"], "market_chart_ready")
        self.assertEqual(result.stream_events[0]["delivery_scope"], "finance_market_chart")
        generated = result.stream_events[0]["generated_file"]
        self.assertEqual(generated["output_format"], "png")
        self.assertEqual(generated["mime_type"], "image/png")
        self.assertEqual(generated["created_by_tool"], "render_market_chart")
        self.assertEqual(generated["source_ids"], ["message::chart-request"])
        self.assertGreater(generated["file_size"], 0)
        self.assertTrue(Path(generated["absolute_path"]).is_file())
        self.assertIn('"as_of":', result.followup_context)
        self.assertIn('"series_sha256":', result.followup_context)
        self.assertNotIn(str(self.root), result.followup_context)

    def test_upstream_failure_does_not_register_or_emit_an_empty_chart(self) -> None:
        handler = self.handlers["render_market_chart"]
        call = handler.normalize_call(
            {
                "type": "render_market_chart",
                "code": "000000.TEST",
                "lookback": 20,
            }
        )
        context = ToolExecutionContext(
            profile_user_id="owner",
            session_id="session",
            now_ts=1_752_153_600,
            visual_payload={},
            request_context={"message": "请画 000000.TEST 的 K 线图"},
        )
        unavailable = MarketDataResponse(
            ok=False,
            status="unavailable",
            provider="synthetic_choice",
            source="Synthetic Choice Fixture",
            as_of=None,
            reason="synthetic outage",
            data=None,
        )

        with patch.object(self.market_service, "price_series_response", return_value=unavailable):
            result = handler.execute(call=call or {}, context=context)

        self.assertFalse(any(event.get("type") == "market_chart_ready" for event in result.stream_events))
        self.assertEqual(result.stream_events[0]["status"], "unavailable")
        self.assertIn('"status": "unavailable"', result.followup_context)
        self.assertEqual(
            self.memory_store.list_generated_files(profile_user_id="owner", session_id="session"),
            [],
        )


class FinanceChartQQDeliveryTests(unittest.TestCase):
    def _event(self, path: Path) -> dict:
        return {
            "type": "market_chart_ready",
            "delivery_scope": "finance_market_chart",
            "send_to_user": True,
            "generated_file": {
                "generated_id": "generated::chart",
                "absolute_path": str(path),
                "output_title": "市场图表",
                "output_format": "png",
                "file_ext": "png",
                "mime_type": "image/png",
                "created_by_tool": "render_market_chart",
            },
        }

    def test_subscription_authorization_sends_image_without_current_text_intent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            chart_path = Path(temp_dir) / "chart.png"
            chart_path.write_bytes(b"not-empty")
            gateway = NapCatQQGateway()
            context = QQMessageContext(
                should_respond=True,
                reason="finance_subscription_push",
                is_group=True,
                target_id=20001,
                group_id=20001,
                session_id="qq_group_shared_20001",
                profile_user_id="qq_group_shared_20001",
                finance_mode="push",
                clean_message="",
                raw_message="",
            )

            class FakeResponse:
                def raise_for_status(self) -> None:
                    return None

                def json(self):
                    return {"status": "ok"}

            with patch("companion_v01.qq_gateway.requests.post", return_value=FakeResponse()) as post:
                result = gateway.send_market_charts(
                    context,
                    [self._event(chart_path)],
                    authorization="finance_subscription_push",
                )

            self.assertTrue(result["ok"])
            payload = post.call_args.kwargs["json"]
            self.assertEqual(payload["group_id"], 20001)
            self.assertEqual(payload["message"][0]["type"], "image")

    def test_chart_delivery_does_not_bypass_finance_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            chart_path = Path(temp_dir) / "chart.png"
            chart_path.write_bytes(b"not-empty")
            gateway = NapCatQQGateway()
            context = QQMessageContext(
                should_respond=True,
                reason="private_message",
                target_id=10001,
                user_id=10001,
                session_id="qq_10001",
                profile_user_id="qq_10001",
                finance_mode="off",
                clean_message="发图",
                raw_message="发图",
            )

            with patch("companion_v01.qq_gateway.requests.post") as post:
                result = gateway.send_market_charts(
                    context,
                    [self._event(chart_path)],
                    authorization="finance_tool_result",
                )

            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "blocked")
            post.assert_not_called()

    def test_proactive_adapter_passes_subscription_authorization_to_chart_delivery(self) -> None:
        calls = []

        class FakeGateway:
            def send_replies(self, context, messages):
                return {"ok": True, "count": len(messages), "results": [{"ok": True}]}

            def send_market_charts(self, context, events, *, authorization):
                calls.append((context, events, authorization))
                return {"ok": True, "status": "sent", "count": 1, "results": [{"ok": True}]}

        subscription = FinanceSubscription(
            subscription_id="sub-chart",
            client="qq",
            target_id="20001",
            is_group=True,
            session_id="qq_group_shared_20001",
            profile_user_id="qq_group_shared_20001",
            character_pack_id="akane_default",
            finance_mode="push",
            enabled=True,
            filters={"codes": ["000000.TEST"]},
            delivery_policy={"level": "alert"},
        )
        analysis = FinanceAnalysisResult(
            ok=True,
            status="analyzed",
            analysis_id="market_analysis:chart",
            messages=("市场快讯",),
            frame={"tool_events": [{"type": "market_chart_ready"}]},
        )

        with (
            patch.object(config, "FINANCE_ASSISTANT_ENABLED", True),
            patch.object(config, "QQ_FINANCE_PUSH_ENABLED", True),
            patch.object(config, "QQ_BRIDGE_ENABLED", True),
        ):
            result = QQFinanceDeliveryAdapter(FakeGateway()).deliver(
                subscription=subscription,
                analysis=analysis,
            )

        self.assertTrue(result.ok)
        self.assertEqual(calls[0][2], "finance_subscription_push")
        self.assertEqual(calls[0][0].reason, "finance_subscription_push")


if __name__ == "__main__":
    unittest.main()
