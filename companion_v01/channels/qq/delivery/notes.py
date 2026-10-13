"""Consume one-off delivery feedback in the next QQ context."""
from __future__ import annotations

import threading


class QQDeliveryNotes:
    def __init__(self) -> None:
        self._delivery_notes: dict[str, list[str]] = {}
        self._delivery_notes_lock = threading.RLock()

    def add_delivery_note(self, session_id: str, note: str) -> None:
        key = str(session_id or "").strip()
        note_text = str(note or "").strip()
        if not key or not note_text:
            return
        with self._delivery_notes_lock:
            self._delivery_notes.setdefault(key, []).append(note_text)

    def consume_delivery_notes(self, session_id: str) -> list[str]:
        key = str(session_id or "").strip()
        if not key:
            return []
        with self._delivery_notes_lock:
            notes = self._delivery_notes.pop(key, [])
        return [str(n).strip() for n in notes if str(n).strip()]
