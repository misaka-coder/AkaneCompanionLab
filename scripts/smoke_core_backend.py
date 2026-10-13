"""Exercise fresh headless startup, HTTP chat, memory continuity and local execution.

Model transport uses an isolated loopback test server. No real credentials,
remote model calls, desktop processes or audio services are used.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]


def worker(data_root: Path) -> dict:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    requests_seen: list[dict] = []
    reply = "后端测试回复。"

    class ModelHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests_seen.append(payload)
            content = json.dumps({"emotion": "normal", "utterances": [
                {"text": reply, "say": "バックエンドの確認ができました。", "style": "calm"},
            ], "memory_metadata": {}}, ensure_ascii=False)
            body = json.dumps({"id": "smoke-completion", "object": "chat.completion",
                               "created": 1, "model": "smoke-model", "choices": [
                                   {"index": 0, "message": {"role": "assistant", "content": content},
                                    "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}},
                              ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env_file = data_root / "empty.env"
    env_file.write_text("", encoding="utf-8")
    # Remove inherited product settings before constructing config/app. A smoke
    # must never discover the maintainer's live profile or model endpoint.
    for name in list(os.environ):
        if name.startswith(("AKANE_", "CHAT_", "TEXT_", "AUX_", "VISION_", "MEMCORE_", "EMBEDDING_", "QQ_")):
            os.environ.pop(name)
    model_url = f"http://127.0.0.1:{server.server_port}/v1"
    os.environ.update({"AKANE_ENV_FILE": str(env_file), "AKANE_DATA_ROOT": str(data_root / "data"),
                       "EMBEDDING_PROVIDER": "auto", "MEMCORE_RETRIEVAL_MODE": "navigation",
                       "QQ_BRIDGE_ENABLED": "false", "PYTHONNOUSERSITE": "1"})
    for role in ("CHAT", "TEXT", "AUX", "VISION"):
        os.environ.update({f"{role}_API_KEY": "loopback-smoke-placeholder",
                           f"{role}_BASE_URL": model_url, f"{role}_MODEL_NAME": "smoke-model",
                           f"{role}_API_PROTOCOL": "openai"})
    try:
        from companion_v01.app import app
        from fastapi.testclient import TestClient
        from companion_v01.execution_run import ExecutionRunOwner
        from companion_v01.local_execution_runtime.executor import TrustedLocalExecutor

        with TestClient(app, client=("127.0.0.1", 12345)) as client:
            health = client.get("/health")
            assert health.status_code == 200, health.text
            assert "chromadb" not in sys.modules, "core startup loaded optional Chroma"
            assert "torch" not in sys.modules, "core startup loaded local ML"
            frames = []
            for message in ("记住测试标记 alpha。", "继续核对刚才的测试标记。"):
                response = client.post("/think_once", json={"user_id": "smoke-user", "message": message})
                assert response.status_code == 200, response.text
                frame = response.json()
                assert reply in json.dumps(frame, ensure_ascii=False), frame
                assert not frame.get("_transient_final_failure"), frame
                frames.append(frame)
            assert len(requests_seen) >= 2, "chat did not use the actual model transport"
            last_prompt = json.dumps(requests_seen[-1].get("messages", []), ensure_ascii=False)
            assert "测试标记 alpha" in last_prompt and reply in last_prompt, "previous turn did not enter memory context"

            workspace = data_root / "workspace"
            workspace.mkdir()
            executor = TrustedLocalExecutor(workspace_root=workspace, run_log_dir=data_root / "exec-logs")
            owner = ExecutionRunOwner(profile_user_id="smoke-user", session_id="smoke-session", provider_id="local")
            command = "Write-Output 'agent-smoke-ok'" if os.name == "nt" else "printf 'agent-smoke-ok\\n'"
            result = executor.run(owner=owner, command=command, initial_wait_seconds=5, timeout_seconds=10)
            assert result.status == "completed" and result.exit_code == 0, result
            assert "agent-smoke-ok" in result.stdout, result
            environment = executor.prompt_environment()
            executor.request_shutdown()
            summary = {"ok": True, "health_status": health.status_code, "chat_turns": len(frames),
                       "memory_continuity": True, "model_transport_requests": len(requests_seen),
                       "agent_shell": environment["command_shell"], "agent_platform": environment["platform"],
                       "agent_command_status": result.status, "optional_chroma_loaded": False,
                       "optional_torch_loaded": False, "memory_backend": "memcore"}
        return summary
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker), ensure_ascii=False))
        print("AKANE_CORE_BACKEND_SMOKE_OK")
        return 0
    # Process exit releases the host instance lease and SQLite handles before
    # the parent removes its temporary data, including on Windows.
    with tempfile.TemporaryDirectory(prefix="akane-core-backend-smoke-") as temporary:
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment["PYTHONNOUSERSITE"] = "1"
        subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", temporary],
                       cwd=ROOT, env=environment, check=True, timeout=120)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
