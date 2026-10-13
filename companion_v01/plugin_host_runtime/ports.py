"""Permission-scoped host services injected before plugin registration."""

from __future__ import annotations

from typing import Any

from ..plugin_api import (
    BACKGROUND_JOB_PERMISSION,
    EVENT_SUBSCRIBE_PERMISSION,
    EVENT_EMIT_PERMISSION,
    CONTEXT_OBSERVE_PERMISSION,
    AGENT_TURN_REQUEST_PERMISSION,
    HOOK_SUBSCRIBE_PERMISSION,
    RESOURCE_READ_PERMISSION,
    CAPABILITY_INVOKE_PERMISSION,
    NOTIFICATION_SEND_PERMISSION,
    PLUGIN_QQ_COMMAND_PERMISSION,
    PLUGIN_STORAGE_WRITE_PERMISSION,
    SKILL_CONTRIBUTION_PERMISSION,
    SYSTEM_PROMPT_CONTRIBUTION_PERMISSION,
    PluginManifest,
)
from ..plugin_capability_calls import ScopedPluginCapabilityPort
from ..plugin_connections import ScopedPluginConnectionPort, permitted_connections
from ..plugin_event_ports import ScopedPluginEventsPort
from ..plugin_notifications import _PluginScopedNotificationPort
from ..plugin_resources import ScopedPluginResourcePort
from ..plugin_task_ports import ScopedPluginTasksPort

from .constants import _HOST_AVAILABLE_STATES
from .models import _ActivationFailure
from .registrar import _StagedRegistrar


def bind_registrar_ports(host: Any, registrar: _StagedRegistrar, *,
                         plugin_id: str, manifest: PluginManifest) -> None:
    # If the plugin declares storage.write, resolve its scoped data dir
    # and inject it into the registrar BEFORE register() is called, so
    # the plugin can capture the path to initialise adapters with it.
    if PLUGIN_STORAGE_WRITE_PERMISSION in manifest.permissions:
        if host._storage_service is None:
            raise _ActivationFailure("storage_service_unavailable")
        try:
            plugin_data_dir = host._storage_service.get_plugin_data_dir(
                plugin_id
            )
        except (ValueError, OSError):
            raise _ActivationFailure("storage_dir_creation_failed") from None
        registrar._set_storage_dir(plugin_data_dir)
    registrar._set_background_service_invocation_factory(
        host._background_service_invocation_factory
    )
    # Inject job permission flag if declared
    if RESOURCE_READ_PERMISSION in manifest.permissions:
        if host._resource_provider is None:
            raise _ActivationFailure("resource_provider_unavailable")
        registrar._resource_port = ScopedPluginResourcePort(plugin_id, host._resource_provider)
    if CAPABILITY_INVOKE_PERMISSION in manifest.permissions:
        if host._capability_provider is None:
            raise _ActivationFailure("capability_provider_unavailable")
        registrar._capability_port = ScopedPluginCapabilityPort(plugin_id, host._capability_provider)
    if permitted_connections(manifest.permissions):
        if host._connection_provider is None:
            raise _ActivationFailure("connection_provider_unavailable")
        registrar._connection_port = ScopedPluginConnectionPort(
            plugin_id, host._connection_provider,
            private_values=registrar._private_result_values,
        )
    if BACKGROUND_JOB_PERMISSION in manifest.permissions:
        registrar._set_job_permission(True)
    # Inject notification port if declared and bound
    if NOTIFICATION_SEND_PERMISSION in manifest.permissions:
        if host._notification_port is None:
            raise _ActivationFailure("notification_port_unavailable")
        registrar._set_notification_port(
            _PluginScopedNotificationPort(
                plugin_id=plugin_id,
                delegate=host._notification_port,
                ledger=host._notification_ledger,
                availability_provider=lambda: host._state in _HOST_AVAILABLE_STATES,
            )
        )
    # Inject QQ command permission flag if declared
    if PLUGIN_QQ_COMMAND_PERMISSION in manifest.permissions:
        registrar._set_qq_command_permission(True)
    if EVENT_SUBSCRIBE_PERMISSION in manifest.permissions:
        registrar._set_event_permission(True)
    if any(permission in manifest.permissions for permission in (EVENT_EMIT_PERMISSION, EVENT_SUBSCRIBE_PERMISSION, CONTEXT_OBSERVE_PERMISSION, AGENT_TURN_REQUEST_PERMISSION)):
        registrar._events_port = ScopedPluginEventsPort(
            plugin_id, host._events_provider or host.build_event_broker(),
        )
    if host._tasks_provider is not None:
        registrar._task_port = ScopedPluginTasksPort(plugin_id, host._tasks_provider)
    if HOOK_SUBSCRIBE_PERMISSION in manifest.permissions:
        registrar._set_hook_permission(True)
    if SYSTEM_PROMPT_CONTRIBUTION_PERMISSION in manifest.permissions:
        registrar._set_prompt_permission(True)
    if SKILL_CONTRIBUTION_PERMISSION in manifest.permissions:
        registrar._set_skill_permission(True)
