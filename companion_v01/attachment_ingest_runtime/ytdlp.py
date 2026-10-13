"""Optional yt-dlp extraction, download, allowlisted cookies and result selection."""

from __future__ import annotations

import contextlib
import importlib.util
import mimetypes
import shutil
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlparse
import config
from ..public_url_policy import is_ytdlp_provider_url
from .definitions import (
    AttachmentMaterializationError,
    REMOTE_MEDIA_DEFAULT_REFERER,
    REMOTE_MEDIA_DEFAULT_TIMEOUT,
    REMOTE_MEDIA_DEFAULT_USER_AGENT,
    REMOTE_MEDIA_YTDLP_ALLOWED_EXTRACTORS,
    RemoteMediaDescriptor,
)


@contextlib.contextmanager
def _bilibili_anonymous_playurl_patch() -> Iterator[None]:
    """Route anonymous Bilibili playurl lookups through the unsigned endpoint.

    yt-dlp signs its playurl request with the wbi key, and Bilibili answers that
    signed call with HTTP 412 (risk control) for anonymous callers on some
    networks. The unsigned endpoint still answers normally, so the signed variant
    is substituted only while not logged in. The patch is process-global, but it
    is scoped to a single yt-dlp call and always restored, so no other request in
    this process observes it.
    """
    try:
        from yt_dlp.extractor.bilibili import BilibiliBaseIE  # type: ignore
    except Exception:
        yield
        return

    original = getattr(BilibiliBaseIE, "_download_playinfo", None)
    if original is None:
        yield
        return

    def _download_playinfo(self, bvid, cid, headers=None, query=None):
        if self.is_logged_in:
            return original(self, bvid, cid, headers=headers, query=query)
        params = {"bvid": bvid, "cid": cid, "fnval": 4048, **(query or {})}
        qn = params.get("qn")
        note = f"Downloading video format {qn} for cid {cid}" if qn else f"Downloading video formats for cid {cid}"
        return self._download_json(
            "https://api.bilibili.com/x/player/playurl",
            bvid,
            query=params,
            headers=headers,
            note=note,
        )["data"]

    BilibiliBaseIE._download_playinfo = _download_playinfo  # type: ignore[method-assign]
    try:
        yield
    finally:
        BilibiliBaseIE._download_playinfo = original  # type: ignore[method-assign]


