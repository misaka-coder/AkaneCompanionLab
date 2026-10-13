"""Stable entry for the versioned managed PluginHost process boundary.

Implementation lives in plugin_generation_process; the worker command remains compatible.
"""

from __future__ import annotations

from .plugin_generation_process.invocation import (
    asyncio as asyncio, sys as sys, asdict as asdict, replace as replace,
    InvocationContext as InvocationContext, Event as Event,
    consume_generation_artifact as consume_generation_artifact,
    discard_generation_artifacts as discard_generation_artifacts,
    is_generation_artifact_reference as is_generation_artifact_reference,
    capability_result_from_wire as capability_result_from_wire,
    invocation_context_to_wire as invocation_context_to_wire,
    ManagedArtifactError as ManagedArtifactError,
    normalize_managed_artifact_reference as normalize_managed_artifact_reference, drain as drain,
)
from .plugin_generation_process.transport import (
    json as json, queue as queue, uuid as uuid, TextIO as TextIO,
    PLUGIN_GENERATION_PROTOCOL as PLUGIN_GENERATION_PROTOCOL,
)
from .plugin_generation_process.lifecycle import (
    shutil as shutil, subprocess as subprocess, threading as threading, time as time, Any as Any,
    EventSubscription as EventSubscription, ConnectionSpec as ConnectionSpec,
    validate_connection_specs as validate_connection_specs,
    validate_event_subscription as validate_event_subscription, json_snapshot as json_snapshot,
    PluginGenerationSkillError as PluginGenerationSkillError,
    decode_generation_skill_roots as decode_generation_skill_roots,
)
from .plugin_generation_process.process import (
    Path as Path, is_valid_plugin_id as is_valid_plugin_id,
    is_valid_permission_id as is_valid_permission_id,
    GenerationHostCallbackRouter as GenerationHostCallbackRouter,
    capability_descriptor_to_wire as capability_descriptor_to_wire,
    PLUGIN_GENERATION_START_TIMEOUT_SECONDS as PLUGIN_GENERATION_START_TIMEOUT_SECONDS,
    PLUGIN_GENERATION_STOP_TIMEOUT_SECONDS as PLUGIN_GENERATION_STOP_TIMEOUT_SECONDS,
    generation_skill_export_dir as generation_skill_export_dir,
    ManagedArtifactSink as ManagedArtifactSink, ContributedSkillRoot as ContributedSkillRoot,
    PluginGenerationProcess as PluginGenerationProcess,
    PluginQQCommandBroker as PluginQQCommandBroker,
    PluginGenerationQQCommandBroker as PluginGenerationQQCommandBroker,
)
from .plugin_generation_process.contracts import (
    MappingProxyType as MappingProxyType, Mapping as Mapping,
    CapabilityDescriptor as CapabilityDescriptor, CapabilityResult as CapabilityResult,
    PluginQQCommandResult as PluginQQCommandResult,
    PluginHookDispatchResult as PluginHookDispatchResult,
    PluginGenerationCodecError as PluginGenerationCodecError,
    capability_descriptor_from_wire as capability_descriptor_from_wire,
    COMMAND_FAILURE_REPLY as COMMAND_FAILURE_REPLY, PluginGenerationError as PluginGenerationError,
    _decode_capability_snapshot as _decode_capability_snapshot,
    _decode_event_types as _decode_event_types, _decode_hook_types as _decode_hook_types,
    _decode_background_service_ids as _decode_background_service_ids,
    _decode_qq_commands as _decode_qq_commands,
    _decode_stable_prompt_blocks as _decode_stable_prompt_blocks,
    _generation_hook_failure as _generation_hook_failure,
    _generation_qq_command_failure as _generation_qq_command_failure,
    _generation_artifact_failure as _generation_artifact_failure,
)
from .plugin_generation_process.dispatch import (
    PluginHookEnvelope as PluginHookEnvelope,
    plugin_hook_dispatch_result_from_wire as plugin_hook_dispatch_result_from_wire,
    plugin_hook_envelope_to_wire as plugin_hook_envelope_to_wire,
    plugin_qq_command_result_from_wire as plugin_qq_command_result_from_wire,
    qq_command_dispatch_to_wire as qq_command_dispatch_to_wire,
)


def main(argv: list[str] | None = None) -> int:
    # Keep the historical module command as an alias of the worker entry.
    from .plugin_generation_worker import main as worker_main
    return worker_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PLUGIN_GENERATION_PROTOCOL",
    "PLUGIN_GENERATION_START_TIMEOUT_SECONDS",
    "PLUGIN_GENERATION_STOP_TIMEOUT_SECONDS",
    "PluginGenerationError",
    "PluginGenerationProcess",
    "PluginGenerationQQCommandBroker",
]
