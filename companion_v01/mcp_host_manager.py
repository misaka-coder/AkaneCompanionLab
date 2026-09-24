"""Akane Host ownership for long-lived MCP transport sessions and management."""

from __future__ import annotations

import asyncio
import atexit
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import replace
import hashlib
import json
import threading
import time
from uuid import uuid4
from typing import Any, Mapping

from capcore_adapter_mcp import (
    McpClientError,
    McpSdkPooledStdioClient,
    McpSdkPooledStreamableHttpClient,
    McpStdioServerConfig,
    McpStreamableHttpServerConfig,
    McpToolRecord,
)

from .local_capability_config import (
    APPROVAL_MODE_DISABLED,
    _apply_mcp_low_risk_allowlist,
    approval_mode_for_capability,
    clear_mcp_server_profile_override,
    get_mcp_server_runtime_config,
    list_mcp_server_configs,
    load_capability_config,
    migrate_legacy_profile_mcp_servers,
    normalize_mcp_tool_discovery_payload,
    normalize_mcp_server_config_payload,
    remove_mcp_server_config,
    save_mcp_server_config,
    save_mcp_server_discovery,
    save_mcp_server_profile_enabled,
)
from .mcp_stdio_discoverer import (
    _resolve_runtime_env_and_args,
    _resolve_stdio_command,
    _streamable_http_server_config,
    _tool_record_mapping,
)
from .mcp_diagnostics import build_mcp_failure_diagnostic, mcp_failure_reason


def _transport(value: Any) -> str:
    text = str(value or "stdio").strip().lower().replace("-", "_")
    return "streamable_http" if text in {"http", "streamablehttp", "streamable_http"} else "stdio"


def _config_fingerprint(config: Mapping[str, Any]) -> str:
    stable = {key: config.get(key) for key in ("transport", "command", "args", "cwd", "env", "url", "headers")}
    return hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


