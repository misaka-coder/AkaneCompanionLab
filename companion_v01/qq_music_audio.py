"""Validate model-selected public audio URLs for QQ voice delivery."""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable


PUBLIC_AUDIO_SAMPLE_BYTES = 512
PUBLIC_AUDIO_URL_MAX_CHARS = 2048
PROVIDER_AUDIO_HOST_SUFFIXES = (
    "music.163.com",
    "music.126.net",
    "qqmusic.qq.com",
    "tc.qq.com",
)


class _UnsafeAudioRedirectError(Exception):
    pass


def _hostname_matches_suffix(hostname: str, suffix: str) -> bool:
    clean_host = str(hostname or "").strip().rstrip(".").lower()
    clean_suffix = str(suffix or "").strip().strip(".").lower()
    return bool(clean_host and clean_suffix) and (
        clean_host == clean_suffix or clean_host.endswith("." + clean_suffix)
    )


def _audio_sample_looks_binary(sample: bytes) -> bool:
    value = bytes(sample or b"")
    if not value:
        return False
    stripped = value.lstrip().lower()
    if stripped.startswith((b"<!doctype", b"<html", b"<?xml")):
        return False
    return value.startswith((b"ID3", b"OggS", b"fLaC", b"RIFF")) or (
        len(value) >= 2 and value[0] == 0xFF and (value[1] & 0xE0) == 0xE0
    ) or (len(value) >= 12 and value[4:8] == b"ftyp")


def _public_hostname_status(
    hostname: str,
    *,
    resolver: Callable[..., Any] = socket.getaddrinfo,
) -> str:
    clean_host = str(hostname or "").strip().rstrip(".").lower()
    if not clean_host:
        return "missing_hostname"
    try:
        literal = ipaddress.ip_address(clean_host)
        addresses = [literal]
    except ValueError:
        try:
            rows = resolver(clean_host, None, type=socket.SOCK_STREAM)
        except (OSError, socket.gaierror):
            return "hostname_unreachable"
        addresses = []
        for row in rows or []:
            try:
                addresses.append(ipaddress.ip_address(str(row[4][0] or "")))
            except (IndexError, TypeError, ValueError):
                continue
    if not addresses:
        return "hostname_unreachable"
    if any(
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        for address in addresses
    ):
        return "private_or_reserved_host"
    return "public"


def _audio_hostname_status(hostname: str, *, resolver: Callable[..., Any]) -> str:
    """Honor known provider hosts behind local TUN fake-IP DNS.

    Dove/Clash-style enhanced DNS commonly maps public provider hosts into
    198.18.0.0/15.  We permit that reserved answer only for the small provider
    host allowlist; arbitrary model-provided hosts remain subject to strict
    public-IP validation.
    """
    status = _public_hostname_status(hostname, resolver=resolver)
    clean_host = str(hostname or "").strip().rstrip(".").lower()
    if status == "private_or_reserved_host" and any(
        _hostname_matches_suffix(clean_host, suffix)
        for suffix in PROVIDER_AUDIO_HOST_SUFFIXES
    ):
        return "public"
    return status


class _PublicAudioRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, *, resolver: Callable[..., Any]) -> None:
        super().__init__()
        self._resolver = resolver

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        parsed = urllib.parse.urlparse(str(newurl or "").strip())
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or _audio_hostname_status(str(parsed.hostname), resolver=self._resolver) != "public"
        ):
            raise _UnsafeAudioRedirectError("unsafe_audio_redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def resolve_public_audio_url(
    url: str,
    *,
    timeout_seconds: float = 8.0,
    opener: Callable[..., Any] | None = None,
    resolver: Callable[..., Any] = socket.getaddrinfo,
) -> dict[str, Any]:
    """Verify a model-selected public audio URL without downloading the track.

    This is a transport preflight, not a media downloader.  It rejects local or
    credential-bearing URLs, checks both the original and redirected host, and
    reads only a small ranged sample before handing the URL to OneBot.
    """

    clean_url = str(url or "").strip()
    if not clean_url or len(clean_url) > PUBLIC_AUDIO_URL_MAX_CHARS:
        return {"ok": False, "status": "invalid_audio_url"}
    parsed = urllib.parse.urlparse(clean_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return {"ok": False, "status": "invalid_audio_url"}
    host_status = _audio_hostname_status(str(parsed.hostname), resolver=resolver)
    if host_status != "public":
        return {"ok": False, "status": host_status}
    request = urllib.request.Request(
        clean_url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; AkaneAudioDelivery/1.0)",
            "Range": f"bytes=0-{PUBLIC_AUDIO_SAMPLE_BYTES - 1}",
        },
    )
    open_request = opener or urllib.request.build_opener(
        _PublicAudioRedirectHandler(resolver=resolver)
    ).open
    try:
        with open_request(request, timeout=max(1.0, min(float(timeout_seconds), 15.0))) as response:
            sample = response.read(PUBLIC_AUDIO_SAMPLE_BYTES)
            media_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            final_url = str(response.geturl() or clean_url).strip()
    except _UnsafeAudioRedirectError:
        return {"ok": False, "status": "unsafe_audio_redirect"}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"ok": False, "status": "audio_url_unreachable"}
    final = urllib.parse.urlparse(final_url)
    if final.scheme not in {"http", "https"} or not final.hostname or final.username or final.password:
        return {"ok": False, "status": "unsafe_audio_redirect"}
    final_host_status = _audio_hostname_status(str(final.hostname), resolver=resolver)
    if final_host_status != "public":
        return {"ok": False, "status": "unsafe_audio_redirect"}
    media_ok = media_type.startswith("audio/") or (
        media_type in {"application/octet-stream", "binary/octet-stream"}
        and _audio_sample_looks_binary(sample)
    )
    if not media_ok or not sample or sample.lstrip().lower().startswith((b"<!doctype", b"<html")):
        return {"ok": False, "status": "not_public_audio"}
    return {"ok": True, "status": "ready", "url": final_url, "media_type": media_type or "audio/unknown"}