class AttachmentYtDlpMixin:
    """Methods use the state owned by AttachmentIngestService."""

    def _extract_remote_media_with_yt_dlp(
        self,
        *,
        url: str,
        preferred_title: str = "",
    ) -> RemoteMediaDescriptor | None:
        target = self._validate_public_remote_url(url)
        url = target.url
        if not self._is_ytdlp_provider_allowed(url):
            raise AttachmentMaterializationError("remote_media_provider_not_allowed")
        if importlib.util.find_spec("yt_dlp") is None:
            return None
        try:
            from yt_dlp import YoutubeDL  # type: ignore
        except Exception as exc:
            raise AttachmentMaterializationError("remote_media_ytdlp_unavailable") from exc

        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
        }
        options.update(
            self._yt_dlp_common_options(
                timeout=float(
                    getattr(config, "REMOTE_MEDIA_DOWNLOAD_TIMEOUT", REMOTE_MEDIA_DEFAULT_TIMEOUT)
                    or REMOTE_MEDIA_DEFAULT_TIMEOUT
                )
            )
        )
        try:
            with _bilibili_anonymous_playurl_patch(), YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            raise AttachmentMaterializationError(self._classify_ytdlp_error(exc, phase="extract")) from exc

        if not isinstance(info, dict):
            raise AttachmentMaterializationError("remote_media_extract_failed")
        if str(info.get("_type") or "").strip().lower() == "playlist" or isinstance(info.get("entries"), list):
            raise AttachmentMaterializationError("remote_media_playlist_not_supported")

        ext = str(info.get("ext") or "").strip().lower()
        if not ext:
            webpage_url = str(info.get("webpage_url") or url).strip()
            ext = Path(unquote(urlparse(webpage_url).path or "")).suffix.lower().lstrip(".")
        mime_type = mimetypes.guess_type(f"sample.{ext or 'bin'}")[0] or ""
        if not mime_type:
            mime_type = "audio/mpeg" if str(info.get("vcodec") or "").strip().lower() == "none" else "video/mp4"
        title = str(preferred_title or info.get("title") or "").strip()
        if not title:
            title = (
                Path(unquote(urlparse(str(info.get("webpage_url") or url).strip()).path or "")).stem or "remote_media"
            )
        is_audio_only = str(info.get("vcodec") or "").strip().lower() == "none" and str(
            info.get("acodec") or ""
        ).strip().lower() not in {"", "none"}
        return RemoteMediaDescriptor(
            source_url=url,
            webpage_url=str(info.get("webpage_url") or url).strip(),
            title=title,
            ext=ext or ("mp3" if is_audio_only else "mp4"),
            mime_type=mime_type,
            kind="audio" if is_audio_only else "file",
            download_mode="yt_dlp",
            extractor=str(info.get("extractor") or urlparse(url).netloc).strip(),
            extractor_key=str(info.get("extractor_key") or "").strip(),
            uploader=str(info.get("uploader") or "").strip(),
            channel=str(info.get("channel") or info.get("uploader_id") or "").strip(),
            duration_seconds=self._safe_float(info.get("duration")),
            thumbnail_url=str(info.get("thumbnail") or "").strip(),
            description=str(info.get("description") or "").strip()[:1000],
            file_size_hint=self._safe_int(info.get("filesize") or info.get("filesize_approx")),
        )

    def _download_remote_media_with_yt_dlp(
        self,
        *,
        descriptor: RemoteMediaDescriptor,
        target_dir: Path,
        handle: str,
        timeout: float,
        max_bytes: int,
    ) -> Path:
        source_target = self._validate_public_remote_url(descriptor.source_url)
        if not self._is_ytdlp_provider_allowed(source_target.url):
            raise AttachmentMaterializationError("remote_media_provider_not_allowed")
        try:
            from yt_dlp import YoutubeDL  # type: ignore
        except Exception as exc:
            raise AttachmentMaterializationError("remote_media_ytdlp_unavailable") from exc

        ffmpeg_path = shutil.which("ffmpeg")
        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "overwrites": True,
            "outtmpl": {"default": str(target_dir / f"{handle}.%(ext)s")},
            "cachedir": False,
            "writesubtitles": False,
            "writeautomaticsub": False,
            "writethumbnail": False,
            "writeinfojson": False,
            "restrictfilenames": False,
            "windowsfilenames": False,
        }
        options.update(self._yt_dlp_common_options(timeout=timeout))
        if ffmpeg_path:
            options["ffmpeg_location"] = str(Path(ffmpeg_path).parent)
        if max_bytes > 0:
            options["max_filesize"] = max_bytes
        try:
            with _bilibili_anonymous_playurl_patch(), YoutubeDL(options) as ydl:
                ydl.download([source_target.url])
        except Exception as exc:
            raise AttachmentMaterializationError(self._classify_ytdlp_error(exc, phase="download")) from exc

        downloaded = self._locate_downloaded_remote_media_file(
            target_dir=target_dir,
            handle=handle,
        )
        if downloaded is None:
            raise AttachmentMaterializationError("remote_media_download_missing")
        if max_bytes > 0 and downloaded.stat().st_size > max_bytes:
            raise AttachmentMaterializationError("attachment_too_large")
        return downloaded

    def _yt_dlp_common_options(self, *, timeout: float | None = None) -> dict[str, Any]:
        user_agent = (
            str(getattr(config, "REMOTE_MEDIA_YTDLP_USER_AGENT", "") or "").strip() or REMOTE_MEDIA_DEFAULT_USER_AGENT
        )
        referer = str(getattr(config, "REMOTE_MEDIA_YTDLP_REFERER", "") or "").strip() or REMOTE_MEDIA_DEFAULT_REFERER
        headers = {
            "User-Agent": user_agent,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if referer:
            headers["Referer"] = referer
        options: dict[str, Any] = {
            "http_headers": headers,
            "allowed_extractors": list(REMOTE_MEDIA_YTDLP_ALLOWED_EXTRACTORS),
            "proxy": "",
        }
        if timeout is not None:
            options["socket_timeout"] = timeout
        cookiefile = str(getattr(config, "REMOTE_MEDIA_YTDLP_COOKIEFILE", "") or "").strip()
        if cookiefile:
            options["cookiefile"] = self._validated_ytdlp_cookiefile(cookiefile)
        return options

    def _validated_ytdlp_cookiefile(self, value: str) -> str:
        cookie_path = Path(str(value or "").strip()).expanduser()
        try:
            if not cookie_path.is_file() or cookie_path.stat().st_size > 2 * 1024 * 1024:
                raise AttachmentMaterializationError("remote_media_cookie_unavailable")
            lines = cookie_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except AttachmentMaterializationError:
            raise
        except OSError as exc:
            raise AttachmentMaterializationError("remote_media_cookie_unavailable") from exc

        for line in lines:
            normalized = line.strip()
            if not normalized or (normalized.startswith("#") and not normalized.startswith("#HttpOnly_")):
                continue
            if normalized.startswith("#HttpOnly_"):
                normalized = normalized[len("#HttpOnly_") :]
            domain = normalized.split("\t", 1)[0].strip().lower().lstrip(".").rstrip(".")
            if not domain:
                continue
            if not is_ytdlp_provider_url(f"https://{domain}/"):
                raise AttachmentMaterializationError("remote_media_cookie_domain_forbidden")
        return str(cookie_path)

    def _locate_downloaded_remote_media_file(self, *, target_dir: Path, handle: str) -> Path | None:
        ignored_suffixes = {
            ".part",
            ".ytdl",
            ".json",
            ".description",
            ".vtt",
            ".srt",
            ".ass",
            ".lrc",
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
        }
        candidates: list[Path] = []
        for candidate in target_dir.glob(f"{handle}.*"):
            if not candidate.is_file():
                continue
            if candidate.suffix.lower() in ignored_suffixes:
                continue
            candidates.append(candidate)
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_size)
