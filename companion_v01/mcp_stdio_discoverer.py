from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Mapping


class McpStdioDiscoveryError(RuntimeError):
    """Raised when an MCP stdio server cannot provide a tools/list response."""


class McpStdioToolDiscoverer:
    """Minimal MCP stdio client for discovery only.

    This client performs initialize + tools/list and never calls tools. It uses
    newline-delimited JSON-RPC over stdio, matching the common MCP stdio
    transport used by local servers.
    """

    def __init__(self, *, timeout_seconds: float = 8.0, max_pages: int = 4, max_messages: int = 80) -> None:
        self.timeout_seconds = max(1.0, float(timeout_seconds or 8.0))
        self.max_pages = max(1, int(max_pages or 4))
        self.max_messages = max(8, int(max_messages or 80))

    async def __call__(self, *, server: Mapping[str, Any]) -> dict[str, Any]:
        if str(server.get("transport") or "stdio").strip() != "stdio":
            raise McpStdioDiscoveryError("unsupported_transport")
        command = str(server.get("command") or "").strip()
        if not command:
            raise McpStdioDiscoveryError("missing_command")
        args = [str(item) for item in server.get("args") or [] if str(item or "").strip()]
        cwd = str(server.get("cwd") or "").strip() or None
        env = os.environ.copy()
        raw_env = server.get("env")
        if isinstance(raw_env, Mapping):
            env.update({str(key): str(value) for key, value in raw_env.items()})

        process = await asyncio.create_subprocess_exec(
            command,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=cwd,
            env=env,
        )
        try:
            return await asyncio.wait_for(self._discover(process), timeout=self.timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise McpStdioDiscoveryError("mcp_discovery_timeout") from exc
        finally:
            await self._stop_process(process)

    async def _discover(self, process: asyncio.subprocess.Process) -> dict[str, Any]:
        await self._send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "akane", "version": "0.1"},
                },
            },
        )
        initialize = await self._read_response(process, 1)
        if initialize.get("error"):
            raise McpStdioDiscoveryError("mcp_initialize_failed")

        await self._send(
            process,
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
        )

        tools: list[dict[str, Any]] = []
        cursor = ""
        for page in range(self.max_pages):
            request_id = page + 2
            params = {"cursor": cursor} if cursor else {}
            await self._send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/list",
                    "params": params,
                },
            )
            response = await self._read_response(process, request_id)
            if response.get("error"):
                raise McpStdioDiscoveryError("mcp_tools_list_failed")
            result = response.get("result") if isinstance(response.get("result"), Mapping) else {}
            page_tools = result.get("tools") if isinstance(result.get("tools"), list) else []
            tools.extend(item for item in page_tools if isinstance(item, Mapping))
            cursor = str(result.get("nextCursor") or result.get("next_cursor") or "").strip()
            if not cursor:
                break
        return {"tools": tools}

    async def _send(self, process: asyncio.subprocess.Process, message: Mapping[str, Any]) -> None:
        if process.stdin is None:
            raise McpStdioDiscoveryError("mcp_stdin_unavailable")
        data = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        process.stdin.write(data)
        await process.stdin.drain()

    async def _read_response(self, process: asyncio.subprocess.Process, response_id: int) -> dict[str, Any]:
        if process.stdout is None:
            raise McpStdioDiscoveryError("mcp_stdout_unavailable")
        for _ in range(self.max_messages):
            line = await process.stdout.readline()
            if not line:
                raise McpStdioDiscoveryError("mcp_stdout_closed")
            try:
                message = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            if message.get("id") == response_id:
                return message
        raise McpStdioDiscoveryError("mcp_response_not_found")

    async def _stop_process(self, process: asyncio.subprocess.Process) -> None:
        if process.stdin is not None:
            try:
                process.stdin.close()
            except Exception:
                pass
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=1.5)
            except Exception:
                if process.returncode is None:
                    process.kill()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=1.5)
                    except Exception:
                        pass
