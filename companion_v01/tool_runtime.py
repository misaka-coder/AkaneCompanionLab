"""Compatibility facade: re-exports all tool handler contracts and domain handlers.

Single authoritative implementations live in ``companion_v01.tool_handlers.*``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import ipaddress
import inspect
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote_plus, urlparse

import config
from capcore import build_permission_request as capcore_build_permission_request
from capcore import build_tool_spec as capcore_build_tool_spec
from capcore import validate_invocation_args as capcore_validate_invocation_args

from .browser_page_runtime import BrowserPageResult, ManagedBrowserPageRunner
from .capcore_runtime import (
    approval_required_event as capcore_approval_required_event,
    invocation_context_from_execution as capcore_invocation_context_from_execution,
    manual_permission_request as capcore_manual_permission_request,
    resolve_permission_for_profile as capcore_resolve_permission_for_profile,
)
from .capability_adapters import CapabilityProtocolError, InvocationContext
from .capability_registry import (
    APPLY_STYLE_TO_EXISTING_FILE_TOOL_SPEC,
    BROWSER_PAGE_TOOL_SPEC,
    BROWSE_MEMORY_TOOL_SPEC,
    CALL_NPC_TOOL_SPEC,
    CANCEL_REMINDER_TOOL_SPEC,
    CHECK_INVENTORY_TOOL_SPEC,
    CLEAR_ATTACHMENT_FOCUS_TOOL_SPEC,
    COMPOSE_FILE_TOOL_SPEC,
    CONVERT_MEDIA_FILE_TOOL_SPEC,
    COVER_SONG_TOOL_SPEC,
    CLEAN_VOICE_TRACK_TOOL_SPEC,
    DELEGATE_TASK_TOOL_SPEC,
    FETCH_MEDIA_FROM_URL_TOOL_SPEC,
    FOCUS_WORKSPACE_TOOL_SPEC,
    GENERATE_IMAGE_TOOL_SPEC,
    INSPECT_ATTACHMENT_TOOL_SPEC,
    INSPECT_GENERATED_FILE_TOOL_SPEC,
    INSPECT_MEDIA_INFO_TOOL_SPEC,
    LIST_REMINDERS_TOOL_SPEC,
    LIST_WORKSPACE_TOOL_SPEC,
    LOAD_CHARACTER_CONTEXT_TOOL_SPEC,
    LOAD_MATERIAL_TOOL_SPEC,
    MANAGE_ARTIFACT_TOOL_SPEC,
    MANAGE_GENERATED_FILE_TOOL_SPEC,
    MANAGE_GIFT_TOOL_SPEC,
    MANAGE_PERSONA_TOOL_SPEC,
    MANAGE_TASK_WORKSPACE_TOOL_SPEC,
    OPEN_BROWSER_TOOL_SPEC,
    OPEN_MUSIC_SEARCH_TOOL_SPEC,
    PREPARE_VOICE_DATASET_TOOL_SPEC,
    READ_ATTACHMENT_SECTION_TOOL_SPEC,
    OPEN_MEMORY_TOOL_SPEC,
    READ_MEMORY_TIMELINE_TOOL_SPEC,
    READ_WORKSPACE_TOOL_SPEC,
    REGISTER_WORKSPACE_ITEMS_TOOL_SPEC,
    RETRIEVE_MEMORY_TOOL_SPEC,
    RETRY_ATTACHMENT_TOOL_SPEC,
    REVISE_GENERATED_FILE_TOOL_SPEC,
    SEND_FILE_TOOL_SPEC,
    SEND_STICKER_TOOL_SPEC,
    SEPARATE_AUDIO_STEMS_TOOL_SPEC,
    SET_REMINDER_TOOL_SPEC,
    SYNC_ATTACHMENT_WORKSPACE_TOOL_SPEC,
    TRANSCRIBE_MEDIA_TOOL_SPEC,
    WEB_SEARCH_TOOL_SPEC,
)
from .local_capability_config import get_mcp_server_runtime_config
from .desktop_satellite_specs import desktop_satellite_spec
from .mcp_stdio_discoverer import McpStdioDiscoveryError, McpToolCaller
from .anysearch_rest_client import AnySearchRestClient, AnySearchRestError
from .npc_runtime import GenericNPCRuntime
from .store import MemoryStore
from .task_workspace import TaskWorkspaceService
from .text_utils import normalize_text, resolve_reminder_due_timestamp, timestamp_to_datetime_label
from .workspace_files import WorkspaceFileService



from .tool_handlers.core import (
    BaseToolHandler,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolFollowupEnvelope,
    ToolMetadata,
    operation_tool_result,
    TOOL_METADATA_BY_TYPE,
    TOOL_SPEC_BY_TYPE,
)


from .tool_handlers.adapters import (
    AdapterCapabilityToolHandler,
    DesktopSatelliteToolHandler,
)
from .tool_handlers.memory import (
    BrowseMemoryToolHandler,
    OpenMemoryToolHandler,
    ReadMemoryTimelineToolHandler,
    RetrieveMemoryToolHandler,
)
from .tool_handlers.character_world import (
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

from .tool_handlers.attachments import (
    ClearAttachmentFocusToolHandler,
    FetchMediaFromUrlToolHandler,
    InspectAttachmentToolHandler,
    LoadMaterialToolHandler,
    ReadAttachmentSectionToolHandler,
    RetryAttachmentToolHandler,
    SyncAttachmentWorkspaceToolHandler,
)
from .tool_handlers.workspace import (
    FocusWorkspaceToolHandler,
    ListWorkspaceToolHandler,
    ManageTaskWorkspaceToolHandler,
    ReadWorkspaceToolHandler,
    RegisterWorkspaceItemsToolHandler,
)

from .tool_handlers.generated_media import (
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
from .tool_handlers.web_browser import (
    BrowserPageToolHandler,
    OpenBrowserToolHandler,
    OpenMusicSearchToolHandler,
    WebSearchToolHandler,
)
from .tool_handlers.skills import LoadSkillToolHandler, ManageSkillToolHandler
from .skill_specs import LOAD_SKILL_TOOL_SPEC, MANAGE_SKILL_TOOL_SPEC
