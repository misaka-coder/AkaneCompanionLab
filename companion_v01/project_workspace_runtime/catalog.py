"""Project catalog, conversation selections, ownership, and public records."""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any, Mapping

from .definitions import (
    ProjectWorkspaceError, ProjectWorkspaceScope, _PROJECT_ID_RE,
    _ROOT_KIND_MANAGED, _ROOT_KIND_HOST_BOUND,
)


class ProjectCatalogMixin:
    def scope_for(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        client_mode: str,
        actor_stable_id: str = "",
        actor_profile_user_id: str = "",
    ) -> ProjectWorkspaceScope:
        profile = str(profile_user_id or "").strip()
        session = str(session_id or "").strip()
        actor = str(actor_stable_id or "").strip()
        actor_profile = str(actor_profile_user_id or "").strip()
        mode = str(client_mode or "").strip().lower()
        if not profile:
            raise ProjectWorkspaceError("workspace_owner_required")
        from ..owner_private_session import current_private_store_scope
        shared = current_private_store_scope(self.store)
        if shared and (profile, session) == (shared.profile, shared.session):
            return ProjectWorkspaceScope("qq_user", "master", "", "master")
        if mode.startswith("qq"):
            if session.startswith("qq_group_shared_"):
                if not actor:
                    raise ProjectWorkspaceError("group_actor_required")
                if not actor_profile:
                    raise ProjectWorkspaceError("group_actor_profile_required")
                return ProjectWorkspaceScope("qq_user", actor_profile, "", session)
            return ProjectWorkspaceScope("qq_user", actor_profile or profile, "", session)
        if session.startswith("qq_group_shared_"):
            if not actor:
                raise ProjectWorkspaceError("group_actor_required")
            return ProjectWorkspaceScope("group", session, actor)
        if mode == "desktop_pet":
            return ProjectWorkspaceScope("desktop", profile, "")
        return ProjectWorkspaceScope("private", profile, "")

    def create(self, *, scope: ProjectWorkspaceScope, display_name: str) -> dict[str, Any]:
        self._prepare_scope(scope)
        name = self._display_name(display_name)
        workspace_id = "proj_" + uuid.uuid4().hex
        root_relpath = f"Projects/{workspace_id}"
        final_root = self._root_from_relpath(root_relpath)
        temp_root = self.projects_root / f".{workspace_id}.{uuid.uuid4().hex}.tmp"
        with self._lock:
            temp_root.mkdir(parents=False, exist_ok=False)
            try:
                os.replace(temp_root, final_root)
                record = self.store.add_project_workspace(
                    workspace_id=workspace_id,
                    display_name=name,
                    owner_kind=scope.owner_kind,
                    owner_id=scope.owner_id,
                    actor_scope=scope.actor_scope,
                    root_relpath=root_relpath,
                )
                self.store.set_project_workspace_selection(
                    selection_key=scope.selection_key,
                    workspace_id=workspace_id,
                )
            except Exception:
                if temp_root.exists():
                    temp_root.rmdir()
                if final_root.exists() and not any(final_root.iterdir()):
                    final_root.rmdir()
                raise
        return self._public_record(record, selected=True)

    def bind_existing(
        self,
        *,
        scope: ProjectWorkspaceScope,
        host_directory: str | Path,
        display_name: str = "",
    ) -> dict[str, Any]:
        self._prepare_scope(scope)
        root = self._validate_host_binding_root(host_directory)
        name = self._display_name(display_name or root.name)
        canonical = os.path.normcase(str(root))
        with self._lock:
            existing_records = self.store.list_project_workspaces(
                owner_kind=scope.owner_kind,
                owner_id=scope.owner_id,
                actor_scope=scope.actor_scope,
                include_archived=True,
            )
            for existing in existing_records:
                if str(existing.get("root_kind") or _ROOT_KIND_MANAGED) != _ROOT_KIND_HOST_BOUND:
                    continue
                existing_path = os.path.normcase(str(existing.get("host_root_path") or ""))
                if existing_path != canonical:
                    continue
                if str(existing.get("state") or "") != "active":
                    existing = self.store.update_project_workspace_state(
                        workspace_id=str(existing["workspace_id"]),
                        state="active",
                    ) or existing
                self.store.set_project_workspace_selection(
                    selection_key=scope.selection_key,
                    workspace_id=str(existing["workspace_id"]),
                )
                result = self._public_record(existing, selected=True)
                result["already_bound"] = True
                return result
            workspace_id = "proj_" + uuid.uuid4().hex
            record = self.store.add_project_workspace(
                workspace_id=workspace_id,
                display_name=name,
                owner_kind=scope.owner_kind,
                owner_id=scope.owner_id,
                actor_scope=scope.actor_scope,
                root_relpath="",
                root_kind=_ROOT_KIND_HOST_BOUND,
                host_root_path=str(root),
            )
            self.store.set_project_workspace_selection(
                selection_key=scope.selection_key,
                workspace_id=workspace_id,
            )
        return self._public_record(record, selected=True)

    def list(self, *, scope: ProjectWorkspaceScope, include_archived: bool = False) -> dict[str, Any]:
        self._prepare_scope(scope)
        current = self.current(scope=scope)
        selected_id = str((current or {}).get("workspace_id") or "")
        records = self.store.list_project_workspaces(
            owner_kind=scope.owner_kind,
            owner_id=scope.owner_id,
            actor_scope=scope.actor_scope,
            include_archived=include_archived,
        )
        public_records = [
            self._public_record(item, selected=item["workspace_id"] == selected_id)
            for item in records
        ]
        # A member sees the group's selected project, not the owner's private catalog.
        if current and not any(item["selected"] for item in public_records):
            public_records.insert(0, current)
        return {
            "status": "ok",
            "selected_workspace_id": selected_id,
            "workspaces": public_records,
        }

    def select(self, *, scope: ProjectWorkspaceScope, workspace_id: str) -> dict[str, Any]:
        self._prepare_scope(scope)
        record = self._owned_record(scope=scope, workspace_id=workspace_id, require_active=True)
        self._root_from_record(record, require_exists=True)
        self.store.set_project_workspace_selection(
            selection_key=scope.selection_key,
            workspace_id=str(record["workspace_id"]),
        )
        return self._public_record(record, selected=True)

    def close(self, *, scope: ProjectWorkspaceScope) -> dict[str, Any]:
        """Stop using the selected project as this conversation's default cwd.

        Closing changes only the conversation selection.  It does not archive
        the project, delete files, or change any filesystem permission.
        """

        self._prepare_scope(scope)
        workspace_id = self.store.get_project_workspace_selection(selection_key=scope.selection_key)
        if workspace_id:
            self.store.clear_project_workspace_selection(
                selection_key=scope.selection_key,
                workspace_id=workspace_id,
            )
        return {
            "closed": bool(workspace_id),
            "previous_workspace_id": str(workspace_id or ""),
            "working_directory": str(self.execution_workspace_root),
        }

    def archive(self, *, scope: ProjectWorkspaceScope, workspace_id: str) -> dict[str, Any]:
        self._prepare_scope(scope)
        record = self._owned_record(scope=scope, workspace_id=workspace_id, require_active=False)
        if str(record.get("state") or "") == "archived":
            return self._public_record(record, selected=False)
        updated = self.store.update_project_workspace_state(
            workspace_id=str(record["workspace_id"]),
            state="archived",
        )
        self.store.clear_project_workspace_selection(
            selection_key=scope.selection_key,
            workspace_id=str(record["workspace_id"]),
        )
        return self._public_record(updated or record, selected=False)

    def current(self, *, scope: ProjectWorkspaceScope) -> dict[str, Any] | None:
        self._prepare_scope(scope)
        workspace_id = self.store.get_project_workspace_selection(selection_key=scope.selection_key)
        if not workspace_id:
            return None
        try:
            record = self._owned_record(scope=scope, workspace_id=workspace_id, require_active=True)
            self._root_from_record(record, require_exists=True)
        except ProjectWorkspaceError:
            self.store.clear_project_workspace_selection(
                selection_key=scope.selection_key, workspace_id=workspace_id,
            )
            return None
        return self._public_record(record, selected=True)

    def _prepare_scope(self, scope: ProjectWorkspaceScope) -> None:
        if scope.owner_kind != "qq_user":
            return
        # Early Project Workspace releases stored private QQ catalogs as
        # ``private/<profile>``. That identity is unambiguous and can be moved
        # safely. Group-wide legacy catalogs are deliberately not guessed:
        # their original creator was not stored by the broken runtime path.
        self.store.reassign_project_workspaces(
            from_owner_kind="private",
            from_owner_id=scope.owner_id,
            from_actor_scope="",
            to_owner_kind=scope.owner_kind,
            to_owner_id=scope.owner_id,
            to_actor_scope=scope.actor_scope,
        )

    def _owned_record(
        self,
        *,
        scope: ProjectWorkspaceScope,
        workspace_id: str,
        require_active: bool,
    ) -> dict[str, Any]:
        clean_id = str(workspace_id or "").strip()
        if not _PROJECT_ID_RE.fullmatch(clean_id):
            raise ProjectWorkspaceError("workspace_not_found")
        record = self.store.get_project_workspace(clean_id)
        if record is None:
            raise ProjectWorkspaceError("workspace_not_found")
        owned = (
            str(record.get("owner_kind") or "") == scope.owner_kind
            and str(record.get("owner_id") or "") == scope.owner_id
            and str(record.get("actor_scope") or "") == scope.actor_scope
        )
        shared_selected = bool(scope.shared_group) and (
            self.store.get_project_workspace_selection(selection_key=scope.selection_key) == clean_id
        )
        # This resolves project visibility only. Tool authorization still uses
        # the requesting actor at dispatch; sharing a cwd grants no permission.
        if not owned and not shared_selected:
            raise ProjectWorkspaceError("workspace_not_found")
        if require_active and str(record.get("state") or "") != "active":
            raise ProjectWorkspaceError("workspace_archived")
        return record

    @staticmethod
    def _display_name(value: str) -> str:
        name = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
        name = re.sub(r"\s+", " ", name).strip()
        if not name:
            raise ProjectWorkspaceError("display_name_required")
        if len(name) > 80:
            raise ProjectWorkspaceError("display_name_too_long", max_chars=80, actual_chars=len(name))
        return name

    def _public_record(self, record: Mapping[str, Any], *, selected: bool) -> dict[str, Any]:
        root_kind = str(record.get("root_kind") or _ROOT_KIND_MANAGED)
        root: Path | None = None
        try:
            root = self._root_from_record(record, require_exists=True)
            available = root.is_dir()
        except ProjectWorkspaceError:
            available = False
        workspace_id = str(record.get("workspace_id") or "")
        is_current = bool(selected and available and str(record.get("state") or "") == "active")
        return {
            "workspace_id": workspace_id,
            "display_name": str(record.get("display_name") or ""),
            "owner_kind": str(record.get("owner_kind") or ""),
            "state": str(record.get("state") or ""),
            "root_kind": root_kind,
            "available": available,
            "selected": bool(selected and available),
            "alias": "alias:project" if is_current else "",
            "working_directory": str(root) if is_current and root is not None else "",
            "created_at": int(record.get("created_at") or 0),
            "updated_at": int(record.get("updated_at") or 0),
        }
