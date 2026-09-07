"""Fake-IP recovery without weakening private-network or TLS boundaries."""

from __future__ import annotations

import json
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPSConnection

from companion_v01 import public_dns
from companion_v01.attachment_ingest import AttachmentIngestService, AttachmentMaterializationError
from companion_v01.public_http import PinnedPublicAdapter, get_pinned_public_response
from companion_v01.public_url_policy import (
    PublicUrlPolicyError,
    PublicUrlTarget,
    ValidatedPublicPeer,
    validate_public_http_url,
    validate_response_peer,
)
from tests.test_attachment_ingest import FakeHttpSession, FakeStreamResponse


PUBLIC = "93.184.216.34"
SYNTHETIC = "198.18.0.117"


class PublicDnsPolicyTests(unittest.TestCase):
    def test_synthetic_domains_use_one_generic_fallback_and_keep_operational_url(self):
        with (
            patch("companion_v01.public_url_policy.resolve_host_addresses", return_value=(SYNTHETIC,)),
            patch("companion_v01.public_url_policy.resolve_public_dns", return_value=(PUBLIC,)) as fallback,
        ):
            for host in ("b23.tv", "www.python.org", "cdn.example.com", "www.bilibili.com"):
                url = f"https://{host}/file?signature=keep-it#fragment"
                target = validate_public_http_url(url)
                self.assertEqual(target.addresses, (PUBLIC,))
                self.assertEqual(target.url, url.split("#")[0])
                self.assertEqual(fallback.call_args.args, (host,))
            self.assertEqual(fallback.call_count, 4)

    def test_normal_public_dns_is_unchanged_and_never_uses_fallback(self):
        with (
            patch("companion_v01.public_url_policy.resolve_host_addresses", return_value=(PUBLIC,)),
            patch("companion_v01.public_url_policy.resolve_public_dns") as fallback,
        ):
            self.assertEqual(validate_public_http_url("https://media.example/file").addresses, (PUBLIC,))
            fallback.assert_not_called()

    def test_real_private_and_mixed_private_synthetic_dns_are_not_overridden(self):
        for rows in (
            ("127.0.0.1",),
            ("10.0.0.5", SYNTHETIC),
            (PUBLIC, "10.0.0.5"),
            ("::ffff:127.0.0.1",),
            ("169.254.169.254",),
            ("fd00::1",),
            ("100.64.0.1",),
        ):
            with (
                self.subTest(rows=rows),
                patch("companion_v01.public_url_policy.resolve_host_addresses", return_value=rows),
                patch("companion_v01.public_url_policy.resolve_public_dns") as fallback,
            ):
                with self.assertRaisesRegex(PublicUrlPolicyError, "remote_url_private_address"):
                    validate_public_http_url("https://media.example/file")
                fallback.assert_not_called()

    def test_ip_literals_and_local_names_never_trigger_external_dns(self):
        with patch("companion_v01.public_url_policy.resolve_public_dns") as fallback:
            for url in (
                "http://198.18.0.117/x",
                "http://127.0.0.1/",
                "http://[::1]/",
                "http://localhost/",
                "http://service.internal/",
                "http://service.local/",
            ):
                with self.subTest(url=url), self.assertRaises(PublicUrlPolicyError):
                    validate_public_http_url(url)
            fallback.assert_not_called()

    def test_private_or_mixed_encrypted_dns_answers_still_fail_closed(self):
        for rows in ((PUBLIC, "10.0.0.1"), ("::1",), (SYNTHETIC,), (PUBLIC, "fd00::1")):
            with (
                self.subTest(rows=rows),
                patch("companion_v01.public_url_policy.resolve_host_addresses", return_value=(SYNTHETIC,)),
                patch("companion_v01.public_url_policy.resolve_public_dns", return_value=rows),
            ):
                with self.assertRaisesRegex(PublicUrlPolicyError, "remote_url_private_address"):
                    validate_public_http_url("https://media.example/")

    def test_failure_and_opt_out_are_explicit_not_misreported_as_bad_user_link(self):
        with patch("companion_v01.public_url_policy.resolve_host_addresses", return_value=(SYNTHETIC,)):
            with patch("companion_v01.public_url_policy.config.PUBLIC_URL_DNS_FALLBACK_ENABLED", False):
                with self.assertRaisesRegex(PublicUrlPolicyError, "remote_url_synthetic_dns_address"):
                    validate_public_http_url("https://media.example/")
            with patch("companion_v01.public_url_policy.resolve_public_dns", side_effect=public_dns.PublicDnsError()):
                with self.assertRaisesRegex(PublicUrlPolicyError, "remote_url_public_dns_unavailable"):
                    validate_public_http_url("https://media.example/")
        service = object.__new__(AttachmentIngestService)
        feedback = service._humanize_remote_fetch_error("remote_url_public_dns_unavailable")
        self.assertIn("访问环境", feedback)
        self.assertNotIn("请提供公开直链", feedback)
        self.assertEqual(
            service._material_failure_code("remote_url_public_dns_unavailable"), "remote_url_public_dns_unavailable"
        )

    def test_port_zero_rejected_and_unicode_hostname_matches_requests_idna(self):
        with self.assertRaisesRegex(PublicUrlPolicyError, "remote_url_invalid"):
            validate_public_http_url("https://example.com:0/")
        target = validate_public_http_url("https://例子.测试:443/path", resolver=lambda *_: (PUBLIC,))
        prepared = requests.Request("GET", target.url).prepare()
        adapter = PinnedPublicAdapter(target, PUBLIC)
        with patch.object(HTTPAdapter, "send", return_value="sent"):
            self.assertEqual(adapter.send(prepared), "sent")
        adapter.close()


