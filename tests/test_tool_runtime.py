from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from companion_v01.capability_adapters import CapabilityDescriptor, CapabilityIOSlot, CapabilityResult
from companion_v01.local_capability_config import save_approval_policy_config, save_mcp_server_config
from companion_v01.browser_page_runtime import BrowserPageResult, ManagedBrowserPageRunner
from companion_v01.tool_runtime import (
    AdapterCapabilityToolHandler,
    BrowserPageToolHandler,
    OpenBrowserToolHandler,
    OpenMusicSearchToolHandler,
    RetrieveMemoryToolHandler,
    ToolMetadata,
    ToolExecutionContext,
    WebSearchToolHandler,
)
from companion_v01.anysearch_rest_client import AnySearchRestError
from companion_v01.mcp_stdio_discoverer import McpStdioDiscoveryError


class RetrieveMemoryToolHandlerTests(unittest.TestCase):
    def test_prompt_instruction_frames_retrieval_as_deep_memory_space(self) -> None:
        handler = RetrieveMemoryToolHandler(retrieve_fn=lambda **kwargs: None)

        instruction = handler.build_prompt_instruction()

        self.assertIn(handler.tool_spec().description, instruction)
        self.assertIn("entity_anchors", instruction)
        self.assertIn("time_hint.start_at/end_at", instruction)
        self.assertIn("within_memory_id", instruction)
        self.assertIn("top raw-first ranked", instruction)
        self.assertTrue(instruction.endswith("这是内部记忆读取，不要先在 speech 里宣布。"))

    def test_normalize_call_accepts_canonical_raw_first_filters_without_hidden_limits(self) -> None:
        handler = RetrieveMemoryToolHandler(retrieve_fn=lambda **kwargs: None)

        call = handler.normalize_call(
            {
                "type": "retrieve_memory",
                "query": "我喜欢喝什么饮料",
                "entity_anchors": "可乐，无糖可乐",
                "topic_terms": ["喜欢", "饮料", "喜欢"],
                "source_layers": ["raw", "semantic_summary"],
                "memory_facets": ["preference", "decision"],
                "about_roles": ["user", "external"],
                "within_memory_id": " episode-cola ",
            }
        )

        self.assertIsNotNone(call)
        assert call is not None
        self.assertEqual(call["entity_anchors"], ["可乐", "无糖可乐"])
        self.assertEqual(call["topic_terms"], ["喜欢", "饮料"])
        self.assertEqual(call["source_layers"], ["raw", "semantic_summary"])
        self.assertEqual(call["memory_facets"], ["preference", "decision"])
        self.assertEqual(call["about_roles"], ["user", "external"])
        self.assertEqual(call["within_memory_id"], "episode-cola")
        self.assertNotIn("limit", call)
        self.assertNotIn("importance_min", call)

    def test_normalize_call_rejects_non_string_within_memory_id_instead_of_broadening(self) -> None:
        handler = RetrieveMemoryToolHandler(retrieve_fn=lambda **kwargs: None)

        self.assertIsNone(
            handler.normalize_call(
                {
                    "type": "retrieve_memory",
                    "query": "早茶同行者",
                    "within_memory_id": ["episode-1"],
                }
            )
        )

    def test_normalize_call_preserves_package_owned_exact_time_hint(self) -> None:
        handler = RetrieveMemoryToolHandler(retrieve_fn=lambda **kwargs: None)

        call = handler.normalize_call(
            {
                "type": "retrieve_memory",
                "query": "和 misaka 一起来玩的另一个人是谁",
                "entity_anchors": ["misaka"],
                "topic_terms": ["一起来玩", "同行"],
                "memory_facets": ["relationship"],
                "about_roles": ["third_party"],
                "time_hint": {
                    "start_at": "2026-08-03 11:00",
                    "end_at": "2026-08-03 12:00",
                },
            }
        )

        self.assertIsNotNone(call)
        assert call is not None
        self.assertEqual(
            call["time_hint"],
            {
                "start_at": "2026-08-03 11:00",
                "end_at": "2026-08-03 12:00",
            },
        )
        self.assertEqual(call["entity_anchors"], ["misaka"])
        self.assertNotIn("李嘉图", repr(call))

    def test_normalize_call_does_not_hide_mixed_time_modes_from_memcore_validation(self) -> None:
        handler = RetrieveMemoryToolHandler(retrieve_fn=lambda **kwargs: None)

        call = handler.normalize_call(
            {
                "type": "retrieve_memory",
                "query": "昨天上午的同行是谁",
                "time_hint": {
                    "start_at": "2026-08-03 11:00",
                    "end_at": "2026-08-03 12:00",
                    "date_label": "2026-08-03",
                },
            }
        )

        self.assertIsNotNone(call)
        assert call is not None
        self.assertEqual(
            call["time_hint"],
            {
                "start_at": "2026-08-03 11:00",
                "end_at": "2026-08-03 12:00",
                "date_label": "2026-08-03",
            },
        )

    def test_normalize_call_preserves_explicit_kind_request_for_host_authorization(self) -> None:
        handler = RetrieveMemoryToolHandler(retrieve_fn=lambda **kwargs: None)

        call = handler.normalize_call(
            {
                "type": "retrieve_memory",
                "query": "政策快讯",
                "include_explicit": True,
                "kind_patterns": ["EVENT.FINANCE.*", "event.finance.*", "message.*", "bad pattern"],
            }
        )

        self.assertIsNotNone(call)
        assert call is not None
        self.assertTrue(call["include_explicit"])
        self.assertEqual(call["kind_patterns"], ["event.finance.*", "message.*", "bad pattern"])


