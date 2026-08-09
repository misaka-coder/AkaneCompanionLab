from __future__ import annotations

import json
import unittest

from companion_v01.desktop_satellite import DesktopSatelliteService
from companion_v01.desktop_satellite_specs import (
    DESKTOP_SATELLITE_TOOL_SPECS,
    SYSTEM_PROCESS_SNAPSHOT_TOOL_SPEC,
    desktop_satellite_spec,
)
from companion_v01.native_tool_schema import build_openai_native_tool_from_spec
from companion_v01.tool_runtime import DesktopSatelliteToolHandler


class SystemProcessSnapshotContractTests(unittest.TestCase):
    def test_spec_is_registered_and_well_formed(self) -> None:
        ids = {spec.capability_id for spec in DESKTOP_SATELLITE_TOOL_SPECS}
        self.assertIn("system_process_snapshot", ids)
        spec = desktop_satellite_spec("system_process_snapshot")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.risk, "low")
        self.assertEqual(spec.confirm, "never")
        self.assertEqual(spec.idempotency, "read_only")
        self.assertIn("read_system_processes", spec.effects)
        self.assertEqual(spec.input_schema["required"], [])

    def test_schema_projection_is_deterministic(self) -> None:
        def dump() -> str:
            return json.dumps(
                build_openai_native_tool_from_spec(SYSTEM_PROCESS_SNAPSHOT_TOOL_SPEC),
                ensure_ascii=False,
                sort_keys=True,
            )

        self.assertEqual(dump(), dump())
        self.assertTrue(SYSTEM_PROCESS_SNAPSHOT_TOOL_SPEC.schema_hash.startswith("sha256:"))

    def test_registration_validation_accepts_the_offer(self) -> None:
        service = DesktopSatelliteService(instance_id="instance-a", token="device-secret")
        supported = service._validate_registration(
            {
                "type": "register",
                "protocol_version": 1,
                "instance_id": "instance-a",
                "offers": [
                    {
                        "tool_id": SYSTEM_PROCESS_SNAPSHOT_TOOL_SPEC.capability_id,
                        "spec_version": SYSTEM_PROCESS_SNAPSHOT_TOOL_SPEC.spec_version,
                        "schema_version": SYSTEM_PROCESS_SNAPSHOT_TOOL_SPEC.schema_version,
                        "schema_hash": SYSTEM_PROCESS_SNAPSHOT_TOOL_SPEC.schema_hash,
                    }
                ],
            }
        )
        self.assertIn("system_process_snapshot", supported)

    def test_registration_validation_rejects_wrong_hash(self) -> None:
        service = DesktopSatelliteService(instance_id="instance-a", token="device-secret")
        supported = service._validate_registration(
            {
                "type": "register",
                "protocol_version": 1,
                "instance_id": "instance-a",
                "offers": [
                    {
                        "tool_id": "system_process_snapshot",
                        "spec_version": "1.0.0",
                        "schema_version": 1,
                        "schema_hash": "sha256:" + "0" * 64,
                    }
                ],
            }
        )
        self.assertNotIn("system_process_snapshot", supported)

    def test_receipt_is_none_while_offline(self) -> None:
        service = DesktopSatelliteService(instance_id="instance-a", token="device-secret")
        self.assertIsNone(service.resolve_receipt(SYSTEM_PROCESS_SNAPSHOT_TOOL_SPEC))

    def test_handler_capability_status_offline(self) -> None:
        handler = DesktopSatelliteToolHandler(tool_id="system_process_snapshot", offer_source=None)
        status = handler.capability_status()
        self.assertIs(status["enabled"], False)
        self.assertEqual(status["status"], "unavailable")
        self.assertEqual(status["reason"], "satellite_not_configured")


class SystemProcessSnapshotSanitizationTests(unittest.TestCase):
    def test_safe_result_data_keeps_pid_and_name_but_drops_secrets(self) -> None:
        payload = {
            "ok": True,
            "status": "ok",
            "platform": "windows",
            "processes": [
                {"pid": 4, "name": "System"},
                {"pid": 1234, "name": "explorer.exe"},
                {"pid": 5678, "name": "C:\\Users\\alice\\app.exe", "path": "C:\\secret\\path"},
                {"pid": 9999, "name": "svc.exe", "token": "secret-token", "authorization": "Bearer x"},
            ],
        }
        cleaned = DesktopSatelliteService._safe_result_data(payload)
        processes = cleaned["processes"]
        self.assertEqual(processes[0], {"pid": 4, "name": "System"})
        self.assertEqual(processes[1], {"pid": 1234, "name": "explorer.exe"})
        self.assertEqual(processes[2]["name"], "[local_path]")
        self.assertNotIn("path", processes[2])
        self.assertNotIn("token", processes[3])
        self.assertNotIn("authorization", processes[3])
        self.assertNotIn("C:\\Users", str(cleaned))
        self.assertNotIn("secret-token", str(cleaned))

    def test_safe_result_data_redacts_secrets_embedded_in_allowed_strings(self) -> None:
        cleaned = DesktopSatelliteService._safe_result_data(
            {
                "message": "authorization=private-value Bearer abc.def",
                "nested": {"detail": "/home/alice/private.txt"},
            }
        )
        serialized = json.dumps(cleaned, ensure_ascii=False)
        self.assertNotIn("private-value", serialized)
        self.assertNotIn("abc.def", serialized)
        self.assertNotIn("/home/alice", serialized)
        self.assertIn("[redacted]", serialized)

    def test_safe_reason_maps_known_codes_and_rejects_unknown(self) -> None:
        self.assertEqual(
            DesktopSatelliteService._safe_reason("process_enumeration_failed"),
            "process_enumeration_failed",
        )
        self.assertEqual(
            DesktopSatelliteService._safe_reason("unsupported_platform"),
            "unsupported_platform",
        )
        self.assertEqual(DesktopSatelliteService._safe_reason("something_unknown"), "executor_failed")

    def test_feedback_mentions_the_display_name(self) -> None:
        self.assertIn(
            "Read system process list",
            DesktopSatelliteService._success_feedback("system_process_snapshot"),
        )
        self.assertIn(
            "Read system process list",
            DesktopSatelliteService._failure_feedback("system_process_snapshot", rejected=False),
        )


if __name__ == "__main__":
    unittest.main()
