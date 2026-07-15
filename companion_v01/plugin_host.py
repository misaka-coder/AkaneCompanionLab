"""Restart-only host for explicitly allowlisted, trusted in-process plugins."""

from __future__ import annotations

import asyncio
import inspect
import re
from copy import deepcopy
from dataclasses import dataclass, replace
from importlib import metadata as importlib_metadata
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from capcore import (
    CapabilityAdapter,
    CapabilityDescriptor,
    CapabilityResult,
    HealthStatus,
    InvocationContext,
    validate_invocation_args,
)

from .distribution_artifacts import audit_distribution_artifact
from .instance_profile import PluginSelection
from .plugin_contribution_policy import ContributionPolicyDecision, PluginContributionPolicy
from .plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    AKANE_PLUGIN_ENTRYPOINT_GROUP,
    BACKGROUND_JOB_PERMISSION,
    MANAGED_ARTIFACT_WRITE_PERMISSION,
    MAX_MANAGED_ARTIFACT_BYTES,
    NOTIFICATION_SEND_PERMISSION,
    PLUGIN_QQ_COMMAND_PERMISSION,
    PLUGIN_STORAGE_WRITE_PERMISSION,
    ManagedArtifactPayload,
    PluginManifest,
    PluginRegistrar,
    PluginResultPayload,
    is_valid_capability_id,
    is_valid_permission_id,
    is_valid_plugin_id,
)
from .plugin_jobs import _HostJobController, run_supervised_job
from .plugin_managed_artifacts import ManagedArtifactError, ManagedArtifactSink
from .plugin_notifications import _NotificationDeliveryLedger, _PluginScopedNotificationPort
from .plugin_qq_commands import PluginQQCommandBroker, _PluginCommandRegistration
from .plugin_storage import PluginStorageService
from .plugin_result_projection import sanitize_capability_result
from .plugin_result_experience import (
    PluginResultExperienceError,
    has_reserved_plugin_result_key,
    project_plugin_result_payload,
)


_SAFE_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}$")
_HOST_AVAILABLE_STATES = frozenset({"active", "degraded"})
_MAX_ADAPTERS_PER_PLUGIN = 16
_MAX_CAPABILITIES_PER_PLUGIN = 64
_MAX_QQ_COMMANDS_PER_PLUGIN = 32
_MAX_QQ_COMMAND_LENGTH = 64
_QQ_COMMAND_PATTERN = re.compile(r"^/[^\s/]{1,63}$")


@dataclass(frozen=True, slots=True)
class PluginStatus:
    plugin_id: str
    enabled: bool
    status: str
    reason: str = ""
    plugin_version: str = ""
    stage: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "plugin_id": self.plugin_id,
            "enabled": self.enabled,
            "status": self.status,
            "reason": self.reason,
        }
        if self.plugin_version:
            payload["plugin_version"] = self.plugin_version
        if self.stage:
            payload["stage"] = self.stage
        return payload


@dataclass(frozen=True, slots=True)
class _CapabilityRegistration:
    plugin_id: str
    adapter: CapabilityAdapter
    descriptor: CapabilityDescriptor
    permissions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ActivePlugin:
    plugin: Any
    adapters: tuple[CapabilityAdapter, ...]
    capability_ids: tuple[str, ...]
    job: Any  # PluginBackgroundJob | None
    qq_command_registrations: tuple[_PluginCommandRegistration, ...] = ()


class _ActivationFailure(RuntimeError):
    def __init__(self, reason: str, *, status: str = "failed", stage: str = "") -> None:
        self.reason = reason
        self.status = status
        self.stage = stage
        super().__init__(reason)


class _StagedRegistrar(PluginRegistrar):
    def __init__(self) -> None:
        self._adapters: list[CapabilityAdapter] = []
        self._sealed = False
        self._storage_dir: Path | None = None
        self._job: Any = None  # PluginBackgroundJob | None
        self._job_permission: bool = False
        self._notification_port: Any = None  # NotificationPort | None
        self._notification_permission: bool = False
        self._qq_commands: list[_PluginCommandRegistration] = []
        self._qq_command_permission: bool = False

    @property
    def adapters(self) -> tuple[CapabilityAdapter, ...]:
        return tuple(self._adapters)

    @property
    def job(self) -> Any:
        return self._job

    def add_capability_adapter(self, adapter: CapabilityAdapter) -> None:
        if self._sealed:
            raise RuntimeError("plugin_registrar_sealed")
        self._adapters.append(adapter)

    def add_background_job(self, job: Any) -> None:
        if self._sealed:
            raise RuntimeError("plugin_registrar_sealed")
        if not self._job_permission:
            raise RuntimeError("job_permission_required")
        if self._job is not None:
            raise RuntimeError("duplicate_plugin_job")
        if not callable(getattr(job, "start", None)) or not callable(getattr(job, "stop", None)):
            raise RuntimeError("invalid_plugin_job")
        self._job = job

    def get_storage_dir(self) -> Path:
        if self._sealed:
            raise RuntimeError("plugin_registrar_sealed")
        if self._storage_dir is None:
            raise RuntimeError("storage_permission_required")
        return self._storage_dir

    def get_notification_port(self) -> Any:
        if self._sealed:
            raise RuntimeError("plugin_registrar_sealed")
        if not self._notification_permission or self._notification_port is None:
            raise RuntimeError("notification_permission_required")
        return self._notification_port

    def add_qq_command(self, command: str, handler: Any) -> None:
        if self._sealed:
            raise RuntimeError("plugin_registrar_sealed")
        if not self._qq_command_permission:
            raise RuntimeError("qq_command_permission_required")
        cmd = str(command or "").strip()
        if len(cmd) > _MAX_QQ_COMMAND_LENGTH or _QQ_COMMAND_PATTERN.fullmatch(cmd) is None:
            raise RuntimeError("invalid_qq_command")
        if not callable(getattr(handler, "handle", None)):
            raise RuntimeError("invalid_qq_command_handler")
        normalized = cmd.lower()
        if any(reg.command == normalized for reg in self._qq_commands):
            raise RuntimeError("duplicate_plugin_qq_command")
        if len(self._qq_commands) >= _MAX_QQ_COMMANDS_PER_PLUGIN:
            raise RuntimeError("too_many_plugin_qq_commands")
        self._qq_commands.append(_PluginCommandRegistration(
            plugin_id="",  # will be filled in by host after seal
            command=normalized,
            handler=handler,
        ))

    @property
    def qq_commands(self) -> tuple[Any, ...]:
        return tuple(self._qq_commands)

    def _set_storage_dir(self, path: Path) -> None:
        """Called by PluginHost after manifest validation; not part of the plugin API."""
        self._storage_dir = path

    def _set_job_permission(self, allowed: bool) -> None:
        self._job_permission = allowed

    def _set_notification_port(self, port: Any) -> None:
        self._notification_port = port
        self._notification_permission = port is not None

    def _set_qq_command_permission(self, allowed: bool) -> None:
        self._qq_command_permission = allowed

    def seal(self) -> None:
        self._sealed = True


