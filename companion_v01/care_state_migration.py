"""One-time split of role vitals, conversation relationships and user accounts."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "akane.care_runtime.v3"
RELATIONSHIP_ANCHORS_VERSION = 1
ACCOUNT_FIELDS = frozenset({
    "qq_coins", "qq_redeem_quota", "qq_saiken_balance", "qq_saiken_count",
    "inventory", "last_offering_date", "last_checkin_date", "checkin_streak",
})
RELATION_FIELDS = frozenset({"qq_affection", "anchors", "pending_tier_event", "updated_at"})
BODY_FIELDS = frozenset({
    "hunger", "energy", "hunger_frac", "energy_frac", "vitals_updated_at_ms", "updated_at",
})


def migrate_care_state(
    state: dict[str, Any], *, known_conversations: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Freeze the old snapshot; seed only conversations already known at migration.

    Old data cannot reconstruct separate past vitals. Each existing conversation
    starts from the same frozen value, then evolves independently. No balances
    or inventories are copied into conversation records.
    """
    if state.get("schema_version") == SCHEMA_VERSION:
        return state
    if state.get("schema_version") not in (None, "", "akane.care_runtime.v1", "akane.care_runtime.v2"):
        raise ValueError("care_schema_unsupported")
    old_characters = deepcopy(state.get("characters") or {})
    old_relations = deepcopy(state.get("relations") or {})
    state["legacy_v2"] = {"characters": old_characters, "relations": old_relations}
    state["bodies"] = {}
    state["relationships"] = {}
    state["accounts"] = {
        char: {user: {key: deepcopy(value) for key, value in record.items() if key in ACCOUNT_FIELDS}
               for user, record in records.items() if isinstance(record, dict)}
        for char, records in old_relations.items() if isinstance(records, dict)
    }
    state["economies"] = {
        char: {"shrine_total": max(0, int(body.get("shrine_total") or 0))}
        for char, body in old_characters.items() if isinstance(body, dict)
    }
    for item in known_conversations:
        char = str(item.get("character_pack_id") or "default_character").strip()
        conversation = str(item.get("session_id") or "").strip()
        if not conversation:
            continue
        old_body = old_characters.get(char) or {}
        if isinstance(old_body, dict):
            state["bodies"].setdefault(char, {})[conversation] = {
                key: deepcopy(value) for key, value in old_body.items() if key in BODY_FIELDS
            }
        old_users = old_relations.get(char) or {}
        known_users = {re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(user)).strip("_") or "default"
                       for user in item.get("relation_user_ids", ())}
        state["relationships"].setdefault(char, {})[conversation] = {
            user: {key: deepcopy(value) for key, value in record.items() if key in RELATION_FIELDS}
            for user, record in old_users.items() if isinstance(record, dict) and user in known_users
        }
    # Keep legacy desktop balances/work/inventory as a separate, unchanged
    # snapshot. The owner binding deliberately starts from the QQ account.
    state.pop("characters", None)
    state.pop("relations", None)
    state["schema_version"] = SCHEMA_VERSION
    state["relationship_anchors_version"] = RELATIONSHIP_ANCHORS_VERSION
    return state


def restore_relationship_anchors(
    state: dict[str, Any], *, migration_baseline: Mapping[str, Any],
) -> dict[str, Any]:
    """Repair the early v3 omission using the proven original migration baseline.

    The caller must reconstruct the baseline from the stopped pre-migration
    backup, including its known conversations. Do not infer old membership
    from today's conversations or overwrite post-migration assets/state.
    """
    if state.get("schema_version") != SCHEMA_VERSION or migration_baseline.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("care_anchor_repair_requires_v3")
    if state.get("relationship_anchors_version") == RELATIONSHIP_ANCHORS_VERSION:
        return state
    if not state.get("legacy_v2") or state.get("legacy_v2") != migration_baseline.get("legacy_v2"):
        raise ValueError("care_anchor_repair_baseline_mismatch")
    repaired = deepcopy(state)
    for char, conversations in migration_baseline.get("relationships", {}).items():
        for conversation, users in conversations.items():
            current_users = repaired.get("relationships", {}).get(char, {}).get(conversation, {})
            for user, baseline_relation in users.items():
                current_relation = current_users.get(user)
                old = baseline_relation.get("anchors")
                if not isinstance(current_relation, dict) or not isinstance(old, dict) or not old:
                    continue
                anchors = current_relation.setdefault("anchors", {})
                # An already restored relation must not count its history twice.
                already_inherited = int(anchors.get("first_seen_ms") or 0) == int(old.get("first_seen_ms") or 0) > 0
                for key, value in old.items():
                    if key in {"total_turns", "late_night_turns"} and not already_inherited:
                        anchors[key] = int(value or 0) + int(anchors.get(key) or 0)
                    elif key in {"first_seen_ms", "first_fed"}:
                        previous = anchors.get(key)
                        old_ms = int(value.get("ms") or 0) if isinstance(value, dict) else int(value or 0)
                        new_ms = int(previous.get("ms") or 0) if isinstance(previous, dict) else int(previous or 0)
                        if old_ms > 0 and (new_ms <= 0 or old_ms < new_ms):
                            anchors[key] = deepcopy(value)
                    elif key == "max_checkin_streak":
                        anchors[key] = max(int(value or 0), int(anchors.get(key) or 0))
                    elif key not in anchors:
                        anchors[key] = deepcopy(value)
    repaired["relationship_anchors_version"] = RELATIONSHIP_ANCHORS_VERSION
    return repaired
