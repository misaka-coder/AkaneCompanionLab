"""Browser download interception, storage tracking, and Attachment Inbox integration."""
from __future__ import annotations

import logging
import mimetypes
import os
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("akane.browser_download")

DEFAULT_MAX_DOWNLOAD_BYTES = 0  # 0 means unlimited (bounded only by physical available disk space)
DEFAULT_DOWNLOAD_TTL_SECONDS = 3600.0  # 1 hour


@dataclass
class BrowserDownloadRecord:
    download_id: str
    profile_user_id: str = "default_user"
    session_id: str = "default_session"
    url: str = ""
    suggested_filename: str = ""
    status: str = "pending"  # pending | completed | failed | cancelled
    file_size: int = 0
    saved_path: str = ""
    storage_relpath: str = ""
    attachment_id: str = ""
    attachment_handle: str = ""
    error_reason: str = ""
    started_at: float = field(default_factory=time.time)
    completed_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "download_id": self.download_id,
            "profile_user_id": self.profile_user_id,
            "session_id": self.session_id,
            "url": self.url,
            "suggested_filename": self.suggested_filename,
            "status": self.status,
            "file_size": self.file_size,
            "saved_path": self.saved_path,
            "storage_relpath": self.storage_relpath,
            "attachment_id": self.attachment_id,
            "attachment_handle": self.attachment_handle,
            "error_reason": self.error_reason,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


def sanitize_download_filename(filename: str) -> str:
    """Sanitize suggested filename to prevent directory traversal and illegal characters."""
    raw = str(filename or "").strip()
    # Strip any directory traversal elements
    raw = os.path.basename(raw.replace("\\", "/"))
    # Remove illegal characters for Windows and Unix filenames
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw).strip(". ")
    if not cleaned:
        cleaned = f"download_{uuid.uuid4().hex[:8]}.bin"
    return cleaned[:120]


