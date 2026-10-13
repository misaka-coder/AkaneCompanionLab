"""Public capability contracts and per-turn selection API.

Implementation lives in ``capability_registry_runtime``; ``executor_broker``
owns execution authorization and dispatch.
"""

from capcore import CapabilityToolSpec
from memcore import build_native_memory_tool_specs
from .client_protocol import ClientMode
from .desktop_satellite_specs import DESKTOP_SATELLITE_TOOL_SPECS
from companion_v01.channels.qq.onebot.model_actions import MODEL_ONEBOT_ACTION_NAMES
from companion_v01.channels.qq.onebot.model_actions import MODEL_ONEBOT_MESSAGE_SELECTOR_KINDS
from .tool_continuation import FINISH_TURN_PARAMETER
from .executor_broker import (
    ExecutionReceipt,
    BrokerExecutionResult,
    ServerLocalBrokerResult,
    CapabilityOfferSource,
    ExecutorBroker,
)
from .capability_registry_runtime.memory_contracts import (
    RETRIEVE_MEMORY_TOOL_SPEC,
    READ_MEMORY_TIMELINE_TOOL_SPEC,
    BROWSE_MEMORY_TOOL_SPEC,
    OPEN_MEMORY_TOOL_SPEC,
    LIST_MEMORY_CONVERSATIONS_TOOL_SPEC,
    LOAD_CHARACTER_CONTEXT_TOOL_SPEC,
)
from .capability_registry_runtime.browser_contracts import (
    OPEN_BROWSER_TOOL_SPEC,
    WEB_SEARCH_TOOL_SPEC,
    BROWSER_PAGE_TOOL_SPEC,
    BROWSER_EXTRA_PROPERTIES,
)
from .capability_registry_runtime.attachment_contracts import (
    INSPECT_ATTACHMENT_TOOL_SPEC,
    LOAD_MATERIAL_TOOL_SPEC,
    RETRY_ATTACHMENT_TOOL_SPEC,
    CLEAR_ATTACHMENT_FOCUS_TOOL_SPEC,
    READ_ATTACHMENT_SECTION_TOOL_SPEC,
)
from .capability_registry_runtime.workspace_contracts import (
    LIST_WORKSPACE_TOOL_SPEC,
    READ_WORKSPACE_TOOL_SPEC,
    REGISTER_WORKSPACE_ITEMS_TOOL_SPEC,
    INSPECT_GENERATED_FILE_TOOL_SPEC,
    MANAGE_GENERATED_FILE_TOOL_SPEC,
)
from .capability_registry_runtime.delivery_contracts import (
    SEND_FILE_TOOL_SPEC,
    SEND_STICKER_TOOL_SPEC,
    ONEBOT_ACTION_TOOL_SPEC,
    SEND_MUSIC_CARD_TOOL_SPEC,
    SEND_AUDIO_TOOL_SPEC,
)
from .capability_registry_runtime.media_contracts import (
    OPEN_MUSIC_SEARCH_TOOL_SPEC,
    FETCH_MEDIA_FROM_URL_TOOL_SPEC,
    INSPECT_MEDIA_INFO_TOOL_SPEC,
)
from .capability_registry_runtime.context import (
    DOCUMENT_ATTACHMENT_FORMATS,
    DOCUMENT_GENERATED_FORMATS,
    MEDIA_FORMATS,
    IMAGE_GENERATED_FORMATS,
    is_document_attachment,
    is_media_attachment,
    is_image_attachment,
    is_document_generated_file,
    is_media_generated_file,
    is_image_generated_file,
)
from .capability_registry_runtime.models import (
    CapabilitySnapshot,
    CapabilityModule,
    CapabilitySelection,
    CapabilityDisclosure,
    resolve_capability_disclosures,
)
from .capability_registry_runtime.modules import (
    COMMON_CLIENT_MODES,
    CHAT_FILE_CLIENT_MODES,
    COMMON_TOOL_NAMES,
    WEB_SEARCH_TOOL_NAMES,
    EXEC_TOOL_NAMES,
    EXTENSION_MANAGEMENT_TOOL_NAMES,
    DESKTOP_BROWSER_TOOL_NAMES,
    DESKTOP_MUSIC_REQUEST_TOOL_NAMES,
    DESKTOP_WORKSPACE_TOOL_NAMES,
    REMOTE_MEDIA_TOOL_NAMES,
    ATTACHMENT_WORKSPACE_TOOL_NAMES,
    IMAGE_MATERIAL_TOOL_NAMES,
    DOCUMENT_WORKBENCH_TOOL_NAMES,
    MEDIA_WORKBENCH_TOOL_NAMES,
    MEDIA_WORKBENCH_SHELL_OVERLAP_TOOL_NAMES,
    GENERATED_FILE_MANAGEMENT_TOOL_NAMES,
    FILE_HANDOFF_TOOL_NAMES,
    TEXT_CARD_TOOL_NAMES,
    QQ_STICKER_TOOL_NAMES,
    QQ_ONEBOT_ACTION_TOOL_NAMES,
    QQ_MUSIC_CARD_TOOL_NAMES,
    QQ_AUDIO_DELIVERY_TOOL_NAMES,
)
from .capability_registry_runtime.offers import ServerLocalOfferIndex
from .capability_registry_runtime.selection import CapabilityRegistry


