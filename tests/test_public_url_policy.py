from __future__ import annotations

import unittest
from types import SimpleNamespace

from companion_v01.public_url_policy import (
    PublicUrlPolicyError,
    is_ytdlp_provider_url,
    public_url_display_origin,
    public_url_fingerprint,
    validate_public_http_url,
    validate_response_peer,
)
from companion_v01.media_bridge_engine import redact_remote_media_urls_for_prompt


class PublicUrlPolicyTests(unittest.TestCase):
    def test_public_domain_is_normalized_and_fragment_is_removed(self) -> None:
        calls: list[tuple[str, int]] = []

        def resolver(hostname: str, port: int):
            calls.append((hostname, port))
            return ("93.184.216.34",)

        result = validate_public_http_url(
            "https://Media.Example/path/file.mp4?token=secret#fragment",
            resolver=resolver,
        )

        self.assertEqual(result.url, "https://Media.Example/path/file.mp4?token=secret")
        self.assertEqual(result.origin, "https://media.example")
        self.assertEqual(result.addresses, ("93.184.216.34",))
        self.assertEqual(calls, [("media.example", 443)])
        self.assertNotIn("token=secret", repr(result))

    def test_private_literal_and_mixed_dns_results_are_rejected(self) -> None:
        cases = (
            ("http://127.0.0.1/private", lambda *_args: ("127.0.0.1",)),
            ("http://169.254.169.254/latest/meta-data", lambda *_args: ("169.254.169.254",)),
            ("https://mixed.example/file", lambda *_args: ("93.184.216.34", "10.0.0.5")),
            ("https://v6.example/file", lambda *_args: ("fe80::1",)),
            ("https://loopback-v6.example/file", lambda *_args: ("::1",)),
            ("https://cgnat.example/file", lambda *_args: ("100.64.0.1",)),
            ("https://unspecified.example/file", lambda *_args: ("0.0.0.0",)),
            ("https://multicast.example/file", lambda *_args: ("224.0.0.1",)),
            ("https://mapped.example/file", lambda *_args: ("::ffff:127.0.0.1",)),
        )
        for url, resolver in cases:
            with self.subTest(url=url):
                with self.assertRaisesRegex(PublicUrlPolicyError, "remote_url_private_address"):
                    validate_public_http_url(url, resolver=resolver)

    def test_invalid_scheme_credentials_local_names_and_dns_failure_are_rejected(self) -> None:
        cases = (
            ("file:///tmp/private", "remote_url_scheme_forbidden"),
            ("https://user:pass@example.com/file", "remote_url_credentials_forbidden"),
            ("http://localhost/private", "remote_url_host_forbidden"),
            ("http://service.internal/private", "remote_url_host_forbidden"),
            ("https://missing.example/file", "remote_url_dns_failed"),
        )
        for url, code in cases:
            with self.subTest(url=url):
                with self.assertRaisesRegex(PublicUrlPolicyError, code):
                    validate_public_http_url(url, resolver=lambda *_args: ())

    def test_fingerprint_and_display_origin_do_not_expose_query_or_credentials(self) -> None:
        url = "https://example.com/private/file?token=secret"
        fingerprint = public_url_fingerprint(url)

        self.assertTrue(fingerprint.startswith("url_sha256:"))
        self.assertNotIn("secret", fingerprint)
        self.assertEqual(public_url_display_origin(url), "https://example.com")
        self.assertEqual(public_url_display_origin("https://user:pass@example.com/private"), "")
        self.assertEqual(public_url_display_origin("http://127.0.0.1/private"), "")

    def test_response_peer_must_match_a_prevalidated_public_address(self) -> None:
        target = validate_public_http_url(
            "https://media.example/file.mp4",
            resolver=lambda *_args: ("93.184.216.34",),
        )

        def response_for(peer_ip: str):
            socket = SimpleNamespace(getpeername=lambda: (peer_ip, 443))
            return SimpleNamespace(raw=SimpleNamespace(connection=SimpleNamespace(sock=socket)))

        validate_response_peer(response_for("93.184.216.34"), target)
        validate_response_peer(response_for("::ffff:93.184.216.34"), target)
        with self.assertRaisesRegex(PublicUrlPolicyError, "remote_url_peer_mismatch"):
            validate_response_peer(response_for("93.184.216.35"), target)
        with self.assertRaisesRegex(PublicUrlPolicyError, "remote_url_peer_unverifiable"):
            validate_response_peer(SimpleNamespace(raw=None), target)

    def test_ytdlp_provider_allowlist_accepts_subdomains_without_brand_wildcards(self) -> None:
        self.assertTrue(is_ytdlp_provider_url("https://www.bilibili.com/video/BV1"))
        self.assertTrue(is_ytdlp_provider_url("https://b23.tv/demo"))
        self.assertFalse(is_ytdlp_provider_url("https://bilibili.com.evil.example/watch"))
        self.assertFalse(is_ytdlp_provider_url("https://unknown.example/watch"))

    def test_model_visible_remote_links_keep_only_public_origin_or_marker(self) -> None:
        message = (
            "处理 https://media.example/private/clip.mp4?token=topsecret，"
            "不要读取 http://127.0.0.1/private?key=internal。"
        )

        rendered = redact_remote_media_urls_for_prompt(message)

        self.assertIn("https://media.example", rendered)
        self.assertIn("[受限的远程链接]", rendered)
        self.assertNotIn("/private", rendered)
        self.assertNotIn("topsecret", rendered)
        self.assertNotIn("internal", rendered)


if __name__ == "__main__":
    unittest.main()
