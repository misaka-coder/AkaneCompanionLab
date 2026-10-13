"""Staged synchronous plugin registration with scoped permission checks."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any
import unicodedata

from capcore import CapabilityAdapter

from ..plugin_api import PluginRegistrar
from ..plugin_events import _PluginEventRegistration, validate_event_subscription
from ..plugin_hooks import SUPPORTED_HOOK_TYPES, _PluginHookRegistration
from ..plugin_qq_commands import _PluginCommandRegistration
from ..skill_runtime import SkillError, validate_contributed_skill_root

from .constants import (
    _BACKGROUND_SERVICE_ID_PATTERN,
    _MAX_PROMPT_BLOCKS_PER_PLUGIN,
    _MAX_PROMPT_BLOCK_CHARS,
    _MAX_PROMPT_BLOCK_TOTAL_CHARS,
    _MAX_QQ_COMMANDS_PER_PLUGIN,
    _MAX_QQ_COMMAND_LENGTH,
    _PROMPT_BLOCK_ID_PATTERN,
    _QQ_COMMAND_PATTERN,
)
from .models import (
    _ActivationFailure,
    _BackgroundServiceRegistration,
    _PromptBlockRegistration,
    _SkillRegistration,
)


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
