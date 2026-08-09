"""Explicit assembly of every built-in tool handler.

This is the single construction site for built-in handlers. It does not scan
modules, register anything globally, or read mutable global state; the engine
resolves the required services once and passes them as explicit keyword-only
arguments.
"""

from __future__ import annotations

from typing import Any

from ..task_worker_tool import DelegateTaskToolHandler
from .adapters import DesktopSatelliteToolHandler
from .attachments import (
    ClearAttachmentFocusToolHandler,
    FetchMediaFromUrlToolHandler,
    InspectAttachmentToolHandler,
    LoadMaterialToolHandler,
    ReadAttachmentSectionToolHandler,
    RetryAttachmentToolHandler,
    SyncAttachmentWorkspaceToolHandler,
)
from .execution import ExecCancelToolHandler, ExecRunToolHandler, ExecStatusToolHandler
from .character_world import (
    CallNPCToolHandler,
    CancelReminderToolHandler,
    CheckInventoryToolHandler,
    ListRemindersToolHandler,
    LoadCharacterContextToolHandler,
    ManageArtifactToolHandler,
    ManageGiftToolHandler,
    ManagePersonaToolHandler,
    SetReminderToolHandler,
)
from .core import BaseToolHandler
from .generated_media import (
    ApplyStyleToExistingFileToolHandler,
    CleanVoiceTrackToolHandler,
    ComposeFileToolHandler,
    ConvertMediaFileToolHandler,
    CoverSongToolHandler,
    GenerateImageToolHandler,
    InspectGeneratedFileToolHandler,
    InspectMediaInfoToolHandler,
    ManageGeneratedFileToolHandler,
    PrepareVoiceDatasetToolHandler,
    ReviseGeneratedFileToolHandler,
    SendFileToolHandler,
    SendStickerToolHandler,
    SeparateAudioStemsToolHandler,
    TranscribeMediaToolHandler,
)
from .memory import (
    BrowseMemoryToolHandler,
    OpenMemoryToolHandler,
    ReadMemoryTimelineToolHandler,
    RetrieveMemoryToolHandler,
)
from .web_browser import (
    BrowserPageToolHandler,
    OpenBrowserToolHandler,
    OpenMusicSearchToolHandler,
    WebSearchToolHandler,
)
from .workspace import (
    FocusWorkspaceToolHandler,
    ListWorkspaceToolHandler,
    ManageTaskWorkspaceToolHandler,
    ReadWorkspaceToolHandler,
    RegisterWorkspaceItemsToolHandler,
)


