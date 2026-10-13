"""Stable durable voice port imports; implementation lives in durable_storage."""

from __future__ import annotations

import json as json
import os as os
import re as re
import sqlite3 as sqlite3
import threading as threading
import uuid as uuid
from collections.abc import Mapping as Mapping
from dataclasses import dataclass as dataclass
from dataclasses import field as field
from hashlib import sha256 as sha256
from pathlib import Path as Path
from typing import Any as Any
from voicecore import ReplayResult as ReplayResult
from voicecore import VoiceCommand as VoiceCommand
from voicecore import VoiceEvent as VoiceEvent
from voicecore import VoiceRuntimeSnapshot as VoiceRuntimeSnapshot
from voicecore import replay_events as replay_events
from voicecore import voice_command_from_dict as voice_command_from_dict
from voicecore import voice_command_to_dict as voice_command_to_dict
from voicecore import voice_event_from_dict as voice_event_from_dict
from voicecore import voice_event_to_dict as voice_event_to_dict
from .host import VoiceCommandReceiptLoadResult as VoiceCommandReceiptLoadResult
from .host import VoiceCommandReceiptRecord as VoiceCommandReceiptRecord
from .host import VoiceHostPortResult as VoiceHostPortResult
from .host import VoiceProjectionOutboxLoadResult as VoiceProjectionOutboxLoadResult
from .stream_bridge import VoiceTextArtifactResult as VoiceTextArtifactResult
from .durable_storage.contracts import _STORAGE_SCHEMA_VERSION as _STORAGE_SCHEMA_VERSION
from .durable_storage.contracts import _JOURNAL_DATABASE_FILENAME as _JOURNAL_DATABASE_FILENAME
from .durable_storage.contracts import _TEXT_ARTIFACT_REF_PATTERN as _TEXT_ARTIFACT_REF_PATTERN
from .durable_storage.contracts import _AUDIO_ARTIFACT_REF_PATTERN as _AUDIO_ARTIFACT_REF_PATTERN
from .durable_storage.contracts import _AUDIO_MEDIA_TYPE_PATTERN as _AUDIO_MEDIA_TYPE_PATTERN
from .durable_storage.contracts import _AUDIO_ARTIFACT_MAGIC as _AUDIO_ARTIFACT_MAGIC
from .durable_storage.contracts import _PROJECTION_TARGETS as _PROJECTION_TARGETS
from .durable_storage.contracts import _PROJECTION_RECORD_FIELDS as _PROJECTION_RECORD_FIELDS
from .durable_storage.contracts import _COMMAND_RECEIPT_PHASES as _COMMAND_RECEIPT_PHASES
from .durable_storage.contracts import _JournalStorageError as _JournalStorageError
from .durable_storage.contracts import VoiceJournalLoadResult as VoiceJournalLoadResult
from .durable_storage.contracts import VoiceJournalReplayResult as VoiceJournalReplayResult
from .durable_storage.contracts import VoiceTextArtifactReadResult as VoiceTextArtifactReadResult
from .durable_storage.contracts import VoiceAudioArtifactResult as VoiceAudioArtifactResult
from .durable_storage.contracts import VoiceAudioArtifactReadResult as VoiceAudioArtifactReadResult
from .durable_storage.storage import _conversation_storage_root as _conversation_storage_root
from .durable_storage.storage import _required_identity as _required_identity
from .durable_storage.storage import _required_generation as _required_generation
from .durable_storage.storage import _canonical_json as _canonical_json
from .durable_storage.storage import _canonical_json_value as _canonical_json_value
from .durable_storage.storage import _record_set_digest as _record_set_digest
from .durable_storage.storage import _atomic_write_json as _atomic_write_json
from .durable_storage.storage import _atomic_write_bytes as _atomic_write_bytes
from .durable_storage.journal import SqliteVoiceRuntimeJournal as SqliteVoiceRuntimeJournal
from .durable_storage.artifacts import FileVoiceTextArtifactPort as FileVoiceTextArtifactPort
from .durable_storage.artifacts import FileVoiceAudioArtifactPort as FileVoiceAudioArtifactPort
from .durable_storage.artifacts import _valid_audio_origin as _valid_audio_origin
