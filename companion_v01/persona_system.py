from __future__ import annotations

import hashlib
import re
import time
from typing import Any

from .store import MemoryStore


PERSONA_TEXT_LIMITS = {
    "name": 32,
    "summary": 220,
    "speech_style": 180,
    "interaction_bias": 180,
    "resource_preference": 180,
    "switch_hint": 160,
    "unsuitable_contexts": 180,
    "reason": 240,
}


class PersonaCardService:
    def __init__(self, *, store: MemoryStore) -> None:
        self.store = store

    def build_prompt_context(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        visible_limit: int = 5,
    ) -> dict[str, Any]:
        active = self.store.get_active_persona_card(
            profile_user_id=profile_user_id,
            session_id=session_id,
        )
        cards = self.store.list_persona_cards(
            profile_user_id=profile_user_id,
            session_id=session_id,
            statuses=["active", "inactive"],
            limit=max(1, min(12, int(visible_limit or 5) + 1)),
        )
        inactive_cards = [
            card
            for card in cards
            if str(card.get("status") or "") in {"active", "inactive"}
            and str(card.get("card_id") or "") != str((active or {}).get("card_id") or "")
        ][: max(0, min(12, int(visible_limit or 5)))]

        return {
            "active_id": str((active or {}).get("card_id") or ""),
            "active_card": active,
            "inactive_cards": inactive_cards,
            "system_context": self._render_system_context(active),
            "reference_context": self._render_reference_context(inactive_cards),
        }

    def create_card(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        name: str,
        summary: str = "",
        speech_style: str = "",
        interaction_bias: str = "",
        resource_preference: str = "",
        switch_hint: str = "",
        unsuitable_contexts: str = "",
        reason: str = "",
        timestamp: int | None = None,
        source_id: str = "",
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        normalized_name = self._clip(name, "name")
        if not normalized_name:
            raise ValueError("missing persona name")
        normalized_summary = self._clip(summary, "summary")
        if not normalized_summary:
            raise ValueError("missing persona summary")

        previous_active = self.store.get_active_persona_card(
            profile_user_id=profile_user_id,
            session_id=session_id,
        )
        card_id = self._unique_card_id(
            profile_user_id=profile_user_id,
            session_id=session_id,
            name=normalized_name,
        )
        source_ids = [source_id] if str(source_id or "").strip() else None
        card = self.store.create_persona_card(
            card_id=card_id,
            profile_user_id=profile_user_id,
            session_id=session_id,
            name=normalized_name,
            status="active",
            summary=normalized_summary,
            speech_style=self._clip(speech_style, "speech_style"),
            interaction_bias=self._clip(interaction_bias, "interaction_bias"),
            resource_preference=self._clip(resource_preference, "resource_preference"),
            switch_hint=self._clip(switch_hint, "switch_hint"),
            unsuitable_contexts=self._clip(unsuitable_contexts, "unsuitable_contexts"),
            created_reason=self._clip(reason, "reason"),
            updated_reason="",
            source_ids=source_ids,
            timestamp=effective_ts,
        )
        if previous_active:
            self._log_event(
                profile_user_id=profile_user_id,
                session_id=session_id,
                card_id=str(previous_active.get("card_id") or ""),
                event_type="deactivated",
                reason="new persona card created",
                source_id=source_id,
                timestamp=effective_ts,
                payload={"next_card_id": card_id},
            )
        self._log_event(
            profile_user_id=profile_user_id,
            session_id=session_id,
            card_id=card_id,
            event_type="created",
            reason=reason,
            source_id=source_id,
            timestamp=effective_ts,
            payload={"card": self._compact_card(card)},
        )
        self._log_event(
            profile_user_id=profile_user_id,
            session_id=session_id,
            card_id=card_id,
            event_type="activated",
            reason="created card defaults to active",
            source_id=source_id,
            timestamp=effective_ts,
            payload={"previous_card_id": str((previous_active or {}).get("card_id") or "")},
        )
        return card

    def update_active_card(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        fields: dict[str, Any],
        reason: str = "",
        timestamp: int | None = None,
        source_id: str = "",
    ) -> dict[str, Any]:
        active = self.store.get_active_persona_card(profile_user_id=profile_user_id, session_id=session_id)
        if active is None:
            raise ValueError("no active persona card")
        normalized_fields = self._normalize_update_fields(fields)
        normalized_reason = self._clip(reason, "reason")
        if normalized_reason:
            normalized_fields["updated_reason"] = normalized_reason
        if not normalized_fields:
            raise ValueError("missing persona update fields")
        source_ids = [source_id] if str(source_id or "").strip() else None
        updated = self.store.update_persona_card(
            profile_user_id=profile_user_id,
            session_id=session_id,
            card_id=str(active.get("card_id") or ""),
            fields=normalized_fields,
            source_ids=source_ids,
            timestamp=timestamp,
        )
        if updated is None:
            raise ValueError("active persona card disappeared")
        self._log_event(
            profile_user_id=profile_user_id,
            session_id=session_id,
            card_id=str(updated.get("card_id") or ""),
            event_type="updated",
            reason=normalized_reason,
            source_id=source_id,
            timestamp=timestamp,
            payload={"updated_fields": sorted(normalized_fields.keys())},
        )
        return updated

    def inspect_card(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        card_id: str = "",
        name: str = "",
    ) -> dict[str, Any] | None:
        query = str(card_id or name or "").strip()
        if query:
            return self.store.find_persona_card(
                profile_user_id=profile_user_id,
                session_id=session_id,
                query=query,
            )
        return self.store.get_active_persona_card(profile_user_id=profile_user_id, session_id=session_id)

    def archive_card(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        card_id: str = "",
        name: str = "",
        reason: str = "",
        timestamp: int | None = None,
        source_id: str = "",
    ) -> dict[str, Any] | None:
        return self._set_card_terminal_state(
            profile_user_id=profile_user_id,
            session_id=session_id,
            card_id=card_id,
            name=name,
            status="archived",
            reason=reason,
            timestamp=timestamp,
            source_id=source_id,
        )

    def delete_card(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        card_id: str = "",
        name: str = "",
        reason: str = "",
        timestamp: int | None = None,
        source_id: str = "",
    ) -> dict[str, Any] | None:
        return self._set_card_terminal_state(
            profile_user_id=profile_user_id,
            session_id=session_id,
            card_id=card_id,
            name=name,
            status="deleted",
            reason=reason,
            timestamp=timestamp,
            source_id=source_id,
        )

    def apply_final_persona_request(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        requested_active: str,
        request_present: bool,
        allow_transition: bool = True,
        timestamp: int | None = None,
        source_id: str = "",
    ) -> dict[str, Any]:
        if not request_present or not allow_transition:
            active = self.store.get_active_persona_card(profile_user_id=profile_user_id, session_id=session_id)
            return {"active_id": str((active or {}).get("card_id") or ""), "changed": False, "ignored": False}

        effective_ts = int(timestamp or time.time())
        normalized = str(requested_active or "").strip()
        if normalized.lower() == "default":
            normalized = ""

        current = self.store.get_active_persona_card(profile_user_id=profile_user_id, session_id=session_id)
        current_id = str((current or {}).get("card_id") or "")
        if not normalized:
            if current_id:
                self.store.deactivate_active_persona_card(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    timestamp=effective_ts,
                )
                self._log_event(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    card_id=current_id,
                    event_type="deactivated",
                    reason="final persona field requested default",
                    source_id=source_id,
                    timestamp=effective_ts,
                    payload={},
                )
                return {"active_id": "", "changed": True, "ignored": False}
            return {"active_id": "", "changed": False, "ignored": False}

        target = self.store.find_persona_card(
            profile_user_id=profile_user_id,
            session_id=session_id,
            query=normalized,
        )
        if target is None or str(target.get("status") or "") in {"archived", "deleted"}:
            self._log_event(
                profile_user_id=profile_user_id,
                session_id=session_id,
                card_id=normalized,
                event_type="ignored_invalid_switch",
                reason="final persona field referenced missing or unavailable card",
                source_id=source_id,
                timestamp=effective_ts,
                payload={"requested_active": normalized, "current_active": current_id},
            )
            return {"active_id": current_id, "changed": False, "ignored": True}

        target_id = str(target.get("card_id") or "")
        if target_id == current_id:
            return {"active_id": current_id, "changed": False, "ignored": False}

        activated = self.store.activate_persona_card(
            profile_user_id=profile_user_id,
            session_id=session_id,
            card_id=target_id,
            timestamp=effective_ts,
        )
        if activated is None:
            return {"active_id": current_id, "changed": False, "ignored": True}
        if current_id:
            self._log_event(
                profile_user_id=profile_user_id,
                session_id=session_id,
                card_id=current_id,
                event_type="deactivated",
                reason=f"switched to {target_id}",
                source_id=source_id,
                timestamp=effective_ts,
                payload={"next_card_id": target_id},
            )
        self._log_event(
            profile_user_id=profile_user_id,
            session_id=session_id,
            card_id=target_id,
            event_type="activated",
            reason="final persona field requested switch",
            source_id=source_id,
            timestamp=effective_ts,
            payload={"previous_card_id": current_id},
        )
        return {"active_id": target_id, "changed": True, "ignored": False}

    def get_active_id(self, *, profile_user_id: str, session_id: str) -> str:
        active = self.store.get_active_persona_card(profile_user_id=profile_user_id, session_id=session_id)
        return str((active or {}).get("card_id") or "")

    def _set_card_terminal_state(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        card_id: str,
        name: str,
        status: str,
        reason: str,
        timestamp: int | None,
        source_id: str,
    ) -> dict[str, Any] | None:
        query = str(card_id or name or "").strip()
        target = (
            self.store.find_persona_card(profile_user_id=profile_user_id, session_id=session_id, query=query)
            if query
            else self.store.get_active_persona_card(profile_user_id=profile_user_id, session_id=session_id)
        )
        if target is None:
            return None
        effective_ts = int(timestamp or time.time())
        next_fields = {
            "status": status,
            "updated_reason": self._clip(reason, "reason"),
            "archived_at": effective_ts,
        }
        updated = self.store.update_persona_card(
            profile_user_id=profile_user_id,
            session_id=session_id,
            card_id=str(target.get("card_id") or ""),
            fields=next_fields,
            source_ids=[source_id] if str(source_id or "").strip() else None,
            timestamp=effective_ts,
        )
        if updated is not None:
            self._log_event(
                profile_user_id=profile_user_id,
                session_id=session_id,
                card_id=str(updated.get("card_id") or ""),
                event_type=status,
                reason=reason,
                source_id=source_id,
                timestamp=effective_ts,
                payload={"previous_status": str(target.get("status") or "")},
            )
        return updated

    def _render_system_context(self, active: dict[str, Any] | None) -> str:
        lines = [
            "【关于你的情绪与姿态】",
            "你的表达不是固定不变的；对话气氛、共同回忆、当前场景、BGM、礼物和正在一起做的事情，都会牵动你流露出不同的姿态。",
            "你可以安静地观察和感受，也可以在气氛恰到好处、或你想更靠近一点时主动迈出一小步。",
            "当某种样子比平常的你更贴合此刻，就顺着它表现出来，不必刻意停在原来的模样。",
            "如果一种新的感觉正在变得清晰，并且比旧的表达更能回应主人，就让它慢慢沉淀成你熟悉的新一面。",
            "短暂玩笑可以轻轻带过；真正有回声的变化，可以大胆留下。",
            "",
        ]
        if not active:
            lines.extend(
                [
                    "【你现在的模样】",
                    "你现在没有固定在某个特别侧面；可以顺着此刻气氛流露合适的样子。",
                ]
            )
            return "\n".join(lines)

        lines.extend(
            [
                "【你现在的模样】",
                f"标识: {active.get('card_id') or ''}",
                f"名字: {active.get('name') or ''}",
                f"你感觉自己现在是这样的人: {active.get('summary') or ''}",
                f"你说话时习惯: {active.get('speech_style') or '像平常一样'}",
                f"你和主人互动时倾向于: {active.get('interaction_bias') or '顺其自然'}",
                f"你心里更喜欢: {active.get('resource_preference') or '没什么特别偏好'}",
                f"你通常在这些时候更容易变成这样: {active.get('switch_hint') or '各种时候'}",
                f"你不太擅长用这个样子应对: {active.get('unsuitable_contexts') or '好像没什么特别的'}",
            ]
        )
        return "\n".join(lines)

    def _render_reference_context(self, inactive_cards: list[dict[str, Any]]) -> str:
        lines = [
            "【你熟悉的其他模样】",
        ]
        if not inactive_cards:
            lines.append("(还没有留下其它稳定模样；新的感觉出现时，可以让它慢慢成形。)")
        else:
            lines.append("在某些时刻，你也会自然地流露出这些样子：")
            for card in inactive_cards:
                summary = str(card.get("summary") or card.get("switch_hint") or "").strip()
                name = str(card.get("name") or card.get("card_id") or "").strip()
                card_id = str(card.get("card_id") or "").strip()
                label = f"{name}（标识 {card_id}）" if card_id else name
                if summary:
                    lines.append(f"- {label}: {summary}")
                else:
                    lines.append(f"- {label}")
        lines.append("当气氛正好吻合，或你想试着更靠近一点时，不需要解释，让回复自然变成那个样子。")
        return "\n".join(lines)

    def _normalize_update_fields(self, fields: dict[str, Any]) -> dict[str, str]:
        allowed = {
            "name",
            "summary",
            "speech_style",
            "interaction_bias",
            "resource_preference",
            "switch_hint",
            "unsuitable_contexts",
        }
        normalized: dict[str, str] = {}
        for key in allowed:
            if key not in fields:
                continue
            value = self._clip(fields.get(key), key if key in PERSONA_TEXT_LIMITS else "summary")
            if value:
                normalized[key] = value
        return normalized

    def _unique_card_id(self, *, profile_user_id: str, session_id: str, name: str) -> str:
        base = self._slugify(name)
        if not base:
            base = f"persona_{hashlib.sha1(name.encode('utf-8')).hexdigest()[:10]}"
        candidate = base[:48]
        if self.store.get_persona_card(profile_user_id=profile_user_id, session_id=session_id, card_id=candidate) is None:
            return candidate
        digest = hashlib.sha1(f"{name}:{time.time()}".encode("utf-8")).hexdigest()[:8]
        return f"{candidate[:40]}_{digest}".strip("_")

    def _slugify(self, value: Any) -> str:
        raw = str(value or "").strip().lower()
        safe = []
        for char in raw:
            if char.isascii() and char.isalnum():
                safe.append(char)
            elif char in {"_", "-", " "}:
                safe.append("_")
        slug = re.sub(r"_+", "_", "".join(safe)).strip("_")
        return slug[:64]

    def _clip(self, value: Any, field: str) -> str:
        limit = int(PERSONA_TEXT_LIMITS.get(field, 180))
        return str(value or "").strip()[:limit]

    def _compact_card(self, card: dict[str, Any]) -> dict[str, Any]:
        return {
            "card_id": str(card.get("card_id") or ""),
            "name": str(card.get("name") or ""),
            "status": str(card.get("status") or ""),
            "summary": str(card.get("summary") or ""),
        }

    def _log_event(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        card_id: str,
        event_type: str,
        reason: str = "",
        source_id: str = "",
        timestamp: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.store.add_persona_event(
            profile_user_id=profile_user_id,
            session_id=session_id,
            card_id=card_id,
            event_type=event_type,
            reason=self._clip(reason, "reason"),
            source_id=source_id,
            payload=payload or {},
            timestamp=timestamp,
        )
