from __future__ import annotations

from typing import Any

from .repository import GiftRepository
from .processors.base import BaseGiftProcessor


class GiftDecisionService:
    def __init__(
        self,
        *,
        repository: GiftRepository,
        processors: dict[str, BaseGiftProcessor],
    ) -> None:
        self.repository = repository
        self.processors = dict(processors)

    def build_pending_prompt_context(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        limit: int = 3,
    ) -> str:
        focused = self._resolve_focus_for_prompt(
            profile_user_id=profile_user_id,
            session_id=session_id,
        )
        pending_items = self.list_inventory(
            profile_user_id=profile_user_id,
            session_id=session_id,
            scope="pending_recent",
            limit=limit,
        )
        items = pending_items["items"]
        if not items:
            return ""

        lines = ["礼物状态补充："]
        if focused is not None:
            lines.append("当前你们正在讨论的礼物：")
            lines.append(
                self._format_prompt_card(
                    focused,
                    include_actions=True,
                )
            )
        lines.append("最近在手边的礼物：")
        for item in items:
            lines.append(self._format_inventory_item(item))
        overflow = int(pending_items.get("overflow_count") or 0)
        if overflow > 0:
            lines.append(f"除此之外，旁边的盒子里还有 {overflow} 件较早的未处理礼物。")
        lines.append(
            "除非用户主动提起或你确实需要处理它们，否则不用逐个展开；"
            "如果需要查看完整清单，请调用 check_inventory 工具。"
        )
        lines.append(
            "如果用户明确表示要留下、吃掉、先放着、不要了、只看看，或要从自己的收藏里移出/彻底删除，"
            "并且礼物对象明确，请调用 manage_gift 工具执行；"
            "如果礼物对象不明确，就先追问，不要擅自处理。"
        )
        lines.append(
            "如果用户和你已经明确讨论好某张图片/资产的正式名字、集合或用途"
            "（例如新场景、新衣服、新立绘、表情图），请使用 manage_artifact 工具落库；"
            "如果只是想看一眼或普通收下，不要使用 manage_artifact。"
            "整理时注意：场景/相册用场景或相册集合；服装/表情/形象用服装或形象集合，"
            "不要把表情图塞进看起来像纯场景的集合。"
        )
        return "\n".join(lines)

    def list_inventory(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        scope: str = "pending_recent",
        limit: int = 5,
    ) -> dict[str, Any]:
        normalized_scope = str(scope or "pending_recent").strip().lower() or "pending_recent"
        normalized_limit = max(1, min(20, int(limit or 5)))
        records: list[dict[str, Any]]
        total_count = 0
        overflow_count = 0

        if normalized_scope == "pending_recent":
            records = self._list_pending_window(
                profile_user_id=profile_user_id,
                session_id=session_id,
                limit=normalized_limit,
            )
            total_count = self.repository.count_assets(
                profile_user_id=profile_user_id,
                statuses=["pending"],
            )
            overflow_count = max(0, total_count - len(records))
        else:
            statuses = {
                "pending_all": ["pending"],
                "kept": ["kept"],
                "internalized": ["internalized"],
            }.get(normalized_scope, ["pending"])
            records = self.repository.list_assets(
                profile_user_id=profile_user_id,
                status=statuses[0] if len(statuses) == 1 else None,
                statuses=statuses,
                limit=normalized_limit,
            )
            total_count = self.repository.count_assets(
                profile_user_id=profile_user_id,
                statuses=statuses,
            )
            overflow_count = max(0, total_count - len(records))

        items = [self._build_inventory_item(record) for record in records]
        return {
            "scope": normalized_scope,
            "items": items,
            "total_count": total_count,
            "overflow_count": overflow_count,
        }

    def _list_pending_window(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        current_session_records = self.repository.list_assets(
            profile_user_id=profile_user_id,
            session_id=session_id,
            status="pending",
            limit=limit,
        )
        if len(current_session_records) >= limit:
            return current_session_records[:limit]

        seen_asset_ids = {str(record.get("asset_id") or "") for record in current_session_records}
        profile_records = self.repository.list_assets(
            profile_user_id=profile_user_id,
            status="pending",
            limit=max(limit * 4, limit),
        )
        merged = list(current_session_records)
        for record in profile_records:
            asset_id = str(record.get("asset_id") or "")
            if asset_id in seen_asset_ids:
                continue
            merged.append(record)
            seen_asset_ids.add(asset_id)
            if len(merged) >= limit:
                break
        return merged[:limit]

    def _build_inventory_item(self, record: dict[str, Any]) -> dict[str, Any]:
        processor = self.processors.get(str(record.get("asset_type") or ""))
        if processor is None:
            summary = f"礼物: {str(record.get('display_name') or '未命名礼物').strip() or '未命名礼物'}"
            return {
                "asset_id": str(record.get("asset_id") or ""),
                "asset_type": str(record.get("asset_type") or ""),
                "display_name": str(record.get("display_name") or ""),
                "summary": summary,
                "status": str(record.get("status") or "pending"),
            }
        return processor.build_prompt_card(record)

    def _resolve_focus_for_prompt(
        self,
        *,
        profile_user_id: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        session = self.repository.store.get_session(profile_user_id, session_id)
        focus_asset_id = str((session or {}).get("current_gift_focus_asset_id") or "").strip()
        if not focus_asset_id:
            return None
        asset = self.repository.get_asset(
            profile_user_id=profile_user_id,
            asset_id=focus_asset_id,
        )
        if asset is None or str(asset.get("session_id") or "") != str(session_id):
            return None
        processor = self.processors.get(str(asset.get("asset_type") or ""))
        if processor is None:
            return None
        return {
            "asset_id": str(asset.get("asset_id") or ""),
            "display_name": str(asset.get("display_name") or ""),
            "asset_type": str(asset.get("asset_type") or ""),
            "status": str(asset.get("status") or "pending"),
            "summary": processor.build_short_label(asset),
            "allowed_actions": processor.allowed_actions(asset),
        }

    def _format_inventory_item(self, item: dict[str, Any]) -> str:
        return (
            f"- asset_id={str(item.get('asset_id') or '')} | "
            f"{str(item.get('summary') or '未命名礼物')} | "
            f"status={str(item.get('status') or 'pending')}"
        )

    def _format_prompt_card(self, item: dict[str, Any], *, include_actions: bool) -> str:
        line = (
            f"- asset_id={str(item.get('asset_id') or '')} | "
            f"{str(item.get('summary') or '未命名礼物')} | "
            f"status={str(item.get('status') or 'pending')}"
        )
        if include_actions:
            actions = [
                str(action).strip()
                for action in list(item.get("allowed_actions") or [])
                if str(action).strip() and str(action).strip() in {"observe", "keep", "internalize", "defer", "reject", "remove", "purge"}
            ]
            if actions:
                line += f" | allowed_actions={','.join(actions)}"
        return line
