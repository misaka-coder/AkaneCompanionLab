from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import config
from services.llm_client import build_llm_client

from .resource_manifest import ResourceManifest
from .store import MemoryStore


logger = logging.getLogger("akane.vision")
JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
PROMPT_STYLE_REVISION = "atmo4"


@dataclass(frozen=True)
class VisionTarget:
    observation_type: str
    target_id: str
    source_path: Path
    public_path: str
    resource_fingerprint: str
    prompt_version: str
    title: str
    hint_text: str
    owner_profile_user_id: str = ""
    owner_session_id: str = ""


class VisionObservationService:
    def __init__(
        self,
        base_dir: Path,
        *,
        store: MemoryStore,
        resource_manifest: ResourceManifest | None = None,
        gift_assets_dir: Path | None = None,
        analyze_image_fn: Callable[[VisionTarget], dict[str, Any]] | None = None,
        on_observation_ready: Callable[[VisionTarget, dict[str, Any]], None] | None = None,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.gift_assets_dir = Path(gift_assets_dir or base_dir)
        self.store = store
        self.resource_manifest = resource_manifest
        self._lock = threading.RLock()
        self._jobs_in_flight: set[str] = set()
        self._analyze_image_fn = analyze_image_fn or self._analyze_with_remote_model
        self._on_observation_ready = on_observation_ready
        self._client = self._build_client()

    def reset(self) -> None:
        with self._lock:
            self._jobs_in_flight.clear()

    def reload_client(self) -> dict[str, Any]:
        client = self._build_client()
        with self._lock:
            self._client = client
        return {
            "status": "reloaded" if client is not None else "not_configured",
            "model": str(getattr(config, "VISION_MODEL_NAME", "") or "").strip(),
        }

    def build_scene_prompt_context(
        self,
        *,
        visual_payload: dict[str, Any] | None,
        extra_bgm_tracks: list[dict[str, Any]] | None = None,
        extra_scene_groups: list[dict[str, Any]] | None = None,
        extra_character_outfits: list[dict[str, Any]] | None = None,
    ) -> str:
        target = self._resolve_scene_target(
            visual_payload=visual_payload or {},
            extra_bgm_tracks=extra_bgm_tracks,
            extra_scene_groups=extra_scene_groups,
            extra_character_outfits=extra_character_outfits,
        )
        if target is None:
            return ""

        observation = self.store.get_vision_observation(
            observation_type=target.observation_type,
            resource_fingerprint=target.resource_fingerprint,
            prompt_version=target.prompt_version,
        )
        if (observation is None or self._should_retry_observation(observation)) and bool(
            getattr(config, "VISION_AUTO_SCENE_OBSERVE", True)
        ):
            self.schedule_scene_observation(
                visual_payload=visual_payload or {},
                extra_bgm_tracks=extra_bgm_tracks,
                extra_scene_groups=extra_scene_groups,
                extra_character_outfits=extra_character_outfits,
            )
            return ""
        if observation is None or str(observation.get("status") or "") != "ready":
            return ""
        return self._format_observation_prompt(
            heading="当前场景沉浸观察：",
            observation=observation,
        )

    def build_outfit_prompt_context(
        self,
        *,
        visual_payload: dict[str, Any] | None,
        extra_bgm_tracks: list[dict[str, Any]] | None = None,
        extra_scene_groups: list[dict[str, Any]] | None = None,
        extra_character_outfits: list[dict[str, Any]] | None = None,
    ) -> str:
        target = self._resolve_outfit_target(
            visual_payload=visual_payload or {},
            extra_bgm_tracks=extra_bgm_tracks,
            extra_scene_groups=extra_scene_groups,
            extra_character_outfits=extra_character_outfits,
        )
        if target is None:
            return ""

        observation = self.store.get_vision_observation(
            observation_type=target.observation_type,
            resource_fingerprint=target.resource_fingerprint,
            prompt_version=target.prompt_version,
        )
        if (observation is None or self._should_retry_observation(observation)) and bool(
            getattr(config, "VISION_AUTO_OUTFIT_OBSERVE", True)
        ):
            self.schedule_outfit_observation(
                visual_payload=visual_payload or {},
                extra_bgm_tracks=extra_bgm_tracks,
                extra_scene_groups=extra_scene_groups,
                extra_character_outfits=extra_character_outfits,
            )
            return ""
        if observation is None or str(observation.get("status") or "") != "ready":
            return ""
        return self._format_observation_prompt(
            heading="当前服装体感观察：",
            observation=observation,
        )

    def schedule_scene_observation(
        self,
        *,
        visual_payload: dict[str, Any],
        extra_bgm_tracks: list[dict[str, Any]] | None = None,
        extra_scene_groups: list[dict[str, Any]] | None = None,
        extra_character_outfits: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        target = self._resolve_scene_target(
            visual_payload=visual_payload,
            extra_bgm_tracks=extra_bgm_tracks,
            extra_scene_groups=extra_scene_groups,
            extra_character_outfits=extra_character_outfits,
        )
        if target is None:
            return None
        return self._schedule_target(target)

    def schedule_outfit_observation(
        self,
        *,
        visual_payload: dict[str, Any],
        extra_bgm_tracks: list[dict[str, Any]] | None = None,
        extra_scene_groups: list[dict[str, Any]] | None = None,
        extra_character_outfits: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        target = self._resolve_outfit_target(
            visual_payload=visual_payload,
            extra_bgm_tracks=extra_bgm_tracks,
            extra_scene_groups=extra_scene_groups,
            extra_character_outfits=extra_character_outfits,
        )
        if target is None:
            return None
        return self._schedule_target(target)

    def ensure_scene_observation(
        self,
        *,
        visual_payload: dict[str, Any],
        extra_bgm_tracks: list[dict[str, Any]] | None = None,
        extra_scene_groups: list[dict[str, Any]] | None = None,
        extra_character_outfits: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        target = self._resolve_scene_target(
            visual_payload=visual_payload,
            extra_bgm_tracks=extra_bgm_tracks,
            extra_scene_groups=extra_scene_groups,
            extra_character_outfits=extra_character_outfits,
        )
        if target is None:
            return None
        return self._ensure_target(target)

    def ensure_outfit_observation(
        self,
        *,
        visual_payload: dict[str, Any],
        extra_bgm_tracks: list[dict[str, Any]] | None = None,
        extra_scene_groups: list[dict[str, Any]] | None = None,
        extra_character_outfits: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        target = self._resolve_outfit_target(
            visual_payload=visual_payload,
            extra_bgm_tracks=extra_bgm_tracks,
            extra_scene_groups=extra_scene_groups,
            extra_character_outfits=extra_character_outfits,
        )
        if target is None:
            return None
        return self._ensure_target(target)

    def build_gift_prompt_context(self, *, asset: dict[str, Any] | None) -> str:
        target = self._resolve_gift_target(asset=asset)
        if target is None:
            return ""

        observation = self.store.get_vision_observation(
            observation_type=target.observation_type,
            resource_fingerprint=target.resource_fingerprint,
            prompt_version=target.prompt_version,
        )
        if (observation is None or self._should_retry_observation(observation)) and bool(
            getattr(config, "VISION_AUTO_GIFT_OBSERVE", True)
        ):
            self.schedule_gift_observation(asset=asset)
            return ""
        if observation is None or str(observation.get("status") or "") != "ready":
            return ""
        return self._format_observation_prompt(
            heading="当前礼物视觉观察：",
            observation=observation,
        )

    def schedule_gift_observation(self, *, asset: dict[str, Any] | None) -> dict[str, Any] | None:
        target = self._resolve_gift_target(asset=asset)
        if target is None:
            return None
        return self._schedule_target(target)

    def ensure_gift_observation(self, *, asset: dict[str, Any] | None) -> dict[str, Any] | None:
        target = self._resolve_gift_target(asset=asset)
        if target is None:
            return None
        return self._ensure_target(target)

    def schedule_attachment_image_observation(
        self,
        *,
        attachment: dict[str, Any] | None,
        source_path: Path,
    ) -> dict[str, Any] | None:
        target = self._resolve_attachment_image_target(
            attachment=attachment,
            source_path=Path(source_path),
        )
        if target is None:
            return None
        return self._schedule_target(target)

    def analyze_gift_once(self, *, asset: dict[str, Any] | None) -> dict[str, Any] | None:
        target = self._resolve_gift_target(asset=asset)
        if target is None:
            return None

        override = self._load_override_observation(
            target.source_path,
            observation_type=target.observation_type,
        )
        if override is not None:
            return {
                "status": "ready",
                "provider": "override",
                "model_name": "override",
                "observation": self._normalize_observation_card(override, observation_type=target.observation_type),
            }

        if self._client is None and self._analyze_image_fn == self._analyze_with_remote_model:
            return None

        observation = self._normalize_observation_card(
            self._analyze_image_fn(target),
            observation_type=target.observation_type,
        )
        return {
            "status": "ready",
            "provider": self._provider_label(),
            "model_name": str(getattr(config, "VISION_MODEL_NAME", "") or "").strip(),
            "observation": observation,
        }

    def analyze_screen_clip(
        self,
        *,
        frames: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("视觉模型尚未配置。")
        usable_frames = [
            frame
            for frame in list(frames or [])[:6]
            if isinstance(frame, dict) and str(frame.get("data_url") or "").startswith("data:image/")
        ]
        if not usable_frames:
            raise RuntimeError("没有可用的屏幕帧。")

        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": self._build_screen_clip_user_instruction(context or {}, usable_frames),
            }
        ]
        for frame in usable_frames:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": str(frame.get("data_url") or "")},
                }
            )

        response = self._client.chat.completions.create(
            model=str(getattr(config, "VISION_MODEL_NAME", "") or "").strip(),
            messages=[
                {
                    "role": "system",
                    "content": self._build_screen_clip_system_instruction(),
                },
                {
                    "role": "user",
                    "content": content,
                },
            ],
            temperature=0.45,
        )
        raw_text = self._coerce_response_text(response)
        payload = self._extract_json_dict(raw_text)
        return self._normalize_screen_clip_observation(payload)

    def _build_client(self) -> Any | None:
        if not bool(getattr(config, "VISION_ENABLED", True)):
            return None
        if not str(getattr(config, "VISION_API_KEY", "") or "").strip():
            return None
        if not str(getattr(config, "VISION_BASE_URL", "") or "").strip():
            return None
        if not str(getattr(config, "VISION_MODEL_NAME", "") or "").strip():
            return None
        try:
            return build_llm_client(
                api_key=config.VISION_API_KEY,
                base_url=config.VISION_BASE_URL,
                protocol=getattr(config, "VISION_API_PROTOCOL", "auto"),
                timeout=float(getattr(config, "VISION_REQUEST_TIMEOUT", 60.0) or 60.0),
                max_retries=0,
            )
        except Exception as exc:
            logger.warning("Vision client init failed: %s", exc)
            return None

    def _schedule_target(self, target: VisionTarget) -> dict[str, Any] | None:
        existing = self.store.get_vision_observation(
            observation_type=target.observation_type,
            resource_fingerprint=target.resource_fingerprint,
            prompt_version=target.prompt_version,
        )
        if self._can_reuse_existing_observation(existing):
            return existing

        override = self._load_override_observation(
            target.source_path,
            observation_type=target.observation_type,
        )
        if override is not None:
            saved = self._save_observation(
                target=target,
                status="ready",
                observation=override,
                provider="override",
                model_name="override",
            )
            self._notify_observation_ready(target=target, observation=saved)
            return saved

        if self._client is None and self._analyze_image_fn == self._analyze_with_remote_model:
            return existing

        pending = self._save_observation(
            target=target,
            status="pending",
            observation={},
            provider=self._provider_label(),
            model_name=str(getattr(config, "VISION_MODEL_NAME", "") or "").strip(),
        )
        job_key = self._job_key(target)
        with self._lock:
            if job_key in self._jobs_in_flight:
                return pending
            self._jobs_in_flight.add(job_key)

        thread = threading.Thread(
            target=self._run_observation_job,
            args=(target, job_key),
            name=f"akane-vision-{target.observation_type}",
            daemon=True,
        )
        thread.start()
        return pending

    def _can_reuse_existing_observation(self, observation: dict[str, Any] | None) -> bool:
        if not isinstance(observation, dict):
            return False
        status = str(observation.get("status") or "").strip().lower()
        if status == "ready":
            return True
        if status in {"pending", "running"}:
            return not self._should_retry_observation(observation)
        return False

    def _should_retry_observation(self, observation: dict[str, Any] | None) -> bool:
        if not isinstance(observation, dict):
            return False
        status = str(observation.get("status") or "").strip().lower()
        if status == "error":
            return True
        if status not in {"pending", "running"}:
            return False

        updated_at = int(observation.get("updated_at") or 0)
        if updated_at <= 0:
            return True
        age_seconds = max(0, int(time.time()) - updated_at)
        if status == "pending":
            return age_seconds >= 15
        running_stale_seconds = max(int(getattr(config, "VISION_REQUEST_TIMEOUT", 60.0) or 60.0) + 30, 180)
        return age_seconds >= running_stale_seconds

    def _ensure_target(self, target: VisionTarget) -> dict[str, Any] | None:
        existing = self.store.get_vision_observation(
            observation_type=target.observation_type,
            resource_fingerprint=target.resource_fingerprint,
            prompt_version=target.prompt_version,
        )
        if existing is not None and str(existing.get("status") or "") == "ready":
            return existing

        override = self._load_override_observation(
            target.source_path,
            observation_type=target.observation_type,
        )
        if override is not None:
            saved = self._save_observation(
                target=target,
                status="ready",
                observation=override,
                provider="override",
                model_name="override",
            )
            self._notify_observation_ready(target=target, observation=saved)
            return saved

        if self._client is None and self._analyze_image_fn == self._analyze_with_remote_model:
            return existing

        return self._observe_target(target)

    def _run_observation_job(self, target: VisionTarget, job_key: str) -> None:
        try:
            self._observe_target(target)
        finally:
            with self._lock:
                self._jobs_in_flight.discard(job_key)

    def _observe_target(self, target: VisionTarget) -> dict[str, Any] | None:
        try:
            self._save_observation(
                target=target,
                status="running",
                observation={},
                provider=self._provider_label(),
                model_name=str(getattr(config, "VISION_MODEL_NAME", "") or "").strip(),
            )
            observation = self._normalize_observation_card(self._analyze_image_fn(target), observation_type=target.observation_type)
            saved = self._save_observation(
                target=target,
                status="ready",
                observation=observation,
                provider=self._provider_label(),
                model_name=str(getattr(config, "VISION_MODEL_NAME", "") or "").strip(),
            )
            self._notify_observation_ready(target=target, observation=saved)
            return saved
        except Exception as exc:
            logger.warning("Vision observation failed for %s: %s", target.source_path, exc)
            saved = self._save_observation(
                target=target,
                status="error",
                observation={},
                error_message=str(exc),
                provider=self._provider_label(),
                model_name=str(getattr(config, "VISION_MODEL_NAME", "") or "").strip(),
            )
            self._notify_observation_ready(target=target, observation=saved)
            return saved

    def _resolve_scene_target(
        self,
        *,
        visual_payload: dict[str, Any],
        extra_bgm_tracks: list[dict[str, Any]] | None = None,
        extra_scene_groups: list[dict[str, Any]] | None = None,
        extra_character_outfits: list[dict[str, Any]] | None = None,
    ) -> VisionTarget | None:
        if self.resource_manifest is None:
            return None

        bundle = self.resource_manifest.resolve_visual_bundle(
            visual_payload,
            extra_bgm_tracks=extra_bgm_tracks,
            extra_scene_groups=extra_scene_groups,
            extra_character_outfits=extra_character_outfits,
        )
        major = bundle.get("major")
        minor = bundle.get("minor")
        background = bundle.get("background")
        if not isinstance(background, dict):
            return None

        public_path = str(background.get("path") or "").strip()
        source_path = self._resolve_public_path(public_path)
        if source_path is None or not source_path.exists():
            return None

        title_parts = [
            str((major or {}).get("name") or (major or {}).get("id") or "").strip(),
            str((minor or {}).get("name") or (minor or {}).get("id") or "").strip(),
            str(background.get("name") or background.get("id") or "").strip(),
        ]
        target_id = "::".join(
            part
            for part in [
                str((major or {}).get("id") or "").strip(),
                str((minor or {}).get("id") or "").strip(),
                str(background.get("id") or "").strip(),
            ]
            if part
        )
        hint_parts = [
            str((major or {}).get("description") or "").strip(),
            str((major or {}).get("ai_hint") or "").strip(),
            str((minor or {}).get("description") or "").strip(),
            str((minor or {}).get("ai_hint") or "").strip(),
            str(background.get("description") or "").strip(),
            str(background.get("ai_hint") or "").strip(),
        ]
        return VisionTarget(
            observation_type="scene",
            target_id=target_id,
            source_path=source_path,
            public_path=public_path,
            resource_fingerprint=self._fingerprint_file(source_path),
            prompt_version=self._current_prompt_version(),
            title=" / ".join(part for part in title_parts if part),
            hint_text=" ".join(part for part in hint_parts if part),
        )

    def _resolve_outfit_target(
        self,
        *,
        visual_payload: dict[str, Any],
        extra_bgm_tracks: list[dict[str, Any]] | None = None,
        extra_scene_groups: list[dict[str, Any]] | None = None,
        extra_character_outfits: list[dict[str, Any]] | None = None,
    ) -> VisionTarget | None:
        if self.resource_manifest is None:
            return None

        bundle = self.resource_manifest.resolve_visual_bundle(
            visual_payload,
            extra_bgm_tracks=extra_bgm_tracks,
            extra_scene_groups=extra_scene_groups,
            extra_character_outfits=extra_character_outfits,
        )
        outfit = bundle.get("outfit")
        if not isinstance(outfit, dict):
            return None

        representative_emotion = self._select_outfit_representative_emotion(
            outfit=outfit,
            fallback_emotion=bundle.get("emotion"),
        )
        if representative_emotion is None:
            return None

        public_path = str(representative_emotion.get("path") or "").strip()
        source_path = self._resolve_public_path(public_path)
        if source_path is None or not source_path.exists():
            return None

        hint_parts = [
            str(outfit.get("description") or "").strip(),
            str(outfit.get("ai_hint") or "").strip(),
            str(representative_emotion.get("description") or "").strip(),
            str(representative_emotion.get("ai_hint") or "").strip(),
        ]
        return VisionTarget(
            observation_type="outfit",
            target_id=str(outfit.get("id") or "").strip(),
            source_path=source_path,
            public_path=public_path,
            resource_fingerprint=self._fingerprint_file(source_path),
            prompt_version=self._current_prompt_version(),
            title=str(outfit.get("name") or outfit.get("id") or "当前服装").strip() or "当前服装",
            hint_text=" ".join(part for part in hint_parts if part),
        )

    def _resolve_gift_target(self, *, asset: dict[str, Any] | None) -> VisionTarget | None:
        if not isinstance(asset, dict):
            return None
        if str(asset.get("asset_type") or "").strip().lower() != "image":
            return None
        storage_relpath = str(asset.get("storage_relpath") or "").strip()
        if not storage_relpath:
            return None
        source_path = self.gift_assets_dir / Path(storage_relpath)
        if not source_path.exists():
            return None
        public_path = str(asset.get("asset_url") or "").strip()
        hint_parts = [
            str(asset.get("display_name") or "").strip(),
            str(asset.get("payload", {}).get("user_caption") or "").strip(),
        ]
        return VisionTarget(
            observation_type="gift",
            target_id=str(asset.get("asset_id") or "").strip(),
            source_path=source_path,
            public_path=public_path,
            resource_fingerprint=self._fingerprint_file(source_path),
            prompt_version=self._current_prompt_version(),
            title=str(asset.get("display_name") or asset.get("origin_name") or "礼物").strip() or "礼物",
            hint_text=" ".join(part for part in hint_parts if part),
            owner_profile_user_id=str(asset.get("profile_user_id") or "").strip(),
        )

    def _resolve_attachment_image_target(
        self,
        *,
        attachment: dict[str, Any] | None,
        source_path: Path,
    ) -> VisionTarget | None:
        if not isinstance(attachment, dict):
            return None
        if str(attachment.get("kind") or "").strip().lower() != "image":
            return None
        if not source_path.exists() or not source_path.is_file():
            return None

        title = (
            str(attachment.get("summary_title") or "").strip()
            or str(attachment.get("origin_name") or "").strip()
            or str(attachment.get("attachment_handle") or "").strip()
            or "图片附件"
        )
        hint_parts = [
            str(attachment.get("attachment_handle") or "").strip(),
            str(attachment.get("origin_name") or "").strip(),
            str(attachment.get("short_hint") or "").strip(),
        ]
        return VisionTarget(
            observation_type="attachment_image",
            target_id=str(attachment.get("attachment_id") or "").strip(),
            source_path=source_path,
            public_path="",
            resource_fingerprint=self._fingerprint_file(source_path),
            prompt_version=self._current_prompt_version(),
            title=title,
            hint_text=" ".join(part for part in hint_parts if part),
            owner_profile_user_id=str(attachment.get("profile_user_id") or "").strip(),
            owner_session_id=str(attachment.get("session_id") or "").strip(),
        )

    def _select_outfit_representative_emotion(
        self,
        *,
        outfit: dict[str, Any],
        fallback_emotion: Any = None,
    ) -> dict[str, Any] | None:
        emotions = [
            emotion
            for emotion in list(outfit.get("emotions") or [])
            if isinstance(emotion, dict)
        ]
        if not emotions and isinstance(fallback_emotion, dict):
            return fallback_emotion
        if not emotions:
            return None

        default_emotion_id = str((outfit.get("_meta") or {}).get("default_emotion") or "").strip()
        if default_emotion_id:
            for emotion in emotions:
                if str(emotion.get("id") or "").strip() == default_emotion_id:
                    return emotion
        return emotions[0]

    def _resolve_public_path(self, public_path: str) -> Path | None:
        normalized = str(public_path or "").strip()
        if normalized.startswith("/assets/"):
            relative = normalized.removeprefix("/assets/").strip("/")
            if not relative:
                return None
            return self.resource_manifest.assets_dir / Path(relative)
        if normalized.startswith("/user-assets/"):
            relative = normalized.removeprefix("/user-assets/").strip("/")
            if not relative:
                return None
            return self.gift_assets_dir / Path(relative)
        return None

    def _load_override_observation(
        self,
        source_path: Path,
        *,
        observation_type: str,
    ) -> dict[str, Any] | None:
        json_path = source_path.with_suffix(".vision.json")
        if json_path.exists():
            try:
                payload = json.loads(json_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    return self._normalize_observation_card(payload, observation_type=observation_type)
            except Exception:
                return None

        for suffix in (".vision.md", ".vision.txt"):
            text_path = source_path.with_suffix(suffix)
            if text_path.exists():
                try:
                    summary = text_path.read_text(encoding="utf-8").strip()
                except Exception:
                    summary = ""
                if summary:
                    return self._normalize_observation_card({"summary": summary}, observation_type=observation_type)
        return None

    def _save_observation(
        self,
        *,
        target: VisionTarget,
        status: str,
        observation: dict[str, Any],
        provider: str = "",
        model_name: str = "",
        error_message: str = "",
    ) -> dict[str, Any]:
        summary = str(observation.get("summary") or "").strip()
        return self.store.upsert_vision_observation(
            observation_type=target.observation_type,
            resource_fingerprint=target.resource_fingerprint,
            target_id=target.target_id,
            source_path=str(target.source_path),
            public_path=target.public_path,
            prompt_version=target.prompt_version,
            provider=provider,
            model_name=model_name,
            status=status,
            summary=summary,
            observation=observation,
            error_message=error_message,
        )

    def _notify_observation_ready(self, *, target: VisionTarget, observation: dict[str, Any]) -> None:
        callback = self._on_observation_ready
        if callback is None:
            return
        try:
            callback(target, observation)
        except Exception as exc:
            logger.warning("Vision observation ready hook failed for %s: %s", target.target_id, exc)

    def _analyze_with_remote_model(self, target: VisionTarget) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("视觉模型尚未配置。")

        image_bytes = target.source_path.read_bytes()
        max_bytes = int(getattr(config, "VISION_MAX_IMAGE_BYTES", 8 * 1024 * 1024) or 8 * 1024 * 1024)
        if len(image_bytes) > max_bytes:
            raise RuntimeError(f"图像过大，当前限制为 {max_bytes} bytes。")

        media_type = self._guess_media_type(target.source_path)
        image_url = (
            f"data:{media_type};base64,"
            f"{base64.b64encode(image_bytes).decode('ascii')}"
        )
        response = self._client.chat.completions.create(
            model=str(getattr(config, "VISION_MODEL_NAME", "") or "").strip(),
            messages=[
                {
                    "role": "system",
                    "content": self._build_system_instruction(target),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": self._build_user_instruction(target),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url},
                        },
                    ],
                },
            ],
            temperature=0.35,
        )
        raw_text = self._coerce_response_text(response)
        return self._extract_json_dict(raw_text)

    def _build_system_instruction(self, target: VisionTarget) -> str:
        if target.observation_type == "scene":
            return (
                "你是一个视觉观察器，不是图像分类器。"
                "请只依据图像和提供的少量提示，输出一个 JSON 对象。"
                "字段固定为 summary, entities, mood_tags, uncertainty。"
                "你的输出会直接提供给一个正身处场景中的角色。"
                "summary 用 1 句中文写成角色置身其中时能感知到的环境速写，保持 70% 证据、30% 氛围；"
                "不要写成教程说明，不要用“这是一张”“这是一份关于”“画面中有很多”这种过度客观的开头。"
                "绝对不要提及屏幕、截图、画面、图片、照片、镜头、UI、界面、文档、高亮标注、像素、观察者等媒介或旁观者词汇。"
                "优先描述空间、光线、时间感、冷暖感、空气感和整体安静或热闹的气氛。"
                "entities 与 mood_tags 各给 2 到 6 个短词；"
                "不确定的地方写进 uncertainty，不要臆造剧情，不要直接替角色说话，不要输出多余字段。"
            )
        if target.observation_type == "gift":
            return (
                "你是一个视觉观察器。当前任务是观察一份会被角色收下的图片礼物。"
                "请只依据图像和提供的少量提示，输出一个 JSON 对象。"
                "字段固定为 summary, entities, mood_tags, uncertainty。"
                "summary 用 1 句中文写成轻微带氛围的第一眼观察，保持 70% 证据、30% 氛围；"
                "不要写成说明书式总结，不要用“这是一张”“这是一份关于”开头。"
                "优先描述这张图最先映入眼帘的部分、整体气氛，以及它为什么会让人想收藏。"
                "entities 与 mood_tags 各给 2 到 6 个短词；"
                "不确定的地方写进 uncertainty，不要臆造剧情，不要直接替角色抒情，不要输出多余字段。"
            )
        if target.observation_type == "outfit":
            return (
                "你是一个视觉观察器。当前任务是观察角色的服装与整体穿搭。"
                "请只依据图像和提供的少量提示，输出一个 JSON 对象。"
                "字段固定为 summary, entities, mood_tags, uncertainty, appearance_traits。"
                "你的输出会直接提供给一个正穿着这套衣服的角色。"
                "summary 用 1 句中文写成穿在身上的整体气质与体感记录，保持 70% 证据、30% 氛围；"
                "entities 与 mood_tags 各给 2 到 6 个短词；"
                "appearance_traits 给 3 到 6 个短词，优先写能支撑人物形象的稳定外观要点："
                "发色、发长、瞳色、主服装颜色、标志性配饰颜色。"
                "重点观察服装、颜色、材质、风格和整体气质，不必细致描述表情。"
                "绝对不要提及图中、人物、截图、屏幕、画面、照片、镜头等旁观者词汇。"
                "如果颜色或长度能清楚看出来，就应该写进 appearance_traits；"
                "如果拿不准，再写进 uncertainty，不要随便猜。"
                "不要写成纯服装清单，也不要直接替角色说话。"
                "不确定的地方写进 uncertainty，不要臆造剧情，不要输出多余字段。"
            )
        if target.observation_type == "attachment_image":
            return (
                "你是一个视觉观察器。当前任务是观察聊天里临时发来的图片附件。"
                "请只依据图像和提供的少量提示，输出一个 JSON 对象。"
                "字段固定为 summary_title, summary, entities, mood_tags, uncertainty。"
                "summary_title 用 4 到 12 个中文字符写临时标题，方便聊天中称呼它；"
                "标题要具体，例如“晚餐照片”“作业截图”“白猫窗边”，不要使用原始文件名，不要带扩展名。"
                "summary 用 1 句中文概括画面里能确认的主要内容和氛围，可以说明它像照片、截图、菜单或文档画面。"
                "entities 与 mood_tags 各给 2 到 6 个短词；"
                "不确定的地方写进 uncertainty，不要臆造看不见的故事，不要直接替角色说话，不要输出多余字段。"
            )
        return (
            "你是一个视觉观察器。请只依据图像和提供的少量提示，输出一个 JSON 对象。"
            "字段固定为 summary, entities, mood_tags, uncertainty。"
            "summary 用 1 句中文写成轻微带氛围的观察，保持 70% 证据、30% 氛围；"
            "entities 与 mood_tags 各给 2 到 6 个短词；"
            "不确定的地方写进 uncertainty，不要臆造剧情，不要输出多余字段。"
        )

    def _build_user_instruction(self, target: VisionTarget) -> str:
        lines = [
            f"观察对象标题：{target.title or '未命名对象'}",
            f"辅助提示：{target.hint_text or '(无)'}",
        ]
        if target.observation_type == "scene":
            lines.append("请更关注空间、光线、时间感、冷暖感和整体氛围。默认视角是角色正身处其中，不是站在屏幕外描述图片。")
        elif target.observation_type == "gift":
            lines.append("请更关注这张图第一眼最打动人的部分，以及它给人的收藏感。不要臆造故事。")
        elif target.observation_type == "attachment_image":
            lines.append("请为这张临时图片生成一个好称呼的短标题，并概括画面主要内容；如果是截图，也可以明确说像截图。")
        if target.observation_type == "outfit":
            lines.append(
                "请重点整理这套服装穿在身上的视觉特点与整体气质，忽略细微表情变化。"
                "默认视角不是旁观者。"
                "如果能明确辨认，请优先写出发色、发长、瞳色、上装主色、下装主色、标志配饰颜色。"
            )
        lines.append("请输出严格 JSON。")
        return "\n".join(lines)

    def _build_screen_clip_system_instruction(self) -> str:
        return (
            "你像当前前台角色坐在用户旁边时的一双眼睛。"
            "你会看到几眼连续的近况，请把这几秒里能确认的事情整理成一个 JSON 对象。"
            "字段固定为 summary, current_state, visible_text, concrete_details, changes, topics, mood_tags, salience, sensitive, confidence, uncertainty。"
            "summary 写 1 句中文，像递给当前前台角色的第一眼印象：具体、轻微有温度，但不要替前台角色开口说话。"
            "summary 尽量包含能确认的事实，例如软件/网页/游戏名、窗口标题、主体内容、按钮、卡片、代码、角色动作或视频内容。"
            "不要用“画面不断变化”“出现人物”“屏幕上有内容”这类空泛描述替代具体观察，也不要写成冷冰冰的监控日志。"
            "current_state 写当前最后一帧能确认的具体状态，例如停在某个网页、编辑器、游戏战斗/菜单、视频画面、聊天窗口。"
            "visible_text 写 0 到 6 条能看清的屏幕文字、标题、按钮、文件名或代码关键词；看不清就不要猜。"
            "concrete_details 写 2 到 6 条可见细节，例如左侧列表、右侧视频推荐、终端输出、卡牌名称、角色姿势、弹窗内容。"
            "changes 写 0 到 4 条这几帧之间发生的具体变化。"
            "topics 和 mood_tags 各写 1 到 6 个短词。"
            "salience 是 0 到 1，表示这段画面多值得当前前台角色主动轻轻提一句；只有能看出具体内容时才给 0.45 以上。"
            "如果只能确认很泛的东西，请 summary 明确写“看不清具体内容，只能确认……”，salience 不超过 0.2。"
            "如果像密码、隐私聊天、支付、证件、敏感个人信息，sensitive 设为 true，summary 只写泛化描述，不要复述细节。"
            "confidence 是 0 到 1。不确定的内容放进 uncertainty。"
            "不要臆造看不见的剧情，不要输出 JSON 之外的文字。"
        )

    def _build_screen_clip_user_instruction(
        self,
        context: dict[str, Any],
        frames: list[dict[str, Any]],
    ) -> str:
        foreground = context.get("foreground") if isinstance(context.get("foreground"), dict) else {}
        title = str(foreground.get("title") or "").strip()
        process_name = str(foreground.get("process_name") or foreground.get("processName") or "").strip()
        start_ts = int(context.get("captured_start_ts") or 0)
        end_ts = int(context.get("captured_end_ts") or 0)
        duration = max(0, end_ts - start_ts)
        lines = [
            f"这是一组连续看见的近况，共 {len(frames)} 次。",
        ]
        if duration:
            lines.append(f"大约覆盖 {duration} 秒。")
        if title or process_name:
            label = title or "未知标题"
            if process_name:
                label += f"（{process_name}）"
            lines.append(f"当前窗口线索：{label}")
        lines.append("请串联这些瞬间，描述这几秒里主人正在看的具体内容。")
        lines.append("优先提取可读文字、窗口标题、网页标题、按钮、代码关键词、游戏界面、视频标题、卡片/角色/弹窗等具体证据。")
        lines.append("如果看不清，请直接承认看不清，不要用空泛描述假装有信息。")
        lines.append("请输出严格 JSON。")
        return "\n".join(lines)

    def _current_prompt_version(self) -> str:
        base_version = str(getattr(config, "VISION_PROMPT_VERSION", "v1") or "v1").strip() or "v1"
        if base_version.endswith(f"-{PROMPT_STYLE_REVISION}"):
            return base_version
        return f"{base_version}-{PROMPT_STYLE_REVISION}"

    def _coerce_response_text(self, response: Any) -> str:
        try:
            choice = response.choices[0]
        except Exception:
            return ""
        message = getattr(choice, "message", None)
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and str(item.get("type") or "").strip() == "text":
                    text = str(item.get("text") or "").strip()
                    if text:
                        parts.append(text)
            return "\n".join(parts).strip()
        return str(content or "").strip()

    def _extract_json_dict(self, text: str) -> dict[str, Any]:
        raw = str(text or "").strip()
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            match = JSON_RE.search(raw)
            if match:
                try:
                    payload = json.loads(match.group(0))
                    return payload if isinstance(payload, dict) else {}
                except Exception:
                    return {}
        return {}

    def _normalize_observation_card(self, payload: dict[str, Any], *, observation_type: str) -> dict[str, Any]:
        def _list(key: str, fallback_key: str = "") -> list[str]:
            raw = payload.get(key)
            if raw is None and fallback_key:
                raw = payload.get(fallback_key)
            if isinstance(raw, list):
                result = [str(item).strip() for item in raw if str(item).strip()]
            elif isinstance(raw, str):
                result = [part.strip() for part in raw.split(",") if part.strip()]
            else:
                result = []
            deduped: list[str] = []
            for item in result:
                if item not in deduped:
                    deduped.append(item)
            return deduped[:6]

        summary = str(payload.get("summary") or "").strip()
        card = {
            "type": f"{observation_type}_observation",
            "summary": summary[:220],
            "entities": _list("entities", fallback_key="salient_entities"),
            "mood_tags": _list("mood_tags"),
            "uncertainty": _list("uncertainty"),
        }
        if observation_type == "attachment_image":
            title = str(payload.get("summary_title") or payload.get("title") or "").strip()
            card["summary_title"] = title[:40]
        if observation_type == "outfit":
            card["appearance_traits"] = _list("appearance_traits", fallback_key="salient_traits")
        return card

    def _normalize_screen_clip_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
        def _list(key: str) -> list[str]:
            raw = payload.get(key)
            if isinstance(raw, list):
                items = [str(item).strip() for item in raw if str(item).strip()]
            elif isinstance(raw, str):
                items = [part.strip() for part in raw.replace("，", ",").split(",") if part.strip()]
            else:
                items = []
            deduped: list[str] = []
            for item in items:
                if item not in deduped:
                    deduped.append(item)
            return deduped[:6]

        def _float(key: str, default: float) -> float:
            try:
                value = float(payload.get(key))
            except Exception:
                value = default
            return max(0.0, min(1.0, value))

        return {
            "summary": str(payload.get("summary") or "").strip()[:260],
            "current_state": str(payload.get("current_state") or payload.get("state") or "").strip()[:220],
            "visible_text": _list("visible_text")[:6],
            "concrete_details": _list("concrete_details")[:6],
            "changes": _list("changes")[:4],
            "topics": _list("topics"),
            "mood_tags": _list("mood_tags"),
            "salience": _float("salience", 0.0),
            "sensitive": bool(payload.get("sensitive")),
            "confidence": _float("confidence", 0.5),
            "uncertainty": _list("uncertainty")[:4],
        }

    def _format_observation_prompt(self, *, heading: str, observation: dict[str, Any]) -> str:
        card = dict(observation.get("observation") or {})
        lines = [heading]
        summary = str(card.get("summary") or observation.get("summary") or "").strip()
        if summary:
            lines.append(f"- summary: {summary}")
        appearance_traits = [str(item).strip() for item in card.get("appearance_traits") or [] if str(item).strip()]
        if appearance_traits:
            lines.append(f"- appearance_traits: {', '.join(appearance_traits)}")
        entities = [str(item).strip() for item in card.get("entities") or [] if str(item).strip()]
        if entities:
            lines.append(f"- entities: {', '.join(entities)}")
        mood_tags = [str(item).strip() for item in card.get("mood_tags") or [] if str(item).strip()]
        if mood_tags:
            lines.append(f"- mood_tags: {', '.join(mood_tags)}")
        uncertainty = [str(item).strip() for item in card.get("uncertainty") or [] if str(item).strip()]
        if uncertainty:
            lines.append(f"- uncertainty: {', '.join(uncertainty)}")
        return "\n".join(lines)

    def _provider_label(self) -> str:
        if self._client is None:
            return ""
        return str(getattr(self._client, "protocol", "openai_compat") or "openai_compat")

    def _job_key(self, target: VisionTarget) -> str:
        return f"{target.observation_type}:{target.resource_fingerprint}:{target.prompt_version}"

    def _fingerprint_file(self, path: Path) -> str:
        payload = path.read_bytes()
        stat = path.stat()
        digest = hashlib.sha1()
        digest.update(payload)
        digest.update(str(int(stat.st_mtime)).encode("utf-8"))
        digest.update(str(int(stat.st_size)).encode("utf-8"))
        return digest.hexdigest()

    def _guess_media_type(self, path: Path) -> str:
        suffix = path.suffix.lower()
        return {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(suffix, "image/png")
