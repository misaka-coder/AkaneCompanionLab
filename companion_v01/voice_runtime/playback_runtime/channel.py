"""The owning playback channel: one host, queue/receipt state and shared lock."""

from __future__ import annotations
import asyncio
import threading
from collections import deque
from typing import Any, Callable
from ..tts_executor import VoiceAudioArtifactPort, VoiceReadableTextArtifactPort
from .contracts import _DeliveryRecord, _ControlRecord
from .delivery import PlaybackDelivery
from .receipts import PlaybackReceipts
from .controls import PlaybackControls
from .lifecycle import PlaybackLifecycle


class VoicePlaybackDeliveryChannel(PlaybackDelivery, PlaybackReceipts, PlaybackControls, PlaybackLifecycle):
    "Bridge one live client playback queue to VoiceCore observations.\n\n    Sending bytes is not delivery. The channel advances VoiceCore only after\n    the client acknowledges enqueue, playback start, and a terminal outcome.\n"

    def __init__(
        self,
        *,
        host: Any,
        voice_turn_id: str,
        conversation_id: str,
        conversation_generation: int,
        text_artifacts: VoiceReadableTextArtifactPort,
        audio_artifacts: VoiceAudioArtifactPort,
        terminal_notifier: Callable[[str], None] | None = None,
    ) -> None:
        self.host = host
        self.voice_turn_id = str(voice_turn_id or "")
        self.conversation_id = str(conversation_id or "")
        self.conversation_generation = int(conversation_generation)
        self.text_artifacts = text_artifacts
        self.audio_artifacts = audio_artifacts
        self.terminal_notifier = terminal_notifier
        self._records: dict[str, _DeliveryRecord] = {}
        self._delivery_by_command_id: dict[str, str] = {}
        self._delivery_by_speech_unit_id: dict[str, str] = {}
        self._controls: dict[str, _ControlRecord] = {}
        self._control_by_command_id: dict[str, str] = {}
        self._outbound: deque[str] = deque()
        self._control_outbound: deque[str] = deque()
        self._closed = False
        self._close_reason = ""
        self._pending_receipts: set[str] = set()
        self._guard = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._activity: asyncio.Event | None = None
        self._terminal_notified = False
        self._presentations: dict[str, dict[str, Any]] = {}
        self._presentation_sent: set[str] = set()

    @property
    def closed(self) -> bool:
        with self._guard:
            return self._closed
