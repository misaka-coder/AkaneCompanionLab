from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from contextvars import ContextVar, copy_context
from pathlib import Path
from typing import Any, Callable, Generator, Mapping

import config
from memcore import memory_metadata_has_signal as memcore_metadata_has_signal

from .artifact_system import ArtifactContainerService
from .artifact_broker import ArtifactBroker
from .attachment_inbox import AttachmentInboxService
from .attachment_ingest import AttachmentIngestService
from .background_tasks import BackgroundTaskRunner
from .capability_adapters import McpStdioCapabilityAdapter
from .capability_registry import (
    CapabilityRegistry,
    CapabilitySelection,
    CapabilitySnapshot,
    ExecutorBroker,
    ServerLocalOfferIndex,
    is_document_attachment,
    is_document_generated_file,
    is_media_attachment,
    is_media_generated_file,
    resolve_capability_disclosures,
)
from . import desktop_pet_engine
from .tool_batch import execute_tool_batch, cancelled_tool_result
from .turn_coordination import cancellation_requested, current_cancellation_check
from .tool_continuation import can_finish_tool_batch
from .embedding_provider import BaseEmbeddingProvider, CachedEmbeddingProvider, HashedEmbeddingProvider
from .generated_files import GeneratedFileService
from .local_media_executor import LocalMediaExecutorClient
from .image_materials import SessionImageMaterialResolver
from . import gift_engine
from .gift_system import GiftSystemService
from .instance_profile import InstanceContext, build_local_default_instance_context
from .instance_runtime import InstanceRuntimeLayout, require_instance_owned_path
from . import media_bridge_engine
from .huggingface_provider import HuggingFaceEmbeddingProvider
from .jina_embedding_provider import (
    DEFAULT_JINA_EMBEDDING_BASE_URL,
    DEFAULT_JINA_EMBEDDING_MODEL,
    JinaEmbeddingProvider,
)
from .remote_embedding_provider import RemoteEmbeddingProvider
from .llm_runtime import LLMRuntime, ModelRequestCancelled
from .client_protocol import ClientCapability, ClientMode, ClientProtocolContext, DESKTOP_PET_DEFAULT_CAPABILITIES
from .execution_specs import EXEC_TOOL_SPEC_BY_ID
from .care_runtime import CareModulePort, normalize_desktop_care_config
from .desktop_pet_character_resources import load_character_care_config
from .desktop_music_timeline import DesktopMusicTimelineService
from .deployment_security import QQChannelRuntimeConfig
from . import desktop_context_engine
from .mode_profiles import ModeProfileRegistry
from .output_adapters import OutputAdapterRegistry
from .persona_config import PERSONA
from .engine_services.turn_completion import complete_turn
from .engine_services.turn_contracts import DeferredTurn
from .engine_services.turn_preparation import prepare_turn
from .engine_services.turn_rounds import run_turn_rounds
from .engine_services.turn_transport import TurnTransportMixin
from .engine_services.service_factories import (
    ServiceFactoryMixin,
)
from .engine_services.media_vision_routing import (
    MediaVisionAndRoutingMixin,
)
from .engine_services.prompt_context import (
    PromptContextMixin,
)
from .engine_services.output_formatting import (
    OutputFormattingMixin,
)
from .engine_services.desktop_care_pet import (
    DesktopCareAndPetMixin,
)
from .engine_services.memcore_turn_pipeline import (
    MEMORY_ANNOTATION_STATUS_FIELD,
    MEMORY_METADATA_PRESENT_FIELD,
    _MEMCORE_OPEN_TURN_GUARD,
    MemcoreTurnPipelineMixin,
)
from .engine_services.tool_execution import (
    MEDIA_PRESET_ROUTING,
    ToolExecutionMixin,
)
from .engine_services.final_response_streamer import (
    FINAL_RESPONSE_TEMPERATURE,
    FINAL_RESPONSE_RECOVERY_FEEDBACK,
    FINAL_RESPONSE_EXTERNAL_TEXT_FEEDBACK,
    FINAL_TOOL_PROTOCOL_RECOVERY_FEEDBACK,
    FINAL_RESPONSE_PLAIN_TEXT_FEEDBACK,
    FINAL_PROMPT_CACHE_LAYOUT_VERSION,
    FinalResponseStreamerMixin,
)
from .persona_system import PersonaCardService
from .prompt_blocks import CURRENT_ASSISTANT_STATE_MARKER
from .prompt_builder import PromptBuilder
from .prompt_profiles import PromptModule, PromptProfileRegistry
from .plugin_api import (
    AFTER_TOOL_CALL_HOOK,
    BEFORE_TOOL_CALL_HOOK,
    PluginHookEnvelope,
    PluginToolCallSnapshot,
    PluginToolResultSnapshot,
)
from .plugin_text_presentation import apply_plugin_text_presentation_policy
from .plugin_event_delivery import apply_plugin_notification_output_policy
from . import final_output_engine
from .local_capability_config import load_capability_config
from .retrieval_types import RetrievalPipelineResult
from . import retrieval_engine
from .resource_manifest import ResourceManifest
from .runtime_settings import BotSettingsView
from .sticker_assets import StickerAssetService
from . import tool_orchestration_engine
from .tool_invocation import NATIVE_ANTHROPIC
from .tool_invocation import NATIVE_OPENAI
from .tool_invocation import NATIVE_REASONING_CONTENT_FIELD, NATIVE_TOOL_CALL_FIELD, NATIVE_TOOL_CALLS_FIELD
from services import provider_continuation as continuation
from .tool_invocation import TOOL_MODEL_ARGUMENTS_FIELD, TOOL_MODEL_NAME_FIELD
from .tool_invocation import TOOL_INVOCATION_ID_FIELD
from .tool_invocation import (
    TOOL_CAPABILITY_SELECTION_FIELD,
    TOOL_EXECUTION_RECEIPT_FIELD,
    TOOL_EXECUTION_RECEIPTS_FIELD,
)
from .tool_invocation import TOOL_SOURCE_FIELD
from .tool_runtime import (
    AdapterCapabilityToolHandler,
    BaseToolHandler,
    BrowserPageToolHandler,
    ClearAttachmentFocusToolHandler,
    BrowseMemoryToolHandler,
    DesktopSatelliteToolHandler,
    FetchMediaFromUrlToolHandler,
    InspectAttachmentToolHandler,
    InspectGeneratedFileToolHandler,
    InspectMediaInfoToolHandler,
    ListWorkspaceToolHandler,
    LoadCharacterContextToolHandler,
    LoadMaterialToolHandler,
    ManageGeneratedFileToolHandler,
    OpenBrowserToolHandler,
    OpenMusicSearchToolHandler,
    ReadAttachmentSectionToolHandler,
    OpenMemoryToolHandler,
    ReadMemoryTimelineToolHandler,
    ReadWorkspaceToolHandler,
    RegisterWorkspaceItemsToolHandler,
    RetrieveMemoryToolHandler,
    RetryAttachmentToolHandler,
    SendFileToolHandler,
    SendStickerToolHandler,
    ToolExecutionContext,
    ToolExecutionResult,
    WebSearchToolHandler,
)
from . import visual_context_engine
from .vision_service import VisionObservationService
from .store import MemoryStore, normalize_character_pack_id
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


