"""Phase 3: paged web search / extraction results (complete entries, cursor, fingerprints)."""

from __future__ import annotations

import tempfile
import unittest

from companion_v01.tool_handlers.core import ToolExecutionContext
from companion_v01.tool_handlers.web_browser import WebSearchToolHandler
from companion_v01.local_capability_config import save_mcp_server_config


class FakeSearchRestClient:
    """Deterministic AnySearch REST stand-in returning canned payloads per query."""

    def __init__(self, payload_by_query: dict) -> None:
        self.payload_by_query = payload_by_query
        self.calls: list[tuple[str, dict]] = []

    @property
    def endpoint(self) -> str:
        return "https://fake-anysearch.test"

    def call(self, *, action: str, arguments: dict) -> dict:
        self.calls.append((action, dict(arguments)))
        if action == "search":
            return self.payload_by_query.get(str(arguments.get("query") or ""), {"results": []})
        if action == "extract":
            return self.payload_by_query.get(str(arguments.get("url") or ""), {"text": ""})
        return {"results": []}


def _context(profile_user_id: str = "p1", session_id: str = "s1") -> ToolExecutionContext:
    return ToolExecutionContext(
        profile_user_id=profile_user_id,
        session_id=session_id,
        now_ts=1_700_000_000,
        visual_payload={},
        client_mode="desktop_pet",
    )


