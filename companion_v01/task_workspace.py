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
        owner: str = "frontstage",
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
        """Render active task state as compact working context for the frontstage assistant."""

        tasks = self._list_prompt_tasks(
            profile_user_id=profile_user_id,
            session_id=session_id,
            active_limit=max(1, int(task_limit or 2)),
            handoff_limit=2,
        )
        if not tasks:
            return ""

        lines = [
            "【当前任务工作区】",
            "这里记录的是当前会话里还没收尾的多步任务，或刚完成但还需要你接手汇报/交付的后台任务；它用于接续工作，不等同于长期记忆。",
            "重要：任务工作区只是白板，不代表任务已经执行。用户说“开始/继续/直接做”时，请调用真正的处理工具推进，不要只口头承诺或汇报计划。",
            "如果看到后台工坊交接状态，请按交接摘要和建议接手自然继续：完成就确认/交付，阻塞就问清问题，部分完成就说明现有成果并继续推进。",
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
            metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
            workshop = metadata.get("workshop") if isinstance(metadata.get("workshop"), dict) else {}
            assigned_agent = str(workshop.get("assigned_agent") or "").strip()
            workshop_status = str(workshop.get("status") or "").strip()
            workshop_bits = []
            if assigned_agent:
                workshop_bits.append(assigned_agent)
            if workshop_status:
                workshop_bits.append(workshop_status)
            if workshop_bits:
                lines.append("- 后台工坊: " + " / ".join(workshop_bits))
            brief = str(workshop.get("brief") or "").strip()
            if brief:
                lines.append(f"- 工坊说明: {brief[:200]}")

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

            handoff = self.get_task_handoff(task)
            if handoff:
                lines.extend(self.render_handoff_lines(handoff, bullet="- "))

            frontstage_lines = self.render_frontstage_status_lines(task, handoff=handoff, bullet="- ")
            if frontstage_lines:
                lines.extend(frontstage_lines)

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

    def _list_prompt_tasks(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        active_limit: int,
        handoff_limit: int = 2,
    ) -> list[dict[str, Any]]:
        active_tasks = self.list_tasks(
            profile_user_id=profile_user_id,
            session_id=session_id,
            statuses=["running", "waiting_user", "queued"],
            limit=max(1, int(active_limit or 2)),
        )
        seen_ids = {
            str(task.get("task_id") or "").strip()
            for task in active_tasks
            if str(task.get("task_id") or "").strip()
        }
        if handoff_limit <= 0:
            return active_tasks

        pending_events = self.store.list_task_workspace_events(
            profile_user_id=profile_user_id,
            session_id=session_id,
            status="pending",
            limit=max(8, int(handoff_limit or 2) * 8),
        )
        handoff_tasks: list[dict[str, Any]] = []
        for event in reversed(pending_events):
            task_id = str(event.get("task_id") or "").strip()
            if not task_id or task_id in seen_ids:
                continue
            task = self.get_task(task_id)
            if task is None:
                continue
            status = str(task.get("status") or "").strip().lower()
            if status in {"cleaned", "canceled"}:
                continue
            seen_ids.add(task_id)
            handoff_tasks.append(task)
            if len(handoff_tasks) >= max(1, int(handoff_limit or 2)):
                break
        return [*active_tasks, *handoff_tasks]

    def get_task_handoff(self, task: dict[str, Any]) -> dict[str, Any]:
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        workshop = metadata.get("workshop") if isinstance(metadata.get("workshop"), dict) else {}
        handoff = workshop.get("handoff") if isinstance(workshop.get("handoff"), dict) else {}
        if handoff:
            return dict(handoff)

        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            return {}
        events = self.list_events(task_id=task_id, status="pending", limit=20)
        if not events:
            events = self.list_events(task_id=task_id, limit=20)
        for event in reversed(events):
            if not isinstance(event, dict):
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            event_handoff = payload.get("handoff") if isinstance(payload.get("handoff"), dict) else {}
            if event_handoff:
                return dict(event_handoff)
        return {}

    def list_status_summaries(
        self,
        *,
        profile_user_id: str,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return compact read-only task summaries for passive frontends."""

        max_items = max(1, min(50, int(limit or 20)))
        tasks = self.list_tasks(
            profile_user_id=profile_user_id,
            session_id=session_id,
            statuses=["queued", "running", "waiting_user", "completed", "failed"],
            limit=max_items,
        )
        task_map: dict[str, dict[str, Any]] = {}
        for task in tasks:
            task_id = str(task.get("task_id") or "").strip()
            if task_id:
                task_map[task_id] = task

        pending_events = self.store.list_task_workspace_events(
            profile_user_id=profile_user_id,
            session_id=session_id,
            status="pending",
            limit=max(20, max_items * 2),
        )
        for event in reversed(pending_events):
            task_id = str(event.get("task_id") or "").strip()
            if not task_id or task_id in task_map:
                continue
            task = self.get_task(task_id)
            if not task:
                continue
            status = str(task.get("status") or "").strip().lower()
            if status in {"cleaned", "canceled"}:
                continue
            task_map[task_id] = task
            if len(task_map) >= max_items * 2:
                break

        sorted_tasks = sorted(
            task_map.values(),
            key=lambda item: int(item.get("updated_at") or item.get("created_at") or 0),
            reverse=True,
        )
        summaries: list[dict[str, Any]] = []
        for task in sorted_tasks:
            summary = self._build_task_status_summary(task)
            if summary:
                summaries.append(summary)
            if len(summaries) >= max_items:
                break
        return summaries

    def _build_task_status_summary(self, task: dict[str, Any]) -> dict[str, Any]:
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            return {}

        handoff = self.get_task_handoff(task)
        state = self._resolve_status_summary_state(task, handoff)
        title = self._build_status_summary_title(task)
        summary = self._build_status_summary_text(task, handoff, state)
        compact_handoff = {
            "state": state,
            "next_action": self._truncate_text(handoff.get("next_action") if isinstance(handoff, dict) else "", 80),
            "artifacts": self._compact_status_summary_artifacts(task, handoff, limit=8),
        }

        return {
            "task_id": task_id,
            "status": state,
            "title": title,
            "summary": summary,
            "handoff": compact_handoff,
            "updated_at": int(task.get("updated_at") or task.get("created_at") or 0),
        }

    def _resolve_status_summary_state(self, task: dict[str, Any], handoff: dict[str, Any]) -> str:
        handoff_status = str((handoff or {}).get("status") or "").strip().lower()
        if handoff_status in {"completed", "blocked", "partial"}:
            return handoff_status

        task_status = str(task.get("status") or "").strip().lower()
        if task_status == "waiting_user":
            return "blocked"
        return task_status or "running"

    def _build_status_summary_title(self, task: dict[str, Any]) -> str:
        title = str(task.get("normalized_goal") or "").strip()
        if not title:
            raw_request = task.get("raw_request") if isinstance(task.get("raw_request"), dict) else {}
            title = str(raw_request.get("text") or "").strip()
        return self._truncate_text(title or str(task.get("task_id") or "后台任务"), 120)

    def _build_status_summary_text(self, task: dict[str, Any], handoff: dict[str, Any], state: str) -> str:
        summary = str((handoff or {}).get("summary") or "").strip()
        if summary:
            return self._truncate_text(summary, 240)

        pending_question = task.get("pending_question") if isinstance(task.get("pending_question"), dict) else {}
        question = str(pending_question.get("text") or pending_question.get("question") or "").strip()
        if question:
            return self._truncate_text(question, 240)

        defaults = {
            "completed": "后台任务已完成。",
            "blocked": "后台任务需要用户确认后才能继续。",
            "partial": "后台任务已完成一部分，仍需要接手判断下一步。",
            "running": "后台任务正在处理。",
            "queued": "后台任务已排队。",
            "failed": "后台任务执行失败。",
        }
        return defaults.get(state, "后台任务状态已更新。")

    def _compact_status_summary_artifacts(
        self,
        task: dict[str, Any],
        handoff: dict[str, Any],
        *,
        limit: int,
    ) -> list[str]:
        raw_items = (handoff or {}).get("artifacts") if isinstance(handoff, dict) else []
        if not isinstance(raw_items, list) or not raw_items:
            raw_items = task.get("artifacts") if isinstance(task.get("artifacts"), list) else []

        rendered: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            if isinstance(item, dict):
                text = str(
                    item.get("id")
                    or item.get("handle")
                    or item.get("generated_handle")
                    or item.get("attachment_handle")
                    or item.get("title")
                    or ""
                ).strip()
            else:
                text = str(item or "").strip()
            if not text:
                continue
            text = self._truncate_text(text, 120)
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            rendered.append(text)
            if len(rendered) >= max(1, int(limit or 1)):
                break
        return rendered

    def _truncate_text(self, value: Any, limit: int) -> str:
        text = str(value or "").strip()
        max_len = max(1, int(limit or 1))
        return text[:max_len]

    def render_handoff_lines(self, handoff: dict[str, Any], *, bullet: str = "- ") -> list[str]:
        if not isinstance(handoff, dict) or not handoff:
            return []
        lines: list[str] = []
        status = str(handoff.get("status") or "").strip()
        if status:
            status_labels = {
                "completed": "完成，等待前台助手交付/确认",
                "blocked": "阻塞，等待用户回答",
                "partial": "部分完成，等待前台助手接手推进",
            }
            lines.append(f"{bullet}交接状态: {status_labels.get(status, status)}")
        summary = str(handoff.get("summary") or "").strip()
        if summary:
            lines.append(f"{bullet}交接摘要: {summary[:240]}")
        completed = self._render_handoff_text_items(handoff.get("completed_steps"), limit=6)
        if completed:
            lines.append(f"{bullet}已完成: " + "；".join(completed))
        active = self._render_handoff_text_items(handoff.get("active_steps"), limit=6)
        if active:
            lines.append(f"{bullet}还在推进: " + "；".join(active))
        blocked = self._render_handoff_text_items(handoff.get("blocked_steps"), limit=4)
        if blocked:
            lines.append(f"{bullet}卡住位置: " + "；".join(blocked))
        remaining = self._render_handoff_text_items(handoff.get("remaining_steps"), limit=6)
        if remaining:
            lines.append(f"{bullet}仍需处理: " + "；".join(remaining))
        artifacts = self._render_handoff_artifacts(handoff.get("artifacts"), limit=8)
        if artifacts:
            lines.append(f"{bullet}交接候选: " + "；".join(artifacts))
        question = str(handoff.get("user_question") or "").strip()
        if question:
            lines.append(f"{bullet}需要问用户: {question[:220]}")
        instruction = str(handoff.get("akane_instruction") or "").strip()
        if instruction:
            lines.append(f"{bullet}建议接手: {instruction[:280]}")
        return lines

    def _render_handoff_text_items(self, value: Any, *, limit: int) -> list[str]:
        raw_items = value if isinstance(value, list) else []
        rendered: list[str] = []
        for item in raw_items:
            text = str(item or "").strip()
            if not text:
                continue
            rendered.append(text[:160])
            if len(rendered) >= max(1, int(limit or 1)):
                break
        return rendered

    def _render_handoff_artifacts(self, value: Any, *, limit: int) -> list[str]:
        raw_items = value if isinstance(value, list) else []
        rendered: list[str] = []
        for item in raw_items:
            if isinstance(item, dict):
                artifact_id = str(item.get("id") or item.get("handle") or "").strip()
                title = str(item.get("title") or "").strip()
                kind = str(item.get("kind") or "").strip()
                stem_role = str(item.get("stem_role") or "").strip()
                label = artifact_id or title
                if not label:
                    continue
                suffix = " / ".join(part for part in [kind, stem_role, title if title and title != label else ""] if part)
                rendered.append(label + (f"({suffix})" if suffix else ""))
            else:
                text = str(item or "").strip()
                if text:
                    rendered.append(text[:160])
            if len(rendered) >= max(1, int(limit or 1)):
                break
        return rendered

    def render_frontstage_status_lines(
        self,
        task: dict[str, Any],
        *,
        handoff: dict[str, Any] | None = None,
        bullet: str = "- ",
    ) -> list[str]:
        """Render user-facing guidance for the frontstage assistant without replacing task facts."""

        if not isinstance(task, dict) or not task:
            return []
        effective_handoff = handoff if isinstance(handoff, dict) else self.get_task_handoff(task)
        pending_question = task.get("pending_question") if isinstance(task.get("pending_question"), dict) else {}
        question = str(pending_question.get("text") or pending_question.get("question") or "").strip()
        status = str(task.get("status") or "").strip().lower()
        handoff_status = str((effective_handoff or {}).get("status") or "").strip().lower()
        next_action = str((effective_handoff or {}).get("next_action") or "").strip().lower()
        artifacts = [artifact for artifact in list(task.get("artifacts") or []) if isinstance(artifact, dict)]
        steps = [step for step in list(task.get("steps") or []) if isinstance(step, dict)]

        if question or status == "waiting_user" or handoff_status == "blocked":
            prompt_question = question or str((effective_handoff or {}).get("user_question") or "").strip()
            lines = [f"{bullet}前台状态: 等待用户确认"]
            if prompt_question:
                lines.append(f"{bullet}前台回应: 直接问用户：{prompt_question[:220]} 不要复述后台日志或工具错误。")
            else:
                lines.append(f"{bullet}前台回应: 说明后台需要用户补充信息，用用户能回答的话问清楚；不要复述技术日志。")
            return lines

        if status == "completed" or handoff_status == "completed":
            handoff_artifacts = (effective_handoff or {}).get("artifacts") if isinstance(effective_handoff, dict) else []
            artifact_labels = self._render_handoff_artifacts(handoff_artifacts, limit=4)
            if not artifact_labels:
                artifact_labels = self._render_task_artifact_labels(artifacts, limit=4)
            lines = [f"{bullet}前台状态: 后台已完成，等待前台助手确认/交付"]
            if next_action == "send_to_user":
                detail = "，".join(artifact_labels) if artifact_labels else "结果"
                lines.append(f"{bullet}前台回应: 如果本轮用户已明确要发送结果，可以简短说明已经做好，并用 send_file 精确发送 {detail}；否则先确认要不要发送这些结果。")
            elif next_action == "report_only" and not artifact_labels:
                lines.append(f"{bullet}前台回应: 自然说明已经处理完；如果用户还需要文件，再根据资源区或生成区继续处理。")
            else:
                detail = "，".join(artifact_labels) if artifact_labels else "结果"
                lines.append(f"{bullet}前台回应: 自然说明已经做好 {detail}；如果用户没明确要发文件，先问是否发送以及要发送哪一份。")
            return lines

        if handoff_status == "partial":
            completed = self._render_handoff_text_items((effective_handoff or {}).get("completed_steps"), limit=4)
            active = self._render_handoff_text_items((effective_handoff or {}).get("active_steps"), limit=4)
            remaining = self._render_handoff_text_items((effective_handoff or {}).get("remaining_steps"), limit=4)
            pieces = []
            if completed:
                pieces.append("已完成 " + "、".join(completed))
            if active:
                pieces.append("正在 " + "、".join(active))
            if remaining:
                pieces.append("还剩 " + "、".join(remaining))
            summary = "；".join(pieces) if pieces else "已有一部分结果，后续还没收尾"
            return [
                f"{bullet}前台状态: 后台部分完成，仍需接手推进",
                f"{bullet}前台回应: 用户问进度时简短说明：{summary}；可询问是否先发送现有成果，或继续后台处理。",
            ]

        if status in {"queued", "running"}:
            active_steps = self._render_steps_by_status(steps, {"running"}, limit=3)
            queued_steps = self._render_steps_by_status(steps, {"queued"}, limit=3)
            done_steps = self._render_steps_by_status(steps, {"done", "completed"}, limit=3)
            current = "、".join(active_steps or queued_steps)
            if not current:
                current = "后台任务" if status == "running" else "等待后台开始"
            details = []
            if done_steps:
                details.append("已完成 " + "、".join(done_steps))
            if active_steps:
                details.append("正在 " + "、".join(active_steps))
            elif queued_steps:
                details.append("排队/待处理 " + "、".join(queued_steps))
            detail_text = "；".join(details) or current
            front_status = "后台正在处理" if status == "running" or active_steps else "后台已排队"
            return [
                f"{bullet}前台状态: {front_status}",
                f"{bullet}前台回应: 用户问“好了没/到哪了”时，只说当前进度：{detail_text}；不要编造完成，也不要长篇解释内部 task_id。",
            ]

        return []

    def _render_steps_by_status(self, steps: list[dict[str, Any]], statuses: set[str], *, limit: int) -> list[str]:
        rendered: list[str] = []
        for step in steps:
            status = str(step.get("status") or "").strip().lower()
            if status not in statuses:
                continue
            title = str(step.get("title") or step.get("name") or step.get("id") or "").strip()
            if title:
                rendered.append(title[:120])
            if len(rendered) >= max(1, int(limit or 1)):
                break
        return rendered

    def _render_task_artifact_labels(self, artifacts: list[dict[str, Any]], *, limit: int) -> list[str]:
        labels: list[str] = []
        for artifact in artifacts:
            artifact_id = str(artifact.get("id") or artifact.get("generated_handle") or artifact.get("attachment_handle") or "").strip()
            title = str(artifact.get("title") or "").strip()
            label = artifact_id or title
            if not label:
                continue
            if title and title != label:
                label = f"{label}({title[:60]})"
            labels.append(label[:120])
            if len(labels) >= max(1, int(limit or 1)):
                break
        return labels

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
            from_actor="frontstage",
            message=reason,
            payload={"mode": metadata["cleanup"]["mode"]},
            status="handled",
            timestamp=effective_ts,
        )
        return updated
