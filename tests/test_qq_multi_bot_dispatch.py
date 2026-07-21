from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.deployment_security import QQChannelRuntimeConfig
from companion_v01.plugin_api import PluginQQCommandResult
from companion_v01.qq_gateway import NapCatQQGateway
from companion_v01.routes.qq import build_qq_router


class _Metrics:
    def __init__(self) -> None:
        self.observed: list[tuple[str, bool]] = []

    def observe_request(self, name: str, *, duration_ms: float, ok: bool) -> None:
        self.observed.append((name, ok))


class _Engine:
    desktop_pet_character_resources = None

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.turns: list[dict[str, Any]] = []

    def prefetch_remote_media_links_for_message(self, **_kwargs: Any) -> dict[str, Any]:
        return {}

    def process_turn_stream(self, payload: dict[str, Any]):
        self.turns.append(dict(payload))
        yield {
            "type": "final_ui",
            "payload": {
                "reply_medium": "text",
                "speech": self.reply,
                "speech_segments": [self.reply],
                "tool_events": [],
            },
        }

    def mark_generated_file_delivery(self, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": True}


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"status": "ok"}


def _message_text(post: dict[str, Any]) -> str:
    return "".join(
        str(item.get("data", {}).get("text") or "") for item in post["json"]["message"] if item.get("type") == "text"
    )


class _Broker:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def handles(command: str) -> bool:
        return command == "/identity"

    async def dispatch(self, **kwargs: Any) -> PluginQQCommandResult:
        self.calls.append(dict(kwargs))
        return PluginQQCommandResult(handled=True, reply_text=self.reply)


def _channel(*, profile_ref: str, bot_id: str, port: int, secret: str, token: str) -> QQChannelRuntimeConfig:
    return QQChannelRuntimeConfig(
        enabled=True,
        profile_ref=profile_ref,
        bot_id=bot_id,
        onebot_http_url=f"http://127.0.0.1:{port}",
        webhook_secret=secret,
        onebot_access_token=token,
        require_webhook_auth=True,
        require_self_id=True,
    )


