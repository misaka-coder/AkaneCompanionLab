from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Generator

import config
from akane_paths import get_akane_data_paths

from .artifact_system import ArtifactContainerService
from .attachment_inbox import AttachmentInboxService
from .attachment_ingest import AttachmentIngestService
from .background_tasks import BackgroundTaskRunner
from .capability_adapters import CapabilityAdapterRegistry, McpStdioCapabilityAdapter
from .capability_registry import CapabilityRegistry, CapabilitySelection, CapabilitySnapshot, is_document_attachment, is_document_generated_file, is_media_attachment, is_media_generated_file
from . import desktop_pet_engine
from .embedding_provider import BaseEmbeddingProvider, CachedEmbeddingProvider, HashedEmbeddingProvider
from .generated_files import GeneratedFileService
from . import gift_engine
from .gift_system import GiftSystemService
from . import media_bridge_engine
from .huggingface_provider import HuggingFaceEmbeddingProvider
from .llm_runtime import LLMRuntime
from .memory_compaction_service import MemoryCompactionService
from .memory_rendering import render_semantic_summary_timeline, render_summary_timeline
from .memory_timeline import MemoryTimelineService
from .client_protocol import ClientCapability, ClientMode, ClientProtocolContext
from .care_runtime import CareRuntimeStore
from .desktop_music_timeline import DesktopMusicTimelineService
from .desktop_screen_vision import DesktopScreenVisionWorkspace
from . import desktop_context_engine
from .mode_profiles import ModeProfileRegistry
from .npc_runtime import GenericNPCRuntime
from .output_adapters import OutputAdapterRegistry
from .persona_config import PERSONA
from .persona_system import PersonaCardService
from .prompt_builder import PromptBuilder
from .prompt_profiles import PromptModule, PromptProfileRegistry
from . import final_output_engine
from .local_capability_config import load_capability_config
from . import reminder_engine
from .retrieval_service import RetrievalService
from .retrieval_types import RetrievalPipelineResult
from . import retrieval_engine
from .resource_manifest import ResourceManifest
from .sticker_assets import StickerAssetService
from .task_workspace import TaskWorkspaceService
from . import task_workspace_engine
from .task_worker import TaskWorkerService
from .task_worker_tool import DelegateTaskToolHandler
from . import tool_orchestration_engine
from .tool_invocation import NATIVE_TOOL_CALL_FIELD
from .tool_runtime import AdapterCapabilityToolHandler, ApplyStyleToExistingFileToolHandler, BaseToolHandler, BrowserPageToolHandler, CallNPCToolHandler, CancelReminderToolHandler, CheckInventoryToolHandler, CleanVoiceTrackToolHandler, ClearAttachmentFocusToolHandler, ComposeFileToolHandler, ConvertMediaFileToolHandler, FetchMediaFromUrlToolHandler, FocusWorkspaceToolHandler, InspectAttachmentToolHandler, InspectGeneratedFileToolHandler, InspectMediaInfoToolHandler, ListRemindersToolHandler, ListWorkspaceToolHandler, LoadCharacterContextToolHandler, ManageArtifactToolHandler, ManageGeneratedFileToolHandler, ManageGiftToolHandler, ManagePersonaToolHandler, ManageTaskWorkspaceToolHandler, OpenBrowserToolHandler, OpenMusicSearchToolHandler, PrepareVoiceDatasetToolHandler, ReadAttachmentSectionToolHandler, ReadMemoryTimelineToolHandler, ReadWorkspaceToolHandler, RegisterWorkspaceItemsToolHandler, RetrieveMemoryToolHandler, ReviseGeneratedFileToolHandler, RetryAttachmentToolHandler, SendFileToolHandler, SendGeneratedFileToolHandler, SendStickerToolHandler, SeparateAudioStemsToolHandler, SetReminderToolHandler, SyncAttachmentWorkspaceToolHandler, ToolExecutionContext, ToolExecutionResult, TranscribeMediaToolHandler, WebSearchToolHandler
from . import visual_context_engine
from .vision_service import VisionObservationService
from .store import MemoryStore
from .text_utils import (
    detect_time_of_day_from_text,
    extract_semantic_tags,
    infer_time_of_day,
    normalize_text,
    parse_joined_tags,
    render_chat_line,
    render_chat_timeline,
    timestamp_to_datetime_label,
    timestamp_to_date_label,
)
from .vector_entry_builder import (
    build_raw_vector_entry,
    build_semantic_summary_vector_entry,
    build_summary_vector_entry,
)
from .vector_store import VectorStore
from .vision_observation_router import VisionObservationRouter
from .workspace_files import WorkspaceFileService

logger = logging.getLogger("akane.engine")

MEDIA_PRESET_ROUTING = [
    "【媒体任务预设路由】",
    "- 生成字幕 → transcribe_media output_format=srt/vtt",
    "- 转写文字稿/会议纪要前置 → transcribe_media output_format=md/txt",
    "- 提取视频音频 → convert_media_file output_format=mp3/wav",
    "- 压缩音频/减小体积 → convert_media_file bitrate（如 128k/192k）",
    "- 截取片段 → convert_media_file start_time/end_time",
    "- 声音忽大忽小 → convert_media_file normalize_volume=true",
    "- 声音太小 → convert_media_file volume_gain_db 正数",
    "- 声音太大 → convert_media_file volume_gain_db 负数",
    "- 人声降噪/去混响 → clean_voice_track",
    "- 人声伴奏分离 → separate_audio_stems",
    "- 训练素材切片打包 → prepare_voice_dataset",
    "- 只要原文件不处理 → send_file，不要转写/转码/净化",
    "",
    "涉及大小、码率、分辨率、时长、格式兼容等具体约束时，先 inspect_media_info 查当前规格，再决定 convert_media_file 参数。",
    "人声处理组合：需要人声/伴奏分离时先 separate_audio_stems；需要更干净人声时，再对 vocals 结果调用 clean_voice_track。",
]


