from __future__ import annotations

import atexit
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Mapping

from capcore_adapter_mcp import (
    McpClientError,
    McpSdkPooledStreamableHttpClient,
    McpSdkStreamableHttpClient,
    McpStreamableHttpServerConfig,
    McpToolRecord,
)


class McpStdioDiscoveryError(RuntimeError):
    """Raised when an MCP stdio server cannot provide a tools/list response."""


_ENV_PLACEHOLDER_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]{0,79})\}")
_DOTENV_MAX_BYTES = 128 * 1024


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
        _hydrate_env_placeholders(env, args=args, cwd=cwd)
        args = _expand_env_placeholders(args, env)

        exe, prefix_args = _resolve_stdio_command(command)
        process = await asyncio.create_subprocess_exec(
            exe,
            *prefix_args,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
        try:
            return await asyncio.wait_for(self._discover(process), timeout=self.timeout_seconds)
        except asyncio.TimeoutError as exc:
            stderr_text = await _read_stderr(process)
            raise McpStdioDiscoveryError(f"mcp_discovery_timeout{': ' + stderr_text if stderr_text else ''}") from exc
        except McpStdioDiscoveryError as exc:
            stderr_text = await _read_stderr(process)
            if stderr_text and not str(exc).endswith(stderr_text):
                raise McpStdioDiscoveryError(f"{exc}: {stderr_text}") from exc
            raise
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
        await _stop_stdio_process(process)


class McpStdioToolCaller:
    """Minimal MCP stdio client for one bounded tools/call request."""

    def __init__(self, *, timeout_seconds: float = 20.0, max_messages: int = 120) -> None:
        self.timeout_seconds = max(2.0, float(timeout_seconds or 20.0))
        self.max_messages = max(8, int(max_messages or 120))

    async def __call__(
        self,
        *,
        server: Mapping[str, Any],
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if str(server.get("transport") or "stdio").strip() != "stdio":
            raise McpStdioDiscoveryError("unsupported_transport")
        command = str(server.get("command") or "").strip()
        if not command:
            raise McpStdioDiscoveryError("missing_command")
        tool_name = str(tool_name or "").strip()
        if not tool_name:
            raise McpStdioDiscoveryError("missing_tool_name")

        args = [str(item) for item in server.get("args") or [] if str(item or "").strip()]
        cwd = str(server.get("cwd") or "").strip() or None
        env = os.environ.copy()
        raw_env = server.get("env")
        if isinstance(raw_env, Mapping):
            env.update({str(key): str(value) for key, value in raw_env.items()})
        _hydrate_env_placeholders(env, args=args, cwd=cwd)
        args = _expand_env_placeholders(args, env)

        exe, prefix_args = _resolve_stdio_command(command)
        process = await asyncio.create_subprocess_exec(
            exe,
            *prefix_args,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
        try:
            return await asyncio.wait_for(
                self._call_tool(process, tool_name=tool_name, arguments=arguments or {}),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise McpStdioDiscoveryError("mcp_tool_call_timeout") from exc
        finally:
            await self._stop_process(process)

    async def _call_tool(
        self,
        process: asyncio.subprocess.Process,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
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
        await self._send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": dict(arguments or {}),
                },
            },
        )
        response = await self._read_response(process, 2)
        if response.get("error"):
            raise McpStdioDiscoveryError("mcp_tool_call_failed")
        result = response.get("result")
        return result if isinstance(result, dict) else {}

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
        await _stop_stdio_process(process)


class McpToolDiscoverer:
    """Transport-neutral MCP discovery facade.

    Stdio remains on the existing compatibility path. Streamable HTTP delegates
    protocol and lifecycle handling to capcore-adapter-mcp's official-SDK
    client, so Akane only owns config hydration and product-facing errors.
    """

    def __init__(self, *, timeout_seconds: float = 8.0, max_pages: int = 4, max_messages: int = 80) -> None:
        self.timeout_seconds = max(1.0, float(timeout_seconds or 8.0))
        self._stdio = McpStdioToolDiscoverer(
            timeout_seconds=self.timeout_seconds,
            max_pages=max_pages,
            max_messages=max_messages,
        )
        self._streamable_http = McpSdkStreamableHttpClient()

    async def __call__(self, *, server: Mapping[str, Any]) -> dict[str, Any]:
        transport = _normalized_transport(server.get("transport"))
        if transport == "stdio":
            return await self._stdio(server=server)
        if transport != "streamable_http":
            raise McpStdioDiscoveryError("unsupported_transport")
        config = _streamable_http_server_config(server, timeout_seconds=self.timeout_seconds)
        try:
            tools = await self._streamable_http.list_tools(config)
        except McpClientError as exc:
            raise McpStdioDiscoveryError(str(exc) or "mcp_tools_list_failed") from exc
        return {"tools": [_tool_record_mapping(tool) for tool in tools]}


class McpToolCaller:
    """Transport-neutral one-call MCP facade backed by package transports."""

    def __init__(self, *, timeout_seconds: float = 20.0, max_messages: int = 120) -> None:
        self.timeout_seconds = max(2.0, float(timeout_seconds or 20.0))
        self._stdio = McpStdioToolCaller(
            timeout_seconds=self.timeout_seconds,
            max_messages=max_messages,
        )
        self._streamable_http = McpSdkStreamableHttpClient()
        self._streamable_http_worker = _McpStreamableHttpWorker()

    async def __call__(
        self,
        *,
        server: Mapping[str, Any],
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        transport = _normalized_transport(server.get("transport"))
        if transport == "stdio":
            return await self._stdio(server=server, tool_name=tool_name, arguments=arguments)
        if transport != "streamable_http":
            raise McpStdioDiscoveryError("unsupported_transport")
        config = _streamable_http_server_config(server, timeout_seconds=self.timeout_seconds)
        try:
            if isinstance(self._streamable_http, McpSdkStreamableHttpClient):
                result = await self._streamable_http_worker.call_tool(
                    config,
                    tool_name,
                    dict(arguments or {}),
                )
            else:
                result = await self._streamable_http.call_tool(
                    config,
                    tool_name,
                    dict(arguments or {}),
                )
        except McpClientError as exc:
            raise McpStdioDiscoveryError(str(exc) or "mcp_tool_call_failed") from exc
        return dict(result)

    async def aclose(self) -> None:
        await self._streamable_http_worker.aclose()


class _McpStreamableHttpWorker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: McpSdkPooledStreamableHttpClient | None = None
        self._thread: threading.Thread | None = None
        self._closed = False
        atexit.register(self.close)

    async def call_tool(
        self,
        server: McpStreamableHttpServerConfig,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        self._ensure_started()
        if self._loop is None or self._client is None:
            raise McpClientError("mcp_http_worker_unavailable")
        future = asyncio.run_coroutine_threadsafe(
            self._client.call_tool(server, tool_name, arguments),
            self._loop,
        )
        return await asyncio.wrap_future(future)

    async def aclose(self) -> None:
        loop = self._loop
        client = self._client
        if loop is None or client is None or self._closed:
            return
        future = asyncio.run_coroutine_threadsafe(client.aclose(), loop)
        await asyncio.wrap_future(future)
        self._closed = True
        loop.call_soon_threadsafe(loop.stop)

    def close(self) -> None:
        loop = self._loop
        client = self._client
        if loop is None or client is None or self._closed:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(client.aclose(), loop)
            future.result(timeout=2.0)
        except Exception:
            pass
        self._closed = True
        loop.call_soon_threadsafe(loop.stop)

    def _ensure_started(self) -> None:
        if self._ready.is_set():
            return
        with self._lock:
            if self._ready.is_set():
                return
            if self._closed:
                raise McpClientError("mcp_http_worker_closed")
            self._thread = threading.Thread(
                target=self._run,
                name="akane-mcp-http",
                daemon=True,
            )
            self._thread.start()
        if not self._ready.wait(timeout=3.0):
            raise McpClientError("mcp_http_worker_start_timeout")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._client = McpSdkPooledStreamableHttpClient()
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()


def _normalized_transport(value: Any) -> str:
    text = str(value or "stdio").strip().lower().replace("-", "_")
    if text in {"http", "streamablehttp", "streamable_http"}:
        return "streamable_http"
    return text or "stdio"


def _streamable_http_server_config(
    server: Mapping[str, Any],
    *,
    timeout_seconds: float,
) -> McpStreamableHttpServerConfig:
    url = str(server.get("url") or server.get("endpoint") or "").strip()
    cwd = str(server.get("cwd") or "").strip() or None
    raw_headers = server.get("headers")
    header_values = [str(value or "") for value in raw_headers.values()] if isinstance(raw_headers, Mapping) else []
    env = os.environ.copy()
    _hydrate_env_placeholders(env, args=header_values, cwd=cwd)
    headers = (
        {
            str(key): _ENV_PLACEHOLDER_RE.sub(lambda match: str(env.get(match.group(1)) or match.group(0)), str(value))
            for key, value in raw_headers.items()
        }
        if isinstance(raw_headers, Mapping)
        else {}
    )
    return McpStreamableHttpServerConfig(
        server_id=str(server.get("serverId") or server.get("server_id") or server.get("id") or "").strip(),
        url=url,
        headers=headers,
        enabled=bool(server.get("enabled", True)),
        timeout_seconds=timeout_seconds,
        discovery_timeout_seconds=timeout_seconds,
    )


def _tool_record_mapping(tool: McpToolRecord) -> dict[str, Any]:
    raw = dict(tool.raw or {})
    raw["name"] = tool.name
    if tool.description:
        raw["description"] = tool.description
    raw["inputSchema"] = dict(tool.input_schema or {})
    if tool.output_schema:
        raw["outputSchema"] = dict(tool.output_schema)
    if tool.annotations:
        raw["annotations"] = dict(tool.annotations)
    return raw


def _hydrate_env_placeholders(env: dict[str, str], *, args: list[str], cwd: str | None = None) -> None:
    wanted = {match.group(1) for arg in args for match in _ENV_PLACEHOLDER_RE.finditer(str(arg or ""))}
    missing = {key for key in wanted if key not in env or env.get(key) == ""}
    if not missing:
        return

    dotenv_values: dict[str, str] = {}
    for env_path in _candidate_env_files(cwd):
        dotenv_values.update(_read_dotenv_values(env_path, missing - set(dotenv_values)))
        if missing.issubset(dotenv_values):
            break
    for key in missing:
        value = dotenv_values.get(key)
        if value:
            env[key] = value


def _expand_env_placeholders(args: list[str], env: Mapping[str, str]) -> list[str]:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = env.get(key)
        return str(value) if value else match.group(0)

    return [_ENV_PLACEHOLDER_RE.sub(replace, str(arg or "")) for arg in args]


def _resolve_stdio_command(command: str) -> tuple[str, list[str]]:
    """Return (executable, prefix_args) for the given command.

    Windows .cmd/.bat files must be passed directly to
    create_subprocess_exec. Wrapping them in cmd.exe /c breaks argument
    quoting when the script path and later arguments both contain spaces, which
    is common for npx plus Authorization headers.
    """
    text = str(command or "").strip()
    if not text:
        return text, []
    resolved = shutil.which(text) or text
    if sys.platform == "win32" and resolved.lower().endswith((".cmd", ".bat")):
        return resolved, []
    return resolved, []


async def _read_stderr(process: asyncio.subprocess.Process, *, max_bytes: int = 2048) -> str:
    """Read whatever stderr the process has already written, non-blocking."""
    if process.stderr is None:
        return ""
    try:
        raw = await asyncio.wait_for(process.stderr.read(max_bytes), timeout=0.5)
        return raw.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


async def _stop_stdio_process(process: asyncio.subprocess.Process) -> None:
    if process.stdin is not None:
        try:
            process.stdin.close()
            await process.stdin.wait_closed()
        except Exception:
            pass
    if process.returncode is None:
        await _terminate_stdio_process_tree(process)
    try:
        await asyncio.wait_for(process.communicate(), timeout=1.0)
        return
    except Exception:
        pass
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.communicate(), timeout=1.0)
    except Exception:
        pass
    finally:
        transport = getattr(process, "_transport", None)
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass
        await asyncio.sleep(0)


async def _terminate_stdio_process_tree(process: asyncio.subprocess.Process) -> None:
    if sys.platform != "win32":
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        return
    try:
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        await asyncio.wait_for(killer.communicate(), timeout=2.0)
    except Exception:
        try:
            process.terminate()
        except ProcessLookupError:
            pass


def _candidate_env_files(cwd: str | None) -> list[Path]:
    paths: list[Path] = []
    if cwd:
        paths.append(Path(cwd) / ".env")
    paths.append(Path.cwd() / ".env")
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _read_dotenv_values(path: Path, keys: set[str]) -> dict[str, str]:
    if not keys or not path.is_file():
        return {}
    try:
        if path.stat().st_size > _DOTENV_MAX_BYTES:
            return {}
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    values: dict[str, str] = {}
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        if key not in keys:
            continue
        values[key] = _clean_dotenv_value(value)
    return values


def _clean_dotenv_value(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1]
    return text.strip()
