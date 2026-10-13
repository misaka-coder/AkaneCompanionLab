"""Locked care authority persistence, migration, and event receipts."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .care_catalog import MAX_CARE_EVENT_LEDGER
from .care_state_migration import SCHEMA_VERSION, migrate_care_state


class CarePersistenceMixin:
    """Read isolated transaction state and publish it only after atomic save."""

    @staticmethod
    def _find_care_event(state: dict[str, Any], event_id: str) -> dict[str, Any] | None:
        target = str(event_id or "").strip()
        if not target:
            return None
        events = state.get("care_events")
        if not isinstance(events, list):
            return None
        for event in reversed(events):
            if isinstance(event, dict) and str(event.get("event_id") or "") == target:
                return dict(event)
        return None

    @staticmethod
    def _record_care_event(state: dict[str, Any], event: dict[str, Any]) -> None:
        events = state.setdefault("care_events", [])
        if not isinstance(events, list):
            events = []
            state["care_events"] = events
        event_id = str(event.get("event_id") or "").strip()
        if event_id and CarePersistenceMixin._find_care_event(state, event_id) is not None:
            return
        events.append(dict(event))
        if len(events) > MAX_CARE_EVENT_LEDGER:
            del events[:-MAX_CARE_EVENT_LEDGER]

    @staticmethod
    def _poke_cooldown_active(
        state: dict[str, Any],
        *,
        scope_key: str,
        group_scope_key: str,
        now_ms: int,
        cooldown_ms: int,
        group_cooldown_ms: int,
    ) -> bool:
        events = state.get("care_events")
        if not isinstance(events, list):
            return False
        for event in reversed(events):
            if not isinstance(event, dict) or event.get("source") != "poke":
                continue
            if event.get("status") != "ok":
                continue
            if event.get("kind") not in {"coin_change", "inventory_grant", "inventory_consume", "lottery"}:
                continue
            previous_ms = int(event.get("timestamp_ms") or 0)
            if previous_ms <= 0:
                continue
            elapsed = now_ms - previous_ms
            if event.get("scope_key") == scope_key and elapsed < cooldown_ms:
                return True
            if group_scope_key and event.get("group_scope_key") == group_scope_key and elapsed < group_cooldown_ms:
                return True
            if elapsed > max(cooldown_ms, group_cooldown_ms):
                break
        return False

    def _load(self) -> dict[str, Any]:
        if self._state is None:
            state: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
            if self.path.exists():
                # Corrupt authority data must never silently reset balances.
                state = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(state, dict):
                    raise ValueError("care_state_invalid")
            needs_migration = state.get("schema_version") != SCHEMA_VERSION
            if needs_migration:
                origins = self._known_conversations() if self._known_conversations else ()
                # Preserve the exact pre-migration authority before any write.
                # Never replace this snapshot on a retry or later startup.
                backup = self.path.with_name(f"{self.path.name}.pre-v3.bak")
                if self.path.exists() and not backup.exists():
                    temporary = backup.with_name(f"{backup.name}.{uuid.uuid4().hex}.tmp")
                    try:
                        temporary.write_bytes(self.path.read_bytes())
                        temporary.replace(backup)
                    finally:
                        temporary.unlink(missing_ok=True)
                state = migrate_care_state(state, known_conversations=origins)
            if self._reset_baseline_on_first_load:
                baseline_ms = int(time.time() * 1000)
                for conversations in state.get("bodies", {}).values():
                    for body in conversations.values():
                        body["vitals_updated_at_ms"] = baseline_ms
                self._reset_baseline_on_first_load = False
            if needs_migration:
                self._save(state)
            else:
                self._state = state
                self._state_json = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        # Failed persistence leaves both committed memory and disk unchanged.
        # Decode the committed JSON to give each transaction an isolated tree.
        return json.loads(self._state_json)

    def _save(self, state: dict[str, Any]) -> None:
        payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        committed = json.loads(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f"{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp_path.write_text(payload, encoding="utf-8")
            tmp_path.replace(self.path)
        finally:
            tmp_path.unlink(missing_ok=True)
        self._state = committed
        self._state_json = payload
