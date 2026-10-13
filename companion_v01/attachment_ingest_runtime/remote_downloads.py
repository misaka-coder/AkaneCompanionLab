"""Remote media download routing and temporary Bilibili segment assembly."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any
import config
from .definitions import (
    AttachmentMaterializationError,
    REMOTE_MEDIA_DEFAULT_MAX_BYTES,
    REMOTE_MEDIA_DEFAULT_TIMEOUT,
    RemoteMediaDescriptor,
)


class AttachmentRemoteDownloadsMixin:
    """Methods use the state owned by AttachmentIngestService."""

    def _download_bilibili_media(
        self,
        *,
        descriptor: RemoteMediaDescriptor,
        target_path: Path,
        timeout: float,
        max_bytes: int,
    ) -> None:
        media_urls = tuple(url for url in descriptor.media_urls if str(url or "").strip())
        if not media_urls:
            raise AttachmentMaterializationError("remote_media_bilibili_stream_missing")
        if max_bytes > 0 and descriptor.file_size_hint > max_bytes:
            raise AttachmentMaterializationError("attachment_too_large")
        headers = self._bilibili_request_headers(referer=descriptor.webpage_url)
        if len(media_urls) == 1:
            self._download_to_path(
                url=media_urls[0],
                target_path=target_path,
                timeout=timeout,
                max_bytes=max_bytes,
                headers=headers,
            )
            return

        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            raise AttachmentMaterializationError("remote_media_multi_segment_unsupported")
        segment_paths: list[Path] = []
        concat_path = target_path.with_name(f".{target_path.stem}.segments.txt")
        building_path = target_path.with_name(f".{target_path.stem}.building{target_path.suffix}")
        try:
            total_size = 0
            for index, media_url in enumerate(media_urls, start=1):
                segment_path = target_path.with_name(
                    f".{target_path.stem}.segment-{index:03d}{target_path.suffix}"
                )
                self._download_to_path(
                    url=media_url,
                    target_path=segment_path,
                    timeout=timeout,
                    max_bytes=max_bytes,
                    headers=headers,
                )
                segment_paths.append(segment_path)
                total_size += segment_path.stat().st_size
                if max_bytes > 0 and total_size > max_bytes:
                    raise AttachmentMaterializationError("attachment_too_large")
            concat_lines = [
                "file '" + str(path.resolve()).replace("\\", "/").replace("'", "'\\''") + "'"
                for path in segment_paths
            ]
            concat_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
            completed = subprocess.run(
                [
                    ffmpeg_path,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_path),
                    "-c",
                    "copy",
                    str(building_path),
                ],
                capture_output=True,
                text=True,
                timeout=max(60.0, timeout),
                check=False,
            )
            if completed.returncode != 0 or not building_path.is_file() or building_path.stat().st_size <= 0:
                raise AttachmentMaterializationError("remote_media_download_failed")
            building_path.replace(target_path)
        finally:
            concat_path.unlink(missing_ok=True)
            building_path.unlink(missing_ok=True)
            for path in segment_paths:
                path.unlink(missing_ok=True)

    def _download_remote_media(
        self,
        *,
        item: dict[str, Any],
        descriptor: RemoteMediaDescriptor,
    ) -> Path:
        target_dir = self._workspace_date_dir(item)
        target_dir.mkdir(parents=True, exist_ok=True)
        handle = self._safe_path_part(item.get("attachment_handle") or item.get("attachment_id"))
        timeout = float(
            getattr(config, "REMOTE_MEDIA_DOWNLOAD_TIMEOUT", REMOTE_MEDIA_DEFAULT_TIMEOUT)
            or REMOTE_MEDIA_DEFAULT_TIMEOUT
        )
        max_bytes = int(
            getattr(config, "REMOTE_MEDIA_MAX_BYTES", REMOTE_MEDIA_DEFAULT_MAX_BYTES) or REMOTE_MEDIA_DEFAULT_MAX_BYTES
        )

        if descriptor.download_mode in {"direct", "bilibili_api"}:
            suffix = f".{descriptor.ext.lstrip('.')}" if descriptor.ext else ".bin"
            target_path = self._available_attachment_path(
                target_dir=target_dir,
                handle=handle,
                origin_name=descriptor.origin_name,
                suffix=suffix,
            )
            if descriptor.download_mode == "bilibili_api":
                self._download_bilibili_media(
                    descriptor=descriptor,
                    target_path=target_path,
                    timeout=timeout,
                    max_bytes=max_bytes,
                )
            else:
                self._download_to_path(
                    url=descriptor.source_url,
                    target_path=target_path,
                    timeout=timeout,
                    max_bytes=max_bytes,
                    headers={
                        "User-Agent": "Mozilla/5.0 AkaneCompanionLab/1.0",
                        "Accept": "*/*",
                    },
                )
            return target_path

        download_stem = self._available_attachment_stem(
            target_dir=target_dir,
            handle=handle,
            origin_name=descriptor.origin_name,
        )
        return self._download_remote_media_with_yt_dlp(
            descriptor=descriptor,
            target_dir=target_dir,
            handle=download_stem,
            timeout=timeout,
            max_bytes=max_bytes,
        )
