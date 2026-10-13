"""One journal object, conversation identity, database path and shared lock."""

from __future__ import annotations

import threading
from pathlib import Path
from .commands import JournalCommands
from .events import JournalEvents
from .schema import JournalSchema
from .contracts import _JOURNAL_DATABASE_FILENAME
from .storage import _conversation_storage_root, _required_identity, _required_generation


class SqliteVoiceRuntimeJournal(JournalEvents, JournalCommands, JournalSchema):
    "Transactional VoiceEvent journal for one conversation generation."

    def __init__(
        self,
        *,
        state_dir: Path,
        conversation_id: str,
        conversation_generation: int,
    ) -> None:
        self.conversation_id = _required_identity(conversation_id, "conversation_id")
        self.conversation_generation = _required_generation(conversation_generation)
        self._conversation_root = _conversation_storage_root(
            state_dir=state_dir,
            conversation_id=self.conversation_id,
            conversation_generation=self.conversation_generation,
        )
        self._database_path = self._conversation_root / _JOURNAL_DATABASE_FILENAME
        self._guard = threading.RLock()