class BrowserDownloadTracker:
    """Thread-safe tracker for downloads in a managed browser session."""

    def __init__(
        self,
        *,
        attachment_service: Any = None,
        download_dir: Path | str | None = None,
        max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
        ttl_seconds: float = DEFAULT_DOWNLOAD_TTL_SECONDS,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.attachment_service = attachment_service
        self.download_dir = Path(download_dir).resolve() if download_dir else None
        self.max_bytes = max(0, int(max_bytes))
        self.ttl_seconds = max(60.0, float(ttl_seconds))
        self._now = now or time.time
        self._lock = threading.RLock()
        self._downloads: dict[str, BrowserDownloadRecord] = {}
        self._latest_id: str = ""
        self._active_playwright_downloads: dict[str, Any] = {}

    def set_attachment_service(self, service: Any) -> None:
        with self._lock:
            self.attachment_service = service

    def handle_playwright_download(
        self,
        download: Any,
        *,
        profile_user_id: str = "default_user",
        session_id: str = "default_session",
    ) -> BrowserDownloadRecord:
        """Handle a Playwright download event synchronously on the Playwright thread."""
        download_id = f"dl_{uuid.uuid4().hex[:12]}"
        raw_url = str(getattr(download, "url", "") or "")
        suggested = sanitize_download_filename(getattr(download, "suggested_filename", "") or "")

        record = BrowserDownloadRecord(
            download_id=download_id,
            profile_user_id=str(profile_user_id or "default_user").strip() or "default_user",
            session_id=str(session_id or "default_session").strip() or "default_session",
            url=raw_url,
            suggested_filename=suggested,
            status="pending",
            started_at=float(self._now()),
        )

        with self._lock:
            self._downloads[download_id] = record
            self._latest_id = download_id
            self._active_playwright_downloads[download_id] = download
            self._evict_expired_locked()

        temp_path: Path | None = None
        try:
            attachment_svc = self.attachment_service
            if attachment_svc is not None and getattr(attachment_svc, "base_dir", None):
                target_base = Path(attachment_svc.base_dir) / "downloads"
            else:
                target_base = self.download_dir or (Path(tempfile.gettempdir()) / "akane_downloads")
            target_base.mkdir(parents=True, exist_ok=True)
            # Stage on the destination filesystem so publication is atomic even
            # when Windows TEMP and the managed workspace use different drives.
            with tempfile.NamedTemporaryFile(dir=target_base, prefix=".download_", suffix=".part", delete=False) as tmp:
                temp_path = Path(tmp.name)
            download.save_as(str(temp_path))
            if not temp_path.exists():
                raise RuntimeError("downloaded_file_not_found_on_disk")

            file_size = temp_path.stat().st_size
            if self.max_bytes > 0 and file_size > self.max_bytes:
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass
                raise ValueError(f"file_too_large: size {file_size} exceeds configured limit {self.max_bytes}")

            # Destination directory determination
            final_target: Path | None = None
            storage_relpath = ""

            attachment_svc = self.attachment_service
            if attachment_svc is not None and getattr(attachment_svc, "base_dir", None):
                target_base = Path(attachment_svc.base_dir) / "downloads"
                target_base.mkdir(parents=True, exist_ok=True)
                unique_name = f"{int(self._now())}_{uuid.uuid4().hex[:6]}_{suggested}"
                final_target = target_base / unique_name
                # Atomic replace
                os.replace(temp_path, final_target)
                storage_relpath = f"downloads/{unique_name}"
                # Preserve the saved file identity if inbox registration fails;
                # a caller can distinguish a saved file from a usable handle.
                record.file_size = file_size
                record.saved_path = str(final_target)
                record.storage_relpath = storage_relpath

                attachment_id = ""
                attachment_handle = ""
                try:
                    mime_type = mimetypes.guess_type(suggested)[0] or "application/octet-stream"
                    if hasattr(attachment_svc, "create_pending") and hasattr(attachment_svc, "mark_ready"):
                        item = attachment_svc.create_pending(
                            profile_user_id=profile_user_id,
                            session_id=session_id,
                            source="browser_download",
                            kind="file",
                            origin_name=suggested,
                            mime_type=mime_type,
                            file_ext=final_target.suffix.lower(),
                            file_size=file_size,
                            storage_relpath=storage_relpath,
                            detail={"download_id": download_id, "url": raw_url},
                        )
                        attachment_id = str(item.get("attachment_id") or "")
                        attachment_handle = str(item.get("attachment_handle") or "")
                        record.attachment_id = attachment_id
                        if not attachment_id:
                            raise RuntimeError("attachment_id_missing")

                        ready_item = attachment_svc.mark_ready(
                            profile_user_id=profile_user_id,
                            session_id=session_id,
                            attachment_id=attachment_id,
                            summary_title=f"网页下载: {suggested}",
                            short_hint=f"浏览器下载文件 ({file_size} 字节)",
                            detail={"download_id": download_id, "url": raw_url},
                        )
                        if not isinstance(ready_item, dict) or ready_item.get("status") != "ready":
                            raise RuntimeError("attachment_not_ready")
                        attachment_handle = str(ready_item.get("attachment_handle") or attachment_handle)
                        if not attachment_handle:
                            raise RuntimeError("attachment_handle_missing")
                    else:
                        raise RuntimeError("attachment_registration_unavailable")
                except Exception as reg_exc:
                    logger.warning("Failed to register downloaded file in attachment inbox: %s", type(reg_exc).__name__)
                    raise RuntimeError(f"attachment_registration_failed:{type(reg_exc).__name__}") from reg_exc

                with self._lock:
                    record.status = "completed"
                    record.file_size = file_size
                    record.saved_path = str(final_target)
                    record.storage_relpath = storage_relpath
                    record.attachment_id = attachment_id
                    record.attachment_handle = attachment_handle
                    record.completed_at = float(self._now())
            else:
                # Fallback directory
                fallback_dir = self.download_dir or (Path(tempfile.gettempdir()) / "akane_downloads")
                fallback_dir.mkdir(parents=True, exist_ok=True)
                unique_name = f"{int(self._now())}_{uuid.uuid4().hex[:6]}_{suggested}"
                final_target = fallback_dir / unique_name
                os.replace(temp_path, final_target)

                with self._lock:
                    record.status = "completed"
                    record.file_size = file_size
                    record.saved_path = str(final_target)
                    record.storage_relpath = unique_name
                    record.completed_at = float(self._now())

        except Exception as exc:
            err_msg = str(exc)
            if hasattr(download, "failure"):
                try:
                    failure_info = download.failure()
                    if failure_info:
                        err_msg = f"{failure_info}: {err_msg}"
                except Exception:
                    pass
            with self._lock:
                if record.status != "cancelled":
                    is_cancelled = "cancel" in err_msg.lower() or "abort" in err_msg.lower()
                    record.status = "cancelled" if is_cancelled else "failed"
                    record.error_reason = err_msg if not is_cancelled else (record.error_reason or "cancelled_by_user")
                record.completed_at = float(self._now())
            try:
                if temp_path is not None and temp_path.exists():
                    temp_path.unlink(missing_ok=True)
            except Exception:
                pass
        finally:
            with self._lock:
                self._active_playwright_downloads.pop(download_id, None)

        return record

    def get(self, download_id: str) -> BrowserDownloadRecord | None:
        clean_id = str(download_id or "").strip()
        if not clean_id:
            return None
        with self._lock:
            record = self._downloads.get(clean_id)
            if record is None:
                return None
            return record

    def latest(self) -> BrowserDownloadRecord | None:
        with self._lock:
            if not self._latest_id:
                return None
            return self.get(self._latest_id)

    def list_all(self) -> list[BrowserDownloadRecord]:
        with self._lock:
            return list(self._downloads.values())

    def cancel(self, download_id: str) -> bool:
        """Actively cancel an in-flight download task by download_id."""
        clean_id = str(download_id or "").strip()
        if not clean_id:
            return False
        with self._lock:
            rec = self._downloads.get(clean_id)
            dl = self._active_playwright_downloads.pop(clean_id, None)
            if dl is not None:
                try:
                    if hasattr(dl, "cancel"):
                        dl.cancel()
                except Exception:
                    pass
            if rec is not None:
                if rec.status == "pending":
                    rec.status = "cancelled"
                    rec.error_reason = "cancelled_by_user"
                    rec.completed_at = float(self._now())
                    return True
                return rec.status == "cancelled"
            return False

    def cancel_pending_downloads(self) -> None:
        with self._lock:
            for dl_id, dl in list(self._active_playwright_downloads.items()):
                try:
                    if hasattr(dl, "cancel"):
                        dl.cancel()
                except Exception:
                    pass
                rec = self._downloads.get(dl_id)
                if rec and rec.status == "pending":
                    rec.status = "cancelled"
                    rec.error_reason = "browser_closed_or_cancelled"
            self._active_playwright_downloads.clear()

    def clear(self) -> None:
        with self._lock:
            self.cancel_pending_downloads()
            self._downloads.clear()
            self._latest_id = ""

    def _evict_expired_locked(self) -> None:
        now = float(self._now())
        expired = [
            dl_id
            for dl_id, rec in self._downloads.items()
            if rec.status != "pending" and now - rec.started_at > self.ttl_seconds
        ]
        for dl_id in expired:
            del self._downloads[dl_id]
            if self._latest_id == dl_id:
                self._latest_id = ""
