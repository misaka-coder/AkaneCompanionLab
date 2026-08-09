from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config

from companion_v01.capability_approval import CapabilityApprovalStore
from companion_v01.capability_registry import ExecutorBroker
from companion_v01.desktop_satellite import DesktopSatelliteService
from companion_v01.desktop_satellite_specs import (
    DESKTOP_SATELLITE_TOOL_SPECS,
    SYSTEM_MEDIA_CONTROL_TOOL_SPEC,
    SYSTEM_VOLUME_TOOL_SPEC,
    desktop_satellite_spec,
)
from companion_v01.native_tool_schema import build_openai_native_tool_from_spec
from companion_v01.tool_invocation import ToolInvocation
from companion_v01.tool_orchestration_engine import execute_tool_invocation
from companion_v01.tool_runtime import DesktopSatelliteToolHandler

SPEC = SYSTEM_VOLUME_TOOL_SPEC


def _invocation(name: str, arguments: dict) -> ToolInvocation:
    spec = desktop_satellite_spec(name) or SPEC
    return ToolInvocation(
        name=name,
        arguments=arguments,
        source="native_openai",
        id="inv_1",
        execution_receipt={
            "instance_id": "fake",
            "offer_id": "fake",
            "lease_epoch": "fake",
            "tool_id": name,
            "offer_expires_at": 9999999999,
            "spec_version": spec.spec_version,
            "schema_version": spec.schema_version,
            "schema_hash": spec.schema_hash,
        },
    )


def _engine(base_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(
        capability_config_base_dir=base_dir,
        approval_store=CapabilityApprovalStore(),
        tool_handlers={
            "system_volume": DesktopSatelliteToolHandler(tool_id="system_volume", offer_source=None),
            "system_media_control": DesktopSatelliteToolHandler(tool_id="system_media_control", offer_source=None),
        },
        executor_broker=ExecutorBroker(None),
    )


class SystemVolumeContractTests(unittest.TestCase):
    def test_spec_is_registered_and_medium_risk(self) -> None:
        ids = {spec.capability_id for spec in DESKTOP_SATELLITE_TOOL_SPECS}
        self.assertIn("system_volume", ids)
        spec = desktop_satellite_spec("system_volume")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.risk, "medium")
        self.assertIn("control_system_volume", spec.effects)

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
        self.assertIn("system_volume", supported)

    def test_normalize_call_validates_action_and_value(self) -> None:
        handler = DesktopSatelliteToolHandler(tool_id="system_volume", offer_source=None)
        self.assertEqual(handler.normalize_call({"type": "system_volume", "action": "get"}), {"type": "system_volume", "action": "get"})
        self.assertEqual(handler.normalize_call({"type": "system_volume", "action": "set", "value": 30}), {"type": "system_volume", "action": "set", "value": 30})
        self.assertIsNone(handler.normalize_call({"type": "system_volume", "action": "set"}))
        self.assertIsNone(handler.normalize_call({"type": "system_volume", "action": "set", "value": 101}))
        self.assertIsNone(handler.normalize_call({"type": "system_volume", "action": "set", "value": 30.5}))
        self.assertIsNone(handler.normalize_call({"type": "system_volume", "action": "set", "value": True}))
        self.assertIsNone(handler.normalize_call({"type": "system_volume", "action": "bogus"}))

    def test_safe_reason_maps_volume_codes(self) -> None:
        self.assertEqual(DesktopSatelliteService._safe_reason("volume_read_failed"), "volume_read_failed")
        self.assertEqual(DesktopSatelliteService._safe_reason("volume_set_failed"), "volume_set_failed")


class SatelliteChannelAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base_dir = Path(self._tmp.name)
        self.engine = _engine(self.base_dir)

    def _dispatch(self, invocation: ToolInvocation, *, is_group: bool = False, user_id: int = 10001):
        request_context = {}
        if is_group:
            request_context = {"qq_delivery_context": {"is_group": True, "group_id": 123, "user_id": 456}}
        else:
            request_context = {"qq_delivery_context": {"is_group": False, "user_id": user_id}}
        return execute_tool_invocation(
            self.engine,
            invocation=invocation,
            profile_user_id="alice",
            session_id="s1",
            character_pack_id="",
            visual_payload={},
            now_ts=0,
            request_context=request_context,
        )

    def test_group_message_rejected_for_volume(self) -> None:
        result, envelope = self._dispatch(_invocation("system_volume", {"action": "get"}), is_group=True)
        self.assertEqual(envelope.status, "error")
        self.assertEqual(result.stream_events[0]["status"], "blocked")
        self.assertEqual(result.stream_events[0]["reason"], "device_action_requires_owner_private_chat")
        self.assertIn("群聊", result.followup_context)

    def test_group_message_rejected_for_media_control(self) -> None:
        result, envelope = self._dispatch(
            _invocation(SYSTEM_MEDIA_CONTROL_TOOL_SPEC.capability_id, {"action": "pause"}),
            is_group=True,
        )
        self.assertEqual(envelope.status, "error")
        self.assertEqual(result.stream_events[0]["reason"], "device_action_requires_owner_private_chat")

    def test_private_chat_not_rejected_by_channel(self) -> None:
        with patch.object(config, "MASTER_QQ", "10001"):
            result, envelope = self._dispatch(_invocation("system_volume", {"action": "get"}), is_group=False)
        # Not a group rejection; offline dispatch yields an unavailable result.
        self.assertNotEqual(result.stream_events[0].get("reason"), "device_action_requires_owner_private_chat")

    def test_non_owner_private_chat_is_rejected(self) -> None:
        with patch.object(config, "MASTER_QQ", "10001"):
            result, envelope = self._dispatch(
                _invocation("system_volume", {"action": "get"}),
                user_id=20002,
            )
        self.assertEqual(envelope.status, "error")
        self.assertEqual(result.stream_events[0]["reason"], "device_action_requires_owner_private_chat")
        self.assertIn("不是来自已配置的主人账号", result.followup_context)

    def test_private_chat_fails_closed_when_owner_is_unconfigured(self) -> None:
        with patch.object(config, "MASTER_QQ", ""):
            result, envelope = self._dispatch(_invocation("system_volume", {"action": "get"}))
        self.assertEqual(envelope.status, "error")
        self.assertEqual(result.stream_events[0]["reason"], "device_action_requires_owner_private_chat")


if __name__ == "__main__":
    unittest.main()
