"""Lifecycle host for explicitly allowlisted, trusted in-process plugins."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
import unicodedata
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
    EVENT_SUBSCRIBE_PERMISSION,
    HOOK_SUBSCRIBE_PERMISSION,
    MANAGED_ARTIFACT_WRITE_PERMISSION,
    MAX_MANAGED_ARTIFACT_BYTES,
    MODEL_REASONING_PERMISSION,
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
    MAX_EVENT_FIELD_CHARS,
    MAX_EVENT_FIELDS,
    MAX_EVENT_TOTAL_CHARS,
    PluginEventBroker,
    _PluginEventRegistration,
)
from .plugin_managed_artifacts import (
    ManagedArtifactError,
    ManagedArtifactSink,
    normalize_managed_artifact_reference,
)
from .plugin_notifications import _NotificationDeliveryLedger, _PluginScopedNotificationPort
from .plugin_qq_commands import PluginQQCommandBroker, _PluginCommandRegistration
from .plugin_reasoning import PluginScopedReasoningPort
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
    prompt_character_count: int = 0

    @property
    def contribution_types(self) -> tuple[str, ...]:
        kinds: list[str] = []
        if self.capability_ids:
            kinds.append("capabilities")
        if self.qq_commands:
            kinds.append("commands")
        if self.event_types:
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
            "commands": list(self.qq_commands),
            "event_handlers": list(self.event_types),
            "hooks": list(self.hook_types),
            "background_services": list(self.background_service_ids),
            "prompt_blocks": list(self.prompt_block_ids),
            "skills": list(self.skill_names),
            "prompt_character_count": self.prompt_character_count,
        }


@dataclass(frozen=True, slots=True)
class _CapabilityRegistration:
    plugin_id: str
    adapter: CapabilityAdapter
    descriptor: CapabilityDescriptor
    permissions: tuple[str, ...]


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
    background_services: tuple[_BackgroundServiceRegistration, ...] = ()
    qq_command_registrations: tuple[_PluginCommandRegistration, ...] = ()
    event_registrations: tuple[_PluginEventRegistration, ...] = ()
    hook_registrations: tuple[_PluginHookRegistration, ...] = ()
    prompt_block_registrations: tuple[_PromptBlockRegistration, ...] = ()
    skill_registrations: tuple[_SkillRegistration, ...] = ()


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
        self._background_services: list[_BackgroundServiceRegistration] = []
        self._job_permission: bool = False
        self._notification_port: Any = None  # NotificationPort | None
        self._notification_permission: bool = False
        self._reasoning_port: Any = None  # PluginReasoningPort | None
        self._reasoning_permission: bool = False
        self._qq_commands: list[_PluginCommandRegistration] = []
        self._qq_command_permission: bool = False
        self._event_registrations: list[_PluginEventRegistration] = []
        self._event_permission: bool = False
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
        self._background_services.append(
            _BackgroundServiceRegistration(service_id=service_id, service=service)
        )

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

    def get_reasoning_port(self) -> Any:
        if self._sealed:
            raise RuntimeError("plugin_registrar_sealed")
        if not self._reasoning_permission or self._reasoning_port is None:
            raise RuntimeError("reasoning_permission_required")
        return self._reasoning_port

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

    def add_event_handler(self, event_type: str, handler: Any) -> None:
        if self._sealed:
            raise RuntimeError("plugin_registrar_sealed")
        if not self._event_permission:
            raise RuntimeError("event_subscribe_permission_required")
        normalized = str(event_type or "").strip().lower()
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,119}", normalized) is None:
            raise RuntimeError("invalid_plugin_event_type")
        if not callable(getattr(handler, "handle_event", None)):
            raise RuntimeError("invalid_plugin_event_handler")
        if any(item.event_type == normalized for item in self._event_registrations):
            raise RuntimeError("duplicate_plugin_event_handler")
        self._event_registrations.append(
            _PluginEventRegistration(plugin_id="", event_type=normalized, handler=handler)
        )

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

    def _set_job_permission(self, allowed: bool) -> None:
        self._job_permission = allowed

    def _set_notification_port(self, port: Any) -> None:
        self._notification_port = port
        self._notification_permission = port is not None

    def _set_reasoning_port(self, port: Any) -> None:
        self._reasoning_port = port
        self._reasoning_permission = port is not None

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
        managed_artifact_timeout_seconds: float = 5.0,
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
        self._managed_artifact_timeout_seconds = max(0.1, float(managed_artifact_timeout_seconds))
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
        self._reasoning_port: Any = None  # PluginReasoningPort | None
        self._notification_ledger = _NotificationDeliveryLedger()
        self._background_service_tasks: dict[
            tuple[str, str], tuple[Any, _HostJobController, asyncio.Task]
        ] = {}
        self._background_service_statuses: dict[tuple[str, str], dict[str, str]] = {}
        self._background_service_stop_timeout_seconds: float = 10.0
        self._background_service_stop_failure_count = 0
        self._generation = 0
        self._hook_broker: PluginHookBroker | None = None

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
                len(item.event_types) for item in self._contribution_snapshots
            ),
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
                    "event_fields": MAX_EVENT_FIELDS,
                    "event_field_chars": MAX_EVENT_FIELD_CHARS,
                    "event_total_chars": MAX_EVENT_TOTAL_CHARS,
                },
                "timeouts": {
                    "activation_step_seconds": self._activation_timeout_seconds,
                    "invoke_seconds": self._invoke_timeout_seconds,
                    "managed_artifact_seconds": self._managed_artifact_timeout_seconds,
                    "adapter_close_seconds": self._close_timeout_seconds,
                    "background_service_stop_seconds": self._background_service_stop_timeout_seconds,
                    "event_handler_seconds": self._event_handler_timeout_seconds,
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

    def bind_reasoning_port(self, port: Any) -> None:
        """Bind host-owned model/tool reasoning before restart-only startup."""

        if self._state != "created":
            raise RuntimeError("plugin_host_already_started")
        if not callable(getattr(port, "analyze", None)):
            raise TypeError("invalid_reasoning_port")
        self._reasoning_port = port

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

        return PluginEventBroker(
            self._event_registrations_snapshot(),
            handler_timeout_seconds=self._event_handler_timeout_seconds,
            availability_provider=lambda: self._state in _HOST_AVAILABLE_STATES,
            registrations_provider=self._event_registrations_snapshot,
        )

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
            registration
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

    async def _start_once(self, *, allow_stopped: bool = False) -> dict[str, Any]:
        async with self._lifecycle_lock:
            if self._state in _HOST_AVAILABLE_STATES or self._state in {"starting", "stopping"}:
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

            self._plugin_statuses = tuple(statuses)
            self._active_plugins = MappingProxyType(dict(working_plugins))
            self._capabilities = MappingProxyType(dict(working_capabilities))
            self._activation_order = tuple(activation_order)
            self._generation += 1
            self._contribution_snapshots = _build_contribution_snapshots(
                working_plugins,
                generation=self._generation,
            )
            # Publish one independently supervised task for every registered
            # service.  The tuple key avoids ambiguous string concatenation and
            # lets sibling services keep running when one exits.
            service_tasks: dict[
                tuple[str, str], tuple[Any, _HostJobController, asyncio.Task]
            ] = {}
            self._background_service_statuses = {}
            for plugin_id, active_plugin in working_plugins.items():
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
            enabled_failures = any(status.enabled and status.status != "active" for status in statuses)
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
            return await _await_with_optional_timeout(
                asyncio.wrap_future(concurrent_future),
                timeout_seconds=_combined_invocation_timeout(
                    self._invoke_timeout_seconds,
                    self._managed_artifact_timeout_seconds,
                ),
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
                result = await _await_with_optional_timeout(
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
        normalized_artifact_ref = normalize_managed_artifact_reference(
            artifact_ref,
            draft=payload.artifact,
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
        reserved_skill_names: frozenset[str],
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
            if MODEL_REASONING_PERMISSION in manifest.permissions:
                if self._reasoning_port is None:
                    raise _ActivationFailure("reasoning_port_unavailable")
                registrar._set_reasoning_port(
                    PluginScopedReasoningPort(
                        delegate=self._reasoning_port,
                        availability_provider=lambda: self._state in _HOST_AVAILABLE_STATES,
                    )
                )
            # Inject QQ command permission flag if declared
            if PLUGIN_QQ_COMMAND_PERMISSION in manifest.permissions:
                registrar._set_qq_command_permission(True)
            if EVENT_SUBSCRIBE_PERMISSION in manifest.permissions:
                registrar._set_event_permission(True)
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
            validate_registration = getattr(self._contribution_policy, "validate_registration", None)
            if callable(validate_registration):
                _require_policy_acceptance(
                    lambda: validate_registration(
                        manifest=manifest,
                        capability_count=len(registrations),
                        qq_command_count=len(registrar.qq_commands),
                        event_handler_count=len(registrar.event_registrations),
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


def _combined_invocation_timeout(
    invoke_timeout_seconds: float | None,
    managed_artifact_timeout_seconds: float,
) -> float | None:
    if invoke_timeout_seconds is None:
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
    *,
    generation: int,
) -> tuple[PluginContributionSnapshot, ...]:
    snapshots: list[PluginContributionSnapshot] = []
    for plugin_id, active in active_plugins.items():
        prompt_blocks = tuple(
            sorted(active.prompt_block_registrations, key=lambda item: item.block_id)
        )
        snapshots.append(
            PluginContributionSnapshot(
                plugin_id=_public_plugin_id(plugin_id),
                generation=generation,
                capability_ids=tuple(sorted(active.capability_ids)),
                qq_commands=tuple(
                    sorted(item.command for item in active.qq_command_registrations)
                ),
                event_types=tuple(
                    sorted(item.event_type for item in active.event_registrations)
                ),
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
            )
        )
    snapshots.sort(key=lambda item: item.plugin_id)
    return tuple(snapshots)


def _current_task_is_cancelling() -> bool:
    task = asyncio.current_task()
    return bool(task is not None and task.cancelling())


__all__ = ["PluginContributionSnapshot", "PluginHost", "PluginStatus"]
