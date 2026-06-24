from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from ..store import MemoryStore
from .decision_service import GiftDecisionService
from .projection import GiftProjection
from .repository import GiftRepository
from .processors.audio_processor import AudioGiftProcessor
from .processors.base import BaseGiftProcessor
from .processors.image_processor import ImageGiftProcessor


class GiftSystemService:
    def __init__(self, base_dir: Path, *, store: MemoryStore, llm: Any | None = None) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.llm = llm
        self.repository = GiftRepository(store=store)
        self.processors: dict[str, BaseGiftProcessor] = {
            "audio": AudioGiftProcessor(base_dir=self.base_dir),
            "image": ImageGiftProcessor(base_dir=self.base_dir),
        }
        self.projection = GiftProjection(
            repository=self.repository,
            processors=self.processors,
            public_path_builder=self._build_public_path,
        )
        self.decision_service = GiftDecisionService(
            repository=self.repository,
            processors=self.processors,
        )

    def reset(self) -> None:
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def ingest_upload(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        filename: str,
        content_type: str,
        content: bytes,
        now_ts: int | None = None,
        origin_source_id: str = "",
        source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        processor = self._resolve_upload_processor(filename=filename, content_type=content_type)
        created = processor.ingest_upload(
            profile_user_id=profile_user_id,
            session_id=session_id,
            filename=filename,
            content_type=content_type,
            content=content,
            now_ts=now_ts,
        )
        record = self.repository.create_asset(
            asset_id=str(created["asset_id"]),
            resource_id=str(created["resource_id"]),
            profile_user_id=profile_user_id,
            session_id=session_id,
            display_name=str(created["display_name"]),
            asset_type=str(created["asset_type"]),
            origin_event_type=str(created["origin_event_type"]),
            origin_source_id=origin_source_id,
            source_ids=source_ids,
            payload=dict(created.get("payload") or {}),
            media_kind=str(created.get("media_kind") or ""),
            origin_name=str(created.get("origin_name") or ""),
            mime_type=str(created.get("mime_type") or ""),
            file_ext=str(created.get("file_ext") or ""),
            file_size=int(created.get("file_size") or 0),
            storage_relpath=str(created.get("storage_relpath") or ""),
            status=str(created.get("status") or "pending"),
            timestamp=now_ts,
            last_touched_at=int(now_ts or time.time()),
        )
        self.store.set_session_gift_focus(
            profile_user_id=profile_user_id,
            session_id=session_id,
            asset_id=str(record.get("asset_id") or ""),
            timestamp=now_ts,
        )
        return self._decorate_record(record)

    def list_assets(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
        asset_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        records = self.repository.list_assets(
            profile_user_id=profile_user_id,
            session_id=session_id,
            asset_type=asset_type,
            limit=limit,
        )
        return [self._decorate_record(record) for record in records]

    def apply_action(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
        asset_id: str,
        action: str,
        timestamp: int | None = None,
        source_id: str = "",
    ) -> dict[str, Any] | None:
        normalized_action = self._normalize_action(action)
        if not normalized_action:
            raise ValueError("unsupported gift action")

        record = self.resolve_focus_asset(
            profile_user_id=profile_user_id,
            session_id=str(session_id or "").strip(),
            asset_id=asset_id,
        )
        if record is None:
            return None

        processor = self._get_processor(record)
        if normalized_action not in processor.allowed_actions(record):
            raise ValueError("gift action is not allowed in current state")

        effective_ts = int(timestamp or time.time())
        target_asset_id = str(record.get("asset_id") or "")
        if normalized_action == "purge":
            return self.discard_asset(
                profile_user_id=profile_user_id,
                session_id=session_id,
                asset_id=target_asset_id,
                timestamp=effective_ts,
            )
        next_payload = processor.build_action_payload(
            record,
            action=normalized_action,
        )
        next_display_name = None
        reset_container = False
        if normalized_action in {"keep", "internalize"}:
            next_payload, next_display_name, reset_container = self._finalize_image_seed_for_action(
                asset=record,
                payload=next_payload,
            )
        if normalized_action in {"defer", "ask_user"}:
            updated = self.repository.update_asset(
                profile_user_id=profile_user_id,
                asset_id=target_asset_id,
                timestamp=effective_ts,
                source_ids=[source_id] if source_id else None,
                payload=next_payload,
                last_decision_at=effective_ts,
                last_touched_at=effective_ts,
            )
        else:
            updated = self.repository.update_asset(
                profile_user_id=profile_user_id,
                asset_id=target_asset_id,
                display_name=next_display_name,
                status=self._action_to_status(normalized_action),
                timestamp=effective_ts,
                source_ids=[source_id] if source_id else None,
                payload=next_payload,
                container_key="" if reset_container else None,
                container_name="" if reset_container else None,
                last_decision_at=effective_ts,
                last_touched_at=effective_ts,
            )
        if updated is not None:
            self._sync_focus_after_action(
                asset=updated,
                action=normalized_action,
                session_id=str(session_id or updated.get("session_id") or ""),
                timestamp=effective_ts,
            )
        return self._decorate_record(updated) if updated else None

    def build_runtime_projection(self, *, profile_user_id: str) -> dict[str, Any]:
        return self.projection.build_runtime_projection(profile_user_id=profile_user_id)

    def build_internalized_bgm_entries(self, profile_user_id: str) -> list[dict[str, Any]]:
        return list(self.build_runtime_projection(profile_user_id=profile_user_id).get("extra_bgm_tracks") or [])

    def build_transient_image_reply(
        self,
        *,
        asset: dict[str, Any],
        observation: dict[str, Any] | None,
    ) -> str:
        observation_card = observation.get("observation") if isinstance(observation, dict) and isinstance(observation.get("observation"), dict) else {}
        fallback = {
            "assistant_line": self._fallback_transient_image_reply(observation_card=observation_card),
        }
        if self.llm is None:
            return str(fallback["assistant_line"])

        try:
            result = self.llm.call_aux_json(
                system_prompt=(
                    "你是当前前台角色。用户刚刚只是把一张日常图片递给你看看，不是正式送礼。"
                    "请基于观察卡，用第一人称自然回应 1 到 2 句。"
                    "表达你看到这张图时的感觉，可以有一点开心或被分享日常的小温度。"
                    "不要说要收下、归档、存进相册、变成场景、吃掉，也不要提文件名。"
                    "只输出 JSON，对字段 assistant_line 负责。"
                ),
                user_prompt=(
                    f"视觉 summary：{str(observation_card.get('summary') or '').strip() or '(无)'}\n"
                    f"视觉 entities：{', '.join(str(item).strip() for item in list(observation_card.get('entities') or []) if str(item).strip()) or '(无)'}\n"
                    f"视觉 mood_tags：{', '.join(str(item).strip() for item in list(observation_card.get('mood_tags') or []) if str(item).strip()) or '(无)'}\n"
                    f"视觉 uncertainty：{', '.join(str(item).strip() for item in list(observation_card.get('uncertainty') or []) if str(item).strip()) or '(无)'}\n"
                    "请给出当前前台角色看完这张图后的即时回应。"
                ),
                fallback=fallback,
                temperature=0.55,
                prompt_cache_key="aux:gift_transient_image_reply",
            )
        except Exception:
            return str(fallback["assistant_line"])

        assistant_line = str(result.get("assistant_line") or "").strip()
        return assistant_line or str(fallback["assistant_line"])

    def discard_asset(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
        asset_id: str,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        record = self.repository.get_asset(
            profile_user_id=profile_user_id,
            asset_id=asset_id,
        )
        if record is None:
            return None

        storage_relpath = str(record.get("storage_relpath") or "").strip()
        if storage_relpath:
            absolute_path = self.base_dir / Path(storage_relpath)
            try:
                if absolute_path.exists():
                    absolute_path.unlink()
            except OSError:
                pass

        deleted = self.repository.delete_asset(
            profile_user_id=profile_user_id,
            asset_id=asset_id,
        )
        normalized_session_id = str(session_id or record.get("session_id") or "").strip()
        if normalized_session_id:
            session = self.store.get_session(profile_user_id, normalized_session_id)
            focus_asset_id = str((session or {}).get("current_gift_focus_asset_id") or "").strip()
            if focus_asset_id == str(asset_id or "").strip():
                self.store.clear_session_gift_focus(
                    profile_user_id=profile_user_id,
                    session_id=normalized_session_id,
                    timestamp=int(timestamp or time.time()),
                )
        return self._decorate_record(deleted) if deleted else None

    def build_pending_prompt_context(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        limit: int = 3,
    ) -> str:
        return self.decision_service.build_pending_prompt_context(
            profile_user_id=profile_user_id,
            session_id=session_id,
            limit=limit,
        )

    def list_inventory(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        scope: str = "pending_recent",
        limit: int = 5,
    ) -> dict[str, Any]:
        return self.decision_service.list_inventory(
            profile_user_id=profile_user_id,
            session_id=session_id,
            scope=scope,
            limit=limit,
        )

    def apply_image_observation_metadata(
        self,
        *,
        profile_user_id: str,
        asset_id: str,
        observation: dict[str, Any],
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        asset = self.repository.get_asset(
            profile_user_id=profile_user_id,
            asset_id=asset_id,
        )
        if asset is None or str(asset.get("asset_type") or "").strip().lower() != "image":
            return None

        payload = dict(asset.get("payload") or {})
        observation_card = observation.get("observation") if isinstance(observation.get("observation"), dict) else {}
        if not observation_card:
            return None

        existing_collections = self._list_existing_album_collections(
            profile_user_id=profile_user_id,
            exclude_asset_id=str(asset.get("asset_id") or asset_id),
        )
        suggestion = self._suggest_image_metadata(
            asset=asset,
            observation=observation_card,
            existing_collections=existing_collections,
        )
        payload["vision_summary"] = str(observation_card.get("summary") or "").strip()
        payload["vision_entities"] = [
            str(item).strip()
            for item in list(observation_card.get("entities") or [])
            if str(item).strip()
        ][:6]
        payload["vision_mood_tags"] = [
            str(item).strip()
            for item in list(observation_card.get("mood_tags") or [])
            if str(item).strip()
        ][:6]
        payload["vision_uncertainty"] = [
            str(item).strip()
            for item in list(observation_card.get("uncertainty") or [])
            if str(item).strip()
        ][:6]

        current_display_name = str(asset.get("display_name") or "").strip()
        payload.setdefault("display_name_source", "filename")
        payload["seed_name"] = suggestion["display_name"]
        payload["seed_collection_key"] = suggestion["collection_key"]
        payload["seed_collection_name"] = suggestion["collection_name"]
        payload["seed_source"] = "vision_auto"
        payload["seed_updated_at"] = int(timestamp or time.time())

        updated = self.repository.update_asset(
            profile_user_id=profile_user_id,
            asset_id=str(asset.get("asset_id") or asset_id),
            display_name=current_display_name,
            payload=payload,
            timestamp=timestamp,
            last_touched_at=int(timestamp or time.time()),
        )
        return self._decorate_record(updated) if updated else None

    def resolve_focus_asset(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        asset_id: str = "",
    ) -> dict[str, Any] | None:
        normalized_asset_id = str(asset_id or "").strip()
        if normalized_asset_id:
            record = self.repository.get_asset(
                profile_user_id=profile_user_id,
                asset_id=normalized_asset_id,
            )
            if record is not None:
                if (
                    session_id
                    and str(record.get("session_id") or "") == session_id
                    and str(record.get("status") or "") in {"pending", "kept", "internalized"}
                ):
                    self.store.set_session_gift_focus(
                        profile_user_id=profile_user_id,
                        session_id=session_id,
                        asset_id=normalized_asset_id,
                    )
                return record

        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return None

        session = self.store.get_session(profile_user_id, normalized_session_id)
        focus_asset_id = str((session or {}).get("current_gift_focus_asset_id") or "").strip()
        if focus_asset_id:
            focused = self.repository.get_asset(
                profile_user_id=profile_user_id,
                asset_id=focus_asset_id,
            )
            if focused is not None and str(focused.get("session_id") or "") == normalized_session_id:
                if str(focused.get("status") or "") in {"pending", "kept", "internalized"}:
                    return focused
            self.store.clear_session_gift_focus(
                profile_user_id=profile_user_id,
                session_id=normalized_session_id,
            )

        fallback = self.repository.list_assets(
            profile_user_id=profile_user_id,
            session_id=normalized_session_id,
            statuses=["pending", "kept"],
            limit=1,
        )
        if not fallback:
            return None
        self.store.set_session_gift_focus(
            profile_user_id=profile_user_id,
            session_id=normalized_session_id,
            asset_id=str(fallback[0].get("asset_id") or ""),
        )
        return fallback[0]

    def _resolve_upload_processor(self, *, filename: str, content_type: str) -> BaseGiftProcessor:
        for processor in self.processors.values():
            if processor.supports_upload(filename=filename, content_type=content_type):
                return processor
        raise ValueError("当前只支持可识别的礼物文件类型。")

    def _get_processor(self, asset: dict[str, Any]) -> BaseGiftProcessor:
        asset_type = str(asset.get("asset_type") or "").strip()
        processor = self.processors.get(asset_type)
        if processor is None:
            raise ValueError(f"unsupported gift asset type: {asset_type or '(empty)'}")
        return processor

    def _finalize_image_seed_for_action(
        self,
        *,
        asset: dict[str, Any],
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], str | None, bool]:
        if str(asset.get("asset_type") or "").strip().lower() != "image":
            return payload, None, False

        next_payload = dict(payload)
        next_display_name: str | None = None
        reset_container = False

        seed_name = str(next_payload.get("seed_name") or "").strip()
        display_name_source = str(next_payload.get("display_name_source") or "filename").strip().lower() or "filename"
        if seed_name and display_name_source in {"", "filename", "seed_auto", "vision_auto"}:
            next_display_name = seed_name[:80]
            next_payload["display_name_source"] = "akane_confirmed"

        collection_source = str(next_payload.get("collection_source") or "").strip().lower()
        raw_seed_collection_key = str(next_payload.get("seed_collection_key") or "").strip()
        raw_seed_collection_name = str(next_payload.get("seed_collection_name") or "").strip()
        seed_collection_key = self._normalize_collection_key(raw_seed_collection_key or raw_seed_collection_name) if (raw_seed_collection_key or raw_seed_collection_name) else ""
        seed_collection_name = str(next_payload.get("seed_collection_name") or "").strip()
        if seed_collection_key and collection_source in {"", "default", "seed_auto", "vision_auto"}:
            next_payload["collection_key"] = seed_collection_key
            next_payload["collection_name"] = seed_collection_name[:12] or (
                "回忆" if seed_collection_key == "memories" else seed_collection_key[:12]
            )
            next_payload["collection_source"] = "akane_confirmed"
            reset_container = True

        return next_payload, next_display_name, reset_container

    def _normalize_action(self, action: str) -> str:
        normalized = str(action or "").strip().lower()
        return {
            "save": "keep",
            "keep": "keep",
            "internalize": "internalize",
            "reject": "reject",
            "remove": "remove",
            "purge": "purge",
            "defer": "defer",
            "ask_user": "ask_user",
        }.get(normalized, "")

    def _action_to_status(self, action: str) -> str:
        return {
            "keep": "kept",
            "internalize": "internalized",
            "reject": "rejected",
            "remove": "rejected",
        }.get(action, "pending")

    def _sync_focus_after_action(
        self,
        *,
        asset: dict[str, Any],
        action: str,
        session_id: str,
        timestamp: int,
    ) -> None:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return
        asset_id = str(asset.get("asset_id") or "").strip()
        if not asset_id:
            return
        if action in {"defer", "ask_user"}:
            self.store.set_session_gift_focus(
                profile_user_id=str(asset.get("profile_user_id") or ""),
                session_id=normalized_session_id,
                asset_id=asset_id,
                timestamp=timestamp,
            )
            return
        if action in {"keep", "internalize"}:
            self.store.set_session_gift_focus(
                profile_user_id=str(asset.get("profile_user_id") or ""),
                session_id=normalized_session_id,
                asset_id=asset_id,
                timestamp=timestamp,
            )
            return
        self.store.clear_session_gift_focus(
            profile_user_id=str(asset.get("profile_user_id") or ""),
            session_id=normalized_session_id,
            timestamp=timestamp,
        )

    def _decorate_record(self, record: dict[str, Any]) -> dict[str, Any]:
        decorated = dict(record)
        decorated["asset_url"] = self._build_public_path(str(record.get("storage_relpath") or ""))
        return decorated

    def _build_public_path(self, storage_relpath: str) -> str:
        normalized = str(storage_relpath or "").strip().replace("\\", "/").lstrip("/")
        return f"/user-assets/{normalized}" if normalized else ""

    def _suggest_image_metadata(
        self,
        *,
        asset: dict[str, Any],
        observation: dict[str, Any],
        existing_collections: list[dict[str, Any]],
    ) -> dict[str, str]:
        fallback = self._fallback_image_metadata(
            asset=asset,
            observation=observation,
            existing_collections=existing_collections,
        )
        if self.llm is None:
            return fallback

        existing_lines = []
        for collection in existing_collections:
            collection_key = str(collection.get("collection_key") or "").strip()
            collection_name = str(collection.get("collection_name") or "").strip()
            total_count = int(collection.get("total_count") or 0)
            if collection_key and collection_name:
                existing_lines.append(f"- {collection_name} ({collection_key}) / {total_count} 张")

        try:
            result = self.llm.call_aux_json(
                system_prompt=(
                    "你在帮当前前台角色整理收到的图片礼物。"
                    "请只输出 JSON，对字段 display_name, collection_key, collection_name 负责。"
                    "display_name 要像当前前台角色会给图片起的名字，简短、自然、带一点情绪，不要像文件名，不要加《》。"
                    "collection_key 必须是小写英文或下划线，适合作为稳定集合 id。"
                    "collection_name 是给用户看的中文集合名，2 到 6 个字，避免过长。"
                    "不要臆造图中不存在的剧情，只能根据观察卡命名和归类。"
                    "如果已有集合已经明显合适，优先复用已有集合，不要平白新建同义集合。"
                    "只有在现有集合都明显不合适时，才创建新集合。"
                ),
                user_prompt=(
                    f"当前图片原文件名：{str(asset.get('origin_name') or '').strip() or '(无)'}\n"
                    f"当前显示名：{str(asset.get('display_name') or '').strip() or '(无)'}\n"
                    f"视觉 summary：{str(observation.get('summary') or '').strip() or '(无)'}\n"
                    f"视觉 entities：{', '.join(str(item).strip() for item in list(observation.get('entities') or []) if str(item).strip()) or '(无)'}\n"
                    f"视觉 mood_tags：{', '.join(str(item).strip() for item in list(observation.get('mood_tags') or []) if str(item).strip()) or '(无)'}\n"
                    f"视觉 uncertainty：{', '.join(str(item).strip() for item in list(observation.get('uncertainty') or []) if str(item).strip()) or '(无)'}\n"
                    f"已有相册集合：\n{chr(10).join(existing_lines) if existing_lines else '(目前还没有现成集合)'}\n"
                    "请输出一个适合当前前台角色私人相册的名字和集合归类。"
                ),
                fallback=fallback,
                temperature=0.35,
                prompt_cache_key="aux:gift_image_metadata",
            )
        except Exception:
            return fallback
        return self._normalize_image_metadata(
            result,
            fallback=fallback,
            existing_collections=existing_collections,
        )

    def _fallback_image_metadata(
        self,
        *,
        asset: dict[str, Any],
        observation: dict[str, Any],
        existing_collections: list[dict[str, Any]],
    ) -> dict[str, str]:
        summary = str(observation.get("summary") or "").strip()
        entities = [str(item).strip() for item in list(observation.get("entities") or []) if str(item).strip()]
        mood_tags = [str(item).strip() for item in list(observation.get("mood_tags") or []) if str(item).strip()]
        display_name = self._fallback_image_display_name(
            summary=summary,
            entities=entities,
            mood_tags=mood_tags,
            origin_name=str(asset.get("origin_name") or "").strip(),
        )
        collection_key, collection_name = self._fallback_collection_assignment(
            summary=summary,
            entities=entities,
            mood_tags=mood_tags,
            existing_collections=existing_collections,
        )
        return {
            "display_name": display_name,
            "collection_key": collection_key,
            "collection_name": collection_name,
        }

    def _normalize_image_metadata(
        self,
        payload: dict[str, Any],
        *,
        fallback: dict[str, str],
        existing_collections: list[dict[str, Any]],
    ) -> dict[str, str]:
        display_name = str(payload.get("display_name") or "").strip()
        if not display_name:
            display_name = fallback["display_name"]
        display_name = display_name.strip("《》[](){}\"' ")[:24] or fallback["display_name"]

        resolved_collection = self._resolve_collection_choice(
            collection_key=payload.get("collection_key"),
            collection_name=payload.get("collection_name"),
            existing_collections=existing_collections,
        )
        if resolved_collection is None:
            resolved_collection = self._resolve_collection_choice(
                collection_key=fallback["collection_key"],
                collection_name=fallback["collection_name"],
                existing_collections=existing_collections,
            )
        if resolved_collection is None:
            resolved_collection = {
                "collection_key": fallback["collection_key"],
                "collection_name": fallback["collection_name"],
            }
        return {
            "display_name": display_name,
            "collection_key": str(resolved_collection["collection_key"]),
            "collection_name": str(resolved_collection["collection_name"]),
        }

    def _fallback_image_display_name(
        self,
        *,
        summary: str,
        entities: list[str],
        mood_tags: list[str],
        origin_name: str,
    ) -> str:
        cleaned = summary
        for prefix in ("一张", "一幅", "画面里", "画面中", "照片里", "图片里"):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip()
        for splitter in ("，", "。", ",", "."):
            if splitter in cleaned:
                cleaned = cleaned.split(splitter, 1)[0].strip()
                break
        cleaned = cleaned.strip("《》[](){}\"' ")
        if cleaned:
            return cleaned[:18]
        if mood_tags and entities:
            return f"{mood_tags[0]}{entities[0]}"[:18]
        if entities:
            return entities[0][:18]
        stem = Path(origin_name).stem.strip()
        return (stem or "新的图片礼物")[:18]

    def _fallback_collection_assignment(
        self,
        *,
        summary: str,
        entities: list[str],
        mood_tags: list[str],
        existing_collections: list[dict[str, Any]],
    ) -> tuple[str, str]:
        joined = " ".join([summary, *entities, *mood_tags]).lower()
        mapping = [
            (("雨", "下雨", "雨天"), ("rain", "雨天")),
            (("夜", "夜景", "深夜", "星", "月"), ("night", "夜色")),
            (("窗", "卧室", "房间", "居家", "床"), ("room", "房间")),
            (("街", "城市", "楼", "建筑"), ("city", "街景")),
            (("海", "山", "旅行", "风景", "天空", "云"), ("travel", "远景")),
        ]
        for keywords, assignment in mapping:
            if any(keyword in joined for keyword in keywords):
                resolved = self._resolve_collection_choice(
                    collection_key=assignment[0],
                    collection_name=assignment[1],
                    existing_collections=existing_collections,
                )
                if resolved is not None:
                    return (str(resolved["collection_key"]), str(resolved["collection_name"]))
                return assignment
        resolved_default = self._resolve_collection_choice(
            collection_key="memories",
            collection_name="回忆",
            existing_collections=existing_collections,
        )
        if resolved_default is not None:
            return (str(resolved_default["collection_key"]), str(resolved_default["collection_name"]))
        return ("memories", "回忆")

    def _list_existing_album_collections(
        self,
        *,
        profile_user_id: str,
        exclude_asset_id: str = "",
    ) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        normalized_exclude_asset_id = str(exclude_asset_id or "").strip()
        records = self.repository.list_assets(
            profile_user_id=profile_user_id,
            asset_type="image",
            statuses=["kept", "internalized"],
            limit=200,
        )
        for record in records:
            if normalized_exclude_asset_id and str(record.get("asset_id") or "").strip() == normalized_exclude_asset_id:
                continue
            payload = dict(record.get("payload") or {})
            collection_key = self._normalize_collection_key(payload.get("collection_key"))
            collection_name = str(payload.get("collection_name") or "").strip()
            if not collection_key:
                continue
            if not collection_name:
                collection_name = "回忆" if collection_key == "memories" else collection_key
            current = grouped.get(collection_key)
            if current is None:
                grouped[collection_key] = {
                    "collection_key": collection_key,
                    "collection_name": collection_name[:12],
                    "total_count": 1,
                }
                continue
            current["total_count"] = int(current.get("total_count") or 0) + 1
            if not str(current.get("collection_name") or "").strip():
                current["collection_name"] = collection_name[:12]
        return sorted(
            grouped.values(),
            key=lambda item: (-int(item.get("total_count") or 0), str(item.get("collection_name") or "")),
        )

    def _resolve_collection_choice(
        self,
        *,
        collection_key: Any,
        collection_name: Any,
        existing_collections: list[dict[str, Any]],
    ) -> dict[str, str] | None:
        normalized_key = self._normalize_collection_key(collection_key)
        normalized_name = str(collection_name or "").strip()

        if existing_collections:
            for existing in existing_collections:
                existing_key = self._normalize_collection_key(existing.get("collection_key"))
                existing_name = str(existing.get("collection_name") or "").strip()
                if normalized_key and existing_key == normalized_key:
                    return {
                        "collection_key": existing_key,
                        "collection_name": existing_name or normalized_name or "回忆",
                    }
                if normalized_name and existing_name and existing_name == normalized_name:
                    return {
                        "collection_key": existing_key or normalized_key or "memories",
                        "collection_name": existing_name,
                    }

        if not normalized_key and not normalized_name:
            return None
        if not normalized_key:
            normalized_key = self._normalize_collection_key(normalized_name)
        if not normalized_key:
            return None
        safe_name = normalized_name[:12] if normalized_name else ("回忆" if normalized_key == "memories" else normalized_key[:12])
        return {
            "collection_key": normalized_key,
            "collection_name": safe_name,
        }

    def _normalize_collection_key(self, value: Any) -> str:
        raw = str(value or "").strip().lower()
        safe_chars = []
        for char in raw:
            if char.isalnum():
                safe_chars.append(char)
            elif char in {"_", "-"}:
                safe_chars.append(char)
        normalized = "".join(safe_chars).strip("-_")
        return normalized[:32]

    def _fallback_transient_image_reply(self, *, observation_card: dict[str, Any]) -> str:
        summary = str(observation_card.get("summary") or "").strip()
        mood_tags = [str(item).strip() for item in list(observation_card.get("mood_tags") or []) if str(item).strip()]
        entities = [str(item).strip() for item in list(observation_card.get("entities") or []) if str(item).strip()]
        if summary:
            if mood_tags:
                return f"我看到了哦，整张图有种{mood_tags[0]}的感觉。{summary}"
            return f"我看到了哦。{summary}"
        if entities:
            return f"我看到了哦，第一眼会注意到{entities[0]}。谢谢主人把这样的日常也分享给我。"
        return "我看到了哦。只是这样被主人顺手分享一下日常，我也会很开心。"
