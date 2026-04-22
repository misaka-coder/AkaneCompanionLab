from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

from .base import BaseGiftProcessor


IMAGE_GIFT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_IMAGE_GIFT_SIZE_BYTES = 12 * 1024 * 1024
DEFAULT_COLLECTION_KEY = "memories"
DEFAULT_COLLECTION_NAME = "回忆"
DEFAULT_KEEP_ROLE = "photo"
DEFAULT_INTERNALIZE_ROLE = "scene"
GIFT_GALLERY_MAJOR_ID = "gift_gallery"
GIFT_GALLERY_MAJOR_NAME = "私人收藏"


class ImageGiftProcessor(BaseGiftProcessor):
    asset_type = "image"

    def allowed_actions(self, asset: dict[str, Any]) -> list[str]:
        actions = list(super().allowed_actions(asset))
        status = str(asset.get("status") or "").strip().lower()
        if status == "pending" and "observe" not in actions:
            actions.insert(0, "observe")
        return actions

    def supports_upload(self, *, filename: str, content_type: str) -> bool:
        suffix = Path(str(filename or "").strip()).suffix.lower()
        if suffix in IMAGE_GIFT_EXTENSIONS:
            return True
        normalized_content_type = str(content_type or "").strip().lower()
        return normalized_content_type.startswith("image/")

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
        if suffix not in IMAGE_GIFT_EXTENSIONS:
            raise ValueError("当前只支持 png / jpg / jpeg / webp 图片礼物。")

        payload = bytes(content or b"")
        if not payload:
            raise ValueError("上传的图片内容为空。")
        if len(payload) > MAX_IMAGE_GIFT_SIZE_BYTES:
            raise ValueError("这张图片有点太大了，先控制在 12MB 以内吧。")

        asset_id = f"gift_{uuid.uuid4().hex}"
        profile_bucket = self._profile_bucket(profile_user_id)
        relative_path = Path(profile_bucket) / "images" / f"{asset_id}{suffix}"
        absolute_path = self.base_dir / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_bytes(payload)

        mime_type = str(content_type or "").strip() or self._guess_mime_type(suffix)
        display_name = self._build_display_name(normalized_name)
        collection_key = DEFAULT_COLLECTION_KEY
        collection_name = DEFAULT_COLLECTION_NAME
        return {
            "asset_id": asset_id,
            "resource_id": f"gift_scene_{uuid.uuid4().hex[:12]}",
            "profile_user_id": str(profile_user_id),
            "session_id": str(session_id),
            "display_name": display_name,
            "asset_type": self.asset_type,
            "origin_event_type": "upload",
            "media_kind": "image",
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
                "collection_key": collection_key,
                "collection_name": collection_name,
                "projection_role": DEFAULT_KEEP_ROLE,
                "keep_projection_role": DEFAULT_KEEP_ROLE,
                "internalize_projection_role": DEFAULT_INTERNALIZE_ROLE,
                "display_name_source": "filename",
            },
        }

    def build_short_label(self, asset: dict[str, Any]) -> str:
        display_name = str(asset.get("display_name") or "未命名图片").strip() or "未命名图片"
        payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
        collection_name = str(payload.get("collection_name") or DEFAULT_COLLECTION_NAME).strip() or DEFAULT_COLLECTION_NAME
        parts = [f"图片: {display_name}（归档: {collection_name}）"]
        source_name = str(asset.get("origin_name") or payload.get("filename") or "").strip()
        seed_name = str(payload.get("seed_name") or "").strip()
        seed_collection_name = str(payload.get("seed_collection_name") or "").strip()
        if source_name and source_name != display_name:
            parts.append(f"原始文件名: {source_name}")
        if seed_name and seed_name != display_name:
            seed_text = f"视觉建议名: {seed_name}"
            if seed_collection_name:
                seed_text += f" / 建议集合: {seed_collection_name}"
            parts.append(seed_text)
        return "；".join(parts)

    def build_prompt_card(self, asset: dict[str, Any]) -> dict[str, Any]:
        card = super().build_prompt_card(asset)
        payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
        card["collection_name"] = str(payload.get("collection_name") or DEFAULT_COLLECTION_NAME).strip() or DEFAULT_COLLECTION_NAME
        card["projection_role"] = str(payload.get("projection_role") or DEFAULT_KEEP_ROLE).strip() or DEFAULT_KEEP_ROLE
        card["source_name"] = str(asset.get("origin_name") or payload.get("filename") or "").strip()
        card["seed_name"] = str(payload.get("seed_name") or "").strip()
        card["seed_collection_name"] = str(payload.get("seed_collection_name") or "").strip()
        return card

    def build_action_payload(self, asset: dict[str, Any], *, action: str) -> dict[str, Any]:
        payload = super().build_action_payload(asset, action=action)
        keep_role = str(payload.get("keep_projection_role") or DEFAULT_KEEP_ROLE).strip() or DEFAULT_KEEP_ROLE
        internalize_role = str(payload.get("internalize_projection_role") or DEFAULT_INTERNALIZE_ROLE).strip() or DEFAULT_INTERNALIZE_ROLE
        if action == "keep":
            payload["projection_role"] = keep_role
        elif action == "internalize":
            payload["projection_role"] = internalize_role
        return payload

    def build_runtime_projection(
        self,
        asset: dict[str, Any],
        *,
        public_asset_url: str,
    ) -> dict[str, Any]:
        payload = asset.get("payload") if isinstance(asset.get("payload"), dict) else {}
        projection_role = str(payload.get("projection_role") or DEFAULT_KEEP_ROLE).strip().lower() or DEFAULT_KEEP_ROLE
        if not public_asset_url:
            return {}
        if projection_role == "character":
            return self._build_character_projection(
                asset,
                payload=payload,
                public_asset_url=public_asset_url,
            )
        if projection_role != "scene":
            return {}

        display_name = str(asset.get("display_name") or "未命名图片").strip() or "未命名图片"
        origin_name = str(asset.get("origin_name") or "").strip()
        collection_key = self._normalize_collection_key(payload.get("collection_key"))
        collection_name = str(payload.get("collection_name") or DEFAULT_COLLECTION_NAME).strip() or DEFAULT_COLLECTION_NAME
        vision_summary = str(payload.get("vision_summary") or "").strip()
        background_entry = {
            "id": str(asset.get("resource_id") or asset.get("asset_id") or "").strip() or str(asset.get("asset_id") or ""),
            "name": display_name,
            "description": vision_summary or "主人送给你的私人图片，已经被你收进自己的场景收藏里。",
            "path": public_asset_url,
            "aliases": [origin_name] if origin_name and origin_name != display_name else [],
            "ai_hint": (
                f"这是主人送给你的图片《{display_name}》，"
                "已经被你收进自己的场景收藏里，可以作为场景背景使用。"
            ),
            "_meta": {
                "gift_scene": True,
                "collection_key": collection_key,
                "projection_role": projection_role,
            },
            "_fallback_priority": 180,
        }
        return {
            "scene_group": {
                "id": GIFT_GALLERY_MAJOR_ID,
                "name": GIFT_GALLERY_MAJOR_NAME,
                "description": "主人送给你的、已经被你收进自己世界里的私人场景收藏。",
                "aliases": ["私人场景", "收藏相册"],
                "minors": [
                    {
                        "id": collection_key,
                        "name": collection_name,
                        "description": f"你为自己整理的「{collection_name}」图像收藏。",
                        "aliases": [collection_key] if collection_key != collection_name else [],
                        "backgrounds": [background_entry],
                        "bgm_tracks": [],
                        "_fallback_priority": 180,
                    }
                ],
                "_fallback_priority": 180,
            }
        }

    def _build_character_projection(
        self,
        asset: dict[str, Any],
        *,
        payload: dict[str, Any],
        public_asset_url: str,
    ) -> dict[str, Any]:
        display_name = str(asset.get("display_name") or "未命名图片").strip() or "未命名图片"
        origin_name = str(asset.get("origin_name") or "").strip()
        asset_role = str(payload.get("character_asset_role") or payload.get("asset_role") or "expression").strip().lower()
        outfit_id = str(payload.get("character_outfit_id") or payload.get("collection_key") or "").strip()
        outfit_name = str(payload.get("character_outfit_name") or payload.get("collection_name") or outfit_id).strip()
        emotion_id = str(payload.get("character_emotion_id") or "").strip()
        if not emotion_id:
            emotion_id = "normal" if asset_role in {"outfit", "portrait"} else display_name
        emotion_name = str(payload.get("character_emotion_name") or "").strip()
        if not emotion_name:
            emotion_name = display_name if asset_role == "expression" else "默认"
        if not outfit_id:
            outfit_id = outfit_name or "custom_outfit"
        if not outfit_name:
            outfit_name = outfit_id

        collection_key = str(payload.get("collection_key") or "").strip()
        aliases = []
        for alias in [outfit_name, collection_key]:
            if alias and alias not in {outfit_id, *aliases}:
                aliases.append(alias)

        vision_summary = str(payload.get("vision_summary") or "").strip()
        emotion_description = vision_summary or f"主人和你一起整理出的角色资源《{display_name}》。"
        emotion_entry = {
            "id": emotion_id,
            "name": emotion_name,
            "description": emotion_description,
            "path": public_asset_url,
            "aliases": [origin_name] if origin_name and origin_name not in {display_name, emotion_id} else [],
            "ai_hint": (
                f"这是主人和你一起认领的角色资源《{display_name}》。"
                f"它属于「{outfit_name}」形象集合，可作为表情/立绘状态 {emotion_id} 使用。"
            ),
            "_meta": {
                "gift_character": True,
                "asset_role": asset_role,
                "asset_id": str(asset.get("asset_id") or ""),
            },
            "_fallback_priority": 180,
        }
        return {
            "character_outfit": {
                "id": outfit_id,
                "name": outfit_name,
                "description": f"主人和你一起整理出的「{outfit_name}」形象集合。",
                "aliases": aliases,
                "allowed_emotions": [emotion_id],
                "emotions": [emotion_entry],
                "_meta": {
                    "gift_character": True,
                    "asset_role": asset_role,
                    "collection_key": collection_key,
                },
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
            return "新的图片礼物"
        return stem[:80]

    def _normalize_collection_key(self, value: Any) -> str:
        raw = str(value or "").strip().lower()
        safe_chars = []
        for char in raw:
            if char.isalnum():
                safe_chars.append(char)
            elif char in {"_", "-"}:
                safe_chars.append(char)
        normalized = "".join(safe_chars).strip("-_")
        return normalized or DEFAULT_COLLECTION_KEY

    def _guess_mime_type(self, suffix: str) -> str:
        return {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(str(suffix or "").strip().lower(), "image/png")
