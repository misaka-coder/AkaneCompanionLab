"""Search paging, malformed cursors and asynchronous readiness behavior."""

import tempfile
import threading
import unittest
from unittest.mock import patch

from companion_v01.anysearch_rest_client import AnySearchRestError
from companion_v01.mcp_stdio_discoverer import McpStdioDiscoveryError
from companion_v01.paged_reading import json_payload, make_paged_cursor
from companion_v01.tool_handlers.web_browser import WebSearchToolHandler
from tests.test_web_browser_boundaries import context
from tests.test_web_search_paged_results import FakeSearchRestClient


class WebSearchBoundaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name

    def handler(self, client):
        return WebSearchToolHandler(config_base_dir=self.directory, anysearch_rest_client=client,
                                    mcp_tool_caller=object(), readiness_probe_in_background=False)

    def test_single_huge_entry_is_bounded_and_lossless_across_pages(self):
        snippet = "\n".join(f"证据 {i} " + "字" * 100 for i in range(1300))
        handler = self.handler(FakeSearchRestClient({"q": {"results": [
            {"title": "large", "url": "https://example.com/large", "snippet": snippet},
            {"title": "last", "url": "https://example.com/last", "snippet": "tail"},
        ]}}))
        call = {"type": "web_search", "query": "q"}
        pages = []
        for _ in range(10):
            result = handler.execute(call=handler.normalize_call(call), context=context())
            self.assertLessEqual(len(result.followup_context), 53_000)
            self.assertEqual(result.followup_envelope.diagnostics["format"], "structured_text")
            body = result.followup_context.split("搜索结果文档：\n", 1)[1]
            body = body.split("\n文档已展示 ", 1)[0].split("\n已读完本次返回的搜索结果文档", 1)[0]
            pages.append(body)
            cursor = (result.followup_envelope.continuation or {}).get("cursor")
            if not cursor:
                self.assertTrue(result.followup_envelope.complete)
                break
            call = {"type": "web_search", "cursor": cursor}
        else:
            self.fail("paging did not terminate")
        joined = "".join(pages)
        self.assertIn(snippet, joined)
        self.assertIn("2. last", joined)
        self.assertEqual(joined.count("1. large"), 1)

    def test_huge_entry_change_after_first_page_is_a_terminal_failure(self):
        client = FakeSearchRestClient({"q": {"results": [{"title": "large", "snippet": "A" * 100_000}]}})
        handler = self.handler(client)
        first = handler.execute(call=handler.normalize_call({"type": "web_search", "query": "q"}), context=context())
        cursor = first.followup_envelope.continuation["cursor"]
        client.payload_by_query["q"]["results"][0]["snippet"] = "B" * 100_000
        page = handler.execute(call={"type": "web_search", "cursor": cursor}, context=context())
        self.assertEqual(page.followup_envelope.diagnostics["status"], "content_changed")
        self.assertIsNone(page.followup_envelope.continuation)

    def test_malformed_cursor_fields_are_rejected_before_provider_call(self):
        client = FakeSearchRestClient({})
        handler = self.handler(client)
        valid = {"a": "search", "args": {"query": "q"}, "s": 0, "o": 0, "f": "a" * 64}
        for changes in ({"s": "broken"}, {"o": -1}, {"o": []}, {"s": True}, {"args": 3},
                        {"args": {"query": "q", "cursor": "nested"}}, {"f": ""},
                        {"a": "extract", "args": {"url": "http://127.0.0.1/"}}):
            cursor = make_paged_cursor(tool="we", binding=handler._web_cursor_owner_binding(context()),
                                       payload=json_payload({**valid, **changes}))
            with self.subTest(changes=changes):
                result = handler.execute(call={"type": "web_search", "cursor": cursor}, context=context())
                self.assertEqual(result.followup_envelope.diagnostics["status"], "cursor_invalid")
        self.assertEqual(client.calls, [])

    def test_malformed_source_url_does_not_discard_other_search_evidence(self):
        handler = self.handler(FakeSearchRestClient({"q": {"results": [
            {"title": "bad link", "url": "https://[broken/", "snippet": "public evidence"},
            {"title": "valid", "url": "https://example.com/ok"},
        ]}}))
        result = handler.execute(call=handler.normalize_call({"type": "web_search", "query": "q"}), context=context())
        self.assertIn("public evidence", result.followup_context)
        self.assertIn("https://example.com/ok", result.followup_context)

    def test_bad_domain_url_is_ignored_without_raising(self):
        handler = self.handler(FakeSearchRestClient({}))
        result = handler.normalize_call({"type": "web_search", "query": "q", "domain": "https://[broken/"})
        self.assertNotIn("domain", result)


