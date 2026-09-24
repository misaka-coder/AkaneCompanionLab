"""Host-owned policies for plugin contributions.

PluginHost owns lifecycle and transactional publication.  A contribution
policy owns the surfaces currently allowed by one host composition.  Keeping
these concerns separate prevents the deliberately narrow M65-C diagnostic
rules from becoming permanent PluginHost behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol

from capcore import CapabilityDescriptor
from akane_plugin.service_contracts import SERVICE_PROVIDE_PERMISSION, service_method_info
from akane_plugin.connections import connection_name_from_permission
from akane_plugin.policies import POLICY_PROVIDE_PERMISSION, POLICY_SERVICE_PREFIX
from .tool_continuation import declared_followup

from .plugin_api import (
    BACKGROUND_JOB_PERMISSION,
    CAPABILITY_PROMPT_INVOKE_PERMISSION,
    CAPABILITY_INVOKE_PERMISSION,
    DIAGNOSTICS_INVOKE_PERMISSION,
    EVENT_SUBSCRIBE_PERMISSION,
    EVENT_EMIT_PERMISSION,
    CONTEXT_OBSERVE_PERMISSION,
    AGENT_TURN_REQUEST_PERMISSION,
    HOOK_SUBSCRIBE_PERMISSION,
    MANAGED_ARTIFACT_WRITE_PERMISSION,
    RESOURCE_READ_PERMISSION,
    NETWORK_READ_PERMISSION,
    NOTIFICATION_SEND_PERMISSION,
    PLUGIN_QQ_COMMAND_PERMISSION,
    PLUGIN_STATE_EFFECT,
    PLUGIN_STORAGE_WRITE_PERMISSION,
    SKILL_CONTRIBUTION_PERMISSION,
    SYSTEM_PROMPT_CONTRIBUTION_PERMISSION,
    PluginManifest,
)


@dataclass(frozen=True, slots=True)
class ContributionPolicyDecision:
    accepted: bool

    @classmethod
    def allow(cls) -> "ContributionPolicyDecision":
        return cls(accepted=True)

    @classmethod
    def reject(cls) -> "ContributionPolicyDecision":
        return cls(accepted=False)


class PluginContributionPolicy(Protocol):
    policy_id: str

    def validate_manifest(self, manifest: PluginManifest) -> ContributionPolicyDecision: ...

    def validate_capability(
        self,
        *,
        plugin_id: str,
        descriptor: CapabilityDescriptor,
    ) -> ContributionPolicyDecision: ...

    # Optional compatibility hook. PluginHost calls it when present after the
    # registrar has produced a complete staged contribution snapshot.
    def validate_registration(
        self,
        *,
        manifest: PluginManifest,
        capability_count: int,
        qq_command_count: int,
        event_subscription_count: int,
        hook_handler_count: int,
        has_background_job: bool,
        prompt_block_count: int,
        skill_count: int,
    ) -> ContributionPolicyDecision: ...


def _validate_trusted_read_capability(
    descriptor: CapabilityDescriptor,
) -> ContributionPolicyDecision:
    if not descriptor.prompt_exposed:
        return ContributionPolicyDecision.reject()
    if descriptor.risk != "low":
        return ContributionPolicyDecision.reject()
    if descriptor.confirm != "never":
        return ContributionPolicyDecision.reject()
    artifact_outputs = tuple(output for output in descriptor.outputs if output.delivery == "generated_file")
    expected_effects = ("network", "filesystem") if artifact_outputs else ("network",)
    if descriptor.effects != expected_effects:
        return ContributionPolicyDecision.reject()
    return _validate_background_semantics(descriptor)


def _validate_background_semantics(
    descriptor: CapabilityDescriptor,
) -> ContributionPolicyDecision:
    raw = descriptor.raw if isinstance(descriptor.raw, Mapping) else {}
    model_followup = raw.get("model_followup", "required")
    if not isinstance(model_followup, str) or model_followup not in {"required", "optional"}:
        return ContributionPolicyDecision.reject()
    execution_class = str(raw.get("execution_class") or "sync").strip().lower()
    try:
        declared_followup(raw)
    except ValueError:
        return ContributionPolicyDecision.reject()
    completion_mode = str(raw.get("completion_mode") or "agent").strip().lower()
    memory_mode = str(raw.get("memory_mode") or "timeline").strip().lower()
    if execution_class not in {"sync", "long_task"}:
        return ContributionPolicyDecision.reject()
    # Direct delivery needs an explicit channel payload contract. Do not map it
    # to an Agent completion until that separate path actually exists.
    if completion_mode not in {"agent", "silent"}:
        return ContributionPolicyDecision.reject()
    if memory_mode not in {"current_turn", "timeline"}:
        return ContributionPolicyDecision.reject()
    return ContributionPolicyDecision.allow()


def _validate_trusted_stateful_capability(
    descriptor: CapabilityDescriptor,
) -> ContributionPolicyDecision:
    """Validate honest CapCore semantics without freezing a domain-effect list."""

    try:
        service = service_method_info(descriptor)
    except (ValueError, TypeError):
        return ContributionPolicyDecision.reject()
    if bool(service) == bool(descriptor.prompt_exposed):
        return ContributionPolicyDecision.reject()
    if descriptor.risk not in {"low", "medium", "high"}:
        return ContributionPolicyDecision.reject()
    if descriptor.confirm not in {"never", "first_time", "always"}:
        return ContributionPolicyDecision.reject()
    if descriptor.risk != "low" and descriptor.confirm == "never":
        return ContributionPolicyDecision.reject()
    artifact_outputs = tuple(output for output in descriptor.outputs if output.delivery == "generated_file")
    if artifact_outputs and "filesystem" not in descriptor.effects:
        return ContributionPolicyDecision.reject()
    return _validate_background_semantics(descriptor)


@dataclass(frozen=True, slots=True)
class M65CDiagnosticContributionPolicy:
    """Temporary M65-C policy: side-effect-free local diagnostic capabilities."""

    policy_id: str = field(default="m65c.diagnostic-capability.v1", init=False)

    def validate_manifest(self, manifest: PluginManifest) -> ContributionPolicyDecision:
        if manifest.permissions != (DIAGNOSTICS_INVOKE_PERMISSION,):
            return ContributionPolicyDecision.reject()
        return ContributionPolicyDecision.allow()

    def validate_capability(
        self,
        *,
        plugin_id: str,
        descriptor: CapabilityDescriptor,
    ) -> ContributionPolicyDecision:
        del plugin_id
        if descriptor.prompt_exposed:
            return ContributionPolicyDecision.reject()
        if descriptor.risk != "low":
            return ContributionPolicyDecision.reject()
        if descriptor.confirm != "never":
            return ContributionPolicyDecision.reject()
        if descriptor.effects:
            return ContributionPolicyDecision.reject()
        return ContributionPolicyDecision.allow()

    def validate_registration(self, **_kwargs: object) -> ContributionPolicyDecision:
        return ContributionPolicyDecision.allow()


@dataclass(frozen=True, slots=True)
class TrustedReadNetworkContributionPolicy:
    """Prompt-visible trusted reads, optionally producing one managed artifact."""

    policy_id: str = field(default="trusted.read-network-capability.v1", init=False)

    def validate_manifest(self, manifest: PluginManifest) -> ContributionPolicyDecision:
        if manifest.permissions not in {
            (
                CAPABILITY_PROMPT_INVOKE_PERMISSION,
                NETWORK_READ_PERMISSION,
            ),
            (
                CAPABILITY_PROMPT_INVOKE_PERMISSION,
                NETWORK_READ_PERMISSION,
                MANAGED_ARTIFACT_WRITE_PERMISSION,
            ),
        }:
            return ContributionPolicyDecision.reject()
        return ContributionPolicyDecision.allow()

    def validate_capability(
        self,
        *,
        plugin_id: str,
        descriptor: CapabilityDescriptor,
    ) -> ContributionPolicyDecision:
        del plugin_id
        return _validate_trusted_read_capability(descriptor)

    def validate_registration(
        self,
        *,
        capability_count: int,
        **_kwargs: object,
    ) -> ContributionPolicyDecision:
        return (
            ContributionPolicyDecision.allow()
            if capability_count > 0
            else ContributionPolicyDecision.reject()
        )


@dataclass(frozen=True, slots=True)
class TrustedStatefulPluginContributionPolicy:
    """Trusted in-process plugins with explicitly declared host services.

    A plugin may contribute capabilities, QQ commands, background services, or
    stable prompt blocks without pretending to provide all four. Capability
    plugins declare ``capability.prompt.invoke``. Network, scoped plugin state,
    and managed artifacts are independent permissions whose known effects are
    cross-checked against the manifest. CapCore remains authoritative for
    risk/confirmation semantics; this policy deliberately does not freeze an
    allowlist of future domain effects.
    """

    policy_id: str = field(default="trusted.stateful-plugin.v1", init=False)

    _ALLOWED = frozenset(
        {
            CAPABILITY_PROMPT_INVOKE_PERMISSION,
            SERVICE_PROVIDE_PERMISSION,
            POLICY_PROVIDE_PERMISSION,
            CAPABILITY_INVOKE_PERMISSION,
            NETWORK_READ_PERMISSION,
            PLUGIN_STORAGE_WRITE_PERMISSION,
            BACKGROUND_JOB_PERMISSION,
            NOTIFICATION_SEND_PERMISSION,
            MANAGED_ARTIFACT_WRITE_PERMISSION,
            PLUGIN_QQ_COMMAND_PERMISSION,
            RESOURCE_READ_PERMISSION,
            SKILL_CONTRIBUTION_PERMISSION,
            SYSTEM_PROMPT_CONTRIBUTION_PERMISSION,
            EVENT_SUBSCRIBE_PERMISSION,
            EVENT_EMIT_PERMISSION,
            CONTEXT_OBSERVE_PERMISSION,
            AGENT_TURN_REQUEST_PERMISSION,
            HOOK_SUBSCRIBE_PERMISSION,
        }
    )
    _CONTRIBUTION_PERMISSIONS = frozenset(
        {
            CAPABILITY_PROMPT_INVOKE_PERMISSION,
            SERVICE_PROVIDE_PERMISSION,
            BACKGROUND_JOB_PERMISSION,
            PLUGIN_QQ_COMMAND_PERMISSION,
            SKILL_CONTRIBUTION_PERMISSION,
            SYSTEM_PROMPT_CONTRIBUTION_PERMISSION,
            EVENT_SUBSCRIBE_PERMISSION,
            HOOK_SUBSCRIBE_PERMISSION,
        }
    )

    def validate_manifest(self, manifest: PluginManifest) -> ContributionPolicyDecision:
        perms = frozenset(manifest.permissions)
        if not perms or any(p not in self._ALLOWED and connection_name_from_permission(p) is None for p in perms):
            return ContributionPolicyDecision.reject()
        if not perms.intersection(self._CONTRIBUTION_PERMISSIONS):
            return ContributionPolicyDecision.reject()
        if MANAGED_ARTIFACT_WRITE_PERMISSION in perms and not perms.intersection({CAPABILITY_PROMPT_INVOKE_PERMISSION, SERVICE_PROVIDE_PERMISSION}):
            return ContributionPolicyDecision.reject()
        return ContributionPolicyDecision.allow()

    def validate_capability(
        self,
        *,
        plugin_id: str,
        descriptor: CapabilityDescriptor,
    ) -> ContributionPolicyDecision:
        del plugin_id
        return _validate_trusted_stateful_capability(descriptor)

    def validate_capability_permissions(
        self,
        *,
        manifest: PluginManifest,
        descriptor: CapabilityDescriptor,
    ) -> ContributionPolicyDecision:
        permissions = frozenset(manifest.permissions)
        if service_method_info(descriptor) and SERVICE_PROVIDE_PERMISSION not in permissions:
            return ContributionPolicyDecision.reject()
        service = service_method_info(descriptor)
        if service and service["service_id"].startswith(POLICY_SERVICE_PREFIX):
            if POLICY_PROVIDE_PERMISSION not in permissions:
                return ContributionPolicyDecision.reject()
            from .execution_policies import valid_policy_descriptor
            if not valid_policy_descriptor(descriptor):
                return ContributionPolicyDecision.reject()
        effects = frozenset(descriptor.effects)
        if "network" in effects and NETWORK_READ_PERMISSION not in permissions:
            return ContributionPolicyDecision.reject()
        if PLUGIN_STATE_EFFECT in effects and PLUGIN_STORAGE_WRITE_PERMISSION not in permissions:
            return ContributionPolicyDecision.reject()
        if any(output.delivery == "generated_file" for output in descriptor.outputs):
            if MANAGED_ARTIFACT_WRITE_PERMISSION not in permissions:
                return ContributionPolicyDecision.reject()
        return ContributionPolicyDecision.allow()

    def validate_registration(
        self,
        *,
        manifest: PluginManifest,
        capability_count: int,
        **_kwargs: object,
    ) -> ContributionPolicyDecision:
        if capability_count and CAPABILITY_PROMPT_INVOKE_PERMISSION not in manifest.permissions:
            return ContributionPolicyDecision.reject()
        return ContributionPolicyDecision.allow()


__all__ = [
    "ContributionPolicyDecision",
    "M65CDiagnosticContributionPolicy",
    "PluginContributionPolicy",
    "TrustedReadNetworkContributionPolicy",
    "TrustedStatefulPluginContributionPolicy",
]
