"""Stable playback delivery entry; implementations live in playback_runtime."""

from __future__ import annotations
import asyncio as asyncio
import hashlib as hashlib
import threading as threading
from collections import deque as deque
from collections.abc import Mapping as Mapping
from dataclasses import dataclass as dataclass, replace as replace
from datetime import datetime as datetime, timezone as timezone
from typing import Any as Any, Callable as Callable
from voicecore import VoiceEvent as VoiceEvent
from .host import VoiceCommandExecutionResult as VoiceCommandExecutionResult
from .tts_executor import (
    VoiceAudioArtifactPort as VoiceAudioArtifactPort,
    VoiceReadableTextArtifactPort as VoiceReadableTextArtifactPort,
)
from .playback_runtime.channel import VoicePlaybackDeliveryChannel as VoicePlaybackDeliveryChannel
from .playback_runtime.executor import AkaneVoicePlaybackCommandExecutor as AkaneVoicePlaybackCommandExecutor
from .playback_runtime.contracts import VOICE_PLAYBACK_OUTPUT_MODE as VOICE_PLAYBACK_OUTPUT_MODE
from .playback_runtime.contracts import VoicePlaybackDeliveryRequest as VoicePlaybackDeliveryRequest
from .playback_runtime.contracts import VoicePlaybackControlRequest as VoicePlaybackControlRequest
from .playback_runtime.contracts import VoicePlaybackDeliveryResult as VoicePlaybackDeliveryResult
from .playback_runtime.contracts import _DeliveryRecord as _DeliveryRecord
from .playback_runtime.contracts import _ControlRecord as _ControlRecord
from .playback_runtime.contracts import _safe_optional_text as _safe_optional_text
from .playback_runtime.contracts import _same_delivery_request as _same_delivery_request
from .playback_runtime.contracts import _safe_reason as _safe_reason
