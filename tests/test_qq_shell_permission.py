from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

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

        test_case = self

        class _Engine:
            capability_config_base_dir = test_case.config_root
            execution_provider = object()
            tool_handlers = {"exec_run": _ExecHandler()}

            def process_turn_stream(self, payload: dict):
                test_case.process_calls.append(payload)
                yield {"type": "final_ui", "payload": {"speech": "should not run"}}

        self.gateway = NapCatQQGateway()
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
                logger=SimpleNamespace(exception=lambda *_args, **_kwargs: None),
                log_event=lambda *_args, **_kwargs: None,
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
    def test_master_can_enable_current_group_without_running_chat(self, _request) -> None:
        response = self.client.post(
            "/api/qq/napcat/event",
            json=self._event(user_id=QQ_MASTER_ID, message="/shell on"),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["reason"], "qq_shell_permission_command")
        self.assertEqual(payload["approval_mode"], "trusted_auto_allow")
        self.assertEqual(self.process_calls, [])

        group_policy = get_approval_policy_config(
            base_dir=self.config_root,
            profile_user_id=f"qq_group_shared_{QQ_GROUP_ID}",
        )["approvalPolicy"]
        master_policy = get_approval_policy_config(
            base_dir=self.config_root,
            profile_user_id="master",
        )["approvalPolicy"]
        self.assertEqual(group_policy["capabilityModes"], {"exec_run": "trusted_auto_allow"})
        self.assertEqual(master_policy["capabilityModes"], {})

    @patch("companion_v01.onebot_transport.requests.Session.request", return_value=_Response())
    @patch("companion_v01.qq_gateway.config.MASTER_QQ", str(QQ_MASTER_ID))
    @patch("companion_v01.qq_gateway.config.QQ_BOT_QQ", str(QQ_BOT_ID))
    def test_group_member_cannot_change_shell_permission(self, _request) -> None:
        response = self.client.post(
            "/api/qq/napcat/event",
            json=self._event(user_id=QQ_USER_ID, message="/shell on"),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["command_status"], "forbidden")
        group_policy = get_approval_policy_config(
            base_dir=self.config_root,
            profile_user_id=f"qq_group_shared_{QQ_GROUP_ID}",
        )["approvalPolicy"]
        self.assertEqual(group_policy["capabilityModes"], {})
        self.assertEqual(self.process_calls, [])


if __name__ == "__main__":
    unittest.main()
