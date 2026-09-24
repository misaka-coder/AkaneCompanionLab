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
from .memory_compaction_service import MemoryCompactionService
from .memory_rendering import render_semantic_summary_timeline, render_summary_timeline
from .memory_timeline import MemoryTimelineService
from .client_protocol import ClientCapability, ClientMode, ClientProtocolContext
from .execution_specs import EXEC_TOOL_SPEC_BY_ID
from .care_runtime import CareModulePort, normalize_desktop_care_config
from .desktop_pet_character_resources import load_character_care_config
from .desktop_music_timeline import DesktopMusicTimelineService
from .deployment_security import QQChannelRuntimeConfig
from . import desktop_context_engine
from .mode_profiles import ModeProfileRegistry
from .output_adapters import OutputAdapterRegistry
from .persona_config import PERSONA
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
from .retrieval_service import RetrievalService
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


_MEMCORE_OPEN_TURN_GUARD: ContextVar[dict[str, str] | None] = ContextVar(
    "akane_memcore_open_turn_guard",
    default=None,
)
FINAL_RESPONSE_TEMPERATURE = 0.8

# Same-turn model-decision retry feedback. The retry request must reuse
# the original user message, current images, MemCore timeline, completed
# tool_call/tool_result and the same stable system prefix and cache key; the
# only addition is this host feedback appended once at the tail. It is
# request-scoped (never written into long-term history) and clearly marked as
# host feedback, not a user message.
FINAL_RESPONSE_RECOVERY_FEEDBACK = (
    "【宿主反馈】\n"
    "上一条输出没有形成有效的工具调用或可交付回复。\n"
    "请基于同一用户请求和已经存在的工具结果继续：如果仍需操作，请发出合法工具调用；"
    "如果任务已完成，请输出符合协议的交付回复。\n"
    "不要重复已完成的工具，不要讨论这次格式错误。"
)

FINAL_RESPONSE_EXTERNAL_TEXT_FEEDBACK = (
    "【宿主反馈】\n"
    "上一条回复在 JSON 对象外还有用户可见文字；只提取 speech 会丢失那部分内容，因此本次没有交付。\n"
    "请保留完整答案，但只输出一个 JSON 对象，并把所有要发给用户的文字都放进 speech。"
    "不要重复已完成的工具，不要讨论这次格式修正。"
)

# Some OpenAI-compatible gateways serialize an intended tool call into their
# private text protocol instead of returning a structured tool_calls field.
# That text is neither user-facing speech nor an executable decision. Keep the
# correction request-scoped so the stable prompt/cache prefix does not change.
FINAL_TOOL_PROTOCOL_RECOVERY_FEEDBACK = (
    "【宿主反馈】\n"
    "上一条输出包含文本化的工具协议，没有形成可执行的原生工具调用，因此不会交付给用户。\n"
    "如果仍需操作，请使用当前提供的原生工具入口发出结构化工具调用；"
    "不要把 DSML、XML 标签或工具协议写进 speech。"
    "如果任务已完成，请正常输出可交付回复。不要重复已完成的工具。"
)

# Last-resort delivery after the configured structured decisions all fail. It
# is an explicit protocol-exhaustion path, not an inferred final stage.
FINAL_RESPONSE_PLAIN_TEXT_FEEDBACK = (
    "【宿主反馈】\n"
    "连续多次输出仍没有形成有效的工具调用或可交付回复。"
    "本次请直接用纯文本向用户说明当前实际完成度、已有证据和具体阻塞；"
    "不要输出 JSON，不要调用工具，也不要把未完成事项写成已完成。"
)

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
MEMORY_ANNOTATION_STATUS_FIELD = "_memory_annotation_status"
MEMORY_METADATA_PRESENT_FIELD = "_memory_metadata_present"

MEDIA_PRESET_ROUTING = [
    "【媒体任务预设路由】",
    "- 只要原文件不处理 → send_file，不要转写/转码/净化",
    "",
    "生成与交付分别确认：以工具实际返回的交付状态为准，已发送的结果不要重复发送；仅登记的成果可按用户要求调用 send_file 精确交付，多个结果可一次批量发送。",
    "涉及大小、码率、分辨率、时长、格式兼容等具体约束时，先读取当前规格，再根据当前实际可用工具决定处理方式。",
]


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


