"""Shared scalar and URL validation without changing backend network policy."""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Callable
from urllib.parse import urlparse


def normalize_http_url(
    value: Any,
    *,
    is_private_host: Callable[[str], bool],
    allow_private: bool = False,
    reject_secret_query: bool = False,
) -> str:
    url = str(value or "").strip()
    if not url or len(url) > 1600 or any(ord(ch) < 32 for ch in url) or re.search(r"\s", url):
        return ""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        # Accessing port validates malformed/out-of-range values too.
        parsed.port
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return ""
    if not hostname or (is_private_host(hostname) and not allow_private):
        return ""
    if reject_secret_query and re.search(r"(?i)(api[_-]?key|password|secret|token)=", parsed.query or ""):
        return ""
    return url


class BrowserCommonMixin:
    def _is_private_or_local_host(self, hostname: str) -> bool:
        host = str(hostname or "").strip().lower().strip("[]")
        if not host or host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
            return True
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved)

    def _coerce_int(self, value: Any, *, minimum: int, maximum: int, default: int) -> int:
        try:
            number = int(value)
        except Exception:
            number = default
        return max(minimum, min(maximum, number))

    def _clip(self, value: str, limit: int) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 20)].rstrip() + "\n...[truncated]"
