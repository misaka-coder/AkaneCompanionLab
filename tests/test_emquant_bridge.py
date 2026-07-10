from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from services.emquant_bridge import (
    FORBIDDEN_MUTATING_FUNCTIONS,
    READ_ONLY_QUERY_FUNCTIONS,
    READ_ONLY_SUBSCRIPTION_FUNCTIONS,
    EmQuantBridgeRuntime,
    EmQuantSDKLoader,
    FakeEmQuantData,
    FakeEmQuantSDK,
    create_emquant_bridge_app,
)
from services.market_data import MarketDataValidationError


SYNTHETIC_NEWS_ROWS = [
    {
        "datetime": "2026-07-10 09:30:00",
        "eitime": "2026-07-10 09:31:00",
        "code": "000000.TEST",
        "content": "synthetic bridge news",
        "title": "合成桥接资讯",
        "infoCode": "BRIDGE-NEWS-001",
        "medianname": "Fake EmQuant SDK",
        "url": "https://example.invalid/bridge-news-001",
        "type": "companynews",
        "label": "synthetic",
    }
]

SYNTHETIC_QUOTE_ROWS = [
    {
        "TIME": "2026-07-10 10:00:00",
        "CODE": "000000.TEST",
        "PRECLOSE": 100,
        "OPEN": 100.5,
        "HIGH": 103,
        "LOW": 99.5,
        "NOW": 102,
        "VOLUME": 123456,
        "AMOUNT": 12500000,
    }
]


class EmQuantSDKLoaderTests(unittest.TestCase):
    def test_missing_or_invalid_sdk_root_fails_without_importing_dll(self) -> None:
        loader = EmQuantSDKLoader()

        missing = loader.load("")
        invalid = loader.load(Path(tempfile.gettempdir()) / "does-not-exist-emquant")

        self.assertFalse(missing.ok)
        self.assertEqual(missing.status, "missing_config")
        self.assertFalse(invalid.ok)
        self.assertEqual(invalid.status, "invalid_sdk")
        self.assertNotIn(str(Path(tempfile.gettempdir())), invalid.to_public_dict()["reason"])

    def test_read_only_allowlists_never_include_mutating_portfolio_functions(self) -> None:
        exposed = READ_ONLY_QUERY_FUNCTIONS | READ_ONLY_SUBSCRIPTION_FUNCTIONS

        self.assertTrue({"cfn", "csqsnapshot", "cnq", "csq"}.issubset(exposed))
        self.assertTrue(FORBIDDEN_MUTATING_FUNCTIONS.isdisjoint(exposed))