class PluginHost:
    """Own plugin discovery, activation, immutable registration, and shutdown.

    Construction only captures an immutable instance selection and callables.
    Discovery, artifact audit, imports, factories, registration, health checks,
    and capability enumeration happen exclusively in :meth:`start`.
    """

    def __init__(
        self,
        selections: tuple[PluginSelection, ...],
        *,
        contribution_policy: PluginContributionPolicy,
        entry_points_provider: Callable[[], Iterable[Any]] | None = None,
        activation_timeout_seconds: float = 5.0,
        invoke_timeout_seconds: float = 5.0,
        managed_artifact_timeout_seconds: float = 5.0,
        close_timeout_seconds: float = 2.0,
    ) -> None:
        if not isinstance(selections, tuple) or any(
            not isinstance(selection, PluginSelection) for selection in selections
        ):
            raise TypeError("plugin_selections_must_be_snapshot")
        contribution_policy_id = str(getattr(contribution_policy, "policy_id", "") or "").strip()
        if (
            not is_valid_capability_id(contribution_policy_id)
            or not callable(getattr(contribution_policy, "validate_manifest", None))
            or not callable(getattr(contribution_policy, "validate_capability", None))
        ):
            raise TypeError("invalid_plugin_contribution_policy")
        self._selections = selections
        self._contribution_policy = contribution_policy
        self._contribution_policy_id = contribution_policy_id
        self._entry_points_provider = entry_points_provider or _installed_plugin_entry_points
        self._activation_timeout_seconds = max(0.1, float(activation_timeout_seconds))
        self._invoke_timeout_seconds = max(0.1, float(invoke_timeout_seconds))
        self._managed_artifact_timeout_seconds = max(0.1, float(managed_artifact_timeout_seconds))
        self._close_timeout_seconds = max(0.1, float(close_timeout_seconds))

        self._state = "created"
        self._plugin_statuses: tuple[PluginStatus, ...] = tuple(
            PluginStatus(
                plugin_id=_public_plugin_id(selection.plugin_id),
                enabled=selection.enabled,
                status="pending" if selection.enabled else "disabled",
                reason="host_not_started" if selection.enabled else "instance_disabled",
            )
            for selection in self._selections
        )
        self._active_plugins: Mapping[str, _ActivePlugin] = MappingProxyType({})
        self._capabilities: Mapping[str, _CapabilityRegistration] = MappingProxyType({})
        self._activation_order: tuple[CapabilityAdapter, ...] = ()
        self._closed_adapters: list[CapabilityAdapter] = []
        self._close_failure_count = 0
        self._runtime_loop: asyncio.AbstractEventLoop | None = None
        self._managed_artifact_sink: ManagedArtifactSink | None = None
        self._storage_service: PluginStorageService | None = None
        self._notification_port: Any = None  # NotificationPort | None
        self._notification_ledger = _NotificationDeliveryLedger()
        self._job_tasks: dict[str, tuple[Any, _HostJobController, asyncio.Task]] = {}
        self._job_statuses: dict[str, dict[str, str]] = {}
        self._job_stop_timeout_seconds: float = 10.0
        self._job_stop_failure_count = 0

        self._lifecycle_lock = asyncio.Lock()
        self._invoke_lock = asyncio.Lock()
        self._inflight_count = 0
        self._inflight_zero = asyncio.Event()
        self._inflight_zero.set()

    @property
    def state(self) -> str:
        return self._state

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return tuple(self._capabilities)

    @property
    def capability_descriptors(self) -> Mapping[str, CapabilityDescriptor]:
        """Return an immutable point-in-time view without exposing adapters."""

        return MappingProxyType(
            {
                capability_id: _copy_descriptor_snapshot(registration.descriptor)
                for capability_id, registration in self._capabilities.items()
            }
        )

    def status_snapshot(self) -> dict[str, Any]:
        reason = ""
        if self._state == "degraded":
            reason = (
                "plugin_runtime_failed"
                if any(item.get("status") == "failed" for item in self._job_statuses.values())
                else "plugin_activation_failed"
            )
        elif self._state not in _HOST_AVAILABLE_STATES:
            reason = "host_unavailable"
        job_statuses = [
            {
                "plugin_id": _public_plugin_id(plugin_id),
                "status": item.get("status", "unknown"),
                "reason": item.get("reason", ""),
            }
            for plugin_id, item in self._job_statuses.items()
        ]
        return {
            "ok": self._state == "active",
            "status": self._state,
            "reason": reason,
            "contribution_policy": self._contribution_policy_id,
            "plugin_count": len(self._active_plugins),
            "capability_count": len(self._capabilities),
            "job_count": len(self._job_tasks),
            "running_job_count": sum(
                1 for item in self._job_statuses.values() if item.get("status") == "running"
            ),
            "job_stop_failure_count": self._job_stop_failure_count,
            "jobs": job_statuses,
            "close_failure_count": self._close_failure_count,
            "plugins": [status.as_dict() for status in self._plugin_statuses],
        }

    def bind_managed_artifact_sink(self, sink: ManagedArtifactSink) -> None:
        """Bind the host-owned artifact sink before restart-only startup."""

        if self._state != "created":
            raise RuntimeError("plugin_host_already_started")
        if not callable(getattr(sink, "materialize", None)):
            raise TypeError("invalid_managed_artifact_sink")
        self._managed_artifact_sink = sink

    def bind_plugin_storage_service(self, storage_service: PluginStorageService) -> None:
        """Bind the host-owned scoped storage service before restart-only startup.

        Must be called before :meth:`start`.  Plugins that declare
        ``storage.write`` permission may call ``registrar.get_storage_dir()``
        during registration to receive their scoped data directory.
        """
        if self._state != "created":
            raise RuntimeError("plugin_host_already_started")
        if not callable(getattr(storage_service, "get_plugin_data_dir", None)):
            raise TypeError("invalid_plugin_storage_service")
        self._storage_service = storage_service

    def bind_notification_port(self, port: Any) -> None:
        """Bind the host-owned notification port before restart-only startup.

        Must be called before :meth:`start`.  Plugins that declare
        ``notification.send`` permission receive this port via
        ``registrar.get_notification_port()`` during registration.
        """
        if self._state != "created":
            raise RuntimeError("plugin_host_already_started")
        if not callable(getattr(port, "send", None)):
            raise TypeError("invalid_notification_port")
        self._notification_port = port

    def build_qq_command_broker(self) -> PluginQQCommandBroker:
        """Build an immutable command broker from all activated plugin QQ commands.

        Call after :meth:`start`.  Registrations do not mutate at runtime, and
        the returned broker rejects dispatch once the host begins stopping.
        """
        registrations: list[_PluginCommandRegistration] = []
        for active in self._active_plugins.values():
            for reg in active.qq_command_registrations:
                registrations.append(reg)
        return PluginQQCommandBroker(
            tuple(registrations),
            availability_provider=lambda: self._state in _HOST_AVAILABLE_STATES,
        )

    async def start(self) -> dict[str, Any]:
        async with self._lifecycle_lock:
            if self._state in _HOST_AVAILABLE_STATES or self._state in {"starting", "stopping", "stopped"}:
                return self.status_snapshot()

            self._runtime_loop = asyncio.get_running_loop()
            self._state = "starting"
            working_plugins: dict[str, _ActivePlugin] = {}
            working_capabilities: dict[str, _CapabilityRegistration] = {}
            working_qq_commands: set[str] = set()
            activation_order: list[CapabilityAdapter] = []
            statuses: list[PluginStatus] = []

            enabled_ids = {selection.plugin_id for selection in self._selections if selection.enabled}
            entry_points_by_name: dict[str, list[Any]] = {}
            discovery_failed = False
            if enabled_ids:
                try:
                    discovered = tuple(self._entry_points_provider())
                    for entry_point in discovered:
                        name = str(getattr(entry_point, "name", "") or "")
                        if name in enabled_ids:
                            entry_points_by_name.setdefault(name, []).append(entry_point)
                except Exception:
                    discovery_failed = True

            for selection in self._selections:
                if not selection.enabled:
                    statuses.append(
                        PluginStatus(
                            plugin_id=_public_plugin_id(selection.plugin_id),
                            enabled=False,
                            status="disabled",
                            reason="instance_disabled",
                        )
                    )
                    continue
                if discovery_failed:
                    statuses.append(
                        PluginStatus(
                            plugin_id=_public_plugin_id(selection.plugin_id),
                            enabled=True,
                            status="unavailable",
                            reason="plugin_discovery_failed",
                        )
                    )
                    continue

                status, active, registrations = await self._activate_plugin(
                    selection,
                    entry_points_by_name.get(selection.plugin_id, []),
                    reserved_capability_ids=frozenset(working_capabilities),
                    reserved_qq_commands=frozenset(working_qq_commands),
                )
                statuses.append(status)
                if active is None:
                    continue
                working_plugins[selection.plugin_id] = active
                for registration in registrations:
                    working_capabilities[registration.descriptor.id] = registration
                working_qq_commands.update(
                    registration.command for registration in active.qq_command_registrations
                )
                activation_order.extend(active.adapters)

            self._plugin_statuses = tuple(statuses)
            self._active_plugins = MappingProxyType(dict(working_plugins))
            self._capabilities = MappingProxyType(dict(working_capabilities))
            self._activation_order = tuple(activation_order)
            # Start supervised job tasks for every successfully activated plugin with a job
            job_tasks: dict[str, tuple[Any, _HostJobController, asyncio.Task]] = {}
            self._job_statuses = {}
            for plugin_id, active_plugin in working_plugins.items():
                if active_plugin.job is None:
                    continue
                controller = _HostJobController()
                controller._arm()
                task = asyncio.create_task(run_supervised_job(active_plugin.job, controller))
                self._job_statuses[plugin_id] = {"status": "running", "reason": ""}
                task.add_done_callback(
                    lambda done, pid=plugin_id, ctl=controller: self._on_job_task_done(pid, ctl, done)
                )
                job_tasks[plugin_id] = (active_plugin.job, controller, task)
            self._job_tasks = job_tasks
            enabled_failures = any(status.enabled and status.status != "active" for status in statuses)
            async with self._invoke_lock:
                self._state = "degraded" if enabled_failures else "active"
            if job_tasks:
                # Give every job one scheduling opportunity so an immediate exit
                # is reflected in the startup snapshot instead of fake readiness.
                await asyncio.sleep(0)
                for plugin_id, (_job, controller, task) in job_tasks.items():
                    if task.done() and self._job_statuses.get(plugin_id, {}).get("status") == "running":
                        self._on_job_task_done(plugin_id, controller, task)
            return self.status_snapshot()

    async def stop(self) -> dict[str, Any]:
        async with self._lifecycle_lock:
            if self._state == "stopped":
                return self.status_snapshot()
            if self._state == "created":
                self._state = "stopped"
                return self.status_snapshot()

            async with self._invoke_lock:
                self._state = "stopping"

            try:
                await asyncio.wait_for(
                    self._inflight_zero.wait(),
                    timeout=self._invoke_timeout_seconds + self._managed_artifact_timeout_seconds + 0.5,
                )
            except (TimeoutError, asyncio.TimeoutError):
                pass

            # Stop supervised job tasks before closing capability adapters
            await self._stop_job_tasks()

            await self._close_adapters(reversed(self._activation_order))
            self._active_plugins = MappingProxyType({})
            self._capabilities = MappingProxyType({})
            self._activation_order = ()
            self._job_tasks = {}
            self._state = "stopped"
            self._runtime_loop = None
            return self.status_snapshot()

    async def invoke_from_consumer(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        *,
        context: InvocationContext,
    ) -> CapabilityResult:
        """Run an invocation on the lifecycle loop from a synchronous Engine worker.

        Plugin adapters are activated on the FastAPI lifecycle loop and may own
        loop-bound async resources. Engine tool execution is synchronous and
        normally runs in a worker thread, so executing ``invoke`` through a new
        per-thread event loop would violate that ownership boundary.
        """

        runtime_loop = self._runtime_loop
        if runtime_loop is None or runtime_loop.is_closed() or not runtime_loop.is_running():
            return CapabilityResult(
                is_error=True,
                status="host_unavailable",
                reason="host_unavailable",
            )
        current_loop = asyncio.get_running_loop()
        if current_loop is runtime_loop:
            return await self.invoke(capability_id, args, context=context)
        try:
            concurrent_future = asyncio.run_coroutine_threadsafe(
                self.invoke(capability_id, args, context=context),
                runtime_loop,
            )
        except RuntimeError:
            return CapabilityResult(
                is_error=True,
                status="host_unavailable",
                reason="host_unavailable",
            )
        try:
            return await asyncio.wait_for(
                asyncio.wrap_future(concurrent_future),
                timeout=self._invoke_timeout_seconds + self._managed_artifact_timeout_seconds + 0.5,
            )
        except asyncio.CancelledError:
            concurrent_future.cancel()
            raise
        except (TimeoutError, asyncio.TimeoutError):
            concurrent_future.cancel()
            return CapabilityResult(
                is_error=True,
                status="error",
                reason="plugin_invoke_timeout",
            )
        except Exception:
            return CapabilityResult(
                is_error=True,
                status="error",
                reason="plugin_invoke_failed",
            )

    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        *,
        context: InvocationContext,
    ) -> CapabilityResult:
        async with self._invoke_lock:
            if self._state not in _HOST_AVAILABLE_STATES:
                return CapabilityResult(
                    is_error=True,
                    status="host_unavailable",
                    reason="host_unavailable",
                )
            registration = self._capabilities.get(str(capability_id or ""))
            if registration is None:
                return CapabilityResult(
                    is_error=True,
                    status="not_found",
                    reason="unknown_capability",
                )
            if not isinstance(context, InvocationContext):
                return CapabilityResult(
                    is_error=True,
                    status="validation_error",
                    reason="invalid_invocation_context",
                )
            self._inflight_count += 1
            self._inflight_zero.clear()

        try:
            validation = validate_invocation_args(registration.descriptor, args)
            if not validation.ok:
                first_reason = validation.errors[0].code if validation.errors else "invalid_arguments"
                return CapabilityResult(
                    is_error=True,
                    status="validation_error",
                    reason=first_reason,
                    content={"errors": [error.as_dict() for error in validation.errors]},
                )
            try:
                result = await asyncio.wait_for(
                    registration.adapter.invoke(
                        registration.descriptor.id,
                        validation.normalized_args,
                        context,
                    ),
                    timeout=self._invoke_timeout_seconds,
                )
            except asyncio.CancelledError:
                if _current_task_is_cancelling():
                    raise
                return CapabilityResult(
                    is_error=True,
                    status="error",
                    reason="plugin_invoke_failed",
                )
            except (TimeoutError, asyncio.TimeoutError):
                return CapabilityResult(
                    is_error=True,
                    status="error",
                    reason="plugin_invoke_timeout",
                )
            except Exception:
                return CapabilityResult(
                    is_error=True,
                    status="error",
                    reason="plugin_invoke_failed",
                )
            if not isinstance(result, CapabilityResult):
                return CapabilityResult(
                    is_error=True,
                    status="error",
                    reason="plugin_invoke_invalid_result",
                )
            return await self._finalize_capability_result(
                result,
                registration=registration,
                context=context,
            )
        finally:
            async with self._invoke_lock:
                self._inflight_count = max(0, self._inflight_count - 1)
                if self._inflight_count == 0:
                    self._inflight_zero.set()

    async def _finalize_capability_result(
        self,
        result: CapabilityResult,
        *,
        registration: _CapabilityRegistration,
        context: InvocationContext,
    ) -> CapabilityResult:
        payload = result.content
        if not isinstance(payload, ManagedArtifactPayload):
            return _project_public_plugin_result(result, content=payload)

        if result.is_error:
            return _managed_artifact_failure("managed_artifact_on_error")
        artifact_output = _managed_artifact_output(registration.descriptor)
        if artifact_output is None:
            return _managed_artifact_failure("managed_artifact_not_declared")
        if MANAGED_ARTIFACT_WRITE_PERMISSION not in registration.permissions:
            return _managed_artifact_failure("managed_artifact_permission_required")
        if self._managed_artifact_sink is None:
            return _managed_artifact_failure("managed_artifact_sink_unavailable")

        public_result = _project_public_plugin_result(
            result,
            content=payload.content,
        )
        if public_result.is_error:
            return public_result

        data = getattr(payload.artifact, "data", None)
        declared_max_bytes = int(artifact_output.max_bytes or 0)
        if not isinstance(data, bytes) or len(data) > declared_max_bytes:
            return _managed_artifact_failure("managed_artifact_too_large")
        try:
            artifact_ref = await asyncio.wait_for(
                self._managed_artifact_sink.materialize(
                    payload.artifact,
                    context=context,
                    capability_id=registration.descriptor.id,
                ),
                timeout=self._managed_artifact_timeout_seconds,
            )
        except asyncio.CancelledError:
            if _current_task_is_cancelling():
                raise
            return _managed_artifact_failure("managed_artifact_write_failed")
        except (TimeoutError, asyncio.TimeoutError):
            return _managed_artifact_failure("managed_artifact_write_timeout")
        except ManagedArtifactError as exc:
            return _managed_artifact_failure(exc.reason)
        except Exception:
            return _managed_artifact_failure("managed_artifact_write_failed")
        normalized_artifact_ref = _normalize_managed_artifact_reference(
            artifact_ref,
            payload=payload,
            capability_id=registration.descriptor.id,
        )
        if normalized_artifact_ref is None:
            return _managed_artifact_failure("managed_artifact_invalid_reference")

        if isinstance(public_result.content, Mapping):
            combined_content: dict[str, Any] = dict(public_result.content)
        else:
            combined_content = {"result": public_result.content}
        combined_content["managed_artifacts"] = [normalized_artifact_ref]
        return sanitize_capability_result(
            CapabilityResult(
                is_error=False,
                status=public_result.status,
                reason=public_result.reason,
                content=combined_content,
            )
        )

    async def _activate_plugin(
        self,
        selection: PluginSelection,
        entry_points: list[Any],
        *,
        reserved_capability_ids: frozenset[str],
        reserved_qq_commands: frozenset[str],
    ) -> tuple[PluginStatus, _ActivePlugin | None, tuple[_CapabilityRegistration, ...]]:
        registrar = _StagedRegistrar()
        artifact_version = ""
        try:
            if not is_valid_plugin_id(selection.plugin_id):
                raise _ActivationFailure("invalid_plugin_id")
            if not entry_points:
                raise _ActivationFailure("plugin_not_installed", status="unavailable")
            if len(entry_points) != 1:
                raise _ActivationFailure("duplicate_plugin_artifact")
            entry_point = entry_points[0]
            if str(getattr(entry_point, "name", "") or "") != selection.plugin_id:
                raise _ActivationFailure("entry_point_name_mismatch")

            try:
                distribution = entry_point.dist
            except Exception:
                raise _ActivationFailure("distribution_metadata_unavailable") from None
            artifact = audit_distribution_artifact(distribution)
            if not artifact.ok:
                raise _ActivationFailure(artifact.reason)
            artifact_version = artifact.version

            try:
                factory = entry_point.load()
            except Exception:
                raise _ActivationFailure("plugin_load_failed") from None
            _validate_zero_parameter_factory(factory)
            try:
                plugin = factory()
            except Exception:
                raise _ActivationFailure("plugin_factory_failed") from None

            manifest = getattr(plugin, "manifest", None)
            _validate_manifest(
                manifest,
                expected_plugin_id=selection.plugin_id,
                artifact_version=artifact_version,
            )
            _require_policy_acceptance(
                lambda: self._contribution_policy.validate_manifest(manifest),
                stage="manifest_contributions",
            )
            # If the plugin declares storage.write, resolve its scoped data dir
            # and inject it into the registrar BEFORE register() is called, so
            # the plugin can capture the path to initialise adapters with it.
            if PLUGIN_STORAGE_WRITE_PERMISSION in manifest.permissions:
                if self._storage_service is None:
                    raise _ActivationFailure("storage_service_unavailable")
                try:
                    plugin_data_dir = self._storage_service.get_plugin_data_dir(
                        selection.plugin_id
                    )
                except (ValueError, OSError):
                    raise _ActivationFailure("storage_dir_creation_failed") from None
                registrar._set_storage_dir(plugin_data_dir)
            # Inject job permission flag if declared
            if BACKGROUND_JOB_PERMISSION in manifest.permissions:
                registrar._set_job_permission(True)
            # Inject notification port if declared and bound
            if NOTIFICATION_SEND_PERMISSION in manifest.permissions:
                if self._notification_port is None:
                    raise _ActivationFailure("notification_port_unavailable")
                registrar._set_notification_port(
                    _PluginScopedNotificationPort(
                        plugin_id=selection.plugin_id,
                        delegate=self._notification_port,
                        ledger=self._notification_ledger,
                        availability_provider=lambda: self._state in _HOST_AVAILABLE_STATES,
                    )
                )
            # Inject QQ command permission flag if declared
            if PLUGIN_QQ_COMMAND_PERMISSION in manifest.permissions:
                registrar._set_qq_command_permission(True)
            register = getattr(plugin, "register", None)
            if not callable(register):
                raise _ActivationFailure("invalid_plugin_contract")
            try:
                register_result = register(registrar)
            except Exception:
                raise _ActivationFailure("plugin_registration_failed") from None
            registrar.seal()
            if inspect.isawaitable(register_result):
                close_awaitable = getattr(register_result, "close", None)
                cancel_awaitable = getattr(register_result, "cancel", None)
                if callable(close_awaitable):
                    close_awaitable()
                elif callable(cancel_awaitable):
                    cancel_awaitable()
                raise _ActivationFailure("invalid_plugin_registration_result")
            if register_result is not None:
                raise _ActivationFailure("invalid_plugin_registration_result")
            if any(reg.command in reserved_qq_commands for reg in registrar.qq_commands):
                raise _ActivationFailure("qq_command_conflict")
            if not registrar.adapters:
                raise _ActivationFailure("plugin_registered_no_adapters")
            if len(registrar.adapters) > _MAX_ADAPTERS_PER_PLUGIN:
                raise _ActivationFailure("too_many_plugin_adapters")
            if len({id(adapter) for adapter in registrar.adapters}) != len(registrar.adapters):
                raise _ActivationFailure("duplicate_plugin_adapter")

            registrations: list[_CapabilityRegistration] = []
            local_capability_ids: set[str] = set()
            for adapter in registrar.adapters:
                _validate_adapter_contract(adapter)
                health = await _bounded_adapter_call(
                    adapter.health(),
                    timeout_seconds=self._activation_timeout_seconds,
                    failure_reason="plugin_health_failed",
                )
                if not isinstance(health, HealthStatus):
                    raise _ActivationFailure("invalid_plugin_health_result")
                if not health.ok:
                    raise _ActivationFailure("plugin_health_unavailable")
                descriptors = await _bounded_adapter_call(
                    adapter.list_capabilities(),
                    timeout_seconds=self._activation_timeout_seconds,
                    failure_reason="plugin_capability_enumeration_failed",
                )
                if not isinstance(descriptors, tuple):
                    raise _ActivationFailure("invalid_capability_enumeration_result")
                for descriptor in descriptors:
                    _validate_descriptor_contract(descriptor, plugin_id=selection.plugin_id)
                    _validate_managed_artifact_descriptor_contract(descriptor, manifest=manifest)
                    _require_policy_acceptance(
                        lambda: self._contribution_policy.validate_capability(
                            plugin_id=selection.plugin_id,
                            descriptor=descriptor,
                        ),
                        stage="capability_contributions",
                    )
                    descriptor_snapshot = _copy_descriptor_snapshot(descriptor)
                    capability_id = descriptor_snapshot.id
                    if capability_id in local_capability_ids:
                        raise _ActivationFailure("duplicate_plugin_capability")
                    if capability_id in reserved_capability_ids:
                        raise _ActivationFailure("capability_id_conflict")
                    local_capability_ids.add(capability_id)
                    registrations.append(
                        _CapabilityRegistration(
                            plugin_id=selection.plugin_id,
                            adapter=adapter,
                            descriptor=descriptor_snapshot,
                            permissions=manifest.permissions,
                        )
                    )
                    if len(registrations) > _MAX_CAPABILITIES_PER_PLUGIN:
                        raise _ActivationFailure("too_many_plugin_capabilities")
            if not registrations:
                raise _ActivationFailure("plugin_registered_no_capabilities")

            active = _ActivePlugin(
                plugin=plugin,
                adapters=registrar.adapters,
                capability_ids=tuple(registration.descriptor.id for registration in registrations),
                job=registrar.job,
                qq_command_registrations=tuple(
                    _PluginCommandRegistration(
                        plugin_id=selection.plugin_id,
                        command=reg.command,
                        handler=reg.handler,
                    )
                    for reg in registrar.qq_commands
                ),
            )
            return (
                PluginStatus(
                    plugin_id=_public_plugin_id(selection.plugin_id),
                    enabled=True,
                    status="active",
                    plugin_version=manifest.plugin_version,
                ),
                active,
                tuple(registrations),
            )
        except _ActivationFailure as exc:
            registrar.seal()
            await self._close_adapters(reversed(registrar.adapters))
            return (
                PluginStatus(
                    plugin_id=_public_plugin_id(selection.plugin_id),
                    enabled=True,
                    status=exc.status,
                    reason=exc.reason,
                    plugin_version=artifact_version if _is_safe_version(artifact_version) else "",
                    stage=exc.stage,
                ),
                None,
                (),
            )
        except Exception:
            registrar.seal()
            await self._close_adapters(reversed(registrar.adapters))
            return (
                PluginStatus(
                    plugin_id=_public_plugin_id(selection.plugin_id),
                    enabled=True,
                    status="failed",
                    reason="plugin_activation_failed",
                ),
                None,
                (),
            )

    def _on_job_task_done(
        self,
        plugin_id: str,
        controller: _HostJobController,
        task: asyncio.Task,
    ) -> None:
        """Record a background job outcome without exposing its exception text."""

        if controller.shutdown_requested or self._state in {"stopping", "stopped"}:
            try:
                task.exception()
            except (asyncio.CancelledError, Exception):
                pass
            self._job_statuses[plugin_id] = {"status": "stopped", "reason": ""}
            return

        if task.cancelled():
            reason = "job_cancelled"
        else:
            try:
                exception = task.exception()
            except asyncio.CancelledError:
                exception = None
                reason = "job_cancelled"
            else:
                reason = "job_failed" if exception is not None else "job_exited"
        self._job_statuses[plugin_id] = {"status": "failed", "reason": reason}
        if self._state in _HOST_AVAILABLE_STATES:
            self._state = "degraded"

    async def _stop_job_tasks(self) -> None:
        """Signal all supervised job tasks to stop and await their completion."""
        if not self._job_tasks:
            return
        # Signal shutdown on all controllers
        for _job, controller, _task in self._job_tasks.values():
            controller.signal_shutdown()
        # Secondary stop signal
        for _job, controller, _task in reversed(tuple(self._job_tasks.values())):
            try:
                await asyncio.wait_for(_job.stop(), timeout=self._close_timeout_seconds)
            except asyncio.CancelledError:
                if _current_task_is_cancelling():
                    raise
                self._job_stop_failure_count += 1
            except Exception:
                self._job_stop_failure_count += 1
        # Await all tasks with bounded timeout
        active_tasks = [task for _, _, task in self._job_tasks.values() if not task.done()]
        if active_tasks:
            _done, pending = await asyncio.wait(
                active_tasks,
                timeout=self._job_stop_timeout_seconds,
            )
            if pending:
                self._job_stop_failure_count += len(pending)
                for task in pending:
                    task.cancel()
                await asyncio.wait(pending, timeout=self._close_timeout_seconds)

    async def _close_adapters(self, adapters: Iterable[CapabilityAdapter]) -> None:
        for adapter in adapters:
            if any(closed is adapter for closed in self._closed_adapters):
                continue
            self._closed_adapters.append(adapter)
            try:
                await asyncio.wait_for(
                    adapter.aclose(),
                    timeout=self._close_timeout_seconds,
                )
            except asyncio.CancelledError:
                if _current_task_is_cancelling():
                    raise
                self._close_failure_count += 1
                continue
            except Exception:
                self._close_failure_count += 1
                continue


