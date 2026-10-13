"""Public result projection and output-schema validation."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from capcore import (
    CapabilityDescriptor,
    CapabilityToolSpec,
    CapabilityResult,
    validate_tool_spec_result,
)

from ..plugin_api import PluginResultPayload
from ..plugin_result_experience import (
    PluginResultExperienceError,
    has_reserved_plugin_result_key,
    project_plugin_result_payload,
)
from ..plugin_result_projection import sanitize_capability_result


def _managed_artifact_output(descriptor: CapabilityDescriptor) -> Any | None:
    outputs = tuple(output for output in descriptor.outputs if output.delivery == "generated_file")
    return outputs[0] if len(outputs) == 1 else None


def _managed_artifact_failure(reason: str, *, artifacts: list | tuple = ()) -> CapabilityResult:
    return CapabilityResult(
        is_error=True,
        status="partial" if artifacts else "error",
        reason=str(reason or "managed_artifact_failed"),
        content={"managed_artifacts": list(artifacts)} if artifacts else None,
    )


def _project_public_plugin_result(
    result: CapabilityResult,
    *,
    content: Any,
) -> CapabilityResult:
    if isinstance(content, PluginResultPayload):
        if result.is_error:
            return _managed_artifact_failure("plugin_result_experience_on_error")
        try:
            projected_content = project_plugin_result_payload(content)
        except PluginResultExperienceError as exc:
            return _managed_artifact_failure(exc.reason)
    else:
        if has_reserved_plugin_result_key(content):
            return _managed_artifact_failure("plugin_result_reserved_key")
        projected_content = content
    candidate = replace(result, content=projected_content)
    if not candidate.is_error and not candidate.has_value:
        candidate = replace(candidate, value=content.content if isinstance(content, PluginResultPayload) else content)
    return sanitize_capability_result(candidate)


def _validate_public_result(result: CapabilityResult, spec: CapabilityToolSpec) -> CapabilityResult:
    if result.is_error:
        return result
    validation = validate_tool_spec_result(spec, result.value)
    if validation.ok:
        return result
    return CapabilityResult(
        is_error=True, status="result_validation_error", reason="plugin_result_schema_mismatch",
        content={
            "errors": [error.as_dict() for error in validation.errors],
            "execution_status": "completed", "retryable": False,
        },
    )
