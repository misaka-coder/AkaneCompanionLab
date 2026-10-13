"""Compatibility facade for final-response requests, policy, and transports."""

from .final_response_constants import (
    FINAL_RESPONSE_TEMPERATURE as FINAL_RESPONSE_TEMPERATURE,
    FINAL_RESPONSE_RECOVERY_FEEDBACK as FINAL_RESPONSE_RECOVERY_FEEDBACK,
    FINAL_RESPONSE_EXTERNAL_TEXT_FEEDBACK as FINAL_RESPONSE_EXTERNAL_TEXT_FEEDBACK,
    FINAL_TOOL_PROTOCOL_RECOVERY_FEEDBACK as FINAL_TOOL_PROTOCOL_RECOVERY_FEEDBACK,
    FINAL_RESPONSE_PLAIN_TEXT_FEEDBACK as FINAL_RESPONSE_PLAIN_TEXT_FEEDBACK,
    FINAL_PROMPT_CACHE_LAYOUT_VERSION as FINAL_PROMPT_CACHE_LAYOUT_VERSION,
)
from .final_response_sync import FinalResponseSyncMixin
from .final_response_stream import FinalResponseStreamMixin
from .final_response_events import FinalResponseEventMixin
from .final_response_fallback import FinalResponseFallbackMixin
from .final_response_request import FinalResponseRequestMixin
from .final_response_policy import FinalResponsePolicyMixin
from .final_response_recovery import FinalResponseRecoveryMixin
from .final_response_failures import FinalResponseFailureMixin


class FinalResponseStreamerMixin(
    FinalResponseSyncMixin,
    FinalResponseStreamMixin,
    FinalResponseEventMixin,
    FinalResponseFallbackMixin,
    FinalResponseRequestMixin,
    FinalResponsePolicyMixin,
    FinalResponseRecoveryMixin,
    FinalResponseFailureMixin,
):
    """Static service composition retaining existing engine methods and constants."""
