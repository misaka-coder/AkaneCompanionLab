"""Bilibili video metadata for QQ sharing, without downloading media or logging in."""

from __future__ import annotations

import json
import re
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import requests

from .public_http import get_pinned_public_response
from .public_url_policy import PublicUrlPolicyError, validate_public_http_url, validate_response_peer


_BVID = r"BV[0-9A-Za-z]{10}"
_PAGE_HOSTS = {"www.bilibili.com", "bilibili.com", "m.bilibili.com"}
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
    "Accept": "application/json,text/html;q=0.8",
}
_MAX_BYTES = 512 * 1024


class BilibiliShareError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _locator(source: str) -> tuple[str, dict[str, str]]:
    if not isinstance(source, str) or len(source) > 2048:
        raise BilibiliShareError("bilibili_source_invalid")
    value = source.strip()
    if re.fullmatch(_BVID, value):
        return f"https://www.bilibili.com/video/{value}", {"bvid": value}
    if not value or any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
        raise BilibiliShareError("bilibili_source_invalid")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"https", "http"} or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 80, 443)
                or parsed.hostname not in _PAGE_HOSTS | {"b23.tv"}):
            raise ValueError
    except ValueError as exc:
        raise BilibiliShareError("bilibili_source_unsupported") from exc
    if parsed.hostname == "b23.tv":
        if not re.fullmatch(r"/[0-9A-Za-z]+/?", parsed.path):
            raise BilibiliShareError("bilibili_source_unsupported")
        return value, {}
    match = re.fullmatch(rf"/video/({_BVID}|av[1-9][0-9]{{0,15}})/?", parsed.path)
    if not match:
        raise BilibiliShareError("bilibili_video_required")
    # This slice shares a whole video, not an unverified miniapp part/seek route.
    if any(p != "1" for p in parse_qs(parsed.query).get("p", [])):
        raise BilibiliShareError("bilibili_part_unsupported")
    identity = match.group(1)
    return value, {"bvid": identity} if identity.startswith("BV") else {"aid": identity[2:]}


def _request(url: str, deadline: float, *, redirect_only: bool = False):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BilibiliShareError("bilibili_metadata_timeout")
    target = validate_public_http_url(url)
    with requests.Session() as session:
        response = get_pinned_public_response(session, target, timeout=remaining, headers=_HEADERS)
        try:
            validate_response_peer(response, target)
            status = response.status_code
            if redirect_only and status in {301, 302, 303, 307, 308}:
                return str(response.headers.get("Location") or "")
            if status in {403, 412, 429}:
                raise BilibiliShareError("bilibili_metadata_rate_limited")
            if status != 200 or redirect_only:
                raise BilibiliShareError("bilibili_metadata_unavailable")
            chunks = []
            size = 0
            for chunk in response.iter_content(chunk_size=16 * 1024):
                size += len(chunk)
                if size > _MAX_BYTES:
                    raise BilibiliShareError("bilibili_metadata_too_large")
                if time.monotonic() > deadline:
                    raise BilibiliShareError("bilibili_metadata_timeout")
                chunks.append(chunk)
            return json.loads(b"".join(chunks).decode("utf-8"))
        finally:
            response.close()


def resolve_bilibili_share(source: str, *, timeout: float = 10.0) -> dict[str, str]:
    """Resolve only video IDs / allowlisted short links into real NapCat inputs."""
    deadline = time.monotonic() + max(0.1, min(15.0, timeout))
    try:
        url, identity = _locator(source)
        for _ in range(3):
            if identity:
                break
            location = _request(url, deadline, redirect_only=True)
            if not location:
                raise BilibiliShareError("bilibili_redirect_invalid")
            next_url = urljoin(url, location)
            if urlsplit(url).scheme == "https" and urlsplit(next_url).scheme != "https":
                raise BilibiliShareError("bilibili_redirect_invalid")
            url, identity = _locator(next_url)  # Reject foreign hosts before DNS / HTTP.
        if not identity:
            raise BilibiliShareError("bilibili_redirect_limit")
        payload = _request("https://api.bilibili.com/x/web-interface/view?" + urlencode(identity), deadline)
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise BilibiliShareError("bilibili_metadata_unavailable")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise BilibiliShareError("bilibili_metadata_invalid")
        bvid, aid = data.get("bvid"), data.get("aid")
        title, cover = data.get("title"), data.get("pic")
        if (not isinstance(bvid, str) or not re.fullmatch(_BVID, bvid)
                or isinstance(aid, bool) or not isinstance(aid, int) or not 0 < aid < 10**16
                or not isinstance(title, str) or not title.strip() or len(title) > 512
                or not isinstance(cover, str) or not cover):
            raise BilibiliShareError("bilibili_metadata_incomplete")
        if identity.get("bvid", bvid) != bvid or identity.get("aid", str(aid)) != str(aid):
            raise BilibiliShareError("bilibili_metadata_identity_mismatch")
        # The cover is passed to NapCat, never fetched here. Only Bilibili's CDN.
        pic = urlsplit(cover)
        if (pic.scheme not in {"https", "http"} or not (pic.hostname or "").endswith(".hdslb.com")
                or pic.username is not None or pic.password is not None or pic.port not in (None, 80, 443)):
            raise BilibiliShareError("bilibili_cover_invalid")
        desc = data.get("desc")
        return {
            "type": "bili", "title": title.strip(),
            "desc": desc[:1024] if isinstance(desc, str) else "",
            "picUrl": cover, "jumpUrl": f"pages/video/video.html?avid={aid}",
            "webUrl": f"https://www.bilibili.com/video/{bvid}",
        }
    except BilibiliShareError:
        raise
    except PublicUrlPolicyError as exc:
        raise BilibiliShareError("bilibili_public_url_rejected") from exc
    except requests.Timeout as exc:
        raise BilibiliShareError("bilibili_metadata_timeout") from exc
    except requests.RequestException as exc:
        raise BilibiliShareError("bilibili_metadata_unavailable") from exc
    except (ValueError, TypeError, UnicodeError) as exc:
        raise BilibiliShareError("bilibili_metadata_invalid") from exc
