from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from companion_v01.local_capability_config import save_mcp_server_config
from companion_v01.tool_runtime import OpenBrowserToolHandler, RetrieveMemoryToolHandler, ToolExecutionContext, WebSearchToolHandler


class RetrieveMemoryToolHandlerTests(unittest.TestCase):
    def test_prompt_instruction_frames_retrieval_as_deep_memory_space(self) -> None:
        handler = RetrieveMemoryToolHandler(retrieve_fn=lambda **kwargs: None)

        instruction = handler.build_prompt_instruction()

        self.assertIn("自己的深层记忆空间", instruction)
        self.assertIn("生日、重要日期、偏好、称呼、旧约定", instruction)
        self.assertIn("可以自然在这里翻一下", instruction)
        self.assertIn("只要你觉得更早的记忆可能有帮助", instruction)

    def test_normalize_call_accepts_precision_filters_and_preserves_zero_importance(self) -> None:
        handler = RetrieveMemoryToolHandler(retrieve_fn=lambda **kwargs: None)

        call = handler.normalize_call(
            {
                "type": "retrieve_memory",
                "query": "我喜欢喝什么饮料",
                "keywords": "喜欢，可乐 饮料",
                "source_layers": ["raw", "semantic", "bad_layer"],
                "subject_scopes": ["用户", "other", "bad_scope"],
                "categories": "偏好,项目,bad_category",
                "importance_min": 0,
                "limit": 99,
            }
        )

        self.assertIsNotNone(call)
        assert call is not None
        self.assertEqual(call["source_layers"], ["raw", "semantic_summary"])
        self.assertEqual(call["subject_scopes"], ["user", "other"])
        self.assertEqual(call["categories"], ["preference", "project_work"])
        self.assertEqual(call["importance_min"], 0.0)
        self.assertEqual(call["limit"], 12)


class WebSearchToolHandlerTests(unittest.TestCase):
    def _context(self) -> ToolExecutionContext:
        return ToolExecutionContext(
            profile_user_id="master",
            session_id="desktop",
            now_ts=1712400000,
            visual_payload={},
            client_mode="desktop_pet",
        )

    def test_normalize_call_bounds_search_and_rejects_private_extract_url(self) -> None:
        handler = WebSearchToolHandler(config_base_dir="unused", mcp_tool_caller=object())

        search = handler.normalize_call(
            {
                "type": "web_search",
                "query": "AnySearch MCP 能力",
                "max_results": 99,
                "domain": "https://example.com/path",
            }
        )
        self.assertIsNotNone(search)
        assert search is not None
        self.assertEqual(search["action"], "search")
        self.assertEqual(search["max_results"], 10)
        self.assertEqual(search["domain"], "example.com")

        public_extract = handler.normalize_call(
            {
                "type": "web_search",
                "action": "extract",
                "url": "https://example.com/article",
                "max_chars": 99999,
            }
        )
        self.assertIsNotNone(public_extract)
        assert public_extract is not None
        self.assertEqual(public_extract["max_chars"], 5000)

        self.assertIsNone(
            handler.normalize_call(
                {
                    "type": "web_search",
                    "action": "extract",
                    "url": "http://127.0.0.1:9999/secret",
                }
            )
        )
        self.assertIsNone(
            handler.normalize_call(
                {
                    "type": "web_search",
                    "action": "extract",
                    "url": "file:///C:/Users/Lenovo/private.txt",
                }
            )
        )

    def test_execute_search_calls_anysearch_mcp_and_redacts_secret_material(self) -> None:
        class FakeCaller:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            async def __call__(self, *, server: dict, tool_name: str, arguments: dict) -> dict:
                self.calls.append({"server": server, "tool_name": tool_name, "arguments": arguments})
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "results": [
                                        {
                                            "title": "AnySearch 文档",
                                            "url": "https://example.com/anysearch",
                                            "snippet": "公开搜索结果，密钥 dotenv-secret Authorization: Bearer dotenv-secret",
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ]
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, ".env").write_text("ANYSEARCH_API_KEY=dotenv-secret\n", encoding="utf-8")
            saved = save_mcp_server_config(
                base_dir=temp_dir,
                profile_user_id="master",
                server_id="anysearch",
                payload={
                    "enabled": True,
                    "displayName": "AnySearch",
                    "command": "fake-anysearch",
                    "args": ["Authorization: Bearer ${ANYSEARCH_API_KEY}"],
                    "cwd": temp_dir,
                },
            )
            self.assertTrue(saved["ok"])

            caller = FakeCaller()
            handler = WebSearchToolHandler(config_base_dir=temp_dir, mcp_tool_caller=caller)
            call = handler.normalize_call(
                {"type": "web_search", "action": "search", "query": "AnySearch 是什么", "max_results": 3}
            )
            self.assertIsNotNone(call)
            assert call is not None

            result = handler.execute(call=call, context=self._context())

            self.assertEqual(caller.calls[0]["tool_name"], "search")
            self.assertEqual(caller.calls[0]["arguments"], {"query": "AnySearch 是什么", "max_results": 3})
            self.assertIn("AnySearch 联网搜索结果", result.followup_context)
            self.assertIn("AnySearch 文档", result.followup_context)
            self.assertIn("https://example.com/anysearch", result.followup_context)
            self.assertNotIn("dotenv-secret", result.followup_context)
            self.assertNotIn("Authorization: Bearer dotenv-secret", result.followup_context)
            self.assertNotIn(temp_dir, result.followup_context)

    def test_execute_returns_structured_unavailable_when_anysearch_is_unconfigured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handler = WebSearchToolHandler(config_base_dir=temp_dir, mcp_tool_caller=object())
            call = handler.normalize_call({"type": "web_search", "query": "今天的新闻"})
            self.assertIsNotNone(call)
            assert call is not None

            result = handler.execute(call=call, context=self._context())

            self.assertEqual(result.state_updates["web_search_status"], "unavailable")
            self.assertIn("AnySearch 联网能力暂时不可用", result.followup_context)
            self.assertIn("missing_config", result.followup_context)