# Same-turn model-decision retry feedback. The retry request must reuse
# the original user message, current images, MemCore timeline, completed
# tool_call/tool_result and the same stable system prefix and cache key; the
# only addition is this host feedback appended once at the tail. It is
# request-scoped (never written into long-term history) and clearly marked as
# host feedback, not a user message. The canonical text (including the tool
# protocol and last-resort notes) lives in
# engine_services/final_response_constants.py and is imported above; a second
# copy here silently drifts from the text the policy actually appends.
# Wire text that carries any JSON structure (braces or a quoted JSON key)
# is treated as (possibly damaged) JSON, never as complete plain text.
_JSON_FRAGMENT_RE = re.compile(r'[{}]|"[A-Za-z_][A-Za-z0-9_]*"\s*:')
_TEXT_TOOL_PROTOCOL_RE = re.compile(
    r"(?is)(?:DSML.{0,160}(?:tool_calls?|invoke|parameter)|"
    r"(?:tool_calls?|invoke|parameter).{0,160}DSML)"
)


class _ContextBoundGenerator:
    """Advance one generator inside the same Context for its whole lifetime.

    Starlette may iterate a synchronous response generator from different
    copied AnyIO contexts. ContextVar tokens cannot be reset from a different
    Context, and turn-scope state would otherwise disappear between yields.
    """

    def __init__(self, generator: Generator[dict[str, Any], None, None]) -> None:
        self._generator = generator
        self._context = copy_context()
        self._closed = False

    def __iter__(self) -> _ContextBoundGenerator:
        return self

    def __next__(self) -> dict[str, Any]:
        if self._closed:
            raise StopIteration
        try:
            return self._context.run(next, self._generator)
        except StopIteration:
            self._closed = True
            raise

    def send(self, value: None) -> dict[str, Any]:
        if self._closed:
            raise StopIteration
        try:
            return self._context.run(self._generator.send, value)
        except StopIteration:
            self._closed = True
            raise

    def throw(self, *args: Any) -> dict[str, Any]:
        if self._closed:
            raise StopIteration
        try:
            return self._context.run(self._generator.throw, *args)
        except StopIteration:
            self._closed = True
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._context.run(self._generator.close)


