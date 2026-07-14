from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import config

from companion_v01.finance import (
    AkaneFinanceAnalysisClient,
    FinanceAnalysisResult,
    FinanceDeliveryAuthorization,
    FinanceDeliveryResult,
    FinanceEventOrchestrator,
    ensure_market_push_contract,
)
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.engine_services.turn_context import is_transient_user_turn
from companion_v01.memcore_integration.manager import MemcoreManager
from services.market_data import MarketEvent, MarketEventStore


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _event(*, event_id: str = "choice:finance-f6-001", title: str = "合成公司披露季度经营数据") -> MarketEvent:
    return MarketEvent(
        provider="mock_choice",
        event_id=event_id,
        published_at=1_752_110_000,
        produced_at=1_752_110_030,
        received_at=1_752_110_060,
        code="000000.TEST",
        content_type="companynews",
        title=title,
        source="Synthetic Choice Fixture",
        url=f"https://example.invalid/{event_id}",
        sentiment="unknown",
        labels=("earnings", "synthetic"),
        sector_code="",
        raw_hash=_hash(event_id),
    )


class _FakeAnalysisClient:
    def __init__(self, *, tool_events=None) -> None:
        self.requests = []
        self.tool_events = list(tool_events or [])

    def analyze(self, request):
        self.requests.append(request)
        messages = ensure_market_push_contract(
            request=request,
            frame={"speech": "这条事件可能影响市场预期，但还需要结合完整正文和行情核验。"},
        )
        return FinanceAnalysisResult(
            ok=True,
            status="analyzed",
            analysis_id=request.analysis_id,
            messages=messages,
            frame={"speech": "\n".join(messages), "tool_events": list(self.tool_events)},
        )


class _FakeDeliveryAdapter:
    def __init__(self, *, authorized: bool = True, failures: int = 0, part_failures=None) -> None:
        self.authorized = authorized
        self.failures = failures
        self.part_failures = dict(part_failures or {})
        self.authorizations = []
        self.deliveries = []

    def authorize(self, subscription):
        self.authorizations.append(subscription)
        return FinanceDeliveryAuthorization(
            self.authorized,
            "authorized" if self.authorized else "disabled",
            "" if self.authorized else "push_disabled",
        )

    def deliver_part(self, *, subscription, part):
        self.deliveries.append((subscription, part))
        if self.failures > 0:
            self.failures -= 1
            return FinanceDeliveryResult(False, "failed", "qq_unreachable")
        remaining = int(self.part_failures.get(part.part_type) or 0)
        if remaining > 0:
            self.part_failures[part.part_type] = remaining - 1
            return FinanceDeliveryResult(False, "failed", f"{part.part_type}_unreachable")
        return FinanceDeliveryResult(True, "delivered")


class FinanceEventOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = MarketEventStore(Path(self.temp_dir.name) / "finance_events.sqlite3")
        self.subscription = self.store.upsert_subscription(
            subscription_id="sub-f6-group",
            client="qq",
            target_id="20001",
            is_group=True,
            session_id="qq_group_shared_20001",
            profile_user_id="qq_group_shared_20001",
            character_pack_id="akane_default",
            finance_mode="push",
            enabled=True,
            filters={"codes": ["000000.TEST"]},
            delivery_policy={"level": "notify"},
            created_by_actor_id="qq:10001",
            now_ts=100,
        )

    def _orchestrator(self, *, authorized: bool = True, failures: int = 0, tool_events=None, part_failures=None):
        analysis = _FakeAnalysisClient(tool_events=tool_events)
        delivery = _FakeDeliveryAdapter(
            authorized=authorized,
            failures=failures,
            part_failures=part_failures,
        )
        orchestrator = FinanceEventOrchestrator(
            store=self.store,
            analysis_client=analysis,
            delivery_adapter=delivery,
            clock=lambda: 1_752_111_000,
        )
        return orchestrator, analysis, delivery

    def test_event_to_analysis_to_delivery_is_idempotent(self) -> None:
        orchestrator, analysis, delivery = self._orchestrator()

        first = orchestrator.process_event(_event(), now_ts=200)
        replay = orchestrator.process_event(_event(), now_ts=300)

        self.assertEqual(first.delivered_count, 1)
        self.assertEqual(first.delivery_results[0].status, "delivered")
        self.assertEqual(replay.delivery_results[0].status, "already_delivered")
        self.assertEqual(len(analysis.requests), 1)
        self.assertEqual(len(delivery.deliveries), 1)
        request = analysis.requests[0]
        payload = request.to_turn_payload()
        self.assertEqual(payload["turn_kind"], "market_event")
        self.assertEqual(payload["client_turn_kind"], "proactive")
        self.assertTrue(payload["transient_user_message"])
        self.assertTrue(payload["transient_assistant_message"])
        self.assertFalse(AkaneMemoryEngine._should_persist_assistant_turn(payload))
        self.assertTrue(AkaneMemoryEngine._should_persist_assistant_turn({}))
        self.assertEqual(payload["finance_mode"], "push")
        self.assertEqual(payload["prompt_scope"], "finance_push")
        self.assertFalse(payload["pre_retrieval_enabled"])
        self.assertIn("本次财经推送参数", payload["extra_context"])
        self.assertNotIn("具体比例、价格、涨跌幅", payload["extra_context"])
        self.assertTrue(is_transient_user_turn(payload))
        self.assertIn("【外部市场事件，不是用户发言】", payload["message"])
        self.assertIn("000000.TEST", payload["message"])
        self.assertNotIn("actor_stable_id", payload)
        pushed = str(delivery.deliveries[0][1].payload.get("message") or "")
        self.assertEqual(delivery.deliveries[0][1].payload.get("analysis_status"), "analyzed")
        self.assertEqual(delivery.deliveries[0][1].payload.get("analysis_attempts"), 1)
        self.assertIn("已确认事实", pushed)
        self.assertIn("分析推断", pushed)
        self.assertIn("尚待验证与风险", pushed)
        self.assertIn("来源：", pushed)
        self.assertIn("发布时间：", pushed)

    def test_failed_qq_delivery_remains_retryable(self) -> None:
        orchestrator, analysis, delivery = self._orchestrator(failures=1)

        first = orchestrator.process_event(_event(event_id="choice:finance-f6-retry"), now_ts=200)
        retry = orchestrator.retry_pending(now_ts=300)

        self.assertEqual(first.delivery_results[0].status, "failed")
        self.assertEqual(retry[0].status, "delivered")
        self.assertEqual(len(analysis.requests), 1)
        self.assertEqual(len(delivery.deliveries), 2)
        stored = self.store.get_delivery(
            event_id="choice:finance-f6-retry",
            subscription_id=self.subscription.subscription_id,
        )
        self.assertEqual(stored.status, "delivered")
        self.assertEqual(stored.attempt_count, 2)

    def test_artifact_retry_does_not_repeat_delivered_text_or_rerun_analysis(self) -> None:
        chart_event = {
            "type": "market_chart_ready",
            "delivery_scope": "finance_market_chart",
            "send_to_user": True,
            "generated_file": {"generated_id": "chart-retry-test"},
        }
        orchestrator, analysis, delivery = self._orchestrator(
            tool_events=[chart_event],
            part_failures={"chart": 1},
        )

        first_event = _event(event_id="choice:finance-f9-parts")
        first = orchestrator.process_event(first_event, now_ts=200)
        clustered = orchestrator.process_event(
            replace(
                first_event,
                event_id="choice:finance-f9-parts-clustered",
                title=f"{first_event.title} 更新",
                published_at=first_event.published_at + 60,
                raw_hash=_hash("choice:finance-f9-parts-clustered"),
            ),
            now_ts=250,
        )
        retry = orchestrator.retry_pending(now_ts=300)

        self.assertEqual(first.delivery_results[0].status, "failed")
        self.assertEqual(clustered.delivery_results[0].status, "duplicate_cluster")
        self.assertEqual(retry[0].status, "delivered")
        self.assertEqual(len(analysis.requests), 1)
        self.assertEqual([part.part_type for _, part in delivery.deliveries], ["text", "chart", "chart"])
        parts = self.store.list_delivery_parts(
            event_id="choice:finance-f9-parts",
            subscription_id=self.subscription.subscription_id,
        )
        by_type = {part.part_type: part for part in parts}
        self.assertEqual(by_type["text"].status, "delivered")
        self.assertEqual(by_type["text"].attempt_count, 1)
        self.assertEqual(by_type["chart"].status, "delivered")
        self.assertEqual(by_type["chart"].attempt_count, 2)

    def test_exhausted_analysis_is_not_retried_every_worker_cycle(self) -> None:
        class ExhaustedAnalysis:
            def __init__(self):
                self.requests = []

            def analyze(inner_self, request):
                inner_self.requests.append(request)
                return FinanceAnalysisResult(
                    ok=False,
                    status="transient_fallback_analysis",
                    analysis_id=request.analysis_id,
                    reason="transient fallback; exhausted 3 analysis attempt(s)",
                    analysis_attempts=3,
                )

        analysis = ExhaustedAnalysis()
        delivery = _FakeDeliveryAdapter()
        orchestrator = FinanceEventOrchestrator(
            store=self.store,
            analysis_client=analysis,
            delivery_adapter=delivery,
        )

        first = orchestrator.process_event(_event(event_id="choice:finance-f9-exhausted"), now_ts=200)
        retry = orchestrator.retry_pending(now_ts=300)
        stored = self.store.get_delivery(
            event_id="choice:finance-f9-exhausted",
            subscription_id=self.subscription.subscription_id,
        )

        self.assertEqual(first.delivery_results[0].status, "failed")
        self.assertTrue(stored.reason.startswith("analysis_exhausted:"))
        self.assertEqual(retry, ())
        self.assertEqual(len(analysis.requests), 1)
        self.assertEqual(delivery.deliveries, [])

    def test_disabled_push_does_not_spend_analysis_or_create_delivery(self) -> None:
        orchestrator, analysis, delivery = self._orchestrator(authorized=False)

        result = orchestrator.process_event(_event(event_id="choice:finance-f6-disabled"), now_ts=200)

        self.assertEqual(result.delivery_results[0].status, "unauthorized")
        self.assertEqual(analysis.requests, [])
        self.assertEqual(delivery.deliveries, [])
        self.assertIsNone(
            self.store.get_delivery(
                event_id="choice:finance-f6-disabled",
                subscription_id=self.subscription.subscription_id,
            )
        )

    def test_subscription_disabled_during_analysis_prevents_qq_send(self) -> None:
        delivery = _FakeDeliveryAdapter()

        class DisablingAnalysis(_FakeAnalysisClient):
            def analyze(inner_self, request):
                result = super().analyze(request)
                self.store.set_subscription_enabled(
                    self.subscription.subscription_id,
                    enabled=False,
                    now_ts=250,
                )
                return result

        analysis = DisablingAnalysis()
        orchestrator = FinanceEventOrchestrator(
            store=self.store,
            analysis_client=analysis,
            delivery_adapter=delivery,
        )

        result = orchestrator.process_event(_event(event_id="choice:finance-f6-cancelled"), now_ts=200)

        self.assertEqual(result.delivery_results[0].status, "cancelled")
        self.assertEqual(delivery.deliveries, [])
        stored = self.store.get_delivery(
            event_id="choice:finance-f6-cancelled",
            subscription_id=self.subscription.subscription_id,
        )
        self.assertEqual(stored.status, "cancelled")

    def test_low_importance_event_is_archived_without_ai(self) -> None:
        orchestrator, analysis, delivery = self._orchestrator()
        low = replace(
            _event(event_id="choice:finance-f6-low", title="合成公司普通资讯"),
            content_type="misc",
            labels=("synthetic",),
        )

        result = orchestrator.process_event(low, now_ts=200)

        self.assertEqual(result.delivery_results[0].status, "archived")
        self.assertEqual(analysis.requests, [])
        self.assertEqual(delivery.deliveries, [])

    def test_same_cluster_is_not_pushed_twice(self) -> None:
        orchestrator, analysis, delivery = self._orchestrator()
        first_event = _event(event_id="choice:finance-f6-cluster-a")
        second_event = replace(
            first_event,
            event_id="choice:finance-f6-cluster-b",
            title=f"{first_event.title} 更新",
            published_at=first_event.published_at + 60,
            raw_hash=_hash("choice:finance-f6-cluster-b"),
        )

        first = orchestrator.process_event(first_event, now_ts=200)
        second = orchestrator.process_event(second_event, now_ts=300)

        self.assertEqual(first.delivery_results[0].status, "delivered")
        self.assertEqual(second.delivery_results[0].status, "duplicate_cluster")
        self.assertEqual(len(analysis.requests), 1)
        self.assertEqual(len(delivery.deliveries), 1)


