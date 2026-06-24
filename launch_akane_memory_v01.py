from __future__ import annotations

import importlib.util
import logging
import os
import socket
import sys
from pathlib import Path

# Configure logging early — before any third-party imports that may log.
# This ensures all log output (including from config / uvicorn import)
# uses a consistent format with timestamps.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("akane.launch")

import uvicorn
import config
from akane_paths import migrate_legacy_data


def _preflight() -> tuple[str, int]:
    """Friendly pre-flight checks before starting the server.

    Returns validated (host, port) on success.
    Calls ``sys.exit(1)`` on fatal configuration errors.
    """
    # 1. .env file — warn but don't block; env vars or defaults may suffice
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        logger.warning(".env file not found at %s; using environment variables or defaults.", env_path)

    # 2. API key check — never print the key itself, only whether it is configured
    text_key = str(getattr(config, "TEXT_API_KEY", "") or "").strip()
    chat_key = str(getattr(config, "CHAT_API_KEY", "") or "").strip()
    final_key = chat_key or text_key  # chat falls back to text when empty
    if not final_key:
        logger.warning(
            "Neither CHAT_API_KEY nor TEXT_API_KEY is configured; LLM calls will fail. "
            "Set at least one in .env or environment variables."
        )
    else:
        logger.info(
            "API key: CHAT=%s, TEXT=%s",
            "set" if chat_key else "not set",
            "set" if text_key else "not set",
        )

    # 3. yt-dlp — optional but common dependency for media features
    if importlib.util.find_spec("yt_dlp") is None:
        logger.warning("yt-dlp is not installed; media download / video features will not work.")

    # 4. HOST / PORT — must be valid; exit with a friendly message on failure
    host = str(os.getenv("COMPANION_HOST", str(getattr(config, "HOST", "0.0.0.0")))).strip()
    port_str = str(os.getenv("COMPANION_PORT", str(getattr(config, "PORT", 9999)))).strip()
    try:
        port = int(port_str)
        if not (1 <= port <= 65535):
            raise ValueError(f"port {port} is out of range (1-65535)")
    except (ValueError, TypeError) as exc:
        logger.error(
            "Invalid PORT=%r (COMPANION_PORT env or config.PORT): %s. "
            "Please set a valid port number and try again.",
            port_str,
            exc,
        )
        sys.exit(1)

    logger.info("Preflight OK: host=%s port=%s", host, port)
    return host, port


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
    migration = migrate_legacy_data(Path(__file__).resolve().parent, paths=config.AKANE_DATA_PATHS)
    if migration.failed:
        logger.warning(
            "User data root is ready, but %s legacy files could not be copied.",
            migration.failed,
        )
    elif migration.copied:
        logger.info(
            "Migrated %s legacy files without overwriting existing data.",
            migration.copied,
        )

    host, port = _preflight()

    main_url = f"http://127.0.0.1:{port}/"
    resource_preview_url = f"http://127.0.0.1:{port}/resource-preview"

    logger.info("AkaneCompanionLab starting: host=%s port=%s", host, port)
    logger.info("Python: %s", sys.executable)
    logger.info("yt-dlp: %s", "available" if importlib.util.find_spec("yt_dlp") is not None else "missing")
    logger.info("Local: %s", main_url)
    logger.info("Resource preview: %s", resource_preview_url)
    if host == "0.0.0.0":
        for ip in _collect_ipv4_candidates():
            logger.info("Mobile: http://%s:%s/", ip, port)
        logger.info("Ensure phone and computer are on the same Wi-Fi.")

    uvicorn.run("companion_v01.app:app", host=host, port=port, reload=False)
