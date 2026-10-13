"""Keep callback scopes and artifact handoff tied to actual outcomes."""

from __future__ import annotations

import asyncio
import sys
import uuid
from dataclasses import asdict, replace
from typing import Any, Mapping
from capcore import CapabilityResult, InvocationContext
from akane_plugin.events import Event
from ..plugin_generation_artifacts import consume_generation_artifact, discard_generation_artifacts, is_generation_artifact_reference
from ..plugin_generation_codec import PluginGenerationCodecError, capability_result_from_wire, invocation_context_to_wire, json_snapshot
from ..plugin_managed_artifacts import ManagedArtifactError, normalize_managed_artifact_reference
from ..plugin_subprocess import drain
from .contracts import PluginGenerationError, _generation_artifact_failure


class GenerationInvocationMixin:
    """Keep callback scopes and artifact handoff tied to actual outcomes."""

    @property
    def service_scope_id(self) -> str:
        """Stable host request id used by this generation's supervised services."""

        return f"service-scope:{self.generation_id}:{self.plugin_id}"

    def begin_service_invocation(self) -> None:
        """Register the host invocation used by this generation's services."""

        self._callback_router.begin_service_invocation(
            self.service_scope_id, plugin_id=self.plugin_id,
            context=InvocationContext(),
            permissions=tuple(self._permissions),
            connection_specs=self._connection_specs,
        )

    async def end_service_invocation(self) -> None:
        await self._callback_router.finish_invocation(self.service_scope_id)

    @property
    def background_service_scope_factory(self):
        """Return a factory the worker uses to open one invocation per service.

        The host registers the invocation under the same id the worker sets in
        its callback lane, so a service can use host ports that need an
        invocation for its whole lifetime.
        """

        def factory(service_id: str):
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def scope():
                request_id = self.service_scope_id
                try:
                    self._callback_router.begin_service_invocation(
                        request_id, plugin_id=self.plugin_id,
                        context=InvocationContext(),
                        permissions=tuple(self._permissions),
                        connection_specs=self._connection_specs,
                        service_id=service_id,
                    )
                except Exception:
                    import traceback as _traceback
                    print(f"service scope failed: {_traceback.format_exc()}", file=sys.stderr)
                    raise
                try:
                    yield request_id
                finally:
                    await self._callback_router.finish_invocation(request_id)

            return scope()

        return factory

    async def invoke(
        self,
        capability_id: str,
        args: Mapping[str, Any],
        *,
        context: InvocationContext,
    ) -> CapabilityResult:
        """Invoke one public Capability without imposing a generation timeout."""

        try:
            wire_args = json_snapshot(dict(args))
            wire_context = invocation_context_to_wire(context)
        except (TypeError, ValueError, PluginGenerationCodecError) as exc:
            raise PluginGenerationError("plugin_generation_invocation_invalid") from exc
        return await self._invoke_scoped("invoke", {
            "capability_id": str(capability_id or ""), "args": wire_args, "context": wire_context,
        }, capability_id=capability_id, context=context)

    async def invoke_event(self, subscription_id, event: Event, *, context: InvocationContext, scope_id: str,
                           delivery_id: str, origin_context: InvocationContext | None = None) -> CapabilityResult:
        if not any(item.subscription_id == subscription_id for item in self._event_subscriptions):
            return CapabilityResult(is_error=True, status="unavailable", reason="event_subscription_unavailable")
        return await self._invoke_scoped("event.invoke", {
            "subscription_id": subscription_id, "event": json_snapshot(asdict(event)),
            "context": invocation_context_to_wire(context), "scope_id": scope_id, "delivery_id": delivery_id,
        }, capability_id=subscription_id, context=context, event_delivery_id=delivery_id, origin_context=origin_context)

    async def _invoke_scoped(self, command, payload, *, capability_id, context, event_delivery_id="", origin_context=None):
        request_id = uuid.uuid4().hex
        try:
            self._callback_router.begin_invocation(
                request_id, plugin_id=self.plugin_id, context=context, capability_id=capability_id,
                # Process-installed legacy plugins have no installation approval
                # receipt. They cannot obtain newly privileged connection access.
                permissions=(self._permissions if self._approved_permissions is not None else
                             tuple(p for p in self._permissions if not p.startswith("connection."))),
                event_delivery_id=event_delivery_id,
                connection_specs=self._connection_specs,
                event_origin_context=origin_context,
                arguments=payload.get("args", {}),
            )
            request_id, response_queue = self._send_request(
                command, payload,
                request_id=request_id,
            )
        except BaseException:
            await self._callback_router.finish_invocation(request_id)
            raise
        response_task = asyncio.create_task(asyncio.to_thread(self._next_response, response_queue, None))
        cancellation_observed = False
        try:
            try:
                response = await asyncio.shield(response_task)
            except asyncio.CancelledError:
                cancellation_observed = True
                self._callback_router.revoke_invocation(request_id)
                self._send_cancel(request_id)
                # Wait for the worker's actual terminal response before
                # deleting input copies that a converter may still have open.
                while not response_task.done():
                    try:
                        await asyncio.shield(response_task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                # The worker may suppress cancellation and return an actual
                # success, or an error such as unconfirmed remote completion.
                # A cancellation request is not proof that either became a
                # cancelled operation. Validate its real terminal below.
                response = response_task.result()
            finally:
                self._discard_pending(request_id, response_queue)
            self._validate_response(response, expected_type="response")
            if response.get("request_id") != request_id:
                raise PluginGenerationError("plugin_generation_protocol_invalid")
            if not response.get("ok"):
                raise PluginGenerationError(
                    str(response.get("reason") or "plugin_generation_invoke_failed")
                )
            try:
                result = capability_result_from_wire(response.get("result"))
            except PluginGenerationCodecError as exc:
                raise PluginGenerationError("plugin_generation_protocol_invalid") from exc
            if cancellation_observed and result.is_error and result.status == "cancelled":
                if isinstance(result.content, Mapping):
                    discard_generation_artifacts(self._artifact_outbox_dir, result.content.get("managed_artifacts"))
                raise asyncio.CancelledError()
            forwarded = self._callback_router.forwarded_result(request_id, result)
            if forwarded is not None:
                return forwarded
            if command == "event.invoke":
                return result
            materialize = self._materialize_generation_artifact(
                result, capability_id=str(capability_id or ""), context=context,
            )
            if cancellation_observed:
                # Complete the confirmed outcome once, including its artifact
                # handoff, even if the caller repeats the cancellation signal.
                finalized, _ = await drain(asyncio.create_task(materialize))
                return finalized
            return await materialize
        finally:
            try:
                await self._callback_router.finish_invocation(request_id)
            finally:
                self._finish_invocation()

    async def _materialize_generation_artifact(
        self,
        result: CapabilityResult,
        *,
        capability_id: str,
        context: InvocationContext,
    ) -> CapabilityResult:
        content = result.content
        if not isinstance(content, Mapping) or "managed_artifacts" not in content:
            return result
        references = content.get("managed_artifacts")
        if (
            not isinstance(references, list) or not references
            or not all(is_generation_artifact_reference(ref) for ref in references)
            or len({ref["generated_id"] for ref in references}) != len(references)
        ):
            discard_generation_artifacts(self._artifact_outbox_dir, references)
            return _generation_artifact_failure("managed_artifact_handoff_invalid")
        descriptor = self._capability_descriptors.get(capability_id)
        outputs = tuple(output for output in descriptor.outputs if output.delivery == "generated_file") if descriptor else ()
        if len(outputs) != 1:
            discard_generation_artifacts(self._artifact_outbox_dir, references)
            return _generation_artifact_failure("managed_artifact_not_declared")
        staged_artifacts = []
        materialized_refs = []
        try:
            for reference in references:
                staged_artifacts.append(consume_generation_artifact(
                    self._artifact_outbox_dir,
                    reference, capability_id=capability_id,
                ))
            if sum(item.data_path.stat().st_size for item in staged_artifacts) > outputs[0].max_bytes:
                return _generation_artifact_failure("managed_artifact_too_large")
            if any(item.draft.source_handles or item.draft.revision_of for item in staged_artifacts) and "resource.read" not in self._permissions:
                return _generation_artifact_failure("managed_artifact_lineage_resource_permission_required")
            if self._managed_artifact_sink is None:
                return _generation_artifact_failure("managed_artifact_sink_unavailable")
            for staged in staged_artifacts:
                materialized = await asyncio.wait_for(
                    self._managed_artifact_sink.materialize(
                        staged.draft, context=context, capability_id=capability_id,
                    ),
                    timeout=self.managed_artifact_timeout_seconds or None,
                )
                normalized = normalize_managed_artifact_reference(
                    materialized, draft=staged.draft, capability_id=capability_id,
                )
                if normalized is None or is_generation_artifact_reference(normalized):
                    raise ManagedArtifactError("managed_artifact_invalid_reference")
                materialized_refs.append(normalized)
            projected_content = dict(content)
            projected_content["managed_artifacts"] = materialized_refs
            return replace(result, content=projected_content)
        except asyncio.CancelledError:
            raise
        except (TimeoutError, asyncio.TimeoutError):
            return _generation_artifact_failure("managed_artifact_write_timeout", artifacts=materialized_refs)
        except ManagedArtifactError as exc:
            return _generation_artifact_failure(exc.reason, artifacts=materialized_refs)
        except Exception:
            return _generation_artifact_failure("managed_artifact_write_failed", artifacts=materialized_refs)
        finally:
            discard_generation_artifacts(self._artifact_outbox_dir, references)
