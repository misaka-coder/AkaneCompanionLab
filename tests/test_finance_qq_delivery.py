from __future__ import annotations

import unittest
from unittest.mock import patch

import config
from companion_v01.finance import FinanceAnalysisResult, QQFinanceDeliveryAdapter
from services.market_data import FinanceSubscription


def _subscription(*, enabled: bool = True, finance_mode: str = "push", is_group: bool = True):
    return FinanceSubscription(
        subscription_id="sub-qq-push",
        client="qq",
        target_id="20001",
        is_group=is_group,
        session_id="qq_group_shared_20001" if is_group else "qq_pri_20001",
        profile_user_id="qq_group_shared_20001" if is_group else "qq_20001",
        character_pack_id="akane_default",
        finance_mode=finance_mode,
        enabled=enabled,
        filters={"codes": ["000000.TEST"]},
        delivery_policy={"level": "notify"},
    )


class _FakeGateway:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.calls = []

    def send_replies(self, context, messages):
        self.calls.append((context, messages))
        if self.ok:
            return {"ok": True, "count": len(messages), "results": [{"ok": True}]}
        return {"ok": False, "count": 1, "results": [{"ok": False, "reason": "offline"}]}


class QQFinanceDeliveryAdapterTests(unittest.TestCase):
    def test_delivery_requires_all_runtime_and_subscription_switches(self) -> None:
        gateway = _FakeGateway()
        adapter = QQFinanceDeliveryAdapter(gateway)

        for finance_enabled, push_enabled, bridge_enabled, expected in (
            (False, True, True, "finance_assistant_disabled"),
            (True, False, True, "qq_finance_push_disabled"),
            (True, True, False, "qq_bridge_disabled"),
        ):
            with (
                self.subTest(expected=expected),
                patch.object(config, "FINANCE_ASSISTANT_ENABLED", finance_enabled, create=True),
                patch.object(config, "QQ_FINANCE_PUSH_ENABLED", push_enabled, create=True),
                patch.object(config, "QQ_BRIDGE_ENABLED", bridge_enabled),
            ):
                authorization = adapter.authorize(_subscription())
                self.assertFalse(authorization.allowed)
                self.assertEqual(authorization.reason, expected)

        with (
            patch.object(config, "FINANCE_ASSISTANT_ENABLED", True, create=True),
            patch.object(config, "QQ_FINANCE_PUSH_ENABLED", True, create=True),
            patch.object(config, "QQ_BRIDGE_ENABLED", True),
        ):
            self.assertEqual(adapter.authorize(_subscription(enabled=False)).reason, "subscription_disabled")
            self.assertEqual(
                adapter.authorize(_subscription(finance_mode="qa")).reason, "subscription_not_in_push_mode"
            )

    def test_authorized_group_delivery_uses_structured_qq_context(self) -> None:
        gateway = _FakeGateway()
        adapter = QQFinanceDeliveryAdapter(gateway)
        analysis = FinanceAnalysisResult(
            ok=True,
            status="analyzed",
            analysis_id="market_analysis:test",
            messages=("【市场快讯｜10:00】\n来源：Synthetic",),
        )

        with (
            patch.object(config, "FINANCE_ASSISTANT_ENABLED", True, create=True),
            patch.object(config, "QQ_FINANCE_PUSH_ENABLED", True, create=True),
            patch.object(config, "QQ_BRIDGE_ENABLED", True),
        ):
            result = adapter.deliver(subscription=_subscription(), analysis=analysis)

        self.assertTrue(result.ok)
        context, messages = gateway.calls[0]
        self.assertTrue(context.is_group)
        self.assertEqual(context.group_id, 20001)
        self.assertEqual(context.target_id, 20001)
        self.assertEqual(context.reason, "finance_subscription_push")
        self.assertEqual(context.finance_mode, "push")
        self.assertEqual(messages, list(analysis.messages))

    def test_gateway_failure_is_structured_for_retry(self) -> None:
        gateway = _FakeGateway(ok=False)
        adapter = QQFinanceDeliveryAdapter(gateway)
        analysis = FinanceAnalysisResult(
            ok=True,
            status="analyzed",
            analysis_id="market_analysis:test",
            messages=("push",),
        )

        with (
            patch.object(config, "FINANCE_ASSISTANT_ENABLED", True, create=True),
            patch.object(config, "QQ_FINANCE_PUSH_ENABLED", True, create=True),
            patch.object(config, "QQ_BRIDGE_ENABLED", True),
        ):
            result = adapter.deliver(subscription=_subscription(), analysis=analysis)

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "offline")


if __name__ == "__main__":
    unittest.main()