class EncryptedDnsTests(unittest.TestCase):
    def setUp(self):
        with public_dns._LOCK:
            public_dns._CACHE.clear()
        self.addCleanup(public_dns._CACHE.clear)

    def response(self, data, *, status=200):
        raw = data if isinstance(data, bytes) else json.dumps(data).encode()
        return SimpleNamespace(status_code=status, iter_content=lambda **_: iter([raw]), close=Mock())

    def answer(self, *, name="media.example", kind=1, addresses=(PUBLIC,)):
        return {
            "Status": 0,
            "Question": [{"name": name + ".", "type": kind}],
            "Answer": [{"name": name, "type": kind, "TTL": 30, "data": ip} for ip in addresses],
        }

    def query(self, response, *, kind=1):
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.get.return_value = response
        with patch("companion_v01.public_dns.requests.Session", return_value=session):
            result = public_dns._query("media.example", kind)
        return result, session

    def test_bootstrap_uses_numeric_tls_endpoint_no_redirect_proxy_or_user_url(self):
        result, session = self.query(self.response(self.answer()))
        self.assertEqual(result, ((PUBLIC,), 30))
        self.assertEqual(session.get.call_args.args, ("https://1.1.1.1/dns-query",))
        self.assertFalse(session.trust_env)
        kwargs = session.get.call_args.kwargs
        self.assertEqual(kwargs["params"], {"name": "media.example", "type": 1, "cd": "false"})
        self.assertFalse(kwargs["allow_redirects"])
        self.assertNotEqual(kwargs.get("verify"), False)

    def test_cname_chain_allowed_but_unrelated_answer_not_used(self):
        data = self.answer(addresses=())
        data["Answer"] = [
            {"name": "media.example", "type": 5, "TTL": 20, "data": "cdn.example."},
            {"name": "cdn.example", "type": 1, "TTL": 30, "data": PUBLIC},
            {"name": "unrelated.example", "type": 1, "TTL": 30, "data": "10.0.0.1"},
        ]
        result, _ = self.query(self.response(data))
        self.assertEqual(result, ((PUBLIC,), 20))

    def test_bad_status_question_oversized_and_malformed_responses_are_bounded_errors(self):
        cases = [
            self.response(self.answer(), status=302),
            self.response(b"invalid"),
            self.response(b" " * (65536 + 1)),
            self.response({**self.answer(), "Status": 2}),
            self.response({**self.answer(), "TC": True}),
            self.response(self.answer(name="other.example")),
            self.response(self.answer(kind=28)),
            self.response(self.answer(addresses=("not an IP",))),
        ]
        for response in cases:
            with self.subTest(status=response.status_code), self.assertRaises(public_dns.PublicDnsError):
                self.query(response)
            response.close.assert_called_once()

    def test_both_families_and_bounded_ttl_cache(self):
        clock = [100.0]

        def query(host, kind):
            self.assertEqual(host, "media.example")
            return ((PUBLIC,), 30) if kind == 1 else (("2606:4700:4700::1111",), 20)

        with (
            patch("companion_v01.public_dns._query", side_effect=query) as resolver,
            patch("companion_v01.public_dns.time.monotonic", side_effect=lambda: clock[0]),
        ):
            expected = (PUBLIC, "2606:4700:4700::1111")
            self.assertEqual(public_dns.resolve_public_dns("media.example"), expected)
            self.assertEqual(public_dns.resolve_public_dns("media.example"), expected)
            self.assertEqual(resolver.call_count, 2)
            clock[0] = 121.0
            self.assertEqual(public_dns.resolve_public_dns("media.example"), expected)
            self.assertEqual(resolver.call_count, 4)

    def test_failure_is_not_cached_and_a_failed_family_is_not_hidden(self):
        def query(_host, kind):
            if kind == 28:
                raise public_dns.PublicDnsError()
            return ((PUBLIC,), 30)

        with patch("companion_v01.public_dns._query", side_effect=query):
            with self.assertRaises(public_dns.PublicDnsError):
                public_dns.resolve_public_dns("media.example")
        self.assertEqual(len(public_dns._CACHE), 0)


