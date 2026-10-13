"""Retry decisions, private locators and validated managed-copy recovery."""

from __future__ import annotations

from ..owner_private_session import private_asset_scope

import time
from pathlib import Path
from typing import Any


class AttachmentRetryMixin:
    """Methods use the state owned by AttachmentIngestService."""

    @private_asset_scope
    def retry_attachment(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str = "latest",
        kind: str = "any",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        item = self.attachment_service.resolve_attachment(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
            kind=kind,
        )
        if item is None:
            return {
                "ok": False,
                "status": "not_found",
                "item": None,
                "followup_context": "你刚刚想重试某个附件，但当前没有找到明确匹配的临时图片或文件。请自然向用户确认是哪一个。",
            }

        current_status = str(item.get("status") or "").strip()
        if current_status == "ready":
            return {
                "ok": True,
                "status": "already_ready",
                "item": item,
                "followup_context": (
                    "这个附件其实已经处理成功了，可以直接查看。请自然告诉用户不用再重试，并基于已有附件内容继续回应。"
                ),
            }
        if current_status == "pending_observation":
            return {
                "ok": True,
                "status": "already_pending",
                "item": item,
                "followup_context": (
                    "这个附件仍在处理中，还不需要重复重试。请自然告诉用户稍等一下，不要再次调用 retry_attachment。"
                ),
            }

        previous_error = str(item.get("error_message") or "").strip()
        retry_item = self.store.update_attachment_inbox_item(
            profile_user_id=profile_user_id,
            session_id=session_id,
            attachment_id=str(item.get("attachment_id") or ""),
            status="pending_observation",
            short_hint="正在重新读取这个附件。",
            error_message="",
            detail=self._merge_item_detail(
                item,
                {
                    "retry": {
                        "previous_status": current_status,
                        "previous_error": previous_error[:500],
                        "retried_at": effective_ts,
                    }
                },
            ),
            updated_at=effective_ts,
        ) or dict(item, status="pending_observation", error_message="", updated_at=effective_ts)

        payload, retry_source = self._build_retry_payload(retry_item, previous_error=previous_error)
        trusted_local_source = self._resolve_managed_retry_source(retry_item)
        if trusted_local_source is not None:
            retry_source = "managed_copy"
        self.background_tasks.submit(
            lane="attachment",
            name=f"retry_attachment:{retry_item.get('attachment_handle') or retry_item.get('attachment_id')}",
            fn=self._process_qq_attachment,
            args=(retry_item, payload, effective_ts),
            kwargs={"trusted_local_source": trusted_local_source} if trusted_local_source is not None else None,
        )
        return {
            "ok": True,
            "status": "retry_started",
            "item": retry_item,
            "retry_source": retry_source,
            "locator_available": retry_source == "private_locator",
            "followup_context": (
                f"你刚刚已经开始重新处理工作台材料 {retry_item.get('attachment_handle') or ''}。"
                "请自然告诉用户你在重新试一次；结果会在处理完成后进入当前材料工作台。"
                "不要立刻断言已经成功，也不要重复调用 retry_attachment。"
            ),
        }

    def _build_retry_payload(
        self,
        item: dict[str, Any],
        *,
        previous_error: str = "",
    ) -> tuple[dict[str, Any], str]:
        del previous_error
        origin_name = self._clean_filename(item.get("origin_name") or "")
        payload = {
            "kind": str(item.get("kind") or "file").strip(),
            "origin_name": origin_name,
            "name": origin_name,
            "file": origin_name,
            "mime_type": str(item.get("mime_type") or "").strip(),
            "file_ext": str(item.get("file_ext") or "").strip(),
            "file_size": self._safe_int(item.get("file_size")),
        }
        retry_source = "onebot_name_fallback" if origin_name else "unavailable"
        storage_relpath = str(item.get("storage_relpath") or "").strip()
        if storage_relpath.lower().startswith("workspace:") and self.workspace_uri_resolver is not None:
            candidate = self.workspace_uri_resolver(storage_relpath)
            if candidate is not None and candidate.exists() and candidate.is_file():
                payload["workspace_uri"] = storage_relpath
                return payload, "managed_copy"

        private_source = self.store.get_attachment_private_source(
            profile_user_id=str(item.get("profile_user_id") or ""),
            session_id=str(item.get("session_id") or ""),
            attachment_id=str(item.get("attachment_id") or ""),
        )
        locator = private_source.get("locator") if isinstance(private_source, dict) else None
        if isinstance(locator, dict) and locator:
            file_token = self._safe_onebot_file_token(locator.get("file_token"), allow_path_shape=True)
            if file_token:
                payload["file"] = file_token
                payload["file_id"] = file_token
            url = self._safe_private_source_url(locator.get("url"))
            if url:
                payload["url"] = url
            if file_token or url:
                retry_source = "private_locator"
                payload["_private_source_registered"] = True
        return payload, retry_source

    def _resolve_managed_retry_source(self, item: dict[str, Any]) -> Path | None:
        storage_relpath = str(item.get("storage_relpath") or "").strip()
        if not storage_relpath or storage_relpath.lower().startswith("workspace:"):
            return None
        for storage_root in [self.base_dir, *self.legacy_base_dirs]:
            try:
                root = storage_root.resolve(strict=True)
                candidate = (root / Path(storage_relpath)).resolve(strict=True)
                candidate.relative_to(root)
            except (OSError, ValueError):
                continue
            if candidate.is_file():
                return candidate
        return None
