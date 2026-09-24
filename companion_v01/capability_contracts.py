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

from capcore import CapabilityToolSpec

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
        "spec": deepcopy(public),
        "fingerprint": fingerprint,
        "native_policy_accepted": bool(getattr(handler, "policy_accepted_native_tool", False)),
        # This identifies the disclosed contract in this scope. It never grants
        # permissions; the live target is checked again at every execution.
        "contract_ref": reference_encoder(reference_digest) if callable(reference_encoder) else "contract_" + reference_digest,
    }


def contract_failure(capability_id: str, reason: str = "capability_contract_stale") -> ToolExecutionResult:
    recovery = {
        "load": {"tool": "capability_load", "capability_ids": [capability_id]},
        "invoke": "Use capability_invoke with the newly returned contract_ref and arguments. Do not retry the old native declaration.",
    }
    if reason == "capability_not_available":
        recovery = {
            "list": {"tool": "capability_list"},
            "hint": "This target is no longer visible in the current scope. This does not establish that the user revoked permission: an executor may be offline or its lease may have expired. Check the current directory; other capabilities remain usable. Reload only an available exact id. Do not report a permission change without explicit evidence.",
        }
    elif reason == "capability_target_invalid":
        recovery = {
            "hint": "Use capability_invoke with a target capability_id, its loaded contract_ref, and an arguments object. Discovery entry points are called directly, not as invocation targets.",
            "list": {"tool": "capability_list"},
        }
    payload = {
        "status": "rejected", "reason": reason, "capability_id": capability_id,
        "executed": False,
        "recovery": recovery,
    }
    return ToolExecutionResult(
        tool_type=capability_id,
        followup_context=canonical(payload),
        stream_events=[{"type": "capability_execution_result", **{k: payload[k] for k in ("status", "reason", "capability_id")}, "tool_type": capability_id, "executed": False}],
        state_updates={"capability_execution": payload},
    )


class ContractBoundHandler:
    """A per-request binding over an existing handler, with a live admission check.

    There is no dispatch registry here. The resolver reads the host's current
    selection; invocation still uses the captured real handler and its broker.
    """

    def __init__(self, handler, snapshot: Mapping[str, Any], *, current_contract):
        self._handler = handler
        self.snapshot = deepcopy(dict(snapshot))
        self._current_contract = current_contract
        self.tool_type = str(snapshot["capability_id"])
        self._spec = CapabilityToolSpec(**deepcopy(snapshot["spec"]))
        self.policy_accepted_native_tool = bool(snapshot.get("native_policy_accepted", False))

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_handler"), name)

    def tool_spec(self):
        return self._spec

    def contract_error(self) -> str:
        try:
            current = self._current_contract(self.tool_type)
        except Exception:
            return "capability_not_available"
        if current is None:
            return "capability_not_available"
        if getattr(self, "_contract_retained", False):
            # Admission pinned this exact implementation while its disclosed
            # contract was current. Ordinary publication can replace a worker
            # during a policy callback or queue wait; it does not rewrite that
            # in-flight call. The provider's retained grant still detects real
            # withdrawal, and the current directory above detects scope removal.
            return "" if self._handler.invocation_grant_active() else "capability_not_available"
        if current.get("contract_ref") != self.snapshot.get("contract_ref"):
            return "capability_contract_stale"
        return ""

    def normalize_call(self, value):
        # Preserve a genuine stale call until it receives its paired error;
        # never normalize old arguments using a newly activated contract.
        if self._handler is None or self.contract_error():
            return dict(value) if isinstance(value, dict) else None
        return self._handler.normalize_call(value)

    def capability_status(self, **kwargs):
        reason = self.contract_error()
        if reason:
            return {"enabled": False, "status": "unavailable", "reason": reason}
        status = getattr(self._handler, "capability_status", None)
        return status(**kwargs) if callable(status) else {"enabled": True, "status": "ready"}

    def admit_execution(self, *, call, context):
        reason = self.contract_error()
        if reason:
            return ToolExecutionAdmission.stop(contract_failure(self.tool_type, reason))
        return self._handler.admit_execution(call=call, context=context)

    def execute(self, *, call, context):
        reason = self.contract_error()
        return contract_failure(self.tool_type, reason) if reason else self._handler.execute(call=call, context=context)

    def execute_admitted(self, *, call, context):
        reason = self.contract_error()
        return contract_failure(self.tool_type, reason) if reason else self._handler.execute_admitted(call=call, context=context)

    def retain_invocation(self):
        retained = copy(self)
        retain = getattr(self._handler, "retain_invocation", None)
        valid = not self.contract_error()
        retained._handler = retain() if callable(retain) else self._handler
        retained._contract_retained = valid and callable(retain) and callable(
            getattr(retained._handler, "invocation_grant_active", None))
        return retained

    def release_invocation(self):
        release = getattr(self._handler, "release_invocation", None)
        if callable(release):
            release()
