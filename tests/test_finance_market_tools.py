from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import config
from companion_v01.capability_registry import CapabilityRegistry
from companion_v01.client_protocol import ClientMode, ClientProtocolContext, QQ_TEXT_DEFAULT_CAPABILITIES
from companion_v01.domain_profiles import FINANCE_DOMAIN_PROFILE_ID
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.engine_services.tool_rounds import (
    max_tool_rounds,
    should_stop_after_tool_events,
    should_stop_for_finance_no_progress,
)
from companion_v01.finance import (
    MarketDataToolService,
    build_market_tool_handlers,
    compute_series_metrics,
)
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.tool_orchestration_engine import build_multi_tool_followup_context
from companion_v01.tool_runtime import ToolExecutionContext
from services.emquant_bridge import EmQuantBridgeRuntime, FakeEmQuantSDK, create_emquant_bridge_app
from services.llm_client import _convert_openai_tools_to_anthropic_tools
from services.market_data import (
    EmQuantBridgeMarketDataProvider,
    MarketDataValidationError,
    MarketEventStore,
    MarketQuoteRequest,
    MarketSeriesRequest,
    MockMarketDataProvider,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "choice_market_data_synthetic_v1.json"
MARKET_ZONE = ZoneInfo("Asia/Shanghai")

SERIES_ROWS = [
    {
        "datetime": "2026-07-08",
        "code": "000000.TEST",
        "OPEN": 98,
        "HIGH": 100,
        "LOW": 97,
        "CLOSE": 99,
        "VOLUME": 900,
        "AMOUNT": 89000,
    },
    {
        "datetime": "2026-07-09",
        "code": "000000.TEST",
        "OPEN": 99,
        "HIGH": 101,
        "LOW": 98,
        "CLOSE": 100,
        "VOLUME": 1000,
        "AMOUNT": 100000,
    },
    {
        "datetime": "2026-07-10",
        "code": "000000.TEST",
        "OPEN": 100,
        "HIGH": 103,
        "LOW": 99,
        "CLOSE": 102,
        "VOLUME": 1500,
        "AMOUNT": 153000,
    },
]


class _TestClientSession:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def request(self, method, url, *, json, headers, timeout, allow_redirects, params=None):
        return self.client.request(
            method,
            url,
            json=json,
            headers=headers,
            params=params,
            follow_redirects=allow_redirects,
        )


class _BrokenSession:
    def request(self, *_args, **_kwargs):
        raise RuntimeError("synthetic bridge outage")


class EmQuantBridgeMarketDataProviderTests(unittest.TestCase):
    def test_provider_polls_and_normalizes_news_callback_events(self) -> None:
        sdk = FakeEmQuantSDK()
        runtime = EmQuantBridgeRuntime(
            enabled=True,
            sdk=sdk,
            provider_id="fake_emquant",
            source_name="Fake EmQuant SDK",
            clock=lambda: 1_752_153_600,
        )
        runtime.register_subscription(
            subscription_id="news-sub-worker",
            kind="news",
            codes=("000000.TEST",),
            fields=("companynews",),
        )
        client = TestClient(create_emquant_bridge_app(runtime, access_token="bridge-secret"))
        client.post("/start", headers={"Authorization": "Bearer bridge-secret"})
        state = runtime.list_subscriptions()[0]
        sdk.emit_news(
            state.serial_id,
            [
                {
                    "datetime": "2026-07-10 09:30:00",
                    "eitime": "2026-07-10 09:31:00",
                    "code": "000000.TEST",
                    "content": "离线合成 callback。",
                    "title": "合成公司披露季度经营数据",
                    "infoCode": "WORKER-NEWS-001",
                    "medianname": "Fake EmQuant SDK",
                    "url": "https://example.invalid/WORKER-NEWS-001",
                    "type": "companynews",
                    "label": "earnings,synthetic",
                }
            ],
        )
        provider = EmQuantBridgeMarketDataProvider(
            access_token="bridge-secret",
            session=_TestClientSession(client),
            clock=lambda: 1_752_153_600,
        )

        batch = provider.poll_market_events(limit=10)

        self.assertTrue(batch.ok, batch.to_public_dict())
        self.assertEqual(batch.status, "ok")
        self.assertEqual(len(batch.events), 1)
        self.assertEqual(batch.events[0].event_id, "choice:WORKER-NEWS-001")
        self.assertEqual(batch.events[0].code, "000000.TEST")
        self.assertEqual(runtime.poll_events(), ())
        self.assertNotIn("bridge-secret", str(batch.to_public_dict()))

    def test_provider_reads_loopback_bridge_and_normalizes_series(self) -> None:
        sdk = FakeEmQuantSDK(series_rows=SERIES_ROWS)
        runtime = EmQuantBridgeRuntime(
            enabled=True,
            sdk=sdk,
            provider_id="fake_emquant",
            source_name="Fake EmQuant SDK",
            clock=lambda: 1_752_153_600,
        )
        client = TestClient(create_emquant_bridge_app(runtime, access_token="bridge-secret"))
        client.post("/start", headers={"Authorization": "Bearer bridge-secret"})
        provider = EmQuantBridgeMarketDataProvider(
            access_token="bridge-secret",
            session=_TestClientSession(client),
            clock=lambda: 1_752_153_600,
        )

        response = provider.get_price_series(
            MarketSeriesRequest(
                code="000000.TEST",
                interval="1d",
                date_from=int(datetime(2026, 7, 8, tzinfo=MARKET_ZONE).timestamp()),
                date_to=int(datetime(2026, 7, 10, 23, 59, 59, tzinfo=MARKET_ZONE).timestamp()),
                limit=10,
            )
        )

        self.assertEqual(response.status, "ok", response.to_public_dict())
        self.assertEqual(response.data.points[-1].close, 102)
        self.assertEqual(response.data.provider, "choice_emquant")
        self.assertNotIn("bridge-secret", str(response.to_public_dict()))

    def test_provider_rejects_non_loopback_url(self) -> None:
        with self.assertRaises(MarketDataValidationError) as raised:
            EmQuantBridgeMarketDataProvider("https://example.com")

        self.assertEqual(raised.exception.field, "base_url")

    def test_bridge_outage_is_unavailable_not_fake_data(self) -> None:
        provider = EmQuantBridgeMarketDataProvider(session=_BrokenSession())

        response = provider.get_quote_snapshots(MarketQuoteRequest(codes=("000000.TEST",)))

        self.assertFalse(response.ok)
        self.assertEqual(response.status, "unavailable")
        self.assertEqual(response.data, ())
        self.assertNotIn("synthetic", response.reason.lower())


class FinanceMarketToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.provider = MockMarketDataProvider.from_fixture_path(FIXTURE_PATH)
        self.service = MarketDataToolService(
            provider=self.provider,
            event_store=MarketEventStore(Path(self.temp_dir.name) / "events.sqlite3"),
            clock=lambda: 1_752_153_600,
        )
        self.service.event_store.upsert_security(
            provider=self.provider.id,
            code="000000.TEST",
            display_name="合成测试公司",
            aliases=("测试公司", "Synthetic Corp"),
            market="TEST",
            security_type="equity",
            source="Synthetic Choice Fixture",
            as_of=1_752_153_600,
            now_ts=1_752_153_600,
        )
        self.handlers = build_market_tool_handlers(self.service)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_handlers_publish_strict_native_schemas_and_finance_metadata(self) -> None:
        specs = build_openai_native_tool_specs(self.handlers)
        names = {item["function"]["name"] for item in specs}

        self.assertEqual(
            names,
            {
                "market_resolve_security",
                "market_news_search",
                "market_quote_snapshot",
                "market_price_series",
            },
        )
        quote_schema = next(item for item in specs if item["function"]["name"] == "market_quote_snapshot")
        self.assertFalse(quote_schema["function"]["parameters"]["additionalProperties"])
        self.assertEqual(quote_schema["function"]["parameters"]["required"], ["codes"])
        anthropic_specs = _convert_openai_tools_to_anthropic_tools(specs)
        anthropic_quote = next(item for item in anthropic_specs if item["name"] == "market_quote_snapshot")
        self.assertEqual(anthropic_quote["input_schema"]["required"], ["codes"])
        self.assertFalse(anthropic_quote["input_schema"]["additionalProperties"])
        for handler in self.handlers.values():
            metadata = handler.tool_metadata()
            self.assertEqual(metadata.family, "finance_read")
            self.assertEqual(metadata.operation, "read")
            self.assertEqual(metadata.default_round_budget, 12)

    def test_resolver_requires_unique_exact_match_before_trusting_code(self) -> None:
        resolver = self.handlers["market_resolve_security"]
        context = ToolExecutionContext(
            profile_user_id="owner",
            session_id="session",
            now_ts=1_752_153_600,
            visual_payload={},
        )

        exact = resolver.execute(
            call=resolver.normalize_call({"type": "market_resolve_security", "query": "测试公司"}),
            context=context,
        )
        partial = resolver.execute(
            call=resolver.normalize_call({"type": "market_resolve_security", "query": "合成"}),
            context=context,
        )

        self.assertIn('"resolved": true', exact.followup_context)
        self.assertIn('"resolved_code": "000000.TEST"', exact.followup_context)
        self.assertIn('"resolution_status": "needs_confirmation"', partial.followup_context)
        self.service.ensure_trusted_codes(
            ("000000.TEST",),
            profile_user_id="owner",
            session_id="session",
        )
        with self.assertRaises(MarketDataValidationError):
            self.service.ensure_trusted_codes(
                ("000000.TEST",),
                profile_user_id="owner",
                session_id="other-session",
            )

    def test_master_code_still_requires_resolver_but_user_literal_is_allowed(self) -> None:
        with self.assertRaises(MarketDataValidationError) as unresolved_master:
            self.service.ensure_trusted_codes(
                ("000000.TEST",),
                profile_user_id="owner",
                session_id="session",
                request_context={"message": "帮我看看测试公司"},
            )

        self.assertEqual(unresolved_master.exception.code, "untrusted_provider_code")
        with self.assertRaises(MarketDataValidationError) as blocked:
            self.service.ensure_trusted_codes(
                ("999999.TEST",),
                profile_user_id="owner",
                session_id="session",
                request_context={"message": "帮我看看测试公司"},
            )

        self.assertEqual(blocked.exception.code, "untrusted_provider_code")
        self.service.ensure_trusted_codes(
            ("999999.TEST",),
            profile_user_id="owner",
            session_id="session",
            request_context={"message": "帮我看看 999999.TEST"},
        )

    def test_legacy_normalization_rejects_unknown_or_invalid_parameters(self) -> None:
        quote = self.handlers["market_quote_snapshot"]
        series = self.handlers["market_price_series"]

        self.assertIsNone(quote.normalize_call({"type": "market_quote_snapshot", "codes": [], "broadcast": True}))
        news = self.handlers["market_news_search"]
        self.assertIsNone(
            news.normalize_call(
                {
                    "type": "market_news_search",
                    "query": "测试",
                    "codes": ["not a code"],
                }
            )
        )
        self.assertIsNone(
            series.normalize_call(
                {"type": "market_price_series", "code": "000000.TEST", "interval": "5m"}
            )
        )
        self.assertIsNone(
            series.normalize_call(
                {"type": "market_price_series", "code": "000000.TEST", "limit": 9999}
            )
        )

    def test_program_metrics_are_deterministic(self) -> None:
        response = self.provider.get_price_series(
            MarketSeriesRequest(code="000000.TEST", interval="1d", limit=3)
        )
        metrics = compute_series_metrics(response.data)

        self.assertEqual(metrics["observation_count"], 3)
        self.assertAlmostEqual(metrics["interval_return_pct"], 2.0, places=6)
        self.assertGreaterEqual(metrics["max_drawdown_pct"], 0)
        self.assertIn("ma5", metrics["moving_averages"])
        self.assertEqual(
            metrics["relative_volume"]["formula"],
            "latest_volume / mean(previous_completed_period_volumes)",
        )

    def test_all_market_handlers_execute_with_source_and_as_of_evidence(self) -> None:
        calls = {
            "market_resolve_security": {
                "type": "market_resolve_security",
                "query": "测试公司",
            },
            "market_news_search": {
                "type": "market_news_search",
                "query": "季度经营",
                "codes": ["000000.TEST"],
                "content_types": ["companynews"],
                "limit": 5,
            },
            "market_quote_snapshot": {
                "type": "market_quote_snapshot",
                "codes": ["000000.TEST"],
            },
            "market_price_series": {
                "type": "market_price_series",
                "code": "000000.TEST",
                "interval": "1d",
                "limit": 3,
            },
        }
        context = ToolExecutionContext(
            profile_user_id="owner",
            session_id="session",
            now_ts=1_752_153_600,
            visual_payload={},
        )

        for name, raw_call in calls.items():
            handler = self.handlers[name]
            normalized = handler.normalize_call(raw_call)
            self.assertIsNotNone(normalized, name)
            result = handler.execute(call=normalized, context=context)
            self.assertEqual(result.stream_events[0]["status"], "ok", result.followup_context)
            self.assertIn('"source":', result.followup_context)
            self.assertIn('"as_of":', result.followup_context)
            self.assertEqual(result.state_updates["finance_evidence"]["status"], "ok")

    def test_finance_tools_expose_only_in_finance_domain(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        engine.capability_registry = CapabilityRegistry()
        engine.tool_handlers = dict(self.handlers)
        engine.store = SimpleNamespace(
            list_attachment_inbox_items=lambda **_kwargs: [],
            list_generated_files=lambda **_kwargs: [],
        )
        context = ClientProtocolContext(
            requested_mode=ClientMode.QQ_TEXT,
            effective_mode=ClientMode.QQ_TEXT,
            capabilities=QQ_TEXT_DEFAULT_CAPABILITIES,
        )

        with patch.object(config, "FINANCE_ASSISTANT_ENABLED", True):
            finance_handlers = engine._resolve_tool_handlers(
                client_context=context,
                profile_user_id="owner",
                session_id="session",
                domain_profile_id=FINANCE_DOMAIN_PROFILE_ID,
            )
            default_handlers = engine._resolve_tool_handlers(
                client_context=context,
                profile_user_id="owner",
                session_id="session",
            )

        self.assertIn("market_quote_snapshot", finance_handlers)
        self.assertIn("market_resolve_security", finance_handlers)
        self.assertNotIn("market_quote_snapshot", default_handlers)

    def test_finance_round_budget_and_stop_handoff_are_explicit(self) -> None:
        with patch.object(config, "FINANCE_ASSISTANT_ENABLED", True), patch.object(
            config, "FINANCE_TOOL_ROUND_BUDGET", 12
        ), patch.object(config, "FINANCE_TOOL_ROUND_HARD_LIMIT", 16):
            self.assertEqual(max_tool_rounds(domain_profile_id=FINANCE_DOMAIN_PROFILE_ID), 12)

        prompt = build_multi_tool_followup_context(
            ["第 1 次工具结果：已有可靠快照"],
            allow_more=False,
            stop_reason="tool_budget_exhausted",
        )
        self.assertIn("完整、可交付的最终回复", prompt)
        self.assertIn("不得只回复", prompt)
        self.assertIn("当前可支持的结论", prompt)
        self.assertTrue(should_stop_after_tool_events([{"status": "permission_denied"}]))
        repeated = [
            SimpleNamespace(
                state_updates={
                    "finance_evidence": {
                        "status": "ok",
                        "result_hash": "same-result",
                    }
                }
            ),
            SimpleNamespace(
                state_updates={
                    "finance_evidence": {
                        "status": "ok",
                        "result_hash": "same-result",
                    }
                }
            ),
        ]
        self.assertTrue(should_stop_for_finance_no_progress(repeated))


if __name__ == "__main__":
    unittest.main()