class FinanceAnalysisClientTests(unittest.TestCase):
    def _request(self, event_id: str):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        store = MarketEventStore(Path(temp_dir.name) / "analysis_retry.sqlite3")
        event_result = store.upsert_event(_event(event_id=event_id), now_ts=100)
        subscription = store.upsert_subscription(
            subscription_id=f"sub-{event_id.rsplit(':', 1)[-1]}",
            client="qq",
            target_id="20001",
            is_group=True,
            session_id="qq_group_shared_20001",
            profile_user_id="qq_group_shared_20001",
            finance_mode="push",
            enabled=True,
            filters={"codes": ["000000.TEST"]},
            delivery_policy={"level": "notify"},
            now_ts=100,
        )
        from companion_v01.finance import FinanceAnalysisRequest, FinanceEventImportancePolicy

        decision = FinanceEventImportancePolicy().evaluate(event=event_result.record.event, subscription=subscription)
        return FinanceAnalysisRequest.create(
            event_record=event_result.record,
            subscription=subscription,
            importance=decision,
            requested_at=1_752_111_000,
        )

    def test_persona_fallback_retries_and_only_final_analysis_enters_memcore(self) -> None:
        request = self._request("choice:finance-f9-fallback-retry")

        class FakeMemcore:
            enabled = True

            def __init__(self):
                self.calls = []

            def record_tool_exchange(self, **kwargs):
                self.calls.append(("tool", kwargs))
                return {"ok": True, "status": "recorded"}

            def record_assistant_turn(self, record, **kwargs):
                self.calls.append(("assistant", record, kwargs))
                return {"ok": True, "status": "recorded"}

            def compact_due_background(self, **kwargs):
                self.calls.append(("compact", kwargs))
                return {"ok": True, "status": "scheduled"}

        class FakeEngine:
            def __init__(self):
                self.memcore_manager = FakeMemcore()
                self.outputs = [
                    {"speech": "我在认真听你说，要不要再多告诉我一点？"},
                    {"speech": "我在认真听你说，要不要再多告诉我一点？"},
                    {"speech": "事件可能影响短期预期，但必须继续核验完整正文与带时间戳的行情。"},
                ]
                self.payloads = []

            def process_turn(self, payload):
                self.payloads.append(payload)
                return self.outputs.pop(0)

        engine = FakeEngine()
        result = AkaneFinanceAnalysisClient(
            engine,
            max_attempts=3,
            retry_backoff_seconds=0,
        ).analyze(request)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "analyzed_after_retry")
        self.assertEqual(result.analysis_attempts, 3)
        self.assertEqual(len(engine.payloads), 3)
        self.assertEqual([item[0] for item in engine.memcore_manager.calls], ["tool", "assistant", "compact"])
        stored_content = engine.memcore_manager.calls[1][1]["content"]
        self.assertNotIn("我在认真听你说", stored_content)

    def test_exhausted_fallback_attempts_never_pollute_memcore(self) -> None:
        request = self._request("choice:finance-f9-fallback-exhausted")

        class FakeMemcore:
            enabled = True

            def __init__(self):
                self.calls = []

        class FakeEngine:
            def __init__(self):
                self.memcore_manager = FakeMemcore()
                self.calls = 0

            def process_turn(self, _payload):
                self.calls += 1
                return {"speech": "我在认真听你说，要不要再多告诉我一点？"}

        engine = FakeEngine()
        result = AkaneFinanceAnalysisClient(
            engine,
            max_attempts=3,
            retry_backoff_seconds=0,
        ).analyze(request)

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "transient_fallback_analysis")
        self.assertEqual(result.analysis_attempts, 3)
        self.assertEqual(engine.calls, 3)
        self.assertEqual(engine.memcore_manager.calls, [])

    def test_validated_quote_push_reinjects_authoritative_facts(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        store = MarketEventStore(Path(temp_dir.name) / "validated_quote.sqlite3")
        title = "513000.SH 公开聚合盘中快照：上涨1.30%，最新价 1.013，昨收 1，数据时间 2026-07-10T14:29:45+08:00"
        event = MarketEvent(
            provider="public_market",
            event_id="public_quote:test",
            published_at=1_783_667_400,
            produced_at=1_783_667_390,
            received_at=1_783_667_400,
            code="513000.SH",
            content_type="quote_move",
            title=title,
            source="AkShare/Eastmoney public web data",
            url="",
            sentiment="positive",
            labels=("validated_quote", "deterministic_calculation", "public_intraday_snapshot"),
            sector_code="",
            raw_hash=_hash("public_quote:test"),
        )
        record = store.upsert_event(event, now_ts=1_783_667_400).record
        subscription = store.upsert_subscription(
            subscription_id="validated-quote-sub",
            client="qq",
            target_id="872732158",
            is_group=True,
            session_id="qq_group_shared_872732158",
            profile_user_id="qq_group_shared_872732158",
            finance_mode="push",
            enabled=True,
            delivery_policy={"level": "notify"},
            now_ts=1_783_667_400,
        )
        from companion_v01.finance import FinanceAnalysisRequest, FinanceEventImportancePolicy

        decision = FinanceEventImportancePolicy().evaluate(event=event, subscription=subscription)
        request = FinanceAnalysisRequest.create(
            event_record=record,
            subscription=subscription,
            importance=decision,
            requested_at=1_783_667_410,
        )

        messages = ensure_market_push_contract(
            request=request,
            frame={"speech": "可能反映跨境 ETF 风险偏好变化，但需继续观察。"},
        )

        combined = "\n".join(messages)
        self.assertIn(f"已确认事实：{title}", combined)
        self.assertIn("模型解释不能修改上述价格", combined)
        self.assertIn("标题是本次推送唯一权威行情事实", request.render_analysis_instruction())

    def test_direct_news_relay_survives_optional_analysis_refusal(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        store = MarketEventStore(Path(temp_dir.name) / "direct_relay.sqlite3")
        event = MarketEvent(
            provider="public_market",
            event_id="public_news:test-relay",
            published_at=1_783_667_400,
            produced_at=1_783_667_405,
            received_at=1_783_667_410,
            code="GLOBAL.MARKET",
            content_type="news_flash",
            title="东方财富7×24快讯：特朗普表示将公布新的经济政策",
            source="东方财富 7×24 全球财经快讯",
            url="https://finance.eastmoney.com/a/test-relay.html",
            sentiment="unknown",
            labels=(
                "direct_relay",
                "optional_model_analysis",
                "source_report_only",
                "market_wide",
            ),
            sector_code="",
            raw_hash=_hash("public_news:test-relay"),
        )
        record = store.upsert_event(event, now_ts=1_783_667_410).record
        subscription = store.upsert_subscription(
            subscription_id="direct-relay-sub",
            client="qq",
            target_id="872732158",
            is_group=True,
            session_id="qq_group_shared_872732158",
            profile_user_id="qq_group_shared_872732158",
            finance_mode="push",
            enabled=True,
            filters={"include_market_wide": True},
            delivery_policy={"level": "notify"},
            now_ts=1_783_667_410,
        )
        from companion_v01.finance import FinanceAnalysisRequest, FinanceEventImportancePolicy

        request = FinanceAnalysisRequest.create(
            event_record=record,
            subscription=subscription,
            importance=FinanceEventImportancePolicy().evaluate(event=event, subscription=subscription),
            requested_at=1_783_667_420,
        )

        class RefusingEngine:
            memcore_manager = None

            def process_turn(self, _payload):
                return {"speech": "抱歉，我无法分析这条消息。"}

        result = AkaneFinanceAnalysisClient(
            RefusingEngine(),
            max_attempts=2,
            retry_backoff_seconds=0,
        ).analyze(request)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "relayed_without_analysis")
        self.assertEqual(len(result.messages), 1)
        self.assertIn("东方财富 7×24 快讯原文转发", result.messages[0])
        self.assertIn(event.title, result.messages[0])
        self.assertNotIn("抱歉", result.messages[0])

    def test_direct_news_analysis_keeps_original_link_in_analysis_message(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        store = MarketEventStore(Path(temp_dir.name) / "direct_relay_link.sqlite3")
        event = MarketEvent(
            provider="public_market",
            event_id="public_news:test-analysis-link",
            published_at=1_783_667_400,
            produced_at=1_783_667_405,
            received_at=1_783_667_410,
            code="GLOBAL.MARKET",
            content_type="news_flash",
            title="东方财富7×24快讯：特朗普表示将公布新的经济政策",
            source="东方财富 7×24 全球财经快讯",
            url="https://finance.eastmoney.com/a/test-analysis-link.html",
            sentiment="unknown",
            labels=("direct_relay", "optional_model_analysis", "source_report_only", "market_wide"),
            sector_code="",
            raw_hash=_hash("public_news:test-analysis-link"),
        )
        record = store.upsert_event(event, now_ts=1_783_667_410).record
        subscription = store.upsert_subscription(
            subscription_id="direct-relay-link-sub",
            client="qq",
            target_id="872732158",
            is_group=True,
            session_id="qq_group_shared_872732158",
            profile_user_id="qq_group_shared_872732158",
            finance_mode="push",
            enabled=True,
            filters={"include_market_wide": True},
            delivery_policy={"level": "notify"},
            now_ts=1_783_667_410,
        )
        from companion_v01.finance import FinanceAnalysisRequest, FinanceEventImportancePolicy

        request = FinanceAnalysisRequest.create(
            event_record=record,
            subscription=subscription,
            importance=FinanceEventImportancePolicy().evaluate(event=event, subscription=subscription),
            requested_at=1_783_667_420,
        )

        class AnalyzingEngine:
            memcore_manager = None

            def process_turn(self, _payload):
                return {"speech": "这可能影响短期风险偏好，但政策细节和市场反应仍需继续核验。"}

        result = AkaneFinanceAnalysisClient(
            AnalyzingEngine(),
            max_attempts=1,
            retry_backoff_seconds=0,
        ).analyze(request)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "analyzed")
        self.assertEqual(len(result.messages), 1)
        self.assertNotIn("东方财富 7×24 快讯原文转发", result.messages[0])
        self.assertNotIn(event.title, result.messages[0])
        self.assertNotIn("尚待验证与风险", result.messages[0])
        self.assertNotIn("接下来观察", result.messages[0])
        self.assertNotIn("来源：", result.messages[0])
        self.assertNotIn("发布时间：", result.messages[0])
        self.assertEqual(result.messages[0].count(event.url), 1)
        self.assertTrue(result.messages[0].endswith(f"原文链接：{event.url}"))
        instruction = request.render_analysis_instruction()
        self.assertIn("具体比例、价格、涨跌幅", instruction)
        self.assertIn("新闻的发布时间当成数据统计时点", instruction)
        self.assertIn("强行套用 A 股、美股", instruction)
        self.assertIn("没有有意义的金融或市场传导", instruction)
        self.assertIn("结论成立条件与可能的反向情形", instruction)

    def test_direct_news_analysis_retries_unsupported_timed_comparison_with_feedback(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        store = MarketEventStore(Path(temp_dir.name) / "direct_relay_evidence_retry.sqlite3")
        event = MarketEvent(
            provider="public_market",
            event_id="public_news:test-evidence-retry",
            published_at=1_783_667_400,
            produced_at=1_783_667_405,
            received_at=1_783_667_410,
            code="GLOBAL.MARKET",
            content_type="news_flash",
            title="东方财富7×24快讯：国内航司计划取消明日航班超2800架次",
            source="东方财富 7×24 全球财经快讯",
            url="https://finance.eastmoney.com/a/test-evidence-retry.html",
            sentiment="unknown",
            labels=("direct_relay", "optional_model_analysis", "source_report_only", "market_wide"),
            sector_code="",
            raw_hash=_hash("public_news:test-evidence-retry"),
        )
        record = store.upsert_event(event, now_ts=1_783_667_410).record
        subscription = store.upsert_subscription(
            subscription_id="direct-relay-evidence-retry-sub",
            client="qq",
            target_id="872732158",
            is_group=True,
            session_id="qq_group_shared_872732158",
            profile_user_id="qq_group_shared_872732158",
            finance_mode="push",
            enabled=True,
            filters={"include_market_wide": True},
            delivery_policy={"level": "notify"},
            now_ts=1_783_667_410,
        )
        from companion_v01.finance import FinanceAnalysisRequest, FinanceEventImportancePolicy

        request = FinanceAnalysisRequest.create(
            event_record=record,
            subscription=subscription,
            importance=FinanceEventImportancePolicy().evaluate(event=event, subscription=subscription),
            requested_at=1_783_667_420,
        )

        class RetryEngine:
            memcore_manager = None

            def __init__(self):
                self.payloads = []

            def process_turn(self, payload):
                self.payloads.append(payload)
                if len(self.payloads) == 1:
                    return {"speech": "航班取消量两小时内从1000架次跳到2800架次，涨了近三倍。"}
                return {
                    "speech": "新快讯称，国内航司计划取消明日进出港航班超过2800架次，具体统计口径和时点仍以原文为准。周一开盘再观察市场反应。"
                }

        engine = RetryEngine()
        result = AkaneFinanceAnalysisClient(
            engine,
            max_attempts=2,
            retry_backoff_seconds=0,
        ).analyze(request)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "analyzed_after_retry")
        self.assertEqual(result.analysis_attempts, 2)
        self.assertEqual(len(engine.payloads), 2)
        self.assertIn("上一次输出未通过发送前证据门禁", engine.payloads[1]["extra_context"])
        self.assertIn("同口径、同统计时点", engine.payloads[1]["extra_context"])
        self.assertNotIn("1000", "\n".join(result.messages))

    def test_direct_news_analysis_allows_precise_claim_after_successful_search(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        store = MarketEventStore(Path(temp_dir.name) / "direct_relay_evidence_tool.sqlite3")
        event = MarketEvent(
            provider="public_market",
            event_id="public_news:test-evidence-tool",
            published_at=1_783_667_400,
            produced_at=1_783_667_405,
            received_at=1_783_667_410,
            code="GLOBAL.MARKET",
            content_type="news_flash",
            title="东方财富7×24快讯：海外运输通道出现新进展",
            source="东方财富 7×24 全球财经快讯",
            url="https://finance.eastmoney.com/a/test-evidence-tool.html",
            sentiment="unknown",
            labels=("direct_relay", "optional_model_analysis", "source_report_only", "market_wide"),
            sector_code="",
            raw_hash=_hash("public_news:test-evidence-tool"),
        )
        record = store.upsert_event(event, now_ts=1_783_667_410).record
        subscription = store.upsert_subscription(
            subscription_id="direct-relay-evidence-tool-sub",
            client="qq",
            target_id="872732158",
            is_group=True,
            session_id="qq_group_shared_872732158",
            profile_user_id="qq_group_shared_872732158",
            finance_mode="push",
            enabled=True,
            filters={"include_market_wide": True},
            delivery_policy={"level": "notify"},
            now_ts=1_783_667_410,
        )
        from companion_v01.finance import FinanceAnalysisRequest, FinanceEventImportancePolicy

        request = FinanceAnalysisRequest.create(
            event_record=record,
            subscription=subscription,
            importance=FinanceEventImportancePolicy().evaluate(event=event, subscription=subscription),
            requested_at=1_783_667_420,
        )

        class EvidenceEngine:
            memcore_manager = None

            def process_turn(self, _payload):
                return {
                    "speech": "经补充核验，该通道承担约20%的相关运输量；该比例仍应结合后续官方数据观察。",
                    "tool_events": [{"type": "web_search_completed", "status": "ok", "provider": "anysearch"}],
                }

        result = AkaneFinanceAnalysisClient(
            EvidenceEngine(),
            max_attempts=1,
            retry_backoff_seconds=0,
        ).analyze(request)

        self.assertTrue(result.ok)
        self.assertIn("20%", "\n".join(result.messages))

    def test_direct_news_analysis_removes_duplicate_metadata_and_template_labels(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        store = MarketEventStore(Path(temp_dir.name) / "direct_relay_cleanup.sqlite3")
        event = MarketEvent(
            provider="public_market",
            event_id="public_news:test-analysis-cleanup",
            published_at=1_783_667_400,
            produced_at=1_783_667_405,
            received_at=1_783_667_410,
            code="GLOBAL.MARKET",
            content_type="news_flash",
            title="东方财富7×24快讯：海外市场出现新的政策信号",
            source="东方财富 7×24 全球财经快讯",
            url="https://finance.eastmoney.com/a/test-analysis-cleanup.html",
            sentiment="unknown",
            labels=("direct_relay", "optional_model_analysis", "source_report_only", "market_wide"),
            sector_code="",
            raw_hash=_hash("public_news:test-analysis-cleanup"),
        )
        record = store.upsert_event(event, now_ts=1_783_667_410).record
        subscription = store.upsert_subscription(
            subscription_id="direct-relay-cleanup-sub",
            client="qq",
            target_id="872732158",
            is_group=True,
            session_id="qq_group_shared_872732158",
            profile_user_id="qq_group_shared_872732158",
            finance_mode="push",
            enabled=True,
            filters={"include_market_wide": True},
            delivery_policy={"level": "notify"},
            now_ts=1_783_667_410,
        )
        from companion_v01.finance import FinanceAnalysisRequest, FinanceEventImportancePolicy

        request = FinanceAnalysisRequest.create(
            event_record=record,
            subscription=subscription,
            importance=FinanceEventImportancePolicy().evaluate(event=event, subscription=subscription),
            requested_at=1_783_667_420,
        )

        class VerboseEngine:
            memcore_manager = None

            def process_turn(self, _payload):
                return {
                    "speech": "\n".join(
                        [
                            "【快讯|22:46】",
                            "已确认事实：海外市场出现新的政策信号。",
                            "分析推断：短期可能影响风险偏好，但仍需核验政策细节。",
                            "来源：东方财富 7×24 全球财经快讯｜发布时间：2026-07-11T21:58:40+08:00",
                            f"原文链接：{event.url}",
                            "接下来观察：等待更多信息。",
                        ]
                    )
                }

        result = AkaneFinanceAnalysisClient(
            VerboseEngine(),
            max_attempts=1,
            retry_backoff_seconds=0,
        ).analyze(request)

        combined = "\n".join(result.messages)
        self.assertTrue(result.ok)
        self.assertIn("海外市场出现新的政策信号", combined)
        self.assertIn("短期可能影响风险偏好", combined)
        self.assertNotIn("已确认事实：", combined)
        self.assertNotIn("分析推断：", combined)
        self.assertNotIn("发布时间：", combined)
        self.assertNotIn("【快讯|22:46】", combined)
        self.assertEqual(combined.count("【财经快讯｜"), 1)
        self.assertNotIn("等待更多信息", combined)
        self.assertEqual(combined.count(event.url), 1)
        self.assertTrue(combined.endswith(f"原文链接：{event.url}"))

    def test_direct_news_analysis_can_be_disabled_without_calling_engine(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        store = MarketEventStore(Path(temp_dir.name) / "direct_relay_disabled.sqlite3")
        event = MarketEvent(
            provider="public_market",
            event_id="public_news:test-analysis-disabled",
            published_at=1_783_667_400,
            produced_at=1_783_667_405,
            received_at=1_783_667_410,
            code="GLOBAL.MARKET",
            content_type="news_flash",
            title="东方财富7×24快讯：海外市场公布新的经济数据",
            source="东方财富 7×24 全球财经快讯",
            url="https://finance.eastmoney.com/a/test-analysis-disabled.html",
            sentiment="unknown",
            labels=("direct_relay", "optional_model_analysis", "source_report_only", "market_wide"),
            sector_code="",
            raw_hash=_hash("public_news:test-analysis-disabled"),
        )
        record = store.upsert_event(event, now_ts=1_783_667_410).record
        subscription = store.upsert_subscription(
            subscription_id="direct-relay-disabled-sub",
            client="qq",
            target_id="872732158",
            is_group=True,
            session_id="qq_group_shared_872732158",
            profile_user_id="qq_group_shared_872732158",
            finance_mode="push",
            enabled=True,
            filters={"include_market_wide": True},
            delivery_policy={"level": "notify"},
            now_ts=1_783_667_410,
        )
        from companion_v01.finance import FinanceAnalysisRequest, FinanceEventImportancePolicy

        request = FinanceAnalysisRequest.create(
            event_record=record,
            subscription=subscription,
            importance=FinanceEventImportancePolicy().evaluate(event=event, subscription=subscription),
            requested_at=1_783_667_420,
        )

        class UnexpectedEngine:
            memcore_manager = None

            def process_turn(self, _payload):
                raise AssertionError("engine must not be called when optional analysis is disabled")

        with patch.object(config, "FINANCE_PUBLIC_NEWS_MODEL_ANALYSIS_ENABLED", False):
            result = AkaneFinanceAnalysisClient(UnexpectedEngine()).analyze(request)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "relayed_without_analysis")
        self.assertEqual(len(result.messages), 1)
        self.assertIn(event.url, result.messages[0])

    def test_direct_news_analysis_reintroducing_sensitive_subject_is_omitted(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        store = MarketEventStore(Path(temp_dir.name) / "direct_relay_output_policy.sqlite3")
        event = MarketEvent(
            provider="public_market",
            event_id="public_news:test-analysis-policy",
            published_at=1_783_667_400,
            produced_at=1_783_667_405,
            received_at=1_783_667_410,
            code="GLOBAL.MARKET",
            content_type="news_flash",
            title="东方财富7×24快讯：海外市场风险偏好出现变化",
            source="东方财富 7×24 全球财经快讯",
            url="https://finance.eastmoney.com/a/test-analysis-policy.html",
            sentiment="unknown",
            labels=("direct_relay", "optional_model_analysis", "source_report_only", "market_wide"),
            sector_code="",
            raw_hash=_hash("public_news:test-analysis-policy"),
        )
        record = store.upsert_event(event, now_ts=1_783_667_410).record
        subscription = store.upsert_subscription(
            subscription_id="direct-relay-output-policy-sub",
            client="qq",
            target_id="872732158",
            is_group=True,
            session_id="qq_group_shared_872732158",
            profile_user_id="qq_group_shared_872732158",
            finance_mode="push",
            enabled=True,
            filters={"include_market_wide": True},
            delivery_policy={"level": "notify"},
            now_ts=1_783_667_410,
        )
        from companion_v01.finance import FinanceAnalysisRequest, FinanceEventImportancePolicy

        request = FinanceAnalysisRequest.create(
            event_record=record,
            subscription=subscription,
            importance=FinanceEventImportancePolicy().evaluate(event=event, subscription=subscription),
            requested_at=1_783_667_420,
        )

        class UnsafeAnalysisEngine:
            memcore_manager = None

            def process_turn(self, _payload):
                return {"speech": "分析中意外引入中共中央政治局相关内容。"}

        result = AkaneFinanceAnalysisClient(
            UnsafeAnalysisEngine(),
            max_attempts=1,
            retry_backoff_seconds=0,
        ).analyze(request)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "relayed_without_analysis")
        self.assertEqual(len(result.messages), 1)
        self.assertNotIn("政治局", result.messages[0])
        self.assertIn(event.url, result.messages[0])

    def test_engine_analysis_records_tool_trace_and_assistant_not_user(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        store = MarketEventStore(Path(temp_dir.name) / "analysis.sqlite3")
        event_result = store.upsert_event(_event(event_id="choice:finance-f6-memory"), now_ts=100)
        subscription = store.upsert_subscription(
            subscription_id="sub-memory",
            client="qq",
            target_id="20001",
            is_group=True,
            session_id="qq_group_shared_20001",
            profile_user_id="qq_group_shared_20001",
            finance_mode="push",
            enabled=True,
            filters={"codes": ["000000.TEST"]},
            delivery_policy={"level": "notify"},
            now_ts=100,
        )
        from companion_v01.finance import FinanceEventImportancePolicy

        decision = FinanceEventImportancePolicy().evaluate(event=event_result.record.event, subscription=subscription)
        from companion_v01.finance import FinanceAnalysisRequest

        request = FinanceAnalysisRequest.create(
            event_record=event_result.record,
            subscription=subscription,
            importance=decision,
            requested_at=1_752_111_000,
        )

        class FakeMemcore:
            enabled = True

            def __init__(self):
                self.calls = []

            def record_tool_exchange(self, **kwargs):
                self.calls.append(("tool", kwargs))
                return {"ok": True, "status": "recorded"}

            def record_assistant_turn(self, record, **kwargs):
                self.calls.append(("assistant", record, kwargs))
                return {"ok": True, "status": "recorded"}

            def compact_due_background(self, **kwargs):
                self.calls.append(("compact", kwargs))
                return {"ok": True, "status": "scheduled"}

        class FakeEngine:
            def __init__(self):
                self.memcore_manager = FakeMemcore()
                self.payloads = []

            def process_turn(self, payload):
                self.payloads.append(payload)
                return {"speech": "这条信息可能改变短期预期，但仍需核验完整正文。", "speech_segments": []}

        engine = FakeEngine()
        result = AkaneFinanceAnalysisClient(engine).analyze(request)

        self.assertTrue(result.ok)
        self.assertEqual(engine.payloads[0]["turn_kind"], "market_event")
        self.assertEqual([item[0] for item in engine.memcore_manager.calls], ["tool", "assistant", "compact"])
        assistant_record = engine.memcore_manager.calls[1][1]
        self.assertEqual(assistant_record["source_id"], request.analysis_id)
        self.assertEqual(assistant_record["memory_metadata"]["categories"], ["market_analysis"])
        self.assertNotIn("user", [item[0] for item in engine.memcore_manager.calls])

    def test_memcore_manager_exposes_public_tool_exchange_facade(self) -> None:
        class FakeSystem:
            def record_tool_exchange(self, **kwargs):
                return {
                    "tool_use": {"source_id": "trace:tool_use"},
                    "tool_result": {"source_id": "trace:tool_result"},
                }

        manager = object.__new__(MemcoreManager)
        manager._reason = ""
        manager._get_system_or_none = lambda **_: FakeSystem()

        result = manager.record_tool_exchange(
            tool_name="market_feed",
            result={"event_id": "event-1"},
            profile_user_id="profile",
            session_id="session",
            source_id_prefix="trace",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["tool_use_source_id"], "trace:tool_use")
        self.assertEqual(result["tool_result_source_id"], "trace:tool_result")

    def test_memcore_config_does_not_gain_finance_categories_without_plugin_contract(self) -> None:
        class FakeMemoryConfig:
            def __init__(self, **kwargs):
                self.categories = tuple(kwargs["categories"])

        class FakeMemcore:
            DEFAULT_CATEGORIES = ("casual", "tool_trace", "material_trace")
            MemoryConfig = FakeMemoryConfig

        manager = object.__new__(MemcoreManager)
        manager.visible_scope = "user"
        manager.enable_flavor = False

        memory_config = manager._build_memory_config(FakeMemcore)

        self.assertEqual(memory_config.categories, FakeMemcore.DEFAULT_CATEGORIES)
        self.assertNotIn("market_event", memory_config.categories)
        self.assertNotIn("market_analysis", memory_config.categories)


if __name__ == "__main__":
    unittest.main()