logger = logging.getLogger("akane.engine")

# Bump only when the stable final-request layout changes incompatibly. Keeping
# the version inside the routing digest prevents a provider cache bucket built
# from an older prefix layout from shadowing a newly stabilized conversation.
FINAL_PROMPT_CACHE_LAYOUT_VERSION = "responses-unified-timeline-v3"



def resolve_engine_workspace_root(
    *,
    instance_context: InstanceContext,
    runtime_layout: InstanceRuntimeLayout | None,
    configured_root: str,
) -> str | Path:
    configured = str(configured_root or "").strip()
    if runtime_layout is None or instance_context.is_compatibility_default:
        return configured
    if configured:
        return require_instance_owned_path(
            runtime_layout,
            configured,
            reason="workspace_path_outside_instance_root",
        )
    return runtime_layout.workspace_dir


def resolve_engine_memcore_storage_path(
    *,
    instance_context: InstanceContext,
    runtime_layout: InstanceRuntimeLayout | None,
    configured_path: str,
    engine_dir: Path,
) -> Path:
    configured = str(configured_path or "").strip()
    if runtime_layout is not None and not instance_context.is_compatibility_default:
        if configured:
            return require_instance_owned_path(
                runtime_layout,
                configured,
                reason="memcore_path_outside_instance_root",
            )
        return Path(engine_dir) / "memcore_v01.db"
    return Path(configured) if configured else Path(engine_dir) / "memcore_v01.db"


