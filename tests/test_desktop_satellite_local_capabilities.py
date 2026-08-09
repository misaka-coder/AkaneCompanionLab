from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace

import config
from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion_v01.capability_registry import (
    BrokerExecutionResult,
    CapabilityRegistry,
    CapabilitySnapshot,
    ExecutorBroker,
)
from companion_v01.client_protocol import ClientMode
from companion_v01.deployment_security import AdminWriteAuth
from companion_v01.desktop_satellite import DesktopSatelliteService
from companion_v01.desktop_satellite_specs import DESKTOP_SATELLITE_TOOL_SPECS
from companion_v01.routes.satellite import build_satellite_router
from companion_v01.tool_invocation import (
    NATIVE_OPENAI,
    TOOL_EXECUTION_RECEIPT_FIELD,
    TOOL_INVOCATION_ID_FIELD,
    TOOL_SOURCE_FIELD,
    ToolInvocation,
)
from companion_v01.tool_orchestration_engine import (
    build_native_tool_schemas,
    execute_tool_invocation,
    normalize_tool_invocation,
    validate_tool_invocation,
)
from companion_v01.tool_runtime import DesktopSatelliteToolHandler


def _registration(instance_id: str) -> dict[str, object]:
    return {
        "type": "register",
        "protocol_version": 1,
        "instance_id": instance_id,
        "offers": [
            {
                "tool_id": spec.capability_id,
                "spec_version": spec.spec_version,
                "schema_version": spec.schema_version,
                "schema_hash": spec.schema_hash,
            }
            for spec in DESKTOP_SATELLITE_TOOL_SPECS
        ],
    }


def _execution_message(
    message_type: str,
    registered: dict[str, object],
    invocation_id: str,
    tool_id: str,
    *,
    status: str = "",
    reason: str = "",
    data: dict[str, object] | None = None,
) -> dict[str, object]:
    offer_ids = registered["offer_ids"]
    assert isinstance(offer_ids, dict)
    payload: dict[str, object] = {
        "type": message_type,
        "protocol_version": 1,
        "instance_id": registered["instance_id"],
        "lease_epoch": registered["lease_epoch"],
        "offer_id": offer_ids[tool_id],
        "invocation_id": invocation_id,
        "tool_id": tool_id,
    }
    if status:
        payload["status"] = status
    if reason:
        payload["reason"] = reason
    if data is not None:
        payload["data"] = data
    return payload


