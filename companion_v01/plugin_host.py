"""Lifecycle host for explicitly allowlisted, trusted in-process plugins."""

from __future__ import annotations

from typing import Any, Callable, Iterable

from .plugin_host_runtime.activation import PluginActivationMixin
from .plugin_host_runtime.artifacts import PluginResultMixin
from .plugin_host_runtime.background import PluginBackgroundMixin
from .plugin_host_runtime.bindings import PluginBindingMixin
from .plugin_host_runtime.cleanup import PluginCleanupMixin
from .plugin_host_runtime.constants import (
    _BACKGROUND_SERVICE_ID_PATTERN,
    _HOST_AVAILABLE_STATES,
    _MAX_ADAPTERS_PER_PLUGIN,
    _MAX_BACKGROUND_SERVICE_ID_CHARS,
    _MAX_CAPABILITIES_PER_PLUGIN,
    _MAX_PERMISSIONS_PER_PLUGIN,
    _MAX_PROMPT_BLOCKS_PER_PLUGIN,
    _MAX_PROMPT_BLOCK_CHARS,
    _MAX_PROMPT_BLOCK_TOTAL_CHARS,
    _MAX_QQ_COMMANDS_PER_PLUGIN,
    _MAX_QQ_COMMAND_LENGTH,
    _PROMPT_BLOCK_ID_PATTERN,
    _QQ_COMMAND_PATTERN,
    _SAFE_VERSION_PATTERN,
    _is_safe_version,
    _plugin_skill_mount_name,
    _public_plugin_id,
)
from .plugin_host_runtime.consumer import PluginConsumerMixin
from .plugin_host_runtime.contracts import (
    _require_policy_acceptance,
    _validate_adapter_contract,
    _validate_descriptor_contract,
    _validate_managed_artifact_descriptor_contract,
)
from .plugin_host_runtime.contributions import _build_contribution_snapshots
from .plugin_host_runtime.descriptors import _copy_descriptor_snapshot, _copy_descriptor_value
from .plugin_host_runtime.initialization import initialize_host
from .plugin_host_runtime.invocation import PluginInvocationMixin
from .plugin_host_runtime.lifecycle import PluginLifecycleMixin
from .plugin_host_runtime.loading import (
    _installed_plugin_entry_points,
    _validate_manifest,
    _validate_zero_parameter_factory,
)
from .plugin_host_runtime.models import (
    PluginContributionSnapshot,
    PluginStatus,
    _ActivationFailure,
    _ActivePlugin,
    _BackgroundServiceRegistration,
    _CapabilityRegistration,
    _PromptBlockRegistration,
    _SkillRegistration,
)
from .plugin_host_runtime.registrar import _StagedRegistrar
from .plugin_host_runtime.results import (
    _managed_artifact_failure,
    _managed_artifact_output,
    _project_public_plugin_result,
    _validate_public_result,
)
from .plugin_host_runtime.snapshots import PluginSnapshotMixin
from .plugin_host_runtime.timing import (
    _await_with_optional_timeout,
    _bounded_adapter_call,
    _combined_invocation_timeout,
    _current_task_is_cancelling,
)

from .instance_profile import PluginSelection
from .plugin_contribution_policy import PluginContributionPolicy
from .plugin_events import DEFAULT_EVENT_HANDLER_TIMEOUT_SECONDS
from .plugin_hooks import DEFAULT_HOOK_HANDLER_TIMEOUT_SECONDS


class PluginHost(
    PluginActivationMixin,
    PluginLifecycleMixin,
    PluginBackgroundMixin,
    PluginCleanupMixin,
    PluginSnapshotMixin,
    PluginBindingMixin,
    PluginInvocationMixin,
    PluginConsumerMixin,
    PluginResultMixin,
):
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
        initialize_host(
            self, selections, contribution_policy=contribution_policy,
            entry_points_provider=entry_points_provider,
            activation_timeout_seconds=activation_timeout_seconds,
            invoke_timeout_seconds=invoke_timeout_seconds,
            managed_artifact_timeout_seconds=managed_artifact_timeout_seconds,
            close_timeout_seconds=close_timeout_seconds,
            event_handler_timeout_seconds=event_handler_timeout_seconds,
            hook_handler_timeout_seconds=hook_handler_timeout_seconds,
            default_entry_points_provider=_installed_plugin_entry_points,
        )


__all__ = ["PluginContributionSnapshot", "PluginHost", "PluginStatus"]
