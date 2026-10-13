"""Existing durable result types and schema/envelope constants."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from voicecore import ReplayResult, VoiceEvent


_STORAGE_SCHEMA_VERSION = 3

_JOURNAL_DATABASE_FILENAME = "journal.sqlite3"

_TEXT_ARTIFACT_REF_PATTERN = re.compile(r"^voice-text:(?P<digest>[0-9a-f]{64})$")

_AUDIO_ARTIFACT_REF_PATTERN = re.compile(r"^voice-audio:(?P<digest>[0-9a-f]{64})$")

_AUDIO_MEDIA_TYPE_PATTERN = re.compile(r"^audio/[a-z0-9][a-z0-9.+-]{0,63}$")

_AUDIO_ARTIFACT_MAGIC = b"AKANE-VOICE-AUDIO\x00\x01"

_PROJECTION_TARGETS = frozenset({"runtime", "host", "memcore", "model"})

_PROJECTION_RECORD_FIELDS = frozenset({"projection_id", "target", "kind", "source_event_id", "payload"})

_COMMAND_RECEIPT_PHASES = frozenset({"executing", "observations_pending", "completed", "released"})


class _JournalStorageError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class VoiceJournalLoadResult:
    status: str
    reason: str = ""
    events: tuple[VoiceEvent, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


@dataclass(frozen=True)
class VoiceJournalReplayResult:
    status: str
    reason: str = ""
    replay: ReplayResult | None = None

    @property
    def ok(self) -> bool:
        return self.status == "succeeded" and self.replay is not None


@dataclass(frozen=True)
class VoiceTextArtifactReadResult:
    status: str
    reason: str = ""
    text: str = ""
    media_type: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


@dataclass(frozen=True)
class VoiceAudioArtifactResult:
    status: str
    artifact_ref: str = ""
    reason: str = ""
    retryable: bool = False

    @property
    def ok(self) -> bool:
        return self.status in {"succeeded", "duplicate"} and bool(self.artifact_ref)

    @classmethod
    def succeeded(cls, artifact_ref: str) -> VoiceAudioArtifactResult:
        return cls(status="succeeded", artifact_ref=artifact_ref)

    @classmethod
    def duplicate(cls, artifact_ref: str) -> VoiceAudioArtifactResult:
        return cls(status="duplicate", artifact_ref=artifact_ref)

    @classmethod
    def failed(
        cls,
        reason: str,
        *,
        retryable: bool = False,
    ) -> VoiceAudioArtifactResult:
        return cls(status="failed", reason=reason, retryable=retryable)


@dataclass(frozen=True)
class VoiceAudioArtifactReadResult:
    status: str
    reason: str = ""
    audio: bytes = b""
    media_type: str = ""
    origin: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"
