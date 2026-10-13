"""Adapter, capability and contribution-policy contracts."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from capcore import CapabilityDescriptor

from ..plugin_api import MANAGED_ARTIFACT_WRITE_PERMISSION, PluginManifest, is_valid_capability_id
from ..plugin_contribution_policy import ContributionPolicyDecision

from .models import _ActivationFailure


def _validate_adapter_contract(adapter: Any) -> None:
    if not str(getattr(adapter, "provider_id", "") or "").strip():
        raise _ActivationFailure("invalid_capability_adapter")
    for method_name in ("health", "list_capabilities", "invoke", "aclose"):
        if not callable(getattr(adapter, method_name, None)):
            raise _ActivationFailure("invalid_capability_adapter")


def _validate_descriptor_contract(descriptor: Any, *, plugin_id: str) -> None:
    if not isinstance(descriptor, CapabilityDescriptor):
        raise _ActivationFailure("invalid_capability_descriptor")
    if not is_valid_capability_id(descriptor.id):
        raise _ActivationFailure("invalid_capability_id")
    if not descriptor.id.startswith(f"{plugin_id}."):
        raise _ActivationFailure("capability_prefix_mismatch")
    if not isinstance(descriptor.raw, Mapping) or type(descriptor.raw.get("owner_only", False)) is not bool:
        raise _ActivationFailure("tool_owner_only_invalid")


def _validate_managed_artifact_descriptor_contract(
    descriptor: CapabilityDescriptor,
    *,
    manifest: PluginManifest,
) -> None:
    artifact_outputs = tuple(
        output for output in descriptor.outputs if output.delivery == "generated_file"
    )
    if not artifact_outputs:
        return
    if len(artifact_outputs) != 1:
        raise _ActivationFailure("managed_artifact_output_count_invalid")
    if MANAGED_ARTIFACT_WRITE_PERMISSION not in manifest.permissions:
        raise _ActivationFailure("managed_artifact_permission_required")
    output = artifact_outputs[0]
    if output.kind != "file" or not output.required:
        raise _ActivationFailure("managed_artifact_output_invalid")
    if (
        isinstance(output.max_bytes, bool)
        or not isinstance(output.max_bytes, int)
        or output.max_bytes <= 0
    ):
        raise _ActivationFailure("managed_artifact_size_limit_invalid")
    if "filesystem" not in descriptor.effects:
        raise _ActivationFailure("managed_artifact_effect_required")


def _require_policy_acceptance(evaluate: Callable[[], Any], *, stage: str) -> None:
    try:
        decision = evaluate()
    except Exception:
        raise _ActivationFailure("contribution_policy_failed", stage=stage) from None
    if not isinstance(decision, ContributionPolicyDecision):
        raise _ActivationFailure("contribution_policy_failed", stage=stage)
    if not decision.accepted:
        raise _ActivationFailure("contribution_policy_rejected", stage=stage)