class DesktopSatelliteLocalCapabilitiesTests(unittest.TestCase):
    @staticmethod
    def _app(service: DesktopSatelliteService) -> FastAPI:
        app = FastAPI()
        app.include_router(
            build_satellite_router(
                satellite_service=service,
                admin_auth=AdminWriteAuth.local_compatibility(),
            )
        )
        return app

    def test_local_tool_schema_is_stable_while_execution_receipts_follow_readiness(self) -> None:
        service = DesktopSatelliteService(instance_id="instance-a", token="device-secret")
        registry = CapabilityRegistry(offer_source=service)
        expected_ids = {spec.capability_id for spec in DESKTOP_SATELLITE_TOOL_SPECS}
        expected_schema_ids = expected_ids | {"open_browser"}

        offline_by_mode = {
            mode: registry.select(CapabilitySnapshot(client_mode=mode))
            for mode in (ClientMode.DESKTOP_PET, ClientMode.QQ_TEXT)
        }
        for offline in offline_by_mode.values():
            self.assertTrue(expected_ids.isdisjoint(offline.tool_names))
            self.assertTrue(expected_schema_ids.issubset(offline.schema_tool_names))
            self.assertEqual({spec.capability_id for spec in offline.tool_specs}, expected_schema_ids)

        with TestClient(self._app(service)) as client:
            with client.websocket_connect(
                "/capabilities/satellite/ws",
                headers={"Authorization": "Bearer device-secret"},
            ) as websocket:
                websocket.receive_json()
                websocket.send_json(_registration("instance-a"))
                registered = websocket.receive_json()
                self.assertEqual(set(registered["offer_ids"]), expected_ids)

                for mode in (ClientMode.DESKTOP_PET, ClientMode.QQ_TEXT):
                    selection = registry.select(CapabilitySnapshot(client_mode=mode))
                    self.assertTrue(expected_ids.issubset(selection.tool_names))
                    self.assertEqual(selection.schema_tool_names, offline_by_mode[mode].schema_tool_names)
                    self.assertEqual(
                        {spec.capability_id for spec in selection.tool_specs},
                        expected_schema_ids,
                    )
                    for spec in DESKTOP_SATELLITE_TOOL_SPECS:
                        receipt = selection.execution_receipts[spec.capability_id]
                        self.assertEqual(receipt["schema_hash"], spec.schema_hash)
                        self.assertEqual(receipt["instance_id"], "instance-a")

    def test_one_host_connection_dispatches_isolated_invocations_for_two_bots(self) -> None:
        service = DesktopSatelliteService(instance_id="host-a", token="device-secret")
        bot_a_source = service.for_bot(bot_id="bot-a", memory_space_id="memory-a")
        bot_b_source = service.for_bot(bot_id="bot-b", memory_space_id="memory-b")
        spec = next(item for item in DESKTOP_SATELLITE_TOOL_SPECS if item.capability_id == "system_media_snapshot")
        results: dict[str, BrokerExecutionResult] = {}

        with TestClient(self._app(service)) as client:
            with client.websocket_connect(
                "/capabilities/satellite/ws",
                headers={"Authorization": "Bearer device-secret"},
            ) as websocket:
                websocket.receive_json()
                websocket.send_json(_registration("host-a"))
                registered = websocket.receive_json()
                receipt_a = bot_a_source.resolve_receipt(spec)
                receipt_b = bot_b_source.resolve_receipt(spec)
                self.assertIsNotNone(receipt_a)
                self.assertIsNotNone(receipt_b)
                assert receipt_a is not None
                assert receipt_b is not None
                self.assertNotEqual(receipt_a.instance_id, receipt_b.instance_id)
                self.assertEqual(bot_a_source.validate_receipt(spec, receipt_a), "")
                self.assertEqual(
                    bot_b_source.validate_receipt(spec, receipt_a),
                    "receipt_instance_mismatch",
                )

                wire_invocation_ids: list[str] = []
                for result_key, source, receipt in (
                    ("bot-a", bot_a_source, receipt_a),
                    ("bot-b", bot_b_source, receipt_b),
                ):
                    broker = ExecutorBroker(source)

                    def execute(
                        *,
                        key: str = result_key,
                        active_broker: ExecutorBroker = broker,
                        active_receipt=receipt,
                    ) -> None:
                        results[key] = active_broker.execute(
                            spec=spec,
                            receipt_value=active_receipt.as_dict(),
                            invocation_id="call_shared",
                            arguments={},
                        )

                    worker = threading.Thread(target=execute, daemon=True)
                    worker.start()
                    invoke = websocket.receive_json()
                    self.assertEqual(invoke["instance_id"], "host-a")
                    wire_invocation_id = str(invoke["invocation_id"])
                    wire_invocation_ids.append(wire_invocation_id)
                    self.assertTrue(wire_invocation_id.startswith("inv_"))
                    self.assertEqual(len(wire_invocation_id), 68)
                    websocket.send_json(
                        _execution_message(
                            "result",
                            registered,
                            wire_invocation_id,
                            spec.capability_id,
                            status="succeeded",
                            data={"ok": True, "bot": result_key},
                        )
                    )
                    worker.join(timeout=5)
                    self.assertFalse(worker.is_alive())

                self.assertNotEqual(wire_invocation_ids[0], wire_invocation_ids[1])

        self.assertEqual(results["bot-a"].status, "succeeded")
        self.assertEqual(results["bot-b"].status, "succeeded")
        self.assertEqual(results["bot-a"].data["bot"], "bot-a")
        self.assertEqual(results["bot-b"].data["bot"], "bot-b")

    def test_invalid_arguments_fail_before_broker_dispatch(self) -> None:
        handlers = {
            spec.capability_id: DesktopSatelliteToolHandler(tool_id=spec.capability_id)
            for spec in DESKTOP_SATELLITE_TOOL_SPECS
        }
        engine = SimpleNamespace(tool_handlers=handlers)
        valid = ToolInvocation(
            name="system_media_control",
            arguments={"action": "pause"},
            execution_receipt={"receipt": "present"},
        )
        invalid = ToolInvocation(
            name="system_media_control",
            arguments={"action": "volume_up"},
            execution_receipt={"receipt": "present"},
        )
        self.assertTrue(validate_tool_invocation(engine, valid).ok)
        rejected = validate_tool_invocation(engine, invalid)
        self.assertFalse(rejected.ok)
        self.assertEqual(rejected.code, "bad_args")

    def test_native_invocation_metadata_is_not_treated_as_tool_arguments(self) -> None:
        handler = DesktopSatelliteToolHandler(tool_id="desktop_context_snapshot")
        normalized = handler.normalize_call(
            {
                "type": "desktop_context_snapshot",
                TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                TOOL_INVOCATION_ID_FIELD: "call_native_context",
                TOOL_EXECUTION_RECEIPT_FIELD: {"offer_id": "offer-a"},
            }
        )
        self.assertEqual(normalized, {"type": "desktop_context_snapshot"})

    def test_generic_normalization_preserves_private_execution_receipt(self) -> None:
        handler = DesktopSatelliteToolHandler(tool_id="desktop_context_snapshot")
        receipt = {"offer_id": "offer-a", "lease_epoch": "lease-a"}
        engine = SimpleNamespace(
            _resolve_tool_handlers=lambda **_kwargs: {"desktop_context_snapshot": handler}
        )
        invocation = normalize_tool_invocation(
            engine,
            {
                "type": "desktop_context_snapshot",
                TOOL_SOURCE_FIELD: NATIVE_OPENAI,
                TOOL_INVOCATION_ID_FIELD: "call_native_context",
                TOOL_EXECUTION_RECEIPT_FIELD: receipt,
            },
        )
        self.assertIsNotNone(invocation)
        assert invocation is not None
        self.assertEqual(invocation.execution_receipt, receipt)

    def test_online_local_tools_project_to_native_schemas(self) -> None:
        handlers = {
            spec.capability_id: DesktopSatelliteToolHandler(tool_id=spec.capability_id)
            for spec in DESKTOP_SATELLITE_TOOL_SPECS
        }
        expected_ids = set(handlers)
        original_enabled = config.ENABLE_NATIVE_TOOL_DECISION
        original_allowlist = config.NATIVE_TOOL_DECISION_ALLOWLIST
        try:
            config.ENABLE_NATIVE_TOOL_DECISION = True
            config.NATIVE_TOOL_DECISION_ALLOWLIST = "web_search"
            schemas = build_native_tool_schemas(
                handlers,
                allow_tool_call=True,
                allowed_tool_names=expected_ids,
            )
        finally:
            config.ENABLE_NATIVE_TOOL_DECISION = original_enabled
            config.NATIVE_TOOL_DECISION_ALLOWLIST = original_allowlist
        self.assertEqual(
            {item["function"]["name"] for item in schemas},
            expected_ids,
        )

    def test_result_tool_id_is_bound_and_sensitive_data_is_removed(self) -> None:
        service = DesktopSatelliteService(instance_id="instance-a", token="device-secret")
        broker = ExecutorBroker(service)
        spec = next(item for item in DESKTOP_SATELLITE_TOOL_SPECS if item.capability_id == "system_media_snapshot")
        result_box: dict[str, BrokerExecutionResult] = {}

        with TestClient(self._app(service)) as client:
            with client.websocket_connect(
                "/capabilities/satellite/ws",
                headers={"Authorization": "Bearer device-secret"},
            ) as websocket:
                websocket.receive_json()
                websocket.send_json(_registration("instance-a"))
                registered = websocket.receive_json()
                receipt = service.resolve_receipt(spec)
                self.assertIsNotNone(receipt)
                assert receipt is not None

                def execute() -> None:
                    result_box["result"] = broker.execute(
                        spec=spec,
                        receipt_value=receipt.as_dict(),
                        invocation_id="call_media_snapshot",
                        arguments={},
                    )

                worker = threading.Thread(target=execute, daemon=True)
                worker.start()
                invoke = websocket.receive_json()
                self.assertEqual(invoke["tool_id"], spec.capability_id)

                websocket.send_json(
                    _execution_message(
                        "result",
                        registered,
                        "call_media_snapshot",
                        "desktop_context_snapshot",
                        status="succeeded",
                    )
                )
                worker.join(timeout=0.05)
                self.assertTrue(worker.is_alive())

                websocket.send_json(
                    _execution_message(
                        "accepted",
                        registered,
                        "call_media_snapshot",
                        spec.capability_id,
                    )
                )
                websocket.send_json(
                    _execution_message(
                        "result",
                        registered,
                        "call_media_snapshot",
                        spec.capability_id,
                        status="succeeded",
                        data={
                            "ok": True,
                            "status": "ready",
                            "title": "Song",
                            "path": "private-path",
                            "apiToken": "private-token",
                            "nested": {"authorization": "private-auth", "artist": "Artist"},
                        },
                    )
                )
                worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        result = result_box["result"]
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.data["title"], "Song")
        self.assertNotIn("path", result.data)
        self.assertNotIn("apiToken", result.data)
        self.assertEqual(result.data["nested"], {"artist": "Artist"})
        self.assertNotIn("private-", str(result))

    def test_real_executor_data_reaches_tool_result_envelope(self) -> None:
        spec = next(item for item in DESKTOP_SATELLITE_TOOL_SPECS if item.capability_id == "desktop_context_snapshot")

        class DataBroker:
            def execute(self, **_kwargs):
                return BrokerExecutionResult(
                    status="succeeded",
                    model_feedback="真实读取完成。",
                    data={"ok": True, "foreground": {"processName": "editor"}},
                )

        handler = DesktopSatelliteToolHandler(tool_id=spec.capability_id)
        engine = SimpleNamespace(
            tool_handlers={spec.capability_id: handler},
            executor_broker=DataBroker(),
        )
        invocation = ToolInvocation(
            name=spec.capability_id,
            arguments={},
            execution_receipt={"receipt": "present"},
        )
        result, envelope = execute_tool_invocation(
            engine,
            invocation=invocation,
            profile_user_id="master",
            session_id="s1",
            visual_payload={},
            now_ts=1000,
        )
        self.assertEqual(envelope.status, "ok")
        self.assertEqual(envelope.data["result"]["foreground"]["processName"], "editor")
        self.assertIn("真实读取完成", result.followup_context)


if __name__ == "__main__":
    unittest.main()
