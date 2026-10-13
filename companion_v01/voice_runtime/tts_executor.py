"""Stable voice TTS imports; implementation lives in tts_runtime."""
from __future__ import annotations
import asyncio as asyncio
import hashlib as hashlib
import inspect as inspect
import logging as logging
import threading as threading
import uuid as uuid
from collections.abc import Mapping as Mapping
from dataclasses import dataclass as dataclass
from datetime import datetime as datetime, timezone as timezone
from typing import Any as Any, Callable as Callable, Protocol as Protocol
from voicecore import VoiceEvent as VoiceEvent
from ..background_tasks import BackgroundTaskRunner as BackgroundTaskRunner
from ..tts_service import TTSServiceError as TTSServiceError
from .durable_ports import VoiceAudioArtifactReadResult as VoiceAudioArtifactReadResult, VoiceAudioArtifactResult as VoiceAudioArtifactResult, VoiceTextArtifactReadResult as VoiceTextArtifactReadResult
from .host import VoiceCommandExecutionResult as VoiceCommandExecutionResult, VoiceCommandExecutor as VoiceCommandExecutor

from .tts_runtime.commands import _TTSJob as _TTSJob
from .tts_runtime.commands import VoiceReadableTextArtifactPort as VoiceReadableTextArtifactPort
from .tts_runtime.commands import VoiceAudioArtifactPort as VoiceAudioArtifactPort
from .tts_runtime.commands import VoiceCommandRouterExecutor as VoiceCommandRouterExecutor
from .tts_runtime.commands import logger as logger
from .tts_runtime.executor import AkaneVoiceTTSCommandExecutor as AkaneVoiceTTSCommandExecutor
from .tts_runtime.synthesis import _coerce_synthesized_audio as _coerce_synthesized_audio
from .tts_runtime.synthesis import _resolve_awaitable as _resolve_awaitable