class OpenBrowserToolHandlerTests(unittest.TestCase):
    def _context(self) -> ToolExecutionContext:
        return ToolExecutionContext(
            profile_user_id="master",
            session_id="desktop",
            now_ts=1712400000,
            visual_payload={},
            client_mode="desktop_pet",
        )

    def test_normalize_call_accepts_public_url_and_rejects_private_targets(self) -> None:
        handler = OpenBrowserToolHandler()

        call = handler.normalize_call(
            {
                "type": "open_browser",
                "url": "https://example.com/docs?x=1",
                "label": "Example Docs",
                "reason": "用户要求打开",
            }
        )
        self.assertIsNotNone(call)
        assert call is not None
        self.assertEqual(call["url"], "https://example.com/docs?x=1")
        self.assertEqual(call["label"], "Example Docs")

        for url in (
            "http://127.0.0.1:9999/health",
            "http://localhost/admin",
            "file:///C:/Users/Lenovo/private.txt",
            "https://user:pass@example.com/private",
            "https://example.com/a b",
        ):
            self.assertIsNone(handler.normalize_call({"type": "open_browser", "url": url}), url)

    def test_execute_only_emits_browser_open_request_event(self) -> None:
        handler = OpenBrowserToolHandler()
        call = handler.normalize_call({"type": "open_browser", "url": "https://example.com", "label": "Example"})
        self.assertIsNotNone(call)
        assert call is not None

        result = handler.execute(call=call, context=self._context())

        self.assertEqual(result.tool_type, "open_browser")
        self.assertEqual(result.stream_events[0]["type"], "browser_open_requested")
        self.assertEqual(result.stream_events[0]["url"], "https://example.com")
        self.assertFalse(result.stream_events[0]["requires_confirmation"])
        self.assertIn("不要声称你已经读取了网页内容", result.followup_context)


if __name__ == "__main__":
    unittest.main()