def _installed_plugin_entry_points() -> tuple[Any, ...]:
    discovered = importlib_metadata.entry_points()
    if hasattr(discovered, "select"):
        return tuple(discovered.select(group=AKANE_PLUGIN_ENTRYPOINT_GROUP))
    return tuple(discovered.get(AKANE_PLUGIN_ENTRYPOINT_GROUP, ()))


def _validate_zero_parameter_factory(factory: Any) -> None:
    if not callable(factory):
        raise _ActivationFailure("invalid_plugin_factory")
    try:
        parameters = inspect.signature(factory).parameters
    except (TypeError, ValueError):
        raise _ActivationFailure("invalid_plugin_factory") from None
    if parameters:
        raise _ActivationFailure("plugin_factory_must_be_zero_parameter")


def _validate_manifest(manifest: Any, *, expected_plugin_id: str, artifact_version: str) -> None:
    if not isinstance(manifest, PluginManifest):
        raise _ActivationFailure("invalid_plugin_manifest")
    if manifest.plugin_id != expected_plugin_id:
        raise _ActivationFailure("plugin_id_mismatch")
    if not is_valid_plugin_id(manifest.plugin_id):
        raise _ActivationFailure("invalid_plugin_id")
    if manifest.plugin_api_version != AKANE_PLUGIN_API_VERSION:
        raise _ActivationFailure("plugin_api_version_mismatch")
    if not _is_safe_version(manifest.plugin_version):
        raise _ActivationFailure("invalid_plugin_version")
    if manifest.plugin_version != artifact_version:
        raise _ActivationFailure("plugin_version_mismatch")
    if (
        not isinstance(manifest.permissions, tuple)
        or len(manifest.permissions) > 32
        or len(set(manifest.permissions)) != len(manifest.permissions)
        or any(not is_valid_permission_id(permission) for permission in manifest.permissions)
    ):
        raise _ActivationFailure("invalid_plugin_manifest")


