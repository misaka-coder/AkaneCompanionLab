"""Resolve provider-owned public audio URLs for QQ music delivery fallbacks.

This module never reads cookies or account credentials.  It only asks QQ
Music's public web endpoint for a short-lived URL available to anonymous web
clients.  Empty results (VIP, copyright, region, or provider policy) are a
normal structured unavailable outcome, not an invitation to bypass access.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable


QQ_MUSIC_SONGMID_RE = re.compile(r"^(?=.*[A-Za-z])[0-9A-Za-z_-]{1,64}$")
QQ_MUSIC_VKEY_ENDPOINT = "https://u.y.qq.com/cgi-bin/musicu.fcg"
MAX_RESPONSE_BYTES = 1024 * 1024


def resolve_qq_music_public_audio_url(
    songmid: str,
    *,
    timeout_seconds: float = 8.0,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    clean_mid = str(songmid or "").strip()
    if not QQ_MUSIC_SONGMID_RE.fullmatch(clean_mid):
        return {"ok": False, "status": "invalid_songmid"}

    payload = {
        "req_1": {
            "module": "vkey.GetVkeyServer",
            "method": "CgiGetVkey",
            "param": {
                "guid": "10000",
                "songmid": [clean_mid],
                "songtype": [0],
                "uin": "0",
                "loginflag": 1,
                "platform": "20",
            },
        },
        "comm": {"uin": 0, "format": "json", "ct": 24, "cv": 0},
    }
    request = urllib.request.Request(
        QQ_MUSIC_VKEY_ENDPOINT,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; AkaneMusicDelivery/1.0)",
            "Referer": "https://y.qq.com/",
        },
    )
    try:
        with opener(request, timeout=max(1.0, min(float(timeout_seconds), 15.0))) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"ok": False, "status": "provider_unreachable"}
    if len(raw) > MAX_RESPONSE_BYTES:
        return {"ok": False, "status": "response_too_large"}
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False, "status": "invalid_provider_response"}

    response_block = document.get("req_1") if isinstance(document, dict) else None
    data = response_block.get("data") if isinstance(response_block, dict) else None
    entries = data.get("midurlinfo") if isinstance(data, dict) else None
    first = entries[0] if isinstance(entries, list) and entries and isinstance(entries[0], dict) else {}
    purl = str(first.get("purl") or "").strip()
    sip_values = data.get("sip") if isinstance(data, dict) else None
    sip = str(sip_values[0] or "").strip() if isinstance(sip_values, list) and sip_values else ""
    if not purl or not sip:
        return {"ok": False, "status": "public_audio_unavailable"}

    candidate = urllib.parse.urljoin(sip, purl)
    parsed = urllib.parse.urlparse(candidate)
    hostname = str(parsed.hostname or "").lower()
    trusted_host = hostname.endswith(".qqmusic.qq.com") or hostname.endswith(".tc.qq.com")
    if parsed.scheme not in {"http", "https"} or not trusted_host:
        return {"ok": False, "status": "untrusted_provider_url"}
    if parsed.scheme == "http":
        parsed = parsed._replace(scheme="https")
        candidate = urllib.parse.urlunparse(parsed)
    return {"ok": True, "status": "ready", "url": candidate}