class EmQuantBridgeRuntimeTests(unittest.TestCase):
    def _runtime(self, sdk: FakeEmQuantSDK, **kwargs) -> EmQuantBridgeRuntime:
        return EmQuantBridgeRuntime(
            enabled=True,
            sdk=sdk,
            provider_id="fake_emquant",
            source_name="Fake EmQuant SDK",
            clock=lambda: 1_752_110_000,
            **kwargs,
        )

    def test_disabled_bridge_does_not_touch_sdk(self) -> None:
        sdk = FakeEmQuantSDK()
        runtime = EmQuantBridgeRuntime(enabled=False, sdk=sdk)

        result = runtime.start()

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "missing_config")
        self.assertEqual(sdk.calls, [])
        self.assertEqual(runtime.health().status, "disabled")

    def test_start_uses_non_force_login_options_and_reports_capabilities(self) -> None:
        sdk = FakeEmQuantSDK()
        runtime = self._runtime(sdk)

        result = runtime.start()
        health = runtime.health().to_public_dict()

        self.assertTrue(result.ok, result.to_public_dict())
        self.assertEqual(health["status"], "ready")
        self.assertTrue(health["logged_in"])
        start_call = next(call for call in sdk.calls if call[0] == "start")
        options = str(start_call[1][0])
        self.assertIn("ForceLogin=0", options)
        self.assertIn("RecordLoginInfo=0", options)
        self.assertNotIn("UserName", options)
        self.assertNotIn("Password", options)
        self.assertTrue(health["capabilities"]["csq"]["present"])
        self.assertTrue(health["capabilities"]["datastatistics"]["operational"])
        self.assertEqual(health["quota_status"]["subscription_state"]["status"], "disabled")

    def test_start_permission_error_is_not_reported_ready(self) -> None:
        runtime = self._runtime(FakeEmQuantSDK(start_error_code=10001003))

        result = runtime.start()
        health = runtime.health()

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "permission_denied")
        self.assertEqual(health.status, "permission_denied")
        self.assertFalse(health.logged_in)
        self.assertFalse(health.capabilities["start"].authorized)

    def test_function_presence_and_permission_are_reported_separately(self) -> None:
        sdk = FakeEmQuantSDK(function_error_codes={"csqsnapshot": 10001012})
        runtime = self._runtime(sdk)
        runtime.start()

        result = runtime.quote_snapshot(codes=("000000.TEST",))
        capability = runtime.health().capabilities["csqsnapshot"]

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "permission_denied")
        self.assertTrue(capability.present)
        self.assertFalse(capability.authorized)
        self.assertFalse(capability.operational)
        self.assertNotEqual(runtime.health().status, "ready")

    def test_sync_queries_return_choice_shaped_records(self) -> None:
        sdk = FakeEmQuantSDK(news_rows=SYNTHETIC_NEWS_ROWS, quote_rows=SYNTHETIC_QUOTE_ROWS)
        runtime = self._runtime(sdk)
        runtime.start()

        news = runtime.query_news(
            codes=("000000.TEST",),
            content_types=("companynews",),
            mode=2,
            options="count=5",
        )
        quote = runtime.quote_snapshot(codes=("000000.TEST",))

        self.assertTrue(news.ok, news.to_public_dict())
        self.assertEqual(news.data["records"][0]["infoCode"], "BRIDGE-NEWS-001")
        self.assertEqual(news.data["records"][0]["code"], "000000.TEST")
        self.assertTrue(quote.ok, quote.to_public_dict())
        self.assertEqual(quote.data["records"][0]["NOW"], 102)
        self.assertEqual(quote.data["records"][0]["code"], "000000.TEST")

    def test_subscription_callback_only_enqueues_structured_event(self) -> None:
        sdk = FakeEmQuantSDK()
        runtime = self._runtime(sdk)
        runtime.register_subscription(
            subscription_id="news-sub-1",
            kind="news",
            codes=("000000.TEST",),
            fields=("companynews",),
        )
        runtime.start()
        state = runtime.list_subscriptions()[0]

        sdk.emit_news(state.serial_id, SYNTHETIC_NEWS_ROWS)
        events = runtime.poll_events(limit=10)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "news")
        self.assertEqual(events[0].serial_id, state.serial_id)
        self.assertEqual(events[0].payload["records"][0]["infoCode"], "BRIDGE-NEWS-001")
        self.assertGreater(runtime.health().last_news_at, 0)
        self.assertNotIn("porder", [name for name, _args in sdk.calls])

    def test_callback_queue_overflow_degrades_without_blocking(self) -> None:
        sdk = FakeEmQuantSDK()
        runtime = self._runtime(sdk, callback_queue_maxsize=1)
        runtime.register_subscription(
            subscription_id="quote-sub-1",
            kind="quote",
            codes=("000000.TEST",),
            fields=("TIME", "NOW"),
        )
        runtime.start()
        state = runtime.list_subscriptions()[0]

        sdk.emit_quote(state.serial_id, SYNTHETIC_QUOTE_ROWS, "TIME,NOW")
        sdk.emit_quote(state.serial_id, SYNTHETIC_QUOTE_ROWS, "TIME,NOW")

        health = runtime.health()
        self.assertEqual(health.queue_size, 1)
        self.assertEqual(health.dropped_callback_count, 1)
        self.assertEqual(health.status, "degraded")

    def test_subscription_callback_error_updates_capability_and_health(self) -> None:
        sdk = FakeEmQuantSDK()
        runtime = self._runtime(sdk)
        runtime.start()
        state = runtime.register_subscription(
            subscription_id="news-error",
            kind="news",
            codes=("000000.TEST",),
            fields=("companynews",),
        )

        sdk.news_callbacks[state.serial_id](
            FakeEmQuantData(
                ErrorCode=10001024,
                ErrorMsg="fake_permission_denied",
                SerialID=state.serial_id,
            )
        )

        health = runtime.health()
        self.assertEqual(health.status, "permission_denied")
        self.assertFalse(health.capabilities["cnq"].authorized)
        self.assertFalse(health.capabilities["cnq"].operational)

    def test_stop_cancels_subscriptions_before_sdk_stop(self) -> None:
        sdk = FakeEmQuantSDK()
        runtime = self._runtime(sdk)
        runtime.register_subscription(
            subscription_id="news-sub-stop",
            kind="news",
            codes=("000000.TEST",),
            fields=("companynews",),
        )
        runtime.register_subscription(
            subscription_id="quote-sub-stop",
            kind="quote",
            codes=("000000.TEST",),
            fields=("TIME", "NOW"),
        )
        runtime.start()

        result = runtime.stop()
        call_names = [name for name, _args in sdk.calls]

        self.assertTrue(result.ok)
        self.assertLess(call_names.index("cnqcancel"), call_names.index("stop"))
        self.assertLess(call_names.index("csqcancel"), call_names.index("stop"))
        self.assertEqual(runtime.health().status, "stopped")
        self.assertFalse(runtime.health().logged_in)

    def test_cancel_failure_is_not_reported_as_disabled_success(self) -> None:
        sdk = FakeEmQuantSDK(function_error_codes={"cnqcancel": 10000016})
        runtime = self._runtime(sdk)
        runtime.start()
        active = runtime.register_subscription(
            subscription_id="cancel-failure",
            kind="news",
            codes=("000000.TEST",),
            fields=("companynews",),
        )

        disabled = runtime.disable_subscription("cancel-failure")

        self.assertTrue(active.active)
        self.assertEqual(disabled.status, "cancel_failed")
        self.assertGreater(disabled.serial_id, 0)
        self.assertEqual(runtime.health().status, "degraded")
        self.assertEqual(runtime.health().last_error_code, 10000016)

    def test_subscription_specs_persist_and_restore_with_fake_sdk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "emquant_subscriptions.json"
            first = self._runtime(FakeEmQuantSDK(), subscription_state_path=state_path)
            first.register_subscription(
                subscription_id="persisted-news",
                kind="news",
                codes=("000000.TEST",),
                fields=("companynews",),
            )

            sdk = FakeEmQuantSDK()
            restored = self._runtime(sdk, subscription_state_path=state_path)
            start = restored.start()
            state = restored.list_subscriptions()[0]

            self.assertTrue(start.ok, start.to_public_dict())
            self.assertEqual(state.spec.subscription_id, "persisted-news")
            self.assertTrue(state.active)
            self.assertGreater(state.serial_id, 0)

    def test_system_disconnect_callback_updates_health(self) -> None:
        sdk = FakeEmQuantSDK()
        runtime = self._runtime(sdk)
        runtime.start()

        sdk.emit_system_error(10002014)
        health = runtime.health()
        events = runtime.poll_events()

        self.assertEqual(health.status, "disconnected")
        self.assertFalse(health.logged_in)
        self.assertEqual(events[0].kind, "system")
        self.assertEqual(events[0].error_code, 10002014)

    def test_sensitive_subscription_options_are_rejected(self) -> None:
        runtime = self._runtime(FakeEmQuantSDK())

        with self.assertRaises(MarketDataValidationError) as raised:
            runtime.register_subscription(
                subscription_id="bad-options",
                kind="news",
                codes=("000000.TEST",),
                fields=("companynews",),
                options="UserName=secret",
            )

        self.assertEqual(raised.exception.status, "invalid_arguments")


