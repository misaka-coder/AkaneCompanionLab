from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from companion_v01.finance import FinanceSubscriptionService
from companion_v01.qq_gateway import NapCatQQGateway, QQMessageContext
from companion_v01.routes.qq import build_qq_router
from services.market_data import MarketEventStore


class FinanceSubscriptionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = MarketEventStore(Path(self.temp_dir.name) / "subscriptions.sqlite3")
        self.store.upsert_security(
            provider="mock_choice",
            code="000000.TEST",
            display_name="合成测试公司",
            aliases=("测试公司",),
            source="Synthetic Security Master",
            as_of=1_752_153_600,
            now_ts=100,
        )
        self.service = FinanceSubscriptionService(store=self.store, provider_id="mock_choice")
        self.context = QQMessageContext(
            should_respond=True,
            reason="group_mention",
            is_group=True,
            target_id=20001,
            user_id=10001,
            group_id=20001,
            session_id="qq_group_shared_20001",
            profile_user_id="qq_group_shared_20001",
            clean_message="开启财经推送",
            raw_message="Akane 开启财经推送",
            sender_label="管理员",
            character_pack_id="akane_default",
            finance_mode="off",
        )

    def test_push_mode_creates_subscription_and_watchlist_survives_reenable(self) -> None:
        enabled = self.service.sync_mode(self.context, "push")
        subscription = self.store.get_subscription(enabled["subscription_id"])

        self.assertTrue(enabled["ok"], enabled)
        self.assertTrue(subscription.enabled)
        self.assertTrue(subscription.is_group)
        self.assertEqual(subscription.created_by_actor_id, "qq:10001")
        self.assertEqual(dict(subscription.delivery_policy), {"level": "notify"})

        add_context = QQMessageContext(
            **{
                **self.context.__dict__,
                "clean_message": "关注 测试公司",
                "raw_message": "Akane 关注 测试公司",
            }
        )
        added = self.service.handle_watchlist_command(add_context, authorized=True)
        disabled = self.service.sync_mode(self.context, "qa")
        reenabled = self.service.sync_mode(self.context, "push")

        self.assertTrue(added["ok"], added)
        self.assertEqual(added["code"], "000000.TEST")
        self.assertEqual(disabled["status"], "disabled")
        self.assertEqual(reenabled["watchlist_count"], 1)
        self.assertEqual(self.store.list_watchlist(subscription.subscription_id)[0].display_name, "合成测试公司")

    def test_public_market_push_subscription_enables_market_wide_news(self) -> None:
        service = FinanceSubscriptionService(store=self.store, provider_id="public_market")

        result = service.sync_mode(self.context, "push")
        subscription = self.store.get_subscription(result["subscription_id"])

        self.assertTrue(result["ok"])
        self.assertTrue(dict(subscription.filters)["include_market_wide"])

    def test_watchlist_commands_require_push_subscription_and_group_authority(self) -> None:
        add_context = QQMessageContext(
            **{
                **self.context.__dict__,
                "clean_message": "关注 000000.TEST",
            }
        )

        missing = self.service.handle_watchlist_command(add_context, authorized=True)
        self.service.sync_mode(self.context, "push")
        forbidden = self.service.handle_watchlist_command(add_context, authorized=False)
        list_context = QQMessageContext(
            **{
                **self.context.__dict__,
                "clean_message": "关注列表",
            }
        )
        listed = self.service.handle_watchlist_command(list_context, authorized=False)

        self.assertEqual(missing["status"], "subscription_required")
        self.assertEqual(forbidden["status"], "forbidden")
        self.assertEqual(listed["status"], "listed")
        self.assertEqual(self.store.list_watchlist(self.service.sync_mode(self.context, "push")["subscription_id"]), ())

    def test_unresolved_name_never_constructs_exchange_suffix(self) -> None:
        self.service.sync_mode(self.context, "push")
        unknown_context = QQMessageContext(
            **{
                **self.context.__dict__,
                "clean_message": "关注 某个不存在的证券",
            }
        )

        result = self.service.handle_watchlist_command(unknown_context, authorized=True)

        self.assertFalse(result["ok"])
        self.assertIn(result["status"], {"not_found", "needs_confirmation"})
        self.assertIn("不会自行拼交易所后缀", result["reply"])

    def test_reenable_consolidates_duplicate_subscriptions_without_losing_watchlist(self) -> None:
        for subscription_id, code, now_ts in (
            ("legacy-sub-a", "000001.TEST", 100),
            ("legacy-sub-b", "000002.TEST", 200),
        ):
            self.store.upsert_subscription(
                subscription_id=subscription_id,
                client="qq",
                target_id="20001",
                is_group=True,
                session_id=self.context.session_id,
                profile_user_id=self.context.profile_user_id,
                finance_mode="push",
                enabled=True,
                filters={},
                delivery_policy={"level": "notify"},
                now_ts=now_ts,
            )
            self.store.upsert_watchlist_item(
                subscription_id=subscription_id,
                provider="mock_choice",
                code=code,
                now_ts=now_ts,
            )

        result = self.service.sync_mode(self.context, "push")

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["disabled_duplicate_count"], 1)
        self.assertEqual(
            {item.code for item in self.store.list_watchlist(result["subscription_id"])},
            {"000001.TEST", "000002.TEST"},
        )
        enabled = self.store.list_subscriptions(enabled=True, client="qq", target_id="20001")
        self.assertEqual(len(enabled), 1)

    def test_gateway_mode_switch_fails_closed_when_subscription_write_fails(self) -> None:
        gateway = NapCatQQGateway()

        class FailingSubscriptionService:
            def sync_mode(self, _context, _mode):
                return {"ok": False, "status": "subscription_failed", "reason": "synthetic"}

        with (
            patch.object(config, "FINANCE_ASSISTANT_ENABLED", True),
            patch.object(config, "QQ_FINANCE_MODE_COMMANDS_ENABLED", True),
            patch.object(config, "QQ_FINANCE_PUSH_ENABLED", True),
        ):
            active_mode = gateway.resolve_finance_mode(self.context.session_id)
            result = gateway.handle_finance_mode_command(
                self.context,
                event={"sender": {"role": "admin"}},
                subscription_service=FailingSubscriptionService(),
            )

        self.assertEqual(result["status"], "subscription_sync_failed")
        self.assertEqual(gateway.resolve_finance_mode(self.context.session_id), active_mode)


