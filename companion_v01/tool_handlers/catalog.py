"""Explicit assembly of static built-in tool handlers.

Runtime-owned handlers (such as subagents) bind in BotRuntime after their
services exist. This factory does not scan
modules, register anything globally, or read mutable global state; the engine
resolves the required services once and passes them as explicit keyword-only
arguments.
"""

from __future__ import annotations

from typing import Any

from .adapters import DesktopSatelliteToolHandler
from .computer_use import ComputerUseToolHandler
from .attachments import (
    ClearAttachmentFocusToolHandler,
    FetchMediaFromUrlToolHandler,
    InspectAttachmentToolHandler,
    LoadMaterialToolHandler,
    ReadAttachmentSectionToolHandler,
    RetryAttachmentToolHandler,
)
from .execution import ExecCancelToolHandler, ExecInputToolHandler, ExecRunToolHandler, ExecStatusToolHandler
from .extensions import ManageExtensionToolHandler
from .character_world import LoadCharacterContextToolHandler
from .core import BaseToolHandler
from .generated_media import (
    InspectGeneratedFileToolHandler,
    InspectMediaInfoToolHandler,
    ManageGeneratedFileToolHandler,
    SendFileToolHandler,
    SendStickerToolHandler,
)
from .memory import (
    BrowseMemoryToolHandler,
    OpenMemoryToolHandler,
    ReadMemoryTimelineToolHandler,
    RetrieveMemoryToolHandler,
)
from .music import SendAudioToolHandler, SendMusicCardToolHandler
from .mcp_management import InvokeMcpToolHandler, LoadMcpToolHandler, McpManageToolHandler
from .qq_onebot import OneBotActionToolHandler
from .project_workspace import (
    ManageProjectWorkspaceToolHandler,
    ProjectInspectToolHandler,
    WorkspacePatchToolHandler,
    WorkspaceWriteToolHandler,
)
from .skills import LoadSkillToolHandler, ManageSkillToolHandler
from .web_browser import (
    BrowserPageToolHandler,
    OpenBrowserToolHandler,
    OpenMusicSearchToolHandler,
    WebSearchToolHandler,
)
from .workspace import (
    ListWorkspaceToolHandler,
    ReadWorkspaceToolHandler,
    RegisterWorkspaceItemsToolHandler,
)


def build_builtin_tool_handlers(
    *,
    store: Any,
    sticker_assets: Any,
    capability_offer_source: Any,
    capability_config_base_dir: Any,
    memory_timeline_service: Any,
    context_libraries: Any,
    attachment_service: Any,
    image_material_resolver: Any,
    workspace_file_service: Any,
    attachment_ingest_service: Any,
    generated_file_service: Any,
    retrieve_fn: Any,
    skill_registry: Any | None = None,
    execution_provider: Any | None = None,
    approval_store: Any | None = None,
    project_workspace_service: Any | None = None,
    mcp_management_service: Any | None = None,
    extension_management_service: Any | None = None,
    executor_broker: Any | None = None,
) -> dict[str, BaseToolHandler]:
    """Construct every built-in handler from explicitly injected services.

    Optional image generation is supplied only through the plugin bridge.
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
        "inspect_attachment": InspectAttachmentToolHandler(attachment_service=attachment_service),
        "load_material": LoadMaterialToolHandler(image_material_resolver=image_material_resolver),
        "read_attachment_section": ReadAttachmentSectionToolHandler(
            attachment_service=attachment_service
        ),
        "clear_attachment_focus": ClearAttachmentFocusToolHandler(
            attachment_service=attachment_service,
        ),
        "list_workspace": ListWorkspaceToolHandler(workspace_service=workspace_file_service),
        "read_workspace": ReadWorkspaceToolHandler(workspace_service=workspace_file_service),
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
        "inspect_media_info": InspectMediaInfoToolHandler(
            generated_file_service=generated_file_service
        ),
        "inspect_generated_file": InspectGeneratedFileToolHandler(
            generated_file_service=generated_file_service
        ),
        "send_file": SendFileToolHandler(generated_file_service=generated_file_service,
                                        project_workspace_service=project_workspace_service),
        "send_sticker": SendStickerToolHandler(
            sticker_service=sticker_assets,
        ),
        "send_music_card": SendMusicCardToolHandler(),
        "send_audio": SendAudioToolHandler(generated_file_service=generated_file_service),
        "onebot_action": OneBotActionToolHandler(),
        "manage_generated_file": ManageGeneratedFileToolHandler(
            generated_file_service=generated_file_service,
            project_workspace_service=project_workspace_service,
        ),
        "web_search": WebSearchToolHandler(
            config_base_dir=capability_config_base_dir,
        ),
        "open_browser": OpenBrowserToolHandler(offer_source=capability_offer_source),
        "computer_use": ComputerUseToolHandler(offer_source=capability_offer_source),
        "desktop_screenshot": DesktopSatelliteToolHandler(
            tool_id="desktop_screenshot",
            offer_source=capability_offer_source,
        ),
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
        "browser_page": BrowserPageToolHandler(
            personal_offer_source=capability_offer_source,
            executor_broker=executor_broker,
            approval_store=approval_store,
            generated_file_service=generated_file_service,
            attachment_service=attachment_service,
            image_material_resolver=image_material_resolver,
            config_base_dir=capability_config_base_dir,
        ),
    }
    if skill_registry is not None:
        handlers["load_skill"] = LoadSkillToolHandler(registry=skill_registry)
    if mcp_management_service is not None:
        handlers["load_mcp"] = LoadMcpToolHandler(service=mcp_management_service)
        handlers["invoke_mcp"] = InvokeMcpToolHandler()
        handlers["mcp_manage"] = McpManageToolHandler(
            service=mcp_management_service,
            approval_store=approval_store,
            config_base_dir=capability_config_base_dir,
        )
    if extension_management_service is not None:
        handlers["manage_extension"] = ManageExtensionToolHandler(
            service=extension_management_service,
            approval_store=approval_store,
            config_base_dir=capability_config_base_dir,
        )
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
            project_workspace_service=project_workspace_service,
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
        handlers["exec_input"] = ExecInputToolHandler(
            execution_provider=execution_provider,
            config_base_dir=capability_config_base_dir,
            approval_store=approval_store,
        )
        if skill_registry is not None:
            handlers["manage_skill"] = ManageSkillToolHandler(
                registry=skill_registry,
                approval_store=approval_store,
                config_base_dir=capability_config_base_dir,
            )
        if project_workspace_service is not None:
            handlers["manage_project_workspace"] = ManageProjectWorkspaceToolHandler(
                service=project_workspace_service
            )
            handlers["project_inspect"] = ProjectInspectToolHandler(
                service=project_workspace_service,
                execution_provider=execution_provider,
            )
            handlers["workspace_write"] = WorkspaceWriteToolHandler(
                service=project_workspace_service,
                execution_provider=execution_provider,
            )
            handlers["workspace_patch"] = WorkspacePatchToolHandler(
                service=project_workspace_service,
                execution_provider=execution_provider,
            )
    return handlers
