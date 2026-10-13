"""Matching for AttachmentInboxService."""

from __future__ import annotations

from ..owner_private_session import private_asset_scope

from typing import Any
from .definitions import ACTIVE_ATTACHMENT_STATUSES


class AttachmentMatchingMixin:
    @private_asset_scope
    def resolve_attachment(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str = "",
        kind: str = "any",
    ) -> dict[str, Any] | None:
        return self._resolve_target(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
            kind=kind,
        )

    @private_asset_scope
    def find_attachment_matches(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
        kind: str = "any",
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        items = self.store.find_attachment_inbox_item_matches(
            profile_user_id=profile_user_id,
            session_id=session_id,
            query=str(target or "").strip(),
            kind=kind,
            statuses=list(statuses) if statuses is not None else list(ACTIVE_ATTACHMENT_STATUSES),
            limit=limit,
        )
        return [{key: value for key, value in item.items() if key != "_match_rank"} for item in items]

    @private_asset_scope
    def format_attachment_ambiguity(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
        kind: str = "any",
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> str:
        resolution = self._resolve_target_result(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
            kind=kind,
            statuses=list(statuses) if statuses is not None else list(ACTIVE_ATTACHMENT_STATUSES),
        )
        return self._format_ambiguity_target(
            target=str(target or "").strip(),
            items=list(resolution.get("ambiguous_matches") or []) if isinstance(resolution, dict) else [],
        )

    def _resolve_target(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
        kind: str,
    ) -> dict[str, Any] | None:
        result = self._resolve_target_result(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=target,
            kind=kind,
        )
        item = result.get("item") if isinstance(result, dict) else None
        return item if isinstance(item, dict) else None

    def _resolve_target_result(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
        kind: str,
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> dict[str, Any]:
        normalized_target = str(target or "").strip()
        lowered_target = normalized_target.lower()
        normalized_statuses = list(statuses) if statuses is not None else list(ACTIVE_ATTACHMENT_STATUSES)
        items = self.store.list_attachment_inbox_items(
            profile_user_id=profile_user_id,
            session_id=session_id,
            statuses=normalized_statuses,
            limit=200,
        )
        if not normalized_target or lowered_target in {"current", "latest", "最近", "当前"}:
            kind_filtered = self._filter_items_by_target_kind(items, normalized_target, kind)
            item = self._latest_item(kind_filtered)
            return {"item": item, "ambiguous_matches": []}

        matches = self._find_target_matches_from_items(
            profile_user_id=profile_user_id,
            session_id=session_id,
            target=normalized_target,
            items=items,
            kind=kind,
            statuses=normalized_statuses,
        )
        if not matches:
            return {"item": None, "ambiguous_matches": []}
        if len(matches) > 1:
            return {"item": None, "ambiguous_matches": matches}
        return {"item": matches[0], "ambiguous_matches": []}

    def _load_attachment_items_by_id(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        attachment_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        wanted = {str(item_id or "").strip() for item_id in attachment_ids or [] if str(item_id or "").strip()}
        if not wanted:
            return {}
        items = self.store.list_attachment_inbox_items(
            profile_user_id=profile_user_id,
            session_id=session_id,
            statuses=list(ACTIVE_ATTACHMENT_STATUSES),
            limit=max(80, len(wanted) + 20),
        )
        found = {
            str(item.get("attachment_id") or "").strip(): item
            for item in items
            if str(item.get("attachment_id") or "").strip() in wanted
        }
        for item_id in wanted - found.keys():
            item = self.store.get_attachment_inbox_item(
                profile_user_id=profile_user_id, session_id=session_id, attachment_id=item_id,
            )
            if isinstance(item, dict) and item.get("status") in ACTIVE_ATTACHMENT_STATUSES:
                found[item_id] = item
        return found

    def _resolve_target_from_items(
        self,
        *,
        target: str,
        items: list[dict[str, Any]],
        kind: str,
    ) -> dict[str, Any] | None:
        matches = self._find_target_matches_from_items_local(
            target=target,
            items=items,
            kind=kind,
        )
        return matches[0] if len(matches) == 1 else None

    def _find_target_matches_from_items(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        target: str,
        items: list[dict[str, Any]],
        kind: str,
        statuses: list[str] | tuple[str, ...] | set[str] | None,
    ) -> list[dict[str, Any]]:
        local_matches = self._find_target_matches_from_items_local(
            target=target,
            items=items,
            kind=kind,
        )
        if local_matches:
            return local_matches

        # An exact ID/handle overrides the kind hint just as it does in the
        # local window. The store enforces the owner and active-status scope.
        precise_candidates = self.store.find_attachment_inbox_item_matches(
            profile_user_id=profile_user_id, session_id=session_id,
            query=str(target or "").strip(), kind="any", statuses=statuses, limit=8,
        )
        normalized = str(target or "").strip()
        exact = [item for item in precise_candidates
                 if normalized == str(item.get("attachment_id") or "").strip()
                 or normalized.lower() == str(item.get("attachment_handle") or "").strip().lower()]
        if exact:
            return [{key: value for key, value in exact[0].items() if key != "_match_rank"}]

        raw_matches = self.store.find_attachment_inbox_item_matches(
            profile_user_id=profile_user_id,
            session_id=session_id,
            query=str(target or "").strip(),
            kind=kind,
            statuses=statuses,
            limit=8,
        )
        if not raw_matches:
            return []
        item_by_id = {
            str(item.get("attachment_id") or "").strip(): item
            for item in items
            if str(item.get("attachment_id") or "").strip()
        }
        matches: list[dict[str, Any]] = []
        for match in raw_matches:
            item_id = str(match.get("attachment_id") or "").strip()
            resolved = item_by_id.get(item_id) or match
            if resolved is not None:
                matches.append(dict(resolved, _match_rank=match.get("_match_rank")))
        if not matches:
            return []
        best_rank = int(matches[0].get("_match_rank") or 99)
        top_matches = [
            {key: value for key, value in item.items() if key != "_match_rank"}
            for item in matches
            if int(item.get("_match_rank") or 99) == best_rank
        ]
        return self._dedupe_attachment_items(top_matches)

    def _find_target_matches_from_items_local(
        self,
        *,
        target: str,
        items: list[dict[str, Any]],
        kind: str,
    ) -> list[dict[str, Any]]:
        normalized = str(target or "").strip()
        if not normalized:
            return []
        lowered = normalized.lower()
        kind_filtered = self._filter_items_by_target_kind(items, normalized, kind)

        exact_matches = [
            item
            for item in items
            if normalized == str(item.get("attachment_id") or "").strip()
            or lowered == str(item.get("attachment_handle") or "").strip().lower()
        ]
        if exact_matches:
            return self._dedupe_attachment_items(exact_matches[:1])

        if lowered in {"current", "latest", "最近", "当前", "最后一张", "最后一个"}:
            latest = self._latest_item(kind_filtered)
            return [latest] if latest is not None else []
        if lowered in {"oldest", "first", "最早", "第一张", "第一个"}:
            oldest = self._oldest_item(kind_filtered)
            return [oldest] if oldest is not None else []

        reverse_index = self._parse_reverse_ordinal(normalized)
        if reverse_index is not None:
            ordered = self._sort_by_sequence(kind_filtered)
            if 1 <= reverse_index <= len(ordered):
                return [ordered[-reverse_index]]
            return []

        sequence_no = self._parse_sequence_no(normalized) if self._looks_like_sequence_reference(normalized) else None
        if sequence_no is not None:
            sequence_matches = [item for item in kind_filtered if int(item.get("sequence_no") or 0) == sequence_no]
            return self._dedupe_attachment_items(sequence_matches)
        return []
