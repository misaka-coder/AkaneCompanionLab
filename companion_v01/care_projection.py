"""Client care snapshots, desktop authority views, and feeding presentation."""

from __future__ import annotations

import time
from copy import deepcopy
from typing import Any

from .care_catalog import DEFAULT_THRESHOLDS, DESKTOP_AUTHORITY_VERSION
from .care_config import (
    _bounded_int,
    _coerce_positive_int,
    _initial_desktop_state,
    _is_qq_mode,
    _normalize_work_task,
    _safe_key,
    normalize_desktop_care_config,
    normalize_desktop_legacy_state,
)
from .care_state import _affection_tier, _detect_and_store_tier_event


def format_care_feed_prompt(
    *,
    item_name: str,
    item_description: str = "",
    hunger_delta: int = 0,
    energy_delta: int = 0,
    affection_delta: int = 0,
    current_hunger: int = 0,
    current_energy: int = 0,
    current_affection: int = 0,
    actor_label: str = "我",
    quantity: int = 1,
    extra_facts: list[str] | None = None,
    user_message: str = "",
) -> dict[str, Any]:
    """Universal prompt and interaction fact builder for feeding Akane.

    Shared across Desktop Pet, QQ Bot, and Scene Room. Supports quantity and
    client-specific extra_facts extensions while guaranteeing canonical state format
    and turn_kind="desktop_pet_care_feed" energy exemption.
    """

    def _signed(n: int) -> str:
        val = int(n or 0)
        return f"+{val}" if val > 0 else str(val)

    qty_suffix = f" x{quantity}" if quantity > 1 else ""
    feed_lead = f"刚才发生的互动：{actor_label}投喂了你「{item_name}」{qty_suffix}。"

    status_line = (
        f"状态变化：饥饿 {_signed(hunger_delta)}，精力 {_signed(energy_delta)}，好感 {_signed(affection_delta)}。"
    )
    current_state_line = (
        f"当前状态：饥饿 {int(current_hunger or 0)}/100，"
        f"精力 {int(current_energy or 0)}/100，"
        f"好感 {int(current_affection or 0)}/100。"
    )

    description = " ".join(str(item_description or "").split())[:120]
    description_line = f"商品介绍：{description}" if description else ""
    lines = [feed_lead, status_line, current_state_line]
    if description_line:
        lines.append(description_line)
    if extra_facts:
        lines.extend(f for f in extra_facts if f)
    if user_message:
        lines.append(user_message)

    turn_message = "\n".join(lines)
    memory_message = user_message or feed_lead

    return {
        "turn_message": turn_message,
        "memory_message": memory_message,
        "turn_kind": "desktop_pet_care_feed",
        "feed_lead": feed_lead,
        "description_line": description_line,
        "status_line": status_line,
        "state_line": current_state_line,
    }