class AdapterCapabilityToolHandlerTests(unittest.TestCase):
    def _context(self) -> ToolExecutionContext:
        return ToolExecutionContext(
            profile_user_id="master",
            session_id="desktop",
            now_ts=1712400000,
            visual_payload={},
            client_mode="desktop_pet",
        )

    def _descriptor(
        self,
        *,
        risk: str = "low",
        confirm: str = "never",
        inputs: tuple[CapabilityIOSlot, ...] | None = None,
    ) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            id="mcp.demo.echo",
            display_name="echo",
            short_hint="Echo text",
            visible_in=("desktop",),
            prompt_exposed=True,
            risk=risk,
            confirm=confirm,
            effects=(),
            trigger=None,
            inputs=inputs or (CapabilityIOSlot(name="text", kind="string", required=True),),
            outputs=(),
            raw={
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                }
            },
        )

    def test_adapter_capability_validates_args_before_permission_or_invoke(self) -> None:
        class FakeAdapter:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            async def invoke(self, capability_id: str, args: dict[str, object], ctx: object) -> CapabilityResult:
                self.calls.append({"capability_id": capability_id, "args": dict(args), "ctx": ctx})
                return CapabilityResult(is_error=False, content={"content": []}, status="ok")

        adapter = FakeAdapter()
        handler = AdapterCapabilityToolHandler(
            capability_id="mcp.demo.echo",
            adapter=adapter,
            descriptor=self._descriptor(risk="high", confirm="always"),
            config_base_dir="unused",
        )

        result = handler.execute(
            call={"type": "mcp.demo.echo", "arguments": {}},
            context=self._context(),
        )

        self.assertEqual(adapter.calls, [])
        self.assertEqual(result.stream_events[0]["type"], "adapter_capability_failed")
        self.assertEqual(result.stream_events[0]["status"], "validation_error")
        self.assertEqual(result.stream_events[0]["reason"], "missing_required")

    def test_adapter_capability_keeps_unknown_args_for_capcore_validation(self) -> None:
        class FakeAdapter:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            async def invoke(self, capability_id: str, args: dict[str, object], ctx: object) -> CapabilityResult:
                self.calls.append({"capability_id": capability_id, "args": dict(args), "ctx": ctx})
                return CapabilityResult(is_error=False, content={"content": []}, status="ok")

        adapter = FakeAdapter()
        handler = AdapterCapabilityToolHandler(
            capability_id="mcp.demo.echo",
            adapter=adapter,
            descriptor=self._descriptor(),
            config_base_dir="unused",
        )

        call = handler.normalize_call(
            {
                "type": "mcp.demo.echo",
                "text": "hello",
                "extra": "should fail in capcore",
            }
        )
        self.assertIsNotNone(call)
        assert call is not None
        self.assertEqual(call["arguments"]["extra"], "should fail in capcore")

        result = handler.execute(call=call, context=self._context())

        self.assertEqual(adapter.calls, [])
        self.assertEqual(result.stream_events[0]["type"], "adapter_capability_failed")
        self.assertEqual(result.stream_events[0]["status"], "validation_error")
        self.assertEqual(result.stream_events[0]["reason"], "unknown_argument")

    def test_adapter_capability_does_not_treat_native_wire_metadata_as_arguments(self) -> None:
        handler = AdapterCapabilityToolHandler(
            capability_id="mcp.demo.echo",
            adapter=object(),
            descriptor=self._descriptor(),
            config_base_dir="unused",
        )

        call = handler.normalize_call(
            {
                "type": "mcp.demo.echo",
                "text": "hello",
                "_tool_source": "native_openai",
                "_tool_invocation_id": "call_native_1",
                "_tool_model_name": "mcp_demo_echo_abcd123456",
            }
        )

        self.assertEqual(call, {"type": "mcp.demo.echo", "arguments": {"text": "hello"}})

    def test_adapter_capability_prompt_schema_uses_capcore_projection(self) -> None:
        descriptor = self._descriptor(
            inputs=(
                CapabilityIOSlot(
                    name="text",
                    kind="string",
                    required=True,
                    raw={"description": "Text to echo"},
                ),
            )
        )
        descriptor = CapabilityDescriptor(
            id=descriptor.id,
            display_name=descriptor.display_name,
            short_hint=descriptor.short_hint,
            visible_in=descriptor.visible_in,
            prompt_exposed=descriptor.prompt_exposed,
            risk=descriptor.risk,
            confirm=descriptor.confirm,
            effects=descriptor.effects,
            trigger=descriptor.trigger,
            inputs=descriptor.inputs,
            outputs=descriptor.outputs,
            raw={
                "inputSchema": {
                    "type": "object",
                    "properties": {"wrong": {"type": "string"}},
                    "required": ["wrong"],
                }
            },
        )
        handler = AdapterCapabilityToolHandler(
            capability_id="mcp.demo.echo",
            adapter=object(),
            descriptor=descriptor,
            config_base_dir="unused",
        )

        instruction = handler.build_prompt_instruction()
        metadata = handler.tool_metadata()

        self.assertIn("text:string required - Text to echo", instruction)
        self.assertNotIn("wrong", instruction)
        assert metadata.input_schema is not None
        self.assertIn("text", metadata.input_schema["properties"])
        self.assertNotIn("wrong", metadata.input_schema["properties"])

    def test_adapter_capability_requires_approval_with_capcore_preview(self) -> None:
        class FakeAdapter:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            async def invoke(self, capability_id: str, args: dict[str, object], ctx: object) -> CapabilityResult:
                self.calls.append({"capability_id": capability_id, "args": dict(args), "ctx": ctx})
                return CapabilityResult(is_error=False, content={"content": []}, status="ok")

        adapter = FakeAdapter()
        handler = AdapterCapabilityToolHandler(
            capability_id="mcp.demo.echo",
            adapter=adapter,
            descriptor=self._descriptor(
                risk="high",
                confirm="always",
                inputs=(
                    CapabilityIOSlot(name="text", kind="string", required=True),
                    CapabilityIOSlot(name="api_key", kind="string", required=True),
                    CapabilityIOSlot(name="local_path", kind="string", required=True),
                ),
            ),
            config_base_dir="unused",
        )

        result = handler.execute(
            call={
                "type": "mcp.demo.echo",
                "arguments": {
                    "text": "hello",
                    "api_key": "plain-secret-value",
                    "local_path": r"C:\Users\ExampleUser\secret.txt",
                },
            },
            context=self._context(),
        )

        event = result.stream_events[0]
        self.assertEqual(adapter.calls, [])
        self.assertEqual(event["type"], "capability_approval_required")
        self.assertEqual(event["risk"], "high")
        self.assertEqual(event["approvalMode"], "ask_each_time")
        self.assertEqual(event["payloadPreview"]["api_key"], "[redacted]")
        self.assertEqual(event["payloadPreview"]["local_path"], "[local_path]")

    def test_adapter_capability_trusted_auto_allow_executes_required_tool(self) -> None:
        class FakeAdapter:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            async def invoke(self, capability_id: str, args: dict[str, object], ctx: object) -> CapabilityResult:
                self.calls.append({"capability_id": capability_id, "args": dict(args), "ctx": ctx})
                return CapabilityResult(
                    is_error=False,
                    content={"content": [{"type": "text", "text": "done"}]},
                    status="ok",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            saved = save_approval_policy_config(
                base_dir=temp_dir,
                profile_user_id="master",
                payload={"defaultMode": "trusted_auto_allow"},
            )
            self.assertTrue(saved["ok"])
            adapter = FakeAdapter()
            handler = AdapterCapabilityToolHandler(
                capability_id="mcp.demo.echo",
                adapter=adapter,
                descriptor=self._descriptor(risk="high", confirm="always"),
                config_base_dir=temp_dir,
            )

            result = handler.execute(
                call={"type": "mcp.demo.echo", "arguments": {"text": "hello"}},
                context=self._context(),
            )

        self.assertEqual(adapter.calls[0]["capability_id"], "mcp.demo.echo")
        self.assertEqual(adapter.calls[0]["args"], {"text": "hello"})
        self.assertEqual(result.stream_events[0]["type"], "adapter_capability_completed")
        self.assertEqual(result.stream_events[0]["status"], "ok")
        self.assertIn("done", result.followup_context)

    def test_adapter_capability_projects_only_safe_provider_identity(self) -> None:
        class FakeAdapter:
            async def invoke(self, capability_id: str, args: dict[str, object], ctx: object) -> CapabilityResult:
                return CapabilityResult(
                    is_error=False,
                    content={"result": {"provider": "eastmoney_public_market"}},
                    status="ok",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            save_approval_policy_config(
                base_dir=temp_dir,
                profile_user_id="master",
                payload={"defaultMode": "trusted_auto_allow"},
            )
            handler = AdapterCapabilityToolHandler(
                capability_id="akane.finance.quote_snapshot.v1",
                adapter=FakeAdapter(),
                descriptor=self._descriptor(risk="low", confirm="never"),
                config_base_dir=temp_dir,
            )

            result = handler.execute(
                call={"type": "akane.finance.quote_snapshot.v1", "arguments": {"text": "hello"}},
                context=self._context(),
            )

        self.assertEqual(result.stream_events[0]["provider"], "eastmoney_public_market")


class WebSearchToolHandlerTests(unittest.TestCase):
    def _context(self) -> ToolExecutionContext:
        return ToolExecutionContext(
            profile_user_id="master",
            session_id="desktop",
            now_ts=1712400000,
            visual_payload={},
            client_mode="desktop_pet",
        )

    def test_tool_metadata_marks_web_search_as_read_only_research_family(self) -> None:
        metadata = WebSearchToolHandler(config_base_dir="unused", mcp_tool_caller=object()).tool_metadata()

        self.assertIsInstance(metadata, ToolMetadata)
        self.assertEqual(metadata.family, "web_research")
        self.assertEqual(metadata.operation, "read")
        self.assertEqual(metadata.risk, "low")
        self.assertGreaterEqual(metadata.default_round_budget, 6)

    def test_capability_status_uses_direct_rest_when_mcp_config_is_missing(self) -> None:
        class FakeRestClient:
            endpoint = "https://api.anysearch.test/v1/search"

            def call(self, *, action: str, arguments: dict) -> dict:
                return {"results": []}

        with tempfile.TemporaryDirectory() as temp_dir:
            handler = WebSearchToolHandler(
                config_base_dir=temp_dir,
                mcp_tool_caller=object(),
                anysearch_rest_client=FakeRestClient(),
                readiness_probe_in_background=False,
            )

            status = handler.capability_status(profile_user_id="master", client_mode="desktop_pet")

            self.assertTrue(status["enabled"])
            self.assertEqual(status["status"], "ready")
            self.assertEqual(status["transport"], "rest")

    def test_default_readiness_probe_uses_configured_web_search_timeout(self) -> None:
        with patch("companion_v01.tool_runtime.config.WEB_SEARCH_MCP_TIMEOUT_SECONDS", 17.0):
            handler = WebSearchToolHandler(config_base_dir="unused")

        self.assertEqual(handler.mcp_tool_caller.timeout_seconds, 17.0)
        self.assertEqual(handler.readiness_mcp_tool_caller.timeout_seconds, 17.0)
        self.assertEqual(handler.anysearch_rest_client.timeout_seconds, 17.0)

    def test_capability_status_actively_probes_anysearch_and_caches_success(self) -> None:
        class FakeCaller:
            def __init__(self) -> None:
                self.calls = []

            async def __call__(self, *, server: dict, tool_name: str, arguments: dict) -> dict:
                self.calls.append((tool_name, arguments))
                return {"content": [{"type": "text", "text": '{"results": []}'}]}

        with tempfile.TemporaryDirectory() as temp_dir:
            saved = save_mcp_server_config(
                base_dir=temp_dir,
                profile_user_id="master",
                server_id="anysearch",
                payload={
                    "enabled": True,
                    "displayName": "AnySearch",
                    "command": "fake-anysearch",
                    "args": [],
                    "cwd": temp_dir,
                },
            )
            self.assertTrue(saved["ok"])
            caller = FakeCaller()
            handler = WebSearchToolHandler(
                config_base_dir=temp_dir,
                mcp_tool_caller=caller,
                readiness_probe_in_background=False,
            )

            first = handler.capability_status(profile_user_id="master", client_mode="desktop_pet")
            second = handler.capability_status(profile_user_id="master", client_mode="desktop_pet")

            self.assertTrue(first["enabled"])
            self.assertEqual(second["status"], "ready")
            self.assertEqual(caller.calls, [("search", {"query": "OpenAI", "max_results": 1})])

    def test_capability_status_falls_back_to_rest_when_mcp_process_fails(self) -> None:
        class FailingCaller:
            def __init__(self) -> None:
                self.calls = 0

            async def __call__(self, *, server: dict, tool_name: str, arguments: dict) -> dict:
                self.calls += 1
                raise McpStdioDiscoveryError("mcp_stdout_closed")

        class FakeRestClient:
            endpoint = "https://api.anysearch.test/v1/search"

            def __init__(self) -> None:
                self.calls = []

            def call(self, *, action: str, arguments: dict) -> dict:
                self.calls.append((action, arguments))
                return {"results": []}

        with tempfile.TemporaryDirectory() as temp_dir:
            save_mcp_server_config(
                base_dir=temp_dir,
                profile_user_id="master",
                server_id="anysearch",
                payload={
                    "enabled": True,
                    "displayName": "AnySearch",
                    "command": "broken-anysearch",
                    "args": [],
                    "cwd": temp_dir,
                },
            )
            rest = FakeRestClient()
            handler = WebSearchToolHandler(
                config_base_dir=temp_dir,
                mcp_tool_caller=FailingCaller(),
                anysearch_rest_client=rest,
                readiness_probe_in_background=False,
            )

            status = handler.capability_status(profile_user_id="master", client_mode="desktop_pet")

            self.assertTrue(status["enabled"])
            self.assertEqual(status["status"], "ready")
            self.assertEqual(status["transport"], "rest")
            self.assertEqual(status["fallback_from"], "mcp")
            self.assertEqual(rest.calls, [("search", {"query": "OpenAI", "max_results": 1})])

    def test_default_capability_probe_keeps_tool_exposed_while_background_check_runs(self) -> None:
        completed = threading.Event()

        class FakeCaller:
            async def __call__(self, *, server: dict, tool_name: str, arguments: dict) -> dict:
                completed.set()
                return {"content": [{"type": "text", "text": '{"results": []}'}]}

        with tempfile.TemporaryDirectory() as temp_dir:
            save_mcp_server_config(
                base_dir=temp_dir,
                profile_user_id="master",
                server_id="anysearch",
                payload={
                    "enabled": True,
                    "displayName": "AnySearch",
                    "command": "fake-anysearch",
                    "args": [],
                    "cwd": temp_dir,
                },
            )
            handler = WebSearchToolHandler(config_base_dir=temp_dir, mcp_tool_caller=FakeCaller())

            pending = handler.capability_status(profile_user_id="master", client_mode="desktop_pet")
            self.assertTrue(completed.wait(timeout=1.0))
            ready = pending
            for _ in range(100):
                ready = handler.capability_status(profile_user_id="master", client_mode="desktop_pet")
                if ready["status"] == "ready":
                    break
                time.sleep(0.01)

            self.assertTrue(pending["enabled"])
            self.assertEqual(pending["status"], "checking")
            self.assertTrue(ready["enabled"])

    def test_execute_keeps_mcp_ready_after_one_tool_result_error(self) -> None:
        class ErrorCaller:
            def __init__(self) -> None:
                self.calls: list[str] = []

            async def __call__(self, *, server: dict, tool_name: str, arguments: dict) -> dict:
                self.calls.append(tool_name)
                if tool_name == "extract":
                    return {
                        "isError": True,
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "extract_fetch_failed\n"
                                    "fetch failed: unable to retrieve the page\n"
                                    "Authorization: Bearer provider-secret"
                                ),
                            }
                        ],
                    }
                return {"content": [{"type": "text", "text": "search result"}]}

        with tempfile.TemporaryDirectory() as temp_dir:
            save_mcp_server_config(
                base_dir=temp_dir,
                profile_user_id="master",
                server_id="anysearch",
                payload={
                    "enabled": True,
                    "displayName": "AnySearch",
                    "command": "fake-anysearch",
                    "args": [],
                    "cwd": temp_dir,
                },
            )
            caller = ErrorCaller()
            handler = WebSearchToolHandler(config_base_dir=temp_dir, mcp_tool_caller=caller)
            extract_call = handler.normalize_call(
                {"type": "web_search", "action": "extract", "url": "https://example.com/page"}
            )
            search_call = handler.normalize_call({"type": "web_search", "query": "今天新闻"})
            assert extract_call is not None
            assert search_call is not None

            result = handler.execute(call=extract_call, context=self._context())
            status = handler.capability_status(profile_user_id="master", client_mode="desktop_pet")
            search_result = handler.execute(call=search_call, context=self._context())

            self.assertEqual(result.state_updates["web_search_status"], "failed")
            self.assertEqual(result.state_updates["web_search_reason"], "extract_fetch_failed")
            self.assertIn("fetch failed: unable to retrieve the page", result.followup_context)
            self.assertIn("不代表整个搜索服务不可用", result.followup_context)
            self.assertNotIn("provider-secret", result.followup_context)
            self.assertIn("Authorization: Bearer [redacted]", result.followup_context)
            self.assertTrue(status["enabled"])
            self.assertEqual(search_result.state_updates["web_search_status"], "ok")
            self.assertEqual(caller.calls, ["extract", "search"])

    def test_prompt_instruction_searches_current_public_facts_without_explicit_search_word(self) -> None:
        handler = WebSearchToolHandler(config_base_dir="unused", mcp_tool_caller=object())

        instruction = handler.build_prompt_instruction()

        self.assertIn("高变化事实", instruction)
        self.assertIn("不需要用户显式说", instruction)
        self.assertIn("batch_search", instruction)
        self.assertIn("时间范围", instruction)
        self.assertIn("日经指数现在多少", instruction)
        self.assertIn("七月新番有哪些", instruction)
        self.assertIn("稳定常识", instruction)
        self.assertNotIn("不确定是否需要实时信息，先自然询问", instruction)

    def test_time_range_search_followup_requests_broader_coverage(self) -> None:
        handler = WebSearchToolHandler(config_base_dir="unused", mcp_tool_caller=object())

        followup = handler._format_search_followup(
            action="search",
            call={"query": "最近一周和日经225有关的新闻"},
            payload=[
                {
                    "title": "单日行情",
                    "url": "https://example.com/one-day",
                    "snippet": "只覆盖 2026-07-10。",
                }
            ],
            redaction_terms=[],
        )

        self.assertIn("当前任务尚未完成", followup)
        self.assertIn("batch_search", followup)
        self.assertIn("extract", followup)

    def test_transient_failure_invites_alternate_read_only_research(self) -> None:
        handler = WebSearchToolHandler(config_base_dir="unused", mcp_tool_caller=object())

        result = handler._failure("mcp_tool_call_timeout", "AnySearch MCP 调用超时。")

        self.assertIn("换查询词", result.followup_context)
        self.assertIn("拆小批次", result.followup_context)
        self.assertIn("其它检索工具", result.followup_context)
        self.assertNotIn("所有公开信息渠道都不可用", result.followup_context.split("不要把", 1)[0])

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
                    "url": "file:///C:/Users/ExampleUser/private.txt",
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
                                            "published_at": "2026-07-10T15:00:00+09:00",
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
            self.assertIn("当前消息时间和本次检索时间", result.followup_context)
            self.assertIn("搜索摘要不是规范化行情快照", result.followup_context)
            self.assertIn("2026-07-10T15:00:00+09:00", result.followup_context)
            self.assertNotIn("dotenv-secret", result.followup_context)
            self.assertNotIn("Authorization: Bearer dotenv-secret", result.followup_context)
            self.assertNotIn(temp_dir, result.followup_context)

    def test_execute_returns_structured_unavailable_when_direct_anysearch_fails(self) -> None:
        class FailingRestClient:
            endpoint = "https://api.anysearch.test/v1/search"

            def call(self, *, action: str, arguments: dict) -> dict:
                raise AnySearchRestError("anysearch_network_unavailable", retryable=True)

        with tempfile.TemporaryDirectory() as temp_dir:
            handler = WebSearchToolHandler(
                config_base_dir=temp_dir,
                mcp_tool_caller=object(),
                anysearch_rest_client=FailingRestClient(),
            )
            call = handler.normalize_call({"type": "web_search", "query": "今天的新闻"})
            self.assertIsNotNone(call)
            assert call is not None

            result = handler.execute(call=call, context=self._context())

            self.assertEqual(result.state_updates["web_search_status"], "unavailable")
            self.assertIn("AnySearch 联网能力暂时不可用", result.followup_context)
            self.assertIn("anysearch_network_unavailable", result.followup_context)

    def test_execute_uses_direct_anysearch_without_node_or_saved_config(self) -> None:
        class FakeRestClient:
            endpoint = "https://api.anysearch.test/v1/search"

            def __init__(self) -> None:
                self.calls = []

            def call(self, *, action: str, arguments: dict) -> dict:
                self.calls.append((action, arguments))
                return {
                    "results": [
                        {
                            "title": "国内公开搜索结果",
                            "url": "https://example.cn/news",
                            "snippet": "无需本地 Node 或 MCP 代理。",
                        }
                    ]
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            rest = FakeRestClient()
            handler = WebSearchToolHandler(
                config_base_dir=temp_dir,
                mcp_tool_caller=object(),
                anysearch_rest_client=rest,
            )
            call = handler.normalize_call({"type": "web_search", "query": "国内财经新闻", "max_results": 3})
            assert call is not None

            result = handler.execute(call=call, context=self._context())

            self.assertEqual(rest.calls, [("search", {"query": "国内财经新闻", "max_results": 3})])
            self.assertEqual(result.state_updates["web_search_status"], "ok")
            self.assertIn("国内公开搜索结果", result.followup_context)

    def test_execute_search_falls_back_to_rest_when_mcp_process_fails(self) -> None:
        class FailingCaller:
            def __init__(self) -> None:
                self.calls = 0

            async def __call__(self, *, server: dict, tool_name: str, arguments: dict) -> dict:
                self.calls += 1
                raise McpStdioDiscoveryError("mcp_stdout_closed")

        class FakeRestClient:
            endpoint = "https://api.anysearch.test/v1/search"

            def __init__(self) -> None:
                self.calls = []

            def call(self, *, action: str, arguments: dict) -> dict:
                self.calls.append((action, arguments))
                return {
                    "results": [
                        {
                            "title": "REST 降级结果",
                            "url": "https://example.com/fallback",
                            "snippet": "MCP 进程失败后仍可完成只读搜索。",
                        }
                    ]
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            save_mcp_server_config(
                base_dir=temp_dir,
                profile_user_id="master",
                server_id="anysearch",
                payload={
                    "enabled": True,
                    "displayName": "AnySearch",
                    "command": "broken-anysearch",
                    "args": [],
                    "cwd": temp_dir,
                },
            )
            rest = FakeRestClient()
            caller = FailingCaller()
            handler = WebSearchToolHandler(
                config_base_dir=temp_dir,
                mcp_tool_caller=caller,
                anysearch_rest_client=rest,
                readiness_probe_in_background=False,
            )
            call = handler.normalize_call({"type": "web_search", "query": "今天新闻", "max_results": 2})
            assert call is not None

            readiness = handler.capability_status(profile_user_id="master", client_mode="desktop_pet")
            result = handler.execute(call=call, context=self._context())
            status = handler.capability_status(profile_user_id="master", client_mode="desktop_pet")

            self.assertEqual(readiness["transport"], "rest")
            self.assertEqual(result.state_updates["web_search_status"], "ok")
            self.assertIn("REST 降级结果", result.followup_context)
            self.assertEqual(caller.calls, 1)
            self.assertEqual(
                rest.calls,
                [
                    ("search", {"query": "OpenAI", "max_results": 1}),
                    ("search", {"query": "今天新闻", "max_results": 2}),
                ],
            )
            self.assertEqual(status["transport"], "rest")
            self.assertEqual(status["fallback_from"], "mcp")

    def test_qq_search_uses_owner_capability_profile_by_default(self) -> None:
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
                                            "title": "公开搜索结果",
                                            "url": "https://example.com/news",
                                            "snippet": "QQ 群聊也可以使用 owner 配置的搜索能力。",
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ]
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            saved = save_mcp_server_config(
                base_dir=temp_dir,
                profile_user_id="master",
                server_id="anysearch",
                payload={
                    "enabled": True,
                    "displayName": "AnySearch",
                    "command": "fake-anysearch",
                    "args": [],
                    "cwd": temp_dir,
                },
            )
            self.assertTrue(saved["ok"])

            caller = FakeCaller()
            handler = WebSearchToolHandler(config_base_dir=temp_dir, mcp_tool_caller=caller)
            call = handler.normalize_call({"type": "web_search", "query": "今天新闻", "max_results": 3})
            self.assertIsNotNone(call)
            assert call is not None
            context = ToolExecutionContext(
                profile_user_id="qq_group_shared_123456",
                session_id="qq_group_shared_123456",
                now_ts=1712400000,
                visual_payload={},
                client_mode="qq_text",
            )

            with (
                patch("companion_v01.tool_runtime.config.QQ_WEB_SEARCH_PROFILE_USER_ID", ""),
                patch(
                    "companion_v01.tool_runtime.config.WEB_OWNER_PROFILE_USER_ID",
                    "master",
                ),
            ):
                result = handler.execute(call=call, context=context)

            self.assertEqual(caller.calls[0]["tool_name"], "search")
            self.assertEqual(caller.calls[0]["arguments"], {"query": "今天新闻", "max_results": 3})
            self.assertEqual(result.state_updates["web_search_status"], "ok")
            self.assertEqual(result.state_updates["web_search_profile_user_id"], "master")
            self.assertIn("公开搜索结果", result.followup_context)


class OpenBrowserToolHandlerTests(unittest.TestCase):
    def _context(self) -> ToolExecutionContext:
        return ToolExecutionContext(
            profile_user_id="master",
            session_id="desktop",
            now_ts=1712400000,
            visual_payload={},
            client_mode="desktop_pet",
        )

    def test_prompt_instruction_distinguishes_user_visible_open_from_page_read(self) -> None:
        handler = OpenBrowserToolHandler()
        instruction = handler.build_prompt_instruction()

        self.assertEqual(handler.tool_spec().capability_id, "open_browser")
        self.assertIn("用户明确要求", instruction)
        self.assertIn('"required": ["url"]', instruction)
        self.assertIn("不读取页面", instruction)

    def test_browser_tools_expose_browser_control_metadata(self) -> None:
        open_metadata = OpenBrowserToolHandler().tool_metadata()
        page_metadata = BrowserPageToolHandler().tool_metadata()

        self.assertEqual(open_metadata.family, "browser_control")
        self.assertEqual(page_metadata.family, "browser_control")
        self.assertEqual(page_metadata.operation, "mixed")
        self.assertGreater(page_metadata.default_round_budget, open_metadata.default_round_budget)

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
            "file:///C:/Users/ExampleUser/private.txt",
            "https://user:pass@example.com/private",
            "https://example.com/a b",
        ):
            self.assertIsNone(handler.normalize_call({"type": "open_browser", "url": url}), url)

    def test_execute_requires_executor_broker_instead_of_fire_and_forget(self) -> None:
        handler = OpenBrowserToolHandler()
        call = handler.normalize_call({"type": "open_browser", "url": "https://example.com", "label": "Example"})
        self.assertIsNotNone(call)
        assert call is not None

        with self.assertRaisesRegex(RuntimeError, "open_browser_requires_executor_broker"):
            handler.execute(call=call, context=self._context())


