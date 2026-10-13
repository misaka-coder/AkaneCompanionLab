"""Scoped persistent focus state for workspace files."""

from __future__ import annotations

from typing import Any

from .definitions import WorkspacePathError


class WorkspaceFocusMixin:
    def focus_items(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        targets: list[str],
        action: str,
        recursive: bool = True,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"add", "set", "remove"}:
            return {
                "status": "invalid_action",
                "reason": "action must be add, set, or remove",
                "items": [],
            }

        requested_targets = self._normalize_target_list(targets)
        resolved_uris: list[str] = []
        item_results: list[dict[str, Any]] = []
        existing_states = self._state_map(
            profile_user_id=profile_user_id,
            session_id=session_id,
        )
        for requested in requested_targets:
            try:
                path, uri = self.resolve_uri(requested)
            except WorkspacePathError as exc:
                item_results.append(
                    {
                        "requested": "(invalid workspace path)",
                        "status": "denied",
                        "reason": str(exc),
                    }
                )
                continue
            if not path.exists():
                if normalized_action == "remove":
                    matched_uris = [
                        state_uri
                        for state_uri in existing_states
                        if state_uri == uri or state_uri.startswith(f"{uri.rstrip('/')}/")
                    ]
                    for matched_uri in matched_uris:
                        if matched_uri not in resolved_uris:
                            resolved_uris.append(matched_uri)
                    if matched_uris:
                        item_results.append(
                            {
                                "uri": uri,
                                "status": "resolved_missing",
                                "state_count": len(matched_uris),
                            }
                        )
                    else:
                        item_results.append({"uri": uri, "status": "missing"})
                else:
                    item_results.append({"uri": uri, "status": "missing"})
                continue
            if path.is_file():
                if uri not in resolved_uris:
                    resolved_uris.append(uri)
                item_results.append({"uri": uri, "status": "resolved"})
                continue
            if not path.is_dir():
                item_results.append({"uri": uri, "status": "unsupported"})
                continue

            candidates = self._walk_directory(path, depth=64 if recursive else 1)
            directory_files = [candidate for candidate in candidates if candidate.is_file()]
            directory_uris = [self.to_uri(candidate) for candidate in directory_files]
            if normalized_action == "remove":
                directory_prefix = f"{uri.rstrip('/')}/"
                directory_uris.extend(
                    state_uri for state_uri in existing_states if state_uri.startswith(directory_prefix)
                )
            for candidate_uri in directory_uris:
                if candidate_uri not in resolved_uris:
                    resolved_uris.append(candidate_uri)
            item_results.append(
                {
                    "uri": uri,
                    "status": "resolved",
                    "file_count": len(directory_files),
                }
            )

        # An explicit empty selection (or an existing empty directory) can
        # clear focus. A failed replacement must preserve the previous state.
        can_replace = normalized_action == 'set' and (
            not requested_targets or any(item.get('status') == 'resolved' for item in item_results)
        )
        operation_applied = can_replace or bool(resolved_uris)
        if operation_applied:
            self.store.update_workspace_file_focus(
                profile_user_id=profile_user_id,
                session_id=session_id,
                workspace_uris=resolved_uris,
                action=normalized_action,
                timestamp=timestamp,
            )

        focused_states = self.store.list_workspace_file_states(
            profile_user_id=profile_user_id,
            session_id=session_id,
            focused_only=True,
        )
        return {
            "status": "ok" if operation_applied else "failed",
            "reason": "" if operation_applied else "no workspace items were resolved",
            "action": normalized_action,
            "affected": resolved_uris,
            "items": item_results,
            "focused": [str(item.get("workspace_uri") or "") for item in focused_states],
        }

    def _state_map(self, *, profile_user_id: str, session_id: str) -> dict[str, dict[str, Any]]:
        states = self.store.list_workspace_file_states(
            profile_user_id=profile_user_id,
            session_id=session_id,
        )
        return {
            str(item.get("workspace_uri") or ""): item
            for item in states
            if str(item.get("workspace_uri") or "").strip()
        }