class WebSearchReadinessBoundaryTests(unittest.TestCase):
    server = {"enabled": True, "command": "fixture-search", "args": []}

    def test_mcp_error_probe_uses_the_same_available_rest_fallback_as_execution(self):
        class Caller:
            async def __call__(self, **kwargs):
                return {"isError": True, "reason": "fixture_search_failed"}

        with tempfile.TemporaryDirectory() as directory:
            handler = WebSearchToolHandler(config_base_dir=directory, mcp_tool_caller=Caller(),
                                           anysearch_rest_client=FakeSearchRestClient({}), readiness_probe_in_background=False)
            with patch("companion_v01.tool_handlers.web_browser_runtime.search_readiness.get_mcp_server_runtime_config", return_value=self.server):
                result = handler.capability_status(profile_user_id="p1")
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["healthy"])
        self.assertEqual(result["fallback_from"], "mcp")

    def test_slow_failed_probe_cannot_replace_newer_successful_execution(self):
        entered = threading.Event()
        release = threading.Event()

        class ProbeCaller:
            async def __call__(self, **kwargs):
                entered.set()
                if not release.wait(timeout=5):
                    raise RuntimeError("fixture timeout")
                raise McpStdioDiscoveryError("fixture_probe_failed")

        class Caller:
            async def __call__(self, **kwargs):
                return {"results": [{"title": "fresh", "url": "https://example.com"}]}

        class Rest:
            endpoint = "https://fixture-rest.test"

            def call(self, **kwargs):
                raise AnySearchRestError("anysearch_network_unavailable")

        with tempfile.TemporaryDirectory() as directory:
            handler = WebSearchToolHandler(config_base_dir=directory, mcp_tool_caller=Caller(),
                                           readiness_mcp_tool_caller=ProbeCaller(), anysearch_rest_client=Rest(),
                                           readiness_probe_in_background=False)
            key = ("p1", handler._server_readiness_fingerprint(self.server))
            worker = threading.Thread(target=handler._probe_readiness, kwargs={"cache_key": key, "server": self.server})
            worker.start()
            try:
                self.assertTrue(entered.wait(timeout=2))
                with patch("companion_v01.tool_handlers.web_browser_runtime.search_execution.get_mcp_server_runtime_config", return_value=self.server):
                    result = handler.execute(call={"type": "web_search", "query": "q"}, context=context())
                self.assertEqual(result.state_updates["web_search_status"], "ok")
            finally:
                release.set()
                worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            with patch("companion_v01.tool_handlers.web_browser_runtime.search_readiness.get_mcp_server_runtime_config", return_value=self.server):
                status = handler.capability_status(profile_user_id="p1")
            self.assertEqual(status["status"], "ready")
            self.assertTrue(status["healthy"])

    def test_readiness_cache_has_a_hard_bound_even_when_entries_are_fresh(self):
        handler = WebSearchToolHandler(config_base_dir="unused", anysearch_rest_client=FakeSearchRestClient({}),
                                       mcp_tool_caller=object(), readiness_probe_in_background=False)
        for i in range(40):
            handler.capability_status(profile_user_id=f"fixture{i}")
        self.assertLessEqual(len(handler._readiness_cache), 32)

    def test_header_secret_is_redacted_even_when_provider_echoes_only_its_value(self):
        handler = WebSearchToolHandler(config_base_dir="unused", mcp_tool_caller=object())
        terms = handler._redaction_terms_for_server({"headers": {"Authorization": "Bearer fixture-header-secret"}})
        result = handler._paged_extract_result(call={"url": "https://example.com"},
                                               result={"text": "echo: fixture-header-secret"}, redaction_terms=terms,
                                               start_offset=0, expected_fingerprint="", owner_binding="p1", profile_user_id="p1")
        self.assertNotIn("fixture-header-secret", result.followup_context)


if __name__ == "__main__":
    unittest.main()
