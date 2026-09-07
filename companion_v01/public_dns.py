"""Bounded encrypted DNS fallback for synthetic (Fake-IP) system answers.

Only hostnames are sent, never URLs, cookies or headers. This is not a general
override for private/split-horizon DNS. The URL policy decides when to use it.
"""

from __future__ import annotations

import ipaddress
import json
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import requests


# Numeric, TLS-verified bootstrap avoids asking the intercepted system resolver
# how to reach its replacement. No redirect or environment proxy is accepted.
DOH_ENDPOINT = "https://1.1.1.1/dns-query"
_MAX_RESPONSE_BYTES = 64 * 1024
_CACHE_LIMIT = 128
_CACHE: OrderedDict[str, tuple[float, tuple[str, ...]]] = OrderedDict()
_LOCK = threading.Lock()


class PublicDnsError(ValueError):
    def __init__(self) -> None:
        super().__init__("remote_url_public_dns_unavailable")


def _canonical_name(value: str) -> str:
    return value.rstrip(".").encode("idna").decode("ascii").lower()


def _query(hostname: str, record_type: int) -> tuple[tuple[str, ...], int]:
    try:
        deadline = time.monotonic() + 8.0
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(
                DOH_ENDPOINT,
                params={"name": hostname, "type": record_type, "cd": "false"},
                headers={"Accept": "application/dns-json"},
                timeout=(3.0, 5.0),
                stream=True,
                allow_redirects=False,
            )
            try:
                if response.status_code != 200:
                    raise PublicDnsError()
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_content(chunk_size=8192):
                    size += len(chunk)
                    if size > _MAX_RESPONSE_BYTES or time.monotonic() > deadline:
                        raise PublicDnsError()
                    chunks.append(chunk)
                data = json.loads(b"".join(chunks))
            finally:
                response.close()
        if not isinstance(data, dict) or data.get("Status") != 0 or data.get("TC") is True:
            raise PublicDnsError()
        questions = data.get("Question")
        if (
            not isinstance(questions, list)
            or len(questions) != 1
            or not isinstance(questions[0], dict)
            or _canonical_name(questions[0].get("name", "")) != hostname
            or questions[0].get("type") != record_type
        ):
            raise PublicDnsError()
        answers = data.get("Answer", [])
        if not isinstance(answers, list) or len(answers) > 128 or any(not isinstance(row, dict) for row in answers):
            raise PublicDnsError()
        names = {hostname}
        ttl = 60
        # Only accept addresses for the requested name or its returned CNAME chain.
        for _ in range(16):
            previous = len(names)
            for answer in answers:
                if answer.get("type") == 5 and _canonical_name(answer.get("name", "")) in names:
                    names.add(_canonical_name(answer.get("data", "")))
                    ttl = min(ttl, max(0, int(answer.get("TTL", 0))))
            if len(names) == previous:
                break
        addresses: list[str] = []
        for answer in answers:
            if answer.get("type") != record_type or _canonical_name(answer.get("name", "")) not in names:
                continue
            address = ipaddress.ip_address(answer.get("data", ""))
            if address.version != (4 if record_type == 1 else 6):
                raise PublicDnsError()
            addresses.append(str(address))
            ttl = min(ttl, max(0, int(answer.get("TTL", 0))))
        return tuple(dict.fromkeys(addresses)), ttl
    except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
        raise PublicDnsError() from exc


def resolve_public_dns(hostname: str) -> tuple[str, ...]:
    hostname = _canonical_name(hostname)
    if not hostname or "." not in hostname or len(hostname) > 253:
        raise PublicDnsError()
    with _LOCK:
        cached = _CACHE.get(hostname)
        if cached and cached[0] > time.monotonic():
            _CACHE.move_to_end(hostname)
            return cached[1]
    # Both families are checked. Never hide a private AAAA behind a public A.
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda kind: _query(hostname, kind), (1, 28)))
    addresses = tuple(dict.fromkeys(address for rows, _ttl in results for address in rows))
    if not addresses:
        raise PublicDnsError()
    ttl = min(ttl for _rows, ttl in results)
    if ttl > 0:
        with _LOCK:
            _CACHE[hostname] = (time.monotonic() + ttl, addresses)
            _CACHE.move_to_end(hostname)
            while len(_CACHE) > _CACHE_LIMIT:
                _CACHE.popitem(last=False)
    return addresses
