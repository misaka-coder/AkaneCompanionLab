from __future__ import annotations

import hashlib
import ipaddress
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit


HostResolver = Callable[[str, int], Iterable[str]]
REMOTE_MEDIA_YTDLP_ALLOWED_HOSTS = (
    "b23.tv",
    "bilibili.com",
    "youtube.com",
    "youtu.be",
    "douyin.com",
    "iesdouyin.com",
    "ixigua.com",
    "kuaishou.com",
)


class PublicUrlPolicyError(ValueError):
    """Stable public URL rejection without echoing the rejected locator."""

    def __init__(self, code: str) -> None:
        self.code = str(code or "remote_url_rejected")
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class PublicUrlTarget:
    url: str = field(repr=False)
    origin: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


def validate_public_http_url(
    value: str,
    *,
    resolver: HostResolver | None = None,
    max_chars: int = 2048,
) -> PublicUrlTarget:
    raw = str(value or "").strip()
    if not raw or len(raw) > max(1, int(max_chars)) or any(ord(char) < 32 or char.isspace() for char in raw):
        raise PublicUrlPolicyError("remote_url_invalid")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise PublicUrlPolicyError("remote_url_invalid") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise PublicUrlPolicyError("remote_url_scheme_forbidden")
    if parsed.username is not None or parsed.password is not None:
        raise PublicUrlPolicyError("remote_url_credentials_forbidden")
    hostname = str(parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname or "%" in hostname or hostname == "localhost" or hostname.endswith((".local", ".internal")):
        raise PublicUrlPolicyError("remote_url_host_forbidden")
    effective_port = int(port or (443 if scheme == "https" else 80))
    if effective_port <= 0 or effective_port > 65535:
        raise PublicUrlPolicyError("remote_url_invalid")

    addresses = _resolved_addresses(
        hostname,
        effective_port,
        resolver=resolver or resolve_host_addresses,
    )
    if not addresses:
        raise PublicUrlPolicyError("remote_url_dns_failed")
    for address in addresses:
        try:
            parsed_address = ipaddress.ip_address(address)
        except ValueError as exc:
            raise PublicUrlPolicyError("remote_url_dns_failed") from exc
        if not _is_public_address(parsed_address):
            raise PublicUrlPolicyError("remote_url_private_address")

    normalized_url = urlunsplit((scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
    origin = _origin(scheme=scheme, hostname=hostname, port=effective_port)
    return PublicUrlTarget(
        url=normalized_url,
        origin=origin,
        hostname=hostname,
        port=effective_port,
        addresses=addresses,
    )


def resolve_host_addresses(hostname: str, port: int) -> tuple[str, ...]:
    try:
        records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise PublicUrlPolicyError("remote_url_dns_failed") from exc
    addresses = []
    for record in records:
        sockaddr = record[4] if len(record) > 4 else ()
        address = str(sockaddr[0] if sockaddr else "").strip()
        if address and address not in addresses:
            addresses.append(address)
    return tuple(addresses)


def public_url_fingerprint(value: str) -> str:
    raw = str(value or "").strip()
    return f"url_sha256:{hashlib.sha256(raw.encode('utf-8', errors='replace')).hexdigest()}" if raw else ""


def public_url_display_origin(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return ""
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or parsed.username is not None or parsed.password is not None:
        return ""
    hostname = str(parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname or "%" in hostname or hostname == "localhost" or hostname.endswith((".local", ".internal")):
        return ""
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None and not _is_public_address(literal):
        return ""
    effective_port = int(port or (443 if scheme == "https" else 80))
    if effective_port <= 0 or effective_port > 65535:
        return ""
    return _origin(scheme=scheme, hostname=hostname, port=effective_port)


def is_ytdlp_provider_url(value: str) -> bool:
    try:
        hostname = str(urlsplit(str(value or "")).hostname or "").strip().lower().rstrip(".")
    except ValueError:
        return False
    return any(hostname == allowed or hostname.endswith(f".{allowed}") for allowed in REMOTE_MEDIA_YTDLP_ALLOWED_HOSTS)


def validate_response_peer(response: Any, target: PublicUrlTarget) -> None:
    raw = getattr(response, "raw", None)
    connection = getattr(raw, "connection", None)
    if connection is None:
        connection = getattr(raw, "_connection", None)
    sock = getattr(connection, "sock", None)
    try:
        peer_value = sock.getpeername()[0] if sock is not None else ""
        peer = _canonical_address(ipaddress.ip_address(str(peer_value or "").strip()))
        allowed = {_canonical_address(ipaddress.ip_address(address)) for address in target.addresses}
    except (OSError, TypeError, ValueError) as exc:
        raise PublicUrlPolicyError("remote_url_peer_unverifiable") from exc
    if not _is_public_address(peer) or peer not in allowed:
        raise PublicUrlPolicyError("remote_url_peer_mismatch")


def _resolved_addresses(hostname: str, port: int, *, resolver: HostResolver) -> tuple[str, ...]:
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        return (str(literal),)
    try:
        return tuple(
            dict.fromkeys(str(item or "").strip() for item in resolver(hostname, port) if str(item or "").strip())
        )
    except PublicUrlPolicyError:
        raise
    except Exception as exc:
        raise PublicUrlPolicyError("remote_url_dns_failed") from exc


def _origin(*, scheme: str, hostname: str, port: int) -> str:
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 443 if scheme == "https" else 80
    suffix = "" if port == default_port else f":{port}"
    return f"{scheme}://{rendered_host}{suffix}"


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    address = _canonical_address(address)
    return bool(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
    )


def _canonical_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    mapped = getattr(address, "ipv4_mapped", None)
    return mapped if mapped is not None else address
