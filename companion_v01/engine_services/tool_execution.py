"""Compatibility composition for the engine tool execution methods."""
from __future__ import annotations

from .tool_trace import ToolTraceMixin
from .tool_history_projection import ToolHistoryProjectionMixin
from .tool_protocol import ToolProtocolMixin
from .tool_batch_orchestration import ToolBatchOrchestrationMixin
from .tool_capabilities import ToolCapabilitiesMixin
from .tool_prompt_context import ToolPromptContextMixin
from .tool_hooks import ToolHooksMixin
from .tool_prompt_context import MEDIA_PRESET_ROUTING as MEDIA_PRESET_ROUTING


class ToolExecutionMixin(
    ToolTraceMixin,
    ToolHistoryProjectionMixin,
    ToolProtocolMixin,
    ToolBatchOrchestrationMixin,
    ToolCapabilitiesMixin,
    ToolPromptContextMixin,
    ToolHooksMixin,
):
    """Expose the existing engine API through static, single-owner mixins."""
