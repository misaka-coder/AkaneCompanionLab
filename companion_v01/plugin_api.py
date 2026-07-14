"""Public M65-C contract for trusted, in-process Akane plugins.

This module intentionally contains contracts only.  It does not discover,
load, activate, or retain plugins and must remain safe to import from an
installed plugin artifact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from capcore import CapabilityAdapter


AKANE_PLUGIN_API_VERSION = 1
AKANE_PLUGIN_ENTRYPOINT_GROUP = "akane.plugins.v1"
DIAGNOSTICS_INVOKE_PERMISSION = "diagnostics.invoke"
CAPABILITY_PROMPT_INVOKE_PERMISSION = "capability.prompt.invoke"
NETWORK_READ_PERMISSION = "network.read"
MANAGED_ARTIFACT_WRITE_PERMISSION = "artifact.write"
MAX_MANAGED_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_PLUGIN_ID_LENGTH = 64
MAX_CAPABILITY_ID_LENGTH = 128
MAX_PERMISSION_ID_LENGTH = 64

_PLUGIN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_CAPABILITY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_PERMISSION_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def is_valid_plugin_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= MAX_PLUGIN_ID_LENGTH
        and _PLUGIN_ID_PATTERN.fullmatch(value) is not None
    )


def is_valid_capability_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= MAX_CAPABILITY_ID_LENGTH
        and _CAPABILITY_ID_PATTERN.fullmatch(value) is not None
    )


def is_valid_permission_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= MAX_PERMISSION_ID_LENGTH
        and _PERMISSION_ID_PATTERN.fullmatch(value) is not None
    )


@dataclass(frozen=True, slots=True)
class PluginManifest:
    plugin_id: str
    plugin_version: str
    plugin_api_version: int
    permissions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PluginResultExperience:
    """Domain semantics for Akane to project; never a free-form prompt."""

    summary: str
    facts: tuple[str, ...] = ()
    as_of: str = ""
    warnings: tuple[str, ...] = ()
    interpretation_notes: tuple[str, ...] = ()
    suggested_next_actions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PluginResultPayload:
    """Public capability data plus bounded domain semantics for Akane."""

    content: Any
    experience: PluginResultExperience


@dataclass(frozen=True, slots=True)
class ManagedArtifactDraft:
    """Path-free bytes proposed by a trusted plugin for host-owned storage."""

    data: bytes
    title: str
    output_format: str
    mime_type: str
    summary: str = ""
    send_to_user: bool = True


@dataclass(frozen=True, slots=True)
class ManagedArtifactPayload:
    """Capability content plus at most one artifact committed by PluginHost."""

    content: Any
    artifact: ManagedArtifactDraft


class PluginRegistrar(Protocol):
    def add_capability_adapter(self, adapter: CapabilityAdapter) -> None: ...


class AkanePlugin(Protocol):
    manifest: PluginManifest

    def register(self, registrar: PluginRegistrar) -> None: ...


__all__ = [
    "AKANE_PLUGIN_API_VERSION",
    "AKANE_PLUGIN_ENTRYPOINT_GROUP",
    "CAPABILITY_PROMPT_INVOKE_PERMISSION",
    "DIAGNOSTICS_INVOKE_PERMISSION",
    "MANAGED_ARTIFACT_WRITE_PERMISSION",
    "MAX_MANAGED_ARTIFACT_BYTES",
    "NETWORK_READ_PERMISSION",
    "AkanePlugin",
    "ManagedArtifactDraft",
    "ManagedArtifactPayload",
    "PluginManifest",
    "PluginRegistrar",
    "PluginResultExperience",
    "PluginResultPayload",
    "is_valid_capability_id",
    "is_valid_permission_id",
    "is_valid_plugin_id",
]