class PinnedHttpTests(unittest.TestCase):
    def setUp(self):
        self.target = validate_public_http_url(
            "https://media.example:443/file?signature=keep", resolver=lambda *_: (PUBLIC,)
        )
        self.adapter = PinnedPublicAdapter(self.target, PUBLIC)
        self.addCleanup(self.adapter.close)

    def test_socket_pool_is_ip_pinned_but_sni_certificate_and_host_use_original_domain(self):
        prepared = requests.Request("GET", self.target.url, headers={"Host": "wrong.example"}).prepare()
        pool = self.adapter.get_connection_with_tls_context(prepared, verify=True)
        self.assertEqual(pool.host, PUBLIC)
        self.assertEqual(pool.conn_kw["server_hostname"], "media.example")
        self.assertEqual(pool.assert_hostname, "media.example")
        original_resolver = socket.getaddrinfo
        with patch.object(HTTPAdapter, "send", return_value="sent"):
            self.assertEqual(self.adapter.send(prepared), "sent")
        self.assertEqual(prepared.headers["Host"], "media.example")
        self.assertEqual(prepared.url, self.target.url)
        self.assertIs(socket.getaddrinfo, original_resolver)

    def test_unexpected_target_method_proxy_or_disabled_tls_is_rejected_before_send(self):
        bad = [
            (requests.Request("POST", self.target.url).prepare(), {}),
            (requests.Request("GET", "https://other.example/").prepare(), {}),
            (requests.Request("GET", "http://media.example/").prepare(), {}),
            (requests.Request("GET", self.target.url).prepare(), {"verify": False}),
            (requests.Request("GET", self.target.url).prepare(), {"verify": None}),
            (requests.Request("GET", self.target.url).prepare(), {"proxies": {"https": "http://127.0.0.1:8888"}}),
        ]
        with patch.object(HTTPAdapter, "send") as send:
            for prepared, kwargs in bad:
                with self.assertRaisesRegex(PublicUrlPolicyError, "remote_url_request_target_mismatch"):
                    self.adapter.send(prepared, **kwargs)
            send.assert_not_called()

    def test_private_constructed_target_or_unapproved_ip_is_rejected(self):
        target = PublicUrlTarget(
            "https://media.example/", "https://media.example", "media.example", 443, ("127.0.0.1",)
        )
        with self.assertRaises(PublicUrlPolicyError):
            PinnedPublicAdapter(target, "127.0.0.1")
        with self.assertRaises(PublicUrlPolicyError):
            PinnedPublicAdapter(self.target, "8.8.8.8")

    def test_connection_checks_peer_before_http_and_preserves_proof_after_close(self):
        pool = self.adapter.get_connection(self.target.url)
        conn = pool._new_conn()
        sock = SimpleNamespace(getpeername=lambda: (PUBLIC, 443), close=Mock())
        with patch.object(
            HTTPSConnection, "connect", autospec=True, side_effect=lambda connection: setattr(connection, "sock", sock)
        ):
            conn.connect()
        raw = SimpleNamespace(connection=None)
        with patch.object(HTTPSConnection, "getresponse", return_value=raw):
            self.assertIs(conn.getresponse(), raw)
        conn.close()
        validate_response_peer(SimpleNamespace(raw=raw), self.target)
        self.assertIsInstance(raw._akane_public_peer, ValidatedPublicPeer)

    def test_mismatched_actual_peer_is_rejected_and_closed(self):
        pool = self.adapter.get_connection(self.target.url)
        conn = pool._new_conn()
        sock = SimpleNamespace(getpeername=lambda: ("127.0.0.1", 443), close=Mock())
        with patch.object(
            HTTPSConnection, "connect", autospec=True, side_effect=lambda connection: setattr(connection, "sock", sock)
        ):
            with self.assertRaisesRegex(PublicUrlPolicyError, "remote_url_peer_mismatch"):
                conn.connect()
        sock.close.assert_called_once()

    def test_non_verified_or_wrong_origin_proof_does_not_bypass_policy(self):
        for proof in (
            {"address": PUBLIC},
            ValidatedPublicPeer(PUBLIC, "https://other.example"),
            ValidatedPublicPeer("127.0.0.1", self.target.origin),
        ):
            raw = SimpleNamespace(connection=None, _akane_public_peer=proof)
            with self.assertRaises(PublicUrlPolicyError):
                validate_response_peer(SimpleNamespace(raw=raw), self.target)


