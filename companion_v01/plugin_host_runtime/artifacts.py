"""Declared managed artifact writes and public capability result finalization."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping
import asyncio

from capcore import CapabilityResult, InvocationContext

from ..plugin_api import (
    MANAGED_ARTIFACT_WRITE_PERMISSION,
    RESOURCE_READ_PERMISSION,
    ManagedArtifactPayload,
)
from ..plugin_managed_artifacts import (
    ManagedArtifactError,
    normalize_managed_artifact_reference,
    validate_managed_artifact_draft,
)
from ..plugin_resources import current_resource_invocation
from ..plugin_result_projection import sanitize_capability_result

from .models import _CapabilityRegistration
from .results import (
    _managed_artifact_failure,
    _managed_artifact_output,
    _project_public_plugin_result,
    _validate_public_result,
)
from .timing import _await_with_optional_timeout, _current_task_is_cancelling


class PluginResultMixin:
    async def _finalize_capability_result(
        self,
        result: CapabilityResult,
        *,
        registration: _CapabilityRegistration,
        context: InvocationContext,
    ) -> CapabilityResult:
        payload = result.content
        from ..plugin_result_forwarding import forwarded_dependency_result

        forwarded = forwarded_dependency_result(current_resource_invocation.get(), result)
        if forwarded is not None:
            return _validate_public_result(forwarded, registration.tool_spec)
        if not isinstance(payload, ManagedArtifactPayload):
            return _validate_public_result(
                _project_public_plugin_result(result, content=payload), registration.tool_spec,
            )

        if result.is_error:
            return _managed_artifact_failure("managed_artifact_on_error")
        artifact_output = _managed_artifact_output(registration.descriptor)
        if artifact_output is None:
            return _managed_artifact_failure("managed_artifact_not_declared")
        if MANAGED_ARTIFACT_WRITE_PERMISSION not in registration.permissions:
            return _managed_artifact_failure("managed_artifact_permission_required")
        if self._managed_artifact_sink is None:
            return _managed_artifact_failure("managed_artifact_sink_unavailable")

        public_result = _validate_public_result(
            _project_public_plugin_result(result, content=payload.content), registration.tool_spec,
        )
        if public_result.is_error:
            return public_result

        if not isinstance(payload.artifacts, tuple) or not payload.artifacts:
            return _managed_artifact_failure("managed_artifacts_required")
        declared_max_bytes = int(artifact_output.max_bytes or 0)
        try:
            total_bytes = sum(validate_managed_artifact_draft(draft).file_size for draft in payload.artifacts)
        except ManagedArtifactError as exc:
            return _managed_artifact_failure(exc.reason)
        if any(draft.source_handles or draft.revision_of for draft in payload.artifacts) and RESOURCE_READ_PERMISSION not in registration.permissions:
            return _managed_artifact_failure("managed_artifact_lineage_resource_permission_required")
        if total_bytes > declared_max_bytes:
            return _managed_artifact_failure("managed_artifact_too_large")
        artifact_refs = []
        for draft in payload.artifacts:
            try:
                artifact_ref = await _await_with_optional_timeout(
                    self._managed_artifact_sink.materialize(
                        draft, context=context, capability_id=registration.descriptor.id,
                    ),
                    timeout_seconds=self._managed_artifact_timeout_seconds or None,
                )
                normalized = normalize_managed_artifact_reference(
                    artifact_ref, draft=draft, capability_id=registration.descriptor.id,
                )
                if normalized is None:
                    raise ManagedArtifactError("managed_artifact_invalid_reference")
                artifact_refs.append(normalized)
            except asyncio.CancelledError:
                if _current_task_is_cancelling():
                    raise
                return _managed_artifact_failure("managed_artifact_write_failed", artifacts=artifact_refs)
            except (TimeoutError, asyncio.TimeoutError):
                return _managed_artifact_failure("managed_artifact_write_timeout", artifacts=artifact_refs)
            except ManagedArtifactError as exc:
                return _managed_artifact_failure(exc.reason, artifacts=artifact_refs)
            except Exception:
                return _managed_artifact_failure("managed_artifact_write_failed", artifacts=artifact_refs)

        if isinstance(public_result.content, Mapping):
            combined_content: dict[str, Any] = dict(public_result.content)
        else:
            combined_content = {"result": public_result.content}
        combined_content["managed_artifacts"] = artifact_refs
        return sanitize_capability_result(
            replace(public_result, content=combined_content)
        )
