"""Marketplace, cancellable artifact work and existing sync/diagnostic entry points."""

from __future__ import annotations


import asyncio
from functools import wraps
from pathlib import Path
from typing import Any, Iterable, Mapping
from capcore import CapabilityResult, InvocationContext
from ..plugin_installation import PluginInstallationError
from .contracts import _failure


async def _wait_terminal_task(task: asyncio.Task) -> bool:
    """Keep ownership of admitted work even after repeated caller cancellation."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    return cancelled


def _serialized_mutation(operation):
    """Acquire the one lock, then settle the entire commit/restore transaction.

    A caller cancelled while queued never starts work. Once admitted, its
    cancellation is delivered after the artifact, selection and live runtime
    have reached the operation's terminal state.
    """

    @wraps(operation)
    async def settled(self, *args, **kwargs):
        async with self._operation_lock:
            task = asyncio.create_task(operation(self, *args, **kwargs))
            cancelled = await _wait_terminal_task(task)
            if cancelled:
                if not task.cancelled():
                    task.exception()  # Consume a terminal failure before returning cancellation.
                raise asyncio.CancelledError
            return task.result()

    return settled


class ManagementOperations:
    __slots__ = ()

    async def browse_market(self) -> dict[str, Any]:
        if self.market is None:
            return {**_failure("unavailable", "plugin_market_not_configured"), "plugins": []}
        try:
            payload = await asyncio.to_thread(self.market.browse)
            installed = {item["plugin_id"]: item for item in self.public_snapshot()["plugins"]}
            for item in payload["plugins"]:
                current = installed.get(item["plugin_id"], {})
                item["installed_version"] = current.get("version", "")
                item["installed_status"] = current.get("runtime_status", "not_installed")
            return payload
        except PluginInstallationError as exc:
            return {
                **_failure(exc.status, exc.reason, schema_errors=exc.schema_errors, diagnostics=exc.diagnostics),
                "plugins": [],
            }
        except Exception:
            return {**_failure("error", "plugin_market_read_failed"), "plugins": []}

    async def stage_market(self, *, plugin_id: str, digest: str) -> dict[str, Any]:
        if self.market is None or self.artifact_store is None:
            return _failure("unavailable", "plugin_market_not_configured")
        async with self._operation_lock:
            return await self._run_cancellable_artifact_operation(
                lambda: self.market.stage(self.artifact_store, plugin_id=plugin_id, digest=digest),
                failure_reason="plugin_market_stage_failed",
                discard_staged_result=True,
            )

    async def _run_cancellable_artifact_operation(
        self,
        operation: Any,
        *,
        failure_reason: str,
        discard_staged_result: bool = False,
    ) -> dict[str, Any]:
        """Drain a synchronous installer before releasing the management lock."""

        task = asyncio.create_task(asyncio.to_thread(operation))
        cancelled = await _wait_terminal_task(task)
        if cancelled:
            if not task.cancelled() and task.exception() is None:
                result = task.result()
                if discard_staged_result and isinstance(result, Mapping):
                    stage_id = str(result.get("stage_id") or "").strip()
                    if stage_id:
                        cleanup = asyncio.create_task(asyncio.to_thread(self.artifact_store.discard_stage, stage_id))
                        await _wait_terminal_task(cleanup)
                        try:
                            removed = cleanup.result()
                        except Exception:
                            raise asyncio.CancelledError("plugin_stage_discard_failed") from None
                        if not isinstance(removed, Mapping) or not removed.get("ok"):
                            raise asyncio.CancelledError("plugin_stage_discard_failed")
            raise asyncio.CancelledError
        try:
            return dict(task.result())
        except PluginInstallationError as exc:
            return _failure(exc.status, exc.reason, schema_errors=exc.schema_errors, diagnostics=exc.diagnostics)
        except Exception:
            return _failure("error", failure_reason)

    async def stage_wheel(self, *, wheel_path: str) -> dict[str, Any]:
        if self.artifact_store is None:
            return _failure("unavailable", "plugin_artifact_store_unavailable")
        normalized = str(wheel_path or "").strip()
        if not normalized:
            return _failure("invalid_request", "plugin_wheel_path_required")
        async with self._operation_lock:
            return await self._run_cancellable_artifact_operation(
                lambda: self.artifact_store.stage_wheel(Path(normalized)),
                failure_reason="plugin_stage_failed",
                discard_staged_result=True,
            )

    async def stage_source(self, *, source_path: str) -> dict[str, Any]:
        if self.artifact_store is None:
            return _failure("unavailable", "plugin_artifact_store_unavailable")
        normalized = str(source_path or "").strip()
        if not normalized:
            return _failure("invalid_request", "plugin_source_path_required")
        async with self._operation_lock:
            return await self._run_cancellable_artifact_operation(
                lambda: self.artifact_store.stage_source(Path(normalized)),
                failure_reason="plugin_source_stage_failed",
                discard_staged_result=True,
            )

    async def test_source(self, *, source_path: str) -> dict[str, Any]:
        if self.artifact_store is None:
            return _failure("unavailable", "plugin_artifact_store_unavailable")
        normalized = str(source_path or "").strip()
        if not normalized:
            return _failure("invalid_request", "plugin_source_path_required")
        async with self._operation_lock:
            return await self._run_cancellable_artifact_operation(
                lambda: self.artifact_store.test_source(Path(normalized)),
                failure_reason="plugin_source_test_failed",
            )

    @_serialized_mutation
    async def discard_stage(self, *, stage_id: str) -> dict[str, Any]:
        if self.artifact_store is None:
            return _failure("unavailable", "plugin_artifact_store_unavailable")
        try:
            return dict(await asyncio.to_thread(self.artifact_store.discard_stage, stage_id))
        except PluginInstallationError as exc:
            return _failure(exc.status, exc.reason, schema_errors=exc.schema_errors, diagnostics=exc.diagnostics)
        except Exception:
            return _failure("error", "plugin_stage_discard_failed")

    async def invoke_capability(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        *,
        context: InvocationContext,
    ) -> CapabilityResult:
        """Invoke diagnostics through the same runtime used by lifecycle work."""

        return await self.plugin_runtime.invoke(
            capability_id,
            args,
            context=context,
        )

    def execute_sync(
        self,
        *,
        action: str,
        plugin_id: str = "",
        path: str = "",
        stage_id: str = "",
        digest: str = "",
        approved_permissions: Iterable[str] = (),
    ) -> dict[str, Any]:
        normalized_action = str(action or "").strip().lower()
        if normalized_action == "list":
            payload = self.snapshot()
            payload["action"] = "list"
            return payload
        if normalized_action == "market":
            coroutine = self.browse_market()
        elif normalized_action == "stage_market":
            coroutine = self.stage_market(plugin_id=plugin_id, digest=digest)
        elif normalized_action == "test_source":
            coroutine = self.test_source(source_path=path)
        elif normalized_action == "stage_source":
            coroutine = self.stage_source(source_path=path)
        elif normalized_action == "stage_wheel":
            coroutine = self.stage_wheel(wheel_path=path)
        elif normalized_action == "install":
            coroutine = self.install_stage(
                stage_id=stage_id,
                approved_permissions=tuple(approved_permissions),
            )
        elif normalized_action == "discard_stage":
            coroutine = self.discard_stage(stage_id=stage_id)
        elif normalized_action == "enable":
            coroutine = self.set_enabled(plugin_id=plugin_id, enabled=True)
        elif normalized_action == "disable":
            coroutine = self.set_enabled(plugin_id=plugin_id, enabled=False)
        elif normalized_action == "rollback":
            coroutine = self.rollback(plugin_id=plugin_id)
        elif normalized_action == "uninstall":
            coroutine = self.uninstall(plugin_id=plugin_id)
        else:
            return _failure("invalid_request", "extension_action_invalid", plugin_id=plugin_id)
        runtime_loop = self.plugin_runtime.runtime_loop
        if runtime_loop is None or not runtime_loop.is_running():
            coroutine.close()
            return _failure("unavailable", "plugin_runtime_loop_unavailable", plugin_id=plugin_id)
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is runtime_loop:
            coroutine.close()
            return _failure("unavailable", "plugin_management_requires_worker_thread", plugin_id=plugin_id)
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, runtime_loop)
        except RuntimeError:
            coroutine.close()
            return _failure("unavailable", "plugin_runtime_loop_unavailable", plugin_id=plugin_id)
        except Exception:
            coroutine.close()
            return _failure("error", "plugin_management_failed", plugin_id=plugin_id)
        try:
            return dict(future.result(timeout=self.sync_timeout_seconds))
        except TimeoutError:
            future.cancel()
            return _failure("timeout", "plugin_management_timeout", plugin_id=plugin_id)
        except Exception:
            return _failure("error", "plugin_management_failed", plugin_id=plugin_id)
