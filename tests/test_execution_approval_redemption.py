from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from companion_v01.capability_approval import (
    CapabilityApprovalStore,
    build_approval_request_fingerprint,
)
from companion_v01.execution_local import TrustedLocalExecutor
from companion_v01.tool_handlers.execution import ExecRunToolHandler
from companion_v01.tool_runtime import ToolExecutionContext


def _context(*, profile_user_id: str = "alice", session_id: str = "s1") -> ToolExecutionContext:
    return ToolExecutionContext(
        profile_user_id=profile_user_id,
        session_id=session_id,
        now_ts=0,
        visual_payload={},
        client_mode="qq",
    )


class _Clock:
    def __init__(self, start: str = "2026-01-01T00:00:00+00:00") -> None:
        self.now = datetime.fromisoformat(start)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class ApprovalGrantRedemptionTests(unittest.TestCase):
    def _payload(self, **overrides):
        payload = {
            "capabilityId": "exec_run",
            "actionId": "exec_run",
            "risk": "high",
            "approvalMode": "ask_each_time",
            "title": "执行命令需要确认",
            "summary": "Akane 想运行一条命令。",
            "approvalReason": "command_execution_requires_confirmation",
            "requestFingerprint": build_approval_request_fingerprint({"command": "echo hi"}),
            "resource": "subdir",
            "deviceId": "local",
        }
        payload.update(overrides)
        return payload

    def _approve(self, store: CapabilityApprovalStore, *, session_id: str = "s1") -> str:
        created = store.create_request(profile_user_id="alice", session_id=session_id, payload=self._payload())
        self.assertTrue(created["ok"], created)
        request_id = created["requestId"]
        decided = store.decide_request(
            profile_user_id="alice",
            request_id=request_id,
            payload={"decision": "approved"},
        )
        self.assertEqual(decided["status"], "approved")
        return request_id

    def test_grant_redemption_matches_binding(self) -> None:
        store = CapabilityApprovalStore()
        self._approve(store)
        grant = store.resolve_grant(
            profile_user_id="alice",
            session_id="s1",
            capability_id="exec_run",
            action_id="exec_run",
            resource="subdir",
            device="local",
            fingerprint=build_approval_request_fingerprint({"command": "echo hi"}),
        )
        self.assertIsNotNone(grant)
        assert grant is not None
        self.assertTrue(grant["grantId"].startswith("approvalgrant_"))

    def test_grant_does_not_cover_a_different_command(self) -> None:
        store = CapabilityApprovalStore()
        self._approve(store)
        grant = store.resolve_grant(
            profile_user_id="alice",
            session_id="s1",
            capability_id="exec_run",
            action_id="exec_run",
            resource="subdir",
            device="local",
            fingerprint=build_approval_request_fingerprint({"command": "rm -rf x"}),
        )
        self.assertIsNone(grant)

    def test_grant_does_not_cross_session(self) -> None:
        store = CapabilityApprovalStore()
        self._approve(store)
        grant = store.resolve_grant(
            profile_user_id="alice",
            session_id="s2",
            capability_id="exec_run",
            action_id="exec_run",
            resource="subdir",
            device="local",
            fingerprint=build_approval_request_fingerprint({"command": "echo hi"}),
        )
        self.assertIsNone(grant)

    def test_grant_requires_resource_and_device_match(self) -> None:
        store = CapabilityApprovalStore()
        self._approve(store)
        base = {
            "profile_user_id": "alice",
            "session_id": "s1",
            "capability_id": "exec_run",
            "action_id": "exec_run",
            "resource": "subdir",
            "device": "local",
            "fingerprint": build_approval_request_fingerprint({"command": "echo hi"}),
        }
        self.assertIsNone(store.resolve_grant(**{**base, "resource": "other"}))
        self.assertIsNone(store.resolve_grant(**{**base, "device": "cloud"}))

    def test_legacy_request_without_binding_cannot_authorize_exec(self) -> None:
        store = CapabilityApprovalStore()
        created = store.create_request(
            profile_user_id="alice",
            session_id="s1",
            payload={"capabilityId": "exec_run", "actionId": "exec_run", "risk": "high"},
        )
        store.decide_request(profile_user_id="alice", request_id=created["requestId"], payload={"decision": "approved"})
        grant = store.resolve_grant(
            profile_user_id="alice",
            session_id="s1",
            capability_id="exec_run",
            action_id="exec_run",
            resource="anything",
            device="any",
            fingerprint=build_approval_request_fingerprint({"command": "echo hi"}),
        )
        self.assertIsNone(grant)

    def test_grant_is_single_use(self) -> None:
        store = CapabilityApprovalStore()
        self._approve(store)
        arguments = {
            "profile_user_id": "alice",
            "session_id": "s1",
            "capability_id": "exec_run",
            "action_id": "exec_run",
            "resource": "subdir",
            "device": "local",
            "fingerprint": build_approval_request_fingerprint({"command": "echo hi"}),
        }
        self.assertIsNotNone(store.resolve_grant(**arguments))
        self.assertIsNone(store.resolve_grant(**arguments))

    def test_expired_grant_is_not_redeemable(self) -> None:
        clock = _Clock()
        store = CapabilityApprovalStore(now_func=clock)
        self._approve(store)
        self.assertIsNotNone(
            store.resolve_grant(
                profile_user_id="alice",
                session_id="s1",
                capability_id="exec_run",
                action_id="exec_run",
                resource="subdir",
                device="local",
                fingerprint=build_approval_request_fingerprint({"command": "echo hi"}),
            )
        )
        clock.advance(121)
        self.assertIsNone(
            store.resolve_grant(
                profile_user_id="alice",
                session_id="s1",
                capability_id="exec_run",
                action_id="exec_run",
                resource="subdir",
                device="local",
                fingerprint=build_approval_request_fingerprint({"command": "echo hi"}),
            )
        )

    def test_fingerprint_is_deterministic(self) -> None:
        self.assertEqual(
            build_approval_request_fingerprint({"command": "echo hi", "cwd": "a"}),
            build_approval_request_fingerprint({"cwd": "a", "command": "echo hi"}),
        )
        self.assertNotEqual(
            build_approval_request_fingerprint({"command": "echo hi"}),
            build_approval_request_fingerprint({"command": "echo hi "}),
        )


class ExecRedemptionHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base_dir = Path(self._tmp.name)
        workspace = self.base_dir / "workspace"
        workspace.mkdir()
        self.provider = TrustedLocalExecutor(
            workspace_root=workspace,
            run_log_dir=self.base_dir / "runlogs",
            provider_id="local",
        )
        self.approval_store = CapabilityApprovalStore()
        self.handler = ExecRunToolHandler(
            execution_provider=self.provider,
            config_base_dir=self.base_dir,
            approval_store=self.approval_store,
        )
        self.call = {"type": "exec_run", "command": "echo hi", "initial_wait_seconds": 1}

    def test_ask_then_approve_then_redempt(self) -> None:
        first = self.handler.execute(call=self.call, context=_context())
        self.assertEqual(first.stream_events[0]["type"], "capability_approval_required")
        request_id = first.stream_events[0].get("requestId")
        self.assertTrue(request_id)
        self.assertFalse(self.provider._logs)

        decided = self.approval_store.decide_request(
            profile_user_id="alice",
            request_id=request_id,
            payload={"decision": "approved"},
        )
        self.assertEqual(decided["status"], "approved")

        second = self.handler.execute(call=self.call, context=_context())
        self.assertEqual(second.stream_events[0]["type"], "capability_execution_result")
        self.assertEqual(second.stream_events[0]["status"], "completed")
        self.assertIn("完成", second.followup_context)
        self.assertIn("stdout", second.followup_context)
        third = self.handler.execute(call=self.call, context=_context())
        self.assertEqual(third.stream_events[0]["type"], "capability_approval_required")

    def test_grant_does_not_cover_a_changed_command(self) -> None:
        first = self.handler.execute(call=self.call, context=_context())
        request_id = first.stream_events[0]["requestId"]
        self.approval_store.decide_request(
            profile_user_id="alice",
            request_id=request_id,
            payload={"decision": "approved"},
        )
        changed = {**self.call, "command": "echo different"}
        result = self.handler.execute(call=changed, context=_context())
        self.assertEqual(result.stream_events[0]["type"], "capability_approval_required")
        self.assertNotEqual(result.stream_events[0].get("requestId"), request_id)

    def test_grant_is_session_scoped(self) -> None:
        first = self.handler.execute(call=self.call, context=_context())
        self.approval_store.decide_request(
            profile_user_id="alice",
            request_id=first.stream_events[0]["requestId"],
            payload={"decision": "approved"},
        )
        other_session = self.handler.execute(call=self.call, context=_context(session_id="s2"))
        self.assertEqual(other_session.stream_events[0]["type"], "capability_approval_required")


if __name__ == "__main__":
    unittest.main()