class AkaneMemoryEngine(
    TurnTransportMixin,
    FinalResponseStreamerMixin,
    ToolExecutionMixin,
    MemcoreTurnPipelineMixin,
    DesktopCareAndPetMixin,
    OutputFormattingMixin,
    PromptContextMixin,
    MediaVisionAndRoutingMixin,
    ServiceFactoryMixin,
):
    def __init__(
        self,
        base_dir: Path,
        resource_manifest: ResourceManifest | None = None,
        desktop_pet_character_resources: Any = None,
        instance_context: InstanceContext | None = None,
        runtime_layout: InstanceRuntimeLayout | None = None,
        plugin_capability_source: Any = None,
        extension_management_service: Any = None,
        stable_system_blocks_provider: Callable[[], tuple[str, ...]] | None = None,
        qq_channel_config: QQChannelRuntimeConfig | None = None,
        capability_offer_source: Any = None,
        settings: BotSettingsView | None = None,
        user_assets_public_prefix: str = "/user-assets",
    ):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.instance_context = instance_context or build_local_default_instance_context()
        self.runtime_layout = runtime_layout
        self.settings = settings or BotSettingsView.from_config(config)
        artifact_data_root = runtime_layout.data_root if runtime_layout is not None else self.base_dir.resolve().parent
        self.artifact_broker = ArtifactBroker(
            instance_id=self.instance_context.instance_id,
            data_root=artifact_data_root,
        )
        self.capability_config_base_dir = (
            runtime_layout.users_data_dir
            if runtime_layout is not None
            else Path(getattr(config, "DATA_DIR", "users_data") or "users_data")
        )
        self.logs_dir = (
            runtime_layout.logs_dir
            if runtime_layout is not None
            else Path(getattr(config, "LOG_DIR", "logs") or "logs")
        )
        configured_workspace = str(getattr(config, "AKANE_WORKSPACE_ROOT", "") or "").strip()
        self.workspace_root = resolve_engine_workspace_root(
            instance_context=self.instance_context,
            runtime_layout=runtime_layout,
            configured_root=configured_workspace,
        )
        configured_memcore = str(getattr(config, "MEMCORE_STORAGE_PATH", "") or "").strip()
        self.memcore_storage_path = resolve_engine_memcore_storage_path(
            instance_context=self.instance_context,
            runtime_layout=runtime_layout,
            configured_path=configured_memcore,
            engine_dir=self.base_dir,
        )
        self.plugin_capability_source = plugin_capability_source
        self.extension_management_service = extension_management_service
        self.stable_system_blocks_provider = stable_system_blocks_provider
        self.plugin_hook_broker: Any = None
        self.qq_channel_config = qq_channel_config
        self.capability_offer_source = capability_offer_source
        self.resource_manifest = resource_manifest
        self.desktop_pet_character_resources = desktop_pet_character_resources
        self.care_module = CareModulePort.from_feature(
            enabled=bool(self.instance_context.features.care),
            storage_path=self.base_dir / "care_runtime.json",
            reset_baseline_on_start=not self.instance_context.is_compatibility_default,
            known_conversations=lambda: self.store.list_care_conversation_origins(
                master_qq=getattr(getattr(self, "owner_private_policy", None), "master_qq", "")),
        )
        self.store = MemoryStore(self.base_dir)
        self.embedding_provider = self._build_embedding_provider()
        self.vector_store = None
        self.llm = LLMRuntime(
            log_dir=self.logs_dir,
            instance_id=self.instance_context.instance_id,
            settings=self.settings,
            config_module=config,
        )
        self.memcore_manager = self._build_memcore_manager()
        self.gift_service = GiftSystemService(
            self.base_dir / "user_assets",
            store=self.store,
            llm=self.llm,
            public_prefix=user_assets_public_prefix,
        )
        self.background_tasks = BackgroundTaskRunner(
            {
                "attachment": int(getattr(config, "BACKGROUND_ATTACHMENT_WORKERS", 3) or 3),
                "timeline": int(getattr(config, "BACKGROUND_TIMELINE_WORKERS", 1) or 1),
                "host-jobs": int(getattr(config, "BACKGROUND_HOST_JOB_WORKERS", 2) or 2),
                "subagents": int(getattr(config, "BACKGROUND_HOST_JOB_WORKERS", 2) or 2),
            },
            default_workers=int(getattr(config, "BACKGROUND_DEFAULT_WORKERS", 1) or 1),
        )
        self.memory_timeline_service = None
        self.workspace_file_service = WorkspaceFileService(
            root_dir=self.workspace_root,
            store=self.store,
            max_read_bytes=int(
                getattr(config, "AKANE_WORKSPACE_MAX_READ_BYTES", 64 * 1024 * 1024) or (64 * 1024 * 1024)
            ),
        )
        attachment_workspace_dir = self.workspace_file_service.layer_dir("Inbox")
        generated_workspace_dir = self.workspace_file_service.layer_dir("Outputs")
        self.attachment_inbox_service = AttachmentInboxService(
            store=self.store,
            base_dir=attachment_workspace_dir,
            legacy_base_dirs=[self.base_dir / "attachment_inbox_files"],
            workspace_uri_resolver=self.workspace_file_service.resolve_file_uri,
            material_trace_recorder=self._record_attachment_material_trace,
        )
        if self.settings.vision_enabled:
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
        self.local_media_executor = self._create_local_media_executor()
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
            vocal_preparer=self._prepare_timeline_vocals,
            transcriber=self._prepare_timeline_transcript,
        )
        self.gift_assets = self.gift_service
        sticker_assets_dir = (
            Path(getattr(resource_manifest, "assets_dir"))
            if resource_manifest is not None and getattr(resource_manifest, "assets_dir", None)
            else Path(__file__).resolve().parent.parent / "web" / "assets"
        )
        self.sticker_assets = StickerAssetService(assets_dir=sticker_assets_dir)
        if self.settings.vision_enabled:
            self.vision_service: VisionObservationService | None = VisionObservationService(
                self.base_dir / "vision_cache",
                store=self.store,
                resource_manifest=self.resource_manifest,
                gift_assets_dir=self.base_dir / "user_assets",
                gift_assets_public_prefix=user_assets_public_prefix,
                on_observation_ready=(
                    self.vision_observation_router.handle if self.vision_observation_router is not None else None
                ),
                settings=self.settings,
                config_module=config,
            )
        else:
            self.vision_service = None
        self.attachment_ingest_service = AttachmentIngestService(
            base_dir=attachment_workspace_dir,
            store=self.store,
            attachment_service=self.attachment_inbox_service,
            vision_service=self.vision_service,
            background_tasks=self.background_tasks,
            legacy_base_dirs=[self.base_dir / "attachment_inbox_files"],
            ensure_storage_ready=self.workspace_file_service.ensure_layout,
            workspace_uri_resolver=self.workspace_file_service.resolve_file_uri,
            qq_channel_config=self.qq_channel_config,
        )
        self.prompt_builder = PromptBuilder(
            PERSONA,
            stable_system_blocks_provider=self.stable_system_blocks_provider,
        )
        self.mode_profile_registry = ModeProfileRegistry()
        self.prompt_profile_registry = PromptProfileRegistry()
        self.output_adapters = OutputAdapterRegistry()
        from .mcp_host_manager import McpHostManager, McpManagementService

        self.mcp_host_manager = McpHostManager()
        self.mcp_management_service = McpManagementService(
            base_dir=self.capability_config_base_dir,
            manager=self.mcp_host_manager,
        )
        self.tool_handlers = self._build_tool_handlers()
        # Server-local offer index for all concrete in-process handlers. Static
        # handlers are always ready; handlers with capability_status() are probed.
        _server_offer_index = ServerLocalOfferIndex()
        _server_offer_index.replace_handlers(self.tool_handlers)
        self.capability_registry = CapabilityRegistry(
            offer_source=capability_offer_source,
            server_offer_index=_server_offer_index,
        )
        self.executor_broker = ExecutorBroker(capability_offer_source)
        self._embedding_reindex_lock = threading.RLock()
        self._embedding_reindex_stop = threading.Event()
        self._embedding_reindex_thread: threading.Thread | None = None
        self._close_lock = threading.RLock()
        self._closed = False
        self._close_status: dict[str, Any] | None = None
        self._embedding_reindex_status = {
            "state": "disabled" if self._memcore_owns_legacy_vector_index() or self.vector_store is None else "idle",
            "processed": 0,
            "total": 0,
            "started_at": 0.0,
            "finished_at": 0.0,
            "error": "memcore_owns_legacy_vector_index" if self._memcore_owns_legacy_vector_index() else "",
            "collection_name": str(getattr(self.vector_store, "collection_name", "")),
        }
        self._maybe_start_embedding_reindex()

    def reset(self) -> None:
        self.store.reset()
        if hasattr(self, "vector_store") and self.vector_store is not None:
            self.vector_store.reset()
        self.gift_service.reset()
        if self.vision_service is not None:
            self.vision_service.reset()

    def reload_model_services(
        self,
        *,
        settings: BotSettingsView | None = None,
    ) -> dict[str, Any]:
        self.settings = settings or BotSettingsView.from_config(config)
        result: dict[str, Any] = {
            "llm": self.llm.reload_from_config(settings=self.settings),
            "vision": {"status": "disabled"},
        }
        if self.vision_service is not None:
            result["vision"] = self.vision_service.reload_client(settings=self.settings)
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


    def request_shutdown(self) -> None:
        """Signal cooperative workers before bounded shutdown joins begin."""

        self._embedding_reindex_stop.set()
        subagents = getattr(self, "host_subagent_jobs", None)
        if subagents is not None:
            subagents.request_shutdown()
        execution_provider = getattr(self, "execution_provider", None)
        request_execution_shutdown = getattr(execution_provider, "request_shutdown", None)
        if callable(request_execution_shutdown):
            try:
                request_execution_shutdown()
            except Exception:
                logger.warning("execution provider shutdown signal failed")
        memcore_manager = getattr(self, "memcore_manager", None)
        request_shutdown = getattr(memcore_manager, "request_shutdown", None)
        if callable(request_shutdown):
            request_shutdown()

    def close(self) -> dict[str, Any]:
        """Stop all known writers before the process can be replaced/migrated."""

        with self._close_lock:
            if self._closed:
                return dict(
                    self._close_status
                    or {
                        "status": "degraded",
                        "reason": "writer_shutdown_in_progress",
                        "failures": ["shutdown_in_progress"],
                    }
                )
            self._closed = True
        self.request_shutdown()

        failures: list[str] = []
        deadline = time.monotonic() + 20.0

        def remaining(*, cap: float | None = None) -> float:
            value = max(0.0, deadline - time.monotonic())
            return min(value, cap) if cap is not None else value

        browser_handler = (getattr(self, "tool_handlers", {}) or {}).get("browser_page")
        browser_close = getattr(browser_handler, "close", None)
        if callable(browser_close):
            try:
                if browser_close(timeout=remaining(cap=5.0)) is False:
                    failures.append("browser_page_close_failed")
            except Exception:
                failures.append("browser_page_close_failed")

        mcp_host_manager = getattr(self, "mcp_host_manager", None)
        if mcp_host_manager is not None:
            try:
                if not mcp_host_manager.close(timeout=remaining(cap=5.0)):
                    failures.append("mcp_host_manager_timeout")
            except Exception:
                failures.append("mcp_host_manager_close_failed")

        background_tasks = getattr(self, "background_tasks", None)
        if background_tasks is not None:
            try:
                if not background_tasks.close(timeout=remaining(cap=5.0)):
                    failures.append("background_tasks_timeout")
            except Exception:
                failures.append("background_tasks_close_failed")

        for name in ("vision_service",):
            service = getattr(self, name, None)
            close = getattr(service, "close", None)
            if callable(close):
                try:
                    if close(timeout=remaining(cap=5.0)) is False:
                        failures.append(f"{name}_timeout")
                except Exception:
                    failures.append(f"{name}_close_failed")

        thread = getattr(self, "_embedding_reindex_thread", None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=remaining(cap=5.0))
        if thread is not None and thread.is_alive():
            failures.append("embedding_reindex_timeout")

        memcore_manager = getattr(self, "memcore_manager", None)
        if memcore_manager is not None:
            try:
                if not memcore_manager.close(timeout=remaining()):
                    failures.append("memcore_close_deferred")
            except Exception:
                failures.append("memcore_close_failed")
        vector_store = getattr(self, "vector_store", None)
        if vector_store is not None and "embedding_reindex_timeout" not in failures:
            try:
                vector_store.close()
            except Exception:
                failures.append("vector_store_close_failed")
        # The shared model belongs to the engine, not any one memory namespace.
        # Keep it alive if a writer or background worker has not drained yet.
        if not failures:
            for client in self.__dict__.pop("_subagent_route_clients", {}).values():
                close = getattr(client, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        failures.append("subagent_client_close_failed")
            embedding_close = getattr(getattr(self, "embedding_provider", None), "close", None)
            if callable(embedding_close):
                try:
                    if embedding_close(timeout=remaining(cap=3.0)) is False:
                        failures.append("embedding_close_incomplete")
                except Exception:
                    failures.append("embedding_close_failed")
        result = {
            "status": "stopped" if not failures else "degraded",
            "reason": "closed" if not failures else "writer_shutdown_incomplete",
            "failures": failures,
        }
        with self._close_lock:
            self._close_status = dict(result)
        return result

    def _build_memcore_manager(self):
        try:
            from .memcore_integration import MemcoreManager

            manager = MemcoreManager.from_engine(self)
            status = manager.status()
            if status.get("enabled") and not status.get("available"):
                logger.warning("memcore backend requested but unavailable: %s", status.get("reason") or "unknown")
            return manager
        except Exception as exc:
            logger.warning("memcore manager disabled during setup: %s", exc)
            return None

    def migrate_memcore_legacy_path_projections(self, *, dry_run: bool = False) -> dict[str, Any]:
        """Explicit pre-traffic maintenance for legacy path-damage projections.

        Never runs on the ordinary request path.  Returns per-namespace
        migration reports and aggregate counts without payload text.
        """

        manager = getattr(self, "memcore_manager", None)
        runner = getattr(manager, "run_legacy_path_projection_migration", None)
        if not callable(runner):
            return {
                "ok": False,
                "status": "unavailable",
                "reason": "memcore_migration_unavailable",
                "dry_run": bool(dry_run),
            }
        try:
            return runner(dry_run=bool(dry_run))
        except Exception as exc:
            return {
                "ok": False,
                "status": "failed",
                "reason": f"{type(exc).__name__}",
                "dry_run": bool(dry_run),
            }




    def _runtime_settings_view(self) -> BotSettingsView:
        current = getattr(self, "settings", None)
        if isinstance(current, BotSettingsView):
            return current
        current = BotSettingsView.from_config(config)
        self.settings = current
        return current

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
        from .plugin_invocation_scope import plugin_turn_scope
        from .owner_private_session import private_turn_scope

        with plugin_turn_scope(self), private_turn_scope(self, payload):
            return self._process_turn_scoped(payload)

    def _process_turn_scoped(self, payload: dict[str, Any]) -> dict[str, Any]:
        guard_token = _MEMCORE_OPEN_TURN_GUARD.set({})
        exit_reason = "turn_scope_exited_open"
        try:
            generator = self._run_turn_core(payload, mode="sync")
            while True:
                try:
                    event = next(generator)
                except StopIteration as stop:
                    return stop.value
                generator.close()
                raise RuntimeError("turn_sync_path_emitted_stream_event")
        except ModelRequestCancelled:
            exit_reason = "turn_scope_revoked"
            return self._cancelled_model_turn_output()
        except BaseException:
            exit_reason = "turn_processing_exception"
            raise
        finally:
            self._abort_open_memcore_turn_guard(reason=exit_reason)
            _MEMCORE_OPEN_TURN_GUARD.reset(guard_token)

    @staticmethod
    def _cancelled_model_turn_output() -> dict[str, Any]:
        """Pre-generation cancellation has no completed model/tool output."""
        return {"status": "stopped", "reason": "turn_scope_revoked", "speech": "",
                "speech_segments": [], "tool_events": [], "npc_turns": [],
                "execution_cancellations": []}

    def _run_turn_core(
        self,
        payload: dict[str, Any],
        *,
        mode: str,
        _precommitted_memcore_turn: dict[str, str] | None = None,
        _voice_playback_context: str = "",
    ) -> Generator[dict[str, Any], None, dict[str, Any]]:
        """Single authoritative turn mainline shared by sync and stream entries.

        ``process_turn`` drains this generator and returns its value; the
        streaming entry forwards every yielded event and returns the same value.
        Only streaming mode emits user-visible events; sync mode must never
        yield (the sync drain raises if that invariant breaks).
        """
        turn = prepare_turn(
            self, payload, mode=mode,
            _precommitted_memcore_turn=_precommitted_memcore_turn,
            _voice_playback_context=_voice_playback_context,
        )
        if isinstance(turn, DeferredTurn):
            if str(mode or "").strip() == "stream":
                yield {"type": "final", "payload": turn.output}
            return turn.output
        outcome = yield from run_turn_rounds(self, turn)
        if outcome.stopped_output is not None:
            return outcome.stopped_output
        return (yield from complete_turn(self, turn, outcome.state))


    def process_voice_turn_stream(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        source_id: str,
        memcore_turn_id: str,
        voice_turn_id: str,
        message: str,
        timestamp: int,
        playback_context: str = "",
        conversation_ref: str = "",
    ) -> Generator[dict[str, Any], None, None]:
        """Run the normal Thinking Agent over an already committed voice turn."""

        normalized_source_id = str(source_id or "").strip()
        normalized_turn_id = str(memcore_turn_id or "").strip()
        normalized_voice_turn_id = str(voice_turn_id or "").strip()
        normalized_message = str(message or "").strip()
        if not normalized_source_id or not normalized_turn_id or not normalized_voice_turn_id or not normalized_message:
            raise ValueError("voice_precommitted_turn_invalid")
        return self.process_turn_stream(
            {
                "message": normalized_message,
                "user_id": str(session_id or ""),
                "real_user_id": str(profile_user_id or ""),
                "character_pack_id": str(character_pack_id or ""),
                "timestamp": int(timestamp or time.time()),
                "client_mode": "desktop_pet",
                "_memory_desktop_ref": conversation_ref,
                "transient_user_message": True,
                "transient_assistant_message": True,
                "client_capabilities": [*DESKTOP_PET_DEFAULT_CAPABILITIES, "speech_units_v1"],
            },
            _precommitted_memcore_turn={
                "source_id": normalized_source_id,
                "turn_id": normalized_turn_id,
                "voice_turn_id": normalized_voice_turn_id,
            },
            **({"_voice_playback_context": playback_context} if playback_context else {}),
        )

    def process_voice_candidate_stream(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        voice_turn_id: str,
        message: str,
        timestamp: int,
        playback_context: str = "",
        conversation_ref: str = "",
    ) -> Generator[dict[str, Any], None, None]:
        """Generate a non-persistent, tool-free candidate from provisional ASR text."""

        normalized_voice_turn_id = str(voice_turn_id or "").strip()
        normalized_message = str(message or "").strip()
        if not normalized_voice_turn_id or not normalized_message:
            raise ValueError("voice_candidate_turn_invalid")
        return self.process_turn_stream(
            {
                "message": normalized_message,
                "user_id": str(session_id or ""),
                "real_user_id": str(profile_user_id or ""),
                "character_pack_id": str(character_pack_id or ""),
                "timestamp": int(timestamp or time.time()),
                "client_mode": "desktop_pet",
                "_memory_desktop_ref": conversation_ref,
                "transient_user_message": True,
                "transient_assistant_message": True,
                "voice_speculative_candidate": True,
                "client_capabilities": [*DESKTOP_PET_DEFAULT_CAPABILITIES, "speech_units_v1"],
            },
            **({"_voice_playback_context": playback_context} if playback_context else {}),
        )

    def process_turn_stream(
        self,
        payload: dict[str, Any],
        *,
        _precommitted_memcore_turn: dict[str, str] | None = None,
        _voice_playback_context: str = "",
    ) -> _ContextBoundGenerator:
        return _ContextBoundGenerator(
            self._process_turn_stream_scoped(
                payload,
                _precommitted_memcore_turn=_precommitted_memcore_turn,
                **({"_voice_playback_context": _voice_playback_context} if _voice_playback_context else {}),
            )
        )

    def _process_turn_stream_scoped(
        self,
        payload: dict[str, Any],
        **kwargs,
    ) -> Generator[dict[str, Any], None, None]:
        from .owner_private_session import private_turn_scope

        with private_turn_scope(self, payload):
            yield from self._process_turn_stream_inner(payload, **kwargs)

    def _process_turn_stream_inner(
        self,
        payload: dict[str, Any],
        *,
        _precommitted_memcore_turn: dict[str, str] | None = None,
        _voice_playback_context: str = "",
    ) -> Generator[dict[str, Any], None, None]:
        guard_token = _MEMCORE_OPEN_TURN_GUARD.set({})
        exit_reason = "turn_stream_scope_exited_open"
        try:
            from .plugin_invocation_scope import plugin_turn_scope

            with plugin_turn_scope(self):
                core = self._run_turn_core(
                    payload,
                    mode="stream",
                    _precommitted_memcore_turn=_precommitted_memcore_turn,
                    **({"_voice_playback_context": _voice_playback_context} if _voice_playback_context else {}),
                )
                from .owner_private_session import current_private_scope
                private = current_private_scope(self)
                try:
                    for event in core:
                        if private and private.proactive_guard:
                            private.proactive_guard.before_event(event)
                        yield event
                finally:
                    core.close()
        except ModelRequestCancelled:
            exit_reason = "turn_scope_revoked"
            output = self._cancelled_model_turn_output()
            yield {"type": "turn_stopped", "reason": output["reason"], "payload": output}
        except GeneratorExit:
            raise
        except BaseException:
            exit_reason = "turn_stream_processing_exception"
            raise
        finally:
            self._abort_open_memcore_turn_guard(reason=exit_reason)
            _MEMCORE_OPEN_TURN_GUARD.reset(guard_token)
