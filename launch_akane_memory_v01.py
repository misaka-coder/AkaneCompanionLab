from __future__ import annotations

import os
import socket
import sys
import importlib.util

import uvicorn
import config


def _collect_ipv4_candidates() -> list[str]:
    ips: set[str] = set()

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith(("127.", "169.254.", "198.18.")):
                ips.add(ip)
    except OSError:
        pass

    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM)
        for info in infos:
            ip = str(info[4][0] or "").strip()
            if ip and not ip.startswith(("127.", "169.254.", "198.18.")):
                ips.add(ip)
    except OSError:
        pass

    return sorted(ips)


if __name__ == "__main__":
    host = os.getenv("COMPANION_HOST", getattr(config, "HOST", "0.0.0.0"))
    port = int(os.getenv("COMPANION_PORT", str(getattr(config, "PORT", 9999))))
    main_url = f"http://127.0.0.1:{port}/"
    resource_preview_url = f"http://127.0.0.1:{port}/resource-preview"

    print(f"[INFO] AkaneCompanionLab 服务启动中: host={host} port={port}")
    print(f"[INFO] Python: {sys.executable}")
    print(f"[INFO] yt-dlp module: {'ok' if importlib.util.find_spec('yt_dlp') is not None else 'missing'}")
    print(f"[INFO] 本机主界面: {main_url}")
    print(f"[INFO] 资源调试页: {resource_preview_url}")
    if host == "0.0.0.0":
        for ip in _collect_ipv4_candidates():
            print(f"[INFO] 手机主界面: http://{ip}:{port}/")
            print(f"[INFO] 手机资源调试页: http://{ip}:{port}/resource-preview")
        print("[INFO] 请确保手机和电脑在同一 Wi-Fi 下。")

    uvicorn.run("companion_v01.app:app", host=host, port=port, reload=False)