class FinanceSubscriptionRouteTests(unittest.TestCase):
    def test_group_admin_command_persists_subscription_and_watchlist_without_llm(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MarketEventStore(Path(temp_dir) / "route_subscriptions.sqlite3")
            store.upsert_security(
                provider="mock_choice",
                code="000000.TEST",
                display_name="合成测试公司",
                aliases=("测试公司",),
                source="Synthetic Security Master",
                as_of=1_752_153_600,
                now_ts=100,
            )
            service = FinanceSubscriptionService(store=store, provider_id="mock_choice")
            gateway = NapCatQQGateway()
            process_calls = []

            class FakeEngine:
                def process_turn_stream(self, payload):
                    process_calls.append(payload)
                    yield {"type": "final_ui", "payload": {"speech": "should not run"}}

            class FakeMetrics:
                def observe_request(self, *_args, **_kwargs):
                    return None

            class FakeResponse:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {"status": "ok"}

            class FakeWorker:
                def status(self):
                    return {"enabled": False, "running": False, "status": "disabled"}

            app = FastAPI()
            app.include_router(
                build_qq_router(
                    engine=FakeEngine(),
                    config_module=SimpleNamespace(QQ_BRIDGE_ENABLED=True),
                    qq_gateway=gateway,
                    runtime_metrics=FakeMetrics(),
                    logger=SimpleNamespace(exception=lambda *_args, **_kwargs: None),
                    log_event=lambda *_args, **_kwargs: None,
                    finance_subscription_service=service,
                    finance_event_worker=FakeWorker(),
                )
            )
            with (
                patch.object(config, "FINANCE_ASSISTANT_ENABLED", True),
                patch.object(config, "QQ_FINANCE_MODE_COMMANDS_ENABLED", True),
                patch.object(config, "QQ_FINANCE_PUSH_ENABLED", True),
                patch.object(config, "QQ_BRIDGE_ENABLED", True),
                patch("companion_v01.qq_gateway.requests.post", return_value=FakeResponse()),
            ):
                enabled = TestClient(app).post(
                    "/api/qq/napcat/event",
                    json={
                        "post_type": "message",
                        "message_type": "group",
                        "self_id": 90001,
                        "user_id": 10001,
                        "group_id": 20001,
                        "message_id": "finance-sub-enable",
                        "raw_message": "Akane 开启财经推送",
                        "sender": {"nickname": "管理员", "role": "admin"},
                    },
                )
                added = TestClient(app).post(
                    "/api/qq/napcat/event",
                    json={
                        "post_type": "message",
                        "message_type": "group",
                        "self_id": 90001,
                        "user_id": 10001,
                        "group_id": 20001,
                        "message_id": "finance-watch-add",
                        "raw_message": "Akane 关注 测试公司",
                        "sender": {"nickname": "管理员", "role": "admin"},
                    },
                )
                status = TestClient(app).get("/api/qq/napcat/status")

            enabled_payload = enabled.json()
            added_payload = added.json()
            self.assertEqual(enabled_payload["reason"], "qq_finance_mode_command")
            self.assertTrue(enabled_payload["subscription_id"])
            self.assertEqual(added_payload["reason"], "qq_finance_watchlist_command")
            self.assertEqual(added_payload["code"], "000000.TEST")
            self.assertEqual(added_payload["watchlist_count"], 1)
            self.assertEqual(status.json()["data"]["finance_event_worker"]["status"], "disabled")
            self.assertEqual(process_calls, [])


if __name__ == "__main__":
    unittest.main()
