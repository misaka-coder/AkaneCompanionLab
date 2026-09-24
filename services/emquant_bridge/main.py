from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from .local_api import create_emquant_bridge_app
from .runtime import EmQuantBridgeRuntime


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def build_runtime_from_env() -> EmQuantBridgeRuntime:
    enabled = str(os.getenv("EMQUANT_ENABLED", "false") or "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    api_root = str(os.getenv("EMQUANT_API_ROOT", "") or "").strip()
    raw_state_path = str(os.getenv("EMQUANT_SUBSCRIPTION_STATE_PATH", "") or "").strip()
    state_path = Path(raw_state_path).expanduser() if raw_state_path else None
    try:
        http_timeout = int(os.getenv("EMQUANT_HTTP_TIMEOUT_SECONDS", "15") or 15)
    except ValueError:
        http_timeout = 15
    try:
        callback_queue_maxsize = int(os.getenv("EMQUANT_CALLBACK_QUEUE_MAX", "5000") or 5000)
    except ValueError:
        callback_queue_maxsize = 5000
    return EmQuantBridgeRuntime(
        enabled=enabled,
        api_root=api_root,
        subscription_state_path=state_path,
        http_timeout_seconds=http_timeout,
        callback_queue_maxsize=max(1, callback_queue_maxsize),
    )


def main() -> None:
    host = str(os.getenv("EMQUANT_BRIDGE_HOST", "127.0.0.1") or "127.0.0.1").strip().lower()
    if host not in LOOPBACK_HOSTS:
        host = "127.0.0.1"
    try:
        port = int(os.getenv("EMQUANT_BRIDGE_PORT", "9910") or 9910)
    except ValueError:
        port = 9910
    port = max(1, min(65535, port))
    runtime = build_runtime_from_env()
    if runtime.enabled:
        runtime.start()
    access_token = str(os.getenv("EMQUANT_BRIDGE_TOKEN", "") or "").strip()
    app = create_emquant_bridge_app(runtime, access_token=access_token)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
