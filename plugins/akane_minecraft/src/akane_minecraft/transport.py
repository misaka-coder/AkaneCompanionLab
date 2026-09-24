"""Bounded, authenticated MCP requests to an explicitly configured local game.

An effectful request is never retried after a timeout/disconnect. The server
may already have accepted it; a fresh status observation is needed instead.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx


MAX_RESPONSE_BYTES = 1024 * 1024


class MinecraftError(Exception):
    def __init__(self, reason: str, *, uncertain: bool = False):
        self.reason, self.uncertain = reason, uncertain
        super().__init__(reason)


@dataclass(frozen=True)
class GameConnection:
    endpoint: str
    token: str = field(repr=False)
    companion: str = "Reimu"
    timeout_seconds: float = 15
    session_minutes: int = 0

    @classmethod
    def parse(cls, values: dict):
        try:
            endpoint = str(values["endpoint"]).strip()
            parsed = urlsplit(endpoint)
            if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or parsed.username is not None or parsed.password is not None
                    or parsed.path != "/mcp" or parsed.query or parsed.fragment
                    or not parsed.port or any(c.isspace() for c in endpoint)):
                raise ValueError()
            token = values["token"]
            companion = values.get("companion", "Reimu")
            timeout = values.get("timeout_seconds", 15)
            minutes = values.get("session_minutes", 0)
            if (not isinstance(token, str) or not 8 <= len(token) <= 1024
                    or any(ord(c) < 32 or ord(c) == 127 for c in token)
                    or not isinstance(companion, str) or not 1 <= len(companion) <= 64
                    or type(timeout) not in {int, float} or not 5 <= timeout <= 60
                    or type(minutes) is not int or not 0 <= minutes <= 120):
                raise ValueError()
            return cls(endpoint, token, companion, float(timeout), minutes)
        except (ValueError, KeyError, TypeError):
            raise MinecraftError("minecraft_connection_invalid") from None

    @property
    def body_key(self):
        # Aliases for the same loopback endpoint must not create two controllers.
        return (urlsplit(self.endpoint).port, self.companion.casefold())


def public_value(value, connection: GameConnection):
    """Game data is untrusted and never a place to echo connection credentials."""
    if isinstance(value, str):
        return value.replace(connection.token, "[credential]").replace(connection.endpoint, "[game endpoint]")
    if isinstance(value, list):
        return [public_value(item, connection) for item in value]
    if isinstance(value, dict):
        return {public_value(str(key), connection): public_value(item, connection) for key, item in value.items()}
    return value


class NumenClient:
    def __init__(self, connection: GameConnection, *, transport=None):
        self.connection = connection
        self._client = httpx.AsyncClient(
            timeout=connection.timeout_seconds, follow_redirects=False, trust_env=False, transport=transport,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )
        self._sequence = 0
        self._session_id = ""
        self._protocol = ""
        self._ready = False
        self._closed = False
        self._init_lock = asyncio.Lock()

    async def _rpc(self, method: str, params: dict, *, effectful=False, notification=False):
        if self._closed:
            raise MinecraftError("minecraft_client_closed")
        self._sequence += 1
        request_id = self._sequence
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            payload["id"] = request_id
        headers = {"Authorization": "Bearer " + self.connection.token,
                   "Accept": "application/json, text/event-stream"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._protocol:
            headers["MCP-Protocol-Version"] = self._protocol
        try:
            async with self._client.stream("POST", self.connection.endpoint, headers=headers, json=payload) as response:
                if response.status_code in {401, 403}:
                    raise MinecraftError("minecraft_authentication_failed")
                if response.status_code != 200 and not (notification and response.status_code in {202, 204}):
                    raise MinecraftError("minecraft_http_error", uncertain=effectful)
                if method == "initialize":
                    self._session_id = response.headers.get("Mcp-Session-Id", "")
                if notification:
                    return {}
                raw = bytearray()
                is_sse = response.headers.get("Content-Type", "").split(";", 1)[0] == "text/event-stream"
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise MinecraftError("minecraft_response_too_large", uncertain=effectful)
                    if is_sse:
                        for frame in bytes(raw).replace(b"\r\n", b"\n").split(b"\n\n")[:-1]:
                            data = b"\n".join(line[5:].lstrip() for line in frame.split(b"\n") if line.startswith(b"data:"))
                            if not data:
                                continue
                            candidate = json.loads(data)
                            if isinstance(candidate, dict) and candidate.get("id") == request_id:
                                return self._result(candidate, request_id, effectful)
                if is_sse:
                    raise MinecraftError("minecraft_rpc_response_missing", uncertain=effectful)
                return self._result(json.loads(raw), request_id, effectful)
        except asyncio.CancelledError:
            raise
        except MinecraftError:
            raise
        except httpx.ConnectError:
            raise MinecraftError("minecraft_unreachable", uncertain=effectful) from None
        except httpx.TimeoutException:
            raise MinecraftError("minecraft_timeout", uncertain=effectful) from None
        except (httpx.HTTPError, ValueError, TypeError, UnicodeError):
            raise MinecraftError("minecraft_invalid_response", uncertain=effectful) from None

    @staticmethod
    def _result(value, request_id, effectful):
        if not isinstance(value, dict) or value.get("jsonrpc") != "2.0" or value.get("id") != request_id:
            raise MinecraftError("minecraft_rpc_response_invalid", uncertain=effectful)
        if "error" in value:
            raise MinecraftError("minecraft_rpc_error", uncertain=effectful)
        if not isinstance(value.get("result"), dict):
            raise MinecraftError("minecraft_rpc_response_invalid", uncertain=effectful)
        return value["result"]

    async def initialize(self):
        async with self._init_lock:
            if self._ready:
                return
            result = await self._rpc("initialize", {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "akane-minecraft", "version": "0.1.5"},
            })
            if result.get("serverInfo", {}).get("name") != "numen-mcp":
                raise MinecraftError("minecraft_wrong_mcp_server")
            self._protocol = str(result.get("protocolVersion") or "2024-11-05")
            await self._rpc("notifications/initialized", {}, notification=True)
            self._ready = True

    async def list_tools(self):
        await self.initialize()
        result = await self._rpc("tools/list", {})
        if not isinstance(result.get("tools"), list):
            raise MinecraftError("minecraft_tool_catalog_invalid")
        return result["tools"]

    async def call(self, name: str, arguments: dict, *, effectful=False):
        await self.initialize()
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments}, effectful=effectful)
        return public_value(result, self.connection)

    async def aclose(self):
        if not self._closed:
            self._closed = True
            await self._client.aclose()


def tool_value(result: dict):
    blocks = result.get("content")
    if not isinstance(blocks, list) or any(not isinstance(item, dict) for item in blocks):
        raise MinecraftError("minecraft_tool_result_invalid")
    if any(item.get("type") != "text" for item in blocks):
        raise MinecraftError("minecraft_tool_result_unsupported")
    text = "\n".join(str(item.get("text") or "") for item in blocks)
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        value = text
    error = bool(result.get("isError")) or isinstance(value, dict) and value.get("success") is False
    return value, error


def active_task(value):
    if not isinstance(value, dict) or value.get("success") is False:
        raise MinecraftError("minecraft_task_state_unknown")
    data = value.get("data", value)
    if not isinstance(data, dict):
        raise MinecraftError("minecraft_task_state_unknown")
    task_id = data.get("task_id")
    state = str(data.get("state") or data.get("status") or "").lower()
    if task_id and state not in {"completed", "done", "failed", "stopped", "cancelled"}:
        return str(task_id)
    if value.get("success") is True or state in {"idle", "completed", "done", "stopped", "cancelled"}:
        return ""
    raise MinecraftError("minecraft_task_state_unknown")
