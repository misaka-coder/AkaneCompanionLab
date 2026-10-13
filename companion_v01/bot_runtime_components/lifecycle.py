"""Ordered startup, job recovery and service shutdown for one Bot."""

from __future__ import annotations

import asyncio
from typing import Any

from ..capability_diagnosis import build_host_command_registrations
from ..host_jobs import HostJob

from .completion import _dispatch_host_job_completion


class BotLifecycle:
    """Start and drain the services owned by one BotRuntime."""

    __slots__ = ()

    async def start(self) -> dict[str, Any]:
        async with self._lifecycle_lock:
            if self._stop_status is not None:
                return {"status": "unavailable", "reason": "bot_runtime_stopped", "bot_id": self.bot_id}
            if self._stopping:
                return {"status": "unavailable", "reason": "bot_runtime_stopping", "bot_id": self.bot_id}
            if self._started:
                result = dict(self._start_status)
                result["already_started"] = True
                if result["status"] == "active":
                    result["reason"] = "already_started"
                return result
            return await self._start_services()

    async def _start_services(self) -> dict[str, Any]:
        plugin_status = await self.plugin_runtime.start()
        reconcile_runtime = getattr(
            self.extension_management_service,
            "reconcile_runtime",
            None,
        )
        artifact_status = (
            reconcile_runtime(plugin_status)
            if callable(reconcile_runtime)
            else {"status": "not_configured", "reload_required": False}
        )
        if artifact_status.get("reload_required"):
            first_artifact_status = dict(artifact_status)
            fallback_status = await self.plugin_runtime.restart()
            fallback_artifact_status = (
                reconcile_runtime(fallback_status)
                if callable(reconcile_runtime)
                else {"status": "not_configured", "reload_required": False}
            )
            artifact_status = {
                **dict(fallback_artifact_status),
                "fallback_attempted": True,
                "initial_status": str(first_artifact_status.get("status") or ""),
            }
            if fallback_status.get("published") is True:
                plugin_status = fallback_status
        host_commands = build_host_command_registrations(
            engine=self.engine,
            qq_gateway=self.qq_gateway,
            config_module=self.config_module,
            satellite_service=self.desktop_satellite_service,
            bot_label=str(getattr(self, "display_name", "") or ""),
        )
        self.plugin_command_broker = self.plugin_runtime.build_qq_command_broker(host_registrations=host_commands)
        self.plugin_event_broker = self.plugin_runtime.build_event_broker()
        bind_timeline = getattr(self.plugin_runtime, "bind_timeline_recorder", None)
        if callable(bind_timeline):
            # Reuse the engine's existing public MemCore timeline port.
            bind_timeline(self.engine.record_plugin_timeline_event)
        build_hook_broker = getattr(self.plugin_runtime, "build_hook_broker", None)
        self.plugin_hook_broker = build_hook_broker() if callable(build_hook_broker) else None
        recover_queue = getattr(self.session_work_queue, "recover", None)
        if callable(recover_queue):
            await recover_queue()
        recover_jobs = getattr(self.job_store, "recover_abandoned_claims", None)
        if callable(recover_jobs):
            await asyncio.to_thread(recover_jobs)
        if self.host_workflow_jobs is not None:
            self.host_workflow_jobs.recover()
        if self.host_tool_jobs is not None:
            host_loop = asyncio.get_running_loop()

            def submit_completion(job: HostJob) -> Any:
                future = asyncio.run_coroutine_threadsafe(
                    _dispatch_host_job_completion(self.engine, self.plugin_agent_event_router, job),
                    host_loop,
                )
                try:
                    return future.result(timeout=900.0)
                except Exception:
                    future.cancel()
                    raise

            self.host_tool_jobs.bind_terminal_callback(submit_completion)
            if self.host_execution_jobs is not None:
                await asyncio.to_thread(self.host_execution_jobs.recover)
            if self.host_subagent_jobs is not None:
                self.host_subagent_jobs.recover()
            self.host_tool_jobs.recover()
        bind_hook_broker = getattr(self.engine, "bind_plugin_hook_broker", None)
        if callable(bind_hook_broker):
            bind_hook_broker(self.plugin_hook_broker)
        bind_gateway_hook_broker = getattr(self.qq_gateway, "bind_plugin_hook_broker", None)
        if callable(bind_gateway_hook_broker):
            bind_gateway_hook_broker(self.plugin_hook_broker)
        mirror = getattr(self.engine, "owner_private_group_memory", None)
        if mirror is not None:
            await mirror.start()
        status = "degraded" if plugin_status.get("status") == "degraded" else "active"
        self._start_status = {
            "status": status,
            "reason": "plugin_runtime_degraded" if status == "degraded" else "",
            "bot_id": self.bot_id,
            "plugin_status": plugin_status,
            "plugin_artifact_status": artifact_status,
        }
        self._started = True
        return dict(self._start_status)

    async def stop(self) -> dict[str, Any]:
        # Closing admission is irreversible even if a drain must be retried.
        self._stopping = True
        async with self._lifecycle_lock:
            return await self._stop_services()

    def _deferred_shutdown(self, reason: str, queue_status: dict[str, Any], engine_reason: str) -> dict[str, Any]:
        return {
            "status": "degraded",
            "reason": reason,
            "bot_id": self.bot_id,
            "queue_status": queue_status,
            "engine_status": {"status": "deferred", "reason": engine_reason},
        }

    async def _stop_services(self) -> dict[str, Any]:
        if self._stop_status is not None:
            return dict(self._stop_status)
        self._started = False
        queue_status: dict[str, Any] = {"status": "not_configured"}
        try:
            self.plugin_agent_event_router.request_shutdown()
        except Exception:
            return self._deferred_shutdown("agent_turn_shutdown_signal_failed", queue_status, "agent_turns_not_drained")
        if self.session_work_queue is not None:
            try:
                queue_status = await self.session_work_queue.close(timeout=10.0)
            except Exception:
                queue_status = {"status": "error", "reason": "session_queue_shutdown_failed"}
            if queue_status.get("status") != "stopped":
                # Do not cancel a to_thread model turn or close the services it
                # still uses. A later stop may retry after the turn settles.
                return self._deferred_shutdown(
                    "session_queue_shutdown_incomplete", queue_status, "session_queue_not_drained"
                )

        failures: list[str] = []
        mirror = getattr(self.engine, "owner_private_group_memory", None)
        if mirror is not None:
            try:
                drained = await mirror.close()
            except Exception:
                return self._deferred_shutdown(
                    "private_memory_import_shutdown_failed", queue_status, "private_memory_import_not_drained"
                )
            if not drained:
                return self._deferred_shutdown(
                    "private_memory_import_shutdown_incomplete", queue_status, "private_memory_import_not_drained"
                )
        try:
            await self.plugin_agent_event_router.aclose()
        except Exception:
            return self._deferred_shutdown("agent_turn_shutdown_failed", queue_status, "agent_turns_not_drained")
        followup_status: dict[str, Any] = {"status": "not_configured"}
        plugin_status: dict[str, Any] = {"status": "not_started"}
        voice_status: dict[str, Any] = {"status": "not_configured"}
        engine_status: dict[str, Any] = {"status": "not_started"}

        if self.host_tool_jobs is not None:
            try:
                self.host_tool_jobs.bind_terminal_callback(None)
            except Exception:
                failures.append("host_job_callback_unbind_failed")

        request_engine_shutdown = getattr(self.engine, "request_shutdown", None)
        if callable(request_engine_shutdown):
            try:
                request_engine_shutdown()
            except Exception:
                failures.append("engine_shutdown_signal_failed")

        if self.qq_followup_tasks is not None:
            try:
                followup_status = await self.qq_followup_tasks.close(timeout=10.0)
                if followup_status.get("status") != "stopped":
                    failures.append("qq_followup_shutdown_incomplete")
            except Exception:
                failures.append("qq_followup_shutdown_failed")
                followup_status = {"status": "error", "reason": "qq_followup_shutdown_failed"}

        try:
            plugin_status = await self.plugin_runtime.stop()
            if int(plugin_status.get("close_failure_count") or 0) > 0 or plugin_status.get("cleanup_failures"):
                failures.append("plugin_runtime_close_incomplete")
            elif plugin_status.get("status") != "stopped":
                failures.append("plugin_runtime_shutdown_incomplete")
        except Exception:
            failures.append("plugin_runtime_shutdown_failed")
            plugin_status = {"status": "error", "reason": "plugin_runtime_shutdown_failed"}

        self.plugin_command_broker = None
        self.plugin_event_broker = None
        unbind_gateway_hook_broker = getattr(self.qq_gateway, "bind_plugin_hook_broker", None)
        if callable(unbind_gateway_hook_broker):
            try:
                unbind_gateway_hook_broker(None)
            except Exception:
                failures.append("qq_gateway_hook_unbind_failed")
        self.plugin_hook_broker = None
        if self.voice_runtime_service is not None:
            try:
                # Runtime cleanup owns blocking worker/thread joins. Keep those
                # joins off the Host lifecycle loop so sibling Bots can stop
                # concurrently under the same shutdown deadline.
                voice_status = await asyncio.to_thread(self.voice_runtime_service.close)
                if voice_status.get("status") != "stopped":
                    failures.append("voice_runtime_shutdown_incomplete")
            except Exception:
                failures.append("voice_runtime_shutdown_failed")
                voice_status = {"status": "error", "reason": "voice_runtime_shutdown_failed"}
        try:
            engine_status = await asyncio.to_thread(self.engine.close)
            if engine_status.get("status") != "stopped":
                failures.append("engine_shutdown_incomplete")
        except Exception:
            failures.append("engine_shutdown_failed")
            engine_status = {"status": "error", "reason": "engine_shutdown_failed"}

        self._stop_status = {
            "status": "degraded" if failures else "stopped",
            "reason": failures[0] if failures else "",
            "bot_id": self.bot_id,
            "failures": failures,
            "queue_status": queue_status,
            "followup_status": followup_status,
            "plugin_status": plugin_status,
            "voice_status": voice_status,
            "engine_status": engine_status,
        }
        return dict(self._stop_status)
