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
from capcore import CapabilityToolSpec, validate_tool_spec_args

from capcore_adapter_mcp import (
    McpClientError,
    McpSdkPooledStdioClient,
    McpSdkPooledStreamableHttpClient,
    McpStdioServerConfig,
    McpStreamableHttpServerConfig,
    McpToolRecord,
    McpToolContractError,
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


def _tool_record_from_config(tool: Mapping[str, Any]) -> McpToolRecord:
    return McpToolRecord(name=str(tool.get("name") or ""), description=str(tool.get("description") or ""),
                         input_schema=tool.get("inputSchema"), output_schema=tool.get("outputSchema"),
                         annotations=tool.get("annotations"), raw=dict(tool))


def _catalog_fingerprint(tools) -> str:
    public = [{key: tool.get(key) or ("" if key in {"name", "description"} else {})
               for key in ("name", "description", "inputSchema", "outputSchema", "annotations")} for tool in tools]
    return hashlib.sha256(json.dumps(sorted(public, key=lambda tool: tool["name"]),
                                    ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


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
        self._confirmed_catalogs: dict[tuple[str, str], tuple[str, tuple[dict[str, Any], ...], str]] = {}
        self._discover_tasks: dict[tuple[str, str, str, str], asyncio.Task] = {}
        self.catalog_sink = None
        atexit.register(self.close)

    def contract_revision(self, *, profile_user_id: str, server_id: str) -> str:
        with self._lock:
            return f"{self._contract_boot}:{self._contract_epochs.get((profile_user_id, server_id), 0)}"

    def confirmed_tools(self, *, profile_user_id: str, server_id: str, server_config: Mapping[str, Any]):
        with self._lock:
            value = self._confirmed_catalogs.get((profile_user_id, server_id))
            if (value is not None and value[0] == _config_fingerprint(server_config)
                    and _catalog_fingerprint(server_config.get("tools") or []) in
                    {value[2], _catalog_fingerprint(value[1])}):
                return tuple(dict(tool) for tool in value[1])
        return None

    def _remember_tools(self, profile_user_id, server_id, config, tools, expected_revision):
        records = tuple(_tool_record_mapping(tool) for tool in tools)
        with self._lock:
            if expected_revision != self.contract_revision(profile_user_id=profile_user_id, server_id=server_id):
                return False
            self._confirmed_catalogs[(profile_user_id, server_id)] = (
                _config_fingerprint(config), records, _catalog_fingerprint(config.get("tools") or []))
        return True

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
        expected_tool: McpToolRecord | None = None,
    ) -> Mapping[str, Any]:
        future = self._submit(
            self._call_tool(
                profile_user_id=profile_user_id,
                server_id=server_id,
                server_config=server_config,
                tool_name=tool_name,
                arguments=arguments,
                expected_revision=expected_revision,
                expected_tool=expected_tool,
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
        revision = self.contract_revision(profile_user_id=profile_user_id, server_id=server_id)
        key = (profile_user_id, server_id, _config_fingerprint(server_config), revision)
        task = self._discover_tasks.get(key)
        if task is None:
            task = asyncio.create_task(self._discover_once(profile_user_id=profile_user_id,
                server_id=server_id, server_config=server_config, expected_revision=revision))
            self._discover_tasks[key] = task
            task.add_done_callback(lambda finished: self._finish_discovery(key, finished))
        try:
            return await asyncio.shield(task)
        finally:
            if task.done() and self._discover_tasks.get(key) is task:
                self._discover_tasks.pop(key, None)

    def _finish_discovery(self, key, task):
        if self._discover_tasks.get(key) is task:
            self._discover_tasks.pop(key, None)
        if not task.cancelled():
            task.exception()

    async def _discover_once(self, *, profile_user_id, server_id, server_config, expected_revision):
        if expected_revision != self.contract_revision(profile_user_id=profile_user_id, server_id=server_id):
            raise McpClientError("mcp_config_changed_during_discovery")
        self._mark_current(profile_user_id, server_id, expected_revision, status="starting", reason="")
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
            self._mark_current(profile_user_id, server_id, expected_revision, status="error", reason=reason)
            raise McpClientError(reason) from exc
        if not self._remember_tools(profile_user_id, server_id, server_config, tools, expected_revision):
            raise McpClientError("mcp_config_changed_during_discovery")
        self._mark_current(
            profile_user_id,
            server_id,
            expected_revision,
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
        expected_tool: McpToolRecord | None = None,
    ) -> Mapping[str, Any]:
        if expected_revision and expected_revision != self.contract_revision(profile_user_id=profile_user_id, server_id=server_id):
            raise McpClientError("executor_runtime_changed")
        server = self._runtime_server(profile_user_id, server_id, server_config)
        revision = expected_revision or self.contract_revision(profile_user_id=profile_user_id, server_id=server_id)
        transport = self._http if isinstance(server, McpStreamableHttpServerConfig) else self._stdio
        assert transport is not None
        try:
            try:
                result = await transport.call_tool(server, tool_name, arguments, expected_tool=expected_tool)
            except McpToolContractError:
                # The transport guarantees no tools/call was sent. Resolve its
                # confirmed declaration, rather than asking the model to renew
                # a public fingerprint. Never replay a dispatched/unknown call.
                current = next((tool for tool in transport.confirmed_tools(server) or () if tool.name == tool_name), None)
                if current is None:
                    raise McpClientError("mcp_tool_not_available")
                # Read-only hints are not trusted for host authorization. Only
                # newly declared effects can require stronger admission here.
                policy_hints = ("destructiveHint", "openWorldHint")
                if expected_tool is None or any(
                    (current.annotations or {}).get(key) is True
                    and (expected_tool.annotations or {}).get(key) is not True
                    for key in policy_hints
                ):
                    raise McpClientError("mcp_tool_policy_changed: current safety hints must be admitted by the host")
                spec = CapabilityToolSpec(capability_id="mcp.current", display_name=tool_name, description="",
                    input_schema=dict(current.input_schema or {}), risk="low", confirm="never", effects=(), visible_in=())
                validation = validate_tool_spec_args(spec, arguments)
                if not validation.ok:
                    details = [{"field":error.argument, "reason":error.code, "detail":error.detail}
                               for error in validation.errors]
                    raise McpClientError("mcp_tool_arguments_invalid: " + json.dumps(details, ensure_ascii=False))
                result = await transport.call_tool(server, tool_name, arguments, expected_tool=current)
        except McpToolContractError as exc:
            self._mark_current(profile_user_id, server_id, revision, status="ready", reason="",
                               contractStatus="changed")
            raise McpClientError("mcp_tool_declaration_changed_before_dispatch") from exc
        except Exception as exc:
            reason = mcp_failure_reason(exc, fallback="mcp_tool_call_failed")
            self._mark_current(profile_user_id, server_id, revision, status="error", reason=reason)
            raise McpClientError(reason) from exc
        finally:
            tools = transport.confirmed_tools(server)
            if tools is not None and self._remember_tools(profile_user_id, server_id, server_config, tools, revision):
                sink = self.catalog_sink
                if callable(sink):
                    try:
                        await asyncio.to_thread(sink, profile_user_id, server_id, server_config,
                                                tuple(_tool_record_mapping(tool) for tool in tools), revision)
                    except Exception:
                        self._mark_current(profile_user_id, server_id, revision,
                                           catalogReason="mcp_catalog_persist_failed")
        self._mark_current(profile_user_id, server_id, revision, status="ready", reason="")
        return result

    def _mark_current(self, profile_user_id, server_id, expected_revision, **fields):
        with self._lock:
            if expected_revision == self.contract_revision(profile_user_id=profile_user_id, server_id=server_id):
                value = dict(self._statuses.get((profile_user_id, server_id)) or {"status": "starting", "reason": ""})
                value.pop("updatedAt", None)
                self._mark(profile_user_id, server_id, **{**value, **fields})

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
            if self._closed:
                raise McpClientError("mcp_host_manager_closed")
            if not self._ready.is_set() and self._thread is None:
                thread = threading.Thread(target=self._run_loop, name="akane-mcp-host", daemon=True)
                thread.start()
                self._thread = thread
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
        self._tools = tuple(_tool_record_from_config(tool) for tool in self.server_config.get("tools") or [])

    async def list_tools(self, _server: Any) -> tuple[McpToolRecord, ...]:
        if self._contract_revision != self.manager.contract_revision(profile_user_id=self.profile_user_id, server_id=self.server_id):
            raise McpClientError("executor_runtime_changed")
        return self._tools

    async def call_tool(self, _server: Any, tool_name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self.manager.acall_tool(
            profile_user_id=self.profile_user_id,
            server_id=self.server_id,
            server_config=self.server_config,
            tool_name=tool_name,
            arguments=arguments,
            expected_revision=self._contract_revision,
            expected_tool=next((tool for tool in self._tools if tool.name == tool_name), None),
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
        self.manager.catalog_sink = self._persist_invocation_catalog
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

    def _persist_invocation_catalog(self, profile_user_id, server_id, config, tools, revision):
        with self._server_lock("__host__", server_id):
            current = get_mcp_server_runtime_config(base_dir=self.base_dir, profile_user_id=profile_user_id, server_id=server_id)
            if (not current or not current.get("enabled") or _config_fingerprint(current) != _config_fingerprint(config)
                    or self.manager.contract_revision(profile_user_id=profile_user_id, server_id=server_id) != revision
                    or _catalog_fingerprint(current.get("tools") or []) not in
                    {_catalog_fingerprint(config.get("tools") or []), _catalog_fingerprint(tools)}):
                return
            normalized = normalize_mcp_tool_discovery_payload(server_id, {"tools": list(tools)})
            previous = {str(tool.get("name") or ""): tool for tool in current.get("tools") or []}
            merged = [{**tool, **{key: previous.get(tool["name"], {})[key]
                       for key in ("risk", "confirm", "effects", "modelVisible", "promptExposed")
                       if key in previous.get(tool["name"], {})}} for tool in normalized.get("tools") or []]
            if normalized.get("ok") and merged != list(current.get("tools") or []):
                result = save_mcp_server_discovery(base_dir=self.base_dir, profile_user_id=profile_user_id,
                                                   server_id=server_id, payload={"tools": merged})
                if not result.get("ok"):
                    raise McpClientError("mcp_catalog_persist_failed")

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