def _validate_adapter_contract(adapter: Any) -> None:
    if not str(getattr(adapter, "provider_id", "") or "").strip():
        raise _ActivationFailure("invalid_capability_adapter")
    for method_name in ("health", "list_capabilities", "invoke", "aclose"):
        if not callable(getattr(adapter, method_name, None)):
            raise _ActivationFailure("invalid_capability_adapter")


def _validate_descriptor_contract(descriptor: Any, *, plugin_id: str) -> None:
    if not isinstance(descriptor, CapabilityDescriptor):
        raise _ActivationFailure("invalid_capability_descriptor")
    if not is_valid_capability_id(descriptor.id):
        raise _ActivationFailure("invalid_capability_id")
    if not descriptor.id.startswith(f"{plugin_id}."):
        raise _ActivationFailure("capability_prefix_mismatch")


def _validate_managed_artifact_descriptor_contract(
    descriptor: CapabilityDescriptor,
    *,
    manifest: PluginManifest,
) -> None:
    artifact_outputs = tuple(
        output for output in descriptor.outputs if output.delivery == "generated_file"
    )
    if not artifact_outputs:
        return
    if len(artifact_outputs) != 1:
        raise _ActivationFailure("managed_artifact_output_count_invalid")
    if MANAGED_ARTIFACT_WRITE_PERMISSION not in manifest.permissions:
        raise _ActivationFailure("managed_artifact_permission_required")
    output = artifact_outputs[0]
    if output.kind != "file" or not output.required:
        raise _ActivationFailure("managed_artifact_output_invalid")
    if (
        isinstance(output.max_bytes, bool)
        or not isinstance(output.max_bytes, int)
        or output.max_bytes <= 0
        or output.max_bytes > MAX_MANAGED_ARTIFACT_BYTES
    ):
        raise _ActivationFailure("managed_artifact_size_limit_invalid")
    if "filesystem" not in descriptor.effects:
        raise _ActivationFailure("managed_artifact_effect_required")