class EmQuantBridgeLocalAPITests(unittest.TestCase):
    def test_optional_bridge_token_is_required_without_being_echoed(self) -> None:
        runtime = EmQuantBridgeRuntime(enabled=False)
        client = TestClient(create_emquant_bridge_app(runtime, access_token="local-secret"))

        missing = client.get("/health")
        allowed = client.get("/health", headers={"Authorization": "Bearer local-secret"})

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.json()["status"], "unauthorized")
        self.assertEqual(allowed.status_code, 200)
        self.assertNotIn("local-secret", allowed.text)

    def test_non_loopback_client_is_rejected(self) -> None:
        runtime = EmQuantBridgeRuntime(enabled=False)
        client = TestClient(
            create_emquant_bridge_app(runtime),
            client=("203.0.113.10", 50000),
        )

        response = client.get("/health")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["status"], "forbidden")

    def test_loopback_api_exposes_read_only_operations_without_generic_sdk_call(self) -> None:
        sdk = FakeEmQuantSDK(quote_rows=SYNTHETIC_QUOTE_ROWS)
        runtime = EmQuantBridgeRuntime(
            enabled=True,
            sdk=sdk,
            provider_id="fake_emquant",
            source_name="Fake EmQuant SDK",
            clock=lambda: 1_752_110_000,
        )
        client = TestClient(create_emquant_bridge_app(runtime))

        start = client.post("/start")
        quote = client.post("/quotes/snapshot", json={"codes": ["000000.TEST"]})
        subscription = client.post(
            "/subscriptions",
            json={
                "subscription_id": "api-news-sub",
                "kind": "news",
                "codes": ["000000.TEST"],
                "fields": ["companynews"],
            },
        )
        forbidden = client.post("/sdk/call", json={"function": "porder", "arguments": {}})

        self.assertEqual(start.status_code, 200)
        self.assertEqual(quote.status_code, 200)
        self.assertEqual(quote.json()["data"]["records"][0]["NOW"], 102)
        self.assertEqual(subscription.status_code, 200)
        self.assertEqual(subscription.json()["subscription"]["status"], "active")
        self.assertEqual(forbidden.status_code, 404)
        self.assertNotIn("porder", [name for name, _args in sdk.calls])

    def test_api_rejects_invalid_code_without_broad_query(self) -> None:
        runtime = EmQuantBridgeRuntime(
            enabled=True,
            sdk=FakeEmQuantSDK(),
            provider_id="fake_emquant",
            source_name="Fake EmQuant SDK",
        )
        client = TestClient(create_emquant_bridge_app(runtime))
        client.post("/start")

        response = client.post("/quotes/snapshot", json={"codes": ["not a code"]})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "invalid_arguments")


if __name__ == "__main__":
    unittest.main()
