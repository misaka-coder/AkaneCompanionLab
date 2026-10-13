"""Stable active-generation publication, scope and invocation entry.

Implementation lives in active_generation_runtime; historical imports stay available.
"""

from __future__ import annotations

from .active_generation_runtime.snapshot import (
    PluginActiveGenerationError as PluginActiveGenerationError,
    PluginGenerationEndpoint as PluginGenerationEndpoint,
    PluginGenerationSnapshot as PluginGenerationSnapshot,
    deepcopy as deepcopy,
    MappingProxyType as MappingProxyType,
    Any as Any,
    Mapping as Mapping,
    Protocol as Protocol,
    CapabilityDescriptor as CapabilityDescriptor,
    CapabilityResult as CapabilityResult,
    InvocationContext as InvocationContext,
    Event as Event,
    EventSubscription as EventSubscription,
    PluginSelection as PluginSelection,
    PluginHookEnvelope as PluginHookEnvelope,
    PluginQQCommandResult as PluginQQCommandResult,
    is_valid_plugin_id as is_valid_plugin_id,
    PluginHookDispatchResult as PluginHookDispatchResult,
    ContributedSkillRoot as ContributedSkillRoot,
)
from .active_generation_runtime.bindings import (
    _ProcessLifetime as _ProcessLifetime,
    _CapabilityGrant as _CapabilityGrant,
    PluginGenerationScope as PluginGenerationScope,
    asyncio as asyncio,
    dataclass as dataclass,
    field as field,
    contextmanager as contextmanager,
    current_generation_scope as current_generation_scope,
    generation_scopes as generation_scopes,
    use_generation_scopes as use_generation_scopes,
)
from .active_generation_runtime.lifecycle import (
    service_method_info as service_method_info,
    drain as drain,
)
from .active_generation_runtime.dispatch import (
    _PluginEventRegistration as _PluginEventRegistration,
    COMMAND_FAILURE_REPLY as COMMAND_FAILURE_REPLY,
)
from .active_generation_runtime.runtime import (
    PluginCapabilityBinding as PluginCapabilityBinding,
    ActivePluginGeneration as ActivePluginGeneration,
    ActiveGenerationEventBroker as ActiveGenerationEventBroker,
    ActiveGenerationHookBroker as ActiveGenerationHookBroker,
    ActiveGenerationQQCommandBroker as ActiveGenerationQQCommandBroker,
    threading as threading,
    PluginEventBroker as PluginEventBroker,
    PluginQQCommandBroker as PluginQQCommandBroker,
)


__all__ = [
    "ActiveGenerationEventBroker",
    "ActiveGenerationHookBroker",
    "ActiveGenerationQQCommandBroker",
    "ActivePluginGeneration",
    "PluginActiveGenerationError",
    "PluginGenerationEndpoint",
    "PluginGenerationSnapshot",
]
