"""Published contracts, not execution grants.

Only public ToolSpecs and opaque revision digests can enter durable snapshots.
Handlers, credentials and runtime configuration stay in the existing host.
"""
from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import asdict
import hashlib
import json
from typing import Any, Mapping

from capcore import CapabilityResult, CapabilityToolSpec

from .tool_handlers.core import ToolExecutionAdmission, ToolExecutionResult


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def scope_key(*, profile_user_id="", session_id="", character_pack_id="", client_mode="", domain_profile_id="", authorization_profile_user_id="") -> str:
    return digest([profile_user_id, session_id, character_pack_id, client_mode, domain_profile_id, authorization_profile_user_id])


def contract_snapshot(handler: Any, *, scope: str) -> dict[str, Any] | None:
    spec = handler.tool_spec()
    if not isinstance(spec, CapabilityToolSpec):
        return None
    public = asdict(spec)
    adapter = getattr(handler, "adapter", None)
    revision = getattr(handler, "contract_revision", "") or getattr(adapter, "contract_revision", "")
    if callable(revision):
        revision = revision()
    fingerprint = digest({"spec": public, "provider_revision": str(revision or "")})
    reference_digest = digest([scope, spec.capability_id, fingerprint])
    reference_encoder = getattr(handler, "contract_reference", None)
    return {
        "capability_id": spec.capability_id,
        # asdict already detached every field from the live handler's spec.
        "spec": public,
        "fingerprint": fingerprint,
        "native_policy_accepted": bool(getattr(handler, "policy_accepted_native_tool", False)),
        # This identifies the disclosed contract in this scope. It never grants
        # permissions; the live target is checked again at every execution.
        "contract_ref": reference_encoder(reference_digest) if callable(reference_encoder) else "contract_" + reference_digest,
    }


def contract_failure(capability_id: str, reason: str = "capability_not_available") -> ToolExecutionResult:
    recovery = {"hint": "Check the current tool and its arguments."}
    if reason == "capability_not_available":
        recovery = {
            "list": {"tool": "capability_list"},
            "hint": "This target is not available in the current scope. Check the current directory; do not infer a permission change from an unavailable executor.",
        }
    elif reason == "capability_target_invalid":
        recovery = {
            "hint": "Use capability_invoke with a target capability_id and an arguments object. Call discovery tools directly.",
            "list": {"tool": "capability_list"},
        }
    elif reason == "tool_not_declared":
        recovery = {"hint": "Use capability_invoke with this exact capability_id and its arguments. capability_load provides the current usage if needed."}
    elif reason == "executor_runtime_changed":
        recovery = {"hint": "The executor restarted before dispatch; nothing ran. Retry against the current executor."}
    payload = {
        "status": "rejected", "reason": reason, "capability_id": capability_id,
        "executed": False,
        "recovery": recovery,
    }
    return ToolExecutionResult(
        tool_type=capability_id,
        capability_result=CapabilityResult(is_error=True, status="rejected", reason=reason, content=payload),
        followup_context=canonical(payload),
        stream_events=[{"type": "capability_execution_result", **{k: payload[k] for k in ("status", "reason", "capability_id")}, "tool_type": capability_id, "executed": False}],
        state_updates={"capability_execution": payload},
    )


class ContractBoundHandler:
    """Frozen presentation, resolved once to a current implementation per call.

    Fingerprints describe the published view; they never grant or gate execution.
    A bound copy uses the live spec for validation and policy, and pins its worker.
    """

    def __init__(self, handler, snapshot: Mapping[str, Any], *, current_handler, native_declared=True, execution_bound=False):
        self._handler = handler
        self.snapshot = deepcopy(dict(snapshot))
        self._current_handler = current_handler
        self.native_declared = native_declared
        self._execution_bound = execution_bound
        self.tool_type = str(snapshot["capability_id"])
        self._spec = CapabilityToolSpec(**deepcopy(snapshot["spec"]))
        self.policy_accepted_native_tool = bool(snapshot.get("native_policy_accepted", False))

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_handler"), name)

    def tool_spec(self):
        return self._handler.tool_spec() if self._execution_bound else self._spec

    def bind_for_execution(self):
        if not self.native_declared:
            return None
        if self._execution_bound:
            return self
        try:
            handler = self._current_handler(self.tool_type)
        except Exception:
            handler = None
        if handler is None:
            return None
        bound = copy(self)
        bound._handler = handler
        bound._execution_bound = True
        bound.policy_accepted_native_tool = bool(getattr(handler, "policy_accepted_native_tool", False))
        return bound

    def availability_error(self):
        grant = getattr(self._handler, "invocation_grant_active", None)
        if callable(grant) and not grant():
            return "capability_not_available"
        try:
            return "" if self._current_handler(self.tool_type) is not None else "capability_not_available"
        except Exception:
            return "capability_not_available"

    def normalize_call(self, value):
        # Preserve the raw input until admission resolves the live schema.
        if not self._execution_bound:
            return dict(value) if isinstance(value, dict) else None
        return self._handler.normalize_call(value)

    def capability_status(self, **kwargs):
        if self._handler is None:
            return {"enabled": False, "status": "unavailable", "reason": "capability_not_available"}
        status = getattr(self._handler, "capability_status", None)
        return status(**kwargs) if callable(status) else {"enabled": True, "status": "ready"}

    def admit_execution(self, *, call, context):
        bound = self.bind_for_execution()
        if bound is None:
            return ToolExecutionAdmission.stop(contract_failure(self.tool_type,
                "tool_not_declared" if not self.native_declared else "capability_not_available"))
        return bound._handler.admit_execution(call=call, context=context)

    def execute(self, *, call, context):
        bound = self.bind_for_execution()
        reason = bound.availability_error() if bound is not None else (
            "tool_not_declared" if not self.native_declared else "capability_not_available")
        return contract_failure(self.tool_type, reason) if reason else bound._handler.execute(call=call, context=context)

    def execute_admitted(self, *, call, context):
        bound = self.bind_for_execution()
        reason = bound.availability_error() if bound is not None else (
            "tool_not_declared" if not self.native_declared else "capability_not_available")
        return contract_failure(self.tool_type, reason) if reason else bound._handler.execute_admitted(call=call, context=context)

    def retain_invocation(self):
        bound = self.bind_for_execution()
        if bound is None:
            return self
        retained = copy(bound)
        retain = getattr(bound._handler, "retain_invocation", None)
        retained._handler = retain() if callable(retain) else bound._handler
        return retained

    def release_invocation(self):
        release = getattr(self._handler, "release_invocation", None)
        if callable(release):
            release()
