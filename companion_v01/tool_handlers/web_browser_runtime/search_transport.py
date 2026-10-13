"""MCP/REST transport, awaitable bridging and fallback."""

from __future__ import annotations

import asyncio
import inspect
import re
import threading
from typing import Any, Mapping

from ...anysearch_rest_client import AnySearchRestError
from ...mcp_stdio_discoverer import McpStdioDiscoveryError
from ..core import ToolExecutionResult


def _mcp_server_has_transport_config(server: Mapping[str, Any] | None) -> bool:
    if not isinstance(server, Mapping) or not bool(server.get("enabled")):
        return False
    transport = str(server.get("transport") or "stdio").strip().lower().replace("-", "_")
    if transport in {"http", "streamablehttp", "streamable_http"}:
        return bool(str(server.get("url") or "").strip())
    return transport == "stdio" and bool(str(server.get("command") or "").strip())


class SearchTransportMixin:
    async def _call_mcp(
        self,
        *,
        server: Mapping[str, Any],
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = self.mcp_tool_caller(server=server, tool_name=tool_name, arguments=arguments)
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, dict) else {}

    async def _call_readiness_mcp(
        self,
        *,
        server: Mapping[str, Any],
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = self.readiness_mcp_tool_caller(server=server, tool_name=tool_name, arguments=arguments)
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _mcp_result_is_error(result: Any) -> bool:
        return isinstance(result, Mapping) and bool(result.get("isError") or result.get("is_error"))

    @staticmethod
    def _safe_mcp_failure_reason(value: Any) -> str:
        reason = str(value or "").partition(":")[0].strip().lower()
        return reason if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason) else "mcp_call_failed"

    def _run_coro_blocking(self, coro: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        result_box: dict[str, Any] = {}
        error_box: dict[str, BaseException] = {}

        def runner() -> None:
            try:
                result_box["value"] = asyncio.run(coro)
            except BaseException as exc:
                error_box["error"] = exc

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        if "error" in error_box:
            raise error_box["error"]
        return result_box.get("value")

    def _fetch_web_result(
        self, *, server: Mapping[str, Any] | None, profile_user_id: str,
        action: str, arguments: Mapping[str, Any], redaction_terms: list[str],
    ) -> tuple[Any, list[str], bool] | ToolExecutionResult:
        use_mcp = _mcp_server_has_transport_config(server)
        providers_attempted: list[str] = []
        fallback_used = False
        mcp_failure_reason = ""
        result: Any = None
        if use_mcp and server:
            providers_attempted.append("mcp")
            try:
                result = self._run_coro_blocking(self._call_mcp(server=server, tool_name=action, arguments=arguments))
                if self._mcp_result_is_error(result):
                    payload = self._extract_payload(result, redaction_terms=redaction_terms)
                    detail = self._payload_to_text(payload, redaction_terms=redaction_terms)
                    mcp_failure_reason = self._mcp_tool_error_reason(result=result, detail=detail)
            except McpStdioDiscoveryError as exc:
                mcp_failure_reason = self._safe_mcp_failure_reason(exc)
            except Exception:
                mcp_failure_reason = "mcp_call_failed"
            if mcp_failure_reason:
                self._remember_server_failure(profile_user_id, server, reason=mcp_failure_reason)
                if action not in {"search", "batch_search"}:
                    return self._mcp_tool_failure(
                        action=action,
                        result=result if isinstance(result, Mapping) else {"reason": mcp_failure_reason},
                        redaction_terms=redaction_terms,
                        profile_user_id=profile_user_id,
                    )
                fallback_used = True
                providers_attempted.append("rest")
                try:
                    result = self.anysearch_rest_client.call(action=action, arguments=arguments)
                except AnySearchRestError as rest_exc:
                    return self._failure(
                        rest_exc.reason,
                        f"AnySearch MCP（{mcp_failure_reason}）与官方 HTTPS 搜索均不可用。",
                    )
                except Exception:
                    return self._failure(
                        "anysearch_rest_failed",
                        f"AnySearch MCP（{mcp_failure_reason}）与官方 HTTPS 搜索均不可用。",
                    )
                use_mcp = False
                self._remember_server_rest_fallback(
                    profile_user_id,
                    server,
                    mcp_reason=mcp_failure_reason,
                )
        else:
            providers_attempted.append("rest")
            try:
                result = self.anysearch_rest_client.call(action=action, arguments=arguments)
            except AnySearchRestError as exc:
                return self._failure(exc.reason, "AnySearch 官方 HTTPS 搜索调用失败。")
            except Exception:
                return self._failure("anysearch_rest_failed", "AnySearch 官方 HTTPS 搜索调用失败。")
        if use_mcp and server:
            self._remember_server_ready(profile_user_id, server)

        return result, providers_attempted, fallback_used