class WebSearchPagedTests(unittest.TestCase):
    def _handler(self, client: FakeSearchRestClient) -> WebSearchToolHandler:
        temp_dir = tempfile.mkdtemp(prefix="akane_ws_search_")
        return WebSearchToolHandler(
            config_base_dir=temp_dir,
            anysearch_rest_client=client,
            readiness_probe_in_background=False,
        )

    def test_search_pages_by_complete_entries_without_mid_entry_cuts(self) -> None:
        fat_results = [
            {
                "title": f"结果 {index} 标题",
                "url": f"https://example.com/{index}",
                "snippet": f"摘要{index} " + "字" * 4000,
                "published_at": "2026-08-01",
            }
            for index in range(10)
        ]
        client = FakeSearchRestClient({"q": {"results": fat_results}})
        handler = self._handler(client)

        first = handler.execute(
            call=handler.normalize_call({"type": "web_search", "query": "q", "max_results": 10}) or {},
            context=_context(),
        )
        envelope = first.followup_envelope
        self.assertTrue(envelope.producer_bounded)
        self.assertFalse(envelope.complete)
        self.assertNotIn("[truncated]", str(first.followup_context))
        self.assertNotIn("已截断", str(first.followup_context))
        cursor = envelope.continuation["cursor"]

        continuation = handler.execute(call={"type": "web_search", "cursor": cursor}, context=_context())
        self.assertEqual(continuation.state_updates["web_search_status"], "ok")
        self.assertTrue(continuation.followup_envelope.complete)
        self.assertIsNone(continuation.followup_envelope.continuation)
        # re-query happened with the original arguments
        self.assertEqual(client.calls[0], ("search", {"query": "q", "max_results": 10}))
        self.assertEqual(client.calls[1], client.calls[0])

    def test_markdown_fourteen_results_are_normalized_without_raw_clipping(self) -> None:
        markdown = "\n\n".join(
            f"{index}. [结果 {index}](https://example.com/{index})\n摘要 {index}，包含完整证据。"
            for index in range(1, 15)
        )
        client = FakeSearchRestClient({"markdown": {"results": markdown}})
        handler = self._handler(client)

        result = handler.execute(
            call=handler.normalize_call({"type": "web_search", "query": "markdown"}) or {},
            context=_context(),
        )

        self.assertEqual(result.followup_envelope.diagnostics["entry_count"], 14)
        self.assertIn("结果 14", result.followup_context)
        self.assertNotIn("[truncated]", result.followup_context)
        self.assertNotIn("没有拿到可用搜索结果", result.followup_context)

    def test_unstructured_markdown_is_returned_as_lossless_paged_raw_document(self) -> None:
        raw = "# 搜索服务原始回答\n" + "\n".join(f"证据行 {index} {'字' * 120}" for index in range(900))
        client = FakeSearchRestClient({"raw": {"results": raw}})
        handler = self._handler(client)

        first = handler.execute(
            call=handler.normalize_call({"type": "web_search", "query": "raw"}) or {},
            context=_context(),
        )
        self.assertEqual(first.followup_envelope.diagnostics["format"], "markdown_raw")
        self.assertFalse(first.followup_envelope.complete)
        self.assertNotIn("[truncated]", first.followup_context)
        cursor = first.followup_envelope.continuation["cursor"]
        pages = [first.followup_context]
        while cursor:
            page = handler.execute(call={"type": "web_search", "cursor": cursor}, context=_context())
            pages.append(page.followup_context)
            cursor = (page.followup_envelope.continuation or {}).get("cursor")
        joined = "\n".join(pages)
        self.assertIn("证据行 0", joined)
        self.assertIn("证据行 899", joined)

    def test_explicit_empty_result_is_no_results_not_parse_failure(self) -> None:
        handler = self._handler(FakeSearchRestClient({"empty": {"results": []}}))
        result = handler.execute(
            call=handler.normalize_call({"type": "web_search", "query": "empty"}) or {},
            context=_context(),
        )

        self.assertEqual(result.state_updates["web_search_status"], "no_results")
        self.assertEqual(result.followup_envelope.diagnostics["status"], "no_results")

    def test_mcp_error_result_falls_back_to_rest_on_the_same_execution(self) -> None:
        class ErrorMcpCaller:
            def __init__(self) -> None:
                self.calls = 0

            async def __call__(self, **_kwargs):
                self.calls += 1
                return {"isError": True, "content": [{"type": "text", "text": "provider_timeout"}]}

        with tempfile.TemporaryDirectory(prefix="akane_ws_fallback_") as temp_dir:
            save_mcp_server_config(
                base_dir=temp_dir,
                profile_user_id="p1",
                server_id="anysearch",
                payload={
                    "enabled": True,
                    "displayName": "AnySearch",
                    "command": "configured-anysearch",
                    "args": [],
                    "cwd": temp_dir,
                },
            )
            rest = FakeSearchRestClient(
                {"fallback": {"results": [{"title": "REST 结果", "url": "https://example.com/fallback"}]}}
            )
            mcp = ErrorMcpCaller()
            handler = WebSearchToolHandler(
                config_base_dir=temp_dir,
                mcp_tool_caller=mcp,
                anysearch_rest_client=rest,
                readiness_probe_in_background=True,
            )

            result = handler.execute(
                call=handler.normalize_call({"type": "web_search", "query": "fallback"}) or {},
                context=_context(),
            )

            self.assertEqual(mcp.calls, 1)
            self.assertIn("REST 结果", result.followup_context)
            self.assertEqual(result.followup_envelope.diagnostics["providers_attempted"], ["mcp", "rest"])
            self.assertTrue(result.followup_envelope.diagnostics["fallback_used"])

    def test_search_result_change_returns_structured_content_changed(self) -> None:
        client = FakeSearchRestClient({"q": {"results": [
            {"title": f"结果 {index}", "url": f"https://example.com/{index}", "snippet": "字" * 4000}
            for index in range(10)
        ]}})
        handler = self._handler(client)
        first = handler.execute(
            call=handler.normalize_call({"type": "web_search", "query": "q"}) or {},
            context=_context(),
        )
        cursor = first.followup_envelope.continuation["cursor"]
        client.payload_by_query["q"] = {"results": [{"title": "完全不同", "url": "https://example.com/x", "snippet": "新"}]}
        continuation = handler.execute(call={"type": "web_search", "cursor": cursor}, context=_context())
        self.assertEqual(continuation.followup_envelope.diagnostics["status"], "content_changed")
        self.assertIn("content_changed", str(continuation.followup_context))

    def test_search_cursor_rejected_cross_session(self) -> None:
        client = FakeSearchRestClient({"q": {"results": [
            {"title": f"结果 {index}", "url": f"https://example.com/{index}", "snippet": "字" * 4000}
            for index in range(10)
        ]}})
        handler = self._handler(client)
        first = handler.execute(
            call=handler.normalize_call({"type": "web_search", "query": "q"}) or {},
            context=_context(),
        )
        cursor = first.followup_envelope.continuation["cursor"]
        foreign = handler.execute(
            call={"type": "web_search", "cursor": cursor}, context=_context(session_id="other")
        )
        self.assertEqual(foreign.followup_envelope.diagnostics["status"], "cursor_invalid")

    def test_extract_pages_100k_text_losslessly(self) -> None:
        text = "头\n" + "\n".join(f"第{i}行 网页正文-{'字'*50}" for i in range(2000))
        client = FakeSearchRestClient({"https://example.com/article": {"text": text, "title": "长文"}})
        handler = self._handler(client)

        first = handler.execute(
            call=handler.normalize_call({"type": "web_search", "action": "extract", "url": "https://example.com/article"}) or {},
            context=_context(),
        )
        envelope = first.followup_envelope
        self.assertFalse(envelope.complete)
        joined = self._extract_page_text(first.followup_context)
        cursor = envelope.continuation["cursor"]
        while cursor:
            continuation = handler.execute(call={"type": "web_search", "cursor": cursor}, context=_context())
            joined += self._extract_page_text(continuation.followup_context)
            next_cont = continuation.followup_envelope.continuation
            cursor = next_cont["cursor"] if next_cont else None
        self.assertEqual(joined, text)
        self.assertIn("长文", first.followup_context)
        self.assertIn("https://example.com/article", first.followup_context)

    @staticmethod
    def _extract_page_text(followup: str) -> str:
        marker = "正文摘录：\n"
        position = followup.find(marker)
        if position == -1:
            return ""
        body = followup[position + len(marker):]
        for tail in ("\n正文还有未展示部分。", "\n已读完本次提取到的全部正文"):
            cut = body.find(tail)
            if cut != -1:
                body = body[:cut]
        return body

    def test_extract_changed_page_returns_stale_cursor(self) -> None:
        text = "A" * 80_000
        client = FakeSearchRestClient({"https://example.com/article": {"text": text}})
        handler = self._handler(client)
        first = handler.execute(
            call=handler.normalize_call({"type": "web_search", "action": "extract", "url": "https://example.com/article"}) or {},
            context=_context(),
        )
        cursor = first.followup_envelope.continuation["cursor"]
        client.payload_by_query["https://example.com/article"] = {"text": "B" * 80_000}
        continuation = handler.execute(call={"type": "web_search", "cursor": cursor}, context=_context())
        self.assertEqual(continuation.followup_envelope.diagnostics["status"], "stale_cursor")

    def test_extract_secret_material_never_enters_cursor_or_page(self) -> None:
        client = FakeSearchRestClient(
            {"https://example.com/article": {"text": "api_key=TOP-SECRET-123\n正文" + "字" * 60_000}}
        )
        handler = self._handler(client)
        first = handler.execute(
            call=handler.normalize_call({"type": "web_search", "action": "extract", "url": "https://example.com/article"}) or {},
            context=_context(),
        )
        self.assertNotIn("TOP-SECRET-123", str(first.followup_context))
        cursor = first.followup_envelope.continuation["cursor"]
        self.assertNotIn("TOP-SECRET-123", cursor)

    def test_sub_domains_result_is_complete_bounded(self) -> None:
        client = FakeSearchRestClient({"d": {"domains": ["a.example.com", "b.example.com"]}})
        handler = self._handler(client)
        result = handler.execute(
            call=handler.normalize_call(
                {"type": "web_search", "action": "get_sub_domains", "domains": ["example.com"]}
            )
            or {},
            context=_context(),
        )
        self.assertTrue(result.followup_envelope.producer_bounded)
        self.assertTrue(result.followup_envelope.complete)


if __name__ == "__main__":
    unittest.main()