class AkaneMemoryEngine:
    def __init__(
        self,
        base_dir: Path,
        resource_manifest: ResourceManifest | None = None,
        desktop_pet_character_resources: Any = None,
    ):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.resource_manifest = resource_manifest
        self.desktop_pet_character_resources = desktop_pet_character_resources
        self.care_runtime = CareRuntimeStore(self.base_dir / "care_runtime.json")
        self.store = MemoryStore(self.base_dir)
        self.embedding_provider = self._build_embedding_provider()
        self.vector_store = VectorStore(
            self.base_dir / "chroma",
            embedding_provider=self.embedding_provider,
        )
        self.llm = LLMRuntime()
        self.gift_service = GiftSystemService(
            self.base_dir / "user_assets",
            store=self.store,
            llm=self.llm,
        )
        self.background_tasks = BackgroundTaskRunner(
            {
                "attachment": int(getattr(config, "BACKGROUND_ATTACHMENT_WORKERS", 3) or 3),
                "timeline": int(getattr(config, "BACKGROUND_TIMELINE_WORKERS", 1) or 1),
            },
            default_workers=int(getattr(config, "BACKGROUND_DEFAULT_WORKERS", 1) or 1),
        )
        self.memory_timeline_service = MemoryTimelineService(
            store=self.store,
            root_dir=self.base_dir / "memory",
            characters_dir=getattr(
                self.desktop_pet_character_resources,
                "characters_dir",
                None,
            ),
            background_tasks=self.background_tasks,
        )
        self.store.set_message_write_callback(
            self.memory_timeline_service.handle_message_write
        )
        self.memory_timeline_service.schedule_existing_backfill()
        self.workspace_file_service = WorkspaceFileService(
            root_dir=getattr(config, "AKANE_WORKSPACE_ROOT", ""),
            store=self.store,
            max_read_bytes=int(
                getattr(config, "AKANE_WORKSPACE_MAX_READ_BYTES", 64 * 1024 * 1024)
                or (64 * 1024 * 1024)
            ),
        )
        attachment_workspace_dir = self.workspace_file_service.layer_dir("Inbox")
        generated_workspace_dir = self.workspace_file_service.layer_dir("Outputs")
        self.attachment_inbox_service = AttachmentInboxService(
            store=self.store,
            base_dir=attachment_workspace_dir,
            legacy_base_dirs=[self.base_dir / "attachment_inbox_files"],
            workspace_uri_resolver=self.workspace_file_service.resolve_file_uri,
        )
        if getattr(config, "VISION_ENABLED", True):
            self.vision_observation_router: VisionObservationRouter | None = VisionObservationRouter(
                store=self.store,
                gift_service=self.gift_service,
                attachment_service=self.attachment_inbox_service,
            )
        else:
            self.vision_observation_router = None
        self.artifact_service = ArtifactContainerService(
            store=self.store,
            public_path_builder=self.gift_service._build_public_path,
        )
        self.persona_card_service = PersonaCardService(store=self.store)
        self.task_workspace_service = TaskWorkspaceService(store=self.store)
        self.generated_file_service = GeneratedFileService(
            base_dir=generated_workspace_dir,
            store=self.store,
            attachment_service=self.attachment_inbox_service,
            legacy_base_dirs=[self.base_dir / "generated_files"],
            ensure_storage_ready=self.workspace_file_service.ensure_layout,
            work_dir=self.base_dir / "generated_work",
        )
        self.desktop_music_timeline_service = DesktopMusicTimelineService(
            store=self.store,
            generated_file_service=self.generated_file_service,
            background_tasks=self.background_tasks,
        )
        self.gift_assets = self.gift_service
        self.npc_runtime = GenericNPCRuntime(self.base_dir / "generic_npc_memory_v01", self.llm)
        sticker_assets_dir = (
            Path(getattr(resource_manifest, "assets_dir"))
            if resource_manifest is not None and getattr(resource_manifest, "assets_dir", None)
            else Path(__file__).resolve().parent.parent / "web" / "assets"
        )
        self.sticker_assets = StickerAssetService(assets_dir=sticker_assets_dir)
        if getattr(config, "VISION_ENABLED", True):
            self.vision_service: VisionObservationService | None = VisionObservationService(
                self.base_dir / "vision_cache",
                store=self.store,
                resource_manifest=self.resource_manifest,
                gift_assets_dir=self.base_dir / "user_assets",
                on_observation_ready=(
                    self.vision_observation_router.handle
                    if self.vision_observation_router is not None
                    else None
                ),
            )
            self.desktop_screen_vision: DesktopScreenVisionWorkspace | None = DesktopScreenVisionWorkspace(
                vision_service=self.vision_service,
                max_ready_per_session=int(getattr(config, "DESKTOP_SCREEN_VISION_MAX_CLIPS", 5) or 5),
                ttl_sec=int(getattr(config, "DESKTOP_SCREEN_VISION_TTL_SEC", 15 * 60) or (15 * 60)),
            )
        else:
            self.vision_service = None
            self.desktop_screen_vision = None
        self.attachment_ingest_service = AttachmentIngestService(
            base_dir=attachment_workspace_dir,
            store=self.store,
            attachment_service=self.attachment_inbox_service,
            vision_service=self.vision_service,
            background_tasks=self.background_tasks,
            legacy_base_dirs=[self.base_dir / "attachment_inbox_files"],
            ensure_storage_ready=self.workspace_file_service.ensure_layout,
            workspace_uri_resolver=self.workspace_file_service.resolve_file_uri,
        )
        self.prompt_builder = PromptBuilder(PERSONA)
        self.mode_profile_registry = ModeProfileRegistry()
        self.prompt_profile_registry = PromptProfileRegistry()
        self.output_adapters = OutputAdapterRegistry()
        self.retrieval_service = RetrievalService(
            store=self.store,
            vector_store=self.vector_store,
            llm=self.llm,
            prompt_builder=self.prompt_builder,
        )
        self.compaction_service = MemoryCompactionService(
            store=self.store,
            vector_store=self.vector_store,
            llm=self.llm,
            prompt_builder=self.prompt_builder,
            persona_context_provider=self._build_memory_compaction_persona_context,
        )
        self.task_worker_service = TaskWorkerService(
            llm=self.llm,
            task_workspace_service=self.task_workspace_service,
            background_tasks=self.background_tasks,
            tool_handlers_provider=lambda: getattr(self, "tool_handlers", {}) or {},
            attachment_context_builder=self._build_task_worker_attachment_context,
            generated_context_builder=self._build_task_worker_generated_context,
            record_tool_artifacts=self._record_tool_result_artifacts_in_task_workspace,
        )
        self.tool_handlers = self._build_tool_handlers()
        self.capability_registry = CapabilityRegistry()
        self._embedding_reindex_lock = threading.RLock()
        self._embedding_reindex_thread: threading.Thread | None = None
        self._embedding_reindex_status = {
            "state": "idle",
            "processed": 0,
            "total": 0,
            "started_at": 0.0,
            "finished_at": 0.0,
            "error": "",
            "collection_name": str(self.vector_store.collection_name),
        }
        self.capability_adapter_registry = CapabilityAdapterRegistry(
            builtin_dir=Path(__file__).parent / "builtin_capability_manifests",
            profile_dir_provider=self._resolve_profile_capability_manifests_dir,
        )
        self.capability_adapter_registry.scan()
        self._maybe_start_embedding_reindex()

    def _resolve_profile_capability_manifests_dir(self) -> Path:
        profile_user_id = str(getattr(self, "profile_user_id", "") or "").strip()
        if not profile_user_id:
            return get_akane_data_paths().users_data / ".no_active_profile" / "capability_manifests"
        return get_akane_data_paths().users_data / profile_user_id / "capability_manifests"

    def reset(self) -> None:
        self._get_compaction_service().reset()
        self.store.reset()
        self.memory_timeline_service.clear_mirror()
        self.vector_store.reset()
        self.gift_service.reset()
        if self.vision_service is not None:
            self.vision_service.reset()
        if self.desktop_screen_vision is not None:
            self.desktop_screen_vision.reset()
        self.npc_runtime.reset()

    def reload_model_services(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "llm": self.llm.reload_from_config(),
            "vision": {"status": "disabled"},
        }
        if self.vision_service is not None:
            result["vision"] = self.vision_service.reload_client()
        return result

    def build_resource_manifest(
        self,
        *,
        profile_user_id: str = "",
        client_mode: str = "",
        character_pack_id: str = "",
    ) -> dict[str, Any]:
        resource_manifest = self._resolve_resource_manifest_for_client(
            client_mode=client_mode,
            character_pack_id=character_pack_id,
        )
        return visual_context_engine.build_resource_manifest(
            self,
            profile_user_id=profile_user_id,
            resource_manifest=resource_manifest,
        )

    def list_gift_assets(self, *, profile_user_id: str, media_kind: str = "all", limit: int = 50) -> list[dict[str, Any]]:
        return gift_engine.list_gift_assets(
            self,
            profile_user_id=profile_user_id,
            media_kind=media_kind,
            limit=limit,
        )

    def upload_gift_asset(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        filename: str,
        content_type: str,
        content: bytes,
        now_ts: int | None = None,
    ) -> dict[str, Any]:
        return gift_engine.upload_gift_asset(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            filename=filename,
            content_type=content_type,
            content=content,
            now_ts=now_ts,
        )

    def apply_gift_action(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
        asset_id: str,
        action: str,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        return gift_engine.apply_gift_action(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            asset_id=asset_id,
            action=action,
            timestamp=timestamp,
        )

    def observe_gift_image_once(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        asset_id: str,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        return gift_engine.observe_gift_image_once(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            asset_id=asset_id,
            timestamp=timestamp,
        )

    def list_gift_inventory(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        scope: str = "pending_recent",
        limit: int = 5,
    ) -> dict[str, Any]:
        return gift_engine.list_gift_inventory(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            scope=scope,
            limit=limit,
        )

    def list_artifact_containers(
        self,
        *,
        profile_user_id: str,
        preview_limit: int = 3,
        include_empty: bool = True,
    ) -> list[dict[str, Any]]:
        return gift_engine.list_artifact_containers(
            self,
            profile_user_id=profile_user_id,
            preview_limit=preview_limit,
            include_empty=include_empty,
        )

    def list_artifacts_in_container(
        self,
        *,
        profile_user_id: str,
        container_type: str,
        container_key: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        return gift_engine.list_artifacts_in_container(
            self,
            profile_user_id=profile_user_id,
            container_type=container_type,
            container_key=container_key,
            limit=limit,
        )

    def close(self) -> None:
        self._get_compaction_service().close()
        background_tasks = getattr(self, "background_tasks", None)
        if background_tasks is not None:
            background_tasks.close()

    def snapshot_embedding_reindex_status(self) -> dict[str, Any]:
        with self._embedding_reindex_lock:
            return dict(self._embedding_reindex_status)

    def _build_embedding_provider(self) -> BaseEmbeddingProvider:
        provider_mode = str(getattr(config, "EMBEDDING_PROVIDER", "auto") or "auto").strip().lower() or "auto"
        base_provider: BaseEmbeddingProvider = HashedEmbeddingProvider()
        if provider_mode in {"auto", "huggingface", "hf", "sentence-transformer", "sentence-transformers"}:
            try:
                base_provider = HuggingFaceEmbeddingProvider(
                    model_name=str(getattr(config, "EMBEDDING_MODEL_NAME", "") or "BAAI/bge-m3"),
                    device=str(getattr(config, "EMBEDDING_DEVICE", "") or "").strip() or None,
                    local_files_only=bool(getattr(config, "EMBEDDING_LOCAL_FILES_ONLY", True)),
                    cache_folder=str(getattr(config, "EMBEDDING_CACHE_FOLDER", "") or "").strip() or None,
                    hf_endpoint=str(getattr(config, "HF_ENDPOINT", "") or "").strip() or None,
                )
            except Exception:
                base_provider = HashedEmbeddingProvider()
        if int(getattr(config, "EMBEDDING_CACHE_SIZE", 0) or 0) > 0:
            return CachedEmbeddingProvider(
                base_provider,
                max_entries=int(getattr(config, "EMBEDDING_CACHE_SIZE", 0) or 0),
            )
        return base_provider

    def _maybe_start_embedding_reindex(self) -> None:
        total_records = self.store.count_vectorizable_records()
        current_entries = self.vector_store.count_entries()
        if total_records <= 0 or current_entries >= total_records:
            with self._embedding_reindex_lock:
                self._embedding_reindex_status.update(
                    {
                        "state": "idle",
                        "processed": int(current_entries),
                        "total": int(total_records),
                        "started_at": 0.0,
                        "finished_at": time.time() if total_records <= 0 else 0.0,
                        "error": "",
                        "collection_name": str(self.vector_store.collection_name),
                    }
                )
            return

        with self._embedding_reindex_lock:
            thread = self._embedding_reindex_thread
            if thread is not None and thread.is_alive():
                return
            self._embedding_reindex_status.update(
                {
                    "state": "running",
                    "processed": 0,
                    "total": int(total_records),
                    "started_at": time.time(),
                    "finished_at": 0.0,
                    "error": "",
                    "collection_name": str(self.vector_store.collection_name),
                }
            )
            self._embedding_reindex_thread = threading.Thread(
                target=self._run_embedding_reindex,
                name="akane-embedding-reindex",
                daemon=True,
            )
            self._embedding_reindex_thread.start()
        logger.info(
            "Akane 正在后台悄悄整理以前的回忆哦，可能需要稍微花一点点时间～ "
            f"(collection={self.vector_store.collection_name}, current={current_entries}, total={total_records})"
        )

    def _run_embedding_reindex(self) -> None:
        batch_size = max(1, int(getattr(config, "EMBEDDING_REINDEX_BATCH_SIZE", 64) or 64))
        processed = 0
        try:
            batch_iterators = (
                (self.store.iter_messages_for_vector_reindex(batch_size), build_raw_vector_entry),
                (self.store.iter_summaries_for_vector_reindex(batch_size), build_summary_vector_entry),
                (self.store.iter_semantic_summaries_for_vector_reindex(batch_size), build_semantic_summary_vector_entry),
            )
            for batches, entry_builder in batch_iterators:
                for record_batch in batches:
                    entries = [entry_builder(record) for record in record_batch]
                    if not entries:
                        continue
                    self.vector_store.upsert_entries(entries)
                    processed += len(entries)
                    with self._embedding_reindex_lock:
                        self._embedding_reindex_status["processed"] = int(processed)
            with self._embedding_reindex_lock:
                self._embedding_reindex_status.update(
                    {
                        "state": "completed",
                        "processed": int(self.store.count_vectorizable_records()),
                        "total": int(self.store.count_vectorizable_records()),
                        "finished_at": time.time(),
                        "error": "",
                    }
                )
            logger.info(
                "Akane 的回忆整理完成啦～ "
                f"(collection={self.vector_store.collection_name}, total={self._embedding_reindex_status['total']})"
            )
        except Exception as exc:
            with self._embedding_reindex_lock:
                self._embedding_reindex_status.update(
                    {
                        "state": "error",
                        "processed": int(processed),
                        "finished_at": time.time(),
                        "error": str(exc),
                    }
                )
            logger.exception("Embedding reindex failed: %s", exc)

    def _get_prompt_builder(self) -> PromptBuilder:
        prompt_builder = getattr(self, "prompt_builder", None)
        if prompt_builder is None:
            prompt_builder = PromptBuilder(PERSONA)
            self.prompt_builder = prompt_builder
        return prompt_builder

    def _get_mode_profile_registry(self) -> ModeProfileRegistry:
        registry = getattr(self, "mode_profile_registry", None)
        if registry is None:
            registry = ModeProfileRegistry()
            self.mode_profile_registry = registry
        return registry

    def _get_prompt_profile_registry(self) -> PromptProfileRegistry:
        registry = getattr(self, "prompt_profile_registry", None)
        if registry is None:
            registry = PromptProfileRegistry()
            self.prompt_profile_registry = registry
        return registry

    def _get_output_adapter_registry(self) -> OutputAdapterRegistry:
        registry = getattr(self, "output_adapters", None)
        if registry is None:
            registry = OutputAdapterRegistry()
            self.output_adapters = registry
        return registry

    def _resolve_client_protocol_context(self, payload: dict[str, Any] | None) -> ClientProtocolContext:
        return self._get_mode_profile_registry().resolve_from_payload(payload)

    def _resolve_resource_manifest_for_client(
        self,
        *,
        client_mode: str = "",
        character_pack_id: str = "",
    ) -> ResourceManifest | None:
        raw_mode = client_mode.value if isinstance(client_mode, ClientMode) else str(client_mode or "").strip()
        character_pack_mode = raw_mode in {
            ClientMode.DESKTOP_PET.value,
            ClientMode.QQ_TEXT.value,
        }
        if not character_pack_mode:
            return self.resource_manifest

        service = getattr(self, "desktop_pet_character_resources", None)
        if service is None:
            return None if raw_mode == ClientMode.QQ_TEXT.value else self.resource_manifest
        manifest = service.get_manifest(character_pack_id) if character_pack_id else None
        if raw_mode == ClientMode.QQ_TEXT.value:
            return manifest
        return manifest or self.resource_manifest

    def _resolve_turn_resource_manifest(
        self,
        payload: dict[str, Any],
        client_context: ClientProtocolContext,
    ) -> ResourceManifest | None:
        if client_context.effective_mode not in {
            ClientMode.DESKTOP_PET,
            ClientMode.QQ_TEXT,
        }:
            return self.resource_manifest
        return self._resolve_resource_manifest_for_client(
            client_mode=client_context.effective_mode.value,
            character_pack_id=self._resolve_payload_character_pack_id(payload),
        )

    @staticmethod
    def _resolve_payload_character_pack_id(payload: dict[str, Any]) -> str:
        for key in ("character_pack_id", "characterPackId", "character_pack"):
            value = str((payload or {}).get(key) or "").strip()
            if value:
                return value
        current_visual = (payload or {}).get("current_visual")
        if isinstance(current_visual, dict):
            for key in ("character_pack_id", "characterPackId", "character_pack"):
                value = str(current_visual.get(key) or "").strip()
                if value:
                    return value
            character = current_visual.get("character")
            if isinstance(character, dict):
                for key in ("character_pack_id", "characterPackId", "character_pack", "pack_id"):
                    value = str(character.get(key) or "").strip()
                    if value:
                        return value
        return ""

    def _resolve_turn_speaker_identity(
        self,
        client_context: ClientProtocolContext,
        character_pack_id: str,
    ) -> dict[str, str]:
        """Resolve display speaker identity for the current turn.

        DesktopPet / QQ text modes with a valid character pack  →  character pack identity.
        Other modes  →  persona_profiles.toml defaults (PERSONA).
        """
        if (
            client_context is not None
            and client_context.effective_mode in {ClientMode.DESKTOP_PET, ClientMode.QQ_TEXT}
        ):
            service = getattr(self, "desktop_pet_character_resources", None)
            if service is not None and character_pack_id:
                identity_builder = getattr(service, "build_character_identity", None)
                if identity_builder is not None:
                    try:
                        identity = identity_builder(character_pack_id)
                    except Exception:
                        identity = {}
                    if identity:
                        return {
                            "character_id": str(identity.get("character_id") or identity.get("pack_id") or ""),
                            "assistant_name": str(identity.get("assistant_name") or ""),
                            "user_label": str(identity.get("user_label") or ""),
                            "app_name": str(identity.get("app_name") or ""),
                            "pack_id": str(identity.get("pack_id") or ""),
                        }

        return {
            "character_id": "",
            "assistant_name": PERSONA.assistant_name,
            "user_label": PERSONA.user_label,
            "app_name": PERSONA.assistant_name,
            "pack_id": "",
        }

    def _build_desktop_pet_character_pack_prompt_context(
        self,
        *,
        character_pack_id: str,
        resource_manifest: ResourceManifest | None = None,
        client_mode: str = ClientMode.DESKTOP_PET.value,
    ) -> dict[str, str]:
        service = getattr(self, "desktop_pet_character_resources", None)
        if service is None or not character_pack_id:
            return {"system_context": "", "reference_context": "", "active_id": ""}
        builder = getattr(service, "build_persona_prompt_context", None)
        if builder is None:
            return {"system_context": "", "reference_context": "", "active_id": ""}
        try:
            context = builder(
                character_pack_id,
                resource_manifest=resource_manifest,
                client_mode=client_mode,
            )
        except Exception as exc:
            logger.warning("desktop pet character pack prompt context failed: %s", exc)
            return {"system_context": "", "reference_context": "", "active_id": ""}
        return context if isinstance(context, dict) else {"system_context": "", "reference_context": "", "active_id": ""}

    @staticmethod
    def _merge_prompt_persona_contexts(*contexts: dict[str, Any]) -> dict[str, str]:
        system_parts: list[str] = []
        reference_parts: list[str] = []
        active_id = ""
        for context in contexts:
            if not isinstance(context, dict):
                continue
            system_context = str(context.get("system_context") or "").strip()
            reference_context = str(context.get("reference_context") or "").strip()
            current_active_id = str(context.get("active_id") or "").strip()
            if system_context:
                system_parts.append(system_context)
            if reference_context:
                reference_parts.append(reference_context)
            if current_active_id:
                active_id = current_active_id
        return {
            "system_context": "\n\n".join(system_parts),
            "reference_context": "\n\n".join(reference_parts),
            "active_id": active_id,
        }

    def _get_persona_card_service(self) -> PersonaCardService | None:
        service = getattr(self, "persona_card_service", None)
        if service is not None:
            return service
        store = getattr(self, "store", None)
        if store is None:
            return None
        service = PersonaCardService(store=store)
        self.persona_card_service = service
        return service

    def _build_memory_compaction_persona_context(
        self,
        *,
        profile_user_id: str = "",
        session_id: str = "",
        character_pack_id: str = "",
    ) -> dict[str, str]:
        contexts: list[dict[str, Any]] = []
        if character_pack_id:
            contexts.append(
                self._build_desktop_pet_character_pack_prompt_context(
                    character_pack_id=character_pack_id,
                    resource_manifest=None,
                    client_mode="memory",
                )
            )
        persona_service = self._get_persona_card_service() if not character_pack_id else None
        if persona_service is not None and profile_user_id and session_id:
            try:
                contexts.append(
                    persona_service.build_prompt_context(
                        profile_user_id=profile_user_id,
                        session_id=session_id,
                        visible_limit=5,
                    )
                )
            except Exception as exc:
                logger.warning("memory compaction persona context failed: %s", exc)
        return self._merge_prompt_persona_contexts(*contexts)

    def _get_task_workspace_service(self) -> TaskWorkspaceService | None:
        service = getattr(self, "task_workspace_service", None)
        if service is not None:
            return service
        store = getattr(self, "store", None)
        if store is None:
            return None
        service = TaskWorkspaceService(store=store)
        self.task_workspace_service = service
        return service

    def _get_task_worker_service(self) -> TaskWorkerService | None:
        service = getattr(self, "task_worker_service", None)
        if service is not None:
            return service
        task_workspace_service = self._get_task_workspace_service()
        if task_workspace_service is None:
            return None
        service = TaskWorkerService(
            llm=self.llm,
            task_workspace_service=task_workspace_service,
            background_tasks=getattr(self, "background_tasks", None),
            tool_handlers_provider=lambda: getattr(self, "tool_handlers", {}) or {},
            attachment_context_builder=self._build_task_worker_attachment_context,
            generated_context_builder=self._build_task_worker_generated_context,
            record_tool_artifacts=self._record_tool_result_artifacts_in_task_workspace,
        )
        self.task_worker_service = service
        return service

    def _get_attachment_inbox_service(self) -> AttachmentInboxService | None:
        service = getattr(self, "attachment_inbox_service", None)
        if service is not None:
            return service
        store = getattr(self, "store", None)
        if store is None:
            return None
        workspace_service = self._get_workspace_file_service()
        base_dir = (
            workspace_service.layer_dir("Inbox")
            if workspace_service is not None
            else self.base_dir / "attachment_inbox_files"
        )
        service = AttachmentInboxService(
            store=store,
            base_dir=base_dir,
            legacy_base_dirs=[self.base_dir / "attachment_inbox_files"],
            workspace_uri_resolver=workspace_service.resolve_file_uri if workspace_service is not None else None,
        )
        self.attachment_inbox_service = service
        return service

    def _get_attachment_ingest_service(self) -> AttachmentIngestService | None:
        service = getattr(self, "attachment_ingest_service", None)
        if service is not None:
            return service
        store = getattr(self, "store", None)
        vision_service = getattr(self, "vision_service", None)
        if store is None:
            return None
        attachment_service = self._get_attachment_inbox_service()
        if attachment_service is None:
            return None
        workspace_service = self._get_workspace_file_service()
        base_dir = (
            workspace_service.layer_dir("Inbox")
            if workspace_service is not None
            else self.base_dir / "attachment_inbox_files"
        )
        service = AttachmentIngestService(
            base_dir=base_dir,
            store=store,
            attachment_service=attachment_service,
            vision_service=vision_service,
            background_tasks=getattr(self, "background_tasks", None),
            legacy_base_dirs=[self.base_dir / "attachment_inbox_files"],
            ensure_storage_ready=workspace_service.ensure_layout if workspace_service is not None else None,
            workspace_uri_resolver=workspace_service.resolve_file_uri if workspace_service is not None else None,
        )
        self.attachment_ingest_service = service
        return service

    def _get_workspace_file_service(self) -> WorkspaceFileService | None:
        service = getattr(self, "workspace_file_service", None)
        if service is not None:
            return service
        store = getattr(self, "store", None)
        if store is None:
            return None
        service = WorkspaceFileService(
            root_dir=getattr(config, "AKANE_WORKSPACE_ROOT", ""),
            store=store,
            max_read_bytes=int(
                getattr(config, "AKANE_WORKSPACE_MAX_READ_BYTES", 64 * 1024 * 1024)
                or (64 * 1024 * 1024)
            ),
        )
        self.workspace_file_service = service
        return service

    def _get_generated_file_service(self) -> GeneratedFileService | None:
        service = getattr(self, "generated_file_service", None)
        if service is not None:
            return service
        store = getattr(self, "store", None)
        if store is None:
            return None
        attachment_service = self._get_attachment_inbox_service()
        if attachment_service is None:
            return None
        workspace_service = self._get_workspace_file_service()
        base_dir = (
            workspace_service.layer_dir("Outputs")
            if workspace_service is not None
            else self.base_dir / "generated_files"
        )
        service = GeneratedFileService(
            base_dir=base_dir,
            store=store,
            attachment_service=attachment_service,
            legacy_base_dirs=[self.base_dir / "generated_files"],
            ensure_storage_ready=workspace_service.ensure_layout if workspace_service is not None else None,
            work_dir=self.base_dir / "generated_work",
        )
        self.generated_file_service = service
        return service

    def _get_desktop_music_timeline_service(self) -> DesktopMusicTimelineService | None:
        service = getattr(self, "desktop_music_timeline_service", None)
        if service is not None:
            return service
        store = getattr(self, "store", None)
        generated_file_service = self._get_generated_file_service()
        if store is None or generated_file_service is None:
            return None
        service = DesktopMusicTimelineService(
            store=store,
            generated_file_service=generated_file_service,
            background_tasks=getattr(self, "background_tasks", None),
        )
        self.desktop_music_timeline_service = service
        return service

    def _get_music_context_assembler(self):
        assembler = getattr(self, "music_context_assembler", None)
        if assembler is not None:
            return assembler
        store = getattr(self, "store", None)
        if store is None:
            return None
        from .music_context import MusicContextAssembler, MusicControl
        from . import music_control_store

        def _controls_provider(profile_user_id: str) -> frozenset:
            _default = frozenset({
                MusicControl.PAUSE, MusicControl.NEXT,
                MusicControl.PREV, MusicControl.RECOMMEND,
            })
            if not profile_user_id:
                return _default
            try:
                with store._connect() as conn:
                    music_control_store.ensure_schema(conn)
                    names = music_control_store.get_enabled_controls(
                        conn, profile_user_id=profile_user_id
                    )
            except Exception:
                return _default
            valid = {c.value for c in MusicControl}
            return frozenset(MusicControl(n) for n in names if n in valid)

        assembler = MusicContextAssembler(store=store, controls_provider=_controls_provider)
        self.music_context_assembler = assembler
        return assembler

    def _get_retrieval_service(self) -> RetrievalService:
        retrieval_service = getattr(self, "retrieval_service", None)
        if retrieval_service is None:
            retrieval_service = RetrievalService(
                store=self.store,
                vector_store=self.vector_store,
                llm=self.llm,
                prompt_builder=self._get_prompt_builder(),
            )
            self.retrieval_service = retrieval_service
        return retrieval_service

    @staticmethod
    def _collect_visible_context_source_ids(
        *,
        recent_raw: list[dict[str, Any]],
        recent_episodic_summaries: list[dict[str, Any]],
        recent_semantic_summaries: list[dict[str, Any]],
        extra_source_ids: list[str] | None = None,
    ) -> list[str]:
        return retrieval_engine.collect_visible_context_source_ids(
            recent_raw=recent_raw,
            recent_episodic_summaries=recent_episodic_summaries,
            recent_semantic_summaries=recent_semantic_summaries,
            extra_source_ids=extra_source_ids,
        )

    def _get_compaction_service(self) -> MemoryCompactionService:
        compaction_service = getattr(self, "compaction_service", None)
        if compaction_service is None:
            compaction_service = MemoryCompactionService(
                store=self.store,
                vector_store=self.vector_store,
                llm=self.llm,
                prompt_builder=self._get_prompt_builder(),
                persona_context_provider=self._build_memory_compaction_persona_context,
            )
            self.compaction_service = compaction_service
        return compaction_service

    def _coerce_bool(self, value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
        return None

    def _prepare_care_context_for_turn(
        self,
        payload: dict[str, Any],
        client_context: ClientProtocolContext,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        now_ts: int,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return payload
        care_runtime = getattr(self, "care_runtime", None)
        if care_runtime is None:
            return payload
        client_mode = client_context.effective_mode.value if client_context is not None else ""
        relation_user_id = self._resolve_care_relation_user_id(
            payload,
            client_context,
            profile_user_id=profile_user_id,
        )
        now_ms = int(max(1, now_ts) * 1000)
        desktop_care = payload.get("desktop_care")
        try:
            if isinstance(desktop_care, dict):
                is_desktop = (
                    client_context is not None
                    and client_context.effective_mode == ClientMode.DESKTOP_PET
                )
                if is_desktop:
                    # Record turn before sync so the snapshot already includes this turn's count
                    try:
                        care_runtime.record_turn(
                            profile_user_id=profile_user_id,
                            character_pack_id=character_pack_id,
                            relation_user_id=profile_user_id,
                            now_ms=now_ms,
                        )
                    except Exception as exc:
                        logger.warning("desktop care record_turn failed: %s", exc)
                sync_result = care_runtime.sync_from_client(
                    profile_user_id=profile_user_id,
                    character_pack_id=character_pack_id,
                    client_mode=client_mode,
                    care_payload=desktop_care,
                    relation_user_id=relation_user_id,
                    now_ms=now_ms,
                )
                if is_desktop:
                    # Enrich desktop_care with tier event and anchors from sync
                    merged_care = dict(desktop_care)
                    if sync_result.get("pending_tier_event"):
                        merged_care["pending_tier_event"] = sync_result["pending_tier_event"]
                    if sync_result.get("anchors"):
                        merged_care["anchors"] = sync_result["anchors"]
                    enriched_payload = dict(payload)
                    enriched_payload["desktop_care"] = merged_care
                    return enriched_payload
            if client_context is not None and client_context.effective_mode == ClientMode.QQ_TEXT:
                enriched_payload = dict(payload)
                enriched_payload["desktop_care"] = care_runtime.snapshot_for_client(
                    profile_user_id=profile_user_id,
                    character_pack_id=character_pack_id,
                    client_mode=ClientMode.QQ_TEXT.value,
                    relation_user_id=relation_user_id,
                    now_ms=now_ms,
                )
                return enriched_payload
        except Exception as exc:
            logger.warning("care runtime context failed: %s", exc)
        return payload

    def _resolve_care_relation_user_id(
        self,
        payload: dict[str, Any],
        client_context: ClientProtocolContext | None,
        *,
        profile_user_id: str,
    ) -> str:
        if client_context is not None and client_context.effective_mode == ClientMode.QQ_TEXT:
            delivery = payload.get("qq_delivery_context") if isinstance(payload, dict) else {}
            if isinstance(delivery, dict):
                qq_user_id = str(delivery.get("user_id") or "").strip()
                if qq_user_id and qq_user_id != "0":
                    return f"qq:{qq_user_id}"
            return f"qq:{profile_user_id}"
        return str(profile_user_id or "master")

    def _apply_care_state_request(
        self,
        final_output: dict[str, Any],
        client_context: ClientProtocolContext,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        payload: dict[str, Any] | None = None,
        now_ts: int,
    ) -> None:
        if client_context is None or client_context.effective_mode != ClientMode.QQ_TEXT:
            return
        care_runtime = getattr(self, "care_runtime", None)
        if care_runtime is None or not isinstance(final_output, dict):
            return
        try:
            relation_user_id = self._resolve_care_relation_user_id(
                payload or {},
                client_context,
                profile_user_id=profile_user_id,
            )
        except Exception as exc:
            logger.warning("care runtime relation_user_id resolve failed: %s", exc)
            relation_user_id = ""
        # Per-reply effect: always fire for every QQ response
        try:
            care_runtime.apply_energy_cost(
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                relation_user_id=relation_user_id,
                energy_cost=1,
                coin_reward=1,
                now_ms=int(max(1, now_ts) * 1000),
            )
        except Exception as exc:
            logger.warning("care runtime energy cost failed: %s", exc)
        try:
            care_runtime.record_turn(
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                relation_user_id=relation_user_id,
                now_ms=int(max(1, now_ts) * 1000),
            )
        except Exception as exc:
            logger.warning("care runtime record_turn failed: %s", exc)
        # Affinity update: only when LLM signals a non-zero delta
        state_request = final_output.get("state_request")
        if not isinstance(state_request, dict):
            return
        affinity_delta = state_request.get("affinity")
        try:
            delta = max(-5, min(5, int(affinity_delta)))
        except (TypeError, ValueError):
            return
        if delta == 0:
            return
        try:
            snapshot = care_runtime.apply_affinity_delta(
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                client_mode=ClientMode.QQ_TEXT.value,
                relation_user_id=relation_user_id,
                delta=delta,
                now_ms=int(max(1, now_ts) * 1000),
            )
        except Exception as exc:
            logger.warning("care runtime affinity update failed: %s", exc)
            return
        final_output["care_state"] = snapshot

    def _resolve_pre_retrieval_enabled(self, *, payload: dict[str, Any]) -> bool:
        return retrieval_engine.resolve_pre_retrieval_enabled(self, payload=payload)

    def _build_skipped_pre_retrieval_pipeline(
        self,
        *,
        user_message: str,
        now_ts: int,
        reason: str,
    ) -> RetrievalPipelineResult:
        return retrieval_engine.build_skipped_pre_retrieval_pipeline(
            self,
            user_message=user_message,
            now_ts=now_ts,
            reason=reason,
        )

    def _run_pre_retrieval_pipeline(
        self,
        *,
        payload: dict[str, Any],
        profile_user_id: str,
        character_pack_id: str = "",
        user_message: str,
        now_ts: int,
        recent_raw: list[dict[str, Any]],
        recent_episodic_summaries: list[dict[str, Any]],
        recent_semantic_summaries: list[dict[str, Any]],
        current_user_source_id: str,
        verifier_debug_enabled: bool | None,
    ) -> RetrievalPipelineResult:
        return retrieval_engine.run_pre_retrieval_pipeline(
            self,
            payload=payload,
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            user_message=user_message,
            now_ts=now_ts,
            recent_raw=recent_raw,
            recent_episodic_summaries=recent_episodic_summaries,
            recent_semantic_summaries=recent_semantic_summaries,
            current_user_source_id=current_user_source_id,
            verifier_debug_enabled=verifier_debug_enabled,
        )

    def _should_index_user_record_in_vector(self, *, router_output: dict[str, Any]) -> bool:
        return retrieval_engine.should_index_user_record_in_vector(self, router_output=router_output)

    def _apply_user_vector_index_policy(
        self,
        *,
        user_record: dict[str, Any],
        router_output: dict[str, Any],
    ) -> dict[str, Any]:
        return retrieval_engine.apply_user_vector_index_policy(
            self,
            user_record=user_record,
            router_output=router_output,
        )

    def _schedule_summary_cycle(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> None:
        self._get_compaction_service().schedule_summary_cycle(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )

    def _run_summary_cycle(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> None:
        self._get_compaction_service().run_summary_cycle(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )

    def ingest_qq_attachments(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachments: list[dict[str, Any]],
        timestamp: int | None = None,
    ) -> list[dict[str, Any]]:
        service = self._get_attachment_ingest_service()
        if service is None:
            return []
        return service.ingest_qq_attachments(
            profile_user_id=profile_user_id,
            session_id=session_id,
            attachments=attachments,
            timestamp=timestamp,
        )

    def ingest_desktop_pet_audio_attachment(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source_path: Path | str,
        origin_name: str = "",
        mime_type: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        return desktop_pet_engine.ingest_desktop_pet_audio_attachment(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            source_path=source_path,
            origin_name=origin_name,
            mime_type=mime_type,
            timestamp=timestamp,
        )

    def import_desktop_pet_local_paths(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        paths: list[Any] | tuple[Any, ...] | set[Any] | str,
        recursive: bool = False,
        max_files: int = 40,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        return desktop_pet_engine.import_desktop_pet_local_paths(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            paths=paths,
            recursive=recursive,
            max_files=max_files,
            timestamp=timestamp,
        )

    def resolve_desktop_pet_audio_attachment(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
    ) -> tuple[dict[str, Any], Path] | None:
        return desktop_pet_engine.resolve_desktop_pet_audio_attachment(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
        )

    def resolve_desktop_pet_generated_audio(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
    ) -> tuple[dict[str, Any], Path] | None:
        return desktop_pet_engine.resolve_desktop_pet_generated_audio(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
        )

    def resolve_desktop_pet_attachment_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
    ) -> tuple[dict[str, Any], Path] | None:
        return desktop_pet_engine.resolve_desktop_pet_attachment_file(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
        )

    def resolve_desktop_pet_generated_file(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
    ) -> tuple[dict[str, Any], Path] | None:
        return desktop_pet_engine.resolve_desktop_pet_generated_file(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
        )

    def build_desktop_pet_workspace_panel(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        limit: int = 24,
    ) -> dict[str, Any]:
        return desktop_pet_engine.build_desktop_pet_workspace_panel(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            limit=limit,
        )

    def manage_desktop_pet_workspace_panel(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        action: str,
        item_type: str = "",
        target: str = "",
    ) -> dict[str, Any]:
        return desktop_pet_engine.manage_desktop_pet_workspace_panel(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            action=action,
            item_type=item_type,
            target=target,
        )

    def prepare_desktop_music_timeline(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        activity: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return desktop_pet_engine.prepare_desktop_music_timeline(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            activity=activity,
        )

    def submit_desktop_screen_vision_clip(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        frames: list[dict[str, Any]],
        foreground: dict[str, Any] | None = None,
        captured_start_ts: int | None = None,
        captured_end_ts: int | None = None,
        mode: str = "",
    ) -> dict[str, Any]:
        if self.desktop_screen_vision is None:
            return {"ok": False, "reason": "vision_disabled"}
        return self.desktop_screen_vision.submit_clip(
            profile_user_id=profile_user_id,
            session_id=session_id,
            frames=frames,
            foreground=foreground,
            captured_start_ts=captured_start_ts,
            captured_end_ts=captured_end_ts,
            mode=mode,
        )

    def list_desktop_screen_vision_observations(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        limit: int = 3,
        include_pending: bool = False,
    ) -> list[dict[str, Any]]:
        if self.desktop_screen_vision is None:
            return []
        return self.desktop_screen_vision.list_latest(
            profile_user_id=profile_user_id,
            session_id=session_id,
            limit=limit,
            include_pending=include_pending,
        )

    def get_desktop_screen_vision_clip(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        clip_id: str,
    ) -> dict[str, Any] | None:
        if self.desktop_screen_vision is None:
            return None
        return self.desktop_screen_vision.get_clip(
            profile_user_id=profile_user_id,
            session_id=session_id,
            clip_id=clip_id,
        )

    def clear_desktop_screen_vision_observations(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if self.desktop_screen_vision is None:
            return {"ok": False, "reason": "vision_disabled"}
        return self.desktop_screen_vision.clear(
            profile_user_id=profile_user_id,
            session_id=session_id,
        )

    def build_desktop_screen_vision_context(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        limit: int = 3,
    ) -> str:
        if self.desktop_screen_vision is None:
            return ""
        return self.desktop_screen_vision.build_prompt_context(
            profile_user_id=profile_user_id,
            session_id=session_id,
            limit=limit,
        )

    def build_desktop_screen_vision_reaction(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        clip_id: str,
    ) -> dict[str, Any]:
        if self.desktop_screen_vision is None:
            return {"ok": False, "reason": "vision_disabled"}
        observation = self.desktop_screen_vision.get_clip(
            profile_user_id=profile_user_id,
            session_id=session_id,
            clip_id=clip_id,
        )
        if not observation:
            return {"ok": False, "skip": True, "reason": "not_found"}
        if str(observation.get("status") or "") != "ready":
            return {"ok": True, "skip": True, "reason": "not_ready", "clip": observation}
        return self.desktop_screen_vision.build_reaction_with_llm(
            llm=self.llm,
            observation=observation,
        )

    def _is_transient_user_turn(self, payload: dict[str, Any]) -> bool:
        turn_kind = str(payload.get("turn_kind") or payload.get("client_turn_kind") or "").strip().lower()
        return bool(payload.get("transient_user_message")) or turn_kind in {
            "desktop_pet_proactive",
            "proactive",
        }

    def _build_transient_user_record(
        self,
        *,
        user_message: str,
        now_ts: int,
        date_label: str,
        time_of_day: str,
    ) -> dict[str, Any]:
        return {
            "source_id": "",
            "role": "user",
            "content": user_message,
            "timestamp": now_ts,
            "date_label": date_label,
            "time_of_day": time_of_day,
            "semantic_tags": [],
        }

    def _extract_desktop_screen_frame_images(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        frames = payload.get("desktop_screen_frames") if isinstance(payload, dict) else None
        if not isinstance(frames, list):
            return []
        images: list[dict[str, Any]] = []
        for item in frames[-5:]:
            if not isinstance(item, dict):
                continue
            data_url = str(item.get("data_url") or item.get("dataUrl") or "").strip()
            if not data_url.startswith("data:image/") or len(data_url) > 2_000_000:
                continue
            images.append(
                {
                    "data_url": data_url,
                    "captured_at": int(item.get("captured_at") or item.get("capturedAt") or 0),
                    "width": int(float(item.get("width") or 0)),
                    "height": int(float(item.get("height") or 0)),
                }
            )
        return images

    def _build_desktop_screen_frame_prompt_context(self, frames: list[dict[str, Any]]) -> str:
        usable = [frame for frame in frames if str(frame.get("data_url") or "").startswith("data:image/")]
        if not usable:
            return ""
        first_ts = int(usable[0].get("captured_at") or 0)
        last_ts = int(usable[-1].get("captured_at") or 0)
        duration = max(0, last_ts - first_ts)
        duration_text = f"，大约是最近 {duration} 秒里的变化" if duration > 0 else ""
        return "\n".join(
            [
                "【刚才一起看到的情况】",
                f"你刚才在主人旁边看了几眼{duration_text}。",
                "请优先贴着能看清的具体内容回应，像一起看视频、打游戏或做事时顺着眼前的小事接话。",
                "不要只泛泛地说主人看得认真或还在看同一个东西；看不清的地方就轻轻带过，别把拿不准的内容说死，也不要解释自己是怎么看到的。",
            ]
        )

    def prefetch_remote_media_links_for_message(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        message: str,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        return media_bridge_engine.prefetch_remote_media_links_for_message(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            message=message,
            timestamp=timestamp,
        )

    def _recent_prefetchable_remote_media_urls(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        limit: int = 24,
    ) -> list[str]:
        return media_bridge_engine.recent_prefetchable_remote_media_urls(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            limit=limit,
        )

    def _message_requests_remote_media_retry(self, message: str) -> bool:
        return media_bridge_engine.message_requests_remote_media_retry(message)

    def _extract_prefetchable_remote_media_urls(self, message: str) -> list[str]:
        return media_bridge_engine.extract_prefetchable_remote_media_urls(message)

    def _message_requests_remote_media_fetch(self, message: str, *, urls: list[str]) -> bool:
        return media_bridge_engine.message_requests_remote_media_fetch(message, urls=urls)

    def wait_for_qq_attachments_settled(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachment_ids: list[str],
        timeout_seconds: float = 8.0,
    ) -> dict[str, Any]:
        return media_bridge_engine.wait_for_qq_attachments_settled(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            attachment_ids=attachment_ids,
            timeout_seconds=timeout_seconds,
        )

    def mark_generated_file_delivery(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        generated_id: str,
        delivery_status: str,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        return media_bridge_engine.mark_generated_file_delivery(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            generated_id=generated_id,
            delivery_status=delivery_status,
            timestamp=timestamp,
        )

    def process_turn(self, payload: dict[str, Any]) -> dict[str, Any]:
        client_context = self._resolve_client_protocol_context(payload)
        turn_character_pack_id = self._resolve_payload_character_pack_id(payload)
        turn_resource_manifest = self._resolve_turn_resource_manifest(payload, client_context)
        trace_id = str(payload.get("trace_id") or f"{PERSONA.trace_prefix}_{uuid.uuid4().hex[:12]}")
        session_id = str(payload.get("user_id") or payload.get("session_id") or "default_session")
        profile_user_id = str(payload.get("real_user_id") or session_id)
        user_message = str(payload.get("message") or "").strip()
        now_ts = int(payload.get("timestamp") or time.time())
        payload = self._prepare_care_context_for_turn(
            payload,
            client_context,
            profile_user_id=profile_user_id,
            character_pack_id=turn_character_pack_id,
            now_ts=now_ts,
        )
        date_label = timestamp_to_date_label(now_ts)
        time_of_day = detect_time_of_day_from_text(user_message) or infer_time_of_day(now_ts)
        turn_extra_user_context = self._build_turn_extra_user_context(payload, client_context)
        desktop_screen_images = self._extract_desktop_screen_frame_images(payload)
        if desktop_screen_images:
            turn_extra_user_context = self._merge_extra_user_context(
                turn_extra_user_context,
                self._build_desktop_screen_frame_prompt_context(desktop_screen_images),
            )
        transient_user_turn = self._is_transient_user_turn(payload)

        self.consume_due_reminders(
            profile_user_id=profile_user_id,
            session_id=session_id,
            now_ts=now_ts,
            current_visual_payload=payload.get("current_visual"),
        )

        if transient_user_turn:
            user_record = self._build_transient_user_record(
                user_message=user_message,
                now_ts=now_ts,
                date_label=date_label,
                time_of_day=time_of_day,
            )
        else:
            user_record = self.store.add_message(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=turn_character_pack_id,
                role="user",
                content=user_message,
                timestamp=now_ts,
                date_label=date_label,
                time_of_day=time_of_day,
                semantic_tags=extract_semantic_tags(user_message),
            )
            self._schedule_summary_cycle(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=turn_character_pack_id,
            )

        recent_raw = self.store.get_unsummarized_messages(
            session_id,
            character_pack_id=turn_character_pack_id,
        )
        if transient_user_turn:
            recent_raw = [*recent_raw, user_record]
        episodic_limit = max(1, int(getattr(config, "EPISODIC_VISIBLE_MAX", getattr(config, "RECENT_SUMMARY_LIMIT", 5))))
        semantic_limit = max(1, int(getattr(config, "SEMANTIC_VISIBLE_LIMIT", 3)))
        recent_episodic_summaries = self.store.get_visible_episodic_summaries(
            profile_user_id,
            limit=episodic_limit,
            character_pack_id=turn_character_pack_id,
        )
        recent_semantic_summaries = (
            self.store.get_recent_semantic_summaries(
                profile_user_id,
                limit=semantic_limit,
                character_pack_id=turn_character_pack_id,
            )
            if bool(getattr(config, "ENABLE_SEMANTIC_MEMORY", True))
            else []
        )
        verifier_debug_enabled = self._coerce_bool(payload.get("verifier_debug"))
        final_debug_enabled = self._coerce_bool(payload.get("final_debug"))
        retrieval_pipeline = self._run_pre_retrieval_pipeline(
            payload=payload,
            profile_user_id=profile_user_id,
            character_pack_id=turn_character_pack_id,
            user_message=user_message,
            now_ts=now_ts,
            recent_raw=recent_raw,
            recent_episodic_summaries=recent_episodic_summaries,
            recent_semantic_summaries=recent_semantic_summaries,
            current_user_source_id=str(user_record.get("source_id") or ""),
            verifier_debug_enabled=verifier_debug_enabled,
        )
        router_output = retrieval_pipeline.router_output
        router_timing = retrieval_pipeline.router_timing
        retrieval_result = retrieval_pipeline.retrieval_result
        verifier_output = retrieval_pipeline.verifier_output
        confirmed_snippets = retrieval_pipeline.confirmed_snippets
        verifier_timing = retrieval_pipeline.verifier_timing
        if not transient_user_turn:
            user_record = self._apply_user_vector_index_policy(
                user_record=user_record,
                router_output=router_output,
            )
            self._upsert_raw_record(user_record)

        final_output = self._build_final_response(
            session_id=session_id,
            profile_user_id=profile_user_id,
            user_message=user_message,
            recent_raw=recent_raw,
            recent_episodic_summaries=recent_episodic_summaries,
            recent_semantic_summaries=recent_semantic_summaries,
            confirmed_snippets=confirmed_snippets,
            now_ts=now_ts,
            current_visual_payload=payload.get("current_visual"),
            extra_user_context=turn_extra_user_context,
            client_context=client_context,
            resource_manifest=turn_resource_manifest,
            character_pack_id=turn_character_pack_id,
            user_images=desktop_screen_images,
            final_debug_enabled=final_debug_enabled,
        )
        recent_raw_for_turn = list(recent_raw)
        tool_turns: list[dict[str, Any]] = []
        preface_turns: list[dict[str, str]] = []
        tool_result: ToolExecutionResult | None = None
        tool_results: list[ToolExecutionResult] = []
        tool_events: list[dict[str, Any]] = []
        tool_followups: list[str] = []
        seen_tool_calls: set[str] = set()
        max_tool_rounds = self._max_tool_rounds()
        tool_round_index = 0
        memory_exclude_source_ids = [
            str(hit.get("source_id") or "").strip()
            for hit in retrieval_result.get("fused_hits", [])
            if str(hit.get("source_id") or "").strip()
        ]
        while tool_round_index < max_tool_rounds:
            final_output, tool_call, rejection = self._prepare_tool_round_decision(
                final_output=final_output,
                user_message=user_message,
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
            )
            if not tool_call:
                if not rejection:
                    break
                allow_retry = self._record_tool_call_rejection(
                    final_output=final_output,
                    rejection=rejection,
                    tool_followups=tool_followups,
                    session_id=session_id,
                    tool_round_index=tool_round_index,
                    max_tool_rounds=max_tool_rounds,
                )
                final_output = self._build_final_response(
                    session_id=session_id,
                    profile_user_id=profile_user_id,
                    user_message=user_message,
                    recent_raw=recent_raw_for_turn,
                    recent_episodic_summaries=recent_episodic_summaries,
                    recent_semantic_summaries=recent_semantic_summaries,
                    confirmed_snippets=confirmed_snippets,
                    now_ts=now_ts,
                    current_visual_payload=payload.get("current_visual"),
                    extra_user_context=self._build_tool_round_extra_context(
                        turn_extra_user_context=turn_extra_user_context,
                        tool_followups=tool_followups,
                        allow_more=allow_retry,
                    ),
                    client_context=client_context,
                    resource_manifest=turn_resource_manifest,
                    character_pack_id=turn_character_pack_id,
                    user_images=desktop_screen_images,
                    allow_tool_call=allow_retry,
                    final_debug_enabled=final_debug_enabled,
                )
                tool_round_index += 1
                if allow_retry:
                    continue
                break
            max_tool_rounds = self._resolve_tool_round_budget(
                current_budget=max_tool_rounds,
                tool_call=tool_call,
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
            )

            tool_signature = self._tool_call_signature(tool_call)
            if tool_signature in seen_tool_calls:
                tool_followups.append(
                    f"系统刚刚拦截了一次重复工具调用：{self._describe_tool_call_for_prompt(tool_call)}。"
                    "请基于已经拿到的工具结果自然回应，不要继续重复调用同一个工具。"
                )
                final_output = self._build_final_response(
                    session_id=session_id,
                    profile_user_id=profile_user_id,
                    user_message=user_message,
                    recent_raw=recent_raw_for_turn,
                    recent_episodic_summaries=recent_episodic_summaries,
                    recent_semantic_summaries=recent_semantic_summaries,
                    confirmed_snippets=confirmed_snippets,
                    now_ts=now_ts,
                    current_visual_payload=payload.get("current_visual"),
                    extra_user_context=self._build_tool_round_extra_context(
                        turn_extra_user_context=turn_extra_user_context,
                        tool_followups=tool_followups,
                        allow_more=False,
                    ),
                    client_context=client_context,
                    resource_manifest=turn_resource_manifest,
                    character_pack_id=turn_character_pack_id,
                    user_images=desktop_screen_images,
                    allow_tool_call=False,
                    final_debug_enabled=final_debug_enabled,
                )
                break
            seen_tool_calls.add(tool_signature)

            self._record_assistant_preface_for_tool_call(
                tool_call=tool_call,
                final_output=final_output,
                preface_turns=preface_turns,
                recent_raw_for_turn=recent_raw_for_turn,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=turn_character_pack_id,
                now_ts=now_ts,
                date_label=date_label,
                time_of_day=time_of_day,
            )
            tool_result, _current_events = self._execute_and_record_tool_round(
                tool_call=tool_call,
                final_output=final_output,
                tool_results=tool_results,
                tool_events=tool_events,
                tool_followups=tool_followups,
                tool_turns=tool_turns,
                recent_raw_for_turn=recent_raw_for_turn,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=turn_character_pack_id,
                now_ts=now_ts,
                current_user_source_id=str(user_record.get("source_id") or ""),
                client_context=client_context,
                memory_exclude_source_ids=memory_exclude_source_ids,
                request_context=payload,
            )

            stop_after_tool = self._should_stop_after_tool_events(_current_events)
            allow_more_tools = (tool_round_index < max_tool_rounds - 1) and not stop_after_tool
            final_output = self._build_final_response(
                session_id=session_id,
                profile_user_id=profile_user_id,
                user_message=user_message,
                recent_raw=recent_raw_for_turn,
                recent_episodic_summaries=recent_episodic_summaries,
                recent_semantic_summaries=recent_semantic_summaries,
                confirmed_snippets=confirmed_snippets,
                now_ts=now_ts,
                current_visual_payload=payload.get("current_visual"),
                extra_user_context=self._build_tool_round_extra_context(
                    turn_extra_user_context=turn_extra_user_context,
                    tool_followups=tool_followups,
                    allow_more=allow_more_tools,
                    stop_reason=(
                        "tool_unavailable"
                        if stop_after_tool
                        else "tool_budget_exhausted" if not allow_more_tools else ""
                    ),
                ),
                client_context=client_context,
                resource_manifest=turn_resource_manifest,
                character_pack_id=turn_character_pack_id,
                user_images=desktop_screen_images,
                allow_tool_call=allow_more_tools,
                final_debug_enabled=final_debug_enabled,
            )
            tool_round_index += 1

        final_output = self._apply_persona_state_to_final_output(
            profile_user_id=profile_user_id,
            session_id=session_id,
            final_output=final_output,
            now_ts=now_ts,
            source_id=str(user_record.get("source_id") or ""),
            tool_result=tool_result,
        )
        final_output["tool_events"] = tool_events
        final_output["npc_turns"] = tool_turns
        final_output["dialogue_turns"] = self._build_dialogue_turns(
            preface_turn=preface_turns,
            npc_turns=tool_turns,
            final_speech=final_output.get("speech"),
            final_speech_segments=final_output.get("speech_segments"),
            speaker_name=self._resolve_turn_speaker_identity(
                client_context, turn_character_pack_id,
            )["assistant_name"],
        )
        self._apply_care_state_request(
            final_output,
            client_context,
            profile_user_id=profile_user_id,
            character_pack_id=turn_character_pack_id,
            payload=payload,
            now_ts=now_ts,
        )
        memory_tags = final_output_engine.extract_memory_keywords(self, final_output)
        memory_metadata = final_output.get("memory_metadata")
        if not isinstance(memory_metadata, dict):
            memory_metadata = final_output_engine.normalize_memory_metadata(self, None)
        else:
            memory_metadata = dict(memory_metadata)
        memory_metadata["keywords"] = memory_tags
        final_output["memory_metadata"] = memory_metadata
        final_output.pop("memory_tags", None)
        if not transient_user_turn:
            user_record = self._apply_memory_metadata_to_user_record(
                user_record=user_record,
                memory_metadata=memory_metadata,
            )
        if memory_tags and not transient_user_turn:
            user_record = self._apply_memory_tags_to_user_record(
                user_record=user_record,
                memory_tags=memory_tags,
            )
        if client_context.effective_mode != ClientMode.DESKTOP_PET:
            self._schedule_visual_observations_for_payload(
                payload=final_output,
                profile_user_id=profile_user_id,
                session_id=session_id,
            )

        assistant_record = self.store.add_message(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=turn_character_pack_id,
            role="assistant",
            content=final_output.get("speech", ""),
            timestamp=int(time.time()),
            semantic_tags=extract_semantic_tags(final_output.get("speech", "")),
            memory_metadata=self._build_assistant_timeline_metadata(final_output),
        )
        self._upsert_raw_record(assistant_record)
        self._schedule_summary_cycle(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=turn_character_pack_id,
        )

        self.store.append_eval_turn(
            trace_id=trace_id,
            session_id=session_id,
            profile_user_id=profile_user_id,
            character_pack_id=turn_character_pack_id,
            user_message=user_message,
            router_json=router_output,
            verifier_json=verifier_output,
            final_json=final_output,
        )

        final_output["trace_id"] = trace_id
        debug_payload = self._build_retrieval_debug_payload(
            router_output=router_output,
            router_timing=router_timing,
            retrieval_result=retrieval_result,
            verifier_output=verifier_output,
            verifier_timing=verifier_timing,
            confirmed_snippets=confirmed_snippets,
        )
        memory_tool_updates = [
            result.state_updates.get("memory_retrieval")
            for result in tool_results
            if isinstance(result.state_updates, dict) and result.state_updates.get("memory_retrieval")
        ]
        if memory_tool_updates:
            debug_payload["memory_tool"] = memory_tool_updates[-1]
            debug_payload["memory_tool_rounds"] = memory_tool_updates
        character_context_debug = self._build_character_context_debug_payload(
            character_pack_id=turn_character_pack_id,
            user_message=user_message,
            tool_results=tool_results,
        )
        if character_context_debug:
            debug_payload["character_context"] = character_context_debug
        final_output["_debug"] = debug_payload
        return final_output

    def process_turn_stream(self, payload: dict[str, Any]) -> Generator[dict[str, Any], None, None]:
        client_context = self._resolve_client_protocol_context(payload)
        turn_character_pack_id = self._resolve_payload_character_pack_id(payload)
        turn_resource_manifest = self._resolve_turn_resource_manifest(payload, client_context)
        trace_id = str(payload.get("trace_id") or f"{PERSONA.trace_prefix}_{uuid.uuid4().hex[:12]}")
        session_id = str(payload.get("user_id") or payload.get("session_id") or "default_session")
        profile_user_id = str(payload.get("real_user_id") or session_id)
        user_message = str(payload.get("message") or "").strip()
        now_ts = int(payload.get("timestamp") or time.time())
        payload = self._prepare_care_context_for_turn(
            payload,
            client_context,
            profile_user_id=profile_user_id,
            character_pack_id=turn_character_pack_id,
            now_ts=now_ts,
        )
        date_label = timestamp_to_date_label(now_ts)
        time_of_day = detect_time_of_day_from_text(user_message) or infer_time_of_day(now_ts)
        turn_extra_user_context = self._build_turn_extra_user_context(payload, client_context)
        desktop_screen_images = self._extract_desktop_screen_frame_images(payload)
        if desktop_screen_images:
            turn_extra_user_context = self._merge_extra_user_context(
                turn_extra_user_context,
                self._build_desktop_screen_frame_prompt_context(desktop_screen_images),
            )
        transient_user_turn = self._is_transient_user_turn(payload)

        self.consume_due_reminders(
            profile_user_id=profile_user_id,
            session_id=session_id,
            now_ts=now_ts,
            current_visual_payload=payload.get("current_visual"),
        )

        if transient_user_turn:
            user_record = self._build_transient_user_record(
                user_message=user_message,
                now_ts=now_ts,
                date_label=date_label,
                time_of_day=time_of_day,
            )
        else:
            user_record = self.store.add_message(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=turn_character_pack_id,
                role="user",
                content=user_message,
                timestamp=now_ts,
                date_label=date_label,
                time_of_day=time_of_day,
                semantic_tags=extract_semantic_tags(user_message),
            )
            self._schedule_summary_cycle(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=turn_character_pack_id,
            )

        recent_raw = self.store.get_unsummarized_messages(
            session_id,
            character_pack_id=turn_character_pack_id,
        )
        if transient_user_turn:
            recent_raw = [*recent_raw, user_record]
        episodic_limit = max(1, int(getattr(config, "EPISODIC_VISIBLE_MAX", getattr(config, "RECENT_SUMMARY_LIMIT", 5))))
        semantic_limit = max(1, int(getattr(config, "SEMANTIC_VISIBLE_LIMIT", 3)))
        recent_episodic_summaries = self.store.get_visible_episodic_summaries(
            profile_user_id,
            limit=episodic_limit,
            character_pack_id=turn_character_pack_id,
        )
        recent_semantic_summaries = (
            self.store.get_recent_semantic_summaries(
                profile_user_id,
                limit=semantic_limit,
                character_pack_id=turn_character_pack_id,
            )
            if bool(getattr(config, "ENABLE_SEMANTIC_MEMORY", True))
            else []
        )
        verifier_debug_enabled = self._coerce_bool(payload.get("verifier_debug"))
        final_debug_enabled = self._coerce_bool(payload.get("final_debug"))
        retrieval_pipeline = self._run_pre_retrieval_pipeline(
            payload=payload,
            profile_user_id=profile_user_id,
            character_pack_id=turn_character_pack_id,
            user_message=user_message,
            now_ts=now_ts,
            recent_raw=recent_raw,
            recent_episodic_summaries=recent_episodic_summaries,
            recent_semantic_summaries=recent_semantic_summaries,
            current_user_source_id=str(user_record.get("source_id") or ""),
            verifier_debug_enabled=verifier_debug_enabled,
        )
        router_output = retrieval_pipeline.router_output
        router_timing = retrieval_pipeline.router_timing
        retrieval_result = retrieval_pipeline.retrieval_result
        verifier_output = retrieval_pipeline.verifier_output
        confirmed_snippets = retrieval_pipeline.confirmed_snippets
        verifier_timing = retrieval_pipeline.verifier_timing
        if not transient_user_turn:
            user_record = self._apply_user_vector_index_policy(
                user_record=user_record,
                router_output=router_output,
            )
            self._upsert_raw_record(user_record)

        final_output = yield from self._stream_final_response(
            session_id=session_id,
            profile_user_id=profile_user_id,
            user_message=user_message,
            recent_raw=recent_raw,
            recent_episodic_summaries=recent_episodic_summaries,
            recent_semantic_summaries=recent_semantic_summaries,
            confirmed_snippets=confirmed_snippets,
            now_ts=now_ts,
            current_visual_payload=payload.get("current_visual"),
            extra_user_context=turn_extra_user_context,
            client_context=client_context,
            resource_manifest=turn_resource_manifest,
            character_pack_id=turn_character_pack_id,
            user_images=desktop_screen_images,
            final_debug_enabled=final_debug_enabled,
        )
        recent_raw_for_turn = list(recent_raw)
        tool_turns: list[dict[str, Any]] = []
        preface_turns: list[dict[str, str]] = []
        tool_result: ToolExecutionResult | None = None
        tool_results: list[ToolExecutionResult] = []
        tool_events: list[dict[str, Any]] = []
        tool_followups: list[str] = []
        seen_tool_calls: set[str] = set()
        max_tool_rounds = self._max_tool_rounds()
        tool_round_index = 0
        memory_exclude_source_ids = [
            str(hit.get("source_id") or "").strip()
            for hit in retrieval_result.get("fused_hits", [])
            if str(hit.get("source_id") or "").strip()
        ]
        while tool_round_index < max_tool_rounds:
            final_output, tool_call, rejection = self._prepare_tool_round_decision(
                final_output=final_output,
                user_message=user_message,
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
            )
            yield {
                "type": "assistant_stage_decision",
                "has_tool_call": bool(tool_call),
                "tool_type": str((tool_call or {}).get("type") or ""),
                "rejected_tool_call": bool(rejection),
            }
            if not tool_call:
                if not rejection:
                    break
                allow_retry = self._record_tool_call_rejection(
                    final_output=final_output,
                    rejection=rejection,
                    tool_followups=tool_followups,
                    session_id=session_id,
                    tool_round_index=tool_round_index,
                    max_tool_rounds=max_tool_rounds,
                )
                final_output = yield from self._stream_final_response(
                    session_id=session_id,
                    profile_user_id=profile_user_id,
                    user_message=user_message,
                    recent_raw=recent_raw_for_turn,
                    recent_episodic_summaries=recent_episodic_summaries,
                    recent_semantic_summaries=recent_semantic_summaries,
                    confirmed_snippets=confirmed_snippets,
                    now_ts=now_ts,
                    current_visual_payload=payload.get("current_visual"),
                    extra_user_context=self._build_tool_round_extra_context(
                        turn_extra_user_context=turn_extra_user_context,
                        tool_followups=tool_followups,
                        allow_more=allow_retry,
                    ),
                    client_context=client_context,
                    resource_manifest=turn_resource_manifest,
                    character_pack_id=turn_character_pack_id,
                    user_images=desktop_screen_images,
                    allow_tool_call=allow_retry,
                    final_debug_enabled=final_debug_enabled,
                )
                tool_round_index += 1
                if allow_retry:
                    continue
                break
            max_tool_rounds = self._resolve_tool_round_budget(
                current_budget=max_tool_rounds,
                tool_call=tool_call,
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
            )

            tool_signature = self._tool_call_signature(tool_call)
            if tool_signature in seen_tool_calls:
                tool_followups.append(
                    f"系统刚刚拦截了一次重复工具调用：{self._describe_tool_call_for_prompt(tool_call)}。"
                    "请基于已经拿到的工具结果自然回应，不要继续重复调用同一个工具。"
                )
                final_output = yield from self._stream_final_response(
                    session_id=session_id,
                    profile_user_id=profile_user_id,
                    user_message=user_message,
                    recent_raw=recent_raw_for_turn,
                    recent_episodic_summaries=recent_episodic_summaries,
                    recent_semantic_summaries=recent_semantic_summaries,
                    confirmed_snippets=confirmed_snippets,
                    now_ts=now_ts,
                    current_visual_payload=payload.get("current_visual"),
                    extra_user_context=self._build_tool_round_extra_context(
                        turn_extra_user_context=turn_extra_user_context,
                        tool_followups=tool_followups,
                        allow_more=False,
                    ),
                    client_context=client_context,
                    resource_manifest=turn_resource_manifest,
                    character_pack_id=turn_character_pack_id,
                    user_images=desktop_screen_images,
                    allow_tool_call=False,
                    final_debug_enabled=final_debug_enabled,
                )
                break
            seen_tool_calls.add(tool_signature)

            self._record_assistant_preface_for_tool_call(
                tool_call=tool_call,
                final_output=final_output,
                preface_turns=preface_turns,
                recent_raw_for_turn=recent_raw_for_turn,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=turn_character_pack_id,
                now_ts=now_ts,
                date_label=date_label,
                time_of_day=time_of_day,
            )
            yield self._build_tool_working_stream_event(tool_call)
            tool_result, current_events = self._execute_and_record_tool_round(
                tool_call=tool_call,
                final_output=final_output,
                tool_results=tool_results,
                tool_events=tool_events,
                tool_followups=tool_followups,
                tool_turns=tool_turns,
                recent_raw_for_turn=recent_raw_for_turn,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=turn_character_pack_id,
                now_ts=now_ts,
                current_user_source_id=str(user_record.get("source_id") or ""),
                client_context=client_context,
                memory_exclude_source_ids=memory_exclude_source_ids,
                request_context=payload,
            )
            for stream_event in current_events:
                yield stream_event

            stop_after_tool = self._should_stop_after_tool_events(current_events)
            allow_more_tools = (tool_round_index < max_tool_rounds - 1) and not stop_after_tool
            final_output = yield from self._stream_final_response(
                session_id=session_id,
                profile_user_id=profile_user_id,
                user_message=user_message,
                recent_raw=recent_raw_for_turn,
                recent_episodic_summaries=recent_episodic_summaries,
                recent_semantic_summaries=recent_semantic_summaries,
                confirmed_snippets=confirmed_snippets,
                now_ts=now_ts,
                current_visual_payload=payload.get("current_visual"),
                extra_user_context=self._build_tool_round_extra_context(
                    turn_extra_user_context=turn_extra_user_context,
                    tool_followups=tool_followups,
                    allow_more=allow_more_tools,
                    stop_reason=(
                        "tool_unavailable"
                        if stop_after_tool
                        else "tool_budget_exhausted" if not allow_more_tools else ""
                    ),
                ),
                client_context=client_context,
                resource_manifest=turn_resource_manifest,
                character_pack_id=turn_character_pack_id,
                user_images=desktop_screen_images,
                allow_tool_call=allow_more_tools,
                final_debug_enabled=final_debug_enabled,
            )
            tool_round_index += 1

        final_output = self._apply_persona_state_to_final_output(
            profile_user_id=profile_user_id,
            session_id=session_id,
            final_output=final_output,
            now_ts=now_ts,
            source_id=str(user_record.get("source_id") or ""),
            tool_result=tool_result,
        )
        final_output["tool_events"] = tool_events
        final_output["npc_turns"] = tool_turns
        final_output["dialogue_turns"] = self._build_dialogue_turns(
            preface_turn=preface_turns,
            npc_turns=tool_turns,
            final_speech=final_output.get("speech"),
            final_speech_segments=final_output.get("speech_segments"),
            speaker_name=self._resolve_turn_speaker_identity(
                client_context, turn_character_pack_id,
            )["assistant_name"],
        )
        self._apply_care_state_request(
            final_output,
            client_context,
            profile_user_id=profile_user_id,
            character_pack_id=turn_character_pack_id,
            payload=payload,
            now_ts=now_ts,
        )
        memory_tags = final_output_engine.extract_memory_keywords(self, final_output)
        memory_metadata = final_output.get("memory_metadata")
        if not isinstance(memory_metadata, dict):
            memory_metadata = final_output_engine.normalize_memory_metadata(self, None)
        else:
            memory_metadata = dict(memory_metadata)
        memory_metadata["keywords"] = memory_tags
        final_output["memory_metadata"] = memory_metadata
        final_output.pop("memory_tags", None)
        if not transient_user_turn:
            user_record = self._apply_memory_metadata_to_user_record(
                user_record=user_record,
                memory_metadata=memory_metadata,
            )
        if memory_tags and not transient_user_turn:
            user_record = self._apply_memory_tags_to_user_record(
                user_record=user_record,
                memory_tags=memory_tags,
            )
        if client_context.effective_mode != ClientMode.DESKTOP_PET:
            self._schedule_visual_observations_for_payload(
                payload=final_output,
                profile_user_id=profile_user_id,
                session_id=session_id,
            )

        ui_final_payload = dict(final_output)

        assistant_record = self.store.add_message(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=turn_character_pack_id,
            role="assistant",
            content=final_output.get("speech", ""),
            timestamp=int(time.time()),
            semantic_tags=extract_semantic_tags(final_output.get("speech", "")),
            memory_metadata=self._build_assistant_timeline_metadata(final_output),
        )
        self._upsert_raw_record(assistant_record)
        self._schedule_summary_cycle(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=turn_character_pack_id,
        )

        self.store.append_eval_turn(
            trace_id=trace_id,
            session_id=session_id,
            profile_user_id=profile_user_id,
            character_pack_id=turn_character_pack_id,
            user_message=user_message,
            router_json=router_output,
            verifier_json=verifier_output,
            final_json=final_output,
        )

        yield {"type": "final_ui", "payload": ui_final_payload}

        final_output["trace_id"] = trace_id
        debug_payload = self._build_retrieval_debug_payload(
            router_output=router_output,
            router_timing=router_timing,
            retrieval_result=retrieval_result,
            verifier_output=verifier_output,
            verifier_timing=verifier_timing,
            confirmed_snippets=confirmed_snippets,
        )
        memory_tool_updates = [
            result.state_updates.get("memory_retrieval")
            for result in tool_results
            if isinstance(result.state_updates, dict) and result.state_updates.get("memory_retrieval")
        ]
        if memory_tool_updates:
            debug_payload["memory_tool"] = memory_tool_updates[-1]
            debug_payload["memory_tool_rounds"] = memory_tool_updates
        character_context_debug = self._build_character_context_debug_payload(
            character_pack_id=turn_character_pack_id,
            user_message=user_message,
            tool_results=tool_results,
        )
        if character_context_debug:
            debug_payload["character_context"] = character_context_debug
        final_output["_debug"] = debug_payload
        yield {"type": "final", "payload": final_output}

    def _build_character_context_debug_payload(
        self,
        *,
        character_pack_id: str,
        user_message: str,
        tool_results: list[ToolExecutionResult],
    ) -> dict[str, Any]:
        automatic: dict[str, Any] = {}
        context_library_service = getattr(
            getattr(self, "desktop_pet_character_resources", None),
            "context_libraries",
            None,
        )
        automatic_loader = getattr(
            context_library_service,
            "load_automatic_context",
            None,
        )
        if automatic_loader is not None and character_pack_id:
            try:
                result = automatic_loader(character_pack_id, user_message)
            except Exception as exc:
                logger.warning("automatic character context diagnostics failed: %s", exc)
                result = {}
            matches = [
                {
                    "target": str(item.get("target") or ""),
                    "matched_terms": [
                        str(term)
                        for term in item.get("matched_terms") or []
                        if str(term).strip()
                    ],
                }
                for item in result.get("matches") or []
                if isinstance(item, dict) and str(item.get("target") or "").strip()
            ]
            loaded = [
                str(item.get("target") or "")
                for item in result.get("loaded") or []
                if isinstance(item, dict) and str(item.get("target") or "").strip()
            ]
            failed = [
                {
                    "target": str(item.get("target") or ""),
                    "status": str(item.get("status") or "unavailable"),
                    "reason": str(item.get("reason") or ""),
                }
                for item in result.get("failed") or []
                if isinstance(item, dict)
            ]
            if matches or loaded or failed:
                automatic = {
                    "status": str(result.get("status") or "unavailable"),
                    "matches": matches,
                    "loaded": loaded,
                    "failed": failed,
                }

        tool_rounds = [
            dict(result.state_updates.get("character_context") or {})
            for result in tool_results
            if (
                isinstance(result.state_updates, dict)
                and isinstance(result.state_updates.get("character_context"), dict)
            )
        ]
        if not automatic and not tool_rounds:
            return {}
        return {
            "automatic": automatic,
            "tool_rounds": tool_rounds,
        }

    def _build_retrieval_debug_payload(
        self,
        *,
        router_output: dict[str, Any],
        router_timing: dict[str, Any],
        retrieval_result: dict[str, Any],
        verifier_output: dict[str, Any],
        verifier_timing: dict[str, Any],
        confirmed_snippets: list[str],
    ) -> dict[str, Any]:
        memory_snippets = list(retrieval_result.get("memory_snippets") or [])
        selected_memory_snippets: list[dict[str, Any]] = []
        seen_indexes: set[int] = set()
        for raw_index in verifier_output.get("selected_indexes") or []:
            try:
                index = int(raw_index)
            except Exception:
                continue
            if index < 1 or index > len(memory_snippets) or index in seen_indexes:
                continue
            seen_indexes.add(index)
            selected_memory_snippets.append(
                {
                    "index": index,
                    "snippet": memory_snippets[index - 1],
                }
            )
        if not selected_memory_snippets and confirmed_snippets:
            selected_memory_snippets = [
                {
                    "index": None,
                    "snippet": str(snippet),
                }
                for snippet in confirmed_snippets
                if str(snippet).strip()
            ]
        return {
            "router_output": router_output,
            "router_timing": router_timing,
            "retrieval_result": {
                "filtered_candidate_count": retrieval_result["filtered_candidate_count"],
                "time_filter": retrieval_result["time_filter"],
                "fused_hits": retrieval_result["fused_hits"],
                "memory_snippets": memory_snippets,
                "selected_memory_snippets": selected_memory_snippets,
            },
            "verifier_output": verifier_output,
            "verifier_timing": verifier_timing,
        }

    def _build_final_response(
        self,
        *,
        session_id: str,
        profile_user_id: str,
        user_message: str,
        recent_raw: list[dict[str, Any]],
        recent_episodic_summaries: list[dict[str, Any]],
        recent_semantic_summaries: list[dict[str, Any]],
        confirmed_snippets: list[str],
        now_ts: int,
        current_visual_payload: Any = None,
        extra_user_context: str = "",
        client_context: ClientProtocolContext | None = None,
        resource_manifest: ResourceManifest | None = None,
        character_pack_id: str = "",
        user_images: list[dict[str, Any]] | None = None,
        allow_tool_call: bool = True,
        final_debug_enabled: bool | None = None,
    ) -> dict[str, Any]:
        generation_context = self._prepare_final_response_context(
            session_id=session_id,
            user_message=user_message,
            recent_raw=recent_raw,
            recent_episodic_summaries=recent_episodic_summaries,
            recent_semantic_summaries=recent_semantic_summaries,
            confirmed_snippets=confirmed_snippets,
            now_ts=now_ts,
            current_visual_payload=current_visual_payload,
            profile_user_id=profile_user_id,
            extra_user_context=extra_user_context,
            client_context=client_context,
            resource_manifest=resource_manifest,
            character_pack_id=character_pack_id,
            allow_tool_call=allow_tool_call,
            final_debug_enabled=final_debug_enabled,
            enable_native_tools=True,
        )
        result = self.llm.call_chat_json(
            system_prompt=str(generation_context["system_prompt"]),
            user_prompt=str(generation_context["user_prompt"]),
            fallback=dict(generation_context["fallback"]),
            temperature=0.7,
            prompt_cache_key="chat:final",
            user_images=user_images,
            system_extra_blocks=generation_context.get("system_extra_blocks"),
            history_turns=generation_context.get("history_turns"),
            prompt_audit_sections=generation_context.get("prompt_audit_sections"),
            native_tools=generation_context.get("native_tools"),
            native_tool_choice=generation_context.get("native_tool_choice", ""),
        )
        return self._normalize_final_output(
            result=result,
            visual_defaults=dict(generation_context["visual_defaults"]),
            profile_user_id=profile_user_id,
            session_id=session_id,
            client_context=client_context,
            resource_manifest=resource_manifest,
            allow_tool_call=bool(generation_context.get("allow_tool_call", allow_tool_call)),
            debug_enabled=bool(generation_context["debug_enabled"]),
            user_message=user_message,
        )

    def _stream_final_response(
        self,
        *,
        session_id: str,
        profile_user_id: str,
        user_message: str,
        recent_raw: list[dict[str, Any]],
        recent_episodic_summaries: list[dict[str, Any]],
        recent_semantic_summaries: list[dict[str, Any]],
        confirmed_snippets: list[str],
        now_ts: int,
        current_visual_payload: Any = None,
        extra_user_context: str = "",
        client_context: ClientProtocolContext | None = None,
        resource_manifest: ResourceManifest | None = None,
        character_pack_id: str = "",
        user_images: list[dict[str, Any]] | None = None,
        allow_tool_call: bool = True,
        final_debug_enabled: bool | None = None,
    ) -> Generator[dict[str, Any], None, dict[str, Any]]:
        generation_context = self._prepare_final_response_context(
            session_id=session_id,
            user_message=user_message,
            recent_raw=recent_raw,
            recent_episodic_summaries=recent_episodic_summaries,
            recent_semantic_summaries=recent_semantic_summaries,
            confirmed_snippets=confirmed_snippets,
            now_ts=now_ts,
            current_visual_payload=current_visual_payload,
            profile_user_id=profile_user_id,
            extra_user_context=extra_user_context,
            client_context=client_context,
            resource_manifest=resource_manifest,
            character_pack_id=character_pack_id,
            allow_tool_call=allow_tool_call,
            final_debug_enabled=final_debug_enabled,
            enable_native_tools=True,
        )
        speaker_identity = self._resolve_turn_speaker_identity(
            client_context, character_pack_id,
        )
        yield {
            "type": "turn_start",
            "speaker": speaker_identity["assistant_name"],
        }
        stream_result = yield from self.llm.stream_chat_json(
            system_prompt=str(generation_context["system_prompt"]),
            user_prompt=str(generation_context["user_prompt"]),
            fallback=dict(generation_context["fallback"]),
            temperature=0.7,
            prompt_cache_key="chat:final",
            user_images=user_images,
            native_tools=generation_context.get("native_tools"),
            native_tool_choice=generation_context.get("native_tool_choice", ""),
            system_extra_blocks=generation_context.get("system_extra_blocks"),
            history_turns=generation_context.get("history_turns"),
            prompt_audit_sections=generation_context.get("prompt_audit_sections"),
            early_tool_call_validator=(
                lambda call: self._normalize_tool_call(
                    call,
                    client_context=client_context,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                )
                is not None
            )
            if bool(generation_context.get("allow_tool_call", allow_tool_call))
            else None,
        )
        if str(stream_result.error or "").strip():
            yield {
                "type": "stream_error",
                "message": str(stream_result.error),
                "partial": {
                    "emotion": str(stream_result.latest_emotion or ""),
                    "speech": str(stream_result.latest_speech or ""),
                },
            }
        return self._normalize_final_output(
            result=stream_result.parsed,
            visual_defaults=dict(generation_context["visual_defaults"]),
            profile_user_id=profile_user_id,
            session_id=session_id,
            client_context=client_context,
            resource_manifest=resource_manifest,
            user_message=user_message,
            allow_tool_call=bool(generation_context.get("allow_tool_call", allow_tool_call)),
            debug_enabled=bool(generation_context["debug_enabled"]),
        )

    def _prepare_final_response_context(
        self,
        *,
        session_id: str,
        user_message: str,
        recent_raw: list[dict[str, Any]],
        recent_episodic_summaries: list[dict[str, Any]],
        recent_semantic_summaries: list[dict[str, Any]],
        confirmed_snippets: list[str],
        now_ts: int,
        profile_user_id: str,
        current_visual_payload: Any = None,
        extra_user_context: str = "",
        client_context: ClientProtocolContext | None = None,
        resource_manifest: ResourceManifest | None = None,
        character_pack_id: str = "",
        allow_tool_call: bool = True,
        final_debug_enabled: bool | None = None,
        enable_native_tools: bool = False,
    ) -> dict[str, Any]:
        client_context = client_context or self._resolve_client_protocol_context({})
        prompt_profile = self._get_prompt_profile_registry().resolve(client_context)
        effective_allow_tool_call = bool(
            allow_tool_call
            and prompt_profile.includes(PromptModule.TOOLS)
            and client_context.has_capability(ClientCapability.TOOL_ACTIONS)
        )
        requested_debug_enabled = bool(
            getattr(config, "FINAL_DEBUG", False)
            if final_debug_enabled is None
            else final_debug_enabled
        )
        debug_enabled = bool(requested_debug_enabled and prompt_profile.supports_thought_debug)
        if client_context.effective_mode != ClientMode.QQ_TEXT:
            resource_manifest = resource_manifest or self.resource_manifest
        manifest = resource_manifest.refresh() if resource_manifest else None
        runtime_projection = self._get_user_runtime_projection(profile_user_id)
        user_bgm_tracks = list(runtime_projection.get("extra_bgm_tracks") or [])
        user_scene_groups = list(runtime_projection.get("extra_scene_groups") or [])
        user_character_outfits = list(runtime_projection.get("extra_character_outfits") or [])
        desktop_pet_character_only = client_context.effective_mode == ClientMode.DESKTOP_PET
        character_pack_persona_enabled = client_context.effective_mode in {ClientMode.DESKTOP_PET, ClientMode.QQ_TEXT}
        raw_text = render_chat_timeline(recent_raw)
        _history_records, current_record = self._split_history_records(
            recent_raw=recent_raw,
            user_message=user_message,
            now_ts=now_ts,
        )
        current_message_text = self._render_current_message_line(
            current_user_record=current_record,
        )
        episodic_summary_text = render_summary_timeline(
            recent_episodic_summaries,
            store=self.store,
        )
        semantic_summary_text = render_semantic_summary_timeline(
            recent_semantic_summaries,
            store=self.store,
        )
        memory_text = "\n\n".join(confirmed_snippets) if confirmed_snippets else ""
        extra_context = str(extra_user_context or "").strip()
        attachment_service = self._get_attachment_inbox_service()
        attachment_focus_context = (
            attachment_service.build_prompt_context(
                profile_user_id=profile_user_id,
                session_id=session_id,
            )
            if (
                attachment_service is not None
                and prompt_profile.includes(PromptModule.EXTRA_CONTEXT)
                and client_context.effective_mode in {ClientMode.QQ_TEXT, ClientMode.DESKTOP_PET}
            )
            else ""
        )
        generated_file_service = self._get_generated_file_service()
        generated_file_context = (
            generated_file_service.build_prompt_context(
                profile_user_id=profile_user_id,
                session_id=session_id,
                limit=8,
            )
            if (
                generated_file_service is not None
                and prompt_profile.includes(PromptModule.EXTRA_CONTEXT)
                and client_context.effective_mode in {ClientMode.QQ_TEXT, ClientMode.DESKTOP_PET}
            )
            else ""
        )
        workspace_file_service = self._get_workspace_file_service()
        workspace_file_context = (
            workspace_file_service.build_prompt_context(
                profile_user_id=profile_user_id,
                session_id=session_id,
            )
            if (
                workspace_file_service is not None
                and prompt_profile.includes(PromptModule.EXTRA_CONTEXT)
                and client_context.effective_mode == ClientMode.DESKTOP_PET
            )
            else ""
        )
        task_workspace_service = self._get_task_workspace_service()
        task_workspace_context = (
            task_workspace_service.build_prompt_context(
                profile_user_id=profile_user_id,
                session_id=session_id,
            )
            if (
                task_workspace_service is not None
                and prompt_profile.includes(PromptModule.EXTRA_CONTEXT)
            )
            else ""
        )
        pending_gift_context = (
            self.gift_service.build_pending_prompt_context(
                profile_user_id=profile_user_id,
                session_id=session_id,
                limit=3,
            )
            if prompt_profile.includes(PromptModule.PENDING_GIFTS)
            else ""
        )
        current_visual_context_payload = self._resolve_current_visual_payload(
            session_id=session_id,
            current_visual_payload=current_visual_payload,
        )
        scene_observation_context = (
            self.vision_service.build_scene_prompt_context(
                visual_payload=current_visual_context_payload,
                extra_bgm_tracks=user_bgm_tracks,
                extra_scene_groups=user_scene_groups,
                extra_character_outfits=user_character_outfits,
            )
            if self.vision_service is not None and prompt_profile.includes(PromptModule.SCENE_OBSERVATION) and not desktop_pet_character_only
            else ""
        )
        outfit_observation_context = (
            self.vision_service.build_outfit_prompt_context(
                visual_payload=current_visual_context_payload,
                extra_bgm_tracks=user_bgm_tracks,
                extra_scene_groups=user_scene_groups,
                extra_character_outfits=user_character_outfits,
            )
            if self.vision_service is not None and prompt_profile.includes(PromptModule.OUTFIT_OBSERVATION) and not desktop_pet_character_only
            else ""
        )
        focused_gift = (
            self.gift_service.resolve_focus_asset(
                profile_user_id=profile_user_id,
                session_id=session_id,
                asset_id="",
            )
            if prompt_profile.includes(PromptModule.FOCUSED_GIFT_OBSERVATION)
            else None
        )
        gift_observation_context = (
            self.vision_service.build_gift_prompt_context(asset=focused_gift)
            if self.vision_service is not None and focused_gift is not None
            else ""
        )
        persona_service = self._get_persona_card_service()
        profile_persona_enabled = not (character_pack_persona_enabled and bool(character_pack_id))
        persona_context = (
            persona_service.build_prompt_context(
                profile_user_id=profile_user_id,
                session_id=session_id,
                visible_limit=5,
            )
            if (
                profile_persona_enabled
                and persona_service is not None
                and prompt_profile.includes(PromptModule.PERSONA)
            )
            else {"system_context": "", "reference_context": "", "active_id": ""}
        )
        character_pack_persona_context = (
            self._build_desktop_pet_character_pack_prompt_context(
                character_pack_id=character_pack_id,
                resource_manifest=resource_manifest,
                client_mode=client_context.effective_mode.value,
            )
            if character_pack_persona_enabled and prompt_profile.includes(PromptModule.PERSONA)
            else {"system_context": "", "reference_context": "", "active_id": ""}
        )
        if character_pack_persona_enabled and character_pack_id:
            context_library_service = getattr(
                getattr(self, "desktop_pet_character_resources", None),
                "context_libraries",
                None,
            )
            automatic_context_builder = getattr(
                context_library_service,
                "build_automatic_context",
                None,
            )
            if automatic_context_builder is not None:
                try:
                    automatic_context = str(
                        automatic_context_builder(character_pack_id, user_message) or ""
                    ).strip()
                except Exception as exc:
                    logger.warning("automatic character context loading failed: %s", exc)
                    automatic_context = ""
                if automatic_context:
                    character_pack_persona_context = dict(character_pack_persona_context)
                    existing_reference = str(
                        character_pack_persona_context.get("reference_context") or ""
                    ).strip()
                    character_pack_persona_context["reference_context"] = "\n\n".join(
                        part for part in [existing_reference, automatic_context] if part
                    )
        persona_context = self._merge_prompt_persona_contexts(
            character_pack_persona_context,
            persona_context,
        )
        visual_observation_sections = [
            text
            for text in [
                scene_observation_context,
                outfit_observation_context,
            ]
            if text
        ]
        extra_context_candidates = [
            (
                "client_mode",
                self._build_client_mode_prompt_context(client_context)
                if prompt_profile.includes(PromptModule.CLIENT_MODE)
                else "",
            ),
            (
                "relationship",
                self._build_memory_relationship_context(
                    profile_user_id=profile_user_id,
                    character_pack_id=character_pack_id,
                    now_ts=now_ts,
                ),
            ),
            ("task_workspace", task_workspace_context),
            ("workspace_files", workspace_file_context),
            ("attachment_focus", attachment_focus_context),
            ("generated_files", generated_file_context),
            ("pending_gifts", pending_gift_context),
            ("gift_observation", gift_observation_context),
            (
                "turn_extra_context",
                extra_context if prompt_profile.includes(PromptModule.EXTRA_CONTEXT) else "",
            ),
        ]
        extra_context_audit_sections = self._build_extra_context_audit_sections(extra_context_candidates)
        extra_context_sections = [section["text"] for section in extra_context_audit_sections]
        merged_extra_context = "\n\n".join(extra_context_sections) if extra_context_sections else "(无额外上下文)"
        visual_defaults = (
            resource_manifest.build_runtime_manifest(
                extra_bgm_tracks=user_bgm_tracks,
                extra_scene_groups=user_scene_groups,
                extra_character_outfits=user_character_outfits,
            )["defaults"]
            if manifest
            else {
                "major": "default",
                "minor": "default",
                "background": "evening_classroom",
                "bgm": "",
                "outfit": "default",
                "emotion": "normal",
            }
        )
        if resource_manifest and current_visual_context_payload:
            try:
                current_visual_defaults = resource_manifest.normalize_visual_output(
                    json.loads(json.dumps(current_visual_context_payload)),
                    extra_bgm_tracks=user_bgm_tracks,
                    extra_scene_groups=user_scene_groups,
                    extra_character_outfits=user_character_outfits,
                )
                visual_defaults = dict(visual_defaults)
                if desktop_pet_character_only:
                    visual_defaults["outfit"] = str(
                        current_visual_defaults.get("character", {}).get("outfit") or visual_defaults["outfit"]
                    )
                    visual_defaults["emotion"] = str(current_visual_defaults.get("emotion") or visual_defaults["emotion"])
                else:
                    current_scene = current_visual_defaults.get("scene") if isinstance(current_visual_defaults, dict) else {}
                    current_character = current_visual_defaults.get("character") if isinstance(current_visual_defaults, dict) else {}
                    if isinstance(current_scene, dict):
                        visual_defaults["major"] = str(current_scene.get("major") or visual_defaults["major"])
                        visual_defaults["minor"] = str(current_scene.get("minor") or visual_defaults["minor"])
                        visual_defaults["background"] = str(current_scene.get("background") or visual_defaults["background"])
                        visual_defaults["bgm"] = str(current_scene.get("bgm") or visual_defaults["bgm"])
                    if isinstance(current_character, dict):
                        visual_defaults["outfit"] = str(current_character.get("outfit") or visual_defaults["outfit"])
                    visual_defaults["emotion"] = str(current_visual_defaults.get("emotion") or visual_defaults["emotion"])
            except Exception as exc:
                logger.warning("current visual defaults failed: %s", exc)
        if client_context.effective_mode == ClientMode.QQ_TEXT:
            resource_context = (
                "QQ 端不渲染立绘；emotion 的可选值已由当前角色包表情图片清单约束。"
                if resource_manifest
                else "QQ 端不渲染立绘，当前角色包没有可用的表情图片清单。"
            )
        else:
            resource_context = (
                resource_manifest.build_character_prompt_context(
                    extra_character_outfits=user_character_outfits,
                )
                if desktop_pet_character_only
                else resource_manifest.build_prompt_context(
                    extra_bgm_tracks=user_bgm_tracks,
                    extra_scene_groups=user_scene_groups,
                    extra_character_outfits=user_character_outfits,
                )
            ) if resource_manifest and prompt_profile.includes(PromptModule.RESOURCE_MANIFEST) else "当前没有额外的视觉资源。"
        current_visual_context = (
            self._build_current_visual_context(
                profile_user_id=profile_user_id,
                session_id=session_id,
                current_visual_payload=current_visual_payload,
                visual_payload=current_visual_context_payload,
                runtime_projection=runtime_projection,
                character_only=desktop_pet_character_only,
                resource_manifest=resource_manifest,
            )
            if prompt_profile.includes(PromptModule.CURRENT_VISUAL_STATE)
            else "(当前客户端模式不需要完整演出状态。)"
        )
        if visual_observation_sections:
            current_visual_context = "\n\n".join([current_visual_context, *visual_observation_sections])
        mode_prompt_override = prompt_profile.mode_prompt_override(debug_enabled=debug_enabled)
        if resource_manifest and client_context.effective_mode in {
            ClientMode.DESKTOP_PET,
            ClientMode.QQ_TEXT,
        }:
            default_emotion_json = json.dumps(
                str(visual_defaults.get("emotion") or "normal"),
                ensure_ascii=False,
            )
            mode_prompt_override = mode_prompt_override.replace(
                '"emotion":"normal"',
                f'"emotion":{default_emotion_json}',
            )
        native_tools: list[dict[str, Any]] = []
        native_legacy_exclusions: set[str] = set()
        if enable_native_tools:
            native_capability_selection = self._resolve_capability_selection(
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
            )
            native_plan = tool_orchestration_engine.build_native_tool_decision_plan(
                self._resolve_tool_handlers(
                    client_context=client_context,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                ),
                allow_tool_call=effective_allow_tool_call,
                provider_supports_native_tools=self.llm.chat_supports_native_tools(),
                allowed_tool_names=native_capability_selection.tool_names,
            )
            if native_plan.enabled:
                native_tools = native_plan.tools
                native_legacy_exclusions = native_plan.legacy_prompt_exclusions
            elif native_plan.status == "unsupported":
                self.llm.record_metric("native_tool_provider_unsupported")
        generation_context = self._get_prompt_builder().build_final_generation_context(
            now_ts=now_ts,
            raw_text=raw_text,
            current_message_text=current_message_text,
            episodic_summary_text=episodic_summary_text,
            semantic_summary_text=semantic_summary_text,
            memory_text=memory_text,
            current_visual_context=current_visual_context,
            resource_context=resource_context,
            extra_context=merged_extra_context,
            extra_context_audit_sections=extra_context_audit_sections,
            persona_system_context=str(persona_context.get("system_context") or ""),
            persona_reference_context=str(persona_context.get("reference_context") or ""),
            persona_active_id=str(persona_context.get("active_id") or ""),
            visual_defaults=visual_defaults,
            allow_tool_call=effective_allow_tool_call,
            tool_prompt_context=self._build_tool_prompt_context(
                allow_tool_call=effective_allow_tool_call,
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
                exclude_tool_types=native_legacy_exclusions,
            ),
            debug_enabled=debug_enabled,
            system_prompt_override=prompt_profile.system_prompt_override,
            mode_prompt_override=mode_prompt_override,
        )
        if desktop_pet_character_only and client_context.has_capability(ClientCapability.AUDIO_PLAYBACK):
            fallback_payload = generation_context.get("fallback")
            if isinstance(fallback_payload, dict):
                fallback_payload["activity"] = None
        if native_tools:
            native_tool_round_instruction = self._build_native_tool_round_instruction(native_tools)
            generation_context["system_prompt"] = "\n\n".join(
                part
                for part in [
                    str(generation_context.get("system_prompt") or "").strip(),
                    native_tool_round_instruction,
                ]
                if part
            )
        generation_context["allow_tool_call"] = effective_allow_tool_call
        generation_context["native_tools"] = native_tools
        generation_context["native_tool_choice"] = "auto" if native_tools else ""
        generation_context["prompt_profile"] = prompt_profile.to_public_dict()
        if client_context.effective_mode == ClientMode.QQ_TEXT:
            fallback_payload = generation_context.get("fallback")
            if isinstance(fallback_payload, dict):
                fallback_payload.pop("character", None)
                fallback_payload.pop("scene", None)
                fallback_payload.pop("live2d", None)
                fallback_payload.pop("pet", None)
                fallback_payload.pop("activity", None)
        return generation_context

    def _normalize_final_output(
        self,
        *,
        result: dict[str, Any] | None,
        visual_defaults: dict[str, Any],
        profile_user_id: str = "",
        session_id: str = "",
        allow_tool_call: bool,
        debug_enabled: bool,
        client_context: ClientProtocolContext | None = None,
        resource_manifest: ResourceManifest | None = None,
        user_message: str = "",
    ) -> dict[str, Any]:
        return final_output_engine.normalize_final_output(
            self,
            result=result,
            visual_defaults=visual_defaults,
            profile_user_id=profile_user_id,
            session_id=session_id,
            allow_tool_call=allow_tool_call,
            debug_enabled=debug_enabled,
            client_context=client_context,
            resource_manifest=resource_manifest,
            user_message=user_message,
        )

    def _normalize_speech_payload(
        self,
        *,
        speech: Any,
        speech_segments: Any,
        fallback_to_default: bool = True,
    ) -> tuple[str, list[str]]:
        return final_output_engine.normalize_speech_payload(
            speech=speech,
            speech_segments=speech_segments,
            fallback_to_default=fallback_to_default,
        )

    def _apply_persona_state_to_final_output(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        final_output: dict[str, Any],
        now_ts: int,
        source_id: str = "",
        tool_result: ToolExecutionResult | None = None,
    ) -> dict[str, Any]:
        return final_output_engine.apply_persona_state_to_final_output(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            final_output=final_output,
            now_ts=now_ts,
            source_id=source_id,
            tool_result=tool_result,
        )

    def _normalize_code_snippet(self, value: Any) -> str:
        return final_output_engine.normalize_code_snippet(value)

    def _normalize_activity_action(self, value: Any) -> dict[str, Any] | None:
        return final_output_engine.normalize_activity_action(value)

    def _build_assistant_dialogue_turn(self, speech: Any, *, speaker_name: str | None = None) -> dict[str, str] | None:
        return final_output_engine.build_assistant_dialogue_turn(speech, speaker_name=speaker_name)

    def _build_dialogue_turns(
        self,
        *,
        preface_turn: dict[str, str] | list[dict[str, str]] | None,
        npc_turns: list[dict[str, Any]],
        final_speech: Any,
        final_speech_segments: Any = None,
        speaker_name: str | None = None,
    ) -> list[dict[str, str]]:
        return final_output_engine.build_dialogue_turns(
            preface_turn=preface_turn,
            npc_turns=npc_turns,
            final_speech=final_speech,
            final_speech_segments=final_speech_segments,
            speaker_name=speaker_name,
        )

    def _max_tool_rounds(self) -> int:
        return tool_orchestration_engine.max_tool_rounds()

    def _resolve_tool_round_budget(
        self,
        *,
        current_budget: int,
        tool_call: dict[str, Any],
        client_context: ClientProtocolContext | None = None,
        profile_user_id: str = "",
        session_id: str = "",
    ) -> int:
        return tool_orchestration_engine.resolve_tool_round_budget(
            self._resolve_tool_handlers(
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
            ),
            tool_call,
            current_budget=current_budget,
        )

    def _tool_call_signature(self, tool_call: dict[str, Any]) -> str:
        return tool_orchestration_engine.tool_call_signature(tool_call)

    def _describe_tool_call_for_prompt(self, tool_call: dict[str, Any]) -> str:
        return tool_orchestration_engine.describe_tool_call_for_prompt(tool_call)

    def _build_tool_working_stream_event(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        tool_type = str((tool_call or {}).get("type") or "unknown").strip() or "unknown"
        return {
            "type": "assistant_working",
            "status": "running",
            "phase": "tool_call",
            "tool_type": tool_type,
            "message": "我查一下。",
        }

    def _should_stop_after_tool_events(self, events: list[dict[str, Any]]) -> bool:
        blocking_statuses = {"unavailable", "error", "failed", "failure"}
        for event in events or []:
            if not isinstance(event, dict):
                continue
            status = str(event.get("status") or "").strip().lower()
            if status in blocking_statuses:
                return True
        return False

    def _prepare_tool_round_decision(
        self,
        *,
        final_output: dict[str, Any],
        user_message: str,
        client_context: ClientProtocolContext,
        profile_user_id: str,
        session_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
        native_tool_call = final_output.pop(NATIVE_TOOL_CALL_FIELD, None)
        raw_tool_call = native_tool_call if isinstance(native_tool_call, dict) and native_tool_call else None
        if raw_tool_call is None:
            final_output = self._promote_narrated_tool_call(
                final_output,
                user_message=user_message,
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
            )
            raw_tool_call = final_output.get("tool_call")
        else:
            final_output["tool_call"] = None
        tool_call = self._normalize_tool_call(
            raw_tool_call,
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )
        rejection = (
            self._describe_tool_call_rejection(
                raw_tool_call,
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
            )
            if raw_tool_call and not tool_call
            else ""
        )
        return final_output, tool_call, rejection

    def _record_tool_call_rejection(
        self,
        *,
        final_output: dict[str, Any],
        rejection: str,
        tool_followups: list[str],
        session_id: str,
        tool_round_index: int,
        max_tool_rounds: int,
    ) -> bool:
        logger.warning(
            "tool_call_rejected session=%s reason_tool=%s",
            session_id,
            str((final_output.get("tool_call") or {}).get("type") or ""),
        )
        tool_followups.append(rejection)
        return tool_round_index < max_tool_rounds - 1

    def _build_tool_round_extra_context(
        self,
        *,
        turn_extra_user_context: str,
        tool_followups: list[str],
        allow_more: bool,
        stop_reason: str = "",
    ) -> str:
        return self._merge_extra_user_context(
            turn_extra_user_context,
            self._build_multi_tool_followup_context(
                tool_followups,
                allow_more=allow_more,
                stop_reason=stop_reason,
            ),
        )

    def _record_assistant_preface_for_tool_call(
        self,
        *,
        tool_call: dict[str, Any],
        final_output: dict[str, Any],
        preface_turns: list[dict[str, str]],
        recent_raw_for_turn: list[dict[str, Any]],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        now_ts: int,
        date_label: str,
        time_of_day: str,
    ) -> None:
        internal_read_tool = str(tool_call.get("type") or "") in {
            "retrieve_memory",
            "read_memory_timeline",
            "load_character_context",
        }
        preface_turn = None if internal_read_tool else self._build_assistant_dialogue_turn(final_output.get("speech"))
        if not preface_turn:
            return
        preface_turns.append(preface_turn)
        preface_record = self.store.add_message(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            role="assistant",
            content=preface_turn["speech"],
            timestamp=now_ts,
            date_label=date_label,
            time_of_day=time_of_day,
            semantic_tags=extract_semantic_tags(preface_turn["speech"]),
            memory_metadata=self._build_assistant_timeline_metadata(final_output),
        )
        self._upsert_raw_record(preface_record)
        self._schedule_summary_cycle(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        recent_raw_for_turn.append(preface_record)

    def _execute_and_record_tool_round(
        self,
        *,
        tool_call: dict[str, Any],
        final_output: dict[str, Any],
        tool_results: list[ToolExecutionResult],
        tool_events: list[dict[str, Any]],
        tool_followups: list[str],
        tool_turns: list[dict[str, Any]],
        recent_raw_for_turn: list[dict[str, Any]],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        now_ts: int,
        current_user_source_id: str,
        client_context: ClientProtocolContext,
        memory_exclude_source_ids: list[str],
        request_context: dict[str, Any],
    ) -> tuple[ToolExecutionResult | None, list[dict[str, Any]]]:
        tool_result = self._execute_tool_call(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            tool_call=tool_call,
            visual_payload=final_output,
            now_ts=now_ts,
            current_user_source_id=current_user_source_id,
            client_context=client_context,
            memory_exclude_source_ids=memory_exclude_source_ids,
            request_context=request_context,
        )
        if not tool_result:
            return None, []

        tool_results.append(tool_result)
        current_events = list(tool_result.stream_events)
        workspace_events, workspace_followup = self._record_tool_result_artifacts_in_task_workspace(
            profile_user_id=profile_user_id,
            session_id=session_id,
            tool_result=tool_result,
            now_ts=now_ts,
        )
        current_events.extend(workspace_events)
        tool_events.extend(current_events)
        tool_followups.append(
            f"第 {len(tool_results)} 次工具（{tool_result.tool_type}）结果：\n"
            f"{tool_orchestration_engine.shape_tool_followup(tool_result.followup_context, tool_type=tool_result.tool_type)}"
        )
        if workspace_followup:
            tool_followups.append(workspace_followup)
        current_tool_turns = list(tool_result.raw_turns)
        tool_turns.extend(current_tool_turns)
        for tool_turn in current_tool_turns:
            speaker = str(tool_turn.get("speaker") or "NPC").strip() or "NPC"
            speech = str(tool_turn.get("speech") or "").strip()
            if not speech:
                continue
            tool_record = self.store.add_message(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                role=f"npc:{speaker}",
                content=speech,
                timestamp=max(now_ts, int(time.time())),
                semantic_tags=extract_semantic_tags(speech),
            )
            self._upsert_raw_record(tool_record)
            self._schedule_summary_cycle(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
            recent_raw_for_turn.append(tool_record)
        return tool_result, current_events

    def _record_tool_result_artifacts_in_task_workspace(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        tool_result: ToolExecutionResult,
        now_ts: int,
    ) -> tuple[list[dict[str, Any]], str]:
        return task_workspace_engine.record_tool_result_artifacts_in_task_workspace(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            tool_result=tool_result,
            now_ts=now_ts,
        )

    def _extract_task_workspace_artifacts_from_tool_events(
        self,
        *,
        tool_type: str,
        stream_events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return task_workspace_engine.extract_task_workspace_artifacts_from_tool_events(
            tool_type=tool_type,
            stream_events=stream_events,
        )

    def _task_workspace_artifact_from_generated_file(
        self,
        *,
        generated: Any,
        tool_type: str,
        send_to_user: bool,
    ) -> dict[str, Any] | None:
        return task_workspace_engine.task_workspace_artifact_from_generated_file(
            generated=generated,
            tool_type=tool_type,
            send_to_user=send_to_user,
        )

    def _task_workspace_artifact_from_attachment_item(
        self,
        *,
        item: Any,
        tool_type: str,
    ) -> dict[str, Any] | None:
        return task_workspace_engine.task_workspace_artifact_from_attachment_item(
            item=item,
            tool_type=tool_type,
        )

    def _merge_task_workspace_artifacts(
        self,
        *,
        existing: list[dict[str, Any]],
        additions: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return task_workspace_engine.merge_task_workspace_artifacts(
            existing=existing,
            additions=additions,
        )

    def _task_workspace_artifact_identity(self, artifact: dict[str, Any]) -> str:
        return task_workspace_engine.task_workspace_artifact_identity(artifact)

    def _compact_task_workspace_for_event(self, task: dict[str, Any]) -> dict[str, Any]:
        return task_workspace_engine.compact_task_workspace_for_event(task)

    def _build_multi_tool_followup_context(
        self,
        tool_followups: list[str],
        *,
        allow_more: bool,
        stop_reason: str = "",
    ) -> str:
        return tool_orchestration_engine.build_multi_tool_followup_context(
            tool_followups,
            allow_more=allow_more,
            stop_reason=stop_reason,
        )

    def _normalize_memory_tags(self, value: Any) -> list[str]:
        raw_items: list[str] = []
        if isinstance(value, list):
            raw_items = [str(item).strip() for item in value if str(item).strip()]
        elif isinstance(value, str):
            normalized = (
                str(value)
                .replace("，", ",")
                .replace("、", ",")
                .replace("；", ",")
                .replace(";", ",")
                .replace("|", ",")
            )
            raw_items = parse_joined_tags(normalized)

        normalized_items: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            compact = normalize_text(item).strip("[](){}\"' ")
            if not compact or len(compact) > 16:
                continue
            dedupe_key = compact.lower()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized_items.append(compact)
            if len(normalized_items) >= 4:
                break
        return normalized_items

    def _apply_memory_tags_to_user_record(
        self,
        *,
        user_record: dict[str, Any],
        memory_tags: list[str],
    ) -> dict[str, Any]:
        merged_tags: list[str] = []
        seen: set[str] = set()
        for item in [*memory_tags, *list(user_record.get("semantic_tags") or [])]:
            tag = normalize_text(item)
            if not tag:
                continue
            dedupe_key = tag.lower()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            merged_tags.append(tag)

        user_record["semantic_tags"] = merged_tags
        self.store.update_message_semantic_tags(user_record["source_id"], merged_tags)
        self._upsert_raw_record(user_record)
        return user_record

    def _apply_memory_metadata_to_user_record(
        self,
        *,
        user_record: dict[str, Any],
        memory_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = dict(memory_metadata or {})
        user_record["memory_metadata"] = metadata
        self.store.update_message_memory_metadata(user_record["source_id"], metadata)
        self._upsert_raw_record(user_record)
        return user_record

    def _build_assistant_timeline_metadata(
        self,
        final_output: dict[str, Any],
    ) -> dict[str, Any]:
        output = final_output if isinstance(final_output, dict) else {}
        memory_metadata = output.get("memory_metadata")
        mood_tags = (
            list(memory_metadata.get("mood_tags") or [])
            if isinstance(memory_metadata, dict)
            else []
        )
        character = output.get("character")
        outfit = (
            str(character.get("outfit") or "").strip()
            if isinstance(character, dict)
            else ""
        )
        return {
            "response_emotion": str(output.get("emotion") or "").strip(),
            "response_outfit": outfit,
            "mood_tags": mood_tags,
        }

    def _build_memory_relationship_context(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str,
        now_ts: int,
    ) -> str:
        service = getattr(self, "memory_timeline_service", None)
        if service is None:
            return ""
        try:
            return str(
                service.build_acquaintance_prompt(
                    profile_user_id=profile_user_id,
                    character_pack_id=character_pack_id,
                    now_ts=now_ts,
                )
                or ""
            ).strip()
        except Exception as exc:
            logger.warning("memory relationship prompt failed: %s", exc)
            return ""

    def _normalize_choices(self, value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []

        normalized: list[dict[str, str]] = []
        seen_texts: set[str] = set()
        for index, item in enumerate(value, start=1):
            if isinstance(item, dict):
                text = str(item.get("text") or item.get("label") or "").strip()
                choice_id = str(item.get("id") or "").strip()
            else:
                text = str(item or "").strip()
                choice_id = ""

            if not text:
                continue
            dedupe_key = normalize_text(text).lower()
            if dedupe_key in seen_texts:
                continue
            seen_texts.add(dedupe_key)
            normalized.append(
                {
                    "id": choice_id or f"choice_{index}",
                    "text": text[:40],
                }
            )
            if len(normalized) >= 4:
                break
        return normalized

    def _build_tool_handlers(self) -> dict[str, BaseToolHandler]:
        return {
            "retrieve_memory": RetrieveMemoryToolHandler(
                retrieve_fn=self._execute_retrieve_memory_tool,
            ),
            "read_memory_timeline": ReadMemoryTimelineToolHandler(
                timeline_service=self.memory_timeline_service,
            ),
            "load_character_context": LoadCharacterContextToolHandler(
                context_library_service=getattr(
                    self.desktop_pet_character_resources,
                    "context_libraries",
                    None,
                ),
            ),
            "call_npc": CallNPCToolHandler(
                npc_runtime=self.npc_runtime,
                describe_scene=self._describe_tool_scene_context,
                build_followup_context=self._build_npc_followup_context,
            ),
            "set_reminder": SetReminderToolHandler(store=self.store),
            "list_reminders": ListRemindersToolHandler(store=self.store),
            "cancel_reminder": CancelReminderToolHandler(store=self.store),
            "check_inventory": CheckInventoryToolHandler(gift_service=self.gift_service),
            "inspect_attachment": InspectAttachmentToolHandler(
                attachment_service=self._get_attachment_inbox_service()
            ),
            "read_attachment_section": ReadAttachmentSectionToolHandler(
                attachment_service=self._get_attachment_inbox_service()
            ),
            "sync_attachment_workspace": SyncAttachmentWorkspaceToolHandler(
                attachment_service=self._get_attachment_inbox_service()
            ),
            "clear_attachment_focus": ClearAttachmentFocusToolHandler(
                attachment_service=self._get_attachment_inbox_service()
            ),
            "list_workspace": ListWorkspaceToolHandler(
                workspace_service=self._get_workspace_file_service()
            ),
            "read_workspace": ReadWorkspaceToolHandler(
                workspace_service=self._get_workspace_file_service()
            ),
            "focus_workspace": FocusWorkspaceToolHandler(
                workspace_service=self._get_workspace_file_service()
            ),
            "register_workspace_items": RegisterWorkspaceItemsToolHandler(
                workspace_service=self._get_workspace_file_service(),
                attachment_ingest_service=self._get_attachment_ingest_service(),
            ),
            "retry_attachment": RetryAttachmentToolHandler(
                attachment_ingest_service=self._get_attachment_ingest_service()
            ),
            "fetch_media_from_url": FetchMediaFromUrlToolHandler(
                attachment_ingest_service=self._get_attachment_ingest_service()
            ),
            "compose_file": ComposeFileToolHandler(
                generated_file_service=self._get_generated_file_service()
            ),
            "revise_generated_file": ReviseGeneratedFileToolHandler(
                generated_file_service=self._get_generated_file_service()
            ),
            "apply_style_to_existing_file": ApplyStyleToExistingFileToolHandler(
                generated_file_service=self._get_generated_file_service()
            ),
            "inspect_media_info": InspectMediaInfoToolHandler(
                generated_file_service=self._get_generated_file_service()
            ),
            "separate_audio_stems": SeparateAudioStemsToolHandler(
                generated_file_service=self._get_generated_file_service()
            ),
            "clean_voice_track": CleanVoiceTrackToolHandler(
                generated_file_service=self._get_generated_file_service()
            ),
            "transcribe_media": TranscribeMediaToolHandler(
                generated_file_service=self._get_generated_file_service()
            ),
            "prepare_voice_dataset": PrepareVoiceDatasetToolHandler(
                generated_file_service=self._get_generated_file_service()
            ),
            "inspect_generated_file": InspectGeneratedFileToolHandler(
                generated_file_service=self._get_generated_file_service()
            ),
            "send_file": SendFileToolHandler(
                generated_file_service=self._get_generated_file_service()
            ),
            "convert_media_file": ConvertMediaFileToolHandler(
                generated_file_service=self._get_generated_file_service()
            ),
            "send_generated_file": SendGeneratedFileToolHandler(
                generated_file_service=self._get_generated_file_service()
            ),
            "send_sticker": SendStickerToolHandler(
                sticker_service=self.sticker_assets,
            ),
            "manage_generated_file": ManageGeneratedFileToolHandler(
                generated_file_service=self._get_generated_file_service()
            ),
            "manage_gift": ManageGiftToolHandler(
                gift_service=self.gift_service,
                observe_image_fn=self.observe_gift_image_once,
            ),
            "manage_artifact": ManageArtifactToolHandler(
                artifact_service=self.artifact_service,
            ),
            "manage_persona": ManagePersonaToolHandler(
                persona_service=self.persona_card_service,
            ),
            "manage_task_workspace": ManageTaskWorkspaceToolHandler(
                task_workspace_service=self._get_task_workspace_service(),
            ),
            "delegate_task": DelegateTaskToolHandler(
                task_worker_service=self._get_task_worker_service(),
            ),
            "web_search": WebSearchToolHandler(
                config_base_dir=Path(getattr(config, "DATA_DIR", "users_data") or "users_data"),
            ),
            "open_browser": OpenBrowserToolHandler(),
            "open_music_search": OpenMusicSearchToolHandler(),
            "browser_page": BrowserPageToolHandler(),
        }

    def _resolve_tool_handlers(
        self,
        *,
        client_context: ClientProtocolContext | None = None,
        profile_user_id: str = "",
        session_id: str = "",
    ) -> dict[str, BaseToolHandler]:
        handlers = getattr(self, "tool_handlers", {}) or {}
        dynamic_handlers = self._build_mcp_adapter_tool_handlers(
            profile_user_id=profile_user_id,
            client_context=client_context,
        )
        if client_context is None:
            return {**dict(handlers), **dynamic_handlers}

        selected_names = list(
            self._resolve_capability_selection(
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
            ).tool_names
        )
        return {
            tool_name: ({**handlers, **dynamic_handlers})[tool_name]
            for tool_name in selected_names
            if tool_name in {**handlers, **dynamic_handlers}
        }

    def _resolve_capability_selection(
        self,
        *,
        client_context: ClientProtocolContext | None = None,
        profile_user_id: str = "",
        session_id: str = "",
    ) -> CapabilitySelection:
        handlers = getattr(self, "tool_handlers", {}) or {}
        if client_context is None:
            return CapabilitySelection(
                light_hints=(),
                tool_names=tuple(handlers.keys()),
                module_names=("all_tools",),
            )
        if not str(profile_user_id or "").strip() or not str(session_id or "").strip():
            return CapabilitySelection(
                light_hints=(),
                tool_names=tuple(self._legacy_mode_tool_names(client_context)),
                module_names=("legacy_mode_pack",),
            )
        snapshot = self._build_capability_snapshot(
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )
        registry = getattr(self, "capability_registry", None) or CapabilityRegistry()
        selection = registry.select(snapshot)
        dynamic_handlers = self._build_mcp_adapter_tool_handlers(
            profile_user_id=profile_user_id,
            client_context=client_context,
        )
        if not dynamic_handlers:
            return selection
        dynamic_tool_names = tuple(name for name in dynamic_handlers.keys() if name not in selection.tool_names)
        if not dynamic_tool_names:
            return selection
        return CapabilitySelection(
            light_hints=(
                *selection.light_hints,
                "当前 profile 有已显式暴露给 prompt 的本地 MCP 工具；调用失败时不要假装完成，涉及高风险动作会先请求确认。",
            ),
            tool_names=(*selection.tool_names, *dynamic_tool_names),
            module_names=(*selection.module_names, "mcp_adapter_tools"),
            layer_names=(*selection.layer_names, "mcp"),
        )

    def _build_mcp_adapter_tool_handlers(
        self,
        *,
        profile_user_id: str = "",
        client_context: ClientProtocolContext | None = None,
    ) -> dict[str, BaseToolHandler]:
        if not str(profile_user_id or "").strip():
            return {}
        try:
            config_payload = load_capability_config(
                base_dir=Path(getattr(config, "DATA_DIR", "users_data") or "users_data"),
                profile_user_id=profile_user_id,
            )
        except Exception:
            return {}
        servers = config_payload.get("mcpServers") if isinstance(config_payload.get("mcpServers"), dict) else {}
        handlers: dict[str, BaseToolHandler] = {}
        for server_id, server_config in sorted(servers.items(), key=lambda item: str(item[0])):
            if not isinstance(server_config, dict) or not bool(server_config.get("enabled")):
                continue
            if not str(server_config.get("command") or "").strip():
                continue
            tools = [tool for tool in server_config.get("tools") or [] if isinstance(tool, dict)]
            prompt_tools = [
                tool
                for tool in tools
                if bool(tool.get("promptExposed") or tool.get("prompt_exposed"))
            ]
            if not prompt_tools:
                continue
            adapter = McpStdioCapabilityAdapter(
                provider_id=f"provider.mcp.{server_id}",
                server_id=str(server_id),
                server_config={**server_config, "serverId": str(server_id)},
                tool_configs=tuple(prompt_tools),
            )
            for tool in prompt_tools:
                descriptor = adapter._descriptor_for_tool(tool)
                if descriptor.id and descriptor.prompt_exposed:
                    handlers[descriptor.id] = AdapterCapabilityToolHandler(
                        capability_id=descriptor.id,
                        adapter=adapter,
                        descriptor=descriptor,
                    )
        return handlers

    def _legacy_mode_tool_names(self, client_context: ClientProtocolContext) -> list[str]:
        registry = getattr(self, "capability_registry", None) or CapabilityRegistry()
        return list(registry.tool_names_for_mode(client_context.effective_mode))

    def _build_capability_snapshot(
        self,
        *,
        client_context: ClientProtocolContext,
        profile_user_id: str,
        session_id: str,
    ) -> CapabilitySnapshot:
        attachments = self.store.list_attachment_inbox_items(
            profile_user_id=profile_user_id,
            session_id=session_id,
            statuses=["ready", "pending_observation", "failed"],
            limit=80,
        )
        generated_files = self.store.list_generated_files(
            profile_user_id=profile_user_id,
            session_id=session_id,
            statuses=["ready", "failed"],
            limit=40,
        )
        return CapabilitySnapshot(
            client_mode=client_context.effective_mode,
            has_any_attachment=bool(attachments),
            has_document_attachment=any(is_document_attachment(item) for item in attachments),
            has_media_attachment=any(is_media_attachment(item) for item in attachments),
            has_generated_file=bool(generated_files),
            has_document_generated_file=any(is_document_generated_file(item) for item in generated_files),
            has_media_generated_file=any(is_media_generated_file(item) for item in generated_files),
            has_pending_gift=False,
        )

    def _build_tool_prompt_context(
        self,
        *,
        allow_tool_call: bool,
        client_context: ClientProtocolContext | None = None,
        profile_user_id: str = "",
        session_id: str = "",
        exclude_tool_types: set[str] | None = None,
    ) -> str:
        if not allow_tool_call:
            return "本轮不要调用任何工具，tool_call 固定为 null。"

        selection = self._resolve_capability_selection(
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )
        handlers = self._resolve_tool_handlers(
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )
        excluded = {str(item).strip() for item in (exclude_tool_types or set()) if str(item).strip()}
        if excluded:
            handlers = {
                tool_type: handler
                for tool_type, handler in handlers.items()
                if str(tool_type) not in excluded
            }
        media_routing: list[str] = []
        if "media_workbench" in selection.module_names:
            media_routing = [*MEDIA_PRESET_ROUTING, ""]

        if not handlers:
            hints = [hint for hint in selection.light_hints if hint]
            if not hints and not media_routing:
                return "当前没有可用工具，tool_call 固定为 null。"
            parts: list[str] = []
            if hints:
                parts.append("【可用能力概览】")
                parts.extend(f"- {hint}" for hint in hints)
                parts.append("")
            parts.extend(media_routing)
            parts.append("当前没有需要展开的具体工具，tool_call 固定为 null。")
            return "\n".join(parts)

        lines: list[str] = []
        if selection.light_hints:
            lines.append("【可用能力概览】")
            for hint in selection.light_hints:
                lines.append(f"- {hint}")
            lines.append("")
        if client_context and client_context.effective_mode == ClientMode.DESKTOP_PET and "send_file" in selection.tool_names:
            lines.extend(
                [
                    "【桌宠文件交付】",
                    "- send_file 在桌宠里表示把已有文件交给手边工作台；如果用户明确说“打开”“显示位置”“放桌面”“复制路径”，"
                    "先完成必要的生成/转换，再对目标 handle 调用 send_file，并加 delivery_action："
                    "open、reveal、save_desktop 或 copy_path。只是让用户拿到文件时，可以不填 delivery_action。",
                    "",
                ]
            )
        lines.extend(media_routing)
        lines.append("【当前可调用工具】")
        for handler in handlers.values():
            instruction = str(handler.build_prompt_instruction() or "").strip()
            if instruction:
                lines.append(instruction)
        lines.append(
            "重要：真正调用工具只能写在 tool_call 字段；不要在 speech 里写“工具调用：...”或“我调用工具了”来代替。"
            "如果 tool_call 为 null，系统不会执行任何工具，也不要声称工具已经调用或失败。"
        )
        lines.append("如果不需要工具，tool_call 输出 null。一次只调用一个工具。")
        return "\n".join(lines)

    def _build_native_tool_round_instruction(self, native_tools: list[dict[str, Any]] | None) -> str:
        native_tool_names = sorted(
            {
                str(((tool.get("function") or {}).get("name") if isinstance(tool, dict) else "") or "").strip()
                for tool in native_tools or []
                if str(((tool.get("function") or {}).get("name") if isinstance(tool, dict) else "") or "").strip()
            }
        )
        name_text = "、".join(native_tool_names) if native_tool_names else "已提供的 native 工具"
        return (
            "【native 工具轮优先规则】\n"
            f"本轮已通过 provider native tools 提供：{name_text}。\n"
            f"若当前请求需要上述 native 工具，必须直接通过 provider tool_calls 调用；"
            "不要输出最终表现 JSON 正文，也不要在 JSON 的 tool_call 字段里手写这些 native 工具。\n"
            "只有仍在可用工具清单中、且没有通过 native schema 提供的 legacy 工具，才可以继续写入 JSON tool_call。\n"
            "如果不需要任何 legacy 工具，最终表现 JSON 的 tool_call 字段必须为 null。\n"
            "不要在 speech 里声称工具已调用、已完成或已失败；真实状态以系统工具结果为准。"
        )

    def _build_turn_extra_user_context(
        self,
        payload: dict[str, Any] | None,
        client_context: ClientProtocolContext | None,
    ) -> str:
        return desktop_context_engine.build_turn_extra_user_context(
            self,
            payload,
            client_context,
        )

    def _merge_extra_user_context(self, *parts: Any) -> str:
        return desktop_context_engine.merge_extra_user_context(*parts)

    def _build_desktop_context_prompt(
        self,
        desktop_context: Any,
        client_context: ClientProtocolContext | None,
    ) -> str:
        return desktop_context_engine.build_desktop_context_prompt(desktop_context, client_context)

    def _sanitize_desktop_context_text(self, value: Any, limit: int) -> str:
        return desktop_context_engine.sanitize_desktop_context_text(value, limit)

    def _build_desktop_activity_prompt(
        self,
        activity: Any,
        client_context: ClientProtocolContext | None,
        *,
        profile_user_id: str = "",
        session_id: str = "",
    ) -> str:
        return desktop_context_engine.build_desktop_activity_prompt(
            self,
            activity,
            client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )

    def _build_desktop_music_timeline_prompt(
        self,
        activity: dict[str, Any],
        *,
        profile_user_id: str = "",
        session_id: str = "",
    ) -> str:
        return desktop_context_engine.build_desktop_music_timeline_prompt(
            self,
            activity,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )

    def _safe_activity_seconds(self, value: Any) -> float:
        return desktop_context_engine.safe_activity_seconds(value)

    def _format_activity_time(self, value: Any) -> str:
        return desktop_context_engine.format_activity_time(value)

    def _build_client_mode_prompt_context(self, client_context: ClientProtocolContext | None) -> str:
        return desktop_context_engine.build_client_mode_prompt_context(client_context)

    def _build_task_worker_attachment_context(self, profile_user_id: str, session_id: str) -> str:
        service = self._get_attachment_inbox_service()
        if service is None:
            return ""
        return service.build_prompt_context(
            profile_user_id=profile_user_id,
            session_id=session_id,
            detail_limit=8,
            index_limit=24,
            pending_limit=8,
        )

    def _build_task_worker_generated_context(self, profile_user_id: str, session_id: str) -> str:
        service = self._get_generated_file_service()
        if service is None:
            return ""
        return service.build_prompt_context(
            profile_user_id=profile_user_id,
            session_id=session_id,
            limit=8,
        )

    def _normalize_tool_call(
        self,
        value: Any,
        *,
        client_context: ClientProtocolContext | None = None,
        profile_user_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any] | None:
        return tool_orchestration_engine.normalize_tool_call(
            self,
            value,
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )

    def _describe_tool_call_rejection(
        self,
        value: Any,
        *,
        client_context: ClientProtocolContext | None = None,
        profile_user_id: str = "",
        session_id: str = "",
    ) -> str:
        return tool_orchestration_engine.classify_tool_call_rejection(
            self,
            value,
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )

    def _promote_narrated_tool_call(
        self,
        final_output: dict[str, Any],
        *,
        user_message: str,
        client_context: ClientProtocolContext | None = None,
        profile_user_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        return tool_orchestration_engine.promote_narrated_tool_call(
            self,
            final_output,
            user_message=user_message,
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )

    def _execute_tool_call(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        tool_call: dict[str, Any],
        visual_payload: dict[str, Any],
        now_ts: int,
        current_user_source_id: str = "",
        client_context: ClientProtocolContext | None = None,
        memory_exclude_source_ids: list[str] | None = None,
        request_context: dict[str, Any] | None = None,
    ) -> ToolExecutionResult | None:
        return tool_orchestration_engine.execute_tool_call(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            tool_call=tool_call,
            visual_payload=visual_payload,
            now_ts=now_ts,
            current_user_source_id=current_user_source_id,
            client_context=client_context,
            memory_exclude_source_ids=memory_exclude_source_ids,
            request_context=request_context,
        )

    def _execute_retrieve_memory_tool(
        self,
        *,
        call: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        return retrieval_engine.execute_retrieve_memory_tool(
            self,
            call=call,
            context=context,
        )

    def _describe_tool_scene_context(self, visual_payload: dict[str, Any]) -> str:
        return visual_context_engine.describe_tool_scene_context(self, visual_payload)

    def _normalize_npc_tool_call(self, value: Any) -> dict[str, str] | None:
        handlers = getattr(self, "tool_handlers", {}) or {}
        handler = handlers.get("call_npc")
        if handler is not None:
            normalized = handler.normalize_call(value)
            return normalized if isinstance(normalized, dict) else None

        if not isinstance(value, dict):
            return None

        call_type = str(value.get("type") or "").strip()
        if call_type != "call_npc":
            return None

        query = str(value.get("query") or value.get("question") or value.get("prompt") or "").strip()
        if not query:
            return None

        npc_name = str(value.get("npc_name") or value.get("name") or "路人").strip() or "路人"
        npc_role = str(value.get("npc_role") or value.get("role") or "通用NPC").strip() or "通用NPC"
        return {
            "type": "call_npc",
            "npc_name": npc_name[:24],
            "npc_role": npc_role[:40],
            "query": query[:120],
        }

    def _build_npc_followup_context(self, npc_turn: dict[str, Any]) -> str:
        speaker = str(npc_turn.get("speaker") or "NPC").strip() or "NPC"
        speech = str(npc_turn.get("speech") or "").strip()
        if not speech:
            return ""
        return (
            f"场景里刚刚有一位 NPC 说了话：\n"
            f"{speaker}: {speech}\n\n"
            f"请你在知道这句 NPC 台词的前提下继续自然回应。"
        )

    def consume_due_reminders(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        now_ts: int | None = None,
        current_visual_payload: Any = None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        return reminder_engine.consume_due_reminders(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            now_ts=now_ts,
            current_visual_payload=current_visual_payload,
            limit=limit,
        )

    def _build_reminder_notification_payload(
        self,
        *,
        reminder: dict[str, Any],
        visual_payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return reminder_engine.build_reminder_notification_payload(
            self,
            reminder=reminder,
            visual_payload=visual_payload,
        )

    def _generate_reminder_notification_speech(
        self,
        *,
        reminder: dict[str, Any],
        visual_payload: dict[str, Any],
    ) -> str:
        return reminder_engine.generate_reminder_notification_speech(
            self,
            reminder=reminder,
            visual_payload=visual_payload,
        )

    def _persist_due_reminder_notification(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        notification: dict[str, Any],
        now_ts: int,
    ) -> None:
        reminder_engine.persist_due_reminder_notification(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            notification=notification,
            now_ts=now_ts,
        )

    def _format_reminder_notification(self, reminder: dict[str, Any]) -> str:
        return reminder_engine.format_reminder_notification(reminder)

    def _build_current_visual_context(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        current_visual_payload: Any,
        visual_payload: dict[str, Any] | None = None,
        runtime_projection: dict[str, Any] | None = None,
        character_only: bool = False,
        resource_manifest: ResourceManifest | None = None,
    ) -> str:
        return visual_context_engine.build_current_visual_context(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            current_visual_payload=current_visual_payload,
            visual_payload=visual_payload,
            runtime_projection=runtime_projection,
            character_only=character_only,
            resource_manifest=resource_manifest,
        )

    def _schedule_visual_observations_for_payload(
        self,
        *,
        payload: dict[str, Any] | None,
        profile_user_id: str,
        session_id: str,
    ) -> None:
        visual_context_engine.schedule_visual_observations_for_payload(
            self,
            payload=payload,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )

    def _handle_ready_visual_observation(
        self,
        target,
        observation: dict[str, Any],
    ) -> None:
        visual_context_engine.handle_ready_visual_observation(self, target, observation)

    def _get_user_runtime_projection(self, profile_user_id: str) -> dict[str, Any]:
        return visual_context_engine.get_user_runtime_projection(self, profile_user_id)

    def _get_user_bgm_tracks(self, profile_user_id: str) -> list[dict[str, Any]]:
        return visual_context_engine.get_user_bgm_tracks(self, profile_user_id)

    def _resolve_current_visual_payload(self, *, session_id: str, current_visual_payload: Any) -> dict[str, Any] | None:
        return visual_context_engine.resolve_current_visual_payload(
            self,
            session_id=session_id,
            current_visual_payload=current_visual_payload,
        )

    def _coerce_visual_payload(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return visual_context_engine.coerce_visual_payload(payload)

    @staticmethod
    def _build_extra_context_audit_sections(candidates: list[tuple[str, Any]]) -> list[dict[str, str]]:
        sections: list[dict[str, str]] = []
        for name, text in candidates:
            rendered_name = str(name or "").strip()
            rendered_text = str(text or "").strip()
            if rendered_name and rendered_text:
                sections.append({"name": rendered_name, "text": rendered_text})
        return sections

    @staticmethod
    def _split_history_records(
        *,
        recent_raw: list[dict[str, Any]],
        user_message: str,
        now_ts: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        records = list(recent_raw or [])
        current_record: dict[str, Any] = {
            "role": "user",
            "content": user_message,
            "timestamp": now_ts,
        }
        if not records:
            return [], current_record

        last_record = records[-1]
        last_role = str(last_record.get("role", "") or "").strip().lower()
        last_content = normalize_text(str(last_record.get("content", "") or ""))
        current_content = normalize_text(user_message)
        if last_role == "user" and last_content == current_content:
            return records[:-1], last_record
        return records, current_record

    @staticmethod
    def _build_history_turns(records: list[dict[str, Any]]) -> list[dict[str, str]]:
        turns: list[dict[str, str]] = []
        for rec in records:
            raw_role = str(rec.get("role", "") or "").strip()
            role = raw_role.lower()
            content = str(rec.get("content", "") or "").strip()
            if not content:
                continue
            rendered_content = render_chat_line(
                role=raw_role,
                content=content,
                timestamp=rec.get("timestamp"),
            )
            if role == "assistant":
                turns.append({"role": "assistant", "content": rendered_content})
            elif role.startswith("npc:"):
                # npc: downgrade to user to avoid unsupported role in API
                turns.append({"role": "user", "content": rendered_content})
            else:
                turns.append({"role": "user", "content": rendered_content})
        return turns

    def _render_current_message_line(
        self,
        *,
        current_user_record: dict[str, Any],
    ) -> str:
        return render_chat_line(
            role=str(current_user_record.get("role") or "user"),
            content=str(current_user_record.get("content") or ""),
            timestamp=current_user_record.get("timestamp"),
        )

    def _upsert_raw_record(self, record: dict[str, Any]) -> None:
        if not bool(record.get("index_in_vector", True)):
            return
        self.vector_store.upsert_entries([build_raw_vector_entry(record)])
