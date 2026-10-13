"""Observable asynchronous readiness with a bounded cache."""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Mapping

from ...anysearch_rest_client import AnySearchRestError
from ...local_capability_config import get_mcp_server_runtime_config
from ...mcp_stdio_discoverer import McpStdioDiscoveryError
from .search_transport import _mcp_server_has_transport_config


class SearchReadinessMixin:
    def capability_status(
        self,
        *,
        profile_user_id: str = "",
        session_id: str = "",
        client_mode: str = "",
    ) -> dict[str, Any]:
        del session_id
        runtime_profile_user_id = self._resolve_runtime_profile_user_id_values(
            profile_user_id=profile_user_id,
            client_mode=client_mode,
        )
        server = get_mcp_server_runtime_config(
            base_dir=self.config_base_dir,
            profile_user_id=runtime_profile_user_id,
            server_id=self.server_id,
        )
        if server and not bool(server.get("enabled")):
            return {"offered": False, "enabled": False, "status": "disabled", "reason": "anysearch_disabled"}

        if not _mcp_server_has_transport_config(server):
            return self._rest_capability_status(runtime_profile_user_id)

        fingerprint = self._server_readiness_fingerprint(server)
        cache_key = (runtime_profile_user_id, fingerprint)
        now = float(self._readiness_clock())
        with self._readiness_lock:
            cached = self._readiness_cache.get(cache_key)
            if cached is not None and cached[0] > now:
                return self._stable_configured_status(cached[1])
            if self._readiness_probe_in_background:
                if cache_key not in self._readiness_probes_inflight:
                    self._readiness_probes_inflight.add(cache_key)
                    threading.Thread(
                        target=self._probe_readiness_background,
                        kwargs={"cache_key": cache_key, "server": dict(server)},
                        name="akane-anysearch-readiness",
                        daemon=True,
                    ).start()
                return {
                    "offered": True,
                    "enabled": True,
                    "status": "checking",
                    "reason": "anysearch_probe_pending",
                    "cache_ttl_seconds": 1.0,
                }
        return self._stable_configured_status(self._probe_readiness(cache_key=cache_key, server=server))

    def _rest_capability_status(self, profile_user_id: str) -> dict[str, Any]:
        endpoint = str(getattr(self.anysearch_rest_client, "endpoint", "anysearch-rest") or "anysearch-rest")
        fingerprint = "rest:" + hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
        cache_key = (profile_user_id, fingerprint)
        now = float(self._readiness_clock())
        with self._readiness_lock:
            cached = self._readiness_cache.get(cache_key)
            if cached is not None and cached[0] > now:
                return self._stable_configured_status(cached[1])
            if self._readiness_probe_in_background:
                if cache_key not in self._readiness_probes_inflight:
                    self._readiness_probes_inflight.add(cache_key)
                    threading.Thread(
                        target=self._probe_rest_readiness_background,
                        kwargs={"cache_key": cache_key},
                        name="akane-anysearch-rest-readiness",
                        daemon=True,
                    ).start()
                return {
                    "offered": True,
                    "enabled": True,
                    "status": "checking",
                    "reason": "anysearch_rest_probe_pending",
                    "cache_ttl_seconds": 1.0,
                }
        return self._stable_configured_status(self._probe_rest_readiness(cache_key=cache_key))

    def _probe_rest_readiness(self, *, cache_key: tuple[str, str]) -> dict[str, Any]:
        with self._readiness_lock:
            previous = self._readiness_cache.get(cache_key)
        try:
            self.anysearch_rest_client.call(action="search", arguments={"query": "OpenAI", "max_results": 1})
            status = {"enabled": True, "status": "ready", "reason": "", "transport": "rest"}
        except AnySearchRestError as exc:
            status = {"enabled": False, "status": "unavailable", "reason": exc.reason, "transport": "rest"}
        except Exception:
            status = {
                "enabled": False,
                "status": "unavailable",
                "reason": "anysearch_rest_probe_failed",
                "transport": "rest",
            }
        return self._remember_probe_readiness(cache_key, status, previous)

    def _probe_rest_readiness_background(self, *, cache_key: tuple[str, str]) -> None:
        try:
            self._probe_rest_readiness(cache_key=cache_key)
        finally:
            with self._readiness_lock:
                self._readiness_probes_inflight.discard(cache_key)

    def _probe_readiness(
        self,
        *,
        cache_key: tuple[str, str],
        server: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._readiness_lock:
            previous = self._readiness_cache.get(cache_key)
        try:
            result = self._run_coro_blocking(
                self._call_readiness_mcp(
                    server=server,
                    tool_name="search",
                    arguments={"query": "OpenAI", "max_results": 1},
                )
            )
            if self._mcp_result_is_error(result):
                status = self._probe_rest_fallback(
                    cache_key=cache_key, mcp_reason=self._mcp_tool_error_reason(result=result, detail=""),
                )
            else:
                status = {"enabled": True, "status": "ready", "reason": "", "cache_ttl_seconds": 5.0}
        except McpStdioDiscoveryError as exc:
            status = self._probe_rest_fallback(
                cache_key=cache_key,
                mcp_reason=self._safe_mcp_failure_reason(exc),
            )
        except Exception as exc:
            status = self._probe_rest_fallback(
                cache_key=cache_key, mcp_reason=f"anysearch_probe_failed:{type(exc).__name__}",
            )
        return self._remember_probe_readiness(cache_key, status, previous)

    def _probe_rest_fallback(self, *, cache_key: tuple[str, str], mcp_reason: str) -> dict[str, Any]:
        endpoint = str(getattr(self.anysearch_rest_client, "endpoint", "anysearch-rest") or "anysearch-rest")
        rest_cache_key = (cache_key[0], "rest:" + hashlib.sha256(endpoint.encode("utf-8")).hexdigest())
        rest_status = self._probe_rest_readiness(cache_key=rest_cache_key)
        if bool(rest_status.get("enabled")):
            status = {
                **rest_status,
                "fallback_from": "mcp",
                "mcp_reason": str(mcp_reason or "mcp_call_failed")[:120],
            }
        else:
            status = {
                "enabled": False,
                "status": "unavailable",
                "reason": str(mcp_reason or "mcp_call_failed")[:120],
                "fallback_reason": str(rest_status.get("reason") or "anysearch_rest_failed")[:120],
            }
        return status

    def _remember_probe_readiness(
        self, cache_key: tuple[str, str], status: Mapping[str, Any],
        previous: tuple[float, dict[str, Any]] | None,
    ) -> dict[str, Any]:
        with self._readiness_lock:
            current = self._readiness_cache.get(cache_key)
            if current is previous:
                self._remember_readiness(cache_key, status)
            elif current is not None:
                # A real execution or another probe finished after this probe
                # started. Keep its newer evidence instead of a late failure.
                return dict(current[1])
        return dict(status)

    def _probe_readiness_background(self, *, cache_key: tuple[str, str], server: Mapping[str, Any]) -> None:
        try:
            self._probe_readiness(cache_key=cache_key, server=server)
        finally:
            with self._readiness_lock:
                self._readiness_probes_inflight.discard(cache_key)

    @staticmethod
    def _server_readiness_fingerprint(server: Mapping[str, Any]) -> str:
        stable = {
            "enabled": bool(server.get("enabled")),
            "transport": str(server.get("transport") or "stdio"),
            "command": str(server.get("command") or ""),
            "args": [str(item or "") for item in server.get("args") or []],
            "cwd": str(server.get("cwd") or ""),
            "url": str(server.get("url") or ""),
            "headers": {
                str(key): str(value)
                for key, value in sorted(
                    (server.get("headers") or {}).items(),
                    key=lambda item: str(item[0]),
                )
            }
            if isinstance(server.get("headers"), Mapping)
            else {},
        }
        serialized = json.dumps(stable, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _stable_configured_status(status: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(status)
        raw_enabled = bool(result.get("enabled"))
        result["offered"] = True
        result["enabled"] = True
        if str(result.get("status") or "") != "checking":
            result["healthy"] = raw_enabled
        return result

    def _remember_readiness(self, cache_key: tuple[str, str], status: Mapping[str, Any]) -> None:
        ttl = self._readiness_ready_ttl_seconds if bool(status.get("enabled")) else self._readiness_failure_ttl_seconds
        with self._readiness_lock:
            self._readiness_cache[cache_key] = (float(self._readiness_clock()) + ttl, dict(status))
            if len(self._readiness_cache) > 32:
                now = float(self._readiness_clock())
                self._readiness_cache = {key: value for key, value in self._readiness_cache.items() if value[0] > now}
                while len(self._readiness_cache) > 32:
                    oldest = min(self._readiness_cache, key=lambda key: self._readiness_cache[key][0])
                    del self._readiness_cache[oldest]

    def _remember_server_ready(self, profile_user_id: str, server: Mapping[str, Any]) -> None:
        self._remember_readiness(
            (profile_user_id, self._server_readiness_fingerprint(server)),
            {"enabled": True, "status": "ready", "reason": "", "cache_ttl_seconds": 5.0},
        )

    def _cached_server_readiness(
        self,
        profile_user_id: str,
        server: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        cache_key = (profile_user_id, self._server_readiness_fingerprint(server))
        now = float(self._readiness_clock())
        with self._readiness_lock:
            cached = self._readiness_cache.get(cache_key)
            if cached is None or cached[0] <= now:
                return None
            return dict(cached[1])

    def _remember_server_failure(self, profile_user_id: str, server: Mapping[str, Any], *, reason: str) -> None:
        self._remember_readiness(
            (profile_user_id, self._server_readiness_fingerprint(server)),
            {"enabled": False, "status": "unavailable", "reason": str(reason or "mcp_call_failed")[:120]},
        )

    def _remember_server_rest_fallback(
        self,
        profile_user_id: str,
        server: Mapping[str, Any],
        *,
        mcp_reason: str,
    ) -> None:
        self._remember_readiness(
            (profile_user_id, self._server_readiness_fingerprint(server)),
            {
                "enabled": True,
                "status": "ready",
                "reason": "",
                "transport": "rest",
                "fallback_from": "mcp",
                "mcp_reason": str(mcp_reason or "mcp_call_failed")[:120],
            },
        )
