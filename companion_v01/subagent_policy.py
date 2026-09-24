"""Task-worker composition over an already authorized parent selection."""

from .engine_services.tool_rounds import restrict_capability_selection
from .tool_handlers.core import TOOL_METADATA_BY_TYPE


# These belong to the parent conversation, not to an independent deliverable.
# New work tools inherit by default; this is not a second tool registry.
_PARENT_FAMILIES = frozenset({
    "memory", "character_context", "music_request", "social_delivery",
    "file_handoff", "qq_audio_delivery", "qq_music_delivery",
})
_PARENT_TOOLS = frozenset({
    "spawn_subagent", "onebot_action", "open_browser",
    "manage_project_workspace",  # Mutates the parent's selected project.
    "clear_attachment_focus",  # Mutates the parent's current attachment focus.
})


def task_capability_selection(selection):
    """Shrink discovery and dispatch together, without changing parent grants."""
    allowed = []
    candidates = dict.fromkeys((*selection.tool_names, *sorted(selection.execution_allowlist or ())))
    for name in candidates:
        if name in _PARENT_TOOLS:
            continue
        handler = selection.resolved_handlers.get(name)
        getter = getattr(handler, "tool_metadata", None)
        metadata = getter() if callable(getter) else TOOL_METADATA_BY_TYPE.get(name)
        if getattr(metadata, "family", "") not in _PARENT_FAMILIES:
            allowed.append(name)
    return restrict_capability_selection(selection, allowed_tool_names=allowed)
