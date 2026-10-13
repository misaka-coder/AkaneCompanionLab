"""Shared capability route constants and callback types."""
from __future__ import annotations
import re
from typing import Any, Callable


LogEvent = Callable[..., None]
ProviderHealthChecker = Callable[[str, int, float], tuple[bool, str]]
ProviderTtsTestRunner = Callable[..., Any]
McpToolDiscoverer = Callable[..., Any]
WORKFLOW_JOB_ID_RE = re.compile(r"^job_[a-f0-9]{32}$")
GPT_SOVITS_PROVIDER_ID = "provider.tts.gpt_sovits.local"
PROVIDER_TTS_TEST_DEFAULT_TEXT = "你好，主人，本地语音服务已经接通。"
PROVIDER_TTS_TEST_TEXT_MAX_CHARS = 120
PROVIDER_TTS_TEST_AUDIO_MAX_BYTES = 2 * 1024 * 1024
