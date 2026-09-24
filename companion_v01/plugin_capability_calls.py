"""Invocation-local plugin composition through existing admission and broker.

No business recipe, capability registry, background queue or delivery authority.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field, replace
from contextlib import ExitStack
from uuid import uuid4

from capcore import CapabilityResult
from akane_plugin.service_contracts import service_target, service_discovery_target

from .plugin_api import is_valid_capability_id
from .plugin_resources import current_resource_invocation, dependency_chain, service_call_chain, host_service_parameters
from .plugin_result_projection import sanitize_capability_result
from .plugin_result_forwarding import remember_dependency_result
from .plugin_subprocess import drain
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .tool_handlers.core import ToolExecutionContext
from .execution_resource_policy import ExecutionResourcePolicy, ExecutionResourceBudget, current_resource_budget
from .execution_policies import policy_evaluation, policy_tool_spec


def failure(reason, status="error"):
    return CapabilityResult(is_error=True, status=status, reason=reason)


@dataclass(frozen=True)
class HostServiceResult:
    """Program result and provenance observed by the host, never by plugin JSON."""

    result: CapabilityResult
    origin: dict = field(default_factory=dict)
    cancellation_requested: bool = False
    delivery_allowed: object = field(default=None, repr=False, compare=False)


@dataclass
class _HostServiceInvocation:
    # This is a host consumer, not a plugin resource/capability permission grant.
    context: ToolExecutionContext
    active: bool = True
    resource_budget: object = None
    policy_evaluation: bool = False
    generation_scopes: tuple = ()
    service_call_chain: bool = False
    origin: dict = field(default_factory=dict)
    delivery_allowed: object = None


class _HostCancellation:
    def __init__(self, callback):
        self.event = threading.Event()
        self.callback = callback

    def set(self):
        self.event.set()

    def is_set(self):
        return self.event.is_set() or bool(self.callback and self.callback())


class ScopedPluginCapabilityPort:
    def __init__(self, plugin_id, provider):
        self._plugin_id, self._provider = plugin_id, provider

    async def invoke(self, capability_id, arguments):
        """API-v1 file-call projection over the single program invocation path."""
        if not isinstance(arguments, dict):
            return failure("capability_dependency_request_invalid", "rejected")
        if arguments.get("send_to_user", False) is not False:
            return failure("capability_dependency_delivery_forbidden", "rejected")
        result = await self.call_result(capability_id, {**arguments, "send_to_user": False})
        if result.is_error:
            return result
        artifacts = result.content.get("managed_artifacts") if isinstance(result.content, dict) else None
        handles = [item["generated_handle"] for item in artifacts or ()]
        if not handles:
            return failure("capability_dependency_artifact_missing")
        return replace(result, content={"artifacts": handles})

    async def call_result(self, capability_id, arguments):
        scope = current_resource_invocation.get()
        if scope is None or not scope.active or scope.plugin_id != self._plugin_id:
            return failure("capability_invocation_required", "rejected")
        if not scope.can_invoke_capabilities:
            return failure("capability_invoke_permission_required", "rejected")
        task = asyncio.current_task()
        if task.cancelling():
            scope.revoke()
            raise asyncio.CancelledError()
        scope.pending.add(task)
        try:
            result = await self._provider.invoke(capability_id, arguments, invocation=scope)
            if not isinstance(result, CapabilityResult):
                return failure("capability_dependency_result_invalid")
            # Cancellation cannot permit more parent business work, but an
            # actual dependency error (including unknown remote completion)
            # must survive as a failure rather than a false stopped claim.
            if (not scope.active or task.cancelling()) and (not result.is_error or result.status == "cancelled"):
                raise asyncio.CancelledError()
            result = sanitize_capability_result(result)
            remember_dependency_result(scope, result)
            return result
        except asyncio.CancelledError:
            raise
        except Exception:
            return failure("capability_dependency_failed")
        finally:
            scope.pending.discard(task)


class EnginePluginCapabilityProvider:
    def __init__(self, engine):
        self._engine = engine

    def _budget(self, invocation):
        if invocation.resource_budget is None:
            policy = getattr(getattr(self._engine, "executor_broker", None), "resource_policy", None) or ExecutionResourcePolicy()
            invocation.resource_budget = ExecutionResourceBudget(policy)
        return invocation.resource_budget

    async def call_service(self, service_id, method, arguments, *, context, version=1, private_parameters=None):
        """Host-only entry through the same service lease, admission and broker.

        Identity must come from the authenticated host consumer. This method is
        not exposed over plugin RPC and never issues a synthetic plugin grant.
        Binding happens inside the executor thread, after cancellation checks.
        """
        from .tool_handlers.core import ToolExecutionContext

        if (not isinstance(context, ToolExecutionContext) or context.global_scope
                or not context.profile_user_id or not context.session_id):
            return HostServiceResult(failure("service_context_required", "rejected"))
        try:
            target = service_target(service_id, method, version)
            if not isinstance(arguments, dict):
                raise ValueError()
            args = json.loads(json.dumps(arguments, allow_nan=False, ensure_ascii=False))
            if private_parameters is not None and not isinstance(private_parameters, dict):
                raise ValueError()
            private = json.loads(json.dumps(private_parameters or {}, allow_nan=False))
        except (TypeError, ValueError, UnicodeError):
            return HostServiceResult(failure("service_request_invalid", "rejected"))
        invocation = _HostServiceInvocation(context=context)
        budget_failure = self._budget(invocation).input_failure(args)
        if budget_failure is not None:
            return HostServiceResult(budget_failure)
        cancelled = _HostCancellation(context.cancel_requested)
        # A host entry always starts a fresh service chain. A caller's plugin
        # ContextVars must not confer resources, policies or generation leases.
        scope_token = current_resource_invocation.set(None)
        chain_token = dependency_chain.set(())
        private_token = host_service_parameters.set(private)
        try:
            work = asyncio.create_task(asyncio.to_thread(self._execute, target, args, invocation, cancelled))
            try:
                result = await asyncio.shield(work)
            except asyncio.CancelledError:
                cancelled.set()
                result, _ = await drain(work)
            return HostServiceResult(result, dict(invocation.origin), cancelled.is_set(), invocation.delivery_allowed)
        finally:
            invocation.active = False
            host_service_parameters.reset(private_token)
            dependency_chain.reset(chain_token)
            current_resource_invocation.reset(scope_token)

    async def invoke(self, capability_id, arguments, *, invocation):
        if not invocation.active or not invocation.can_invoke_capabilities:
            return failure("capability_invocation_expired", "rejected")
        if (not invocation.context.profile_user_id or not invocation.context.session_id) and not getattr(invocation.context, "global_scope", False):
            return failure("capability_context_required", "rejected")
        budget = self._budget(invocation)
        if capability_id == {"operation": "execution_policies"}:
            if arguments != {}:
                return failure("capability_dependency_request_invalid", "rejected")
            pipeline = getattr(getattr(self._engine, "executor_broker", None), "execution_policies", None)
            if pipeline is None:
                return failure("execution_policy_inspection_unavailable", "unavailable")
            return CapabilityResult(is_error=False, status="ok", value=pipeline.snapshot())
        if capability_id == {"operation": "resource_budget"}:
            if arguments != {}:
                return failure("capability_dependency_request_invalid", "rejected")
            return CapabilityResult(is_error=False, status="ok", value=budget.snapshot(depth=len(invocation.dependency_chain)))
        if isinstance(capability_id, dict):
            try:
                if set(capability_id) == {"operation", "service_id"} and capability_id["operation"] == "services.list":
                    capability_id = service_discovery_target(capability_id["service_id"])
                    if arguments != {}:
                        raise ValueError()
                elif set(capability_id) == {"service_id", "method", "version"}:
                    capability_id = service_target(**capability_id)
                else:
                    raise ValueError()
            except (TypeError, ValueError):
                return failure("service_request_invalid", "rejected")
        elif not is_valid_capability_id(capability_id):
            return failure("capability_dependency_request_invalid", "rejected")
        if not isinstance(arguments, dict):
            return failure("capability_dependency_request_invalid", "rejected")
        try:
            encoded = json.dumps(arguments, allow_nan=False, ensure_ascii=False)
            args = json.loads(encoded)
        except (ValueError, TypeError, UnicodeError):
            return failure("capability_dependency_request_invalid", "rejected")
        chain = (*invocation.dependency_chain, invocation.capability_id)
        if capability_id in chain:
            return failure("capability_dependency_cycle", "rejected")
        failed = budget.input_failure(args) or budget.reserve_dependency(depth=len(chain))
        if failed is not None:
            failed.content["target"] = capability_id
            return failed
        cancelled = threading.Event()
        token = dependency_chain.set(chain)
        scope_token = current_resource_invocation.set(invocation)
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
            current_resource_invocation.reset(scope_token)
            invocation.pending.discard(task)

    def _execute(self, capability_id, args, invocation, cancelled):
        from .plugin_invocation_scope import use_generation_scopes

        with ExitStack() as stack:
            policy_token = policy_evaluation.set(invocation.policy_evaluation and bool(getattr(invocation.context, "global_scope", False)))
            stack.callback(policy_evaluation.reset, policy_token)
            budget_token = current_resource_budget.set(invocation.resource_budget)
            stack.callback(current_resource_budget.reset, budget_token)
            stack.enter_context(use_generation_scopes(invocation.generation_scopes))
            is_service = isinstance(capability_id, dict)
            if is_service and not invocation.service_call_chain:
                source = getattr(self._engine, "plugin_capability_source", None)
                factory = getattr(source, "service_scope", None)
                if callable(factory):
                    # A service chain binds at its own first call, even when
                    # the caller belongs to an older model-turn snapshot.
                    stack.enter_context(factory())
            token = service_call_chain.set(is_service or invocation.service_call_chain)
            try:
                return self._execute_scoped(capability_id, args, invocation, cancelled)
            finally:
                service_call_chain.reset(token)

    def _execute_scoped(self, capability_id, args, invocation, cancelled):
        from .executor_broker import ExecutorBroker
        from .tool_handlers.core import ToolExecutionContext

        context = invocation.context
        if cancelled.is_set() or not invocation.active:
            return failure("invocation_cancelled", "cancelled")
        try:
            global_scope = bool(getattr(context, "global_scope", False))
            if isinstance(capability_id, dict):
                source = getattr(self._engine, "plugin_capability_source", None)
                if capability_id.get("operation") == "services.list":
                    discover = getattr(source, "list_services", None)
                    if not callable(discover):
                        return failure("service_discovery_unavailable", "unavailable")
                    # Read the same leased declarations used by resolution. No
                    # business handler, model followup or execution receipt is created.
                    value = discover(capability_id["service_id"])
                    if cancelled.is_set() or not invocation.active:
                        return failure("invocation_cancelled", "cancelled")
                    return CapabilityResult(is_error=False, status="ok", value=value)
                resolve = getattr(source, "resolve_service_handler", None)
                if not callable(resolve):
                    return failure("service_unavailable", "unavailable")
                handler, reason = resolve(capability_id)
                if handler is None:
                    return failure(reason, "unavailable")
                if isinstance(invocation, _HostServiceInvocation):
                    invocation.delivery_allowed = getattr(handler.adapter, "delivery_allowed", None)
                    invocation.origin = {
                        "plugin_id": str(getattr(handler.adapter, "plugin_id", "")),
                        "generation_id": str(getattr(handler.adapter, "contract_revision", "")),
                        "capability_id": handler.tool_type,
                    }
                capability_id = handler.tool_type
                if capability_id in dependency_chain.get():
                    return failure("capability_dependency_cycle", "rejected")
            else:
                from .mode_profiles import ModeProfileRegistry
                # Unbound programs use the resolver's existing no-client branch.
                client = None if global_scope else ModeProfileRegistry().resolve_from_payload({"client_mode": context.client_mode})
                handler = self._engine._resolve_tool_handlers(
                    profile_user_id=context.profile_user_id,
                    session_id=context.session_id,
                    client_context=client,
                ).get(capability_id)
            if handler is None:
                return failure("capability_dependency_unavailable", "unavailable")
            if not getattr(handler, "supports_program_results", False):
                return failure("capability_dependency_not_composable", "rejected")
            if global_scope:
                spec = handler.tool_spec()
                if spec.risk != "low" or spec.confirm != "never" or spec.effects or any(
                    slot.delivery == "generated_file" for slot in getattr(handler.descriptor, "outputs", ())
                ):
                    return failure("capability_context_required", "rejected")
            call = handler.normalize_call({"type": capability_id, "arguments": args})
            if call is None:
                return failure("capability_dependency_request_invalid", "validation_error")
            invocation_id = "plugin-dependency-" + uuid4().hex
            if isinstance(invocation, _HostServiceInvocation):
                invocation_id = context.invocation_id or "host-service-" + uuid4().hex
                invocation.origin["invocation_id"] = invocation_id
            execution_context = ToolExecutionContext(
                context.profile_user_id,
                context.session_id,
                int(time.time()),
                {},
                client_mode=context.client_mode,
                invocation_id=invocation_id,
                cancel_requested=cancelled.is_set,
                result_consumer="program",
                global_scope=global_scope,
                character_pack_id=getattr(context, "character_pack_id", "") if not global_scope else "",
                request_context=(dict(context.request_context or {}) if isinstance(context, ToolExecutionContext) else {
                    "actor_profile_user_id": str(getattr(context, "authorization_profile_user_id", "") or ""),
                }),
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
                input_arguments=args,
                policy_spec=policy_tool_spec(handler), policy_client_mode=context.client_mode,
            )
            if getattr(execution, "policy_failure", None) is not None:
                return CapabilityResult(is_error=True, status=execution.status, reason=execution.reason,
                                        content=execution.policy_failure)
            if getattr(execution, "resource_limit", None) is not None:
                return CapabilityResult(is_error=True, status="resource_exhausted", reason=execution.reason,
                                        content=execution.resource_limit)
            result = execution.result
            if execution.status != "succeeded" or result is None:
                return failure("capability_dependency_execution_unconfirmed")
            if not isinstance(result.capability_result, CapabilityResult):
                return failure("capability_dependency_result_invalid")
            return sanitize_capability_result(result.capability_result)
        except Exception:
            return failure("capability_dependency_failed")
