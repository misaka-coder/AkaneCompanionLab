"""Pinned public HTTP reads, validated redirects and atomic bounded downloads."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
import requests
import config
from ..public_http import get_pinned_public_response
from ..public_url_policy import (
    PublicUrlPolicyError,
    PublicUrlTarget,
    is_ytdlp_provider_url,
    validate_public_http_url,
    validate_response_peer,
)
from .definitions import (
    AttachmentMaterializationError,
    QQ_ATTACHMENT_DEFAULT_MAX_BYTES,
    REMOTE_DOWNLOAD_MAX_REDIRECTS,
    REMOTE_DOWNLOAD_REDIRECT_STATUSES,
    REMOTE_MEDIA_DEFAULT_TIMEOUT,
)


class AttachmentPublicDownloadMixin:
    """Methods use the state owned by AttachmentIngestService."""

    def _resolve_public_redirect_url(self, *, url: str, headers: dict[str, str]) -> str:
        current_target = self._validate_public_remote_url(url)
        timeout = min(
            30.0,
            float(
                getattr(config, "REMOTE_MEDIA_DOWNLOAD_TIMEOUT", REMOTE_MEDIA_DEFAULT_TIMEOUT)
                or REMOTE_MEDIA_DEFAULT_TIMEOUT
            ),
        )
        for redirect_count in range(REMOTE_DOWNLOAD_MAX_REDIRECTS + 1):
            status_code, location = self._request_pinned_redirect(
                target=current_target,
                headers=headers,
                timeout=timeout,
            )
            if status_code not in REMOTE_DOWNLOAD_REDIRECT_STATUSES:
                if status_code < 200 or status_code >= 300:
                    raise AttachmentMaterializationError("remote_media_extract_failed")
                return current_target.url
            if not location:
                raise AttachmentMaterializationError("remote_url_redirect_missing")
            if redirect_count >= REMOTE_DOWNLOAD_MAX_REDIRECTS:
                raise AttachmentMaterializationError("remote_url_redirect_limit")
            next_target = self._validate_public_remote_url(urljoin(current_target.url, location))
            if urlparse(current_target.url).scheme == "https" and urlparse(next_target.url).scheme == "http":
                raise AttachmentMaterializationError("remote_url_https_downgrade")
            if self._parse_bilibili_video_identity(next_target.url):
                return next_target.url
            current_target = next_target
        raise AttachmentMaterializationError("remote_url_redirect_limit")

    def _request_pinned_redirect(
        self,
        *,
        target: PublicUrlTarget,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, str]:
        safe_headers = {
            str(key).strip(): str(value).replace("\r", " ").replace("\n", " ").strip()
            for key, value in headers.items()
            if str(key).strip()
            and str(key).casefold() not in {"host", "connection", "cookie", "authorization", "proxy-authorization"}
        }
        try:
            with requests.Session() as session:
                response = get_pinned_public_response(session, target, timeout=timeout, headers=safe_headers)
                try:
                    validate_response_peer(response, target)
                    return int(response.status_code or 0), str(response.headers.get("Location") or "").strip()
                finally:
                    response.close()
        except PublicUrlPolicyError as exc:
            raise AttachmentMaterializationError(exc.code) from exc
        except requests.Timeout as exc:
            raise AttachmentMaterializationError("attachment_download_timeout") from exc
        except requests.RequestException as exc:
            raise AttachmentMaterializationError("remote_media_extract_failed") from exc

    def _fetch_public_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        max_bytes: int = 2 * 1024 * 1024,
    ) -> dict[str, Any]:
        target = self._validate_public_remote_url(url)
        timeout = min(
            30.0,
            float(
                getattr(config, "REMOTE_MEDIA_DOWNLOAD_TIMEOUT", REMOTE_MEDIA_DEFAULT_TIMEOUT)
                or REMOTE_MEDIA_DEFAULT_TIMEOUT
            ),
        )
        try:
            with requests.Session() as session:
                response = get_pinned_public_response(session, target, timeout=timeout, headers=headers)
                try:
                    validate_response_peer(response, target)
                    if int(response.status_code or 0) < 200 or int(response.status_code or 0) >= 300:
                        raise AttachmentMaterializationError("remote_media_bilibili_api_failed")
                    content_length = self._safe_int(response.headers.get("Content-Length"))
                    if content_length > max_bytes:
                        raise AttachmentMaterializationError("remote_media_bilibili_api_failed")
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > max_bytes:
                            raise AttachmentMaterializationError("remote_media_bilibili_api_failed")
                        chunks.append(chunk)
                finally:
                    response.close()
        except PublicUrlPolicyError as exc:
            raise AttachmentMaterializationError(exc.code) from exc
        except requests.Timeout as exc:
            raise AttachmentMaterializationError("attachment_download_timeout") from exc
        except requests.RequestException as exc:
            raise AttachmentMaterializationError("remote_media_bilibili_api_failed") from exc
        try:
            payload = json.loads(b"".join(chunks).decode("utf-8"))
        except Exception as exc:
            raise AttachmentMaterializationError("remote_media_bilibili_api_failed") from exc
        if not isinstance(payload, dict):
            raise AttachmentMaterializationError("remote_media_bilibili_api_failed")
        return payload

    def _download_to_path(
        self,
        *,
        url: str,
        target_path: Path,
        timeout: float | None = None,
        max_bytes: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        timeout_value = float(
            timeout if timeout is not None else (getattr(config, "QQ_ATTACHMENT_DOWNLOAD_TIMEOUT", 20.0) or 20.0)
        )
        max_bytes_value = int(
            max_bytes
            if max_bytes is not None
            else (
                getattr(config, "QQ_ATTACHMENT_MAX_BYTES", QQ_ATTACHMENT_DEFAULT_MAX_BYTES)
                or QQ_ATTACHMENT_DEFAULT_MAX_BYTES
            )
        )
        request_headers = dict(
            headers
            or {
                "User-Agent": "Mozilla/5.0 AkaneCompanionLab/1.0",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            }
        )
        request_headers = {
            str(key): str(value)
            for key, value in request_headers.items()
            if str(key).casefold() not in {"authorization", "proxy-authorization", "cookie"}
        }

        temp_path = target_path.with_name(f".{target_path.name}.part")
        temp_path.unlink(missing_ok=True)
        deadline = time.monotonic() + max(0.1, timeout_value)
        try:
            current_target = self._validate_public_remote_url(url)
            redirect_count = 0
            with requests.Session() as session:
                while True:
                    if time.monotonic() > deadline:
                        raise AttachmentMaterializationError("attachment_download_timeout")
                    response = get_pinned_public_response(
                        session, current_target, timeout=min(timeout_value, max(0.25, deadline - time.monotonic())),
                        headers=request_headers,
                    )
                    try:
                        validate_response_peer(response, current_target)
                        status_code = int(getattr(response, "status_code", 0) or 0)
                        if status_code in REMOTE_DOWNLOAD_REDIRECT_STATUSES:
                            location = str(getattr(response, "headers", {}).get("Location") or "").strip()
                            if not location:
                                raise AttachmentMaterializationError("remote_url_redirect_missing")
                            if redirect_count >= REMOTE_DOWNLOAD_MAX_REDIRECTS:
                                raise AttachmentMaterializationError("remote_url_redirect_limit")
                            next_target = self._validate_public_remote_url(urljoin(current_target.url, location))
                            if (
                                urlparse(current_target.url).scheme == "https"
                                and urlparse(next_target.url).scheme == "http"
                            ):
                                raise AttachmentMaterializationError("remote_url_https_downgrade")
                            current_target = next_target
                            redirect_count += 1
                            continue

                        if status_code < 200 or status_code >= 300:
                            raise AttachmentMaterializationError("remote_media_download_failed")
                        response.raise_for_status()
                        content_length = self._safe_int(getattr(response, "headers", {}).get("Content-Length"))
                        if max_bytes_value > 0 and content_length > max_bytes_value:
                            raise AttachmentMaterializationError("attachment_too_large")
                        total = 0
                        with temp_path.open("wb") as handle:
                            for chunk in response.iter_content(chunk_size=64 * 1024):
                                if time.monotonic() > deadline:
                                    raise AttachmentMaterializationError("attachment_download_timeout")
                                if not chunk:
                                    continue
                                total += len(chunk)
                                if max_bytes_value > 0 and total > max_bytes_value:
                                    raise AttachmentMaterializationError("attachment_too_large")
                                handle.write(chunk)
                        temp_path.replace(target_path)
                        return
                    finally:
                        response.close()
        except PublicUrlPolicyError as exc:
            raise AttachmentMaterializationError(exc.code) from exc
        except requests.Timeout as exc:
            raise AttachmentMaterializationError("attachment_download_timeout") from exc
        except requests.RequestException as exc:
            raise AttachmentMaterializationError("remote_media_download_failed") from exc
        except AttachmentMaterializationError:
            raise
        except Exception as exc:
            raise AttachmentMaterializationError("remote_media_download_failed") from exc
        finally:
            temp_path.unlink(missing_ok=True)

    def _validate_public_remote_url(self, url: str) -> PublicUrlTarget:
        return validate_public_http_url(url, resolver=self.public_host_resolver)

    def _is_ytdlp_provider_allowed(self, url: str) -> bool:
        return is_ytdlp_provider_url(url)