class OpenMusicSearchToolHandlerTests(unittest.TestCase):
    def _context(self) -> ToolExecutionContext:
        return ToolExecutionContext(
            profile_user_id="master",
            session_id="desktop",
            now_ts=1712400000,
            visual_payload={},
            client_mode="desktop_pet",
        )

    def test_prompt_instruction_frames_search_as_not_playback(self) -> None:
        instruction = OpenMusicSearchToolHandler().build_prompt_instruction()

        self.assertIn("点歌", instruction)
        self.assertIn("公开音乐平台搜索页", instruction)
        self.assertIn("不会自动点击播放", instruction)
        self.assertIn("不要声称歌曲已经开始播放", instruction)

    def test_normalize_call_builds_safe_song_search_request(self) -> None:
        handler = OpenMusicSearchToolHandler()

        call = handler.normalize_call(
            {
                "type": "open_music_search",
                "title": "晴天",
                "artist": "周杰伦",
                "platform": "网易云",
            }
        )

        self.assertIsNotNone(call)
        assert call is not None
        self.assertEqual(call["title"], "晴天")
        self.assertEqual(call["artist"], "周杰伦")
        self.assertEqual(call["platform"], "netease_music")
        self.assertEqual(call["query"], "晴天 周杰伦")

    def test_normalize_call_defaults_platform_and_rejects_secret_or_url_query(self) -> None:
        handler = OpenMusicSearchToolHandler()

        call = handler.normalize_call({"type": "open_music_search", "query": "夜に駆ける"})
        self.assertIsNotNone(call)
        assert call is not None
        self.assertEqual(call["platform"], "qq_music")

        self.assertIsNone(handler.normalize_call({"type": "open_music_search", "query": "https://example.com/song"}))
        self.assertIsNone(handler.normalize_call({"type": "open_music_search", "query": "token=secret"}))

    def test_execute_emits_browser_open_request_without_claiming_playback(self) -> None:
        handler = OpenMusicSearchToolHandler()
        call = handler.normalize_call(
            {
                "type": "open_music_search",
                "title": "晴天",
                "artist": "周杰伦",
                "platform": "qq_music",
            }
        )
        self.assertIsNotNone(call)
        assert call is not None

        result = handler.execute(call=call, context=self._context())

        self.assertEqual(result.tool_type, "open_music_search")
        self.assertEqual(result.stream_events[0]["type"], "browser_open_requested")
        self.assertIn("y.qq.com", result.stream_events[0]["url"])
        self.assertFalse(result.stream_events[0]["requires_confirmation"])
        self.assertEqual(result.state_updates["music_request_status"], "opened_search")
        self.assertEqual(result.state_updates["music_request_platform"], "qq_music")
        self.assertIn("不代表歌曲已经开始播放", result.followup_context)


