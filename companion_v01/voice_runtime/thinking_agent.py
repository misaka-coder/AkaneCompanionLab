"""Stable thinking agent imports; implementation lives in thinking_runtime."""
from __future__ import annotations

import hashlib as hashlib
import threading as threading
import uuid as uuid
from collections.abc import Mapping as Mapping
from dataclasses import dataclass as dataclass
from datetime import datetime as datetime
from datetime import timezone as timezone
from typing import Any as Any
from typing import Callable as Callable
from voicecore import VoiceEvent as VoiceEvent
from ..background_tasks import BackgroundTaskRunner as BackgroundTaskRunner
from .host import AkaneVoiceRuntimeHost as AkaneVoiceRuntimeHost
from .host import VoiceCommandExecutionResult as VoiceCommandExecutionResult
from .stream_bridge import VoiceResponseStreamBridge as VoiceResponseStreamBridge
from .stream_bridge import VoiceStreamEventFactory as VoiceStreamEventFactory
from .stream_bridge import VoiceTextArtifactPort as VoiceTextArtifactPort
from .playback_context import render_unsettled_playback_context as render_unsettled_playback_context

from .thinking_runtime.context import VoiceThinkingStartResult as VoiceThinkingStartResult
from .thinking_runtime.context import _GenerationJob as _GenerationJob
from .thinking_runtime.context import _RecoveryVoiceEventFactory as _RecoveryVoiceEventFactory
from .thinking_runtime.executor import AkaneThinkingAgentCommandExecutor as AkaneThinkingAgentCommandExecutor
