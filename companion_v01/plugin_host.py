"""Lifecycle host for explicitly allowlisted, trusted in-process plugins."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
import threading
import unicodedata
from copy import deepcopy
from dataclasses import dataclass, field, replace
from importlib import metadata as importlib_metadata
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from capcore import (
    CapabilityAdapter,
    CapabilityDescriptor,
    CapabilityToolSpec,
    CapabilityResult,
    HealthStatus,
    InvocationContext,
    SchemaDefinitionError,
    build_tool_spec,
    validate_tool_spec_args,
    validate_tool_spec_result,
)
from akane_plugin.events import Event, EventSubscription
from akane_plugin.service_contracts import service_method_info, service_catalog, validate_service_dependencies
from akane_plugin.connections import ConnectionSpec, validate_connection_specs

from .distribution_artifacts import audit_distribution_artifact
from .instance_profile import PluginSelection
from .plugin_contribution_policy import ContributionPolicyDecision, PluginContributionPolicy
from .plugin_api import (
    AKANE_PLUGIN_API_VERSION,
    AKANE_PLUGIN_ENTRYPOINT_GROUP,
    BACKGROUND_JOB_PERMISSION,
    EVENT_SUBSCRIBE_PERMISSION,
    EVENT_EMIT_PERMISSION,
    CONTEXT_OBSERVE_PERMISSION,
    AGENT_TURN_REQUEST_PERMISSION,
    HOOK_SUBSCRIBE_PERMISSION,
    MANAGED_ARTIFACT_WRITE_PERMISSION,
    RESOURCE_READ_PERMISSION,
    CAPABILITY_INVOKE_PERMISSION,
    NOTIFICATION_SEND_PERMISSION,
    PLUGIN_QQ_COMMAND_PERMISSION,
    PLUGIN_STORAGE_WRITE_PERMISSION,
    SKILL_CONTRIBUTION_PERMISSION,
    SYSTEM_PROMPT_CONTRIBUTION_PERMISSION,
    ManagedArtifactPayload,
    PluginManifest,
    PluginRegistrar,
    PluginResultPayload,
    is_valid_capability_id,
    is_valid_permission_id,
    is_valid_plugin_id,
)
from .plugin_jobs import _HostJobController, run_supervised_job
from .plugin_hooks import (
    DEFAULT_HOOK_HANDLER_TIMEOUT_SECONDS,
    PluginHookBroker,
    SUPPORTED_HOOK_TYPES,
    _PluginHookRegistration,
)
from .plugin_events import (
    DEFAULT_EVENT_HANDLER_TIMEOUT_SECONDS,
    PluginEventBroker,
    _PluginEventRegistration,
    validate_event_subscription,
)
from .plugin_event_ports import ScopedPluginEventsPort
from .plugin_task_ports import ScopedPluginTasksPort
from .plugin_managed_artifacts import (
    ManagedArtifactError,
    ManagedArtifactSink,
    normalize_managed_artifact_reference,
    validate_managed_artifact_draft,
)
from .plugin_notifications import _NotificationDeliveryLedger, _PluginScopedNotificationPort
from .plugin_resources import ResourceInvocation, ScopedPluginResourcePort, current_resource_invocation
from .plugin_capability_calls import ScopedPluginCapabilityPort
from .plugin_connections import ScopedPluginConnectionPort, permitted_connections
from .plugin_qq_commands import PluginQQCommandBroker, _PluginCommandRegistration
from .plugin_storage import PluginStorageService
from .plugin_result_projection import sanitize_capability_result
from .plugin_result_experience import (
    PluginResultExperienceError,
    has_reserved_plugin_result_key,
    project_plugin_result_payload,
)
from .skill_runtime import ContributedSkillRoot, SkillError, validate_contributed_skill_root


_SAFE_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}$")
_HOST_AVAILABLE_STATES = frozenset({"active", "degraded"})
_MAX_ADAPTERS_PER_PLUGIN = 16
_MAX_CAPABILITIES_PER_PLUGIN = 64
_MAX_QQ_COMMANDS_PER_PLUGIN = 32
_MAX_QQ_COMMAND_LENGTH = 64
_QQ_COMMAND_PATTERN = re.compile(r"^/[^\s/]{1,63}$")
_MAX_PROMPT_BLOCKS_PER_PLUGIN = 8
_MAX_PROMPT_BLOCK_CHARS = 16_000
_MAX_PROMPT_BLOCK_TOTAL_CHARS = 32_000
_MAX_PERMISSIONS_PER_PLUGIN = 32
_PROMPT_BLOCK_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MAX_BACKGROUND_SERVICE_ID_CHARS = 64
_BACKGROUND_SERVICE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass(frozen=True, slots=True)
class PluginStatus:
    plugin_id: str
    enabled: bool
    status: str
    reason: str = ""
    plugin_version: str = ""
    stage: str = ""
    permissions: tuple[str, ...] = ()
    capability_ids: tuple[str, ...] = ()
    qq_commands: tuple[str, ...] = ()
    event_types: tuple[str, ...] = ()
    hook_types: tuple[str, ...] = ()
    skill_names: tuple[str, ...] = ()
    schema_errors: tuple[Mapping[str, str], ...] = ()

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
        if self.schema_errors:
            payload["schema_errors"] = [dict(error) for error in self.schema_errors]
        if self.permissions:
            payload["permissions"] = list(self.permissions)
        if self.capability_ids:
            payload["capability_ids"] = list(self.capability_ids)
        if self.qq_commands:
            payload["qq_commands"] = list(self.qq_commands)
        if self.event_types:
            payload["event_types"] = list(self.event_types)
        if self.hook_types:
            payload["hook_types"] = list(self.hook_types)
        if self.skill_names:
            payload["skill_names"] = list(self.skill_names)
        return payload


@dataclass(frozen=True, slots=True)
class PluginContributionSnapshot:
    """Immutable inventory of one active plugin's real host contributions.

    Only contribution kinds that PluginHost can execute today are listed.
    Providers and UI pages are added only when their runtime
    contracts exist; empty future placeholders are never advertised.
    """

    plugin_id: str
    generation: int
    capability_ids: tuple[str, ...] = ()
    qq_commands: tuple[str, ...] = ()
    event_types: tuple[str, ...] = ()
    hook_types: tuple[str, ...] = ()
    background_service_ids: tuple[str, ...] = ()
    prompt_block_ids: tuple[str, ...] = ()
    skill_names: tuple[str, ...] = ()
    surfaces: tuple[str, ...] = ()
    prompt_character_count: int = 0
    event_subscriptions: tuple[EventSubscription, ...] = ()
    service_methods: tuple[CapabilityDescriptor, ...] = ()
    requires_services: tuple = ()
    connections: tuple = ()

    @property
    def contribution_types(self) -> tuple[str, ...]:
        kinds: list[str] = []
        if self.capability_ids:
            kinds.append("capabilities")
        if self.service_methods:
            kinds.append("services")
        if self.connections:
            kinds.append("connections")
        if self.qq_commands:
            kinds.append("commands")
        if self.event_subscriptions:
            kinds.append("event_handlers")
        if self.hook_types:
            kinds.append("hooks")
        if self.background_service_ids:
            kinds.append("background_services")
        if self.prompt_block_ids:
            kinds.append("prompt_blocks")
        if self.skill_names:
            kinds.append("skills")
        return tuple(kinds)

    def as_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "generation": self.generation,
            "types": list(self.contribution_types),
            "capabilities": list(self.capability_ids),
            **({"connections": [spec.as_dict() for spec in self.connections]} if self.connections else {}),
            **({"services": service_catalog(self.service_methods)} if self.service_methods else {}),
            **({"requires_services": [{"service_id": item.service_id, "version": item.version}
                for item in self.requires_services]} if self.requires_services else {}),
            "commands": list(self.qq_commands),
            "event_handlers": list(self.event_types),
            **({"event_subscriptions": [
                {"subscription_id": item.subscription_id, "event_type": item.event_type, "sources": list(item.sources),
                 "scope": item.scope, "coalesce": item.coalesce, "persistence": item.persistence,
                 "request_turn": item.request_turn}
                for item in self.event_subscriptions
            ]} if self.event_subscriptions else {}),
            "hooks": list(self.hook_types),
            "background_services": list(self.background_service_ids),
            "prompt_blocks": list(self.prompt_block_ids),
            "skills": list(self.skill_names),
            "surfaces": list(self.surfaces),
            "prompt_character_count": self.prompt_character_count,
        }


@dataclass(frozen=True, slots=True)
class _CapabilityRegistration:
    plugin_id: str
    adapter: CapabilityAdapter
    descriptor: CapabilityDescriptor
    permissions: tuple[str, ...]
    tool_spec: CapabilityToolSpec
    private_result_values: set[str] = field(default_factory=set, repr=False, compare=False)
    connections: tuple = ()


@dataclass(frozen=True, slots=True)
class _PromptBlockRegistration:
    block_id: str
    text: str


@dataclass(frozen=True, slots=True)
class _SkillRegistration:
    name: str
    root: Path
    mount_name: str


@dataclass(frozen=True, slots=True)
class _BackgroundServiceRegistration:
    service_id: str
    service: Any  # PluginBackgroundJob


@dataclass(frozen=True, slots=True)
class _ActivePlugin:
    plugin: Any
    adapters: tuple[CapabilityAdapter, ...]
    capability_ids: tuple[str, ...]
    requires_services: tuple = ()
    connections: tuple = ()
    background_services: tuple[_BackgroundServiceRegistration, ...] = ()
    qq_command_registrations: tuple[_PluginCommandRegistration, ...] = ()
    event_registrations: tuple[_PluginEventRegistration, ...] = ()
    hook_registrations: tuple[_PluginHookRegistration, ...] = ()
    prompt_block_registrations: tuple[_PromptBlockRegistration, ...] = ()
    skill_registrations: tuple[_SkillRegistration, ...] = ()


class _ActivationFailure(RuntimeError):
    def __init__(self, reason: str, *, status: str = "failed", stage: str = "", schema_errors=()) -> None:
        self.reason = reason
        self.status = status
        self.stage = stage
        self.schema_errors = tuple(schema_errors)
        super().__init__(reason)


class _StagedRegistrar(PluginRegistrar):
    def __init__(self) -> None:
        self._adapters: list[CapabilityAdapter] = []
        self._sealed = False
        self._storage_dir: Path | None = None
        self._background_service_invocation_factory: Any = None
        self._resource_port: Any = None
        self._capability_port: Any = None
        self._connection_port: Any = None
        self._private_result_values: set[str] = set()
        self._background_services: list[_BackgroundServiceRegistration] = []
        self._job_permission: bool = False
        self._notification_port: Any = None  # NotificationPort | None
        self._notification_permission: bool = False
        self._qq_commands: list[_PluginCommandRegistration] = []
        self._qq_command_permission: bool = False
        self._event_registrations: list[_PluginEventRegistration] = []
        self._event_permission: bool = False
        self._events_port: Any = None
        self._task_port: Any = None
        self._hook_registrations: list[_PluginHookRegistration] = []
        self._hook_permission: bool = False
        self._prompt_blocks: list[_PromptBlockRegistration] = []
        self._prompt_permission: bool = False
        self._skills: list[_SkillRegistration] = []
        self._skill_permission: bool = False

    @property
    def adapters(self) -> tuple[CapabilityAdapter, ...]:
        return tuple(self._adapters)

    @property
    def background_services(self) -> tuple[_BackgroundServiceRegistration, ...]:
        return tuple(self._background_services)

    def add_capability_adapter(self, adapter: CapabilityAdapter) -> None:
        if self._sealed:
            raise RuntimeError("plugin_registrar_sealed")
        self._adapters.append(adapter)

    def add_skill(self, skill_root: Path) -> None:
        if self._sealed:
            raise RuntimeError("plugin_registrar_sealed")
        if not self._skill_permission:
            raise RuntimeError("skill_contribution_permission_required")
        try:
            entry = validate_contributed_skill_root(Path(skill_root))
            root = entry.root
        except (OSError, SkillError, TypeError, ValueError) as exc:
            reason = exc.reason if isinstance(exc, SkillError) else "skill_package_invalid"
            raise RuntimeError(reason) from None
        if any(item.name == entry.name for item in self._skills):
            raise RuntimeError("duplicate_plugin_skill")
        self._skills.append(
            _SkillRegistration(name=entry.name, root=root, mount_name="")
        )

    def add_prompt_block(self, block_id: str, text: str) -> None:
        if self._sealed:
            raise RuntimeError("plugin_registrar_sealed")
        if not self._prompt_permission:
            raise RuntimeError("prompt_contribution_permission_required")
        if not isinstance(block_id, str) or _PROMPT_BLOCK_ID_PATTERN.fullmatch(block_id) is None:
            raise RuntimeError("invalid_prompt_block_id")
        if not isinstance(text, str):
            raise RuntimeError("invalid_prompt_block_text")
        normalized_text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized_text or len(normalized_text) > _MAX_PROMPT_BLOCK_CHARS:
            raise RuntimeError("invalid_prompt_block_text")
        if any(
            unicodedata.category(character) == "Cc"
            and character not in {"\n", "\t"}
            for character in normalized_text
        ):
            raise RuntimeError("invalid_prompt_block_text")
        if any(registration.block_id == block_id for registration in self._prompt_blocks):
            raise RuntimeError("duplicate_prompt_block_id")
        if len(self._prompt_blocks) >= _MAX_PROMPT_BLOCKS_PER_PLUGIN:
            raise RuntimeError("too_many_prompt_blocks")
        if sum(len(registration.text) for registration in self._prompt_blocks) + len(
            normalized_text
        ) > _MAX_PROMPT_BLOCK_TOTAL_CHARS:
            raise RuntimeError("prompt_blocks_too_large")
        self._prompt_blocks.append(
            _PromptBlockRegistration(block_id=block_id, text=normalized_text)
        )

    def add_background_service(self, service_id: str, service: Any) -> None:
        if self._sealed:
            raise RuntimeError("plugin_registrar_sealed")
        if not self._job_permission:
            raise RuntimeError("job_permission_required")
        if not isinstance(service_id, str) or _BACKGROUND_SERVICE_ID_PATTERN.fullmatch(service_id) is None:
            raise RuntimeError("invalid_background_service_id")
        if any(item.service_id == service_id for item in self._background_services):
            raise RuntimeError("duplicate_background_service_id")
        if not callable(getattr(service, "start", None)) or not callable(getattr(service, "stop", None)):
            raise RuntimeError("invalid_background_service")
        # The SDK background adapter opens the host invocation itself through
        # ``background_service_invocation_factory``; the host does not wrap it.
        self._background_services.append(
            _BackgroundServiceRegistration(service_id=service_id, service=service)
        )

    def get_storage_dir(self) -> Path:
        if self._sealed:
            raise RuntimeError("plugin_registrar_sealed")
        if self._storage_dir is None:
            raise RuntimeError("storage_permission_required")
        return self._storage_dir

    def get_resource_port(self) -> Any:
        if self._sealed:
            raise RuntimeError("plugin_registrar_sealed")
        if self._resource_port is None:
            raise RuntimeError("resource_read_permission_required")
        return self._resource_port

    def get_capability_port(self) -> Any:
        if self._sealed:
            raise RuntimeError("plugin_registrar_sealed")
        if self._capability_port is None:
            raise RuntimeError("capability_invoke_permission_required")
        return self._capability_port

    def get_events_port(self) -> Any:
        if self._sealed:
            raise RuntimeError("plugin_registrar_sealed")
        if self._events_port is None:
            raise RuntimeError("event_permission_required")
        return self._events_port

    def get_task_port(self) -> Any:
        if self._sealed:
            raise RuntimeError("plugin_registrar_sealed")
        return self._task_port

    def add_event_subscription(self, subscription, handler) -> None:
        if self._sealed:
            raise RuntimeError("plugin_registrar_sealed")
        if not self._event_permission:
            raise _ActivationFailure("event_subscribe_permission_required")
        reason = validate_event_subscription(subscription)
        if reason:
            raise _ActivationFailure(reason)
        if not callable(getattr(handler, "handle_event", None)):
            raise _ActivationFailure("event_handler_invalid")
        if any(item.subscription and item.subscription.subscription_id == subscription.subscription_id for item in self._event_registrations):
            raise _ActivationFailure("event_subscription_duplicate")
        subscription = replace(subscription, event_type=subscription.event_type.strip().lower())
        self._event_registrations.append(_PluginEventRegistration("", subscription.event_type, subscription, handler))

    def get_connection_port(self) -> Any:
        if self._sealed:
            raise RuntimeError("plugin_registrar_sealed")
        if self._connection_port is None:
            raise RuntimeError("connection_permission_required")
        return self._connection_port

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

    def add_hook_handler(self, hook_type: str, handler: Any) -> None:
        if self._sealed:
            raise RuntimeError("plugin_registrar_sealed")
        if not self._hook_permission:
            raise RuntimeError("hook_subscribe_permission_required")
        normalized = str(hook_type or "").strip().lower()
        if normalized not in SUPPORTED_HOOK_TYPES:
            raise RuntimeError("unsupported_plugin_hook_type")
        if not callable(getattr(handler, "handle_hook", None)):
            raise RuntimeError("invalid_plugin_hook_handler")
        if any(item.hook_type == normalized for item in self._hook_registrations):
            raise RuntimeError("duplicate_plugin_hook_handler")
        self._hook_registrations.append(
            _PluginHookRegistration(plugin_id="", hook_type=normalized, handler=handler)
        )

    @property
    def qq_commands(self) -> tuple[Any, ...]:
        return tuple(self._qq_commands)

    @property
    def event_registrations(self) -> tuple[_PluginEventRegistration, ...]:
        return tuple(self._event_registrations)

    @property
    def hook_registrations(self) -> tuple[_PluginHookRegistration, ...]:
        return tuple(self._hook_registrations)

    @property
    def prompt_blocks(self) -> tuple[_PromptBlockRegistration, ...]:
        return tuple(self._prompt_blocks)

    @property
    def skills(self) -> tuple[_SkillRegistration, ...]:
        return tuple(self._skills)

    def _set_storage_dir(self, path: Path) -> None:
        """Called by PluginHost after manifest validation; not part of the plugin API."""
        self._storage_dir = path

    @property
    def background_service_invocation_factory(self) -> Any:
        """Runtime hook: register one host invocation for a supervised service.

        The SDK's background adapter calls this at service start so the service
        can use host ports that need an invocation (for example ``request_turn``)
        for its whole lifetime. It returns the host-side request id, or an empty
        string when the runtime has no callback lane.
        """

        return self._background_service_invocation_factory

    def _set_background_service_invocation_factory(self, factory: Any) -> None:
        self._background_service_invocation_factory = factory if callable(factory) else None

    def _set_job_permission(self, allowed: bool) -> None:
        self._job_permission = allowed

    def _set_notification_port(self, port: Any) -> None:
        self._notification_port = port
        self._notification_permission = port is not None

    def _set_qq_command_permission(self, allowed: bool) -> None:
        self._qq_command_permission = allowed

    def _set_event_permission(self, allowed: bool) -> None:
        self._event_permission = allowed

    def _set_hook_permission(self, allowed: bool) -> None:
        self._hook_permission = allowed

    def _set_prompt_permission(self, allowed: bool) -> None:
        self._prompt_permission = allowed

    def _set_skill_permission(self, allowed: bool) -> None:
        self._skill_permission = allowed

    def seal(self) -> None:
        self._sealed = True


class PluginHost:
    """Own plugin discovery, generation-scoped registration, and shutdown.

    Each running generation uses one immutable selection snapshot.  The
    extension-management service may replace that snapshot only by draining the
    current generation and starting a new one through :meth:`reconfigure`.
    Discovery, artifact audit, imports, factories, registration, health checks,
    and capability enumeration happen exclusively during lifecycle startup.
    """

    def __init__(
        self,
        selections: tuple[PluginSelection, ...],
        *,
        contribution_policy: PluginContributionPolicy,
        entry_points_provider: Callable[[], Iterable[Any]] | None = None,
        activation_timeout_seconds: float = 5.0,
        invoke_timeout_seconds: float | None = None,
        managed_artifact_timeout_seconds: float = 0.0,
        close_timeout_seconds: float = 2.0,
        event_handler_timeout_seconds: float = DEFAULT_EVENT_HANDLER_TIMEOUT_SECONDS,
        hook_handler_timeout_seconds: float = DEFAULT_HOOK_HANDLER_TIMEOUT_SECONDS,
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
        self._invoke_timeout_seconds = (
            None if invoke_timeout_seconds is None else max(0.1, float(invoke_timeout_seconds))
        )
        self._managed_artifact_timeout_seconds = max(0.0, float(managed_artifact_timeout_seconds))
        self._resource_provider: Any = None
        self._capability_provider: Any = None
        self._connection_provider: Any = None
        self._background_service_invocation_factory: Any = None
        self._close_timeout_seconds = max(0.1, float(close_timeout_seconds))
        self._event_handler_timeout_seconds = max(0.01, float(event_handler_timeout_seconds))
        self._hook_handler_timeout_seconds = max(0.01, float(hook_handler_timeout_seconds))

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
        self._contribution_snapshots: tuple[PluginContributionSnapshot, ...] = ()
        self._activation_order: tuple[CapabilityAdapter, ...] = ()
        self._closed_adapters: list[CapabilityAdapter] = []
        self._close_failure_count = 0
        self._runtime_loop: asyncio.AbstractEventLoop | None = None
        self._managed_artifact_sink: ManagedArtifactSink | None = None
        self._storage_service: PluginStorageService | None = None
        self._notification_port: Any = None  # NotificationPort | None
        self._background_service_invocation_factory: Any = None
        self._notification_ledger = _NotificationDeliveryLedger()
        self._background_service_tasks: dict[
            tuple[str, str], tuple[Any, _HostJobController, asyncio.Task]
        ] = {}
        self._background_service_statuses: dict[tuple[str, str], dict[str, str]] = {}
        self._background_service_stop_timeout_seconds: float = 10.0
        self._background_service_stop_failure_count = 0
        self._generation = 0
        self._hook_broker: PluginHookBroker | None = None
        self._event_broker: PluginEventBroker | None = None
        self._events_provider: Any = None
        self._tasks_provider: Any = None

        self._management_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._invoke_lock = asyncio.Lock()
        self._inflight_count = 0
        self._inflight_zero = asyncio.Event()
        self._inflight_zero.set()

    @property
    def state(self) -> str:
        return self._state

    @property
    def selections(self) -> tuple[PluginSelection, ...]:
        return tuple(self._selections)

    @property
    def runtime_loop(self) -> asyncio.AbstractEventLoop | None:
        return self._runtime_loop

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return tuple(self._capabilities)

    @property
    def capability_owners(self) -> Mapping[str, str]:
        return MappingProxyType({key: item.plugin_id for key, item in self._capabilities.items()})

    @property
    def capability_descriptors(self) -> Mapping[str, CapabilityDescriptor]:
        """Return an immutable point-in-time view without exposing adapters."""

        return MappingProxyType(
            {
                capability_id: _copy_descriptor_snapshot(registration.descriptor)
                for capability_id, registration in self._capabilities.items()
            }
        )

    @property
    def contribution_snapshots(self) -> tuple[PluginContributionSnapshot, ...]:
        """Return the immutable contribution inventory for this generation."""

        return self._contribution_snapshots

    def stable_system_prompt_blocks(self) -> tuple[str, ...]:
        """Return the active restart-only prompt snapshot in stable key order."""

        registrations = [
            (plugin_id, registration.block_id, registration.text)
            for plugin_id, active in self._active_plugins.items()
            for registration in active.prompt_block_registrations
        ]
        registrations.sort(key=lambda item: (item[0], item[1]))
        return tuple(text for _plugin_id, _block_id, text in registrations)

    def skill_roots(self) -> tuple[ContributedSkillRoot, ...]:
        """Return the active generation's immutable plugin Skill mounts."""

        roots = [
            ContributedSkillRoot(
                source=f"plugin:{plugin_id}",
                root=registration.root,
                mount_name=registration.mount_name,
            )
            for plugin_id, active in self._active_plugins.items()
            for registration in active.skill_registrations
        ]
        roots.sort(key=lambda item: (item.source, item.root.name.casefold()))
        return tuple(roots)

    def status_snapshot(self) -> dict[str, Any]:
        reason = ""
        if self._state == "degraded":
            reason = (
                "plugin_runtime_failed"
                if any(
                    item.get("status") in {"degraded", "failed"}
                    for item in self._background_service_statuses.values()
                )
                else "plugin_activation_failed"
            )
        elif self._state not in _HOST_AVAILABLE_STATES:
            reason = "host_unavailable"
        background_service_statuses = [
            {
                "plugin_id": _public_plugin_id(plugin_id),
                "service_id": service_id,
                "status": item.get("status", "unknown"),
                "reason": item.get("reason", ""),
            }
            for (plugin_id, service_id), item in sorted(self._background_service_statuses.items())
        ]
        contribution_by_plugin = {
            item.plugin_id: item for item in self._contribution_snapshots
        }
        plugin_statuses: list[dict[str, Any]] = []
        for status in self._plugin_statuses:
            payload = status.as_dict()
            contribution = contribution_by_plugin.get(status.plugin_id)
            if contribution is not None:
                payload["contribution_snapshot"] = contribution.as_dict()
            plugin_statuses.append(payload)
        return {
            "ok": self._state == "active",
            "status": self._state,
            "reason": reason,
            "contribution_policy": self._contribution_policy_id,
            "generation": self._generation,
            "configured_plugin_count": len(self._plugin_statuses),
            "plugin_count": len(self._active_plugins),
            "capability_count": sum(
                len(item.capability_ids) for item in self._contribution_snapshots
            ),
            "prompt_block_count": sum(
                len(item.prompt_block_ids) for item in self._contribution_snapshots
            ),
            "event_handler_count": sum(
                len(item.event_types) + len(item.event_subscriptions) for item in self._contribution_snapshots
            ),
            **({"event_runtime": self._event_broker.status_snapshot()} if self._event_broker is not None else {}),
            "hook_handler_count": sum(
                len(item.hook_types) for item in self._contribution_snapshots
            ),
            "skill_count": sum(
                len(item.skill_names) for item in self._contribution_snapshots
            ),
            "hook_runtime": (
                self._hook_broker.status_snapshot()
                if self._hook_broker is not None
                else {
                    "dispatch_count": 0,
                    "dispatch_failure_count": 0,
                    "diagnostic_count": 0,
                    "last_diagnostics": [],
                    "last_failures": [],
                }
            ),
            "background_service_count": len(self._background_service_statuses),
            "running_background_service_count": sum(
                1
                for item in self._background_service_statuses.values()
                if item.get("status") == "running"
            ),
            "background_service_stop_failure_count": self._background_service_stop_failure_count,
            "background_services": background_service_statuses,
            "close_failure_count": self._close_failure_count,
            "contract": {
                "supported_hook_types": sorted(SUPPORTED_HOOK_TYPES),
                "registration_limits": {
                    "adapters_per_plugin": _MAX_ADAPTERS_PER_PLUGIN,
                    "capabilities_per_plugin": _MAX_CAPABILITIES_PER_PLUGIN,
                    "qq_commands_per_plugin": _MAX_QQ_COMMANDS_PER_PLUGIN,
                    "prompt_blocks_per_plugin": _MAX_PROMPT_BLOCKS_PER_PLUGIN,
                    "prompt_block_chars": _MAX_PROMPT_BLOCK_CHARS,
                    "prompt_block_total_chars": _MAX_PROMPT_BLOCK_TOTAL_CHARS,
                    "background_service_id_chars": _MAX_BACKGROUND_SERVICE_ID_CHARS,
                    "permissions_per_plugin": _MAX_PERMISSIONS_PER_PLUGIN,
                },
                "timeouts": {
                    "activation_step_seconds": self._activation_timeout_seconds,
                    "invoke_seconds": self._invoke_timeout_seconds,
                    "managed_artifact_seconds": self._managed_artifact_timeout_seconds,
                    "adapter_close_seconds": self._close_timeout_seconds,
                    "background_service_stop_seconds": self._background_service_stop_timeout_seconds,
                    "legacy_event_handler_seconds": self._event_handler_timeout_seconds,
                    "hook_handler_seconds": self._hook_handler_timeout_seconds,
                },
            },
            "plugins": plugin_statuses,
        }

    def bind_managed_artifact_sink(self, sink: ManagedArtifactSink) -> None:
        """Bind the host-owned artifact sink before restart-only startup."""

        if self._state != "created":
            raise RuntimeError("plugin_host_already_started")
        if not callable(getattr(sink, "materialize", None)):
            raise TypeError("invalid_managed_artifact_sink")
        self._managed_artifact_sink = sink
        if self._event_broker is not None:
            self._event_broker.observations.sink = sink

    def bind_resource_provider(self, provider: Any) -> None:
        if self._state != "created":
            raise RuntimeError("plugin_host_already_started")
        if not callable(getattr(provider, "open", None)):
            raise TypeError("invalid_resource_provider")
        self._resource_provider = provider

    def bind_capability_provider(self, provider: Any) -> None:
        if self._state != "created":
            raise RuntimeError("plugin_host_already_started")
        if not callable(getattr(provider, "invoke", None)):
            raise TypeError("invalid_capability_provider")
        self._capability_provider = provider

    def bind_connection_provider(self, provider: Any) -> None:
        if self._state != "created":
            raise RuntimeError("plugin_host_already_started")
        if not callable(getattr(provider, "resolve", None)):
            raise TypeError("invalid_connection_provider")
        self._connection_provider = provider

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

    def bind_background_service_invocation_factory(self, factory: Any) -> None:
        """Bind the runtime hook that gives supervised services an invocation.

        ``factory`` is called with a service id and returns an async context
        manager; the host exposes a zero-argument form to the SDK background
        adapter, which has no service id of its own.
        """

        if self._state != "created":
            raise RuntimeError("plugin_host_already_started")
        if not callable(factory):
            self._background_service_invocation_factory = None
            return
        self._background_service_invocation_factory = lambda: factory("")

    def bind_turn_router(self, router: Any) -> None:
        """Bind the host-owned turn-request router for declared subscriptions."""

        if self._state != "created":
            raise RuntimeError("plugin_host_already_started")
        if not callable(getattr(router, "request_turn", None)):
            raise TypeError("invalid_turn_router")
        self.build_event_broker().bind_turn_router(router)

    def build_qq_command_broker(
        self,
        host_registrations: tuple = (),
    ) -> PluginQQCommandBroker:
        """Build an immutable command broker from all activated plugin QQ commands.

        Call after :meth:`start`.  Registrations do not mutate at runtime, and
        the returned broker rejects dispatch once the host begins stopping.
        ``host_registrations`` may carry host builtin commands (e.g. ``/能力``);
        they take precedence over plugin registrations with the same token.
        """
        return PluginQQCommandBroker(
            self._qq_command_registrations_snapshot(),
            host_registrations=tuple(host_registrations or ()),
            availability_provider=lambda: self._state in _HOST_AVAILABLE_STATES,
            registrations_provider=self._qq_command_registrations_snapshot,
        )

    def build_event_broker(self) -> PluginEventBroker:
        """Build a generation-aware observer over active event handlers."""

        if self._event_broker is None:
            self._event_broker = PluginEventBroker(
                (), handler_timeout_seconds=self._event_handler_timeout_seconds,
                availability_provider=lambda: self._state in _HOST_AVAILABLE_STATES,
                registrations_provider=self._event_registrations_snapshot,
                owners_provider=lambda: {key: str(self._generation) for key in self._active_plugins},
                runtime_loop_provider=lambda: self._runtime_loop,
                executor=self._invoke_event_registration,
            )
            self._event_broker.observations.sink = self._managed_artifact_sink
        return self._event_broker

    def bind_events_provider(self, provider):
        if not callable(getattr(provider, "request", None)):
            raise TypeError("event_provider_invalid")
        self._events_provider = provider

    def bind_tasks_provider(self, provider):
        if not callable(getattr(provider, "request", None)):
            raise TypeError("task_provider_invalid")
        self._tasks_provider = provider

    def build_hook_broker(self) -> PluginHookBroker:
        """Build the generation-aware execution Hook observer."""

        broker = PluginHookBroker(
            self._hook_registrations_snapshot(),
            handler_timeout_seconds=self._hook_handler_timeout_seconds,
            availability_provider=lambda: self._state in _HOST_AVAILABLE_STATES,
            registrations_provider=self._hook_registrations_snapshot,
            runtime_loop_provider=lambda: self._runtime_loop,
        )
        self._hook_broker = broker
        return broker

    def _qq_command_registrations_snapshot(self) -> tuple[_PluginCommandRegistration, ...]:
        return tuple(
            registration
            for active in self._active_plugins.values()
            for registration in active.qq_command_registrations
        )

    def _event_registrations_snapshot(self) -> tuple[_PluginEventRegistration, ...]:
        return tuple(
            replace(registration, generation_id=str(self._generation))
            for active in self._active_plugins.values()
            for registration in active.event_registrations
        )

    def _hook_registrations_snapshot(self) -> tuple[_PluginHookRegistration, ...]:
        return tuple(
            registration
            for active in self._active_plugins.values()
            for registration in active.hook_registrations
        )

    async def start(self) -> dict[str, Any]:
        async with self._management_lock:
            return await self._start_once()

    async def _start_once(self, *, allow_stopped: bool = False, prepare_only: bool = False) -> dict[str, Any]:
        async with self._lifecycle_lock:
            if self._state in _HOST_AVAILABLE_STATES or self._state in {"starting", "prepared", "stopping"}:
                return self.status_snapshot()
            if self._state == "stopped" and not allow_stopped:
                return self.status_snapshot()

            self._runtime_loop = asyncio.get_running_loop()
            self._state = "starting"
            # Adapter identities are scoped to one lifecycle generation.  Old
            # references have already been closed by _stop_once().
            self._closed_adapters = []
            working_plugins: dict[str, _ActivePlugin] = {}
            working_capabilities: dict[str, _CapabilityRegistration] = {}
            working_qq_commands: set[str] = set()
            working_skill_names: set[str] = set()
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
                    reserved_skill_names=frozenset(working_skill_names),
                    check_health=not prepare_only,
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
                working_skill_names.update(
                    registration.name for registration in active.skill_registrations
                )
                activation_order.extend(active.adapters)

            if not prepare_only and any(active.requires_services for active in working_plugins.values()):
                from .plugin_service_dependencies import resolve_dependencies
                def dependency_plan():
                    return resolve_dependencies({item.plugin_id: item.as_dict() for item in _build_contribution_snapshots(
                        working_plugins, working_capabilities, generation=self._generation + 1)})
                async def withdraw(plugin_id, status, reason):
                    active = working_plugins.pop(plugin_id)
                    for capability_id in active.capability_ids:
                        working_capabilities.pop(capability_id, None)
                    await self._close_adapters(reversed(active.adapters))
                    for index, item in enumerate(statuses):
                        if item.plugin_id == plugin_id:
                            statuses[index] = replace(item, status=status, reason=reason)
                plan = dependency_plan()
                for plugin_id in plan.activation_order:
                    active = working_plugins[plugin_id]
                    if not active.requires_services or plugin_id in dependency_plan().waiting:
                        continue
                    try:
                        for adapter in active.adapters:
                            await self._check_adapter_health(adapter)
                    except _ActivationFailure as exc:
                        await withdraw(plugin_id, "failed", exc.reason)
                for plugin_id, errors in dependency_plan().waiting.items():
                    await withdraw(plugin_id, "waiting_dependency", errors[0]["reason"])
                activation_order = [adapter for active in working_plugins.values() for adapter in active.adapters]

            self._plugin_statuses = tuple(statuses)
            self._active_plugins = MappingProxyType(dict(working_plugins))
            self._capabilities = MappingProxyType(dict(working_capabilities))
            self._activation_order = tuple(activation_order)
            self._generation += 1
            self._contribution_snapshots = _build_contribution_snapshots(
                working_plugins,
                working_capabilities,
                generation=self._generation,
            )
            if prepare_only:
                self._plugin_statuses = tuple(replace(item, status="prepared") if item.status == "active" else item
                                              for item in self._plugin_statuses)
                self._state = "prepared" if all(not item.enabled or item.status == "prepared" for item in self._plugin_statuses) else "failed"
                return self.status_snapshot()
            return await self._run_registered_plugins()

    async def prepare(self) -> dict[str, Any]:
        """Register and validate declarations without health checks or jobs."""
        async with self._management_lock:
            return await self._start_once(prepare_only=True)

    async def activate_prepared(self) -> dict[str, Any]:
        """Activate after the caller has checked the full generation's dependencies."""
        async with self._management_lock:
            async with self._lifecycle_lock:
                if self._state != "prepared":
                    return self.status_snapshot()
                try:
                    for active in self._active_plugins.values():
                        for adapter in active.adapters:
                            await self._check_adapter_health(adapter)
                except _ActivationFailure as exc:
                    self._state = "failed"
                    self._plugin_statuses = tuple(replace(item, status="failed", reason=exc.reason)
                                                  if item.enabled else item for item in self._plugin_statuses)
                    return self.status_snapshot()
                self._plugin_statuses = tuple(replace(item, status="active") if item.status == "prepared" else item
                                              for item in self._plugin_statuses)
                return await self._run_registered_plugins()

    async def _check_adapter_health(self, adapter):
        health = await _bounded_adapter_call(
            adapter.health(),
            timeout_seconds=self._activation_timeout_seconds,
            failure_reason="plugin_health_failed",
        )
        if not isinstance(health, HealthStatus):
            raise _ActivationFailure("invalid_plugin_health_result")
        if not health.ok:
            safe_health = sanitize_capability_result(
                CapabilityResult(is_error=True, status="unavailable", reason=health.reason)
            )
            raise _ActivationFailure(safe_health.reason or "plugin_health_unavailable")

    async def _run_registered_plugins(self):
        # Publish one independently supervised task for every registered
        # service.  The tuple key avoids ambiguous string concatenation and
        # lets sibling services keep running when one exits.
        service_tasks: dict[
            tuple[str, str], tuple[Any, _HostJobController, asyncio.Task]
        ] = {}
        self._background_service_statuses = {}
        for plugin_id, active_plugin in self._active_plugins.items():
            for registration in active_plugin.background_services:
                service_key = (plugin_id, registration.service_id)
                controller = _HostJobController()
                controller._arm()
                task = asyncio.create_task(
                    run_supervised_job(registration.service, controller),
                    name=f"plugin-service:{plugin_id}:{registration.service_id}",
                )
                self._background_service_statuses[service_key] = {
                    "status": "running",
                    "reason": "",
                }
                task.add_done_callback(
                    lambda done, key=service_key, ctl=controller: self._on_background_service_done(
                        key,
                        ctl,
                        done,
                    )
                )
                service_tasks[service_key] = (registration.service, controller, task)
        self._background_service_tasks = service_tasks
        enabled_failures = any(status.enabled and status.status != "active" for status in self._plugin_statuses)
        async with self._invoke_lock:
            self._state = "degraded" if enabled_failures else "active"
        if service_tasks:
            # Give every service one scheduling opportunity so an immediate exit
            # is reflected in the startup snapshot instead of fake readiness.
            await asyncio.sleep(0)
            for service_key, (_service, controller, task) in service_tasks.items():
                if (
                    task.done()
                    and self._background_service_statuses.get(service_key, {}).get("status")
                    == "running"
                ):
                    self._on_background_service_done(service_key, controller, task)
        return self.status_snapshot()

    async def stop(self) -> dict[str, Any]:
        async with self._management_lock:
            return await self._stop_once()

    async def restart(self) -> dict[str, Any]:
        """Recreate installed plugin instances without claiming code hot reload.

        The current selection snapshot remains authoritative.  This operation
        drains active calls, stops jobs, closes adapters, then re-runs discovery
        and activation.  Imported Python modules may still come from the current
        process cache; true code-generation hot swap belongs to the future
        isolated PluginHost runtime.
        """

        async with self._management_lock:
            await self._stop_once()
            return await self._start_once(allow_stopped=True)

    async def reconfigure(self, selections: tuple[PluginSelection, ...]) -> dict[str, Any]:
        """Apply one validated selection snapshot through the normal lifecycle."""

        if not isinstance(selections, tuple) or any(not isinstance(item, PluginSelection) for item in selections):
            raise TypeError("plugin_selections_must_be_snapshot")
        if any(not is_valid_plugin_id(item.plugin_id) or not isinstance(item.enabled, bool) for item in selections):
            raise ValueError("invalid_plugin_selection")
        if len({item.plugin_id for item in selections}) != len(selections):
            raise ValueError("duplicate_plugin_selection")
        async with self._management_lock:
            await self._stop_once()
            self._selections = tuple(selections)
            return await self._start_once(allow_stopped=True)

    async def _stop_once(self) -> dict[str, Any]:
        async with self._lifecycle_lock:
            if self._state == "stopped":
                return self.status_snapshot()
            if self._state == "created":
                self._state = "stopped"
                return self.status_snapshot()

            async with self._invoke_lock:
                self._state = "stopping"

            if self._event_broker is not None:
                await self._event_broker.reconcile()

            try:
                await _await_with_optional_timeout(
                    self._inflight_zero.wait(),
                    timeout_seconds=_combined_invocation_timeout(
                        self._invoke_timeout_seconds,
                        self._managed_artifact_timeout_seconds,
                    ),
                )
            except (TimeoutError, asyncio.TimeoutError):
                pass

            # Stop supervised services before closing capability adapters.
            await self._stop_background_services()

            await self._close_adapters(reversed(self._activation_order))
            self._active_plugins = MappingProxyType({})
            self._capabilities = MappingProxyType({})
            self._contribution_snapshots = ()
            self._activation_order = ()
            self._background_service_tasks = {}
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
        cancel_requested = threading.Event()
        invocation_tasks: list[asyncio.Task] = []

        async def invoke_on_runtime() -> CapabilityResult:
            invocation_tasks.append(asyncio.current_task())
            if cancel_requested.is_set():
                raise asyncio.CancelledError()
            return await self.invoke(capability_id, args, context=context)

        invocation = invoke_on_runtime()
        try:
            concurrent_future = asyncio.run_coroutine_threadsafe(
                invocation,
                runtime_loop,
            )
        except RuntimeError:
            invocation.close()
            return CapabilityResult(
                is_error=True,
                status="host_unavailable",
                reason="host_unavailable",
            )
        consumer_future = asyncio.wrap_future(concurrent_future)

        async def cancel_and_drain() -> None:
            # Cancelling the concurrent Future itself acknowledges immediately,
            # before the lifecycle-loop task has released resources. Cancel the
            # actual task and retain its Future until that task really finishes.
            cancel_requested.set()

            def cancel_task() -> None:
                if invocation_tasks:
                    invocation_tasks[0].cancel()

            runtime_loop.call_soon_threadsafe(cancel_task)
            while not consumer_future.done():
                try:
                    await asyncio.shield(consumer_future)
                except asyncio.CancelledError:
                    continue

        try:
            return await _await_with_optional_timeout(
                asyncio.shield(consumer_future),
                timeout_seconds=_combined_invocation_timeout(
                    self._invoke_timeout_seconds,
                    self._managed_artifact_timeout_seconds,
                ),
            )
        except asyncio.CancelledError:
            await cancel_and_drain()
            return consumer_future.result()
        except (TimeoutError, asyncio.TimeoutError):
            await cancel_and_drain()
            if not consumer_future.cancelled():
                return consumer_future.result()
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
            from .capcore_runtime import tool_identity_rejection

            reason = tool_identity_rejection(registration.descriptor, context)
            if reason:
                return CapabilityResult(is_error=True, status="blocked", reason=reason)
            self._inflight_count += 1
            self._inflight_zero.clear()

        resource_invocation = ResourceInvocation(
            registration.plugin_id, context, capability_id=registration.descriptor.id,
            can_invoke_capabilities=CAPABILITY_INVOKE_PERMISSION in registration.permissions,
            connection_names=permitted_connections(registration.permissions),
            connection_specs=registration.connections,
            private_values=registration.private_result_values,
            generation_id=str(self._generation),
            event_origin_context=getattr(current_resource_invocation.get(), "event_origin_context", None),
            event_delivery_id=getattr(current_resource_invocation.get(), "event_delivery_id", ""),
            can_emit_events=EVENT_EMIT_PERMISSION in registration.permissions,
            can_bind_events=EVENT_SUBSCRIBE_PERMISSION in registration.permissions,
            can_observe_context=CONTEXT_OBSERVE_PERMISSION in registration.permissions,
            can_request_turn=AGENT_TURN_REQUEST_PERMISSION in registration.permissions,
        )
        initializer = getattr(self._events_provider or self.build_event_broker(), "initialize_scope", None)
        if callable(initializer):
            initializer(resource_invocation)
        resource_token = current_resource_invocation.set(resource_invocation)
        try:
            validation = validate_tool_spec_args(registration.tool_spec, args)
            if not validation.ok:
                first_reason = validation.errors[0].code if validation.errors else "invalid_arguments"
                return CapabilityResult(
                    is_error=True,
                    status="validation_error",
                    reason=first_reason,
                    content={"errors": [error.as_dict() for error in validation.errors]},
                )
            try:
                resource_invocation.arguments = deepcopy(validation.normalized_args)
                result = await resource_invocation.run(
                    registration.adapter.invoke(
                        registration.descriptor.id,
                        validation.normalized_args,
                        context,
                    ),
                    timeout_seconds=self._invoke_timeout_seconds,
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
            current_resource_invocation.reset(resource_token)
            await resource_invocation.aclose()
            async with self._invoke_lock:
                self._inflight_count = max(0, self._inflight_count - 1)
                if self._inflight_count == 0:
                    self._inflight_zero.set()

    async def invoke_event(self, subscription_id, event: Event, *, context: InvocationContext, scope_id: str,
                           delivery_id: str) -> CapabilityResult:
        registration = next((item for item in self._event_registrations_snapshot() if item.subscription and
                             item.subscription.subscription_id == subscription_id), None)
        if registration is None:
            return CapabilityResult(is_error=True, status="unavailable", reason="event_subscription_unavailable")
        return await self._invoke_event_registration(registration, event, context=context,
                                                      scope_id=scope_id, delivery_id=delivery_id)

    async def _invoke_event_registration(self, registration, event, *, context, scope_id, delivery_id,
                                         origin_context=None, invocation_out=None, keep_scope_open=False):
        async with self._invoke_lock:
            if self._state not in _HOST_AVAILABLE_STATES or registration.generation_id != str(self._generation):
                return CapabilityResult(is_error=True, status="unavailable", reason="event_subscription_unavailable")
            self._inflight_count += 1
            self._inflight_zero.clear()
        scope = ResourceInvocation(
            registration.plugin_id, context, capability_id=registration.subscription.subscription_id,
            can_invoke_capabilities=CAPABILITY_INVOKE_PERMISSION in registration.permissions,
            can_read_resources=RESOURCE_READ_PERMISSION in registration.permissions,
            connection_names=permitted_connections(registration.permissions),
            connection_specs=registration.connections,
            private_values=registration.private_values, generation_id=str(self._generation),
            event_delivery_id=delivery_id, can_emit_events=EVENT_EMIT_PERMISSION in registration.permissions,
            event_origin_context=origin_context,
            can_bind_events=EVENT_SUBSCRIBE_PERMISSION in registration.permissions,
            can_observe_context=CONTEXT_OBSERVE_PERMISSION in registration.permissions,
            can_request_turn=AGENT_TURN_REQUEST_PERMISSION in registration.permissions,
        )
        initializer = getattr(self._events_provider or self.build_event_broker(), "initialize_scope", None)
        if callable(initializer):
            initializer(scope)
        # Expose the live invocation to the caller (the event broker needs it for
        # a subscription-declared request_turn) without changing the result type.
        if invocation_out is not None:
            invocation_out[0] = scope
        token = current_resource_invocation.set(scope)
        try:
            result = await scope.run(registration.handler.handle_event(event, scope_id))
            return sanitize_capability_result(result)
        except asyncio.CancelledError:
            raise
        except Exception:
            return CapabilityResult(is_error=True, status="error", reason="event_handler_exception")
        finally:
            current_resource_invocation.reset(token)
            # A subscription that declares `request_turn` needs this same
            # invocation alive while the host queues the turn; the broker closes
            # it after that finishes.
            if not keep_scope_open:
                await scope.aclose()
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
        from .plugin_result_forwarding import forwarded_dependency_result

        forwarded = forwarded_dependency_result(current_resource_invocation.get(), result)
        if forwarded is not None:
            return _validate_public_result(forwarded, registration.tool_spec)
        if not isinstance(payload, ManagedArtifactPayload):
            return _validate_public_result(
                _project_public_plugin_result(result, content=payload), registration.tool_spec,
            )

        if result.is_error:
            return _managed_artifact_failure("managed_artifact_on_error")
        artifact_output = _managed_artifact_output(registration.descriptor)
        if artifact_output is None:
            return _managed_artifact_failure("managed_artifact_not_declared")
        if MANAGED_ARTIFACT_WRITE_PERMISSION not in registration.permissions:
            return _managed_artifact_failure("managed_artifact_permission_required")
        if self._managed_artifact_sink is None:
            return _managed_artifact_failure("managed_artifact_sink_unavailable")

        public_result = _validate_public_result(
            _project_public_plugin_result(result, content=payload.content), registration.tool_spec,
        )
        if public_result.is_error:
            return public_result

        if not isinstance(payload.artifacts, tuple) or not payload.artifacts:
            return _managed_artifact_failure("managed_artifacts_required")
        declared_max_bytes = int(artifact_output.max_bytes or 0)
        try:
            total_bytes = sum(validate_managed_artifact_draft(draft).file_size for draft in payload.artifacts)
        except ManagedArtifactError as exc:
            return _managed_artifact_failure(exc.reason)
        if any(draft.source_handles or draft.revision_of for draft in payload.artifacts) and RESOURCE_READ_PERMISSION not in registration.permissions:
            return _managed_artifact_failure("managed_artifact_lineage_resource_permission_required")
        if total_bytes > declared_max_bytes:
            return _managed_artifact_failure("managed_artifact_too_large")
        artifact_refs = []
        for draft in payload.artifacts:
            try:
                artifact_ref = await _await_with_optional_timeout(
                    self._managed_artifact_sink.materialize(
                        draft, context=context, capability_id=registration.descriptor.id,
                    ),
                    timeout_seconds=self._managed_artifact_timeout_seconds or None,
                )
                normalized = normalize_managed_artifact_reference(
                    artifact_ref, draft=draft, capability_id=registration.descriptor.id,
                )
                if normalized is None:
                    raise ManagedArtifactError("managed_artifact_invalid_reference")
                artifact_refs.append(normalized)
            except asyncio.CancelledError:
                if _current_task_is_cancelling():
                    raise
                return _managed_artifact_failure("managed_artifact_write_failed", artifacts=artifact_refs)
            except (TimeoutError, asyncio.TimeoutError):
                return _managed_artifact_failure("managed_artifact_write_timeout", artifacts=artifact_refs)
            except ManagedArtifactError as exc:
                return _managed_artifact_failure(exc.reason, artifacts=artifact_refs)
            except Exception:
                return _managed_artifact_failure("managed_artifact_write_failed", artifacts=artifact_refs)

        if isinstance(public_result.content, Mapping):
            combined_content: dict[str, Any] = dict(public_result.content)
        else:
            combined_content = {"result": public_result.content}
        combined_content["managed_artifacts"] = artifact_refs
        return sanitize_capability_result(
            replace(public_result, content=combined_content)
        )

    async def _activate_plugin(
        self,
        selection: PluginSelection,
        entry_points: list[Any],
        *,
        reserved_capability_ids: frozenset[str],
        reserved_qq_commands: frozenset[str],
        reserved_skill_names: frozenset[str],
        check_health: bool = True,
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
            manifest = replace(manifest, connections=tuple(ConnectionSpec.from_dict(spec.as_dict()) for spec in manifest.connections))
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
            registrar._set_background_service_invocation_factory(
                self._background_service_invocation_factory
            )
            # Inject job permission flag if declared
            if RESOURCE_READ_PERMISSION in manifest.permissions:
                if self._resource_provider is None:
                    raise _ActivationFailure("resource_provider_unavailable")
                registrar._resource_port = ScopedPluginResourcePort(selection.plugin_id, self._resource_provider)
            if CAPABILITY_INVOKE_PERMISSION in manifest.permissions:
                if self._capability_provider is None:
                    raise _ActivationFailure("capability_provider_unavailable")
                registrar._capability_port = ScopedPluginCapabilityPort(selection.plugin_id, self._capability_provider)
            if permitted_connections(manifest.permissions):
                if self._connection_provider is None:
                    raise _ActivationFailure("connection_provider_unavailable")
                registrar._connection_port = ScopedPluginConnectionPort(
                    selection.plugin_id, self._connection_provider,
                    private_values=registrar._private_result_values,
                )
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
            if EVENT_SUBSCRIBE_PERMISSION in manifest.permissions:
                registrar._set_event_permission(True)
            if any(permission in manifest.permissions for permission in (EVENT_EMIT_PERMISSION, EVENT_SUBSCRIBE_PERMISSION, CONTEXT_OBSERVE_PERMISSION, AGENT_TURN_REQUEST_PERMISSION)):
                registrar._events_port = ScopedPluginEventsPort(
                    selection.plugin_id, self._events_provider or self.build_event_broker(),
                )
            if self._tasks_provider is not None:
                registrar._task_port = ScopedPluginTasksPort(selection.plugin_id, self._tasks_provider)
            if HOOK_SUBSCRIBE_PERMISSION in manifest.permissions:
                registrar._set_hook_permission(True)
            if SYSTEM_PROMPT_CONTRIBUTION_PERMISSION in manifest.permissions:
                registrar._set_prompt_permission(True)
            if SKILL_CONTRIBUTION_PERMISSION in manifest.permissions:
                registrar._set_skill_permission(True)
            register = getattr(plugin, "register", None)
            if not callable(register):
                raise _ActivationFailure("invalid_plugin_contract")
            try:
                register_result = register(registrar)
            except _ActivationFailure:
                raise
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
            if any(reg.subscription and not reg.subscription.subscription_id.startswith(selection.plugin_id + ".")
                   for reg in registrar.event_registrations):
                raise _ActivationFailure("event_subscription_id_invalid")
            if any(reg.command in reserved_qq_commands for reg in registrar.qq_commands):
                raise _ActivationFailure("qq_command_conflict")
            if any(reg.name in reserved_skill_names for reg in registrar.skills):
                raise _ActivationFailure("skill_name_conflict")
            if len(registrar.adapters) > _MAX_ADAPTERS_PER_PLUGIN:
                raise _ActivationFailure("too_many_plugin_adapters")
            if len({id(adapter) for adapter in registrar.adapters}) != len(registrar.adapters):
                raise _ActivationFailure("duplicate_plugin_adapter")

            registrations: list[_CapabilityRegistration] = []
            local_capability_ids: set[str] = set()
            for adapter in registrar.adapters:
                _validate_adapter_contract(adapter)
                if check_health and not manifest.requires_services:
                    await self._check_adapter_health(adapter)
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
                    validate_permissions = getattr(
                        self._contribution_policy,
                        "validate_capability_permissions",
                        None,
                    )
                    if callable(validate_permissions):
                        _require_policy_acceptance(
                            lambda: validate_permissions(
                                manifest=manifest,
                                descriptor=descriptor,
                            ),
                            stage="capability_permissions",
                        )
                    descriptor_snapshot = _copy_descriptor_snapshot(descriptor)
                    try:
                        canonical_spec = build_tool_spec(descriptor_snapshot)
                    except SchemaDefinitionError as exc:
                        raise _ActivationFailure(
                            exc.code, stage="capability_schema", schema_errors=({
                                "code": exc.code, "field": exc.path, "message": exc.detail,
                            },),
                        ) from None
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
                            tool_spec=canonical_spec,
                            private_result_values=registrar._private_result_values,
                            connections=manifest.connections,
                        )
                    )
                    if len(registrations) > _MAX_CAPABILITIES_PER_PLUGIN:
                        raise _ActivationFailure("too_many_plugin_capabilities")
            validate_registration = getattr(self._contribution_policy, "validate_registration", None)
            if callable(validate_registration):
                _require_policy_acceptance(
                    lambda: validate_registration(
                        manifest=manifest,
                        capability_count=sum(service_method_info(item.descriptor) is None for item in registrations),
                        qq_command_count=len(registrar.qq_commands),
                        event_subscription_count=len(registrar.event_registrations),
                        hook_handler_count=len(registrar.hook_registrations),
                        has_background_job=bool(registrar.background_services),
                        prompt_block_count=len(registrar.prompt_blocks),
                        skill_count=len(registrar.skills),
                    ),
                    stage="registration_contributions",
                )
            if not (
                registrations
                or registrar.qq_commands
                or registrar.event_registrations
                or registrar.hook_registrations
                or registrar.background_services
                or registrar.prompt_blocks
                or registrar.skills
            ):
                raise _ActivationFailure("plugin_registered_no_contributions")

            active = _ActivePlugin(
                plugin=plugin,
                adapters=registrar.adapters,
                capability_ids=tuple(registration.descriptor.id for registration in registrations),
                requires_services=manifest.requires_services,
                connections=manifest.connections,
                background_services=registrar.background_services,
                qq_command_registrations=tuple(
                    _PluginCommandRegistration(
                        plugin_id=selection.plugin_id,
                        command=reg.command,
                        handler=reg.handler,
                    )
                    for reg in registrar.qq_commands
                ),
                event_registrations=tuple(
                    _PluginEventRegistration(
                        plugin_id=selection.plugin_id,
                        event_type=reg.event_type,
                        handler=reg.handler,
                        subscription=reg.subscription,
                        permissions=tuple(manifest.permissions),
                        private_values=registrar._private_result_values,
                        connections=manifest.connections,
                    )
                    for reg in registrar.event_registrations
                ),
                hook_registrations=tuple(
                    _PluginHookRegistration(
                        plugin_id=selection.plugin_id,
                        hook_type=reg.hook_type,
                        handler=reg.handler,
                    )
                    for reg in registrar.hook_registrations
                ),
                prompt_block_registrations=tuple(registrar.prompt_blocks),
                skill_registrations=tuple(
                    replace(
                        registration,
                        mount_name=_plugin_skill_mount_name(selection.plugin_id, registration.name),
                    )
                    for registration in registrar.skills
                ),
            )
            return (
                PluginStatus(
                    plugin_id=_public_plugin_id(selection.plugin_id),
                    enabled=True,
                    status="active",
                    plugin_version=manifest.plugin_version,
                    permissions=tuple(manifest.permissions),
                    capability_ids=tuple(registration.descriptor.id for registration in registrations),
                    qq_commands=tuple(reg.command for reg in registrar.qq_commands),
                    event_types=tuple(reg.event_type for reg in registrar.event_registrations),
                    hook_types=tuple(reg.hook_type for reg in registrar.hook_registrations),
                    skill_names=tuple(reg.name for reg in registrar.skills),
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
                    schema_errors=exc.schema_errors,
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

    def _on_background_service_done(
        self,
        service_key: tuple[str, str],
        controller: _HostJobController,
        task: asyncio.Task,
    ) -> None:
        """Record one service outcome without exposing its exception text."""

        if controller.shutdown_requested or self._state in {"stopping", "stopped"}:
            try:
                task.exception()
            except (asyncio.CancelledError, Exception):
                pass
            previous = self._background_service_statuses.get(service_key, {})
            if previous.get("status") != "degraded":
                self._background_service_statuses[service_key] = {
                    "status": "stopped",
                    "reason": str(previous.get("reason") or ""),
                }
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
        self._background_service_statuses[service_key] = {
            "status": "failed",
            "reason": reason,
        }
        if self._state in _HOST_AVAILABLE_STATES:
            self._state = "degraded"

    async def _stop_background_services(self) -> None:
        """Signal every supervised service and await independent shutdown."""
        if not self._background_service_tasks:
            return
        for _service, controller, _task in self._background_service_tasks.values():
            controller.signal_shutdown()
        for service_key, (service, _controller, _task) in reversed(
            tuple(self._background_service_tasks.items())
        ):
            try:
                await asyncio.wait_for(service.stop(), timeout=self._close_timeout_seconds)
            except asyncio.CancelledError:
                if _current_task_is_cancelling():
                    raise
                self._record_background_service_stop_failure(service_key, "service_stop_cancelled")
            except Exception:
                self._record_background_service_stop_failure(service_key, "service_stop_failed")
        active_tasks = {
            service_key: task
            for service_key, (_service, _controller, task) in self._background_service_tasks.items()
            if not task.done()
        }
        if active_tasks:
            _done, pending = await asyncio.wait(
                tuple(active_tasks.values()),
                timeout=self._background_service_stop_timeout_seconds,
            )
            if pending:
                pending_set = set(pending)
                for service_key, task in active_tasks.items():
                    if task in pending_set:
                        self._record_background_service_stop_failure(
                            service_key,
                            "service_stop_timeout",
                        )
                for task in pending:
                    task.cancel()
                await asyncio.wait(pending, timeout=self._close_timeout_seconds)

    def _record_background_service_stop_failure(
        self,
        service_key: tuple[str, str],
        reason: str,
    ) -> None:
        self._background_service_stop_failure_count += 1
        self._background_service_statuses[service_key] = {
            "status": "degraded",
            "reason": reason,
        }

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
    try:
        validate_service_dependencies(manifest.requires_services)
        validate_connection_specs(manifest.connections, manifest.permissions)
    except ValueError as exc:
        raise _ActivationFailure(str(exc)) from None
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
        or len(manifest.permissions) > _MAX_PERMISSIONS_PER_PLUGIN
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
    if not isinstance(descriptor.raw, Mapping) or type(descriptor.raw.get("owner_only", False)) is not bool:
        raise _ActivationFailure("tool_owner_only_invalid")


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
    ):
        raise _ActivationFailure("managed_artifact_size_limit_invalid")
    if "filesystem" not in descriptor.effects:
        raise _ActivationFailure("managed_artifact_effect_required")


def _managed_artifact_output(descriptor: CapabilityDescriptor) -> Any | None:
    outputs = tuple(output for output in descriptor.outputs if output.delivery == "generated_file")
    return outputs[0] if len(outputs) == 1 else None


def _managed_artifact_failure(reason: str, *, artifacts: list | tuple = ()) -> CapabilityResult:
    return CapabilityResult(
        is_error=True,
        status="partial" if artifacts else "error",
        reason=str(reason or "managed_artifact_failed"),
        content={"managed_artifacts": list(artifacts)} if artifacts else None,
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
    candidate = replace(result, content=projected_content)
    if not candidate.is_error and not candidate.has_value:
        candidate = replace(candidate, value=content.content if isinstance(content, PluginResultPayload) else content)
    return sanitize_capability_result(candidate)


def _validate_public_result(result: CapabilityResult, spec: CapabilityToolSpec) -> CapabilityResult:
    if result.is_error:
        return result
    validation = validate_tool_spec_result(spec, result.value)
    if validation.ok:
        return result
    return CapabilityResult(
        is_error=True, status="result_validation_error", reason="plugin_result_schema_mismatch",
        content={
            "errors": [error.as_dict() for error in validation.errors],
            "execution_status": "completed", "retryable": False,
        },
    )


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
            input_schema=_copy_descriptor_value(descriptor.input_schema),
            output_schema=_copy_descriptor_value(descriptor.output_schema),
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


def _combined_invocation_timeout(
    invoke_timeout_seconds: float | None,
    managed_artifact_timeout_seconds: float,
) -> float | None:
    if invoke_timeout_seconds is None or managed_artifact_timeout_seconds <= 0:
        return None
    return invoke_timeout_seconds + managed_artifact_timeout_seconds + 0.5


async def _await_with_optional_timeout(awaitable: Any, *, timeout_seconds: float | None) -> Any:
    if timeout_seconds is None:
        return await awaitable
    return await asyncio.wait_for(awaitable, timeout=timeout_seconds)


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


def _plugin_skill_mount_name(plugin_id: str, skill_name: str) -> str:
    material = f"{plugin_id}\0{skill_name}".encode("utf-8")
    return f"plugin_skill_{hashlib.sha256(material).hexdigest()[:16]}"


def _build_contribution_snapshots(
    active_plugins: Mapping[str, _ActivePlugin],
    capabilities: Mapping[str, _CapabilityRegistration],
    *,
    generation: int,
) -> tuple[PluginContributionSnapshot, ...]:
    snapshots: list[PluginContributionSnapshot] = []
    for plugin_id, active in active_plugins.items():
        prompt_blocks = tuple(
            sorted(active.prompt_block_registrations, key=lambda item: item.block_id)
        )
        surfaces = {
            surface
            for capability_id in active.capability_ids
            for surface in tuple(
                getattr(
                    getattr(capabilities.get(capability_id), "descriptor", None),
                    "visible_in",
                    (),
                )
                or ()
            )
            if surface in {"desktop", "qq"}
        }
        if active.qq_command_registrations:
            surfaces.add("qq")
        if active.prompt_block_registrations or active.skill_registrations:
            surfaces.update(("desktop", "qq"))
        snapshots.append(
            PluginContributionSnapshot(
                plugin_id=_public_plugin_id(plugin_id),
                generation=generation,
                requires_services=active.requires_services,
                connections=active.connections,
                capability_ids=tuple(sorted(key for key in active.capability_ids
                    if service_method_info(capabilities[key].descriptor) is None)),
                service_methods=tuple(_copy_descriptor_snapshot(capabilities[key].descriptor)
                    for key in sorted(active.capability_ids) if service_method_info(capabilities[key].descriptor) is not None),
                qq_commands=tuple(
                    sorted(item.command for item in active.qq_command_registrations)
                ),
                event_types=tuple(
                    sorted(item.event_type for item in active.event_registrations if item.subscription is None)
                ),
                event_subscriptions=tuple(item.subscription for item in active.event_registrations if item.subscription),
                hook_types=tuple(
                    sorted(item.hook_type for item in active.hook_registrations)
                ),
                background_service_ids=tuple(
                    sorted(item.service_id for item in active.background_services)
                ),
                prompt_block_ids=tuple(item.block_id for item in prompt_blocks),
                prompt_character_count=sum(len(item.text) for item in prompt_blocks),
                skill_names=tuple(
                    sorted(item.name for item in active.skill_registrations)
                ),
                surfaces=tuple(sorted(surfaces)),
            )
        )
    snapshots.sort(key=lambda item: item.plugin_id)
    return tuple(snapshots)


def _current_task_is_cancelling() -> bool:
    task = asyncio.current_task()
    return bool(task is not None and task.cancelling())


__all__ = ["PluginContributionSnapshot", "PluginHost", "PluginStatus"]
