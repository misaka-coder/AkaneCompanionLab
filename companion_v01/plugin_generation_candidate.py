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


class PluginGenerationSourceResolver(Protocol):
    def resolve_generation_source(self, plugin_id: str) -> PluginGenerationSource: ...


class PluginGenerationCandidateError(RuntimeError):
    """A full candidate could not be built; no partial candidate is returned."""

    def __init__(self, reason: str, *, plugin_id: str = "") -> None:
        self.reason = str(reason or "plugin_generation_candidate_failed")
        self.plugin_id = str(plugin_id or "")
        super().__init__(self.reason)


class PluginGenerationCandidateBuilder:
    """Resolve and start exactly one process for every enabled selection."""

    def __init__(
        self,
        *,
        source_resolver: PluginGenerationSourceResolver,
        project_root: Path,
        work_root: Path,
        python_executable: str = sys.executable,
        process_factory: Callable[..., PluginGenerationEndpoint] = PluginGenerationProcess,
        managed_artifact_sink: Any = None,
        notification_port: Any = None,
        reasoning_port: Any = None,
        plugin_storage_data_root: Path | None = None,
        plugin_storage_instance_id: str = "",
    ) -> None:
        if not callable(getattr(source_resolver, "resolve_generation_source", None)):
            raise TypeError("invalid_plugin_generation_source_resolver")
        self.source_resolver = source_resolver
        self.project_root = Path(project_root).resolve()
        self.work_root = Path(work_root).resolve()
        self.python_executable = str(python_executable or sys.executable)
        self.process_factory = process_factory
        self.managed_artifact_sink = managed_artifact_sink
        self.notification_port = notification_port
        self.reasoning_port = reasoning_port
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
                self._bind_ports(process)
                processes.append(process)

            starts = await asyncio.gather(
                *(asyncio.to_thread(getattr(process, "start")) for process in processes),
                return_exceptions=True,
            )
            for process, result in zip(processes, starts):
                if isinstance(result, PluginGenerationError):
                    raise PluginGenerationCandidateError(
                        result.reason,
                        plugin_id=process.plugin_id,
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
            return PluginGenerationSnapshot(normalized, tuple(processes))
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
            ("bind_notification_port", self.notification_port),
            ("bind_reasoning_port", self.reasoning_port),
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
    async def _stop_all(processes: list[PluginGenerationEndpoint]) -> None:
        if not processes:
            return
        await asyncio.gather(
            *(asyncio.to_thread(process.stop) for process in processes),
            return_exceptions=True,
        )


__all__ = [
    "PluginGenerationCandidateBuilder",
    "PluginGenerationCandidateError",
    "PluginGenerationSourceResolver",
]
