from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def write_manifest(root: Path, instance_id: str) -> None:
    path = root / "instances" / instance_id / "instance.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            (
                "schema_version = 1",
                f'instance_id = "{instance_id}"',
                'character_pack_id = "akane_v1"',
                "plugins = []",
                "",
                "[features]",
                "care = false",
                "",
                "[channels.qq]",
                "enabled = false",
                'profile_ref = ""',
                "",
            )
        ),
        encoding="utf-8",
    )


def instance_env(root: Path, instance_id: str, token: str, port: int) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "AKANE_DATA_ROOT": str(root),
            "AKANE_INSTANCE_ID": instance_id,
            "AKANE_ADMIN_TOKEN": token,
            "COMPANION_PORT": str(port),
            "QQ_BRIDGE_ENABLED": "false",
            "EMBEDDING_PROVIDER": "hashed",
            "VISION_ENABLED": "false",
            "MEMORY_BACKEND": "legacy",
            "ENABLE_SEMANTIC_MEMORY": "false",
        }
    )
    return env


def request_json(url: str, *, token: str = "", body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def wait_health(port: int, expected_instance_id: str, timeout: float = 120.0) -> dict:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            status, payload = request_json(f"http://127.0.0.1:{port}/health")
            if status == 200 and payload == {
                "status": "ok",
                "instance_id": expected_instance_id,
                "root_binding": "valid",
            }:
                return payload
            last_error = f"unexpected health: {status} {payload}"
        except Exception as error:  # noqa: BLE001 - smoke reports bounded detail locally
            last_error = type(error).__name__
        time.sleep(0.4)
    raise RuntimeError(f"backend {expected_instance_id} did not become ready: {last_error}")


def run_launcher(instance_id: str, data_root: Path, port: int) -> subprocess.CompletedProcess[str]:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        raise RuntimeError("PowerShell is unavailable")
    return subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "start_akane_next.ps1"),
            "-InstanceId",
            instance_id,
            "-DataRoot",
            str(data_root),
            "-BackendPort",
            str(port),
            "-ReuseBackend",
            "-SkipDesktop",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )


def main() -> int:
    ports = [free_port(), free_port()]
    while ports[1] == ports[0]:
        ports[1] = free_port()

    with tempfile.TemporaryDirectory(prefix="akane-m65-e4-") as temp_dir:
        base = Path(temp_dir)
        specs = [
            ("instance-a", base / "a", "admin-a", ports[0]),
            ("instance-b", base / "b", "admin-b", ports[1]),
        ]
        processes: list[subprocess.Popen[bytes]] = []
        try:
            for instance_id, data_root, token, port in specs:
                write_manifest(data_root, instance_id)
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "companion_v01.app:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--log-level",
                        "warning",
                    ],
                    cwd=ROOT,
                    env=instance_env(data_root, instance_id, token, port),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                processes.append(process)

            for instance_id, _data_root, _token, port in specs:
                wait_health(port, instance_id)

            for instance_id, _data_root, token, port in specs:
                denied_status, denied = request_json(f"http://127.0.0.1:{port}/control-center/actions/noop", body={})
                allowed_status, allowed = request_json(
                    f"http://127.0.0.1:{port}/control-center/actions/noop", token=token, body={}
                )
                assert denied_status == 401 and denied.get("reason") == "admin_auth_required"
                assert allowed_status == 200 and allowed.get("status") == "not-implemented"
                assert wait_health(port, instance_id)["instance_id"] == instance_id

            matching = run_launcher("instance-a", specs[0][1], specs[0][3])
            assert matching.returncode == 0, matching.stdout + matching.stderr

            mismatched = run_launcher("instance-a", specs[0][1], specs[1][3])
            assert mismatched.returncode != 0, "launcher unexpectedly reused another instance"
            assert wait_health(specs[1][3], "instance-b")["instance_id"] == "instance-b"

            print("M65-E4 dual-instance smoke: ok")
            return 0
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
