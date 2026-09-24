from __future__ import annotations

from pathlib import Path
from typing import Any


class BaseGiftProcessor:
    asset_type: str = ""

    def __init__(self, *, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)

    def supports_upload(self, *, filename: str, content_type: str) -> bool:
        return False

    def ingest_upload(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        filename: str,
        content_type: str,
        content: bytes,
        now_ts: int | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def allowed_actions(self, asset: dict[str, Any]) -> list[str]:
        status = str(asset.get("status") or "").strip().lower()
        if status == "pending":
            return ["keep", "internalize", "reject", "remove", "purge", "defer", "ask_user"]
        if status == "kept":
            return ["internalize", "reject", "remove", "purge", "defer", "ask_user"]
        if status == "internalized":
            return ["remove", "purge"]
        if status == "rejected":
            return ["purge"]
        return []

    def build_prompt_card(self, asset: dict[str, Any]) -> dict[str, Any]:
        return {
            "asset_id": str(asset.get("asset_id") or ""),
            "asset_type": self.asset_type,
            "display_name": str(asset.get("display_name") or "未命名礼物").strip() or "未命名礼物",
            "summary": self.build_short_label(asset),
            "status": str(asset.get("status") or "pending"),
        }

    def build_short_label(self, asset: dict[str, Any]) -> str:
        display_name = str(asset.get("display_name") or "未命名礼物").strip() or "未命名礼物"
        return f"礼物: {display_name}"

    def build_action_payload(self, asset: dict[str, Any], *, action: str) -> dict[str, Any]:
        payload = asset.get("payload")
        return dict(payload) if isinstance(payload, dict) else {}

    def build_runtime_projection(
        self,
        asset: dict[str, Any],
        *,
        public_asset_url: str,
    ) -> dict[str, Any]:
        return {}
