from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Generator
from urllib.parse import urlparse

import config

from .artifact_system import ArtifactContainerService
from .attachment_inbox import AttachmentInboxService
from .attachment_ingest import AttachmentIngestService
from .background_tasks import BackgroundTaskRunner
from .capability_registry import CapabilityRegistry, CapabilitySelection, CapabilitySnapshot, is_document_attachment, is_document_generated_file, is_media_attachment, is_media_generated_file
from .embedding_provider import BaseEmbeddingProvider, CachedEmbeddingProvider, HashedEmbeddingProvider
from .generated_files import GeneratedFileService
from .gift_system import GiftSystemService
from .huggingface_provider import HuggingFaceEmbeddingProvider
from .llm_runtime import LLMRuntime
from .memory_compaction_service import MemoryCompactionService
from .memory_rendering import render_semantic_summary_timeline, render_summary_timeline
from .client_protocol import ClientCapability, ClientMode, ClientProtocolContext
from .mode_profiles import ModeProfileRegistry
from .npc_runtime import GenericNPCRuntime
from .output_adapters import OutputAdapterRegistry
from .persona_config import PERSONA
from .persona_system import PersonaCardService
from .prompt_builder import PromptBuilder
from .prompt_profiles import PromptModule, PromptProfileRegistry
from .retrieval_service import RetrievalService
from .resource_manifest import ResourceManifest
from .task_workspace import TaskWorkspaceService
from .tool_runtime import ApplyStyleToExistingFileToolHandler, BaseToolHandler, CallNPCToolHandler, CancelReminderToolHandler, CheckInventoryToolHandler, CleanVoiceTrackToolHandler, ClearAttachmentFocusToolHandler, ComposeFileToolHandler, ConvertMediaFileToolHandler, FetchMediaFromUrlToolHandler, InspectAttachmentToolHandler, InspectGeneratedFileToolHandler, InspectMediaInfoToolHandler, ListRemindersToolHandler, ManageArtifactToolHandler, ManageGeneratedFileToolHandler, ManageGiftToolHandler, ManagePersonaToolHandler, ManageTaskWorkspaceToolHandler, PrepareVoiceDatasetToolHandler, ReadAttachmentSectionToolHandler, RetrieveMemoryToolHandler, ReviseGeneratedFileToolHandler, RetryAttachmentToolHandler, SendFileToolHandler, SendGeneratedFileToolHandler, SeparateAudioStemsToolHandler, SetReminderToolHandler, SyncAttachmentWorkspaceToolHandler, ToolExecutionContext, ToolExecutionResult, TranscribeMediaToolHandler
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


TOOL_PACKS: dict[str, tuple[str, ...]] = {
    "base": (
        "retrieve_memory",
        "set_reminder",
        "list_reminders",
        "cancel_reminder",
        "manage_persona",
        "manage_task_workspace",
    ),
    "web_scene": (
        "call_npc",
        "check_inventory",
        "manage_gift",
        "manage_artifact",
    ),
    "qq": (
        "fetch_media_from_url",
        "sync_attachment_workspace",
        "inspect_attachment",
        "read_attachment_section",
        "retry_attachment",
        "clear_attachment_focus",
        "compose_file",
        "revise_generated_file",
        "apply_style_to_existing_file",
        "inspect_media_info",
        "convert_media_file",
        "clean_voice_track",
        "transcribe_media",
        "prepare_voice_dataset",
        "inspect_generated_file",
        "send_file",
        "send_generated_file",
        "manage_generated_file",
    ),
    "desktop": (
        "fetch_media_from_url",
        "sync_attachment_workspace",
        "inspect_attachment",
        "read_attachment_section",
        "retry_attachment",
        "clear_attachment_focus",
        "compose_file",
        "revise_generated_file",
        "apply_style_to_existing_file",
        "inspect_media_info",
        "convert_media_file",
        "clean_voice_track",
        "transcribe_media",
        "prepare_voice_dataset",
        "inspect_generated_file",
        "send_file",
        "send_generated_file",
        "manage_generated_file",
    ),
}

MODE_TOOL_PACKS: dict[ClientMode, tuple[str, ...]] = {
    ClientMode.SCENE_STATIC: ("base", "web_scene"),
    ClientMode.SCENE_LIVE2D: ("base", "web_scene"),
    ClientMode.QQ_TEXT: ("base", "qq"),
    ClientMode.DESKTOP_PET: ("base", "desktop"),
}


