"""Public metadata boundary tests: real parsing/policy, fake external responses."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import requests

from companion_v01.public_url_policy import ValidatedPublicPeer, validate_public_http_url
from companion_v01.qq_bilibili_share import BilibiliShareError, resolve_bilibili_share

BVID = "BV17x411w7KC"
METADATA = {"code": 0, "data": {"bvid": BVID, "aid": 170001, "title": "真实视频标题",
                              "desc": "视频介绍", "pic": "https://i0.hdslb.com/bfs/archive/real.jpg"}}


class PublicResponse:
    def __init__(self, target, payload=METADATA, *, status=200, location="", raw=None):
        self.status_code = status
        self.headers = {"Location": location}
        self.raw = SimpleNamespace(_akane_public_peer=ValidatedPublicPeer("8.8.8.8", target.origin))
        self.body = json.dumps(payload).encode() if raw is None else raw
        self.closed = False

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset:offset + chunk_size]

    def close(self):
        self.closed = True


def public_target(url):
    return validate_public_http_url(url, resolver=lambda *_: ["8.8.8.8"])


class BilibiliShareTests(unittest.TestCase):
    def setUp(self):
        self.targets = []
        self.responses = []
        self.factory = lambda target: PublicResponse(target)
        p = patch('companion_v01.qq_bilibili_share.validate_public_http_url', side_effect=public_target)
        p.start(); self.addCleanup(p.stop)
        p = patch('companion_v01.qq_bilibili_share.get_pinned_public_response', side_effect=self.fetch)
        p.start(); self.addCleanup(p.stop)

    def fetch(self, session, target, **kwargs):
        self.targets.append(target)
        self.assertNotIn('Authorization', kwargs['headers'])
        self.assertNotIn('Cookie', kwargs['headers'])
        self.assertLessEqual(kwargs['timeout'], 15)
        response = self.factory(target)
        self.responses.append(response)
        return response

    def failure(self, source, reason):
        with self.assertRaises(BilibiliShareError) as raised:
            resolve_bilibili_share(source)
        self.assertEqual(raised.exception.reason, reason)

    def test_bv_and_video_urls_use_real_metadata_without_downloading_video(self):
        for source in (BVID, f'https://www.bilibili.com/video/{BVID}/?share_source=qq',
                       'https://m.bilibili.com/video/av170001?p=1'):
            with self.subTest(source=source):
                self.targets.clear()
                result = resolve_bilibili_share(source)
                self.assertEqual(result['title'], '真实视频标题')
                self.assertEqual(result['jumpUrl'], 'pages/video/video.html?avid=170001')
                self.assertEqual(result['webUrl'], f'https://www.bilibili.com/video/{BVID}')
                self.assertEqual(result['picUrl'], METADATA['data']['pic'])
                self.assertEqual(len(self.targets), 1)
                self.assertEqual(self.targets[0].hostname, 'api.bilibili.com')
        self.assertTrue(all(r.closed for r in self.responses))

    def test_b23_redirect_is_allowlisted_then_resolved_by_id(self):
        self.factory = lambda t: (PublicResponse(t, status=302, location=f'https://www.bilibili.com/video/{BVID}')
                                  if t.hostname == 'b23.tv' else PublicResponse(t))
        result = resolve_bilibili_share('https://b23.tv/actualShort')
        self.assertEqual(result['title'], '真实视频标题')
        self.assertEqual([t.hostname for t in self.targets], ['b23.tv', 'api.bilibili.com'])

    def test_untrusted_sources_cannot_trigger_fetch(self):
        for source in (None, 123, '', 'https://evil.test/video/'+BVID,
                       'https://www.bilibili.com.evil.test/video/'+BVID,
                       'https://user:pass@www.bilibili.com/video/'+BVID,
                       'http://127.0.0.1/video/'+BVID, 'file:///video/'+BVID,
                       'https://www.bilibili.com:999/video/'+BVID,
                       'https://www.bilibili.com/search?keyword='+BVID,
                       'https://www.bilibili.com/video/'+BVID+'?p=2'):
            with self.subTest(source=source), self.assertRaises(BilibiliShareError):
                resolve_bilibili_share(source)
        self.assertFalse(self.targets)

    def test_redirect_cannot_escape_provider_or_downgrade_tls(self):
        for location in ('http://127.0.0.1/', 'https://example.com/video/'+BVID,
                         'http://www.bilibili.com/video/'+BVID, '//localhost/x'):
            self.targets.clear()
            self.factory = lambda t: PublicResponse(t, status=302, location=location)
            with self.subTest(location=location), self.assertRaises(BilibiliShareError):
                resolve_bilibili_share('https://b23.tv/abc')
            self.assertEqual(len(self.targets), 1)

    def test_redirect_loop_is_bounded(self):
        self.factory = lambda t: PublicResponse(t, status=302, location='https://b23.tv/again')
        self.failure('https://b23.tv/abc', 'bilibili_redirect_limit')
        self.assertEqual(len(self.targets), 3)

    def test_rate_limit_and_bad_body_are_honest_failures(self):
        for status in (403, 412, 429):
            self.factory = lambda t: PublicResponse(t, status=status)
            self.failure(BVID, 'bilibili_metadata_rate_limited')
        for raw, reason in ((b'<html>challenge</html>', 'bilibili_metadata_invalid'),
                            (b'x' * (512 * 1024 + 1), 'bilibili_metadata_too_large')):
            self.factory = lambda t: PublicResponse(t, raw=raw)
            self.failure(BVID, reason)
        self.assertTrue(all(r.closed for r in self.responses))

    def test_identity_missing_fields_and_cover_must_be_valid(self):
        for changes, reason in (({'aid': 0}, 'bilibili_metadata_incomplete'),
                                ({'aid': True}, 'bilibili_metadata_incomplete'),
                                ({'title': ''}, 'bilibili_metadata_incomplete'),
                                ({'pic': ''}, 'bilibili_metadata_incomplete'),
                                ({'bvid': 'BV1GJ411x7h7'}, 'bilibili_metadata_identity_mismatch'),
                                ({'pic': 'http://127.0.0.1/a'}, 'bilibili_cover_invalid')):
            payload = {'code': 0, 'data': {**METADATA['data'], **changes}}
            self.factory = lambda t: PublicResponse(t, payload)
            self.failure(BVID, reason)

    def test_timeout_has_no_sensitive_exception_text(self):
        def timeout(_):
            raise requests.Timeout('internal-sensitive-path-and-url')
        self.factory = timeout
        self.failure(BVID, 'bilibili_metadata_timeout')

    def test_dns_and_peer_checks_are_not_bypassed(self):
        with patch('companion_v01.qq_bilibili_share.validate_public_http_url',
                   side_effect=lambda u: validate_public_http_url(u, resolver=lambda *_: ['127.0.0.1'])):
            self.failure(BVID, 'bilibili_public_url_rejected')
        self.assertFalse(self.targets)
        def bad_peer(target):
            response = PublicResponse(target)
            response.raw._akane_public_peer = ValidatedPublicPeer('127.0.0.1', target.origin)
            return response
        self.factory = bad_peer
        self.failure(BVID, 'bilibili_public_url_rejected')
        self.assertTrue(self.responses[-1].closed)