__all__ = (
    "CapabilityToolSpec", "build_native_memory_tool_specs", "ClientMode",
    "DESKTOP_SATELLITE_TOOL_SPECS", "MODEL_ONEBOT_ACTION_NAMES",
    "MODEL_ONEBOT_MESSAGE_SELECTOR_KINDS", "FINISH_TURN_PARAMETER", "ExecutionReceipt",
    "BrokerExecutionResult", "ServerLocalBrokerResult", "CapabilityOfferSource", "ExecutorBroker",
    "RETRIEVE_MEMORY_TOOL_SPEC", "READ_MEMORY_TIMELINE_TOOL_SPEC", "BROWSE_MEMORY_TOOL_SPEC",
    "OPEN_MEMORY_TOOL_SPEC", "LIST_MEMORY_CONVERSATIONS_TOOL_SPEC",
    "LOAD_CHARACTER_CONTEXT_TOOL_SPEC", "OPEN_BROWSER_TOOL_SPEC", "WEB_SEARCH_TOOL_SPEC",
    "BROWSER_PAGE_TOOL_SPEC", "BROWSER_EXTRA_PROPERTIES", "INSPECT_ATTACHMENT_TOOL_SPEC",
    "LOAD_MATERIAL_TOOL_SPEC", "RETRY_ATTACHMENT_TOOL_SPEC", "CLEAR_ATTACHMENT_FOCUS_TOOL_SPEC",
    "READ_ATTACHMENT_SECTION_TOOL_SPEC", "LIST_WORKSPACE_TOOL_SPEC", "READ_WORKSPACE_TOOL_SPEC",
    "REGISTER_WORKSPACE_ITEMS_TOOL_SPEC", "INSPECT_GENERATED_FILE_TOOL_SPEC",
    "MANAGE_GENERATED_FILE_TOOL_SPEC", "SEND_FILE_TOOL_SPEC", "SEND_STICKER_TOOL_SPEC",
    "ONEBOT_ACTION_TOOL_SPEC", "SEND_MUSIC_CARD_TOOL_SPEC", "SEND_AUDIO_TOOL_SPEC",
    "OPEN_MUSIC_SEARCH_TOOL_SPEC", "FETCH_MEDIA_FROM_URL_TOOL_SPEC",
    "INSPECT_MEDIA_INFO_TOOL_SPEC", "DOCUMENT_ATTACHMENT_FORMATS", "DOCUMENT_GENERATED_FORMATS",
    "MEDIA_FORMATS", "IMAGE_GENERATED_FORMATS", "is_document_attachment", "is_media_attachment",
    "is_image_attachment", "is_document_generated_file", "is_media_generated_file",
    "is_image_generated_file", "CapabilitySnapshot", "CapabilityModule", "CapabilitySelection",
    "CapabilityDisclosure", "resolve_capability_disclosures", "COMMON_CLIENT_MODES",
    "CHAT_FILE_CLIENT_MODES", "COMMON_TOOL_NAMES", "WEB_SEARCH_TOOL_NAMES", "EXEC_TOOL_NAMES",
    "EXTENSION_MANAGEMENT_TOOL_NAMES", "DESKTOP_BROWSER_TOOL_NAMES",
    "DESKTOP_MUSIC_REQUEST_TOOL_NAMES", "DESKTOP_WORKSPACE_TOOL_NAMES", "REMOTE_MEDIA_TOOL_NAMES",
    "ATTACHMENT_WORKSPACE_TOOL_NAMES", "IMAGE_MATERIAL_TOOL_NAMES",
    "DOCUMENT_WORKBENCH_TOOL_NAMES", "MEDIA_WORKBENCH_TOOL_NAMES",
    "MEDIA_WORKBENCH_SHELL_OVERLAP_TOOL_NAMES", "GENERATED_FILE_MANAGEMENT_TOOL_NAMES",
    "FILE_HANDOFF_TOOL_NAMES", "TEXT_CARD_TOOL_NAMES", "QQ_STICKER_TOOL_NAMES",
    "QQ_ONEBOT_ACTION_TOOL_NAMES", "QQ_MUSIC_CARD_TOOL_NAMES", "QQ_AUDIO_DELIVERY_TOOL_NAMES",
    "ServerLocalOfferIndex", "CapabilityRegistry",
)
