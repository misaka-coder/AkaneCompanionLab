from __future__ import annotations

import time
from typing import Any

from .store import MemoryStore


class TaskWorkspaceService:
    """A lightweight task ledger for future multi-step/background work.

    V1 deliberately records intent, steps, artifacts and events only. It does
    not execute tools or start child agents; those layers can attach to this
    stable workspace later.
    """

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def create_task(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        raw_request_text: str,
        source_message_id: str = "",
        normalized_goal: str = "",
        success_criteria: list[Any] | None = None,
        constraints: list[Any] | None = None,
        steps: list[dict[str, Any]] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        owner: str = "Akane",
        status: str = "queued",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        effective_ts = int(timestamp or time.time())
        raw_request = {
            "text": str(raw_request_text or "").strip(),
            "source_message_id": str(source_message_id or "").strip(),
        }
        task = self.store.add_task_workspace(
            profile_user_id=profile_user_id,
            session_id=session_id,
            owner=owner,
            status=status,
            raw_request=raw_request,
            normalized_goal=normalized_goal,
            success_criteria=success_criteria or [],
            constraints=constraints or [],
            steps=steps or [],
            artifacts=artifacts or [],
            metadata=metadata or {},
            timestamp=effective_ts,
        )
        self.store.append_task_workspace_event(
            task_id=str(task["task_id"]),
            profile_user_id=profile_user_id,
            session_id=session_id,
            event_type="task_created",
            from_actor=owner,
            priority="normal",
            message=normalized_goal or raw_request["text"],
            payload={
                "source_message_id": raw_request["source_message_id"],
                "success_criteria": success_criteria or [],
            },
            status="handled",
            timestamp=effective_ts,
        )
        return task

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self.store.get_task_workspace(task_id)

    def list_tasks(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
        statuses: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return self.store.list_task_workspaces(
            profile_user_id=profile_user_id,
            session_id=session_id,
            statuses=statuses,
            limit=limit,
        )

    def update_task(
        self,
        *,
        task_id: str,
        status: str | None = None,
        normalized_goal: str | None = None,
        steps: list[dict[str, Any]] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        pending_question: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        return self.store.update_task_workspace(
            task_id=task_id,
            status=status,
            normalized_goal=normalized_goal,
            steps=steps,
            artifacts=artifacts,
            pending_question=pending_question,
            metadata=metadata,
            updated_at=timestamp,
        )

    def append_event(
        self,
        *,
        task_id: str,
        event_type: str,
        from_actor: str = "",
        priority: str = "normal",
        requires_user: bool = False,
        message: str = "",
        payload: dict[str, Any] | None = None,
        status: str = "pending",
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        task = self.store.get_task_workspace(task_id)
        if not task:
            raise ValueError(f"Task workspace not found: {task_id}")
        return self.store.append_task_workspace_event(
            task_id=task_id,
            profile_user_id=str(task["profile_user_id"]),
            session_id=str(task["session_id"]),
            event_type=event_type,
            from_actor=from_actor,
            priority=priority,
            requires_user=requires_user,
            message=message,
            payload=payload or {},
            status=status,
            timestamp=timestamp,
        )

    def list_events(
        self,
        *,
        task_id: str,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return self.store.list_task_workspace_events(
            task_id=task_id,
            status=status,
            limit=limit,
        )

    def mark_event_handled(
        self,
        *,
        event_id: str,
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        return self.store.mark_task_workspace_event_handled(
            event_id=event_id,
            status="handled",
            handled_at=timestamp,
        )

    def complete_task(
        self,
        *,
        task_id: str,
        artifacts: list[dict[str, Any]] | None = None,
        message: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        effective_ts = int(timestamp or time.time())
        updated = self.store.update_task_workspace(
            task_id=task_id,
            status="completed",
            artifacts=artifacts,
            completed_at=effective_ts,
            updated_at=effective_ts,
        )
        if updated:
            self.append_event(
                task_id=task_id,
                event_type="task_completed",
                from_actor="system",
                message=message,
                payload={"artifacts": artifacts or []},
                status="handled",
                timestamp=effective_ts,
            )
        return updated

    def cleanup_task(
        self,
        *,
        task_id: str,
        mode: str = "clean_scratch",
        reason: str = "",
        timestamp: int | None = None,
    ) -> dict[str, Any] | None:
        task = self.store.get_task_workspace(task_id)
        if not task:
            return None
        effective_ts = int(timestamp or time.time())
        metadata = dict(task.get("metadata") or {})
        metadata["cleanup"] = {
            "mode": str(mode or "clean_scratch").strip() or "clean_scratch",
            "reason": str(reason or "").strip(),
            "cleaned_at": effective_ts,
        }
        updated = self.store.update_task_workspace(
            task_id=task_id,
            status="cleaned",
            metadata=metadata,
            cleaned_at=effective_ts,
            updated_at=effective_ts,
        )
        self.store.append_task_workspace_event(
            task_id=task_id,
            profile_user_id=str(task["profile_user_id"]),
            session_id=str(task["session_id"]),
            event_type="task_cleaned",
            from_actor="Akane",
            message=reason,
            payload={"mode": metadata["cleanup"]["mode"]},
            status="handled",
            timestamp=effective_ts,
        )
        return updated
