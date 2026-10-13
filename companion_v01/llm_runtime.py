"""Compatibility entry and instance state for the model runtime.

Implementation boundaries live in model_runtime; each runtime keeps its own
settings, model bundles, metric locks and error state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import logging
import re
import threading

from .model_request_control import ModelRequestCancelled
from .model_runtime.api import ModelCallsMixin
from .model_runtime.json_calls import JSONCallsMixin
from .model_runtime.json_parsing import (
    JSONParsingMixin,
    JSON_RE,
    _memory_metadata_has_signal,
    _memory_metadata_truth,
)
from .model_runtime.json_stream import (
    JSON_ESCAPE_MAP,
    _TopLevelJSONStreamTap,
    _completed_tool_preface_text,
    _speech_segment_has_terminal_boundary,
)
from .model_runtime.message_payloads import MessagePayloadsMixin
from .model_runtime.metrics import RuntimeMetricsMixin, SECRET_PATTERNS
from .model_runtime.models import (
    ChatJSONResult,
    ChatJSONStreamResult,
    ChatTextResult,
    ModelBundle,
    ModelExecutionTarget,
    NDJSONCallResult,
    ProviderToolProfile,
    _CompletionPayload,
)
from .model_runtime.prompt_audit import PROMPT_AUDIT_LOCK, PromptAuditMixin
from .model_runtime.provider_execution import ProviderExecutionMixin
from .model_runtime.provider_tools import (
    CONFIG_ALLOWLISTED_PROVIDER_TOOL_PROFILE,
    DEFAULT_PROVIDER_TOOL_PROFILE,
    OPENAI_COMPAT_DEFAULT_PROVIDER_TOOL_PROFILE,
    PROVIDER_TOOL_PROFILES,
    ProviderToolsMixin,
)
from .model_runtime.request_policy import RequestPolicyMixin
from .model_runtime.responses import (
    ResponsesProtocolMixin,
    _ResponsesStreamAdapter,
    _responses_error_summary,
)
from .model_runtime.stream_calls import StreamCallsMixin
from .model_runtime.targets import (
    DEFAULT_MEMCORE_SUMMARY_MODEL_NAME,
    ModelTargetsMixin,
    _safe_chat_model_override,
)
from .runtime_settings import BotSettingsView
from services.llm_client import build_llm_client
import config


logger = logging.getLogger("akane.llm_runtime")


class LLMRuntime(
    ResponsesProtocolMixin,
    JSONParsingMixin,
    ModelTargetsMixin,
    MessagePayloadsMixin,
    ProviderToolsMixin,
    RequestPolicyMixin,
    ModelCallsMixin,
    JSONCallsMixin,
    StreamCallsMixin,
    ProviderExecutionMixin,
    PromptAuditMixin,
    RuntimeMetricsMixin,
):
    supports_request_observer = True

    @staticmethod
    def _build_llm_client(**kwargs: Any) -> Any:
        """Retain the entry client-factory override used by integrations."""
        return build_llm_client(**kwargs)

    def __init__(
        self,
        *,
        log_dir: Path | str | None = None,
        instance_id: str = "",
        settings: BotSettingsView | None = None,
        config_module: Any = config,
    ):
        self._config_module = config_module
        self.settings = settings or BotSettingsView.from_config(config_module)
        self.log_dir = Path(log_dir) if log_dir is not None else Path(str(getattr(config_module, "LOG_DIR", "") or "logs"))
        safe_instance_id = str(instance_id or "local-default").strip()
        self.instance_id = (
            safe_instance_id if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", safe_instance_id) else "unknown"
        )
        self._bundle_lock = threading.RLock()
        self.aux = self._build_aux_bundle()
        self.chat = self._build_chat_bundle()
        self.memcore_summary = self._build_memcore_summary_bundle()
        self.vision = self._build_vision_bundle()
        self._metrics_lock = threading.RLock()
        self._metrics = {
            "aux_json_calls": 0,
            "chat_json_calls": 0,
            "aux_ndjson_calls": 0,
            "chat_stream_calls": 0,
            "errors": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_usage_calls": 0,
            "cache_hit_calls": 0,
            "reported_input_tokens": 0,
            "reported_output_tokens": 0,
            "final_cache_read_tokens": 0,
            "final_cache_creation_tokens": 0,
            "final_reported_input_tokens": 0,
            "final_reported_output_tokens": 0,
            "final_cache_usage_calls": 0,
            "final_cache_hit_calls": 0,
            "chat_json_fallbacks": 0,
            "native_tool_decision_sent": 0,
            "native_tool_provider_unsupported": 0,
            "native_tool_call_extracted": 0,
            "native_tool_calls_extra": 0,
            "native_tool_calls_truncated": 0,
            "native_tool_no_call": 0,
            "prompt_cache_retention_compat": 0,
        }
        self._last_error_lock = threading.RLock()
        self._last_error: dict[str, str] = {}
