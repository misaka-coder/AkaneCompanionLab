from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import config

from .background_tasks import BackgroundTaskRunner
from .llm_runtime import LLMRuntime
from .store import normalize_character_pack_id
from .task_workspace import TaskWorkspaceService
from .tool_runtime import BaseToolHandler, ToolExecutionContext, ToolExecutionResult


logger = logging.getLogger("akane.task_worker")


ToolHandlersProvider = Callable[[], dict[str, BaseToolHandler]]
PromptContextBuilder = Callable[[str, str], str]
ToolArtifactRecorder = Callable[..., tuple[list[dict[str, Any]], str]]
TaskCompletionCallback = Callable[..., None]


AGENT_ALLOWED_TOOLS: dict[str, set[str]] = {
    "document_agent": {
        "sync_attachment_workspace",
        "inspect_attachment",
        "read_attachment_section",
        "compose_file",
        "revise_generated_file",
        "apply_style_to_existing_file",
        "inspect_generated_file",
    },
    "media_agent": {
        "fetch_media_from_url",
        "sync_attachment_workspace",
        "inspect_attachment",
        "inspect_media_info",
        "convert_media_file",
        "separate_audio_stems",
        "clean_voice_track",
        "transcribe_media",
        "prepare_voice_dataset",
        "inspect_generated_file",
        "compose_file",
    },
    "speech_agent": {
        "sync_attachment_workspace",
        "inspect_attachment",
        "inspect_media_info",
        "separate_audio_stems",
        "clean_voice_track",
        "transcribe_media",
        "prepare_voice_dataset",
        "compose_file",
        "revise_generated_file",
        "inspect_generated_file",
    },
    "resource_agent": {
        "sync_attachment_workspace",
        "inspect_attachment",
        "read_attachment_section",
        "inspect_generated_file",
    },
}

AGENT_ALIASES = {
    "auto": "media_agent",
    "worker": "media_agent",
    "video_agent": "media_agent",
    "audio_agent": "media_agent",
    "file_agent": "document_agent",
    "doc_agent": "document_agent",
}


@dataclass(frozen=True)
class WorkerDelegation:
    task_id: str
    assigned_agent: str
    brief: str
    handle_id: str = ""
    started: bool = False


@dataclass
class WorkerRunSummary:
    task_id: str
    assigned_agent: str
    status: str
    rounds: int = 0
    messages: list[str] = field(default_factory=list)
    tool_results: list[str] = field(default_factory=list)


