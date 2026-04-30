from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Generator

import config

from .artifact_system import ArtifactContainerService
from .attachment_inbox import AttachmentInboxService
from .attachment_ingest import AttachmentIngestService
from .background_tasks import BackgroundTaskRunner
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
from .client_protocol import ClientCapability, ClientMode, ClientProtocolContext
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
from .tool_runtime import ApplyStyleToExistingFileToolHandler, BaseToolHandler, CallNPCToolHandler, CancelReminderToolHandler, CheckInventoryToolHandler, CleanVoiceTrackToolHandler, ClearAttachmentFocusToolHandler, ComposeFileToolHandler, ConvertMediaFileToolHandler, FetchMediaFromUrlToolHandler, InspectAttachmentToolHandler, InspectGeneratedFileToolHandler, InspectMediaInfoToolHandler, ListRemindersToolHandler, ManageArtifactToolHandler, ManageGeneratedFileToolHandler, ManageGiftToolHandler, ManagePersonaToolHandler, ManageTaskWorkspaceToolHandler, PrepareVoiceDatasetToolHandler, ReadAttachmentSectionToolHandler, RetrieveMemoryToolHandler, ReviseGeneratedFileToolHandler, RetryAttachmentToolHandler, SendFileToolHandler, SendGeneratedFileToolHandler, SendStickerToolHandler, SeparateAudioStemsToolHandler, SetReminderToolHandler, SyncAttachmentWorkspaceToolHandler, ToolExecutionContext, ToolExecutionResult, TranscribeMediaToolHandler
from . import visual_context_engine
from .vision_service import VisionObservationService
from .store import MemoryStore
from .text_utils import (
    detect_time_of_day_from_text,
    extract_semantic_tags,
    infer_time_of_day,
    join_tags,
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

logger = logging.getLogger("akane.engine")


class AkaneMemoryEngine:
    def __init__(
        self,
        base_dir: Path,
        resource_manifest: ResourceManifest | None = None,
        desktop_pet_character_resources: Any = None,
    ):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
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
        self.attachment_inbox_service = AttachmentInboxService(
            store=self.store,
            base_dir=self.base_dir / "attachment_inbox_files",
        )
        self.vision_observation_router = VisionObservationRouter(
            store=self.store,
            gift_service=self.gift_service,
            attachment_service=self.attachment_inbox_service,
        )
        self.artifact_service = ArtifactContainerService(
            store=self.store,
            public_path_builder=self.gift_service._build_public_path,
        )
        self.persona_card_service = PersonaCardService(store=self.store)
        self.task_workspace_service = TaskWorkspaceService(store=self.store)
        self.generated_file_service = GeneratedFileService(
            base_dir=self.base_dir / "generated_files",
            store=self.store,
            attachment_service=self.attachment_inbox_service,
        )
        self.desktop_music_timeline_service = DesktopMusicTimelineService(
            store=self.store,
            generated_file_service=self.generated_file_service,
            background_tasks=self.background_tasks,
        )
        self.gift_assets = self.gift_service
        self.npc_runtime = GenericNPCRuntime(self.base_dir / "generic_npc_memory_v01", self.llm)
        self.resource_manifest = resource_manifest
        self.desktop_pet_character_resources = desktop_pet_character_resources
        sticker_assets_dir = (
            Path(getattr(resource_manifest, "assets_dir"))
            if resource_manifest is not None and getattr(resource_manifest, "assets_dir", None)
            else Path(__file__).resolve().parent.parent / "web" / "assets"
        )
        self.sticker_assets = StickerAssetService(assets_dir=sticker_assets_dir)
        self.vision_service = VisionObservationService(
            self.base_dir / "vision_cache",
            store=self.store,
            resource_manifest=self.resource_manifest,
            gift_assets_dir=self.base_dir / "user_assets",
            on_observation_ready=self.vision_observation_router.handle,
        )
        self.desktop_screen_vision = DesktopScreenVisionWorkspace(
            vision_service=self.vision_service,
            max_ready_per_session=int(getattr(config, "DESKTOP_SCREEN_VISION_MAX_CLIPS", 5) or 5),
            ttl_sec=int(getattr(config, "DESKTOP_SCREEN_VISION_TTL_SEC", 15 * 60) or (15 * 60)),
        )
        self.attachment_ingest_service = AttachmentIngestService(
            base_dir=self.base_dir / "attachment_inbox_files",
            store=self.store,
            attachment_service=self.attachment_inbox_service,
            vision_service=self.vision_service,
            background_tasks=self.background_tasks,
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
        self._maybe_start_embedding_reindex()

    def reset(self) -> None:
        self._get_compaction_service().reset()
        self.store.reset()
        self.vector_store.reset()
        self.gift_service.reset()
        self.vision_service.reset()
        self.desktop_screen_vision.reset()
        self.npc_runtime.reset()

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
                    model_name=str(getattr(config, "EMBEDDING_MODEL_NAME", "") or "BAAI/bge-small-zh-v1.5"),
                    device=str(getattr(config, "EMBEDDING_DEVICE", "") or "").strip() or None,
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
        mode = ClientMode.DESKTOP_PET if raw_mode == ClientMode.DESKTOP_PET.value else None
        if mode != ClientMode.DESKTOP_PET:
            return self.resource_manifest

        service = getattr(self, "desktop_pet_character_resources", None)
        if service is None:
            return self.resource_manifest
        manifest = service.get_manifest(character_pack_id) if character_pack_id else None
        return manifest or self.resource_manifest

    def _resolve_turn_resource_manifest(
        self,
        payload: dict[str, Any],
        client_context: ClientProtocolContext,
    ) -> ResourceManifest | None:
        if client_context.effective_mode != ClientMode.DESKTOP_PET:
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

    def _build_desktop_pet_character_pack_prompt_context(
        self,
        *,
        character_pack_id: str,
        resource_manifest: ResourceManifest | None = None,
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
        service = AttachmentInboxService(
            store=store,
            base_dir=self.base_dir / "attachment_inbox_files",
        )
        self.attachment_inbox_service = service
        return service

    def _get_attachment_ingest_service(self) -> AttachmentIngestService | None:
        service = getattr(self, "attachment_ingest_service", None)
        if service is not None:
            return service
        store = getattr(self, "store", None)
        vision_service = getattr(self, "vision_service", None)
        if store is None or vision_service is None:
            return None
        attachment_service = self._get_attachment_inbox_service()
        if attachment_service is None:
            return None
        service = AttachmentIngestService(
            base_dir=self.base_dir / "attachment_inbox_files",
            store=store,
            attachment_service=attachment_service,
            vision_service=vision_service,
            background_tasks=getattr(self, "background_tasks", None),
        )
        self.attachment_ingest_service = service
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
        service = GeneratedFileService(
            base_dir=self.base_dir / "generated_files",
            store=store,
            attachment_service=attachment_service,
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

    def _schedule_summary_cycle(self, *, profile_user_id: str, session_id: str) -> None:
        self._get_compaction_service().schedule_summary_cycle(
            profile_user_id=profile_user_id,
            session_id=session_id,
        )

    def _run_summary_cycle(self, *, profile_user_id: str, session_id: str) -> None:
        self._get_compaction_service().run_summary_cycle(
            profile_user_id=profile_user_id,
            session_id=session_id,
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
                role="user",
                content=user_message,
                timestamp=now_ts,
                date_label=date_label,
                time_of_day=time_of_day,
                semantic_tags=extract_semantic_tags(user_message),
            )
            self._schedule_summary_cycle(profile_user_id=profile_user_id, session_id=session_id)

        recent_raw = self.store.get_unsummarized_messages(session_id)
        if transient_user_turn:
            recent_raw = [*recent_raw, user_record]
        episodic_limit = max(1, int(getattr(config, "EPISODIC_VISIBLE_MAX", getattr(config, "RECENT_SUMMARY_LIMIT", 5))))
        semantic_limit = max(1, int(getattr(config, "SEMANTIC_VISIBLE_LIMIT", 3)))
        recent_episodic_summaries = self.store.get_visible_episodic_summaries(profile_user_id, limit=episodic_limit)
        recent_semantic_summaries = (
            self.store.get_recent_semantic_summaries(profile_user_id, limit=semantic_limit)
            if bool(getattr(config, "ENABLE_SEMANTIC_MEMORY", True))
            else []
        )
        verifier_debug_enabled = self._coerce_bool(payload.get("verifier_debug"))
        final_debug_enabled = self._coerce_bool(payload.get("final_debug"))
        retrieval_pipeline = self._run_pre_retrieval_pipeline(
            payload=payload,
            profile_user_id=profile_user_id,
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
        memory_exclude_source_ids = [
            str(hit.get("source_id") or "").strip()
            for hit in retrieval_result.get("fused_hits", [])
            if str(hit.get("source_id") or "").strip()
        ]
        for tool_round_index in range(max_tool_rounds):
            final_output = self._promote_narrated_tool_call(
                final_output,
                user_message=user_message,
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
            )
            tool_call = self._normalize_tool_call(
                final_output.get("tool_call"),
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
            )
            if not tool_call:
                break

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
                    extra_user_context=self._merge_extra_user_context(
                        turn_extra_user_context,
                        self._build_multi_tool_followup_context(tool_followups, allow_more=False),
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

            internal_memory_tool = str(tool_call.get("type") or "") == "retrieve_memory"
            preface_turn = None if internal_memory_tool else self._build_assistant_dialogue_turn(final_output.get("speech"))
            if preface_turn:
                preface_turns.append(preface_turn)
                preface_record = self.store.add_message(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    role="assistant",
                    content=preface_turn["speech"],
                    timestamp=now_ts,
                    date_label=date_label,
                    time_of_day=time_of_day,
                    semantic_tags=extract_semantic_tags(preface_turn["speech"]),
                )
                self._upsert_raw_record(preface_record)
                self._schedule_summary_cycle(profile_user_id=profile_user_id, session_id=session_id)
                recent_raw_for_turn.append(preface_record)

            tool_result = self._execute_tool_call(
                profile_user_id=profile_user_id,
                session_id=session_id,
                tool_call=tool_call,
                visual_payload=final_output,
                now_ts=now_ts,
                current_user_source_id=str(user_record.get("source_id") or ""),
                client_context=client_context,
                memory_exclude_source_ids=memory_exclude_source_ids,
            )
            if tool_result:
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
                if str(tool_result.followup_context or "").strip():
                    tool_followups.append(
                        f"第 {len(tool_results)} 次工具（{tool_result.tool_type}）结果：\n"
                        f"{str(tool_result.followup_context).strip()}"
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
                        role=f"npc:{speaker}",
                        content=speech,
                        timestamp=max(now_ts, int(time.time())),
                        semantic_tags=extract_semantic_tags(speech),
                    )
                    self._upsert_raw_record(tool_record)
                    self._schedule_summary_cycle(profile_user_id=profile_user_id, session_id=session_id)
                    recent_raw_for_turn.append(tool_record)

            allow_more_tools = tool_round_index < max_tool_rounds - 1
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
                extra_user_context=self._merge_extra_user_context(
                    turn_extra_user_context,
                    self._build_multi_tool_followup_context(tool_followups, allow_more=allow_more_tools),
                ),
                client_context=client_context,
                resource_manifest=turn_resource_manifest,
                character_pack_id=turn_character_pack_id,
                user_images=desktop_screen_images,
                allow_tool_call=allow_more_tools,
                final_debug_enabled=final_debug_enabled,
            )

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
        )
        memory_tags = self._normalize_memory_tags(final_output.get("memory_tags"))
        final_output["memory_tags"] = join_tags(memory_tags)
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
            role="assistant",
            content=final_output.get("speech", ""),
            timestamp=int(time.time()),
            semantic_tags=extract_semantic_tags(final_output.get("speech", "")),
        )
        self._upsert_raw_record(assistant_record)
        self._schedule_summary_cycle(profile_user_id=profile_user_id, session_id=session_id)

        self.store.append_eval_turn(
            trace_id=trace_id,
            session_id=session_id,
            profile_user_id=profile_user_id,
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
                role="user",
                content=user_message,
                timestamp=now_ts,
                date_label=date_label,
                time_of_day=time_of_day,
                semantic_tags=extract_semantic_tags(user_message),
            )
            self._schedule_summary_cycle(profile_user_id=profile_user_id, session_id=session_id)

        recent_raw = self.store.get_unsummarized_messages(session_id)
        if transient_user_turn:
            recent_raw = [*recent_raw, user_record]
        episodic_limit = max(1, int(getattr(config, "EPISODIC_VISIBLE_MAX", getattr(config, "RECENT_SUMMARY_LIMIT", 5))))
        semantic_limit = max(1, int(getattr(config, "SEMANTIC_VISIBLE_LIMIT", 3)))
        recent_episodic_summaries = self.store.get_visible_episodic_summaries(profile_user_id, limit=episodic_limit)
        recent_semantic_summaries = (
            self.store.get_recent_semantic_summaries(profile_user_id, limit=semantic_limit)
            if bool(getattr(config, "ENABLE_SEMANTIC_MEMORY", True))
            else []
        )
        verifier_debug_enabled = self._coerce_bool(payload.get("verifier_debug"))
        final_debug_enabled = self._coerce_bool(payload.get("final_debug"))
        retrieval_pipeline = self._run_pre_retrieval_pipeline(
            payload=payload,
            profile_user_id=profile_user_id,
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
        memory_exclude_source_ids = [
            str(hit.get("source_id") or "").strip()
            for hit in retrieval_result.get("fused_hits", [])
            if str(hit.get("source_id") or "").strip()
        ]
        for tool_round_index in range(max_tool_rounds):
            final_output = self._promote_narrated_tool_call(
                final_output,
                user_message=user_message,
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
            )
            tool_call = self._normalize_tool_call(
                final_output.get("tool_call"),
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
            )
            if not tool_call:
                break

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
                    extra_user_context=self._merge_extra_user_context(
                        turn_extra_user_context,
                        self._build_multi_tool_followup_context(tool_followups, allow_more=False),
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

            internal_memory_tool = str(tool_call.get("type") or "") == "retrieve_memory"
            preface_turn = None if internal_memory_tool else self._build_assistant_dialogue_turn(final_output.get("speech"))
            if preface_turn:
                preface_turns.append(preface_turn)
                preface_record = self.store.add_message(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    role="assistant",
                    content=preface_turn["speech"],
                    timestamp=now_ts,
                    date_label=date_label,
                    time_of_day=time_of_day,
                    semantic_tags=extract_semantic_tags(preface_turn["speech"]),
                )
                self._upsert_raw_record(preface_record)
                self._schedule_summary_cycle(profile_user_id=profile_user_id, session_id=session_id)
                recent_raw_for_turn.append(preface_record)

            tool_result = self._execute_tool_call(
                profile_user_id=profile_user_id,
                session_id=session_id,
                tool_call=tool_call,
                visual_payload=final_output,
                now_ts=now_ts,
                current_user_source_id=str(user_record.get("source_id") or ""),
                client_context=client_context,
                memory_exclude_source_ids=memory_exclude_source_ids,
            )
            if tool_result:
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
                for stream_event in current_events:
                    yield stream_event
                if str(tool_result.followup_context or "").strip():
                    tool_followups.append(
                        f"第 {len(tool_results)} 次工具（{tool_result.tool_type}）结果：\n"
                        f"{str(tool_result.followup_context).strip()}"
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
                        role=f"npc:{speaker}",
                        content=speech,
                        timestamp=max(now_ts, int(time.time())),
                        semantic_tags=extract_semantic_tags(speech),
                    )
                    self._upsert_raw_record(tool_record)
                    self._schedule_summary_cycle(profile_user_id=profile_user_id, session_id=session_id)
                    recent_raw_for_turn.append(tool_record)

            allow_more_tools = tool_round_index < max_tool_rounds - 1
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
                extra_user_context=self._merge_extra_user_context(
                    turn_extra_user_context,
                    self._build_multi_tool_followup_context(tool_followups, allow_more=allow_more_tools),
                ),
                client_context=client_context,
                resource_manifest=turn_resource_manifest,
                character_pack_id=turn_character_pack_id,
                user_images=desktop_screen_images,
                allow_tool_call=allow_more_tools,
                final_debug_enabled=final_debug_enabled,
            )

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
        )
        memory_tags = self._normalize_memory_tags(final_output.get("memory_tags"))
        final_output["memory_tags"] = join_tags(memory_tags)
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
            role="assistant",
            content=final_output.get("speech", ""),
            timestamp=int(time.time()),
            semantic_tags=extract_semantic_tags(final_output.get("speech", "")),
        )
        self._upsert_raw_record(assistant_record)
        self._schedule_summary_cycle(profile_user_id=profile_user_id, session_id=session_id)

        self.store.append_eval_turn(
            trace_id=trace_id,
            session_id=session_id,
            profile_user_id=profile_user_id,
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
        final_output["_debug"] = debug_payload
        yield {"type": "final", "payload": final_output}

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
        )
        result = self.llm.call_chat_json(
            system_prompt=str(generation_context["system_prompt"]),
            user_prompt=str(generation_context["user_prompt"]),
            fallback=dict(generation_context["fallback"]),
            temperature=0.7,
            prompt_cache_key="chat:final",
            user_images=user_images,
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
        )
        yield {
            "type": "turn_start",
            "speaker": PERSONA.assistant_name,
        }
        stream_result = yield from self.llm.stream_chat_json(
            system_prompt=str(generation_context["system_prompt"]),
            user_prompt=str(generation_context["user_prompt"]),
            fallback=dict(generation_context["fallback"]),
            temperature=0.7,
            prompt_cache_key="chat:final",
            user_images=user_images,
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
        resource_manifest = resource_manifest or self.resource_manifest
        manifest = resource_manifest.refresh() if resource_manifest else None
        runtime_projection = self._get_user_runtime_projection(profile_user_id)
        user_bgm_tracks = list(runtime_projection.get("extra_bgm_tracks") or [])
        user_scene_groups = list(runtime_projection.get("extra_scene_groups") or [])
        user_character_outfits = list(runtime_projection.get("extra_character_outfits") or [])
        desktop_pet_character_only = client_context.effective_mode == ClientMode.DESKTOP_PET
        raw_text = render_chat_timeline(recent_raw)
        current_message_text = self._render_current_message_line(
            current_user_record=recent_raw[-1] if recent_raw else {
                "role": "user",
                "content": user_message,
                "timestamp": now_ts,
            },
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
            if prompt_profile.includes(PromptModule.SCENE_OBSERVATION) and not desktop_pet_character_only
            else ""
        )
        outfit_observation_context = (
            self.vision_service.build_outfit_prompt_context(
                visual_payload=current_visual_context_payload,
                extra_bgm_tracks=user_bgm_tracks,
                extra_scene_groups=user_scene_groups,
                extra_character_outfits=user_character_outfits,
            )
            if prompt_profile.includes(PromptModule.OUTFIT_OBSERVATION) and not desktop_pet_character_only
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
            if focused_gift is not None
            else ""
        )
        persona_service = self._get_persona_card_service()
        persona_context = (
            persona_service.build_prompt_context(
                profile_user_id=profile_user_id,
                session_id=session_id,
                visible_limit=5,
            )
            if persona_service is not None and prompt_profile.includes(PromptModule.PERSONA)
            else {"system_context": "", "reference_context": "", "active_id": ""}
        )
        character_pack_persona_context = (
            self._build_desktop_pet_character_pack_prompt_context(
                character_pack_id=character_pack_id,
                resource_manifest=resource_manifest,
            )
            if desktop_pet_character_only and prompt_profile.includes(PromptModule.PERSONA)
            else {"system_context": "", "reference_context": "", "active_id": ""}
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
        extra_context_sections = [
            text
            for text in [
                self._build_client_mode_prompt_context(client_context)
                if prompt_profile.includes(PromptModule.CLIENT_MODE)
                else "",
                extra_context if prompt_profile.includes(PromptModule.EXTRA_CONTEXT) else "",
                task_workspace_context,
                attachment_focus_context,
                generated_file_context,
                pending_gift_context,
                gift_observation_context,
            ]
            if text
        ]
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
        resource_context = (
            (
                resource_manifest.build_character_prompt_context(
                    extra_character_outfits=user_character_outfits,
                )
                if desktop_pet_character_only
                else resource_manifest.build_prompt_context(
                    extra_bgm_tracks=user_bgm_tracks,
                    extra_scene_groups=user_scene_groups,
                    extra_character_outfits=user_character_outfits,
                )
            )
            if resource_manifest and prompt_profile.includes(PromptModule.RESOURCE_MANIFEST)
            else "当前没有额外的视觉资源。"
        )
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
            ),
            debug_enabled=debug_enabled,
            system_prompt_override=prompt_profile.system_prompt_override,
            mode_prompt_override=prompt_profile.mode_prompt_override(debug_enabled=debug_enabled),
        )
        if desktop_pet_character_only and client_context.has_capability(ClientCapability.AUDIO_PLAYBACK):
            fallback_payload = generation_context.get("fallback")
            if isinstance(fallback_payload, dict):
                fallback_payload["activity"] = None
        generation_context["allow_tool_call"] = effective_allow_tool_call
        generation_context["prompt_profile"] = prompt_profile.to_public_dict()
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

    def _build_assistant_dialogue_turn(self, speech: Any) -> dict[str, str] | None:
        return final_output_engine.build_assistant_dialogue_turn(speech)

    def _build_dialogue_turns(
        self,
        *,
        preface_turn: dict[str, str] | list[dict[str, str]] | None,
        npc_turns: list[dict[str, Any]],
        final_speech: Any,
        final_speech_segments: Any = None,
    ) -> list[dict[str, str]]:
        return final_output_engine.build_dialogue_turns(
            preface_turn=preface_turn,
            npc_turns=npc_turns,
            final_speech=final_speech,
            final_speech_segments=final_speech_segments,
        )

    def _max_tool_rounds(self) -> int:
        return tool_orchestration_engine.max_tool_rounds()

    def _tool_call_signature(self, tool_call: dict[str, Any]) -> str:
        return tool_orchestration_engine.tool_call_signature(tool_call)

    def _describe_tool_call_for_prompt(self, tool_call: dict[str, Any]) -> str:
        return tool_orchestration_engine.describe_tool_call_for_prompt(tool_call)

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

    def _build_multi_tool_followup_context(self, tool_followups: list[str], *, allow_more: bool) -> str:
        return tool_orchestration_engine.build_multi_tool_followup_context(
            tool_followups,
            allow_more=allow_more,
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
        }

    def _resolve_tool_handlers(
        self,
        *,
        client_context: ClientProtocolContext | None = None,
        profile_user_id: str = "",
        session_id: str = "",
    ) -> dict[str, BaseToolHandler]:
        handlers = getattr(self, "tool_handlers", {}) or {}
        if client_context is None:
            return dict(handlers)

        selected_names = list(
            self._resolve_capability_selection(
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
            ).tool_names
        )
        return {
            tool_name: handlers[tool_name]
            for tool_name in selected_names
            if tool_name in handlers
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
        return registry.select(snapshot)

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
        if not handlers:
            hints = [hint for hint in selection.light_hints if hint]
            if not hints:
                return "当前没有可用工具，tool_call 固定为 null。"
            return "\n".join(
                [
                    "【可用能力概览】",
                    *[f"- {hint}" for hint in hints],
                    "当前没有需要展开的具体工具，tool_call 固定为 null。",
                ]
            )

        lines = []
        if selection.light_hints:
            lines.append("【可用能力概览】")
            for hint in selection.light_hints:
                lines.append(f"- {hint}")
            lines.append("")
        lines.append("【当前可调用工具】")
        for handler in handlers.values():
            lines.append(handler.build_prompt_instruction())
        lines.append(
            "重要：真正调用工具只能写在 tool_call 字段；不要在 speech 里写“工具调用：...”或“我调用工具了”来代替。"
            "如果 tool_call 为 null，系统不会执行任何工具，也不要声称工具已经调用或失败。"
        )
        lines.append("如果不需要工具，tool_call 输出 null。一次只调用一个工具。")
        return "\n".join(lines)

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
        tool_call: dict[str, Any],
        visual_payload: dict[str, Any],
        now_ts: int,
        current_user_source_id: str = "",
        client_context: ClientProtocolContext | None = None,
        memory_exclude_source_ids: list[str] | None = None,
    ) -> ToolExecutionResult | None:
        return tool_orchestration_engine.execute_tool_call(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            tool_call=tool_call,
            visual_payload=visual_payload,
            now_ts=now_ts,
            current_user_source_id=current_user_source_id,
            client_context=client_context,
            memory_exclude_source_ids=memory_exclude_source_ids,
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