def _managed_artifact_output(descriptor: CapabilityDescriptor) -> Any | None:
    outputs = tuple(output for output in descriptor.outputs if output.delivery == "generated_file")
    return outputs[0] if len(outputs) == 1 else None


def _managed_artifact_failure(reason: str) -> CapabilityResult:
    return CapabilityResult(
        is_error=True,
        status="error",
        reason=str(reason or "managed_artifact_failed"),
    )


def _project_public_plugin_result(
    result: CapabilityResult,
    *,
    content: Any,
) -> CapabilityResult:
    if isinstance(content, PluginResultPayload):
        if result.is_error:
            return _managed_artifact_failure("plugin_result_experience_on_error")
        try:
            projected_content = project_plugin_result_payload(content)
        except PluginResultExperienceError as exc:
            return _managed_artifact_failure(exc.reason)
    else:
        if has_reserved_plugin_result_key(content):
            return _managed_artifact_failure("plugin_result_reserved_key")
        projected_content = content
    return sanitize_capability_result(
        CapabilityResult(
            is_error=result.is_error,
            status=result.status,
            reason=result.reason,
            content=projected_content,
        )
    )


def _normalize_managed_artifact_reference(
    value: Any,
    *,
    payload: ManagedArtifactPayload,
    capability_id: str,
) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    generated_id = str(value.get("generated_id") or "").strip()
    generated_handle = str(value.get("generated_handle") or "").strip()
    output_title = str(value.get("output_title") or "").strip()
    output_format = str(value.get("output_format") or "").strip().lower().lstrip(".")
    mime_type = str(value.get("mime_type") or "").strip().lower()
    created_by_tool = str(value.get("created_by_tool") or "").strip()
    file_size = value.get("file_size")
    send_to_user = value.get("send_to_user")
    draft = payload.artifact
    if (
        not generated_id.startswith("generated::")
        or len(generated_id) > 128
        or not generated_handle
        or len(generated_handle) > 64
        or output_title != str(draft.title or "").strip()
        or output_format != str(draft.output_format or "").strip().lower().lstrip(".")
        or mime_type != str(draft.mime_type or "").strip().lower()
        or created_by_tool != capability_id
        or isinstance(file_size, bool)
        or not isinstance(file_size, int)
        or file_size != len(draft.data)
        or not isinstance(send_to_user, bool)
        or send_to_user is not draft.send_to_user
    ):
        return None
    return {
        "generated_id": generated_id,
        "generated_handle": generated_handle,
        "output_title": output_title,
        "output_format": output_format,
        "mime_type": mime_type,
        "file_size": file_size,
        "created_by_tool": created_by_tool,
        "send_to_user": send_to_user,
    }