class TaskWorkerService:
    """Run constrained background specialists against task workspaces.

    The worker is intentionally not a second frontstage assistant. It receives a scoped brief,
    can use only a small tool set, writes progress back to the task workspace,
    and never sends user-facing files/messages by itself.
    """

    def __init__(
        self,
        *,
        llm: LLMRuntime,
        task_workspace_service: TaskWorkspaceService,
        background_tasks: BackgroundTaskRunner | None,
        tool_handlers_provider: ToolHandlersProvider,
        attachment_context_builder: PromptContextBuilder,
        generated_context_builder: PromptContextBuilder,
        record_tool_artifacts: ToolArtifactRecorder,
        on_task_completed: TaskCompletionCallback | None = None,
    ) -> None:
        self.llm = llm
        self.task_workspace_service = task_workspace_service
        self.background_tasks = background_tasks
        self.tool_handlers_provider = tool_handlers_provider
        self.attachment_context_builder = attachment_context_builder
        self.generated_context_builder = generated_context_builder
        self.record_tool_artifacts = record_tool_artifacts
        self.on_task_completed = on_task_completed

    def delegate_task(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        brief: str,
        agent: str = "auto",
        task_id: str = "",
        raw_request_text: str = "",
        normalized_goal: str = "",
        source_message_id: str = "",
        success_criteria: list[Any] | None = None,
        constraints: list[Any] | None = None,
        steps: list[dict[str, Any]] | None = None,
        inputs: list[Any] | None = None,
        expected_outputs: list[Any] | None = None,
        delivery_context: dict[str, Any] | None = None,
        auto_start: bool = True,
        timestamp: int | None = None,
    ) -> WorkerDelegation:
        now_ts = int(timestamp or time.time())
        assigned_agent = self._normalize_agent(agent)
        clean_brief = str(brief or normalized_goal or raw_request_text or "").strip()
        if not clean_brief:
            clean_brief = "根据当前任务工作区继续推进任务。"

        task = self.task_workspace_service.get_task(task_id) if task_id else None
        if task is None:
            metadata = {
                "workshop": {
                    "assigned_agent": assigned_agent,
                    "brief": clean_brief,
                    "status": "queued",
                    "inputs": self._normalize_text_list(inputs),
                    "expected_outputs": self._normalize_text_list(expected_outputs),
                    "delegated_at": now_ts,
                }
            }
            normalized_delivery_context = self._normalize_delivery_context(delivery_context)
            if normalized_delivery_context:
                metadata["delivery"] = normalized_delivery_context
            task = self.task_workspace_service.create_task(
                profile_user_id=profile_user_id,
                session_id=session_id,
                raw_request_text=raw_request_text or clean_brief,
                source_message_id=source_message_id,
                normalized_goal=normalized_goal or clean_brief,
                success_criteria=success_criteria or [],
                constraints=constraints or [],
                steps=steps or [],
                metadata=metadata,
                owner="frontstage",
                status="queued",
                timestamp=now_ts,
            )
        else:
            metadata = dict(task.get("metadata") or {})
            workshop = dict(metadata.get("workshop") or {})
            workshop.update(
                {
                    "assigned_agent": assigned_agent,
                    "brief": clean_brief,
                    "status": "queued",
                    "inputs": self._normalize_text_list(inputs) or list(workshop.get("inputs") or []),
                    "expected_outputs": self._normalize_text_list(expected_outputs) or list(workshop.get("expected_outputs") or []),
                    "delegated_at": now_ts,
                    "updated_at": now_ts,
                }
            )
            metadata["workshop"] = workshop
            normalized_delivery_context = self._normalize_delivery_context(delivery_context)
            if normalized_delivery_context and not isinstance(metadata.get("delivery"), dict):
                metadata["delivery"] = normalized_delivery_context
            task = self.task_workspace_service.update_task(
                task_id=str(task["task_id"]),
                status="queued",
                metadata=metadata,
                timestamp=now_ts,
            ) or task

        task_id = str(task.get("task_id") or "").strip()
        self.task_workspace_service.append_event(
            task_id=task_id,
            event_type="worker_delegated",
            from_actor="frontstage",
            priority="normal",
            message=f"{assigned_agent} 已接收后台工坊任务。",
            payload={
                "assigned_agent": assigned_agent,
                "brief": clean_brief,
                "inputs": self._normalize_text_list(inputs),
                "expected_outputs": self._normalize_text_list(expected_outputs),
            },
            status="handled",
            timestamp=now_ts,
        )

        handle_id = ""
        started = False
        if auto_start and self.background_tasks is not None:
            handle = self.background_tasks.submit(
                lane="task_worker",
                name=f"{assigned_agent}:{task_id}",
                fn=self.run_task_sync,
                kwargs={
                    "task_id": task_id,
                    "profile_user_id": profile_user_id,
                    "session_id": session_id,
                    "assigned_agent": assigned_agent,
                    "now_ts": now_ts,
                },
            )
            handle_id = handle.task_id
            started = True

        return WorkerDelegation(
            task_id=task_id,
            assigned_agent=assigned_agent,
            brief=clean_brief,
            handle_id=handle_id,
            started=started,
        )

    def run_task_sync(
        self,
        *,
        task_id: str,
        profile_user_id: str,
        session_id: str,
        assigned_agent: str = "auto",
        now_ts: int | None = None,
    ) -> WorkerRunSummary:
        start_ts = int(now_ts or time.time())
        agent = self._normalize_agent(assigned_agent)
        summary = WorkerRunSummary(task_id=task_id, assigned_agent=agent, status="running")
        task = self.task_workspace_service.get_task(task_id)
        if not task:
            return WorkerRunSummary(task_id=task_id, assigned_agent=agent, status="missing_task")

        self._update_workshop_status(task, status="running", timestamp=start_ts)
        self.task_workspace_service.append_event(
            task_id=task_id,
            event_type="worker_started",
            from_actor=agent,
            message=f"{agent} 开始处理后台任务。",
            payload={},
            status="handled",
            timestamp=start_ts,
        )

        tool_followups: list[str] = []
        max_rounds = max(1, min(5, int(getattr(config, "MAX_TASK_WORKER_ROUNDS", 3) or 3)))
        allowed_handlers = self._allowed_handlers(agent)
        if not allowed_handlers:
            self._block_task(task_id=task_id, agent=agent, message="后台工坊没有可用工具。", question="", timestamp=start_ts)
            return WorkerRunSummary(task_id=task_id, assigned_agent=agent, status="blocked", rounds=0)

        for round_index in range(max_rounds):
            summary.rounds += 1
            current_task = self.task_workspace_service.get_task(task_id) or task
            result = self.llm.call_chat_json(
                system_prompt=self._build_worker_system_prompt(agent=agent, handlers=allowed_handlers),
                user_prompt=self._build_worker_user_prompt(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    task=current_task,
                    assigned_agent=agent,
                    tool_followups=tool_followups,
                ),
                fallback={"status": "blocked", "message": "后台工坊没有拿到可解析的 JSON。", "tool_call": None},
                temperature=0.2,
                prompt_cache_key="chat:task_worker",
            )
            normalized = self._normalize_worker_output(result)
            message = normalized["message"]
            if message:
                summary.messages.append(message)
            steps = normalized["steps"]
            artifacts = normalized["artifacts"]
            if steps or artifacts:
                self._apply_worker_state(
                    task_id=task_id,
                    task=current_task,
                    steps=steps,
                    artifacts=artifacts,
                    timestamp=int(time.time()),
                )

            tool_call = normalized["tool_call"]
            if tool_call:
                tool_result = self._execute_worker_tool(
                    tool_call=tool_call,
                    handlers=allowed_handlers,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    task_id=task_id,
                )
                if tool_result is None:
                    blocked_message = f"{agent} 想调用一个不可用或参数不完整的工具：{self._describe_tool_call(tool_call)}"
                    self._block_task(
                        task_id=task_id,
                        agent=agent,
                        message=blocked_message,
                        question="后台工坊需要重新确认下一步工具或参数。",
                        timestamp=int(time.time()),
                    )
                    summary.status = "blocked"
                    return summary
                self.record_tool_artifacts(
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    tool_result=tool_result,
                    now_ts=int(time.time()),
                )
                followup = str(tool_result.followup_context or "").strip()
                if followup:
                    tool_followups.append(f"第 {len(tool_followups) + 1} 次工具（{tool_result.tool_type}）结果：\n{followup}")
                    summary.tool_results.append(followup[:240])
                self.task_workspace_service.append_event(
                    task_id=task_id,
                    event_type="worker_tool_executed",
                    from_actor=agent,
                    message=f"{agent} 调用了 {tool_result.tool_type}。",
                    payload={"tool_type": tool_result.tool_type, "followup": followup[:1000]},
                    status="handled",
                    timestamp=int(time.time()),
                )
                continue

            status = normalized["status"]
            if status == "done":
                final_task = self.task_workspace_service.get_task(task_id) or current_task
                handoff = self._build_handoff_payload(
                    task=final_task,
                    agent=agent,
                    status="completed",
                    message=message or f"{agent} 已完成后台任务。",
                    question="",
                    next_action=normalized["next_action"],
                    handoff_note=normalized["handoff_note"],
                )
                self.task_workspace_service.complete_task(
                    task_id=task_id,
                    artifacts=[dict(item) for item in list(final_task.get("artifacts") or []) if isinstance(item, dict)],
                    message=message or f"{agent} 已完成后台任务。",
                    timestamp=int(time.time()),
                )
                post_complete_task = self.task_workspace_service.get_task(task_id) or final_task
                self._update_workshop_status(post_complete_task, status="done", handoff=handoff, timestamp=int(time.time()))
                self.task_workspace_service.append_event(
                    task_id=task_id,
                    event_type="worker_completed",
                    from_actor=agent,
                    priority="high",
                    message=message or f"{agent} 已完成后台任务。",
                    payload={"handoff": handoff},
                    status="pending",
                    timestamp=int(time.time()),
                )
                self._notify_task_completed(
                    task_id=task_id,
                    profile_user_id=profile_user_id,
                    session_id=session_id,
                    task=post_complete_task,
                    handoff=handoff,
                )
                summary.status = "done"
                return summary

            if status == "blocked":
                self._block_task(
                    task_id=task_id,
                    agent=agent,
                    message=message or f"{agent} 暂时卡住了。",
                    question=normalized["question"],
                    next_action=normalized["next_action"],
                    handoff_note=normalized["handoff_note"],
                    timestamp=int(time.time()),
                )
                summary.status = "blocked"
                return summary

            waiting_task = self.task_workspace_service.get_task(task_id) or current_task
            handoff = self._build_handoff_payload(
                task=waiting_task,
                agent=agent,
                status="partial",
                message=message or f"{agent} 暂停等待下一步。",
                question=normalized["question"],
                next_action=normalized["next_action"],
                handoff_note=normalized["handoff_note"],
            )
            self._update_workshop_status(waiting_task, status="paused", handoff=handoff, timestamp=int(time.time()))
            self.task_workspace_service.append_event(
                task_id=task_id,
                event_type="worker_waiting",
                from_actor=agent,
                message=message or f"{agent} 暂停等待下一步。",
                payload={"handoff": handoff},
                status="pending",
                timestamp=int(time.time()),
            )
            summary.status = "waiting"
            return summary

        limit_task = self.task_workspace_service.get_task(task_id) or task
        handoff = self._build_handoff_payload(
            task=limit_task,
            agent=agent,
            status="partial",
            message=f"{agent} 已达到本轮后台工坊最大执行轮数，请前台助手查看任务工作区后决定是否继续。",
            question="",
            next_action="continue_work",
            handoff_note="已完成一部分后台步骤，但仍需要前台助手决定是否继续推进或先交付现有成果。",
        )
        self.task_workspace_service.append_event(
            task_id=task_id,
            event_type="worker_round_limit",
            from_actor=agent,
            priority="normal",
            message=f"{agent} 已达到本轮后台工坊最大执行轮数，请前台助手查看任务工作区后决定是否继续。",
            payload={"max_rounds": max_rounds, "handoff": handoff},
            status="pending",
            timestamp=int(time.time()),
        )
        self._update_workshop_status(limit_task, status="paused", handoff=handoff, timestamp=int(time.time()))
        summary.status = "paused"
        return summary

    def _allowed_handlers(self, agent: str) -> dict[str, BaseToolHandler]:
        all_handlers = dict(self.tool_handlers_provider() or {})
        allowed = set(AGENT_ALLOWED_TOOLS.get(agent) or set())
        if not allowed:
            allowed = set().union(*AGENT_ALLOWED_TOOLS.values())
        return {name: handler for name, handler in all_handlers.items() if name in allowed}

    def _execute_worker_tool(
        self,
        *,
        tool_call: dict[str, Any],
        handlers: dict[str, BaseToolHandler],
        profile_user_id: str,
        session_id: str,
        task_id: str,
    ) -> ToolExecutionResult | None:
        tool_type = str(tool_call.get("type") or "").strip()
        handler = handlers.get(tool_type)
        if handler is None:
            return None
        safe_tool_call = dict(tool_call)
        if tool_type in {
            "compose_file",
            "revise_generated_file",
            "apply_style_to_existing_file",
            "convert_media_file",
            "separate_audio_stems",
            "clean_voice_track",
            "transcribe_media",
            "prepare_voice_dataset",
        }:
            safe_tool_call["send_to_user"] = False
        normalized = handler.normalize_call(safe_tool_call)
        if not normalized:
            return None
        return handler.execute(
            call=normalized,
            context=ToolExecutionContext(
                profile_user_id=profile_user_id,
                session_id=session_id,
                now_ts=int(time.time()),
                visual_payload={"_task_worker": True, "_task_id": task_id},
            ),
        )

    def _build_worker_system_prompt(self, *, agent: str, handlers: dict[str, BaseToolHandler]) -> str:
        lines = [
            "你是后台工坊里的 specialist worker，不是前台说话的角色。",
            "你负责执行被委派的任务，把进度和产物写回任务工作区；不要和用户闲聊，不要输出角色台词。",
            "你不能直接发送文件给用户，也不能替前台助手做最终汇报、最终发送或清理收尾。产物生成后留在生成区和任务工作区，由前台助手决定如何确认、发送与清理。",
            "任务工作区里的产物不都等于要交付的文件：中间素材、参考件、伴奏/人声拆轨中的非目标轨，都只算可继续使用的素材。",
            "只有直接符合用户目标或期望产物的结果，才在 handoff.artifacts 里作为交接候选；不确定用户要哪份时用 ask_confirmation，让前台助手问清楚。",
            "即使你觉得某个旧文件已经没用，也不要主动归档/删除；把情况写进任务工作区，交给前台助手处理。",
            "你必须只输出一个合法 JSON 对象，不能输出 markdown 或额外解释。",
            "JSON 字段：",
            '{"status":"continue|done|blocked","message":"给前台助手的内部简短说明","tool_call":{...}|null,'
            '"steps":[{"title":"步骤","status":"queued|running|done|failed|waiting_user","note":"可选"}],'
            '"artifacts":[{"id":"gen_001","kind":"md","title":"可选","delivery_role":"final_output|workspace_material"}],"question":"卡住时要前台助手/用户确认的问题，可空",'
            '"handoff":{"summary":"交给前台助手接手的一句话","next_action":"send_to_user|ask_confirmation|continue_work|ask_user|report_only","user_question":"需要用户回答时的自然问题，可空"}}',
            "如果下一步需要工具，就把工具写进 tool_call，并把 status 设为 continue。",
            "如果任务完成，tool_call 为 null，status 设为 done。",
            "如果任务完成且已有产物，handoff.next_action 必须写清是 send_to_user 还是 ask_confirmation。",
            "如果缺少必要信息，tool_call 为 null，status 设为 blocked，并写清 question；question 要写成前台助手可以直接问用户的话。",
            "",
            "【你当前可用的受限工具】",
        ]
        for handler in handlers.values():
            lines.append(handler.build_prompt_instruction())
        lines.append("一次只调用一个工具；不要调用未列出的工具。")
        return "\n".join(lines)

    def _build_worker_user_prompt(
        self,
        *,
        profile_user_id: str,
        session_id: str,
        task: dict[str, Any],
        assigned_agent: str,
        tool_followups: list[str],
    ) -> str:
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        workshop = metadata.get("workshop") if isinstance(metadata.get("workshop"), dict) else {}
        raw_request = task.get("raw_request") if isinstance(task.get("raw_request"), dict) else {}
        lines = [
            "【委派任务】",
            f"task_id: {task.get('task_id')}",
            f"assigned_agent: {assigned_agent}",
            f"目标: {task.get('normalized_goal') or raw_request.get('text') or ''}",
            f"brief: {workshop.get('brief') or ''}",
        ]
        success_criteria = [str(item).strip() for item in list(task.get("success_criteria") or []) if str(item).strip()]
        constraints = [str(item).strip() for item in list(task.get("constraints") or []) if str(item).strip()]
        if success_criteria:
            lines.append("验收标准: " + "；".join(success_criteria[:8]))
        if constraints:
            lines.append("约束: " + "；".join(constraints[:8]))
        inputs = [str(item).strip() for item in list(workshop.get("inputs") or []) if str(item).strip()]
        outputs = [str(item).strip() for item in list(workshop.get("expected_outputs") or []) if str(item).strip()]
        if inputs:
            lines.append("输入/素材: " + "；".join(inputs[:12]))
        if outputs:
            lines.append("期望产物: " + "；".join(outputs[:12]))

        task_context = self.task_workspace_service.build_prompt_context(
            profile_user_id=profile_user_id,
            session_id=session_id,
            task_limit=3,
            event_limit=8,
        )
        attachment_context = self.attachment_context_builder(profile_user_id, session_id)
        generated_context = self.generated_context_builder(profile_user_id, session_id)
        for title, value in [
            ("任务工作区", task_context),
            ("工作台材料/素材", attachment_context),
            ("生成文件区", generated_context),
        ]:
            clean_value = str(value or "").strip()
            if clean_value:
                lines.extend(["", f"【{title}】", clean_value])
        if tool_followups:
            lines.extend(["", "【刚刚的工具反馈】", "\n\n".join(tool_followups[-4:])])
        lines.append("\n请决定下一步：继续调用一个工具、标记完成，或提出阻塞问题。")
        return "\n".join(lines)

    def _normalize_worker_output(self, value: Any) -> dict[str, Any]:
        data = value if isinstance(value, dict) else {}
        status = str(data.get("status") or "").strip().lower()
        if status not in {"continue", "done", "blocked"}:
            status = "continue" if isinstance(data.get("tool_call"), dict) else "blocked"
        message = str(data.get("message") or data.get("note") or "").strip()[:500]
        raw_handoff = data.get("handoff") if isinstance(data.get("handoff"), dict) else {}
        question = str(data.get("question") or raw_handoff.get("user_question") or "").strip()[:400]
        tool_call = data.get("tool_call") if isinstance(data.get("tool_call"), dict) else None
        return {
            "status": status,
            "message": message,
            "question": question,
            "tool_call": dict(tool_call) if isinstance(tool_call, dict) else None,
            "steps": self._normalize_steps(data.get("steps")),
            "artifacts": self._normalize_artifacts(data.get("artifacts")),
            "next_action": self._normalize_next_action(raw_handoff.get("next_action") or data.get("next_action") or data.get("delivery")),
            "handoff_note": str(raw_handoff.get("summary") or raw_handoff.get("note") or data.get("handoff_note") or "").strip()[:500],
        }

    def _apply_worker_state(
        self,
        *,
        task_id: str,
        task: dict[str, Any],
        steps: list[dict[str, Any]],
        artifacts: list[dict[str, Any]],
        timestamp: int,
    ) -> None:
        update_kwargs: dict[str, Any] = {"task_id": task_id, "timestamp": timestamp}
        if steps:
            existing_steps = [dict(item) for item in list(task.get("steps") or []) if isinstance(item, dict)]
            update_kwargs["steps"] = self._merge_steps(existing_steps, steps)
        if artifacts:
            existing = [dict(item) for item in list(task.get("artifacts") or []) if isinstance(item, dict)]
            update_kwargs["artifacts"] = self._merge_artifacts(existing, artifacts)
        self.task_workspace_service.update_task(**update_kwargs)
        self.task_workspace_service.append_event(
            task_id=task_id,
            event_type="worker_state_updated",
            from_actor="task_worker",
            message="后台工坊更新了步骤或产物。",
            payload={"steps": steps, "artifacts": artifacts},
            status="handled",
            timestamp=timestamp,
        )

    def _block_task(
        self,
        *,
        task_id: str,
        agent: str,
        message: str,
        question: str,
        timestamp: int,
        next_action: str = "",
        handoff_note: str = "",
    ) -> None:
        task = self.task_workspace_service.get_task(task_id) or {}
        handoff = self._build_handoff_payload(
            task=task,
            agent=agent,
            status="blocked",
            message=message,
            question=question,
            next_action=next_action or "ask_user",
            handoff_note=handoff_note,
        )
        pending_question = {"text": question, "reason": message, "asked_at": timestamp} if question else None
        self.task_workspace_service.update_task(
            task_id=task_id,
            status="waiting_user" if question else "running",
            pending_question=pending_question,
            metadata=self._metadata_with_workshop_update(task, status="blocked", handoff=handoff, timestamp=timestamp),
            timestamp=timestamp,
        )
        self.task_workspace_service.append_event(
            task_id=task_id,
            event_type="worker_blocked",
            from_actor=agent,
            priority="high",
            requires_user=bool(question),
            message=message,
            payload={"question": question, "handoff": handoff},
            status="pending",
            timestamp=timestamp,
        )

    def _update_workshop_status(
        self,
        task: dict[str, Any],
        *,
        status: str,
        handoff: dict[str, Any] | None = None,
        timestamp: int,
    ) -> None:
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            return
        self.task_workspace_service.update_task(
            task_id=task_id,
            status="running" if status == "running" else None,
            metadata=self._metadata_with_workshop_update(task, status=status, handoff=handoff, timestamp=timestamp),
            timestamp=timestamp,
        )

    def _metadata_with_workshop_update(
        self,
        task: dict[str, Any],
        *,
        status: str,
        handoff: dict[str, Any] | None,
        timestamp: int,
    ) -> dict[str, Any]:
        metadata = dict(task.get("metadata") or {})
        workshop = dict(metadata.get("workshop") or {})
        workshop["status"] = status
        workshop["updated_at"] = timestamp
        if handoff:
            workshop["handoff"] = dict(handoff)
        metadata["workshop"] = workshop
        return metadata

    def _build_handoff_payload(
        self,
        *,
        task: dict[str, Any],
        agent: str,
        status: str,
        message: str,
        question: str,
        next_action: str,
        handoff_note: str,
    ) -> dict[str, Any]:
        steps = [step for step in list(task.get("steps") or []) if isinstance(step, dict)]
        artifacts = [artifact for artifact in list(task.get("artifacts") or []) if isinstance(artifact, dict)]
        handoff_artifacts = self._select_handoff_artifacts(task=task, artifacts=artifacts)
        completed_steps = self._summarize_steps(steps, {"done", "completed"})
        active_steps = self._summarize_steps(steps, {"running"})
        blocked_steps = self._summarize_steps(steps, {"waiting_user", "failed"})
        remaining_steps = self._summarize_steps(steps, {"queued"})
        artifact_summaries = self._summarize_artifacts(handoff_artifacts)
        normalized_action = self._default_next_action(
            next_action,
            status=status,
            has_artifacts=bool(artifact_summaries),
            has_question=bool(question),
        )
        summary_text = handoff_note or message or self._default_handoff_summary(
            status=status,
            artifacts=artifact_summaries,
            completed_steps=completed_steps,
        )
        handoff = {
            "status": status,
            "agent": agent,
            "summary": summary_text[:500],
            "completed_steps": completed_steps,
            "active_steps": active_steps,
            "blocked_steps": blocked_steps,
            "remaining_steps": remaining_steps,
            "artifacts": artifact_summaries,
            "workspace_artifact_count": len(artifacts),
            "next_action": normalized_action,
            "akane_instruction": self._handoff_instruction(
                status=status,
                next_action=normalized_action,
                has_artifacts=bool(artifact_summaries),
                has_question=bool(question),
            ),
        }
        if question:
            handoff["user_question"] = question[:400]
        return handoff

    def _select_handoff_artifacts(
        self,
        *,
        task: dict[str, Any],
        artifacts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        candidates = [artifact for artifact in artifacts if isinstance(artifact, dict)]
        if not candidates:
            return []

        explicit = [
            artifact
            for artifact in candidates
            if bool(artifact.get("send_to_user"))
            or str(artifact.get("delivery_role") or "").strip().lower() in {"requested_output", "final_output", "deliverable"}
            or bool(artifact.get("deliverable"))
        ]
        if explicit:
            return explicit

        intent = self._task_delivery_intent_text(task)
        final_tool_artifacts = self._select_final_tool_artifacts(intent=intent, artifacts=candidates)
        if final_tool_artifacts:
            return final_tool_artifacts

        by_audio_role = self._select_audio_role_artifacts(intent=intent, artifacts=candidates)
        if by_audio_role:
            return by_audio_role

        generated = [artifact for artifact in candidates if str(artifact.get("source") or "") == "generated_file"]
        if generated:
            return [generated[-1]]
        return candidates[-1:]

    def _task_delivery_intent_text(self, task: dict[str, Any]) -> str:
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        workshop = metadata.get("workshop") if isinstance(metadata.get("workshop"), dict) else {}
        raw_request = task.get("raw_request") if isinstance(task.get("raw_request"), dict) else {}
        parts: list[str] = [
            str(task.get("normalized_goal") or ""),
            str(raw_request.get("text") or ""),
            str(workshop.get("brief") or ""),
        ]
        for key in ("expected_outputs", "inputs"):
            values = workshop.get(key) if isinstance(workshop.get(key), list) else []
            parts.extend(str(item or "") for item in values)
        for key in ("success_criteria", "constraints"):
            values = task.get(key) if isinstance(task.get(key), list) else []
            parts.extend(str(item or "") for item in values)
        return " ".join(part.strip().lower() for part in parts if str(part or "").strip())

    def _select_audio_role_artifacts(self, *, intent: str, artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        role_items = [
            artifact
            for artifact in artifacts
            if str(artifact.get("stem_role") or "").strip()
            or str(artifact.get("created_by_tool") or artifact.get("tool") or "") == "separate_audio_stems"
        ]
        if not role_items:
            return []
        wants_both = any(token in intent for token in ("人声伴奏", "人声和伴奏", "人声/伴奏", "两轨", "拆轨", "分离人声和伴奏"))
        only_vocals = any(token in intent for token in ("只要人声", "只发人声", "只需要人声", "只保留人声", "只要干声"))
        only_instrumental = any(token in intent for token in ("只要伴奏", "只发伴奏", "只需要伴奏", "只保留伴奏"))
        wants_vocals = any(token in intent for token in ("只要人声", "人声", "vocals", "vocal", "干声", "歌声"))
        wants_instrumental = any(token in intent for token in ("只要伴奏", "伴奏", "instrumental", "no vocals", "去人声"))
        if only_vocals:
            selected = [artifact for artifact in role_items if self._artifact_has_audio_role(artifact, {"vocals", "vocal", "人声", "干声", "歌声"})]
            if selected:
                return selected
        if only_instrumental:
            selected = [artifact for artifact in role_items if self._artifact_has_audio_role(artifact, {"instrumental", "伴奏", "no_vocals", "accompaniment"})]
            if selected:
                return selected
        if wants_both or (wants_vocals and wants_instrumental):
            return role_items
        if wants_vocals:
            selected = [artifact for artifact in role_items if self._artifact_has_audio_role(artifact, {"vocals", "vocal", "人声", "干声", "歌声"})]
            if selected:
                return selected
        if wants_instrumental:
            selected = [artifact for artifact in role_items if self._artifact_has_audio_role(artifact, {"instrumental", "伴奏", "no_vocals", "accompaniment"})]
            if selected:
                return selected
        return []

    def _artifact_has_audio_role(self, artifact: dict[str, Any], roles: set[str]) -> bool:
        haystack = " ".join(
            str(artifact.get(key) or "").strip().lower()
            for key in ("stem_role", "title", "id", "generated_handle")
        )
        return any(role.lower() in haystack for role in roles)

    def _select_final_tool_artifacts(self, *, intent: str, artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if any(token in intent for token in ("训练素材", "数据集", "dataset", "切片")):
            selected = [
                artifact
                for artifact in artifacts
                if str(artifact.get("created_by_tool") or artifact.get("tool") or "") == "prepare_voice_dataset"
                or str(artifact.get("kind") or "").lower() in {"zip", "7z"}
            ]
            if selected:
                return selected[-1:]
        if any(token in intent for token in ("降噪", "净化", "去混响", "去回声", "denoise", "clean")):
            selected = [
                artifact
                for artifact in artifacts
                if str(artifact.get("created_by_tool") or artifact.get("tool") or "") == "clean_voice_track"
            ]
            if selected:
                return selected[-1:]
        if any(token in intent for token in ("总结", "文档", "markdown", "md", "docx", "pdf", "表格", "字幕", "转写", "文字稿", "稿")):
            selected = [
                artifact
                for artifact in artifacts
                if str(artifact.get("source") or "") == "generated_file"
                and str(artifact.get("created_by_tool") or artifact.get("tool") or "")
                in {"compose_file", "revise_generated_file", "transcribe_media"}
            ]
            if selected:
                return selected[-1:]
        if any(token in intent for token in ("提取音频", "音频轨", "转音频", "extract audio")):
            selected = [
                artifact
                for artifact in artifacts
                if str(artifact.get("created_by_tool") or artifact.get("tool") or "") == "convert_media_file"
                or str(artifact.get("kind") or "").lower() in {"wav", "flac", "mp3", "m4a", "ogg"}
            ]
            if selected:
                return selected[-1:]
        return []

    def _summarize_steps(self, steps: list[dict[str, Any]], statuses: set[str]) -> list[str]:
        rendered: list[str] = []
        for step in steps:
            status = str(step.get("status") or "").strip().lower()
            if status not in statuses:
                continue
            title = str(step.get("title") or step.get("name") or step.get("id") or "").strip()
            note = str(step.get("note") or "").strip()
            if not title:
                continue
            rendered.append((f"{title}: {note}" if note else title)[:180])
            if len(rendered) >= 12:
                break
        return rendered

    def _summarize_artifacts(self, artifacts: list[dict[str, Any]]) -> list[dict[str, str]]:
        rendered: list[dict[str, str]] = []
        seen: set[str] = set()
        for artifact in artifacts:
            artifact_id = str(artifact.get("id") or artifact.get("generated_handle") or artifact.get("attachment_handle") or "").strip()
            title = str(artifact.get("title") or "").strip()
            kind = str(artifact.get("kind") or artifact.get("format") or "").strip()
            stem_role = str(artifact.get("stem_role") or "").strip()
            delivery_role = str(artifact.get("delivery_role") or "").strip()
            key = (artifact_id or title).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            summary = {"id": artifact_id[:80], "title": title[:120], "kind": kind[:40]}
            if stem_role:
                summary["stem_role"] = stem_role[:40]
            if delivery_role:
                summary["delivery_role"] = delivery_role[:60]
            rendered.append(summary)
            if len(rendered) >= 20:
                break
        return rendered

    def _normalize_next_action(self, value: Any) -> str:
        raw = str(value or "").strip().lower()
        aliases = {
            "send": "send_to_user",
            "send_file": "send_to_user",
            "send_to_user": "send_to_user",
            "direct_send": "send_to_user",
            "deliver": "send_to_user",
            "confirm": "ask_confirmation",
            "ask_confirmation": "ask_confirmation",
            "confirm_then_send": "ask_confirmation",
            "review_first": "ask_confirmation",
            "ask_user": "ask_user",
            "question": "ask_user",
            "continue": "continue_work",
            "continue_work": "continue_work",
            "keep_working": "continue_work",
            "report": "report_only",
            "report_only": "report_only",
        }
        return aliases.get(raw, "")

    def _default_next_action(self, value: str, *, status: str, has_artifacts: bool, has_question: bool) -> str:
        normalized = self._normalize_next_action(value)
        if normalized:
            return normalized
        if has_question:
            return "ask_user"
        if status == "blocked":
            return "report_only"
        if status == "completed":
            return "ask_confirmation" if has_artifacts else "report_only"
        if status == "partial":
            return "continue_work"
        return "report_only"

    def _default_handoff_summary(
        self,
        *,
        status: str,
        artifacts: list[dict[str, str]],
        completed_steps: list[str],
    ) -> str:
        if status == "completed":
            if artifacts:
                return "后台任务已完成，并产出了可交接的结果候选。"
            return "后台任务已完成。"
        if status == "blocked":
            return "后台任务需要用户补充信息后才能继续。"
        if completed_steps or artifacts:
            return "后台任务已完成一部分，仍有后续步骤需要前台助手接手判断。"
        return "后台任务暂停在中途，需要前台助手接手判断下一步。"

    def _handoff_instruction(self, *, status: str, next_action: str, has_artifacts: bool, has_question: bool) -> str:
        if next_action == "send_to_user":
            return "用户已明确要结果时，前台助手可以用 send_file 精确发送这些 handle；发送后再询问是否需要清理任务工作区。"
        if next_action == "ask_confirmation":
            return "先请用户确认要不要发送、以及具体发送哪份结果；用户确认后再用 send_file 精确发送对应 handle。"
        if next_action == "ask_user" or has_question:
            return "把交接问题改成自然口吻直接问用户，等用户回答后再继续委派或调用工具。"
        if status == "blocked":
            return "后台任务暂时无法继续；请由前台助手改用可用工具推进，或自然说明需要稍后重试。"
        if status == "partial" or next_action == "continue_work":
            if has_artifacts:
                return "说明哪些已经完成、哪些还在继续，并询问用户要不要先发送现有成果，或继续后台处理剩余步骤。"
            return "说明哪些已经完成、哪些还在继续，然后继续委派或调用工具推进剩余步骤。"
        return "自然汇报后台结果，并根据用户反馈决定是否继续或清理任务工作区。"

    def _normalize_agent(self, value: Any) -> str:
        raw = str(value or "auto").strip().lower()
        raw = AGENT_ALIASES.get(raw, raw)
        if raw not in AGENT_ALLOWED_TOOLS:
            return "media_agent"
        return raw

    def _normalize_delivery_context(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        client = str(value.get("client") or value.get("client_mode") or "").strip().lower()
        if client and client != "qq_text":
            return {}
        target_id = self._coerce_positive_int(value.get("target_id"))
        if not target_id:
            return {}
        return {
            "client": "qq_text",
            "is_group": bool(value.get("is_group")),
            "target_id": target_id,
            "user_id": self._coerce_positive_int(value.get("user_id")),
            "group_id": self._coerce_positive_int(value.get("group_id")),
            "session_id": str(value.get("session_id") or "")[:120],
            "profile_user_id": str(value.get("profile_user_id") or "")[:120],
            "character_pack_id": normalize_character_pack_id(value.get("character_pack_id") or value.get("characterPackId")),
            "clean_message": str(value.get("clean_message") or "")[:1000],
            "raw_message": str(value.get("raw_message") or "")[:1000],
            "sender_label": str(value.get("sender_label") or "")[:120],
        }

    def _coerce_positive_int(self, value: Any) -> int:
        try:
            parsed = int(value or 0)
        except Exception:
            return 0
        return parsed if parsed > 0 else 0

    def _notify_task_completed(
        self,
        *,
        task_id: str,
        profile_user_id: str,
        session_id: str,
        task: dict[str, Any],
        handoff: dict[str, Any],
    ) -> None:
        callback = getattr(self, "on_task_completed", None)
        if callback is None:
            return
        try:
            callback(
                task_id=task_id,
                profile_user_id=profile_user_id,
                session_id=session_id,
                task=task,
                handoff=handoff,
            )
        except Exception:
            logger.exception("task completion callback failed: task_id=%s", task_id)

    def _normalize_text_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        raw_items = value if isinstance(value, list) else [value]
        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            text = str(item or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(text[:200])
            if len(normalized) >= 20:
                break
        return normalized

    def _normalize_steps(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(value, start=1):
            if isinstance(item, dict):
                title = str(item.get("title") or item.get("name") or item.get("id") or "").strip()
                status = str(item.get("status") or "queued").strip().lower()
                note = str(item.get("note") or "").strip()
            else:
                title = str(item or "").strip()
                status = "queued"
                note = ""
            if not title:
                continue
            if status not in {"queued", "running", "done", "failed", "waiting_user"}:
                status = "queued"
            normalized.append({"id": f"worker_step_{index}", "title": title[:120], "status": status, "note": note[:200]})
            if len(normalized) >= 12:
                break
        return normalized

    def _normalize_artifacts(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, dict):
                continue
            artifact_id = str(item.get("id") or item.get("generated_handle") or item.get("attachment_handle") or "").strip()
            title = str(item.get("title") or "").strip()
            if not artifact_id and not title:
                continue
            key = (artifact_id or title).lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(
                {
                    "id": artifact_id[:80],
                    "kind": str(item.get("kind") or item.get("format") or "").strip()[:40],
                    "title": title[:120],
                    "source": str(item.get("source") or "task_worker").strip()[:60],
                    "delivery_role": str(item.get("delivery_role") or item.get("role") or "workspace_material").strip()[:60],
                }
            )
            if "deliverable" in item:
                normalized[-1]["deliverable"] = bool(item.get("deliverable"))
            if len(normalized) >= 20:
                break
        return normalized

    def _merge_artifacts(self, existing: list[dict[str, Any]], additions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        key_to_index: dict[str, int] = {}
        for item in existing:
            if not isinstance(item, dict):
                continue
            key = str(item.get("id") or item.get("title") or "").strip().lower()
            if not key or key in key_to_index:
                continue
            merged.append(dict(item))
            key_to_index[key] = len(merged) - 1
        for item in additions:
            if not isinstance(item, dict):
                continue
            key = str(item.get("id") or item.get("title") or "").strip().lower()
            if not key:
                continue
            if key in key_to_index:
                current = dict(merged[key_to_index[key]])
                for field, value in item.items():
                    if value not in (None, "", [], {}):
                        current[field] = value
                merged[key_to_index[key]] = current
                continue
            key_to_index[key] = len(merged)
            merged.append(dict(item))
        return merged

    def _merge_steps(self, existing: list[dict[str, Any]], additions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = [dict(item) for item in existing if isinstance(item, dict)]
        key_to_index: dict[str, int] = {}

        def _keys(step: dict[str, Any]) -> list[str]:
            keys: list[str] = []
            step_id = str(step.get("id") or "").strip().lower()
            title = str(step.get("title") or "").strip().lower()
            if step_id:
                keys.append(f"id:{step_id}")
            if title:
                keys.append(f"title:{title}")
            return keys

        def _register(step: dict[str, Any], index: int) -> None:
            for key in _keys(step):
                key_to_index[key] = index

        for index, step in enumerate(merged):
            _register(step, index)

        for item in additions:
            if not isinstance(item, dict):
                continue
            candidate = dict(item)
            existing_index = next((key_to_index[key] for key in _keys(candidate) if key in key_to_index), None)
            if existing_index is None:
                if not str(candidate.get("id") or "").strip():
                    candidate["id"] = f"worker_step_{len(merged) + 1}"
                merged.append(candidate)
                _register(candidate, len(merged) - 1)
                continue
            current = dict(merged[existing_index])
            for field in ("id", "title", "status", "note"):
                value = candidate.get(field)
                if value not in (None, ""):
                    current[field] = value
            merged[existing_index] = current
            _register(current, existing_index)

        return merged[:20]

    def _describe_tool_call(self, tool_call: dict[str, Any]) -> str:
        try:
            return json.dumps(tool_call, ensure_ascii=False, sort_keys=True)[:300]
        except Exception:
            return repr(tool_call)[:300]
