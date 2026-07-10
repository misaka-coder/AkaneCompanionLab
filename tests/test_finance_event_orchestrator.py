from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from companion_v01.finance import (
    AkaneFinanceAnalysisClient,
    FinanceAnalysisResult,
    FinanceDeliveryAuthorization,
    FinanceDeliveryResult,
    FinanceEventOrchestrator,
    ensure_market_push_contract,
)
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
    def __init__(self) -> None:
        self.requests = []

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
            frame={"speech": "\n".join(messages)},
        )


class _FakeDeliveryAdapter:
    def __init__(self, *, authorized: bool = True, failures: int = 0) -> None:
        self.authorized = authorized
        self.failures = failures
        self.authorizations = []
        self.deliveries = []

    def authorize(self, subscription):
        self.authorizations.append(subscription)
        return FinanceDeliveryAuthorization(
            self.authorized,
            "authorized" if self.authorized else "disabled",
            "" if self.authorized else "push_disabled",
        )

    def deliver(self, *, subscription, analysis):
        self.deliveries.append((subscription, analysis))
        if self.failures > 0:
            self.failures -= 1
            return FinanceDeliveryResult(False, "failed", "qq_unreachable")
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

    def _orchestrator(self, *, authorized: bool = True, failures: int = 0):
        analysis = _FakeAnalysisClient()
        delivery = _FakeDeliveryAdapter(authorized=authorized, failures=failures)
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
        self.assertEqual(payload["finance_mode"], "push")
        self.assertTrue(is_transient_user_turn(payload))
        self.assertIn("【外部市场事件，不是用户发言】", payload["message"])
        self.assertIn("000000.TEST", payload["message"])
        self.assertNotIn("actor_stable_id", payload)
        pushed = "\n".join(delivery.deliveries[0][1].messages)
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
        self.assertEqual(len(analysis.requests), 2)
        self.assertNotEqual(analysis.requests[0].analysis_id, analysis.requests[1].analysis_id)
        self.assertEqual(len(delivery.deliveries), 2)
        stored = self.store.get_delivery(
            event_id="choice:finance-f6-retry",
            subscription_id=self.subscription.subscription_id,
        )
        self.assertEqual(stored.status, "delivered")
        self.assertEqual(stored.attempt_count, 2)

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

    def test_memcore_config_accepts_fixed_finance_categories(self) -> None:
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

        self.assertIn("market_event", memory_config.categories)
        self.assertIn("market_analysis", memory_config.categories)
        self.assertEqual(len(memory_config.categories), len(set(memory_config.categories)))


if __name__ == "__main__":
    unittest.main()
