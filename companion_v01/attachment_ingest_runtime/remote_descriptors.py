"""Public media URL normalization and direct or platform descriptor resolution."""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from urllib.parse import (
    unquote,
    urlencode,
    urlparse,
)
import config
from .definitions import (
    AUDIO_MEDIA_SUFFIXES,
    AttachmentMaterializationError,
    MEDIA_SUFFIXES,
    REMOTE_MEDIA_DEFAULT_MAX_URLS,
    REMOTE_MEDIA_DEFAULT_USER_AGENT,
    RemoteMediaDescriptor,
)


class AttachmentRemoteDescriptorsMixin:
    """Methods use the state owned by AttachmentIngestService."""

    # Hosts whose links are pure redirects; yt-dlp ships no extractor for them.
    YTDLP_SHORT_LINK_HOSTS = ("b23.tv",)

    def _normalize_remote_urls(self, value: list[str] | tuple[str, ...] | set[str] | str) -> list[str]:
        if isinstance(value, str):
            raw_items = re.split(r"[\s,，;；]+", value)
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = [value]
        seen: set[str] = set()
        normalized: list[str] = []
        limit = max(
            1,
            int(
                getattr(config, "REMOTE_MEDIA_MAX_URLS_PER_CALL", REMOTE_MEDIA_DEFAULT_MAX_URLS)
                or REMOTE_MEDIA_DEFAULT_MAX_URLS
            ),
        )
        for item in raw_items:
            text = str(item or "").strip()
            if not text:
                continue
            parsed = urlparse(text)
            if parsed.scheme not in {"http", "https"}:
                continue
            if text in seen:
                continue
            seen.add(text)
            normalized.append(text[:1000])
            if len(normalized) >= limit:
                break
        return normalized

    def _fetch_remote_media_descriptor(
        self,
        *,
        url: str,
        preferred_title: str = "",
    ) -> RemoteMediaDescriptor:
        target = self._validate_public_remote_url(url)
        url = target.url
        direct_descriptor = self._build_direct_media_descriptor(
            url=url,
            preferred_title=preferred_title,
        )
        if direct_descriptor is not None:
            return direct_descriptor

        if not self._is_ytdlp_provider_allowed(url):
            raise AttachmentMaterializationError("remote_media_provider_not_allowed")
        url = self._canonical_ytdlp_page_url(url)
        try:
            descriptor = self._extract_remote_media_with_yt_dlp(
                url=url,
                preferred_title=preferred_title,
            )
        except AttachmentMaterializationError:
            descriptor = self._extract_remote_media_with_bilibili_api(
                url=url,
                preferred_title=preferred_title,
            )
            if descriptor is None:
                raise
        if descriptor is not None:
            return descriptor

        descriptor = self._extract_remote_media_with_bilibili_api(
            url=url,
            preferred_title=preferred_title,
        )
        if descriptor is not None:
            return descriptor

        raise AttachmentMaterializationError("remote_media_ytdlp_unavailable")

    def _build_direct_media_descriptor(
        self,
        *,
        url: str,
        preferred_title: str = "",
    ) -> RemoteMediaDescriptor | None:
        parsed = urlparse(url)
        suffix = Path(unquote(parsed.path or "")).suffix.lower()
        if suffix not in MEDIA_SUFFIXES:
            return None
        title = (
            str(preferred_title or Path(parsed.path).stem or parsed.netloc or "remote_media").strip() or "remote_media"
        )
        mime_type = mimetypes.guess_type(parsed.path or "")[0] or ""
        if not mime_type:
            mime_type = "audio/mpeg" if suffix in AUDIO_MEDIA_SUFFIXES else "video/mp4"
        kind = "audio" if suffix in AUDIO_MEDIA_SUFFIXES or mime_type.startswith("audio/") else "file"
        return RemoteMediaDescriptor(
            source_url=url,
            webpage_url=url,
            title=title,
            ext=suffix.lstrip("."),
            mime_type=mime_type,
            kind=kind,
            download_mode="direct",
            extractor="direct",
            extractor_key="direct",
        )

    def _extract_remote_media_with_bilibili_api(
        self,
        *,
        url: str,
        preferred_title: str = "",
    ) -> RemoteMediaDescriptor | None:
        if not self._is_bilibili_public_url(url):
            return None

        page_url = self._resolve_bilibili_page_url(url)
        identity = self._parse_bilibili_video_identity(page_url)
        if not identity:
            raise AttachmentMaterializationError("remote_media_bilibili_identity_missing")

        view_query = urlencode(identity)
        view_payload = self._fetch_public_json(
            url=f"https://api.bilibili.com/x/web-interface/view?{view_query}",
            headers=self._bilibili_request_headers(referer=page_url),
        )
        if self._safe_int(view_payload.get("code")) != 0:
            raise AttachmentMaterializationError("remote_media_bilibili_api_failed")
        view_data = view_payload.get("data") if isinstance(view_payload.get("data"), dict) else {}
        cid = self._safe_int(view_data.get("cid"))
        if not cid:
            pages = view_data.get("pages") if isinstance(view_data.get("pages"), list) else []
            first_page = pages[0] if pages and isinstance(pages[0], dict) else {}
            cid = self._safe_int(first_page.get("cid"))
        if not cid:
            raise AttachmentMaterializationError("remote_media_bilibili_api_failed")

        play_identity = dict(identity)
        play_identity.update({"cid": str(cid), "qn": "64", "fnval": "0", "fnver": "0", "fourk": "1"})
        play_payload = self._fetch_public_json(
            url=f"https://api.bilibili.com/x/player/playurl?{urlencode(play_identity)}",
            headers=self._bilibili_request_headers(referer=page_url),
        )
        if self._safe_int(play_payload.get("code")) != 0:
            raise AttachmentMaterializationError("remote_media_bilibili_api_failed")
        play_data = play_payload.get("data") if isinstance(play_payload.get("data"), dict) else {}
        durl = play_data.get("durl") if isinstance(play_data.get("durl"), list) else []
        media_urls: list[str] = []
        total_size = 0
        for item in durl:
            if not isinstance(item, dict):
                continue
            media_url = str(item.get("url") or "").strip()
            if not media_url:
                continue
            self._validate_public_remote_url(media_url)
            media_urls.append(media_url)
            total_size += self._safe_int(item.get("size"))
        if not media_urls:
            raise AttachmentMaterializationError("remote_media_bilibili_stream_missing")

        bvid = str(view_data.get("bvid") or identity.get("bvid") or "").strip()
        aid = self._safe_int(view_data.get("aid") or identity.get("aid"))
        canonical_page_url = (
            f"https://www.bilibili.com/video/{bvid}" if bvid else f"https://www.bilibili.com/video/av{aid}"
        )
        owner = view_data.get("owner") if isinstance(view_data.get("owner"), dict) else {}
        play_format = str(play_data.get("format") or "").strip().lower()
        ext = "mp4" if "mp4" in play_format else "flv"
        return RemoteMediaDescriptor(
            source_url=url,
            webpage_url=canonical_page_url,
            title=str(preferred_title or view_data.get("title") or "bilibili_video").strip(),
            ext=ext,
            mime_type="video/mp4" if ext == "mp4" else "video/x-flv",
            kind="file",
            download_mode="bilibili_api",
            extractor="BiliBili public API",
            extractor_key="BiliBiliPublicApi",
            uploader=str(owner.get("name") or "").strip(),
            channel=str(owner.get("mid") or "").strip(),
            duration_seconds=self._safe_float(view_data.get("duration")),
            thumbnail_url=str(view_data.get("pic") or "").strip(),
            description=str(view_data.get("desc") or "").strip()[:1000],
            file_size_hint=total_size,
            media_urls=tuple(media_urls),
        )

    def _is_bilibili_public_url(self, url: str) -> bool:
        host = str(urlparse(str(url or "").strip()).hostname or "").strip().lower().rstrip(".")
        return host == "b23.tv" or host == "bilibili.com" or host.endswith(".bilibili.com")

    def _canonical_ytdlp_page_url(self, url: str) -> str:
        """Rewrite a provider short link into a page URL that yt-dlp can extract.

        yt-dlp ships no extractor for b23.tv, and the public-API fallback is
        answered with HTTP 412 (risk control) for anonymous callers, so the
        redirect is followed here and reduced to the canonical video page. The
        original link is kept whenever resolution is unavailable.
        """
        host = str(urlparse(str(url or "").strip()).hostname or "").strip().lower().rstrip(".")
        if host not in self.YTDLP_SHORT_LINK_HOSTS:
            return url
        try:
            resolved = self._resolve_bilibili_page_url(url)
        except AttachmentMaterializationError:
            return url
        identity = self._parse_bilibili_video_identity(resolved)
        if identity.get("bvid"):
            return f"https://www.bilibili.com/video/{identity['bvid']}"
        if identity.get("aid"):
            return f"https://www.bilibili.com/video/av{identity['aid']}"
        return resolved

    def _parse_bilibili_video_identity(self, url: str) -> dict[str, str]:
        text = unquote(str(url or "").strip())
        bvid_match = re.search(r"(?i)(BV[0-9A-Za-z]{10})", text)
        if bvid_match:
            return {"bvid": bvid_match.group(1)}
        aid_match = re.search(r"(?i)(?:/video/)?av(\d+)", text)
        if aid_match:
            return {"aid": aid_match.group(1)}
        return {}

    def _resolve_bilibili_page_url(self, url: str) -> str:
        target = self._validate_public_remote_url(url)
        if self._parse_bilibili_video_identity(target.url):
            return target.url
        if str(urlparse(target.url).hostname or "").strip().lower() != "b23.tv":
            return target.url
        return self._resolve_public_redirect_url(
            url=target.url,
            headers=self._bilibili_request_headers(referer="https://www.bilibili.com/"),
        )

    def _bilibili_request_headers(self, *, referer: str) -> dict[str, str]:
        return {
            "User-Agent": (
                str(getattr(config, "REMOTE_MEDIA_YTDLP_USER_AGENT", "") or "").strip()
                or REMOTE_MEDIA_DEFAULT_USER_AGENT
            ),
            "Referer": str(referer or "https://www.bilibili.com/").strip(),
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
