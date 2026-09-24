"""Existing executor and receipt authority, independent of model tool tables."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Protocol
from capcore import CapabilityToolSpec


@dataclass(frozen=True)
class ExecutionReceipt:
    instance_id: str
    tool_id: str
    offer_id: str
    lease_epoch: str
    offer_expires_at: float
    spec_version: str
    schema_version: int
    schema_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "tool_id": self.tool_id,
            "offer_id": self.offer_id,
            "lease_epoch": self.lease_epoch,
            "offer_expires_at": self.offer_expires_at,
            "spec_version": self.spec_version,
            "schema_version": self.schema_version,
            "schema_hash": self.schema_hash,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ExecutionReceipt | None":
        if not isinstance(value, Mapping):
            return None
        try:
            receipt = cls(
                instance_id=str(value.get("instance_id") or "").strip(),
                tool_id=str(value.get("tool_id") or "").strip(),
                offer_id=str(value.get("offer_id") or "").strip(),
                lease_epoch=str(value.get("lease_epoch") or "").strip(),
                offer_expires_at=float(value.get("offer_expires_at") or 0),
                spec_version=str(value.get("spec_version") or "").strip(),
                schema_version=int(value.get("schema_version") or 0),
                schema_hash=str(value.get("schema_hash") or "").strip().lower(),
            )
        except (TypeError, ValueError):
            return None
        if not all(
            (
                receipt.instance_id,
                receipt.tool_id,
                receipt.offer_id,
                receipt.lease_epoch,
                receipt.spec_version,
                receipt.schema_hash,
            )
        ):
            return None
        return receipt


@dataclass(frozen=True)
class BrokerExecutionResult:
    status: str
    reason: str = ""
    model_feedback: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ServerLocalBrokerResult:
    status: str
    reason: str = ""
    result: Any = None
    resource_limit: dict[str, Any] | None = None
    policy_failure: dict[str, Any] | None = None


class CapabilityOfferSource(Protocol):
    instance_id: str

    def resolve_receipt(self, spec: CapabilityToolSpec) -> ExecutionReceipt | None: ...

    def validate_receipt(self, spec: CapabilityToolSpec, receipt: ExecutionReceipt) -> str: ...

    def dispatch(
        self,
        *,
        spec: CapabilityToolSpec,
        receipt: ExecutionReceipt,
        invocation_id: str,
        arguments: Mapping[str, Any],
        timeout_seconds: float,
    ) -> BrokerExecutionResult: ...


class ExecutorBroker:
    """Instance-owned dispatch and idempotency boundary for every executor."""

    def __init__(self, offer_source: CapabilityOfferSource | None, *, clock=time.time, resource_policy=None, execution_policies=None) -> None:
        from .execution_resource_policy import ExecutionResourcePolicy
        from .execution_policies import ExecutionPolicyPipeline
        self.resource_policy = resource_policy or ExecutionResourcePolicy()
        self.execution_policies = execution_policies or ExecutionPolicyPipeline()
        self.offer_source = offer_source
        self._clock = clock
        self._lock = threading.RLock()
        self._ledger: dict[
            tuple[str, str],
            tuple[str, BrokerExecutionResult | None],
        ] = {}
        self._server_local_ledger: dict[
            tuple[str, str],
            tuple[str, str, ServerLocalBrokerResult | None],
        ] = {}

    def execute_server_local(
        self,
        *,
        tool_id: str,
        invocation_id: str,
        dispatch: Callable[[], Any],
        retain_result: bool = True,
        ledger_scope: str = "",
        request_data: Mapping[str, Any] | None = None,
        input_arguments: Mapping[str, Any] | None = None,
        policy_spec=None,
        policy_client_mode="",
    ) -> ServerLocalBrokerResult:
        clean_tool_id = str(tool_id or "").strip()
        clean_invocation_id = str(invocation_id or "").strip()
        if not clean_tool_id:
            return ServerLocalBrokerResult(status="rejected", reason="missing_tool_id")
        if not clean_invocation_id:
            return ServerLocalBrokerResult(status="rejected", reason="missing_invocation_id")
        if not callable(dispatch):
            return ServerLocalBrokerResult(status="rejected", reason="invalid_server_local_dispatch")
        ledger_key = (str(ledger_scope or "").strip(), clean_invocation_id)
        request_fingerprint = _broker_request_fingerprint(
            {"tool_id": clean_tool_id, "request": dict(request_data or {})}
        )

        with self._lock:
            if ledger_key in self._ledger:
                return ServerLocalBrokerResult(
                    status="rejected",
                    reason="invocation_id_executor_conflict",
                )
            existing = self._server_local_ledger.get(ledger_key)
            if existing is not None:
                existing_tool_id, existing_fingerprint, existing_result = existing
                if existing_tool_id != clean_tool_id:
                    return ServerLocalBrokerResult(
                        status="rejected",
                        reason="invocation_id_tool_conflict",
                    )
                if existing_fingerprint != request_fingerprint:
                    return ServerLocalBrokerResult(
                        status="rejected",
                        reason="invocation_id_request_conflict",
                    )
                if existing_result is None:
                    return ServerLocalBrokerResult(
                        status="running",
                        reason="duplicate_invocation_in_progress",
                    )
                return existing_result
            self._server_local_ledger[ledger_key] = (
                clean_tool_id,
                request_fingerprint,
                None,
            )
            if len(self._server_local_ledger) > 512:
                terminal = [
                    (key, value)
                    for key, value in self._server_local_ledger.items()
                    if value[2] is not None
                ]
                self._server_local_ledger = dict(terminal[-384:])
                self._server_local_ledger[ledger_key] = (
                    clean_tool_id,
                    request_fingerprint,
                    None,
                )

        from .execution_resource_policy import dispatch_with_resource_policy, ExecutionResourceLimitExceeded
        from .execution_policies import ExecutionPolicyRejected
        arguments = input_arguments if input_arguments is not None else dict(request_data or {})
        try:
            raw_result = dispatch_with_resource_policy(self.resource_policy, clean_tool_id,
                arguments, lambda: self.execution_policies.execute(tool_id=clean_tool_id,
                    invocation_id=clean_invocation_id, arguments=arguments, dispatch=dispatch,
                    spec=policy_spec, client_mode=policy_client_mode))
            if raw_result is None:
                result = ServerLocalBrokerResult(
                    status="failed",
                    reason="server_local_empty_result",
                )
            else:
                result = ServerLocalBrokerResult(
                    status="succeeded",
                    result=raw_result,
                )
        except ExecutionResourceLimitExceeded as exc:
            result = ServerLocalBrokerResult(status="resource_exhausted", reason=exc.result.reason,
                                             resource_limit=exc.result.content)
        except ExecutionPolicyRejected as exc:
            result = ServerLocalBrokerResult(status=exc.status, reason=str(exc), policy_failure=exc.details)
        except Exception:
            result = ServerLocalBrokerResult(
                status="execution_unknown",
                reason="server_local_dispatch_failed",
            )

        ledger_result = result
        if result.status == "succeeded" and not retain_result:
            ledger_result = ServerLocalBrokerResult(status="succeeded", reason="completed")
        with self._lock:
            self._server_local_ledger[ledger_key] = (
                clean_tool_id,
                request_fingerprint,
                ledger_result,
            )
        return result

    def execute(
        self,
        *,
        spec: CapabilityToolSpec,
        receipt_value: Mapping[str, Any] | None,
        invocation_id: str,
        arguments: Mapping[str, Any],
        timeout_seconds: float = 15.0,
        ledger_scope: str = "",
        policy_client_mode: str = "",
    ) -> BrokerExecutionResult:
        receipt = ExecutionReceipt.from_mapping(receipt_value)
        if receipt is None:
            return BrokerExecutionResult(
                status="rejected",
                reason="missing_execution_receipt",
                model_feedback="当前桌面动作没有有效的执行凭据，不能执行。",
            )
        if receipt.offer_expires_at <= float(self._clock()):
            return BrokerExecutionResult(
                status="unavailable_before_dispatch",
                reason="offer_expired",
                model_feedback="当前无法连接桌面执行器，请直接说明这次暂时不能打开网页。",
            )
        clean_invocation_id = str(invocation_id or "").strip()
        if not clean_invocation_id:
            return BrokerExecutionResult(status="rejected", reason="missing_invocation_id")
        ledger_key = (str(ledger_scope or "").strip(), clean_invocation_id)
        request_fingerprint = _broker_request_fingerprint(
            {
                "tool_id": spec.capability_id,
                "schema_hash": spec.schema_hash,
                "instance_id": receipt.instance_id,
                "arguments": dict(arguments),
            }
        )
        with self._lock:
            if ledger_key in self._server_local_ledger:
                return BrokerExecutionResult(
                    status="rejected",
                    reason="invocation_id_executor_conflict",
                )
            if ledger_key in self._ledger:
                existing_fingerprint, existing_result = self._ledger[ledger_key]
                if existing_fingerprint != request_fingerprint:
                    return BrokerExecutionResult(
                        status="rejected",
                        reason="invocation_id_request_conflict",
                    )
                if existing_result is None:
                    return BrokerExecutionResult(
                        status="running",
                        reason="duplicate_invocation_in_progress",
                        model_feedback="同一桌面动作已经在处理中，不会重复执行。",
                    )
                return existing_result
            self._ledger[ledger_key] = (request_fingerprint, None)
            if len(self._ledger) > 512:
                terminal = [
                    (key, value)
                    for key, value in self._ledger.items()
                    if value[1] is not None
                ]
                self._ledger = dict(terminal[-384:])
                self._ledger[ledger_key] = (request_fingerprint, None)
        from .execution_resource_policy import current_resource_budget, ExecutionResourceBudget
        budget = current_resource_budget.get() or ExecutionResourceBudget(self.resource_policy)
        failed = budget.input_failure(arguments)
        if failed is not None:
            failed.content["target"] = spec.capability_id
            result = BrokerExecutionResult(status="resource_exhausted", reason=failed.reason,
                model_feedback=json.dumps(failed.content, ensure_ascii=False), data={"resource_limit": failed.content})
            with self._lock:
                self._ledger[ledger_key] = (request_fingerprint, result)
            return result
        source = self.offer_source
        if source is None:
            result = BrokerExecutionResult(
                status="unavailable_before_dispatch",
                reason="executor_unavailable",
                model_feedback="当前无法连接桌面执行器，请直接说明这次暂时不能打开网页。",
            )
        else:
            reason = source.validate_receipt(spec, receipt)
            if reason:
                result = BrokerExecutionResult(
                    status="unavailable_before_dispatch",
                    reason=reason,
                    model_feedback="当前无法连接桌面执行器，请直接说明这次暂时不能打开网页。",
                )
            else:
                try:
                    from .execution_policies import ExecutionPolicyRejected
                    result = self.execution_policies.execute(tool_id=spec.capability_id,
                        invocation_id=clean_invocation_id, arguments=dict(arguments), spec=spec,
                        executor="desktop_satellite", client_mode=policy_client_mode, dispatch=lambda: source.dispatch(
                            spec=spec, receipt=receipt, invocation_id=clean_invocation_id,
                            arguments=dict(arguments), timeout_seconds=max(1.0, min(30.0, float(timeout_seconds)))))
                except ExecutionPolicyRejected as exc:
                    result = BrokerExecutionResult(status=exc.status, reason=str(exc),
                        model_feedback=json.dumps(exc.details, ensure_ascii=False), data={"policy_failure": exc.details})
                except Exception:
                    result = BrokerExecutionResult(
                        status="execution_unknown",
                        reason="executor_dispatch_failed",
                        model_feedback="桌面动作的执行结果暂时无法确认，请不要声称网页已经打开。",
                    )
        with self._lock:
            # Screenshots may be several MB. Keep at most one private image for
            # replay rather than multiplying it by the ordinary 512-call ledger.
            if "imageBase64" in result.data:
                self._ledger = {key: entry for key, entry in self._ledger.items()
                                if entry[1] is None or "imageBase64" not in entry[1].data}
            retained = result
            if spec.capability_id in {"computer_use", "browser_page_personal"}:
                data = dict(result.data)
                data["screenshots"] = [{k:v for k,v in shot.items() if k != "imageBase64"}
                                       for shot in data.get("screenshots", []) if isinstance(shot, dict)]
                retained = replace(result, data=data)
            self._ledger[ledger_key] = (request_fingerprint, retained)
        return result


def _broker_request_fingerprint(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