def build_builtin_tool_handlers(
    *,
    store: Any,
    npc_runtime: Any,
    gift_service: Any,
    artifact_service: Any,
    persona_card_service: Any,
    sticker_assets: Any,
    capability_offer_source: Any,
    capability_config_base_dir: Any,
    memory_timeline_service: Any,
    context_libraries: Any,
    attachment_service: Any,
    image_material_resolver: Any,
    task_workspace_service: Any,
    workspace_file_service: Any,
    attachment_ingest_service: Any,
    generated_file_service: Any,
    image_generation_service: Any | None,
    cover_song_service: Any | None,
    task_worker_service: Any,
    retrieve_fn: Any,
    describe_scene: Any,
    build_npc_followup_context: Any,
    observe_gift_image_fn: Any,
    execution_provider: Any | None = None,
    approval_store: Any | None = None,
) -> dict[str, BaseToolHandler]:
    """Construct every built-in handler from explicitly injected services.

    Handler keys, construction order and conditional availability (generate_image
    and cover_song only when their service is available) mirror the previous
    engine-owned assembly exactly.
    """
    handlers: dict[str, BaseToolHandler] = {
        "retrieve_memory": RetrieveMemoryToolHandler(
            retrieve_fn=retrieve_fn,
        ),
        "read_memory_timeline": ReadMemoryTimelineToolHandler(
            timeline_service=memory_timeline_service,
        ),
        "browse_memory": BrowseMemoryToolHandler(
            timeline_service=memory_timeline_service,
        ),
        "open_memory": OpenMemoryToolHandler(
            timeline_service=memory_timeline_service,
        ),
        "load_character_context": LoadCharacterContextToolHandler(
            context_library_service=context_libraries,
        ),
        "call_npc": CallNPCToolHandler(
            npc_runtime=npc_runtime,
            describe_scene=describe_scene,
            build_followup_context=build_npc_followup_context,
        ),
        "set_reminder": SetReminderToolHandler(store=store),
        "list_reminders": ListRemindersToolHandler(store=store),
        "cancel_reminder": CancelReminderToolHandler(store=store),
        "check_inventory": CheckInventoryToolHandler(gift_service=gift_service),
        "inspect_attachment": InspectAttachmentToolHandler(attachment_service=attachment_service),
        "load_material": LoadMaterialToolHandler(image_material_resolver=image_material_resolver),
        "read_attachment_section": ReadAttachmentSectionToolHandler(
            attachment_service=attachment_service
        ),
        "sync_attachment_workspace": SyncAttachmentWorkspaceToolHandler(
            attachment_service=attachment_service
        ),
        "clear_attachment_focus": ClearAttachmentFocusToolHandler(
            attachment_service=attachment_service,
            task_workspace_service=task_workspace_service,
        ),
        "list_workspace": ListWorkspaceToolHandler(workspace_service=workspace_file_service),
        "read_workspace": ReadWorkspaceToolHandler(workspace_service=workspace_file_service),
        "focus_workspace": FocusWorkspaceToolHandler(workspace_service=workspace_file_service),
        "register_workspace_items": RegisterWorkspaceItemsToolHandler(
            workspace_service=workspace_file_service,
            attachment_ingest_service=attachment_ingest_service,
        ),
        "retry_attachment": RetryAttachmentToolHandler(
            attachment_ingest_service=attachment_ingest_service
        ),
        "fetch_media_from_url": FetchMediaFromUrlToolHandler(
            attachment_ingest_service=attachment_ingest_service
        ),
        "compose_file": ComposeFileToolHandler(generated_file_service=generated_file_service),
        "revise_generated_file": ReviseGeneratedFileToolHandler(
            generated_file_service=generated_file_service
        ),
        "apply_style_to_existing_file": ApplyStyleToExistingFileToolHandler(
            generated_file_service=generated_file_service
        ),
        "inspect_media_info": InspectMediaInfoToolHandler(
            generated_file_service=generated_file_service
        ),
        "separate_audio_stems": SeparateAudioStemsToolHandler(
            generated_file_service=generated_file_service
        ),
        "clean_voice_track": CleanVoiceTrackToolHandler(
            generated_file_service=generated_file_service
        ),
        "transcribe_media": TranscribeMediaToolHandler(
            generated_file_service=generated_file_service
        ),
        "prepare_voice_dataset": PrepareVoiceDatasetToolHandler(
            generated_file_service=generated_file_service
        ),
        "inspect_generated_file": InspectGeneratedFileToolHandler(
            generated_file_service=generated_file_service
        ),
        "send_file": SendFileToolHandler(generated_file_service=generated_file_service),
        "convert_media_file": ConvertMediaFileToolHandler(
            generated_file_service=generated_file_service
        ),
        "send_sticker": SendStickerToolHandler(
            sticker_service=sticker_assets,
        ),
        "manage_generated_file": ManageGeneratedFileToolHandler(
            generated_file_service=generated_file_service,
            task_workspace_service=task_workspace_service,
        ),
        "manage_gift": ManageGiftToolHandler(
            gift_service=gift_service,
            observe_image_fn=observe_gift_image_fn,
        ),
        "manage_artifact": ManageArtifactToolHandler(
            artifact_service=artifact_service,
        ),
        "manage_persona": ManagePersonaToolHandler(
            persona_service=persona_card_service,
        ),
        "manage_task_workspace": ManageTaskWorkspaceToolHandler(
            task_workspace_service=task_workspace_service,
        ),
        "delegate_task": DelegateTaskToolHandler(
            task_worker_service=task_worker_service,
        ),
        "web_search": WebSearchToolHandler(
            config_base_dir=capability_config_base_dir,
        ),
        "open_browser": OpenBrowserToolHandler(),
        "desktop_context_snapshot": DesktopSatelliteToolHandler(
            tool_id="desktop_context_snapshot",
            offer_source=capability_offer_source,
        ),
        "system_media_snapshot": DesktopSatelliteToolHandler(
            tool_id="system_media_snapshot",
            offer_source=capability_offer_source,
        ),
        "system_media_control": DesktopSatelliteToolHandler(
            tool_id="system_media_control",
            offer_source=capability_offer_source,
        ),
        "system_process_snapshot": DesktopSatelliteToolHandler(
            tool_id="system_process_snapshot",
            offer_source=capability_offer_source,
        ),
        "system_process_terminate": DesktopSatelliteToolHandler(
            tool_id="system_process_terminate",
            offer_source=capability_offer_source,
        ),
        "system_volume": DesktopSatelliteToolHandler(
            tool_id="system_volume",
            offer_source=capability_offer_source,
        ),
        "open_music_search": OpenMusicSearchToolHandler(),
        "browser_page": BrowserPageToolHandler(),
    }
    if execution_provider is not None:
        resource_bridge = None
        workspace_root = getattr(execution_provider, "workspace_root", None)
        if generated_file_service is not None and workspace_root is not None:
            from ..execution_resources import ExecutionResourceBridge

            resource_bridge = ExecutionResourceBridge(
                generated_file_service=generated_file_service,
                workspace_root=workspace_root,
            )
        handlers["exec_run"] = ExecRunToolHandler(
            execution_provider=execution_provider,
            config_base_dir=capability_config_base_dir,
            approval_store=approval_store,
            resource_bridge=resource_bridge,
        )
        handlers["exec_status"] = ExecStatusToolHandler(
            execution_provider=execution_provider,
            config_base_dir=capability_config_base_dir,
            approval_store=approval_store,
            resource_bridge=resource_bridge,
        )
        handlers["exec_cancel"] = ExecCancelToolHandler(
            execution_provider=execution_provider,
            config_base_dir=capability_config_base_dir,
            approval_store=approval_store,
            resource_bridge=resource_bridge,
        )
    if image_generation_service is not None:
        handlers["generate_image"] = GenerateImageToolHandler(
            image_generation_service=image_generation_service,
        )
    if cover_song_service is not None:
        handlers["cover_song"] = CoverSongToolHandler(cover_song_service=cover_song_service)
    return handlers
