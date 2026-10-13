"""Care relationship updates and completed-turn settlement."""

from __future__ import annotations

import time
from copy import deepcopy
from datetime import datetime
from typing import Any

from .care_config import _bounded_int, _coerce_positive_int, _is_qq_mode
from .care_state import _detect_and_store_tier_event


class CareTurnMixin:
    """Settle a completed reply once under the shared care lock."""

    def apply_affinity_delta(
        self,
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        client_mode: str = "",
        relation_user_id: str = "",
        delta: Any,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        conversation_id = conversation_id or profile_user_id
        delta_value = _bounded_int(delta, -5, 5, fallback=0)
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        with self._lock:
            state = self._load()
            self._body_entry(state, character_pack_id=character_pack_id, conversation_id=conversation_id)
            relation = self._relation_entry(
                state,
                character_pack_id=character_pack_id,
                conversation_id=conversation_id,
                relation_user_id=relation_user_id or profile_user_id,
            )
            before = int(relation.get("qq_affection", 10))
            after = _bounded_int(before + delta_value, 0, 100)
            relation["qq_affection"] = after
            relation["updated_at"] = now_ms
            _detect_and_store_tier_event(relation, before, after)
            self._save(state)
            return self._snapshot(
                state,
                character_pack_id=character_pack_id,
                client_mode=client_mode,
                relation_user_id=relation_user_id or profile_user_id,
                now_ms=now_ms,
                conversation_id=conversation_id,
            )

    def record_turn(
        self,
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        relation_user_id: str = "",
        now_ms: int | None = None,
    ) -> None:
        """Update per-turn memory anchors: first_seen_ms, total_turns, late_night_turns."""
        conversation_id = conversation_id or profile_user_id
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        with self._lock:
            state = self._load()
            relation = self._relation_entry(
                state,
                character_pack_id=character_pack_id,
                relation_user_id=relation_user_id or profile_user_id,
                conversation_id=conversation_id,
            )
            anchors = relation.setdefault("anchors", {})
            if "first_seen_ms" not in anchors:
                anchors["first_seen_ms"] = now_ms
            anchors["total_turns"] = int(anchors.get("total_turns") or 0) + 1
            try:
                hour = datetime.fromtimestamp(now_ms / 1000).hour
                if hour >= 22 or hour < 4:
                    anchors["late_night_turns"] = int(anchors.get("late_night_turns") or 0) + 1
            except Exception:
                pass
            relation["updated_at"] = now_ms
            self._save(state)

    def settle_turn(
        self,
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        relation_user_id: str = "",
        client_mode: str,
        energy_cost: int,
        coin_reward: int = 0,
        affinity_delta: int = 0,
        event_id: str = "",
        now_ms: int | None = None,
        consumed_tier_event: Any = None,
        shared_body: bool = False,
    ) -> dict[str, Any]:
        """Commit a completed reply's care effects once, in one transaction."""
        conversation_id = conversation_id or profile_user_id
        user = relation_user_id or profile_user_id
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        with self._lock:
            state = self._load()
            snapshot_args = dict(
                character_pack_id=character_pack_id,
                conversation_id=conversation_id,
                relation_user_id=user,
                client_mode=client_mode,
                now_ms=now_ms,
            )
            receipt = f"turn:{character_pack_id}:{conversation_id}:{event_id}" if event_id else ""
            if receipt and self._find_care_event(state, receipt):
                return self._snapshot(state, **snapshot_args)
            body = self._body_entry(state, character_pack_id=character_pack_id, conversation_id=conversation_id)
            relation = self._relation_entry(
                state, character_pack_id=character_pack_id, conversation_id=conversation_id, relation_user_id=user
            )
            if _is_qq_mode(client_mode) or shared_body:
                self._apply_body_time_decay(body, now_ms)
            body["energy"] = max(0, int(body["energy"]) - max(0, int(energy_cost)))
            body["updated_at"] = now_ms
            if coin_reward:
                account = self._account_entry(state, character_pack_id=character_pack_id, relation_user_id=user)
                account["qq_coins"] = _bounded_int(
                    int(account.get("qq_coins") or 0) + max(0, int(coin_reward)), 0, 999999
                )
            anchors = relation.setdefault("anchors", {})
            anchors.setdefault("first_seen_ms", now_ms)
            anchors["total_turns"] = int(anchors.get("total_turns") or 0) + 1
            hour = datetime.fromtimestamp(now_ms / 1000).hour
            if hour >= 22 or hour < 4:
                anchors["late_night_turns"] = int(anchors.get("late_night_turns") or 0) + 1
            if consumed_tier_event and relation.get("pending_tier_event") == consumed_tier_event:
                relation.pop("pending_tier_event", None)
            delta = _bounded_int(affinity_delta, -5, 5)
            before = int(relation.get("qq_affection", 10))
            after = _bounded_int(before + delta, 0, 100)
            relation["qq_affection"] = after
            _detect_and_store_tier_event(relation, before, after)
            relation["updated_at"] = now_ms
            if receipt:
                self._record_care_event(state, {"event_id": receipt, "kind": "turn_settlement", "timestamp_ms": now_ms})
            snapshot = self._snapshot(state, **snapshot_args)
            self._save(state)
            return snapshot

    def apply_energy_cost(
        self,
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        relation_user_id: str = "",
        energy_cost: int = 1,
        coin_reward: int = 0,
        now_ms: int | None = None,
    ) -> None:
        """Deduct energy from shared body; optionally credit coins to the sender."""
        conversation_id = conversation_id or profile_user_id
        cost = max(0, int(energy_cost))
        reward = max(0, int(coin_reward))
        if cost == 0 and reward == 0:
            return
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        with self._lock:
            state = self._load()
            body = self._body_entry(state, character_pack_id=character_pack_id, conversation_id=conversation_id)
            if cost > 0:
                current = _bounded_int(body.get("energy"), 0, 100, fallback=70)
                body["energy"] = max(0, current - cost)
                body["vitals_updated_at_ms"] = now_ms
            if reward > 0:
                rel_key = relation_user_id or profile_user_id
                relation = self._relation_entry(
                    state,
                    character_pack_id=character_pack_id,
                    relation_user_id=rel_key,
                    conversation_id=conversation_id,
                )
                account = self._account_entry(state, character_pack_id=character_pack_id, relation_user_id=rel_key)
                current_coins = _bounded_int(account.get("qq_coins"), 0, 999999, fallback=0)
                account["qq_coins"] = min(999999, current_coins + reward)
                relation["updated_at"] = now_ms
            self._save(state)

    def consume_tier_event(
        self,
        *,
        profile_user_id: str,
        character_pack_id: str = "",
        conversation_id: str = "",
        relation_user_id: str = "",
    ) -> dict[str, Any] | None:
        with self._lock:
            state = self._load()
            relation = self._relation_entry(
                state,
                character_pack_id=character_pack_id,
                conversation_id=conversation_id or profile_user_id,
                relation_user_id=relation_user_id or profile_user_id,
            )
            event = relation.pop("pending_tier_event", None)
            if event:
                self._save(state)
            return deepcopy(event)
