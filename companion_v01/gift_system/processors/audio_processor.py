from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

from .base import BaseGiftProcessor


AUDIO_GIFT_EXTENSIONS = {".mp3", ".ogg", ".wav", ".m4a", ".flac"}
MAX_AUDIO_GIFT_SIZE_BYTES = 20 * 1024 * 1024


class AudioGiftProcessor(BaseGiftProcessor):
    asset_type = "audio"

    def supports_upload(self, *, filename: str, content_type: str) -> bool:
        suffix = Path(str(filename or "").strip()).suffix.lower()
        return suffix in AUDIO_GIFT_EXTENSIONS

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
        normalized_name = self._normalize_filename(filename)
        suffix = Path(normalized_name).suffix.lower()
        if suffix not in AUDIO_GIFT_EXTENSIONS:
            raise ValueError("当前只支持 mp3 / ogg / wav / m4a / flac 音频礼物。")

        payload = bytes(content or b"")
        if not payload:
            raise ValueError("上传的音频内容为空。")
        if len(payload) > MAX_AUDIO_GIFT_SIZE_BYTES:
            raise ValueError("这份音频礼物有点太大了，先控制在 20MB 以内吧。")

        asset_id = f"gift_{uuid.uuid4().hex}"
        profile_bucket = self._profile_bucket(profile_user_id)
        relative_path = Path(profile_bucket) / "bgm" / f"{asset_id}{suffix}"
        absolute_path = self.base_dir / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_bytes(payload)

        mime_type = str(content_type or "").strip()
        display_name = self._build_display_name(normalized_name)
        return {
            "asset_id": asset_id,
            "resource_id": f"gift_bgm_{uuid.uuid4().hex[:12]}",
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "display_name": display_name,
            "asset_type": self.asset_type,
            "origin_event_type": "upload",
            "media_kind": "bgm",
            "origin_name": normalized_name,
            "mime_type": mime_type,
            "file_ext": suffix,
            "file_size": len(payload),
            "storage_relpath": relative_path.as_posix(),
            "status": "pending",
            "timestamp": now_ts,
            "payload": {
                "filename": normalized_name,
                "mime_type": mime_type,
                "file_ext": suffix,
                "file_size": len(payload),
                "storage_relpath": relative_path.as_posix(),
            },
        }

    def build_short_label(self, asset: dict[str, Any]) -> str:
        display_name = str(asset.get("display_name") or "未命名礼物").strip() or "未命名礼物"
        return f"音乐: {display_name}"

    def build_runtime_projection(
        self,
        asset: dict[str, Any],
        *,
        public_asset_url: str,
    ) -> dict[str, Any]:
        display_name = str(asset.get("display_name") or "未命名礼物").strip() or "未命名礼物"
        origin_name = str(asset.get("origin_name") or "").strip()
        if not public_asset_url:
            return {}
        return {
            "bgm_track": {
                "id": str(asset.get("resource_id") or ""),
                "name": display_name,
                "description": "主人送给你的私人收藏曲目。",
                "path": public_asset_url,
                "aliases": [origin_name] if origin_name and origin_name != display_name else [],
                "tags": ["礼物", "私人收藏", "可全局使用"],
                "ai_hint": (
                    f"这是主人送给你的私人收藏歌曲《{display_name}》，"
                    "可以在任意场景下当作 BGM 使用。"
                ),
                "_meta": {"gift_track": True},
                "_fallback_priority": 180,
            }
        }

    def _profile_bucket(self, profile_user_id: str) -> str:
        digest = hashlib.sha1(str(profile_user_id or "").encode("utf-8")).hexdigest()
        return digest[:16]

    def _normalize_filename(self, filename: str) -> str:
        raw_name = Path(str(filename or "").strip()).name.strip()
        if not raw_name:
            raise ValueError("缺少礼物文件名。")
        safe_chars = []
        for char in raw_name:
            if char in {'"', "'", "<", ">", "|", ":", "*", "?", "\r", "\n", "\t", "\\", "/"}:
                safe_chars.append("_")
            else:
                safe_chars.append(char)
        normalized = "".join(safe_chars).strip(" .")
        if not normalized:
            raise ValueError("礼物文件名不可用。")
        return normalized[:120]

    def _build_display_name(self, filename: str) -> str:
        stem = Path(filename).stem.strip()
        if not stem:
            return "新的礼物歌曲"
        return stem[:80]