class AkaneMemoryEngine:
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
        )
        self.store = MemoryStore(self.base_dir)
        self.embedding_provider = self._build_embedding_provider()
        self.vector_store = VectorStore(
            self.base_dir / "chroma",
            embedding_provider=self.embedding_provider,
        )
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
        if self._should_init_legacy_memory_timeline():
            self.memory_timeline_service = MemoryTimelineService(
                store=self.store,
                root_dir=self.base_dir / "memory",
                background_tasks=self.background_tasks,
            )
            self.store.set_message_write_callback(self.memory_timeline_service.handle_message_write)
            self.memory_timeline_service.schedule_existing_backfill()
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
        if self._should_eager_init_legacy_memory_services():
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
        else:
            self.retrieval_service = None
            self.compaction_service = None
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
        compaction_service = getattr(self, "compaction_service", None)
        if compaction_service is not None:
            compaction_service.reset()
        self.store.reset()
        memory_timeline_service = getattr(self, "memory_timeline_service", None)
        if memory_timeline_service is not None:
            memory_timeline_service.clear_mirror()
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

    def list_gift_assets(
        self, *, profile_user_id: str, media_kind: str = "all", limit: int = 50
    ) -> list[dict[str, Any]]:
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

        compaction_service = getattr(self, "compaction_service", None)
        if compaction_service is not None:
            try:
                compaction_service.close()
            except Exception:
                failures.append("compaction_close_failed")
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

    def _memcore_manager_if_enabled(self):
        manager = getattr(self, "memcore_manager", None)
        if manager is None or not getattr(manager, "enabled", False):
            return None
        return manager

    def _memcore_owns_compaction(self) -> bool:
        backend = str(getattr(config, "MEMORY_BACKEND", "memcore") or "memcore").strip().lower()
        if backend != "memcore":
            return False
        manager = self._memcore_manager_if_enabled()
        return manager is not None and bool(getattr(manager, "available", False))

    def _memcore_owns_legacy_vector_index(self) -> bool:
        backend = str(getattr(config, "MEMORY_BACKEND", "memcore") or "memcore").strip().lower()
        if backend != "memcore":
            return False
        manager = self._memcore_manager_if_enabled()
        return manager is not None and bool(getattr(manager, "available", False))

    def _memcore_owns_visible_memory(self) -> bool:
        return self._memcore_owns_legacy_vector_index()

    def _should_eager_init_legacy_memory_services(self) -> bool:
        return not self._memcore_owns_legacy_vector_index()

    def _should_init_legacy_memory_timeline(self) -> bool:
        return not self._memcore_owns_visible_memory()

    def _load_turn_visible_memory(
        self,
        *,
        session_id: str,
        profile_user_id: str,
        character_pack_id: str,
        user_record: dict[str, Any],
        include_transient_user_record: bool,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        if self._memcore_owns_visible_memory():
            return ([dict(user_record)] if user_record else []), [], []

        recent_raw = self.store.get_unsummarized_messages(
            session_id,
            character_pack_id=character_pack_id,
        )
        if include_transient_user_record:
            recent_raw = [*recent_raw, user_record]
        episodic_limit = max(1, int(getattr(config, "EPISODIC_VISIBLE_MAX", 10)))
        semantic_limit = max(1, int(getattr(config, "SEMANTIC_VISIBLE_LIMIT", 3)))
        recent_episodic_summaries = self.store.get_visible_episodic_summaries(
            profile_user_id,
            limit=episodic_limit,
            character_pack_id=character_pack_id,
        )
        recent_semantic_summaries = (
            self.store.get_recent_semantic_summaries(
                profile_user_id,
                limit=semantic_limit,
                character_pack_id=character_pack_id,
            )
            if bool(getattr(config, "ENABLE_SEMANTIC_MEMORY", True))
            else []
        )
        return recent_raw, recent_episodic_summaries, recent_semantic_summaries

    def _append_memcore_passive_message(
        self,
        *,
        user_record: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        actor_stable_id: str = "",
        actor_display_name: str = "",
        target_actor_id: str = "",
        target_actor_display_name: str = "",
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None:
            return {}
        try:
            return manager.append_standalone_message(
                user_record,
                role="user",
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                actor_stable_id=actor_stable_id,
                actor_display_name=actor_display_name,
                observed=True,
                target_actor_id=target_actor_id,
                target_actor_display_name=target_actor_display_name,
            )
        except Exception as exc:
            logger.warning("memcore passive message append failed: %s", exc)
            return {"ok": False, "status": "failed", "reason": str(exc)}

    def _append_memcore_standalone_assistant(
        self,
        *,
        assistant_record: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> dict[str, Any]:
        """Persist a visible reply whose triggering group message already exists."""

        manager = self._memcore_manager_if_enabled()
        if manager is None:
            return {}
        try:
            return manager.append_standalone_message(
                assistant_record,
                role="assistant",
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
        except Exception as exc:
            logger.warning("memcore standalone assistant append failed: %s", exc)
            return {"ok": False, "status": "failed", "reason": str(exc)}

    @staticmethod
    def _track_open_memcore_turn_for_guard(
        *,
        turn_id: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> None:
        if _MEMCORE_OPEN_TURN_GUARD.get() is None:
            return
        resolved_turn_id = str(turn_id or "").strip()
        if not resolved_turn_id:
            return
        _MEMCORE_OPEN_TURN_GUARD.set(
            {
                "turn_id": resolved_turn_id,
                "profile_user_id": str(profile_user_id or "").strip(),
                "session_id": str(session_id or "").strip(),
                "character_pack_id": str(character_pack_id or "").strip(),
            }
        )

    @staticmethod
    def _clear_open_memcore_turn_guard(turn_id: str) -> None:
        tracked = _MEMCORE_OPEN_TURN_GUARD.get()
        if not isinstance(tracked, dict):
            return
        if str(tracked.get("turn_id") or "").strip() != str(turn_id or "").strip():
            return
        _MEMCORE_OPEN_TURN_GUARD.set({})

    def _abort_open_memcore_turn_guard(self, *, reason: str) -> None:
        tracked = _MEMCORE_OPEN_TURN_GUARD.get()
        if not isinstance(tracked, dict):
            return
        turn_id = str(tracked.get("turn_id") or "").strip()
        if not turn_id:
            return
        # Clear first so a failure in the recovery path cannot recursively
        # retain or re-abort the same turn while unwinding the caller.
        _MEMCORE_OPEN_TURN_GUARD.set({})
        try:
            result = self._abort_memcore_input_turn(
                turn_id=turn_id,
                reason=str(reason or "turn_scope_exited_open"),
                profile_user_id=str(tracked.get("profile_user_id") or ""),
                session_id=str(tracked.get("session_id") or ""),
                character_pack_id=str(tracked.get("character_pack_id") or ""),
            )
            if not isinstance(result, dict) or not result.get("ok"):
                logger.warning(
                    "memcore guarded turn abort failed status=%s reason=%s",
                    str((result or {}).get("status") or "unknown")[:40] if isinstance(result, dict) else "invalid",
                    str((result or {}).get("reason") or "abort_failed")[:120]
                    if isinstance(result, dict)
                    else "invalid_result",
                )
        except Exception as exc:
            logger.warning("memcore guarded turn abort failed type=%s", type(exc).__name__)

    def _begin_memcore_input_turn(
        self,
        *,
        user_record: dict[str, Any],
        external_event: dict[str, Any] | None,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        actor_stable_id: str,
        actor_display_name: str,
        target_actor_id: str = "",
        target_actor_display_name: str = "",
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None:
            return {}
        try:
            result = manager.begin_input_turn(
                user_record,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                actor_stable_id=actor_stable_id,
                actor_display_name=actor_display_name,
                target_actor_id=target_actor_id,
                target_actor_display_name=target_actor_display_name,
                external_event=external_event,
            )
            self._warn_memcore_write_result("input turn open", result)
            if not isinstance(result, dict):
                return {
                    "ok": False,
                    "status": "failed",
                    "reason": "invalid_input_turn_open_result",
                    "turn_id": "",
                    "writable": False,
                }
            normalized = dict(result)
            status = str(normalized.get("status") or "").strip().lower()
            turn_id = str(normalized.get("turn_id") or "").strip()
            writable = bool(
                normalized.get("ok") and turn_id and status in {"open", "opened"} and normalized.get("writable", True)
            )
            normalized["writable"] = writable
            if not writable:
                # ``begin_turn`` is idempotent and may return the old handle for
                # an already completed/aborted stimulus.  Such a handle is
                # useful evidence, but it is not a writable current turn.
                # Never pass its id to tool/metadata writers.
                normalized["turn_id"] = ""
                if turn_id and status:
                    logger.warning(
                        "memcore input turn is not writable status=%s",
                        status[:80],
                    )
            else:
                self._track_open_memcore_turn_for_guard(
                    turn_id=turn_id,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                )
            return normalized
        except Exception as exc:
            exception_code = f"exception_{type(exc).__name__}"
            logger.warning("memcore input turn open failed reason=%s", type(exc).__name__)
            return {
                "ok": False,
                "status": "failed",
                "reason": exception_code,
                "turn_id": "",
                "writable": False,
            }

    def _begin_memcore_existing_input_turn(
        self,
        *,
        source_id: str,
        stimulus_source_ids: list[str] | None = None,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None:
            return {}
        try:
            result = manager.begin_existing_input_turn(
                source_id=source_id,
                stimulus_source_ids=stimulus_source_ids,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
            self._warn_memcore_write_result("existing input turn open", result)
            if not isinstance(result, dict):
                return {
                    "ok": False,
                    "status": "failed",
                    "reason": "invalid_existing_input_turn_open_result",
                    "turn_id": "",
                    "writable": False,
                }
            normalized = dict(result)
            status = str(normalized.get("status") or "").strip().lower()
            turn_id = str(normalized.get("turn_id") or "").strip()
            writable = bool(
                normalized.get("ok") and turn_id and status in {"open", "opened"} and normalized.get("writable", True)
            )
            normalized["writable"] = writable
            if not writable:
                normalized["turn_id"] = ""
            else:
                self._track_open_memcore_turn_for_guard(
                    turn_id=turn_id,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                )
            return normalized
        except Exception as exc:
            logger.warning("memcore existing input turn open failed reason=%s", type(exc).__name__)
            return {
                "ok": False,
                "status": "failed",
                "reason": f"exception_{type(exc).__name__}",
                "turn_id": "",
                "writable": False,
            }

    def _begin_memcore_hidden_host_turn(
        self,
        *,
        referenced_source_ids: list[str] | None = None,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None:
            return {}
        try:
            result = manager.begin_hidden_host_turn(
                referenced_source_ids=referenced_source_ids,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
            self._warn_memcore_write_result("hidden host turn open", result)
            normalized = dict(result) if isinstance(result, dict) else {}
            status = str(normalized.get("status") or "").strip().lower()
            turn_id = str(normalized.get("turn_id") or "").strip()
            writable = bool(
                normalized.get("ok") and turn_id and status in {"open", "opened"} and normalized.get("writable", True)
            )
            normalized["writable"] = writable
            if not writable:
                normalized["turn_id"] = ""
            else:
                self._track_open_memcore_turn_for_guard(
                    turn_id=turn_id,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                )
            return normalized
        except Exception as exc:
            logger.warning("memcore hidden host turn open failed reason=%s", type(exc).__name__)
            return {
                "ok": False,
                "status": "failed",
                "reason": f"exception_{type(exc).__name__}",
                "turn_id": "",
                "writable": False,
            }

    @staticmethod
    def _memcore_input_turn_failure(result: Any) -> dict[str, Any] | None:
        if not isinstance(result, dict) or not result:
            return None
        status = str(result.get("status") or "failed").strip().lower()
        writable = bool(
            result.get("writable")
            or (result.get("ok") and str(result.get("turn_id") or "").strip() and status in {"open", "opened"})
        )
        if writable:
            return None
        detail = AkaneMemoryEngine._safe_memcore_failure_code(
            result.get("reason") or status,
            fallback="input_turn_unavailable",
        )
        return {
            "status": AkaneMemoryEngine._safe_memcore_failure_code(status, fallback="failed"),
            "reason": "input_turn_not_writable",
            "detail": detail,
            "delivery_status": "model_reply_preserved",
        }

    @staticmethod
    def _safe_memcore_failure_code(value: Any, *, fallback: str) -> str:
        text = str(value or "").strip().lower()
        if re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,119}", text):
            return text
        return str(fallback or "memcore_failure")[:120]

    @staticmethod
    def _attach_nonfatal_memcore_failure(
        final_output: dict[str, Any],
        failure: dict[str, Any] | None,
    ) -> None:
        if not isinstance(failure, dict) or not failure:
            return
        if isinstance(final_output.get("_memcore_failure"), dict):
            return
        final_output["_memcore_failure"] = {
            "status": AkaneMemoryEngine._safe_memcore_failure_code(
                failure.get("status"),
                fallback="failed",
            ),
            "reason": AkaneMemoryEngine._safe_memcore_failure_code(
                failure.get("reason"),
                fallback="memcore_write_failed",
            ),
            "detail": AkaneMemoryEngine._safe_memcore_failure_code(
                failure.get("detail"),
                fallback="unavailable",
            ),
            "delivery_status": "model_reply_preserved",
        }

    def _stage_memcore_turn_metadata(
        self,
        *,
        source_id: str,
        memory_metadata: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        actor_stable_id: str = "",
        actor_display_name: str = "",
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None:
            return {}
        try:
            result = manager.stage_turn_metadata(
                source_id,
                memory_metadata,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                actor_stable_id=actor_stable_id,
                actor_display_name=actor_display_name,
            )
            self._warn_memcore_write_result("metadata staging", result)
            return result
        except Exception as exc:
            logger.warning("memcore metadata staging failed: %s", exc)
            return {"ok": False, "status": "failed", "reason": str(exc)}

    def _append_memcore_turn_intermediate(
        self,
        *,
        turn_id: str,
        assistant_record: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None or not str(turn_id or "").strip():
            return {}
        try:
            result = manager.append_turn_intermediate(
                assistant_record,
                turn_id=turn_id,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
            self._warn_memcore_write_result("intermediate append", result)
            return result
        except Exception as exc:
            logger.warning("memcore intermediate append failed: %s", exc)
            return {"ok": False, "status": "failed", "reason": str(exc)}

    def _append_memcore_turn_user_input(
        self,
        *,
        turn_id: str,
        user_record: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        actor_stable_id: str,
        actor_display_name: str,
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None or not str(turn_id or "").strip():
            return {"ok": False, "status": "unavailable", "reason": "open_memcore_turn_missing"}
        append_input = getattr(manager, "append_turn_user_input", None)
        if not callable(append_input):
            return {"ok": False, "status": "unavailable", "reason": "turn_user_input_append_unavailable"}
        try:
            result = append_input(
                user_record,
                turn_id=turn_id,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                actor_stable_id=actor_stable_id,
                actor_display_name=actor_display_name,
            )
            self._warn_memcore_write_result("turn user input append", result)
            return result if isinstance(result, dict) else {
                "ok": False,
                "status": "failed",
                "reason": "invalid_turn_user_input_append_result",
            }
        except Exception as exc:
            logger.warning("memcore turn user input append failed: %s", type(exc).__name__)
            return {"ok": False, "status": "failed", "reason": f"exception_{type(exc).__name__}"}

    def _record_turn_steering_inputs(
        self,
        *,
        steers: list[Any],
        turn_id: str,
        recent_raw_for_turn: list[dict[str, Any]],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        prepared_attachments: dict[str, Any] | None = None,
    ) -> tuple[list[str], list[str]]:
        applied_source_ids: list[str] = []
        failed_source_ids: list[str] = []
        for steer in steers:
            source_id = str(getattr(steer, "source_id", "") or "").strip()
            content = str(getattr(steer, "content", "") or "").strip()
            timestamp = int(getattr(steer, "timestamp", 0) or time.time())
            if not source_id or not content:
                continue
            attachments = (prepared_attachments or {}).get(source_id) or {}
            if attachments.get("context"):
                content = self._merge_extra_user_context(content, attachments["context"])
            steer_record = self.store.add_message(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                role="user",
                content=content,
                timestamp=timestamp,
                semantic_tags=extract_semantic_tags(content),
                source_id=source_id,
                memory_metadata={"current_attachment_ids": attachments["attachment_ids"]}
                if attachments else None,
            )
            self._upsert_raw_record(steer_record)
            recent_raw_for_turn.append(steer_record)
            append_result = self._append_memcore_turn_user_input(
                turn_id=turn_id,
                user_record=steer_record,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                actor_stable_id=str(getattr(steer, "actor_id", "") or ""),
                actor_display_name=str(getattr(steer, "actor_display_name", "") or ""),
            )
            if append_result.get("ok"):
                applied_source_ids.append(source_id)
            else:
                failed_source_ids.append(source_id)
        return applied_source_ids, failed_source_ids

    def _settle_turn_steering_receipts(
        self,
        *,
        steers: list[Any],
        applied_source_ids: list[str],
        failed_source_ids: list[str],
    ) -> dict[str, int]:
        """Finish durable inbox claims only after the turn accepted each steer."""

        observer = getattr(self, "plugin_steering_observer", None)
        if callable(observer):
            try:
                observer(applied_source_ids=applied_source_ids, failed_source_ids=failed_source_ids)
            except Exception as exc:
                logger.warning("plugin steering receipt update failed: %s", exc.__class__.__name__)
        inbox_store = getattr(self, "session_inbox_store", None)
        if inbox_store is None:
            return {"committed": 0, "failed": 0, "settlement_failed": 0}
        applied = set(applied_source_ids)
        failed = set(failed_source_ids)
        counts = {"committed": 0, "failed": 0, "settlement_failed": 0}
        for steer in steers:
            source_id = str(getattr(steer, "source_id", "") or "").strip()
            item_id = str(getattr(steer, "receipt_item_id", "") or "").strip()
            claim_token = str(getattr(steer, "receipt_claim_token", "") or "").strip()
            if not item_id or not claim_token:
                continue
            try:
                if source_id in applied:
                    result = inbox_store.commit(item_id, claim_token=claim_token)
                    outcome = "committed"
                elif source_id in failed:
                    result = inbox_store.fail(
                        item_id,
                        claim_token=claim_token,
                        error="memcore_turn_user_input_append_failed",
                        retryable=False,
                    )
                    outcome = "failed"
                else:
                    continue
            except Exception as exc:
                counts["settlement_failed"] += 1
                logger.warning(
                    "turn steer inbox settlement raised: item=%s source=%s reason=%s",
                    item_id,
                    source_id,
                    exc.__class__.__name__,
                )
                continue
            if isinstance(result, dict) and result.get("ok"):
                counts[outcome] += 1
            else:
                counts["settlement_failed"] += 1
                logger.warning(
                    "turn steer inbox settlement failed: item=%s source=%s status=%s",
                    item_id,
                    source_id,
                    str(result.get("status") if isinstance(result, dict) else "invalid_result"),
                )
        return counts

    def _chat_provider_protocol_for_memcore(
        self,
        *,
        chat_model_override: str = "",
        execution_target: Any = None,
    ) -> str:
        if execution_target is not None:
            protocol = str(getattr(execution_target, "protocol", "") or "").strip()
            if protocol:
                return protocol
        runtime = getattr(self, "llm", None)
        protocol_getter = getattr(runtime, "chat_provider_protocol", None)
        if not callable(protocol_getter):
            return ""
        try:
            return str(protocol_getter(chat_model_override=chat_model_override) or "").strip()
        except Exception as exc:
            logger.debug("memcore provider protocol resolution failed: %s", exc)
            return ""

    def _complete_memcore_input_turn(
        self,
        *,
        turn_id: str,
        assistant_record: dict[str, Any],
        memory_metadata: dict[str, Any] | None,
        provider_output_raw: str,
        chat_model_override: str = "",
        execution_target: Any = None,
        annotation_status: str = "",
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        append_final: bool = True,
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None or not str(turn_id or "").strip():
            return {}
        try:
            provider_profile = ""
            provider_projection: dict[str, Any] | None = None
            if str(provider_output_raw or "") or not append_final:
                provider_profile = self._chat_provider_protocol_for_memcore(
                    chat_model_override=chat_model_override,
                    execution_target=execution_target,
                )
                if provider_profile and str(provider_output_raw or ""):
                    provider_projection = {
                        "role": "assistant",
                        "content": str(provider_output_raw),
                    }
            result = manager.complete_input_turn(
                turn_id=turn_id,
                assistant_record=assistant_record,
                memory_metadata=memory_metadata,
                provider_output_raw=str(provider_output_raw or ""),
                provider_profile=provider_profile,
                provider_projection=provider_projection,
                annotation_status=(
                    str(annotation_status or "").strip()
                    or ("accepted_model" if isinstance(memory_metadata, dict) else "missing")
                ),
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                append_final=append_final,
            )
            self._warn_memcore_write_result("input turn completion", result)
            return result
        except Exception as exc:
            logger.warning("memcore input turn completion failed: %s", exc)
            return {"ok": False, "status": "failed", "reason": str(exc)}

    def _finalize_memcore_input_turn_for_delivery(
        self,
        *,
        final_output: dict[str, Any],
        turn_id: str,
        assistant_record: dict[str, Any],
        memory_metadata: dict[str, Any] | None,
        provider_output_raw: str,
        chat_model_override: str,
        execution_target: Any = None,
        annotation_status: str,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> bool:
        """Close a MemCore turn without discarding an already generated reply.

        ``complete_turn`` is idempotent, so one immediate retry is safe for a
        transient store failure.  If both attempts fail, close the open turn
        through the explicit abort path and attach a bounded, path-free status
        to the real model result.  A successful abort becomes a terminal turn
        and schedules the same provider-aware compaction path.
        """

        if not str(turn_id or "").strip():
            return True
        completion: dict[str, Any] = {}
        for _attempt in range(2):
            completion = self._complete_memcore_input_turn(
                turn_id=turn_id,
                assistant_record=assistant_record,
                memory_metadata=memory_metadata,
                provider_output_raw="" if final_output.get("_notification_suppressed") else provider_output_raw,
                chat_model_override=chat_model_override,
                execution_target=execution_target,
                annotation_status=annotation_status,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                append_final=not bool(
                    final_output.get("_tool_finished_turn") or final_output.get("_notification_suppressed")
                ),
            )
            if not completion or bool(completion.get("ok")):
                if completion and completion.get("ok"):
                    self._clear_open_memcore_turn_guard(turn_id)
                    if final_output.get("_notification_suppressed"):
                        try:
                            receipt = self.record_plugin_timeline_event(
                                {
                                    "source_id": f"{turn_id}:notification_suppressed",
                                    "user_id": session_id,
                                    "real_user_id": profile_user_id,
                                    "character_pack_id": character_pack_id,
                                    "timestamp": int(time.time()),
                                    "event": {
                                        "event_type": "notification.delivery",
                                        "source": "host",
                                        "fields": {
                                            "status": "suppressed",
                                            "reason": str(final_output["_notification_suppressed"]),
                                            "scope": "final_text_and_automatic_emotion",
                                            "text_sent": "false",
                                        },
                                    },
                                }
                            )
                        except Exception:
                            receipt = {"ok": False}
                        if not receipt.get("ok"):
                            self._attach_nonfatal_memcore_failure(
                                final_output,
                                {"status": "failed", "reason": "notification_suppression_receipt_failed"},
                            )
                return True
        aborted = self._abort_memcore_input_turn(
            turn_id=turn_id,
            reason="input_turn_completion_failed",
            chat_model_override=chat_model_override,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        final_output["_memcore_failure"] = {
            "status": str(completion.get("status") or "failed")[:40],
            "reason": "input_turn_completion_failed",
            "recovery_status": "aborted" if bool((aborted or {}).get("ok")) else "abort_failed",
            "delivery_status": "model_reply_preserved",
        }
        return False

    @staticmethod
    def _memory_metadata_has_signal(metadata: Any) -> bool:
        return memcore_metadata_has_signal(metadata)

    def _attach_memory_annotation_truth(
        self,
        output: dict[str, Any],
        *,
        result: Any,
        raw_result: Any,
    ) -> None:
        allowed = {"accepted_model", "accepted_host", "missing", "invalid", "plain", "fallback", "rejected"}
        status = str(getattr(result, "metadata_status", "") or "").strip()
        present_value = getattr(result, "metadata_present", None)
        if status not in allowed:
            raw_payload = raw_result if isinstance(raw_result, dict) else {}
            present_value = "memory_metadata" in raw_payload
            if not present_value:
                status = "missing"
            elif isinstance(raw_payload.get("memory_metadata"), dict):
                status = "accepted_model"
            else:
                status = "invalid"
        if status in {"missing", "invalid", "plain", "fallback"} and self._memory_metadata_has_signal(
            output.get("memory_metadata")
        ):
            status = "accepted_host"
        output[MEMORY_ANNOTATION_STATUS_FIELD] = status
        output[MEMORY_METADATA_PRESENT_FIELD] = bool(present_value)

    def _pop_memory_annotation_status(self, output: dict[str, Any]) -> str:
        status = str(output.pop(MEMORY_ANNOTATION_STATUS_FIELD, "") or "").strip()
        output.pop(MEMORY_METADATA_PRESENT_FIELD, None)
        if status in {"accepted_model", "accepted_host", "missing", "invalid", "plain", "fallback", "rejected"}:
            return status
        return "accepted_host" if self._memory_metadata_has_signal(output.get("memory_metadata")) else "missing"

    def _abort_memcore_input_turn(
        self,
        *,
        turn_id: str,
        reason: str,
        chat_model_override: str = "",
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None or not str(turn_id or "").strip():
            return {}
        try:
            result = manager.abort_input_turn(
                turn_id=turn_id,
                reason=reason,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
            self._warn_memcore_write_result("input turn abort", result)
            if isinstance(result, dict) and bool(result.get("ok")):
                self._clear_open_memcore_turn_guard(turn_id)
                compaction = self._schedule_memcore_compaction(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                    chat_model_override=chat_model_override,
                )
                if compaction:
                    result = {**result, "compaction": compaction}
            return result
        except Exception as exc:
            logger.warning("memcore input turn abort failed: %s", exc)
            return {"ok": False, "status": "failed", "reason": str(exc)}

    @staticmethod
    def _warn_memcore_write_result(operation: str, result: Any) -> None:
        if not isinstance(result, dict) or bool(result.get("ok")):
            return
        logger.warning(
            "memcore %s rejected status=%s reason=%s",
            operation,
            str(result.get("status") or "unknown")[:80],
            str(result.get("reason") or "")[:160],
        )

    def _record_attachment_material_trace(
        self,
        *,
        event_type: str,
        item: dict[str, Any],
        timestamp: int,
        reason: str = "",
        delete_storage: bool = False,
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None or not isinstance(item, dict):
            return {}
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        profile_user_id = str(item.get("profile_user_id") or "").strip()
        session_id = str(item.get("session_id") or "").strip()
        character_pack_id = str(detail.get("character_pack_id") or item.get("character_pack_id") or "").strip()
        try:
            if str(event_type or "").strip().lower() == "cleanup":
                return manager.record_material_cleanup(
                    item=item,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                    timestamp=timestamp,
                    reason=reason,
                    delete_storage=delete_storage,
                )
            return manager.record_material_reference(
                item=item,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                timestamp=timestamp,
            )
        except Exception as exc:
            logger.warning("memcore attachment material trace failed: %s", exc)
            return {"ok": False, "status": "failed", "reason": str(exc)}

    def _record_generated_workspace_cleanup(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
        action: str,
        status: str,
        managed: list[dict[str, Any]],
        failures: list[dict[str, Any]],
        unresolved: list[str],
        reason: str,
        timestamp: int,
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None:
            return {"ok": True, "status": "skipped", "reason": "memcore_disabled"}
        resolved_character_pack_id = str(character_pack_id or "").strip()
        if not resolved_character_pack_id:
            store = getattr(self, "store", None)
            session = (
                store.get_session(profile_user_id, session_id)
                if store is not None and hasattr(store, "get_session")
                else None
            )
            if isinstance(session, dict):
                resolved_character_pack_id = str(session.get("character_pack_id") or "").strip()
        try:
            result = manager.record_generated_workspace_cleanup(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=resolved_character_pack_id,
                action=action,
                status=status,
                managed=managed,
                failures=failures,
                unresolved=unresolved,
                reason=reason,
                timestamp=timestamp,
            )
            self._warn_memcore_write_result("generated workspace cleanup", result)
            return result
        except Exception as exc:
            logger.warning("memcore generated workspace cleanup failed: %s", exc)
            return {"ok": False, "status": "failed", "reason": str(exc)}

    def _schedule_memcore_compaction(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        chat_model_override: str = "",
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None:
            return {}
        try:
            return manager.compact_due_background(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                provider_profile=self._chat_provider_protocol_for_memcore(chat_model_override=chat_model_override),
            )
        except Exception as exc:
            logger.warning("memcore compaction scheduling failed: %s", exc)
            return {"ok": False, "status": "failed", "reason": str(exc)}

    def backfill_memcore_from_legacy_raw(
        self,
        *,
        profile_user_id: str = "",
        character_pack_id: str | None = None,
        batch_size: int = 64,
        limit: int | None = None,
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None:
            return {"ok": False, "status": "disabled", "reason": "memcore_manager_not_enabled"}
        try:
            return manager.import_legacy_raw_messages(
                legacy_store=self.store,
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                batch_size=batch_size,
                limit=limit,
            )
        except Exception as exc:
            logger.warning("memcore legacy raw backfill failed: %s", exc)
            return {"ok": False, "status": "failed", "reason": str(exc)}

    def backfill_memcore_from_legacy_long_term(
        self,
        *,
        profile_user_id: str = "",
        character_pack_id: str | None = None,
        batch_size: int = 64,
        limit: int | None = None,
    ) -> dict[str, Any]:
        manager = self._memcore_manager_if_enabled()
        if manager is None:
            return {"ok": False, "status": "disabled", "reason": "memcore_manager_not_enabled"}
        try:
            return manager.import_legacy_long_term_memory(
                legacy_store=self.store,
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                batch_size=batch_size,
                limit=limit,
            )
        except Exception as exc:
            logger.warning("memcore legacy long-term backfill failed: %s", exc)
            return {"ok": False, "status": "failed", "reason": str(exc)}

    def backfill_memcore_from_legacy_memory(
        self,
        *,
        profile_user_id: str = "",
        character_pack_id: str | None = None,
        batch_size: int = 64,
        limit: int | None = None,
    ) -> dict[str, Any]:
        raw = self.backfill_memcore_from_legacy_raw(
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            batch_size=batch_size,
            limit=limit,
        )
        long_term = self.backfill_memcore_from_legacy_long_term(
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            batch_size=batch_size,
            limit=limit,
        )
        ok = bool(raw.get("ok")) and bool(long_term.get("ok"))
        reason = "; ".join(
            part
            for part in (
                str(raw.get("reason") or ""),
                str(long_term.get("reason") or ""),
            )
            if part
        )
        return {
            "ok": ok,
            "status": "completed" if ok else "partial",
            "reason": reason,
            "raw": raw,
            "long_term": long_term,
        }

    def snapshot_embedding_reindex_status(self) -> dict[str, Any]:
        with self._embedding_reindex_lock:
            snapshot = dict(self._embedding_reindex_status)
        snapshot["provider_health"] = dict(getattr(self, "_embedding_startup_status", {}) or {})
        return snapshot

    def _build_embedding_provider(self) -> BaseEmbeddingProvider:
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
                )
            except Exception:
                base_provider = HashedEmbeddingProvider()
        if self._embedding_startup_status.get("status") == "not_checked":
            self._embedding_startup_status.update(
                {
                    "provider": base_provider.name,
                    "model": str(getattr(base_provider, "model_name", "") or ""),
                    "dimension": base_provider.dimension,
                    "status": "degraded" if base_provider.name == "hashed" else "ready",
                    "ok": base_provider.name != "hashed",
                    "reason": "hashed_embedding_selected_or_fallback" if base_provider.name == "hashed" else "",
                }
            )
        if int(getattr(config, "EMBEDDING_CACHE_SIZE", 0) or 0) > 0:
            return CachedEmbeddingProvider(
                base_provider,
                max_entries=int(getattr(config, "EMBEDDING_CACHE_SIZE", 0) or 0),
            )
        return base_provider

    def _maybe_start_embedding_reindex(self) -> None:
        if self._memcore_owns_legacy_vector_index():
            with self._embedding_reindex_lock:
                self._embedding_reindex_status.update(
                    {
                        "state": "disabled",
                        "processed": 0,
                        "total": 0,
                        "started_at": 0.0,
                        "finished_at": time.time(),
                        "error": "memcore_owns_legacy_vector_index",
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

    def _get_prompt_builder(self) -> PromptBuilder:
        prompt_builder = getattr(self, "prompt_builder", None)
        if prompt_builder is None:
            prompt_builder = PromptBuilder(
                PERSONA,
                stable_system_blocks_provider=getattr(
                    self,
                    "stable_system_blocks_provider",
                    None,
                ),
            )
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
        from .engine_services.turn_context import resolve_payload_character_pack_id as _fn

        return _fn(payload)

    @staticmethod
    def _resolve_turn_actor(payload: dict[str, Any]) -> tuple[str, str]:
        from .engine_services.turn_context import resolve_turn_actor as _fn

        return _fn(payload)

    @staticmethod
    def _resolve_turn_domain_profile(payload: dict[str, Any]) -> str:
        from .domain_profiles import DomainProfileRegistry

        return DomainProfileRegistry().get(payload.get("domain_profile")).id

    def _resolve_turn_speaker_identity(
        self,
        client_context: ClientProtocolContext,
        character_pack_id: str,
    ) -> dict[str, str]:
        """Resolve display speaker identity for the current turn.

        DesktopPet / QQ text modes with a valid character pack  →  character pack identity.
        Other modes  →  persona_profiles.toml defaults (PERSONA).
        """
        if client_context is not None and client_context.effective_mode in {ClientMode.DESKTOP_PET, ClientMode.QQ_TEXT}:
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
        preferred_outfit: str = "",
        extra_character_outfits: list[dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        service = getattr(self, "desktop_pet_character_resources", None)
        if service is None or not character_pack_id:
            return {
                "system_context": "",
                "reference_context": "",
                "resource_context": "",
                "active_id": "",
            }
        builder = getattr(service, "build_persona_prompt_context", None)
        if builder is None:
            return {
                "system_context": "",
                "reference_context": "",
                "resource_context": "",
                "active_id": "",
            }
        try:
            context = builder(
                character_pack_id,
                resource_manifest=resource_manifest,
                client_mode=client_mode,
                preferred_outfit=preferred_outfit,
                extra_character_outfits=extra_character_outfits,
            )
        except Exception as exc:
            logger.warning("desktop pet character pack prompt context failed: %s", exc)
            return {
                "system_context": "",
                "reference_context": "",
                "resource_context": "",
                "active_id": "",
            }
        return (
            context
            if isinstance(context, dict)
            else {
                "system_context": "",
                "reference_context": "",
                "resource_context": "",
                "active_id": "",
            }
        )

    @staticmethod
    def _merge_prompt_persona_contexts(*contexts: dict[str, Any]) -> dict[str, str]:
        system_parts: list[str] = []
        reference_parts: list[str] = []
        resource_parts: list[str] = []
        active_id = ""
        for context in contexts:
            if not isinstance(context, dict):
                continue
            system_context = str(context.get("system_context") or "").strip()
            reference_context = str(context.get("reference_context") or "").strip()
            resource_context = str(context.get("resource_context") or "").strip()
            current_active_id = str(context.get("active_id") or "").strip()
            if system_context:
                system_parts.append(system_context)
            if reference_context:
                reference_parts.append(reference_context)
            if resource_context:
                resource_parts.append(resource_context)
            if current_active_id:
                active_id = current_active_id
        return {
            "system_context": "\n\n".join(system_parts),
            "reference_context": "\n\n".join(reference_parts),
            "resource_context": "\n\n".join(resource_parts),
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
        return self._merge_prompt_persona_contexts(*contexts)

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
        from .optional_media_binding import prepare_timeline_transcript

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
        from .optional_media_binding import prepare_timeline_vocals

        return prepare_timeline_vocals(self, **kwargs)

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

    def _get_retrieval_service(self) -> RetrievalService:
        cached = getattr(self, "retrieval_service", None)
        if cached is not None:
            return cached
        from .engine_services.memory_facade import get_retrieval_service as _fn

        svc = _fn(
            store=self.store, vector_store=self.vector_store, llm=self.llm, prompt_builder=self._get_prompt_builder()
        )
        self.retrieval_service = svc
        return svc

    @staticmethod
    def _collect_visible_context_source_ids(
        *,
        recent_raw: list[dict[str, Any]],
        recent_episodic_summaries: list[dict[str, Any]],
        recent_semantic_summaries: list[dict[str, Any]],
        extra_source_ids: list[str] | None = None,
    ) -> list[str]:
        from .engine_services.memory_facade import collect_visible_context_source_ids as _fn

        return _fn(
            recent_raw=recent_raw,
            recent_episodic_summaries=recent_episodic_summaries,
            recent_semantic_summaries=recent_semantic_summaries,
            extra_source_ids=extra_source_ids,
        )

    def _get_compaction_service(self) -> MemoryCompactionService:
        cached = getattr(self, "compaction_service", None)
        if cached is not None:
            return cached
        from .engine_services.memory_facade import get_compaction_service as _fn

        svc = _fn(
            store=self.store,
            vector_store=self.vector_store,
            llm=self.llm,
            prompt_builder=self._get_prompt_builder(),
            persona_context_provider=self._build_memory_compaction_persona_context,
        )
        self.compaction_service = svc
        return svc

    def _coerce_bool(self, value: Any) -> bool | None:
        from .engine_services.turn_context import coerce_bool as _fn

        return _fn(value)

    def get_care_module(self) -> CareModulePort:
        module = getattr(self, "care_module", None)
        return module if isinstance(module, CareModulePort) else CareModulePort.disabled("not_configured")

    def care_feature_status(self) -> dict[str, Any]:
        return self.get_care_module().status_payload()

    def _load_desktop_care_config(self, character_pack_id: str) -> dict[str, Any]:
        return load_character_care_config(
            getattr(self, "desktop_pet_character_resources", None),
            character_pack_id,
        )

    def care_enabled_for_context(
        self,
        *,
        character_pack_id: str,
        client_context: ClientProtocolContext | None,
    ) -> bool:
        if not self.get_care_module().enabled:
            return False
        if client_context is not None and client_context.effective_mode == ClientMode.DESKTOP_PET:
            return bool(normalize_desktop_care_config(self._load_desktop_care_config(character_pack_id))["enabled"])
        return True

    def build_desktop_care_snapshot(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str,
        legacy_state: Any = None,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        module = self.get_care_module()
        runtime = module.runtime
        if runtime is None:
            return module.disabled_result()
        raw_config = self._load_desktop_care_config(character_pack_id)
        config = normalize_desktop_care_config(raw_config)
        if not config["enabled"]:
            return {
                "ok": False,
                "status": "disabled",
                "reason": "character_care_disabled",
            }
        return runtime.snapshot_for_desktop(
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            care_config=raw_config,
            legacy_state=legacy_state,
            now_ms=now_ms,
        )

    def manage_desktop_care_action(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str,
        action: str,
        item_id: str = "",
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        module = self.get_care_module()
        runtime = module.runtime
        if runtime is None:
            return module.disabled_result()
        raw_config = self._load_desktop_care_config(character_pack_id)
        config = normalize_desktop_care_config(raw_config)
        if not config["enabled"]:
            return {
                "ok": False,
                "status": "disabled",
                "reason": "character_care_disabled",
            }
        return runtime.perform_desktop_action(
            profile_user_id=profile_user_id,
            character_pack_id=character_pack_id,
            care_config=raw_config,
            action=action,
            item_id=item_id,
            now_ms=now_ms,
        )

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
        care_module = self.get_care_module()
        care_runtime = care_module.runtime
        if care_runtime is None:
            sanitized_payload = dict(payload)
            sanitized_payload.pop("desktop_care", None)
            sanitized_payload.pop("care_state", None)
            return sanitized_payload
        client_mode = client_context.effective_mode.value if client_context is not None else ""
        relation_user_id = self._resolve_care_relation_user_id(
            payload,
            client_context,
            profile_user_id=profile_user_id,
        )
        now_ms = int(max(1, now_ts) * 1000)
        desktop_care = payload.get("desktop_care")
        try:
            is_desktop = client_context is not None and client_context.effective_mode == ClientMode.DESKTOP_PET
            if is_desktop:
                result = self.build_desktop_care_snapshot(
                    profile_user_id=profile_user_id,
                    character_pack_id=character_pack_id,
                    legacy_state=desktop_care,
                    now_ms=now_ms,
                )
                snapshot = result.get("snapshot") if isinstance(result, dict) else None
                if isinstance(snapshot, dict):
                    enriched_payload = dict(payload)
                    enriched_payload["desktop_care"] = snapshot
                    return enriched_payload
                sanitized_payload = dict(payload)
                sanitized_payload.pop("desktop_care", None)
                sanitized_payload.pop("care_state", None)
                return sanitized_payload
            if isinstance(desktop_care, dict):
                care_runtime.sync_from_client(
                    profile_user_id=profile_user_id,
                    character_pack_id=character_pack_id,
                    client_mode=client_mode,
                    care_payload=desktop_care,
                    relation_user_id=relation_user_id,
                    now_ms=now_ms,
                )
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
        if not isinstance(final_output, dict):
            return
        care_module = self.get_care_module()
        if not care_module.enabled:
            final_output.pop("state_request", None)
            final_output.pop("care_state", None)
            return
        if client_context is None or client_context.effective_mode not in {
            ClientMode.DESKTOP_PET,
            ClientMode.QQ_TEXT,
        }:
            return
        is_desktop = client_context.effective_mode == ClientMode.DESKTOP_PET
        if is_desktop and not self.care_enabled_for_context(
            character_pack_id=character_pack_id,
            client_context=client_context,
        ):
            final_output.pop("state_request", None)
            final_output.pop("care_state", None)
            return
        care_runtime = care_module.runtime
        if care_runtime is None:
            return
        now_ms = int(max(1, now_ts) * 1000)
        try:
            relation_user_id = self._resolve_care_relation_user_id(
                payload or {},
                client_context,
                profile_user_id=profile_user_id,
            )
        except Exception as exc:
            logger.warning("care runtime relation_user_id resolve failed: %s", exc)
            relation_user_id = ""
        raw_config = self._load_desktop_care_config(character_pack_id) if is_desktop else {}
        desktop_config = normalize_desktop_care_config(raw_config) if is_desktop else {}
        turn_kind = str((payload or {}).get("turn_kind") or "").strip().lower()
        if is_desktop:
            energy_cost = (
                0
                if turn_kind == "desktop_pet_care_feed"
                else int(
                    desktop_config["decay"][
                        "energy_per_proactive" if turn_kind == "desktop_pet_proactive" else "energy_per_reply"
                    ]
                )
            )
            coin_reward = 0
        else:
            energy_cost = 1
            coin_reward = 1
        try:
            care_runtime.apply_energy_cost(
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                relation_user_id=relation_user_id,
                energy_cost=energy_cost,
                coin_reward=coin_reward,
                now_ms=now_ms,
            )
        except Exception as exc:
            logger.warning("care runtime energy cost failed: %s", exc)
        try:
            care_runtime.record_turn(
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                relation_user_id=relation_user_id,
                now_ms=now_ms,
            )
        except Exception as exc:
            logger.warning("care runtime record_turn failed: %s", exc)
        snapshot: dict[str, Any] | None = None
        state_request = final_output.get("state_request")
        if isinstance(state_request, dict):
            affinity_delta = state_request.get("affinity")
            try:
                delta = max(-5, min(5, int(affinity_delta)))
            except (TypeError, ValueError):
                delta = 0
            if delta:
                try:
                    snapshot = care_runtime.apply_affinity_delta(
                        profile_user_id=profile_user_id,
                        character_pack_id=character_pack_id,
                        client_mode=client_context.effective_mode.value,
                        relation_user_id=relation_user_id,
                        delta=delta,
                        now_ms=now_ms,
                    )
                except Exception as exc:
                    logger.warning("care runtime affinity update failed: %s", exc)
        if snapshot is None:
            try:
                if is_desktop:
                    snapshot_result = care_runtime.snapshot_for_desktop(
                        profile_user_id=profile_user_id,
                        character_pack_id=character_pack_id,
                        care_config=raw_config,
                        now_ms=now_ms,
                    )
                    snapshot = snapshot_result.get("snapshot")
                else:
                    snapshot = care_runtime.snapshot_for_client(
                        profile_user_id=profile_user_id,
                        character_pack_id=character_pack_id,
                        client_mode=ClientMode.QQ_TEXT.value,
                        relation_user_id=relation_user_id,
                        now_ms=now_ms,
                    )
            except Exception as exc:
                logger.warning("care runtime response snapshot failed: %s", exc)
        if isinstance(snapshot, dict):
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
        if self._memcore_owns_compaction():
            return
        from .engine_services.memory_facade import schedule_summary_cycle as _fn

        _fn(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            compaction_service=self._get_compaction_service(),
        )

    def _run_summary_cycle(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str = "",
    ) -> None:
        if self._memcore_owns_compaction():
            manager = self._memcore_manager_if_enabled()
            if manager is not None:
                manager.compact_due_sync(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                    provider_profile=self._chat_provider_protocol_for_memcore(),
                )
            return
        from .engine_services.memory_facade import run_summary_cycle as _fn

        _fn(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            compaction_service=self._get_compaction_service(),
        )

    def ingest_qq_attachments(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachments: list[dict[str, Any]],
        character_pack_id: str = "",
        timestamp: int | None = None,
        observe_images: bool = True,
    ) -> list[dict[str, Any]]:
        service = self._get_attachment_ingest_service()
        if service is None:
            return []
        return service.ingest_qq_attachments(
            profile_user_id=profile_user_id,
            session_id=session_id,
            attachments=attachments,
            character_pack_id=character_pack_id,
            timestamp=timestamp,
            observe_images=observe_images,
        )

    def ingest_desktop_pet_audio_attachment(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        source_path: Path | str,
        origin_name: str = "",
        mime_type: str = "",
        character_pack_id: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        return desktop_pet_engine.ingest_desktop_pet_audio_attachment(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            source_path=source_path,
            origin_name=origin_name,
            mime_type=mime_type,
            character_pack_id=character_pack_id,
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
        character_pack_id: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        return desktop_pet_engine.import_desktop_pet_local_paths(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            paths=paths,
            recursive=recursive,
            max_files=max_files,
            character_pack_id=character_pack_id,
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
        character_pack_id: str = "",
        action: str,
        item_type: str = "",
        target: str = "",
    ) -> dict[str, Any]:
        return desktop_pet_engine.manage_desktop_pet_workspace_panel(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
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


    def _is_transient_user_turn(self, payload: dict[str, Any]) -> bool:
        from .engine_services.turn_context import is_transient_user_turn as _fn

        return _fn(payload)

    @staticmethod
    def _should_persist_assistant_turn(payload: dict[str, Any]) -> bool:
        return not bool(payload.get("transient_assistant_message"))

    @staticmethod
    def _should_persist_completed_assistant(
        persist_requested: bool,
        final_output: dict[str, Any],
    ) -> bool:
        return (
            bool(persist_requested)
            and not bool(final_output.get("_transient_final_failure"))
            and not bool(final_output.get("_deliberate_silence"))
            and not bool(final_output.get("_notification_suppressed"))
        )

    @staticmethod
    def _pop_user_memory_source_id(
        payload: dict[str, Any],
        *,
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        memory_role: str = "user",
    ) -> str:
        raw_idempotency_key = payload.pop("memory_idempotency_key", "")
        if not isinstance(raw_idempotency_key, str) or not raw_idempotency_key.strip():
            return ""
        source_material = json.dumps(
            {
                "profile_user_id": str(profile_user_id),
                "session_id": str(session_id),
                "character_pack_id": normalize_character_pack_id(character_pack_id),
                "memory_idempotency_key": raw_idempotency_key.strip(),
                "role": str(memory_role or "user").strip() or "user",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(source_material.encode("utf-8", errors="ignore")).hexdigest()
        return f"plugin-event:{digest}"

    @staticmethod
    def _pop_plugin_external_event(
        payload: dict[str, Any],
        *,
        event_allowed: bool,
    ) -> dict[str, Any] | None:
        raw = payload.pop("plugin_external_event", None)
        if not event_allowed or not isinstance(raw, dict) or "data" not in raw:
            return None
        event_type = str(raw.get("event_type") or "").strip().lower()
        source = str(raw.get("source") or "").strip()
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,119}", event_type) is None or not source:
            return None
        try:
            data = json.loads(json.dumps(raw["data"], ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError, OverflowError, RecursionError):
            return None
        return {"event_type": event_type, "source": source, "data": data}

    @staticmethod
    def _external_event_memory_metadata(event: dict[str, Any]) -> dict[str, Any]:
        _ = event
        return {}

    @staticmethod
    def _render_external_event_memory_content(event: dict[str, Any]) -> str:
        """Return the stable stored identity of one structured external event.

        ``message`` on a plugin turn request is a turn instruction and may
        improve when the plugin is upgraded. It must therefore not be the
        immutable content behind ``memory_idempotency_key``. The structured
        event is the durable stimulus; render only those facts here so the same
        event can be retried with better instructions without colliding, while
        changed event facts still fail the store's normal idempotency check.
        """

        return json.dumps(event, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)

    def _build_transient_user_record(
        self,
        *,
        user_message: str,
        now_ts: int,
        date_label: str,
        time_of_day: str,
    ) -> dict[str, Any]:
        from .engine_services.turn_context import build_transient_user_record as _fn

        return _fn(user_message=user_message, now_ts=now_ts, date_label=date_label, time_of_day=time_of_day)

    @staticmethod
    def _normalize_message_addressing(
        payload: dict[str, Any],
        *,
        fallback_mode: str,
    ) -> dict[str, Any]:
        raw = payload.get("message_addressing")
        if not isinstance(raw, dict):
            return (
                {
                    "mode": "observed",
                    "trigger": "passive",
                    "addressed_to_assistant": False,
                    "explicit_assistant_mention": False,
                    "primary_target": {},
                    "mentions": [],
                }
                if fallback_mode == "observed"
                else {}
            )
        mode = str(raw.get("mode") or fallback_mode).strip().lower()
        if mode not in {"current_request", "observed"}:
            mode = fallback_mode
        trigger = str(raw.get("trigger") or "").strip()[:80]
        mentions: list[dict[str, Any]] = []
        for item in list(raw.get("mentions") or [])[:16]:
            if not isinstance(item, dict):
                continue
            actor_id = str(item.get("actor_id") or "").strip()[:160]
            if not actor_id:
                continue
            mentions.append(
                {
                    "actor_id": actor_id,
                    "display_name": str(item.get("display_name") or "").strip()[:160],
                    "is_assistant": bool(item.get("is_assistant")),
                }
            )
        addressed_to_assistant = bool(raw.get("addressed_to_assistant"))
        reply_raw = raw.get("reply_reference")
        reply_reference: dict[str, Any] = {}
        if isinstance(reply_raw, dict):
            reply_actor_id = str(reply_raw.get("actor_id") or "").strip()[:160]
            reply_actor_name = str(reply_raw.get("actor_display_name") or "").strip()[:160]
            reply_message_id = str(reply_raw.get("message_id") or "").strip()[:160]
            reply_excerpt = str(reply_raw.get("excerpt") or "").strip()[:1000]
            try:
                reply_timestamp = int(float(reply_raw.get("timestamp") or 0))
            except (TypeError, ValueError):
                reply_timestamp = 0
            reply_conversation_kind = str(reply_raw.get("conversation_kind") or "").strip()[:32]
            reply_conversation_id = str(reply_raw.get("conversation_id") or "").strip()[:160]
            try:
                reply_attachment_count = max(0, int(reply_raw.get("attachment_count") or 0))
            except (TypeError, ValueError):
                reply_attachment_count = 0
            reply_mentions: list[dict[str, Any]] = []
            seen_reply_mentions: set[str] = set()
            for item in list(reply_raw.get("mentions") or [])[:16]:
                if not isinstance(item, dict):
                    continue
                mention_id = str(item.get("actor_id") or "").strip()[:160]
                if not mention_id or mention_id in seen_reply_mentions:
                    continue
                seen_reply_mentions.add(mention_id)
                reply_mentions.append(
                    {
                        "actor_id": mention_id,
                        "display_name": str(item.get("display_name") or "").strip()[:160],
                        "is_assistant": bool(item.get("is_assistant")),
                    }
                )
            if reply_actor_id or reply_message_id or reply_excerpt:
                reply_reference = {
                    "actor_id": reply_actor_id,
                    "actor_display_name": reply_actor_name,
                    "message_id": reply_message_id,
                    "excerpt": reply_excerpt,
                }
                if reply_timestamp > 0:
                    reply_reference["timestamp"] = reply_timestamp
                if reply_conversation_kind:
                    reply_reference["conversation_kind"] = reply_conversation_kind
                if reply_conversation_id:
                    reply_reference["conversation_id"] = reply_conversation_id
                if reply_attachment_count > 0:
                    reply_reference["attachment_count"] = reply_attachment_count
                if reply_mentions:
                    reply_reference["mentions"] = reply_mentions
        primary_raw = raw.get("primary_target")
        primary = dict(primary_raw) if isinstance(primary_raw, dict) else {}
        target_id = str(primary.get("actor_id") or "").strip()[:160]
        target_name = str(primary.get("display_name") or "").strip()[:160]
        if addressed_to_assistant:
            target_id = "assistant"
            target_name = ""
        normalized = {
            "mode": mode,
            "trigger": trigger,
            "addressed_to_assistant": addressed_to_assistant,
            "explicit_assistant_mention": bool(raw.get("explicit_assistant_mention")),
            "primary_target": {
                "actor_id": target_id,
                "display_name": target_name,
            }
            if target_id
            else {},
            "mentions": mentions,
        }
        if reply_reference:
            normalized["reply_reference"] = reply_reference
        return normalized

    @staticmethod
    def _apply_message_addressing(
        record: dict[str, Any],
        addressing: dict[str, Any],
    ) -> tuple[str, str]:
        if not addressing:
            return "", ""
        record["message_addressing"] = dict(addressing)
        if str(addressing.get("mode") or "") == "observed":
            record["kind"] = "message.user.observed"
        target = addressing.get("primary_target")
        if not isinstance(target, dict):
            return "", ""
        target_id = str(target.get("actor_id") or "").strip()
        target_name = str(target.get("display_name") or "").strip()
        if target_id:
            record["target_actor_id"] = target_id
            record["target_actor_display_name"] = target_name
        return target_id, target_name

    @staticmethod
    def _normalize_forward_references(payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw_references = payload.get("forward_references")
        if not isinstance(raw_references, (list, tuple)):
            return []
        references: list[dict[str, Any]] = []
        for raw in raw_references:
            if not isinstance(raw, dict):
                continue
            nodes: list[dict[str, Any]] = []
            for raw_node in list(raw.get("nodes") or []):
                if not isinstance(raw_node, dict):
                    continue
                try:
                    index = max(0, int(raw_node.get("index") or 0))
                except (TypeError, ValueError):
                    index = 0
                try:
                    timestamp = int(float(raw_node.get("timestamp") or 0))
                except (TypeError, ValueError):
                    timestamp = 0
                try:
                    attachment_count = max(0, int(raw_node.get("attachment_count") or 0))
                except (TypeError, ValueError):
                    attachment_count = 0
                node = {
                    "index": index,
                    "actor_id": str(raw_node.get("actor_id") or "").strip(),
                    "actor_display_name": str(
                        raw_node.get("actor_display_name") or raw_node.get("actor_label") or ""
                    ).strip(),
                    "text": str(raw_node.get("text") or ""),
                }
                mentions: list[dict[str, Any]] = []
                seen_mentions: set[str] = set()
                for raw_mention in list(raw_node.get("mentions") or [])[:16]:
                    if not isinstance(raw_mention, dict):
                        continue
                    mention_id = str(raw_mention.get("actor_id") or "").strip()[:160]
                    if not mention_id or mention_id in seen_mentions:
                        continue
                    seen_mentions.add(mention_id)
                    mentions.append(
                        {
                            "actor_id": mention_id,
                            "display_name": str(raw_mention.get("display_name") or "").strip()[:160],
                            "is_assistant": bool(raw_mention.get("is_assistant")),
                        }
                    )
                if mentions:
                    node["mentions"] = mentions
                message_id = str(raw_node.get("message_id") or "").strip()
                if timestamp > 0:
                    node["timestamp"] = timestamp
                if message_id:
                    node["message_id"] = message_id
                if attachment_count > 0:
                    node["attachment_count"] = attachment_count
                nodes.append(node)
            entry: dict[str, Any] = {
                "source_part_id": str(raw.get("source_part_id") or "").strip(),
                "forward_id": str(raw.get("forward_id") or "").strip(),
                "status": str(raw.get("status") or "unavailable").strip().lower(),
                "nodes": nodes,
            }
            if "ok" in raw:
                entry["ok"] = bool(raw.get("ok"))
            reason = str(raw.get("reason") or "").strip()
            if reason:
                entry["reason"] = reason
            entry["node_count"] = len(nodes)
            if entry["forward_id"] or entry["source_part_id"]:
                references.append(entry)
        return references

    def record_passive_qq_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._record_passive_qq_message(payload, schedule_maintenance=True)

    def record_plugin_timeline_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist a typed plugin event without opening a model-response turn."""

        manager = self._memcore_manager_if_enabled()
        if manager is None:
            return {"ok": False, "status": "unavailable", "reason": "memcore_not_enabled"}
        source_id = str(payload.get("source_id") or "").strip()
        session_id = str(payload.get("user_id") or payload.get("session_id") or "default_session")
        profile_user_id = str(payload.get("real_user_id") or payload.get("profile_user_id") or session_id)
        character_pack_id = self._resolve_payload_character_pack_id(payload)
        event = payload.get("event") if isinstance(payload.get("event"), dict) else None
        if not source_id or event is None:
            return {"ok": False, "status": "invalid_event", "reason": "event_contract_invalid"}
        result = manager.append_standalone_event(
            event,
            source_id=source_id,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            timestamp=int(payload.get("timestamp") or time.time()),
        )
        if bool(result.get("ok")) and self._memcore_owns_compaction():
            result = {
                **result,
                "compaction": self._schedule_memcore_compaction(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                ),
            }
        return result

    def record_passive_qq_messages(self, payloads: list[dict[str, Any]]) -> dict[str, Any]:
        """Record one ordered passive-message batch and schedule maintenance once."""

        normalized = [dict(item) for item in list(payloads or []) if isinstance(item, dict)]
        if not normalized:
            return {"ok": False, "status": "empty_batch", "results": []}
        identities = {
            (
                str(item.get("real_user_id") or item.get("profile_user_id") or "").strip(),
                str(item.get("user_id") or item.get("session_id") or "").strip(),
                str(self._resolve_payload_character_pack_id(item) or "").strip(),
            )
            for item in normalized
        }
        if len(identities) != 1:
            return {
                "ok": False,
                "status": "mixed_batch_scope",
                "reason": "passive_batch_requires_one_conversation",
                "results": [],
            }
        results: list[dict[str, Any]] = []
        for item in normalized:
            try:
                result = self._record_passive_qq_message(item, schedule_maintenance=False)
            except Exception as exc:
                result = {
                    "ok": False,
                    "status": "record_failed",
                    "reason": exc.__class__.__name__,
                }
            results.append(dict(result or {}))
        successful = [item for item in results if bool(item.get("ok"))]
        maintenance: dict[str, Any] = {}
        if successful:
            sample = successful[0]
            if self._memcore_owns_compaction():
                maintenance = self._schedule_memcore_compaction(
                    profile_user_id=str(sample.get("profile_user_id") or ""),
                    session_id=str(sample.get("session_id") or ""),
                    character_pack_id=str(sample.get("character_pack_id") or ""),
                )
            else:
                self._schedule_summary_cycle(
                    profile_user_id=str(sample.get("profile_user_id") or ""),
                    session_id=str(sample.get("session_id") or ""),
                    character_pack_id=str(sample.get("character_pack_id") or ""),
                )
                maintenance = {"ok": True, "status": "scheduled"}
        failed_count = len(results) - len(successful)
        return {
            "ok": failed_count == 0,
            "status": "recorded" if failed_count == 0 else "partially_recorded",
            "count": len(results),
            "recorded_count": len(successful),
            "failed_count": failed_count,
            "results": results,
            "maintenance": maintenance,
        }

    def _record_passive_qq_message(
        self,
        payload: dict[str, Any],
        *,
        schedule_maintenance: bool,
    ) -> dict[str, Any]:
        turn_character_pack_id = self._resolve_payload_character_pack_id(payload)
        actor_stable_id, actor_display_name = self._resolve_turn_actor(payload)
        session_id = str(payload.get("user_id") or payload.get("session_id") or "default_session")
        profile_user_id = str(payload.get("real_user_id") or session_id)
        user_message = str(payload.get("message") or "").strip()
        memory_user_message = str(payload.get("memory_message") or user_message).strip()
        if not user_message:
            return {
                "ok": False,
                "status": "empty_message",
                "session_id": session_id,
                "profile_user_id": profile_user_id,
                "character_pack_id": turn_character_pack_id,
            }
        now_ts = int(payload.get("timestamp") or time.time())
        date_label = timestamp_to_date_label(now_ts)
        time_of_day = detect_time_of_day_from_text(user_message) or infer_time_of_day(now_ts)
        addressing = self._normalize_message_addressing(
            payload,
            fallback_mode="observed",
        )
        forward_references = self._normalize_forward_references(payload)
        memory_metadata = {
            "source": "qq_group_passive",
            "client_mode": str(payload.get("client_mode") or "qq_text"),
            "passive": True,
            "message_addressing": addressing,
        }
        if forward_references:
            memory_metadata["forward_references"] = forward_references
        user_record = self.store.add_message(
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=turn_character_pack_id,
            role="user",
            content=memory_user_message,
            timestamp=now_ts,
            date_label=date_label,
            time_of_day=time_of_day,
            semantic_tags=extract_semantic_tags(memory_user_message),
            memory_metadata=memory_metadata,
            index_in_vector=False,
        )
        source_message_id = str(payload.get("source_message_id") or "").strip()
        if source_message_id:
            user_record["source_message_id"] = source_message_id
        target_actor_id, target_actor_display_name = self._apply_message_addressing(
            user_record,
            addressing,
        )
        if forward_references:
            user_record["forward_references"] = forward_references
        if schedule_maintenance and not self._memcore_owns_compaction():
            self._schedule_summary_cycle(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=turn_character_pack_id,
            )
        memcore_result = self._append_memcore_passive_message(
            user_record=user_record,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=turn_character_pack_id,
            actor_stable_id=actor_stable_id,
            actor_display_name=actor_display_name,
            target_actor_id=target_actor_id,
            target_actor_display_name=target_actor_display_name,
        )
        compaction_result = (
            self._schedule_memcore_compaction(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=turn_character_pack_id,
            )
            if schedule_maintenance and self._memcore_owns_compaction()
            else {}
        )
        return {
            "ok": True,
            "status": "recorded",
            "source_id": str(user_record.get("source_id") or ""),
            "session_id": session_id,
            "profile_user_id": profile_user_id,
            "character_pack_id": turn_character_pack_id,
            "memcore": memcore_result,
            "compaction": compaction_result,
        }

    def _extract_desktop_screen_frame_images(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        from .engine_services.turn_context import extract_desktop_screen_frame_images as _fn

        return _fn(payload)

    def _extract_native_user_images(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw_images = payload.get("native_user_images") if isinstance(payload, dict) else None
        if not isinstance(raw_images, list):
            return []
        images: list[dict[str, Any]] = []
        for raw in raw_images[:5]:
            if not isinstance(raw, dict):
                continue
            data_url = str(raw.get("data_url") or "").strip()
            if not data_url.startswith("data:image/"):
                continue
            images.append(
                {
                    "data_url": data_url,
                    "attachment_id": str(raw.get("attachment_id") or "").strip(),
                    "attachment_handle": str(raw.get("attachment_handle") or "").strip(),
                    "title": str(raw.get("title") or "").strip()[:80],
                }
            )
        return images

    @staticmethod
    def _build_native_user_image_prompt_context(images: list[dict[str, Any]]) -> str:
        labels: list[str] = []
        for image in images:
            label = (
                str(image.get("attachment_handle") or "").strip()
                or str(image.get("title") or "").strip()
                or str(image.get("attachment_id") or "").strip()
            )
            if label and label not in labels:
                labels.append(label)
        suffix = "、".join(labels[:5]) or f"{len(images)} 张图片"
        return (
            "【本轮原生图片】\n"
            f"系统已通过 provider 原生多模态通道提供 {len(images)} 张当前图片：{suffix}。\n"
            "请直接依据这些原始图片和用户本轮文字回答；工作台里的视觉摘要只是辅助证据。"
            "不要声称只能看到摘要，也不要把旧图片、角色立绘或历史附件当成本轮图片。"
        )

    def _steering_native_user_images(
        self,
        steers: list[Any],
        *,
        applied_source_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        applied = {
            str(source_id or "").strip()
            for source_id in list(applied_source_ids or [])
            if str(source_id or "").strip()
        }
        raw_images: list[dict[str, Any]] = []
        # When several inputs coalesce at one safe boundary, the newest image is
        # the most likely correction to the active task and must not be pushed
        # out by the five-image provider cap.
        for steer in reversed(list(steers or [])):
            source_id = str(getattr(steer, "source_id", "") or "").strip()
            if applied and source_id not in applied:
                continue
            raw_images.extend(
                dict(item)
                for item in list(getattr(steer, "native_user_images", ()) or [])
                if isinstance(item, dict)
            )
        return self._merge_model_image_inputs([], raw_images)

    def _build_desktop_screen_frame_prompt_context(self, frames: list[dict[str, Any]]) -> str:
        from .engine_services.turn_context import build_desktop_screen_frame_prompt_context as _fn

        return _fn(frames)

    def prefetch_remote_media_links_for_message(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        message: str,
        character_pack_id: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        return media_bridge_engine.prefetch_remote_media_links_for_message(
            self,
            profile_user_id=profile_user_id,
            session_id=session_id,
            message=message,
            character_pack_id=character_pack_id,
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

    def native_chat_vision_status(self, *, chat_model_override: str = "") -> dict[str, Any]:
        """Whether real images can be sent to a chat-capable target this turn.

        This no longer requires the chat and vision routes to be byte-identical.
        A separately configured VISION_* bundle is itself a usable image target;
        a chat bundle explicitly declared image-capable (CHAT_SUPPORTS_IMAGES)
        is the fallback.  Real image parsing still happens independently, so a
        present image marker without parsed bytes never routes as "seen".
        """
        settings = self._runtime_settings_view()
        if not settings.vision_enabled:
            return {"enabled": False, "reason": "vision_disabled"}
        vision_configured = bool(
            settings.vision_api_key and settings.vision_base_url and settings.vision_model_name
        )
        if vision_configured:
            return {
                "enabled": True,
                "reason": "vision_configured",
                "model": settings.vision_model_name,
                "protocol": settings.vision_api_protocol,
            }
        if settings.chat_supports_images:
            chat_model = str(chat_model_override or settings.chat_model_name).strip()
            return {
                "enabled": True,
                "reason": "chat_supports_images",
                "model": chat_model,
                "protocol": settings.chat_api_protocol,
            }
        return {"enabled": False, "reason": "multimodal_model_unavailable"}

    def _resolve_turn_execution_target(
        self,
        *,
        has_real_images: bool,
        tool_image_upgrade: bool = False,
        chat_model_override: str = "",
    ) -> Any:
        """Resolve the immutable per-turn model target through the runtime.

        Falls back to ``None`` for lightweight engines without a routing
        resolver so existing test harnesses keep the legacy override path.
        """
        runtime = getattr(self, "llm", None)
        resolver = getattr(runtime, "resolve_turn_execution_target", None)
        if callable(resolver):
            return resolver(
                has_real_images=has_real_images,
                tool_image_upgrade=tool_image_upgrade,
                chat_model_override=chat_model_override,
            )
        return None

    def _recompute_turn_execution_target(
        self,
        *,
        current_target: Any,
        tool_results: list[ToolExecutionResult],
        chat_model_override: str = "",
    ) -> Any:
        """Re-resolve the model target after a tool batch, one-way only.

        - a vision target never downgrades back to chat;
        - a chat target upgrades to vision only when the tool batch produced
          real ``data:image/...`` model inputs (not OCR text, summaries or plain
          tool feedback);
        - image loading failures produce no upgrade and the batch never guesses.
        """
        tool_images = self._merge_tool_model_image_inputs([], list(tool_results or []))
        return self._upgrade_turn_execution_target_for_images(
            current_target=current_target,
            model_image_inputs=tool_images,
            chat_model_override=chat_model_override,
            tool_image_upgrade=True,
        )

    def _upgrade_turn_execution_target_for_images(
        self,
        *,
        current_target: Any,
        model_image_inputs: list[dict[str, Any]],
        chat_model_override: str = "",
        tool_image_upgrade: bool = False,
    ) -> Any:
        if current_target is not None and str(getattr(current_target, "role", "") or "") == "vision":
            return current_target
        if not self._merge_model_image_inputs([], model_image_inputs):
            return current_target
        return self._resolve_turn_execution_target(
            has_real_images=not tool_image_upgrade,
            tool_image_upgrade=tool_image_upgrade,
            chat_model_override=chat_model_override,
        )

    def prepare_qq_native_image_inputs(self, **kwargs) -> dict[str, Any]:
        """Compatibility adapter for QQ callers; one shared image preparation path."""
        return self.prepare_native_image_inputs(**kwargs)

    def prepare_native_image_inputs(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachment_ids: list[str],
        chat_model_override: str = "",
        timeout_seconds: float = 8.0,
    ) -> dict[str, Any]:
        native_status = self.native_chat_vision_status(chat_model_override=chat_model_override)
        if not native_status.get("enabled"):
            return {"ok": False, "status": "disabled", "reason": native_status.get("reason"), "images": []}
        service = self._get_attachment_inbox_service()
        if service is None or not hasattr(service, "build_native_image_inputs"):
            return {"ok": False, "status": "unavailable", "reason": "attachment_service_unavailable", "images": []}
        result = service.build_native_image_inputs(
            profile_user_id=profile_user_id,
            session_id=session_id,
            attachment_ids=attachment_ids,
            timeout_seconds=timeout_seconds,
            max_count=5,
            max_bytes_per_image=self._runtime_settings_view().vision_max_image_bytes,
            max_total_bytes=20 * 1024 * 1024,
        )
        return {**dict(result or {}), "native_vision": native_status}

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

        with plugin_turn_scope(self):
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
        precommitted_memcore_turn = (
            dict(_precommitted_memcore_turn) if isinstance(_precommitted_memcore_turn, dict) else {}
        )
        externally_managed_memcore_turn = bool(precommitted_memcore_turn)
        precommitted_source_id = str(precommitted_memcore_turn.get("source_id") or "").strip()
        precommitted_turn_id = str(precommitted_memcore_turn.get("turn_id") or "").strip()
        precommitted_voice_turn_id = str(precommitted_memcore_turn.get("voice_turn_id") or "").strip()
        if externally_managed_memcore_turn and (
            not precommitted_source_id or not precommitted_turn_id or not precommitted_voice_turn_id
        ):
            raise ValueError("voice_precommitted_turn_invalid")
        streaming = str(mode or "").strip() == "stream"
        client_context = self._resolve_client_protocol_context(payload)
        turn_character_pack_id = self._resolve_payload_character_pack_id(payload)
        actor_stable_id, actor_display_name = self._resolve_turn_actor(payload)
        turn_domain_profile_id = self._resolve_turn_domain_profile(payload)
        payload = dict(payload)
        message_addressing = self._normalize_message_addressing(
            payload,
            fallback_mode="current_request",
        )
        forward_references = self._normalize_forward_references(payload)
        speculative_voice_candidate = bool(payload.get("voice_speculative_candidate"))
        payload.pop("finance_mode", None)
        payload.pop("prompt_scope", None)
        payload["domain_profile"] = turn_domain_profile_id
        turn_kind = str(payload.get("turn_kind") or "").strip().lower()
        prompt_scope = turn_kind if turn_kind in {"qq_attention", "qq_optional_reply"} else ""
        attention_reference_source_ids = [
            str(item or "").strip()
            for item in list(payload.pop("memory_attention_reference_source_ids", []) or [])
            if str(item or "").strip()
        ]
        if prompt_scope != "qq_attention":
            attention_reference_source_ids = []
        else:
            attention_reference_source_ids = list(dict.fromkeys(attention_reference_source_ids))
        payload.pop("plugin_stable_system_context", None)
        turn_control_id = str(payload.pop("_turn_control_id", "") or "").strip()
        plugin_external_event = self._pop_plugin_external_event(
            payload,
            event_allowed=turn_kind == "plugin_event",
        )
        turn_resource_manifest = self._resolve_turn_resource_manifest(payload, client_context)
        chat_model_override = str(payload.get("chat_model_override") or "").strip()
        trace_id = str(payload.get("trace_id") or f"{PERSONA.trace_prefix}_{uuid.uuid4().hex[:12]}")
        session_id = str(payload.get("user_id") or payload.get("session_id") or "default_session")
        profile_user_id = str(payload.get("real_user_id") or session_id)
        user_memory_source_id = self._pop_user_memory_source_id(
            payload,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=turn_character_pack_id,
            memory_role=(
                f"event.{plugin_external_event['event_type']}" if plugin_external_event is not None else "user"
            ),
        )
        user_message = str(payload.get("message") or "").strip()
        memory_user_message = str(payload.get("memory_message") or user_message).strip()
        if plugin_external_event is not None:
            memory_user_message = self._render_external_event_memory_content(plugin_external_event)
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
        if _voice_playback_context:
            turn_extra_user_context = self._merge_extra_user_context(turn_extra_user_context, _voice_playback_context)
        if client_context.effective_mode == ClientMode.DESKTOP_PET and "current_attachment_ids" in payload:
            attachments = desktop_pet_engine.prepare_desktop_turn_attachments(
                self, profile_user_id=profile_user_id, session_id=session_id,
                character_pack_id=turn_character_pack_id,
                attachment_ids=payload.get("current_attachment_ids"),
                chat_model_override=chat_model_override,
            )
            payload["current_attachment_ids"] = attachments["attachment_ids"]
            payload["native_user_images"] = attachments["images"]
            turn_extra_user_context = self._merge_extra_user_context(
                turn_extra_user_context, attachments["context"],
            )
        native_user_images = self._extract_native_user_images(payload)
        if native_user_images:
            turn_extra_user_context = self._merge_extra_user_context(
                turn_extra_user_context,
                self._build_native_user_image_prompt_context(native_user_images),
            )
        desktop_screen_images = self._extract_desktop_screen_frame_images(payload)
        if desktop_screen_images:
            turn_extra_user_context = self._merge_extra_user_context(
                turn_extra_user_context,
                self._build_desktop_screen_frame_prompt_context(desktop_screen_images),
            )
        turn_user_images = [*native_user_images, *desktop_screen_images][:5]
        turn_execution_target = self._resolve_turn_execution_target(
            has_real_images=bool(turn_user_images),
            chat_model_override=chat_model_override,
        )
        transient_user_turn = self._is_transient_user_turn(payload) or externally_managed_memcore_turn
        current_input_transient = transient_user_turn and not externally_managed_memcore_turn
        persist_assistant_turn = self._should_persist_assistant_turn(payload) and not externally_managed_memcore_turn
        external_event_turn = plugin_external_event is not None

        if transient_user_turn:
            user_record = self._build_transient_user_record(
                user_message=user_message,
                now_ts=now_ts,
                date_label=date_label,
                time_of_day=time_of_day,
            )
            if externally_managed_memcore_turn:
                user_record.update(
                    {
                        "source_id": precommitted_source_id,
                        "role": "message.user.voice",
                        "kind": "message.user.voice",
                        "semantic_text": user_message,
                        "payload": {
                            "text": user_message,
                            "modality": "voice",
                            "voice_turn_id": precommitted_voice_turn_id,
                        },
                    }
                )
        else:
            user_memory_metadata = (
                self._external_event_memory_metadata(plugin_external_event)
                if plugin_external_event is not None
                else {}
            )
            if message_addressing:
                user_memory_metadata["message_addressing"] = message_addressing
            if forward_references:
                user_memory_metadata["forward_references"] = forward_references
            if payload.get("current_attachment_ids"):
                user_memory_metadata["current_attachment_ids"] = list(payload["current_attachment_ids"])
            user_record = self.store.add_message(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=turn_character_pack_id,
                role=(f"event.{plugin_external_event['event_type']}" if plugin_external_event is not None else "user"),
                content=memory_user_message,
                timestamp=now_ts,
                date_label=date_label,
                time_of_day=time_of_day,
                semantic_tags=extract_semantic_tags(memory_user_message),
                memory_metadata=user_memory_metadata or None,
                source_id=user_memory_source_id,
            )
            source_message_id = str(payload.get("source_message_id") or "").strip()
            if source_message_id:
                user_record["source_message_id"] = source_message_id
            if not self._memcore_owns_compaction():
                self._schedule_summary_cycle(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=turn_character_pack_id,
                )

        target_actor_id, target_actor_display_name = self._apply_message_addressing(
            user_record,
            message_addressing,
        )
        if forward_references:
            user_record["forward_references"] = forward_references
        turn_projection_source_id = str(user_record.get("source_id") or "").strip()
        recent_raw, recent_episodic_summaries, recent_semantic_summaries = self._load_turn_visible_memory(
            session_id=session_id,
            profile_user_id=profile_user_id,
            character_pack_id=turn_character_pack_id,
            user_record=user_record,
            include_transient_user_record=transient_user_turn,
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
            current_user_source_id=turn_projection_source_id,
            verifier_debug_enabled=verifier_debug_enabled,
        )
        router_output = retrieval_pipeline.router_output
        router_timing = retrieval_pipeline.router_timing
        retrieval_result = retrieval_pipeline.retrieval_result
        verifier_output = retrieval_pipeline.verifier_output
        confirmed_snippets = retrieval_pipeline.confirmed_snippets
        verifier_timing = retrieval_pipeline.verifier_timing
        memcore_turn_id = ""
        turn_memcore_failure: dict[str, Any] | None = None
        if externally_managed_memcore_turn:
            memcore_turn_id = precommitted_turn_id
        elif current_input_transient and not speculative_voice_candidate:
            memcore_open = self._begin_memcore_hidden_host_turn(
                referenced_source_ids=attention_reference_source_ids,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=turn_character_pack_id,
            )
            memcore_turn_id = str((memcore_open or {}).get("turn_id") or "").strip()
            hidden_source_id = str((memcore_open or {}).get("source_id") or "").strip()
            if hidden_source_id:
                turn_projection_source_id = hidden_source_id
            turn_memcore_failure = self._memcore_input_turn_failure(memcore_open)
        elif not transient_user_turn:
            user_record = self._apply_user_vector_index_policy(
                user_record=user_record,
                router_output=router_output,
            )
            self._upsert_raw_record(user_record)
            memcore_open = self._begin_memcore_input_turn(
                user_record=user_record,
                external_event=plugin_external_event,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=turn_character_pack_id,
                actor_stable_id=actor_stable_id,
                actor_display_name=actor_display_name,
                target_actor_id=target_actor_id,
                target_actor_display_name=target_actor_display_name,
            )
            memcore_turn_id = str((memcore_open or {}).get("turn_id") or "").strip()
            turn_memcore_failure = self._memcore_input_turn_failure(memcore_open)

        prompt_exclude_source_ids: list[str] = []
        # Request-frozen user text is turn-scoped, not generation-scoped.  A
        # tool continuation rebuilds prompt context, but it must keep the exact
        # current user message that crossed the first provider boundary while
        # appending only new action/result projections after it.
        request_projection_state: dict[str, Any] = {
            # The current stimulus is a turn invariant.  Tool prefaces and
            # observations are appended to ``recent_raw_for_turn`` later, so
            # rediscovering the stimulus from the mutable history tail can
            # lose its source id and abort an otherwise successful tool turn.
            # Transient events have no provider-visible durable stimulus: the
            # event is the request-local user tail, while the hidden
            # host record only owns tools/finalization.  Asking MemCore to
            # project that prompt-invisible record as the current message makes
            # an otherwise valid history read fail with
            # ``current_source_not_projected``.
            "current_user_source_id": "" if current_input_transient else turn_projection_source_id,
            # Keep current-turn event content out of persistent prompt history;
            # its tools and actual assistant reply still belong to a real turn.
            "record_request_projection": not current_input_transient,
            "current_input_transient": current_input_transient,

        }
        if actor_stable_id:
            request_projection_state["actor_stable_id"] = actor_stable_id
        actor_profile_user_id = str(payload.get("actor_profile_user_id") or "").strip()
        if actor_profile_user_id:
            request_projection_state["actor_profile_user_id"] = actor_profile_user_id
        authorization_profile_user_id = str(payload.get("actor_profile_user_id") or "").strip()
        if authorization_profile_user_id:
            request_projection_state["authorization_profile_user_id"] = authorization_profile_user_id
        from .engine_services.turn_context import resolve_qq_actor_relation

        current_actor_relation = resolve_qq_actor_relation(
            client_mode=client_context.effective_mode,
            actor_stable_id=actor_stable_id,
            master_qq=getattr(config, "MASTER_QQ", ""),
        )
        if current_actor_relation:
            request_projection_state["current_actor_relation"] = current_actor_relation
        final_output = yield from self._generate_round(
            mode=mode,
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
            user_images=turn_user_images,
            allow_tool_call=not speculative_voice_candidate,
            final_debug_enabled=final_debug_enabled,
            chat_model_override=chat_model_override,
            execution_target=turn_execution_target,
            prompt_exclude_source_ids=prompt_exclude_source_ids,
            domain_profile_id=turn_domain_profile_id,
            prompt_scope=prompt_scope,
            stable_system_context="",
            request_projection_state=request_projection_state,
        )
        recent_raw_for_turn = list(recent_raw)
        tool_turns: list[dict[str, Any]] = []
        preface_turns: list[dict[str, str]] = []
        tool_result: ToolExecutionResult | None = None
        tool_results: list[ToolExecutionResult] = []
        tool_events: list[dict[str, Any]] = []
        tool_followups: list[str] = []
        tool_history_turns: list[dict[str, Any]] = []
        native_reasoning_by_call_id: dict[str, str] = {}
        provider_continuations: dict[str, dict[str, Any]] = {}
        recorded_tool_call_ids: set[str] = set()
        if speculative_voice_candidate:
            max_tool_rounds = -1
            warning_remaining = 0
        else:
            max_tool_rounds = self._max_tool_rounds(domain_profile_id=turn_domain_profile_id)
            warning_remaining = self._tool_round_warning_remaining(hard_limit=max_tool_rounds)
        tool_round_index = 0
        tool_round_warning_emitted = False
        tool_protocol_attempts = 0
        tool_protocol_retry_limit = max(
            1,
            int(getattr(config, "CHAT_MODEL_DECISION_MAX_ATTEMPTS", 3) or 3),
        )
        provider_output_raw = ""
        memory_exclude_source_ids = [
            str(hit.get("source_id") or "").strip()
            for hit in retrieval_result.get("fused_hits", [])
            if str(hit.get("source_id") or "").strip()
        ]
        # One authoritative hard limit counts only tool batches that were
        # actually executed. Invalid/rejected model decisions have their own
        # bounded retry counter and never consume tool evidence budget.
        deferred_control_snapshot: dict[str, Any] = {}
        while max_tool_rounds == 0 or tool_round_index <= max_tool_rounds:
            control_snapshot: dict[str, Any] = deferred_control_snapshot
            deferred_control_snapshot = {}
            turn_coordinator = getattr(self, "turn_coordinator", None)
            if not control_snapshot and turn_control_id and turn_coordinator is not None:
                control_snapshot = dict(turn_coordinator.drain(turn_control_id) or {})
            if cancellation_requested():
                control_snapshot["stop_requested"] = True
            pending_steers = list(control_snapshot.get("steers") or [])
            steer_attachments = {}
            if client_context.effective_mode == ClientMode.DESKTOP_PET:
                for steer in pending_steers:
                    ids = getattr(steer, "current_attachment_ids", None)
                    if ids is not None:
                        steer_attachments[steer.source_id] = desktop_pet_engine.prepare_desktop_turn_attachments(
                            self, profile_user_id=profile_user_id, session_id=session_id,
                            character_pack_id=turn_character_pack_id, attachment_ids=list(ids),
                            chat_model_override=chat_model_override,
                        )
            if bool(control_snapshot.get("stop_requested")):
                stop_reason = str(control_snapshot.get("stop_reason") or "user_stopped")
                if pending_steers:
                    recorded_before_stop, failed_before_stop = self._record_turn_steering_inputs(
                        prepared_attachments=steer_attachments,
                        steers=pending_steers,
                        turn_id=memcore_turn_id,
                        recent_raw_for_turn=recent_raw_for_turn,
                        profile_user_id=profile_user_id,
                        session_id=session_id,
                        character_pack_id=turn_character_pack_id,
                    )
                    self._settle_turn_steering_receipts(
                        steers=pending_steers,
                        applied_source_ids=recorded_before_stop,
                        failed_source_ids=failed_before_stop,
                    )
                    if streaming:
                        yield {
                            "type": "turn_steer_recorded_before_stop",
                            "count": len(recorded_before_stop),
                            "failed_count": len(failed_before_stop),
                        }
                latest_run_status: dict[str, str] = {}
                for completed_result in tool_results:
                    updates = getattr(completed_result, "state_updates", None)
                    execution = updates.get("capability_execution") if isinstance(updates, dict) else None
                    if not isinstance(execution, dict):
                        continue
                    run_id = str(execution.get("run_id") or "").strip()
                    if run_id:
                        latest_run_status[run_id] = str(execution.get("status") or "").strip().lower()
                cancel_calls = [
                    {"type": "exec_cancel", "run_id": run_id}
                    for run_id, status in latest_run_status.items()
                    if status == "running"
                ]
                cancellation_results: list[ToolExecutionResult] = []
                if cancel_calls:
                    cancellation_results, cancellation_events = self._execute_and_record_tool_batch(
                        tool_calls=cancel_calls,
                        final_output=final_output,
                        provider_output_raw=provider_output_raw,
                        tool_results=tool_results,
                        tool_events=tool_events,
                        tool_followups=tool_followups,
                        tool_turns=tool_turns,
                        recent_raw_for_turn=recent_raw_for_turn,
                        profile_user_id=profile_user_id,
                        session_id=session_id,
                        character_pack_id=turn_character_pack_id,
                        now_ts=int(time.time()),
                        current_user_source_id=turn_projection_source_id,
                        client_context=client_context,
                        memory_exclude_source_ids=memory_exclude_source_ids,
                        request_context={**payload, "_host_visible_tool_history": request_projection_state.get("visible_tool_history", [])},
                        tool_history_turns=tool_history_turns,
                        prompt_exclude_source_ids=prompt_exclude_source_ids,
                        recorded_tool_call_ids=recorded_tool_call_ids,
                        domain_profile_id=turn_domain_profile_id,
                        memcore_turn_id=memcore_turn_id,
                        execution_target=turn_execution_target,
                    )
                    if streaming:
                        for cancellation_event in cancellation_events:
                            yield cancellation_event
                if memcore_turn_id and not externally_managed_memcore_turn:
                    self._abort_memcore_input_turn(
                        turn_id=memcore_turn_id,
                        reason=stop_reason,
                        chat_model_override=chat_model_override,
                        profile_user_id=profile_user_id,
                        session_id=session_id,
                        character_pack_id=turn_character_pack_id,
                    )
                stopped_output = {
                    "status": "stopped",
                    "reason": stop_reason,
                    "speech": "",
                    "speech_segments": [],
                    "tool_events": tool_events,
                    "npc_turns": tool_turns,
                    "execution_cancellations": [
                        dict(getattr(result, "state_updates", {}) or {}) for result in cancellation_results
                    ],
                    "trace_id": trace_id,
                }
                if streaming:
                    yield {"type": "turn_stopped", "reason": stop_reason, "payload": stopped_output}
                return stopped_output
            if pending_steers:
                applied_source_ids, failed_source_ids = self._record_turn_steering_inputs(
                    prepared_attachments=steer_attachments,
                    steers=pending_steers,
                    turn_id=memcore_turn_id,
                    recent_raw_for_turn=recent_raw_for_turn,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=turn_character_pack_id,
                )
                self._settle_turn_steering_receipts(
                    steers=pending_steers,
                    applied_source_ids=applied_source_ids,
                    failed_source_ids=failed_source_ids,
                )
                if streaming:
                    yield {
                        "type": "turn_steer_applied" if applied_source_ids else "turn_steer_failed",
                        "count": len(applied_source_ids),
                        "failed_count": len(failed_source_ids),
                    }
                if applied_source_ids:
                    steer_images = self._steering_native_user_images(
                        pending_steers,
                        applied_source_ids=applied_source_ids,
                    )
                    for source_id in applied_source_ids:
                        attachments = steer_attachments.get(source_id)
                        if attachments is not None:
                            payload["current_attachment_ids"] = attachments["attachment_ids"]
                            steer_images = self._merge_model_image_inputs(attachments["images"], steer_images)
                    if steer_images:
                        turn_user_images = self._merge_model_image_inputs(steer_images, turn_user_images)
                        turn_execution_target = self._upgrade_turn_execution_target_for_images(
                            current_target=turn_execution_target,
                            model_image_inputs=steer_images,
                            chat_model_override=chat_model_override,
                        )
                        turn_extra_user_context = self._merge_extra_user_context(
                            turn_extra_user_context,
                            self._build_native_user_image_prompt_context(steer_images),
                        )
                    tool_protocol_attempts = 0
                    # Steering never closes the tool surface: the next model
                    # decision keeps the same channel permissions and all
                    # completed tool evidence from this open turn.
                    final_output = yield from self._generate_round(
                        mode=mode,
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
                            allow_more=True,
                        ),
                        client_context=client_context,
                        resource_manifest=turn_resource_manifest,
                        character_pack_id=turn_character_pack_id,
                        user_images=turn_user_images,
                        allow_tool_call=True,
                        final_debug_enabled=final_debug_enabled,
                        chat_model_override=chat_model_override,
                        execution_target=turn_execution_target,
                        post_user_turns=tool_history_turns,
                        prompt_exclude_source_ids=prompt_exclude_source_ids,
                        domain_profile_id=turn_domain_profile_id,
                        prompt_scope=prompt_scope,
                        stable_system_context="",
                        request_projection_state=request_projection_state,
                    )
                    continue
            provider_output_raw = str(final_output.pop("_provider_output_raw", "") or "")
            native_reasoning_content = str(final_output.pop(NATIVE_REASONING_CONTENT_FIELD, "") or "").strip()
            provider_continuation = final_output.pop(continuation.RESULT_FIELD, None)
            final_output, tool_calls, rejections = self._prepare_tool_round_decisions(
                final_output=final_output,
                user_message=user_message,
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
                domain_profile_id=turn_domain_profile_id,
            )
            if tool_calls and max_tool_rounds > 0 and tool_round_index >= max_tool_rounds:
                blocked_calls = "；".join(self._describe_tool_call_for_prompt(tool_call) for tool_call in tool_calls)
                tool_followups.append(
                    f"模型在本轮已经执行 {tool_round_index}/{max_tool_rounds} 轮工具后又请求：{blocked_calls}。"
                    "这些额外调用没有执行；上方最后一批工具结果仍是真实结果。"
                )
                logger.warning(
                    "tool_round_hard_limit session=%s rounds=%s blocked=%s",
                    session_id,
                    tool_round_index,
                    len(tool_calls),
                )
                final_output = yield from self._generate_round(
                    mode=mode,
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
                        stop_reason="tool_budget_exhausted",
                    ),
                    client_context=client_context,
                    resource_manifest=turn_resource_manifest,
                    character_pack_id=turn_character_pack_id,
                    user_images=turn_user_images,
                    allow_tool_call=False,
                    final_debug_enabled=final_debug_enabled,
                    chat_model_override=chat_model_override,
                    execution_target=turn_execution_target,
                    post_user_turns=tool_history_turns,
                    prompt_exclude_source_ids=prompt_exclude_source_ids,
                    domain_profile_id=turn_domain_profile_id,
                    prompt_scope=prompt_scope,
                    stable_system_context="",
                    request_projection_state=request_projection_state,
                )
                break
            if streaming:
                native_preface_text = str(final_output.pop("_native_preface_text", "") or "").strip()
                native_preface_streamed = bool(final_output.pop("_native_preface_streamed", False))
                if (
                    native_preface_text
                    and not native_preface_streamed
                    and tool_calls
                    and self._tool_call_allows_assistant_preface(tool_calls[0])
                ):
                    yield {"type": "speech_segment", "index": 0, "text": native_preface_text}
                yield {
                    "type": "assistant_stage_decision",
                    "has_tool_call": bool(tool_calls),
                    "tool_type": str((tool_calls[0] if tool_calls else {}).get("type") or ""),
                    "tool_types": [str(call.get("type") or "") for call in tool_calls],
                    "tool_count": len(tool_calls),
                    "rejected_tool_call": bool(rejections),
                }
            if not tool_calls:
                if not rejections:
                    if turn_control_id and turn_coordinator is not None:
                        finalization = dict(turn_coordinator.begin_finalization(turn_control_id) or {})
                        if finalization.get("stop_requested") or finalization.get("steers"):
                            deferred_control_snapshot = finalization
                            continue
                    break
                self._record_tool_call_rejection(
                    final_output=final_output,
                    rejection="\n".join(rejections),
                    tool_followups=tool_followups,
                    session_id=session_id,
                )
                tool_protocol_attempts += 1
                if tool_protocol_attempts >= tool_protocol_retry_limit:
                    final_output = self._tool_protocol_failure_output(rejections=rejections)
                    break
                final_output = yield from self._generate_round(
                    mode=mode,
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
                        allow_more=True,
                        stop_reason="",
                    ),
                    client_context=client_context,
                    resource_manifest=turn_resource_manifest,
                    character_pack_id=turn_character_pack_id,
                    user_images=turn_user_images,
                    allow_tool_call=True,
                    final_debug_enabled=final_debug_enabled,
                    chat_model_override=chat_model_override,
                    execution_target=turn_execution_target,
                    post_user_turns=tool_history_turns,
                    prompt_exclude_source_ids=prompt_exclude_source_ids,
                    domain_profile_id=turn_domain_profile_id,
                    prompt_scope=prompt_scope,
                    stable_system_context="",
                    request_projection_state=request_projection_state,
                )
                continue
            if rejections:
                tool_followups.extend(rejections)
            # A repeated signature is not an invalid decision. Polling, MCP
            # reload after a restart, a test rerun, or any external state change
            # can make byte-identical arguments produce fresh evidence. Tools
            # that mutate external state own their idempotency/receipt policy;
            # the generic model loop must not silently discard a legal call or
            # convert repetition into a terminal no-tools phase.
            executable_calls = list(tool_calls)
            if isinstance(provider_continuation, dict):
                for call in provider_continuation.get("calls", []):
                    if call.get("id"):
                        provider_continuations[call["id"]] = provider_continuation
            if native_reasoning_content:
                for executable_call in executable_calls:
                    call_id = str(executable_call.get(TOOL_INVOCATION_ID_FIELD) or "").strip()
                    if call_id:
                        native_reasoning_by_call_id[call_id] = native_reasoning_content

            preface_source_id = self._record_assistant_preface_for_tool_call(
                tool_call=executable_calls[0],
                final_output=final_output,
                preface_turns=preface_turns,
                recent_raw_for_turn=recent_raw_for_turn,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=turn_character_pack_id,
                now_ts=now_ts,
                date_label=date_label,
                time_of_day=time_of_day,
                memcore_turn_id=memcore_turn_id,
            )
            if (
                preface_source_id
                and self._tool_call_uses_native_history(executable_calls[0])
                and preface_source_id not in prompt_exclude_source_ids
            ):
                prompt_exclude_source_ids.append(preface_source_id)
            if streaming:
                working_event = self._build_tool_working_stream_event(executable_calls[0])
                if len(executable_calls) > 1:
                    working_event.update(
                        {
                            "phase": "tool_batch",
                            "tool_count": len(executable_calls),
                            "tool_types": [str(call.get("type") or "") for call in executable_calls],
                            "message": "我一起查一下。",
                        }
                    )
                yield working_event
            batch_results, current_events = self._execute_and_record_tool_batch(
                tool_calls=executable_calls,
                final_output=final_output,
                provider_output_raw=provider_output_raw,
                tool_results=tool_results,
                tool_events=tool_events,
                tool_followups=tool_followups,
                tool_turns=tool_turns,
                recent_raw_for_turn=recent_raw_for_turn,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=turn_character_pack_id,
                now_ts=now_ts,
                current_user_source_id=turn_projection_source_id,
                client_context=client_context,
                memory_exclude_source_ids=memory_exclude_source_ids,
                request_context={**payload, "_host_visible_tool_history": request_projection_state.get("visible_tool_history", [])},
                tool_history_turns=tool_history_turns,
                prompt_exclude_source_ids=prompt_exclude_source_ids,
                recorded_tool_call_ids=recorded_tool_call_ids,
                domain_profile_id=turn_domain_profile_id,
                memcore_turn_id=memcore_turn_id,
                execution_target=turn_execution_target,
                native_reasoning_by_call_id=native_reasoning_by_call_id,
                provider_continuations=provider_continuations,
            )
            tool_result = batch_results[-1] if batch_results else None
            tool_protocol_attempts = 0
            if streaming:
                for stream_event in current_events:
                    yield stream_event
            batch_memcore_failure = self._tool_batch_memcore_failure(batch_results)
            if batch_memcore_failure is not None:
                turn_memcore_failure = batch_memcore_failure
            if batch_memcore_failure is None and can_finish_tool_batch(batch_results):
                # Calls/results have already been paired in MemCore. Finish via
                # the existing silent-turn path, not a fabricated model reply.
                final_output = {
                    "emotion": final_output.get("emotion", "normal"),
                    "reply_medium": "text",
                    "speech": "",
                    "speech_segments": [],
                    "tool_call": None,
                    "_deliberate_silence": True,
                    "_tool_finished_turn": True,
                }
                provider_output_raw = ""
                tool_round_index += 1
                # Re-enter the normal finalization boundary so a user steer or
                # stop arriving during the action is not lost.
                continue
            turn_execution_target = self._recompute_turn_execution_target(
                current_target=turn_execution_target,
                tool_results=batch_results,
                chat_model_override=chat_model_override,
            )

            tool_round_index += 1
            hard_budget_reached = max_tool_rounds > 0 and tool_round_index >= max_tool_rounds
            round_control_feedback = ""
            remaining_rounds = (
                max(0, max_tool_rounds - tool_round_index)
                if max_tool_rounds > 0
                else 0
            )
            if (
                max_tool_rounds > 0
                and not hard_budget_reached
                and not tool_round_warning_emitted
                and warning_remaining > 0
                and remaining_rounds <= warning_remaining
            ):
                tool_round_warning_emitted = True
                round_control_feedback = self._build_tool_round_warning(
                    used_rounds=tool_round_index,
                    hard_limit=max_tool_rounds,
                )
                logger.info(
                    "tool_round_warning session=%s used=%s hard=%s remaining=%s",
                    session_id,
                    tool_round_index,
                    max_tool_rounds,
                    remaining_rounds,
                )

            final_output = yield from self._generate_round(
                mode=mode,
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
                    allow_more=not hard_budget_reached,
                    stop_reason="tool_budget_exhausted" if hard_budget_reached else "",
                    round_control_feedback=round_control_feedback,
                ),
                client_context=client_context,
                resource_manifest=turn_resource_manifest,
                character_pack_id=turn_character_pack_id,
                user_images=turn_user_images,
                allow_tool_call=not hard_budget_reached,
                final_debug_enabled=final_debug_enabled,
                chat_model_override=chat_model_override,
                execution_target=turn_execution_target,
                post_user_turns=tool_history_turns,
                prompt_exclude_source_ids=prompt_exclude_source_ids,
                domain_profile_id=turn_domain_profile_id,
                prompt_scope=prompt_scope,
                stable_system_context="",
                request_projection_state=request_projection_state,
            )

        if not speculative_voice_candidate:
            final_output = self._apply_persona_state_to_final_output(
                profile_user_id=profile_user_id,
                session_id=session_id,
                final_output=final_output,
                now_ts=now_ts,
                source_id=turn_projection_source_id,
                tool_result=tool_result,
            )
        if external_event_turn:
            final_output["_provider_output_raw"] = provider_output_raw
            apply_plugin_text_presentation_policy(
                final_output,
                strip_leading_addresses_from=payload.get(
                    "plugin_text_strip_leading_addresses",
                    (),
                ),
            )
            apply_plugin_notification_output_policy(
                final_output,
                {**payload, "plugin_external_event": plugin_external_event},
            )
        self._attach_nonfatal_memcore_failure(final_output, turn_memcore_failure)
        final_output["tool_events"] = tool_events
        final_output["npc_turns"] = tool_turns
        final_output["dialogue_turns"] = self._build_dialogue_turns(
            preface_turn=preface_turns,
            npc_turns=tool_turns,
            final_speech=final_output.get("speech"),
            final_speech_segments=final_output.get("speech_segments"),
            speaker_name=self._resolve_turn_speaker_identity(
                client_context,
                turn_character_pack_id,
            )["assistant_name"],
        )
        if not speculative_voice_candidate:
            self._apply_care_state_request(
                final_output,
                client_context,
                profile_user_id=profile_user_id,
                character_pack_id=turn_character_pack_id,
                payload=payload,
                now_ts=now_ts,
            )
        provider_output_raw = str(final_output.pop("_provider_output_raw", provider_output_raw) or "")
        memory_annotation_status = self._pop_memory_annotation_status(final_output)
        memory_tags = final_output_engine.extract_memory_search_terms(final_output)
        memory_metadata = final_output.get("memory_metadata")
        if not isinstance(memory_metadata, dict):
            memory_metadata = final_output_engine.normalize_memory_metadata(self, None)
        else:
            memory_metadata = dict(memory_metadata)
        final_output["memory_metadata"] = memory_metadata
        final_output.pop("memory_tags", None)
        if not transient_user_turn and not external_event_turn:
            user_record = self._apply_memory_metadata_to_user_record(
                user_record=user_record,
                memory_metadata=memory_metadata,
            )
        if memcore_turn_id and not externally_managed_memcore_turn:
            self._stage_memcore_turn_metadata(
                source_id=turn_projection_source_id,
                memory_metadata=memory_metadata,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=turn_character_pack_id,
                actor_stable_id="" if external_event_turn else actor_stable_id,
                actor_display_name="" if external_event_turn else actor_display_name,
            )
        if memory_tags and not transient_user_turn and not external_event_turn:
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

        persist_assistant_turn = self._should_persist_completed_assistant(
            persist_assistant_turn,
            final_output,
        )
        deliberate_silence = bool(final_output.get("_deliberate_silence"))
        if persist_assistant_turn:
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
            if memcore_turn_id:
                if self._finalize_memcore_input_turn_for_delivery(
                    final_output=final_output,
                    turn_id=memcore_turn_id,
                    assistant_record=assistant_record,
                    memory_metadata=memory_metadata,
                    provider_output_raw=provider_output_raw,
                    chat_model_override=chat_model_override,
                    execution_target=turn_execution_target,
                    annotation_status=memory_annotation_status,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=turn_character_pack_id,
                ):
                    self._schedule_memcore_compaction(
                        profile_user_id=profile_user_id,
                        session_id=session_id,
                        character_pack_id=turn_character_pack_id,
                        chat_model_override=chat_model_override,
                    )
            elif prompt_scope == "qq_attention":
                standalone_result = self._append_memcore_standalone_assistant(
                    assistant_record=assistant_record,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=turn_character_pack_id,
                )
                if standalone_result and bool(standalone_result.get("ok")):
                    self._schedule_memcore_compaction(
                        profile_user_id=profile_user_id,
                        session_id=session_id,
                        character_pack_id=turn_character_pack_id,
                        chat_model_override=chat_model_override,
                    )
                elif standalone_result:
                    self._attach_nonfatal_memcore_failure(
                        final_output,
                        {
                            "status": str(standalone_result.get("status") or "failed"),
                            "reason": "assistant_timeline_append_failed",
                            "detail": self._safe_memcore_failure_code(
                                standalone_result.get("reason"),
                                fallback="standalone_assistant_rejected",
                            ),
                        },
                    )
        elif (
            (deliberate_silence or final_output.get("_notification_suppressed"))
            and memcore_turn_id
            and not externally_managed_memcore_turn
        ):
            # Preserve an actual silent provider envelope; a host-completed
            # action closes without appending any assistant entry. Neither path
            # creates a blank legacy bubble. Tool pairs remain replayable.
            silent_assistant_record = {
                "source_id": f"assistant_silent_{uuid.uuid4().hex}",
                "role": "assistant",
                "content": "",
                "timestamp": int(time.time()),
                "semantic_tags": [],
                "memory_metadata": self._build_assistant_timeline_metadata(final_output),
            }
            if self._finalize_memcore_input_turn_for_delivery(
                final_output=final_output,
                turn_id=memcore_turn_id,
                assistant_record=silent_assistant_record,
                memory_metadata=memory_metadata,
                provider_output_raw=provider_output_raw,
                chat_model_override=chat_model_override,
                execution_target=turn_execution_target,
                annotation_status=memory_annotation_status,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=turn_character_pack_id,
            ):
                self._schedule_memcore_compaction(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=turn_character_pack_id,
                    chat_model_override=chat_model_override,
                )
        elif memcore_turn_id and not externally_managed_memcore_turn:
            self._abort_memcore_input_turn(
                turn_id=memcore_turn_id,
                reason="assistant_turn_not_persisted",
                chat_model_override=chat_model_override,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=turn_character_pack_id,
            )
        if not speculative_voice_candidate and not self._memcore_owns_compaction():
            self._schedule_summary_cycle(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=turn_character_pack_id,
            )

        if persist_assistant_turn:
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

        if streaming:
            ui_final_payload = dict(final_output)
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
        if streaming:
            yield {"type": "final", "payload": final_output}
        return final_output

    def _generate_round(
        self,
        *,
        mode: str,
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
        stable_system_context: str = "",
        client_context: ClientProtocolContext | None = None,
        resource_manifest: ResourceManifest | None = None,
        character_pack_id: str = "",
        user_images: list[dict[str, Any]] | None = None,
        allow_tool_call: bool = True,
        final_debug_enabled: bool | None = None,
        chat_model_override: str = "",
        execution_target: Any = None,
        post_user_turns: list[dict[str, Any]] | None = None,
        prompt_exclude_source_ids: list[str] | None = None,
        domain_profile_id: str = "",
        prompt_scope: str = "",
        request_projection_state: dict[str, Any] | None = None,
    ) -> Generator[dict[str, Any], None, dict[str, Any]]:
        """Dispatch one model generation to the transport implementation.

        The transport pair stays intentionally separate: non-streaming
        ``call_chat_json`` and streaming ``stream_chat_json`` (with its own
        stream→non-stream→uncached degrade chain) are two legitimate transports,
        not a duplicated state machine. This is the single branch point the turn
        mainline uses for every generation round.
        """
        if cancellation_requested():
            return {}
        try:
            if str(mode or "").strip() == "stream":
                return (yield from self._stream_final_response(
                    session_id=session_id,
                    profile_user_id=profile_user_id,
                    user_message=user_message,
                    recent_raw=recent_raw,
                    recent_episodic_summaries=recent_episodic_summaries,
                    recent_semantic_summaries=recent_semantic_summaries,
                    confirmed_snippets=confirmed_snippets,
                    now_ts=now_ts,
                    current_visual_payload=current_visual_payload,
                    extra_user_context=extra_user_context,
                    stable_system_context=stable_system_context,
                    client_context=client_context,
                    resource_manifest=resource_manifest,
                    character_pack_id=character_pack_id,
                    user_images=user_images,
                    allow_tool_call=allow_tool_call,
                    final_debug_enabled=final_debug_enabled,
                    chat_model_override=chat_model_override,
                    execution_target=execution_target,
                    post_user_turns=post_user_turns,
                    prompt_exclude_source_ids=prompt_exclude_source_ids,
                    domain_profile_id=domain_profile_id,
                    prompt_scope=prompt_scope,
                    request_projection_state=request_projection_state,
                ))
            return self._build_final_response(
                session_id=session_id,
                profile_user_id=profile_user_id,
                user_message=user_message,
                recent_raw=recent_raw,
                recent_episodic_summaries=recent_episodic_summaries,
                recent_semantic_summaries=recent_semantic_summaries,
                confirmed_snippets=confirmed_snippets,
                now_ts=now_ts,
                current_visual_payload=current_visual_payload,
                extra_user_context=extra_user_context,
                stable_system_context=stable_system_context,
                client_context=client_context,
                resource_manifest=resource_manifest,
                character_pack_id=character_pack_id,
                user_images=user_images,
                allow_tool_call=allow_tool_call,
                final_debug_enabled=final_debug_enabled,
                chat_model_override=chat_model_override,
                execution_target=execution_target,
                post_user_turns=post_user_turns,
                prompt_exclude_source_ids=prompt_exclude_source_ids,
                domain_profile_id=domain_profile_id,
                prompt_scope=prompt_scope,
                request_projection_state=request_projection_state,
            )
        except ModelRequestCancelled:
            # The core drains stop controls and preserves completed tool receipts.
            return {}

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
                "transient_user_message": True,
                "transient_assistant_message": True,
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
                "transient_user_message": True,
                "transient_assistant_message": True,
                "voice_speculative_candidate": True,
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
        *,
        _precommitted_memcore_turn: dict[str, str] | None = None,
        _voice_playback_context: str = "",
    ) -> Generator[dict[str, Any], None, None]:
        guard_token = _MEMCORE_OPEN_TURN_GUARD.set({})
        exit_reason = "turn_stream_scope_exited_open"
        try:
            from .plugin_invocation_scope import plugin_turn_scope

            with plugin_turn_scope(self):
                yield from self._run_turn_core(
                    payload,
                    mode="stream",
                    _precommitted_memcore_turn=_precommitted_memcore_turn,
                    **({"_voice_playback_context": _voice_playback_context} if _voice_playback_context else {}),
                )
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
                    "matched_terms": [str(term) for term in item.get("matched_terms") or [] if str(term).strip()],
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
        stable_system_context: str = "",
        client_context: ClientProtocolContext | None = None,
        resource_manifest: ResourceManifest | None = None,
        character_pack_id: str = "",
        user_images: list[dict[str, Any]] | None = None,
        allow_tool_call: bool = True,
        final_debug_enabled: bool | None = None,
        chat_model_override: str = "",
        execution_target: Any = None,
        post_user_turns: list[dict[str, Any]] | None = None,
        prompt_exclude_source_ids: list[str] | None = None,
        domain_profile_id: str = "",
        prompt_scope: str = "",
        request_projection_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._is_multimodal_unavailable_target(execution_target):
            return self._multimodal_unavailable_output(
                str(execution_target.get("reason") or "multimodal_model_unavailable")
            )
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
            stable_system_context=stable_system_context,
            client_context=client_context,
            resource_manifest=resource_manifest,
            character_pack_id=character_pack_id,
            allow_tool_call=allow_tool_call,
            final_debug_enabled=final_debug_enabled,
            enable_native_tools=True,
            chat_model_override=chat_model_override,
            execution_target=execution_target,
            post_user_turns=post_user_turns,
            prompt_exclude_source_ids=prompt_exclude_source_ids,
            domain_profile_id=domain_profile_id,
            prompt_scope=prompt_scope,
            current_user_source_id=str(
                (request_projection_state or {}).get("current_user_source_id") or ""
            ).strip(),
            current_input_transient=bool((request_projection_state or {}).get("current_input_transient")),
            current_actor_relation=str(
                (request_projection_state or {}).get("current_actor_relation") or ""
            ).strip(),
            authorization_profile_user_id=str(
                (request_projection_state or {}).get("authorization_profile_user_id") or ""
            ).strip(),
            actor_stable_id=str(
                (request_projection_state or {}).get("actor_stable_id") or ""
            ).strip(),
            actor_profile_user_id=str(
                (request_projection_state or {}).get("actor_profile_user_id") or ""
            ).strip(),
            request_projection_state=request_projection_state,
        )
        projection_failure = generation_context.get("memcore_projection_failure")
        if isinstance(projection_failure, dict):
            return self._memcore_projection_failure_output(projection_failure)
        request_observer = self._build_memcore_request_observer(
            generation_context=generation_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            request_projection_state=request_projection_state,
        )
        max_attempts = self._final_response_max_attempts(generation_context)
        prompt_cache_key = self._final_prompt_cache_key(generation_context)
        original_allow_tool_call = bool(generation_context.get("allow_tool_call", allow_tool_call))
        normalized: dict[str, Any] = {}
        provider_output_raw = ""
        retry_feedback = ""
        transport_failures = 0
        rate_limit_failures = 0
        transport_retry_pending = False
        for attempt in range(1, max_attempts + 1):
            # A malformed decision retries against the same context and tool
            # surface. A legal tool call is a decision, not damaged final text.
            retry_mode = attempt > 1
            metrics_before = self.llm.snapshot_metrics() if hasattr(self.llm, "snapshot_metrics") else {}
            retry_note = (
                self._final_response_retry_note(
                    generation_context,
                    previous_feedback=retry_feedback,
                )
                if retry_mode and not transport_retry_pending
                else ""
            )
            request_kwargs = self._build_final_response_request_kwargs(
                generation_context=generation_context,
                request_projection_state=request_projection_state,
                user_images=user_images,
                chat_model_override=chat_model_override,
                execution_target=execution_target,
                prompt_cache_key=prompt_cache_key,
                retry_note=retry_note,
                request_observer=request_observer,
                allow_tool_call=original_allow_tool_call,
            )
            call_result = (
                self.llm.call_chat_json_result(**request_kwargs) if hasattr(self.llm, "call_chat_json_result") else None
            )
            result = call_result.parsed if call_result is not None else self.llm.call_chat_json(**request_kwargs)
            if "request_observer_rejected:" in str(getattr(call_result, "error", "") or ""):
                return self._memcore_projection_failure_output(
                    {"status": "failed", "reason": "request_projection_record_failed"}
                )
            provider_output_raw = str(getattr(call_result, "raw_text", "") or "")
            call_error = str(getattr(call_result, "error", "") or "").strip()
            if call_error:
                transport_failures += 1
            if "native_tools_unsupported" in call_error:
                self._record_final_service_failure_metric()
                return self._final_service_failure_output(reason="provider_native_tools_unsupported")
            if self._provider_error_is_rate_limited(call_error):
                rate_limit_failures += 1
                if rate_limit_failures >= 2 or attempt >= max_attempts:
                    self._record_final_rate_limit_metric()
                    self._record_final_service_failure_metric()
                    return self._final_service_failure_output(reason="provider_rate_limited")
                transport_retry_pending = True
                if hasattr(self.llm, "record_metric"):
                    self.llm.record_metric("chat_final_rate_limit_retries")
                continue
            if call_error:
                # A host fallback object is not a model decision. Retry based
                # on transport/protocol provenance rather than inspecting the
                # fallback sentence for particular words.
                normalized = {}
                retry_feedback = "provider_error"
                transport_retry_pending = True
                if attempt < max_attempts:
                    if hasattr(self.llm, "record_metric"):
                        self.llm.record_metric("chat_final_response_retries")
                    continue
                break
            transport_retry_pending = False
            metrics_after = self.llm.snapshot_metrics() if hasattr(self.llm, "snapshot_metrics") else {}
            parse_fallback = self._llm_result_used_fallback(
                call_result,
                metrics_before=metrics_before,
                metrics_after=metrics_after,
            )
            normalized = self._normalize_final_output(
                result=result,
                visual_defaults=dict(generation_context["visual_defaults"]),
                profile_user_id=profile_user_id,
                session_id=session_id,
                client_context=client_context,
                resource_manifest=resource_manifest,
                allow_tool_call=original_allow_tool_call,
                debug_enabled=bool(generation_context["debug_enabled"]),
                user_message=user_message,
                domain_profile_id=domain_profile_id,
                capability_selection=generation_context.get(TOOL_CAPABILITY_SELECTION_FIELD),
                allow_deliberate_silence=bool(generation_context.get("allow_deliberate_silence")),
            )
            self._attach_native_reasoning_content(normalized, raw_result=result)
            if parse_fallback:
                # This payload came from the host fallback object.  Its
                # presentation defaults are not model-authored speech.
                normalized["_provider_fallback"] = True
            self._attach_memory_annotation_truth(normalized, result=call_result, raw_result=result)
            self._attach_tool_execution_receipts(normalized, generation_context)
            self._attach_nonfatal_memcore_failure(
                normalized,
                generation_context.get("memcore_projection_recovery")
                or generation_context.get("memcore_request_projection_failure"),
            )
            terminal_output = self._final_attempt_terminal_output(
                normalized=normalized,
                parse_fallback=parse_fallback,
                provider_output_raw=provider_output_raw,
                generation_context=generation_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
                client_context=client_context,
                resource_manifest=resource_manifest,
                user_message=user_message,
                domain_profile_id=domain_profile_id,
            )
            if terminal_output is not None:
                return terminal_output
            retry_feedback = self._final_response_retry_feedback(
                raw_result=result,
                normalized=normalized,
                parse_fallback=parse_fallback,
                provider_output_raw=provider_output_raw,
            )
            self._log_final_response_retry(
                prompt_scope=str(generation_context.get("prompt_scope") or ""),
                attempt=attempt,
                feedback=retry_feedback,
                raw_result=result,
                provider_output_raw=provider_output_raw,
            )
            if attempt < max_attempts and hasattr(self.llm, "record_metric"):
                self.llm.record_metric("chat_final_response_retries")
        # Every structured generation failed. A legal tool call still continues
        # the ordinary tool loop. Otherwise one final same-context plain-text
        # generation (no JSON requirement, no tools) delivers the model's own
        # text; the host only wraps presentation defaults. Only when every
        # provider call was unreachable do we emit an explicit service failure
        # notification instead of pretending the model answered.
        if self._final_output_has_tool_call(normalized):
            if provider_output_raw:
                normalized["_provider_output_raw"] = provider_output_raw
            return normalized
        if (
            self._is_retryable_final_output(normalized)
            or self._raw_tool_call_is_pending(provider_output_raw)
            or retry_feedback == "json_external_text"
        ):
            recovered = self._recover_final_response_plain_text(
                generation_context=generation_context,
                request_projection_state=request_projection_state,
                user_images=user_images,
                chat_model_override=chat_model_override,
                execution_target=execution_target,
                request_observer=request_observer,
                prompt_cache_key=prompt_cache_key,
                profile_user_id=profile_user_id,
                session_id=session_id,
                client_context=client_context,
                resource_manifest=resource_manifest,
                user_message=user_message,
                domain_profile_id=domain_profile_id,
            )
            if recovered is not None:
                self._attach_nonfatal_memcore_failure(
                    recovered,
                    generation_context.get("memcore_projection_recovery")
                    or generation_context.get("memcore_request_projection_failure"),
                )
                return recovered
            if transport_failures >= max_attempts and max_attempts > 0:
                self._record_final_service_failure_metric()
                return self._final_service_failure_output(reason="provider_unreachable")
            normalized["_transient_final_failure"] = True
            normalized.pop("_provider_output_raw", None)
        else:
            if provider_output_raw:
                normalized["_provider_output_raw"] = provider_output_raw
        return normalized

    @staticmethod
    def _final_response_max_attempts(generation_context: dict[str, Any]) -> int:
        del generation_context
        return max(1, int(getattr(config, "CHAT_MODEL_DECISION_MAX_ATTEMPTS", 3) or 3))

    @staticmethod
    def _final_response_retry_note(
        generation_context: dict[str, Any],
        *,
        previous_feedback: str = "",
    ) -> str:
        if previous_feedback == "text_tool_protocol":
            return FINAL_TOOL_PROTOCOL_RECOVERY_FEEDBACK
        if previous_feedback == "json_external_text":
            return FINAL_RESPONSE_EXTERNAL_TEXT_FEEDBACK
        return FINAL_RESPONSE_RECOVERY_FEEDBACK

    @staticmethod
    def _final_response_retry_feedback(
        *,
        raw_result: Any,
        normalized: Any,
        parse_fallback: bool,
        provider_output_raw: str = "",
    ) -> str:
        if AkaneMemoryEngine._output_contains_text_tool_protocol(
            normalized,
            provider_output_raw=provider_output_raw,
        ):
            return "text_tool_protocol"
        if AkaneMemoryEngine._raw_json_has_external_text(provider_output_raw):
            return "json_external_text"
        if parse_fallback:
            return "json_parse_fallback"
        if not isinstance(raw_result, dict):
            return "result_not_object"
        if "speech" not in raw_result:
            return "speech_missing"
        raw_speech = raw_result.get("speech")
        if not isinstance(raw_speech, str):
            return "speech_wrong_type"
        if not raw_speech.strip():
            return "speech_empty"
        if not isinstance(normalized, dict) or not str(normalized.get("speech") or "").strip():
            return "speech_unusable"
        return "speech_not_deliverable"

    @staticmethod
    def _text_contains_tool_protocol(value: Any) -> bool:
        return bool(_TEXT_TOOL_PROTOCOL_RE.search(str(value or "")))

    @staticmethod
    def _output_contains_text_tool_protocol(
        output: Any,
        *,
        provider_output_raw: str = "",
    ) -> bool:
        speech = output.get("speech") if isinstance(output, dict) else ""
        return AkaneMemoryEngine._text_contains_tool_protocol(speech) or (
            AkaneMemoryEngine._text_contains_tool_protocol(provider_output_raw)
        )

    @staticmethod
    def _raw_json_has_external_text(raw: Any) -> bool:
        """Return true when valid JSON is surrounded by model-authored prose.

        Silently extracting the object is lossy: the surrounding prose can be
        the real task report while ``speech`` is only a short summary. A plain
        object, or an object wrapped only in a Markdown JSON fence, is valid.
        Other prefix/suffix content gets one same-context repair turn.
        """

        text = str(raw or "").strip()
        if not text:
            return False
        try:
            return not isinstance(json.loads(text), dict)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", text):
            start = match.start()
            try:
                payload, consumed = decoder.raw_decode(text[start:])
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            prefix = text[:start].strip().lower()
            suffix = text[start + consumed :].strip().lower()
            return prefix not in {"", "```", "```json"} or suffix not in {"", "```"}
        return False

    def _build_final_response_request_kwargs(
        self,
        *,
        generation_context: dict[str, Any],
        request_projection_state: dict[str, Any] | None,
        user_images: list[dict[str, Any]] | None,
        chat_model_override: str,
        execution_target: Any,
        prompt_cache_key: str,
        retry_note: str,
        request_observer: Any,
        allow_tool_call: bool,
    ) -> dict[str, Any]:
        """Build one final-response provider request.

        A retry request keeps the original user message, images,
        MemCore timeline, completed tool results, stable system prefix and
        cache key; host feedback is appended only at the tail. Retry does not
        change tool permissions. Only an explicit caller decision such as a
        consumed hard budget sets allow_tool_call=False.
        """
        user_prompt = self._request_projection_user_prompt(
            request_projection_state,
            str(generation_context["user_prompt"]),
        )
        if retry_note and request_observer is None:
            user_prompt = f"{user_prompt}\n\n{retry_note}"
        kwargs = {
            "system_prompt": str(generation_context["system_prompt"]),
            "user_prompt": user_prompt,
            "fallback": dict(generation_context["fallback"]),
            "temperature": FINAL_RESPONSE_TEMPERATURE,
            "prompt_cache_key": prompt_cache_key,
            "user_images": user_images,
            "system_extra_blocks": generation_context.get("system_extra_blocks"),
            "history_turns": generation_context.get("history_turns"),
            "ephemeral_turns": self._final_response_retry_ephemeral_turns(
                generation_context=generation_context,
                retry_note=retry_note,
                request_observer=request_observer,
            ),
            "post_user_turns": generation_context.get("post_user_turns"),
            "prompt_audit_sections": generation_context.get("prompt_audit_sections"),
            "chat_model_override": chat_model_override,
            "execution_target": execution_target,
        }
        # Keep the native tool schema byte-stable for the whole open turn.
        # A consumed hard budget changes only the decision policy; removing
        # schemas here would invalidate the provider cache prefix and switch
        # the model onto a different wire contract at the most fragile point.
        native_tools = generation_context.get("native_tools")
        kwargs["native_tools"] = native_tools
        kwargs["native_tool_choice"] = (
            "none"
            if native_tools and not allow_tool_call
            else generation_context.get("native_tool_choice", "")
        )
        if request_observer is not None:
            kwargs["request_observer"] = request_observer
        return kwargs

    @staticmethod
    def _final_output_has_tool_call(output: Any) -> bool:
        if not isinstance(output, dict):
            return False
        return bool(
            output.get("tool_call")
            or output.get(NATIVE_TOOL_CALL_FIELD)
            or output.get(NATIVE_TOOL_CALLS_FIELD)
        )

    @staticmethod
    def _raw_tool_call_is_pending(raw: str) -> bool:
        """True when the wire text carries a non-null `tool_call` intent.

        A final answer is allowed to omit the optional `tool_call` field or
        write an explicit `tool_call: null`; only an explicit non-null tool
        call blocks plain-text/parse recovery so a tool preface is never
        mistaken for a final answer.
        """
        text = str(raw or "")
        if re.search(r'["\']tool_call["\']\s*:\s*null\b', text, flags=re.IGNORECASE):
            return False
        return bool(re.search(r'["\']tool_call["\']\s*:', text, flags=re.IGNORECASE))

    def _final_attempt_terminal_output(
        self,
        *,
        normalized: dict[str, Any],
        parse_fallback: bool,
        provider_output_raw: str,
        generation_context: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        client_context: ClientProtocolContext | None,
        resource_manifest: ResourceManifest | None,
        user_message: str,
        domain_profile_id: str,
    ) -> dict[str, Any] | None:
        """Classify one generation attempt as a tool decision or delivery.

        Returns a deliverable normalized dict, or `None` when the attempt
        must be retried with the same-context host feedback. Shared by the
        streaming and non-streaming transports so both surfaces agree on the
        terminal semantics:
        - complete plain text without JSON structure and without a pending
          tool call is wrapped directly as `speech` with default
          presentation fields;
        - a legal tool call continues the ordinary tool loop (never recovery);
        - complete model speech is delivered (parse recovery included);
        Tool availability is controlled only by the caller's explicit
        allow_tool_call decision, never by the fact that this is a retry.
        """
        if self._output_contains_text_tool_protocol(
            normalized,
            provider_output_raw=provider_output_raw,
        ):
            return None
        if not self._final_output_has_tool_call(normalized) and self._raw_json_has_external_text(
            provider_output_raw
        ):
            return None
        if not self._final_output_has_tool_call(normalized):
            wrapped = self._wrap_plain_text_final_speech(
                provider_output_raw=provider_output_raw,
                parse_fallback=parse_fallback,
                generation_context=generation_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
                client_context=client_context,
                resource_manifest=resource_manifest,
                user_message=user_message,
                domain_profile_id=domain_profile_id,
            )
            if wrapped is not None:
                return wrapped
        if parse_fallback:
            if self._is_deliverable_parse_recovery(
                normalized,
                parse_fallback=True,
                provider_output_raw=provider_output_raw,
            ):
                self._record_final_response_parse_recovery_metric()
                normalized.pop("_provider_fallback", None)
                if provider_output_raw:
                    normalized["_provider_output_raw"] = provider_output_raw
                return normalized
            # The host fallback speech is not a model answer.  Retry unless
            # the provider wire text proves the complete speech above.
            return None
        if self._final_output_has_tool_call(normalized):
            if provider_output_raw:
                normalized["_provider_output_raw"] = provider_output_raw
            return normalized
        if not self._is_retryable_final_output(normalized):
            if parse_fallback and normalized.get("_deliberate_silence"):
                return None
            # A complete speech extracted from malformed wire text is still a
            # tool preface when the wire carries a non-null tool call. It must
            # never be delivered as the final answer; same-context regeneration
            # replaces it.
            if parse_fallback and self._raw_tool_call_is_pending(provider_output_raw):
                return None
            if provider_output_raw:
                normalized["_provider_output_raw"] = provider_output_raw
            return normalized
        return None

    def _wrap_plain_text_final_speech(
        self,
        *,
        provider_output_raw: str,
        parse_fallback: bool,
        generation_context: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        client_context: ClientProtocolContext | None,
        resource_manifest: ResourceManifest | None,
        user_message: str,
        domain_profile_id: str,
    ) -> dict[str, Any] | None:
        """Wrap complete plain-text provider output as the final speech.

        Only when the wire text is complete ordinary text with no JSON
        structure (and therefore no pending tool call) is the model's own text
        packaged as `speech`; presentation fields use the defaults. Damaged
        JSON fragments are never wrapped here — they go to same-context
        regeneration instead.
        """
        raw = str(provider_output_raw or "").strip()
        if not raw or not parse_fallback or _JSON_FRAGMENT_RE.search(raw):
            return None
        if self._text_contains_tool_protocol(raw):
            return None
        text = raw
        if text.startswith("```") and text.endswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3:
                text = "\n".join(lines[1:-1]).strip()
        if not text:
            return None
        wrapped = self._normalize_final_output(
            result={"speech": text},
            visual_defaults=dict(generation_context["visual_defaults"]),
            profile_user_id=profile_user_id,
            session_id=session_id,
            client_context=client_context,
            resource_manifest=resource_manifest,
            allow_tool_call=False,
            debug_enabled=bool(generation_context["debug_enabled"]),
            user_message=user_message,
            domain_profile_id=domain_profile_id,
            capability_selection=generation_context.get(TOOL_CAPABILITY_SELECTION_FIELD),
            allow_deliberate_silence=bool(generation_context.get("allow_deliberate_silence")),
        )
        wrapped["_provider_output_raw"] = raw
        wrapped["_final_recovery"] = {"kind": "plain_text_wrap"}
        # The model authored only the text on this recovery path. Keep the
        # normalized emotion for clients that require a visual state, but mark
        # it as a presentation default so QQ does not publish it as if the
        # model had deliberately chosen that semantic expression.
        wrapped["_emotion_model_authored"] = False
        return wrapped

    def _recover_final_response_plain_text(
        self,
        *,
        generation_context: dict[str, Any],
        request_projection_state: dict[str, Any] | None,
        user_images: list[dict[str, Any]] | None,
        chat_model_override: str,
        execution_target: Any,
        request_observer: Any,
        prompt_cache_key: str,
        profile_user_id: str,
        session_id: str,
        client_context: ClientProtocolContext | None,
        resource_manifest: ResourceManifest | None,
        user_message: str,
        domain_profile_id: str,
        delivered_speech_segments: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """One same-context plain-text generation after structured failures.

        No JSON requirement and no tools; the model authors the text and the
        host only wraps presentation defaults. Returns `None` when the
        provider is unavailable, lacks this capability, or still produced no
        usable text.
        """
        call = getattr(self.llm, "call_chat_text", None)
        if not callable(call):
            return None
        note = self._stream_final_response_recovery_feedback(
            delivered_speech_segments,
            plain_text=True,
        )
        user_prompt = self._request_projection_user_prompt(
            request_projection_state,
            str(generation_context["user_prompt"]),
        )
        if request_observer is None:
            user_prompt = f"{user_prompt}\n\n{note}"
        try:
            result = call(
                system_prompt=str(generation_context["system_prompt"]),
                user_prompt=user_prompt,
                temperature=FINAL_RESPONSE_TEMPERATURE,
                prompt_cache_key=prompt_cache_key,
                user_images=user_images,
                system_extra_blocks=generation_context.get("system_extra_blocks"),
                history_turns=generation_context.get("history_turns"),
                ephemeral_turns=self._final_response_retry_ephemeral_turns(
                    generation_context=generation_context,
                    retry_note=note if request_observer is not None else "",
                    request_observer=request_observer,
                ),
                post_user_turns=generation_context.get("post_user_turns"),
                prompt_audit_sections=generation_context.get("prompt_audit_sections"),
                chat_model_override=chat_model_override,
                request_observer=request_observer,
                execution_target=execution_target,
            )
        except Exception as exc:
            logger.warning(
                "final response plain text recovery failed error_type=%s",
                exc.__class__.__name__,
            )
            return None
        speech = self._extract_final_plain_text_speech(getattr(result, "text", ""))
        if not speech or self._text_contains_tool_protocol(speech):
            return None
        normalized = self._normalize_final_output(
            result={"speech": speech},
            visual_defaults=dict(generation_context["visual_defaults"]),
            profile_user_id=profile_user_id,
            session_id=session_id,
            client_context=client_context,
            resource_manifest=resource_manifest,
            allow_tool_call=False,
            debug_enabled=bool(generation_context["debug_enabled"]),
            user_message=user_message,
            domain_profile_id=domain_profile_id,
            capability_selection=generation_context.get(TOOL_CAPABILITY_SELECTION_FIELD),
            allow_deliberate_silence=bool(generation_context.get("allow_deliberate_silence")),
        )
        raw_text = str(getattr(result, "raw_text", "") or "") or speech
        error = str(getattr(result, "error", "") or "").strip()
        if raw_text:
            normalized["_provider_output_raw"] = raw_text
        normalized["_final_recovery"] = {
            "kind": "plain_text",
            **({"error": error} if error else {}),
        }
        # call_chat_text has no structured emotion channel. The normalizer
        # supplies a presentation default, which must not be mistaken for a
        # model-authored semantic expression by delivery surfaces.
        normalized["_emotion_model_authored"] = False
        self._record_final_plain_text_recovery_metric()
        return normalized

    @staticmethod
    def _stream_final_response_recovery_feedback(
        delivered_segments: list[str] | None,
        *,
        plain_text: bool = False,
    ) -> str:
        segments = [str(item or "").strip() for item in (delivered_segments or []) if str(item or "").strip()]
        if not segments:
            return FINAL_RESPONSE_PLAIN_TEXT_FEEDBACK if plain_text else FINAL_RESPONSE_RECOVERY_FEEDBACK
        delivered = "\n".join(f"- {item}" for item in segments)
        feedback = (
            "【宿主反馈】\n"
            "上一条输出没有形成有效的工具调用或完整交付，但以下从 speech 中解析出的完整句子已经提交到本轮交付通道，"
            "用户可能已经看到或听到，不能安全撤回。"
            "不要重复、改写或否认它们。\n"
            "请基于同一用户请求和已经存在的工具结果继续：仍需操作就发出合法工具调用，"
            "任务已完成就只补充尚未交付的剩余答复；不要重复已完成的工具，"
            "不要讨论这次格式或传输错误。\n"
            f"【已交付句子】\n{delivered}"
        )
        if plain_text:
            feedback += "\n本次请直接输出纯文本剩余答复：不要输出 JSON，不要调用任何工具。"
        return feedback

    def _merge_streamed_speech_into_final(
        self,
        output: dict[str, Any],
        *,
        delivered_segments: list[str],
    ) -> dict[str, Any]:
        """Rebuild canonical speech from its delivered prefix and recovery tail.

        ``delivered_segments`` are parser-derived boundaries from the model's
        earlier ``speech`` value, not an independent content authority.
        """
        segments = [str(item or "").strip() for item in delivered_segments if str(item or "").strip()]
        if not segments:
            return output
        normalized = dict(output or {})
        final_speech = str(normalized.get("speech") or "").strip()
        delivered_text = "\n".join(segments)
        compact_delivered = "".join(delivered_text.split())
        compact_final = "".join(final_speech.split())
        if compact_delivered and compact_final.startswith(compact_delivered):
            return normalized
        merged_text = "\n".join([delivered_text, final_speech] if final_speech else [delivered_text])
        speech, speech_segments = self._normalize_speech_payload(
            speech=merged_text,
            fallback_to_default=False,
        )
        normalized["speech"] = speech
        normalized["speech_segments"] = speech_segments
        return normalized

    @staticmethod
    def _extract_final_plain_text_speech(text: Any) -> str:
        """Best-effort speech extraction from the plain-text recovery response.

        Accepts the whole text when it carries no JSON structure; a stray JSON
        object with a usable `speech` string is also honored. Anything that
        still looks like damaged JSON is rejected so the host never fabricates
        or mangles an answer.
        """
        candidate = str(text or "").strip()
        if not candidate:
            return ""
        if candidate.startswith("```") and candidate.endswith("```"):
            lines = candidate.splitlines()
            if len(lines) >= 3:
                candidate = "\n".join(lines[1:-1]).strip()
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, dict):
            speech = str(parsed.get("speech") or "").strip()
            if speech:
                return speech
        if _JSON_FRAGMENT_RE.search(candidate):
            return ""
        return candidate

    def _record_final_plain_text_recovery_metric(self) -> None:
        record = getattr(self.llm, "record_metric", None)
        if callable(record):
            record("chat_final_plain_text_recoveries")

    def _record_final_service_failure_metric(self) -> None:
        record = getattr(self.llm, "record_metric", None)
        if callable(record):
            record("chat_final_service_failures")

    def _record_final_rate_limit_metric(self) -> None:
        record = getattr(self.llm, "record_metric", None)
        if callable(record):
            record("chat_final_rate_limited")

    @staticmethod
    def _provider_error_is_rate_limited(error: Any) -> bool:
        text = str(error or "").strip().lower()
        if not text:
            return False
        return bool(re.search(r"\b429\b", text)) or any(
            marker in text
            for marker in (
                "upstream_rate_limited",
                "ratelimiterror",
                "rate limit",
                "rate_limit",
                "http 429",
                "status 429",
                "status_code=429",
                "status_code: 429",
            )
        )

    @staticmethod
    def _final_service_failure_output(*, reason: str = "") -> dict[str, Any]:
        """Structured service-failure terminal state.

        Only emitted when every provider call in the turn was unreachable;
        never pretends the model answered.
        """
        return {
            "emotion": "concerned",
            "speech": "我这边服务暂时不可用，这次没能完成回复。请稍后再试一次。",
            "tool_call": None,
            "memory_metadata": {},
            "_transient_final_failure": True,
            "_service_failure": {
                "status": "unavailable",
                "reason": str(reason or "provider_unreachable")[:80],
            },
        }

    def _record_final_response_parse_recovery_metric(self) -> None:
        record = getattr(self.llm, "record_metric", None)
        if callable(record):
            record("chat_final_response_parse_recoveries")

    def _is_deliverable_parse_recovery(
        self,
        output: Any,
        *,
        parse_fallback: bool,
        provider_output_raw: str,
    ) -> bool:
        """Accept only complete model-authored speech from malformed JSON.

        The streaming parser exposes ``speech`` only after its JSON string value
        is closed. We still require proof that no tool call remains pending.
        Disabling tools in the request does not turn a model-authored non-null
        compatibility tool_call into final speech; that preface must be retried
        rather than delivered as if the requested action happened.
        """

        if not parse_fallback or not isinstance(output, dict):
            return False
        if output.get("_deliberate_silence"):
            # A damaged JSON fragment does not prove completion of the compact
            # {"speech":""} decision protocol.
            return False
        if output.get("tool_call") or output.get(NATIVE_TOOL_CALL_FIELD) or output.get(NATIVE_TOOL_CALLS_FIELD):
            return False
        if self._is_retryable_final_output(output):
            # The provider-fallback marker is expected here.  Empty or
            # otherwise unusable speech remains retryable.
            if not output.get("_provider_fallback") or not str(output.get("speech") or "").strip():
                return False
        speech = str(output.get("speech") or "").strip()
        raw = str(provider_output_raw or "")
        encoded_speech = {
            json.dumps(speech, ensure_ascii=False)[1:-1],
            json.dumps(speech, ensure_ascii=True)[1:-1],
        }
        if not raw.strip() or not any(candidate and candidate in raw for candidate in encoded_speech):
            return False
        # A final answer is allowed to omit the optional ``tool_call`` field.
        # Only reject recovery when the wire text explicitly contains a
        # non-null tool call; requiring an explicit ``tool_call: null`` turned
        # ordinary short replies into false "undeliverable" failures.
        return not self._raw_tool_call_is_pending(provider_output_raw)

    @staticmethod
    def _log_final_response_retry(
        *,
        prompt_scope: str,
        attempt: int,
        feedback: str,
        raw_result: Any,
        provider_output_raw: str,
    ) -> None:
        raw_keys = sorted(str(key)[:80] for key in raw_result)[:24] if isinstance(raw_result, dict) else []
        raw_text = str(provider_output_raw or "")
        logger.warning(
            "final response unusable prompt_scope=%s attempt=%s reason=%s "
            "raw_type=%s raw_keys=%s raw_chars=%s raw_sha256=%s",
            str(prompt_scope or "default")[:80],
            attempt,
            feedback,
            type(raw_result).__name__,
            raw_keys,
            len(raw_text),
            hashlib.sha256(raw_text.encode("utf-8", errors="ignore")).hexdigest()[:16] if raw_text else "",
        )

    @staticmethod
    def _final_response_retry_ephemeral_turns(
        *,
        generation_context: dict[str, Any],
        retry_note: str,
        request_observer: Any,
    ) -> list[dict[str, Any]] | None:
        original = generation_context.get("ephemeral_turns")
        turns = [dict(turn) for turn in list(original or []) if isinstance(turn, dict)]
        # A MemCore request observer freezes the persistent current user
        # message.  Mutating that message on retry would make the provider
        # request diverge from the recorded projection, but omitting the repair
        # instruction entirely just repeats the same failed request.  Put the
        # repair instruction in a request-scoped tail turn instead: the
        # append-only history and cacheable prefix stay identical, while the
        # model can see exactly what must be repaired.
        if retry_note and request_observer is not None:
            turns.append({"role": "user", "content": retry_note})
        if turns:
            return turns
        return [] if isinstance(original, list) else None

    @staticmethod
    def _final_prompt_cache_key(generation_context: dict[str, Any]) -> str:
        """Bucket prompts only by stable routing identity.

        Persona reference/state text may change within one character as the
        current message selects context-library material. Keep those volatile
        suffixes out of the routing key. Tool readiness, selected schemas and
        capability disclosures are also request-time state: putting them in the
        routing key sent consecutive turns to different provider cache buckets.
        ``prompt_scope`` remains available to delivery, retry and audit logic,
        but it does not define a second provider cache family. Ordinary turns
        and proactive external events in one conversation share one MemCore
        timeline, so the provider remains authoritative for their exact common
        prefix matching.
        """

        system_prompt = str(generation_context.get("system_prompt") or "")
        stable_system_prefix = system_prompt.split(CURRENT_ASSISTANT_STATE_MARKER, 1)[0].rstrip()
        fallback = generation_context.get("fallback")
        persona = fallback.get("persona") if isinstance(fallback, dict) else None
        stable_payload = {
            "cache_layout_version": FINAL_PROMPT_CACHE_LAYOUT_VERSION,
            "system_prefix": stable_system_prefix,
            "conversation_scope": str(generation_context.get("prompt_cache_scope_hash") or ""),
            "persona_active": str(persona.get("active") or "") if isinstance(persona, dict) else "",
            "prompt_profile": generation_context.get("prompt_profile") or {},
            "domain_profile": generation_context.get("domain_profile") or {},
            "stable_system_context_hash": str(generation_context.get("stable_system_context_hash") or ""),
        }
        canonical = json.dumps(stable_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8", errors="ignore")).hexdigest()[:20]
        return f"chat:final:{digest}"

    def _is_retryable_final_output(self, output: Any) -> bool:
        """True when the normalized output carries no deliverable final speech.

        A parse fallback no longer forces a retry by itself: complete model
        speech extracted from malformed wire text is deliverable. Pending tool
        calls are not retryable here either — they continue the ordinary tool
        loop. Retry count alone never changes tool permissions.
        """
        if not isinstance(output, dict):
            return True
        if output.get("_provider_fallback"):
            return True
        if output.get("tool_call") or output.get(NATIVE_TOOL_CALL_FIELD) or output.get(NATIVE_TOOL_CALLS_FIELD):
            return False
        if output.get("_deliberate_silence"):
            return False
        text = str(output.get("speech") or "").strip()
        if not text:
            return True
        if self._text_contains_tool_protocol(text):
            return True
        return False

    @staticmethod
    def _llm_result_used_fallback(
        result: Any,
        *,
        metrics_before: dict[str, Any],
        metrics_after: dict[str, Any],
    ) -> bool:
        explicit = getattr(result, "fallback_used", None)
        if explicit is not None:
            return bool(explicit)
        return int(metrics_after.get("chat_json_fallbacks", 0) or 0) > int(
            metrics_before.get("chat_json_fallbacks", 0) or 0
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
        stable_system_context: str = "",
        client_context: ClientProtocolContext | None = None,
        resource_manifest: ResourceManifest | None = None,
        character_pack_id: str = "",
        user_images: list[dict[str, Any]] | None = None,
        allow_tool_call: bool = True,
        final_debug_enabled: bool | None = None,
        chat_model_override: str = "",
        execution_target: Any = None,
        post_user_turns: list[dict[str, Any]] | None = None,
        prompt_exclude_source_ids: list[str] | None = None,
        domain_profile_id: str = "",
        prompt_scope: str = "",
        request_projection_state: dict[str, Any] | None = None,
    ) -> Generator[dict[str, Any], None, dict[str, Any]]:
        if self._is_multimodal_unavailable_target(execution_target):
            return self._multimodal_unavailable_output(
                str(execution_target.get("reason") or "multimodal_model_unavailable")
            )
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
            stable_system_context=stable_system_context,
            client_context=client_context,
            resource_manifest=resource_manifest,
            character_pack_id=character_pack_id,
            allow_tool_call=allow_tool_call,
            final_debug_enabled=final_debug_enabled,
            enable_native_tools=True,
            chat_model_override=chat_model_override,
            execution_target=execution_target,
            post_user_turns=post_user_turns,
            prompt_exclude_source_ids=prompt_exclude_source_ids,
            domain_profile_id=domain_profile_id,
            prompt_scope=prompt_scope,
            current_user_source_id=str(
                (request_projection_state or {}).get("current_user_source_id") or ""
            ).strip(),
            current_input_transient=bool((request_projection_state or {}).get("current_input_transient")),
            current_actor_relation=str(
                (request_projection_state or {}).get("current_actor_relation") or ""
            ).strip(),
            authorization_profile_user_id=str(
                (request_projection_state or {}).get("authorization_profile_user_id") or ""
            ).strip(),
            actor_stable_id=str(
                (request_projection_state or {}).get("actor_stable_id") or ""
            ).strip(),
            actor_profile_user_id=str(
                (request_projection_state or {}).get("actor_profile_user_id") or ""
            ).strip(),
            request_projection_state=request_projection_state,
        )
        projection_failure = generation_context.get("memcore_projection_failure")
        if isinstance(projection_failure, dict):
            return self._memcore_projection_failure_output(projection_failure)
        request_observer = self._build_memcore_request_observer(
            generation_context=generation_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            request_projection_state=request_projection_state,
        )
        speaker_identity = self._resolve_turn_speaker_identity(
            client_context,
            character_pack_id,
        )
        yield {
            "type": "turn_start",
            "speaker": speaker_identity["assistant_name"],
        }
        max_attempts = self._final_response_max_attempts(generation_context)
        prompt_cache_key = self._final_prompt_cache_key(generation_context)
        original_allow_tool_call = bool(generation_context.get("allow_tool_call", allow_tool_call))
        normalized: dict[str, Any] = {}
        stream_result: Any = None
        streamed_speech_to_user = False
        delivered_speech_segments: list[str] = []
        unrecovered_stream_error = ""
        unrecovered_stream_partial: dict[str, str] = {}
        provider_output_raw = ""
        retry_feedback = ""
        transport_failures = 0
        attempts_made = 0
        unrecovered_nonstream_transport_failure = False
        for attempt in range(1, max_attempts + 1):
            # A malformed decision retries against the same context and tool
            # surface. Streaming prefix de-duplication is transport-only and
            # does not change what the model is allowed to decide.
            retry_mode = attempt > 1
            attempts_made += 1
            metrics_before = self.llm.snapshot_metrics() if hasattr(self.llm, "snapshot_metrics") else {}
            delivered_before_attempt = list(delivered_speech_segments)
            recovery_prefix_keys = ["".join(item.split()) for item in delivered_before_attempt]
            recovery_prefix_index = 0
            recovery_prefix_matching = retry_mode and bool(recovery_prefix_keys)
            retry_note = ""
            if retry_mode:
                retry_note = (
                    self._final_response_retry_note(
                        generation_context,
                        previous_feedback=retry_feedback,
                    )
                    if not delivered_before_attempt
                    else self._stream_final_response_recovery_feedback(delivered_before_attempt)
                )
            request_kwargs = self._build_final_response_request_kwargs(
                generation_context=generation_context,
                request_projection_state=request_projection_state,
                user_images=user_images,
                chat_model_override=chat_model_override,
                execution_target=execution_target,
                prompt_cache_key=prompt_cache_key,
                retry_note=retry_note,
                request_observer=request_observer,
                allow_tool_call=original_allow_tool_call,
            )
            iterator = self.llm.stream_chat_json(
                **request_kwargs,
                early_tool_call_validator=(
                    lambda call: (
                        self._normalize_tool_call(
                            self._with_tool_execution_receipt(call, generation_context),
                            client_context=client_context,
                            profile_user_id=profile_user_id,
                            session_id=session_id,
                            domain_profile_id=domain_profile_id,
                            capability_selection=generation_context.get(TOOL_CAPABILITY_SELECTION_FIELD),
                        )
                        is not None
                    )
                )
                if original_allow_tool_call
                else None,
            )
            attempt_had_speech_chunk = False
            while True:
                try:
                    event = next(iterator)
                except StopIteration as stop:
                    stream_result = stop.value
                    break
                if isinstance(event, dict):
                    # The LLM runtime parses top-level JSON fields incrementally
                    # and emits speech/ui events before the final JSON object is
                    # complete. Forward those events immediately so the HTTP
                    # NDJSON stream is genuinely user-visible streaming. We
                    # still retain the events for the final normalized result;
                    # persistence and retry decisions remain completion-bound.
                    event_type = str(event.get("type") or "")
                    event_text = str(event.get("text") or "")
                    if event_type in {"speech_chunk", "speech_segment"} and self._text_contains_tool_protocol(
                        event_text
                    ):
                        # Provider-private tool syntax is not speech. Terminal
                        # validation below retries with native tools available.
                        continue
                    if event_type == "speech_chunk" and event_text:
                        attempt_had_speech_chunk = True
                    if event_type == "speech_segment":
                        segment_text = str(event.get("text") or "").strip()
                        segment_key = "".join(segment_text.split())
                        if not segment_text or self._is_retryable_final_output(
                            {"speech": segment_text, "tool_call": None}
                        ):
                            continue
                        if recovery_prefix_matching:
                            if (
                                recovery_prefix_index < len(recovery_prefix_keys)
                                and segment_key == recovery_prefix_keys[recovery_prefix_index]
                            ):
                                recovery_prefix_index += 1
                                if recovery_prefix_index >= len(recovery_prefix_keys):
                                    recovery_prefix_matching = False
                                continue
                            # Only an exact leading replay is suppressed. Once
                            # recovery diverges into its new tail, repeated text
                            # is legitimate speech and must remain visible.
                            recovery_prefix_matching = False
                        delivered_speech_segments.append(segment_text)
                        streamed_speech_to_user = True
                    yield event
            metrics_after = self.llm.snapshot_metrics() if hasattr(self.llm, "snapshot_metrics") else {}
            parse_fallback = self._llm_result_used_fallback(
                stream_result,
                metrics_before=metrics_before,
                metrics_after=metrics_after,
            )
            stream_error = str(getattr(stream_result, "error", "") or "").strip()
            stream_rate_limited = self._provider_error_is_rate_limited(stream_error)
            provider_output_raw = str(getattr(stream_result, "raw_text", "") or "")
            if "request_observer_rejected:" in stream_error:
                return self._memcore_projection_failure_output(
                    {"status": "failed", "reason": "request_projection_record_failed"}
                )
            if "native_tools_unsupported" in stream_error:
                self._record_final_service_failure_metric()
                return self._final_service_failure_output(reason="provider_native_tools_unsupported")
            if stream_error:
                transport_failures += 1
                unrecovered_stream_error = stream_error
                unrecovered_stream_partial = {
                    "emotion": str(getattr(stream_result, "latest_emotion", "") or ""),
                    "speech": str(getattr(stream_result, "latest_speech", "") or ""),
                }
            normalized = self._normalize_final_output(
                result=getattr(stream_result, "parsed", None),
                visual_defaults=dict(generation_context["visual_defaults"]),
                profile_user_id=profile_user_id,
                session_id=session_id,
                client_context=client_context,
                resource_manifest=resource_manifest,
                user_message=user_message,
                allow_tool_call=original_allow_tool_call,
                debug_enabled=bool(generation_context["debug_enabled"]),
                domain_profile_id=domain_profile_id,
                capability_selection=generation_context.get(TOOL_CAPABILITY_SELECTION_FIELD),
                allow_deliberate_silence=bool(generation_context.get("allow_deliberate_silence")),
            )
            self._attach_native_reasoning_content(
                normalized,
                raw_result=getattr(stream_result, "parsed", None),
            )
            if parse_fallback:
                normalized["_provider_fallback"] = True
            self._attach_memory_annotation_truth(
                normalized,
                result=stream_result,
                raw_result=getattr(stream_result, "parsed", None),
            )
            self._attach_tool_execution_receipts(normalized, generation_context)
            native_preface_text = str(getattr(stream_result, "native_preface_text", "") or "").strip()
            if native_preface_text:
                normalized["_native_preface_text"] = native_preface_text
            terminal_output = (
                None
                if stream_error
                else self._final_attempt_terminal_output(
                    normalized=normalized,
                    parse_fallback=parse_fallback,
                    provider_output_raw=provider_output_raw,
                    generation_context=generation_context,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    client_context=client_context,
                    resource_manifest=resource_manifest,
                    user_message=user_message,
                    domain_profile_id=domain_profile_id,
                )
            )
            if terminal_output is not None:
                normalized = self._merge_streamed_speech_into_final(
                    terminal_output,
                    delivered_segments=delivered_before_attempt if retry_mode else [],
                )
                if self._final_output_has_tool_call(normalized) and len(delivered_speech_segments) > len(
                    delivered_before_attempt
                ):
                    normalized["_native_preface_streamed"] = True
                unrecovered_stream_error = ""
                unrecovered_stream_partial = {}
                break
            retry_feedback = self._final_response_retry_feedback(
                raw_result=getattr(stream_result, "parsed", None),
                normalized=normalized,
                parse_fallback=parse_fallback,
                provider_output_raw=provider_output_raw,
            )
            if not stream_rate_limited:
                self._log_final_response_retry(
                    prompt_scope=str(generation_context.get("prompt_scope") or ""),
                    attempt=attempt,
                    feedback=retry_feedback,
                    raw_result=getattr(stream_result, "parsed", None),
                    provider_output_raw=provider_output_raw,
                )
            if attempt_had_speech_chunk:
                yield {
                    "type": "speech_reset",
                    "speech": "\n".join(delivered_speech_segments),
                    "attempt": attempt,
                    "reason": "attempt_not_deliverable",
                }
            if stream_error:
                if streamed_speech_to_user:
                    # Complete sentence boundaries already emitted from speech
                    # may be visible. The next structured attempt receives that
                    # exact prefix and only continues the missing tail.
                    if attempt < max_attempts:
                        if hasattr(self.llm, "record_metric"):
                            self.llm.record_metric("chat_final_response_retries")
                        continue
                    break
                if hasattr(self.llm, "record_metric"):
                    self.llm.record_metric("chat_stream_nonstream_fallbacks")
                fallback_metrics_before = self.llm.snapshot_metrics() if hasattr(self.llm, "snapshot_metrics") else {}
                fallback_call_result = (
                    self.llm.call_chat_json_result(**request_kwargs)
                    if hasattr(self.llm, "call_chat_json_result")
                    else None
                )
                fallback_result = (
                    fallback_call_result.parsed
                    if fallback_call_result is not None
                    else self.llm.call_chat_json(**request_kwargs)
                )
                fallback_error = str(getattr(fallback_call_result, "error", "") or "").strip()
                if "request_observer_rejected:" in fallback_error:
                    return self._memcore_projection_failure_output(
                        {"status": "failed", "reason": "request_projection_record_failed"}
                    )
                if "native_tools_unsupported" in fallback_error:
                    self._record_final_service_failure_metric()
                    return self._final_service_failure_output(reason="provider_native_tools_unsupported")
                provider_output_raw = str(getattr(fallback_call_result, "raw_text", "") or "")
                if stream_rate_limited and self._provider_error_is_rate_limited(fallback_error):
                    self._record_final_rate_limit_metric()
                    self._record_final_service_failure_metric()
                    return self._final_service_failure_output(reason="provider_rate_limited")
                fallback_metrics_after = self.llm.snapshot_metrics() if hasattr(self.llm, "snapshot_metrics") else {}
                fallback_parse_failure = self._llm_result_used_fallback(
                    fallback_call_result,
                    metrics_before=fallback_metrics_before,
                    metrics_after=fallback_metrics_after,
                )
                fallback_transport_failure = (
                    bool(fallback_error and fallback_error != "response_truncated")
                    or int(fallback_metrics_after.get("errors", 0) or 0)
                    > int(fallback_metrics_before.get("errors", 0) or 0)
                )
                unrecovered_nonstream_transport_failure = fallback_transport_failure
                normalized = self._normalize_final_output(
                    result=fallback_result,
                    visual_defaults=dict(generation_context["visual_defaults"]),
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    client_context=client_context,
                    resource_manifest=resource_manifest,
                    user_message=user_message,
                    allow_tool_call=original_allow_tool_call,
                    debug_enabled=bool(generation_context["debug_enabled"]),
                    domain_profile_id=domain_profile_id,
                    capability_selection=generation_context.get(TOOL_CAPABILITY_SELECTION_FIELD),
                    allow_deliberate_silence=bool(generation_context.get("allow_deliberate_silence")),
                )
                self._attach_native_reasoning_content(normalized, raw_result=fallback_result)
                self._attach_memory_annotation_truth(
                    normalized,
                    result=fallback_call_result,
                    raw_result=fallback_result,
                )
                if fallback_transport_failure:
                    if hasattr(self.llm, "record_metric"):
                        self.llm.record_metric("chat_stream_uncached_fallbacks")
                    uncached_request_kwargs = {**request_kwargs, "prompt_cache_key": ""}
                    uncached_metrics_before = (
                        self.llm.snapshot_metrics() if hasattr(self.llm, "snapshot_metrics") else {}
                    )
                    uncached_call_result = (
                        self.llm.call_chat_json_result(**uncached_request_kwargs)
                        if hasattr(self.llm, "call_chat_json_result")
                        else None
                    )
                    uncached_result = (
                        uncached_call_result.parsed
                        if uncached_call_result is not None
                        else self.llm.call_chat_json(**uncached_request_kwargs)
                    )
                    uncached_error = str(getattr(uncached_call_result, "error", "") or "").strip()
                    if "request_observer_rejected:" in uncached_error:
                        return self._memcore_projection_failure_output(
                            {"status": "failed", "reason": "request_projection_record_failed"}
                        )
                    if "native_tools_unsupported" in uncached_error:
                        self._record_final_service_failure_metric()
                        return self._final_service_failure_output(reason="provider_native_tools_unsupported")
                    provider_output_raw = str(getattr(uncached_call_result, "raw_text", "") or "")
                    uncached_metrics_after = (
                        self.llm.snapshot_metrics() if hasattr(self.llm, "snapshot_metrics") else {}
                    )
                    fallback_parse_failure = self._llm_result_used_fallback(
                        uncached_call_result,
                        metrics_before=uncached_metrics_before,
                        metrics_after=uncached_metrics_after,
                    )
                    unrecovered_nonstream_transport_failure = (
                        bool(uncached_error and uncached_error != "response_truncated")
                        or int(uncached_metrics_after.get("errors", 0) or 0)
                        > int(uncached_metrics_before.get("errors", 0) or 0)
                    )
                    normalized = self._normalize_final_output(
                        result=uncached_result,
                        visual_defaults=dict(generation_context["visual_defaults"]),
                        profile_user_id=profile_user_id,
                        session_id=session_id,
                        client_context=client_context,
                        resource_manifest=resource_manifest,
                        user_message=user_message,
                        allow_tool_call=original_allow_tool_call,
                        debug_enabled=bool(generation_context["debug_enabled"]),
                        domain_profile_id=domain_profile_id,
                        capability_selection=generation_context.get(TOOL_CAPABILITY_SELECTION_FIELD),
                        allow_deliberate_silence=bool(generation_context.get("allow_deliberate_silence")),
                    )
                    self._attach_native_reasoning_content(normalized, raw_result=uncached_result)
                    self._attach_memory_annotation_truth(
                        normalized,
                        result=uncached_call_result,
                        raw_result=uncached_result,
                    )
                    if not self._is_retryable_final_output(
                        normalized,
                    ) and hasattr(self.llm, "record_metric"):
                        self.llm.record_metric("chat_stream_uncached_recoveries")
                self._attach_tool_execution_receipts(normalized, generation_context)
                if normalized.get(NATIVE_TOOL_CALL_FIELD) or normalized.get(NATIVE_TOOL_CALLS_FIELD):
                    fallback_preface_text = str(normalized.get("speech") or "").strip()
                    if fallback_preface_text:
                        normalized["_native_preface_text"] = fallback_preface_text
                if not fallback_parse_failure and not self._is_retryable_final_output(normalized):
                    unrecovered_stream_error = ""
                    unrecovered_stream_partial = {}
                    if hasattr(self.llm, "record_metric"):
                        self.llm.record_metric("chat_stream_nonstream_recoveries")
                elif hasattr(self.llm, "record_metric"):
                    self.llm.record_metric("chat_stream_nonstream_fallback_failures")
                # A transport-level stream failure gets one equivalent
                # non-stream request. If that also fails, return the existing
                # structured fallback instead of hammering the same upstream
                # with more stream attempts.
                break
            if attempt < max_attempts and hasattr(self.llm, "record_metric"):
                self.llm.record_metric("chat_final_response_retries")
        if unrecovered_stream_error and streamed_speech_to_user:
            recovered = self._recover_final_response_plain_text(
                generation_context=generation_context,
                request_projection_state=request_projection_state,
                user_images=user_images,
                chat_model_override=chat_model_override,
                execution_target=execution_target,
                request_observer=request_observer,
                prompt_cache_key=prompt_cache_key,
                profile_user_id=profile_user_id,
                session_id=session_id,
                client_context=client_context,
                resource_manifest=resource_manifest,
                user_message=user_message,
                domain_profile_id=domain_profile_id,
                delivered_speech_segments=delivered_speech_segments,
            )
            if recovered is not None:
                normalized = self._merge_streamed_speech_into_final(
                    recovered,
                    delivered_segments=delivered_speech_segments,
                )
                unrecovered_stream_error = ""
                unrecovered_stream_partial = {}
        # Events were forwarded as they arrived above. Do not replay them here:
        # replaying would duplicate speech in the UI/TTS pipeline. In the rare
        # case a future stream implementation only returns buffered events,
        # preserve compatibility by forwarding events that were not already
        # emitted (currently all events from this path are emitted immediately).
        if unrecovered_stream_error:
            yield {
                "type": "stream_error",
                "message": unrecovered_stream_error,
                "partial": unrecovered_stream_partial,
            }
        if unrecovered_stream_error:
            # The stream failed and its equivalent non-stream request did not
            # produce a provider-authored, parseable decision. Preserve any
            # already delivered sentence, but never mistake the host fallback
            # object for a successful model reply.
            if unrecovered_nonstream_transport_failure and transport_failures >= attempts_made:
                self._record_final_service_failure_metric()
                normalized = self._final_service_failure_output(reason="provider_unreachable")
            else:
                normalized["_transient_final_failure"] = True
                normalized.pop("_provider_output_raw", None)
        elif self._final_output_has_tool_call(normalized):
            # A legal tool call still continues the ordinary tool loop.
            if provider_output_raw:
                normalized["_provider_output_raw"] = provider_output_raw
        elif (
            self._is_retryable_final_output(normalized)
            or self._raw_tool_call_is_pending(provider_output_raw)
            or retry_feedback == "json_external_text"
        ):
            recovered = self._recover_final_response_plain_text(
                generation_context=generation_context,
                request_projection_state=request_projection_state,
                user_images=user_images,
                chat_model_override=chat_model_override,
                execution_target=execution_target,
                request_observer=request_observer,
                prompt_cache_key=prompt_cache_key,
                profile_user_id=profile_user_id,
                session_id=session_id,
                client_context=client_context,
                resource_manifest=resource_manifest,
                user_message=user_message,
                domain_profile_id=domain_profile_id,
                delivered_speech_segments=delivered_speech_segments,
            )
            if recovered is not None:
                normalized = self._merge_streamed_speech_into_final(
                    recovered,
                    delivered_segments=delivered_speech_segments,
                )
            elif transport_failures >= attempts_made and attempts_made > 0:
                self._record_final_service_failure_metric()
                normalized = self._final_service_failure_output(reason="provider_unreachable")
            else:
                normalized["_transient_final_failure"] = True
                normalized.pop("_provider_output_raw", None)
        else:
            if provider_output_raw:
                normalized["_provider_output_raw"] = provider_output_raw
        self._attach_nonfatal_memcore_failure(
            normalized,
            generation_context.get("memcore_projection_recovery")
            or generation_context.get("memcore_request_projection_failure"),
        )
        return normalized

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
        stable_system_context: str = "",
        client_context: ClientProtocolContext | None = None,
        resource_manifest: ResourceManifest | None = None,
        character_pack_id: str = "",
        allow_tool_call: bool = True,
        final_debug_enabled: bool | None = None,
        enable_native_tools: bool = False,
        chat_model_override: str = "",
        execution_target: Any = None,
        post_user_turns: list[dict[str, Any]] | None = None,
        prompt_exclude_source_ids: list[str] | None = None,
        domain_profile_id: str = "",
        prompt_scope: str = "",
        current_user_source_id: str = "",
        current_input_transient: bool = False,
        current_actor_relation: str = "",
        authorization_profile_user_id: str = "",
        actor_stable_id: str = "",
        actor_profile_user_id: str = "",
        request_projection_state: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from .engine_services.response_builder import prepare_context as _fn

        return _fn(
            self,
            session_id=session_id,
            user_message=user_message,
            recent_raw=recent_raw,
            recent_episodic_summaries=recent_episodic_summaries,
            recent_semantic_summaries=recent_semantic_summaries,
            confirmed_snippets=confirmed_snippets,
            now_ts=now_ts,
            profile_user_id=profile_user_id,
            current_visual_payload=current_visual_payload,
            extra_user_context=extra_user_context,
            stable_system_context=stable_system_context,
            client_context=client_context,
            resource_manifest=resource_manifest,
            character_pack_id=character_pack_id,
            allow_tool_call=allow_tool_call,
            final_debug_enabled=final_debug_enabled,
            enable_native_tools=enable_native_tools,
            chat_model_override=chat_model_override,
            execution_target=execution_target,
            post_user_turns=post_user_turns,
            prompt_exclude_source_ids=prompt_exclude_source_ids,
            domain_profile_id=domain_profile_id,
            prompt_scope=prompt_scope,
            current_user_source_id=current_user_source_id,
            current_input_transient=current_input_transient,
            current_actor_relation=current_actor_relation,
            authorization_profile_user_id=authorization_profile_user_id,
            actor_stable_id=actor_stable_id,
            actor_profile_user_id=actor_profile_user_id,
            request_projection_state=request_projection_state,
        )

    @staticmethod
    def _memcore_projection_failure_output(failure: dict[str, Any]) -> dict[str, Any]:
        raw_detail = str(failure.get("detail") or "").strip()
        detail = (
            AkaneMemoryEngine._safe_memcore_failure_code(raw_detail, fallback="projection_detail_unavailable")
            if raw_detail
            else ""
        )
        return {
            "emotion": "concerned",
            "speech": "这次上下文没有完整衔接成功，我先不在证据有缺口的情况下乱答。请稍后再试。",
            "tool_call": None,
            "memory_metadata": {},
            "_transient_final_failure": True,
            "_memcore_failure": {
                "status": AkaneMemoryEngine._safe_memcore_failure_code(
                    failure.get("status"),
                    fallback="failed",
                )[:40],
                "reason": AkaneMemoryEngine._safe_memcore_failure_code(
                    failure.get("reason"),
                    fallback="projection_unavailable",
                ),
                **({"detail": detail} if detail else {}),
            },
        }

    @staticmethod
    def _multimodal_unavailable_output(reason: str = "") -> dict[str, Any]:
        """Structured non-guessing reply when real images exist but no model
        can consume them.

        Deliberately not a text-model guess: the assistant states the limitation
        instead of pretending to see the image.  The reason stays out of the
        user-facing speech and is only carried in the audit field.
        """
        normalized_reason = str(reason or "multimodal_model_unavailable").strip()
        return {
            "emotion": "neutral",
            "speech": "我这边暂时没有可用的识图模型，直接看不了这张图。你可以先用文字描述一下，或者等识图模型配置好后再发一次。",
            "tool_call": None,
            "memory_metadata": {},
            "_multimodal_unavailable": {
                "status": "unavailable",
                "reason": normalized_reason or "multimodal_model_unavailable",
                "required_modalities": ["image"],
            },
        }

    @staticmethod
    def _is_multimodal_unavailable_target(target: Any) -> bool:
        return isinstance(target, dict) and str(target.get("status") or "") == "unavailable"

    def _build_memcore_request_observer(
        self,
        *,
        generation_context: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        request_projection_state: dict[str, Any] | None = None,
    ) -> Any:
        if isinstance(request_projection_state, dict) and request_projection_state.get(
            "record_request_projection"
        ) is False:
            return None
        if not bool(getattr(self.llm, "supports_request_observer", False)):
            return None
        manager = getattr(self, "memcore_manager", None)
        recorder = getattr(manager, "record_request_projection", None)
        binder = getattr(manager, "bind_request_projection_messages", None)
        projection_read = generation_context.get("memcore_projection_read")
        if not callable(recorder) or not callable(binder) or not isinstance(projection_read, dict):
            return None
        turn_id = str(projection_read.get("current_turn_id") or "").strip()
        current_messages = [
            dict(message)
            for message in list(projection_read.get("current_turn_messages") or [])
            if isinstance(message, dict)
        ]
        if not turn_id or not current_messages:
            return None
        frozen_turn_messages: list[dict[str, Any]] = []
        frozen_request_indexes: list[int] = []

        def observe(request: dict[str, Any]) -> dict[str, Any]:
            nonlocal frozen_turn_messages, frozen_request_indexes
            if not isinstance(request, dict):
                return {"ok": False, "status": "failed", "reason": "request_observation_invalid"}
            if isinstance(request_projection_state, dict):
                from .capability_discovery import visible_tool_result_messages
                request_projection_state["visible_tool_history"] = visible_tool_result_messages(request.get("history_messages", []))
            persistent_messages = [
                dict(message)
                for message in list(request.get("persistent_turn_messages") or [])
                if isinstance(message, dict)
            ]
            if not frozen_turn_messages:
                if not persistent_messages:
                    return {"ok": False, "status": "failed", "reason": "persistent_turn_messages_missing"}
                if len(persistent_messages) != len(current_messages):
                    stale_frozen_turn = bool(current_messages) and all(
                        str(message.get("projection_status") or "").strip().lower() == "request_frozen"
                        for message in current_messages
                    )
                    if not stale_frozen_turn:
                        return {"ok": False, "status": "failed", "reason": "persistent_turn_count_mismatch"}
                    # An idempotently replayed stimulus can resolve to an
                    # already completed/aborted turn.  That stale turn is not
                    # writable, and its request-frozen user/tool/assistant sequence no
                    # longer has the same shape as this delivery attempt.
                    # Preserve the model reply while declining to mutate the
                    # stale projection; normal writable turns still take the
                    # strict record-and-verify path below.
                    logger.warning(
                        "memcore request projection skipped reason=persistent_turn_count_mismatch "
                        "projected_count=%s request_count=%s",
                        len(current_messages),
                        len(persistent_messages),
                    )
                    return {
                        "ok": True,
                        "status": "skipped",
                        "reason": "persistent_turn_count_mismatch",
                        "recorded": False,
                    }
                prepared: list[dict[str, Any]] = []
                for metadata, actual in zip(current_messages, persistent_messages):
                    actual_role = str(actual.get("role") or "").strip().lower()
                    if actual_role not in {"user", "assistant", "tool"}:
                        return {"ok": False, "status": "failed", "reason": "current_turn_role_invalid"}
                    source_ids = [
                        str(source_id or "").strip()
                        for source_id in list(metadata.get("source_ids") or [])
                        if str(source_id or "").strip()
                    ]
                    if not source_ids:
                        return {"ok": False, "status": "failed", "reason": "current_turn_source_ids_missing"}
                    prepared.append(
                        {
                            **metadata,
                            "payload": dict(actual),
                            "source_ids": source_ids,
                        }
                    )
                binding = binder(messages=prepared, active_turn_id=turn_id)
                if not isinstance(binding, dict) or not binding.get("ok"):
                    detail = str((binding or {}).get("reason") or "request_binding_failed")
                    return {
                        "ok": False,
                        "status": "failed",
                        "reason": "context_authority_failed",
                        "detail": detail,
                    }
                frozen_turn_messages = [
                    dict(message)
                    for message in list(binding.get("active_messages") or [])
                    if isinstance(message, dict)
                ]
                frozen_request_indexes = [int(index) for index in list(binding.get("active_request_indexes") or [])]
                if not frozen_turn_messages or len(frozen_request_indexes) != len(frozen_turn_messages):
                    return {
                        "ok": False,
                        "status": "failed",
                        "reason": "context_authority_failed",
                        "detail": "active_turn_binding_invalid",
                    }
            result = recorder(
                turn_id=turn_id,
                provider_profile=str(request.get("protocol") or ""),
                turn_messages=[dict(message) for message in frozen_turn_messages],
                history_messages=[dict(message) for message in persistent_messages],
                history_message_indexes=list(frozen_request_indexes),
                audit_history_messages=[
                    dict(message)
                    for message in list(request.get("audit_history_messages") or [])
                    if isinstance(message, dict)
                ],
                attempt=0,
                model_route=request.get("model_route") or {},
                system_prefix=request.get("system_prefix") or "",
                tool_schema=request.get("tool_schema") or [],
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
            if isinstance(result, dict) and result.get("ok"):
                if isinstance(request_projection_state, dict) and persistent_messages:
                    first_message = persistent_messages[0]
                    if str(first_message.get("role") or "").strip().lower() == "user":
                        frozen_prompt = self._provider_message_text(first_message.get("content"))
                        if frozen_prompt:
                            request_projection_state.setdefault("current_user_prompt", frozen_prompt)
                            request_projection_state.setdefault("turn_id", turn_id)
                generation_context["memcore_request_projection"] = {
                    key: result.get(key)
                    for key in (
                        "status",
                        "turn_id",
                        "attempt",
                        "provider_profile",
                        "projection_count",
                        "projection_hashes",
                        "history_hash",
                        "full_prefix_hash",
                        "media_omitted",
                    )
                }
                return {"ok": True, "status": "recorded"}
            failure_reason = str((result or {}).get("reason") or "request_projection_record_failed")
            if failure_reason.startswith("projection_audit_"):
                logger.warning("memcore audit persistence failed reason=%s", failure_reason)
                public_reason = "audit_persistence_failed"
            else:
                # The binding above already proved that the provider-visible
                # current turn matches the authoritative MemCore projection.
                # Persisting a request audit is evidence about that request;
                # it is not permission to send it.  A recorder outage must be
                # observable, but must not cancel an otherwise valid Agent
                # decision or discard completed tool results.
                logger.warning("memcore request projection persistence failed reason=%s", failure_reason)
                public_reason = "request_projection_record_failed"
            failure = {
                "status": str((result or {}).get("status") or "failed"),
                "reason": public_reason,
                "detail": failure_reason,
                "delivery_status": "model_reply_preserved",
            }
            generation_context["memcore_request_projection"] = {
                "status": "degraded",
                "reason": public_reason,
                "detail": failure_reason,
            }
            generation_context["memcore_request_projection_failure"] = failure
            return {"ok": True, "status": "degraded", "reason": public_reason}

        return observe

    @staticmethod
    def _provider_message_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "").strip().lower()
            if block_type not in {"text", "input_text"}:
                continue
            text = block.get("text")
            if text is None:
                text = block.get("input_text")
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)

    @staticmethod
    def _request_projection_user_prompt(
        request_projection_state: dict[str, Any] | None,
        fallback: str,
    ) -> str:
        if isinstance(request_projection_state, dict):
            frozen = request_projection_state.get("current_user_prompt")
            if isinstance(frozen, str) and frozen:
                return frozen
        return str(fallback or "")

    @staticmethod
    def _attach_tool_execution_receipts(
        output: dict[str, Any],
        generation_context: dict[str, Any],
    ) -> None:
        receipts = generation_context.get(TOOL_EXECUTION_RECEIPTS_FIELD)
        if not isinstance(receipts, dict) or not receipts:
            return
        output[TOOL_EXECUTION_RECEIPTS_FIELD] = {
            str(name): dict(receipt) for name, receipt in receipts.items() if isinstance(receipt, dict)
        }

    @staticmethod
    def _with_tool_execution_receipt(
        call: Any,
        generation_context: dict[str, Any],
    ) -> Any:
        if not isinstance(call, dict):
            return call
        receipts = generation_context.get(TOOL_EXECUTION_RECEIPTS_FIELD)
        receipt = receipts.get(str(call.get("type") or "").strip()) if isinstance(receipts, dict) else None
        if not isinstance(receipt, dict):
            return call
        enriched = dict(call)
        enriched[TOOL_EXECUTION_RECEIPT_FIELD] = dict(receipt)
        return enriched

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
        domain_profile_id: str = "",
        capability_selection: Any = None,
        allow_deliberate_silence: bool = False,
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
            domain_profile_id=domain_profile_id,
            capability_selection=capability_selection,
            allow_deliberate_silence=allow_deliberate_silence,
        )

    @staticmethod
    def _attach_native_reasoning_content(
        normalized: dict[str, Any],
        *,
        raw_result: Any,
    ) -> None:
        """Carry private reasoning only with a real native tool decision."""

        if not isinstance(raw_result, Mapping):
            return
        if not (
            raw_result.get(NATIVE_TOOL_CALL_FIELD)
            or raw_result.get(NATIVE_TOOL_CALLS_FIELD)
        ):
            return
        reasoning_content = str(raw_result.get(NATIVE_REASONING_CONTENT_FIELD) or "").strip()
        if reasoning_content:
            normalized[NATIVE_REASONING_CONTENT_FIELD] = reasoning_content
        state = raw_result.get(continuation.RESULT_FIELD)
        if isinstance(state, dict):
            normalized[continuation.RESULT_FIELD] = state

    def _normalize_speech_payload(
        self,
        *,
        speech: Any,
        fallback_to_default: bool = True,
    ) -> tuple[str, list[str]]:
        return final_output_engine.normalize_speech_payload(
            speech=speech,
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

    def _max_tool_rounds(self, *, domain_profile_id: str = "") -> int:
        from .engine_services.tool_rounds import max_tool_rounds as _fn

        return _fn(domain_profile_id=domain_profile_id)

    def _tool_round_warning_remaining(self, *, hard_limit: int) -> int:
        from .engine_services.tool_rounds import tool_round_warning_remaining as _fn

        return _fn(hard_limit=hard_limit)

    def _build_tool_round_warning(
        self,
        *,
        used_rounds: int,
        hard_limit: int,
    ) -> str:
        from .engine_services.tool_rounds import build_tool_round_warning as _fn

        manager = getattr(self, "memcore_manager", None)
        return _fn(
            used_rounds=used_rounds,
            hard_limit=hard_limit,
            memcore_enabled=bool(manager is not None and getattr(manager, "enabled", False)),
        )

    def _tool_call_signature(self, tool_call: dict[str, Any]) -> str:
        from .engine_services.tool_rounds import tool_call_signature as _fn

        return _fn(tool_call)

    def _describe_tool_call_for_prompt(self, tool_call: dict[str, Any]) -> str:
        from .engine_services.tool_rounds import describe_tool_call_for_prompt as _fn

        return _fn(tool_call)

    def _build_tool_working_stream_event(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        from .engine_services.tool_rounds import build_tool_working_stream_event as _fn

        return _fn(tool_call)

    def _prepare_tool_round_decision(
        self,
        *,
        final_output: dict[str, Any],
        user_message: str,
        client_context: ClientProtocolContext,
        profile_user_id: str,
        session_id: str,
        domain_profile_id: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
        final_output, tool_calls, rejections = self._prepare_tool_round_decisions(
            final_output=final_output,
            user_message=user_message,
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile_id,
        )
        return (
            final_output,
            tool_calls[0] if tool_calls else None,
            rejections[0] if rejections else "",
        )

    def _prepare_tool_round_decisions(
        self,
        *,
        final_output: dict[str, Any],
        user_message: str,
        client_context: ClientProtocolContext,
        profile_user_id: str,
        session_id: str,
        domain_profile_id: str = "",
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
        execution_receipts = final_output.pop(TOOL_EXECUTION_RECEIPTS_FIELD, None)
        # M66-C frozen round: retrieve the CapabilitySelection resolved in
        # prepare_context() so normalize does not re-resolve handlers.
        frozen_capability_selection = final_output.pop(TOOL_CAPABILITY_SELECTION_FIELD, None)
        native_tool_calls = final_output.pop(NATIVE_TOOL_CALLS_FIELD, None)
        native_tool_call = final_output.pop(NATIVE_TOOL_CALL_FIELD, None)
        native_carrier_present = bool(
            (isinstance(native_tool_calls, list) and any(isinstance(call, dict) and call for call in native_tool_calls))
            or (isinstance(native_tool_call, dict) and native_tool_call)
        )
        raw_tool_calls = (
            [dict(call) for call in native_tool_calls if isinstance(call, dict) and call]
            if isinstance(native_tool_calls, list)
            else []
        )
        if not raw_tool_calls and isinstance(native_tool_call, dict) and native_tool_call:
            raw_tool_calls = [native_tool_call]
        if not raw_tool_calls:
            final_output = self._promote_narrated_tool_call(
                final_output,
                user_message=user_message,
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
            )
            raw_tool_call = final_output.get("tool_call")
            if isinstance(raw_tool_call, dict) and raw_tool_call:
                raw_tool_calls = [raw_tool_call]
        else:
            final_output["tool_call"] = None
        tool_calls: list[dict[str, Any]] = []
        rejections: list[str] = []
        resolved_round_handlers = getattr(frozen_capability_selection, "resolved_handlers", {}) or {}
        native_model_names = tuple(
            str(call.get("type") or "").strip()
            for call in raw_tool_calls
            if (
                isinstance(call, dict)
                and str(call.get("type") or "").strip().startswith("mcp_")
                and str(call.get("type") or "").strip() not in resolved_round_handlers
            )
        )
        direct_mcp_aliases: dict[str, str] = {}
        direct_mcp_selection = frozen_capability_selection
        if native_carrier_present and native_model_names:
            from .engine_services.tool_rounds import resolve_unloaded_mcp_native_aliases

            direct_mcp_aliases, direct_mcp_selection = resolve_unloaded_mcp_native_aliases(
                self,
                model_tool_names=native_model_names,
                profile_user_id=profile_user_id,
                client_context=client_context,
                domain_profile_id=domain_profile_id,
                capability_selection=frozen_capability_selection,
            )
        recovered_native_aliases: dict[str, str] | None = None
        for raw_tool_call in raw_tool_calls:
            raw_tool_name = str(raw_tool_call.get("type") or "").strip()
            call_capability_selection = frozen_capability_selection
            declared_native_names = {
                str(name or "").strip()
                for name in getattr(frozen_capability_selection, "native_tool_names", ()) or ()
                if str(name or "").strip()
            }
            native_aliases = getattr(frozen_capability_selection, "native_tool_aliases", {}) or {}
            canonical_native_name = str(native_aliases.get(raw_tool_name) or "").strip()
            if (
                native_carrier_present
                and raw_tool_name not in resolved_round_handlers
                and raw_tool_name not in declared_native_names
                and not canonical_native_name
            ):
                if recovered_native_aliases is None:
                    from .native_tool_schema import build_openai_native_tool_specs, native_tool_model_name_map

                    selected_native_names = set(declared_native_names)
                    alias_handlers = dict(resolved_round_handlers)
                    if not alias_handlers and frozen_capability_selection is None:
                        try:
                            alias_handlers = self._resolve_tool_handlers(
                                client_context=client_context,
                                profile_user_id=profile_user_id,
                                session_id=session_id,
                                domain_profile_id=domain_profile_id,
                            )
                        except Exception:
                            alias_handlers = {}
                    if not selected_native_names:
                        selected_native_names = {
                            str(name or "").strip()
                            for name, handler in alias_handlers.items()
                            if str(name or "").strip()
                            and bool(getattr(handler, "policy_accepted_native_tool", False))
                        }
                    selected_native_handlers = {
                        name: handler
                        for name, handler in alias_handlers.items()
                        if name in selected_native_names
                    }
                    recovered_native_aliases = native_tool_model_name_map(
                        build_openai_native_tool_specs(
                            selected_native_handlers,
                            allowed_tool_names=selected_native_names,
                        )
                    )
                canonical_native_name = str(recovered_native_aliases.get(raw_tool_name) or "").strip()
            recovered_native_name = str((recovered_native_aliases or {}).get(raw_tool_name) or "").strip()
            if canonical_native_name and (
                canonical_native_name in resolved_round_handlers
                or recovered_native_name == canonical_native_name
            ):
                raw_tool_call = dict(raw_tool_call)
                raw_tool_call["type"] = canonical_native_name
                raw_tool_call.setdefault(TOOL_MODEL_NAME_FIELD, raw_tool_name)
                raw_tool_name = canonical_native_name
            if raw_tool_name == "capability_invoke":
                from .capability_exposure import route_invocation
                raw_tool_call, call_capability_selection = route_invocation(
                    raw_tool_call, frozen_capability_selection,
                )
            if raw_tool_name == "invoke_mcp":
                from .capability_exposure import route_mcp_invocation
                raw_tool_call, call_capability_selection = route_mcp_invocation(
                    raw_tool_call, frozen_capability_selection,
                )
            canonical_mcp_name = direct_mcp_aliases.get(raw_tool_name, "")
            if canonical_mcp_name:
                raw_tool_call = dict(raw_tool_call)
                raw_tool_call["type"] = canonical_mcp_name
                raw_tool_call[TOOL_MODEL_NAME_FIELD] = raw_tool_name
                call_capability_selection = direct_mcp_selection
            receipt = execution_receipts.get(raw_tool_name) if isinstance(execution_receipts, dict) else None
            if isinstance(receipt, dict):
                raw_tool_call = dict(raw_tool_call)
                raw_tool_call[TOOL_EXECUTION_RECEIPT_FIELD] = dict(receipt)
            tool_call = self._normalize_tool_call(
                raw_tool_call,
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
                domain_profile_id=domain_profile_id,
                capability_selection=call_capability_selection,
            )
            if tool_call:
                tool_calls.append(tool_call)
                continue
            rejection = self._describe_tool_call_rejection(
                raw_tool_call,
                client_context=client_context,
                profile_user_id=profile_user_id,
                session_id=session_id,
                domain_profile_id=domain_profile_id,
            )
            if rejection:
                rejections.append(rejection)
        return final_output, tool_calls, rejections

    def _record_tool_call_rejection(
        self,
        *,
        final_output: dict[str, Any],
        rejection: str,
        tool_followups: list[str],
        session_id: str,
    ) -> None:
        reason_tool, reason_code = self._tool_call_rejection_log_fields(
            final_output=final_output,
            rejection=rejection,
        )
        logger.warning(
            "tool_call_rejected session=%s reason_tool=%s reason=%s",
            session_id,
            reason_tool or "unknown",
            reason_code,
        )
        tool_followups.append(rejection)

    @staticmethod
    def _tool_protocol_failure_output(*, rejections: list[str]) -> dict[str, Any]:
        """Expose a repeated carrier failure without pretending completion."""

        return {
            "emotion": "",
            "speech": (
                "刚才连续的工具请求没有形成可执行的协议，已经完成的工具结果仍然保留，"
                "但这次任务没有完整结束。"
            ),
            "tool_call": None,
            "memory_metadata": {},
            "_agent_failure": {
                "status": "protocol_error",
                "reason": "tool_call_protocol_invalid",
                "rejection_count": len(list(rejections or [])),
            },
        }

    @staticmethod
    def _tool_call_rejection_log_fields(
        *,
        final_output: dict[str, Any],
        rejection: str,
    ) -> tuple[str, str]:
        """Return bounded diagnostic labels without logging model arguments.

        Rejection feedback is model-facing text and may contain the submitted
        command, URL, query, path, or other user material.  Logs only need the
        attempted capability and rejection class; the full feedback remains in
        the active tool round and MemCore rather than crossing the log boundary.
        """

        reason_tool = str((final_output.get("tool_call") or {}).get("type") or "").strip()
        text = str(rejection or "")
        if not reason_tool:
            match = re.search(r"工具(?:「\s*|\s+)([A-Za-z][A-Za-z0-9_.:-]{0,79})(?:」)?", text)
            if match is not None:
                reason_tool = match.group(1)
        if not text.strip():
            reason_code = "rejection_detail_missing"
        elif "兼容 JSON tool_call" in text or "歧义调用" in text:
            reason_code = "native_tool_wrong_carrier"
        elif "参数不完整" in text or "格式不对" in text or "参数不符合" in text:
            reason_code = "bad_args"
        elif "执行器不可用" in text or "当前不在线" in text or "没有提供" in text:
            reason_code = "not_available"
        elif "本轮不可用" in text or "不存在的工具" in text:
            reason_code = "unknown_tool"
        else:
            reason_code = "validation_rejected"
        return reason_tool[:80], reason_code

    def _build_tool_round_extra_context(
        self,
        *,
        turn_extra_user_context: str,
        tool_followups: list[str],
        allow_more: bool,
        stop_reason: str = "",
        round_control_feedback: str = "",
    ) -> str:
        context = str(turn_extra_user_context or "").strip()
        if any(str(item or "").strip() for item in tool_followups) or stop_reason:
            context = self._merge_extra_user_context(
                context,
                self._build_multi_tool_followup_context(
                    tool_followups,
                    allow_more=allow_more,
                    stop_reason=stop_reason,
                ),
            )
        return self._merge_extra_user_context(
            context,
            str(round_control_feedback or "").strip(),
        )

    @staticmethod
    def _tool_call_allows_assistant_preface(tool_call: dict[str, Any]) -> bool:
        return str(tool_call.get("type") or "") not in {
            "retrieve_memory",
            "browse_memory",
            "read_memory_timeline",
            "open_memory",
            "load_character_context",
        }

    @staticmethod
    def _tool_call_uses_native_history(tool_call: dict[str, Any]) -> bool:
        return str(tool_call.get(TOOL_SOURCE_FIELD) or "").strip() in {
            NATIVE_ANTHROPIC,
            NATIVE_OPENAI,
        }

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
        memcore_turn_id: str = "",
    ) -> str:
        preface_turn = (
            self._build_assistant_dialogue_turn(final_output.get("speech"))
            if self._tool_call_allows_assistant_preface(tool_call)
            else None
        )
        if not preface_turn:
            return ""
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
        self._append_memcore_turn_intermediate(
            turn_id=memcore_turn_id,
            assistant_record=preface_record,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
        )
        if not self._memcore_owns_compaction():
            self._schedule_summary_cycle(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
        recent_raw_for_turn.append(preface_record)
        return str(preface_record.get("source_id") or "").strip()

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
        tool_history_turns: list[dict[str, Any]] | None = None,
        prompt_exclude_source_ids: list[str] | None = None,
        domain_profile_id: str = "",
        memcore_turn_id: str = "",
        execution_target: Any = None,
    ) -> tuple[ToolExecutionResult | None, list[dict[str, Any]]]:
        results, current_events = self._execute_and_record_tool_batch(
            tool_calls=[tool_call],
            final_output=final_output,
            tool_results=tool_results,
            tool_events=tool_events,
            tool_followups=tool_followups,
            tool_turns=tool_turns,
            recent_raw_for_turn=recent_raw_for_turn,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            now_ts=now_ts,
            current_user_source_id=current_user_source_id,
            client_context=client_context,
            memory_exclude_source_ids=memory_exclude_source_ids,
            request_context=request_context,
            tool_history_turns=tool_history_turns,
            prompt_exclude_source_ids=prompt_exclude_source_ids,
            domain_profile_id=domain_profile_id,
            memcore_turn_id=memcore_turn_id,
            execution_target=execution_target,
        )
        return (results[-1] if results else None), current_events

    def _execute_and_record_tool_batch(
        self,
        *,
        tool_calls: list[dict[str, Any]],
        final_output: dict[str, Any],
        provider_output_raw: str = "",
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
        tool_history_turns: list[dict[str, Any]] | None = None,
        prompt_exclude_source_ids: list[str] | None = None,
        recorded_tool_call_ids: set[str] | None = None,
        domain_profile_id: str = "",
        memcore_turn_id: str = "",
        execution_target: Any = None,
        native_reasoning_by_call_id: Mapping[str, str] | None = None,
        provider_continuations: dict | None = None,
    ) -> tuple[list[ToolExecutionResult], list[dict[str, Any]]]:
        calls = [dict(call) for call in tool_calls if isinstance(call, dict) and call]
        if not calls:
            return [], []

        def execute(call: dict[str, Any]) -> ToolExecutionResult:
            return self._execute_tool_call_with_hooks(
                call=call,
                final_output=final_output,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                now_ts=now_ts,
                current_user_source_id=current_user_source_id,
                client_context=client_context,
                memory_exclude_source_ids=memory_exclude_source_ids,
                request_context={**request_context, "_model_execution_target": execution_target,
                                 "_host_visible_tool_history": [*request_context.get("_host_visible_tool_history", []), *list(tool_history_turns or [])],
                                 "_host_origin_turn_id": memcore_turn_id},
                domain_profile_id=domain_profile_id,
            )

        handler_sets = {}
        def handler_for(call):
            selection = call.get(TOOL_CAPABILITY_SELECTION_FIELD)
            key = id(selection)
            if key not in handler_sets:
                handler_sets[key] = self._resolve_tool_handlers(
                    client_context=client_context,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    domain_profile_id=domain_profile_id,
                    capability_selection=selection,
                )
            return handler_sets[key].get(str(call.get("type") or ""))

        executed = execute_tool_batch(calls, execute=execute, handler_for=handler_for, scope_id=session_id,
                                      cancelled=current_cancellation_check())
        completed = list(executed)

        batch_events: list[dict[str, Any]] = []
        followup_start = len(tool_followups)
        history_items: list[tuple[dict[str, Any], ToolExecutionResult, str]] = []
        for call, result in zip(calls, executed):
            assert result is not None
            current_events, shaped_followup = self._record_tool_round_result(
                tool_call=call,
                tool_result=result,
                tool_results=tool_results,
                tool_events=tool_events,
                tool_followups=tool_followups,
                tool_turns=tool_turns,
                recent_raw_for_turn=recent_raw_for_turn,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                now_ts=now_ts,
            )
            batch_events.extend(current_events)
            history_items.append((call, result, shaped_followup))
        trace_source_ids, trace_record_failure = self._record_memcore_tool_batch(
            items=history_items,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            now_ts=now_ts,
            current_user_source_id=current_user_source_id,
            memcore_turn_id=memcore_turn_id,
            recorded_tool_call_ids=recorded_tool_call_ids,
        )
        batch_model_images = self._merge_tool_model_image_inputs(
            [],
            [result for _call, result, _shaped in history_items],
        )
        chat_model_override = str((request_context or {}).get("chat_model_override") or "").strip()
        # The next request may be sent to a different provider when this batch
        # produced real image inputs.  Build the temporary tool-history
        # projection for that next target, rather than inheriting the provider
        # shape that happened to emit the previous tool call.  MemCore itself
        # stores provider-neutral entries; this is only the active-turn wire
        # projection.
        projection_target = execution_target
        if batch_model_images and str(getattr(execution_target, "role", "") or "") != "vision":
            projection_target = self._resolve_turn_execution_target(
                has_real_images=False,
                tool_image_upgrade=True,
                chat_model_override=chat_model_override,
            )
        projection_provider_profile = str(getattr(projection_target, "protocol", "") or "").strip().lower()
        if projection_provider_profile:
            projection_model_images = batch_model_images
        else:
            native_vision_status = self.native_chat_vision_status(
                chat_model_override=chat_model_override,
            )
            projection_model_images = batch_model_images if bool(native_vision_status.get("enabled")) else []
        media_source_ids = self._record_memcore_tool_media_input(
            model_image_inputs=batch_model_images,
            related_source_ids=trace_source_ids,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            now_ts=now_ts,
            memcore_turn_id=memcore_turn_id,
        )
        if prompt_exclude_source_ids is not None:
            for source_id in [*trace_source_ids, *media_source_ids]:
                if source_id not in prompt_exclude_source_ids:
                    prompt_exclude_source_ids.append(source_id)
        tool_projection = self._append_tool_history_batch(
            tool_history_turns=tool_history_turns,
            items=history_items,
            trace_source_ids=trace_source_ids,
            media_source_ids=media_source_ids,
            model_image_inputs=projection_model_images,
            provider_output_raw=provider_output_raw,
            provider_profile=projection_provider_profile,
            profile_user_id=profile_user_id,
            session_id=session_id,
            character_pack_id=character_pack_id,
            memcore_turn_id=memcore_turn_id,
            current_user_source_id=current_user_source_id,
            native_reasoning_by_call_id=native_reasoning_by_call_id,
            provider_continuations=provider_continuations,
        )
        manager = getattr(self, "memcore_manager", None)
        memcore_required = bool(
            str(memcore_turn_id or "").strip() and manager is not None and getattr(manager, "enabled", False)
        )
        memcore_failure: dict[str, Any] | None = None
        if memcore_required and trace_record_failure is not None:
            memcore_failure = dict(trace_record_failure)
        elif memcore_required and len(trace_source_ids) != 2 * len(history_items):
            memcore_failure = {
                "status": "failed",
                "reason": "tool_trace_record_failed",
                "detail": "tool_trace_incomplete",
            }
        elif memcore_required and batch_model_images and not media_source_ids:
            memcore_failure = {
                "status": "failed",
                "reason": "tool_media_record_failed",
                "detail": "tool_media_unavailable",
            }
        elif memcore_required and not tool_projection.get("ok"):
            memcore_failure = {
                "status": "failed",
                "reason": "tool_projection_build_failed",
                "detail": self._safe_memcore_failure_code(
                    tool_projection.get("reason"),
                    fallback="tool_projection_unavailable",
                ),
            }
        if memcore_failure is not None:
            for result in completed:
                result.state_updates["_memcore_failure"] = dict(memcore_failure)
        has_native_calls = any(
            str(call.get(TOOL_SOURCE_FIELD) or "").strip() in {NATIVE_ANTHROPIC, NATIVE_OPENAI} for call in calls
        )
        if tool_projection.get("ok") and tool_projection.get("status") == "projected" and not has_native_calls:
            del tool_followups[followup_start:]
        if not tool_projection.get("ok") and has_native_calls:
            for _call, result, shaped_followup in history_items:
                feedback = str(shaped_followup or "").strip()
                if feedback:
                    tool_followups.append(
                        "【本轮真实工具结果】\n"
                        f"工具：{result.tool_type}\n"
                        f"{feedback}\n"
                        "以上是本轮已经返回的真实结果；请据此继续，不要把上下文存储状态误认为工具执行失败。"
                    )
        return completed, batch_events

    @staticmethod
    def _tool_batch_memcore_failure(
        tool_results: list[ToolExecutionResult],
    ) -> dict[str, Any] | None:
        for result in list(tool_results or []):
            state_updates = getattr(result, "state_updates", None)
            failure = state_updates.get("_memcore_failure") if isinstance(state_updates, dict) else None
            if isinstance(failure, dict):
                return dict(failure)
        return None

    def _record_tool_round_result(
        self,
        *,
        tool_call: dict[str, Any],
        tool_result: ToolExecutionResult,
        tool_results: list[ToolExecutionResult],
        tool_events: list[dict[str, Any]],
        tool_followups: list[str],
        tool_turns: list[dict[str, Any]],
        recent_raw_for_turn: list[dict[str, Any]],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        now_ts: int,
    ) -> tuple[list[dict[str, Any]], str]:

        tool_results.append(tool_result)
        current_events = list(tool_result.stream_events)
        tool_events.extend(current_events)
        result_followup = tool_orchestration_engine.append_structured_artifact_receipts(
            tool_result.followup_context,
            stream_events=tool_result.stream_events,
        )
        shaped_followup = tool_orchestration_engine.shape_tool_followup(
            (
                tool_result.followup_envelope.with_content(result_followup)
                if tool_result.followup_envelope is not None
                else result_followup
            ),
            tool_type=tool_result.tool_type,
        )
        if str(tool_call.get(TOOL_SOURCE_FIELD) or "").strip() not in {NATIVE_ANTHROPIC, NATIVE_OPENAI}:
            tool_followups.append(f"第 {len(tool_results)} 次工具（{tool_result.tool_type}）结果：\n{shaped_followup}")
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
            if not self._memcore_owns_compaction():
                self._schedule_summary_cycle(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    character_pack_id=character_pack_id,
                )
            recent_raw_for_turn.append(tool_record)
        return current_events, shaped_followup

    @staticmethod
    def _merge_model_image_inputs(
        current_images: list[dict[str, Any]],
        additional_images: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for raw in [*list(current_images or []), *list(additional_images or [])]:
            if not isinstance(raw, dict):
                continue
            data_url = str(raw.get("data_url") or "")
            if not data_url.startswith("data:image/"):
                continue
            source_id = str(raw.get("attachment_id") or "").strip()
            handle = str(raw.get("attachment_handle") or "").strip()
            identity = (source_id, handle)
            if identity != ("", "") and identity in seen:
                continue
            if identity != ("", ""):
                seen.add(identity)
            merged.append(dict(raw))
            if len(merged) >= 5:
                break
        return merged

    @staticmethod
    def _merge_tool_model_image_inputs(
        current_images: list[dict[str, Any]],
        tool_results: list[ToolExecutionResult],
    ) -> list[dict[str, Any]]:
        images = [*list(current_images or []), *[
                item
                for result in list(tool_results or [])
                for item in list(getattr(result, "model_image_inputs", None) or [])
            ]]
        observations = [item for item in images if isinstance(item, dict) and item.get("ephemeral_observation")]
        if observations:
            images = [observations[-1], *[item for item in images if not isinstance(item, dict) or not item.get("ephemeral_observation")]]
        return AkaneMemoryEngine._merge_model_image_inputs([], images)

    def _append_tool_history_batch(
        self,
        *,
        tool_history_turns: list[dict[str, Any]] | None,
        items: list[tuple[dict[str, Any], ToolExecutionResult, str]],
        trace_source_ids: list[str],
        media_source_ids: list[str] | None = None,
        model_image_inputs: list[dict[str, Any]] | None = None,
        provider_output_raw: str = "",
        provider_profile: str = "",
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        memcore_turn_id: str = "",
        current_user_source_id: str = "",
        native_reasoning_by_call_id: Mapping[str, str] | None = None,
        provider_continuations: dict | None = None,
    ) -> dict[str, Any]:
        if tool_history_turns is None:
            return {"ok": True, "status": "skipped", "reason": "history_target_missing"}
        sources = {
            str(tool_call.get(TOOL_SOURCE_FIELD) or "").strip()
            for tool_call, _result, _shaped in items
            if str(tool_call.get(TOOL_SOURCE_FIELD) or "").strip() in {NATIVE_ANTHROPIC, NATIVE_OPENAI}
        }
        media_ids = {
            str(source_id or "").strip() for source_id in list(media_source_ids or []) if str(source_id or "").strip()
        }
        has_legacy_calls = any(
            str(tool_call.get(TOOL_SOURCE_FIELD) or "").strip() not in {NATIVE_ANTHROPIC, NATIVE_OPENAI}
            for tool_call, _result, _shaped in items
        )
        if sources and has_legacy_calls:
            return {"ok": False, "status": "failed", "reason": "mixed_tool_history_modes"}
        if not sources and not has_legacy_calls and not media_ids:
            return {"ok": True, "status": "skipped", "reason": "no_tool_calls"}
        if len(sources) != 1:
            if sources:
                return {"ok": False, "status": "failed", "reason": "mixed_native_provider_batch"}
        manager = getattr(self, "memcore_manager", None)
        build_open_projection = getattr(manager, "build_open_turn_projection", None)
        build_full_projection = getattr(manager, "build_context_projection", None)
        selected_ids = set(media_ids)
        selected_ids.update(
            str(source_id or "").strip() for source_id in trace_source_ids if str(source_id or "").strip()
        )
        normalized_turn_id = str(memcore_turn_id or "").strip()
        use_focused_projection = callable(build_open_projection) and bool(normalized_turn_id)
        build_projection = build_open_projection if use_focused_projection else build_full_projection
        if not callable(build_projection) or not selected_ids:
            return {"ok": False, "status": "unavailable", "reason": "memcore_projection_unavailable"}
        provider_profile = str(provider_profile or "").strip().lower()
        if not provider_profile:
            provider_profile = next(iter(sources), "")
        if not provider_profile:
            runtime = getattr(self, "llm", None)
            protocol_getter = getattr(runtime, "chat_provider_protocol", None)
            if callable(protocol_getter):
                provider_profile = str(protocol_getter() or "").strip()
        if not provider_profile:
            return {"ok": False, "status": "unavailable", "reason": "provider_profile_unavailable"}
        projection_kwargs = {
            "provider_profile": provider_profile,
            "profile_user_id": profile_user_id,
            "session_id": session_id,
            "character_pack_id": character_pack_id,
        }
        if use_focused_projection:
            projection_kwargs["turn_id"] = normalized_turn_id
        projection = build_projection(**projection_kwargs)
        if not isinstance(projection, dict) or not projection.get("ok"):
            return {"ok": False, "status": "unavailable", "reason": "memcore_projection_build_failed"}
        projection_messages = [
            message for message in list(projection.get("messages") or []) if isinstance(message, dict)
        ]
        selected_messages = [
            message
            for message in projection_messages
            if selected_ids.intersection(str(source_id or "").strip() for source_id in message.get("source_ids") or [])
        ]
        selected_turn_ids = {
            str(message.get("turn_id") or "").strip()
            for message in selected_messages
            if str(message.get("turn_id") or "").strip()
        }
        if len(selected_turn_ids) > 1 or (
            normalized_turn_id and selected_turn_ids and selected_turn_ids != {normalized_turn_id}
        ):
            return {"ok": False, "status": "failed", "reason": "tool_projection_crosses_turns"}
        current_turn_messages = selected_messages
        if selected_turn_ids:
            current_turn_id = next(iter(selected_turn_ids))
            current_turn_messages = [
                message
                for message in projection_messages
                if str(message.get("turn_id") or "").strip() == current_turn_id
            ]
            if not current_turn_messages:
                return {"ok": False, "status": "failed", "reason": "tool_turn_projection_missing"}
            # The runtime inserts a provider-visible current stimulus before
            # ``post_user_turns``. Remove that exact source from the rebuilt
            # open-turn suffix instead of assuming it is always the first
            # projected message. Hidden host turns (for example QQ attention)
            # have a prompt-invisible stimulus, so their first visible message
            # can be the assistant tool call itself; dropping it leaves an
            # orphan ``role=tool`` message that strict providers reject.
            normalized_current_source_id = str(current_user_source_id or "").strip()
            if normalized_current_source_id:
                current_turn_messages = [
                    message
                    for message in current_turn_messages
                    if normalized_current_source_id
                    not in {
                        str(source_id or "").strip()
                        for source_id in message.get("source_ids") or []
                        if str(source_id or "").strip()
                    }
                ]
        projected_messages: list[dict[str, Any]] = []
        media_attached = False
        legacy_assistant_written = False
        legacy_action_ids = set(trace_source_ids[0::2]) if has_legacy_calls else set()
        legacy_result_ids = set(trace_source_ids[1::2]) if has_legacy_calls else set()
        for message in current_turn_messages:
            payload = dict(message.get("payload") or {})
            message_source_ids = {
                str(source_id or "").strip()
                for source_id in message.get("source_ids") or []
                if str(source_id or "").strip()
            }
            if media_ids.intersection(message_source_ids):
                payload = self._attach_model_images_to_projection(
                    payload,
                    model_image_inputs=list(model_image_inputs or []),
                )
                media_attached = True
            elif has_legacy_calls and legacy_action_ids.intersection(message_source_ids):
                raw = str(provider_output_raw or "")
                if not raw or legacy_assistant_written:
                    return {
                        "ok": False,
                        "status": "failed",
                        "reason": "legacy_provider_output_unavailable",
                    }
                payload = {"role": "assistant", "content": raw}
                legacy_assistant_written = True
            elif has_legacy_calls and legacy_result_ids.intersection(message_source_ids):
                payload = self._legacy_tool_result_history_message(payload)
            payload = self._overlay_native_reasoning_content(
                payload,
                native_reasoning_by_call_id=native_reasoning_by_call_id,
            )
            payload = continuation.overlay(payload, provider_continuations)
            projected_messages.append(payload)
        covered_ids = {
            str(source_id or "").strip()
            for message in selected_messages
            if isinstance(message, dict)
            for source_id in message.get("source_ids") or []
            if str(source_id or "").strip() in selected_ids
        }
        if not projected_messages or covered_ids != selected_ids:
            return {"ok": False, "status": "failed", "reason": "tool_projection_incomplete"}
        if media_ids and not media_attached:
            return {"ok": False, "status": "failed", "reason": "media_projection_incomplete"}
        if has_legacy_calls and not legacy_assistant_written:
            return {"ok": False, "status": "failed", "reason": "legacy_assistant_projection_missing"}
        tool_history_turns[:] = projected_messages
        return {
            "ok": True,
            "status": "projected",
            "reason": "",
            "provider_profile": str(projection.get("provider_profile") or ""),
            "message_count": len(projected_messages),
            "source_count": len(covered_ids),
        }

    @staticmethod
    def _overlay_native_reasoning_content(
        payload: dict[str, Any],
        *,
        native_reasoning_by_call_id: Mapping[str, str] | None,
    ) -> dict[str, Any]:
        """Decorate an open-turn tool call after MemCore projection.

        The mapping exists only for the active host turn. MemCore remains the
        authority for message order and durable action/result data; private
        reasoning is overlaid only on the provider wire shape that requires it.
        """

        if str(payload.get("role") or "").strip().lower() != "assistant":
            return payload
        reasoning_map = native_reasoning_by_call_id or {}
        tool_calls = payload.get("tool_calls")
        if not isinstance(tool_calls, list):
            return payload
        matched = [
            str(reasoning_map.get(str(call.get("id") or "").strip()) or "").strip()
            for call in tool_calls
            if isinstance(call, dict) and str(call.get("id") or "").strip()
        ]
        reasoning_content = next((value for value in matched if value), "")
        if not reasoning_content:
            return payload
        return {**payload, "reasoning_content": reasoning_content}

    @staticmethod
    def _legacy_tool_result_history_message(payload: dict[str, Any]) -> dict[str, Any]:
        content = payload.get("content")
        nested_call_ids: list[str] = []
        if isinstance(content, list):
            nested_call_ids = [
                str(item.get("tool_use_id") or item.get("tool_call_id") or "").strip()
                for item in content
                if isinstance(item, dict) and str(item.get("tool_use_id") or item.get("tool_call_id") or "").strip()
            ]
            result_text = "\n".join(
                str(item.get("text") or item.get("content") or "").strip()
                for item in content
                if isinstance(item, dict) and str(item.get("text") or item.get("content") or "").strip()
            )
        else:
            result_text = str(content or "").strip()
        call_id = str(payload.get("tool_call_id") or "").strip() or ",".join(nested_call_ids)
        lines = ["[tool.result]"]
        if call_id:
            lines.append(f"call_id: {call_id}")
        lines.extend(("content:", result_text, "请基于以上真实工具结果继续处理当前请求。"))
        return {"role": "user", "content": "\n".join(lines)}

    @staticmethod
    def _attach_model_images_to_projection(
        payload: dict[str, Any],
        *,
        model_image_inputs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if str(payload.get("role") or "").strip().lower() != "user":
            return dict(payload)
        content = payload.get("content")
        if isinstance(content, list):
            blocks = [dict(item) for item in content if isinstance(item, dict)]
        else:
            blocks = [{"type": "text", "text": str(content or "")}]
        for item in list(model_image_inputs or [])[:5]:
            if not isinstance(item, dict):
                continue
            data_url = str(item.get("data_url") or "").strip()
            if data_url.startswith("data:image/"):
                blocks.append({"type": "image_url", "image_url": {"url": data_url}})
        return {**dict(payload), "content": blocks}

    def _record_memcore_tool_media_input(
        self,
        *,
        model_image_inputs: list[dict[str, Any]],
        related_source_ids: list[str],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        now_ts: int,
        memcore_turn_id: str,
    ) -> list[str]:
        if not model_image_inputs or not str(memcore_turn_id or "").strip():
            return []
        manager = getattr(self, "memcore_manager", None)
        append_media = getattr(manager, "append_turn_media_input", None)
        if not callable(append_media):
            return []
        safe_items = [
            {
                "attachment_id": str(item.get("attachment_id") or "").strip(),
                "attachment_handle": str(item.get("attachment_handle") or "").strip(),
                "mime_type": str(item.get("mime_type") or item.get("content_type") or "").strip(),
            }
            for item in model_image_inputs
            if isinstance(item, dict)
        ]
        try:
            recorded = append_media(
                items=safe_items,
                turn_id=memcore_turn_id,
                related_source_ids=related_source_ids,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                timestamp=max(now_ts, int(time.time())),
            )
            self._warn_memcore_write_result("tool media input record", recorded)
            source_id = str((recorded or {}).get("source_id") or "").strip()
            return [source_id] if bool((recorded or {}).get("ok")) and source_id else []
        except Exception as exc:
            logger.warning("memcore tool media input record failed reason=%s", type(exc).__name__)
            return []

    @staticmethod
    def _tool_call_model_arguments(tool_call: dict[str, Any]) -> dict[str, Any]:
        """Recover the arguments the provider originally saw.

        Adapter/plugin handlers use an internal ``arguments`` envelope for
        CapCore validation. Provider-native schemas do not expose that envelope,
        so replaying it would change the assistant tool_call and break strict
        continuation gateways such as PinAI. Only unwrap mapped native calls;
        ordinary tools and legacy calls retain their existing shape.
        """

        model_arguments = tool_call.get(TOOL_MODEL_ARGUMENTS_FIELD)
        if isinstance(model_arguments, Mapping):
            return {str(key): value for key, value in model_arguments.items()}
        payload = {
            str(key): value
            for key, value in dict(tool_call or {}).items()
            if key != "type" and not str(key).startswith("_tool_")
        }
        wrapped = payload.get("arguments")
        if str(tool_call.get(TOOL_MODEL_NAME_FIELD) or "").strip() and len(payload) == 1 and isinstance(wrapped, dict):
            return {str(key): value for key, value in wrapped.items() if not str(key).startswith("_tool_")}
        return payload

    def _record_memcore_tool_batch(
        self,
        *,
        items: list[tuple[dict[str, Any], ToolExecutionResult, str]],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        now_ts: int,
        current_user_source_id: str,
        memcore_turn_id: str,
        recorded_tool_call_ids: set[str] | None,
    ) -> tuple[list[str], dict[str, Any] | None]:
        manager = getattr(self, "memcore_manager", None)
        if manager is None or not getattr(manager, "enabled", False) or not str(memcore_turn_id or "").strip():
            return [], None
        exchanges: list[dict[str, Any]] = []
        trace_keys: list[str] = []
        for tool_call, tool_result, shaped_followup in items:
            tool_type = str(tool_call.get("type") or tool_result.tool_type or "unknown").strip() or "unknown"
            call_id = str(tool_call.get(TOOL_INVOCATION_ID_FIELD) or "").strip()
            if not call_id:
                call_id = (
                    "call_" + hashlib.sha256(self._tool_call_signature(tool_call).encode("utf-8")).hexdigest()[:16]
                )
            trace_key = f"{tool_type}:{call_id}"
            if recorded_tool_call_ids is not None:
                if trace_key in recorded_tool_call_ids:
                    continue
            trace_keys.append(trace_key)
            feedback = str(shaped_followup or "").strip()
            stored_result: Any = self._sanitize_tool_trace_text(feedback)
            trace_receipt = getattr(tool_result, "trace_receipt", None)
            retention_anchor = (
                self._sanitize_tool_trace_value(dict(trace_receipt))
                if isinstance(trace_receipt, Mapping) and trace_receipt
                else None
            )
            result_status = self._tool_result_trace_status(tool_result)
            source_material = f"{current_user_source_id}|{session_id}|{call_id}|{tool_type}"
            exchanges.append(
                {
                    "tool_name": str(tool_call.get(TOOL_MODEL_NAME_FIELD) or "").strip() or tool_type,
                    "tool_call_id": call_id,
                    "tool_input": self._sanitize_tool_trace_value(self._tool_call_model_arguments(tool_call)),
                    "result": stored_result,
                    "source": str(tool_call.get(TOOL_SOURCE_FIELD) or tool_result.tool_type or tool_type),
                    "timestamp": max(now_ts, int(time.time())),
                    "source_id_prefix": (
                        "tooltrace:" + hashlib.sha256(source_material.encode("utf-8")).hexdigest()[:32]
                    ),
                    "result_status": result_status,
                    "retention_anchor": retention_anchor,
                }
            )
        if not exchanges:
            return [], None
        try:
            recorded = manager.record_tool_batch(
                exchanges=exchanges,
                turn_id=memcore_turn_id,
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
            )
            self._warn_memcore_write_result("tool batch record", recorded)
            if not isinstance(recorded, dict) or not bool(recorded.get("ok")):
                detail = self._safe_memcore_failure_code(
                    (recorded or {}).get("reason") or (recorded or {}).get("status"),
                    fallback="tool_trace_store_rejected",
                )
                return [], {
                    "status": self._safe_memcore_failure_code(
                        (recorded or {}).get("status"),
                        fallback="failed",
                    ),
                    "reason": "tool_trace_record_failed",
                    "detail": detail,
                }
            source_ids = [
                source_id
                for exchange in list((recorded or {}).get("exchanges") or [])
                if isinstance(exchange, dict)
                for source_id in (
                    str(exchange.get("tool_use_source_id") or "").strip(),
                    str(exchange.get("tool_result_source_id") or "").strip(),
                )
                if source_id
            ]
            if len(source_ids) != 2 * len(exchanges):
                return source_ids, {
                    "status": "failed",
                    "reason": "tool_trace_record_failed",
                    "detail": "tool_trace_incomplete",
                }
            if recorded_tool_call_ids is not None:
                recorded_tool_call_ids.update(trace_keys)
            return source_ids, None
        except Exception as exc:
            logger.warning("memcore tool batch record failed reason=%s", type(exc).__name__)
            return [], {
                "status": "failed",
                "reason": "tool_trace_record_failed",
                "detail": self._safe_memcore_failure_code(
                    f"exception_{type(exc).__name__}",
                    fallback="tool_trace_store_exception",
                ),
            }

    def _sanitize_tool_trace_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): self._sanitize_tool_trace_value(item)
                for key, item in value.items()
                if not self._is_sensitive_tool_trace_key(key)
            }
        if isinstance(value, list):
            return [self._sanitize_tool_trace_value(item) for item in value]
        if isinstance(value, tuple):
            return [self._sanitize_tool_trace_value(item) for item in value]
        if isinstance(value, str):
            return self._sanitize_tool_trace_text(value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self._sanitize_tool_trace_text(str(value))

    @staticmethod
    def _is_sensitive_tool_trace_key(value: Any) -> bool:
        """Reject credentials, not model-visible operation coordinates.

        Paths are legitimate tool arguments and often the only durable way for
        a later native-tool round to continue work.  Removing them here made
        MemCore's projected assistant action differ from the provider action
        that was actually executed.  Transport/cache/database paths that must
        stay private belong outside model-call arguments in the first place;
        credential-shaped values remain filtered below.
        """

        key = re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())
        if not key:
            return False
        if key in {
            "apikey",
            "authorization",
            "authtoken",
            "accesstoken",
            "clientsecret",
            "cookie",
            "password",
            "refreshtoken",
            "secret",
        }:
            return True
        if key.endswith("apikey") or key.endswith("secret") or key.endswith("password"):
            return True
        return False

    @staticmethod
    def _configured_tool_trace_secret_values() -> tuple[str, ...]:
        """Return exact deployment secrets that may be removed from traces.

        Source code and command output are opaque model evidence.  Guessing a
        secret from text such as ``Token = LexToken`` changes the action/result
        that MemCore later projects and can corrupt a following file rewrite.
        Credential-shaped structured keys are filtered separately; free text
        is only scrubbed when it contains a value the host actually configured
        as a credential.
        """

        markers = (
            "API_KEY",
            "ACCESS_TOKEN",
            "AUTH_TOKEN",
            "CLIENT_SECRET",
            "COOKIE",
            "PASSWORD",
            "REFRESH_TOKEN",
            "WEBHOOK_SECRET",
        )
        values: set[str] = set()
        for name, raw in vars(config).items():
            upper_name = str(name or "").upper()
            if not any(marker in upper_name for marker in markers):
                continue
            if not isinstance(raw, str):
                continue
            secret = raw.strip()
            if len(secret) < 8 or secret.lower() in {"configured", "redacted", "none", "null"}:
                continue
            values.add(secret)
        return tuple(sorted(values, key=len, reverse=True))

    @classmethod
    def _sanitize_tool_trace_text(cls, value: str) -> str:
        text = str(value or "")
        text = re.sub(
            r"(?i)(\bauthorization\s*:\s*bearer\s+)[^\s,;]+",
            r"\1[redacted]",
            text,
        )
        for secret in cls._configured_tool_trace_secret_values():
            text = text.replace(secret, "[redacted]")
        return text

    def _tool_result_is_error(self, tool_result: ToolExecutionResult) -> bool:
        feedback = str(getattr(tool_result, "followup_context", "") or "")
        if "<tool_use_error>" in feedback:
            return True
        for event in getattr(tool_result, "stream_events", []) or []:
            if not isinstance(event, dict):
                continue
            status = str(event.get("status") or event.get("state") or "").strip().lower()
            if status in {
                "error",
                "failed",
                "failure",
                "unavailable",
                "unavailable_before_dispatch",
                "execution_unknown",
                "rejected",
                "denied",
                "blocked",
                "canceled",
                "cancelled",
            }:
                return True
        return False

    def _tool_result_trace_status(self, tool_result: ToolExecutionResult) -> str:
        tool_type = str(getattr(tool_result, "tool_type", "") or "").strip()
        for event in getattr(tool_result, "stream_events", []) or []:
            if not isinstance(event, dict):
                continue
            status = str(event.get("status") or event.get("state") or "").strip().lower()
            if status in {"canceled", "cancelled"}:
                return "cancelled"
            if (
                str(event.get("type") or "").strip() == "capability_approval_required"
                or status == "approval_required"
            ):
                return "approval_required"
            # Preserve trusted terminal states in the MemCore observation
            # instead of collapsing them to a generic error/success, so a
            # timed-out or unconfirmed operation (exec commands and reviewed
            # desktop satellite capabilities alike) is never remembered as
            # done.  capability_execution_result events are only emitted by
            # reviewed execution paths, so their known terminal states are
            # safe to keep verbatim.
            if (
                str(event.get("type") or "").strip() == "capability_execution_result"
                and status in {"failed", "timed_out", "execution_unknown"}
            ):
                return status
        return "error" if self._tool_result_is_error(tool_result) else "success"

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
                str(value).replace("，", ",").replace("、", ",").replace("；", ",").replace(";", ",").replace("|", ",")
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
        mood_tags = list(memory_metadata.get("mood_tags") or []) if isinstance(memory_metadata, dict) else []
        character = output.get("character")
        outfit = str(character.get("outfit") or "").strip() if isinstance(character, dict) else ""
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
        manager = self._memcore_manager_if_enabled()
        if manager is not None:
            result = manager.acquaintance_note(
                profile_user_id=profile_user_id,
                session_id=profile_user_id,
                character_pack_id=character_pack_id,
                now_ts=now_ts,
            )
            if result:
                return result
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

    def _build_memory_timeline_tool_service(self) -> Any:
        try:
            from .memcore_integration.timeline import MemcoreTimelineToolService

            return MemcoreTimelineToolService(
                legacy_service=getattr(self, "memory_timeline_service", None),
                memcore_manager=getattr(self, "memcore_manager", None),
            )
        except Exception as exc:
            logger.warning("memcore timeline tool adapter disabled: %s", exc)
            return getattr(self, "memory_timeline_service", None)

    def _get_approval_store(self) -> Any:
        store = getattr(self, "approval_store", None)
        if store is not None:
            return store
        from .capability_approval import CapabilityApprovalStore

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
        from .skill_runtime import SkillRegistry, resolve_skill_roots

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

            from .execution_local import TrustedLocalExecutor

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
            sdk_root = Path(getattr(config, "BASE_DIR", Path(__file__).resolve().parent.parent)) / "examples" / "plugins"
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
            from .project_workspace import ProjectWorkspaceService

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

    def _build_tool_handlers(self) -> dict[str, BaseToolHandler]:
        from .tool_handlers.catalog import build_builtin_tool_handlers

        return build_builtin_tool_handlers(
            executor_broker=getattr(self, "executor_broker", None),
            store=self.store,
            sticker_assets=self.sticker_assets,
            capability_offer_source=self.capability_offer_source,
            capability_config_base_dir=self.capability_config_base_dir,
            memory_timeline_service=self._build_memory_timeline_tool_service(),
            context_libraries=getattr(
                self.desktop_pet_character_resources,
                "context_libraries",
                None,
            ),
            attachment_service=self._get_attachment_inbox_service(),
            image_material_resolver=self._get_image_material_resolver(),
            project_workspace_service=self._get_project_workspace_service(),
            workspace_file_service=self._get_workspace_file_service(),
            attachment_ingest_service=self._get_attachment_ingest_service(),
            generated_file_service=self._get_generated_file_service(),
            retrieve_fn=self._execute_retrieve_memory_tool,
            skill_registry=self._get_skill_registry(),
            execution_provider=self._build_execution_provider(),
            approval_store=self._get_approval_store(),
            mcp_management_service=getattr(self, "mcp_management_service", None),
            extension_management_service=getattr(self, "extension_management_service", None),
        )

    def bind_qq_delivery_port(self, delivery_port: Any | None) -> None:
        """Bind the live QQ transport port to QQ-only delivery handlers.

        The engine is constructed before the channel gateway, so the handlers
        are assembled first and receive the narrow port once the runtime owns a
        real gateway.  No delivery target or transport state enters prompts.
        """
        for tool_name in ("send_music_card", "send_audio", "onebot_action"):
            handler = self.tool_handlers.get(tool_name)
            binder = getattr(handler, "bind_delivery_port", None)
            if callable(binder):
                binder(delivery_port)

    def bind_plugin_hook_broker(self, broker: Any | None) -> None:
        """Bind the generation-aware Hook broker without changing tool schemas."""

        if broker is not None and not callable(getattr(broker, "dispatch_from_consumer", None)):
            raise TypeError("invalid_plugin_hook_broker")
        self.plugin_hook_broker = broker

    def _dispatch_plugin_hook(self, hook: PluginHookEnvelope) -> Any:
        broker = getattr(self, "plugin_hook_broker", None)
        dispatch = getattr(broker, "dispatch_from_consumer", None)
        if not callable(dispatch):
            return None
        try:
            return dispatch(hook)
        except Exception:
            logger.warning("plugin hook dispatch failed hook_type=%s", hook.hook_type)
            return None

    def _plugin_hook_observes(self, hook_type: str) -> bool:
        broker = getattr(self, "plugin_hook_broker", None)
        observes = getattr(broker, "observes", None)
        if not callable(observes):
            return False
        try:
            return bool(observes(hook_type))
        except Exception:
            return False

    @staticmethod
    def _tool_hook_result_status(result: ToolExecutionResult) -> tuple[str, str]:
        capability = result.capability_result
        if capability is not None and capability.is_error:
            status = str(capability.status or "")
            return ("cancelled" if capability.status == "cancelled" else "failed"), str(
                capability.reason or (status if status not in {"ok", "success", "succeeded", "completed"} else "")
                or "adapter_business_error")
        reason = ""
        for event in list(result.stream_events or []):
            if not isinstance(event, dict):
                continue
            status = str(event.get("status") or "").strip().lower()
            if event.get("is_error") is True:
                reason = str(event.get("reason") or status or "adapter_business_error").strip()
                return ("cancelled" if status == "cancelled" else "failed"), reason
            if status in {"failed", "error", "rejected", "unavailable", "cancelled"}:
                reason = str(event.get("reason") or status).strip()
                return status, reason
        if "<tool_use_error>" in str(result.followup_context or ""):
            return "failed", "tool_use_error"
        return "succeeded", ""

    def _resolve_tool_handlers(
        self,
        *,
        client_context: ClientProtocolContext | None = None,
        profile_user_id: str = "",
        session_id: str = "",
        domain_profile_id: str = "",
        capability_selection: CapabilitySelection | None = None,
    ) -> dict[str, BaseToolHandler]:
        from .engine_services.tool_rounds import resolve_tool_handlers as _fn

        return _fn(
            self,
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile_id,
            capability_selection=capability_selection,
        )

    def _build_loadable_capability_catalog(self, *, ready_tool_names, profile_user_id):
        """One catalog renderer for conversation and isolated task prompts."""
        parts, errors = [], []
        if "load_skill" in ready_tool_names:
            try:
                parts.append(str(self._get_skill_registry().prompt_catalog(
                    available_tool_names=set(ready_tool_names)) or "").strip())
            except Exception as exc:
                logger.warning("skill catalog projection failed: %s", type(exc).__name__)
                errors.append("Skill 目录当前读取失败；保留上一版目录。")
        text = "\n\n".join(part for part in parts if part)
        return ("" if errors else text or "【可按需加载的 Skill】\n- 当前没有可按需加载的 Skill。", errors)

    def _resolve_capability_selection(
        self,
        *,
        client_context: ClientProtocolContext | None = None,
        profile_user_id: str = "",
        session_id: str = "",
        domain_profile_id: str = "",
        intent_text: str = "",
        authorization_profile_user_id: str = "",
        character_pack_id: str = "",
    ) -> CapabilitySelection:
        from .engine_services.tool_rounds import resolve_capability_selection as _fn

        return _fn(
            self,
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile_id,
            intent_text=intent_text,
            authorization_profile_user_id=authorization_profile_user_id,
            character_pack_id=character_pack_id,
        )

    def _build_mcp_adapter_tool_handlers(
        self,
        *,
        profile_user_id: str = "",
        client_context: ClientProtocolContext | None = None,
    ) -> dict[str, BaseToolHandler]:
        from .engine_services.tool_rounds import build_mcp_adapter_tool_handlers as _fn

        return _fn(
            self,
            profile_user_id=profile_user_id,
            client_context=client_context,
        )

    def _build_python_adapter_tool_handlers(
        self,
        *,
        profile_user_id: str = "",
        client_context: ClientProtocolContext | None = None,
    ) -> dict[str, BaseToolHandler]:
        from .engine_services.tool_rounds import build_python_adapter_tool_handlers as _fn

        return _fn(
            self,
            profile_user_id=profile_user_id,
            client_context=client_context,
        )

    def _legacy_mode_tool_names(
        self,
        client_context: ClientProtocolContext,
        *,
        domain_profile_id: str = "",
    ) -> list[str]:
        from .engine_services.tool_rounds import legacy_mode_tool_names as _fn

        return _fn(self, client_context, domain_profile_id=domain_profile_id)

    def _build_capability_snapshot(
        self,
        *,
        client_context: ClientProtocolContext,
        profile_user_id: str,
        session_id: str,
    ) -> CapabilitySnapshot:
        from .engine_services.tool_rounds import build_capability_snapshot as _fn

        return _fn(
            self,
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
        )

    @staticmethod
    def _build_execution_host_context(
        handler: Any,
        *,
        profile_user_id: str = "",
        session_id: str = "",
        client_mode: str = "",
        actor_stable_id: str = "",
        actor_profile_user_id: str = "",
        execution_scope: Any = None,
    ) -> str:
        """Render one compact host fact block shared by native and legacy tools."""

        provider = getattr(handler, "execution_provider", None) if handler is not None else None
        describe = getattr(provider, "prompt_environment", None)
        if not callable(describe):
            describe = getattr(provider, "model_environment", None)
        if not callable(describe):
            return ""
        try:
            environment = dict(describe() or {})
        except Exception:
            return ""
        platform = str(environment.get("platform") or "host")
        command_shell = str(environment.get("command_shell") or "host-default")
        script_shell = str(environment.get("preferred_script_shell") or command_shell)
        host_access = environment.get("host_access") if isinstance(environment.get("host_access"), dict) else {}
        filesystem = str(host_access.get("filesystem") or "host_user_permissions")
        absolute_cwd = str(host_access.get("absolute_cwd") or "supported")
        dependency_storage = (
            environment.get("dependency_storage")
            if isinstance(environment.get("dependency_storage"), dict)
            else {}
        )
        runtime = str(dependency_storage.get("runtime") or "host_path")
        raw_credential_refs = (
            host_access.get("credential_env_refs")
            if isinstance(host_access.get("credential_env_refs"), dict)
            else {}
        )
        credential_refs = ", ".join(
            f"{name}={str(raw_credential_refs[name] or 'missing')}"
            for name in sorted(str(item) for item in raw_credential_refs)
        )
        credential_context = ""
        if credential_refs:
            credential_context = (
                f"\ncredential_env_refs: {credential_refs}\n"
                "命令可按当前 Shell 语法使用 configured 引用；真实值由执行器注入并从输出中遮蔽。"
            )
        cwd_state: dict[str, Any] = {}
        describe_cwd = getattr(handler, "working_directory_context", None)
        if execution_scope is not None:
            cwd_state = {"working_directory": execution_scope.working_directory, "project": "task"}
        elif callable(describe_cwd) and profile_user_id and session_id:
            try:
                cwd_state = dict(
                    describe_cwd(
                        profile_user_id=profile_user_id,
                        session_id=session_id,
                        client_mode=client_mode,
                        actor_stable_id=actor_stable_id,
                        actor_profile_user_id=actor_profile_user_id,
                    )
                    or {}
                )
            except Exception:
                cwd_state = {}
        working_directory = str(cwd_state.get("working_directory") or "execution_root").strip()
        project = str(cwd_state.get("project") or "none").strip()
        workspace_id = str(cwd_state.get("workspace_id") or "").strip()
        return (
            "Execution environment:\n"
            f"platform: {platform}\n"
            f"shell: {command_shell}\n"
            f"working_directory: {json.dumps(working_directory, ensure_ascii=False)}\n"
            f"project: {json.dumps(project, ensure_ascii=False)}\n"
            f"workspace_id: {workspace_id or 'none'}\n"
            f"access: filesystem={filesystem}; absolute_cwd={absolute_cwd}; runtime={runtime}; script_shell={script_shell}\n"
            "exec_run/status/cancel 操作的是此执行宿主，不依赖桌宠窗口在线；桌面工具操作的是绑定的桌面设备，两者可能不是同一台机器。"
            "停止自己启动的命令用 exec_cancel(run_id)；处理宿主上的其他进程可用 exec_run 先查进程再操作。"
            "某个专用工具不可用不等于宿主不能执行，仍可在用户授权范围内使用本轮提供的宿主命令工具。"
            "用户指向另一台电脑时先明确执行位置，不要把桌面设备的 PID 直接用于宿主命令。\n"
            "普通环境与 PATH 按宿主继承，凭据类和 Akane 内部变量除外；依赖缓存共享，项目依赖遵循清单与锁文件。"
            f"{credential_context}"
        )

    def _build_tool_prompt_context_sections(
        self,
        *,
        allow_tool_call: bool,
        client_context: ClientProtocolContext | None = None,
        profile_user_id: str = "",
        session_id: str = "",
        exclude_tool_types: set[str] | None = None,
        domain_profile_id: str = "",
        capability_selection: CapabilitySelection | None = None,
        include_capability_status: bool = True,
        actor_stable_id: str = "",
        actor_profile_user_id: str = "",
    ) -> dict[str, str]:
        if not allow_tool_call:
            return {
                "execution_context": "",
                "catalog_context": "",
                "catalog_status": "skipped",
                "round_context": "本轮不要调用任何工具；按当前模式的最终回复协议作答。",
            }

        selection = capability_selection or self._resolve_capability_selection(
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile_id,
        )
        handlers = self._resolve_tool_handlers(
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile_id,
            capability_selection=selection,
        )
        ready_tool_names = tuple(handlers)
        execution_handler = handlers.get("exec_run")
        raw_disclosures = tuple(getattr(selection, "disclosures", ()) or ())
        disclosures = (
            resolve_capability_disclosures(selection, available_tool_names=ready_tool_names)
            if include_capability_status and raw_disclosures
            else ()
        )
        capability_hints = tuple(selection.light_hints) if include_capability_status else ()
        excluded = {str(item).strip() for item in (exclude_tool_types or set()) if str(item).strip()}
        if excluded:
            handlers = {tool_type: handler for tool_type, handler in handlers.items() if str(tool_type) not in excluded}
        if getattr(selection, "capability_catalog", None) is not None:
            # Deferred contracts are disclosed by load results only, never
            # duplicated into the legacy prompt as a second exposure path.
            handlers = {name: handler for name, handler in handlers.items() if name in selection.schema_tool_names}
        execution_host_context = self._build_execution_host_context(
            execution_handler,
            profile_user_id=profile_user_id,
            session_id=session_id,
            client_mode=(client_context.effective_mode.value if client_context else ""),
            actor_stable_id=actor_stable_id,
            actor_profile_user_id=actor_profile_user_id,
        )
        catalog_context, catalog_errors = self._build_loadable_capability_catalog(
            ready_tool_names=ready_tool_names, profile_user_id=profile_user_id,
        )
        if getattr(selection, "capability_catalog", None) is not None:
            from .capability_exposure import directory_text
            catalog_context = "\n\n".join(part for part in (directory_text(selection), catalog_context) if part)
        catalog_status = "failed" if catalog_errors else "ready"
        media_routing: list[str] = []
        if "media_workbench" in selection.module_names:
            media_routing = [*MEDIA_PRESET_ROUTING, ""]

        def append_capability_context(parts: list[str]) -> None:
            ready = [item for item in disclosures if item.state == "ready"]
            latent = [item for item in disclosures if item.state == "latent"]
            unavailable = [item for item in disclosures if item.state == "unavailable"]
            disclosed_summaries = {item.summary for item in disclosures}
            extra_ready_hints = [hint for hint in capability_hints if hint and hint not in disclosed_summaries]
            if ready or extra_ready_hints:
                parts.append("【当前可用能力概览】")
                parts.extend(f"- {item.summary}" for item in ready)
                parts.extend(f"- {hint}" for hint in extra_ready_hints)
                parts.append("")
            elif not disclosures and capability_hints:
                parts.append("【能力概览】")
                parts.extend(f"- {hint}" for hint in capability_hints if hint)
                parts.append("")
            if latent:
                parts.append("【可按需激活的能力】")
                for item in latent:
                    detail = item.summary
                    if item.reason:
                        detail += f" 当前没有展开具体工具，因为：{item.reason}"
                    if item.activation:
                        detail += f" 激活方式：{item.activation}"
                    parts.append(f"- {detail}")
                parts.append("")
            if unavailable:
                parts.append("【暂不可用的能力】")
                for item in unavailable:
                    detail = item.summary
                    if item.reason:
                        detail += f" 当前没有暴露具体工具，因为：{item.reason}"
                    if item.activation:
                        detail += f" 恢复条件：{item.activation}"
                    parts.append(f"- {detail}")
                parts.append("")

        if not handlers:
            if not include_capability_status and excluded:
                direct_hint = (
                    "本轮可直接调用的工具及参数以请求中实际附带的工具定义为准；"
                    "不要把这些工具手写进最终 JSON 的兼容 tool_call 字段。"
                )
                return {
                    "execution_context": execution_host_context,
                    "catalog_context": catalog_context,
                    "catalog_status": catalog_status,
                    "round_context": direct_hint,
                }
            if not disclosures and not capability_hints and not media_routing:
                round_text = "当前没有可用工具；按当前模式的最终回复协议作答。"
                if catalog_errors:
                    round_text = "\n".join([*(f"- {item}" for item in catalog_errors), round_text])
                return {
                    "execution_context": execution_host_context,
                    "catalog_context": catalog_context,
                    "catalog_status": catalog_status,
                    "round_context": round_text,
                }
            parts: list[str] = []
            append_capability_context(parts)
            if catalog_errors:
                parts.extend(["【能力目录状态】", *(f"- {item}" for item in catalog_errors), ""])
            parts.extend(media_routing)
            parts.append("当前没有需要展开的具体工具；按当前模式的最终回复协议作答。")
            return {
                "execution_context": execution_host_context,
                "catalog_context": catalog_context,
                "catalog_status": catalog_status,
                "round_context": "\n".join(parts),
            }

        lines: list[str] = []
        append_capability_context(lines)
        if catalog_errors:
            lines.extend(["【能力目录状态】", *(f"- {item}" for item in catalog_errors), ""])
        if (
            client_context
            and client_context.effective_mode == ClientMode.DESKTOP_PET
            and "send_file" in selection.tool_names
        ):
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
        lines.extend(
            [
                "【当前可调用工具（兼容 JSON 通道）】",
                "以下工具没有出现在本轮直接工具入口中；需要时按各自格式写入最终 JSON 的 "
                "tool_call，一次一个。工具结果返回后再判断是否继续。",
            ]
        )
        for handler in handlers.values():
            instruction = str(handler.build_prompt_instruction() or "").strip()
            if instruction:
                lines.append(instruction)
        return {
            "execution_context": execution_host_context,
            "catalog_context": catalog_context,
            "catalog_status": catalog_status,
            "round_context": "\n".join(lines),
        }

    def _build_tool_prompt_context(
        self,
        **kwargs: Any,
    ) -> str:
        """Compatibility composition for diagnostics and focused unit tests.

        Production prompt assembly consumes the structured sections so runtime
        coordinates, the durable capability catalog and per-round guidance do
        not accidentally acquire one shared lifecycle again.
        """

        section_builder = getattr(self, "_build_tool_prompt_context_sections", None)
        if callable(section_builder):
            sections = section_builder(**kwargs)
        else:
            sections = AkaneMemoryEngine._build_tool_prompt_context_sections(self, **kwargs)
        return "\n\n".join(
            part
            for part in (
                str(sections.get("execution_context") or "").strip(),
                str(sections.get("catalog_context") or "").strip(),
                str(sections.get("round_context") or "").strip(),
            )
            if part
        )

    def _build_native_tool_round_instruction(self, native_tools: list[dict[str, Any]] | None) -> str:
        from .engine_services.tool_rounds import build_native_tool_round_instruction as _fn

        return _fn(native_tools)

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

    def _normalize_tool_call(
        self,
        value: Any,
        *,
        client_context: ClientProtocolContext | None = None,
        profile_user_id: str = "",
        session_id: str = "",
        domain_profile_id: str = "",
        capability_selection: Any = None,
    ) -> dict[str, Any] | None:
        return tool_orchestration_engine.normalize_tool_call(
            self,
            value,
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile_id,
            capability_selection=capability_selection,
        )

    def _describe_tool_call_rejection(
        self,
        value: Any,
        *,
        client_context: ClientProtocolContext | None = None,
        profile_user_id: str = "",
        session_id: str = "",
        domain_profile_id: str = "",
    ) -> str:
        return tool_orchestration_engine.classify_tool_call_rejection(
            self,
            value,
            client_context=client_context,
            profile_user_id=profile_user_id,
            session_id=session_id,
            domain_profile_id=domain_profile_id,
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
        domain_profile_id: str = "",
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
            domain_profile_id=domain_profile_id,
        )

    def _execute_tool_call_with_hooks(
        self,
        *,
        call: dict[str, Any],
        final_output: dict[str, Any],
        profile_user_id: str,
        session_id: str,
        character_pack_id: str,
        now_ts: int,
        current_user_source_id: str,
        client_context: ClientProtocolContext,
        memory_exclude_source_ids: list[str],
        request_context: dict[str, Any],
        domain_profile_id: str = "",
    ) -> ToolExecutionResult:
        """Execute one tool while publishing observation-only lifecycle Hooks."""

        tool_type = str(call.get("type") or "unknown").strip() or "unknown"
        if cancellation_requested():
            return cancelled_tool_result(tool_type)
        observes_before = self._plugin_hook_observes(BEFORE_TOOL_CALL_HOOK)
        observes_after = self._plugin_hook_observes(AFTER_TOOL_CALL_HOOK)
        hook_trace_id = ""
        if observes_before or observes_after:
            hook_trace_id = str(call.get(TOOL_INVOCATION_ID_FIELD) or "").strip()
            if not hook_trace_id:
                hook_trace_id = f"tool_{uuid.uuid4().hex[:20]}"
        if observes_before:
            tool_source = str(call.get(TOOL_SOURCE_FIELD) or "legacy_json").strip() or "legacy_json"
            arguments = {
                str(key): value
                for key, value in call.items()
                if key != "type" and not str(key).startswith("_tool_")
            }
            arguments_json = self._plugin_hook_arguments_json(arguments)
            self._dispatch_plugin_hook(
                PluginHookEnvelope(
                    hook_id=f"{hook_trace_id}:before",
                    hook_type=BEFORE_TOOL_CALL_HOOK,
                    occurred_at=max(now_ts, int(time.time())),
                    subject=f"tool:{tool_type}",
                    payload=PluginToolCallSnapshot(
                        invocation_id=hook_trace_id,
                        tool_name=tool_type,
                        source=tool_source,
                        profile_user_id=profile_user_id,
                        session_id=session_id,
                        character_pack_id=character_pack_id,
                        arguments_json=arguments_json,
                    ),
                )
            )
        started_at = time.monotonic()
        try:
            result = cancelled_tool_result(tool_type) if cancellation_requested() else self._execute_tool_call(
                profile_user_id=profile_user_id,
                session_id=session_id,
                character_pack_id=character_pack_id,
                tool_call=call,
                visual_payload=final_output,
                now_ts=now_ts,
                current_user_source_id=current_user_source_id,
                client_context=client_context,
                memory_exclude_source_ids=memory_exclude_source_ids,
                request_context=request_context,
                domain_profile_id=domain_profile_id,
            )
        except Exception as exc:
            result = ToolExecutionResult(
                tool_type=tool_type,
                stream_events=[
                    {
                        "type": "tool_execution_failed",
                        "tool_type": tool_type,
                        "status": "failed",
                        "reason": f"tool_exception:{type(exc).__name__}",
                    }
                ],
                followup_context=(
                    f"<tool_use_error>工具 {tool_type} 执行失败（{type(exc).__name__}）；"
                    "请结合本批其它结果继续处理，不要假设该工具已经成功。</tool_use_error>"
                ),
            )
        if result is None:
            result = ToolExecutionResult(
                tool_type=tool_type,
                stream_events=[
                    {
                        "type": "tool_execution_failed",
                        "tool_type": tool_type,
                        "status": "failed",
                        "reason": "empty_tool_result",
                    }
                ],
                followup_context="<tool_use_error>工具执行没有返回结果。</tool_use_error>",
            )
        if observes_after:
            hook_status, hook_reason = self._tool_hook_result_status(result)
            self._dispatch_plugin_hook(
                PluginHookEnvelope(
                    hook_id=f"{hook_trace_id}:after",
                    hook_type=AFTER_TOOL_CALL_HOOK,
                    occurred_at=int(time.time()),
                    subject=f"tool:{tool_type}",
                    payload=PluginToolResultSnapshot(
                        invocation_id=hook_trace_id,
                        tool_name=str(result.tool_type or tool_type),
                        status=hook_status,
                        duration_ms=round((time.monotonic() - started_at) * 1000, 3),
                        reason=hook_reason,
                        model_feedback=str(result.followup_context or ""),
                        event_types=tuple(
                            str(event.get("type") or "").strip()
                            for event in result.stream_events
                            if isinstance(event, dict) and str(event.get("type") or "").strip()
                        ),
                    ),
                )
            )
        return result

    @staticmethod
    def _plugin_hook_arguments_json(arguments: Mapping[str, Any]) -> str:
        """Freeze public arguments while keeping credentials out of snapshots."""

        def sanitize(value: Any, *, field_name: str = "") -> Any:
            normalized_name = re.sub(r"[^a-z0-9]", "", field_name.lower())
            if normalized_name.endswith(
                ("accesstoken", "apikey", "authorization", "cookie", "password", "secret", "token")
            ):
                return "<configured>"
            if isinstance(value, Mapping):
                return {
                    str(key): sanitize(item, field_name=str(key))
                    for key, item in value.items()
                }
            if isinstance(value, (list, tuple)):
                return [sanitize(item) for item in value]
            if value is None or isinstance(value, (str, int, float, bool)):
                return value
            return f"<non_json:{type(value).__name__}>"

        return json.dumps(
            sanitize(dict(arguments)),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
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
        last_content = normalize_text(str(last_record.get("content", "") or ""))
        current_content = normalize_text(user_message)
        last_role = str(last_record.get("role", "") or "").strip().lower()
        last_kind = str(last_record.get("kind", "") or "").strip().lower()
        current_stimulus = any(
            candidate == "user"
            or candidate == "message.user"
            or candidate.startswith("message.user.")
            or candidate.startswith("event.")
            for candidate in (last_role, last_kind)
        )
        if current_stimulus and last_content == current_content:
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
            rendered_content = AkaneMemoryEngine._render_memory_record_for_prompt(rec)
            if role == "assistant" or role.startswith("assistant."):
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
        return self._render_memory_record_for_prompt(current_user_record)

    @staticmethod
    def _render_memory_record_for_prompt(record: dict[str, Any]) -> str:
        role = str(record.get("role") or "user").strip()
        if role.lower().startswith("event."):
            try:
                from memcore.rendering import render_prompt_message

                timezone = str(getattr(config, "MEMCORE_TIMEZONE", "") or "Asia/Shanghai").strip() or "Asia/Shanghai"
                return render_prompt_message(dict(record), tz=timezone)
            except Exception:
                pass
        return render_chat_line(
            role=role,
            content=str(record.get("content") or ""),
            timestamp=record.get("timestamp"),
        )

    def _upsert_raw_record(self, record: dict[str, Any]) -> None:
        if not bool(record.get("index_in_vector", True)):
            return
        if self._memcore_owns_legacy_vector_index():
            record["index_in_vector"] = False
            source_id = str(record.get("source_id") or "").strip()
            update_index_flag = getattr(getattr(self, "store", None), "update_message_index_in_vector", None)
            if source_id and callable(update_index_flag):
                update_index_flag(source_id, False)
            return
        self.vector_store.upsert_entries([build_raw_vector_entry(record)])
