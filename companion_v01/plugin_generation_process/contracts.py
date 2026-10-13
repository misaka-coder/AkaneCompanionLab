"""Public failures and validated ready declarations for one plugin process."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping
from capcore import CapabilityDescriptor, CapabilityResult
from ..plugin_api import PluginQQCommandResult
from ..plugin_hooks import PluginHookDispatchResult
from ..plugin_generation_codec import PluginGenerationCodecError, capability_descriptor_from_wire
from ..plugin_qq_commands import COMMAND_FAILURE_REPLY



class PluginGenerationError(RuntimeError):
    """A public, path-free generation lifecycle failure."""

    def __init__(self, reason: str, *, schema_errors=()) -> None:
        self.reason = str(reason or "plugin_generation_failed")
        self.schema_errors = tuple(dict(error) for error in schema_errors)
        super().__init__(self.reason)


def _decode_capability_snapshot(value: object) -> Mapping[str, CapabilityDescriptor]:
    if not isinstance(value, list):
        raise PluginGenerationError("plugin_generation_protocol_invalid")
    descriptors: dict[str, CapabilityDescriptor] = {}
    try:
        for item in value:
            descriptor = capability_descriptor_from_wire(item)
            if descriptor.id in descriptors:
                raise PluginGenerationCodecError("duplicate_capability")
            descriptors[descriptor.id] = descriptor
    except PluginGenerationCodecError as exc:
        raise PluginGenerationError("plugin_generation_protocol_invalid") from exc
    return MappingProxyType(descriptors)


def _decode_event_types(value: object) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        raise PluginGenerationError("plugin_generation_protocol_invalid")
    raw_event_types = value.get("event_handlers", [])
    if not isinstance(raw_event_types, list) or any(
        not isinstance(item, str) for item in raw_event_types
    ):
        raise PluginGenerationError("plugin_generation_protocol_invalid")
    return tuple(sorted(set(raw_event_types)))


def _decode_hook_types(value: object) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        raise PluginGenerationError("plugin_generation_protocol_invalid")
    raw_hook_types = value.get("hooks", [])
    if not isinstance(raw_hook_types, list) or any(
        not isinstance(item, str) for item in raw_hook_types
    ):
        raise PluginGenerationError("plugin_generation_protocol_invalid")
    return tuple(sorted(set(raw_hook_types)))


def _decode_background_service_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        raise PluginGenerationError("plugin_generation_protocol_invalid")
    raw_service_ids = value.get("background_services", [])
    if not isinstance(raw_service_ids, list) or any(
        not isinstance(item, str) for item in raw_service_ids
    ):
        raise PluginGenerationError("plugin_generation_protocol_invalid")
    return tuple(sorted(set(raw_service_ids)))


def _decode_qq_commands(value: object) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        raise PluginGenerationError("plugin_generation_protocol_invalid")
    raw_commands = value.get("commands", [])
    if not isinstance(raw_commands, list) or any(
        not isinstance(item, str) for item in raw_commands
    ):
        raise PluginGenerationError("plugin_generation_protocol_invalid")
    return tuple(sorted(set(raw_commands)))


def _decode_stable_prompt_blocks(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise PluginGenerationError("plugin_generation_protocol_invalid")
    return tuple(value)


def _generation_hook_failure(status: str, reason: object) -> PluginHookDispatchResult:
    safe_reason = str(reason or "plugin_hook_dispatch_failed")
    return PluginHookDispatchResult(
        ok=False,
        status=str(status or "host_unavailable"),
        failures=(("host", safe_reason),),
    )


def _generation_qq_command_failure(reason: object) -> PluginQQCommandResult:
    return PluginQQCommandResult(
        handled=True,
        reply_text=COMMAND_FAILURE_REPLY,
        reason=str(reason or "plugin_generation_unavailable"),
    )


def _generation_artifact_failure(reason: str, *, artifacts: list | tuple = ()) -> CapabilityResult:
    return CapabilityResult(
        is_error=True,
        status="partial" if artifacts else "error",
        reason=str(reason or "managed_artifact_write_failed"),
        content={"managed_artifacts": list(artifacts)} if artifacts else None,
    )
