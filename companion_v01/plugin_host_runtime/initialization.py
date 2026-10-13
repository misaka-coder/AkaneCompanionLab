"""Inert host configuration, empty generation state and synchronization."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping
import asyncio

from capcore import CapabilityAdapter

from ..instance_profile import PluginSelection
from ..plugin_api import is_valid_capability_id
from ..plugin_contribution_policy import PluginContributionPolicy
from ..plugin_events import DEFAULT_EVENT_HANDLER_TIMEOUT_SECONDS, PluginEventBroker
from ..plugin_hooks import DEFAULT_HOOK_HANDLER_TIMEOUT_SECONDS, PluginHookBroker
from ..plugin_jobs import _HostJobController
from ..plugin_managed_artifacts import ManagedArtifactSink
from ..plugin_notifications import _NotificationDeliveryLedger
from ..plugin_storage import PluginStorageService

from .constants import _public_plugin_id
from .loading import _installed_plugin_entry_points
from .models import PluginContributionSnapshot, PluginStatus, _ActivePlugin, _CapabilityRegistration


def initialize_host(
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
    default_entry_points_provider: Callable[[], Iterable[Any]] = _installed_plugin_entry_points,
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
    self._entry_points_provider = entry_points_provider or default_entry_points_provider
    self._activation_timeout_seconds = max(0.1, float(activation_timeout_seconds))
    self._invoke_timeout_seconds = (
        None if invoke_timeout_seconds is None else max(0.1, float(invoke_timeout_seconds))
    )
    self._managed_artifact_timeout_seconds = max(0.0, float(managed_artifact_timeout_seconds))
    self._resource_provider: Any = None
    self._capability_provider: Any = None
    self._connection_provider: Any = None
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
