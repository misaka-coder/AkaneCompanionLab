"""Trusted attachment materialization and host-private QQ retrieval sources."""

from __future__ import annotations

import base64
import binascii
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import config
from .definitions import (
    AttachmentMaterializationError,
    QQ_ATTACHMENT_DEFAULT_MAX_BYTES,
)


logger = logging.getLogger("akane.attachment_ingest")


class AttachmentQQSourcesMixin:
    """Methods use the state owned by AttachmentIngestService."""

    def _sanitize_qq_materialization_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        sanitized = dict(payload)
        for key in ("path", "local_path", "workspace_uri", "storage_relpath", "trusted_local_source"):
            sanitized.pop(key, None)
        return sanitized

    def _private_qq_source_locator(self, payload: dict[str, Any]) -> dict[str, str]:
        """Extract only opaque retrieval capabilities for host-private retry storage."""

        locator: dict[str, str] = {}
        file_token = self._safe_onebot_file_token(
            payload.get("file") or payload.get("file_id"),
            allow_path_shape=True,
        )
        if file_token:
            locator["file_token"] = file_token
        url = self._safe_private_source_url(payload.get("url"))
        if url:
            locator["url"] = url
        return locator

    def _safe_private_source_url(self, value: Any) -> str:
        url = str(value or "").strip()
        if not url or len(url) > 4096 or any(ord(char) < 32 for char in url):
            return ""
        parsed = urlparse(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return ""
        if parsed.username is not None or parsed.password is not None:
            return ""
        return url

    def _mark_private_source_attempt(
        self,
        *,
        item: dict[str, Any],
        payload: dict[str, Any],
        status: str,
        timestamp: int,
        reason: str = "",
        clear_locator: bool = False,
    ) -> None:
        if not bool(payload.get("_private_source_registered")):
            return
        try:
            self.store.mark_attachment_private_source_attempt(
                profile_user_id=str(item.get("profile_user_id") or ""),
                session_id=str(item.get("session_id") or ""),
                attachment_id=str(item.get("attachment_id") or ""),
                route=str(payload.get("_materialization_route") or "unavailable"),
                status=status,
                reason=reason,
                clear_locator=clear_locator,
                timestamp=timestamp,
            )
        except Exception as exc:
            logger.warning(
                "attachment private source attempt update failed: attachment_id=%s status=%s error_type=%s",
                str(item.get("attachment_id") or ""),
                str(status or ""),
                type(exc).__name__,
            )

    def _materialize_attachment_file(
        self,
        *,
        item: dict[str, Any],
        payload: dict[str, Any],
        trusted_local_source: Path | None = None,
    ) -> Path | None:
        workspace_uri = str(payload.get("workspace_uri") or "").strip()
        if workspace_uri:
            payload["_materialization_route"] = "managed_copy"
            if self.workspace_uri_resolver is None:
                raise RuntimeError("workspace file resolver is unavailable")
            source_path = self.workspace_uri_resolver(workspace_uri)
            if source_path is None or not source_path.exists() or not source_path.is_file():
                raise FileNotFoundError(workspace_uri)
            return source_path
        if self.ensure_storage_ready is not None:
            self.ensure_storage_ready()
        origin_name = self._clean_filename(
            payload.get("origin_name")
            or payload.get("name")
            or payload.get("file")
            or payload.get("filename")
            or item.get("origin_name")
            or ""
        )
        suffix = self._guess_suffix(origin_name=origin_name, payload=payload, kind=str(item.get("kind") or "file"))
        target_dir = self._workspace_date_dir(item)
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            target_dir.resolve().relative_to(self.base_dir.resolve())
        except Exception:
            raise RuntimeError("attachment destination escaped the managed workspace") from None
        handle = self._safe_path_part(item.get("attachment_handle") or item.get("attachment_id"))
        target_path = self._available_attachment_path(
            target_dir=target_dir,
            handle=handle,
            origin_name=origin_name,
            suffix=suffix,
        )
        try:
            target_path.resolve(strict=False).relative_to(self.base_dir.resolve())
        except Exception:
            raise RuntimeError("attachment destination escaped the managed workspace") from None

        if trusted_local_source is not None:
            payload["_materialization_route"] = "managed_copy"
            source = Path(trusted_local_source)
            if not source.exists() or not source.is_file():
                raise AttachmentMaterializationError("attachment_trusted_local_missing")
            self._copy_trusted_file(source=source, target_path=target_path, enforce_max_bytes=False)
            return target_path

        payload["_materialization_route"] = "onebot"
        onebot_cached = self._copy_from_onebot_cache(
            item=item,
            payload=payload,
            target_path=target_path,
            origin_name=origin_name,
        )
        if onebot_cached is not None:
            return onebot_cached

        url = str(payload.get("url") or "").strip()
        if url:
            payload["_materialization_route"] = "remote_url"
            self._download_to_path(url=url, target_path=target_path)
            return target_path
        return None

    def _copy_from_onebot_cache(
        self,
        *,
        item: dict[str, Any],
        payload: dict[str, Any],
        target_path: Path,
        origin_name: str,
    ) -> Path | None:
        supplied_token = str(payload.get("file") or payload.get("file_id") or "").strip()
        file_token = self._safe_onebot_file_token(supplied_token, allow_path_shape=True)
        if not file_token:
            fallback_name = Path(
                str(payload.get("origin_name") or origin_name or item.get("origin_name") or "").replace("\\", "/")
            ).name
            file_token = self._safe_onebot_file_token(fallback_name)
        if not file_token:
            return None

        kind = self._normalize_kind(item.get("kind") or payload.get("kind"))
        endpoints = ["/get_image"] if kind == "image" else []
        endpoints.append("/get_file")
        timeout = float(getattr(config, "QQ_ATTACHMENT_DOWNLOAD_TIMEOUT", 20.0) or 20.0)
        for endpoint in endpoints:
            action_result = self._onebot_transport.call(endpoint, {"file": file_token}, timeout=timeout)
            if not action_result.ok:
                continue
            data = action_result.data
            encoded_file = data.get("base64")
            if isinstance(encoded_file, str) and encoded_file:
                max_bytes = int(getattr(config, "QQ_ATTACHMENT_MAX_BYTES", QQ_ATTACHMENT_DEFAULT_MAX_BYTES) or 0)
                max_encoded_bytes = ((max_bytes + 2) // 3) * 4 if max_bytes > 0 else 0
                if max_encoded_bytes <= 0 or len(encoded_file) <= max_encoded_bytes:
                    try:
                        decoded_file = base64.b64decode(encoded_file, validate=True)
                    except (ValueError, binascii.Error):
                        decoded_file = b""
                    if decoded_file and (max_bytes <= 0 or len(decoded_file) <= max_bytes):
                        self._write_bytes_atomically(target_path=target_path, payload=decoded_file)
                        return target_path
            for key in ("path", "local_path", "file"):
                cached_path = str(data.get(key) or "").strip()
                if not cached_path:
                    continue
                source = self._resolve_trusted_onebot_cache_path(cached_path)
                if source is None:
                    continue
                self._copy_trusted_file(source=source, target_path=target_path)
                return target_path
        return None

    def _safe_onebot_file_token(self, value: Any, *, allow_path_shape: bool = False) -> str:
        token = str(value or "").strip()
        if not token or len(token) > 512 or any(ord(char) < 32 for char in token):
            return ""
        if not allow_path_shape and ("/" in token or "\\" in token or ":" in token):
            return ""
        if token in {".", ".."}:
            return ""
        return token

    def _resolve_trusted_onebot_cache_path(self, value: Any) -> Path | None:
        raw_path = str(value or "").strip()
        if not raw_path:
            return None
        try:
            source = Path(raw_path).resolve(strict=True)
        except OSError:
            return None
        if not source.is_file():
            return None
        raw_roots = str(getattr(config, "QQ_ONEBOT_CACHE_ROOTS", "") or "")
        for raw_root in re.split(r"[;\r\n]+", raw_roots):
            root_text = raw_root.strip()
            if not root_text:
                continue
            try:
                root_candidate = Path(root_text)
                if not root_candidate.is_absolute():
                    continue
                root = root_candidate.resolve(strict=True)
                if not root.is_dir():
                    continue
                source.relative_to(root)
            except (OSError, ValueError):
                continue
            return source
        return None
