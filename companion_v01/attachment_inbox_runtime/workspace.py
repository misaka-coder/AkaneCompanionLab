"""Workspace for AttachmentInboxService."""

from __future__ import annotations

from ..owner_private_session import private_asset_scope

import time
from typing import Any
from .definitions import WORKSPACE_MAX_TARGETS


class AttachmentWorkspaceMixin:
    @private_asset_scope
    def sync_workspace(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        focus_targets: list[Any] | tuple[Any, ...] | set[Any] | str | None,
        kind: str = "any",
        reason: str = "",
        timestamp: int | None = None,
        max_focus: int = WORKSPACE_MAX_TARGETS,
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        targets = self._normalize_targets(focus_targets)
        resolved: list[dict[str, Any]] = []
        unresolved: list[str] = []
        ambiguous_targets: list[str] = []
        for target in targets:
            resolution = self._resolve_target_result(
                profile_user_id=profile_user_id,
                session_id=session_id,
                target=target,
                kind=kind,
            )
            item = resolution.get("item") if isinstance(resolution, dict) else None
            if item is None:
                ambiguity = self._format_ambiguity_target(
                    target=target,
                    items=list(resolution.get("ambiguous_matches") or []) if isinstance(resolution, dict) else [],
                )
                if ambiguity:
                    ambiguous_targets.append(ambiguity)
                else:
                    unresolved.append(target)
                continue
            item_id = str(item.get("attachment_id") or "").strip()
            if item_id and item_id not in {str(existing.get("attachment_id") or "") for existing in resolved}:
                resolved.append(item)

        target_limit = max(1, min(WORKSPACE_MAX_TARGETS, int(max_focus or WORKSPACE_MAX_TARGETS)))
        overflow = resolved[target_limit:]
        selected = resolved[:target_limit]
        selected_ids = [
            str(item.get("attachment_id") or "") for item in selected if str(item.get("attachment_id") or "")
        ]
        synced = self.store.sync_attachment_workspace_focus(
            profile_user_id=profile_user_id,
            session_id=session_id,
            attachment_ids=selected_ids,
            timestamp=effective_ts,
        )
        return {
            "ok": bool(synced or not targets),
            "focused": synced,
            "unresolved": unresolved,
            "ambiguous_targets": ambiguous_targets,
            "overflow": overflow,
            "followup_context": self._build_workspace_followup(
                focused=synced,
                unresolved=unresolved,
                ambiguous_targets=ambiguous_targets,
                overflow=overflow,
                reason=reason,
            ),
        }

    @private_asset_scope
    def inspect_attachment(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str = "",
        kind: str = "any",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        if str(target or "").strip().lower() == "all":
            activity_context = self.build_activity_prompt_context(
                profile_user_id=profile_user_id,
                session_id=session_id,
                kind=kind,
            )
            return {
                "ok": True,
                "status": "listed",
                "item": None,
                "followup_context": activity_context or "attachment.workspace\n当前会话没有可用材料。",
            }
        resolution = self._resolve_target_result(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
            kind=kind,
        )
        item = resolution.get("item") if isinstance(resolution, dict) else None
        if item is None:
            ambiguity = self._format_ambiguity_target(
                target=str(target or "").strip(),
                items=list(resolution.get("ambiguous_matches") or []) if isinstance(resolution, dict) else [],
            )
            return {
                "ok": False,
                "item": None,
                "ambiguous_targets": [ambiguity] if ambiguity else [],
                "followup_context": (
                    "你刚刚想查看某个附件，但当前没有找到明确匹配的图片或文件。请自然向用户确认是哪一个。"
                    if not ambiguity
                    else (f"你刚刚想查看某个附件，但当前有多个候选，请让用户确认：{ambiguity}。不要自己替用户决定。")
                ),
            }

        touched = (
            self.store.update_attachment_inbox_item(
                profile_user_id=profile_user_id,
                session_id=session_id,
                attachment_id=str(item.get("attachment_id") or ""),
                last_used_at=int(timestamp or time.time()),
                updated_at=int(timestamp or time.time()),
            )
            or item
        )
        touched = self._with_private_source_status(touched)
        return {
            "ok": True,
            "item": touched,
            "followup_context": self._build_inspect_followup(touched),
        }

    def _with_private_source_status(self, item: dict[str, Any]) -> dict[str, Any]:
        decorated = dict(item)
        storage_relpath = str(item.get("storage_relpath") or "").strip()
        managed_copy_available = bool(storage_relpath)
        status_reader = getattr(self.store, "get_attachment_private_source_status", None)
        safe_status: dict[str, Any] = {}
        if callable(status_reader):
            result = status_reader(
                profile_user_id=str(item.get("profile_user_id") or ""),
                session_id=str(item.get("session_id") or ""),
                attachment_id=str(item.get("attachment_id") or ""),
            )
            if isinstance(result, dict):
                safe_status = {
                    key: result.get(key)
                    for key in (
                        "locator_available",
                        "source_kind",
                        "expires_at",
                        "last_attempt_at",
                        "last_attempt_route",
                        "last_attempt_status",
                        "last_attempt_reason",
                    )
                    if key in result
                }
        decorated["source_access"] = {
            "managed_copy_available": managed_copy_available,
            **safe_status,
        }
        return decorated
