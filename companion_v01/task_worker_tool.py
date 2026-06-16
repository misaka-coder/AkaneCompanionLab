from __future__ import annotations

from typing import Any

from .task_worker import TaskWorkerService
from .tool_runtime import BaseToolHandler, ToolExecutionContext, ToolExecutionResult


class DelegateTaskToolHandler(BaseToolHandler):
    tool_type = "delegate_task"

    def __init__(self, *, task_worker_service: TaskWorkerService) -> None:
        self.task_worker_service = task_worker_service

    def build_prompt_instruction(self) -> str:
        return (
            "- delegate_task：当用户的需求明显需要后台工坊分担，例如多步文件/音视频处理、转写后总结、批量生成多个产物，"
            "或你需要继续陪用户聊天但任务可以在后台执行时使用。"
            "这个工具会创建或接管任务工作区，并把任务交给 specialist worker；worker 只处理任务和登记产物，不会直接发文件给用户。"
            "格式为 {\"type\":\"delegate_task\",\"agent\":\"auto|document_agent|media_agent|speech_agent|resource_agent\","
            "\"brief\":\"给后台工坊的明确任务说明\",\"task_id\":\"可选；已有任务 id\","
            "\"goal\":\"对用户需求的归一化目标\",\"inputs\":[\"素材或编号\"],\"expected_outputs\":[\"期望产物\"],"
            "\"steps\":[{\"title\":\"步骤\",\"status\":\"queued\"}],\"constraints\":[\"约束\"],\"success_criteria\":[\"验收标准\"]}。"
            "不要把一句话就能直接完成的小事委派出去；如果用户明确要求立刻发送已有文件或生成一个简单文本文件，直接调用对应处理工具即可。"
            "但在 QQ 纯文字客户端里，音视频转码、分离人声伴奏、降噪、转写、训练素材切片打包这类媒体处理通常可能较慢，优先委派给后台工坊，避免前台阻塞或文件交付超时。"
            "委派成功后前台只需要简短告诉用户后台已经开始，不要长篇解释内部流程或 task_id。"
        )

    def normalize_call(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if str(value.get("type") or "").strip() != self.tool_type:
            return None
        brief = str(value.get("brief") or value.get("instruction") or value.get("task") or "").strip()
        goal = str(value.get("goal") or value.get("normalized_goal") or value.get("title") or brief).strip()
        raw_request = str(value.get("raw_request") or value.get("user_request") or goal or brief).strip()
        if not brief and not goal and not raw_request:
            return None
        agent = self._normalize_agent(value.get("agent") or value.get("worker") or value.get("role") or "auto")
        return {
            "type": self.tool_type,
            "agent": agent,
            "task_id": str(value.get("task_id") or value.get("id") or "").strip()[:96],
            "brief": (brief or goal or raw_request)[:1000],
            "goal": goal[:500],
            "raw_request": raw_request[:800],
            "inputs": self._normalize_text_list(value.get("inputs") or value.get("source_ids") or value.get("sources")),
            "expected_outputs": self._normalize_text_list(value.get("expected_outputs") or value.get("outputs")),
            "success_criteria": self._normalize_text_list(value.get("success_criteria") or value.get("criteria") or value.get("acceptance")),
            "constraints": self._normalize_text_list(value.get("constraints") or value.get("rules")),
            "steps": self._normalize_steps(value.get("steps") or value.get("plan")),
        }

    def execute(self, *, call: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        delivery_context = self._extract_delivery_context(context)
        delegation = self.task_worker_service.delegate_task(
            profile_user_id=context.profile_user_id,
            session_id=context.session_id,
            brief=str(call.get("brief") or ""),
            agent=str(call.get("agent") or "auto"),
            task_id=str(call.get("task_id") or ""),
            raw_request_text=str(call.get("raw_request") or ""),
            normalized_goal=str(call.get("goal") or ""),
            source_message_id=context.current_user_source_id,
            success_criteria=list(call.get("success_criteria") or []),
            constraints=list(call.get("constraints") or []),
            steps=list(call.get("steps") or []),
            inputs=list(call.get("inputs") or []),
            expected_outputs=list(call.get("expected_outputs") or []),
            delivery_context=delivery_context,
            auto_start=True,
            timestamp=context.now_ts,
        )
        start_text = "已开始后台执行" if delegation.started else "已登记但尚未启动"
        return ToolExecutionResult(
            tool_type=self.tool_type,
            stream_events=[
                {
                    "type": "task_worker_delegated",
                    "task_id": delegation.task_id,
                    "assigned_agent": delegation.assigned_agent,
                    "started": delegation.started,
                    "handle_id": delegation.handle_id,
                }
            ],
            followup_context=(
                f"后台工坊已接收任务 {delegation.task_id}，由 {delegation.assigned_agent} 处理，{start_text}。"
                "请只用一句简短前台话告诉用户后台已经开始处理；不要长篇解释内部流程或 task_id，"
                "也不要声称产物已经完成，除非任务工作区后续出现完成事件。"
            ),
            state_updates={
                "task_id": delegation.task_id,
                "assigned_agent": delegation.assigned_agent,
                "handle_id": delegation.handle_id,
                "started": delegation.started,
            },
        )

    def _extract_delivery_context(self, context: ToolExecutionContext) -> dict[str, Any]:
        request_context = context.request_context if isinstance(context.request_context, dict) else {}
        delivery_context = request_context.get("qq_delivery_context")
        if not isinstance(delivery_context, dict):
            return {}
        if str(context.client_mode or "").strip() != "qq_text":
            return {}
        normalized = dict(delivery_context)
        normalized["client"] = "qq_text"
        return normalized

    def _normalize_agent(self, value: Any) -> str:
        raw = str(value or "auto").strip().lower()
        aliases = {
            "auto": "auto",
            "worker": "auto",
            "video_agent": "media_agent",
            "audio_agent": "media_agent",
            "media_agent": "media_agent",
            "speech_agent": "speech_agent",
            "document_agent": "document_agent",
            "doc_agent": "document_agent",
            "file_agent": "document_agent",
            "resource_agent": "resource_agent",
        }
        return aliases.get(raw, "auto")

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
            normalized.append({"id": f"delegate_step_{index}", "title": title[:120], "status": status, "note": note[:200]})
            if len(normalized) >= 12:
                break
        return normalized