class AkaneMemoryEngine:
    def __init__(self, base_dir: Path, resource_manifest: ResourceManifest | None = None):
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
        self.gift_assets = self.gift_service
        self.npc_runtime = GenericNPCRuntime(self.base_dir / "generic_npc_memory_v01", self.llm)
        self.resource_manifest = resource_manifest
        self.vision_service = VisionObservationService(
            self.base_dir / "vision_cache",
            store=self.store,
            resource_manifest=self.resource_manifest,
            gift_assets_dir=self.base_dir / "user_assets",
            on_observation_ready=self.vision_observation_router.handle,
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
        self.npc_runtime.reset()

    def build_resource_manifest(self, *, profile_user_id: str = "") -> dict[str, Any]:
        if not self.resource_manifest:
            return {
                "schema_version": 2,
                "scenes": {"majors": []},
                "characters": {"outfits": []},
                "defaults": {},
            }
        self.resource_manifest.refresh()
        runtime_projection = self._get_user_runtime_projection(profile_user_id)
        return self.resource_manifest.build_runtime_manifest(
            extra_bgm_tracks=list(runtime_projection.get("extra_bgm_tracks") or []),
            extra_scene_groups=list(runtime_projection.get("extra_scene_groups") or []),
            extra_character_outfits=list(runtime_projection.get("extra_character_outfits") or []),
        )

    def list_gift_assets(self, *, profile_user_id: str, media_kind: str = "all", limit: int = 50) -> list[dict[str, Any]]:
        normalized_media_kind = str(media_kind or "").strip().lower() or "all"
        asset_type = {
            "bgm": "audio",
            "audio": "audio",
            "image": "image",
            "photo": "image",
            "all": None,
        }.get(normalized_media_kind, None)
        return self.gift_service.list_assets(
            profile_user_id=profile_user_id,
            asset_type=asset_type,
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
        asset = self.gift_service.ingest_upload(
            profile_user_id=profile_user_id,
            session_id=session_id,
            filename=filename,
            content_type=content_type,
            content=content,
            now_ts=now_ts,
        )
        if str(asset.get("asset_type") or "").strip().lower() == "image":
            try:
                self.vision_service.schedule_gift_observation(asset=asset)
            except Exception as exc:
                logger.warning("schedule gift observation after upload failed: %s", exc)
        return asset

    def apply_gift_action(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
        asset_id: str,
        action: str,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        return self.gift_service.apply_action(
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
        asset = self.gift_service.resolve_focus_asset(
            profile_user_id=profile_user_id,
            session_id=session_id,
            asset_id=asset_id,
        )
        if asset is None:
            return None
        if str(asset.get("asset_type") or "").strip().lower() != "image":
            raise ValueError("only image gifts can be observed without saving")

        observation = self.vision_service.analyze_gift_once(asset=asset)
        assistant_line = self.gift_service.build_transient_image_reply(
            asset=asset,
            observation=observation,
        )
        discarded = self.gift_service.discard_asset(
            profile_user_id=profile_user_id,
            session_id=session_id,
            asset_id=str(asset.get("asset_id") or asset_id),
            timestamp=timestamp,
        )
        return {
            "assistant_line": assistant_line,
            "asset": discarded or asset,
            "observation": dict((observation or {}).get("observation") or {}),
        }

    def list_gift_inventory(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        scope: str = "pending_recent",
        limit: int = 5,
    ) -> dict[str, Any]:
        return self.gift_service.list_inventory(
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
        return self.artifact_service.list_containers(
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
        return self.artifact_service.list_container_items(
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
        visible_ids: list[str] = []
        seen: set[str] = set()

        def add_source_id(value: Any) -> None:
            source_id = str(value or "").strip()
            if not source_id or source_id in seen:
                return
            seen.add(source_id)
            visible_ids.append(source_id)

        for source_id in extra_source_ids or []:
            add_source_id(source_id)
        for row in recent_raw or []:
            add_source_id(row.get("source_id"))
        for summary in recent_episodic_summaries or []:
            add_source_id(summary.get("source_id") or summary.get("summary_id"))
        for semantic_summary in recent_semantic_summaries or []:
            add_source_id(semantic_summary.get("source_id") or semantic_summary.get("semantic_id"))
        return visible_ids

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

    def _should_index_user_record_in_vector(self, *, router_output: dict[str, Any]) -> bool:
        if not bool(router_output.get("need_retrieval")):
            return True
        normalized = self._coerce_bool(router_output.get("index_current_message"))
        return True if normalized is None else bool(normalized)

    def _apply_user_vector_index_policy(
        self,
        *,
        user_record: dict[str, Any],
        router_output: dict[str, Any],
    ) -> dict[str, Any]:
        should_index = self._should_index_user_record_in_vector(router_output=router_output)
        if bool(user_record.get("index_in_vector", True)) != should_index:
            user_record["index_in_vector"] = should_index
            self.store.update_message_index_in_vector(user_record["source_id"], should_index)
        else:
            user_record["index_in_vector"] = should_index
        return user_record

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

    def prefetch_remote_media_links_for_message(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        message: str,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Deterministically fetch explicit media links before the final reply.

        This prevents recent failed attempts in chat history from making the
        model answer "it failed again" without actually trying the current URL.
        """
        urls = self._extract_prefetchable_remote_media_urls(message)
        if not urls and self._message_requests_remote_media_retry(message):
            urls = self._recent_prefetchable_remote_media_urls(
                profile_user_id=profile_user_id,
                session_id=session_id,
            )
        if not urls:
            return {}
        if not self._message_requests_remote_media_fetch(message, urls=urls):
            return {}
        service = self._get_attachment_ingest_service()
        if service is None:
            return {}
        return service.fetch_media_from_urls(
            profile_user_id=profile_user_id,
            session_id=session_id,
            urls=urls,
            timestamp=timestamp,
        )

    def _recent_prefetchable_remote_media_urls(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        limit: int = 24,
    ) -> list[str]:
        messages = self.store.get_session_messages(
            profile_user_id=profile_user_id,
            session_id=session_id,
            limit=limit,
        )
        for item in reversed(messages):
            urls = self._extract_prefetchable_remote_media_urls(str(item.get("content") or ""))
            if urls:
                return urls[:6]
        return []

    def _message_requests_remote_media_retry(self, message: str) -> bool:
        raw_text = str(message or "")
        text = normalize_text(raw_text).lower()
        if not text:
            return False
        retry_markers = (
            "再试",
            "重试",
            "重新试",
            "重新下载",
            "再下载",
            "再来一次",
            "试一次",
            "继续试",
            "完整报错",
            "报错",
            "一字不落",
        )
        media_markers = (
            "下载",
            "链接",
            "视频",
            "音频",
            "素材",
            "工具",
            "报错",
            "工作台",
        )
        haystacks = (raw_text, text)
        has_retry = any(marker in haystack for haystack in haystacks for marker in retry_markers)
        has_media = any(marker in haystack for haystack in haystacks for marker in media_markers)
        return has_retry and has_media

    def _extract_prefetchable_remote_media_urls(self, message: str) -> list[str]:
        text = str(message or "")
        if not text:
            return []
        candidates = re.findall(r"https?://[^\s<>\]）)\"'，。；、]+", text)
        normalized: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            url = candidate.rstrip(".,!?;:，。！？；：")
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            if url in seen:
                continue
            seen.add(url)
            normalized.append(url)
            if len(normalized) >= 6:
                break
        return normalized

    def _message_requests_remote_media_fetch(self, message: str, *, urls: list[str]) -> bool:
        text = normalize_text(message).lower()
        if not urls:
            return False
        intent_keywords = (
            "下载",
            "拉进",
            "拉到",
            "获取",
            "转写",
            "转录",
            "字幕",
            "总结",
            "处理",
            "视频",
            "音频",
            "媒体",
            "这个链接",
            "链接",
        )
        if any(keyword in text for keyword in intent_keywords):
            return True
        known_media_hosts = (
            "b23.tv",
            "bilibili.com",
            "youtube.com",
            "youtu.be",
            "douyin.com",
            "iesdouyin.com",
            "ixigua.com",
            "kuaishou.com",
        )
        for url in urls:
            host = urlparse(url).netloc.lower()
            if any(host == known or host.endswith("." + known) for known in known_media_hosts):
                return True
        return False

    def wait_for_qq_attachments_settled(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachment_ids: list[str],
        timeout_seconds: float = 8.0,
    ) -> dict[str, Any]:
        service = self._get_attachment_inbox_service()
        if service is None:
            return {"ok": True, "ready": [], "failed": [], "pending": [], "missing": []}
        return service.wait_for_attachments_settled(
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
        service = self._get_generated_file_service()
        if service is None:
            return None
        return service.mark_delivery_status(
            profile_user_id=profile_user_id,
            session_id=session_id,
            generated_id=generated_id,
            delivery_status=delivery_status,
            timestamp=timestamp,
        )

    def process_turn(self, payload: dict[str, Any]) -> dict[str, Any]:
        client_context = self._resolve_client_protocol_context(payload)
        trace_id = str(payload.get("trace_id") or f"{PERSONA.trace_prefix}_{uuid.uuid4().hex[:12]}")
        session_id = str(payload.get("user_id") or payload.get("session_id") or "default_session")
        profile_user_id = str(payload.get("real_user_id") or session_id)
        user_message = str(payload.get("message") or "").strip()
        now_ts = int(payload.get("timestamp") or time.time())
        date_label = timestamp_to_date_label(now_ts)
        time_of_day = detect_time_of_day_from_text(user_message) or infer_time_of_day(now_ts)

        self.consume_due_reminders(
            profile_user_id=profile_user_id,
            session_id=session_id,
            now_ts=now_ts,
            current_visual_payload=payload.get("current_visual"),
        )

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
        retrieval_pipeline = self._get_retrieval_service().run_explicit(
            profile_user_id=profile_user_id,
            original_query=user_message,
            now_ts=now_ts,
            exclude_source_ids=self._collect_visible_context_source_ids(
                recent_raw=recent_raw,
                recent_episodic_summaries=recent_episodic_summaries,
                recent_semantic_summaries=recent_semantic_summaries,
                extra_source_ids=[user_record["source_id"]],
            ),
            verifier_debug_enabled=verifier_debug_enabled,
            route="pre_retrieval",
        )
        router_output = retrieval_pipeline.router_output
        router_timing = retrieval_pipeline.router_timing
        retrieval_result = retrieval_pipeline.retrieval_result
        verifier_output = retrieval_pipeline.verifier_output
        confirmed_snippets = retrieval_pipeline.confirmed_snippets
        verifier_timing = retrieval_pipeline.verifier_timing
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
            extra_user_context=str(payload.get("extra_context") or ""),
            client_context=client_context,
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
                    extra_user_context=self._build_multi_tool_followup_context(tool_followups, allow_more=False),
                    client_context=client_context,
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
                tool_events.extend(list(tool_result.stream_events))
                if str(tool_result.followup_context or "").strip():
                    tool_followups.append(
                        f"第 {len(tool_results)} 次工具（{tool_result.tool_type}）结果：\n"
                        f"{str(tool_result.followup_context).strip()}"
                    )
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
                extra_user_context=self._build_multi_tool_followup_context(tool_followups, allow_more=allow_more_tools),
                client_context=client_context,
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
        if memory_tags:
            user_record = self._apply_memory_tags_to_user_record(
                user_record=user_record,
                memory_tags=memory_tags,
            )
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
        trace_id = str(payload.get("trace_id") or f"{PERSONA.trace_prefix}_{uuid.uuid4().hex[:12]}")
        session_id = str(payload.get("user_id") or payload.get("session_id") or "default_session")
        profile_user_id = str(payload.get("real_user_id") or session_id)
        user_message = str(payload.get("message") or "").strip()
        now_ts = int(payload.get("timestamp") or time.time())
        date_label = timestamp_to_date_label(now_ts)
        time_of_day = detect_time_of_day_from_text(user_message) or infer_time_of_day(now_ts)

        self.consume_due_reminders(
            profile_user_id=profile_user_id,
            session_id=session_id,
            now_ts=now_ts,
            current_visual_payload=payload.get("current_visual"),
        )

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
        retrieval_pipeline = self._get_retrieval_service().run_explicit(
            profile_user_id=profile_user_id,
            original_query=user_message,
            now_ts=now_ts,
            exclude_source_ids=self._collect_visible_context_source_ids(
                recent_raw=recent_raw,
                recent_episodic_summaries=recent_episodic_summaries,
                recent_semantic_summaries=recent_semantic_summaries,
                extra_source_ids=[user_record["source_id"]],
            ),
            verifier_debug_enabled=verifier_debug_enabled,
            route="pre_retrieval",
        )
        router_output = retrieval_pipeline.router_output
        router_timing = retrieval_pipeline.router_timing
        retrieval_result = retrieval_pipeline.retrieval_result
        verifier_output = retrieval_pipeline.verifier_output
        confirmed_snippets = retrieval_pipeline.confirmed_snippets
        verifier_timing = retrieval_pipeline.verifier_timing
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
            extra_user_context=str(payload.get("extra_context") or ""),
            client_context=client_context,
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
                    extra_user_context=self._build_multi_tool_followup_context(tool_followups, allow_more=False),
                    client_context=client_context,
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
                tool_events.extend(current_events)
                for stream_event in current_events:
                    yield stream_event
                if str(tool_result.followup_context or "").strip():
                    tool_followups.append(
                        f"第 {len(tool_results)} 次工具（{tool_result.tool_type}）结果：\n"
                        f"{str(tool_result.followup_context).strip()}"
                    )
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
                extra_user_context=self._build_multi_tool_followup_context(tool_followups, allow_more=allow_more_tools),
                client_context=client_context,
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
        if memory_tags:
            user_record = self._apply_memory_tags_to_user_record(
                user_record=user_record,
                memory_tags=memory_tags,
            )
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
            allow_tool_call=allow_tool_call,
            final_debug_enabled=final_debug_enabled,
        )
        result = self.llm.call_chat_json(
            system_prompt=str(generation_context["system_prompt"]),
            user_prompt=str(generation_context["user_prompt"]),
            fallback=dict(generation_context["fallback"]),
            temperature=0.7,
        )
        return self._normalize_final_output(
            result=result,
            visual_defaults=dict(generation_context["visual_defaults"]),
            profile_user_id=profile_user_id,
            session_id=session_id,
            client_context=client_context,
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
        manifest = self.resource_manifest.refresh() if self.resource_manifest else None
        runtime_projection = self._get_user_runtime_projection(profile_user_id)
        user_bgm_tracks = list(runtime_projection.get("extra_bgm_tracks") or [])
        user_scene_groups = list(runtime_projection.get("extra_scene_groups") or [])
        user_character_outfits = list(runtime_projection.get("extra_character_outfits") or [])
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
            )
            if (
                generated_file_service is not None
                and prompt_profile.includes(PromptModule.EXTRA_CONTEXT)
                and client_context.effective_mode in {ClientMode.QQ_TEXT, ClientMode.DESKTOP_PET}
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
            if prompt_profile.includes(PromptModule.SCENE_OBSERVATION)
            else ""
        )
        outfit_observation_context = (
            self.vision_service.build_outfit_prompt_context(
                visual_payload=current_visual_context_payload,
                extra_bgm_tracks=user_bgm_tracks,
                extra_scene_groups=user_scene_groups,
                extra_character_outfits=user_character_outfits,
            )
            if prompt_profile.includes(PromptModule.OUTFIT_OBSERVATION)
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
                attachment_focus_context,
                generated_file_context,
                pending_gift_context,
                gift_observation_context,
            ]
            if text
        ]
        merged_extra_context = "\n\n".join(extra_context_sections) if extra_context_sections else "(无额外上下文)"

        visual_defaults = (
            self.resource_manifest.build_runtime_manifest(
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
        resource_context = (
            self.resource_manifest.build_prompt_context(
                extra_bgm_tracks=user_bgm_tracks,
                extra_scene_groups=user_scene_groups,
                extra_character_outfits=user_character_outfits,
            )
            if self.resource_manifest and prompt_profile.includes(PromptModule.RESOURCE_MANIFEST)
            else "当前没有额外的视觉资源。"
        )
        current_visual_context = (
            self._build_current_visual_context(
                profile_user_id=profile_user_id,
                session_id=session_id,
                current_visual_payload=current_visual_payload,
                visual_payload=current_visual_context_payload,
                runtime_projection=runtime_projection,
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
    ) -> dict[str, Any]:
        client_context = client_context or self._resolve_client_protocol_context({})
        raw_result = result if isinstance(result, dict) else {}
        normalized = dict(raw_result or {})
        persona_request_present = "persona" in raw_result
        persona_request_active = ""
        raw_persona = raw_result.get("persona")
        if isinstance(raw_persona, dict):
            persona_request_active = str(raw_persona.get("active") or "").strip()
        elif persona_request_present:
            persona_request_active = str(raw_persona or "").strip()
        if debug_enabled:
            thought = str(normalized.get("thought") or "").strip()
            normalized["thought"] = thought or PERSONA.final_fallback_thought
        else:
            normalized.pop("thought", None)
        normalized.setdefault("status", "final")
        normalized.setdefault("emotion", visual_defaults["emotion"])
        normalized.setdefault("score", 0.0)
        normalized["tool_call"] = (
            self._normalize_tool_call(
                normalized.get("tool_call"),
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
            )
            if allow_tool_call
            else None
        )
        speech, speech_segments = self._normalize_speech_payload(
            speech=normalized.get("speech"),
            speech_segments=normalized.get("speech_segments"),
            fallback_to_default=not bool(normalized.get("tool_call")),
        )
        normalized["speech"] = speech
        normalized["speech_segments"] = speech_segments
        normalized["code_snippet"] = self._normalize_code_snippet(normalized.get("code_snippet"))
        normalized["memory_tags"] = join_tags(self._normalize_memory_tags(normalized.get("memory_tags")))
        normalized["choices"] = self._normalize_choices(normalized.get("choices"))
        persona_service = self._get_persona_card_service()
        current_persona_id = (
            persona_service.get_active_id(profile_user_id=profile_user_id, session_id=session_id)
            if persona_service is not None and profile_user_id and session_id
            else ""
        )
        normalized["persona"] = {
            "active": persona_request_active if persona_request_present else current_persona_id,
        }
        normalized["_persona_request"] = {
            "present": bool(persona_request_present),
            "active": persona_request_active,
        }
        if not isinstance(normalized.get("character"), dict):
            normalized["character"] = {"outfit": visual_defaults["outfit"]}
        normalized["character"].setdefault("outfit", visual_defaults["outfit"])
        if not isinstance(normalized.get("scene"), dict):
            normalized["scene"] = {
                "major": visual_defaults["major"],
                "minor": visual_defaults["minor"],
                "background": visual_defaults["background"],
                "bgm": visual_defaults["bgm"],
            }
        normalized["scene"].setdefault("major", visual_defaults["major"])
        normalized["scene"].setdefault("minor", visual_defaults["minor"])
        normalized["scene"].setdefault("background", visual_defaults["background"])
        normalized["scene"].setdefault("bgm", visual_defaults["bgm"])
        if self.resource_manifest:
            runtime_projection = self._get_user_runtime_projection(profile_user_id)
            normalized = self.resource_manifest.normalize_visual_output(
                normalized,
                extra_bgm_tracks=list(runtime_projection.get("extra_bgm_tracks") or []),
                extra_scene_groups=list(runtime_projection.get("extra_scene_groups") or []),
                extra_character_outfits=list(runtime_projection.get("extra_character_outfits") or []),
            )
        normalized = self._get_output_adapter_registry().normalize(normalized, client_context)
        return normalized

    def _normalize_speech_payload(
        self,
        *,
        speech: Any,
        speech_segments: Any,
        fallback_to_default: bool = True,
    ) -> tuple[str, list[str]]:
        segments: list[str] = []
        if isinstance(speech_segments, list):
            for item in speech_segments:
                value = item
                if isinstance(item, dict):
                    value = item.get("speech") or item.get("text") or ""
                text = " ".join(str(value or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()).strip()
                if not text:
                    continue
                segments.append(text[:500])
                if len(segments) >= 3:
                    break

        if segments:
            return "\n".join(segments), segments

        text = str(speech or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            if not fallback_to_default:
                return "", []
            text = PERSONA.final_fallback_speech
        inferred_segments = [line.strip() for line in text.split("\n") if line.strip()]
        if 1 < len(inferred_segments) <= 3:
            return "\n".join(inferred_segments), inferred_segments
        return text, [text]

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
        normalized = dict(final_output or {})
        request = normalized.pop("_persona_request", {})
        request_present = bool(request.get("present")) if isinstance(request, dict) else False
        requested_active = str(request.get("active") or "").strip() if isinstance(request, dict) else ""
        persona_tool_changed = bool(
            tool_result
            and isinstance(tool_result.state_updates, dict)
            and tool_result.state_updates.get("persona_state_changed")
        )
        persona_service = self._get_persona_card_service()
        if persona_service is None:
            existing_persona = normalized.get("persona")
            active_id = str(existing_persona.get("active") or "").strip() if isinstance(existing_persona, dict) else ""
            normalized["persona"] = {"active": active_id}
            return normalized
        state = persona_service.apply_final_persona_request(
            profile_user_id=profile_user_id,
            session_id=session_id,
            requested_active=requested_active,
            request_present=request_present,
            allow_transition=not persona_tool_changed,
            timestamp=now_ts,
            source_id=source_id,
        )
        normalized["persona"] = {
            "active": str(state.get("active_id") or ""),
        }
        return normalized

    def _normalize_code_snippet(self, value: Any) -> str:
        text = str(value or "")
        if not text.strip():
            return ""
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if normalized.startswith("```") and normalized.endswith("```"):
            lines = normalized.splitlines()
            if len(lines) >= 2:
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                normalized = "\n".join(lines).strip()
        return normalized[:4000]

    def _build_assistant_dialogue_turn(self, speech: Any) -> dict[str, str] | None:
        text = str(speech or "").strip()
        if not text:
            return None
        return {
            "speaker": PERSONA.assistant_name,
            "speech": text,
        }

    def _build_dialogue_turns(
        self,
        *,
        preface_turn: dict[str, str] | list[dict[str, str]] | None,
        npc_turns: list[dict[str, Any]],
        final_speech: Any,
        final_speech_segments: Any = None,
    ) -> list[dict[str, str]]:
        turns: list[dict[str, str]] = []
        if isinstance(preface_turn, list):
            turns.extend([turn for turn in preface_turn if isinstance(turn, dict)])
        elif preface_turn:
            turns.append(preface_turn)

        for npc_turn in npc_turns:
            speaker = str(npc_turn.get("speaker") or "NPC").strip() or "NPC"
            speech = str(npc_turn.get("speech") or "").strip()
            if not speech:
                continue
            turns.append(
                {
                    "speaker": speaker,
                    "speech": speech,
                }
            )

        if isinstance(final_speech_segments, list) and final_speech_segments:
            for segment in final_speech_segments:
                final_turn = self._build_assistant_dialogue_turn(segment)
                if final_turn:
                    turns.append(final_turn)
        else:
            final_turn = self._build_assistant_dialogue_turn(final_speech)
            if final_turn:
                turns.append(final_turn)

        normalized: list[dict[str, str]] = []
        for turn in turns:
            if normalized and normalized[-1] == turn:
                continue
            normalized.append(turn)
        return normalized

    def _max_tool_rounds(self) -> int:
        raw_value = getattr(config, "MAX_TOOL_ROUNDS", 3)
        try:
            value = int(raw_value)
        except Exception:
            value = 3
        return max(1, min(5, value))

    def _tool_call_signature(self, tool_call: dict[str, Any]) -> str:
        try:
            return json.dumps(tool_call, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            return repr(sorted((str(key), str(value)) for key, value in dict(tool_call or {}).items()))

    def _describe_tool_call_for_prompt(self, tool_call: dict[str, Any]) -> str:
        tool_type = str(tool_call.get("type") or "unknown").strip() or "unknown"
        details = {
            str(key): value
            for key, value in dict(tool_call or {}).items()
            if key != "type" and value not in (None, "", [], {})
        }
        if not details:
            return tool_type
        try:
            return f"{tool_type} {json.dumps(details, ensure_ascii=False, sort_keys=True, default=str)[:500]}"
        except Exception:
            return f"{tool_type} {details!r}"[:500]

    def _build_multi_tool_followup_context(self, tool_followups: list[str], *, allow_more: bool) -> str:
        lines: list[str] = ["【本轮工具执行记录】"]
        if tool_followups:
            lines.extend([str(item).strip() for item in tool_followups if str(item).strip()])
        else:
            lines.append("(暂时没有可用的工具结果。)")
        if allow_more:
            lines.append(
                "如果任务还没完成，可以继续在 tool_call 字段调用下一步必要工具；"
                "如果结果已经足够，请将 tool_call 设为 null，并自然回复主人。"
            )
        else:
            lines.append("本轮不要再调用工具，请将 tool_call 设为 null，并基于已有结果自然回复主人。")
        return "\n\n".join(lines)

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
        pack_names = MODE_TOOL_PACKS.get(client_context.effective_mode, ("base",))
        selected_names: list[str] = []
        seen: set[str] = set()
        for pack_name in pack_names:
            for tool_name in TOOL_PACKS.get(pack_name, ()):
                if tool_name in seen:
                    continue
                seen.add(tool_name)
                selected_names.append(tool_name)
        return selected_names

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

    def _build_client_mode_prompt_context(self, client_context: ClientProtocolContext | None) -> str:
        if client_context is None:
            return ""
        public = client_context.to_public_dict()
        lines = [
            "【客户端模式】",
            f"当前有效模式：{public.get('effective_mode')}",
            f"输出 profile：{public.get('output_profile')}",
            "本轮只需要遵循当前 profile 的输出字段；不要在台词里解释这些系统字段。",
        ]
        if public.get("degraded_from"):
            lines.append(
                f"请求模式 {public.get('degraded_from')} 已降级为 {public.get('effective_mode')}；"
                "按有效模式输出即可。"
            )
        return "\n".join(lines)

    def _normalize_tool_call(
        self,
        value: Any,
        *,
        client_context: ClientProtocolContext | None = None,
        profile_user_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None

        tool_type = str(value.get("type") or "").strip()
        if not tool_type:
            return None

        handlers = self._resolve_tool_handlers(
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )
        handler = handlers.get(tool_type)
        if handler is None:
            return None
        return handler.normalize_call(value)

    def _promote_narrated_tool_call(
        self,
        final_output: dict[str, Any],
        *,
        user_message: str,
        client_context: ClientProtocolContext | None = None,
        profile_user_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        """Recover when the model narrates a tool call in speech instead of JSON.

        The model occasionally says "工具调用：fetch_media_from_url ..." in speech
        while leaving tool_call as null. Only the JSON field is executable, so we
        promote this very narrow remote-media case when the user's current message
        clearly asks for download/retry work.
        """
        existing = self._normalize_tool_call(
            final_output.get("tool_call"),
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )
        if existing:
            return final_output

        speech_parts = [str(final_output.get("speech") or "")]
        segments = final_output.get("speech_segments")
        if isinstance(segments, list):
            speech_parts.extend(str(item or "") for item in segments)
        speech = "\n".join(part for part in speech_parts if part).strip()
        if not speech:
            return final_output
        narrated_tool = "fetch_media_from_url" in speech or (
            "工具调用" in speech and ("链接" in speech or "url" in speech.lower())
        )
        if not narrated_tool:
            return final_output

        urls = self._extract_prefetchable_remote_media_urls(str(user_message or ""))
        retry_requested = self._message_requests_remote_media_retry(user_message)
        if not urls and retry_requested:
            urls = self._recent_prefetchable_remote_media_urls(
                profile_user_id=profile_user_id,
                session_id=session_id,
            )
        if not urls:
            return final_output
        if not retry_requested and not self._message_requests_remote_media_fetch(user_message, urls=urls):
            return final_output

        repaired = dict(final_output)
        repaired["tool_call"] = {
            "type": "fetch_media_from_url",
            "urls": urls,
        }
        return repaired

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
        normalized_call = self._normalize_tool_call(
            tool_call,
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )
        if not normalized_call:
            return None

        handlers = self._resolve_tool_handlers(
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )
        handler = handlers.get(str(normalized_call.get("type") or ""))
        if handler is None:
            return None

        enriched_visual_payload = dict(visual_payload or {})
        enriched_visual_payload["_profile_user_id"] = profile_user_id
        if memory_exclude_source_ids:
            enriched_visual_payload["_memory_retrieval_exclude_source_ids"] = list(memory_exclude_source_ids)
        return handler.execute(
            call=normalized_call,
            context=ToolExecutionContext(
                profile_user_id=profile_user_id,
                session_id=session_id,
                now_ts=now_ts,
                visual_payload=enriched_visual_payload,
                current_user_source_id=current_user_source_id,
            ),
        )

    def _execute_retrieve_memory_tool(
        self,
        *,
        call: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        query = normalize_text(str(call.get("query") or "")).strip()
        keywords = [str(item).strip() for item in list(call.get("keywords") or []) if str(item).strip()]
        time_hint = call.get("time_hint") if isinstance(call.get("time_hint"), dict) else None
        current_user_record = (
            self.store.get_message_by_source_id(context.current_user_source_id)
            if str(context.current_user_source_id or "").strip()
            else None
        )
        original_query = str((current_user_record or {}).get("content") or query)
        episodic_limit = max(1, int(getattr(config, "EPISODIC_VISIBLE_MAX", getattr(config, "RECENT_SUMMARY_LIMIT", 5))))
        semantic_limit = max(1, int(getattr(config, "SEMANTIC_VISIBLE_LIMIT", 3)))
        recent_raw = self.store.get_unsummarized_messages(context.session_id)
        recent_episodic_summaries = self.store.get_visible_episodic_summaries(context.profile_user_id, limit=episodic_limit)
        recent_semantic_summaries = (
            self.store.get_recent_semantic_summaries(context.profile_user_id, limit=semantic_limit)
            if bool(getattr(config, "ENABLE_SEMANTIC_MEMORY", True))
            else []
        )
        extra_excludes = []
        visual_payload = context.visual_payload if isinstance(context.visual_payload, dict) else {}
        raw_extra_excludes = visual_payload.get("_memory_retrieval_exclude_source_ids")
        if isinstance(raw_extra_excludes, list):
            extra_excludes = [str(item).strip() for item in raw_extra_excludes if str(item).strip()]
        exclude_source_ids = self._collect_visible_context_source_ids(
            recent_raw=recent_raw,
            recent_episodic_summaries=recent_episodic_summaries,
            recent_semantic_summaries=recent_semantic_summaries,
            extra_source_ids=[context.current_user_source_id, *extra_excludes],
        )
        pipeline = self._get_retrieval_service().run_explicit(
            profile_user_id=context.profile_user_id,
            original_query=original_query,
            now_ts=int(context.now_ts),
            query=query,
            keywords=keywords,
            time_hint=time_hint,
            exclude_source_ids=exclude_source_ids,
            verifier_debug_enabled=False,
            route="post_retrieval",
        )
        snippets = [str(item).strip() for item in pipeline.confirmed_snippets if str(item).strip()]
        if snippets:
            followup_context = (
                "你刚刚主动检索了长期记忆。下面是可能回答主人问题的参考记忆：\n"
                + "\n\n".join(snippets)
                + "\n\n请基于这些参考记忆自然回应；不要声称系统绝对证明了这些记忆。"
            )
        else:
            followup_context = (
                "你刚刚主动检索了长期记忆，但这次没有找到足以回答主人问题的相关记忆。"
                "请自然说明自己没有想起可靠线索，不要编造。"
            )
        return ToolExecutionResult(
            tool_type="retrieve_memory",
            raw_turns=[],
            stream_events=[],
            followup_context=followup_context,
            state_updates={
                "memory_retrieval": {
                    "tool_call": {
                        "query": query,
                        "keywords": keywords,
                        "time_hint": time_hint or {},
                    },
                    "retrieval_result": pipeline.retrieval_result,
                    "verifier_output": pipeline.verifier_output,
                    "verifier_timing": pipeline.verifier_timing,
                    "confirmed_snippets": snippets,
                }
            },
        )

    def _describe_tool_scene_context(self, visual_payload: dict[str, Any]) -> str:
        if self.resource_manifest:
            profile_user_id = str(visual_payload.get("_profile_user_id") or "").strip()
            runtime_projection = self._get_user_runtime_projection(profile_user_id) if profile_user_id else {}
            return self.resource_manifest.describe_visual_state(
                visual_payload,
                extra_bgm_tracks=list(runtime_projection.get("extra_bgm_tracks") or []),
                extra_scene_groups=list(runtime_projection.get("extra_scene_groups") or []),
                extra_character_outfits=list(runtime_projection.get("extra_character_outfits") or []),
            )
        return "当前场景未设置"

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
        effective_now_ts = int(now_ts or time.time())
        due_records = self.store.claim_due_reminders(
            profile_user_id=profile_user_id,
            session_id=session_id,
            now_ts=effective_now_ts,
            limit=limit,
        )
        if not due_records:
            return []

        visual_payload = self._resolve_current_visual_payload(
            session_id=session_id,
            current_visual_payload=current_visual_payload,
        )
        notifications = [
            self._build_reminder_notification_payload(
                reminder=record,
                visual_payload=visual_payload,
            )
            for record in due_records
        ]
        for notification in notifications:
            self._persist_due_reminder_notification(
                profile_user_id=profile_user_id,
                session_id=session_id,
                notification=notification,
                now_ts=effective_now_ts,
            )
        return notifications

    def _build_reminder_notification_payload(
        self,
        *,
        reminder: dict[str, Any],
        visual_payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        visual = self._coerce_visual_payload(visual_payload or {}) or {
            "emotion": "normal",
            "character": {},
            "scene": {},
        }
        speech = self._generate_reminder_notification_speech(
            reminder=reminder,
            visual_payload=visual,
        )
        return {
            "reminder_id": reminder["reminder_id"],
            "source": "reminder",
            "emotion": str(visual.get("emotion") or "normal"),
            "speech": speech,
            "memory_tags": "",
            "status": "final",
            "score": 0.0,
            "tool_call": None,
            "choices": [],
            "character": dict(visual.get("character") or {}),
            "scene": dict(visual.get("scene") or {}),
            "dialogue_turns": [
                {
                    "speaker": PERSONA.assistant_name,
                    "speech": speech,
                }
            ],
            "due_ts": int(reminder["due_ts"]),
            "fired_at": int(reminder.get("fired_at") or reminder["due_ts"]),
        }

    def _generate_reminder_notification_speech(
        self,
        *,
        reminder: dict[str, Any],
        visual_payload: dict[str, Any],
    ) -> str:
        fallback_speech = self._format_reminder_notification(reminder)
        visual_context = self._describe_tool_scene_context(visual_payload)
        result = self.llm.call_chat_json(
            system_prompt=(
                "你是 Akane。现在有一条已经到时间的提醒需要你自然地说出口。"
                "你只输出一个合法 JSON 对象，字段固定为 speech。"
                "speech 要像 Akane 当下自然想起这件事后对用户说的一句提醒，口吻亲近、简短、自然。"
                "只需要 1 到 2 句，不要解释系统原理，不要说自己忘记了，也不要输出多余字段。"
            ),
            user_prompt=(
                f"当前演出状态：{visual_context}\n"
                f"提醒内容：{str(reminder.get('content') or '').strip()}\n"
                f"原始提醒时间说法：{str(reminder.get('raw_time_text') or '').strip() or '(未提供)'}\n"
                f"当前时间：{timestamp_to_datetime_label(int(reminder.get('fired_at') or reminder.get('due_ts') or time.time()))}\n"
                "请用 Akane 的语气说一句现在该提醒用户的话。"
            ),
            fallback={"speech": fallback_speech},
            temperature=0.85,
        )
        speech = normalize_text(str(result.get("speech") or fallback_speech))
        return speech[:120] if speech else fallback_speech

    def _persist_due_reminder_notification(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        notification: dict[str, Any],
        now_ts: int,
    ) -> None:
        speech = str(notification.get("speech") or "").strip()
        if not speech:
            return
        reminder_ts = int(notification.get("fired_at") or now_ts)
        record = self.store.add_message(
            profile_user_id=profile_user_id,
            session_id=session_id,
            role="assistant",
            content=speech,
            timestamp=reminder_ts,
            date_label=timestamp_to_date_label(reminder_ts),
            time_of_day=infer_time_of_day(reminder_ts),
            semantic_tags=extract_semantic_tags(speech),
        )
        self._upsert_raw_record(record)
        self._schedule_summary_cycle(profile_user_id=profile_user_id, session_id=session_id)

    def _format_reminder_notification(self, reminder: dict[str, Any]) -> str:
        content = str(reminder.get("content") or "").strip()
        raw_time_text = str(reminder.get("raw_time_text") or "").strip()
        if raw_time_text:
            return f"喵呜，到时间啦。你之前让我在{raw_time_text}提醒你“{content}”，现在该去做啦。"
        return f"喵呜，到时间啦。你之前让我提醒你的事是“{content}”，现在该去做啦。"

    def _build_current_visual_context(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        current_visual_payload: Any,
        visual_payload: dict[str, Any] | None = None,
        runtime_projection: dict[str, Any] | None = None,
    ) -> str:
        if not self.resource_manifest:
            return "当前没有额外的演出状态参考。"

        effective_visual_payload = visual_payload or self._resolve_current_visual_payload(
            session_id=session_id,
            current_visual_payload=current_visual_payload,
        )
        if not effective_visual_payload:
            return "当前没有额外的演出状态参考。"
        effective_runtime_projection = runtime_projection or self._get_user_runtime_projection(profile_user_id)
        return self.resource_manifest.describe_visual_state(
            effective_visual_payload,
            extra_bgm_tracks=list(effective_runtime_projection.get("extra_bgm_tracks") or []),
            extra_scene_groups=list(effective_runtime_projection.get("extra_scene_groups") or []),
            extra_character_outfits=list(effective_runtime_projection.get("extra_character_outfits") or []),
        )

    def _schedule_visual_observations_for_payload(
        self,
        *,
        payload: dict[str, Any] | None,
        profile_user_id: str,
        session_id: str,
    ) -> None:
        if not isinstance(payload, dict):
            return

        visual_payload = self._coerce_visual_payload(payload)
        runtime_projection = self._get_user_runtime_projection(profile_user_id)
        if visual_payload:
            try:
                self.vision_service.schedule_scene_observation(
                    visual_payload=visual_payload,
                    extra_bgm_tracks=list(runtime_projection.get("extra_bgm_tracks") or []),
                    extra_scene_groups=list(runtime_projection.get("extra_scene_groups") or []),
                    extra_character_outfits=list(runtime_projection.get("extra_character_outfits") or []),
                )
            except Exception as exc:
                logger.warning("schedule scene observation failed: %s", exc)
            try:
                self.vision_service.schedule_outfit_observation(
                    visual_payload=visual_payload,
                    extra_bgm_tracks=list(runtime_projection.get("extra_bgm_tracks") or []),
                    extra_scene_groups=list(runtime_projection.get("extra_scene_groups") or []),
                    extra_character_outfits=list(runtime_projection.get("extra_character_outfits") or []),
                )
            except Exception as exc:
                logger.warning("schedule outfit observation failed: %s", exc)

        try:
            focused_gift = self.gift_service.resolve_focus_asset(
                profile_user_id=profile_user_id,
                session_id=session_id,
                asset_id="",
            )
            if focused_gift:
                self.vision_service.schedule_gift_observation(asset=focused_gift)
        except Exception as exc:
            logger.warning("schedule gift observation failed: %s", exc)

    def _handle_ready_visual_observation(
        self,
        target,
        observation: dict[str, Any],
    ) -> None:
        self.vision_observation_router.handle(target, observation)

    def _get_user_runtime_projection(self, profile_user_id: str) -> dict[str, Any]:
        normalized = str(profile_user_id or "").strip()
        if not normalized:
            return {
                "extra_bgm_tracks": [],
                "extra_scene_groups": [],
                "extra_character_outfits": [],
            }
        return self.gift_service.build_runtime_projection(profile_user_id=normalized)

    def _get_user_bgm_tracks(self, profile_user_id: str) -> list[dict[str, Any]]:
        return list(self._get_user_runtime_projection(profile_user_id).get("extra_bgm_tracks") or [])

    def _resolve_current_visual_payload(self, *, session_id: str, current_visual_payload: Any) -> dict[str, Any] | None:
        if isinstance(current_visual_payload, dict):
            payload = self._coerce_visual_payload(current_visual_payload)
            if payload:
                return payload

        latest_eval = self.store.get_latest_eval_turn(session_id)
        if not latest_eval:
            return None
        final_json = latest_eval.get("final_json")
        if not isinstance(final_json, dict):
            return None
        return self._coerce_visual_payload(final_json)

    def _coerce_visual_payload(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        scene = payload.get("scene")
        character = payload.get("character")
        emotion = payload.get("emotion")
        if not isinstance(scene, dict) and not isinstance(character, dict) and emotion is None:
            return None
        return {
            "emotion": str(emotion or ""),
            "character": character if isinstance(character, dict) else {},
            "scene": scene if isinstance(scene, dict) else {},
        }

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