class BrowserPageToolHandlerTests(unittest.TestCase):
    def _context(self) -> ToolExecutionContext:
        return ToolExecutionContext(
            profile_user_id="master",
            session_id="desktop",
            now_ts=1712400000,
            visual_payload={},
            client_mode="desktop_pet",
        )

    def test_prompt_instruction_distinguishes_managed_read_from_user_visible_open(self) -> None:
        instruction = BrowserPageToolHandler(browser_runner=object()).build_prompt_instruction()

        self.assertIn("可见托管浏览器窗口", instruction)
        self.assertIn("不会接管用户手动打开的 Edge/Chrome 标签页", instruction)
        self.assertIn('"open_for_user":true', instruction)
        self.assertIn("使用 open_browser", instruction)
        self.assertIn("自己读取、总结、核对页面正文", instruction)
        self.assertIn("才使用 browser_page", instruction)
        self.assertIn("snapshot", instruction)
        self.assertIn("ref", instruction)
        self.assertIn("candidate_index", instruction)
        self.assertIn("不要每完成一步就询问用户", instruction)
        self.assertIn("accessibility snapshot", instruction)
        self.assertIn("scroll", instruction)
        self.assertIn("elements", instruction)
        self.assertIn("不要声称已经点击或输入", instruction)

    def test_normalize_call_accepts_public_url_and_bounds_text_size(self) -> None:
        handler = BrowserPageToolHandler(browser_runner=object())

        call = handler.normalize_call(
            {
                "type": "browser_page",
                "action": "open",
                "url": "https://example.com/docs?x=1",
                "max_chars": 99999,
                "open_for_user": True,
            }
        )

        self.assertIsNotNone(call)
        assert call is not None
        self.assertEqual(call["action"], "navigate")
        self.assertEqual(call["url"], "https://example.com/docs?x=1")
        self.assertEqual(call["max_chars"], 5000)
        self.assertTrue(call["open_for_user"])

        read_current = handler.normalize_call({"type": "browser_page", "action": "read", "max_chars": 12})
        self.assertIsNotNone(read_current)
        assert read_current is not None
        self.assertEqual(read_current["action"], "read_text")
        self.assertEqual(read_current["url"], "")
        self.assertEqual(read_current["max_chars"], 500)

        elements = handler.normalize_call({"type": "browser_page", "action": "inspect_elements", "element_limit": 99})
        self.assertIsNotNone(elements)
        assert elements is not None
        self.assertEqual(elements["action"], "elements")
        self.assertEqual(elements["element_limit"], 40)

        scroll = handler.normalize_call({"type": "browser_page", "action": "scroll", "scroll_delta": -9999})
        self.assertIsNotNone(scroll)
        assert scroll is not None
        self.assertEqual(scroll["action"], "scroll")
        self.assertEqual(scroll["scroll_delta"], -2400)

        click = handler.normalize_call(
            {"type": "browser_page", "action": "click", "selector": "button:has-text('搜索')"}
        )
        self.assertIsNotNone(click)
        assert click is not None
        self.assertEqual(click["action"], "click")
        self.assertEqual(click["selector"], "button:has-text('搜索')")

        fill = handler.normalize_call(
            {"type": "browser_page", "action": "fill", "selector": "input[name='q']", "text": "Akane"}
        )
        self.assertIsNotNone(fill)
        assert fill is not None
        self.assertEqual(fill["action"], "fill")
        self.assertEqual(fill["text"], "Akane")

        press = handler.normalize_call(
            {"type": "browser_page", "action": "press", "selector": "input[name='q']", "key": "return"}
        )
        self.assertIsNotNone(press)
        assert press is not None
        self.assertEqual(press["key"], "Enter")

        snapshot = handler.normalize_call({"type": "browser_page", "action": "observe", "max_chars": 2000})
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot["action"], "snapshot")

        ref_click = handler.normalize_call({"type": "browser_page", "action": "click", "ref": "[ref=e12]"})
        self.assertIsNotNone(ref_click)
        assert ref_click is not None
        self.assertEqual(ref_click["ref"], "e12")

        candidate_click = handler.normalize_call({"type": "browser_page", "action": "click", "candidate_index": "2"})
        self.assertIsNotNone(candidate_click)
        assert candidate_click is not None
        self.assertEqual(candidate_click["candidate_index"], 2)
        self.assertEqual(candidate_click["selector"], "")
        self.assertEqual(candidate_click["ref"], "")

        self.assertIsNone(handler.normalize_call({"type": "browser_page", "action": "click", "selector": "#password"}))
        self.assertIsNone(
            handler.normalize_call(
                {"type": "browser_page", "action": "fill", "selector": "input[name='q']", "text": "token=secret"}
            )
        )
        self.assertIsNone(handler.normalize_call({"type": "browser_page", "action": "press", "key": "Control+L"}))
        self.assertIsNone(handler.normalize_call({"type": "browser_page", "action": "click", "ref": "bad-ref"}))

    def test_normalize_call_rejects_private_and_secret_bearing_targets(self) -> None:
        handler = BrowserPageToolHandler(browser_runner=object())

        for url in (
            "http://127.0.0.1:9999/health",
            "http://localhost/admin",
            "http://10.0.0.4/admin",
            "file:///C:/Users/ExampleUser/private.txt",
            "https://user:pass@example.com/private",
            "https://example.com/a b",
            "https://example.com/?token=secret-value",
        ):
            self.assertIsNone(
                handler.normalize_call({"type": "browser_page", "action": "navigate", "url": url}),
                url,
            )

    def test_execute_returns_bounded_page_excerpt_without_streaming_body_text(self) -> None:
        class FakeRunner:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def run(self, *, action: str, url: str = "", max_chars: int = 3000) -> BrowserPageResult:
                self.calls.append({"action": action, "url": url, "max_chars": max_chars})
                return BrowserPageResult(
                    ok=True,
                    status="available",
                    action=action,
                    url="https://example.com/article?password=should-redact",
                    title="Example Article",
                    text="正文第一段\nAuthorization: Bearer should-redact\napi_key=should-redact",
                )

        runner = FakeRunner()
        handler = BrowserPageToolHandler(browser_runner=runner)
        call = handler.normalize_call({"type": "browser_page", "url": "https://example.com/article", "max_chars": 1000})
        self.assertIsNotNone(call)
        assert call is not None

        result = handler.execute(call=call, context=self._context())

        self.assertEqual(
            runner.calls[0], {"action": "navigate", "url": "https://example.com/article", "max_chars": 1000}
        )
        self.assertEqual(result.tool_type, "browser_page")
        self.assertEqual(result.stream_events[0]["type"], "browser_page_read")
        self.assertEqual(result.stream_events[0]["url"], "https://example.com/article?password=[redacted]")
        self.assertNotIn("正文第一段", json.dumps(result.stream_events, ensure_ascii=False))
        self.assertIn("Example Article", result.followup_context)
        self.assertIn("正文第一段", result.followup_context)
        self.assertIn("页面状态快照：", result.followup_context)
        self.assertIn("Akane 托管浏览器窗口", result.followup_context)
        self.assertIn("不是用户手动打开的系统浏览器标签页", result.followup_context)
        self.assertIn("下一步提示", result.followup_context)
        self.assertIn("browser_page scroll", result.followup_context)
        self.assertNotIn("should-redact", result.followup_context)
        self.assertEqual(result.state_updates["browser_page_status"], "available")
        self.assertIn("browser_page scroll", result.state_updates["browser_page_next_hint"])
        self.assertFalse(result.state_updates["browser_open_requested"])

    def test_execute_can_request_user_visible_open_while_reading_managed_page(self) -> None:
        class FakeRunner:
            def run(self, *, action: str, url: str = "", max_chars: int = 3000) -> BrowserPageResult:
                return BrowserPageResult(
                    ok=True,
                    status="available",
                    action=action,
                    url="https://example.com/article",
                    title="Example Article",
                    text="正文第一段",
                )

        handler = BrowserPageToolHandler(browser_runner=FakeRunner())
        call = handler.normalize_call(
            {
                "type": "browser_page",
                "action": "navigate",
                "url": "https://example.com/article",
                "open_for_user": True,
            }
        )
        self.assertIsNotNone(call)
        assert call is not None

        result = handler.execute(call=call, context=self._context())

        self.assertEqual(result.stream_events[0]["type"], "browser_open_requested")
        self.assertEqual(result.stream_events[0]["url"], "https://example.com/article")
        self.assertEqual(result.stream_events[1]["type"], "browser_page_read")
        self.assertIn("已请求桌宠把该公开网页交给系统浏览器打开给用户看", result.followup_context)
        self.assertTrue(result.state_updates["browser_open_requested"])

    def test_execute_scroll_reads_page_after_bounded_scroll_without_clicking(self) -> None:
        class FakeRunner:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def run(self, **kwargs) -> BrowserPageResult:
                self.calls.append(dict(kwargs))
                return BrowserPageResult(
                    ok=True,
                    status="available",
                    action=str(kwargs.get("action") or ""),
                    url="https://example.com/long",
                    title="Long Page",
                    text="滚动后的正文",
                )

        runner = FakeRunner()
        handler = BrowserPageToolHandler(browser_runner=runner)
        call = handler.normalize_call({"type": "browser_page", "action": "scroll", "scroll_delta": 1200})
        self.assertIsNotNone(call)
        assert call is not None

        result = handler.execute(call=call, context=self._context())

        self.assertEqual(runner.calls[0]["action"], "scroll")
        self.assertEqual(runner.calls[0]["scroll_delta"], 1200)
        self.assertEqual(result.stream_events[0]["type"], "browser_page_read")
        self.assertEqual(result.stream_events[0]["scroll_delta"], 1200)
        self.assertIn("滚动后的正文", result.followup_context)
        self.assertNotIn("点击", result.followup_context.split("页面状态快照：", 1)[0])

    def test_execute_elements_returns_visible_candidate_summary_only(self) -> None:
        class FakeRunner:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def run(self, **kwargs) -> BrowserPageResult:
                self.calls.append(dict(kwargs))
                return BrowserPageResult(
                    ok=True,
                    status="available",
                    action=str(kwargs.get("action") or ""),
                    url="https://example.com/form",
                    title="Form Page",
                    text="1. link: 文档 (https://example.com/docs)\n2. button: 搜索\n3. input: Search",
                )

        runner = FakeRunner()
        handler = BrowserPageToolHandler(browser_runner=runner)
        call = handler.normalize_call({"type": "browser_page", "action": "elements", "element_limit": 3})
        self.assertIsNotNone(call)
        assert call is not None

        result = handler.execute(call=call, context=self._context())

        self.assertEqual(runner.calls[0]["action"], "elements")
        self.assertEqual(runner.calls[0]["element_limit"], 3)
        self.assertIn("元素摘要：", result.followup_context)
        self.assertIn("不表示已经点击或输入", result.followup_context)
        self.assertIn("ref 或 candidate_index", result.followup_context)
        self.assertEqual(result.stream_events[0]["element_count"], 3)
        self.assertEqual(result.state_updates["browser_page_element_count"], 3)
        self.assertIn("candidate_index", result.state_updates["browser_page_next_hint"])

    def test_execute_control_action_requires_approval_by_default(self) -> None:
        class FakeRunner:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def run(self, **kwargs) -> BrowserPageResult:
                self.calls.append(dict(kwargs))
                return BrowserPageResult(ok=True, status="executed", action=str(kwargs.get("action") or ""))

        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            handler = BrowserPageToolHandler(browser_runner=runner, config_base_dir=temp_dir)
            call = handler.normalize_call(
                {"type": "browser_page", "action": "click", "selector": "button:has-text('搜索')"}
            )
            self.assertIsNotNone(call)
            assert call is not None

            result = handler.execute(call=call, context=self._context())

        self.assertEqual(runner.calls, [])
        self.assertEqual(result.stream_events[0]["type"], "capability_approval_required")
        self.assertEqual(result.stream_events[0]["capabilityId"], "tool.browser_page")
        self.assertEqual(result.stream_events[0]["actionId"], "browser_page.click")
        self.assertEqual(result.stream_events[0]["risk"], "high")
        self.assertEqual(result.state_updates["browser_control_status"], "approval_required")
        self.assertIn("不要声称已经点击、输入或按键", result.followup_context)

    def test_execute_control_action_runs_when_profile_policy_is_trusted_auto_allow(self) -> None:
        class FakeRunner:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def run(self, **kwargs) -> BrowserPageResult:
                self.calls.append(dict(kwargs))
                return BrowserPageResult(
                    ok=True,
                    status="executed",
                    action=str(kwargs.get("action") or ""),
                    url="https://example.com/search",
                    title="Search",
                    text="搜索结果",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            saved = save_approval_policy_config(
                base_dir=temp_dir,
                profile_user_id="master",
                payload={"defaultMode": "trusted_auto_allow"},
            )
            self.assertTrue(saved["ok"])
            runner = FakeRunner()
            handler = BrowserPageToolHandler(browser_runner=runner, config_base_dir=temp_dir)
            call = handler.normalize_call({"type": "browser_page", "action": "fill", "ref": "e4", "text": "Akane"})
            self.assertIsNotNone(call)
            assert call is not None

            result = handler.execute(call=call, context=self._context())

            self.assertEqual(runner.calls[0]["action"], "fill")
            self.assertEqual(runner.calls[0]["ref"], "e4")
            self.assertEqual(runner.calls[0]["text"], "Akane")
            self.assertEqual(result.stream_events[0]["status"], "executed")
            self.assertEqual(result.state_updates["browser_control_status"], "executed")
            self.assertIn("高风险浏览器控制动作已在授权边界内执行", result.followup_context)

    def test_execute_click_can_use_visible_candidate_index(self) -> None:
        class FakeRunner:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def run(self, **kwargs) -> BrowserPageResult:
                self.calls.append(dict(kwargs))
                return BrowserPageResult(
                    ok=True,
                    status="executed",
                    action=str(kwargs.get("action") or ""),
                    url="https://www.bilibili.com/video/BV1test",
                    title="候选视频",
                    text="视频页状态",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            saved = save_approval_policy_config(
                base_dir=temp_dir,
                profile_user_id="master",
                payload={"defaultMode": "trusted_auto_allow"},
            )
            self.assertTrue(saved["ok"])
            runner = FakeRunner()
            handler = BrowserPageToolHandler(browser_runner=runner, config_base_dir=temp_dir)
            call = handler.normalize_call({"type": "browser_page", "action": "click", "candidate_index": 1})
            self.assertIsNotNone(call)
            assert call is not None

            result = handler.execute(call=call, context=self._context())

            self.assertEqual(runner.calls[0]["action"], "click")
            self.assertEqual(runner.calls[0]["candidate_index"], 1)
            self.assertEqual(runner.calls[0]["selector"], "")
            self.assertEqual(runner.calls[0]["ref"], "")
            self.assertEqual(result.state_updates["browser_control_status"], "executed")

    def test_execute_control_action_can_use_injected_approval_checker(self) -> None:
        class FakeRunner:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def run(self, **kwargs) -> BrowserPageResult:
                self.calls.append(dict(kwargs))
                return BrowserPageResult(ok=True, status="executed", action=str(kwargs.get("action") or "press"))

        checker_calls: list[dict[str, object]] = []

        def checker(**kwargs) -> bool:
            checker_calls.append(dict(kwargs))
            return kwargs.get("action_id") == "browser_page.press"

        runner = FakeRunner()
        handler = BrowserPageToolHandler(browser_runner=runner, approval_checker=checker)
        call = handler.normalize_call(
            {"type": "browser_page", "action": "press", "selector": "input[name='q']", "key": "Enter"}
        )
        self.assertIsNotNone(call)
        assert call is not None

        result = handler.execute(call=call, context=self._context())

        self.assertEqual(checker_calls[0]["capability_id"], "tool.browser_page")
        self.assertEqual(checker_calls[0]["action_id"], "browser_page.press")
        self.assertEqual(runner.calls[0]["key"], "Enter")
        self.assertEqual(result.state_updates["browser_control_status"], "executed")

    def test_execute_returns_structured_unavailable_when_runner_is_missing(self) -> None:
        class MissingRunner:
            def capability_status(self) -> dict:
                return {"enabled": False, "status": "missing_executor", "reason": "playwright_not_installed"}

            def run(self, *, action: str, url: str = "", max_chars: int = 3000) -> BrowserPageResult:
                return BrowserPageResult(
                    ok=False,
                    status="unavailable",
                    action=action,
                    reason="playwright_not_installed",
                )

        handler = BrowserPageToolHandler(browser_runner=MissingRunner())
        self.assertEqual(handler.capability_status()["status"], "missing_executor")

        call = handler.normalize_call({"type": "browser_page", "action": "current"})
        self.assertIsNotNone(call)
        assert call is not None
        result = handler.execute(call=call, context=self._context())

        self.assertEqual(result.stream_events[0]["status"], "unavailable")
        self.assertIn("暂时不可用", result.followup_context)
        self.assertIn("不要编造页面结果", result.followup_context)
        self.assertEqual(result.state_updates["browser_page_status"], "unavailable")

    def test_managed_browser_runner_defaults_to_visible_desktop_window(self) -> None:
        runner = ManagedBrowserPageRunner()

        self.assertFalse(runner.headless)
        self.assertEqual(runner.browser_channel, "msedge" if sys.platform == "win32" else "")


if __name__ == "__main__":
    unittest.main()
