"""Host-owned policies for plugin contributions.

PluginHost owns lifecycle and transactional publication.  A contribution
policy owns the surfaces currently allowed by one host composition.  Keeping
these concerns separate prevents the deliberately narrow M65-C diagnostic
rules from becoming permanent PluginHost behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from capcore import CapabilityDescriptor

from .plugin_api import (
    CAPABILITY_PROMPT_INVOKE_PERMISSION,
    DIAGNOSTICS_INVOKE_PERMISSION,
    MANAGED_ARTIFACT_WRITE_PERMISSION,
    NETWORK_READ_PERMISSION,
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
        return ContributionPolicyDecision.allow()


__all__ = [
    "ContributionPolicyDecision",
    "M65CDiagnosticContributionPolicy",
    "PluginContributionPolicy",
    "TrustedReadNetworkContributionPolicy",
]
