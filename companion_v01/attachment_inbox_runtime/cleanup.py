"""Cleanup for AttachmentInboxService."""

from __future__ import annotations

from ..owner_private_session import private_asset_scope

import time
from pathlib import Path
from typing import Any


class AttachmentCleanupMixin:
    @private_asset_scope
    def clear_focus(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str = "current",
        targets: list[Any] | tuple[Any, ...] | set[Any] | str | None = None,
        kind: str = "any",
        reason: str = "",
        delete_storage: bool = False,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        normalized_targets = self._normalize_targets(targets)
        unresolved: list[str] = []
        ambiguous_targets: list[str] = []
        if normalized_targets:
            lowered_targets = {item.lower() for item in normalized_targets}
            if lowered_targets & {"all", "全部", "*", "current", "当前", "工作台"}:
                cleared = self.store.clear_attachment_inbox_items(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    target="current",
                    kind=kind,
                    timestamp=effective_ts,
                )
            elif lowered_targets & {"latest", "最近", "最新"}:
                cleared = self.store.clear_attachment_inbox_items(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    target="latest",
                    kind=kind,
                    timestamp=effective_ts,
                )
            else:
                resolved: list[dict[str, Any]] = []
                seen_ids: set[str] = set()
                for item_target in normalized_targets:
                    resolution = self._resolve_target_result(
                        profile_user_id=profile_user_id,
                        session_id=session_id,
                        target=item_target,
                        kind=kind,
                    )
                    item = resolution.get("item") if isinstance(resolution, dict) else None
                    if item is None:
                        ambiguity = self._format_ambiguity_target(
                            target=item_target,
                            items=list(resolution.get("ambiguous_matches") or [])
                            if isinstance(resolution, dict)
                            else [],
                        )
                        if ambiguity:
                            ambiguous_targets.append(ambiguity)
                        else:
                            unresolved.append(item_target)
                        continue
                    item_id = str(item.get("attachment_id") or "").strip()
                    if item_id and item_id not in seen_ids:
                        seen_ids.add(item_id)
                        resolved.append(item)

                cleared = []
                for item in resolved:
                    updated = self.store.update_attachment_inbox_item(
                        profile_user_id=profile_user_id,
                        session_id=session_id,
                        attachment_id=str(item.get("attachment_id") or ""),
                        status="cleared",
                        updated_at=effective_ts,
                    )
                    cleared.append(updated or dict(item, status="cleared", updated_at=effective_ts))
        else:
            cleared = self.store.clear_attachment_inbox_items(
                profile_user_id=profile_user_id,
                session_id=session_id,
                target=target,
                kind=kind,
                timestamp=effective_ts,
            )
        purged_files: list[str] = []
        purge_failures: list[dict[str, str]] = []
        already_absent_files: list[str] = []
        for item in cleared:
            self._section_cache.discard_attachment(profile_user_id, session_id, str(item.get("attachment_id") or ""))
        if delete_storage:
            purged_files, purge_failures, already_absent_files = self._delete_cleared_storage_files(cleared)
        for item in cleared:
            self.record_material_status(
                item,
                event_type="cleanup",
                timestamp=effective_ts,
                reason=reason,
                delete_storage=delete_storage,
            )
        if not cleared:
            missing = f"没有找到这些目标：{', '.join(unresolved[:5])}。" if unresolved else ""
            return {
                "ok": False,
                "cleared": [],
                "purged_files": [],
                "purge_failures": [],
                "already_absent_files": [],
                "unresolved": unresolved,
                "ambiguous_targets": ambiguous_targets,
                "followup_context": (
                    "你刚刚想移除工作台材料焦点，但没有找到可移除的材料。"
                    + (missing if missing else "")
                    + (
                        "这些目标不够明确，存在多个候选，请让用户确认：" + "；".join(ambiguous_targets[:5]) + "。"
                        if ambiguous_targets
                        else ""
                    )
                    + "请自然继续对话，不要重复调用工具。"
                ),
            }

        names = "、".join(self._display_name(item) for item in cleared[:3])
        if len(cleared) > 3:
            names += f" 等 {len(cleared)} 个"
        missing = f"未找到：{', '.join(unresolved[:5])}。" if unresolved else ""
        suffix = f"原因：{reason}。" if str(reason or "").strip() else ""
        return {
            "ok": True,
            "cleared": cleared,
            "purged_files": purged_files,
            "purge_failures": purge_failures,
            "already_absent_files": already_absent_files,
            "unresolved": unresolved,
            "ambiguous_targets": ambiguous_targets,
            "followup_context": (
                f"你刚刚已经从工作台材料焦点中移除了 {len(cleared)} 个材料"
                f"（{names}）。{missing}"
                + (
                    "这些目标不够明确，存在多个候选，请让用户确认：" + "；".join(ambiguous_targets[:5]) + "。"
                    if ambiguous_targets
                    else ""
                )
                + f"{suffix}这些材料不会继续注入上下文，也不会作为礼物、角色资源或长期记忆保存。"
                + (f"已同时删除 {len(purged_files)} 个附件原始文件。" if purged_files else "")
                + (
                    f"另有 {len(already_absent_files)} 个附件本来就没有可删除的本地副本。"
                    if already_absent_files
                    else ""
                )
                + (
                    f"有 {len(purge_failures)} 个来源文件未能删除；请明确告诉用户只是退出了工作台，"
                    "不要声称这些文件已经从磁盘删除。"
                    if purge_failures
                    else ""
                )
                + "请自然继续回应，不要重复调用工具。"
            ),
        }

    def _delete_cleared_storage_files(
        self,
        items: list[dict[str, Any]],
    ) -> tuple[list[str], list[dict[str, str]], list[str]]:
        purged: list[str] = []
        failures: list[dict[str, str]] = []
        already_absent: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            target = str(
                item.get("attachment_handle")
                or item.get("attachment_id")
                or item.get("summary_title")
                or item.get("origin_name")
                or "attachment"
            ).strip()[:120]
            relpath = str(item.get("storage_relpath") or "").strip()
            if not relpath:
                already_absent.append(target)
                continue
            if relpath.lower().startswith("workspace:"):
                failures.append(
                    {
                        "target": target,
                        "code": "external_source_retained",
                        "reason": "这个材料来自外部工作区；已移出当前工作台，但没有删除外部源文件。",
                    }
                )
                continue
            path = self._resolve_storage_path(item)
            if path is None:
                candidates = self._managed_storage_candidates(item)
                path = next((candidate for candidate in candidates if candidate.exists()), None)
                if path is None and candidates:
                    path = candidates[0]
            if path is None:
                failures.append(
                    {
                        "target": target,
                        "code": "storage_unavailable",
                        "reason": "无法可靠定位这个附件的托管副本；已移出工作台，但没有声称删除文件。",
                    }
                )
                continue
            if not self._is_managed_storage_path(path):
                failures.append(
                    {
                        "target": target,
                        "code": "unmanaged_storage",
                        "reason": "这个文件不在 Akane 管理的附件目录内；已移出工作台，但保留了源文件。",
                    }
                )
                continue
            try:
                if not path.exists():
                    already_absent.append(target)
                elif not path.is_file():
                    failures.append(
                        {
                            "target": target,
                            "code": "storage_not_file",
                            "reason": "托管位置不是普通文件，系统没有执行删除。",
                        }
                    )
                else:
                    path.unlink()
                    purged.append(target)
            except OSError:
                failures.append(
                    {
                        "target": target,
                        "code": "delete_failed",
                        "reason": "删除附件托管副本时发生文件系统错误；材料已退出工作台，但文件可能仍在。",
                    }
                )
        return purged, failures, already_absent

    def _is_managed_storage_path(self, path: Path) -> bool:
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            return False
        for root in [self.base_dir, *self.legacy_base_dirs]:
            if root is None:
                continue
            try:
                resolved.relative_to(Path(root).resolve(strict=False))
                return True
            except (OSError, ValueError):
                continue
        return False
