from __future__ import annotations

import http.server
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from companion_v01.capability_registry import (
    OPEN_BROWSER_TOOL_SPEC,
    BrokerExecutionResult,
    CapabilityRegistry,
    CapabilitySnapshot,
    ExecutorBroker,
)
from companion_v01.client_protocol import ClientMode
from companion_v01.deployment_security import AdminWriteAuth
from companion_v01.desktop_satellite import DesktopSatelliteService
from companion_v01.desktop_satellite_specs import DESKTOP_SATELLITE_TOOL_SPECS
from companion_v01.engine import AkaneMemoryEngine
from companion_v01.native_tool_schema import build_openai_native_tool_specs
from companion_v01.routes.satellite import build_satellite_router
from companion_v01.settings_catalog import MANAGED_DEPLOYMENT, get_spec
from companion_v01.tool_invocation import TOOL_EXECUTION_RECEIPT_FIELD, TOOL_EXECUTION_RECEIPTS_FIELD
from companion_v01.tool_orchestration_engine import (
    execute_tool_invocation,
    normalize_tool_invocation,
    validate_tool_invocation,
)
from companion_v01.tool_runtime import OpenBrowserToolHandler


ROOT = Path(__file__).resolve().parents[1]


class MutableClock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _registration(instance_id: str, *, schema_hash: str | None = None) -> dict[str, object]:
    return {
        "type": "register",
        "protocol_version": 1,
        "instance_id": instance_id,
        "offers": [
            {
                "tool_id": OPEN_BROWSER_TOOL_SPEC.capability_id,
                "spec_version": OPEN_BROWSER_TOOL_SPEC.spec_version,
                "schema_version": OPEN_BROWSER_TOOL_SPEC.schema_version,
                "schema_hash": schema_hash or OPEN_BROWSER_TOOL_SPEC.schema_hash,
            }
        ],
    }


def _execution_message(
    message_type: str,
    registered: dict[str, object],
    invocation_id: str,
    *,
    status: str = "",
) -> dict[str, object]:
    offer_ids = registered["offer_ids"]
    assert isinstance(offer_ids, dict)
    payload: dict[str, object] = {
        "type": message_type,
        "protocol_version": 1,
        "instance_id": registered["instance_id"],
        "lease_epoch": registered["lease_epoch"],
        "offer_id": offer_ids[OPEN_BROWSER_TOOL_SPEC.capability_id],
        "invocation_id": invocation_id,
        "tool_id": OPEN_BROWSER_TOOL_SPEC.capability_id,
    }
    if status:
        payload["status"] = status
    return payload


