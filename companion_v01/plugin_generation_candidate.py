"""Build one complete isolated plugin generation from selected artifacts.

Artifact selection remains owned by :mod:`plugin_installation`; process
execution remains owned by :mod:`plugin_generation`; atomic publication stays
in :mod:`plugin_active_generation`.  This module is only the composition seam
between those three authorities.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Callable, Protocol

from .instance_profile import PluginSelection
from .plugin_api import is_valid_plugin_id
from .plugin_active_generation import (
    PluginActiveGenerationError,
    PluginGenerationEndpoint,
    PluginGenerationSnapshot,
)
from .plugin_generation import PluginGenerationError, PluginGenerationProcess
from .plugin_installation import PluginGenerationSource, PluginInstallationError
from .plugin_service_dependencies import resolve_dependencies, provider_bindings


class PluginGenerationSourceResolver(Protocol):
    def resolve_generation_source(self, plugin_id: str) -> PluginGenerationSource: ...


class PluginGenerationCandidateError(RuntimeError):
    """A full candidate could not be built; no partial candidate is returned."""

    def __init__(self, reason: str, *, plugin_id: str = "", schema_errors=()) -> None:
        self.reason = str(reason or "plugin_generation_candidate_failed")
        self.plugin_id = str(plugin_id or "")
        self.schema_errors = tuple(dict(error) for error in schema_errors)
        super().__init__(self.reason)


class PluginGenerationCandidateBuilder:
    """Prepare enabled plugins, then activate the dependency-resolved subset."""

    def __init__(
        self,
        *,
        source_resolver: PluginGenerationSourceResolver,
        project_root: Path,
        work_root: Path,
        python_executable: str = sys.executable,
        process_factory: Callable[..., PluginGenerationEndpoint] = PluginGenerationProcess,
        managed_artifact_sink: Any = None,
        resource_provider: Any = None,
        capability_provider: Any = None,
        connection_provider: Any = None,
        events_provider: Any = None,
        tasks_provider: Any = None,
        notification_port: Any = None,
        plugin_storage_data_root: Path | None = None,
        plugin_storage_instance_id: str = "",
        service_bindings_provider: Any = None,
        bootstrap_services: tuple = (),
    ) -> None:
        if not callable(getattr(source_resolver, "resolve_generation_source", None)):
            raise TypeError("invalid_plugin_generation_source_resolver")
        self.source_resolver = source_resolver
        self.project_root = Path(project_root).resolve()
        self.work_root = Path(work_root).resolve()
        self.python_executable = str(python_executable or sys.executable)
        self.process_factory = process_factory
        self.service_bindings_provider = service_bindings_provider
        self.bootstrap_services = tuple(bootstrap_services)
        self.managed_artifact_sink = managed_artifact_sink
        self.resource_provider = resource_provider
        self.capability_provider = capability_provider
        self.connection_provider = connection_provider
        self.events_provider = events_provider
        self.tasks_provider = tasks_provider
        self.notification_port = notification_port
        self.plugin_storage_data_root = (
            Path(plugin_storage_data_root).resolve()
            if plugin_storage_data_root is not None
            else None
        )
        self.plugin_storage_instance_id = str(plugin_storage_instance_id or "").strip()
        if (self.plugin_storage_data_root is None) != (not self.plugin_storage_instance_id):
            raise ValueError("plugin_generation_storage_scope_incomplete")

    async def build(
        self,
        selections: tuple[PluginSelection, ...],
    ) -> PluginGenerationSnapshot:
        normalized = self._validate_selections(selections)
        try:
            bindings = self.service_bindings_provider() if self.service_bindings_provider is not None else ()
            provider_bindings(bindings)
        except Exception as exc:
            raise PluginGenerationCandidateError("service_provider_bindings_invalid") from exc
        enabled = tuple(item for item in normalized if item.enabled)
        sources: list[PluginGenerationSource] = []
        for selection in enabled:
            try:
                source = await asyncio.to_thread(
                    self.source_resolver.resolve_generation_source,
                    selection.plugin_id,
                )
            except PluginInstallationError as exc:
                raise PluginGenerationCandidateError(
                    exc.reason,
                    plugin_id=selection.plugin_id,
                    schema_errors=exc.schema_errors,
                ) from exc
            except Exception as exc:
                raise PluginGenerationCandidateError(
                    "plugin_generation_source_unavailable",
                    plugin_id=selection.plugin_id,
                ) from exc
            if not isinstance(source, PluginGenerationSource):
                raise PluginGenerationCandidateError(
                    "plugin_generation_source_invalid",
                    plugin_id=selection.plugin_id,
                )
            if source.plugin_id != selection.plugin_id:
                raise PluginGenerationCandidateError(
                    "plugin_generation_source_mismatch",
                    plugin_id=selection.plugin_id,
                )
            sources.append(source)

        processes: list[PluginGenerationEndpoint] = []
        try:
            for source in sources:
                process = self.process_factory(
                    project_root=self.project_root,
                    site_dir=source.site_dir,
                    plugin_id=source.plugin_id,
                    work_dir=self.work_root / source.plugin_id,
                    python_executable=self.python_executable,
                    plugin_storage_data_root=self.plugin_storage_data_root,
                    plugin_storage_instance_id=self.plugin_storage_instance_id,
                )
                if source.approved_permissions is not None:
                    bind_approval = getattr(process, "bind_approved_permissions", None)
                    if not callable(bind_approval):
                        raise PluginGenerationCandidateError("plugin_permission_binding_unavailable", plugin_id=source.plugin_id)
                    bind_approval(source.approved_permissions)
                self._bind_ports(process)
                processes.append(process)

            starts = await asyncio.gather(
                *(asyncio.to_thread(getattr(process, "prepare")) for process in processes),
                return_exceptions=True,
            )
            for process, result in zip(processes, starts):
                if isinstance(result, PluginGenerationError):
                    raise PluginGenerationCandidateError(
                        result.reason,
                        plugin_id=process.plugin_id,
                        schema_errors=result.schema_errors,
                    ) from result
                if isinstance(result, BaseException):
                    raise PluginGenerationCandidateError(
                        "plugin_generation_start_failed",
                        plugin_id=process.plugin_id,
                    ) from result
                if not isinstance(result, dict) or not bool(result.get("ok")):
                    raise PluginGenerationCandidateError(
                        str(
                            result.get("reason")
                            if isinstance(result, dict)
                            else "plugin_generation_start_failed"
                        )
                        or "plugin_generation_start_failed",
                        plugin_id=process.plugin_id,
                    )
            statuses = {process.plugin_id: process.public_status_snapshot() for process in processes}
            try:
                plan = resolve_dependencies({key: value.get("contribution_snapshot", {}) for key, value in statuses.items()}, bindings,
                                            bootstrap_services=self.bootstrap_services)
            except (TypeError, ValueError, KeyError) as exc:
                raise PluginGenerationCandidateError("service_dependency_declarations_invalid") from exc
            by_id = {process.plugin_id: process for process in processes}
            if plan.bootstrap_errors:
                raise PluginGenerationCandidateError("execution_policy_dependency_unavailable")
            waiting = {key: {**statuses[key], "status": "waiting_dependency", "reason": errors[0]["reason"],
                              "dependency_errors": list(errors)} for key, errors in plan.waiting.items()}
            await self._stop_all([by_id[key] for key in waiting], require_success=True)
            for plugin_id in plan.activation_order:
                try:
                    activated = await asyncio.to_thread(by_id[plugin_id].activate)
                    if not isinstance(activated, dict) or not activated.get("ok"):
                        raise PluginGenerationCandidateError("plugin_generation_activation_failed", plugin_id=plugin_id)
                except PluginGenerationError as exc:
                    raise PluginGenerationCandidateError(exc.reason, plugin_id=plugin_id) from exc
            return PluginGenerationSnapshot(normalized, tuple(by_id[key] for key in plan.activation_order),
                waiting=waiting, service_bindings=plan.bindings)
        except PluginActiveGenerationError as exc:
            await self._stop_all(processes)
            raise PluginGenerationCandidateError(exc.reason) from exc
        except BaseException:
            await self._stop_all(processes)
            raise

    @staticmethod
    def _validate_selections(
        selections: tuple[PluginSelection, ...],
    ) -> tuple[PluginSelection, ...]:
        if not isinstance(selections, tuple):
            raise PluginGenerationCandidateError("plugin_generation_selections_invalid")
        seen: set[str] = set()
        for item in selections:
            if (
                not isinstance(item, PluginSelection)
                or not is_valid_plugin_id(item.plugin_id)
                or not isinstance(item.enabled, bool)
            ):
                raise PluginGenerationCandidateError("plugin_generation_selections_invalid")
            if item.plugin_id in seen:
                raise PluginGenerationCandidateError("duplicate_plugin_selection")
            seen.add(item.plugin_id)
        return tuple(selections)

    def _bind_ports(self, process: PluginGenerationEndpoint) -> None:
        bindings = (
            ("bind_managed_artifact_sink", self.managed_artifact_sink),
            ("bind_resource_provider", self.resource_provider),
            ("bind_capability_provider", self.capability_provider),
            ("bind_connection_provider", self.connection_provider),
            ("bind_events_provider", self.events_provider),
            ("bind_tasks_provider", self.tasks_provider),
            ("bind_notification_port", self.notification_port),
        )
        for method_name, value in bindings:
            if value is None:
                continue
            bind = getattr(process, method_name, None)
            if not callable(bind):
                raise PluginGenerationCandidateError(
                    "plugin_generation_port_unsupported",
                    plugin_id=process.plugin_id,
                )
            bind(value)

    @staticmethod
    async def _stop_all(processes: list[PluginGenerationEndpoint], *, require_success=False) -> None:
        if not processes:
            return
        results = await asyncio.gather(
            *(asyncio.to_thread(process.stop) for process in processes),
            return_exceptions=True,
        )
        if require_success:
            for process, result in zip(processes, results):
                if not isinstance(result, dict) or not result.get("ok") or process.running:
                    raise PluginGenerationCandidateError("plugin_dependency_wait_cleanup_failed", plugin_id=process.plugin_id)


__all__ = [
    "PluginGenerationCandidateBuilder",
    "PluginGenerationCandidateError",
    "PluginGenerationSourceResolver",
]
