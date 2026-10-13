"""Own one exact child process and its line-delimited JSON control lane."""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from capcore import CapabilityDescriptor
from akane_plugin.events import EventSubscription
from akane_plugin.connections import ConnectionSpec
from ..plugin_api import PluginQQCommandResult, is_valid_plugin_id, is_valid_permission_id
from ..plugin_generation_callbacks import GenerationHostCallbackRouter
from ..plugin_generation_codec import capability_descriptor_from_wire, capability_descriptor_to_wire, json_snapshot
from ..plugin_generation_protocol import PLUGIN_GENERATION_START_TIMEOUT_SECONDS, PLUGIN_GENERATION_STOP_TIMEOUT_SECONDS
from ..plugin_generation_skills import generation_skill_export_dir
from ..plugin_managed_artifacts import ManagedArtifactSink
from ..plugin_qq_commands import PluginQQCommandBroker
from ..skill_runtime import ContributedSkillRoot
from .lifecycle import GenerationLifecycleMixin
from .transport import GenerationTransportMixin
from .invocation import GenerationInvocationMixin
from .dispatch import GenerationDispatchMixin


class PluginGenerationQQCommandBroker:
    """Compose host commands with one isolated plugin generation."""

    def __init__(
        self,
        generation: "PluginGenerationProcess",
        *,
        host_registrations: tuple = (),
    ) -> None:
        self._generation = generation
        self._host_broker = PluginQQCommandBroker(
            (),
            host_registrations=tuple(host_registrations or ()),
        )

    @property
    def registered_commands(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (
                    *self._host_broker.registered_commands,
                    *self._generation.registered_qq_commands,
                )
            )
        )

    def handles(self, command: str) -> bool:
        return self._host_broker.handles(command) or self._generation.handles_qq_command(
            command
        )

    async def dispatch(self, **command_args: Any) -> PluginQQCommandResult:
        command = str(command_args.get("command") or "")
        if self._host_broker.handles(command):
            return await self._host_broker.dispatch(**command_args)
        if not self._generation.handles_qq_command(command):
            return PluginQQCommandResult(
                handled=False,
                reason="no_matching_command",
            )
        return await self._generation.dispatch_qq_command(**command_args)


