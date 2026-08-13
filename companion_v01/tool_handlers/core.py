"""Shared tool-handler contract: context, result, metadata and base handler.

Owns only the cross-domain protocol types and the canonical ToolSpec/metadata
projection tables. No domain handler implementation lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping

from ..capability_registry import (
    APPLY_STYLE_TO_EXISTING_FILE_TOOL_SPEC,
    BROWSER_PAGE_TOOL_SPEC,
    BROWSE_MEMORY_TOOL_SPEC,
    CALL_NPC_TOOL_SPEC,
    CANCEL_REMINDER_TOOL_SPEC,
    CHECK_INVENTORY_TOOL_SPEC,
    CLEAN_VOICE_TRACK_TOOL_SPEC,
    CLEAR_ATTACHMENT_FOCUS_TOOL_SPEC,
    COMPOSE_FILE_TOOL_SPEC,
    CONVERT_MEDIA_FILE_TOOL_SPEC,
    COVER_SONG_TOOL_SPEC,
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
    OPEN_MEMORY_TOOL_SPEC,
    OPEN_MUSIC_SEARCH_TOOL_SPEC,
    PREPARE_VOICE_DATASET_TOOL_SPEC,
    READ_ATTACHMENT_SECTION_TOOL_SPEC,
    READ_MEMORY_TIMELINE_TOOL_SPEC,
    READ_WORKSPACE_TOOL_SPEC,
    REGISTER_WORKSPACE_ITEMS_TOOL_SPEC,
    RETRIEVE_MEMORY_TOOL_SPEC,
    RETRY_ATTACHMENT_TOOL_SPEC,
    REVISE_GENERATED_FILE_TOOL_SPEC,
    SEND_AUDIO_TOOL_SPEC,
    SEND_FILE_TOOL_SPEC,
    SEND_MUSIC_CARD_TOOL_SPEC,
    SEND_STICKER_TOOL_SPEC,
    SEPARATE_AUDIO_STEMS_TOOL_SPEC,
    SET_REMINDER_TOOL_SPEC,
    SYNC_ATTACHMENT_WORKSPACE_TOOL_SPEC,
    TRANSCRIBE_MEDIA_TOOL_SPEC,
    WEB_SEARCH_TOOL_SPEC,
)
from ..execution_specs import EXEC_CANCEL_TOOL_SPEC, EXEC_RUN_TOOL_SPEC, EXEC_STATUS_TOOL_SPEC
from ..skill_specs import LOAD_SKILL_TOOL_SPEC, MANAGE_SKILL_TOOL_SPEC


@dataclass(frozen=True)
class ToolExecutionContext:
    profile_user_id: str
    session_id: str
    now_ts: int
    visual_payload: dict[str, Any]
    character_pack_id: str = ""
    current_user_source_id: str = ""
    client_mode: str = ""
    request_context: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolFollowupEnvelope:
    """Model-facing tool evidence whose producer owns completeness and continuation."""

    content: str
    producer_bounded: bool = False
    complete: bool = True
    continuation: Mapping[str, Any] | None = None
    diagnostics: Mapping[str, Any] | None = None

    def with_content(self, content: Any) -> "ToolFollowupEnvelope":
        return ToolFollowupEnvelope(
            content=str(content or ""),
            producer_bounded=bool(self.producer_bounded),
            complete=bool(self.complete),
            continuation=dict(self.continuation or {}) or None,
            diagnostics=dict(self.diagnostics or {}) or None,
        )


@dataclass
class ToolExecutionResult:
    tool_type: str
    raw_turns: list[dict[str, Any]] = field(default_factory=list)
    stream_events: list[dict[str, Any]] = field(default_factory=list)
    followup_context: str = ""
    followup_envelope: ToolFollowupEnvelope | None = None
    state_updates: dict[str, Any] = field(default_factory=dict)
    # Optional compact navigation/retention record chosen by the producer.
    # MemCore stores it beside, never instead of, the complete model-visible
    # followup_context so the same observation remains available across turns.
    trace_receipt: Mapping[str, Any] | None = None
    # Internal-only provider image blocks. Never copy this field into prompt
    # text, stream events, logs, memcore, or public final output.
    model_image_inputs: list[dict[str, Any]] = field(default_factory=list)


def operation_tool_result(
    *,
    tool_type: str,
    operation_result: Any,
    success_events: list[dict[str, Any]] | None = None,
    state_updates: dict[str, Any] | None = None,
) -> ToolExecutionResult:
    """Map a service operation into one honest model-facing tool result.

    Generated-file and media services return dictionaries with ``ok`` plus a
    human-readable ``followup_context``.  Older handlers copied only the text,
    so ``ok=false`` with no stream event looked successful to the orchestration
    loop.  Keep successful payloads unchanged, but make every explicit failure
    observable and safe to append to the current MemCore turn.
    """

    payload = dict(operation_result) if isinstance(operation_result, Mapping) else {}
    followup = str(payload.get("followup_context") or "").strip()
    explicit_failure = not isinstance(operation_result, Mapping) or payload.get("ok") is False
    if not explicit_failure:
        return ToolExecutionResult(
            tool_type=tool_type,
            stream_events=[
                dict(event)
                for event in list(success_events or [])
                if isinstance(event, Mapping)
            ],
            followup_context=followup,
            state_updates=dict(state_updates or {}),
        )

    raw_reason = str(
        payload.get("error")
        or payload.get("reason")
        or payload.get("status")
        or "operation_failed"
    ).strip()
    reason = re.sub(r"[^A-Za-z0-9_.:-]+", "_", raw_reason)[:120] or "operation_failed"
    failure_event = {
        "type": "tool_execution_failed",
        "tool_type": str(tool_type or "unknown"),
        "status": "failed",
        "reason": reason,
    }
    if not followup:
        followup = f"工具 {tool_type} 没有完成（{reason}）。"
    if "<tool_use_error>" not in followup:
        followup = (
            f"<tool_use_error>{followup} "
            "请基于这个真实失败结果自然告诉用户；不要声称已经完成，也不要虚构产物句柄。"
            "</tool_use_error>"
        )
    return ToolExecutionResult(
        tool_type=tool_type,
        # A failed service response may still contain partial/stale artifact
        # fields. Never project success events from those fields alongside the
        # failure terminal state.
        stream_events=[failure_event],
        followup_context=followup,
        state_updates={
            **dict(state_updates or {}),
            "operation_failure": {
                "tool_type": str(tool_type or "unknown"),
                "reason": reason,
            },
        },
    )


@dataclass(frozen=True)
class ToolMetadata:
    family: str = "general"
    operation: str = "mixed"
    risk: str = "medium"
    default_round_budget: int = 3
    background: bool = False
    aliases: tuple[str, ...] = ()
    input_schema: Mapping[str, Any] | None = None
    requires_confirmation: bool = False

    @property
    def is_read_only(self) -> bool:
        return str(self.operation or "").strip().lower() == "read"


LIST_REMINDERS_INPUT_SCHEMA: dict[str, Any] = {
    "description": ("List the user's reminders. Use it when the user asks what reminders they currently have."),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {
            "type": "string",
            "enum": ["pending", "done", "all"],
            "description": "Which reminders to list. Default pending.",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 10,
            "description": "Maximum reminders to return. Default 5.",
        },
    },
    "required": [],
}


CHECK_INVENTORY_INPUT_SCHEMA: dict[str, Any] = {
    "description": ("Check gift inventory. Use it when the user asks about gifts on hand or in the gift box."),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scope": {
            "type": "string",
            "enum": ["pending_recent", "pending_all", "kept", "internalized"],
            "description": "Inventory scope. Prefer pending_recent for what's on hand.",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "description": "Maximum items. Default 3 for pending_recent, else 5.",
        },
    },
    "required": [],
}


INSPECT_MEDIA_INFO_INPUT_SCHEMA: dict[str, Any] = {
    "description": (
        "Read media specs (duration, codec, sample rate, channels, bitrate, "
        "resolution, fps) of an existing file/audio/generated item. Read-only; "
        "does not create files."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "source_id": {
            "type": "string",
            "maxLength": 120,
            "description": "Handle of an existing media item, e.g. file_001 / audio_001 / gen_001.",
        },
    },
    "required": ["source_id"],
}


LOAD_CHARACTER_CONTEXT_INPUT_SCHEMA: dict[str, Any] = {
    "description": (
        "Load specific entries from the active character pack's context libraries "
        "by their target names. The available target names are listed in the prompt. "
        "Read-only; does not modify anything."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "targets": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 20,
            "description": "Target names to load, taken from the character pack's listed libraries/entries.",
        },
    },
    "required": ["targets"],
}


INSPECT_ATTACHMENT_INPUT_SCHEMA: dict[str, Any] = {
    "description": (
        "List the current attachment workspace, or open and inspect a single image or file "
        "(temporary context, not gifts/character resources/long-term memory). "
        "To compare multiple materials, prefer sync_attachment_workspace."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "target": {
            "type": "string",
            "maxLength": 120,
            "description": "'all' to list the workspace, or attachment id / title / filename / 'latest'. Defaults to latest.",
        },
        "kind": {
            "type": "string",
            "enum": ["any", "image", "file", "document", "audio"],
            "description": "Optional kind filter. Default any.",
        },
    },
    "required": [],
}


LOAD_MATERIAL_INPUT_SCHEMA: dict[str, Any] = {
    "description": (
        "Reload one to five original images from the current session's temporary "
        "attachment/generated-file workspace into the next multimodal model round. "
        "Use when an older image must be examined again; not needed when the current "
        "turn already includes the image or when only its saved summary is enough."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "targets": {
            "type": "array",
            "items": {"type": "string", "maxLength": 120},
            "minItems": 1,
            "maxItems": 5,
            "description": "Current-session image handles such as img_001 or gen_002.",
        },
        "purpose": {
            "type": "string",
            "maxLength": 240,
            "description": "Short reason the original pixels are needed, e.g. compare details before editing.",
        },
    },
    "required": ["targets"],
}


GENERATE_IMAGE_INPUT_SCHEMA: dict[str, Any] = {
    "description": (
        "Generate a new image or edit one to five current-session reference images. "
        "The host resolves img_*/gen_* handles and calls the configured PinAI GPT Image provider. "
        "Use only for an explicit image-generation/editing intent; never invent paths, URLs, or base64."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "prompt": {
            "type": "string",
            "minLength": 1,
            "maxLength": 4000,
            "description": "Complete creative/edit instruction preserving the user's requested constraints.",
        },
        "reference_images": {
            "type": "array",
            "items": {"type": "string", "maxLength": 120},
            "maxItems": 5,
            "description": "Optional current-session img_*/gen_* handles. Omit for text-to-image.",
        },
        "mask_image": {
            "type": "string",
            "maxLength": 120,
            "description": "Optional current-session mask image handle. Requires at least one reference image.",
        },
        "size": {
            "type": "string",
            "pattern": "^(auto|[0-9]{3,4}x[0-9]{3,4})$",
            "description": "auto or WIDTHxHEIGHT; bounded by the host. Common: 1024x1024, 1536x1024, 1024x1536.",
        },
        "quality": {"type": "string", "enum": ["auto", "low", "medium", "high"]},
        "background": {
            "type": "string",
            "enum": ["auto", "opaque"],
            "description": "gpt-image-2 does not support transparent backgrounds.",
        },
        "output_format": {"type": "string", "enum": ["png", "jpeg", "webp"]},
        "compression": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
            "description": "JPEG/WebP output compression quality. Ignored for PNG.",
        },
        "input_fidelity": {
            "type": "string",
            "enum": ["auto", "low", "high"],
            "description": "How strongly edits should preserve input details.",
        },
        "n": {"type": "integer", "minimum": 1, "maximum": 4},
        "output_title": {"type": "string", "maxLength": 80},
        "send_to_user": {
            "type": "boolean",
            "description": "Default true: deliver generated images through the current client.",
        },
    },
    "required": ["prompt"],
}


READ_ATTACHMENT_SECTION_INPUT_SCHEMA: dict[str, Any] = {
    "description": (
        "Expand a specific page / line range / table / sheet of a long attachment in "
        "the workspace. Only reveals already-parsed text; the system reports when a "
        "file has no text layer. Not for image gifts or long-term memory."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "target": {
            "type": "string",
            "maxLength": 120,
            "description": "file id / title / filename / 'latest'.",
        },
        "section": {
            "type": "string",
            "maxLength": 120,
            "description": "e.g. '第2页' / '第10-30行' / '第1个表' / 'Sheet1'.",
        },
        "kind": {
            "type": "string",
            "enum": ["any", "file", "document"],
            "description": "Optional. Default document.",
        },
    },
    "required": [],
}


SYNC_ATTACHMENT_WORKSPACE_INPUT_SCHEMA: dict[str, Any] = {
    "description": (
        "Reorganize the attachment workspace in one shot: keep the final set of "
        "materials to focus on (multiple images/files may be kept for comparison) and "
        "collapse the rest. Submit the final list once; do not toggle items one by one."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "focus_targets": {
            "type": "array",
            "items": {"type": "string", "maxLength": 120},
            "maxItems": 30,
            "description": "Final workspace list after reorg: ids / '第2张图' / descriptive names.",
        },
        "kind": {
            "type": "string",
            "enum": ["any", "image", "file", "document", "audio"],
            "description": "Optional kind filter. Default any.",
        },
        "reason": {
            "type": "string",
            "maxLength": 160,
            "description": "Why these materials are needed.",
        },
    },
    "required": [],
}


LIST_WORKSPACE_INPUT_SCHEMA: dict[str, Any] = {
    "description": (
        "List one or more directories in Akane's accessible workspace folder. Use it "
        "first to see what materials exist. Only workspace:/ relative paths — never "
        "local absolute paths."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "paths": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 50,
            "description": "workspace:/ relative directories, e.g. ['workspace:/Inbox']. Omit to list the workspace root.",
        },
        "depth": {
            "type": "integer",
            "minimum": 0,
            "maximum": 8,
            "description": "1 lists direct children; larger expands subdirectories. Default 1.",
        },
        "max_entries": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50000,
            "description": "Maximum entries to return. Default 10000.",
        },
    },
    "required": [],
}


READ_WORKSPACE_INPUT_SCHEMA: dict[str, Any] = {
    "description": (
        "Read one or more files from the workspace by their workspace:/ relative paths "
        "(from list_workspace). Supports text/Word/Excel/PDF and ZIP listings; binary "
        "media returns a status pointing to a dedicated tool. Never guess local absolute paths."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "targets": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 200,
            "description": "workspace:/ relative file paths from list_workspace.",
        },
        "max_chars": {
            "type": "integer",
            "minimum": 1000,
            "maximum": 4000000,
            "description": "Maximum characters to read. Default 1000000.",
        },
    },
    "required": ["targets"],
}


INSPECT_GENERATED_FILE_INPUT_SCHEMA: dict[str, Any] = {
    "description": (
        "Re-read a file you generated (e.g. gen_001): its body, head/tail, zip file "
        "list, or manifest. Read-only — does not send, modify, or delete. To resend a "
        "file use send_file; to edit it use revise_generated_file."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "target": {
            "type": "string",
            "maxLength": 120,
            "description": "Generated id / 'latest' / file title. Default latest.",
        },
        "section": {
            "type": "string",
            "maxLength": 260,
            "description": "content | head | tail | summary | file_list | manifest | file:<name>. Default content.",
        },
        "max_chars": {
            "type": "integer",
            "minimum": 500,
            "maximum": 40000,
            "description": "Maximum characters. Default 12000.",
        },
    },
    "required": [],
}


COVER_SONG_INPUT_SCHEMA: dict[str, Any] = {
    "description": (
        "Create or restore a cached AI cover from a current-session audio/video/generated material. "
        "The backend separates vocals and instrumental, converts the lead vocal with a local voice model, "
        "mixes the result, stores it as a generated artifact, and can deliver it as QQ voice or file."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "source_id": {
            "type": "string",
            "description": "Optional current-session audio/video/generated handle such as audio_001, file_001, or gen_001.",
            "maxLength": 120,
        },
        "song_title": {
            "type": "string",
            "description": "Song title. Required when restoring a previously cached cover without source_id.",
            "maxLength": 120,
        },
        "artist": {
            "type": "string",
            "description": "Optional original artist for cache disambiguation.",
            "maxLength": 80,
        },
        "voice_model": {
            "type": "string",
            "description": "Target local RVC model name, or auto for the configured default.",
            "maxLength": 120,
        },
        "pitch_shift": {"type": "integer", "minimum": -24, "maximum": 24},
        "index_rate": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "filter_radius": {"type": "integer", "minimum": 0, "maximum": 7},
        "rms_mix_rate": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "protect": {"type": "number", "minimum": 0.0, "maximum": 0.5},
        "vocal_gain_db": {"type": "number", "minimum": -12.0, "maximum": 12.0},
        "instrumental_gain_db": {"type": "number", "minimum": -12.0, "maximum": 6.0},
        "output_format": {"type": "string", "enum": ["mp3", "flac", "wav"]},
        "delivery": {"type": "string", "enum": ["auto", "voice", "file", "both", "none"]},
        "force_rebuild": {"type": "boolean"},
    },
    "required": [],
}


TOOL_METADATA_BY_TYPE: dict[str, ToolMetadata] = {
    "retrieve_memory": ToolMetadata(
        family="memory",
        operation="read",
        risk="low",
        default_round_budget=3,
        input_schema=RETRIEVE_MEMORY_TOOL_SPEC.input_schema,
    ),
    "read_memory_timeline": ToolMetadata(
        family="memory",
        operation="read",
        risk="low",
        default_round_budget=3,
        input_schema=READ_MEMORY_TIMELINE_TOOL_SPEC.input_schema,
    ),
    "browse_memory": ToolMetadata(
        family="memory",
        operation="read",
        risk="low",
        default_round_budget=3,
        input_schema=BROWSE_MEMORY_TOOL_SPEC.input_schema,
    ),
    "open_memory": ToolMetadata(
        family="memory",
        operation="read",
        risk="low",
        default_round_budget=3,
        input_schema=OPEN_MEMORY_TOOL_SPEC.input_schema,
    ),
    "load_character_context": ToolMetadata(
        family="character_context",
        operation="read",
        risk="low",
        default_round_budget=3,
        input_schema=LOAD_CHARACTER_CONTEXT_INPUT_SCHEMA,
    ),
    "set_reminder": ToolMetadata(family="reminder", operation="control", risk="low", default_round_budget=3),
    "list_reminders": ToolMetadata(
        family="reminder",
        operation="read",
        risk="low",
        default_round_budget=3,
        input_schema=LIST_REMINDERS_INPUT_SCHEMA,
    ),
    "cancel_reminder": ToolMetadata(family="reminder", operation="control", risk="low", default_round_budget=3),
    "call_npc": ToolMetadata(family="web_scene", operation="mixed", risk="low", default_round_budget=3),
    "check_inventory": ToolMetadata(
        family="web_scene",
        operation="read",
        risk="low",
        default_round_budget=3,
        input_schema=CHECK_INVENTORY_INPUT_SCHEMA,
    ),
    "manage_gift": ToolMetadata(family="web_scene", operation="control", risk="low", default_round_budget=3),
    "manage_artifact": ToolMetadata(family="web_scene", operation="control", risk="low", default_round_budget=3),
    "manage_persona": ToolMetadata(family="persona", operation="control", risk="medium", default_round_budget=3),
    "manage_task_workspace": ToolMetadata(
        family="task_workspace", operation="control", risk="medium", default_round_budget=3
    ),
    "delegate_task": ToolMetadata(
        family="background_task", operation="background", risk="medium", default_round_budget=3, background=True
    ),
    "web_search": ToolMetadata(family="web_research", operation="read", risk="low", default_round_budget=8),
    "browser_page": ToolMetadata(
        family="browser_control", operation="mixed", risk="medium", default_round_budget=10, requires_confirmation=True
    ),
    "open_music_search": ToolMetadata(
        family="music_request", operation="control", risk="medium", default_round_budget=4, requires_confirmation=True
    ),
    "fetch_media_from_url": ToolMetadata(
        family="media_fetch", operation="control", risk="medium", default_round_budget=4, requires_confirmation=True
    ),
    "sync_attachment_workspace": ToolMetadata(
        family="file_workspace",
        operation="read",
        risk="low",
        default_round_budget=3,
        input_schema=SYNC_ATTACHMENT_WORKSPACE_INPUT_SCHEMA,
    ),
    "inspect_attachment": ToolMetadata(
        family="file_workspace",
        operation="read",
        risk="low",
        default_round_budget=3,
        input_schema=INSPECT_ATTACHMENT_INPUT_SCHEMA,
    ),
    "load_material": ToolMetadata(
        family="file_workspace",
        operation="read",
        risk="low",
        default_round_budget=4,
        input_schema=LOAD_MATERIAL_INPUT_SCHEMA,
    ),
    "generate_image": ToolMetadata(
        family="image_generation",
        operation="external",
        risk="medium",
        default_round_budget=5,
        background=True,
        input_schema=GENERATE_IMAGE_INPUT_SCHEMA,
    ),
    "retry_attachment": ToolMetadata(family="file_workspace", operation="control", risk="low", default_round_budget=3),
    "clear_attachment_focus": ToolMetadata(
        family="file_workspace", operation="control", risk="low", default_round_budget=3
    ),
    "read_attachment_section": ToolMetadata(
        family="file_workspace",
        operation="read",
        risk="low",
        default_round_budget=3,
        input_schema=READ_ATTACHMENT_SECTION_INPUT_SCHEMA,
    ),
    "list_workspace": ToolMetadata(
        family="file_workspace",
        operation="read",
        risk="low",
        default_round_budget=4,
        input_schema=LIST_WORKSPACE_INPUT_SCHEMA,
    ),
    "read_workspace": ToolMetadata(
        family="file_workspace",
        operation="read",
        risk="low",
        default_round_budget=4,
        input_schema=READ_WORKSPACE_INPUT_SCHEMA,
    ),
    "focus_workspace": ToolMetadata(family="file_workspace", operation="control", risk="low", default_round_budget=4),
    "register_workspace_items": ToolMetadata(
        family="file_workspace", operation="control", risk="low", default_round_budget=4
    ),
    "compose_file": ToolMetadata(
        family="file_workspace", operation="control", risk="medium", default_round_budget=4, requires_confirmation=True
    ),
    "revise_generated_file": ToolMetadata(
        family="file_workspace", operation="control", risk="medium", default_round_budget=4, requires_confirmation=True
    ),
    "apply_style_to_existing_file": ToolMetadata(
        family="file_workspace", operation="control", risk="medium", default_round_budget=4, requires_confirmation=True
    ),
    "inspect_generated_file": ToolMetadata(
        family="file_workspace",
        operation="read",
        risk="low",
        default_round_budget=3,
        input_schema=INSPECT_GENERATED_FILE_INPUT_SCHEMA,
    ),
    "manage_generated_file": ToolMetadata(
        family="file_workspace", operation="control", risk="medium", default_round_budget=3, requires_confirmation=True
    ),
    "send_file": ToolMetadata(
        family="file_handoff", operation="control", risk="medium", default_round_budget=3, requires_confirmation=True
    ),
    "send_audio": ToolMetadata(
        family="qq_audio_delivery", operation="control", risk="low", default_round_budget=3
    ),
    "send_sticker": ToolMetadata(family="social_delivery", operation="control", risk="low", default_round_budget=3),
    "send_music_card": ToolMetadata(
        family="qq_music_delivery", operation="control", risk="low", default_round_budget=3
    ),
    "inspect_media_info": ToolMetadata(
        family="media_workbench",
        operation="read",
        risk="low",
        default_round_budget=3,
        input_schema=INSPECT_MEDIA_INFO_INPUT_SCHEMA,
    ),
    "separate_audio_stems": ToolMetadata(
        family="media_workbench",
        operation="background",
        risk="medium",
        default_round_budget=4,
        background=True,
        requires_confirmation=True,
    ),
    "cover_song": ToolMetadata(
        family="media_workbench",
        operation="background",
        risk="medium",
        default_round_budget=5,
        background=True,
        input_schema=COVER_SONG_INPUT_SCHEMA,
    ),
    "clean_voice_track": ToolMetadata(
        family="media_workbench",
        operation="background",
        risk="medium",
        default_round_budget=4,
        background=True,
        requires_confirmation=True,
    ),
    "transcribe_media": ToolMetadata(
        family="media_workbench",
        operation="background",
        risk="medium",
        default_round_budget=4,
        background=True,
        requires_confirmation=True,
    ),
    "prepare_voice_dataset": ToolMetadata(
        family="media_workbench",
        operation="background",
        risk="medium",
        default_round_budget=4,
        background=True,
        requires_confirmation=True,
    ),
    "convert_media_file": ToolMetadata(
        family="media_workbench",
        operation="background",
        risk="medium",
        default_round_budget=4,
        background=True,
        requires_confirmation=True,
    ),
    "exec_run": ToolMetadata(
        family="execution",
        operation="control",
        default_round_budget=4,
        background=True,
    ),
    "exec_status": ToolMetadata(
        family="execution",
        operation="read",
        default_round_budget=4,
    ),
    "exec_cancel": ToolMetadata(
        family="execution",
        operation="control",
        default_round_budget=3,
    ),
    "load_skill": ToolMetadata(
        family="skill",
        operation="read",
        default_round_budget=4,
    ),
    "manage_skill": ToolMetadata(
        family="skill",
        operation="control",
        default_round_budget=4,
    ),
}


# M66-C: Single canonical ToolSpec lookup for all built-in tools.
# BaseToolHandler.tool_spec() uses this by default. Handlers that define their
# own explicit tool_spec() override this lookup — identical result, more explicit.
TOOL_SPEC_BY_TYPE: dict[str, Any] = {
    "retrieve_memory": RETRIEVE_MEMORY_TOOL_SPEC,
    "read_memory_timeline": READ_MEMORY_TIMELINE_TOOL_SPEC,
    "browse_memory": BROWSE_MEMORY_TOOL_SPEC,
    "open_memory": OPEN_MEMORY_TOOL_SPEC,
    "load_character_context": LOAD_CHARACTER_CONTEXT_TOOL_SPEC,
    "set_reminder": SET_REMINDER_TOOL_SPEC,
    "list_reminders": LIST_REMINDERS_TOOL_SPEC,
    "cancel_reminder": CANCEL_REMINDER_TOOL_SPEC,
    "call_npc": CALL_NPC_TOOL_SPEC,
    "check_inventory": CHECK_INVENTORY_TOOL_SPEC,
    "manage_gift": MANAGE_GIFT_TOOL_SPEC,
    "manage_artifact": MANAGE_ARTIFACT_TOOL_SPEC,
    "manage_persona": MANAGE_PERSONA_TOOL_SPEC,
    "manage_task_workspace": MANAGE_TASK_WORKSPACE_TOOL_SPEC,
    "delegate_task": DELEGATE_TASK_TOOL_SPEC,
    "web_search": WEB_SEARCH_TOOL_SPEC,
    "browser_page": BROWSER_PAGE_TOOL_SPEC,
    "open_music_search": OPEN_MUSIC_SEARCH_TOOL_SPEC,
    "fetch_media_from_url": FETCH_MEDIA_FROM_URL_TOOL_SPEC,
    "sync_attachment_workspace": SYNC_ATTACHMENT_WORKSPACE_TOOL_SPEC,
    "inspect_attachment": INSPECT_ATTACHMENT_TOOL_SPEC,
    "load_material": LOAD_MATERIAL_TOOL_SPEC,
    "generate_image": GENERATE_IMAGE_TOOL_SPEC,
    "retry_attachment": RETRY_ATTACHMENT_TOOL_SPEC,
    "clear_attachment_focus": CLEAR_ATTACHMENT_FOCUS_TOOL_SPEC,
    "read_attachment_section": READ_ATTACHMENT_SECTION_TOOL_SPEC,
    "list_workspace": LIST_WORKSPACE_TOOL_SPEC,
    "read_workspace": READ_WORKSPACE_TOOL_SPEC,
    "focus_workspace": FOCUS_WORKSPACE_TOOL_SPEC,
    "register_workspace_items": REGISTER_WORKSPACE_ITEMS_TOOL_SPEC,
    "compose_file": COMPOSE_FILE_TOOL_SPEC,
    "revise_generated_file": REVISE_GENERATED_FILE_TOOL_SPEC,
    "apply_style_to_existing_file": APPLY_STYLE_TO_EXISTING_FILE_TOOL_SPEC,
    "inspect_generated_file": INSPECT_GENERATED_FILE_TOOL_SPEC,
    "manage_generated_file": MANAGE_GENERATED_FILE_TOOL_SPEC,
    "send_file": SEND_FILE_TOOL_SPEC,
    "send_audio": SEND_AUDIO_TOOL_SPEC,
    "send_sticker": SEND_STICKER_TOOL_SPEC,
    "send_music_card": SEND_MUSIC_CARD_TOOL_SPEC,
    "inspect_media_info": INSPECT_MEDIA_INFO_TOOL_SPEC,
    "separate_audio_stems": SEPARATE_AUDIO_STEMS_TOOL_SPEC,
    "clean_voice_track": CLEAN_VOICE_TRACK_TOOL_SPEC,
    "transcribe_media": TRANSCRIBE_MEDIA_TOOL_SPEC,
    "prepare_voice_dataset": PREPARE_VOICE_DATASET_TOOL_SPEC,
    "convert_media_file": CONVERT_MEDIA_FILE_TOOL_SPEC,
    "cover_song": COVER_SONG_TOOL_SPEC,
    "exec_run": EXEC_RUN_TOOL_SPEC,
    "exec_status": EXEC_STATUS_TOOL_SPEC,
    "exec_cancel": EXEC_CANCEL_TOOL_SPEC,
    "load_skill": LOAD_SKILL_TOOL_SPEC,
    "manage_skill": MANAGE_SKILL_TOOL_SPEC,
}


def _project_tool_metadata_from_spec(tool_type: str, spec: Any) -> ToolMetadata:
    legacy = TOOL_METADATA_BY_TYPE.get(tool_type) or ToolMetadata()
    return ToolMetadata(
        family=legacy.family,
        operation=legacy.operation,
        risk=str(getattr(spec, "risk", "") or legacy.risk),
        default_round_budget=legacy.default_round_budget,
        background=legacy.background,
        aliases=legacy.aliases,
        input_schema=getattr(spec, "input_schema", None),
        requires_confirmation=str(getattr(spec, "confirm", "never") or "never") != "never",
    )


# Compatibility projection only: capcore ToolSpec is the semantic/schema authority.
TOOL_METADATA_BY_TYPE = {
    tool_type: _project_tool_metadata_from_spec(tool_type, spec)
    for tool_type, spec in TOOL_SPEC_BY_TYPE.items()
}


class BaseToolHandler:
    tool_type: str = ""

    def tool_spec(self):
        """M66-C: Return the canonical ToolSpec for this tool. Handlers with an
        explicit override take precedence; all others resolve via TOOL_SPEC_BY_TYPE."""
        return TOOL_SPEC_BY_TYPE.get(str(self.tool_type or "").strip())

    def tool_metadata(self) -> ToolMetadata:
        metadata = TOOL_METADATA_BY_TYPE.get(str(self.tool_type or "").strip())
        if metadata is not None:
            return metadata
        return ToolMetadata()

    def build_prompt_instruction(self) -> str:
        raise NotImplementedError

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        raise NotImplementedError

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        raise NotImplementedError