class CareProjectionMixin:
    """Project body, relationship, and account authority for each client."""

    def sync_from_client(
        self,
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        client_mode: str = "",
        care_payload: Any,
        relation_user_id: str = "",
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        conversation_id = conversation_id or profile_user_id
        if not isinstance(care_payload, dict) or care_payload.get("enabled") is False:
            return self.snapshot_for_client(
                profile_user_id=profile_user_id,
                character_pack_id=character_pack_id,
                client_mode=client_mode,
                relation_user_id=relation_user_id,
                now_ms=now_ms,
                conversation_id=conversation_id,
            )

        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        with self._lock:
            state = self._load()
            body = (
                self._body_entry(state, character_pack_id=character_pack_id, conversation_id=conversation_id)
                if _is_qq_mode(client_mode)
                else self._desktop_view(
                    state,
                    character_pack_id=character_pack_id,
                    conversation_id=conversation_id,
                    relation_user_id=relation_user_id or profile_user_id,
                )
            )
            if (
                not _is_qq_mode(client_mode)
                and int(body.get("desktop_authority_version") or 0) >= DESKTOP_AUTHORITY_VERSION
            ):
                return self._snapshot(
                    state,
                    character_pack_id=character_pack_id,
                    client_mode=client_mode,
                    relation_user_id=relation_user_id or profile_user_id,
                    now_ms=now_ms,
                    conversation_id=conversation_id,
                )
            vitals_written = False
            for key in ("hunger", "energy"):
                if key in care_payload:
                    body[key] = _bounded_int(care_payload.get(key), 0, 100, fallback=int(body.get(key, 0)))
                    vitals_written = True
            if vitals_written:
                body["vitals_updated_at_ms"] = now_ms
            if not _is_qq_mode(client_mode) and "coins" in care_payload:
                body["coins"] = _bounded_int(care_payload.get("coins"), 0, 999999, fallback=int(body.get("coins", 20)))
            desktop_tier_event = None
            if "affection" in care_payload:
                if _is_qq_mode(client_mode):
                    relation = self._relation_entry(
                        state,
                        character_pack_id=character_pack_id,
                        relation_user_id=relation_user_id or profile_user_id,
                        conversation_id=conversation_id,
                    )
                    relation["qq_affection"] = _bounded_int(
                        care_payload.get("affection"),
                        0,
                        100,
                        fallback=int(relation.get("qq_affection", 10)),
                    )
                    relation["updated_at"] = now_ms
                else:
                    aff_before = int(body.get("desktop_affection", 10))
                    aff_after = _bounded_int(care_payload.get("affection"), 0, 100, fallback=aff_before)
                    tier_before = _affection_tier(aff_before)
                    tier_after = _affection_tier(aff_after)
                    if tier_before != tier_after:
                        desktop_tier_event = {
                            "from_tier": tier_before,
                            "to_tier": tier_after,
                            "direction": "up" if aff_after > aff_before else "down",
                        }
                    body["desktop_affection"] = aff_after
            body["updated_at"] = now_ms
            body["last_client_sync_at"] = now_ms
            if not _is_qq_mode(client_mode):
                self._commit_desktop_view(
                    state,
                    body,
                    character_pack_id=character_pack_id,
                    conversation_id=conversation_id,
                    relation_user_id=relation_user_id or profile_user_id,
                )
            self._save(state)
            snap = self._snapshot(
                state,
                character_pack_id=character_pack_id,
                client_mode=client_mode,
                relation_user_id=relation_user_id or profile_user_id,
                now_ms=now_ms,
                conversation_id=conversation_id,
            )
            if desktop_tier_event:
                snap["pending_tier_event"] = desktop_tier_event
            return snap

    def snapshot_for_client(
        self,
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        client_mode: str = "",
        relation_user_id: str = "",
        now_ms: int | None = None,
        hunger_per_hour: float | None = None,
    ) -> dict[str, Any]:
        conversation_id = conversation_id or profile_user_id
        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        with self._lock:
            state = self._load()
            body = self._body_entry(state, character_pack_id=character_pack_id, conversation_id=conversation_id)
            pending_tier_event = None
            if _is_qq_mode(client_mode):
                relation = self._relation_entry(
                    state,
                    character_pack_id=character_pack_id,
                    relation_user_id=relation_user_id or profile_user_id,
                    conversation_id=conversation_id,
                )
                self._apply_body_time_decay(
                    body,
                    now_ms,
                    hunger_per_hour=hunger_per_hour if hunger_per_hour is not None else 8.0,
                )
                # A card read must not consume the next model turn's event.
                pending_tier_event = deepcopy(relation.get("pending_tier_event"))
            self._save(state)
            snap = self._snapshot(
                state,
                character_pack_id=character_pack_id,
                client_mode=client_mode,
                relation_user_id=relation_user_id or profile_user_id,
                now_ms=now_ms,
                conversation_id=conversation_id,
            )
            if pending_tier_event:
                snap["pending_tier_event"] = pending_tier_event
            return snap

    def snapshot_for_desktop(
        self,
        *,
        profile_user_id: str,
        conversation_id: str = "",
        character_pack_id: str = "",
        relation_user_id: str = "",
        shared_account: bool = False,
        care_config: Any = None,
        legacy_state: Any = None,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Return the desktop authority snapshot and import legacy state once."""
        conversation_id = conversation_id or profile_user_id
        relation_user_id = relation_user_id or profile_user_id

        now_ms = _coerce_positive_int(now_ms, fallback=int(time.time() * 1000))
        config = normalize_desktop_care_config(care_config)
        with self._lock:
            state = self._load()
            character_key = _safe_key(character_pack_id or "default_character")
            existing_body = conversation_id in state.get("bodies", {}).get(character_key, {})
            body = self._desktop_view(
                state,
                character_pack_id=character_pack_id,
                conversation_id=conversation_id,
                relation_user_id=relation_user_id,
                shared_account=shared_account,
            )
            migration = self._ensure_desktop_authority(
                body,
                config=config,
                legacy_state=legacy_state,
                had_existing_body=existing_body,
                now_ms=now_ms,
            )
            (self._apply_body_time_decay if shared_account else self._apply_desktop_hunger_decay)(
                body,
                now_ms,
                hunger_per_hour=8.0 if shared_account else float(config["decay"]["hunger_per_hour"]),
            )
            self._commit_desktop_view(
                state,
                body,
                character_pack_id=character_pack_id,
                conversation_id=conversation_id,
                relation_user_id=relation_user_id,
            )
            self._save(state)
            return {
                "ok": True,
                "status": "ok",
                "reason": "",
                "migration": migration,
                "snapshot": self._snapshot(
                    state,
                    character_pack_id=character_pack_id,
                    client_mode="desktop_pet",
                    relation_user_id=relation_user_id,
                    now_ms=now_ms,
                    conversation_id=conversation_id,
                ),
            }

    def _snapshot(
        self,
        state: dict[str, Any],
        *,
        conversation_id: str,
        character_pack_id: str,
        client_mode: str,
        relation_user_id: str,
        now_ms: int,
    ) -> dict[str, Any]:
        body = self._body_entry(state, character_pack_id=character_pack_id, conversation_id=conversation_id)
        relation = self._relation_entry(
            state,
            character_pack_id=character_pack_id,
            relation_user_id=relation_user_id,
            conversation_id=conversation_id,
        )
        account = self._account_entry(state, character_pack_id=character_pack_id, relation_user_id=relation_user_id)
        affection = _bounded_int(relation.get("qq_affection"), 0, 100, fallback=10)
        affection_scope = "qq_text" if _is_qq_mode(client_mode) else "desktop_pet"
        coins = _bounded_int(account.get("qq_coins"), 0, 999999, fallback=0)
        last_offering_date = str(account.get("last_offering_date") or "")
        inventory = {k: dict(v) for k, v in (account.get("inventory") or {}).items() if isinstance(v, dict)}
        if not _is_qq_mode(client_mode):
            inventory = {k: int(v.get("count") or 0) for k, v in inventory.items() if int(v.get("count") or 0) > 0}
        checkin_streak = int(account.get("checkin_streak") or 0)
        redeem_quota = _bounded_int(account.get("qq_redeem_quota"), 0, 999999, fallback=0)
        saiken_balance = _bounded_int(account.get("qq_saiken_balance"), 0, 999999999, fallback=0)
        saiken_count = int(account.get("qq_saiken_count") or 0)
        relation_anchors = deepcopy(relation.get("anchors") or {})
        snapshot = {
            "enabled": True,
            "source": "care_runtime",
            "shared_vitals": True,
            "vitals_scope": "conversation",
            "affection_scope": affection_scope,
            "now": now_ms,
            "hunger": _bounded_int(body.get("hunger"), 0, 100, fallback=55),
            "energy": _bounded_int(body.get("energy"), 0, 100, fallback=70),
            "coins": coins,
            "redeem_quota": redeem_quota,
            "saiken_balance": saiken_balance,
            "saiken_count": saiken_count,
            "shrine_total": _bounded_int(
                self._economy_entry(state, character_pack_id=character_pack_id).get("shrine_total"),
                0,
                999999999,
                fallback=0,
            ),
            "affection": affection,
            "checkin_streak": checkin_streak,
            "last_offering_date": last_offering_date,
            "inventory": inventory,
            "thresholds": dict(DEFAULT_THRESHOLDS),
            "anchors": relation_anchors,
        }
        if relation.get("pending_tier_event"):
            snapshot["pending_tier_event"] = deepcopy(relation["pending_tier_event"])
        if not _is_qq_mode(client_mode):
            snapshot.update(
                {
                    "authority": "care_runtime",
                    "work_task": _normalize_work_task(account.get("desktop_work_task")),
                    "last_allowance_at": max(0, int(account.get("desktop_last_allowance_at_ms") or 0)),
                    "last_decay_at": max(0, int(body.get("vitals_updated_at_ms") or 0)),
                    "updated_at": max(0, int(body.get("updated_at") or 0)),
                }
            )
        return snapshot

    def _ensure_desktop_authority(
        self,
        body: dict[str, Any],
        *,
        config: dict[str, Any],
        legacy_state: Any,
        had_existing_body: bool,
        now_ms: int,
    ) -> dict[str, Any]:
        if int(body.get("desktop_authority_version") or 0) >= DESKTOP_AUTHORITY_VERSION:
            return {"status": "already_authoritative", "imported": False}

        legacy = normalize_desktop_legacy_state(legacy_state, config=config)
        had_desktop_sync = int(body.get("last_client_sync_at") or 0) > 0
        migration_source = "character_defaults"
        if legacy is not None:
            migration_source = "legacy_pet_state"
        elif had_existing_body:
            migration_source = "care_runtime_v1"

        if not had_existing_body:
            source = legacy or _initial_desktop_state(config)
            body["hunger"] = source["hunger"]
            body["energy"] = source["energy"]
        if not had_desktop_sync:
            source = legacy or _initial_desktop_state(config)
            body["coins"] = source["coins"]
            body["desktop_affection"] = source["affection"]

        migration_state = legacy or _initial_desktop_state(config)
        body["desktop_inventory"] = dict(migration_state["inventory"])
        body["desktop_work_task"] = _normalize_work_task(migration_state.get("work_task"))
        body["desktop_last_allowance_at_ms"] = max(
            0,
            int(migration_state.get("last_allowance_at") or 0),
        )
        body["desktop_authority_version"] = DESKTOP_AUTHORITY_VERSION
        body["desktop_authority_initialized_at_ms"] = now_ms
        body["desktop_authority_migration_source"] = migration_source
        body["vitals_updated_at_ms"] = now_ms
        body["updated_at"] = now_ms
        return {
            "status": "imported" if legacy is not None else "initialized",
            "imported": legacy is not None,
            "source": migration_source,
        }

    def _desktop_view(
        self,
        state: dict[str, Any],
        *,
        character_pack_id: str,
        conversation_id: str,
        relation_user_id: str,
        shared_account: bool = False,
    ) -> dict[str, Any]:
        body = self._body_entry(state, character_pack_id=character_pack_id, conversation_id=conversation_id)
        relation = self._relation_entry(
            state,
            character_pack_id=character_pack_id,
            conversation_id=conversation_id,
            relation_user_id=relation_user_id,
        )
        account = self._account_entry(state, character_pack_id=character_pack_id, relation_user_id=relation_user_id)
        view = deepcopy(body)
        view.update({key: deepcopy(value) for key, value in account.items() if key.startswith("desktop_")})
        view["coins"] = int(account.get("qq_coins") or 0)
        view["desktop_affection"] = int(relation.get("qq_affection", 10))
        view["desktop_inventory"] = {
            key: int(value.get("count") or 0)
            for key, value in (account.get("inventory") or {}).items()
            if isinstance(value, dict)
        }
        if shared_account:
            # QQ is the authority for a binding. Never import stale local money
            # or reset its existing body/relationship from desktop defaults.
            view["desktop_authority_version"] = DESKTOP_AUTHORITY_VERSION
        elif not view.get("desktop_authority_version"):
            char = _safe_key(character_pack_id or "default_character")
            claims = state.setdefault("legacy_desktop_claims", {})
            old = (state.get("legacy_v2", {}).get("characters") or {}).get(char)
            if isinstance(old, dict) and char not in claims:
                view.update(deepcopy(old))
                claims[char] = relation_user_id
        return view

    def _commit_desktop_view(
        self,
        state: dict[str, Any],
        view: dict[str, Any],
        *,
        character_pack_id: str,
        conversation_id: str,
        relation_user_id: str,
    ) -> None:
        body = self._body_entry(state, character_pack_id=character_pack_id, conversation_id=conversation_id)
        relation = self._relation_entry(
            state,
            character_pack_id=character_pack_id,
            conversation_id=conversation_id,
            relation_user_id=relation_user_id,
        )
        account = self._account_entry(state, character_pack_id=character_pack_id, relation_user_id=relation_user_id)
        for key in (
            "hunger",
            "energy",
            "hunger_frac",
            "energy_frac",
            "vitals_updated_at_ms",
            "updated_at",
            "last_client_sync_at",
            "desktop_hunger_decay_fraction",
        ):
            if key in view:
                body[key] = view[key]
        before = int(relation.get("qq_affection", 10))
        after = int(view.get("desktop_affection", 10))
        _detect_and_store_tier_event(relation, before, after)
        relation["qq_affection"] = after
        account["qq_coins"] = int(view.get("coins") or 0)
        old_inventory = account.get("inventory") or {}
        account["inventory"] = {
            key: {
                **deepcopy(old_inventory.get(key) or {}),
                **deepcopy((view.get("desktop_inventory_metadata") or {}).get(key) or {}),
                "count": int(count),
            }
            for key, count in (view.get("desktop_inventory") or {}).items()
            if int(count) > 0
        }
        for key, value in view.items():
            if key.startswith("desktop_") and key not in {
                "desktop_affection",
                "desktop_inventory",
                "desktop_inventory_metadata",
                "desktop_hunger_decay_fraction",
            }:
                account[key] = deepcopy(value)
