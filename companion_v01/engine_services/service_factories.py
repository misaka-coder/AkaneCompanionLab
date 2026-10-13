"""Embedding provider construction, reindexing, media executors, workspace services, and execution provider factories."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Generator, Mapping

import config

from ..attachment_inbox import AttachmentInboxService
from ..attachment_ingest import AttachmentIngestService
from ..desktop_music_timeline import DesktopMusicTimelineService
from ..embedding_provider import (
    BaseEmbeddingProvider,
    CachedEmbeddingProvider,
    HashedEmbeddingProvider,
)
from ..generated_files import GeneratedFileService
from ..huggingface_provider import HuggingFaceEmbeddingProvider
from ..image_materials import SessionImageMaterialResolver
from ..jina_embedding_provider import (
    DEFAULT_JINA_EMBEDDING_BASE_URL,
    DEFAULT_JINA_EMBEDDING_MODEL,
    JinaEmbeddingProvider,
)
from ..local_media_executor import LocalMediaExecutorClient
from ..memcore_integration.contracts import normalize_retrieval_mode
from ..remote_embedding_provider import RemoteEmbeddingProvider
from ..vector_entry_builder import (
    build_raw_vector_entry,
    build_semantic_summary_vector_entry,
    build_summary_vector_entry,
)
from ..workspace_files import WorkspaceFileService

logger = logging.getLogger("akane.engine")


class ServiceFactoryMixin:
    """Mixin providing embedding providers, media engines, workspace services, and execution provider factories."""

    def snapshot_embedding_reindex_status(self) -> dict[str, Any]:
        with self._embedding_reindex_lock:
            snapshot = dict(self._embedding_reindex_status)
        snapshot["provider_health"] = dict(getattr(self, "_embedding_startup_status", {}) or {})
        return snapshot

    def snapshot_embedding_runtime(self) -> dict[str, Any]:
        provider = getattr(self, "embedding_provider", None)
        snapshot = getattr(provider, "snapshot_runtime", None)
        if callable(snapshot):
            return snapshot()
        if getattr(self, "memory_retrieval_mode", "hybrid") == "navigation":
            return {
                "status": "disabled", "reason": "retrieval_disabled", "retrieval_mode": "navigation",
                "model_weight_bytes": 0, "batch_size": 0, "effective_batch_size": 0,
                "active_calls": 0, "calls": 0, "cached_entries": 0, "cache_max_entries": 0,
                "cuda_metrics_available": False, "cuda_allocated_bytes": 0, "cuda_reserved_bytes": 0,
            }
        return {"status": "unavailable", "reason": "embedding_runtime_metrics_unavailable"}

    def _build_embedding_provider(self) -> BaseEmbeddingProvider | None:
        self.memory_retrieval_mode = normalize_retrieval_mode(
            getattr(config, "MEMCORE_RETRIEVAL_MODE", "navigation"),
            backend=getattr(config, "MEMORY_BACKEND", "memcore"),
        )
        if self.memory_retrieval_mode == "navigation":
            self._embedding_startup_status = {
                "ok": False, "status": "disabled", "reason": "retrieval_disabled",
                "retrieval_mode": "navigation", "provider": "", "model": "", "dimension": 0,
            }
            return None
        provider_mode = str(getattr(config, "EMBEDDING_PROVIDER", "auto") or "auto").strip().lower() or "auto"
        self._embedding_startup_status = {
            "ok": True,
            "status": "not_checked",
            "provider": provider_mode,
            "model": "",
            "dimension": 0,
            "reason": "",
        }
        base_provider: BaseEmbeddingProvider = HashedEmbeddingProvider()
        if provider_mode in {"remote", "openai-compatible", "openai_compatible"}:
            base_provider = RemoteEmbeddingProvider(
                api_key=str(getattr(config, "EMBEDDING_API_KEY", "") or ""),
                base_url=str(getattr(config, "EMBEDDING_BASE_URL", "") or ""),
                model_name=str(getattr(config, "EMBEDDING_MODEL_NAME", "") or ""),
                dimension=int(getattr(config, "EMBEDDING_DIMENSION", 1024) or 1024),
                timeout=float(getattr(config, "EMBEDDING_TIMEOUT_SECONDS", 30.0) or 30.0),
            )
            self._embedding_startup_status = base_provider.verify_retrieval_space()
            if not self._embedding_startup_status.get("ok"):
                logger.warning(
                    "Remote embedding startup verification failed: %s",
                    self._embedding_startup_status.get("reason") or "unknown",
                )
        elif provider_mode in {"jina", "jina-ai"}:
            configured_model = str(getattr(config, "EMBEDDING_MODEL_NAME", "") or "").strip()
            model_name = (
                DEFAULT_JINA_EMBEDDING_MODEL
                if not configured_model or configured_model == "BAAI/bge-m3"
                else configured_model
            )
            base_provider = JinaEmbeddingProvider(
                api_key=str(getattr(config, "EMBEDDING_API_KEY", "") or ""),
                base_url=(
                    str(getattr(config, "EMBEDDING_BASE_URL", "") or "").strip() or DEFAULT_JINA_EMBEDDING_BASE_URL
                ),
                model_name=model_name,
                dimension=int(getattr(config, "EMBEDDING_DIMENSION", 1024) or 1024),
                timeout=float(getattr(config, "EMBEDDING_TIMEOUT_SECONDS", 30.0) or 30.0),
            )
            self._embedding_startup_status = base_provider.verify_retrieval_space()
            if not self._embedding_startup_status.get("ok"):
                logger.warning(
                    "Jina embedding startup verification failed: %s",
                    self._embedding_startup_status.get("reason") or "unknown",
                )
        elif provider_mode in {"auto", "huggingface", "hf", "sentence-transformer", "sentence-transformers"}:
            try:
                base_provider = HuggingFaceEmbeddingProvider(
                    model_name=str(getattr(config, "EMBEDDING_MODEL_NAME", "") or "BAAI/bge-m3"),
                    device=str(getattr(config, "EMBEDDING_DEVICE", "") or "").strip() or None,
                    local_files_only=bool(getattr(config, "EMBEDDING_LOCAL_FILES_ONLY", True)),
                    cache_folder=str(getattr(config, "EMBEDDING_CACHE_FOLDER", "") or "").strip() or None,
                    precision=str(getattr(config, "EMBEDDING_PRECISION", "float32") or "float32"),
                    batch_size=int(getattr(config, "EMBEDDING_BATCH_SIZE", 8)),
                    idle_seconds=float(getattr(config, "EMBEDDING_IDLE_CACHE_SECONDS", 30.0)),
                )
            except Exception as exc:
                base_provider = HashedEmbeddingProvider()
                self._embedding_startup_status.update(
                    reason="local_embedding_load_failed", error_type=type(exc).__name__,
                )
        if self._embedding_startup_status.get("status") == "not_checked":
            self._embedding_startup_status.update(
                {
                    "provider": base_provider.name,
                    "model": str(getattr(base_provider, "model_name", "") or ""),
                    "dimension": base_provider.dimension,
                    "status": "degraded" if base_provider.name == "hashed" else "ready",
                    "ok": base_provider.name != "hashed",
                    "reason": self._embedding_startup_status.get("reason") or (
                        "hashed_embedding_selected_or_fallback" if base_provider.name == "hashed" else ""
                    ),
                }
            )
        if int(getattr(config, "EMBEDDING_CACHE_SIZE", 0) or 0) > 0:
            return CachedEmbeddingProvider(
                base_provider,
                max_entries=int(getattr(config, "EMBEDDING_CACHE_SIZE", 0) or 0),
            )
        return base_provider

    def _maybe_start_embedding_reindex(self) -> None:
        if self._memcore_owns_legacy_vector_index() or getattr(self, "vector_store", None) is None:
            with self._embedding_reindex_lock:
                self._embedding_reindex_status.update(
                    {
                        "state": "disabled",
                        "processed": 0,
                        "total": 0,
                        "started_at": 0.0,
                        "finished_at": time.time(),
                        "error": (
                            "retrieval_disabled" if getattr(self, "memory_retrieval_mode", "hybrid") == "navigation"
                            else "memcore_owns_legacy_vector_index"
                        ),
                        "collection_name": str(getattr(getattr(self, "vector_store", None), "collection_name", "")),
                    }
                )
            return
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
                daemon=False,
            )
            self._embedding_reindex_thread.start()
        logger.info(
            "Akane 正在后台悄悄整理以前的回忆哦，可能需要稍微花一点点时间～ "
            f"(collection={self.vector_store.collection_name}, current={current_entries}, total={total_records})"
        )

    def _run_embedding_reindex(self) -> None:
        if self._memcore_owns_legacy_vector_index():
            with self._embedding_reindex_lock:
                self._embedding_reindex_status.update(
                    {
                        "state": "disabled",
                        "processed": 0,
                        "finished_at": time.time(),
                        "error": "memcore_owns_legacy_vector_index",
                    }
                )
            return
        batch_size = max(1, int(getattr(config, "EMBEDDING_REINDEX_BATCH_SIZE", 64) or 64))
        processed = 0
        stop_event = getattr(self, "_embedding_reindex_stop", None)
        try:
            batch_iterators = (
                (self.store.iter_messages_for_vector_reindex(batch_size), build_raw_vector_entry),
                (self.store.iter_summaries_for_vector_reindex(batch_size), build_summary_vector_entry),
                (
                    self.store.iter_semantic_summaries_for_vector_reindex(batch_size),
                    build_semantic_summary_vector_entry,
                ),
            )
            for batches, entry_builder in batch_iterators:
                for record_batch in batches:
                    if stop_event is not None and stop_event.is_set():
                        with self._embedding_reindex_lock:
                            self._embedding_reindex_status.update(
                                {
                                    "state": "stopped",
                                    "processed": int(processed),
                                    "finished_at": time.time(),
                                    "error": "shutdown_requested",
                                }
                            )
                        return
                    entries = [entry_builder(record) for record in record_batch]
                    if not entries:
                        continue
                    if stop_event is not None and stop_event.is_set():
                        with self._embedding_reindex_lock:
                            self._embedding_reindex_status.update(
                                {
                                    "state": "stopped",
                                    "processed": int(processed),
                                    "finished_at": time.time(),
                                    "error": "shutdown_requested",
                                }
                            )
                        return
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
            material_trace_recorder=self._record_attachment_material_trace,
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
            qq_channel_config=getattr(self, "qq_channel_config", None),
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
            root_dir=self.workspace_root,
            store=store,
            max_read_bytes=int(
                getattr(config, "AKANE_WORKSPACE_MAX_READ_BYTES", 64 * 1024 * 1024) or (64 * 1024 * 1024)
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

    def _prepare_timeline_transcript(self, **kwargs):
        from ..optional_media_binding import prepare_timeline_transcript

        return prepare_timeline_transcript(self, **kwargs)

    def _create_local_media_executor(self) -> LocalMediaExecutorClient | None:
        base_url = str(getattr(config, "LOCAL_MEDIA_EXECUTOR_BASE_URL", "") or "").strip()
        if not base_url:
            return None
        try:
            return LocalMediaExecutorClient(
                base_url=base_url,
                timeout_seconds=float(getattr(config, "LOCAL_MEDIA_EXECUTOR_TIMEOUT_SECONDS", 1800.0) or 1800.0),
            )
        except ValueError as exc:
            logger.warning("local media executor disabled: %s", exc)
            return None

    def _get_local_media_executor(self) -> LocalMediaExecutorClient | None:
        if hasattr(self, "local_media_executor"):
            return getattr(self, "local_media_executor", None)
        service = self._create_local_media_executor()
        self.local_media_executor = service
        return service

    def _get_image_material_resolver(self) -> SessionImageMaterialResolver | None:
        resolver = getattr(self, "image_material_resolver", None)
        if resolver is not None:
            return resolver
        attachment_service = self._get_attachment_inbox_service()
        generated_file_service = self._get_generated_file_service()
        if attachment_service is None or generated_file_service is None:
            return None
        resolver = SessionImageMaterialResolver(
            attachment_service=attachment_service,
            generated_file_service=generated_file_service,
        )
        self.image_material_resolver = resolver
        return resolver

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
            vocal_preparer=self._prepare_timeline_vocals,
            transcriber=self._prepare_timeline_transcript,
        )
        self.desktop_music_timeline_service = service
        return service

    def _prepare_timeline_vocals(self, **kwargs):
        from ..optional_media_binding import prepare_timeline_vocals

        return prepare_timeline_vocals(self, **kwargs)

    def _get_music_context_assembler(self):
        assembler = getattr(self, "music_context_assembler", None)
        if assembler is not None:
            return assembler
        store = getattr(self, "store", None)
        if store is None:
            return None
        from ..music_context import MusicContextAssembler, MusicControl
        from .. import music_control_store

        def _controls_provider(profile_user_id: str) -> frozenset:
            _default = frozenset(
                {
                    MusicControl.PAUSE,
                    MusicControl.NEXT,
                    MusicControl.PREV,
                    MusicControl.RECOMMEND,
                }
            )
            if not profile_user_id:
                return _default
            try:
                with store._connect() as conn:
                    music_control_store.ensure_schema(conn)
                    names = music_control_store.get_enabled_controls(conn, profile_user_id=profile_user_id)
            except Exception:
                return _default
            valid = {c.value for c in MusicControl}
            return frozenset(MusicControl(n) for n in names if n in valid)

        assembler = MusicContextAssembler(store=store, controls_provider=_controls_provider)
        self.music_context_assembler = assembler
        return assembler


    def _build_memory_timeline_tool_service(self) -> Any:
        try:
            from ..memcore_integration.timeline import MemcoreTimelineToolService

            return MemcoreTimelineToolService(
                memcore_manager=getattr(self, "memcore_manager", None),
            )
        except Exception as exc:
            logger.warning("memcore timeline tool adapter disabled: %s", exc)
            return None

    def _get_approval_store(self) -> Any:
        store = getattr(self, "approval_store", None)
        if store is not None:
            return store
        from ..capability_approval import CapabilityApprovalStore

        store = CapabilityApprovalStore()
        try:
            setattr(self, "approval_store", store)
        except Exception:
            pass
        return store

    def _get_skill_registry(self) -> Any:
        registry = getattr(self, "skill_registry", None)
        if registry is not None:
            return registry
        from ..skill_runtime import SkillRegistry, resolve_skill_roots

        bundled_root, managed_root, execution_workspace = resolve_skill_roots(config)
        registry = SkillRegistry(
            bundled_root=bundled_root,
            managed_root=managed_root,
            execution_workspace_root=execution_workspace,
            contributed_roots_provider=getattr(self, "plugin_skill_roots_provider", None),
        )
        try:
            setattr(self, "skill_registry", registry)
        except Exception:
            pass
        return registry

    def _build_execution_provider(self) -> Any | None:
        """Host-bound execution provider, or None when execution is disabled.

        Driven by the host config surface (``EXECUTION_ENABLED`` + paths), which
        is frozen at startup — the model can never select a provider. None keeps
        the exec tools out of the schema entirely; a configured-but-unavailable
        provider degrades to a structured ``capability_unavailable`` result and
        a ``capability_status()`` unavailable disclosure instead.
        """
        provider = getattr(self, "execution_provider", None)
        if provider is not None:
            return provider
        if not bool(getattr(config, "EXECUTION_ENABLED", False)):
            return None
        try:
            from pathlib import Path

            from ..execution_local import TrustedLocalExecutor

            data_root = Path(getattr(config, "DATA_ROOT", "users_data"))
            state_dir = Path(getattr(config, "STATE_DIR", "users_data"))
            configured_workspace = str(getattr(config, "EXECUTION_WORKSPACE_ROOT", "") or "").strip()
            if configured_workspace:
                workspace_root = Path(configured_workspace)
            else:
                # Default workspace inside the Akane data root. Auto-created only
                # in this host-owned default; an explicitly configured path is
                # never created here so a misspelled host setting fails closed
                # as workspace_missing instead of appearing silently elsewhere.
                workspace_root = data_root / "execution_workspace"
                try:
                    workspace_root.mkdir(parents=True, exist_ok=True)
                except OSError:
                    pass
            run_log_dir = Path(getattr(config, "EXECUTION_RUN_LOG_DIR", "") or (state_dir / "execution_runlogs"))
            allowed_raw = str(getattr(config, "EXECUTION_ALLOWED_ENV_NAMES", "") or "").strip()
            allowed_names = [name.strip() for name in allowed_raw.split(",") if name.strip()] or None
            credential_raw = str(getattr(config, "EXECUTION_CREDENTIAL_ENV_NAMES", "") or "").strip()
            credential_names = [name.strip() for name in credential_raw.split(",") if name.strip()]
            proxy_url = str(getattr(config, "EXECUTION_PROXY_URL", "") or "").strip()
            mounts = self._get_skill_registry().mount_paths()
            sdk_root = Path(getattr(config, "BASE_DIR", Path(__file__).resolve().parents[2])) / "examples" / "plugins"
            if sdk_root.is_dir():
                # Stable entry to the release's public plugin examples.  It is
                # intentionally not advertised by the generic coding prompt;
                # the plugin-development Skill will disclose it when relevant.
                mounts["akane-sdk"] = sdk_root
            provider = TrustedLocalExecutor(
                workspace_root=workspace_root,
                run_log_dir=run_log_dir,
                allowed_env_names=allowed_names,
                credential_env_names=credential_names,
                proxy_url=proxy_url,
                mounts=mounts,
            )
        except Exception as exc:
            logger.warning("execution provider disabled: %s", exc)
            return None
        try:
            setattr(self, "execution_provider", provider)
        except Exception:
            pass
        return provider

    def _get_project_workspace_service(self) -> Any | None:
        service = getattr(self, "project_workspace_service", None)
        if service is not None:
            return service
        provider = self._build_execution_provider()
        workspace_root = getattr(provider, "workspace_root", None) if provider is not None else None
        if workspace_root is None:
            return None
        try:
            from ..project_workspace import ProjectWorkspaceService

            service = ProjectWorkspaceService(
                store=self.store,
                execution_workspace_root=workspace_root,
                protected_roots=(
                    self.runtime_layout.data_root,
                    self.runtime_layout.users_data_dir,
                    self.runtime_layout.state_dir,
                    self.runtime_layout.logs_dir,
                    self.runtime_layout.cache_dir,
                    self.runtime_layout.run_dir,
                ) if getattr(self, "runtime_layout", None) is not None else (),
            )
        except Exception as exc:
            logger.warning("project workspace service disabled: %s", type(exc).__name__)
            return None
        try:
            setattr(self, "project_workspace_service", service)
        except Exception:
            pass
        return service
