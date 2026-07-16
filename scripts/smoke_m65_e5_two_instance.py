from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "akane.test.e5"
PLUGIN_CAPABILITY_ID = f"{PLUGIN_ID}.stateful-probe.v1"
SHARED_PROFILE_ID = "shared-profile"
SHARED_SESSION_ID = "shared-session"
SHARED_CHARACTER_ID = "shared-character"
SHARED_RECIPIENT_ID = "user:424242"
SHARED_NOTIFICATION_KEY = "shared-delivery-key"


@dataclass(frozen=True, slots=True)
class InstanceSpec:
    instance_id: str
    data_root: Path
    backend_port: int
    admin_token: str
    qq_profile_ref: str
    qq_bot_id: str
    qq_webhook_secret: str
    qq_access_token: str
    onebot_url: str
    model_api_key: str
    model_name: str
    model_base_url: str


@dataclass(slots=True)
class BackendHandle:
    process: subprocess.Popen[bytes]
    log_handle: BinaryIO
    log_path: Path


class CaptureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], *, kind: str, marker: str = "") -> None:
        super().__init__(address, CaptureHandler)
        self.kind = kind
        self.marker = marker
        self._records: list[dict[str, Any]] = []
        self._records_lock = threading.RLock()
        self._thread = threading.Thread(target=self.serve_forever, name=f"akane-e5-{kind}-{marker}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        self._thread.join(timeout=10)

    def capture(self, record: dict[str, Any]) -> None:
        with self._records_lock:
            self._records.append(dict(record))

    def records(self) -> list[dict[str, Any]]:
        with self._records_lock:
            return [dict(item) for item in self._records]


class CaptureHandler(BaseHTTPRequestHandler):
    server_version = "AkaneE5Capture/1.0"

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        server = self.server
        if not isinstance(server, CaptureServer):
            self.send_error(500)
            return
        try:
            content_length = max(0, min(2 * 1024 * 1024, int(self.headers.get("content-length") or 0)))
        except ValueError:
            content_length = 0
        raw_body = self.rfile.read(content_length) if content_length else b""
        try:
            body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {}
        server.capture(
            {
                "path": self.path,
                "authorization": str(self.headers.get("authorization") or ""),
                "body": body if isinstance(body, dict) else {},
            }
        )
        if server.kind == "llm" and self.path.endswith("/chat/completions"):
            self._write_json(self._llm_response(body if isinstance(body, dict) else {}))
            return
        if server.kind == "onebot":
            self._write_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": f"{server.marker}-{len(server.records())}"},
                }
            )
            return
        self._write_json({"ok": False, "reason": "not_found"}, status_code=404)

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _llm_response(self, body: dict[str, Any]) -> dict[str, Any]:
        marker = (
            "instance-a" if "/instance-a/" in self.path else "instance-b" if "/instance-b/" in self.path else "unknown"
        )
        speech = f"{marker}-reply"
        content = json.dumps(
            {
                "status": "ok",
                "emotion": "normal",
                "speech": speech,
                "speech_segments": [speech],
                "reply_medium": "text",
            },
            ensure_ascii=False,
        )
        return {
            "id": f"chatcmpl-e5-{marker}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": str(body.get("model") or "e5-model"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    def _write_json(self, payload: dict[str, Any], *, status_code: int = 200) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def free_ports(count: int) -> list[int]:
    sockets: list[socket.socket] = []
    try:
        for _index in range(count):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
        return [int(sock.getsockname()[1]) for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def request_json(
    url: str,
    *,
    token: str = "",
    body: dict[str, Any] | None = None,
    timeout: float = 12.0,
) -> tuple[int, dict[str, Any]]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw else {}
            return response.status, payload if isinstance(payload, dict) else {"value": payload}
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw[:500]}
        return error.code, payload if isinstance(payload, dict) else {"value": payload}


def request_bytes(url: str, *, timeout: float = 12.0) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"Accept": "*/*"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def request_multipart_file(
    url: str,
    *,
    path: Path,
    timeout: float = 12.0,
) -> tuple[int, dict[str, Any]]:
    boundary = f"akane-smoke-{time.time_ns()}"
    fields = {
        "user_id": SHARED_SESSION_ID,
        "session_id": SHARED_SESSION_ID,
        "real_user_id": SHARED_PROFILE_ID,
        "character_pack_id": SHARED_CHARACTER_ID,
    }
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("ascii"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode("ascii"))
    body.extend(
        (
            f'Content-Disposition: form-data; name="files"; filename="{path.name}"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).encode("utf-8")
    )
    body.extend(path.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode("ascii"))
    request = urllib.request.Request(
        url,
        data=bytes(body),
        headers={
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw else {}
            return response.status, payload if isinstance(payload, dict) else {"value": payload}
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw[:500]}
        return error.code, payload if isinstance(payload, dict) else {"value": payload}


def backend_url(spec: InstanceSpec, path: str) -> str:
    return f"http://127.0.0.1:{spec.backend_port}{path}"


def identity_query() -> str:
    return urllib.parse.urlencode(
        {
            "user_id": SHARED_SESSION_ID,
            "real_user_id": SHARED_PROFILE_ID,
            "character_pack_id": SHARED_CHARACTER_ID,
        }
    )


def identity_payload(**extra: Any) -> dict[str, Any]:
    return {
        "user_id": SHARED_SESSION_ID,
        "real_user_id": SHARED_PROFILE_ID,
        "client_mode": "desktop_pet",
        "character_pack_id": SHARED_CHARACTER_ID,
        **extra,
    }


def wait_health(spec: InstanceSpec, timeout: float = 120.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error = ""
    expected = {
        "status": "ok",
        "instance_id": spec.instance_id,
        "root_binding": "valid",
    }
    while time.monotonic() < deadline:
        try:
            status, payload = request_json(backend_url(spec, "/health"), timeout=3)
            if status == 200 and payload == expected:
                return payload
            last_error = f"unexpected health: {status} {payload}"
        except Exception as error:  # noqa: BLE001 - local smoke retains bounded failure type
            last_error = type(error).__name__
        time.sleep(0.35)
    raise RuntimeError(f"backend {spec.instance_id} did not become ready: {last_error}")


def write_instance_root(spec: InstanceSpec) -> None:
    manifest_path = spec.data_root / "instances" / spec.instance_id / "instance.toml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        "\n".join(
            (
                "schema_version = 1",
                f'instance_id = "{spec.instance_id}"',
                f'character_pack_id = "{SHARED_CHARACTER_ID}"',
                f'plugins = [{{ id = "{PLUGIN_ID}", enabled = true }}]',
                "",
                "[features]",
                "care = true",
                "",
                "[channels.qq]",
                "enabled = true",
                f'profile_ref = "{spec.qq_profile_ref}"',
                "",
            )
        ),
        encoding="utf-8",
    )
    character_path = spec.data_root / "characters" / SHARED_CHARACTER_ID / "character.json"
    character_path.parent.mkdir(parents=True, exist_ok=True)
    character_path.write_text(
        json.dumps(
            {
                "schema_version": "akane.character.v0.2",
                "identity": {"id": SHARED_CHARACTER_ID, "name": "Shared Character"},
                "appearance": {"default_outfit": "default", "default_emotion": "normal"},
                "care": {
                    "enabled": True,
                    "initial_coins": 10,
                    "initial_hunger": 50,
                    "initial_energy": 80,
                    "initial_affection": 15,
                    "shop_items": [
                        {
                            "id": "dango",
                            "name": "Dango",
                            "price": 4,
                            "usable_in": ["desktop_pet"],
                            "effects": {"hunger": 20, "energy": 3, "affection": 2},
                        }
                    ],
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def write_probe_distribution(root: Path) -> None:
    package_dir = root / "akane_e5_probe"
    dist_info = root / "akane_e5_probe-0.1.0.dist-info"
    package_dir.mkdir(parents=True, exist_ok=True)
    dist_info.mkdir(parents=True, exist_ok=True)
    package_dir.joinpath("__init__.py").write_text(
        textwrap.dedent(
            """
            from __future__ import annotations

            import json
            import os
            from pathlib import Path
            from typing import Any

            from capcore_adapter_python import PythonCapabilityAdapter, PythonCapabilitySpec

            from companion_v01.plugin_api import (
                AKANE_PLUGIN_API_VERSION,
                CAPABILITY_PROMPT_INVOKE_PERMISSION,
                NETWORK_READ_PERMISSION,
                NOTIFICATION_SEND_PERMISSION,
                PLUGIN_STORAGE_WRITE_PERMISSION,
                NotificationIntent,
                PluginManifest,
            )


            PLUGIN_ID = "akane.test.e5"
            CAPABILITY_ID = f"{PLUGIN_ID}.stateful-probe.v1"


            def _read_state(path: Path) -> dict[str, Any]:
                if not path.is_file():
                    return {}
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    return {}
                return payload if isinstance(payload, dict) else {}


            def _write_state(path: Path, payload: dict[str, Any]) -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
                temp_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\\n",
                    encoding="utf-8",
                )
                os.replace(temp_path, path)


            class E5ProbePlugin:
                manifest = PluginManifest(
                    plugin_id=PLUGIN_ID,
                    plugin_version="0.1.0",
                    plugin_api_version=AKANE_PLUGIN_API_VERSION,
                    permissions=(
                        CAPABILITY_PROMPT_INVOKE_PERMISSION,
                        NETWORK_READ_PERMISSION,
                        PLUGIN_STORAGE_WRITE_PERMISSION,
                        NOTIFICATION_SEND_PERMISSION,
                    ),
                )

                def register(self, registrar: Any) -> None:
                    instance_id = str(os.environ.get("AKANE_INSTANCE_ID") or "unknown")
                    storage_dir = registrar.get_storage_dir()
                    notification_port = registrar.get_notification_port()
                    state_path = storage_dir / "state.json"
                    _write_state(
                        storage_dir / "activation.json",
                        {"instance_id": instance_id, "plugin_id": PLUGIN_ID},
                    )

                    async def stateful_probe(
                        value: str = "",
                        idempotency_key: str = "",
                        recipient_id: str = "user:424242",
                    ) -> dict[str, Any]:
                        previous = _read_state(state_path)
                        generation = int(previous.get("generation") or 0) + 1
                        state = {
                            "instance_id": instance_id,
                            "plugin_id": PLUGIN_ID,
                            "value": str(value or ""),
                            "generation": generation,
                        }
                        _write_state(state_path, state)
                        result = await notification_port.send(
                            NotificationIntent(
                                channel="qq_text",
                                recipient_id=str(recipient_id or "user:424242"),
                                text=f"{instance_id}:{value}",
                                idempotency_key=str(idempotency_key or ""),
                            )
                        )
                        return {
                            **state,
                            "notification": {
                                "ok": bool(result.ok),
                                "status": str(result.status or ""),
                                "reason": str(result.reason or ""),
                            },
                        }

                    spec = PythonCapabilitySpec.from_callable(
                        stateful_probe,
                        capability_id=CAPABILITY_ID,
                        display_name="M65-E5 Stateful Probe",
                        short_hint="Exercise instance-owned plugin state and notification delivery.",
                        visible_in=("base",),
                        prompt_exposed=True,
                        risk="low",
                        confirm="never",
                        effects=("network",),
                        infer_inputs=True,
                    )
                    registrar.add_capability_adapter(
                        PythonCapabilityAdapter(
                            provider_id="provider.akane.test.e5",
                            capabilities=(spec,),
                        )
                    )


            def create_plugin() -> E5ProbePlugin:
                return E5ProbePlugin()
            """
        ).lstrip(),
        encoding="utf-8",
    )
    dist_info.joinpath("METADATA").write_text(
        "Metadata-Version: 2.1\nName: akane-e5-probe\nVersion: 0.1.0\n",
        encoding="utf-8",
    )
    dist_info.joinpath("entry_points.txt").write_text(
        f"[akane.plugins.v1]\n{PLUGIN_ID} = akane_e5_probe:create_plugin\n",
        encoding="utf-8",
    )


def instance_env(spec: InstanceSpec, plugin_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    inherited_pythonpath = str(env.get("PYTHONPATH") or "").strip()
    env.update(
        {
            "AKANE_DATA_ROOT": str(spec.data_root),
            "AKANE_INSTANCE_ID": spec.instance_id,
            "AKANE_ADMIN_TOKEN": spec.admin_token,
            "COMPANION_PORT": str(spec.backend_port),
            "QQ_BRIDGE_ENABLED": "false",
            "QQ_CHANNEL_PROFILE_REF": spec.qq_profile_ref,
            "QQ_BOT_QQ": spec.qq_bot_id,
            "QQ_ONEBOT_HTTP_URL": spec.onebot_url,
            "QQ_WEBHOOK_SECRET": spec.qq_webhook_secret,
            "QQ_ONEBOT_ACCESS_TOKEN": spec.qq_access_token,
            "TEXT_API_KEY": spec.model_api_key,
            "TEXT_BASE_URL": spec.model_base_url,
            "TEXT_MODEL_NAME": spec.model_name,
            "TEXT_API_PROTOCOL": "openai",
            "AUX_API_KEY": spec.model_api_key,
            "AUX_BASE_URL": spec.model_base_url,
            "AUX_MODEL_NAME": spec.model_name,
            "AUX_API_PROTOCOL": "openai",
            "CHAT_API_KEY": spec.model_api_key,
            "CHAT_BASE_URL": spec.model_base_url,
            "CHAT_MODEL_NAME": spec.model_name,
            "CHAT_API_PROTOCOL": "openai",
            "EMBEDDING_PROVIDER": "hashed",
            "VISION_ENABLED": "false",
            "MEMORY_BACKEND": "legacy",
            "ENABLE_SEMANTIC_MEMORY": "false",
            "PRE_RETRIEVAL_DEFAULT_ENABLED": "false",
            "SUMMARY_TRIGGER_COUNT": "1000",
            "PUBLIC_GUARD_ENABLED": "false",
            "PYTHONPATH": str(plugin_root) + (os.pathsep + inherited_pythonpath if inherited_pythonpath else ""),
        }
    )
    return env


def start_backend(
    spec: InstanceSpec,
    *,
    plugin_root: Path,
    log_suffix: str = "",
) -> BackendHandle:
    spec.data_root.joinpath("logs").mkdir(parents=True, exist_ok=True)
    suffix = f".{log_suffix}" if log_suffix else ""
    log_path = spec.data_root / "logs" / f"akane_backend.{spec.instance_id}{suffix}.log"
    log_handle = log_path.open("ab")
    creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "companion_v01.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(spec.backend_port),
                "--log-level",
                "warning",
            ],
            cwd=ROOT,
            env=instance_env(spec, plugin_root),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
    except Exception:
        log_handle.close()
        raise
    return BackendHandle(process=process, log_handle=log_handle, log_path=log_path)


def stop_backend(handle: BackendHandle, *, timeout: float = 20.0) -> None:
    try:
        if handle.process.poll() is None:
            handle.process.terminate()
        try:
            handle.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            handle.process.kill()
            handle.process.wait(timeout=10)
    finally:
        handle.log_handle.close()


def assert_client_storage_isolation() -> None:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required for the E5 client-cache acceptance")
    result = subprocess.run(
        [node, str(ROOT / "desktop_pet_next" / "scripts" / "instance-storage-smoke.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "instance storage smoke: ok" in result.stdout


def assert_qq_boundaries(a: InstanceSpec, b: InstanceSpec) -> None:
    event = {
        "post_type": "message",
        "message_type": "private",
        "self_id": a.qq_bot_id,
        "user_id": "424242",
        "message_id": "cross-secret",
        "message": "noop",
    }
    status, payload = request_json(
        backend_url(a, "/api/qq/napcat/event"),
        token=b.qq_webhook_secret,
        body=event,
    )
    assert status == 401 and payload.get("reason") == "qq_webhook_auth_required", payload
    wrong_identity = dict(event, self_id=b.qq_bot_id, message_id="cross-bot")
    status, payload = request_json(
        backend_url(a, "/api/qq/napcat/event"),
        token=a.qq_webhook_secret,
        body=wrong_identity,
    )
    assert status == 403 and payload.get("reason") == "qq_self_id_mismatch", payload

    event_b = dict(event, self_id=b.qq_bot_id, message_id="cross-secret-b")
    status, payload = request_json(
        backend_url(b, "/api/qq/napcat/event"),
        token=a.qq_webhook_secret,
        body=event_b,
    )
    assert status == 401 and payload.get("reason") == "qq_webhook_auth_required", payload


def save_model_configuration(spec: InstanceSpec, *, wrong_admin_token: str) -> None:
    payload = {
        "providerId": "openai_compatible",
        "protocol": "openai",
        "baseUrl": spec.model_base_url,
        "apiKey": spec.model_api_key,
        "chatModel": spec.model_name,
        "useForVision": False,
        "timeoutSeconds": 30,
    }
    denied_status, denied = request_json(
        backend_url(spec, "/control-center/model-service"),
        token=wrong_admin_token,
        body=payload,
    )
    assert denied_status == 401 and denied.get("reason") == "admin_auth_required", denied
    status, saved = request_json(
        backend_url(spec, "/control-center/model-service"),
        token=spec.admin_token,
        body=payload,
    )
    assert status == 200 and saved.get("ok") is True, saved
    assert saved.get("chatModel") == spec.model_name
    assert saved.get("baseUrl") == spec.model_base_url
    assert "apiKey" not in saved and spec.model_api_key not in json.dumps(saved, ensure_ascii=False)


def assert_model_configuration_isolation(a: InstanceSpec, b: InstanceSpec) -> None:
    save_model_configuration(a, wrong_admin_token=b.admin_token)
    save_model_configuration(b, wrong_admin_token=a.admin_token)
    for current, other in ((a, b), (b, a)):
        status, payload = request_json(backend_url(current, "/control-center/model-service"))
        assert status == 200 and payload.get("chatModel") == current.model_name, payload
        assert payload.get("baseUrl") == current.model_base_url
        assert payload.get("hasApiKey") is True
        assert current.model_api_key not in json.dumps(payload, ensure_ascii=False)
        config_path = current.data_root / "users_data" / "_local" / "model_service.json"
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved.get("api_key") == current.model_api_key
        assert saved.get("chat_model") == current.model_name
        assert other.model_api_key not in config_path.read_text(encoding="utf-8")
        assert other.model_name not in config_path.read_text(encoding="utf-8")


def session_payload(spec: InstanceSpec) -> dict[str, Any]:
    status, payload = request_json(
        backend_url(spec, "/sessions/ensure"),
        body=identity_payload(),
    )
    assert status == 200, payload
    return payload


def message_contents(payload: dict[str, Any]) -> list[str]:
    return [str(item.get("content") or "") for item in payload.get("messages", []) if isinstance(item, dict)]


def assert_memory_isolation(a: InstanceSpec, b: InstanceSpec, llm_server: CaptureServer) -> None:
    think_a = identity_payload(
        message="memory-a-only",
        pre_retrieval_enabled=False,
        timestamp=int(time.time()),
    )
    status, frame_a = request_json(backend_url(a, "/think_once"), body=think_a, timeout=30)
    assert status == 200 and frame_a.get("speech") == "instance-a-reply", frame_a

    status, b_before = request_json(backend_url(b, f"/sessions?{identity_query()}"))
    assert status == 200 and b_before.get("sessions") == [], b_before

    think_b = identity_payload(
        message="memory-b-only",
        pre_retrieval_enabled=False,
        timestamp=int(time.time()),
    )
    status, frame_b = request_json(backend_url(b, "/think_once"), body=think_b, timeout=30)
    assert status == 200 and frame_b.get("speech") == "instance-b-reply", frame_b

    a_messages = message_contents(session_payload(a))
    b_messages = message_contents(session_payload(b))
    assert "memory-a-only" in a_messages and "instance-a-reply" in a_messages
    assert "memory-b-only" not in a_messages and "instance-b-reply" not in a_messages
    assert "memory-b-only" in b_messages and "instance-b-reply" in b_messages
    assert "memory-a-only" not in b_messages and "instance-a-reply" not in b_messages

    records = llm_server.records()
    a_records = [item for item in records if "/instance-a/" in str(item.get("path") or "")]
    b_records = [item for item in records if "/instance-b/" in str(item.get("path") or "")]
    assert a_records and b_records
    assert all(item.get("authorization") == f"Bearer {a.model_api_key}" for item in a_records)
    assert all(item.get("authorization") == f"Bearer {b.model_api_key}" for item in b_records)
    assert all((item.get("body") or {}).get("model") == a.model_name for item in a_records)
    assert all((item.get("body") or {}).get("model") == b.model_name for item in b_records)


def care_snapshot(spec: InstanceSpec) -> dict[str, Any]:
    status, payload = request_json(
        backend_url(spec, "/desktop-pet/care/snapshot"),
        body=identity_payload(),
    )
    assert status == 200 and payload.get("ok") is True, payload
    snapshot = payload.get("snapshot")
    assert isinstance(snapshot, dict), payload
    return snapshot


def assert_care_isolation(a: InstanceSpec, b: InstanceSpec) -> None:
    initial_a = care_snapshot(a)
    initial_b = care_snapshot(b)
    assert initial_a.get("coins") == 10 and initial_b.get("coins") == 10
    status, bought = request_json(
        backend_url(a, "/desktop-pet/care/action"),
        body=identity_payload(action="buy", item_id="dango"),
    )
    assert status == 200 and bought.get("ok") is True, bought
    assert bought.get("snapshot", {}).get("coins") == 6
    assert bought.get("snapshot", {}).get("inventory") == {"dango": 1}
    untouched_b = care_snapshot(b)
    assert untouched_b.get("coins") == 10 and untouched_b.get("inventory") == {}

    a_state = a.data_root / "users_data" / "akane_memory_v01" / "care_runtime.json"
    b_state = b.data_root / "users_data" / "akane_memory_v01" / "care_runtime.json"
    assert a_state.is_file() and b_state.is_file()
    assert json.loads(a_state.read_text(encoding="utf-8")) != json.loads(b_state.read_text(encoding="utf-8"))


def assert_file_isolation(a: InstanceSpec, b: InstanceSpec, temp_root: Path) -> tuple[str, str]:
    source_a = temp_root / "incoming-a" / "shared.txt"
    source_b = temp_root / "incoming-b" / "shared.txt"
    source_a.parent.mkdir(parents=True, exist_ok=True)
    source_b.parent.mkdir(parents=True, exist_ok=True)
    source_a.write_text("file-a-only", encoding="utf-8")
    source_b.write_text("file-b-only", encoding="utf-8")

    status, imported_a = request_multipart_file(
        backend_url(a, "/desktop-pet/workspace/import-file"),
        path=source_a,
    )
    assert status == 200 and imported_a.get("imported") == 1, imported_a
    handle_a = str(imported_a.get("items", [{}])[0].get("handle") or "")
    assert handle_a
    missing_status, _missing = request_bytes(
        backend_url(b, f"/desktop-pet/workspace/attachments/{handle_a}/content?{identity_query()}")
    )
    assert missing_status == 404

    status, imported_b = request_multipart_file(
        backend_url(b, "/desktop-pet/workspace/import-file"),
        path=source_b,
    )
    assert status == 200 and imported_b.get("imported") == 1, imported_b
    handle_b = str(imported_b.get("items", [{}])[0].get("handle") or "")
    assert handle_b and handle_b == handle_a

    a_status, a_content = request_bytes(
        backend_url(a, f"/desktop-pet/workspace/attachments/{handle_a}/content?{identity_query()}")
    )
    b_status, b_content = request_bytes(
        backend_url(b, f"/desktop-pet/workspace/attachments/{handle_b}/content?{identity_query()}")
    )
    assert a_status == 200 and a_content == b"file-a-only"
    assert b_status == 200 and b_content == b"file-b-only"

    a_workspace_files = [path.read_bytes() for path in a.data_root.joinpath("workspace").rglob("*") if path.is_file()]
    b_workspace_files = [path.read_bytes() for path in b.data_root.joinpath("workspace").rglob("*") if path.is_file()]
    assert b"file-a-only" in a_workspace_files and b"file-b-only" not in a_workspace_files
    assert b"file-b-only" in b_workspace_files and b"file-a-only" not in b_workspace_files
    return handle_a, handle_b


def plugin_status(spec: InstanceSpec) -> dict[str, Any]:
    status, payload = request_json(
        backend_url(spec, "/admin/plugins/status"),
        token=spec.admin_token,
    )
    assert status == 200, payload
    plugin = next((item for item in payload.get("plugins", []) if item.get("plugin_id") == PLUGIN_ID), None)
    assert isinstance(plugin, dict) and plugin.get("status") == "active", payload
    return payload


def invoke_plugin(spec: InstanceSpec, *, value: str) -> dict[str, Any]:
    status, payload = request_json(
        backend_url(spec, f"/admin/plugins/capabilities/{PLUGIN_CAPABILITY_ID}/invoke"),
        token=spec.admin_token,
        body={
            "value": value,
            "idempotency_key": SHARED_NOTIFICATION_KEY,
            "recipient_id": SHARED_RECIPIENT_ID,
        },
    )
    assert status == 200 and payload.get("ok") is True, payload
    content = payload.get("content")
    assert isinstance(content, dict), payload
    return content


def plugin_state_path(spec: InstanceSpec) -> Path:
    return spec.data_root / "instances" / spec.instance_id / "plugins" / PLUGIN_ID / "state.json"


def assert_plugin_and_notification_isolation(
    a: InstanceSpec,
    b: InstanceSpec,
    onebot_a: CaptureServer,
    onebot_b: CaptureServer,
) -> None:
    plugin_status(a)
    plugin_status(b)
    first_a = invoke_plugin(a, value="plugin-a-only")
    first_b = invoke_plugin(b, value="plugin-b-only")
    assert first_a.get("instance_id") == a.instance_id and first_a.get("generation") == 1
    assert first_b.get("instance_id") == b.instance_id and first_b.get("generation") == 1
    assert first_a.get("notification", {}).get("status") == "delivered"
    assert first_b.get("notification", {}).get("status") == "delivered"

    second_a = invoke_plugin(a, value="plugin-a-only")
    second_b = invoke_plugin(b, value="plugin-b-only")
    assert second_a.get("generation") == 2 and second_b.get("generation") == 2
    assert second_a.get("notification", {}).get("status") == "already_delivered"
    assert second_b.get("notification", {}).get("status") == "already_delivered"

    state_a = json.loads(plugin_state_path(a).read_text(encoding="utf-8"))
    state_b = json.loads(plugin_state_path(b).read_text(encoding="utf-8"))
    assert state_a.get("instance_id") == a.instance_id and state_a.get("value") == "plugin-a-only"
    assert state_b.get("instance_id") == b.instance_id and state_b.get("value") == "plugin-b-only"

    records_a = onebot_a.records()
    records_b = onebot_b.records()
    assert len(records_a) == 1 and len(records_b) == 1
    assert records_a[0].get("authorization") == f"Bearer {a.qq_access_token}"
    assert records_b[0].get("authorization") == f"Bearer {b.qq_access_token}"
    assert records_a[0].get("body", {}).get("message") == f"{a.instance_id}:plugin-a-only"
    assert records_b[0].get("body", {}).get("message") == f"{b.instance_id}:plugin-b-only"


def assert_persisted_boundaries_after_restart(
    a: InstanceSpec,
    b: InstanceSpec,
    *,
    handle_a: str,
    handle_b: str,
    onebot_a: CaptureServer,
    onebot_b: CaptureServer,
) -> None:
    a_messages = message_contents(session_payload(a))
    b_messages = message_contents(session_payload(b))
    assert "memory-a-only" in a_messages and "memory-b-only" not in a_messages
    assert "memory-b-only" in b_messages and "memory-a-only" not in b_messages
    assert care_snapshot(a).get("inventory") == {"dango": 1}
    assert care_snapshot(b).get("inventory") == {}

    a_status, a_content = request_bytes(
        backend_url(a, f"/desktop-pet/workspace/attachments/{handle_a}/content?{identity_query()}")
    )
    b_status, b_content = request_bytes(
        backend_url(b, f"/desktop-pet/workspace/attachments/{handle_b}/content?{identity_query()}")
    )
    assert a_status == 200 and a_content == b"file-a-only"
    assert b_status == 200 and b_content == b"file-b-only"

    status, model_a = request_json(backend_url(a, "/control-center/model-service"))
    assert status == 200 and model_a.get("chatModel") == a.model_name
    restarted_a = invoke_plugin(a, value="plugin-a-after-restart")
    still_running_b = invoke_plugin(b, value="plugin-b-still-running")
    assert restarted_a.get("generation") == 3
    assert restarted_a.get("notification", {}).get("status") == "delivered"
    assert still_running_b.get("generation") == 3
    assert still_running_b.get("notification", {}).get("status") == "already_delivered"
    assert len(onebot_a.records()) == 2
    assert len(onebot_b.records()) == 1


def assert_log_safety(specs: list[InstanceSpec], log_paths: list[Path]) -> None:
    secrets = [
        value
        for spec in specs
        for value in (
            spec.admin_token,
            spec.qq_webhook_secret,
            spec.qq_access_token,
            spec.model_api_key,
        )
    ]
    for spec in specs:
        expected = spec.data_root / "logs" / f"akane_backend.{spec.instance_id}.log"
        assert expected in log_paths and expected.is_file()
    for path in log_paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for secret in secrets:
            assert secret not in text, f"secret leaked into {path.name}"


def main() -> int:
    ports = free_ports(6)
    with tempfile.TemporaryDirectory(prefix="akane-m65-e5-") as temp_dir:
        base = Path(temp_dir)
        plugin_root = base / "probe-distribution"
        write_probe_distribution(plugin_root)

        llm_server = CaptureServer(("127.0.0.1", ports[0]), kind="llm")
        onebot_a = CaptureServer(("127.0.0.1", ports[3]), kind="onebot", marker="instance-a")
        onebot_b = CaptureServer(("127.0.0.1", ports[4]), kind="onebot", marker="instance-b")
        llm_server.start()
        onebot_a.start()
        onebot_b.start()

        specs = [
            InstanceSpec(
                instance_id="instance-a",
                data_root=base / "a",
                backend_port=ports[1],
                admin_token="e5-admin-a",
                qq_profile_ref="e5-qq-a",
                qq_bot_id="10000001",
                qq_webhook_secret="e5-webhook-a",
                qq_access_token="e5-onebot-a",
                onebot_url=f"http://127.0.0.1:{ports[3]}",
                model_api_key="e5-model-key-a",
                model_name="e5-model-a",
                model_base_url=f"http://127.0.0.1:{ports[0]}/instance-a/v1",
            ),
            InstanceSpec(
                instance_id="instance-b",
                data_root=base / "b",
                backend_port=ports[2],
                admin_token="e5-admin-b",
                qq_profile_ref="e5-qq-b",
                qq_bot_id="10000002",
                qq_webhook_secret="e5-webhook-b",
                qq_access_token="e5-onebot-b",
                onebot_url=f"http://127.0.0.1:{ports[4]}",
                model_api_key="e5-model-key-b",
                model_name="e5-model-b",
                model_base_url=f"http://127.0.0.1:{ports[0]}/instance-b/v1",
            ),
        ]
        for spec in specs:
            write_instance_root(spec)

        handles: list[BackendHandle] = []
        log_paths: list[Path] = []
        try:
            assert_client_storage_isolation()
            for spec in specs:
                handle = start_backend(spec, plugin_root=plugin_root)
                handles.append(handle)
                log_paths.append(handle.log_path)
            for spec in specs:
                wait_health(spec)

            a, b = specs
            assert_qq_boundaries(a, b)
            assert_model_configuration_isolation(a, b)
            assert_memory_isolation(a, b, llm_server)
            assert_care_isolation(a, b)
            handle_a, handle_b = assert_file_isolation(a, b, base)
            assert_plugin_and_notification_isolation(a, b, onebot_a, onebot_b)

            duplicate_spec = InstanceSpec(
                instance_id=a.instance_id,
                data_root=a.data_root,
                backend_port=ports[5],
                admin_token=a.admin_token,
                qq_profile_ref=a.qq_profile_ref,
                qq_bot_id=a.qq_bot_id,
                qq_webhook_secret=a.qq_webhook_secret,
                qq_access_token=a.qq_access_token,
                onebot_url=a.onebot_url,
                model_api_key=a.model_api_key,
                model_name=a.model_name,
                model_base_url=a.model_base_url,
            )
            duplicate = start_backend(duplicate_spec, plugin_root=plugin_root, log_suffix="duplicate")
            log_paths.append(duplicate.log_path)
            try:
                duplicate.process.wait(timeout=30)
                assert duplicate.process.returncode not in (None, 0), "duplicate root process unexpectedly started"
            finally:
                stop_backend(duplicate)
            wait_health(b)

            first_a = handles[0]
            stop_backend(first_a)
            handles.remove(first_a)
            wait_health(b)

            restarted_a = start_backend(a, plugin_root=plugin_root)
            handles.append(restarted_a)
            if restarted_a.log_path not in log_paths:
                log_paths.append(restarted_a.log_path)
            wait_health(a)
            wait_health(b)
            assert_persisted_boundaries_after_restart(
                a,
                b,
                handle_a=handle_a,
                handle_b=handle_b,
                onebot_a=onebot_a,
                onebot_b=onebot_b,
            )

            stop_backend(restarted_a)
            handles.remove(restarted_a)
            wait_health(b)
            remaining_b = next(handle for handle in handles if handle.process.poll() is None)
            stop_backend(remaining_b)
            handles.remove(remaining_b)

            assert_log_safety(specs, log_paths)
            print("M65-E5 two-instance acceptance: ok")
            return 0
        finally:
            for handle in list(handles):
                stop_backend(handle)
            llm_server.stop()
            onebot_a.stop()
            onebot_b.stop()


if __name__ == "__main__":
    raise SystemExit(main())