def _copy_descriptor_snapshot(descriptor: CapabilityDescriptor) -> CapabilityDescriptor:
    try:
        copied_trigger = (
            replace(descriptor.trigger, raw=_copy_descriptor_value(descriptor.trigger.raw))
            if descriptor.trigger is not None
            else None
        )
        copied_inputs = tuple(replace(slot, raw=_copy_descriptor_value(slot.raw)) for slot in descriptor.inputs)
        copied_outputs = tuple(replace(slot, raw=_copy_descriptor_value(slot.raw)) for slot in descriptor.outputs)
        copied = replace(
            descriptor,
            trigger=copied_trigger,
            inputs=copied_inputs,
            outputs=copied_outputs,
            raw=_copy_descriptor_value(descriptor.raw),
        )
    except Exception:
        raise _ActivationFailure("invalid_capability_descriptor_snapshot") from None
    if not isinstance(copied, CapabilityDescriptor):
        raise _ActivationFailure("invalid_capability_descriptor_snapshot")
    return copied


def _copy_descriptor_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {deepcopy(raw_key): _copy_descriptor_value(raw_value) for raw_key, raw_value in value.items()}
    if isinstance(value, list):
        return [_copy_descriptor_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_copy_descriptor_value(item) for item in value)
    return deepcopy(value)


