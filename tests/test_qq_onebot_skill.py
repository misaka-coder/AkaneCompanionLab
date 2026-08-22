from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch


_SCRIPT = Path(__file__).parents[1] / "skills" / "qq-onebot-actions" / "scripts" / "onebot_call.py"
_SPEC = importlib.util.spec_from_file_location("qq_onebot_call", _SCRIPT)
assert _SPEC and _SPEC.loader
qq_onebot_call = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(qq_onebot_call)


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.body


class QQOneBotSkillTests(unittest.TestCase):
    def test_decode_body_preserves_long_json_without_shared_cut(self) -> None:
        payload = {"status": "ok", "retcode": 0, "data": {"text": "x" * 12000}}
        decoded = qq_onebot_call._decode_body(json.dumps(payload).encode())
        self.assertEqual(decoded, payload)

    def test_call_preserves_onebot_business_failure(self) -> None:
        body = b'{"status":"failed","retcode":100,"message":"permission denied","wording":"no"}'
        with patch.object(qq_onebot_call.urllib.request, "urlopen", return_value=_Response(body)):
            result = qq_onebot_call.call(3001, "token-value", "send_forward_msg", {})
        self.assertEqual(result["retcode"], 100)
        self.assertEqual(result["message"], "permission denied")
        self.assertEqual(result["reason"], "onebot_retcode_failed")

    def test_zero_retcode_is_success_even_when_integer_zero(self) -> None:
        self.assertTrue(qq_onebot_call._is_success({"status": "ok", "retcode": 0}))
        self.assertFalse(qq_onebot_call._is_success({"status": "ok", "retcode": 1}))

    def test_call_keeps_http_error_body(self) -> None:
        class _HTTPError(qq_onebot_call.urllib.error.HTTPError):
            def __init__(self) -> None:
                super().__init__("http://127.0.0.1:3001/send_forward_msg", 403, "forbidden", {}, None)

            def read(self) -> bytes:
                return b'{"status":"failed","retcode":403,"message":"bad token"}'

        with patch.object(qq_onebot_call.urllib.request, "urlopen", side_effect=_HTTPError()):
            result = qq_onebot_call.call(3001, "token-value", "send_forward_msg", {})
        self.assertEqual(result["http_status"], 403)
        self.assertEqual(result["message"], "bad token")


if __name__ == "__main__":
    unittest.main()