class QQMultiBotDispatchTests(unittest.TestCase):
    def _app(self, *, broker_a: Any = None, broker_b: Any = None):
        app = FastAPI()
        engine_a = _Engine("A 的正常回复")
        engine_b = _Engine("B 的正常回复")
        channel_a = _channel(
            profile_ref="qq.bot-a",
            bot_id="10000001",
            port=3001,
            secret="secret-a",
            token="token-a",
        )
        channel_b = _channel(
            profile_ref="qq.bot-b",
            bot_id="10000002",
            port=3002,
            secret="secret-b",
            token="token-b",
        )
        gateway_a = NapCatQQGateway(channel_config=channel_a, wake_words=("Akane",))
        gateway_b = NapCatQQGateway(channel_config=channel_b, wake_words=("金融助手",))
        common = {
            "config_module": SimpleNamespace(QQ_BRIDGE_ENABLED=True, QQ_STREAM_REPLIES_ENABLED=False),
            "logger": SimpleNamespace(exception=lambda *_args, **_kwargs: None),
            "log_event": lambda *_args, **_kwargs: None,
        }
        app.include_router(
            build_qq_router(
                engine=engine_a,
                qq_gateway=gateway_a,
                runtime_metrics=_Metrics(),
                channel_config=channel_a,
                route_base="/api/bots/bot-a/qq",
                plugin_command_broker_provider=lambda: broker_a,
                **common,
            )
        )
        app.include_router(
            build_qq_router(
                engine=engine_b,
                qq_gateway=gateway_b,
                runtime_metrics=_Metrics(),
                channel_config=channel_b,
                route_base="/api/bots/bot-b/qq",
                plugin_command_broker_provider=lambda: broker_b,
                **common,
            )
        )
        app.include_router(
            build_qq_router(
                engine=engine_a,
                qq_gateway=gateway_a,
                runtime_metrics=_Metrics(),
                channel_config=channel_a,
                route_base="/api/qq",
                plugin_command_broker_provider=lambda: broker_a,
                **common,
            )
        )
        return app, engine_a, engine_b, gateway_a, gateway_b

    def test_canonical_paths_isolate_secret_self_id_engine_and_outbound_gateway(self) -> None:
        app, engine_a, engine_b, _gateway_a, _gateway_b = self._app()
        posts: list[dict[str, Any]] = []

        def fake_post(_method: str, url: str, **kwargs: Any) -> _Response:
            posts.append({"url": url, **kwargs})
            return _Response()

        event_a = {
            "post_type": "message",
            "message_type": "private",
            "self_id": "10000001",
            "user_id": "20000001",
            "message_id": "event-a",
            "raw_message": "你好 A",
        }
        event_b = {
            **event_a,
            "self_id": "10000002",
            "message_id": "event-b",
            "raw_message": "你好 B",
        }
        client = TestClient(app)
        with patch("companion_v01.onebot_transport.requests.Session.request", side_effect=fake_post):
            wrong_secret = client.post(
                "/api/bots/bot-a/qq/napcat/event",
                headers={"Authorization": "Bearer secret-b"},
                json=event_a,
            )
            wrong_self = client.post(
                "/api/bots/bot-a/qq/napcat/event",
                headers={"Authorization": "Bearer secret-a"},
                json=event_b,
            )
            response_a = client.post(
                "/api/bots/bot-a/qq/napcat/event",
                headers={"Authorization": "Bearer secret-a"},
                json=event_a,
            )
            response_b = client.post(
                "/api/bots/bot-b/qq/napcat/event",
                headers={"Authorization": "Bearer secret-b"},
                json=event_b,
            )

        self.assertEqual(wrong_secret.status_code, 401)
        self.assertEqual(wrong_self.status_code, 403)
        self.assertEqual(response_a.status_code, 200)
        self.assertEqual(response_b.status_code, 200)
        self.assertEqual(len(engine_a.turns), 1)
        self.assertEqual(len(engine_b.turns), 1)
        self.assertEqual(
            [item["url"] for item in posts],
            [
                "http://127.0.0.1:3001/send_private_msg",
                "http://127.0.0.1:3002/send_private_msg",
            ],
        )
        self.assertEqual(posts[0]["headers"]["Authorization"], "Bearer token-a")
        self.assertEqual(posts[1]["headers"]["Authorization"], "Bearer token-b")
        self.assertIn("A 的正常回复", _message_text(posts[0]))
        self.assertIn("B 的正常回复", _message_text(posts[1]))

    def test_legacy_path_is_only_an_alias_for_default_bot(self) -> None:
        app, engine_a, engine_b, _gateway_a, _gateway_b = self._app()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": "10000001",
            "user_id": "20000001",
            "message_id": "legacy-event-a",
            "raw_message": "旧路径",
        }
        with patch("companion_v01.onebot_transport.requests.Session.request", return_value=_Response()):
            response = TestClient(app).post(
                "/api/qq/napcat/event",
                headers={"Authorization": "Bearer secret-a"},
                json=event,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(engine_a.turns), 1)
        self.assertEqual(engine_b.turns, [])

    def test_default_alias_and_canonical_path_share_duplicate_event_guard(self) -> None:
        app, engine_a, engine_b, _gateway_a, _gateway_b = self._app()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": "10000001",
            "user_id": "20000001",
            "message_id": "same-event-on-two-routes",
            "raw_message": "同一事件不能回复两次",
        }
        client = TestClient(app)
        with patch("companion_v01.onebot_transport.requests.Session.request", return_value=_Response()) as mocked_post:
            canonical = client.post(
                "/api/bots/bot-a/qq/napcat/event",
                headers={"Authorization": "Bearer secret-a"},
                json=event,
            )
            legacy_alias = client.post(
                "/api/qq/napcat/event",
                headers={"Authorization": "Bearer secret-a"},
                json=event,
            )

        self.assertEqual(canonical.status_code, 200)
        self.assertEqual(legacy_alias.json(), {"status": "ignored", "reason": "duplicate_event"})
        self.assertEqual(len(engine_a.turns), 1)
        self.assertEqual(engine_b.turns, [])
        mocked_post.assert_called_once()

    def test_per_bot_wake_words_do_not_cross_trigger_or_match_account_suffix(self) -> None:
        _app, _engine_a, _engine_b, gateway_a, gateway_b = self._app()
        base_event = {
            "post_type": "message",
            "message_type": "group",
            "user_id": "20000001",
            "group_id": "30000001",
            "message_id": "group-wake-a",
            "message": [{"type": "text", "data": {"text": "Akane 切换角色 reimu"}}],
        }
        context_a = gateway_a.build_message_context({**base_event, "self_id": "10000001"})
        context_b = gateway_b.build_message_context({**base_event, "self_id": "10000002", "message_id": "group-wake-b"})
        suffix_context = gateway_a.build_message_context(
            {
                **base_event,
                "self_id": "10000001",
                "message_id": "group-wake-suffix",
                "message": [{"type": "text", "data": {"text": "Akane218 今天在线吗"}}],
            }
        )

        self.assertTrue(context_a.should_respond)
        self.assertEqual(context_a.reason, "group_wake_word")
        self.assertFalse(context_b.should_respond)
        self.assertEqual(context_b.reason, "group_passive_observed")
        self.assertFalse(suffix_context.should_respond)
        self.assertEqual(suffix_context.reason, "group_passive_observed")

    def test_plugin_command_broker_is_selected_from_target_bot_runtime(self) -> None:
        broker_a = _Broker("A 插件命令")
        broker_b = _Broker("B 插件命令")
        app, engine_a, engine_b, _gateway_a, _gateway_b = self._app(
            broker_a=broker_a,
            broker_b=broker_b,
        )
        base_event = {
            "post_type": "message",
            "message_type": "private",
            "user_id": "20000001",
            "raw_message": "/identity now",
        }
        posts: list[dict[str, Any]] = []

        def fake_post(_method: str, url: str, **kwargs: Any) -> _Response:
            posts.append({"url": url, **kwargs})
            return _Response()

        with patch("companion_v01.onebot_transport.requests.Session.request", side_effect=fake_post):
            response_a = TestClient(app).post(
                "/api/bots/bot-a/qq/napcat/event",
                headers={"Authorization": "Bearer secret-a"},
                json={**base_event, "self_id": "10000001", "message_id": "command-a"},
            )
            response_b = TestClient(app).post(
                "/api/bots/bot-b/qq/napcat/event",
                headers={"Authorization": "Bearer secret-b"},
                json={**base_event, "self_id": "10000002", "message_id": "command-b"},
            )

        self.assertEqual(response_a.json()["reason"], "qq_plugin_command")
        self.assertEqual(response_b.json()["reason"], "qq_plugin_command")
        self.assertEqual([item["idempotency_key"] for item in broker_a.calls], ["command-a"])
        self.assertEqual([item["idempotency_key"] for item in broker_b.calls], ["command-b"])
        self.assertEqual(engine_a.turns, [])
        self.assertEqual(engine_b.turns, [])
        self.assertIn("A 插件命令", _message_text(posts[0]))
        self.assertIn("B 插件命令", _message_text(posts[1]))


if __name__ == "__main__":
    unittest.main()