def _require_policy_acceptance(evaluate: Callable[[], Any], *, stage: str) -> None:
    try:
        decision = evaluate()
    except Exception:
        raise _ActivationFailure("contribution_policy_failed", stage=stage) from None
    if not isinstance(decision, ContributionPolicyDecision):
        raise _ActivationFailure("contribution_policy_failed", stage=stage)
    if not decision.accepted:
        raise _ActivationFailure("contribution_policy_rejected", stage=stage)


async def _bounded_adapter_call(awaitable: Any, *, timeout_seconds: float, failure_reason: str) -> Any:
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout_seconds)
    except asyncio.CancelledError:
        if _current_task_is_cancelling():
            raise
        raise _ActivationFailure(failure_reason) from None
    except (TimeoutError, asyncio.TimeoutError):
        raise _ActivationFailure(failure_reason) from None
    except Exception:
        raise _ActivationFailure(failure_reason) from None


def _is_safe_version(version: Any) -> bool:
    return isinstance(version, str) and _SAFE_VERSION_PATTERN.fullmatch(version) is not None


def _public_plugin_id(plugin_id: Any) -> str:
    return plugin_id if is_valid_plugin_id(plugin_id) else "invalid-plugin-id"


def _current_task_is_cancelling() -> bool:
    task = asyncio.current_task()
    return bool(task is not None and task.cancelling())


__all__ = ["PluginHost", "PluginStatus"]
