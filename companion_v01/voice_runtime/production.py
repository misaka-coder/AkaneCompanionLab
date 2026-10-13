"""Stable production voice imports; implementation lives in production_runtime."""
from __future__ import annotations

import hashlib as hashlib
import re as re
import threading as threading
import uuid as uuid
from collections.abc import Mapping as Mapping
from dataclasses import dataclass as dataclass
from datetime import datetime as datetime, timezone as timezone
from pathlib import Path as Path
from typing import Any as Any, Callable as Callable
from capcore_adapter_speech import ASRSessionOpenResult as ASRSessionOpenResult, ASRSessionUpdate as ASRSessionUpdate, NormalizedASRSession as NormalizedASRSession, PCMStreamNormalizer as PCMStreamNormalizer
from voicecore import VoiceEvent as VoiceEvent, initial_snapshot as initial_snapshot
from ..background_tasks import BackgroundTaskRunner as BackgroundTaskRunner
from .asr_bridge import VoiceASRSessionBridge as VoiceASRSessionBridge
from .asr_provider import build_voice_asr_provider as build_voice_asr_provider
from .asr_realtime_turn import VoiceASRRealtimeTurnCoordinator as VoiceASRRealtimeTurnCoordinator
from .private_session import PrivateVoiceMemory as PrivateVoiceMemory, PrivateVoiceLease as PrivateVoiceLease, PrivateVoiceCoordinator as PrivateVoiceCoordinator
from .candidate import AkaneVoiceCandidateValidationCommandExecutor as AkaneVoiceCandidateValidationCommandExecutor
from .durable_ports import FileVoiceAudioArtifactPort as FileVoiceAudioArtifactPort, FileVoiceTextArtifactPort as FileVoiceTextArtifactPort, SqliteVoiceRuntimeJournal as SqliteVoiceRuntimeJournal
from .host import AkaneVoiceRuntimeHost as AkaneVoiceRuntimeHost, VoiceHostPortResult as VoiceHostPortResult
from .playback_delivery import VOICE_PLAYBACK_OUTPUT_MODE as VOICE_PLAYBACK_OUTPUT_MODE, AkaneVoicePlaybackCommandExecutor as AkaneVoicePlaybackCommandExecutor, VoicePlaybackDeliveryChannel as VoicePlaybackDeliveryChannel
from .realtime_protocol import VoiceRealtimeCoordinatorResolution as VoiceRealtimeCoordinatorResolution, VoiceRealtimeOpenRequest as VoiceRealtimeOpenRequest
from .semantic_pulse import AkaneVoiceSemanticPulseCommandExecutor as AkaneVoiceSemanticPulseCommandExecutor
from .thinking_agent import AkaneThinkingAgentCommandExecutor as AkaneThinkingAgentCommandExecutor, VoiceThinkingStartResult as VoiceThinkingStartResult
from .tts_executor import AkaneVoiceTTSCommandExecutor as AkaneVoiceTTSCommandExecutor, VoiceCommandRouterExecutor as VoiceCommandRouterExecutor

from .production_runtime.assembly import _SerializedVoiceRuntimeHost as _SerializedVoiceRuntimeHost
from .production_runtime.events import (
    AkaneVoiceEventFactory as AkaneVoiceEventFactory,
    MemcoreVoiceProjectionPort as MemcoreVoiceProjectionPort,
    _SAFE_REASON as _SAFE_REASON,
    _provider_public_summary as _provider_public_summary,
    _safe_reason as _safe_reason,
)
from .production_runtime.service import AkaneVoiceRuntimeService as AkaneVoiceRuntimeService
from .production_runtime.sessions import (
    AkaneVoiceRuntimeCall as AkaneVoiceRuntimeCall,
    VoiceRuntimeCallOpenResult as VoiceRuntimeCallOpenResult,
    _VoiceRuntimeContext as _VoiceRuntimeContext,
)