class McpHostManager:
    """Own pooled MCP clients on one stable Host event loop."""

    def __init__(self, *, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = max(2.0, float(timeout_seconds or 20.0))
        self._lock = threading.RLock()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stdio: McpSdkPooledStdioClient | None = None
        self._http: McpSdkPooledStreamableHttpClient | None = None
        self._closed = False
        self._statuses: dict[tuple[str, str], dict[str, Any]] = {}
        self._contract_boot = uuid4().hex
        self._contract_epochs: dict[tuple[str, str], int] = {}
        atexit.register(self.close)

    def contract_revision(self, *, profile_user_id: str, server_id: str) -> str:
        with self._lock:
            return f"{self._contract_boot}:{self._contract_epochs.get((profile_user_id, server_id), 0)}"

    def client(
        self,
        *,
        profile_user_id: str,
        server_id: str,
        server_config: Mapping[str, Any],
    ) -> "ManagedMcpClient":
        return ManagedMcpClient(
            manager=self,
            profile_user_id=profile_user_id,
            server_id=server_id,
            server_config=server_config,
        )

    def discover(
        self,
        *,
        profile_user_id: str,
        server_id: str,
        server_config: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._run(
            self._discover(
                profile_user_id=profile_user_id,
                server_id=server_id,
                server_config=server_config,
            )
        )

    async def adiscover(
        self,
        *,
        profile_user_id: str,
        server_id: str,
        server_config: Mapping[str, Any],
    ) -> dict[str, Any]:
        future = self._submit(
            self._discover(
                profile_user_id=profile_user_id,
                server_id=server_id,
                server_config=server_config,
            )
        )
        return await asyncio.wrap_future(future)

    async def alist_tools(
        self,
        *,
        profile_user_id: str,
        server_id: str,
        server_config: Mapping[str, Any],
    ) -> tuple[McpToolRecord, ...]:
        result = await self.adiscover(
            profile_user_id=profile_user_id,
            server_id=server_id,
            server_config=server_config,
        )
        return tuple(result.get("toolRecords") or ())

    async def acall_tool(
        self,
        *,
        profile_user_id: str,
        server_id: str,
        server_config: Mapping[str, Any],
        tool_name: str,
        arguments: Mapping[str, Any],
        expected_revision: str = "",
    ) -> Mapping[str, Any]:
        future = self._submit(
            self._call_tool(
                profile_user_id=profile_user_id,
                server_id=server_id,
                server_config=server_config,
                tool_name=tool_name,
                arguments=arguments,
                expected_revision=expected_revision,
            )
        )
        return await asyncio.wrap_future(future)

    def stop_server(
        self,
        *,
        profile_user_id: str,
        server_id: str,
        server_config: Mapping[str, Any],
        disabled: bool = False,
        mark_status: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            key = (profile_user_id, server_id)
            self._contract_epochs[key] = self._contract_epochs.get(key, 0) + 1
        status = "disabled" if disabled else "starting"
        if not self._ready.is_set():
            if mark_status:
                self._mark(profile_user_id, server_id, status=status, reason="")
            return {"ok": True, "status": status, "serverId": server_id}
        try:
            self._run(self._close_server(profile_user_id, server_id, server_config))
        except Exception:
            self._mark(profile_user_id, server_id, status="error", reason="mcp_server_stop_failed")
            return {"ok": False, "status": "error", "reason": "mcp_server_stop_failed"}
        if mark_status:
            self._mark(profile_user_id, server_id, status=status, reason="")
        return {"ok": True, "status": status, "serverId": server_id}

    def status(self, *, profile_user_id: str, server_id: str) -> dict[str, Any]:
        with self._lock:
            value = dict(self._statuses.get((str(profile_user_id), str(server_id))) or {})
        return value or {"status": "starting", "reason": "mcp_runtime_not_started"}

    def close(self, *, timeout: float = 15.0) -> bool:
        with self._lock:
            if self._closed:
                return True
            self._closed = True
            loop = self._loop
            stdio = self._stdio
            http = self._http
            thread = self._thread
        clean = True
        deadline = time.monotonic() + max(0.0, float(timeout or 0.0))

        def remaining() -> float:
            return max(0.0, deadline - time.monotonic())

        if loop is not None:
            for transport in (stdio, http):
                if transport is None:
                    continue
                if remaining() <= 0:
                    clean = False
                    continue
                try:
                    asyncio.run_coroutine_threadsafe(transport.aclose(), loop).result(timeout=remaining())
                except Exception:
                    clean = False
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None and thread.is_alive():
            thread.join(timeout=remaining())
        return clean and not (thread is not None and thread.is_alive())

    async def _discover(
        self,
        *,
        profile_user_id: str,
        server_id: str,
        server_config: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._mark(profile_user_id, server_id, status="starting", reason="")
        server = self._runtime_server(profile_user_id, server_id, server_config)
        try:
            if isinstance(server, McpStreamableHttpServerConfig):
                assert self._http is not None
                tools = await self._http.list_tools(server)
            else:
                assert self._stdio is not None
                tools = await self._stdio.list_tools(server)
        except Exception as exc:
            reason = mcp_failure_reason(exc, fallback="mcp_tools_list_failed")
            self._mark(profile_user_id, server_id, status="error", reason=reason)
            raise McpClientError(reason) from exc
        self._mark(
            profile_user_id,
            server_id,
            status="ready",
            reason="",
            fingerprint=_config_fingerprint(server_config),
            toolCount=len(tools),
        )
        return {
            "tools": [_tool_record_mapping(tool) for tool in tools],
            "toolRecords": tuple(tools),
        }

    async def _call_tool(
        self,
        *,
        profile_user_id: str,
        server_id: str,
        server_config: Mapping[str, Any],
        tool_name: str,
        arguments: Mapping[str, Any],
        expected_revision: str = "",
    ) -> Mapping[str, Any]:
        if expected_revision and expected_revision != self.contract_revision(profile_user_id=profile_user_id, server_id=server_id):
            raise McpClientError("capability_contract_stale")
        server = self._runtime_server(profile_user_id, server_id, server_config)
        try:
            if isinstance(server, McpStreamableHttpServerConfig):
                assert self._http is not None
                result = await self._http.call_tool(server, tool_name, arguments)
            else:
                assert self._stdio is not None
                result = await self._stdio.call_tool(server, tool_name, arguments)
        except Exception as exc:
            reason = mcp_failure_reason(exc, fallback="mcp_tool_call_failed")
            self._mark(profile_user_id, server_id, status="error", reason=reason)
            raise McpClientError(reason) from exc
        self._mark(profile_user_id, server_id, status="ready", reason="")
        return result

    async def _close_server(
        self,
        profile_user_id: str,
        server_id: str,
        server_config: Mapping[str, Any],
    ) -> None:
        server = self._runtime_server(profile_user_id, server_id, server_config)
        if isinstance(server, McpStreamableHttpServerConfig):
            assert self._http is not None
            await self._http.aclose_server(server)
        else:
            assert self._stdio is not None
            await self._stdio.aclose_server(server)

    def _runtime_server(
        self,
        profile_user_id: str,
        server_id: str,
        config: Mapping[str, Any],
    ) -> McpStdioServerConfig | McpStreamableHttpServerConfig:
        runtime_id = f"{str(profile_user_id or 'default')}::{str(server_id)}"
        if _transport(config.get("transport")) == "streamable_http":
            server = _streamable_http_server_config(config, timeout_seconds=self.timeout_seconds)
            return replace(server, server_id=runtime_id)
        command = str(config.get("command") or "").strip()
        args = [str(item) for item in config.get("args") or [] if str(item or "").strip()]
        cwd = str(config.get("cwd") or "").strip() or None
        raw_env = config.get("env")
        env, args = _resolve_runtime_env_and_args(raw_env=raw_env, args=args, cwd=cwd)
        executable, prefix_args = _resolve_stdio_command(command)
        return McpStdioServerConfig(
            server_id=runtime_id,
            command=executable,
            args=tuple([*prefix_args, *args]),
            env=env,
            cwd=cwd,
            enabled=bool(config.get("enabled", True)),
            timeout_seconds=self.timeout_seconds,
            discovery_timeout_seconds=self.timeout_seconds,
        )

    def _mark(self, profile_user_id: str, server_id: str, *, status: str, reason: str, **extra: Any) -> None:
        with self._lock:
            self._statuses[(str(profile_user_id), str(server_id))] = {
                "status": status,
                "reason": reason,
                "updatedAt": time.time(),
                **extra,
            }

    def _run(self, awaitable: Any) -> Any:
        future = self._submit(awaitable)
        try:
            return future.result(timeout=self.timeout_seconds + 5.0)
        except FutureTimeoutError as exc:
            future.cancel()
            raise McpClientError("mcp_host_operation_timeout") from exc

    def _submit(self, awaitable: Any):
        try:
            self._ensure_started()
            if self._loop is None:
                raise McpClientError("mcp_host_worker_unavailable")
        except BaseException:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise
        return asyncio.run_coroutine_threadsafe(awaitable, self._loop)

    def _ensure_started(self) -> None:
        if self._ready.is_set():
            if self._closed:
                raise McpClientError("mcp_host_manager_closed")
            return
        with self._lock:
            if not self._ready.is_set():
                if self._closed:
                    raise McpClientError("mcp_host_manager_closed")
                self._thread = threading.Thread(target=self._run_loop, name="akane-mcp-host", daemon=True)
                self._thread.start()
        if not self._ready.wait(timeout=3.0):
            raise McpClientError("mcp_host_worker_start_timeout")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._stdio = McpSdkPooledStdioClient()
        self._http = McpSdkPooledStreamableHttpClient()
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()


class ManagedMcpClient:
    """Async adapter client that schedules transport work on the Host manager loop."""

    def __init__(
        self,
        *,
        manager: McpHostManager,
        profile_user_id: str,
        server_id: str,
        server_config: Mapping[str, Any],
    ) -> None:
        self.manager = manager
        self.profile_user_id = str(profile_user_id)
        self.server_id = str(server_id)
        self.server_config = dict(server_config)
        self._contract_revision = manager.contract_revision(profile_user_id=self.profile_user_id, server_id=self.server_id)

    async def list_tools(self, _server: Any) -> tuple[McpToolRecord, ...]:
        if self._contract_revision != self.manager.contract_revision(profile_user_id=self.profile_user_id, server_id=self.server_id):
            raise McpClientError("capability_contract_stale")
        return await self.manager.alist_tools(
            profile_user_id=self.profile_user_id,
            server_id=self.server_id,
            server_config=self.server_config,
        )

    async def call_tool(self, _server: Any, tool_name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self.manager.acall_tool(
            profile_user_id=self.profile_user_id,
            server_id=self.server_id,
            server_config=self.server_config,
            tool_name=tool_name,
            arguments=arguments,
            expected_revision=self._contract_revision,
        )

    async def aclose(self) -> None:
        return None


class McpManagementService:
    """Host-owned config and lifecycle orchestration with last-good replacement."""

    def __init__(self, *, base_dir: Any, manager: McpHostManager) -> None:
        self.base_dir = base_dir
        self.manager = manager
        self._locks_guard = threading.RLock()
        self._server_locks: dict[tuple[str, str], threading.RLock] = {}
        try:
            self.migration_status = migrate_legacy_profile_mcp_servers(base_dir=self.base_dir)
        except Exception:
            self.migration_status = {
                "ok": False,
                "status": "migration_failed",
                "reason": "legacy_mcp_registry_migration_failed",
            }

    def _server_lock(self, profile_user_id: str, server_id: str) -> threading.RLock:
        key = (str(profile_user_id), str(server_id))
        with self._locks_guard:
            return self._server_locks.setdefault(key, threading.RLock())

    def list(self, *, profile_user_id: str) -> dict[str, Any]:
        payload = list_mcp_server_configs(base_dir=self.base_dir, profile_user_id=profile_user_id)
        payload["registryMigration"] = dict(self.migration_status)
        for server in payload.get("mcpServers") or []:
            if not isinstance(server, dict):
                continue
            runtime = self.manager.status(
                profile_user_id=profile_user_id,
                server_id=str(server.get("serverId") or ""),
            )
            server["runtimeStatus"] = runtime.get("status")
            server["runtimeReason"] = runtime.get("reason")
        return payload

    def prompt_catalog(self, *, profile_user_id: str) -> str:
        config = load_capability_config(base_dir=self.base_dir, profile_user_id=profile_user_id)
        if approval_mode_for_capability(config.get("approvalPolicy"), "mcp.family") == APPROVAL_MODE_DISABLED:
            return ""
        payload = list_mcp_server_configs(base_dir=self.base_dir, profile_user_id=profile_user_id)
        rows: list[tuple[str, str, str, int]] = []
        for server in payload.get("mcpServers") or []:
            if not isinstance(server, Mapping) or not bool(server.get("enabled")):
                continue
            if str(server.get("status") or "") != "ready":
                continue
            server_id = str(server.get("serverId") or "").strip()
            name = str(server.get("name") or server_id).strip()
            tool_count = int(server.get("toolCount") or 0)
            description = str(server.get("catalogDescription") or "").strip()
            if not description:
                description = f"{name} 提供的外部能力，共 {tool_count} 个工具"
            rows.append((server_id, name, description, tool_count))
        if not rows:
            return ""
        stable = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        revision = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:12]
        lines = [
            f"【可按需加载的 MCP｜目录 {revision}】",
            "MCP 工具默认不占用本轮 schema。可见历史已明确给出准确 server_id、tool_name 和参数契约时，"
            "可用 invoke_mcp 直接复用；否则调用 load_mcp 查看完整工具定义，可一次加载多个。"
            "调用只使用已注册连接及其现有权限；load_mcp 展开的 schema 仅在当前任务回合有效。",
        ]
        lines.extend(f"- {server_id}：{description}" for server_id, _name, description, _count in rows)
        return "\n".join(lines)

    def configure(
        self,
        *,
        profile_user_id: str,
        server_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._server_lock("__host__", server_id):
            return self._configure_locked(
                profile_user_id=profile_user_id,
                server_id=server_id,
                payload=payload,
            )

    def _configure_locked(
        self,
        *,
        profile_user_id: str,
        server_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized = normalize_mcp_server_config_payload(server_id, payload)
        if not normalized.get("ok"):
            return dict(normalized)
        old = get_mcp_server_runtime_config(
            base_dir=self.base_dir,
            profile_user_id=profile_user_id,
            server_id=server_id,
        )
        candidate = {**normalized, "serverId": server_id}
        tools: list[Mapping[str, Any]] | None = None
        if bool(candidate.get("enabled")):
            try:
                discovery = self.manager.discover(
                    profile_user_id=profile_user_id,
                    server_id=server_id,
                    server_config=candidate,
                )
                tools = list(discovery.get("tools") or [])
            except Exception as exc:
                diagnostic = build_mcp_failure_diagnostic(exc, stage="initialize_and_list_tools")
                return {
                    "ok": False,
                    "status": "error",
                    "serverId": server_id,
                    "reason": mcp_failure_reason(exc, fallback="mcp_start_failed"),
                    "diagnostic": diagnostic,
                    "recommendedAction": diagnostic["recommendedAction"],
                    "lastGoodPreserved": bool(old),
                }
        saved = save_mcp_server_config(
            base_dir=self.base_dir,
            profile_user_id=profile_user_id,
            server_id=server_id,
            payload=normalized,
            discovered_tools=tools,
        )
        if not saved.get("ok"):
            if tools is not None:
                self.manager.stop_server(
                    profile_user_id=profile_user_id,
                    server_id=server_id,
                    server_config=candidate,
                    mark_status=False,
                )
                self.manager._mark(
                    profile_user_id,
                    server_id,
                    status="ready" if old else "error",
                    reason="" if old else "mcp_config_save_failed",
                )
            return saved
        clear_mcp_server_profile_override(
            base_dir=self.base_dir,
            profile_user_id=profile_user_id,
            server_id=server_id,
        )
        if old and _config_fingerprint(old) != _config_fingerprint(candidate):
            self.manager.stop_server(
                profile_user_id=profile_user_id,
                server_id=server_id,
                server_config=old,
                disabled=not bool(candidate.get("enabled")),
                mark_status=False,
            )
        elif not bool(candidate.get("enabled")):
            self.manager.stop_server(
                profile_user_id=profile_user_id,
                server_id=server_id,
                server_config=old or candidate,
                disabled=True,
            )
        saved["status"] = "ready" if bool(candidate.get("enabled")) else "disabled"
        saved["toolCount"] = (
            len(tools or []) if tools is not None else int(saved.get("mcpServer", {}).get("toolCount") or 0)
        )
        saved["lastGoodPreserved"] = False
        return saved

    def discover(self, *, profile_user_id: str, server_id: str) -> dict[str, Any]:
        with self._server_lock("__host__", server_id):
            config = get_mcp_server_runtime_config(
                base_dir=self.base_dir,
                profile_user_id=profile_user_id,
                server_id=server_id,
            )
            if not config:
                return {
                    "ok": False,
                    "status": "not_found",
                    "serverId": server_id,
                    "reason": "mcp_server_config_missing",
                }
            if not config.get("enabled"):
                return {
                    "ok": False,
                    "status": "disabled",
                    "serverId": server_id,
                    "reason": "mcp_server_disabled",
                }
            try:
                discovery = self.manager.discover(
                    profile_user_id=profile_user_id,
                    server_id=server_id,
                    server_config=config,
                )
            except Exception as exc:
                diagnostic = build_mcp_failure_diagnostic(exc, stage="list_tools")
                return {
                    "ok": False,
                    "status": "error",
                    "serverId": server_id,
                    "reason": mcp_failure_reason(exc, fallback="mcp_tools_list_failed"),
                    "diagnostic": diagnostic,
                    "recommendedAction": diagnostic["recommendedAction"],
                }
            saved = save_mcp_server_discovery(
                base_dir=self.base_dir,
                profile_user_id=profile_user_id,
                server_id=server_id,
                payload=discovery,
            )
            if saved.get("ok"):
                saved["status"] = "ready"
            return saved

    def set_enabled(self, *, profile_user_id: str, server_id: str, enabled: bool) -> dict[str, Any]:
        with self._server_lock(profile_user_id, server_id):
            config = get_mcp_server_runtime_config(
                base_dir=self.base_dir,
                profile_user_id=profile_user_id,
                server_id=server_id,
            )
            if not config:
                return {
                    "ok": False,
                    "status": "not_found",
                    "serverId": server_id,
                    "reason": "mcp_server_config_missing",
                }
            result = save_mcp_server_profile_enabled(
                base_dir=self.base_dir,
                profile_user_id=profile_user_id,
                server_id=server_id,
                enabled=enabled,
            )
            if result.get("ok") and not enabled:
                self.manager.stop_server(
                    profile_user_id=profile_user_id,
                    server_id=server_id,
                    server_config=config,
                    disabled=True,
                )
            return result

    def restart(self, *, profile_user_id: str, server_id: str) -> dict[str, Any]:
        with self._server_lock(profile_user_id, server_id):
            config = get_mcp_server_runtime_config(
                base_dir=self.base_dir,
                profile_user_id=profile_user_id,
                server_id=server_id,
            )
            if not config:
                return {
                    "ok": False,
                    "status": "not_found",
                    "serverId": server_id,
                    "reason": "mcp_server_config_missing",
                }
            if not config.get("enabled"):
                return {"ok": False, "status": "disabled", "serverId": server_id, "reason": "mcp_server_disabled"}
            self.manager.stop_server(
                profile_user_id=profile_user_id,
                server_id=server_id,
                server_config=config,
            )
            return self.discover(profile_user_id=profile_user_id, server_id=server_id)

    def remove(self, *, profile_user_id: str, server_id: str) -> dict[str, Any]:
        with self._server_lock("__host__", server_id):
            config = get_mcp_server_runtime_config(
                base_dir=self.base_dir,
                profile_user_id=profile_user_id,
                server_id=server_id,
            )
            removed = remove_mcp_server_config(
                base_dir=self.base_dir,
                profile_user_id=profile_user_id,
                server_id=server_id,
            )
            if removed.get("ok") and config:
                self.manager.stop_server(
                    profile_user_id=profile_user_id,
                    server_id=server_id,
                    server_config=config,
                    disabled=True,
                )
            return removed