class PluginGenerationProcess(GenerationLifecycleMixin, GenerationTransportMixin, GenerationInvocationMixin, GenerationDispatchMixin):
    """Own one exact child process and its line-delimited JSON control lane."""

    def __init__(
        self,
        *,
        project_root: Path,
        site_dir: Path,
        plugin_id: str,
        work_dir: Path,
        python_executable: str = sys.executable,
        start_timeout_seconds: float = PLUGIN_GENERATION_START_TIMEOUT_SECONDS,
        stop_timeout_seconds: float | None = PLUGIN_GENERATION_STOP_TIMEOUT_SECONDS,
        managed_artifact_timeout_seconds: float = 0.0,
        plugin_storage_data_root: Path | None = None,
        plugin_storage_instance_id: str = "",
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.site_dir = Path(site_dir).resolve()
        self.plugin_id = str(plugin_id or "").strip()
        self.work_dir = Path(work_dir).resolve()
        self.python_executable = str(python_executable or sys.executable)
        self.start_timeout_seconds = max(0.1, float(start_timeout_seconds))
        self.stop_timeout_seconds = (
            None
            if stop_timeout_seconds is None
            else max(0.1, float(stop_timeout_seconds))
        )
        self.managed_artifact_timeout_seconds = max(
            0.0,
            float(managed_artifact_timeout_seconds),
        )
        storage_instance_id = str(plugin_storage_instance_id or "").strip()
        if (plugin_storage_data_root is None) != (not storage_instance_id):
            raise ValueError("plugin_generation_storage_scope_incomplete")
        if storage_instance_id and not is_valid_plugin_id(storage_instance_id):
            raise ValueError("plugin_generation_storage_scope_invalid")
        self.plugin_storage_data_root = (
            Path(plugin_storage_data_root).resolve()
            if plugin_storage_data_root is not None
            else None
        )
        self.plugin_storage_instance_id = storage_instance_id
        self.generation_id = uuid.uuid4().hex
        self.implementation_revision = ""
        self._artifact_outbox_dir = self.work_dir / "outbox" / self.generation_id
        self._skill_export_dir = generation_skill_export_dir(
            self.work_dir,
            self.generation_id,
        )
        self._process: subprocess.Popen[str] | None = None
        self._ready: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._pending: dict[str, queue.Queue[dict[str, Any] | None]] = {}
        self._pending_lock = threading.Lock()
        self._active_invocation_condition = threading.Condition()
        self._active_invocations = 0
        self._reader: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._stop_lock = threading.Lock()
        self._capability_descriptors: Mapping[str, CapabilityDescriptor] = MappingProxyType({})
        self._event_types: tuple[str, ...] = ()
        self._event_subscriptions: tuple[EventSubscription, ...] = ()
        self._hook_types: tuple[str, ...] = ()
        self._qq_commands: tuple[str, ...] = ()
        self._background_service_ids: tuple[str, ...] = ()
        self._stable_prompt_blocks: tuple[str, ...] = ()
        self._skill_roots: tuple[ContributedSkillRoot, ...] = ()
        self._plugin_version = ""
        self._permissions: tuple[str, ...] = ()
        self._connection_specs: tuple[ConnectionSpec, ...] = ()
        self._approved_permissions: tuple[str, ...] | None = None
        self._contribution_snapshot: Mapping[str, Any] = MappingProxyType({})
        self._managed_artifact_sink: ManagedArtifactSink | None = None
        self._callback_router = GenerationHostCallbackRouter(
            generation_id=self.generation_id,
            start_timeout_seconds=self.start_timeout_seconds,
            write_response=self._write_callback_response,
        )
        self._stopping = False
        self._closed = False
        self._prepared = False

    @property
    def running(self) -> bool:
        process = self._process
        return bool(process is not None and process.poll() is None and not self._closed)

    @property
    def capability_descriptors(self) -> Mapping[str, CapabilityDescriptor]:
        """Return the immutable public Capability snapshot published at ready."""

        return MappingProxyType({
            key: capability_descriptor_from_wire(capability_descriptor_to_wire(descriptor))
            for key, descriptor in self._capability_descriptors.items()
        })

    @property
    def registered_event_types(self) -> tuple[str, ...]:
        """Return the exact event subscriptions published by this generation."""

        return self._event_types

    @property
    def registered_hook_types(self) -> tuple[str, ...]:
        """Return the exact lifecycle Hook subscriptions published at ready."""

        return self._hook_types

    @property
    def event_subscriptions(self) -> tuple[EventSubscription, ...]:
        return self._event_subscriptions

    @property
    def registered_background_service_ids(self) -> tuple[str, ...]:
        """Return the exact supervised services published at ready."""

        return self._background_service_ids

    @property
    def registered_qq_commands(self) -> tuple[str, ...]:
        """Return the exact QQ command tokens published at ready."""

        return self._qq_commands

    def public_status_snapshot(self) -> dict[str, Any]:
        """Return path-free ready metadata without a health request."""

        return {
            "plugin_id": self.plugin_id,
            "enabled": True,
            "status": ("prepared" if self._prepared else "active") if self.running else "unavailable",
            "reason": "" if self.running else "plugin_generation_unavailable",
            **({"plugin_version": self._plugin_version} if self._plugin_version else {}),
            **({"permissions": list(self._permissions)} if self._permissions else {}),
            "contribution_snapshot": json_snapshot(dict(self._contribution_snapshot)),
        }

    def stable_system_prompt_blocks(self) -> tuple[str, ...]:
        """Return the generation's immutable restart-only prompt snapshot."""

        return self._stable_prompt_blocks

    def skill_roots(self) -> tuple[ContributedSkillRoot, ...]:
        """Return verified Skill mounts while this generation is available."""

        return self._skill_roots if self.running else ()

    def handles_qq_command(self, command: str) -> bool:
        return str(command or "").strip().lower() in self._qq_commands

    def build_qq_command_broker(
        self,
        host_registrations: tuple = (),
    ) -> PluginGenerationQQCommandBroker:
        return PluginGenerationQQCommandBroker(
            self,
            host_registrations=host_registrations,
        )

    def observes(self, event_type: str) -> bool:
        normalized = str(event_type or "").strip().lower()
        return normalized in self._event_types or normalized in self._hook_types

    def bind_managed_artifact_sink(self, sink: ManagedArtifactSink) -> None:
        if self._process is not None:
            raise RuntimeError("plugin_generation_already_started")
        if not callable(getattr(sink, "materialize", None)):
            raise TypeError("invalid_managed_artifact_sink")
        self._managed_artifact_sink = sink

    def bind_notification_port(self, port: Any) -> None:
        if self._process is not None:
            raise RuntimeError("plugin_generation_already_started")
        self._callback_router.bind_notification_port(port)

    def bind_resource_provider(self, provider: Any) -> None:
        if self._process is not None:
            raise RuntimeError("plugin_generation_already_started")
        self._callback_router.bind_resource_provider(provider)

    def bind_events_provider(self, provider: Any) -> None:
        if self._process is not None:
            raise RuntimeError("plugin_generation_already_started")
        self._callback_router.bind_events_provider(provider)

    def bind_tasks_provider(self, provider: Any) -> None:
        if self._process is not None:
            raise RuntimeError("plugin_generation_already_started")
        self._callback_router.bind_tasks_provider(provider)

    def bind_capability_provider(self, provider: Any) -> None:
        if self._process is not None:
            raise RuntimeError("plugin_generation_already_started")
        self._callback_router.bind_capability_provider(provider)

    def bind_connection_provider(self, provider: Any) -> None:
        if self._process is not None:
            raise RuntimeError("plugin_generation_already_started")
        self._callback_router.bind_connection_provider(provider)

    def bind_approved_permissions(self, permissions: tuple[str, ...]) -> None:
        if self._process is not None:
            raise RuntimeError("plugin_generation_already_started")
        if not isinstance(permissions, tuple) or any(not is_valid_permission_id(p) for p in permissions):
            raise ValueError("plugin_approved_permissions_invalid")
        self._approved_permissions = tuple(sorted(set(permissions)))