class CapabilityFabricM66Tests(unittest.TestCase):
    def _app(self, service: DesktopSatelliteService) -> FastAPI:
        app = FastAPI()
        app.include_router(
            build_satellite_router(
                satellite_service=service,
                admin_auth=AdminWriteAuth.local_compatibility(),
            )
        )
        return app

    def test_satellite_rejects_bad_token_instance_and_schema(self) -> None:
        service = DesktopSatelliteService(instance_id="instance-a", token="device-secret")
        with TestClient(self._app(service)) as client:
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect(
                    "/capabilities/satellite/ws",
                    headers={"Authorization": "Bearer wrong-secret"},
                ):
                    pass

            with client.websocket_connect(
                "/capabilities/satellite/ws",
                headers={"Authorization": "Bearer device-secret"},
            ) as websocket:
                websocket.receive_json()
                websocket.send_json(_registration("instance-b"))
                with self.assertRaises(WebSocketDisconnect) as mismatch:
                    websocket.receive_json()
                self.assertEqual(mismatch.exception.code, 4403)

            with client.websocket_connect(
                "/capabilities/satellite/ws",
                headers={"Authorization": "Bearer device-secret"},
            ) as websocket:
                websocket.receive_json()
                websocket.send_json(_registration("instance-a", schema_hash="sha256:wrong"))
                with self.assertRaises(WebSocketDisconnect) as mismatch:
                    websocket.receive_json()
                self.assertEqual(mismatch.exception.code, 4403)

        self.assertEqual(service.diagnostics()["status"], "offline")
        self.assertNotIn("device-secret", str(service.diagnostics()))

    def test_satellite_diagnostics_require_admin_auth_without_secret_leak(self) -> None:
        service = DesktopSatelliteService(instance_id="instance-a", token="device-secret")
        app = FastAPI()
        app.include_router(
            build_satellite_router(
                satellite_service=service,
                admin_auth=AdminWriteAuth(
                    token="admin-secret",
                    require_token=True,
                    allow_loopback_without_token=False,
                ),
            )
        )
        with TestClient(app) as client:
            denied = client.get("/capabilities/satellite/status")
            allowed = client.get(
                "/capabilities/satellite/status",
                headers={"Authorization": "Bearer admin-secret"},
            )
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.json()["reason"], "admin_auth_required")
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["status"], "offline")
        serialized = str(allowed.json())
        self.assertNotIn("device-secret", serialized)
        self.assertNotIn("admin-secret", serialized)

    def test_offer_controls_desktop_and_qq_schema_and_frozen_receipt(self) -> None:
        clock = MutableClock()
        service = DesktopSatelliteService(
            instance_id="instance-a",
            token="device-secret",
            clock=clock,
        )
        registry = CapabilityRegistry(offer_source=service)
        offline = registry.select(
            CapabilitySnapshot(client_mode=ClientMode.DESKTOP_PET),
            intent_text="请用浏览器打开这个网页 https://example.com",
        )
        self.assertNotIn("open_browser", offline.tool_names)
        stable_offer_schema = {"open_browser", *(spec.capability_id for spec in DESKTOP_SATELLITE_TOOL_SPECS)}
        self.assertTrue(stable_offer_schema.issubset(offline.schema_tool_names))
        self.assertEqual({spec.capability_id for spec in offline.tool_specs}, stable_offer_schema)
        self.assertEqual(offline.disclosures[-1].state, "unavailable")

        with TestClient(self._app(service)) as client:
            with client.websocket_connect(
                "/capabilities/satellite/ws",
                headers={"Authorization": "Bearer device-secret"},
            ) as websocket:
                hello = websocket.receive_json()
                self.assertEqual(hello["instance_id"], "instance-a")
                websocket.send_json(_registration("instance-a"))
                websocket.receive_json()

                for mode in (ClientMode.DESKTOP_PET, ClientMode.QQ_TEXT):
                    selection = registry.select(CapabilitySnapshot(client_mode=mode))
                    self.assertIn("open_browser", selection.tool_names)
                    self.assertTrue(stable_offer_schema.issubset(selection.schema_tool_names))
                    self.assertEqual({spec.capability_id for spec in selection.tool_specs}, stable_offer_schema)
                    self.assertEqual(
                        selection.execution_receipts["open_browser"]["instance_id"],
                        "instance-a",
                    )

                selection = registry.select(CapabilitySnapshot(client_mode=ClientMode.DESKTOP_PET))
                raw_call = {
                    "type": "open_browser",
                    "url": "https://example.com/docs",
                    TOOL_EXECUTION_RECEIPT_FIELD: selection.execution_receipts["open_browser"],
                }
                engine = SimpleNamespace(tool_handlers={"open_browser": OpenBrowserToolHandler()})
                invocation = normalize_tool_invocation(engine, raw_call)
                self.assertIsNotNone(invocation)
                assert invocation is not None
                self.assertEqual(
                    invocation.execution_receipt["offer_id"], selection.execution_receipts["open_browser"]["offer_id"]
                )
                self.assertTrue(validate_tool_invocation(engine, invocation).ok)

                native = build_openai_native_tool_specs(
                    {"open_browser": OpenBrowserToolHandler()},
                    allowed_tool_names={"open_browser"},
                )
                self.assertEqual(native[0]["function"]["description"], OPEN_BROWSER_TOOL_SPEC.description)
                self.assertEqual(native[0]["function"]["parameters"], OPEN_BROWSER_TOOL_SPEC.input_schema)

        clock.value += 31
        self.assertNotIn(
            "open_browser",
            registry.select(CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT)).tool_names,
        )
        self.assertTrue(
            stable_offer_schema.issubset(
                registry.select(CapabilitySnapshot(client_mode=ClientMode.QQ_TEXT)).schema_tool_names
            )
        )

    def test_broker_requires_exact_instance_live_lease_and_real_result(self) -> None:
        clock = MutableClock()
        service = DesktopSatelliteService(
            instance_id="instance-a",
            token="device-secret",
            clock=clock,
        )
        broker = ExecutorBroker(service, clock=clock)
        with TestClient(self._app(service)) as client:
            with client.websocket_connect(
                "/capabilities/satellite/ws",
                headers={"Authorization": "Bearer device-secret"},
            ) as websocket:
                websocket.receive_json()
                websocket.send_json(_registration("instance-a"))
                registered = websocket.receive_json()
                receipt = service.resolve_receipt(OPEN_BROWSER_TOOL_SPEC)
                self.assertIsNotNone(receipt)
                assert receipt is not None

                wrong_instance = receipt.as_dict()
                wrong_instance["instance_id"] = "instance-b"
                rejected = broker.execute(
                    spec=OPEN_BROWSER_TOOL_SPEC,
                    receipt_value=wrong_instance,
                    invocation_id="call_wrong_instance",
                    arguments={"url": "https://example.com"},
                )
                self.assertEqual(rejected.status, "unavailable_before_dispatch")
                self.assertEqual(rejected.reason, "receipt_instance_mismatch")

                result_box: dict[str, object] = {}

                def execute() -> None:
                    result_box["result"] = broker.execute(
                        spec=OPEN_BROWSER_TOOL_SPEC,
                        receipt_value=receipt.as_dict(),
                        invocation_id="call_real_ack",
                        arguments={"url": "https://example.com/docs"},
                    )

                worker = threading.Thread(target=execute, daemon=True)
                worker.start()
                invoke = websocket.receive_json()
                self.assertEqual(invoke["instance_id"], "instance-a")
                self.assertEqual(invoke["schema_hash"], OPEN_BROWSER_TOOL_SPEC.schema_hash)
                websocket.send_json(_execution_message("accepted", registered, "call_real_ack"))
                websocket.send_json(_execution_message("result", registered, "call_real_ack", status="succeeded"))
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())
                result = result_box["result"]
                self.assertEqual(result.status, "succeeded")
                self.assertNotIn("https://", str(result))

                duplicate = broker.execute(
                    spec=OPEN_BROWSER_TOOL_SPEC,
                    receipt_value=receipt.as_dict(),
                    invocation_id="call_real_ack",
                    arguments={"url": "https://example.com/docs"},
                )
                self.assertEqual(duplicate.status, "succeeded")

                expiring_receipt = service.resolve_receipt(OPEN_BROWSER_TOOL_SPEC)
                assert expiring_receipt is not None
                clock.value = expiring_receipt.offer_expires_at + 1
                expired = broker.execute(
                    spec=OPEN_BROWSER_TOOL_SPEC,
                    receipt_value=expiring_receipt.as_dict(),
                    invocation_id="call_expired",
                    arguments={"url": "https://example.com"},
                )
                self.assertEqual(expired.reason, "offer_expired")

    def test_generation_receipt_is_private_and_frozen_into_tool_call(self) -> None:
        engine = AkaneMemoryEngine.__new__(AkaneMemoryEngine)
        receipt = {
            "instance_id": "instance-a",
            "tool_id": "open_browser",
            "offer_id": "offer-a",
            "lease_epoch": "lease-a",
            "offer_expires_at": 2000.0,
            "spec_version": OPEN_BROWSER_TOOL_SPEC.spec_version,
            "schema_version": OPEN_BROWSER_TOOL_SPEC.schema_version,
            "schema_hash": OPEN_BROWSER_TOOL_SPEC.schema_hash,
        }
        output = {"tool_call": {"type": "open_browser", "url": "https://example.com"}}
        engine._attach_tool_execution_receipts(
            output,
            {TOOL_EXECUTION_RECEIPTS_FIELD: {"open_browser": receipt}},
        )
        engine._normalize_tool_call = lambda value, **_kwargs: dict(value)
        engine._promote_narrated_tool_call = lambda value, **_kwargs: value
        final, calls, rejections = engine._prepare_tool_round_decisions(
            final_output=output,
            user_message="打开网页",
            client_context=SimpleNamespace(),
            profile_user_id="master",
            session_id="s1",
        )
        self.assertEqual(rejections, [])
        self.assertEqual(calls[0][TOOL_EXECUTION_RECEIPT_FIELD], receipt)
        self.assertNotIn(TOOL_EXECUTION_RECEIPTS_FIELD, final)

    def test_broker_failure_is_not_shaped_as_success(self) -> None:
        class FailedBroker:
            def execute(self, **_kwargs):
                return BrokerExecutionResult(
                    status="execution_unknown",
                    reason="executor_disconnected_after_accept",
                    model_feedback="结果无法确认，请不要声称已经打开。",
                )

        receipt = {
            "instance_id": "instance-a",
            "tool_id": "open_browser",
            "offer_id": "offer-a",
            "lease_epoch": "lease-a",
            "offer_expires_at": 2000.0,
            "spec_version": OPEN_BROWSER_TOOL_SPEC.spec_version,
            "schema_version": OPEN_BROWSER_TOOL_SPEC.schema_version,
            "schema_hash": OPEN_BROWSER_TOOL_SPEC.schema_hash,
        }
        engine = SimpleNamespace(
            tool_handlers={"open_browser": OpenBrowserToolHandler()},
            executor_broker=FailedBroker(),
        )
        invocation = normalize_tool_invocation(
            engine,
            {
                "type": "open_browser",
                "url": "https://example.com",
                TOOL_EXECUTION_RECEIPT_FIELD: receipt,
            },
        )
        assert invocation is not None
        result, envelope = execute_tool_invocation(
            engine,
            invocation=invocation,
            profile_user_id="master",
            session_id="s1",
            visual_payload={},
            now_ts=1000,
        )
        self.assertEqual(envelope.status, "error")
        self.assertEqual(envelope.data["status"], "execution_unknown")
        self.assertEqual(result.stream_events[0]["type"], "capability_execution_result")
        self.assertTrue(AkaneMemoryEngine.__new__(AkaneMemoryEngine)._tool_result_is_error(result))
        self.assertNotIn("https://", str(envelope.data))

    def test_disconnect_before_and_after_accept_have_distinct_safe_outcomes(self) -> None:
        for acknowledged, expected_status in (
            (False, "unavailable_before_dispatch"),
            (True, "execution_unknown"),
        ):
            with self.subTest(acknowledged=acknowledged):
                service = DesktopSatelliteService(instance_id="instance-a", token="device-secret")
                broker = ExecutorBroker(service)
                result_box: dict[str, object] = {}
                with TestClient(self._app(service)) as client:
                    with client.websocket_connect(
                        "/capabilities/satellite/ws",
                        headers={"Authorization": "Bearer device-secret"},
                    ) as websocket:
                        websocket.receive_json()
                        websocket.send_json(_registration("instance-a"))
                        registered = websocket.receive_json()
                        receipt = service.resolve_receipt(OPEN_BROWSER_TOOL_SPEC)
                        assert receipt is not None

                        def execute() -> None:
                            result_box["result"] = broker.execute(
                                spec=OPEN_BROWSER_TOOL_SPEC,
                                receipt_value=receipt.as_dict(),
                                invocation_id=f"call_disconnect_{acknowledged}",
                                arguments={"url": "https://example.com"},
                                timeout_seconds=4,
                            )

                        worker = threading.Thread(target=execute, daemon=True)
                        worker.start()
                        websocket.receive_json()
                        if acknowledged:
                            websocket.send_json(
                                _execution_message(
                                    "accepted",
                                    registered,
                                    f"call_disconnect_{acknowledged}",
                                )
                            )
                    worker.join(timeout=5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(result_box["result"].status, expected_status)

    def test_device_secret_is_deployment_managed_and_not_a_public_setting(self) -> None:
        spec = get_spec("AKANE_DESKTOP_SATELLITE_TOKEN")
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertTrue(spec.sensitive)
        self.assertEqual(spec.managed_in, MANAGED_DEPLOYMENT)

    def test_cloud_launcher_has_no_backend_process_branch(self) -> None:
        source = (ROOT / "start_akane_next.ps1").read_text(encoding="utf-8")
        self.assertIn("if (-not $CloudSatellite -and -not $SkipBackend)", source)
        self.assertIn("cloud_satellite_instance_verification_failed", source)
        self.assertIn("cloud_satellite_token_required", source)
        self.assertIn("cloud_satellite_requires_https", source)
        self.assertIn("Import-AkaneSatelliteTokenForInstance", source)
        self.assertIn("$env:AKANE_DESKTOP_SATELLITE_TOKEN = New-AkaneSatelliteToken", source)
        cloud_guard = source.index("if (-not $CloudSatellite -and -not $SkipBackend)")
        self.assertLess(cloud_guard, source.index("Stop-AkaneBackendProcess", cloud_guard))
        self.assertLess(cloud_guard, source.index("Starting backend with", cloud_guard))
        build_guard = source.index("if ($shouldBuild)")
        self.assertLess(
            source.index("Stop-AkaneDesktopProcesses -ExePath $releaseExe", build_guard),
            source.index("& npm run tauri -- build", build_guard),
        )

    def test_personal_cloud_launcher_owns_tunnel_and_offer_verification(self) -> None:
        source = (ROOT / "start_akane_cloud_personal.ps1").read_text(encoding="utf-8")
        batch = (ROOT / "start_akane_cloud_personal.bat").read_text(encoding="utf-8")

        self.assertIn('"-N"', source)
        self.assertIn('"ExitOnForwardFailure=yes"', source)
        self.assertIn('"ServerAliveInterval=30"', source)
        self.assertIn('"-R"', source)
        self.assertIn("$GptSoVitsLocalPort = 9880", source)
        self.assertIn("$GptSoVitsRemotePort = 19880", source)
        self.assertIn("AKANE_GPT_SOVITS_ROOT", source)
        self.assertIn("api_v2.py", source)
        self.assertIn("GPT_SoVITS/configs/tts_infer.yaml", source)
        self.assertIn("gpt_sovits_api.pid", source)
        self.assertIn("gpt_sovits_reverse_tunnel.pid", source)
        self.assertIn("Get-AkaneGptSoVitsApi", source)
        self.assertIn("Get-AkaneRemoteGptSoVitsApi", source)
        self.assertIn("Invoke-AkaneCloudGptSoVitsHealthCheck", source)
        self.assertIn("cloud_gpt_sovits_health_refresh_failed", source)
        self.assertIn('"/tts"', source)
        self.assertIn("Get-CimInstance Win32_Process", source)
        self.assertIn("$actualExecutableValue = ([string]$process.ExecutablePath).Trim()", source)
        self.assertIn("if (-not $actualExecutableValue -or -not $expectedExecutableValue)", source)
        self.assertIn("catch {", source)
        self.assertIn("Stop-Process -Id $storedProcessId -Force", source)
        self.assertIn("Import-AkanePersonalSatelliteToken", source)
        self.assertIn("AKANE_DESKTOP_SATELLITE_TOKEN_$suffix", source)
        self.assertIn("Test-AkaneCloudHealth", source)
        self.assertIn("CloudSatellite = $true", source)
        self.assertIn("tool.open_browser", source)
        self.assertIn("tool.desktop_context_snapshot", source)
        self.assertIn("tool.system_media_snapshot", source)
        self.assertIn("tool.system_media_control", source)
        self.assertNotIn("AKANE_ADMIN_TOKEN", source)
        self.assertNotRegex(source, r"AKANE_DESKTOP_SATELLITE_TOKEN\s*=\s*[\"'][^\"']{16,}")
        self.assertIn("start_akane_cloud_personal.ps1", batch)

    def test_local_media_launcher_accepts_ready_in_process_demucs(self) -> None:
        source = (ROOT / "scripts" / "start_akane_local_media.ps1").read_text(encoding="utf-8")

        self.assertIn("-not [bool]$health.separation.ready", source)
        self.assertNotIn('$health.separation.executor -notlike "isolated_*"', source)

    def test_personal_cloud_launcher_reuses_verified_loopback_tunnel(self) -> None:
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            self.skipTest("PowerShell is unavailable")

        class HealthHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path != "/health":
                    self.send_response(404)
                    self.end_headers()
                    return
                body = b'{"status":"ok","instance_id":"personal","root_binding":"valid"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                return

        health_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
        health_thread = threading.Thread(target=health_server.serve_forever, daemon=True)
        health_thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                env = dict(os.environ)
                env["AKANE_DESKTOP_SATELLITE_TOKEN_PERSONAL"] = "test-personal-device-token"
                result = subprocess.run(
                    [
                        powershell,
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(ROOT / "start_akane_cloud_personal.ps1"),
                        "-LocalPort",
                        str(health_server.server_port),
                        "-DataRoot",
                        temp_dir,
                        "-SkipDesktop",
                        "-SkipOfferCheck",
                        "-SkipLocalMedia",
                        "-SkipGptSoVits",
                    ],
                    cwd=ROOT,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Reusing the verified cloud tunnel", result.stdout)
                self.assertIn("Cloud instance verified: personal", result.stdout)
                self.assertIn("Akane cloud personal startup completed", result.stdout)
        finally:
            health_server.shutdown()
            health_server.server_close()
            health_thread.join(timeout=3)

    def test_cloud_launcher_real_smoke_verifies_remote_without_touching_local_port(self) -> None:
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            self.skipTest("PowerShell is unavailable")

        class HealthHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path != "/health":
                    self.send_response(404)
                    self.end_headers()
                    return
                instance_id = str(getattr(self.server, "instance_id", "instance-a"))
                body = ('{"status":"ok","instance_id":"%s","root_binding":"valid"}' % instance_id).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                return

        health_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
        health_thread = threading.Thread(target=health_server.serve_forever, daemon=True)
        health_thread.start()
        sentinel = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sentinel.bind(("127.0.0.1", 0))
        sentinel.listen(1)
        sentinel.settimeout(0.2)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                data_root = Path(temp_dir)
                manifest = data_root / "instances" / "instance-a" / "instance.toml"
                manifest.parent.mkdir(parents=True)
                manifest.write_text(
                    'schema_version = 1\ninstance_id = "instance-a"\ncharacter_pack_id = "akane_v1"\n'
                    "[features]\ncare = true\n",
                    encoding="utf-8",
                )
                env = dict(os.environ)
                env["AKANE_DESKTOP_SATELLITE_TOKEN"] = "test-device-token"
                command = [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ROOT / "start_akane_next.ps1"),
                    "-CloudSatellite",
                    "-BackendUrl",
                    f"http://127.0.0.1:{health_server.server_port}",
                    "-InstanceId",
                    "instance-a",
                    "-DataRoot",
                    str(data_root),
                    "-BackendPort",
                    str(sentinel.getsockname()[1]),
                    "-SkipDesktop",
                    "-NoBuild",
                ]
                result = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("[INFO] Done.", result.stdout)
                health_server.instance_id = "instance-b"
                mismatch = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertNotEqual(mismatch.returncode, 0)
                self.assertIn("cloud_satellite_instance_verification_failed", mismatch.stdout + mismatch.stderr)
                with self.assertRaises((TimeoutError, socket.timeout)):
                    connection, _ = sentinel.accept()
                    connection.close()
        finally:
            sentinel.close()
            health_server.shutdown()
            health_server.server_close()
            health_thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
