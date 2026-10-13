"""Remote media ingestion, fallback, cleanup and safe result summaries."""

from __future__ import annotations

from ..owner_private_session import private_asset_scope

import mimetypes
import time
from typing import Any
from urllib.parse import urlparse
from ..public_url_policy import public_url_display_origin, public_url_fingerprint
from .definitions import (
    AttachmentMaterializationError,
    RemoteMediaDescriptor,
)


class AttachmentRemoteMediaMixin:
    """Methods use the state owned by AttachmentIngestService."""

    @private_asset_scope
    def fetch_media_from_urls(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        urls: list[str] | tuple[str, ...] | set[str] | str,
        preferred_title: str = "",
        character_pack_id: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        normalized_urls = self._normalize_remote_urls(urls)
        if not normalized_urls:
            return {
                "ok": False,
                "items": [],
                "failed": [],
                "followup_context": (
                    "你刚刚想通过链接获取视频或音频，但这次没有拿到明确可用的 http/https 链接。"
                    "请自然向用户确认具体链接，不要重复调用 fetch_media_from_url。"
                ),
            }

        items: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        single_title = str(preferred_title or "").strip()
        for index, url in enumerate(normalized_urls, start=1):
            title_hint = single_title if len(normalized_urls) == 1 else ""
            item: dict[str, Any] | None = None
            display_origin = public_url_display_origin(url)
            try:
                target = self._validate_public_remote_url(url)
                url = target.url
                display_origin = target.origin
                descriptor = self._fetch_remote_media_descriptor(
                    url=url,
                    preferred_title=title_hint,
                )
                item = self.attachment_service.create_pending(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    source="remote_url",
                    kind=descriptor.kind,
                    origin_name=self._clean_filename(descriptor.origin_name),
                    mime_type=str(descriptor.mime_type or "").strip(),
                    file_ext=f".{descriptor.ext.lstrip('.')}" if descriptor.ext else "",
                    file_size=max(0, int(descriptor.file_size_hint or 0)),
                    source_event_id=public_url_fingerprint(url),
                    source_message_id="",
                    detail=self._material_context_detail(character_pack_id=character_pack_id),
                    timestamp=effective_ts,
                )
                ready_item = self._process_remote_media_download(
                    item=item,
                    descriptor=descriptor,
                    timestamp=effective_ts,
                )
                if ready_item is not None:
                    self._clear_stale_failed_remote_items(
                        ready_item=ready_item,
                        source_url=url,
                        timestamp=effective_ts,
                    )
                    items.append(ready_item)
                    continue
                self._mark_failed(item, "下载完成后没有生成可用的媒体文件。", timestamp=effective_ts)
                failures.append(
                    {
                        "url": display_origin,
                        "error": "下载完成后没有生成可用的媒体文件。",
                        "index_label": f"第{index}个链接",
                    }
                )
            except Exception as exc:
                error_code = self._remote_fetch_error_code(exc)
                error_message = self._humanize_remote_fetch_error(error_code)
                if item is not None:
                    self._mark_failed(item, error_code, timestamp=effective_ts)
                failures.append(
                    {
                        "url": display_origin,
                        "error": error_message,
                        "index_label": f"第{index}个链接",
                    }
                )

        return {
            "ok": bool(items),
            "items": items,
            "failed": failures,
            "followup_context": self._build_remote_media_followup(
                items=items,
                failures=failures,
            ),
        }

    def _clear_stale_failed_remote_items(
        self,
        *,
        ready_item: dict[str, Any],
        source_url: str,
        timestamp: int,
    ) -> None:
        normalized_url = str(source_url or "").strip()
        if not normalized_url:
            return
        matching_source_ids = {normalized_url, public_url_fingerprint(normalized_url)}
        profile_user_id = str(ready_item.get("profile_user_id") or "").strip()
        session_id = str(ready_item.get("session_id") or "").strip()
        ready_id = str(ready_item.get("attachment_id") or "").strip()
        if not profile_user_id or not session_id:
            return

        failed_items = self.store.list_attachment_inbox_items(
            profile_user_id=profile_user_id,
            session_id=session_id,
            statuses=["failed"],
            limit=200,
        )
        for failed in failed_items:
            failed_id = str(failed.get("attachment_id") or "").strip()
            if not failed_id or failed_id == ready_id:
                continue
            if str(failed.get("source") or "").strip() != "remote_url":
                continue
            if str(failed.get("source_event_id") or "").strip() not in matching_source_ids:
                continue
            updated = self.store.update_attachment_inbox_item(
                profile_user_id=profile_user_id,
                session_id=session_id,
                attachment_id=failed_id,
                status="cleared",
                updated_at=timestamp,
            )
            self.attachment_service.record_material_status(
                updated or dict(failed, status="cleared", updated_at=timestamp),
                event_type="cleanup",
                timestamp=timestamp,
                reason="同一远程链接已经重新获取成功，旧失败记录已清理。",
                delete_storage=False,
            )

    def _process_remote_media_download(
        self,
        *,
        item: dict[str, Any],
        descriptor: RemoteMediaDescriptor,
        timestamp: int,
    ) -> dict[str, Any] | None:
        try:
            source_path = self._download_remote_media(
                item=item,
                descriptor=descriptor,
            )
        except AttachmentMaterializationError:
            if descriptor.download_mode != "yt_dlp":
                raise
            fallback_descriptor = self._extract_remote_media_with_bilibili_api(
                url=descriptor.source_url,
                preferred_title=descriptor.title,
            )
            if fallback_descriptor is None:
                raise
            descriptor = fallback_descriptor
            source_path = self._download_remote_media(
                item=item,
                descriptor=descriptor,
            )
        relpath = self._storage_relpath(source_path)
        mime_type = str(descriptor.mime_type or mimetypes.guess_type(str(source_path))[0] or "").strip()
        file_ext = source_path.suffix.lower()
        self.store.update_attachment_inbox_item(
            profile_user_id=str(item.get("profile_user_id") or ""),
            session_id=str(item.get("session_id") or ""),
            attachment_id=str(item.get("attachment_id") or ""),
            storage_relpath=relpath,
            mime_type=mime_type,
            file_ext=file_ext,
            file_size=source_path.stat().st_size if source_path.exists() else 0,
            updated_at=timestamp,
        )

        file_card = self._build_file_card(source_path=source_path, item=item, mime_type=mime_type)
        remote_card = self._enrich_remote_media_card(
            file_card=file_card,
            descriptor=descriptor,
        )
        return self.attachment_service.mark_ready(
            profile_user_id=str(item.get("profile_user_id") or ""),
            session_id=str(item.get("session_id") or ""),
            attachment_id=str(item.get("attachment_id") or ""),
            summary_title=str(remote_card.get("summary_title") or file_card.get("summary_title") or ""),
            short_hint=str(remote_card.get("short_hint") or file_card.get("short_hint") or ""),
            detail=self._merge_item_detail(
                item,
                remote_card.get("detail") if isinstance(remote_card.get("detail"), dict) else {},
            ),
            timestamp=timestamp,
        )

    def _enrich_remote_media_card(
        self,
        *,
        file_card: dict[str, Any],
        descriptor: RemoteMediaDescriptor,
    ) -> dict[str, Any]:
        summary_title = str(file_card.get("summary_title") or descriptor.title or descriptor.origin_name).strip()
        detail = dict(file_card.get("detail") or {}) if isinstance(file_card.get("detail"), dict) else {}
        source_fingerprint = public_url_fingerprint(descriptor.source_url)
        webpage_fingerprint = public_url_fingerprint(descriptor.webpage_url or descriptor.source_url)
        thumbnail_fingerprint = public_url_fingerprint(descriptor.thumbnail_url)
        display_origin = public_url_display_origin(descriptor.webpage_url) or public_url_display_origin(
            descriptor.source_url
        )
        detail["remote_source"] = {
            "platform": self._safe_prompt_label(
                descriptor.extractor or descriptor.extractor_key or urlparse(display_origin).netloc
            ),
            "uploader": self._safe_prompt_label(descriptor.uploader or descriptor.channel),
            "display_origin": display_origin,
            "source_fingerprint": source_fingerprint,
            "webpage_fingerprint": webpage_fingerprint,
            "thumbnail_fingerprint": thumbnail_fingerprint,
        }
        if descriptor.description and not detail.get("description"):
            detail["description"] = descriptor.description
        if descriptor.duration_seconds is not None:
            media_info = detail.get("media_info") if isinstance(detail.get("media_info"), dict) else {}
            if media_info and not media_info.get("duration_seconds"):
                media_info["duration_seconds"] = round(float(descriptor.duration_seconds), 3)
                detail["media_info"] = media_info

        source_bits = []
        platform = str(detail["remote_source"].get("platform") or "").strip()
        uploader = str(detail["remote_source"].get("uploader") or "").strip()
        if platform:
            source_bits.append(f"来源 {platform}")
        if uploader:
            source_bits.append(f"发布者 {uploader}")
        short_hint = str(file_card.get("short_hint") or detail.get("summary") or "").strip()
        if source_bits:
            suffix = "；".join(source_bits)
            short_hint = f"{short_hint}；{suffix}" if short_hint else suffix
        return {
            "summary_title": summary_title,
            "short_hint": short_hint[:320],
            "detail": detail,
        }

    def _build_remote_media_followup(
        self,
        *,
        items: list[dict[str, Any]],
        failures: list[dict[str, str]],
    ) -> str:
        if items and not failures:
            labels = "、".join(
                str(item.get("attachment_handle") or item.get("summary_title") or item.get("origin_name") or "").strip()
                for item in items[:6]
                if str(
                    item.get("attachment_handle") or item.get("summary_title") or item.get("origin_name") or ""
                ).strip()
            )
            return (
                f"你刚刚已经把链接里的媒体素材放进当前材料工作台了：{labels or '已下载媒体'}。"
                "请基于这个既成事实自然回应，不要重复调用 fetch_media_from_url。"
                "如果用户想继续处理内容，请将这些句柄交给当前实际可用的工具。"
            )
        if failures and not items:
            reasons = "；".join(
                f"{entry.get('index_label') or '某个链接'}：{str(entry.get('error') or '').strip()[:120]}"
                for entry in failures[:4]
            )
            install_hint = self._remote_fetch_failure_needs_ytdlp_hint(failures)
            retry_hint = (
                "请自然告诉用户当前媒体获取服务暂时不可用；这是服务端能力问题，不要让用户配置服务器环境或提供 cookies。"
                if install_hint
                else (
                    "请自然告诉用户失败原因；如果像是网络、权限或平台临时问题，可以建议换公开链接或稍后重试。"
                    "不要让用户维护服务器 cookies。"
                )
            )
            return (
                "你刚刚尝试通过链接获取媒体，但这次没有成功。"
                + (f"失败情况：{reasons}。" if reasons else "")
                + retry_hint
            )
        success_labels = "、".join(
            str(item.get("attachment_handle") or item.get("summary_title") or item.get("origin_name") or "").strip()
            for item in items[:6]
            if str(item.get("attachment_handle") or item.get("summary_title") or item.get("origin_name") or "").strip()
        )
        reasons = "；".join(
            f"{entry.get('index_label') or '某个链接'}：{str(entry.get('error') or '').strip()[:120]}"
            for entry in failures[:4]
        )
        return (
            f"你刚刚通过链接成功拿到这些媒体：{success_labels or '部分媒体'}。"
            + (f"同时还有部分失败：{reasons}。" if reasons else "")
            + "请自然向用户说明哪些已经到手、哪些还没成功；不要重复调用 fetch_media_from_url。"
        )
