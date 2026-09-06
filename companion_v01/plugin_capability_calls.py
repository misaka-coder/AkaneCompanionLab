"""Invocation-local plugin composition through existing admission and broker.

No business recipe, capability registry, background queue or delivery authority.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from uuid import uuid4

from capcore import CapabilityResult

from .capability_registry import ExecutorBroker
from .mode_profiles import ModeProfileRegistry
from .plugin_api import is_valid_capability_id
from .plugin_resources import current_resource_invocation, dependency_chain
from .plugin_result_projection import sanitize_capability_result
from .plugin_subprocess import drain
from .tool_runtime import ToolExecutionContext


def failure(reason, status="error"):
    return CapabilityResult(is_error=True, status=status, reason=reason)


class ScopedPluginCapabilityPort:
    def __init__(self, plugin_id, provider):
        self._plugin_id, self._provider = plugin_id, provider

    async def invoke(self, capability_id, arguments):
        scope = current_resource_invocation.get()
        if scope is None or not scope.active or scope.plugin_id != self._plugin_id:
            return failure("capability_invocation_required", "rejected")
        if not scope.can_invoke_capabilities:
            return failure("capability_invoke_permission_required", "rejected")
        task = asyncio.current_task()
        scope.pending.add(task)
        try:
            result = await self._provider.invoke(capability_id, arguments, invocation=scope)
            if not isinstance(result, CapabilityResult):
                return failure("capability_dependency_result_invalid")
            # Cancellation cannot permit more parent business work, but an
            # actual dependency error (including unknown remote completion)
            # must survive as a failure rather than a false stopped claim.
            if task.cancelling() and (not result.is_error or result.status == "cancelled"):
                raise asyncio.CancelledError()
            return sanitize_capability_result(result)
        except asyncio.CancelledError:
            raise
        except Exception:
            return failure("capability_dependency_failed")
        finally:
            scope.pending.discard(task)


class EnginePluginCapabilityProvider:
    def __init__(self, engine):
        self._engine = engine

    async def invoke(self, capability_id, arguments, *, invocation):
        if not invocation.active or not invocation.can_invoke_capabilities:
            return failure("capability_invocation_expired", "rejected")
        if not invocation.context.profile_user_id or not invocation.context.session_id:
            return failure("capability_context_required", "rejected")
        if not is_valid_capability_id(capability_id) or not isinstance(arguments, dict):
            return failure("capability_dependency_request_invalid", "rejected")
        try:
            encoded = json.dumps(arguments, allow_nan=False, ensure_ascii=False)
            if len(encoded.encode("utf-8")) > 16 * 1024:
                return failure("capability_dependency_request_too_large", "rejected")
            args = json.loads(encoded)
        except (ValueError, TypeError, UnicodeError):
            return failure("capability_dependency_request_invalid", "rejected")
        chain = (*invocation.dependency_chain, invocation.capability_id)
        if capability_id in chain:
            return failure("capability_dependency_cycle", "rejected")
        if len(chain) > 4 or invocation.dependency_calls >= 32:
            return failure("capability_dependency_budget_exceeded", "rejected")
        invocation.dependency_calls += 1
        cancelled = threading.Event()
        token = dependency_chain.set(chain)
        task = asyncio.current_task()
        invocation.pending.add(task)
        work = asyncio.create_task(asyncio.to_thread(self._execute, capability_id, args, invocation, cancelled))
        try:
            try:
                return await asyncio.shield(work)
            except asyncio.CancelledError:
                cancelled.set()
                result, _ = await drain(work)
                return result
        finally:
            dependency_chain.reset(token)
            invocation.pending.discard(task)

    def _execute(self, capability_id, args, invocation, cancelled):
        context = invocation.context
        if cancelled.is_set() or not invocation.active:
            return failure("invocation_cancelled", "cancelled")
        try:
            client = ModeProfileRegistry().resolve_from_payload({"client_mode": context.client_mode})
            handler = self._engine._resolve_tool_handlers(
                profile_user_id=context.profile_user_id,
                session_id=context.session_id,
                client_context=client,
            ).get(capability_id)
            if handler is None or not getattr(handler, "policy_accepted_plugin_capability", False):
                return failure("capability_dependency_unavailable", "unavailable")
            descriptor = handler.descriptor
            if not any(s.delivery == "generated_file" for s in descriptor.outputs) or not any(
                s.name == "send_to_user" and s.kind == "boolean" for s in descriptor.inputs
            ):
                return failure("capability_dependency_not_composable", "rejected")
            if args.get("send_to_user", False) is not False:
                return failure("capability_dependency_delivery_forbidden", "rejected")
            args["send_to_user"] = False
            call = handler.normalize_call({"type": capability_id, "arguments": args})
            invocation_id = "plugin-dependency-" + uuid4().hex
            execution_context = ToolExecutionContext(
                context.profile_user_id,
                context.session_id,
                int(time.time()),
                {},
                client_mode=context.client_mode,
                invocation_id=invocation_id,
                cancel_requested=cancelled.is_set,
            )
            broker = getattr(self._engine, "executor_broker", None)
            if broker is None:
                broker = self._engine.executor_broker = ExecutorBroker(None)
            execution = broker.execute_server_local(
                tool_id=capability_id,
                invocation_id=invocation_id,
                dispatch=lambda: handler.execute(call=call, context=execution_context),
                ledger_scope=f"{context.profile_user_id}\x1f{context.session_id}",
                request_data={"arguments": call},
            )
            result = execution.result
            if execution.status != "succeeded" or result is None:
                return failure("capability_dependency_execution_unconfirmed")
            status = result.state_updates.get("adapter_capability_status", "error")
            if status != "ok":
                return sanitize_capability_result(
                    failure(
                        result.state_updates.get("adapter_capability_reason") or status,
                        status,
                    )
                )
            events = [e for e in result.stream_events if e.get("type") == "generated_file_ready"]
            handles = [e.get("generated_file", {}).get("generated_handle") for e in events]
            if not handles or len(handles) > 20 or not all(isinstance(h, str) and h for h in handles):
                return failure("capability_dependency_artifact_missing")
            if any(e.get("send_to_user") for e in events):
                return failure("capability_dependency_delivery_forbidden")
            return sanitize_capability_result(
                CapabilityResult(is_error=False, status="ok", content={"artifacts": handles})
            )
        except Exception:
            return failure("capability_dependency_failed")
