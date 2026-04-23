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

    def build_prompt_context(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        task_limit: int = 2,
        step_limit: int = 6,
        artifact_limit: int = 8,
        event_limit: int = 3,
    ) -> str:
        """Render active task state as compact working context for Akane."""

        tasks = self.list_tasks(
            profile_user_id=profile_user_id,
            session_id=session_id,
            statuses=["running", "waiting_user", "queued"],
            limit=max(1, int(task_limit or 2)),
        )
        if not tasks:
            return ""

        lines = [
            "【当前任务工作区】",
            "这里记录的是当前会话里还没收尾的多步任务；它用于接续工作，不等同于长期记忆。",
            "重要：任务工作区只是白板，不代表任务已经执行。用户说“开始/继续/直接做”时，请调用真正的处理工具推进，不要只口头承诺或汇报计划。",
        ]
        for index, task in enumerate(tasks, start=1):
            task_id = str(task.get("task_id") or "").strip()
            status = str(task.get("status") or "").strip() or "running"
            goal = str(task.get("normalized_goal") or "").strip()
            if not goal:
                raw_request = task.get("raw_request") if isinstance(task.get("raw_request"), dict) else {}
                goal = str(raw_request.get("text") or "").strip()
            lines.append(f"\n任务 {index}: {task_id or '(无 id)'}")
            lines.append(f"- 状态: {status}")
            if goal:
                lines.append(f"- 目标: {goal[:240]}")

            steps = [step for step in list(task.get("steps") or []) if isinstance(step, dict)]
            if steps:
                rendered_steps: list[str] = []
                for step in steps[: max(1, int(step_limit or 6))]:
                    title = str(step.get("title") or step.get("name") or step.get("id") or "未命名步骤").strip()
                    step_status = str(step.get("status") or "queued").strip()
                    note = str(step.get("note") or "").strip()
                    rendered = f"{title}({step_status})"
                    if note:
                        rendered += f": {note[:80]}"
                    rendered_steps.append(rendered)
                if len(steps) > len(rendered_steps):
                    rendered_steps.append(f"...还有 {len(steps) - len(rendered_steps)} 步")
                lines.append("- 步骤: " + "；".join(rendered_steps))

            artifacts = [artifact for artifact in list(task.get("artifacts") or []) if isinstance(artifact, dict)]
            if artifacts:
                rendered_artifacts: list[str] = []
                for artifact in artifacts[: max(1, int(artifact_limit or 8))]:
                    artifact_id = str(artifact.get("id") or artifact.get("generated_handle") or artifact.get("attachment_handle") or "").strip()
                    title = str(artifact.get("title") or "").strip()
                    kind = str(artifact.get("kind") or "").strip()
                    label = artifact_id or title or "未命名产物"
                    suffix_parts = [part for part in [kind, title if title and title != label else ""] if part]
                    rendered_artifacts.append(label + (f"({' / '.join(suffix_parts)})" if suffix_parts else ""))
                if len(artifacts) > len(rendered_artifacts):
                    rendered_artifacts.append(f"...还有 {len(artifacts) - len(rendered_artifacts)} 个")
                lines.append("- 可用产物: " + "；".join(rendered_artifacts))

            pending_question = task.get("pending_question") if isinstance(task.get("pending_question"), dict) else {}
            question = str(pending_question.get("text") or pending_question.get("question") or "").strip()
            if question:
                lines.append(f"- 等待用户确认: {question[:200]}")

            recent_events = self.list_events(task_id=task_id, limit=50)[-max(0, int(event_limit or 3)) :] if task_id else []
            rendered_events: list[str] = []
            for event in recent_events:
                if not isinstance(event, dict):
                    continue
                event_type = str(event.get("event_type") or "").strip()
                message = str(event.get("message") or "").strip()
                if event_type or message:
                    rendered_events.append(f"{event_type or 'event'}: {message[:120]}")
            if rendered_events:
                lines.append("- 最近事件: " + "；".join(rendered_events))

        lines.append("\n如果任务已经完成或用户确认不需要继续，请使用 manage_task_workspace 更新、完成或清理工作区；如果任务还没实际产生产物，请先调用对应处理工具。")
        return "\n".join(lines)

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
