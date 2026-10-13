"""Detached capability descriptor snapshots."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any, Mapping

from capcore import CapabilityDescriptor

from .models import _ActivationFailure


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
