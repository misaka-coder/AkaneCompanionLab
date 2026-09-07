"""Request-local IP pinning for the shared public attachment/media fetch path.

Keep the original URL, HTTP Host, SNI and certificate identity. Only the socket
destination changes to an already validated public IP. Never patch global DNS.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

from .public_url_policy import (
    PublicUrlPolicyError,
    PublicUrlTarget,
    ValidatedPublicPeer,
    validate_public_http_url,
    validate_public_peer,
)


def _pinned_pool_classes(target: PublicUrlTarget):
    class VerifyPeer:
        def connect(self):
            super().connect()
            try:
                self._akane_public_peer = validate_public_peer(self.sock.getpeername()[0], target)
            except (OSError, PublicUrlPolicyError):
                self.close()
                raise

        def getresponse(self):
            response = super().getresponse()
            response._akane_public_peer = self._akane_public_peer
            return response

    class PinnedHTTPConnection(VerifyPeer, HTTPConnection):
        pass

    class PinnedHTTPSConnection(VerifyPeer, HTTPSConnection):
        pass

    class PinnedHTTPPool(HTTPConnectionPool):
        ConnectionCls = PinnedHTTPConnection

    class PinnedHTTPSPool(HTTPSConnectionPool):
        ConnectionCls = PinnedHTTPSConnection

    return {"http": PinnedHTTPPool, "https": PinnedHTTPSPool}


class PinnedPublicAdapter(HTTPAdapter):
    def __init__(self, target: PublicUrlTarget, address: str) -> None:
        # Defense in depth for callers constructing a target themselves.
        checked = validate_public_http_url(target.url, resolver=lambda *_: target.addresses)
        if address not in checked.addresses or checked.origin != target.origin:
            raise PublicUrlPolicyError("remote_url_peer_mismatch")
        self.target = checked
        self.address = address
        super().__init__(max_retries=0)
        self.poolmanager.pool_classes_by_scheme = _pinned_pool_classes(checked)

    def _pool(self, pool_kwargs: dict[str, Any] | None = None):
        kwargs = dict(pool_kwargs or {})
        scheme = urlsplit(self.target.url).scheme
        if scheme == "https":
            kwargs["server_hostname"] = self.target.hostname
            kwargs["assert_hostname"] = self.target.hostname
        return self.poolmanager.connection_from_host(
            self.address,
            port=self.target.port,
            scheme=scheme,
            pool_kwargs=kwargs,
        )

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        # Requests >= 2.32.2 supplies the TLS pool key, including its CA context.
        _host_params, kwargs = self.build_connection_pool_key_attributes(request, verify, cert)
        return self._pool(kwargs)

    def get_connection(self, url, proxies=None):
        # Requests < 2.32.2 still applies cert_verify in HTTPAdapter.send.
        return self._pool()

    def build_response(self, req, resp):
        # urllib3 1.x builds its HTTPResponse in the pool rather than in the
        # connection. Preserve the same proof before Requests consumes redirects.
        connection = getattr(resp, "connection", None) or getattr(resp, "_connection", None)
        proof = getattr(connection, "_akane_public_peer", None)
        if isinstance(proof, ValidatedPublicPeer):
            resp._akane_public_peer = proof
        return super().build_response(req, resp)

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        parsed = urlsplit(request.url)
        expected = urlsplit(self.target.url)
        if (
            request.method != "GET"
            or parsed.scheme != expected.scheme
            or (parsed.hostname or "").lower().rstrip(".") != self.target.hostname
            or (parsed.port or (443 if parsed.scheme == "https" else 80)) != self.target.port
            or parsed.username is not None
            or parsed.password is not None
            or not (verify is True or (isinstance(verify, str) and bool(verify)))
            or any((proxies or {}).values())
        ):
            raise PublicUrlPolicyError("remote_url_request_target_mismatch")
        request.headers["Host"] = urlsplit(self.target.origin).netloc
        return super().send(request, stream=stream, timeout=timeout, verify=verify, cert=cert, proxies={})


def get_pinned_public_response(
    session: requests.Session,
    target: PublicUrlTarget,
    *,
    timeout: float,
    headers: dict[str, str],
):
    """Return a streaming response; caller owns peer validation, redirects/close.

    Only retry network failures for this idempotent GET, within one shared deadline.
    HTTP error statuses are returned unchanged. Each redirect needs a new target.
    """
    deadline = time.monotonic() + max(0.25, timeout)
    last_error: requests.RequestException | None = None
    for address in target.addresses[:3]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        adapter = PinnedPublicAdapter(target, address)
        # A request-local Session is used by all consumers. Replace both schemes
        # so an unexpected URL cannot fall through to an unpinned default adapter.
        for scheme in ("https://", "http://"):
            previous = session.adapters.get(scheme)
            if previous is not None:
                previous.close()
            session.mount(scheme, adapter)
        session.trust_env = False
        session.cookies.clear()
        try:
            return session.get(
                target.url,
                stream=True,
                timeout=(min(3.0, remaining), remaining),
                headers=headers,
                allow_redirects=False,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise requests.Timeout("public request deadline exceeded")