class PublicFetchIntegrationTests(unittest.TestCase):
    def test_synthetic_dns_redirect_to_another_public_host_downloads_with_distinct_pins(self):
        service = object.__new__(AttachmentIngestService)
        service.public_host_resolver = None
        first = FakeStreamResponse(peer_ip=PUBLIC, status_code=302, headers={"Location": "https://cdn.example/final"})
        second = FakeStreamResponse(peer_ip="8.8.8.8", chunks=[b"real pipeline fixture"])
        session = FakeHttpSession([first, second])
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("companion_v01.public_url_policy.resolve_host_addresses", return_value=(SYNTHETIC,)),
            patch(
                "companion_v01.public_url_policy.resolve_public_dns",
                side_effect=lambda host: (PUBLIC,) if host == "media.example" else ("8.8.8.8",),
            ),
            patch("companion_v01.attachment_ingest.requests.Session", return_value=session),
        ):
            target = Path(tmp) / "file.bin"
            service._download_to_path(url="https://media.example/start", target_path=target, max_bytes=100, timeout=10)
            self.assertEqual(target.read_bytes(), b"real pipeline fixture")
            self.assertEqual(len(session.calls), 2)
            self.assertEqual(session.adapters["https://"].address, "8.8.8.8")
            self.assertTrue(first.closed and second.closed)

    def test_synthetic_public_start_redirect_to_real_private_still_never_fetches_private(self):
        service = object.__new__(AttachmentIngestService)
        service.public_host_resolver = None
        first = FakeStreamResponse(peer_ip=PUBLIC, status_code=302, headers={"Location": "http://127.0.0.1/private"})
        session = FakeHttpSession([first])
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("companion_v01.public_url_policy.resolve_host_addresses", return_value=(SYNTHETIC,)),
            patch("companion_v01.public_url_policy.resolve_public_dns", return_value=(PUBLIC,)),
            patch("companion_v01.attachment_ingest.requests.Session", return_value=session),
        ):
            target = Path(tmp) / "file.bin"
            with self.assertRaisesRegex(AttachmentMaterializationError, "remote_url_private_address"):
                service._download_to_path(url="https://media.example/start", target_path=target)
            self.assertFalse(target.exists())
            self.assertFalse(target.with_name(".file.bin.part").exists())
            self.assertEqual(len(session.calls), 1)

    def test_network_retry_is_bounded_and_http_rejection_not_retried(self):
        target = validate_public_http_url(
            "https://media.example/", resolver=lambda *_: (PUBLIC, "8.8.8.8", "1.1.1.1", "9.9.9.9")
        )
        with (
            requests.Session() as session,
            patch.object(session, "get", side_effect=requests.ConnectionError("test")) as get,
        ):
            with self.assertRaises(requests.ConnectionError):
                get_pinned_public_response(session, target, timeout=1, headers={})
            self.assertEqual(get.call_count, 3)
        response = FakeStreamResponse(peer_ip=PUBLIC, status_code=403)
        with requests.Session() as session, patch.object(session, "get", return_value=response) as get:
            self.assertIs(get_pinned_public_response(session, target, timeout=1, headers={}), response)
            self.assertEqual(get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
