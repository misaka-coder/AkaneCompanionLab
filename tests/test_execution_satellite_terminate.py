from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from capcore import PermissionDecision

from companion_v01.capability_approval import CapabilityApprovalStore, build_approval_request_fingerprint
from companion_v01.capability_registry import ExecutorBroker
from companion_v01.desktop_satellite import DesktopSatelliteService
from companion_v01.desktop_satellite_specs import (
    DESKTOP_SATELLITE_TOOL_SPECS,
    SYSTEM_PROCESS_TERMINATE_TOOL_SPEC,
    desktop_satellite_spec,
)
from companion_v01.local_capability_config import save_approval_policy_config
from companion_v01.native_tool_schema import build_openai_native_tool_from_spec
from companion_v01.tool_invocation import ToolInvocation
from companion_v01.tool_orchestration_engine import _satellite_permission_gate, execute_tool_invocation
from companion_v01.tool_runtime import DesktopSatelliteToolHandler

SPEC = SYSTEM_PROCESS_TERMINATE_TOOL_SPEC


def _invocation(pid: int, **kwargs) -> ToolInvocation:
    payload = {
        "name": "system_process_terminate",
        "arguments": {"pid": pid},
        "source": "native_openai",
        "id": "inv_1",
        "execution_receipt": {
            "instance_id": "fake",
            "offer_id": "fake",
            "lease_epoch": "fake",
            "tool_id": "system_process_terminate",
            "offer_expires_at": 9999999999,
            "spec_version": SPEC.spec_version,
            "schema_version": SPEC.schema_version,
            "schema_hash": SPEC.schema_hash,
        },
    }
    payload.update(kwargs)
    return ToolInvocation(**payload)


def _engine(base_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(
        capability_config_base_dir=base_dir,
        approval_store=CapabilityApprovalStore(),
        tool_handlers={
            "system_process_terminate": DesktopSatelliteToolHandler(
                tool_id="system_process_terminate", offer_source=None
            )
        },
        executor_broker=ExecutorBroker(None),
    )


class SystemProcessTerminateContractTests(unittest.TestCase):
    def test_spec_is_registered_and_high_risk(self) -> None:
        ids = {spec.capability_id for spec in DESKTOP_SATELLITE_TOOL_SPECS}
        self.assertIn("system_process_terminate", ids)
        spec = desktop_satellite_spec("system_process_terminate")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.risk, "high")
        self.assertEqual(spec.confirm, "always")
        self.assertIn("terminate_system_process", spec.effects)

    def test_schema_projection_is_deterministic(self) -> None:
        def dump() -> str:
            return json.dumps(build_openai_native_tool_from_spec(SPEC), ensure_ascii=False, sort_keys=True)

        self.assertEqual(dump(), dump())

    def test_registration_validation_accepts_the_offer(self) -> None:
        service = DesktopSatelliteService(instance_id="instance-a", token="device-secret")
        supported = service._validate_registration(
            {
                "type": "register",
                "protocol_version": 1,
                "instance_id": "instance-a",
                "offers": [
                    {
                        "tool_id": SPEC.capability_id,
                        "spec_version": SPEC.spec_version,
                        "schema_version": SPEC.schema_version,
                        "schema_hash": SPEC.schema_hash,
                    }
                ],
            }
        )
        self.assertIn("system_process_terminate", supported)

    def test_normalize_call_validates_pid(self) -> None:
        handler = DesktopSatelliteToolHandler(tool_id="system_process_terminate", offer_source=None)
        self.assertEqual(handler.normalize_call({"type": "system_process_terminate", "pid": 1234}), {"type": "system_process_terminate", "pid": 1234})
        self.assertIsNone(handler.normalize_call({"type": "system_process_terminate", "pid": 0}))
        self.assertIsNone(handler.normalize_call({"type": "system_process_terminate", "pid": -1}))
        self.assertIsNone(handler.normalize_call({"type": "system_process_terminate", "pid": 2**32}))
        self.assertIsNone(handler.normalize_call({"type": "system_process_terminate", "pid": "abc"}))
        self.assertIsNone(handler.normalize_call({"type": "system_process_terminate", "pid": 1.5}))
        self.assertIsNone(handler.normalize_call({"type": "system_process_terminate", "pid": True}))
        self.assertIsNone(handler.normalize_call({"type": "system_process_terminate"}))

    def test_safe_reason_maps_terminate_codes(self) -> None:
        self.assertEqual(DesktopSatelliteService._safe_reason("terminate_failed"), "terminate_failed")
        self.assertEqual(DesktopSatelliteService._safe_reason("invalid_pid"), "invalid_pid")
        self.assertEqual(
            DesktopSatelliteService._safe_reason("termination_unconfirmed"),
            "termination_unconfirmed",
        )


class SatelliteApprovalGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base_dir = Path(self._tmp.name)

    def _set_policy(self, mode: str) -> None:
        result = save_approval_policy_config(
            base_dir=self.base_dir,
            profile_user_id="alice",
            payload={"defaultMode": mode},
        )
        self.assertTrue(result.get("ok"), result)

    def _gate(self, engine, pid: int, *, instance_id: str = "fake"):
        invocation = _invocation(pid)
        invocation = ToolInvocation(
            **{
                **invocation.__dict__,
                "execution_receipt": {**dict(invocation.execution_receipt), "instance_id": instance_id},
            }
        )
        return _satellite_permission_gate(
            engine,
            spec=SPEC,
            invocation=invocation,
            profile_user_id="alice",
            session_id="s1",
            client_context=None,
        )

    def test_high_risk_satellite_asks_by_default(self) -> None:
        engine = _engine(self.base_dir)
        gated = self._gate(engine, 1234)
        self.assertIsNotNone(gated)
        result, envelope = gated
        self.assertEqual(envelope.status, "ask")
        self.assertEqual(result.stream_events[0]["type"], "capability_approval_required")
        self.assertTrue(result.stream_events[0].get("requestId"))

    def test_trusted_auto_allow_proceeds(self) -> None:
        self._set_policy("trusted_auto_allow")
        engine = _engine(self.base_dir)
        self.assertIsNone(self._gate(engine, 1234))

    def test_blocked_when_policy_disabled(self) -> None:
        denied = PermissionDecision(
            allowed=False,
            requires_user_decision=False,
            mode="disabled",
            reason="capability_disabled_by_policy",
        )
        engine = _engine(self.base_dir)
        with patch(
            "companion_v01.capcore_runtime.resolve_permission_for_profile",
            return_value=denied,
        ):
            gated = self._gate(engine, 1234)
        self.assertIsNotNone(gated)
        result, envelope = gated
        self.assertEqual(envelope.status, "error")
        self.assertEqual(result.stream_events[0]["status"], "blocked")

    def test_grant_redemption_then_proceeds(self) -> None:
        engine = _engine(self.base_dir)
        gated = self._gate(engine, 1234)
        self.assertIsNotNone(gated)
        request_id = gated[0].stream_events[0]["requestId"]
        engine.approval_store.decide_request(
            profile_user_id="alice",
            request_id=request_id,
            payload={"decision": "approved"},
        )
        self.assertIsNone(self._gate(engine, 1234))

    def test_grant_does_not_cover_a_different_pid(self) -> None:
        engine = _engine(self.base_dir)
        gated = self._gate(engine, 1234)
        request_id = gated[0].stream_events[0]["requestId"]
        engine.approval_store.decide_request(
            profile_user_id="alice",
            request_id=request_id,
            payload={"decision": "approved"},
        )
        again = self._gate(engine, 9999)
        self.assertIsNotNone(again)
        self.assertEqual(again[1].status, "ask")

    def test_grant_is_bound_to_the_satellite_instance(self) -> None:
        engine = _engine(self.base_dir)
        gated = self._gate(engine, 1234, instance_id="device-a")
        request_id = gated[0].stream_events[0]["requestId"]
        engine.approval_store.decide_request(
            profile_user_id="alice",
            request_id=request_id,
            payload={"decision": "approved"},
        )
        other_device = self._gate(engine, 1234, instance_id="device-b")
        self.assertIsNotNone(other_device)
        self.assertEqual(other_device[1].status, "ask")
        self.assertIsNone(self._gate(engine, 1234, instance_id="device-a"))

    def test_missing_device_receipt_does_not_request_unusable_approval(self) -> None:
        engine = _engine(self.base_dir)
        invocation = _invocation(1234, execution_receipt={})
        gated = _satellite_permission_gate(
            engine,
            spec=SPEC,
            invocation=invocation,
            profile_user_id="alice",
            session_id="s1",
            client_context=None,
        )
        self.assertIsNone(gated)


class SatelliteTerminateDispatchTests(unittest.TestCase):
    def test_execute_tool_invocation_ask_path_returns_ask_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            engine = _engine(base_dir)
            result, envelope = execute_tool_invocation(
                engine,
                invocation=_invocation(1234),
                profile_user_id="alice",
                session_id="s1",
                character_pack_id="",
                visual_payload={},
                now_ts=0,
            )
            self.assertEqual(envelope.status, "ask")
            self.assertEqual(result.stream_events[0]["type"], "capability_approval_required")
            # No real dispatch happened.
            self.assertEqual(engine.executor_broker._ledger, {})

    def test_offline_terminate_is_unavailable_without_creating_an_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _engine(Path(tmp))
            result, envelope = execute_tool_invocation(
                engine,
                invocation=_invocation(1234, execution_receipt={}),
                profile_user_id="alice",
                session_id="s1",
                character_pack_id="",
                visual_payload={},
                now_ts=0,
            )
            self.assertEqual(envelope.status, "error")
            self.assertEqual(result.stream_events[0]["status"], "unavailable")
            self.assertEqual(result.stream_events[0]["reason"], "not_available")
            self.assertEqual(
                engine.approval_store.list_requests(profile_user_id="alice")["approvalRequests"],
                [],
            )


if __name__ == "__main__":
    unittest.main()
