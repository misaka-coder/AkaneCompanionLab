from __future__ import annotations

import unittest

import requests

from companion_v01.deployment_security import QQChannelRuntimeConfig
from companion_v01.onebot_transport import OneBotActionTransport


class _Response:
    def __init__(self, body=None, *, status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code

    def json(self):
        return self._body


class _Session:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = []
        self.trust_env = True

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _config(*, url: str, token: str, bot_id: str) -> QQChannelRuntimeConfig:
    return QQChannelRuntimeConfig(
        enabled=True,
        profile_ref=bot_id,
        bot_id=bot_id,
        onebot_http_url=url,
        webhook_secret="",
        onebot_access_token=token,
        require_webhook_auth=False,
        require_self_id=True,
    )


class OneBotActionTransportTests(unittest.TestCase):
    def test_session_isolated_token_redirect_and_proxy_policy(self) -> None:
        first_session = _Session([_Response({"status": "ok", "retcode": "0", "data": {"message_id": 1}})])
        second_session = _Session([_Response({"status": "ok", "retcode": 0, "data": {"message_id": 2}})])
        first = OneBotActionTransport(
            _config(url="http://localhost:3001", token="token-a", bot_id="1"),
            session=first_session,  # type: ignore[arg-type]
        )
        second = OneBotActionTransport(
            _config(url="http://127.0.0.1:3002", token="token-b", bot_id="2"),
            session=second_session,  # type: ignore[arg-type]
        )

        self.assertTrue(first.call("send_private_msg", {"user_id": 3, "message": "hi"}).ok)
        self.assertTrue(second.call("send_private_msg", {"user_id": 4, "message": "hi"}).ok)
        self.assertFalse(first_session.trust_env)
        self.assertFalse(second_session.trust_env)
        self.assertEqual(first_session.calls[0][1], "http://127.0.0.1:3001/send_private_msg")
        self.assertEqual(first_session.calls[0][2]["headers"], {"Authorization": "Bearer token-a"})
        self.assertEqual(second_session.calls[0][2]["headers"], {"Authorization": "Bearer token-b"})
        self.assertFalse(first_session.calls[0][2]["allow_redirects"])

    def test_redirect_http_and_onebot_failures_are_distinct(self) -> None:
        session = _Session(
            [
                _Response({}, status_code=302),
                _Response({}, status_code=503),
                _Response({"status": "ok", "retcode": 1404, "data": {"secret": "not returned"}}),
                _Response({"status": "failed", "retcode": 0, "data": {}}),
                _Response({"retcode": 0, "data": {}}),
            ]
        )
        transport = OneBotActionTransport(
            _config(url="http://127.0.0.1:3001", token="secret", bot_id="1"),
            session=session,  # type: ignore[arg-type]
        )

        self.assertEqual(transport.call("get_file", {"file": "a"}).code, "redirect_rejected")
        self.assertEqual(transport.call("get_file", {"file": "a"}).code, "http_error")
        retcode = transport.call("get_file", {"file": "a"})
        self.assertEqual(retcode.code, "onebot_retcode_error")
        self.assertEqual(retcode.data, {})
        self.assertEqual(transport.call("get_file", {"file": "a"}).code, "onebot_status_error")
        self.assertEqual(transport.call("get_file", {"file": "a"}).code, "onebot_status_error")

    def test_allowlist_and_safe_transport_failure(self) -> None:
        session = _Session([requests.ConnectionError("token=secret http://private/path")])
        transport = OneBotActionTransport(
            _config(url="http://127.0.0.1:3001", token="secret", bot_id="1"),
            session=session,  # type: ignore[arg-type]
        )
        rejected = transport.call("../admin", {})
        self.assertEqual(rejected.code, "action_not_allowed")
        self.assertEqual(session.calls, [])
        failed = transport.call("get_image", {"file": r"C:\\NapCat\\cache\\cat.jpg"})
        serialized = repr(failed)
        self.assertEqual(failed.code, "connection_error")
        self.assertNotIn("secret", serialized)
        self.assertNotIn("NapCat", serialized)


if __name__ == "__main__":
    unittest.main()
