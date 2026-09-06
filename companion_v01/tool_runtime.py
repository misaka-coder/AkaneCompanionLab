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

from .browser_page_runtime import BrowserPageResult, ManagedBrowserPageRunner
from .capability_registry import (
    BROWSER_PAGE_TOOL_SPEC,
    BROWSE_MEMORY_TOOL_SPEC,
    CLEAR_ATTACHMENT_FOCUS_TOOL_SPEC,
    FETCH_MEDIA_FROM_URL_TOOL_SPEC,
    INSPECT_ATTACHMENT_TOOL_SPEC,
    INSPECT_GENERATED_FILE_TOOL_SPEC,
    INSPECT_MEDIA_INFO_TOOL_SPEC,
    LIST_WORKSPACE_TOOL_SPEC,
    LOAD_CHARACTER_CONTEXT_TOOL_SPEC,
    LOAD_MATERIAL_TOOL_SPEC,
    MANAGE_GENERATED_FILE_TOOL_SPEC,
    OPEN_BROWSER_TOOL_SPEC,
    OPEN_MUSIC_SEARCH_TOOL_SPEC,
    READ_ATTACHMENT_SECTION_TOOL_SPEC,
    OPEN_MEMORY_TOOL_SPEC,
    READ_MEMORY_TIMELINE_TOOL_SPEC,
    READ_WORKSPACE_TOOL_SPEC,
    REGISTER_WORKSPACE_ITEMS_TOOL_SPEC,
    RETRIEVE_MEMORY_TOOL_SPEC,
    RETRY_ATTACHMENT_TOOL_SPEC,
    SEND_AUDIO_TOOL_SPEC,
    SEND_FILE_TOOL_SPEC,
    SEND_MUSIC_CARD_TOOL_SPEC,
    SEND_STICKER_TOOL_SPEC,
    WEB_SEARCH_TOOL_SPEC,
)
from .local_capability_config import get_mcp_server_runtime_config
from .desktop_satellite_specs import desktop_satellite_spec
from .mcp_stdio_discoverer import McpStdioDiscoveryError, McpToolCaller
from .anysearch_rest_client import AnySearchRestClient, AnySearchRestError
from .store import MemoryStore
from .text_utils import normalize_text
from .workspace_files import WorkspaceFileService



from .tool_handlers.core import (
    BaseToolHandler,
    ToolExecutionAdmission,
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
from .tool_handlers.character_world import LoadCharacterContextToolHandler

from .tool_handlers.attachments import (
    ClearAttachmentFocusToolHandler,
    FetchMediaFromUrlToolHandler,
    InspectAttachmentToolHandler,
    LoadMaterialToolHandler,
    ReadAttachmentSectionToolHandler,
    RetryAttachmentToolHandler,
)
from .tool_handlers.workspace import (
    ListWorkspaceToolHandler,
    ReadWorkspaceToolHandler,
    RegisterWorkspaceItemsToolHandler,
)

from .tool_handlers.generated_media import (
    InspectGeneratedFileToolHandler,
    InspectMediaInfoToolHandler,
    ManageGeneratedFileToolHandler,
    SendFileToolHandler,
    SendStickerToolHandler,
)
from .tool_handlers.music import SendAudioToolHandler, SendMusicCardToolHandler
from .tool_handlers.web_browser import (
    BrowserPageToolHandler,
    OpenBrowserToolHandler,
    OpenMusicSearchToolHandler,
    WebSearchToolHandler,
)
from .tool_handlers.skills import LoadSkillToolHandler, ManageSkillToolHandler
from .skill_specs import LOAD_SKILL_TOOL_SPEC, MANAGE_SKILL_TOOL_SPEC
