from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping


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
            raise McpStdioDiscoveryError(
                f"mcp_discovery_timeout{': ' + stderr_text if stderr_text else ''}"
            ) from exc
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


def _hydrate_env_placeholders(env: dict[str, str], *, args: list[str], cwd: str | None = None) -> None:
    wanted = {
        match.group(1)
        for arg in args
        for match in _ENV_PLACEHOLDER_RE.finditer(str(arg or ""))
    }
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
