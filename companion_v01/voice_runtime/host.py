"""Stable voice Host imports; implementation lives in host_runtime."""
from __future__ import annotations

from collections.abc import Mapping as Mapping
from dataclasses import dataclass as dataclass
from typing import Any as Any
from typing import Protocol as Protocol
from voicecore import TransitionResult as TransitionResult
from voicecore import TransitionStatus as TransitionStatus
from voicecore import VoiceCommand as VoiceCommand
from voicecore import VoiceEvent as VoiceEvent
from voicecore import VoiceRuntimeSnapshot as VoiceRuntimeSnapshot
from voicecore import initial_snapshot as initial_snapshot
from voicecore import projection_to_host_record as projection_to_host_record
from voicecore import reduce_event as reduce_event
from voicecore import snapshot_to_dict as snapshot_to_dict
from voicecore import validate_snapshot as validate_snapshot
from voicecore import voice_command_from_dict as voice_command_from_dict
from voicecore import voice_command_to_dict as voice_command_to_dict
from voicecore import voice_event_from_dict as voice_event_from_dict
from voicecore import voice_event_to_dict as voice_event_to_dict

from .host_runtime.contracts import VoiceHostPortResult as VoiceHostPortResult
from .host_runtime.contracts import VoiceCommandExecutionResult as VoiceCommandExecutionResult
from .host_runtime.contracts import VoiceProjectionOutboxLoadResult as VoiceProjectionOutboxLoadResult
from .host_runtime.contracts import VoiceCommandReceiptRecord as VoiceCommandReceiptRecord
from .host_runtime.contracts import VoiceCommandReceiptLoadResult as VoiceCommandReceiptLoadResult
from .host_runtime.contracts import VoiceRuntimeJournal as VoiceRuntimeJournal
from .host_runtime.contracts import VoiceProjectionPort as VoiceProjectionPort
from .host_runtime.contracts import VoiceCommandExecutor as VoiceCommandExecutor
from .host_runtime.contracts import VoiceHostDispatchResult as VoiceHostDispatchResult
from .host_runtime.contracts import VoiceHostDriveResult as VoiceHostDriveResult
from .host_runtime.contracts import VoiceProjectionDrainResult as VoiceProjectionDrainResult
from .host_runtime.contracts import VoiceCommandReceiptDrainResult as VoiceCommandReceiptDrainResult
from .host_runtime.runtime import AkaneVoiceRuntimeHost as AkaneVoiceRuntimeHost
