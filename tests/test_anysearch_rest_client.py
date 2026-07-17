from __future__ import annotations

import json
import unittest
import urllib.error

from companion_v01.anysearch_rest_client import AnySearchRestClient, AnySearchRestError


class _Response:
    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self._raw[:limit]


class AnySearchRestClientTests(unittest.TestCase):
    def test_anonymous_search_omits_authorization_and_projects_results(self) -> None:
        requests = []

        def open_request(request, *, timeout):
            requests.append((request, timeout))
            return _Response(
                {
                    "code": 0,
                    "data": {
                        "results": [
                            {"title": "公开结果", "url": "https://example.cn/a", "snippet": "摘要"}
                        ]
                    },
                }
            )

        client = AnySearchRestClient(api_key_provider=lambda: "", urlopen=open_request)
        result = client.call(action="search", arguments={"query": "财经新闻", "max_results": 3})

        request, timeout = requests[0]
        self.assertNotIn("Authorization", dict(request.header_items()))
        self.assertGreater(timeout, 0)
        self.assertEqual(result["results"][0]["title"], "公开结果")

    def test_configured_key_is_sent_only_as_authorization_header(self) -> None:
        requests = []

        def open_request(request, *, timeout):
            requests.append(request)
            return _Response({"code": 0, "data": {"results": []}})

        client = AnySearchRestClient(api_key_provider=lambda: "private-key", urlopen=open_request)
        client.call(action="search", arguments={"query": "公开信息"})

        self.assertEqual(requests[0].get_header("Authorization"), "Bearer private-key")
        self.assertNotIn(b"private-key", requests[0].data)

    def test_rate_limit_is_structured_and_retryable(self) -> None:
        def open_request(request, *, timeout):
            raise urllib.error.HTTPError(request.full_url, 429, "limited", {}, None)

        client = AnySearchRestClient(api_key_provider=lambda: "", urlopen=open_request)

        with self.assertRaises(AnySearchRestError) as raised:
            client.call(action="search", arguments={"query": "公开信息"})

        self.assertEqual(raised.exception.reason, "anysearch_rate_limited")
        self.assertTrue(raised.exception.retryable)

    def test_batch_search_deduplicates_urls_across_queries(self) -> None:
        calls = 0

        def open_request(request, *, timeout):
            nonlocal calls
            calls += 1
            return _Response(
                {
                    "code": 0,
                    "data": {"results": [{"title": f"结果{calls}", "url": "https://example.cn/same"}]},
                }
            )

        client = AnySearchRestClient(api_key_provider=lambda: "", urlopen=open_request)
        result = client.call(
            action="batch_search",
            arguments={"queries": [{"query": "查询一"}, {"query": "查询二"}]},
        )

        self.assertEqual(calls, 2)
        self.assertEqual(len(result["results"]), 1)


if __name__ == "__main__":
    unittest.main()
