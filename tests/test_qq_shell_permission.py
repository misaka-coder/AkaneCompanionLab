from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.capability_approval import CapabilityApprovalStore
from companion_v01.local_capability_config import get_approval_policy_config
from companion_v01.qq_gateway import NapCatQQGateway
from companion_v01.routes.qq import build_qq_router


QQ_BOT_ID = 10001
QQ_MASTER_ID = 10002
QQ_USER_ID = 10003
QQ_GROUP_ID = 20001


class _RuntimeMetrics:
    def observe_request(self, _name: str, *, duration_ms: float, ok: bool) -> None:
        del duration_ms, ok


class _Response:
    @staticmethod
    def raise_for_status() -> None:
        return None

    @staticmethod
    def json() -> dict[str, str]:
        return {"status": "ok"}


class _ExecHandler:
    @staticmethod
    def capability_status() -> dict[str, object]:
        return {"enabled": True, "status": "ready"}


class QQShellPermissionRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_root = Path(self._tmp.name)
        self.process_calls: list[dict] = []
        self.followups = []
        self.errors: list[tuple] = []
        self.approval_store = CapabilityApprovalStore()

        test_case = self

        class _Engine:
            capability_config_base_dir = test_case.config_root
            execution_provider = object()
            tool_handlers = {"exec_run": _ExecHandler()}

            def _get_approval_store(self):
                return test_case.approval_store

            def prefetch_remote_media_links_for_message(self, **_kwargs):
                return {}

            def process_turn_stream(self, payload: dict):
                test_case.process_calls.append(payload)
                yield {"type": "final_ui", "payload": {"speech": "should not run"}}

        self.gateway = NapCatQQGateway()

        class _TaskSupervisor:
            def create_task(_self, coroutine):
                test_case.followups.append(coroutine)
                return coroutine

        app = FastAPI()
        app.include_router(
            build_qq_router(
                engine=_Engine(),
                config_module=SimpleNamespace(
                    QQ_BRIDGE_ENABLED=True,
                    EXECUTION_QQ_ENABLED=True,
                    DATA_DIR=str(self.config_root),
                ),
                qq_gateway=self.gateway,
                runtime_metrics=_RuntimeMetrics(),
                logger=SimpleNamespace(
                    exception=lambda *_args, **_kwargs: None,
                    error=lambda *args, **_kwargs: test_case.errors.append((args, _kwargs)),
                ),
                log_event=lambda *_args, **_kwargs: None,
                async_task_supervisor=_TaskSupervisor(),
            )
        )
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def _event(self, *, user_id: int, message: str) -> dict[str, object]:
        return {
            "post_type": "message",
            "message_type": "group",
            "self_id": QQ_BOT_ID,
            "user_id": user_id,
            "group_id": QQ_GROUP_ID,
            "message_id": f"shell-{user_id}-{message}",
            "raw_message": message,
            "message": [{"type": "text", "data": {"text": message}}],
        }

    @patch("companion_v01.onebot_transport.requests.Session.request", return_value=_Response())
    @patch("companion_v01.qq_gateway.config.MASTER_QQ", str(QQ_MASTER_ID))
    @patch("companion_v01.qq_gateway.config.QQ_BOT_QQ", str(QQ_BOT_ID))
    def test_access_command_sets_owner_scoped_families_without_running_chat(self, _request) -> None:
        response = self.client.post(
            "/api/qq/napcat/event",
            json=self._event(user_id=QQ_MASTER_ID, message="/access all on"),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["reason"], "qq_access_permission_command")
        self.assertEqual(
            payload["modes"],
            {"ops": "trusted_auto_allow", "extensions": "trusted_auto_allow"},
        )
        owner_policy = get_approval_policy_config(
            base_dir=self.config_root,
            profile_user_id="master",
        )["approvalPolicy"]
        group_policy = get_approval_policy_config(
            base_dir=self.config_root,
            profile_user_id=f"qq_group_shared_{QQ_GROUP_ID}",
        )["approvalPolicy"]
        self.assertEqual(
            {item["id"]: item["mode"] for item in owner_policy["families"]},
            {"ops": "trusted_auto_allow", "extensions": "trusted_auto_allow"},
        )
        self.assertEqual(
            {item["id"]: item["mode"] for item in group_policy["families"]},
            {"ops": "ask_each_time", "extensions": "ask_each_time"},
        )
        self.assertEqual(self.process_calls, [])

    @patch("companion_v01.onebot_transport.requests.Session.request", return_value=_Response())
    @patch("companion_v01.qq_gateway.config.MASTER_QQ", str(QQ_MASTER_ID))
    @patch("companion_v01.qq_gateway.config.QQ_BOT_QQ", str(QQ_BOT_ID))
    def test_removed_shell_and_mcp_commands_are_not_control_plane_routes(self, _request) -> None:
        for message in ("/shell on", "/mcp on"):
            response = self.client.post(
                "/api/qq/napcat/event",
                json=self._event(user_id=QQ_MASTER_ID, message=message),
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["reason"], "group_passive_observed")

        policy = get_approval_policy_config(
            base_dir=self.config_root,
            profile_user_id="master",
        )["approvalPolicy"]
        self.assertEqual(
            {item["id"]: item["mode"] for item in policy["families"]},
            {"ops": "ask_each_time", "extensions": "ask_each_time"},
        )
        self.assertEqual(self.process_calls, [])

    @patch("companion_v01.onebot_transport.requests.Session.request", return_value=_Response())
    @patch("companion_v01.qq_gateway.config.MASTER_QQ", str(QQ_MASTER_ID))
    @patch("companion_v01.qq_gateway.config.QQ_BOT_QQ", str(QQ_BOT_ID))
    def test_master_approval_auto_resumes_as_original_requester(self, _request) -> None:
        profile_id = f"qq_group_shared_{QQ_GROUP_ID}"
        created = self.approval_store.create_request(
            profile_user_id=profile_id,
            session_id=profile_id,
            payload={
                "capabilityId": "mcp.text-utils.normalize",
                "actionId": "mcp.text-utils.normalize",
                "risk": "high",
                "approvalMode": "ask_each_time",
                "requestFingerprint": "c" * 64,
                "authorizationProfileUserId": f"qq_{QQ_USER_ID}",
            },
        )
        response = self.client.post(
            "/api/qq/napcat/event",
            json=self._event(user_id=QQ_MASTER_ID, message="/approve"),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["reason"], "qq_capability_approval_command")
        self.assertEqual(payload["command_status"], "approved")
        self.assertEqual(self.process_calls, [])
        self.assertEqual(len(self.followups), 1)
        asyncio.run(self.followups.pop())
        self.assertEqual(len(self.process_calls), 1, self.errors)
        resumed = self.process_calls[0]
        self.assertTrue(resumed["transient_user_message"])
        self.assertEqual(resumed["memory_message"], "")
        self.assertEqual(resumed["actor_profile_user_id"], f"qq_{QQ_USER_ID}")
        self.assertEqual(resumed["actor_stable_id"], f"qq:{QQ_USER_ID}")
        self.assertEqual(resumed["turn_kind"], "capability_approval_resume")
        resolved = self.approval_store.get_request(
            profile_user_id=profile_id,
            request_id=created["requestId"],
        )
        self.assertEqual(resolved["status"], "approved")


if __name__ == "__main__":
    unittest.main()
